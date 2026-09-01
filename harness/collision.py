"""Branch and title -> claimed issue numbers (spec §5.6).

Pure functions, no I/O. As of 2026-09-01 eight of the nine open pull requests on
``Bright-Bots-Initiative/brightboost`` carry no ``closingIssuesReferences`` at
all, yet six of them are plainly working an open issue that is identifiable only
from the branch name. Selecting on GitHub's link graph alone would duplicate
another contributor's in-flight work, so the branch names are the source of
truth here.
"""

from __future__ import annotations

import re
from typing import Iterable

# §5.6 patterns, drawn from live observation of the repository.
#   agent-737/qtr-ceiling                    -> 737
#   fix-801/ci-shell-gate-isolation          -> 801
#   agent-b/782-required-step-coverage       -> 782
#   jack/chore-740-parity-guards             -> 740
BRANCH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"agent-(\d+)/"),
    re.compile(r"fix-(\d+)/"),
    re.compile(r"^[A-Za-z]+-[A-Za-z]/(\d+)-"),
    re.compile(r"^[A-Za-z0-9_-]+/[a-z]+-(\d+)-"),
)

# Explicit references, and the GitHub closing keywords, case-insensitive.
TITLE_HASH_PATTERN: re.Pattern[str] = re.compile(r"#(\d+)")
TITLE_KEYWORD_PATTERN: re.Pattern[str] = re.compile(
    r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#?(\d+)"
)


def issue_numbers_from_branch(branch: str) -> set[int]:
    """Issue numbers a branch name claims. Empty set when it claims none."""
    if not branch:
        return set()
    text = str(branch).strip()
    found: set[int] = set()
    for pattern in BRANCH_PATTERNS:
        for match in pattern.finditer(text):
            digits = match.group(1)
            if digits:
                found.add(int(digits))
    return found


def issue_numbers_from_title(title: str) -> set[int]:
    """Issue numbers a pull-request title claims, via ``#N`` or a closing keyword."""
    if not title:
        return set()
    text = str(title)
    found: set[int] = set()
    for match in TITLE_HASH_PATTERN.finditer(text):
        found.add(int(match.group(1)))
    for match in TITLE_KEYWORD_PATTERN.finditer(text):
        found.add(int(match.group(1)))
    return found


def claimed_issue_numbers(branches: Iterable[str], pr_titles: Iterable[str]) -> set[int]:
    """Every issue number some in-flight branch or pull-request title already claims."""
    claimed: set[int] = set()
    for branch in branches:
        claimed |= issue_numbers_from_branch(branch)
    for title in pr_titles:
        claimed |= issue_numbers_from_title(title)
    return claimed
