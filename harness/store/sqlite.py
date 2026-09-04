"""SQLite persistence: schema, migrations and every query (I-5: no SQL outside this module).

Delivery 1's ``harness/store.py`` moved here verbatim (RUN-DECISIONS-D2 R-A) and extended for the
store seam: the twelve Delivery 2 states, the unified transition table, ``publish_proposal``,
``merged_issues`` and ``reconcile_stale_running`` (B147).
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
from harness.redact import write_redacted

# The row in ``schema_version`` (HARNESS-SPEC 5.2.1, verbatim; tests B7/B8 assert it stays 1).
SCHEMA_VERSION = 1
# ``PRAGMA user_version``: the Delivery 2 layout (twelve states, seven stages in the CHECKs).
LAYOUT_VERSION = 2

# The Delivery 1 layout (HARNESS-SPEC 5.2.1) has no constant here and is never created:
# ``migrate()`` builds every fresh database from ``SCHEMA_SQL_V2`` below, and a database
# already on layout 1 is widened by ``WORK_ITEM_DDL_V2``/``STAGE_RUN_DDL_V2``. The spec's
# own fence is the record of what layout 1 said, and the only one that cannot disagree
# with the DDL in force.

# Delivery 2 layout: the same tables with the widened CHECK constraints. ``{name}`` lets the
# migration create the rebuilt table under a temporary name.
WORK_ITEM_DDL_V2 = """
CREATE TABLE {name} (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  kind          TEXT    NOT NULL CHECK (kind IN ('issue','pr','audit_finding')),
  external_ref  TEXT    NOT NULL,
  title         TEXT    NOT NULL,
  state         TEXT    NOT NULL CHECK (state IN
                  ('discovered','proposing','proposed','approved','implementing',
                   'packaged','shipped','revising','merged','blocked','needs-human',
                   'abandoned')),
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
"""

STAGE_RUN_DDL_V2 = """
CREATE TABLE {name} (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  work_item_id    INTEGER NOT NULL REFERENCES work_item(id),
  stage           TEXT    NOT NULL CHECK (stage IN
                    ('discover','propose','implement','package','deliver','revise','decompose')),
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
"""

_OTHER_TABLES_SQL = """
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
"""

_INDEX_SQL = """
CREATE INDEX idx_work_item_state ON work_item(state);
CREATE INDEX idx_stage_run_item  ON stage_run(work_item_id);
CREATE INDEX idx_event_item      ON event(work_item_id);
CREATE INDEX idx_api_call_ts     ON api_call(ts);
"""

SCHEMA_SQL_V2 = (
    "\nCREATE TABLE schema_version (version INTEGER NOT NULL);\n"
    + WORK_ITEM_DDL_V2.format(name="work_item")
    + STAGE_RUN_DDL_V2.format(name="stage_run")
    + _OTHER_TABLES_SQL
    + _INDEX_SQL
)

STATES: tuple[str, ...] = (
    "discovered",
    "proposing",
    "proposed",
    "approved",
    "implementing",
    "packaged",
    "shipped",
    "revising",
    "merged",
    "blocked",
    "needs-human",
    "abandoned",
)

LABELS: dict[str, str] = {
    "discovered": "harness:queued",
    "proposing": "harness:proposing",
    "proposed": "harness:proposed",
    "approved": "harness:approved",
    "implementing": "harness:running",
    "packaged": "harness:packaged",
    "shipped": "harness:shipped",
    "revising": "harness:revising",
    "merged": "harness:merged",
    "blocked": "harness:blocked",
    "needs-human": "harness:needs-human",
    "abandoned": "harness:abandoned",
}

# The one transition table, shared by both stores: RUN-DECISIONS-D2 section 3 minus the three
# pairs Delivery 1 pins illegal (B11: discovered->blocked, approved->blocked,
# shipped->abandoned), plus revising->approved, which RUN-DECISIONS-D3 "Handoff and continue"
# step 6 requires ("transition the item to `approved` from whichever of
# `implementing`/`packaged`/`revising` it is in"). ``"new"`` is the pseudo-state of a row that
# does not exist yet.
TRANSITIONS: dict[str, frozenset[str]] = {
    "new": frozenset({"discovered"}),
    "discovered": frozenset({"proposing", "proposed", "abandoned"}),
    "proposing": frozenset({"proposed", "discovered", "blocked", "abandoned"}),
    "proposed": frozenset({"approved", "proposing", "discovered", "blocked", "abandoned"}),
    "approved": frozenset({"implementing", "discovered", "abandoned"}),
    "implementing": frozenset({"packaged", "approved", "blocked", "abandoned"}),
    "packaged": frozenset({"shipped", "revising", "approved", "blocked", "abandoned"}),
    "shipped": frozenset({"revising", "merged", "needs-human", "blocked"}),
    "revising": frozenset({"shipped", "needs-human", "approved", "blocked", "abandoned"}),
    "needs-human": frozenset({"revising", "shipped", "abandoned"}),
    "blocked": frozenset({"approved", "discovered", "abandoned"}),
    "merged": frozenset(),
    "abandoned": frozenset(),
}

# B147: where a stage left mid-flight returns to.
STALE_PREVIOUS: dict[str, str] = {
    "implementing": "approved",
    "revising": "shipped",
    "proposing": "discovered",
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

STAGE_RUN_COLUMNS: tuple[str, ...] = (
    "id",
    "work_item_id",
    "stage",
    "backend",
    "status",
    "started_at",
    "ended_at",
    "turns",
    "allowance_pct",
    "cost_usd",
    "exit_reason",
    "transcript_path",
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


def bare_filename(filename: str) -> str:
    """A proposal file name with no directory part; anything else is a StoreError."""
    name = str(filename).strip()
    if not name or name in (".", "..") or "/" in name or "\\" in name or name != Path(name).name:
        raise StoreError(f"proposal filename must be a bare file name, got {filename!r}")
    return name


class SqliteStore:
    """The one place SQL lives. Access is serial; the harness is single-threaded."""

    def __init__(
        self,
        db_path: Path,
        clock: Clock | None = None,
        proposals_dir: Path | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self._clock: Clock = clock if clock is not None else SystemClock()
        if proposals_dir is None:
            proposals_dir = self.db_path.parent / "proposals"
        self.proposals_dir = Path(proposals_dir)
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

    def _now(self) -> str:
        return iso(self._clock.now())

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ----------------------------------------------------------------- migration

    def migrate(self) -> None:
        """Create the schema (layout 2) or upgrade a layout-1 database. Idempotent (B7, B8).

        The ``schema_version`` row stays ``1`` (5.2.1 verbatim); the Delivery 2 layout is stamped
        in ``PRAGMA user_version`` and recognised from the CHECK constraints themselves.
        """
        conn = self.conn
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
        ).fetchone()
        if row is None:
            try:
                conn.executescript(SCHEMA_SQL_V2)
                conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
                conn.execute(f"PRAGMA user_version={LAYOUT_VERSION}")
            except sqlite3.Error as exc:
                raise StoreError(f"migration failed: {exc}") from exc
            conn.execute("PRAGMA foreign_keys=ON")
            return
        if self._table_sql("work_item").find("'merged'") < 0:
            self._rebuild("work_item", WORK_ITEM_DDL_V2, WORK_ITEM_COLUMNS, "idx_work_item_state")
        if self._table_sql("stage_run").find("'deliver'") < 0:
            self._rebuild("stage_run", STAGE_RUN_DDL_V2, STAGE_RUN_COLUMNS, "idx_stage_run_item")
        current = conn.execute("PRAGMA user_version").fetchone()
        if current is None or int(current[0]) < LAYOUT_VERSION:
            conn.execute(f"PRAGMA user_version={LAYOUT_VERSION}")
        conn.execute("PRAGMA foreign_keys=ON")

    def _table_sql(self, table: str) -> str:
        row = self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        return str(row["sql"]) if row is not None and row["sql"] is not None else ""

    def _rebuild(self, table: str, ddl: str, columns: tuple[str, ...], index: str) -> None:
        """Additive rebuild: copy every row into a table with the widened CHECK, then swap."""
        conn = self.conn
        temp = f"{table}_v{LAYOUT_VERSION}"
        cols = ", ".join(columns)
        index_sql = {
            "idx_work_item_state": "CREATE INDEX idx_work_item_state ON work_item(state)",
            "idx_stage_run_item": "CREATE INDEX idx_stage_run_item ON stage_run(work_item_id)",
        }[index]
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN")
        try:
            conn.execute(f"DROP TABLE IF EXISTS {temp}")
            conn.execute(ddl.format(name=temp))
            conn.execute(f"INSERT INTO {temp} ({cols}) SELECT {cols} FROM {table}")
            conn.execute(f"DROP TABLE {table}")
            conn.execute(f"ALTER TABLE {temp} RENAME TO {table}")
            conn.execute(index_sql)
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            conn.execute("ROLLBACK")
            conn.execute("PRAGMA foreign_keys=ON")
            raise StoreError(
                f"migration of {table} to layout {LAYOUT_VERSION} failed: {exc}"
            ) from exc
        conn.execute("PRAGMA foreign_keys=ON")

    # ---------------------------------------------------------------- work items

    def create_work_item(
        self,
        *,
        kind: str,
        external_ref: str,
        title: str,
        tier_required: int = 0,
        body: str = "",
        upstream_body: str = "",
    ) -> int:
        """Insert a new item in state ``discovered``. B9: duplicate ref -> DuplicateWorkItem.

        ``body`` has no column; a non-empty one is kept as an event row (S11).
        """
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
        item_id = int(cur.lastrowid)
        if body:
            self.append_event(item_id, "info", f"body: {body}")
        return item_id

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
        """Move an item between states per 5.2.2 / RUN-DECISIONS-D2 section 3 (B10-B12)."""
        item = self.get_work_item(item_id)
        if item is None:
            raise StoreError(f"no work item {item_id}")
        from_state = item.state
        allowed = TRANSITIONS.get(from_state, frozenset())
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

    def _mirror_work_item(self, item: WorkItem) -> None:
        """Package-internal: upsert a row under an explicit id (the GitHub store's scratch copy).

        ``parent_id``/``depends_on`` are kept only when the referenced row is present, so the
        foreign keys hold.
        """
        now = self._now()
        parent_id = item.parent_id if self._row_exists(item.parent_id) else None
        depends_on = item.depends_on if self._row_exists(item.depends_on) else None
        values = (
            int(item.id),
            item.kind,
            item.external_ref,
            item.title,
            item.state,
            parent_id,
            depends_on,
            int(item.tier_required or 0),
            item.spec_path,
            item.package_path,
            item.base_sha,
            item.branch_name,
            int(item.attempts or 0),
            item.created_at or now,
            item.updated_at or now,
        )
        assignments = ", ".join(
            f"{name} = excluded.{name}" for name in WORK_ITEM_COLUMNS if name != "id"
        )
        try:
            self.conn.execute(
                "INSERT INTO work_item (" + ", ".join(WORK_ITEM_COLUMNS) + ") "
                "VALUES (" + ", ".join("?" for _ in WORK_ITEM_COLUMNS) + ") "
                "ON CONFLICT (id) DO UPDATE SET " + assignments,
                values,
            )
        except sqlite3.Error as exc:
            raise StoreError(f"cannot mirror work item {item.id}: {exc}") from exc

    def _row_exists(self, item_id: int | None) -> bool:
        if item_id is None:
            return False
        row = self.conn.execute(
            "SELECT 1 FROM work_item WHERE id = ?", (int(item_id),)
        ).fetchone()
        return row is not None

    # ------------------------------------------------------------- delivery 2 seam

    def publish_proposal(self, item_id: int, filename: str, text: str) -> str:
        """Write ``<proposals_dir>/<filename>`` redacted and move the item to ``proposed``.

        Returns the path written, as a string (the GitHub store returns a PR URL instead).
        """
        name = bare_filename(filename)
        item = self.get_work_item(item_id)
        if item is None:
            raise StoreError(f"no work item {item_id}")
        path = self.proposals_dir / name
        write_redacted(path, text)
        self.transition(item_id, "proposed", reason=f"proposal published at {path}")
        return str(path)

    def merged_issues(self) -> set[int]:
        """Ids of every item in state ``merged``."""
        rows = self.conn.execute("SELECT id FROM work_item WHERE state = 'merged'").fetchall()
        return {int(r["id"]) for r in rows}

    def reconcile_stale_running(self, older_than_iso: str) -> list[int]:
        """B147: items stuck mid-flight (``updated_at`` before the cutoff) go back one state."""
        rows = self.conn.execute(
            "SELECT id, state, updated_at FROM work_item "
            "WHERE state IN ('implementing', 'revising', 'proposing') AND updated_at < ? "
            "ORDER BY id",
            (str(older_than_iso),),
        ).fetchall()
        reset: list[int] = []
        for row in rows:
            item_id = int(row["id"])
            previous = STALE_PREVIOUS[str(row["state"])]
            self.transition(
                item_id,
                previous,
                reason=(
                    f"reconcile: {row['state']} since {row['updated_at']} with no live run "
                    f"(older than {older_than_iso}) (B147)"
                ),
            )
            reset.append(item_id)
        return reset

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


# Delivery 1 name for the class; ``harness.store.Store`` is bound to it too (RUN-DECISIONS-D2 R-A).
Store = SqliteStore


__all__ = [
    "SCHEMA_SQL_V2",
    "SCHEMA_VERSION",
    "LAYOUT_VERSION",
    "STATES",
    "LABELS",
    "TRANSITIONS",
    "STALE_PREVIOUS",
    "WORK_ITEM_COLUMNS",
    "STAGE_RUN_COLUMNS",
    "UPDATABLE_COLUMNS",
    "WorkItem",
    "StageRun",
    "SqliteStore",
    "Store",
    "bare_filename",
]
