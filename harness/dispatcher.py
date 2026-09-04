"""The pure dispatcher (handoff §6.4): a JSON plan from a ledger and candidates; starts nothing."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Collection, Sequence

from harness.clock import iso
from harness.config import Config, in_run_window
from harness.ledger import Ledger

__all__ = ["STATIC_USD", "Candidate", "Plan", "plan", "usage_stop", "estimate_usd"]

STATIC_USD: dict[str, float] = {
    "discover": 0.20,
    "propose": 0.50,
    "implement": 2.50,
    "revise": 1.00,
    "decompose": 0.30,
    "package": 0.05,
}


@dataclass(frozen=True)
class Candidate:
    issue: int
    depends_on: tuple[int, ...] = ()
    stage: str = "implement"
    created_at: str = ""


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


def usage_stop(ledger: Ledger, config: Config, carry: bool = False) -> str | None:
    """The usage stop for this ledger, or ``None`` when nothing observed stops work (B206).

    Pure, and the single implementation of the rule: :meth:`harness.governor.Governor.
    usage_stop_reason` delegates here so the admission check and the plan can never disagree.
    With no observation at all the answer is ``None`` (B207) and the USD path governs alone —
    B114 survives as "no decision may DEPEND on the signal being present".

    ``carry=True`` is the item carried across a weekly reset: it may keep going until weekly
    usage reaches ``OVERRUN_PCT`` instead of ``WEEKLY_USAGE_STOP_PCT``.
    """
    weekly = ledger.weekly_utilization()
    session = ledger.session_utilization()
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
    """``"; weekly 49%, session 7%"`` once both utilizations are known, else nothing (B211)."""
    weekly = ledger.weekly_utilization()
    session = ledger.session_utilization()
    if weekly is None or session is None:
        return ""
    return f"; weekly {weekly * 100:.0f}%, session {session * 100:.0f}%"


def _window_reason(config: Config) -> str:
    """The exact B210 reason naming the configured window."""
    return f"outside run window ({config.run_window_start}-{config.run_window_end} UTC)"


def plan(
    *,
    now: datetime,
    ledger: Ledger,
    config: Config,
    candidates: Sequence[Candidate],
    merged: Collection[int],
    halted: bool,
) -> Plan:
    """Selection in the handoff §6.4 order, D3 order: rate limit -> halted -> usage stop ->
    reserve -> run window -> candidates. Pure: same inputs, byte-identical plan (A33)."""
    now_iso = iso(now)
    if ledger.rate_limited(now_iso):
        until = ledger.window.get("rate_limited_until")
        return Plan(start=(), reason=f"rate limited until {until}", skipped={})
    if halted:
        return Plan(start=(), reason="halted", skipped={})

    # B209: an item carried across a weekly reset resumes before anything else, on the
    # overrun leeway rather than the weekly stop, and even outside the run window.
    carry_id = ledger.carry_issue()
    carry_ok = carry_id is not None and usage_stop(ledger, config, carry=True) is None

    stopped = usage_stop(ledger, config)
    if stopped is not None and not carry_ok:
        return Plan(start=(), reason=stopped, skipped={})

    weekly_cap = float(config.weekly_cap_usd)
    reserve_pct = float(config.reserve_pct)
    spent = float(ledger.window.get("spent_usd", 0.0) or 0.0)
    ceiling = weekly_cap * (1.0 - reserve_pct / 100.0)
    if spent >= ceiling:
        return Plan(start=(), reason="reserve", skipped={})
    remaining = ceiling - spent

    max_slots = int(config.max_concurrent_items)
    if config.store_backend != "github":
        max_slots = 1

    merged_ids = {int(number) for number in merged}
    ordered = sorted(candidates, key=lambda c: (c.created_at, int(c.issue)))

    # B210: outside the window only the carry item may run; when nothing does, the plan says so.
    window_open = in_run_window(config, now)
    if not window_open and not carry_ok:
        return Plan(start=(), reason=_window_reason(config), skipped={})

    start: list[int] = []
    skipped: dict[str, str] = {}
    if carry_ok and carry_id is not None:
        start.append(int(carry_id))
    for candidate in ordered:
        key = str(candidate.issue)
        if carry_ok and int(candidate.issue) == carry_id:
            continue
        if not window_open:
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
    # B211: the D2 shape, with the observed utilizations appended when they are known.
    reason = (
        f"budget {pct:.0f}% remaining, {len(start)} of max {max_slots} slots"
        f"{_usage_suffix(ledger)}"
    )
    return Plan(start=tuple(start), reason=reason, skipped=skipped)
