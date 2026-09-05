"""Trust file and the first half of the actor gate (handoff 5.5, 8.2 - B131). Fails closed."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Mapping

# GitHub's assertion of the commenter's relationship to the repo. Anything else, including
# CONTRIBUTOR, FIRST_TIMER, FIRST_TIME_CONTRIBUTOR and NONE, is refused (B131 condition 2).
AUTHOR_ASSOCIATIONS: frozenset[str] = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})

#: What each level may do, for the messages that have to explain a refusal (B269/D60).
LEVEL_NAMES: dict[int, str] = {
    0: "no access",
    1: "asker",
    2: "maintainer",
    3: "operator",
}

#: A handle listed with no level. Least privilege on ambiguity: a typo in this file must never
#: silently grant power. `harness doctor` names every one of them so it is never silent either.
DEFAULT_LEVEL = 1

#: The highest level. Nothing above the operator.
MAX_LEVEL = 3


def normalise_handle(handle: str) -> str:
    """Canonical form of a GitHub login: stripped, no leading ``@``, lower-cased (R4.5)."""
    return handle.strip().lstrip("@").lower()


@dataclass(frozen=True, eq=False)
class Trust:
    """Who may command the harness, and how much (B269/D60).

    Behaves as the set of handles it used to be -- ``in``, iteration and truthiness all work as
    before -- so every caller that only asks "is this handle trusted at all" is unchanged.
    """

    levels: Mapping[str, int] = field(default_factory=dict)
    #: Handles listed with no explicit level, so `doctor` can name them.
    implicit: tuple[str, ...] = ()
    #: Lines that look like a level and are not one. Refused, and named rather than guessed at.
    malformed: tuple[str, ...] = ()

    def __contains__(self, handle: object) -> bool:
        return normalise_handle(str(handle)) in self.levels

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(self.levels))

    def __len__(self) -> int:
        return len(self.levels)

    def __bool__(self) -> bool:
        return bool(self.levels)

    def __eq__(self, other: object) -> bool:
        """Equal to another :class:`Trust` by levels, and to a set by handles.

        B131's tests compare the loaded file with a frozenset of handles, and what they assert
        -- comments ignored, placeholders ignored, case folded -- has not changed. The levels
        are new information beside that, not a replacement for it.
        """
        if isinstance(other, Trust):
            return dict(self.levels) == dict(other.levels)
        if isinstance(other, (set, frozenset)):
            return set(self.levels) == {normalise_handle(str(h)) for h in other}
        return NotImplemented

    def __hash__(self) -> int:
        return hash(frozenset(self.levels.items()))

    def level_of(self, handle: str) -> int:
        """The level for `handle`; 0 when it is not in the file at all."""
        return int(self.levels.get(normalise_handle(handle), 0))

    def at_least(self, level: int) -> tuple[str, ...]:
        """Every handle at or above `level`, sorted."""
        return tuple(sorted(h for h, lvl in self.levels.items() if lvl >= int(level)))


def parse_trust(text: str) -> Trust:
    """``<level> <handle>`` or a bare ``<handle>``, ``#`` comments, blank lines ignored.

    A bare handle is :data:`DEFAULT_LEVEL`. An entry containing ``<`` or ``>`` is an unresolved
    placeholder (``<NATHAN_HANDLE>``) and is not a handle.

    A first token that is all digits is an attempt at a level. If it is not one of 1..3 the
    whole line is **refused** and recorded in :attr:`Trust.malformed` -- not clamped, and not
    reinterpreted as a handle. `9 someone` meant something, and neither guessing which end of
    the range they meant nor granting access to a handle named `9` is an improvement on saying
    so.
    """
    levels: dict[str, int] = {}
    implicit: list[str] = []
    malformed: list[str] = []
    for raw_line in text.splitlines():
        entry = raw_line.split("#", 1)[0].strip()
        if not entry or "<" in entry or ">" in entry:
            continue
        parts = entry.split()
        level = DEFAULT_LEVEL
        handle_part = parts[0]
        explicit = False
        if parts[0].isdigit():
            if len(parts) < 2 or not 1 <= int(parts[0]) <= MAX_LEVEL:
                malformed.append(entry)
                continue
            level = int(parts[0])
            handle_part = parts[1]
            explicit = True
        handle = normalise_handle(handle_part)
        if not handle:
            continue
        if not explicit:
            implicit.append(handle)
        # A handle listed twice keeps the highest level it was given.
        levels[handle] = max(level, levels.get(handle, 0))
    return Trust(
        levels=levels,
        implicit=tuple(sorted(set(implicit))),
        malformed=tuple(malformed),
    )


def load_trust(path: Path) -> Trust:
    """Read ``.harness/trust.txt``. A missing or unreadable file is nobody, not everybody."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return Trust()
    return parse_trust(text)


def is_authorised(
    handle: str,
    author_association: str,
    trusted: Trust | frozenset[str] | set[str],
    *,
    min_level: int = 1,
) -> bool:
    """B131: BOTH the trust file (operator's intent) AND the association (GitHub's assertion).

    Neither alone suffices. B270 adds the third: the handle's level must reach `min_level`. A
    plain set of handles is still accepted and every handle in one counts as
    :data:`DEFAULT_LEVEL`, so a caller that has not been taught about levels cannot accidentally
    grant more than the least.
    """
    if author_association not in AUTHOR_ASSOCIATIONS:
        return False
    if isinstance(trusted, Trust):
        return trusted.level_of(handle) >= int(min_level)
    return normalise_handle(handle) in trusted and DEFAULT_LEVEL >= int(min_level)
