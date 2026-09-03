"""Invariant tests — HARNESS-SPEC §9 (I-1 … I-10) plus the §10.1 structural criterion A1.

These are checked by inspecting the source tree at run time, not by running the program.
Every test here is a negative assertion: it proves a forbidden construct is absent.

Stack: Python 3.13 standard library + pytest==8.3.4 only.
"""

from __future__ import annotations

import ast
import builtins
import io
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS_DIR = REPO_ROOT / "harness"
TESTS_DIR = REPO_ROOT / "tests"

# §4.1 — every Python file the package is allowed to contain.
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
    # Delivery 2 — DELIVERY-2-HANDOFF §3 file map (data change only; D2-R12.3).
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

# §4.3 — every test module the delivery is required to ship.
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
        # I-1' (Delivery 2, DECISIONS D13): harness/gh.py is the one module permitted to issue a
        # non-GET request; the new I-11 test pins that exemption to gh.py alone.
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
# I-5 — SQL exists only in store.py
# --------------------------------------------------------------------------------------


def test_i5_sql_exists_only_in_store_py():
    violations: list[str] = []

    for path in _harness_sources():
        # Delivery 2 moved the SQLite store to a package (DECISIONS D12); SQL lives there only.
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
# A1 — the file manifest of §4 is exact
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
# Delivery 2 additions — DELIVERY-2-HANDOFF.md §3 (file map, D2-R1), §7 (workflow hygiene),
# §10.4 (B142, B143), §12 (I-2′, I-11 … I-17), plus RUN-DECISIONS-D2 §10/§15/§17.
# Appended by the D2 spec-tester (T3). Additions only — D2-R12.3. Nothing above was edited
# except the SPEC_PACKAGE_FILES data constant, which gained the D2 file-map entries.
# ======================================================================================

import hashlib
import json
import sys
import tomllib

WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
SPENDING_WORKFLOWS = ("discover.yml", "implement.yml", "feedback.yml")
ALL_WORKFLOWS = (
    "discover.yml",
    "implement.yml",
    "feedback.yml",
    "ops.yml",
    "heartbeat.yml",
    "selftest.yml",
)
# RUN-DECISIONS-D2 §15 / handoff §7.1 — the frozen crons. ops.yml and selftest.yml have none.
FROZEN_CRONS = {
    "discover.yml": "17 7 * * 0",
    "implement.yml": "23 */6 * * *",
    "feedback.yml": "41 */3 * * 1-5",
    "heartbeat.yml": "5 9 * * 1",
}
ROUND_MINUTES = (0, 15, 30, 45)

# RUN-DECISIONS-D2 §7 — every gh.py write method (I-13's write set).
GH_WRITE_METHODS = (
    "comment",
    "set_labels",
    "create_issue",
    "create_pull",
    "request_reviewers",
    "close_pull",
    "create_branch_file",
)

# Handoff §3 — the Python files Delivery 2 adds (D1's 27 minus store.py, plus store/*).
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
]

# Handoff §3 — test modules and fixtures marked NEW.
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
]

# Handoff §3 — every non-Python file marked NEW.
D2_REQUIRED_FILES = [
    ".github/workflows/discover.yml",
    ".github/workflows/implement.yml",
    ".github/workflows/feedback.yml",
    ".github/workflows/ops.yml",
    ".github/workflows/heartbeat.yml",
    ".github/workflows/selftest.yml",
    ".github/CODEOWNERS",
    ".github/ISSUE_TEMPLATE/work-item.md",
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
    "DELIVERY-2-REVIEW.md",
]

# RUN-DECISIONS-D2 §2 — the eleven .harness/config.json knob keys (B112).
CONFIG_JSON_KEYS = (
    "WEEKLY_CAP_USD",
    "PER_CALL_CAP_USD",
    "RESERVE_PCT",
    "MAX_CONCURRENT_ITEMS",
    "MAX_REVISE_CYCLES",
    "NOTIFY_POLL_HOURS",
    "MAX_SUBISSUES",
    "TRACKING_ISSUE",
    "FORK_REPO",
    "UPSTREAM_REPO",
    "TRUST_FILE",
)

# RUN-DECISIONS-D2 §2 — the D2 .env keys, .env.example values (inline; duplicated on purpose).
D2_ENV_KEYS: dict[str, str] = {
    "WEEKLY_CAP_USD": "25.00",
    "PER_CALL_CAP_USD": "3.00",
    "MAX_CONCURRENT_ITEMS": "1",
    "MAX_REVISE_CYCLES": "3",
    "FORK_REPO": "",
    "UPSTREAM_REPO": "Bright-Bots-Initiative/brightboost",
    "TRUST_FILE": ".harness/trust.txt",
    "NOTIFY_POLL_HOURS": "3",
    "MAX_SUBISSUES": "8",
    "SELF_REPO": "jgoetzmann/bright-bots-harness",
    "TRACKING_ISSUE": "",
    "STORE_BACKEND": "sqlite",
}

# RUN-DECISIONS-D2 §10 — a minimal tree carrying every pinned path.
PIN_TREE: dict[str, str] = {
    "harness/gates.py": "SEQUENCE = ('npm run lint', 'npm run build')\n",
    "harness/packager.py": "def build():\n    return 'package'\n",
    "harness/redact.py": "REDACTION = '[REDACTED]'\n",
    "prompts/system.md": "# system\nYou are the harness.\n",
    "prompts/decompose.md": "# decompose\n$issue_title\n$max\n",
}


# --------------------------------------------------------------------------------------
# D2 helpers
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
    """RUN-DECISIONS-D2 §10: sha256 over (posix path + NUL + bytes) per pinned file, sorted."""
    pinned = ["harness/gates.py", "harness/packager.py", "harness/redact.py"]
    pinned += sorted(
        f"prompts/{p.name}" for p in (root / "prompts").iterdir() if p.is_file()
    )
    digest = hashlib.sha256()
    for rel in sorted(pinned):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update((root / rel).read_bytes())
    return digest.hexdigest()


# --------------------------------------------------------------------------------------
# I-2′ — the gh CLI is never invoked; harness/gh.py is the only GitHub access path
# --------------------------------------------------------------------------------------


def test_i2_prime_the_gh_cli_is_never_invoked_and_gh_py_is_the_only_transport():
    """I-2′ (handoff §12, D2-R3.1): no `gh` program token anywhere; the D1 semantics unchanged."""
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
    """I-11 (handoff §12, D2-R3.2): the string `Authorization` — the header an authenticated
    request carries — is built in harness/gh.py and nowhere else.

    Corrected at Delivery 2 reconcile (.fullsend/notes/test-corrections-d2.md): the scan reads
    string constants and f-string parts, not identifiers, because HARNESS-SPEC §5.3 froze the
    `governor.Authorization` dataclass in Delivery 1 and I-11 is about the request header.
    Docstrings are prose, not a header, and are skipped.
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
    """I-11 (handoff §12, D2-R3.3): github_token() lives in config.py; only gh.py imports it."""
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
# I-12 — no code path merges, approves or dismisses (name contains `merge` for D2-R3.5)
# --------------------------------------------------------------------------------------


def test_i12_merge_approve_and_dismiss_endpoints_do_not_exist():
    """I-12 / B109 (handoff §4.5, §12; D2-R3.4, R3.5): the code to merge, approve or dismiss
    a review does not exist — no such endpoint string, no such payload, no such method."""
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
# I-13 — every gh.py write routes its payload through redact (name contains `redact`, R3.6)
# --------------------------------------------------------------------------------------


def test_i13_every_gh_write_method_routes_its_payload_through_redact():
    """I-13 / B108 (handoff §12, D2-R3.6): each GitHubClient write method's body reaches a
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
# I-14 — no issue is created outside this repository (name contains `issue_repo`, R3.7)
# --------------------------------------------------------------------------------------


def test_i14_create_issue_repo_is_always_self_repo():
    """I-14 / B110 (handoff §4.6, §12; D2-R3.7): create_issue has no repo parameter and
    targets self_repo; no call site passes one."""
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


# --------------------------------------------------------------------------------------
# I-15 — B64's forbidden-diff check stays single-sourced in implement.py
# --------------------------------------------------------------------------------------


def test_i15_reject_forbidden_diff_lives_in_implement_py_and_is_not_copied_into_deliver_py():
    """I-15 (handoff §12, D2-R3.8): `_reject_forbidden_diff` exists in implement.py and
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


# --------------------------------------------------------------------------------------
# I-16 — no module above the store branches on execution mode
# --------------------------------------------------------------------------------------


def test_i16_no_module_above_the_store_branches_on_execution_mode():
    """I-16 (handoff §2, §12; D2-R3.9): the exact review grep finds nothing under stages/,
    gates.py, packager.py or governor.py."""
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
# I-17 — stdlib only (name contains `stdlib`, R3.10)
# --------------------------------------------------------------------------------------


def test_i17_stdlib_only_no_runtime_dependency_and_no_third_party_import():
    """I-17 (handoff §12, D2-R1.12, R3.10): pyproject has no runtime dependency and every
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
    """RUN-DECISIONS-D2 §17 (handoff R9.3 scan): no `threading` or `asyncio` import anywhere
    under harness/; the local-loop heartbeat is a plain sleep loop."""
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
    """B142 (handoff §10.4): compute() over the pinned set is deterministic."""
    from harness import verify_pin

    root = _pin_repo(tmp_path / "repo")

    first = verify_pin.compute(root)
    second = verify_pin.compute(root)

    assert re.fullmatch(r"[0-9a-f]{64}", first), f"not a sha256 hex digest: {first!r}"
    assert first == second


def test_b142_compute_matches_the_frozen_formula(tmp_path):
    """B142 / RUN-DECISIONS-D2 §10: sha256 over (relative posix path + NUL + bytes) for each
    pinned file in sorted order — gates.py, packager.py, redact.py and every prompt."""
    from harness import verify_pin

    root = _pin_repo(tmp_path / "repo")

    assert verify_pin.compute(root) == _expected_pin(root)


def test_b142_pinned_set_names_exactly_the_three_result_defining_modules():
    """B142 / RUN-DECISIONS-D2 §10: PINNED is gates.py, packager.py, redact.py (prompts/ is
    added by directory walk, not by listing)."""
    from harness import verify_pin

    assert tuple(verify_pin.PINNED) == (
        "harness/gates.py",
        "harness/packager.py",
        "harness/redact.py",
    )


def test_b142_one_changed_byte_in_a_prompt_changes_the_pin(tmp_path):
    """B142 (handoff §10.4): prompts are pinned data; a single byte moves the hash."""
    from harness import verify_pin

    root = _pin_repo(tmp_path / "repo")
    before = verify_pin.compute(root)
    prompt = root / "prompts" / "system.md"
    prompt.write_bytes(prompt.read_bytes()[:-1] + b"!")

    after = verify_pin.compute(root)

    assert after != before


def test_b142_one_changed_byte_in_gates_py_changes_the_pin(tmp_path):
    """B142 (handoff §10.4): the gate sequence is pinned; a single byte moves the hash."""
    from harness import verify_pin

    root = _pin_repo(tmp_path / "repo")
    before = verify_pin.compute(root)
    gates = root / "harness" / "gates.py"
    gates.write_bytes(gates.read_bytes() + b"\n")

    assert verify_pin.compute(root) != before


def test_b142_a_new_file_under_prompts_changes_the_pin(tmp_path):
    """B142 (handoff §10.4): every file under prompts/ is in the set, so adding one moves it."""
    from harness import verify_pin

    root = _pin_repo(tmp_path / "repo")
    before = verify_pin.compute(root)
    (root / "prompts" / "revise.md").write_text("# revise\n", encoding="utf-8", newline="\n")

    assert verify_pin.compute(root) != before


def test_b142_a_change_outside_the_pinned_set_does_not_change_the_pin(tmp_path):
    """B142 (handoff §10.4): only the pinned set is hashed; an unrelated module is not."""
    from harness import verify_pin

    root = _pin_repo(tmp_path / "repo")
    before = verify_pin.compute(root)
    (root / "harness" / "ledger.py").write_text("x = 1\n", encoding="utf-8", newline="\n")
    (root / "README.md").write_text("hello\n", encoding="utf-8", newline="\n")

    assert verify_pin.compute(root) == before


def test_b142_check_passes_when_the_pin_matches(tmp_path):
    """B142 (handoff §10.4): a matching .harness/PIN is accepted silently."""
    from harness import verify_pin

    root = _pin_repo(tmp_path / "repo")
    (root / ".harness" / "PIN").write_text(
        verify_pin.compute(root) + "\n", encoding="utf-8", newline="\n"
    )

    assert verify_pin.check(root) is None


def test_b142_check_reads_the_first_token_of_the_first_line(tmp_path):
    """B142 / RUN-DECISIONS-D2 §10: .harness/PIN is `<sha256> [anything]`, first line only."""
    from harness import verify_pin

    root = _pin_repo(tmp_path / "repo")
    (root / ".harness" / "PIN").write_text(
        verify_pin.compute(root) + "  harness/gates.py harness/packager.py\nsecond line\n",
        encoding="utf-8",
        newline="\n",
    )

    assert verify_pin.check(root) is None


def test_b142_check_raises_pin_mismatch_on_a_wrong_pin(tmp_path):
    """B142 (handoff §10.4): a wrong .harness/PIN is a startup failure, PinMismatch."""
    from harness import verify_pin
    from harness.errors import PinMismatch

    root = _pin_repo(tmp_path / "repo")
    (root / ".harness" / "PIN").write_text("0" * 64 + "\n", encoding="utf-8", newline="\n")

    with pytest.raises(PinMismatch):
        verify_pin.check(root)


def test_b142_check_raises_pin_mismatch_after_a_prompt_edit(tmp_path):
    """B142 (handoff §10.4): the pin that matched stops matching once a prompt changes."""
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
    """B142 / RUN-DECISIONS-D2 §1: PinMismatch derives from HarnessError."""
    from harness.errors import HarnessError, PinMismatch

    assert issubclass(PinMismatch, HarnessError)


def test_b142_main_print_emits_the_computed_hash(tmp_path, monkeypatch, capsys):
    """B142 / RUN-DECISIONS-D2 §10: `verify_pin --print` prints the hash and exits 0."""
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
    """B143 / RUN-DECISIONS-D2 R-E (handoff §10.4, D2-R11.7, R11.8): build_context adds exactly
    two roots — state/ and proposals/ — to Delivery 1's eight (runs, packages, the db parent,
    the halt file, and the package-root and cwd HUMAN.md/.env pairs); .harness/PIN and
    .harness/HALT are not roots and lie under none.

    Corrected at Delivery 2 reconcile (.fullsend/notes/test-corrections-d2.md): the count was
    written as 6 + 2; Delivery 1's `context.py` already registers eight."""
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
# D2-R1 — structural conformance (handoff §3)
# --------------------------------------------------------------------------------------


def test_a1_d2_file_map_is_complete_and_exclusive():
    """A1 / D2-R1.13 (handoff §3): every harness/**/*.py of the D2 file map exists and no
    other Python file exists under harness/."""
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
    """A1 (handoff §3): every tests/ file marked NEW ships."""
    missing = [rel for rel in D2_REQUIRED_TEST_FILES if not (REPO_ROOT / rel).is_file()]
    assert missing == [], "handoff §3 test files missing: " + ", ".join(missing)


def test_a1_d2_every_new_non_python_file_exists():
    """A1 / D2-R1.13 (handoff §3): every non-Python file marked NEW exists."""
    missing = [rel for rel in D2_REQUIRED_FILES if not (REPO_ROOT / rel).is_file()]
    assert missing == [], "handoff §3 files missing: " + ", ".join(missing)


def test_d2_r1_1_the_store_is_a_package_with_the_three_modules():
    """D2-R1.1 (handoff §2, §3): harness/store/{__init__,sqlite,github}.py exist."""
    store_dir = HARNESS_DIR / "store"
    assert store_dir.is_dir(), "harness/store/ must be a package"
    for name in ("__init__.py", "sqlite.py", "github.py"):
        assert (store_dir / name).is_file(), f"harness/store/{name} is required (D2-R1.1)"


def test_d2_r1_2_the_old_store_module_is_gone():
    """D2-R1.2 (handoff §3, §13 phase 1): the move was a move, not a copy."""
    assert not (HARNESS_DIR / "store.py").exists(), "harness/store.py must not exist (D2-R1.2)"


def test_d2_r1_3_the_four_new_modules_exist():
    """D2-R1.3 (handoff §3): dispatcher.py, ledger.py, keywords.py, trust.py."""
    present = sorted(
        name
        for name in ("dispatcher.py", "ledger.py", "keywords.py", "trust.py")
        if (HARNESS_DIR / name).is_file()
    )
    assert present == ["dispatcher.py", "keywords.py", "ledger.py", "trust.py"]


def test_d2_r1_4_the_three_new_stages_exist():
    """D2-R1.4 (handoff §3): stages/{revise,deliver,decompose}.py."""
    present = sorted(
        name
        for name in ("revise.py", "deliver.py", "decompose.py")
        if (HARNESS_DIR / "stages" / name).is_file()
    )
    assert present == ["decompose.py", "deliver.py", "revise.py"]


def test_d2_r1_5_exactly_six_workflow_files_with_the_specified_names():
    """D2-R1.5 (handoff §3, §7): six YAML files, exactly the named ones."""
    assert WORKFLOWS_DIR.is_dir(), ".github/workflows/ is required"
    found = sorted(p.name for p in WORKFLOWS_DIR.glob("*.yml"))
    assert found == sorted(ALL_WORKFLOWS), f"workflow set differs from handoff §7: {found}"
    assert list(WORKFLOWS_DIR.glob("*.yaml")) == [], "no .yaml files beside the six .yml"


def test_d2_r1_6_governance_files_exist():
    """D2-R1.6 (handoff §5.5): CODEOWNERS, .harness/trust.txt, .harness/config.json."""
    for rel in (".github/CODEOWNERS", ".harness/trust.txt", ".harness/config.json"):
        assert (REPO_ROOT / rel).is_file(), f"{rel} is required (D2-R1.6)"


def test_d2_r1_7_no_submodule_and_no_vendored_product_repo():
    """D2-R1.7 (handoff §3.1): no .gitmodules, no submodules/, no vendored product checkout."""
    assert not (REPO_ROOT / ".gitmodules").exists(), ".gitmodules must not exist"
    assert not (REPO_ROOT / "submodules").exists(), "submodules/ must not exist"
    assert not (REPO_ROOT / "brightboost").exists(), "no vendored copy of the product repo"


def test_d2_r1_8_the_two_new_prompt_files_exist():
    """D2-R1.8 (handoff §3, §4.6, §9): prompts/decompose.md and prompts/revise.md."""
    present = sorted(
        name for name in ("decompose.md", "revise.md") if (REPO_ROOT / "prompts" / name).is_file()
    )
    assert present == ["decompose.md", "revise.md"]


def test_d2_r1_9_the_five_local_mode_control_files_exist():
    """D2-R1.9 (handoff §3, §10.1): bb-start/stop/watcher.ps1, bb-configure.py, bb-config.json."""
    expected = ["bb-config.json", "bb-configure.py", "bb-start.ps1", "bb-stop.ps1", "bb-watcher.ps1"]
    found = sorted(
        p.name
        for p in REPO_ROOT.iterdir()
        if p.is_file() and (p.name.startswith("bb-") and p.suffix in (".ps1", ".py", ".json"))
    )
    assert found == expected, f"local control plane differs: {found}"


def test_d2_r1_10_local_has_at_least_seven_entries():
    """D2-R1.10 (handoff §3, §10): local/ carries the container plumbing (>= 7 entries)."""
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
    """D2-R1.11 / RUN-DECISIONS-D2 §15 (handoff §3.1, §10.1): .gitignore covers bb-work and runs."""
    gitignore = REPO_ROOT / ".gitignore"
    assert gitignore.is_file()
    lines = {ln.strip() for ln in _read(gitignore).splitlines()}
    assert lines & {"bb-work/", "bb-work", "/bb-work/", "/bb-work"}, (
        ".gitignore must ignore bb-work/ (D2-R1.11)"
    )
    assert lines & {"runs/", "runs", "/runs/", "/runs"}, ".gitignore must ignore runs/"


def test_d2_r1_12_pyproject_declares_no_runtime_dependency():
    """D2-R1.12 (handoff §3, I-17): `project.dependencies` is [] or absent."""
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"].get("dependencies", []) == []


def test_a44_watchdog_filename_and_content_avoid_the_rk_wildcard():
    """A44 (handoff §3, §10.1, D2-R9.12): local/watchdog-bb.ps1 exists and neither its name nor
    its content contains the substring `watchdog.ps1`."""
    path = REPO_ROOT / "local" / "watchdog-bb.ps1"
    assert path.is_file(), "local/watchdog-bb.ps1 is required"
    assert "watchdog.ps1" not in path.name
    assert "watchdog.ps1" not in path.read_text(encoding="utf-8", errors="replace")


def test_d2_state_ledger_ships_as_an_empty_window_starting_2026_09_07():
    """RUN-DECISIONS-D2 §15 (handoff §6.2): state/ledger.json = Ledger.empty("2026-09-07T00:00:00Z")."""
    path = REPO_ROOT / "state" / "ledger.json"
    assert path.is_file(), "state/ledger.json is required (handoff §3)"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema"] == 1
    assert payload["window"]["period_start"] == "2026-09-07T00:00:00Z"
    assert payload["window"]["spent_usd"] == 0
    assert payload["window"]["calls"] == 0
    assert payload["window"]["rate_limited_until"] is None
    assert payload["history"] == []
    assert isinstance(payload["observations"], dict)
    assert isinstance(payload["cursors"], dict)


def test_b112_harness_config_json_carries_exactly_the_eleven_knob_keys():
    """B112 / RUN-DECISIONS-D2 §2, §15 (handoff §5.5): .harness/config.json is an object whose
    keys are exactly the eleven operational knobs — nothing that alters what the harness concludes."""
    path = REPO_ROOT / ".harness" / "config.json"
    assert path.is_file(), ".harness/config.json is required"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    assert set(payload) == set(CONFIG_JSON_KEYS), f"keys differ from B112's knob set: {sorted(payload)}"


def test_d2_trust_file_lists_the_operator_and_codeowners_protects_the_governance_paths():
    """Handoff §5.5 (D2-R4.8): .harness/trust.txt names jgoetzmann; CODEOWNERS assigns
    /.harness/, /prompts/, /.github/, /harness/gates.py, /harness/redact.py and /proposals/."""
    trust = (REPO_ROOT / ".harness" / "trust.txt").read_text(encoding="utf-8")
    handles = [
        ln.strip().lower()
        for ln in trust.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    assert "jgoetzmann" in handles

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
# Workflow hygiene — handoff §7 (read-only, regex over the YAML text)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ALL_WORKFLOWS)
def test_b124_every_cron_minute_is_non_zero_and_not_a_quarter_hour(name):
    """B124 (handoff §7.1, D2-R7.1): every cron minute field is non-zero and non-round."""
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
    """B124 / RUN-DECISIONS-D2 §15 (handoff §7.1): the four crons are the frozen ones and the
    two event-driven workflows carry none."""
    crons = _cron_values(_d2_workflow(name))
    if name in FROZEN_CRONS:
        assert crons == [FROZEN_CRONS[name]], f"{name}: crons {crons} != {[FROZEN_CRONS[name]]}"
    else:
        assert crons == [], f"{name} must not be scheduled (handoff §7.1): {crons}"


@pytest.mark.parametrize("name", ALL_WORKFLOWS)
def test_b125_every_job_sets_a_timeout_of_at_most_120_minutes(name):
    """B125 (handoff §7.1, D2-R7.2): `timeout-minutes` on every `jobs.<id>`, <= 120."""
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
    """B126 (handoff §7.1, D2-R7.3): each spending workflow uploads runs/item-*/ under
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
    """B126 (handoff §7.1, D2-R7.3): wherever an artifact is uploaded, it is guarded."""
    text = _d2_workflow(name)
    always = re.compile(r"^\s*if:\s*(\$\{\{\s*)?always\(\)\s*(\}\})?\s*(#.*)?$", re.M)
    for block in _step_blocks(text):
        if "upload-artifact" in block:
            assert always.search(block), f"{name}: unguarded upload-artifact step:\n{block}"


@pytest.mark.parametrize("name", SPENDING_WORKFLOWS)
def test_b118_ledger_writers_share_one_concurrency_group_without_cancellation(name):
    """B118 (handoff §6.2, D2-R7.4): concurrency group harness-ledger, cancel-in-progress false."""
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
    """D2-R7.6 (handoff §7.2, RUN-DECISIONS-D2 §15): least privilege — a permissions block
    exists and `write-all` appears nowhere."""
    text = _d2_workflow(name)
    assert "write-all" not in text, f"{name}: write-all is not least privilege (D2-R7.6)"
    assert re.search(r"^\s*permissions:", text, re.M), f"{name}: no permissions block (D2-R7.6)"


def test_r7_8_pull_request_target_appears_in_no_workflow():
    """D2-R7.8 (RUN-DECISIONS-D2 §15): `pull_request_target` is absent from every workflow."""
    assert WORKFLOWS_DIR.is_dir()
    offenders = sorted(
        p.name
        for p in WORKFLOWS_DIR.iterdir()
        if p.is_file() and "pull_request_target" in p.read_text(encoding="utf-8", errors="replace")
    )
    assert offenders == [], "pull_request_target found in: " + ", ".join(offenders)


def test_b129_selftest_runs_the_fake_backend_suite_on_linux_and_windows():
    """B129 (handoff §7.3, D2-R7.7): selftest.yml triggers on pull_request, matrixes
    ubuntu-latest and windows-latest, runs pytest under BACKEND=fake."""
    text = _d2_workflow("selftest.yml")
    assert _has_pull_request_trigger(text), "selftest.yml must run on pull_request (B129)"
    assert re.search(r"^\s*matrix:", text, re.M), "selftest.yml must use a matrix (B129)"
    assert "ubuntu-latest" in text
    assert "windows-latest" in text
    assert re.search(r"BACKEND\s*[:=]\s*['\"]?fake", text), "BACKEND=fake is required (B129)"
    assert "pytest" in text


def test_b130_selftest_uses_no_secret_and_is_the_only_pull_request_workflow():
    """B130 (handoff §7.3, D2-R7.7): selftest.yml references no secret; no other workflow
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
    """B144 (handoff §11.1, D2-R7.10): heartbeat.yml comments on TRACKING_ISSUE weekly and
    never touches the Claude credential."""
    text = _d2_workflow("heartbeat.yml")
    assert "TRACKING_ISSUE" in text, "heartbeat.yml must reference TRACKING_ISSUE (B144)"
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in text, "the heartbeat spends nothing (B144)"
    assert re.search(r"harness\s+(run|propose|revise|decompose|implement)\b", text) is None
    assert _cron_values(text) == ["5 9 * * 1"]


def test_b145_ops_listens_to_completed_runs_of_the_three_spending_workflows():
    """B145 (handoff §11.2, D2-R7.9): ops.yml triggers on workflow_run completed and files
    harness:ops issues."""
    text = _d2_workflow("ops.yml")
    assert "workflow_run" in text, "ops.yml must trigger on workflow_run (B145)"
    assert "completed" in text
    assert "harness:ops" in text, "ops.yml must label its issues harness:ops (B145)"
    for workflow in ("discover", "implement", "feedback"):
        assert re.search(workflow, text, re.I), f"ops.yml must watch {workflow} (B145)"


def test_b146_ops_names_the_step_kinds_it_never_retries():
    """B146 (handoff §11.2, D2-R7.9): the retry exclusion list names revise, propose and gate
    steps — a red gate is information, not a transient."""
    text = _d2_workflow("ops.yml")
    for word in ("revise", "propose", "gate"):
        assert re.search(word, text, re.I), f"ops.yml never-retry rule must name {word!r} (B146)"


def test_b105_no_workflow_pushes_a_workflow_file_to_the_fork():
    """B105 / D2-R7.12 (handoff §4.4, §7): no push whose target is the fork carries a .github
    path; the fork's default branch only ever moves by fast-forward."""
    fork = "brightboost-harness/brightboost"
    violations: list[str] = []
    for name in ALL_WORKFLOWS:
        for lineno, line in enumerate(_d2_workflow(name).splitlines(), start=1):
            if "git push" in line and fork in line and ".github" in line:
                violations.append(f"{name}:{lineno}")
            if fork in line and ".github/workflows" in line:
                violations.append(f"{name}:{lineno}")
    assert violations == [], "B105 violated: " + ", ".join(violations)


def test_b127_implement_yml_orders_halt_doctor_sync_fork_dispatch_then_work():
    """B127 / B150 / D2-R7.5 (handoff §7.2, §11.4): HALT check → doctor → sync-fork →
    dispatch → run, in that order."""
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
    """B127 / B128 (handoff §7.2): push to main on proposals/** is the approval trigger,
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


@pytest.mark.parametrize("name", SPENDING_WORKFLOWS)
def test_b149_every_spending_workflow_checks_repo_halt_before_doctor_and_dispatch(name):
    """B149 / B150 / A43 (handoff §11.4, D2-R6.16): the .harness/HALT check precedes doctor
    and the dispatcher, logs why, and exits 0."""
    text = _d2_workflow(name)
    halt = _first_line_index(text, r"\.harness/HALT")
    assert halt is not None, f"{name}: no .harness/HALT check (B149)"
    assert "halted by .harness/HALT" in text, f"{name}: the halt step must log why (B149)"
    assert re.search(r"\.harness/HALT.*exit 0|exit 0.*\.harness/HALT", text, re.S), (
        f"{name}: the halt step must exit 0 (B149)"
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
    """B127 (handoff §7.2, RUN-DECISIONS-D2 §15): doctor precedes dispatch in every spending job."""
    text = _d2_workflow(name)
    doctor = _first_line_index(text, r"harness\s+doctor\b")
    dispatch = _first_line_index(text, r"harness\s+dispatch\b")
    assert doctor is not None, f"{name}: harness doctor is required (B127)"
    assert dispatch is not None, f"{name}: harness dispatch is required (B122)"
    assert doctor < dispatch, f"{name}: doctor must run before dispatch (B127)"


# --------------------------------------------------------------------------------------
# B113 / B134 — properties that live in governance and documentation, checked as text
# --------------------------------------------------------------------------------------


def test_b113_branch_protection_is_a_named_human_prerequisite_and_codeowners_covers_governance():
    """B113: main requires one approving review and no force-push; the harness's own proposal PRs are
    subject to it. That is a repository setting a human applies (HUMAN.md item 9), and CODEOWNERS
    keeps the governance files behind review."""
    human = (REPO_ROOT / "HUMAN.md").read_text(encoding="utf-8").lower()
    assert "branch protection" in human and "force-push" in human.replace("force push", "force-push")
    owners = (REPO_ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
    for path in ("/.harness/", "/prompts/", "/.github/", "/harness/gates.py", "/harness/redact.py"):
        assert path in owners, f"CODEOWNERS must cover {path}"


def test_b134_operations_doc_states_the_event_driven_versus_polled_asymmetry():
    """B134: commands on this repository are event-driven; on the product repository they are found by
    the sweep with latency NOTIFY_POLL_HOURS. docs/OPERATIONS.md must say so, or it reads as a bug."""
    ops = (REPO_ROOT / "docs" / "OPERATIONS.md").read_text(encoding="utf-8")
    assert "NOTIFY_POLL_HOURS" in ops
    lowered = ops.lower()
    assert "event" in lowered and ("poll" in lowered or "sweep" in lowered)


def test_b149_the_repo_level_halt_file_is_committable_while_the_root_halt_stays_ignored():
    """B149: `.harness/HALT` is a one-line commit on the default branch, so it must not be caught by
    Delivery 1's `HALT` ignore line (I-6), which is for the local scratch kill file only."""
    import subprocess

    def ignored(path: str) -> bool:
        return subprocess.run(["git", "check-ignore", "-q", path], cwd=REPO_ROOT).returncode == 0

    assert ignored("HALT"), "the root HALT must stay ignored (I-6)"
    assert not ignored(".harness/HALT"), ".harness/HALT must be committable (B149)"
