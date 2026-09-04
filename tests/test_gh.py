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
    client.create_label("me/self", name="harness:queued", color="#0e8a16", description="secret=abc")
    (call,) = client.sent
    assert call["method"] == "POST" and call["url"].endswith("/repos/me/self/labels")
    assert call["payload"]["name"] == "harness:queued" and call["payload"]["color"] == "0e8a16"
    assert "abc" not in call["payload"]["description"]

    unarmed = GitHubClient("o/r", store, clock, 50, token="", self_repo="me/self", dry_run=True)
    with pytest.raises(TierViolation):
        unarmed.create_label("me/self", name="x", color="000000")
    assert unarmed.sent == []
