"""Disposable clone lifecycle (SPEC §5.7) and the fast-forward-only fork sync (handoff §4.4)."""

from __future__ import annotations

import logging
import os
import re
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from harness.clock import Clock
from harness.errors import CloneError, ForkDiverged, PreflightFailed
from harness.gates import run_command
from harness.store import WorkItem

log = logging.getLogger("harness")

_BYTES_PER_GB = 1024 ** 3

#: The one refspec ``sync_fork`` ever hands to ``push``: upstream's main onto the fork's main.
FORK_SYNC_REFSPEC = "upstream/main:refs/heads/main"

GitRunner = Callable[[list[str], Path], tuple[int, str, str]]


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


def branch_name_for(item: WorkItem) -> str:
    """``harness/<type>-<issue>-<slug>`` — always under the ``harness/`` namespace (B45)."""
    type_ = "fix" if item.kind == "issue" else "chore"
    return f"harness/{type_}-{item.issue_number or item.id}-{_slugify(item.title)}"


def _source_repo(config) -> str:
    """The repository clones come from: the fork at tier 2 (D2 §4.4), else the product repo.

    B220/D40: the fork is only the right source while the harness can keep it current, and
    that takes a write credential -- `sync_fork` fast-forwards it through `gh.push_ref`, which
    tier 2 alone can call. Below tier 2 the fork is frozen wherever it was last left, so
    cloning it pins every proposal, diff and gate run to a stale base and silently reports it
    as the product repository. Measured: a tier-0 trial cloned a fork twelve commits behind
    upstream. With no push there is no reason to prefer the fork at all.
    """
    fork = (getattr(config, "fork_repo", "") or "").strip()
    if fork and int(getattr(config, "permission_tier", 0) or 0) >= 2:
        return fork
    return config.repo


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
        git_runner: GitRunner | None = None,
    ) -> None:
        self.config = config
        self.clock = clock
        self.git_bin = git_bin
        # The default is unauthenticated https to the fork when one is configured, else to the
        # product repository. There is no ssh branch and no credential branch: the only way to
        # point this elsewhere is an explicit override, used by tests.
        self.clone_url = clone_url or f"https://github.com/{_source_repo(config)}.git"
        self._free_bytes = free_bytes
        self._git_runner = git_runner

    # -- internals ---------------------------------------------------------

    def _run_git(self, argv: list[str], cwd: Path) -> tuple[int, str, str]:
        run = self._git_runner if self._git_runner is not None else run_command
        return run([self.git_bin, *argv], cwd)

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

    def _fork_point(self, clone_path: Path, main_sha: str, item: WorkItem) -> str:
        """The base of a re-acquired branch: where it left main, else what the store recorded."""
        code, out, _err = self._run_git(["merge-base", "HEAD", main_sha], clone_path)
        sha = out.strip() if code == 0 else ""
        if sha:
            return sha
        recorded = getattr(item, "base_sha", None)
        return str(recorded) if recorded else main_sha

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

    def acquire(
        self,
        item: WorkItem,
        *,
        branch: str | None = None,
        from_fork: bool = False,
    ) -> Lease:
        """Fresh clone under ``runs_dir/item-<id>/clone``; ``branch`` re-acquires an existing
        one."""
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
        head_sha = out.strip()
        if not head_sha:
            raise CloneError("git rev-parse HEAD produced no sha")

        if branch:
            code, _out, err = self._run_git(["fetch", "origin", branch], clone_path)
            if code != 0:
                raise CloneError(
                    f"git fetch origin {branch} failed ({code}): {err.strip()[-2000:]}"
                )
            code, _out, err = self._run_git(["switch", branch], clone_path)
            if code != 0:
                raise CloneError(f"git switch {branch} failed ({code}): {err.strip()[-2000:]}")
            base_sha = self._fork_point(clone_path, head_sha, item)
            branch_name = branch
        else:
            base_sha = head_sha
            branch_name = branch_name_for(item)
            code, _out, err = self._run_git(["switch", "-c", branch_name], clone_path)
            if code != 0:
                raise CloneError(
                    f"git switch -c {branch_name} failed ({code}): {err.strip()[-2000:]}"
                )

        log.info(
            "clone acquired run_id=%s base_sha=%s branch=%s existing=%s from_fork=%s",
            run_id,
            base_sha,
            branch_name,
            bool(branch),
            from_fork,
        )
        return Lease(run_id=run_id, path=clone_path, base_sha=base_sha, branch=branch_name)

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


# -- fork sync (D2 §4.4, B105) -------------------------------------------------------------


def sync_fork(
    config,
    *,
    workdir: Path,
    push: Callable[[Path, str], None],
    git_runner: GitRunner | None = None,
) -> str:
    """Fast-forward the fork's ``main`` from upstream or raise ``ForkDiverged`` (B105); the sha."""
    fork_repo = (getattr(config, "fork_repo", "") or "").strip()
    if not fork_repo:
        raise CloneError("sync_fork: FORK_REPO is not configured; there is no fork to sync")
    upstream_repo = (getattr(config, "upstream_repo", "") or "").strip() or config.repo
    run = git_runner if git_runner is not None else run_command

    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    bare = workdir / "fork.git"
    if bare.exists():
        shutil.rmtree(bare, onexc=_on_rmtree_error)

    fork_url = f"https://github.com/{fork_repo}.git"
    upstream_url = f"https://github.com/{upstream_repo}.git"

    def git(argv: list[str], cwd: Path) -> str:
        code, out, err = run(["git", *argv], cwd)
        if code != 0:
            raise CloneError(
                f"sync_fork: git {' '.join(argv[:2])} failed ({code}): {err.strip()[-2000:]}"
            )
        return out.strip()

    git(["clone", "--bare", "--no-tags", fork_url, str(bare)], workdir)
    git(["remote", "add", "upstream", upstream_url], bare)
    git(["fetch", "origin", "refs/heads/main:refs/remotes/origin/main"], bare)
    git(["fetch", "upstream", "refs/heads/main:refs/remotes/upstream/main"], bare)
    fork_sha = git(["rev-parse", "origin/main"], bare)
    upstream_sha = git(["rev-parse", "upstream/main"], bare)

    if fork_sha == upstream_sha:
        log.info("sync_fork: %s main already at upstream %s", fork_repo, fork_sha)
        return fork_sha

    code, _out, _err = run(["git", "merge-base", "--is-ancestor", fork_sha, upstream_sha], bare)
    if code != 0:
        raise ForkDiverged(
            f"fork {fork_repo} main is at {fork_sha} but upstream {upstream_repo} main is at "
            f"{upstream_sha}; not a fast-forward, nothing pushed (B105)"
        )

    push(bare, FORK_SYNC_REFSPEC)
    log.info("sync_fork: %s main fast-forwarded %s -> %s", fork_repo, fork_sha, upstream_sha)
    return upstream_sha
