"""B359-B385 (D70): the adversarial self-audit before delivery. B386 is in test_docs_drift.py.

The loop runs on the tests/test_stages.py rig:
`ScriptedRunner` places each model answer on a specific call, and `FakeTree` stands in for git.
The rig's clone is a plain directory, so every git operation the loop, the
handoff, the packager and deliver make goes to a model of git's own semantics, never to
whatever git the host happens to have. The tests that need git itself -- T13's diff, T20's
database, and B387, B388, B390 and B394, which drive the git that undoes what a model did --
use a real repository with an explicit identity and `core.autocrlf` off in the clone.

Every test drives the code and asserts what it left behind: the stage rows, the files under
`runs/`, the tree, the store, and the pull request body deliver builds.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import harness.__main__ as cli
import harness.gates as gates_mod
import harness.packager as packager_mod
import harness.stages.deliver as deliver_mod
import harness.stages.implement as implement_mod
from harness import prettier, priority
from harness.clone import Lease
from harness.errors import BudgetExhausted, GateFailed, Halted, HarnessError, RateLimited
from harness.gates import GateResult
from harness.governor import Governor
from harness.runner.base import RunResult
from harness.stages.deliver import build_pr_body, self_audit_block
from harness.stages.implement import implement
from harness.stages.package import package
from harness.stages.propose import propose
from harness.store import Store
from tests.test_stages import (
    GREEN,
    RED,
    ScriptedRunner,
    approved_item,
    proposable,
    stub_implement_side_effects,
)

BASE = "a" * 40  # FakeClones' base commit
IMPLEMENTED = "export const total = measure('dist/**/*.{js,mjs}');\n"
GOLDEN_DIR = Path(__file__).resolve().parent / "fixtures" / "deliver" / "pr_body_cap0"
RESET_AT = "2026-09-02T18:00:00Z"
TOKEN = "ghp_" + "Z9" * 18
BROKEN_LINT = [
    GateResult(
        name="npm run lint",
        argv=("npm", "run", "lint"),
        exit_code=2,
        stdout_tail="",
        stderr_tail="error  'total' is assigned a value but never used",
    )
]


# --------------------------------------------------------------------------------------------
# git, modelled
# --------------------------------------------------------------------------------------------


def _git_words(argv: list[str]) -> list[str]:
    """The subcommand and its arguments, past `git` and its global options."""
    words = list(argv[1:]) if argv and argv[0] == "git" else list(argv)
    while words:
        if words[0] == "-c":
            words = words[2:]
        elif words[0].startswith("--"):
            words = words[1:]
        else:
            break
    return words


class FakeTree:
    """Git's semantics, for the operations D70's loop and its neighbours use, on a directory.

    A commit is a snapshot of every file under the clone and the working tree is the directory
    itself. A path is tracked when the head snapshot holds it, so `reset --hard` leaves an
    untracked file where it is, as git does -- the reason `_discard_since` restores leftovers.
    """

    def __init__(self) -> None:
        self.snapshots: dict[str, dict[str, bytes]] = {BASE: {}}
        self.head = BASE
        self.branch = "refs/heads/harness/fix-816-x"
        self.commits: list[dict] = []
        self.unmodelled: list[list[str]] = []

    # -- reading ----------------------------------------------------------------------------

    @staticmethod
    def files(clone) -> dict[str, bytes]:
        root = Path(clone)
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file() and path.relative_to(root).parts[0] != ".git"
        }

    def full(self, sha: str) -> str:
        names = [key for key in self.snapshots if key.startswith(sha)]
        assert len(names) == 1, f"no single commit is named {sha!r}"
        return names[0]

    @staticmethod
    def _differ(a: dict, b: dict) -> list[str]:
        return sorted(p for p in set(a) | set(b) if a.get(p) != b.get(p))

    def committed_paths(self) -> set[str]:
        return {path for snapshot in self.snapshots.values() for path in snapshot}

    # -- the implement injectables, with production's signatures ------------------------------

    def changed_paths(self, clone, sha):
        """`prettier.all_changed_paths`: the working tree against `sha`, untracked included."""
        return self._differ(self.files(clone), self.snapshots[self.full(sha)])

    def commit(self, clone, message):
        """`implement._commit`: `git add -A` and commit; nothing to commit is no error."""
        files = self.files(clone)
        paths = self._differ(files, self.snapshots[self.head])
        if not paths:
            return
        sha = hashlib.sha1(f"{self.head}\n{message}\n{len(self.commits)}".encode()).hexdigest()
        self.snapshots[sha] = files
        self.commits.append({"sha": sha, "parent": self.head, "message": message, "paths": paths})
        self.head = sha

    def unified_diff(self, lease):
        """`implement._unified_diff`: `git diff <base>..HEAD`, committed changes only."""
        before, after = self.snapshots[self.full(lease.base_sha)], self.snapshots[self.head]
        lines: list[str] = []
        for path in self._differ(after, before):
            lines.append(f"diff --git a/{path} b/{path}")
            lines.extend("-" + line for line in before.get(path, b"").decode().splitlines())
            lines.extend("+" + line for line in after.get(path, b"").decode().splitlines())
        return "\n".join(lines) + "\n", False

    def reset_to(self, clone, sha):
        """`git reset --hard`: every tracked path to `sha`; untracked files stay."""
        target = self.snapshots[self.full(sha)]
        for path in set(self.snapshots[self.head]) | set(target):
            self._put(Path(clone), path, target.get(path))
        self.head = self.full(sha)

    def restore_paths(self, clone, sha, paths):
        """`implement._restore_paths`: each path as `sha` holds it, or gone when it does not."""
        target = self.snapshots[self.full(sha)]
        for path in paths:
            self._put(Path(clone), path, target.get(path))

    def head_state(self, clone):
        """`implement._head_state`: the branch HEAD names, and its full commit."""
        return f"{self.branch} {self.head}"

    def put_head(self, clone, state):
        """`implement._put_head`: HEAD and its branch back on a state; the tree untouched."""
        ref, _, sha = state.partition(" ")
        self.branch, self.head = ref, self.full(sha)

    def diff_lines(self, lease, changed):
        """`implement._diff_lines`: added and removed lines of the tree against the base."""
        before, now = self.snapshots[self.full(lease.base_sha)], self.files(lease.path)
        added: list[str] = []
        removed: list[str] = []
        for path in changed:
            old = before.get(path, b"").decode().splitlines()
            new = now.get(path, b"").decode().splitlines()
            added += [line for line in new if line not in old]
            removed += [line for line in old if line not in new]
        return added, removed

    @staticmethod
    def _put(root: Path, path: str, content: bytes | None) -> None:
        target = root / path
        if content is None:
            if target.is_file():
                target.unlink()
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    # -- `gates.run_command`, for the git that deliver, the handoff and the packager run ------

    def run_command(self, argv, cwd):
        words = _git_words(list(argv))
        if words[:1] == ["rev-parse"] and "HEAD" in words:
            return 0, self.head + "\n", ""
        if words[:2] == ["status", "--porcelain"]:
            changed = self._differ(self.files(cwd), self.snapshots[self.head])
            return 0, "".join(f" M {path}\n" for path in changed), ""
        if words[:1] == ["format-patch"] and "-o" in words:
            out = Path(words[words.index("-o") + 1])
            chain, sha = [], self.head
            while sha != BASE:
                commit = next(c for c in self.commits if c["sha"] == sha)
                chain.insert(0, commit)
                sha = commit["parent"]
            for number, commit in enumerate(chain, start=1):
                body = f"From {commit['sha']}\nSubject: {commit['message'].splitlines()[0]}\n"
                (out / f"{number:04d}-change.patch").write_text(body, encoding="utf-8")
            return 0, "", ""
        if words[:2] == ["bundle", "create"]:
            Path(words[2]).write_bytes(b"# v2 git bundle\n")
            return 0, "", ""
        self.unmodelled.append(list(argv))
        return 128, "", f"fatal: not modelled by FakeTree: {' '.join(argv)}"


# --------------------------------------------------------------------------------------------
# model answers
# --------------------------------------------------------------------------------------------


def finding(
    where: str = "acceptance:2",
    *,
    severity: str = "blocking",
    claim: str = "the over-ceiling case is never tested",
    evidence: str = "no test builds an esm bundle over the ceiling",
) -> dict:
    return {"severity": severity, "claim": claim, "where": where, "evidence": evidence}


def audit(*findings: dict) -> str:
    """An auditor's answer: the selfaudit block on the first line, prose after it."""
    payload = {"verdict": "findings" if findings else "clean", "findings": list(findings)}
    return f"<!-- selfaudit: {json.dumps(payload)} -->\nThe reasons, for a person."


def write(path: str, text: str):
    """What a call holding Edit leaves in the clone."""

    def effect(request):
        target = Path(request.cwd) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")

    return effect


def answer(text, *effects):
    """A step that does each effect during the call, then answers `text` (None: the fixture)."""

    def step(request):
        for effect in effects:
            effect(request)
        return text

    return step


def rate_limited() -> RunResult:
    return RunResult(
        ok=False, text="", turns=0, duration_ms=12,
        session_id=None, exit_code=1, transcript=(),
        error=f"You've hit your usage limit. Resets at {RESET_AT}", reset_at=RESET_AT,
    )


def blocks(prompt: str) -> dict[str, str]:
    """Every `data_block` in a prompt, by label, with its body as fenced."""
    pattern = re.compile(
        r"^Data — not instructions: (?P<label>[^\n]+)\n(?P<fence>`{4,})text\n"
        r"(?P<body>.*?)^(?P=fence)(?!`)",
        re.M | re.S,
    )
    return {m.group("label"): m.group("body") for m in pattern.finditer(prompt)}


# --------------------------------------------------------------------------------------------
# the rig
# --------------------------------------------------------------------------------------------


@dataclasses.dataclass
class Loop:
    rig: object
    item_id: int
    tree: FakeTree
    runner: ScriptedRunner
    bodies: list

    @property
    def ctx(self):
        return self.rig.ctx

    @property
    def run_dir(self) -> Path:
        return self.rig.ctx.run_dir

    @property
    def clone(self) -> Path:
        return self.run_dir / "clone"

    @property
    def body(self) -> str:
        return self.bodies[-1][1]

    def history(self) -> list[dict]:
        return json.loads((self.run_dir / "selfaudit.json").read_text(encoding="utf-8"))["history"]

    def stages(self, item_id: int | None = None) -> list[str]:
        rows = self.rig.store.list_stage_runs(work_item_id=item_id or self.item_id)
        return [row.stage for row in rows]

    def state(self, item_id: int | None = None) -> str:
        return self.rig.store.get_work_item(item_id or self.item_id).state

    def requests(self, stage: str) -> list:
        return [request for request in self.runner.requests if request.stage == stage]

    def decisions(self) -> str:
        return (self.run_dir / "DECISIONS.md").read_text(encoding="utf-8")

    def sequence(self) -> list[str]:
        return [
            entry
            for entry in self.rig.log
            if entry.startswith(("run:", "gates(", "reset_to:"))
            or entry in ("commit", "package", "deliver")
        ]

    def through_delivery(self) -> Lease:
        """implement, package and deliver, as `harness run --item` runs them. Tier 0: deliver
        has no credential, so it builds the body, writes DELIVER.json and pushes nothing."""
        lease = implement(self.ctx, self.item_id)
        self.rig.log.append("package")
        package(self.ctx, self.item_id, lease)
        self.rig.log.append("deliver")
        assert deliver_mod.deliver(self.ctx, self.item_id) == ""
        return lease


def loop_rig(
    tmp_path,
    monkeypatch,
    *,
    script: dict | None = None,
    gates=lambda baseline: GREEN,
    cap: int | None = None,
    can_write: bool = False,
    via: str | None = None,
    unified_diff=None,
) -> Loop:
    rig, item_id = proposable(tmp_path, via=via or "requested")
    propose(rig.ctx, item_id)
    approved_item(rig, item_id)
    rig.log.clear()
    rig.clones.released.clear()

    rig.gh.can_write = can_write
    rig.gh.pushed = []

    def push_branch(clone, branch, *, remote_repo, force=False, git_runner=None):
        rig.gh.pushed.append((branch, remote_repo, bool(force)))

    rig.gh.push_branch = push_branch

    steps = {"implement": [answer(None, write("src/lib/bundle.ts", IMPLEMENTED))]}
    steps.update(script or {})
    runner = ScriptedRunner(rig.runner.inner, steps)
    rig.runner.inner = runner

    tree = FakeTree()

    def gate_runner(clone, *, baseline, runner=None):
        rig.log.append(f"gates(baseline={baseline})")
        return list(gates(baseline))

    stub_implement_side_effects(
        monkeypatch,
        rig.log,
        gate_runner=gate_runner,
        changed=tree.changed_paths,
        tip=None,  # production's TIP_SHA, `deliver._tip_sha`, on the modelled `rev-parse`
        commit=tree.commit,
        unified_diff=unified_diff or tree.unified_diff,
        reset_to=tree.reset_to,
        restore_paths=tree.restore_paths,
        head_state=tree.head_state,
        put_head=tree.put_head,
    )
    monkeypatch.setattr(implement_mod, "DIFF_LINES", tree.diff_lines)
    monkeypatch.setattr(gates_mod, "run_command", tree.run_command)
    monkeypatch.setattr(packager_mod, "run_command", tree.run_command)

    bodies: list = []
    real_body = deliver_mod.build_pr_body

    def spy(package_dir, **kwargs):
        body = real_body(package_dir, **kwargs)
        bodies.append((kwargs, body))
        return body

    monkeypatch.setattr(deliver_mod, "build_pr_body", spy)
    if cap is not None:
        rig.ctx.config = dataclasses.replace(rig.ctx.config, max_self_audit_cycles=cap)
    return Loop(rig, item_id, tree, runner, bodies)


def engage_halt(loop_ref: dict):
    def effect(request=None):
        loop_ref["loop"].ctx.config.halt_file.write_text("", encoding="utf-8")

    return effect


def unpublishable_is_clear(monkeypatch):
    """B301/B315 test the handoff's `.github/` walk on a real clone; this rig has none, so the
    walk is answered as clear and the push goes to the recording client."""
    monkeypatch.setattr(deliver_mod, "_unpublishable", lambda ctx, clone, branch: ([], ""))


def assert_parked_with_a_carry(loop: Loop, *, can_write: bool) -> None:
    """approved, carried, the work kept -- never released, never deleted."""
    assert loop.state() == "approved"
    assert loop.ctx.ledger.carry_issue() == loop.item_id
    handoff = loop.run_dir / "HANDOFF.md"
    assert handoff.is_file()
    assert "halted during self-audit" in handoff.read_text(encoding="utf-8")
    assert loop.rig.clones.released == []
    assert (loop.clone / "src" / "lib" / "bundle.ts").read_text(encoding="utf-8") == IMPLEMENTED
    branch = loop.rig.store.get_work_item(loop.item_id).branch_name
    assert loop.rig.gh.pushed == ([(branch, "", False)] if can_write else [])


# --------------------------------------------------------------------------------------------
# T1-T10: the loop
# --------------------------------------------------------------------------------------------


def test_B359_a_clean_first_audit_is_one_call_and_the_item_goes_on_to_package(
    tmp_path, monkeypatch
):
    loop = loop_rig(tmp_path, monkeypatch)

    loop.through_delivery()

    assert loop.sequence() == [
        "gates(baseline=True)", "run:implement", "commit", "gates(baseline=False)",
        "run:selfaudit", "package", "deliver",
    ]
    assert loop.stages().count("selfaudit") == 1
    assert "selfaudit_fix" not in loop.stages()
    tip = loop.tree.head[:12]
    assert [(e["tip"], e["status"], e["findings"]) for e in loop.history()] == [(tip, "ok", [])]
    assert loop.state() == "packaged"
    assert f"**Self-audit at `{tip}`: clean.**" in loop.body


def test_B360_a_gate_green_at_baseline_and_red_after_the_change_blocks_before_any_audit(
    tmp_path, monkeypatch
):
    loop = loop_rig(tmp_path, monkeypatch, gates=lambda baseline: GREEN if baseline else RED)

    with pytest.raises(GateFailed):
        implement(loop.ctx, loop.item_id)

    assert loop.state() == "blocked"
    assert "selfaudit" not in loop.stages() and "selfaudit_fix" not in loop.stages()
    assert loop.requests("selfaudit") == []
    assert not (loop.run_dir / "selfaudit.json").exists()


def test_B361_a_gate_red_at_baseline_does_not_block_and_the_auditor_is_told_it_was_already_red(
    tmp_path, monkeypatch
):
    loop = loop_rig(tmp_path, monkeypatch, gates=lambda baseline: GREEN + RED)

    loop.through_delivery()

    [request] = loop.requests("selfaudit")
    summary = blocks(request.prompt)["the gate results after the change"]
    assert "- npm run test:unit: exit code 1, red, pre-existing" in summary
    assert "- npm run lint: exit code 0, green" in summary
    assert loop.history()[0]["status"] == "ok"
    assert loop.state() == "packaged"


def test_B362_findings_get_a_fix_pass_the_gates_again_and_a_clean_audit_before_package(
    tmp_path, monkeypatch
):
    loop = loop_rig(
        tmp_path,
        monkeypatch,
        script={
            "selfaudit": [audit(finding()), audit()],
            "selfaudit_fix": [
                answer(None, write("src/lib/bundle.test.ts", "it('fails over the ceiling');\n"))
            ],
        },
    )

    loop.through_delivery()

    assert loop.sequence() == [
        "gates(baseline=True)", "run:implement", "commit", "gates(baseline=False)",
        "run:selfaudit", "run:selfaudit_fix", "commit", "gates(baseline=False)",
        "run:selfaudit", "package", "deliver",
    ]
    implemented, fixed = loop.tree.commits
    assert fixed["paths"] == ["src/lib/bundle.test.ts"]
    assert fixed["message"].splitlines()[0].endswith("address self-audit findings for issue:816")
    first, second = loop.history()
    assert first["tip"] == implemented["sha"][:12] and second["tip"] == fixed["sha"][:12]
    assert first["outcome"] == "fix pass committed; no new gate failures"
    assert second["status"] == "ok" and second["findings"] == []
    second_diff = blocks(loop.requests("selfaudit")[1].prompt)
    assert any("src/lib/bundle.test.ts" in body for body in second_diff.values())
    assert loop.state() == "packaged"
    assert f"**Self-audit at `{fixed['sha'][:12]}`: clean.**" in loop.body


def test_B363_three_distinct_findings_at_cap_three_are_carried_and_the_item_still_delivers(
    tmp_path, monkeypatch
):
    loop = loop_rig(
        tmp_path,
        monkeypatch,
        script={
            "selfaudit": [
                audit(finding("acceptance:1", claim="the under-ceiling case is untested")),
                audit(finding("acceptance:2", claim="the over-ceiling case is untested")),
                audit(finding("behavior:3", claim="the measured file list is never printed")),
            ],
            "selfaudit_fix": [
                answer(None, write("src/lib/under.test.ts", "it('passes under');\n")),
                answer(None, write("src/lib/over.test.ts", "it('fails over');\n")),
            ],
        },
    )

    loop.through_delivery()

    assert len(loop.requests("selfaudit")) == 3
    assert len(loop.requests("selfaudit_fix")) == 2
    history = loop.history()
    assert [e["outcome"] for e in history] == [
        "fix pass committed; no new gate failures",
        "fix pass committed; no new gate failures",
        "cap reached; findings carried",
    ]
    assert [f["claim"] for f in history[-1]["findings"]] == [
        "the measured file list is never printed"
    ]
    assert loop.state() == "packaged"
    tip = loop.tree.head[:12]
    assert (
        f"**Self-audit at `{tip}`: 1 blocking finding (+0 notes)** — a model reviewing its own "
        "diff; an opinion, not a gate result."
    ) in loop.body
    assert "- `behavior:3` — the measured file list is never printed" in loop.body


def test_B364_the_same_findings_twice_stop_the_loop_after_one_fix_pass(tmp_path, monkeypatch):
    loop = loop_rig(
        tmp_path,
        monkeypatch,
        script={
            "selfaudit": [audit(finding()), audit(finding()), audit(finding())],
            "selfaudit_fix": [
                answer(None, write("src/lib/bundle.test.ts", "it('fails over');\n")),
                answer(None, write("src/lib/bundle.test.ts", "it('fails over, again');\n")),
            ],
        },
    )

    loop.through_delivery()

    assert len(loop.requests("selfaudit")) == 2
    assert len(loop.requests("selfaudit_fix")) == 1
    assert [e["outcome"] for e in loop.history()] == [
        "fix pass committed; no new gate failures",
        "repeated finding; stopped",
    ]
    assert loop.state() == "packaged"


def test_B365_cap_zero_makes_no_call_writes_nothing_and_leaves_the_pr_body_byte_identical(
    tmp_path, monkeypatch
):
    loop = loop_rig(tmp_path, monkeypatch, cap=0)

    loop.through_delivery()

    assert not {"selfaudit", "selfaudit_fix"} & set(loop.stages())
    assert loop.requests("selfaudit") == [] and loop.requests("selfaudit_fix") == []
    assert not (loop.run_dir / "selfaudit.json").exists()
    assert not (loop.run_dir / "transcript" / "selfaudit.jsonl").exists()
    assert "self-audit" not in loop.decisions().lower()
    kwargs, _ = loop.bodies[-1]
    assert kwargs["self_audit"] is None

    golden = json.loads((GOLDEN_DIR / "kwargs.json").read_bytes().decode("utf-8"))
    golden["config"] = SimpleNamespace(**golden["config"])
    golden["trusted"] = tuple(golden["trusted"])
    body = build_pr_body(GOLDEN_DIR / "package", **golden, self_audit=kwargs["self_audit"])
    assert body.encode("utf-8") == (GOLDEN_DIR.parent / "pr_body_cap0.md").read_bytes()


def test_B366_notes_alone_get_no_fix_pass_are_counted_in_the_body_and_listed_in_the_package(
    tmp_path, monkeypatch
):
    notes = [
        finding("src/lib/bundle.ts:1", severity="note", claim="a sourcemap could be counted"),
        finding("acceptance:1", severity="note", claim="the message could name the ceiling"),
    ]
    loop = loop_rig(tmp_path, monkeypatch, script={"selfaudit": [audit(*notes)]})

    loop.through_delivery()

    assert loop.requests("selfaudit_fix") == []
    tip = loop.tree.head[:12]
    assert f"**Self-audit at `{tip}`: no blocking findings, 2 notes.**" in loop.body
    package_decisions = (loop.run_dir / "package" / "DECISIONS.md").read_text(encoding="utf-8")
    for note in notes:
        assert note["claim"] not in loop.body
        assert note["claim"] in package_decisions
    assert loop.state() == "packaged"


def test_B367_a_fix_pass_that_changes_nothing_stops_the_loop_without_blocking(
    tmp_path, monkeypatch
):
    loop = loop_rig(
        tmp_path,
        monkeypatch,
        script={
            "selfaudit": [audit(finding())],
            "selfaudit_fix": ["The finding does not hold: acceptance 2 is tested at line 40."],
        },
    )

    loop.through_delivery()

    assert len(loop.requests("selfaudit")) == 1
    assert len(loop.tree.commits) == 1
    assert [e["outcome"] for e in loop.history()] == ["fix pass changed nothing"]
    assert loop.state() == "packaged"
    assert "- Cycle 1: fix pass changed nothing." in loop.body


def test_B368_a_fix_pass_that_breaks_a_gate_is_reverted_and_the_item_still_delivers(
    tmp_path, monkeypatch
):
    post: list[int] = []

    def gates(baseline):
        if baseline:
            return GREEN
        post.append(1)
        return GREEN if len(post) == 1 else BROKEN_LINT

    loop = loop_rig(
        tmp_path,
        monkeypatch,
        gates=gates,
        script={
            "selfaudit": [audit(finding())],
            "selfaudit_fix": [answer(None, write("src/lib/bundle.ts", "let total;\n"))],
        },
    )

    loop.through_delivery()

    [entry] = loop.history()
    pre_fix = entry["tip"]
    assert f"reset_to:{pre_fix}" in loop.rig.log
    assert loop.tree.head[:12] == pre_fix
    assert (loop.clone / "src" / "lib" / "bundle.ts").read_text(encoding="utf-8") == IMPLEMENTED
    final = json.loads((loop.run_dir / "gates" / "final.json").read_text(encoding="utf-8"))
    assert [(row["name"], row["exit_code"]) for row in final] == [("npm run lint", 0)]
    assert entry["outcome"] == "fix pass reverted: broke npm run lint"
    assert loop.state() == "packaged"
    assert f"**Self-audit at `{pre_fix}`: 1 blocking finding" in loop.body


# --------------------------------------------------------------------------------------------
# T11-T21: the boundary
# --------------------------------------------------------------------------------------------


def test_B369_the_auditor_reads_and_the_fix_pass_holds_implements_tools(tmp_path, monkeypatch):
    loop = loop_rig(
        tmp_path,
        monkeypatch,
        script={
            "selfaudit": [audit(finding())],
            "selfaudit_fix": [answer(None, write("src/lib/bundle.test.ts", "it('fails');\n"))],
        },
    )

    implement(loop.ctx, loop.item_id)

    audits = loop.requests("selfaudit")
    assert len(audits) == 2
    for request in audits:
        assert request.allowed_tools == ("Read", "Glob", "Grep", "Bash")
        assert request.disallowed_tools == ("Edit", "Write", "WebFetch", "WebSearch")
    [implementation] = loop.requests("implement")
    [fix] = loop.requests("selfaudit_fix")
    assert fix.allowed_tools == implementation.allowed_tools
    assert fix.allowed_tools == ("Read", "Edit", "Write", "Bash", "Glob", "Grep")
    assert fix.disallowed_tools == implementation.disallowed_tools == ("WebFetch", "WebSearch")


def test_B370_the_auditor_is_given_the_package_the_paths_the_diff_and_the_gates_as_data(
    tmp_path, monkeypatch
):
    loop = loop_rig(tmp_path, monkeypatch)

    implement(loop.ctx, loop.item_id)

    [request] = loop.requests("selfaudit")
    found = blocks(request.prompt)
    spec = found["the approved work package"]
    for criterion in (
        "The check passes on an esm build that is genuinely under the ceiling",
        "The check fails on an esm build that is genuinely over the ceiling",
    ):
        assert criterion in spec
        assert request.prompt.count(criterion) == 1
    for number in range(1, 16):
        assert f"N{number}. behavior number {number} is observable and testable" in spec
    assert found["the work package's touched paths"].splitlines() == [
        "scripts/check-bundle-size.js",
        "src/lib/bundle.ts",
    ]
    diff = found[f"git diff {BASE[:12]}..{loop.tree.head[:12]}"]
    assert "+" + IMPLEMENTED.strip() in diff
    assert request.prompt.count(IMPLEMENTED.strip()) == 1
    assert found["the gate results after the change"] == "- npm run lint: exit code 0, green\n"


def _git(cwd: Path, *args: str) -> str:
    """Real git, with an explicit identity and nothing from the host's configuration."""
    argv = [
        "git", "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false",
        "-c", "user.name=Self Audit Test", "-c", "user.email=selfaudit@example.invalid",
        "-c", "init.defaultBranch=main", *args,
    ]
    proc = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, f"{' '.join(argv)}: {proc.stderr}"
    return proc.stdout.strip()


def test_B371_a_cut_diff_is_marked_and_a_finding_on_a_file_past_the_cut_still_counts(
    tmp_path, monkeypatch
):
    repo = tmp_path / "real"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "README.md").write_text("base\n", encoding="utf-8", newline="\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-verify", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    large = "".join(f"export const line{n} = {n};\n" for n in range(400))
    (repo / "src").mkdir()
    (repo / "src" / "a-large.ts").write_text(large, encoding="utf-8", newline="\n")
    cut = repo / "src" / "z-cut.ts"
    cut.write_text("export const cut = 1;\n", encoding="utf-8", newline="\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-verify", "-m", "change")
    monkeypatch.setattr(implement_mod, "MAX_DIFF_CHARS", 2000)

    text, truncated = implement_mod._unified_diff(
        Lease(run_id="real", path=repo, base_sha=base, branch="main")
    )

    assert truncated
    assert "[diff truncated here: 2000 of " in text
    assert "z-cut.ts" not in text
    assert len(text) < 2000 + 200

    loop = loop_rig(
        tmp_path,
        monkeypatch,
        unified_diff=lambda lease: (text, truncated),
        script={
            "implement": [
                answer(None, write("src/a-large.ts", large), write("src/z-cut.ts", "x = 1;\n"))
            ],
            "selfaudit": [
                audit(finding("src/z-cut.ts", severity="note", claim="nothing reads the flag"))
            ],
        },
    )

    implement(loop.ctx, loop.item_id)

    [request] = loop.requests("selfaudit")
    assert "was cut at 2000 characters" in request.prompt
    found = blocks(request.prompt)
    [diff] = [body for label, body in found.items() if label.startswith("git diff")]
    assert "[diff truncated here: 2000 of " in diff
    assert diff.split("Every changed path:\n", 1)[1].split() == ["src/a-large.ts", "src/z-cut.ts"]
    assert [f["where"] for f in loop.history()[0]["findings"]] == ["src/z-cut.ts"]


def test_B372_the_fix_pass_is_given_every_blocking_findings_claim_and_where_as_data(
    tmp_path, monkeypatch
):
    found = [
        finding("acceptance:2", claim="the over-ceiling case is never tested"),
        finding("src/lib/bundle.ts:1", claim="the glob still misses .cjs output"),
        finding("behavior:4", severity="note", claim="a note the fix pass is never given"),
    ]
    loop = loop_rig(
        tmp_path,
        monkeypatch,
        script={
            "selfaudit": [audit(*found)],
            "selfaudit_fix": [answer(None, write("src/lib/bundle.test.ts", "it('fails');\n"))],
        },
    )

    implement(loop.ctx, loop.item_id)

    [fix] = loop.requests("selfaudit_fix")
    given = blocks(fix.prompt)["the self-audit's blocking findings"]
    for item in found[:2]:
        assert item["claim"] in given and item["where"] in given
        assert fix.prompt.count(item["claim"]) == 1
    assert "a note the fix pass is never given" not in fix.prompt


def test_B373_a_fix_pass_that_touches_github_blocks_the_item_and_pushes_nothing(
    tmp_path, monkeypatch
):
    loop = loop_rig(
        tmp_path,
        monkeypatch,
        can_write=True,
        script={
            "selfaudit": [audit(finding())],
            "selfaudit_fix": [answer(None, write(".github/workflows/ci.yml", "on: push\n"))],
        },
    )

    with pytest.raises(HarnessError, match="forbidden diff"):
        implement(loop.ctx, loop.item_id)

    assert loop.state() == "blocked"
    assert loop.rig.gh.pushed == []
    assert len(loop.tree.commits) == 1
    assert ".github/workflows/ci.yml" not in loop.tree.committed_paths()
    assert [keep for _lease, keep in loop.rig.clones.released] == [True]
    assert loop.history()[0]["findings"]


@pytest.mark.parametrize("can_write", [False, True], ids=["no-credential", "credential"])
def test_B374_a_halt_before_the_audit_call_parks_the_item_with_its_work(
    tmp_path, monkeypatch, can_write
):
    ref: dict = {}
    halt = engage_halt(ref)
    unpublishable_is_clear(monkeypatch)

    def diff_then_halt(lease):
        result = ref["loop"].tree.unified_diff(lease)
        halt()
        return result

    loop = ref["loop"] = loop_rig(
        tmp_path, monkeypatch, can_write=can_write, unified_diff=diff_then_halt
    )

    lease = implement(loop.ctx, loop.item_id)

    assert lease.branch
    assert "selfaudit" not in loop.stages()
    assert_parked_with_a_carry(loop, can_write=can_write)
    assert loop.history()[-1]["status"] == "not run: halted"


@pytest.mark.parametrize("can_write", [False, True], ids=["no-credential", "credential"])
def test_B374_a_halt_on_the_fix_pass_call_parks_the_item_with_its_work(
    tmp_path, monkeypatch, can_write
):
    ref: dict = {}
    unpublishable_is_clear(monkeypatch)
    loop = ref["loop"] = loop_rig(
        tmp_path,
        monkeypatch,
        can_write=can_write,
        script={
            "selfaudit": [answer(audit(finding()), engage_halt(ref))],
            "selfaudit_fix": [answer(None, write("src/lib/bundle.test.ts", "it('fails');\n"))],
        },
    )

    implement(loop.ctx, loop.item_id)

    assert "selfaudit_fix" not in loop.stages()
    assert loop.history()[0]["findings"][0]["where"] == "acceptance:2"
    assert len(loop.tree.commits) == 1
    assert_parked_with_a_carry(loop, can_write=can_write)
    assert loop.history()[-1]["outcome"] == "halted before the fix pass; findings carried"


def test_B374_a_commanded_halt_parks_the_item_and_the_run_stops_before_packaging(
    tmp_path, monkeypatch
):
    ref: dict = {}

    def command_halt(request):
        ref["loop"].ctx.ledger.request_halt("jgoetzmann", "stop for the night", RESET_AT)

    loop = ref["loop"] = loop_rig(
        tmp_path,
        monkeypatch,
        script={
            "selfaudit": [answer(audit(finding()), command_halt)],
            "selfaudit_fix": [answer(None, write("src/lib/bundle.test.ts", "it('fails');\n"))],
        },
    )

    lease = implement(loop.ctx, loop.item_id)

    assert not loop.ctx.config.halt_file.exists()
    assert "selfaudit_fix" not in loop.stages()
    assert_parked_with_a_carry(loop, can_write=False)
    assert loop.history()[-1]["outcome"] == "halted before the fix pass; findings carried"
    with pytest.raises(Halted):
        package(loop.ctx, loop.item_id, lease)
    assert not (loop.run_dir / "package").exists()
    assert loop.state() == "approved"


def test_B375_a_usage_stop_on_the_audit_call_hands_off_and_starts_no_other_item(
    tmp_path, monkeypatch, capsys
):
    loop = loop_rig(tmp_path, monkeypatch)
    second = loop.rig.store.create_work_item(
        kind="issue", external_ref="issue:817", title="a second approved item"
    )
    propose(loop.ctx, second)
    approved_item(loop.rig, second)
    loop.rig.log.clear()
    stop = "weekly subscription usage is 91%, at or above the 90% stop"

    real_authorize = Governor.authorize

    def authorize(self, work_item_id, stage):
        if stage == "selfaudit":
            raise BudgetExhausted(stop)
        return real_authorize(self, work_item_id, stage)

    monkeypatch.setattr(Governor, "authorize", authorize)
    real_build = cli.build_context

    def wired(config, *, run_id=None, **_ignored):
        return real_build(
            config, run_id=run_id, runner=loop.rig.runner, gh=loop.rig.gh,
            clock=loop.ctx.clock, store=loop.rig.store, clones=loop.rig.clones,
        )

    monkeypatch.setattr(cli, "build_context", wired)
    monkeypatch.chdir(tmp_path)
    capsys.readouterr()

    rc = cli.main(["run"])

    out = capsys.readouterr().out
    assert rc == 0, out
    assert loop.state() == "approved"
    ledger = json.loads((tmp_path / "state" / "ledger.json").read_text(encoding="utf-8"))
    assert ledger["window"]["carry"]["issue"] == loop.item_id
    assert stop in (loop.run_dir / "HANDOFF.md").read_text(encoding="utf-8")
    assert "implement" not in loop.stages(second)
    assert loop.state(second) == "approved"
    assert [entry for entry in loop.rig.log if entry == "run:implement"] == ["run:implement"]


def test_B376_a_rate_limited_audit_call_returns_the_item_to_approved_with_the_reason(
    tmp_path, monkeypatch
):
    loop = loop_rig(tmp_path, monkeypatch, script={"selfaudit": [rate_limited()]})

    with pytest.raises(RateLimited):
        implement(loop.ctx, loop.item_id)

    assert loop.state() == "approved"
    messages = [event["message"] for event in loop.rig.store.events(loop.item_id)]
    assert (
        f"transition implementing -> approved: rate limited until {RESET_AT}; returned from "
        "implementing to approved so selfaudit is retried after the reset"
    ) in messages
    assert loop.history()[-1]["status"] == "not run: rate limited"


def test_B376_a_rate_limited_fix_pass_discards_its_edits_and_returns_the_item_to_approved(
    tmp_path, monkeypatch
):
    loop = loop_rig(
        tmp_path,
        monkeypatch,
        script={
            "selfaudit": [audit(finding())],
            "selfaudit_fix": [answer(rate_limited(), write("src/lib/half-done.ts", "x\n"))],
        },
    )

    with pytest.raises(RateLimited):
        implement(loop.ctx, loop.item_id)

    assert loop.state() == "approved"
    messages = [event["message"] for event in loop.rig.store.events(loop.item_id)]
    assert any("so selfaudit_fix is retried after the reset" in m for m in messages)
    assert not (loop.clone / "src" / "lib" / "half-done.ts").exists()
    assert loop.tree.changed_paths(loop.clone, loop.tree.head) == []
    assert loop.history()[-1]["outcome"] == "fix pass rate limited; its changes were discarded"


@pytest.mark.parametrize("via", ["requested", "assigned", "suggested"])
@pytest.mark.parametrize("stage", ["selfaudit", "selfaudit_fix"])
def test_B377_both_self_audit_stages_class_as_unblock_whatever_the_route(stage, via):
    assert priority.class_of(stage, via=via) == "unblock"


def test_B377_a_suggested_item_still_gets_its_fix_pass_while_requested_work_waits(
    tmp_path, monkeypatch
):
    ref: dict = {}

    def somebody_asks(request):
        ref["loop"].rig.store.create_work_item(
            kind="issue", external_ref="issue:900", title="somebody asked for this"
        )

    loop = ref["loop"] = loop_rig(
        tmp_path,
        monkeypatch,
        via="suggested",
        script={
            "selfaudit": [answer(audit(finding()), somebody_asks)],
            "selfaudit_fix": [answer(None, write("src/lib/bundle.test.ts", "it('fails');\n"))],
        },
    )

    implement(loop.ctx, loop.item_id)

    # The refusal is real: suggested work is refused while the new item waits.
    assert priority.admit(
        priority.class_of("implement", via="suggested"),
        store=loop.rig.store, ledger=loop.ctx.ledger, config=loop.ctx.config,
    )
    assert loop.stages().count("selfaudit_fix") == 1
    assert len(loop.tree.commits) == 2
    assert loop.history()[0]["outcome"] == "fix pass committed; no new gate failures"


#: `stage_run` exactly as layout 2 created it, before D70 (main at a8e4903).
LAYOUT_2_STAGE_RUN = """
CREATE TABLE stage_run (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  work_item_id    INTEGER NOT NULL REFERENCES work_item(id),
  stage           TEXT    NOT NULL CHECK (stage IN
                    ('discover','propose','implement','package','deliver','revise','decompose')),
  backend         TEXT    NOT NULL,
  status          TEXT    NOT NULL CHECK (status IN
                    ('running','ok','failed','halted','budget_exhausted','timeout')),
  started_at      TEXT    NOT NULL,
  ended_at        TEXT,
  turns           INTEGER,
  allowance_pct   REAL,
  cost_usd        REAL,
  exit_reason     TEXT,
  transcript_path TEXT
);
"""


def _user_version(db: Path) -> int:
    conn = sqlite3.connect(db)
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def test_B378_a_fresh_database_records_both_self_audit_stages(tmp_path, frozen_clock):
    store = Store(tmp_path / "fresh.db", frozen_clock)
    store.migrate()
    item = store.create_work_item(kind="issue", external_ref="issue:1", title="t")

    for stage in ("selfaudit", "selfaudit_fix"):
        store.start_stage_run(item, stage, "fake")

    assert [row.stage for row in store.list_stage_runs(work_item_id=item)] == [
        "selfaudit", "selfaudit_fix"
    ]
    store.close()
    assert _user_version(tmp_path / "fresh.db") == 3


def test_B378_a_layout_2_database_migrates_and_keeps_every_row(tmp_path, frozen_clock):
    db = tmp_path / "layout2.db"
    store = Store(db, frozen_clock)
    store.migrate()
    item = store.create_work_item(kind="issue", external_ref="issue:1", title="t")
    old = store.start_stage_run(item, "deliver", "fake")
    store.close()
    conn = sqlite3.connect(db)
    conn.executescript(
        "PRAGMA foreign_keys=OFF;\n"
        "CREATE TABLE stage_run_copy AS SELECT * FROM stage_run;\n"
        "DROP TABLE stage_run;\n"
        + LAYOUT_2_STAGE_RUN
        + "INSERT INTO stage_run (id, work_item_id, stage, backend, status, started_at, "
        "ended_at, turns, exit_reason, transcript_path) SELECT * FROM stage_run_copy;\n"
        "DROP TABLE stage_run_copy;\n"
        "CREATE INDEX idx_stage_run_item ON stage_run(work_item_id);\n"
        "PRAGMA user_version=2;\n"
    )
    with pytest.raises(sqlite3.IntegrityError):  # the old CHECK is really in force
        conn.execute(
            "INSERT INTO stage_run (work_item_id, stage, backend, status, started_at) "
            "VALUES (?, 'selfaudit_fix', 'fake', 'running', '2026-09-01T12:00:00Z')",
            (item,),
        )
    conn.close()
    assert _user_version(db) == 2

    store = Store(db, frozen_clock)
    store.migrate()
    new = store.start_stage_run(item, "selfaudit_fix", "fake")
    store.migrate()  # and again: nothing changes

    assert [(row.id, row.stage) for row in store.list_stage_runs(work_item_id=item)] == [
        (old, "deliver"), (new, "selfaudit_fix")
    ]
    store.close()
    assert _user_version(db) == 3


@pytest.mark.parametrize(
    "path, tampered",
    [("audit-scratch.txt", "the auditor was here\n"), ("src/lib/bundle.ts", "sed -i was here\n")],
    ids=["a-new-file", "a-tracked-file"],
)
def test_B379_whatever_the_auditor_writes_is_restored_away_and_its_audit_discarded(
    tmp_path, monkeypatch, path, tampered
):
    loop = loop_rig(
        tmp_path,
        monkeypatch,
        script={
            "selfaudit": [answer(audit(finding()), write(path, tampered))],
            "selfaudit_fix": [answer(None, write("src/lib/bundle.test.ts", "it('fails');\n"))],
        },
    )

    loop.through_delivery()

    [entry] = loop.history()
    assert entry["status"] == "not run: the auditor modified the tree"
    assert entry["findings"] == []
    assert loop.requests("selfaudit_fix") == []
    current = loop.clone / path
    assert not current.exists() or current.read_text(encoding="utf-8") == IMPLEMENTED
    snapshots = loop.tree.snapshots.values()
    assert all(tampered.encode() not in snapshot.get(path, b"") for snapshot in snapshots)
    assert len(loop.tree.commits) == 1
    assert "**Self-audit: not run — the auditor modified the tree.**" in loop.body


# --------------------------------------------------------------------------------------------
# T22-T24: fail closed
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "The change does what was approved.",
        "<!-- selfaudit: {not json} -->",
        '<!-- selfaudit: {"verdict": "maybe", "findings": []} -->',
        '<!-- selfaudit: {"verdict": "findings"} -->',
        'First, some prose.\n<!-- selfaudit: {"verdict": "clean", "findings": []} -->',
    ],
    ids=["no-block", "not-json", "bad-verdict", "no-findings-list", "not-the-first-line"],
)
def test_B380_an_unreadable_answer_is_no_findings_and_not_run(tmp_path, monkeypatch, text):
    loop = loop_rig(tmp_path, monkeypatch, script={"selfaudit": [text]})

    loop.through_delivery()

    [entry] = loop.history()
    assert entry["status"] == "not run: unreadable"
    assert entry["findings"] == []
    assert loop.requests("selfaudit_fix") == []
    assert loop.state() == "packaged"
    assert "**Self-audit: not run — unreadable.**" in loop.body


def test_B381_where_must_name_a_changed_or_touched_path_or_a_real_index(tmp_path, monkeypatch):
    offered = [
        finding("src/nowhere.ts", severity="note", claim="names no path of this change"),
        finding("scripts/check-bundle-size.js", severity="note", claim="an omission"),
        finding("acceptance:2", severity="note", claim="criterion two"),
        finding("acceptance:3", severity="note", claim="there is no third criterion"),
        finding("behavior:15", severity="note", claim="the last behavior"),
        finding("behavior:16", severity="note", claim="there is no sixteenth behavior"),
        finding("src/lib/bundle.ts:12", severity="note", claim="a changed line"),
    ]
    loop = loop_rig(tmp_path, monkeypatch, script={"selfaudit": [audit(*offered)]})

    implement(loop.ctx, loop.item_id)

    assert "scripts/check-bundle-size.js" not in loop.tree.changed_paths(loop.clone, BASE)
    assert [f["where"] for f in loop.history()[0]["findings"]] == [
        "scripts/check-bundle-size.js", "acceptance:2", "behavior:15", "src/lib/bundle.ts:12"
    ]
    decisions = loop.decisions()
    for where in ("src/nowhere.ts", "acceptance:3", "behavior:16"):
        assert f"discarded a finding: finding" in decisions and repr(where) in decisions


def test_B382_a_token_in_the_auditors_answer_reaches_no_record_package_or_body(
    tmp_path, monkeypatch
):
    loop = loop_rig(
        tmp_path,
        monkeypatch,
        cap=1,
        script={
            "selfaudit": [
                audit(
                    finding(
                        "acceptance:2",
                        claim=f"the log prints {TOKEN} in full",
                        evidence=f"line 3 reads token={TOKEN}",
                    )
                )
            ]
        },
    )

    loop.through_delivery()

    assert "the log prints [REDACTED] in full" in loop.body  # it got there, redacted
    secret = TOKEN.encode()
    assert secret not in (loop.run_dir / "selfaudit.json").read_bytes()
    assert secret not in (loop.run_dir / "DECISIONS.md").read_bytes()
    for path in (loop.run_dir / "package").rglob("*"):
        if path.is_file():
            assert secret not in path.read_bytes(), path
    assert TOKEN not in loop.body


# --------------------------------------------------------------------------------------------
# T25-T27: where it surfaces
# --------------------------------------------------------------------------------------------


def test_B383_the_body_shows_the_audit_after_the_gates_five_findings_and_a_bounded_block(
    tmp_path, monkeypatch
):
    many = [
        finding(f"behavior:{n}", claim=f"claim {n:02d} " + "x" * 600, evidence="e" * 2000)
        for n in range(1, 13)
    ]
    loop = loop_rig(tmp_path, monkeypatch, cap=1, script={"selfaudit": [audit(*many)]})

    loop.through_delivery()

    body = loop.body
    tip = loop.tree.head[:12]
    line = (
        f"**Self-audit at `{tip}`: 12 blocking findings (+0 notes)** — a model reviewing its "
        "own diff; an opinion, not a gate result."
    )
    assert body.index("<summary>Gate results") < body.index(line)
    assert body.index(line) < body.index("<summary>The review package, verbatim")
    listed = [row for row in body.splitlines() if row.startswith("- `behavior:")]
    assert [row[: len("- `behavior:1`")] for row in listed] == [
        f"- `behavior:{n}`" for n in range(1, 6)
    ]
    assert "- and 7 more" in body.splitlines()
    assert "e" * 50 not in body  # evidence is in the package's DECISIONS.md, never the body
    block = loop.bodies[-1][0]["self_audit"]
    assert block.splitlines()[0] == line
    assert len(block) < 3000
    # The parser keeps 500 characters of a claim, and the body lists 300 of them: the bound is
    # the body's own, not one the parser happens to impose first.
    assert "x" * 301 not in block
    for row in listed:
        assert len(row.split(" — ", 1)[1]) == 300, row


def test_B384_with_no_record_the_body_says_not_run_for_this_revision(tmp_path, monkeypatch):
    loop = loop_rig(tmp_path, monkeypatch)
    lease = implement(loop.ctx, loop.item_id)
    package(loop.ctx, loop.item_id, lease)
    (loop.run_dir / "selfaudit.json").unlink()

    deliver_mod.deliver(loop.ctx, loop.item_id)

    assert "**Self-audit: not run for this revision.**" in loop.body
    assert "Self-audit at" not in loop.body


def test_B384_a_tip_that_moved_since_the_audit_is_not_run_for_this_revision(
    tmp_path, monkeypatch
):
    loop = loop_rig(tmp_path, monkeypatch, script={"selfaudit": [audit(finding())]})
    lease = implement(loop.ctx, loop.item_id)
    package(loop.ctx, loop.item_id, lease)
    audited = loop.history()[-1]["tip"]
    (loop.clone / "src" / "lib" / "later.ts").write_text("x\n", encoding="utf-8")
    loop.tree.commit(loop.clone, "fix: a revision the auditor never saw")

    deliver_mod.deliver(loop.ctx, loop.item_id)

    assert loop.tree.head[:12] != audited
    assert "**Self-audit: not run for this revision.**" in loop.body
    assert "blocking finding" not in loop.body
    assert self_audit_block({"history": [{"tip": audited, "status": "ok"}]}, "") == (
        "**Self-audit: not run for this revision.**"
    )


def test_B385_doctor_names_the_self_audit_cap_and_exits_0(tmp_path, monkeypatch, capsys):
    from tests.test_cli import doctor_ok, write_d2_repo

    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path)
    assert cli.main(["init"]) == 0
    doctor_ok(monkeypatch)
    capsys.readouterr()

    assert cli.main(["doctor"]) == 0
    assert "MAX_SELF_AUDIT_CYCLES" in capsys.readouterr().out

    (tmp_path / ".harness").mkdir(exist_ok=True)
    (tmp_path / ".harness" / "config.json").write_text(
        json.dumps({"MAX_SELF_AUDIT_CYCLES": 1}) + "\n", encoding="utf-8", newline="\n"
    )
    assert cli.main(["doctor", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["config_keys"]["MAX_SELF_AUDIT_CYCLES"] == "1"


# --------------------------------------------------------------------------------------------
# B387-B394: what the adversarial pass on the pull request found
# --------------------------------------------------------------------------------------------

BRANCH = "harness/fix-816-x"
LIVE = "live" + "Q7k" * 12  # a live secret value that no vendor pattern knows
AWKWARD = (" leading-space.ts", "del\x7fete.ts")  # legal on NTFS too; git C-quotes the second


def real_repo(root: Path) -> tuple[Path, str, str]:
    """A clone as implement leaves it: a base, and the implementation committed on the branch.

    `core.autocrlf` is set in the clone itself, because the harness's own git calls read the
    clone's configuration and not `_git`'s flags, and a Windows runner turns it on globally.
    """
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "core.autocrlf", "false")
    (root / "README.md").write_text("base\n", encoding="utf-8", newline="\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "--no-verify", "-m", "base")
    base = _git(root, "rev-parse", "HEAD")
    _git(root, "checkout", "-q", "-b", BRANCH)
    (root / "src" / "lib").mkdir(parents=True)
    (root / "src" / "lib" / "bundle.ts").write_text(IMPLEMENTED, encoding="utf-8", newline="\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "--no-verify", "-m", "feat: implement the package")
    return root, base, _git(root, "rev-parse", "HEAD")


def git_step(*commands: tuple[str, ...]):
    """What a call holding Bash does with git in the clone."""

    def effect(request):
        for command in commands:
            _git(Path(request.cwd), *command)

    return effect


def slip_in_a_commit(request):
    """`commit-tree` and `update-ref`: a new commit on the branch, and the tree left untouched."""
    cwd = Path(request.cwd)
    sha = _git(cwd, "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "chore: slipped in")
    _git(cwd, "update-ref", f"refs/heads/{BRANCH}", sha)


def real_loop(tmp_path, monkeypatch, script: dict):
    """`_run_self_audit` on a real repository: the rig's store, runner and gates, and production's
    git for every operation the loop makes. Returns the loop, the lease, the tip and the call."""
    names = (
        "CHANGED_PATHS", "COMMIT", "TIP_SHA", "UNIFIED_DIFF", "RESET_TO", "RESTORE_PATHS",
        "HEAD_STATE", "PUT_HEAD", "DIFF_LINES",
    )
    production = {name: getattr(implement_mod, name) for name in names}
    run_command = gates_mod.run_command
    loop = loop_rig(tmp_path, monkeypatch, script=script)
    for name, value in production.items():
        monkeypatch.setattr(implement_mod, name, value)
    monkeypatch.setattr(gates_mod, "run_command", run_command)
    repo, base, tip = real_repo(tmp_path / "real")
    lease = Lease(run_id="real", path=repo, base_sha=base, branch=BRANCH)
    loop.rig.store.transition(loop.item_id, "implementing", reason="implement acquired a clone")
    item = loop.rig.store.get_work_item(loop.item_id)
    spec_text = implement_mod._read_spec(loop.ctx, item)
    pkg = implement_mod.parse_work_package(spec_text)

    def run():
        return implement_mod._run_self_audit(
            loop.ctx, loop.item_id, item, lease, pkg, spec_text, list(GREEN), list(GREEN)
        )

    return loop, lease, tip, run


def assert_on_the_tip(lease: Lease, tip: str, subjects: list[str]) -> None:
    repo = lease.path
    assert _git(repo, "rev-parse", "HEAD") == tip
    assert _git(repo, "symbolic-ref", "HEAD") == f"refs/heads/{BRANCH}"
    assert _git(repo, "status", "--porcelain", "--untracked-files=all") == ""
    assert _git(repo, "log", "--format=%s", f"{lease.base_sha}..HEAD").splitlines() == subjects


AUDITOR = "AUDITOR WAS HERE\n"


@pytest.mark.parametrize(
    "moves",
    [
        [
            write("src/lib/bundle.ts", AUDITOR),
            git_step(("commit", "-q", "--no-verify", "-am", "chore: the auditor's commit")),
        ],
        [git_step(("commit", "-q", "--no-verify", "--amend", "-m", "chore: the auditor's words"))],
        [git_step(("reset", "-q", "--soft", "HEAD~1"))],
        [slip_in_a_commit],
        [
            git_step(("checkout", "-q", "-b", "elsewhere")),
            write("src/lib/bundle.ts", AUDITOR),
            git_step(("commit", "-q", "--no-verify", "-am", "chore: elsewhere")),
        ],
        [git_step(("checkout", "-q", "--detach"))],
    ],
    ids=["commit", "amend", "reset-soft", "update-ref", "switch", "detach"],
)
def test_B387_an_auditor_that_moves_the_branch_has_it_put_back_and_its_audit_discarded(
    tmp_path, monkeypatch, moves
):
    loop, lease, tip, run = real_loop(
        tmp_path, monkeypatch, {"selfaudit": [answer(audit(finding()), *moves)]}
    )

    run()

    assert_on_the_tip(lease, tip, ["feat: implement the package"])
    patch = _git(lease.path, "format-patch", "--stdout", f"{lease.base_sha}..HEAD")
    assert AUDITOR.strip() not in patch
    [entry] = loop.history()
    assert entry["status"] == "not run: the auditor modified the tree"
    assert entry["findings"] == []
    assert loop.requests("selfaudit_fix") == []


def test_B387_a_rate_limited_auditor_that_committed_is_put_back_too(tmp_path, monkeypatch):
    moves = [
        write("src/lib/bundle.ts", AUDITOR),
        git_step(("commit", "-q", "--no-verify", "-am", "chore: the auditor's commit")),
    ]
    loop, lease, tip, run = real_loop(
        tmp_path, monkeypatch, {"selfaudit": [answer(rate_limited(), *moves)]}
    )

    with pytest.raises(RateLimited):
        run()

    assert_on_the_tip(lease, tip, ["feat: implement the package"])
    assert loop.history()[-1]["status"] == "not run: rate limited"
    assert loop.state() == "approved"


def test_B388_a_fix_pass_that_commits_its_own_work_is_committed_again_by_the_harness(
    tmp_path, monkeypatch
):
    commit = ("commit", "-q", "--no-verify", "-m", "wip: the model's own commit")
    loop, lease, tip, run = real_loop(
        tmp_path,
        monkeypatch,
        {
            "selfaudit": [audit(finding()), audit()],
            "selfaudit_fix": [
                answer(
                    None,
                    write("src/lib/bundle.test.ts", "it('holds');\n"),
                    git_step(("add", "-A"), commit),
                )
            ],
        },
    )

    run()

    repo = lease.path
    subjects = _git(repo, "log", "--format=%s", f"{lease.base_sha}..HEAD").splitlines()
    assert len(subjects) == 2 and subjects[1] == "feat: implement the package"
    assert "address self-audit findings" in subjects[0]
    assert "the model's own commit" not in _git(repo, "log", "--format=%B", "HEAD")
    assert _git(repo, "rev-parse", "HEAD~1") == tip
    assert _git(repo, "show", "HEAD:src/lib/bundle.test.ts") == "it('holds');"
    assert _git(repo, "symbolic-ref", "HEAD") == f"refs/heads/{BRANCH}"
    assert _git(repo, "status", "--porcelain", "--untracked-files=all") == ""
    first, second = loop.history()
    assert first["tip"] == tip[:12]
    assert first["outcome"] == "fix pass committed; no new gate failures"
    assert (second["tip"], second["status"]) == (_git(repo, "rev-parse", "HEAD")[:12], "ok")


def test_B388_a_fix_pass_that_resets_the_branch_to_the_base_cannot_pass_for_no_change(
    tmp_path, monkeypatch
):
    loop, lease, tip, run = real_loop(
        tmp_path,
        monkeypatch,
        {
            "selfaudit": [audit(finding())],
            "selfaudit_fix": [answer(None, git_step(("reset", "-q", "--soft", "HEAD~1")))],
        },
    )

    run()

    assert_on_the_tip(lease, tip, ["feat: implement the package"])
    [entry] = loop.history()
    assert entry["outcome"] == "fix pass changed nothing"


def test_B389_a_halt_before_the_fix_pass_leaves_that_cycles_findings_as_the_last_word(
    tmp_path, monkeypatch
):
    ref: dict = {}
    unpublishable_is_clear(monkeypatch)
    loop = ref["loop"] = loop_rig(
        tmp_path, monkeypatch, script={"selfaudit": [answer(audit(finding()), engage_halt(ref))]}
    )

    implement(loop.ctx, loop.item_id)

    tip = loop.tree.head[:12]
    [entry] = loop.history()
    assert (entry["tip"], entry["status"]) == (tip, "ok")
    assert [f["where"] for f in entry["findings"]] == ["acceptance:2"]
    assert entry["outcome"] == "halted before the fix pass; findings carried"
    block = self_audit_block({"history": loop.history()}, tip).splitlines()
    assert block[0].startswith(f"**Self-audit at `{tip}`: 1 blocking finding (+0 notes)**")
    assert "- Cycle 1: halted before the fix pass; findings carried." in block


def test_B389_a_halt_after_a_committed_fix_is_not_run_for_the_tip_nobody_audited(
    tmp_path, monkeypatch
):
    ref: dict = {}
    unpublishable_is_clear(monkeypatch)
    loop = ref["loop"] = loop_rig(
        tmp_path,
        monkeypatch,
        script={
            "selfaudit": [audit(finding())],
            "selfaudit_fix": [
                answer(None, write("src/lib/bundle.test.ts", "it('holds');\n"), engage_halt(ref))
            ],
        },
    )

    implement(loop.ctx, loop.item_id)

    first, second = loop.history()
    assert first["outcome"] == "fix pass committed; no new gate failures"
    assert (second["cycle"], second["status"]) == (2, "not run: halted")
    assert second["tip"] == loop.tree.head[:12] != first["tip"]


def test_B390_a_name_git_would_quote_is_read_as_it_is_by_the_change_set_restore_and_b64(
    tmp_path,
):
    repo, base, tip = real_repo(tmp_path / "real")
    for name in AWKWARD:
        (repo / name).write_text("it.skip('x');\n", encoding="utf-8", newline="\n")

    assert prettier.all_changed_paths(repo, tip) == sorted(AWKWARD)
    lease = Lease(run_id="real", path=repo, base_sha=base, branch=BRANCH)
    added, _removed = implement_mod._diff_lines(lease, list(AWKWARD))
    assert added.count("it.skip('x');") == len(AWKWARD)

    implement_mod._restore_paths(repo, tip[:12], prettier.all_changed_paths(repo, tip))

    assert prettier.all_changed_paths(repo, tip) == []
    assert not any((repo / name).exists() for name in AWKWARD)


def test_B391_a_clone_the_loop_cannot_put_back_blocks_the_item_and_commits_nothing(
    tmp_path, monkeypatch
):
    loop = loop_rig(
        tmp_path,
        monkeypatch,
        can_write=True,
        script={"selfaudit": [answer(audit(finding()), write("audit-scratch.txt", "left\n"))]},
    )
    monkeypatch.setattr(implement_mod, "RESTORE_PATHS", lambda clone, sha, paths: None)

    with pytest.raises(HarnessError, match="could not put"):
        implement(loop.ctx, loop.item_id)

    assert loop.state() == "blocked"
    assert [keep for _lease, keep in loop.rig.clones.released] == [True]
    assert (loop.clone / "audit-scratch.txt").exists()
    assert "audit-scratch.txt" not in loop.tree.committed_paths()
    assert loop.rig.gh.pushed == []
    assert loop.requests("selfaudit_fix") == []
    assert loop.history()[-1]["status"] == "not run: the auditor modified the tree"


def test_B392_a_secret_a_finding_cap_would_cut_is_redacted_whole_before_the_cut(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", LIVE)

    def across(cap: int, secret: str, kept: int) -> str:
        return "c" * (cap - kept) + secret + " and the rest"

    offered = [
        finding("acceptance:2", claim=across(500, TOKEN, 33), evidence=across(1000, LIVE, 30)),
        finding(
            "acceptance:1", severity="note",
            claim=across(500, LIVE, 30), evidence=across(1000, TOKEN, 33),
        ),
        finding(across(120, LIVE, 30), severity="note", claim="names nothing"),
        finding(across(120, TOKEN, 33), severity="note", claim="names nothing either"),
    ]
    loop = loop_rig(tmp_path, monkeypatch, cap=1, script={"selfaudit": [audit(*offered)]})

    loop.through_delivery()

    assert len(loop.history()[0]["findings"]) == 2
    assert loop.decisions().count("discarded a finding") == 2
    places = {"the body": loop.body}
    for path in loop.run_dir.rglob("*"):
        if path.is_file() and "clone" not in path.relative_to(loop.run_dir).parts:
            places[path.relative_to(loop.run_dir).as_posix()] = path.read_text(
                encoding="utf-8", errors="replace"
            )
    assert "selfaudit.json" in places and "DECISIONS.md" in places
    for secret in (TOKEN, LIVE):
        pieces = [secret[i : i + 20] for i in range(len(secret) - 19)]
        for name, text in places.items():
            assert not any(piece in text for piece in pieces), (name, secret[:4])


def test_B393_a_mention_in_a_finding_is_not_live_in_the_comment_a_halt_posts(
    tmp_path, monkeypatch
):
    ref: dict = {}
    unpublishable_is_clear(monkeypatch)
    quoted = finding(
        claim="ask @octocat to review", evidence="CODEOWNERS names @octocat in <b>`bold`</b>"
    )
    loop = ref["loop"] = loop_rig(
        tmp_path,
        monkeypatch,
        can_write=True,
        script={"selfaudit": [answer(audit(quoted), engage_halt(ref))]},
    )

    implement(loop.ctx, loop.item_id)

    posted = [
        body for _repo, _number, body in loop.rig.gh.comments_posted
        if "halted during self-audit" in body
    ]
    assert len(posted) == 1
    assert "ask @​octocat to review" in posted[0]  # the finding's line is in the comment
    assert "@octocat" not in posted[0]
    assert "<b>" not in posted[0]
    assert "@octocat" not in loop.decisions()


def test_B394_the_git_that_undoes_a_models_changes_works_on_a_real_clone(tmp_path):
    repo, base, _ = real_repo(tmp_path / "real")
    for name in ("b.txt", "keep.txt", "gone.txt"):
        (repo / name).write_text(f"{name}\n", encoding="utf-8", newline="\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-verify", "-m", "feat: more of it")
    tip = _git(repo, "rev-parse", "HEAD")
    short = tip[:12]

    # _restore_paths: every kind of mess a call holding Bash can leave, each path by name.
    (repo / "keep.txt").write_text("changed\n", encoding="utf-8", newline="\n")
    (repo / "gone.txt").unlink()
    (repo / "new-dir").mkdir()
    (repo / "new-dir" / "new.txt").write_text("new\n", encoding="utf-8", newline="\n")
    (repo / "staged.txt").write_text("staged\n", encoding="utf-8", newline="\n")
    _git(repo, "add", "staged.txt")
    (repo / "[ab].txt").write_text("a pathspec that matches b.txt\n", encoding="utf-8")
    (repo / "b.txt").write_text("changed, and never named\n", encoding="utf-8", newline="\n")
    named = [p for p in prettier.all_changed_paths(repo, short) if p != "b.txt"]
    assert "[ab].txt" in named and "new-dir/new.txt" in named

    implement_mod._restore_paths(repo, short, named)

    assert prettier.all_changed_paths(repo, short) == ["b.txt"]
    assert _git(repo, "ls-files", "b.txt") == "b.txt"
    assert (repo / "b.txt").read_text(encoding="utf-8") == "changed, and never named\n"
    assert (repo / "keep.txt").read_text(encoding="utf-8") == "keep.txt\n"
    assert (repo / "gone.txt").read_text(encoding="utf-8") == "gone.txt\n"
    assert not (repo / "new-dir" / "new.txt").exists() and not (repo / "staged.txt").exists()

    # _reset_to: a committed fix is undone and the branch is back on the tip.
    (repo / "fix.txt").write_text("fix\n", encoding="utf-8", newline="\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-verify", "-m", "fix: the fix")

    implement_mod._reset_to(repo, short)

    assert _git(repo, "rev-parse", "HEAD") == tip
    assert prettier.all_changed_paths(repo, short) == []

    # _head_state and _put_head: a switch and a commit elsewhere; HEAD back, the tree untouched.
    state = implement_mod._head_state(repo)
    assert state == f"refs/heads/{BRANCH} {tip}"
    _git(repo, "checkout", "-q", "-b", "elsewhere")
    (repo / "moved.txt").write_text("moved\n", encoding="utf-8", newline="\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-verify", "-m", "chore: elsewhere")
    assert implement_mod._head_state(repo) != state

    implement_mod._put_head(repo, state)

    assert implement_mod._head_state(repo) == state
    assert (repo / "moved.txt").exists()
    implement_mod._reset_to(repo, short)
    assert _git(repo, "status", "--porcelain", "--untracked-files=all") == ""
    assert _git(repo, "rev-parse", f"refs/heads/{BRANCH}") == tip
    assert base != tip
