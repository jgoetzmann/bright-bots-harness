"""D70 smoke: the self-audit loop runs end to end on the tests/test_stages.py rig.

These prove the loop is wired, not that each rule holds; the behaviour tests (B359 onward) do
that. Nothing here touches git, the network or a model: the rig's clone is a plain directory
and every git operation the loop uses is stubbed by `stub_implement_side_effects`.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace

import harness.stages.implement as implement_mod
from harness.stages.deliver import build_pr_body, self_audit_block
from harness.stages.implement import implement
from harness.stages.propose import propose
from tests.test_stages import (
    CLEAN_SELFAUDIT,
    GREEN,
    RED,
    approved_item,
    proposable,
    stub_implement_side_effects,
)

TIP = "b" * 12
BLOCKING = (
    '<!-- selfaudit: {"verdict": "findings", "findings": [{"severity": "blocking", '
    '"claim": "the over-ceiling case is never tested", "where": "acceptance:2", '
    '"evidence": "no test builds an esm bundle over the ceiling"}]} -->'
)
GOLDEN_DIR = Path(__file__).resolve().parent / "fixtures" / "deliver" / "pr_body_cap0"


class Scripted:
    """The rig's FakeRunner, with per-stage texts popped in order and a hook after each call."""

    def __init__(self, inner, texts: dict[str, list[str]], after=None) -> None:
        self.inner = inner
        self.texts = {stage: list(queue) for stage, queue in texts.items()}
        self.after = after

    def run(self, request):
        result = self.inner.run(request)
        queue = self.texts.get(request.stage)
        if queue:
            result = dataclasses.replace(result, text=queue.pop(0))
        if self.after is not None:
            self.after(request)
        return result


def _implemented(tmp_path, monkeypatch, *, gates, texts=None, after=None):
    rig, item_id = proposable(tmp_path)
    propose(rig.ctx, item_id)
    approved_item(rig, item_id)
    rig.log.clear()
    rig.clones.released.clear()
    if texts or after:
        rig.runner.inner = Scripted(rig.runner.inner, texts or {}, after)

    def gate_runner(clone, *, baseline, runner=None):
        rig.log.append(f"gates(baseline={baseline})")
        return list(gates(baseline))

    stub_implement_side_effects(monkeypatch, rig.log, gate_runner=gate_runner)
    return rig, item_id


def _sequence(rig) -> list[str]:
    return [
        entry
        for entry in rig.log
        if entry.startswith(("run:", "gates(", "reset_to:")) or entry == "commit"
    ]


def _record(rig, item_id) -> dict:
    path = rig.run_dir(f"item-{item_id}") / "selfaudit.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_d70_smoke_a_clean_audit_runs_once_after_the_gates(tmp_path, monkeypatch):
    rig, item_id = _implemented(tmp_path, monkeypatch, gates=lambda baseline: GREEN)

    implement(rig.ctx, item_id)

    assert _sequence(rig) == [
        "gates(baseline=True)", "run:implement", "commit", "gates(baseline=False)",
        "run:selfaudit",
    ]
    audit = [r for r in rig.runner.requests if r.stage == "selfaudit"]
    assert audit[0].allowed_tools == ("Read", "Glob", "Grep", "Bash")
    history = _record(rig, item_id)["history"]
    assert [(e["tip"], e["status"], e["findings"]) for e in history] == [(TIP, "ok", [])]
    assert rig.store.get_work_item(item_id).state == "implementing"


def test_d70_smoke_findings_get_a_fix_pass_new_gates_and_a_second_audit(tmp_path, monkeypatch):
    rig, item_id = _implemented(
        tmp_path, monkeypatch, gates=lambda baseline: GREEN,
        texts={"selfaudit": [BLOCKING, CLEAN_SELFAUDIT]},
    )

    implement(rig.ctx, item_id)

    assert _sequence(rig) == [
        "gates(baseline=True)", "run:implement", "commit", "gates(baseline=False)",
        "run:selfaudit", "run:selfaudit_fix", "commit", "gates(baseline=False)",
        "run:selfaudit",
    ]
    history = _record(rig, item_id)["history"]
    assert [e["status"] for e in history] == ["ok", "ok"]
    assert history[0]["findings"][0]["where"] == "acceptance:2"
    assert history[0]["outcome"] == "fix pass committed; no new gate failures"
    assert history[1]["findings"] == []
    assert rig.store.get_work_item(item_id).state == "implementing"


def test_d70_smoke_a_fix_that_breaks_a_gate_is_reverted_and_not_blocked(tmp_path, monkeypatch):
    post: list[int] = []

    def gates(baseline):
        if baseline:
            return GREEN
        post.append(1)
        return GREEN if len(post) == 1 else RED

    rig, item_id = _implemented(
        tmp_path, monkeypatch, gates=gates, texts={"selfaudit": [BLOCKING]}
    )

    implement(rig.ctx, item_id)

    assert _sequence(rig)[-3:] == ["commit", "gates(baseline=False)", f"reset_to:{TIP}"]
    final = json.loads(
        (rig.run_dir(f"item-{item_id}") / "gates" / "final.json").read_text(encoding="utf-8")
    )
    assert [row["exit_code"] for row in final] == [0]
    history = _record(rig, item_id)["history"]
    assert history[-1]["outcome"] == "fix pass reverted: broke npm run test:unit"
    assert rig.store.get_work_item(item_id).state == "implementing"


def test_d70_smoke_a_halt_on_the_fix_pass_hands_the_item_off(tmp_path, monkeypatch):
    handed: list[str] = []

    def halt_after_audit(request):
        if request.stage == "selfaudit":
            rig.ctx.config.halt_file.write_text("", encoding="utf-8")

    rig, item_id = _implemented(
        tmp_path, monkeypatch, gates=lambda baseline: GREEN,
        texts={"selfaudit": [BLOCKING]}, after=halt_after_audit,
    )
    monkeypatch.setattr(
        implement_mod.deliver_mod, "handoff",
        lambda ctx, item, *, reason: handed.append(reason),
    )

    lease = implement(rig.ctx, item_id)

    assert lease.branch
    assert len(handed) == 1 and handed[0].startswith("halted during self-audit")
    assert "run:selfaudit_fix" not in rig.log
    assert [keep for _lease, keep in rig.clones.released] == []
    assert _record(rig, item_id)["history"][-1]["status"] == "not run: halted"


def test_d70_smoke_cap_zero_makes_no_call_and_writes_no_record(tmp_path, monkeypatch):
    rig, item_id = _implemented(tmp_path, monkeypatch, gates=lambda baseline: GREEN)
    rig.ctx.config = dataclasses.replace(rig.ctx.config, max_self_audit_cycles=0)

    implement(rig.ctx, item_id)

    assert "run:selfaudit" not in rig.log
    assert not (rig.run_dir(f"item-{item_id}") / "selfaudit.json").exists()


def test_d70_smoke_the_pr_body_line_sits_after_the_gate_block(tmp_path):
    kwargs = json.loads((GOLDEN_DIR / "kwargs.json").read_bytes().decode("utf-8"))
    kwargs["config"] = SimpleNamespace(**kwargs["config"])
    kwargs["trusted"] = tuple(kwargs["trusted"])
    golden = (GOLDEN_DIR.parent / "pr_body_cap0.md").read_bytes()

    assert build_pr_body(GOLDEN_DIR / "package", **kwargs).encode("utf-8") == golden

    record = {"history": [{"cycle": 1, "tip": TIP, "status": "ok", "outcome": "", "findings": [
        {"severity": "note", "claim": "c", "where": "acceptance:1", "evidence": ""}]}]}
    block = self_audit_block(record, TIP)
    line = f"**Self-audit at `{TIP}`: no blocking findings, 1 notes.**"
    assert block.splitlines()[0] == line
    body = build_pr_body(GOLDEN_DIR / "package", **kwargs, self_audit=block)
    assert body.index("<summary>Gate results") < body.index(line)
    assert body.index(line) < body.index("<summary>The review package, verbatim")
    assert self_audit_block(record, "c" * 12) == "**Self-audit: not run for this revision.**"
