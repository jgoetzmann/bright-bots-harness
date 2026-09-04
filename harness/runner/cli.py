"""The ``claude`` CLI backend (SPEC 5.4.3; D2 §6.1 and §6.3; D3 the usage stream, B200-B202)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
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
EXIT_ARGV_TOO_LONG = 126

#: The prompt travels on stdin, never in argv (D35). What is left is flags plus the system
#: prompt, and that still has to fit: on Windows ``claude`` is an npm ``.CMD`` shim, so the
#: whole command line passes through ``cmd.exe``, whose hard ceiling is 8191 characters --
#: about a quarter of the 32767 ``CreateProcess`` allows. Over it, cmd.exe prints "The command
#: line is too long." and exits non-zero, which reads like a model failure and is not one.
ARGV_LIMIT_WINDOWS = 8191
ARGV_LIMIT_POSIX = 131072

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


def argv_limit() -> int:
    """The platform ceiling on one command line (B216)."""
    return ARGV_LIMIT_WINDOWS if os.name == "nt" else ARGV_LIMIT_POSIX


def argv_too_long(argv: Sequence[str]) -> str | None:
    """A legible refusal when `argv` cannot be spawned, else ``None`` (B216).

    Names the offender, because the only argument that can realistically grow past the ceiling
    is ``--system-prompt`` -- and ``prompts/system.md`` is pinned, so the fix is a prompt edit
    and a re-pin, not a code change.
    """
    limit = argv_limit()
    # One separator per gap, matching how the OS assembles the line.
    length = sum(len(part) for part in argv) + max(0, len(argv) - 1)
    if length <= limit:
        return None
    widest = max(range(len(argv)), key=lambda i: len(argv[i]), default=0)
    culprit = argv[widest] if argv else ""
    name = argv[widest - 1] if widest > 0 and argv[widest - 1].startswith("--") else "argv"
    return (
        f"command line is {length} characters; this platform allows {limit}. "
        f"The longest argument is {name} ({len(culprit)} characters). "
        "The prompt already travels on stdin (D35); shorten the system prompt and re-pin."
    )


def deny_settings(paths: Sequence[str]) -> str | None:
    """``--settings`` JSON denying Read on `paths`, or ``None`` when there is nothing to deny.

    B218/D36. The rule form that the CLI actually enforces is a bare absolute path -- a
    ``//``-prefixed one is accepted and silently matches nothing, and a relative glob such as
    ``**/.env`` matches nothing either. Both were measured, not assumed.
    """
    rules = [f"Read({path})" for path in paths if str(path).strip()]
    if not rules:
        return None
    return json.dumps({"permissions": {"deny": rules}}, separators=(",", ":"))


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


#: The stream event that carries the subscription windows (D3 "Why").
RATE_LIMIT_EVENT = "rate_limit_event"

#: The two unified windows the CLI reports, in the order they are stored.
#: One fact, stated in two places: ``harness.ledger.USAGE_WINDOWS`` is the consumer's copy and
#: must stay equal to this one (pinned by tests/test_ledger.py). The runner imports no domain
#: module by design, so the shared home is ``harness/runner/base.py``, beside ``RunResult.usage``.
USAGE_WINDOWS: tuple[str, ...] = ("five_hour", "seven_day")


def _reset_iso(value: object) -> str | None:
    """``resetsAt`` as ISO-Z: the CLI sends epoch seconds; a string is passed through."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.isdigit():
            return _reset_iso(int(text))
        return _normalise_iso(text)
    return None


def usage_from_event(event: Mapping[str, Any]) -> dict | None:
    """One ``rate_limit_event`` as the :attr:`RunResult.usage` shape, or ``None``.

    Tolerant on purpose (B114): anything missing simply drops out, and an event with no
    recognisable window is no observation at all rather than a zero one.
    """
    if not isinstance(event, Mapping):
        return None
    info: Any = event.get("rate_limit_info")
    if not isinstance(info, Mapping):
        info = event if "unifiedWindows" in event else None
    if not isinstance(info, Mapping):
        return None
    windows = info.get("unifiedWindows")
    if not isinstance(windows, Mapping):
        return None
    usage: dict = {}
    for name in USAGE_WINDOWS:
        window = windows.get(name)
        if not isinstance(window, Mapping):
            continue
        utilization = _as_float(window.get("utilization"))
        if utilization is None:
            continue
        usage[name] = {
            "utilization": utilization,
            "resets_at": _reset_iso(window.get("resetsAt", window.get("resets_at"))),
        }
    if not usage:
        return None
    status = info.get("status")
    if isinstance(status, str) and status:
        usage["status"] = status
    return usage


def parse_stream(stdout: str) -> tuple[dict | None, dict | None]:
    """``(result object, usage)`` from ``--output-format stream-json`` output (B201).

    Pure. The LAST line whose ``type`` is ``"result"`` is the result object (the same fields
    the non-streaming JSON carries); every ``rate_limit_event`` updates the usage and the last
    one wins. Lines that are not JSON objects are ignored, and no result line at all is
    ``(None, usage)`` — the caller treats that as unparseable output (B30).
    """
    result: dict | None = None
    usage: dict | None = None
    for line in (stdout or "").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            event: Any = json.loads(text)
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "result":
            result = event
            continue
        if kind == RATE_LIMIT_EVENT:
            found = usage_from_event(event)
            if found is not None:
                usage = found
    if usage is None and isinstance(result, dict):
        # Some builds fold the last observation into the result line itself.
        usage = usage_from_event(result)
    return result, usage


class ClaudeCliRunner:
    """Runs one stage by shelling out to the ``claude`` binary."""

    name = "cli"

    def __init__(
        self,
        claude_bin: str = "claude",
        spawn: Callable[..., subprocess.CompletedProcess] | None = None,
        capture_usage: bool = False,
    ) -> None:
        self.claude_bin = claude_bin
        self.spawn = spawn if spawn is not None else _spawn_resolved
        #: B200: ask for the JSON-lines stream so every ``rate_limit_event`` is visible.
        #: Off by default, which keeps the Delivery 2 argv byte-for-byte.
        self.capture_usage = bool(capture_usage)

    # -- argv and environment ------------------------------------------------

    def build_argv(self, request: RunRequest) -> list[str]:
        """The order frozen in SPEC 5.4.3 (B25), plus ``--max-budget-usd`` after ``--max-turns``.

        B200: with ``capture_usage`` the ``--output-format json`` pair becomes
        ``--output-format stream-json --verbose`` in that same position; nothing else moves.

        B216/D35: the prompt is NOT here. ``claude --print`` reads it from stdin, and every
        flag below is bounded while the prompt is not -- an implement prompt carries a diff and
        a gate log. The option terminator went with it: nothing follows the flags, so a prompt
        beginning with ``-`` can no longer be read as one.
        """
        output_format: list[str] = (
            ["--output-format", "stream-json", "--verbose"]
            if self.capture_usage
            else ["--output-format", "json"]
        )
        argv: list[str] = [
            self.claude_bin,
            "--print",
            *output_format,
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
        settings = deny_settings(request.deny_read)
        if settings is not None:
            # B218: `--setting-sources` first, so the operator's own ~/.claude and the
            # repository's .claude/ cannot widen what this call may touch; then the deny rules.
            argv.append("--setting-sources")
            argv.append("")
            argv.append("--settings")
            argv.append(settings)
        if request.system_prompt is not None:
            argv.append("--system-prompt")
            argv.append(request.system_prompt)
        for directory in request.add_dirs:
            argv.append("--add-dir")
            argv.append(str(directory))
        return argv

    def build_env(self) -> dict[str, str]:
        """The parent environment minus every key in :data:`STRIPPED_ENV_KEYS` (B26)."""
        base = environ_snapshot()
        return {key: value for key, value in base.items() if key not in STRIPPED_ENV_KEYS}

    # -- the call ------------------------------------------------------------

    def run(self, request: RunRequest) -> RunResult:
        argv = self.build_argv(request)
        too_long = argv_too_long(argv)
        if too_long is not None:
            # B216: refused here, legibly, rather than as cmd.exe's bare "The command line is
            # too long." on stderr -- which arrives as a non-zero exit and reads like a failed
            # model call. Nothing is spent.
            return self._failure(request, EXIT_ARGV_TOO_LONG, too_long)
        env = self.build_env()
        try:
            proc = self.spawn(
                argv,
                cwd=str(request.cwd),
                env=env,
                input=request.prompt,
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
        data, usage = self.parse_stdout(stdout)

        if exit_code != 0:
            # B119: exhaustion is an outcome with a reset time, not a generic failure.
            combined = f"{stdout}\n{stderr}"
            if RATE_LIMIT_PATTERN.search(combined):
                return self._failure(
                    request,
                    exit_code,
                    stderr or stdout,
                    reset_at=parse_reset_at(combined),
                    usage=usage,
                )
            # A budget stop (D19: `subtype` error_max_budget_usd) exits 1 with a complete JSON
            # result; keep its cost, turns and reason rather than dumping the JSON as the error.
            if isinstance(data, dict) and data.get("is_error"):
                return self._from_json(request, data, stderr, exit_code, usage=usage)
            return self._failure(request, exit_code, stderr or stdout, usage=usage)

        if not isinstance(data, dict):
            return self._failure(
                request,
                exit_code,
                stderr or "claude produced unparseable stdout",
                usage=usage,
            )

        return self._from_json(request, data, stderr, exit_code, usage=usage)

    # -- parsing -------------------------------------------------------------

    def parse_stdout(self, stdout: str) -> tuple[dict | None, dict | None]:
        """``(result object, usage)``: JSON lines when streaming (B201), one object otherwise."""
        if self.capture_usage:
            return parse_stream(stdout)
        try:
            data: Any = json.loads(stdout)
        except (ValueError, TypeError):
            data = None
        return (data if isinstance(data, dict) else None), None

    def _from_json(
        self,
        request: RunRequest,
        data: dict,
        stderr: str,
        exit_code: int,
        *,
        usage: dict | None = None,
    ) -> RunResult:
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
            usage=usage,
        )

    def _failure(
        self,
        request: RunRequest,
        exit_code: int,
        stderr: str,
        *,
        reset_at: str | None = None,
        usage: dict | None = None,
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
            usage=usage,
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
