"""B31-B37: harness.gh.GitHubReadOnly (HARNESS-SPEC 5.5).

Every response is canned through the injectable `opener` fixed by RUN-DECISIONS. Nothing here
touches the network, and the clock is frozen at 2026-09-01T12:00:00Z.
"""

from __future__ import annotations

import email.message
import io
import json
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone

from types import SimpleNamespace

import pytest

from harness.clock import FrozenClock, iso
from harness.errors import GitHubError, RateCeilingReached
from harness.gh import GitHubReadOnly
from harness.store import Store

FROZEN_AT = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
REPO = "Bright-Bots-Initiative/brightboost"
API = "https://api.github.com"
ISSUE_URL = f"{API}/repos/{REPO}/issues/816"
ISSUE_816 = {"number": 816, "title": "bundle size check fails on esm output"}


def make_headers(mapping=None):
    message = email.message.Message()
    for key, value in (mapping or {}).items():
        message[key] = value
    return message


class FakeResponse:
    """Canned stand-in for an HTTP response: .status, .headers.get(), .read()."""

    def __init__(self, payload, *, status=200, headers=None):
        self.status = status
        self.code = status
        if isinstance(payload, (bytes, bytearray)):
            self._body = bytes(payload)
        else:
            self._body = json.dumps(payload).encode("utf-8")
        self.headers = make_headers(headers)

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def http_error(url, code, *, payload=b"", headers=None):
    if isinstance(payload, (bytes, bytearray)):
        body = bytes(payload)
    else:
        body = json.dumps(payload).encode("utf-8")
    return urllib.error.HTTPError(url, code, "canned", make_headers(headers), io.BytesIO(body))


class FakeOpener:
    """Records every urllib.request.Request handed to it and replays canned outcomes."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def __call__(self, request, *args, **kwargs):
        self.requests.append(request)
        if not self.outcomes:
            raise AssertionError(f"unexpected extra request: {request.full_url}")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    @property
    def urls(self):
        return [request.full_url for request in self.requests]


def header_value(request, name):
    for key, value in request.header_items():
        if key.lower() == name.lower():
            return value
    return None


def one_hour_ago():
    return iso(FROZEN_AT - timedelta(hours=1))


@pytest.fixture
def clock():
    return FrozenClock(FROZEN_AT)


@pytest.fixture
def store(tmp_path, clock):
    store = Store(tmp_path / "h.db", clock)
    store.migrate()
    yield store
    store.close()


def make_gh(store, clock, opener, *, ceiling=100):
    return GitHubReadOnly(REPO, store, clock, ceiling, opener=opener)


# --- B31 -------------------------------------------------------------------------------------


def test_B31_no_authorization_header_is_sent_and_ua_and_accept_are(store, clock):
    opener = FakeOpener(FakeResponse([ISSUE_816]))
    gh = make_gh(store, clock, opener)

    gh.issues()

    request = opener.requests[0]
    assert request.get_header("Authorization") is None
    assert not [k for k, _ in request.header_items() if k.lower() == "authorization"]
    assert (header_value(request, "User-Agent") or "").startswith("bright-bots-harness/")
    assert header_value(request, "Accept") == "application/vnd.github+json"


def test_B31_no_method_on_the_client_ever_sends_an_authorization_header(store, clock):
    opener = FakeOpener(
        FakeResponse(ISSUE_816),
        FakeResponse([ISSUE_816]),
        FakeResponse([{"number": 9, "title": "chore: x", "head": {"ref": "jack/chore-740-x"}}]),
        FakeResponse([{"name": "main"}]),
    )
    gh = make_gh(store, clock, opener)

    gh.issue(816)
    gh.issues()
    gh.pulls()
    gh.branches()

    assert len(opener.requests) == 4
    for request in opener.requests:
        assert request.get_header("Authorization") is None
        assert not [k for k, _ in request.header_items() if k.lower() == "authorization"]
        assert not [k for k, _ in request.header_items() if k.lower() == "proxy-authorization"]


def test_I1_every_github_request_is_a_GET_with_no_body(store, clock):
    opener = FakeOpener(FakeResponse(ISSUE_816))
    gh = make_gh(store, clock, opener)

    gh.issue(816)

    request = opener.requests[0]
    assert request.get_method() == "GET"
    assert request.data is None


# --- B32 -------------------------------------------------------------------------------------


def test_B32_a_cached_etag_is_sent_as_if_none_match_and_a_304_serves_the_cache(store, clock):
    cached = {"number": 816, "title": "the cached title"}
    store.cache_put(ISSUE_URL, 'W/"etag-abc"', json.dumps(cached))
    opener = FakeOpener(http_error(ISSUE_URL, 304))
    gh = make_gh(store, clock, opener)

    result = gh.issue(816)

    assert header_value(opener.requests[0], "If-None-Match") == 'W/"etag-abc"'
    assert result == cached


def test_B32_a_plain_304_response_also_serves_the_cached_body(store, clock):
    cached = {"number": 816, "title": "the cached title"}
    store.cache_put(ISSUE_URL, 'W/"etag-abc"', json.dumps(cached))
    opener = FakeOpener(FakeResponse(b"", status=304))
    gh = make_gh(store, clock, opener)

    result = gh.issue(816)

    assert result == cached


def test_B32_no_if_none_match_header_is_sent_when_nothing_is_cached(store, clock):
    opener = FakeOpener(FakeResponse(ISSUE_816, headers={"ETag": 'W/"fresh"'}))
    gh = make_gh(store, clock, opener)

    assert gh.issue(816) == ISSUE_816
    assert header_value(opener.requests[0], "If-None-Match") is None


# --- B33 -------------------------------------------------------------------------------------


def test_B33_an_uncached_request_appends_an_api_call_row(store, clock):
    opener = FakeOpener(FakeResponse(ISSUE_816))
    gh = make_gh(store, clock, opener)
    before = store.api_calls_since(one_hour_ago())

    gh.issue(816)

    assert store.api_calls_since(one_hour_ago()) == before + 1


def test_B33_a_304_cache_hit_still_appends_an_api_call_row(store, clock):
    store.cache_put(ISSUE_URL, 'W/"etag-abc"', json.dumps(ISSUE_816))
    opener = FakeOpener(http_error(ISSUE_URL, 304))
    gh = make_gh(store, clock, opener)
    before = store.api_calls_since(one_hour_ago())

    gh.issue(816)

    assert store.api_calls_since(one_hour_ago()) == before + 1


# --- B34 -------------------------------------------------------------------------------------


def test_B34_the_hourly_ceiling_raises_RateCeilingReached_before_any_request(store, clock):
    for index in range(3):
        store.record_api_call(f"{API}/repos/{REPO}/issues/{index}", 200, False)
    opener = FakeOpener()
    gh = make_gh(store, clock, opener, ceiling=3)

    with pytest.raises(RateCeilingReached):
        gh.issues()

    assert opener.requests == []


def test_B34_a_request_below_the_ceiling_is_issued(store, clock):
    store.record_api_call(f"{API}/repos/{REPO}/issues/1", 200, False)
    opener = FakeOpener(FakeResponse([ISSUE_816]))
    gh = make_gh(store, clock, opener, ceiling=3)

    assert gh.issues() == [ISSUE_816]
    assert len(opener.requests) == 1


def test_B34_rate_budget_remaining_falls_to_zero_at_the_ceiling(store, clock):
    gh = make_gh(store, clock, FakeOpener(), ceiling=2)
    assert gh.rate_budget_remaining() == 2

    store.record_api_call(f"{API}/repos/{REPO}/issues/1", 200, False)
    store.record_api_call(f"{API}/repos/{REPO}/issues/2", 304, True)

    assert gh.rate_budget_remaining() == 0


# --- B35 -------------------------------------------------------------------------------------


def test_B35_labels_are_sent_as_one_comma_joined_query_parameter(store, clock):
    opener = FakeOpener(FakeResponse([ISSUE_816]))
    gh = make_gh(store, clock, opener)

    gh.issues(state="open", labels=["harness-ok", "bug"])

    url = urllib.parse.unquote(opener.urls[0])
    assert "labels=harness-ok,bug" in url
    assert "state=open" in url
    assert url.startswith(f"{API}/repos/{REPO}/issues")


def test_B35_no_labels_parameter_is_sent_when_labels_is_empty(store, clock):
    opener = FakeOpener(FakeResponse([ISSUE_816]))
    gh = make_gh(store, clock, opener)

    gh.issues()

    assert "labels=" not in urllib.parse.unquote(opener.urls[0])


# --- B36 -------------------------------------------------------------------------------------


def test_B36_branches_follows_link_rel_next_to_completion(store, clock):
    next_url = f"{API}/repositories/1024/branches?per_page=100&page=2"
    link = f'<{next_url}>; rel="next", <{next_url}>; rel="last"'
    opener = FakeOpener(
        FakeResponse(
            [{"name": "main"}, {"name": "agent-737/qtr-ceiling"}],
            headers={"Link": link},
        ),
        FakeResponse([{"name": "fix-801/ci-shell-gate-isolation"}]),
    )
    gh = make_gh(store, clock, opener)

    result = gh.branches()

    assert result == ["main", "agent-737/qtr-ceiling", "fix-801/ci-shell-gate-isolation"]
    assert len(opener.requests) == 2
    assert opener.urls[0].startswith(f"{API}/repos/{REPO}/branches")
    assert "per_page=100" in opener.urls[0]
    assert opener.urls[1] == next_url


def test_B36_branches_stops_when_the_link_header_has_no_next_relation(store, clock):
    last = f"{API}/repositories/1024/branches?per_page=100&page=1"
    opener = FakeOpener(
        FakeResponse([{"name": "main"}], headers={"Link": f'<{last}>; rel="last"'}),
    )
    gh = make_gh(store, clock, opener)

    assert gh.branches() == ["main"]
    assert len(opener.requests) == 1


# --- B37 -------------------------------------------------------------------------------------


def test_B37_a_403_with_a_rate_limit_body_raises_RateCeilingReached(store, clock):
    body = {"message": "API rate limit exceeded for 203.0.113.9.", "documentation_url": "x"}
    opener = FakeOpener(http_error(f"{API}/repos/{REPO}/branches", 403, payload=body))
    gh = make_gh(store, clock, opener)

    with pytest.raises(RateCeilingReached):
        gh.branches()


def test_B37_a_403_with_x_ratelimit_remaining_zero_raises_RateCeilingReached(store, clock):
    opener = FakeOpener(
        http_error(
            f"{API}/repos/{REPO}/pulls",
            403,
            payload={"message": "Forbidden"},
            headers={"X-RateLimit-Remaining": "0"},
        )
    )
    gh = make_gh(store, clock, opener)

    with pytest.raises(RateCeilingReached):
        gh.pulls()


def test_B37_a_404_raises_GitHubError_and_not_RateCeilingReached(store, clock):
    opener = FakeOpener(http_error(ISSUE_URL, 404, payload={"message": "Not Found"}))
    gh = make_gh(store, clock, opener)

    with pytest.raises(GitHubError) as caught:
        gh.issue(816)

    assert not isinstance(caught.value, RateCeilingReached)


# --------------------------------------------------------------------------------------
# create_label — the write behind `harness init --labels` (handoff §16 item 11)
# --------------------------------------------------------------------------------------


def test_create_label_posts_to_the_given_repo_labels_endpoint_redacted_and_needs_a_token(tmp_path):
    """§5.3: a label create is a write like any other — tier-gated, redacted, recorded in `sent`."""
    from harness.clock import FrozenClock
    from harness.errors import TierViolation
    from harness.gh import GitHubClient
    from harness.store import Store
    from datetime import datetime, timezone

    clock = FrozenClock(datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc))
    store = Store(tmp_path / "h.db", clock)
    store.migrate()
    client = GitHubClient(
        "o/r", store, clock, 50, token="ghp_" + "FAKE0" * 8, self_repo="me/self", dry_run=True
    )
    client.create_label("me/self", name="stage:queued", color="#0e8a16", description="secret=abc")
    (call,) = client.sent
    assert call["method"] == "POST" and call["url"].endswith("/repos/me/self/labels")
    assert call["payload"]["name"] == "stage:queued" and call["payload"]["color"] == "0e8a16"
    assert "abc" not in call["payload"]["description"]

    unarmed = GitHubClient("o/r", store, clock, 50, token="", self_repo="me/self", dry_run=True)
    with pytest.raises(TierViolation):
        unarmed.create_label("me/self", name="x", color="000000")
    assert unarmed.sent == []


# --------------------------------------------------------------------------------------
# B229 - the push carries its own hook suppression, and says why it failed (D49)
# --------------------------------------------------------------------------------------


def test_b229_the_push_turns_hooks_off_on_the_command_line(tmp_path):
    """B229: `acquire` sets core.hooksPath in the clone's config, and then `npm ci` runs the
    product repository's `prepare` script -- husky -- which sets it right back. On the command
    line nothing can override it."""
    from harness.clone import HOOKS_OFF

    calls: list[list[str]] = []

    def runner(argv, cwd=None):
        calls.append(list(argv))
        return (0, "", "")

    from datetime import datetime, timezone

    from pathlib import Path

    from harness.gh import GitHubClient
    from harness.store import Store

    clock = FrozenClock(datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc))
    store = Store(tmp_path / "h.db", clock)
    store.migrate()
    client = GitHubClient(
        "o/r", store, clock, 50, token="ghp_" + "FAKE0" * 8, self_repo="me/self"
    )
    client.push_ref(tmp_path,"main:refs/heads/main", remote_repo="o/n", git_runner=runner)

    argv = calls[0]
    assert f"core.hooksPath={HOOKS_OFF}" in argv
    assert argv.index("-c") < argv.index("push")


def test_b229_a_refused_push_reports_gits_own_lines_not_the_hooks_output():
    """B229: measured -- a refusing hook's tail was a vitest browser stack, and that is what the
    error said the push failed for."""
    from harness.gh import _push_reason

    noise = "\n".join(["box art"] * 200)
    detail = f"{noise}\nremote: rejected by pre-push\nerror: failed to push some refs"

    reason = _push_reason(detail)

    assert "remote: rejected by pre-push" in reason
    assert "error: failed to push some refs" in reason
    assert "box art" not in reason


def test_b229_a_push_failure_with_no_git_prefix_still_says_something():
    from harness.gh import _push_reason

    assert _push_reason("something odd happened") == "something odd happened"
    assert _push_reason("") == "(no output)"


# --------------------------------------------------------------------------------------
# B231 - the request ceiling follows the tier (D51)
# --------------------------------------------------------------------------------------


def test_b231_tier_0_keeps_the_unauthenticated_ceiling():
    """B231: no token means GitHub's own limit is 60 an hour, and 50 is the margin under it."""
    from harness.gh import ceiling_for

    config = SimpleNamespace(github_api_ceiling_per_hour=50, permission_tier=0)

    assert ceiling_for(config) == 50


def test_b231_tier_2_gets_the_authenticated_ceiling():
    """B231: measured -- the first delivery opened its pull request upstream and then failed on
    the very next call, a label write, because it was held to the unauthenticated figure."""
    from harness.gh import AUTHENTICATED_CEILING_PER_HOUR, ceiling_for

    config = SimpleNamespace(github_api_ceiling_per_hour=50, permission_tier=2)

    assert ceiling_for(config) == AUTHENTICATED_CEILING_PER_HOUR


def test_b231_a_configured_ceiling_above_the_default_still_wins():
    """B231: the raise is a floor, not an override; an operator who set it higher meant it."""
    from harness.gh import ceiling_for

    config = SimpleNamespace(github_api_ceiling_per_hour=9000, permission_tier=2)

    assert ceiling_for(config) == 9000


def test_b231_build_client_uses_the_tier_aware_ceiling(tmp_path, monkeypatch):
    """B231: the raise has to reach the client, not just exist as a function."""
    from datetime import datetime, timezone

    from harness import gh as gh_mod
    from harness.store import Store

    clock = FrozenClock(datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc))
    store = Store(tmp_path / "h.db", clock)
    store.migrate()
    config = SimpleNamespace(
        repo="o/r",
        github_api_ceiling_per_hour=50,
        permission_tier=2,
        self_repo="me/self",
    )
    monkeypatch.setattr("harness.config.github_token", lambda: "")

    client = gh_mod.build_client(config, store, clock)

    assert client.ceiling_per_hour == gh_mod.AUTHENTICATED_CEILING_PER_HOUR


# --------------------------------------------------------------------------------------
# B296-B298 (D67) - one protected path set, the commit walk, and the push guard built on it
# --------------------------------------------------------------------------------------
GUARD_TOKEN = "ghp_" + "FAKE0" * 8
HARNESS = "harness@brightboost-harness"
WIP = "harness@localhost"
DEV = "dev@example.com"
CI = ".github/workflows/ci-cd.yml"


def _git(repo, *args: str) -> str:
    import subprocess

    argv = [
        "git", "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false",
        # A runner has no ambient identity, and commit-tree needs one (D39).
        "-c", "user.email=harness@localhost", "-c", "user.name=harness", *args,
    ]
    done = subprocess.run(argv, cwd=str(repo), capture_output=True, text=True)
    assert done.returncode == 0, f"{argv}: {done.stderr}"
    return done.stdout.strip()


def _repo(tmp_path):
    repo = tmp_path / "clone"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    return repo


def _commit(repo, email: str, files: dict, message: str = "change") -> str:
    """One commit authored by `email`; a `None` value deletes that path."""
    for rel, text in files.items():
        if text is None:
            _git(repo, "rm", "-q", rel)
            continue
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", f"user.email={email}", "-c", "user.name=someone", "commit", "-q",
         "--allow-empty", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _guard_client(tmp_path, *, dry_run=True, token=GUARD_TOKEN, opener=None):
    from harness.gh import GitHubClient

    clock = FrozenClock(datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc))
    store = Store(tmp_path / "guard.db", clock)
    store.migrate()
    return GitHubClient("o/r", store, clock, 50, token=token, self_repo="me/self",
                        dry_run=dry_run, opener=opener)


def _pushed(client) -> list[dict]:
    return [entry for entry in client.sent if entry["method"] == "git push"]


def _recorder(calls: list, code: int = 0, err: str = ""):
    """A git runner that records every argv. When it succeeds it answers B312's history probe
    as a full, ungrafted clone does -- git always prints both answers -- and everything else
    with nothing, so a fake cannot pass the probe by printing what production never prints."""
    def run(argv, cwd=None):
        calls.append(list(argv))
        if code == 0 and "--is-shallow-repository" in argv:
            return (0, "false\n.git/info/grafts\n", "")
        return (code, "", err)

    return run


@pytest.mark.parametrize("path", [
    CI, ".github/CODEOWNERS", ".github/dependabot.yml", ".github/ISSUE_TEMPLATE/bug.md",
    ".github/actions/setup/action.yml", "./.github/CODEOWNERS", "`.github/dependabot.yml`",
    '".github/workflows/a\\"b.yml"', ".github\\workflows\\win.yml",
    "packages/app/.github/workflows/x.yml",
])
def test_b296_every_path_under_github_is_protected(path):
    """B296 / D67 (handoff 8, test 7): all of `.github/`, not only its workflows -- composite
    actions, dependabot and CODEOWNERS steer CI and review too -- in every spelling a path can
    reach the check in, git's C-quoted form included."""
    from harness.clone import protected_paths_in

    assert protected_paths_in([path]) == [path]


@pytest.mark.parametrize("path", [
    "docs/github-setup.md", "src/github/client.ts", "src/dotgithub/x.yml", ".githubrc",
    "github/workflows/x.yml", "", "   ",
])
def test_b296_a_path_that_only_mentions_github_is_not_protected(path):
    from harness.clone import protected_paths_in

    assert protected_paths_in([path]) == []


def test_b296_the_one_set_is_all_of_github_and_b64_reads_it():
    from harness import clone
    from harness.stages import implement

    assert clone.PROTECTED_PUSH_PATHS == ("/.github/",)
    assert implement.FORBIDDEN_DIFF_PATHS is clone.PROTECTED_PUSH_PATHS


def test_b297_the_walk_stops_at_the_first_commit_the_harness_did_not_author(tmp_path):
    """B297 (handoff 8, test 9): [upstream commit touching .github/] then [harness commit
    touching src/]. The walk takes the harness's commit and stops at upstream's author, so
    upstream's own workflow change is relayed, not refused."""
    from harness.clone import walk_harness_commits

    repo = _repo(tmp_path)
    _commit(repo, DEV, {"README.md": "# p\n"}, "chore: seed")
    upstream = _commit(repo, DEV, {CI: "on: push\n"}, "ci: pipeline")
    mine = _commit(repo, HARNESS, {"src/app.ts": "export {};\n"}, "fix: thing")

    walk = walk_harness_commits(repo, "HEAD")

    assert [c.sha for c in walk.commits] == [mine]
    assert walk.commits[0].paths == ("src/app.ts",)
    assert walk.stopped_at == upstream and not walk.capped and walk.protected() == []
    client = _guard_client(tmp_path)
    client.push_branch(repo, "main", remote_repo="o/fork")
    assert len(_pushed(client)) == 1


def test_b298_a_harness_commit_under_github_is_refused_by_sha_and_path(tmp_path):
    """B298 (handoff 8, test 10): [harness src/] then [harness .github/]. Refused; the error
    names the commit and the path, and nothing is recorded as pushed."""
    repo = _repo(tmp_path)
    _commit(repo, DEV, {"README.md": "# p\n"}, "chore: seed")
    _commit(repo, HARNESS, {"src/app.ts": "export {};\n"}, "fix: thing")
    bad = _commit(repo, WIP, {CI: "on: push\n"}, "wip: handoff")
    client = _guard_client(tmp_path)

    with pytest.raises(GitHubError) as caught:
        client.push_branch(repo, "main", remote_repo="o/fork")

    assert bad[:12] in str(caught.value) and CI in str(caught.value)
    assert _pushed(client) == []


def test_b298_a_github_change_below_the_tip_is_refused_too(tmp_path):
    """B298: every harness commit above the mainline is checked, not only the tip."""
    repo = _repo(tmp_path)
    _commit(repo, DEV, {"README.md": "# p\n"}, "chore: seed")
    bad = _commit(repo, HARNESS, {".github/dependabot.yml": "version: 2\n"}, "chore: deps")
    _commit(repo, HARNESS, {"src/app.ts": "export {};\n"}, "fix: thing")

    with pytest.raises(GitHubError, match=bad[:12]):
        _guard_client(tmp_path).push_branch(repo, "main", remote_repo="o/fork")


def test_b297_a_human_commit_on_top_ends_the_walk_where_b139_takes_over(tmp_path):
    """B297 (handoff 8, test 11): [harness commits] then [a human's commit]. The walk stops at
    once -- that commit is its author's to have pushed -- and B139 is what refuses to
    force-push over it. They agree on the one fact they share: the tip is not the harness's."""
    from harness.clone import HARNESS_AUTHOR_EMAILS, Lease, walk_harness_commits
    from harness.stages.revise import tip_author_email

    repo = _repo(tmp_path)
    _commit(repo, DEV, {"README.md": "# p\n"}, "chore: seed")
    _commit(repo, HARNESS, {".github/dependabot.yml": "version: 2\n"}, "chore: deps")
    human = _commit(repo, "jack@example.com", {"src/app.ts": "export {};\n"}, "fix: by hand")

    walk = walk_harness_commits(repo, "HEAD")

    assert walk.commits == () and walk.stopped_at == human
    lease = Lease(run_id="r", path=repo, base_sha="", branch="main")
    assert tip_author_email(lease) not in HARNESS_AUTHOR_EMAILS


def test_b297_a_root_commit_lists_its_paths(tmp_path):
    """B297 (handoff 8, test 12): `git diff-tree` prints nothing for a root commit without
    `--root`; the walk's `git log --name-only` has no such gap."""
    from harness.clone import walk_harness_commits

    repo = _repo(tmp_path)
    root = _commit(repo, HARNESS, {".github/CODEOWNERS": "* @x\n", "src/a.ts": "x\n"}, "feat: all")

    walk = walk_harness_commits(repo, "HEAD")

    assert walk.commits[0].sha == root and walk.stopped_at == ""
    assert set(walk.commits[0].paths) == {".github/CODEOWNERS", "src/a.ts"}
    with pytest.raises(GitHubError, match="CODEOWNERS"):
        _guard_client(tmp_path).push_branch(repo, "main", remote_repo="o/fork")


def test_b297_a_merge_commit_carries_the_paths_it_brought_in(tmp_path):
    """B297 (handoff 8, test 13): a merge the harness made brings a `.github/` change in on its
    first-parent diff, and the walk sees it. Plain `git log` shows a merge no paths at all; on
    git 2.31 and later `--first-parent` alone already gives a merge its first-parent diff, so
    `--diff-merges=first-parent` is redundant there and kept for older git and for the reader --
    this asserts the property (the walk sees the path), not the flag that is one way to get it."""
    from harness.clone import walk_harness_commits

    repo = _repo(tmp_path)
    _commit(repo, DEV, {"README.md": "# p\n"}, "chore: seed")
    _git(repo, "checkout", "-q", "-b", "side")
    _commit(repo, HARNESS, {".github/dependabot.yml": "version: 2\n"}, "chore: deps")
    _git(repo, "checkout", "-q", "main")
    _commit(repo, HARNESS, {"src/app.ts": "export {};\n"}, "fix: thing")
    _git(repo, "-c", f"user.email={HARNESS}", "-c", "user.name=h", "merge", "-q", "--no-ff",
         "-m", "chore: bring in side", "side")

    walk = walk_harness_commits(repo, "HEAD")

    assert ".github/dependabot.yml" in walk.commits[0].paths
    with pytest.raises(GitHubError, match="dependabot"):
        _guard_client(tmp_path).push_branch(repo, "main", remote_repo="o/fork")


def test_b297_past_the_cap_the_walk_refuses_rather_than_truncates(tmp_path, monkeypatch):
    """B297 (handoff 8, test 14): a walk that takes the cap's worth of harness commits and finds
    more cannot vouch for the ones below, so the push is refused, not cut short in silence.
    The cap is 100; three runs the same code in a fraction of the commits."""
    from harness import clone

    assert clone.PROTECTED_SCAN_COMMITS == 100
    monkeypatch.setattr(clone, "PROTECTED_SCAN_COMMITS", 3)
    repo = _repo(tmp_path)
    _commit(repo, DEV, {"README.md": "# p\n"}, "chore: seed")
    for n in range(3):
        _commit(repo, HARNESS, {f"src/{n}.ts": "x\n"}, f"fix: {n}")
    assert not clone.walk_harness_commits(repo, "HEAD").capped, "exactly the cap is vouched for"
    _commit(repo, HARNESS, {"src/3.ts": "x\n"}, "fix: 3")

    walk = clone.walk_harness_commits(repo, "HEAD")

    assert walk.capped and len(walk.commits) == 3 and walk.protected() == []
    with pytest.raises(GitHubError, match="never checked"):
        _guard_client(tmp_path).push_branch(repo, "main", remote_repo="o/fork")


@pytest.mark.parametrize("change", ["delete", "rename"])
def test_b297_deleting_or_moving_a_workflow_is_refused(tmp_path, change):
    """B297 (handoff 8, test 8): D42's bug at the new layer. Deleting a workflow is not milder
    than editing one, and `--no-renames` lists a move as the deletion it is."""
    repo = _repo(tmp_path)
    _commit(repo, DEV, {CI: "on: push\n"}, "ci: pipeline")
    if change == "delete":
        _commit(repo, HARNESS, {CI: None}, "chore: drop the pipeline")
    else:
        (repo / "docs").mkdir()
        _git(repo, "mv", CI, "docs/ci-cd.yml")
        _commit(repo, HARNESS, {}, "docs: park the pipeline")

    with pytest.raises(GitHubError, match="ci-cd"):
        _guard_client(tmp_path).push_branch(repo, "main", remote_repo="o/fork")


def test_b297_a_non_ascii_workflow_name_is_still_seen(tmp_path):
    """B297: `core.quotepath=off`, plus the quote strip, so git's escaping cannot hide one."""
    repo = _repo(tmp_path)
    _commit(repo, DEV, {"README.md": "# p\n"}, "chore: seed")
    _commit(repo, HARNESS, {".github/workflows/déploiement.yml": "on: push\n"}, "ci: fr")

    with pytest.raises(GitHubError, match="workflows"):
        _guard_client(tmp_path).push_branch(repo, "main", remote_repo="o/fork")


def test_b297_a_case_variant_of_a_harness_email_is_still_walked(tmp_path):
    """B297: stopping early is the direction that checks less, so case is folded."""
    repo = _repo(tmp_path)
    _commit(repo, DEV, {"README.md": "# p\n"}, "chore: seed")
    _commit(repo, "Harness@LocalHost", {".github/CODEOWNERS": "* @x\n"}, "chore: owners")

    with pytest.raises(GitHubError, match="CODEOWNERS"):
        _guard_client(tmp_path).push_branch(repo, "main", remote_repo="o/fork")


def test_b298_push_ref_still_makes_exactly_one_git_call(tmp_path):
    """B298 (handoff 8, test 15): `push_ref` -- sync-fork's relay of upstream's own main -- is
    outside the walk, so it is still exactly one git call, and that call is the push."""
    calls: list = []
    client = _guard_client(tmp_path, dry_run=False)

    client.push_ref(tmp_path,"upstream/main:refs/heads/main", remote_repo="o/fork",
                    git_runner=_recorder(calls))

    assert len(calls) == 1 and "push" in calls[0]


def test_b298_push_branch_walks_through_the_injected_runner_before_it_pushes(tmp_path):
    """B298 (handoff 8, test 16): the check runs through `git_runner`, not `run_command`, and
    before the push; the fake sees both argvs, the walk first."""
    calls: list = []
    client = _guard_client(tmp_path, dry_run=False)

    client.push_branch(tmp_path,"harness/fix-1-x", remote_repo="o/fork",
                       git_runner=_recorder(calls))

    # B312's two history probes, then the walk, then the push -- nothing leaves first.
    assert [argv[1] for argv in calls[:2]] == ["for-each-ref", "rev-parse"]
    assert [("log" in argv, "push" in argv) for argv in calls[2:]] == [(True, False),
                                                                        (False, True)]
    assert "--first-parent" in calls[2] and calls[2][-2:] == ["harness/fix-1-x", "--"]


def test_b298_a_walk_git_cannot_run_refuses_the_push(tmp_path):
    """B298 (handoff 8, test 17): exit 128, no `.git` -- the walk checked nothing, so the push
    is refused. It fails closed."""
    calls: list = []
    client = _guard_client(tmp_path, dry_run=False)

    with pytest.raises(GitHubError, match="could not be listed"):
        client.push_branch(tmp_path,"harness/fix-1-x", remote_repo="o/fork",
                           git_runner=_recorder(calls, 128, "fatal: not a git repository"))

    assert len(calls) == 1 and "push" not in calls[0]
    assert _pushed(client) == []


def test_b298_without_a_token_the_walk_never_runs(tmp_path):
    """B298 (handoff 8, test 18): `_require_write` still comes first; no subprocess at all."""
    from harness.errors import TierViolation

    calls: list = []
    client = _guard_client(tmp_path, token="")

    with pytest.raises(TierViolation):
        client.push_branch(tmp_path,"b", remote_repo="o/fork", git_runner=_recorder(calls))

    assert calls == []


def test_b298_a_dry_run_over_a_real_clone_still_refuses(tmp_path):
    """B298 (handoff 8, test 19): a dry run reports the refusal it would make."""
    repo = _repo(tmp_path)
    _commit(repo, DEV, {"README.md": "# p\n"}, "chore: seed")
    _commit(repo, HARNESS, {".github/dependabot.yml": "version: 2\n"}, "chore: deps")
    client = _guard_client(tmp_path, dry_run=True)

    with pytest.raises(GitHubError, match="dependabot"):
        client.push_branch(repo, "main", remote_repo="o/fork")

    assert client.sent == []


def test_b312_the_push_guard_refuses_a_clone_with_a_replace_ref(tmp_path):
    """B312/D67: a `refs/replace/` entry maps the harness's `.github/` commit to a replacement
    whose tree lacks it, so the walk sees a clean history -- but the push sends the real
    objects. The guard refuses to read a substituted clone rather than walk the wrong one."""
    repo = _repo(tmp_path)
    _commit(repo, DEV, {"README.md": "# p\n"}, "chore: seed")
    real = _commit(repo, HARNESS, {".github/workflows/ci.yml": "on: push\n"}, "ci: sneak")
    twin = _git(repo, "commit-tree", f"{real}~1^{{tree}}", "-p", f"{real}~1", "-m", "twin")
    _git(repo, "replace", real, twin)
    client = _guard_client(tmp_path, dry_run=False)

    with pytest.raises(GitHubError, match="substituted"):
        client.push_branch(repo, "main", remote_repo="o/fork")

    assert _pushed(client) == []


def test_b305_token_scopes_reads_x_oauth_scopes_on_an_unconditional_request(tmp_path):
    """B305: the scopes come off `X-OAuth-Scopes`, on a request that carries the token and no
    `If-None-Match` -- a 304 replayed from the cache is no source for a credential's scopes."""
    opener = FakeOpener(FakeResponse(
        {"login": "bot"}, headers={"X-OAuth-Scopes": "workflow, public_repo,notifications"}
    ))
    client = _guard_client(tmp_path, opener=opener)
    client.store.cache_put(f"{API}/user", 'W/"cached"', json.dumps({"login": "bot"}))

    assert client.token_scopes() == ("notifications", "public_repo", "workflow")

    request = opener.requests[0]
    assert header_value(request, "If-None-Match") is None
    assert header_value(request, "Authorization") == f"token {GUARD_TOKEN}"


def test_b305_no_scopes_header_means_cannot_tell(tmp_path):
    """B305: a fine-grained token sends no `X-OAuth-Scopes`; that is "unknown", not "none"."""
    opener = FakeOpener(FakeResponse({"login": "bot"}))

    assert _guard_client(tmp_path, opener=opener).token_scopes() is None
