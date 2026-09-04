"""The store seam: ``Store`` (the SQLite class), ``StoreProtocol``, ``GitHubStore``,
``open_store``."""

from __future__ import annotations

from pathlib import Path

from harness import trust
from typing import Any, Protocol

from harness.errors import StoreError
from harness.store.github import GitHubStore
from harness.store.sqlite import (
    LABELS,
    STATES,
    TRANSITIONS,
    SqliteStore,
    StageRun,
    WorkItem,
)
from harness.store.sqlite import SqliteStore as Store


class StoreProtocol(Protocol):
    """Every public method of :class:`SqliteStore`, plus the three Delivery 2 seam methods."""

    def migrate(self) -> None: ...

    def close(self) -> None: ...

    def create_work_item(
        self,
        *,
        kind: str,
        external_ref: str,
        title: str,
        tier_required: int = 0,
        body: str = "",
        upstream_body: str = "",
    ) -> int: ...

    def get_work_item(self, item_id: int) -> WorkItem | None: ...

    def find_by_ref(self, external_ref: str) -> WorkItem | None: ...

    def list_work_items(self, *, state: str | None = None) -> list[WorkItem]: ...

    def transition(self, item_id: int, to_state: str, *, reason: str) -> None: ...

    def update_work_item(self, item_id: int, **fields: object) -> None: ...

    def start_stage_run(self, work_item_id: int, stage: str, backend: str) -> int: ...

    def finish_stage_run(
        self,
        run_id: int,
        *,
        status: str,
        turns: int | None,
        allowance_pct: float | None,
        cost_usd: float | None,
        exit_reason: str | None,
        transcript_path: str | None,
    ) -> None: ...

    def list_stage_runs(
        self, work_item_id: int | None = None, status: str | None = None
    ) -> list[StageRun]: ...

    def completed_allowances(self, stage: str) -> list[float]: ...

    def append_event(self, work_item_id: int | None, level: str, message: str) -> None: ...

    def events(self, work_item_id: int | None = None) -> list[dict]: ...

    def ensure_budget_period(
        self, unit: str, period_start: str, period_end: str, allocated: float
    ) -> None: ...

    def budget_period(self, unit: str, period_start: str) -> tuple[float, float]: ...

    def consume_budget(self, unit: str, period_start: str, amount: float) -> None: ...

    def cache_get(self, url: str) -> tuple[str | None, str] | None: ...

    def cache_put(self, url: str, etag: str | None, body: str) -> None: ...

    def record_api_call(self, url: str, status: int, cached: bool) -> None: ...

    def api_calls_since(self, iso_ts: str) -> int: ...

    # Delivery 2 (RUN-DECISIONS-D2 section 3)

    def publish_proposal(self, item_id: int, filename: str, text: str) -> str: ...

    def merged_issues(self) -> set[int]: ...

    def reconcile_stale_running(self, older_than_iso: str) -> list[int]: ...


def open_store(config: Any, clock: Any, gh: Any = None) -> StoreProtocol:
    """``sqlite`` -> a migrated :class:`SqliteStore`; ``github`` -> :class:`GitHubStore` over it."""
    repo_root = getattr(config, "repo_root", None)
    proposals_dir = Path(repo_root) / "proposals" if repo_root is not None else None
    scratch = SqliteStore(Path(config.db_path), clock, proposals_dir=proposals_dir)
    scratch.migrate()
    if getattr(config, "store_backend", "sqlite") != "github":
        return scratch
    if gh is None:
        scratch.close()
        raise StoreError("STORE_BACKEND=github needs a GitHub client (gh=None)")
    return GitHubStore(
        gh,
        self_repo=str(getattr(config, "self_repo", "")),
        scratch=scratch,
        clock=clock,
        config=config,
        trusted=trust.load_trust(Path(config.trust_file)) if getattr(config, "trust_file", "") else (),
    )


__all__ = [
    "GitHubStore",
    "LABELS",
    "STATES",
    "SqliteStore",
    "StageRun",
    "Store",
    "StoreProtocol",
    "TRANSITIONS",
    "WorkItem",
    "open_store",
]
