"""Injectable time source.

Every timestamp the harness stores anywhere is produced by :func:`iso`, so tests
never sleep and never read the wall clock.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol, runtime_checkable

__all__ = ["Clock", "SystemClock", "FrozenClock", "iso", "parse_iso"]

ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


@runtime_checkable
class Clock(Protocol):
    """A source of the current time, always timezone-aware and always UTC."""

    def now(self) -> datetime:
        """Return the current instant as a tz-aware UTC ``datetime``."""
        ...


class SystemClock:
    """The real wall clock, in UTC."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FrozenClock:
    """A clock that only moves when a test moves it."""

    def __init__(self, at: datetime) -> None:
        self._at = _as_utc(at)

    def now(self) -> datetime:
        return self._at

    def advance(self, seconds: float) -> None:
        """Move the clock forward (or back, for a negative value) by ``seconds``."""
        self._at = self._at + timedelta(seconds=seconds)

    def set(self, at: datetime) -> None:
        """Pin the clock to ``at``; a naive value is read as UTC."""
        self._at = _as_utc(at)


def _as_utc(dt: datetime) -> datetime:
    """Coerce ``dt`` to a tz-aware UTC datetime; a naive value is assumed UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso(dt: datetime) -> str:
    """Render ``dt`` as ``YYYY-MM-DDTHH:MM:SSZ``, always UTC, always with the ``Z``."""
    return _as_utc(dt).strftime(ISO_FORMAT)


def parse_iso(s: str) -> datetime:
    """Parse a timestamp written by :func:`iso` back into a tz-aware UTC datetime."""
    text = s.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"not an ISO timestamp: {s!r}") from exc
    return _as_utc(parsed)
