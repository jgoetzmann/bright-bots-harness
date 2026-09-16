"""The ``claude`` CLI backend, including the usage stream (B200-B202)."""

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
from harness.runner.base import (
    RATE_LIMIT_PATTERN,
    USAGE_OVERAGE_IN_USE,
    USAGE_REJECTED,
    USAGE_REJECTED_RESETS_AT,
    RunRequest,
    RunResult,
    exhausted_reset,
    usage_rejected,
)

#: Removed from the child environment (B26): the harness runs on a subscription, and a stray
#: key would silently move its calls to the API billing pool.
STRIPPED_ENV_KEYS: tuple[str, ...] = ("ANTHROPIC_API_KEY",)

#: How much of stderr survives into ``RunResult.error``.
STDERR_TAIL_CHARS = 2000

#: The error of a refusal that came with no words at all: the usage signal said ``rejected``
#: and the CLI printed nothing else (D71). The subtype on a refusal reads "success", which
#: says nothing.
REFUSED_ERROR = "the subscription refused the call (rate_limit_event status: rejected)"

#: Synthetic exit codes for the cases where the process never reported one.
EXIT_TIMEOUT = 124
EXIT_NOT_EXECUTABLE = 127
EXIT_ARGV_TOO_LONG = 126

#: The prompt travels on stdin, never in argv (D35). The flags and the system prompt still have
#: to fit: on Windows ``claude`` is an npm ``.CMD`` shim, so the whole command line passes
#: through ``cmd.exe``, whose ceiling is 8191 characters, about a quarter of the 32767
#: ``CreateProcess`` allows. Over it, cmd.exe prints "The command line is too long." and exits
#: non-zero, which reads like a model failure.
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

    It names the longest argument. The one that can realistically grow past the ceiling is
    ``--system-prompt``, and ``prompts/system.md`` is pinned, so the fix is a prompt edit and a
    re-pin.
    """
    limit = argv_limit()
    # One separator per gap, matching how the OS assembles the line.
    length = sum(len(part) for part in argv) + max(0, len(argv) - 1)
    if length <= limit:
        return None
    widest = max(range(len(argv)), key=lambda i: len(argv[i]), default=0)
    culprit = argv[widest] if argv else ""
    name = argv[widest - 1] if widest > 0 and argv[widest - 1].startswith("--") else "argv"
    advice = {
        "--system-prompt": "shorten prompts/system.md and re-pin",
        "--settings": "shorten the deny list (stages.deny_read_paths) or the repository path",
    }.get(name, "shorten it")
    return (
        f"command line is {length} characters; this platform allows {limit}. "
        f"The longest argument is {name} ({len(culprit)} characters): {advice}. "
        "The prompt itself already travels on stdin (D35) and is not the cause."
    )


def deny_settings(paths: Sequence[str]) -> str | None:
    """``--settings`` JSON denying Read on `paths`, or ``None`` when there is nothing to deny.

    The CLI enforces a bare absolute path only (B218): a ``//``-prefixed rule is accepted and
    silently matches nothing, and so is a relative glob such as ``**/.env``.
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


def _refusal_reset(usage: Mapping[str, Any] | None, rejected: bool, text: str) -> str | None:
    """The reset of a refusal: the exhausted window's own reset when the signal said
    ``rejected`` (B396), else whatever the wording carries (B119)."""
    if rejected:
        found = exhausted_reset(usage)
        if found is not None:
            return found
    return parse_reset_at(text)


def _normalise_iso(raw: str) -> str:
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return raw
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


#: The stream event that carries the subscription windows.
RATE_LIMIT_EVENT = "rate_limit_event"

#: The two unified windows the CLI reports, in the order they are stored. One fact in two
#: places: ``harness.ledger.USAGE_WINDOWS`` is the consumer's copy and must stay equal to this
#: one (tests/test_ledger.py pins it). The runner imports no domain module, so neither
#: definition can import the other.
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

    Tolerant (B114): anything missing drops out, and an event with no recognisable window is no
    observation at all rather than a zero one.
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
    if status == USAGE_REJECTED:
        # Kept on a rejection only, so an `allowed` reading keeps its shape. The CLI does not
        # block a call running on extra usage, and the limit that refused may be none of the
        # unified windows (`seven_day_opus`, `overage`), so its own reset is recorded (B404).
        if info.get("isUsingOverage") is True or info.get("overageInUse") is True:
            usage[USAGE_OVERAGE_IN_USE] = True
        limit_reset = _reset_iso(info.get("resetsAt"))
        if limit_reset is not None:
            usage[USAGE_REJECTED_RESETS_AT] = limit_reset
        limit_type = info.get("rateLimitType")
        if isinstance(limit_type, str) and limit_type:
            usage["rate_limit_type"] = limit_type
    return usage


def parse_stream(stdout: str) -> tuple[dict | None, dict | None]:
    """``(result object, usage)`` from ``--output-format stream-json`` output (B201).

    Pure. The last line whose ``type`` is ``"result"`` is the result object, carrying the same
    fields as the non-streaming JSON; every ``rate_limit_event`` updates the usage and the last
    one wins. Lines that are not JSON objects are ignored, and no result line at all gives
    ``(None, usage)``, which the caller treats as unparseable output.
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
        #: Ask for the JSON-lines stream, so every ``rate_limit_event`` is visible (B200). Off
        #: by default, which keeps the plain ``--output-format json`` argv.
        self.capture_usage = bool(capture_usage)

    # -- argv and environment ------------------------------------------------

    def build_argv(self, request: RunRequest) -> list[str]:
        """The frozen argv order (B25).

        With ``capture_usage`` the ``--output-format json`` pair becomes ``--output-format
        stream-json --verbose`` in that same position, and nothing else moves.

        The prompt is not here: ``claude --print`` reads it from stdin, because every flag below
        is bounded while a prompt carrying a diff and a gate log is not. The option terminator
        went with it, and nothing follows the flags, so a prompt beginning with ``-`` cannot be
        read as one.
        """
        output_format: list[str] = (
            ["--output-format", "stream-json", "--verbose"]
            if self.capture_usage
            else ["--output-format", "json"]
        )
        argv: list[str] = [self.claude_bin, "--print", *output_format]
        # What the session is (format, model, effort) precedes what it may spend (turns), what
        # it may do (permission mode, tools) and what it may read (settings, system prompt,
        # add-dir). Model and effort are omitted when unset (B225).
        if request.model:
            argv.append("--model")
            argv.append(str(request.model))
        if request.effort:
            argv.append("--effort")
            argv.append(str(request.effort))
        argv.append("--max-turns")
        argv.append(str(request.max_turns))
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
            # An empty `--setting-sources` first, so the operator's own ~/.claude and the
            # repository's .claude/ cannot widen what this call may touch, then the deny rules.
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
            # Refused here, with a readable reason and nothing spent, rather than as cmd.exe's
            # bare "The command line is too long." on a non-zero exit (B216).
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
        # The subscription's own verdict (B396). On a refusal the CLI can exit 0 with
        # `is_error: true` and `subtype: "success"`, so this is the one signal that cannot be
        # misread; without it, the wording below still classifies.
        rejected = usage_rejected(usage)
        reported_error = isinstance(data, dict) and bool(data.get("is_error"))

        if exit_code != 0:
            # Exhaustion is an outcome with a reset time (B119). With a result line, the
            # wording and the reset are read from the CLI's own message and stderr, as at exit
            # 0: the stream also carries tool output, and a budget stop whose tool output
            # mentions a limit is still a budget stop. Without one, the whole output is all
            # there is.
            if isinstance(data, dict):
                haystack = f"{_as_str(data.get('result')) or ''}\n{stderr}"
            else:
                haystack = f"{stdout}\n{stderr}"
            if rejected or RATE_LIMIT_PATTERN.search(haystack):
                reset_at = _refusal_reset(usage, rejected, haystack)
                if reported_error:
                    return self._from_json(
                        request, data, stderr, exit_code, usage=usage, reset_at=reset_at
                    )
                return self._failure(
                    request,
                    exit_code,
                    (stderr or REFUSED_ERROR) if rejected else (stderr or stdout),
                    reset_at=reset_at,
                    usage=usage,
                )
            # An `is_error` result at a non-zero exit with a complete JSON body, such as
            # `error_max_turns`, keeps its turns and its own message instead of being dumped
            # as stderr (B432).
            if reported_error:
                return self._from_json(request, data, stderr, exit_code, usage=usage)
            return self._failure(request, exit_code, stderr or stdout, usage=usage)

        if not isinstance(data, dict):
            if rejected:
                return self._failure(
                    request,
                    exit_code,
                    stderr or REFUSED_ERROR,
                    reset_at=exhausted_reset(usage),
                    usage=usage,
                )
            return self._failure(
                request,
                exit_code,
                stderr or "claude produced unparseable stdout",
                usage=usage,
            )

        if reported_error:
            # A refusal at exit 0, matched against the CLI's own message and never the whole
            # stream: a successful reply that talks about limits is still a reply (B397).
            message = _as_str(data.get("result")) or ""
            if rejected or RATE_LIMIT_PATTERN.search(message):
                return self._from_json(
                    request,
                    data,
                    stderr,
                    exit_code,
                    usage=usage,
                    reset_at=_refusal_reset(usage, rejected, message),
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
        reset_at: str | None = None,
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
            # What the CLI said, first: it carries its message in `result` and often leaves
            # stderr empty, and on a refusal the subtype reads "success". Then stderr, then the
            # subtype, which is all a result with an empty message reports (B395).
            message = (_as_str(data.get("result")) or "").strip()
            subtype = str(data.get("subtype") or "")
            if message:
                error = redact(message[-STDERR_TAIL_CHARS:])
            elif stderr:
                error = redact(stderr[-STDERR_TAIL_CHARS:])
            elif usage_rejected(usage):
                # Only when the signal said `rejected`; a reset alone is not a refusal (B405).
                error = REFUSED_ERROR
            else:
                error = subtype or "claude reported is_error"
        return RunResult(
            ok=not is_error,
            text=text,
            turns=_as_int(data.get("num_turns")),
            duration_ms=_as_int(data.get("duration_ms")),
            session_id=_as_str(data.get("session_id")),
            exit_code=exit_code,
            transcript=(
                {"role": "user", "content": request.prompt},
                {"role": "assistant", "content": text},
                {"raw": data},
            ),
            error=error,
            reset_at=reset_at,
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
