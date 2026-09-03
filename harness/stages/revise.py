"""The revise stage (handoff §9): one bounded revision of a delivered branch, fully re-gated."""

from __future__ import annotations

import dataclasses
import json
import logging
from typing import Any, Literal, Mapping, Sequence

from harness import gates, redact
from harness.clone import Lease
from harness.context import Context
from harness.errors import (
    GitHubError,
    Halted,
    HarnessError,
    IllegalTransition,
    PreflightFailed,
    RateCeilingReached,
    RunnerError,
)
from harness.gates import GateResult
from harness.halt import check_halt
from harness.stages import data_block, load_prompt, run_model
from harness.stages import deliver as deliver_mod
from harness.stages import implement as implement_mod
from harness.stages.propose import parse_work_package

__all__ = [
    "FAILING_CONCLUSIONS",
    "HARNESS_AUTHOR_EMAILS",
    "TRUSTED_ASSOCIATIONS",
    "gather_ci_feedback",
    "gather_review_feedback",
    "revise",
    "tip_author_email",
]

log = logging.getLogger("harness")

ALLOWED_TOOLS = implement_mod.ALLOWED_TOOLS
DISALLOWED_TOOLS = implement_mod.DISALLOWED_TOOLS
TIMEOUT_S = 3600
MAX_FEEDBACK_CHARS = 60000

#: Check-run conclusions that count as a failing job.
FAILING_CONCLUSIONS: tuple[str, ...] = (
    "failure",
    "timed_out",
    "cancelled",
    "action_required",
    "stale",
)

#: B139: the branch tip must carry one of these author emails to be force-pushed. The second
#: is what Delivery 1's ``implement.COMMIT`` writes and cannot be changed (do-not-touch).
HARNESS_AUTHOR_EMAILS: tuple[str, ...] = ("harness@brightboost-harness", "harness@localhost")

#: B131: review feedback is fed to the model only from trusted handles with one of these.
TRUSTED_ASSOCIATIONS: frozenset[str] = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})


# --------------------------------------------------------------------------------------------
# pure feedback builders
# --------------------------------------------------------------------------------------------


def _is_trusted(comment: Mapping[str, Any], trusted: frozenset[str]) -> bool:
    user = comment.get("user") or {}
    login = str(user.get("login") or "").strip().lower() if isinstance(user, Mapping) else ""
    association = str(comment.get("author_association") or "").strip().upper()
    return bool(login) and login in trusted and association in TRUSTED_ASSOCIATIONS


def gather_review_feedback(
    reviews: Sequence[Mapping[str, Any]],
    comments: Sequence[Mapping[str, Any]],
    trusted: frozenset[str],
) -> str:
    """Review bodies and comments from trusted actors only, with their anchors (B133)."""
    chunks: list[str] = []
    for review in reviews:
        if not _is_trusted(review, trusted):
            continue
        body = str(review.get("body") or "").strip()
        if not body:
            continue
        login = str((review.get("user") or {}).get("login") or "")
        state = str(review.get("state") or "COMMENTED")
        chunks.append(f"### Review by @{login} ({state})\n{gates._tail(body)}\n")
    for comment in comments:
        if not _is_trusted(comment, trusted):
            continue
        body = str(comment.get("body") or "").strip()
        if not body:
            continue
        login = str((comment.get("user") or {}).get("login") or "")
        path = str(comment.get("path") or "")
        line = comment.get("line") or comment.get("original_line")
        anchor = f"`{path}`" + (f" line {line}" if line else "") if path else "(no file anchor)"
        hunk = str(comment.get("diff_hunk") or "").strip()
        chunk = f"### Review comment by @{login} on {anchor}\n{gates._tail(body)}\n"
        if hunk:
            chunk += f"\nDiff hunk:\n{gates._tail(hunk)}\n"
        chunks.append(chunk)
    return "\n".join(chunks)


def gather_ci_feedback(check_runs: Sequence[Mapping[str, Any]]) -> tuple[str, list[GateResult]]:
    """Failing check runs as prompt text plus gate-shaped results for the signature (B138)."""
    chunks: list[str] = []
    shaped: list[GateResult] = []
    for run in check_runs:
        if not isinstance(run, Mapping):
            continue
        conclusion = str(run.get("conclusion") or "").lower()
        if conclusion not in FAILING_CONCLUSIONS:
            continue
        name = str(run.get("name") or "(unnamed check)")
        output = run.get("output") or {}
        title = str(output.get("title") or "") if isinstance(output, Mapping) else ""
        summary = str(output.get("summary") or "") if isinstance(output, Mapping) else ""
        text = str(output.get("text") or "") if isinstance(output, Mapping) else ""
        link = str(run.get("html_url") or run.get("details_url") or "")
        chunk = f"### {name} — {conclusion}\n"
        if link:
            chunk += f"{link}\n"
        if title:
            chunk += f"{title}\n"
        if summary:
            chunk += f"\n{gates._tail(summary)}\n"
        if text:
            chunk += f"\nLog tail:\n{gates._tail(text)}\n"
        chunks.append(chunk)
        shaped.append(
            GateResult(
                name=name,
                argv=(),
                exit_code=1,
                stdout_tail=gates._tail(summary),
                stderr_tail=gates._tail(title or summary or text),
            )
        )
    return "\n".join(chunks), shaped


# --------------------------------------------------------------------------------------------
# the stage
# --------------------------------------------------------------------------------------------


def revise(
    ctx: Context,
    item_id: int,
    *,
    source: Literal["ci", "conflict", "review"],
    notes: str = "",
    lease: Lease | None = None,
) -> Lease | None:
    """One revision cycle: the lease when a branch was revised, ``None`` when it stopped first."""
    check_halt(ctx.config.halt_file)

    item = ctx.store.get_work_item(item_id)
    if item is None:
        raise HarnessError(f"no work item {item_id}")
    entry_state = str(item.state)
    nested = lease is not None
    explicit = bool(notes.strip())
    if entry_state == "shipped":
        pass
    elif entry_state == "needs-human":
        if not explicit:
            raise HarnessError(
                f"item {item_id} is needs-human; revise runs only on an explicit /harness fix "
                "from a trusted actor (B137)"
            )
    elif entry_state == "packaged" and nested:
        pass
    else:
        raise IllegalTransition(
            f"illegal transition {entry_state} -> revising for work item {item_id}: revise "
            "requires shipped (or needs-human with notes)"
        )

    cap = int(ctx.config.max_revise_cycles)
    cycles = _cycle_count(ctx, item_id)
    if cycles >= cap and not explicit:
        # B137: the cap is a stop, and a human is the only thing that restarts it.
        _to_needs_human(
            ctx,
            item_id,
            f"revise cap reached: {cycles} of {cap} cycles used on this delivery; "
            "a trusted actor's /harness fix restarts the loop",
        )
        return None

    if not nested:
        blockers = ctx.clones.preflight()
        if blockers:
            raise PreflightFailed("; ".join(blockers))
    ctx.store.transition(
        item_id, "revising", reason=f"revise ({source}) started, cycle {cycles + 1} of {cap}"
    )
    the_lease = (
        lease
        if lease is not None
        else ctx.clones.acquire(item, branch=item.branch_name, from_fork=True)
    )
    try:
        return _revise_leased(
            ctx,
            item,
            the_lease,
            source=source,
            notes=notes,
            entry_state=entry_state,
            nested=nested,
        )
    except Halted:
        # R7.7 / A10: a halt mid-stage releases the clone and leaves the item resumable.
        _back_to(ctx, item_id, entry_state, "halted mid-revise")
        ctx.clones.release(the_lease, keep=False)
        raise


def _revise_leased(
    ctx: Context,
    item: Any,
    lease: Lease,
    *,
    source: str,
    notes: str,
    entry_state: str,
    nested: bool,
) -> Lease | None:
    item_id = int(item.id)
    ctx.record_decision(
        f"revise ({source}) holds clone {lease.path} on branch {lease.branch}; "
        f"entry state {entry_state}"
    )
    if not nested:
        prep = list(implement_mod.PREPARE(lease.path))
        implement_mod._write_gates(ctx, "prepare", prep)
        ctx.record_decision(
            "prepared the re-acquired clone: "
            + (", ".join(f"{r.name} (exit {r.exit_code})" for r in prep) or "nothing to install")
        )
    check_halt(ctx.config.halt_file)

    upstream = str(ctx.config.upstream_repo)
    fork_owner = str(ctx.config.fork_repo or "").split("/")[0]
    pr = deliver_mod._find_open_pull(ctx, upstream, f"{fork_owner}:{lease.branch}")
    conflicted: list[str] = []
    if source == "conflict":
        conflicted = deliver_mod._conflicted_files(lease)
        if not conflicted:
            conflicted = deliver_mod._rebase(
                lease, upstream, deliver_mod._default_branch(ctx, upstream)
            )
        if conflicted:
            ctx.record_decision(
                f"rebase left {len(conflicted)} conflicted file(s): " + ", ".join(conflicted)
            )
        else:
            ctx.record_decision("rebase onto upstream's main was clean; no conflict to resolve")

    feedback, shaped = _feedback(ctx, item, source, lease, pr, notes, conflicted)
    feedback = redact.redact(feedback)  # §9.1: the log tail reaches the model redacted (I-13)
    signature = gates.signature(shaped)
    if not feedback.strip():
        ctx.record_decision(f"revise ({source}): no feedback to act on; no model call was made")
        if source == "conflict":
            # /harness rebase with a clean rebase still re-runs every gate (B136).
            return _gate_and_ship(ctx, item, lease, entry_state=entry_state, source=source)
        _back_to(ctx, item_id, entry_state, f"revise ({source}) found nothing to act on")
        return None
    if signature and _signature_seen(ctx, item_id, signature):
        # B138: the same failure again means the loop is going nowhere. Stop before spending.
        _to_needs_human(
            ctx,
            item_id,
            f"repeated failure signature {signature[:12]} on revise ({source}); "
            "stopping the loop before another model call (B138)",
        )
        return None
    if signature:
        _remember_signature(ctx, item_id, signature)

    spec_text = implement_mod._read_spec(ctx, item)
    pkg = parse_work_package(spec_text)
    prompt = load_prompt("revise").substitute(
        source=source,
        feedback=data_block(f"{source} feedback", feedback[:MAX_FEEDBACK_CHARS]),
        spec_text=spec_text,
    )
    result = run_model(
        ctx,
        stage="revise",
        item_id=item_id,
        prompt=prompt,
        allowed_tools=ALLOWED_TOOLS,
        disallowed_tools=DISALLOWED_TOOLS,
        timeout_s=TIMEOUT_S,
        cwd=lease.path,
        add_dirs=(lease.path,),
        entry_state=entry_state,
    )
    if not result.ok:
        implement_mod._block(
            ctx, item_id, lease, f"revise call failed: {result.error or 'unknown'}"
        )
        raise RunnerError(f"revise call failed for item {item_id}: {result.error or 'unknown'}")

    # The model's edits are the diff against the tip it was given, not against the old base:
    # after a rebase the old base also differs by everything upstream merged since.
    tip = deliver_mod._tip_sha(lease) or lease.base_sha
    check_lease = dataclasses.replace(lease, base_sha=tip)
    changed = list(implement_mod.CHANGED_PATHS(check_lease.path, check_lease.base_sha))
    ctx.record_decision(
        f"revise ({source}) changed paths: " + (", ".join(changed) if changed else "(none)")
    )
    implement_mod._reject_forbidden_diff(ctx, item_id, check_lease, changed)  # B64, one copy

    if conflicted:
        _continue_rebase(ctx, item_id, lease)
    else:
        implement_mod._format_and_commit(ctx, pkg, item, lease, changed, first=False)
    return _gate_and_ship(ctx, item, lease, entry_state=entry_state, source=source)


def _gate_and_ship(
    ctx: Context, item: Any, lease: Lease, *, entry_state: str, source: str
) -> Lease | None:
    """B136: the complete sequence, then either blocked (red) or pushed and shipped (green)."""
    item_id = int(item.id)
    check_halt(ctx.config.halt_file)
    baseline_red = _baseline_red(ctx, item_id)
    final = list(implement_mod.GATE_RUNNER(lease.path, baseline=False))
    implement_mod._write_gates(ctx, "final", final)
    new_failures = [r for r in final if r.exit_code != 0 and r.name not in baseline_red]
    if new_failures:
        names = ", ".join(f"{r.name} (exit {r.exit_code})" for r in new_failures)
        _remember_signature(ctx, item_id, gates.signature(new_failures))
        implement_mod._block(
            ctx, item_id, lease, f"gates red after revise ({source}): {names}; nothing pushed"
        )
        return None
    carried = sorted({r.name for r in final if r.exit_code != 0})
    ctx.record_decision(
        f"gate sequence has no new failures after revise ({source})"
        + (f"; pre-existing reds carried: {', '.join(carried)}" if carried else "; fully green")
        + "; no gate was widened, skipped or retimed"
    )

    # B139: force-push only under harness/, and only onto a tip the harness itself authored.
    if not lease.branch.startswith("harness/"):
        _to_needs_human(
            ctx, item_id, f"branch {lease.branch} is outside harness/; never force-pushed (B139)"
        )
        return None
    email = tip_author_email(lease)
    if email not in HARNESS_AUTHOR_EMAILS:
        _to_needs_human(
            ctx,
            item_id,
            f"branch tip authored by {email or 'an unknown author'}, not the harness; "
            "a human has pushed to this branch, so nothing was force-pushed (B139)",
        )
        return None

    if not ctx.gh.can_write:
        ctx.record_decision(
            "revise: gates pass but there is no credential; the branch stays in the clone for "
            "the host to push"
        )
        deliver_mod.deliver(ctx, item_id, lease=lease)
        _back_to(ctx, item_id, entry_state, "revised locally; branch left for the host to push")
        return lease

    fork = str(ctx.config.fork_repo)
    ctx.gh.push_branch(lease.path, lease.branch, remote_repo=fork, force=True)
    ctx.record_decision(f"force-pushed {lease.branch} to {fork} (tip authored by {email})")
    if entry_state == "packaged":
        # deliver's rebase conflicted before any pull request existed (§4.5 step 2): open it.
        url = deliver_mod.deliver(ctx, item_id, lease=lease)
        ctx.store.append_event(item_id, "info", f"revise ({source}) delivered: {url or 'no URL'}")
    else:
        ctx.store.transition(
            item_id,
            "shipped",
            reason=(
                f"revise ({source}) force-pushed {lease.branch} to {fork}; the delivery pull "
                "request now carries the revised tip"
            ),
        )
    return lease


# --------------------------------------------------------------------------------------------
# feedback by source
# --------------------------------------------------------------------------------------------


def _feedback(
    ctx: Context,
    item: Any,
    source: str,
    lease: Lease,
    pr: dict | None,
    notes: str,
    conflicted: Sequence[str],
) -> tuple[str, list[GateResult]]:
    chunks: list[str] = []
    shaped: list[GateResult] = []
    upstream = str(ctx.config.upstream_repo)

    if source == "conflict":
        for path in conflicted:
            content = deliver_mod._read(lease.path / path)
            chunks.append(f"### Conflicted file `{path}`\n{gates._tail(content)}\n")
            shaped.append(GateResult(path, (), 1, "", "merge conflict"))
    if source in ("ci", "review"):
        ci_text, ci_shaped = _ci_feedback(ctx, upstream, pr)
        if ci_text:
            chunks.append("## Failing checks on the pull request\n\n" + ci_text)
            shaped.extend(ci_shaped)
    if source == "review":
        review_text = _review_feedback(ctx, upstream, pr)
        if review_text:
            chunks.append("## Review feedback from trusted reviewers\n\n" + review_text)
            shaped.append(GateResult("review", (), 1, "", review_text.splitlines()[0]))
    if notes.strip():
        chunks.append("## Notes from the trusted /harness command\n\n" + notes.strip() + "\n")
    return "\n".join(chunks), shaped


def _ci_feedback(ctx: Context, upstream: str, pr: dict | None) -> tuple[str, list[GateResult]]:
    if pr is None:
        return "", []
    head = pr.get("head") or {}
    sha = str(head.get("sha") or "") if isinstance(head, Mapping) else ""
    if not sha:
        return "", []
    try:
        raw = ctx.gh.check_runs(upstream, sha)
    except (GitHubError, RateCeilingReached) as exc:
        ctx.record_decision(f"could not read check runs for {sha[:12]}: {exc}")
        return "", []
    runs = raw.get("check_runs") if isinstance(raw, Mapping) else raw
    return gather_ci_feedback(list(runs or []))


def _review_feedback(ctx: Context, upstream: str, pr: dict | None) -> str:
    if pr is None:
        return ""
    number = int(pr.get("number") or 0)
    if not number:
        return ""
    try:
        reviews = list(ctx.gh.pull_reviews(upstream, number))
        comments = list(ctx.gh.pull_review_comments(upstream, number))
    except (GitHubError, RateCeilingReached) as exc:
        ctx.record_decision(f"could not read reviews for #{number}: {exc}")
        return ""
    trusted = frozenset(str(h).lower() for h in ctx.trusted)
    return gather_review_feedback(reviews, comments, trusted)


# --------------------------------------------------------------------------------------------
# git
# --------------------------------------------------------------------------------------------


def tip_author_email(lease: Lease) -> str:
    """``git log -1 --format=%ae`` — who authored the branch tip (B139)."""
    code, out, _ = gates.run_command(["git", "log", "-1", "--format=%ae"], lease.path)
    return out.strip() if code == 0 else ""


def _continue_rebase(ctx: Context, item_id: int, lease: Lease) -> None:
    code, _, err = gates.run_command(["git", "add", "-A"], lease.path)
    if code != 0:
        raise HarnessError(f"git add failed in {lease.path}: {err.strip()[-2000:]}")
    code, out, err = gates.run_command(
        ["git", "-c", "core.editor=true", *deliver_mod.GIT_IDENTITY, "rebase", "--continue"],
        lease.path,
    )
    if code != 0:
        remaining = deliver_mod._conflicted_files(lease)
        reason = (
            "conflicts remain after the model's resolution: " + ", ".join(remaining)
            if remaining
            else f"git rebase --continue failed: {(err or out).strip()[-500:]}"
        )
        implement_mod._block(ctx, item_id, lease, reason)
        raise HarnessError(f"item {item_id} blocked: {reason}")
    ctx.record_decision("rebase continued to completion after the conflict resolution")


# --------------------------------------------------------------------------------------------
# bookkeeping
# --------------------------------------------------------------------------------------------


def _cycle_count(ctx: Context, item_id: int) -> int:
    """Revise cycles so far: the ledger's history and the scratch store, whichever knows more."""
    history = getattr(ctx.ledger, "history", None) or []
    from_ledger = 0
    for entry in history:
        if not isinstance(entry, Mapping) or entry.get("stage") != "revise":
            continue
        issue = entry.get("issue")
        try:
            if int(issue) == item_id:
                from_ledger += 1
        except (TypeError, ValueError):
            continue
    from_scratch = sum(
        1 for row in ctx.store.list_stage_runs(work_item_id=item_id) if row.stage == "revise"
    )
    return max(from_ledger, from_scratch)


def _signature_seen(ctx: Context, item_id: int, signature: str) -> bool:
    marker = f"revise signature {signature}"
    return any(marker in str(event.get("message") or "") for event in ctx.store.events(item_id))


def _remember_signature(ctx: Context, item_id: int, signature: str) -> None:
    if not signature:
        return
    ctx.store.append_event(item_id, "info", f"revise signature {signature}")


def _baseline_red(ctx: Context, item_id: int) -> set[str]:
    """Gates already red before the change, from the run's baseline."""
    raw = deliver_mod._read(ctx.run_dir / "gates" / "baseline.json").strip()
    if raw:
        try:
            rows = json.loads(raw)
        except ValueError:
            rows = []
        if isinstance(rows, list):
            return {
                str(r.get("name"))
                for r in rows
                if isinstance(r, Mapping) and r.get("exit_code") not in (0, None)
            }
    return set()


def _to_needs_human(ctx: Context, item_id: int, reason: str) -> None:
    """B137/B138/B139: stop, say why on the issue, and wait for a trusted human."""
    ctx.record_decision(f"item {item_id} needs a human: {reason}")
    ctx.store.append_event(item_id, "warn", f"needs-human: {reason}")
    item = ctx.store.get_work_item(item_id)
    if item is None or item.state == "needs-human":
        return
    try:
        ctx.store.transition(item_id, "needs-human", reason=reason)
    except HarnessError as exc:
        ctx.record_decision(f"could not move item {item_id} to needs-human ({exc}); blocking")
        try:
            ctx.store.transition(item_id, "blocked", reason=reason)
        except HarnessError as inner:
            ctx.record_decision(f"could not block item {item_id} either: {inner}")


def _back_to(ctx: Context, item_id: int, state: str, why: str) -> None:
    item = ctx.store.get_work_item(item_id)
    if item is None or item.state == state:
        return
    try:
        ctx.store.transition(item_id, state, reason=f"{why}; returned to {state}")
    except HarnessError as exc:
        ctx.record_decision(f"could not return item {item_id} to {state}: {exc}")
