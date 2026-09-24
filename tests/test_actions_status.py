"""B480-B487: status says what GitHub Actions is doing (D79).

From a thread, a harness that is thinking and a harness that is off look identical, and D65
answered that with `ack`. What it could not answer is the third state: queued behind the
`harness-ledger` lock, or cancelled by a newer arrival. These tests pin the read, the renderer,
and — the half that matters — every way the read can fail.

An empty list must never stand in for a failure: "nothing is running" and "I could not look" call
for opposite actions.
"""

from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import harness.__main__ as cli
from harness import links
from harness.clock import FrozenClock, iso
from harness.errors import GitHubError, RateCeilingReached
from harness.gh import PUBLIC_READ_TIMEOUT_S, GitHubReadOnly, public_reader
from harness.store import Store

from tests.test_gh import API, FakeOpener, FakeResponse, http_error
from tests.test_cli import forbid_network, freeze_run_clock, make_item, write_d2_repo
from tests.test_d4_routes import request_rig

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
REPO = "jgoetzmann/bright-bots-harness"
NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)


def run_row(
    name: str,
    *,
    status: str = "completed",
    conclusion: str | None = "success",
    minutes_ago: int = 5,
    event: str = "schedule",
    number: int = 1,
) -> dict:
    """One `workflow_runs` row in the shape the REST API returns it."""
    return {
        "id": 1000 + number,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "event": event,
        "run_started_at": iso(NOW - timedelta(minutes=minutes_ago)),
        "created_at": iso(NOW - timedelta(minutes=minutes_ago)),
        "html_url": f"https://github.com/{REPO}/actions/runs/{1000 + number}",
    }


def reader(tmp_path, *outcomes):
    clock = FrozenClock(NOW)
    store = Store(tmp_path / "actions.db", clock)
    store.migrate()
    opener = FakeOpener(*outcomes)
    return GitHubReadOnly(REPO, store, clock, 100, opener=opener), opener


class Listing:
    """A client whose only capability is listing workflow runs."""

    def __init__(self, rows=(), *, raises: Exception | None = None) -> None:
        self.rows = list(rows)
        self.raises = raises
        self.calls: list[tuple] = []

    def workflow_runs(self, repo, *, per_page=30):
        self.calls.append((repo, per_page))
        if self.raises is not None:
            raise self.raises
        return list(self.rows)


# --------------------------------------------------------------------------------------
# B480 - the read
# --------------------------------------------------------------------------------------


def test_B480_workflow_runs_is_one_get_that_reads_the_list_out_of_the_object(tmp_path):
    """B480: a read, on the read-only client, so `public_reader` inherits it and `ack` can use
    it without a credential. One page: the endpoint lists newest first, and walking a history
    that grows with every comment is what `watchdog.yml` already refuses to do."""
    rows = [run_row("implement", number=1), run_row("feedback", number=2)]
    link = f'<{API}/repositories/1/actions/runs?page=2>; rel="next"'
    gh, opener = reader(tmp_path, FakeResponse({"workflow_runs": rows}, headers={"Link": link}))

    got = gh.workflow_runs(REPO)

    assert got == rows
    assert len(opener.requests) == 1, "a `next` link must not be followed"
    assert opener.requests[0].get_method() == "GET"
    assert opener.requests[0].data is None
    assert opener.urls[0] == f"{API}/repos/{REPO}/actions/runs?per_page=100"
    assert opener.requests[0].get_header("Authorization") is None


def test_B480_the_read_is_not_on_the_write_surface():
    """B480: I-13 governs writes, and this is not one. It is defined on `GitHubReadOnly`, so the
    unauthenticated reader has it, and it is in no write set."""
    from tests.test_invariants import GH_WRITE_METHODS

    assert "workflow_runs" not in GH_WRITE_METHODS
    assert "workflow_runs" in vars(GitHubReadOnly), "it must live on the read-only client"
    assert hasattr(public_reader(), "workflow_runs")


@pytest.mark.parametrize(
    "payload", [[{"id": 1}], {"total_count": 0}, {"workflow_runs": "nope"}]
)
def test_B480_a_shape_without_the_runs_key_is_an_error_and_not_an_empty_queue(tmp_path, payload):
    """B480: the shape that would otherwise read as "nothing is running", which is the one
    answer this must never invent."""
    gh, _ = reader(tmp_path, FakeResponse(payload))

    with pytest.raises(GitHubError):
        gh.workflow_runs(REPO)


# --------------------------------------------------------------------------------------
# B481-B483 - the renderer
# --------------------------------------------------------------------------------------


def test_B481_the_section_names_what_is_running_queued_and_recently_cancelled():
    """B481: the three facts the operator could not get from `/harness status` before."""
    rows = [
        run_row("implement", status="in_progress", conclusion=None, minutes_ago=12, event="push"),
        run_row("feedback", status="queued", conclusion=None, minutes_ago=3, number=2),
        run_row("implement", conclusion="cancelled", minutes_ago=30, number=3),
        run_row("implement", conclusion="cancelled", minutes_ago=40, number=4),
    ]

    lines = links.actions_lines(rows, NOW)

    assert lines[0] == "**Actions** — 1 running, 1 queued"
    assert "`implement` **running** 12m — push" in lines[1]
    assert "/actions/runs/1001" in lines[1], "and a link to the run itself"
    assert "`feedback` queued 3m" in lines[2]
    assert any("cancelled in the last 6h: `implement` ×2" in line for line in lines)


def test_B481_a_cancelled_run_older_than_the_window_is_not_reported():
    """B481: the cancelled line is a view of a burst that just happened, not a history."""
    rows = [run_row("implement", conclusion="cancelled", minutes_ago=60 * 9)]

    lines = links.actions_lines(rows, NOW)

    assert lines == ["**Actions** — nothing running or queued."]


def test_B482_a_queued_ledger_group_run_is_named_as_queued_behind_the_lock():
    """B482: "queued" alone reads as GitHub being slow. Queued *behind the ledger lock* is the
    true answer and the one that tells the operator to wait rather than to investigate."""
    rows = [
        run_row("feedback", status="queued", conclusion=None, minutes_ago=3),
        run_row("watchdog", status="queued", conclusion=None, minutes_ago=1, number=2),
    ]

    lines = links.actions_lines(rows, NOW)

    behind = [line for line in lines if "behind the ledger lock" in line]
    assert len(behind) == 1 and "`feedback`" in behind[0]
    assert any("`watchdog` queued" in line for line in lines)
    assert not any("`watchdog`" in line and "lock" in line for line in lines)


def test_B482_the_group_list_is_the_one_the_workflows_declare():
    """B482: a renderer cannot read the workflow files, so the list is hard-coded exactly as
    `feedback.yml`'s machine handle is — and pinned here to what those files actually declare,
    which is the trick B437 uses. A workflow joining or leaving the group fails the build."""
    declaring = sorted(
        path.stem
        for path in sorted(WORKFLOWS.glob("*.yml"))
        if re.search(r"group:\s*['\"]?harness-ledger['\"]?", path.read_text(encoding="utf-8"))
    )

    assert sorted(links.LEDGER_GROUP_WORKFLOWS) == declaring


def test_B483_nothing_running_is_one_line_and_a_skipped_run_is_never_listed():
    """B483: `feedback.yml` skips at job level on every unrelated comment, so a skipped run is
    the most common row there is. Listing them is the noise `watchdog.yml` already filters, and
    an empty bullet under a headline reads as a row that failed to render."""
    assert links.actions_lines([], NOW) == ["**Actions** — nothing running or queued."]

    skipped = [
        run_row("feedback", conclusion="skipped", number=1),
        run_row("feedback", conclusion="success", number=2),
        run_row("implement", conclusion="failure", number=3),
    ]

    lines = links.actions_lines(skipped, NOW)

    assert lines == ["**Actions** — nothing running or queued."]
    assert not any(line.startswith("- ") for line in lines)


def test_B483_the_row_list_is_capped_like_the_queue():
    rows = [
        run_row("implement", status="queued", conclusion=None, minutes_ago=n, number=n)
        for n in range(1, 9)
    ]

    lines = links.actions_lines(rows, NOW, limit=5)

    assert lines[0] == "**Actions** — 0 running, 8 queued"
    assert lines[-1] == "- …and 3 more"
    assert len([line for line in lines if line.startswith("- `")]) == 5


# --------------------------------------------------------------------------------------
# B484 - every way it can fail
# --------------------------------------------------------------------------------------


def test_B484_every_way_the_read_can_fail_costs_one_line_and_nothing_else():
    """B484: the load-bearing half. The fakes replace GitHub, so the happy path proves little;
    what matters is that no failure is ever rendered as an idle queue, and that none of them
    escapes to break the answer they are one line of."""
    cases = {
        "a client with no such method": (object(), "cannot list workflow runs"),
        "a refused read": (Listing(raises=GitHubError("403 for /actions/runs")), "403"),
        "the request ceiling": (
            Listing(raises=RateCeilingReached("ceiling")), "request ceiling reached"
        ),
        "an unexpected shape": (SimpleNamespace(workflow_runs=lambda repo, per_page=30: "nope"),
                                "unexpected shape"),
        "something nobody predicted": (
            SimpleNamespace(workflow_runs=lambda repo, per_page=30: 1 / 0), "could not be read"
        ),
    }
    for label, (client, expected) in cases.items():
        rows, error, truncated = cli._actions_rows(client, REPO)

        assert rows == [] and truncated is False, label
        assert error and expected in error, f"{label}: {error!r}"
        assert links.actions_lines(rows, NOW, error=error) == [f"**Actions** — {error}"], label
        assert "nothing running" not in error, f"{label} must not read as idle"


def test_B484_a_readable_client_reports_no_error_and_an_unknown_repo_is_unreadable():
    """The other half: the guard must not swallow a live answer."""
    listing = Listing([run_row("implement", status="in_progress", conclusion=None)])

    rows, error, truncated = cli._actions_rows(listing, REPO)

    assert error == "" and len(rows) == 1 and truncated is False
    assert listing.calls == [(REPO, cli.ACTIONS_PER_PAGE)]
    assert cli._actions_rows(listing, "")[1], "with no repository there is nothing to read"


def test_B484_a_row_that_is_not_an_object_is_dropped_rather_than_rendered():
    rows, error, _ = cli._actions_rows(Listing([run_row("implement"), "junk", None]), REPO)

    assert error == "" and len(rows) == 1


# --------------------------------------------------------------------------------------
# B485 - one renderer
# --------------------------------------------------------------------------------------


def test_B485_the_reply_the_cli_and_the_pinned_queue_use_one_renderer(
    tmp_path, monkeypatch, capsys
):
    """B485: the `queue_lines` pattern again. Three answers to "what is Actions doing" that can
    disagree are worse than one."""
    rows = [
        run_row("implement", status="in_progress", conclusion=None, minutes_ago=12, event="push"),
        run_row("feedback", status="queued", conclusion=None, minutes_ago=3, number=2),
    ]
    rig = request_rig(tmp_path)
    rig.gh.workflow_runs = lambda repo, per_page=30: list(rows)
    now = rig.ctx.clock.now()
    expected = links.actions_lines(rows, now)
    assert expected[0].startswith("**Actions** — 1 running")

    reply = cli._usage_report(rig.ctx, rig.config, now)
    pinned = "\n".join(cli._queue_block_lines(rig.ctx, rig.config, now))

    for surface, text in (("reply", reply), ("pinned issue", pinned)):
        assert "\n".join(expected) in text, f"the {surface} does not carry the rendered section"

    # And the CLI, which reads through the real client. The clock is frozen to the one the rig
    # renders against, or the two would compute different ages for the same run.
    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path)
    freeze_run_clock(monkeypatch, NOW)
    assert cli.main(["init"]) == 0
    make_item(tmp_path, state="proposed")
    monkeypatch.setattr(
        GitHubReadOnly, "workflow_runs", lambda self, repo, per_page=30: list(rows)
    )
    capsys.readouterr()

    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out

    assert "\n".join(expected) in out
    assert cli.main(["status", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["actions"]["error"] is None
    assert [row["name"] for row in payload["actions"]["runs"]] == ["implement", "feedback"]


def test_B485_a_failed_read_still_leaves_the_rest_of_the_answer_whole(tmp_path):
    """B485: the section is one line of a reply that has a job to do. A status answer that lost
    its allowance and its queue because Actions could not be listed would be a worse failure
    than the one it was reporting."""
    rig = request_rig(tmp_path)
    rig.gh.workflow_runs = lambda repo, per_page=30: (_ for _ in ()).throw(GitHubError("410"))
    now = rig.ctx.clock.now()

    reply = cli._usage_report(rig.ctx, rig.config, now)

    assert "**Actions** — could not be read" in reply
    assert "**Allowance**" in reply and "**Queue**" in reply and "**Next**" in reply


# --------------------------------------------------------------------------------------
# B486 - the fast lane
# --------------------------------------------------------------------------------------


def test_B486_the_fast_lane_shows_actions_through_the_unauthenticated_reader(
    tmp_path, capsys, monkeypatch
):
    """B486: `ACK_ANSWERS == {"status"}` means the operator's own `/harness status` comment is
    answered by `ack` and skipped by the sweep — so without this the one surface they asked
    about would never show it.

    It reads through `gh.public_reader()`: unauthenticated, no token, no store, no lock, tier 0
    preserved. B440's proof obligation still holds, and is asserted here again: `harness ack`
    writes no ledger, so `ack.yml` stays outside the `harness-ledger` group.
    """
    from harness.ledger import Ledger

    from tests.test_ack_and_watchdog import _env, _write_ledger

    rows = [run_row("implement", status="queued", conclusion=None, minutes_ago=7, event="push")]
    asked: list = []

    class Public(Listing):
        def workflow_runs(self, repo, *, per_page=30):
            asked.append(repo)
            return list(rows)

    env = _env(tmp_path)
    _write_ledger(tmp_path, Ledger.empty("2026-08-31T00:00:00Z"))
    ledger_file = tmp_path / "state" / "ledger.json"
    before = ledger_file.read_text(encoding="utf-8")
    body = tmp_path / "c.txt"
    body.write_text("/harness status", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "PUBLIC_READER", lambda *a, **k: Public())
    capsys.readouterr()

    assert cli.main([
        "--config", str(env), "ack", "--body-file", str(body),
        "--actor", "jgoetzmann", "--association", "OWNER",
    ]) == 0

    out = json.loads(capsys.readouterr().out)
    assert out["answered"] is True, "the fast lane answered it"
    assert "**Actions** — 0 running, 1 queued" in out["comment"]
    assert "behind the ledger lock" in out["comment"]
    assert "**Allowance**" in out["comment"], "and the rest of the answer is still there"
    assert asked, "it really did read through the unauthenticated client"
    assert ledger_file.read_text(encoding="utf-8") == before, "ack writes no ledger (B440)"


def test_B486_a_failed_actions_read_does_not_cost_the_fast_answer(tmp_path, capsys, monkeypatch):
    """B486: in `ack` the read is one unauthenticated request against a shared 60/hour ceiling,
    so it will sometimes be refused. That must cost the section and never the answer."""
    from harness.ledger import Ledger

    from tests.test_ack_and_watchdog import _env, _write_ledger

    env = _env(tmp_path)
    _write_ledger(tmp_path, Ledger.empty("2026-08-31T00:00:00Z"))
    body = tmp_path / "c.txt"
    body.write_text("/harness status", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "PUBLIC_READER", lambda *a, **k: (_ for _ in ()).throw(OSError("no")))
    capsys.readouterr()

    assert cli.main([
        "--config", str(env), "ack", "--body-file", str(body),
        "--actor", "jgoetzmann", "--association", "OWNER",
    ]) == 0

    out = json.loads(capsys.readouterr().out)
    assert out["answered"] is True
    assert "**Allowance**" in out["comment"]


# --------------------------------------------------------------------------------------
# B487 - doctor
# --------------------------------------------------------------------------------------


def test_B487_doctor_warns_and_never_degrades_when_the_runs_list_cannot_be_read(
    tmp_path, monkeypatch, capsys
):
    """B487: `doctor` gates discover, implement and feedback under `set -e`, so anything filed as
    a problem stops the fleet. Losing a status line does not stop the harness — it stops one line
    of one reply — and filing it as a problem would be issue #27 again (D74/B305's rule)."""
    payload: dict = {}
    config = SimpleNamespace(permission_tier=2, self_repo=REPO)
    monkeypatch.setattr(
        cli, "_context", lambda config, args, *, run_id: SimpleNamespace(gh=object())
    )

    warning = cli._doctor_actions(config, SimpleNamespace(), payload)

    assert warning and "not readable" in warning
    assert payload["actions"]["readable"] is False

    # And a readable one is reported rather than warned about.
    good: dict = {}
    monkeypatch.setattr(
        cli,
        "_context",
        lambda config, args, *, run_id: SimpleNamespace(gh=Listing([run_row("implement")])),
    )
    assert cli._doctor_actions(config, SimpleNamespace(), good) == ""
    assert good["actions"] == {"readable": True, "runs": 1}

    # Tier 0 holds no token and reads this on demand; "I could not check" is not a finding.
    tier0: dict = {}
    assert cli._doctor_actions(SimpleNamespace(permission_tier=0), SimpleNamespace(), tier0) == ""
    assert tier0["actions"] == {"readable": None}


def test_B487_an_actions_warning_never_changes_doctors_exit_code(tmp_path, monkeypatch, capsys):
    """B487: asserted the way B305 asserts it — against doctor's own exit code with and without
    the finding, rather than against an absolute, so a runner missing `claude` cannot mask it."""
    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path)
    assert cli.main(["init"]) == 0
    monkeypatch.setattr(cli, "_doctor_trust_access", lambda config, args, trusted, payload: ())
    forbid_network(monkeypatch)
    capsys.readouterr()

    monkeypatch.setattr(cli, "_doctor_actions", lambda config, args, payload: "")
    baseline = cli.main(["doctor"])
    capsys.readouterr()

    monkeypatch.setattr(
        cli, "_doctor_actions", lambda config, args, payload: "the Actions run list is not readable"
    )
    code = cli.main(["doctor"])
    out = capsys.readouterr().out

    assert code == baseline, "an Actions warning must not change doctor's exit code"
    assert "warnings (the harness still runs)" in out
    assert "Actions run list is not readable" in out


# --------------------------------------------------------------------------------------
# B490-B492 - the page, the fast lane's silence, and a row that cannot be forged
# --------------------------------------------------------------------------------------


def test_B490_a_run_below_the_first_thirty_is_still_reported():
    """B490: `ack.yml` fires on every comment, `feedback.yml` makes a job-skipped run on every
    comment and `selftest.yml` one per pull-request push, so thirty rows was minutes on a busy
    morning — exactly the burst an operator would be investigating. The live run fell off the
    page, and the section then said "nothing running or queued" in the same words it uses when
    the queue really is empty."""
    rows = [run_row("feedback", conclusion="skipped", number=n) for n in range(1, 31)]
    rows.append(run_row("implement", status="in_progress", conclusion=None, number=31))
    listing = Listing(rows)

    read, error, truncated = cli._actions_rows(listing, REPO)

    assert cli.ACTIONS_PER_PAGE == 100, "still one page, but the largest the endpoint serves"
    assert listing.calls == [(REPO, 100)]
    assert error == "" and truncated is False
    lines = links.actions_lines(read, NOW, truncated=truncated)
    assert lines[0] == "**Actions** — 1 running, 0 queued"
    assert any("`implement` **running**" in line for line in lines)


def test_B490_a_full_page_is_never_rendered_as_idle():
    """B490: a page is a bound on what was read and never a statement about what exists, so a
    full one is reported as a bound. "Nothing running or queued" would be a claim about runs
    nobody looked at — the failure D79 exists to prevent, one page further out."""
    full = [
        run_row("feedback", conclusion="success", number=n)
        for n in range(1, cli.ACTIONS_PER_PAGE + 1)
    ]

    read, error, truncated = cli._actions_rows(Listing(full), REPO)

    assert error == "" and truncated is True
    idle = links.actions_lines(read, NOW, truncated=truncated)
    assert idle == [f"**Actions** — nothing running or queued in the newest {len(read)} runs."]
    assert idle[0] != "**Actions** — nothing running or queued.", "the bound is the whole point"

    busy = links.actions_lines(
        read[1:] + [run_row("implement", status="in_progress", conclusion=None, number=999)],
        NOW,
        truncated=True,
    )
    assert busy[0] == "**Actions** — 1 running, 0 queued in the newest 100 runs"

    # One row short of a page is not truncated, and carries no bound at all.
    short, _, cut = cli._actions_rows(Listing(full[:-1]), REPO)
    assert cut is False
    assert links.actions_lines(short, NOW, truncated=cut) == [
        "**Actions** — nothing running or queued."
    ]


def test_B491_a_failed_actions_read_omits_the_section_in_the_fast_answer(
    tmp_path, capsys, monkeypatch
):
    """B491: D79 says a failure there omits the section, and the code rendered an error line
    instead. That read is unauthenticated against 60/hour shared by every job on the runner's
    address, so a 403 is routine rather than exceptional, and an error line about a read nobody
    asked for would become the normal shape of the fast answer."""
    from harness.ledger import Ledger

    from tests.test_ack_and_watchdog import _env, _write_ledger

    env = _env(tmp_path)
    _write_ledger(tmp_path, Ledger.empty("2026-08-31T00:00:00Z"))
    body = tmp_path / "c.txt"
    body.write_text("/harness status", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    argv = [
        "--config", str(env), "ack", "--body-file", str(body),
        "--actor", "jgoetzmann", "--association", "OWNER",
    ]
    monkeypatch.setattr(
        cli, "PUBLIC_READER", lambda *a, **k: Listing(raises=GitHubError("403 rate limit"))
    )
    capsys.readouterr()

    assert cli.main(argv) == 0

    refused = json.loads(capsys.readouterr().out)
    assert refused["answered"] is True
    assert "**Actions**" not in refused["comment"], "a failure omits the section"
    assert "could not be read" not in refused["comment"]
    assert "**Allowance**" in refused["comment"], "and the rest of the answer is whole"

    # The control: a read that works still shows it, so the silence is about the failure and not
    # about `ack` having stopped rendering the section.
    monkeypatch.setattr(
        cli,
        "PUBLIC_READER",
        lambda *a, **k: Listing([run_row("implement", status="in_progress", conclusion=None)]),
    )
    assert cli.main(argv) == 0

    assert "**Actions** — 1 running" in json.loads(capsys.readouterr().out)["comment"]


def test_B491_the_public_read_is_bounded_by_a_timeout(monkeypatch):
    """B491: the first `gh.py` read on the ack fast path with nothing bounding it but the job
    timeout. `urlopen` without one waits on the socket default, which is no timeout at all, so a
    connection that never answers would hold `ack.yml` until GitHub cancels the job."""
    seen: dict = {}

    def fake_urlopen(request, *args, **kwargs):
        seen.update(kwargs)
        seen["url"] = request.full_url
        return FakeResponse({"workflow_runs": []})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    assert public_reader().workflow_runs(REPO) == []

    assert seen["timeout"] == PUBLIC_READ_TIMEOUT_S
    assert 0 < PUBLIC_READ_TIMEOUT_S <= 30, "a bound nobody would ever reach is not a bound"
    assert seen["url"].endswith("/actions/runs?per_page=100")


def test_B492_a_newline_in_a_workflow_name_cannot_forge_rows():
    """B492: only a repository writer can name a workflow, and `queue_lines` embeds issue titles
    the same way — but a name carrying a newline renders as bullets of its own beneath the row,
    and `_defused` is already next door in this module. One run is one line."""
    row = run_row(
        "evil\n- `fake` **running** 5m — push",
        status="in_progress",
        conclusion=None,
        event="push\n- `also-fake` queued 1m",
    )

    lines = links.actions_lines([row], NOW)

    assert lines[0] == "**Actions** — 1 running, 0 queued"
    assert len(lines) == 2, f"a name with a newline in it forged rows: {lines}"
    assert all("\n" not in line and "\r" not in line for line in lines)


# --------------------------------------------------------------------------------------
# B511 (D88) - the status reply and the pinned queue both show the open-delivery cap
# --------------------------------------------------------------------------------------


def test_B511_the_reply_and_the_pinned_queue_carry_the_cap_line(tmp_path, monkeypatch):
    """B511: a hold that only the dispatch log mentions looks, from the pinned issue, like a
    queue that is stuck for no reason."""
    import harness.stages.deliver as deliver_mod

    line = "- open delivery pull requests upstream: 6 of 2; no new item starts implementing"
    monkeypatch.setattr(deliver_mod, "delivery_cap_line", lambda ctx: line)
    rig = request_rig(tmp_path)
    rig.gh.workflow_runs = lambda repo, per_page=30: []
    now = rig.ctx.clock.now()

    assert line in cli._usage_report(rig.ctx, rig.config, now).splitlines()
    assert line in cli._queue_block_lines(rig.ctx, rig.config, now)
