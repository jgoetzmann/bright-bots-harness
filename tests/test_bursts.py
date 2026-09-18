"""B472-B479: a burst of merges loses no approval (D78).

The incident, in one paragraph. The operator merged five proposal pull requests within 44
seconds. Each merge pushed to `proposals/**` and started an `implement` run in the
`harness-ledger` group. `cancel-in-progress` is false, so GitHub keeps one run in progress and
**one** pending, and each new arrival cancelled the previously pending one: four of five runs
died without executing a step. The approve step was gated on `github.event_name == 'push'` and
read its own push diff, which exists only inside that event payload — so the four cancelled runs
never recorded their approvals, and all five items stayed at `stage:needs-approval` with nothing
to recover them.

The cancellation is not the bug. The bug is that a cancelled run held the only copy of work
nothing else would redo. These tests pin the repair: the committed proposal file is the durable
record, and every run reconciles against it.
"""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

import pytest

import harness.__main__ as cli
from harness.errors import IllegalTransition
from harness.store import Store

from tests.test_cli import forbid_network, open_store, write_d2_repo

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
LEDGER_WRITERS = ("discover.yml", "implement.yml", "feedback.yml")
APPROVE_STEP = "Approve merged proposals (every proposal on main)"


def _wf(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def seed(tmp_path: Path, count: int, *, state: str = "proposed") -> list[int]:
    """`count` work items walked to `state`, each with its own external reference."""
    store = open_store(tmp_path)
    store.migrate()
    ids: list[int] = []
    walk = {
        "proposed": ["proposed"],
        "approved": ["proposed", "approved"],
        "blocked": ["proposed", "blocked"],
        "implementing": ["proposed", "approved", "implementing"],
    }[state]
    for index in range(count):
        item_id = store.create_work_item(
            kind="issue",
            external_ref=f"issue:{816 + index}",
            title=f"item {816 + index} needs work",
        )
        for step in walk:
            store.transition(item_id, step, reason="test setup")
        ids.append(item_id)
    store.close()
    return ids


def write_proposals(tmp_path: Path, item_ids) -> list[str]:
    """The committed `proposals/<id>-<slug>.md` a merged proposal leaves on main (D46)."""
    directory = tmp_path / "proposals"
    directory.mkdir(parents=True, exist_ok=True)
    names = []
    for item_id in item_ids:
        name = f"{item_id}-bundle-size-check.md"
        (directory / name).write_text(f"# plan for {item_id}\n", encoding="utf-8", newline="\n")
        names.append(name)
    return names


def states(tmp_path: Path) -> dict[int, str]:
    store = open_store(tmp_path)
    found = {int(item.id): item.state for item in store.list_work_items()}
    store.close()
    return found


def approve_merged(tmp_path, monkeypatch, capsys) -> tuple[int, dict]:
    """`harness --json approve --merged`, as the exit code and the payload it printed."""
    monkeypatch.chdir(tmp_path)
    forbid_network(monkeypatch)
    capsys.readouterr()
    code = cli.main(["--json", "approve", "--merged"])
    return code, json.loads(capsys.readouterr().out)


# --------------------------------------------------------------------------------------
# B472-B473 - the incident, and a second pass over it
# --------------------------------------------------------------------------------------


def test_B472_five_proposals_merged_at_once_are_all_approved_by_one_pass(
    tmp_path, monkeypatch, capsys
):
    """B472: the incident reproduced. Five items at `proposed` with five files on main, and one
    pass — the run that survived the burst — approves all five.

    This is what makes the four cancelled runs harmless: whichever run lives checks out a `main`
    that already carries every file, so nothing depends on the diff that died with them.
    """
    write_d2_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init"]) == 0
    ids = seed(tmp_path, 5)
    write_proposals(tmp_path, ids)
    assert set(states(tmp_path).values()) == {"proposed"}

    code, payload = approve_merged(tmp_path, monkeypatch, capsys)

    assert code == 0
    assert sorted(payload["approved"]) == sorted(ids)
    assert payload["skipped"] == {} and payload["failed"] == {}
    assert set(states(tmp_path).values()) == {"approved"}, "not one approval was lost"


def test_B473_a_second_pass_transitions_nothing_and_comments_nothing(
    tmp_path, monkeypatch, capsys
):
    """B473: it runs on every implement and feedback run, several times a day, for ever. A pass
    that re-transitioned an item already approved would comment on its issue each time (B101) and
    would fight whatever moved it on."""
    write_d2_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init"]) == 0
    ids = seed(tmp_path, 3)
    write_proposals(tmp_path, ids)
    approve_merged(tmp_path, monkeypatch, capsys)
    before = states(tmp_path)

    code, payload = approve_merged(tmp_path, monkeypatch, capsys)

    assert code == 0
    assert payload["approved"] == [], "nothing is approved twice"
    assert payload["skipped"] == {} and payload["failed"] == {}
    assert states(tmp_path) == before


# --------------------------------------------------------------------------------------
# B474-B475 - what it must not touch
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("state", ["blocked", "implementing", "approved"])
def test_B474_a_stopped_or_in_flight_item_with_its_file_on_main_is_never_resurrected(
    tmp_path, monkeypatch, capsys, state
):
    """B474: the state machine is the guard, and it is the whole guard. An item stopped after its
    proposal merged keeps its file on main for ever, so a reconciler that looked at files alone
    would restart it on every run, indefinitely. Only `proposed -> approved` is ever made."""
    write_d2_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init"]) == 0
    ids = seed(tmp_path, 2, state=state)
    write_proposals(tmp_path, ids)
    before = states(tmp_path)

    code, payload = approve_merged(tmp_path, monkeypatch, capsys)

    assert code == 0
    assert payload["approved"] == []
    assert states(tmp_path) == before, f"a {state} item was touched"


def test_B475_a_file_with_no_item_and_an_item_with_no_file_are_each_skipped_and_named(
    tmp_path, monkeypatch, capsys
):
    """B475: a proposal whose pull request is still open has no file on main yet, and must wait
    for the merge — that is gate 1. A file whose item no longer exists is not an approval to
    make. Neither is an error, and the report names what it did not do."""
    write_d2_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init"]) == 0
    ids = seed(tmp_path, 3)
    with_file, without_file = ids[:1], ids[1:]
    write_proposals(tmp_path, with_file)
    # A file for an item that does not exist at all, and one whose name is not a proposal.
    (tmp_path / "proposals" / "9999-gone.md").write_text("x\n", encoding="utf-8")
    (tmp_path / "proposals" / "notes.md").write_text("x\n", encoding="utf-8")
    (tmp_path / "proposals" / f"{without_file[0]}-nope.txt").write_text("x\n", encoding="utf-8")

    code, payload = approve_merged(tmp_path, monkeypatch, capsys)

    assert code == 0
    assert payload["approved"] == with_file
    assert sorted(payload["skipped"]) == sorted(str(n) for n in without_file)
    for why in payload["skipped"].values():
        assert why == "no proposal file on main"
    after = states(tmp_path)
    assert after[with_file[0]] == "approved"
    assert all(after[n] == "proposed" for n in without_file), "a .txt is not a proposal"


def test_B476_one_item_that_cannot_transition_does_not_strand_the_others(
    tmp_path, monkeypatch, capsys
):
    """B476: this pass is what recovers a burst, so stopping at the first failure would lose the
    rest of it — the failure mode the whole decision exists to remove. It is warned about rather
    than raised: the exit code belongs to B489."""
    write_d2_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init"]) == 0
    ids = seed(tmp_path, 4)
    write_proposals(tmp_path, ids)
    doomed = ids[1]
    real = Store.transition

    def sometimes_refuse(self, item_id, to_state, *, reason):
        if int(item_id) == doomed:
            raise IllegalTransition(f"item {item_id} cannot move (test)")
        return real(self, item_id, to_state, reason=reason)

    monkeypatch.setattr(Store, "transition", sometimes_refuse)
    forbid_network(monkeypatch)
    capsys.readouterr()

    code = cli.main(["--json", "approve", "--merged"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 0, "one stuck item must not fail the step the keyword sweep runs after"
    assert f"::warning::harness approve --merged: item {doomed}" in captured.err
    assert sorted(payload["approved"]) == sorted(n for n in ids if n != doomed)
    assert list(payload["failed"]) == [str(doomed)]
    after = states(tmp_path)
    assert after[doomed] == "proposed"
    assert all(after[n] == "approved" for n in ids if n != doomed), "the others still went"


def test_B476_merged_with_an_item_id_is_refused_rather_than_guessed_at(
    tmp_path, monkeypatch, capsys
):
    """The two forms mean different things, so asking for both is a mistake worth naming."""
    write_d2_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init"]) == 0
    forbid_network(monkeypatch)
    capsys.readouterr()

    assert cli.main(["approve", "4", "--merged"]) == 1
    assert "takes no item id" in capsys.readouterr().err

    assert cli.main(["approve"]) == 1
    assert "needs an item id, or --merged" in capsys.readouterr().err


# --------------------------------------------------------------------------------------
# B477-B478 - the workflows
# --------------------------------------------------------------------------------------


def test_B477_implement_approves_on_every_run_and_reads_no_push_diff():
    """B477: the load-bearing evidence, because the fakes replace GitHub and cannot show this.

    The step must no longer be gated on the event, and must read no diff: a diff is what died
    with the cancelled runs. Its position is pinned too — sync-fork, then approve, then dispatch
    (B127/B150).
    """
    text = _wf("implement.yml")

    assert "github.event.before" not in text, "the push diff is what could not survive the run"
    assert "harness approve --merged" in text
    step = text.split(f"- name: {APPROVE_STEP}", 1)[1].split("- name:", 1)[0]
    assert "github.event_name" not in step, "the approval must not be gated on the event"
    assert "git diff" not in step and "git show" not in step

    def at(pattern: str) -> int:
        found = re.search(pattern, text)
        assert found, f"implement.yml no longer carries {pattern!r}"
        return found.start()

    assert at(r"harness sync-fork") < at(re.escape(APPROVE_STEP)) < at(r"harness dispatch")
    # The trigger itself is unchanged: a merged proposal still starts a run (B127).
    assert "proposals/**" in text


def test_B478_the_sweep_reconciles_too_and_ops_classifies_both_steps():
    """B478: `implement.yml` only wakes inside the daily window, so a burst merged at 20:00 UTC
    would wait until 11:23 the next day. `feedback.yml` runs three-hourly on weekdays and now
    carries the same step, in the same position.

    Both run before anything is spent, so both are retryable — and `ops.yml` decides that by
    name, which is why the name has to match.
    """
    feedback = _wf("feedback.yml")

    assert "harness approve --merged" in feedback
    assert f"- name: {APPROVE_STEP}" in feedback

    def at(text: str, pattern: str) -> int:
        found = re.search(pattern, text)
        assert found, pattern
        return found.start()

    assert at(feedback, r"harness sync-fork") < at(feedback, re.escape(APPROVE_STEP))
    assert at(feedback, re.escape(APPROVE_STEP)) < at(feedback, r"harness dispatch")

    ops = _wf("ops.yml")
    allow = ops.split("RETRYABLE_STEPS = [", 1)[1].split("];", 1)[0]
    assert f"'{APPROVE_STEP}'" in allow, "ops.yml must classify the step it may re-run"
    assert "Approve merged proposals (push to proposals/**)" not in ops, "the old name is gone"

    # The guard that makes the allow-list safe: every step of both workflows is classified.
    for name in LEDGER_WRITERS:
        labels = re.findall(r"^      - name: (.+)$", _wf(name), re.M)
        assert APPROVE_STEP in labels or name == "discover.yml"


def test_B489_one_stuck_item_never_takes_the_keyword_surface_down():
    """B489: in `feedback.yml` the order is sync-fork → approve → dispatch → **Sweep keywords**,
    and a step that exits non-zero skips every later step in the job. One locked, transferred or
    403-ing issue would then stop `/harness halt`, `/harness resume` and `/harness block` being
    read at all, on every three-hourly run, while `ack.yml` kept acknowledging — and `ops.yml`
    rescues none of it, because its retry pattern matches neither an `IllegalTransition` nor a
    403. `fix: a warning must not take the fleet down (#27)` is the same rule.
    """
    source = inspect.getsource(cli._approve_merged)

    assert "return EXIT_ERROR" not in source, "the exit code is what the step's success is read off"
    assert "::warning::" in source, "the failure is still reported, in the form Actions surfaces"

    feedback = _wf("feedback.yml")
    step = feedback.split(f"- name: {APPROVE_STEP}", 1)[1].split("- name:", 1)[0]
    assert "continue-on-error: true" in step, "nor may an unexpected failure skip the sweep"

    sweep = feedback.split("- name: Sweep keywords", 1)[1].split("run:", 1)[0]
    assert "steps.halt.outputs.halted != 'true'" in sweep
    assert "approve" not in sweep, "the sweep must not be conditioned on the approval"


def test_B479_the_ledger_still_has_exactly_one_writer_group():
    """B479: the proof obligation behind D78. B118 says every workflow that writes
    state/ledger.json shares one group and never cancels a run; the repair had to live inside
    jobs that already hold that lock rather than beside them."""
    declaring = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        if re.search(r"group:\s*['\"]?harness-ledger['\"]?", text):
            declaring.append(path.name)

    assert sorted(declaring) == sorted(LEDGER_WRITERS), (
        f"the ledger group is declared by {declaring}; it must be exactly the three writers"
    )
    for name in LEDGER_WRITERS:
        text = _wf(name)
        assert re.search(r"cancel-in-progress:\s*false\b", text), name
        assert not re.search(r"cancel-in-progress:\s*true\b", text), name
        assert "state/ledger.json" in text

    # And no workflow outside the group commits the ledger.
    for path in sorted(WORKFLOWS.glob("*.yml")):
        if path.name in LEDGER_WRITERS:
            continue
        text = path.read_text(encoding="utf-8")
        assert "Commit state/ledger.json" not in text, (
            f"{path.name} writes the ledger without holding the lock (B118)"
        )
