"""Stage registry and prompt loading.

A stage is a callable taking a :class:`harness.context.Context` plus stage-specific arguments.
Prompts are data: they live in ``prompts/`` as Markdown and are rendered with a strict
``string.Template`` substitution so a stray brace in repository content cannot break rendering.
"""

from __future__ import annotations

import string
from pathlib import Path
from typing import Any, Callable

__all__ = [
    "PROMPTS_DIR",
    "STAGES",
    "StageFn",
    "discover",
    "implement",
    "load_prompt",
    "package",
    "propose",
    "system_prompt",
]

StageFn = Callable[..., Any]

#: ``<repo root>/prompts`` — this file is ``<repo root>/harness/stages/__init__.py``.
PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"


def load_prompt(name: str) -> string.Template:
    """Load ``prompts/<name>.md`` as a :class:`string.Template`.

    The template is never cached: prompts are data, and editing one during a run is expected to
    take effect on the next call rather than requiring a restart.
    """
    path = PROMPTS_DIR / f"{name}.md"
    text = path.read_text(encoding="utf-8")
    return string.Template(text)


def system_prompt() -> str:
    """The shared system prompt, verbatim. Never substituted."""
    return load_prompt("system").template


# Imported after ``load_prompt`` is defined: the stage modules import it back out of this
# partially-initialised package, which works only because the name already exists by now.
from harness.stages.discover import discover  # noqa: E402
from harness.stages.implement import implement  # noqa: E402
from harness.stages.package import package  # noqa: E402
from harness.stages.propose import propose  # noqa: E402

STAGES: dict[str, StageFn] = {
    "discover": discover,
    "propose": propose,
    "implement": implement,
    "package": package,
}
