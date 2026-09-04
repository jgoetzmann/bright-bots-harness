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
from harness.runner import cli as cli_mod
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


def test_b216_a_flag_shaped_prompt_reaches_claude_on_stdin(tmp_path):
    """B216/D35: a prompt that looks like a flag is stdin, so it can never be parsed as one."""
    spawn = SpawnRecorder(stdout=json_stdout(result="ok"))
    runner = ClaudeCliRunner(spawn=spawn)
    request = dataclasses.replace(minimal_request(tmp_path), prompt="--not-a-flag please")

    runner.run(request)

    assert "--not-a-flag please" not in spawn.argv
    assert "--" not in spawn.argv
    assert spawn.kwargs["input"] == "--not-a-flag please"


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
    """RUN-DECISIONS-D2 §12: RunRequest.max_budget_usd defaults to None and is the last field
    Delivery 2 defines. B218 appends ``deny_read`` after it, exactly as Delivery 3 appended
    ``usage`` after ``RunResult.reset_at``; what this pins is its position among the D2 fields
    and its default."""
    names = [f.name for f in dataclasses.fields(RunRequest)]
    budget = dataclasses.fields(RunRequest)[names.index("max_budget_usd")]

    assert names[-4:] == ["max_budget_usd", "deny_read", "model", "effort"]
    assert budget.default is None
    assert minimal_request(tmp_path).max_budget_usd is None


def test_b119_run_result_gains_reset_at_as_its_last_field_defaulting_to_none(tmp_path):
    """RUN-DECISIONS-D2 §12: RunResult.reset_at defaults to None and is the last field the
    Delivery 2 runner defines. Delivery 3 appends ``usage`` after it (RUN-DECISIONS-D3
    "Runner"), so reset_at is now second from last; its position relative to the D2 fields
    and its default are what this pins."""
    names = [f.name for f in dataclasses.fields(RunResult)]
    reset_at = next(f for f in dataclasses.fields(RunResult) if f.name == "reset_at")

    assert names[-2:] == ["reset_at", "usage"]
    assert reset_at.default is None


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


# --------------------------------------------------------------------------
# Delivery 3 — RUN-DECISIONS-D3 "Runner" (B200-B203). Appended by the D3 spec-tester (T1);
# additions only. The usage signal is the one rate_limit_event that
# `claude -p --output-format stream-json --verbose` emits per call; nothing here starts a
# process — every runner is built with the injectable spawn kwarg.
# --------------------------------------------------------------------------

# RUN-DECISIONS-D3 "Config" — the five new keys with their .env.example values (inline).
D3_USAGE_ENV: dict[str, str] = {
    "WEEKLY_USAGE_STOP_PCT": "90",
    "SESSION_USAGE_STOP_PCT": "70",
    "OVERRUN_PCT": "10",
    "RUN_WINDOW_START": "mon 08:00",
    "RUN_WINDOW_END": "tue 20:00",
}

# The verified 2026-09-03 event, verbatim from RUN-DECISIONS-D3 "Why".
FIVE_HOUR_EPOCH = 1788519600  # 2026-09-04T11:00:00Z
SEVEN_DAY_EPOCH = 1788897600  # 2026-09-08T20:00:00Z (Tuesday 20:00 UTC)
FIVE_HOUR_ISO = "2026-09-04T11:00:00Z"
SEVEN_DAY_ISO = "2026-09-08T20:00:00Z"


def rate_limit_event(*, five_hour: float = 0.07, seven_day: float = 0.49,
                     status: str = "allowed", five_hour_resets: int = FIVE_HOUR_EPOCH,
                     seven_day_resets: int = SEVEN_DAY_EPOCH) -> dict:
    """One rate_limit_event line exactly as RUN-DECISIONS-D3 "Why" records it."""
    return {
        "type": "rate_limit_event",
        "rate_limit_info": {
            "status": status,
            "resetsAt": five_hour_resets,
            "rateLimitType": "five_hour",
            "overageStatus": "rejected",
            "isUsingOverage": False,
            "unifiedWindows": {
                "five_hour": {"utilization": five_hour, "resetsAt": five_hour_resets},
                "seven_day": {"utilization": seven_day, "resetsAt": seven_day_resets},
            },
        },
    }


def stream_result(**overrides) -> dict:
    """The stream-json result line: the same fields as today's --output-format json."""
    payload = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "the patch is ready",
        "num_turns": 7,
        "total_cost_usd": 0.4275,
        "duration_ms": 18234,
        "session_id": "01JABCDEF",
    }
    payload.update(overrides)
    return payload


def stream_stdout(*objects: dict) -> str:
    """JSON lines, one object per line, with the trailing newline a real CLI emits."""
    return "".join(json.dumps(obj) + "\n" for obj in objects)


def usage_dict(**overrides) -> dict:
    """The RunResult.usage shape of RUN-DECISIONS-D3 "Runner"."""
    payload = {
        "five_hour": {"utilization": 0.07, "resets_at": FIVE_HOUR_ISO},
        "seven_day": {"utilization": 0.49, "resets_at": SEVEN_DAY_ISO},
        "status": "allowed",
        "observed_at": "2026-09-03T12:00:00Z",
    }
    payload.update(overrides)
    return payload


def usage_fixture_payload(**overrides) -> dict:
    """A FakeRunner fixture body whose top-level keys are the RunResult field names."""
    payload = {
        "ok": True,
        "text": "implemented",
        "turns": 3,
        "cost_usd": 0.5,
        "allowance_pct": None,
        "duration_ms": 1200,
        "session_id": "fixture-session",
        "exit_code": 0,
        "transcript": [],
        "error": None,
        "reset_at": None,
        "usage": usage_dict(),
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def d3_config(tmp_path, write_env):
    """A Config from a complete .env — every D1 and D2 key plus the five D3 keys."""
    from harness.config import load_config

    path = write_env(tmp_path / "d3" / ".env", **D3_USAGE_ENV)
    return load_config(env_path=path, environ={})


# --------------------------------------------------------------------------
# B200 - capture_usage swaps the output format in place and moves nothing else
# --------------------------------------------------------------------------


def test_b200_capture_usage_replaces_output_format_json_in_the_same_position(tmp_path):
    """B200: with capture_usage=True the tokens after --print are
    `--output-format stream-json --verbose`; the bare `json` token is gone and every other
    flag keeps its B25 position."""
    spawn = SpawnRecorder(stdout=stream_stdout(rate_limit_event(), stream_result()))
    runner = ClaudeCliRunner(spawn=spawn, capture_usage=True)

    runner.run(minimal_request(tmp_path))

    assert spawn.argv[1:5] == ["--print", "--output-format", "stream-json", "--verbose"]
    assert spawn.argv == [
        "claude",
        "--print",
        "--output-format",
        "stream-json",
        "--verbose",
        "--max-turns",
        "30",
        "--permission-mode",
        "acceptEdits",
        "--allowed-tools",
        "Read,Glob,Grep",
    ]
    assert "json" not in spawn.argv
    assert spawn.argv.count("--output-format") == 1
    assert spawn.argv.count("--verbose") == 1


def test_b200_the_default_runner_argv_is_the_unchanged_b25_argv(tmp_path):
    """B200: capture_usage defaults to False, and then argv is exactly as B25 froze it —
    `--output-format json`, no `--verbose`, nothing else touched."""
    spawn = SpawnRecorder(stdout=json_stdout(result="ok"))
    runner = ClaudeCliRunner(spawn=spawn)

    runner.run(minimal_request(tmp_path))

    assert runner.capture_usage is False
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
    ]
    assert "--verbose" not in spawn.argv
    assert "stream-json" not in spawn.argv


def test_b200_capture_usage_false_explicitly_is_also_the_b25_argv(tmp_path):
    """B200: False → argv exactly as B25 today; the kwarg is opt-in, not a rename."""
    spawn = SpawnRecorder(stdout=json_stdout(result="ok"))
    runner = ClaudeCliRunner(spawn=spawn, capture_usage=False)

    runner.run(minimal_request(tmp_path))

    assert runner.capture_usage is False
    assert spawn.argv[1:5] == ["--print", "--output-format", "json", "--max-turns"]
    assert "--verbose" not in spawn.argv


def test_b200_capture_usage_leaves_every_optional_flag_in_its_b25_order(tmp_path):
    """B200/B25/B119: the swap is positional — the budget, tool, system-prompt and add-dir
    flags keep their order, and the prompt is still last after the terminator."""
    extra_a = tmp_path / "shared"
    extra_b = tmp_path / "docs"
    spawn = SpawnRecorder(stdout=stream_stdout(stream_result()))
    runner = ClaudeCliRunner(spawn=spawn, capture_usage=True)
    request = dataclasses.replace(
        maximal_request(tmp_path, extra_a, extra_b), max_budget_usd=3.0
    )

    runner.run(request)

    assert spawn.argv == [
        "claude",
        "--print",
        "--output-format",
        "stream-json",
        "--verbose",
        "--max-turns",
        "80",
        "--max-budget-usd",
        "3.0",
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
    ]


def test_b200_capture_usage_never_adds_a_skip_permissions_flag(tmp_path):
    """B200/B27 (I-3): the new flags do not open a path past the permission ceiling."""
    spawn = SpawnRecorder(stdout=stream_stdout(rate_limit_event(), stream_result()))
    runner = ClaudeCliRunner(spawn=spawn, capture_usage=True)

    runner.run(minimal_request(tmp_path))

    assert not any("dangerously" in entry for entry in spawn.argv)
    assert not any("skip-permissions" in entry for entry in spawn.argv)
    index = spawn.argv.index("--permission-mode")
    assert spawn.argv[index + 1] == "acceptEdits"


# --------------------------------------------------------------------------
# B201 - parse_stream is a pure helper: (result, last rate_limit_event)
# --------------------------------------------------------------------------


def test_b201_parse_stream_returns_the_result_and_the_usage(tmp_path):
    """B201: parse_stream(stdout) -> (result, usage); the result is the result object and the
    usage is the rate_limit_event's unified windows with epoch resets rendered as ISO-Z."""
    from harness.runner.cli import parse_stream

    stdout = stream_stdout(
        {"type": "system", "subtype": "init", "session_id": "01JABCDEF"},
        rate_limit_event(),
        {"type": "assistant", "message": {"content": "working"}},
        stream_result(),
    )

    result, usage = parse_stream(stdout)

    assert result is not None
    assert result["result"] == "the patch is ready"
    assert result["num_turns"] == 7
    assert result["total_cost_usd"] == pytest.approx(0.4275)
    assert usage is not None
    assert usage["five_hour"]["utilization"] == pytest.approx(0.07)
    assert usage["five_hour"]["resets_at"] == FIVE_HOUR_ISO
    assert usage["seven_day"]["utilization"] == pytest.approx(0.49)
    assert usage["seven_day"]["resets_at"] == SEVEN_DAY_ISO
    assert usage["status"] == "allowed"


def test_b201_parse_stream_leaves_observed_at_to_the_stage(tmp_path):
    """RUN-DECISIONS-D3 "Runner": observed_at is filled by the stage from the clock, not by the
    runner — the parsed usage carries no clock reading of its own."""
    from harness.runner.cli import parse_stream

    _, usage = parse_stream(stream_stdout(rate_limit_event(), stream_result()))

    assert usage is not None
    assert usage.get("observed_at") in (None, "")


def test_b201_parse_stream_keeps_the_last_rate_limit_event(tmp_path):
    """B201: multiple rate_limit_events — the last one wins."""
    from harness.runner.cli import parse_stream

    stdout = stream_stdout(
        rate_limit_event(five_hour=0.01, seven_day=0.10),
        rate_limit_event(five_hour=0.04, seven_day=0.30),
        rate_limit_event(five_hour=0.07, seven_day=0.49, status="allowed_warning"),
        stream_result(),
    )

    _, usage = parse_stream(stdout)

    assert usage is not None
    assert usage["five_hour"]["utilization"] == pytest.approx(0.07)
    assert usage["seven_day"]["utilization"] == pytest.approx(0.49)
    assert usage["status"] == "allowed_warning"


def test_b201_parse_stream_keeps_the_last_result_line(tmp_path):
    """B201: the LAST line whose type == "result" is the result object."""
    from harness.runner.cli import parse_stream

    stdout = stream_stdout(
        stream_result(result="first pass", num_turns=1),
        rate_limit_event(),
        stream_result(result="second pass", num_turns=9),
    )

    result, usage = parse_stream(stdout)

    assert result is not None
    assert result["result"] == "second pass"
    assert result["num_turns"] == 9
    assert usage is not None


def test_b201_parse_stream_without_a_result_line_returns_none_and_the_usage(tmp_path):
    """B201: no result line -> (None, usage) — the usage still survives so the governor can act
    on the signal even when the call itself produced nothing parseable."""
    from harness.runner.cli import parse_stream

    stdout = stream_stdout(
        {"type": "system", "subtype": "init"},
        rate_limit_event(five_hour=0.66, seven_day=0.91),
    )

    result, usage = parse_stream(stdout)

    assert result is None
    assert usage is not None
    assert usage["seven_day"]["utilization"] == pytest.approx(0.91)


def test_b201_parse_stream_without_a_rate_limit_event_returns_none_usage(tmp_path):
    """B201/B114: no decision may depend on the signal being present — a stream with no
    rate_limit_event parses to (result, None), never to a guessed utilization."""
    from harness.runner.cli import parse_stream

    result, usage = parse_stream(stream_stdout(stream_result()))

    assert result is not None
    assert result["result"] == "the patch is ready"
    assert usage is None


def test_b201_parse_stream_of_empty_output_is_two_nones(tmp_path):
    """B201: empty stdout carries neither a result nor a usage."""
    from harness.runner.cli import parse_stream

    assert parse_stream("") == (None, None)
    assert parse_stream("\n\n") == (None, None)


def test_b201_parse_stream_is_pure_and_repeatable(tmp_path):
    """B201: a pure helper in harness/runner/cli.py. It returns this exact pair; a second call
    on the same text returns the same pair; the string it was handed is unchanged; and a caller
    mutating the returned result does not change what the next call returns.

    The value is pinned first and repeatability is checked against that value, because
    `parse_stream(x) == parse_stream(x)` alone is a tautology — it holds for any deterministic
    function, `lambda s: (None, None)` included."""
    from harness.runner.cli import parse_stream

    stdout = stream_stdout(rate_limit_event(), stream_result())
    handed_in = stdout
    expected = (
        stream_result(),
        {
            "five_hour": {"utilization": 0.07, "resets_at": FIVE_HOUR_ISO},
            "seven_day": {"utilization": 0.49, "resets_at": SEVEN_DAY_ISO},
            "status": "allowed",
        },
    )

    first = parse_stream(stdout)
    second = parse_stream(stdout)

    assert parse_stream.__module__ == "harness.runner.cli", parse_stream.__module__
    assert first == expected, first
    assert second == expected, second
    assert stdout == handed_in, "parse_stream must not touch the text it is handed"

    first[0]["result"] = "mutated by the caller"
    assert parse_stream(stdout) == expected, "a caller's mutation must not reach the next parse"


def test_b201_capture_usage_run_carries_the_usage_onto_the_run_result(tmp_path):
    """B200/B201: with capture_usage=True stdout is JSON lines, the result line populates the
    RunResult exactly as today, and the last rate_limit_event lands on RunResult.usage."""
    spawn = SpawnRecorder(
        stdout=stream_stdout(
            rate_limit_event(five_hour=0.02, seven_day=0.20),
            rate_limit_event(five_hour=0.07, seven_day=0.49),
            stream_result(),
        )
    )
    runner = ClaudeCliRunner(spawn=spawn, capture_usage=True)

    result = runner.run(minimal_request(tmp_path))

    assert result.ok is True
    assert result.text == "the patch is ready"
    assert result.turns == 7
    assert result.cost_usd == pytest.approx(0.4275)
    assert result.session_id == "01JABCDEF"
    assert result.usage is not None
    assert result.usage["five_hour"]["utilization"] == pytest.approx(0.07)
    assert result.usage["seven_day"]["utilization"] == pytest.approx(0.49)
    assert result.usage["seven_day"]["resets_at"] == SEVEN_DAY_ISO


def test_b201_a_stream_without_a_result_line_is_unparseable(tmp_path):
    """B201/B30: no result line → treat as unparseable — ok is False and an error is set."""
    spawn = SpawnRecorder(
        returncode=0, stdout=stream_stdout(rate_limit_event()), stderr="nothing on stdout"
    )
    runner = ClaudeCliRunner(spawn=spawn, capture_usage=True)

    result = runner.run(minimal_request(tmp_path))

    assert result.ok is False
    assert result.error is not None


def test_b201_a_run_without_the_signal_has_usage_none(tmp_path):
    """B201/B114: no rate_limit_event in the stream → RunResult.usage is None, and the run is
    still a success."""
    spawn = SpawnRecorder(stdout=stream_stdout(stream_result()))
    runner = ClaudeCliRunner(spawn=spawn, capture_usage=True)

    result = runner.run(minimal_request(tmp_path))

    assert result.ok is True
    assert result.usage is None


def test_b201_the_default_runner_reports_no_usage(tmp_path):
    """B200/B201: without capture_usage the CLI answers plain JSON and usage stays None."""
    spawn = SpawnRecorder(stdout=json_stdout(result="ok", num_turns=1))
    runner = ClaudeCliRunner(spawn=spawn)

    result = runner.run(minimal_request(tmp_path))

    assert result.ok is True
    assert result.usage is None


def test_b201_run_result_gains_usage_as_its_last_field_defaulting_to_none(tmp_path):
    """RUN-DECISIONS-D3: RunResult.usage is the LAST field, after reset_at, default None."""
    names = [f.name for f in dataclasses.fields(RunResult)]

    assert names[-2:] == ["reset_at", "usage"]
    assert dataclasses.fields(RunResult)[-1].default is None
    assert run_result().usage is None


def test_b201_is_rate_limited_is_unchanged_by_a_usage_carrying_result(tmp_path):
    """RUN-DECISIONS-D3 "Runner": is_rate_limited unchanged — a successful call that happens to
    report 99 % weekly utilization is not a rate-limited call."""
    from harness.runner.base import is_rate_limited

    spawn = SpawnRecorder(
        stdout=stream_stdout(rate_limit_event(five_hour=0.95, seven_day=0.99), stream_result())
    )
    runner = ClaudeCliRunner(spawn=spawn, capture_usage=True)

    result = runner.run(minimal_request(tmp_path))

    assert result.ok is True
    assert result.reset_at is None
    assert is_rate_limited(result) is False


# --------------------------------------------------------------------------
# B202 - get_runner builds the CLI runner with capture_usage on
# --------------------------------------------------------------------------


def test_b202_get_runner_builds_the_cli_runner_with_capture_usage_true(d3_config):
    """B202: get_runner(config) builds ClaudeCliRunner(capture_usage=True) for backend "cli",
    so every real call carries the usage signal home."""
    config = dataclasses.replace(d3_config, backend="cli")

    runner = get_runner(config)

    assert isinstance(runner, ClaudeCliRunner)
    assert runner.name == "cli"
    assert runner.capture_usage is True


def test_b202_get_runner_still_builds_the_fake_runner_for_backend_fake(d3_config):
    """B202/B24: the fake backend is unchanged — no CLI runner, no capture flag."""
    config = dataclasses.replace(d3_config, backend="fake")

    runner = get_runner(config)

    assert isinstance(runner, FakeRunner)
    assert runner.name == "fake"


def test_b202_a_directly_constructed_cli_runner_still_defaults_to_off(tmp_path):
    """B202/B200: only get_runner opts in; the constructor default stays False so D1 callers
    keep the D1 argv."""
    assert ClaudeCliRunner().capture_usage is False
    assert ClaudeCliRunner(claude_bin="claude-2.1.251").capture_usage is False


def test_b202_the_runner_from_get_runner_emits_the_stream_argv(tmp_path, d3_config):
    """B202/B200: the runner get_runner hands back really does ask for stream-json."""
    config = dataclasses.replace(d3_config, backend="cli")
    runner = get_runner(config)
    spawn = SpawnRecorder(stdout=stream_stdout(rate_limit_event(), stream_result()))
    runner.spawn = spawn

    result = runner.run(minimal_request(tmp_path))

    assert spawn.argv[1:5] == ["--print", "--output-format", "stream-json", "--verbose"]
    assert result.usage is not None


# --------------------------------------------------------------------------
# B203 - the fake runner replays a usage fixture
# --------------------------------------------------------------------------


def test_b203_fake_runner_copies_the_usage_key_onto_the_run_result(tmp_path):
    """B203: a FakeRunner fixture key `usage` (the RunResult shape) is copied through."""
    runner = fake_runner_with(tmp_path, "implement", usage_fixture_payload())

    result = runner.run(implement_request(tmp_path))

    assert result.ok is True
    assert result.usage == usage_dict()
    assert result.usage["seven_day"]["utilization"] == pytest.approx(0.49)


def test_b203_the_fixture_usage_may_omit_observed_at(tmp_path):
    """B203: observed_at is optional in the fixture — the stage stamps it from the clock."""
    usage = usage_dict()
    usage.pop("observed_at")
    runner = fake_runner_with(tmp_path, "implement", usage_fixture_payload(usage=usage))

    result = runner.run(implement_request(tmp_path))

    assert result.usage is not None
    assert result.usage.get("observed_at") is None
    assert result.usage["five_hour"]["resets_at"] == FIVE_HOUR_ISO


def test_b203_a_fixture_without_usage_has_usage_none(tmp_path):
    """B203/B114: absent -> None; the fake path never invents a utilization."""
    payload = usage_fixture_payload()
    payload.pop("usage")
    runner = fake_runner_with(tmp_path, "implement", payload)

    result = runner.run(implement_request(tmp_path))

    assert result.ok is True
    assert result.usage is None


def test_b203_a_missing_fixture_has_usage_none(tmp_path):
    """B203/B114: the no-fixture failure result carries no usage either."""
    runner = fake_runner_with(tmp_path, "implement", usage_fixture_payload())

    result = runner.run(dataclasses.replace(minimal_request(tmp_path), stage="decompose"))

    assert result.ok is False
    assert result.usage is None


def test_b203_a_rate_limited_fixture_may_still_carry_usage(tmp_path):
    """B203/B119: the two signals are independent — a rate-limited fixture can also report the
    utilization that caused it."""
    from harness.runner.base import is_rate_limited

    payload = rate_limited_payload()
    payload["usage"] = usage_dict(seven_day={"utilization": 0.99, "resets_at": SEVEN_DAY_ISO})
    runner = fake_runner_with(tmp_path, "implement", payload)

    result = runner.run(implement_request(tmp_path))

    assert result.ok is False
    assert is_rate_limited(result) is True
    assert result.usage is not None
    assert result.usage["seven_day"]["utilization"] == pytest.approx(0.99)


# --------------------------------------------------------------------------
# B216 - the prompt travels on stdin, and argv has a ceiling (D35)
# --------------------------------------------------------------------------


def test_b216_the_prompt_is_passed_on_stdin_and_never_in_argv(tmp_path):
    """B216: `claude --print` reads the prompt from stdin; argv ends at the last flag."""
    spawn = SpawnRecorder(stdout=json_stdout(result="ok"))
    runner = ClaudeCliRunner(spawn=spawn)

    runner.run(minimal_request(tmp_path))

    assert spawn.kwargs["input"] == "do the thing"
    assert "do the thing" not in spawn.argv
    assert spawn.argv[-1] == "Read,Glob,Grep"


def test_b216_a_prompt_far_past_the_platform_ceiling_still_runs(tmp_path):
    """B216: the unbounded argument is the prompt, so size stops being a spawn concern."""
    spawn = SpawnRecorder(stdout=json_stdout(result="ok"))
    runner = ClaudeCliRunner(spawn=spawn)
    huge = "x" * (cli_mod.ARGV_LIMIT_WINDOWS * 4)
    request = dataclasses.replace(minimal_request(tmp_path), prompt=huge)

    result = runner.run(request)

    assert result.ok
    assert spawn.kwargs["input"] == huge


def test_b216_argv_over_the_ceiling_is_refused_before_the_spawn(tmp_path):
    """B216: an oversized system prompt fails legibly and spends nothing."""
    spawn = SpawnRecorder(stdout=json_stdout(result="ok"))
    runner = ClaudeCliRunner(spawn=spawn)
    request = dataclasses.replace(
        minimal_request(tmp_path), system_prompt="s" * (cli_mod.argv_limit() + 1)
    )

    result = runner.run(request)

    assert not result.ok
    assert result.exit_code == cli_mod.EXIT_ARGV_TOO_LONG
    assert "--system-prompt" in (result.error or "")
    assert spawn.calls == [], "the ceiling must be checked before claude is started"


def test_b216_argv_too_long_returns_none_under_the_ceiling():
    """B216: the guard is a ceiling, not a budget; an ordinary argv passes untouched."""
    assert cli_mod.argv_too_long(["claude", "--print", "--max-turns", "30"]) is None


def test_b216_argv_too_long_counts_the_separators():
    """B216: the OS pays for the spaces between arguments, so the check must too."""
    limit = cli_mod.argv_limit()
    exact = ["a" * (limit - 2), "b"]  # len 'a...' + 1 separator + len 'b' == limit
    assert cli_mod.argv_too_long(exact) is None
    assert cli_mod.argv_too_long([*exact, "c"]) is not None


def test_b216_the_windows_ceiling_is_the_cmd_exe_one_not_createprocess():
    """B216: `claude` is an npm .CMD shim on Windows, so cmd.exe's 8191 governs."""
    assert cli_mod.ARGV_LIMIT_WINDOWS == 8191
    assert cli_mod.ARGV_LIMIT_POSIX > cli_mod.ARGV_LIMIT_WINDOWS


# --------------------------------------------------------------------------
# B218 - Read is not confined to cwd, so the sensitive paths are denied (D36)
# --------------------------------------------------------------------------


def test_b218_deny_read_becomes_setting_sources_and_settings(tmp_path):
    """B218: the deny list travels as --settings, with --setting-sources emptied first."""
    spawn = SpawnRecorder(stdout=json_stdout(result="ok"))
    runner = ClaudeCliRunner(spawn=spawn)
    request = dataclasses.replace(
        minimal_request(tmp_path), deny_read=("D:/repo/.env", "D:/repo/state/**")
    )

    runner.run(request)

    argv = spawn.argv
    assert argv[argv.index("--setting-sources") + 1] == ""
    settings = json.loads(argv[argv.index("--settings") + 1])
    assert settings == {
        "permissions": {"deny": ["Read(D:/repo/.env)", "Read(D:/repo/state/**)"]}
    }


def test_b218_no_deny_list_means_no_settings_flags(tmp_path):
    """B218: nothing to deny, nothing added; the B25 argv is untouched."""
    spawn = SpawnRecorder(stdout=json_stdout(result="ok"))
    runner = ClaudeCliRunner(spawn=spawn)

    runner.run(minimal_request(tmp_path))

    assert "--settings" not in spawn.argv
    assert "--setting-sources" not in spawn.argv


def test_b218_deny_rules_carry_no_double_slash_prefix(tmp_path):
    """B218: `Read(//<path>)` is accepted by the CLI and matches nothing -- measured, not
    assumed. The enforced form is the bare absolute path."""
    spawn = SpawnRecorder(stdout=json_stdout(result="ok"))
    runner = ClaudeCliRunner(spawn=spawn)
    request = dataclasses.replace(minimal_request(tmp_path), deny_read=("/home/x/.ssh/**",))

    runner.run(request)

    rules = json.loads(spawn.argv[spawn.argv.index("--settings") + 1])["permissions"]["deny"]
    assert rules == ["Read(/home/x/.ssh/**)"]
    assert not any("//" in rule for rule in rules)


def test_b218_deny_settings_skips_blank_entries():
    """B218: an empty configured path must not become a rule that denies everything."""
    assert cli_mod.deny_settings(["", "   "]) is None
    assert cli_mod.deny_settings([]) is None


# --------------------------------------------------------------------------
# B225 - the model and the reasoning effort are pinned, not left to the CLI
# --------------------------------------------------------------------------


def test_b225_model_and_effort_precede_the_spending_flags(tmp_path):
    """B225: what the session *is* comes before what it may spend, so the pair sits between
    the output format and --max-turns."""
    spawn = SpawnRecorder(stdout=json_stdout(result="ok"))
    runner = ClaudeCliRunner(spawn=spawn)
    request = dataclasses.replace(minimal_request(tmp_path), model="opus", effort="xhigh")

    runner.run(request)

    assert spawn.argv[:9] == [
        "claude",
        "--print",
        "--output-format",
        "json",
        "--model",
        "opus",
        "--effort",
        "xhigh",
        "--max-turns",
    ]


def test_b225_an_unset_model_and_effort_leave_the_argv_untouched(tmp_path):
    """B225: both are omitted when unset, so the Delivery 2 argv shape is what a request
    without them still produces."""
    spawn = SpawnRecorder(stdout=json_stdout(result="ok"))
    runner = ClaudeCliRunner(spawn=spawn)

    runner.run(minimal_request(tmp_path))

    assert "--model" not in spawn.argv
    assert "--effort" not in spawn.argv


def test_b225_each_flag_is_independent(tmp_path):
    """B225: a model with no effort, and an effort with no model, are both legal."""
    for field, flag, value in (("model", "--model", "opus"), ("effort", "--effort", "max")):
        spawn = SpawnRecorder(stdout=json_stdout(result="ok"))
        runner = ClaudeCliRunner(spawn=spawn)
        runner.run(dataclasses.replace(minimal_request(tmp_path), **{field: value}))
        assert spawn.argv[spawn.argv.index(flag) + 1] == value
        other = "--effort" if flag == "--model" else "--model"
        assert other not in spawn.argv


def test_b225_the_effort_levels_are_the_ones_the_cli_accepts():
    """B225: measured against `claude --help`: low, medium, high, xhigh, max."""
    from harness.config import EFFORT_LEVELS

    assert EFFORT_LEVELS == ("low", "medium", "high", "xhigh", "max")
