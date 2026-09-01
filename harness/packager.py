"""Builds the review package of HARNESS-SPEC §7.2 and promotes it into ``packages/``."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict
from pathlib import Path

from harness import __version__
from harness.clock import iso
from harness.clone import Lease
from harness.context import Context
from harness.errors import PackageError, WriteOutsideAllowedRoots
from harness.gates import run_command
from harness.redact import allowed_roots, write_redacted
from harness.stages.propose import parse_work_package

# Exactly the §7.2 entries. B73: the package directory contains these and nothing else.
PACKAGE_FILES: tuple[str, ...] = (
    "README.md",
    "DIAGNOSIS.md",
    "DECISIONS.md",
    "EVIDENCE.md",
    "ACCEPTANCE.md",
    "BASE",
    "manifest.json",
    "bundle.gitbundle",
    "transcript.jsonl",
)
PACKAGE_DIRS: tuple[str, ...] = ("patches",)
PACKAGE_ENTRIES: tuple[str, ...] = PACKAGE_FILES + PACKAGE_DIRS

# Gates that need a live database. §12 Q1 is unanswered, so when these are absent from a
# recorded sequence the omission is stated in EVIDENCE.md rather than passed over.
DATABASE_GATES: tuple[str, ...] = (
    "npx prisma generate",
    "bash scripts/check-prisma-drift.sh",
)

OMISSION_NOTE = (
    "> **Database gates omitted.** HARNESS-SPEC §12 question 1 — whether a local throwaway\n"
    "> Postgres counts as \"using secrets\" — is unanswered, so the gate sequence ran the\n"
    "> non-database subset. The gates listed below are the ones that actually ran. This\n"
    "> package does **not** claim full parity with the product repo's CI.\n"
    "> Gates not run: {names}.\n"
)

_TEXT_SUFFIXES = frozenset({".md", ".json", ".jsonl", ".patch", ".txt", ""})


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _read_json_list(path: Path) -> list[dict]:
    raw = _read_text(path).strip()
    if not raw:
        return []
    try:
        loaded = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if isinstance(loaded, list):
        return [row for row in loaded if isinstance(row, dict)]
    return []


def _fence(text: str) -> str:
    """Wrap verbatim output in a fence long enough that the output cannot break out."""
    body = text if text.endswith("\n") or text == "" else text + "\n"
    longest = 0
    for line in body.splitlines():
        stripped = line.strip()
        if stripped and set(stripped) == {"`"}:
            longest = max(longest, len(stripped))
    fence = "`" * max(3, longest + 1)
    return f"{fence}text\n{body}{fence}\n"


def _gate_section(title: str, gates: list[dict]) -> str:
    lines = [f"## {title}", ""]
    if not gates:
        lines.append("_No gate results were recorded for this phase._")
        lines.append("")
        return "\n".join(lines)
    for gate in gates:
        name = str(gate.get("name", "(unnamed gate)"))
        argv = gate.get("argv") or ()
        exit_code = gate.get("exit_code")
        stdout = str(gate.get("stdout_tail") or "")
        stderr = str(gate.get("stderr_tail") or "")
        verdict = "PASS" if exit_code == 0 else "FAIL"
        lines.append(f"### {name} — exit code {exit_code} ({verdict})")
        lines.append("")
        if argv:
            joined = " ".join(str(part) for part in argv)
            lines.append(f"argv: `{joined}`")
            lines.append("")
        lines.append("stdout (verbatim tail):")
        lines.append("")
        lines.append(_fence(stdout))
        lines.append("stderr (verbatim tail):")
        lines.append("")
        lines.append(_fence(stderr))
    return "\n".join(lines)


def _omission_note(gates: list[dict]) -> str:
    if not gates:
        return ""
    seen = {str(gate.get("name", "")) for gate in gates}
    missing = [name for name in DATABASE_GATES if name not in seen]
    if not missing:
        return ""
    return OMISSION_NOTE.format(names=", ".join(f"`{name}`" for name in missing))


def _all_green(gates: list[dict]) -> bool:
    return bool(gates) and all(gate.get("exit_code") == 0 for gate in gates)


def _failing_names(gates: list[dict]) -> list[str]:
    return [str(gate.get("name", "?")) for gate in gates if gate.get("exit_code") != 0]


def _collect_transcript(run_dir: Path) -> str:
    transcript_dir = run_dir / "transcript"
    if not transcript_dir.is_dir():
        return ""
    chunks: list[str] = []
    for path in sorted(transcript_dir.glob("*.jsonl")):
        text = _read_text(path)
        if not text:
            continue
        chunks.append(text if text.endswith("\n") else text + "\n")
    return "".join(chunks)


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "_None recorded._"


def _prune(package_dir: Path) -> None:
    """B73: remove anything in the package directory that §7.2 does not name."""
    for entry in package_dir.iterdir():
        if entry.name in PACKAGE_ENTRIES:
            continue
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)


def build(ctx: Context, item_id: int, lease: Lease, *, git_runner=None) -> Path:
    """Assemble ``runs/<run-id>/package/`` per §7.2 and return its path."""
    run_git = git_runner or run_command
    item = ctx.store.get_work_item(item_id)
    if item is None:
        raise PackageError(f"no work item {item_id}")

    base_sha = lease.base_sha or item.base_sha or ""
    branch = lease.branch or item.branch_name or ""
    if len(base_sha) != 40:
        raise PackageError(f"base sha for item {item_id} is not a 40-character sha: {base_sha!r}")
    if not branch:
        raise PackageError(f"no branch recorded for item {item_id}")
    if not item.spec_path or not Path(item.spec_path).is_file():
        raise PackageError(f"no spec file for item {item_id}: {item.spec_path!r}")

    run_dir = ctx.run_dir
    package_dir = run_dir / "package"
    if package_dir.exists():
        shutil.rmtree(package_dir, ignore_errors=True)
    package_dir.mkdir(parents=True, exist_ok=True)
    patches_dir = package_dir / "patches"
    patches_dir.mkdir(parents=True, exist_ok=True)

    spec_text = _read_text(Path(item.spec_path))
    pkg = parse_work_package(spec_text)

    prep = _read_json_list(run_dir / "gates" / "prepare.json")
    baseline = _read_json_list(run_dir / "gates" / "baseline.json")
    final = _read_json_list(run_dir / "gates" / "final.json")
    run_decisions = _read_text(run_dir / "DECISIONS.md")
    transcript = _collect_transcript(run_dir)

    gate_flags = {"F1": False, "F2": False, "F3": False, "F4": False, "F5": False}
    stored_flags = _read_text(run_dir / "fullsend_gate.json").strip()
    if stored_flags:
        try:
            loaded = json.loads(stored_flags)
        except (ValueError, TypeError):
            loaded = None
        if isinstance(loaded, dict):
            for key in gate_flags:
                gate_flags[key] = bool(loaded.get(key, False))

    stage_rows = [asdict(row) for row in ctx.store.list_stage_runs(work_item_id=item_id)]
    stages = [
        {
            "stage": row.get("stage"),
            "turns": row.get("turns"),
            "allowance_pct": row.get("allowance_pct"),
        }
        for row in stage_rows
    ]

    # --- patches (B75): format-patch against the exact base, so `git am` onto BASE works.
    clone = lease.path
    code, out, err = run_git(
        ["git", "format-patch", f"{base_sha}..HEAD", "-o", str(patches_dir.resolve())],
        clone,
    )
    if code != 0:
        raise PackageError(f"git format-patch failed ({code}): {(err or out).strip()}")
    patch_files = sorted(path.name for path in patches_dir.glob("*.patch"))
    patch_count = len(patch_files)

    # --- bundle (B76): the branch, fetchable with no remote at all.
    bundle_path = package_dir / "bundle.gitbundle"
    code, out, err = run_git(
        ["git", "bundle", "create", str(bundle_path.resolve()), branch],
        clone,
    )
    if code != 0:
        raise PackageError(f"git bundle create failed ({code}): {(err or out).strip()}")

    created_at = iso(ctx.clock.now())
    touched_paths = list(pkg.touched_paths)

    # --- BASE (B74): 40 chars and a newline, nothing else.
    write_redacted(package_dir / "BASE", base_sha + "\n")

    # --- EVIDENCE.md (B77): verbatim, both phases, exit codes, never summarised.
    evidence: list[str] = [
        "# Evidence",
        "",
        "Verbatim output of the product repository's own gate sequence, with exit codes.",
        "Nothing below is summarised, trimmed for readability, or re-worded. Tails are the",
        "last characters captured by the gate runner, as captured.",
        "",
        f"- Base commit: `{base_sha}`",
        f"- Branch: `{branch}`",
        f"- Captured: {created_at}",
        "",
    ]
    note = _omission_note(final or baseline)
    if note:
        evidence.append(note)
        evidence.append("")
    if prep:
        evidence.append(_gate_section("Preparation — dependency install, not a gate", prep))
        evidence.append("")
        evidence.append(
            "`npm ci` against the pinned lockfile so the gates run on an installed tree. It "
            "checks nothing and is not part of the gate sequence."
        )
        evidence.append("")
    evidence.append(_gate_section("Baseline — untouched tree at BASE", baseline))
    evidence.append("")
    evidence.append(
        "Any red in the baseline is pre-existing. It is not attributable to this change and\n"
        "was not used to justify loosening anything."
    )
    evidence.append("")
    evidence.append(_gate_section("Post-change — the branch as packaged", final))
    evidence.append("")
    evidence.append(
        "No gate was widened, skipped, given a longer timeout, or marked `continue-on-error`\n"
        "to reach green. A red the harness could not fix honestly is a blocked item, not a\n"
        "passed one."
    )
    evidence.append("")
    write_redacted(package_dir / "EVIDENCE.md", "\n".join(evidence))

    # --- DIAGNOSIS.md
    diagnosis: list[str] = ["# Diagnosis", ""]
    if pkg.diagnosis.strip():
        diagnosis.append(pkg.diagnosis.strip())
    else:
        diagnosis.append("_No diagnosis section was present in the work package._")
    diagnosis.append("")
    if pkg.issue.strip():
        diagnosis.extend(["## Issue", "", pkg.issue.strip(), ""])
    if pkg.approach.strip():
        diagnosis.extend(["## Approach", "", pkg.approach.strip(), ""])
    if pkg.risks.strip():
        diagnosis.extend(["## Risks", "", pkg.risks.strip(), ""])
    write_redacted(package_dir / "DIAGNOSIS.md", "\n".join(diagnosis))

    # --- DECISIONS.md
    condition_text = {
        "F1": "F1 — at least 3 independently describable slices",
        "F2": "F2 — at least 15 numbered behaviors",
        "F3": "F3 — no open questions",
        "F4": (
            "F4 — no path under prisma/, migrations/, backend/scripts/predeploy*, "
            ".github/workflows/"
        ),
        "F5": "F5 — fullsend enabled in config",
    }
    fullsend_taken = all(gate_flags.values())
    decisions: list[str] = [
        "# Decisions",
        "",
        "Every decision taken to produce this package, including the fullsend-gate outcome.",
        "",
        "## From the work package",
        "",
        _bullets(list(pkg.decisions)),
        "",
        "## Fullsend fitness gate (HARNESS-SPEC §5.9.3)",
        "",
        f"Path taken: **{'fullsend' if fullsend_taken else 'single agent'}**.",
        "",
        "| Condition | Held |",
        "|---|---|",
    ]
    for key in ("F1", "F2", "F3", "F4", "F5"):
        decisions.append(f"| {condition_text[key]} | {'yes' if gate_flags[key] else 'no'} |")
    decisions.extend(["", "## Recorded during the run", ""])
    decisions.append(run_decisions.strip() if run_decisions.strip() else "_Nothing recorded._")
    decisions.append("")
    write_redacted(package_dir / "DECISIONS.md", "\n".join(decisions))

    # --- ACCEPTANCE.md: every criterion, met or not, with the evidence for the verdict.
    criteria = list(pkg.acceptance)
    green = _all_green(final)
    failing = _failing_names(final)
    acceptance: list[str] = [
        "# Acceptance criteria",
        "",
        "Each criterion from the work package, marked met or not met, with the evidence the",
        "verdict rests on. A criterion is marked met only when the post-change gate sequence",
        "in `EVIDENCE.md` is fully green; anything else is reported as not met, with the",
        "failing gates named. The harness does not mark its own work met on assertion.",
        "",
    ]
    if not criteria:
        acceptance.append("_The work package listed no acceptance criteria._")
        acceptance.append("")
    else:
        if green:
            evidence_line = (
                "post-change gate sequence green in `EVIDENCE.md` "
                f"({len(final)} gates, every exit code 0)"
            )
        elif final:
            evidence_line = (
                "post-change gate sequence not green in `EVIDENCE.md` — failing: "
                + ", ".join(f"`{name}`" for name in failing)
            )
        else:
            evidence_line = "no post-change gate results were recorded in this run"
        for index, criterion in enumerate(criteria, start=1):
            mark = "met" if green else "NOT met"
            acceptance.append(f"{index}. **[{mark}]** {criterion}")
            acceptance.append(f"   - Evidence: {evidence_line}.")
        acceptance.append("")
        if not green:
            acceptance.append(
                "A reviewer should confirm each criterion by hand against the reconstructed"
                " tree; the harness declines to claim them while a gate is red or unrun."
            )
            acceptance.append("")
    if touched_paths:
        acceptance.append("## Touched paths declared by the work package")
        acceptance.append("")
        acceptance.append(_bullets(touched_paths))
        acceptance.append("")
    write_redacted(package_dir / "ACCEPTANCE.md", "\n".join(acceptance))

    # --- transcript.jsonl: always present, possibly empty.
    write_redacted(package_dir / "transcript.jsonl", transcript)

    # --- manifest.json: §7.2 schema, keys in that order, indent=2.
    manifest = {
        "schema": 1,
        "item_id": item.id,
        "external_ref": item.external_ref,
        "repo": ctx.config.repo,
        "base_sha": base_sha,
        "branch": branch,
        "created_at": created_at,
        "harness_version": __version__,
        "backend": ctx.config.backend,
        "fullsend": fullsend_taken,
        "fullsend_gate": {key: gate_flags[key] for key in ("F1", "F2", "F3", "F4", "F5")},
        "stages": stages,
        "gates": [
            {"name": str(gate.get("name", "")), "exit_code": gate.get("exit_code")}
            for gate in (final or baseline)
        ],
        "patch_count": patch_count,
        "touched_paths": touched_paths,
    }
    write_redacted(package_dir / "manifest.json", json.dumps(manifest, indent=2) + "\n")

    # --- README.md: the entry point a reviewer opens first.
    title = pkg.title.strip() or item.title
    readme: list[str] = [
        f"# Review package — {title}",
        "",
        f"Work item **{item.id}** (`{item.external_ref}`) on `{ctx.config.repo}`.",
        f"Produced by bright-bots-harness {__version__} on the `{ctx.config.backend}` backend",
        f"at {created_at}.",
        "",
        "## What this is",
        "",
        "A change proposed by an automated harness that holds no credentials and cannot push.",
        "Nothing here has touched GitHub beyond unauthenticated public reads. It is yours to",
        "accept, amend, or discard; applying it is a human action.",
        "",
        "## What changed",
        "",
        f"- Base commit: `{base_sha}`",
        f"- Branch: `{branch}`",
        f"- Patches: {patch_count}",
    ]
    for name in patch_files:
        readme.append(f"  - `patches/{name}`")
    if touched_paths:
        readme.append("- Paths the work package expected to change:")
        for path_name in touched_paths:
            readme.append(f"  - `{path_name}`")
    readme.extend(
        [
            "",
            "## How to verify",
            "",
            "Reconstruct the exact tree the harness had. This needs nothing but this directory",
            "and the public product repository:",
            "",
            "```bash",
            f"git clone https://github.com/{ctx.config.repo}.git r && cd r",
            'git checkout "$(cat ../BASE)"',
            "git am ../patches/*.patch",
            "```",
            "",
            "Or fetch the branch straight out of the bundle, with no network at all:",
            "",
            "```bash",
            f"git clone bundle.gitbundle -b {branch} r",
            "```",
            "",
            "Then read `EVIDENCE.md`, which carries the verbatim output of the repository's own",
            "gate sequence, baseline and post-change, with exit codes.",
            "",
            "## What is in here",
            "",
            "| File | Contents |",
            "|---|---|",
            "| `README.md` | This file |",
            "| `DIAGNOSIS.md` | What is wrong, with citations |",
            "| `DECISIONS.md` | Every decision taken, including the fullsend-gate outcome |",
            "| `EVIDENCE.md` | Verbatim gate output, baseline and post-change, with exit codes |",
            "| `ACCEPTANCE.md` | Each acceptance criterion, marked met or not, with evidence |",
            "| `BASE` | The 40-character base commit sha |",
            "| `manifest.json` | Machine-readable summary |",
            "| `patches/` | The patch series, applying cleanly to `BASE` |",
            "| `bundle.gitbundle` | The branch, fetchable without a remote |",
            "| `transcript.jsonl` | Full redacted model transcript |",
            "",
        ]
    )
    write_redacted(package_dir / "README.md", "\n".join(readme))

    _prune(package_dir)
    ctx.store.update_work_item(item_id, package_path=str(package_dir))
    ctx.store.append_event(
        item_id,
        "info",
        f"packaged item {item_id} into {package_dir} ({patch_count} patches)",
    )
    return package_dir


def _guard_binary_dest(dest: Path) -> None:
    """I-8 for the one file that cannot go through write_redacted: the git bundle."""
    roots = allowed_roots()
    if not roots:
        return
    resolved = dest.resolve()
    for root in roots:
        if resolved == root or root in resolved.parents:
            return
    raise WriteOutsideAllowedRoots(f"refusing to write outside the allowed roots: {dest}")


def _copy_entry(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.suffix.lower() in _TEXT_SUFFIXES:
        write_redacted(dest, _read_text(src))
        return
    _guard_binary_dest(dest)
    shutil.copy2(src, dest)


def archive(ctx: Context, item_id: int, *, with_transcript: bool) -> Path:
    """Promote a built package into ``packages/<item>-<yyyymmddThhmmssZ>/``.

    B78: everything is copied except ``transcript.jsonl``, which comes only when asked.
    Refuses any item that is not in state ``packaged``.
    """
    item = ctx.store.get_work_item(item_id)
    if item is None:
        raise PackageError(f"no work item {item_id}")
    if item.state != "packaged":
        raise PackageError(
            f"item {item_id} is in state {item.state!r}; archive requires state 'packaged'"
        )

    source = Path(item.package_path) if item.package_path else ctx.run_dir / "package"
    if not source.is_dir():
        raise PackageError(f"no package directory to archive for item {item_id}: {source}")

    stamp = iso(ctx.clock.now()).replace("-", "").replace(":", "")
    destination = ctx.config.packages_dir / f"{item_id}-{stamp}"
    # The stamp has one-second resolution; a second archive in the same second must not land
    # inside the first.
    suffix = 1
    while destination.exists():
        suffix += 1
        destination = ctx.config.packages_dir / f"{item_id}-{stamp}-{suffix}"
    destination.mkdir(parents=True, exist_ok=True)

    for name in PACKAGE_FILES:
        if name == "transcript.jsonl" and not with_transcript:
            continue
        entry = source / name
        if entry.is_file():
            _copy_entry(entry, destination / name)

    patches_src = source / "patches"
    (destination / "patches").mkdir(parents=True, exist_ok=True)
    if patches_src.is_dir():
        for patch in sorted(patches_src.glob("*.patch")):
            _copy_entry(patch, destination / "patches" / patch.name)

    ctx.store.append_event(item_id, "info", f"archived item {item_id} to {destination}")
    return destination
