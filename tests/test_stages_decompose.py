"""Delivery 2 - the `decompose` stage.

Behaviors under test: B110 (N sub-issues in THIS repository, each queued and linked to the
parent; parent blocked with a comment listing them; never an issue on the product repo, I-14)
and B111 (bounded by max_subissues; a sub-issue is never decomposed - depth is one).

Fixtures are inline on purpose. Nothing here touches the network or the wall clock.
"""
from __future__ import annotations

import copy
import json
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.clock import FrozenClock, iso
from harness.clone import Lease
from harness.config import load_config
from harness.context import build_context
from harness.errors import GitHubError, HarnessError, TierViolation
from harness.ledger import Ledger
from harness.runner.fake import FakeRunner
from harness.stages.decompose import decompose
from harness.store.github import GitHubStore
from harness.store.sqlite import SqliteStore

SELF_REPO = "jgoetzmann/bright-bots-harness"
UPSTREAM = "Bright-Bots-Initiative/brightboost"
FORK = "brightboost-harness/brightboost"
BOT = "brightboost-harness"
RUN_URL = "https://github.com/jgoetzmann/bright-bots-harness/actions/runs/4242"
T0 = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
PARENT = 40
PARENT_TITLE = "Dashboard has several unrelated defects"
PARENT_BODY = ("Three things are wrong on the dashboard page and they do not share a cause:\n"
               "the auth reducer is untested, the page crashes with no user, and the API base\n"
               "URL env var is undocumented.")
SUB_TITLES = (
    "Add unit tests for the auth reducer",
    "Guard the null user on the dashboard",
    "Document VITE_API_URL",
)
SUB_BODIES = (
    "cover login and logout transitions in src/store/auth.test.ts",
    "src/pages/Dashboard.tsx:42 dereferences user.name before the session resolves",
    "the README never says which env var the client reads",
)
DECOMPOSE_TEXT = "\n".join(f"{i + 1}. {t} — {b}" for i, (t, b) in
                           enumerate(zip(SUB_TITLES, SUB_BODIES))) + "\n"

WRITE_METHODS = frozenset({
    "comment", "set_labels", "create_issue", "create_pull", "request_reviewers", "close_pull",
    "create_branch_file", "push_branch", "push_ref",
})

BASE_ENV = {
    "BACKEND": "fake",
    "REPO": UPSTREAM,
    "PERMISSION_TIER": "2",
    "ALLOWLIST_LABEL": "harness-ok",
    "WEEKLY_BUDGET_PCT": "90",
    "SESSION_BUDGET_PCT": "90",
    "RESERVE_PCT": "5",
    "WEEKLY_RESET_DAY": "monday",
    "MAX_CONCURRENT_CLONES": "1",
    "MAX_TURNS_DISCOVER": "10",
    "MAX_TURNS_PROPOSE": "30",
    "MAX_TURNS_IMPLEMENT": "80",
    "MAX_TURNS_PACKAGE": "10",
    "MAX_RETRIES_GATES": "3",
    "GITHUB_API_CEILING_PER_HOUR": "500",
    "MIN_FREE_DISK_GB": "1",
    "DB_PATH": "harness.db",
    "RUNS_DIR": "runs",
    "PACKAGES_DIR": "packages",
    "HALT_FILE": "HALT",
    "FULLSEND_ENABLED": "false",
    "HARNESS_GITHUB_TOKEN": "",
    "ANTHROPIC_API_KEY": "",
    "WEEKLY_CAP_USD": "100.00",
    "PER_CALL_CAP_USD": "3.00",
    "MAX_CONCURRENT_ITEMS": "1",
    "MAX_REVISE_CYCLES": "3",
    "FORK_REPO": FORK,
    "UPSTREAM_REPO": UPSTREAM,
    "TRUST_FILE": "trust.txt",
    "NOTIFY_POLL_HOURS": "3",
    "MAX_SUBISSUES": "8",
    "SELF_REPO": SELF_REPO,
    "TRACKING_ISSUE": "",
    "STORE_BACKEND": "github",
}


# --------------------------------------------------------------------------------------
# Inline fixtures
# --------------------------------------------------------------------------------------
def _w(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def make_config(tmp_path: Path, **overrides):
    values = dict(BASE_ENV)
    values.update(overrides)
    env = _w(tmp_path / ".env", "".join(f"{k}={v}\n" for k, v in values.items()))
    _w(tmp_path / "trust.txt", "# handles whose keywords are honoured\njgoetzmann\n")
    return load_config(env, environ={})


class FakeGh:
    """Stand-in for `harness.gh.GitHubClient` exposing exactly the section-7 surface.

    Issues live in `repos[repo][number]` (labels as `[{"name": ...}]`); comments, label
    events, reviews and review comments are keyed by `(repo, number)`. Every method call is
    appended to `calls` with its bound arguments; every write is also appended to `sent` as
    `{"method", "url", "payload"}`, like the real client. `create_issue` has no repo
    parameter at all (I-14): a caller passing one gets TypeError.
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

    def set_issue_labels(self, number, labels, *, repo=None) -> None:
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


class FakeClones:
    """Hands out a Lease on a directory; decompose must never need it, but Context wants one."""

    def __init__(self, repo_path: Path) -> None:
        self.repo_path = repo_path
        self.acquired: list[dict] = []
        self.released: list[tuple[Lease, bool]] = []

    def preflight(self) -> list[str]:
        return []

    def acquire(self, item, *, branch=None, from_fork=False) -> Lease:
        self.acquired.append({"item": item.id, "branch": branch, "from_fork": from_fork})
        return Lease(run_id=f"item-{item.id}", path=self.repo_path, base_sha="0" * 40,
                     branch=branch or "harness/chore-0-none")

    def release(self, lease: Lease, *, keep: bool) -> None:
        self.released.append((lease, keep))


class RecordingRunner:
    """Wraps a FakeRunner and keeps every RunRequest it was asked to run."""

    name = "fake"

    def __init__(self, inner) -> None:
        self.inner = inner
        self.requests: list = []

    def run(self, request):
        self.requests.append(request)
        return self.inner.run(request)

    def prompt_text(self) -> str:
        return "\n".join(f"{r.prompt or ''}\n{r.system_prompt or ''}" for r in self.requests)


def write_fixtures(dir_: Path, *, decompose_text: str | None = DECOMPOSE_TEXT) -> Path:
    base = {"turns": 2, "cost_usd": 0.12, "allowance_pct": None, "duration_ms": 800,
            "session_id": "sess-2", "exit_code": 0, "transcript": [], "error": None,
            "reset_at": None}
    if decompose_text is not None:
        _w(dir_ / "decompose.json", json.dumps({"ok": True, "text": decompose_text, **base}))
    _w(dir_ / "rate_limited.json", json.dumps({
        "ok": False, "text": "", "turns": None, "cost_usd": 0.0, "allowance_pct": None,
        "duration_ms": 40, "session_id": None, "exit_code": 1, "transcript": [],
        "error": "Claude usage limit reached. Resets at 2026-09-02T13:30:00Z",
        "reset_at": "2026-09-02T13:30:00Z", "rate_limited": True}))
    return dir_


def setup_decompose(tmp_path: Path, *, parent_body: str = PARENT_BODY,
                    parent_labels=("harness:queued",), decompose_text=DECOMPOSE_TEXT,
                    **env_overrides):
    config = make_config(tmp_path, **env_overrides)
    clock = FrozenClock(T0)
    gh = FakeGh(clock=clock)
    scratch = SqliteStore(tmp_path / "scratch.db", clock)
    scratch.migrate()
    store = GitHubStore(gh, self_repo=SELF_REPO, scratch=scratch, clock=clock, run_url=RUN_URL)
    gh.add_issue(PARENT, PARENT_TITLE, body=parent_body, labels=list(parent_labels))
    gh.calls.clear()
    gh.sent.clear()
    runner = RecordingRunner(FakeRunner(write_fixtures(tmp_path / "fixtures",
                                                       decompose_text=decompose_text)))
    ledger = Ledger.empty("2026-08-31T00:00:00Z")
    clones = FakeClones(tmp_path / "unused-clone")
    ctx = build_context(config, run_id=f"item-{PARENT}", runner=runner, gh=gh, clock=clock,
                        store=store, clones=clones, ledger=ledger,
                        trusted=frozenset({"jgoetzmann"}))
    return SimpleNamespace(ctx=ctx, gh=gh, store=store, runner=runner, ledger=ledger,
                           config=config)


def created_numbers(gh, before: set[int]) -> list[int]:
    return sorted(set(gh.repos[SELF_REPO]) - before)


# --------------------------------------------------------------------------------------
# B110 - N sub-issues here, each queued and linked; parent blocked and commented
# --------------------------------------------------------------------------------------
def test_B110_decompose_creates_queued_sub_issues_linked_to_the_parent(tmp_path):
    """B110: N sub-issues in self_repo, each harness:queued, each body carries Parent: #<parent>."""
    s = setup_decompose(tmp_path)
    before = set(s.gh.repos[SELF_REPO])
    children = decompose(s.ctx, PARENT)
    new = created_numbers(s.gh, before)
    assert len(new) == 3
    assert sorted(children) == new
    for n in new:
        issue = s.gh.repos[SELF_REPO][n]
        assert s.gh.state_labels(n) == ["harness:queued"]
        assert f"Parent: #{PARENT}" in issue["body"]
        assert s.store.get_work_item(n).state == "discovered"


def test_B110_parent_is_blocked_with_a_comment_listing_the_children(tmp_path):
    """B110: the parent ends harness:blocked and its thread lists every child number."""
    s = setup_decompose(tmp_path)
    comments_before = len(s.gh.comments_of(PARENT))
    children = decompose(s.ctx, PARENT)
    assert s.gh.state_labels(PARENT) == ["harness:blocked"]
    assert s.store.get_work_item(PARENT).state == "blocked"
    new_comments = s.gh.comments_of(PARENT)[comments_before:]
    assert new_comments
    joined = "\n".join(c["body"] for c in new_comments)
    for n in children:
        assert f"#{n}" in joined


def test_B110_sub_issue_titles_and_bodies_come_from_the_numbered_lines(tmp_path):
    """B110 / section 13: `N. <title> - <body>` lines become the sub-issues, in order."""
    s = setup_decompose(tmp_path)
    before = set(s.gh.repos[SELF_REPO])
    children = decompose(s.ctx, PARENT)
    new = created_numbers(s.gh, before)
    assert children == new
    titles = [s.gh.repos[SELF_REPO][n]["title"] for n in new]
    assert titles == list(SUB_TITLES)
    for n, body in zip(new, SUB_BODIES):
        assert body in s.gh.repos[SELF_REPO][n]["body"]


def test_B110_create_issue_is_called_with_title_body_labels_and_never_a_repo(tmp_path):
    """B110 / I-14: every create_issue call is (title, body, labels); the fake has no repo slot."""
    s = setup_decompose(tmp_path)
    decompose(s.ctx, PARENT)
    calls = s.gh.calls_named("create_issue")
    assert len(calls) == 3
    for c in calls:
        assert set(c) == {"name", "title", "body", "labels"}
        assert c["labels"] == ["harness:queued"]
        assert f"Parent: #{PARENT}" in c["body"]
    for entry in s.gh.sent:
        if entry["method"] == "POST" and entry["url"].endswith("/issues"):
            assert entry["url"] == f"/repos/{SELF_REPO}/issues"


def test_B110_decompose_never_files_anything_on_the_product_repo(tmp_path):
    """B110 / I-14: the product repository gains no issue, no PR, no comment."""
    s = setup_decompose(tmp_path)
    decompose(s.ctx, PARENT)
    assert s.gh.repos[UPSTREAM] == {}
    assert s.gh.prs[UPSTREAM] == {}
    assert all(c["repo"] == SELF_REPO for c in s.gh.calls
               if c["name"] in ("comment", "set_labels"))
    assert s.gh.calls_named("create_pull") == []
    assert s.gh.calls_named("push_branch") == []


def test_B110_the_model_sees_the_parent_issue_and_the_bound(tmp_path):
    """B110 / section 13: prompts/decompose.md is rendered with the issue title and body."""
    s = setup_decompose(tmp_path)
    decompose(s.ctx, PARENT)
    assert len(s.runner.requests) == 1
    text = s.runner.prompt_text()
    assert PARENT_TITLE in text
    assert "the auth reducer is untested" in text


# --------------------------------------------------------------------------------------
# B111 - bounded: at most max_subissues, and depth is one
# --------------------------------------------------------------------------------------
def test_B111_at_most_max_subissues_are_created(tmp_path):
    """B111: MAX_SUBISSUES=2 with three numbered lines -> exactly two sub-issues."""
    s = setup_decompose(tmp_path, MAX_SUBISSUES="2")
    before = set(s.gh.repos[SELF_REPO])
    children = decompose(s.ctx, PARENT)
    new = created_numbers(s.gh, before)
    assert len(new) == 2
    assert sorted(children) == new
    assert len(s.gh.calls_named("create_issue")) == 2
    assert s.gh.state_labels(PARENT) == ["harness:blocked"]


def test_B111_a_sub_issue_is_refused_without_a_model_call_or_any_issue(tmp_path):
    """B111: an issue whose body says Parent: #N is never decomposed; nothing runs, nothing is filed."""
    s = setup_decompose(tmp_path, parent_body=f"Parent: #7\n\nGuard the null user on the dashboard.")
    before = set(s.gh.repos[SELF_REPO])
    with pytest.raises(HarnessError):
        decompose(s.ctx, PARENT)
    assert s.runner.requests == []
    assert s.gh.calls_named("create_issue") == []
    assert created_numbers(s.gh, before) == []
    assert s.gh.state_labels(PARENT) == ["harness:queued"]
    assert s.gh.sent == []


def test_B111_depth_is_one_end_to_end(tmp_path):
    """B111: a child produced by decompose is itself refused by decompose."""
    s = setup_decompose(tmp_path)
    children = decompose(s.ctx, PARENT)
    assert children
    child = children[0]
    count_before = len(s.gh.repos[SELF_REPO])
    runs_before = len(s.runner.requests)
    with pytest.raises(HarnessError):
        decompose(s.ctx, child)
    assert len(s.gh.repos[SELF_REPO]) == count_before
    assert len(s.runner.requests) == runs_before
    assert s.gh.state_labels(child) == ["harness:queued"]


def test_B111_a_failed_model_call_files_nothing(tmp_path):
    """B110/B111: with no decompose result (missing fixture -> ok=False) no sub-issue is created."""
    s = setup_decompose(tmp_path, decompose_text=None)
    before = set(s.gh.repos[SELF_REPO])
    try:
        decompose(s.ctx, PARENT)
    except HarnessError:
        pass
    assert s.gh.calls_named("create_issue") == []
    assert created_numbers(s.gh, before) == []


def test_B111_text_without_numbered_lines_files_nothing(tmp_path):
    """B110/B111: model text with no `N. title - body` lines yields no sub-issue."""
    s = setup_decompose(tmp_path, decompose_text="I could not find independent parts here.\n")
    before = set(s.gh.repos[SELF_REPO])
    try:
        result = decompose(s.ctx, PARENT)
    except HarnessError:
        result = []
    assert result == []
    assert s.gh.calls_named("create_issue") == []
    assert created_numbers(s.gh, before) == []


def test_B111_max_subissues_is_not_a_target(tmp_path):
    """B111: the bound caps; it never pads. Three lines under MAX_SUBISSUES=8 -> three issues."""
    s = setup_decompose(tmp_path, MAX_SUBISSUES="8")
    before = set(s.gh.repos[SELF_REPO])
    decompose(s.ctx, PARENT)
    assert len(created_numbers(s.gh, before)) == 3
