"""The pure dispatcher (handoff §6.4): a JSON plan from a ledger and candidates; starts nothing."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Collection, Sequence

from harness.clock import iso
from harness.config import Config
from harness.ledger import Ledger

__all__ = ["STATIC_USD", "Candidate", "Plan", "plan"]

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


def plan(
    *,
    now: datetime,
    ledger: Ledger,
    config: Config,
    candidates: Sequence[Candidate],
    merged: Collection[int],
    halted: bool,
) -> Plan:
    """Selection in the handoff §6.4 order. Pure: same inputs, byte-identical plan (A33)."""
    now_iso = iso(now)
    if ledger.rate_limited(now_iso):
        until = ledger.window.get("rate_limited_until")
        return Plan(start=(), reason=f"rate limited until {until}", skipped={})
    if halted:
        return Plan(start=(), reason="halted", skipped={})

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
    if max_slots < 1:
        max_slots = 1

    merged_ids = {int(number) for number in merged}
    ordered = sorted(candidates, key=lambda c: (c.created_at, int(c.issue)))

    start: list[int] = []
    skipped: dict[str, str] = {}
    for candidate in ordered:
        key = str(candidate.issue)
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

    pct = max(0.0, (1.0 - spent / weekly_cap) * 100.0) if weekly_cap > 0 else 0.0
    reason = f"budget {pct:.0f}% remaining, {len(start)} of max {max_slots} slots"
    return Plan(start=tuple(start), reason=reason, skipped=skipped)
