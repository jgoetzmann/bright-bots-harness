"""Spec tests for ``harness.ledger`` — Delivery 2 handoff §6.2 (B114, B115, B116, B117).

Written from the spec before the implementation existed. Surface is frozen by
``.fullsend/RUN-DECISIONS-D2.md`` §4; fixtures are inline on purpose.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from harness import redact
from harness.clock import FrozenClock, iso
from harness.errors import HarnessError
from harness.ledger import Ledger, load, rebuild, save

NOW = FrozenClock(datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)).now()
NOW_ISO = iso(NOW)  # 2026-09-02T12:00:00Z (a Wednesday)
PERIOD_START = "2026-08-31T00:00:00Z"  # the Monday before NOW
RUN_URL = "https://github.com/jgoetzmann/bright-bots-harness/actions/runs/1"


def fresh() -> Ledger:
    return Ledger.empty(PERIOD_START)


def spend(ledger: Ledger, *, n: int = 1, stage: str = "implement", usd: float = 1.0,
          issue: int = 816, ts: str = NOW_ISO) -> None:
    for i in range(n):
        ledger.record(ts=ts, stage=stage, issue=issue + i, usd=usd, run=f"{RUN_URL}/{i}")


def b101_comment(*, stage: str, to_state: str, run: str, usd: float, issue: int,
                 created_at: str, reason: str = "ok") -> dict:
    """A transition comment exactly as RUN-DECISIONS-D2 §3 says GitHubStore writes it (the
    stage/run/cost record every transition leaves on the issue)."""
    body = f"**harness** `{stage}` → `{to_state}`\nrun: {run}\ncost: ${usd:.2f}\n{reason}"
    return {"body": body, "created_at": created_at, "issue": issue}


@pytest.fixture
def state_dir(tmp_path: Path):
    """A writable state directory registered as an I-8 write root for the duration of the test."""
    d = tmp_path / "state"
    d.mkdir()
    before = redact.allowed_roots()
    redact.set_write_roots([tmp_path])
    yield d
    redact.set_write_roots(list(before))


# ---------------------------------------------------------------------------
# B114 — nobody claims to know remaining allowance
# ---------------------------------------------------------------------------

def test_B114_ledger_exposes_no_allowance_field_or_method():
    """B114: no module claims to know remaining allowance — the Ledger has no attribute named
    for it, and neither its JSON nor its window/observations mention it."""
    ledger = fresh()
    spend(ledger, n=3, usd=2.0)
    names = [n for n in dir(ledger) if "allowance" in n.lower()]
    assert names == []
    assert "allowance" not in ledger.to_json().lower()
    assert "allowance" not in (json.dumps(ledger.window) + json.dumps(ledger.observations)).lower()


def test_B114_record_ignores_allowance_pct_from_a_runresult_shaped_payload():
    """B114: a RunResult-shaped payload carrying allowance_pct never lands in the ledger — record
    either refuses the extra key or drops it; the persisted state depends on usd only."""
    ledger = fresh()
    payload = {"ts": NOW_ISO, "stage": "implement", "issue": 816, "usd": 2.31,
               "run": RUN_URL, "allowance_pct": 42.5}
    try:
        ledger.record(**payload)
    except TypeError:
        payload.pop("allowance_pct")
        ledger.record(**payload)
    text = ledger.to_json()
    assert "allowance" not in text.lower()
    assert "42.5" not in text
    assert ledger.window["spent_usd"] == pytest.approx(2.31)
    assert ledger.window["calls"] == 1
    assert ledger.history[-1] == {"ts": NOW_ISO, "stage": "implement", "issue": 816,
                                  "usd": 2.31, "run": RUN_URL}


def test_B114_record_accumulates_spend_and_calls_in_the_window():
    """B114 (§6.1 spend accounting): every record adds its usd to window.spent_usd and one to
    window.calls — the ledger accumulates cost, it never estimates allowance."""
    ledger = fresh()
    ledger.record(ts=NOW_ISO, stage="propose", issue=816, usd=0.42, run=RUN_URL)
    ledger.record(ts=NOW_ISO, stage="implement", issue=816, usd=2.10, run=RUN_URL)
    ledger.record(ts=NOW_ISO, stage="implement", issue=823, usd=0.0, run=RUN_URL)
    assert ledger.window["spent_usd"] == pytest.approx(2.52)
    assert ledger.window["calls"] == 3
    assert ledger.window["period_start"] == PERIOD_START
    assert [h["issue"] for h in ledger.history] == [816, 816, 823]


# ---------------------------------------------------------------------------
# B115 — temp-then-os.replace
# ---------------------------------------------------------------------------

def test_B115_save_writes_the_json_and_leaves_no_temp_file(state_dir: Path):
    """B115: save is temp-then-os.replace; afterwards the directory holds only the ledger file,
    the file's bytes are exactly to_json(), LF newlines, trailing newline."""
    ledger = fresh()
    spend(ledger, usd=2.31)
    path = state_dir / "ledger.json"
    save(ledger, path)
    assert [p.name for p in state_dir.iterdir()] == ["ledger.json"]
    raw = path.read_bytes()
    assert raw.endswith(b"\n")
    assert b"\r\n" not in raw
    assert raw.decode("utf-8") == ledger.to_json()
    data = json.loads(raw)
    assert data["schema"] == 1
    assert data["window"]["spent_usd"] == pytest.approx(2.31)


def test_B115_save_failure_before_replace_leaves_original_unchanged(state_dir: Path,
                                                                    monkeypatch):
    """B115: when os.replace fails mid-write the existing ledger file is byte-for-byte untouched —
    the temp-then-replace protocol never truncates or half-writes the real file."""
    path = state_dir / "ledger.json"
    original = fresh()
    spend(original, usd=1.00)
    save(original, path)
    before = path.read_bytes()

    updated = fresh()
    spend(updated, n=5, usd=9.99)
    assert updated.to_json().encode("utf-8") != before  # the overwrite would have changed it

    def boom(*args, **kwargs):
        raise OSError("disk full during replace")

    monkeypatch.setattr(os, "replace", boom)
    try:
        save(updated, path)
    except Exception:  # noqa: BLE001 — the spec does not fix the exception type
        pass
    assert path.read_bytes() == before
    assert json.loads(path.read_text(encoding="utf-8"))["window"]["spent_usd"] == pytest.approx(1.0)


def test_B115_save_then_load_round_trips_exactly(state_dir: Path):
    """B115: load(save(ledger)) reproduces the same JSON text, including cursors and history."""
    ledger = fresh()
    spend(ledger, n=2, usd=0.5)
    ledger.mark_seen("IC_abc")
    ledger.count_denied("mallory")
    ledger.set_rate_limited("2026-09-02T13:00:00Z")
    path = state_dir / "ledger.json"
    save(ledger, path)
    loaded = load(path)
    assert loaded.to_json() == ledger.to_json()
    assert loaded.seen("IC_abc") is True
    assert loaded.cursors["keyword_denied"]["mallory"] == 1
    assert loaded.rate_limited(NOW_ISO) is True


def test_B115_to_json_key_order_indent_and_trailing_newline():
    """B115/§6.2: the file IS the §6.2 JSON — keys in the handoff's order, indent=2, one trailing
    newline, window keys in order period_start/spent_usd/calls/rate_limited_until."""
    ledger = fresh()
    text = ledger.to_json()
    assert text.endswith("\n")
    assert not text.endswith("\n\n")
    assert text.startswith('{\n  "schema": 1,\n  "window": {\n    "period_start": ')
    data = json.loads(text)
    assert list(data.keys()) == ["schema", "window", "observations", "cursors", "history"]
    assert list(data["window"].keys()) == ["period_start", "spent_usd", "calls",
                                           "rate_limited_until"]
    assert data["window"] == {"period_start": PERIOD_START, "spent_usd": 0.0, "calls": 0,
                              "rate_limited_until": None}
    assert data["observations"] == {}
    assert data["history"] == []


def test_B115_from_json_to_json_round_trip_is_identical():
    """B115: from_json(to_json(x)).to_json() == to_json(x) — the on-disk form is lossless."""
    ledger = fresh()
    spend(ledger, n=4, usd=1.25)
    ledger.mark_seen("IC_1")
    ledger.count_denied("mallory")
    text = ledger.to_json()
    again = Ledger.from_json(text)
    assert again.to_json() == text
    assert again.window["spent_usd"] == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# B116 — history cap
# ---------------------------------------------------------------------------

def test_B116_history_capped_at_500_after_501_records_observations_count_all():
    """B116: after 501 records history holds at most 500 entries (oldest folded into
    observations), while observations, calls and spend still account for all 501."""
    ledger = fresh()
    for i in range(501):
        ledger.record(ts=NOW_ISO, stage="implement", issue=i, usd=1.0, run=f"{RUN_URL}/{i}")
    assert len(ledger.history) <= 500
    assert len(json.loads(ledger.to_json())["history"]) <= 500
    assert ledger.observations["implement"]["n"] == 501
    assert ledger.window["calls"] == 501
    assert ledger.window["spent_usd"] == pytest.approx(501.0)
    assert ledger.history[-1]["issue"] == 500  # append-only: the newest survives
    assert ledger.median_usd("implement") == pytest.approx(1.0)


def test_B116_history_of_exactly_500_is_not_truncated():
    """B116: the cap is 500 inclusive — 500 records keep all 500 entries, oldest first."""
    ledger = fresh()
    for i in range(500):
        ledger.record(ts=NOW_ISO, stage="implement", issue=i, usd=1.0, run=f"{RUN_URL}/{i}")
    assert len(ledger.history) == 500
    assert ledger.history[0]["issue"] == 0
    assert ledger.history[-1]["issue"] == 499
    assert ledger.observations["implement"]["n"] == 500


def test_B116_history_entries_have_exactly_the_five_keys_in_order():
    """B116/§6.2: each history entry is {ts, stage, issue, usd, run} — nothing else is appended."""
    ledger = fresh()
    ledger.record(ts=NOW_ISO, stage="revise", issue=816, usd=0.88, run=RUN_URL)
    entry = json.loads(ledger.to_json())["history"][0]
    assert list(entry.keys()) == ["ts", "stage", "issue", "usd", "run"]
    assert entry == {"ts": NOW_ISO, "stage": "revise", "issue": 816, "usd": 0.88, "run": RUN_URL}


# ---------------------------------------------------------------------------
# B117 — reconstructible from the issue's transition comments
# ---------------------------------------------------------------------------

def test_B117_rebuild_from_transition_comments_matches_record_within_a_cent():
    """B117: rebuilding from the issues' transition comments reproduces history, observations,
    calls and spend of a ledger built by record() within 0.01."""
    entries = [
        ("propose", "proposed", 816, 0.42, "2026-09-02T09:00:00Z"),
        ("implement", "packaged", 816, 2.31, "2026-09-02T09:30:00Z"),
        ("propose", "proposed", 823, 0.55, "2026-09-02T10:00:00Z"),
        ("implement", "packaged", 823, 1.97, "2026-09-02T10:30:00Z"),
        ("propose", "proposed", 830, 0.39, "2026-09-02T11:00:00Z"),
        ("revise", "shipped", 816, 0.88, "2026-09-02T11:30:00Z"),
    ]
    reference = Ledger.empty("2026-09-01T00:00:00Z")
    comments = []
    for k, (stage, to_state, issue, usd, ts) in enumerate(entries):
        run = f"{RUN_URL}/{k}"
        reference.record(ts=ts, stage=stage, issue=issue, usd=usd, run=run)
        comments.append(b101_comment(stage=stage, to_state=to_state, run=run, usd=usd,
                                     issue=issue, created_at=ts))

    rebuilt = rebuild(comments)

    assert len(rebuilt.history) == len(reference.history) == 6
    for got, want in zip(rebuilt.history, reference.history):
        assert got["stage"] == want["stage"]
        assert got["issue"] == want["issue"]
        assert got["run"] == want["run"]
        assert got["ts"] == want["ts"]
        assert got["usd"] == pytest.approx(want["usd"], abs=0.01)
    assert set(rebuilt.observations) == {"propose", "implement", "revise"}
    for stage in ("propose", "implement", "revise"):
        assert rebuilt.observations[stage]["n"] == reference.observations[stage]["n"]
        assert rebuilt.observations[stage]["median_usd"] == pytest.approx(
            reference.observations[stage]["median_usd"], abs=0.01)
    assert rebuilt.median_usd("propose") == pytest.approx(reference.median_usd("propose"),
                                                          abs=0.01)
    assert rebuilt.window["spent_usd"] == pytest.approx(reference.window["spent_usd"], abs=0.01)
    assert rebuilt.window["calls"] == reference.window["calls"] == 6


def test_B117_rebuild_ignores_comments_that_are_not_transition_comments():
    """B117: human chatter and other non-transition comments contribute nothing — only comments
    carrying the stage/run/cost record are replayed."""
    comments = [
        {"body": "looks good to me", "created_at": "2026-09-02T09:00:00Z", "issue": 816},
        {"body": "/harness fix", "created_at": "2026-09-02T09:05:00Z", "issue": 816},
        b101_comment(stage="implement", to_state="packaged", run=RUN_URL, usd=2.31, issue=816,
                     created_at="2026-09-02T09:30:00Z"),
        {"body": "cost: $99.00 is what I paid for lunch", "created_at": "2026-09-02T09:40:00Z",
         "issue": 816},
    ]
    rebuilt = rebuild(comments)
    assert len(rebuilt.history) == 1
    assert rebuilt.history[0]["usd"] == pytest.approx(2.31, abs=0.01)
    assert rebuilt.window["spent_usd"] == pytest.approx(2.31, abs=0.01)
    assert rebuilt.window["calls"] == 1


def test_B117_rebuild_from_no_comments_is_an_empty_ledger():
    """B117: an empty comment stream rebuilds to an empty ledger with schema 1."""
    rebuilt = rebuild([])
    assert rebuilt.schema == 1
    assert rebuilt.history == []
    assert rebuilt.observations == {}
    assert rebuilt.window["spent_usd"] == 0.0
    assert rebuilt.window["calls"] == 0


def test_B117_load_of_a_missing_file_is_the_epoch_empty_ledger():
    """B117: losing the file costs accuracy, not correctness — load() of a missing path returns
    Ledger.empty("1970-01-01T00:00:00Z") rather than raising."""
    ledger = load(Path("Z:/definitely/not/here/ledger.json"))
    assert ledger.window["period_start"] == "1970-01-01T00:00:00Z"
    assert ledger.window["spent_usd"] == 0.0
    assert ledger.window["calls"] == 0
    assert ledger.window["rate_limited_until"] is None
    assert ledger.history == []
    assert ledger.observations == {}
    assert ledger.to_json() == Ledger.empty("1970-01-01T00:00:00Z").to_json()


def test_B117_from_json_rejects_a_wrong_schema():
    """B117: a ledger whose schema is not 1 is refused, never silently reinterpreted."""
    data = json.loads(fresh().to_json())
    data["schema"] = 2
    with pytest.raises((HarnessError, ValueError)):
        Ledger.from_json(json.dumps(data))


def test_B117_from_json_rejects_malformed_text():
    """B117: text that is not a JSON object is refused."""
    with pytest.raises((HarnessError, ValueError)):
        Ledger.from_json('{"schema": 1, "window": ')


def test_B117_from_json_rejects_a_missing_schema_key():
    """B117: a JSON object with no schema key is not a ledger."""
    data = json.loads(fresh().to_json())
    del data["schema"]
    with pytest.raises((HarnessError, ValueError, KeyError)):
        Ledger.from_json(json.dumps(data))


# ---------------------------------------------------------------------------
# observations / median (B116 fold + §6.4 step 6 inputs)
# ---------------------------------------------------------------------------

def test_B116_median_usd_is_none_below_three_observations():
    """B116/§6.4: below three observations there is no median — the dispatcher must fall back
    to the static table, so median_usd returns None, not 0."""
    ledger = fresh()
    assert ledger.median_usd("implement") is None
    spend(ledger, n=2, stage="implement", usd=2.0)
    assert ledger.median_usd("implement") is None
    assert ledger.observations["implement"]["n"] == 2


def test_B116_median_usd_after_three_observations_is_the_middle_value():
    """B116/§6.4: with three observations the median is the middle value."""
    ledger = fresh()
    ledger.record(ts=NOW_ISO, stage="implement", issue=1, usd=0.50, run=RUN_URL)
    ledger.record(ts=NOW_ISO, stage="implement", issue=2, usd=9.00, run=RUN_URL)
    ledger.record(ts=NOW_ISO, stage="implement", issue=3, usd=2.10, run=RUN_URL)
    assert ledger.median_usd("implement") == pytest.approx(2.10)
    assert ledger.observations["implement"] == {"n": 3, "median_usd": pytest.approx(2.10)}


def test_B116_median_usd_for_an_unobserved_stage_is_none():
    """B116/§6.4: a stage never recorded has no median and no observations entry."""
    ledger = fresh()
    spend(ledger, n=3, stage="implement")
    assert ledger.median_usd("decompose") is None
    assert "decompose" not in ledger.observations


# ---------------------------------------------------------------------------
# rate limit (B120 writes it, B121 reads it)
# ---------------------------------------------------------------------------

def test_B121_rate_limited_is_true_while_now_is_before_until():
    """B121/B120: after set_rate_limited(until), rate_limited(now) is True for now < until and the
    window records the reset time."""
    ledger = fresh()
    ledger.set_rate_limited("2026-09-02T13:00:00Z")
    assert ledger.window["rate_limited_until"] == "2026-09-02T13:00:00Z"
    assert ledger.rate_limited(NOW_ISO) is True
    assert ledger.rate_limited("2026-09-02T12:59:59Z") is True
    assert json.loads(ledger.to_json())["window"]["rate_limited_until"] == "2026-09-02T13:00:00Z"


def test_B121_rate_limited_is_false_at_and_after_until():
    """B121: 'starts nothing while now < rate_limited_until' — at now == until the limit has
    expired, and so it has after."""
    ledger = fresh()
    ledger.set_rate_limited("2026-09-02T12:00:00Z")
    assert ledger.rate_limited("2026-09-02T12:00:00Z") is False
    assert ledger.rate_limited("2026-09-02T12:00:01Z") is False
    assert ledger.rate_limited("2026-09-03T00:00:00Z") is False


def test_B121_set_rate_limited_none_clears_the_limit():
    """B121/B120: set_rate_limited(None) clears the window's reset time."""
    ledger = fresh()
    ledger.set_rate_limited("2026-09-09T00:00:00Z")
    assert ledger.rate_limited(NOW_ISO) is True
    ledger.set_rate_limited(None)
    assert ledger.window["rate_limited_until"] is None
    assert ledger.rate_limited(NOW_ISO) is False


def test_B121_fresh_ledger_is_not_rate_limited():
    """B121: an empty ledger has no reset time and is never rate limited."""
    ledger = fresh()
    assert ledger.window["rate_limited_until"] is None
    assert ledger.rate_limited(NOW_ISO) is False
    assert ledger.rate_limited("1970-01-01T00:00:00Z") is False


# ---------------------------------------------------------------------------
# window roll (§6.2 window, RUN-DECISIONS-D2 §4 roll_window)
# ---------------------------------------------------------------------------

def test_B116_roll_window_after_seven_days_resets_spend_and_keeps_history():
    """B116 (append-only history) + §6.2 window: when now >= period_start + 7d the window rolls to
    the most recent reset day with zero spend and zero calls; history and observations survive."""
    ledger = Ledger.empty("2026-08-17T00:00:00Z")
    spend(ledger, n=3, usd=1.5, ts="2026-08-18T00:00:00Z")
    assert ledger.window["spent_usd"] == pytest.approx(4.5)
    rolled = ledger.roll_window(NOW, "monday")
    assert rolled is True
    assert ledger.window["period_start"] == "2026-08-31T00:00:00Z"
    assert ledger.window["spent_usd"] == 0.0
    assert ledger.window["calls"] == 0
    assert len(ledger.history) == 3
    assert ledger.observations["implement"]["n"] == 3
    assert ledger.median_usd("implement") == pytest.approx(1.5)


def test_B116_roll_window_before_seven_days_is_a_no_op():
    """§6.2 window: inside the seven-day window roll_window returns False and changes nothing."""
    ledger = fresh()
    spend(ledger, n=2, usd=2.0)
    before = ledger.to_json()
    assert ledger.roll_window(NOW, "monday") is False
    assert ledger.to_json() == before
    assert ledger.window["period_start"] == PERIOD_START
    assert ledger.window["spent_usd"] == pytest.approx(4.0)


def test_B116_roll_window_at_exactly_seven_days_rolls():
    """§6.2 window: the boundary is inclusive — now == period_start + 7d rolls."""
    ledger = Ledger.empty("2026-08-24T00:00:00Z")
    spend(ledger, usd=3.0, ts="2026-08-25T00:00:00Z")
    at_boundary = datetime(2026, 8, 24, tzinfo=timezone.utc) + timedelta(days=7)
    assert ledger.roll_window(at_boundary, "monday") is True
    assert ledger.window["period_start"] == "2026-08-31T00:00:00Z"
    assert ledger.window["spent_usd"] == 0.0
    assert ledger.window["calls"] == 0
    assert len(ledger.history) == 1


# ---------------------------------------------------------------------------
# cursors: seen / mark_seen (B135) and keyword_denied (B132)
# ---------------------------------------------------------------------------

def test_B135_seen_is_false_for_an_unknown_comment_id():
    """B135: a comment id never marked is not seen."""
    ledger = fresh()
    assert ledger.seen("IC_kwDOAbc123") is False
    assert ledger.seen("") is False
    assert ledger.cursors["seen_comment_ids"] == []


def test_B135_mark_seen_then_seen_is_true_and_persists_through_json():
    """B135: mark_seen records the node id in cursors.seen_comment_ids; it survives to_json/
    from_json; other ids stay unseen."""
    ledger = fresh()
    ledger.mark_seen("IC_kwDOAbc123")
    assert ledger.seen("IC_kwDOAbc123") is True
    assert ledger.seen("IC_kwDOAbc124") is False
    assert "IC_kwDOAbc123" in ledger.cursors["seen_comment_ids"]
    again = Ledger.from_json(ledger.to_json())
    assert again.seen("IC_kwDOAbc123") is True
    assert again.seen("IC_kwDOAbc124") is False


def test_B135_mark_seen_twice_records_the_id_once():
    """B135: marking the same id twice does not duplicate it — a replay is a no-op."""
    ledger = fresh()
    ledger.mark_seen("IC_1")
    ledger.mark_seen("IC_1")
    assert ledger.cursors["seen_comment_ids"].count("IC_1") == 1
    assert ledger.seen("IC_1") is True


def test_B132_count_denied_increments_per_handle():
    """B132: each denial is counted under the handle only — no body, no comment id."""
    ledger = fresh()
    ledger.count_denied("mallory")
    ledger.count_denied("mallory")
    ledger.count_denied("eve")
    assert ledger.cursors["keyword_denied"] == {"mallory": 2, "eve": 1}
    data = json.loads(ledger.to_json())
    assert data["cursors"]["keyword_denied"] == {"mallory": 2, "eve": 1}


def test_B132_count_denied_unknown_handle_is_absent():
    """B132: a handle never denied has no entry; a fresh ledger's keyword_denied is empty."""
    ledger = fresh()
    assert ledger.cursors["keyword_denied"] == {}
    ledger.count_denied("mallory")
    assert "jgoetzmann" not in ledger.cursors["keyword_denied"]
    assert ledger.cursors["keyword_denied"].get("jgoetzmann", 0) == 0


def test_B117_empty_ledger_shape():
    """B117/§6.2: Ledger.empty(period_start) is the canonical zero state every rebuild and every
    missing-file load starts from."""
    ledger = Ledger.empty("2026-09-07T00:00:00Z")
    assert ledger.schema == 1
    assert ledger.window == {"period_start": "2026-09-07T00:00:00Z", "spent_usd": 0.0,
                             "calls": 0, "rate_limited_until": None}
    assert ledger.observations == {}
    assert ledger.cursors["notifications_last_seen"] is None
    assert ledger.cursors["seen_comment_ids"] == []
    assert ledger.cursors["keyword_denied"] == {}
    assert ledger.history == []
