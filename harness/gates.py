"""The product repository's own gate sequence (SPEC §5.11).

Seven gates, in a fixed order, run against a clone this module never modifies. The whole point of
the module is that it is boring and honest: it does not widen a gate, skip one, extend a timeout,
or stop early when one goes red. Every gate runs on every invocation so the reviewer sees the
complete picture, and a red the harness cannot fix stays red.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

log = logging.getLogger("harness")

TAIL_CHARS = 4000
GATE_TIMEOUT_S = 1800


@dataclass(frozen=True)
class GateResult:
    name: str
    argv: tuple[str, ...]
    exit_code: int
    stdout_tail: str
    stderr_tail: str


# (display name, argv, subdirectory of the clone to run in)
_SEQUENCE: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("npx prisma generate", ("npx", "prisma", "generate"), ""),
    ("npm run lint", ("npm", "run", "lint"), ""),
    ("npm run typecheck", ("npm", "run", "typecheck"), ""),
    ("backend: npm run typecheck", ("npm", "run", "typecheck"), "backend"),
    ("bash scripts/check-prisma-drift.sh", ("bash", "scripts/check-prisma-drift.sh"), ""),
    ("npm run test:unit", ("npm", "run", "test:unit"), ""),
    ("npm run build", ("npm", "run", "build"), ""),
)

GATE_NAMES: tuple[str, ...] = tuple(name for name, _argv, _sub in _SEQUENCE)


def _resolve_argv(argv: Sequence[str]) -> tuple[str, ...]:
    """On Windows the node shims are batch files, so npm and npx need the .cmd suffix."""
    if sys.platform == "win32" and argv and argv[0] in ("npm", "npx"):
        return (argv[0] + ".cmd", *argv[1:])
    return tuple(argv)


def _tail(text: str) -> str:
    if not text:
        return ""
    return text[-TAIL_CHARS:]


def _default_runner(argv: list[str], cwd: Path) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            shell=False,
            capture_output=True,
            text=True,
            timeout=GATE_TIMEOUT_S,
        )
    except FileNotFoundError as exc:
        return 127, "", f"{argv[0]} not found: {exc}"
    except subprocess.TimeoutExpired as exc:
        # A gate that times out is a red gate. It is never retried with a longer timeout.
        out = exc.stdout or ""
        err = exc.stderr or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        return 124, out, err + f"\ngate timed out after {GATE_TIMEOUT_S}s"
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def run_sequence(
    clone: Path,
    *,
    baseline: bool,
    runner: Callable[[list[str], Path], tuple[int, str, str]] | None = None,
) -> list[GateResult]:
    """Run all seven gates against ``clone`` and return one result each, in order.

    ``baseline`` says whether this is the untouched-tree run. It labels the log line; it does not
    change which gates run or how they are judged. A baseline red is pre-existing and belongs in
    the evidence, not in a justification for loosening anything.
    """
    run = runner if runner is not None else _default_runner
    root = Path(clone)
    label = "baseline" if baseline else "post-change"
    results: list[GateResult] = []

    for name, argv, subdir in _SEQUENCE:
        cwd = root / subdir if subdir else root
        resolved = _resolve_argv(argv)
        code, out, err = run(list(resolved), cwd)
        result = GateResult(
            name=name,
            argv=resolved,
            exit_code=int(code),
            stdout_tail=_tail(out),
            stderr_tail=_tail(err),
        )
        results.append(result)
        log.info("gate %s %s exit=%s", label, name, result.exit_code)

    return results


def _first_error_line(result: GateResult) -> str:
    for blob in (result.stderr_tail, result.stdout_tail):
        for line in (blob or "").splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
    return ""


def signature(results: Sequence[GateResult]) -> str:
    """A stable hash of which gates failed and how.

    Two consecutive identical signatures mean the diagnose-and-fix loop is going in circles, and
    the implement stage stops rather than burning the remaining retries on the same wall.
    """
    failing = [r for r in results if r.exit_code != 0]
    if not failing:
        return ""
    payload = "\n".join(f"{r.name}|{_first_error_line(r)}" for r in failing)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def is_green(results: Sequence[GateResult]) -> bool:
    return all(r.exit_code == 0 for r in results)
