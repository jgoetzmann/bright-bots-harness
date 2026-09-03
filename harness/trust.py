"""Trust file and the first half of the actor gate (handoff 5.5, 8.2 - B131). Fails closed."""
from __future__ import annotations

from pathlib import Path

# GitHub's assertion of the commenter's relationship to the repo. Anything else, including
# CONTRIBUTOR, FIRST_TIMER, FIRST_TIME_CONTRIBUTOR and NONE, is refused (B131 condition 2).
AUTHOR_ASSOCIATIONS: frozenset[str] = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})


def normalise_handle(handle: str) -> str:
    """Canonical form of a GitHub login: stripped, no leading ``@``, lower-cased (R4.5)."""
    return handle.strip().lstrip("@").lower()


def load_trust(path: Path) -> frozenset[str]:
    """Read ``.harness/trust.txt``: one handle per line, ``#`` comments, blank lines ignored.

    Handles are lower-cased. An entry containing ``<`` or ``>`` is an unresolved placeholder
    (``<NATHAN_HANDLE>``) and is not a handle. A missing or unreadable file is an empty set.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return frozenset()
    handles: set[str] = set()
    for raw_line in text.splitlines():
        entry = raw_line.split("#", 1)[0].strip()
        if not entry or "<" in entry or ">" in entry:
            continue
        handle = normalise_handle(entry)
        if handle:
            handles.add(handle)
    return frozenset(handles)


def is_authorised(handle: str, author_association: str, trusted: frozenset[str]) -> bool:
    """B131: BOTH the trust file (operator's intent) AND the association (GitHub's assertion).

    Neither alone suffices. The handle comparison is case-insensitive; the association must be
    exactly one of ``AUTHOR_ASSOCIATIONS`` as GitHub spells it.
    """
    in_trust_file = normalise_handle(handle) in trusted
    association_ok = author_association in AUTHOR_ASSOCIATIONS
    return in_trust_file and association_ok
