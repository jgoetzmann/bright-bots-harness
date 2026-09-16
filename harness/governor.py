"""Admission control: the subscription usage stops, the stored rate limit, and the turn caps."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from harness.clock import Clock, iso
from harness.config import Config
from harness.dispatcher import usage_stop
from harness.errors import BudgetExhausted
from harness.ledger import Ledger

log = logging.getLogger("harness")

# Stages with no MAX_TURNS_* key of their own borrow the nearest stage's turn cap.
_TURNS_FALLBACK: dict[str, str] = {
    "revise": "implement",
    "decompose": "propose",
    "deliver": "package",
    "ask": "package",
    "audit": "propose",
    # Fallbacks, so a complete .env needs no new MAX_TURNS_* keys (D70).
    "selfaudit": "propose",
    "selfaudit_fix": "implement",
}


@dataclass(frozen=True)
class Authorization:
    id: str
    work_item_id: int
    stage: str
    max_turns: int


class Governor:
    """What may start a model call: the usage stops, the stored rate limit, the turn caps.

    The ledger is required and holds every fact admission reads (D74).
    """

    def __init__(self, config: Config, clock: Clock, ledger: Ledger) -> None:
        self.config = config
        self.clock = clock
        self.ledger = ledger

    # -- usage stops ------------------------------------------------------------------------

    def usage_stop_reason(self, *, carry: bool = False) -> str | None:
        """Why the subscription signal says to stop, or ``None`` (B206).

        ``None`` while nothing has been observed: unknown admits. ``carry=True`` is the item
        carried across a weekly reset, which runs on ``OVERRUN_PCT`` instead of
        ``WEEKLY_USAGE_STOP_PCT``. The rule itself lives in
        :func:`harness.dispatcher.usage_stop`, so admission and the plan cannot drift apart.
        """
        # Through the clock, so a reading whose window has reset no longer refuses (B399).
        return usage_stop(self.ledger, self.config, carry=carry, now=self.clock.now())

    def _is_carry(self, work_item_id: int) -> bool:
        carried = self.ledger.carry_issue()
        return carried is not None and int(carried) == int(work_item_id)

    # -- admission --------------------------------------------------------------------------

    def _max_turns(self, stage: str) -> int:
        turns = self.config.max_turns
        if stage in turns:
            return int(turns[stage])
        fallback = _TURNS_FALLBACK.get(stage)
        if fallback is not None and fallback in turns:
            return int(turns[fallback])
        return int(turns[stage])

    def authorize(self, work_item_id: int, stage: str) -> Authorization:
        """Admit one call, or raise :class:`BudgetExhausted` naming what refused it.

        The usage stop is checked before the rate limit, so an operator past the weekly stop
        hears about the allowance rather than about a reset time (B208).
        """
        stop = self.usage_stop_reason(carry=self._is_carry(work_item_id))
        if stop is not None:
            raise BudgetExhausted(stop)
        now_iso = iso(self.clock.now())
        if self.ledger.rate_limited(now_iso):
            until = self.ledger.window.get("rate_limited_until")
            raise BudgetExhausted(f"rate limited until {until}")
        auth = Authorization(
            id=f"{work_item_id}:{stage}:{now_iso}",
            work_item_id=work_item_id,
            stage=stage,
            max_turns=self._max_turns(stage),
        )
        log.debug("authorized %s (%d turns)", auth.id, auth.max_turns)
        return auth

    def record(self, auth: Authorization, *, usage: dict | None = None) -> None:
        """Book the call in the ledger.

        ``usage`` is the subscription signal the stage observed, if any; ``None`` records
        nothing and erases nothing.
        """
        now = iso(self.clock.now())
        # The reading lands first: observe_usage zeroes window["calls"] on a seven-day
        # turnover, so recording first would lose the call that produced it (B431).
        self.ledger.observe_usage(usage, now)
        # No config key carries the run URL and only config.py reads the environment (I-4), so
        # the entry's `run` is the empty string; `GitHubStore(run_url=...)` is the other end.
        self.ledger.record(ts=now, stage=auth.stage, issue=int(auth.work_item_id), run="")
        log.debug("recorded %s", auth.id)
