"""Shared fixtures for the Bright Bots Harness suite.

Everything here is derived from HARNESS-SPEC section 5 (``## Surface``) and from
RUN-DECISIONS.md. No fixture inspects the implementation.

Time is always frozen: 2026-09-01T12:00:00Z, a Tuesday. The most recent monday
(the default ``WEEKLY_RESET_DAY``) is therefore 2026-08-31.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

# --------------------------------------------------------------------------
# Frozen time
# --------------------------------------------------------------------------

FROZEN_AT = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
FROZEN_ISO = "2026-09-01T12:00:00Z"
PERIOD_START_ISO = "2026-08-31T00:00:00Z"
PERIOD_END_ISO = "2026-09-07T00:00:00Z"

# --------------------------------------------------------------------------
# .env
# --------------------------------------------------------------------------

# Every key named in RUN-DECISIONS "Config extras", with the safe defaults from
# RUN-DECISIONS "Defaults for .env.example". Paths are relative so they resolve
# against the .env file's own directory (which is tmp_path in every fixture).
DEFAULT_ENV: dict[str, str] = {
    "BACKEND": "fake",
    "REPO": "Bright-Bots-Initiative/brightboost",
    "PERMISSION_TIER": "0",
    "ALLOWLIST_LABEL": "harness-ok",
    "WEEKLY_BUDGET_PCT": "40",
    "SESSION_BUDGET_PCT": "15",
    "RESERVE_PCT": "10",
    "WEEKLY_RESET_DAY": "monday",
    "MAX_CONCURRENT_CLONES": "1",
    "MAX_TURNS_DISCOVER": "10",
    "MAX_TURNS_PROPOSE": "30",
    "MAX_TURNS_IMPLEMENT": "80",
    "MAX_TURNS_PACKAGE": "10",
    "MAX_RETRIES_GATES": "2",
    "GITHUB_API_CEILING_PER_HOUR": "50",
    "MIN_FREE_DISK_GB": "5",
    "DB_PATH": "harness.db",
    "RUNS_DIR": "runs",
    "PACKAGES_DIR": "packages",
    "HALT_FILE": "HALT",
    "FULLSEND_ENABLED": "false",
    "WEEKLY_CAP_USD": "25.00",
    "PER_CALL_CAP_USD": "3.00",
    "MAX_CONCURRENT_ITEMS": "1",
    "MAX_REVISE_CYCLES": "3",
    "FORK_REPO": "",
    "UPSTREAM_REPO": "Bright-Bots-Initiative/brightboost",
    "TRUST_FILE": ".harness/trust.txt",
    "NOTIFY_POLL_HOURS": "3",
    "MAX_SUBISSUES": "8",
    "SELF_REPO": "jgoetzmann/bright-bots-harness",
    "TRACKING_ISSUE": "",
    "STORE_BACKEND": "sqlite",
    "WEEKLY_USAGE_STOP_PCT": "90",
    "SESSION_USAGE_STOP_PCT": "70",
    "OVERRUN_PCT": "10",
    "RUN_WINDOW_START": "",
    "RUN_WINDOW_END": "",
    "HARNESS_GITHUB_TOKEN": "",
    "ANTHROPIC_API_KEY": "",
}

# A token whose shape satisfies RUN-DECISIONS' ^github_pat_[A-Za-z0-9_]{40,}$
VALID_PAT = "github_pat_" + "A1b2C3d4E5" * 5
# A token whose shape satisfies ^ghp_[A-Za-z0-9]{30,}$
VALID_GHP = "ghp_" + "Z9y8X7w6V5" * 4


def write_env(path: Path, **overrides: object) -> Path:
    """Write a complete ``.env`` at *path* and return *path*.

    A keyword override replaces the value for that key. Passing ``None``
    *removes* the key (used for B2). A keyword name that is not a known key is
    written verbatim (used for B3).
    """
    values = dict(DEFAULT_ENV)
    for key, value in overrides.items():
        if value is None:
            values.pop(key, None)
        else:
            values[key] = str(value)
    lines = ["# written by tests/conftest.py", ""]
    lines.extend(f"{key}={value}" for key, value in values.items())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


@pytest.fixture(name="write_env")
def write_env_fixture():
    """The :func:`write_env` helper, injectable so other suites can reuse it."""
    return write_env


@pytest.fixture
def default_env() -> dict[str, str]:
    """A mutable copy of the default ``.env`` mapping."""
    return dict(DEFAULT_ENV)


@pytest.fixture
def env_file(tmp_path: Path) -> Path:
    """A complete, valid ``.env`` at ``tmp_path/.env``."""
    return write_env(tmp_path / ".env")


# --------------------------------------------------------------------------
# Harness objects
# --------------------------------------------------------------------------


@pytest.fixture
def frozen_clock():
    """A ``FrozenClock`` at 2026-09-01T12:00:00Z (a Tuesday)."""
    from harness.clock import FrozenClock

    return FrozenClock(FROZEN_AT)


@pytest.fixture
def sample_config(env_file: Path):
    """``Config`` loaded from :func:`env_file` with no environment overrides."""
    from harness.config import load_config

    return load_config(env_path=env_file, environ={})


@pytest.fixture
def store(tmp_path: Path, frozen_clock):
    """A migrated ``Store`` on ``tmp_path/harness.db`` driven by the frozen clock."""
    from harness.store import Store

    s = Store(tmp_path / "harness.db", clock=frozen_clock)
    s.migrate()
    try:
        yield s
    finally:
        s.close()


# --------------------------------------------------------------------------
# Runner fixtures (written inline; nothing is read from the repo's own fixtures)
# --------------------------------------------------------------------------

# A complete section 7.1 work package: >= 1 decision, fewer than 3 slices,
# fewer than 15 behaviors, empty open questions (RUN-DECISIONS "Runner").
WORK_PACKAGE_816 = """# fix(scripts): bundle size check misreports esm chunks

## Issue
https://github.com/Bright-Bots-Initiative/brightboost/issues/816 - issue 816. The bundle size
gate fails builds that are comfortably inside the budget.

## Diagnosis
scripts/check-bundle-size.js sums every entry under dist/assets, including source maps and
fonts, so the reported total is roughly twice the shipped javascript.

## Approach
Restrict the sum to the emitted .js chunks and print a per-chunk breakdown so a regression is
attributable to one chunk.

## Slices
1. Restrict the asset glob to .js chunks.
2. Print a per-chunk size breakdown.

## Behaviors
1. The check ignores non-javascript assets.
2. The check fails when the javascript total exceeds the budget.

## Acceptance criteria
- The bundle size check exits 0 on a clean build of main.
- The failure message names the chunk that pushed the total over budget.

## Decisions
- Filter inside the script rather than changing the vite config, because the config is shared
  with the storybook build and widening it would change two consumers.

## Open questions
None

## Touched paths
- scripts/check-bundle-size.js

## Risks
A narrower filter could hide a genuine regression in a non-javascript asset.
"""


def runner_fixture_payload(
    text: str,
    *,
    ok: bool = True,
    turns: int | None = 3,
    cost_usd: float | None = 0.0125,
    allowance_pct: float | None = None,
    duration_ms: int | None = 4321,
    session_id: str | None = "fixture-session",
    exit_code: int = 0,
    error: str | None = None,
) -> dict:
    """A fixture body whose top-level keys are exactly the ``RunResult`` fields."""
    return {
        "ok": ok,
        "text": text,
        "turns": turns,
        "cost_usd": cost_usd,
        "allowance_pct": allowance_pct,
        "duration_ms": duration_ms,
        "session_id": session_id,
        "exit_code": exit_code,
        "transcript": [
            {"role": "user", "content": "fixture prompt"},
            {"role": "assistant", "content": text},
        ],
        "error": error,
    }


FAKE_FIXTURES: dict[str, dict] = {
    "discover": runner_fixture_payload("#816\n#801\n"),
    "propose": runner_fixture_payload(WORK_PACKAGE_816),
    "implement": runner_fixture_payload(
        "Filtered the asset glob to .js chunks and added a per-chunk breakdown."
    ),
    "package": runner_fixture_payload("Package assembled."),
    "diagnose_gate_failure": runner_fixture_payload(
        "npm run lint failed on an unused import; remove it."
    ),
}


@pytest.fixture
def runner_fixtures_dir(tmp_path: Path) -> Path:
    """A directory of ``<stage>.json`` runner fixtures, written inline."""
    directory = tmp_path / "runner_fixtures"
    directory.mkdir(parents=True, exist_ok=True)
    for stage, payload in FAKE_FIXTURES.items():
        (directory / f"{stage}.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8", newline="\n"
        )
    return directory


@pytest.fixture
def fake_runner(runner_fixtures_dir: Path):
    """``FakeRunner`` pointed at :func:`runner_fixtures_dir`."""
    from harness.runner.fake import FakeRunner

    return FakeRunner(runner_fixtures_dir)
