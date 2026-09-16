"""Runner protocol, the request and result shapes, and the backend factory."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from harness.errors import ConfigError

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from harness.config import Config


#: The CLI's usage-limit signature (B119). Matched case-insensitively against stdout and stderr
#: of a non-zero exit, and against the message of an ``is_error`` result at any exit; a hit is
#: an outcome with a reset time. ``hit your ... limit`` is the CLI's newer wording.
RATE_LIMIT_PATTERN = re.compile(
    r"(?i)(usage limit|rate limit|too many requests|limit reached|resets? (at|in)"
    r"|hit your (?:\w+ )?limit)"
)

#: The ``status`` a ``rate_limit_event`` carries when the subscription refused the call (D71).
#: The CLI then exits 0 with ``is_error: true`` and ``subtype: "success"``, so neither the exit
#: code nor the subtype says what happened, and only this status does.
USAGE_REJECTED = "rejected"


#: The runner's name for the CLI's ``isUsingOverage`` / ``overageInUse``, kept on a rejected
#: reading only (B403).
USAGE_OVERAGE_IN_USE = "overage_in_use"

#: The runner's name for a rejected event's top-level ``resetsAt``: the reset of the limit that
#: refused, whichever type it is (B404).
USAGE_REJECTED_RESETS_AT = "rejected_resets_at"


def usage_rejected(usage: Mapping[str, Any] | None) -> bool:
    """True when the usage signal says the subscription refused the call (B396).

    It applies the CLI's own "not blocked" test in 2.1.272,
    ``status !== "rejected" || isUsingOverage || overageInUse``. A call that went ahead on extra
    usage and then failed for another reason, such as max turns, was not refused; classifying
    it as a rate limit would return the item instead of reporting the failure.
    """
    return (
        isinstance(usage, Mapping)
        and usage.get("status") == USAGE_REJECTED
        and usage.get(USAGE_OVERAGE_IN_USE) is not True
    )


def _iso_instant(raw: object) -> tuple[datetime, str] | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed, parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def exhausted_reset(usage: Mapping[str, Any] | None) -> str | None:
    """When the refusal lifts: the ``resets_at`` of the window at or over 1.0 (B396).

    The earliest one when several are, because that is the soonest a call can be tried again.
    When no unified window is exhausted, which happens when a model-specific weekly limit such
    as ``seven_day_opus`` refused while both unified readings are below 1.0, the event's own
    top-level reset is used. ``None`` when neither is readable; the stage then falls back to
    its default delay.
    """
    if not isinstance(usage, Mapping):
        return None
    found: list[tuple[datetime, str]] = []
    for window in usage.values():
        if not isinstance(window, Mapping):
            continue
        utilization = window.get("utilization")
        if isinstance(utilization, bool) or not isinstance(utilization, (int, float)):
            continue
        if utilization < 1.0:
            continue
        instant = _iso_instant(window.get("resets_at"))
        if instant is not None:
            found.append(instant)
    if found:
        return min(found)[1]
    instant = _iso_instant(usage.get(USAGE_REJECTED_RESETS_AT))
    return None if instant is None else instant[1]


@dataclass(frozen=True)
class RunRequest:
    """Everything a backend needs to make one model call."""

    stage: str
    prompt: str
    system_prompt: str | None
    allowed_tools: tuple[str, ...]
    disallowed_tools: tuple[str, ...]
    max_turns: int
    cwd: Path
    timeout_s: int
    add_dirs: tuple[Path, ...] = ()
    #: Absolute paths the model must not read (B218). Each becomes one ``permissions.deny``
    #: rule passed through ``--settings``. The CLI does not confine ``Read`` to ``cwd``: under
    #: ``--permission-mode acceptEdits`` it reads any absolute path it is given, including the
    #: harness's own ``.env``. A directory is written with a trailing ``/**``.
    deny_read: tuple[str, ...] = ()
    #: ``claude --model`` and ``--effort``, omitted from argv when ``None``, which is what the
    #: fake backend expects (B225).
    model: str | None = None
    effort: str | None = None


@dataclass(frozen=True)
class RunResult:
    """The outcome of one model call.

    Every optional field is ``None`` when the backend could not determine it;
    a missing field is never an error (B29).
    """

    ok: bool
    text: str
    turns: int | None
    duration_ms: int | None
    session_id: str | None
    exit_code: int
    transcript: tuple[dict, ...]
    error: str | None
    #: When the call was refused for exhaustion: an ISO-Z timestamp, or a relative ISO
    #: duration such as ``"+PT30M"`` for the stage to add to its clock. ``None`` on every
    #: other outcome.
    reset_at: str | None = None
    #: The subscription usage the CLI reported alongside the call:
    #: ``{"five_hour": {"utilization": float, "resets_at": iso}, "seven_day": {...},
    #: "status": str}``. ``None`` whenever the backend saw no signal, and no decision may
    #: depend on it being there (B114).
    usage: dict | None = None


class Runner(Protocol):
    """The one method every backend implements."""

    name: str

    def run(self, request: RunRequest) -> RunResult: ...


def is_rate_limited(result: RunResult) -> bool:
    """True when a failed result is the CLI saying "come back later" (D71).

    Three signals, any one enough: a reset time, the limit wording in the error, or a failed
    call whose usage signal says ``rejected``. A result carrying no usage signal is classified
    by the first two alone.
    """
    if result.reset_at is not None or RATE_LIMIT_PATTERN.search(result.error or ""):
        return True
    return not result.ok and usage_rejected(result.usage)


def get_runner(config: "Config") -> Runner:
    """Return the backend named by ``config.backend`` (B24); anything unknown is a
    ``ConfigError``."""
    backend = getattr(config, "backend", None)
    if backend == "cli":
        from harness.runner.cli import ClaudeCliRunner

        # The real backend always asks for the usage stream (B202). The flag stays off by
        # default, so a hand-built runner keeps the plain ``--output-format json`` argv.
        return ClaudeCliRunner(capture_usage=True)
    if backend == "fake":
        from harness.runner.fake import FakeRunner

        return FakeRunner()
    raise ConfigError(f"BACKEND: unknown backend {backend!r} (expected 'cli' or 'fake')")
