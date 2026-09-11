"""Trust file and the first half of the actor gate (handoff 5.5, 8.2 - B131). Fails closed."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping

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

#: D68: ``vouch:<numeric GitHub user id>`` after the handle pins the line to ONE account. A
#: token that starts with this word is an attempt at a vouch, so a typo in the id refuses the
#: line rather than being quietly ignored into a grant that means something else.
VOUCH_WORD = "vouch"
_VOUCH_RE = re.compile(r"vouch:([1-9][0-9]{0,19})", re.IGNORECASE)
_DIGITS_RE = re.compile(r"[0-9]{1,20}")


def normalise_handle(handle: str) -> str:
    """Canonical form of a GitHub login: stripped, no leading ``@``, lower-cased (R4.5)."""
    return handle.strip().lstrip("@").lower()


def parse_user_id(value: object) -> int | None:
    """A GitHub numeric user id, or None when `value` is not one.

    REST payloads carry an int; the `ack` workflow hands one over as text. Anything else --
    empty, zero, negative, a bool, a non-ASCII digit -- is "unknown", and unknown never matches
    a vouched id.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    text = str(value if value is not None else "").strip()
    if not _DIGITS_RE.fullmatch(text):
        return None
    number = int(text)
    return number if number > 0 else None


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
    #: D68: handle -> the one numeric account id its line vouches for.
    vouched: Mapping[str, int] = field(default_factory=dict)

    def __contains__(self, handle: object) -> bool:
        return normalise_handle(str(handle)) in self.levels

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(self.levels))

    def __len__(self) -> int:
        return len(self.levels)

    def __bool__(self) -> bool:
        return bool(self.levels)

    def __eq__(self, other: object) -> bool:
        """Equal to another :class:`Trust` by levels and vouches, and to a set by handles.

        B131's tests compare the loaded file with a frozenset of handles, and what they assert
        -- comments ignored, placeholders ignored, case folded -- has not changed. The levels
        are new information beside that, not a replacement for it.
        """
        if isinstance(other, Trust):
            return (dict(self.levels), dict(self.vouched)) == (
                dict(other.levels), dict(other.vouched)
            )
        if isinstance(other, (set, frozenset)):
            return set(self.levels) == {normalise_handle(str(h)) for h in other}
        return NotImplemented

    def __hash__(self) -> int:
        return hash((frozenset(self.levels.items()), frozenset(self.vouched.items())))

    def level_of(self, handle: str) -> int:
        """The level for `handle`; 0 when it is not in the file at all."""
        return int(self.levels.get(normalise_handle(handle), 0))

    def vouched_id(self, handle: str) -> int | None:
        """D68: the account id `handle`'s line vouches for, or None when it vouches for none."""
        found = self.vouched.get(normalise_handle(handle))
        return int(found) if found is not None else None

    def at_least(self, level: int) -> tuple[str, ...]:
        """Every handle at or above `level`, sorted."""
        return tuple(sorted(h for h, lvl in self.levels.items() if lvl >= int(level)))


def parse_trust(text: str) -> Trust:
    """``<level> <handle> [vouch:<id>]`` or a bare ``<handle>``, ``#`` comments, blanks ignored.

    A bare handle is :data:`DEFAULT_LEVEL`. An entry containing ``<`` or ``>`` is an unresolved
    placeholder (``<NATHAN_HANDLE>``) and is not a handle.

    A first token that is all digits is an attempt at a level. If it is not one of 1..3 the
    whole line is **refused** and recorded in :attr:`Trust.malformed` -- not clamped, and not
    reinterpreted as a handle. `9 someone` meant something, and neither guessing which end of
    the range they meant nor granting access to a handle named `9` is an improvement on saying
    so.

    D68 adds the vouch, under the same rule. A token beginning ``vouch`` must be exactly
    ``vouch:<positive integer>``, and a line may carry one; anything else refuses the whole
    line. A handle vouched for two DIFFERENT ids is refused entirely -- every line naming it --
    because which account the operator meant is exactly the question the vouch exists to settle.
    """
    levels: dict[str, int] = {}
    implicit: list[str] = []
    malformed: list[str] = []
    vouches: dict[str, list[tuple[int, str]]] = {}
    for raw_line in text.splitlines():
        entry = raw_line.split("#", 1)[0].strip()
        if not entry or "<" in entry or ">" in entry:
            continue
        parts = entry.split()
        level = DEFAULT_LEVEL
        handle_part = parts[0]
        rest = parts[1:]
        explicit = False
        if parts[0].isdigit():
            if len(parts) < 2 or not 1 <= int(parts[0]) <= MAX_LEVEL:
                malformed.append(entry)
                continue
            level = int(parts[0])
            handle_part = parts[1]
            rest = parts[2:]
            explicit = True
        if handle_part.lower().startswith(VOUCH_WORD):
            # `2 vouch:193453438` -- a vouch with no handle in front of it. Never a login.
            malformed.append(entry)
            continue
        attempts = [token for token in rest if token.lower().startswith(VOUCH_WORD)]
        pinned: int | None = None
        if attempts:
            match = _VOUCH_RE.fullmatch(attempts[0]) if len(attempts) == 1 else None
            if match is None:
                malformed.append(entry)
                continue
            pinned = int(match.group(1))
        handle = normalise_handle(handle_part)
        if not handle:
            continue
        if not explicit:
            implicit.append(handle)
        # A handle listed twice keeps the highest level it was given.
        levels[handle] = max(level, levels.get(handle, 0))
        if pinned is not None:
            vouches.setdefault(handle, []).append((pinned, entry))
    vouched: dict[str, int] = {}
    for handle, pins in vouches.items():
        if len({pin for pin, _entry in pins}) == 1:
            vouched[handle] = pins[0][0]
            continue
        levels.pop(handle, None)
        malformed.extend(entry for _pin, entry in pins)
    return Trust(
        levels=levels,
        implicit=tuple(sorted(set(implicit) & set(levels))),
        malformed=tuple(malformed),
        vouched=vouched,
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
    user_id: object = None,
) -> bool:
    """B131: BOTH the trust file (operator's intent) AND the association (GitHub's assertion).

    Neither alone suffices. B270 adds the third: the handle's level must reach `min_level`. A
    plain set of handles is still accepted and every handle in one counts as
    :data:`DEFAULT_LEVEL`, so a caller that has not been taught about levels cannot accidentally
    grant more than the least.

    D68 replaces the association half, and only that half, for a vouched line. The association
    guards a NAME: a login can be renamed away and claimed by somebody else, and GitHub's
    per-repository association is what said "this is still the person you meant". A numeric
    account id is never reused, so a line that names the id already says that -- more precisely
    than the association does. A vouched handle therefore passes when `user_id` equals the
    vouched id, whatever the association; and it is refused when it does not, whatever the
    association, because a different account holding that login is not the one the line names.
    The level still caps what either may do.

    Callers with a comment in hand use :func:`comment_authorised`, which reads all three fields
    from the payload in one place.
    """
    association = str(author_association or "").strip().upper()
    if not isinstance(trusted, Trust):
        if association not in AUTHOR_ASSOCIATIONS:
            return False
        return normalise_handle(handle) in trusted and DEFAULT_LEVEL >= int(min_level)
    if trusted.level_of(handle) < int(min_level):
        return False
    pinned = trusted.vouched_id(handle)
    if pinned is not None:
        return parse_user_id(user_id) == pinned
    return association in AUTHOR_ASSOCIATIONS


def comment_author(comment: Mapping[str, Any]) -> tuple[str, str, int | None]:
    """``(login, author_association, numeric user id)`` from a REST comment-shaped payload.

    Issue comments, pull request review comments and reviews all carry ``user.login``,
    ``user.id`` and ``author_association``. Reads nothing else -- in particular never the body,
    which B273 forbids reading before the gate has passed.
    """
    user = comment.get("user") or {}
    if not isinstance(user, Mapping):
        user = {}
    login = str(user.get("login") or "").strip()
    association = str(comment.get("author_association") or "").strip().upper()
    return login, association, parse_user_id(user.get("id"))


def comment_authorised(comment: Mapping[str, Any], trusted: Any, *, min_level: int = 1) -> bool:
    """THE actor gate for one comment, on every surface (B131, B270, D68).

    The sweep, `harness ack` and revise's review filter all call this, so the rule -- the
    association half, the vouch that replaces it, and the level -- is applied in exactly one
    place and cannot be taught to one caller and forgotten by another.
    """
    login, association, uid = comment_author(comment)
    if not login:
        return False
    return is_authorised(login, association, trusted, min_level=min_level, user_id=uid)
