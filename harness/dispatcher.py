"""The pure dispatcher: a JSON plan from a ledger and candidates; it starts nothing."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Collection, Sequence

from harness.clock import iso
from harness.config import Config, in_run_window, is_daily_window, run_window_label
from harness.ledger import Ledger

__all__ = ["Candidate", "Plan", "plan", "usage_stop"]


@dataclass(frozen=True)
class Candidate:
    issue: int
    depends_on: tuple[int, ...] = ()
    created_at: str = ""
    #: The operator asked for this item now, so the run window does not hold it back. The
    #: window governs when the harness chooses work on its own (B285).
    forced: bool = False
    #: Which of the five priority classes this belongs to (B287).
    cls: str = "directed"


@dataclass(frozen=True)
class Plan:
    start: tuple[int, ...]
    reason: str
    skipped: dict[str, str]

    def to_json(self) -> str:
        payload = {
            "start": [int(issue) for issue in self.start],
            "reason": self.reason,
            "skipped": {str(key): str(value) for key, value in self.skipped.items()},
        }
        return json.dumps(payload, indent=2, sort_keys=False)


def usage_stop(
    ledger: Ledger, config: Config, carry: bool = False, now: datetime | None = None
) -> str | None:
    """The usage stop for this ledger, or ``None`` when nothing observed stops work (B206).

    Pure, and the single implementation of the rule: ``Governor.usage_stop_reason`` delegates
    here, so the admission check and the plan cannot disagree. With no observation at all the
    answer is ``None``.

    ``carry=True`` is the item carried across a weekly reset. Outside a weekly run window it
    runs on the leeway, so it stops once weekly usage reaches ``OVERRUN_PCT``; inside the
    window, or under a daily one, it is held to the ordinary stops like any other item (D81).
    ``now`` expires an observation whose window has reset since, so both callers pass their
    clock; without it the window is unknown and the leeway applies.
    """
    weekly = ledger.weekly_utilization(now)
    session = ledger.session_utilization(now)
    if carry and now is not None:
        window_open = in_run_window(config, now) or ledger.block_open(now)
        carry = not window_open and not is_daily_window(config)
    if weekly is not None:
        if carry:
            leeway = float(config.overrun_pct)
            if weekly * 100.0 >= leeway:
                return f"carry leeway {leeway:.0f}% reached"
        else:
            limit = float(config.weekly_usage_stop_pct)
            if weekly * 100.0 >= limit:
                return f"weekly usage {weekly * 100:.0f}% >= {limit:.0f}%"
    if session is not None:
        limit = float(config.session_usage_stop_pct)
        if session * 100.0 >= limit:
            return f"session usage {session * 100:.0f}% >= {limit:.0f}%"
    return None


def _usage_suffix(ledger: Ledger) -> str:
    """``"; weekly 49%, session 7%"`` once both utilizations are known, else nothing (B211).

    The last readings as observed, without the expiry the stops above apply.
    """
    weekly = ledger.weekly_utilization()
    session = ledger.session_utilization()
    if weekly is None or session is None:
        return ""
    return f"; weekly {weekly * 100:.0f}%, session {session * 100:.0f}%"


def rank(cls: str) -> int:
    """The priority class's position, lowest first (D63). Imported lazily so this module keeps
    no import of `priority`, which reads a store."""
    from harness.priority import rank as _rank

    return _rank(cls)


def _window_reason(config: Config) -> str:
    """The reason text naming the configured run window (B210)."""
    return f"outside run window ({run_window_label(config)} UTC)"


def plan(
    *,
    now: datetime,
    ledger: Ledger,
    config: Config,
    candidates: Sequence[Candidate],
    merged: Collection[int],
    halted: bool,
    suggested_refused: str | None = None,
) -> Plan:
    """Select in order: rate limit, halted, commanded halt, carry, usage stop, run window,
    then candidates.

    ``suggested_refused`` is why ``priority.admit`` would refuse suggested work now, and each
    suggested candidate is skipped with it, so the plan never starts what admission refuses.
    Pure: the same inputs give a byte-identical plan.
    """
    now_iso = iso(now)
    if ledger.rate_limited(now_iso):
        until = ledger.window.get("rate_limited_until")
        return Plan(start=(), reason=f"rate limited until {until}", skipped={})
    if halted:
        return Plan(start=(), reason="halted", skipped={})
    # The commanded halt, so `harness dispatch` names who stopped the harness and why.
    commanded = ledger.halt_request()
    if commanded is not None:
        who = commanded.get("by", "someone")
        why = f": {commanded['reason']}" if commanded.get("reason") else ""
        return Plan(start=(), reason=f"halted by @{who}{why}", skipped={})

    # An item carried across a weekly reset resumes before anything else. It may run outside a
    # weekly run window, on the overrun leeway instead of the weekly stop, but not a daily one: a
    # daily window is the one subscription session a day, and a carry resuming outside it would
    # run in the operator's own daytime session (B413).
    # A block is the operator lending the harness sessions they do not need, so it opens the
    # window and nothing else (D77). Everything downstream inherits it: the carry test, the
    # early return below and the per-candidate skip all read `window_open`. It sits after the
    # usage stop deliberately, so a block can never outlive one.
    window_open = in_run_window(config, now) or ledger.block_open(now)
    carry_id = ledger.carry_issue()
    carry_ok = (
        carry_id is not None
        and (window_open or not is_daily_window(config))
        and usage_stop(ledger, config, carry=True, now=now) is None
    )

    stopped = usage_stop(ledger, config, now=now)
    if stopped is not None and not carry_ok:
        return Plan(start=(), reason=stopped, skipped={})

    max_slots = int(config.max_concurrent_items)
    if config.store_backend != "github":
        max_slots = 1

    merged_ids = {int(number) for number in merged}
    # Class first, then forced to the front of its own class, then oldest first: `forced`
    # breaks ties inside a class and never promotes across one (B289).
    ordered = sorted(
        candidates,
        key=lambda c: (rank(c.cls), 0 if c.forced else 1, c.created_at, int(c.issue)),
    )

    # Outside the window only the carry item and forced items may run (B210).
    forced_ids = {int(c.issue) for c in candidates if c.forced}
    if not window_open and not carry_ok and not forced_ids:
        return Plan(start=(), reason=_window_reason(config), skipped={})

    start: list[int] = []
    skipped: dict[str, str] = {}
    if carry_ok and carry_id is not None:
        start.append(int(carry_id))
    for candidate in ordered:
        key = str(candidate.issue)
        if carry_ok and int(candidate.issue) == carry_id:
            continue
        if not window_open and not candidate.forced:
            skipped[key] = "outside run window"
            continue
        if suggested_refused and candidate.cls == "suggested":
            skipped[key] = suggested_refused
            continue
        unmet = [int(dep) for dep in candidate.depends_on if int(dep) not in merged_ids]
        if unmet:
            skipped[key] = f"depends_on {unmet[0]} not merged"
            continue
        if len(start) >= max_slots:
            skipped[key] = "slots full"
            continue
        start.append(int(candidate.issue))

    # The observed utilizations are appended when they are known (B211). Kept in a local: an
    # f-string inlined into the Plan(...) call would be collected as a stop reason by
    # `tests/test_invariants.py` and then demand a classification it does not have.
    reason = f"{len(start)} of max {max_slots} slots{_usage_suffix(ledger)}"
    return Plan(start=tuple(start), reason=reason, skipped=skipped)
