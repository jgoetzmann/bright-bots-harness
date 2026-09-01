"""FakeRunner determinism.

Section 4.3 assigns this file "Fake determinism" and gives it no B number; the
contract is HARNESS-SPEC section 5.4.2 plus RUN-DECISIONS "Runner". B24 covers
the one behavior number that reaches here (get_runner selecting FakeRunner).

The fixtures below are written inline. Nothing under `tests/fixtures/` is read.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

import harness.runner.fake as fake_module
from harness.runner import RunRequest, RunResult
from harness.runner.fake import FakeRunner

PROPOSE_PAYLOAD = {
    "ok": True,
    "text": "# fix(scripts): bundle size check misreports esm chunks\n",
    "turns": 12,
    "cost_usd": 0.1875,
    "allowance_pct": 1.8,
    "duration_ms": 20100,
    "session_id": "fixture-propose",
    "exit_code": 0,
    "transcript": [
        {"role": "user", "content": "propose a fix for issue 816"},
        {"role": "assistant", "content": "here is the work package"},
    ],
    "error": None,
}

FAILED_PAYLOAD = {
    "ok": False,
    "text": "",
    "turns": 1,
    "cost_usd": None,
    "allowance_pct": None,
    "duration_ms": 900,
    "session_id": None,
    "exit_code": 1,
    "transcript": [],
    "error": "the model gave up on the gate loop",
}


def write_fixture(directory: Path, stage: str, payload: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stage}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8", newline="\n")
    return path


def request_for(stage: str, cwd: Path, *, prompt: str = "the prompt") -> RunRequest:
    return RunRequest(
        stage=stage,
        prompt=prompt,
        system_prompt=None,
        allowed_tools=("Read", "Glob", "Grep"),
        disallowed_tools=("Bash", "Edit", "Write", "WebFetch", "WebSearch"),
        max_turns=30,
        cwd=cwd,
        timeout_s=1200,
    )


@pytest.fixture
def fixtures_dir(tmp_path) -> Path:
    directory = tmp_path / "fixtures"
    write_fixture(directory, "propose", PROPOSE_PAYLOAD)
    write_fixture(directory, "implement", FAILED_PAYLOAD)
    return directory


# --------------------------------------------------------------------------
# Section 5.4.2 - the fixture is returned verbatim
# --------------------------------------------------------------------------


def test_s542_the_fixture_is_returned_verbatim(fixtures_dir, tmp_path):
    """5.4.2: every RunResult field comes straight from <stage>.json."""
    runner = FakeRunner(fixtures_dir)

    result = runner.run(request_for("propose", tmp_path))

    assert isinstance(result, RunResult)
    assert result.ok is True
    assert result.text == PROPOSE_PAYLOAD["text"]
    assert result.turns == 12
    assert result.cost_usd == pytest.approx(0.1875)
    assert result.allowance_pct == pytest.approx(1.8)
    assert result.duration_ms == 20100
    assert result.session_id == "fixture-propose"
    assert result.exit_code == 0
    assert result.error is None


def test_s542_the_transcript_is_a_tuple_of_dicts_in_order(fixtures_dir, tmp_path):
    """5.4.2: the JSON transcript list becomes RunResult's tuple, order preserved."""
    runner = FakeRunner(fixtures_dir)

    result = runner.run(request_for("propose", tmp_path))

    assert isinstance(result.transcript, tuple)
    assert result.transcript == (
        {"role": "user", "content": "propose a fix for issue 816"},
        {"role": "assistant", "content": "here is the work package"},
    )


def test_b24_the_fake_runner_is_named_fake(fixtures_dir):
    """B24: the Runner protocol's name attribute is "fake"."""
    assert FakeRunner(fixtures_dir).name == "fake"


# --------------------------------------------------------------------------
# Section 5.4.2 - determinism
# --------------------------------------------------------------------------


def test_s542_two_runs_of_one_instance_are_identical(fixtures_dir, tmp_path):
    """5.4.2: the same fixture yields an equal RunResult every time."""
    runner = FakeRunner(fixtures_dir)
    request = request_for("propose", tmp_path)

    first = runner.run(request)
    second = runner.run(request)

    assert first == second
    assert first.transcript == second.transcript


def test_s542_two_instances_over_one_fixture_dir_agree(fixtures_dir, tmp_path):
    """5.4.2: determinism survives re-construction, so CI reruns match."""
    first = FakeRunner(fixtures_dir).run(request_for("propose", tmp_path))
    second = FakeRunner(fixtures_dir).run(request_for("propose", tmp_path))

    assert first == second


def test_s542_the_result_does_not_depend_on_the_request(fixtures_dir, tmp_path):
    """5.4.2: the fake keys off the stage alone; prompt and turns cannot change it."""
    runner = FakeRunner(fixtures_dir)
    other_cwd = tmp_path / "elsewhere"
    other_cwd.mkdir()

    first = runner.run(request_for("propose", tmp_path, prompt="one prompt"))
    second_request = RunRequest(
        stage="propose",
        prompt="a completely different prompt",
        system_prompt="and a system prompt",
        allowed_tools=(),
        disallowed_tools=(),
        max_turns=1,
        cwd=other_cwd,
        timeout_s=1,
    )
    second = runner.run(second_request)

    assert first == second


def test_s542_each_stage_reads_its_own_fixture(fixtures_dir, tmp_path):
    """5.4.2: <stage>.json is the key, so two stages give two different results."""
    runner = FakeRunner(fixtures_dir)

    propose = runner.run(request_for("propose", tmp_path))
    implement = runner.run(request_for("implement", tmp_path))

    assert propose.text != implement.text
    assert propose.ok is True
    assert implement.ok is False


# --------------------------------------------------------------------------
# Section 5.4.2 - failure paths
# --------------------------------------------------------------------------


def test_s542_a_missing_fixture_is_not_ok_and_names_the_stage(fixtures_dir, tmp_path):
    """5.4.2: a stage with no fixture returns a failed RunResult, never raises."""
    runner = FakeRunner(fixtures_dir)

    result = runner.run(request_for("nosuchstage", tmp_path))

    assert result.ok is False
    assert result.exit_code == 1
    assert result.text == ""
    assert result.error is not None
    assert "nosuchstage" in result.error
    assert "no fixture" in result.error.lower()


def test_s542_a_missing_fixture_directory_is_not_ok(tmp_path):
    """5.4.2: an absent fixtures directory is a failed run, not an OSError."""
    runner = FakeRunner(tmp_path / "does" / "not" / "exist")

    result = runner.run(request_for("propose", tmp_path))

    assert result.ok is False
    assert result.error is not None
    assert "propose" in result.error


def test_s542_a_missing_fixture_is_deterministic_too(fixtures_dir, tmp_path):
    """5.4.2: the failure path is as repeatable as the success path."""
    runner = FakeRunner(fixtures_dir)

    first = runner.run(request_for("nosuchstage", tmp_path))
    second = runner.run(request_for("nosuchstage", tmp_path))

    assert first == second


def test_s542_a_fixture_recording_a_failure_round_trips(fixtures_dir, tmp_path):
    """5.4.2: ok=false and an error string are returned as written."""
    runner = FakeRunner(fixtures_dir)

    result = runner.run(request_for("implement", tmp_path))

    assert result.ok is False
    assert result.exit_code == 1
    assert result.error == "the model gave up on the gate loop"
    assert result.transcript == ()
    assert result.cost_usd is None
    assert result.allowance_pct is None


def test_s542_one_missing_stage_does_not_poison_the_others(fixtures_dir, tmp_path):
    """5.4.2: a failed lookup leaves the runner usable for a stage that does exist."""
    runner = FakeRunner(fixtures_dir)

    runner.run(request_for("nosuchstage", tmp_path))
    result = runner.run(request_for("propose", tmp_path))

    assert result.ok is True
    assert result.session_id == "fixture-propose"


# --------------------------------------------------------------------------
# Section 5.4.2 - no network, no subprocess (source-level, as section 9 does it)
# --------------------------------------------------------------------------


def test_s542_the_fake_module_names_neither_subprocess_nor_urllib():
    """5.4.2: FakeRunner MUST NOT import subprocess or urllib."""
    source = inspect.getsource(fake_module)

    assert "subprocess" not in source
    assert "urllib" not in source


def test_s542_the_fake_module_binds_neither_subprocess_nor_urllib():
    """5.4.2: the same rule, checked against the imported module's namespace."""
    assert not hasattr(fake_module, "subprocess")
    assert not hasattr(fake_module, "urllib")
    assert not hasattr(fake_module, "requests")
    assert not hasattr(fake_module, "socket")
