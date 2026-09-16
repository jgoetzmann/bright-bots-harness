"""D71: a subscription refusal is a rate limit that ends at its reset. B398-B402.

An exhausted weekly allowance refuses a call in about two seconds: a `rate_limit_event` with
status "rejected" and seven_day at 1.0, then a result with `subtype: "success"`,
`is_error: true` and the message in `result`, at exit 0.

B395-B397 (the runner) live in tests/test_runner_cli.py. This file holds what happens after
the runner: the refusal as a B120 outcome end to end (B398), a stored reading that expires at
its own reset (B399), and the three workflows that carry the evidence (B400-B402). Every clock
here is frozen, on both sides of the reset.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

import harness.__main__ as cli
import harness.context as context_mod
import harness.gh as gh_mod
from harness import links, priority
from harness.clock import FrozenClock, iso
from harness.config import KNOWN_KEYS, load_config
from harness.dispatcher import Candidate, plan, usage_stop
from harness.errors import BudgetExhausted, GitHubError
from harness.governor import Authorization, Governor
from harness.ledger import Ledger
from harness.runner.cli import ClaudeCliRunner
from harness.store import Store

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

OBSERVED = "2026-09-13T12:45:53Z"
RESET = "2026-09-15T20:00:00Z"
FIVE_HOUR_RESET = "2026-09-13T17:40:00Z"
PERIOD_START = "2026-09-07T00:00:00Z"  # the Monday before, WEEKLY_RESET_DAY=monday

#: window.usage exactly as the live ledger on harness-state recorded it at the failing second.
INCIDENT_USAGE = {
    "five_hour": {"utilization": 0.0, "resets_at": FIVE_HOUR_RESET},
    "seven_day": {"utilization": 1.0, "resets_at": RESET},
    "status": "rejected",
    "observed_at": OBSERVED,
}

SECRET = "sk-ant-" + "Q1w2E3r4T5y6U7i8O9p0A1s2D3f4G5"


def at(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


BEFORE_RESET = at("2026-09-15T19:59:59Z")
MONDAY_HEARTBEAT = at("2026-09-14T09:05:00Z")
AT_RESET = at(RESET)
NEXT_SUNDAY = at("2026-09-20T07:17:00Z")  # the next scheduled discover


def incident_ledger(*, rate_limited_until: str | None) -> Ledger:
    led = Ledger.empty(PERIOD_START)
    led.observe_usage(json.loads(json.dumps(INCIDENT_USAGE)), OBSERVED)
    led.set_rate_limited(rate_limited_until)
    return led


@pytest.fixture
def config(tmp_path, write_env):
    """The .env.example thresholds: weekly stop 90%, session stop 70%, an always-open window."""
    return load_config(env_path=write_env(tmp_path / "cfg" / ".env"), environ={})


@pytest.fixture
def empty_store(config):
    store = Store(config.db_path, FrozenClock(AT_RESET))
    store.migrate()
    yield store
    store.close()


def _plan(config, led, now):
    return plan(
        now=now,
        ledger=led,
        config=config,
        candidates=(Candidate(issue=816),),
        merged=(),
        halted=False,
    )


# --------------------------------------------------------------------------------------
# B399 - a stored refusal holds until its window resets, and not a second longer
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("now", [MONDAY_HEARTBEAT, BEFORE_RESET], ids=["monday", "1s-before"])
def test_b399_before_the_reset_the_stored_refusal_stops_everything(config, empty_store, now):
    """B399: until 2026-09-15T20:00:00Z the 100% reading is a stop for the dispatcher, the
    governor, the audit and suggestion gates, and the status headline alike."""
    led = incident_ledger(rate_limited_until=None)

    assert usage_stop(led, config, now=now) == "weekly usage 100% >= 90%"
    assert _plan(config, led, now).reason == "weekly usage 100% >= 90%"
    governor = Governor(config, FrozenClock(now), led)
    with pytest.raises(BudgetExhausted, match="weekly usage 100%"):
        governor.authorize(1, "discover")
    assert priority.admit("audit", store=empty_store, ledger=led, config=config, now=now)
    assert priority.admit("suggested", store=empty_store, ledger=led, config=config, now=now)
    assert "nothing will start" in "\n".join(links.usage_headline(led, config, now))


def test_b399_before_the_reset_the_rate_limit_b120_recorded_holds_too(config):
    """B399/B120: with the reset recorded as rate_limited_until, the plan names it first."""
    led = incident_ledger(rate_limited_until=RESET)

    assert _plan(config, led, BEFORE_RESET).reason == f"rate limited until {RESET}"


@pytest.mark.parametrize("now", [AT_RESET, NEXT_SUNDAY], ids=["at-reset", "next-sunday"])
@pytest.mark.parametrize("limited", [RESET, None], ids=["b120-recorded", "never-recorded"])
def test_b399_at_the_reset_the_refusal_expires_with_no_command(config, empty_store, now, limited):
    """B399: at the reset instant, and at the next scheduled discover, nothing stops: the plan
    starts the candidate, the governor authorizes, both gates admit, and the headline stops
    claiming a stop. Whether or not B120 recorded the reset, the harness resumes by itself."""
    led = incident_ledger(rate_limited_until=limited)

    assert led.rate_limited(iso(now)) is False
    assert usage_stop(led, config, now=now) is None
    assert usage_stop(led, config, carry=True, now=now) is None
    result = _plan(config, led, now)
    assert result.start == (816,), result.reason
    governor = Governor(config, FrozenClock(now), led)
    assert isinstance(governor.authorize(1, "discover"), Authorization)
    for cls in ("audit", "suggested"):
        assert priority.admit(cls, store=empty_store, ledger=led, config=config, now=now) is None
    assert "nothing will start" not in "\n".join(links.usage_headline(led, config, now))


def test_b399_the_governor_does_not_wait_for_its_own_week_to_roll(config, empty_store):
    """B399: the deadlock this closes. Before it, a reading expired only when `period_start`
    passed its `observed_at`, and `period_start` moves when the governor rolls the window --
    which `authorize` does AFTER the usage stop has already refused. So the stop refused the one
    call that could have brought a fresh reading, on every run, however long after the reset."""
    led = incident_ledger(rate_limited_until=None)
    governor = Governor(config, FrozenClock(NEXT_SUNDAY), led)

    assert governor.usage_stop_reason() is None
    assert isinstance(governor.authorize(1, "discover"), Authorization)


def test_b399_each_window_expires_at_its_own_reset(config):
    """B399: the five-hour window resets on its own schedule, and only its reading expires."""
    led = Ledger.empty(PERIOD_START)
    led.observe_usage(
        {
            "five_hour": {"utilization": 0.8, "resets_at": FIVE_HOUR_RESET},
            "seven_day": {"utilization": 0.5, "resets_at": RESET},
            "status": "allowed",
        },
        OBSERVED,
    )

    assert usage_stop(led, config, now=at("2026-09-13T17:39:59Z")) == "session usage 80% >= 70%"
    assert usage_stop(led, config, now=at(FIVE_HOUR_RESET)) is None
    assert led.session_utilization(at(FIVE_HOUR_RESET)) is None
    assert led.weekly_utilization(at(FIVE_HOUR_RESET)) == pytest.approx(0.5)


def test_b399_a_reading_with_no_stated_reset_is_kept(config):
    """B399/B114: expiry needs the window's own reset. A reading without one is kept exactly as
    before, and a caller that passes no clock sees exactly what it saw before."""
    led = Ledger.empty(PERIOD_START)
    led.observe_usage({"seven_day": {"utilization": 0.95}, "status": "allowed"}, OBSERVED)

    assert usage_stop(led, config, now=NEXT_SUNDAY) == "weekly usage 95% >= 90%"
    expired = incident_ledger(rate_limited_until=None)
    assert usage_stop(expired, config) == "weekly usage 100% >= 90%"


# --------------------------------------------------------------------------------------
# B398 - the refusal, end to end: runner -> run_model -> discover -> CLI -> workflow
# --------------------------------------------------------------------------------------


def _refusal_stdout(message: str) -> str:
    event = {
        "type": "rate_limit_event",
        "rate_limit_info": {
            "status": "rejected",
            "resetsAt": 1789502400,
            "rateLimitType": "seven_day",
            "overageStatus": "rejected",
            "isUsingOverage": False,
            "unifiedWindows": {
                "five_hour": {"utilization": 0.0, "resetsAt": 1789321200},
                "seven_day": {"utilization": 1.0, "resetsAt": 1789502400},
            },
        },
    }
    result = {
        "type": "result",
        "subtype": "success",
        "is_error": True,
        "result": message,
        "num_turns": 1,
        "total_cost_usd": 0.0,
        "duration_ms": 2013,
        "session_id": "refused",
    }
    return json.dumps(event) + "\n" + json.dumps(result) + "\n"


def test_b398_a_refused_discover_exits_0_records_the_reset_and_keeps_the_evidence(
    tmp_path, monkeypatch, capsys, write_env
):
    """B398/B120/D71: the 09-13 refusal through the real CLI runner, run_model, discover's triage
    and `harness --json discover`. Exit 0; stdout is JSON the workflow's jq can read; the ledger
    holds rate_limited_until at the seven-day reset and the rejected reading; the item is still
    queued; exactly one model call; and the transcript -- redacted -- holds the CLI's raw result.

    Neutralised: every config key in the host environment, the clock (frozen at the failing
    second), the runner's spawn, and GitHub (any read raises)."""
    for key in KNOWN_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    write_env(tmp_path / ".env")
    write_env(tmp_path / ".env.example")
    monkeypatch.setattr(context_mod, "SystemClock", lambda: FrozenClock(at(OBSERVED)))

    calls: list[list[str]] = []
    message = f"Weekly usage exhausted for {SECRET} · resets Sep 15, 8pm (UTC)"

    def spawn(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, _refusal_stdout(message), "")

    monkeypatch.setattr(
        context_mod, "get_runner", lambda config: ClaudeCliRunner(spawn=spawn, capture_usage=True)
    )

    def no_github(self, path):
        raise GitHubError(f"no request may be issued in this test: {path}")

    monkeypatch.setattr(gh_mod.GitHubReadOnly, "get", no_github)

    assert cli.main(["init"]) == 0
    store = Store(tmp_path / "harness.db")
    store.migrate()
    item_id = store.create_work_item(kind="issue", external_ref="issue:816", title="bundle size")
    store.close()
    (tmp_path / "state").mkdir(exist_ok=True)
    (tmp_path / "state" / "ledger.json").write_text(
        Ledger.empty(PERIOD_START).to_json(), encoding="utf-8", newline="\n"
    )
    capsys.readouterr()

    rc = cli.main(["--json", "discover", "--mode", "triage"])

    out, err = capsys.readouterr()
    assert rc == 0, f"a refusal is a normal outcome (B120); got {rc}: {out} {err}"
    assert len(calls) == 1, "one model call, and nothing retried"
    assert json.loads(out) == {"rate_limited_until": RESET}
    assert "triage ranking failed" not in out + err
    assert "error:" not in err

    window = json.loads((tmp_path / "state" / "ledger.json").read_text(encoding="utf-8"))["window"]
    assert window["rate_limited_until"] == RESET
    assert window["usage"]["status"] == "rejected"
    assert window["usage"]["seven_day"] == {"utilization": 1.0, "resets_at": RESET}
    assert window["usage"]["observed_at"] == OBSERVED

    store = Store(tmp_path / "harness.db")
    assert store.get_work_item(item_id).state == "discovered"
    store.close()

    transcripts = list((tmp_path / "runs").rglob("discover.jsonl"))
    assert len(transcripts) == 1
    written = transcripts[0].read_text(encoding="utf-8")
    assert '"subtype": "success"' in written and '"is_error": true' in written
    assert SECRET not in written, "the transcript is uploaded (B400), so it must be redacted"


def test_b398_without_json_the_refusal_is_still_the_plain_line(monkeypatch, capsys):
    """B398: `--json` changes the document, not the outcome; the plain form is unchanged."""
    from harness.errors import RateLimited

    def refused(args):
        raise RateLimited("discover rate limited", reset_at=RESET)

    monkeypatch.setitem(cli.COMMANDS, "status", refused)

    assert cli.main(["status"]) == 0
    assert capsys.readouterr().out.strip() == f"rate limited until {RESET}"
    assert cli.main(["--json", "status"]) == 0
    assert json.loads(capsys.readouterr().out) == {"rate_limited_until": RESET}


def _wf(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def test_b398_discover_yml_ends_a_refused_run_green_before_harvesting_ids():
    """B398: the workflow half. A refusal exits 0 and prints the reset; the step says so and
    stops before the id harvest, so the ledger step still records the reset."""
    text = _wf("discover.yml")

    guard = text.index(".rate_limited_until // empty")
    assert guard < text.index("[.created[]?]")
    assert "nothing proposed this run" in text[guard : text.index("[.created[]?]")]


# --------------------------------------------------------------------------------------
# B400 - the redacted transcripts are uploaded
# --------------------------------------------------------------------------------------


def _upload_paths(text: str) -> list[str]:
    block = text.split("uses: actions/upload-artifact@v4", 1)[1]
    match = re.search(r"path: \|\r?\n((?:[ \t]+\S[^\n]*(?:\n|$))+)", block)
    assert match, "the upload step has no path list"
    return [line.strip() for line in match.group(1).splitlines()]


@pytest.mark.parametrize("name", ["discover.yml", "implement.yml", "feedback.yml"])
def test_b400_every_spending_workflow_uploads_the_transcripts(name):
    """B400/D71: `transcript/<stage>.jsonl` is written through redact and holds the CLI's raw
    result object. discover.yml's list had no `.jsonl`, so #43's evidence was discarded."""
    paths = _upload_paths(_wf(name))

    assert "runs/**/*.jsonl" in paths
    assert "!runs/**/clone/**" in paths, "the exclusions still follow the includes"


# --------------------------------------------------------------------------------------
# B401 - the ops issue carries the harness's own error lines, not only the tail
# --------------------------------------------------------------------------------------


def test_b401_the_ops_issue_carries_the_error_lines_as_well_as_the_tail():
    """B401/D71: #43's last 50 lines were upload noise. The error forms are matched wherever they
    fell in the failing job, redacted, bounded, and put ahead of the tail, which stays."""
    text = _wf("ops.yml")

    assert "tail = lines.slice(-50).map(redact).join('\\n');" in text
    match = re.search(r"const ERROR_LINE_RE = /(.+)/i;", text)
    assert match, "ops.yml must define ERROR_LINE_RE"
    pattern = re.compile(match.group(1), re.I)
    for line in (
        "error: triage ranking failed: success",
        "##[error]Process completed with exit code 1.",
        "::error::harness discover exited 1",
        "rate limited until 2026-09-15T20:00:00Z",
        "budget exhausted: weekly usage 100% >= 90%",
    ):
        assert pattern.search(line), line
    for noise in ("Artifact discover-1 has been successfully uploaded", "ValueError: nope"):
        assert not pattern.search(noise), noise

    limit = int(re.search(r"const ERROR_LINES_MAX = (\d+);", text).group(1))
    width = int(re.search(r"const ERROR_LINE_CHARS = (\d+);", text).group(1))
    assert 0 < limit <= 50 and 0 < width <= 1000
    assert "found.push(redact(body).slice(0, ERROR_LINE_CHARS))" in text
    assert "found.slice(-ERROR_LINES_MAX)" in text
    assert "'##[group]Run '" in text, "the script listing, which quotes every echo, is skipped"
    body = text.split("const body = [", 1)[1]
    assert body.index("errorLines,") < body.index("tail,")


# --------------------------------------------------------------------------------------
# B402 - the heartbeat reads the live ledger
# --------------------------------------------------------------------------------------


def test_b402_the_heartbeat_loads_the_ledger_from_harness_state_before_reporting():
    """B402/D71: the Monday heartbeat read main's seed and said "not measured yet" beside a 100%
    refusal on harness-state. The live copy is loaded first, replaces the seed only once it
    parses, and the comment says which one it read. Still read-only and spends nothing."""
    text = _wf("heartbeat.yml")
    steps = [s.strip() for s in re.findall(r"^      - name: (.+)$", text, re.M)]
    load = "Load state/ledger.json from harness-state"

    assert load in steps
    assert steps.index(load) < steps.index("harness status --json and harness ledger --json")
    assert steps.index(load) < steps.index("Post the heartbeat comment")
    block = text.split(f"- name: {load}", 1)[1].split("\n      - name:", 1)[0]
    assert "contents/state/ledger.json?ref=harness-state" in block
    assert "json.load" in block
    assert "mv state/ledger.json.new state/ledger.json" in block
    assert "ledger-source" in block and "ledger read from: ${ledgerSource}" in text
    assert "contents: read" in text and "contents: write" not in text
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in text


# --------------------------------------------------------------------------------------
# B406 - `harness ledger` and `harness status` stop saying STOPPED at the reset
# --------------------------------------------------------------------------------------

SESSION_RESET = "2026-09-15T14:00:00Z"


def _cli_repo_with_a_rejected_reading(tmp_path, monkeypatch, write_env, now: datetime) -> None:
    """A repository `cli.main` runs in: the clock frozen at `now`, GitHub refused, and a ledger
    holding a rejected reading from inside this harness week (so no roll can hide it)."""
    for key in KNOWN_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    write_env(tmp_path / ".env")
    write_env(tmp_path / ".env.example")
    monkeypatch.setattr(context_mod, "SystemClock", lambda: FrozenClock(now))

    def no_github(self, path):
        raise GitHubError(f"no request may be issued in this test: {path}")

    monkeypatch.setattr(gh_mod.GitHubReadOnly, "get", no_github)
    assert cli.main(["init"]) == 0
    led = Ledger.empty("2026-09-14T00:00:00Z")
    led.observe_usage(
        {
            "five_hour": {"utilization": 0.85, "resets_at": SESSION_RESET},
            "seven_day": {"utilization": 1.0, "resets_at": RESET},
            "status": "rejected",
        },
        "2026-09-15T12:00:00Z",
    )
    (tmp_path / "state").mkdir(exist_ok=True)
    (tmp_path / "state" / "ledger.json").write_text(led.to_json(), encoding="utf-8", newline="\n")


@pytest.mark.parametrize("command", ["ledger", "status"])
@pytest.mark.parametrize(
    ("now", "session_stopped", "weekly_stopped"),
    [
        ("2026-09-15T13:59:59Z", True, True),
        (SESSION_RESET, False, True),
        ("2026-09-15T19:59:59Z", False, True),
        (RESET, False, False),
    ],
    ids=["1s-before-session-reset", "at-session-reset", "1s-before-weekly-reset", "at-reset"],
)
def test_b406_the_ledger_and_status_views_agree_with_the_stop_on_both_sides_of_the_reset(
    tmp_path, monkeypatch, capsys, write_env, command, now, session_stopped, weekly_stopped
):
    """B406/D71: `harness ledger` (the D41 view) and `harness status` printed STOPPED for a
    reading whose window had already reset, while the governor and the dispatcher had stopped
    refusing. Each window's line now follows its own reset, and says the same as the stop."""
    frozen = at(now)
    _cli_repo_with_a_rejected_reading(tmp_path, monkeypatch, write_env, frozen)
    capsys.readouterr()

    assert cli.main([command]) == 0

    out = capsys.readouterr().out
    session = next(line for line in out.splitlines() if line.strip().startswith("session (5h)"))
    weekly = next(line for line in out.splitlines() if line.strip().startswith("weekly  (7d)"))
    for line, stopped in ((session, session_stopped), (weekly, weekly_stopped)):
        assert ("(STOPPED)" in line) is stopped, line
        assert ("(window reset since; no longer stops anything)" in line) is (not stopped), line

    cfg = load_config(env_path=tmp_path / ".env", environ={})
    led = Ledger.from_json((tmp_path / "state" / "ledger.json").read_text(encoding="utf-8"))
    assert (usage_stop(led, cfg, now=frozen) is not None) is (session_stopped or weekly_stopped)


# --------------------------------------------------------------------------------------
# B407 - the propose loop does not log a refusal as a proposal
# --------------------------------------------------------------------------------------


def test_b407_the_propose_loop_stops_at_a_refusal_instead_of_logging_a_proposal():
    """B407/D71: `harness propose` exits 0 on a refusal (B120) and prints `rate limited until
    <reset>`. The loop read only the exit code, so the log said `item N: proposed` for a proposal
    that never happened. The output is kept, read before the success branch, and a refusal breaks
    the loop the way a budget stop does -- never as a failure."""
    text = _wf("discover.yml")
    match = re.search(r"for n in \$ids; do\n(.*?)\n\s*done\n", text, re.S)
    assert match, "discover.yml has no propose loop"
    lines = [line.strip() for line in match.group(1).splitlines() if line.strip()]

    call = next(line for line in lines if line.startswith("if harness propose"))
    tee = re.search(r'\|\s*tee\s+"(runs/propose-\$\{n\}\.txt)"', call)
    assert tee, f"propose's output must be kept for the refusal check: {call}"
    reader = next(
        i for i, line in enumerate(lines) if "^rate limited until" in line and tee.group(1) in line
    )
    guard = next(i for i, line in enumerate(lines) if '-n "${limited}"' in line)
    proposed = lines.index('echo "item ${n}: proposed"')
    assert reader < guard < proposed
    assert '"${status}" -eq 0' in lines[guard]
    handler = lines[guard + 1 : guard + 3]
    assert "break" in handler and not any("failed=1" in line for line in handler), handler
    assert "runs/**/*.txt" in _upload_paths(text), "the kept output is uploaded as evidence"


# --------------------------------------------------------------------------------------
# B408 - the heartbeat's fallback names the real HTTP code, and a bad body as a bad body
# --------------------------------------------------------------------------------------


def test_b408_the_heartbeat_fallback_reports_the_real_code_and_a_bad_body():
    """B408/D71: `|| echo 000` inside the substitution appended a second 000 to the one curl
    prints on a connection failure ("HTTP 000000"), and an unparseable 200 put a traceback in the
    log and read as "HTTP 200". The fallback sits outside, and a bad body has its own words."""
    text = _wf("heartbeat.yml")
    block = text.split("- name: Load state/ledger.json from harness-state", 1)[1]
    block = block.split("\n      - name:", 1)[0]

    substitution = re.search(r'code="\$\((curl .*?)\)"', block, re.S)
    assert substitution, "the HTTP code is read from curl's -w output"
    assert "echo 000" not in substitution.group(1)
    assert ')" || true' in block
    assert 'code="${code:-000}"' in block
    assert "HTTP 200 but not valid JSON" in block
    parse = next(line for line in block.splitlines() if "json.load" in line)
    assert "2>/dev/null" in parse, "a parse failure is a message, not a traceback"
