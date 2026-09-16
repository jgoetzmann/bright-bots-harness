"""One queue for every model call, and an order to it (D63).

The dispatcher governs which approved items may start. Every other model call, such as a
sweep-triggered `revise`, a `propose` from `discover.yml`, an `ask` or an `audit`, fires on its
own trigger, so without an order a busy morning of review comments would spend the session's
allowance before the run window's work began.

This module decides order. It never admits a call the governor would refuse (B291).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

__all__ = [
    "CLASSES",
    "CLASS_OF_STAGE",
    "OUTSTANDING_STATES",
    "Waiting",
    "class_of",
    "rank",
    "admit",
    "outstanding",
    "headroom_pct",
    "queue",
    "via_of",
]

#: Highest first; D63 says why each class sits where it does.
CLASSES: tuple[str, ...] = ("answer", "unblock", "directed", "audit", "suggested")

#: The class of a stage's call when nothing more specific applies. `propose` and `implement`
#: are absent: their class comes from why the item exists, which is the `via:` axis.
CLASS_OF_STAGE: dict[str, str] = {
    "ask": "answer",
    "revise": "unblock",
    "deliver": "unblock",
    "package": "unblock",
    # Both self-audit calls run after an item's gates are green. Classing them by stage keeps a
    # suggested item from being refused half-way; an unmapped stage falls through to `via` (D70).
    "selfaudit": "unblock",
    "selfaudit_fix": "unblock",
    "audit": "audit",
    "decompose": "directed",
    "discover": "directed",
}

#: How an item's `via:` maps onto a class.
CLASS_OF_VIA: dict[str, str] = {
    "assigned": "directed",
    "requested": "directed",
    "audit": "directed",
    "suggested": "suggested",
}

#: An item in any of these is work somebody is waiting on, and it blocks suggested work.
#: `proposed` and `shipped` count too: each means a pull request is open awaiting a human.
OUTSTANDING_STATES: tuple[str, ...] = (
    "discovered",
    "approved",
    "proposing",
    "proposed",
    "implementing",
    "implemented",
    "packaged",
    "revising",
    "shipped",
)


@dataclass(frozen=True)
class Waiting:
    """One thing in the queue, for the plan and for `harness dispatch` to print."""

    cls: str
    label: str
    since: str = ""
    forced: bool = False
    item_id: int | None = None
    note: str = ""

    def key(self) -> tuple:
        """Class first, forced to the front of its own class, then oldest first (B289)."""
        return (rank(self.cls), 0 if self.forced else 1, self.since, self.item_id or 0)


def rank(cls: str) -> int:
    """The class's position, lowest first; an unknown class sorts last."""
    try:
        return CLASSES.index(cls)
    except ValueError:
        return len(CLASSES)


def class_of(stage: str, *, via: str = "", state: str = "") -> str:
    """The class of one call. `stage` decides it unless the work's `via:` is more specific.

    A `propose` of a suggestion is class 4 and a `propose` of an assigned issue is class 2:
    urgency follows who is waiting for the call.
    """
    if stage in ("propose", "implement") or not stage:
        return CLASS_OF_VIA.get(str(via or "").strip(), "directed")
    named = CLASS_OF_STAGE.get(str(stage))
    if named is not None:
        return named
    return CLASS_OF_VIA.get(str(via or "").strip(), "directed")


def outstanding(store: Any) -> list[Any]:
    """Every work item somebody is waiting on. Empty is the precondition for suggested work."""
    items: list[Any] = []
    for state in OUTSTANDING_STATES:
        try:
            items.extend(store.list_work_items(state=state))
        except Exception:  # pragma: no cover - a store that cannot answer blocks suggestions
            return [object()]
    # Suggestions do not block each other, or the first one made would wedge the route that
    # makes them.
    return [item for item in items if not _is_suggested(item)]


def _is_suggested(item: Any) -> bool:
    return via_of(item) == "suggested"


def headroom_pct(ledger: Any, now: Any = None) -> float | None:
    """Weekly subscription usage as a percentage, or None when nothing has been observed.

    None means unknown and is never read as zero. Given ``now``, a reading whose seven-day
    window has reset since is unknown too, because it describes a week that is over (B399).
    """
    # Through the ledger's own accessor, which applies the staleness guard: the subscription's
    # own reset moves `period_start` and leaves the last observation in place, so a raw read
    # reports the previous week's figure and would refuse every audit in a fresh window (B295).
    accessor = getattr(ledger, "weekly_utilization", None)
    if callable(accessor):
        fraction = accessor() if now is None else accessor(now)
        return None if fraction is None else float(fraction) * 100.0
    window = getattr(ledger, "window", {}) or {}
    usage = window.get("usage")
    if not isinstance(usage, dict):
        return None
    observed_at, start = usage.get("observed_at"), window.get("period_start")
    if observed_at and start and str(observed_at) < str(start):
        return None
    # `seven_day` is the key the ledger stores and the one `runner/cli.py` reads: `USAGE_WINDOWS`
    # names the two windows, and "weekly" is not one of them. A wrong key here returns None
    # forever instead of raising, which silently unbinds the headroom half of B290.
    weekly = usage.get("seven_day")
    if not isinstance(weekly, dict):
        return None
    if now is not None and weekly.get("resets_at"):
        from harness.ledger import window_has_reset

        if window_has_reset(weekly, now):
            return None
    utilization = weekly.get("utilization")
    if utilization is None:
        return None
    try:
        return float(utilization) * 100.0
    except (TypeError, ValueError):
        return None


def admit(
    cls: str,
    *,
    store: Any,
    ledger: Any,
    config: Any,
    now: Any = None,
) -> str | None:
    """None when a call of this class may proceed, else the reason it may not (B290).

    ``now`` is the caller's clock, so a reading whose window has reset stops refusing (B399).

    Two classes are refused here. `suggested` is work nobody asked for, so it waits for an
    empty queue and for headroom. `audit` was asked for, but it is the longest single call the
    harness makes and it draws on the seven-day utilization this account shares with everything
    else, so it needs room measured in that unit.

    Every other class was asked for by a person and is bounded by the governor.
    """
    if cls == "audit":
        floor = float(getattr(config, "audit_min_headroom_pct", 75.0) or 0.0)
        used = headroom_pct(ledger, now)
        if used is not None and used >= floor:
            return (
                f"weekly subscription usage is {used:.0f}%, at or above the {floor:.0f}% ceiling "
                "for an audit — it is the longest single call the harness makes, and the "
                "allowance is shared. Ask again after the window resets, or narrow the lens"
            )
        return None
    if cls != "suggested":
        return None

    pending = outstanding(store)
    if pending:
        ids = sorted(int(getattr(i, "id", 0) or 0) for i in pending)[:5]
        named = ", ".join(f"#{n}" for n in ids if n) or f"{len(pending)} item(s)"
        return (
            f"work somebody asked for is still outstanding ({named}); suggested work runs "
            "only when the queue is empty"
        )

    limit = float(getattr(config, "suggest_min_headroom_pct", 50.0) or 0.0)
    used = headroom_pct(ledger, now)
    if used is None:
        # Unobserved means unknown, not "no headroom": the signal arrives with a real model
        # call, so a fresh ledger, a tier-0 run and every local run have none, and refusing
        # here would disable discovery. The two usage stops guard the allowance on every call.
        return None
    if used >= limit:
        return (
            f"weekly usage is {used:.0f}%, at or above the {limit:.0f}% ceiling for suggested "
            "work; the rest of the week is reserved for work somebody asked for"
        )
    return None


def queue(
    *,
    store: Any,
    ledger: Any,
    pending_asks: Sequence[str] = (),
    open_reviews: Sequence[Any] = (),
) -> list[Waiting]:
    """Everything waiting on a model call, in the order it should get one (B289/B292)."""
    rows: list[Waiting] = []
    forced = set(ledger.forced())

    for question in pending_asks:
        rows.append(Waiting(cls="answer", label=f"ask: {question}"))

    for item in open_reviews:
        number = int(getattr(item, "id", 0) or 0)
        rows.append(
            Waiting(
                cls="unblock",
                label=f"#{number} {getattr(item, 'title', '')}".strip(),
                since=str(getattr(item, "updated_at", "") or ""),
                forced=number in forced,
                item_id=number,
                note=str(getattr(item, "state", "") or ""),
            )
        )

    seen = {row.item_id for row in rows if row.item_id}
    for state in ("discovered", "approved", "proposing", "implementing"):
        for item in _items(store, state):
            number = int(getattr(item, "id", 0) or 0)
            if number in seen:
                continue
            seen.add(number)
            rows.append(
                Waiting(
                    cls=class_of("", via=via_of(item), state=state),
                    label=f"#{number} {getattr(item, 'title', '')}".strip(),
                    since=str(getattr(item, "created_at", "") or ""),
                    forced=number in forced,
                    item_id=number,
                    note=state,
                )
            )
    rows.sort(key=Waiting.key)
    return rows


def _items(store: Any, state: str) -> Iterable[Any]:
    try:
        return store.list_work_items(state=state)
    except Exception:  # pragma: no cover - a store that cannot answer contributes nothing
        return ()


def via_of(item: Any) -> str:
    """How this item came to exist: the `via:` axis, from the item's own label (D56).

    The reference is consulted only for an audit promotion, whose `audit:<issue>:<n>` shape
    says the same thing and is the one case where an older item may carry no label.
    """
    named = str(getattr(item, "via", "") or "").strip()
    if named in CLASS_OF_VIA:
        return named
    ref = str(getattr(item, "external_ref", "") or "")
    return "audit" if ref.startswith("audit:") else "requested"
