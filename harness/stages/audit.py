"""The audit stage: one lens, one read, one findings issue, no work items (B247-B256).

An audit is deliberately not a discovery route. Discovery produces work; an audit produces a
*list*, and a person turns lines of that list into work with `/harness promote`. The gap between
the two is the whole point: one sentence ("audit accessibility") must not become eight
implementation runs nobody approved.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable

from harness import links
from harness.context import Context
from harness.errors import HarnessError
from harness.halt import check_halt
from harness.stages import data_block, load_prompt, run_model
from harness.store.sqlite import KIND_LABELS, VIA_LABELS

__all__ = [
    "audit",
    "promote",
    "Finding",
    "parse_findings",
    "render_audit_body",
    "tick",
    "ALLOWED_TOOLS",
    "DISALLOWED_TOOLS",
    "SEVERITIES",
    "TIMEOUT_S",
]

log = logging.getLogger("harness")

ALLOWED_TOOLS = ("Read", "Glob", "Grep")
DISALLOWED_TOOLS = ("Bash", "Edit", "Write", "WebFetch", "WebSearch")
TIMEOUT_S = 1800

SEVERITIES: tuple[str, ...] = ("high", "medium", "low")

#: The marker that makes a finding promotable. `- [ ]` is GitHub's own checkbox, so the issue is
#: readable as a list of what remains without anyone learning a convention.
_FINDING_RE = re.compile(
    r"^\s*-\s*\[(?P<done>[ xX])\]\s*\*\*(?P<n>\d+)\.\s*(?P<title>[^*]+)\*\*\s*(?P<rest>.*)$"
)

#: What the model emits, before it becomes the checklist above.
_MODEL_LINE = re.compile(
    r"^\s*(?P<n>\d+)[.)]\s*\*\*(?P<title>[^*]+)\*\*\s*(?:[-—]\s*(?P<rest>.*))?$"
)

_NOT_REACHED = re.compile(r"^\s{0,3}##\s+not\s+reached\s*$", re.IGNORECASE)
_FINDINGS_HEAD = re.compile(r"^\s{0,3}##\s+findings\s*$", re.IGNORECASE)


class Finding:
    """One line of an audit: a title, the paths it concerns, a severity, and why it matters."""

    __slots__ = ("number", "title", "paths", "severity", "note", "done")

    def __init__(
        self,
        number: int,
        title: str,
        *,
        paths: tuple[str, ...] = (),
        severity: str = "medium",
        note: str = "",
        done: bool = False,
    ) -> None:
        self.number = int(number)
        self.title = title.strip()
        self.paths = paths
        self.severity = severity if severity in SEVERITIES else "medium"
        self.note = note.strip()
        self.done = bool(done)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Finding({self.number}, {self.title!r}, severity={self.severity!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Finding):
            return NotImplemented
        return (
            self.number == other.number
            and self.title == other.title
            and self.paths == other.paths
            and self.severity == other.severity
            and self.note == other.note
            and self.done == other.done
        )

    def line(self) -> str:
        """The finding as one checklist line of the issue body."""
        box = "x" if self.done else " "
        parts = [f"- [{box}] **{self.number}. {self.title}**"]
        if self.paths:
            parts.append(" — " + ", ".join(f"`{p}`" for p in self.paths))
        parts.append(f" — {self.severity}")
        if self.note:
            parts.append(f" — {self.note}")
        return "".join(parts)


def parse_findings(text: str) -> tuple[list[Finding], str]:
    """``(findings, not_reached)`` from either the model's output or an audit issue body.

    Both shapes are read by one parser on purpose: `promote` has to re-read what `audit` wrote,
    and two parsers that must agree about a format eventually do not.
    """
    findings: list[Finding] = []
    not_reached: list[str] = []
    in_not_reached = False
    for raw in str(text or "").splitlines():
        if _NOT_REACHED.match(raw):
            in_not_reached = True
            continue
        if _FINDINGS_HEAD.match(raw):
            in_not_reached = False
            continue
        if in_not_reached:
            if raw.strip().startswith("#"):
                in_not_reached = False
            elif raw.strip():
                not_reached.append(raw.strip().lstrip("-").strip())
            continue
        match = _FINDING_RE.match(raw) or _MODEL_LINE.match(raw)
        if match is None:
            continue
        groups = match.groupdict()
        paths, severity, note = _split_rest(groups.get("rest") or "")
        findings.append(
            Finding(
                int(groups["n"]),
                groups["title"],
                paths=paths,
                severity=severity,
                note=note,
                done=str(groups.get("done") or " ").strip().lower() == "x",
            )
        )
    return findings, "\n".join(f"- {line}" for line in not_reached)


def _split_rest(rest: str) -> tuple[tuple[str, ...], str, str]:
    """The `paths — severity — sentence` tail of a finding line, in whatever order it arrived."""
    fields = [part.strip() for part in re.split(r"\s+[—-]\s+", rest.strip()) if part.strip()]
    paths: tuple[str, ...] = ()
    severity = "medium"
    note = ""
    for field in fields:
        low = field.strip().strip(".").lower()
        if low in SEVERITIES:
            severity = low
        elif "`" in field and not note:
            paths = tuple(p for p in re.findall(r"`([^`]+)`", field) if p.strip())
        elif not note:
            note = field
        else:
            note = f"{note} — {field}"
    return paths, severity, note


def render_audit_body(
    config: Any,
    *,
    lens: str,
    findings: Iterable[Finding],
    not_reached: str,
    base_sha: str,
    actor: str,
    stopped: str = "",
    trusted: Any = None,
) -> str:
    """The body of the audit issue: the lens, the findings as a checklist, what was missed."""
    upstream = str(getattr(config, "upstream_repo", "") or "")
    lines = [
        f"An audit of [`{upstream}`]({links.repo_url(upstream)}) at "
        f"`{base_sha[:12] or 'main'}`, requested by @{actor or 'someone'}.",
        "",
        "**Lens.** " + lens.strip(),
        "",
        "This issue is a **list, not a plan**, and it is not a work item — nothing here is "
        "queued and nothing here will be implemented on its own. Reply "
        "`/harness promote <n>` (or `/harness promote all`) to turn a finding into a work "
        "item, which then goes through the ordinary proposal gate like any other.",
        "",
        "## Findings",
        "",
    ]
    listed = list(findings)
    if listed:
        lines.extend(finding.line() for finding in listed)
    else:
        lines.append("None. Nothing in this repository matched the lens.")
    lines.append("")
    if stopped:
        # B250: an audit that ran out of room says so where the reader is, not only in a log.
        lines.append(f"> **Incomplete.** {stopped}")
        lines.append("")
    if not_reached.strip():
        lines.append("## Not reached")
        lines.append("")
        lines.append(not_reached.strip())
        lines.append("")
    lines.append(links.signature(config, trusted=trusted))
    return "\n".join(lines)


def tick(body: str, numbers: Iterable[int]) -> str:
    """Check off the named findings in an audit issue body, leaving everything else alone."""
    wanted = {int(n) for n in numbers}
    out: list[str] = []
    for raw in str(body or "").splitlines():
        match = _FINDING_RE.match(raw)
        if match is not None and int(match.group("n")) in wanted:
            out.append(raw.replace("- [ ]", "- [x]", 1))
        else:
            out.append(raw)
    return "\n".join(out)


# --------------------------------------------------------------------------------------------
# the stage
# --------------------------------------------------------------------------------------------


def audit(ctx: Context, *, lens: str, actor: str = "") -> int:
    """B247-B251: one read of the product repository, one findings issue, no work items."""
    ctx.check_halt()
    scope = str(lens or "").strip()
    if not scope:
        # B256: refused before any GitHub read and before any model call. An audit with no lens
        # is "look at everything", which is the one scope the budget cannot bound.
        raise HarnessError(
            "audit needs a lens: what should it look for? `/harness audit accessibility in "
            "src/components`, say, or `/harness audit redundant code`."
        )
    if not ctx.gh.can_write:
        raise HarnessError("audit opens an issue and there is no write credential")

    lease = ctx.clones.acquire(_READER, run_id="audit", read_only=True)
    stopped = ""
    try:
        prompt = load_prompt("audit").substitute(
            actor=actor or "someone",
            repo=str(getattr(ctx.config, "upstream_repo", "") or ""),
            base_sha=lease.base_sha or "unknown",
            lens=data_block("the lens", scope),
        )
        result = run_model(
            ctx,
            stage="audit",
            item_id=None,
            prompt=prompt,
            allowed_tools=ALLOWED_TOOLS,
            disallowed_tools=DISALLOWED_TOOLS,
            timeout_s=TIMEOUT_S,
            cwd=lease.path,
        )
        base_sha = lease.base_sha or ""
    finally:
        ctx.clones.release(lease, keep=False)

    if not result.ok:
        # B250: a cap reached mid-audit is not a crash. Whatever was found before the ceiling is
        # still worth reading, and the issue says where it stopped so the next one can continue.
        stopped = str(result.error or "the audit stopped before it finished")
    findings, not_reached = parse_findings(result.text or "")
    if not findings and not stopped:
        ctx.record_decision(f"audit through the lens {scope!r} found nothing to report")

    title = f"Audit: {scope}" if len(scope) <= 60 else f"Audit: {scope[:59].rstrip()}…"
    body = render_audit_body(
        ctx.config,
        lens=scope,
        findings=findings,
        not_reached=not_reached,
        base_sha=base_sha,
        actor=actor,
        stopped=stopped,
        trusted=ctx.trusted,
    )
    # B251/B255: no stage label. Without one the store does not see it, so it can never enter
    # the queue and can never be transitioned -- the label families are what make it a work item.
    created = ctx.gh.create_issue(title, body, [KIND_LABELS["audit"], VIA_LABELS["requested"]])
    number = int(created["number"])
    ctx.record_decision(
        f"audit through the lens {scope!r} opened issue #{number} with {len(findings)} "
        f"finding(s) and created no work items; promoting one is a person's decision"
    )
    log.info("audit opened issue %s with %d findings", number, len(findings))
    return number


def promote(ctx: Context, *, issue_number: int, which: str, actor: str = "") -> list[int]:
    """B252-B254: one work item per named finding, `via:audit`, ticked off in the issue."""
    ctx.check_halt()
    issue = ctx.gh.get(f"/repos/{ctx.config.self_repo}/issues/{int(issue_number)}") or {}
    body = str(issue.get("body") or "")
    findings, _ = parse_findings(body)
    if not findings:
        raise HarnessError(f"#{issue_number} lists no findings to promote")

    by_number = {finding.number: finding for finding in findings}
    wanted = _wanted(which, findings, cap=int(getattr(ctx.config, "suggest_max_per_run", 0) or 0))
    if not wanted:
        raise HarnessError(
            "promote which? `/harness promote 3`, `/harness promote 1,4,7`, or "
            "`/harness promote all`."
        )
    unknown = sorted(n for n in wanted if n not in by_number)
    if unknown:
        raise HarnessError(
            f"#{issue_number} has no finding {', '.join(str(n) for n in unknown)}; "
            f"it has 1-{max(by_number)}"
        )

    created: list[int] = []
    promoted: list[int] = []
    for number in sorted(wanted):
        finding = by_number[number]
        ref = f"audit:{int(issue_number)}:{number}"
        existing = ctx.store.find_by_ref(ref)
        if existing is not None:
            # B254: promoting twice is one item. The reference carries the audit and the line,
            # so the second attempt finds the first rather than opening a near-duplicate.
            promoted.append(number)
            continue
        item_id = ctx.store.create_work_item(
            kind="issue",
            external_ref=ref,
            title=finding.title,
            tier_required=0,
            upstream_body=_finding_body(ctx, issue_number, finding),
            via="audit",
        )
        ctx.store.append_event(
            item_id, "info", f"promoted from finding {number} of audit #{issue_number}"
        )
        created.append(item_id)
        promoted.append(number)

    if promoted and ctx.gh.can_write:
        ctx.gh.update_issue_body(ctx.config.self_repo, int(issue_number), tick(body, promoted))
    ctx.record_decision(
        f"promoted finding(s) {', '.join(str(n) for n in sorted(promoted))} of audit "
        f"#{issue_number} into work item(s) {created or 'that already existed'}, at the "
        f"request of @{actor or 'someone'}"
    )
    return created


def _wanted(which: str, findings: list[Finding], *, cap: int) -> set[int]:
    """The finding numbers `which` names. `all` means every unticked one, up to the cap."""
    text = str(which or "").strip().lower()
    if text in ("all", "*", "everything"):
        open_ones = [f.number for f in findings if not f.done]
        # B253: `all` is still bounded. A twenty-finding audit promoted in one comment would put
        # twenty proposals in the queue, which is exactly what the audit/promote split prevents.
        return set(open_ones[:cap] if cap else open_ones)
    return {int(n) for n in re.findall(r"\d+", text)}


def _finding_body(ctx: Context, issue_number: int, finding: Finding) -> str:
    """What the work item quotes: the finding, and where it came from."""
    self_repo = str(getattr(ctx.config, "self_repo", "") or "")
    where = links.issue_url(self_repo, int(issue_number)) if self_repo else ""
    lines = [f"Finding {finding.number} of an audit"]
    lines[0] += f" ([#{issue_number}]({where}))." if where else f" (#{issue_number})."
    lines.append("")
    lines.append(f"**Severity.** {finding.severity}")
    if finding.paths:
        lines.append("")
        lines.append("**Paths.** " + ", ".join(f"`{p}`" for p in finding.paths))
    if finding.note:
        lines.append("")
        lines.append(finding.note)
    lines.append("")
    lines.append(
        "A finding names a problem, not a plan. The proposal is what decides what to do about "
        "it, and merging that proposal is what approves it."
    )
    return "\n".join(lines)


class _Reader:
    """The stand-in `clones.acquire` needs; an audit belongs to no work item."""

    id: Any = "audit"
    branch_name = None
    base_sha = None


_READER = _Reader()
