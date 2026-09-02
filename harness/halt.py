"""The kill switch: a file whose existence stops the harness at the next stage boundary.

Two switches exist. The Delivery 1 ``HALT_FILE`` (B148) is checked at stage boundaries and maps
to exit 5. The Delivery 2 ``.harness/HALT`` (B149/B150) lives on the repository's default branch,
is checked FIRST by every spending command, and maps to exit 0 with a message on stdout.
"""

from __future__ import annotations

from pathlib import Path

from harness.errors import Halted, RepoHalted
from harness.redact import guarded_write

__all__ = [
    "halted",
    "check_halt",
    "engage",
    "disengage",
    "repo_halt_path",
    "repo_halted",
    "check_repo_halt",
    "REPO_HALT_MESSAGE",
]

HALT_TEXT = "halted by the operator\n"

#: The exact stdout line the CLI prints when ``.harness/HALT`` stops a spending command (B149).
REPO_HALT_MESSAGE = "halted by .harness/HALT"


def halted(halt_file: Path) -> bool:
    """True when the halt file exists."""
    return Path(halt_file).exists()


def check_halt(halt_file: Path) -> None:
    """Raise :class:`Halted` when the kill switch is engaged; otherwise return."""
    path = Path(halt_file)
    if path.exists():
        raise Halted(f"halt file present at {path}")


def engage(halt_file: Path) -> None:
    """Create the halt file. Idempotent: an existing halt stays halted."""
    guarded_write(Path(halt_file), HALT_TEXT)


def disengage(halt_file: Path) -> None:
    """Remove the halt file. Idempotent: a missing halt file is already disengaged."""
    Path(halt_file).unlink(missing_ok=True)


def repo_halt_path(repo_root: Path) -> Path:
    """``<repo_root>/.harness/HALT`` — outside ``redact.allowed_roots()``, never written here."""
    return Path(repo_root) / ".harness" / "HALT"


def repo_halted(repo_root: Path) -> bool:
    """True when ``.harness/HALT`` exists under ``repo_root`` (B149)."""
    return repo_halt_path(repo_root).exists()


def check_repo_halt(repo_root: Path) -> None:
    """Raise :class:`RepoHalted` when ``.harness/HALT`` exists; otherwise return (B150)."""
    if repo_halted(repo_root):
        raise RepoHalted(REPO_HALT_MESSAGE)
