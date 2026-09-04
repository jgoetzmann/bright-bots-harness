"""Delivery 2 - `GitHubStore` (harness/store/github.py) against an in-memory GitHub.

Behaviors under test: B100, B101, B102, B147, plus the store surface frozen in
`.fullsend/RUN-DECISIONS-D2.md` section 3 (create / update / get / list / merged_issues /
publish_proposal / illegal transitions).

Every fixture is inline on purpose. Nothing here touches the network or the wall clock.
"""
from __future__ import annotations

import copy
import json
import re
import urllib.parse
from datetime import datetime, timedelta, timezone

import pytest

from harness import links
from harness.clock import FrozenClock, iso
from harness.errors import (
    DuplicateWorkItem,
    GitHubError,
    HarnessError,
    IllegalTransition,
    StoreError,
    TierViolation,
)
from harness.store import LABELS, STATES
from harness.store.github import GitHubStore, _origin_ref
from harness.store.sqlite import SqliteStore

SELF_REPO = "jgoetzmann/bright-bots-harness"
UPSTREAM = "Bright-Bots-Initiative/brightboost"
BOT = "brightboost-harness"
RUN_URL = "https://github.com/jgoetzmann/bright-bots-harness/actions/runs/4242"
T0 = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
SHA_A = "3f2a9c1e5b7d4a6f8e0c2b4d6f8a0c2e4b6d8f0a"
BRANCH = "harness/fix-816-dashboard-crashes-on-first-render"
PROPOSAL_TEXT = (
    "---\nissue: 816\nupstream_issue: 816\ntitle: \"fix(dashboard): guard null user\"\n"
    "kind: fix\nslices: 1\nrisk: low\ntouched_paths:\n  - src/pages/Dashboard.tsx\n"
    "depends_on: []\nestimated_turns: 40\ngate_expectation: green\nbaseline_red: []\n---\n\n"
    "# fix(dashboard): guard null user\n\n## Diagnosis\n`src/pages/Dashboard.tsx:42`.\n"
)

# The complete read + write surface of harness/gh.py per RUN-DECISIONS-D2 section 7.
GH_SURFACE = frozenset({
    "get", "issue", "issues", "pulls", "branches", "rate_budget_remaining",
    "comment", "set_labels", "create_issue", "create_pull", "request_reviewers", "close_pull",
    "create_branch_file", "push_branch", "push_ref",
    "notifications", "issue_comments", "pull", "pull_reviews", "pull_review_comments",
    "check_runs", "user",
})
WRITE_METHODS = frozenset({
    "comment", "set_labels", "create_issue", "create_pull", "request_reviewers", "close_pull",
    "create_branch_file", "push_branch", "push_ref",
})


# --------------------------------------------------------------------------------------
# Inline fixtures
# --------------------------------------------------------------------------------------
class FakeGh:
    """Stand-in for `harness.gh.GitHubClient` exposing exactly the section-7 surface.

    Issues live in `repos[repo][number]` (labels as `[{"name": ...}]`); comments, label
    events, reviews and review comments are keyed by `(repo, number)`. Every method call is
    appended to `calls` with its bound arguments; every write is also appended to `sent` as
    `{"method", "url", "payload"}`, like the real client. Anything outside section 7 does not
    exist here, so a caller reaching for it gets `AttributeError`.
    """

    def __init__(self, *, repo: str = UPSTREAM, self_repo: str = SELF_REPO, clock=None,
                 can_write: bool = True) -> None:
        self.repo = repo
        self.self_repo = self_repo
        self.clock = clock
        self.can_write = can_write
        self.dry_run = False
        self.sent: list[dict] = []
        self.calls: list[dict] = []
        self.refused: list[str] = []
        self.repos: dict[str, dict[int, dict]] = {repo: {}, self_repo: {}}
        self.comments: dict[tuple[str, int], list[dict]] = {}
        self.events: dict[tuple[str, int], list[dict]] = {}
        self.prs: dict[str, dict[int, dict]] = {repo: {}, self_repo: {}}
        self.reviews: dict[tuple[str, int], list[dict]] = {}
        self.review_comments: dict[tuple[str, int], list[dict]] = {}
        self.check_runs_by_ref: dict[str | None, list[dict]] = {}
        self.branch_names: dict[str, list[str]] = {repo: ["main"], self_repo: ["main"]}
        self._next_number: dict[str, int] = {repo: 1000, self_repo: 1}
        self._next_comment_id = 5000

    # ---- test-side helpers (not part of section 7) ----
    def _now(self) -> str:
        return iso(self.clock.now()) if self.clock is not None else iso(T0)

    def _record(self, name: str, **bound) -> None:
        self.calls.append({"name": name, **bound})

    def _write(self, method: str, url: str, payload) -> None:
        if not self.can_write:
            self.refused.append(f"{method} {url}")
            raise TierViolation(f"write attempted without a token: {method} {url}")
        self.sent.append({"method": method, "url": url, "payload": copy.deepcopy(payload)})

    def _issue(self, repo: str, number: int) -> dict:
        try:
            return self.repos[repo][number]
        except KeyError:
            raise GitHubError(f"404 /repos/{repo}/issues/{number}") from None

    def _pr(self, repo: str, number: int) -> dict:
        try:
            return self.prs[repo][number]
        except KeyError:
            raise GitHubError(f"404 /repos/{repo}/pulls/{number}") from None

    def _new_issue(self, repo, number, title, body, labels, user) -> dict:
        now = self._now()
        issue = {
            "number": number, "node_id": f"I_{number}", "title": title, "body": body,
            "state": "open", "labels": [{"name": n} for n in labels],
            "user": {"login": user}, "assignees": [], "assignee": None, "comments": 0,
            "created_at": now, "updated_at": now,
            "html_url": f"https://github.com/{repo}/issues/{number}",
        }
        self.repos.setdefault(repo, {})[number] = issue
        self.comments.setdefault((repo, number), [])
        events = self.events.setdefault((repo, number), [])
        for name in labels:
            events.append({"event": "labeled", "label": {"name": name}, "created_at": now,
                           "actor": {"login": user}})
        self._next_number[repo] = max(self._next_number.get(repo, 1), number + 1)
        return issue

    def add_issue(self, number, title, *, body="", labels=(), repo=None, state="open",
                  user="jgoetzmann") -> dict:
        issue = self._new_issue(repo or self.self_repo, number, title, body, list(labels), user)
        issue["state"] = state
        return issue

    def add_pull(self, number, *, head_ref, head_sha, base_sha, repo=None, title="", body="",
                 user=BOT) -> dict:
        repo = repo or self.repo
        pr = {
            "number": number, "node_id": f"PR_{number}", "state": "open", "title": title,
            "body": body, "html_url": f"https://github.com/{repo}/pull/{number}",
            "user": {"login": user},
            "head": {"ref": head_ref, "sha": head_sha, "label": f"{BOT}:{head_ref}",
                     "repo": {"full_name": f"{BOT}/brightboost"}},
            "base": {"ref": "main", "sha": base_sha, "repo": {"full_name": repo}},
            "requested_reviewers": [], "created_at": self._now(),
        }
        self.prs.setdefault(repo, {})[number] = pr
        self.comments.setdefault((repo, number), [])
        return pr

    def set_issue_labels(self, number, labels, *, repo=None) -> None:
        """What a human does in the GitHub UI: not recorded as a harness call."""
        issue = self._issue(repo or self.self_repo, number)
        issue["labels"] = [{"name": n} for n in labels]
        issue["updated_at"] = self._now()

    def labels(self, number, *, repo=None) -> list[str]:
        return [lab["name"] for lab in self._issue(repo or self.self_repo, number)["labels"]]

    def state_labels(self, number, *, repo=None) -> list[str]:
        return sorted(n for n in self.labels(number, repo=repo) if n.startswith("harness:"))

    def comments_of(self, number, *, repo=None) -> list[dict]:
        return list(self.comments.get((repo or self.self_repo, number), []))

    def calls_named(self, name: str) -> list[dict]:
        return [c for c in self.calls if c["name"] == name]

    def write_names(self) -> set[str]:
        return {c["name"] for c in self.calls if c["name"] in WRITE_METHODS}

    def _list_issues(self, repo, state, labels_csv) -> list[dict]:
        want = [lab for lab in labels_csv.split(",") if lab]
        out = []
        for issue in self.repos.get(repo, {}).values():
            if state != "all" and issue["state"] != state:
                continue
            names = {lab["name"] for lab in issue["labels"]}
            if not all(w in names for w in want):
                continue
            out.append(copy.deepcopy(issue))
        return sorted(out, key=lambda i: i["number"])

    # ---- section 7 reads ----
    def get(self, path: str):
        self._record("get", path=path)
        url = path
        for prefix in ("https://api.github.com", "http://api.github.com"):
            if url.startswith(prefix):
                url = url[len(prefix):]
        parsed = urllib.parse.urlsplit(url)
        parts = [p for p in parsed.path.split("/") if p]
        q = dict(urllib.parse.parse_qsl(parsed.query))
        if parts == ["user"]:
            return self.user_dict()
        if parts[:1] == ["notifications"]:
            return []
        if len(parts) >= 3 and parts[0] == "repos":
            repo = f"{parts[1]}/{parts[2]}"
            rest = parts[3:]
            if rest == ["issues"]:
                return self._list_issues(repo, q.get("state", "open"), q.get("labels", ""))
            if rest[:1] == ["issues"] and len(rest) == 2:
                return copy.deepcopy(self._issue(repo, int(rest[1])))
            if rest[:2] == ["issues", "comments"] and len(rest) == 3:
                cid = int(rest[2])
                for lst in self.comments.values():
                    for c in lst:
                        if c["id"] == cid:
                            return copy.deepcopy(c)
                raise GitHubError(f"404 {path}")
            if rest[:1] == ["issues"] and len(rest) == 3 and rest[2] == "comments":
                return copy.deepcopy(self.comments.get((repo, int(rest[1])), []))
            if rest[:1] == ["issues"] and len(rest) == 3 and rest[2] == "events":
                return copy.deepcopy(self.events.get((repo, int(rest[1])), []))
            if rest == ["pulls"]:
                state = q.get("state", "open")
                return [copy.deepcopy(p) for p in self.prs.get(repo, {}).values()
                        if state == "all" or p["state"] == state]
            if rest[:1] == ["pulls"] and len(rest) == 2:
                return copy.deepcopy(self._pr(repo, int(rest[1])))
            if rest[:1] == ["pulls"] and len(rest) == 3 and rest[2] == "reviews":
                return copy.deepcopy(self.reviews.get((repo, int(rest[1])), []))
            if rest[:1] == ["pulls"] and len(rest) == 3 and rest[2] == "comments":
                return copy.deepcopy(self.review_comments.get((repo, int(rest[1])), []))
            if rest[:1] == ["commits"] and len(rest) == 3 and rest[2] == "check-runs":
                runs = self._check_runs(rest[1])
                return {"total_count": len(runs), "check_runs": runs}
            if rest == ["branches"]:
                return [{"name": n, "commit": {"sha": "0" * 40}}
                        for n in self.branch_names.get(repo, [])]
        raise GitHubError(f"FakeGh: unhandled GET {path}")

    def _check_runs(self, ref) -> list[dict]:
        runs = self.check_runs_by_ref.get(ref, self.check_runs_by_ref.get(None, []))
        return copy.deepcopy(runs)

    def issue(self, number: int) -> dict:
        self._record("issue", number=number)
        return copy.deepcopy(self._issue(self.repo, number))

    def issues(self, *, state: str = "open", labels=()) -> list[dict]:
        self._record("issues", state=state, labels=list(labels))
        return self._list_issues(self.repo, state, ",".join(labels))

    def pulls(self, *, state: str = "open") -> list[dict]:
        self._record("pulls", state=state)
        return [copy.deepcopy(p) for p in self.prs.get(self.repo, {}).values()
                if state == "all" or p["state"] == state]

    def branches(self) -> list[str]:
        self._record("branches")
        return list(self.branch_names.get(self.repo, []))

    def rate_budget_remaining(self) -> int:
        self._record("rate_budget_remaining")
        return 1000

    def notifications(self, since_iso) -> list[dict]:
        self._record("notifications", since_iso=since_iso)
        return []

    def issue_comments(self, repo, number) -> list[dict]:
        self._record("issue_comments", repo=repo, number=number)
        return copy.deepcopy(self.comments.get((repo, number), []))

    def pull(self, repo, number) -> dict:
        self._record("pull", repo=repo, number=number)
        return copy.deepcopy(self._pr(repo, number))

    def pull_reviews(self, repo, number) -> list[dict]:
        self._record("pull_reviews", repo=repo, number=number)
        return copy.deepcopy(self.reviews.get((repo, number), []))

    def pull_review_comments(self, repo, number) -> list[dict]:
        self._record("pull_review_comments", repo=repo, number=number)
        return copy.deepcopy(self.review_comments.get((repo, number), []))

    def check_runs(self, repo, ref) -> list[dict]:
        self._record("check_runs", repo=repo, ref=ref)
        return self._check_runs(ref)

    def user_dict(self) -> dict:
        return {"login": BOT, "id": 424242, "type": "User"}

    def user(self) -> dict:
        self._record("user")
        return self.user_dict()

    # ---- section 7 writes ----
    def comment(self, repo, number, body) -> dict:
        self._record("comment", repo=repo, number=number, body=body)
        self._write("POST", f"/repos/{repo}/issues/{number}/comments", {"body": body})
        target = self.repos.get(repo, {}).get(number) or self.prs.get(repo, {}).get(number)
        if target is None:
            raise GitHubError(f"404 /repos/{repo}/issues/{number}")
        now = self._now()
        cid = self._next_comment_id
        self._next_comment_id += 1
        c = {"id": cid, "node_id": f"IC_{cid}", "body": body, "user": {"login": BOT},
             "author_association": "OWNER", "created_at": now, "updated_at": now,
             "html_url": f"{target['html_url']}#issuecomment-{cid}"}
        self.comments.setdefault((repo, number), []).append(c)
        target["comments"] = len(self.comments[(repo, number)])
        target["updated_at"] = now
        return copy.deepcopy(c)

    def set_labels(self, repo, number, labels) -> list[dict]:
        new = list(labels)
        self._record("set_labels", repo=repo, number=number, labels=new)
        self._write("PUT", f"/repos/{repo}/issues/{number}/labels", {"labels": new})
        issue = self._issue(repo, number)
        now = self._now()
        old = {lab["name"] for lab in issue["labels"]}
        events = self.events.setdefault((repo, number), [])
        for name in sorted(old - set(new)):
            events.append({"event": "unlabeled", "label": {"name": name}, "created_at": now,
                           "actor": {"login": BOT}})
        for name in new:
            if name not in old:
                events.append({"event": "labeled", "label": {"name": name},
                               "created_at": now, "actor": {"login": BOT}})
        issue["labels"] = [{"name": n} for n in new]
        issue["updated_at"] = now
        return [{"name": n} for n in new]

    def create_issue(self, title, body, labels) -> dict:
        self._record("create_issue", title=title, body=body, labels=list(labels))
        self._write("POST", f"/repos/{self.self_repo}/issues",
                    {"title": title, "body": body, "labels": list(labels)})
        number = self._next_number[self.self_repo]
        issue = self._new_issue(self.self_repo, number, title, body, list(labels), BOT)
        return copy.deepcopy(issue)

    def create_pull(self, repo, *, head, base, title, body) -> dict:
        self._record("create_pull", repo=repo, head=head, base=base, title=title, body=body)
        self._write("POST", f"/repos/{repo}/pulls",
                    {"head": head, "base": base, "title": title, "body": body})
        number = self._next_number.get(repo, 1000)
        self._next_number[repo] = number + 1
        head_ref = head.split(":")[-1]
        head_owner = head.split(":")[0] if ":" in head else repo.split("/")[0]
        pr = {
            "number": number, "node_id": f"PR_{number}", "state": "open", "title": title,
            "body": body, "html_url": f"https://github.com/{repo}/pull/{number}",
            "user": {"login": BOT},
            "head": {"ref": head_ref, "sha": "0" * 40, "label": head,
                     "repo": {"full_name": f"{head_owner}/{repo.split('/')[1]}"}},
            "base": {"ref": base, "repo": {"full_name": repo}},
            "requested_reviewers": [], "created_at": self._now(),
        }
        self.prs.setdefault(repo, {})[number] = pr
        self.comments.setdefault((repo, number), [])
        return copy.deepcopy(pr)

    def request_reviewers(self, repo, number, reviewers) -> dict:
        self._record("request_reviewers", repo=repo, number=number, reviewers=list(reviewers))
        self._write("POST", f"/repos/{repo}/pulls/{number}/requested_reviewers",
                    {"reviewers": list(reviewers)})
        pr = self._pr(repo, number)
        pr["requested_reviewers"] = [{"login": r} for r in reviewers]
        return copy.deepcopy(pr)

    def close_pull(self, repo, number) -> dict:
        self._record("close_pull", repo=repo, number=number)
        self._write("PATCH", f"/repos/{repo}/pulls/{number}", {"state": "closed"})
        pr = self._pr(repo, number)
        pr["state"] = "closed"
        return copy.deepcopy(pr)

    def create_branch_file(self, repo, *, branch, path, content, message, base="main") -> dict:
        self._record("create_branch_file", repo=repo, branch=branch, path=path,
                     content=content, message=message, base=base)
        self._write("PUT", f"/repos/{repo}/contents/{path}",
                    {"branch": branch, "message": message, "content": content, "base": base})
        self.branch_names.setdefault(repo, []).append(branch)
        return {"content": {"path": path}, "commit": {"sha": "c" * 40}}

    def push_branch(self, clone, branch, *, remote_repo, force=False, git_runner=None) -> None:
        self._record("push_branch", clone=str(clone), branch=branch, remote_repo=remote_repo,
                     force=bool(force))
        self._write("git push", f"https://github.com/{remote_repo}.git {branch}",
                    {"force": bool(force), "clone": str(clone)})

    def push_ref(self, repo_path, refspec, *, remote_repo, git_runner=None) -> None:
        self._record("push_ref", repo_path=str(repo_path), refspec=refspec,
                     remote_repo=remote_repo)
        self._write("git push", f"https://github.com/{remote_repo}.git {refspec}",
                    {"force": False, "repo_path": str(repo_path)})


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(T0)


@pytest.fixture
def gh(clock) -> FakeGh:
    return FakeGh(clock=clock)


@pytest.fixture
def store(tmp_path, gh, clock) -> GitHubStore:
    scratch = SqliteStore(tmp_path / "scratch.db", clock)
    scratch.migrate()
    return GitHubStore(gh, self_repo=SELF_REPO, scratch=scratch, clock=clock, run_url=RUN_URL)


def _create(store, ref="issue:816", title="Dashboard crashes on first render") -> int:
    return store.create_work_item(kind="issue", external_ref=ref, title=title, tier_required=0)


def _label_puts(gh) -> int:
    return len([s for s in gh.sent if s["method"] == "PUT" and s["url"].endswith("/labels")])


def _comment_calls(gh) -> int:
    return len(gh.calls_named("comment"))


def _meta_payload(body: str) -> dict | None:
    m = re.search(r"<!--\s*harness-meta\s*(\{.*?\})\s*-->", body, re.S)
    return json.loads(m.group(1)) if m else None


# --------------------------------------------------------------------------------------
# B100 - exactly one state label; a transition is one label call; two labels raise
# --------------------------------------------------------------------------------------
def test_B100_create_work_item_files_one_queued_issue_in_self_repo(gh, store):
    """B100: create_work_item creates an issue in self_repo carrying exactly one state label."""
    n = _create(store)
    assert isinstance(n, int)
    issue = gh.repos[SELF_REPO][n]
    assert issue["title"] == "Dashboard crashes on first render"
    # B227: the body is prose now, not the bare reference. What B100 pins is that the reference
    # is still there and still machine-findable -- `_origin_ref` is what makes duplicate
    # detection work, and it now reads the marker line rather than the first line.
    assert links.REF_MARKER in issue["body"]
    assert "issue:816" in issue["body"]
    assert _origin_ref(issue) == "issue:816"
    assert gh.state_labels(n) == ["harness:queued"]
    assert gh.repos[UPSTREAM] == {}, "an issue was filed outside self_repo (I-14)"
    item = store.get_work_item(n)
    assert item is not None
    assert item.id == n
    assert item.state == "discovered"
    assert item.kind == "issue"
    # B230: the reference the item was CREATED with. `self:<n>` was a synthetic stand-in, and
    # it cost the delivery pull request its `Closes` keyword: `issue_number` reads digits out of
    # this string, so `self:4` answered 4 -- the harness issue -- instead of the product's 633.
    assert item.external_ref == "issue:816"
    assert item.issue_number == 816
    assert item.title == "Dashboard crashes on first render"


def test_B100_transition_is_one_label_call_that_swaps_the_state_label_only(gh, store):
    """B100: transition = one set_labels call; old state label gone, new one present, the rest kept."""
    n = _create(store)
    gh.repos[SELF_REPO][n]["labels"].append({"name": "bug"})
    puts_before = _label_puts(gh)
    store.transition(n, "proposing", reason="propose starting")
    assert _label_puts(gh) - puts_before == 1
    assert gh.state_labels(n) == ["harness:proposing"]
    assert "bug" in gh.labels(n)
    assert store.get_work_item(n).state == "proposing"


CHAIN = ["proposing", "proposed", "approved", "implementing", "packaged", "shipped", "revising",
         "needs-human", "revising", "shipped", "merged"]


def test_B100_every_transition_in_a_full_lifecycle_leaves_exactly_one_state_label(gh, store):
    """B100: after each legal transition the issue carries exactly one harness:* label."""
    n = _create(store)
    for state in CHAIN:
        store.transition(n, state, reason=f"to {state}")
        assert gh.state_labels(n) == [LABELS[state]], state
        assert store.get_work_item(n).state == state


def test_B100_two_state_labels_make_get_work_item_raise_store_error(gh, store):
    """B100: an item with two harness:* state labels raises StoreError instead of guessing."""
    gh.add_issue(7, "ambiguous", labels=["harness:approved", "harness:running"])
    with pytest.raises(StoreError):
        store.get_work_item(7)


def test_B100_two_state_labels_make_transition_raise_and_change_nothing(gh, store):
    """B100: transition on a two-label item raises StoreError; labels and writes untouched."""
    gh.add_issue(7, "ambiguous", labels=["harness:approved", "harness:running"])
    sent_before = len(gh.sent)
    with pytest.raises(StoreError):
        store.transition(7, "implementing", reason="run")
    assert gh.labels(7) == ["harness:approved", "harness:running"]
    assert len(gh.sent) == sent_before


def test_B100_two_state_labels_are_not_listed_as_a_state(gh, store):
    """B100: list_work_items(state=...) never reports a two-label item as being in either state."""
    gh.add_issue(7, "ambiguous", labels=["harness:approved", "harness:running"])
    gh.add_issue(8, "clean", labels=["harness:approved"])
    for state in ("approved", "implementing"):
        try:
            ids = [i.id for i in store.list_work_items(state=state)]
        except StoreError:
            continue
        assert 7 not in ids, state


def test_B100_issue_without_a_state_label_is_invisible(gh, store):
    """B100: zero harness:* labels means not a work item: get returns None, list omits it."""
    gh.add_issue(8, "plain issue", labels=["bug"])
    gh.add_issue(9, "queued", labels=["harness:queued"])
    assert store.get_work_item(8) is None
    assert [i.id for i in store.list_work_items()] == [9]


def test_B100_transition_of_an_invisible_issue_is_refused_without_writes(gh, store):
    """B100: transitioning an issue with no state label raises a HarnessError and writes nothing."""
    gh.add_issue(8, "plain issue", labels=["bug"])
    with pytest.raises(HarnessError):
        store.transition(8, "proposing", reason="x")
    assert gh.labels(8) == ["bug"]
    assert gh.sent == []


# --------------------------------------------------------------------------------------
# B101 - every transition writes exactly one comment: stage, run URL, cost, resulting state
# --------------------------------------------------------------------------------------
def test_B101_transition_comment_names_stage_state_run_url_and_cost(gh, store):
    """B101: one comment per transition; it names the stage, the resulting state, run URL and $."""
    n = _create(store)
    run_id = store.start_stage_run(n, "propose", "fake")
    store.finish_stage_run(run_id, status="ok", turns=7, allowance_pct=None, cost_usd=0.42,
                           exit_reason=None, transcript_path=None)
    comments_before = _comment_calls(gh)
    posted_before = len(gh.comments_of(n))
    store.transition(n, "proposed", reason="proposal published")
    assert _comment_calls(gh) - comments_before == 1
    new = gh.comments_of(n)[posted_before:]
    assert len(new) == 1
    body = new[0]["body"]
    assert "propose" in body
    assert "proposed" in body
    assert RUN_URL in body
    assert "$0.42" in body


def test_B101_transition_without_a_stage_run_still_comments_with_a_dollar_cost(gh, store):
    """B101: with no stage run in this process the comment still carries the URL and $0.00."""
    n = _create(store)
    store.transition(n, "proposing", reason="x")  # discovered->blocked is not a legal pair (D1 5.2.2)
    comments_before = _comment_calls(gh)
    store.transition(n, "blocked", reason="operator hold")
    assert _comment_calls(gh) - comments_before == 1
    body = gh.comments_of(n)[-1]["body"]
    assert "blocked" in body
    assert RUN_URL in body
    assert "$0.00" in body


def test_B101_every_transition_in_a_lifecycle_adds_exactly_one_comment(gh, store):
    """B101: the issue thread is the event log - one comment call per transition, no more."""
    n = _create(store)
    for state in CHAIN:
        before = _comment_calls(gh)
        store.transition(n, state, reason=f"to {state}")
        assert _comment_calls(gh) - before == 1, state
        assert state in gh.comments_of(n)[-1]["body"]


def test_B101_illegal_transition_writes_no_comment(gh, store):
    """B101: a refused transition is not a transition - no comment, no label call."""
    n = _create(store)
    before_comments = _comment_calls(gh)
    before_puts = _label_puts(gh)
    with pytest.raises(IllegalTransition):
        store.transition(n, "shipped", reason="skip everything")
    assert _comment_calls(gh) == before_comments
    assert _label_puts(gh) == before_puts


# --------------------------------------------------------------------------------------
# B102 - a human-set label is honoured, not overwritten
# --------------------------------------------------------------------------------------
def test_B102_human_requeue_is_reflected_by_get_and_list(gh, store):
    """B102: a human moving blocked -> queued in the UI is read back as `discovered`."""
    n = _create(store)
    store.transition(n, "proposing", reason="x")
    store.transition(n, "blocked", reason="gates red")
    assert store.get_work_item(n).state == "blocked"
    gh.set_issue_labels(n, ["harness:queued"])
    assert store.get_work_item(n).state == "discovered"
    assert n in [i.id for i in store.list_work_items(state="discovered")]
    assert n not in [i.id for i in store.list_work_items(state="blocked")]


def test_B102_transition_from_a_human_set_state_is_validated_against_that_state(gh, store):
    """B102: after a human re-queue, proposed -> approved is refused because the item is queued."""
    n = _create(store)
    store.transition(n, "proposing", reason="x")
    store.transition(n, "proposed", reason="x")
    gh.set_issue_labels(n, ["harness:queued"])
    puts_before = _label_puts(gh)
    with pytest.raises(IllegalTransition):
        store.transition(n, "approved", reason="proposal merged")
    assert gh.state_labels(n) == ["harness:queued"]
    assert _label_puts(gh) == puts_before


def test_B102_transition_from_a_human_set_state_succeeds_when_legal(gh, store):
    """B102: after a human re-queue, discovered -> proposing is legal and lands one label."""
    n = _create(store)
    store.transition(n, "proposing", reason="x")
    store.transition(n, "proposed", reason="x")
    gh.set_issue_labels(n, ["harness:queued"])
    store.transition(n, "proposing", reason="re-run propose")
    assert gh.state_labels(n) == ["harness:proposing"]
    assert store.get_work_item(n).state == "proposing"


def test_B102_human_abandon_is_terminal_for_the_harness(gh, store):
    """B102: a human setting harness:abandoned wins; the harness cannot move it anywhere."""
    n = _create(store)
    gh.set_issue_labels(n, ["harness:abandoned"])
    for target in ("proposing", "discovered", "approved"):
        with pytest.raises(IllegalTransition):
            store.transition(n, target, reason="x")
    assert gh.state_labels(n) == ["harness:abandoned"]


# --------------------------------------------------------------------------------------
# create_work_item duplicates
# --------------------------------------------------------------------------------------
def test_create_work_item_duplicate_title_and_ref_raises_and_files_nothing(gh, store):
    """B100/section 3: a duplicate title+external_ref among open issues raises DuplicateWorkItem."""
    _create(store)
    count_before = len(gh.repos[SELF_REPO])
    with pytest.raises(DuplicateWorkItem):
        _create(store)
    assert len(gh.repos[SELF_REPO]) == count_before


def test_create_work_item_after_the_original_is_closed_files_a_new_issue(gh, store):
    """Section 3: the duplicate check is over OPEN issues; a closed twin does not block."""
    n = _create(store)
    gh.repos[SELF_REPO][n]["state"] = "closed"
    m = _create(store)
    assert m != n
    assert gh.state_labels(m) == ["harness:queued"]


# --------------------------------------------------------------------------------------
# update_work_item / get_work_item round trip through the hidden meta comment
# --------------------------------------------------------------------------------------
def test_update_work_item_round_trips_fields_through_the_meta_comment(gh, store):
    """Section 3: update_work_item posts a hidden meta comment that get_work_item merges."""
    n = _create(store)
    store.update_work_item(n, base_sha=SHA_A, branch_name=BRANCH, spec_path="runs/item-1/spec/1.md")
    item = store.get_work_item(n)
    assert item.base_sha == SHA_A
    assert item.branch_name == BRANCH
    assert item.spec_path == "runs/item-1/spec/1.md"
    metas = [_meta_payload(c["body"]) for c in gh.comments_of(n)]
    metas = [m for m in metas if m is not None]
    assert metas, "no <!-- harness-meta {...} --> comment was posted"
    assert metas[-1]["base_sha"] == SHA_A
    assert metas[-1]["branch_name"] == BRANCH
    assert metas[-1]["spec_path"] == "runs/item-1/spec/1.md"


def test_get_work_item_reads_meta_from_github_with_a_fresh_scratch(gh, store, clock, tmp_path):
    """Section 3: the scratch db is per-run; a new store on the same issue sees the meta values."""
    n = _create(store)
    store.update_work_item(n, base_sha=SHA_A, branch_name=BRANCH)
    scratch2 = SqliteStore(tmp_path / "scratch2.db", clock)
    scratch2.migrate()
    store2 = GitHubStore(gh, self_repo=SELF_REPO, scratch=scratch2, clock=clock, run_url=RUN_URL)
    item = store2.get_work_item(n)
    assert item is not None
    assert item.base_sha == SHA_A
    assert item.branch_name == BRANCH
    assert item.state == "discovered"


def test_update_work_item_latest_meta_wins(gh, store):
    """Section 3: two updates -> the most recent meta comment is the one merged."""
    n = _create(store)
    store.update_work_item(n, base_sha="1" * 40)
    store.update_work_item(n, base_sha="2" * 40)
    assert store.get_work_item(n).base_sha == "2" * 40


def test_update_work_item_on_a_two_label_item_raises(gh, store):
    """B100: even a metadata update refuses to operate on an ambiguous two-label item."""
    gh.add_issue(7, "ambiguous", labels=["harness:approved", "harness:running"])
    with pytest.raises(StoreError):
        store.update_work_item(7, base_sha=SHA_A)


# --------------------------------------------------------------------------------------
# list / merged_issues
# --------------------------------------------------------------------------------------
def test_list_work_items_filters_by_state_label(gh, store):
    """Section 3: list_work_items(state=) selects by label; no filter lists every work item."""
    gh.add_issue(11, "a", labels=["harness:approved"])
    gh.add_issue(12, "b", labels=["harness:approved", "bug"])
    gh.add_issue(13, "c", labels=["harness:shipped"])
    gh.add_issue(14, "d", labels=["bug"])
    assert sorted(i.id for i in store.list_work_items(state="approved")) == [11, 12]
    assert [i.id for i in store.list_work_items(state="shipped")] == [13]
    assert [i.id for i in store.list_work_items(state="merged")] == []
    assert sorted(i.id for i in store.list_work_items()) == [11, 12, 13]


def test_merged_issues_returns_numbers_labelled_merged(gh, store):
    """Section 3: merged_issues() is the set of issue numbers labelled harness:merged."""
    gh.add_issue(21, "a", labels=["harness:merged"])
    gh.add_issue(22, "b", labels=["harness:merged", "bug"])
    gh.add_issue(23, "c", labels=["harness:shipped"])
    gh.add_issue(24, "d", labels=["bug"])
    assert store.merged_issues() == {21, 22}


def test_merged_issues_is_empty_when_nothing_is_merged(gh, store):
    """Section 3: no harness:merged label anywhere -> empty set, not None."""
    gh.add_issue(23, "c", labels=["harness:shipped"])
    assert store.merged_issues() == set()


# --------------------------------------------------------------------------------------
# publish_proposal
# --------------------------------------------------------------------------------------
def test_publish_proposal_creates_branch_file_and_pr_then_marks_proposed(gh, store):
    """Section 3 / B103 path: branch file on harness/propose-<id>, PR to main, proposed label."""
    n = _create(store)
    store.transition(n, "proposing", reason="propose")
    filename = f"{n}-dashboard-crashes-on-first-render.md"
    url = store.publish_proposal(n, filename, PROPOSAL_TEXT)
    cbf = gh.calls_named("create_branch_file")
    assert len(cbf) == 1
    assert cbf[0]["repo"] == SELF_REPO
    assert cbf[0]["branch"] == f"harness/propose-{n}"
    assert cbf[0]["path"] == f"proposals/{filename}"
    assert cbf[0]["content"] == PROPOSAL_TEXT
    assert cbf[0]["message"]
    cp = gh.calls_named("create_pull")
    assert len(cp) == 1
    assert cp[0]["repo"] == SELF_REPO
    assert cp[0]["head"] == f"harness/propose-{n}"
    assert cp[0]["base"] == "main"
    assert url in {p["html_url"] for p in gh.prs[SELF_REPO].values()}
    assert gh.state_labels(n) == ["harness:proposed"]
    assert store.get_work_item(n).state == "proposed"


def test_publish_proposal_never_touches_the_product_repo(gh, store):
    """I-14 / section 3: the proposal PR and branch file land in self_repo only."""
    n = _create(store)
    store.transition(n, "proposing", reason="propose")
    store.publish_proposal(n, f"{n}-x.md", PROPOSAL_TEXT)
    assert gh.prs[UPSTREAM] == {}
    assert all(c["repo"] == SELF_REPO for c in gh.calls
               if c["name"] in ("create_branch_file", "create_pull"))


def test_publish_proposal_from_a_wrong_state_raises_and_keeps_the_label(gh, store):
    """Section 3: publish transitions proposing -> proposed; from approved that is illegal."""
    n = _create(store)
    gh.set_issue_labels(n, ["harness:approved"])
    with pytest.raises(IllegalTransition):
        store.publish_proposal(n, f"{n}-x.md", PROPOSAL_TEXT)
    assert gh.state_labels(n) == ["harness:approved"]


# --------------------------------------------------------------------------------------
# B147 - reconciliation of items a dead run left mid-flight
# --------------------------------------------------------------------------------------
def test_B147_reconcile_resets_stale_implementing_and_leaves_fresh_alone(gh, store, clock):
    """B147: an `implementing` item older than the cutoff goes back to approved; a fresh one stays."""
    a = _create(store, ref="issue:816", title="stale")
    gh.set_issue_labels(a, ["harness:approved"])
    store.transition(a, "implementing", reason="run start")          # at 12:00
    b = _create(store, ref="issue:823", title="fresh")
    gh.set_issue_labels(b, ["harness:approved"])
    clock.advance(3.5 * 3600)                                          # 15:30
    store.transition(b, "implementing", reason="run start")
    clock.advance(0.5 * 3600)                                          # 16:00
    cutoff = iso(clock.now() - timedelta(hours=3))                     # 13:00
    reset = store.reconcile_stale_running(cutoff)
    assert reset == [a]
    assert gh.state_labels(a) == ["harness:approved"]
    assert store.get_work_item(a).state == "approved"
    assert gh.state_labels(b) == ["harness:running"]
    assert store.get_work_item(b).state == "implementing"


def test_B147_reconcile_resets_stale_revising_to_its_previous_state(gh, store, clock):
    """B147: a `revising` item stranded by a dead run returns to `shipped`."""
    n = _create(store)
    gh.set_issue_labels(n, ["harness:shipped"])
    store.transition(n, "revising", reason="revise start")
    clock.advance(4 * 3600)
    reset = store.reconcile_stale_running(iso(clock.now() - timedelta(hours=3)))
    assert reset == [n]
    assert gh.state_labels(n) == ["harness:shipped"]


def test_B147_reconcile_with_nothing_stale_returns_empty_and_writes_nothing(gh, store, clock):
    """B147: fresh in-flight items and settled items are untouched; the call is a read."""
    n = _create(store)
    gh.set_issue_labels(n, ["harness:approved"])
    store.transition(n, "implementing", reason="run start")
    clock.advance(600)
    sent_before = len(gh.sent)
    assert store.reconcile_stale_running(iso(clock.now() - timedelta(hours=3))) == []
    assert len(gh.sent) == sent_before
    assert gh.state_labels(n) == ["harness:running"]


def test_B147_reconcile_ignores_old_items_that_are_not_in_flight(gh, store, clock):
    """B147: only implementing / revising / proposing are reconciled; an old approved item stays."""
    n = _create(store)
    store.transition(n, "proposing", reason="x")
    store.transition(n, "proposed", reason="x")
    store.transition(n, "approved", reason="merged proposal")
    m = _create(store, ref="issue:900", title="parked")
    store.transition(m, "proposing", reason="x")  # discovered->blocked is not a legal pair (D1 5.2.2)
    store.transition(m, "blocked", reason="hold")
    clock.advance(24 * 3600)
    assert store.reconcile_stale_running(iso(clock.now() - timedelta(hours=3))) == []
    assert gh.state_labels(n) == ["harness:approved"]
    assert gh.state_labels(m) == ["harness:blocked"]


# --------------------------------------------------------------------------------------
# Illegal transitions
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("target", ["implementing", "packaged", "shipped", "revising",
                                    "needs-human", "merged"])
def test_illegal_transition_from_discovered_raises_and_changes_no_label(gh, store, target):
    """Section 3 table / B11: a pair absent from the unified table raises IllegalTransition."""
    n = _create(store)
    puts_before = _label_puts(gh)
    with pytest.raises(IllegalTransition):
        store.transition(n, target, reason="skip ahead")
    assert gh.state_labels(n) == ["harness:queued"]
    assert _label_puts(gh) == puts_before


@pytest.mark.parametrize("terminal", ["merged", "abandoned"])
def test_terminal_states_reject_every_transition(gh, store, terminal):
    """Section 3 table: merged and abandoned are terminal; every target raises."""
    gh.add_issue(31, "done", labels=[LABELS[terminal]])
    for target in STATES:
        with pytest.raises(IllegalTransition):
            store.transition(31, target, reason="x")
    assert gh.state_labels(31) == [LABELS[terminal]]


def test_transition_to_an_unknown_state_raises(gh, store):
    """Section 3: a state outside STATES is never written as a label."""
    n = _create(store)
    with pytest.raises(HarnessError):
        store.transition(n, "flying", reason="x")
    assert gh.state_labels(n) == ["harness:queued"]


# --------------------------------------------------------------------------------------
# Delegation and the client surface
# --------------------------------------------------------------------------------------
def test_stage_runs_delegate_to_scratch_and_write_nothing_to_github(gh, store):
    """Section 3: stage runs live in the scratch db; starting/finishing one sends no write."""
    n = _create(store)
    sent_before = len(gh.sent)
    rid = store.start_stage_run(n, "propose", "fake")
    store.finish_stage_run(rid, status="ok", turns=3, allowance_pct=None, cost_usd=0.42,
                           exit_reason=None, transcript_path=None)
    runs = store.list_stage_runs(work_item_id=n)
    assert len(runs) == 1
    assert runs[0].status == "ok"
    assert runs[0].cost_usd == 0.42
    assert len(gh.sent) == sent_before


def test_store_only_uses_the_section_7_client_surface(gh, store, clock):
    """Section 7 / I-11: every method the store calls on the client is a section-7 name."""
    n = _create(store)
    store.update_work_item(n, base_sha=SHA_A, branch_name=BRANCH)
    store.get_work_item(n)
    store.list_work_items(state="discovered")
    store.transition(n, "proposing", reason="x")
    store.publish_proposal(n, f"{n}-x.md", PROPOSAL_TEXT)
    store.merged_issues()
    clock.advance(4 * 3600)
    store.reconcile_stale_running(iso(clock.now() - timedelta(hours=3)))
    names = {c["name"] for c in gh.calls}
    assert names <= GH_SURFACE, names - GH_SURFACE
    assert names & WRITE_METHODS, "the store never wrote through the client"
    assert not any(w in name for name in names for w in ("merge", "approve", "dismiss"))


# --------------------------------------------------------------------------------------
# B230 - the work item keeps the reference it was created with (D50)
# --------------------------------------------------------------------------------------


def test_b230_the_product_issue_number_survives_a_round_trip(gh, store):
    """B230: `deliver` decides whether to write `Closes` by asking whether the reference starts
    `issue:`. A synthetic `self:<n>` answers no, and the pull request silently loses it."""
    n = _create(store)

    item = store.get_work_item(n)

    assert item.external_ref.startswith("issue:")
    assert item.issue_number == 816, "the PRODUCT issue, not the harness issue"


def test_b230_a_body_in_the_old_format_still_resolves(gh, store):
    """B230: items opened before the bodies became prose carry the reference as their first
    line and no marker; those must keep working."""
    n = _create(store)
    gh.repos[SELF_REPO][n]["body"] = "issue:816"

    assert store.get_work_item(n).external_ref == "issue:816"


def test_b230_an_issue_with_no_recoverable_reference_falls_back_to_itself(gh, store):
    """B230: an issue a human opened by hand and labelled has no reference at all; naming
    itself is the honest answer, and it is what find_by_ref already understands."""
    n = _create(store)
    gh.repos[SELF_REPO][n]["body"] = ""
    for comment in gh.comments.get((SELF_REPO, n), []):
        comment["body"] = "not meta"

    assert store.get_work_item(n).external_ref == f"self:{n}"
