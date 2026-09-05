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
from types import SimpleNamespace

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
WEEKLY_CAP_USD=25.00
PER_CALL_CAP_USD=3.00
MAX_CONCURRENT_ITEMS=1
MAX_REVISE_CYCLES=3
FORK_REPO=
UPSTREAM_REPO=Bright-Bots-Initiative/brightboost
TRUST_FILE=.harness/trust.txt
NOTIFY_POLL_HOURS=3
MAX_SUBISSUES=8
SELF_REPO=jgoetzmann/bright-bots-harness
TRACKING_ISSUE=
STORE_BACKEND=sqlite
WEEKLY_USAGE_STOP_PCT=90
SESSION_USAGE_STOP_PCT=70
OVERRUN_PCT=10
RUN_WINDOW_START=
RUN_WINDOW_END=
MODEL=opus
EFFORT=xhigh
INBOX_ISSUE=0
AUDIT_CAP_USD=20.00
SUGGEST_MAX_PER_RUN=5
COMMENT_UPSTREAM=true
ASK_CAP_USD=0.50
ASK_MAX_PER_DAY=20
SUGGEST_MIN_HEADROOM_PCT=50
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

    def issues_assigned_to(self, login: str, *, state: str = "open") -> list[dict]:
        self.calls.append(("issues_assigned_to", login, state))
        handle = str(login or "").lstrip("@").lower()
        out = []
        for item in self._issues:
            holders = [h for h in (item.get("assignees") or []) if h]
            if item.get("assignee"):
                holders.append(item["assignee"])
            if any(str((h or {}).get("login") or "").lower() == handle for h in holders):
                out.append(item)
        return out

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
    # B219: propose now takes and releases a read-only clone of its own; this test is about
    # the one implement holds when the halt lands.
    rig.clones.released.clear()

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


# --------------------------------------------------------------------------
# B103 / B104 — the proposal schema is bounded; touched_paths must exist upstream
# --------------------------------------------------------------------------


def _valid_front() -> dict:
    return {
        "issue": 1,
        "upstream_issue": 816,
        "title": "fix(scripts): bundle size check misses esm output",
        "kind": "fix",
        "slices": 1,
        "risk": "low",
        "touched_paths": ["scripts/check-bundle-size.js"],
        "depends_on": [],
        "estimated_turns": 40,
        "gate_expectation": "green",
        "baseline_red": [],
    }


def test_B103_a_valid_front_matter_produces_no_errors():
    """B103: every field present, every enum closed, every path real -> no validation error."""
    from harness.stages.propose import validate_proposal

    errors = validate_proposal(
        _valid_front(), path_exists=lambda p: True, max_turns=80, open_issue=lambda n: n == 1
    )
    assert errors == []


@pytest.mark.parametrize(
    ("mutation", "fragment"),
    [
        ({"kind": "feature"}, "kind"),
        ({"risk": "extreme"}, "risk"),
        ({"slices": 6}, "slices"),
        ({"estimated_turns": 0}, "estimated_turns"),
        ({"gate_expectation": "maybe"}, "gate_expectation"),
        ({"unexpected": 1}, "unknown key"),
        ({"title": ""}, "title"),
    ],
)
def test_B103_a_closed_enum_range_or_unknown_key_fails_validation(mutation, fragment):
    """B103: the front matter is a bounded schema; every enum closed, unknown keys rejected."""
    from harness.stages.propose import validate_proposal

    front = _valid_front()
    front.update(mutation)
    errors = validate_proposal(
        front, path_exists=lambda p: True, max_turns=80, open_issue=lambda n: n == 1
    )
    assert errors, "expected a validation error"
    assert any(fragment in e for e in errors), errors


def test_B103_a_missing_required_key_fails_validation():
    """B103: every field required."""
    from harness.stages.propose import validate_proposal

    front = _valid_front()
    del front["risk"]
    errors = validate_proposal(
        front, path_exists=lambda p: True, max_turns=80, open_issue=lambda n: n == 1
    )
    assert any("risk" in e for e in errors), errors


def test_B104_a_touched_path_absent_from_the_product_repo_fails_validation():
    """B104: touched_paths entries are checked against the product repository at the base."""
    from harness.stages.propose import validate_proposal

    front = _valid_front()
    front["touched_paths"] = ["scripts/check-bundle-size.js", "src/does/not/exist.ts"]
    seen: list[str] = []

    def path_exists(path: str) -> bool:
        seen.append(path)
        return path == "scripts/check-bundle-size.js"

    errors = validate_proposal(
        front, path_exists=path_exists, max_turns=80, open_issue=lambda n: n == 1
    )
    assert seen == ["scripts/check-bundle-size.js", "src/does/not/exist.ts"]
    assert len(errors) == 1 and "src/does/not/exist.ts" in errors[0]


def test_B104_every_touched_path_is_checked_and_an_empty_list_is_rejected():
    """B104: the check is per entry; an empty list is not 'nothing to check', it is invalid."""
    from harness.stages.propose import validate_proposal

    front = _valid_front()
    front["touched_paths"] = []
    errors = validate_proposal(
        front, path_exists=lambda p: True, max_turns=80, open_issue=lambda n: n == 1
    )
    assert any("touched_paths" in e for e in errors), errors


# --------------------------------------------------------------------------------------
# B218 - deny_read_paths names every credential store a stage could otherwise read (D36)
# --------------------------------------------------------------------------------------


def _deny(tmp_path: Path):
    from harness.stages import deny_read_paths

    return deny_read_paths(load_config(write_env(tmp_path), environ={})), tmp_path


def test_b218_deny_read_paths_covers_the_env_the_ledger_and_the_pin(tmp_path):
    """B218: `.env` holds the PAT and the OAuth token; state/ and .harness/ hold the rest."""
    paths, root = _deny(tmp_path)
    root = Path(root).resolve().as_posix()

    assert f"{root}/.env" in paths
    assert f"{root}/local/.env" in paths
    assert f"{root}/.harness/**" in paths
    assert f"{root}/state/**" in paths


def test_b218_deny_read_paths_covers_the_operator_home_credential_stores(tmp_path):
    """B218: an unconfined Read reaches ~/.ssh and ~/.claude as easily as the repository."""
    paths, _ = _deny(tmp_path)
    home = Path.home().resolve().as_posix()

    for relative in (".claude/**", ".ssh/**", ".aws/**", ".config/gh/**"):
        assert f"{home}/{relative}" in paths, relative


def test_b218_deny_read_paths_are_absolute_posix_and_never_double_slashed(tmp_path):
    """B218: the CLI enforces bare absolute paths only; a relative or // rule matches nothing."""
    paths, _ = _deny(tmp_path)

    assert paths, "an empty deny list would confine nothing"
    for path in paths:
        assert not path.startswith("//"), path
        assert "\\" not in path, path
        assert Path(path.replace("/**", "")).is_absolute(), path


def test_b218_run_model_hands_the_deny_list_to_every_request(tmp_path):
    """B218: the confinement is run_model's, so no stage can forget to ask for it."""
    from harness import stages as stages_mod

    rig = make_rig(tmp_path, run_id="deny", gh=FakeGh())
    stages_mod.run_model(
        rig.ctx,
        stage="propose",
        item_id=None,
        prompt="p",
        allowed_tools=("Read", "Glob", "Grep"),
        disallowed_tools=(),
        timeout_s=10,
        cwd=rig.config.runs_dir,
    )

    request = rig.runner.requests[-1]
    assert request.deny_read == stages_mod.deny_read_paths(rig.config)
    assert request.deny_read, "an empty deny list would confine nothing"


# --------------------------------------------------------------------------------------
# B219 - propose reads the product repository, so it is given one (D37)
# --------------------------------------------------------------------------------------


def test_b219_propose_acquires_a_clone_and_runs_the_model_inside_it(tmp_path):
    """B219: the prompt says "Read the repository at the current working directory" and
    forbids listing a path it has not seen. An empty run directory supports neither."""
    rig, item_id = proposable(tmp_path)

    propose(rig.ctx, item_id)

    assert len(rig.clones.acquired) == 1, "propose made no clone"
    lease = rig.clones.acquired[0]
    request = rig.runner.requests[-1]
    assert Path(request.cwd) == Path(lease.path)
    assert tuple(Path(d) for d in request.add_dirs) == (Path(lease.path),)


def test_b219_propose_releases_the_clone_without_keeping_it(tmp_path):
    """B219: read-only. implement acquires its own clone from the base the package pins."""
    rig, item_id = proposable(tmp_path)

    propose(rig.ctx, item_id)

    assert rig.clones.released == [(rig.clones.acquired[0], False)]


def test_b219_a_failed_propose_still_releases_the_clone(tmp_path):
    """B219: MAX_CONCURRENT_CLONES is 1, so a leaked lease would wedge the next item."""
    rig, item_id = proposable(tmp_path, propose_text="no proposal block here at all")

    with pytest.raises(HarnessError):
        propose(rig.ctx, item_id)

    assert rig.clones.released == [(rig.clones.acquired[0], False)]


def test_b219_propose_records_the_base_it_read(tmp_path):
    """B219: the package cites one tree; DECISIONS names which, so a reviewer can check it."""
    rig, item_id = proposable(tmp_path)

    propose(rig.ctx, item_id)

    decisions = (rig.config.runs_dir / f"item-{item_id}" / "DECISIONS.md").read_text(
        encoding="utf-8"
    )
    assert rig.clones.acquired[0].base_sha in decisions


# --------------------------------------------------------------------------------------
# B222 / B223 - a deletion is a change, and an empty change set is a failure (D42, D43)
# --------------------------------------------------------------------------------------


def test_b222_implement_asks_for_the_change_set_not_the_format_list():
    """B222: the two lists differ by exactly the deletions, and implement needs the wider one."""
    from harness import prettier
    from harness.stages import implement as implement_mod

    assert implement_mod.CHANGED_PATHS is prettier.all_changed_paths
    assert implement_mod.CHANGED_PATHS is not prettier.changed_paths


def test_b223_an_implement_call_that_changes_nothing_blocks_the_item(tmp_path, monkeypatch):
    """B223: the run that found this committed nothing, packaged zero patches, printed
    "implemented item 1" and exited 0."""
    rig, item_id = proposable(tmp_path)
    propose(rig.ctx, item_id)
    approved_item(rig, item_id)

    def changed_paths(clone, base_sha, git_runner=None):
        return []

    stub_implement_side_effects(monkeypatch, rig.log, gate_runner=lambda *a, **k: list(GREEN))
    monkeypatch.setattr(implement_mod, "CHANGED_PATHS", changed_paths)

    with pytest.raises(HarnessError):
        implement(rig.ctx, item_id)

    assert rig.store.get_work_item(item_id).state == "blocked"
    assert "commit" not in rig.log


def test_b223_the_block_reason_names_the_paths_the_package_expected(tmp_path, monkeypatch):
    """B223: "nothing changed" is only actionable next to what was supposed to change."""
    rig, item_id = proposable(tmp_path)
    propose(rig.ctx, item_id)
    approved_item(rig, item_id)

    stub_implement_side_effects(monkeypatch, rig.log, gate_runner=lambda *a, **k: list(GREEN))
    monkeypatch.setattr(implement_mod, "CHANGED_PATHS", lambda *a, **k: [])

    with pytest.raises(HarnessError):
        implement(rig.ctx, item_id)

    decisions = (rig.config.runs_dir / f"item-{item_id}" / "DECISIONS.md").read_text(
        encoding="utf-8"
    )
    assert "left the tree unchanged" in decisions
    assert "src/" in decisions


def test_b223_an_empty_change_set_never_reaches_the_post_change_gates(tmp_path, monkeypatch):
    """B223: gates on an uncommitted tree were what made the empty run look green."""
    rig, item_id = proposable(tmp_path)
    propose(rig.ctx, item_id)
    approved_item(rig, item_id)
    rig.log.clear()

    def gate_runner(clone, *, baseline, runner=None):
        rig.log.append(f"gates(baseline={baseline})")
        return list(GREEN)

    stub_implement_side_effects(monkeypatch, rig.log, gate_runner=gate_runner)
    monkeypatch.setattr(implement_mod, "CHANGED_PATHS", lambda *a, **k: [])

    with pytest.raises(HarnessError):
        implement(rig.ctx, item_id)

    assert rig.log.count("gates(baseline=False)") == 0
    assert rig.log.count("gates(baseline=True)") == 1


# --------------------------------------------------------------------------------------
# B219 - the clone propose acquires must not cost it its way back (review findings)
# --------------------------------------------------------------------------------------


def test_b219_a_clone_that_cannot_be_acquired_returns_the_item_to_discovered(
    tmp_path, monkeypatch
):
    """B219: `_enter` moves the item to `proposing` before the clone is asked for. A clone
    failure after that would strand it there, mid-flight, with nothing to resume from."""
    from harness.errors import CloneError

    rig, item_id = proposable(tmp_path)

    def refuse(item, **kwargs):
        raise CloneError("git clone failed (128): network unreachable")

    monkeypatch.setattr(rig.clones, "acquire", refuse)

    with pytest.raises(CloneError):
        propose(rig.ctx, item_id)

    assert rig.store.get_work_item(item_id).state == "discovered"
    assert rig.runner.calls == 0, "nothing should have been spent"


def test_b219_a_failed_preflight_also_returns_the_item(tmp_path, monkeypatch):
    """B219: same reasoning one step earlier -- preflight runs after the transition too."""
    from harness.errors import PreflightFailed

    rig, item_id = proposable(tmp_path)
    monkeypatch.setattr(rig.clones, "preflight", lambda: ["free disk 1.0 GB below MIN 5.0"])

    with pytest.raises(PreflightFailed):
        propose(rig.ctx, item_id)

    assert rig.store.get_work_item(item_id).state == "discovered"


def test_b219_an_item_already_proposing_returns_to_discovered_not_to_proposing(
    tmp_path, monkeypatch
):
    """B219: `_enter` deliberately answers "discovered" for an item found mid-flight, because
    `proposing` is not a state it can be returned to. Recomputing that answer from
    `item.state` inside the leased body silently got it wrong for exactly this case."""
    from harness.errors import CloneError

    rig, item_id = proposable(tmp_path)
    rig.store.transition(item_id, "proposing", reason="a previous run died here")
    monkeypatch.setattr(
        rig.clones, "acquire", lambda item, **kw: (_ for _ in ()).throw(CloneError("boom"))
    )

    with pytest.raises(CloneError):
        propose(rig.ctx, item_id)

    assert rig.store.get_work_item(item_id).state == "discovered"


# --------------------------------------------------------------------------------------
# B226 - the work package survives the run that produced it (D46)
# --------------------------------------------------------------------------------------


PROPOSAL_ON_DISK = """---
issue: 7
upstream_issue: 633
title: "chore: delete the orphan"
kind: chore
---

# chore: delete the orphan

## Acceptance criteria
- the file is gone
"""


def test_b226_the_recorded_spec_is_used_when_it_is_still_here(tmp_path):
    """B226: unchanged in local mode, where propose and implement share a disk."""
    from harness.stages.propose import work_package_text

    spec = tmp_path / "runs" / "item-7" / "spec" / "7.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# from the spec file\n", encoding="utf-8")
    item = SimpleNamespace(id=7, spec_path=str(spec))

    assert work_package_text(item, repo_root=tmp_path) == "# from the spec file\n"


def test_b226_a_dead_runners_spec_path_falls_back_to_the_committed_proposal(tmp_path):
    """B226: the first live Actions run died here. `runs/` is ephemeral per runner and gate 1
    -- a human merging the proposal PR -- necessarily puts propose and implement in different
    runs, so the recorded absolute path belongs to a machine that no longer exists."""
    from harness.stages.propose import work_package_text

    (tmp_path / "proposals").mkdir()
    (tmp_path / "proposals" / "7-chore-delete-the-orphan.md").write_text(
        PROPOSAL_ON_DISK, encoding="utf-8"
    )
    item = SimpleNamespace(id=7, spec_path="/home/runner/work/gone/runs/item-7/spec/7.md")

    text = work_package_text(item, repo_root=tmp_path)

    assert text.startswith("# chore: delete the orphan")
    assert "issue: 7" not in text, "the front matter must be stripped"


def test_b226_the_proposal_is_found_by_id_not_by_deriving_the_slug(tmp_path):
    """B226: the filename's slug comes from the proposal's own front-matter title, not the
    item's, so the two do not match and the lookup has to glob the id prefix."""
    from harness.stages.propose import work_package_text

    (tmp_path / "proposals").mkdir()
    (tmp_path / "proposals" / "7-a-slug-nobody-could-derive.md").write_text(
        PROPOSAL_ON_DISK, encoding="utf-8"
    )
    item = SimpleNamespace(id=7, spec_path="", title="a completely different title")

    assert "delete the orphan" in work_package_text(item, repo_root=tmp_path)


def test_b226_a_similar_id_does_not_match(tmp_path):
    """B226: item 7 must not pick up item 70's proposal."""
    from harness.stages.propose import work_package_text

    (tmp_path / "proposals").mkdir()
    (tmp_path / "proposals" / "70-someone-elses.md").write_text(PROPOSAL_ON_DISK, encoding="utf-8")
    item = SimpleNamespace(id=7, spec_path="")

    with pytest.raises(HarnessError):
        work_package_text(item, repo_root=tmp_path)


def test_b226_neither_source_names_both_places_it_looked(tmp_path):
    """B226: the old message named only the dead path, which reads like a local bug."""
    from harness.stages.propose import work_package_text

    item = SimpleNamespace(id=7, spec_path="/home/runner/work/gone/runs/item-7/spec/7.md")

    with pytest.raises(HarnessError) as excinfo:
        work_package_text(item, repo_root=tmp_path)

    message = str(excinfo.value)
    assert "runs/item-7/spec/7.md" in message
    assert "proposals/7-*.md" in message
    assert "does not survive between runs" in message


def test_b226_strip_front_matter_leaves_a_body_without_one_alone():
    """B226: the recorded spec has no front matter; only the published proposal does."""
    from harness.stages.propose import strip_front_matter

    body = "# just a body\n\nwith text\n"

    assert strip_front_matter(body) == body


def test_b226_strip_front_matter_drops_the_blank_line_after_the_block():
    """B226: the published proposal is front matter + a blank line + the body."""
    from harness.stages.propose import strip_front_matter

    assert strip_front_matter(PROPOSAL_ON_DISK).startswith("# chore: delete the orphan")


def test_b226_implement_reads_the_proposal_when_the_spec_is_gone(tmp_path, monkeypatch):
    """B226: the end-to-end shape of the defect -- implement on a fresh runner."""
    rig, item_id = proposable(tmp_path)
    propose(rig.ctx, item_id)
    approved_item(rig, item_id)

    item = rig.store.get_work_item(item_id)
    spec = Path(item.spec_path)
    proposals = Path(rig.config.repo_root) / "proposals"
    proposals.mkdir(parents=True, exist_ok=True)
    (proposals / f"{item_id}-whatever.md").write_text(
        "---\nissue: 1\n---\n\n" + spec.read_text(encoding="utf-8"), encoding="utf-8"
    )
    spec.unlink()  # the runner that wrote it is gone

    stub_implement_side_effects(monkeypatch, rig.log, gate_runner=lambda *a, **k: list(GREEN))
    monkeypatch.setattr(
        implement_mod, "CHANGED_PATHS", lambda *a, **k: ["src/lib/bundle.ts"]
    )

    implement(rig.ctx, item_id)

    assert rig.store.get_work_item(item_id).state == "implementing"


def test_b226_a_re_proposal_wins_over_the_older_file(tmp_path):
    """B226: a re-propose under a changed title lands a second proposals/<id>-*.md beside the
    first. The later one is the one that was approved."""
    import os
    import time
    from harness.stages.propose import work_package_text

    proposals = tmp_path / "proposals"
    proposals.mkdir()
    old = proposals / "7-the-first-attempt.md"
    old.write_text("---\nissue: 7\n---\n\n# stale\n", encoding="utf-8")
    new = proposals / "7-the-second-attempt.md"
    new.write_text("---\nissue: 7\n---\n\n# current\n", encoding="utf-8")
    os.utime(old, (time.time() - 600, time.time() - 600))
    item = SimpleNamespace(id=7, spec_path="")

    assert work_package_text(item, repo_root=tmp_path).strip() == "# current"


def test_b226_equal_mtimes_fall_back_to_the_later_name(tmp_path):
    """B226: a git checkout stamps every file with the same mtime, so the tie-break has to be
    deterministic rather than arbitrary."""
    import os
    from harness.stages.propose import work_package_text

    proposals = tmp_path / "proposals"
    proposals.mkdir()
    first = proposals / "7-aaa.md"
    first.write_text("---\nissue: 7\n---\n\n# aaa\n", encoding="utf-8")
    second = proposals / "7-zzz.md"
    second.write_text("---\nissue: 7\n---\n\n# zzz\n", encoding="utf-8")
    stamp = first.stat().st_mtime
    os.utime(second, (stamp, stamp))
    item = SimpleNamespace(id=7, spec_path="")

    assert work_package_text(item, repo_root=tmp_path).strip() == "# zzz"


def test_b226_a_non_integer_id_does_not_escape_the_error(tmp_path):
    """B226: `int(item.id)` sat outside the guard, so a bad id raised past deliver's except."""
    from harness.stages.propose import work_package_text

    item = SimpleNamespace(id="not-a-number", spec_path="")

    with pytest.raises(HarnessError):
        work_package_text(item, repo_root=tmp_path)


# --------------------------------------------------------------------------------------
# B233 - assigning the bot to an issue queues it (D53)
# --------------------------------------------------------------------------------------


def _assigned_issue(number: int, login: str, *, title: str = "a ticket", labels=()):
    return gh_issue(number, title=title, labels=list(labels)) | {
        "assignees": [{"login": login}],
        "assignee": {"login": login},
    }


def test_b233_the_machine_account_is_the_fork_owner(tmp_path):
    """B233: derived, not configured -- a fork the account does not own is not one it can push
    to, so FORK_REPO already names the account and a second key could only disagree."""
    from harness.stages.discover import machine_account

    assert machine_account(SimpleNamespace(fork_repo="jgoetzmann-bot/brightboost")) == (
        "jgoetzmann-bot"
    )
    assert machine_account(SimpleNamespace(fork_repo="")) == ""


def test_b233_an_issue_assigned_to_us_is_not_excluded_and_needs_no_label(tmp_path):
    """B233: assignment is the statement the allowlist label makes, made on the ticket itself.
    Requiring both would mean a maintainer had to say the same thing twice."""
    from harness.stages.discover import _rejection_reason

    kwargs = dict(number=7, claimed=set(), allowlist_label="harness-ok", ignore_allowlist=False)

    assert _rejection_reason(_assigned_issue(7, "JGoetzmann-Bot"), machine="jgoetzmann-bot", **kwargs) is None


def test_b233_an_issue_assigned_to_somebody_else_is_still_excluded(tmp_path):
    """B233: B55 is unchanged -- somebody else's assignment is somebody else's work."""
    from harness.stages.discover import _rejection_reason

    kwargs = dict(number=7, claimed=set(), allowlist_label="harness-ok", ignore_allowlist=False)

    assert _rejection_reason(_assigned_issue(7, "someone"), machine="jgoetzmann-bot", **kwargs) == (
        "assigned"
    )


def test_b233_an_unassigned_issue_still_needs_the_allowlist_label(tmp_path):
    """B233: the label route is untouched for everything nobody assigned."""
    from harness.stages.discover import _rejection_reason

    reason = _rejection_reason(
        gh_issue(7, labels=()), number=7, claimed=set(), allowlist_label="harness-ok",
        ignore_allowlist=False, machine="jgoetzmann-bot",
    )

    assert reason == "missing allowlist label harness-ok"


def test_b233_an_excluded_label_still_wins_over_assignment(tmp_path):
    """B233: `intern-starter` is work reserved for a human learning the codebase. Assigning the
    bot to one does not make it the bot's."""
    from harness.stages.discover import _rejection_reason

    issue = _assigned_issue(7, "jgoetzmann-bot", labels=["intern-starter"])
    reason = _rejection_reason(
        issue, number=7, claimed=set(), allowlist_label="harness-ok",
        ignore_allowlist=False, machine="jgoetzmann-bot",
    )

    assert reason == "excluded label intern-starter"


def test_b233_assigned_discover_queues_each_one_and_makes_no_model_call(tmp_path):
    """B233: which issues are assigned is a fact, not a judgement, so no model is asked."""
    gh = FakeGh(issues=(_assigned_issue(901, "jgoetzmann-bot", title="one"),
                        _assigned_issue(902, "jgoetzmann-bot", title="two")))
    rig = make_rig(tmp_path, run_id="assigned", gh=gh)
    rig.ctx.config.__dict__["fork_repo"] = "jgoetzmann-bot/brightboost"

    created = discover(rig.ctx, mode="assigned", target=None, lens=None)

    assert len(created) == 2
    assert rig.runner.calls == 0, "assigned discovery must not spend"
    refs = {rig.store.get_work_item(i).external_ref for i in created}
    assert refs == {"issue:901", "issue:902"}


def test_b233_an_issue_already_queued_is_left_alone(tmp_path):
    """B233: the sweep runs every three hours; it must be safe to run repeatedly."""
    gh = FakeGh(issues=(_assigned_issue(901, "jgoetzmann-bot"),))
    rig = make_rig(tmp_path, run_id="assigned", gh=gh)
    rig.ctx.config.__dict__["fork_repo"] = "jgoetzmann-bot/brightboost"
    first = discover(rig.ctx, mode="assigned", target=None, lens=None)

    second = discover(rig.ctx, mode="assigned", target=None, lens=None)

    assert len(first) == 1
    assert second == []


def test_b233_no_fork_configured_says_so_rather_than_queueing_nothing(tmp_path):
    """B233: with no FORK_REPO there is no login to be assigned to, and silence would read as
    "nothing is assigned"."""
    rig = make_rig(tmp_path, run_id="assigned", gh=FakeGh())
    rig.ctx.config.__dict__["fork_repo"] = ""

    with pytest.raises(HarnessError) as excinfo:
        discover(rig.ctx, mode="assigned", target=None, lens=None)

    assert "FORK_REPO" in str(excinfo.value)
