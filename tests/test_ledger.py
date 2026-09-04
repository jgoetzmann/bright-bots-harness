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


# ---------------------------------------------------------------------------
# Delivery 3 — RUN-DECISIONS-D3 "Ledger" (B204, B205) plus carry and the
# backward-compatible from_json. Appended by the D3 spec-tester (T1); additions only.
#
# The signal is the CLI's rate_limit_event: utilization is a fraction 0..1 and
# seven_day.resets_at is the subscription's weekly reset (Tuesday 20:00 UTC).
# ---------------------------------------------------------------------------

D3_FIVE_HOUR_RESET = "2026-09-04T11:00:00Z"
D3_SEVEN_DAY_RESET = "2026-09-08T20:00:00Z"  # Tuesday 20:00 UTC
D3_NEXT_SEVEN_DAY_RESET = "2026-09-15T20:00:00Z"

# A ledger file written by Delivery 2, before window.usage and window.carry existed.
D2_LEDGER_TEXT = json.dumps(
    {
        "schema": 1,
        "window": {"period_start": PERIOD_START, "spent_usd": 1.5, "calls": 2,
                   "rate_limited_until": None},
        "observations": {"implement": {"n": 3, "median_usd": 0.5}},
        "cursors": {"notifications_last_seen": None, "seen_comment_ids": ["IC_1"],
                    "keyword_denied": {}},
        "history": [],
    },
    indent=2,
) + "\n"


def usage(*, weekly: float = 0.49, session: float = 0.07,
          seven_day_resets: str = D3_SEVEN_DAY_RESET,
          five_hour_resets: str = D3_FIVE_HOUR_RESET,
          status: str = "allowed", observed_at: str | None = None) -> dict:
    """The RUN-DECISIONS-D3 usage shape as the stage hands it to observe_usage."""
    payload = {
        "five_hour": {"utilization": session, "resets_at": five_hour_resets},
        "seven_day": {"utilization": weekly, "resets_at": seven_day_resets},
        "status": status,
    }
    if observed_at is not None:
        payload["observed_at"] = observed_at
    return payload


# ---------------------------------------------------------------------------
# B204 — observe_usage stores the signal; the two readers report fractions
# ---------------------------------------------------------------------------

def test_B204_observe_usage_stores_the_signal_on_the_window():
    """B204: observe_usage(usage, now_iso) stores it under window.usage, and
    weekly_utilization()/session_utilization() report the fractions unchanged."""
    ledger = fresh()
    ledger.observe_usage(usage(weekly=0.49, session=0.07), NOW_ISO)
    assert ledger.weekly_utilization() == pytest.approx(0.49)
    assert ledger.session_utilization() == pytest.approx(0.07)
    stored = ledger.window["usage"]
    assert stored["seven_day"]["utilization"] == pytest.approx(0.49)
    assert stored["seven_day"]["resets_at"] == D3_SEVEN_DAY_RESET
    assert stored["five_hour"]["utilization"] == pytest.approx(0.07)
    assert stored["five_hour"]["resets_at"] == D3_FIVE_HOUR_RESET


def test_B204_a_second_observation_replaces_the_first():
    """B204: the ledger keeps the latest reading, not a history of readings."""
    ledger = fresh()
    ledger.observe_usage(usage(weekly=0.10, session=0.02), NOW_ISO)
    ledger.observe_usage(usage(weekly=0.51, session=0.33), "2026-09-02T13:00:00Z")
    assert ledger.weekly_utilization() == pytest.approx(0.51)
    assert ledger.session_utilization() == pytest.approx(0.33)


def test_B204_a_never_observed_ledger_reports_none():
    """B204/B114: no decision may depend on the signal being present — with nothing observed
    both readers return None rather than a guess, and the window carries no usage."""
    ledger = fresh()
    spend(ledger, n=3, usd=2.0)
    assert ledger.weekly_utilization() is None
    assert ledger.session_utilization() is None
    assert ledger.window.get("usage") is None


def test_B204_observe_usage_of_none_is_a_no_op_not_a_crash():
    """B204/B114: a call with usage=None (the CLI reported nothing) must not raise and must not
    invent a utilization."""
    ledger = fresh()
    ledger.observe_usage(None, NOW_ISO)
    assert ledger.weekly_utilization() is None
    assert ledger.session_utilization() is None


def test_B204_usage_observed_before_the_window_start_is_stale():
    """B204: weekly_utilization/session_utilization are None when the observation predates the
    current window's start — a stale reading is no reading."""
    ledger = Ledger.empty("2026-09-01T00:00:00Z")
    ledger.observe_usage(usage(weekly=0.49, session=0.07), "2026-08-30T00:00:00Z")
    assert ledger.weekly_utilization() is None
    assert ledger.session_utilization() is None


def test_B204_usage_survives_to_json_and_from_json():
    """B204: the observation is part of the persisted window, so a later process reads the same
    utilization back."""
    ledger = fresh()
    ledger.observe_usage(usage(weekly=0.49, session=0.07), NOW_ISO)
    again = Ledger.from_json(ledger.to_json())
    assert again.weekly_utilization() == pytest.approx(0.49)
    assert again.session_utilization() == pytest.approx(0.07)
    assert again.to_json() == ledger.to_json()


def test_B204_save_then_load_round_trips_the_usage_and_the_carry(state_dir: Path):
    """B204/B115: save/load keeps both new window entries byte for byte."""
    ledger = fresh()
    ledger.observe_usage(usage(weekly=0.49, session=0.07), NOW_ISO)
    ledger.set_carry(816, NOW_ISO, "weekly usage 91% >= 90%")
    path = state_dir / "ledger.json"
    save(ledger, path)
    loaded = load(path)
    assert loaded.to_json() == ledger.to_json()
    assert loaded.weekly_utilization() == pytest.approx(0.49)
    assert loaded.carry_issue() == 816


def test_B204_observing_usage_does_not_touch_spend_calls_or_history():
    """B204: the usage signal and the USD accounting are independent — observing one changes
    nothing about the other (the USD path still governs when usage is absent)."""
    ledger = fresh()
    spend(ledger, n=2, usd=1.25)
    before_history = list(ledger.history)
    ledger.observe_usage(usage(weekly=0.49, session=0.07), NOW_ISO)
    assert ledger.window["spent_usd"] == pytest.approx(2.5)
    assert ledger.window["calls"] == 2
    assert ledger.history == before_history


# ---------------------------------------------------------------------------
# B205 — a new seven_day reset rolls the window and keeps the carry
# ---------------------------------------------------------------------------

def test_B205_a_new_seven_day_reset_rolls_the_window_and_keeps_the_carry():
    """B205: when seven_day.resets_at differs from the one the window implies and now is past
    the previous reset, the window rolls to that previous reset — spend and calls zeroed, the
    carried item kept so it can continue on the new week's leeway."""
    ledger = Ledger.empty("2026-09-01T20:00:00Z")
    spend(ledger, n=2, usd=2.5, ts="2026-09-02T00:00:00Z")
    ledger.set_carry(816, "2026-09-08T19:00:00Z", "weekly usage 91% >= 90%")
    ledger.observe_usage(usage(weekly=0.91, seven_day_resets=D3_SEVEN_DAY_RESET),
                         "2026-09-08T19:00:00Z")
    assert ledger.window["period_start"] == "2026-09-01T20:00:00Z"

    ledger.observe_usage(usage(weekly=0.02, session=0.01,
                               seven_day_resets=D3_NEXT_SEVEN_DAY_RESET),
                         "2026-09-08T20:00:01Z")

    assert ledger.window["period_start"] == D3_SEVEN_DAY_RESET
    assert ledger.window["spent_usd"] == 0.0
    assert ledger.window["calls"] == 0
    assert ledger.carry_issue() == 816
    assert ledger.weekly_utilization() == pytest.approx(0.02)


def test_B205_the_same_seven_day_reset_does_not_roll_the_window():
    """B205: an observation whose reset matches the window's implied reset changes nothing —
    the usual case, once per call, all week long."""
    ledger = Ledger.empty("2026-09-01T20:00:00Z")
    spend(ledger, usd=3.0, ts="2026-09-02T00:00:00Z")
    ledger.observe_usage(usage(weekly=0.10, seven_day_resets=D3_SEVEN_DAY_RESET),
                         "2026-09-02T00:00:00Z")
    ledger.observe_usage(usage(weekly=0.40, seven_day_resets=D3_SEVEN_DAY_RESET),
                         "2026-09-05T00:00:00Z")
    assert ledger.window["period_start"] == "2026-09-01T20:00:00Z"
    assert ledger.window["spent_usd"] == pytest.approx(3.0)
    assert ledger.window["calls"] == 1
    assert ledger.weekly_utilization() == pytest.approx(0.40)


def test_B205_a_later_reset_before_the_previous_one_has_passed_does_not_roll():
    """B205: both conditions are required — a differing reset alone, while now is still before
    the previous reset, leaves the window where it is."""
    ledger = Ledger.empty("2026-09-01T20:00:00Z")
    spend(ledger, usd=4.0, ts="2026-09-02T00:00:00Z")
    ledger.observe_usage(usage(seven_day_resets=D3_SEVEN_DAY_RESET), "2026-09-02T00:00:00Z")
    ledger.observe_usage(usage(seven_day_resets=D3_NEXT_SEVEN_DAY_RESET),
                         "2026-09-07T00:00:00Z")
    assert ledger.window["period_start"] == "2026-09-01T20:00:00Z"
    assert ledger.window["spent_usd"] == pytest.approx(4.0)


def test_B205_the_roll_keeps_history_and_observations():
    """B205/B116: rolling on the subscription reset is the D2 roll with a better boundary —
    the append-only history and the per-stage observations survive it."""
    ledger = Ledger.empty("2026-09-01T20:00:00Z")
    for i in range(3):
        ledger.record(ts="2026-09-02T00:00:00Z", stage="implement", issue=800 + i, usd=1.5,
                      run=RUN_URL)
    ledger.observe_usage(usage(seven_day_resets=D3_SEVEN_DAY_RESET), "2026-09-02T00:00:00Z")
    ledger.observe_usage(usage(seven_day_resets=D3_NEXT_SEVEN_DAY_RESET),
                         "2026-09-08T20:00:01Z")
    assert ledger.window["period_start"] == D3_SEVEN_DAY_RESET
    assert len(ledger.history) == 3
    assert ledger.observations["implement"]["n"] == 3
    assert ledger.median_usd("implement") == pytest.approx(1.5)


def test_B205_usage_observed_before_the_rolled_window_start_is_stale():
    """B205/B204: after the roll an observation from the old week no longer answers for the new
    one — the reader returns None until the next call reports."""
    ledger = Ledger.empty("2026-09-01T20:00:00Z")
    ledger.observe_usage(usage(weekly=0.91), "2026-09-02T00:00:00Z")
    assert ledger.weekly_utilization() == pytest.approx(0.91)
    ledger.roll_window(datetime(2026, 9, 14, tzinfo=timezone.utc), "monday")
    assert ledger.window["period_start"] == "2026-09-14T00:00:00Z"
    assert ledger.weekly_utilization() is None
    assert ledger.session_utilization() is None


def test_B205_roll_window_still_works_when_no_usage_was_ever_observed():
    """B205: the D2 roll_window is untouched — a ledger that never saw the signal still rolls on
    the seven-day boundary."""
    ledger = Ledger.empty("2026-08-17T00:00:00Z")
    spend(ledger, n=2, usd=1.0, ts="2026-08-18T00:00:00Z")
    assert ledger.roll_window(NOW, "monday") is True
    assert ledger.window["period_start"] == "2026-08-31T00:00:00Z"
    assert ledger.window["spent_usd"] == 0.0
    assert ledger.weekly_utilization() is None


# ---------------------------------------------------------------------------
# carry — set_carry / carry_issue / clear_carry
# ---------------------------------------------------------------------------

def test_B205_set_carry_records_issue_since_and_reason():
    """RUN-DECISIONS-D3 "Ledger": window.carry is {"issue", "since", "reason"} and
    carry_issue() reads the issue number back."""
    ledger = fresh()
    assert ledger.carry_issue() is None
    ledger.set_carry(816, NOW_ISO, "weekly usage 91% >= 90%")
    assert ledger.carry_issue() == 816
    assert ledger.window["carry"] == {"issue": 816, "since": NOW_ISO,
                                      "reason": "weekly usage 91% >= 90%"}


def test_B205_clear_carry_removes_it():
    """RUN-DECISIONS-D3 "Ledger": clear_carry() drops the carried item (the continue run went
    green and was packaged)."""
    ledger = fresh()
    ledger.set_carry(816, NOW_ISO, "session usage 72% >= 70%")
    ledger.clear_carry()
    assert ledger.carry_issue() is None
    assert ledger.window.get("carry") is None


def test_B205_set_carry_replaces_a_previous_carry():
    """RUN-DECISIONS-D3 "Ledger": one carried item at a time — the newest handoff wins."""
    ledger = fresh()
    ledger.set_carry(816, NOW_ISO, "weekly usage 91% >= 90%")
    ledger.set_carry(823, "2026-09-02T13:00:00Z", "carry leeway 10% reached")
    assert ledger.carry_issue() == 823
    assert ledger.window["carry"]["since"] == "2026-09-02T13:00:00Z"
    assert ledger.window["carry"]["reason"] == "carry leeway 10% reached"


def test_B205_clearing_a_carry_that_was_never_set_is_a_no_op():
    """RUN-DECISIONS-D3 "Ledger": clear_carry() on a fresh ledger neither raises nor invents."""
    ledger = fresh()
    ledger.clear_carry()
    assert ledger.carry_issue() is None


def test_B205_carry_survives_to_json_and_from_json():
    """RUN-DECISIONS-D3 "Ledger": the carry is persisted — the next process knows which item to
    continue."""
    ledger = fresh()
    ledger.set_carry(816, NOW_ISO, "weekly usage 91% >= 90%")
    again = Ledger.from_json(ledger.to_json())
    assert again.carry_issue() == 816
    assert again.window["carry"]["reason"] == "weekly usage 91% >= 90%"
    assert again.to_json() == ledger.to_json()


def test_B205_carry_is_independent_of_the_usage_observation():
    """RUN-DECISIONS-D3 "Ledger": clearing the carry does not clear the usage, and observing
    usage does not clear the carry."""
    ledger = fresh()
    ledger.set_carry(816, NOW_ISO, "weekly usage 91% >= 90%")
    ledger.observe_usage(usage(weekly=0.91, session=0.30), NOW_ISO)
    assert ledger.carry_issue() == 816
    ledger.clear_carry()
    assert ledger.weekly_utilization() == pytest.approx(0.91)


# ---------------------------------------------------------------------------
# from_json accepts a Delivery 2 file (no usage, no carry)
# ---------------------------------------------------------------------------

def test_B204_from_json_accepts_a_file_without_the_new_keys():
    """RUN-DECISIONS-D3 "Ledger": from_json accepts files without these keys (defaults None) —
    the ledger written by Delivery 2 loads unchanged."""
    ledger = Ledger.from_json(D2_LEDGER_TEXT)
    assert ledger.window["spent_usd"] == pytest.approx(1.5)
    assert ledger.window["calls"] == 2
    assert ledger.window.get("usage") is None
    assert ledger.window.get("carry") is None
    assert ledger.weekly_utilization() is None
    assert ledger.session_utilization() is None
    assert ledger.carry_issue() is None
    assert ledger.seen("IC_1") is True


def test_B204_a_delivery_2_ledger_can_then_observe_and_carry():
    """RUN-DECISIONS-D3 "Ledger": an upgraded file is fully usable — the first D3 run observes
    usage and sets a carry on it without a migration step."""
    ledger = Ledger.from_json(D2_LEDGER_TEXT)
    ledger.observe_usage(usage(weekly=0.49, session=0.07), NOW_ISO)
    ledger.set_carry(816, NOW_ISO, "weekly usage 91% >= 90%")
    assert ledger.weekly_utilization() == pytest.approx(0.49)
    assert ledger.carry_issue() == 816
    assert Ledger.from_json(ledger.to_json()).carry_issue() == 816


def test_B204_load_of_a_missing_file_has_no_usage_and_no_carry(tmp_path: Path):
    """RUN-DECISIONS-D3 "Ledger" / B117: the empty ledger a missing file yields reports no
    utilization and carries nothing."""
    ledger = load(tmp_path / "nope" / "ledger.json")
    assert ledger.weekly_utilization() is None
    assert ledger.session_utilization() is None
    assert ledger.carry_issue() is None
