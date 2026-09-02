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
