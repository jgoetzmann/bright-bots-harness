"""Runner backends: the ``claude`` CLI and a deterministic fake."""

from __future__ import annotations

from harness.runner.base import Runner, RunRequest, RunResult, get_runner
from harness.runner.cli import ClaudeCliRunner
from harness.runner.fake import FakeRunner

__all__ = [
    "ClaudeCliRunner",
    "FakeRunner",
    "RunRequest",
    "RunResult",
    "Runner",
    "get_runner",
]
