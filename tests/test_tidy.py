"""B445-B453: `harness tidy` -- the queue on the pinned issue, and the harness's own old
comments (D76).

Two halves of one command. The queue half runs on every sweep and rewrites only the span
between two markers on an issue whose body a person wrote. The prune half runs weekly and may
only ever reach a comment the harness itself wrote and marked.

Nothing here spends: the runner refuses to be called at all.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

import harness.__main__ as main_mod
from harness import links
from harness.clock import FrozenClock, iso
from harness.config import load_config
from harness.context import build_context
from harness.gh import mark_machine_written
from harness.store import Store
from tests.conftest import FROZEN_AT, write_env

INBOX = 19
TRACKING = 2
SELF_REPO = "jgoetzmann/bright-bots-harness"


class NoRunner:
    """`tidy` makes no model call, so the runner is the assertion."""

    def run(self, *args, **kwargs):  # pragma: no cover - reached only on a regression
        raise AssertionError("tidy must make no model call")


class TidyGh:
    """Records every write. Reads are served from inline dicts, never HTTP."""

    def __init__(self, *, body: str = "", comments=()) -> None:
        self.can_write = True
        self.dry_run = False
        self.body = body
        self._comments = [dict(row) for row in comments]
        self.updated_bodies: list[tuple[str, int, str]] = []
        self.deleted: list[int] = []
        self.calls: list[tuple] = []

    def get(self, path: str):
        self.calls.append(("get", path))
        return {"number": TRACKING, "body": self.body}

    def update_issue_body(self, repo: str, number: int, body: str) -> dict:
        self.calls.append(("update_issue_body", repo, int(number)))
        self.updated_bodies.append((repo, int(number), body))
        self.body = body
        return {"number": int(number), "body": body}

    def issue_comments(self, repo: str, number: int) -> list[dict]:
        self.calls.append(("issue_comments", repo, int(number)))
        return [dict(row) for row in self._comments if int(row["issue"]) == int(number)]

    def delete_issue_comment(self, repo: str, comment_id: int) -> dict:
        self.calls.append(("delete_issue_comment", repo, int(comment_id)))
        self.deleted.append(int(comment_id))
        return {"deleted": int(comment_id)}

    def read_issues(self) -> set[int]:
        return {call[2] for call in self.calls if call[0] == "issue_comments"}


def make_ctx(tmp_path, *, gh, tracking: int = TRACKING, inbox: int = INBOX):
    """A Context over a real store and ledger, with the fake client above."""
    env_path = write_env(
        tmp_path / ".env",
        SELF_REPO=SELF_REPO,
        TRACKING_ISSUE=str(tracking),
        INBOX_ISSUE=str(inbox),
    )
    config = load_config(env_path, environ={})
    clock = FrozenClock(FROZEN_AT)
    store = Store(config.db_path, clock)
    store.migrate()
    ctx = build_context(
        config, run_id="tidy", runner=NoRunner(), gh=gh, clock=clock, store=store,
        clones=object(),
    )
    return ctx, config


def body_with_markers(inside: str = "stale") -> str:
    return (
        "# The queue\n\nWritten by a person, above.\n\n"
        f"{links.QUEUE_START}\n{inside}\n{links.QUEUE_END}\n\nWritten by a person, below.\n"
    )


def machine_comment(ident: int, *, issue: int, days_old: float, body: str = "the queue") -> dict:
    """A comment the harness wrote: marked at the transport, as every one of them is."""
    return {
        "id": ident,
        "issue": issue,
        "created_at": iso(FROZEN_AT - timedelta(days=days_old)),
        "body": mark_machine_written(body),
        "user": {"login": "jgoetzmann-bot"},
    }


def human_comment(ident: int, *, issue: int, days_old: float) -> dict:
    return {
        "id": ident,
        "issue": issue,
        "created_at": iso(FROZEN_AT - timedelta(days=days_old)),
        "body": "please make the activity cards keyboard reachable",
        "user": {"login": "nathan"},
    }


# --------------------------------------------------------------------------------------
# B445-B448 - publishing the queue
# --------------------------------------------------------------------------------------


def test_B445_only_the_span_between_the_markers_is_rewritten(tmp_path):
    """The rest of that issue is a person's prose. The markers are the whole contract."""
    gh = TidyGh(body=body_with_markers())
    ctx, config = make_ctx(tmp_path, gh=gh)

    result = main_mod._publish_queue(ctx, config)

    assert f"written to #{TRACKING}" in result
    _repo, number, new_body = gh.updated_bodies[-1]
    assert number == TRACKING
    assert new_body.startswith("# The queue\n\nWritten by a person, above.\n\n")
    assert new_body.endswith("\n\nWritten by a person, below.\n")
    assert "stale" not in new_body, "the old block is gone"
    assert new_body.count(links.QUEUE_START) == 1 and new_body.count(links.QUEUE_END) == 1


def test_B446_an_unchanged_block_sends_no_patch(tmp_path):
    """B446: published on every sweep, which is several times a day. Rewriting a body that has
    not changed puts an edit in the issue's timeline and spends a write for nothing."""
    gh = TidyGh(body=body_with_markers())
    ctx, config = make_ctx(tmp_path, gh=gh)

    assert "written to" in main_mod._publish_queue(ctx, config)
    written = len(gh.updated_bodies)

    assert main_mod._publish_queue(ctx, config) == "unchanged"
    assert len(gh.updated_bodies) == written, "no second PATCH"


def test_B447_the_block_names_the_queue_the_halt_the_allowance_and_the_window(tmp_path):
    """B447: what a person opening the pinned issue needs in order to stop guessing."""
    gh = TidyGh(body=body_with_markers())
    ctx, config = make_ctx(tmp_path, gh=gh)
    ctx.ledger.request_halt("jgoetzmann", "the usage looks wrong", iso(FROZEN_AT))

    main_mod._publish_queue(ctx, config)

    body = gh.updated_bodies[-1][2]
    assert "**Halted** by @jgoetzmann" in body and "the usage looks wrong" in body
    assert "**Allowance**" in body
    assert "**Queue**" in body and "- empty" in body
    assert "run window" in body
    assert "next scheduled sweep" in body
    assert iso(FROZEN_AT) in body, "and when it was written"


def test_B447_the_pinned_queue_and_the_status_reply_use_one_renderer(tmp_path):
    """Two answers to the same question that can disagree are worse than one answer."""
    from harness import priority

    rows = [
        priority.Waiting(cls="directed", label="#4 make the cards reachable", note="discovered"),
        priority.Waiting(cls="suggested", label="#9 delete the orphan", forced=True),
    ]

    lines = links.queue_lines(rows)

    assert lines[0] == "**Queue** — 2 waiting"
    assert "`directed` #4 make the cards reachable · discovered" in lines[1]
    assert "**forced**" in lines[2]
    assert links.queue_lines([])[1] == "- empty"
    assert links.queue_lines(rows, limit=1)[-1] == "- …and 1 more"


def test_B448_a_body_without_the_markers_is_left_alone_and_says_so(tmp_path):
    """Never appended and never guessed at: a harness that decides where its own block belongs
    rewrites what somebody else wrote."""
    gh = TidyGh(body="a body a person wrote, with no markers anywhere in it")
    ctx, config = make_ctx(tmp_path, gh=gh)

    result = main_mod._publish_queue(ctx, config)

    assert "no queue markers" in result
    assert gh.updated_bodies == [], "nothing was written at all"


def test_B448_only_the_first_marker_pair_is_honoured_and_a_reversed_pair_writes_nothing():
    """The splice is pure, so the awkward shapes are cheap to pin here."""
    assert links.replace_queue_block("no markers", "x") is None
    assert links.replace_queue_block(f"{links.QUEUE_END}\n{links.QUEUE_START}", "x") is None
    assert links.replace_queue_block(f"a{links.QUEUE_START}old{links.QUEUE_END}b", "NEW") == (
        "aNEWb"
    )


# --------------------------------------------------------------------------------------
# B449-B453 - hygiene
# --------------------------------------------------------------------------------------


def test_B449_a_comment_without_the_machine_marker_is_never_deleted(tmp_path):
    """The marker is the sole test, and it is applied at the transport to everything the
    harness writes. Nothing a person writes carries it, so no human comment is reachable."""
    gh = TidyGh(comments=[human_comment(i, issue=INBOX, days_old=400) for i in range(1, 40)])
    ctx, config = make_ctx(tmp_path, gh=gh)

    deleted, _skipped = main_mod._prune_machine_comments(ctx, config)

    assert deleted == [] and gh.deleted == []


def test_B450_the_newest_machine_comments_and_anything_recent_survive(tmp_path):
    """B450: both conditions have to hold. Recent context survives whatever the count, and an
    old thread never empties itself out completely."""
    old = [machine_comment(i, issue=INBOX, days_old=400 - i) for i in range(1, 31)]
    recent = [machine_comment(100 + i, issue=INBOX, days_old=3) for i in range(5)]
    gh = TidyGh(comments=old + recent)
    ctx, config = make_ctx(tmp_path, gh=gh)

    deleted, _skipped = main_mod._prune_machine_comments(ctx, config)

    # 35 machine comments: the newest 20 stay, leaving the 15 oldest, all past 30 days.
    assert len(deleted) == 15
    assert set(deleted) <= {row["id"] for row in old}
    assert not set(deleted) & {row["id"] for row in recent}, "recent context is kept"


def test_B450_a_thread_at_or_under_the_keep_count_loses_nothing_however_old(tmp_path):
    gh = TidyGh(comments=[machine_comment(i, issue=INBOX, days_old=900) for i in range(1, 21)])
    ctx, config = make_ctx(tmp_path, gh=gh)

    assert main_mod._prune_machine_comments(ctx, config)[0] == []


def test_B451_the_prune_runs_at_most_once_a_week(tmp_path):
    """B451: `tidy` itself runs on every sweep, to keep the queue fresh. The cursor is what
    keeps the prune half off that schedule."""
    gh = TidyGh(comments=[machine_comment(i, issue=INBOX, days_old=400) for i in range(1, 41)])
    ctx, config = make_ctx(tmp_path, gh=gh)

    first, _ = main_mod._prune_machine_comments(ctx, config)
    assert first, "the first run prunes"
    assert ctx.ledger.pruned_at() == iso(FROZEN_AT)

    gh.deleted.clear()
    second, skipped = main_mod._prune_machine_comments(ctx, config)
    assert second == [] and gh.deleted == []
    assert "last pruned" in skipped

    ctx.clock.advance(main_mod.PRUNE_EVERY_DAYS * 24 * 3600)
    third, _ = main_mod._prune_machine_comments(ctx, config)
    assert third, "a week later it runs again"


def test_B452_no_issue_but_the_inbox_and_the_tracking_issue_is_touched(tmp_path):
    """B452: two issues, both named in the configuration. A work item's own thread is its log,
    and deleting from it would destroy the record of what happened."""
    rows = (
        [machine_comment(i, issue=INBOX, days_old=400) for i in range(1, 41)]
        + [machine_comment(100 + i, issue=TRACKING, days_old=400) for i in range(1, 41)]
        + [machine_comment(200 + i, issue=7, days_old=400) for i in range(1, 41)]
    )
    gh = TidyGh(comments=rows)
    ctx, config = make_ctx(tmp_path, gh=gh)

    main_mod._prune_machine_comments(ctx, config)

    assert gh.read_issues() == {INBOX, TRACKING}
    assert all(ident < 200 for ident in gh.deleted), "issue 7 is not the harness's to tidy"


def test_B452_with_no_write_credential_nothing_is_deleted(tmp_path):
    gh = TidyGh(comments=[machine_comment(i, issue=INBOX, days_old=400) for i in range(1, 41)])
    gh.can_write = False
    ctx, config = make_ctx(tmp_path, gh=gh)

    deleted, skipped = main_mod._prune_machine_comments(ctx, config)

    assert deleted == [] and gh.deleted == []
    assert "no write credential" in skipped
    assert ctx.ledger.pruned_at() is None, "and the cursor does not move over work not done"


def test_B453_a_dry_run_records_the_delete_and_sends_nothing(tmp_path):
    """`--dry-run` is a global flag and it has to hold for the one write that cannot be undone.
    The opener is the assertion: reaching it at all fails the test."""
    from harness.gh import GitHubClient

    def refuse(request):  # pragma: no cover - reached only on a regression
        raise AssertionError(f"a dry run sent {request.get_method()} {request.full_url}")

    clock = FrozenClock(FROZEN_AT)
    store = Store(tmp_path / "h.db", clock)
    store.migrate()
    client = GitHubClient(
        "o/r", store, clock, 50, token="ghp_" + "FAKE0" * 8, self_repo=SELF_REPO,
        dry_run=True, opener=refuse,
    )

    assert client.delete_issue_comment(SELF_REPO, 99) == {"deleted": 99}
    assert [call["method"] for call in client.sent] == ["DELETE"]
    assert client.sent[0]["url"].endswith("/issues/comments/99")


def test_B453_the_prune_survives_a_comment_it_cannot_delete(tmp_path):
    """One refusal -- a comment somebody deleted by hand between the read and the write -- must
    not abandon the rest of the sweep."""
    from harness.errors import GitHubError

    gh = TidyGh(comments=[machine_comment(i, issue=INBOX, days_old=400) for i in range(1, 41)])
    original = gh.delete_issue_comment

    def sometimes_refuse(repo, comment_id):
        if int(comment_id) == 3:
            raise GitHubError("404 Not Found")
        return original(repo, comment_id)

    gh.delete_issue_comment = sometimes_refuse
    ctx, config = make_ctx(tmp_path, gh=gh)

    deleted, _skipped = main_mod._prune_machine_comments(ctx, config)

    assert 3 not in deleted
    assert len(deleted) == 19, "the other nineteen still went"
