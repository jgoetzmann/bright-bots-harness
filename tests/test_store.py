"""B7-B15. HARNESS-SPEC section 5.2, its verbatim DDL (5.2.1) and its transition
table (5.2.2), plus the Store extras fixed by RUN-DECISIONS.

The database is opened directly with sqlite3 in a few places: schema and row
state are observable state, not implementation detail.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from harness.clock import iso
from harness.errors import DuplicateWorkItem, IllegalTransition
from harness.store import StageRun, Store, WorkItem

# Every table named in the verbatim DDL of section 5.2.1.
EXPECTED_TABLES = {
    "schema_version",
    "work_item",
    "stage_run",
    "budget_period",
    "event",
    "http_cache",
    "api_call",
}

# Every index named in the verbatim DDL of section 5.2.1.
EXPECTED_INDEXES = {
    "idx_work_item_state",
    "idx_stage_run_item",
    "idx_event_item",
    "idx_api_call_ts",
}

# The legal transitions of section 5.2.2, exhaustively.
LEGAL_PAIRS = [
    ("discovered", "proposed"),
    ("discovered", "abandoned"),
    ("proposed", "approved"),
    ("proposed", "blocked"),
    ("proposed", "abandoned"),
    ("approved", "implementing"),
    ("approved", "abandoned"),
    ("implementing", "packaged"),
    ("implementing", "blocked"),
    ("implementing", "abandoned"),
    ("implementing", "approved"),
    ("packaged", "approved"),
    ("packaged", "abandoned"),
    ("packaged", "shipped"),
    ("blocked", "approved"),
    ("blocked", "abandoned"),
]

# Pairs absent from section 5.2.2, including both terminal states.
ILLEGAL_PAIRS = [
    ("discovered", "approved"),
    ("discovered", "implementing"),
    ("discovered", "packaged"),
    ("discovered", "shipped"),
    ("discovered", "blocked"),
    ("proposed", "implementing"),
    ("proposed", "packaged"),
    ("proposed", "shipped"),
    ("approved", "packaged"),
    ("approved", "proposed"),
    ("approved", "blocked"),
    ("implementing", "shipped"),
    ("packaged", "implementing"),
    ("packaged", "proposed"),
    ("blocked", "implementing"),
    ("blocked", "proposed"),
    ("shipped", "abandoned"),
    ("shipped", "approved"),
    ("abandoned", "approved"),
    ("abandoned", "discovered"),
    ("discovered", "not_a_state"),
]

# How to walk a freshly created item (which starts in 'discovered') into a state.
PATH_TO_STATE = {
    "discovered": (),
    "proposed": ("proposed",),
    "approved": ("proposed", "approved"),
    "implementing": ("proposed", "approved", "implementing"),
    "packaged": ("proposed", "approved", "implementing", "packaged"),
    "blocked": ("proposed", "blocked"),
    "shipped": ("proposed", "approved", "implementing", "packaged", "shipped"),
    "abandoned": ("abandoned",),
}

_REF_COUNTER = iter(range(1, 100000))


def make_item(store: Store, state: str, *, title: str = "bundle size gate misreports") -> int:
    """Create a work item and walk it to *state* using only legal transitions."""
    ref = f"issue:{next(_REF_COUNTER)}"
    item_id = store.create_work_item(kind="issue", external_ref=ref, title=title)
    for step in PATH_TO_STATE[state]:
        store.transition(item_id, step, reason="test setup")
    return item_id


def sqlite_names(db_path: Path, kind: str) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = ?", (kind,)
        ).fetchall()
    finally:
        conn.close()
    return {row[0] for row in rows}


# --------------------------------------------------------------------------
# B7 - migrate() creates every table and sets schema_version to 1
# --------------------------------------------------------------------------


def test_b7_migrate_creates_every_table_from_the_ddl(tmp_path, frozen_clock):
    """B7: every table named in section 5.2.1 exists after migrate()."""
    db_path = tmp_path / "b7.db"
    store = Store(db_path, clock=frozen_clock)
    store.migrate()
    store.close()

    assert EXPECTED_TABLES <= sqlite_names(db_path, "table")


def test_b7_migrate_creates_every_index_from_the_ddl(tmp_path, frozen_clock):
    """B7: the four indexes of section 5.2.1 exist after migrate()."""
    db_path = tmp_path / "b7idx.db"
    store = Store(db_path, clock=frozen_clock)
    store.migrate()
    store.close()

    assert EXPECTED_INDEXES <= sqlite_names(db_path, "index")


def test_b7_migrate_sets_schema_version_to_one(tmp_path, frozen_clock):
    """B7: schema_version holds exactly one row, with version 1."""
    db_path = tmp_path / "b7ver.db"
    store = Store(db_path, clock=frozen_clock)
    store.migrate()
    store.close()

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("SELECT version FROM schema_version").fetchall()
    finally:
        conn.close()

    assert [row[0] for row in rows] == [1]


def test_b7_construction_alone_creates_no_tables(tmp_path, frozen_clock):
    """B7: migrate() is what builds the schema; __init__ MUST NOT (RUN-DECISIONS)."""
    db_path = tmp_path / "b7lazy.db"
    store = Store(db_path, clock=frozen_clock)
    store.close()

    assert "work_item" not in sqlite_names(db_path, "table")


# --------------------------------------------------------------------------
# B8 - migrate() is idempotent
# --------------------------------------------------------------------------


def test_b8_migrate_twice_changes_nothing(tmp_path, frozen_clock):
    """B8: a second migrate() neither duplicates the version row nor drops data."""
    db_path = tmp_path / "b8.db"
    store = Store(db_path, clock=frozen_clock)
    store.migrate()
    item_id = store.create_work_item(kind="issue", external_ref="issue:816", title="t")
    store.migrate()
    survivor = store.get_work_item(item_id)
    store.close()

    assert survivor is not None
    assert survivor.external_ref == "issue:816"

    conn = sqlite3.connect(str(db_path))
    try:
        versions = [row[0] for row in conn.execute("SELECT version FROM schema_version")]
    finally:
        conn.close()
    assert versions == [1]


def test_b8_migrate_three_times_still_holds_one_version_row(tmp_path, frozen_clock):
    """B8: idempotence is not a one-shot property."""
    db_path = tmp_path / "b8x3.db"
    store = Store(db_path, clock=frozen_clock)
    store.migrate()
    store.migrate()
    store.migrate()
    store.close()

    conn = sqlite3.connect(str(db_path))
    try:
        versions = [row[0] for row in conn.execute("SELECT version FROM schema_version")]
    finally:
        conn.close()
    assert versions == [1]


# --------------------------------------------------------------------------
# B9 - duplicate external_ref
# --------------------------------------------------------------------------


def test_b9_a_duplicate_external_ref_raises_duplicate_work_item(store):
    """B9: external_ref is unique; the second create raises DuplicateWorkItem."""
    store.create_work_item(kind="issue", external_ref="issue:816", title="first")

    with pytest.raises(DuplicateWorkItem):
        store.create_work_item(kind="issue", external_ref="issue:816", title="second")


def test_b9_the_rejected_duplicate_leaves_one_row(store):
    """B9: a refused duplicate writes nothing; the original title survives."""
    store.create_work_item(kind="issue", external_ref="issue:816", title="first")
    with pytest.raises(DuplicateWorkItem):
        store.create_work_item(kind="pr", external_ref="issue:816", title="second")

    items = store.list_work_items()

    assert [item.title for item in items] == ["first"]


def test_b9_a_duplicate_across_kinds_is_still_a_duplicate(store):
    """B9: uniqueness is on external_ref alone, not on (kind, external_ref)."""
    store.create_work_item(kind="issue", external_ref="issue:816", title="first")

    with pytest.raises(DuplicateWorkItem):
        store.create_work_item(kind="audit_finding", external_ref="issue:816", title="other")


def test_b9_find_by_ref_matches_exactly(store):
    """B9: find_by_ref matches external_ref exactly, so near-misses are not duplicates."""
    item_id = store.create_work_item(kind="issue", external_ref="issue:816", title="t")

    found = store.find_by_ref("issue:816")
    assert isinstance(found, WorkItem)
    assert found.id == item_id
    assert store.find_by_ref("issue:81") is None
    assert store.find_by_ref("issue:8160") is None
    assert store.find_by_ref("816") is None


def test_b9_a_new_work_item_starts_in_discovered(store, frozen_clock):
    """B9: create_work_item lands the row in 'discovered' with clock timestamps."""
    item_id = store.create_work_item(kind="issue", external_ref="issue:816", title="t")

    item = store.get_work_item(item_id)

    assert item is not None
    assert item.state == "discovered"
    assert item.kind == "issue"
    assert item.tier_required == 0
    assert item.attempts == 0
    assert item.parent_id is None
    assert item.depends_on is None
    assert item.created_at == iso(frozen_clock.now())
    assert item.updated_at == iso(frozen_clock.now())


def test_b9_get_work_item_returns_none_for_an_unknown_id(store):
    """B9: an id that was never created returns None rather than raising."""
    assert store.get_work_item(4242) is None


# --------------------------------------------------------------------------
# B10 - every legal transition succeeds and updates updated_at
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("from_state", "to_state"), LEGAL_PAIRS)
def test_b10_every_legal_transition_is_accepted(store, frozen_clock, from_state, to_state):
    """B10: section 5.2.2 lists these pairs, so transition MUST accept them."""
    item_id = make_item(store, from_state)
    before = store.get_work_item(item_id)
    assert before is not None
    assert before.state == from_state

    frozen_clock.advance(60)
    store.transition(item_id, to_state, reason="because the spec says so")

    after = store.get_work_item(item_id)
    assert after is not None
    assert after.state == to_state


@pytest.mark.parametrize(("from_state", "to_state"), LEGAL_PAIRS)
def test_b10_a_legal_transition_bumps_updated_at(store, frozen_clock, from_state, to_state):
    """B10: updated_at moves to the clock's now; created_at does not move."""
    item_id = make_item(store, from_state)
    before = store.get_work_item(item_id)
    assert before is not None

    frozen_clock.advance(3600)
    store.transition(item_id, to_state, reason="clock check")

    after = store.get_work_item(item_id)
    assert after is not None
    assert after.updated_at == iso(frozen_clock.now())
    assert after.updated_at != before.updated_at
    assert after.created_at == before.created_at


def test_b10_list_work_items_filters_by_state(store):
    """B10: a transitioned item moves between the state buckets."""
    approved = make_item(store, "approved")
    make_item(store, "discovered")

    assert [item.id for item in store.list_work_items(state="approved")] == [approved]
    assert approved not in [item.id for item in store.list_work_items(state="discovered")]
    assert len(store.list_work_items()) == 2


# --------------------------------------------------------------------------
# B11 - illegal transitions raise and leave the state alone
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("from_state", "to_state"), ILLEGAL_PAIRS)
def test_b11_an_illegal_transition_raises(store, from_state, to_state):
    """B11: any pair absent from section 5.2.2 raises IllegalTransition."""
    item_id = make_item(store, from_state)

    with pytest.raises(IllegalTransition):
        store.transition(item_id, to_state, reason="should not be allowed")


@pytest.mark.parametrize(("from_state", "to_state"), ILLEGAL_PAIRS)
def test_b11_a_refused_transition_leaves_the_row_untouched(
    store, frozen_clock, from_state, to_state
):
    """B11: the refused write changes neither state nor updated_at."""
    item_id = make_item(store, from_state)
    before = store.get_work_item(item_id)
    assert before is not None

    frozen_clock.advance(120)
    with pytest.raises(IllegalTransition):
        store.transition(item_id, to_state, reason="should not be allowed")

    after = store.get_work_item(item_id)
    assert after is not None
    assert after.state == from_state
    assert after.updated_at == before.updated_at


def test_b11_transitioning_an_unknown_item_creates_nothing(store):
    """B11: a transition against a missing id writes no work_item and no event.

    The spec names no exception for this case, so the outcome asserted is the
    stored state, not the error type.
    """
    try:
        store.transition(9999, "proposed", reason="no such item")
    except Exception:
        pass

    assert store.get_work_item(9999) is None
    assert store.list_work_items() == []


# --------------------------------------------------------------------------
# B12 - transition writes an event recording from, to and reason
# --------------------------------------------------------------------------


def test_b12_transition_writes_an_event_naming_from_to_and_reason(store):
    """B12: the event row records the from-state, the to-state and the reason."""
    item_id = make_item(store, "discovered")
    reason = "spec accepted by the reviewer"

    store.transition(item_id, "proposed", reason=reason)

    messages = [event["message"] for event in store.events(item_id)]
    assert any(
        "discovered" in message and "proposed" in message and reason in message
        for message in messages
    ), messages


def test_b12_each_transition_adds_its_own_event(store):
    """B12: events accumulate, one per accepted transition."""
    item_id = make_item(store, "discovered")

    before = len(store.events(item_id))
    store.transition(item_id, "proposed", reason="one")
    store.transition(item_id, "approved", reason="two")

    after = store.events(item_id)
    assert len(after) == before + 2
    joined = " ".join(event["message"] for event in after)
    assert "one" in joined
    assert "two" in joined


def test_b12_a_refused_transition_writes_no_event(store):
    """B12: only accepted transitions are recorded; a refusal is not a transition."""
    item_id = make_item(store, "discovered")
    before = len(store.events(item_id))

    with pytest.raises(IllegalTransition):
        store.transition(item_id, "shipped", reason="not allowed")

    assert len(store.events(item_id)) == before


def test_b12_append_event_is_readable_back(store):
    """B12: events() returns what append_event wrote, scoped to the item."""
    item_id = make_item(store, "discovered")
    other_id = make_item(store, "discovered")

    store.append_event(item_id, "warn", "disk is getting tight")

    messages = [event["message"] for event in store.events(item_id)]
    assert "disk is getting tight" in messages
    assert "disk is getting tight" not in [e["message"] for e in store.events(other_id)]


# --------------------------------------------------------------------------
# B13 - stage runs
# --------------------------------------------------------------------------


def test_b13_start_stage_run_inserts_a_running_row(store, frozen_clock):
    """B13: start_stage_run inserts with status 'running' and no ended_at."""
    item_id = make_item(store, "approved")

    run_id = store.start_stage_run(item_id, "implement", "fake")

    runs = store.list_stage_runs(work_item_id=item_id)
    assert len(runs) == 1
    run = runs[0]
    assert isinstance(run, StageRun)
    assert run.id == run_id
    assert run.stage == "implement"
    assert run.backend == "fake"
    assert run.status == "running"
    assert run.started_at == iso(frozen_clock.now())
    assert run.ended_at is None
    assert run.turns is None
    assert run.allowance_pct is None


def test_b13_finish_stage_run_sets_a_terminal_status_and_ended_at(store, frozen_clock):
    """B13: finish_stage_run closes the row with every recorded field."""
    item_id = make_item(store, "approved")
    run_id = store.start_stage_run(item_id, "implement", "fake")

    frozen_clock.advance(90)
    store.finish_stage_run(
        run_id,
        status="ok",
        turns=12,
        allowance_pct=1.75,
        cost_usd=0.42,
        exit_reason=None,
        transcript_path="runs/item-1/transcript/implement.jsonl",
    )

    run = store.list_stage_runs(work_item_id=item_id)[0]
    assert run.status == "ok"
    assert run.ended_at == iso(frozen_clock.now())
    assert run.turns == 12
    assert run.allowance_pct == pytest.approx(1.75)
    assert run.cost_usd == pytest.approx(0.42)
    assert run.transcript_path == "runs/item-1/transcript/implement.jsonl"


def test_b13_a_failed_stage_run_records_its_exit_reason(store, frozen_clock):
    """B13: 'failed' is a terminal status and the exit reason is preserved."""
    item_id = make_item(store, "approved")
    run_id = store.start_stage_run(item_id, "implement", "fake")

    frozen_clock.advance(30)
    store.finish_stage_run(
        run_id,
        status="failed",
        turns=None,
        allowance_pct=None,
        cost_usd=None,
        exit_reason="npm run lint stayed red after 2 retries",
        transcript_path=None,
    )

    run = store.list_stage_runs(work_item_id=item_id)[0]
    assert run.status == "failed"
    assert run.ended_at is not None
    assert run.exit_reason == "npm run lint stayed red after 2 retries"
    assert store.list_stage_runs(status="running") == []


def test_b13_list_stage_runs_filters_by_status(store):
    """B13: an in-flight run is exactly a stage_run still in status 'running'."""
    item_id = make_item(store, "approved")
    finished = store.start_stage_run(item_id, "propose", "fake")
    store.finish_stage_run(
        finished,
        status="ok",
        turns=1,
        allowance_pct=0.1,
        cost_usd=None,
        exit_reason=None,
        transcript_path=None,
    )
    running = store.start_stage_run(item_id, "implement", "fake")

    assert [run.id for run in store.list_stage_runs(status="running")] == [running]
    assert [run.id for run in store.list_stage_runs(status="ok")] == [finished]


def test_b13_an_unknown_stage_name_is_rejected_by_the_schema(store):
    """B13: the DDL constrains stage to the four names; a fifth writes no row."""
    item_id = make_item(store, "approved")

    try:
        store.start_stage_run(item_id, "ship", "fake")
    except Exception:
        pass

    assert [run.stage for run in store.list_stage_runs(work_item_id=item_id)] == []


def test_b13_an_unknown_status_is_rejected_by_the_schema(store):
    """B13: the DDL constrains status to the six names; a seventh leaves 'running'."""
    item_id = make_item(store, "approved")
    run_id = store.start_stage_run(item_id, "implement", "fake")

    try:
        store.finish_stage_run(
            run_id,
            status="mostly_fine",
            turns=1,
            allowance_pct=0.1,
            cost_usd=None,
            exit_reason=None,
            transcript_path=None,
        )
    except Exception:
        pass

    assert store.list_stage_runs(work_item_id=item_id)[0].status == "running"


# --------------------------------------------------------------------------
# B14 - consume_budget is atomic
# --------------------------------------------------------------------------


PERIOD_START = "2026-08-31T00:00:00Z"
PERIOD_END = "2026-09-07T00:00:00Z"


def test_b14_consume_budget_accumulates_on_an_existing_period(store):
    """B14: the happy path, so the atomicity tests below have something to protect."""
    store.ensure_budget_period("allowance_pct", PERIOD_START, PERIOD_END, 40.0)

    store.consume_budget("allowance_pct", PERIOD_START, 5.0)
    store.consume_budget("allowance_pct", PERIOD_START, 2.5)

    allocated, consumed = store.budget_period("allowance_pct", PERIOD_START)
    assert allocated == pytest.approx(40.0)
    assert consumed == pytest.approx(7.5)


def test_b14_consuming_against_a_missing_period_does_not_partially_apply(store):
    """B14: a consume with no period row creates nothing and moves nothing."""
    store.ensure_budget_period("allowance_pct", PERIOD_START, PERIOD_END, 40.0)
    store.consume_budget("allowance_pct", PERIOD_START, 5.0)
    missing_start = "2025-01-06T00:00:00Z"

    try:
        store.consume_budget("allowance_pct", missing_start, 9.0)
    except Exception:
        pass

    assert store.budget_period("allowance_pct", missing_start) == (0.0, 0.0)
    allocated, consumed = store.budget_period("allowance_pct", PERIOD_START)
    assert allocated == pytest.approx(40.0)
    assert consumed == pytest.approx(5.0)


def test_b14_consuming_against_an_illegal_unit_does_not_partially_apply(store):
    """B14: the DDL admits only 'allowance_pct' and 'usd'; a third unit writes nothing."""
    store.ensure_budget_period("allowance_pct", PERIOD_START, PERIOD_END, 40.0)
    store.consume_budget("allowance_pct", PERIOD_START, 5.0)

    try:
        store.consume_budget("tokens", PERIOD_START, 3.0)
    except Exception:
        pass

    assert store.budget_period("tokens", PERIOD_START) == (0.0, 0.0)
    assert store.budget_period("allowance_pct", PERIOD_START)[1] == pytest.approx(5.0)


def test_b14_budget_period_is_zero_when_no_row_exists(store):
    """B14: an unknown period reads as (0.0, 0.0) rather than raising (RUN-DECISIONS)."""
    assert store.budget_period("allowance_pct", "1999-01-04T00:00:00Z") == (0.0, 0.0)


def test_b14_ensure_budget_period_is_idempotent(store):
    """B14: re-ensuring a period must not reset consumed, or a crash would refund spend."""
    store.ensure_budget_period("allowance_pct", PERIOD_START, PERIOD_END, 40.0)
    store.consume_budget("allowance_pct", PERIOD_START, 6.0)

    store.ensure_budget_period("allowance_pct", PERIOD_START, PERIOD_END, 40.0)

    assert store.budget_period("allowance_pct", PERIOD_START) == (
        pytest.approx(40.0),
        pytest.approx(6.0),
    )


def test_b14_units_keep_separate_ledgers(store):
    """B14: 'usd' and 'allowance_pct' share a period_start but not a balance."""
    store.ensure_budget_period("allowance_pct", PERIOD_START, PERIOD_END, 40.0)
    store.ensure_budget_period("usd", PERIOD_START, PERIOD_END, 12.0)

    store.consume_budget("usd", PERIOD_START, 3.0)

    assert store.budget_period("allowance_pct", PERIOD_START)[1] == pytest.approx(0.0)
    assert store.budget_period("usd", PERIOD_START)[1] == pytest.approx(3.0)


# --------------------------------------------------------------------------
# B15 - the http cache round-trips
# --------------------------------------------------------------------------


URL = "https://api.github.com/repos/Bright-Bots-Initiative/brightboost/issues/816"


def test_b15_cache_get_is_none_before_anything_is_stored(store):
    """B15: a cold cache is a miss, not an empty body."""
    assert store.cache_get(URL) is None


def test_b15_cache_round_trips_a_null_etag(store):
    """B15: a response with no ETag stores None, not the string 'None'."""
    body = '{"number": 816, "title": "bundle size"}'

    store.cache_put(URL, None, body)

    assert store.cache_get(URL) == (None, body)


def test_b15_cache_round_trips_a_real_etag_and_body_exactly(store):
    """B15: the ETag and body come back byte for byte."""
    etag = 'W/"3f8a1c2d4e5b6a7980112233445566778899aabb"'
    body = '{"number": 816, "labels": [{"name": "harness-ok"}], "body": "line1\\nline2"}'

    store.cache_put(URL, etag, body)

    assert store.cache_get(URL) == (etag, body)


def test_b15_cache_put_overwrites_the_previous_entry_for_a_url(store):
    """B15: url is the primary key, so a re-put replaces rather than duplicates."""
    store.cache_put(URL, 'W/"old"', '{"v": 1}')
    store.cache_put(URL, 'W/"new"', '{"v": 2}')

    assert store.cache_get(URL) == ('W/"new"', '{"v": 2}')


def test_b15_cache_entries_are_keyed_by_the_full_url(store):
    """B15: two urls that differ only in query string are two cache entries."""
    open_url = URL + "?state=open"
    closed_url = URL + "?state=closed"
    store.cache_put(open_url, None, '{"state": "open"}')
    store.cache_put(closed_url, None, '{"state": "closed"}')

    assert store.cache_get(open_url) == (None, '{"state": "open"}')
    assert store.cache_get(closed_url) == (None, '{"state": "closed"}')
    assert store.cache_get(URL) is None


def test_b15_an_empty_body_is_stored_as_an_empty_string_not_a_miss(store):
    """B15: an empty body is a hit; conflating it with a miss would refetch forever."""
    store.cache_put(URL, None, "")

    assert store.cache_get(URL) == (None, "")


# --------------------------------------------------------------------------
# One DDL, not two. The Delivery 1 constant was exported and never executed.
# --------------------------------------------------------------------------


def test_b7_the_module_carries_exactly_one_ddl_and_migrate_runs_it(tmp_path, frozen_clock):
    """``migrate()`` creates every fresh database from ``SCHEMA_SQL_V2``; the Delivery 1 DDL is
    not a constant here any more. Two DDL blocks that disagree about the legal state set, only
    one of them reachable, is the hazard: a reader cannot tell which is in force, and the spec
    fence in HARNESS-SPEC 5.2.1 is the record of the layout that is not."""
    from harness.store import sqlite as sqlite_module

    assert not hasattr(sqlite_module, "SCHEMA_SQL")
    assert "SCHEMA_SQL" not in sqlite_module.__all__
    assert "SCHEMA_SQL_V2" in sqlite_module.__all__

    db = tmp_path / "one-ddl.db"
    store = Store(db, frozen_clock)
    store.migrate()
    try:
        conn = sqlite3.connect(db)
        try:
            names = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )}
            assert EXPECTED_TABLES <= names
            # The widened CHECK of layout 2 is what is actually in force.
            assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == \
                sqlite_module.LAYOUT_VERSION
            work_item_ddl = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'work_item'"
            ).fetchone()[0]
            for state in ("proposing", "revising", "merged", "needs-human"):
                assert state in work_item_ddl
        finally:
            conn.close()
    finally:
        store.close()
