"""The pin over what the harness checks and what it refuses to say (handoff §10.4, B142/B143).

``python -m harness.verify_pin --check`` recomputes a SHA-256 over the pinned set in sorted path
order and compares it with the first token of the first line of ``.harness/PIN``. The pinned set
is ``harness/gates.py``, ``harness/packager.py``, ``harness/redact.py`` and every file under
``prompts/``. Mismatch is a startup failure in both modes.

``--write`` is the orchestrator's tool. It is the only branch in this module that writes, it uses a
plain ``open`` rather than a harness write path, and ``.harness/PIN`` is deliberately outside
``redact.allowed_roots()`` so the harness cannot re-pin itself (B143).
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Sequence

from harness.errors import PinMismatch

__all__ = [
    "PINNED",
    "PROMPTS_DIR",
    "PIN_RELATIVE",
    "default_repo_root",
    "pinned_files",
    "compute",
    "read_pin",
    "check",
    "main",
]

#: The code files in the pinned set; every file under :data:`PROMPTS_DIR` joins them.
PINNED: tuple[str, ...] = ("harness/gates.py", "harness/packager.py", "harness/redact.py")

#: Directory whose every file (recursively) is part of the pinned set.
PROMPTS_DIR: str = "prompts"

#: Where the pin lives, relative to the repository root.
PIN_RELATIVE: str = ".harness/PIN"


def default_repo_root() -> Path:
    """The repository root: the directory containing the ``harness`` package."""
    return Path(__file__).resolve().parent.parent


def pinned_files(repo_root: Path) -> list[str]:
    """Relative POSIX paths of the pinned set under ``repo_root``, sorted."""
    root = Path(repo_root)
    paths: set[str] = set(PINNED)
    prompts = root / PROMPTS_DIR
    if prompts.is_dir():
        for candidate in prompts.rglob("*"):
            if candidate.is_file():
                paths.add(candidate.relative_to(root).as_posix())
    return sorted(paths)


def compute(repo_root: Path) -> str:
    """SHA-256 hex over ``path + "\0" + bytes`` for each pinned file in sorted path order."""
    root = Path(repo_root)
    digest = hashlib.sha256()
    for relative in pinned_files(root):
        path = root / relative
        if not path.is_file():
            raise PinMismatch(f"pinned file missing: {relative}")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def read_pin(repo_root: Path) -> str:
    """The recorded pin: the first token of the first line of ``.harness/PIN``."""
    path = Path(repo_root) / PIN_RELATIVE
    if not path.is_file():
        raise PinMismatch(f"no pin file at {path}")
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    tokens = lines[0].split() if lines else []
    if not tokens:
        raise PinMismatch(f"pin file {path} is empty")
    return tokens[0]


def check(repo_root: Path) -> None:
    """Raise :class:`PinMismatch` unless the computed hash equals the recorded pin (B142)."""
    expected = read_pin(repo_root)
    actual = compute(repo_root)
    if actual != expected:
        raise PinMismatch(f"pin mismatch: .harness/PIN has {expected}, computed {actual}")


def _write_pin(repo_root: Path) -> Path:
    """Write ``.harness/PIN`` with the current hash. Reached only through ``--write``."""
    root = Path(repo_root)
    digest = compute(root)
    path = root / PIN_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    # Orchestrator-only: a plain open, on purpose, outside every harness write root (B143).
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(f"{digest}\n")
    return path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m harness.verify_pin",
        description="Verify, print, or (orchestrator only) write the harness pin.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--check",
        action="store_true",
        help="recompute the hash and compare it with .harness/PIN (default; exit 1 on mismatch)",
    )
    group.add_argument(
        "--print",
        action="store_true",
        dest="print_hash",
        help="print the computed hash and exit 0",
    )
    group.add_argument(
        "--write",
        action="store_true",
        help="write the computed hash to .harness/PIN (for the orchestrator only)",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="repository root (default: the directory containing the harness package)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: ``--check`` (default), ``--print``, or ``--write``; returns the exit code."""
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    root = Path(args.repo_root) if args.repo_root is not None else default_repo_root()
    try:
        if args.print_hash:
            print(compute(root))
            return 0
        if args.write:
            path = _write_pin(root)
            print(f"wrote {path}: {read_pin(root)}")
            return 0
        check(root)
        print(f"pin ok: {read_pin(root)}")
        return 0
    except PinMismatch as exc:
        print(f"pin check failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
