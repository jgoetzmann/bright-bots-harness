"""PR-scoped prettier invocation. Never pointed at the whole tree (I-7)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Callable, Sequence

from harness.clone import GUARD_GIT
from harness.gates import run_command

log = logging.getLogger("harness")


def _npx() -> str:
    return "npx.cmd" if sys.platform == "win32" else "npx"


def all_changed_paths(
    clone: Path,
    base_sha: str,
    git_runner: Callable[[list[str], Path], tuple[int, str, str]] | None = None,
) -> list[str]:
    """Every path that differs from ``base_sha``, deletions included, plus untracked files.

    This is the change set, and it is a different list from the one prettier gets (B222).
    :func:`changed_paths` filters to added and modified, because formatting a file that no
    longer exists is an error. The "did anything change?" question and the forbidden-diff guard
    both read this list instead, or a deletion-only change would be invisible to them: an
    implement run that only deleted files would commit nothing, and a diff removing a CI
    workflow would pass the guard that exists to stop it.
    """
    return _diff_names(clone, base_sha, git_runner, diff_filter=None)


def changed_paths(
    clone: Path,
    base_sha: str,
    git_runner: Callable[[list[str], Path], tuple[int, str, str]] | None = None,
) -> list[str]:
    """Added and modified paths versus ``base_sha``, plus untracked files, deduped and sorted.

    Deletions are excluded (``--diff-filter=AM``), because formatting a path that no longer
    exists is an error. For the change set itself, use :func:`all_changed_paths` (B222).
    """
    return _diff_names(clone, base_sha, git_runner, diff_filter="AM")


def _diff_names(
    clone: Path,
    base_sha: str,
    git_runner: Callable[[list[str], Path], tuple[int, str, str]] | None,
    *,
    diff_filter: str | None,
) -> list[str]:
    run = git_runner if git_runner is not None else run_command
    root = Path(clone)
    seen: list[str] = []

    # This list is the protected-path check's path arm, so it reads history the way a push
    # sends it (B312). `GUARD_GIT` keeps a `refs/replace/` entry for ``base_sha`` from swapping
    # in a tree that already holds the change, and `--no-renames` lists a move out of
    # `.github/` as the deletion it is; with rename detection `--name-only` names only the new
    # path.
    # Both lists are read NUL-separated (`-z`). Line by line, git C-quotes a name holding a
    # double quote, a backslash or a control character even with `core.quotepath=off`, and
    # unquoting is lossy, so such a name reaches no file and the tree guard cannot remove it
    # (B390).
    argv = [*GUARD_GIT, "diff", "--name-only", "--no-renames", "-z"]
    if diff_filter is not None:
        argv.append(f"--diff-filter={diff_filter}")
    argv.append(base_sha)
    code, out, err = run(argv, root)
    if code != 0:
        log.warning("git diff against %s failed (%s): %s", base_sha, code, err.strip()[:500])
    else:
        seen.extend(out.split("\0"))

    code, out, err = run([*GUARD_GIT, "ls-files", "-z", "--others", "--exclude-standard"], root)
    if code != 0:
        log.warning("git ls-files --others failed (%s): %s", code, err.strip()[:500])
    else:
        seen.extend(out.split("\0"))

    paths: list[str] = []
    for path in seen:
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
