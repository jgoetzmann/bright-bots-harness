"""GitHub-as-queue store (Delivery 2 section 4).

A work item is one issue in ``self_repo`` carrying exactly one ``harness:*`` state label. Labels
are read fresh on every call (B102: a human's relabel is honoured, never overwritten from a
cache); two state labels raise ``StoreError`` (B100); each transition is one ``set_labels`` plus
one B101 comment. The columns an issue cannot hold (spec_path, base_sha, branch_name,
package_path, attempts, previous_state, ...) travel in a hidden ``<!-- harness-meta {...} -->``
marker appended to the comment thread; the latest marker wins. Stage runs, budget, cache, API
call counts and events live in ``scratch``, a per-run :class:`SqliteStore`.

The GitHub client is duck-typed: ``get`` (Delivery 1) for reads; ``set_labels``, ``comment``,
``create_issue``, ``create_branch_file`` and ``create_pull`` (RUN-DECISIONS-D2 section 7) for
writes. Nothing here reads an execution-mode environment variable (I-16).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from harness.clock import Clock, iso, parse_iso
from harness.errors import DuplicateWorkItem, GitHubError, IllegalTransition, StoreError
from harness.redact import redact
from harness.store.sqlite import (
    LABELS,
    STALE_PREVIOUS,
    TRANSITIONS,
    UPDATABLE_COLUMNS,
    SqliteStore,
    StageRun,
    WorkItem,
    bare_filename,
)

STATE_OF_LABEL: dict[str, str] = {label: state for state, label in LABELS.items()}

META_PREFIX = "<!-- harness-meta "
META_SUFFIX = " -->"
_META_RE = re.compile(r"<!--\s*harness-meta\s*(\{.*?\})\s*-->", re.DOTALL)
# A live-run marker: a plain comment (no harness-meta) naming a workflow run.
_RUN_MARKER_RE = re.compile(r"actions/runs/\d+|<!--\s*harness-run\b")
_PARENT_RE = re.compile(r"Parent:\s*#(\d+)")
_SUB_REF_RE = re.compile(r"^sub:(\d+):\d+$")
_NOT_FOUND_RE = re.compile(r"\b404\b|not found", re.IGNORECASE)

PER_PAGE = 100
MAX_PAGES = 50

# Meta keys that map onto WorkItem columns.
_META_COLUMNS = (
    "spec_path",
    "package_path",
    "base_sha",
    "branch_name",
    "attempts",
    "parent_id",
    "depends_on",
    "tier_required",
)


def _label_names(issue: Mapping[str, Any]) -> list[str]:
    names: list[str] = []
    for label in issue.get("labels") or []:
        name = label.get("name") if isinstance(label, Mapping) else label
        if isinstance(name, str) and name:
            names.append(name)
    return names


def _state_of(issue: Mapping[str, Any]) -> str | None:
    """The state named by the issue's labels; None when it carries none (not a work item)."""
    states = [STATE_OF_LABEL[name] for name in _label_names(issue) if name in STATE_OF_LABEL]
    if len(states) > 1:
        number = issue.get("number", "?")
        labels = ", ".join(LABELS[state] for state in states)
        raise StoreError(f"issue #{number} carries {len(states)} harness state labels: {labels}")
    return states[0] if states else None


def _origin_ref(issue: Mapping[str, Any]) -> str:
    """The external_ref an item was created with: the first non-blank line of the body."""
    body = str(issue.get("body") or "")
    for line in body.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _parse_meta(body: str) -> dict[str, Any] | None:
    found = None
    for match in _META_RE.finditer(body):
        try:
            data = json.loads(match.group(1))
        except ValueError:
            continue
        if isinstance(data, dict):
            found = data
    return found


def _latest_meta(comments: list[dict]) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    for comment in comments:
        found = _parse_meta(str(comment.get("body") or ""))
        if found is not None:
            meta = found
    return meta


def _meta_marker(meta: Mapping[str, Any]) -> str:
    return META_PREFIX + json.dumps(dict(meta), sort_keys=True, default=str) + META_SUFFIX


def _int_or_none(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return parse_iso(value)
    except ValueError:
        return None


def _item_from(issue: Mapping[str, Any], meta: Mapping[str, Any], state: str) -> WorkItem:
    number = int(issue["number"])
    parent = _int_or_none(meta.get("parent_id"))
    if parent is None:
        match = _PARENT_RE.search(str(issue.get("body") or ""))
        parent = int(match.group(1)) if match else None
    return WorkItem(
        id=number,
        kind="issue",
        external_ref=f"self:{number}",
        title=str(issue.get("title") or ""),
        state=state,
        parent_id=parent,
        depends_on=_int_or_none(meta.get("depends_on")),
        tier_required=_int_or_none(meta.get("tier_required")) or 0,
        spec_path=_str_or_none(meta.get("spec_path")),
        package_path=_str_or_none(meta.get("package_path")),
        base_sha=_str_or_none(meta.get("base_sha")),
        branch_name=_str_or_none(meta.get("branch_name")),
        attempts=_int_or_none(meta.get("attempts")) or 0,
        created_at=str(issue.get("created_at") or ""),
        updated_at=str(issue.get("updated_at") or ""),
    )


class GitHubStore:
    """The queue is GitHub; ``scratch`` holds everything that is per-run."""

    def __init__(
        self,
        gh: Any,
        *,
        self_repo: str,
        scratch: SqliteStore,
        clock: Clock,
        run_url: str = "",
    ) -> None:
        if not self_repo or "/" not in self_repo:
            raise StoreError(f"self_repo must be 'owner/name', got {self_repo!r}")
        self.gh = gh
        self.self_repo = self_repo
        self.scratch = scratch
        self._clock = clock
        self.run_url = run_url
        # item id -> (stage, usd) of the latest finish_stage_run in this process (B101 comment).
        self._last_run: dict[int, tuple[str, float]] = {}
        # run id -> (item id, stage) for runs started in this process.
        self._runs: dict[int, tuple[int, str]] = {}

    # ------------------------------------------------------------------ plumbing

    def _pages(self, path: str) -> list[dict]:
        """Every page of a list endpoint. Page 1 is the bare path; later pages add ``&page=N``."""
        collected: list[dict] = []
        for page in range(1, MAX_PAGES + 1):
            url = path if page == 1 else f"{path}&page={page}"
            data = self.gh.get(url)
            if not isinstance(data, list):
                break
            collected.extend(dict(row) for row in data if isinstance(row, Mapping))
            if len(data) < PER_PAGE:
                break
        return collected

    def _issues(self, *, state: str = "all", label: str | None = None) -> list[dict]:
        query = f"state={state}"
        if label:
            query += f"&labels={label}"
        query += f"&per_page={PER_PAGE}"
        rows = self._pages(f"/repos/{self.self_repo}/issues?{query}")
        # GitHub serves pull requests from the issues endpoint; they are not work items.
        return [row for row in rows if "pull_request" not in row]

    def _issue(self, number: int) -> dict | None:
        """The issue, read fresh (B102); None when it does not exist or is a pull request."""
        try:
            data = self.gh.get(f"/repos/{self.self_repo}/issues/{int(number)}")
        except GitHubError as exc:
            if _NOT_FOUND_RE.search(str(exc)):
                return None
            raise
        if not isinstance(data, Mapping) or "pull_request" in data:
            return None
        return dict(data)

    def _comments(self, number: int) -> list[dict]:
        return self._pages(
            f"/repos/{self.self_repo}/issues/{int(number)}/comments?per_page={PER_PAGE}"
        )

    def _meta(self, number: int) -> dict[str, Any]:
        return _latest_meta(self._comments(number))

    def _require(self, item_id: int) -> tuple[dict, str]:
        issue = self._issue(item_id)
        state = _state_of(issue) if issue is not None else None
        if issue is None or state is None:
            raise StoreError(f"no work item {item_id}")
        return issue, state

    def _set_state_label(self, issue: Mapping[str, Any], to_state: str) -> None:
        kept = [name for name in _label_names(issue) if name not in STATE_OF_LABEL]
        self.gh.set_labels(self.self_repo, int(issue["number"]), kept + [LABELS[to_state]])

    def _transition(
        self,
        item_id: int,
        to_state: str,
        *,
        reason: str,
        extra_meta: Mapping[str, Any] | None = None,
    ) -> None:
        issue, from_state = self._require(item_id)
        if to_state not in TRANSITIONS.get(from_state, frozenset()):
            raise IllegalTransition(
                f"illegal transition {from_state} -> {to_state} for work item {item_id}"
            )
        meta = self._meta(item_id)
        meta.update(dict(extra_meta or {}))
        meta["previous_state"] = from_state
        meta["ts"] = iso(self._clock.now())
        self._set_state_label(issue, to_state)
        stage, usd = self._last_run.get(item_id, ("-", 0.0))
        body = (
            f"**harness** `{stage}` → `{to_state}`\n"
            f"run: {self.run_url}\ncost: ${usd:.2f}\n{reason}"
        )
        self.gh.comment(self.self_repo, item_id, redact(body + "\n\n" + _meta_marker(meta)))
        item = _item_from(issue, meta, to_state)
        self.scratch._mirror_work_item(item)
        self.scratch.append_event(
            item_id, "info", f"transition {from_state} -> {to_state}: {reason}"
        )

    def _ensure_mirror(self, item_id: int) -> None:
        """Scratch needs a work_item row for its foreign keys; fetch it once per process."""
        if self.scratch.get_work_item(item_id) is not None:
            return
        item = self.get_work_item(item_id)
        if item is None:
            raise StoreError(f"no work item {item_id}")
        self.scratch._mirror_work_item(item)

    # ----------------------------------------------------------------- migration

    def migrate(self) -> None:
        self.scratch.migrate()

    def close(self) -> None:
        self.scratch.close()

    # ---------------------------------------------------------------- work items

    def create_work_item(
        self,
        *,
        kind: str,
        external_ref: str,
        title: str,
        tier_required: int = 0,
        body: str = "",
    ) -> int:
        """Open an issue in ``self_repo`` labelled ``harness:queued``; returns its number.

        The issue body is ``external_ref`` on its first line (the duplicate check and
        ``find_by_ref`` read it back), then ``body`` when given, then ``Parent: #N`` for a
        ``sub:N:i`` reference (S11; the caller never appends it).
        """
        for issue in self._issues(state="open"):
            if _origin_ref(issue) == external_ref:
                raise DuplicateWorkItem(
                    f"work item already exists for {external_ref}: #{issue.get('number')}"
                )
        text = f"{external_ref}\n\n{body}" if body else external_ref
        sub = _SUB_REF_RE.match(external_ref)
        if sub:
            text = f"{text}\n\nParent: #{sub.group(1)}"
        created = self.gh.create_issue(title, redact(text), [LABELS["discovered"]])
        number = int(created["number"])
        meta: dict[str, Any] = {
            "external_ref": external_ref,
            "kind": kind,
            "ts": iso(self._clock.now()),
        }
        if tier_required:
            meta["tier_required"] = int(tier_required)
        if sub:
            meta["parent_id"] = int(sub.group(1))
        self.gh.comment(self.self_repo, number, _meta_marker(meta))
        return number

    def get_work_item(self, item_id: int) -> WorkItem | None:
        issue = self._issue(item_id)
        if issue is None:
            return None
        state = _state_of(issue)
        if state is None:
            return None
        meta = dict(self._meta(item_id))
        # Without a credential the meta marker is never posted; the scratch mirror carries it.
        mirror = self.scratch.get_work_item(item_id)
        if mirror is not None:
            for field in ("spec_path", "base_sha", "branch_name", "package_path", "attempts"):
                if meta.get(field) in (None, "", 0) and getattr(mirror, field) not in (None, ""):
                    meta[field] = getattr(mirror, field)
        return _item_from(issue, meta, state)

    def find_by_ref(self, external_ref: str) -> WorkItem | None:
        """``self:<n>`` is issue n; anything else matches the ref the item was created with."""
        if external_ref.startswith("self:") and external_ref[5:].isdigit():
            return self.get_work_item(int(external_ref[5:]))
        issues = [issue for issue in self._issues() if _state_of(issue) is not None]
        for issue in issues:
            if _origin_ref(issue) == external_ref:
                number = int(issue["number"])
                return _item_from(issue, self._meta(number), _state_of(issue) or "")
        for issue in issues:
            number = int(issue["number"])
            meta = self._meta(number)
            if meta.get("external_ref") == external_ref:
                return _item_from(issue, meta, _state_of(issue) or "")
        return None

    def list_work_items(self, *, state: str | None = None) -> list[WorkItem]:
        if state is not None and state not in LABELS:
            return []
        label = LABELS[state] if state is not None else None
        items: list[WorkItem] = []
        for issue in self._issues(label=label):
            found = _state_of(issue)
            if found is None or (state is not None and found != state):
                continue
            items.append(_item_from(issue, self._meta(int(issue["number"])), found))
        return sorted(items, key=lambda item: item.id)

    def transition(self, item_id: int, to_state: str, *, reason: str) -> None:
        """One ``set_labels`` (old state label out, new one in, others kept) + one B101 comment."""
        self._transition(item_id, to_state, reason=reason)

    def update_work_item(self, item_id: int, **fields: object) -> None:
        """Record columns in the meta marker (and scratch); ``state`` swaps the label."""
        unknown = sorted(str(k) for k in fields if k not in UPDATABLE_COLUMNS)
        if unknown:
            raise StoreError(f"unknown work_item column(s): {', '.join(unknown)}")
        issue, state = self._require(item_id)
        meta = self._meta(item_id)
        new_state = fields.get("state")
        for name, value in fields.items():
            if name in ("state", "updated_at"):
                continue
            meta[name] = value
        meta["ts"] = iso(self._clock.now())
        if new_state is not None and new_state != state:
            if new_state not in LABELS:
                raise StoreError(f"unknown state {new_state!r} for work item {item_id}")
            self._set_state_label(issue, str(new_state))
            state = str(new_state)
        if getattr(self.gh, "can_write", False):
            # The marker is a cache of scratch; without a credential scratch alone carries it.
            self.gh.comment(self.self_repo, item_id, redact(_meta_marker(meta)))
        self.scratch._mirror_work_item(_item_from(issue, meta, state))

    # ------------------------------------------------------------- delivery 2 seam

    def publish_proposal(self, item_id: int, filename: str, text: str) -> str:
        """Branch + file + PR in ``self_repo`` (gate 1); ``proposing -> proposed``; the PR URL."""
        name = bare_filename(filename)
        item = self.get_work_item(item_id)
        if item is None:
            raise StoreError(f"no work item {item_id}")
        branch = f"harness/propose-{item_id}"
        path = f"proposals/{name}"
        self.gh.create_branch_file(
            self.self_repo,
            branch=branch,
            path=path,
            content=redact(text),
            message=f"harness: proposal for #{item_id}",
        )
        pull = self.gh.create_pull(
            self.self_repo,
            head=branch,
            base="main",
            title=f"proposal: {item.title} (#{item_id})",
            body=redact(
                f"Proposal for #{item_id}: `{path}`.\n\n"
                "Merging this PR approves the proposal (gate 1); closing it rejects it."
            ),
        )
        url = ""
        if isinstance(pull, Mapping):
            url = str(pull.get("html_url") or pull.get("url") or "")
        self._transition(
            item_id,
            "proposed",
            reason=f"proposal PR {url}",
            extra_meta={"proposal_url": url, "proposal_path": path},
        )
        return url

    def merged_issues(self) -> set[int]:
        """Numbers of every issue labelled ``harness:merged``."""
        merged: set[int] = set()
        for issue in self._issues(label=LABELS["merged"]):
            if _state_of(issue) == "merged":
                merged.add(int(issue["number"]))
        return merged

    def reconcile_stale_running(self, older_than_iso: str) -> list[int]:
        """B147: mid-flight items whose label predates the cutoff, with no live-run marker
        comment since, go back to the ``previous_state`` of their meta marker."""
        cutoff = parse_iso(older_than_iso)
        reset: list[int] = []
        for state in ("implementing", "revising", "proposing"):
            for issue in self._issues(label=LABELS[state]):
                if _state_of(issue) != state:
                    continue
                number = int(issue["number"])
                comments = self._comments(number)
                applied = self._label_applied_at(issue, comments, LABELS[state])
                if applied >= cutoff:
                    continue
                if self._has_live_marker(comments, applied):
                    continue
                target = str(_latest_meta(comments).get("previous_state") or "")
                if target not in TRANSITIONS.get(state, frozenset()):
                    target = STALE_PREVIOUS[state]
                self._transition(
                    number,
                    target,
                    reason=(
                        f"reconcile: {state} since {iso(applied)} with no live run "
                        f"(older than {older_than_iso}) (B147)"
                    ),
                )
                reset.append(number)
        return reset

    def _label_applied_at(
        self, issue: Mapping[str, Any], comments: list[dict], label: str
    ) -> datetime:
        """When the state label went on: the issue's ``labeled`` event, else the latest
        transition comment, else the issue's ``updated_at``."""
        number = int(issue["number"])
        latest: datetime | None = None
        try:
            events = self._pages(
                f"/repos/{self.self_repo}/issues/{number}/events?per_page={PER_PAGE}"
            )
        except (GitHubError, LookupError, ValueError, TypeError):
            events = []
        for event in events:
            if event.get("event") != "labeled":
                continue
            name = event.get("label")
            name = name.get("name") if isinstance(name, Mapping) else name
            when = _timestamp(event.get("created_at"))
            if name == label and when is not None and (latest is None or when > latest):
                latest = when
        if latest is not None:
            return latest
        for comment in comments:
            body = str(comment.get("body") or "")
            when = _timestamp(comment.get("created_at"))
            if "harness-meta" in body and "previous_state" in body and when is not None:
                latest = when
        if latest is not None:
            return latest
        updated = _timestamp(issue.get("updated_at"))
        return updated if updated is not None else self._clock.now()

    @staticmethod
    def _has_live_marker(comments: list[dict], since: datetime) -> bool:
        for comment in comments:
            body = str(comment.get("body") or "")
            if "harness-meta" in body or not _RUN_MARKER_RE.search(body):
                continue
            when = _timestamp(comment.get("created_at"))
            if when is not None and when > since:
                return True
        return False

    # ---------------------------------------------------------------- stage runs

    def start_stage_run(self, work_item_id: int, stage: str, backend: str) -> int:
        self._ensure_mirror(work_item_id)
        run_id = self.scratch.start_stage_run(work_item_id, stage, backend)
        self._runs[run_id] = (work_item_id, stage)
        return run_id

    def finish_stage_run(
        self,
        run_id: int,
        *,
        status: str,
        turns: int | None,
        allowance_pct: float | None,
        cost_usd: float | None,
        exit_reason: str | None,
        transcript_path: str | None,
    ) -> None:
        self.scratch.finish_stage_run(
            run_id,
            status=status,
            turns=turns,
            allowance_pct=allowance_pct,
            cost_usd=cost_usd,
            exit_reason=exit_reason,
            transcript_path=transcript_path,
        )
        owner = self._runs.get(run_id)
        if owner is None:
            for run in self.scratch.list_stage_runs():
                if run.id == run_id:
                    owner = (run.work_item_id, run.stage)
                    break
        if owner is not None:
            self._last_run[owner[0]] = (owner[1], float(cost_usd or 0.0))

    def list_stage_runs(
        self, work_item_id: int | None = None, status: str | None = None
    ) -> list[StageRun]:
        return self.scratch.list_stage_runs(work_item_id, status)

    def completed_allowances(self, stage: str) -> list[float]:
        return self.scratch.completed_allowances(stage)

    # -------------------------------------------------------------------- events

    def append_event(self, work_item_id: int | None, level: str, message: str) -> None:
        if work_item_id is not None:
            self._ensure_mirror(work_item_id)
        self.scratch.append_event(work_item_id, level, message)

    def events(self, work_item_id: int | None = None) -> list[dict]:
        return self.scratch.events(work_item_id)

    # -------------------------------------------------------------------- budget

    def ensure_budget_period(
        self, unit: str, period_start: str, period_end: str, allocated: float
    ) -> None:
        self.scratch.ensure_budget_period(unit, period_start, period_end, allocated)

    def budget_period(self, unit: str, period_start: str) -> tuple[float, float]:
        return self.scratch.budget_period(unit, period_start)

    def consume_budget(self, unit: str, period_start: str, amount: float) -> None:
        self.scratch.consume_budget(unit, period_start, amount)

    # ---------------------------------------------------------------- http cache

    def cache_get(self, url: str) -> tuple[str | None, str] | None:
        return self.scratch.cache_get(url)

    def cache_put(self, url: str, etag: str | None, body: str) -> None:
        self.scratch.cache_put(url, etag, body)

    # ----------------------------------------------------------------- api calls

    def record_api_call(self, url: str, status: int, cached: bool) -> None:
        self.scratch.record_api_call(url, status, cached)

    def api_calls_since(self, iso_ts: str) -> int:
        return self.scratch.api_calls_since(iso_ts)


__all__ = ["GitHubStore", "STATE_OF_LABEL", "META_PREFIX", "META_SUFFIX", "PER_PAGE"]
