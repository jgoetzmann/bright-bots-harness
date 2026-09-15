"""The pure dispatcher: a JSON plan from a ledger and candidates; it starts nothing."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Collection, Sequence

from harness.clock import iso
from harness.config import Config, in_run_window, is_daily_window, run_window_label
from harness.ledger import Ledger

__all__ = ["STATIC_USD", "Candidate", "Plan", "plan", "usage_stop", "estimate_usd"]

STATIC_USD: dict[str, float] = {
    "discover": 0.20,
    "propose": 0.50,
    "implement": 2.50,
    "revise": 1.00,
    "decompose": 0.30,
    "package": 0.05,
    "ask": 0.05,
    "audit": 3.00,
    "selfaudit": 0.50,
    "selfaudit_fix": 1.00,
}


@dataclass(frozen=True)
class Candidate:
    issue: int
    depends_on: tuple[int, ...] = ()
    stage: str = "implement"
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


def estimate_usd(ledger: Ledger, stage: str) -> float:
    """The observed median once three observations exist, else the static table."""
    observed = ledger.median_usd(stage)
    if observed:
        return float(observed)
    return float(STATIC_USD[stage])


def usage_stop(
    ledger: Ledger, config: Config, carry: bool = False, now: datetime | None = None
) -> str | None:
    """The usage stop for this ledger, or ``None`` when nothing observed stops work (B206).

    Pure, and the single implementation of the rule: ``Governor.usage_stop_reason`` delegates
    here, so the admission check and the plan cannot disagree. With no observation at all the
    answer is ``None`` and the USD path governs alone.

    ``carry=True`` is the item carried across a weekly reset: it may keep going until weekly
    usage reaches ``OVERRUN_PCT`` instead of ``WEEKLY_USAGE_STOP_PCT``.

    ``now`` expires an observation whose window has reset since: a 100% reading stops work
    until its ``resets_at``. Both callers pass their clock.
    """
    weekly = ledger.weekly_utilization(now)
    session = ledger.session_utilization(now)
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
) -> Plan:
    """Select in order: rate limit, halted, usage stop, reserve, run window, then candidates.

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

    # An item carried across a weekly reset resumes before anything else, on the overrun leeway
    # instead of the weekly stop. It may run outside a weekly run window but not a daily one: a
    # daily window is the one subscription session a day, and a carry resuming outside it would
    # run in the operator's own daytime session (B413).
    window_open = in_run_window(config, now)
    carry_id = ledger.carry_issue()
    carry_ok = (
        carry_id is not None
        and (window_open or not is_daily_window(config))
        and usage_stop(ledger, config, carry=True, now=now) is None
    )

    stopped = usage_stop(ledger, config, now=now)
    if stopped is not None and not carry_ok:
        return Plan(start=(), reason=stopped, skipped={})

    weekly_cap = float(config.weekly_cap_usd)
    reserve_pct = float(config.reserve_pct)
    spent = float(ledger.window.get("spent_usd", 0.0) or 0.0)
    ceiling = weekly_cap * (1.0 - reserve_pct / 100.0)
    if spent >= ceiling:
        # `implement.yml` matches the bare token "reserve", so the word stays (B122). The
        # subscription readings are appended once both windows have been observed.
        return Plan(start=(), reason="reserve" + _usage_suffix(ledger), skipped={})
    remaining = ceiling - spent

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
        unmet = [int(dep) for dep in candidate.depends_on if int(dep) not in merged_ids]
        if unmet:
            skipped[key] = f"depends_on {unmet[0]} not merged"
            continue
        usd = estimate_usd(ledger, candidate.stage)
        if usd > remaining:
            skipped[key] = f"estimate ${usd:.2f} exceeds remaining ${remaining:.2f}"
            continue
        if len(start) >= max_slots:
            skipped[key] = "slots full"
            continue
        start.append(int(candidate.issue))

    pct = remaining / weekly_cap * 100.0
    # The observed utilizations are appended when they are known (B211).
    reason = (
        f"budget {pct:.0f}% remaining, {len(start)} of max {max_slots} slots"
        f"{_usage_suffix(ledger)}"
    )
    return Plan(start=tuple(start), reason=reason, skipped=skipped)
