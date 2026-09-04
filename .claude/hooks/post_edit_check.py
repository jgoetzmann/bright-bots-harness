#!/usr/bin/env python3
"""PostToolUse(Write|Edit) guard for this repository.

Reads the hook payload on stdin and prints one JSON object carrying a ``systemMessage`` when the
edit needs attention; prints nothing and exits 0 otherwise. Standard library only, like the package
it guards, and it never blocks: an edit that trips a check is reported, not rejected.

Two checks:

1. **House style** — no line over 100 columns, and the file still compiles (Python files only).
2. **The pin** — ``harness/gates.py``, ``harness/packager.py``, ``harness/redact.py`` and every
   ``prompts/`` file are content-hashed into ``.harness/PIN`` (Delivery 2, B142). Editing one
   invalidates the hash, and ``harness doctor``, ``implement.yml`` and the ``bb`` container all
   fail closed on a mismatch, so the hook says so the moment it happens rather than at the next run.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

WATCHED = ("harness/", "tests/", "prompts/", "local/", "bb-configure.py")
PINNED = ("harness/gates.py", "harness/packager.py", "harness/redact.py", "prompts/")
MAX_COLUMNS = 100


def _relative(path: Path, root: Path) -> str | None:
    """The file as a repo-relative posix path, or None when it is outside the repository."""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    raw = payload.get("tool_response", {}).get("filePath") or payload.get("tool_input", {}).get(
        "file_path"
    )
    if not raw:
        return 0

    root = Path(__file__).resolve().parent.parent.parent
    target = Path(str(raw))
    rel = _relative(target, root)
    if rel is None or not target.is_file():
        return 0
    if not any(rel == watched or rel.startswith(watched) for watched in WATCHED):
        return 0

    notes: list[str] = []
    if rel.endswith(".py"):
        text = target.read_text(encoding="utf-8", errors="replace")
        long_lines = [n for n, line in enumerate(text.splitlines(), 1) if len(line) > MAX_COLUMNS]
        if long_lines:
            listed = ", ".join(str(n) for n in long_lines[:8])
            more = f" (+{len(long_lines) - 8} more)" if len(long_lines) > 8 else ""
            notes.append(f"{rel}: line(s) over the {MAX_COLUMNS}-column house limit: {listed}{more}")
        compiled = subprocess.run(
            [sys.executable, "-m", "py_compile", str(target)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if compiled.returncode != 0:
            tail = (compiled.stderr or compiled.stdout or "").strip().splitlines()
            notes.append(f"{rel} does not compile: {tail[-1] if tail else 'unknown error'}")

    if any(rel == pin or rel.startswith(pin) for pin in PINNED):
        checked = subprocess.run(
            [sys.executable, "-m", "harness.verify_pin", "--check"],
            capture_output=True,
            text=True,
            cwd=str(root),
            encoding="utf-8",
            errors="replace",
        )
        if checked.returncode != 0:
            notes.append(
                f"{rel} is pinned (B142) and .harness/PIN no longer matches. Run "
                "`python -m harness.verify_pin --write`, read the new hash, and commit it: "
                "doctor, implement.yml and the bb container fail closed on a mismatch, and the "
                "PIN is CODEOWNERS-protected (B143)."
            )

    if notes:
        json.dump({"systemMessage": "\n".join(notes)}, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
