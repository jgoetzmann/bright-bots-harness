"""SQLite persistence for the harness: schema, migrations and every query.

Invariant I-5: no SQL string exists anywhere in the package outside this module.
Every timestamp written here is produced by ``harness.clock.iso`` so a frozen clock
in tests yields byte-stable rows.
"""

from __future__ import annotations

import math
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from harness.clock import Clock, SystemClock, iso
from harness.errors import DuplicateWorkItem, IllegalTransition, StoreError

SCHEMA_VERSION = 1

# Verbatim from HARNESS-SPEC.md 5.2.1. Do not reformat.
SCHEMA_SQL = """
CREATE TABLE schema_version (version INTEGER NOT NULL);

CREATE TABLE work_item (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  kind          TEXT    NOT NULL CHECK (kind IN ('issue','pr','audit_finding')),
  external_ref  TEXT    NOT NULL,
  title         TEXT    NOT NULL,
  state         TEXT    NOT NULL CHECK (state IN
                  ('discovered','proposed','approved','implementing',
                   'packaged','shipped','blocked','abandoned')),
  parent_id     INTEGER REFERENCES work_item(id),
  depends_on    INTEGER REFERENCES work_item(id),
  tier_required INTEGER NOT NULL DEFAULT 0,
  spec_path     TEXT,
  package_path  TEXT,
  base_sha      TEXT,
  branch_name   TEXT,
  attempts      INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT    NOT NULL,
  updated_at    TEXT    NOT NULL,
  UNIQUE (external_ref)
);

CREATE TABLE stage_run (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  work_item_id    INTEGER NOT NULL REFERENCES work_item(id),
  stage           TEXT    NOT NULL CHECK (stage IN ('discover','propose','implement','package')),
  backend         TEXT    NOT NULL,
  status          TEXT    NOT NULL CHECK (status IN
                    ('running','ok','failed','halted','budget_exhausted','timeout')),
  started_at      TEXT    NOT NULL,
  ended_at        TEXT,
  turns           INTEGER,
  allowance_pct   REAL,
  cost_usd        REAL,
  exit_reason     TEXT,
  transcript_path TEXT
);

CREATE TABLE budget_period (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  unit         TEXT NOT NULL CHECK (unit IN ('allowance_pct','usd')),
  period_start TEXT NOT NULL,
  period_end   TEXT NOT NULL,
  allocated    REAL NOT NULL,
  consumed     REAL NOT NULL DEFAULT 0,
  UNIQUE (unit, period_start)
);

CREATE TABLE event (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  work_item_id INTEGER REFERENCES work_item(id),
  ts           TEXT NOT NULL,
  level        TEXT NOT NULL CHECK (level IN ('debug','info','warn','error')),
  message      TEXT NOT NULL
);

CREATE TABLE http_cache (
  url        TEXT PRIMARY KEY,
  etag       TEXT,
  body       TEXT NOT NULL,
  fetched_at TEXT NOT NULL
);

CREATE TABLE api_call (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  ts     TEXT    NOT NULL,
  url    TEXT    NOT NULL,
  status INTEGER NOT NULL,
  cached INTEGER NOT NULL
);

CREATE INDEX idx_work_item_state ON work_item(state);
CREATE INDEX idx_stage_run_item  ON stage_run(work_item_id);
CREATE INDEX idx_event_item      ON event(work_item_id);
CREATE INDEX idx_api_call_ts     ON api_call(ts);
"""

TABLES: tuple[str, ...] = (
    "schema_version",
    "work_item",
    "stage_run",
    "budget_period",
    "event",
    "http_cache",
    "api_call",
)

STATES: tuple[str, ...] = (
    "discovered",
    "proposed",
    "approved",
    "implementing",
    "packaged",
    "shipped",
    "blocked",
    "abandoned",
)

# HARNESS-SPEC.md 5.2.2. ``None`` is the pseudo-state of a row that does not exist yet.
LEGAL_TRANSITIONS: dict[str | None, frozenset[str]] = {
    None: frozenset({"discovered"}),
    "discovered": frozenset({"proposed", "abandoned"}),
    "proposed": frozenset({"approved", "blocked", "abandoned"}),
    "approved": frozenset({"implementing", "abandoned"}),
    "implementing": frozenset({"packaged", "blocked", "abandoned", "approved"}),
    "packaged": frozenset({"approved", "abandoned", "shipped"}),
    "blocked": frozenset({"approved", "abandoned"}),
    "shipped": frozenset(),
    "abandoned": frozenset(),
}

WORK_ITEM_COLUMNS: tuple[str, ...] = (
    "id",
    "kind",
    "external_ref",
    "title",
    "state",
    "parent_id",
    "depends_on",
    "tier_required",
    "spec_path",
    "package_path",
    "base_sha",
    "branch_name",
    "attempts",
    "created_at",
    "updated_at",
)

# Columns ``update_work_item`` will accept. ``id`` is not assignable.
UPDATABLE_COLUMNS: frozenset[str] = frozenset(WORK_ITEM_COLUMNS) - {"id"}


@dataclass(frozen=True)
class WorkItem:
    id: int
    kind: Literal["issue", "pr", "audit_finding"]
    external_ref: str
    title: str
    state: str
    parent_id: int | None
    depends_on: int | None
    tier_required: int
    spec_path: str | None
    package_path: str | None
    base_sha: str | None
    branch_name: str | None
    attempts: int
    created_at: str
    updated_at: str

    @property
    def issue_number(self) -> int | None:
        """The number in an ``issue:<n>`` reference; None when the reference carries none."""
        match = re.search(r"\d+", self.external_ref)
        return int(match.group()) if match else None


@dataclass(frozen=True)
class StageRun:
    id: int | None
    work_item_id: int
    stage: str
    backend: str
    status: str
    started_at: str
    ended_at: str | None
    turns: int | None
    allowance_pct: float | None
    cost_usd: float | None
    exit_reason: str | None
    transcript_path: str | None


def _work_item(row: sqlite3.Row) -> WorkItem:
    return WorkItem(
        id=row["id"],
        kind=row["kind"],
        external_ref=row["external_ref"],
        title=row["title"],
        state=row["state"],
        parent_id=row["parent_id"],
        depends_on=row["depends_on"],
        tier_required=row["tier_required"],
        spec_path=row["spec_path"],
        package_path=row["package_path"],
        base_sha=row["base_sha"],
        branch_name=row["branch_name"],
        attempts=row["attempts"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _stage_run(row: sqlite3.Row) -> StageRun:
    return StageRun(
        id=row["id"],
        work_item_id=row["work_item_id"],
        stage=row["stage"],
        backend=row["backend"],
        status=row["status"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        turns=row["turns"],
        allowance_pct=row["allowance_pct"],
        cost_usd=row["cost_usd"],
        exit_reason=row["exit_reason"],
        transcript_path=row["transcript_path"],
    )


class Store:
    """The one place SQL lives. Access is serial; the harness is single-threaded."""

    def __init__(self, db_path: Path, clock: Clock | None = None) -> None:
        self.db_path = Path(db_path)
        self._clock: Clock = clock if clock is not None else SystemClock()
        conn = sqlite3.connect(str(self.db_path), isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        self._conn: sqlite3.Connection | None = conn

    # ------------------------------------------------------------------ plumbing

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise StoreError("store is closed")
        return self._conn

    @property
    def clock(self) -> Clock:
        return self._clock

    def _now(self) -> str:
        return iso(self._clock.now())

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> Store:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    # ----------------------------------------------------------------- migration

    def migrate(self) -> None:
        """Create the 5.2.1 schema and stamp version 1. Idempotent (B7, B8)."""
        conn = self.conn
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
        ).fetchone()
        if row is not None:
            return
        try:
            conn.executescript(SCHEMA_SQL)
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        except sqlite3.Error as exc:
            raise StoreError(f"migration failed: {exc}") from exc
        conn.execute("PRAGMA foreign_keys=ON")

    def schema_version(self) -> int:
        """Current schema version; 0 when the database has never been migrated."""
        conn = self.conn
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
        ).fetchone()
        if row is None:
            return 0
        version = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        if version is None or version["v"] is None:
            return 0
        return int(version["v"])

    def tables(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        return [r["name"] for r in rows]

    # ---------------------------------------------------------------- work items

    def create_work_item(
        self,
        *,
        kind: str,
        external_ref: str,
        title: str,
        tier_required: int = 0,
    ) -> int:
        """Insert a new item in state ``discovered``. B9: duplicate ref -> DuplicateWorkItem."""
        now = self._now()
        try:
            cur = self.conn.execute(
                "INSERT INTO work_item "
                "(kind, external_ref, title, state, tier_required, attempts, "
                "created_at, updated_at) "
                "VALUES (?, ?, ?, 'discovered', ?, 0, ?, ?)",
                (kind, external_ref, title, tier_required, now, now),
            )
        except sqlite3.IntegrityError as exc:
            message = str(exc)
            if "external_ref" in message or "UNIQUE" in message.upper():
                raise DuplicateWorkItem(f"work item already exists for {external_ref}") from exc
            raise StoreError(f"cannot create work item {external_ref}: {exc}") from exc
        except sqlite3.Error as exc:
            raise StoreError(f"cannot create work item {external_ref}: {exc}") from exc
        return int(cur.lastrowid)

    def get_work_item(self, item_id: int) -> WorkItem | None:
        row = self.conn.execute("SELECT * FROM work_item WHERE id = ?", (item_id,)).fetchone()
        return _work_item(row) if row is not None else None

    def find_by_ref(self, external_ref: str) -> WorkItem | None:
        row = self.conn.execute(
            "SELECT * FROM work_item WHERE external_ref = ?", (external_ref,)
        ).fetchone()
        return _work_item(row) if row is not None else None

    def list_work_items(self, *, state: str | None = None) -> list[WorkItem]:
        if state is None:
            rows = self.conn.execute("SELECT * FROM work_item ORDER BY id").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM work_item WHERE state = ? ORDER BY id", (state,)
            ).fetchall()
        return [_work_item(r) for r in rows]

    def transition(self, item_id: int, to_state: str, *, reason: str) -> None:
        """Move an item between states per 5.2.2 (B10-B12)."""
        item = self.get_work_item(item_id)
        if item is None:
            raise StoreError(f"no work item {item_id}")
        from_state = item.state
        allowed = LEGAL_TRANSITIONS.get(from_state, frozenset())
        if to_state not in allowed:
            raise IllegalTransition(
                f"illegal transition {from_state} -> {to_state} for work item {item_id}"
            )
        now = self._now()
        conn = self.conn
        conn.execute("BEGIN")
        try:
            conn.execute(
                "UPDATE work_item SET state = ?, updated_at = ? WHERE id = ?",
                (to_state, now, item_id),
            )
            conn.execute(
                "INSERT INTO event (work_item_id, ts, level, message) VALUES (?, ?, 'info', ?)",
                (item_id, now, f"transition {from_state} -> {to_state}: {reason}"),
            )
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            conn.execute("ROLLBACK")
            raise StoreError(f"transition failed for work item {item_id}: {exc}") from exc

    def update_work_item(self, item_id: int, **fields: object) -> None:
        """Assign real columns only; anything else is a StoreError. Bumps ``updated_at``."""
        unknown = sorted(str(k) for k in fields if k not in UPDATABLE_COLUMNS)
        if unknown:
            raise StoreError(f"unknown work_item column(s): {', '.join(unknown)}")
        assignments: list[str] = []
        values: list[object] = []
        for name, value in fields.items():
            if name == "updated_at":
                continue
            assignments.append(f"{name} = ?")
            values.append(value)
        assignments.append("updated_at = ?")
        values.append(fields.get("updated_at", self._now()))
        values.append(item_id)
        sql = f"UPDATE work_item SET {', '.join(assignments)} WHERE id = ?"
        try:
            cur = self.conn.execute(sql, tuple(values))
        except sqlite3.Error as exc:
            raise StoreError(f"cannot update work item {item_id}: {exc}") from exc
        if cur.rowcount == 0:
            raise StoreError(f"no work item {item_id}")

    # ---------------------------------------------------------------- stage runs

    def start_stage_run(self, work_item_id: int, stage: str, backend: str) -> int:
        """B13: insert with status ``running``."""
        try:
            cur = self.conn.execute(
                "INSERT INTO stage_run (work_item_id, stage, backend, status, started_at) "
                "VALUES (?, ?, ?, 'running', ?)",
                (work_item_id, stage, backend, self._now()),
            )
        except sqlite3.Error as exc:
            raise StoreError(f"cannot start stage run {stage}: {exc}") from exc
        return int(cur.lastrowid)

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
    ) -> None:
        """B13: terminal status plus ``ended_at``."""
        if status == "running":
            raise StoreError("finish_stage_run needs a terminal status, not 'running'")
        try:
            cur = self.conn.execute(
                "UPDATE stage_run SET status = ?, ended_at = ?, turns = ?, allowance_pct = ?, "
                "cost_usd = ?, exit_reason = ?, transcript_path = ? WHERE id = ?",
                (
                    status,
                    self._now(),
                    turns,
                    allowance_pct,
                    cost_usd,
                    exit_reason,
                    transcript_path,
                    run_id,
                ),
            )
        except sqlite3.Error as exc:
            raise StoreError(f"cannot finish stage run {run_id}: {exc}") from exc
        if cur.rowcount == 0:
            raise StoreError(f"no stage run {run_id}")

    def get_stage_run(self, run_id: int) -> StageRun | None:
        row = self.conn.execute("SELECT * FROM stage_run WHERE id = ?", (run_id,)).fetchone()
        return _stage_run(row) if row is not None else None

    def list_stage_runs(
        self, work_item_id: int | None = None, status: str | None = None
    ) -> list[StageRun]:
        clauses: list[str] = []
        params: list[object] = []
        if work_item_id is not None:
            clauses.append("work_item_id = ?")
            params.append(work_item_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        sql = "SELECT * FROM stage_run"
        if clauses:
            sql = sql + " WHERE " + " AND ".join(clauses)
        sql = sql + " ORDER BY id"
        rows = self.conn.execute(sql, tuple(params)).fetchall()
        return [_stage_run(r) for r in rows]

    def completed_allowances(self, stage: str) -> list[float]:
        """Observed allowance_pct of every ``ok`` run of a stage, oldest first."""
        rows = self.conn.execute(
            "SELECT allowance_pct FROM stage_run "
            "WHERE stage = ? AND status = 'ok' AND allowance_pct IS NOT NULL ORDER BY id",
            (stage,),
        ).fetchall()
        return [float(r["allowance_pct"]) for r in rows]

    # -------------------------------------------------------------------- events

    def append_event(self, work_item_id: int | None, level: str, message: str) -> None:
        try:
            self.conn.execute(
                "INSERT INTO event (work_item_id, ts, level, message) VALUES (?, ?, ?, ?)",
                (work_item_id, self._now(), level, message),
            )
        except sqlite3.Error as exc:
            raise StoreError(f"cannot append event at level {level!r}: {exc}") from exc

    def events(self, work_item_id: int | None = None) -> list[dict]:
        """Events for one item, or every event when ``work_item_id`` is None."""
        if work_item_id is None:
            rows = self.conn.execute("SELECT * FROM event ORDER BY id").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM event WHERE work_item_id = ? ORDER BY id", (work_item_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    # -------------------------------------------------------------------- budget

    def ensure_budget_period(
        self, unit: str, period_start: str, period_end: str, allocated: float
    ) -> None:
        """Idempotent insert; an existing period keeps its allocation and consumption."""
        try:
            self.conn.execute(
                "INSERT INTO budget_period (unit, period_start, period_end, allocated, consumed) "
                "VALUES (?, ?, ?, ?, 0) ON CONFLICT (unit, period_start) DO NOTHING",
                (unit, period_start, period_end, float(allocated)),
            )
        except sqlite3.Error as exc:
            raise StoreError(f"cannot create budget period {unit}/{period_start}: {exc}") from exc

    def budget_period(self, unit: str, period_start: str) -> tuple[float, float]:
        """``(allocated, consumed)``; ``(0.0, 0.0)`` when no period row exists."""
        row = self.conn.execute(
            "SELECT allocated, consumed FROM budget_period WHERE unit = ? AND period_start = ?",
            (unit, period_start),
        ).fetchone()
        if row is None:
            return (0.0, 0.0)
        return (float(row["allocated"]), float(row["consumed"]))

    def consume_budget(self, unit: str, period_start: str, amount: float) -> None:
        """B14: one transaction. Anything that fails leaves ``consumed`` untouched."""
        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            raise StoreError(f"budget amount must be a number, got {type(amount).__name__}")
        value = float(amount)
        if math.isnan(value) or math.isinf(value):
            raise StoreError("budget amount must be finite")
        if value < 0:
            raise StoreError("budget amount must not be negative")
        conn = self.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            cur = conn.execute(
                "UPDATE budget_period SET consumed = consumed + ? "
                "WHERE unit = ? AND period_start = ?",
                (value, unit, period_start),
            )
            if cur.rowcount == 0:
                raise StoreError(f"no budget period {unit}/{period_start}")
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            conn.execute("ROLLBACK")
            raise StoreError(f"cannot consume budget {unit}/{period_start}: {exc}") from exc
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    # ---------------------------------------------------------------- http cache

    def cache_get(self, url: str) -> tuple[str | None, str] | None:
        """B15: ``(etag, body)`` exactly as stored, or None on a miss."""
        row = self.conn.execute(
            "SELECT etag, body FROM http_cache WHERE url = ?", (url,)
        ).fetchone()
        if row is None:
            return None
        return (row["etag"], row["body"])

    def cache_put(self, url: str, etag: str | None, body: str) -> None:
        try:
            self.conn.execute(
                "INSERT INTO http_cache (url, etag, body, fetched_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (url) DO UPDATE SET etag = excluded.etag, body = excluded.body, "
                "fetched_at = excluded.fetched_at",
                (url, etag, body, self._now()),
            )
        except sqlite3.Error as exc:
            raise StoreError(f"cannot cache {url}: {exc}") from exc

    # ----------------------------------------------------------------- api calls

    def record_api_call(self, url: str, status: int, cached: bool) -> None:
        try:
            self.conn.execute(
                "INSERT INTO api_call (ts, url, status, cached) VALUES (?, ?, ?, ?)",
                (self._now(), url, int(status), 1 if cached else 0),
            )
        except sqlite3.Error as exc:
            raise StoreError(f"cannot record api call for {url}: {exc}") from exc

    def api_calls_since(self, iso_ts: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM api_call WHERE ts >= ?", (iso_ts,)
        ).fetchone()
        return int(row["n"]) if row is not None else 0

    def list_api_calls(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM api_call ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ counting

    def counts_by_state(self) -> dict[str, int]:
        """Queue depth per state, every state present even at zero."""
        counts: dict[str, int] = {state: 0 for state in STATES}
        rows = self.conn.execute(
            "SELECT state, COUNT(*) AS n FROM work_item GROUP BY state"
        ).fetchall()
        for row in rows:
            counts[row["state"]] = int(row["n"])
        return counts


def open_store(db_path: Path, clock: Clock | None = None) -> Store:
    """Open and migrate in one step; the common case for callers outside tests."""
    store = Store(db_path, clock)
    store.migrate()
    return store


__all__ = [
    "SCHEMA_SQL",
    "SCHEMA_VERSION",
    "STATES",
    "TABLES",
    "LEGAL_TRANSITIONS",
    "WORK_ITEM_COLUMNS",
    "UPDATABLE_COLUMNS",
    "WorkItem",
    "StageRun",
    "Store",
    "open_store",
]
