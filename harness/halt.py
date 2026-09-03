"""The two kill switches: the Delivery 1 ``HALT_FILE`` (B148) and ``.harness/HALT`` (B149/B150)."""

from __future__ import annotations

from pathlib import Path

from harness.errors import Halted, RepoHalted
from harness.redact import guarded_write

__all__ = [
    "halted",
    "check_halt",
    "engage",
    "disengage",
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


def repo_halted(repo_root: Path) -> bool:
    """True when ``.harness/HALT`` exists under ``repo_root`` (B149); never written here."""
    return (Path(repo_root) / ".harness" / "HALT").exists()


def check_repo_halt(repo_root: Path) -> None:
    """Raise :class:`RepoHalted` when ``.harness/HALT`` exists; otherwise return (B150)."""
    if repo_halted(repo_root):
        raise RepoHalted(REPO_HALT_MESSAGE)
