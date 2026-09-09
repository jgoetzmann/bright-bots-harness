"""Spec tests for ``harness.keywords`` — Delivery 2 handoff §8 and §9.2 (B131–B135, B140–B141).

Written from the spec before the implementation existed. Surface is frozen by
``.fullsend/RUN-DECISIONS-D2.md`` §6. Fixtures are inline on purpose.
Review selectors: ``-k denied`` (R4.2), ``-k untrusted_body`` (R4.4), ``-k replay`` (R4.7).
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime, timezone

import pytest

from harness.clock import FrozenClock, iso
from harness.trust import parse_trust
from harness.keywords import VERBS, Command, authorise, command_from, parse, sweep
from harness.ledger import Ledger

NOW = FrozenClock(datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)).now()
NOW_ISO = iso(NOW)
CURSOR = "2026-09-01T00:00:00Z"
SELF_REPO = "jgoetzmann/bright-bots-harness"
UPSTREAM = "Bright-Bots-Initiative/brightboost"
# B269/D60: the trust file carries levels now, and jgoetzmann is the operator. A bare set
# still works everywhere it did, but it grants only the least level, so a fixture that
# exercises level-2 and level-3 verbs has to say which level it means.
TRUSTED = parse_trust("3 jgoetzmann")
CANARY = "CANARY-7f3a-untrusted-body-must-never-appear"
WRITE_PREFIXES = ("create", "set_", "comment", "push", "close", "request")


def fresh_ledger(cursor: str | None = CURSOR) -> Ledger:
    ledger = Ledger.empty("2026-08-31T00:00:00Z")
    ledger.cursors["notifications_last_seen"] = cursor
    return ledger


def comment(*, login: str, association: str, body: str, id: int = 1001,
            node_id: str | None = None, created_at: str = "2026-09-02T11:30:00Z") -> dict:
    data = {"id": id, "user": {"login": login}, "author_association": association, "body": body,
            "created_at": created_at, "updated_at": created_at}
    if node_id is not None:
        data["node_id"] = node_id
    return data


class BodyTrap(Mapping):
    """A comment whose body detonates when read. Every Mapping access goes through
    __getitem__, so keys()/items()/get()/dict(...) all trip it too."""

    def __init__(self, data: dict) -> None:
        self._data = dict(data)

    def __getitem__(self, key):
        if key == "body":
            raise AssertionError("body read before authorisation")
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


def trapped(*, login: str = "mallory", association: str = "NONE", id: int = 4242,
            node_id: str = "IC_trap") -> BodyTrap:
    return BodyTrap(comment(login=login, association=association, body="/harness stop", id=id,
                            node_id=node_id))


def thread(repo: str, number: int, kind: str, thread_id: str) -> dict:
    """A GET /notifications thread in GitHub's shape (subject.url carries the number)."""
    path = "pulls" if kind == "PullRequest" else "issues"
    owner, name = repo.split("/")
    return {
        "id": thread_id,
        "unread": True,
        "reason": "comment",
        "updated_at": "2026-09-02T11:30:00Z",
        "subject": {
            "title": f"thread {number}",
            "url": f"https://api.github.com/repos/{repo}/{path}/{number}",
            "latest_comment_url": f"https://api.github.com/repos/{repo}/issues/comments/1",
            "type": kind,
        },
        "repository": {"full_name": repo, "name": name, "owner": {"login": owner}},
        "url": f"https://api.github.com/notifications/threads/{thread_id}",
    }


class FakeGh:
    """Records every method call. Reads are served from the dicts given; any other method name
    is recorded and returns [] so a write attempt shows up in `calls` instead of raising."""

    def __init__(self, threads=(), comments=None, review_comments=None) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self._threads = list(threads)
        self._comments = dict(comments or {})
        self._review_comments = dict(review_comments or {})
        self.can_write = True  # a sweep must not write even when it could
        self.dry_run = False
        self.sent: list[dict] = []

    def notifications(self, *args, **kwargs):
        self.calls.append(("notifications", args, kwargs))
        return list(self._threads)

    def issue_comments(self, repo, number):
        self.calls.append(("issue_comments", (repo, number), {}))
        return list(self._comments.get((repo, int(number)), []))

    def pull_review_comments(self, repo, number):
        self.calls.append(("pull_review_comments", (repo, number), {}))
        return list(self._review_comments.get((repo, int(number)), []))

    def pull_reviews(self, repo, number):
        self.calls.append(("pull_reviews", (repo, number), {}))
        return []

    def pull(self, repo, number):
        self.calls.append(("pull", (repo, number), {}))
        return {"number": int(number), "state": "open",
                "head": {"ref": f"harness/fix-{number}-x", "sha": "a" * 40},
                "base": {"ref": "main", "sha": "b" * 40}}

    def user(self):
        self.calls.append(("user", (), {}))
        return {"login": "bb-machine"}

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)

        def recorded(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return []

        return recorded

    def write_calls(self) -> list[str]:
        return [name for name, _, _ in self.calls if name.startswith(WRITE_PREFIXES)]

    def since_args(self) -> list:
        out = []
        for name, args, kwargs in self.calls:
            if name == "notifications":
                out.append(args[0] if args else kwargs.get("since_iso", kwargs.get("since")))
        return out


def run_sweep(gh: FakeGh, ledger: Ledger, trusted=TRUSTED) -> list[Command]:
    return sweep(gh, ledger=ledger, trusted=trusted, now_iso=NOW_ISO, self_repo=SELF_REPO,
                 upstream_repo=UPSTREAM)


# ---------------------------------------------------------------------------
# B132 — silent denial (D2-R4.2: -k denied)
# ---------------------------------------------------------------------------

def test_B132_denied_untrusted_comment_returns_none_counts_handle_and_never_reads_body():
    """B132: an untrusted actor's command yields None, is counted under keyword_denied by handle,
    and the comment body is never read (the trap raises on any body access)."""
    ledger = fresh_ledger()
    result = command_from(trapped(login="mallory", association="NONE"), surface="delivery_pr",
                          number=77, trusted=TRUSTED, ledger=ledger)
    assert result is None
    assert ledger.cursors["keyword_denied"] == {"mallory": 1}


def test_B132_denied_authorise_returns_false_and_reads_no_body():
    """B132: authorise() decides from user.login and author_association only — False for an
    untrusted OWNER, and the trapped body is untouched."""
    ledger = fresh_ledger()
    assert authorise(trapped(login="mallory", association="OWNER"), TRUSTED, ledger) is False
    assert ledger.cursors["keyword_denied"] == {"mallory": 1}


def test_B132_denied_trusted_handle_with_wrong_association_is_counted_under_the_handle():
    """B132/B131: a trusted handle commenting as CONTRIBUTOR is denied and counted — the trust
    file alone never authorises."""
    ledger = fresh_ledger()
    result = command_from(trapped(login="jgoetzmann", association="CONTRIBUTOR"),
                          surface="proposal_pr", number=33, trusted=TRUSTED, ledger=ledger)
    assert result is None
    assert ledger.cursors["keyword_denied"] == {"jgoetzmann": 1}


def test_B132_denied_twice_counts_twice_and_is_never_marked_seen():
    """B132/RUN-DECISIONS-D2 §6 order: a denied comment is not marked seen (authorise precedes
    mark_seen), so each denial is counted."""
    ledger = fresh_ledger()
    c = trapped(login="mallory", association="NONE", id=4242, node_id="IC_trap")
    for _ in range(2):
        assert command_from(c, surface="issue", number=12, trusted=TRUSTED, ledger=ledger) is None
    assert ledger.cursors["keyword_denied"]["mallory"] == 2
    assert ledger.seen("IC_trap") is False
    assert ledger.seen("4242") is False


def test_B132_denied_leaves_no_trace_of_the_body_in_ledger_or_logs(caplog):
    """B132: no log entry and no ledger field quotes an untrusted body; only the handle is
    recorded."""
    caplog.set_level(logging.DEBUG)
    ledger = fresh_ledger()
    untrusted = comment(login="mallory", association="NONE", body=f"/harness stop {CANARY}",
                        id=4243, node_id="IC_canary")
    assert command_from(untrusted, surface="delivery_pr", number=77, trusted=TRUSTED,
                        ledger=ledger) is None
    assert CANARY not in caplog.text
    assert CANARY not in ledger.to_json()
    assert "mallory" in ledger.to_json()
    assert ledger.cursors["keyword_denied"] == {"mallory": 1}


def test_B132_denied_in_sweep_makes_no_reply_no_reaction_no_write(caplog):
    """B132: a denied command found by the sweep produces no gh write of any kind, nothing in the
    logs quoting the body, and the actor is counted."""
    caplog.set_level(logging.DEBUG)
    ledger = fresh_ledger()
    untrusted = comment(login="mallory", association="NONE", body=f"/harness fix {CANARY}",
                        id=4244, node_id="IC_canary2")
    gh = FakeGh(threads=[thread(UPSTREAM, 77, "PullRequest", "t1")],
                comments={(UPSTREAM, 77): [untrusted]},
                review_comments={(UPSTREAM, 77): [untrusted]})
    assert run_sweep(gh, ledger) == []
    assert gh.write_calls() == []
    assert gh.sent == []
    assert CANARY not in caplog.text
    assert CANARY not in ledger.to_json()
    assert ledger.cursors["keyword_denied"].get("mallory", 0) >= 1


# ---------------------------------------------------------------------------
# B133 — parse after authorise (D2-R4.4: -k untrusted_body)
# ---------------------------------------------------------------------------

def test_B133_untrusted_body_is_never_parsed_by_command_from():
    """B133: authorisation precedes parsing — command_from on an untrusted comment returns None
    without the body ever being read (the trap would raise)."""
    ledger = fresh_ledger()
    result = command_from(trapped(login="mallory", association="OWNER"), surface="issue",
                          number=12, trusted=TRUSTED, ledger=ledger)
    assert result is None
    assert ledger.cursors["keyword_denied"] == {"mallory": 1}


def test_B133_untrusted_body_never_yields_a_command_in_sweep():
    """B133: sweep over threads whose only comments are untrusted (body-trapped) returns no
    Command, reads no body, writes nothing, and still advances the cursor."""
    ledger = fresh_ledger()
    gh = FakeGh(
        threads=[thread(SELF_REPO, 12, "Issue", "t1"), thread(UPSTREAM, 77, "PullRequest", "t2")],
        comments={(SELF_REPO, 12): [trapped(login="mallory", association="NONE", id=1,
                                            node_id="IC_a")],
                  (UPSTREAM, 77): [trapped(login="eve", association="CONTRIBUTOR", id=2,
                                           node_id="IC_b")]},
        review_comments={(UPSTREAM, 77): [trapped(login="eve", association="NONE", id=3,
                                                  node_id="IC_c")]},
    )
    result = run_sweep(gh, ledger)
    assert result == []
    assert gh.write_calls() == []
    assert ledger.cursors["keyword_denied"].get("mallory", 0) >= 1
    assert ledger.cursors["keyword_denied"].get("eve", 0) >= 1
    assert ledger.cursors["notifications_last_seen"] == NOW_ISO


def test_B133_untrusted_body_with_trusted_handle_but_wrong_association_in_sweep():
    """B133/B131: in the sweep, a trusted handle commenting as CONTRIBUTOR is still untrusted —
    its body is not parsed and no Command results."""
    ledger = fresh_ledger()
    gh = FakeGh(threads=[thread(SELF_REPO, 12, "Issue", "t1")],
                comments={(SELF_REPO, 12): [trapped(login="jgoetzmann",
                                                    association="CONTRIBUTOR", id=9,
                                                    node_id="IC_j")]})
    assert run_sweep(gh, ledger) == []
    assert ledger.cursors["keyword_denied"] == {"jgoetzmann": 1}


# ---------------------------------------------------------------------------
# B135 — at most once (D2-R4.7: -k replay)
# ---------------------------------------------------------------------------

def test_B135_replay_of_the_same_comment_returns_none_the_second_time():
    """B135: the first command_from returns the Command and records the node id; the same comment
    again is a no-op (None)."""
    ledger = fresh_ledger()
    c = comment(login="jgoetzmann", association="OWNER", body="/harness fix", id=555,
                node_id="IC_kwDOAbc555")
    first = command_from(c, surface="delivery_pr", number=77, trusted=TRUSTED, ledger=ledger)
    assert isinstance(first, Command)
    assert first.verb == "revise"  # `fix` is an alias now
    assert ledger.seen("IC_kwDOAbc555") is True
    second = command_from(c, surface="delivery_pr", number=77, trusted=TRUSTED, ledger=ledger)
    assert second is None


def test_B135_replay_sweep_twice_second_sweep_is_empty():
    """B135: a replayed sweep over unchanged notifications yields nothing — the comment ids are
    in the ledger cursors."""
    ledger = fresh_ledger()
    c = comment(login="jgoetzmann", association="OWNER", body="/harness queue", id=1,
                node_id="IC_q1")
    gh = FakeGh(threads=[thread(SELF_REPO, 12, "Issue", "t1")], comments={(SELF_REPO, 12): [c]})
    first = run_sweep(gh, ledger)
    assert [(x.verb, x.number) for x in first] == [("go", 12)]  # `queue` is an alias now
    assert "IC_q1" in ledger.cursors["seen_comment_ids"]
    second = run_sweep(gh, ledger)
    assert second == []
    assert ledger.cursors["seen_comment_ids"].count("IC_q1") == 1


def test_B135_replay_survives_a_ledger_round_trip():
    """B135: the seen id persists through to_json/from_json, so a later process replays to
    nothing."""
    ledger = fresh_ledger()
    c = comment(login="jgoetzmann", association="OWNER", body="/harness stop", id=7,
                node_id="IC_s7")
    assert command_from(c, surface="delivery_pr", number=77, trusted=TRUSTED,
                        ledger=ledger) is not None
    later = Ledger.from_json(ledger.to_json())
    assert command_from(c, surface="delivery_pr", number=77, trusted=TRUSTED,
                        ledger=later) is None


def test_B135_replay_key_prefers_node_id_when_present():
    """B135/RUN-DECISIONS-D2 §6: the recorded id is comment["node_id"] when present."""
    ledger = fresh_ledger()
    c = comment(login="jgoetzmann", association="OWNER", body="/harness rebase", id=8080,
                node_id="PRRC_node8080")
    cmd = command_from(c, surface="delivery_pr", number=77, trusted=TRUSTED, ledger=ledger)
    assert cmd is not None
    assert cmd.comment_id == "PRRC_node8080"
    assert ledger.seen("PRRC_node8080") is True


def test_B135_replay_key_falls_back_to_str_of_numeric_id():
    """B135/RUN-DECISIONS-D2 §6: without node_id the recorded id is str(comment["id"])."""
    ledger = fresh_ledger()
    c = comment(login="jgoetzmann", association="OWNER", body="/harness split", id=9090)
    cmd = command_from(c, surface="issue", number=12, trusted=TRUSTED, ledger=ledger)
    assert cmd is not None
    assert cmd.comment_id == "9090"
    assert ledger.seen("9090") is True
    assert command_from(c, surface="issue", number=12, trusted=TRUSTED, ledger=ledger) is None


# ---------------------------------------------------------------------------
# parse (§8.3 verbs; B133 — parse is the post-authorisation step)
# ---------------------------------------------------------------------------

def test_B133_parse_each_verb():
    """B133/§8.3: every verb in VERBS parses from a line-start '/harness <verb> <args>'."""
    from harness.keywords import ALIASES

    for verb in VERBS:
        assert parse(f"/harness {verb} some args") == (verb, "some args"), verb
        assert parse(f"/harness {verb}") == (verb, ""), verb

    # Three pairs were merged because the SURFACE already told them apart, so the second name
    # only added a way to be wrong. The old names still parse -- to the verb they became, since
    # comments already written must not silently stop working.
    assert parse("/harness fix tighten it") == ("revise", "tighten it")
    assert parse("/harness reject not worth it") == ("stop", "not worth it")
    assert parse("/harness queue") == ("go", "")
    assert parse("/harness usage") == ("status", "")
    assert set(ALIASES) & set(VERBS) == set(), "an alias must not also be a live verb"


def test_B133_parse_unknown_verb_is_none():
    """B133/§8.3: a verb outside VERBS is not a command."""
    assert parse("/harness deploy now") is None
    assert parse("/harness merge") is None
    assert parse("/harness approve") is None
    assert parse("/harness fixit") is None


def test_B133_parse_mid_body_not_at_line_start_is_none():
    """B133/RUN-DECISIONS-D2 §6 regex: '/harness' must begin a line — mid-line mentions are
    prose, not commands."""
    assert parse("please /harness stop") is None
    assert parse("I typed `/harness fix` and nothing happened") is None
    assert parse("see /harness queue above") is None


def test_B133_parse_finds_the_command_on_its_own_line_in_a_multiline_body():
    """B133/RUN-DECISIONS-D2 §6 regex (multiline): a command on a later line is found."""
    assert parse("Thanks for the PR.\n/harness rebase\nAlso fix the typo.") == ("rebase", "")
    assert parse("\n\n/harness fix\n") == ("revise", "")


def test_B133_parse_allows_leading_whitespace():
    """B133/RUN-DECISIONS-D2 §6 regex: ^\\s* permits indentation before /harness."""
    assert parse("   /harness queue") == ("go", "")
    assert parse("\t/harness stop") == ("stop", "")


def test_B133_parse_empty_body_is_none():
    """B133: nothing to parse."""
    assert parse("") is None
    assert parse("\n\n") is None
    assert parse("   ") is None


def test_B133_parse_bare_harness_without_verb_is_none():
    """B133: '/harness' alone, or with only whitespace after it, is not a command."""
    assert parse("/harness") is None
    assert parse("/harness   ") is None
    assert parse("/harness\n") is None


def test_B133_parse_requires_a_separator_between_harness_and_verb():
    """B133/RUN-DECISIONS-D2 §6 regex: `/harnessstop` is not a command — the verb has to be a
    separate token, or any word beginning "harness" becomes one.

    `/harness-stop` IS one now, reversing that clause of §6: the hyphenated spelling makes each
    command a single token, which is what lets several sit in one comment without reading as a
    sentence that got away from someone."""
    assert parse("/harnessstop") is None
    assert parse("/harnessstop now") is None
    assert parse("/harness-stop") == ("stop", "")
    assert parse("/harness-work make the cards reachable") == (
        "work", "make the cards reachable")


def test_B133_parse_args_stop_at_the_end_of_the_command_line():
    """B133/RUN-DECISIONS-D2 §6 regex: (.*) does not cross a newline — args are the rest of the
    command's line only."""
    assert parse("/harness revise line one\nline two") == ("revise", "line one")


def test_B133_parse_plain_prose_is_none():
    """B133: ordinary review text never parses as a command."""
    assert parse("LGTM, merging tomorrow") is None
    assert parse("harness fix please") is None
    assert parse("//harness fix") is None


# ---------------------------------------------------------------------------
# command_from — shape and order (B131/B135)
# ---------------------------------------------------------------------------

def test_B131_command_from_trusted_owner_returns_the_full_command():
    """B131: a trusted OWNER's '/harness fix' on a delivery PR becomes a Command with every field
    populated from the comment and the call."""
    ledger = fresh_ledger()
    c = comment(login="jgoetzmann", association="OWNER", body="/harness fix", id=42,
                node_id="IC_abc")
    cmd = command_from(c, surface="delivery_pr", number=42, trusted=TRUSTED, ledger=ledger)
    # B283/B270 appended `force` and `level`. The level is the actor's, recorded so a refusal
    # can say what it would have needed; jgoetzmann is the operator.
    assert cmd == Command(verb="revise", args="", surface="delivery_pr", number=42,
                          comment_id="IC_abc", actor="jgoetzmann", force=False, level=3)
    assert ledger.cursors["keyword_denied"] == {}


def test_B131_command_from_trusted_member_with_args():
    """B131: MEMBER is accepted and the args carry through verbatim."""
    ledger = fresh_ledger()
    c = comment(login="jgoetzmann", association="MEMBER",
                body="/harness revise mention the migration", id=43, node_id="IC_def")
    cmd = command_from(c, surface="proposal_pr", number=33, trusted=TRUSTED, ledger=ledger)
    assert cmd is not None
    assert (cmd.verb, cmd.args, cmd.surface, cmd.number) == (
        "revise", "mention the migration", "proposal_pr", 33)
    assert cmd.actor == "jgoetzmann"


def test_B131_authorise_trusted_owner_is_true_and_counts_nothing():
    """B131: authorise() is True for a trusted OWNER and leaves keyword_denied untouched."""
    ledger = fresh_ledger()
    c = comment(login="jgoetzmann", association="OWNER", body="/harness stop")
    assert authorise(c, TRUSTED, ledger) is True
    assert ledger.cursors["keyword_denied"] == {}


def test_B135_command_from_trusted_comment_without_a_command_is_none_and_not_marked_seen():
    """B135/RUN-DECISIONS-D2 §6 order: parse None → None before mark_seen, so a trusted comment
    with no command is not recorded as seen and is not counted as denied."""
    ledger = fresh_ledger()
    c = comment(login="jgoetzmann", association="OWNER", body="looks fine, thanks", id=50,
                node_id="IC_prose")
    assert command_from(c, surface="delivery_pr", number=77, trusted=TRUSTED,
                        ledger=ledger) is None
    assert ledger.seen("IC_prose") is False
    assert ledger.cursors["keyword_denied"] == {}


def test_B135_command_from_already_seen_comment_is_none_before_anything_else():
    """B135/RUN-DECISIONS-D2 §6 order: a comment already in seen_comment_ids returns None without
    authorising (no denial counted) and without parsing (trapped body untouched)."""
    ledger = fresh_ledger()
    ledger.mark_seen("IC_seen")
    c = trapped(login="mallory", association="NONE", id=60, node_id="IC_seen")
    assert command_from(c, surface="issue", number=12, trusted=TRUSTED, ledger=ledger) is None
    assert ledger.cursors["keyword_denied"] == {}


# ---------------------------------------------------------------------------
# B140 / B141 — the sweep
# ---------------------------------------------------------------------------

def sweep_fixture() -> tuple[FakeGh, dict]:
    """Three live threads: a self-repo issue, an upstream PR with a review comment, and an
    upstream issue. Every comment is from a trusted OWNER. The upstream issue used to be
    dropped; since B242 it is the `product_issue` surface."""
    self_issue = comment(login="jgoetzmann", association="OWNER", body="/harness queue", id=1,
                         node_id="IC_self12")
    upstream_review = comment(login="jgoetzmann", association="OWNER", body="/harness fix",
                              id=2, node_id="PRRC_up77")
    upstream_review.update({"path": "src/app.ts", "line": 3, "pull_request_review_id": 900})
    upstream_issue = comment(login="jgoetzmann", association="OWNER", body="/harness queue",
                             id=3, node_id="IC_up900")
    gh = FakeGh(
        threads=[thread(SELF_REPO, 12, "Issue", "t1"),
                 thread(UPSTREAM, 77, "PullRequest", "t2"),
                 thread(UPSTREAM, 900, "Issue", "t3")],
        comments={(SELF_REPO, 12): [self_issue], (UPSTREAM, 900): [upstream_issue]},
        review_comments={(UPSTREAM, 77): [upstream_review]},
    )
    return gh, {"self_issue": self_issue, "upstream_review": upstream_review}


def test_B140_sweep_reads_notifications_since_the_cursor_and_returns_commands_in_order():
    """B140: the sweep asks for notifications since the ledger cursor, walks each thread's
    comments, and returns the trusted commands in thread order with the right surface."""
    ledger = fresh_ledger(CURSOR)
    gh, _ = sweep_fixture()
    cmds = run_sweep(gh, ledger)
    assert [(c.verb, c.surface, c.number) for c in cmds] == [
        ("go", "issue", 12), ("revise", "delivery_pr", 77), ("go", "product_issue", 900)]
    assert cmds[0].comment_id == "IC_self12" and cmds[0].actor == "jgoetzmann"
    assert cmds[1].comment_id == "PRRC_up77" and cmds[1].actor == "jgoetzmann"
    assert cmds[2].comment_id == "IC_up900" and cmds[2].actor == "jgoetzmann"
    assert gh.since_args() == [CURSOR]


def test_B140_sweep_advances_the_cursor_to_now():
    """B140: after the sweep the ledger's notifications_last_seen is now_iso."""
    ledger = fresh_ledger(CURSOR)
    gh, _ = sweep_fixture()
    run_sweep(gh, ledger)
    assert ledger.cursors["notifications_last_seen"] == NOW_ISO


def test_B242_an_upstream_issue_thread_is_a_surface_of_its_own():
    """B242/D59: it used to yield nothing at all -- a `/harness` comment on a product issue was
    never seen, not ignored. It is now `product_issue`, which is deliberately NOT `issue`: the
    two repositories number independently, and `_item_for_command` maps `issue` straight to the
    number, so calling this one `issue` would act on the harness item of the same number."""
    only_upstream_issue = FakeGh(
        threads=[thread(UPSTREAM, 900, "Issue", "t3")],
        comments={(UPSTREAM, 900): [comment(login="jgoetzmann", association="OWNER",
                                            body="/harness split", id=5, node_id="IC_up900b")]})

    cmds = run_sweep(only_upstream_issue, fresh_ledger(CURSOR))

    assert [(c.verb, c.surface, c.number) for c in cmds] == [("split", "product_issue", 900)]


def test_B141_sweep_calls_no_write_method():
    """B141: the sweep is read-and-enqueue only — with can_write True it still calls no method
    named create*/set_*/comment*/push*/close*/request* and sends nothing."""
    ledger = fresh_ledger(CURSOR)
    gh, _ = sweep_fixture()
    cmds = run_sweep(gh, ledger)
    assert len(cmds) == 3
    assert gh.write_calls() == []
    assert gh.sent == []
    assert {name for name, _, _ in gh.calls} <= {
        "notifications", "issue_comments", "pull_review_comments", "pull_reviews", "pull",
        "user"}


def test_B141_sweep_makes_no_write_even_when_nothing_is_found():
    """B141/B140: an empty notification list returns no commands, writes nothing, and still
    advances the cursor to now_iso."""
    ledger = fresh_ledger(None)
    gh = FakeGh(threads=[])
    assert run_sweep(gh, ledger) == []
    assert gh.write_calls() == []
    assert ledger.cursors["notifications_last_seen"] == NOW_ISO


def test_B140_sweep_self_repo_pull_thread_is_a_proposal_pr_command():
    """B140/§8.3: a PR thread in this repository is the proposal-PR surface."""
    ledger = fresh_ledger(CURSOR)
    c = comment(login="jgoetzmann", association="OWNER",
                body="/harness revise tighten the diagnosis", id=11, node_id="IC_prop33")
    gh = FakeGh(threads=[thread(SELF_REPO, 33, "PullRequest", "t9")],
                comments={(SELF_REPO, 33): [c]})
    cmds = run_sweep(gh, ledger)
    assert [(x.verb, x.args, x.surface, x.number) for x in cmds] == [
        ("revise", "tighten the diagnosis", "proposal_pr", 33)]
    assert gh.write_calls() == []


def test_B140_sweep_upstream_pull_conversation_comment_is_a_delivery_pr_command():
    """B140/§8.3: a conversation (issue-style) comment on an upstream PR is a delivery-PR
    command too — the sweep cannot miss a thread whichever comment API it lives in."""
    ledger = fresh_ledger(CURSOR)
    c = comment(login="jgoetzmann", association="OWNER", body="/harness rebase", id=12,
                node_id="IC_up77conv")
    gh = FakeGh(threads=[thread(UPSTREAM, 77, "PullRequest", "t8")],
                comments={(UPSTREAM, 77): [c]})
    cmds = run_sweep(gh, ledger)
    assert [(x.verb, x.surface, x.number) for x in cmds] == [("rebase", "delivery_pr", 77)]


def test_B140_sweep_mixed_trusted_and_untrusted_returns_only_the_trusted_command():
    """B140/B132: in one thread an untrusted (trapped) comment and a trusted command coexist —
    only the trusted one becomes a Command; the untrusted one is counted, not read."""
    ledger = fresh_ledger(CURSOR)
    good = comment(login="jgoetzmann", association="OWNER", body="/harness stop", id=21,
                   node_id="IC_good")
    gh = FakeGh(threads=[thread(UPSTREAM, 77, "PullRequest", "t7")],
                comments={(UPSTREAM, 77): [trapped(login="mallory", association="NONE", id=20,
                                                   node_id="IC_bad"), good]})
    cmds = run_sweep(gh, ledger)
    assert [(x.verb, x.comment_id) for x in cmds] == [("stop", "IC_good")]
    assert ledger.cursors["keyword_denied"] == {"mallory": 1}
    assert gh.write_calls() == []


# --------------------------------------------------------------------------------------------
# Several commands in one comment, and the hyphenated spelling.
#
# Both exist for the same reason: a person on a phone thinking of three things should be able to
# say three things. Reading only the first line was a rule nothing enforced except the parser.


def test_parse_all_reads_every_command_line_in_order():
    """Not just the first. Order is the order they were typed, because they are steps."""
    from harness.keywords import parse_all

    body = (
        "morning!\n"
        "/harness-status\n"
        "/harness ask which component owns the activity cards\n"
        "/harness-work make them keyboard reachable\n"
        "thanks\n"
    )
    assert parse_all(body) == [
        ("status", ""),
        ("ask", "which component owns the activity cards"),
        ("work", "make them keyboard reachable"),
    ]


def test_parse_all_skips_an_unknown_verb_without_dropping_the_rest():
    """The old parser read the first `/harness` line and discarded the comment if its verb was
    not real -- so one typo silently ate the commands under it."""
    from harness.keywords import parse_all

    assert parse_all("/harness frobnicate\n/harness status\n") == [("status", "")]


def test_parse_all_resolves_aliases_so_a_mixed_comment_still_lands():
    from harness.keywords import parse_all

    assert parse_all("/harness fix tighten it\n/harness-queue\n") == [
        ("revise", "tighten it"), ("go", "")]


def test_parse_all_of_prose_is_empty():
    """Prose that mentions the harness is still prose, however many times it does it."""
    from harness.keywords import parse_all

    assert parse_all("as discussed, /harness stop -- and maybe /harness go later") == []


def test_commands_from_marks_the_comment_seen_once_for_a_multi_command_body():
    """B135 is per *comment*, not per command. Marking once per command would make the second
    command of a two-command comment look like a replay of the first and be dropped."""
    from harness.keywords import commands_from

    ledger = fresh_ledger()
    c = comment(login="jgoetzmann", association="OWNER", id=1, node_id="IC_multi",
                body="/harness-status\n/harness-go\n")

    first = commands_from(c, trusted=TRUSTED, ledger=ledger, surface="issue", number=12)
    assert [(x.verb, x.number) for x in first] == [("status", 12), ("go", 12)]

    assert commands_from(c, trusted=TRUSTED, ledger=ledger, surface="issue", number=12) == [], (
        "the whole comment is seen, so a second sweep replays neither command")


def test_commands_from_gates_each_command_on_its_own_level():
    """A level-2 handle sending `status` and `halt` in one comment gets the first done and the
    second refused. Gating the comment on its highest verb would refuse the harmless one too;
    gating on its lowest would let the halt through."""
    from harness.keywords import commands_from

    c = comment(login="nathan", association="MEMBER", id=2, node_id="IC_mixed",
                body="/harness-status\n/harness-halt the spend looks wrong\n")
    got = commands_from(c, trusted=parse_trust("2 nathan"), ledger=fresh_ledger(),
                        surface="issue", number=12)

    # The refusal is answered rather than dropped -- silence here reads as the harness being
    # asleep, which is the failure this whole surface exists to avoid.
    assert [x.verb for x in got] == ["status", "__denied__"]
    assert "needs level 3" in got[1].args


def test_a_refused_notifications_feed_still_delivers_the_inbox():
    """The notifications endpoint needs a scope of its own, and I-15 gives the machine PAT
    `public_repo` and nothing else -- so a correctly-configured token is refused here with a 403.

    Found by running the sweep against the live repository: it raised out of `sweep`, and the
    inbox commands it had ALREADY collected went with it. The inbox is the surface used by people
    who have read no documentation; losing it because a feed nobody sees was refused is the worse
    half of the failure by a distance.
    """
    from harness.errors import GitHubError

    ledger = fresh_ledger(None)
    c = comment(login="jgoetzmann", association="OWNER", body="/harness-status", id=99,
                node_id="IC_inbox99")

    class Refusing(FakeGh):
        def notifications(self, since):
            raise GitHubError("github returned 403 ...: Missing the 'notifications' scope.")

    gh = Refusing(comments={(SELF_REPO, 19): [c]})
    cmds = sweep(gh, ledger=ledger, trusted=TRUSTED, now_iso=NOW_ISO, self_repo=SELF_REPO,
                 upstream_repo=UPSTREAM, inbox_issue=19)

    assert [(x.verb, x.surface, x.number) for x in cmds] == [("status", "inbox", 19)]


def test_the_cursor_does_not_advance_over_a_feed_that_never_arrived():
    """Advancing it would skip the window the failed call covered, and every mention in that
    window would go unread for good -- a silent, permanent hole rather than a retry."""
    from harness.errors import GitHubError

    ledger = fresh_ledger(CURSOR)

    class Refusing(FakeGh):
        def notifications(self, since):
            raise GitHubError("403")

    sweep(Refusing(), ledger=ledger, trusted=TRUSTED, now_iso=NOW_ISO, self_repo=SELF_REPO,
          upstream_repo=UPSTREAM, inbox_issue=0)

    assert ledger.cursors["notifications_last_seen"] == CURSOR, "the window must be retried"
