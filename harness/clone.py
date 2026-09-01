"""Disposable clone lifecycle (SPEC §5.7).

The clone is the only place the harness ever writes code. It is created under ``runs_dir``, over
https with no credential, and it is thrown away when the item is done. Two properties are load
bearing and are asserted rather than assumed:

* the remote URL is unauthenticated https, never ssh and never credential-bearing (B42);
* nothing outside ``config.runs_dir`` is ever created or removed (I-8).
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from harness.clock import Clock
from harness.errors import CloneError, PreflightFailed
from harness.store import WorkItem

log = logging.getLogger("harness")

_BYTES_PER_GB = 1024 ** 3
_CLONE_TIMEOUT_S = 1800
_GIT_TIMEOUT_S = 300


@dataclass(frozen=True)
class Lease:
    run_id: str
    path: Path
    base_sha: str
    branch: str


def _slugify(text: str) -> str:
    """Lower-case kebab of ``text``, trimmed to 40 characters on a clean boundary."""
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    if not slug:
        return "work"
    return slug[:40].strip("-") or "work"


def _issue_number(external_ref: str, fallback: int) -> str:
    match = re.search(r"(\d+)", external_ref or "")
    return match.group(1) if match else str(fallback)


def branch_name_for(item: WorkItem) -> str:
    """``harness/<type>-<issue>-<slug>`` — always under the ``harness/`` namespace (B45)."""
    type_ = "fix" if item.kind == "issue" else "chore"
    return f"harness/{type_}-{_issue_number(item.external_ref, item.id)}-{_slugify(item.title)}"


def _on_rmtree_error(func: Callable[..., object], path: str, excinfo: BaseException) -> None:
    """Clear the read-only bit and retry.

    Git marks everything under .git/objects read-only on Windows, so a plain rmtree of a clone
    fails partway through and leaves a half-deleted directory behind.
    """
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        return
    try:
        func(path)
    except OSError:
        return


class CloneManager:
    def __init__(
        self,
        config,
        clock: Clock,
        *,
        git_bin: str = "git",
        clone_url: str | None = None,
        free_bytes: Callable[[Path], int] | None = None,
        git_runner: Callable[[list[str], Path], tuple[int, str, str]] | None = None,
    ) -> None:
        self.config = config
        self.clock = clock
        self.git_bin = git_bin
        # The default is unauthenticated https. There is no ssh branch and no credential branch:
        # the only way to point this elsewhere is an explicit override, used by tests.
        self.clone_url = clone_url or f"https://github.com/{config.repo}.git"
        self._free_bytes = free_bytes
        self._git_runner = git_runner

    # -- internals ---------------------------------------------------------

    def _run_git(self, argv: list[str], cwd: Path) -> tuple[int, str, str]:
        if self._git_runner is not None:
            return self._git_runner(argv, cwd)
        full = [self.git_bin, *argv]
        timeout = _CLONE_TIMEOUT_S if argv and argv[0] == "clone" else _GIT_TIMEOUT_S
        try:
            proc = subprocess.run(
                full,
                cwd=str(cwd),
                shell=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError as exc:
            raise CloneError(f"git binary not found: {self.git_bin}") from exc
        except subprocess.TimeoutExpired as exc:
            raise CloneError(f"git {' '.join(argv)} timed out after {timeout}s") from exc
        return proc.returncode, proc.stdout or "", proc.stderr or ""

    def _free_gb(self) -> float:
        probe = Path(self.config.runs_dir)
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        if self._free_bytes is not None:
            return self._free_bytes(probe) / _BYTES_PER_GB
        return shutil.disk_usage(str(probe)).free / _BYTES_PER_GB

    def _assert_under_runs_dir(self, path: Path) -> Path:
        """I-8. Nothing outside ``runs_dir`` is ever created or removed by this manager."""
        runs_dir = Path(self.config.runs_dir).resolve()
        try:
            target = Path(path).resolve()
        except OSError:
            target = Path(path).absolute()
        if target != runs_dir and not target.is_relative_to(runs_dir):
            raise CloneError(f"refusing to touch {target}; outside runs_dir {runs_dir}")
        return target

    # -- surface -----------------------------------------------------------

    def preflight(self) -> list[str]:
        """Human-readable blockers. An empty list means it is safe to clone."""
        blockers: list[str] = []

        min_gb = float(self.config.min_free_disk_gb)
        try:
            free_gb = self._free_gb()
        except OSError as exc:
            blockers.append(f"disk: cannot determine free space on {self.config.runs_dir}: {exc}")
        else:
            if free_gb < min_gb:
                blockers.append(
                    f"disk: {free_gb:.1f} GB free is below the required {min_gb:.1f} GB "
                    f"on {self.config.runs_dir}"
                )

        if self._git_runner is None and shutil.which(self.git_bin) is None:
            blockers.append(f"git: {self.git_bin} not found on PATH")

        halt_file = Path(self.config.halt_file)
        if halt_file.exists():
            blockers.append(f"halt: {halt_file} exists; run harness resume to clear it")

        return blockers

    def acquire(self, item: WorkItem) -> Lease:
        blockers = self.preflight()
        if blockers:
            raise PreflightFailed("; ".join(blockers))

        run_id = f"item-{item.id}"
        run_dir = self._assert_under_runs_dir(Path(self.config.runs_dir) / run_id)
        clone_path = self._assert_under_runs_dir(run_dir / "clone")

        if clone_path.exists():
            shutil.rmtree(clone_path, onexc=_on_rmtree_error)
        run_dir.mkdir(parents=True, exist_ok=True)

        code, _out, err = self._run_git(
            ["clone", "--no-tags", self.clone_url, str(clone_path)], run_dir
        )
        if code != 0:
            raise CloneError(f"git clone failed ({code}): {err.strip()[-2000:]}")

        code, out, err = self._run_git(["rev-parse", "HEAD"], clone_path)
        if code != 0:
            raise CloneError(f"git rev-parse HEAD failed ({code}): {err.strip()[-2000:]}")
        base_sha = out.strip()
        if not base_sha:
            raise CloneError("git rev-parse HEAD produced no sha")

        branch = branch_name_for(item)
        code, _out, err = self._run_git(["switch", "-c", branch], clone_path)
        if code != 0:
            raise CloneError(f"git switch -c {branch} failed ({code}): {err.strip()[-2000:]}")

        log.info("clone acquired run_id=%s base_sha=%s branch=%s", run_id, base_sha, branch)
        return Lease(run_id=run_id, path=clone_path, base_sha=base_sha, branch=branch)

    def release(self, lease: Lease, *, keep: bool) -> None:
        path = self._assert_under_runs_dir(Path(lease.path))
        run_dir = self._assert_under_runs_dir(Path(self.config.runs_dir) / lease.run_id)

        if keep:
            run_dir.mkdir(parents=True, exist_ok=True)
            marker = run_dir / "KEPT"
            with open(marker, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(f"{path}\n")
            log.info("clone kept at %s", path)
            return

        if path.exists():
            shutil.rmtree(path, onexc=_on_rmtree_error)
        log.info("clone released run_id=%s", lease.run_id)
