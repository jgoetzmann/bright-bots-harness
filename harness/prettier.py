"""PR-scoped prettier invocation (SPEC §5.11).

Scoped to the files this change actually added or modified, matching the product repo's own
``scripts/pr-review-prettier-check.sh``. It is never pointed at the whole tree and never at the
repo-wide formatting npm script (§9, I-7): with ``core.autocrlf=true`` and no ``.gitattributes``
on this machine, a tree-wide run reports hundreds of line-ending-only differences in files the
change never touched, which buries the real result and would rewrite files outside the diff.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Callable, Sequence

log = logging.getLogger("harness")

PRETTIER_TIMEOUT_S = 600


def _npx() -> str:
    return "npx.cmd" if sys.platform == "win32" else "npx"


def _default_runner(argv: list[str], cwd: Path) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            shell=False,
            capture_output=True,
            text=True,
            timeout=PRETTIER_TIMEOUT_S,
        )
    except FileNotFoundError as exc:
        return 127, "", f"{argv[0]} not found: {exc}"
    except subprocess.TimeoutExpired:
        return 124, "", f"prettier timed out after {PRETTIER_TIMEOUT_S}s"
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _default_git(argv: list[str], cwd: Path) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["git", *argv],
            cwd=str(cwd),
            shell=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except FileNotFoundError as exc:
        return 127, "", f"git not found: {exc}"
    except subprocess.TimeoutExpired:
        return 124, "", "git timed out"
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def changed_paths(
    clone: Path,
    base_sha: str,
    git_runner: Callable[[list[str], Path], tuple[int, str, str]] | None = None,
) -> list[str]:
    """Added and modified paths versus ``base_sha``, plus untracked files, deduped and sorted.

    Deletions are excluded (``--diff-filter=AM``) because formatting a path that no longer exists
    is an error, not a no-op.
    """
    run = git_runner if git_runner is not None else _default_git
    root = Path(clone)
    seen: list[str] = []

    code, out, err = run(["diff", "--name-only", "--diff-filter=AM", base_sha], root)
    if code != 0:
        log.warning("git diff against %s failed (%s): %s", base_sha, code, err.strip()[:500])
    else:
        seen.extend(out.splitlines())

    code, out, err = run(["ls-files", "--others", "--exclude-standard"], root)
    if code != 0:
        log.warning("git ls-files --others failed (%s): %s", code, err.strip()[:500])
    else:
        seen.extend(out.splitlines())

    paths: list[str] = []
    for raw in seen:
        path = raw.strip().strip('"')
        if path and path not in paths:
            paths.append(path)
    return sorted(paths)


def write_and_check(
    clone: Path,
    paths: Sequence[str],
    runner: Callable[[list[str], Path], tuple[int, str, str]] | None = None,
) -> tuple[bool, str]:
    """Format the given paths, then verify them. Returns ``(ok, combined_output)``.

    Both invocations name the paths explicitly after ``--`` so a path beginning with a dash cannot
    be read as a flag, and so the scope is exactly the change set.
    """
    scoped = [p for p in paths if p and p.strip()]
    if not scoped:
        return True, ""

    run = runner if runner is not None else _default_runner
    root = Path(clone)
    npx = _npx()
    chunks: list[str] = []

    write_argv = [npx, "prettier", "--write", "--ignore-unknown", "--", *scoped]
    code, out, err = run(write_argv, root)
    chunks.append(f"$ {' '.join(write_argv)}\n{out}{err}\nexit {code}")
    if code != 0:
        log.warning("prettier write failed with exit %s", code)
        return False, "\n\n".join(chunks)

    check_argv = [npx, "prettier", "--check", "--ignore-unknown", "--", *scoped]
    code, out, err = run(check_argv, root)
    chunks.append(f"$ {' '.join(check_argv)}\n{out}{err}\nexit {code}")

    return code == 0, "\n\n".join(chunks)
