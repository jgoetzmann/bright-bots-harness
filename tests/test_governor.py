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
