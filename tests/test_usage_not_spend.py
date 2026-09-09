"""B295: what runs out is the subscription, not a dollar budget.

The harness spent three deliveries reporting dollars. Dollars are an *estimate* derived from
token counts — nobody bills them — and on a subscription they are not the constraint. The first
live run made the gap plain: one model call, **$0.28** estimated, and the seven-day window at
**18%**. By the dollar figure the harness had used 0.08% of its allowance; by the real one,
nearly a fifth of the week.

And most of that 18% was not the harness at all. The allowance is shared with everything else
the same account does, which is the single most important fact about reading these numbers and
the one a dollar total hides completely.
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
    assert "$" not in text, "the dollar estimate belongs underneath, in smaller print"
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


def test_the_dollar_line_says_it_is_an_estimate_and_that_nobody_bills_it():
    """It is kept for three things only: a sense of scale, the `--max-budget-usd` flag the
    runner really does enforce per call, and being the only signal before a real call exists."""
    text = links.spend_estimate(_ledger(weekly=0.18, spent=0.2809, calls=1), CONFIG)

    assert "$0.28" in text
    assert "estimate" in text.lower() and "nobody bills it" in text
    assert "not what runs out" in text


def test_the_dollar_backstop_is_quoted_after_the_reserve_not_before():
    """Quoting the raw cap overstates what is available by exactly the reserve."""
    text = links.spend_estimate(_ledger(spent=0.0), CONFIG)

    assert "$360" in text and "$400" in text and "10% reserve" in text


# --------------------------------------------------------------------------------------
# The gate that follows from it
# --------------------------------------------------------------------------------------


def test_an_audit_is_refused_when_the_week_is_nearly_gone():
    """An audit is the longest single call the harness makes — twenty minutes — and it was
    bounded only by `AUDIT_CAP_USD`, a fiction on a subscription. Starting one with a fifth of
    the week left is how the operator finds the allowance gone the next time they need it."""
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
