"""Spec tests for ``harness.dispatcher`` (B121–B123, B107).

Fixtures are inline.
"""
from __future__ import annotations

import dataclasses
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from harness.clock import FrozenClock, iso
from harness.config import load_config
from harness.dispatcher import Candidate, Plan, plan
from harness.errors import ConfigError
from harness.ledger import Ledger

NOW = FrozenClock(datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)).now()
NOW_ISO = iso(NOW)
PERIOD_START = "2026-08-31T00:00:00Z"
RUN_URL = "https://github.com/jgoetzmann/bright-bots-harness/actions/runs/1"
SLOTS_REASON = re.compile(r"^(\d+) of max (\d+) slots$")

# Every key the config requires.
BASE_ENV: tuple[tuple[str, str], ...] = (
    ("BACKEND", "fake"),
    ("REPO", "Bright-Bots-Initiative/brightboost"),
    ("PERMISSION_TIER", "0"),
    ("ALLOWLIST_LABEL", "harness-ok"),
    ("MAX_TURNS_DISCOVER", "10"),
    ("MAX_TURNS_PROPOSE", "30"),
    ("MAX_TURNS_IMPLEMENT", "80"),
    ("MAX_TURNS_PACKAGE", "10"),
    ("MAX_RETRIES_GATES", "2"),
    ("GITHUB_API_CEILING_PER_HOUR", "50"),
    ("MIN_FREE_DISK_GB", "5"),
    ("DB_PATH", "harness.db"),
    ("RUNS_DIR", "runs"),
    ("PACKAGES_DIR", "packages"),
    ("HALT_FILE", "HALT"),
    ("FULLSEND_ENABLED", "false"),
    ("HARNESS_GITHUB_TOKEN", ""),
    ("ANTHROPIC_API_KEY", ""),
    ("MAX_CONCURRENT_ITEMS", "1"),
    ("MAX_REVISE_CYCLES", "3"),
    ("FORK_REPO", ""),
    ("UPSTREAM_REPO", "Bright-Bots-Initiative/brightboost"),
    ("TRUST_FILE", ".harness/trust.txt"),
    ("MAX_SUBISSUES", "8"),
    ("SELF_REPO", "jgoetzmann/bright-bots-harness"),
    ("TRACKING_ISSUE", ""),
    ("STORE_BACKEND", "sqlite"),
    ("MODEL", "opus"),
    ("EFFORT", "xhigh"),
    ("INBOX_ISSUE", "0"),
    ("SUGGEST_MAX_PER_RUN", "5"),
    ("COMMENT_UPSTREAM", "true"),
    ("ASK_MAX_PER_DAY", "20"),
    ("SUGGEST_MIN_HEADROOM_PCT", "50"),
    ("AUDIT_MIN_HEADROOM_PCT", "75"),
    ("MAX_SELF_AUDIT_CYCLES", "3"),
    ("WEEKLY_USAGE_STOP_PCT", "90"),
    ("SESSION_USAGE_STOP_PCT", "70"),
    ("OVERRUN_PCT", "10"),
    ("RUN_WINDOW_START", ""),
    ("RUN_WINDOW_END", ""),
)


def write_env(tmp_path: Path, **overrides: str) -> Path:
    env = dict(BASE_ENV)
    env.update(overrides)
    path = tmp_path / ".env"
    path.write_text("".join(f"{k}={v}\n" for k, v in env.items()), encoding="utf-8",
                    newline="\n")
    return path


def make_config(tmp_path: Path, **overrides: str):
    return load_config(env_path=write_env(tmp_path, **overrides), environ={})


def github_config(tmp_path: Path, slots: int = 3):
    return make_config(tmp_path, STORE_BACKEND="github", MAX_CONCURRENT_ITEMS=str(slots),
                       FORK_REPO="bb-machine/brightboost")


def empty_ledger() -> Ledger:
    """A ledger whose window is the period containing NOW, with nothing recorded."""
    return Ledger.empty(PERIOD_START)


def cands(*issues: int) -> tuple[Candidate, ...]:
    """Candidates whose created_at increases with position — oldest first as given."""
    return tuple(Candidate(issue=n, created_at=f"2026-09-01T10:{k:02d}:00Z")
                 for k, n in enumerate(issues))


def run_plan(config, ledger: Ledger, candidates, *, merged=frozenset(), halted=False,
             now=NOW) -> Plan:
    return plan(now=now, ledger=ledger, config=config, candidates=tuple(candidates),
                merged=frozenset(merged), halted=halted)


# ---------------------------------------------------------------------------
# B121 — rate limited → empty plan naming the limit
# ---------------------------------------------------------------------------

def test_B121_rate_limited_until_in_the_future_gives_empty_plan_naming_the_limit(tmp_path):
    """B121: while now < rate_limited_until the dispatcher starts nothing and the reason names
    the reset time."""
    config = make_config(tmp_path)
    ledger = empty_ledger()
    ledger.set_rate_limited("2026-09-02T13:00:00Z")
    result = run_plan(config, ledger, cands(816, 823))
    assert result.start == ()
    assert result.reason == "rate limited until 2026-09-02T13:00:00Z"
    data = json.loads(result.to_json())
    assert data["start"] == []
    assert data["reason"] == "rate limited until 2026-09-02T13:00:00Z"


def test_B121_rate_limit_expired_at_now_starts_work(tmp_path):
    """B121: at now == rate_limited_until the limit is over — the plan starts the candidate."""
    config = make_config(tmp_path)
    ledger = empty_ledger()
    ledger.set_rate_limited(NOW_ISO)
    result = run_plan(config, ledger, cands(816))
    assert result.start == (816,)
    assert "rate limited" not in result.reason


def test_B121_rate_limit_in_the_past_starts_work(tmp_path):
    """B121: a reset time already passed does not block the plan."""
    config = make_config(tmp_path)
    ledger = empty_ledger()
    ledger.set_rate_limited("2026-09-01T00:00:00Z")
    result = run_plan(config, ledger, cands(816))
    assert result.start == (816,)


def test_B121_rate_limit_is_checked_before_halt(tmp_path):
    """B121: step 1 (rate limit) precedes step 2 (halt) — both set, the reason is the
    rate limit."""
    config = make_config(tmp_path)
    ledger = empty_ledger()
    ledger.set_rate_limited("2026-09-02T13:00:00Z")
    result = run_plan(config, ledger, cands(816), halted=True)
    assert result.start == ()
    assert result.reason == "rate limited until 2026-09-02T13:00:00Z"


# ---------------------------------------------------------------------------
# halt (the CLI-level HALT exit code and ordering are tested elsewhere)
# ---------------------------------------------------------------------------

def test_B122_halted_gives_empty_plan_with_reason_halted(tmp_path):
    """B122: a repo-level HALT yields an empty plan with reason 'halted' even with
    candidates available."""
    config = make_config(tmp_path)
    result = run_plan(config, empty_ledger(), cands(816, 823), halted=True)
    assert result.start == ()
    assert result.reason == "halted"
    assert json.loads(result.to_json())["start"] == []


# ---------------------------------------------------------------------------
# B107 — depends_on
# ---------------------------------------------------------------------------

def test_B107_unmet_depends_on_is_skipped_with_the_exact_reason(tmp_path):
    """B107: a candidate whose depends_on is not all merged is dropped and reported as
    'depends_on <n> not merged' under its issue number as a string key."""
    config = github_config(tmp_path, slots=3)
    candidates = (Candidate(issue=816, created_at="2026-09-01T10:00:00Z"),
                  Candidate(issue=819, depends_on=(816,), created_at="2026-09-01T10:01:00Z"))
    result = run_plan(config, empty_ledger(), candidates, merged=set())
    assert result.start == (816,)
    assert result.skipped == {"819": "depends_on 816 not merged"}
    data = json.loads(result.to_json())
    assert data["start"] == [816]
    assert data["skipped"] == {"819": "depends_on 816 not merged"}


def test_B107_partially_met_depends_on_names_the_unmerged_one(tmp_path):
    """B107: all dependencies must be merged; the reason names the one that is not."""
    config = github_config(tmp_path, slots=3)
    candidates = (Candidate(issue=830, depends_on=(816, 817), created_at="2026-09-01T10:00:00Z"),)
    result = run_plan(config, empty_ledger(), candidates, merged={816})
    assert result.start == ()
    assert result.skipped == {"830": "depends_on 817 not merged"}


def test_B107_fully_merged_depends_on_starts(tmp_path):
    """B107: once every dependency is stage:done the candidate is eligible."""
    config = github_config(tmp_path, slots=3)
    candidates = (Candidate(issue=830, depends_on=(816, 817), created_at="2026-09-01T10:00:00Z"),)
    result = run_plan(config, empty_ledger(), candidates, merged={816, 817, 900})
    assert result.start == (830,)
    assert result.skipped == {}


def test_B107_dependency_skip_does_not_consume_a_slot(tmp_path):
    """B107: step 5 drops unmet dependencies before step 7 takes slots — the blocked
    oldest candidate does not crowd out the next one."""
    config = make_config(tmp_path)  # sqlite → one slot
    candidates = (Candidate(issue=819, depends_on=(816,), created_at="2026-09-01T10:00:00Z"),
                  Candidate(issue=823, created_at="2026-09-01T10:05:00Z"))
    result = run_plan(config, empty_ledger(), candidates, merged=set())
    assert result.start == (823,)
    assert result.skipped == {"819": "depends_on 816 not merged"}


# ---------------------------------------------------------------------------
# B123 — slots
# ---------------------------------------------------------------------------

def test_B123_github_mode_cap_is_max_concurrent_items(tmp_path):
    """B123: in github (Actions) mode the cap is MAX_CONCURRENT_ITEMS — three of four candidates
    start, the newest is 'slots full', the reason reports 3 of max 3."""
    config = github_config(tmp_path, slots=3)
    assert config.max_concurrent_items == 3
    result = run_plan(config, empty_ledger(), cands(816, 823, 830, 841))
    assert result.start == (816, 823, 830)
    assert result.skipped == {"841": "slots full"}
    m = SLOTS_REASON.match(result.reason)
    assert m, result.reason
    assert (m.group(1), m.group(2)) == ("3", "3")


def test_B123_sqlite_mode_cap_is_one(tmp_path):
    """B123: local (sqlite) mode keeps one clone — exactly one candidate starts, the rest are
    'slots full', the reason reports 1 of max 1."""
    config = make_config(tmp_path)
    assert config.store_backend == "sqlite"
    assert config.max_concurrent_items == 1
    result = run_plan(config, empty_ledger(), cands(816, 823, 830))
    assert result.start == (816,)
    assert result.skipped == {"823": "slots full", "830": "slots full"}
    m = SLOTS_REASON.match(result.reason)
    assert m, result.reason
    assert (m.group(1), m.group(2)) == ("1", "1")


def test_B123_sqlite_mode_rejects_max_concurrent_items_above_one(tmp_path):
    """B123: above 1 is only permitted in Actions mode — MAX_CONCURRENT_ITEMS=2 with
    STORE_BACKEND=sqlite is a ConfigError, never a silently different cap."""
    with pytest.raises(ConfigError):
        make_config(tmp_path, MAX_CONCURRENT_ITEMS="2", STORE_BACKEND="sqlite")


def test_B123_max_concurrent_items_zero_is_rejected(tmp_path):
    """B123: MAX_CONCURRENT_ITEMS must be >= 1."""
    with pytest.raises(ConfigError):
        make_config(tmp_path, MAX_CONCURRENT_ITEMS="0")


# ---------------------------------------------------------------------------
# ordering
# ---------------------------------------------------------------------------

def test_B122_candidates_are_taken_oldest_first_regardless_of_input_order(tmp_path):
    """B122: oldest first by created_at — input order does not matter; with two
    slots the two oldest start and the newest is 'slots full'."""
    config = github_config(tmp_path, slots=2)
    candidates = (Candidate(issue=841, created_at="2026-09-01T12:00:00Z"),
                  Candidate(issue=816, created_at="2026-08-30T08:00:00Z"),
                  Candidate(issue=823, created_at="2026-08-31T09:00:00Z"))
    result = run_plan(config, empty_ledger(), candidates)
    assert result.start == (816, 823)
    assert result.skipped == {"841": "slots full"}


def test_B122_ties_on_created_at_break_by_issue_number(tmp_path):
    """B122: equal created_at orders by issue number."""
    config = github_config(tmp_path, slots=1)
    candidates = (Candidate(issue=823, created_at="2026-09-01T10:00:00Z"),
                  Candidate(issue=816, created_at="2026-09-01T10:00:00Z"))
    result = run_plan(config, empty_ledger(), candidates)
    assert result.start == (816,)
    assert result.skipped == {"823": "slots full"}


# ---------------------------------------------------------------------------
# B122 — pure, JSON shape
# ---------------------------------------------------------------------------

def test_B122_same_inputs_give_byte_identical_plans(tmp_path):
    """B122: the dispatcher is pure — the same ledger/config/candidates produce byte-identical
    to_json() output on two consecutive calls."""
    config = github_config(tmp_path, slots=2)
    ledger = empty_ledger()
    candidates = (Candidate(issue=816, created_at="2026-09-01T10:00:00Z"),
                  Candidate(issue=819, depends_on=(816,), created_at="2026-09-01T10:01:00Z"),
                  Candidate(issue=823, created_at="2026-09-01T10:02:00Z"),
                  Candidate(issue=830, created_at="2026-09-01T10:03:00Z"))
    first = run_plan(config, ledger, candidates, merged={700})
    second = run_plan(config, ledger, candidates, merged={700})
    assert first.to_json().encode("utf-8") == second.to_json().encode("utf-8")
    assert first == second
    assert first.start == (816, 823)
    assert first.skipped == {"819": "depends_on 816 not merged", "830": "slots full"}


def test_B122_plan_mutates_nothing_and_starts_nothing(tmp_path):
    """B122: planning leaves the ledger JSON, the config and the filesystem exactly as they were —
    it emits a plan, it never starts work."""
    config = github_config(tmp_path, slots=3)
    ledger = empty_ledger()
    ledger.mark_seen("IC_1")
    ledger_before = ledger.to_json()
    config_before = repr(config)
    files_before = sorted(str(p) for p in tmp_path.rglob("*"))
    result = run_plan(config, ledger, cands(816, 823))
    assert result.start == (816, 823)
    assert ledger.to_json() == ledger_before
    assert repr(config) == config_before
    assert sorted(str(p) for p in tmp_path.rglob("*")) == files_before


def test_B122_to_json_key_order_and_types(tmp_path):
    """B122: the plan JSON is {"start": [...], "reason": "...", "skipped": {...}} in that
    order, indent=2, start as a list of ints, skipped keyed by issue-number strings."""
    config = github_config(tmp_path, slots=1)
    candidates = (Candidate(issue=816, created_at="2026-09-01T10:00:00Z"),
                  Candidate(issue=819, depends_on=(816,), created_at="2026-09-01T10:01:00Z"))
    text = run_plan(config, empty_ledger(), candidates).to_json()
    assert text.startswith('{\n  "start": [')
    data = json.loads(text)
    assert list(data.keys()) == ["start", "reason", "skipped"]
    assert data["start"] == [816]
    assert all(isinstance(n, int) for n in data["start"])
    assert data["skipped"] == {"819": "depends_on 816 not merged"}
    assert all(isinstance(k, str) for k in data["skipped"])
    assert isinstance(data["reason"], str) and SLOTS_REASON.match(data["reason"])


def test_B122_empty_candidates_give_empty_start_with_the_slots_reason(tmp_path):
    """B122: no candidates → nothing starts, nothing skipped, reason reports 0 of max n slots."""
    config = github_config(tmp_path, slots=3)
    result = run_plan(config, empty_ledger(), ())
    assert result.start == ()
    assert result.skipped == {}
    assert result.reason == "0 of max 3 slots"
    m = SLOTS_REASON.match(result.reason)
    assert m, result.reason
    assert (m.group(1), m.group(2)) == ("0", "3")
    assert json.loads(result.to_json()) == {"start": [], "reason": result.reason, "skipped": {}}


def test_B122_plan_fields_are_immutable_tuple_and_dict(tmp_path):
    """B122: Plan.start is a tuple of ints and Plan.skipped a dict of str→str."""
    config = make_config(tmp_path)
    result = run_plan(config, empty_ledger(), cands(816, 823))
    assert isinstance(result, Plan)
    assert isinstance(result.start, tuple)
    assert isinstance(result.skipped, dict)
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in result.skipped.items())


# ---------------------------------------------------------------------------
# The run window (B209, B210, B211) and config.in_run_window.
#
# plan() order: rate limit → halted → carry → usage stop → run window → candidates.
# NOW (2026-09-02T12:00:00Z) is a Wednesday, outside the mon 08:00 → tue 20:00 window.
# ---------------------------------------------------------------------------

# The five new keys with their .env.example values (inline).
D3_ENV: dict[str, str] = {
    "WEEKLY_USAGE_STOP_PCT": "90",
    "SESSION_USAGE_STOP_PCT": "70",
    "OVERRUN_PCT": "10",
    "RUN_WINDOW_START": "mon 08:00",
    "RUN_WINDOW_END": "tue 20:00",
}
WINDOW_REASON = "outside run window (mon 08:00-tue 20:00 UTC)"
WED = NOW                                                    # 2026-09-02T12:00:00Z, Wednesday
MON_INSIDE = datetime(2026, 9, 7, 9, 0, 0, tzinfo=timezone.utc)   # Monday 09:00 UTC
TUE_INSIDE = datetime(2026, 9, 8, 19, 0, 0, tzinfo=timezone.utc)  # Tuesday 19:00 UTC
SUN_NOON = datetime(2026, 9, 6, 12, 0, 0, tzinfo=timezone.utc)    # Sunday 12:00 UTC
D3_SEVEN_DAY_RESET = "2026-09-08T20:00:00Z"
D3_FIVE_HOUR_RESET = "2026-09-04T11:00:00Z"


def write_d3_env(tmp_path: Path, **overrides: str) -> Path:
    """Every D1 and D2 key (BASE_ENV) plus the five D3 keys, in a directory of its own."""
    env = dict(BASE_ENV)
    env.update(D3_ENV)
    env.update(overrides)
    path = tmp_path / ".env"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{k}={v}\n" for k, v in env.items()), encoding="utf-8",
                    newline="\n")
    return path


def d3_config(tmp_path: Path, **overrides: str):
    return load_config(env_path=write_d3_env(tmp_path, **overrides), environ={})


def d3_github_config(tmp_path: Path, slots: int = 3, **overrides: str):
    return d3_config(tmp_path, STORE_BACKEND="github", MAX_CONCURRENT_ITEMS=str(slots),
                     FORK_REPO="bb-machine/brightboost", **overrides)


def d3_usage(*, weekly: float = 0.49, session: float = 0.07) -> dict:
    return {
        "five_hour": {"utilization": session, "resets_at": D3_FIVE_HOUR_RESET},
        "seven_day": {"utilization": weekly, "resets_at": D3_SEVEN_DAY_RESET},
        "status": "allowed",
        "observed_at": NOW_ISO,
    }


def usage_ledger(*, weekly: float = 0.49, session: float = 0.07,
                 carry: int | None = None) -> Ledger:
    """A ledger that has seen the signal, optionally carrying an item across the reset."""
    ledger = empty_ledger()
    ledger.observe_usage(d3_usage(weekly=weekly, session=session), NOW_ISO)
    if carry is not None:
        ledger.set_carry(carry, NOW_ISO, "weekly usage 91% >= 90%")
    return ledger


# ---------------------------------------------------------------------------
# dispatcher.usage_stop — the same rules the governor applies, for the planner
# ---------------------------------------------------------------------------

def test_B211_usage_stop_reports_the_weekly_and_session_reasons(tmp_path):
    """usage_stop(ledger, config, carry=False) is the planner's
    copy of the governor's rule, with the same exact reasons."""
    from harness.dispatcher import usage_stop

    config = d3_config(tmp_path)
    assert usage_stop(usage_ledger(weekly=0.91), config) == "weekly usage 91% >= 90%"
    assert usage_stop(usage_ledger(weekly=0.10, session=0.72), config) == \
        "session usage 72% >= 70%"
    assert usage_stop(usage_ledger(weekly=0.49, session=0.07), config) is None


def test_B211_usage_stop_uses_the_leeway_for_a_carried_item(tmp_path):
    """carry=True swaps the weekly stop for OVERRUN_PCT."""
    from harness.dispatcher import usage_stop

    config = d3_config(tmp_path)
    ledger = usage_ledger(weekly=0.12)
    assert usage_stop(ledger, config, carry=True) == "carry leeway 10% reached"
    assert usage_stop(ledger, config, carry=False) is None
    assert usage_stop(usage_ledger(weekly=0.05), config, carry=True) is None


def test_B211_usage_stop_is_none_without_the_signal(tmp_path):
    """B114: no decision may DEPEND on the signal — a ledger that never saw a
    rate_limit_event never trips the usage stop."""
    from harness.dispatcher import usage_stop

    config = d3_config(tmp_path)
    assert usage_stop(empty_ledger(), config) is None
    assert usage_stop(empty_ledger(), config, carry=True) is None


def test_B211_a_usage_stop_empties_the_plan_with_that_reason(tmp_path):
    """The usage stop sits between the halt and the run window — past
    the weekly threshold nothing starts and the reason is the usage reason."""
    config = d3_config(tmp_path, RUN_WINDOW_START="", RUN_WINDOW_END="")
    result = run_plan(config, usage_ledger(weekly=0.91), cands(816, 823))
    assert result.start == ()
    assert result.reason == "weekly usage 91% >= 90%"
    assert json.loads(result.to_json())["reason"] == "weekly usage 91% >= 90%"


def test_B211_the_rate_limit_is_still_checked_before_the_usage_stop(tmp_path):
    """The D2 steps keep their places — a live rate limit is
    still the first thing reported."""
    config = d3_config(tmp_path, RUN_WINDOW_START="", RUN_WINDOW_END="")
    ledger = usage_ledger(weekly=0.91)
    ledger.set_rate_limited("2026-09-02T13:00:00Z")
    result = run_plan(config, ledger, cands(816))
    assert result.reason == "rate limited until 2026-09-02T13:00:00Z"


def test_B211_halt_is_still_checked_before_the_usage_stop(tmp_path):
    """Halted still wins over the usage stop."""
    config = d3_config(tmp_path, RUN_WINDOW_START="", RUN_WINDOW_END="")
    result = run_plan(config, usage_ledger(weekly=0.91), cands(816), halted=True)
    assert result.reason == "halted"


# ---------------------------------------------------------------------------
# B209 — the carried item goes first, even outside the run window
# ---------------------------------------------------------------------------

def test_B209_the_carry_item_starts_outside_the_run_window(tmp_path):
    """B209: a carry item exists and usage_stop(carry=True) is None → it is the FIRST entry of
    start, even outside the run window; the ordinary older candidate stays put."""
    config = d3_config(tmp_path)
    ledger = usage_ledger(weekly=0.05, session=0.05, carry=816)

    result = run_plan(config, ledger, cands(810, 816), now=WED)

    assert result.start == (816,)


def test_B209_the_carry_item_is_first_inside_the_window_too(tmp_path):
    """B209: "FIRST entry of start" — inside the window the carried item jumps the oldest-first
    queue, and the remaining slots are filled as usual."""
    config = d3_github_config(tmp_path, slots=3)
    ledger = usage_ledger(weekly=0.05, session=0.05, carry=830)

    result = run_plan(config, ledger, cands(810, 823, 830), now=MON_INSIDE)

    assert result.start[0] == 830
    assert set(result.start) == {810, 823, 830}


DAILY_WINDOW = {"RUN_WINDOW_START": "daily 11:00", "RUN_WINDOW_END": "daily 15:00"}


def _wednesday_at(hour: int, minute: int = 0):
    from datetime import datetime, timezone

    return datetime(2026, 9, 2, hour, minute, tzinfo=timezone.utc)


def test_B413_a_carry_item_waits_outside_a_daily_window(tmp_path):
    """B413 (D72): outside a daily window the carried item does not start. The 18:41 UTC sweep
    (11:41 PDT) would otherwise resume it in the operator's own daytime session, and the plan
    names the window instead."""
    config = d3_config(tmp_path, **DAILY_WINDOW)
    ledger = usage_ledger(weekly=0.05, session=0.05, carry=816)

    result = run_plan(config, ledger, cands(810, 816), now=_wednesday_at(18, 41))

    assert result.start == ()
    assert result.reason == "outside run window (daily 11:00-15:00 UTC)"


def test_B413_a_carry_item_still_goes_first_inside_a_daily_window(tmp_path):
    """B413: the daily window delays the carry; it does not demote it. At 11:23 UTC the carried
    item is the first thing started, ahead of the older candidate."""
    config = d3_config(tmp_path, **DAILY_WINDOW)
    ledger = usage_ledger(weekly=0.05, session=0.05, carry=816)

    result = run_plan(config, ledger, cands(810, 816), now=_wednesday_at(11, 23))

    assert result.start == (816,)


def test_B209_a_carry_item_out_of_leeway_does_not_start(tmp_path):
    """B209: the carry only runs while usage_stop(carry=True) is None — once the leeway is
    spent the carried item waits like everything else."""
    config = d3_config(tmp_path)
    ledger = usage_ledger(weekly=0.30, session=0.05, carry=816)

    result = run_plan(config, ledger, cands(810, 816), now=WED)

    assert result.start == ()
    assert result.reason == WINDOW_REASON


def test_B209_a_carry_item_past_the_weekly_stop_reports_the_usage_stop(tmp_path):
    """B209/B211: past the weekly stop nothing starts at all — neither the carried item (out of
    leeway) nor the ordinary ones — and the reason is the usage stop."""
    config = d3_config(tmp_path)
    ledger = usage_ledger(weekly=0.95, session=0.10, carry=816)

    result = run_plan(config, ledger, cands(810, 816), now=WED)

    assert result.start == ()
    assert result.reason == "weekly usage 95% >= 90%"


def test_B209_no_carry_means_no_exemption(tmp_path):
    """B209: without a carry recorded in the ledger, no candidate gets the out-of-window
    exemption."""
    config = d3_config(tmp_path)

    result = run_plan(config, usage_ledger(weekly=0.05), cands(810, 816), now=WED)

    assert result.start == ()
    assert result.reason == WINDOW_REASON


# ---------------------------------------------------------------------------
# B210 — outside the run window nothing else starts, with the exact reason
# ---------------------------------------------------------------------------

def test_B210_outside_the_run_window_the_plan_is_empty_with_the_exact_reason(tmp_path):
    """B210: outside the run window no non-carry item starts; the reason names the window in
    UTC, exactly as configured."""
    config = d3_config(tmp_path)

    result = run_plan(config, usage_ledger(), cands(816, 823), now=WED)

    assert result.start == ()
    assert result.reason == "outside run window (mon 08:00-tue 20:00 UTC)"
    data = json.loads(result.to_json())
    assert data["start"] == []
    assert data["reason"] == "outside run window (mon 08:00-tue 20:00 UTC)"


def test_B210_the_reason_renders_the_configured_window(tmp_path):
    """B210: the window in the reason is whatever the operator configured, verbatim."""
    config = d3_config(tmp_path, RUN_WINDOW_START="sat 22:00", RUN_WINDOW_END="mon 08:00")

    result = run_plan(config, usage_ledger(), cands(816), now=WED)

    assert result.start == ()
    assert result.reason == "outside run window (sat 22:00-mon 08:00 UTC)"


def test_B210_inside_the_window_the_candidates_start(tmp_path):
    """B210: the window gates the ordinary path only while it is shut — Monday 09:00 UTC is
    inside mon 08:00 → tue 20:00."""
    config = d3_config(tmp_path)

    result = run_plan(config, usage_ledger(), cands(816), now=MON_INSIDE)

    assert result.start == (816,)
    assert "outside run window" not in result.reason


def test_B210_a_wrapping_window_is_open_on_sunday(tmp_path):
    """B210: the window may wrap past Sunday — sat 22:00 → mon 08:00 is open on Sunday noon and
    shut on Wednesday."""
    config = d3_config(tmp_path, RUN_WINDOW_START="sat 22:00", RUN_WINDOW_END="mon 08:00")

    inside = run_plan(config, usage_ledger(), cands(816), now=SUN_NOON)
    outside = run_plan(config, usage_ledger(), cands(816), now=WED)

    assert inside.start == (816,)
    assert outside.start == ()
    assert outside.reason == "outside run window (sat 22:00-mon 08:00 UTC)"


def test_B210_an_empty_window_is_always_open(tmp_path):
    """B210: both empty = always open, for a house that does not want a window."""
    config = d3_config(tmp_path, RUN_WINDOW_START="", RUN_WINDOW_END="")

    for moment in (WED, MON_INSIDE, SUN_NOON, TUE_INSIDE):
        result = run_plan(config, usage_ledger(), cands(816), now=moment)
        assert result.start == (816,)
        assert "outside run window" not in result.reason


# ---------------------------------------------------------------------------
# B211 — inside the window the selection is unchanged, with a usage suffix
# ---------------------------------------------------------------------------

def test_B211_the_reason_is_the_slot_count_with_the_utilization_appended(tmp_path):
    """B211: inside the window with usage under both thresholds the reason is the slot count
    with "; weekly 49%, session 7%" appended — percentages as integers."""
    config = d3_config(tmp_path)

    result = run_plan(config, usage_ledger(weekly=0.49, session=0.07), cands(816),
                      now=MON_INSIDE)

    assert result.start == (816,)
    assert result.reason == "1 of max 1 slots; weekly 49%, session 7%"


def test_B211_the_suffix_reports_the_latest_observation(tmp_path):
    """B211: the numbers are the last observed utilization, rounded to integers."""
    config = d3_config(tmp_path)

    result = run_plan(config, usage_ledger(weekly=0.85, session=0.66), cands(816),
                      now=MON_INSIDE)

    assert result.reason.endswith("; weekly 85%, session 66%")


def test_B211_without_the_signal_the_reason_is_the_bare_slot_count(tmp_path):
    """B211/B114: the suffix is appended only when usage is known — without the signal the
    reason is the slot count alone, character for character."""
    config = d3_config(tmp_path)

    result = run_plan(config, empty_ledger(), cands(816), now=MON_INSIDE)

    assert result.start == (816,)
    assert result.reason == "1 of max 1 slots"
    assert "weekly" not in result.reason
    assert "session" not in result.reason


def test_B211_inside_the_window_the_selection_is_unchanged(tmp_path):
    """B211: unchanged selection — oldest first, unmet dependencies skipped, slots enforced,
    the same skipped strings."""
    config = d3_github_config(tmp_path, slots=2)
    candidates = (Candidate(issue=816, created_at="2026-09-01T10:00:00Z"),
                  Candidate(issue=819, depends_on=(816,), created_at="2026-09-01T10:01:00Z"),
                  Candidate(issue=823, created_at="2026-09-01T10:02:00Z"),
                  Candidate(issue=830, created_at="2026-09-01T10:03:00Z"))

    result = run_plan(config, usage_ledger(), candidates, merged={700}, now=MON_INSIDE)

    assert result.start == (816, 823)
    assert result.skipped == {"819": "depends_on 816 not merged", "830": "slots full"}
    assert result.reason.endswith("; weekly 49%, session 7%")


# ---------------------------------------------------------------------------
# B420 — a pre-D74 ledger's spend stops nothing, and no dollar state survives
# ---------------------------------------------------------------------------

def test_B420_a_pre_d74_ledger_with_spend_no_longer_stops_anything(tmp_path):
    """B420 (D74): the live ledger carried a spend total, a per-stage observation map and USD
    on every history entry. None of it is read, so the candidate starts."""
    fixture = Path(__file__).resolve().parent / "fixtures" / "ledger" / "pre_d74.json"
    config = make_config(tmp_path)
    ledger = Ledger.from_json(fixture.read_text(encoding="utf-8"))
    ledger.window.pop("usage", None)  # the reading is B399's business, not D74's

    result = run_plan(config, ledger, cands(816))

    assert result.start == (816,)
    assert result.reason == "1 of max 1 slots"
    assert result.skipped == {}


def test_B420_the_dispatcher_holds_no_dollar_state(tmp_path):
    """B420 (D74): no static table, no estimator, no per-candidate stage, and nothing in the
    planner's own source that reads or prints money."""
    import inspect

    from harness import dispatcher as dispatcher_module

    assert not hasattr(dispatcher_module, "STATIC_USD")
    assert not hasattr(dispatcher_module, "estimate_usd")
    assert dispatcher_module.__all__ == ["Candidate", "Plan", "plan", "usage_stop"]
    assert "stage" not in {f.name for f in dataclasses.fields(Candidate)}
    source = inspect.getsource(dispatcher_module.plan).lower()
    for token in ("usd", "reserve", "$"):
        assert token not in source, token

    # Order: outside the window a weekly stop is still the reason, because the usage stop is
    # checked first.
    config = d3_config(tmp_path)
    result = run_plan(config, usage_ledger(weekly=0.95), cands(816), now=WED)
    assert result.reason == "weekly usage 95% >= 90%"


def test_B211_planning_still_mutates_nothing(tmp_path):
    """B211/B122: the dispatcher stays pure — the usage-aware plan changes neither the ledger
    nor the filesystem, and two identical calls are byte-identical."""
    config = d3_github_config(tmp_path, slots=2)
    ledger = usage_ledger(weekly=0.05, session=0.05, carry=816)
    before = ledger.to_json()

    first = run_plan(config, ledger, cands(810, 816, 823), now=WED)
    second = run_plan(config, ledger, cands(810, 816, 823), now=WED)

    assert ledger.to_json() == before
    assert first.to_json().encode("utf-8") == second.to_json().encode("utf-8")
    assert first.start == (816,)


# ---------------------------------------------------------------------------
# in_run_window — the wrap cases
# ---------------------------------------------------------------------------

def test_B210_in_run_window_inside_and_outside(tmp_path):
    """in_run_window(config, now) is pure and UTC — Monday 09:00 and
    Tuesday 19:00 are inside mon 08:00 → tue 20:00; Wednesday noon is not."""
    from harness.config import in_run_window

    config = d3_config(tmp_path)

    assert in_run_window(config, MON_INSIDE) is True
    assert in_run_window(config, TUE_INSIDE) is True
    assert in_run_window(config, WED) is False
    assert in_run_window(config, SUN_NOON) is False


def test_B210_in_run_window_wraps_past_sunday(tmp_path):
    """The window may wrap — sat 22:00 → mon 08:00 covers Saturday
    night, all of Sunday and Monday morning, and nothing else."""
    from harness.config import in_run_window

    config = d3_config(tmp_path, RUN_WINDOW_START="sat 22:00", RUN_WINDOW_END="mon 08:00")

    assert in_run_window(config, datetime(2026, 9, 5, 23, 0, tzinfo=timezone.utc)) is True
    assert in_run_window(config, SUN_NOON) is True
    assert in_run_window(config, datetime(2026, 9, 7, 7, 0, tzinfo=timezone.utc)) is True
    assert in_run_window(config, datetime(2026, 9, 5, 21, 0, tzinfo=timezone.utc)) is False
    assert in_run_window(config, datetime(2026, 9, 7, 9, 0, tzinfo=timezone.utc)) is False
    assert in_run_window(config, WED) is False


def test_B210_an_empty_window_is_always_in(tmp_path):
    """Both empty = always open."""
    from harness.config import in_run_window

    config = d3_config(tmp_path, RUN_WINDOW_START="", RUN_WINDOW_END="")

    for day in range(1, 8):
        moment = datetime(2026, 9, day, 3, 30, tzinfo=timezone.utc)
        assert in_run_window(config, moment) is True
