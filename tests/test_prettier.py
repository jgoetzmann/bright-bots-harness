"""B222 (D42): the change set is not the list prettier gets, and deletions belong in it.

Real git repositories, no network: the defect these pin was invisible to every fake because a
fake `changed_paths` returns whatever the test says it returns.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from harness.prettier import all_changed_paths, changed_paths


def _git(*args: str, cwd: Path) -> str:
    argv = ["git", "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args]
    proc = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(argv)} failed in {cwd}: {proc.stderr}")
    return proc.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, str]:
    """A repository with two committed files, returned with its base sha."""
    root = tmp_path / "clone"
    root.mkdir()
    _git("init", "-q", "-b", "main", cwd=root)
    (root / "keep.ts").write_text("export const keep = 1;\n", encoding="utf-8")
    (root / "doomed.ts").write_text("export const doomed = 2;\n", encoding="utf-8")
    _git("add", "-A", cwd=root)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base", cwd=root)
    return root, _git("rev-parse", "HEAD", cwd=root)


def test_b222_a_deletion_is_in_the_change_set(repo):
    """B222: the run that found this deleted the two files its work package named and then
    recorded "changed paths: (none)"."""
    root, base = repo
    (root / "doomed.ts").unlink()

    assert all_changed_paths(root, base) == ["doomed.ts"]


def test_b222_a_deletion_is_not_in_the_list_prettier_gets(repo):
    """B222: unchanged behaviour, and the reason the two lists must stay separate --
    prettier cannot format a file that is gone."""
    root, base = repo
    (root / "doomed.ts").unlink()

    assert changed_paths(root, base) == []


def test_b222_a_deletion_only_change_is_not_mistaken_for_no_change(repo):
    """B222: `if not changed` decided whether anything was committed at all."""
    root, base = repo
    (root / "doomed.ts").unlink()
    (root / "keep.ts").unlink()

    assert all_changed_paths(root, base) == ["doomed.ts", "keep.ts"]
    assert changed_paths(root, base) == []


def test_b222_edits_additions_and_deletions_appear_together_sorted(repo):
    """B222: a mixed diff loses nothing; the two lists differ only by the deletion."""
    root, base = repo
    (root / "doomed.ts").unlink()
    (root / "keep.ts").write_text("export const keep = 99;\n", encoding="utf-8")
    (root / "added.ts").write_text("export const added = 3;\n", encoding="utf-8")

    assert all_changed_paths(root, base) == ["added.ts", "doomed.ts", "keep.ts"]
    assert changed_paths(root, base) == ["added.ts", "keep.ts"]


def test_b222_an_untouched_tree_is_empty_in_both(repo):
    """B222: the guard still has to be able to say "nothing happened"."""
    root, base = repo

    assert all_changed_paths(root, base) == []
    assert changed_paths(root, base) == []


def test_b222_a_deleted_ci_workflow_reaches_the_forbidden_diff_guard(repo):
    """B222: the guard that exists to stop a CI change was fed the AM-filtered list, so
    deleting a workflow passed it silently. Deleting one is not milder than editing it."""
    root, base = repo
    workflow = root / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("on: push\n", encoding="utf-8")
    _git("add", "-A", cwd=root)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "ci", cwd=root)
    with_ci = _git("rev-parse", "HEAD", cwd=root)
    workflow.unlink()

    assert ".github/workflows/ci.yml" in all_changed_paths(root, with_ci)
    assert ".github/workflows/ci.yml" not in changed_paths(root, with_ci)
