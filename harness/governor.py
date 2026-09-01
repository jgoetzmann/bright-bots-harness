"""Budget periods, spend estimates and admission control.

No stage may spend without an :class:`Authorization` issued here. All persistence goes through
:class:`harness.store.Store`; this module contains no SQL (invariant I-5).
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from datetime import timedelta

from harness.clock import Clock, iso
from harness.config import Config
from harness.errors import BudgetExhausted, ConfigError
from harness.store import Store

log = logging.getLogger("harness")

BUDGET_UNIT = "allowance_pct"

WEEKDAYS: tuple[str, ...] = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

STATIC_ESTIMATES: dict[str, float] = {
    "discover": 0.5,
    "propose": 2.0,
    "implement": 8.0,
    "package": 0.5,
}

MIN_OBSERVATIONS = 3


@dataclass(frozen=True)
class Authorization:
    id: str
    work_item_id: int
    stage: str
    granted_pct: float
    max_turns: int


class Governor:
    def __init__(self, store: Store, config: Config, clock: Clock) -> None:
        if config.max_concurrent_clones > 1:
            raise ConfigError(
                "max_concurrent_clones must be 1 in delivery 1, got "
                f"{config.max_concurrent_clones}"
            )
        self.store = store
        self.config = config
        self.clock = clock
        self.session_allocated: float = float(config.session_budget_pct)
        self.session_consumed: float = 0.0

    # -- periods ---------------------------------------------------------------------------

    def current_period(self) -> tuple[str, str]:
        """The week bounded by ``config.weekly_reset_day``, as ``(start_iso, end_iso)``."""
        now = self.clock.now()
        day = str(self.config.weekly_reset_day).strip().lower()
        if day not in WEEKDAYS:
            raise ConfigError(f"weekly_reset_day must be a lowercase weekday name, got {day!r}")
        reset_index = WEEKDAYS.index(day)
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        back = (midnight.weekday() - reset_index) % 7
        start = midnight - timedelta(days=back)
        end = start + timedelta(days=7)
        return iso(start), iso(end)

    def _ensure_period(self) -> tuple[str, str]:
        start, end = self.current_period()
        self.store.ensure_budget_period(
            BUDGET_UNIT, start, end, float(self.config.weekly_budget_pct)
        )
        return start, end

    # -- remaining -------------------------------------------------------------------------

    def remaining_weekly_pct(self) -> float:
        start, _end = self._ensure_period()
        allocated, consumed = self.store.budget_period(BUDGET_UNIT, start)
        return float(allocated) - float(consumed)

    def remaining_session_pct(self) -> float:
        return float(self.session_allocated) - float(self.session_consumed)

    def spendable_pct(self) -> float:
        reserve = float(self.config.weekly_budget_pct) * float(self.config.reserve_pct) / 100.0
        return min(self.remaining_weekly_pct(), self.remaining_session_pct()) - reserve

    # -- estimates -------------------------------------------------------------------------

    def estimate(self, stage: str) -> float:
        observed = [float(v) for v in self.store.completed_allowances(stage)]
        if len(observed) >= MIN_OBSERVATIONS:
            return float(statistics.median(observed))
        if stage not in STATIC_ESTIMATES:
            raise ConfigError(f"unknown stage {stage!r}")
        return STATIC_ESTIMATES[stage]

    def can_fund(self, stage: str) -> bool:
        return self.estimate(stage) <= self.spendable_pct()

    # -- admission -------------------------------------------------------------------------

    def authorize(self, work_item_id: int, stage: str) -> Authorization:
        needed = self.estimate(stage)
        spendable = self.spendable_pct()
        if needed > spendable:
            raise BudgetExhausted(
                f"stage {stage!r} needs {needed:.3f}% but only {spendable:.3f}% is spendable"
            )
        max_turns = self.config.max_turns[stage]
        auth = Authorization(
            id=f"{work_item_id}:{stage}:{iso(self.clock.now())}",
            work_item_id=work_item_id,
            stage=stage,
            granted_pct=needed,
            max_turns=int(max_turns),
        )
        log.debug("authorized %s for %.3f%% (%d turns)", auth.id, needed, auth.max_turns)
        return auth

    def record(
        self, auth: Authorization, *, allowance_pct: float, cost_usd: float | None
    ) -> None:
        amount = 0.0 if allowance_pct is None else float(allowance_pct)
        start, _end = self._ensure_period()
        self.store.consume_budget(BUDGET_UNIT, start, amount)
        self.session_consumed += amount
        log.debug("recorded %.3f%% against %s (cost_usd=%s)", amount, auth.id, cost_usd)

    def begin_session(self, session_pct: float | None = None) -> None:
        if session_pct is None:
            self.session_allocated = float(self.config.session_budget_pct)
        else:
            self.session_allocated = float(session_pct)
        self.session_consumed = 0.0
