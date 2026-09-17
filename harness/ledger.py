"""The call ledger: one JSON file, an append-only history, rebuildable from comments (B117)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from harness.clock import as_utc, iso, parse_iso
from harness.errors import HarnessError
from harness.redact import guarded_write

__all__ = [
    "Ledger",
    "load",
    "save",
    "rebuild",
    "block_until",
    "HISTORY_CAP",
    "EPOCH",
    "MAX_BLOCK_SESSIONS",
    "SESSION_HOURS",
    "USAGE_WINDOWS",
]

SCHEMA = 1
HISTORY_CAP = 500
EPOCH = "1970-01-01T00:00:00Z"

#: One subscription session, in hours. The API reports a `five_hour` window, and a block is
#: counted in those sessions because that is the unit the allowance actually refills in (D77).
SESSION_HOURS = 5

#: The most sessions one `/harness block` may cover: thirty hours. Anything longer is a change
#: to when the harness works, which is `RUN_WINDOW_*` in a reviewed pull request, not a comment.
MAX_BLOCK_SESSIONS = 6

# B101 comment shape: ``**harness** `stage` -> `state`\nrun: URL``, optionally followed by the
# ``cost:`` line older comments carry. The arrow the store writes is U+2192; ``->`` is accepted.
_TRANSITION_COMMENT = re.compile(
    r"\*\*harness\*\*\s+`(?P<stage>[^`]+)`\s*(?:→|->)\s*`(?P<state>[^`]+)`"
    r"[ \t]*\r?\n[ \t]*run:[ \t]*(?P<run>[^\r\n]*)"
    r"(?:\r?\n[ \t]*cost:[ \t]*\$[0-9]+(?:\.[0-9]+)?)?"
)


#: Window keys that stay absent until something is observed or carried; both read as ``None``
#: while absent, so an older ledger file round-trips unchanged.
OPTIONAL_WINDOW_KEYS: tuple[str, ...] = ("usage", "carry")

#: The two unified windows the CLI reports, in the order they are stored. One fact in two
#: places: ``harness.runner.cli.USAGE_WINDOWS`` is the producer's copy and must stay equal to
#: this one (tests/test_ledger.py pins it). The runner imports no domain module, so neither
#: definition can import the other.
USAGE_WINDOWS: tuple[str, ...] = ("five_hour", "seven_day")


class _Window(dict):
    """The window mapping: ``window["usage"]`` and ``window["carry"]`` read as ``None`` before
    anything has been observed or carried."""

    def __missing__(self, key: str):
        if key in OPTIONAL_WINDOW_KEYS:
            return None
        raise KeyError(key)


def _empty_window(period_start: str) -> dict:
    return _Window(
        {
            "period_start": period_start,
            "calls": 0,
            "rate_limited_until": None,
        }
    )


def _fraction(value: object) -> float | None:
    """A utilization as a 0..1 float, or ``None`` when it is not a number."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _normalise_usage(usage: dict, now_iso: str = "") -> dict:
    """The one usage shape, inbound and on disk: the two windows, whatever else came with
    them, then ``observed_at``.

    :meth:`Ledger.observe_usage` applies it to what a stage observed and :meth:`Ledger.from_json`
    applies it to what a file carries, so :meth:`Ledger.to_json` writes what it holds.
    ``resetsAt`` is accepted for ``resets_at``, because a hand-edited or older file may carry
    the CLI's own spelling; ``observed_at`` falls back to ``now_iso`` and is omitted when
    neither is known.
    """
    stored: dict = {}
    for name in USAGE_WINDOWS:
        window = usage.get(name)
        if not isinstance(window, dict):
            continue
        stored[name] = {
            "utilization": _fraction(window.get("utilization")),
            "resets_at": window.get("resets_at", window.get("resetsAt")),
        }
    for key, value in usage.items():
        if key in USAGE_WINDOWS or key == "observed_at":
            continue
        stored[key] = value
    observed_at = str(usage.get("observed_at") or now_iso or "")
    if observed_at:
        stored["observed_at"] = observed_at
    return stored


def window_has_reset(window: object, now: datetime | str) -> bool:
    """True once ``now`` is at or past the window's own ``resets_at`` (B399).

    An observation describes its window until that window resets, and nothing after; a stop it
    caused would otherwise outlive its reset and refuse the call that would refresh it.
    ``False`` when the reset is missing or unreadable, so an observation with no stated end is
    kept.
    """
    if not isinstance(window, dict):
        return False
    raw = window.get("resets_at")
    if not raw:
        return False
    try:
        boundary = parse_iso(str(raw))
        current = parse_iso(now) if isinstance(now, str) else as_utc(now)
    except ValueError:
        return False
    return current >= boundary


def block_until(sessions: int, now: datetime, five_hour_reset: str | None) -> str:
    """When a block of ``sessions`` five-hour sessions ends (D77). Pure, and always bounded.

    With a live ``five_hour.resets_at`` the first session is the remainder of the one already in
    progress, so the grant ends at that reset plus ``sessions - 1`` whole sessions. With no
    reading, an unreadable one, or one already past, it is ``sessions`` sessions from now.

    Never longer than ``sessions`` sessions from now, whatever the reading says: `observe_usage`
    stores `resets_at` verbatim, so a seven-day value landing in the five-hour slot would
    otherwise anchor a one-session block days out and the cap would be on the count alone.
    """
    count = max(1, int(sessions))
    session = timedelta(hours=SESSION_HOURS)
    current = as_utc(now)
    cap = current + session * count
    if five_hour_reset:
        try:
            reset = parse_iso(str(five_hour_reset))
        except ValueError:
            reset = None
        if reset is not None and reset > current:
            return iso(min(reset + session * (count - 1), cap))
    return iso(cap)


def _empty_cursors() -> dict:
    return {"notifications_last_seen": None, "seen_comment_ids": [], "keyword_denied": {}}


@dataclass
class Ledger:
    schema: int = SCHEMA
    window: dict = field(default_factory=lambda: _empty_window(EPOCH))
    cursors: dict = field(default_factory=_empty_cursors)
    history: list = field(default_factory=list)

    # -- calls -----------------------------------------------------------------------------

    def record(self, *, ts: str, stage: str, issue: int, run: str) -> None:
        """Append one call to ``history`` and count it in the window.

        Past ``HISTORY_CAP`` the oldest entries are dropped outright: the history is an audit
        trail of recent calls, and nothing is derived from the entries that fall off it.
        """
        self.history.append({"ts": ts, "stage": stage, "issue": int(issue), "run": run or ""})
        self.window["calls"] = int(self.window.get("calls", 0)) + 1
        if len(self.history) > HISTORY_CAP:
            del self.history[: len(self.history) - HISTORY_CAP]

    # -- rate limit ------------------------------------------------------------------------

    def set_rate_limited(self, until: str | None) -> None:
        self.window["rate_limited_until"] = until

    def rate_limited(self, now_iso: str) -> bool:
        until = self.window.get("rate_limited_until")
        if not until:
            return False
        try:
            return parse_iso(now_iso) < parse_iso(str(until))
        except ValueError:
            return False

    # -- usage -----------------------------------------------------------------------------

    def observe_usage(self, usage: dict | None, now_iso: str) -> None:
        """Store the subscription signal, rolling the window when the week has reset (B204).

        ``None`` is not an observation and never erases the last one, so a fake-backed call
        leaves what the last real call saw. When the newly reported ``seven_day.resets_at``
        differs from the one already stored and ``now`` is at or past that stored reset, the
        subscription week has turned over: the window restarts at the reset instant and
        ``carry`` survives it.
        """
        if not isinstance(usage, dict) or not usage:
            return
        previous = self._observed_reset()
        stored = _normalise_usage(usage, now_iso)
        self.window["usage"] = stored
        current = (stored.get("seven_day") or {}).get("resets_at")
        if not previous or not current or str(current) == str(previous):
            return
        try:
            now = parse_iso(str(now_iso))
            boundary = parse_iso(str(previous))
        except ValueError:
            return
        if now < boundary:
            return
        self.window["period_start"] = str(previous)
        self.window["calls"] = 0

    def _observed_reset(self) -> str | None:
        """The ``seven_day.resets_at`` of the observation currently stored, if any."""
        usage = self.window.get("usage")
        if not isinstance(usage, dict):
            return None
        window = usage.get("seven_day")
        if not isinstance(window, dict):
            return None
        value = window.get("resets_at")
        return str(value) if value else None

    def _utilization(self, name: str, now: datetime | str | None = None) -> float | None:
        usage = self.window.get("usage")
        if not isinstance(usage, dict):
            return None
        observed_at = usage.get("observed_at")
        start = self.window.get("period_start")
        if observed_at and start:
            try:
                if parse_iso(str(observed_at)) < parse_iso(str(start)):
                    return None
            except ValueError:
                pass
        window = usage.get(name)
        if not isinstance(window, dict):
            return None
        if now is not None and window_has_reset(window, now):
            return None
        return _fraction(window.get("utilization"))

    def weekly_utilization(self, now: datetime | str | None = None) -> float | None:
        """The seven-day utilization as a fraction; ``None`` when it predates this window, or
        (given ``now``) when that window has reset since it was observed (B399)."""
        return self._utilization("seven_day", now)

    def session_utilization(self, now: datetime | str | None = None) -> float | None:
        """The five-hour utilization as a fraction; ``None`` when it predates this window, or
        (given ``now``) when that window has reset since it was observed (B399)."""
        return self._utilization("five_hour", now)

    # -- carry -----------------------------------------------------------------------------

    def set_carry(self, issue: int, since: str, reason: str) -> None:
        """Mark one item as carried across the window: it resumes before anything else."""
        self.window["carry"] = {
            "issue": int(issue),
            "since": str(since),
            "reason": str(reason),
        }

    def clear_carry(self) -> None:
        self.window["carry"] = None

    def carry_issue(self) -> int | None:
        carry = self.window.get("carry")
        if not isinstance(carry, dict):
            return None
        try:
            return int(carry.get("issue"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    # -- the commanded halt ------------------------------------------------------------------

    def request_halt(self, actor: str, reason: str, at: str) -> None:
        """Stop the harness spending, on a level-3 `/harness halt`.

        The third of the three switches. `.harness/HALT` is committed and stops the workflows
        before they start; `HALT_FILE` stops a local run. This one lives in the ledger, which
        every runner fetches, so a comment stops the fleet without a commit, and it gates the
        model calls rather than the job, so the sweep keeps listening and `/harness resume`
        lifts it the same way it went on.
        """
        self.window["halt"] = {
            "by": str(actor),
            "reason": str(reason or "").strip(),
            "at": str(at),
        }

    def halt_request(self) -> dict | None:
        halt = self.window.get("halt")
        return dict(halt) if isinstance(halt, dict) and halt else None

    def clear_halt(self) -> None:
        self.window.pop("halt", None)

    # -- the block ---------------------------------------------------------------------------

    def _observed_five_hour_reset(self) -> str | None:
        """The ``five_hour.resets_at`` of the observation currently stored, if any."""
        usage = self.window.get("usage")
        if not isinstance(usage, dict):
            return None
        window = usage.get("five_hour")
        if not isinstance(window, dict):
            return None
        value = window.get("resets_at")
        return str(value) if value else None

    def request_block(
        self, actor: str, sessions: int, now: datetime, reason: str = ""
    ) -> dict:
        """Suspend the run window for the next ``sessions`` five-hour sessions (D77).

        The opposite of the halt, and the same shape: a scheduling fact the operator states in a
        comment, stored where every runner fetches it. It lifts the calendar and nothing else —
        the usage stops, both kill switches, the commanded halt, the trust gate and the two
        human gates all still apply.

        The end is measured **once**, here, from the session clock as it stands now. Re-measuring
        against each later observation would walk the block forward for ever as fresh sessions
        are observed, so ``anchor`` is recorded for the reply and never read again.
        """
        anchor = self._observed_five_hour_reset()
        grant = {
            "by": str(actor),
            "at": iso(now),
            "sessions": int(sessions),
            "until": block_until(int(sessions), now, anchor),
            "anchor": str(anchor or ""),
            "reason": str(reason or "").strip(),
        }
        self.window["block"] = grant
        return dict(grant)

    def block_grant(self) -> dict | None:
        grant = self.window.get("block")
        return dict(grant) if isinstance(grant, dict) and grant else None

    def clear_block(self) -> None:
        self.window.pop("block", None)

    def block_open(self, now: datetime | str) -> bool:
        """True while a block still stands; it ends by the clock alone, with nothing to run.

        Fails closed: a grant with no ``until``, or one that cannot be read, is shut. A block
        lifts a restriction, so anything unreadable about it has to mean "not lifted".
        """
        grant = self.block_grant()
        if grant is None:
            return False
        raw = grant.get("until")
        if not raw:
            return False
        try:
            until = parse_iso(str(raw))
            current = parse_iso(now) if isinstance(now, str) else as_utc(now)
        except ValueError:
            return False
        return current < until

    # -- force -------------------------------------------------------------------------------

    def force(self, item_id: int) -> None:
        """Mark an item window-exempt, in the ledger beside the carry (B283).

        `--force` is a scheduling exemption rather than a property of the work, so it lives
        with the carry and neither store backend needs a column for it.
        """
        forced = self.cursors.setdefault("forced", [])
        if int(item_id) not in forced:
            forced.append(int(item_id))

    def forced(self) -> tuple[int, ...]:
        rows = self.cursors.get("forced")
        return tuple(int(n) for n in rows) if isinstance(rows, list) else ()

    def unforce(self, item_id: int) -> None:
        """Spend the exemption: it starts the item once."""
        rows = self.cursors.get("forced")
        if isinstance(rows, list) and int(item_id) in rows:
            rows.remove(int(item_id))

    # -- cursors ---------------------------------------------------------------------------

    def seen(self, comment_id: str) -> bool:
        return str(comment_id) in self.cursors.setdefault("seen_comment_ids", [])

    def mark_seen(self, comment_id: str) -> None:
        ids = self.cursors.setdefault("seen_comment_ids", [])
        if str(comment_id) not in ids:
            ids.append(str(comment_id))

    def count_denied(self, handle: str) -> None:
        denied = self.cursors.setdefault("keyword_denied", {})
        denied[handle] = int(denied.get(handle, 0)) + 1

    # -- hygiene ---------------------------------------------------------------------------

    def pruned_at(self) -> str | None:
        """When the harness last swept its own old comments away, or None (D76).

        The cursor is what keeps the prune to once a week while `harness tidy` itself runs on
        every sweep to republish the queue.
        """
        value = self.cursors.get("pruned_at")
        return str(value) if value else None

    def mark_pruned(self, at: str) -> None:
        self.cursors["pruned_at"] = str(at)

    # -- serialisation ---------------------------------------------------------------------

    def to_json(self) -> str:
        """Render in the fixed key order, ``indent=2``, with a trailing newline."""
        window = {
            "period_start": self.window.get("period_start", EPOCH),
            "calls": int(self.window.get("calls", 0)),
            "rate_limited_until": self.window.get("rate_limited_until"),
        }
        # Usage, halt and carry are written only once they exist, so a ledger that has never
        # seen one is byte-identical to the file it was before.
        usage = self.window.get("usage")
        if isinstance(usage, dict) and usage:
            # Already normalised, by observe_usage or by from_json: write what is held.
            window["usage"] = dict(usage)
        halt = self.halt_request()
        if halt is not None:
            window["halt"] = {
                "by": str(halt.get("by", "")),
                "reason": str(halt.get("reason", "")),
                "at": str(halt.get("at", "")),
            }
        carry = self.window.get("carry")
        if isinstance(carry, dict) and carry:
            window["carry"] = {
                "issue": int(carry.get("issue", 0)),
                "since": str(carry.get("since", "")),
                "reason": str(carry.get("reason", "")),
            }
        # Written only once a block has been granted, for the same reason as the three above: a
        # ledger that has never seen one is byte-identical to the file it was.
        block = self.block_grant()
        if block is not None:
            window["block"] = {
                "by": str(block.get("by", "")),
                "at": str(block.get("at", "")),
                "sessions": int(block.get("sessions", 0) or 0),
                "until": str(block.get("until", "")),
                "anchor": str(block.get("anchor", "")),
                "reason": str(block.get("reason", "")),
            }
        cursors = {
            "notifications_last_seen": self.cursors.get("notifications_last_seen"),
            "seen_comment_ids": [str(x) for x in self.cursors.get("seen_comment_ids", [])],
            "keyword_denied": {
                str(k): int(v) for k, v in dict(self.cursors.get("keyword_denied", {})).items()
            },
        }
        # `forced` and `ask_calls` are written only once they hold something. Every cursor is
        # rendered explicitly rather than by copying `self.cursors`, because this function is
        # the schema: a key not named here does not survive a save.
        forced = self.forced()
        if forced:
            cursors["forced"] = [int(n) for n in forced]
        asks = self.cursors.get("ask_calls")
        if isinstance(asks, dict) and asks:
            cursors["ask_calls"] = {
                "date": str(asks.get("date", "")),
                "count": int(asks.get("count", 0) or 0),
            }
        pruned = self.pruned_at()
        if pruned:
            cursors["pruned_at"] = pruned
        history = [
            {
                "ts": entry.get("ts", ""),
                "stage": entry.get("stage", ""),
                "issue": int(entry.get("issue", 0)),
                "run": entry.get("run", "") or "",
            }
            for entry in self.history
        ]
        payload = {
            "schema": int(self.schema),
            "window": window,
            "cursors": cursors,
            "history": history,
        }
        return json.dumps(payload, indent=2, sort_keys=False) + "\n"

    @classmethod
    def from_json(cls, text: str) -> "Ledger":
        raw = json.loads(text)
        schema = raw.get("schema") if isinstance(raw, dict) else None
        if schema != SCHEMA:
            raise HarnessError(f"ledger schema must be {SCHEMA}; got {schema!r}")
        # A file written without the usage keys has neither; both then read as None through
        # the window's own default.
        window = _empty_window(EPOCH)
        window.update(dict(raw.get("window") or {}))
        # A file written before D74 carries a spend total and a per-stage observation map.
        # Neither is read, and dropping the window key here keeps it out of the next save.
        window.pop("spent_usd", None)
        # A file's usage block never went through observe_usage, so it is normalised here, and
        # here only.
        loaded_usage = window.get("usage")
        if isinstance(loaded_usage, dict) and loaded_usage:
            window["usage"] = _normalise_usage(dict(loaded_usage))
        cursors = _empty_cursors()
        cursors.update(dict(raw.get("cursors") or {}))
        history = [dict(entry) for entry in list(raw.get("history") or [])]
        ledger = cls(
            schema=SCHEMA,
            window=window,
            cursors=cursors,
            history=history,
        )
        return ledger

    @classmethod
    def empty(cls, period_start: str) -> "Ledger":
        return cls(
            schema=SCHEMA,
            window=_empty_window(period_start),
            cursors=_empty_cursors(),
            history=[],
        )


# -- file I/O --------------------------------------------------------------------------------


def load(path: Path) -> Ledger:
    """Read ``path``; a missing file is an empty ledger whose window starts at the epoch."""
    target = Path(path)
    if not target.is_file():
        return Ledger.empty(EPOCH)
    return Ledger.from_json(target.read_text(encoding="utf-8"))


def save(ledger: Ledger, path: Path) -> None:
    """Write a temp file beside ``path`` through the write guard, then ``os.replace`` (B115)."""
    target = Path(path)
    temp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    guarded_write(temp, ledger.to_json())
    try:
        os.replace(temp, target)
    except OSError:
        try:
            temp.unlink()
        except OSError:
            pass
        raise


# -- rebuild ---------------------------------------------------------------------------------


def parse_transition_comment(body: str) -> tuple[str, str, str] | None:
    """``(stage, state, run_url)`` from a B101 comment body, or ``None``.

    Both comment forms parse: the ``cost:`` line older comments carry is optional and ignored.
    """
    match = _TRANSITION_COMMENT.search(body or "")
    if match is None:
        return None
    return (
        match.group("stage").strip(),
        match.group("state").strip(),
        match.group("run").strip(),
    )


def rebuild(comments: Iterable[dict]) -> Ledger:
    """Regenerate history and the call count from B101 comments
    ``{"body", "created_at", "issue"}``."""
    parsed: list[tuple[str, int, str, str]] = []
    for comment in comments:
        body = str(comment.get("body", "") or "")
        found = parse_transition_comment(body)
        if found is None:
            continue
        stage, _state, run = found
        if not stage or stage == "-":
            continue
        try:
            issue = int(comment.get("issue", 0) or 0)
        except (TypeError, ValueError):
            issue = 0
        created = str(comment.get("created_at", "") or "")
        parsed.append((created, issue, stage, run))
    parsed.sort(key=lambda row: (row[0], row[1]))
    ledger = Ledger.empty(EPOCH)
    for created, issue, stage, run in parsed:
        ledger.record(ts=created, stage=stage, issue=issue, run=run)
    return ledger
