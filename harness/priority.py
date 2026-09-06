"""One queue for every model call, and an order to it (B288-B292, D63).

Before this, the dispatcher governed exactly one thing: which *approved items* may start. Every
other model call — a sweep-triggered `revise`, a `propose` from `discover.yml`, and now `ask` and
`audit` — happened the moment its trigger fired, ordered by nothing but when the comment arrived.
A busy Monday of review comments could spend the session's allowance on revisions before the run
window's real work began, and the usage stops only found out afterwards. They are honest, but they
are a brake, not a steering wheel.

This module is the steering wheel. It decides *order*; it never decides that something may run
which the governor would have refused (B291). Those are different questions and they are answered
in different places on purpose.
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
    "render",
    "via_of",
]

#: Highest first. The order is the argument; see D63 for why each sits where it does.
CLASSES: tuple[str, ...] = ("answer", "unblock", "directed", "audit", "suggested")

#: Which class a stage's call belongs to when nothing else says otherwise. `propose` and
#: `implement` are absent deliberately: their class comes from *why* the item exists, which is
#: the `via:` axis, not from the stage.
CLASS_OF_STAGE: dict[str, str] = {
    "ask": "answer",
    "revise": "unblock",
    "deliver": "unblock",
    "package": "unblock",
    "audit": "audit",
    "decompose": "directed",
    "discover": "directed",
}

#: How an item's `via:` maps onto a class once it is real work rather than a stage.
CLASS_OF_VIA: dict[str, str] = {
    "assigned": "directed",
    "requested": "directed",
    "audit": "directed",
    "suggested": "suggested",
}

#: An item in any of these is work somebody is waiting on, and it blocks suggested work.
#: `proposed` and `shipped` are here too: both mean a pull request is open awaiting a human,
#: which is precisely the state in which the harness should not be inventing more work.
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
        """B287/B289: class first, forced to the front of its own class, then oldest first."""
        return (rank(self.cls), 0 if self.forced else 1, self.since, self.item_id or 0)


def rank(cls: str) -> int:
    """The class's position, lowest first. An unknown class sorts last rather than crashing."""
    try:
        return CLASSES.index(cls)
    except ValueError:
        return len(CLASSES)


def class_of(stage: str, *, via: str = "", state: str = "") -> str:
    """The class of one call. `stage` decides it unless the work's `via:` is more specific.

    A `propose` of a suggestion is class 4 and a `propose` of an assigned issue is class 2 — the
    same stage, a different queue — because what makes a call urgent is who is waiting for it,
    not which function makes it.
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
    # A suggestion already in the queue must not block the next one, or the first suggestion
    # ever made would wedge the route that made it.
    return [item for item in items if not _is_suggested(item)]


def _is_suggested(item: Any) -> bool:
    return via_of(item) == "suggested"


def headroom_pct(ledger: Any) -> float | None:
    """Weekly subscription usage as a percentage, or None when nothing has been observed.

    None is not zero. An unobserved allowance is unknown, and treating unknown as "plenty left"
    is how a system spends a week it did not have.
    """
    window = getattr(ledger, "window", {}) or {}
    usage = window.get("usage")
    if not isinstance(usage, dict):
        return None
    weekly = usage.get("weekly")
    if not isinstance(weekly, dict):
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
) -> str | None:
    """None when a call of this class may proceed, else the reason it may not (B290).

    Only `suggested` is ever refused here. Everything else was asked for by a person, and the
    governor is what decides whether there is allowance for it — this function's job is to stop
    the harness spending on its own ideas while somebody's actual request is waiting.
    """
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
    used = headroom_pct(ledger)
    if used is None:
        # Unobserved is not "no headroom". The signal arrives on the headers of a real model
        # call, so a fresh ledger, a Tier 0 run and every local run have none -- refusing here
        # would silently disable the discovery route that has worked since Delivery 1, and it
        # would fail closed on the wrong thing. The two usage stops are what protect the
        # allowance, and they are checked after this, on every call.
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
    """How this item came to exist: the `via:` axis of D56, from the item's own label.

    The reference is consulted only for an audit promotion, whose `audit:<issue>:<n>` shape says
    the same thing and is the one case where an older item may carry no label.
    """
    named = str(getattr(item, "via", "") or "").strip()
    if named in CLASS_OF_VIA:
        return named
    ref = str(getattr(item, "external_ref", "") or "")
    return "audit" if ref.startswith("audit:") else "requested"


def render(rows: Sequence[Waiting], *, head_reason: str = "") -> list[str]:
    """The queue as lines for `harness dispatch` (B292).

    "Why has my question not been answered" and "why is it proposing things nobody asked for"
    should be one command with an answer, which means printing the order and not only the pick.
    """
    if not rows:
        return ["queue: empty"]
    lines = [f"queue: {len(rows)} waiting, highest priority first"]
    for index, row in enumerate(rows):
        mark = " [forced]" if row.forced else ""
        note = f" ({row.note})" if row.note else ""
        lines.append(f"  {rank(row.cls)}. {row.cls:<9} {row.label}{note}{mark}")
        if index == 0 and head_reason:
            lines.append(f"       -> {head_reason}")
    return lines
