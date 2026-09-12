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

#: D69: the shape of a GitHub login -- ASCII letters, digits and hyphens, at most 39 of them.
#: A handle outside it can never equal a `user.login`, so a line carrying one is a grant to
#: nobody. `nathan@example.com` and `nathan,` both registered literally before this, and were
#: then refused for ever without a word to anybody.
_HANDLE_RE = re.compile(r"[A-Za-z0-9-]{1,39}")


def normalise_handle(handle: str) -> str:
    """Canonical form of a GitHub login: stripped, no leading ``@``, lower-cased (R4.5)."""
    return handle.strip().lstrip("@").lower()


def _is_level_token(token: str) -> bool:
    """True when `token` is an attempt at the level column: ASCII decimal digits, nothing else.

    `str.isdigit()` alone is also true for digits that are not ASCII, and both kinds were
    defects when probed against the shipped parser. An Arabic-Indic three satisfied ``int()``
    and granted OPERATOR level with nothing recorded as malformed -- from a character the
    handle rule beside it would have refused outright. A superscript two raised ValueError,
    which ``load_trust`` did not catch, so it escaped into ``build_context`` and every command
    and workflow built on one. Neither can be a level, so neither is read as one: the line
    falls through to the handle path and is refused and named there, like any other.

    ASCII-ness is the whole added rule. A long run of digits is still an attempt at a level,
    and still refused as out of range: narrowing this to "at most N digits" would send a
    21-digit first token to the handle path, where it is login-shaped and would be granted
    :data:`DEFAULT_LEVEL` in silence.
    """
    return token.isascii() and token.isdigit()


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
    #: D68: the lines of a handle refused because they disagree about the vouch -- two ids, or
    #: a vouched line beside a bare one. Each is in :attr:`malformed` too; this says why.
    conflicted: tuple[str, ...] = ()
    #: D69: unresolved placeholder lines (`2 <NEW_MAINTAINER>`). They grant nothing, as they
    #: always did; recording them is what lets `doctor` name a line that used to vanish.
    skipped: tuple[str, ...] = ()
    #: D69: handles named by more than one accepted line. The highest level still wins, so a
    #: line added to DEMOTE somebody does nothing at all -- the one edit here whose failure
    #: looks exactly like success.
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
    line. A vouched handle is refused entirely -- every line naming it -- when those lines do
    not all carry the same vouch: two DIFFERENT ids, or one line vouching and another naming the
    handle bare. Which account the operator meant is exactly the question the vouch exists to
    settle, and merging the lines would answer it with a grant neither line makes alone:
    ``3 x`` with ``2 x vouch:1`` would otherwise give account 1 level 3 with no association.
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
            # An unresolved placeholder, never a handle. D69 records it rather than dropping
            # it: it granted nothing before and grants nothing now, but a line that vanishes
            # cannot be named by `doctor` -- while the same line fails
            # `Identity.trust_file_ready()` for a reason nothing connects back to it.
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
        # Nothing here judges what the handle STARTS with. A rule that did -- any handle
        # beginning `vouch` refused the whole line -- was wrong twice over: it refused a login
        # that merely shares those five letters (`vouched`, `voucherifyio` and `vouchio` are
        # real accounts), and `refusals` then blamed the vouch, which was the well-formed half,
        # sending the operator to fix the wrong end of the line. The case it was written for,
        # `2 vouch:193453438` -- a vouch with no handle in front of it -- carries a colon and is
        # refused by `_HANDLE_RE` below, which is the rule that knows what a login looks like.
        #
        # D69: EVERY token after the handle must be one the gate actually reads. Before this
        # only tokens beginning `vouch` were inspected and the rest were discarded in silence,
        # so `2 nathan 193453438` -- the id pasted without the keyword, which is exactly what a
        # hurried operator types -- parsed to a plain level-2 line. It looked like a vouch,
        # vouched for nobody, and granted nothing anywhere he was not already a collaborator.
        # Refused for D68's reason: read as "no vouch" the line still grants a level, which is
        # not what its author meant either.
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
        # A handle listed twice keeps the highest level it was given -- when its lines agree
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
    """Read ``.harness/trust.txt``. A missing or unreadable file is nobody, not everybody.

    ValueError is caught beside OSError as defence in depth. This is called from
    ``build_context``, so an exception raised here is not a refused line but a dead command --
    and `doctor` gates discover.yml, feedback.yml and implement.yml under ``set -e``, so it
    would be a dead fleet. One such bug existed (see :func:`_is_level_token`) and is fixed; a
    parser fault must cost the file's contents, never everything the harness does.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return Trust()
    try:
        return parse_trust(text)
    except ValueError:  # pragma: no cover - no known input reaches this; that is the point
        return Trust()


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


# --------------------------------------------------------------------------------------
# D69: reading the file back. Pure, and here rather than in the CLI, because the reasons a
# line grants nothing are the gate's own rules -- `tests/test_trust_vouch.py::test_B330`
# fails the build if any other module decides for itself who is heard.
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
    #: ``vouch`` when the line names an account id, ``association`` when GitHub must vouch
    #: for it instead. The second is the half nobody can see from the commenter's side.
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

    Four ways to grant nothing with four different fixes, so they get four different
    sentences: a level out of range is a typo to correct, a placeholder is a line to finish, a
    handle that is not a login is the wrong text entirely, and a disagreement about an account
    is two lines that have to be made one.
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
            # Ahead of the vouch test, because `vouch` appearing anywhere in the line is true
            # of the HANDLE as well as of a trailing token: `2 vouch:193453438` was reported as
            # a bad vouch when the vouch was fine and the handle was missing.
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

    The mapping is a PARAMETER rather than an import: `keywords` imports this module, so
    reaching back for `keywords.VERB_LEVEL` here would be a cycle -- the one `links._who`
    already sidesteps with a function-local import. Taking it as an argument means the table
    is a function of the levels the gate actually enforces, and prose checked against it
    cannot drift from them.
    """
    return tuple(
        Tier(
            level=level,
            name=LEVEL_NAMES.get(level, str(level)),
            verbs=tuple(sorted(v for v, lvl in verb_level.items() if int(lvl) == level)),
        )
        for level in range(MAX_LEVEL, -1, -1)
    )
