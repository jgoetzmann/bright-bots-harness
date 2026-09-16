"""The :class:`Context` handed to every stage, and the wiring that builds one."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

import harness
from harness import ledger as ledger_module
from harness import redact
from harness.clock import Clock, SystemClock, iso
from harness.clone import CloneManager
from harness.config import Config
from harness import halt as halt_module
from harness.errors import Halted
from harness.gh import GitHubReadOnly, build_client
from harness.governor import Governor
from harness.ledger import Ledger
from harness.runner import Runner, get_runner
from harness.store import Store, open_store
from harness.trust import Trust, load_trust, parse_trust

__all__ = ["Context", "build_context", "repo_root", "ledger_path_for"]

#: The run id used by stages that are not scoped to a single work item.
DEFAULT_RUN_ID = "discover"


def repo_root() -> Path:
    """The repository root: the directory containing the `harness` package."""
    return Path(harness.__file__).resolve().parent.parent


def ledger_path_for(config: Config) -> Path:
    """``<config.repo_root>/state/ledger.json`` — the only place the ledger is persisted."""
    return Path(config.repo_root) / "state" / "ledger.json"


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
    ledger: Ledger | None = None
    ledger_path: Path | None = None
    #: The trust file with its levels; it still behaves as a set of handles (B269).
    trusted: Trust = field(default_factory=Trust)

    @property
    def run_dir(self) -> Path:
        """`runs/<run-id>/` — every artifact this run produces lives under here."""
        return self.config.runs_dir / self.run_id

    def check_halt(self) -> None:
        """Both switches a running stage has to honour, in one call.

        The halt file (B148) and the commanded halt set by `/harness halt`. They live together
        on the context so that no call site can honour one and miss the other, which would
        tell the operator everything stopped when it had not.
        """
        halt_module.check_halt(self.config.halt_file)
        commanded = self.ledger.halt_request()
        if commanded is not None:
            why = f": {commanded['reason']}" if commanded.get("reason") else ""
            raise Halted(
                f"halted by @{commanded.get('by', 'someone')} at "
                f"{commanded.get('at', 'unknown')}{why}. `/harness resume` lifts it."
            )

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
        lines = [
            json.dumps(dict(entry), ensure_ascii=False, sort_keys=True) for entry in transcript
        ]
        body = "".join(f"{line}\n" for line in lines)
        redact.write_redacted(path, body)
        return path

    def save_ledger(self) -> Path:
        """Persist the ledger to ``ledger_path`` (temp file + ``os.replace``, B115)."""
        assert self.ledger is not None and self.ledger_path is not None
        ledger_module.save(self.ledger, self.ledger_path)
        return self.ledger_path


def build_context(
    config: Config,
    *,
    run_id: str | None = None,
    runner: Runner | None = None,
    gh: GitHubReadOnly | None = None,
    clock: Clock | None = None,
    store: Store | None = None,
    clones: CloneManager | None = None,
    ledger: Ledger | None = None,
    trusted: Iterable[str] | None = None,
) -> Context:
    """Wire a :class:`Context`, arming the write guard before anything can write."""
    the_clock: Clock = clock if clock is not None else SystemClock()

    scratch: Store = store if store is not None else Store(config.db_path, the_clock)
    scratch.migrate()

    the_gh = gh if gh is not None else build_client(config, scratch, the_clock)

    if store is not None or config.store_backend == "sqlite":
        the_store: Store = scratch
    else:
        the_store = open_store(config, the_clock, gh=the_gh)
        the_store.migrate()

    ledger_path = ledger_path_for(config)
    the_ledger: Ledger = ledger if ledger is not None else ledger_module.load(ledger_path)
    # A `Trust` is kept whole so its levels survive; a bare iterable of handles becomes level-1
    # entries, the least the file could have meant (B269).
    the_trusted: Trust = (
        (
            trusted
            if isinstance(trusted, Trust)
            else parse_trust(chr(10).join(str(h) for h in trusted))
        )
        if trusted is not None
        else load_trust(config.trust_file)
    )

    governor = Governor(config, the_clock, the_ledger)
    the_runner: Runner = runner if runner is not None else get_runner(config)
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
            # `state/` and `proposals/`; `.harness/` is never a write root (B143).
            Path(config.repo_root) / "state",
            Path(config.repo_root) / "proposals",
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
        ledger=the_ledger,
        ledger_path=ledger_path,
        trusted=the_trusted,
    )
