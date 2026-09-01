"""Stage registry and prompt loading.

A stage is a callable taking a :class:`harness.context.Context` plus stage-specific arguments.
Prompts are data: they live in ``prompts/`` as Markdown and are rendered with a strict
``string.Template`` substitution so a stray brace in repository content cannot break rendering.
"""

from __future__ import annotations

import string
from pathlib import Path
from typing import Any, Callable

from harness.context import Context
from harness.halt import check_halt
from harness.runner import RunRequest, RunResult

__all__ = [
    "PROMPTS_DIR",
    "STAGES",
    "StageFn",
    "load_prompt",
    "run_model",
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


def run_model(
    ctx: Context,
    *,
    stage: str,
    item_id: int | None,
    prompt: str,
    allowed_tools: tuple[str, ...],
    disallowed_tools: tuple[str, ...],
    timeout_s: int,
    cwd: Path,
    add_dirs: tuple[Path, ...] = (),
) -> RunResult:
    """One model call with its bookkeeping: halt check, authorization, ``stage_run`` row,
    transcript, governor record. ``item_id=None`` (triage has no work item yet) opens no row.
    """
    check_halt(ctx.config.halt_file)
    auth = ctx.governor.authorize(item_id or 0, stage)
    ctx.run_dir.mkdir(parents=True, exist_ok=True)
    run_id = None
    if item_id is not None:
        run_id = ctx.store.start_stage_run(item_id, stage, ctx.config.backend)
    request = RunRequest(
        stage=stage,
        prompt=prompt,
        system_prompt=system_prompt(),
        allowed_tools=allowed_tools,
        disallowed_tools=disallowed_tools,
        max_turns=auth.max_turns,
        cwd=cwd,
        timeout_s=timeout_s,
        add_dirs=add_dirs,
    )
    result = ctx.runner.run(request)
    transcript_path = ctx.write_transcript(stage, result.transcript)
    allowance = result.allowance_pct
    if allowance is None:
        allowance = ctx.governor.estimate(stage)
    if run_id is not None:
        ctx.store.finish_stage_run(
            run_id,
            status="ok" if result.ok else "failed",
            turns=result.turns,
            allowance_pct=allowance,
            cost_usd=result.cost_usd,
            exit_reason=result.error,
            transcript_path=str(transcript_path),
        )
    ctx.governor.record(auth, allowance_pct=allowance, cost_usd=result.cost_usd)
    return result


# Imported after ``load_prompt`` and ``run_model`` are defined: the stage modules import them
# back out of this partially-initialised package. Imported as modules, not functions, so that
# ``harness.stages.implement`` stays the module whose injectables the tests monkeypatch.
from harness.stages import discover, implement, package, propose  # noqa: E402

STAGES: dict[str, StageFn] = {
    "discover": discover.discover,
    "propose": propose.propose,
    "implement": implement.implement,
    "package": package.package,
}
