"""PR-scoped prettier invocation (SPEC §5.11).

Scoped to the files this change actually added or modified, matching the product repo's own
``scripts/pr-review-prettier-check.sh``. It is never pointed at the whole tree and never at the
repo-wide formatting npm script (§9, I-7): with ``core.autocrlf=true`` and no ``.gitattributes``
on this machine, a tree-wide run reports hundreds of line-ending-only differences in files the
change never touched, which buries the real result and would rewrite files outside the diff.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Callable, Sequence

from harness.gates import run_command

log = logging.getLogger("harness")


def _npx() -> str:
    return "npx.cmd" if sys.platform == "win32" else "npx"


def changed_paths(
    clone: Path,
    base_sha: str,
    git_runner: Callable[[list[str], Path], tuple[int, str, str]] | None = None,
) -> list[str]:
    """Added and modified paths versus ``base_sha``, plus untracked files, deduped and sorted.

    Deletions are excluded (``--diff-filter=AM``) because formatting a path that no longer exists
    is an error, not a no-op.
    """
    run = git_runner if git_runner is not None else run_command
    root = Path(clone)
    seen: list[str] = []

    code, out, err = run(["git", "diff", "--name-only", "--diff-filter=AM", base_sha], root)
    if code != 0:
        log.warning("git diff against %s failed (%s): %s", base_sha, code, err.strip()[:500])
    else:
        seen.extend(out.splitlines())

    code, out, err = run(["git", "ls-files", "--others", "--exclude-standard"], root)
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

    run = runner if runner is not None else run_command
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
