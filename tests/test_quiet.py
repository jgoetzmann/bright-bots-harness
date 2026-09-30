"""B526-B536: the quiet check that starts spending only while the subscription is quiet (D93).

Nothing here spends, sleeps or reaches the network: the ping is a scripted runner or the fake
backend's `ping.json`, `SLEEP` advances a frozen clock, and the partner bot's runs and jobs come
from `tests/fixtures/quiet/`.
"""

from __future__ import annotations

import copy
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import harness.__main__ as cli
import harness.context as context_mod
import harness.gh as gh_mod
from harness import quiet
from harness.clock import FrozenClock, iso
from harness.config import CONFIG_JSON_KEYS, load_config
from harness.errors import ConfigError, GitHubError
from harness.ledger import Ledger
from harness.runner import RunResult
from harness.runner import cli as runner_cli
from harness.runner.cli import PING_FLAGS, PING_PROMPT, ClaudeCliRunner
from harness.runner.fake import DEFAULT_FIXTURES_DIR, FakeRunner
from tests.test_cli import write_d2_repo
from tests.test_gh import FakeOpener, FakeResponse, header_value, http_error
from tests.test_runner_cli import SpawnRecorder

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "quiet"

T1 = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
T2 = T1 + timedelta(minutes=10)
RESET = "2026-09-01T15:00:00Z"
PARTNER = quiet.Partner("jgoetzmann/JackiOh", "bot-night.yml", ("Build, check and review",))
QUIET_STEP = "Wait until the subscription is quiet (harness quiet)"


def reading(five: float | None = 0.2, *, reset: str = RESET, status: str = "allowed", week=None):
    usage: dict = {"status": status}
    if five is not None:
        usage["five_hour"] = {"utilization": five, "resets_at": reset}
    if week is not None:
        usage["seven_day"] = {"utilization": week, "resets_at": "2026-09-07T00:00:00Z"}
    return usage


def sample(at: datetime, usage, error=None) -> quiet.Sample:
    return quiet.Sample(at=iso(at), usage=usage, error=error)


class Script:
    """A sampler replaying readings, stamped from a clock that `sleep` moves, and each ping
    by ``ping_s`` more."""

    def __init__(self, *readings, start: datetime = T1, ping_s: float = 0) -> None:
        self.readings = list(readings)
        self.clock = FrozenClock(start)
        self.ping_s = ping_s
        self.slept: list[float] = []

    def sample(self) -> quiet.Sample:
        assert self.readings, "the check sampled more often than the script allows"
        self.clock.advance(self.ping_s)
        return sample(self.clock.now(), self.readings.pop(0))

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.clock.advance(seconds)


def never_spending(start, end):
    return [], quiet.PARTNER_IDLE


def run(
    script: Script, *, wait: int = 40, interval: int = 10, partner_check=never_spending
) -> quiet.Outcome:
    """The loop at `interval` minutes, giving up after `wait` minutes."""
    return quiet.wait_until_quiet(
        sample=script.sample,
        sleep=script.sleep,
        clock=script.clock,
        interval_s=interval * 60,
        max_wait_s=wait * 60,
        partner_check=partner_check,
    )


# --------------------------------------------------------------------------------------
# B526 - quiet when neither window rose between two samples an interval apart
# --------------------------------------------------------------------------------------


def test_B526_two_equal_readings_an_interval_apart_are_quiet():
    """B526: one interval, two pings, and no window rose."""
    script = Script(reading(0.2, week=0.4), reading(0.2, week=0.4))

    outcome = run(script)

    assert outcome.quiet is True and outcome.excused is False
    assert script.slept == [600]
    assert len(outcome.samples) == 2
    assert outcome.reason.startswith("no window rose from 2026-09-01T12:00:00Z")


def test_B526_a_fall_is_quiet_and_every_shared_window_is_compared():
    """B526: only a rise counts, in either window the two readings share."""
    first, second = sample(T1, reading(0.3, week=0.5)), sample(T2, reading(0.2, week=0.5))
    assert quiet.compare(first, second) == (quiet.QUIET, ())

    weekly = sample(T2, reading(0.3, week=0.51))
    assert quiet.compare(first, weekly) == (quiet.ROSE, ("seven_day",))
    both = sample(T2, reading(0.31, week=0.51))
    assert quiet.compare(first, both) == (quiet.ROSE, ("five_hour", "seven_day"))


def test_B526_the_json_names_quiet_the_reason_the_samples_and_the_excuse():
    """B526: the document a workflow reads with jq."""
    script = Script(reading(0.2), reading(0.2))

    payload = run(script).to_json()

    assert set(payload) == {"quiet", "reason", "forced", "excused", "samples", "partner"}
    assert payload["quiet"] is True and payload["excused"] is False
    assert [row["at"] for row in payload["samples"]] == [iso(T1), iso(T2)]
    assert payload["samples"][0]["usage"]["five_hour"]["utilization"] == 0.2
    json.dumps(payload)


# --------------------------------------------------------------------------------------
# B527 - a rise waits another interval, and the run gives up after the longest wait
# --------------------------------------------------------------------------------------


def test_B527_a_rise_samples_again_and_a_later_quiet_pair_goes_ahead():
    """B527: 20% -> 24% is somebody spending; 24% -> 24% is quiet."""
    script = Script(reading(0.20), reading(0.24), reading(0.24))

    outcome = run(script)

    assert outcome.quiet is True
    assert script.slept == [600, 600]
    assert len(outcome.samples) == 3


def test_B527_a_rise_every_interval_gives_up_after_the_longest_wait():
    """B527: 40 minutes at 10 is four pairs from five samples, then nothing starts."""
    script = Script(*(reading(0.2 + n / 100) for n in range(5)))

    outcome = run(script, wait=40)

    assert outcome.quiet is False
    assert script.slept == [600] * 4
    assert script.clock.now() - T1 == timedelta(minutes=40)
    assert len(outcome.samples) == 5
    assert "\n" not in outcome.reason
    assert outcome.reason.startswith("not quiet after 40 minutes: five_hour 23% -> 24%")
    assert outcome.reason.endswith("nothing starts this run")
    assert quiet.PARTNER_IDLE in outcome.reason


# --------------------------------------------------------------------------------------
# B528 - a five-hour reset between the samples is inconclusive
# --------------------------------------------------------------------------------------


def test_B528_a_changed_five_hour_reset_is_inconclusive_and_takes_another_interval():
    """B528: 60% before the reset and 2% after it says nothing about who is spending."""
    later = "2026-09-01T17:10:00Z"
    first, second = sample(T1, reading(0.6)), sample(T2, reading(0.02, reset=later))
    assert quiet.compare(first, second) == (quiet.RESET, ())
    rising = sample(T2, reading(0.9, reset=later))
    assert quiet.compare(first, rising) == (quiet.RESET, ()), "a reset outranks a rise"

    script = Script(reading(0.6), reading(0.02, reset=later), reading(0.02, reset=later))
    outcome = run(script)

    assert outcome.quiet is True
    assert script.slept == [600, 600]


def test_B528_resets_until_the_longest_wait_give_up_with_the_reset_as_the_reason():
    """B528: an inconclusive pair is not a quiet one."""
    resets = [f"2026-09-01T1{n}:00:00Z" for n in range(3)]
    script = Script(*(reading(0.1, reset=value) for value in resets))

    outcome = run(script, wait=20)

    assert outcome.quiet is False
    assert "the five-hour window reset between" in outcome.reason


# --------------------------------------------------------------------------------------
# B529 - a refused ping is a usage stop
# --------------------------------------------------------------------------------------


def test_B529_a_refused_first_ping_stops_at_once():
    """B529: `rejected` is the subscription refusing; no interval is waited for it."""
    script = Script(reading(1.0, status="rejected"))

    outcome = run(script)

    assert outcome.quiet is False
    assert script.slept == []
    assert outcome.reason.startswith("usage stop: the subscription refused the ping")
    assert RESET in outcome.reason
    assert outcome.refused is outcome.samples[0]


def test_B529_a_refused_second_ping_stops_too_and_overage_is_no_refusal():
    """B529: a call running on extra usage was not refused (B396)."""
    script = Script(reading(0.5), reading(1.0, status="rejected"))
    assert run(script).quiet is False

    overage = dict(reading(0.5, status="rejected"), overage_in_use=True)
    first = sample(T1, reading(0.5))
    assert quiet.compare(first, sample(T2, overage)) == (quiet.QUIET, ())


# --------------------------------------------------------------------------------------
# B530 - a ping with no reading admits
# --------------------------------------------------------------------------------------


def test_B530_a_ping_that_reports_no_usage_admits_without_waiting():
    """B530: no decision may depend on the signal being there (B114)."""
    script = Script(None)

    outcome = run(script)

    assert outcome.quiet is True
    assert script.slept == []
    assert "reported no usage" in outcome.reason and "unknown is not a stop" in outcome.reason


def test_B530_a_later_ping_with_no_reading_is_skipped_and_never_admits():
    """B530: once a reading exists the check has something to go on, so a later ping that
    reports none is inconclusive, and the next one is compared with the last readable one."""
    first = sample(T1, reading(0.2))
    only_week = sample(T2, reading(None, week=0.3))
    assert quiet.compare(first, only_week) == (quiet.UNREAD, ())

    skipped = Script(reading(0.2), None, reading(0.2))
    assert run(skipped).quiet is True and skipped.slept == [600, 600]

    after_a_rise = run(Script(reading(0.2), reading(0.3), None, None, None), wait=40)
    assert after_a_rise.quiet is False
    assert "reported no usage" in after_a_rise.reason
    assert after_a_rise.partner_note == ""


def test_B527_slow_pings_count_against_the_longest_wait():
    """B527: the wait is bounded by the clock, pings included, and not only by the number of
    pairs: one-minute pairs whose pings take two minutes each stop at about an hour."""
    script = Script(*(reading(0.1 + n / 1000) for n in range(61)), ping_s=120)

    outcome = run(script, wait=60, interval=1)

    elapsed = script.clock.now() - T1
    assert outcome.quiet is False
    assert timedelta(minutes=60) <= elapsed <= timedelta(minutes=60 + 1 + 2)
    assert len(script.slept) < 60
    assert outcome.reason.startswith(f"not quiet after {round(elapsed.total_seconds() / 60)} ")


def test_B528_a_reset_after_a_rise_does_not_carry_the_rises_partner_note():
    """B528: the give-up reason and note describe the last pair."""
    later = "2026-09-01T17:10:00Z"
    script = Script(reading(0.2), reading(0.3), reading(0.01, reset=later))

    outcome = run(script, wait=20)

    assert outcome.quiet is False
    assert outcome.partner_note == ""
    assert quiet.PARTNER_IDLE not in outcome.reason
    assert "reset between" in outcome.reason


# --------------------------------------------------------------------------------------
# B531 - a rise is excused while the partner bot was spending
# --------------------------------------------------------------------------------------


def partner_runs() -> list[dict]:
    payload = json.loads((FIXTURES / "bot_night_runs.json").read_text(encoding="utf-8"))
    return payload["workflow_runs"]


def partner_jobs() -> dict[str, list[dict]]:
    payload = json.loads((FIXTURES / "bot_night_jobs.json").read_text(encoding="utf-8"))
    return {run_id: body["jobs"] for run_id, body in payload.items()}


class Reader:
    """The two reads the partner check makes, answered from the fixtures."""

    def __init__(self, runs=None, jobs=None, error: Exception | None = None) -> None:
        self.runs = partner_runs() if runs is None else runs
        self.jobs = partner_jobs() if jobs is None else jobs
        self.error = error
        self.calls: list[tuple] = []

    def workflow_file_runs(self, repo, workflow, *, per_page):
        self.calls.append(("runs", repo, workflow, per_page))
        if self.error is not None:
            raise self.error
        return copy.deepcopy(self.runs)

    def run_jobs(self, repo, run_id):
        self.calls.append(("jobs", repo, run_id))
        return copy.deepcopy(self.jobs[str(run_id)])


def one_step(run_id: int, **step) -> tuple[list[dict], dict]:
    """A single run of the partner workflow holding one job with one step."""
    run = {"id": run_id, "status": "in_progress", "updated_at": iso(T2)}
    body = {"name": "Build, check and review (item 7)", "conclusion": None, **step}
    return [run], {str(run_id): [{"id": 1, "steps": [body]}]}


def test_B531_a_spending_step_running_across_the_interval_excuses_the_rise():
    """B531: run 9004's build step started at 12:03 and is still going."""
    reader = Reader()

    steps, note = quiet.partner_spending(PARTNER, T1, T2, reader=reader)

    assert len(steps) == 1 and note == ""
    assert "run 9004" in steps[0] and "item 12" in steps[0] and "to now" in steps[0]
    assert ("runs", "jgoetzmann/JackiOh", "bot-night.yml", quiet.PARTNER_RUNS) in reader.calls
    opened = [call[2] for call in reader.calls if call[0] == "jobs"]
    assert 9001 not in opened, "a run that finished before the interval is never opened"
    assert sorted(opened) == [9002, 9003, 9004]


def test_B531_the_fixture_without_the_running_build_excuses_nothing():
    """B531: a step that ended before t1, and one still queued, spent nothing in [t1, t2]."""
    runs = [run for run in partner_runs() if run["id"] != 9004]

    steps, note = quiet.partner_spending(PARTNER, T1, T2, reader=Reader(runs=runs))

    assert steps == [] and note == quiet.PARTNER_IDLE


@pytest.mark.parametrize(
    ("step", "excused"),
    [
        ({"started_at": "2026-09-01T11:40:00Z", "completed_at": "2026-09-01T12:01:00Z"}, True),
        ({"started_at": "2026-09-01T12:09:59Z", "completed_at": None}, True),
        ({"started_at": "2026-09-01T12:10:00Z", "completed_at": None}, True),
        ({"started_at": "2026-09-01T11:00:00Z", "completed_at": "2026-09-01T12:00:00Z"}, True),
        ({"started_at": "2026-09-01T11:00:00Z", "completed_at": "2026-09-01T11:59:59Z"}, False),
        ({"started_at": "2026-09-01T12:10:01Z", "completed_at": None}, False),
        ({"started_at": None, "completed_at": None, "status": "queued"}, False),
        (
            {
                "started_at": "2026-09-01T12:05:00Z",
                "completed_at": "2026-09-01T12:05:00Z",
                "conclusion": "skipped",
            },
            False,
        ),
        (
            {
                "name": "Answer comments",
                "started_at": "2026-09-01T12:05:00Z",
                "completed_at": None,
            },
            False,
        ),
    ],
)
def test_B531_a_step_counts_only_when_it_overlaps_the_interval(step, excused):
    """B531: started by t2, not finished before t1, a spending name, and not skipped."""
    runs, jobs = one_step(77, **step)

    steps, _ = quiet.partner_spending(PARTNER, T1, T2, reader=Reader(runs=runs, jobs=jobs))

    assert bool(steps) is excused


def test_B531_an_api_error_excuses_nothing():
    """B531: failing closed; the note says why."""
    failing = Reader(error=GitHubError("github returned 500 for x: boom", status=500))

    steps, note = quiet.partner_spending(PARTNER, T1, T2, reader=failing, public=lambda: Reader())

    assert steps == []
    assert note.startswith("the partner could not be read") and "500" in note


@pytest.mark.parametrize("broken", [KeyError("id"), ValueError("not an ISO timestamp"), OSError()])
def test_B531_any_other_failure_excuses_nothing(broken):
    """B531: a malformed answer or a dropped connection is not the partner spending."""
    steps, note = quiet.partner_spending(PARTNER, T1, T2, reader=Reader(error=broken))

    assert steps == [] and note.startswith("the partner could not be read")


def test_B531_no_partner_excuses_nothing():
    """B531: an empty QUIET_PARTNER_REPO turns excusing off."""
    assert quiet.partner_spending(None, T1, T2, reader=Reader()) == ([], quiet.NO_PARTNER)


@pytest.mark.parametrize("status", [401, 403, 404])
def test_B531_a_refused_token_read_is_retried_once_without_the_token(status):
    """B531: the partner repository is public, so a token without access to it is no reason
    to give up."""
    refused = Reader(error=GitHubError(f"github returned {status} for x", status=status))
    public = Reader()

    steps, _ = quiet.partner_spending(PARTNER, T1, T2, reader=refused, public=lambda: public)

    assert steps and public.calls, "the second read went without the token"
    failed_again = Reader(error=GitHubError("github returned 404", status=404))
    steps, note = quiet.partner_spending(
        PARTNER, T1, T2, reader=refused, public=lambda: failed_again
    )
    assert steps == [] and "without the token" in note


def test_B531_the_client_reads_both_endpoints_with_the_token_then_once_without(tmp_path):
    """B531: the paths the contract names, the token on the first read, none on the retry."""
    from tests.conftest import VALID_GHP

    runs_url = (
        "https://api.github.com/repos/jgoetzmann/JackiOh/actions/workflows/bot-night.yml/runs"
        "?per_page=10"
    )
    token_opener = FakeOpener(http_error(runs_url, 404))
    client = gh_mod.GitHubClient(
        "Bright-Bots-Initiative/brightboost",
        gh_mod._Unmetered(),
        FrozenClock(T2),
        5000,
        token=VALID_GHP,
        opener=token_opener,
    )
    body = json.loads((FIXTURES / "bot_night_runs.json").read_text(encoding="utf-8"))
    jobs = json.loads((FIXTURES / "bot_night_jobs.json").read_text(encoding="utf-8"))
    public_opener = FakeOpener(
        FakeResponse(body),
        FakeResponse(jobs["9004"]),
        FakeResponse(jobs["9003"]),
        FakeResponse(jobs["9002"]),
    )
    public = gh_mod.GitHubReadOnly(
        "", gh_mod._Unmetered(), FrozenClock(T2), 50, opener=public_opener
    )

    steps, _ = quiet.partner_spending(PARTNER, T1, T2, reader=client, public=lambda: public)

    assert len(steps) == 1
    assert token_opener.urls == [runs_url]
    assert header_value(token_opener.requests[0], "Authorization") is not None
    assert public_opener.urls[0] == runs_url
    assert public_opener.urls[1] == (
        "https://api.github.com/repos/jgoetzmann/JackiOh/actions/runs/9004/jobs?per_page=100"
    )
    assert all(header_value(r, "Authorization") is None for r in public_opener.requests)


def test_B531_an_excused_rise_is_quiet_and_says_whose_spending_excused_it():
    """B531: the partner's build explains 20% -> 26%, so this run goes ahead."""
    script = Script(reading(0.20), reading(0.26))
    seen: list[tuple[datetime, datetime]] = []

    def check(start, end):
        seen.append((start, end))
        return quiet.partner_spending(PARTNER, start, end, reader=Reader())

    outcome = run(script, partner_check=check)

    assert outcome.quiet is True and outcome.excused is True
    assert seen == [(T1, T2)]
    assert "which excuses it" in outcome.reason
    assert outcome.to_json()["partner"]["steps"] == outcome.partner_steps


def test_B531_an_unexcused_rise_is_not_quiet():
    """B531: with the partner idle, the rise is somebody else."""
    runs = [run for run in partner_runs() if run["id"] != 9004]

    def check(start, end):
        return quiet.partner_spending(PARTNER, start, end, reader=Reader(runs=runs))

    outcome = run(Script(reading(0.20), reading(0.26)), wait=10, partner_check=check)

    assert outcome.quiet is False and outcome.excused is False
    assert outcome.partner_note == quiet.PARTNER_IDLE


# --------------------------------------------------------------------------------------
# B532-B533 - `harness quiet`, the skips, the ping and the ledger
# --------------------------------------------------------------------------------------


class PingRunner:
    """A runner whose ping replays readings and counts the calls."""

    name = "scripted"

    def __init__(self, *readings) -> None:
        self.readings = list(readings)
        self.pings = 0

    def run(self, request):  # pragma: no cover - `harness quiet` never runs a stage
        raise AssertionError("harness quiet ran a stage")

    def ping(self) -> RunResult:
        self.pings += 1
        assert self.readings, "harness quiet pinged more often than the script allows"
        usage = self.readings.pop(0)
        return RunResult(
            ok=True,
            text="ok",
            turns=1,
            duration_ms=1,
            session_id="s",
            exit_code=0,
            transcript=(),
            error=None,
            usage=usage,
        )


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repository whose clock is frozen at T1 and moves only when `SLEEP` is called."""
    monkeypatch.chdir(tmp_path)
    clock = FrozenClock(T1)
    slept: list[float] = []

    def sleep(seconds):
        slept.append(seconds)
        clock.advance(seconds)

    monkeypatch.setattr(context_mod, "SystemClock", lambda: clock)
    monkeypatch.setattr(cli, "SLEEP", sleep)
    return type("Repo", (), {"path": tmp_path, "clock": clock, "slept": slept})


def use_runner(monkeypatch, runner) -> None:
    monkeypatch.setattr(context_mod, "get_runner", lambda config: runner)


def quiet_json(capsys, *argv: str) -> dict:
    capsys.readouterr()
    assert cli.main(["--json", "quiet", *argv]) == 0
    return json.loads(capsys.readouterr().out)


def stored_window(root: Path) -> dict:
    return json.loads((root / "state" / "ledger.json").read_text(encoding="utf-8"))["window"]


def test_B532_the_command_is_quiet_on_the_fake_backend_and_keeps_every_reading(
    repo, monkeypatch, capsys
):
    """B532 / B533: `BACKEND=fake` replays `ping.json` twice, spends nothing, and each reading
    becomes the ledger's usage observation; the verdict is stored beside it."""
    write_d2_repo(repo.path, QUIET_PARTNER_REPO="")

    payload = quiet_json(capsys)

    assert payload["quiet"] is True and payload["forced"] is False
    assert repo.slept == [600]
    assert len(payload["samples"]) == 2
    window = stored_window(repo.path)
    assert window["usage"]["five_hour"]["utilization"] == 0.12
    assert window["usage"]["observed_at"] == iso(T2)
    assert window["quiet"] == {"at": iso(T2), "quiet": True, "reason": payload["reason"]}


def test_B532_the_command_gives_up_after_the_wait_and_still_exits_zero(repo, monkeypatch, capsys):
    """B532: not quiet is a normal outcome; the text form is one line."""
    write_d2_repo(repo.path, QUIET_PARTNER_REPO="", QUIET_MAX_WAIT_MINUTES="20")
    use_runner(monkeypatch, PingRunner(reading(0.1), reading(0.2), reading(0.3)))

    capsys.readouterr()
    assert cli.main(["quiet"]) == 0
    out = capsys.readouterr().out.strip()

    assert out.startswith("not quiet: not quiet after 20 minutes")
    assert "\n" not in out
    assert repo.slept == [600, 600]
    assert stored_window(repo.path)["quiet"]["quiet"] is False


@pytest.mark.parametrize("argv", [["--force"], ["--force", "--items", "4"]])
def test_B532_force_skips_the_check_and_pings_nothing(repo, monkeypatch, capsys, argv):
    """B532: the operator asked for this run now."""
    write_d2_repo(repo.path)
    runner = PingRunner()
    use_runner(monkeypatch, runner)

    payload = quiet_json(capsys, *argv)

    assert payload["quiet"] is True and payload["forced"] is True
    assert runner.pings == 0 and repo.slept == []


def test_B532_an_item_forced_with_force_skips_the_check(repo, monkeypatch, capsys):
    """B532: `/harness work ... --force` marked item 4 in the ledger; item 5 was not."""
    write_d2_repo(repo.path, QUIET_PARTNER_REPO="")
    ledger = Ledger.empty("2026-08-31T00:00:00Z")
    ledger.force(4)
    (repo.path / "state").mkdir()
    (repo.path / "state" / "ledger.json").write_text(ledger.to_json(), encoding="utf-8")
    runner = PingRunner(reading(0.2), reading(0.2))
    use_runner(monkeypatch, runner)

    forced = quiet_json(capsys, "--items", "5", "4")
    assert forced["forced"] is True and "item 4" in forced["reason"] and runner.pings == 0

    unforced = quiet_json(capsys, "--items", "5")
    assert unforced["forced"] is False and runner.pings == 2


@pytest.mark.parametrize(
    ("setup", "expected"),
    [
        ("disabled", "QUIET_ENABLED=false"),
        ("commanded", "halted"),
        ("stopped", "session usage 90% >= 70%"),
        ("limited", "rate limited until"),
    ],
)
def test_B532_a_known_answer_needs_no_ping(repo, monkeypatch, capsys, setup, expected):
    """B532: the check off, a halt, the usage stop and a stored rate limit are each answered
    without a model call."""
    write_d2_repo(repo.path, QUIET_ENABLED="false" if setup == "disabled" else "true")
    ledger = Ledger.empty("2026-08-31T00:00:00Z")
    if setup == "commanded":
        ledger.request_halt("jgoetzmann", "lunch", iso(T1))
    if setup == "stopped":
        ledger.observe_usage(reading(0.9), iso(T1))
    if setup == "limited":
        ledger.set_rate_limited("2026-09-01T13:00:00Z")
    (repo.path / "state").mkdir()
    (repo.path / "state" / "ledger.json").write_text(ledger.to_json(), encoding="utf-8")
    runner = PingRunner()
    use_runner(monkeypatch, runner)

    payload = quiet_json(capsys)

    assert runner.pings == 0 and repo.slept == []
    assert expected in payload["reason"]
    assert payload["quiet"] is (setup == "disabled")


def test_B532_the_committed_halt_is_one_json_document_and_exit_zero(repo, capsys):
    """B532: a workflow feeds this stdout to jq, so a halt answers in JSON too."""
    write_d2_repo(repo.path)
    (repo.path / ".harness").mkdir()
    (repo.path / ".harness" / "HALT").write_text("stop\n", encoding="utf-8")

    payload = quiet_json(capsys)

    assert payload["quiet"] is False and payload["reason"].startswith("halted")


def test_B532_a_refused_ping_records_the_rate_limit_until_its_reset(repo, monkeypatch, capsys):
    """B532 / B529: a refusal holds every call until the refused window resets (D71)."""
    write_d2_repo(repo.path)
    use_runner(monkeypatch, PingRunner(reading(1.0, status="rejected")))

    payload = quiet_json(capsys)

    assert payload["quiet"] is False and payload["reason"].startswith("usage stop")
    assert stored_window(repo.path)["rate_limited_until"] == RESET


@pytest.mark.parametrize(
    ("window", "argv", "quiet_expected"),
    [
        (("daily 11:00", "daily 12:05"), ["--window"], False),
        (("daily 11:00", "daily 12:05"), [], True),
        (("daily 13:00", "daily 14:00"), ["--window"], True),
        (("daily 11:00", "daily 16:00"), ["--window"], True),
    ],
)
def test_B532_a_run_window_that_closes_during_the_wait_starts_nothing(
    repo, monkeypatch, capsys, window, argv, quiet_expected
):
    """B532: with `--window`, a check that began inside the run window and ends after it says
    not quiet, because the window bounds when work starts; a check that began outside it, for
    a carried or forced item, is not held by it."""
    start, end = window
    write_d2_repo(
        repo.path, QUIET_PARTNER_REPO="", RUN_WINDOW_START=start, RUN_WINDOW_END=end
    )
    use_runner(monkeypatch, PingRunner(reading(0.2), reading(0.2)))

    payload = quiet_json(capsys, *argv)

    assert payload["quiet"] is quiet_expected
    if not quiet_expected:
        assert payload["reason"].startswith("the run window (daily 11:00-12:05 UTC) closed")


class Cancelled(Exception):
    """What a cancelled job looks like from inside the wait."""


def test_B532_each_reading_is_saved_as_it_is_taken(repo, monkeypatch, capsys):
    """B532: a job cancelled during the wait still leaves its readings for the ledger commit."""
    write_d2_repo(repo.path, QUIET_PARTNER_REPO="")
    use_runner(monkeypatch, PingRunner(reading(0.37)))

    def cancel(seconds):
        raise Cancelled()

    monkeypatch.setattr(cli, "SLEEP", cancel)

    with pytest.raises(Cancelled):
        cli.main(["--json", "quiet"])

    assert stored_window(repo.path)["usage"]["five_hour"]["utilization"] == 0.37


class FailingPing(PingRunner):
    """A ping whose call failed with the given words and no reading."""

    def __init__(self, error: str) -> None:
        super().__init__()
        self.error = error

    def ping(self) -> RunResult:
        self.pings += 1
        return RunResult(
            ok=False,
            text="",
            turns=None,
            duration_ms=None,
            session_id=None,
            exit_code=1,
            transcript=(),
            error=self.error,
        )


def test_B532_what_a_failed_ping_said_is_redacted_everywhere_it_lands(repo, monkeypatch, capsys):
    """B532: the reason can quote a ping's error, which reaches the ledger and the artifact."""
    from tests.conftest import VALID_GHP

    write_d2_repo(repo.path, QUIET_PARTNER_REPO="")
    use_runner(monkeypatch, FailingPing(f"push failed with {VALID_GHP}"))

    capsys.readouterr()
    assert cli.main(["--json", "quiet"]) == 0
    out = capsys.readouterr().out

    payload = json.loads(out)
    assert payload["quiet"] is True and "reported no usage" in payload["reason"]
    assert VALID_GHP not in out
    assert VALID_GHP not in (repo.path / "state" / "ledger.json").read_text(encoding="utf-8")


def test_B532_the_partner_excuses_a_rise_through_the_harness_client(repo, monkeypatch, capsys):
    """B532 / B531: the committed partner, read through the context's GitHub client."""
    write_d2_repo(
        repo.path,
        QUIET_PARTNER_REPO="jgoetzmann/JackiOh",
        QUIET_PARTNER_WORKFLOW="bot-night.yml",
        QUIET_PARTNER_STEPS="Build, check and review",
    )
    use_runner(monkeypatch, PingRunner(reading(0.2), reading(0.3)))
    reader = Reader()
    monkeypatch.setattr(
        gh_mod.GitHubReadOnly,
        "workflow_file_runs",
        lambda self, repo, workflow, *, per_page: reader.workflow_file_runs(
            repo, workflow, per_page=per_page
        ),
    )
    monkeypatch.setattr(
        gh_mod.GitHubReadOnly, "run_jobs", lambda self, repo, run_id: reader.run_jobs(repo, run_id)
    )

    payload = quiet_json(capsys)

    assert payload["quiet"] is True and payload["excused"] is True
    assert "run 9004" in payload["partner"]["steps"][0]


def test_B533_the_ping_argv_prompt_directory_and_environment(monkeypatch):
    """B533: the shared contract's argv, the prompt on stdin, an empty directory outside the
    repository that is gone afterwards, and no API key in the child's environment."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-" + "x" * 30)
    stream = "\n".join(
        [
            json.dumps({"type": "system", "subtype": "init"}),
            json.dumps(
                {
                    "type": "rate_limit_event",
                    "rate_limit_info": {
                        "status": "allowed",
                        "unifiedWindows": {"five_hour": {"utilization": 0.1, "resetsAt": 1}},
                    },
                }
            ),
            json.dumps(
                {
                    "type": "rate_limit_event",
                    "rate_limit_info": {
                        "status": "allowed",
                        "unifiedWindows": {
                            "five_hour": {"utilization": 0.25, "resetsAt": 1788274800}
                        },
                    },
                }
            ),
            json.dumps({"type": "result", "subtype": "success", "result": "ok", "num_turns": 1}),
        ]
    )
    spawn = SpawnRecorder(stdout=stream)
    seen: dict = {}

    def recording(argv, **kwargs):
        cwd = Path(kwargs["cwd"])
        seen["cwd"] = cwd
        seen["empty"] = cwd.is_dir() and not any(cwd.iterdir())
        return spawn(argv, **kwargs)

    result = ClaudeCliRunner(spawn=recording).ping()

    assert spawn.argv == ["claude", *PING_FLAGS]
    assert PING_FLAGS == (
        "--print", "--output-format", "stream-json", "--verbose", "--model", "haiku",
        "--max-turns", "1", "--strict-mcp-config",
    )
    assert spawn.kwargs["input"] == PING_PROMPT == "Reply with the word ok."
    assert "ANTHROPIC_API_KEY" not in spawn.env
    assert seen["empty"] and not seen["cwd"].exists()
    assert REPO_ROOT not in seen["cwd"].resolve().parents
    assert result.usage["five_hour"] == {"utilization": 0.25, "resets_at": "2026-09-01T15:00:00Z"}


def test_B533_a_ping_that_cannot_run_carries_no_reading():
    """B533: a timeout or a missing binary is a sample with an error and no usage."""

    def timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 1)

    def missing(argv, **kwargs):
        raise FileNotFoundError("claude")

    assert ClaudeCliRunner(spawn=timeout).ping().usage is None
    result = ClaudeCliRunner(spawn=missing).ping()
    assert result.usage is None and result.exit_code == runner_cli.EXIT_NOT_EXECUTABLE


def test_B533_a_ping_cut_short_keeps_the_reading_it_already_printed():
    """B533: the `rate_limit_event` comes before the result line, so a timeout after it still
    carries a reading; the captured output arrives as bytes."""
    event = {
        "type": "rate_limit_event",
        "rate_limit_info": {
            "status": "allowed",
            "unifiedWindows": {"five_hour": {"utilization": 0.3, "resetsAt": 1788274800}},
        },
    }

    def timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 1, output=(json.dumps(event) + "\n").encode())

    result = ClaudeCliRunner(spawn=timeout).ping()

    assert result.ok is False and result.exit_code == runner_cli.EXIT_TIMEOUT
    assert result.usage["five_hour"]["utilization"] == 0.3


def test_B533_the_fake_backend_replays_ping_json():
    """B533: `BACKEND=fake` answers the ping from the committed fixture."""
    shipped = json.loads((DEFAULT_FIXTURES_DIR / "ping.json").read_text(encoding="utf-8"))

    result = FakeRunner().ping()

    assert result.ok is True
    assert result.usage == shipped["usage"]


# --------------------------------------------------------------------------------------
# B534 - the six keys
# --------------------------------------------------------------------------------------


def test_B534_the_committed_defaults_are_the_partner_and_ten_of_forty(tmp_path):
    """B534: `.env.example` ships the check on, 10 and 40 minutes, and the JackiOh night bot."""
    example = tmp_path / ".env"
    example.write_text(
        (REPO_ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8"
    )

    config = load_config(env_path=example, environ={})

    assert config.quiet_enabled is True
    assert (config.quiet_interval_minutes, config.quiet_max_wait_minutes) == (10, 40)
    assert config.quiet_partner_repo == "jgoetzmann/JackiOh"
    assert config.quiet_partner_workflow == "bot-night.yml"
    assert config.quiet_partner_steps == ("Build, check and review",)
    for key in (
        "QUIET_ENABLED",
        "QUIET_INTERVAL_MINUTES",
        "QUIET_MAX_WAIT_MINUTES",
        "QUIET_PARTNER_REPO",
        "QUIET_PARTNER_WORKFLOW",
        "QUIET_PARTNER_STEPS",
    ):
        assert key in CONFIG_JSON_KEYS


def test_B534_the_contract_step_names_are_not_prefixes_of_each_other():
    """B534: this harness's own step is not one the partner counts as spending."""
    assert not QUIET_STEP.startswith("Build, check and review")


def half_partner(workflow: str, steps: str) -> dict[str, str]:
    """A partner repository with a workflow or step list the check cannot use."""
    return {
        "QUIET_PARTNER_REPO": "a/b",
        "QUIET_PARTNER_WORKFLOW": workflow,
        "QUIET_PARTNER_STEPS": steps,
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"QUIET_INTERVAL_MINUTES": "0"},
        {"QUIET_INTERVAL_MINUTES": "61"},
        {"QUIET_MAX_WAIT_MINUTES": "5"},
        {"QUIET_MAX_WAIT_MINUTES": "61"},
        {"QUIET_ENABLED": "sometimes"},
        {"QUIET_PARTNER_REPO": "JackiOh"},
        half_partner("", "x"),
        half_partner("night", "x"),
        half_partner("n.yml", ""),
        half_partner("n.yml", "|"),
    ],
)
def test_B534_an_out_of_range_or_half_configured_key_is_a_startup_error(
    tmp_path, write_env, overrides
):
    """B534: refused at load, naming the key."""
    path = write_env(tmp_path / ".env", **overrides)

    with pytest.raises(ConfigError) as excinfo:
        load_config(env_path=path, environ={})

    assert any(key in str(excinfo.value) for key in overrides)


def test_B534_an_empty_partner_needs_nothing_else_and_steps_split_on_the_bar(tmp_path, write_env):
    """B534: a prefix may hold a comma, so `|` separates them."""
    none = load_config(env_path=write_env(tmp_path / ".env", QUIET_PARTNER_REPO=""), environ={})
    assert (none.quiet_partner_repo, none.quiet_partner_steps) == ("", ())

    path = write_env(
        tmp_path / "two" / ".env",
        QUIET_PARTNER_REPO="a/b",
        QUIET_PARTNER_WORKFLOW="night.yaml",
        QUIET_PARTNER_STEPS=" Build, check and review | Answer ",
    )
    two = load_config(env_path=path, environ={})
    assert two.quiet_partner_steps == ("Build, check and review", "Answer")


def test_B534_doctor_prints_the_prefixes_as_they_are_written(tmp_path, monkeypatch, capsys):
    """B534: a tuple is shown joined with the bar, not as a Python repr."""
    monkeypatch.chdir(tmp_path)
    write_d2_repo(
        tmp_path,
        MIN_FREE_DISK_GB="0",
        QUIET_PARTNER_REPO="a/b",
        QUIET_PARTNER_WORKFLOW="n.yml",
        QUIET_PARTNER_STEPS="Build, check and review|Answer",
    )
    config = load_config(env_path=tmp_path / ".env", environ={})

    keys = cli._doctor_config_keys(cli.build_parser().parse_args(["doctor"]), config, None, [])

    assert keys["QUIET_PARTNER_STEPS"] == "Build, check and review|Answer"
    assert keys["QUIET_ENABLED"] == "True"


# --------------------------------------------------------------------------------------
# B535 - where the check sits in the workflows
# --------------------------------------------------------------------------------------


def step_names(name: str) -> list[str]:
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    return [label.strip() for label in re.findall(r"^      - name: (.+)$", text, re.M)]


def step_block(name: str, label: str) -> str:
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    start = text.index(f"      - name: {label}\n")
    end = text.find("\n      - name: ", start + 1)
    return text[start:] if end < 0 else text[start:end]


@pytest.mark.parametrize(
    ("name", "spender"),
    [
        ("implement.yml", "Run planned items (harness run --item)"),
        ("discover.yml", "Discover and propose (harness discover, harness propose)"),
    ],
)
def test_B535_the_check_is_the_step_right_before_the_spending_step(name, spender):
    """B535: after dispatch, immediately before the spending step, which waits on its output."""
    names = step_names(name)

    assert names.count(QUIET_STEP) == 1
    assert names.index(QUIET_STEP) + 1 == names.index(spender)
    assert names.index("harness dispatch") < names.index(QUIET_STEP)
    check = step_block(name, QUIET_STEP)
    assert "id: quiet" in check
    assert re.search(r"harness --json quiet\b", check)
    assert '"quiet=true" >> "$GITHUB_OUTPUT"' in check
    assert '"quiet=false" >> "$GITHUB_OUTPUT"' in check
    assert "steps.quiet.outputs.quiet == 'true'" in step_block(name, spender).splitlines()[1]


def test_B535_an_issue_input_or_a_named_mode_is_the_operator_asking_now():
    """B535: implement passes `--force` for an `issue` input and the plan's items otherwise;
    discover passes it for any dispatched mode but triage."""
    implement = step_block("implement.yml", QUIET_STEP)
    assert 'if [ -n "${INPUT_ISSUE// }" ]; then\n            args=(--force)' in implement
    assert "args=(--window --items ${items})" in implement
    assert 'echo "quiet=true" >> "$GITHUB_OUTPUT"\n            exit 0' in implement

    discover = step_block("discover.yml", QUIET_STEP)
    assert "--window" not in discover, "discover is not held by the run window (D32)"
    assert 'mode="${INPUT_MODE:-triage}"' in discover
    assert '[ "${mode}" != "triage" ]' in discover
    assert 'force="--force"' in discover


@pytest.mark.parametrize("name", ["implement.yml", "discover.yml"])
def test_B535_the_committed_halt_is_read_again_before_a_quiet_run_starts(name):
    """B535: the wait can be long, so `.harness/HALT` on the default branch is read once more
    after the check says quiet, and its presence turns the answer to not quiet."""
    check = step_block(name, QUIET_STEP)
    reread = check.index("contents/.harness/HALT?ref=${REF}")
    assert check.index("harness --json quiet") < reread
    assert 'if [ "${code}" = "200" ]; then\n              quiet="false"' in check
    assert "halted by .harness/HALT" not in check, "B149 keeps that line to the first step"
    for key in ("GH_TOKEN: ${{ github.token }}", "REPO: ${{ github.repository }}"):
        assert key in check


@pytest.mark.parametrize(
    "name", ["feedback.yml", "ack.yml", "heartbeat.yml", "ops.yml", "watchdog.yml"]
)
def test_B535_the_workflows_that_answer_a_person_do_not_wait(name):
    """B535: a person commanding the harness is using it on purpose."""
    assert "harness quiet" not in (WORKFLOWS / name).read_text(encoding="utf-8")
    assert "--json quiet" not in (WORKFLOWS / name).read_text(encoding="utf-8")


def test_B535_the_steps_the_partner_watches_keep_their_names():
    """B535: the JackiOh bot counts these steps of this harness as spending, by prefix."""
    names = step_names("implement.yml") + step_names("discover.yml") + step_names("feedback.yml")
    watched = ("Run planned items", "Discover and propose", "Sweep keywords", "Reconcile stale")
    for prefix in watched:
        assert any(label.startswith(prefix) for label in names), prefix
    assert not any(label.startswith("Build, check and review") for label in names)


# --------------------------------------------------------------------------------------
# B536 - status says when the last check ran and what it found
# --------------------------------------------------------------------------------------


def test_B536_the_ledger_keeps_the_last_check_and_an_older_file_round_trips():
    """B536: written only once a check has run, so a ledger without one is unchanged."""
    ledger = Ledger.empty("2026-08-31T00:00:00Z")
    before = ledger.to_json()
    assert "quiet" not in json.loads(before)["window"]
    assert Ledger.from_json(before).to_json() == before

    ledger.record_quiet(at=iso(T2), quiet=False, reason="not quiet after 40 minutes")
    again = Ledger.from_json(ledger.to_json())

    assert again.quiet_check() == {
        "at": iso(T2),
        "quiet": False,
        "reason": "not quiet after 40 minutes",
    }


def test_B536_status_names_the_last_check(repo, monkeypatch, capsys):
    """B536: one line in `harness status`, and the record in its JSON."""
    write_d2_repo(repo.path, QUIET_PARTNER_REPO="")
    monkeypatch.setattr(gh_mod.GitHubReadOnly, "workflow_runs", lambda self, repo, **kw: [])
    capsys.readouterr()
    assert cli.main(["status"]) == 0
    assert "quiet check" not in capsys.readouterr().out

    quiet_json(capsys)
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert f"quiet check: quiet at {iso(T2)}: no window rose" in out

    assert cli.main(["--json", "status"]) == 0
    assert json.loads(capsys.readouterr().out)["quiet"]["quiet"] is True
