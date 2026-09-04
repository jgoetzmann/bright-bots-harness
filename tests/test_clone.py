"""B42-B48: harness.clone.CloneManager (HARNESS-SPEC 5.7).

Clones are taken from a throwaway local git repository created under tmp_path, so nothing here
reaches the network. The default remote URL is asserted through a recording `git_runner`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.clock import FrozenClock
from harness.clone import CloneManager, Lease
from harness.config import load_config
from harness.store import Store

FROZEN_AT = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
REPO = "Bright-Bots-Initiative/brightboost"
DEFAULT_REMOTE = f"https://github.com/{REPO}.git"
AMPLE_BYTES = 10**13

ENV = {
    "BACKEND": "fake",
    "REPO": REPO,
    "PERMISSION_TIER": "0",
    "ALLOWLIST_LABEL": "harness-ok",
    "WEEKLY_BUDGET_PCT": "40",
    "SESSION_BUDGET_PCT": "15",
    "RESERVE_PCT": "10",
    "WEEKLY_RESET_DAY": "monday",
    "MAX_CONCURRENT_CLONES": "1",
    "MAX_TURNS_DISCOVER": "10",
    "MAX_TURNS_PROPOSE": "30",
    "MAX_TURNS_IMPLEMENT": "80",
    "MAX_TURNS_PACKAGE": "10",
    "MAX_RETRIES_GATES": "2",
    "GITHUB_API_CEILING_PER_HOUR": "50",
    "MIN_FREE_DISK_GB": "5",
    "DB_PATH": "harness.db",
    "RUNS_DIR": "runs",
    "PACKAGES_DIR": "packages",
    "HALT_FILE": "HALT",
    "FULLSEND_ENABLED": "false",
    "WEEKLY_CAP_USD": "25.00",
    "PER_CALL_CAP_USD": "3.00",
    "MAX_CONCURRENT_ITEMS": "1",
    "MAX_REVISE_CYCLES": "3",
    "FORK_REPO": "",
    "UPSTREAM_REPO": REPO,
    "TRUST_FILE": ".harness/trust.txt",
    "NOTIFY_POLL_HOURS": "3",
    "MAX_SUBISSUES": "8",
    "SELF_REPO": "jgoetzmann/bright-bots-harness",
    "TRACKING_ISSUE": "",
    "STORE_BACKEND": "sqlite",
    "WEEKLY_USAGE_STOP_PCT": "90",
    "SESSION_USAGE_STOP_PCT": "70",
    "OVERRUN_PCT": "10",
    "RUN_WINDOW_START": "",
    "RUN_WINDOW_END": "",
    "MODEL": "opus",
    "EFFORT": "xhigh",
    "HARNESS_GITHUB_TOKEN": "",
    "ANTHROPIC_API_KEY": "",
}


def write_env(directory, **overrides):
    values = dict(ENV)
    values.update(overrides)
    path = directory / ".env"
    body = "".join(f"{key}={value}\n" for key, value in values.items())
    path.write_text(body, encoding="utf-8", newline="\n")
    return path


def git(args, cwd):
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, f"git {args} failed: {proc.stdout}{proc.stderr}"
    return proc.stdout.strip()


def make_source_repo(path):
    """A throwaway one-commit repository used as the clone source. Returns its HEAD sha."""
    path.mkdir(parents=True, exist_ok=True)
    git(["init", "-b", "main"], path)
    git(["config", "user.email", "harness-test@example.invalid"], path)
    git(["config", "user.name", "Harness Test"], path)
    git(["config", "commit.gpgsign", "false"], path)
    (path / "README.md").write_text("hello\n", encoding="utf-8", newline="\n")
    git(["add", "README.md"], path)
    git(["commit", "-m", "chore: initial commit"], path)
    return git(["rev-parse", "HEAD"], path)


class RecordingGit:
    """Stands in for subprocess: records argv, creates the clone dir, answers rev-parse."""

    def __init__(self, sha="a" * 40):
        self.sha = sha
        self.calls = []

    def __call__(self, argv, cwd=None, *args, **kwargs):
        argv = list(argv)
        self.calls.append((argv, cwd))
        if "clone" in argv:
            Path(argv[-1]).mkdir(parents=True, exist_ok=True)
        if "rev-parse" in argv:
            return (0, self.sha + "\n", "")
        return (0, "", "")

    @property
    def argv_words(self):
        return [word for argv, _ in self.calls for word in argv]


@pytest.fixture
def clock():
    return FrozenClock(FROZEN_AT)


@pytest.fixture
def config(tmp_path):
    return load_config(env_path=write_env(tmp_path), environ={})


@pytest.fixture
def store(tmp_path, clock):
    store = Store(tmp_path / "h.db", clock)
    store.migrate()
    yield store
    store.close()


@pytest.fixture
def item(store):
    item_id = store.create_work_item(
        kind="issue",
        external_ref="issue:816",
        title="Bundle size check fails on ESM output",
    )
    return store.get_work_item(item_id)


@pytest.fixture
def source_repo(tmp_path):
    """(path, head_sha) of a throwaway local repository to clone from."""
    path = tmp_path / "source"
    return path, make_source_repo(path)


def local_manager(config, clock, source_path):
    return CloneManager(
        config,
        clock,
        clone_url=str(source_path),
        free_bytes=lambda path: AMPLE_BYTES,
    )


# --- B42: the remote URL ----------------------------------------------------------------------


def test_B42_the_default_remote_is_the_public_https_url_of_the_repo(config, clock, item):
    recorder = RecordingGit()
    manager = CloneManager(config, clock, free_bytes=lambda path: AMPLE_BYTES,
                           git_runner=recorder)

    manager.acquire(item)

    assert DEFAULT_REMOTE in recorder.argv_words


def test_B42_no_git_argument_carries_a_credential_or_an_ssh_remote(config, clock, item):
    recorder = RecordingGit()
    manager = CloneManager(config, clock, free_bytes=lambda path: AMPLE_BYTES,
                           git_runner=recorder)

    manager.acquire(item)

    words = recorder.argv_words
    assert words, "acquire ran no git command"
    for word in words:
        assert not word.startswith("ssh://")
        assert not word.startswith("git@")
        assert "git@github.com" not in word
        assert "x-access-token" not in word
    remotes = [word for word in words if "github.com" in word]
    assert remotes, "no github.com remote appeared in any git argv"
    for remote in remotes:
        assert remote.startswith("https://github.com/")
        assert "@" not in remote
        assert "ssh" not in remote


# --- B43: where the clone lands ---------------------------------------------------------------


def test_B43_the_clone_lands_under_runs_dir_at_run_id_clone(config, clock, item, source_repo):
    source_path, _ = source_repo
    manager = local_manager(config, clock, source_path)

    lease = manager.acquire(item)

    assert isinstance(lease, Lease)
    assert lease.run_id == f"item-{item.id}"
    assert lease.path == config.runs_dir / f"item-{item.id}" / "clone"
    assert lease.path.is_dir()
    assert config.runs_dir.resolve() in lease.path.resolve().parents


def test_B43_acquire_creates_nothing_outside_runs_dir(config, clock, item, source_repo):
    source_path, _ = source_repo
    manager = local_manager(config, clock, source_path)
    root = config.runs_dir.parent
    before = {entry.name for entry in root.iterdir()}

    manager.acquire(item)

    after = {entry.name for entry in root.iterdir()}
    created = {name for name in after - before if not name.startswith("harness.db")}
    assert created <= {config.runs_dir.name}


# --- B44: the base sha ------------------------------------------------------------------------


def test_B44_base_sha_is_the_resolved_head_of_the_cloned_repository(
    config, clock, item, source_repo
):
    source_path, head_sha = source_repo
    manager = local_manager(config, clock, source_path)

    lease = manager.acquire(item)

    assert lease.base_sha == head_sha
    assert len(lease.base_sha) == 40
    assert lease.base_sha == git(["rev-parse", "HEAD"], lease.path)


# --- B45: the branch --------------------------------------------------------------------------


def test_B45_the_created_branch_name_starts_with_harness(config, clock, item, source_repo):
    source_path, _ = source_repo
    manager = local_manager(config, clock, source_path)

    lease = manager.acquire(item)

    assert lease.branch.startswith("harness/")
    assert git(["rev-parse", "--abbrev-ref", "HEAD"], lease.path) == lease.branch


def test_B45_the_branch_name_carries_the_type_and_issue_number(config, clock, item, source_repo):
    source_path, _ = source_repo
    manager = local_manager(config, clock, source_path)

    lease = manager.acquire(item)

    assert lease.branch.startswith("harness/fix-816-")


# --- B46 / B47: preflight ---------------------------------------------------------------------


def test_B46_preflight_reports_a_disk_blocker_when_free_space_is_short(config, clock):
    manager = CloneManager(config, clock, free_bytes=lambda path: 1024)

    blockers = manager.preflight()

    assert blockers
    assert any(blocker.startswith("disk:") for blocker in blockers)


def test_B46_preflight_is_empty_when_disk_is_ample_and_nothing_else_blocks(config, clock):
    manager = CloneManager(config, clock, free_bytes=lambda path: AMPLE_BYTES)

    assert manager.preflight() == []


def test_B46_preflight_reports_a_git_blocker_when_the_git_binary_is_missing(config, clock):
    manager = CloneManager(
        config,
        clock,
        git_bin="harness-no-such-git-binary",
        free_bytes=lambda path: AMPLE_BYTES,
    )

    blockers = manager.preflight()

    assert any(blocker.startswith("git:") for blocker in blockers)


def test_B47_preflight_reports_a_halt_blocker_when_the_halt_file_exists(config, clock):
    config.halt_file.write_text("stop\n", encoding="utf-8", newline="\n")
    manager = CloneManager(config, clock, free_bytes=lambda path: AMPLE_BYTES)

    blockers = manager.preflight()

    assert any(blocker.startswith("halt:") for blocker in blockers)


def test_B47_preflight_reports_the_halt_and_disk_blockers_together(config, clock):
    config.halt_file.write_text("stop\n", encoding="utf-8", newline="\n")
    manager = CloneManager(config, clock, free_bytes=lambda path: 1024)

    blockers = manager.preflight()

    assert any(blocker.startswith("halt:") for blocker in blockers)
    assert any(blocker.startswith("disk:") for blocker in blockers)


# --- B48: release -----------------------------------------------------------------------------


def test_B48_release_keep_false_removes_the_clone_directory(config, clock, item, source_repo):
    source_path, _ = source_repo
    manager = local_manager(config, clock, source_path)
    lease = manager.acquire(item)
    assert lease.path.is_dir()

    manager.release(lease, keep=False)

    assert not lease.path.exists()


def test_B48_release_keep_true_leaves_the_clone_and_records_the_path(
    config, clock, item, source_repo
):
    source_path, _ = source_repo
    manager = local_manager(config, clock, source_path)
    lease = manager.acquire(item)

    manager.release(lease, keep=True)

    assert lease.path.is_dir()
    kept = config.runs_dir / lease.run_id / "KEPT"
    assert kept.is_file()
    assert str(lease.path) in kept.read_text(encoding="utf-8")


# --------------------------------------------------------------------------------------
# B220 - the fork is the clone source only while it can be kept current (D40)
# --------------------------------------------------------------------------------------


def test_b220_tier_2_clones_the_fork(tmp_path):
    """B220: at tier 2 sync_fork can fast-forward the fork, so the fork is the right base."""
    from harness.clone import _source_repo

    config = SimpleNamespace(
        fork_repo="bot/product", repo="owner/product", permission_tier=2
    )

    assert _source_repo(config) == "bot/product"


def test_b220_tier_0_clones_the_product_repository_not_the_stale_fork(tmp_path):
    """B220: below tier 2 there is no push, so sync_fork cannot run and the fork is frozen."""
    from harness.clone import _source_repo

    config = SimpleNamespace(
        fork_repo="bot/product", repo="owner/product", permission_tier=0
    )

    assert _source_repo(config) == "owner/product"


def test_b220_no_fork_configured_always_means_the_product_repository():
    """B220: unchanged from Delivery 1 -- an empty FORK_REPO was never a clone source."""
    from harness.clone import _source_repo

    for tier in (0, 2):
        config = SimpleNamespace(fork_repo="", repo="owner/product", permission_tier=tier)
        assert _source_repo(config) == "owner/product"


# --------------------------------------------------------------------------------------
# B224 - a clone that will not delete must say so, not report success (D44)
# --------------------------------------------------------------------------------------


def test_b224_long_path_wraps_a_drive_path_in_the_extended_prefix():
    """B224: the prefix is what lifts the 260-character limit for one call."""
    from harness.clone import EXTENDED_PREFIX, long_path

    result = long_path("C:/Users/x/y")

    if os.name == "nt":
        assert result.startswith(EXTENDED_PREFIX)
        assert result.endswith("Users" + chr(92) + "x" + chr(92) + "y")
    else:
        assert not result.startswith(EXTENDED_PREFIX)


def test_b224_long_path_is_idempotent():
    """B224: rmtree's error handler calls it on paths it may already have wrapped."""
    from harness.clone import long_path

    once = long_path("C:/Users/x")

    assert long_path(once) == once


def test_b224_a_removal_that_cannot_succeed_raises_instead_of_returning(tmp_path):
    """B224: both retries ended in a bare `return`, so shutil.rmtree reported success over a
    partial delete and the failure surfaced two steps later as a git clone error."""
    from harness.clone import _on_rmtree_error

    target = tmp_path / "stubborn"
    target.mkdir()
    calls: list[str] = []

    def always_fails(path):
        calls.append(str(path))
        raise OSError(5, "Access is denied")

    with pytest.raises(OSError):
        _on_rmtree_error(always_fails, str(target), OSError())

    assert calls, "the handler never retried the removal"


def test_b224_a_tree_deeper_than_max_path_is_removed(tmp_path):
    """B224: measured on a real clone -- 1943 files survived a "successful" rmtree, the
    deepest path 324 characters, because `npm ci` nests node_modules inside node_modules."""
    from harness.clone import _on_rmtree_error, long_path

    deep = tmp_path / "clone"
    deep.mkdir()
    current = deep
    while len(str(current)) < 300:
        current = current / "node_modules"
        # The extended form is needed to *build* the tree too: os.mkdir refuses at MAX_PATH,
        # which is why only a native installer like npm produces one of these in the first place.
        os.mkdir(long_path(current))
    leaf = current / "accessible-name-and-description.d.ts.map"
    with open(long_path(leaf), "w", encoding="utf-8") as handle:
        handle.write("{}")

    shutil.rmtree(long_path(deep), onexc=_on_rmtree_error)

    assert not deep.exists()


def test_b224_acquire_refuses_a_clone_directory_it_could_not_clear(
    config, clock, item, source_repo, monkeypatch
):
    """B224: git's own message for this names neither the leftovers nor the reason."""
    from harness import clone as clone_mod

    source_path, _head = source_repo
    manager = local_manager(config, clock, source_path)
    lease = manager.acquire(item)
    (Path(lease.path) / "survivor.txt").write_text("still here", encoding="utf-8")
    monkeypatch.setattr(clone_mod.shutil, "rmtree", lambda *a, **k: None)

    with pytest.raises(clone_mod.CloneError) as excinfo:
        manager.acquire(item)

    assert "could not clear the previous clone" in str(excinfo.value)
    assert "entries remain" in str(excinfo.value)


# --------------------------------------------------------------------------------------
# B229 - no git hook runs in a harness clone (D49)
# --------------------------------------------------------------------------------------


def test_b229_the_clone_has_hooks_turned_off(config, clock, item, source_repo):
    """B229: `npm ci` installs the product repository's husky hooks, and a pre-push hook then
    runs inside `git push` -- in an environment the product does not test, duplicating a gate
    the harness has already run explicitly."""
    from harness.clone import HOOKS_OFF

    source_path, _head = source_repo
    manager = local_manager(config, clock, source_path)

    lease = manager.acquire(item)

    configured = subprocess.run(
        ["git", "config", "core.hooksPath"],
        cwd=str(lease.path), capture_output=True, text=True,
    ).stdout.strip()
    assert configured == HOOKS_OFF


def test_b229_a_refusing_pre_push_hook_does_not_stop_the_push(config, clock, item, source_repo):
    """B229: measured on the real thing -- brightboost's pre-push calls a script that crashes
    under Node 22, so the push was refused and the reviewer got vite output as the reason."""
    source_path, _head = source_repo
    manager = local_manager(config, clock, source_path)
    lease = manager.acquire(item)
    hooks = Path(lease.path) / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "pre-push"
    hook.write_text("#!/bin/sh\necho refused >&2\nexit 1\n", encoding="utf-8", newline="\n")
    hook.chmod(0o755)
    (Path(lease.path) / "new.txt").write_text("x", encoding="utf-8")
    _git_in(lease.path, "add", "-A")
    _git_in(lease.path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "x")

    pushed = subprocess.run(
        ["git", "push", str(source_path), f"HEAD:refs/heads/{lease.branch}"],
        cwd=str(lease.path), capture_output=True, text=True,
    )

    assert pushed.returncode == 0, pushed.stderr
    assert "refused" not in pushed.stderr


def _git_in(cwd, *args):
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr}")
    return proc.stdout
