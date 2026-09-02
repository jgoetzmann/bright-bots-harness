"""Budget periods, spend estimates and admission control (SPEC §5.3; handoff §6 with a ledger)."""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from datetime import timedelta

from harness.clock import Clock, iso
from harness.config import Config
from harness.dispatcher import STATIC_USD
from harness.errors import BudgetExhausted, ConfigError
from harness.ledger import Ledger
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

# Stages Delivery 1 had no MAX_TURNS_* key for borrow the nearest stage's turn cap.
_TURNS_FALLBACK: dict[str, str] = {
    "revise": "implement",
    "decompose": "propose",
    "deliver": "package",
}


@dataclass(frozen=True)
class Authorization:
    id: str
    work_item_id: int
    stage: str
    granted_pct: float
    max_turns: int
    max_budget_usd: float = 0.0


class Governor:
    def __init__(
        self, store: Store, config: Config, clock: Clock, ledger: Ledger | None = None
    ) -> None:
        if config.max_concurrent_clones > 1:
            raise ConfigError(
                "max_concurrent_clones must be 1 in delivery 1, got "
                f"{config.max_concurrent_clones}"
            )
        self.store = store
        self.config = config
        self.clock = clock
        self.ledger = ledger
        self.run_url: str = ""
        self.session_allocated: float = float(config.session_budget_pct)
        self.session_consumed: float = 0.0

    # -- periods ---------------------------------------------------------------------------

    def current_period(self) -> tuple[str, str]:
        """The week bounded by ``config.weekly_reset_day``, as ``(start_iso, end_iso)``."""
        now = self.clock.now()
        reset_index = WEEKDAYS.index(self.config.weekly_reset_day)
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

    def _ensure_window(self) -> Ledger:
        """Roll the ledger window when the week has passed; returns the ledger."""
        assert self.ledger is not None
        self.ledger.roll_window(self.clock.now(), self.config.weekly_reset_day)
        return self.ledger

    # -- USD figures (ledger path only) ----------------------------------------------------

    def _weekly_cap_usd(self) -> float:
        return float(self.config.weekly_cap_usd)

    def _spent_usd(self) -> float:
        ledger = self._ensure_window()
        return float(ledger.window.get("spent_usd", 0.0) or 0.0)

    def _spend_ceiling_usd(self) -> float:
        return self._weekly_cap_usd() * (1.0 - float(self.config.reserve_pct) / 100.0)

    def estimate_usd(self, stage: str) -> float:
        """The ledger median for ``stage`` once three observations exist, else the static USD."""
        assert self.ledger is not None
        observed = self.ledger.median_usd(stage)
        if observed:
            return float(observed)
        return float(STATIC_USD[stage])

    # -- remaining -------------------------------------------------------------------------

    def remaining_weekly_pct(self) -> float:
        if self.ledger is not None:
            cap = self._weekly_cap_usd()
            if cap <= 0:
                return 0.0
            return max(0.0, (1.0 - self._spent_usd() / cap) * 100.0)
        start, _end = self._ensure_period()
        allocated, consumed = self.store.budget_period(BUDGET_UNIT, start)
        return float(allocated) - float(consumed)

    def remaining_session_pct(self) -> float:
        return float(self.session_allocated) - float(self.session_consumed)

    def spendable_pct(self) -> float:
        if self.ledger is not None:
            reserve = float(self.config.reserve_pct)
            return min(self.remaining_weekly_pct(), self.remaining_session_pct()) - reserve
        reserve = float(self.config.weekly_budget_pct) * float(self.config.reserve_pct) / 100.0
        return min(self.remaining_weekly_pct(), self.remaining_session_pct()) - reserve

    # -- estimates -------------------------------------------------------------------------

    def estimate(self, stage: str) -> float:
        if self.ledger is not None:
            cap = self._weekly_cap_usd()
            if cap <= 0:
                return 0.0
            return self.estimate_usd(stage) / cap * 100.0
        observed = [float(v) for v in self.store.completed_allowances(stage)]
        if len(observed) >= MIN_OBSERVATIONS:
            return float(statistics.median(observed))
        return STATIC_ESTIMATES[stage]

    def can_fund(self, stage: str) -> bool:
        if self.ledger is not None:
            if self.ledger.rate_limited(iso(self.clock.now())):
                return False
            return self._spent_usd() + self.estimate_usd(stage) <= self._spend_ceiling_usd()
        return self.estimate(stage) <= self.spendable_pct()

    # -- admission -------------------------------------------------------------------------

    def _max_turns(self, stage: str) -> int:
        turns = self.config.max_turns
        if stage in turns:
            return int(turns[stage])
        fallback = _TURNS_FALLBACK.get(stage)
        if fallback is not None and fallback in turns:
            return int(turns[fallback])
        return int(turns[stage])

    def authorize(self, work_item_id: int, stage: str) -> Authorization:
        if self.ledger is not None:
            now_iso = iso(self.clock.now())
            if self.ledger.rate_limited(now_iso):
                until = self.ledger.window.get("rate_limited_until")
                raise BudgetExhausted(f"rate limited until {until}")
            needed_usd = self.estimate_usd(stage)
            spent = self._spent_usd()
            ceiling = self._spend_ceiling_usd()
            if spent + needed_usd > ceiling:
                raise BudgetExhausted(
                    f"stage {stage!r} needs ${needed_usd:.2f} but ${spent:.2f} of "
                    f"${ceiling:.2f} is already spent this window"
                )
            needed = self.estimate(stage)
        else:
            needed = self.estimate(stage)
            spendable = self.spendable_pct()
            if needed > spendable:
                raise BudgetExhausted(
                    f"stage {stage!r} needs {needed:.3f}% but only {spendable:.3f}% is spendable"
                )
        max_turns = self._max_turns(stage)
        auth = Authorization(
            id=f"{work_item_id}:{stage}:{iso(self.clock.now())}",
            work_item_id=work_item_id,
            stage=stage,
            granted_pct=needed,
            max_turns=int(max_turns),
            max_budget_usd=float(getattr(self.config, "per_call_cap_usd", 0.0) or 0.0),
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
        if self.ledger is not None:
            ledger = self._ensure_window()
            ledger.record(
                ts=iso(self.clock.now()),
                stage=auth.stage,
                issue=int(auth.work_item_id),
                usd=float(cost_usd or 0.0),
                run=self.run_url,
            )
        log.debug("recorded %.3f%% against %s (cost_usd=%s)", amount, auth.id, cost_usd)

    def begin_session(self, session_pct: float | None = None) -> None:
        if session_pct is None:
            self.session_allocated = float(self.config.session_budget_pct)
        else:
            self.session_allocated = float(session_pct)
        self.session_consumed = 0.0
