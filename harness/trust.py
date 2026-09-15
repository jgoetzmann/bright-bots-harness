"""The trust file and the first half of the actor gate. Fails closed (B131)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping

# GitHub's assertion of the commenter's relationship to the repo. Anything else, including
# CONTRIBUTOR, FIRST_TIMER, FIRST_TIME_CONTRIBUTOR and NONE, is refused (B131).
AUTHOR_ASSOCIATIONS: frozenset[str] = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})

#: The name of each level, for the messages that explain a refusal (B269).
LEVEL_NAMES: dict[int, str] = {
    0: "no access",
    1: "asker",
    2: "maintainer",
    3: "operator",
}

#: The level of a handle listed without one: the least privilege, so a typo grants no power.
#: `harness doctor` names every handle that lands here.
DEFAULT_LEVEL = 1

#: The highest level, the operator.
MAX_LEVEL = 3

#: A line may end in ``vouch:<numeric GitHub user id>``, which pins it to one account (D68).
#: A token beginning with this word is read as a vouch, so a typo in the id refuses the line.
VOUCH_WORD = "vouch"
_VOUCH_RE = re.compile(r"vouch:([1-9][0-9]{0,19})", re.IGNORECASE)
_DIGITS_RE = re.compile(r"[0-9]{1,20}")

#: The shape of a GitHub login: ASCII letters, digits and hyphens, at most 39 of them. A handle
#: outside it can never equal a `user.login`, so its line is refused and named rather than
#: registered as a grant to nobody (D69).
_HANDLE_RE = re.compile(r"[A-Za-z0-9-]{1,39}")


def normalise_handle(handle: str) -> str:
    """Canonical form of a GitHub login: stripped, no leading ``@``, lower-cased."""
    return handle.strip().lstrip("@").lower()


def _is_level_token(token: str) -> bool:
    """True when `token` is an attempt at the level column: ASCII decimal digits, nothing else.

    `str.isdigit()` is also true of digits that are not ASCII: an Arabic-Indic three satisfies
    ``int()``, and a superscript two raises ValueError. Neither is read as a level, so such a
    line falls through to the handle path and is refused and named there.

    There is no length limit. A long run of digits is still an attempt at a level and is
    refused as out of range; on the handle path it would be login-shaped and granted
    :data:`DEFAULT_LEVEL` in silence.
    """
    return token.isascii() and token.isdigit()


def parse_user_id(value: object) -> int | None:
    """A GitHub numeric user id, or None when `value` is not one.

    REST payloads carry an int; the `ack` workflow hands one over as text. Empty, zero,
    negative, a bool or a non-ASCII digit is unknown, and unknown never matches a vouched id.
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
    """Who may command the harness, and at what level (B269).

    Behaves as a set of handles for ``in``, iteration, ``len`` and truthiness, so a caller that
    only asks whether a handle is trusted at all needs to know nothing about levels.
    """

    levels: Mapping[str, int] = field(default_factory=dict)
    #: Handles listed with no explicit level, so `doctor` can name them.
    implicit: tuple[str, ...] = ()
    #: Refused lines: a level out of range, a handle that is not a login, an unreadable token,
    #: or a vouch conflict. :func:`refusals` says which.
    malformed: tuple[str, ...] = ()
    #: Handle -> the one numeric account id its line vouches for (D68).
    vouched: Mapping[str, int] = field(default_factory=dict)
    #: The lines of a handle refused because they disagree about the vouch: two ids, or a
    #: vouched line beside a bare one. Each is in :attr:`malformed` too; this says why.
    conflicted: tuple[str, ...] = ()
    #: Unresolved placeholder lines (`2 <NEW_MAINTAINER>`). They grant nothing, and are kept
    #: here so `doctor` can name them (D69).
    skipped: tuple[str, ...] = ()
    #: Handles named by more than one accepted line. The highest level wins, so a line added to
    #: demote a handle changes nothing, and its failure looks like success (D69).
    duplicated: tuple[str, ...] = ()

    def __contains__(self, handle: object) -> bool:
        return normalise_handle(str(handle)) in self.levels

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(self.levels))

    def __len__(self) -> int:
        return len(self.levels)

    def __bool__(self) -> bool:
        return bool(self.levels)

    def __eq__(self, other: object) -> bool:
        """Equal to another :class:`Trust` by levels and vouches, and to a set by handles."""
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
        """The account id `handle`'s line vouches for, or None when it vouches for none (D68)."""
        found = self.vouched.get(normalise_handle(handle))
        return int(found) if found is not None else None

    def at_least(self, level: int) -> tuple[str, ...]:
        """Every handle at or above `level`, sorted."""
        return tuple(sorted(h for h, lvl in self.levels.items() if lvl >= int(level)))


def parse_trust(text: str) -> Trust:
    """``<level> <handle> [vouch:<id>]`` or a bare ``<handle>``; ``#`` comments and blanks ignored.

    A bare handle gets :data:`DEFAULT_LEVEL`. An entry containing ``<`` or ``>`` is an
    unresolved placeholder and never a handle (:attr:`Trust.skipped`).

    An ASCII-digit first token outside 1..3 refuses the whole line into
    :attr:`Trust.malformed`, rather than being clamped or read as a handle. So does any token
    after the handle other than a single ``vouch:<positive integer>``.

    A handle whose lines disagree about the vouch is refused on every line naming it: merging
    ``3 x`` with ``2 x vouch:1`` would give account 1 level 3 with no association (D68).
    """
    levels: dict[str, int] = {}
    implicit: list[str] = []
    malformed: list[str] = []
    conflicted: list[str] = []
    skipped: list[str] = []
    # Every accepted line, per handle: the id it vouches for (None for a bare line), and itself.
    lines_of: dict[str, list[tuple[int | None, str]]] = {}
    for raw_line in text.splitlines():
        entry = raw_line.split("#", 1)[0].strip()
        if not entry:
            continue
        if "<" in entry or ">" in entry:
            # An unresolved placeholder, never a handle. Recorded rather than dropped, so
            # `doctor` can name the line that `Identity.trust_file_ready()` refuses (D69).
            skipped.append(entry)
            continue
        parts = entry.split()
        level = DEFAULT_LEVEL
        handle_part = parts[0]
        rest = parts[1:]
        explicit = False
        if _is_level_token(parts[0]):
            if len(parts) < 2 or not 1 <= int(parts[0]) <= MAX_LEVEL:
                malformed.append(entry)
                continue
            level = int(parts[0])
            handle_part = parts[1]
            rest = parts[2:]
            explicit = True
        # Nothing here judges what the handle starts with: `vouched`, `voucherifyio` and
        # `vouchio` are real accounts. A vouch with no handle before it, `2 vouch:193453438`,
        # carries a colon and is refused by `_HANDLE_RE` below.
        #
        # Every token after the handle must be one the gate reads. `2 nathan 193453438`, the id
        # pasted without the keyword, is refused rather than read as a plain level-2 line (D69).
        pinned: int | None = None
        unreadable = False
        for token in rest:
            match = _VOUCH_RE.fullmatch(token)
            if match is None or pinned is not None:
                unreadable = True
                break
            pinned = int(match.group(1))
        if unreadable:
            malformed.append(entry)
            continue
        handle = normalise_handle(handle_part)
        if not handle:
            continue
        if not _HANDLE_RE.fullmatch(handle):
            malformed.append(entry)
            continue
        if not explicit:
            implicit.append(handle)
        # A handle listed twice keeps the highest level it was given, provided its lines agree
        # about the vouch, which the loop below settles.
        levels[handle] = max(level, levels.get(handle, 0))
        lines_of.setdefault(handle, []).append((pinned, entry))
    vouched: dict[str, int] = {}
    duplicated: list[str] = []
    for handle, lines in lines_of.items():
        pins = {pin for pin, _entry in lines}
        if len(pins) == 1:
            (only,) = pins
            if only is not None:
                vouched[handle] = only
            if len(lines) > 1:
                duplicated.append(handle)
            continue
        levels.pop(handle, None)
        refused = [entry for _pin, entry in lines]
        malformed.extend(refused)
        conflicted.extend(refused)
    return Trust(
        levels=levels,
        implicit=tuple(sorted(set(implicit) & set(levels))),
        malformed=tuple(malformed),
        vouched=vouched,
        conflicted=tuple(conflicted),
        skipped=tuple(skipped),
        duplicated=tuple(sorted(set(duplicated) & set(levels))),
    )


def load_trust(path: Path) -> Trust:
    """Read ``.harness/trust.txt``. A missing or unreadable file trusts nobody.

    ValueError is caught beside OSError. This runs inside ``build_context``, so an exception
    here would kill the command rather than refuse a line, and `doctor` gates discover.yml,
    feedback.yml and implement.yml under ``set -e``. A parser fault costs the file's contents
    and no more.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return Trust()
    try:
        return parse_trust(text)
    except ValueError:  # pragma: no cover - defence in depth; no known input reaches this
        return Trust()


def is_authorised(
    handle: str,
    author_association: str,
    trusted: Trust | frozenset[str] | set[str],
    *,
    min_level: int = 1,
    user_id: object = None,
) -> bool:
    """Both halves: the trust file (the operator's intent) and the association (GitHub's).

    Neither alone suffices, and the handle's level must reach `min_level` (B270). A plain set
    of handles is accepted, and every handle in one counts as :data:`DEFAULT_LEVEL`.

    A vouched line replaces the association half and only that half. A login can be renamed
    away and claimed by somebody else, while a numeric account id is never reused, so a vouched
    handle passes exactly when `user_id` equals the vouched id, whatever the association. The
    level still caps what either may do (D68).

    Callers with a comment in hand use :func:`comment_authorised`.
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

    Issue comments, pull request review comments and reviews all carry these three fields.
    Nothing else is read, and never the body, which stays unread until the gate passes (B273).
    """
    user = comment.get("user") or {}
    if not isinstance(user, Mapping):
        user = {}
    login = str(user.get("login") or "").strip()
    association = str(comment.get("author_association") or "").strip().upper()
    return login, association, parse_user_id(user.get("id"))


def comment_authorised(comment: Mapping[str, Any], trusted: Any, *, min_level: int = 1) -> bool:
    """The actor gate for one comment, on every surface (B131, B270).

    The sweep, `harness ack` and revise's review filter all call this, so the association, the
    vouch that can replace it, and the level are applied in one place for every surface.
    """
    login, association, uid = comment_author(comment)
    if not login:
        return False
    return is_authorised(login, association, trusted, min_level=min_level, user_id=uid)


# --------------------------------------------------------------------------------------
# Reading the file back, for the CLI and `doctor`. Pure, and kept here because why a line
# grants nothing is decided by the gate's own rules (D69).
# --------------------------------------------------------------------------------------


def handle_shaped(handle: str) -> bool:
    """True when `handle` could be a GitHub login at all (letters, digits, hyphens)."""
    return bool(_HANDLE_RE.fullmatch(normalise_handle(handle)))


@dataclass(frozen=True)
class Entry:
    """One accepted handle, and how its line is honoured."""

    handle: str
    level: int
    level_name: str
    #: ``vouch`` when the line names an account id, ``association`` when GitHub must vouch for
    #: it instead.
    route: str
    vouched_id: int | None


def describe(trusted: Trust) -> tuple[Entry, ...]:
    """Every accepted handle, most privileged first, with the route its line takes."""
    out: list[Entry] = []
    for handle in sorted(trusted.levels, key=lambda h: (-int(trusted.levels[h]), h)):
        level = int(trusted.levels[handle])
        pinned = trusted.vouched_id(handle)
        out.append(
            Entry(
                handle=handle,
                level=level,
                level_name=LEVEL_NAMES.get(level, str(level)),
                route="vouch" if pinned is not None else "association",
                vouched_id=pinned,
            )
        )
    return tuple(out)


def _handle_token(line: str) -> str:
    """The token a refused line meant as its handle, for saying what is wrong with it."""
    parts = line.split()
    if not parts:
        return ""
    if _is_level_token(parts[0]) and len(parts) > 1:
        return parts[1]
    return parts[0]


def refusals(trusted: Trust) -> tuple[tuple[str, str], ...]:
    """``(line, what is wrong with it)`` for every line that grants nothing.

    Each kind of refusal has its own fix, so each gets its own sentence: a level out of range
    is a typo, a placeholder is a line to finish, a handle that is not a login is the wrong
    text, and a disagreement about an account is two lines to be made one.
    """
    conflicted = set(trusted.conflicted or ())
    out: list[tuple[str, str]] = []
    for line in trusted.malformed:
        parts = line.split()
        token = _handle_token(line)
        rest = parts[2:] if parts[:1] and _is_level_token(parts[0]) else parts[1:]
        if line in conflicted:
            what = "disagrees with another line about which account its handle is (D68)"
        elif token and not handle_shaped(token):
            # Ahead of the vouch test: in `2 vouch:193453438` the word sits in the handle
            # position, and the fault is the missing handle rather than the vouch.
            what = "names something that is not a GitHub login"
        elif VOUCH_WORD in line.lower():
            what = "carries a vouch that is not one (D68)"
        elif rest:
            what = "carries a token after the handle that is not `vouch:<id>`"
        else:
            what = "is not a level"
        out.append((line, what))
    for line in trusted.skipped:
        out.append((line, "is an unresolved placeholder"))
    return tuple(out)


@dataclass(frozen=True)
class Tier:
    """One level, and the verbs it may give."""

    level: int
    name: str
    verbs: tuple[str, ...]


def tier_table(verb_level: Mapping[str, int]) -> tuple[Tier, ...]:
    """One row per level, most privileged first, built from `verb_level`.

    The mapping is a parameter because `keywords` imports this module, so reading
    `keywords.VERB_LEVEL` here would be a cycle. Taking it as an argument also keeps the table
    a function of the levels the gate enforces, so prose checked against it cannot drift.
    """
    return tuple(
        Tier(
            level=level,
            name=LEVEL_NAMES.get(level, str(level)),
            verbs=tuple(sorted(v for v, lvl in verb_level.items() if int(lvl) == level)),
        )
        for level in range(MAX_LEVEL, -1, -1)
    )
