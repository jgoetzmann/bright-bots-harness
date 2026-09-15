"""B295: what runs out is the subscription allowance, not a dollar budget.

Dollars are an estimate derived from token counts. What the harness reports is the utilization
of the five-hour and seven-day windows, and that allowance is shared with everything else the
same account does.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from harness import links, priority

CONFIG = SimpleNamespace(
    weekly_usage_stop_pct=90.0,
    session_usage_stop_pct=70.0,
    weekly_cap_usd=400.0,
    reserve_pct=10.0,
    audit_min_headroom_pct=75.0,
    suggest_min_headroom_pct=50.0,
)


def _ledger(*, weekly=None, session=None, spent=0.0, calls=0):
    usage = {}
    if weekly is not None:
        usage["seven_day"] = {"utilization": weekly}
    if session is not None:
        usage["five_hour"] = {"utilization": session}
    window = {"spent_usd": spent, "calls": calls}
    if usage:
        window["usage"] = usage
    return SimpleNamespace(window=window)


# --------------------------------------------------------------------------------------
# What the headline says
# --------------------------------------------------------------------------------------


def test_the_headline_leads_with_the_subscription_not_the_dollars():
    text = "\n".join(links.usage_headline(_ledger(weekly=0.18, session=0.01), CONFIG))

    assert text.startswith("**Allowance**")
    assert "$" not in text, "the headline reports the allowance, not an estimate of dollars"
    assert "18% used" in text


def test_the_headline_says_how_much_is_left_not_only_how_much_is_gone():
    """"18% used" is a fact; "72 points before the stop" is the one somebody can act on."""
    text = "\n".join(links.usage_headline(_ledger(weekly=0.18), CONFIG))

    assert "72 points" in text and "90% stop" in text


def test_the_headline_says_the_allowance_is_shared():
    """The reason 18% appeared after a single harness call. Without this line the number reads
    as the harness's own consumption and is wildly wrong."""
    text = "\n".join(links.usage_headline(_ledger(weekly=0.18), CONFIG))

    assert "shared" in text


def test_past_the_stop_it_says_nothing_will_start_rather_than_a_negative_number():
    text = "\n".join(links.usage_headline(_ledger(weekly=0.93), CONFIG))

    assert "at or past" in text and "nothing will start" in text
    assert "-3" not in text


def test_unmeasured_is_said_as_unmeasured_and_not_as_zero():
    """None is not zero. An unobserved allowance is unknown, and a headline reading "0% used"
    would be the most confident possible way of being wrong."""
    text = "\n".join(links.usage_headline(_ledger(), CONFIG))

    assert "not measured yet" in text
    assert "0%" not in text


def test_no_dollar_figure_is_reported_whatever_the_ledger_holds():
    """B295: a ledger carrying a spend still reports the allowance and no dollar figure."""
    for led in (_ledger(weekly=0.18, spent=0.2809, calls=1), _ledger(spent=12.5, calls=3)):
        text = "\n".join(links.usage_headline(led, CONFIG))

        assert "$" not in text and "dollar" not in text.lower()


def test_no_dollar_footer_is_offered_for_a_reply():
    """B295: the estimate rode under every status reply as a `<sub>` footer. Nothing bills it,
    and it is not what runs out, so there is no longer a renderer for it."""
    assert not hasattr(links, "spend_estimate")


# --------------------------------------------------------------------------------------
# The gate that follows from it
# --------------------------------------------------------------------------------------


def test_an_audit_is_refused_when_the_week_is_nearly_gone():
    """An audit is the longest single call the harness makes, and `AUDIT_CAP_USD` does not
    bound it on a subscription. Starting one with a fifth of the week left leaves the allowance
    gone the next time the operator needs it."""
    refused = priority.admit(
        "audit", store=None, ledger=_ledger(weekly=0.80), config=CONFIG
    )

    assert refused is not None
    assert "80%" in refused and "75%" in refused
    assert "shared" in refused


def test_an_audit_is_admitted_with_room_to_spare():
    assert priority.admit(
        "audit", store=None, ledger=_ledger(weekly=0.18), config=CONFIG
    ) is None


def test_an_unmeasured_allowance_does_not_refuse_an_audit():
    """B114/B207: unobserved is not "no headroom". The signal rides on the headers of a real
    call, so a fresh ledger, every tier-0 run and every local run have none — refusing here
    would disable the route entirely and fail closed on the wrong thing. The two usage stops
    protect the allowance, and they are checked on every call regardless."""
    assert priority.admit("audit", store=None, ledger=_ledger(), config=CONFIG) is None


def test_the_audit_floor_is_looser_than_the_suggestion_floor():
    """Somebody asked for the audit; nobody asked for suggested work. The one a person wants
    should survive a week the harness's own ideas do not."""
    assert CONFIG.audit_min_headroom_pct > CONFIG.suggest_min_headroom_pct


@pytest.mark.parametrize("cls", ["answer", "unblock", "directed"])
def test_work_a_person_asked_for_is_never_refused_here(cls):
    """Only the two classes with a written reason are gated. Everything else was asked for, and
    the governor is what decides whether there is allowance for it."""
    assert priority.admit(cls, store=None, ledger=_ledger(weekly=0.99), config=CONFIG) is None


# --------------------------------------------------------------------------------------
# Against a real ledger. The fakes above are a SimpleNamespace with no `period_start` and no
# `observed_at`, so they never reach the staleness guard `roll_window` makes necessary.
# --------------------------------------------------------------------------------------


def _rolled_ledger():
    """A fresh window carrying last window's reading — the case the guard exists for."""
    from harness.ledger import Ledger

    led = Ledger.empty("2026-09-07T00:00:00Z")
    led.observe_usage(
        {"seven_day": {"utilization": 0.88}, "five_hour": {"utilization": 0.63}},
        "2026-09-04T10:00:00Z",          # a Friday, before this window started
    )
    return led


def test_a_reading_from_before_the_window_is_not_reported_as_this_weeks():
    """88% on Friday, a Monday roll, and nothing spent since. Reporting "2 points before the
    stop" on a week with a full allowance also contradicts `harness dispatch`, which reads
    through the same ledger and starts work."""
    text = "\n".join(links.usage_headline(_rolled_ledger(), CONFIG))

    assert "not measured yet" in text
    assert "88" not in text and "2 points" not in text


def test_the_audit_gate_does_not_refuse_a_whole_fresh_window():
    """The same guard, on the reader B295 made the sole bound for `/harness audit`. Without it
    every audit is refused for an entire week in which nothing has been spent, until some other
    stage happens to make a real model call."""
    led = _rolled_ledger()

    assert priority.headroom_pct(led) is None
    assert priority.admit("audit", store=None, ledger=led, config=CONFIG) is None


def test_a_reading_from_inside_the_window_is_reported():
    """The other half: the guard must not swallow a live observation."""
    from harness.ledger import Ledger

    led = Ledger.empty("2026-09-07T00:00:00Z")
    led.observe_usage({"seven_day": {"utilization": 0.18}}, "2026-09-09T10:00:00Z")

    assert "18% used" in "\n".join(links.usage_headline(led, CONFIG))
    assert priority.headroom_pct(led) == pytest.approx(18.0)


def test_the_headline_agrees_with_the_ledgers_own_accessors():
    """The claim the docstring makes. Both readings come off the same guarded accessor, so a
    comment and `harness dispatch` cannot disagree about whether anything is measured at all."""
    for led in (_rolled_ledger(), _live_ledger()):
        measured = "not measured yet" not in "\n".join(links.usage_headline(led, CONFIG))
        assert measured is (led.weekly_utilization() is not None)


def _live_ledger():
    from harness.ledger import Ledger

    led = Ledger.empty("2026-09-07T00:00:00Z")
    led.observe_usage({"seven_day": {"utilization": 0.42}}, "2026-09-08T10:00:00Z")
    return led


def test_under_a_point_of_headroom_does_not_read_as_stopped():
    """`{:.0f}` of 0.4 is "0", and "0 points before the stop" reads as stopped while work in fact
    continues — the CLI, rendering one decimal, says "0.4 to go" on the same ledger."""
    text = "\n".join(links.usage_headline(_ledger(weekly=0.896), CONFIG))

    assert "under a point" in text
    assert "0 points" not in text


def test_a_declined_call_is_answered_as_a_decision_not_a_failure(tmp_path):
    """A usage stop is a normal outcome, like a closed run window, and is answered as a
    decision. `BudgetExhausted` is a `HarnessError`, so the generic failure branch would
    otherwise claim the command did not work (D3)."""
    import harness.__main__ as main_mod
    from harness.errors import BudgetExhausted

    from tests.test_d4_routes import _cmd, request_rig

    rig = request_rig(tmp_path)
    real = main_mod._act_on_command
    main_mod._act_on_command = lambda *a, **k: (_ for _ in ()).throw(
        BudgetExhausted("weekly subscription usage is 80%, at or above the 75% ceiling")
    )
    try:
        record, keep_going = main_mod.run_command(rig.ctx, rig.config, _cmd("audit"))
    finally:
        main_mod._act_on_command = real

    assert keep_going is True, "a decline is not a reason to abandon the batch"
    posted = rig.gh.comments_posted[-1][2]
    assert "Not now" in posted and "80%" in posted
    assert "did not work" not in posted
    assert record["result"].startswith("declined:")


def test_the_audit_stage_refuses_before_it_clones(tmp_path):
    """`run_model` is the last line of defence, but by the time it says no, a full fresh clone
    of the product repository has been made for a call about to be refused. The clone manager
    records every acquisition, so this asserts that none happened."""
    from harness.errors import BudgetExhausted
    from harness.stages.audit import audit

    from tests.test_d4_routes import request_rig

    rig = request_rig(tmp_path)
    # A live reading with no room left: observed inside the window, past the audit ceiling.
    rig.ctx.ledger.observe_usage(
        {"seven_day": {"utilization": 0.80}},
        rig.ctx.ledger.window.get("period_start") or "2026-09-08T00:00:00Z",
    )

    with pytest.raises(BudgetExhausted) as caught:
        audit(rig.ctx, lens="accessibility", actor="jgoetzmann")

    assert "75%" in str(caught.value)
    assert rig.ctx.clones.acquired == [], "it cloned the product repository to then refuse"
