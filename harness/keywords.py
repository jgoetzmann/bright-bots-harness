"""Keyword commands and the actor gate (handoff 8, 9.2 - B131-B135, B140, B141)."""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from harness.trust import MAX_LEVEL, is_authorised

if TYPE_CHECKING:  # annotation only; no import-time dependency on ledger.py
    from harness.ledger import Ledger

VERBS: tuple[str, ...] = (
    "revise", "reject", "fix", "rebase", "stop", "split", "queue",
    # Delivery 4: asking for work rather than steering work that exists.
    "work", "audit", "promote", "go", "ask",
)

#: B270/B272/D60 - the level each verb needs. 3 is the operator, 2 a maintainer, 1 an asker.
#: The line between 3 and 2 is halt versus end: level 2 can park an item and put it back,
#: level 3 can abandon it. A product maintainer needs to be able to stop something heading at
#: their repository without the operator; they do not need to close the book on it.
VERB_LEVEL: dict[str, int] = {
    "ask": 1,
    "work": 2,
    "queue": 2,
    "audit": 2,
    "promote": 2,
    "go": 2,
    "revise": 2,
    "fix": 2,
    "rebase": 2,
    "split": 2,
    "stop": 2,
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
_COMMAND_RE = re.compile(
    r"^\s*(?:@[\w-]+[ \t]+)*/harness\s+(\w+)([^\n]*)", re.MULTILINE | re.IGNORECASE
)
_THREAD_NUMBER_RE = re.compile(r"/(?:issues|pulls)/(\d+)/?$")
_EPOCH = "1970-01-01T00:00:00Z"


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


def parse(body: str) -> tuple[str, str] | None:
    """``/harness <verb> [args]`` on its own line -> ``(verb, args)``; anything else -> None."""
    if not isinstance(body, str):
        return None
    match = _COMMAND_RE.search(body)
    if match is None:
        return None
    verb = match.group(1).lower()
    if verb not in VERBS:
        return None
    return verb, match.group(2).strip()


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
    cid = comment_id(comment)
    if ledger.seen(cid):
        return None
    author = str(((comment.get("user") or {}).get("login")) or "").lstrip("@").lower()
    if machine and author == str(machine).lstrip("@").lower():
        return None
    if not authorise(comment, trusted, ledger):
        return None
    parsed = parse(comment.get("body") or "")
    if parsed is None:
        return None
    ledger.mark_seen(cid)
    verb, raw_args = parsed
    actor = str(comment["user"]["login"])
    level = trusted.level_of(actor) if hasattr(trusted, "level_of") else 1

    # B270: the verb's own level, checked after parsing because the verb is what says which
    # level is needed. The level-1 gate above is what keeps a level-0 body unparsed (B273).
    needed = VERB_LEVEL.get(verb, 3)
    if level < needed:
        ledger.count_denied(actor)
        return Command(
            verb="__denied__",
            args=f"/harness {verb} needs level {needed}; @{actor} is level {level}",
            surface=surface,
            number=int(number),
            comment_id=cid,
            actor=actor,
            level=level,
        )

    args, forced = split_force(raw_args)
    if forced and level < MAX_LEVEL:
        # B284: refused, but the command itself still stands -- the item is queued and waits.
        forced = False
        args = f"{args} (--force ignored: it needs level {MAX_LEVEL})".strip()
    return Command(
        verb=verb,
        args=args,
        surface=surface,
        number=int(number),
        comment_id=cid,
        actor=actor,
        force=forced,
        level=level,
    )


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
    since = ledger.cursors.get("notifications_last_seen") or _EPOCH
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
            command = command_from(
                comment,
                surface=surface,
                number=number,
                trusted=trusted,
                ledger=ledger,
                machine=machine,
            )
            if command is not None:
                commands.append(command)

    if inbox_issue:
        read(self_repo, "inbox", int(inbox_issue))
    for notification in gh.notifications(since):
        target = thread_target(
            notification,
            self_repo=self_repo,
            upstream_repo=upstream_repo,
            inbox_issue=inbox_issue,
        )
        if target is None:
            continue
        read(*(target[0], target[1], target[2]))
    ledger.cursors["notifications_last_seen"] = now_iso
    return commands
