"""Keyword commands and the actor gate (handoff 8, 9.2 - B131-B135, B140, B141)."""
from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from harness.errors import GitHubError
from harness.trust import MAX_LEVEL, is_authorised

log = logging.getLogger(__name__)

if TYPE_CHECKING:  # annotation only; no import-time dependency on ledger.py
    from harness.ledger import Ledger

VERBS: tuple[str, ...] = (
    # Getting work, and getting answers.
    "work", "ask", "status", "audit", "promote",
    # Steering work that exists.
    "revise", "rebase", "stop", "go", "split",
    # The switch.
    "halt", "resume",
)

#: Older names, still accepted. Three pairs were merged because the SURFACE already told them
#: apart, so the second name only ever added a way to be wrong:
#:
#:   fix    -> revise   both mean "redo it with my notes"; a proposal PR re-proposes, a delivery
#:                      pull request re-implements. One word, right thing by context.
#:   reject -> stop     the same action in the same code path -- close the pull request, end the
#:                      item. `reject` only ever encoded which gate you were standing at.
#:   queue  -> go       both mean "proceed with this": a suggestion becomes approved, a blocked
#:                      item goes back in the queue. Which one depends on where it already is.
#:   usage  -> status   the CLI has always called this `status`; two names for one report was a
#:                      needless thing to remember.
#:
#:   ledger -> status   not an old verb at all: the operator typed `/harness ledger` twice on the
#:                      live inbox, reaching for the CLI subcommand of that name. `status` is
#:                      what the CLI would have shown them, so it is what they get.
#:   help   -> status   the first thing anyone types at something that takes commands. `status`
#:                      answers, and every reply carries the pointer to the rest.
#:
#: Kept as aliases rather than removed: comments already written should not stop working, and a
#: verb that silently does nothing is the worst failure this surface has.
ALIASES: dict[str, str] = {
    "fix": "revise",
    "reject": "stop",
    "queue": "go",
    "usage": "status",
    "ledger": "status",
    "help": "status",
}

#: B270/B272/D60 - the level each verb needs. 3 is the operator, 2 a maintainer, 1 an asker.
#: The line between 3 and 2 is halt versus end: level 2 can park an item and put it back,
#: level 3 can abandon it. A product maintainer needs to be able to stop something heading at
#: their repository without the operator; they do not need to close the book on it.
VERB_LEVEL: dict[str, int] = {
    # Reading changes nothing.
    "ask": 1,
    "status": 1,
    # Creating and steering work.
    "work": 2,
    "audit": 2,
    "promote": 2,
    "go": 2,
    "revise": 2,
    "rebase": 2,
    "split": 2,
    "stop": 2,
    # Stopping the harness is the operator's, and so is starting it again. `halt` could
    # arguably be lower -- anyone who can see something going wrong should be able to stop it --
    # but it is paired with `resume` here, and a level that can lift a halt is a level that can
    # undo somebody else's decision to stop.
    "halt": 3,
    "resume": 3,
    # Not a live verb -- an alias with a level of its own. `reject` always meant "end this for
    # good", which is the half of `stop` that level 2 does not get (see `_act_on_command`).
    "reject": 3,
}

#: B283/D62 - appended to a command by the operator to start it now instead of on Monday.
FORCE_FLAG = "--force"

#: `/harness <verb>` at the start of a line, after any number of leading @mentions.
#:
#: Two deliberate loosenings, each for a form somebody will certainly type:
#:
#: **Case.** Every page here says the harness is operated from a phone, and a phone
#: autocapitalises the first word of a comment — so the most likely first command anyone ever
#: types is `/Harness work ...`. The verb is lower-cased below, so the vocabulary is unchanged;
#: only the shouting is forgiven.
#:
#: **Leading mentions.** On the product repository a mention is not decoration, it is the
#: delivery mechanism: `sweep` reads notifications, and the machine account is not subscribed to
#: an issue it has never touched, so `@jgoetzmann-bot` is the only thing that makes a cold thread
#: visible at all. The handoff's own acceptance A3 says to write
#: `@jgoetzmann-bot /harness work` — which, until this, parsed to nothing.
#:
#: What is NOT loosened is the anchor. Only @mentions may precede the command, so prose that
#: happens to contain it — `as discussed, /harness stop` — is still prose.
#: Both spellings, because both get typed: `/harness work …` and `/harness-work …`. The
#: hyphenated form makes each command a single token, which is what makes several of them in one
#: comment read naturally rather than looking like a sentence that got away from someone.
_COMMAND_RE = re.compile(
    r"^\s*(?:@[\w-]+[ \t]+)*/harness[ \t-]+(\w+)([^\n]*)", re.MULTILINE | re.IGNORECASE
)
_THREAD_NUMBER_RE = re.compile(r"/(?:issues|pulls)/(\d+)/?$")
_EPOCH = "1970-01-01T00:00:00Z"

#: How far a FIRST sweep looks back when the ledger carries no cursor yet.
#:
#: Not to the epoch. An unset cursor means "this has never run here", and the honest reading of
#: that is "start now", not "act on every `/harness` comment anyone has ever left". The first
#: sweep after the kill switch comes off is exactly when a backlog would be most surprising and
#: least wanted -- a stale `stop` from a month ago is not an instruction, it is history. One
#: poll interval of overlap keeps a comment left moments before the first run from being lost.
FIRST_SWEEP_LOOKBACK_HOURS = 3


@dataclass(frozen=True)
class Command:
    verb: str
    args: str
    surface: str  # proposal_pr, delivery_pr, issue or product_issue
    number: int
    comment_id: str
    actor: str
    #: B283: the operator asked for this to skip the run window. Level 3 only.
    force: bool = False
    #: The actor's level, so a refusal can say what it would have needed.
    level: int = 0
    #: The word actually typed, when an alias was used. The issue thread is the log, and a
    #: log that records `stop` for a comment that said `reject` cannot be read back honestly.
    typed: str = ""
    #: Something to tell the actor that is NOT part of what they asked for -- a refused
    #: `--force`, say. It rides beside `args` rather than inside it: folding it in made the
    #: refusal notice part of the request, so `/harness work #5 --force` from level 2 stopped
    #: looking like a pointer to issue 5 and opened a free-text item titled with the notice.
    note: str = ""


def comment_id(comment: Mapping[str, Any]) -> str:
    """The identity recorded for B135: the node ID when present, else the numeric id as text."""
    node_id = comment.get("node_id")
    if node_id:
        return str(node_id)
    return str(comment.get("id", ""))


def authorise(comment: Mapping[str, Any], trusted: Any, ledger: Ledger, *, min_level: int = 1) -> bool:
    """The actor gate (B131, B132): login and association only; denial is a bare False.

    B270 adds the level. The default is 1 -- "is this person in the file at all" -- because
    `command_from` runs this before it parses anything, and B273 requires that a level-0
    comment's body is never read. The verb's own level is checked after parsing.
    """
    user = comment.get("user") or {}
    login = str(user.get("login") or "")
    association = str(comment.get("author_association") or "")
    if is_authorised(login, association, trusted, min_level=min_level):
        return True
    ledger.count_denied(login)
    return False


def resolve(verb: str) -> str:
    """The current name for `verb`, following :data:`ALIASES`."""
    lowered = str(verb).lower()
    return ALIASES.get(lowered, lowered)


#: At most this many commands from one comment. Not a limit anybody types into: it is the
#: blast radius of a paste. Every command posts a reply and some spend, so an unbounded comment
#: is an unbounded number of writes against GitHub's content-creation limit -- which arrives as
#: an error on a *later, unrelated* command, after this comment was already marked seen.
MAX_COMMANDS_PER_COMMENT = 10

#: A fenced block is somebody SHOWING a command, not giving one. This is not hypothetical:
#: docs/COMMANDS.md ships a three-command block under the words "that is a normal thing to
#: send", and pasting it to explain the syntax would have run all three.
_FENCE_RE = re.compile(r"^[ \t]*(?:```|~~~).*?(?:^[ \t]*(?:```|~~~)|\Z)", re.M | re.S)


def strip_fences(body: str) -> str:
    """`body` with fenced code blocks blanked out, line count preserved."""
    return _FENCE_RE.sub(lambda m: "\n" * m.group(0).count("\n"), body)


def parse_all(body: str) -> list[tuple[str, str]]:
    """Every command in `body`, in the order typed, as `(resolved verb, args)` pairs.

    Reads every `/harness` line rather than only the first: a person with three things to say
    should be able to say three things, and under the old rule one unknown verb at the top
    discarded everything under it.
    """
    return [(verb, args) for verb, args, _typed in parse_typed(body)]


def parse_typed(body: str) -> list[tuple[str, str, str]]:
    """`parse_all`, plus the word that was actually typed -- which may be an alias."""
    out: list[tuple[str, str, str]] = []
    for match in _COMMAND_RE.finditer(strip_fences(body or "")):
        typed = match.group(1).lower()
        verb = resolve(typed)
        if verb not in VERBS:
            # Skipped, not fatal. Discarding the whole comment on one typo is what the
            # first-line-only rule did, and it did it silently.
            continue
        out.append((verb, match.group(2).strip(), typed))
        if len(out) >= MAX_COMMANDS_PER_COMMENT:
            break
    return out


def parse(body: str) -> tuple[str, str] | None:
    """The first command in `body`, or None. Kept for callers that want exactly one."""
    found = parse_all(body)
    return found[0] if found else None


def split_force(args: str) -> tuple[str, bool]:
    """``<args> --force`` -> ``(<args>, True)`` (B283). The flag may sit anywhere in the args.

    Case-insensitive, like the verb. `--FORCE` used to do two wrong things at once: the flag was
    not honoured, and it was not removed either, so it survived into the notes handed to a stage
    as if somebody had meant to write it. A flag whose entire purpose is "start this now" must
    not be quietly dropped over a shift key.
    """
    parts = [part for part in (args or "").split() if part]
    forced = any(part.lower() == FORCE_FLAG for part in parts)
    return " ".join(part for part in parts if part.lower() != FORCE_FLAG), forced


def command_from(
    comment: Mapping[str, Any],
    *,
    surface: str,
    number: int,
    trusted: frozenset[str],
    ledger: Ledger,
    machine: str = "",
) -> Command | None:
    """Replay check -> not itself -> actor gate -> parse -> mark seen -> Command (B133, B135).

    The machine account is in `trust.txt` -- it has to be, so a person can steer the harness
    from it -- which means that from Delivery 4, when the harness started replying in the very
    threads it sweeps, it could in principle command itself. Nothing it writes today puts
    `/harness` at the start of a line, but "today" is not a guarantee, and the failure mode is a
    loop that spends the allowance. Refused structurally instead: the harness does not take
    orders from itself, whatever it happens to say.
    """
    found = commands_from(
        comment, surface=surface, number=number, trusted=trusted, ledger=ledger, machine=machine
    )
    return found[0] if found else None


def commands_from(
    comment: Mapping[str, Any],
    *,
    surface: str,
    number: int,
    trusted: frozenset[str],
    ledger: Ledger,
    machine: str = "",
) -> list[Command]:
    """Every command in one comment, in order.

    The comment is marked seen ONCE, after parsing, however many commands it carried: the replay
    guard is keyed on the comment, so marking per command would let a later sweep re-run the ones
    an earlier one had not reached.
    """
    cid = comment_id(comment)
    if ledger.seen(cid):
        return []
    author = str(((comment.get("user") or {}).get("login")) or "").lstrip("@").lower()
    if machine and author == str(machine).lstrip("@").lower():
        return []
    if not authorise(comment, trusted, ledger):
        return []
    parsed = parse_typed(comment.get("body") or "")
    if not parsed:
        return []
    ledger.mark_seen(cid)
    actor = str(comment["user"]["login"])
    level = trusted.level_of(actor) if hasattr(trusted, "level_of") else 1
    return [
        _one(v, a, typed=t, surface=surface, number=number, cid=cid, actor=actor, level=level,
             ledger=ledger)
        for v, a, t in parsed
    ]


def _one(
    verb: str,
    raw_args: str,
    *,
    typed: str = "",
    surface: str,
    number: int,
    cid: str,
    actor: str,
    level: int,
    ledger: Ledger,
) -> Command:
    """One parsed `(verb, args)` pair as a Command, with the level gate applied."""

    # B270: the verb's own level, checked after parsing because the verb is what says which
    # level is needed. The level-1 gate in `commands_from` keeps a level-0 body unparsed (B273).
    #
    # The TYPED word decides, when it has a level of its own. `reject` was level 3 and `stop` is
    # level 2, so resolving before gating would have handed a terminal verb to every maintainer
    # -- an authority change nobody asked for, arriving as a side effect of a rename.
    needed = VERB_LEVEL.get(typed or verb, VERB_LEVEL.get(verb, 3))
    if level < needed:
        ledger.count_denied(actor)
        return Command(
            verb="__denied__",
            args=f"/harness {typed or verb} needs level {needed}; @{actor} is level {level}",
            surface=surface,
            number=int(number),
            comment_id=cid,
            actor=actor,
            level=level,
        )

    args, forced = split_force(raw_args)
    note = ""
    if forced and level < MAX_LEVEL:
        # B284: refused, but the command itself still stands -- the item is queued and waits.
        # The notice goes to `note`, never into `args`: `args` is what the actor asked for, and
        # a stage reads it as such.
        forced = False
        note = (
            f"`--force` needs level {MAX_LEVEL} and @{actor} is level {level}, so this waits "
            "for the run window like anything else."
        )
    return Command(
        verb=verb,
        args=args,
        surface=surface,
        number=int(number),
        comment_id=cid,
        actor=actor,
        force=forced,
        level=level,
        note=note,
    )


def _first_sweep_since(now_iso: str) -> str:
    """`FIRST_SWEEP_LOOKBACK_HOURS` before `now_iso`, or the epoch if that cannot be parsed."""
    try:
        now = datetime.strptime(str(now_iso), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return _EPOCH
    back = now - timedelta(hours=FIRST_SWEEP_LOOKBACK_HOURS)
    return back.strftime("%Y-%m-%dT%H:%M:%SZ")


def thread_number(url: str) -> int | None:
    """``.../issues/12`` or ``.../pulls/12`` -> 12; anything else -> None."""
    match = _THREAD_NUMBER_RE.search(url)
    if match is None:
        return None
    return int(match.group(1))


def thread_target(
    notification: Mapping[str, Any],
    *,
    self_repo: str,
    upstream_repo: str,
    inbox_issue: int = 0,
) -> tuple[str, str, int] | None:
    """``(repo, surface, number)`` for a notification the sweep reads, else None."""
    subject = notification.get("subject") or {}
    repo = str((notification.get("repository") or {}).get("full_name") or "")
    number = thread_number(str(subject.get("url") or ""))
    if number is None:
        return None
    kind = subject.get("type")
    on_self = repo.lower() == self_repo.lower()
    on_upstream = repo.lower() == upstream_repo.lower()
    if kind == "PullRequest" and on_self:
        return repo, "proposal_pr", number
    if kind == "PullRequest" and on_upstream:
        return repo, "delivery_pr", number
    if kind == "Issue" and on_self and inbox_issue and number == int(inbox_issue):
        # B241: the inbox is a conversation, not a work item, whatever labels it carries. Its
        # own surface keeps `_item_for_command` from ever resolving it to itself.
        return repo, "inbox", number
    if kind == "Issue" and on_self:
        return repo, "issue", number
    # B242: an issue on the PRODUCT repository is a surface of its own, never `issue`. The two
    # repositories number independently, and `_item_for_command` maps `issue` straight to the
    # number -- so calling this one `issue` would make a command on product #633 act on harness
    # work item #633.
    if kind == "Issue" and on_upstream:
        return repo, "product_issue", number
    return None


def sweep(
    gh: Any,
    *,
    ledger: Ledger,
    trusted: frozenset[str],
    now_iso: str,
    self_repo: str,
    upstream_repo: str,
    inbox_issue: int = 0,
    machine: str = "",
) -> list[Command]:
    """B140/B141: notifications since the cursor -> comments of each thread -> commands.

    B235/B240 add one thread that is read whether or not it notified. A notification only
    arrives on a thread the account is subscribed to, and an account is not subscribed to an
    issue it has never touched -- so on the notification feed alone the very first request ever
    made is the one that silently vanishes. The inbox exists to be commented on by people who
    have not read this paragraph, so it is polled.
    """
    since = ledger.cursors.get("notifications_last_seen")
    if not since:
        since = _first_sweep_since(now_iso)
    commands: list[Command] = []
    seen_threads: set[tuple[str, int]] = set()

    def read(repo: str, surface: str, number: int) -> None:
        if (repo.lower(), number) in seen_threads:
            return
        seen_threads.add((repo.lower(), number))
        comments = list(gh.issue_comments(repo, number))
        if surface in ("proposal_pr", "delivery_pr"):
            # Only a pull request has review comments; asking an issue for them is a 404.
            comments.extend(gh.pull_review_comments(repo, number))
        for comment in comments:
            commands.extend(
                commands_from(
                    comment,
                    surface=surface,
                    number=number,
                    trusted=trusted,
                    ledger=ledger,
                    machine=machine,
                )
            )

    if inbox_issue:
        read(self_repo, "inbox", int(inbox_issue))
    try:
        notifications = list(gh.notifications(since))
    except GitHubError as exc:
        # The notifications endpoint needs a scope of its own, and I-15 gives the machine PAT
        # `public_repo` and nothing else -- so a correctly-configured token can be refused here.
        # Losing the feed costs cold product-issue mentions; letting the refusal out of `sweep`
        # costs the INBOX TOO, which is the surface people who have read no documentation use.
        # The inbox is read before this line for exactly that reason, so keep what it found.
        log.warning("notifications unavailable, inbox only: %s", exc)
        return commands
    for notification in notifications:
        target = thread_target(
            notification,
            self_repo=self_repo,
            upstream_repo=upstream_repo,
            inbox_issue=inbox_issue,
        )
        if target is None:
            continue
        read(*(target[0], target[1], target[2]))
    # Advanced only on a feed that actually came back. Moving it after a failure would skip the
    # window the failed call covered, and those mentions would never be read at all.
    ledger.cursors["notifications_last_seen"] = now_iso
    return commands
