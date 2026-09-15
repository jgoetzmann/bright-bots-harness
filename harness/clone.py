"""Disposable clone lifecycle, and the fast-forward-only fork sync."""

from __future__ import annotations

import logging
import os
import re
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

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
    """The repository clones come from: the fork at tier 2, else the product repository (B220).

    The fork is the right source only while the harness can keep it current, which takes a
    write credential: `sync_fork` fast-forwards it through `gh.push_ref`, and tier 2 alone can
    call that. Below tier 2 the fork stays wherever it was last left, so cloning it would pin
    every proposal, diff and gate run to a stale base while reporting it as the product
    repository.
    """
    fork = (getattr(config, "fork_repo", "") or "").strip()
    chosen = fork if fork and int(getattr(config, "permission_tier", 0) or 0) >= 2 else config.repo
    # The last place a clone can be pointed at the harness itself (I-18). `load_config` already
    # refuses `REPO == SELF_REPO`, so reaching this means the Config was built some other way:
    # a test rig, a hand-made Config, a future caller.
    self_repo = str(getattr(config, "self_repo", "") or "").strip()
    if self_repo and chosen.strip().lower() == self_repo.lower():
        raise CloneError(
            f"refusing to clone {chosen}: it is the harness's own repository, and the harness "
            "does not work on itself (I-18). Changes to the harness are made by a person."
        )
    return chosen


#: The Windows extended-length prefix and its UNC form, spelled without an escape so no
#: line in this module carries a lone backslash that a later edit could silently break.
SEP = chr(92)
EXTENDED_PREFIX = SEP + SEP + "?" + SEP
EXTENDED_UNC_PREFIX = EXTENDED_PREFIX + "UNC" + SEP


def long_path(path: Path | str) -> str:
    """The same path in the form Windows accepts past ``MAX_PATH`` (B224).

    The extended-length prefix opts one call out of the 260-character limit. It needs a
    fully qualified path with backslash separators, so everything goes through ``abspath``
    first, and it is a no-op on every other platform.
    """
    text = os.path.abspath(str(path))
    if os.name != "nt" or text.startswith(EXTENDED_PREFIX):
        return text
    if text.startswith(SEP + SEP):
        return EXTENDED_UNC_PREFIX + text[2:]
    return EXTENDED_PREFIX + text


#: `core.hooksPath` for a harness clone: a path that holds no hooks, so none can fire (B229).
#: A directory that does not exist is what git itself documents for turning hooks off.
HOOKS_OFF = "no-hooks"


# -- what the harness never publishes (I-15) -------------------------------------------------

#: The path prefixes no harness commit may carry to a remote (B296). Matched against
#: `normalise_repo_path`, which prepends "/", so `.github/x` at the top of the tree matches and
#: `docs/github-setup.md` does not. All of `.github/`, since composite actions,
#: `dependabot.yml` and `CODEOWNERS` steer CI and review as a workflow does, and the machine
#: PAT carries `workflow`. This is the one definition: `implement.FORBIDDEN_DIFF_PATHS`, the
#: push guard in `gh.py`, the handoff in `deliver.py` and revise all read it, and
#: `local/preflight.py` holds `local/watchdog-bb.ps1` to the same prefix.
PROTECTED_PUSH_PATHS: tuple[str, ...] = ("/.github/",)

#: The author emails the harness writes (B139). The first is `deliver.GIT_IDENTITY`'s, used by
#: the rebase; the second is what `implement.COMMIT` writes.
HARNESS_AUTHOR_EMAILS: tuple[str, ...] = ("harness@brightboost-harness", "harness@localhost")

#: How many commits the walk takes from a branch tip (B297). A walk that takes this many
#: harness commits without reaching anyone else's refuses rather than pass the rest unchecked;
#: a real branch carries a handful.
PROTECTED_SCAN_COMMITS = 100

#: `git log`'s record and field separators for the walk (ASCII RS and US). Git C-quotes every
#: path holding a control character, so neither can appear on a path line, and no filename can
#: forge the start of a commit record.
_RECORD = chr(30)
_FIELD = chr(31)

#: How every guard-side git read begins: the walk, the path diffs and the author-blind range
#: check (B312). The model can write the clone's own files with Bash, and each option takes
#: away one way those files could show a check a different history from the one a push sends.
#: `--no-replace-objects` ignores `refs/replace/`; `core.commitGraph=false` reads parents and
#: trees from the commits themselves rather than a cache file beside them; `core.quotepath=off`
#: passes a non-ASCII path through as itself. Grafts and a shallow file have no such switch, so
#: `substituted_history` refuses a clone that has either.
GUARD_GIT: tuple[str, ...] = (
    "git", "--no-replace-objects", "-c", "core.commitGraph=false", "-c", "core.quotepath=off",
)

#: The three things `substituted_history` asks git about. `local/watchdog-bb.ps1` asks the same
#: three, and `local/preflight.py` holds it to them.
SUBSTITUTION_PROBES: tuple[str, ...] = ("refs/replace/", "--is-shallow-repository", "info/grafts")


def normalise_repo_path(path: str) -> str:
    """A repository path in the one form the protected-path match reads (B296).

    Backslashes become slashes, a `./` prefix goes, and a leading "/" is prepended, which makes
    a top-level `.github/` match and keeps `src/dotgithub/` from matching. Surrounding quotes
    are stripped, because git C-quotes a path holding a double quote, a backslash or a control
    character, and the opening quote must not carry such a path past the match.
    """
    text = str(path).replace(SEP, "/").strip().strip("`").strip().strip('"')
    while text.startswith("./"):
        text = text[2:]
    return "/" + text.lstrip("/")


def protected_paths_in(paths: Iterable[str]) -> list[str]:
    """The paths in ``paths`` no harness commit may publish, in the order given (B296)."""
    return [
        str(path)
        for path in paths
        if str(path).strip()
        and any(marker in normalise_repo_path(path) for marker in PROTECTED_PUSH_PATHS)
    ]


@dataclass(frozen=True)
class HarnessCommit:
    """One commit the walk attributed to the harness: its sha, author email and paths."""

    sha: str
    email: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class CommitWalk:
    """What `walk_harness_commits` found at the top of a ref (B297).

    ``commits`` is the harness's run of commits from the tip, newest first. ``stopped_at`` is
    the first commit someone else authored, where the mainline begins, or "" when the walk
    reached the root. ``capped`` means it took `PROTECTED_SCAN_COMMITS` harness commits and
    there were more, so it cannot vouch for the ref.
    """

    commits: tuple[HarnessCommit, ...]
    stopped_at: str
    capped: bool

    def protected(self) -> list[tuple[str, str]]:
        """``(sha, path)`` for every protected path a harness commit touches, tip first."""
        return [(c.sha, path) for c in self.commits for path in protected_paths_in(c.paths)]


def harness_walk_argv(ref: str) -> list[str]:
    """The one `git log` the walk runs; `local/watchdog-bb.ps1` runs the same (B304).

    `--name-only --no-renames` lists a rename as the delete and the add it is, and lists
    deletions, since removing a workflow is as much a change as editing one. `--first-parent`
    keeps the walk on the branch's own line; `--diff-merges=first-parent` states what git 2.31
    and later already do, giving a merge commit the paths it brought in. It begins with
    `GUARD_GIT`, so neither `refs/replace/` nor the commit-graph cache can show the walk a
    different history from the one the push sends, and `log.showRoot=true` keeps clone config
    from hiding a root commit's paths. It asks for one more than the cap, to tell "the cap"
    from "past it".
    """
    return [
        "git", "--no-replace-objects", "-c", "core.commitGraph=false", "-c", "core.quotepath=off",
        "-c", "log.showRoot=true", "log",
        "--format=%x1e%H%x1f%ae", "--name-only", "--no-renames", "--first-parent",
        "--diff-merges=first-parent", "-n", str(PROTECTED_SCAN_COMMITS + 1), ref, "--",
    ]


def substituted_history(cwd: Path | str, run: GitRunner | None = None) -> list[str]:
    """Why the clone's history cannot be read as a push would send it; [] if it can (B312).

    A clone can show git a history its objects do not hold, through files the model can write
    with Bash: a `refs/replace/` entry swaps one object for another, `info/grafts` rewrites a
    commit's parents, and a shallow file cuts them off. A push sends the real objects either
    way. `GUARD_GIT` switches off the first for one command, but nothing on a command line
    switches off the other two, so a check refuses such a clone. Git failing to answer is a
    reason too.
    """
    runner = run if run is not None else run_command
    root = Path(cwd)
    replace_refs, shallow_query, grafts_path = SUBSTITUTION_PROBES
    code, out, err = runner(
        ["git", "for-each-ref", "--format=%(refname)", replace_refs], root
    )
    if code != 0:
        return [f"git for-each-ref failed ({code}): {(err or out).strip()[-300:]}"]
    reasons: list[str] = []
    replaced = [line.strip() for line in out.splitlines() if line.strip()]
    if replaced:
        reasons.append(f"{len(replaced)} {replace_refs} ref(s), {replaced[0]} first")
    code, out, err = runner(["git", "rev-parse", shallow_query, "--git-path", grafts_path], root)
    answers = [line.strip() for line in out.splitlines() if line.strip()]
    if code != 0 or len(answers) < 2 or answers[0] not in ("true", "false"):
        return reasons + [f"git rev-parse failed ({code}): {(err or out).strip()[-300:]}"]
    if answers[0] == "true":
        reasons.append("a shallow history")
    grafts = Path(answers[1])
    if (grafts if grafts.is_absolute() else root / grafts).exists():
        reasons.append(f"a grafts file at {answers[1]}")
    return reasons


def _refuse_substituted(cwd: Path | str, ref: str, run: GitRunner) -> None:
    substituted = substituted_history(cwd, run)
    if substituted:
        raise CloneError(
            f"refusing to read {ref}: the clone's history is substituted ("
            + "; ".join(substituted)
            + "), so what git shows is not what a push would send (D67)"
        )


def parse_harness_walk(out: str) -> CommitWalk:
    """Read `harness_walk_argv`'s output, stopping at the first commit a harness email did not
    author (B297). Case is folded: a case-variant of a harness email keeps the walk going and
    is checked, because stopping early is the direction that checks less."""
    commits: list[HarnessCommit] = []
    for record in out.split(_RECORD)[1:]:
        lines = record.splitlines()
        sha, _sep, email = (lines[0] if lines else "").partition(_FIELD)
        sha, email = sha.strip(), email.strip()
        if email.lower() not in HARNESS_AUTHOR_EMAILS:
            return CommitWalk(tuple(commits), sha, False)
        if len(commits) >= PROTECTED_SCAN_COMMITS:
            return CommitWalk(tuple(commits), "", True)
        paths = tuple(line.strip() for line in lines[1:] if line.strip())
        commits.append(HarnessCommit(sha, email, paths))
    return CommitWalk(tuple(commits), "", False)


def walk_harness_commits(cwd: Path | str, ref: str, run: GitRunner | None = None) -> CommitWalk:
    """The commits the harness authored at the top of ``ref``, and what they touch (B297).

    The question is which commits the harness wrote, not what the branch changes against a
    recorded base: after `deliver._rebase` that base differs from the branch by everything
    upstream merged since, upstream's own `.github/workflows/ci-cd.yml` included, so a guard
    asking it would refuse every delivery. A rebase rewrites the committer and keeps the
    author, so upstream's commits keep upstream's authors and the walk stops at the first of
    them. Raises `CloneError` when git fails, and when the clone's history is substituted.
    """
    runner = run if run is not None else run_command
    _refuse_substituted(cwd, ref, runner)
    code, out, err = runner(harness_walk_argv(ref), Path(cwd))
    if code != 0:
        raise CloneError(f"git log {ref} failed ({code}): {(err or out).strip()[-500:]}")
    return parse_harness_walk(out)


def protected_paths_above(
    cwd: Path | str, ref: str, anchors: Sequence[str], run: GitRunner | None = None
) -> list[str]:
    """The protected paths any commit on ``ref`` touches that none of ``anchors`` holds,
    whoever authored it; sorted, once each (B313).

    The author-blind half, for the two places that hold a commit the model cannot have moved:
    deliver, whose `_rebase` has just fetched the upstream commit the branch now sits on, and a
    handoff, which fetches the fork's `main` at check time. Upstream's own commits sit below
    the anchor and are relayed. Everything above it is read, including a merged side branch and
    a change added and removed again inside the range. Raises `CloneError` when git fails or
    the history is substituted, since a range that could not be read was not checked.
    """
    runner = run if run is not None else run_command
    if not [anchor for anchor in anchors if str(anchor).strip()]:
        raise CloneError(f"no commit to read {ref} above, so nothing on it was checked")
    _refuse_substituted(cwd, ref, runner)
    argv = [
        *GUARD_GIT, "log", "--format=%x1e%H", "--name-only", "--no-renames",
        "--diff-merges=first-parent", ref, "--not", *anchors, "--",
    ]
    code, out, err = runner(argv, Path(cwd))
    if code != 0:
        raise CloneError(
            f"git log {ref} --not {' '.join(anchors)} failed ({code}): "
            f"{(err or out).strip()[-500:]}"
        )
    paths = [ln for ln in out.splitlines() if ln.strip() and not ln.startswith(_RECORD)]
    return sorted(set(protected_paths_in(paths)))


def _on_rmtree_error(func: Callable[..., object], path: str, excinfo: BaseException) -> None:
    """Clear the read-only bit, then retry past ``MAX_PATH``; re-raise if it still will not go.

    Git marks everything under .git/objects read-only on Windows, so a plain rmtree of a clone
    fails partway through and leaves a half-deleted directory behind. A path under a nested
    ``node_modules`` chain can also pass ``MAX_PATH``, which is why the retry uses `long_path`.

    The final call re-raises rather than returning, so ``shutil.rmtree`` cannot report success
    over a partial delete and leave the next ``acquire`` to fail on a non-empty destination
    (B224).
    """
    try:
        os.chmod(long_path(path), stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass
    try:
        func(long_path(path))
        return
    except OSError:
        pass
    func(path)  # let the real error out of rmtree


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
        run_id: str | None = None,
        read_only: bool = False,
    ) -> Lease:
        """Fresh clone under ``runs_dir/item-<id>/clone``; ``branch`` re-acquires an existing
        one.

        ``run_id`` names the directory for a read that belongs to no work item, such as an
        `ask`. ``read_only`` stays on the default branch and cuts none of its own, so a stage
        that only reads cannot leave a branch behind (B274).
        """
        blockers = self.preflight()
        if blockers:
            raise PreflightFailed("; ".join(blockers))

        run_id = run_id or f"item-{item.id}"
        run_dir = self._assert_under_runs_dir(Path(self.config.runs_dir) / run_id)
        clone_path = self._assert_under_runs_dir(run_dir / "clone")

        if clone_path.exists():
            shutil.rmtree(long_path(clone_path), onexc=_on_rmtree_error)
        if clone_path.exists():
            # Never hand a half-deleted directory to `git clone`, whose own message for it
            # names neither the leftovers nor the reason (B224). The count is best-effort:
            # whatever defeated the removal can defeat the walk, and a failure to count must
            # not replace this message with a traceback.
            try:
                leftovers: object = sum(1 for _ in clone_path.rglob("*"))
            except OSError:
                leftovers = "an unknown number of"
            raise CloneError(
                f"could not clear the previous clone at {clone_path}: {leftovers} entries "
                "remain. On Windows this is usually a file still open in another process."
            )
        run_dir.mkdir(parents=True, exist_ok=True)

        code, _out, err = self._run_git(
            ["clone", "--no-tags", self.clone_url, str(clone_path)], run_dir
        )
        if code != 0:
            raise CloneError(f"git clone failed ({code}): {err.strip()[-2000:]}")

        # No git hook runs in a harness clone (B229). `npm ci` installs the product's husky
        # hooks, and a pre-push hook then runs inside `git push`, in an environment the product
        # does not test, duplicating a gate the harness has already run and recorded verbatim.
        # The seven pinned gates are the harness's definition of "it works".
        code, _out, err = self._run_git(["config", "core.hooksPath", str(HOOKS_OFF)], clone_path)
        if code != 0:
            raise CloneError(f"git config core.hooksPath failed ({code}): {err.strip()[-2000:]}")

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
        elif read_only:
            base_sha = head_sha
            branch_name = ""
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


# -- fork sync (B105) -----------------------------------------------------------------------


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
