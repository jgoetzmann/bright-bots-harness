"""The decompose stage (handoff §4.6): split one issue of this repository into bounded
sub-issues, each queued here — never on the product repository (I-14), never deeper than one
level (B111)."""

from __future__ import annotations

import logging
import re
from typing import Any

from harness.context import Context
from harness.errors import GitHubError, HarnessError, RateCeilingReached, RunnerError
from harness.halt import check_halt
from harness.redact import write_redacted
from harness.stages import data_block, load_prompt, run_model

__all__ = ["PARENT_MARKER", "decompose", "parse_subissues"]

log = logging.getLogger("harness")

ALLOWED_TOOLS = ("Read", "Glob", "Grep")
DISALLOWED_TOOLS = ("Bash", "Edit", "Write", "WebFetch", "WebSearch")
TIMEOUT_S = 600

#: A sub-issue names its parent like this in its body; finding it refuses decomposition (B111).
PARENT_MARKER = re.compile(r"(?im)^\s*parent:\s*#(\d+)\b")

_LINE = re.compile(r"^\s*(\d+)[.)]\s+(.+?)\s+(?:—|–|--|-)\s+(.+?)\s*$")


def parse_subissues(text: str) -> list[tuple[str, str]]:
    """``N. <title> — <one-paragraph body>`` lines, in order. Anything else is ignored.

    A continuation line (indented, or not starting a new number) extends the previous body.
    """
    parts: list[tuple[str, str]] = []
    for raw in (text or "").splitlines():
        match = _LINE.match(raw)
        if match is not None:
            title = match.group(2).strip().strip("*`").strip()
            body = match.group(3).strip()
            if title:
                parts.append((title, body))
            continue
        stripped = raw.strip()
        if parts and stripped and not stripped.startswith("#"):
            title, body = parts[-1]
            parts[-1] = (title, (body + " " + stripped).strip())
    return parts


def decompose(ctx: Context, issue_number: int) -> list[int]:
    """B110: N sub-issues queued here, the parent blocked with the list. Returns the child ids."""
    check_halt(ctx.config.halt_file)

    parent = ctx.store.get_work_item(int(issue_number))
    if parent is None:
        raise HarnessError(f"no work item {issue_number} in this repository's queue")

    body = _issue_body(ctx, parent)
    if str(parent.external_ref).startswith("sub:") or PARENT_MARKER.search(body):
        # B111: depth is one. A sub-issue is never decomposed, and no model call is made.
        raise HarnessError(
            f"issue #{issue_number} is a sub-issue (it names a parent); depth is one and it "
            "is not decomposed further"
        )

    limit = int(ctx.config.max_subissues)
    prompt = load_prompt("decompose").substitute(
        issue_title=parent.title,
        issue_body=data_block("issue body, verbatim", body),
        max=str(limit),
    )
    result = run_model(
        ctx,
        stage="decompose",
        item_id=parent.id,
        prompt=prompt,
        allowed_tools=ALLOWED_TOOLS,
        disallowed_tools=DISALLOWED_TOOLS,
        timeout_s=TIMEOUT_S,
        cwd=ctx.run_dir,
        entry_state=parent.state,
    )
    if not result.ok:
        raise RunnerError(f"decompose call failed for #{issue_number}: {result.error or 'unknown'}")

    parts = parse_subissues(result.text)
    if len(parts) > limit:
        ctx.record_decision(
            f"decompose returned {len(parts)} sub-issues; keeping the first {limit} "
            f"(max_subissues)"
        )
        parts = parts[:limit]
    if not parts:
        raise RunnerError(f"decompose produced no numbered sub-issues for #{issue_number}")

    children: list[int] = []
    for index, (title, text) in enumerate(parts, start=1):
        ref = f"sub:{parent.id}:{index}"
        existing = ctx.store.find_by_ref(ref)
        if existing is not None:
            children.append(int(existing.id))
            ctx.record_decision(f"sub-issue {ref} already exists as #{existing.id}; reused")
            continue
        # The store carries the paragraph as the issue body and appends the parent link (I-14).
        child = int(
            ctx.store.create_work_item(
                kind="issue", external_ref=ref, title=title, tier_required=0, body=text
            )
        )
        write_redacted(
            ctx.run_dir / "decompose" / f"{child}.md",
            f"# {title}\n\nParent: #{parent.id}\n\n{text}\n",
        )
        ctx.store.append_event(child, "info", f"Parent: #{parent.id} — created by decompose")
        children.append(child)

    listing = ", ".join(f"#{child}" for child in children)
    reason = f"decomposed into {len(children)} sub-issue(s): {listing}"
    ctx.record_decision(f"issue #{parent.id} {reason}")
    if parent.state != "blocked":
        try:
            if parent.state == "discovered":
                # discovered -> blocked is not a legal pair (D1 B11); proposing is the hop.
                ctx.store.transition(parent.id, "proposing", reason="decompose started")
            ctx.store.transition(parent.id, "blocked", reason=reason)
        except HarnessError as exc:
            ctx.record_decision(f"could not block parent #{parent.id}: {exc}")
    ctx.store.append_event(parent.id, "info", reason)
    log.info("decomposed #%s into %s", parent.id, children)
    return children


def _issue_body(ctx: Context, item: Any) -> str:
    """The issue text behind a work item, from this repository when it is one of its issues."""
    ref = str(item.external_ref)
    try:
        if ref.startswith("issue:") and item.issue_number is not None:
            data = ctx.gh.issue(item.issue_number)
        else:
            self_repo = str(getattr(ctx.config, "self_repo", "") or "")
            number = item.issue_number if ref.startswith("self:") else item.id
            if not self_repo or number is None:
                return ""
            data = ctx.gh.get(f"/repos/{self_repo}/issues/{int(number)}")
    except (GitHubError, RateCeilingReached) as exc:
        ctx.record_decision(f"decompose could not read the issue body for #{item.id}: {exc}")
        return ""
    return str(data.get("body") or "").strip() if isinstance(data, dict) else ""
