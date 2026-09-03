"""B24-B30. HARNESS-SPEC section 5.4 and RUN-DECISIONS "Runner".

argv construction and result parsing only. No `claude` process is ever started:
every runner here is built with the injectable ``spawn`` kwarg, which records
the call and returns a canned ``subprocess.CompletedProcess``.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
from pathlib import Path

import pytest

from harness.errors import ConfigError
from harness.runner import RunRequest, RunResult, get_runner
from harness.runner.cli import ClaudeCliRunner
from harness.runner.fake import FakeRunner

SK_ANT_SECRET = "sk-ant-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5"  # 30 chars after the prefix


class SpawnRecorder:
    """A stand-in for ``subprocess.run`` that records the call and answers canned."""

    def __init__(self, *, returncode: int = 0, stdout: str = "{}", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.calls: list[dict] = []

    def __call__(self, *args, **kwargs) -> subprocess.CompletedProcess:
        # Section 5.4.3 fixes "argv as a list" but not whether it is passed
        # positionally or as args=; accept either.
        raw_argv = args[0] if args else kwargs.get("args")
        argv = list(raw_argv)
        self.calls.append({"argv": argv, "kwargs": dict(kwargs)})
        return subprocess.CompletedProcess(
            args=argv, returncode=self.returncode, stdout=self.stdout, stderr=self.stderr
        )

    @property
    def argv(self) -> list[str]:
        assert self.calls, "spawn was never called"
        return self.calls[-1]["argv"]

    @property
    def kwargs(self) -> dict:
        assert self.calls, "spawn was never called"
        return self.calls[-1]["kwargs"]

    @property
    def env(self) -> dict:
        env = self.kwargs.get("env")
        assert env is not None, "no env was passed to spawn"
        return dict(env)


def json_stdout(**fields) -> str:
    return json.dumps(fields)


def minimal_request(cwd: Path) -> RunRequest:
    return RunRequest(
        stage="propose",
        prompt="do the thing",
        system_prompt=None,
        allowed_tools=("Read", "Glob", "Grep"),
        disallowed_tools=(),
        max_turns=30,
        cwd=cwd,
        timeout_s=1200,
    )


def maximal_request(cwd: Path, extra_a: Path, extra_b: Path) -> RunRequest:
    return RunRequest(
        stage="implement",
        prompt="implement it",
        system_prompt="you are the harness",
        allowed_tools=("Read", "Edit", "Write", "Bash", "Glob", "Grep"),
        disallowed_tools=("WebFetch", "WebSearch"),
        max_turns=80,
        cwd=cwd,
        timeout_s=3600,
        add_dirs=(extra_a, extra_b),
    )


# --------------------------------------------------------------------------
# B24 - get_runner maps the backend
# --------------------------------------------------------------------------


def test_b24_get_runner_returns_the_cli_runner_for_backend_cli(sample_config):
    """B24: backend="cli" selects ClaudeCliRunner."""
    config = dataclasses.replace(sample_config, backend="cli")

    runner = get_runner(config)

    assert isinstance(runner, ClaudeCliRunner)
    assert runner.name == "cli"


def test_b24_get_runner_returns_the_fake_runner_for_backend_fake(sample_config):
    """B24: backend="fake" selects FakeRunner."""
    config = dataclasses.replace(sample_config, backend="fake")

    runner = get_runner(config)

    assert isinstance(runner, FakeRunner)
    assert runner.name == "fake"


@pytest.mark.parametrize("backend", ["api", "", "CLI", "claude", "http"])
def test_b24_an_unknown_backend_raises_config_error(sample_config, backend):
    """B24: only two backends ship in Delivery 1; anything else is a ConfigError."""
    config = dataclasses.replace(sample_config, backend=backend)

    with pytest.raises(ConfigError):
        get_runner(config)


# --------------------------------------------------------------------------
# B25 - argv is exactly section 5.4.3, in that order
# --------------------------------------------------------------------------


def test_b25_minimal_argv_matches_section_5_4_3(tmp_path):
    """B25: with no optionals, argv is the fixed prefix, then --, then the prompt."""
    spawn = SpawnRecorder(stdout=json_stdout(result="ok"))
    runner = ClaudeCliRunner(spawn=spawn)

    runner.run(minimal_request(tmp_path))

    assert spawn.argv == [
        "claude",
        "--print",
        "--output-format",
        "json",
        "--max-turns",
        "30",
        "--permission-mode",
        "acceptEdits",
        "--allowed-tools",
        "Read,Glob,Grep",
        "--",
        "do the thing",
    ]


def test_b25_full_argv_matches_section_5_4_3(tmp_path):
    """B25: every optional flag appears once, in the documented order."""
    extra_a = tmp_path / "shared"
    extra_b = tmp_path / "docs"
    spawn = SpawnRecorder(stdout=json_stdout(result="ok"))
    runner = ClaudeCliRunner(spawn=spawn)

    runner.run(maximal_request(tmp_path, extra_a, extra_b))

    assert spawn.argv == [
        "claude",
        "--print",
        "--output-format",
        "json",
        "--max-turns",
        "80",
        "--permission-mode",
        "acceptEdits",
        "--allowed-tools",
        "Read,Edit,Write,Bash,Glob,Grep",
        "--disallowed-tools",
        "WebFetch,WebSearch",
        "--system-prompt",
        "you are the harness",
        "--add-dir",
        str(extra_a),
        "--add-dir",
        str(extra_b),
        "--",
        "implement it",
    ]


def test_b25_empty_disallowed_tools_omits_the_flag(tmp_path):
    """B25: --disallowed-tools is omitted when the tuple is empty, not passed empty."""
    spawn = SpawnRecorder(stdout=json_stdout(result="ok"))
    runner = ClaudeCliRunner(spawn=spawn)

    runner.run(minimal_request(tmp_path))

    assert "--disallowed-tools" not in spawn.argv
    assert "" not in spawn.argv


def test_b25_a_none_system_prompt_omits_the_flag(tmp_path):
    """B25: --system-prompt is omitted when system_prompt is None."""
    spawn = SpawnRecorder(stdout=json_stdout(result="ok"))
    runner = ClaudeCliRunner(spawn=spawn)

    runner.run(minimal_request(tmp_path))

    assert "--system-prompt" not in spawn.argv
    assert "None" not in spawn.argv


def test_b25_empty_add_dirs_omits_the_flag(tmp_path):
    """B25: --add-dir appears once per directory, so zero directories means none."""
    spawn = SpawnRecorder(stdout=json_stdout(result="ok"))
    runner = ClaudeCliRunner(spawn=spawn)

    runner.run(minimal_request(tmp_path))

    assert "--add-dir" not in spawn.argv


def test_b25_the_prompt_is_the_last_argument_after_the_option_terminator(tmp_path):
    """B25: a prompt that looks like a flag still reaches claude as the prompt."""
    spawn = SpawnRecorder(stdout=json_stdout(result="ok"))
    runner = ClaudeCliRunner(spawn=spawn)
    request = dataclasses.replace(minimal_request(tmp_path), prompt="--not-a-flag please")

    runner.run(request)

    assert spawn.argv[-2:] == ["--", "--not-a-flag please"]


def test_b25_every_argv_entry_is_a_string(tmp_path):
    """B25: argv is a list of strings; an int max_turns would not survive exec."""
    spawn = SpawnRecorder(stdout=json_stdout(result="ok"))
    runner = ClaudeCliRunner(spawn=spawn)

    runner.run(minimal_request(tmp_path))

    assert all(isinstance(entry, str) for entry in spawn.argv)


def test_b25_the_claude_binary_is_injectable(tmp_path):
    """B25: claude_bin replaces argv[0] and nothing else."""
    spawn = SpawnRecorder(stdout=json_stdout(result="ok"))
    runner = ClaudeCliRunner(claude_bin="claude-2.1.251", spawn=spawn)

    runner.run(minimal_request(tmp_path))

    assert spawn.argv[0] == "claude-2.1.251"
    assert spawn.argv[1:5] == ["--print", "--output-format", "json", "--max-turns"]


def test_b25_the_subprocess_runs_in_the_request_cwd_with_the_request_timeout(tmp_path):
    """B25: section 5.4.3 fixes cwd=request.cwd, timeout=request.timeout_s, shell=False."""
    spawn = SpawnRecorder(stdout=json_stdout(result="ok"))
    runner = ClaudeCliRunner(spawn=spawn)
    clone = tmp_path / "clone"
    clone.mkdir()
    request = dataclasses.replace(minimal_request(tmp_path), cwd=clone, timeout_s=1200)

    runner.run(request)

    assert Path(spawn.kwargs["cwd"]) == clone
    assert spawn.kwargs["timeout"] == 1200
    assert spawn.kwargs.get("shell", False) is False


# --------------------------------------------------------------------------
# B26 - ANTHROPIC_API_KEY is removed from the child environment
# --------------------------------------------------------------------------


def test_b26_the_child_environment_omits_anthropic_api_key(tmp_path, monkeypatch):
    """B26: a key in the parent MUST NOT reach the subprocess."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", SK_ANT_SECRET)
    spawn = SpawnRecorder(stdout=json_stdout(result="ok"))
    runner = ClaudeCliRunner(spawn=spawn)

    runner.run(minimal_request(tmp_path))

    assert "ANTHROPIC_API_KEY" not in spawn.env
    assert SK_ANT_SECRET not in "".join(spawn.env.values())


def test_b26_the_rest_of_the_parent_environment_is_carried_through(tmp_path, monkeypatch):
    """B26: the child env is a copy of the parent's minus one key, not a blank env."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", SK_ANT_SECRET)
    monkeypatch.setenv("HARNESS_TEST_MARKER", "carried")
    spawn = SpawnRecorder(stdout=json_stdout(result="ok"))
    runner = ClaudeCliRunner(spawn=spawn)

    runner.run(minimal_request(tmp_path))

    assert spawn.env.get("HARNESS_TEST_MARKER") == "carried"
    assert "ANTHROPIC_API_KEY" not in spawn.env


def test_b26_an_absent_key_in_the_parent_is_not_an_error(tmp_path, monkeypatch):
    """B26: removing a key that was never set must not raise."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("HARNESS_TEST_MARKER", "carried")
    spawn = SpawnRecorder(stdout=json_stdout(result="ok"))
    runner = ClaudeCliRunner(spawn=spawn)

    result = runner.run(minimal_request(tmp_path))

    assert result.ok is True
    assert "ANTHROPIC_API_KEY" not in spawn.env
    assert spawn.env.get("HARNESS_TEST_MARKER") == "carried"


def test_b26_the_api_key_never_appears_in_argv_either(tmp_path, monkeypatch):
    """B26: the key is removed, not relocated onto the command line."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", SK_ANT_SECRET)
    spawn = SpawnRecorder(stdout=json_stdout(result="ok"))
    runner = ClaudeCliRunner(spawn=spawn)

    runner.run(minimal_request(tmp_path))

    assert SK_ANT_SECRET not in " ".join(spawn.argv)


# --------------------------------------------------------------------------
# B27 - the skip-permissions flags never appear
# --------------------------------------------------------------------------


def test_b27_no_skip_permissions_flag_in_a_minimal_invocation(tmp_path):
    """B27: invariant I-3; neither flag may appear in argv."""
    spawn = SpawnRecorder(stdout=json_stdout(result="ok"))
    runner = ClaudeCliRunner(spawn=spawn)

    runner.run(minimal_request(tmp_path))

    assert "--dangerously-skip-permissions" not in spawn.argv
    assert "--allow-dangerously-skip-permissions" not in spawn.argv
    assert not any("dangerously" in entry for entry in spawn.argv)


def test_b27_no_skip_permissions_flag_in_a_full_invocation(tmp_path):
    """B27: the optional flags do not open a path to a skip-permissions flag."""
    spawn = SpawnRecorder(stdout=json_stdout(result="ok"))
    runner = ClaudeCliRunner(spawn=spawn)

    runner.run(maximal_request(tmp_path, tmp_path / "a", tmp_path / "b"))

    assert not any("dangerously" in entry for entry in spawn.argv)
    assert not any("skip-permissions" in entry for entry in spawn.argv)


def test_b27_the_permission_mode_is_accept_edits(tmp_path):
    """B27: acceptEdits is the ceiling; it is passed explicitly, not defaulted."""
    spawn = SpawnRecorder(stdout=json_stdout(result="ok"))
    runner = ClaudeCliRunner(spawn=spawn)

    runner.run(minimal_request(tmp_path))

    index = spawn.argv.index("--permission-mode")
    assert spawn.argv[index + 1] == "acceptEdits"


# --------------------------------------------------------------------------
# B28 - a well-formed JSON result populates the numeric fields
# --------------------------------------------------------------------------


def test_b28_a_well_formed_result_populates_every_field(tmp_path):
    """B28: result/num_turns/total_cost_usd/duration_ms/session_id map onto RunResult."""
    payload = {
        "result": "the patch is ready",
        "num_turns": 7,
        "total_cost_usd": 0.4275,
        "duration_ms": 18234,
        "session_id": "01JABCDEF",
        "is_error": False,
    }
    spawn = SpawnRecorder(stdout=json.dumps(payload))
    runner = ClaudeCliRunner(spawn=spawn)

    result = runner.run(minimal_request(tmp_path))

    assert isinstance(result, RunResult)
    assert result.ok is True
    assert result.text == "the patch is ready"
    assert result.turns == 7
    assert result.cost_usd == pytest.approx(0.4275)
    assert result.duration_ms == 18234
    assert result.session_id == "01JABCDEF"
    assert result.exit_code == 0
    assert result.error is None


def test_b28_the_text_key_is_accepted_as_well_as_result(tmp_path):
    """B28: section 5.4.3 names result/text as alternatives for the body."""
    spawn = SpawnRecorder(stdout=json.dumps({"text": "from the text key", "num_turns": 2}))
    runner = ClaudeCliRunner(spawn=spawn)

    result = runner.run(minimal_request(tmp_path))

    assert result.text == "from the text key"
    assert result.turns == 2


def test_b28_allowance_pct_is_none_because_the_cli_never_reports_it(tmp_path):
    """B28: allowance_pct is not a CLI JSON field, so it is always None (RUN-DECISIONS)."""
    spawn = SpawnRecorder(
        stdout=json.dumps({"result": "ok", "num_turns": 1, "allowance_pct": 4.0})
    )
    runner = ClaudeCliRunner(spawn=spawn)

    result = runner.run(minimal_request(tmp_path))

    assert result.allowance_pct is None


def test_b28_the_transcript_carries_prompt_reply_and_raw_json(tmp_path):
    """B28: RUN-DECISIONS fixes the CLI transcript as user, assistant, raw."""
    payload = {"result": "the patch is ready", "num_turns": 7}
    spawn = SpawnRecorder(stdout=json.dumps(payload))
    runner = ClaudeCliRunner(spawn=spawn)

    result = runner.run(minimal_request(tmp_path))

    assert isinstance(result.transcript, tuple)
    assert len(result.transcript) == 3
    assert result.transcript[0] == {"role": "user", "content": "do the thing"}
    assert result.transcript[1] == {"role": "assistant", "content": "the patch is ready"}
    assert result.transcript[2] == {"raw": payload}


def test_b28_run_result_is_frozen(tmp_path):
    """B28: RunResult is a frozen dataclass (section 5.4.1)."""
    spawn = SpawnRecorder(stdout=json_stdout(result="ok"))
    runner = ClaudeCliRunner(spawn=spawn)

    result = runner.run(minimal_request(tmp_path))

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.ok = False


# --------------------------------------------------------------------------
# B29 - missing optional JSON fields yield None rather than raising
# --------------------------------------------------------------------------


def test_b29_missing_optional_fields_become_none(tmp_path):
    """B29: a result body with only 'result' still parses, with None everywhere else."""
    spawn = SpawnRecorder(stdout=json.dumps({"result": "done"}))
    runner = ClaudeCliRunner(spawn=spawn)

    result = runner.run(minimal_request(tmp_path))

    assert result.ok is True
    assert result.text == "done"
    assert result.turns is None
    assert result.cost_usd is None
    assert result.duration_ms is None
    assert result.session_id is None
    assert result.allowance_pct is None
    assert result.exit_code == 0


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"result": "done", "num_turns": None}, "turns"),
        ({"result": "done", "total_cost_usd": None}, "cost_usd"),
        ({"result": "done", "duration_ms": None}, "duration_ms"),
        ({"result": "done", "session_id": None}, "session_id"),
    ],
)
def test_b29_explicit_json_nulls_become_none(tmp_path, payload, field):
    """B29: a null in the JSON is the same as an absent key, never an exception."""
    spawn = SpawnRecorder(stdout=json.dumps(payload))
    runner = ClaudeCliRunner(spawn=spawn)

    result = runner.run(minimal_request(tmp_path))

    assert result.ok is True
    assert getattr(result, field) is None


def test_b29_an_empty_json_object_does_not_crash(tmp_path):
    """B29: every field is optional, so {} is a successful, empty result."""
    spawn = SpawnRecorder(stdout="{}")
    runner = ClaudeCliRunner(spawn=spawn)

    result = runner.run(minimal_request(tmp_path))

    assert result.ok is True
    assert result.exit_code == 0
    assert result.turns is None


def test_b29_unexpected_extra_json_keys_are_ignored(tmp_path):
    """B29: defensive parsing means an added upstream field is not a crash."""
    spawn = SpawnRecorder(
        stdout=json.dumps({"result": "done", "brand_new_field": {"nested": [1, 2]}})
    )
    runner = ClaudeCliRunner(spawn=spawn)

    result = runner.run(minimal_request(tmp_path))

    assert result.ok is True
    assert result.text == "done"


# --------------------------------------------------------------------------
# B30 - non-zero exit or unparseable stdout yields ok=False with a redacted tail
# --------------------------------------------------------------------------


def test_b30_a_non_zero_exit_is_not_ok(tmp_path):
    """B30: the exit code is carried through and ok is False."""
    spawn = SpawnRecorder(returncode=2, stdout="", stderr="claude: something went wrong\n")
    runner = ClaudeCliRunner(spawn=spawn)

    result = runner.run(minimal_request(tmp_path))

    assert result.ok is False
    assert result.exit_code == 2
    assert result.error is not None
    assert "something went wrong" in result.error


def test_b30_the_stderr_tail_is_redacted(tmp_path):
    """B30: a vendor key in stderr is replaced by [REDACTED] before it is stored."""
    stderr = f"fatal: request rejected for {SK_ANT_SECRET} at the edge\n"
    spawn = SpawnRecorder(returncode=1, stdout="", stderr=stderr)
    runner = ClaudeCliRunner(spawn=spawn)

    result = runner.run(minimal_request(tmp_path))

    assert result.ok is False
    assert result.error is not None
    assert SK_ANT_SECRET not in result.error
    assert "[REDACTED]" in result.error
    assert "fatal: request rejected for" in result.error


def test_b30_the_stderr_tail_is_bounded(tmp_path):
    """B30: only the tail is kept, so a runaway stderr cannot fill the store."""
    stderr = "X" * 5000 + "TAIL_MARKER"
    spawn = SpawnRecorder(returncode=1, stdout="", stderr=stderr)
    runner = ClaudeCliRunner(spawn=spawn)

    result = runner.run(minimal_request(tmp_path))

    assert result.error is not None
    assert "TAIL_MARKER" in result.error
    assert result.error.count("X") <= 2000


@pytest.mark.parametrize(
    "stdout",
    ["not json at all", "", "{oops", "<html>gateway timeout</html>", "null\n{"],
)
def test_b30_unparseable_stdout_is_not_ok(tmp_path, stdout):
    """B30: a zero exit with garbage on stdout is still a failed run."""
    spawn = SpawnRecorder(returncode=0, stdout=stdout, stderr="stderr said nothing useful")
    runner = ClaudeCliRunner(spawn=spawn)

    result = runner.run(minimal_request(tmp_path))

    assert result.ok is False
    assert result.error is not None


def test_b30_is_error_true_in_a_parsed_result_is_not_ok(tmp_path):
    """B30: the model reporting its own failure is a failed run (RUN-DECISIONS)."""
    spawn = SpawnRecorder(
        returncode=0, stdout=json.dumps({"result": "I could not do it", "is_error": True})
    )
    runner = ClaudeCliRunner(spawn=spawn)

    result = runner.run(minimal_request(tmp_path))

    assert result.ok is False


def test_b30_a_non_zero_exit_with_parseable_json_is_still_not_ok(tmp_path):
    """B30: a well-formed body does not rescue a non-zero exit."""
    spawn = SpawnRecorder(
        returncode=1, stdout=json.dumps({"result": "partial", "num_turns": 3}), stderr="boom"
    )
    runner = ClaudeCliRunner(spawn=spawn)

    result = runner.run(minimal_request(tmp_path))

    assert result.ok is False
    assert result.exit_code == 1


# --------------------------------------------------------------------------
# Delivery 2 — B119 (handoff §6.3) and RUN-DECISIONS-D2 §1, §12: --max-budget-usd, rate-limit
# classification, RunResult.reset_at, runner.base.is_rate_limited, FakeRunner replay.
# Appended by the D2 spec-tester (T3); additions only (D2-R12.3).
# --------------------------------------------------------------------------

RESET_AT = "2026-09-02T18:00:00Z"
USAGE_LIMIT_STDERR = "You've hit your usage limit. Resets at 2026-09-02T18:00:00Z\n"
D2_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "runner"


def budgeted_request(cwd: Path, budget: float | None) -> RunRequest:
    return dataclasses.replace(minimal_request(cwd), max_budget_usd=budget)


def run_result(**overrides) -> RunResult:
    """A RunResult built field by field, so a missing `reset_at` field fails loudly."""
    fields = {
        "ok": True,
        "text": "done",
        "turns": 1,
        "cost_usd": 0.01,
        "allowance_pct": None,
        "duration_ms": 10,
        "session_id": "s",
        "exit_code": 0,
        "transcript": (),
        "error": None,
        "reset_at": None,
    }
    fields.update(overrides)
    return RunResult(**fields)


def rate_limited_payload(**overrides) -> dict:
    """RUN-DECISIONS-D2 §12: a FakeRunner fixture carrying "rate_limited": true."""
    payload = {
        "ok": False,
        "text": "",
        "turns": 0,
        "cost_usd": 0.0,
        "allowance_pct": None,
        "duration_ms": 12,
        "session_id": None,
        "exit_code": 1,
        "transcript": [],
        "error": USAGE_LIMIT_STDERR.strip(),
        "rate_limited": True,
        "reset_at": RESET_AT,
    }
    payload.update(overrides)
    return payload


def fake_runner_with(tmp_path: Path, stage: str, payload: dict) -> FakeRunner:
    directory = tmp_path / "d2_fixtures"
    directory.mkdir(exist_ok=True)
    (directory / f"{stage}.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8", newline="\n"
    )
    return FakeRunner(directory)


def implement_request(cwd: Path) -> RunRequest:
    return dataclasses.replace(minimal_request(cwd), stage="implement")


# --------------------------------------------------------------------------
# B119 - --max-budget-usd sits right after --max-turns, and only when requested
# --------------------------------------------------------------------------


def test_b119_max_budget_usd_follows_max_turns_in_argv(tmp_path):
    """B119 / RUN-DECISIONS-D2 §12: `--max-budget-usd 3.0` immediately after `--max-turns 30`."""
    spawn = SpawnRecorder(stdout=json_stdout(result="ok"))
    runner = ClaudeCliRunner(spawn=spawn)

    runner.run(budgeted_request(tmp_path, 3.0))

    index = spawn.argv.index("--max-turns")
    assert spawn.argv[index : index + 4] == ["--max-turns", "30", "--max-budget-usd", "3.0"]
    assert spawn.argv.count("--max-budget-usd") == 1


def test_b119_max_budget_usd_is_rendered_as_a_decimal_string(tmp_path):
    """B119: the amount reaches claude as text, e.g. 2.5 → "2.5"."""
    spawn = SpawnRecorder(stdout=json_stdout(result="ok"))
    runner = ClaudeCliRunner(spawn=spawn)

    runner.run(budgeted_request(tmp_path, 2.5))

    index = spawn.argv.index("--max-budget-usd")
    assert spawn.argv[index + 1] == "2.5"
    assert all(isinstance(entry, str) for entry in spawn.argv)


def test_b119_the_rest_of_argv_is_unchanged_by_the_budget_flag(tmp_path):
    """B119 / B25: the flag is inserted, not substituted — everything else stays in order."""
    spawn = SpawnRecorder(stdout=json_stdout(result="ok"))
    runner = ClaudeCliRunner(spawn=spawn)

    runner.run(budgeted_request(tmp_path, 3.0))

    assert spawn.argv == [
        "claude",
        "--print",
        "--output-format",
        "json",
        "--max-turns",
        "30",
        "--max-budget-usd",
        "3.0",
        "--permission-mode",
        "acceptEdits",
        "--allowed-tools",
        "Read,Glob,Grep",
        "--",
        "do the thing",
    ]


def test_b119_max_budget_usd_flag_is_omitted_when_none(tmp_path):
    """B119 / RUN-DECISIONS-D2 §12: max_budget_usd=None → no flag, no "None" in argv."""
    spawn = SpawnRecorder(stdout=json_stdout(result="ok"))
    runner = ClaudeCliRunner(spawn=spawn)

    runner.run(budgeted_request(tmp_path, None))

    assert "--max-budget-usd" not in spawn.argv
    assert "None" not in spawn.argv


def test_b119_a_request_that_never_set_the_budget_omits_the_flag(tmp_path):
    """B119: the D1 request shape (no budget) still produces the D1 argv."""
    spawn = SpawnRecorder(stdout=json_stdout(result="ok"))
    runner = ClaudeCliRunner(spawn=spawn)

    runner.run(minimal_request(tmp_path))

    assert "--max-budget-usd" not in spawn.argv


def test_b119_run_request_gains_max_budget_usd_as_its_last_field_defaulting_to_none(tmp_path):
    """RUN-DECISIONS-D2 §12: RunRequest.max_budget_usd is the last field, default None."""
    last = dataclasses.fields(RunRequest)[-1]

    assert last.name == "max_budget_usd"
    assert last.default is None
    assert minimal_request(tmp_path).max_budget_usd is None


def test_b119_run_result_gains_reset_at_as_its_last_field_defaulting_to_none(tmp_path):
    """RUN-DECISIONS-D2 §12: RunResult.reset_at is the last field, default None."""
    last = dataclasses.fields(RunResult)[-1]

    assert last.name == "reset_at"
    assert last.default is None


# --------------------------------------------------------------------------
# B119 - a usage-limit exit is classified, the reset extracted, nothing raised
# --------------------------------------------------------------------------


def test_b119_a_successful_run_has_no_reset_at_and_is_not_rate_limited(tmp_path):
    """B119: the happy path carries reset_at=None and is_rate_limited() is False."""
    from harness.runner.base import is_rate_limited

    spawn = SpawnRecorder(stdout=json_stdout(result="ok", num_turns=1))
    runner = ClaudeCliRunner(spawn=spawn)

    result = runner.run(minimal_request(tmp_path))

    assert result.ok is True
    assert result.reset_at is None
    assert is_rate_limited(result) is False


def test_b119_usage_limit_on_stderr_is_classified_and_the_reset_extracted(tmp_path):
    """B119 (handoff §6.3): non-zero exit + the CLI's usage-limit signature → ok=False,
    reset_at = the ISO timestamp, is_rate_limited() True — and no exception."""
    from harness.runner.base import is_rate_limited

    spawn = SpawnRecorder(returncode=1, stdout="", stderr=USAGE_LIMIT_STDERR)
    runner = ClaudeCliRunner(spawn=spawn)

    result = runner.run(minimal_request(tmp_path))

    assert result.ok is False
    assert result.exit_code == 1
    assert result.reset_at == RESET_AT
    assert result.error is not None
    assert is_rate_limited(result) is True


def test_b119_usage_limit_on_stdout_is_also_classified(tmp_path):
    """B119 / RUN-DECISIONS-D2 §12: the signature is matched on stderr or stdout."""
    from harness.runner.base import is_rate_limited

    spawn = SpawnRecorder(returncode=1, stdout=USAGE_LIMIT_STDERR, stderr="")
    runner = ClaudeCliRunner(spawn=spawn)

    result = runner.run(minimal_request(tmp_path))

    assert result.ok is False
    assert result.reset_at == RESET_AT
    assert is_rate_limited(result) is True


@pytest.mark.parametrize(
    "stderr",
    [
        "Rate limit exceeded\n",
        "RATE LIMIT hit for this account\n",
        "Too many requests\n",
        "limit reached for the current window\n",
        "You've hit your usage limit.\n",
    ],
)
def test_b119_a_limit_phrase_without_a_timestamp_is_rate_limited_with_no_reset(tmp_path, stderr):
    """B119 / RUN-DECISIONS-D2 §12: the pattern is case-insensitive; without a reset time the
    result is still rate-limited, reset_at None."""
    from harness.runner.base import is_rate_limited

    spawn = SpawnRecorder(returncode=1, stdout="", stderr=stderr)
    runner = ClaudeCliRunner(spawn=spawn)

    result = runner.run(minimal_request(tmp_path))

    assert result.ok is False
    assert result.reset_at is None
    assert is_rate_limited(result) is True


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("Usage limit reached. Resets in 30 minutes.\n", "+PT30M"),
        ("Usage limit reached. Resets in 2 hours.\n", "+PT2H"),
    ],
)
def test_b119_a_relative_reset_becomes_an_iso_duration(tmp_path, phrase, expected):
    """B119 / RUN-DECISIONS-D2 §12: `resets in N (minutes|hours)` → "+PT30M"-style duration;
    the runner has no clock, so the stage resolves it."""
    from harness.runner.base import is_rate_limited

    spawn = SpawnRecorder(returncode=1, stdout="", stderr=phrase)
    runner = ClaudeCliRunner(spawn=spawn)

    result = runner.run(minimal_request(tmp_path))

    assert result.ok is False
    assert result.reset_at == expected
    assert is_rate_limited(result) is True


def test_b119_a_plain_failure_is_not_rate_limited(tmp_path):
    """B119 / B30: an ordinary non-zero exit keeps reset_at None and is_rate_limited() False."""
    from harness.runner.base import is_rate_limited

    spawn = SpawnRecorder(returncode=2, stdout="", stderr="claude: something went wrong\n")
    runner = ClaudeCliRunner(spawn=spawn)

    result = runner.run(minimal_request(tmp_path))

    assert result.ok is False
    assert result.reset_at is None
    assert is_rate_limited(result) is False


def test_b119_a_zero_exit_whose_text_mentions_limits_is_not_rate_limited(tmp_path):
    """B119: classification is for non-zero exits only; a successful reply about limits is a
    successful reply."""
    from harness.runner.base import is_rate_limited

    spawn = SpawnRecorder(
        returncode=0,
        stdout=json.dumps({"result": "Note: resets at 2026-09-02T18:00:00Z is a usage limit."}),
    )
    runner = ClaudeCliRunner(spawn=spawn)

    result = runner.run(minimal_request(tmp_path))

    assert result.ok is True
    assert result.reset_at is None
    assert is_rate_limited(result) is False


def test_b119_the_error_tail_is_still_redacted_when_rate_limited(tmp_path):
    """B119 / B30: classification does not bypass redaction of the stderr tail."""
    stderr = f"token {SK_ANT_SECRET} rejected: You've hit your usage limit. Resets at {RESET_AT}\n"
    spawn = SpawnRecorder(returncode=1, stdout="", stderr=stderr)
    runner = ClaudeCliRunner(spawn=spawn)

    result = runner.run(minimal_request(tmp_path))

    assert result.reset_at == RESET_AT
    assert result.error is not None
    assert SK_ANT_SECRET not in result.error
    assert "[REDACTED]" in result.error


def test_b119_rate_limited_error_carries_reset_at():
    """RUN-DECISIONS-D2 §1: RateLimited(msg, reset_at=None) is a HarnessError with reset_at."""
    from harness.errors import HarnessError, RateLimited

    assert issubclass(RateLimited, HarnessError)
    assert RateLimited("limited").reset_at is None
    assert RateLimited("limited", reset_at=RESET_AT).reset_at == RESET_AT


def test_b119_is_rate_limited_reads_reset_at_or_the_error_text():
    """RUN-DECISIONS-D2 §12: is_rate_limited(result) is pure — True when reset_at is set or the
    error text carries the signature; False for a plain failure or a success."""
    from harness.runner.base import is_rate_limited

    assert is_rate_limited(run_result(ok=False, exit_code=1, error="boom", reset_at=RESET_AT))
    assert is_rate_limited(run_result(ok=False, exit_code=1, error="Too many requests"))
    assert is_rate_limited(run_result(ok=False, exit_code=1, error="usage limit reached"))
    assert not is_rate_limited(run_result(ok=False, exit_code=1, error="segfault"))
    assert not is_rate_limited(run_result(ok=False, exit_code=1, error=None))
    assert not is_rate_limited(run_result(ok=True))


# --------------------------------------------------------------------------
# B119 / RUN-DECISIONS-D2 §12 - the fake runner replays a rate-limit outcome
# --------------------------------------------------------------------------


def test_b119_fake_runner_replays_a_rate_limited_fixture(tmp_path):
    """RUN-DECISIONS-D2 §12: a fixture with "rate_limited": true → ok=False, reset_at copied."""
    from harness.runner.base import is_rate_limited

    runner = fake_runner_with(tmp_path, "implement", rate_limited_payload())

    result = runner.run(implement_request(tmp_path))

    assert result.ok is False
    assert result.reset_at == RESET_AT
    assert is_rate_limited(result) is True


def test_b119_fake_runner_rate_limited_fixture_without_a_reset_has_reset_at_none(tmp_path):
    """RUN-DECISIONS-D2 §12: the flag alone marks the outcome; reset_at is copied as given."""
    from harness.runner.base import is_rate_limited

    runner = fake_runner_with(tmp_path, "implement", rate_limited_payload(reset_at=None))

    result = runner.run(implement_request(tmp_path))

    assert result.ok is False
    assert result.reset_at is None
    assert is_rate_limited(result) is True


def test_b119_fake_runner_without_the_flag_is_not_rate_limited(tmp_path):
    """RUN-DECISIONS-D2 §12: an ordinary fixture has reset_at None and is not rate-limited."""
    from harness.runner.base import is_rate_limited

    payload = rate_limited_payload(ok=True, exit_code=0, error=None, text="implemented")
    payload.pop("rate_limited")
    payload.pop("reset_at")
    runner = fake_runner_with(tmp_path, "implement", payload)

    result = runner.run(implement_request(tmp_path))

    assert result.ok is True
    assert result.reset_at is None
    assert is_rate_limited(result) is False


def test_b119_the_shipped_rate_limited_fixture_replays_as_rate_limited(tmp_path):
    """Handoff §3 / RUN-DECISIONS-D2 §12: tests/fixtures/runner/rate_limited.json ships with
    "rate_limited": true and replays as a rate-limited implement outcome."""
    from harness.runner.base import is_rate_limited

    shipped = D2_FIXTURES_DIR / "rate_limited.json"
    assert shipped.is_file(), "tests/fixtures/runner/rate_limited.json must ship (handoff §3)"
    payload = json.loads(shipped.read_text(encoding="utf-8"))
    assert payload.get("rate_limited") is True
    assert payload.get("ok") is False

    runner = fake_runner_with(tmp_path, "implement", payload)
    result = runner.run(implement_request(tmp_path))

    assert result.ok is False
    assert is_rate_limited(result) is True


def test_b119_the_shipped_revise_fixture_replays_as_a_successful_revise(tmp_path):
    """Handoff §3 / RUN-DECISIONS-D2 §12: tests/fixtures/runner/revise.json ships and is a
    successful RunResult for the revise stage."""
    shipped = D2_FIXTURES_DIR / "revise.json"
    assert shipped.is_file(), "tests/fixtures/runner/revise.json must ship (handoff §3)"
    payload = json.loads(shipped.read_text(encoding="utf-8"))
    assert payload.get("ok") is True

    runner = FakeRunner(D2_FIXTURES_DIR)
    result = runner.run(dataclasses.replace(minimal_request(tmp_path), stage="revise"))

    assert result.ok is True
    assert result.reset_at is None
    assert result.text.strip() != ""


def test_d19_an_is_error_result_with_no_stderr_surfaces_the_cli_subtype(tmp_path):
    """D19: `--max-budget-usd` binding reports `subtype: error_max_budget_usd` with empty stderr;
    the RunResult must carry that name so a diagnose cycle can tell a budget stop from a crash."""
    import json
    import subprocess

    from harness.runner.base import RunRequest
    from harness.runner.cli import ClaudeCliRunner

    payload = {"type": "result", "subtype": "error_max_budget_usd", "is_error": True,
               "result": "", "num_turns": 1, "total_cost_usd": 0.153, "duration_ms": 51940}

    def spawn(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, json.dumps(payload), "")

    runner = ClaudeCliRunner(spawn=spawn)
    result = runner.run(RunRequest(stage="implement", prompt="x", system_prompt=None,
                                   allowed_tools=("Read",), disallowed_tools=(), max_turns=5,
                                   cwd=tmp_path, timeout_s=60, max_budget_usd=0.001))
    assert result.ok is False
    assert result.error == "error_max_budget_usd"
    assert result.cost_usd == 0.153
