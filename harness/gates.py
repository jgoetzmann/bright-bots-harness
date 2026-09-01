"""The product repository's own gate sequence (SPEC §5.11). Never widened, skipped, or retimed."""

from __future__ import annotations

import hashlib
import logging
import shutil
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

def _git_bash() -> str | None:
    """Git for Windows' bash, found relative to ``git`` itself, or None."""
    git = shutil.which("git")
    if not git:
        return None
    for parent in Path(git).resolve().parents:
        for candidate in (parent / "bin" / "bash.exe", parent / "usr" / "bin" / "bash.exe"):
            if candidate.is_file():
                return str(candidate)
    return None


def _resolve_argv(argv: Sequence[str]) -> tuple[str, ...]:
    """Windows: node shims are batch files (.cmd), and bare ``bash`` is the WSL launcher.

    ``C:\Windows\System32\bash.exe`` precedes Git Bash on PATH and fails without a WSL distro,
    so the product's ``bash scripts/*.sh`` gates run under the bash that ships with git.
    """
    if sys.platform != "win32" or not argv:
        return tuple(argv)
    if argv[0] in ("npm", "npx"):
        return (argv[0] + ".cmd", *argv[1:])
    if argv[0] == "bash":
        found = _git_bash()
        if found:
            return (found, *argv[1:])
    return tuple(argv)


def _tail(text: str) -> str:
    if not text:
        return ""
    return text[-TAIL_CHARS:]


def run_command(argv: list[str], cwd: Path) -> tuple[int, str, str]:
    """``(exit_code, stdout, stderr)`` of one subprocess. No shell, ever."""
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GATE_TIMEOUT_S,
        )
    except FileNotFoundError as exc:
        return 127, "", f"{argv[0]} not found: {exc}"
    except subprocess.TimeoutExpired as exc:
        # A command that times out is red. It is never retried with a longer timeout.
        out = exc.stdout or ""
        err = exc.stderr or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        return 124, out, err + f"\n{argv[0]} timed out after {GATE_TIMEOUT_S}s"
    return proc.returncode, proc.stdout or "", proc.stderr or ""


#: Dependency install, run once per clone before the baseline. Not a gate: it changes nothing
#: about what the seven gates check, it only gives them a tree with ``node_modules`` in it —
#: the product's own pre-push hook runs with ``--skip-install`` for the same reason.
_PREPARE: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("prepare: npm ci", ("npm", "ci", "--no-audit", "--no-fund"), ""),
    ("prepare: backend npm ci", ("npm", "ci", "--no-audit", "--no-fund"), "backend"),
)


def prepare(
    clone: Path,
    *,
    runner: Callable[[list[str], Path], tuple[int, str, str]] | None = None,
) -> list[GateResult]:
    """Install pinned dependencies where a ``package-lock.json`` exists. Skipped otherwise.

    Returns one result per install actually attempted, in the same shape as a gate so the
    evidence can carry it verbatim. An empty list means nothing needed installing.
    """
    run = runner if runner is not None else run_command
    root = Path(clone)
    results: list[GateResult] = []
    for name, argv, subdir in _PREPARE:
        cwd = root / subdir if subdir else root
        if not (cwd / "package-lock.json").is_file():
            continue
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
        log.info("%s exit=%s", name, result.exit_code)
    return results


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
    run = runner if runner is not None else run_command
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
