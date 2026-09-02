"""The :class:`Context` handed to every stage, and the wiring that builds one.

Delivery 2 adds the ledger (``state/ledger.json``), the trusted-handle set (``.harness/trust.txt``),
the store seam (``harness.store.open_store``) and the authenticated GitHub client built by
``harness.gh.build_client`` — which reads the token door itself, so this module never does (I-11).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

import harness
from harness import ledger as ledger_module
from harness import redact
from harness.clock import Clock, SystemClock, iso
from harness.clone import CloneManager
from harness.config import Config
from harness.gh import GitHubReadOnly, build_client
from harness.governor import Governor
from harness.ledger import Ledger
from harness.runner import Runner, get_runner
from harness.store import Store, open_store
from harness.trust import load_trust

__all__ = ["Context", "build_context", "repo_root", "ledger_path_for"]

#: The run id used by stages that are not scoped to a single work item.
DEFAULT_RUN_ID = "discover"

#: The ``period_start`` of a ledger that has never been persisted (RUN-DECISIONS-D2 §4).
EPOCH_ISO = "1970-01-01T00:00:00Z"


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
    trusted: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        # Delivery 1 callers construct Context without the three Delivery 2 fields; give them
        # an empty ledger at the configured path so ``save_ledger`` and the stages still work.
        if self.ledger is None:
            self.ledger = Ledger.empty(EPOCH_ISO)
        if self.ledger_path is None:
            self.ledger_path = ledger_path_for(self.config)
        self.trusted = frozenset(handle.lower() for handle in self.trusted)

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
    """Wire a :class:`Context`, arming the write guard before anything can write.

    The GitHub client is built first, on the sqlite scratch store (it needs a cache and the
    api-call ledger); the GitHub-backed store, when configured, is then built on that client.
    With ``store`` injected, or ``STORE_BACKEND=sqlite``, the scratch store IS the store — the
    Delivery 1 wiring, unchanged.
    """
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
    the_trusted: frozenset[str] = (
        frozenset(handle.lower() for handle in trusted)
        if trusted is not None
        else load_trust(config.trust_file)
    )

    governor = Governor(the_store, config, the_clock, ledger=the_ledger)
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
            # Delivery 2 (R-E): exactly these two. `.harness/` is deliberately NOT a root (B143).
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
