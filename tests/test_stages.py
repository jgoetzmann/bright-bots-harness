"""B53-B64: harness.stages, driven by the fake runner.

Every fixture here is written inline on purpose: the `.env`, the FakeRunner
fixture directory, the GitHub client, the clone manager and the clock are all
built in this file. Nothing touches the network, the wall clock, a real
`claude`, or a real `git`.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import harness.stages.implement as implement_mod
from harness.clock import FrozenClock
from harness.clone import Lease
from harness.config import load_config
from harness.context import build_context
from harness.errors import HarnessError, IllegalTransition, NotImplementedInDelivery1
from harness.gates import GateResult
from harness.runner.fake import FakeRunner
from harness.stages.discover import discover
from harness.stages.implement import evaluate_fullsend_gate, implement
from harness.stages.propose import parse_work_package, propose
from harness.store import Store

# --------------------------------------------------------------------------
# Inline fixtures
# --------------------------------------------------------------------------

ENV = """\
# inline test env for tests/test_stages.py
BACKEND=fake
REPO=Bright-Bots-Initiative/brightboost
PERMISSION_TIER=0
ALLOWLIST_LABEL=harness-ok
WEEKLY_BUDGET_PCT=100
SESSION_BUDGET_PCT=100
RESERVE_PCT=0
WEEKLY_RESET_DAY=monday
MAX_CONCURRENT_CLONES=1
MAX_TURNS_DISCOVER=10
MAX_TURNS_PROPOSE=30
MAX_TURNS_IMPLEMENT=80
MAX_TURNS_PACKAGE=10
MAX_RETRIES_GATES=2
GITHUB_API_CEILING_PER_HOUR=50
MIN_FREE_DISK_GB=0
DB_PATH=harness.db
RUNS_DIR=runs
PACKAGES_DIR=packages
HALT_FILE=HALT
FULLSEND_ENABLED={fullsend}
HARNESS_GITHUB_TOKEN=
ANTHROPIC_API_KEY=
"""

FROZEN_AT = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)

RUNNER_STAGES = ("discover", "propose", "implement", "package", "diagnose_gate_failure")


def write_env(tmp_path: Path, *, fullsend: str = "false") -> Path:
    path = tmp_path / ".env"
    path.write_text(ENV.format(fullsend=fullsend), encoding="utf-8", newline="\n")
    return path


def work_package_text(
    *,
    n_slices: int = 3,
    n_behaviors: int = 15,
    open_questions: tuple[str, ...] = (),
    touched: tuple[str, ...] = ("scripts/check-bundle-size.js", "src/lib/bundle.ts"),
) -> str:
    """A complete SPEC 7.1 work package: all ten headings, at least one decision."""
    lines: list[str] = [
        "# fix(build): bundle size check misreports esm output",
        "",
        "## Issue",
        "https://github.com/Bright-Bots-Initiative/brightboost/issues/816 (#816).",
        "The reporter sees a bundle-size failure they cannot reproduce locally.",
        "",
        "## Diagnosis",
        "`scripts/check-bundle-size.js:42` globs `dist/*.js` and never sees",
        "`dist/assets/*.mjs`, so the measured total is wrong on every esm build.",
        "",
        "## Approach",
        "Widen the glob and assert on the measured total rather than the per-file maximum.",
        "",
        "## Slices",
    ]
    for i in range(n_slices):
        lines.append(f"- Slice {i + 1}: an independently describable unit of the change")
    lines += ["", "## Behaviors"]
    for i in range(n_behaviors):
        lines.append(f"- N{i + 1}. behavior number {i + 1} is observable and testable")
    lines += [
        "",
        "## Acceptance criteria",
        "- The check passes on an esm build that is genuinely under the ceiling",
        "- The check fails on an esm build that is genuinely over the ceiling",
        "",
        "## Decisions",
        "- Measure the total rather than the maximum; rejected per-file caps as they",
        "  hide regressions spread across many chunks",
        "",
        "## Open questions",
    ]
    if open_questions:
        for question in open_questions:
            lines.append(f"- {question}")
    else:
        lines.append("None")
    lines += ["", "## Touched paths"]
    for path in touched:
        lines.append(f"- {path}")
    lines += [
        "",
        "## Risks",
        "A wider glob could pick up sourcemaps; the reviewer should check the",
        "measured file list printed by the script.",
        "",
    ]
    return "\n".join(lines)


def write_runner_fixtures(
    fixtures_dir: Path, *, propose_text: str, discover_text: str = "#102\n#101\n"
) -> Path:
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    texts = {
        "discover": discover_text,
        "propose": propose_text,
        "implement": "Edited scripts/check-bundle-size.js to widen the glob.",
        "package": "Package assembled.",
        "diagnose_gate_failure": "The unit test asserts on the old glob; update it.",
    }
    for stage in RUNNER_STAGES:
        payload = {
            "ok": True,
            "text": texts[stage],
            "turns": 3,
            "cost_usd": 0.01,
            "allowance_pct": 1.0,
            "duration_ms": 1234,
            "session_id": f"sess-{stage}",
            "exit_code": 0,
            "transcript": [{"role": "assistant", "content": texts[stage]}],
            "error": None,
        }
        (fixtures_dir / f"{stage}.json").write_text(
            json.dumps(payload), encoding="utf-8", newline="\n"
        )
    return fixtures_dir


def gh_issue(
    number: int,
    *,
    title: str | None = None,
    labels: tuple[str, ...] = ("harness-ok",),
    assigned: bool = False,
) -> dict:
    assignee = {"login": "someone"} if assigned else None
    return {
        "number": number,
        "title": title or f"issue {number} needs work",
        "body": f"Body of issue {number}.",
        "state": "open",
        "labels": [{"name": name} for name in labels],
        "assignee": assignee,
        "assignees": [assignee] if assigned else [],
        "html_url": (
            f"https://github.com/Bright-Bots-Initiative/brightboost/issues/{number}"
        ),
    }


class FakeGh:
    """Stands in for harness.gh.GitHubReadOnly. Serves inline dicts, never HTTP."""

    def __init__(
        self,
        *,
        issues: tuple[dict, ...] = (),
        pulls: tuple[dict, ...] = (),
        branches: tuple[str, ...] = (),
    ) -> None:
        self._issues = list(issues)
        self._pulls = list(pulls)
        self._branches = list(branches)
        self.calls: list[tuple] = []

    def issue(self, number: int) -> dict:
        self.calls.append(("issue", number))
        for item in self._issues:
            if item["number"] == number:
                return item
        return gh_issue(number)

    def issues(self, *, state: str = "open", labels=()) -> list[dict]:
        self.calls.append(("issues", state, tuple(labels)))
        wanted = set(labels)
        out = []
        for item in self._issues:
            have = {label["name"] for label in item.get("labels", [])}
            if wanted and not wanted <= have:
                continue
            out.append(item)
        return out

    def pulls(self, *, state: str = "open") -> list[dict]:
        self.calls.append(("pulls", state))
        return list(self._pulls)

    def branches(self) -> list[str]:
        self.calls.append(("branches",))
        return list(self._branches)

    def get(self, path: str):
        self.calls.append(("get", path))
        return {}

    def rate_budget_remaining(self) -> int:
        return 50


class FakeClones:
    """Stands in for harness.clone.CloneManager. Makes a directory, never a clone."""

    def __init__(self, runs_dir: Path) -> None:
        self.runs_dir = runs_dir
        self.acquired: list[Lease] = []
        self.released: list[tuple[Lease, bool]] = []

    def preflight(self) -> list[str]:
        return []

    def acquire(self, item) -> Lease:
        path = self.runs_dir / f"item-{item.id}" / "clone"
        path.mkdir(parents=True, exist_ok=True)
        lease = Lease(
            run_id=f"item-{item.id}",
            path=path,
            base_sha="a" * 40,
            branch="harness/fix-816-x",
        )
        self.acquired.append(lease)
        return lease

    def release(self, lease: Lease, *, keep: bool) -> None:
        self.released.append((lease, keep))


class CountingRunner:
    """Wraps FakeRunner so a test can see whether a model call happened at all."""

    def __init__(self, inner, log: list[str]) -> None:
        self.inner = inner
        self.name = "fake"
        self.log = log
        self.requests: list[object] = []

    @property
    def calls(self) -> int:
        return len(self.requests)

    def run(self, request):
        self.requests.append(request)
        self.log.append(f"run:{request.stage}")
        return self.inner.run(request)


class Rig:
    def __init__(self, ctx, runner, log, config, store, clones, gh) -> None:
        self.ctx = ctx
        self.runner = runner
        self.log = log
        self.config = config
        self.store = store
        self.clones = clones
        self.gh = gh

    def run_dir(self, run_id: str) -> Path:
        return self.config.runs_dir / run_id


def make_rig(
    tmp_path: Path,
    *,
    run_id: str,
    gh: FakeGh,
    fullsend: str = "false",
    propose_text: str | None = None,
    discover_text: str = "#102\n#101\n",
) -> Rig:
    env_path = write_env(tmp_path, fullsend=fullsend)
    config = load_config(env_path, environ={})
    fixtures_dir = write_runner_fixtures(
        tmp_path / "runner-fixtures",
        propose_text=propose_text if propose_text is not None else work_package_text(),
        discover_text=discover_text,
    )
    log: list[str] = []
    runner = CountingRunner(FakeRunner(fixtures_dir), log)
    clock = FrozenClock(FROZEN_AT)
    store = Store(config.db_path, clock)
    store.migrate()
    clones = FakeClones(config.runs_dir)
    ctx = build_context(
        config,
        run_id=run_id,
        runner=runner,
        gh=gh,
        clock=clock,
        store=store,
        clones=clones,
    )
    return Rig(ctx, runner, log, config, store, clones, gh)


GREEN = [
    GateResult(
        name="npm run lint",
        argv=("npm", "run", "lint"),
        exit_code=0,
        stdout_tail="lint ok",
        stderr_tail="",
    )
]

RED = [
    GateResult(
        name="npm run test:unit",
        argv=("npm", "run", "test:unit"),
        exit_code=1,
        stdout_tail="1 failing",
        stderr_tail="AssertionError: expected 3 to equal 4",
    )
]


def stub_implement_side_effects(monkeypatch, log: list[str], *, gate_runner, changed=None):
    """Replace the four module-level injectables in harness.stages.implement."""

    def prettier(clone, paths, runner=None):
        log.append("prettier")
        return (True, "")

    def changed_paths(clone, base_sha, git_runner=None):
        log.append("changed_paths")
        return list(changed if changed is not None else ["src/lib/bundle.ts"])

    def commit(clone, message):
        log.append("commit")

    monkeypatch.setattr(implement_mod, "GATE_RUNNER", gate_runner)
    monkeypatch.setattr(implement_mod, "PRETTIER", prettier)
    monkeypatch.setattr(implement_mod, "CHANGED_PATHS", changed_paths)
    monkeypatch.setattr(implement_mod, "COMMIT", commit)


def approved_item(rig: Rig, item_id: int) -> None:
    rig.store.transition(item_id, "approved", reason="test approves")


# --------------------------------------------------------------------------
# B53 / B54 - directed discover
# --------------------------------------------------------------------------


def test_B53_directed_discover_creates_exactly_one_discovered_item(tmp_path):
    gh = FakeGh(issues=(gh_issue(816, title="bundle size check misreports esm"),))
    rig = make_rig(tmp_path, run_id="discover", gh=gh)

    ids = discover(rig.ctx, mode="directed", target="816", lens=None)

    assert len(ids) == 1
    item = rig.store.get_work_item(ids[0])
    assert item is not None
    assert item.external_ref == "issue:816"
    assert item.kind == "issue"
    assert item.state == "discovered"
    assert item.title == "bundle size check misreports esm"
    assert len(rig.store.list_work_items()) == 1


def test_B53_directed_discover_makes_no_model_call(tmp_path):
    gh = FakeGh(issues=(gh_issue(816),))
    rig = make_rig(tmp_path, run_id="discover", gh=gh)

    discover(rig.ctx, mode="directed", target="816", lens=None)

    assert rig.runner.calls == 0
    assert rig.log == []


def test_B54_repeating_a_directed_discover_creates_no_duplicate_row(tmp_path):
    gh = FakeGh(issues=(gh_issue(816),))
    rig = make_rig(tmp_path, run_id="discover", gh=gh)

    first = discover(rig.ctx, mode="directed", target="816", lens=None)
    second = discover(rig.ctx, mode="directed", target="816", lens=None)

    assert first == second
    assert len(rig.store.list_work_items()) == 1
    assert rig.runner.calls == 0


# --------------------------------------------------------------------------
# B55 - B58 - triage exclusions
# --------------------------------------------------------------------------


def triage_rig(tmp_path, *, issues, branches=(), pulls=()):
    gh = FakeGh(issues=issues, branches=branches, pulls=pulls)
    return make_rig(tmp_path, run_id="discover", gh=gh)


def refs(rig: Rig) -> set[str]:
    return {item.external_ref for item in rig.store.list_work_items()}


def test_B55_triage_excludes_an_assigned_issue(tmp_path):
    rig = triage_rig(
        tmp_path,
        issues=(gh_issue(101), gh_issue(102, assigned=True)),
    )

    discover(rig.ctx, mode="triage", target=None, lens=None)

    assert "issue:101" in refs(rig)
    assert "issue:102" not in refs(rig)


def test_B56_triage_excludes_an_issue_claimed_by_a_branch_name(tmp_path):
    """B56: an issue whose number appears in an in-flight branch name is never queued."""
    rig = triage_rig(
        tmp_path,
        issues=(gh_issue(101), gh_issue(102)),
        branches=("main", "agent-102/bundle-size"),
    )

    discover(rig.ctx, mode="triage", target=None, lens=None)

    assert "issue:101" in refs(rig)
    assert "issue:102" not in refs(rig)


def test_B56_triage_excludes_an_issue_claimed_by_a_pr_title(tmp_path):
    """B56: an issue referenced by an open PR title is never queued."""
    rig = triage_rig(
        tmp_path,
        issues=(gh_issue(101), gh_issue(102)),
        branches=("main",),
        pulls=({"number": 9, "title": "fix: widen the glob, fixes #102"},),
    )

    discover(rig.ctx, mode="triage", target=None, lens=None)

    assert "issue:101" in refs(rig)
    assert "issue:102" not in refs(rig)


def test_B57_triage_excludes_the_intern_starter_label(tmp_path):
    """B57: the intern-starter label excludes an issue from triage."""
    rig = triage_rig(
        tmp_path,
        issues=(gh_issue(101), gh_issue(102, labels=("harness-ok", "intern-starter"))),
    )

    discover(rig.ctx, mode="triage", target=None, lens=None)

    assert "issue:101" in refs(rig)
    assert "issue:102" not in refs(rig)


def test_B57_triage_excludes_the_large_label(tmp_path):
    """B57: the large label excludes an issue from triage."""
    rig = triage_rig(
        tmp_path,
        issues=(gh_issue(101), gh_issue(102, labels=("harness-ok", "large"))),
    )

    discover(rig.ctx, mode="triage", target=None, lens=None)

    assert "issue:101" in refs(rig)
    assert "issue:102" not in refs(rig)


def test_B57_triage_excludes_the_architecture_label(tmp_path):
    """B57: the architecture label excludes an issue from triage."""
    rig = triage_rig(
        tmp_path,
        issues=(gh_issue(101), gh_issue(102, labels=("harness-ok", "architecture"))),
    )

    discover(rig.ctx, mode="triage", target=None, lens=None)

    assert "issue:101" in refs(rig)
    assert "issue:102" not in refs(rig)


def test_B58_triage_excludes_an_issue_without_the_allowlist_label(tmp_path):
    rig = triage_rig(
        tmp_path,
        issues=(gh_issue(101), gh_issue(102, labels=("bug",))),
    )

    discover(rig.ctx, mode="triage", target=None, lens=None)

    assert "issue:101" in refs(rig)
    assert "issue:102" not in refs(rig)


def test_B58_ignore_allowlist_admits_an_issue_without_the_allowlist_label(tmp_path):
    rig = triage_rig(
        tmp_path,
        issues=(gh_issue(101), gh_issue(102, labels=("bug",))),
    )

    discover(rig.ctx, mode="triage", target=None, lens=None, ignore_allowlist=True)

    assert "issue:101" in refs(rig)
    assert "issue:102" in refs(rig)


# --------------------------------------------------------------------------
# B59 - audit mode
# --------------------------------------------------------------------------


def test_B59_audit_mode_raises_not_implemented_and_makes_no_model_call(tmp_path):
    rig = triage_rig(tmp_path, issues=(gh_issue(101),))

    with pytest.raises(NotImplementedInDelivery1) as excinfo:
        discover(rig.ctx, mode="audit", target=None, lens=None)

    assert "not implemented in delivery 1" in str(excinfo.value)
    assert rig.runner.calls == 0
    assert rig.store.list_work_items() == []


# --------------------------------------------------------------------------
# B60 - propose
# --------------------------------------------------------------------------


def proposable(tmp_path, *, propose_text=None, fullsend="false"):
    gh = FakeGh(issues=(gh_issue(816),))
    env_path = write_env(tmp_path, fullsend=fullsend)
    config = load_config(env_path, environ={})
    clock = FrozenClock(FROZEN_AT)
    store = Store(config.db_path, clock)
    store.migrate()
    item_id = store.create_work_item(
        kind="issue", external_ref="issue:816", title="bundle size check misreports esm"
    )
    fixtures_dir = write_runner_fixtures(
        tmp_path / "runner-fixtures",
        propose_text=propose_text if propose_text is not None else work_package_text(),
    )
    log: list[str] = []
    runner = CountingRunner(FakeRunner(fixtures_dir), log)
    clones = FakeClones(config.runs_dir)
    ctx = build_context(
        config,
        run_id=f"item-{item_id}",
        runner=runner,
        gh=gh,
        clock=clock,
        store=store,
        clones=clones,
    )
    return Rig(ctx, runner, log, config, store, clones, gh), item_id


def test_B60_propose_writes_the_spec_sets_spec_path_and_transitions_to_proposed(tmp_path):
    rig, item_id = proposable(tmp_path)

    path = propose(rig.ctx, item_id)

    expected = rig.config.runs_dir / f"item-{item_id}" / "spec" / f"{item_id}.md"
    assert Path(path).resolve() == expected.resolve()
    assert expected.is_file()

    item = rig.store.get_work_item(item_id)
    assert item.state == "proposed"
    assert item.spec_path is not None
    assert Path(item.spec_path).resolve() == expected.resolve()

    written = expected.read_text(encoding="utf-8")
    for heading in (
        "## Issue",
        "## Diagnosis",
        "## Approach",
        "## Slices",
        "## Behaviors",
        "## Acceptance criteria",
        "## Decisions",
        "## Open questions",
        "## Touched paths",
        "## Risks",
    ):
        assert heading in written


def test_B60_proposing_an_already_proposed_item_raises_illegal_transition(tmp_path):
    rig, item_id = proposable(tmp_path)
    propose(rig.ctx, item_id)

    with pytest.raises(IllegalTransition):
        propose(rig.ctx, item_id)

    assert rig.store.get_work_item(item_id).state == "proposed"


# --------------------------------------------------------------------------
# B61 - the fullsend fitness gate
# --------------------------------------------------------------------------


def gate_config(tmp_path, *, fullsend: str):
    return load_config(write_env(tmp_path, fullsend=fullsend), environ={})


ALL_TRUE = {"F1": True, "F2": True, "F3": True, "F4": True, "F5": True}


def flipped(key: str) -> dict:
    out = dict(ALL_TRUE)
    out[key] = False
    return out


def test_B61_fullsend_gate_is_all_true_only_when_all_five_conditions_hold(tmp_path):
    config = gate_config(tmp_path, fullsend="true")
    pkg = parse_work_package(work_package_text(n_slices=3, n_behaviors=15))

    assert evaluate_fullsend_gate(pkg, config) == ALL_TRUE


def test_B61_fewer_than_three_slices_flips_only_F1(tmp_path):
    config = gate_config(tmp_path, fullsend="true")
    pkg = parse_work_package(work_package_text(n_slices=2, n_behaviors=15))

    assert evaluate_fullsend_gate(pkg, config) == flipped("F1")


def test_B61_fewer_than_fifteen_behaviors_flips_only_F2(tmp_path):
    config = gate_config(tmp_path, fullsend="true")
    pkg = parse_work_package(work_package_text(n_slices=3, n_behaviors=14))

    assert evaluate_fullsend_gate(pkg, config) == flipped("F2")


def test_B61_a_non_empty_open_questions_section_flips_only_F3(tmp_path):
    config = gate_config(tmp_path, fullsend="true")
    pkg = parse_work_package(
        work_package_text(open_questions=("Should the ceiling move to 300kb?",))
    )

    assert evaluate_fullsend_gate(pkg, config) == flipped("F3")


def test_B61_a_prisma_touched_path_flips_only_F4(tmp_path):
    config = gate_config(tmp_path, fullsend="true")
    pkg = parse_work_package(
        work_package_text(touched=("src/lib/bundle.ts", "prisma/schema.prisma"))
    )

    assert evaluate_fullsend_gate(pkg, config) == flipped("F4")


def test_B61_a_workflow_touched_path_flips_only_F4(tmp_path):
    config = gate_config(tmp_path, fullsend="true")
    pkg = parse_work_package(
        work_package_text(touched=("src/lib/bundle.ts", ".github/workflows/ci.yml"))
    )

    assert evaluate_fullsend_gate(pkg, config) == flipped("F4")


def test_B61_a_predeploy_touched_path_flips_only_F4(tmp_path):
    config = gate_config(tmp_path, fullsend="true")
    pkg = parse_work_package(
        work_package_text(touched=("src/lib/bundle.ts", "backend/scripts/predeploy.sh"))
    )

    assert evaluate_fullsend_gate(pkg, config) == flipped("F4")


def test_B61_a_migrations_touched_path_flips_only_F4(tmp_path):
    config = gate_config(tmp_path, fullsend="true")
    pkg = parse_work_package(
        work_package_text(touched=("src/lib/bundle.ts", "migrations/0007_add_index.sql"))
    )

    assert evaluate_fullsend_gate(pkg, config) == flipped("F4")


def test_B61_fullsend_disabled_in_config_flips_only_F5(tmp_path):
    config = gate_config(tmp_path, fullsend="false")
    pkg = parse_work_package(work_package_text(n_slices=3, n_behaviors=15))

    assert evaluate_fullsend_gate(pkg, config) == flipped("F5")


def test_B61_implement_records_every_failing_gate_key_in_decisions_md(tmp_path, monkeypatch):
    rig, item_id = proposable(
        tmp_path,
        fullsend="false",
        propose_text=work_package_text(
            n_slices=2,
            n_behaviors=10,
            open_questions=("Does the ceiling apply to the legacy bundle?",),
        ),
    )
    propose(rig.ctx, item_id)
    approved_item(rig, item_id)

    def gate_runner(clone, *, baseline, runner=None):
        rig.log.append(f"gates(baseline={baseline})")
        return list(GREEN)

    stub_implement_side_effects(monkeypatch, rig.log, gate_runner=gate_runner)

    implement(rig.ctx, item_id)

    decisions = rig.run_dir(f"item-{item_id}") / "DECISIONS.md"
    assert decisions.is_file()
    text = decisions.read_text(encoding="utf-8")
    for key in ("F1", "F2", "F3", "F5"):
        assert key in text


# --------------------------------------------------------------------------
# B62 - baseline gates first
# --------------------------------------------------------------------------


def test_B62_implement_runs_the_baseline_gate_sequence_before_any_other_call(
    tmp_path, monkeypatch
):
    rig, item_id = proposable(tmp_path)
    propose(rig.ctx, item_id)
    approved_item(rig, item_id)
    rig.log.clear()

    def gate_runner(clone, *, baseline, runner=None):
        rig.log.append(f"gates(baseline={baseline})")
        return list(GREEN)

    stub_implement_side_effects(monkeypatch, rig.log, gate_runner=gate_runner)

    implement(rig.ctx, item_id)

    assert rig.log, "implement made no observable call at all"
    assert rig.log[0] == "gates(baseline=True)"
    assert rig.log.count("gates(baseline=True)") == 1


def test_B62_implement_records_the_baseline_gate_result_separately(tmp_path, monkeypatch):
    rig, item_id = proposable(tmp_path)
    propose(rig.ctx, item_id)
    approved_item(rig, item_id)

    def gate_runner(clone, *, baseline, runner=None):
        rig.log.append(f"gates(baseline={baseline})")
        return list(GREEN)

    stub_implement_side_effects(monkeypatch, rig.log, gate_runner=gate_runner)

    implement(rig.ctx, item_id)

    baseline_path = rig.run_dir(f"item-{item_id}") / "gates" / "baseline.json"
    assert baseline_path.is_file()
    recorded = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert isinstance(recorded, list)
    assert [entry["name"] for entry in recorded] == ["npm run lint"]
    assert [entry["exit_code"] for entry in recorded] == [0]


# --------------------------------------------------------------------------
# B63 - repeated failure signature
# --------------------------------------------------------------------------


def test_B63_a_repeated_gate_failure_signature_stops_after_the_second_red(
    tmp_path, monkeypatch
):
    rig, item_id = proposable(tmp_path)
    propose(rig.ctx, item_id)
    approved_item(rig, item_id)
    rig.log.clear()

    def gate_runner(clone, *, baseline, runner=None):
        rig.log.append(f"gates(baseline={baseline})")
        return list(GREEN) if baseline else list(RED)

    stub_implement_side_effects(monkeypatch, rig.log, gate_runner=gate_runner)

    try:
        implement(rig.ctx, item_id)
    except HarnessError:
        pass

    post_change_gate_runs = rig.log.count("gates(baseline=False)")
    assert post_change_gate_runs == 2, (
        "max_retries_gates is 2, so a non-repeating red would allow three post-change "
        f"gate runs; a repeated signature must stop at two, saw {post_change_gate_runs}"
    )

    last = len(rig.log) - 1 - rig.log[::-1].index("gates(baseline=False)")
    after = [c for c in rig.log[last + 1 :] if c.startswith("run:") or c.startswith("gates(")]
    assert after == []

    assert rig.store.get_work_item(item_id).state == "blocked"


def test_B63_a_red_with_a_different_signature_is_retried_and_can_reach_green(
    tmp_path, monkeypatch
):
    rig, item_id = proposable(tmp_path)
    propose(rig.ctx, item_id)
    approved_item(rig, item_id)
    rig.log.clear()
    post_change: list[int] = []

    def gate_runner(clone, *, baseline, runner=None):
        rig.log.append(f"gates(baseline={baseline})")
        if baseline:
            return list(GREEN)
        post_change.append(1)
        return list(RED) if len(post_change) == 1 else list(GREEN)

    stub_implement_side_effects(monkeypatch, rig.log, gate_runner=gate_runner)

    implement(rig.ctx, item_id)

    assert rig.log.count("gates(baseline=False)") == 2
    assert rig.store.get_work_item(item_id).state == "implementing"
    assert (rig.run_dir(f"item-{item_id}") / "gates" / "final.json").is_file()


# --------------------------------------------------------------------------
# B64 - forbidden diffs
# --------------------------------------------------------------------------


def test_B64_a_changed_ci_workflow_path_blocks_the_item_and_is_never_committed(
    tmp_path, monkeypatch
):
    rig, item_id = proposable(tmp_path)
    propose(rig.ctx, item_id)
    approved_item(rig, item_id)
    rig.log.clear()

    def gate_runner(clone, *, baseline, runner=None):
        rig.log.append(f"gates(baseline={baseline})")
        return list(GREEN)

    stub_implement_side_effects(
        monkeypatch,
        rig.log,
        gate_runner=gate_runner,
        changed=["src/lib/bundle.ts", ".github/workflows/x.yml"],
    )

    try:
        implement(rig.ctx, item_id)
    except HarnessError:
        pass

    assert rig.store.get_work_item(item_id).state == "blocked"
    assert "commit" not in rig.log
    assert rig.log.count("gates(baseline=False)") == 0


def test_B64_a_clean_change_set_is_not_blocked(tmp_path, monkeypatch):
    rig, item_id = proposable(tmp_path)
    propose(rig.ctx, item_id)
    approved_item(rig, item_id)
    rig.log.clear()

    def gate_runner(clone, *, baseline, runner=None):
        rig.log.append(f"gates(baseline={baseline})")
        return list(GREEN)

    stub_implement_side_effects(
        monkeypatch, rig.log, gate_runner=gate_runner, changed=["src/lib/bundle.ts"]
    )

    implement(rig.ctx, item_id)

    assert rig.store.get_work_item(item_id).state == "implementing"
    assert "commit" in rig.log


# --------------------------------------------------------------------------
# State machine guard on implement (SPEC 5.2.2 / B11)
# --------------------------------------------------------------------------


def test_B11_implement_on_an_item_that_is_not_approved_raises_illegal_transition(
    tmp_path, monkeypatch
):
    rig, item_id = proposable(tmp_path)
    propose(rig.ctx, item_id)
    rig.log.clear()

    def gate_runner(clone, *, baseline, runner=None):
        rig.log.append(f"gates(baseline={baseline})")
        return list(GREEN)

    stub_implement_side_effects(monkeypatch, rig.log, gate_runner=gate_runner)

    with pytest.raises(IllegalTransition):
        implement(rig.ctx, item_id)

    assert rig.store.get_work_item(item_id).state == "proposed"


# --------------------------------------------------------------------------
# B64 — the diff-text arms: continue-on-error, .skip(, a raised timeout
# --------------------------------------------------------------------------


def _implement_with_diff(tmp_path, monkeypatch, added, removed):
    rig, item_id = proposable(tmp_path)
    propose(rig.ctx, item_id)
    approved_item(rig, item_id)
    rig.log.clear()

    def gate_runner(clone, *, baseline, runner=None):
        rig.log.append(f"gates(baseline={baseline})")
        return list(GREEN)

    stub_implement_side_effects(
        monkeypatch, rig.log, gate_runner=gate_runner, changed=["src/lib/bundle.ts"]
    )
    monkeypatch.setattr(implement_mod, "DIFF_LINES", lambda lease, changed: (added, removed))
    try:
        implement(rig.ctx, item_id)
    except HarnessError:
        pass
    return rig, item_id


def test_B64_a_diff_adding_continue_on_error_blocks_the_item(tmp_path, monkeypatch):
    rig, item_id = _implement_with_diff(
        tmp_path, monkeypatch, added=["    continue-on-error: true"], removed=[]
    )
    assert rig.store.get_work_item(item_id).state == "blocked"
    assert "commit" not in rig.log


def test_B64_a_diff_adding_a_skipped_test_blocks_the_item(tmp_path, monkeypatch):
    rig, item_id = _implement_with_diff(
        tmp_path, monkeypatch, added=["it.skip('flaky', () => {})"], removed=[]
    )
    assert rig.store.get_work_item(item_id).state == "blocked"
    assert "commit" not in rig.log


def test_B64_a_diff_raising_a_timeout_blocks_the_item(tmp_path, monkeypatch):
    rig, item_id = _implement_with_diff(
        tmp_path, monkeypatch, added=["  timeout: 60000,"], removed=["  timeout: 10000,"]
    )
    assert rig.store.get_work_item(item_id).state == "blocked"
    assert "commit" not in rig.log


def test_B64_a_diff_lowering_a_timeout_is_not_blocked(tmp_path, monkeypatch):
    rig, item_id = _implement_with_diff(
        tmp_path, monkeypatch, added=["  timeout: 5000,"], removed=["  timeout: 10000,"]
    )
    assert rig.store.get_work_item(item_id).state == "implementing"
    assert "commit" in rig.log


# --------------------------------------------------------------------------
# B62 — dependency install precedes the baseline and is recorded separately
# --------------------------------------------------------------------------


def test_B62_dependency_install_runs_before_the_baseline_and_is_recorded(tmp_path, monkeypatch):
    rig, item_id = proposable(tmp_path)
    propose(rig.ctx, item_id)
    approved_item(rig, item_id)
    rig.log.clear()

    def gate_runner(clone, *, baseline, runner=None):
        rig.log.append(f"gates(baseline={baseline})")
        return list(GREEN)

    def prepare(clone, *, runner=None):
        rig.log.append("prepare")
        return [GateResult("prepare: npm ci", ("npm", "ci"), 0, "added 1200 packages", "")]

    stub_implement_side_effects(
        monkeypatch, rig.log, gate_runner=gate_runner, changed=["src/lib/bundle.ts"]
    )
    monkeypatch.setattr(implement_mod, "PREPARE", prepare)

    implement(rig.ctx, item_id)

    assert rig.log.index("prepare") < rig.log.index("gates(baseline=True)")
    recorded = json.loads((rig.ctx.run_dir / "gates" / "prepare.json").read_text("utf-8"))
    assert [r["name"] for r in recorded] == ["prepare: npm ci"]
    baseline = json.loads((rig.ctx.run_dir / "gates" / "baseline.json").read_text("utf-8"))
    assert all(r["name"] != "prepare: npm ci" for r in baseline)


# --------------------------------------------------------------------------
# B69 — halt is honoured mid-stage: clone released, item left resumable
# --------------------------------------------------------------------------


def test_B69_a_halt_file_appearing_mid_implement_releases_the_clone_and_resets_the_item(
    tmp_path, monkeypatch
):
    from harness.errors import Halted

    rig, item_id = proposable(tmp_path)
    propose(rig.ctx, item_id)
    approved_item(rig, item_id)
    rig.log.clear()

    def gate_runner(clone, *, baseline, runner=None):
        rig.log.append(f"gates(baseline={baseline})")
        rig.ctx.config.halt_file.write_text("", encoding="utf-8")
        return list(GREEN)

    stub_implement_side_effects(monkeypatch, rig.log, gate_runner=gate_runner)

    with pytest.raises(Halted):
        implement(rig.ctx, item_id)

    assert rig.store.get_work_item(item_id).state == "approved"
    assert rig.log.count("gates(baseline=True)") == 1
    assert "commit" not in rig.log
    assert [keep for _lease, keep in rig.clones.released] == [False]
