"""B459-B471: `/harness block` — the operator lending the harness sessions (D77).

A block suspends the **run window** for the next n five-hour subscription sessions and nothing
else. The tests that matter here are the ones about what it does *not* lift: a block that could
outlive a usage stop or a halt would be a way to spend an allowance the operator had already said
to stop spending.

Nothing here reaches the network or a model: the dispatcher is pure, the ledger is a file, and the
CLI runs against the fake backend with every stage recorded.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

import pytest

import harness.__main__ as cli
from harness import keywords, links
from harness.clock import iso
from harness.dispatcher import Candidate, plan
from harness.keywords import ALIASES, VERB_LEVEL, VERBS, Command, commands_from
from harness.ledger import MAX_BLOCK_SESSIONS, SESSION_HOURS, Ledger, block_until
from harness.trust import MAX_LEVEL, parse_trust

from tests.test_cli import (
    align_ledger_window,
    forbid_network,
    freeze_run_clock,
    lease_on,
    make_item,
    record_stages,
    stage_names,
    write_carry_ledger,
    write_d2_repo,
    write_spec,
)
from tests.test_dispatcher import d3_config, empty_ledger, make_config, usage_ledger
from tests.test_d4_routes import request_rig

# The dispatcher suite's clock: Wednesday 2026-09-02 12:00 UTC, outside `mon 08:00 - tue 20:00`.
NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
NOW_ISO = iso(NOW)
PERIOD_START = "2026-08-31T00:00:00Z"
FIVE_HOUR_RESET = "2026-09-02T16:00:00Z"
DAILY_WINDOW = {"RUN_WINDOW_START": "daily 11:00", "RUN_WINDOW_END": "daily 15:00"}


def blocked_ledger(sessions: int = 3, *, now: datetime = NOW, reading: bool = False) -> Ledger:
    """A ledger carrying a live block, optionally with a session reading behind it."""
    led = usage_ledger(weekly=0.05, session=0.05) if reading else empty_ledger()
    led.request_block("jgoetzmann", sessions, now, "away this afternoon")
    return led


def comment(*, login: str, association: str, body: str, ident: int = 1) -> dict:
    return {
        "id": ident,
        "node_id": f"IC_{ident}",
        "user": {"login": login},
        "author_association": association,
        "body": body,
    }


def cmd(args: str = "", *, actor: str = "jgoetzmann", level: int = 3) -> Command:
    return Command(
        verb="block", args=args, surface="inbox", number=19, comment_id="IC_1",
        actor=actor, level=level,
    )


def run_plan(config, led, *candidates, now=NOW, halted=False):
    return plan(
        now=now, ledger=led, config=config,
        candidates=tuple(
            Candidate(issue=n, created_at=f"2026-09-01T10:0{k}:00Z")
            for k, n in enumerate(candidates)
        ),
        merged=frozenset(), halted=halted,
    )


# --------------------------------------------------------------------------------------
# B459 - the level
# --------------------------------------------------------------------------------------


def test_B459_block_is_level_three_and_a_maintainer_is_refused_naming_the_level():
    """B459: a block is `--force` for the whole fleet over several sessions, and `--force`
    already needs MAX_LEVEL to lift the window for one item. A verb that lifts it for everything
    cannot sit below the flag that lifts it for one — it is the operator's subscription being
    spent, which is what docs/FOR-MAINTAINERS.md §7 says about `--force`."""
    assert "block" in VERBS
    assert VERB_LEVEL["block"] == MAX_LEVEL == 3

    led = Ledger.empty(PERIOD_START)
    refused = commands_from(
        comment(login="nathan", association="MEMBER", body="/harness block 3"),
        surface="inbox", number=19, trusted=parse_trust("2 nathan"), ledger=led,
    )

    assert [c.verb for c in refused] == ["__denied__"]
    assert "needs level 3" in refused[0].args, "the refusal names the level it would have needed"


def test_B459_the_operator_gets_the_verb_and_both_plurals_resolve_to_it():
    """B459: the plural is what a person types with a count, and the unit is what they call it.
    There is deliberately no `unblock`: an alias carries the verb and never the argument, so it
    would resolve to a bare `block` and *report* rather than cancel."""
    led = Ledger.empty(PERIOD_START)
    got = commands_from(
        comment(login="jgoetzmann", association="OWNER", body="/harness block 2 away"),
        surface="inbox", number=19, trusted=parse_trust("3 jgoetzmann"), ledger=led,
    )

    assert [(c.verb, c.args) for c in got] == [("block", "2 away")]
    assert ALIASES["blocks"] == "block" and ALIASES["sessions"] == "block"
    assert "unblock" not in ALIASES and "unblock" not in VERBS
    assert keywords.parse("/harness blocks 3") == ("block", "3")


# --------------------------------------------------------------------------------------
# B460 - the grant on disk
# --------------------------------------------------------------------------------------


def test_B460_the_grant_round_trips_and_a_ledger_without_one_is_byte_identical():
    """B460: the block is written only once granted, exactly as the halt and the carry are, so
    every ledger that has never seen one is byte-identical to the file it was."""
    plain = Ledger.empty(PERIOD_START)
    before = plain.to_json()
    assert "block" not in before
    assert Ledger.from_json(before).to_json() == before

    led = Ledger.empty(PERIOD_START)
    led.request_block("jgoetzmann", 2, NOW, "away this afternoon")
    text = led.to_json()
    again = Ledger.from_json(text)

    assert again.to_json() == text, "saving a loaded ledger is a fixed point"
    grant = again.block_grant()
    assert grant["by"] == "jgoetzmann" and grant["sessions"] == 2
    assert grant["reason"] == "away this afternoon"
    assert list(json.loads(text)["window"]["block"]) == [
        "by", "at", "sessions", "until", "anchor", "reason"
    ], "to_json is the schema, so the keys are rendered explicitly and in order"

    led.clear_block()
    assert led.block_grant() is None
    assert led.to_json() == before, "cancelling leaves the file as it was"


# --------------------------------------------------------------------------------------
# B461-B463 - how "the next n sessions" is measured
# --------------------------------------------------------------------------------------


def test_B461_a_live_session_reading_measures_n_sessions_from_its_reset():
    """B461: the first session is the remainder of the one already in progress, so a block of n
    ends at that reset plus n-1 whole sessions. Anything else would either throw away the part
    of the current session the operator is lending, or grant a session more than they said."""
    led = Ledger.empty(PERIOD_START)
    led.observe_usage(
        {
            "five_hour": {"utilization": 0.1, "resets_at": FIVE_HOUR_RESET},
            "seven_day": {"utilization": 0.2, "resets_at": "2026-09-08T20:00:00Z"},
        },
        NOW_ISO,
    )

    grant = led.request_block("jgoetzmann", 3, NOW, "")

    # 16:00Z, plus two whole five-hour sessions.
    assert grant["until"] == "2026-09-03T02:00:00Z"
    assert grant["anchor"] == FIVE_HOUR_RESET
    assert block_until(1, NOW, FIVE_HOUR_RESET) == FIVE_HOUR_RESET, "one session is the remainder"


@pytest.mark.parametrize(
    "reading",
    [None, "", "not a timestamp", "2026-09-02T09:00:00Z"],  # absent, empty, junk, already past
)
def test_B462_no_reading_a_stale_one_or_an_unreadable_one_measures_from_now_never_forever(
    reading,
):
    """B462: with no session clock to measure from, the honest reading of "the next n sessions"
    is n sessions starting now. The load-bearing half is that there is no branch which returns
    nothing: a missing signal can never grant an unbounded block."""
    until = block_until(3, NOW, reading)

    assert until == iso(NOW + timedelta(hours=3 * SESSION_HOURS))
    assert until, "always finite"

    led = Ledger.empty(PERIOD_START)
    grant = led.request_block("jgoetzmann", 3, NOW, "")
    assert grant["until"] == until, "the ledger with no observation takes the same branch"
    assert grant["anchor"] == ""


def test_B463_the_grant_is_measured_once_and_a_later_observation_does_not_move_it():
    """B463: re-measuring against each new reading would walk the block forward for ever, since
    a fresh session is observed on every call. `anchor` is kept for the reply and never read."""
    led = Ledger.empty(PERIOD_START)
    granted = led.request_block("jgoetzmann", 2, NOW, "")
    until = granted["until"]

    for later in ("2026-09-03T02:00:00Z", "2026-09-04T11:00:00Z"):
        led.observe_usage({"five_hour": {"utilization": 0.4, "resets_at": later}}, NOW_ISO)
        assert led.block_grant()["until"] == until, f"a reading of {later} moved the block"


# --------------------------------------------------------------------------------------
# B464 - the count
# --------------------------------------------------------------------------------------


def test_B464_zero_cancels_a_count_over_the_cap_is_refused_and_a_word_is_not_a_count(tmp_path):
    """B464: `0` is the cancel, because an alias cannot carry one. A count over the cap is
    refused whole rather than clamped — a clamp grants something other than what was asked for,
    which is the rule `trust.parse_trust` applies to a level out of range."""
    rig = request_rig(tmp_path)
    led = rig.ctx.ledger

    granted = cli._block_command(rig.ctx, cmd("2"))
    assert "Blocked out 2 sessions" in granted
    assert led.block_grant() is not None

    cancelled = cli._block_command(rig.ctx, cmd("0"))
    assert "cancelled" in cancelled.lower()
    assert led.block_grant() is None, "zero really cancels"

    over = cli._block_command(rig.ctx, cmd(str(MAX_BLOCK_SESSIONS + 1)))
    assert str(MAX_BLOCK_SESSIONS) in over and "nothing was changed" in over
    assert led.block_grant() is None, "a refused count grants nothing at all"

    word = cli._block_command(rig.ctx, cmd("forever"))
    assert "not a number of sessions" in word
    assert led.block_grant() is None

    assert "nothing to cancel" in cli._block_command(rig.ctx, cmd("0"))
    assert "No block stands" in cli._block_command(rig.ctx, cmd(""))


def test_B464_a_granted_block_names_what_it_does_not_lift(tmp_path):
    """B464: the reply is where a reader learns that a block is not a way round a usage stop."""
    rig = request_rig(tmp_path)

    reply = cli._block_command(rig.ctx, cmd("3 away this afternoon"))

    for named in ("usage stops", ".harness/HALT", "/harness halt", "human gates"):
        assert named in reply, f"the reply must name {named}"
    assert "/harness block 0" in reply and "expires by itself" in reply
    assert "$" not in reply, "user-facing output reports usage, never money (D66/B419)"


# --------------------------------------------------------------------------------------
# B465 - expiry, and failing closed
# --------------------------------------------------------------------------------------


def test_B465_the_block_expires_by_itself_and_an_unreadable_until_is_closed():
    """B465: it ends by the clock alone — nothing has to run for it to end. And a grant whose
    `until` cannot be read is shut, because a block lifts a restriction and anything unreadable
    about it has to mean "not lifted"."""
    led = blocked_ledger(1)
    until = led.block_grant()["until"]

    assert led.block_open(NOW) is True
    assert led.block_open(iso(NOW + timedelta(hours=SESSION_HOURS, seconds=-1))) is True
    assert led.block_open(until) is False, "at `until` it is over"
    assert led.block_open(iso(NOW + timedelta(days=7))) is False

    for broken in ({"by": "x", "sessions": 1}, {"until": ""}, {"until": "soon"}):
        led.window["block"] = dict(broken)
        assert led.block_open(NOW) is False, f"{broken} must fail closed"


# --------------------------------------------------------------------------------------
# B466-B468 - what the plan does with it
# --------------------------------------------------------------------------------------


def test_B466_the_plan_starts_work_outside_the_window_and_adds_no_reason_string(tmp_path):
    """B466: the whole point. Outside the window the plan is empty and names the window; with a
    block standing the same inputs start the work, and the reason is the ordinary slot count.

    No new reason literal: one would have to be classified in MUST_STOP/MAY_PROCEED and would
    change `discover.yml`'s `case "$reason"` contract, so the plan says it by starting the work.
    """
    from tests.test_invariants import (
        MAY_PROCEED_REASON_PREFIXES,
        MUST_STOP_REASON_PREFIXES,
        _dispatcher_reasons,
    )

    config = d3_config(tmp_path)

    shut = run_plan(config, usage_ledger(weekly=0.05, session=0.05), 816)
    assert shut.start == () and shut.reason == "outside run window (mon 08:00-tue 20:00 UTC)"

    open_by_block = run_plan(config, blocked_ledger(3, reading=True), 816)

    assert open_by_block.start == (816,)
    assert open_by_block.reason == "1 of max 1 slots; weekly 5%, session 5%"
    assert "block" not in open_by_block.reason
    assert set(_dispatcher_reasons()) == MUST_STOP_REASON_PREFIXES | MAY_PROCEED_REASON_PREFIXES


def test_B466_an_expired_block_holds_work_back_again(tmp_path):
    """B466: the other half — once it lapses the window is the window again, with no command."""
    config = d3_config(tmp_path)
    led = blocked_ledger(1)

    later = NOW + timedelta(hours=SESSION_HOURS + 1)

    assert run_plan(config, led, 816, now=NOW).start == (816,)
    assert run_plan(config, led, 816, now=later).start == ()
    assert run_plan(config, led, 816, now=later).reason.startswith("outside run window")


def test_B467_a_carried_item_resumes_outside_a_daily_window_while_a_block_stands(tmp_path):
    """B467: under a daily window B413 holds the carried item back, because a carry there is what
    the session stop left behind. A block is the operator saying this session is the harness's,
    so the carry resumes — which is the intent. Where no block stands B413 is unchanged."""
    config = d3_config(tmp_path, **DAILY_WINDOW)
    wednesday_evening = datetime(2026, 9, 2, 18, 41, tzinfo=timezone.utc)

    led = usage_ledger(weekly=0.05, session=0.05, carry=816)
    held = run_plan(config, led, 810, 816, now=wednesday_evening)
    assert held.start == (), "B413 unchanged with no block"
    assert held.reason == "outside run window (daily 11:00-15:00 UTC)"

    led.request_block("jgoetzmann", 2, wednesday_evening, "")
    resumed = run_plan(config, led, 810, 816, now=wednesday_evening)

    assert resumed.start[0] == 816, "the carried item is still first"


def test_B468_a_block_lifts_the_window_and_neither_usage_stop_nor_either_halt(tmp_path):
    """B468: the test this whole decision stands on. A block that could outlive a usage stop or a
    halt would be a way to spend an allowance somebody had already said to stop spending.

    The order in `plan` is what makes it true: rate limit, halted, commanded halt, carry, usage
    stop, *then* the window branch a block opens.
    """
    config = d3_config(tmp_path)

    session_stopped = blocked_ledger(3)
    session_stopped.observe_usage(
        {
            "five_hour": {"utilization": 0.82, "resets_at": "2026-09-02T16:00:00Z"},
            "seven_day": {"utilization": 0.10, "resets_at": "2026-09-08T20:00:00Z"},
        },
        NOW_ISO,
    )
    stopped = run_plan(config, session_stopped, 816)
    assert stopped.start == (), "the session usage stop still stops it"
    assert stopped.reason == "session usage 82% >= 70%"

    weekly_stopped = blocked_ledger(3)
    weekly_stopped.observe_usage(
        {"seven_day": {"utilization": 0.95, "resets_at": "2026-09-08T20:00:00Z"}}, NOW_ISO
    )
    assert run_plan(config, weekly_stopped, 816).reason == "weekly usage 95% >= 90%"

    assert run_plan(config, blocked_ledger(3), 816, halted=True).reason == "halted"

    commanded = blocked_ledger(3)
    commanded.request_halt("jgoetzmann", "the usage looks wrong", NOW_ISO)
    assert run_plan(config, commanded, 816).reason == "halted by @jgoetzmann: the usage looks wrong"

    # And with nothing else in the way it does open the window, so the three above are not
    # passing because the block never worked.
    assert run_plan(config, blocked_ledger(3), 816).start == (816,)


def test_B468_a_block_does_not_raise_the_slot_count(tmp_path):
    """B468: it lifts the calendar, not the number of things that may run at once."""
    config = d3_config(tmp_path)

    result = run_plan(config, blocked_ledger(3), 816, 823, 830)

    assert result.start == (816,), "MAX_CONCURRENT_ITEMS is untouched"
    assert result.skipped == {"823": "slots full", "830": "slots full"}


# --------------------------------------------------------------------------------------
# B469 - the run loop
# --------------------------------------------------------------------------------------


def test_B469_harness_run_starts_approved_items_outside_the_window_under_a_block(
    tmp_path, monkeypatch, capsys
):
    """B469: `harness run` with no `--item` is what a merge-triggered implement run and the
    three-hourly sweep call, so it has to honour a block the way the plan does. Driven end to
    end through the CLI: the block is set by `harness block`, saved, and read back by `run`."""
    at = datetime(2026, 9, 2, 18, 41, tzinfo=timezone.utc)  # Wednesday, outside daily 11:00-15:00
    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path, **DAILY_WINDOW)
    assert cli.main(["init"]) == 0
    item_id = make_item(tmp_path, state="approved")
    write_spec(tmp_path, item_id)
    write_carry_ledger(tmp_path)  # carry is None: nothing is being carried here
    align_ledger_window(tmp_path, at)
    freeze_run_clock(monkeypatch, at)
    ran: list = []
    record_stages(monkeypatch, ran)
    clone_dir = tmp_path / "runs" / f"item-{item_id}" / "clone"
    clone_dir.mkdir(parents=True, exist_ok=True)
    lease_on(monkeypatch, clone_dir, "0" * 40)
    forbid_network(monkeypatch)
    capsys.readouterr()

    # Without a block, the window holds it back.
    assert cli.main(["run"]) == 0
    assert "outside run window (daily 11:00-15:00 UTC); nothing started" in capsys.readouterr().out
    assert ran == []

    assert cli.main(["block", "2"]) == 0
    capsys.readouterr()

    assert cli.main(["run"]) == 0

    captured = capsys.readouterr()
    assert "outside run window" not in captured.out, captured.out
    assert "implement" in stage_names(ran), f"a block must start the item: {ran} {captured}"
    assert [entry[1] for entry in ran if entry[0] == "implement"] == [item_id]


# --------------------------------------------------------------------------------------
# B470 - one renderer
# --------------------------------------------------------------------------------------


def test_B470_one_renderer_reports_the_block_on_every_surface(tmp_path):
    """B470: the `queue_lines` pattern. Two answers to "is the window suspended" that can
    disagree are worse than one answer, so every surface prints the same string."""
    rig = request_rig(tmp_path)
    led = rig.ctx.ledger
    now = rig.ctx.clock.now()
    led.request_block("jgoetzmann", 3, now, "away this afternoon")

    line = links.block_line(led, now)

    assert line.startswith("> **Run window suspended** by @jgoetzmann")
    assert "3 sessions" in line and "away this afternoon" in line
    assert "/harness block 0" in line

    reply = cli._usage_report(rig.ctx, rig.config, now)
    pinned = "\n".join(cli._queue_block_lines(rig.ctx, rig.config, now))
    fast = links.fast_status(rig.config, led, now=now, window="`x` → `y` UTC", next_sweep="soon")

    for surface, text in (("reply", reply), ("pinned issue", pinned), ("fast status", fast)):
        assert line in text, f"the {surface} does not carry the one rendered line"

    led.clear_block()
    assert links.block_line(led, now) == ""
    assert "Run window suspended" not in cli._usage_report(rig.ctx, rig.config, now)


def test_B470_an_expired_grant_reads_as_expired_rather_than_as_standing(tmp_path):
    """B470: a surface that still said "suspended" after the block lapsed would be telling a
    reader the window is open when it is shut."""
    led = Ledger.empty(PERIOD_START)
    led.request_block("jgoetzmann", 1, NOW, "")
    later = NOW + timedelta(hours=SESSION_HOURS + 1)

    assert "Run window suspended" in links.block_line(led, NOW)
    assert "Block expired" in links.block_line(led, later)


# --------------------------------------------------------------------------------------
# B471 - the CLI half
# --------------------------------------------------------------------------------------


def test_B471_the_cli_sets_and_clears_the_same_grant_and_names_what_still_applies(
    tmp_path, monkeypatch, capsys
):
    """B471: `harness block` writes the same grant in the same place as `/harness block`, so the
    two forms cannot come to mean different things."""
    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path, **DAILY_WINDOW)
    assert cli.main(["init"]) == 0
    freeze_run_clock(monkeypatch, NOW)
    forbid_network(monkeypatch)
    capsys.readouterr()

    assert cli.main(["block", "3", "--reason", "away this afternoon"]) == 0
    out = capsys.readouterr().out
    assert "run window suspended for 3 session(s)" in out
    assert "usage stops" in out and "human gates" in out
    assert "$" not in out

    stored = json.loads((tmp_path / "state" / "ledger.json").read_text(encoding="utf-8"))
    grant = stored["window"]["block"]
    assert grant["sessions"] == 3 and grant["by"] == "operator"
    assert grant["until"] == iso(NOW + timedelta(hours=3 * SESSION_HOURS))
    assert grant["reason"] == "away this afternoon"

    assert cli.main(["block"]) == 0
    assert "Run window suspended" in capsys.readouterr().out

    assert cli.main(["block", "0"]) == 0
    assert "block cancelled" in capsys.readouterr().out
    after = json.loads((tmp_path / "state" / "ledger.json").read_text(encoding="utf-8"))
    assert "block" not in after["window"]

    assert cli.main(["block"]) == 0
    assert "no block stands" in capsys.readouterr().out


def test_B471_the_cli_refuses_a_count_it_cannot_honour(tmp_path, monkeypatch, capsys):
    """B471: refused rather than clamped, and exit 1 so a script notices."""
    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path)
    assert cli.main(["init"]) == 0
    forbid_network(monkeypatch)
    capsys.readouterr()

    assert cli.main(["block", str(MAX_BLOCK_SESSIONS + 1)]) == 1
    assert str(MAX_BLOCK_SESSIONS) in capsys.readouterr().err

    assert cli.main(["block", "soon"]) == 1
    capsys.readouterr()

    assert not (tmp_path / "state" / "ledger.json").exists() or "block" not in json.loads(
        (tmp_path / "state" / "ledger.json").read_text(encoding="utf-8")
    )["window"]


def test_B471_the_sweep_clears_a_spent_grant_without_anything_depending_on_it(tmp_path):
    """B471: the block ends by the clock, so this is tidying and not enforcement. `block_open`
    is already False by then; clearing keeps a lapsed grant off the surfaces that report one."""
    led = Ledger.empty(PERIOD_START)
    led.request_block("jgoetzmann", 1, NOW, "")
    later = NOW + timedelta(hours=SESSION_HOURS + 1)

    assert led.block_open(later) is False, "it is already over before anything is cleared"
    source = re.sub(r"\s+", " ", __import__("inspect").getsource(cli.cmd_sweep))
    assert "clear_block()" in source, "the sweep tidies a spent grant away"
