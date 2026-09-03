"""The ``claude`` CLI backend (SPEC 5.4.3; D2 §6.1 budget flag and §6.3 rate-limit outcome)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from harness.config import environ_snapshot
from harness.redact import redact
from harness.runner.base import RATE_LIMIT_PATTERN, RunRequest, RunResult

#: Removed from the child environment (B26). Delivery 1 is subscription-backed;
#: a stray key would silently move the spend to the API billing pool.
STRIPPED_ENV_KEYS: tuple[str, ...] = ("ANTHROPIC_API_KEY",)

#: How much of stderr survives into ``RunResult.error``.
STDERR_TAIL_CHARS = 2000

#: Synthetic exit codes for the cases where the process never reported one.
EXIT_TIMEOUT = 124
EXIT_NOT_EXECUTABLE = 127

_ISO_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
)
_RELATIVE_RESET = re.compile(
    r"(?i)resets?\s+in\s+(?:about\s+|~\s*)?(\d+)\s*(minutes?|mins?|hours?|hrs?|h|m)\b"
)


def _spawn_resolved(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
    """Default spawn: resolve argv[0] on PATH first (Windows ``.CMD`` shims need PATHEXT)."""
    resolved = shutil.which(argv[0]) or argv[0]
    return subprocess.run([resolved, *argv[1:]], **kwargs)  # type: ignore[call-overload]


def parse_reset_at(text: str) -> str | None:
    """The reset marker in a usage-limit message (B119): ISO-Z, ``+PT30M``-style, or ``None``."""
    haystack = text or ""
    match = _ISO_TIMESTAMP.search(haystack)
    if match:
        return _normalise_iso(match.group(0))
    match = _RELATIVE_RESET.search(haystack)
    if match:
        amount = int(match.group(1))
        unit = match.group(2).lower()
        return f"+PT{amount}H" if unit.startswith("h") else f"+PT{amount}M"
    return None


def _normalise_iso(raw: str) -> str:
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return raw
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ClaudeCliRunner:
    """Runs one stage by shelling out to the ``claude`` binary."""

    name = "cli"

    def __init__(
        self,
        claude_bin: str = "claude",
        spawn: Callable[..., subprocess.CompletedProcess] | None = None,
    ) -> None:
        self.claude_bin = claude_bin
        self.spawn = spawn if spawn is not None else _spawn_resolved

    # -- argv and environment ------------------------------------------------

    def build_argv(self, request: RunRequest) -> list[str]:
        """The order frozen in SPEC 5.4.3 (B25), plus ``--max-budget-usd`` after ``--max-turns``."""
        argv: list[str] = [
            self.claude_bin,
            "--print",
            "--output-format",
            "json",
            "--max-turns",
            str(request.max_turns),
        ]
        if request.max_budget_usd is not None:
            argv.append("--max-budget-usd")
            argv.append(str(request.max_budget_usd))
        argv.extend(
            [
                "--permission-mode",
                "acceptEdits",
                "--allowed-tools",
                ",".join(request.allowed_tools),
            ]
        )
        if request.disallowed_tools:
            argv.append("--disallowed-tools")
            argv.append(",".join(request.disallowed_tools))
        if request.system_prompt is not None:
            argv.append("--system-prompt")
            argv.append(request.system_prompt)
        for directory in request.add_dirs:
            argv.append("--add-dir")
            argv.append(str(directory))
        argv.append("--")
        argv.append(request.prompt)
        return argv

    def build_env(self) -> dict[str, str]:
        """The parent environment minus every key in :data:`STRIPPED_ENV_KEYS` (B26)."""
        base = environ_snapshot()
        return {key: value for key, value in base.items() if key not in STRIPPED_ENV_KEYS}

    # -- the call ------------------------------------------------------------

    def run(self, request: RunRequest) -> RunResult:
        argv = self.build_argv(request)
        env = self.build_env()
        try:
            proc = self.spawn(
                argv,
                cwd=str(request.cwd),
                env=env,
                timeout=request.timeout_s,
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired as exc:
            tail = _as_text(getattr(exc, "stderr", None))
            if not tail:
                tail = f"claude timed out after {request.timeout_s}s"
            return self._failure(request, EXIT_TIMEOUT, tail)
        except OSError as exc:
            return self._failure(request, EXIT_NOT_EXECUTABLE, f"{self.claude_bin}: {exc}")

        stdout = _as_text(getattr(proc, "stdout", ""))
        stderr = _as_text(getattr(proc, "stderr", ""))
        exit_code = int(getattr(proc, "returncode", 0) or 0)

        if exit_code != 0:
            # B119: exhaustion is an outcome with a reset time, not a generic failure.
            combined = f"{stdout}\n{stderr}"
            if RATE_LIMIT_PATTERN.search(combined):
                return self._failure(
                    request, exit_code, stderr or stdout, reset_at=parse_reset_at(combined)
                )
            # A budget stop (D19: `subtype` error_max_budget_usd) exits 1 with a complete JSON
            # result; keep its cost, turns and reason rather than dumping the JSON as the error.
            try:
                errored: Any = json.loads(stdout)
            except (ValueError, TypeError):
                errored = None
            if isinstance(errored, dict) and errored.get("is_error"):
                return self._from_json(request, errored, stderr, exit_code)
            return self._failure(request, exit_code, stderr or stdout)

        try:
            data: Any = json.loads(stdout)
        except (ValueError, TypeError):
            data = None
        if not isinstance(data, dict):
            return self._failure(request, exit_code, stderr or "claude produced unparseable stdout")

        return self._from_json(request, data, stderr, exit_code)

    # -- parsing -------------------------------------------------------------

    def _from_json(self, request: RunRequest, data: dict, stderr: str, exit_code: int) -> RunResult:
        """Every JSON field is optional; a missing one is ``None`` (B28, B29)."""
        text = _as_str(data.get("result"))
        if text is None:
            text = _as_str(data.get("text"))
        if text is None:
            text = ""
        is_error = bool(data.get("is_error", False))
        error = None
        if is_error:
            # The CLI names the reason in `subtype` (e.g. error_max_budget_usd, D19) and often
            # leaves stderr empty; the subtype is what a diagnose cycle or an operator needs to see.
            subtype = str(data.get("subtype") or "")
            fallback = subtype or "claude reported is_error"
            error = redact(stderr[-STDERR_TAIL_CHARS:]) if stderr else fallback
        return RunResult(
            ok=not is_error,
            text=text,
            turns=_as_int(data.get("num_turns")),
            cost_usd=_as_float(data.get("total_cost_usd")),
            allowance_pct=None,
            duration_ms=_as_int(data.get("duration_ms")),
            session_id=_as_str(data.get("session_id")),
            exit_code=exit_code,
            transcript=(
                {"role": "user", "content": request.prompt},
                {"role": "assistant", "content": text},
                {"raw": data},
            ),
            error=error,
        )

    def _failure(
        self,
        request: RunRequest,
        exit_code: int,
        stderr: str,
        *,
        reset_at: str | None = None,
    ) -> RunResult:
        """The single failure shape: ``ok=False`` and a redacted stderr tail (B30)."""
        return RunResult(
            ok=False,
            text="",
            turns=None,
            cost_usd=None,
            allowance_pct=None,
            duration_ms=None,
            session_id=None,
            exit_code=exit_code,
            transcript=({"role": "user", "content": request.prompt},),
            error=redact(stderr[-STDERR_TAIL_CHARS:]),
            reset_at=reset_at,
        )


# -- coercion helpers --------------------------------------------------------


def _as_text(value: object) -> str:
    """Whatever the process handed back, as ``str``. Never raises."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value
    return str(value)


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _as_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return None
    return None


def _as_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None
