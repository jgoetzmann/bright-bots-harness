"""Delivery 2 - `clone.sync_fork` and the `deliver` stage.

Behaviors under test: B105, B106, B108, B109 (I-12), plus the `deliver` contract frozen in
`.fullsend/RUN-DECISIONS-D2.md` section 13 (can_write=False path, reviewers, shipped, entry
state).

All git repositories are throwaway local repos under `tmp_path`; the only network URL the
code may build is rewritten to a local path by the injected `git_runner`, and any URL that
survives that rewrite is refused. Fixtures are inline on purpose.
"""
from __future__ import annotations

import copy
import json
import re
import subprocess
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.clock import FrozenClock, iso
from harness.clone import CloneManager, Lease, sync_fork
from harness.config import load_config
from harness.context import build_context
from harness.errors import ForkDiverged, GitHubError, HarnessError, IllegalTransition, TierViolation
from harness.gh import GitHubClient
from harness.ledger import Ledger
from harness.runner.fake import FakeRunner
from harness.stages.deliver import deliver
from harness.store import LABELS, WorkItem
from harness.store.github import GitHubStore
from harness.store.sqlite import SqliteStore

SELF_REPO = "jgoetzmann/bright-bots-harness"
UPSTREAM = "Bright-Bots-Initiative/brightboost"
FORK = "brightboost-harness/brightboost"
BOT = "brightboost-harness"
RUN_URL = "https://github.com/jgoetzmann/bright-bots-harness/actions/runs/4242"
T0 = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
ITEM = 816
BRANCH = "harness/fix-816-dashboard-crashes-on-first-render"
TITLE = "Dashboard crashes on first render"
HARNESS_EMAIL = "harness@brightboost-harness"
DEV_EMAIL = "dev@example.com"
FAKE_SECRET = "ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
DIAGNOSIS_SENTINEL = "DIAGNOSIS-SENTINEL-4c1e"
EVIDENCE_SENTINEL = "EVIDENCE-SENTINEL-9b2d"

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
# The writes deliver is allowed per handoff section 4.5 / 5.3 (push, PR, reviewers, comment+label).
DELIVER_WRITES = frozenset({"push_branch", "push_ref", "create_pull", "request_reviewers",
                            "comment", "set_labels"})

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
# Inline fixtures: config, git helpers, FakeGh, FakeClones, RecordingRunner
# --------------------------------------------------------------------------------------
def _w(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def make_config(tmp_path: Path, *, trusted=("jgoetzmann",), **overrides):
    values = dict(BASE_ENV)
    values.update(overrides)
    env = _w(tmp_path / ".env", "".join(f"{k}={v}\n" for k, v in values.items()))
    _w(tmp_path / "trust.txt", "# handles whose keywords are honoured\n"
       + "".join(f"{h}\n" for h in trusted))
    return load_config(env, environ={})


def _git(*args: str, cwd: Path) -> str:
    argv = ["git", "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args]
    p = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"{' '.join(argv)} failed in {cwd}: {p.stderr}")
    return p.stdout.strip()


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", cwd=path)
    _git("symbolic-ref", "HEAD", "refs/heads/main", cwd=path)
    return path


def _commit(repo: Path, filename: str, content: str, message: str, *, email: str) -> str:
    _w(repo / filename, content)
    _git("add", "-A", cwd=repo)
    _git("-c", f"user.email={email}", "-c", "user.name=someone", "commit", "-q", "-m", message,
         cwd=repo)
    return _git("rev-parse", "HEAD", cwd=repo)


def make_sync_repos(tmp_path: Path):
    """upstream.git and fork.git (bare), identical at u0; a working clone to advance upstream."""
    work = _init_repo(tmp_path / "upstream-work")
    u0 = _commit(work, "README.md", "# brightboost\n", "chore: initial", email=DEV_EMAIL)
    upstream = tmp_path / "upstream.git"
    _git("init", "-q", "--bare", str(upstream), cwd=tmp_path)
    _git("push", "-q", str(upstream), "main:refs/heads/main", cwd=work)
    fork = tmp_path / "fork-remote.git"
    _git("clone", "-q", "--bare", str(upstream), str(fork), cwd=tmp_path)
    return SimpleNamespace(work=work, upstream=upstream, fork=fork, u0=u0)


def advance_upstream(r, message="feat: upstream moves on") -> str:
    sha = _commit(r.work, "src/app.ts", f"export const v = '{message}';\n", message,
                  email=DEV_EMAIL)
    _git("push", "-q", str(r.upstream), "main:refs/heads/main", cwd=r.work)
    return sha


def commit_on_fork_main(r, tmp_path: Path) -> str:
    fork_work = tmp_path / "fork-work"
    _git("clone", "-q", str(r.fork), str(fork_work), cwd=tmp_path)
    sha = _commit(fork_work, "README.md", "# brightboost (fork)\n", "docs: fork readme",
                  email=DEV_EMAIL)
    _git("push", "-q", str(r.fork), "main:refs/heads/main", cwd=fork_work)
    return sha


def main_of(bare: Path) -> str:
    return _git("rev-parse", "refs/heads/main", cwd=bare)


def make_git_runner(r, log: list):
    """The section-8 `git_runner` seam: rewrite the two GitHub URLs to local bare repos."""
    mapping = {
        f"https://github.com/{FORK}.git": str(r.fork),
        f"https://github.com/{FORK}": str(r.fork),
        f"https://github.com/{UPSTREAM}.git": str(r.upstream),
        f"https://github.com/{UPSTREAM}": str(r.upstream),
    }

    def run(argv, cwd=None):
        argv = [mapping.get(str(a), str(a)) for a in argv]
        log.append(argv)
        for a in argv:
            if a.startswith("https://") or a.startswith("git@"):
                return (128, "", f"network disabled in tests: {a}")
        p = subprocess.run(argv, cwd=str(cwd) if cwd else None, capture_output=True, text=True)
        return (p.returncode, p.stdout, p.stderr)

    return run


def make_push(r, pushes: list):
    """The section-8 `push` callable: record, then really fast-forward the local fork."""

    def push(repo_path, refspec):
        pushes.append((Path(repo_path), refspec))
        src, _, dst = refspec.partition(":")
        sha = None
        for cand in (src, f"refs/remotes/{src}"):
            p = subprocess.run(["git", "rev-parse", "--verify", "-q", f"{cand}^{{commit}}"],
                               cwd=str(repo_path), capture_output=True, text=True)
            if p.returncode == 0:
                sha = p.stdout.strip()
                break
        assert sha, f"{src} does not resolve inside {repo_path}"
        _git("push", "-q", str(r.fork), f"{sha}:{dst or 'refs/heads/main'}", cwd=Path(repo_path))

    return push


class FakeGh:
    """Stand-in for `harness.gh.GitHubClient` exposing exactly the section-7 surface.

    Issues live in `repos[repo][number]` (labels as `[{"name": ...}]`); comments, label
    events, reviews and review comments are keyed by `(repo, number)`. Every method call is
    appended to `calls` with its bound arguments; every write is also appended to `sent` as
    `{"method", "url", "payload"}`, like the real client. There is no merge, approve or
    dismiss here: a caller reaching for one gets `AttributeError`.
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

    def add_pull(self, number, *, head_ref, head_sha, base_sha, repo=None, title="", body="",
                 user=BOT) -> dict:
        repo = repo or self.repo
        pr = {
            "number": number, "node_id": f"PR_{number}", "state": "open", "title": title,
            "body": body, "html_url": f"https://github.com/{repo}/pull/{number}",
            "user": {"login": user},
            "head": {"ref": head_ref, "sha": head_sha, "label": f"{BOT}:{head_ref}",
                     "repo": {"full_name": FORK}},
            "base": {"ref": "main", "sha": base_sha, "repo": {"full_name": repo}},
            "requested_reviewers": [], "created_at": self._now(),
        }
        self.prs.setdefault(repo, {})[number] = pr
        self.comments.setdefault((repo, number), [])
        return pr

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
    """Hands out a Lease on a real local git repo; records acquire/release calls."""

    def __init__(self, repo_path: Path, base_sha: str, branch: str) -> None:
        self.repo_path = repo_path
        self.base_sha = base_sha
        self.branch = branch
        self.acquired: list[dict] = []
        self.released: list[tuple[Lease, bool]] = []

    def preflight(self) -> list[str]:
        return []

    def acquire(self, item, *, branch=None, from_fork=False) -> Lease:
        self.acquired.append({"item": item.id, "branch": branch, "from_fork": from_fork})
        return Lease(run_id=f"item-{item.id}", path=self.repo_path, base_sha=self.base_sha,
                     branch=branch or self.branch)

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


def write_fixtures(dir_: Path) -> Path:
    base = {"turns": 3, "cost_usd": 0.31, "allowance_pct": None, "duration_ms": 1200,
            "session_id": "sess-1", "exit_code": 0, "transcript": [], "error": None,
            "reset_at": None}
    for stage, text in (("package", "packaged"), ("implement", "done"),
                        ("diagnose_gate_failure", "guard the selector")):
        _w(dir_ / f"{stage}.json", json.dumps({"ok": True, "text": text, **base}))
    return dir_


def make_work_repo(tmp_path: Path, runs_dir: Path, item_id: int, *, tip_email: str):
    """runs/item-N/clone: main at `base` (dev-authored), BRANCH checked out with one tip commit."""
    repo = _init_repo(runs_dir / f"item-{item_id}" / "clone")
    base = _commit(repo, "src/pages/Dashboard.tsx",
                   "export const Dashboard = () => <h1>{user.name}</h1>;\n",
                   "chore: seed", email=DEV_EMAIL)
    fork = tmp_path / "fork-origin.git"
    _git("init", "-q", "--bare", str(fork), cwd=tmp_path)
    _git("remote", "add", "origin", str(fork), cwd=repo)
    _git("remote", "add", "upstream", str(fork), cwd=repo)
    _git("push", "-q", "origin", "main:refs/heads/main", cwd=repo)
    _git("fetch", "-q", "upstream", cwd=repo)
    _git("checkout", "-q", "-b", BRANCH, cwd=repo)
    tip = _commit(repo, "src/pages/Dashboard.tsx",
                  "export const Dashboard = () => <h1>{user?.name ?? 'Guest'}</h1>;\n",
                  "fix(dashboard): guard null user on first render", email=tip_email)
    return repo, base, tip


README_TEXT = """# fix(dashboard): guard null user on first render

## What this is
A review package produced by the Bright Bots harness.

## How to verify
```bash
git clone https://github.com/Bright-Bots-Initiative/brightboost.git r && cd r
git checkout "$(cat ../BASE)"
git am ../patches/*.patch
```
"""

DIAGNOSIS_TEXT = f"""# Diagnosis

{DIAGNOSIS_SENTINEL}: `src/pages/Dashboard.tsx:42` dereferences `user.name` before the session
query resolves, so the first render throws on a null `user`.
"""

EVIDENCE_TEXT = f"""# Evidence

## baseline

### npm run lint
exit 0
```
{EVIDENCE_SENTINEL}: 0 problems (0 errors, 0 warnings)
```

## final

### npm run test:unit
exit 0
```
Tests: 42 passed, 42 total
```

### npm run build
exit 0
```
built in 3.21s
npm notice using {FAKE_SECRET} for the registry
```
"""


def write_package(run_dir: Path, *, repo: Path, base: str, branch: str) -> Path:
    pkg = run_dir / "package"
    patches = pkg / "patches"
    patches.mkdir(parents=True, exist_ok=True)
    _w(pkg / "README.md", README_TEXT)
    _w(pkg / "DIAGNOSIS.md", DIAGNOSIS_TEXT)
    _w(pkg / "DECISIONS.md", "- 2026-09-02T12:00:00Z fullsend gate F1 false: 1 slice\n")
    _w(pkg / "EVIDENCE.md", EVIDENCE_TEXT)
    _w(pkg / "ACCEPTANCE.md", "- [x] renders with no user - EVIDENCE.md, npm run test:unit\n")
    _w(pkg / "BASE", base + "\n")
    _w(pkg / "manifest.json", json.dumps({
        "schema": 1, "item_id": ITEM, "external_ref": f"self:{ITEM}", "repo": UPSTREAM,
        "base_sha": base, "branch": branch, "created_at": iso(T0), "harness_version": "1.0.0",
        "backend": "fake", "fullsend": False,
        "fullsend_gate": {"F1": False, "F2": False, "F3": True, "F4": True, "F5": False},
        "stages": [{"stage": "implement", "turns": 3, "allowance_pct": None}],
        "gates": [{"name": "npm run lint", "exit_code": 0}],
        "patch_count": 1, "touched_paths": ["src/pages/Dashboard.tsx"],
    }, indent=2) + "\n")
    _git("format-patch", "-q", "-1", "HEAD", "-o", str(patches), cwd=repo)
    _git("bundle", "create", str(pkg / "bundle.gitbundle"), branch, cwd=repo)
    _w(pkg / "transcript.jsonl", "")
    return pkg


SPEC_TEXT = """# fix(dashboard): guard null user on first render

## Issue
#816 - the dashboard throws on first render.

## Diagnosis
`src/pages/Dashboard.tsx:42` dereferences `user.name` before the session query resolves.

## Approach
Optional-chain the selector and render a guest placeholder.

## Slices
1. Guard the selector.

## Behaviors
1. Rendering with no session shows "Guest" instead of throwing.

## Acceptance criteria
- `npm run test:unit` passes with the new case.

## Decisions
1. Placeholder text over a spinner: the session query is fast.

## Open questions
None

## Touched paths
- src/pages/Dashboard.tsx

## Risks
Low.
"""


def setup_deliver(tmp_path: Path, *, state="packaged", can_write=True,
                  trusted=frozenset({"jgoetzmann"}), with_package=True):
    config = make_config(tmp_path, trusted=sorted(trusted))
    clock = FrozenClock(T0)
    gh = FakeGh(clock=clock, can_write=can_write)
    scratch = SqliteStore(tmp_path / "scratch.db", clock)
    scratch.migrate()
    store = GitHubStore(gh, self_repo=SELF_REPO, scratch=scratch, clock=clock, run_url=RUN_URL)
    repo, base, tip = make_work_repo(tmp_path, config.runs_dir, ITEM, tip_email=HARNESS_EMAIL)
    gh.add_issue(ITEM, TITLE, body="issue:816", labels=[LABELS[state]])
    run_dir = config.runs_dir / f"item-{ITEM}"
    spec = _w(run_dir / "spec" / f"{ITEM}.md", SPEC_TEXT)
    fields = {"base_sha": base, "branch_name": BRANCH, "spec_path": str(spec)}
    if with_package:
        pkg = write_package(run_dir, repo=repo, base=base, branch=BRANCH)
        fields["package_path"] = str(pkg)
    store.update_work_item(ITEM, **fields)
    gh.calls.clear()
    gh.sent.clear()
    gh.refused.clear()
    runner = RecordingRunner(FakeRunner(write_fixtures(tmp_path / "fixtures")))
    ledger = Ledger.empty("2026-08-31T00:00:00Z")
    clones = FakeClones(repo, base, BRANCH)
    ctx = build_context(config, run_id=f"item-{ITEM}", runner=runner, gh=gh, clock=clock,
                        store=store, clones=clones, ledger=ledger, trusted=frozenset(trusted))
    return SimpleNamespace(ctx=ctx, gh=gh, store=store, runner=runner, ledger=ledger,
                           clones=clones, repo=repo, base=base, tip=tip, config=config,
                           run_dir=run_dir)


# --------------------------------------------------------------------------------------
# B105 - the fork's default branch moves only by fast-forward from upstream
# --------------------------------------------------------------------------------------
def test_B105_sync_fork_equal_returns_the_sha_and_pushes_nothing(tmp_path):
    """B105: fork/main == upstream/main -> the sha is returned and `push` is never called."""
    r = make_sync_repos(tmp_path)
    config = make_config(tmp_path)
    pushes, log = [], []
    sha = sync_fork(config, workdir=tmp_path / "sync", push=make_push(r, pushes),
                    git_runner=make_git_runner(r, log))
    assert sha == r.u0
    assert pushes == []
    assert main_of(r.fork) == r.u0
    assert log, "sync_fork ran no git command through the injected git_runner"


def test_B105_sync_fork_fast_forwards_a_fork_that_is_behind(tmp_path):
    """B105: fork behind upstream -> exactly one push of upstream/main:refs/heads/main."""
    r = make_sync_repos(tmp_path)
    u1 = advance_upstream(r)
    config = make_config(tmp_path)
    pushes, log = [], []
    workdir = tmp_path / "sync"
    sha = sync_fork(config, workdir=workdir, push=make_push(r, pushes),
                    git_runner=make_git_runner(r, log))
    assert sha == u1
    assert pushes == [(workdir / "fork.git", "upstream/main:refs/heads/main")]
    assert main_of(r.fork) == u1
    assert main_of(r.upstream) == u1


def test_B105_sync_fork_diverged_raises_names_both_shas_and_pushes_nothing(tmp_path):
    """B105: a fork commit upstream lacks -> ForkDiverged, no push, both repos untouched."""
    r = make_sync_repos(tmp_path)
    u1 = advance_upstream(r)
    f1 = commit_on_fork_main(r, tmp_path)
    config = make_config(tmp_path)
    pushes, log = [], []
    with pytest.raises(ForkDiverged) as excinfo:
        sync_fork(config, workdir=tmp_path / "sync", push=make_push(r, pushes),
                  git_runner=make_git_runner(r, log))
    message = str(excinfo.value)
    assert f1[:7] in message
    assert u1[:7] in message
    assert pushes == []
    assert main_of(r.fork) == f1
    assert main_of(r.upstream) == u1


def test_B105_sync_fork_refuses_a_fork_that_is_ahead_of_upstream(tmp_path):
    """B105: a README change on the fork's main is divergence too - refused, nothing pushed."""
    r = make_sync_repos(tmp_path)
    f1 = commit_on_fork_main(r, tmp_path)
    config = make_config(tmp_path)
    pushes, log = [], []
    with pytest.raises(ForkDiverged) as excinfo:
        sync_fork(config, workdir=tmp_path / "sync", push=make_push(r, pushes),
                  git_runner=make_git_runner(r, log))
    assert f1[:7] in str(excinfo.value)
    assert r.u0[:7] in str(excinfo.value)
    assert pushes == []
    assert main_of(r.fork) == f1


def test_B105_sync_fork_never_uses_force(tmp_path):
    """B105: every git command sync_fork issues is force-free; a rewind is never attempted."""
    r = make_sync_repos(tmp_path)
    advance_upstream(r)
    commit_on_fork_main(r, tmp_path)
    config = make_config(tmp_path)
    pushes, log = [], []
    with pytest.raises(ForkDiverged):
        sync_fork(config, workdir=tmp_path / "sync", push=make_push(r, pushes),
                  git_runner=make_git_runner(r, log))
    assert log
    for argv in log:
        assert "--force" not in argv and "-f" not in argv and not any(
            a.startswith("+") for a in argv if ":" in a), argv


# --------------------------------------------------------------------------------------
# B106 - work branches are cut under harness/ from a commit that exists upstream
# --------------------------------------------------------------------------------------
def test_B106_work_branch_is_cut_under_harness_from_an_upstream_commit(tmp_path):
    """B106: acquire() names the branch harness/<kind>-<issue>-<slug>; base_sha exists upstream."""
    r = make_sync_repos(tmp_path)
    config = make_config(tmp_path)
    clones = CloneManager(config, FrozenClock(T0), clone_url=str(r.fork),
                          free_bytes=lambda p: 10 ** 12)
    item = WorkItem(id=ITEM, kind="issue", external_ref="issue:816", title=TITLE,
                    state="approved", parent_id=None, depends_on=None, tier_required=0,
                    spec_path=None, package_path=None, base_sha=None, branch_name=None,
                    attempts=0, created_at=iso(T0), updated_at=iso(T0))
    lease = clones.acquire(item)
    assert lease.branch.startswith("harness/")
    assert re.fullmatch(r"harness/fix-816-[a-z0-9-]+", lease.branch), lease.branch
    assert lease.base_sha == r.u0
    assert _git("cat-file", "-t", lease.base_sha, cwd=r.upstream) == "commit"
    assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=Path(lease.path)) == lease.branch
    assert Path(lease.path).resolve().is_relative_to(config.runs_dir.resolve())


def test_B106_deliver_opens_the_pr_from_a_harness_branch_at_the_recorded_base(tmp_path):
    """B106: the delivery PR's head is the harness/ branch and its body pins the base commit."""
    s = setup_deliver(tmp_path)
    deliver(s.ctx, ITEM)
    cp = s.gh.calls_named("create_pull")
    assert len(cp) == 1
    head = cp[0]["head"]
    assert head.split(":")[-1] == BRANCH
    assert "harness/" in head
    assert s.base in cp[0]["body"]
    assert _git("cat-file", "-t", s.base, cwd=s.repo) == "commit"


# --------------------------------------------------------------------------------------
# B108 - the PR body is the review package, redacted
# --------------------------------------------------------------------------------------
def test_B108_pr_body_carries_diagnosis_base_sha_verbatim_evidence_and_git_am(tmp_path):
    """B108: body has the DIAGNOSIS text, the base sha, gate evidence verbatim, and `git am`."""
    s = setup_deliver(tmp_path)
    deliver(s.ctx, ITEM)
    body = s.gh.calls_named("create_pull")[0]["body"]
    assert DIAGNOSIS_SENTINEL in body
    assert s.base in body
    assert EVIDENCE_SENTINEL in body
    assert "exit 0" in body
    assert "git am" in body


def test_B108_pr_body_is_redacted_before_it_is_sent(tmp_path):
    """B108 / I-13: a token planted in EVIDENCE.md reaches the client only as [REDACTED]."""
    s = setup_deliver(tmp_path)
    deliver(s.ctx, ITEM)
    body = s.gh.calls_named("create_pull")[0]["body"]
    assert FAKE_SECRET not in body
    assert "[REDACTED]" in body
    for entry in s.gh.sent:
        assert FAKE_SECRET not in json.dumps(entry["payload"]), entry["url"]


# --------------------------------------------------------------------------------------
# B109 / I-12 - never merges, approves or dismisses
# --------------------------------------------------------------------------------------
def test_B109_deliver_uses_only_the_section_7_surface_and_no_merge_approve_dismiss(tmp_path):
    """B109 / I-12: every client call deliver makes is a section-7 name from the allowed set."""
    s = setup_deliver(tmp_path)
    deliver(s.ctx, ITEM)
    names = {c["name"] for c in s.gh.calls}
    assert names <= GH_SURFACE, names - GH_SURFACE
    writes = names & WRITE_METHODS
    assert writes, "deliver wrote nothing"
    assert writes <= DELIVER_WRITES, writes - DELIVER_WRITES
    assert not any(w in n for n in names for w in ("merge", "approve", "dismiss"))
    for entry in s.gh.sent:
        assert "/merge" not in entry["url"]
        assert "/reviews" not in entry["url"]


def test_B109_the_real_client_has_no_merge_approve_or_dismiss_method():
    """B109 / I-12: the code to merge, approve or dismiss does not exist on GitHubClient."""
    public = [n for n in dir(GitHubClient) if not n.startswith("_")]
    offenders = [n for n in public if any(w in n.lower() for w in ("merge", "approve", "dismiss"))]
    assert offenders == []
    assert "create_pull" in public and "request_reviewers" in public


# --------------------------------------------------------------------------------------
# deliver contract (RUN-DECISIONS-D2 section 13)
# --------------------------------------------------------------------------------------
def test_deliver_returns_the_pr_url_and_ships_the_item(tmp_path):
    """Section 13 / handoff 4.5 step 6: returns the PR URL; item -> shipped; comment has the URL."""
    s = setup_deliver(tmp_path)
    url = deliver(s.ctx, ITEM)
    prs = list(s.gh.prs[UPSTREAM].values())
    assert len(prs) == 1
    assert url == prs[0]["html_url"]
    assert url.startswith(f"https://github.com/{UPSTREAM}/pull/")
    assert s.store.get_work_item(ITEM).state == "shipped"
    assert s.gh.state_labels(ITEM) == ["harness:shipped"]
    assert any(url in c["body"] for c in s.gh.comments_of(ITEM))


def test_deliver_pushes_the_branch_to_the_fork_and_opens_the_pr_against_upstream_main(tmp_path):
    """Handoff 5.3: the push target is the fork; the PR base is the product repo's main."""
    s = setup_deliver(tmp_path)
    deliver(s.ctx, ITEM)
    pushes = s.gh.calls_named("push_branch")
    assert len(pushes) == 1
    assert pushes[0]["branch"] == BRANCH
    assert pushes[0]["remote_repo"] == FORK
    assert Path(pushes[0]["clone"]).resolve() == s.repo.resolve()
    assert all(c["remote_repo"] == FORK for c in s.gh.calls
               if c["name"] in ("push_branch", "push_ref"))
    cp = s.gh.calls_named("create_pull")
    assert len(cp) == 1
    assert cp[0]["repo"] == UPSTREAM
    assert cp[0]["base"] == "main"
    assert s.gh.prs[SELF_REPO] == {}


def test_deliver_requests_review_from_every_trusted_handle(tmp_path):
    """Handoff 4.5 step 5 / section 13: reviewers == ctx.trusted, on the PR just opened."""
    s = setup_deliver(tmp_path, trusted=frozenset({"jgoetzmann", "nathanhandle"}))
    deliver(s.ctx, ITEM)
    rr = s.gh.calls_named("request_reviewers")
    assert len(rr) == 1
    assert rr[0]["repo"] == UPSTREAM
    assert rr[0]["number"] in s.gh.prs[UPSTREAM]
    assert {h.lower() for h in rr[0]["reviewers"]} == {"jgoetzmann", "nathanhandle"}


def test_deliver_without_a_token_returns_empty_writes_deliver_json_and_sends_nothing(tmp_path):
    """Section 13: can_write False -> "" returned, runs/item-N/DELIVER.json written, no writes."""
    s = setup_deliver(tmp_path, can_write=False)
    result = deliver(s.ctx, ITEM)
    assert result == ""
    marker = s.config.runs_dir / f"item-{ITEM}" / "DELIVER.json"
    assert marker.is_file()
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    assert BRANCH in json.dumps(payload)
    assert s.gh.sent == []
    assert s.gh.refused == []
    assert s.gh.write_names() == set()
    assert s.gh.prs[UPSTREAM] == {}


def test_deliver_from_a_wrong_entry_state_raises_and_opens_nothing(tmp_path):
    """Section 13: deliver requires `packaged`; from `approved` it raises IllegalTransition."""
    s = setup_deliver(tmp_path, state="approved")
    with pytest.raises(IllegalTransition):
        deliver(s.ctx, ITEM)
    assert s.gh.calls_named("create_pull") == []
    assert s.gh.calls_named("push_branch") == []
    assert s.gh.state_labels(ITEM) == ["harness:approved"]


def test_deliver_from_a_terminal_state_raises_and_opens_nothing(tmp_path):
    """Section 13: a merged item is never re-delivered."""
    s = setup_deliver(tmp_path, state="merged")
    with pytest.raises(IllegalTransition):
        deliver(s.ctx, ITEM)
    assert s.gh.prs[UPSTREAM] == {}
    assert s.gh.state_labels(ITEM) == ["harness:merged"]


def test_deliver_without_a_review_package_raises_and_opens_no_pr(tmp_path):
    """B108: no package on disk means no evidence to ship; deliver fails before any PR call."""
    s = setup_deliver(tmp_path, with_package=False)
    with pytest.raises(HarnessError):
        deliver(s.ctx, ITEM)
    assert s.gh.calls_named("create_pull") == []
    assert s.gh.prs[UPSTREAM] == {}


def test_deliver_never_files_an_issue_anywhere(tmp_path):
    """I-14: deliver's writes never include create_issue, in either repository."""
    s = setup_deliver(tmp_path)
    before_self = set(s.gh.repos[SELF_REPO])
    deliver(s.ctx, ITEM)
    assert s.gh.calls_named("create_issue") == []
    assert set(s.gh.repos[SELF_REPO]) == before_self
    assert s.gh.repos[UPSTREAM] == {}
