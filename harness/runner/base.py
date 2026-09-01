"""Runner protocol and the two frozen result shapes (SPEC 5.4.1).

Pure declarations plus the backend factory. This module performs no I/O: the
concrete backends are imported lazily inside :func:`get_runner` so that
``harness.runner.base`` stays importable without touching the filesystem, the
environment, or ``subprocess``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from harness.errors import ConfigError

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from harness.config import Config


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


class Runner(Protocol):
    """The one method every backend implements."""

    name: str

    def run(self, request: RunRequest) -> RunResult: ...


def get_runner(config: "Config") -> Runner:
    """Return the backend named by ``config.backend`` (B24).

    ``"cli"`` yields :class:`~harness.runner.cli.ClaudeCliRunner`, ``"fake"``
    yields :class:`~harness.runner.fake.FakeRunner`. Anything else — including
    the ``api`` backend the protocol admits but delivery 1 does not ship — is a
    ``ConfigError``.
    """
    backend = getattr(config, "backend", None)
    if backend == "cli":
        from harness.runner.cli import ClaudeCliRunner

        return ClaudeCliRunner()
    if backend == "fake":
        from harness.runner.fake import FakeRunner

        return FakeRunner()
    raise ConfigError(f"BACKEND: unknown backend {backend!r} (expected 'cli' or 'fake')")
