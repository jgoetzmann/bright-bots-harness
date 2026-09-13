"""The implement stage: clone, baseline, model call, format, commit, gates, diagnose, block."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any, Mapping, Sequence

from harness import commitmsg, gates, prettier
from harness.clone import (
    GUARD_GIT,
    HOOKS_OFF,
    Lease,
    PROTECTED_PUSH_PATHS,
    normalise_repo_path,
    protected_paths_in,
)
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
    RateLimited,
    RunnerError,
)
from harness.halt import check_halt
from harness.redact import write_redacted
from harness.store import WorkItem
from harness.stages import data_block, load_prompt, run_model
# D70: module-level, for the handoff and `_tip_sha`. `deliver` imports `implement` lazily, so
# this is no cycle; `revise` already imports both at module level.
from harness.stages import deliver as deliver_mod
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
    "RESET_TO",
    "RESTORE_PATHS",
    "TIP_SHA",
    "UNIFIED_DIFF",
    "evaluate_fullsend_gate",
    "implement",
    "parse_self_audit",
]

log = logging.getLogger("harness")

ALLOWED_TOOLS = ("Read", "Edit", "Write", "Bash", "Glob", "Grep")
DISALLOWED_TOOLS = ("WebFetch", "WebSearch")
TIMEOUT_S = 3600

#: D70: the auditor reads and may run read-only commands, and may not edit. `Bash` is the
#: operator's ruling and widens the credential surface (docs/SAFETY.md); anything it writes to
#: the clone is discarded by the tree guard in `_run_self_audit`. To make it read-only, use
#: `ask.py`'s tuples here.
SELFAUDIT_ALLOWED_TOOLS = ("Read", "Glob", "Grep", "Bash")
SELFAUDIT_DISALLOWED_TOOLS = ("Edit", "Write", "WebFetch", "WebSearch")
SELFAUDIT_TIMEOUT_S = 1800
#: D70: the fix pass is one more implementation pass, with implement's own tools.
SELFAUDIT_FIX_ALLOWED_TOOLS = ALLOWED_TOOLS
SELFAUDIT_FIX_DISALLOWED_TOOLS = DISALLOWED_TOOLS

#: D70: how much of the diff the auditor is shown, in the style of `revise.MAX_FEEDBACK_CHARS`.
#: A finding is judged against the full change list, never the paths in the cut text.
MAX_DIFF_CHARS = 60_000
#: D70: the most findings one audit may carry, and how much of each is kept.
MAX_SELF_AUDIT_FINDINGS = 20
MAX_FINDING_CLAIM_CHARS = 500
MAX_FINDING_WHERE_CHARS = 200
MAX_FINDING_EVIDENCE_CHARS = 1000
SELFAUDIT_SEVERITIES = ("blocking", "note")
SELFAUDIT_VERDICTS = ("clean", "findings")

#: Paths that keep a proposal off the fullsend path (F4) — infrastructure others depend on.
FULLSEND_FORBIDDEN_PATHS = (
    "/prisma/",
    "/migrations/",
    "/backend/scripts/predeploy",
    "/.github/workflows/",
)

#: Paths no harness diff may ever contain (B64). D67: the one set, defined beside the push
#: guard in `clone.py` and widened there from `.github/workflows/` to all of `.github/`; the
#: name stays because B64 and its documents cite it.
FORBIDDEN_DIFF_PATHS = PROTECTED_PUSH_PATHS

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


#: D70: every git call the self-audit makes to change the clone. No hooks, and literal paths,
#: so a filename cannot be read as pathspec magic.
_SELFAUDIT_GIT: tuple[str, ...] = (
    *GUARD_GIT, "-c", f"core.hooksPath={HOOKS_OFF}", "--literal-pathspecs",
)


def _unified_diff(lease: Lease) -> tuple[str, bool]:
    """D70: the committed change as the auditor reads it, cut at ``MAX_DIFF_CHARS``.

    `GUARD_GIT`, and no external diff or textconv driver: the clone's `.git/config` is a file a
    model holding Bash could have edited, and a driver named there would decide what the audit
    saw. Raises when git cannot produce the diff; the caller records the audit as not run.
    """
    code, out, err = gates.run_command(
        [*GUARD_GIT, "diff", "--no-ext-diff", "--no-textconv", "--no-color",
         f"{lease.base_sha}..HEAD"],
        lease.path,
    )
    if code != 0:
        raise HarnessError(
            f"git diff {lease.base_sha[:12]}..HEAD failed ({code}): {(err or out).strip()[:300]}"
        )
    if len(out) <= MAX_DIFF_CHARS:
        return out, False
    marker = (
        f"\n[diff truncated here: {MAX_DIFF_CHARS} of {len(out)} characters shown; read the "
        "rest with git diff in the clone]\n"
    )
    return out[:MAX_DIFF_CHARS] + marker, True


def _reset_to(clone: Path, sha: str) -> None:
    """D70: put the branch, the index and the tree back on ``sha``. Raises when git refuses."""
    code, out, err = gates.run_command([*_SELFAUDIT_GIT, "reset", "--hard", "--quiet", sha], clone)
    if code != 0:
        raise HarnessError(
            f"git reset --hard {sha} failed in {clone}: {(err or out).strip()[:300]}"
        )


def _restore_paths(clone: Path, tip: str, paths: Sequence[str]) -> None:
    """D70: make each path what it is at ``tip``; a path the tip does not hold is removed.

    The tree guard: whatever the auditor (or an unfinished fix pass) left in the clone would
    otherwise be committed by the next `git add -A` or by a handoff's work-in-progress commit.
    """
    root = Path(clone).resolve()
    for raw in paths:
        rel = str(raw).strip()
        if not rel:
            continue
        code, _, _ = gates.run_command([*_SELFAUDIT_GIT, "cat-file", "-e", f"{tip}:{rel}"], clone)
        if code == 0:
            code, out, err = gates.run_command(
                [*_SELFAUDIT_GIT, "checkout", tip, "--", rel], clone
            )
            if code != 0:
                raise HarnessError(
                    f"could not restore {rel} to {tip} in {clone}: {(err or out).strip()[:300]}"
                )
            continue
        gates.run_command(
            [*_SELFAUDIT_GIT, "rm", "--cached", "--quiet", "--ignore-unmatch", "--", rel], clone
        )
        target = root / rel
        if not target.parent.resolve().is_relative_to(root):
            raise HarnessError(f"refusing to remove {rel}: it is not inside the clone {root}")
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)


# Module-level injectables. Tests replace these; production uses the real thing.
GATE_RUNNER = gates.run_sequence
PREPARE = gates.prepare
PRETTIER = prettier.write_and_check
#: B222/D42: the change set, deletions included. prettier gets the surviving subset only
#: (`_format_and_commit`), because formatting a path that no longer exists is an error.
CHANGED_PATHS = prettier.all_changed_paths
COMMIT = _commit
#: D70 (handoff §4.12): the git operations the self-audit loop uses. The test rig's clone is a
#: plain directory, so each one is replaceable.
TIP_SHA = deliver_mod._tip_sha  # the 12-character tip, "" when it cannot be read
UNIFIED_DIFF = _unified_diff  # (text, truncated)
RESET_TO = _reset_to
RESTORE_PATHS = _restore_paths


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


#: D67: moved to `clone.normalise_repo_path`, beside the path set it serves; kept by this name
#: for `_fullsend_forbidden` and anything that imported it from here.
_normalise = normalise_repo_path


def _fullsend_forbidden(path: str) -> bool:
    text = _normalise(path)
    return any(marker in text for marker in FULLSEND_FORBIDDEN_PATHS)


# --------------------------------------------------------------------------------------------
# the stage
# --------------------------------------------------------------------------------------------


def implement(ctx: Context, item_id: int) -> Lease:
    """Take an approved item to a committed, gate-checked branch inside a disposable clone."""
    ctx.check_halt()

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
    ctx.check_halt()
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
    ctx.check_halt()
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
        ctx.check_halt()
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

    # D70: the gates have no new failures, by the `_new_failures` rule and never by "every gate
    # exits 0", which would skip every known-red item. 0 cycles: no call, no record, no line.
    if ctx.config.max_self_audit_cycles > 0:
        final, handed_off = _run_self_audit(
            ctx, item_id, item, lease, pkg, spec_text, baseline, final
        )
        if handed_off:
            # §4.6: halted inside the loop. The work is committed and on the fork, the item is
            # approved with a carry, and the run's next halt check stops it.
            return lease

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
    """B64. A diff that widens a check is rejected whole; the item is blocked.

    D67: the path arm asks `clone.protected_paths_in`, the predicate the push guard uses too,
    so the two cannot drift apart -- the whole of `.github/`, deletions included (D42).
    """
    violations: list[str] = [
        f"{path} is under .github/, which steers CI and review, and may never be modified"
        for path in protected_paths_in(changed)
    ]

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
            "forbidden-diff check passed: nothing under .github/ touched, no check disabled, "
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
    # B312/D67: `GUARD_GIT`, so a `refs/replace/` entry for the base cannot hide what was added.
    code, out, _ = gates.run_command(
        [*GUARD_GIT, "diff", "--unified=0", lease.base_sha], lease.path
    )
    if code == 0:
        for line in out.splitlines():
            if line.startswith("+++") or line.startswith("---"):
                continue
            if line.startswith("+"):
                added.append(line[1:])
            elif line.startswith("-"):
                removed.append(line[1:])

    code, out, _ = gates.run_command(
        [*GUARD_GIT, "ls-files", "--others", "--exclude-standard"], lease.path
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
    subject: str | None = None,
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

    message = _commit_message(pkg, item, first=first, subject=subject)
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


def _commit_message(
    pkg: WorkPackage, item: Any, *, first: bool, subject: str | None = None
) -> str:
    """The commit message. ``subject`` replaces a follow-up commit's subject (D70)."""
    given = subject
    type_, scope, subject = _split_title(pkg.title)
    if not first:
        subject = given or f"address gate failures for {item.external_ref}"
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


# --------------------------------------------------------------------------------------------
# D70: the adversarial self-audit before delivery
# --------------------------------------------------------------------------------------------

_SELFAUDIT_OPEN = re.compile(r"<!--\s*selfaudit:\s*")
_WHERE_INDEX = re.compile(r"^(acceptance|behavior):([1-9]\d{0,3})$")
_WHERE_LINE_SUFFIX = re.compile(r":\d+(?:-\d+)?$")


def _squash(value: Any) -> str:
    return " ".join(str(value if value is not None else "").split())


def _where_path(text: str) -> str:
    """A path as a finding or a work package spells it, in the form `git diff` prints it."""
    path = text.strip().strip("`'\"").replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path.lstrip("/")


def _where_ok(
    where: str, changed: set[str], touched: set[str], n_acceptance: int, n_behaviors: int
) -> bool:
    index = _WHERE_INDEX.match(where)
    if index is not None:
        limit = n_acceptance if index.group(1) == "acceptance" else n_behaviors
        return 1 <= int(index.group(2)) <= limit
    path = _where_path(_WHERE_LINE_SUFFIX.sub("", where))
    return bool(path) and (path in changed or path in touched)


def parse_self_audit(
    text: str,
    *,
    changed: Sequence[str],
    touched: Sequence[str],
    n_acceptance: int,
    n_behaviors: int,
) -> tuple[list[dict] | None, list[str]]:
    """D70: ``(findings, problems)`` from the auditor's first line.

    Styled on `propose.validate_proposal`: closed enums, a capped count. ``findings`` is
    ``None`` when there is no readable block, and ``problems`` then says why; otherwise it
    names every finding that was discarded. A finding's `where` must name a path in the full
    change list, a path in the work package's touched paths, or a valid `acceptance:<n>` or
    `behavior:<n>` index -- the last two are how an omission is reported.
    """
    raw = (text or "").lstrip()
    opening = _SELFAUDIT_OPEN.match(raw)
    if opening is None:
        return None, ["the answer does not open with a selfaudit block"]
    rest = raw[opening.end():]
    payload: Any = None
    for close in re.finditer(r"-->", rest):
        try:
            payload = json.loads(rest[: close.start()])
        except ValueError:
            continue
        break
    if not isinstance(payload, dict):
        return None, ["the selfaudit block is not a JSON object"]
    if payload.get("verdict") not in SELFAUDIT_VERDICTS:
        return None, [f"verdict must be one of {', '.join(SELFAUDIT_VERDICTS)}"]
    rows = payload.get("findings")
    if not isinstance(rows, list):
        return None, ["findings must be a list"]

    changed_set = {_where_path(p) for p in changed}
    touched_set = {_where_path(p) for p in touched}
    kept: list[dict] = []
    problems: list[str] = []
    for number, row in enumerate(rows, start=1):
        if len(kept) >= MAX_SELF_AUDIT_FINDINGS:
            problems.append(f"finding {number}: over the cap of {MAX_SELF_AUDIT_FINDINGS}")
            continue
        if not isinstance(row, dict):
            problems.append(f"finding {number}: not an object")
            continue
        severity, claim, where = row.get("severity"), row.get("claim"), row.get("where")
        evidence = row.get("evidence") if row.get("evidence") is not None else ""
        if severity not in SELFAUDIT_SEVERITIES:
            problems.append(f"finding {number}: severity must be blocking or note")
            continue
        if not isinstance(claim, str) or not claim.strip():
            problems.append(f"finding {number}: claim must be a non-empty string")
            continue
        if not isinstance(where, str) or not where.strip() or not isinstance(evidence, str):
            problems.append(f"finding {number}: where and evidence must be strings")
            continue
        where_text = _squash(where)[:MAX_FINDING_WHERE_CHARS]
        if not _where_ok(where_text, changed_set, touched_set, n_acceptance, n_behaviors):
            problems.append(
                f"finding {number}: where {where_text[:120]!r} names no changed path, no "
                "touched path, and no valid acceptance or behavior index"
            )
            continue
        kept.append(
            {
                "severity": severity,
                "claim": _squash(claim)[:MAX_FINDING_CLAIM_CHARS],
                "where": where_text,
                "evidence": _squash(evidence)[:MAX_FINDING_EVIDENCE_CHARS],
            }
        )
    return kept, problems


def _finding_signature(findings: Sequence[Mapping[str, Any]]) -> str:
    """D70: a stable hash of what was found, so the loop never re-enters on the same findings."""
    parts = sorted(
        f"{_squash(f.get('where')).lower()}\x1f{_squash(f.get('claim')).lower()}"
        for f in findings
    )
    return hashlib.sha256("\x1e".join(parts).encode("utf-8")).hexdigest()


def _self_audit_gate_summary(baseline: Sequence[Any], final: Sequence[Any]) -> str:
    pre = {r.name for r in baseline if r.exit_code != 0}
    lines: list[str] = []
    for result in final:
        if result.exit_code == 0:
            verdict = "green"
        elif result.name in pre:
            verdict = "red, pre-existing: already red on the untouched tree before any change"
        else:
            verdict = "red"
        lines.append(f"- {result.name}: exit code {result.exit_code}, {verdict}")
    return "\n".join(lines) if lines else "(no gate results were recorded)"


def _entry(cycle: int, tip: str, status: str = "ok") -> dict:
    return {"cycle": cycle, "tip": tip, "status": status, "findings": [], "outcome": ""}


def _not_run(ctx: Context, entry: dict, reason: str, detail: str = "") -> dict:
    """Fail closed, fail quiet: an audit that cannot answer has no findings and blocks nothing."""
    entry["status"] = f"not run: {reason}"
    entry["findings"] = []
    ctx.record_decision(
        f"self-audit cycle {entry['cycle']} at {entry['tip']} not run: {reason}"
        + (f" ({detail[:300]})" if detail else "")
        + "; no findings, and nothing is blocked"
    )
    return entry


def _self_audit(
    ctx: Context,
    item_id: int,
    lease: Lease,
    pkg: WorkPackage,
    spec_text: str,
    baseline: Sequence[Any],
    final: Sequence[Any],
    tip: str,
    cycle: int,
) -> dict:
    """One `selfaudit` call: the committed diff against the approved work package."""
    entry = _entry(cycle, tip)
    changed = list(CHANGED_PATHS(lease.path, lease.base_sha))
    try:
        diff, truncated = UNIFIED_DIFF(lease)
    except HarnessError as exc:
        return _not_run(ctx, entry, "the diff could not be read", str(exc))
    if not diff.strip():
        return _not_run(ctx, entry, "the diff was empty")
    if truncated:
        note = (
            f"The diff below was cut at {MAX_DIFF_CHARS} characters and is marked where it "
            f"stops. The change touches {len(changed)} path(s) in all, listed after the mark."
        )
        diff = diff + "\nEvery changed path:\n" + "\n".join(changed) + "\n"
    else:
        note = "The diff below is complete."
    prompt = load_prompt("selfaudit").substitute(
        spec_text=data_block("the approved work package", spec_text),
        touched_paths=data_block(
            "the work package's touched paths", "\n".join(pkg.touched_paths) or "(none listed)"
        ),
        gate_summary=data_block(
            "the gate results after the change", _self_audit_gate_summary(baseline, final)
        ),
        diff_truncated=note,
        diff=data_block(f"git diff {lease.base_sha[:12]}..{tip}", diff),
        repo=ctx.config.repo,
        branch=lease.branch,
    )
    try:
        result = run_model(
            ctx,
            stage="selfaudit",
            item_id=item_id,
            prompt=prompt,
            allowed_tools=SELFAUDIT_ALLOWED_TOOLS,
            disallowed_tools=SELFAUDIT_DISALLOWED_TOOLS,
            timeout_s=SELFAUDIT_TIMEOUT_S,
            cwd=lease.path,
            add_dirs=(lease.path,),
        )
    except RunnerError as exc:
        return _not_run(ctx, entry, "the audit call failed", str(exc))
    if not result.ok:
        return _not_run(ctx, entry, "the audit call failed", str(result.error or "unknown"))

    findings, problems = parse_self_audit(
        result.text,
        changed=changed,
        touched=pkg.touched_paths,
        n_acceptance=len(pkg.acceptance),
        n_behaviors=len(pkg.behaviors),
    )
    if findings is None:
        return _not_run(ctx, entry, "unreadable", "; ".join(problems))
    for problem in problems:
        ctx.record_decision(f"self-audit cycle {cycle} discarded a finding: {problem}")
    entry["findings"] = findings
    blocking = sum(1 for f in findings if f["severity"] == "blocking")
    ctx.record_decision(
        f"self-audit cycle {cycle} at {tip} -- a model reviewing its own diff; an opinion, not "
        "a gate result: "
        + (f"{blocking} blocking, {len(findings) - blocking} note(s)" if findings else "clean")
    )
    for finding in findings:
        ctx.record_decision(
            f"self-audit finding ({finding['severity']}) at {finding['where']}: "
            f"{finding['claim']}"
            + (f" -- evidence: {finding['evidence']}" if finding["evidence"] else "")
        )
    return entry


def _self_audit_fix(
    ctx: Context, item_id: int, lease: Lease, spec_text: str, blocking: Sequence[dict]
) -> str:
    """One `selfaudit_fix` call. ``""`` when the call ran, else why it did not."""
    rendered = "\n\n".join(
        f"{number}. [{f['severity']}] at {f['where']}: {f['claim']}\n"
        f"   evidence: {f['evidence'] or '(none given)'}"
        for number, f in enumerate(blocking, start=1)
    )
    prompt = load_prompt("selfaudit_fix").substitute(
        spec_text=data_block("the approved work package", spec_text),
        findings=data_block("the self-audit's blocking findings", rendered),
        repo=ctx.config.repo,
        branch=lease.branch,
    )
    try:
        result = run_model(
            ctx,
            stage="selfaudit_fix",
            item_id=item_id,
            prompt=prompt,
            allowed_tools=SELFAUDIT_FIX_ALLOWED_TOOLS,
            disallowed_tools=SELFAUDIT_FIX_DISALLOWED_TOOLS,
            timeout_s=TIMEOUT_S,
            cwd=lease.path,
            add_dirs=(lease.path,),
        )
    except RunnerError as exc:
        return str(exc) or "the runner failed"
    return "" if result.ok else str(result.error or "unknown")


def _discard_since(ctx: Context, lease: Lease, tip: str) -> None:
    """Back to ``tip``: its commit, its index, its tree, and nothing left untracked."""
    if not tip or lease.base_sha.startswith(tip):
        # Never the base: a reset there deletes the committed implementation (preflight).
        raise HarnessError(f"refusing to reset {lease.branch} to its base {lease.base_sha}")
    RESET_TO(lease.path, tip)
    leftover = list(CHANGED_PATHS(lease.path, tip))
    if leftover:
        RESTORE_PATHS(lease.path, tip, leftover)
    ctx.record_decision(f"self-audit put {lease.branch} back on {tip}")


def _record_self_audit(ctx: Context, history: Sequence[dict]) -> Path:
    """`runs/<run-id>/selfaudit.json`: every cycle, each with the tip it audited."""
    payload = {
        "schema": 1,
        "max_cycles": int(ctx.config.max_self_audit_cycles),
        "history": list(history),
    }
    path = ctx.run_dir / deliver_mod.SELFAUDIT_NAME
    write_redacted(path, json.dumps(payload, indent=2) + "\n")
    return path


def _run_self_audit(
    ctx: Context,
    item_id: int,
    item: Any,
    lease: Lease,
    pkg: WorkPackage,
    spec_text: str,
    baseline: Sequence[Any],
    final: list[Any],
) -> tuple[list[Any], bool]:
    """D70: audit, fix, re-gate, up to the cap. ``(final gate results, handed off)``.

    Advisory throughout: findings that survive go to the human, a fix pass that breaks a gate
    is reverted, and an audit that cannot answer is recorded as not run. The one exception is a
    fix pass whose diff B64 rejects, which blocks the item like any other forbidden diff.
    """
    cap = int(ctx.config.max_self_audit_cycles)
    history: list[dict] = []
    seen: set[str] = set()
    previous = final
    unverified = ""  # the pre-fix tip while a fix pass has not yet been judged by the gates
    try:
        for cycle in range(1, cap + 1):
            tip = TIP_SHA(lease)
            if not tip or lease.base_sha.startswith(tip):
                history.append(
                    _not_run(ctx, _entry(cycle, tip or lease.base_sha[:12]),
                             "the tip could not be read")
                )
                break
            before = set(CHANGED_PATHS(lease.path, tip))
            try:
                entry = _self_audit(
                    ctx, item_id, lease, pkg, spec_text, baseline, final, tip, cycle
                )
            except RateLimited:
                introduced = sorted(set(CHANGED_PATHS(lease.path, tip)) - before)
                if introduced:
                    RESTORE_PATHS(lease.path, tip, introduced)
                history.append(_entry(cycle, tip, "not run: rate limited"))
                raise
            introduced = sorted(set(CHANGED_PATHS(lease.path, tip)) - before)
            if introduced:
                # §4.5: an auditor holding Bash wrote to the clone. What it wrote is discarded,
                # and so is its audit.
                RESTORE_PATHS(lease.path, tip, introduced)
                entry = _not_run(
                    ctx, _entry(cycle, tip), "the auditor modified the tree",
                    "restored " + ", ".join(introduced[:10]),
                )
            history.append(entry)
            blocking = [f for f in entry["findings"] if f["severity"] == "blocking"]
            if entry["status"] != "ok" or not blocking:
                break
            signature = _finding_signature(blocking)
            if signature in seen:
                entry["outcome"] = "repeated finding; stopped"
                ctx.record_decision(
                    f"self-audit finding signature {signature[:12]} repeated; stopping with the "
                    "findings carried to the human"
                )
                break
            seen.add(signature)
            if cycle == cap:
                entry["outcome"] = "cap reached; findings carried"
                ctx.record_decision(
                    f"self-audit reached max_self_audit_cycles={cap}; {len(blocking)} blocking "
                    "finding(s) carried to the human, and delivery is not blocked"
                )
                break

            previous = final
            unverified = tip
            try:
                failed = _self_audit_fix(ctx, item_id, lease, spec_text, blocking)
            except RateLimited:
                _discard_since(ctx, lease, tip)
                unverified = ""
                entry["outcome"] = "fix pass rate limited; its changes were discarded"
                raise
            if failed:
                _discard_since(ctx, lease, tip)
                unverified = ""
                entry["outcome"] = "fix pass failed; its changes were discarded"
                ctx.record_decision(f"self-audit fix pass failed ({failed[:300]}); discarded")
                break
            # revise's pattern: the model does not commit, so its edits are the diff against
            # the tip it was given.
            if not CHANGED_PATHS(lease.path, tip):
                unverified = ""
                entry["outcome"] = "fix pass changed nothing"
                ctx.record_decision("self-audit fix pass changed nothing; findings carried")
                break
            changed = _guarded_changed_paths(ctx, lease)
            _reject_forbidden_diff(ctx, item_id, lease, changed)  # blocks: the one exception
            _format_and_commit(
                ctx, pkg, item, lease, changed, first=False,
                subject=f"address self-audit findings for {item.external_ref}",
            )
            final = list(GATE_RUNNER(lease.path, baseline=False))
            _write_gates(ctx, "final", final)
            broke = _new_failures(baseline, final)
            if broke:
                names = ", ".join(r.name for r in broke)
                RESET_TO(lease.path, tip)
                final = previous
                _write_gates(ctx, "final", final)
                unverified = ""
                entry["outcome"] = f"fix pass reverted: broke {names}"
                ctx.record_decision(
                    f"self-audit fix pass broke {names}, green before it; reverted to {tip} and "
                    "the green gate results written back; findings carried, item not blocked"
                )
                break
            unverified = ""
            entry["outcome"] = "fix pass committed; no new gate failures"
            ctx.record_decision(
                f"self-audit fix pass committed on {lease.branch}; the gate sequence re-ran "
                "with no new failures"
            )
            # Preflight: the one halt check in the loop outside a model call, placed where the
            # tree is gate-verified or already reverted.
            ctx.check_halt()
    except Halted as exc:
        if unverified:
            _discard_since(ctx, lease, unverified)
            final = previous
            _write_gates(ctx, "final", final)
        history.append(_entry(len(history) + 1, TIP_SHA(lease) or lease.base_sha[:12],
                              "not run: halted"))
        _record_self_audit(ctx, history)
        ctx.record_decision(
            f"halted during the self-audit of item {item_id}; handing it off with its work "
            "rather than releasing the clone"
        )
        try:
            deliver_mod.handoff(ctx, item_id, reason=f"halted during self-audit: {exc}")
        except HarnessError as inner:
            ctx.record_decision(f"could not hand item {item_id} off after the halt: {inner}")
            raise exc
        return final, True
    except HarnessError:
        _record_self_audit(ctx, history)  # a usage stop or a block still leaves the evidence
        raise
    _record_self_audit(ctx, history)
    return final, False
