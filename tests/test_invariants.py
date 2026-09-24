"""Invariant tests: I-1 … I-17 (docs/SAFETY.md), the file map, and the structure of the
workflows and the governance files.

Everything here reads the source tree and the workflow text; nothing runs the harness.

Stack: Python 3.13 standard library + pytest==8.3.4 only.
"""

from __future__ import annotations

import ast
import builtins
import io
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS_DIR = REPO_ROOT / "harness"
TESTS_DIR = REPO_ROOT / "tests"

# Every Python file the package may contain.
SPEC_PACKAGE_FILES = [
    "harness/__init__.py",
    "harness/__main__.py",
    "harness/config.py",
    "harness/errors.py",
    "harness/store/__init__.py",
    "harness/store/sqlite.py",
    "harness/governor.py",
    "harness/collision.py",
    "harness/redact.py",
    "harness/gh.py",
    "harness/identity.py",
    "harness/clone.py",
    "harness/halt.py",
    "harness/context.py",
    "harness/clock.py",
    "harness/gates.py",
    "harness/commitmsg.py",
    "harness/prettier.py",
    "harness/packager.py",
    "harness/runner/__init__.py",
    "harness/runner/base.py",
    "harness/runner/cli.py",
    "harness/runner/fake.py",
    "harness/stages/__init__.py",
    "harness/stages/discover.py",
    "harness/stages/propose.py",
    "harness/stages/implement.py",
    "harness/stages/package.py",
    "harness/store/github.py",
    "harness/dispatcher.py",
    "harness/ledger.py",
    "harness/keywords.py",
    "harness/trust.py",
    "harness/verify_pin.py",
    "harness/stages/decompose.py",
    "harness/stages/revise.py",
    "harness/stages/deliver.py",
    # B227: the presentation layer for everything the harness opens on GitHub.
    "harness/links.py",
    "harness/priority.py",
    "harness/stages/ask.py",
    "harness/stages/audit.py",
]

# Test modules that must exist.
SPEC_TEST_FILES = [
    "tests/conftest.py",
    "tests/test_config.py",
    "tests/test_store.py",
    "tests/test_governor.py",
    "tests/test_runner_cli.py",
    "tests/test_runner_fake.py",
    "tests/test_gh.py",
    "tests/test_collision.py",
    "tests/test_clone.py",
    "tests/test_redact.py",
    "tests/test_identity.py",
    "tests/test_stages.py",
    "tests/test_cli.py",
    "tests/test_packager.py",
    "tests/test_invariants.py",
]

NON_GET_METHODS = ("POST", "PUT", "PATCH", "DELETE")
SQL_TOKENS = ("SELECT ", "INSERT ", "UPDATE ", "DELETE ", "CREATE TABLE")


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def _rel(path: Path) -> str:
    return path.resolve().relative_to(REPO_ROOT).as_posix()


def _harness_sources() -> list[Path]:
    """Every .py file under harness/, __pycache__ excluded. Never empty on a real tree."""
    assert HARNESS_DIR.is_dir(), f"{HARNESS_DIR} does not exist"
    files = sorted(
        p for p in HARNESS_DIR.rglob("*.py") if "__pycache__" not in p.parts
    )
    assert files, "no Python sources found under harness/; the invariant scan would be vacuous"
    return files


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _parse(path: Path) -> ast.Module:
    return ast.parse(_read(path), filename=str(path))


def _strip_comment_lines(text: str) -> str:
    """Drop whole-line `#` comments. Docstrings and inline code survive."""
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def _string_constants(tree: ast.AST) -> list[str]:
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def _find_function(tree: ast.AST, name: str) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


# --------------------------------------------------------------------------------------
# I-1 — no module issues a non-GET HTTP request
# --------------------------------------------------------------------------------------


def test_i1_no_module_issues_a_non_get_http_request():
    violations: list[str] = []
    token = re.compile(
        r"(?<![A-Za-z0-9_])(" + "|".join(NON_GET_METHODS) + r")(?![A-Za-z0-9_])"
    )

    for path in _harness_sources():
        # harness/gh.py is the one module that may issue a non-GET request (I-11).
        if _rel(path) == "harness/gh.py":
            continue
        tree = _parse(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg != "method":
                        continue
                    ok = isinstance(kw.value, ast.Constant) and kw.value.value == "GET"
                    if not ok:
                        violations.append(
                            f"{_rel(path)}:{getattr(node, 'lineno', '?')} method= is not \"GET\""
                        )
        for literal in _string_constants(tree):
            match = token.search(literal)
            if match:
                violations.append(
                    f"{_rel(path)} contains the literal {match.group(1)!r}"
                )

    assert violations == [], "I-1 violated: " + "; ".join(violations)


# --------------------------------------------------------------------------------------
# I-2 — the gh CLI is never invoked
# --------------------------------------------------------------------------------------


def test_i2_the_gh_cli_is_never_invoked():
    quoted = re.compile(r"""['"]gh['"]""")
    violations: list[str] = []

    for path in _harness_sources():
        source = _read(path)
        if quoted.search(source):
            violations.append(f"{_rel(path)} contains a quoted 'gh' program token")
        if '"gh ' in source or "'gh " in source:
            violations.append(f"{_rel(path)} contains a 'gh ' command string")

    assert violations == [], "I-2 violated: " + "; ".join(violations)


# --------------------------------------------------------------------------------------
# I-3 — permission-skipping flags never appear
# --------------------------------------------------------------------------------------


def test_i3_permission_skipping_flags_never_appear():
    violations = [
        _rel(path)
        for path in _harness_sources()
        if "dangerously-skip-permissions" in _read(path)
    ]
    assert violations == [], (
        "I-3 violated: dangerously-skip-permissions appears in " + ", ".join(violations)
    )


# --------------------------------------------------------------------------------------
# I-4 — os.environ is read only in config.py
# --------------------------------------------------------------------------------------


def test_i4_os_environ_is_read_only_in_config_py():
    violations: list[str] = []

    for path in _harness_sources():
        if _rel(path) == "harness/config.py":
            continue
        tree = _parse(path)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "environ"
                and isinstance(node.value, ast.Name)
                and node.value.id == "os"
            ):
                violations.append(f"{_rel(path)}:{node.lineno} os.environ")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "getenv"
            ):
                violations.append(f"{_rel(path)}:{node.lineno} getenv()")
            if isinstance(node, ast.ImportFrom) and (node.module or "") == "os":
                for alias in node.names:
                    if alias.name in ("environ", "getenv"):
                        violations.append(
                            f"{_rel(path)}:{node.lineno} from os import {alias.name}"
                        )

    assert violations == [], "I-4 violated: " + "; ".join(violations)


# --------------------------------------------------------------------------------------
# I-5 — SQL exists only in store/sqlite.py
# --------------------------------------------------------------------------------------


def test_i5_sql_exists_only_in_store_py():
    violations: list[str] = []

    for path in _harness_sources():
        if _rel(path) in ("harness/store.py", "harness/store/sqlite.py"):
            continue
        source = _strip_comment_lines(_read(path))
        for token in SQL_TOKENS:
            if token in source:
                violations.append(f"{_rel(path)} contains {token.strip()!r}")

    assert violations == [], "I-5 violated: " + "; ".join(violations)


# --------------------------------------------------------------------------------------
# I-6 — .gitignore covers .env, runs/, HALT
# --------------------------------------------------------------------------------------


def test_i6_gitignore_covers_env_runs_and_halt():
    gitignore = REPO_ROOT / ".gitignore"
    assert gitignore.is_file(), ".gitignore is required by §4.4"
    lines = {ln.strip() for ln in _read(gitignore).splitlines()}
    for required in (".env", "runs/", "HALT"):
        assert required in lines, f"I-6 violated: .gitignore is missing the line {required!r}"


# --------------------------------------------------------------------------------------
# I-7 — npm run format and prettier --check . are never invoked
# --------------------------------------------------------------------------------------


def test_i7_whole_tree_formatting_is_never_invoked():
    violations: list[str] = []

    for path in _harness_sources():
        source = _read(path)
        for forbidden in ("npm run format", "prettier --check ."):
            if forbidden in source:
                violations.append(f"{_rel(path)} contains {forbidden!r}")

    assert violations == [], "I-7 violated: " + "; ".join(violations)


# --------------------------------------------------------------------------------------
# I-8 — no file is written outside the configured roots
# --------------------------------------------------------------------------------------


def test_i8_writes_outside_the_allowed_roots_are_refused(tmp_path):
    from harness import redact
    from harness.errors import WriteOutsideAllowedRoots

    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside" / "leaked.txt"

    redact.set_write_roots([allowed])
    assert allowed.resolve() in tuple(Path(r).resolve() for r in redact.allowed_roots())

    opened: list[tuple[str, str]] = []
    real_builtin_open = builtins.open
    real_io_open = io.open

    def _spy(file, mode="r", *args, **kwargs):
        opened.append((str(file), str(mode)))
        return real_builtin_open(file, mode, *args, **kwargs)

    builtins.open = _spy
    io.open = _spy
    try:
        with pytest.raises(WriteOutsideAllowedRoots):
            redact.guarded_write(outside, "plain text outside the roots")
        with pytest.raises(WriteOutsideAllowedRoots):
            redact.write_redacted(outside, "redacted text outside the roots")
    finally:
        builtins.open = real_builtin_open
        io.open = real_io_open

    writes = [(f, m) for f, m in opened if any(c in m for c in ("w", "a", "x", "+"))]
    assert writes == [], f"I-8 violated: a write was opened despite the refusal: {writes}"
    assert not outside.exists()
    assert not outside.parent.exists()

    # the guard permits what it is configured to permit
    inside = allowed / "nested" / "ok.txt"
    redact.guarded_write(inside, "inside the roots")
    assert inside.read_text(encoding="utf-8") == "inside the roots"


# --------------------------------------------------------------------------------------
# I-9 — the bot token is never transmitted
# --------------------------------------------------------------------------------------


def test_i9_the_bot_token_is_confined_to_config_and_identity():
    allowed_files = {"harness/config.py", "harness/identity.py"}
    violations: list[str] = []

    for path in _harness_sources():
        if _rel(path) in allowed_files:
            continue
        if "HARNESS_GITHUB_TOKEN" in _read(path):
            violations.append(_rel(path))

    assert violations == [], (
        "I-9 violated: HARNESS_GITHUB_TOKEN is referenced in " + ", ".join(violations)
    )

    gh_path = HARNESS_DIR / "gh.py"
    assert gh_path.is_file(), "harness/gh.py is required by §4.1"
    imports: list[str] = []
    for node in ast.walk(_parse(gh_path)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[-1] == "identity":
                    imports.append(f"import {alias.name}")
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.split(".")[-1] == "identity":
                imports.append(f"from {module} import ...")
            for alias in node.names:
                if alias.name == "identity":
                    imports.append(f"from {module or '.'} import identity")

    assert imports == [], "I-9 violated: harness/gh.py imports identity: " + "; ".join(imports)


# --------------------------------------------------------------------------------------
# I-10 — HUMAN.md generation never interpolates an environment value
# --------------------------------------------------------------------------------------


def test_i10_render_human_doc_interpolates_no_environment_or_secret_value():
    identity_path = HARNESS_DIR / "identity.py"
    assert identity_path.is_file(), "harness/identity.py is required by §4.1"

    fn = _find_function(_parse(identity_path), "render_human_doc")
    assert fn is not None, "identity.py must define render_human_doc (§5.12)"

    violations: list[str] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Attribute):
            if node.attr == "environ":
                violations.append(f"line {node.lineno}: .environ")
            if node.attr.endswith("_token") or node.attr.endswith("_key"):
                violations.append(f"line {node.lineno}: .{node.attr}")
        if isinstance(node, ast.Name) and node.id == "environ":
            violations.append(f"line {node.lineno}: environ")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("getenv", "read_secret", "secret_values")
        ):
            violations.append(f"line {node.lineno}: {node.func.attr}()")

    assert violations == [], "I-10 violated: " + "; ".join(violations)


# --------------------------------------------------------------------------------------
# A1 — the file manifest is exact
# --------------------------------------------------------------------------------------


def test_a1_every_package_file_in_section_4_1_exists():
    missing = [rel for rel in SPEC_PACKAGE_FILES if not (REPO_ROOT / rel).is_file()]
    assert missing == [], "§4.1 files missing: " + ", ".join(missing)


def test_a1_no_unexpected_python_file_exists_under_harness():
    found = {_rel(path) for path in _harness_sources()}
    unexpected = sorted(found - set(SPEC_PACKAGE_FILES))
    assert unexpected == [], (
        "a file not listed in §4.1 MUST NOT be created: " + ", ".join(unexpected)
    )


def test_a1_every_test_file_in_section_4_3_exists():
    assert TESTS_DIR.is_dir(), "tests/ does not exist"
    missing = [rel for rel in SPEC_TEST_FILES if not (REPO_ROOT / rel).is_file()]
    assert missing == [], "§4.3 test modules missing: " + ", ".join(missing)


# ======================================================================================
# I-2′ and I-11 … I-17, the pin (B142, B143), the file map, workflow hygiene and governance.
# ======================================================================================

import hashlib
import json
import sys
import tomllib

from harness import verify_pin as verify_pin_mod

WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
SPENDING_WORKFLOWS = ("discover.yml", "implement.yml", "feedback.yml")
#: The original six: every workflow that can spend, hold the ledger lock, or run on a schedule
#: that matters.
HANDOFF_WORKFLOWS = (
    "discover.yml",
    "implement.yml",
    "feedback.yml",
    "ops.yml",
    "heartbeat.yml",
    "selftest.yml",
)
#: Workflows added since, each with the decision that added it. One with no entry fails.
ADDED_WORKFLOWS = {
    "ack.yml": "D65/B293 — say 'working on it' within seconds; spends nothing, takes no lock",
    "watchdog.yml": "D65/B294 — dispatch feedback when a scheduled run never started",
}
ALL_WORKFLOWS = HANDOFF_WORKFLOWS + tuple(ADDED_WORKFLOWS)
# The full expected cron list per scheduled workflow; the others carry none. discover and
# implement fire inside the daily 11:00 -> 19:00 UTC run window (D72, widened by D80).
FROZEN_CRONS = {
    "discover.yml": ["7 11,13 * * *"],
    "implement.yml": ["23 11-18 * * *"],
    "feedback.yml": ["41 */3 * * 1-5"],
    "heartbeat.yml": ["5 9 * * 1"],
    # Offset from feedback's in both fields, so the watchdog never fires with what it watches
    # (B294). `ack.yml` is event-driven and carries no cron.
    "watchdog.yml": ["17 */4 * * *"],
}
ROUND_MINUTES = (0, 15, 30, 45)

# Every gh.py write method (I-13's write set).
GH_WRITE_METHODS = (
    "comment",
    # D76: the one write that removes something. It may only ever reach a comment carrying
    # MACHINE_MARKER, which is applied at the transport to everything the harness writes.
    "delete_issue_comment",
    "set_labels",
    "create_issue",
    # D83: the one issue a delivery files on the product repository, and its later edit.
    "create_product_issue",
    "edit_product_issue",
    # D88: that issue's label.
    "label_product_issue",
    "update_issue_body",
    "create_pull",
    "request_reviewers",
    "close_pull",
    # D86: a done item's harness issue, in this repository only.
    "close_issue",
    "create_branch_file",
)

# Modules the I-17 import scan requires to exist.
D2_NEW_PACKAGE_FILES = [
    "harness/store/github.py",
    "harness/dispatcher.py",
    "harness/ledger.py",
    "harness/keywords.py",
    "harness/trust.py",
    "harness/verify_pin.py",
    "harness/stages/decompose.py",
    "harness/stages/revise.py",
    "harness/stages/deliver.py",
]
D2_PACKAGE_FILES = [
    "harness/__init__.py",
    "harness/__main__.py",
    "harness/config.py",
    "harness/errors.py",
    "harness/store/__init__.py",
    "harness/store/sqlite.py",
    "harness/store/github.py",
    "harness/governor.py",
    "harness/collision.py",
    "harness/redact.py",
    "harness/gh.py",
    "harness/identity.py",
    "harness/clone.py",
    "harness/halt.py",
    "harness/context.py",
    "harness/clock.py",
    "harness/gates.py",
    "harness/commitmsg.py",
    "harness/prettier.py",
    "harness/packager.py",
    "harness/dispatcher.py",
    "harness/ledger.py",
    "harness/keywords.py",
    "harness/trust.py",
    "harness/verify_pin.py",
    "harness/runner/__init__.py",
    "harness/runner/base.py",
    "harness/runner/cli.py",
    "harness/runner/fake.py",
    "harness/stages/__init__.py",
    "harness/stages/discover.py",
    "harness/stages/propose.py",
    "harness/stages/implement.py",
    "harness/stages/package.py",
    "harness/stages/decompose.py",
    "harness/stages/revise.py",
    "harness/stages/deliver.py",
    # B227: the presentation layer for everything the harness opens on GitHub.
    "harness/links.py",
    "harness/priority.py",
    "harness/stages/ask.py",
    "harness/stages/audit.py",
]

# Test modules and fixtures that must exist.
D2_REQUIRED_TEST_FILES = [
    "tests/test_store_github.py",
    "tests/test_ledger.py",
    "tests/test_dispatcher.py",
    "tests/test_keywords.py",
    "tests/test_trust.py",
    "tests/test_stages_revise.py",
    "tests/test_stages_deliver.py",
    "tests/test_stages_decompose.py",
    "tests/fixtures/runner/revise.json",
    "tests/fixtures/runner/rate_limited.json",
    # `BACKEND=fake` needs a fixture for every stage that calls a model.
    "tests/fixtures/runner/ask.json",
    "tests/fixtures/runner/audit.json",
]

# Non-Python files that must exist.
D2_REQUIRED_FILES = [
    ".github/workflows/discover.yml",
    ".github/workflows/implement.yml",
    ".github/workflows/feedback.yml",
    ".github/workflows/ops.yml",
    ".github/workflows/heartbeat.yml",
    ".github/workflows/selftest.yml",
    ".github/CODEOWNERS",
    ".github/pull_request_template.md",
    ".harness/trust.txt",
    ".harness/config.json",
    ".harness/README.md",
    "prompts/decompose.md",
    "prompts/revise.md",
    "proposals/.gitkeep",
    "state/ledger.json",
    "local/Dockerfile",
    "local/entrypoint.sh",
    "local/run.ps1",
    "local/watchdog-bb.ps1",
    "local/container_env.ps1",
    "local/preflight.py",
    "local/README.md",
    "docs/OPERATIONS.md",
    "docs/LOCAL-MODE.md",
    "bb-start.ps1",
    "bb-stop.ps1",
    "bb-watcher.ps1",
    "bb-configure.py",
    "bb-config.json",
]

# The first seven .harness/config.json knob keys (B112).
D2_CONFIG_JSON_KEYS = (
    "MAX_CONCURRENT_ITEMS",
    "MAX_REVISE_CYCLES",
    "MAX_SUBISSUES",
    "TRACKING_ISSUE",
    "FORK_REPO",
    "UPSTREAM_REPO",
    "TRUST_FILE",
)
# The five usage-governance knobs, kept apart so `D3_CONFIG_JSON_KEYS` below is a real union.
D3_NEW_CONFIG_JSON_KEYS = (
    "WEEKLY_USAGE_STOP_PCT",
    "SESSION_USAGE_STOP_PCT",
    "OVERRUN_PCT",
    "RUN_WINDOW_START",
    "RUN_WINDOW_END",
)
# `INBOX_ISSUE` is repository state every runner must agree on, so it is committed; the caps
# added beside it are per-environment and stay in `.env`.
D4_NEW_CONFIG_JSON_KEYS = ("INBOX_ISSUE",)
# Who the harness credits on its commits is repository state, like the inbox (D82).
D82_NEW_CONFIG_JSON_KEYS = ("CO_AUTHOR",)
# D88: the cap on open delivery pull requests, set to the product maintainer's two.
D88_NEW_CONFIG_JSON_KEYS = ("MAX_OPEN_DELIVERIES",)
# The knob keys the shipped .harness/config.json carries.
CONFIG_JSON_KEYS = (
    D2_CONFIG_JSON_KEYS
    + D3_NEW_CONFIG_JSON_KEYS
    + D4_NEW_CONFIG_JSON_KEYS
    + D82_NEW_CONFIG_JSON_KEYS
    + D88_NEW_CONFIG_JSON_KEYS
)

# The .env keys and values the build_context test writes, kept inline.
D2_ENV_KEYS: dict[str, str] = {
    "MAX_CONCURRENT_ITEMS": "1",
    "MAX_REVISE_CYCLES": "3",
    "FORK_REPO": "",
    "UPSTREAM_REPO": "Bright-Bots-Initiative/brightboost",
    "TRUST_FILE": ".harness/trust.txt",
    "MAX_SUBISSUES": "8",
    "SELF_REPO": "jgoetzmann/bright-bots-harness",
    "TRACKING_ISSUE": "",
    "STORE_BACKEND": "sqlite",
    "WEEKLY_USAGE_STOP_PCT": "90",
    "SESSION_USAGE_STOP_PCT": "70",
    "OVERRUN_PCT": "10",
    "RUN_WINDOW_START": "",
    "RUN_WINDOW_END": "",
}

# A minimal tree carrying every pinned path.
PIN_TREE: dict[str, str] = {
    "harness/gates.py": "SEQUENCE = ('npm run lint', 'npm run build')\n",
    "harness/packager.py": "def build():\n    return 'package'\n",
    "harness/redact.py": "REDACTION = '[REDACTED]'\n",
    "prompts/system.md": "# system\nYou are the harness.\n",
    "prompts/decompose.md": "# decompose\n$issue_title\n$max\n",
}


# --------------------------------------------------------------------------------------
# Workflow and pin helpers
# --------------------------------------------------------------------------------------


def _d2_workflow(name: str) -> str:
    path = WORKFLOWS_DIR / name
    assert path.is_file(), f".github/workflows/{name} is required by handoff §3 / §7"
    return path.read_text(encoding="utf-8")


def _workflow_jobs(text: str) -> dict[str, str]:
    """Map every `jobs.<id>` to its block text (two-space indentation, as GitHub's own docs)."""
    jobs: dict[str, list[str]] = {}
    in_jobs = False
    current: str | None = None
    for line in text.splitlines():
        if re.match(r"^jobs:\s*(#.*)?$", line):
            in_jobs = True
            current = None
            continue
        if in_jobs and line.strip() and not line[0].isspace() and not line.startswith("#"):
            in_jobs = False
            current = None
        if not in_jobs:
            continue
        match = re.match(r"^  ([A-Za-z_][\w-]*):\s*(#.*)?$", line)
        if match:
            current = match.group(1)
            jobs[current] = [line]
            continue
        if current is not None:
            jobs[current].append(line)
    return {job: "\n".join(lines) for job, lines in jobs.items()}


def _step_blocks(text: str) -> list[str]:
    """Split every `steps:` list into its `- ` items, each item as one text block."""
    blocks: list[list[str]] = []
    steps_indent: int | None = None
    dash_indent: int | None = None
    current: list[str] | None = None
    for line in text.splitlines():
        if not line.strip():
            if current is not None:
                current.append(line)
            continue
        indent = len(line) - len(line.lstrip(" "))
        steps_key = re.match(r"^(\s*)steps:\s*(#.*)?$", line)
        if steps_key:
            steps_indent = len(steps_key.group(1))
            dash_indent = None
            current = None
            continue
        if steps_indent is None:
            continue
        is_dash = re.match(r"^\s*-\s", line) is not None
        if dash_indent is None:
            if is_dash and indent in (steps_indent, steps_indent + 2):
                dash_indent = indent
                current = [line]
                blocks.append(current)
            elif indent <= steps_indent:
                steps_indent = None
            continue
        if is_dash and indent == dash_indent:
            current = [line]
            blocks.append(current)
            continue
        if indent < dash_indent or (indent == dash_indent and not is_dash):
            steps_indent = None
            dash_indent = None
            current = None
            continue
        if current is not None:
            current.append(line)
    return ["\n".join(block) for block in blocks]


def _cron_values(text: str) -> list[str]:
    values = []
    for line in text.splitlines():
        if "cron:" not in line or line.lstrip().startswith("#"):
            continue
        value = line.split("cron:", 1)[1]
        value = re.sub(r"\s+#.*$", "", value).strip().strip("'\"").strip()
        values.append(value)
    return values


def _has_pull_request_trigger(text: str) -> bool:
    """True when the workflow is triggered by `pull_request` (not `_target`, not `_review_comment`)."""
    if re.search(r"^\s{0,4}pull_request:", text, re.M):
        return True
    if re.search(r"^\s*on:\s*\[[^\]]*\bpull_request\b[^\]]*\]", text, re.M):
        return True
    if re.search(r"^\s*on:\s*pull_request\s*(#.*)?$", text, re.M):
        return True
    if re.search(r"^\s*-\s*pull_request\s*(#.*)?$", text, re.M):
        return True
    return False


def _first_line_index(text: str, pattern: str) -> int | None:
    for index, line in enumerate(text.splitlines()):
        if re.search(pattern, line):
            return index
    return None


def _class_def(tree: ast.AST, name: str) -> ast.ClassDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    return None


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _pin_repo(root: Path) -> Path:
    for rel, body in PIN_TREE.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8", newline="\n")
    (root / ".harness").mkdir(exist_ok=True)
    return root


def _expected_pin(root: Path) -> str:
    """sha256 over (posix path + NUL + normalised bytes), sorted (B217)."""
    pinned = ["harness/gates.py", "harness/packager.py", "harness/redact.py"]
    pinned += sorted(
        f"prompts/{p.name}" for p in (root / "prompts").iterdir() if p.is_file()
    )
    digest = hashlib.sha256()
    for rel in sorted(pinned):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(verify_pin_mod.normalise((root / rel).read_bytes()))
    return digest.hexdigest()


# --------------------------------------------------------------------------------------
# I-2′ — the gh CLI is never invoked; harness/gh.py is the only GitHub access path
# --------------------------------------------------------------------------------------


def test_i2_prime_the_gh_cli_is_never_invoked_and_gh_py_is_the_only_transport():
    """I-2′: no `gh` program token anywhere, and gh.py is the only HTTP transport."""
    quoted = re.compile(r"""['"]gh['"]""")
    violations: list[str] = []

    for path in _harness_sources():
        source = _read(path)
        if quoted.search(source):
            violations.append(f"{_rel(path)} contains a quoted 'gh' program token")
        if '"gh ' in source or "'gh " in source:
            violations.append(f"{_rel(path)} contains a 'gh ' command string")
        for node in ast.walk(_parse(path)):
            if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
                first = node.elts[0]
                if isinstance(first, ast.Constant) and first.value == "gh":
                    violations.append(f"{_rel(path)}:{node.lineno} argv starts with 'gh'")

    assert violations == [], "I-2′ violated: " + "; ".join(violations)

    # GitHub access is harness/gh.py only: no other module opens an HTTP transport.
    transports: list[str] = []
    for path in _harness_sources():
        if _rel(path) == "harness/gh.py":
            continue
        for node in ast.walk(_parse(path)):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                if name.startswith(("urllib.request", "http.client")):
                    transports.append(f"{_rel(path)}:{node.lineno} imports {name}")
    assert transports == [], "I-2′ violated: " + "; ".join(transports)


# --------------------------------------------------------------------------------------
# I-11 — exactly one authenticated client, exactly one token door
# --------------------------------------------------------------------------------------


def test_i11_authorization_header_is_built_in_gh_py_only():
    """I-11: the header string an authenticated request carries is built in harness/gh.py and
    nowhere else.

    The scan reads string constants and f-string parts, not identifiers, so the
    `governor.Authorization` dataclass name does not count, and it skips docstrings.
    """
    scopes = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    with_header: set[str] = set()
    for path in _harness_sources():
        tree = _parse(path)
        docstrings: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, scopes) and node.body and isinstance(node.body[0], ast.Expr):
                first = node.body[0].value
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    docstrings.add(id(first))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if "Authorization" in node.value and id(node) not in docstrings:
                with_header.add(_rel(path))

    assert sorted(with_header) == ["harness/gh.py"], (
        "I-11 violated: the Authorization header string must be built in harness/gh.py only; "
        "found in " + ", ".join(sorted(with_header))
    )


def test_i11_the_token_door_is_defined_in_config_and_imported_by_gh_py_only():
    """I-11: github_token() lives in config.py; only gh.py imports it."""
    config_tree = _parse(HARNESS_DIR / "config.py")
    assert _find_function(config_tree, "github_token") is not None, (
        "config.py must define github_token() (RUN-DECISIONS-D2 §2)"
    )

    importers: set[str] = set()
    users: list[str] = []
    for path in _harness_sources():
        rel = _rel(path)
        if rel == "harness/config.py":
            continue
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.ImportFrom) and any(
                alias.name == "github_token" for alias in node.names
            ):
                importers.add(rel)
            if isinstance(node, ast.Attribute) and node.attr == "github_token":
                users.append(f"{rel}:{node.lineno}")
            if isinstance(node, ast.Name) and node.id == "github_token":
                users.append(f"{rel}:{node.lineno}")

    assert importers == {"harness/gh.py"}, (
        "I-11 violated: github_token must be imported by harness/gh.py and nothing else; "
        f"importers={sorted(importers)}"
    )
    outside = [use for use in users if not use.startswith("harness/gh.py:")]
    assert outside == [], "I-11 violated: github_token referenced in " + ", ".join(outside)


# --------------------------------------------------------------------------------------
# I-12 — no code path merges, approves or dismisses
# --------------------------------------------------------------------------------------


def test_i12_merge_approve_and_dismiss_endpoints_do_not_exist():
    """I-12 / B109: no endpoint string, payload or method exists for any of the three."""
    review_grep = re.compile(r"/merge\b|event.*APPROVE|dismiss")
    violations: list[str] = []

    for path in _harness_sources():
        rel = _rel(path)
        for lineno, line in enumerate(_read(path).splitlines(), start=1):
            if review_grep.search(line):
                violations.append(f"{rel}:{lineno} {line.strip()!r}")
        tree = _parse(path)
        for literal in _string_constants(tree):
            if "/merge" in literal:
                violations.append(f"{rel} string {literal!r} names a merge endpoint")
            if re.search(r"\bAPPROVE\b", literal):
                violations.append(f"{rel} string {literal!r} names the APPROVE review event")
            if "dismiss" in literal.lower():
                violations.append(f"{rel} string {literal!r} names a dismissal")
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if (
                        isinstance(key, ast.Constant)
                        and key.value == "event"
                        and isinstance(value, ast.Constant)
                        and value.value in ("APPROVE", "REQUEST_CHANGES", "COMMENT", "DISMISS")
                    ):
                        violations.append(f"{rel}:{node.lineno} review-event payload")

    gh_path = HARNESS_DIR / "gh.py"
    gh_tree = _parse(gh_path)
    client = _class_def(gh_tree, "GitHubClient")
    assert client is not None, "harness/gh.py must define GitHubClient (RUN-DECISIONS-D2 §7)"
    defined = {
        node.name
        for node in ast.walk(client)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    missing = [name for name in GH_WRITE_METHODS if name not in defined]
    assert missing == [], "GitHubClient is missing write methods: " + ", ".join(missing)

    for node in ast.walk(gh_tree):
        name = None
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = node.name
        elif isinstance(node, ast.Attribute):
            name = node.attr
        if name and any(word in name.lower() for word in ("merge", "approve", "dismiss")):
            violations.append(f"harness/gh.py:{node.lineno} name {name!r}")

    assert violations == [], "I-12 violated: " + "; ".join(violations)


# --------------------------------------------------------------------------------------
# I-13 — every gh.py write routes its payload through redact
# --------------------------------------------------------------------------------------


def test_i13_every_gh_write_method_routes_its_payload_through_redact():
    """I-13 / B108: each GitHubClient write method's body reaches a
    `redact_json`/`redact` call, directly or through a helper defined in gh.py."""
    gh_tree = _parse(HARNESS_DIR / "gh.py")
    client = _class_def(gh_tree, "GitHubClient")
    assert client is not None, "harness/gh.py must define GitHubClient (RUN-DECISIONS-D2 §7)"

    functions: dict[str, list[ast.AST]] = {}
    for node in ast.walk(gh_tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.setdefault(node.name, []).append(node)
    write_methods = {
        node.name: node
        for node in client.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in GH_WRITE_METHODS
    }
    missing = [name for name in GH_WRITE_METHODS if name not in write_methods]
    assert missing == [], "GitHubClient is missing write methods: " + ", ".join(missing)

    def reaches_redact(fn: ast.AST, seen: set[str]) -> bool:
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            if name in ("redact_json", "redact"):
                return True
            func = node.func
            is_local_helper = isinstance(func, ast.Name) or (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "self"
            )
            if name and is_local_helper and name in functions and name not in seen:
                seen.add(name)
                if any(reaches_redact(helper, seen) for helper in functions[name]):
                    return True
        return False

    unredacted = [
        name for name, fn in write_methods.items() if not reaches_redact(fn, {name})
    ]
    assert unredacted == [], (
        "I-13 violated: these write methods never route their payload through redact: "
        + ", ".join(unredacted)
    )


# --------------------------------------------------------------------------------------
# I-14 — no issue is created outside this repository
# --------------------------------------------------------------------------------------


def test_i14_create_issue_repo_is_always_self_repo():
    """I-14 / B110: create_issue has no repo parameter and targets self_repo; no call site
    passes one."""
    gh_tree = _parse(HARNESS_DIR / "gh.py")
    client = _class_def(gh_tree, "GitHubClient")
    assert client is not None, "harness/gh.py must define GitHubClient (RUN-DECISIONS-D2 §7)"
    create_issue = next(
        (
            node
            for node in client.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "create_issue"
        ),
        None,
    )
    assert create_issue is not None, "GitHubClient.create_issue is required (I-14)"

    args = create_issue.args
    params = [a.arg for a in args.posonlyargs + args.args + args.kwonlyargs]
    assert "repo" not in params, f"create_issue must not take a repo parameter: {params}"
    assert args.vararg is None and args.kwarg is None, (
        "create_issue must not accept *args/**kwargs that could smuggle a repo"
    )
    references_self_repo = any(
        (isinstance(node, ast.Attribute) and node.attr == "self_repo")
        or (isinstance(node, ast.Name) and node.id == "self_repo")
        for node in ast.walk(create_issue)
    )
    assert references_self_repo, "create_issue must target self_repo (I-14)"

    call_site_violations: list[str] = []
    for path in _harness_sources():
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.Call) and _call_name(node) == "create_issue":
                if any(kw.arg == "repo" for kw in node.keywords):
                    call_site_violations.append(f"{_rel(path)}:{node.lineno} repo=")
                if len(node.args) > 3:
                    call_site_violations.append(f"{_rel(path)}:{node.lineno} extra positional")
    assert call_site_violations == [], "I-14 violated: " + "; ".join(call_site_violations)


def test_i14_the_one_product_issue_targets_the_client_repo_and_only_deliver_files_it():
    """I-14 (D83): the single exception, `create_product_issue`, takes no repo parameter and
    writes to `/repos/{self.repo}/issues` alone. No module but `deliver.py` names it, by call,
    attribute or string, and no other gh.py write reaches an `/issues` collection."""
    client = _class_def(_parse(HARNESS_DIR / "gh.py"), "GitHubClient")
    methods = {n.name: n for n in client.body if isinstance(n, ast.FunctionDef)}
    method = methods["create_product_issue"]
    params = [a.arg for a in method.args.posonlyargs + method.args.args + method.args.kwonlyargs]
    assert params == ["self", "title", "body", "labels"], params
    assert method.args.vararg is None and method.args.kwarg is None

    def write_paths(node: ast.FunctionDef) -> list[str]:
        """The path argument of every `self._write(...)` call, as source text."""
        return [
            ast.unparse(call.args[1])
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "_write"
            and len(call.args) > 1
        ]

    assert write_paths(method) == ["f'/repos/{self.repo}/issues'"], write_paths(method)
    issue_writers = sorted(
        name
        for name, node in methods.items()
        if any(path.rstrip("'\"").endswith("/issues") for path in write_paths(node))
    )
    assert issue_writers == ["create_issue", "create_product_issue"], issue_writers

    edit = methods["edit_product_issue"]
    assert write_paths(edit) == ["f'/repos/{self.repo}/issues/{n}'"], write_paths(edit)
    label = methods["label_product_issue"]
    assert write_paths(label) == ["f'/repos/{self.repo}/issues/{n}/labels'"], write_paths(label)

    for name in ("create_product_issue", "edit_product_issue", "label_product_issue"):
        naming = sorted(
            {
                _rel(path)
                for path in _harness_sources()
                if path.name != "gh.py"
                for node in ast.walk(_parse(path))
                if (isinstance(node, ast.Attribute) and node.attr == name)
                or (isinstance(node, ast.Name) and node.id == name)
                or (isinstance(node, ast.Constant) and node.value == name)
            }
        )
        assert naming == ["harness/stages/deliver.py"], (name, naming)


#: The gh.py writes that can change a product-repository thread's state (D88).
OWN_THREAD_WRITES = (
    "close_pull",
    "request_reviewers",
    "edit_product_issue",
    "label_product_issue",
)

#: The queue's own writes, which may only ever name SELF_REPO (D88).
SELF_REPO_WRITES = ("set_labels", "update_issue_body", "create_label")


def test_B518_a_thread_somebody_else_opened_is_never_changed():
    """B518 (D88): every gh.py write that can close, label, edit or request review on a product
    thread checks first that this account opened it, and the queue's label and body writes
    name SELF_REPO at every call site, so none reaches another person's issue upstream."""
    client = _class_def(_parse(HARNESS_DIR / "gh.py"), "GitHubClient")
    methods = {n.name: n for n in client.body if isinstance(n, ast.FunctionDef)}
    for name in OWN_THREAD_WRITES:
        calls = [
            call
            for call in ast.walk(methods[name])
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
        ]
        guard = [c.lineno for c in calls if c.func.attr == "_require_own_thread"]
        writes = [c.lineno for c in calls if c.func.attr == "_write"]
        assert guard, f"{name} writes without `_require_own_thread`"
        assert writes and min(guard) < min(writes), f"{name} writes before its check"

    offenders: list[str] = []
    for path in _harness_sources():
        if path.name == "gh.py":
            continue
        for node in ast.walk(_parse(path)):
            if not (isinstance(node, ast.Call) and _call_name(node) in SELF_REPO_WRITES):
                continue
            first = ast.unparse(node.args[0]) if node.args else ""
            if "self_repo" not in first:
                offenders.append(f"{_rel(path)}:{node.lineno} {_call_name(node)}({first})")
    assert offenders == [], "writes that could reach another repository: " + "; ".join(offenders)


# --------------------------------------------------------------------------------------
# I-15 — B64's forbidden-diff check stays single-sourced in implement.py
# --------------------------------------------------------------------------------------


def test_i15_reject_forbidden_diff_lives_in_implement_py_and_is_not_copied_into_deliver_py():
    """I-15: `_reject_forbidden_diff` exists in implement.py and
    deliver.py carries no second copy of the check."""
    implement_tree = _parse(HARNESS_DIR / "stages" / "implement.py")
    assert _find_function(implement_tree, "_reject_forbidden_diff") is not None, (
        "implement.py must still define _reject_forbidden_diff (B64)"
    )

    deliver_path = HARNESS_DIR / "stages" / "deliver.py"
    assert deliver_path.is_file(), "harness/stages/deliver.py is required by handoff §3"
    deliver_tree = _parse(deliver_path)
    copies = [
        node.name
        for node in ast.walk(deliver_tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and "forbidden" in node.name.lower()
    ]
    assert copies == [], "I-15: deliver.py must not duplicate B64's check: " + ", ".join(copies)
    constants = [
        target.id
        for node in ast.walk(deliver_tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and "FORBIDDEN" in target.id
    ]
    assert constants == [], "I-15: deliver.py must not define its own forbidden path set"


def test_i15_one_protected_path_set_defined_in_clone_py_and_read_by_every_publisher():
    """B308: the protected path set lives once, as `clone.PROTECTED_PUSH_PATHS` and its two
    readers, `protected_paths_in` and `walk_harness_commits`. B64 (implement), the push guard
    (gh), the handoff (deliver) and revise all read it, and tests/test_local_mode.py and
    local/preflight.py hold the PowerShell publisher's copy to it."""
    from harness import clone
    from harness.stages import implement

    clone_tree = _parse(HARNESS_DIR / "clone.py")
    assigned = {
        target.id
        for node in ast.walk(clone_tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(target, ast.Name)
    }
    assert "PROTECTED_PUSH_PATHS" in assigned, "clone.py must define the protected path set"
    assert implement.FORBIDDEN_DIFF_PATHS is clone.PROTECTED_PUSH_PATHS

    shared = {"PROTECTED_PUSH_PATHS", "protected_paths_in", "walk_harness_commits"}
    for rel in ("stages/implement.py", "gh.py", "stages/deliver.py", "stages/revise.py"):
        tree = _parse(HARNESS_DIR / rel)
        used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        used |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        assert used & shared, f"harness/{rel} does not read the shared protected path set"

    # A second copy is a module-level collection holding the prefix. identity.py's inline
    # CODEOWNERS check also names "/.github/", but nothing publishes by it.
    def named_sets(tree: ast.Module) -> list[str]:
        found = []
        for node in tree.body:
            value = getattr(node, "value", None)
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(
                value, (ast.Tuple, ast.List, ast.Set)
            ):
                if any(isinstance(e, ast.Constant) and e.value == "/.github/" for e in value.elts):
                    found.append(ast.unparse(node)[:80])
        return found

    copies = [
        f"{_rel(path)}: {hit}"
        for path in _harness_sources()
        if path.name != "clone.py"
        for hit in named_sets(_parse(path))
    ]
    assert copies == [], "a second copy of the protected path set: " + "; ".join(copies)
    assert named_sets(clone_tree), "the check must see clone.py's own set, or it proves nothing"


# --------------------------------------------------------------------------------------
# I-16 — no module above the store branches on execution mode
# --------------------------------------------------------------------------------------


def test_i16_no_module_above_the_store_branches_on_execution_mode():
    """I-16: the execution-mode grep finds nothing under stages/, gates.py, packager.py or
    governor.py."""
    pattern = re.compile(r"GITHUB_ACTIONS|RUNNER_OS|ACTIONS_MODE|is_actions|execution_mode")
    scanned: list[Path] = sorted(
        p for p in (HARNESS_DIR / "stages").rglob("*.py") if "__pycache__" not in p.parts
    )
    for name in ("gates.py", "packager.py", "governor.py"):
        scanned.append(HARNESS_DIR / name)
    for required in ("deliver.py", "revise.py", "decompose.py"):
        assert (HARNESS_DIR / "stages" / required).is_file(), (
            f"harness/stages/{required} is required by handoff §3; the scan would be vacuous"
        )

    violations = [
        f"{_rel(path)}:{lineno} {line.strip()!r}"
        for path in scanned
        if path.is_file()
        for lineno, line in enumerate(_read(path).splitlines(), start=1)
        if pattern.search(line)
    ]
    assert violations == [], "I-16 violated: " + "; ".join(violations)


# --------------------------------------------------------------------------------------
# I-17 — stdlib only
# --------------------------------------------------------------------------------------


def test_i17_stdlib_only_no_runtime_dependency_and_no_third_party_import():
    """I-17: pyproject has no runtime dependency and every
    top-level import under harness/ is the standard library or the package itself."""
    pyproject = REPO_ROOT / "pyproject.toml"
    assert pyproject.is_file(), "pyproject.toml is required"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    assert data["project"].get("dependencies", []) == [], (
        "I-17 violated: runtime dependencies declared: " + str(data["project"]["dependencies"])
    )
    assert "dependencies" not in data["project"].get("dynamic", []), (
        "I-17 violated: dependencies must not be dynamic"
    )

    missing = [rel for rel in D2_NEW_PACKAGE_FILES if not (REPO_ROOT / rel).is_file()]
    assert missing == [], "D2 modules missing from the import scan: " + ", ".join(missing)

    violations: list[str] = []
    for path in _harness_sources():
        for node in ast.walk(_parse(path)):
            roots: list[str] = []
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    continue
                roots = [(node.module or "").split(".")[0]]
            for root in roots:
                if root and root != "harness" and root not in sys.stdlib_module_names:
                    violations.append(f"{_rel(path)}:{node.lineno} imports {root}")
    assert violations == [], "I-17 violated: " + "; ".join(violations)


def test_d2_style_no_threading_or_asyncio_under_harness():
    """No `threading`, `asyncio` or `concurrent` import under harness/; the local-loop heartbeat
    is a plain sleep loop."""
    violations: list[str] = []
    for path in _harness_sources():
        for node in ast.walk(_parse(path)):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [(node.module or "").split(".")[0]]
            for name in names:
                if name in ("threading", "asyncio", "concurrent"):
                    violations.append(f"{_rel(path)}:{node.lineno} imports {name}")
    assert violations == [], "RUN-DECISIONS-D2 §17 violated: " + "; ".join(violations)


# --------------------------------------------------------------------------------------
# B142 — verify_pin recomputes the pinned SHA-256 and refuses a mismatch
# --------------------------------------------------------------------------------------


def test_b142_compute_is_a_sha256_hex_stable_across_two_calls(tmp_path):
    """B142: compute() over the pinned set is deterministic."""
    from harness import verify_pin

    root = _pin_repo(tmp_path / "repo")

    first = verify_pin.compute(root)
    second = verify_pin.compute(root)

    assert re.fullmatch(r"[0-9a-f]{64}", first), f"not a sha256 hex digest: {first!r}"
    assert first == second


def test_b142_compute_matches_the_frozen_formula(tmp_path):
    """B142: sha256 over (relative posix path + NUL + bytes) for each
    pinned file in sorted order — gates.py, packager.py, redact.py and every prompt."""
    from harness import verify_pin

    root = _pin_repo(tmp_path / "repo")

    assert verify_pin.compute(root) == _expected_pin(root)


def test_b142_pinned_set_names_exactly_the_three_result_defining_modules():
    """B142: PINNED is gates.py, packager.py, redact.py (prompts/ is
    added by directory walk, not by listing)."""
    from harness import verify_pin

    assert tuple(verify_pin.PINNED) == (
        "harness/gates.py",
        "harness/packager.py",
        "harness/redact.py",
    )


def test_b142_one_changed_byte_in_a_prompt_changes_the_pin(tmp_path):
    """B142: prompts are pinned data; a single byte moves the hash."""
    from harness import verify_pin

    root = _pin_repo(tmp_path / "repo")
    before = verify_pin.compute(root)
    prompt = root / "prompts" / "system.md"
    prompt.write_bytes(prompt.read_bytes()[:-1] + b"!")

    after = verify_pin.compute(root)

    assert after != before


def test_b142_one_changed_byte_in_gates_py_changes_the_pin(tmp_path):
    """B142: the gate sequence is pinned; a single byte moves the hash."""
    from harness import verify_pin

    root = _pin_repo(tmp_path / "repo")
    before = verify_pin.compute(root)
    gates = root / "harness" / "gates.py"
    gates.write_bytes(gates.read_bytes() + b"\n")

    assert verify_pin.compute(root) != before


def test_b142_a_new_file_under_prompts_changes_the_pin(tmp_path):
    """B142: every file under prompts/ is in the set, so adding one moves it."""
    from harness import verify_pin

    root = _pin_repo(tmp_path / "repo")
    before = verify_pin.compute(root)
    (root / "prompts" / "revise.md").write_text("# revise\n", encoding="utf-8", newline="\n")

    assert verify_pin.compute(root) != before


def test_b142_a_change_outside_the_pinned_set_does_not_change_the_pin(tmp_path):
    """B142: only the pinned set is hashed; an unrelated module is not."""
    from harness import verify_pin

    root = _pin_repo(tmp_path / "repo")
    before = verify_pin.compute(root)
    (root / "harness" / "ledger.py").write_text("x = 1\n", encoding="utf-8", newline="\n")
    (root / "README.md").write_text("hello\n", encoding="utf-8", newline="\n")

    assert verify_pin.compute(root) == before


def test_b142_check_passes_when_the_pin_matches(tmp_path):
    """B142: a matching .harness/PIN is accepted silently."""
    from harness import verify_pin

    root = _pin_repo(tmp_path / "repo")
    (root / ".harness" / "PIN").write_text(
        verify_pin.compute(root) + "\n", encoding="utf-8", newline="\n"
    )

    assert verify_pin.check(root) is None


def test_b142_check_reads_the_first_token_of_the_first_line(tmp_path):
    """B142: .harness/PIN is `<sha256> [anything]`, first line only."""
    from harness import verify_pin

    root = _pin_repo(tmp_path / "repo")
    (root / ".harness" / "PIN").write_text(
        verify_pin.compute(root) + "  harness/gates.py harness/packager.py\nsecond line\n",
        encoding="utf-8",
        newline="\n",
    )

    assert verify_pin.check(root) is None


def test_b142_check_raises_pin_mismatch_on_a_wrong_pin(tmp_path):
    """B142: a wrong .harness/PIN is a startup failure, PinMismatch."""
    from harness import verify_pin
    from harness.errors import PinMismatch

    root = _pin_repo(tmp_path / "repo")
    (root / ".harness" / "PIN").write_text("0" * 64 + "\n", encoding="utf-8", newline="\n")

    with pytest.raises(PinMismatch):
        verify_pin.check(root)


def test_b142_check_raises_pin_mismatch_after_a_prompt_edit(tmp_path):
    """B142: the pin that matched stops matching once a prompt changes."""
    from harness import verify_pin
    from harness.errors import PinMismatch

    root = _pin_repo(tmp_path / "repo")
    (root / ".harness" / "PIN").write_text(
        verify_pin.compute(root) + "\n", encoding="utf-8", newline="\n"
    )
    (root / "prompts" / "system.md").write_text(
        "# system\nYou are a different harness.\n", encoding="utf-8", newline="\n"
    )

    with pytest.raises(PinMismatch):
        verify_pin.check(root)


def test_b142_pin_mismatch_is_a_harness_error():
    """B142: PinMismatch derives from HarnessError."""
    from harness.errors import HarnessError, PinMismatch

    assert issubclass(PinMismatch, HarnessError)


def test_b142_main_print_emits_the_computed_hash(tmp_path, monkeypatch, capsys):
    """B142: `verify_pin --print` prints the hash and exits 0."""
    from harness import verify_pin

    root = _pin_repo(tmp_path / "repo")
    monkeypatch.chdir(root)

    assert verify_pin.main(["--print"]) == 0

    printed = capsys.readouterr().out.strip()
    assert re.fullmatch(r"[0-9a-f]{64}", printed), f"--print must emit a sha256: {printed!r}"
    # The repo root is the cwd or the package's own root; either way the digest is compute()'s.
    assert printed in {verify_pin.compute(root), verify_pin.compute(REPO_ROOT)}


# --------------------------------------------------------------------------------------
# B143 — the pin and the repo-level HALT are outside allowed_roots()
# --------------------------------------------------------------------------------------


def test_b143_pin_and_repo_halt_are_outside_allowed_roots_while_state_and_proposals_are_in(
    tmp_path, write_env
):
    """B143: build_context registers ten roots — runs, packages, the db parent, the halt file,
    the package-root and cwd HUMAN.md/.env pairs, state/ and proposals/. `.harness/PIN` and
    `.harness/HALT` are not roots and lie under none."""
    from harness import redact
    from harness.config import load_config
    from harness.context import build_context

    (tmp_path / "data").mkdir()
    env_path = write_env(tmp_path / ".env", DB_PATH="data/harness.db", **D2_ENV_KEYS)
    config = load_config(env_path=env_path, environ={})
    previous = redact.allowed_roots()
    try:
        build_context(config)
        roots = tuple(Path(r).resolve() for r in redact.allowed_roots())
    finally:
        redact.set_write_roots(list(previous))

    env_root = tmp_path.resolve()
    assert len(roots) == 10, (
        f"expected Delivery 1's eight roots plus state/ and proposals/, nothing else: {roots}"
    )
    assert env_root / "state" in roots
    assert env_root / "proposals" in roots

    def under(path: Path) -> bool:
        return any(path == root or root in path.parents for root in roots)

    assert under(env_root / "state" / "ledger.json")
    assert under(env_root / "proposals" / "816-bundle-size.md")
    for rel in (".harness/PIN", ".harness/HALT", ".harness/config.json", ".harness/trust.txt"):
        assert not under((env_root / rel).resolve()), f"{rel} must be outside allowed_roots()"
        assert not under((REPO_ROOT / rel).resolve()), f"{rel} must be outside allowed_roots()"
    assert not under((env_root / ".harness").resolve())


# --------------------------------------------------------------------------------------
# The file map and the repository structure
# --------------------------------------------------------------------------------------


def test_a1_d2_file_map_is_complete_and_exclusive():
    """A1: every harness/**/*.py in the file map exists, and no other Python file does."""
    missing = [rel for rel in D2_PACKAGE_FILES if not (REPO_ROOT / rel).is_file()]
    assert missing == [], "handoff §3 files missing: " + ", ".join(missing)

    found = {_rel(path) for path in _harness_sources()}
    unexpected = sorted(found - set(D2_PACKAGE_FILES))
    assert unexpected == [], "a file not in the handoff §3 map MUST NOT exist: " + ", ".join(
        unexpected
    )
    assert set(SPEC_PACKAGE_FILES) == set(D2_PACKAGE_FILES), (
        "SPEC_PACKAGE_FILES must carry the Delivery 2 file map"
    )


def test_a1_d2_required_test_modules_and_fixtures_exist():
    """A1: every test module and fixture in the list exists."""
    missing = [rel for rel in D2_REQUIRED_TEST_FILES if not (REPO_ROOT / rel).is_file()]
    assert missing == [], "handoff §3 test files missing: " + ", ".join(missing)


def test_a1_d2_every_new_non_python_file_exists():
    """A1: every non-Python file in the list exists."""
    missing = [rel for rel in D2_REQUIRED_FILES if not (REPO_ROOT / rel).is_file()]
    assert missing == [], "handoff §3 files missing: " + ", ".join(missing)


def test_d2_r1_1_the_store_is_a_package_with_the_three_modules():
    """harness/store/{__init__,sqlite,github}.py exist."""
    store_dir = HARNESS_DIR / "store"
    assert store_dir.is_dir(), "harness/store/ must be a package"
    for name in ("__init__.py", "sqlite.py", "github.py"):
        assert (store_dir / name).is_file(), f"harness/store/{name} is required (D2-R1.1)"


def test_d2_r1_2_the_old_store_module_is_gone():
    """harness/store.py is gone; the store is a package."""
    assert not (HARNESS_DIR / "store.py").exists(), "harness/store.py must not exist (D2-R1.2)"


def test_d2_r1_3_the_four_new_modules_exist():
    """dispatcher.py, ledger.py, keywords.py and trust.py exist."""
    present = sorted(
        name
        for name in ("dispatcher.py", "ledger.py", "keywords.py", "trust.py")
        if (HARNESS_DIR / name).is_file()
    )
    assert present == ["dispatcher.py", "keywords.py", "ledger.py", "trust.py"]


def test_d2_r1_4_the_three_new_stages_exist():
    """stages/{revise,deliver,decompose}.py exist."""
    present = sorted(
        name
        for name in ("revise.py", "deliver.py", "decompose.py")
        if (HARNESS_DIR / "stages" / name).is_file()
    )
    assert present == ["decompose.py", "deliver.py", "revise.py"]


def test_d2_r1_5_the_workflow_set_is_the_handoffs_six_plus_only_what_was_written_down():
    """The original six all exist, and any other workflow is named in `ADDED_WORKFLOWS` with
    the decision that added it, so nothing runs on this repository's schedule and secrets
    without a written reason.
    """
    assert WORKFLOWS_DIR.is_dir(), ".github/workflows/ is required"
    found = sorted(p.name for p in WORKFLOWS_DIR.glob("*.yml"))

    missing = sorted(set(HANDOFF_WORKFLOWS) - set(found))
    assert missing == [], f"a workflow the handoff froze is gone: {missing}"

    unexplained = sorted(set(found) - set(HANDOFF_WORKFLOWS) - set(ADDED_WORKFLOWS))
    assert unexplained == [], (
        f"workflow files with no decision behind them: {unexplained}. Add an ADDED_WORKFLOWS "
        "entry naming the decision, or delete the file."
    )
    assert list(WORKFLOWS_DIR.glob("*.yaml")) == [], "no .yaml files beside the .yml"


def test_every_added_workflow_cites_a_decision_and_the_decision_exists():
    """Each `ADDED_WORKFLOWS` entry cites a decision DECISIONS.md carries as a heading."""
    import re

    decisions = (REPO_ROOT / "DECISIONS.md").read_text(encoding="utf-8")
    for name, reason in ADDED_WORKFLOWS.items():
        assert (WORKFLOWS_DIR / name).is_file(), f"{name} is listed but absent"
        found = re.match(r"(D\d+)/(B\d+)", reason)
        assert found, f"{name}: the reason must open with a D-number and a B-number"
        assert f"## {found.group(1)} " in decisions, (
            f"{name} cites {found.group(1)}, which DECISIONS.md does not carry"
        )


def test_d2_r1_6_governance_files_exist():
    """CODEOWNERS, .harness/trust.txt and .harness/config.json exist."""
    for rel in (".github/CODEOWNERS", ".harness/trust.txt", ".harness/config.json"):
        assert (REPO_ROOT / rel).is_file(), f"{rel} is required (D2-R1.6)"


def test_d2_r1_7_no_submodule_and_no_vendored_product_repo():
    """No .gitmodules, no submodules/, no vendored product checkout."""
    assert not (REPO_ROOT / ".gitmodules").exists(), ".gitmodules must not exist"
    assert not (REPO_ROOT / "submodules").exists(), "submodules/ must not exist"
    assert not (REPO_ROOT / "brightboost").exists(), "no vendored copy of the product repo"


def test_d2_r1_8_the_two_new_prompt_files_exist():
    """prompts/decompose.md and prompts/revise.md exist."""
    present = sorted(
        name for name in ("decompose.md", "revise.md") if (REPO_ROOT / "prompts" / name).is_file()
    )
    assert present == ["decompose.md", "revise.md"]


def test_d2_r1_9_the_five_local_mode_control_files_exist():
    """The root control plane is bb-start/stop/watcher.ps1, bb-configure.py and bb-config.json."""
    expected = ["bb-config.json", "bb-configure.py", "bb-start.ps1", "bb-stop.ps1", "bb-watcher.ps1"]
    found = sorted(
        p.name
        for p in REPO_ROOT.iterdir()
        if p.is_file() and (p.name.startswith("bb-") and p.suffix in (".ps1", ".py", ".json"))
    )
    assert found == expected, f"local control plane differs: {found}"


def test_d2_r1_10_local_has_at_least_seven_entries():
    """local/ carries the container plumbing (at least seven entries)."""
    local = REPO_ROOT / "local"
    assert local.is_dir(), "local/ is required"
    entries = [p.name for p in local.iterdir()]
    assert len(entries) >= 7, f"local/ has {len(entries)} entries: {entries}"
    for name in (
        "Dockerfile",
        "entrypoint.sh",
        "run.ps1",
        "watchdog-bb.ps1",
        "container_env.ps1",
        "preflight.py",
        "README.md",
    ):
        assert (local / name).is_file(), f"local/{name} is required"


def test_d2_r1_11_bb_work_and_runs_are_git_ignored():
    """.gitignore covers bb-work and runs."""
    gitignore = REPO_ROOT / ".gitignore"
    assert gitignore.is_file()
    lines = {ln.strip() for ln in _read(gitignore).splitlines()}
    assert lines & {"bb-work/", "bb-work", "/bb-work/", "/bb-work"}, (
        ".gitignore must ignore bb-work/ (D2-R1.11)"
    )
    assert lines & {"runs/", "runs", "/runs/", "/runs"}, ".gitignore must ignore runs/"


def test_d2_r1_12_pyproject_declares_no_runtime_dependency():
    """`project.dependencies` is [] or absent (I-17)."""
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"].get("dependencies", []) == []


def test_a44_watchdog_filename_and_content_avoid_the_rk_wildcard():
    """A44: local/watchdog-bb.ps1 exists, and neither its name nor its content contains
    `watchdog.ps1`, the pattern rk's process kill matches."""
    path = REPO_ROOT / "local" / "watchdog-bb.ps1"
    assert path.is_file(), "local/watchdog-bb.ps1 is required"
    assert "watchdog.ps1" not in path.name
    assert "watchdog.ps1" not in path.read_text(encoding="utf-8", errors="replace")


def test_d2_state_ledger_ships_as_an_empty_window_starting_2026_09_07():
    """state/ledger.json ships as Ledger.empty("2026-09-07T00:00:00Z")."""
    path = REPO_ROOT / "state" / "ledger.json"
    assert path.is_file(), "state/ledger.json is required (handoff §3)"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema"] == 1
    assert payload["window"]["period_start"] == "2026-09-07T00:00:00Z"
    assert "spent_usd" not in payload["window"]
    assert payload["window"]["calls"] == 0
    assert payload["window"]["rate_limited_until"] is None
    assert payload["history"] == []
    assert "observations" not in payload
    assert isinstance(payload["cursors"], dict)


def test_b112_harness_config_json_carries_exactly_the_fourteen_knob_keys():
    """B112: .harness/config.json's keys are exactly the operational knobs in `CONFIG_JSON_KEYS`
    above, and nothing that alters what the harness concludes."""
    path = REPO_ROOT / ".harness" / "config.json"
    assert path.is_file(), ".harness/config.json is required"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    assert set(payload) == set(CONFIG_JSON_KEYS), f"keys differ from B112's knob set: {sorted(payload)}"


def test_d2_trust_file_lists_the_operator_and_codeowners_protects_the_governance_paths():
    """.harness/trust.txt names jgoetzmann at the level that may end work; CODEOWNERS assigns
    /.harness/, /prompts/, /.github/, /harness/gates.py, /harness/redact.py and /proposals/."""
    # B269: the file is read with the parser, which understands its `<level> <handle>` lines.
    from harness import trust as trust_mod

    trusted = trust_mod.load_trust(REPO_ROOT / ".harness" / "trust.txt")
    assert "jgoetzmann" in trusted
    assert trusted.level_of("jgoetzmann") == trust_mod.MAX_LEVEL

    codeowners = (REPO_ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
    for pattern in (
        "/.harness/",
        "/prompts/",
        "/.github/",
        "/harness/gates.py",
        "/harness/redact.py",
        "/proposals/",
    ):
        assert re.search(
            r"^\s*" + re.escape(pattern) + r"\s+.*@jgoetzmann", codeowners, re.M
        ), f"CODEOWNERS must assign {pattern} to @jgoetzmann"


# --------------------------------------------------------------------------------------
# Workflow hygiene (regex over the YAML text)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ALL_WORKFLOWS)
def test_b124_every_cron_minute_is_non_zero_and_not_a_quarter_hour(name):
    """B124: every cron minute field is non-zero and non-round."""
    text = _d2_workflow(name)
    crons = _cron_values(text)
    if name in FROZEN_CRONS:
        assert crons, f"{name} must carry a cron schedule (handoff §7.1)"
    for cron in crons:
        fields = cron.split()
        assert len(fields) == 5, f"{name}: malformed cron {cron!r}"
        minute = fields[0]
        assert minute.isdigit(), f"{name}: cron minute must be a literal integer: {cron!r}"
        assert int(minute) != 0, f"{name}: cron minute must be non-zero: {cron!r}"
        assert int(minute) not in ROUND_MINUTES, f"{name}: cron minute is round: {cron!r}"


@pytest.mark.parametrize("name", ALL_WORKFLOWS)
def test_b124_crons_are_exactly_the_frozen_schedule(name):
    """B124: each scheduled workflow carries exactly its expected crons, and the others none."""
    crons = _cron_values(_d2_workflow(name))
    if name in FROZEN_CRONS:
        assert crons == FROZEN_CRONS[name], f"{name}: crons {crons} != {FROZEN_CRONS[name]}"
    else:
        assert crons == [], f"{name} must not be scheduled (handoff §7.1): {crons}"


def _cron_hours(field: str) -> list[int]:
    """The hours a cron hour field names: digits, commas and ``a-b`` ranges (D72)."""
    hours: list[int] = []
    for part in field.split(","):
        low, _, high = part.partition("-")
        hours.extend(range(int(low), int(high or low) + 1))
    return hours


@pytest.mark.parametrize("name", ["discover.yml", "implement.yml"])
def test_b412_the_spending_crons_fire_inside_the_daily_run_window(name):
    """B412 (D72): `.harness/config.json`'s daily window contains every discover and implement
    cron, every day. The cron is when GitHub wakes the job and the window is what the dispatcher
    enforces once it is awake, so a row outside the window wakes to nothing it may start."""
    config = json.loads((REPO_ROOT / ".harness" / "config.json").read_text(encoding="utf-8"))
    start, end = str(config["RUN_WINDOW_START"]), str(config["RUN_WINDOW_END"])
    assert start.startswith("daily ") and end.startswith("daily "), (start, end)

    def minute_of_day(point: str) -> int:
        hour, _, minute = point.split(" ", 1)[1].partition(":")
        return int(hour) * 60 + int(minute)

    low, high = minute_of_day(start), minute_of_day(end)
    assert low < high, "a daily window wrapping past midnight needs its own check here"
    crons = _cron_values(_d2_workflow(name))
    assert crons, f"{name} carries no cron"
    for cron in crons:
        minute, hour, day, month, weekday = cron.split()
        assert (day, month, weekday) == ("*", "*", "*"), f"{name}: {cron!r} is not daily"
        for at in (h * 60 + int(minute) for h in _cron_hours(hour)):
            assert low <= at < high, (
                f"{name}: {cron!r} fires outside {start}-{end.split(' ', 1)[1]} UTC"
            )


@pytest.mark.parametrize("name", ["discover.yml", "implement.yml"])
def test_b493_each_spending_workflow_schedules_more_than_one_firing(name):
    """B493 (D80): a dropped firing must not cost the day. GitHub delivers scheduled runs late
    and drops some entirely - on 2026-09-17 four implement firings produced one run, and the
    day's single discover firing arrived 264 minutes late - so one firing inside the window is
    one drop away from a day that builds nothing. Each spending workflow schedules at least two
    distinct firings, which is the half of D80 the window's width cannot supply."""
    firings = [
        hour * 60 + int(cron.split()[0])
        for cron in _cron_values(_d2_workflow(name))
        for hour in _cron_hours(cron.split()[1])
    ]
    assert len(firings) >= 2, f"{name} schedules only {firings}; one drop costs the day"
    assert len(set(firings)) == len(firings), f"{name} schedules a firing twice: {firings}"


@pytest.mark.parametrize("name", ALL_WORKFLOWS)
def test_b125_every_job_sets_a_timeout_of_at_most_120_minutes(name):
    """B125: `timeout-minutes` on every `jobs.<id>`, <= 120."""
    text = _d2_workflow(name)
    jobs = _workflow_jobs(text)
    assert jobs, f"{name}: no jobs found under `jobs:`"
    for job, block in jobs.items():
        match = re.search(r"^    timeout-minutes:\s*(\d+)\s*(#.*)?$", block, re.M)
        assert match, f"{name}: job {job!r} has no job-level timeout-minutes (B125)"
        assert int(match.group(1)) <= 120, f"{name}: job {job!r} timeout exceeds 120 (B125)"
        assert int(match.group(1)) > 0


@pytest.mark.parametrize("name", SPENDING_WORKFLOWS)
def test_b126_every_upload_artifact_step_runs_with_if_always(name):
    """B126: each spending workflow uploads runs/item-*/ under
    `if: always()` so a cancelled or timed-out run still leaves evidence."""
    text = _d2_workflow(name)
    blocks = [block for block in _step_blocks(text) if "upload-artifact" in block]
    assert blocks, f"{name}: no upload-artifact step (B126)"
    always = re.compile(r"^\s*if:\s*(\$\{\{\s*)?always\(\)\s*(\}\})?\s*(#.*)?$", re.M)
    for block in blocks:
        assert always.search(block), f"{name}: upload-artifact step lacks `if: always()`:\n{block}"
    assert any("runs/" in block for block in blocks), f"{name}: no artifact covers runs/ (B126)"


@pytest.mark.parametrize("name", ALL_WORKFLOWS)
def test_b126_no_upload_artifact_step_anywhere_lacks_if_always(name):
    """B126: wherever an artifact is uploaded, it is guarded."""
    text = _d2_workflow(name)
    always = re.compile(r"^\s*if:\s*(\$\{\{\s*)?always\(\)\s*(\}\})?\s*(#.*)?$", re.M)
    for block in _step_blocks(text):
        if "upload-artifact" in block:
            assert always.search(block), f"{name}: unguarded upload-artifact step:\n{block}"


@pytest.mark.parametrize("name", SPENDING_WORKFLOWS)
def test_b118_ledger_writers_share_one_concurrency_group_without_cancellation(name):
    """B118: concurrency group harness-ledger, cancel-in-progress false."""
    text = _d2_workflow(name)
    assert re.search(r"^\s*concurrency:", text, re.M), f"{name}: no concurrency block (B118)"
    assert re.search(r"cancel-in-progress:\s*false\b", text), (
        f"{name}: cancel-in-progress must be false (B118)"
    )
    assert re.search(r"group:\s*['\"]?harness-ledger['\"]?", text), (
        f"{name}: the concurrency group must be harness-ledger (B118)"
    )
    assert not re.search(r"cancel-in-progress:\s*true\b", text)


@pytest.mark.parametrize("name", ALL_WORKFLOWS)
def test_r7_6_permissions_are_declared_and_never_write_all(name):
    """Least privilege: a permissions block exists and `write-all` appears nowhere."""
    text = _d2_workflow(name)
    assert "write-all" not in text, f"{name}: write-all is not least privilege (D2-R7.6)"
    assert re.search(r"^\s*permissions:", text, re.M), f"{name}: no permissions block (D2-R7.6)"


def test_r7_8_pull_request_target_appears_in_no_workflow():
    """`pull_request_target` is absent from every workflow."""
    assert WORKFLOWS_DIR.is_dir()
    offenders = sorted(
        p.name
        for p in WORKFLOWS_DIR.iterdir()
        if p.is_file() and "pull_request_target" in p.read_text(encoding="utf-8", errors="replace")
    )
    assert offenders == [], "pull_request_target found in: " + ", ".join(offenders)


def test_b129_selftest_runs_the_fake_backend_suite_on_linux_and_windows():
    """B129: selftest.yml triggers on pull_request, matrixes
    ubuntu-latest and windows-latest, runs pytest under BACKEND=fake."""
    text = _d2_workflow("selftest.yml")
    assert _has_pull_request_trigger(text), "selftest.yml must run on pull_request (B129)"
    assert re.search(r"^\s*matrix:", text, re.M), "selftest.yml must use a matrix (B129)"
    assert "ubuntu-latest" in text
    assert "windows-latest" in text
    assert re.search(r"BACKEND\s*[:=]\s*['\"]?fake", text), "BACKEND=fake is required (B129)"
    assert "pytest" in text


def test_b130_selftest_uses_no_secret_and_is_the_only_pull_request_workflow():
    """B130: selftest.yml references no secret; no other workflow
    runs on pull_request."""
    selftest = _d2_workflow("selftest.yml")
    assert "secrets." not in selftest, "selftest.yml must not reference secrets (B130)"
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in selftest
    assert "HARNESS_GITHUB_TOKEN" not in selftest
    others = [
        name
        for name in ALL_WORKFLOWS
        if name != "selftest.yml" and _has_pull_request_trigger(_d2_workflow(name))
    ]
    assert others == [], "only selftest.yml may run on pull_request (B130): " + ", ".join(others)


def test_b144_heartbeat_references_the_tracking_issue_and_spends_nothing():
    """B144: heartbeat.yml comments on TRACKING_ISSUE weekly and
    never touches the Claude credential."""
    text = _d2_workflow("heartbeat.yml")
    assert "TRACKING_ISSUE" in text, "heartbeat.yml must reference TRACKING_ISSUE (B144)"
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in text, "the heartbeat spends nothing (B144)"
    assert re.search(r"harness\s+(run|propose|revise|decompose|implement)\b", text) is None
    assert _cron_values(text) == ["5 9 * * 1"]


def test_b145_ops_listens_to_completed_runs_of_the_three_spending_workflows():
    """B145: ops.yml triggers on workflow_run completed and labels its issues with the `kind:`
    label `LABEL_SPECS` creates, so the issue list never carries two ops labels (B264)."""
    from harness.store.sqlite import KIND_LABELS

    text = _d2_workflow("ops.yml")
    assert "workflow_run" in text, "ops.yml must trigger on workflow_run (B145)"
    assert "completed" in text
    assert KIND_LABELS["ops"] in text, f"ops.yml must label its issues {KIND_LABELS['ops']}"
    assert "harness:ops" not in text, "the pre-D4 label name is still in ops.yml"
    for workflow in ("discover", "implement", "feedback"):
        assert re.search(workflow, text, re.I), f"ops.yml must watch {workflow} (B145)"


def test_b146_ops_never_retries_a_step_that_could_have_spent():
    """B146: ops.yml retries a failed job only when the failing step is on an allow-list of
    steps that run before anything is spent.

    `reRunWorkflowRunFailedJobs` re-runs the whole job, so a step that runs after the spend,
    such as `Commit state/ledger.json`, is never retried: the retry reloads the pre-run ledger
    and pays for the same model call twice.
    """
    text = _d2_workflow("ops.yml")
    allow = text.split("RETRYABLE_STEPS = [")[1].split("];")[0]

    assert "const preSpend = RETRYABLE_STEPS.includes(failingStep);" in text, (
        "the allow-list must be the thing that decides, not merely present"
    )
    # Every step that runs a model call, a gate, or anything after the spend.
    for step in (
        "Discover and propose",
        "Run planned items",
        "Sweep keywords",
        "Reconcile stale",
        "Commit state/ledger.json",
        "Upload run artifacts",
    ):
        assert step not in allow, f"ops.yml would retry a job that had already spent: {step!r}"
    # The rule's own words stay in the file, so it can be found by name (B146).
    for word in ("revise", "propose", "gate", "spend"):
        assert re.search(word, text, re.I), f"ops.yml must still name {word!r} (B146)"


def _fork_slug() -> str:
    """The live fork, read from the committed config."""
    data = json.loads((REPO_ROOT / ".harness" / "config.json").read_text(encoding="utf-8"))
    return str(data["FORK_REPO"])


#: How a workflow names the fork: the literal slug, or the `FORK_REPO` variable in any form a
#: workflow uses it (`${{ vars.FORK_REPO }}`, `env.FORK_REPO`, `$FORK_REPO`, `${FORK_REPO}`).
_FORK_VARIABLE = re.compile(r"(?:vars|env)\.FORK_REPO|\$\{?FORK_REPO\b")


def _names_fork(line: str, fork: str) -> bool:
    return fork in line or bool(_FORK_VARIABLE.search(line))


def _pushes_github_to(line: str, fork: str) -> bool:
    return _names_fork(line, fork) and (
        ("git push" in line and ".github" in line) or ".github/workflows" in line
    )


def test_b105_no_workflow_pushes_a_workflow_file_to_the_fork():
    """B105: no push whose target is the fork carries a .github path; the fork's default branch
    only moves by fast-forward.

    The fork slug is read from config (B306) and the `FORK_REPO` variable counts as naming it
    (B312), so the matcher is shown to fire on both spellings and the scan must inspect at
    least one line that names the fork. Static only: it reads workflow YAML and not the runtime
    push path, which is `gh.push_branch`'s commit walk (B298)."""
    fork = _fork_slug()
    assert re.fullmatch(r"[\w.-]+/[\w.-]+", fork), f"FORK_REPO is not an owner/name: {fork!r}"
    assert _pushes_github_to(f"git push https://github.com/{fork}.git HEAD:.github/x", fork)
    assert _pushes_github_to('git push "https://x@github.com/${{ vars.FORK_REPO }}.git" '
                             "HEAD:refs/heads/h -- .github/workflows/ci-cd.yml", fork)
    assert not _pushes_github_to(f"git push https://github.com/{fork}.git HEAD:main", fork)
    assert not _pushes_github_to("git push https://github.com/${{ vars.FORK_REPO }}.git "
                                 "HEAD:main", fork)
    inspected = 0
    violations: list[str] = []
    for name in ALL_WORKFLOWS:
        for lineno, line in enumerate(_d2_workflow(name).splitlines(), start=1):
            if _names_fork(line, fork):
                inspected += 1
            if _pushes_github_to(line, fork):
                violations.append(f"{name}:{lineno}")
    assert violations == [], "B105 violated: " + ", ".join(violations)
    assert inspected > 0, (
        "B105 inspected no workflow line that names the fork, so it proves nothing -- the "
        "workflows reach the fork through the FORK_REPO variable, which the matcher must read"
    )


def test_b127_implement_yml_orders_halt_doctor_sync_fork_dispatch_then_work():
    """B127 / B150: HALT check → doctor → sync-fork → dispatch → run, in that order."""
    text = _d2_workflow("implement.yml")
    order = [
        ("halt", _first_line_index(text, r"\.harness/HALT")),
        ("doctor", _first_line_index(text, r"harness\s+doctor\b")),
        ("sync-fork", _first_line_index(text, r"harness\s+sync-fork\b")),
        ("dispatch", _first_line_index(text, r"harness\s+dispatch\b")),
        ("run", _first_line_index(text, r"harness\s+run\b")),
    ]
    missing = [step for step, index in order if index is None]
    assert missing == [], "implement.yml lacks steps: " + ", ".join(missing)
    indexes = [index for _, index in order]
    assert indexes == sorted(indexes) and len(set(indexes)) == len(indexes), (
        f"implement.yml step order is wrong (B127/B150): {order}"
    )


def test_b127_implement_yml_triggers_on_a_proposal_push_and_passes_the_claude_token():
    """B127 / B128: a push to main on proposals/** is the approval trigger,
    workflow_dispatch exists, CLAUDE_CODE_OAUTH_TOKEN comes from secrets, the ledger commit
    carries [skip ci] (B115)."""
    text = _d2_workflow("implement.yml")
    assert "proposals/**" in text, "a merged proposal must trigger implement.yml (B127)"
    assert "workflow_dispatch" in text
    assert re.search(
        r"CLAUDE_CODE_OAUTH_TOKEN:\s*\$\{\{\s*secrets\.CLAUDE_CODE_OAUTH_TOKEN\s*\}\}", text
    ), "CLAUDE_CODE_OAUTH_TOKEN must be passed from secrets (B128)"
    assert "[skip ci]" in text, "the ledger commit must carry [skip ci] (B115)"
    assert "state/ledger.json" in text


def _halt_step_block(text: str) -> str | None:
    """The one `steps:` item that logs the repo halt, as its own text.

    B149's exit code belongs to that step, not to the workflow as a whole.
    """
    blocks = [b for b in _step_blocks(text) if "halted by .harness/HALT" in b]
    return blocks[0] if len(blocks) == 1 else None


@pytest.mark.parametrize("name", SPENDING_WORKFLOWS)
def test_b149_every_spending_workflow_checks_repo_halt_before_doctor_and_dispatch(name):
    """B149 / B150 / A43: the .harness/HALT check precedes doctor and the dispatcher, logs why,
    and exits 0.

    The exit-code check reads only the halt step's own lines after the log line, and that step
    may exit no other way."""
    text = _d2_workflow(name)
    halt = _first_line_index(text, r"\.harness/HALT")
    assert halt is not None, f"{name}: no .harness/HALT check (B149)"
    assert "halted by .harness/HALT" in text, f"{name}: the halt step must log why (B149)"
    block = _halt_step_block(text)
    assert block is not None, f"{name}: exactly one step must log 'halted by .harness/HALT' (B149)"
    lines = block.splitlines()
    logged_at = next(i for i, line in enumerate(lines) if "halted by .harness/HALT" in line)
    assert any(re.match(r"^\s*exit 0\s*(#.*)?$", line) for line in lines[logged_at:]), (
        f"{name}: the halt step must exit 0 after logging why (B149):\n{block}"
    )
    other_exits = [
        line.strip()
        for line in lines
        if re.match(r"^\s*exit\b", line) and not re.match(r"^\s*exit 0\s*(#.*)?$", line)
    ]
    assert other_exits == [], (
        f"{name}: a repo halt is a normal outcome; the halt step may only exit 0 "
        f"(B149), got {other_exits}"
    )
    for later in (r"harness\s+doctor\b", r"harness\s+dispatch\b"):
        index = _first_line_index(text, later)
        if index is not None:
            assert halt < index, f"{name}: HALT must be checked before {later!r} (B150)"
    spend = _first_line_index(
        text, r"harness\s+(run|discover|propose|sweep|revise|deliver|decompose)\b"
    )
    assert spend is not None, f"{name}: no spending command found"
    assert halt < spend, f"{name}: HALT must be checked before any spending command (B150)"


@pytest.mark.parametrize("name", SPENDING_WORKFLOWS)
def test_b127_every_spending_workflow_runs_doctor_before_dispatching(name):
    """B127: doctor precedes dispatch in every spending job."""
    text = _d2_workflow(name)
    doctor = _first_line_index(text, r"harness\s+doctor\b")
    dispatch = _first_line_index(text, r"harness\s+dispatch\b")
    assert doctor is not None, f"{name}: harness doctor is required (B127)"
    assert dispatch is not None, f"{name}: harness dispatch is required (B122)"
    assert doctor < dispatch, f"{name}: doctor must run before dispatch (B127)"


# --------------------------------------------------------------------------------------
# B113 / B134 — properties that live in governance and documentation, checked as text
# --------------------------------------------------------------------------------------


def test_b113_branch_protection_is_a_named_human_prerequisite_and_codeowners_covers_governance(
    tmp_path, write_env
):
    """B113: main requires one approving review and no force-push, and the harness's own proposal
    PRs are subject to it. A person applies that repository setting, so the setup document
    `harness setup` renders names it; CODEOWNERS keeps the governance files behind review."""
    from harness.config import load_config
    from harness.identity import Identity

    class _Account:
        def get(self, path):
            return {"login": "jgoetzmann-bot", "id": 424242}

    identity = Identity(load_config(env_path=write_env(tmp_path / ".env"), environ={}), _Account())

    human = identity.render_human_doc(identity.assess(2)).lower()

    assert "branch protection" in human
    assert "force-push" in human.replace("force push", "force-push")
    owners = (REPO_ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
    for path in ("/.harness/", "/prompts/", "/.github/", "/harness/gates.py", "/harness/redact.py"):
        assert path in owners, f"CODEOWNERS must cover {path}"


def test_b134_operations_doc_states_the_event_driven_versus_polled_asymmetry():
    """B134: commands on this repository are event-driven; on the product repository the sweep
    finds them on feedback.yml's cron. docs/OPERATIONS.md says so."""
    ops = (REPO_ROOT / "docs" / "OPERATIONS.md").read_text(encoding="utf-8")
    assert "41 */3 * * 1-5" in ops
    lowered = ops.lower()
    assert "event" in lowered and ("poll" in lowered or "sweep" in lowered)


def test_b149_the_repo_level_halt_file_is_committable_while_the_root_halt_stays_ignored():
    """B149: `.harness/HALT` is committable, while the root `HALT` ignore line (I-6) still
    covers the local scratch kill file."""
    import subprocess

    def ignored(path: str) -> bool:
        return subprocess.run(["git", "check-ignore", "-q", path], cwd=REPO_ROOT).returncode == 0

    assert ignored("HALT"), "the root HALT must stay ignored (I-6)"
    assert not ignored(".harness/HALT"), ".harness/HALT must be committable (B149)"


# ======================================================================================
# The implement schedule and the knob set.
# ======================================================================================

# implement.yml runs hourly inside the daily 11:00 → 19:00 UTC window, and discover opens the
# session at 11:07 with a second attempt at 13:07 (D72, widened by D80). feedback and heartbeat
# keep their own schedules.
D72_IMPLEMENT_CRONS = ["23 11-18 * * *"]
D72_UNCHANGED_CRONS = {
    "feedback.yml": "41 */3 * * 1-5",
    "heartbeat.yml": "5 9 * * 1",
}


def test_b215_implement_yml_carries_exactly_the_d72_crons():
    """B215: implement.yml is scheduled as eight hourly passes, 11:23 to 18:23 UTC every day,
    and nothing else; B209's run window and carry loop run on that schedule (D72, D80)."""
    crons = _cron_values(_d2_workflow("implement.yml"))
    assert crons == D72_IMPLEMENT_CRONS, f"implement.yml crons {crons} != {D72_IMPLEMENT_CRONS}"


def test_b215_the_implement_crons_stay_inside_the_run_window():
    """Every scheduled implement run fires every day, and B412 checks each firing against the
    window `.harness/config.json` sets."""
    crons = _cron_values(_d2_workflow("implement.yml"))
    assert crons, "implement.yml carries no cron"
    for cron in crons:
        minute, hour, dom, month, dow = cron.split()
        assert (dom, month, dow) == ("*", "*", "*"), f"implement.yml: {cron!r} is not daily"
    test_b412_the_spending_crons_fire_inside_the_daily_run_window("implement.yml")


@pytest.mark.parametrize("name", ALL_WORKFLOWS)
def test_b124_d3_every_cron_minute_is_still_non_zero_and_not_a_quarter_hour(name):
    """B124: every cron minute in every workflow is a literal, non-zero, non-round integer, so
    the harness never joins the load GitHub queues at the top of the hour."""
    crons = _cron_values(_d2_workflow(name))
    if name == "implement.yml":
        assert crons == D72_IMPLEMENT_CRONS, f"implement.yml must carry the D72 crons: {crons}"
    for cron in crons:
        fields = cron.split()
        assert len(fields) == 5, f"{name}: malformed cron {cron!r}"
        minute = fields[0]
        assert minute.isdigit(), f"{name}: cron minute must be a literal integer: {cron!r}"
        assert int(minute) != 0, f"{name}: cron minute must be non-zero: {cron!r}"
        assert int(minute) not in ROUND_MINUTES, f"{name}: cron minute is round: {cron!r}"


def test_b215_d72_changed_only_the_discover_and_implement_schedules():
    """feedback.yml and heartbeat.yml keep their schedules, and ops.yml and selftest.yml carry
    none."""
    for name, cron in D72_UNCHANGED_CRONS.items():
        assert _cron_values(_d2_workflow(name)) == [cron], name
    for name in ("ops.yml", "selftest.yml"):
        assert _cron_values(_d2_workflow(name)) == [], name


def test_b215_implement_yml_documents_the_dst_drift():
    """The crons are pinned to UTC while the usage reset is quoted in Pacific time, so
    implement.yml comments the DST drift beside them."""
    text = _d2_workflow("implement.yml")
    lowered = text.lower()
    assert "dst" in lowered or "daylight" in lowered, "the DST drift must be commented"
    assert "utc" in lowered


# The first seven knobs plus the five usage-governance ones, INBOX_ISSUE and CO_AUTHOR. Built as
# a union, so the set still demands the newer keys if the shipped file drops them.
D3_CONFIG_JSON_KEYS = tuple(
    sorted(
        set(D2_CONFIG_JSON_KEYS)
        | set(D3_NEW_CONFIG_JSON_KEYS)
        | set(D4_NEW_CONFIG_JSON_KEYS)
        | set(D82_NEW_CONFIG_JSON_KEYS)
        | set(D88_NEW_CONFIG_JSON_KEYS)
    )
)


def test_b112_d3_harness_config_json_carries_the_five_new_knobs():
    """B112: the two usage stops, the carry leeway, the two run-window bounds and `INBOX_ISSUE`
    join the first seven knobs, nothing else does, and each knob has the right type."""
    path = REPO_ROOT / ".harness" / "config.json"
    assert path.is_file(), ".harness/config.json is required"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    assert set(payload) == set(D3_CONFIG_JSON_KEYS), (
        f"keys differ from the D3 knob set: {sorted(payload)}"
    )
    for key in ("WEEKLY_USAGE_STOP_PCT", "SESSION_USAGE_STOP_PCT", "OVERRUN_PCT"):
        assert isinstance(payload[key], (int, float)), f"{key} must be a number"
    for key in ("RUN_WINDOW_START", "RUN_WINDOW_END"):
        assert isinstance(payload[key], str), f"{key} must be a string"


# ======================================================================================
# Two contracts that live half in Python and half in YAML, pinned by reading the workflow text
# against the source of truth:
#   * a scheduled job's spend gate vs. the reason strings harness/dispatcher.py returns;
#   * discover.yml's id harvester vs. what `harness discover` prints.
# ======================================================================================

import fnmatch

DISPATCHER_PY = HARNESS_DIR / "dispatcher.py"

# The dispatcher reasons that mean capacity is gone. A spending job that proceeds through one
# reaches Governor.authorize, exits EXIT_BUDGET and pages an operator for normal scheduling,
# which D33 forbids.
MUST_STOP_REASON_PREFIXES = frozenset({
    "rate limited until ",
    "halted",
    # The commanded halt (`/harness halt`), classified apart so a `case` matching bare `halted`
    # exactly still stops on it.
    "halted by @",
    "weekly usage ",
    "session usage ",
    "carry leeway ",
})
# The one stop reason that must not stop discovery, which the run window does not gate (D32).
MAY_PROCEED_REASON_PREFIXES = frozenset({
    "outside run window (",
})


def _run_scripts(text: str) -> list[str]:
    """Every step's `run:` script, dedented — the shell a workflow actually executes."""
    scripts: list[str] = []
    for block in _step_blocks(text):
        lines = block.splitlines()
        for index, line in enumerate(lines):
            folded = re.match(r"^(\s*)run:\s*\|\s*$", line)
            if folded:
                indent = len(folded.group(1)) + 2
                body: list[str] = []
                for rest in lines[index + 1:]:
                    if rest.strip() and len(rest) - len(rest.lstrip(" ")) < indent:
                        break
                    body.append(rest[indent:])
                scripts.append("\n".join(body))
                break
            inline = re.match(r"^\s*run:\s*(\S.*?)\s*$", line)
            if inline:
                scripts.append(inline.group(1))
                break
    return scripts


def _shell_lines(script: str) -> list[str]:
    """The runnable lines of a shell script — comments and blanks dropped."""
    out = []
    for line in script.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            out.append(stripped)
    return out


def _reason_prefix_and_sample(node: ast.AST) -> tuple[str, str] | None:
    """``(literal prefix, a realistic sample)`` for one reason expression, or None.

    A plain string is its own prefix and sample; an f-string's prefix is the constant text
    before its first placeholder, and the sample fills every placeholder with ``1``.

    A concatenation's prefix is its left operand -- B295 appends the subscription reading to
    ``reserve`` -- and its sample carries a suffix, so a workflow that matches the token exactly
    rather than as a prefix fails here.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _reason_prefix_and_sample(node.left)
        if left is None:
            return None
        prefix, sample = left
        return prefix, f"{sample}; and more"
    if not isinstance(node, ast.JoinedStr):
        return None
    prefix_parts: list[str] = []
    sample_parts: list[str] = []
    still_prefix = True
    for part in node.values:
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            sample_parts.append(part.value)
            if still_prefix:
                prefix_parts.append(part.value)
        else:
            still_prefix = False
            sample_parts.append("1")
    prefix = "".join(prefix_parts)
    return (prefix, "".join(sample_parts)) if prefix else None


def _dispatcher_tree() -> ast.AST:
    return ast.parse(DISPATCHER_PY.read_text(encoding="utf-8"))


def _plan_calls(tree: ast.AST) -> list[ast.Call]:
    """Every ``Plan(...)`` construction in the module, in source order."""
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Plan"
    ]
    calls.sort(key=lambda node: (node.lineno, node.col_offset))
    return calls


def _dispatcher_reasons() -> dict[str, str]:
    """Every reason literal harness/dispatcher.py can hand a caller, as prefix -> sample.

    Read from the source, so a new reason string appears here as soon as it is written and the
    tests below demand that somebody classify it. The three sites are ``usage_stop``'s returns,
    ``_window_reason``'s return, and the
    literal ``reason=`` keywords of the ``Plan(...)`` constructions in ``plan``.
    """
    tree = _dispatcher_tree()
    found: dict[str, str] = {}

    def record(node: ast.AST) -> None:
        pair = _reason_prefix_and_sample(node)
        if pair is not None:
            found[pair[0]] = pair[1]

    for func in ast.walk(tree):
        if isinstance(func, ast.FunctionDef) and func.name in ("usage_stop", "_window_reason"):
            for node in ast.walk(func):
                if isinstance(node, ast.Return) and node.value is not None:
                    record(node.value)
    for call in _plan_calls(tree):
        for keyword in call.keywords:
            if keyword.arg == "reason":
                record(keyword.value)
    return found


def _case_blocks(text: str, subject: str = "$reason") -> list[str]:
    """Every ``case "<subject>" in ... esac`` block in a workflow's shell, as text."""
    opener = re.compile(r'^\s*case\s+"' + re.escape(subject) + r'"\s+in\s*$')
    blocks: list[str] = []
    for script in _run_scripts(text):
        lines = script.splitlines()
        start: int | None = None
        for index, line in enumerate(lines):
            if start is None:
                if opener.match(line):
                    start = index
                continue
            if line.strip() == "esac":
                blocks.append("\n".join(lines[start:index + 1]))
                start = None
    return blocks


def _case_clauses(block: str) -> list[tuple[list[str], str]]:
    """Split one case block into ordered ``(shell glob patterns, clause body)`` pairs."""
    clauses: list[tuple[list[str], str]] = []
    patterns: list[str] | None = None
    body: list[str] = []
    for stripped in _shell_lines(block):
        if stripped.startswith("case ") or stripped == "esac":
            continue
        if patterns is None:
            assert stripped.endswith(")"), f"expected a case pattern, got {stripped!r}"
            patterns = [
                alt.strip().replace('"', "").replace("'", "")
                for alt in stripped[:-1].split("|")
            ]
            body = []
            continue
        body.append(stripped)
        if stripped.endswith(";;"):
            clauses.append((patterns, "\n".join(body)))
            patterns = None
    assert patterns is None, f"unterminated case clause in:\n{block}"
    return clauses


def _case_body_for(block: str, sample: str) -> str:
    """The body the shell would run for ``sample`` — first matching clause wins, as in sh."""
    for patterns, body in _case_clauses(block):
        for pattern in patterns:
            if fnmatch.fnmatchcase(sample, pattern):
                return body
    raise AssertionError(f"no case clause matches {sample!r} in:\n{block}")


def test_dispatcher_reasons_are_all_classified_as_stopping_or_not():
    """Every reason literal in harness/dispatcher.py is classified as stopping a spending job or
    explicitly not, and a new reason fails here until somebody classifies it."""
    prefixes = set(_dispatcher_reasons())
    classified = MUST_STOP_REASON_PREFIXES | MAY_PROCEED_REASON_PREFIXES
    unclassified = sorted(prefixes - classified)
    assert not unclassified, (
        "harness/dispatcher.py returns reasons no workflow test classifies: "
        + ", ".join(repr(p) for p in unclassified)
        + " — add each to MUST_STOP_REASON_PREFIXES or MAY_PROCEED_REASON_PREFIXES"
    )
    missing = sorted(classified - prefixes)
    assert not missing, (
        "these classified reasons are no longer produced by harness/dispatcher.py: "
        + ", ".join(repr(p) for p in missing)
    )


def test_discover_yml_spend_gate_stops_on_every_usage_reason():
    """Every must-stop reason sets proceed=false in discover.yml's `case "$reason"`, evaluated
    the way sh evaluates it: the first matching clause wins (D33)."""
    reasons = _dispatcher_reasons()
    blocks = _case_blocks(_d2_workflow("discover.yml"))
    assert len(blocks) == 1, 'discover.yml must gate spend on exactly one `case "$reason"`'
    block = blocks[0]
    for prefix in sorted(MUST_STOP_REASON_PREFIXES):
        body = _case_body_for(block, reasons[prefix])
        assert "proceed=false" in body, (
            f"discover.yml proceeds on {reasons[prefix]!r}; a usage stop must stop the job"
        )
        assert "proceed=true" not in body, f"discover.yml's clause for {prefix!r} is ambiguous"


def test_discover_yml_spend_gate_does_not_stop_on_the_run_window():
    """The run window bounds implement.yml through its own crons (D32); discovery is a single
    triage call and is not window-gated, so "outside run window (...)" must proceed."""
    reasons = _dispatcher_reasons()
    block = _case_blocks(_d2_workflow("discover.yml"))[0]
    for prefix in sorted(MAY_PROCEED_REASON_PREFIXES):
        body = _case_body_for(block, reasons[prefix])
        assert "proceed=true" in body, (
            f"discover.yml stops on {reasons[prefix]!r}; the run window must not stop discovery"
        )
        assert "proceed=false" not in body


def test_discover_yml_spend_gate_proceeds_on_the_ordinary_slots_reason():
    """The default clause lets the ordinary B211 plan reason through."""
    block = _case_blocks(_d2_workflow("discover.yml"))[0]
    ordinary = "1 of max 1 slots; weekly 39%, session 7%"
    assert "proceed=true" in _case_body_for(block, ordinary)


@pytest.mark.parametrize("name", SPENDING_WORKFLOWS)
def test_no_spending_workflow_gates_on_reason_with_an_incomplete_stop_set(name):
    """A spending workflow that gates on the dispatcher's reason gates on the whole stop set.
    implement.yml consumes `.start`, which is empty under every stop, and feedback.yml has no
    gate, so its keyword sweep and its reconciliation keep working while capacity is gone."""
    reasons = _dispatcher_reasons()
    for block in _case_blocks(_d2_workflow(name)):
        for prefix in sorted(MUST_STOP_REASON_PREFIXES):
            body = _case_body_for(block, reasons[prefix])
            assert "proceed=false" in body, (
                f"{name} gates on the dispatcher reason but proceeds on {reasons[prefix]!r}"
            )


# --------------------------------------------------------------------------------------
# The discover invocation and the id harvester are one contract.
# --------------------------------------------------------------------------------------

DISCOVER_CALL = re.compile(r"\bharness\s+((?:--[\w-]+\s+)*)discover\b")
# What may precede a command in sh: nothing, a pipe/list operator, a subshell, or a keyword.
# Anything else — `echo "... harness discover ..."` — is text about the command, not the call.
COMMAND_POSITION = re.compile(
    r"(?:^|[|;&(]|\b(?:if|then|else|elif|do|while|until|not)\s|\$\(|!)\s*$"
)


def _discover_call(line: str) -> re.Match[str] | None:
    """The `harness ... discover` invocation on this shell line, if it really is one."""
    for match in DISCOVER_CALL.finditer(line):
        if COMMAND_POSITION.search(line[:match.start()]):
            return match
    return None


def _discover_script(text: str) -> str | None:
    """The one `run:` script that invokes `harness discover`, or None."""
    for script in _run_scripts(text):
        if any(_discover_call(line) for line in _shell_lines(script)):
            return script
    return None


def test_cmd_discover_json_payload_is_the_created_key():
    """With the global --json flag `discover` prints one object whose only key is "created";
    without it, one bare id per line. The harvester below reads the first form."""
    tree = ast.parse((HARNESS_DIR / "__main__.py").read_text(encoding="utf-8"))
    func = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "cmd_discover"
    )
    dicts = [node for node in ast.walk(func) if isinstance(node, ast.Dict)]
    assert len(dicts) == 1, "cmd_discover should build exactly one JSON payload"
    keys = [key.value for key in dicts[0].keys if isinstance(key, ast.Constant)]
    assert keys == ["created"], f"cmd_discover's --json payload keys are {keys}"


def test_discover_yml_asks_for_json_and_harvests_the_created_ids():
    """The two halves agree: the global --json flag precedes the subcommand, and the jq reader
    takes `.created` out of the file the invocation tees into."""
    script = _discover_script(_d2_workflow("discover.yml"))
    assert script is not None, "discover.yml must invoke `harness discover`"
    lines = _shell_lines(script)
    call = next((line for line in lines if _discover_call(line)), None)
    assert call is not None
    match = _discover_call(call)
    assert "--json" in match.group(1), (
        "the global --json flag must precede the subcommand: " + call
    )
    tee = re.search(r"\|\s*tee\s+([^\s;]+)", call)
    assert tee, "the discover output must be teed to a file for the harvester and the artifact"
    target = tee.group(1)
    harvest = [line for line in lines if "jq" in line and target in line]
    assert harvest, f"nothing in the step reads {target} back with jq"
    assert any(".created" in line for line in harvest), (
        f"the harvester must read `.created` out of {target}: {harvest}"
    )


def test_discover_yml_no_longer_carries_the_dead_json_guessing_parser():
    """The step carries no parser that guesses at output shapes, and no fallback to
    `harness status --json`, which has queue counts and no item ids."""
    script = _discover_script(_d2_workflow("discover.yml"))
    assert script is not None
    for dead in ("pick_ids", "json.loads", "harness status"):
        assert dead not in script, f"{dead!r} is the dead guessing parser"


@pytest.mark.parametrize("name", ALL_WORKFLOWS)
def test_no_workflow_invokes_discover_without_the_global_json_flag(name):
    """Every `harness discover` call in a workflow passes the global --json flag."""
    for script in _run_scripts(_d2_workflow(name)):
        for line in _shell_lines(script):
            match = _discover_call(line)
            if match:
                assert "--json" in match.group(1), (
                    f"{name}: `harness discover` without the global --json flag: {line}"
                )


def _plan_to_json_keys() -> list[str]:
    """The keys `harness dispatch` prints — Plan.to_json's payload, read from the source."""
    cls = next(
        node for node in ast.walk(_dispatcher_tree())
        if isinstance(node, ast.ClassDef) and node.name == "Plan"
    )
    func = next(
        node for node in ast.walk(cls)
        if isinstance(node, ast.FunctionDef) and node.name == "to_json"
    )
    payload = next(node for node in ast.walk(func) if isinstance(node, ast.Dict))
    return [key.value for key in payload.keys if isinstance(key, ast.Constant)]


@pytest.mark.parametrize("name", SPENDING_WORKFLOWS)
def test_every_jq_key_read_off_the_dispatch_plan_exists_in_plan_to_json(name):
    """`harness dispatch` always prints Plan.to_json, with no --json flag involved, so every key
    implement.yml and discover.yml pull out of the plan is one Plan writes."""
    keys = set(_plan_to_json_keys())
    referenced: set[str] = set()
    for script in _run_scripts(_d2_workflow(name)):
        for line in _shell_lines(script):
            if "jq" not in line or "$plan" not in line:
                continue
            referenced.update(re.findall(r"\.([A-Za-z_]\w*)", line))
            referenced.update(re.findall(r'has\("([A-Za-z_]\w*)"\)', line))
    unknown = sorted(referenced - keys)
    assert not unknown, (
        f"{name} reads {unknown} off the dispatch plan; Plan.to_json writes {sorted(keys)}"
    )


def test_implement_yml_takes_its_items_from_the_plans_start_list():
    """implement.yml needs no reason gate because it consumes `.start`, and every dispatcher
    stop returns `Plan(start=(), ...)`, so the run step finds nothing to do and exits 0."""
    text = _d2_workflow("implement.yml")
    assert re.search(r"jq\s+-r\s+'\.start\[\]'", text), (
        "implement.yml must harvest its items from the plan's .start list"
    )
    assert _case_blocks(text) == [], (
        "implement.yml gates on .start, not on the reason; a reason gate here needs the full "
        "stop set (see test_no_spending_workflow_gates_on_reason_with_an_incomplete_stop_set)"
    )
    tree = _dispatcher_tree()
    func = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "plan"
    )
    calls = _plan_calls(func)
    assert len(calls) >= 2, "harness.dispatcher.plan should build several plans"
    for call in calls[:-1]:
        start = next((kw.value for kw in call.keywords if kw.arg == "start"), None)
        assert isinstance(start, ast.Tuple) and not start.elts, (
            f"dispatcher.py:{call.lineno} returns a non-empty start for a stop; implement.yml's "
            "gate assumes every stop plan starts nothing"
        )


def _exit_code_constant(name: str) -> int:
    """One of harness/__main__.py's EXIT_* constants, read from the source."""
    tree = ast.parse((HARNESS_DIR / "__main__.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                assert isinstance(node.value, ast.Constant), f"{name} must be a literal"
                return int(node.value.value)
    raise AssertionError(f"{name} is not defined in harness/__main__.py")


def test_discover_yml_treats_a_budget_exit_as_a_normal_outcome():
    """Capacity can run out after the dispatch step, and a limit is a normal outcome (B120), so
    both spending commands guard on EXIT_BUDGET, using the number harness/__main__.py
    returns."""
    budget = _exit_code_constant("EXIT_BUDGET")
    script = _discover_script(_d2_workflow("discover.yml"))
    assert script is not None
    lines = _shell_lines(script)
    assignments = [line for line in lines if re.fullmatch(r"EXIT_BUDGET=\d+", line)]
    assert len(assignments) == 1, f"expected one EXIT_BUDGET assignment, got {assignments}"
    assert assignments[0] == f"EXIT_BUDGET={budget}", (
        f"discover.yml says {assignments[0]}; harness/__main__.py returns {budget}"
    )
    guards = [
        index for index, line in enumerate(lines)
        if "${EXIT_BUDGET}" in line and not line.startswith("EXIT_BUDGET=")
    ]
    assert len(guards) >= 2, (
        "both `harness discover` and the `harness propose` loop must guard on EXIT_BUDGET"
    )
    for index in guards:
        handler = lines[index + 1:index + 4]
        assert any(line in ("exit 0", "break") for line in handler), (
            f"the EXIT_BUDGET guard at {lines[index]!r} must exit 0 or break: {handler}"
        )
        assert not any("failed=1" in line for line in handler), (
            "a budget stop must not be recorded as a failed run"
        )


def test_discover_yml_still_fails_the_run_on_a_real_error():
    """Any other non-zero exit from `harness discover` fails the step, and a `harness propose`
    that fails for a non-budget reason sets `failed`, so a broken run is red and reaches
    ops.yml."""
    script = _discover_script(_d2_workflow("discover.yml"))
    lines = _shell_lines(script)
    assert 'exit "${status}"' in lines, "a non-budget discover failure must fail the step"
    assert "failed=1" in lines, "a non-budget propose failure must fail the step"
    assert 'exit "$failed"' in lines, "the step's exit code must carry the propose failures"


# --------------------------------------------------------------------------------------
# B217 — the pin survives a checkout that rewrites line endings
# --------------------------------------------------------------------------------------


def test_b217_a_crlf_checkout_computes_the_same_pin(tmp_path):
    """B217: `core.autocrlf` is true by default on Git for Windows and actions/checkout inherits
    it, so the pin is computed over normalised line endings."""
    from harness import verify_pin

    lf = _pin_repo(tmp_path / "lf")
    crlf = tmp_path / "crlf"
    for rel in verify_pin.pinned_files(lf):
        dst = crlf / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        body = verify_pin.normalise((lf / rel).read_bytes())
        dst.write_bytes(body.replace(verify_pin.LF, verify_pin.CRLF))

    assert verify_pin.compute(crlf) == verify_pin.compute(lf)


def test_b217_normalise_folds_crlf_and_lone_cr_to_lf():
    """B217: both rewrites a checkout can produce collapse to one form."""
    from harness import verify_pin

    assert verify_pin.normalise(b"a" + verify_pin.CRLF + b"b") == b"a" + verify_pin.LF + b"b"
    assert verify_pin.normalise(b"a" + verify_pin.CR + b"b") == b"a" + verify_pin.LF + b"b"
    assert verify_pin.normalise(b"a" + verify_pin.LF + b"b") == b"a" + verify_pin.LF + b"b"


def test_b217_normalising_does_not_blunt_the_pin(tmp_path):
    """B217: only line endings are folded; every other byte still moves the hash."""
    from harness import verify_pin

    root = _pin_repo(tmp_path / "repo")
    before = verify_pin.compute(root)
    target = root / "harness" / "gates.py"
    target.write_bytes(target.read_bytes() + b"# one more comment\n")

    assert verify_pin.compute(root) != before


def test_b217_the_committed_pin_matches_this_working_tree():
    """B217: whatever else changed, .harness/PIN describes the tree it ships with."""
    from harness import verify_pin

    root = verify_pin.default_repo_root()
    if not (root / verify_pin.PIN_RELATIVE).is_file():
        pytest.skip("no .harness/PIN in this tree")

    verify_pin.check(root)


def test_the_fake_backend_has_a_fixture_for_every_stage_that_calls_a_model():
    """`BACKEND=fake`, which README.md and LOCAL-MODE.md tell a new operator to run, answers
    from `tests/fixtures/runner/<stage>.json`, so every stage that calls a model has a fixture
    and can be tried without a credential.
    """
    from harness.runner.fake import DEFAULT_FIXTURES_DIR

    # Every stage the fake runner can be asked for; `deliver` uses the `package` fixture.
    reachable = {
        "discover", "propose", "implement", "package", "revise", "decompose",
        "diagnose_gate_failure", "ask", "audit",
    }
    present = {path.stem for path in DEFAULT_FIXTURES_DIR.glob("*.json")}

    missing = sorted(reachable - present)
    assert missing == [], (
        "no fake fixture for: " + ", ".join(missing) + " — `BACKEND=fake` cannot reach "
        "those stages, so nobody can try them without a real credential"
    )


# --------------------------------------------------------------------------------------
# D74 - no dollar machinery survives anywhere
# --------------------------------------------------------------------------------------

#: Two places a retired name still belongs under `harness/`: the tuple that keeps an existing
#: .env and .harness/config.json loading after D74 removed the keys it names, and the one line
#: that drops a pre-D74 ledger's spend field the next time the ledger is saved.
RETIRED_KEYS_TUPLE = re.compile(r"RETIRED_KEYS[^=]*=\s*\([^)]*\)", re.S)
LEDGER_SPEND_DROP = re.compile(r'window\.pop\("spent_usd", None\)')

DOLLAR_TOKENS = (
    "max-budget-usd", "max_budget_usd", "cost_usd", "spent_usd", "static_usd", "total_cost_usd",
)


def test_B419_no_module_under_harness_computes_or_prints_a_dollar_figure():
    """B419: D74 removed the weekly cap, the per-call cap and every estimate, so no source
    under harness/ names one. The two exemptions are `config.RETIRED_KEYS` and the ledger's
    one-line drop of a pre-D74 `spent_usd`; D74 names both, and each is exempt only in the
    file it belongs to."""
    offenders: list[str] = []
    for path in _harness_sources():
        text = _read(path)
        if path.name == "config.py":
            text = RETIRED_KEYS_TUPLE.sub("", text)
        if path.name == "ledger.py":
            text = LEDGER_SPEND_DROP.sub("", text)
        lowered = text.lower()
        for token in DOLLAR_TOKENS:
            if token in lowered:
                offenders.append(f"{_rel(path)}: {token}")
        if re.search(r"\busd\b", lowered):
            offenders.append(f"{_rel(path)}: usd")
        if "dollar" in lowered:
            offenders.append(f"{_rel(path)}: dollar")
        if re.search(r"\$\d", text):
            offenders.append(f"{_rel(path)}: a $ before a digit")
    assert offenders == [], "dollar machinery under harness/: " + ", ".join(sorted(offenders))


def test_B426_no_workflow_or_local_script_reads_a_dollar_figure():
    """B426: the workflows and the host-side scripts read the ledger directly, so a field D74
    removed from it must not still be read from one of them. Every workflow and every
    PowerShell script is scanned, not a chosen few, so a new reader cannot arrive unnoticed."""
    scanned = (
        sorted(WORKFLOWS_DIR.glob("*.yml"))
        + sorted(REPO_ROOT.glob("*.ps1"))
        + sorted((REPO_ROOT / "local").glob("*.ps1"))
    )
    assert len(scanned) >= 11, f"expected every workflow and both script sets: {scanned}"
    for path in scanned:
        lowered = path.read_text(encoding="utf-8").lower()
        for token in DOLLAR_TOKENS + ("weekly_cap_usd", "reserve_pct", "get-spend"):
            assert token not in lowered, f"{_rel(path)} still reads {token}"
    assert "reserve" not in _case_blocks(_d2_workflow("discover.yml"))[0]

    # The host push still reads the token and the fork through this helper (D67).
    watchdog = (REPO_ROOT / "local" / "watchdog-bb.ps1").read_text(encoding="utf-8")
    assert "Read-EnvValue" in watchdog
    run_ps1 = (REPO_ROOT / "local" / "run.ps1").read_text(encoding="utf-8")
    assert "MAX_CONCURRENT_CLONES" not in run_ps1


def test_B429_the_shipped_config_files_carry_no_retired_key():
    """B429: the two committed configuration files name only live keys. `ANTHROPIC_API_KEY`
    stays known, redacted and stripped from the runner's environment; its example line goes."""
    from harness import config as config_mod
    from harness.runner import cli as runner_cli

    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    shipped = json.loads((REPO_ROOT / ".harness" / "config.json").read_text(encoding="utf-8"))
    for key in config_mod.RETIRED_KEYS:
        assert key not in env_example, f".env.example still carries {key}"
        assert key not in shipped, f".harness/config.json still carries {key}"

    assert "ANTHROPIC_API_KEY=" not in env_example
    keys = re.findall(r"^([A-Z_]+)=", env_example, re.M)
    assert len(keys) == 43, f".env.example carries {len(keys)} keys: {keys}"
    assert len(shipped) == 15, f".harness/config.json carries {len(shipped)} keys"
    assert "ANTHROPIC_API_KEY" in config_mod.SECRET_KEYS
    assert "ANTHROPIC_API_KEY" in runner_cli.STRIPPED_ENV_KEYS


def test_B434_the_suite_imports_the_harness_that_sits_beside_it():
    """B434: `pythonpath` puts the rootdir first on `sys.path`, ahead of the meta-path finder
    the editable install appends, so a bare `pytest` imports this tree. A worktree that tested
    the main checkout's source would report on a diff it never ran (D75).

    `python -m pytest` puts the working directory first by itself, so under the documented
    command the import below holds either way: the line that bites for a bare `pytest` is the
    assertion on the setting."""
    import harness

    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["tool"]["pytest"]["ini_options"].get("pythonpath") == ["."]
    assert Path(harness.__file__).resolve().parent == HARNESS_DIR
