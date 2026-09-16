"""Local-mode control-plane tests: the shell and PowerShell half of the harness.

Nothing under ``local/`` is Python. The first test feeds the entrypoint's real argv to the real
parser; the rest are text assertions that duplicate what ``local/preflight.py`` checks on the
host, so a change to the shell scripts fails here too.

Stack: Python 3.13 standard library + pytest==8.3.4 only.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

import pytest

from harness.__main__ import build_parser

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCAL = REPO_ROOT / "local"
ENTRYPOINT = LOCAL / "entrypoint.sh"
RUN_PS1 = LOCAL / "run.ps1"
WATCHDOG = LOCAL / "watchdog-bb.ps1"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _exec_line() -> str:
    lines = [ln for ln in _read(ENTRYPOINT).splitlines() if ln.startswith("exec ")]
    assert len(lines) == 1, f"local/entrypoint.sh must have exactly one exec line, got {lines}"
    return lines[0]


def _exec_argv() -> list[str]:
    """The entrypoint's exec line as the argv `harness` actually receives.

    `$WORK`/`$LOOP_SECONDS` take the entrypoint's own defaults; `"$@"` is empty because
    local/run.ps1 appends nothing after the image name.
    """
    tokens = shlex.split(_exec_line())
    assert tokens[:4] == ["exec", "python", "-m", "harness"], tokens[:4]
    subs = {"$WORK": "/work", "$WORK/.env": "/work/.env", "$LOOP_SECONDS": "300"}
    return [subs.get(t, t) for t in tokens[4:] if t != "$@"]


# --- the entrypoint's exec line ------------------------------------------------------------


def test_audit5_the_entrypoint_argv_parses_against_the_real_parser():
    """`--config` is defined on the top-level parser only, so argparse rejects it after the
    subcommand, and the container would exit 2 after passing every gate."""
    args = build_parser().parse_args(_exec_argv())
    assert args.command == "local-loop"
    assert args.config == "/work/.env"
    assert args.work == "/work"
    assert args.loop_seconds == 300


def test_audit5_the_old_argv_order_is_still_rejected_by_argparse():
    """A global flag after the subcommand exits 2."""
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(
            ["local-loop", "--work", "/work", "--config", "/work/.env"]
        )
    assert excinfo.value.code == 2


def test_audit5_global_flags_precede_the_subcommand_in_the_exec_line():
    line = _exec_line()
    assert line.index("--config") < line.index("local-loop"), line


def test_audit5_every_doc_that_quotes_the_invocation_quotes_the_one_that_parses():
    """A doc showing the broken order teaches an operator to type it by hand."""
    for rel in ("local/README.md", "docs/LOCAL-MODE.md", "local/run.ps1", "local/entrypoint.sh"):
        for number, line in enumerate(_read(REPO_ROOT / rel).splitlines(), 1):
            if "local-loop" not in line or "--config" not in line:
                continue
            assert line.index("--config") < line.index("local-loop"), (
                f"{rel}:{number} quotes --config after the subcommand: {line.strip()}"
            )


# --- every BB_* knob has a reader ----------------------------------------------------------


def test_audit9_the_exec_line_passes_loop_seconds_through():
    """Without this flag `cmd_local_loop` takes argparse's 300 s default and
    `run.loop_seconds` does nothing."""
    assert "--loop-seconds" in _exec_line()
    assert "BB_LOOP_SECONDS" in _read(ENTRYPOINT), "the entrypoint must be the variable's reader"


def test_audit9_every_bb_variable_run_ps1_sets_is_read_by_the_entrypoint():
    """No file under harness/ may read a BB_ variable (I-4 confines os.environ to config.py,
    which has no BB_ key), so entrypoint.sh is the only reader."""
    passed = sorted(set(re.findall(r'"(BB_[A-Z0-9_]+)=', _read(RUN_PS1))))
    assert passed, "run.ps1 should still set BB_WORK_DIR and BB_LOOP_SECONDS"
    gate = _read(ENTRYPOINT)
    assert [name for name in passed if name in gate] == passed, (
        f"run.ps1 sets {[n for n in passed if n not in gate]}, which entrypoint.sh never reads"
    )


def test_audit9_no_harness_python_file_reads_a_bb_variable():
    offenders = [
        str(p.relative_to(REPO_ROOT))
        for p in (REPO_ROOT / "harness").rglob("*.py")
        if "__pycache__" not in p.parts and "BB_" in _read(p)
    ]
    assert offenders == [], offenders


def test_audit9_the_dead_max_items_per_unit_knob_is_gone_from_every_control_surface():
    """Per-unit item count is the dispatcher's MAX_CONCURRENT_ITEMS (B123); `local-loop` has no
    per-unit flag, so no control surface carries one."""
    for rel in ("bb-config.json", "bb-start.ps1", "bb-watcher.ps1", "local/run.ps1"):
        body = _read(REPO_ROOT / rel)
        code = [ln for ln in body.splitlines() if not ln.strip().startswith("#")]
        for token in ("max_items_per_unit", "MaxItemsPerUnit", "BB_MAX_ITEMS_PER_UNIT"):
            assert not any(token in ln for ln in code), f"{rel} still carries {token}"
    assert json.loads(_read(REPO_ROOT / "bb-config.json"))["run"] == {"loop_seconds": 300}


def test_audit9_bb_config_json_and_the_configure_schema_hold_the_same_keys():
    """Two halves of one table: a file key with no SCHEMA entry is dropped by `load`, and a SCHEMA
    key with no file entry still prints a value in `show`."""
    data = json.loads(_read(REPO_ROOT / "bb-config.json"))
    in_file = {
        f"{section}.{name}"
        for section, values in data.items()
        if not section.startswith("_") and isinstance(values, dict)
        for name in values
    }
    in_schema = set(
        re.findall(r'^\s*"([a-z_]+\.[a-z_0-9]+)":', _read(REPO_ROOT / "bb-configure.py"), re.M)
    )
    assert in_file == in_schema


# --- the two publishers agree -------------------------------------------------------------

# A force with no lease: as a quoted argument (how gh.py builds argv, lines away from the word
# "push") or as a bare token on a push line (how PowerShell writes it). Neither shape matches
# --force-with-lease, and neither matches ``-f`` inside a docstring's backticks.
ARG_FORCE = re.compile(r"""['"](?:--force(?!-with-lease)|-f)['"]""")
LINE_FORCE = re.compile(r"--force(?!-with-lease)|(?<![\w-])-f(?![\w-])")


def _bare_force_lines(body: str, comment: str) -> list[str]:
    out = []
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith(comment):
            continue
        if ARG_FORCE.search(line) or ("push" in line and LINE_FORCE.search(line)):
            out.append(line)
    return out


def test_audit13_the_watchdog_never_pushes_with_a_bare_force():
    """In local mode the watchdog is the only publisher, and a bare `--force` would discard a
    commit someone pushed to the same fork branch between two passes. gh.push_branch follows
    the same rule."""
    assert _bare_force_lines(_read(WATCHDOG), "#") == []


def test_audit13_the_python_publisher_never_pushes_with_a_bare_force():
    assert _bare_force_lines(_read(REPO_ROOT / "harness" / "gh.py"), "#") == []


def test_audit13_the_watchdogs_lease_carries_an_explicit_expected_sha():
    """A bare `--force-with-lease` takes its expectation from a remote-tracking ref. The watchdog
    pushes to a URL, which has none, so git rejects the bare form with "stale info"."""
    body = _read(WATCHDOG)
    assert "--force-with-lease=refs/heads/" in body
    for line in body.splitlines():
        if "--force-with-lease" in line and not line.strip().startswith("#"):
            assert "--force-with-lease=" in line, line.strip()


def test_audit13_the_lease_expects_the_sha_the_watchdog_last_pushed():
    """A lease against a freshly read remote sha agrees with whatever someone else just pushed.
    The expectation is the sha this publisher last left, recorded in runs/<item>/PUSHED."""
    body = _read(WATCHDOG)
    assert re.search(r'--force-with-lease=refs/heads/\$\{branch\}:\$lease', body), body[:0]
    marker_block = body[body.index('$marker = Join-Path'):body.index('$author =')]
    assert "$lease" in marker_block, "the lease must be read from the PUSHED marker"
    assert "ls-remote" not in marker_block, (
        "the lease must not be taken from the fork's current sha - that is not a lease"
    )


def test_audit13_the_watchdog_reads_git_through_the_call_operator():
    """`cmd /c "git -C `"$path`" ..."` inside an interpolated "$( ... )" hands git " <path>" with
    a leading space, so git exits 128 and nothing is pushed."""
    code = [ln for ln in _read(WATCHDOG).splitlines() if not ln.strip().startswith("#")]
    assert [ln for ln in code if 'cmd /c "git' in ln] == []


def test_audit13_preflight_holds_the_publishers_together():
    """These invariants also have to hold on the host, where preflight runs before a start."""
    body = _read(LOCAL / "preflight.py")
    for name in ("check_publishers_agree", "check_entrypoint_invocation",
                 "check_bb_env_has_readers", "check_config_schema_agrees"):
        assert f"def {name}(" in body, f"local/preflight.py must define {name}"
        assert f"    {name}()" in body, f"main() must call {name}"


# --- B304: both publishers refuse the same commits ----------------------------------------


def _walk_function() -> str:
    match = re.search(r"^function Get-UnpublishablePaths.*?^\}", _read(WATCHDOG), re.M | re.S)
    assert match, "local/watchdog-bb.ps1 must define Get-UnpublishablePaths (D67)"
    return match.group(0)


def test_audit13_both_publishers_refuse_the_same_paths_and_trust_the_same_authors():
    """B304: the watchdog pushes with the same PAT, which carries `workflow`, so its walk names
    the prefix, author emails and cap harness/clone.py does and runs the same `git log`.
    local/preflight.py checks the same."""
    from harness import clone

    body = _walk_function()
    emails = re.search(r"\$HarnessEmails\s*=\s*@\(([^)]*)\)", body)
    assert emails, "the watchdog's walk names no author emails"
    named = sorted(re.findall(r'"([^"]+)"', emails.group(1)))
    assert named == sorted(clone.HARNESS_AUTHOR_EMAILS)
    prefix = re.search(r'\$ProtectedPrefix\s*=\s*"([^"]*)"', body)
    assert prefix and (prefix.group(1),) == clone.PROTECTED_PUSH_PATHS
    scan = re.search(r"\$ScanCommits\s*=\s*(\d+)", body)
    assert scan and int(scan.group(1)) == clone.PROTECTED_SCAN_COMMITS
    for flag in clone.harness_walk_argv("HEAD"):
        if flag.startswith("-") or "=" in flag:
            assert flag in body, f"the watchdog's walk lacks {flag!r} (clone.harness_walk_argv)"


def test_audit13_the_watchdog_walks_before_it_pushes():
    code = "\n".join(ln for ln in _read(WATCHDOG).splitlines() if not ln.strip().startswith("#"))
    walk_at = code.find("Get-UnpublishablePaths $clone")
    assert 0 <= walk_at < code.index('push "--force-with-lease'), "walk first, then push"
    assert "$held.Count -gt 0" in code[walk_at:], "a refusal must stop the push"


def _powershell() -> str | None:
    import shutil

    return shutil.which("pwsh") or shutil.which("powershell")


@pytest.mark.skipif(_powershell() is None, reason="no PowerShell on this machine")
@pytest.mark.parametrize("touch, refused", [(".github/workflows/ci-cd.yml", True),
                                            ("src/app.ts", False)])
def test_audit13_the_powershell_walk_and_the_python_walk_agree_on_a_branch(
    tmp_path, touch, refused
):
    """B304: the watchdog's own function, lifted out of the script, runs over the same fixture
    branch as clone.walk_harness_commits. Upstream's workflow commit sits
    below the harness's in both cases; only a harness commit under .github/ is refused."""
    import os
    import subprocess

    from harness import clone

    repo = tmp_path / "clone"
    repo.mkdir()

    def git(*args: str) -> None:
        argv = ["git", "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args]
        done = subprocess.run(argv, cwd=repo, capture_output=True, text=True)
        assert done.returncode == 0, done.stderr

    def commit(email: str, rel: str, message: str) -> None:
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text("x\n", encoding="utf-8", newline="\n")
        git("add", "-A")
        git("-c", f"user.email={email}", "-c", "user.name=someone", "commit", "-q", "-m", message)

    git("init", "-q", "-b", "main")
    commit("dev@example.com", "README.md", "chore: seed")
    commit("dev@example.com", ".github/workflows/upstream.yml", "ci: upstream's own")
    commit("harness@localhost", touch, "wip: handoff")
    script = tmp_path / "walk.ps1"
    script.write_text("\n".join([
        '$ErrorActionPreference = "Continue"',
        _walk_function(),
        "$r = @(Get-UnpublishablePaths $args[0] $args[1])",
        "foreach ($x in $r) { Write-Output $x }",
        'Write-Output "count=$($r.Count)"',
    ]) + "\n", encoding="utf-8")
    argv = [_powershell(), "-NoProfile", "-NonInteractive"]
    if os.name == "nt":
        argv += ["-ExecutionPolicy", "Bypass"]

    done = subprocess.run(argv + ["-File", str(script), str(repo), "HEAD"],
                          capture_output=True, text=True, timeout=180)

    lines = [ln.strip() for ln in done.stdout.splitlines() if ln.strip()]
    assert lines and lines[-1].startswith("count="), (done.stdout, done.stderr)
    ps_refuses = lines[-1] != "count=0"
    py_refuses = bool(clone.walk_harness_commits(repo, "HEAD").protected())
    assert ps_refuses == py_refuses == refused, (lines, done.stderr)
    if refused:
        assert any(touch in line for line in lines[:-1]), lines


def test_b312_the_walk_compares_the_record_separator_ordinally():
    """PowerShell 7 compares with ICU, which treats U+001E as ignorable, so under it
    ``"".StartsWith($rs)`` is true and the blank line ``git log --name-only`` prints between a
    commit's header and its paths would enter the record branch. The comparison must be
    ordinal."""
    body = _walk_function()

    assert "StartsWith($rs, [System.StringComparison]::Ordinal)" in body, body
    assert "$line.Length -eq 0" in body, "an empty line must never reach the record branch"

