"""B65-B72: the CLI, exercised through harness.__main__.main(argv).

Every fixture here is inline. Nothing reaches the network, a real `claude`, or a
real `git`: `WHICH` and `RUN` are monkeypatched for `doctor`, and
`CloneManager.acquire` is booby-trapped in the `run` tests so a build that starts
a stage it should not start fails loudly instead of cloning.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import harness.__main__ as cli
import harness.clone as clone_mod
import harness.gh as gh_mod
from harness.errors import CloneError, GitHubError
from harness.store import Store

# --------------------------------------------------------------------------
# Inline fixtures
# --------------------------------------------------------------------------

ENV_BODY = """\
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
FULLSEND_ENABLED=false
HARNESS_GITHUB_TOKEN=
ANTHROPIC_API_KEY=
"""

# Deliberately different bytes from the .env, so an init that overwrites is visible.
CUSTOM_ENV = "# written by tests/test_cli.py, must survive a second init\n" + ENV_BODY
EXAMPLE_ENV = "# .env.example\n" + ENV_BODY


def write_repo(tmp_path: Path, *, with_env: bool = True) -> None:
    (tmp_path / ".env.example").write_text(EXAMPLE_ENV, encoding="utf-8", newline="\n")
    if with_env:
        (tmp_path / ".env").write_text(CUSTOM_ENV, encoding="utf-8", newline="\n")


def open_store(tmp_path: Path) -> Store:
    return Store(tmp_path / "harness.db")


def make_item(tmp_path: Path, *, state: str) -> int:
    """Create one work item and walk it to `state` through legal transitions."""
    store = open_store(tmp_path)
    store.migrate()
    item_id = store.create_work_item(
        kind="issue", external_ref="issue:816", title="bundle size check misreports esm"
    )
    for step in {"discovered": [], "proposed": ["proposed"], "approved": ["proposed", "approved"]}[
        state
    ]:
        store.transition(item_id, step, reason="test setup")
    store.close()
    return item_id


def stage_run_count(tmp_path: Path) -> int:
    store = open_store(tmp_path)
    rows = store.list_stage_runs()
    store.close()
    return len(rows)


def item_state(tmp_path: Path, item_id: int) -> str:
    store = open_store(tmp_path)
    item = store.get_work_item(item_id)
    store.close()
    return item.state


def make_which(missing: tuple[str, ...] = ()):
    def which(name: str):
        if name in missing:
            return None
        return f"C:\\fake\\bin\\{name}.exe"

    return which


def make_run(*, probe_stdout: str = "", probe_stderr: str = ""):
    """Fake subprocess.run: a version banner for everything but the --max-turns probe."""

    def run(argv, *args, **kwargs):
        argv = list(argv)
        if "--max-turns" in argv:
            return subprocess.CompletedProcess(argv, 1, probe_stdout, probe_stderr)
        return subprocess.CompletedProcess(argv, 0, "2.1.251 (Claude Code)\n", "")

    return run


PROBE_INPUT_MISSING = json.dumps(
    {"type": "result", "is_error": True, "result": "Input must be provided"}
)
PROBE_UNKNOWN_OPTION_STDERR = "error: unknown option '--max-turns'\n"


def forbid_clone(monkeypatch) -> None:
    def boom(self, item):
        raise CloneError("acquire must not be reached in this test")

    monkeypatch.setattr(clone_mod.CloneManager, "acquire", boom)


def forbid_network(monkeypatch) -> None:
    """GitHubReadOnly.get is the single request path; nothing here may take it."""

    def boom(self, path):
        raise GitHubError(f"no request may be issued in this test: {path}")

    monkeypatch.setattr(gh_mod.GitHubReadOnly, "get", boom)


# --------------------------------------------------------------------------
# B65 - every subcommand parses and dispatches
# --------------------------------------------------------------------------


def test_B65_init_status_halt_and_resume_parse_and_dispatch(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    write_repo(tmp_path)

    assert cli.main(["init"]) == 0
    assert cli.main(["status"]) == 0

    assert cli.main(["halt"]) == 0
    assert (tmp_path / "HALT").exists()

    assert cli.main(["resume"]) == 0
    assert not (tmp_path / "HALT").exists()

    capsys.readouterr()


def test_B65_the_global_config_flag_selects_the_env_file(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    write_repo(tmp_path)
    assert cli.main(["init"]) == 0
    capsys.readouterr()

    assert cli.main(["--config", str(tmp_path / ".env"), "status"]) == 0
    assert cli.main(["--verbose", "status"]) == 0
    capsys.readouterr()


def test_B65_discover_mode_audit_exits_2_and_says_not_implemented(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    write_repo(tmp_path)
    assert cli.main(["init"]) == 0
    forbid_network(monkeypatch)
    capsys.readouterr()

    assert cli.main(["discover", "--mode", "audit"]) == 2

    captured = capsys.readouterr()
    assert "not implemented in delivery 1" in captured.err


def test_B65_approve_on_a_discovered_item_exits_1(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    write_repo(tmp_path)
    assert cli.main(["init"]) == 0
    item_id = make_item(tmp_path, state="discovered")
    capsys.readouterr()

    assert cli.main(["approve", str(item_id)]) == 1
    assert item_state(tmp_path, item_id) == "discovered"


def test_B65_propose_on_an_unknown_item_exits_1(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    write_repo(tmp_path)
    assert cli.main(["init"]) == 0
    forbid_network(monkeypatch)
    capsys.readouterr()

    assert cli.main(["propose", "999"]) == 1


def test_B65_an_unknown_subcommand_exits_2_from_argparse(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    write_repo(tmp_path)

    with pytest.raises(SystemExit) as excinfo:
        cli.main(["frobnicate"])

    assert excinfo.value.code == 2
    capsys.readouterr()


# --------------------------------------------------------------------------
# B66 - init is idempotent and never overwrites .env
# --------------------------------------------------------------------------


def test_B66_a_second_init_leaves_a_pre_existing_env_byte_identical(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    write_repo(tmp_path)
    before = (tmp_path / ".env").read_bytes()

    assert cli.main(["init"]) == 0
    assert cli.main(["init"]) == 0

    assert (tmp_path / ".env").read_bytes() == before
    assert (tmp_path / "runs").is_dir()
    assert (tmp_path / "packages").is_dir()
    assert (tmp_path / "harness.db").exists()
    capsys.readouterr()


def test_B66_init_copies_the_example_env_when_none_exists(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    write_repo(tmp_path, with_env=False)
    assert not (tmp_path / ".env").exists()

    assert cli.main(["init"]) == 0

    assert (tmp_path / ".env").read_bytes() == (tmp_path / ".env.example").read_bytes()
    capsys.readouterr()


# --------------------------------------------------------------------------
# B67 / B70 - doctor
# --------------------------------------------------------------------------


def test_B67_doctor_exits_3_and_names_claude_when_the_binary_is_missing(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    write_repo(tmp_path)
    assert cli.main(["init"]) == 0
    capsys.readouterr()

    monkeypatch.setattr(cli, "WHICH", make_which(missing=("claude",)))
    monkeypatch.setattr(cli, "RUN", make_run(probe_stdout=PROBE_INPUT_MISSING))

    assert cli.main(["doctor"]) == 3
    assert "claude" in capsys.readouterr().out


def test_B67_doctor_exits_3_and_names_node_when_the_binary_is_missing(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    write_repo(tmp_path)
    assert cli.main(["init"]) == 0
    capsys.readouterr()

    monkeypatch.setattr(cli, "WHICH", make_which(missing=("node",)))
    monkeypatch.setattr(cli, "RUN", make_run(probe_stdout=PROBE_INPUT_MISSING))

    assert cli.main(["doctor"]) == 3
    assert "node" in capsys.readouterr().out


def test_B70_doctor_exits_0_and_reports_the_max_turns_probe(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    write_repo(tmp_path)
    assert cli.main(["init"]) == 0
    capsys.readouterr()

    monkeypatch.setattr(cli, "WHICH", make_which())
    monkeypatch.setattr(cli, "RUN", make_run(probe_stdout=PROBE_INPUT_MISSING))

    assert cli.main(["doctor"]) == 0
    assert "--max-turns" in capsys.readouterr().out


def test_B70_doctor_exits_3_when_the_probe_reports_an_unknown_option(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    write_repo(tmp_path)
    assert cli.main(["init"]) == 0
    capsys.readouterr()

    monkeypatch.setattr(cli, "WHICH", make_which())
    monkeypatch.setattr(
        cli, "RUN", make_run(probe_stdout="", probe_stderr=PROBE_UNKNOWN_OPTION_STDERR)
    )

    assert cli.main(["doctor"]) == 3
    assert "--max-turns" in capsys.readouterr().out


def test_B67_doctor_exits_3_when_the_halt_file_is_present(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    write_repo(tmp_path)
    assert cli.main(["init"]) == 0
    capsys.readouterr()
    (tmp_path / "HALT").write_text("", encoding="utf-8", newline="\n")

    monkeypatch.setattr(cli, "WHICH", make_which())
    monkeypatch.setattr(cli, "RUN", make_run(probe_stdout=PROBE_INPUT_MISSING))

    assert cli.main(["doctor"]) == 3
    capsys.readouterr()


# --------------------------------------------------------------------------
# B68 - status --json
# --------------------------------------------------------------------------


def test_B68_status_json_emits_valid_json_with_queue_and_budget(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    write_repo(tmp_path)
    assert cli.main(["init"]) == 0
    make_item(tmp_path, state="proposed")
    capsys.readouterr()

    assert cli.main(["status", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert "queue" in payload
    assert "budget" in payload
    assert payload["queue"]["proposed"] == 1
    assert "weekly_remaining_pct" in payload["budget"]
    assert "session_remaining_pct" in payload["budget"]
    assert "spendable_pct" in payload["budget"]


# --------------------------------------------------------------------------
# B69 - the halt file stops a run
# --------------------------------------------------------------------------


def test_B69_run_exits_5_and_starts_no_stage_when_the_halt_file_is_present(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    write_repo(tmp_path)
    assert cli.main(["init"]) == 0
    item_id = make_item(tmp_path, state="approved")
    (tmp_path / "HALT").write_text("", encoding="utf-8", newline="\n")
    forbid_clone(monkeypatch)
    capsys.readouterr()

    assert cli.main(["run", "--item", str(item_id)]) == 5
    assert stage_run_count(tmp_path) == 0


def test_B69_a_halted_run_leaves_the_item_approved_and_resumable(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    write_repo(tmp_path)
    assert cli.main(["init"]) == 0
    item_id = make_item(tmp_path, state="approved")
    (tmp_path / "HALT").write_text("", encoding="utf-8", newline="\n")
    forbid_clone(monkeypatch)
    capsys.readouterr()

    assert cli.main(["run", "--item", str(item_id)]) == 5
    assert item_state(tmp_path, item_id) == "approved"


# --------------------------------------------------------------------------
# B71 - --until
# --------------------------------------------------------------------------


def past_hhmm() -> str:
    """A local wall-clock time that has already passed.

    The CLI compares --until against the real local clock (RUN-DECISIONS), so
    this is the one place the suite reads it. One minute back, except inside the
    first minute of the day where that would wrap into the future.
    """
    now = datetime.now()
    if now.hour == 0 and now.minute == 0:
        return "00:00"
    return (now - timedelta(minutes=1)).strftime("%H:%M")


def test_B71_run_until_a_time_already_past_starts_no_stage_and_exits_0(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    write_repo(tmp_path)
    assert cli.main(["init"]) == 0
    item_id = make_item(tmp_path, state="approved")
    forbid_clone(monkeypatch)
    capsys.readouterr()

    assert cli.main(["run", "--item", str(item_id), "--until", past_hhmm()]) == 0
    assert stage_run_count(tmp_path) == 0
    assert item_state(tmp_path, item_id) == "approved"


# --------------------------------------------------------------------------
# B72 - archive refuses an item that is not packaged
# --------------------------------------------------------------------------


def test_B72_archive_refuses_a_discovered_item(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    write_repo(tmp_path)
    assert cli.main(["init"]) == 0
    item_id = make_item(tmp_path, state="discovered")
    capsys.readouterr()

    assert cli.main(["archive", str(item_id)]) == 1
    assert list((tmp_path / "packages").iterdir()) == []


def test_B72_archive_with_transcript_also_refuses_an_approved_item(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    write_repo(tmp_path)
    assert cli.main(["init"]) == 0
    item_id = make_item(tmp_path, state="approved")
    capsys.readouterr()

    assert cli.main(["archive", str(item_id), "--with-transcript"]) == 1
    assert list((tmp_path / "packages").iterdir()) == []
    assert item_state(tmp_path, item_id) == "approved"
