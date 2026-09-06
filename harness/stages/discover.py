"""The discover stage: directed, triage (queue first, then the product repo), refused audit."""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Sequence

from harness import links, priority
from harness.collision import claimed_issue_numbers
from harness.context import Context
from harness.errors import HarnessError, NotImplementedInDelivery1
from harness.halt import check_halt
from harness.stages import data_block, load_prompt, read_issue_body, run_model
from harness.store.github import _label_names

__all__ = ["discover", "request", "EXCLUDED_LABELS", "machine_account"]

log = logging.getLogger("harness")

#: Labels that take an issue out of the queue regardless of anything else (B57).
EXCLUDED_LABELS = ("intern-starter", "large", "architecture")

ALLOWED_TOOLS = ("Read", "Glob", "Grep")
DISALLOWED_TOOLS = ("Bash", "Edit", "Write", "WebFetch", "WebSearch")
TIMEOUT_S = 600

#: Bodies are fetched for at most this many queued items per triage, to spare the read ceiling.
QUEUE_BODY_LIMIT = 20

_RANK_LINE = re.compile(r"^\s*#?(\d+)\s*$")


def discover(
    ctx: Context,
    *,
    mode: str,
    target: str | None,
    lens: str | None,
    ignore_allowlist: bool = False,
) -> list[int]:
    """Find work. Returns the work-item ids produced, best first."""
    check_halt(ctx.config.halt_file)

    if mode == "audit":
        # B247: an audit produces one findings issue and no work items, so it returns no ids.
        # Imported here rather than at the top: `harness.stages.audit` imports this package.
        from harness.stages.audit import audit

        audit(ctx, lens=lens or "")
        return []
    if mode == "directed":
        return _directed(ctx, target)
    if mode == "assigned":
        return _assigned(ctx)
    if mode == "triage":
        return _triage(ctx, lens, ignore_allowlist)
    raise HarnessError(f"unknown discover mode: {mode!r}")


# --------------------------------------------------------------------------------------------
# directed
# --------------------------------------------------------------------------------------------


def _directed(ctx: Context, target: str | None, *, via: str = "requested") -> list[int]:
    """B53/B54: exactly one work item, no model call, no duplicate on a re-run."""
    number = _parse_target(target)
    ref = f"issue:{number}"

    existing = ctx.store.find_by_ref(ref)
    if existing is not None:
        # B54. Returning the existing item costs no GitHub call at all.
        ctx.record_decision(
            f"directed discover of {ref} returned existing work item {existing.id} "
            f"in state {existing.state} rather than creating a duplicate"
        )
        ctx.store.append_event(existing.id, "info", f"directed discover: {ref} already known")
        return [existing.id]

    issue = ctx.gh.issue(number)
    if issue.get("pull_request") is not None:
        raise HarnessError(f"#{number} is a pull request, not an issue")

    title = str(issue.get("title") or f"issue {number}").strip() or f"issue {number}"
    item_id = ctx.store.create_work_item(
        kind="issue",
        external_ref=ref,
        title=title,
        tier_required=0,
        # B227: the issue the harness opens quotes the one it is tracking, so a reader on the
        # web can judge the work without leaving the page.
        upstream_body=str(issue.get("body") or ""),
        via=via,
    )
    ctx.store.append_event(item_id, "info", f"discovered {ref} by directed target")
    ctx.record_decision(
        f"directed discover created work item {item_id} for {ref} ({title}); "
        f"no model call was made and no ranking was needed"
    )
    log.info("discovered %s as work item %s", ref, item_id)
    return [item_id]


def _parse_target(target: str | None) -> int:
    if target is None or str(target).strip() == "":
        raise HarnessError("directed discover requires --target <issue-number>")
    text = str(target).strip().lstrip("#")
    if not text.isdigit():
        raise HarnessError(f"--target must be an issue number, got {target!r}")
    return int(text)


# --------------------------------------------------------------------------------------------
# triage — the queue in this repository first
# --------------------------------------------------------------------------------------------


def machine_account(config: Any) -> str:
    """The login the harness runs as: the owner of the fork it pushes to (B233).

    Derived rather than configured, because a fork the machine account does not own is not a
    fork it can push to — `FORK_REPO` already names the account, and a second key naming it
    again could only ever disagree.
    """
    fork = str(getattr(config, "fork_repo", "") or "").strip()
    return fork.split("/")[0] if "/" in fork else ""


def _assigned(ctx: Context) -> list[int]:
    """B233: every open product-repository issue assigned to the machine account.

    Assignment is a maintainer saying "this one is yours", on the ticket itself, in the place
    they already work. It is the allowlist label's job without the label — and unlike the
    label, it is a gesture GitHub already has a verb for. No model call: which issues are
    assigned is a fact, not a judgement.
    """
    account = machine_account(ctx.config)
    if not account:
        raise HarnessError(
            "no machine account to look for: FORK_REPO is empty, so there is no login to be "
            "assigned to. Set FORK_REPO, or use --mode directed --target <n>."
        )

    issues = ctx.gh.issues_assigned_to(account)
    ctx.record_decision(
        f"assigned discover: {len(issues)} open issue(s) in {ctx.config.repo} are assigned to "
        f"@{account}"
    )

    created: list[int] = []
    skipped: list[int] = []
    for issue in issues:
        number = _issue_number(issue)
        if number is None:
            continue
        ref = f"issue:{number}"
        if ctx.store.find_by_ref(ref) is not None:
            skipped.append(number)
            continue
        title = str(issue.get("title") or f"issue {number}").strip() or f"issue {number}"
        item_id = ctx.store.create_work_item(
            kind="issue",
            external_ref=ref,
            title=title,
            tier_required=0,
            upstream_body=str(issue.get("body") or ""),
        )
        ctx.store.append_event(item_id, "info", f"queued {ref}: assigned to @{account}")
        created.append(item_id)
        log.info("assigned %s queued as work item %s", ref, item_id)

    if skipped:
        ctx.record_decision(
            f"assigned discover: {sorted(skipped)} already had work items and were left alone"
        )
    if not created:
        ctx.record_decision(
            "assigned discover created nothing: no issue assigned to @"
            f"{account} is new. Assign one on {ctx.config.repo} and it will be picked up."
        )
    return created


def _triage(ctx: Context, lens: str | None, ignore_allowlist: bool) -> list[int]:
    queued = ctx.store.list_work_items(state="discovered")
    if queued:
        return _triage_queue(ctx, queued)
    return _triage_product_repo(ctx, lens, ignore_allowlist)


def _triage_queue(ctx: Context, queued: Sequence[Any]) -> list[int]:
    """Rank the items already queued here. Reads nothing from the product repository and
    creates nothing: the ids returned are the ids that went in, best first."""
    ctx.record_decision(
        f"triage: {len(queued)} work item(s) already discovered in the queue; ranking those "
        "and reading no product-repository issue list"
    )
    prompt = load_prompt("discover_triage").substitute(
        candidates=data_block("queued work items", _render_queue(ctx, queued))
    )
    result = run_model(
        ctx,
        stage="discover",
        item_id=None,
        prompt=prompt,
        allowed_tools=ALLOWED_TOOLS,
        disallowed_tools=DISALLOWED_TOOLS,
        timeout_s=TIMEOUT_S,
        cwd=ctx.run_dir,
    )
    if not result.ok:
        raise HarnessError(f"triage ranking failed: {result.error or 'runner reported failure'}")

    known = {int(item.id) for item in queued}
    ranked = [n for n in _parse_ranking(result.text) if n in known]
    if not ranked:
        ctx.record_decision(
            "triage: the ranking call returned no usable work-item number; "
            "falling back to the queue in store order"
        )
        ranked = [int(item.id) for item in queued]
    for item_id in ranked:
        ctx.store.append_event(item_id, "info", "ranked by triage")
    ctx.record_decision(f"triage ranked queued work items {ranked}")
    return ranked


def _render_queue(ctx: Context, queued: Sequence[Any]) -> str:
    lines: list[str] = []
    for index, item in enumerate(queued):
        lines.append(f"#{item.id} — {item.title} [{item.external_ref}]")
        body = read_issue_body(ctx, item) if index < QUEUE_BODY_LIMIT else ""
        if body:
            lines.append(f"    {' '.join(body.split())[:400]}")
    return "\n".join(lines)


# --------------------------------------------------------------------------------------------
# triage — Delivery 1, the product repository, unchanged
# --------------------------------------------------------------------------------------------


def _triage_product_repo(ctx: Context, lens: str | None, ignore_allowlist: bool) -> list[int]:
    # B257/B290: looking at the product repository for work is *suggesting*, and a suggestion
    # runs only when nothing anybody asked for is outstanding and the week has headroom left.
    # Checked before the GitHub reads as well as before the model call, because the refusal is
    # the same either way and the reads are not free.
    refused = priority.admit(
        "suggested", store=ctx.store, ledger=ctx.ledger, config=ctx.config
    )
    if refused is not None:
        ctx.record_decision(f"triage suggested nothing: {refused}")
        log.info("triage suggested nothing: %s", refused)
        return []

    issues = ctx.gh.issues(state="open")
    pulls = ctx.gh.pulls()
    branches = ctx.gh.branches()

    pr_titles = [str(p.get("title") or "") for p in pulls]
    claimed = claimed_issue_numbers(branches, pr_titles)
    ctx.record_decision(
        f"triage: {len(issues)} open issues, {len(pulls)} open pull requests, "
        f"{len(branches)} branches; claimed issue numbers = {sorted(claimed)}"
    )

    survivors: list[dict] = []
    for issue in issues:
        if issue.get("pull_request") is not None:
            continue
        number = _issue_number(issue)
        if number is None:
            continue
        reason = _rejection_reason(
            issue,
            number=number,
            claimed=claimed,
            allowlist_label=ctx.config.allowlist_label,
            ignore_allowlist=ignore_allowlist,
            machine=machine_account(ctx.config),
        )
        if reason is not None:
            ctx.store.append_event(None, "debug", f"triage excluded #{number}: {reason}")
            continue
        survivors.append(issue)

    if not survivors:
        ctx.record_decision("triage: no candidate survived filtering; no model call was made")
        return []

    ctx.record_decision(
        "triage candidates after filtering: "
        + ", ".join(f"#{_issue_number(i)}" for i in survivors)
    )

    prompt = load_prompt("discover_triage").substitute(
        candidates=data_block("candidate issues", _render_candidates(survivors))
    )
    result = run_model(
        ctx,
        stage="discover",
        item_id=None,
        prompt=prompt,
        allowed_tools=ALLOWED_TOOLS,
        disallowed_tools=DISALLOWED_TOOLS,
        timeout_s=TIMEOUT_S,
        cwd=ctx.run_dir,
    )
    if not result.ok:
        raise HarnessError(f"triage ranking failed: {result.error or 'runner reported failure'}")

    survivor_numbers = {_issue_number(i) for i in survivors}
    ranked = [n for n in _parse_ranking(result.text) if n in survivor_numbers]
    if not ranked:
        ctx.record_decision(
            "triage: the ranking call returned no usable issue number; "
            "falling back to the filtered candidates in GitHub's own order"
        )
        ranked = [n for n in (_issue_number(i) for i in survivors) if n is not None]

    by_number = {_issue_number(i): i for i in survivors}
    item_ids: list[int] = []
    # B258: at most this many per run. Five proposals a person has to read is a week's worth of
    # goodwill; fifty is a reason to turn the harness off.
    cap = int(getattr(ctx.config, "suggest_max_per_run", 0) or 0)
    for number in ranked:
        issue = by_number.get(number)
        if issue is None:
            continue
        if cap and len(item_ids) >= cap:
            ctx.record_decision(
                f"triage stopped at SUGGEST_MAX_PER_RUN={cap}; #{number} and the rest of the "
                f"ranking were not queued and will be reconsidered next run"
            )
            break
        item_ids.append(
            _ensure_item(
                ctx, number, str(issue.get("title") or f"issue {number}"), via="suggested"
            )
        )

    ctx.record_decision(f"triage produced work items {item_ids} for issues {ranked[:len(item_ids)]}")
    return item_ids


def _rejection_reason(
    issue: dict,
    *,
    number: int,
    claimed: set[int],
    allowlist_label: str,
    ignore_allowlist: bool,
    machine: str = "",
) -> str | None:
    """Return why this issue is not a candidate, or ``None`` when it survives."""
    mine = _assigned_to(issue, machine)
    if _is_assigned(issue) and not mine:
        return "assigned"  # B55: to somebody else, so it is somebody else's work
    if number in claimed:
        return "claimed by an in-flight branch or pull request title"  # B56
    labels = _label_names(issue)
    hit = [name for name in labels if name in EXCLUDED_LABELS]
    if hit:
        return f"excluded label {hit[0]}"  # B57
    if mine:
        # B233: assignment is the statement the allowlist label makes, made on the ticket
        # itself. Requiring both would mean a maintainer had to say the same thing twice.
        return None
    if not ignore_allowlist and allowlist_label not in labels:
        return f"missing allowlist label {allowlist_label}"  # B58
    return None


def _assigned_to(issue: dict, login: str) -> bool:
    """Is this issue assigned to `login`? GitHub logins are case-insensitive."""
    handle = str(login or "").strip().lstrip("@").lower()
    if not handle:
        return False
    holders = [h for h in (issue.get("assignees") or []) if h]
    single = issue.get("assignee")
    if single:
        holders.append(single)
    return any(str((h or {}).get("login") or "").lower() == handle for h in holders)


def _is_assigned(issue: dict) -> bool:
    if issue.get("assignee"):
        return True
    assignees = issue.get("assignees")
    return bool(assignees)


def _issue_number(issue: dict) -> int | None:
    raw = issue.get("number")
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else None


def _render_candidates(issues: Sequence[dict]) -> str:
    lines: list[str] = []
    for issue in issues:
        number = _issue_number(issue)
        title = str(issue.get("title") or "").strip()
        labels = sorted(_label_names(issue))
        suffix = f" [{', '.join(labels)}]" if labels else ""
        lines.append(f"#{number} — {title}{suffix}")
        body = str(issue.get("body") or "").strip()
        if body:
            excerpt = " ".join(body.split())[:400]
            lines.append(f"    {excerpt}")
    return "\n".join(lines)


def _parse_ranking(text: str) -> list[int]:
    """Issue numbers, one per line, best first. Duplicates collapse to the first mention."""
    seen: set[int] = set()
    ordered: list[int] = []
    for line in (text or "").splitlines():
        match = _RANK_LINE.match(line)
        if match is None:
            continue
        number = int(match.group(1))
        if number in seen:
            continue
        seen.add(number)
        ordered.append(number)
    return ordered


def _ensure_item(ctx: Context, number: int, title: str, *, via: str = "requested") -> int:
    ref = f"issue:{number}"
    existing = ctx.store.find_by_ref(ref)
    if existing is not None:
        return existing.id
    item_id = ctx.store.create_work_item(
        kind="issue",
        external_ref=ref,
        title=title.strip() or f"issue {number}",
        tier_required=0,
        via=via,
    )
    ctx.store.append_event(item_id, "info", f"discovered {ref} by triage ranking")
    return item_id


# --------------------------------------------------------------------------------------------
# the green light — B259-B263
# --------------------------------------------------------------------------------------------

#: The phrase that makes the comment findable again. `sweep` must never treat its own comment as
#: a new request, and a person must be able to see at a glance that the harness already asked.
GREEN_LIGHT_MARKER = "<!-- bright-bots-harness: green-light-request -->"


def ask_for_green_light(
    ctx: Context, *, item_id: int, issue_number: int, proposal_url: str
) -> bool:
    """Comment on the product issue asking to be let start. True when one was posted (B259).

    This is the one outward write the harness makes without being asked, and it is what makes it
    discoverable to the people who own the work. It is also the thing that has to be honest: a
    proposal is not a change, and an offer is not a claim.
    """
    if not getattr(ctx.config, "comment_upstream", True):
        # B263: the switch silences the product repository and changes nothing else -- the item
        # is still queued, the proposal still exists, delivery still works.
        ctx.record_decision(
            f"no green-light comment on #{issue_number}: COMMENT_UPSTREAM is false"
        )
        return False
    if not ctx.gh.can_write:
        return False

    issue = ctx.gh.issue(int(issue_number))
    if _is_assigned(issue):
        # B261: somebody is already on it. Offering to do their work is not discoverability.
        ctx.record_decision(
            f"no green-light comment on #{issue_number}: it has an assignee"
        )
        return False
    for comment in ctx.gh.issue_comments(ctx.config.upstream_repo, int(issue_number)):
        if GREEN_LIGHT_MARKER in str(comment.get("body") or ""):
            # B260: once per issue, ever. A weekly job that re-asks every week is a weekly job
            # that gets muted, and then nothing it says is read.
            ctx.record_decision(
                f"no green-light comment on #{issue_number}: one was already posted"
            )
            return False

    opening = "I have worked out how I would implement this"
    opening += f" — the plan is at {proposal_url}." if proposal_url else "."
    body = "\n".join(
        [
            GREEN_LIGHT_MARKER,
            opening,
            "",
            "**I have not started, and I will not without a green light.** Assign me, or reply "
            "`/harness go` on this issue.",
            "",
            "Nobody asked for this one — I picked it up while the queue was empty, so ignoring "
            "this comment is a complete and expected answer.",
            "",
            links.signature(ctx.config, trusted=ctx.trusted),
        ]
    )
    ctx.gh.comment(ctx.config.upstream_repo, int(issue_number), body)
    ctx.store.append_event(
        item_id, "info", f"asked for a green light on {ctx.config.upstream_repo}#{issue_number}"
    )
    return True


# --------------------------------------------------------------------------------------------
# request — `/harness work <text or link>` (B235, B238, B239, B244)
# --------------------------------------------------------------------------------------------

#: A GitHub issue link, in any of the forms a person actually pastes.
_ISSUE_URL_RE = re.compile(r"github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/issues/(\d+)")

#: A bare number, or `#633`. Only when it is the whole of the text: `fix 3 of the cards` is a
#: sentence, not a pointer, and reading it as issue 3 would be a confident wrong answer.
_BARE_NUMBER_RE = re.compile(r"^#?(\d+)$")

#: The title of a free-text request is its first line, cut here so the issue list stays readable.
TITLE_LIMIT = 72


def request(
    ctx: Context, *, text: str, actor: str = "", via: str = "requested"
) -> tuple[int | None, str]:
    """One work item from a `/harness work` command. Returns ``(item_id, message)``.

    The same route serves all three ways of asking: a comment on the inbox issue (B235), a
    comment on a product issue (B244), and a link pasted into either (B238). They differ only
    in where the text came from, so they share the creation path rather than each growing one.

    ``item_id`` is None when nothing was created; the message then says what was missing, and
    is meant to be read by the person who asked.
    """
    body = str(text or "").strip()
    if not body:
        # B239: an empty `/harness work` is a person who meant something. Guessing which issue
        # they meant is worse than asking.
        return None, (
            "say what to work on: `/harness work <what you want done>`, or paste a link to an "
            "issue on the product repository."
        )

    pointer = _resolve_pointer(ctx, body)
    if pointer is not None:
        ids = _directed(ctx, str(pointer), via=via)
        return ids[0], f"work item #{ids[0]} tracks issue:{pointer}"

    title = body.splitlines()[0].strip()
    if len(title) > TITLE_LIMIT:
        title = title[: TITLE_LIMIT - 1].rstrip() + "\u2026"
    # The reference is the request itself. There is no upstream issue to point at, and inventing
    # one would make the item look like it tracks something it does not.
    ref = f"request:{_request_slug(ctx, actor, body)}"
    existing = ctx.store.find_by_ref(ref)
    if existing is not None:
        return int(existing.id), f"work item #{existing.id} already covers that request"

    item_id = ctx.store.create_work_item(
        kind="issue",
        external_ref=ref,
        title=title,
        tier_required=0,
        # Quoted as data, not as instructions: the requester is trusted to ask for work, which
        # is not the same as being trusted to write the harness's own prompts (B277).
        upstream_body=body,
        via=via,
    )
    who = f" at the request of @{actor}" if actor else ""
    ctx.store.append_event(item_id, "info", f"requested{who}: {title}")
    ctx.record_decision(
        f"request created work item {item_id} ({title}) from free text{who}; no model call "
        f"was made, because what to work on was stated rather than inferred"
    )
    return item_id, f"work item #{item_id} created"


def _resolve_pointer(ctx: Context, body: str) -> int | None:
    """The product-repository issue number `body` names, or None if it names no issue.

    Refuses a link to the harness's own repository (I-18/B280) rather than tracking it, and
    refuses a link to a third repository rather than silently reading the number out of it and
    applying it to the product repo -- which would be the number trap with an extra step.
    """
    match = _ISSUE_URL_RE.search(body)
    if match is not None:
        repo, number = match.group(1), int(match.group(2))
        self_repo = str(getattr(ctx.config, "self_repo", "") or "")
        upstream = str(getattr(ctx.config, "upstream_repo", "") or "")
        if self_repo and repo.lower() == self_repo.lower():
            raise HarnessError(
                f"{repo}#{number} is in the harness's own repository. The harness does not "
                "work on itself (I-18) -- changes to the harness are made by a person."
            )
        if upstream and repo.lower() != upstream.lower():
            raise HarnessError(
                f"{repo}#{number} is not in {upstream}. The harness only works on the product "
                "repository it is configured for."
            )
        return number
    bare = _BARE_NUMBER_RE.match(body)
    return int(bare.group(1)) if bare else None


def _request_slug(ctx: Context, actor: str, body: str) -> str:
    """A short stable reference for a free-text request.

    Content-derived, so the same request asked twice is the same item rather than two, and so
    it survives the item being reopened. Short because it is read by people in an issue body.
    """
    digest = hashlib.sha256(f"{actor.lower()}\n{body}".encode("utf-8")).hexdigest()
    return digest[:12]
