"""Unauthenticated, cached, read-only GitHub client (spec §5.5)."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta
from typing import Any, Callable, Sequence

from harness import __version__
from harness.clock import Clock, iso
from harness.errors import GitHubError, RateCeilingReached
from harness.store import Store

API_BASE = "https://api.github.com"
ACCEPT = "application/vnd.github+json"
USER_AGENT = f"bright-bots-harness/{__version__}"
PER_PAGE = 100

_LINK_RE = re.compile(r'<([^>]+)>\s*;\s*rel="next"', re.IGNORECASE)


def _header(headers: Any, name: str) -> str | None:
    """Read one header off any object exposing ``.get`` (or nothing at all)."""
    if headers is None:
        return None
    value = headers.get(name)
    return None if value is None else str(value)


def _next_link(headers: Any) -> str | None:
    """Return the ``rel="next"`` URL from a ``Link`` header, when present."""
    raw = _header(headers, "Link")
    if not raw:
        return None
    match = _LINK_RE.search(raw)
    if match is None:
        return None
    return match.group(1).strip()


def _close(response: Any) -> None:
    closer = getattr(response, "close", None)
    if callable(closer):
        try:
            closer()
        except Exception:
            pass


def _body_text(response: Any) -> str:
    reader = getattr(response, "read", None)
    if reader is None:
        return ""
    try:
        raw = reader()
    except Exception:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", "replace")
    return str(raw)


def _decode(body: str, url: str) -> Any:
    if body.strip() == "":
        return {}
    try:
        return json.loads(body)
    except ValueError as exc:
        raise GitHubError(f"unparseable JSON from {url}: {exc}") from exc


def _query(pairs: Sequence[tuple[str, str]]) -> str:
    return urllib.parse.urlencode(list(pairs), quote_via=urllib.parse.quote)


class GitHubReadOnly:
    """Read-only GitHub client. Never authenticates, never writes (§9 I-1, I-2)."""

    def __init__(
        self,
        repo: str,
        store: Store,
        clock: Clock,
        ceiling_per_hour: int,
        opener: Callable[[urllib.request.Request], Any] | None = None,
    ) -> None:
        self.repo = repo
        self.store = store
        self.clock = clock
        self.ceiling_per_hour = int(ceiling_per_hour)
        self._opener = opener if opener is not None else urllib.request.urlopen

    # ---------------------------------------------------------------- metering

    def _window_start(self) -> str:
        return iso(self.clock.now() - timedelta(hours=1))

    def rate_budget_remaining(self) -> int:
        """Calls still allowed inside the trailing hour (never negative)."""
        used = int(self.store.api_calls_since(self._window_start()))
        return max(0, self.ceiling_per_hour - used)

    def _check_ceiling(self, url: str) -> None:
        if self.rate_budget_remaining() <= 0:
            raise RateCeilingReached(
                f"github api ceiling of {self.ceiling_per_hour}/hour reached; "
                f"refusing to fetch {url}"
            )

    # ------------------------------------------------------------------- fetch

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if not path.startswith("/"):
            path = "/" + path
        return API_BASE + path

    def _fetch(self, url: str) -> tuple[Any, Any]:
        """Return ``(decoded_body, response_headers)`` for one read of ``url``."""
        self._check_ceiling(url)
        cached = self.store.cache_get(url)
        headers = {"User-Agent": USER_AGENT, "Accept": ACCEPT}
        if cached is not None and cached[0]:
            headers["If-None-Match"] = cached[0]
        request = urllib.request.Request(url, headers=headers, method="GET")

        try:
            response = self._opener(request)
        except urllib.error.HTTPError as exc:
            return self._handle_http_error(url, exc, cached)

        status = int(getattr(response, "status", 200) or 200)
        response_headers = getattr(response, "headers", None)

        if status == 304:
            self.store.record_api_call(url, 304, True)
            _close(response)
            if cached is None:
                raise GitHubError(f"304 for an uncached url: {url}")
            return _decode(cached[1], url), response_headers

        body = _body_text(response)
        _close(response)
        self.store.record_api_call(url, status, False)

        if status >= 400:
            self._raise_for_status(url, status, body, response_headers)

        etag = _header(response_headers, "ETag")
        self.store.cache_put(url, etag, body)
        return _decode(body, url), response_headers

    def _handle_http_error(self, url: str, exc: Any, cached: Any) -> tuple[Any, Any]:
        code = int(getattr(exc, "code", 0) or 0)
        error_headers = getattr(exc, "headers", None)

        if code == 304:
            self.store.record_api_call(url, 304, True)
            if cached is None:
                raise GitHubError(f"304 for an uncached url: {url}")
            return _decode(cached[1], url), error_headers

        self.store.record_api_call(url, code, False)
        self._raise_for_status(url, code, _body_text(exc), error_headers)

    def _raise_for_status(self, url: str, status: int, body: str, headers: Any) -> None:
        if status == 403:
            remaining = _header(headers, "X-RateLimit-Remaining")
            body_says_rate_limit = "rate limit" in (body or "").lower()
            header_says_exhausted = remaining is not None and remaining.strip() == "0"
            if body_says_rate_limit or header_says_exhausted:
                raise RateCeilingReached(
                    f"github refused {url} with 403 rate limit; unauthenticated reads "
                    "are capped at 60/hour per address"
                )
        tail = (body or "").strip().replace("\n", " ")[:200]
        raise GitHubError(f"github returned {status} for {url}: {tail}")

    # ------------------------------------------------------------------ public

    def get(self, path: str) -> dict | list:
        """The single request path. ``path`` is API-relative or an absolute URL."""
        data, _ = self._fetch(self._url(path))
        return data

    def _paginate(self, path: str) -> list[dict]:
        url: str | None = self._url(path)
        collected: list[dict] = []
        seen: set[str] = set()
        while url:
            if url in seen:
                break
            seen.add(url)
            data, headers = self._fetch(url)
            if isinstance(data, list):
                collected.extend(item for item in data if isinstance(item, dict))
            url = _next_link(headers)
        return collected

    def issue(self, number: int) -> dict:
        data = self.get(f"/repos/{self.repo}/issues/{int(number)}")
        if not isinstance(data, dict):
            raise GitHubError(f"expected an object for issue {number}, got a list")
        return data

    def issues(self, *, state: str = "open", labels: Sequence[str] = ()) -> list[dict]:
        pairs: list[tuple[str, str]] = [("state", state)]
        joined = ",".join(str(label) for label in labels if str(label) != "")
        if joined:
            pairs.append(("labels", joined))
        pairs.append(("per_page", str(PER_PAGE)))
        rows = self._paginate(f"/repos/{self.repo}/issues?{_query(pairs)}")
        # GitHub serves pull requests from the issues endpoint; they are not issues.
        return [row for row in rows if "pull_request" not in row]

    def pulls(self, *, state: str = "open") -> list[dict]:
        pairs = [("state", state), ("per_page", str(PER_PAGE))]
        return self._paginate(f"/repos/{self.repo}/pulls?{_query(pairs)}")

    def branches(self) -> list[str]:
        pairs = [("per_page", str(PER_PAGE))]
        rows = self._paginate(f"/repos/{self.repo}/branches?{_query(pairs)}")
        names: list[str] = []
        for row in rows:
            name = row.get("name")
            if isinstance(name, str) and name:
                names.append(name)
        return names
