"""Runner protocol, the two frozen result shapes, and the backend factory (SPEC 5.4.1)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from harness.errors import ConfigError

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from harness.config import Config


#: The CLI's usage-limit signature (D2 handoff §6.3, B119). Matched case-insensitively against
#: stdout and stderr of a non-zero exit; a hit is an outcome with a reset time, not a failure.
RATE_LIMIT_PATTERN = re.compile(
    r"(?i)(usage limit|rate limit|too many requests|limit reached|resets? (at|in))"
)


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
    #: ``claude --max-budget-usd``; omitted from argv when ``None`` (D2 §6.1, B119).
    max_budget_usd: float | None = None
    #: Absolute paths the model must not read (B218/D36). Each becomes one ``permissions.deny``
    #: rule passed through ``--settings``. ``Read`` is NOT confined to ``cwd`` by the CLI: with
    #: ``--permission-mode acceptEdits`` it will read any absolute path it is given, including
    #: the harness's own ``.env``. A directory is written with a trailing ``/**``.
    deny_read: tuple[str, ...] = ()
    #: ``claude --model`` and ``--effort`` (B225). Omitted from argv when ``None``, which is
    #: what the fake backend and the Delivery 2 argv shape both expect.
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
    cost_usd: float | None
    allowance_pct: float | None
    duration_ms: int | None
    session_id: str | None
    exit_code: int
    transcript: tuple[dict, ...]
    error: str | None
    #: When the call was refused for exhaustion: an ISO-Z timestamp, or a relative ISO
    #: duration such as ``"+PT30M"`` for the stage to add to its clock (D2 §12). ``None``
    #: on every other outcome.
    reset_at: str | None = None
    #: The subscription usage the CLI reported alongside the call (D3, B200-B203):
    #: ``{"five_hour": {"utilization": float, "resets_at": iso}, "seven_day": {...},
    #: "status": str}``. ``None`` whenever the backend saw no signal — no decision may
    #: depend on it being there (B114).
    usage: dict | None = None


class Runner(Protocol):
    """The one method every backend implements."""

    name: str

    def run(self, request: RunRequest) -> RunResult: ...


def is_rate_limited(result: RunResult) -> bool:
    """True when a failed result is the CLI saying "come back later", not "I failed"."""
    return result.reset_at is not None or bool(RATE_LIMIT_PATTERN.search(result.error or ""))


def get_runner(config: "Config") -> Runner:
    """Return the backend named by ``config.backend`` (B24); anything unknown is a
    ``ConfigError``."""
    backend = getattr(config, "backend", None)
    if backend == "cli":
        from harness.runner.cli import ClaudeCliRunner

        # B202: the real backend always asks for the usage stream; the flag stays off by
        # default so a hand-built runner keeps the Delivery 2 argv.
        return ClaudeCliRunner(capture_usage=True)
    if backend == "fake":
        from harness.runner.fake import FakeRunner

        return FakeRunner()
    raise ConfigError(f"BACKEND: unknown backend {backend!r} (expected 'cli' or 'fake')")
