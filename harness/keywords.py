"""Keyword commands: parsing `/harness` lines, and the actor gate (B131-B135, B140, B141)."""
from __future__ import annotations

import functools
import logging
import re
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from harness.errors import GitHubError
from harness.trust import MAX_LEVEL, comment_authorised

log = logging.getLogger(__name__)

if TYPE_CHECKING:  # annotation only; no import-time dependency on ledger.py
    from harness.ledger import Ledger

VERBS: tuple[str, ...] = (
    # Getting work, and getting answers.
    "work", "ask", "status", "audit", "promote",
    # Steering work that exists.
    "revise", "rebase", "stop", "go", "split",
    # The switches: stopping the spending, and lending it the calendar.
    "halt", "resume", "block",
)

#: Other names, still accepted. The surface says which sense is meant, so one verb serves both:
#:
#:   fix    -> revise   "redo it with my notes": a proposal PR re-proposes, a delivery pull
#:                      request re-implements.
#:   reject -> stop     close the pull request and end the item. `reject` keeps a level of its
#:                      own, below.
#:   queue  -> go       "proceed with this": a suggestion becomes approved, a blocked item goes
#:                      back in the queue.
#:   usage  -> status   the CLI's name for this report is `status`.
#:   ledger -> status   the CLI subcommand of that name, typed as a command.
#:   help   -> status   `status` answers, and every reply points to the rest.
#:   blocks -> block    the plural is what a person types with a count.
#:   sessions -> block  the unit a block is counted in.
#:
#: There is deliberately no `unblock`: an alias carries the verb and never the args, so it would
#: resolve to a bare `block` and *report* the standing grant rather than cancel it. A word that
#: looks like it cancels and does not is worse than no word; `/harness block 0` cancels, and
#: every reply says so.
#:
#: Aliases rather than removals, so comments already written keep working.
ALIASES: dict[str, str] = {
    "fix": "revise",
    "reject": "stop",
    "queue": "go",
    "usage": "status",
    "ledger": "status",
    "help": "status",
    "blocks": "block",
    "sessions": "block",
}

#: The level each verb needs: 3 the operator, 2 a maintainer, 1 an asker (B270). Level 2 can
#: park an item and put it back, so a product maintainer can stop something heading at their
#: repository without the operator; abandoning it for good is level 3.
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
    # `halt` is paired with `resume`, and a level that can lift a halt can undo someone else's
    # decision to stop.
    "halt": 3,
    "resume": 3,
    # `block` lifts the run window for the whole fleet over several sessions, and `--force`
    # already needs MAX_LEVEL to lift it for one item. A verb that lifts it for everything
    # cannot sit below the flag that lifts it for one. It is the operator's subscription being
    # spent, which is the same reason docs/FOR-MAINTAINERS.md §7 gives for `--force`.
    "block": 3,
    # Not a live verb, an alias with a level of its own: `reject` ends an item for good, the
    # half of `stop` that level 2 does not get (see `_act_on_command`).
    "reject": 3,
}

#: The verbs `harness ack` answers by itself, in the workflow that takes no lock (B440). One
#: place, because `ack` claims a comment only when every verb in it is one of these and the
#: sweep skips one only on the same test; two spellings of that rule could disagree.
ACK_ANSWERS: frozenset[str] = frozenset({"status"})

#: Appended to a command by the operator to start it outside the run window (B283).
FORCE_FLAG = "--force"

#: `/harness <verb>` or `/harness-<verb>` at the start of a line, after any leading @mentions.
#:
#: Case-insensitive, since a phone autocapitalises the first word of a comment; the verb is
#: lower-cased below, so the vocabulary is unchanged. Leading @mentions are allowed because on
#: the product repository `@jgoetzmann-bot` is what brings a thread the account has never
#: touched into the sweep. Only @mentions may precede the command, so prose that happens to
#: contain one -- `as discussed, /harness stop` -- is still prose.
_COMMAND_RE = re.compile(
    r"^\s*(?:@[\w-]+[ \t]+)*/harness[ \t-]+(\w+)([^\n]*)", re.MULTILINE | re.IGNORECASE
)
_THREAD_NUMBER_RE = re.compile(r"/(?:issues|pulls)/(\d+)/?$")
_EPOCH = "1970-01-01T00:00:00Z"

#: `@<machine account> <verb>` at the start of a line: the same command, said the obvious way.
#:
#: Naming the bot is what a person tries first, and until B435 it did nothing at all -- the two
#: comment-driven workflows woke only on `/harness`, so the run was skipped before any parser
#: saw the words. Restricted to the machine account, because `@nathan status` is a sentence
#: about Nathan and not a command.
#:
#: `(\w+)` cannot match `/harness`, so `@bot /harness work` parses once, through
#: :data:`_COMMAND_RE`, and never twice.
_MENTION_COMMAND_TEMPLATE = r"^[ \t]*@{handle}[ \t]+(\w+)([^\n]*)"

#: The same mention at the start of a line, with no verb required: what `ack` nudges about.
_MENTION_LINE_TEMPLATE = r"^[ \t]*@{handle}\b"


@functools.lru_cache(maxsize=8)
def _mention_re(mention: str) -> "re.Pattern[str] | None":
    """The `@<handle> <verb>` matcher for one handle, or None when there is no handle."""
    handle = str(mention or "").strip().lstrip("@")
    if not handle:
        return None
    return re.compile(
        _MENTION_COMMAND_TEMPLATE.format(handle=re.escape(handle)),
        re.MULTILINE | re.IGNORECASE,
    )


@functools.lru_cache(maxsize=8)
def _mention_line_re(mention: str) -> "re.Pattern[str] | None":
    """The matcher for a line that addresses `mention`, whatever follows it."""
    handle = str(mention or "").strip().lstrip("@")
    if not handle:
        return None
    return re.compile(
        _MENTION_LINE_TEMPLATE.format(handle=re.escape(handle)), re.MULTILINE | re.IGNORECASE
    )

#: How far a first sweep looks back when the ledger carries no cursor yet. An unset cursor
#: means the harness has never run here, so older comments are history and are left alone. One
#: poll interval of overlap keeps a comment left moments before that first run.
FIRST_SWEEP_LOOKBACK_HOURS = 3


@dataclass(frozen=True)
class Command:
    verb: str
    args: str
    surface: str  # proposal_pr, delivery_pr, issue or product_issue
    number: int
    comment_id: str
    actor: str
    #: The operator asked for this to skip the run window. Level 3 only (B283).
    force: bool = False
    #: The actor's level, so a refusal can say what it would have needed.
    level: int = 0
    #: The word actually typed, when an alias was used, so the thread records what was said.
    typed: str = ""
    #: Something to tell the actor that is no part of the request, such as a refused `--force`.
    #: It rides beside `args`, never inside: a stage reads `args` as what was asked for, so
    #: `/harness work #5 --force` from level 2 stays a pointer to issue 5.
    note: str = ""


def comment_id(comment: Mapping[str, Any]) -> str:
    """The identity recorded for B135: the node ID when present, else the numeric id as text."""
    node_id = comment.get("node_id")
    if node_id:
        return str(node_id)
    return str(comment.get("id", ""))


def authorise(comment: Mapping[str, Any], trusted: Any, ledger: Ledger, *, min_level: int = 1) -> bool:
    """The actor gate (B131, B132): login, association and account id; denial is a bare False.

    The default `min_level` of 1 asks only whether the person is in the file, because
    `command_from` runs this before parsing and a level-0 comment's body is never read (B273).
    The verb's own level is checked after parsing.

    The decision is `trust.comment_authorised`, the same call `harness ack` and revise's review
    filter make, so a vouched line is honoured on every surface or on none.
    """
    if comment_authorised(comment, trusted, min_level=min_level):
        return True
    user = comment.get("user") or {}
    ledger.count_denied(str((user.get("login") if isinstance(user, Mapping) else "") or ""))
    return False


def resolve(verb: str) -> str:
    """The current name for `verb`, following :data:`ALIASES`."""
    lowered = str(verb).lower()
    return ALIASES.get(lowered, lowered)


#: At most this many commands from one comment: the blast radius of a paste. Each command
#: posts a reply, and GitHub's content-creation limit arrives as an error on a later, unrelated
#: command, after this comment has been marked seen.
MAX_COMMANDS_PER_COMMENT = 10

#: A fenced block shows a command rather than giving one, so it is blanked before parsing.
#: docs/COMMANDS.md ships a three-command block that pasting would otherwise run.
_FENCE_RE = re.compile(r"^[ \t]*(?:```|~~~).*?(?:^[ \t]*(?:```|~~~)|\Z)", re.M | re.S)


def strip_fences(body: str) -> str:
    """`body` with fenced code blocks blanked out, line count preserved."""
    return _FENCE_RE.sub(lambda m: "\n" * m.group(0).count("\n"), body)


def parse_all(body: str, *, mention: str = "") -> list[tuple[str, str]]:
    """Every command in `body`, in the order typed, as `(resolved verb, args)` pairs."""
    return [(verb, args) for verb, args, _typed in parse_typed(body, mention=mention)]


def parse_typed(body: str, *, mention: str = "") -> list[tuple[str, str, str]]:
    """`parse_all`, plus the word that was actually typed, which may be an alias.

    `mention` is the machine account, and naming it at the start of a line is the second way to
    give a command (B435). Empty means the mention form is off, which is what every caller that
    does not know the account gets, so the `/harness` form is unchanged.
    """
    text = strip_fences(body or "")
    matches = list(_COMMAND_RE.finditer(text))
    mention_re = _mention_re(mention)
    if mention_re is not None:
        matches.extend(mention_re.finditer(text))
    # Both forms are commands, so a comment using each in turn runs them in the order typed.
    matches.sort(key=lambda found: found.start())
    out: list[tuple[str, str, str]] = []
    for match in matches:
        typed = match.group(1).lower()
        verb = resolve(typed)
        if verb not in VERBS:
            # An unknown verb is skipped; the other commands in the comment still run.
            continue
        out.append((verb, match.group(2).strip(), typed))
        if len(out) >= MAX_COMMANDS_PER_COMMENT:
            break
    return out


def parse(body: str, *, mention: str = "") -> tuple[str, str] | None:
    """The first command in `body`, or None. For callers that want exactly one."""
    found = parse_all(body, mention=mention)
    return found[0] if found else None


def mentions_without_command(body: str, mention: str) -> bool:
    """True when `body` addresses `mention` at the start of a line and gives it no verb (B436).

    The case the nudge exists for: somebody names the bot and then writes a sentence. Naming it
    mid-prose -- "thanks @bot" -- is not addressing it and gets nothing.
    """
    line_re = _mention_line_re(mention)
    if line_re is None:
        return False
    text = strip_fences(body or "")
    if not line_re.search(text):
        return False
    return not parse_typed(text, mention=mention)


def answered_by_ack(
    gh: Any,
    repo: str,
    comment: Mapping[str, Any],
    machine: str = "",
    *,
    review: bool = False,
) -> bool:
    """True when `ack` has already answered this comment, so the sweep says nothing (B441).

    The fact is a reaction one of `gh.machine_logins` left on the comment, not text in a
    reply: the harness republishes issue titles and model answers verbatim, so any marker it
    can publish is one a stranger can choose (B455). Anything unreadable answers False, so a
    failure here costs a repeated answer rather than a maintainer's command: the caller has
    already marked this run's commands seen and its `finally` commits that, so an exception
    escaping here would lose them for good (B458).
    """
    # Function-local: `keywords` is imported by the CLI, and `gh` must not be a hard dependency.
    from harness.gh import ANSWERED_REACTION, machine_logins

    reader = getattr(gh, "comment_reactions", None)
    if not callable(reader):
        return False
    ident = comment.get("id")
    if not ident:
        return False
    logins = machine_logins(machine)
    try:
        for row in reader(repo, ident, review=review):
            if str(row.get("content") or "").strip().lower() != ANSWERED_REACTION:
                continue
            author = str(((row.get("user") or {}).get("login")) or "").lstrip("@").lower()
            if author in logins:
                return True
    except (GitHubError, ValueError, TypeError, AttributeError) as exc:
        # Shapes the live API does not send -- a non-numeric id, a string where `user` should
        # be an object -- must not escape. The walk is inside the guard for that reason: see
        # the docstring on what an escaping exception costs (B458).
        log.warning("reactions unreadable for comment %s, answering it: %s", ident, exc)
        return False
    return False


def split_force(args: str) -> tuple[str, bool]:
    """``<args> --force`` -> ``(<args>, True)`` (B283). The flag may sit anywhere in the args.

    Case-insensitive, like the verb, and removed from the args whatever its case, so `--FORCE`
    is honoured and never reaches a stage as part of the request.
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

    The machine account is in `trust.txt`, so a person can steer the harness from it, and the
    harness replies in the threads it sweeps. A comment by the machine account is therefore
    never a command, so the harness cannot command itself into a loop.
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

    The comment is marked seen once, after parsing, however many commands it carried: the
    replay guard is keyed on the comment, so marking per command would let a later sweep re-run
    the ones an earlier one had not reached.
    """
    cid = comment_id(comment)
    if ledger.seen(cid):
        return []
    author = str(((comment.get("user") or {}).get("login")) or "").lstrip("@").lower()
    if machine and author == str(machine).lstrip("@").lower():
        return []
    if not authorise(comment, trusted, ledger):
        return []
    # The machine account is both the comment author this refuses above and the handle the
    # mention form names, so one parameter carries both and they cannot disagree (B435).
    parsed = parse_typed(comment.get("body") or "", mention=machine)
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

    # The verb's own level, checked after parsing because the verb says which level is needed;
    # the level-1 gate in `commands_from` keeps a level-0 body unparsed. The typed word decides
    # when it has a level of its own, so `reject` (3) is not gated as `stop` (2) (B270).
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
        # The flag is refused and the command still stands: the item is queued and waits. The
        # notice goes to `note`, never into `args`, which a stage reads as the request (B284).
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


#: How far each sweep re-reads before its cursor. A run that a comment started can reach the
#: sweep before GitHub delivers that comment's notification, and would move the cursor past it;
#: seen comment ids keep the overlap from answering anything twice (D84).
SWEEP_OVERLAP_MINUTES = 30


def _overlap_since(cursor: str) -> str:
    """``SWEEP_OVERLAP_MINUTES`` before ``cursor``, or ``cursor`` if it cannot be parsed."""
    try:
        at = datetime.strptime(str(cursor), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return cursor
    return (at - timedelta(minutes=SWEEP_OVERLAP_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ")


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
        # The inbox gets a surface of its own whatever labels it carries, which keeps
        # `_item_for_command` from resolving it to itself (B241).
        return repo, "inbox", number
    if kind == "Issue" and on_self:
        return repo, "issue", number
    # An issue on the product repository is a surface of its own. The two repositories number
    # independently, and `_item_for_command` maps `issue` straight to the number, so calling
    # this one `issue` would make a command on product #633 act on work item #633 (B242).
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
    thread: int = 0,
) -> list[Command]:
    """Notifications since the cursor -> comments of each thread -> commands (B140, B141).

    The inbox issue is read whether or not it notified. A notification arrives only on a thread
    the account is subscribed to, and it is not subscribed to an issue it has never touched, so
    on the feed alone a first request there would vanish (B240). ``thread`` is the issue or pull
    request in this repository a comment event names, read first for the same reason (D84).
    """
    cursor = ledger.cursors.get("notifications_last_seen")
    since = _overlap_since(cursor) if cursor else _first_sweep_since(now_iso)
    commands: list[Command] = []
    seen_threads: set[tuple[str, int]] = set()

    def read(repo: str, surface: str, number: int, *, bound: str = "") -> None:
        if (repo.lower(), number) in seen_threads:
            return
        seen_threads.add((repo.lower(), number))
        # The endpoint a reaction is read from differs between the two kinds, so which list a
        # comment came from is carried with it.
        rows = [(row, False) for row in gh.issue_comments(repo, number)]
        if surface in ("proposal_pr", "delivery_pr"):
            # Only a pull request has review comments; asking an issue for them is a 404.
            rows += [(row, True) for row in gh.pull_review_comments(repo, number)]
        if bound:
            # A thread the feed names is read inside the sweep's window only, so a lost ledger,
            # which forgets every seen id, cannot replay its old commands (D85).
            rows = [
                (row, review)
                for row, review in rows
                if str(row.get("updated_at") or row.get("created_at") or "") >= bound
            ]
        for comment, review in rows:
            found = commands_from(
                comment,
                surface=surface,
                number=number,
                trusted=trusted,
                ledger=ledger,
                machine=machine,
            )
            if not found:
                continue
            # Only what `ack` can answer is asked about, which keeps this to one read per
            # status comment; `commands_from` has marked it seen, so it is asked once (B441).
            # Only on this repository: `ack.yml` fires on its own `issue_comment` events, and
            # `github-actions[bot]` is a per-repository identity, so a rocket upstream is some
            # other bot's and would drop a maintainer's command in silence (B457).
            if (
                repo.lower() == self_repo.lower()
                and all(command.verb in ACK_ANSWERS for command in found)
                and answered_by_ack(gh, repo, comment, machine, review=review)
            ):
                continue
            commands.extend(found)

    if inbox_issue:
        read(self_repo, "inbox", int(inbox_issue))
    if thread and int(thread) != int(inbox_issue or 0):
        try:
            data = gh.get(f"/repos/{self_repo}/issues/{int(thread)}")
        except GitHubError as exc:
            log.warning("thread %s unreadable, left to the feed: %s", thread, exc)
            data = None
        if isinstance(data, Mapping):
            surface = "proposal_pr" if data.get("pull_request") is not None else "issue"
            read(self_repo, surface, int(thread))
    try:
        # Read threads too: the harness's own reply or pruning on a thread marks it read, which
        # would hide a command posted there just before (D85).
        notifications = list(gh.notifications(since, include_read=True))
    except GitHubError as exc:
        # The notifications endpoint needs the `notifications` scope, and a token rotated
        # without it is refused here; doctor names the gap (B305). Losing the feed costs cold
        # product-issue mentions, so the inbox is read above this line and what it found is
        # returned rather than letting the refusal out of `sweep`.
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
        read(target[0], target[1], target[2], bound=since)
    # Advanced only on a feed that came back. Advancing after a failure would skip the window
    # the failed call covered, and those mentions would never be read.
    ledger.cursors["notifications_last_seen"] = now_iso
    return commands
