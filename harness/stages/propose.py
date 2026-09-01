"""The propose stage (one model call -> a §7.1 work package) and the work-package parser."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from harness.context import Context
from harness.errors import GitHubError, HarnessError, RateCeilingReached, RunnerError
from harness.halt import check_halt
from harness.redact import write_redacted
from harness.stages import load_prompt, run_model

__all__ = ["WorkPackage", "parse_work_package", "propose", "SECTIONS"]

log = logging.getLogger("harness")

ALLOWED_TOOLS = ("Read", "Glob", "Grep")
DISALLOWED_TOOLS = ("Bash", "Edit", "Write", "WebFetch", "WebSearch")
TIMEOUT_S = 1200

#: Heading text -> ``WorkPackage`` attribute. Keys are compared lower-cased and stripped.
SECTIONS: dict[str, str] = {
    "issue": "issue",
    "diagnosis": "diagnosis",
    "approach": "approach",
    "slices": "slices",
    "behaviors": "behaviors",
    "behaviours": "behaviors",
    "acceptance criteria": "acceptance",
    "acceptance": "acceptance",
    "decisions": "decisions",
    "open questions": "open_questions",
    "touched paths": "touched_paths",
    "risks": "risks",
}

_LIST_FIELDS = frozenset(
    {"slices", "behaviors", "acceptance", "decisions", "open_questions", "touched_paths"}
)

_HEADING = re.compile(r"^\s{0,3}##\s+(.+?)\s*#*\s*$")
_TITLE = re.compile(r"^\s{0,3}#\s+(.+?)\s*#*\s*$")
_BULLET = re.compile(r"^\s*(?:\d+[.)]|[-*+])\s+(.*)$")


@dataclass
class WorkPackage:
    """The parsed form of the §7.1 document. Sections absent from the text come back empty."""

    title: str = ""
    issue: str = ""
    diagnosis: str = ""
    approach: str = ""
    slices: list[str] = field(default_factory=list)
    behaviors: list[str] = field(default_factory=list)
    acceptance: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    touched_paths: list[str] = field(default_factory=list)
    risks: str = ""


def parse_work_package(text: str) -> WorkPackage:
    """Split a §7.1 document on its headings. The headings are the contract; prose is not."""
    pkg = WorkPackage()
    if not text:
        return pkg

    current: str | None = None
    buffers: dict[str, list[str]] = {}
    for line in text.splitlines():
        title_match = _TITLE.match(line)
        if title_match is not None and not line.lstrip().startswith("##"):
            if not pkg.title:
                pkg.title = title_match.group(1).strip()
            continue
        heading_match = _HEADING.match(line)
        if heading_match is not None:
            key = heading_match.group(1).strip().lower().rstrip(":")
            current = SECTIONS.get(key)
            if current is not None:
                buffers.setdefault(current, [])
            continue
        if current is not None:
            buffers.setdefault(current, []).append(line)

    for attr, lines in buffers.items():
        block = "\n".join(lines)
        if attr in _LIST_FIELDS:
            setattr(pkg, attr, _as_list(block))
        else:
            setattr(pkg, attr, block.strip())
    return pkg


def _as_list(block: str) -> list[str]:
    """Numbered or bulleted lines become entries. A lone ``None`` is an empty section."""
    if block.strip().lower() in ("", "none", "n/a", "none."):
        return []
    items: list[str] = []
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.lower() in ("none", "n/a", "none."):
            continue
        if stripped.startswith("```"):
            continue
        match = _BULLET.match(line)
        if match is None:
            continue
        entry = match.group(1).strip()
        if entry.startswith("`") and entry.endswith("`") and entry.count("`") == 2:
            # A path written as `scripts/x.js` is the path, not the backticks.
            entry = entry[1:-1].strip()
        if entry:
            items.append(entry)
    return items


# --------------------------------------------------------------------------------------------
# the stage
# --------------------------------------------------------------------------------------------


def propose(ctx: Context, item_id: int) -> Path:
    """B60: one model call, a spec file on disk, ``spec_path`` set, ``discovered -> proposed``."""
    check_halt(ctx.config.halt_file)

    item = ctx.store.get_work_item(item_id)
    if item is None:
        raise HarnessError(f"no work item {item_id}")

    number = item.issue_number
    body = _issue_body(ctx, number)

    prompt = load_prompt("propose").substitute(
        issue_number=number if number is not None else item.external_ref,
        issue_title=item.title,
        issue_body=body,
        repo=ctx.config.repo,
    )
    result = run_model(
        ctx,
        stage="propose",
        item_id=item_id,
        prompt=prompt,
        allowed_tools=ALLOWED_TOOLS,
        disallowed_tools=DISALLOWED_TOOLS,
        timeout_s=TIMEOUT_S,
        cwd=ctx.run_dir,
    )
    text = (result.text or "").strip()
    if not result.ok or not text:
        raise RunnerError(
            f"propose failed for item {item_id}: {result.error or 'runner returned no text'}"
        )

    spec_path = ctx.run_dir / "spec" / f"{item_id}.md"
    write_redacted(spec_path, text if text.endswith("\n") else text + "\n")
    ctx.store.update_work_item(item_id, spec_path=str(spec_path))

    pkg = parse_work_package(text)
    ctx.record_decision(
        f"propose wrote work package for item {item_id} to {spec_path} "
        f"({len(pkg.slices)} slices, {len(pkg.behaviors)} behaviors, "
        f"{len(pkg.open_questions)} open questions, {len(pkg.touched_paths)} touched paths)"
    )
    for decision in pkg.decisions:
        ctx.record_decision(f"proposal decision: {decision}")
    for question in pkg.open_questions:
        ctx.record_decision(f"proposal open question: {question}")

    ctx.store.transition(item_id, "proposed", reason="propose produced a work package")
    log.info("proposed item %s -> %s", item_id, spec_path)
    return spec_path


def _issue_body(ctx: Context, number: int | None) -> str:
    """The issue body, verbatim. A read failure is recorded, never fatal."""
    if number is None:
        return "(no issue number in the work item reference)"
    try:
        issue = ctx.gh.issue(number)
    except (GitHubError, RateCeilingReached) as exc:
        ctx.record_decision(
            f"propose could not re-read issue #{number} ({exc}); "
            f"the proposal was produced from the stored title alone"
        )
        return "(issue body unavailable: the unauthenticated read failed)"
    return str(issue.get("body") or "").strip() or "(the issue has an empty body)"
