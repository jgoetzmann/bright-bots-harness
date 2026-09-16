"""The ask stage: one question, one read, one answer, no state (B274).

`ask` is the only route that uses the allowance and creates no work item, so asking a question
whose answer is already known is a cheap check of what the harness understands.
"""

from __future__ import annotations

import logging
from typing import Any

from harness.clock import iso
from harness.context import Context
from harness.errors import HarnessError
from harness.halt import check_halt
from harness.stages import data_block, load_prompt, run_model

__all__ = ["ask", "ALLOWED_TOOLS", "DISALLOWED_TOOLS", "QUESTION_LIMIT", "TIMEOUT_S"]

log = logging.getLogger("harness")

#: Read-only, the same tools as `propose`.
ALLOWED_TOOLS = ("Read", "Glob", "Grep")
DISALLOWED_TOOLS = ("Bash", "Edit", "Write", "WebFetch", "WebSearch")
TIMEOUT_S = 300

#: A longer question is cut to this many characters rather than refused, and the reply says so.
QUESTION_LIMIT = 2000

#: Where the day's tally lives: one count per UTC calendar day, shared by every asker.
COUNTER = "ask_calls"


def ask(ctx: Context, *, question: str, actor: str = "") -> str:
    """Answer one question about the product repository. Returns the reply, as markdown.

    Nothing else happens: no work item, no transition, no branch, no pull request. The call is
    authorised like any other and bounded by the ask turn cap and `ASK_MAX_PER_DAY` (B288).
    """
    ctx.check_halt()
    text = str(question or "").strip()
    if not text:
        return "ask what? `/harness ask <question>` — anything about the product repository."

    truncated = len(text) > QUESTION_LIMIT
    if truncated:
        text = text[:QUESTION_LIMIT].rstrip()

    today = iso(ctx.clock.now())[:10]
    used = _used_today(ctx, today)
    cap = int(getattr(ctx.config, "ask_max_per_day", 0) or 0)
    if cap and used >= cap:
        # The daily cap is checked before the model call (B276).
        ctx.record_decision(
            f"ask by @{actor or 'someone'} refused: {used} of {cap} answers already given "
            f"on {today}; no model call was made"
        )
        return (
            f"that is {used} questions today, and the daily limit is {cap}. The limit resets at "
            "midnight UTC. If this one matters more than the ones already asked, say so and it "
            "can be raised."
        )

    lease = ctx.clones.acquire(_READER, run_id="ask", read_only=True)
    try:
        prompt = load_prompt("ask").substitute(
            actor=actor or "someone",
            repo=str(getattr(ctx.config, "upstream_repo", "") or ""),
            base_sha=lease.base_sha or "unknown",
            # Fenced as data: being trusted to ask is not being trusted to write the prompt,
            # and on a public repository anyone can ask (B277).
            question=data_block("the question", text),
        )
        result = run_model(
            ctx,
            stage="ask",
            item_id=None,
            prompt=prompt,
            allowed_tools=ALLOWED_TOOLS,
            disallowed_tools=DISALLOWED_TOOLS,
            timeout_s=TIMEOUT_S,
            cwd=lease.path,
        )
    finally:
        ctx.clones.release(lease, keep=False)

    _count(ctx, today)
    if not result.ok:
        raise HarnessError(f"ask failed: {result.error or 'no answer'}")

    answer = str(result.text or "").strip()
    if not answer:
        raise HarnessError("ask produced no answer")
    ctx.record_decision(
        f"ask by @{actor or 'someone'} answered from {lease.base_sha} without creating or "
        f"changing anything; {used + 1} of {cap or 'unlimited'} for {today}"
    )
    return _framed(answer, base_sha=lease.base_sha or "", truncated=truncated)


def _framed(answer: str, *, base_sha: str, truncated: bool) -> str:
    """The answer, then the caveat that it is a reading of one commit (B278).

    Appended in code rather than asked for in the prompt, so the model cannot omit it.
    """
    lines = [answer, ""]
    if truncated:
        lines.append(
            f"*Only the first {QUESTION_LIMIT} characters of the question were read.*"
        )
        lines.append("")
    where = f"`{base_sha[:12]}`" if base_sha else "the current main"
    lines.append(
        f"*This is a **reading** of {where}, not a decision and not a plan. Nothing was "
        "changed, and no work item was created. To turn it into work, say `/harness work "
        "<what you want done>`.*"
    )
    return "\n".join(lines)


def _used_today(ctx: Context, today: str) -> int:
    tally = ctx.ledger.cursors.get(COUNTER)
    if not isinstance(tally, dict) or tally.get("date") != today:
        return 0
    return int(tally.get("count", 0) or 0)


def _count(ctx: Context, today: str) -> None:
    """Counted after the call, including a failed one, which still used the allowance."""
    ctx.ledger.cursors[COUNTER] = {"date": today, "count": _used_today(ctx, today) + 1}


class _Reader:
    """The stand-in `clones.acquire` needs; an ask belongs to no work item."""

    id: Any = "ask"
    branch_name = None
    base_sha = None


_READER = _Reader()
