"""The propose stage: one model call -> a §7.1 work package -> a bounded proposal file (§4.3)."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from harness import errors, redact
from harness.clone import _slugify
from harness.context import Context
from harness.errors import (
    GitHubError,
    HarnessError,
    IllegalTransition,
    RateCeilingReached,
    RunnerError,
)
from harness.halt import check_halt
from harness.redact import write_redacted
from harness.stages import data_block, load_prompt, run_model

__all__ = [
    "GATE_EXPECTATIONS",
    "GATE_NAMES",
    "KINDS",
    "PROPOSAL_KEYS",
    "RISKS",
    "SECTIONS",
    "WorkPackage",
    "build_front_matter",
    "extract_proposal_block",
    "parse_work_package",
    "propose",
    "render_front_matter",
    "validate_proposal",
]

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

# --- the §4.3 schema -------------------------------------------------------------------------

#: Every key, in the order the front matter is rendered. Every one is required; nothing else is
#: accepted (B103).
PROPOSAL_KEYS: tuple[str, ...] = (
    "issue",
    "upstream_issue",
    "title",
    "kind",
    "slices",
    "risk",
    "touched_paths",
    "depends_on",
    "estimated_turns",
    "gate_expectation",
    "baseline_red",
)
KINDS: tuple[str, ...] = ("fix", "chore", "test", "docs")
RISKS: tuple[str, ...] = ("low", "medium", "high")
GATE_EXPECTATIONS: tuple[str, ...] = ("green", "known-red")
#: The seven gate names of ``gates._SEQUENCE``; ``baseline_red`` may name nothing else.
GATE_NAMES: tuple[str, ...] = (
    "npx prisma generate",
    "npm run lint",
    "npm run typecheck",
    "backend: npm run typecheck",
    "bash scripts/check-prisma-drift.sh",
    "npm run test:unit",
    "npm run build",
)
MAX_TITLE_CHARS = 120
MAX_SLICES = 5
MAX_TOUCHED_PATHS = 40
DEFAULT_ESTIMATED_TURNS = 40
TERMINAL_STATES: tuple[str, ...] = ("merged", "abandoned")

_BLOCK = re.compile(r"<!--\s*proposal:\s*(\{.*?\})\s*-->", re.DOTALL)
_TITLE_TYPE = re.compile(r"^\s*([a-z]+)(?:\([^)]*\))?!?:")
_ISSUE_REF = re.compile(r"\bissue:(\d+)\b")


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
# the proposal block and its schema
# --------------------------------------------------------------------------------------------


def extract_proposal_block(text: str) -> tuple[dict | None, str, list[str]]:
    """``(parsed, text_without_block, errors)`` from the model's ``<!-- proposal: {...} -->``."""
    match = _BLOCK.search(text or "")
    if match is None:
        return None, text or "", []
    stripped = (text[: match.start()] + text[match.end() :]).strip("\n") + "\n"
    try:
        parsed = json.loads(match.group(1))
    except ValueError as exc:
        return None, stripped, [f"proposal block is not valid JSON: {exc}"]
    if not isinstance(parsed, dict):
        return None, stripped, ["proposal block must be a JSON object"]
    return parsed, stripped, []


def _kind_from_title(title: str) -> str:
    match = _TITLE_TYPE.match(title or "")
    if match is None:
        return "fix"
    type_ = match.group(1)
    return type_ if type_ in KINDS else "chore"


def build_front_matter(
    pkg: WorkPackage,
    *,
    item_id: int,
    upstream_issue: int | None,
    block: Mapping[str, Any] | None,
    max_turns: int,
) -> dict[str, Any]:
    """The §4.3 mapping: defaults from the work package, overridden by the model's block."""
    front: dict[str, Any] = {
        "issue": item_id,
        "upstream_issue": upstream_issue,
        "title": (pkg.title or "").strip(),
        "kind": _kind_from_title(pkg.title),
        "slices": max(1, len(pkg.slices)),
        "risk": "medium",
        "touched_paths": list(pkg.touched_paths),
        "depends_on": [],
        "estimated_turns": max(1, min(DEFAULT_ESTIMATED_TURNS, int(max_turns))),
        "gate_expectation": "green",
        "baseline_red": [],
    }
    for key, value in (block or {}).items():
        front[str(key)] = value
    front["issue"] = item_id
    if front.get("upstream_issue") is None:
        front["upstream_issue"] = upstream_issue
    return front


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_proposal(
    front: Mapping[str, Any],
    *,
    path_exists: Callable[[str], bool],
    max_turns: int,
    open_issue: Callable[[int], bool],
) -> list[str]:
    """Every §4.3 rule, as a list of human-readable errors. Empty means valid (B103/B104)."""
    problems: list[str] = []
    keys = set(front.keys())
    for key in PROPOSAL_KEYS:
        if key not in keys:
            problems.append(f"missing required key: {key}")
    for key in sorted(keys - set(PROPOSAL_KEYS)):
        problems.append(f"unknown key: {key}")

    issue = front.get("issue")
    if "issue" in keys:
        if not _is_int(issue) or issue <= 0:
            problems.append("issue must be a positive integer")
        elif not open_issue(issue):
            problems.append(f"issue {issue} is not an open work item in this repository")

    upstream = front.get("upstream_issue")
    if "upstream_issue" in keys and upstream is not None:
        if not _is_int(upstream) or upstream <= 0:
            problems.append("upstream_issue must be a positive integer or null")

    title = front.get("title")
    if "title" in keys:
        if not isinstance(title, str) or not title.strip():
            problems.append("title must be a non-empty string")
        elif len(title.strip()) > MAX_TITLE_CHARS:
            problems.append(f"title must be at most {MAX_TITLE_CHARS} characters")

    kind = front.get("kind")
    if "kind" in keys and kind not in KINDS:
        problems.append(f"kind must be one of {', '.join(KINDS)}; got {kind!r}")

    slices = front.get("slices")
    if "slices" in keys and (not _is_int(slices) or not 1 <= slices <= MAX_SLICES):
        problems.append(f"slices must be an integer from 1 to {MAX_SLICES}; got {slices!r}")

    risk = front.get("risk")
    if "risk" in keys and risk not in RISKS:
        problems.append(f"risk must be one of {', '.join(RISKS)}; got {risk!r}")

    touched = front.get("touched_paths")
    if "touched_paths" in keys:
        if not isinstance(touched, list) or not touched:
            problems.append("touched_paths must be a non-empty list of repository paths")
        elif len(touched) > MAX_TOUCHED_PATHS:
            problems.append(f"touched_paths must list at most {MAX_TOUCHED_PATHS} paths")
        else:
            for index, path in enumerate(touched):
                if not isinstance(path, str) or not path.strip():
                    problems.append(f"touched_paths[{index}] must be a non-empty string")
                    continue
                if not path_exists(path.strip()):
                    problems.append(
                        f"touched_paths[{index}] does not exist in the product repository "
                        f"at the base commit: {path.strip()}"
                    )

    depends = front.get("depends_on")
    if "depends_on" in keys:
        if not isinstance(depends, list):
            problems.append("depends_on must be a list of issue numbers")
        else:
            for index, number in enumerate(depends):
                if not _is_int(number) or number <= 0:
                    problems.append(f"depends_on[{index}] must be a positive integer")

    turns = front.get("estimated_turns")
    if "estimated_turns" in keys and (not _is_int(turns) or not 1 <= turns <= int(max_turns)):
        problems.append(
            f"estimated_turns must be an integer from 1 to {int(max_turns)}; got {turns!r}"
        )

    expectation = front.get("gate_expectation")
    baseline_red = front.get("baseline_red")
    if "gate_expectation" in keys and expectation not in GATE_EXPECTATIONS:
        problems.append(
            f"gate_expectation must be one of {', '.join(GATE_EXPECTATIONS)}; got {expectation!r}"
        )
    if "baseline_red" in keys:
        if not isinstance(baseline_red, list):
            problems.append("baseline_red must be a list of gate names")
        else:
            for index, name in enumerate(baseline_red):
                if not isinstance(name, str) or name not in GATE_NAMES:
                    problems.append(
                        f"baseline_red[{index}] is not a gate name: {name!r} "
                        f"(known: {', '.join(GATE_NAMES)})"
                    )
            if expectation == "known-red" and not baseline_red:
                problems.append("gate_expectation known-red requires a non-empty baseline_red")
    return problems


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if _is_int(value):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def _yaml_list(key: str, values: Any) -> list[str]:
    if not isinstance(values, list) or not values:
        return [f"{key}: []"]
    lines = [f"{key}:"]
    for value in values:
        lines.append(f"  - {_yaml_scalar(value)}")
    return lines


def render_front_matter(front: Mapping[str, Any]) -> str:
    """The §4.3 block, keys in schema order, quoted so that it is valid YAML and valid JSON-ish."""
    lines = ["---"]
    for key in PROPOSAL_KEYS:
        value = front.get(key)
        if key in ("touched_paths", "depends_on", "baseline_red"):
            lines.extend(_yaml_list(key, value))
        elif key == "kind" or key == "risk" or key == "gate_expectation":
            lines.append(f"{key}: {value}")
        else:
            lines.append(f"{key}: {_yaml_scalar(value)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------------------------
# the stage
# --------------------------------------------------------------------------------------------


def propose(ctx: Context, item_id: int, *, notes: str = "") -> Path:
    """B60 + §4.3: one model call, a spec file on disk, a validated proposal published."""
    check_halt(ctx.config.halt_file)

    item = ctx.store.get_work_item(item_id)
    if item is None:
        raise HarnessError(f"no work item {item_id}")

    entry_state = _enter(ctx, item, notes)
    body, upstream_from_body = _issue_body(ctx, item)
    upstream_issue = (
        item.issue_number if item.external_ref.startswith("issue:") else upstream_from_body
    )
    max_turns = int(ctx.config.max_turns["implement"])

    problems: list[str] = []
    text = ""
    for attempt in (1, 2):
        prompt = _render_prompt(ctx, item, body, notes=notes, previous_errors=problems)
        result = run_model(
            ctx,
            stage="propose",
            item_id=item_id,
            prompt=prompt,
            allowed_tools=ALLOWED_TOOLS,
            disallowed_tools=DISALLOWED_TOOLS,
            timeout_s=TIMEOUT_S,
            cwd=ctx.run_dir,
            entry_state=entry_state,
        )
        text = (result.text or "").strip()
        if not result.ok or not text:
            _revert(ctx, item_id, entry_state, "propose call failed")
            raise RunnerError(
                f"propose failed for item {item_id}: {result.error or 'runner returned no text'}"
            )
        block, body_text, problems = extract_proposal_block(text)
        pkg = parse_work_package(body_text)
        front = build_front_matter(
            pkg,
            item_id=item_id,
            upstream_issue=upstream_issue,
            block=block,
            max_turns=max_turns,
        )
        problems = problems + validate_proposal(
            front,
            path_exists=_path_checker(ctx, item),
            max_turns=max_turns,
            open_issue=_open_issue_checker(ctx, item_id),
        )
        if not problems:
            break
        ctx.record_decision(
            f"proposal front matter invalid on attempt {attempt}: " + "; ".join(problems)
        )
        ctx.store.append_event(
            item_id, "warn", f"proposal invalid (attempt {attempt}): " + "; ".join(problems)
        )
    if problems:
        # B103: never published. One retry was made with the errors appended; still invalid.
        reason = "proposal front matter invalid after one retry: " + "; ".join(problems)
        ctx.record_decision(f"item {item_id} blocked: {reason}")
        ctx.store.append_event(item_id, "error", reason)
        try:
            ctx.store.transition(item_id, "blocked", reason=reason)
        except HarnessError as exc:
            ctx.record_decision(f"could not transition item {item_id} to blocked: {exc}")
        raise errors.ProposalInvalid(f"item {item_id}: {reason}", errors=list(problems))

    spec_path = ctx.run_dir / "spec" / f"{item_id}.md"
    write_redacted(spec_path, body_text if body_text.endswith("\n") else body_text + "\n")
    ctx.store.update_work_item(item_id, spec_path=str(spec_path))

    ctx.record_decision(
        f"propose wrote work package for item {item_id} to {spec_path} "
        f"({len(pkg.slices)} slices, {len(pkg.behaviors)} behaviors, "
        f"{len(pkg.open_questions)} open questions, {len(pkg.touched_paths)} touched paths)"
    )
    for decision in pkg.decisions:
        ctx.record_decision(f"proposal decision: {decision}")
    for question in pkg.open_questions:
        ctx.record_decision(f"proposal open question: {question}")
    if block is None:
        ctx.record_decision(
            "the model emitted no proposal block; the front matter was derived from the work "
            "package alone (risk medium, no dependencies, green gate expectation)"
        )

    filename = f"{item_id}-{_slugify(str(front['title']))}.md"
    proposal_text = redact.redact(render_front_matter(front) + "\n" + body_text)
    write_redacted(ctx.run_dir / "proposal" / filename, proposal_text)
    location = ctx.store.publish_proposal(item_id, filename, proposal_text)
    ctx.record_decision(
        f"published proposal {filename} for item {item_id} -> {location}; front matter: "
        f"kind={front['kind']} risk={front['risk']} slices={front['slices']} "
        f"touched_paths={len(front['touched_paths'])} depends_on={front['depends_on']}"
    )
    ctx.store.append_event(item_id, "info", f"proposal published: {location}")

    log.info("proposed item %s -> %s (%s)", item_id, spec_path, location)
    return spec_path


def _enter(ctx: Context, item: Any, notes: str) -> str:
    """Move the item into ``proposing`` and return the state it came from."""
    state = item.state
    if state == "discovered":
        ctx.store.transition(item.id, "proposing", reason="propose started")
        return "discovered"
    if state == "proposed" and notes.strip():
        ctx.store.transition(
            item.id, "proposing", reason="propose re-run with trusted revision notes"
        )
        return "proposed"
    if state == "proposing":
        ctx.record_decision(
            f"item {item.id} was already proposing at entry; proceeding and treating "
            "discovered as the state to return to on a rate limit"
        )
        return "discovered"
    raise IllegalTransition(
        f"illegal transition {state} -> proposing for work item {item.id}: propose needs a "
        "discovered item, or a proposed one with revision notes"
    )


def _revert(ctx: Context, item_id: int, entry_state: str, why: str) -> None:
    try:
        ctx.store.transition(item_id, entry_state, reason=f"{why}; returned to {entry_state}")
    except HarnessError as exc:
        ctx.record_decision(f"could not return item {item_id} to {entry_state}: {exc}")


def _render_prompt(
    ctx: Context, item: Any, body: str, *, notes: str, previous_errors: list[str]
) -> str:
    number = item.issue_number
    if item.external_ref.startswith("issue:") and number is not None:
        issue_number: str = str(number)
    else:
        issue_number = "none"
    notes_text = (
        data_block("revision notes from a trusted reviewer", notes.strip())
        if notes.strip()
        else "(none)"
    )
    if previous_errors:
        errors_text = "\n".join(f"- {error}" for error in previous_errors)
    else:
        errors_text = "(none — this is the first attempt)"
    return load_prompt("propose").substitute(
        issue_number=issue_number,
        harness_issue=str(item.id),
        issue_title=item.title,
        issue_body=data_block("issue body, verbatim", body),
        repo=ctx.config.repo,
        notes=notes_text,
        previous_errors=errors_text,
    )


def _issue_body(ctx: Context, item: Any) -> tuple[str, int | None]:
    """The issue body, verbatim, and any ``issue:<n>`` upstream reference it carries."""
    ref = item.external_ref
    number = item.issue_number
    if ref.startswith("issue:") and number is not None:
        try:
            issue = ctx.gh.issue(number)
        except (GitHubError, RateCeilingReached) as exc:
            ctx.record_decision(
                f"propose could not re-read issue #{number} ({exc}); "
                f"the proposal was produced from the stored title alone"
            )
            return "(issue body unavailable: the unauthenticated read failed)", None
        return str(issue.get("body") or "").strip() or "(the issue has an empty body)", None

    self_repo = str(getattr(ctx.config, "self_repo", "") or "")
    self_number = number if ref.startswith("self:") else item.id
    if not self_repo or self_number is None:
        return "(no issue body available for this work item)", None
    try:
        data = ctx.gh.get(f"/repos/{self_repo}/issues/{int(self_number)}")
    except (GitHubError, RateCeilingReached) as exc:
        ctx.record_decision(
            f"propose could not read {self_repo}#{self_number} ({exc}); "
            "the proposal was produced from the stored title alone"
        )
        return "(issue body unavailable: the read failed)", None
    body = str(data.get("body") or "").strip() if isinstance(data, dict) else ""
    upstream_match = _ISSUE_REF.search(body)
    upstream = int(upstream_match.group(1)) if upstream_match else None
    return body or "(the issue has an empty body)", upstream


def _path_checker(ctx: Context, item: Any) -> Callable[[str], bool]:
    """B104: does the path exist in the product repository at the pinned base commit?"""
    ref = item.base_sha or "HEAD"
    repo = ctx.config.repo

    def path_exists(path: str) -> bool:
        clean = path.replace("\\", "/").strip().strip("/")
        if not clean:
            return False
        try:
            data = ctx.gh.get(f"/repos/{repo}/contents/{clean}?ref={ref}")
        except GitHubError:
            return False
        return data is not None

    return path_exists


def _open_issue_checker(ctx: Context, item_id: int) -> Callable[[int], bool]:
    """``issue`` must be this work item, and it must still be open (not merged/abandoned)."""

    def open_issue(number: int) -> bool:
        if number != item_id:
            return False
        found = ctx.store.get_work_item(number)
        return found is not None and found.state not in TERMINAL_STATES

    return open_issue
