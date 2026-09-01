"""The :class:`Context` handed to every stage, and the wiring that builds one."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

import harness
from harness import redact
from harness.clock import Clock, SystemClock, iso
from harness.clone import CloneManager
from harness.config import Config
from harness.gh import GitHubReadOnly
from harness.governor import Governor
from harness.runner import Runner, get_runner
from harness.store import Store

__all__ = ["Context", "build_context", "repo_root"]

#: The run id used by stages that are not scoped to a single work item.
DEFAULT_RUN_ID = "discover"


def repo_root() -> Path:
    """The repository root: the directory containing the `harness` package."""
    return Path(harness.__file__).resolve().parent.parent


@dataclass
class Context:
    """Everything a stage is allowed to reach, assembled once per invocation."""

    config: Config
    store: Store
    governor: Governor
    runner: Runner
    gh: GitHubReadOnly
    clones: CloneManager
    clock: Clock
    run_id: str

    @property
    def run_dir(self) -> Path:
        """`runs/<run-id>/` — every artifact this run produces lives under here."""
        return self.config.runs_dir / self.run_id

    def record_decision(self, text: str) -> None:
        """Append one dated line to the run's `DECISIONS.md`, redacted on the way out."""
        path = self.run_dir / "DECISIONS.md"
        line = f"- {iso(self.clock.now())} {text}\n"
        existing = ""
        if path.exists():
            existing = path.read_text(encoding="utf-8")
        redact.write_redacted(path, existing + line)

    def write_transcript(self, stage: str, transcript: Iterable[Mapping[str, object]]) -> Path:
        """Write `transcript/<stage>.jsonl`, redacted, and return the path."""
        path = self.run_dir / "transcript" / f"{stage}.jsonl"
        lines = [json.dumps(dict(entry), ensure_ascii=False, sort_keys=True) for entry in transcript]
        body = "".join(f"{line}\n" for line in lines)
        redact.write_redacted(path, body)
        return path


def build_context(
    config: Config,
    *,
    run_id: str | None = None,
    runner: Runner | None = None,
    gh: GitHubReadOnly | None = None,
    clock: Clock | None = None,
    store: Store | None = None,
    clones: CloneManager | None = None,
) -> Context:
    """Wire a :class:`Context`, arming the write guard before anything can write."""
    the_clock: Clock = clock if clock is not None else SystemClock()

    the_store = store if store is not None else Store(config.db_path, the_clock)
    the_store.migrate()

    governor = Governor(the_store, config, the_clock)
    the_runner: Runner = runner if runner is not None else get_runner(config)
    the_gh = (
        gh
        if gh is not None
        else GitHubReadOnly(
            config.repo, the_store, the_clock, config.github_api_ceiling_per_hour
        )
    )
    the_clones = clones if clones is not None else CloneManager(config, the_clock)

    root = repo_root()
    redact.set_write_roots(
        [
            config.runs_dir,
            config.packages_dir,
            config.db_path.parent,
            config.halt_file,
            root / "HUMAN.md",
            root / ".env",
            # The CLI treats its cwd as the repository root (setup writes HUMAN.md there).
            Path.cwd() / "HUMAN.md",
            Path.cwd() / ".env",
        ]
    )

    return Context(
        config=config,
        store=the_store,
        governor=governor,
        runner=the_runner,
        gh=the_gh,
        clones=the_clones,
        clock=the_clock,
        run_id=run_id if run_id is not None else DEFAULT_RUN_ID,
    )
