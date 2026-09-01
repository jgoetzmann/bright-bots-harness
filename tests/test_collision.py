"""B38-B41: harness.collision (HARNESS-SPEC 5.6).

Pure functions, no I/O, except B41 which is asserted against the recorded 2026-09-01 fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.collision import (
    claimed_issue_numbers,
    issue_numbers_from_branch,
    issue_numbers_from_title,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "gh"
BRANCHES_FIXTURE = FIXTURE_DIR / "branches_2026-09-01.json"
PULLS_FIXTURE = FIXTURE_DIR / "pulls_2026-09-01.json"

# B41: the exact set the spec freezes for the live 2026-09-01 listing.
EXPECTED_CLAIMED = {681, 700, 734, 735, 736, 737, 801, 810, 640}


def load_fixture(path):
    return json.loads(path.read_text(encoding="utf-8"))


# --- B38: every pattern in the 5.6 table ------------------------------------------------------


@pytest.mark.parametrize(
    "branch,expected",
    [
        ("agent-737/qtr-ceiling", {737}),
        ("fix-801/ci-shell-gate-isolation", {801}),
        ("agent-b/782-required-step-coverage", {782}),
        ("jack/chore-740-parity-guards", {740}),
    ],
)
def test_B38_each_documented_branch_pattern_extracts_its_issue_number(branch, expected):
    assert issue_numbers_from_branch(branch) == expected


def test_B38_claimed_issue_numbers_unions_every_branch_pattern():
    branches = [
        "agent-737/qtr-ceiling",
        "fix-801/ci-shell-gate-isolation",
        "agent-b/782-required-step-coverage",
        "jack/chore-740-parity-guards",
    ]

    assert claimed_issue_numbers(branches, []) == {737, 801, 782, 740}


# --- B39: branches carrying no issue number ---------------------------------------------------


@pytest.mark.parametrize(
    "branch",
    [
        "main",
        "develop",
        "dependabot/npm_and_yarn/vite-5.4.0",
        "codex/refresh-readme",
        "",
        "hotfix",
        "chore/cleanup",
        "feature/add-lens",
        "release/2026-09",
        "renovate/lock-file-maintenance",
        "agent-/no-number",
    ],
)
def test_B39_a_branch_with_no_issue_number_yields_the_empty_set(branch):
    assert issue_numbers_from_branch(branch) == set()


def test_B39_claimed_issue_numbers_ignores_branches_without_a_number():
    branches = ["main", "develop", "dependabot/npm_and_yarn/vite-5.4.0", "codex/refresh-readme"]

    assert claimed_issue_numbers(branches, []) == set()


def test_B39_claimed_issue_numbers_with_no_branches_and_no_titles_is_empty():
    assert claimed_issue_numbers([], []) == set()


# --- B40: titles ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title,expected",
    [
        ("chore: tidy the ceiling #12", {12}),
        ("Fixes #34", {34}),
        ("CLOSES 56", {56}),
        ("resolved #78", {78}),
        ("fix #9", {9}),
        ("Resolve #11", {11}),
        ("closed #13", {13}),
        ("FIXED #15", {15}),
    ],
)
def test_B40_titles_yield_numbers_from_refs_and_closing_keywords_case_insensitively(
    title, expected
):
    assert issue_numbers_from_title(title) == expected


def test_B40_a_title_with_two_references_yields_both_numbers():
    assert issue_numbers_from_title("fix: #12 and closes #34") == {12, 34}


@pytest.mark.parametrize(
    "title",
    [
        "chore: bump vite to 5.4.0",
        "feat: add the quarterly ceiling",
        "",
        "fix the flaky test",
        "closes the loop on caching",
        "feat: 12 new lenses",
    ],
)
def test_B40_a_title_with_no_reference_yields_the_empty_set(title):
    assert issue_numbers_from_title(title) == set()


def test_B40_claimed_issue_numbers_ignores_titles_without_a_reference():
    assert claimed_issue_numbers([], ["chore: bump vite to 5.4.0", "feat: add a lens"]) == set()


# --- B41: the recorded live listing -----------------------------------------------------------


def test_B41_the_recorded_2026_09_01_fixtures_are_present_and_shaped_as_recorded():
    branches = load_fixture(BRANCHES_FIXTURE)
    pulls = load_fixture(PULLS_FIXTURE)

    assert isinstance(branches, list) and branches
    assert all(isinstance(name, str) for name in branches)
    assert isinstance(pulls, list) and pulls
    for pull in pulls:
        assert isinstance(pull.get("title"), str)
        assert isinstance(pull.get("head", {}).get("ref"), str)


def test_B41_the_live_fixture_yields_the_frozen_claimed_issue_set():
    branches = load_fixture(BRANCHES_FIXTURE)
    titles = [pull["title"] for pull in load_fixture(PULLS_FIXTURE)]

    assert claimed_issue_numbers(branches, titles) == EXPECTED_CLAIMED


def test_B41_every_claimed_number_traces_back_to_a_branch_or_a_title():
    branches = load_fixture(BRANCHES_FIXTURE)
    titles = [pull["title"] for pull in load_fixture(PULLS_FIXTURE)]
    per_source = set()
    for branch in branches:
        per_source |= issue_numbers_from_branch(branch)
    for title in titles:
        per_source |= issue_numbers_from_title(title)

    assert per_source == EXPECTED_CLAIMED
