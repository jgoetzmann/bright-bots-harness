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


def test_B69_run_exits_5_when_the_halt_file_is_present_even_with_an_empty_queue(
    tmp_path, monkeypatch, capsys
):
    """B69: the kill switch is checked before the queue is consulted, not per stage only."""
    monkeypatch.chdir(tmp_path)
    write_repo(tmp_path)
    assert cli.main(["init"]) == 0
    (tmp_path / "HALT").write_text("", encoding="utf-8", newline="\n")
    forbid_clone(monkeypatch)
    capsys.readouterr()

    assert cli.main(["run"]) == 5
    assert stage_run_count(tmp_path) == 0
    assert not list((tmp_path / "runs").glob("*/clone"))


# --------------------------------------------------------------------------
# Delivery 2 — the CLI (handoff §3.2, §6.3, §6.4, §11.4; RUN-DECISIONS-D2 §9, §12, §14).
# Appended by the D2 spec-tester (T3); additions only (D2-R12.3).
# --------------------------------------------------------------------------

from datetime import timezone

# RUN-DECISIONS-D2 §2 — the new keys with their .env.example values.
D2_ENV_LINES = """\
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
"""
D2_ENV_BODY = ENV_BODY  # ENV_BODY already carries the D2 keys (DECISIONS D22)
# Handoff §6.5 — the keys `doctor` must name (A30).
D2_DOCTOR_KEYS = (
    "WEEKLY_CAP_USD",
    "PER_CALL_CAP_USD",
    "RESERVE_PCT",
    "MAX_CONCURRENT_ITEMS",
    "MAX_REVISE_CYCLES",
    "FORK_REPO",
    "UPSTREAM_REPO",
    "TRUST_FILE",
    "NOTIFY_POLL_HOURS",
    "MAX_SUBISSUES",
)
FORK = "brightboost-harness/brightboost"
RESET_AT = "2026-09-02T18:00:00Z"

# RUN-DECISIONS-D2 §9 — every spending command checks .harness/HALT first (sweep is feedback.yml's
# entry point, A43 "every spending entry point").
REPO_HALT_COMMANDS = (
    ["dispatch"],
    ["run"],
    ["discover", "--mode", "triage"],
    ["propose", "1"],
    ["deliver", "1"],
    ["revise", "1", "--source", "ci"],
    ["decompose", "1"],
    ["sweep"],
)

# A complete section 7.1 work package for issue 816 (inline; duplicated on purpose).
SPEC_816 = """# fix(scripts): bundle size check misreports esm chunks

## Issue
https://github.com/Bright-Bots-Initiative/brightboost/issues/816 - issue 816. The bundle size
gate fails builds that are comfortably inside the budget.

## Diagnosis
scripts/check-bundle-size.js sums every entry under dist/assets, including source maps and
fonts, so the reported total is roughly twice the shipped javascript.

## Approach
Restrict the sum to the emitted .js chunks and print a per-chunk breakdown.

## Slices
1. Restrict the asset glob to .js chunks.
2. Print a per-chunk size breakdown.

## Behaviors
1. The check ignores non-javascript assets.
2. The check fails when the javascript total exceeds the budget.

## Acceptance criteria
- The bundle size check exits 0 on a clean build of main.
- The failure message names the chunk that pushed the total over budget.

## Decisions
- Filter inside the script rather than changing the vite config.

## Open questions
None

## Touched paths
- scripts/check-bundle-size.js

## Risks
A narrower filter could hide a genuine regression in a non-javascript asset.
"""


def write_d2_repo(tmp_path: Path, *, with_env: bool = True, **overrides: object) -> None:
    """Like write_repo, with the D2 keys. An override replaces a line; None removes it."""
    lines: list[str] = []
    for line in D2_ENV_BODY.splitlines():
        key, _, _ = line.partition("=")
        if key in overrides:
            value = overrides.pop(key)
            if value is not None:
                lines.append(f"{key}={value}")
            continue
        lines.append(line)
    for key, value in overrides.items():
        if value is not None:
            lines.append(f"{key}={value}")
    body = "\n".join(lines) + "\n"
    (tmp_path / ".env.example").write_text(
        "# .env.example (D2)\n" + body, encoding="utf-8", newline="\n"
    )
    if with_env:
        (tmp_path / ".env").write_text(
            "# written by tests/test_cli.py (D2)\n" + body, encoding="utf-8", newline="\n"
        )


def engage_repo_halt(tmp_path: Path) -> Path:
    (tmp_path / ".harness").mkdir(exist_ok=True)
    path = tmp_path / ".harness" / "HALT"
    path.write_text("stop\n", encoding="utf-8", newline="\n")
    return path


def iso_now(offset_seconds: int = 0) -> str:
    """The real clock, because the CLI uses it (RUN-DECISIONS: `--until` compares against it)."""
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def write_ledger(
    tmp_path: Path,
    *,
    spent_usd: float = 0.0,
    rate_limited_until: str | None = None,
    calls: int = 0,
) -> Path:
    """A handoff §6.2 ledger whose window started a minute ago, so it cannot roll."""
    payload = {
        "schema": 1,
        "window": {
            "period_start": iso_now(-60),
            "spent_usd": spent_usd,
            "calls": calls,
            "rate_limited_until": rate_limited_until,
        },
        "observations": {},
        "cursors": {
            "notifications_last_seen": None,
            "seen_comment_ids": [],
            "keyword_denied": {},
        },
        "history": [],
    }
    (tmp_path / "state").mkdir(exist_ok=True)
    path = tmp_path / "state" / "ledger.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def read_ledger(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "state" / "ledger.json").read_text(encoding="utf-8"))


def forbid_everything(monkeypatch) -> None:
    forbid_clone(monkeypatch)
    forbid_network(monkeypatch)


def rate_limited_fixture() -> dict:
    """RUN-DECISIONS-D2 §12: a FakeRunner fixture with "rate_limited": true replays a rate limit."""
    return {
        "ok": False,
        "text": "",
        "turns": 0,
        "cost_usd": 0.0,
        "allowance_pct": None,
        "duration_ms": 12,
        "session_id": None,
        "exit_code": 1,
        "transcript": [],
        "error": "You've hit your usage limit. Resets at 2026-09-02T18:00:00Z",
        "rate_limited": True,
        "reset_at": RESET_AT,
    }


def doctor_ok(monkeypatch) -> None:
    monkeypatch.setattr(cli, "WHICH", make_which())
    monkeypatch.setattr(cli, "RUN", make_run(probe_stdout=PROBE_INPUT_MISSING))


def dispatch_plan(capsys) -> dict:
    rc = cli.main(["dispatch"])
    out = capsys.readouterr().out
    assert rc == 0, f"dispatch must exit 0; stdout was {out!r}"
    return json.loads(out)


# --------------------------------------------------------------------------
# B149 / B150 - .harness/HALT stops every spending entry point before anything else
# --------------------------------------------------------------------------


@pytest.mark.parametrize("argv", REPO_HALT_COMMANDS, ids=lambda argv: argv[0])
def test_B149_B150_repo_halt_exits_0_before_config_is_loaded_when_dot_env_is_missing(
    tmp_path, monkeypatch, capsys, argv
):
    """B149 / B150 / A43 (handoff §11.4, D2-R6.16): with .harness/HALT under the cwd every
    spending command exits 0 and says why — even with no .env at all, which proves the check
    precedes config loading, doctor and the dispatcher."""
    monkeypatch.chdir(tmp_path)
    engage_repo_halt(tmp_path)
    forbid_everything(monkeypatch)
    assert not (tmp_path / ".env").exists()

    assert cli.main(argv) == 0

    captured = capsys.readouterr()
    assert "halted by .harness/HALT" in captured.out
    assert not (tmp_path / "harness.db").exists()
    assert not (tmp_path / "runs").exists()
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize("argv", REPO_HALT_COMMANDS, ids=lambda argv: argv[0])
def test_B149_B150_repo_halt_spends_nothing_and_leaves_the_queue_untouched(
    tmp_path, monkeypatch, capsys, argv
):
    """B149 / B150 / A43 (handoff §11.4, D2-R6.16): with a full .env and an approved item,
    .harness/HALT still means exit 0, the halt message, no stage run, no clone."""
    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path)
    assert cli.main(["init"]) == 0
    item_id = make_item(tmp_path, state="approved")
    engage_repo_halt(tmp_path)
    forbid_everything(monkeypatch)
    capsys.readouterr()

    assert cli.main(argv) == 0

    captured = capsys.readouterr()
    assert "halted by .harness/HALT" in captured.out
    assert stage_run_count(tmp_path) == 0
    assert item_state(tmp_path, item_id) == "approved"
    assert not list((tmp_path / "runs").glob("*/clone"))


def test_B149_repo_halt_beats_a_config_error(tmp_path, monkeypatch, capsys):
    """B149 / B150 (handoff §11.4): a broken .env would exit non-zero; the halt check runs first
    and wins with exit 0."""
    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path, WEEKLY_CAP_USD="-1")
    engage_repo_halt(tmp_path)
    forbid_everything(monkeypatch)

    assert cli.main(["dispatch"]) == 0
    assert "halted by .harness/HALT" in capsys.readouterr().out


def test_B148_the_delivery_1_halt_file_still_exits_5_under_d2_config(tmp_path, monkeypatch, capsys):
    """B148 (handoff §11.4) / RUN-DECISIONS-D2 §9: local mode keeps the D1 HALT semantics — exit 5,
    nothing started, the item resumable."""
    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path)
    assert cli.main(["init"]) == 0
    item_id = make_item(tmp_path, state="approved")
    (tmp_path / "HALT").write_text("", encoding="utf-8", newline="\n")
    forbid_everything(monkeypatch)
    capsys.readouterr()

    assert cli.main(["run", "--item", str(item_id)]) == 5
    assert stage_run_count(tmp_path) == 0
    assert item_state(tmp_path, item_id) == "approved"


def test_B149_without_the_repo_halt_file_dispatch_is_not_halted(tmp_path, monkeypatch, capsys):
    """B149 (handoff §11.4): the halt message appears only when the file exists."""
    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path)
    assert cli.main(["init"]) == 0
    forbid_everything(monkeypatch)
    capsys.readouterr()

    assert cli.main(["dispatch"]) == 0
    assert "halted by .harness/HALT" not in capsys.readouterr().out


# --------------------------------------------------------------------------
# A33 / B121 / B122 - dispatch is pure: a JSON plan, nothing started
# --------------------------------------------------------------------------


def test_A33_dispatch_prints_a_json_plan_with_start_reason_skipped_and_starts_nothing(
    tmp_path, monkeypatch, capsys
):
    """A33 / B122 (handoff §6.4, D2-R6.4): `dispatch` emits {"start","reason","skipped"} in that
    order, lists the approved item, and starts no stage run and no clone."""
    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path)
    assert cli.main(["init"]) == 0
    item_id = make_item(tmp_path, state="approved")
    forbid_everything(monkeypatch)
    capsys.readouterr()

    plan = dispatch_plan(capsys)

    assert list(plan) == ["start", "reason", "skipped"]
    assert plan["start"] == [item_id]
    assert plan["skipped"] == {}
    assert plan["reason"] == "budget 100% remaining, 1 of max 1 slots"
    assert stage_run_count(tmp_path) == 0
    assert item_state(tmp_path, item_id) == "approved"
    assert not list((tmp_path / "runs").glob("*/clone"))


def test_R6_5_two_dispatches_over_an_unchanged_ledger_are_byte_identical(
    tmp_path, monkeypatch, capsys
):
    """A33 / D2-R6.5 (handoff §6.4): the dispatcher is pure — same ledger, same bytes."""
    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path)
    assert cli.main(["init"]) == 0
    make_item(tmp_path, state="approved")
    write_ledger(tmp_path, spent_usd=1.25, calls=2)
    forbid_everything(monkeypatch)
    capsys.readouterr()

    assert cli.main(["dispatch"]) == 0
    first = capsys.readouterr().out
    assert cli.main(["dispatch"]) == 0
    second = capsys.readouterr().out

    assert first == second
    assert json.loads(first)["start"] != []


def test_B122_dispatch_does_not_modify_the_ledger_file(tmp_path, monkeypatch, capsys):
    """B122 (handoff §6.4): dispatch is pure; the ledger on disk is byte-identical afterwards."""
    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path)
    assert cli.main(["init"]) == 0
    make_item(tmp_path, state="approved")
    ledger_path = write_ledger(tmp_path, spent_usd=1.25, calls=2)
    before = ledger_path.read_bytes()
    forbid_everything(monkeypatch)
    capsys.readouterr()

    assert cli.main(["dispatch"]) == 0

    assert ledger_path.read_bytes() == before
    capsys.readouterr()


def test_B122_dispatch_with_an_empty_queue_emits_an_empty_start_list(tmp_path, monkeypatch, capsys):
    """B122 (handoff §6.4): no approved items means start == [] and the plan is still valid JSON."""
    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path)
    assert cli.main(["init"]) == 0
    make_item(tmp_path, state="proposed")
    forbid_everything(monkeypatch)
    capsys.readouterr()

    plan = dispatch_plan(capsys)

    assert plan["start"] == []
    assert isinstance(plan["reason"], str) and plan["reason"]
    assert plan["skipped"] == {}


def test_B121_dispatch_starts_nothing_while_rate_limited(tmp_path, monkeypatch, capsys):
    """B121 (handoff §6.3, D2-R6.6): now < rate_limited_until → empty plan, the reason names
    the rate limit and its reset time."""
    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path)
    assert cli.main(["init"]) == 0
    make_item(tmp_path, state="approved")
    write_ledger(tmp_path, rate_limited_until="2099-01-01T00:00:00Z")
    forbid_everything(monkeypatch)
    capsys.readouterr()

    plan = dispatch_plan(capsys)

    assert plan["start"] == []
    assert plan["reason"] == "rate limited until 2099-01-01T00:00:00Z"
    assert stage_run_count(tmp_path) == 0


def test_B121_dispatch_resumes_once_the_rate_limit_has_passed(tmp_path, monkeypatch, capsys):
    """B121 (handoff §6.3): a rate_limited_until in the past no longer blocks the plan."""
    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path)
    assert cli.main(["init"]) == 0
    item_id = make_item(tmp_path, state="approved")
    write_ledger(tmp_path, rate_limited_until="2001-01-01T00:00:00Z")
    forbid_everything(monkeypatch)
    capsys.readouterr()

    plan = dispatch_plan(capsys)

    assert plan["start"] == [item_id]
    assert "rate limited" not in plan["reason"]


def test_R6_7_dispatch_at_the_reserve_boundary_emits_an_empty_plan_with_reason_reserve(
    tmp_path, monkeypatch, capsys
):
    """B122 selection step 3 (handoff §6.4, D2-R6.7): spent >= cap × (1 − reserve/100) → empty
    plan, reason `reserve`. Cap 25, reserve 10 %, spent 22.50."""
    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path, RESERVE_PCT="10")
    assert cli.main(["init"]) == 0
    make_item(tmp_path, state="approved")
    write_ledger(tmp_path, spent_usd=22.5, calls=9)
    forbid_everything(monkeypatch)
    capsys.readouterr()

    plan = dispatch_plan(capsys)

    assert plan["start"] == []
    assert plan["reason"] == "reserve"
    assert stage_run_count(tmp_path) == 0


def test_R6_7_dispatch_beyond_the_reserve_is_also_reserve(tmp_path, monkeypatch, capsys):
    """B122 selection step 3 (handoff §6.4): overspend past the cap is still `reserve`."""
    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path, RESERVE_PCT="10")
    assert cli.main(["init"]) == 0
    make_item(tmp_path, state="approved")
    write_ledger(tmp_path, spent_usd=26.0, calls=12)
    forbid_everything(monkeypatch)
    capsys.readouterr()

    plan = dispatch_plan(capsys)

    assert plan["start"] == []
    assert plan["reason"] == "reserve"


def test_R6_7_dispatch_just_below_the_reserve_skips_an_unaffordable_item(
    tmp_path, monkeypatch, capsys
):
    """B122 selection step 6 (handoff §6.4, RUN-DECISIONS-D2 §5): remaining 0.50 < the 2.50
    static implement estimate → the item is skipped with the exact reason string."""
    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path, RESERVE_PCT="10")
    assert cli.main(["init"]) == 0
    item_id = make_item(tmp_path, state="approved")
    write_ledger(tmp_path, spent_usd=22.0, calls=8)
    forbid_everything(monkeypatch)
    capsys.readouterr()

    plan = dispatch_plan(capsys)

    assert plan["start"] == []
    assert plan["skipped"] == {str(item_id): "estimate $2.50 exceeds remaining $0.50"}
    assert plan["reason"].startswith("budget ")
    assert plan["reason"].endswith("0 of max 1 slots")


def test_B121_dispatch_with_the_d1_halt_file_starts_nothing(tmp_path, monkeypatch, capsys):
    """B122 selection step 2 / RUN-DECISIONS-D2 §14: `halted = repo_halted(...) or
    halted(config.halt_file)` — with the D1 HALT file the plan is empty (or the D1 exit 5)."""
    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path)
    assert cli.main(["init"]) == 0
    make_item(tmp_path, state="approved")
    (tmp_path / "HALT").write_text("", encoding="utf-8", newline="\n")
    forbid_everything(monkeypatch)
    capsys.readouterr()

    rc = cli.main(["dispatch"])
    out = capsys.readouterr().out

    if rc == 0:
        plan = json.loads(out)
        assert plan["start"] == []
        assert plan["reason"] == "halted"
    else:
        assert rc == 5
    assert stage_run_count(tmp_path) == 0


# --------------------------------------------------------------------------
# A30 - doctor names every new key and exits 3 on a missing or out-of-range one
# --------------------------------------------------------------------------


def test_A30_doctor_names_every_new_config_key(tmp_path, monkeypatch, capsys):
    """A30 (handoff §6.5, D2-R6.2): `doctor` exits 0 and prints every §6.5 key with its value."""
    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path)
    assert cli.main(["init"]) == 0
    doctor_ok(monkeypatch)
    capsys.readouterr()

    assert cli.main(["doctor"]) == 0

    out = capsys.readouterr().out
    for key in D2_DOCTOR_KEYS:
        assert key in out, f"doctor must name {key} (A30)"
    assert "25.00" in out or "25.0" in out


def test_A30_doctor_exits_3_and_names_a_missing_weekly_cap(tmp_path, monkeypatch, capsys):
    """A30 (handoff §6.5, D2-R6.3): WEEKLY_CAP_USD removed from .env → exit 3, the key named."""
    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path, WEEKLY_CAP_USD=None)
    doctor_ok(monkeypatch)
    forbid_everything(monkeypatch)
    capsys.readouterr()

    assert cli.main(["doctor"]) == 3

    captured = capsys.readouterr()
    assert "WEEKLY_CAP_USD" in captured.out + captured.err


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("MAX_SUBISSUES", "51"),
        ("NOTIFY_POLL_HOURS", "0"),
        ("PER_CALL_CAP_USD", "0"),
        ("STORE_BACKEND", "bogus"),
        ("MAX_CONCURRENT_ITEMS", "2"),
    ],
)
def test_A30_doctor_exits_3_and_names_an_out_of_range_key(tmp_path, monkeypatch, capsys, key, value):
    """A30 (handoff §6.5): an out-of-range value is a degraded doctor naming the key."""
    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path, **{key: value})
    doctor_ok(monkeypatch)
    forbid_everything(monkeypatch)
    capsys.readouterr()

    assert cli.main(["doctor"]) == 3

    captured = capsys.readouterr()
    assert key in captured.out + captured.err


def test_B112_doctor_exits_3_and_names_an_unknown_config_json_key(tmp_path, monkeypatch, capsys):
    """A30 / B112 (handoff §5.5, §6.5): a typo'd key in .harness/config.json is a startup error
    doctor reports by name."""
    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path)
    (tmp_path / ".harness").mkdir()
    (tmp_path / ".harness" / "config.json").write_text(
        json.dumps({"WEEKLY_CAP_USD": 25.0, "WEEKLY_CAP_USDD": 30.0}) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    doctor_ok(monkeypatch)
    forbid_everything(monkeypatch)
    capsys.readouterr()

    assert cli.main(["doctor"]) == 3

    captured = capsys.readouterr()
    assert "WEEKLY_CAP_USDD" in captured.out + captured.err


# --------------------------------------------------------------------------
# harness ledger [--json]
# --------------------------------------------------------------------------


def test_ledger_json_prints_valid_json_with_the_window(tmp_path, monkeypatch, capsys):
    """Handoff §3.2 / RUN-DECISIONS-D2 §14: `ledger --json` prints the window, observations
    and rate-limit state as JSON; B116's ledger shape is what comes back."""
    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path)
    assert cli.main(["init"]) == 0
    forbid_everything(monkeypatch)
    capsys.readouterr()

    assert cli.main(["ledger", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert "window" in payload
    assert "observations" in payload
    for key in ("period_start", "spent_usd", "calls", "rate_limited_until"):
        assert key in payload["window"]


def test_ledger_json_reflects_the_ledger_file(tmp_path, monkeypatch, capsys):
    """Handoff §3.2, §6.2: the printed window is the one in state/ledger.json."""
    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path)
    assert cli.main(["init"]) == 0
    write_ledger(tmp_path, spent_usd=12.41, calls=37, rate_limited_until=RESET_AT)
    forbid_everything(monkeypatch)
    capsys.readouterr()

    assert cli.main(["ledger", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["window"]["spent_usd"] == pytest.approx(12.41)
    assert payload["window"]["calls"] == 37
    assert payload["window"]["rate_limited_until"] == RESET_AT


def test_ledger_plain_output_mentions_the_spend_and_the_rate_limit(tmp_path, monkeypatch, capsys):
    """Handoff §3.2 / RUN-DECISIONS-D2 §14: the human form prints spend and rate-limit state."""
    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path)
    assert cli.main(["init"]) == 0
    write_ledger(tmp_path, spent_usd=12.41, calls=37, rate_limited_until=RESET_AT)
    forbid_everything(monkeypatch)
    capsys.readouterr()

    assert cli.main(["ledger"]) == 0

    out = capsys.readouterr().out
    assert "12.41" in out
    assert RESET_AT in out


# --------------------------------------------------------------------------
# B105 - sync-fork fails loudly on divergence
# --------------------------------------------------------------------------


def test_B105_sync_fork_exits_1_on_divergence_and_names_both_shas(tmp_path, monkeypatch, capsys):
    """B105 / A36 (handoff §4.4, D2-R6.12): ForkDiverged → exit 1, both shas on stderr."""
    from harness.errors import ForkDiverged

    fork_sha = "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c"
    upstream_sha = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4"

    def diverged(*args, **kwargs):
        raise ForkDiverged(
            f"fork main {fork_sha} has diverged from upstream main {upstream_sha}; nothing pushed"
        )

    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path, FORK_REPO=FORK)
    assert cli.main(["init"]) == 0
    monkeypatch.setattr(clone_mod, "sync_fork", diverged)
    monkeypatch.setattr(cli, "sync_fork", diverged, raising=False)
    forbid_everything(monkeypatch)
    capsys.readouterr()

    assert cli.main(["sync-fork"]) == 1

    captured = capsys.readouterr()
    assert fork_sha in captured.err
    assert upstream_sha in captured.err


def test_B105_sync_fork_exits_0_when_the_fork_fast_forwards(tmp_path, monkeypatch, capsys):
    """B105 (handoff §4.4): a fast-forward (or already-equal) sync is exit 0."""
    calls: list[dict] = []

    def fast_forward(config, **kwargs):
        calls.append({"config": config, **kwargs})
        return "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4"

    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path, FORK_REPO=FORK)
    assert cli.main(["init"]) == 0
    monkeypatch.setattr(clone_mod, "sync_fork", fast_forward)
    monkeypatch.setattr(cli, "sync_fork", fast_forward, raising=False)
    forbid_everything(monkeypatch)
    capsys.readouterr()

    assert cli.main(["sync-fork"]) == 0

    assert len(calls) == 1
    assert calls[0]["config"].fork_repo == FORK
    assert "workdir" in calls[0] and isinstance(calls[0]["workdir"], Path)
    assert callable(calls[0]["push"])
    capsys.readouterr()


def test_B105_sync_fork_is_not_reached_when_the_repo_is_halted(tmp_path, monkeypatch, capsys):
    """B105 / B149: sync-fork runs after the HALT check in every workflow; a diverging fake
    proves the CLI does not call it while halted (the halt exits 0 first)."""
    from harness.errors import ForkDiverged

    def diverged(*args, **kwargs):
        raise ForkDiverged("must not be called")

    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path, FORK_REPO=FORK)
    assert cli.main(["init"]) == 0
    engage_repo_halt(tmp_path)
    monkeypatch.setattr(clone_mod, "sync_fork", diverged)
    monkeypatch.setattr(cli, "sync_fork", diverged, raising=False)
    forbid_everything(monkeypatch)
    capsys.readouterr()

    rc = cli.main(["run"])

    assert rc == 0
    assert "halted by .harness/HALT" in capsys.readouterr().out


# --------------------------------------------------------------------------
# B120 - a rate limit returns the item to its previous state, records the reset, exits 0
# --------------------------------------------------------------------------


def test_B120_run_item_returns_the_item_to_approved_and_records_the_reset_when_rate_limited(
    tmp_path, monkeypatch, capsys
):
    """B120 / A39 (handoff §6.3, D2-R6.13): a RateLimited implement call puts the item back to
    `approved`, writes rate_limited_until to the ledger, says so, and exits 0.

    The clone and the gates are faked through the D1 injection points (CloneManager.acquire,
    implement.GATE_RUNNER/PRETTIER/CHANGED_PATHS/COMMIT) so the only thing exercised is the
    rate-limit path from the fake runner to the CLI exit code."""
    import harness.stages.implement as implement_mod
    from harness.runner.fake import FakeRunner

    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path)
    assert cli.main(["init"]) == 0
    item_id = make_item(tmp_path, state="approved")

    spec_path = tmp_path / "runs" / f"item-{item_id}" / "spec" / f"{item_id}.md"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(SPEC_816, encoding="utf-8", newline="\n")
    store = open_store(tmp_path)
    store.update_work_item(item_id, spec_path=str(spec_path))
    store.close()

    clone_dir = tmp_path / "runs" / f"item-{item_id}" / "clone"
    clone_dir.mkdir(parents=True, exist_ok=True)

    def fake_acquire(self, item, *args, **kwargs):
        return clone_mod.Lease(
            run_id=f"item-{item.id}",
            path=clone_dir,
            base_sha="0123456789abcdef0123456789abcdef01234567",
            branch="harness/fix-816-bundle-size-check-misreports-esm",
        )

    monkeypatch.setattr(clone_mod.CloneManager, "acquire", fake_acquire)
    monkeypatch.setattr(implement_mod, "GATE_RUNNER", lambda clone, *a, **k: [], raising=False)
    monkeypatch.setattr(implement_mod, "PRETTIER", lambda *a, **k: (True, ""), raising=False)
    monkeypatch.setattr(implement_mod, "CHANGED_PATHS", lambda *a, **k: [], raising=False)
    monkeypatch.setattr(implement_mod, "COMMIT", lambda *a, **k: None, raising=False)

    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "implement.json").write_text(
        json.dumps(rate_limited_fixture(), indent=2), encoding="utf-8", newline="\n"
    )
    real_init = FakeRunner.__init__

    def fixture_dir_init(self, fixtures_dir=None, *args, **kwargs):
        real_init(self, fixtures)

    monkeypatch.setattr(FakeRunner, "__init__", fixture_dir_init)
    forbid_network(monkeypatch)
    capsys.readouterr()

    rc = cli.main(["run", "--item", str(item_id)])

    captured = capsys.readouterr()
    assert rc == 0, f"a rate limit is a normal condition, exit 0 (B120); got {rc}: {captured}"
    assert item_state(tmp_path, item_id) == "approved"
    assert (tmp_path / "state" / "ledger.json").is_file(), "the ledger must be saved (B120)"
    assert read_ledger(tmp_path)["window"]["rate_limited_until"] == RESET_AT
    assert "rate" in (captured.out + captured.err).lower()
    assert not list((tmp_path / "packages").iterdir())


# --------------------------------------------------------------------------
# B65 (D2) - the new subcommands parse, and refuse what they must
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command", ["dispatch", "deliver", "revise", "decompose", "sweep", "ledger", "sync-fork"]
)
def test_B65_d2_every_new_subcommand_is_known_to_argparse(tmp_path, monkeypatch, capsys, command):
    """B65 / handoff §3.2: each new subcommand parses (`--help` exits 0 from argparse)."""
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as excinfo:
        cli.main([command, "--help"])

    assert excinfo.value.code == 0
    capsys.readouterr()


def test_B65_d2_local_loop_subcommand_exists(tmp_path, monkeypatch, capsys):
    """RUN-DECISIONS-D2 §16: `harness local-loop` is a subcommand."""
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as excinfo:
        cli.main(["local-loop", "--help"])

    assert excinfo.value.code == 0
    capsys.readouterr()


def test_B65_d2_the_global_dry_run_flag_parses(tmp_path, monkeypatch, capsys):
    """RUN-DECISIONS-D2 §14: global `--dry-run` is accepted in front of any subcommand."""
    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path)
    assert cli.main(["init"]) == 0
    forbid_everything(monkeypatch)
    capsys.readouterr()

    assert cli.main(["--dry-run", "status"]) == 0
    capsys.readouterr()


def test_B65_d2_init_labels_is_a_no_op_without_write_access(tmp_path, monkeypatch, capsys):
    """RUN-DECISIONS-D2 §14: `init --labels` with no token (tier 0) exits 0 with a message and
    issues no request."""
    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path)
    forbid_everything(monkeypatch)

    assert cli.main(["init", "--labels"]) == 0

    assert capsys.readouterr().out.strip() != ""


def test_B65_d2_deliver_on_an_unknown_item_exits_1(tmp_path, monkeypatch, capsys):
    """B65 / handoff §3.2: `deliver <id>` for an item that does not exist is exit 1."""
    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path)
    assert cli.main(["init"]) == 0
    forbid_everything(monkeypatch)
    capsys.readouterr()

    assert cli.main(["deliver", "999"]) == 1
    capsys.readouterr()


def test_B65_d2_deliver_refuses_an_item_that_is_not_packaged(tmp_path, monkeypatch, capsys):
    """Handoff §4.5 / RUN-DECISIONS-D2 §13: deliver requires state `packaged`; an approved item
    is refused, left approved, and nothing is pushed."""
    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path)
    assert cli.main(["init"]) == 0
    item_id = make_item(tmp_path, state="approved")
    forbid_everything(monkeypatch)
    capsys.readouterr()

    assert cli.main(["deliver", str(item_id)]) == 1
    assert item_state(tmp_path, item_id) == "approved"
    assert stage_run_count(tmp_path) == 0
    capsys.readouterr()


def test_B65_d2_deliver_requires_an_integer_id(tmp_path, monkeypatch, capsys):
    """B65 / handoff §3.2: `deliver <id>` takes an item id; text is an argparse error."""
    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path)

    with pytest.raises(SystemExit) as excinfo:
        cli.main(["deliver", "eight-one-six"])

    assert excinfo.value.code == 2
    capsys.readouterr()


def test_B65_d2_revise_requires_a_source(tmp_path, monkeypatch, capsys):
    """B65 / handoff §3.2: `revise <id> --source ci|conflict|review` — the source is mandatory."""
    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path)

    with pytest.raises(SystemExit) as excinfo:
        cli.main(["revise", "1"])

    assert excinfo.value.code == 2
    capsys.readouterr()


def test_B65_d2_revise_rejects_an_unknown_source(tmp_path, monkeypatch, capsys):
    """B65 / handoff §3.2: the source enum is closed."""
    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path)

    with pytest.raises(SystemExit) as excinfo:
        cli.main(["revise", "1", "--source", "vibes"])

    assert excinfo.value.code == 2
    capsys.readouterr()


@pytest.mark.parametrize("source", ["ci", "conflict", "review"])
def test_B65_d2_revise_on_an_unknown_item_exits_1(tmp_path, monkeypatch, capsys, source):
    """B65 / handoff §3.2: every legal source parses; an unknown item is exit 1."""
    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path)
    assert cli.main(["init"]) == 0
    forbid_everything(monkeypatch)
    capsys.readouterr()

    assert cli.main(["revise", "999", "--source", source]) == 1
    capsys.readouterr()


def test_B65_d2_revise_refuses_an_item_that_is_not_shipped(tmp_path, monkeypatch, capsys):
    """Handoff §9 / RUN-DECISIONS-D2 §13: revise requires `shipped` (or `needs-human` with an
    explicit fix); an approved item is refused, untouched, and nothing is cloned."""
    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path)
    assert cli.main(["init"]) == 0
    item_id = make_item(tmp_path, state="approved")
    forbid_everything(monkeypatch)
    capsys.readouterr()

    assert cli.main(["revise", str(item_id), "--source", "ci"]) == 1
    assert item_state(tmp_path, item_id) == "approved"
    assert stage_run_count(tmp_path) == 0
    capsys.readouterr()


def test_B65_d2_decompose_requires_an_integer_issue(tmp_path, monkeypatch, capsys):
    """B65 / handoff §3.2: `decompose <issue>` takes an issue number."""
    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path)

    with pytest.raises(SystemExit) as excinfo:
        cli.main(["decompose", "not-a-number"])

    assert excinfo.value.code == 2
    capsys.readouterr()


def test_B65_d2_decompose_of_an_unreachable_issue_exits_1_without_a_model_call(
    tmp_path, monkeypatch, capsys
):
    """B65 / B110: with no network the issue cannot be read; decompose is exit 1 and no stage
    run is opened (the model is never called before the issue is known)."""
    monkeypatch.chdir(tmp_path)
    write_d2_repo(tmp_path)
    assert cli.main(["init"]) == 0
    forbid_everything(monkeypatch)
    capsys.readouterr()

    assert cli.main(["decompose", "999"]) == 1
    assert stage_run_count(tmp_path) == 0
    capsys.readouterr()
