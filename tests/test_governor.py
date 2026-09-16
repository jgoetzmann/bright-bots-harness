"""B20, B206-B208, B421 and B431: the governor.

The clock is frozen at 2026-09-01T12:00:00Z, a Tuesday. Every ledger here starts its window on
the Monday before, 2026-08-31T00:00:00Z.
"""

from __future__ import annotations

import dataclasses
import itertools
from datetime import datetime, timezone
from pathlib import Path

import pytest

from harness.clock import FrozenClock
from harness.config import load_config
from harness.errors import BudgetExhausted
from harness.governor import Authorization, Governor
from harness.ledger import Ledger

PERIOD_START = "2026-08-31T00:00:00Z"

#: The live `harness-state` ledger as it stood before D74, copied verbatim.
PRE_D74 = Path(__file__).resolve().parent / "fixtures" / "ledger" / "pre_d74.json"


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
def ledger():
    """An empty Ledger whose window is the frozen clock's period."""
    return Ledger.empty(PERIOD_START)


@pytest.fixture
def governor(sample_config, frozen_clock, ledger):
    return Governor(sample_config, frozen_clock, ledger)


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
    frozen_clock, make_config, ledger, item_id
):
    """B20: the value is read from config, not from a constant in the governor."""
    config = make_config(MAX_TURNS_IMPLEMENT="17")
    governor = Governor(config, frozen_clock, ledger)

    assert governor.authorize(item_id, "implement").max_turns == 17


@pytest.mark.parametrize(
    ("stage", "expected_turns"),
    [("revise", 80), ("decompose", 30), ("deliver", 10), ("ask", 10), ("audit", 30),
     ("selfaudit", 30), ("selfaudit_fix", 80)],
)
def test_b20_a_stage_without_a_turns_key_borrows_the_nearest_one(
    governor, item_id, stage, expected_turns
):
    """B20: a stage with no MAX_TURNS_* key of its own takes the nearest stage's cap, so a
    complete `.env` needs no new key for it."""
    assert governor.authorize(item_id, stage).max_turns == expected_turns


def test_b20_the_authorization_id_encodes_item_stage_and_clock(governor, item_id, frozen_clock):
    """B20: Authorization.id is f"{work_item_id}:{stage}:{iso(now)}"."""
    from harness.clock import iso

    auth = governor.authorize(item_id, "propose")

    assert auth.id == f"{item_id}:propose:{iso(frozen_clock.now())}"


def test_b20_authorization_is_frozen(governor, item_id):
    """B20: Authorization is a frozen dataclass."""
    auth = governor.authorize(item_id, "propose")

    with pytest.raises(dataclasses.FrozenInstanceError):
        auth.max_turns = 1


# --------------------------------------------------------------------------
# The rate limit refuses, and a refusal writes nothing
# --------------------------------------------------------------------------


def test_s11_authorize_raises_while_rate_limited(governor, ledger, item_id):
    """B121: ledger.rate_limited(now) → BudgetExhausted."""
    ledger.set_rate_limited("2026-09-01T18:00:00Z")

    with pytest.raises(BudgetExhausted):
        governor.authorize(item_id, "discover")


def test_s11_authorize_raises_one_second_inside_the_rate_limit(governor, ledger, item_id):
    """The clock is the injected one — 12:00:00 < 12:00:01 is limited."""
    ledger.set_rate_limited("2026-09-01T12:00:01Z")

    with pytest.raises(BudgetExhausted):
        governor.authorize(item_id, "discover")


def test_s11_authorize_funds_once_the_rate_limit_has_passed(governor, ledger, item_id):
    """A reset time already behind the clock does not block."""
    ledger.set_rate_limited("2026-09-01T11:59:59Z")

    assert isinstance(governor.authorize(item_id, "discover"), Authorization)


def test_s11_authorize_funds_when_the_rate_limit_is_cleared(governor, ledger, item_id):
    """set_rate_limited(None) clears the block."""
    ledger.set_rate_limited("2026-09-01T18:00:00Z")
    ledger.set_rate_limited(None)

    assert isinstance(governor.authorize(item_id, "discover"), Authorization)


def test_s11_a_refused_authorize_leaves_the_ledger_and_the_store_untouched(
    usage_governor, d3_ledger, store, item_id
):
    """B19: a refusal writes nothing anywhere — no history, no call, no stage_run row."""
    observe(d3_ledger, weekly=0.91, session=0.10)
    before = d3_ledger.to_json()

    with pytest.raises(BudgetExhausted):
        usage_governor.authorize(item_id, "implement")

    assert d3_ledger.to_json() == before
    assert d3_ledger.window["calls"] == 0
    assert d3_ledger.history == []
    assert store.list_stage_runs(work_item_id=item_id) == []


def test_s11_a_rate_limited_refusal_leaves_the_ledger_untouched(governor, ledger, item_id):
    """Refusing on a rate limit does not touch the call count or the history."""
    ledger.set_rate_limited("2026-09-01T18:00:00Z")
    before = ledger.to_json()

    with pytest.raises(BudgetExhausted):
        governor.authorize(item_id, "implement")

    assert ledger.to_json() == before
    assert ledger.history == []


# --------------------------------------------------------------------------
# Record counts the call and appends one history entry
# --------------------------------------------------------------------------


def test_s11_record_counts_calls_and_appends_history(governor, ledger, item_id):
    """Each record bumps window.calls and appends one four-key entry."""
    first = governor.authorize(item_id, "implement")
    governor.record(first)
    second = governor.authorize(item_id, "propose")
    governor.record(second)

    assert ledger.window["calls"] == 2
    assert len(ledger.history) == 2
    assert ledger.history[0]["stage"] == "implement"
    assert ledger.history[-1]["stage"] == "propose"
    assert list(ledger.history[-1]) == ["ts", "stage", "issue", "run"]


def test_s11_governor_accepts_the_ledger_positionally(sample_config, frozen_clock, item_id):
    """Governor(config, clock, ledger) — the ledger is required and third."""
    led = Ledger.empty(PERIOD_START)
    led.set_rate_limited("2026-09-01T18:00:00Z")
    governor = Governor(sample_config, frozen_clock, led)

    with pytest.raises(BudgetExhausted):
        governor.authorize(item_id, "implement")


# --------------------------------------------------------------------------
# The usage stops (B206, B207, B208).
#
# The .env.example thresholds are weekly 90 %, session 70 %, carry leeway 10 %. The clock is
# frozen at 2026-09-01T12:00:00Z, inside the window starting 2026-08-31T00:00:00Z, so an
# observation stamped at that instant is fresh.
# --------------------------------------------------------------------------

# The five usage keys with their .env.example values (inline).
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


def d3_usage(*, weekly: float = 0.49, session: float = 0.07,
             seven_day_resets: str = D3_SEVEN_DAY_RESET) -> dict:
    """The usage shape; utilization is a fraction 0..1."""
    return {
        "five_hour": {"utilization": session, "resets_at": D3_FIVE_HOUR_RESET},
        "seven_day": {"utilization": weekly, "resets_at": seven_day_resets},
        "status": "allowed",
        "observed_at": FROZEN_NOW_ISO,
    }


@pytest.fixture
def make_d3_config(tmp_path, write_env):
    """Build a Config carrying every live key plus the five usage keys, each in its own dir."""
    counter = itertools.count()

    def _make(**overrides):
        directory = tmp_path / f"d3cfg{next(counter)}"
        values = {**D3_GOV_ENV, **overrides}
        return load_config(env_path=write_env(directory / ".env", **values), environ={})

    return _make


@pytest.fixture
def d3_config(make_d3_config):
    """The .env.example thresholds: weekly 90 %, session 70 %, leeway 10 %."""
    return make_d3_config()


@pytest.fixture
def d3_ledger():
    """An empty Ledger whose window is the frozen clock's period."""
    return Ledger.empty(PERIOD_START)


@pytest.fixture
def usage_governor(d3_config, frozen_clock, d3_ledger):
    return Governor(d3_config, frozen_clock, d3_ledger)


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
    """B206: the weekly rule comes first — with both past, the weekly one is the reason the
    operator sees."""
    observe(d3_ledger, weekly=0.95, session=0.85)

    assert usage_governor.usage_stop_reason() == "weekly usage 95% >= 90%"


def test_b206_the_configured_thresholds_appear_in_the_reason(make_d3_config, frozen_clock,
                                                             d3_ledger):
    """B206: the percentages come from the config, not from a constant — a house with an 85 %
    weekly stop and a 60 % session stop says so."""
    config = make_d3_config(WEEKLY_USAGE_STOP_PCT="85", SESSION_USAGE_STOP_PCT="60")
    governor = Governor(config, frozen_clock, d3_ledger)

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


def test_b206_the_carry_leeway_reason_names_the_configured_overrun(make_d3_config, frozen_clock,
                                                                   d3_ledger):
    """B206: the leeway percentage in the reason is OVERRUN_PCT, rounded to an integer."""
    config = make_d3_config(OVERRUN_PCT="25")
    governor = Governor(config, frozen_clock, d3_ledger)
    observe(d3_ledger, weekly=0.30, session=0.05)

    assert governor.usage_stop_reason(carry=True) == "carry leeway 25% reached"


def test_b206_a_carried_item_still_obeys_the_session_stop(usage_governor, d3_ledger):
    """B206: only the weekly rule changes for a carried item — the five-hour stop still applies,
    so a carry cannot burn through the session window."""
    observe(d3_ledger, weekly=0.05, session=0.72)

    assert usage_governor.usage_stop_reason(carry=True) == "session usage 72% >= 70%"


# --------------------------------------------------------------------------
# B207 - without the signal there is no usage stop
# --------------------------------------------------------------------------


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


def test_b207_record_forwards_the_usage_to_the_ledger(usage_governor, d3_ledger, item_id):
    """record(auth, *, usage=None) hands the usage to ledger.observe_usage, which is how the
    stop threshold ever fires."""
    auth = usage_governor.authorize(item_id, "implement")

    usage_governor.record(auth, usage=d3_usage(weekly=0.91))

    assert d3_ledger.weekly_utilization() == pytest.approx(0.91)
    assert d3_ledger.window["calls"] == 1
    assert usage_governor.usage_stop_reason() == "weekly usage 91% >= 90%"


def test_b207_record_without_usage_records_the_call_and_no_signal(
    usage_governor, d3_ledger, item_id
):
    """The kwarg defaults to None: the call is counted, the utilization stays unknown."""
    auth = usage_governor.authorize(item_id, "implement")

    usage_governor.record(auth)

    assert d3_ledger.window["calls"] == 1
    assert d3_ledger.weekly_utilization() is None
    assert usage_governor.usage_stop_reason() is None


# --------------------------------------------------------------------------
# B208 - authorize raises BudgetExhausted(reason) before any write
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
    """B208: the check runs BEFORE any write — no stage run is opened and the ledger JSON is
    untouched."""
    observe(d3_ledger, weekly=0.91, session=0.10)
    ledger_before = d3_ledger.to_json()
    runs_before = store.list_stage_runs(work_item_id=item_id)

    with pytest.raises(BudgetExhausted):
        usage_governor.authorize(item_id, "implement")

    assert store.list_stage_runs(work_item_id=item_id) == runs_before
    assert store.list_stage_runs(work_item_id=item_id) == []
    assert d3_ledger.to_json() == ledger_before
    assert d3_ledger.window["calls"] == 0


def test_b208_the_usage_stop_is_checked_before_the_rate_limit(
    usage_governor, d3_ledger, item_id
):
    """B208: with both the weekly stop and a stored rate limit live, the operator is told about
    the allowance rather than about a reset time."""
    observe(d3_ledger, weekly=0.91, session=0.10)
    d3_ledger.set_rate_limited("2026-09-01T18:00:00Z")

    with pytest.raises(BudgetExhausted) as excinfo:
        usage_governor.authorize(item_id, "implement")

    assert str(excinfo.value) == "weekly usage 91% >= 90%"


def test_b208_usage_under_the_thresholds_still_authorizes(usage_governor, d3_ledger, item_id):
    """B208: the stop is the exception, not the rule — 49 % weekly funds an implement."""
    observe(d3_ledger, weekly=0.49, session=0.07)

    auth = usage_governor.authorize(item_id, "implement")

    assert isinstance(auth, Authorization)
    assert auth.max_turns == 80


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


# --------------------------------------------------------------------------
# B101 — the audit link the ledger entry carries
# --------------------------------------------------------------------------


def test_b101_the_ledger_entry_records_an_empty_run_url(usage_governor, d3_ledger, item_id):
    """The transition comment has a ``run:`` slot, and ``ledger.record(run=...)`` fills the same
    slot in the history entry. Nothing in the harness produces a run URL — only ``config.py``
    may read the environment (I-4) and no config key carries one — so what the governor books is
    the empty string, plainly and on purpose."""
    auth = usage_governor.authorize(item_id, "implement")
    usage_governor.record(auth)

    entry = d3_ledger.history[-1]
    assert entry["run"] == ""
    assert entry["stage"] == "implement"
    assert list(entry) == ["ts", "stage", "issue", "run"]


def test_b101_the_governor_carries_no_run_url_field_for_a_caller_to_fill(usage_governor):
    """The mirror field is gone rather than left at "" for a reader to believe in: a URL that
    is coming would have to be wired at ``GitHubStore(run_url=...)`` — the frozen seam — and
    here, in the same change. Assigning this attribute alone would reach no comment."""
    assert not hasattr(usage_governor, "run_url")


# --------------------------------------------------------------------------
# D74 — admission is the usage stop and the rate limit, and nothing else
# --------------------------------------------------------------------------


def test_B421_admission_is_the_usage_stop_and_the_rate_limit_and_nothing_else(
    tmp_path, write_env, frozen_clock, item_id
):
    """B421 (D74): a pre-D74 ledger's spend total stops nothing, even with a one-cent weekly cap
    still written in `.env`. What refuses a call is the usage stop and the stored rate limit."""
    path = write_env(tmp_path / "d74" / ".env", WEEKLY_CAP_USD="0.01")
    config = load_config(env_path=path, environ={})

    spent_only = Ledger.from_json(PRE_D74.read_text(encoding="utf-8"))
    assert spent_only.window.get("spent_usd") is None
    spent_only.window.pop("usage", None)

    auth = Governor(config, frozen_clock, spent_only).authorize(item_id, "implement")
    assert [f.name for f in dataclasses.fields(auth)] == [
        "id", "work_item_id", "stage", "max_turns"
    ]

    # The reading that fixture carries is 100 % of the seven-day window, and it still refuses.
    observed = Ledger.from_json(PRE_D74.read_text(encoding="utf-8"))
    with pytest.raises(BudgetExhausted, match="weekly usage 100%"):
        Governor(config, frozen_clock, observed).authorize(item_id, "implement")

    limited = Ledger.empty(PERIOD_START)
    limited.set_rate_limited("2026-09-01T18:00:00Z")
    with pytest.raises(BudgetExhausted, match="rate limited until"):
        Governor(config, frozen_clock, limited).authorize(item_id, "implement")


def test_B431_record_observes_the_usage_before_it_counts_the_call(sample_config, item_id):
    """B431 (D74): observe_usage zeroes window.calls on a seven-day turnover, so the reading
    lands first and this call is counted into the window it opened."""
    led = Ledger.empty("2026-09-01T20:00:00Z")
    led.observe_usage(d3_usage(weekly=0.50, seven_day_resets=D3_SEVEN_DAY_RESET),
                      "2026-09-02T00:00:00Z")
    clock = FrozenClock(datetime(2026, 9, 8, 20, 0, 1, tzinfo=timezone.utc))
    governor = Governor(sample_config, clock, led)
    auth = Authorization(id="x", work_item_id=item_id, stage="discover", max_turns=10)

    governor.record(auth, usage=d3_usage(weekly=0.02, seven_day_resets="2026-09-15T20:00:00Z"))

    assert led.window["period_start"] == D3_SEVEN_DAY_RESET
    assert led.window["calls"] == 1
    assert [entry["stage"] for entry in led.history] == ["discover"]


def test_B16_B17_B18_B19_B21_B23_the_weekly_allowance_governor_is_retired(governor):
    """B16, B17, B18, B19, B21 and B23: the weekly percentage allowance, its period
    bookkeeping, the session allowance and the observed-median estimate went with the dollar
    machinery (D74). Each number is spelled out because the citation scan counts only the
    numbers a test names literally."""
    import harness.governor as governor_module
    from harness.store import Store

    for name in ("estimate", "can_fund", "spendable_pct", "remaining_weekly_pct",
                 "remaining_session_pct", "begin_session", "current_period",
                 "session_allocated", "session_consumed", "store"):
        assert not hasattr(governor, name), name
    for name in ("STATIC_ESTIMATES", "BUDGET_UNIT", "MIN_OBSERVATIONS", "STAGE_CAP_KEY",
                 "WEEKDAYS"):
        assert not hasattr(governor_module, name), name
    for name in ("ensure_budget_period", "budget_period", "consume_budget",
                 "completed_allowances"):
        assert not hasattr(Store, name), name
