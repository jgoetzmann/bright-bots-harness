"""Deterministic fixture-backed runner (SPEC 5.4.2). No child process, no network."""

from __future__ import annotations

import json
from pathlib import Path

from harness.runner.base import RunRequest, RunResult

#: ``harness/runner/fake.py`` -> ``harness/runner`` -> ``harness`` -> repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: Where the canned results live when no directory is injected.
DEFAULT_FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "runner"


class FakeRunner:
    """Returns the fixture for ``request.stage``; ``"rate_limited": true`` replays exhaustion."""

    name = "fake"

    def __init__(self, fixtures_dir: Path | None = None) -> None:
        self.fixtures_dir = Path(fixtures_dir) if fixtures_dir is not None else DEFAULT_FIXTURES_DIR

    def fixture_path(self, stage: str) -> Path:
        return self.fixtures_dir / f"{stage}.json"

    def run(self, request: RunRequest) -> RunResult:
        path = self.fixture_path(request.stage)
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return _no_fixture(request.stage)
        try:
            data = json.loads(raw)
        except ValueError:
            return _no_fixture(request.stage)
        if not isinstance(data, dict):
            return _no_fixture(request.stage)

        rate_limited = bool(data.get("rate_limited", False))
        reset_at = data.get("reset_at")

        return RunResult(
            ok=False if rate_limited else bool(data.get("ok", False)),
            text=str(data.get("text", "")),
            turns=data.get("turns"),
            cost_usd=data.get("cost_usd"),
            allowance_pct=data.get("allowance_pct"),
            duration_ms=data.get("duration_ms"),
            session_id=data.get("session_id"),
            exit_code=int(data.get("exit_code", 0)),
            transcript=tuple(data.get("transcript") or ()),
            error=data.get("error"),
            reset_at=str(reset_at) if reset_at is not None else None,
        )


def _no_fixture(stage: str) -> RunResult:
    """The one failure shape the fake can produce."""
    return RunResult(
        ok=False,
        text="",
        turns=None,
        cost_usd=None,
        allowance_pct=None,
        duration_ms=None,
        session_id=None,
        exit_code=1,
        transcript=(),
        error=f"no fixture for stage {stage}",
    )
