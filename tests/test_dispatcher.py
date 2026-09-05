"""Spec tests for ``harness.dispatcher`` — Delivery 2 handoff §6.4 (B121–B123, B107).

Written from the spec before the implementation existed. Surface is frozen by
``.fullsend/RUN-DECISIONS-D2.md`` §5; Config keys by §2 plus Delivery 1's list.
Fixtures are inline on purpose.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from harness.clock import FrozenClock, iso
from harness.config import load_config
from harness.dispatcher import STATIC_USD, Candidate, Plan, plan
from harness.errors import ConfigError
from harness.ledger import Ledger

NOW = FrozenClock(datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)).now()
NOW_ISO = iso(NOW)
PERIOD_START = "2026-08-31T00:00:00Z"
RUN_URL = "https://github.com/jgoetzmann/bright-bots-harness/actions/runs/1"
BUDGET_REASON = re.compile(r"^budget \d+% remaining, (\d+) of max (\d+) slots$")

# Every key Delivery 1 requires (RUN-DECISIONS "Config extras") plus every Delivery 2 §2 key.
BASE_ENV: tuple[tuple[str, str], ...] = (
    ("BACKEND", "fake"),
    ("REPO", "Bright-Bots-Initiative/brightboost"),
    ("PERMISSION_TIER", "0"),
    ("ALLOWLIST_LABEL", "harness-ok"),
    ("WEEKLY_BUDGET_PCT", "40"),
    ("SESSION_BUDGET_PCT", "15"),
    ("RESERVE_PCT", "10"),
    ("WEEKLY_RESET_DAY", "monday"),
    ("MAX_CONCURRENT_CLONES", "1"),
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
    ("WEEKLY_CAP_USD", "25.00"),
    ("PER_CALL_CAP_USD", "3.00"),
    ("MAX_CONCURRENT_ITEMS", "1"),
    ("MAX_REVISE_CYCLES", "3"),
    ("FORK_REPO", ""),
    ("UPSTREAM_REPO", "Bright-Bots-Initiative/brightboost"),
    ("TRUST_FILE", ".harness/trust.txt"),
    ("NOTIFY_POLL_HOURS", "3"),
    ("MAX_SUBISSUES", "8"),
    ("SELF_REPO", "jgoetzmann/bright-bots-harness"),
    ("TRACKING_ISSUE", ""),
    ("STORE_BACKEND", "sqlite"),
    ("MODEL", "opus"),
    ("EFFORT", "xhigh"),
    ("INBOX_ISSUE", "0"),
    ("AUDIT_CAP_USD", "20.00"),
    ("SUGGEST_MAX_PER_RUN", "5"),
    ("COMMENT_UPSTREAM", "true"),
    ("ASK_CAP_USD", "0.50"),
    ("ASK_MAX_PER_DAY", "20"),
    ("SUGGEST_MIN_HEADROOM_PCT", "50"),
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


def ledger_spent(spent_usd: float) -> Ledger:
    """A ledger with exactly `spent_usd` spent, recorded under a stage no candidate uses so the
    implement estimate stays on the static table."""
    ledger = Ledger.empty(PERIOD_START)
    if spent_usd:
        ledger.record(ts=NOW_ISO, stage="package", issue=1, usd=spent_usd, run=RUN_URL)
    return ledger


def cands(*issues: int, stage: str = "implement") -> tuple[Candidate, ...]:
    """Candidates whose created_at increases with position — oldest first as given."""
    return tuple(Candidate(issue=n, stage=stage, created_at=f"2026-09-01T10:{k:02d}:00Z")
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
    ledger = ledger_spent(0.0)
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
    ledger = ledger_spent(0.0)
    ledger.set_rate_limited(NOW_ISO)
    result = run_plan(config, ledger, cands(816))
    assert result.start == (816,)
    assert "rate limited" not in result.reason


def test_B121_rate_limit_in_the_past_starts_work(tmp_path):
    """B121: a reset time already passed does not block the plan."""
    config = make_config(tmp_path)
    ledger = ledger_spent(0.0)
    ledger.set_rate_limited("2026-09-01T00:00:00Z")
    result = run_plan(config, ledger, cands(816))
    assert result.start == (816,)


def test_B121_rate_limit_is_checked_before_halt(tmp_path):
    """B121/§6.4 order: step 1 (rate limit) precedes step 2 (halt) — both set, the reason is the
    rate limit."""
    config = make_config(tmp_path)
    ledger = ledger_spent(0.0)
    ledger.set_rate_limited("2026-09-02T13:00:00Z")
    result = run_plan(config, ledger, cands(816), halted=True)
    assert result.start == ()
    assert result.reason == "rate limited until 2026-09-02T13:00:00Z"


# ---------------------------------------------------------------------------
# halt (§6.4 step 2; the CLI-level HALT exit code and ordering are tested elsewhere)
# ---------------------------------------------------------------------------

def test_B122_halted_gives_empty_plan_with_reason_halted(tmp_path):
    """B122 (§6.4 step 2): a repo-level HALT yields an empty plan with reason 'halted' even with
    budget and candidates available."""
    config = make_config(tmp_path)
    result = run_plan(config, ledger_spent(0.0), cands(816, 823), halted=True)
    assert result.start == ()
    assert result.reason == "halted"
    assert json.loads(result.to_json())["start"] == []


def test_B122_halt_is_checked_before_reserve(tmp_path):
    """B122/§6.4 order: step 2 (halt) precedes step 3 (reserve)."""
    config = make_config(tmp_path)
    result = run_plan(config, ledger_spent(24.0), cands(816), halted=True)
    assert result.start == ()
    assert result.reason == "halted"


# ---------------------------------------------------------------------------
# reserve (§6.4 step 3)
# ---------------------------------------------------------------------------

def test_B122_reserve_boundary_spent_equals_cap_times_one_minus_reserve(tmp_path):
    """B122 (§6.4 step 3): spent_usd == weekly_cap_usd*(1-reserve_pct/100) → empty plan, reason
    exactly 'reserve'. 25.00 * 0.90 = 22.50."""
    config = make_config(tmp_path)
    assert config.weekly_cap_usd == pytest.approx(25.0)
    assert config.reserve_pct == pytest.approx(10.0)
    result = run_plan(config, ledger_spent(22.50), cands(816))
    assert result.start == ()
    assert result.reason == "reserve"


def test_B122_reserve_when_spent_exceeds_the_boundary(tmp_path):
    """B122 (§6.4 step 3): spend past the reserve line → empty plan, reason 'reserve'."""
    config = make_config(tmp_path)
    result = run_plan(config, ledger_spent(23.10), cands(816, 823))
    assert result.start == ()
    assert result.reason == "reserve"
    data = json.loads(result.to_json())
    assert data["start"] == []
    assert data["reason"] == "reserve"


def test_B122_just_below_reserve_is_not_reserve_but_estimate_skips(tmp_path):
    """B122 (§6.4 steps 3 and 6): one cent below the reserve line is not 'reserve'; the candidate
    is instead skipped because its $2.50 static estimate exceeds the $0.50 remaining."""
    config = make_config(tmp_path)
    result = run_plan(config, ledger_spent(22.00), cands(816))
    assert result.reason != "reserve"
    assert result.start == ()
    assert result.skipped == {"816": "estimate $2.50 exceeds remaining $0.50"}


# ---------------------------------------------------------------------------
# B107 — depends_on
# ---------------------------------------------------------------------------

def test_B107_unmet_depends_on_is_skipped_with_the_exact_reason(tmp_path):
    """B107: a candidate whose depends_on is not all merged is dropped and reported as
    'depends_on <n> not merged' under its issue number as a string key."""
    config = github_config(tmp_path, slots=3)
    candidates = (Candidate(issue=816, created_at="2026-09-01T10:00:00Z"),
                  Candidate(issue=819, depends_on=(816,), created_at="2026-09-01T10:01:00Z"))
    result = run_plan(config, ledger_spent(0.0), candidates, merged=set())
    assert result.start == (816,)
    assert result.skipped == {"819": "depends_on 816 not merged"}
    data = json.loads(result.to_json())
    assert data["start"] == [816]
    assert data["skipped"] == {"819": "depends_on 816 not merged"}


def test_B107_partially_met_depends_on_names_the_unmerged_one(tmp_path):
    """B107: all dependencies must be merged; the reason names the one that is not."""
    config = github_config(tmp_path, slots=3)
    candidates = (Candidate(issue=830, depends_on=(816, 817), created_at="2026-09-01T10:00:00Z"),)
    result = run_plan(config, ledger_spent(0.0), candidates, merged={816})
    assert result.start == ()
    assert result.skipped == {"830": "depends_on 817 not merged"}


def test_B107_fully_merged_depends_on_starts(tmp_path):
    """B107: once every dependency is harness:merged the candidate is eligible."""
    config = github_config(tmp_path, slots=3)
    candidates = (Candidate(issue=830, depends_on=(816, 817), created_at="2026-09-01T10:00:00Z"),)
    result = run_plan(config, ledger_spent(0.0), candidates, merged={816, 817, 900})
    assert result.start == (830,)
    assert result.skipped == {}


def test_B107_dependency_skip_does_not_consume_a_slot(tmp_path):
    """B107/§6.4 order: step 5 drops unmet dependencies before step 7 takes slots — the blocked
    oldest candidate does not crowd out the next one."""
    config = make_config(tmp_path)  # sqlite → one slot
    candidates = (Candidate(issue=819, depends_on=(816,), created_at="2026-09-01T10:00:00Z"),
                  Candidate(issue=823, created_at="2026-09-01T10:05:00Z"))
    result = run_plan(config, ledger_spent(0.0), candidates, merged=set())
    assert result.start == (823,)
    assert result.skipped == {"819": "depends_on 816 not merged"}


# ---------------------------------------------------------------------------
# estimate vs remaining (§6.4 step 6)
# ---------------------------------------------------------------------------

def test_B122_static_estimate_exceeding_remaining_is_skipped(tmp_path):
    """B122 (§6.4 step 6): below three observations the static table applies — implement is
    $2.50; with $1.50 remaining (22.50 - 21.00) the candidate is skipped with the exact reason."""
    config = make_config(tmp_path)
    assert STATIC_USD["implement"] == pytest.approx(2.50)
    result = run_plan(config, ledger_spent(21.00), cands(816))
    assert result.start == ()
    assert result.skipped == {"816": "estimate $2.50 exceeds remaining $1.50"}
    assert BUDGET_REASON.match(result.reason), result.reason
    assert BUDGET_REASON.match(result.reason).group(1) == "0"


def test_B122_median_estimate_replaces_static_after_three_observations(tmp_path):
    """B122 (§6.4 step 6): with three implement observations the median ($1.00) is the estimate,
    which fits the $1.50 remaining where the static $2.50 would not."""
    config = make_config(tmp_path)
    ledger = Ledger.empty(PERIOD_START)
    for i in range(3):
        ledger.record(ts=NOW_ISO, stage="implement", issue=700 + i, usd=1.00, run=RUN_URL)
    ledger.record(ts=NOW_ISO, stage="package", issue=1, usd=18.00, run=RUN_URL)
    assert ledger.window["spent_usd"] == pytest.approx(21.00)
    assert ledger.median_usd("implement") == pytest.approx(1.00)
    result = run_plan(config, ledger, cands(816))
    assert result.start == (816,)
    assert result.skipped == {}


def test_B122_static_table_values(tmp_path):
    """B122/§6.4: the static estimates are the frozen table."""
    assert STATIC_USD == {"discover": 0.20, "propose": 0.50, "implement": 2.50, "revise": 1.00,
                          "decompose": 0.30, "package": 0.05}


def test_B122_cheaper_stage_fits_where_implement_does_not(tmp_path):
    """B122 (§6.4 step 6): the estimate is per candidate stage — a $0.50 propose fits in $1.50
    remaining while a $2.50 implement is skipped."""
    config = github_config(tmp_path, slots=3)
    candidates = (Candidate(issue=816, stage="implement", created_at="2026-09-01T10:00:00Z"),
                  Candidate(issue=823, stage="propose", created_at="2026-09-01T10:01:00Z"))
    result = run_plan(config, ledger_spent(21.00), candidates)
    assert result.start == (823,)
    assert result.skipped == {"816": "estimate $2.50 exceeds remaining $1.50"}


# ---------------------------------------------------------------------------
# B123 — slots
# ---------------------------------------------------------------------------

def test_B123_github_mode_cap_is_max_concurrent_items(tmp_path):
    """B123: in github (Actions) mode the cap is MAX_CONCURRENT_ITEMS — three of four candidates
    start, the newest is 'slots full', the reason reports 3 of max 3."""
    config = github_config(tmp_path, slots=3)
    assert config.max_concurrent_items == 3
    result = run_plan(config, ledger_spent(0.0), cands(816, 823, 830, 841))
    assert result.start == (816, 823, 830)
    assert result.skipped == {"841": "slots full"}
    m = BUDGET_REASON.match(result.reason)
    assert m, result.reason
    assert (m.group(1), m.group(2)) == ("3", "3")


def test_B123_sqlite_mode_cap_is_one(tmp_path):
    """B123: local (sqlite) mode keeps one clone — exactly one candidate starts, the rest are
    'slots full', the reason reports 1 of max 1."""
    config = make_config(tmp_path)
    assert config.store_backend == "sqlite"
    assert config.max_concurrent_items == 1
    result = run_plan(config, ledger_spent(0.0), cands(816, 823, 830))
    assert result.start == (816,)
    assert result.skipped == {"823": "slots full", "830": "slots full"}
    m = BUDGET_REASON.match(result.reason)
    assert m, result.reason
    assert (m.group(1), m.group(2)) == ("1", "1")


def test_B123_sqlite_mode_rejects_max_concurrent_items_above_one(tmp_path):
    """B123: above 1 is only permitted in Actions mode — MAX_CONCURRENT_ITEMS=2 with
    STORE_BACKEND=sqlite is a ConfigError, never a silently different cap."""
    with pytest.raises(ConfigError):
        make_config(tmp_path, MAX_CONCURRENT_ITEMS="2", STORE_BACKEND="sqlite")


def test_B123_max_concurrent_items_zero_is_rejected(tmp_path):
    """B123/§2: MAX_CONCURRENT_ITEMS must be >= 1."""
    with pytest.raises(ConfigError):
        make_config(tmp_path, MAX_CONCURRENT_ITEMS="0")


# ---------------------------------------------------------------------------
# ordering (§6.4 step 4)
# ---------------------------------------------------------------------------

def test_B122_candidates_are_taken_oldest_first_regardless_of_input_order(tmp_path):
    """B122 (§6.4 step 4): oldest first by created_at — input order does not matter; with two
    slots the two oldest start and the newest is 'slots full'."""
    config = github_config(tmp_path, slots=2)
    candidates = (Candidate(issue=841, created_at="2026-09-01T12:00:00Z"),
                  Candidate(issue=816, created_at="2026-08-30T08:00:00Z"),
                  Candidate(issue=823, created_at="2026-08-31T09:00:00Z"))
    result = run_plan(config, ledger_spent(0.0), candidates)
    assert result.start == (816, 823)
    assert result.skipped == {"841": "slots full"}


def test_B122_ties_on_created_at_break_by_issue_number(tmp_path):
    """B122/RUN-DECISIONS-D2 §5: equal created_at orders by issue number."""
    config = github_config(tmp_path, slots=1)
    candidates = (Candidate(issue=823, created_at="2026-09-01T10:00:00Z"),
                  Candidate(issue=816, created_at="2026-09-01T10:00:00Z"))
    result = run_plan(config, ledger_spent(0.0), candidates)
    assert result.start == (816,)
    assert result.skipped == {"823": "slots full"}


# ---------------------------------------------------------------------------
# B122 — pure, JSON shape
# ---------------------------------------------------------------------------

def test_B122_same_inputs_give_byte_identical_plans(tmp_path):
    """B122: the dispatcher is pure — the same ledger/config/candidates produce byte-identical
    to_json() output on two consecutive calls."""
    config = github_config(tmp_path, slots=2)
    ledger = ledger_spent(3.25)
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
    ledger = ledger_spent(1.0)
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
    """B122/§6.4: the plan JSON is {"start": [...], "reason": "...", "skipped": {...}} in that
    order, indent=2, start as a list of ints, skipped keyed by issue-number strings."""
    config = github_config(tmp_path, slots=1)
    candidates = (Candidate(issue=816, created_at="2026-09-01T10:00:00Z"),
                  Candidate(issue=819, depends_on=(816,), created_at="2026-09-01T10:01:00Z"))
    text = run_plan(config, ledger_spent(0.0), candidates).to_json()
    assert text.startswith('{\n  "start": [')
    data = json.loads(text)
    assert list(data.keys()) == ["start", "reason", "skipped"]
    assert data["start"] == [816]
    assert all(isinstance(n, int) for n in data["start"])
    assert data["skipped"] == {"819": "depends_on 816 not merged"}
    assert all(isinstance(k, str) for k in data["skipped"])
    assert isinstance(data["reason"], str) and BUDGET_REASON.match(data["reason"])


def test_B122_empty_candidates_give_empty_start_with_budget_reason(tmp_path):
    """B122: no candidates → nothing starts, nothing skipped, reason reports 0 of max n slots."""
    config = github_config(tmp_path, slots=3)
    result = run_plan(config, ledger_spent(0.0), ())
    assert result.start == ()
    assert result.skipped == {}
    m = BUDGET_REASON.match(result.reason)
    assert m, result.reason
    assert (m.group(1), m.group(2)) == ("0", "3")
    assert json.loads(result.to_json()) == {"start": [], "reason": result.reason, "skipped": {}}


def test_B122_plan_fields_are_immutable_tuple_and_dict(tmp_path):
    """B122/§5: Plan.start is a tuple of ints and Plan.skipped a dict of str→str."""
    config = make_config(tmp_path)
    result = run_plan(config, ledger_spent(0.0), cands(816, 823))
    assert isinstance(result, Plan)
    assert isinstance(result.start, tuple)
    assert isinstance(result.skipped, dict)
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in result.skipped.items())


# ---------------------------------------------------------------------------
# Delivery 3 — RUN-DECISIONS-D3 "Dispatcher" (B209, B210, B211) and config.in_run_window.
# Appended by the D3 spec-tester (T1); additions only.
#
# plan() order becomes: rate limit → halted → usage stop → reserve → run window → candidates.
# NOW (2026-09-02T12:00:00Z) is a Wednesday, i.e. OUTSIDE the mon 08:00 → tue 20:00 window.
# ---------------------------------------------------------------------------

# RUN-DECISIONS-D3 "Config" — the five new keys with their .env.example values (inline).
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


def usage_ledger(*, weekly: float = 0.49, session: float = 0.07, spent_usd: float = 0.0,
                 carry: int | None = None) -> Ledger:
    """A ledger that has seen the signal, optionally carrying an item across the reset."""
    ledger = ledger_spent(spent_usd)
    ledger.observe_usage(d3_usage(weekly=weekly, session=session), NOW_ISO)
    if carry is not None:
        ledger.set_carry(carry, NOW_ISO, "weekly usage 91% >= 90%")
    return ledger


# ---------------------------------------------------------------------------
# dispatcher.usage_stop — the same rules the governor applies, for the planner
# ---------------------------------------------------------------------------

def test_B211_usage_stop_reports_the_weekly_and_session_reasons(tmp_path):
    """RUN-DECISIONS-D3 "Dispatcher": usage_stop(ledger, config, carry=False) is the planner's
    copy of the governor's rule, with the same exact reasons."""
    from harness.dispatcher import usage_stop

    config = d3_config(tmp_path)
    assert usage_stop(usage_ledger(weekly=0.91), config) == "weekly usage 91% >= 90%"
    assert usage_stop(usage_ledger(weekly=0.10, session=0.72), config) == \
        "session usage 72% >= 70%"
    assert usage_stop(usage_ledger(weekly=0.49, session=0.07), config) is None


def test_B211_usage_stop_uses_the_leeway_for_a_carried_item(tmp_path):
    """RUN-DECISIONS-D3 "Dispatcher": carry=True swaps the weekly stop for OVERRUN_PCT."""
    from harness.dispatcher import usage_stop

    config = d3_config(tmp_path)
    ledger = usage_ledger(weekly=0.12)
    assert usage_stop(ledger, config, carry=True) == "carry leeway 10% reached"
    assert usage_stop(ledger, config, carry=False) is None
    assert usage_stop(usage_ledger(weekly=0.05), config, carry=True) is None


def test_B211_usage_stop_is_none_without_the_signal(tmp_path):
    """RUN-DECISIONS-D3 / B114: no decision may DEPEND on the signal — a ledger that never saw a
    rate_limit_event never trips the usage stop."""
    from harness.dispatcher import usage_stop

    config = d3_config(tmp_path)
    assert usage_stop(ledger_spent(0.0), config) is None
    assert usage_stop(ledger_spent(0.0), config, carry=True) is None


def test_B211_a_usage_stop_empties_the_plan_with_that_reason(tmp_path):
    """RUN-DECISIONS-D3 "Dispatcher": the usage stop sits between halt and reserve — past the
    weekly threshold nothing starts and the reason is the usage reason."""
    config = d3_config(tmp_path, RUN_WINDOW_START="", RUN_WINDOW_END="")
    result = run_plan(config, usage_ledger(weekly=0.91), cands(816, 823))
    assert result.start == ()
    assert result.reason == "weekly usage 91% >= 90%"
    assert json.loads(result.to_json())["reason"] == "weekly usage 91% >= 90%"


def test_B211_the_usage_stop_is_checked_before_the_reserve(tmp_path):
    """RUN-DECISIONS-D3 "Dispatcher": order — usage stop precedes reserve, so a run that is both
    out of dollars and out of allowance reports the allowance."""
    config = d3_config(tmp_path, RUN_WINDOW_START="", RUN_WINDOW_END="")
    result = run_plan(config, usage_ledger(weekly=0.91, spent_usd=23.10), cands(816))
    assert result.start == ()
    assert result.reason == "weekly usage 91% >= 90%"


def test_B211_the_rate_limit_is_still_checked_before_the_usage_stop(tmp_path):
    """RUN-DECISIONS-D3 "Dispatcher": the D2 steps keep their places — a live rate limit is
    still the first thing reported."""
    config = d3_config(tmp_path, RUN_WINDOW_START="", RUN_WINDOW_END="")
    ledger = usage_ledger(weekly=0.91)
    ledger.set_rate_limited("2026-09-02T13:00:00Z")
    result = run_plan(config, ledger, cands(816))
    assert result.reason == "rate limited until 2026-09-02T13:00:00Z"


def test_B211_halt_is_still_checked_before_the_usage_stop(tmp_path):
    """RUN-DECISIONS-D3 "Dispatcher": halted still wins over the usage stop."""
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


def test_B209_the_carry_item_still_obeys_the_dollar_reserve(tmp_path):
    """B209: the leeway is about allowance, not dollars — past the reserve line the plan is the
    D2 reserve plan, carry or no carry."""
    config = d3_config(tmp_path)
    ledger = usage_ledger(weekly=0.05, session=0.05, spent_usd=23.10, carry=816)

    result = run_plan(config, ledger, cands(816), now=WED)

    assert result.start == ()
    assert result.reason == "reserve"


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
    """B210 / RUN-DECISIONS-D3 "Config": both empty = always open — the Delivery 2 dispatcher
    behaviour, unchanged, for a house that does not want a window."""
    config = d3_config(tmp_path, RUN_WINDOW_START="", RUN_WINDOW_END="")

    for moment in (WED, MON_INSIDE, SUN_NOON, TUE_INSIDE):
        result = run_plan(config, usage_ledger(), cands(816), now=moment)
        assert result.start == (816,)
        assert "outside run window" not in result.reason


def test_B210_the_run_window_is_checked_after_the_reserve(tmp_path):
    """B210: order — reserve precedes the run window, so a drained week says "reserve" even
    outside the window."""
    config = d3_config(tmp_path)

    result = run_plan(config, usage_ledger(spent_usd=23.10), cands(816), now=WED)

    assert result.start == ()
    assert result.reason == "reserve"


# ---------------------------------------------------------------------------
# B211 — inside the window the D2 selection is unchanged, with a usage suffix
# ---------------------------------------------------------------------------

def test_B211_the_reason_keeps_the_d2_shape_and_appends_the_utilization(tmp_path):
    """B211: inside the window with usage under both thresholds the reason is the D2 budget
    reason with "; weekly 49%, session 7%" appended — percentages as integers."""
    config = d3_config(tmp_path)

    result = run_plan(config, usage_ledger(weekly=0.49, session=0.07), cands(816),
                      now=MON_INSIDE)

    assert result.start == (816,)
    assert result.reason.endswith("; weekly 49%, session 7%")
    prefix = result.reason.split(";")[0]
    assert BUDGET_REASON.match(prefix), result.reason
    assert BUDGET_REASON.match(prefix).group(1) == "1"


def test_B211_the_suffix_reports_the_latest_observation(tmp_path):
    """B211: the numbers are the last observed utilization, rounded to integers."""
    config = d3_config(tmp_path)

    result = run_plan(config, usage_ledger(weekly=0.85, session=0.66), cands(816),
                      now=MON_INSIDE)

    assert result.reason.endswith("; weekly 85%, session 66%")


def test_B211_without_the_signal_the_reason_is_exactly_the_d2_reason(tmp_path):
    """B211/B114: the suffix is appended only when usage is known — without the signal the D2
    reason is unchanged, character for character."""
    config = d3_config(tmp_path)

    result = run_plan(config, ledger_spent(0.0), cands(816), now=MON_INSIDE)

    assert result.start == (816,)
    assert BUDGET_REASON.match(result.reason), result.reason
    assert "weekly" not in result.reason
    assert "session" not in result.reason


def test_B211_inside_the_window_the_d2_selection_is_unchanged(tmp_path):
    """B211: unchanged D2 selection — oldest first, unmet dependencies skipped, slots enforced,
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


def test_B211_inside_the_window_the_estimate_rule_is_unchanged(tmp_path):
    """B211: the D2 dollar estimate still governs inside the window — a $2.50 implement does not
    fit $1.50 of remaining budget."""
    config = d3_config(tmp_path)

    result = run_plan(config, usage_ledger(spent_usd=21.00), cands(816), now=MON_INSIDE)

    assert result.start == ()
    assert result.skipped == {"816": "estimate $2.50 exceeds remaining $1.50"}
    assert result.reason.endswith("; weekly 49%, session 7%")


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
# in_run_window — the wrap cases (RUN-DECISIONS-D3 "Config")
# ---------------------------------------------------------------------------

def test_B210_in_run_window_inside_and_outside(tmp_path):
    """RUN-DECISIONS-D3 "Config": in_run_window(config, now) is pure and UTC — Monday 09:00 and
    Tuesday 19:00 are inside mon 08:00 → tue 20:00; Wednesday noon is not."""
    from harness.config import in_run_window

    config = d3_config(tmp_path)

    assert in_run_window(config, MON_INSIDE) is True
    assert in_run_window(config, TUE_INSIDE) is True
    assert in_run_window(config, WED) is False
    assert in_run_window(config, SUN_NOON) is False


def test_B210_in_run_window_wraps_past_sunday(tmp_path):
    """RUN-DECISIONS-D3 "Config": the window may wrap — sat 22:00 → mon 08:00 covers Saturday
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
    """RUN-DECISIONS-D3 "Config": both empty = always open."""
    from harness.config import in_run_window

    config = d3_config(tmp_path, RUN_WINDOW_START="", RUN_WINDOW_END="")

    for day in range(1, 8):
        moment = datetime(2026, 9, day, 3, 30, tzinfo=timezone.utc)
        assert in_run_window(config, moment) is True
