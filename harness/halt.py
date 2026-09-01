"""The kill switch.

A single file whose mere existence stops the harness. Checked at every stage
boundary so an operator can stop an overnight run with `harness halt` (or by
touching the file by hand) without racing the process.
"""

from __future__ import annotations

from pathlib import Path

from harness.errors import Halted
from harness.redact import guarded_write

__all__ = ["halted", "check_halt", "engage", "disengage"]

HALT_TEXT = "halted by the operator\n"


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
