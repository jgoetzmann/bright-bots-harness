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
    "harness/store.py",
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
        if _rel(path) == "harness/store.py":
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
