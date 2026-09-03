"""Keyword commands and the actor gate (handoff 8, 9.2 - B131-B135, B140, B141)."""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from harness.trust import is_authorised

if TYPE_CHECKING:  # annotation only; no import-time dependency on ledger.py
    from harness.ledger import Ledger

VERBS: tuple[str, ...] = ("revise", "reject", "fix", "rebase", "stop", "split", "queue")

_COMMAND_RE = re.compile(r"^\s*/harness\s+(\w+)([^\n]*)", re.MULTILINE)
_THREAD_NUMBER_RE = re.compile(r"/(?:issues|pulls)/(\d+)/?$")
_EPOCH = "1970-01-01T00:00:00Z"


@dataclass(frozen=True)
class Command:
    verb: str
    args: str
    surface: str  # proposal_pr, delivery_pr or issue
    number: int
    comment_id: str
    actor: str


def comment_id(comment: Mapping[str, Any]) -> str:
    """The identity recorded for B135: the node ID when present, else the numeric id as text."""
    node_id = comment.get("node_id")
    if node_id:
        return str(node_id)
    return str(comment.get("id", ""))


def authorise(comment: Mapping[str, Any], trusted: frozenset[str], ledger: Ledger) -> bool:
    """The actor gate (B131, B132): login and association only; denial is a bare False."""
    user = comment.get("user") or {}
    login = str(user.get("login") or "")
    association = str(comment.get("author_association") or "")
    if is_authorised(login, association, trusted):
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


def command_from(
    comment: Mapping[str, Any],
    *,
    surface: str,
    number: int,
    trusted: frozenset[str],
    ledger: Ledger,
) -> Command | None:
    """Replay check -> actor gate -> parse -> mark seen -> Command. Order frozen (B133, B135)."""
    cid = comment_id(comment)
    if ledger.seen(cid):
        return None
    if not authorise(comment, trusted, ledger):
        return None
    parsed = parse(comment.get("body") or "")
    if parsed is None:
        return None
    ledger.mark_seen(cid)
    verb, args = parsed
    return Command(
        verb=verb,
        args=args,
        surface=surface,
        number=int(number),
        comment_id=cid,
        actor=str(comment["user"]["login"]),
    )


def thread_number(url: str) -> int | None:
    """``.../issues/12`` or ``.../pulls/12`` -> 12; anything else -> None."""
    match = _THREAD_NUMBER_RE.search(url)
    if match is None:
        return None
    return int(match.group(1))


def thread_target(
    notification: Mapping[str, Any], *, self_repo: str, upstream_repo: str
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
    if kind == "Issue" and on_self:
        return repo, "issue", number
    return None


def sweep(
    gh: Any,
    *,
    ledger: Ledger,
    trusted: frozenset[str],
    now_iso: str,
    self_repo: str,
    upstream_repo: str,
) -> list[Command]:
    """B140/B141: notifications since the cursor -> comments of each thread -> commands."""
    since = ledger.cursors.get("notifications_last_seen") or _EPOCH
    commands: list[Command] = []
    for notification in gh.notifications(since):
        target = thread_target(notification, self_repo=self_repo, upstream_repo=upstream_repo)
        if target is None:
            continue
        repo, surface, number = target
        comments = list(gh.issue_comments(repo, number))
        if surface != "issue":
            comments.extend(gh.pull_review_comments(repo, number))
        for comment in comments:
            command = command_from(
                comment, surface=surface, number=number, trusted=trusted, ledger=ledger
            )
            if command is not None:
                commands.append(command)
    ledger.cursors["notifications_last_seen"] = now_iso
    return commands
