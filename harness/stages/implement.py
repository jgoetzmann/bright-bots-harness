"""The implement stage: clone, baseline, model call, format, commit, gates, diagnose, block."""

from __future__ import annotations

import dataclasses
import json
import logging
import re
from pathlib import Path
from typing import Any, Sequence

from harness import commitmsg, gates, prettier
from harness.clone import Lease
from harness.collision import claimed_issue_numbers
from harness.config import Config
from harness.context import Context
from harness.errors import (
    GateFailed,
    GitHubError,
    Halted,
    HarnessError,
    PreflightFailed,
    RateCeilingReached,
    RunnerError,
)
from harness.halt import check_halt
from harness.redact import write_redacted
from harness.store import WorkItem
from harness.stages import load_prompt, run_model
from harness.stages.propose import (
    PROPOSALS_DIR,
    WorkPackage,
    parse_work_package,
    work_package_text,
)

__all__ = [
    "CHANGED_PATHS",
    "DIFF_LINES",
    "COMMIT",
    "GATE_RUNNER",
    "PREPARE",
    "PRETTIER",
    "evaluate_fullsend_gate",
    "implement",
]

log = logging.getLogger("harness")

ALLOWED_TOOLS = ("Read", "Edit", "Write", "Bash", "Glob", "Grep")
DISALLOWED_TOOLS = ("WebFetch", "WebSearch")
TIMEOUT_S = 3600

#: Paths that keep a proposal off the fullsend path (F4) — infrastructure others depend on.
FULLSEND_FORBIDDEN_PATHS = (
    "/prisma/",
    "/migrations/",
    "/backend/scripts/predeploy",
    "/.github/workflows/",
)

#: Paths no harness diff may ever contain (B64).
FORBIDDEN_DIFF_PATHS = ("/.github/workflows/",)

#: Substrings that, when *added* by the diff, widen or disable a check (B64).
FORBIDDEN_ADDITIONS = (
    ("continue-on-error", "adds continue-on-error"),
    (".skip(", "adds a skipped test"),
    (".only(", "narrows the suite with .only("),
    ("xdescribe(", "adds a disabled test suite"),
    ("xit(", "adds a disabled test"),
    ("eslint-disable", "adds a lint suppression"),
    ("@ts-ignore", "adds a typecheck suppression"),
    ("@ts-expect-error", "adds a typecheck suppression"),
)

_TIMEOUT_NUMBER = re.compile(r"(?i)timeout[^0-9\n]{0,24}(\d+)")
_TITLE_HEADER = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]*)\))?!?:\s*(?P<subject>.+)$")


def _commit(clone: Path, message: str) -> None:
    """Stage everything in the clone and commit it with the given message."""
    code, _, err = gates.run_command(["git", "add", "-A"], clone)
    if code != 0:
        raise HarnessError(f"git add failed in {clone}: {err.strip()}")
    msg_path = clone / ".git" / "HARNESS_COMMIT_MSG"
    msg_path.write_text(message, encoding="utf-8", newline="\n")
    code, out, err = gates.run_command(
        [
            "git",
            "-c",
            "user.name=Bright Bots Harness",
            "-c",
            "user.email=harness@localhost",
            "commit",
            "-F",
            str(msg_path),
        ],
        clone,
    )
    if code != 0 and "nothing to commit" not in (out + err).lower():
        raise HarnessError(f"git commit failed in {clone}: {(err or out).strip()}")


# Module-level injectables. Tests replace these; production uses the real thing.
GATE_RUNNER = gates.run_sequence
PREPARE = gates.prepare
PRETTIER = prettier.write_and_check
#: B222/D42: the change set, deletions included. prettier gets the surviving subset only
#: (`_format_and_commit`), because formatting a path that no longer exists is an error.
CHANGED_PATHS = prettier.all_changed_paths
COMMIT = _commit


# --------------------------------------------------------------------------------------------
# the fullsend fitness gate
# --------------------------------------------------------------------------------------------


def evaluate_fullsend_gate(pkg: WorkPackage, config: Config) -> dict[str, bool]:
    """F1–F5. All five must hold for the parallel path; any failure falls back to single-agent."""
    return {
        "F1": len(pkg.slices) >= 3,
        "F2": len(pkg.behaviors) >= 15,
        "F3": not pkg.open_questions,
        "F4": all(not _fullsend_forbidden(p) for p in pkg.touched_paths),
        "F5": bool(config.fullsend_enabled),
    }


FULLSEND_REASONS = {
    "F1": "fewer than three independently describable slices",
    "F2": "fewer than fifteen numbered behaviors",
    "F3": "the proposal still carries open questions: discovery rather than a decided spec",
    "F4": "the change set touches prisma, migrations, predeploy scripts, or CI workflows",
    "F5": "fullsend_enabled is false in .env",
}


def _normalise(path: str) -> str:
    text = str(path).replace("\\", "/").strip().strip("`").strip()
    while text.startswith("./"):
        text = text[2:]
    return "/" + text.lstrip("/")


def _fullsend_forbidden(path: str) -> bool:
    text = _normalise(path)
    return any(marker in text for marker in FULLSEND_FORBIDDEN_PATHS)


# --------------------------------------------------------------------------------------------
# the stage
# --------------------------------------------------------------------------------------------


def implement(ctx: Context, item_id: int) -> Lease:
    """Take an approved item to a committed, gate-checked branch inside a disposable clone."""
    check_halt(ctx.config.halt_file)

    item = ctx.store.get_work_item(item_id)
    if item is None:
        raise HarnessError(f"no work item {item_id}")

    # §12 Q2: the harness cannot claim an issue, so collision is re-checked immediately before
    # implementation begins, not only at selection time.
    _recheck_collision(ctx, item)

    blockers = ctx.clones.preflight()
    if blockers:
        raise PreflightFailed("; ".join(blockers))

    lease = ctx.clones.acquire(item)
    try:
        return _implement_leased(ctx, item_id, item, lease)
    except Halted:
        # R7.7 / A10: a halt mid-stage releases the clone and leaves the item resumable.
        _release_on_halt(ctx, item_id, lease)
        raise


def _implement_leased(ctx: Context, item_id: int, item: WorkItem, lease: Lease) -> Lease:
    """The part of implement that holds a clone. Halt is checked at every expensive boundary."""
    ctx.store.update_work_item(item_id, base_sha=lease.base_sha, branch_name=lease.branch)
    ctx.store.transition(item_id, "implementing", reason="implement acquired a clone")
    ctx.record_decision(
        f"acquired clone {lease.path} on branch {lease.branch} at base {lease.base_sha}"
    )

    # Dependencies first, or every gate is vacuously red on a fresh clone. Not a gate; recorded
    # in the evidence as what it is.
    prep = list(PREPARE(lease.path))
    _write_gates(ctx, "prepare", prep)
    check_halt(ctx.config.halt_file)
    if prep:
        ctx.record_decision(
            "prepared the clone: "
            + ", ".join(f"{r.name} (exit {r.exit_code})" for r in prep)
            + "; an install step, not a gate, and not part of the gate sequence"
        )
    else:
        ctx.record_decision("no package-lock.json in the clone; dependency install skipped")

    # B62: the untouched tree is measured before anything changes, and recorded separately.
    baseline = list(GATE_RUNNER(lease.path, baseline=True))
    _write_gates(ctx, "baseline", baseline)
    check_halt(ctx.config.halt_file)
    pre_existing = sorted({r.name for r in baseline if r.exit_code != 0})
    if pre_existing:
        ctx.record_decision(
            "baseline gates were already red before any change: "
            + ", ".join(pre_existing)
            + "; these are pre-existing and are not attributed to this change, "
            "and none of them justifies loosening anything"
        )
    else:
        ctx.record_decision("baseline gate sequence was fully green on the untouched tree")

    spec_text = _read_spec(ctx, item)
    pkg = parse_work_package(spec_text)
    fullsend = _fullsend_decision(ctx, pkg)

    prompt_name = "implement_fullsend" if fullsend else "implement"
    prompt = load_prompt(prompt_name).substitute(
        spec_text=spec_text, repo=ctx.config.repo, branch=lease.branch
    )
    result = run_model(
        ctx,
        stage="implement",
        item_id=item_id,
        prompt=prompt,
        allowed_tools=ALLOWED_TOOLS,
        disallowed_tools=DISALLOWED_TOOLS,
        timeout_s=TIMEOUT_S,
        cwd=lease.path,
        add_dirs=(lease.path,),
    )
    if not result.ok:
        _block(ctx, item_id, lease, f"implementation call failed: {result.error or 'unknown'}")
        raise RunnerError(f"implement call failed for item {item_id}: {result.error or 'unknown'}")

    changed = _guarded_changed_paths(ctx, lease)
    if not changed:
        # B223/D43: an implement call that changed nothing has failed, and it must say so.
        # Without this the run committed nothing, packaged zero patches, printed "implemented
        # item N" and exited 0 -- a silent success, which is the one outcome a reviewer cannot
        # catch by reading the exit code. Blocking keeps the clone for inspection.
        reason = "the implementation call left the tree unchanged"
        if pkg.touched_paths:
            reason += "; the work package expected " + ", ".join(pkg.touched_paths[:5])
        _block(ctx, item_id, lease, reason)
        raise RunnerError(f"implement produced no change for item {item_id}: {reason}")
    _reject_forbidden_diff(ctx, item_id, lease, changed)

    _format_and_commit(ctx, pkg, item, lease, changed, first=True)

    final = list(GATE_RUNNER(lease.path, baseline=False))
    _write_gates(ctx, "final", final)

    attempts = 0
    seen: set[str] = set()
    while True:
        check_halt(ctx.config.halt_file)
        new_failures = _new_failures(baseline, final)
        if not new_failures:
            break
        signature = gates.signature(new_failures)
        if signature in seen:
            # B63: a repeated signature means the diagnose loop is going nowhere. Stop now
            # rather than burning the remaining retries on the same wall.
            ctx.record_decision(
                f"gate failure signature {signature[:12]} repeated; stopping immediately "
                f"with {ctx.config.max_retries_gates - attempts} retries unused"
            )
            break
        seen.add(signature)
        if attempts >= ctx.config.max_retries_gates:
            ctx.record_decision(
                f"exhausted max_retries_gates={ctx.config.max_retries_gates} without reaching green"
            )
            break
        attempts += 1
        ctx.record_decision(
            f"diagnose cycle {attempts}: red gates "
            + ", ".join(r.name for r in new_failures)
            + f" (signature {signature[:12]})"
        )
        final = _diagnose_cycle(ctx, item_id, pkg, item, lease, new_failures, spec_text)
        _write_gates(ctx, "final", final)

    remaining = _new_failures(baseline, final)
    if remaining:
        names = ", ".join(f"{r.name} (exit {r.exit_code})" for r in remaining)
        _block(ctx, item_id, lease, f"gates still red after {attempts} diagnose cycles: {names}")
        raise GateFailed(
            f"item {item_id} blocked: gates red after {attempts} diagnose cycles: {names}"
        )

    ctx.store.update_work_item(item_id, attempts=item.attempts + 1)
    still_red = sorted({r.name for r in final if r.exit_code != 0})
    if still_red:
        # Pre-existing reds are carried, not cured. Saying "green" here would be a lie.
        ctx.record_decision(
            f"no new gate failures versus baseline for item {item_id} on {lease.branch} "
            f"after {attempts} diagnose cycles; the sequence is NOT green: "
            f"{len(still_red)} pre-existing red(s) carried ({', '.join(still_red)}); "
            "no gate was widened, skipped or retimed"
        )
    else:
        ctx.record_decision(
            f"gate sequence green for item {item_id} on {lease.branch} "
            f"after {attempts} diagnose cycles; no gate was widened, skipped or retimed"
        )
    log.info("implemented item %s on %s", item_id, lease.branch)
    return lease


# --------------------------------------------------------------------------------------------
# pieces of the loop
# --------------------------------------------------------------------------------------------


def _recheck_collision(ctx: Context, item: Any) -> None:
    number = item.issue_number
    if number is None:
        return
    try:
        branches = ctx.gh.branches()
        pulls = ctx.gh.pulls()
    except (GitHubError, RateCeilingReached) as exc:
        ctx.record_decision(
            f"collision re-check before implement could not read GitHub ({exc}); "
            f"proceeding on the selection-time check alone and recording the gap"
        )
        return
    claimed = claimed_issue_numbers(branches, [str(p.get("title") or "") for p in pulls])
    if number not in claimed:
        ctx.record_decision(
            f"collision re-check immediately before implement: #{number} is still unclaimed "
            f"among {len(branches)} branches and {len(pulls)} open pull requests"
        )
        return
    ctx.record_decision(
        f"collision re-check immediately before implement: #{number} is now claimed by "
        f"in-flight work; the item is blocked rather than duplicating another contributor"
    )
    ctx.store.transition(item.id, "implementing", reason="implement started")
    ctx.store.transition(
        item.id, "blocked", reason=f"#{number} was claimed by in-flight work before implement began"
    )
    raise HarnessError(
        f"item {item.id} blocked: issue #{number} was claimed by in-flight work before "
        f"implementation began"
    )


def _fullsend_decision(ctx: Context, pkg: WorkPackage) -> bool:
    result = evaluate_fullsend_gate(pkg, ctx.config)
    for key in ("F1", "F2", "F3", "F4", "F5"):
        if not result[key]:
            # B61: each individual failure is recorded, not just the verdict.
            ctx.record_decision(f"fullsend gate {key} failed: {FULLSEND_REASONS[key]}")
    passed = all(result[key] for key in ("F1", "F2", "F3", "F4", "F5"))
    ctx.record_decision(
        "fullsend fitness gate "
        + ("passed on all five conditions; using the parallel-slice prompt" if passed else "failed")
        + f"; gate = {json.dumps(result, sort_keys=True)}"
    )
    path = ctx.run_dir / "fullsend_gate.json"
    write_redacted(path, json.dumps(result, indent=2) + "\n")
    return passed


def _read_spec(ctx: Context, item: Any) -> str:
    """B226: the recorded spec when it is still on this disk, else the committed proposal."""
    if not item.spec_path and not _proposal_exists(ctx, item):
        raise HarnessError(f"item {item.id} has no spec_path; run propose and approve first")
    return work_package_text(item, repo_root=ctx.config.repo_root)


def _proposal_exists(ctx: Context, item: Any) -> bool:
    root = Path(ctx.config.repo_root) / PROPOSALS_DIR
    return any(root.glob(f"{int(item.id)}-*.md"))


def _guarded_changed_paths(ctx: Context, lease: Lease) -> list[str]:
    changed = list(CHANGED_PATHS(lease.path, lease.base_sha))
    ctx.record_decision(
        f"changed paths versus {lease.base_sha[:12]}: "
        + (", ".join(changed) if changed else "(none)")
    )
    if not changed:
        ctx.record_decision(
            "the implementation call left the tree unchanged; nothing will be committed"
        )
    return changed


def _reject_forbidden_diff(
    ctx: Context, item_id: int, lease: Lease, changed: Sequence[str]
) -> None:
    """B64. A diff that widens a check is rejected whole; the item is blocked."""
    violations: list[str] = []
    for path in changed:
        text = _normalise(path)
        if any(marker in text for marker in FORBIDDEN_DIFF_PATHS):
            violations.append(f"{path} is a CI workflow and may never be modified")

    added, removed = DIFF_LINES(lease, changed)
    for needle, description in FORBIDDEN_ADDITIONS:
        for line in added:
            if needle in line:
                violations.append(f"{description}: {line.strip()[:120]}")
                break

    added_timeouts = _timeout_numbers(added)
    removed_timeouts = _timeout_numbers(removed)
    if added_timeouts and (not removed_timeouts or max(added_timeouts) > max(removed_timeouts)):
        violations.append(
            f"raises or introduces a timeout (added {max(added_timeouts)}, "
            f"previous {max(removed_timeouts) if removed_timeouts else 'none'})"
        )

    if not violations:
        ctx.record_decision(
            "forbidden-diff check passed: no CI workflow touched, no check disabled, "
            "no timeout raised"
        )
        return

    reason = "; ".join(violations)
    ctx.record_decision(f"forbidden-diff check rejected the change: {reason}")
    _block(ctx, item_id, lease, f"forbidden diff: {reason}")
    raise HarnessError(f"item {item_id} blocked: forbidden diff: {reason}")


def _diff_lines(lease: Lease, changed: Sequence[str]) -> tuple[list[str], list[str]]:
    """Added and removed lines of the working diff, plus every line of each untracked file."""
    added: list[str] = []
    removed: list[str] = []
    code, out, _ = gates.run_command(["git", "diff", "--unified=0", lease.base_sha], lease.path)
    if code == 0:
        for line in out.splitlines():
            if line.startswith("+++") or line.startswith("---"):
                continue
            if line.startswith("+"):
                added.append(line[1:])
            elif line.startswith("-"):
                removed.append(line[1:])

    code, out, _ = gates.run_command(
        ["git", "ls-files", "--others", "--exclude-standard"], lease.path
    )
    if code == 0:
        for rel in out.splitlines():
            rel = rel.strip()
            if not rel:
                continue
            candidate = lease.path / rel
            if not candidate.is_file():
                continue
            try:
                added.extend(
                    candidate.read_text(encoding="utf-8", errors="replace").splitlines()
                )
            except OSError:
                continue
    return added, removed


#: Injectable so the diff-text arms of B64 can be exercised without a git repository.
DIFF_LINES = _diff_lines


def _timeout_numbers(lines: Sequence[str]) -> list[int]:
    found: list[int] = []
    for line in lines:
        for match in _TIMEOUT_NUMBER.findall(line):
            try:
                found.append(int(match))
            except ValueError:
                continue
    return found


def _format_and_commit(
    ctx: Context,
    pkg: WorkPackage,
    item: Any,
    lease: Lease,
    changed: Sequence[str],
    *,
    first: bool,
) -> None:
    if not changed:
        return
    # B222: `changed` now carries deletions, and prettier cannot format a file that is gone.
    formattable = [path for path in changed if (Path(lease.path) / path).exists()]
    ok, output = PRETTIER(lease.path, formattable)
    deleted = len(changed) - len(formattable)
    ctx.record_decision(
        f"prettier over {len(formattable)} of {len(changed)} changed path(s)"
        + (f" ({deleted} deleted, nothing to format)" if deleted else "")
        + ": "
        + ("clean" if ok else "reported differences")
        + (f" — {output.strip()[:300]}" if output and not ok else "")
    )

    message = _commit_message(pkg, item, first=first)
    problems = commitmsg.validate(message)
    if problems:
        ctx.record_decision(
            "generated commit message violated the house rules ("
            + "; ".join(problems)
            + "); falling back to a minimal conforming message"
        )
        message = commitmsg.build(
            "chore",
            "harness",
            f"apply approved change for {item.external_ref}",
            "Generated by the Bright Bots Harness from an approved work package.",
            [],
        )
    COMMIT(lease.path, message)
    ctx.record_decision(f"committed on {lease.branch}: {message.splitlines()[0]}")


def _commit_message(pkg: WorkPackage, item: Any, *, first: bool) -> str:
    type_, scope, subject = _split_title(pkg.title)
    if not first:
        subject = f"address gate failures for {item.external_ref}"
        type_ = "fix"
    body = pkg.approach.strip() or pkg.diagnosis.strip() or "Applies the approved work package."
    footers: list[str] = []
    number = item.issue_number
    if number is not None:
        footers.append(f"Refs: #{number}")
    return commitmsg.build(type_, scope, subject, body, footers)


def _split_title(title: str) -> tuple[str, str | None, str]:
    text = (title or "").strip()
    match = _TITLE_HEADER.match(text)
    if match is not None and match.group("type") in commitmsg.TYPES:
        scope = (match.group("scope") or "").strip() or None
        return match.group("type"), scope, match.group("subject").strip()
    return "fix", None, text or "apply the approved work package"


def _diagnose_cycle(
    ctx: Context,
    item_id: int,
    pkg: WorkPackage,
    item: Any,
    lease: Lease,
    failures: Sequence[Any],
    spec_text: str,
) -> list[Any]:
    prompt = load_prompt("diagnose_gate_failure").substitute(
        gate_output=_render_gate_output(failures), spec_text=spec_text
    )
    result = run_model(
        ctx,
        stage="implement",
        item_id=item_id,
        prompt=prompt,
        allowed_tools=ALLOWED_TOOLS,
        disallowed_tools=DISALLOWED_TOOLS,
        timeout_s=TIMEOUT_S,
        cwd=lease.path,
        add_dirs=(lease.path,),
    )
    if not result.ok:
        ctx.record_decision(f"diagnose call failed: {result.error or 'unknown'}")
        return list(GATE_RUNNER(lease.path, baseline=False))

    changed = list(CHANGED_PATHS(lease.path, lease.base_sha))
    _reject_forbidden_diff(ctx, item_id, lease, changed)
    _format_and_commit(ctx, pkg, item, lease, changed, first=False)
    return list(GATE_RUNNER(lease.path, baseline=False))


def _render_gate_output(results: Sequence[Any]) -> str:
    chunks: list[str] = []
    for result in results:
        chunks.append(
            f"### {result.name}\n"
            f"argv: {' '.join(result.argv)}\n"
            f"exit code: {result.exit_code}\n"
            f"--- stdout ---\n{result.stdout_tail}\n"
            f"--- stderr ---\n{result.stderr_tail}\n"
        )
    return "\n".join(chunks) if chunks else "(no gate output captured)"


def _new_failures(baseline: Sequence[Any], final: Sequence[Any]) -> list[Any]:
    """Failures introduced by the change. A gate red in the baseline is pre-existing."""
    pre = {r.name for r in baseline if r.exit_code != 0}
    return [r for r in final if r.exit_code != 0 and r.name not in pre]


def _write_gates(ctx: Context, name: str, results: Sequence[Any]) -> Path:
    path = ctx.run_dir / "gates" / f"{name}.json"
    payload = [dataclasses.asdict(r) for r in results]
    write_redacted(path, json.dumps(payload, indent=2, default=list) + "\n")
    return path


def _release_on_halt(ctx: Context, item_id: int, lease: Lease) -> None:
    """Halt appeared while a clone was held: reset to approved, drop the clone, record it."""
    ctx.record_decision(
        f"halt file appeared during implement; clone {lease.path} released and item {item_id} "
        "reset to approved for a later run"
    )
    try:
        ctx.store.transition(item_id, "approved", reason="halted mid-implement")
    except HarnessError as exc:  # already terminal — record, do not mask the halt
        ctx.record_decision(f"could not reset item {item_id} to approved on halt: {exc}")
    ctx.clones.release(lease, keep=False)


def _block(ctx: Context, item_id: int, lease: Lease, reason: str) -> None:
    ctx.record_decision(f"item {item_id} blocked: {reason}")
    ctx.store.append_event(item_id, "error", f"implement blocked: {reason}")
    try:
        ctx.store.transition(item_id, "blocked", reason=reason)
    except HarnessError as exc:  # already terminal, or an illegal pair — record, do not mask
        ctx.record_decision(f"could not transition item {item_id} to blocked: {exc}")
    ctx.clones.release(lease, keep=True)
    ctx.record_decision(f"clone kept at {lease.path} for inspection")
