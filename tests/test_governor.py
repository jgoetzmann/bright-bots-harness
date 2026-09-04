"""B16-B23. HARNESS-SPEC section 5.3 and RUN-DECISIONS "Governor".

The clock is frozen at 2026-09-01T12:00:00Z, a Tuesday. With
``weekly_reset_day=monday`` the period is 2026-08-31T00:00:00Z through
2026-09-07T00:00:00Z.
"""

from __future__ import annotations

import dataclasses
import itertools
from datetime import datetime, timezone

import pytest

from harness.config import load_config
from harness.errors import BudgetExhausted, ConfigError
from harness.governor import Authorization, Governor

PERIOD_START = "2026-08-31T00:00:00Z"
PERIOD_END = "2026-09-07T00:00:00Z"

# The static estimate table of section 5.3, as percentages of weekly allowance.
STATIC_ESTIMATES = {"discover": 0.5, "propose": 2.0, "implement": 8.0, "package": 0.5}

# The default budgets from RUN-DECISIONS: weekly 40, session 15, reserve 10.
# spendable = min(40, 15) - 40 * 10/100 = 11.0
DEFAULT_SPENDABLE = 11.0


@pytest.fixture
def make_config(tmp_path, write_env):
    """Build a Config with .env overrides, each in its own directory."""
    counter = itertools.count()

    def _make(**overrides):
        directory = tmp_path / f"cfg{next(counter)}"
        path = write_env(directory / ".env", **overrides)
        return load_config(env_path=path, environ={})

    return _make


@pytest.fixture
def item_id(store):
    """A work item the governor can authorize against."""
    return store.create_work_item(kind="issue", external_ref="issue:816", title="bundle size")


@pytest.fixture
def governor(store, sample_config, frozen_clock):
    return Governor(store, sample_config, frozen_clock)


def complete_run(store, item_id, stage, allowance_pct, *, status="ok"):
    """Record a finished stage_run so estimate() can observe it."""
    run_id = store.start_stage_run(item_id, stage, "fake")
    store.finish_stage_run(
        run_id,
        status=status,
        turns=5,
        allowance_pct=allowance_pct,
        cost_usd=None,
        exit_reason=None,
        transcript_path=None,
    )
    return run_id


# --------------------------------------------------------------------------
# B16 - current_period is bounded by weekly_reset_day, using the injected clock
# --------------------------------------------------------------------------


def test_b16_current_period_runs_from_the_most_recent_monday(governor):
    """B16: Tuesday 2026-09-01 sits in the week starting monday 2026-08-31."""
    assert governor.current_period() == (PERIOD_START, PERIOD_END)


def test_b16_the_reset_day_itself_is_inclusive(store, sample_config, frozen_clock):
    """B16: at 00:00 on the reset day the new period has already started."""
    frozen_clock.set(datetime(2026, 8, 31, 0, 0, 0, tzinfo=timezone.utc))
    governor = Governor(store, sample_config, frozen_clock)

    assert governor.current_period() == (PERIOD_START, PERIOD_END)


def test_b16_one_second_before_the_reset_day_is_the_previous_period(
    store, sample_config, frozen_clock
):
    """B16: 2026-08-30T23:59:59Z still belongs to the week starting 2026-08-24."""
    frozen_clock.set(datetime(2026, 8, 30, 23, 59, 59, tzinfo=timezone.utc))
    governor = Governor(store, sample_config, frozen_clock)

    assert governor.current_period() == ("2026-08-24T00:00:00Z", "2026-08-31T00:00:00Z")


def test_b16_a_different_reset_day_moves_the_boundary(store, frozen_clock, make_config):
    """B16: weekly_reset_day=thursday puts Tuesday 2026-09-01 in the 08-27 week."""
    config = make_config(WEEKLY_RESET_DAY="thursday")
    governor = Governor(store, config, frozen_clock)

    assert governor.current_period() == ("2026-08-27T00:00:00Z", "2026-09-03T00:00:00Z")


def test_b16_a_fresh_governor_has_the_whole_weekly_allowance(governor):
    """B16: with nothing consumed, weekly remaining is the configured allowance."""
    assert governor.remaining_weekly_pct() == pytest.approx(40.0)


# --------------------------------------------------------------------------
# B17 - crossing the reset boundary starts a fresh period
# --------------------------------------------------------------------------


def test_b17_crossing_the_boundary_resets_consumed_to_zero(governor, frozen_clock, store, item_id):
    """B17: spend in one week does not follow the harness into the next."""
    auth = governor.authorize(item_id, "discover")
    governor.record(auth, allowance_pct=3.0, cost_usd=None)
    assert governor.remaining_weekly_pct() == pytest.approx(37.0)

    frozen_clock.set(datetime(2026, 9, 7, 0, 0, 1, tzinfo=timezone.utc))

    assert governor.current_period() == ("2026-09-07T00:00:00Z", "2026-09-14T00:00:00Z")
    assert governor.remaining_weekly_pct() == pytest.approx(40.0)


def test_b17_the_previous_period_keeps_its_ledger(governor, frozen_clock, store, item_id):
    """B17: a fresh period is a new row, not an erased one."""
    auth = governor.authorize(item_id, "discover")
    governor.record(auth, allowance_pct=3.0, cost_usd=None)

    frozen_clock.set(datetime(2026, 9, 7, 0, 0, 1, tzinfo=timezone.utc))
    governor.remaining_weekly_pct()

    allocated, consumed = store.budget_period("allowance_pct", PERIOD_START)
    assert allocated == pytest.approx(40.0)
    assert consumed == pytest.approx(3.0)


def test_b17_a_partial_week_does_not_reset(governor, frozen_clock, item_id):
    """B17: advancing inside the period leaves consumed where it was."""
    auth = governor.authorize(item_id, "discover")
    governor.record(auth, allowance_pct=3.0, cost_usd=None)

    frozen_clock.advance(60 * 60 * 24 * 3)

    assert governor.current_period() == (PERIOD_START, PERIOD_END)
    assert governor.remaining_weekly_pct() == pytest.approx(37.0)


# --------------------------------------------------------------------------
# B18 - spendable_pct formula
# --------------------------------------------------------------------------


def test_b18_spendable_is_the_lower_remaining_minus_the_reserve(governor):
    """B18: min(weekly 40, session 15) - 40 * 10/100 = 11.0."""
    assert governor.remaining_session_pct() == pytest.approx(15.0)
    assert governor.spendable_pct() == pytest.approx(DEFAULT_SPENDABLE)


def test_b18_begin_session_narrows_the_session_side(governor):
    """B18: a smaller session allowance becomes the binding term."""
    governor.begin_session(5.0)

    assert governor.remaining_session_pct() == pytest.approx(5.0)
    assert governor.spendable_pct() == pytest.approx(1.0)


def test_b18_begin_session_with_no_argument_uses_the_configured_session_budget(governor):
    """B18: begin_session() defaults to config.session_budget_pct."""
    governor.begin_session(2.0)
    governor.begin_session()

    assert governor.remaining_session_pct() == pytest.approx(15.0)
    assert governor.spendable_pct() == pytest.approx(DEFAULT_SPENDABLE)


def test_b18_begin_session_clears_session_consumption(governor, item_id):
    """B18: a new session starts at zero consumed, weekly spend untouched."""
    auth = governor.authorize(item_id, "discover")
    governor.record(auth, allowance_pct=4.0, cost_usd=None)
    assert governor.remaining_session_pct() == pytest.approx(11.0)

    governor.begin_session()

    assert governor.remaining_session_pct() == pytest.approx(15.0)
    assert governor.remaining_weekly_pct() == pytest.approx(36.0)


def test_b18_the_reserve_is_a_fraction_of_the_weekly_allocation(store, frozen_clock, make_config):
    """B18: reserve is weekly_budget_pct * reserve_pct/100, not a flat percentage."""
    config = make_config(WEEKLY_BUDGET_PCT="40", SESSION_BUDGET_PCT="100", RESERVE_PCT="50")
    governor = Governor(store, config, frozen_clock)

    assert governor.spendable_pct() == pytest.approx(20.0)


def test_b18_spendable_can_go_negative_rather_than_clamping(governor, item_id):
    """B18: the formula is subtraction; a session smaller than the reserve reads below zero."""
    auth = governor.authorize(item_id, "discover")
    governor.record(auth, allowance_pct=0.5, cost_usd=None)
    governor.begin_session(1.0)

    assert governor.remaining_weekly_pct() == pytest.approx(39.5)
    assert governor.spendable_pct() == pytest.approx(-3.0)


# --------------------------------------------------------------------------
# B19 - authorize raises BudgetExhausted and records nothing
# --------------------------------------------------------------------------


def test_b19_authorize_raises_when_the_estimate_exceeds_spendable(governor, item_id):
    """B19: an unfundable stage is refused before any spend."""
    governor.begin_session(0.5)

    with pytest.raises(BudgetExhausted):
        governor.authorize(item_id, "implement")


def test_b19_a_refused_authorize_consumes_no_budget(governor, store, item_id):
    """B19: the refusal records nothing in the budget ledger."""
    governor.begin_session(0.5)
    before_weekly = governor.remaining_weekly_pct()

    with pytest.raises(BudgetExhausted):
        governor.authorize(item_id, "implement")

    assert governor.remaining_weekly_pct() == pytest.approx(before_weekly)
    assert store.budget_period("allowance_pct", PERIOD_START)[1] == pytest.approx(0.0)


def test_b19_a_refused_authorize_opens_no_stage_run(governor, store, item_id):
    """B19: no stage_run row is left behind by a refused authorization."""
    governor.begin_session(0.5)

    with pytest.raises(BudgetExhausted):
        governor.authorize(item_id, "implement")

    assert store.list_stage_runs(work_item_id=item_id) == []


def test_b19_a_refused_authorize_leaves_the_session_untouched(governor, item_id):
    """B19: a refusal does not quietly burn the session allowance either."""
    governor.begin_session(0.5)

    with pytest.raises(BudgetExhausted):
        governor.authorize(item_id, "implement")

    assert governor.remaining_session_pct() == pytest.approx(0.5)


def test_b19_can_fund_is_false_when_the_estimate_exceeds_spendable(governor):
    """B19: can_fund is the non-raising form of the same admission test."""
    governor.begin_session(0.5)

    assert governor.can_fund("implement") is False
    assert governor.can_fund("discover") is False


def test_b19_can_fund_is_true_for_an_affordable_stage(governor):
    """B19: with 11.0 spendable, implement (8.0) and discover (0.5) both fit."""
    assert governor.can_fund("implement") is True
    assert governor.can_fund("discover") is True


def test_b19_a_drained_week_refuses_even_with_session_left(
    store, frozen_clock, make_config, item_id
):
    """B19: the weekly ledger binds independently of the session allowance."""
    config = make_config(WEEKLY_BUDGET_PCT="10", SESSION_BUDGET_PCT="100", RESERVE_PCT="0")
    governor = Governor(store, config, frozen_clock)
    governor.record(governor.authorize(item_id, "discover"), allowance_pct=9.6, cost_usd=None)

    assert governor.remaining_session_pct() == pytest.approx(90.4)
    with pytest.raises(BudgetExhausted):
        governor.authorize(item_id, "implement")


def test_b19_the_reserve_is_not_spendable(store, frozen_clock, make_config, item_id):
    """B19: an untouched week still refuses a stage that would eat the reserve."""
    config = make_config(WEEKLY_BUDGET_PCT="10", SESSION_BUDGET_PCT="100", RESERVE_PCT="50")
    governor = Governor(store, config, frozen_clock)

    assert governor.remaining_weekly_pct() == pytest.approx(10.0)
    assert governor.spendable_pct() == pytest.approx(5.0)
    with pytest.raises(BudgetExhausted):
        governor.authorize(item_id, "implement")


@pytest.mark.parametrize("stage", ["discover", "propose", "implement", "package"])
def test_b19_no_stage_is_fundable_below_its_estimate(
    store, frozen_clock, make_config, item_id, stage
):
    """B19: with 0.1 spendable, every stage in the static table is refused."""
    config = make_config(WEEKLY_BUDGET_PCT="0.1", SESSION_BUDGET_PCT="100", RESERVE_PCT="0")
    governor = Governor(store, config, frozen_clock)

    assert governor.can_fund(stage) is False
    with pytest.raises(BudgetExhausted):
        governor.authorize(item_id, stage)


def test_b19_a_stage_exactly_at_the_spendable_limit_is_fundable(
    store, frozen_clock, make_config, item_id
):
    """B19: the rule is estimate > spendable, so an exact match still funds."""
    # weekly 8, session 100, reserve 0 -> spendable = 8.0 = the implement estimate.
    config = make_config(WEEKLY_BUDGET_PCT="8", SESSION_BUDGET_PCT="100", RESERVE_PCT="0")
    governor = Governor(store, config, frozen_clock)

    assert governor.spendable_pct() == pytest.approx(8.0)
    assert governor.can_fund("implement") is True
    assert isinstance(governor.authorize(item_id, "implement"), Authorization)


# --------------------------------------------------------------------------
# B20 - authorize returns max_turns from config
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stage", "expected_turns"),
    [("discover", 10), ("propose", 30), ("implement", 80), ("package", 10)],
)
def test_b20_authorize_carries_the_configured_max_turns(governor, item_id, stage, expected_turns):
    """B20: max_turns comes from config.max_turns[stage], per stage."""
    auth = governor.authorize(item_id, stage)

    assert auth.max_turns == expected_turns
    assert auth.stage == stage
    assert auth.work_item_id == item_id


def test_b20_a_changed_max_turns_reaches_the_authorization(
    store, frozen_clock, make_config, item_id
):
    """B20: the value is read from config, not from a constant in the governor."""
    config = make_config(MAX_TURNS_IMPLEMENT="17")
    governor = Governor(store, config, frozen_clock)

    assert governor.authorize(item_id, "implement").max_turns == 17


def test_b20_the_authorization_id_encodes_item_stage_and_clock(governor, item_id, frozen_clock):
    """B20: Authorization.id is f"{work_item_id}:{stage}:{iso(now)}" (RUN-DECISIONS)."""
    from harness.clock import iso

    auth = governor.authorize(item_id, "propose")

    assert auth.id == f"{item_id}:propose:{iso(frozen_clock.now())}"


def test_b20_authorization_is_frozen(governor, item_id):
    """B20: Authorization is a frozen dataclass (section 5.3)."""
    auth = governor.authorize(item_id, "propose")

    with pytest.raises(dataclasses.FrozenInstanceError):
        auth.max_turns = 1


# --------------------------------------------------------------------------
# B21 - record uses the observed amount, not the estimate
# --------------------------------------------------------------------------


def test_b21_record_consumes_the_observed_allowance(governor, store, item_id):
    """B21: authorizing implement estimates 8.0; observing 1.25 consumes 1.25."""
    auth = governor.authorize(item_id, "implement")

    governor.record(auth, allowance_pct=1.25, cost_usd=0.44)

    allocated, consumed = store.budget_period("allowance_pct", PERIOD_START)
    assert allocated == pytest.approx(40.0)
    assert consumed == pytest.approx(1.25)
    assert governor.remaining_weekly_pct() == pytest.approx(38.75)


def test_b21_record_charges_the_session_as_well_as_the_week(governor, item_id):
    """B21: the observed amount lands on both ledgers (RUN-DECISIONS)."""
    auth = governor.authorize(item_id, "implement")

    governor.record(auth, allowance_pct=1.25, cost_usd=None)

    assert governor.remaining_session_pct() == pytest.approx(13.75)


def test_b21_an_observed_overrun_beyond_the_estimate_is_recorded_in_full(
    governor, store, item_id
):
    """B21: a stage that costs more than estimated is charged what it cost."""
    auth = governor.authorize(item_id, "discover")

    governor.record(auth, allowance_pct=9.5, cost_usd=None)

    assert store.budget_period("allowance_pct", PERIOD_START)[1] == pytest.approx(9.5)


def test_b21_recording_a_none_allowance_consumes_nothing(governor, store, item_id):
    """B21: a None allowance records 0, not the estimate (RUN-DECISIONS)."""
    auth = governor.authorize(item_id, "implement")

    governor.record(auth, allowance_pct=None, cost_usd=None)

    assert store.budget_period("allowance_pct", PERIOD_START)[1] == pytest.approx(0.0)
    assert governor.remaining_weekly_pct() == pytest.approx(40.0)
    assert governor.remaining_session_pct() == pytest.approx(15.0)


def test_b21_records_accumulate_across_stages(governor, store, item_id):
    """B21: each recorded observation adds to the same period."""
    for stage, observed in (("discover", 0.4), ("propose", 1.6), ("implement", 6.0)):
        governor.record(governor.authorize(item_id, stage), allowance_pct=observed, cost_usd=None)

    assert store.budget_period("allowance_pct", PERIOD_START)[1] == pytest.approx(8.0)


# --------------------------------------------------------------------------
# B22 - max_concurrent_clones > 1 is a startup error
# --------------------------------------------------------------------------


@pytest.mark.parametrize("clones", [2, 3, 8])
def test_b22_governor_refuses_more_than_one_concurrent_clone(
    store, sample_config, frozen_clock, clones
):
    """B22: Delivery 1 is serial; the governor refuses to start otherwise."""
    config = dataclasses.replace(sample_config, max_concurrent_clones=clones)

    with pytest.raises(ConfigError):
        Governor(store, config, frozen_clock)


def test_b22_governor_accepts_exactly_one_concurrent_clone(store, sample_config, frozen_clock):
    """B22: one is legal and the governor constructs."""
    config = dataclasses.replace(sample_config, max_concurrent_clones=1)

    assert isinstance(Governor(store, config, frozen_clock), Governor)


# --------------------------------------------------------------------------
# B23 - estimate switches to the observed median after three completed runs
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("stage", "expected"), sorted(STATIC_ESTIMATES.items()))
def test_b23_estimate_starts_from_the_static_table(governor, stage, expected):
    """B23: with no history, estimate is the section 5.3 static value."""
    assert governor.estimate(stage) == pytest.approx(expected)


def test_b23_estimate_becomes_the_median_after_three_completed_runs(governor, store, item_id):
    """B23: three ok runs of 4.0, 6.0, 5.0 make the propose estimate 5.0, not 2.0."""
    for observed in (4.0, 6.0, 5.0):
        complete_run(store, item_id, "propose", observed)

    assert governor.estimate("propose") == pytest.approx(5.0)


def test_b23_two_completed_runs_are_not_enough(governor, store, item_id):
    """B23: the switch happens at three, so two observations keep the static 2.0."""
    for observed in (4.0, 6.0):
        complete_run(store, item_id, "propose", observed)

    assert governor.estimate("propose") == pytest.approx(2.0)


def test_b23_failed_and_unmeasured_runs_do_not_count(governor, store, item_id):
    """B23: only ok runs with a non-null allowance_pct feed the median."""
    complete_run(store, item_id, "propose", 4.0)
    complete_run(store, item_id, "propose", 6.0)
    complete_run(store, item_id, "propose", 100.0, status="failed")
    complete_run(store, item_id, "propose", None)

    assert sorted(store.completed_allowances("propose")) == [4.0, 6.0]
    assert governor.estimate("propose") == pytest.approx(2.0)


def test_b23_one_completed_run_does_not_shift_the_estimate(governor, store, item_id):
    """B23: a single outlier must not become the estimate."""
    complete_run(store, item_id, "propose", 9.0)

    assert governor.estimate("propose") == pytest.approx(2.0)


def test_b23_history_is_scoped_to_the_stage(governor, store, item_id):
    """B23: three implement runs must not move the propose estimate."""
    for observed in (11.0, 12.0, 13.0):
        complete_run(store, item_id, "implement", observed)

    assert governor.estimate("implement") == pytest.approx(12.0)
    assert governor.estimate("propose") == pytest.approx(2.0)


def test_b23_a_running_stage_run_does_not_count(governor, store, item_id):
    """B23: an in-flight run has no observation yet, so it cannot shift the estimate."""
    complete_run(store, item_id, "propose", 4.0)
    complete_run(store, item_id, "propose", 6.0)
    store.start_stage_run(item_id, "propose", "fake")

    assert governor.estimate("propose") == pytest.approx(2.0)


def test_b23_the_refined_estimate_drives_admission(governor, store, item_id):
    """B23: once the median says 20.0, propose stops being fundable at 11.0 spendable."""
    for observed in (19.0, 20.0, 21.0):
        complete_run(store, item_id, "propose", observed)

    assert governor.estimate("propose") == pytest.approx(20.0)
    assert governor.can_fund("propose") is False
    with pytest.raises(BudgetExhausted):
        governor.authorize(item_id, "propose")


# --------------------------------------------------------------------------
# Delivery 2 — RUN-DECISIONS-D2 §11 (the ledger-backed governor), handoff §6.
# Appended by the D2 spec-tester (T3); additions only (D2-R12.3).
#
# Cap 25.00 USD, reserve 10 % → the reserve line is 22.50. The static USD table of
# RUN-DECISIONS-D2 §5 gives implement 2.50, so spent 20.00 funds implement exactly and
# spent 20.01 refuses it.
# --------------------------------------------------------------------------

# RUN-DECISIONS-D2 §2 — the D2 keys (inline; duplicated on purpose).
D2_GOV_ENV: dict[str, str] = {
    "WEEKLY_CAP_USD": "25.00",
    "PER_CALL_CAP_USD": "3.00",
    "MAX_CONCURRENT_ITEMS": "1",
    "MAX_REVISE_CYCLES": "3",
    "FORK_REPO": "",
    "UPSTREAM_REPO": "Bright-Bots-Initiative/brightboost",
    "TRUST_FILE": ".harness/trust.txt",
    "NOTIFY_POLL_HOURS": "3",
    "MAX_SUBISSUES": "8",
    "SELF_REPO": "jgoetzmann/bright-bots-harness",
    "TRACKING_ISSUE": "",
    "STORE_BACKEND": "sqlite",
    "RESERVE_PCT": "10",
    "WEEKLY_USAGE_STOP_PCT": "90",
    "SESSION_USAGE_STOP_PCT": "70",
    "OVERRUN_PCT": "10",
    "RUN_WINDOW_START": "",
    "RUN_WINDOW_END": "",
}
# RUN-DECISIONS-D2 §5 — the static USD estimates, and their share of a 25.00 cap.
STATIC_USD = {
    "discover": 0.20,
    "propose": 0.50,
    "implement": 2.50,
    "revise": 1.00,
    "decompose": 0.30,
    "package": 0.05,
}
CAP_USD = 25.0
RESERVE_LINE_USD = 22.5


@pytest.fixture
def d2_config(tmp_path, write_env):
    """A Config carrying the D2 keys with the .env.example values (cap 25, reserve 10)."""
    path = write_env(tmp_path / "d2" / ".env", **D2_GOV_ENV)
    return load_config(env_path=path, environ={})


@pytest.fixture
def ledger():
    """An empty Ledger whose window is the frozen clock's period."""
    from harness.ledger import Ledger

    return Ledger.empty(PERIOD_START)


@pytest.fixture
def ledger_governor(store, d2_config, frozen_clock, ledger):
    return Governor(store, d2_config, frozen_clock, ledger=ledger)


def spend(ledger, usd: float) -> None:
    ledger.window["spent_usd"] = usd


# --------------------------------------------------------------------------
# §11 - authorize refuses past the reserve line and while rate limited
# --------------------------------------------------------------------------


def test_s11_authorize_raises_when_spent_plus_estimate_exceeds_the_reserve_line(
    ledger_governor, ledger, item_id
):
    """RUN-DECISIONS-D2 §11 (handoff §6.4): 20.50 + 2.50 > 22.50 → BudgetExhausted."""
    spend(ledger, 20.5)

    with pytest.raises(BudgetExhausted):
        ledger_governor.authorize(item_id, "implement")


def test_s11_authorize_funds_a_stage_exactly_at_the_reserve_line(ledger_governor, ledger, item_id):
    """RUN-DECISIONS-D2 §11: the rule is strictly greater — 20.00 + 2.50 == 22.50 still funds."""
    spend(ledger, 20.0)

    auth = ledger_governor.authorize(item_id, "implement")

    assert isinstance(auth, Authorization)
    assert auth.stage == "implement"
    assert auth.work_item_id == item_id


def test_s11_authorize_refuses_one_cent_past_the_reserve_line(ledger_governor, ledger, item_id):
    """RUN-DECISIONS-D2 §11: 20.01 + 2.50 > 22.50 → BudgetExhausted."""
    spend(ledger, 20.01)

    with pytest.raises(BudgetExhausted):
        ledger_governor.authorize(item_id, "implement")


@pytest.mark.parametrize("stage", sorted(STATIC_USD))
def test_s11_no_stage_is_fundable_once_the_window_reaches_the_reserve(
    ledger_governor, ledger, item_id, stage
):
    """RUN-DECISIONS-D2 §11 (handoff §6.4 step 3): at spent == reserve line every stage refuses."""
    spend(ledger, RESERVE_LINE_USD)

    assert ledger_governor.can_fund(stage) is False
    with pytest.raises(BudgetExhausted):
        ledger_governor.authorize(item_id, stage)


def test_s11_the_cheapest_stage_is_still_fundable_just_below_the_reserve(
    ledger_governor, ledger, item_id
):
    """RUN-DECISIONS-D2 §11: package (0.05) fits when 0.05 remains; implement (2.50) does not."""
    spend(ledger, RESERVE_LINE_USD - 0.05)

    assert isinstance(ledger_governor.authorize(item_id, "package"), Authorization)
    with pytest.raises(BudgetExhausted):
        ledger_governor.authorize(item_id, "implement")


def test_s11_authorize_raises_while_rate_limited(ledger_governor, ledger, item_id):
    """RUN-DECISIONS-D2 §11 / B121: ledger.rate_limited(now) → BudgetExhausted, even with the
    whole window unspent."""
    ledger.set_rate_limited("2026-09-01T18:00:00Z")

    with pytest.raises(BudgetExhausted):
        ledger_governor.authorize(item_id, "discover")


def test_s11_authorize_raises_one_second_inside_the_rate_limit(ledger_governor, ledger, item_id):
    """RUN-DECISIONS-D2 §11: the clock is the injected one — 12:00:00 < 12:00:01 is limited."""
    ledger.set_rate_limited("2026-09-01T12:00:01Z")

    with pytest.raises(BudgetExhausted):
        ledger_governor.authorize(item_id, "discover")


def test_s11_authorize_funds_once_the_rate_limit_has_passed(ledger_governor, ledger, item_id):
    """RUN-DECISIONS-D2 §11: a reset time already behind the clock does not block."""
    ledger.set_rate_limited("2026-09-01T11:59:59Z")

    assert isinstance(ledger_governor.authorize(item_id, "discover"), Authorization)


def test_s11_authorize_funds_when_the_rate_limit_is_cleared(ledger_governor, ledger, item_id):
    """RUN-DECISIONS-D2 §4/§11: set_rate_limited(None) clears the block."""
    ledger.set_rate_limited("2026-09-01T18:00:00Z")
    ledger.set_rate_limited(None)

    assert isinstance(ledger_governor.authorize(item_id, "discover"), Authorization)


def test_s11_a_refused_authorize_leaves_the_ledger_and_the_store_untouched(
    ledger_governor, ledger, store, item_id
):
    """RUN-DECISIONS-D2 §11 / B19: a refusal spends nothing anywhere."""
    spend(ledger, 21.0)
    calls_before = ledger.window["calls"]

    with pytest.raises(BudgetExhausted):
        ledger_governor.authorize(item_id, "implement")

    assert ledger.window["spent_usd"] == pytest.approx(21.0)
    assert ledger.window["calls"] == calls_before
    assert ledger.history == []
    assert store.list_stage_runs(work_item_id=item_id) == []


def test_s11_a_rate_limited_refusal_leaves_the_ledger_untouched(ledger_governor, ledger, item_id):
    """RUN-DECISIONS-D2 §11: refusing on a rate limit does not touch spend or history."""
    ledger.set_rate_limited("2026-09-01T18:00:00Z")

    with pytest.raises(BudgetExhausted):
        ledger_governor.authorize(item_id, "implement")

    assert ledger.window["spent_usd"] == pytest.approx(0.0)
    assert ledger.history == []


# --------------------------------------------------------------------------
# §11 - record adds cost_usd to the window (and still charges the D1 store)
# --------------------------------------------------------------------------


def test_s11_record_adds_cost_usd_to_the_window(ledger_governor, ledger, item_id):
    """RUN-DECISIONS-D2 §11 / B116: record(auth, ..., cost_usd=2.31) → window.spent_usd 2.31."""
    auth = ledger_governor.authorize(item_id, "implement")

    ledger_governor.record(auth, allowance_pct=None, cost_usd=2.31)

    assert ledger.window["spent_usd"] == pytest.approx(2.31)


def test_s11_record_accumulates_spend_counts_calls_and_appends_history(
    ledger_governor, ledger, item_id
):
    """RUN-DECISIONS-D2 §4, §11: each record adds to spent_usd, bumps calls, appends history."""
    first = ledger_governor.authorize(item_id, "implement")
    ledger_governor.record(first, allowance_pct=None, cost_usd=2.31)
    second = ledger_governor.authorize(item_id, "propose")
    ledger_governor.record(second, allowance_pct=None, cost_usd=0.5)

    assert ledger.window["spent_usd"] == pytest.approx(2.81)
    assert ledger.window["calls"] == 2
    assert len(ledger.history) == 2
    assert ledger.history[-1]["stage"] == "propose"
    assert ledger.history[-1]["usd"] == pytest.approx(0.5)
    assert ledger.history[0]["stage"] == "implement"
    assert ledger.history[0]["usd"] == pytest.approx(2.31)


def test_s11_record_with_no_cost_adds_zero(ledger_governor, ledger, item_id):
    """RUN-DECISIONS-D2 §11: cost_usd=None records 0.0 — never the estimate."""
    auth = ledger_governor.authorize(item_id, "implement")

    ledger_governor.record(auth, allowance_pct=None, cost_usd=None)

    assert ledger.window["spent_usd"] == pytest.approx(0.0)
    assert len(ledger.history) == 1
    assert ledger.history[0]["usd"] == pytest.approx(0.0)


def test_s11_record_still_charges_the_delivery_1_store_ledger(ledger_governor, store, item_id):
    """RUN-DECISIONS-D2 §11 / B21: the D1 allowance_pct path is kept alongside the ledger."""
    auth = ledger_governor.authorize(item_id, "implement")

    ledger_governor.record(auth, allowance_pct=1.25, cost_usd=0.44)

    assert store.budget_period("allowance_pct", PERIOD_START)[1] == pytest.approx(1.25)


def test_s11_recorded_spend_feeds_the_next_admission_decision(ledger_governor, ledger, item_id):
    """RUN-DECISIONS-D2 §11: what record() adds is what authorize() sees next time."""
    for _ in range(8):
        auth = ledger_governor.authorize(item_id, "implement")
        ledger_governor.record(auth, allowance_pct=None, cost_usd=2.5)
    assert ledger.window["spent_usd"] == pytest.approx(20.0)

    assert isinstance(ledger_governor.authorize(item_id, "implement"), Authorization)
    ledger_governor.record(
        ledger_governor.authorize(item_id, "implement"), allowance_pct=None, cost_usd=0.01
    )
    with pytest.raises(BudgetExhausted):
        ledger_governor.authorize(item_id, "implement")


# --------------------------------------------------------------------------
# §11 - Authorization.max_budget_usd is the per-call cap
# --------------------------------------------------------------------------


def test_s11_authorization_carries_per_call_cap_as_max_budget_usd(ledger_governor, d2_config, item_id):
    """RUN-DECISIONS-D2 §11 (handoff §6.1 "enforced twice"): max_budget_usd == per_call_cap_usd."""
    auth = ledger_governor.authorize(item_id, "implement")

    assert auth.max_budget_usd == pytest.approx(d2_config.per_call_cap_usd)
    assert auth.max_budget_usd == pytest.approx(3.0)


def test_s11_authorization_max_budget_usd_without_a_ledger(store, d2_config, frozen_clock, item_id):
    """RUN-DECISIONS-D2 §11: the field is on every Authorization, ledger or not."""
    governor = Governor(store, d2_config, frozen_clock)

    assert governor.authorize(item_id, "propose").max_budget_usd == pytest.approx(3.0)


def test_s11_max_budget_usd_tracks_the_configured_cap(store, frozen_clock, item_id, write_env, tmp_path):
    """RUN-DECISIONS-D2 §11: the value is read from config, not a constant."""
    path = write_env(tmp_path / "cap" / ".env", **{**D2_GOV_ENV, "PER_CALL_CAP_USD": "1.75"})
    config = load_config(env_path=path, environ={})
    governor = Governor(store, config, frozen_clock)

    assert governor.authorize(item_id, "propose").max_budget_usd == pytest.approx(1.75)


# --------------------------------------------------------------------------
# §11 - remaining_weekly_pct and estimate become spend-based with a ledger
# --------------------------------------------------------------------------


def test_s11_remaining_weekly_pct_is_derived_from_spend_over_cap(ledger_governor, ledger):
    """RUN-DECISIONS-D2 §11: (1 − spent/cap) × 100 — 5.00 of 25.00 leaves 80 %."""
    spend(ledger, 5.0)

    assert ledger_governor.remaining_weekly_pct() == pytest.approx(80.0)


def test_s11_remaining_weekly_pct_is_100_when_nothing_is_spent(ledger_governor):
    """RUN-DECISIONS-D2 §11: an untouched ledger is 100 %, not the D1 weekly_budget_pct."""
    assert ledger_governor.remaining_weekly_pct() == pytest.approx(100.0)


def test_s11_remaining_weekly_pct_clamps_at_zero(ledger_governor, ledger):
    """RUN-DECISIONS-D2 §11: overspend reads 0, never negative."""
    spend(ledger, 30.0)

    assert ledger_governor.remaining_weekly_pct() == pytest.approx(0.0)


@pytest.mark.parametrize(("stage", "usd"), sorted(STATIC_USD.items()))
def test_s11_estimate_starts_from_the_static_usd_table_as_a_share_of_the_cap(
    ledger_governor, stage, usd
):
    """RUN-DECISIONS-D2 §5, §11: estimate = STATIC_USD[stage] / weekly_cap_usd × 100."""
    assert ledger_governor.estimate(stage) == pytest.approx(usd / CAP_USD * 100.0)


def test_s11_estimate_uses_the_ledger_median_after_three_observations(
    ledger_governor, ledger, item_id
):
    """RUN-DECISIONS-D2 §4, §11 / B23: three implement records of 1.0, 2.0, 3.0 → median 2.0 →
    8 % of the cap, replacing the static 10 %."""
    for usd in (1.0, 2.0, 3.0):
        auth = ledger_governor.authorize(item_id, "implement")
        ledger_governor.record(auth, allowance_pct=None, cost_usd=usd)

    assert ledger.median_usd("implement") == pytest.approx(2.0)
    assert ledger_governor.estimate("implement") == pytest.approx(8.0)


def test_s11_two_observations_keep_the_static_estimate(ledger_governor, ledger, item_id):
    """RUN-DECISIONS-D2 §4: median_usd is None below three observations, so the static table holds."""
    for usd in (1.0, 3.0):
        auth = ledger_governor.authorize(item_id, "implement")
        ledger_governor.record(auth, allowance_pct=None, cost_usd=usd)

    assert ledger.median_usd("implement") is None
    assert ledger_governor.estimate("implement") == pytest.approx(10.0)


def test_s11_observations_are_scoped_to_the_stage(ledger_governor, item_id):
    """RUN-DECISIONS-D2 §11: three implement observations do not move the propose estimate."""
    for usd in (1.0, 2.0, 3.0):
        auth = ledger_governor.authorize(item_id, "implement")
        ledger_governor.record(auth, allowance_pct=None, cost_usd=usd)

    assert ledger_governor.estimate("propose") == pytest.approx(2.0)


def test_s11_the_observed_median_drives_admission(ledger_governor, ledger, item_id):
    """RUN-DECISIONS-D2 §11: with an observed implement median of 3.00, spent 20.00 refuses
    (20 + 3 > 22.5) where the static 2.50 would have funded."""
    for _ in range(3):
        auth = ledger_governor.authorize(item_id, "implement")
        ledger_governor.record(auth, allowance_pct=None, cost_usd=3.0)
    spend(ledger, 20.0)

    assert ledger_governor.can_fund("implement") is False
    with pytest.raises(BudgetExhausted):
        ledger_governor.authorize(item_id, "implement")


def test_s11_can_fund_is_the_non_raising_form_with_a_ledger(ledger_governor, ledger):
    """RUN-DECISIONS-D2 §11 / B19: can_fund mirrors authorize's admission test."""
    assert ledger_governor.can_fund("implement") is True
    spend(ledger, RESERVE_LINE_USD)
    assert ledger_governor.can_fund("implement") is False
    assert ledger_governor.can_fund("package") is False


def test_s11_governor_accepts_the_ledger_positionally(store, d2_config, frozen_clock, item_id):
    """RUN-DECISIONS-D2 §11: Governor(store, config, clock, ledger) — fourth positional."""
    from harness.ledger import Ledger

    ledger = Ledger.empty(PERIOD_START)
    ledger.window["spent_usd"] = RESERVE_LINE_USD
    governor = Governor(store, d2_config, frozen_clock, ledger)

    with pytest.raises(BudgetExhausted):
        governor.authorize(item_id, "implement")


# --------------------------------------------------------------------------
# §11 - without a ledger the Delivery 1 behaviour is untouched
# --------------------------------------------------------------------------


def test_s11_without_a_ledger_estimates_stay_the_d1_percentages(store, d2_config, frozen_clock):
    """RUN-DECISIONS-D2 §11: no ledger → D1 exactly — implement 8.0 %, propose 2.0 %."""
    governor = Governor(store, d2_config, frozen_clock)

    assert governor.estimate("implement") == pytest.approx(8.0)
    assert governor.estimate("propose") == pytest.approx(2.0)
    assert governor.estimate("discover") == pytest.approx(0.5)
    assert governor.estimate("package") == pytest.approx(0.5)


def test_s11_without_a_ledger_the_weekly_and_spendable_percentages_are_d1(
    store, d2_config, frozen_clock, item_id
):
    """RUN-DECISIONS-D2 §11 / B16, B18: weekly 40, session 15, reserve 10 → spendable 11.0."""
    governor = Governor(store, d2_config, frozen_clock)

    assert governor.remaining_weekly_pct() == pytest.approx(40.0)
    assert governor.spendable_pct() == pytest.approx(DEFAULT_SPENDABLE)
    assert isinstance(governor.authorize(item_id, "implement"), Authorization)


def test_s11_without_a_ledger_a_weekly_cap_in_usd_does_not_change_admission(
    store, frozen_clock, item_id, write_env, tmp_path
):
    """RUN-DECISIONS-D2 §11: WEEKLY_CAP_USD is read only through the ledger; without one the D1
    percentage ledger alone decides."""
    path = write_env(tmp_path / "tiny" / ".env", **{**D2_GOV_ENV, "WEEKLY_CAP_USD": "0.01"})
    config = load_config(env_path=path, environ={})
    governor = Governor(store, config, frozen_clock)

    assert governor.can_fund("implement") is True
    assert isinstance(governor.authorize(item_id, "implement"), Authorization)


# --------------------------------------------------------------------------
# Delivery 3 — RUN-DECISIONS-D3 "Governor" (B206, B207, B208). Appended by the D3
# spec-tester (T1); additions only.
#
# The .env.example thresholds are weekly 90 %, session 70 %, carry leeway 10 %. The clock is
# still frozen at 2026-09-01T12:00:00Z, inside the window starting 2026-08-31T00:00:00Z, so an
# observation stamped at that instant is fresh.
# --------------------------------------------------------------------------

# RUN-DECISIONS-D3 "Config" — the five new keys with their .env.example values (inline).
D3_GOV_ENV: dict[str, str] = {
    "WEEKLY_USAGE_STOP_PCT": "90",
    "SESSION_USAGE_STOP_PCT": "70",
    "OVERRUN_PCT": "10",
    "RUN_WINDOW_START": "mon 08:00",
    "RUN_WINDOW_END": "tue 20:00",
}
D3_SEVEN_DAY_RESET = "2026-09-08T20:00:00Z"
D3_FIVE_HOUR_RESET = "2026-09-04T11:00:00Z"
FROZEN_NOW_ISO = "2026-09-01T12:00:00Z"


def d3_usage(*, weekly: float = 0.49, session: float = 0.07) -> dict:
    """The RUN-DECISIONS-D3 usage shape; utilization is a fraction 0..1."""
    return {
        "five_hour": {"utilization": session, "resets_at": D3_FIVE_HOUR_RESET},
        "seven_day": {"utilization": weekly, "resets_at": D3_SEVEN_DAY_RESET},
        "status": "allowed",
        "observed_at": FROZEN_NOW_ISO,
    }


@pytest.fixture
def make_d3_config(tmp_path, write_env):
    """Build a Config carrying every D1, D2 and D3 key, each in its own directory."""
    counter = itertools.count()

    def _make(**overrides):
        directory = tmp_path / f"d3cfg{next(counter)}"
        values = {**D2_GOV_ENV, **D3_GOV_ENV, **overrides}
        return load_config(env_path=write_env(directory / ".env", **values), environ={})

    return _make


@pytest.fixture
def d3_config(make_d3_config):
    """The .env.example thresholds: weekly 90 %, session 70 %, leeway 10 %."""
    return make_d3_config()


@pytest.fixture
def d3_ledger():
    """An empty Ledger whose window is the frozen clock's period."""
    from harness.ledger import Ledger

    return Ledger.empty(PERIOD_START)


@pytest.fixture
def usage_governor(store, d3_config, frozen_clock, d3_ledger):
    return Governor(store, d3_config, frozen_clock, ledger=d3_ledger)


def observe(ledger, **kwargs) -> None:
    ledger.observe_usage(d3_usage(**kwargs), FROZEN_NOW_ISO)


# --------------------------------------------------------------------------
# B206 - the stop reasons, word for word, with the percentages as integers
# --------------------------------------------------------------------------


def test_b206_weekly_usage_at_or_past_the_stop_gives_the_exact_reason(usage_governor, d3_ledger):
    """B206: weekly >= WEEKLY_USAGE_STOP_PCT/100 → "weekly usage 91% >= 90%" — both
    percentages rounded to integers."""
    observe(d3_ledger, weekly=0.91, session=0.10)

    assert usage_governor.usage_stop_reason() == "weekly usage 91% >= 90%"


def test_b206_session_usage_at_or_past_the_stop_gives_the_exact_reason(usage_governor, d3_ledger):
    """B206: session >= SESSION_USAGE_STOP_PCT/100 → "session usage 72% >= 70%"."""
    observe(d3_ledger, weekly=0.10, session=0.72)

    assert usage_governor.usage_stop_reason() == "session usage 72% >= 70%"


def test_b206_usage_under_both_thresholds_is_no_stop(usage_governor, d3_ledger):
    """B206: the ordinary case — 49 % weekly and 7 % session stop nothing."""
    observe(d3_ledger, weekly=0.49, session=0.07)

    assert usage_governor.usage_stop_reason() is None
    assert usage_governor.usage_stop_reason(carry=False) is None


def test_b206_the_weekly_threshold_is_inclusive(usage_governor, d3_ledger):
    """B206: the comparison is >=, so exactly 90 % stops."""
    observe(d3_ledger, weekly=0.90, session=0.10)

    assert usage_governor.usage_stop_reason() == "weekly usage 90% >= 90%"


def test_b206_the_session_threshold_is_inclusive(usage_governor, d3_ledger):
    """B206: exactly 70 % session stops too."""
    observe(d3_ledger, weekly=0.10, session=0.70)

    assert usage_governor.usage_stop_reason() == "session usage 70% >= 70%"


def test_b206_one_point_below_the_weekly_threshold_does_not_stop(usage_governor, d3_ledger):
    """B206: 89 % is under 90 % — the run continues."""
    observe(d3_ledger, weekly=0.89, session=0.10)

    assert usage_governor.usage_stop_reason() is None


def test_b206_the_weekly_rule_is_reported_when_both_thresholds_are_past(
    usage_governor, d3_ledger
):
    """B206: RUN-DECISIONS-D3 lists the weekly rule first — with both past, the weekly one is
    the reason the operator sees."""
    observe(d3_ledger, weekly=0.95, session=0.85)

    assert usage_governor.usage_stop_reason() == "weekly usage 95% >= 90%"


def test_b206_the_configured_thresholds_appear_in_the_reason(make_d3_config, store,
                                                             frozen_clock, d3_ledger):
    """B206: the percentages come from the config, not from a constant — a house with an 85 %
    weekly stop and a 60 % session stop says so."""
    config = make_d3_config(WEEKLY_USAGE_STOP_PCT="85", SESSION_USAGE_STOP_PCT="60")
    governor = Governor(store, config, frozen_clock, ledger=d3_ledger)

    observe(d3_ledger, weekly=0.86, session=0.10)
    assert governor.usage_stop_reason() == "weekly usage 86% >= 85%"

    observe(d3_ledger, weekly=0.10, session=0.61)
    assert governor.usage_stop_reason() == "session usage 61% >= 60%"


def test_b206_the_percentages_are_rounded_to_integers(usage_governor, d3_ledger):
    """B206: a utilization of 0.9149 reads as 91 %, not 91.49 % — the reason is for a human."""
    observe(d3_ledger, weekly=0.9149, session=0.1234)

    assert usage_governor.usage_stop_reason() == "weekly usage 91% >= 90%"


def test_b206_carry_uses_the_overrun_leeway_instead_of_the_weekly_stop(
    usage_governor, d3_ledger
):
    """B206: with carry=True the weekly rule uses OVERRUN_PCT — at 12 % weekly the carried item
    is out of leeway while an ordinary item is nowhere near the 90 % stop."""
    observe(d3_ledger, weekly=0.12, session=0.10)

    assert usage_governor.usage_stop_reason(carry=True) == "carry leeway 10% reached"
    assert usage_governor.usage_stop_reason(carry=False) is None


def test_b206_carry_under_the_leeway_may_continue(usage_governor, d3_ledger):
    """B206: just after the weekly reset the carried item still has leeway — 5 % is under 10 %."""
    observe(d3_ledger, weekly=0.05, session=0.05)

    assert usage_governor.usage_stop_reason(carry=True) is None


def test_b206_the_carry_leeway_boundary_is_inclusive(usage_governor, d3_ledger):
    """B206: "until weekly usage reaches this" — at exactly 10 % the leeway is spent."""
    observe(d3_ledger, weekly=0.10, session=0.05)

    assert usage_governor.usage_stop_reason(carry=True) == "carry leeway 10% reached"


def test_b206_the_carry_leeway_reason_names_the_configured_overrun(make_d3_config, store,
                                                                   frozen_clock, d3_ledger):
    """B206: the leeway percentage in the reason is OVERRUN_PCT, rounded to an integer."""
    config = make_d3_config(OVERRUN_PCT="25")
    governor = Governor(store, config, frozen_clock, ledger=d3_ledger)
    observe(d3_ledger, weekly=0.30, session=0.05)

    assert governor.usage_stop_reason(carry=True) == "carry leeway 25% reached"


def test_b206_a_carried_item_still_obeys_the_session_stop(usage_governor, d3_ledger):
    """B206: only the weekly rule changes for a carried item — the five-hour stop still applies,
    so a carry cannot burn through the session window."""
    observe(d3_ledger, weekly=0.05, session=0.72)

    assert usage_governor.usage_stop_reason(carry=True) == "session usage 72% >= 70%"


# --------------------------------------------------------------------------
# B207 - without a ledger, or without the signal, there is no usage stop
# --------------------------------------------------------------------------


def test_b207_no_ledger_means_no_usage_stop(store, d3_config, frozen_clock):
    """B207: without a ledger the governor is the Delivery 1 governor — usage_stop_reason is
    None, whichever way it is asked."""
    governor = Governor(store, d3_config, frozen_clock)

    assert governor.usage_stop_reason() is None
    assert governor.usage_stop_reason(carry=True) is None


def test_b207_a_ledger_that_never_saw_the_signal_has_no_usage_stop(usage_governor):
    """B207/B114: usage=None → None. No decision may DEPEND on the signal being present."""
    assert usage_governor.usage_stop_reason() is None
    assert usage_governor.usage_stop_reason(carry=True) is None


def test_b207_an_observation_of_none_leaves_the_governor_without_a_stop(
    usage_governor, d3_ledger
):
    """B207: a call that reported no rate_limit_event does not create a stop out of nothing."""
    d3_ledger.observe_usage(None, FROZEN_NOW_ISO)

    assert usage_governor.usage_stop_reason() is None


def test_b207_a_stale_observation_is_not_a_usage_stop(usage_governor, d3_ledger, item_id):
    """B207/B204: a reading from before the window's start does not answer for this window, so
    it neither stops the run nor refuses authorization."""
    stale = d3_usage(weekly=0.99, session=0.99)
    stale["observed_at"] = "2026-08-25T00:00:00Z"
    d3_ledger.observe_usage(stale, "2026-08-25T00:00:00Z")

    assert usage_governor.usage_stop_reason() is None
    assert isinstance(usage_governor.authorize(item_id, "implement"), Authorization)


def test_b207_without_usage_the_usd_path_still_governs(usage_governor, d3_ledger, item_id):
    """B207: with usage=None the USD path (WEEKLY_CAP_USD/RESERVE_PCT) governs exactly as in
    Delivery 2 — spending past the reserve line still refuses."""
    assert usage_governor.usage_stop_reason() is None
    spend(d3_ledger, RESERVE_LINE_USD)

    with pytest.raises(BudgetExhausted):
        usage_governor.authorize(item_id, "implement")


def test_b207_record_forwards_the_usage_to_the_ledger(usage_governor, d3_ledger, item_id):
    """RUN-DECISIONS-D3 "Governor": record(auth, *, allowance_pct, cost_usd, usage=None) hands
    the usage to ledger.observe_usage, which is how the stop threshold ever fires."""
    auth = usage_governor.authorize(item_id, "implement")

    usage_governor.record(auth, allowance_pct=None, cost_usd=1.0, usage=d3_usage(weekly=0.91))

    assert d3_ledger.weekly_utilization() == pytest.approx(0.91)
    assert d3_ledger.window["spent_usd"] == pytest.approx(1.0)
    assert usage_governor.usage_stop_reason() == "weekly usage 91% >= 90%"


def test_b207_record_without_usage_records_the_spend_and_no_signal(
    usage_governor, d3_ledger, item_id
):
    """RUN-DECISIONS-D3 "Governor": the kwarg defaults to None and the D2 record path is
    unchanged — cost lands, utilization stays unknown."""
    auth = usage_governor.authorize(item_id, "implement")

    usage_governor.record(auth, allowance_pct=None, cost_usd=2.0)

    assert d3_ledger.window["spent_usd"] == pytest.approx(2.0)
    assert d3_ledger.weekly_utilization() is None
    assert usage_governor.usage_stop_reason() is None


# --------------------------------------------------------------------------
# B208 - authorize raises BudgetExhausted(reason) before any store write
# --------------------------------------------------------------------------


def test_b208_authorize_raises_with_the_usage_reason_as_its_message(
    usage_governor, d3_ledger, item_id
):
    """B208: authorize raises BudgetExhausted(reason) when usage_stop_reason() is not None, and
    the message is that reason word for word."""
    observe(d3_ledger, weekly=0.91, session=0.10)

    with pytest.raises(BudgetExhausted) as excinfo:
        usage_governor.authorize(item_id, "implement")

    assert str(excinfo.value) == "weekly usage 91% >= 90%"


def test_b208_the_session_stop_also_refuses_authorization(usage_governor, d3_ledger, item_id):
    """B208: the five-hour window stops work too."""
    observe(d3_ledger, weekly=0.10, session=0.72)

    with pytest.raises(BudgetExhausted) as excinfo:
        usage_governor.authorize(item_id, "implement")

    assert str(excinfo.value) == "session usage 72% >= 70%"


def test_b208_a_refused_authorize_writes_nothing_to_the_store_or_the_ledger(
    usage_governor, d3_ledger, store, item_id
):
    """B208: the check runs BEFORE any store write — no stage run is opened, no budget is
    consumed, and the ledger JSON is untouched."""
    observe(d3_ledger, weekly=0.91, session=0.10)
    ledger_before = d3_ledger.to_json()
    runs_before = store.list_stage_runs(work_item_id=item_id)

    with pytest.raises(BudgetExhausted):
        usage_governor.authorize(item_id, "implement")

    assert store.list_stage_runs(work_item_id=item_id) == runs_before
    assert store.list_stage_runs(work_item_id=item_id) == []
    assert d3_ledger.to_json() == ledger_before
    assert d3_ledger.window["calls"] == 0
    assert d3_ledger.window["spent_usd"] == 0.0


def test_b208_the_usage_stop_is_checked_before_the_usd_checks(
    usage_governor, d3_ledger, item_id
):
    """B208: the usage stop comes first — with both the reserve line and the weekly stop past,
    the operator is told about the usage, not about the dollars."""
    observe(d3_ledger, weekly=0.91, session=0.10)
    spend(d3_ledger, RESERVE_LINE_USD)

    with pytest.raises(BudgetExhausted) as excinfo:
        usage_governor.authorize(item_id, "implement")

    assert str(excinfo.value) == "weekly usage 91% >= 90%"


def test_b208_usage_under_the_thresholds_still_authorizes(usage_governor, d3_ledger, item_id):
    """B208: the stop is the exception, not the rule — 49 % weekly funds an implement."""
    observe(d3_ledger, weekly=0.49, session=0.07)

    auth = usage_governor.authorize(item_id, "implement")

    assert isinstance(auth, Authorization)
    assert auth.max_budget_usd == pytest.approx(3.0)


def test_b208_the_carried_item_is_judged_by_the_leeway(usage_governor, d3_ledger, store,
                                                       item_id):
    """B208: authorize uses carry=<work_item_id == ledger.carry_issue()> — at 12 % weekly an
    ordinary item is funded while the carried one is out of leeway."""
    observe(d3_ledger, weekly=0.12, session=0.10)
    other_id = store.create_work_item(kind="issue", external_ref="issue:823", title="other")

    assert isinstance(usage_governor.authorize(item_id, "implement"), Authorization)

    d3_ledger.set_carry(item_id, FROZEN_NOW_ISO, "weekly usage 91% >= 90%")

    with pytest.raises(BudgetExhausted) as excinfo:
        usage_governor.authorize(item_id, "implement")
    assert str(excinfo.value) == "carry leeway 10% reached"
    assert isinstance(usage_governor.authorize(other_id, "implement"), Authorization)


def test_b208_a_carried_item_inside_the_leeway_is_authorized(usage_governor, d3_ledger,
                                                             item_id):
    """B208: the point of the leeway — after the weekly reset the carried item continues."""
    observe(d3_ledger, weekly=0.03, session=0.05)
    d3_ledger.set_carry(item_id, FROZEN_NOW_ISO, "weekly usage 91% >= 90%")

    assert isinstance(usage_governor.authorize(item_id, "implement"), Authorization)


def test_b208_a_carried_item_at_the_weekly_stop_is_refused_by_the_leeway(
    usage_governor, d3_ledger, item_id
):
    """B208: the leeway is stricter than the weekly stop, never looser — at 91 % the carried
    item is refused for leeway, and nothing is written."""
    observe(d3_ledger, weekly=0.91, session=0.10)
    d3_ledger.set_carry(item_id, FROZEN_NOW_ISO, "weekly usage 91% >= 90%")

    with pytest.raises(BudgetExhausted) as excinfo:
        usage_governor.authorize(item_id, "implement")

    assert str(excinfo.value) == "carry leeway 10% reached"


def test_b208_without_a_ledger_authorize_is_the_delivery_1_path(store, d3_config, frozen_clock,
                                                                item_id):
    """B208/B207: no ledger, no usage stop — the D1 percentage governor decides alone."""
    governor = Governor(store, d3_config, frozen_clock)

    assert isinstance(governor.authorize(item_id, "implement"), Authorization)


# --------------------------------------------------------------------------
# B101 — the audit link the ledger entry carries. Governor.run_url was a field
# nothing ever assigned; the entry it produced was always the empty string.
# --------------------------------------------------------------------------


def test_b101_the_ledger_entry_records_an_empty_run_url(usage_governor, d3_ledger, item_id):
    """RUN-DECISIONS-D2 section 3 fixes the ``run:`` slot of the transition comment, and
    ``ledger.record(run=...)`` fills the same slot in the history entry. Nothing in the harness
    produces a run URL — only ``config.py`` may read the environment (I-4) and no config key
    carries one — so what the governor books is the empty string, plainly and on purpose."""
    auth = usage_governor.authorize(item_id, "implement")
    usage_governor.record(auth, allowance_pct=1.0, cost_usd=2.5)

    entry = d3_ledger.history[-1]
    assert entry["run"] == ""
    assert entry["stage"] == "implement"
    assert entry["usd"] == pytest.approx(2.5)


def test_b101_the_governor_carries_no_run_url_field_for_a_caller_to_fill(usage_governor):
    """The mirror field is gone rather than left at "" for a reader to believe in: a URL that
    is coming would have to be wired at ``GitHubStore(run_url=...)`` — the frozen seam — and
    here, in the same change. Assigning this attribute alone would reach no comment."""
    assert not hasattr(usage_governor, "run_url")
