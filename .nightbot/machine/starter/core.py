"""The cloud-agnostic half of the night bot's starter: does a job wait for the machine?

Each cloud's handler (``aws/starter.py``, ``azure/starter.py``, ``gcp/main.py``) runs every five
minutes, reads its machine's power state and calls :func:`tick`: if the machine is stopped and a
job of the night bot's workflow waits for one of its runners, it starts the machine. The machine
stops itself once idle (``common/idle-stop.sh``), so a stopped machine and a queued ``nightbot-*``
job mean a job is waiting for it. A job that has waited longer than :data:`MAX_WAIT` is left alone:
its runner is missing, and starting the machine for it would only keep the machine on for nothing
until GitHub gives the job up (after 24 hours).

Standard library only; each deploy script packages this file beside its handler.

Environment (:func:`env_config`):

``REPO``
    ``OWNER/NAME``; or ``REPO_ID``, the repository's numeric id, which survives a rename or a
    transfer (preferred when both are set).
``WORKFLOWS``
    Comma-separated workflow files whose jobs may wait for the machine (``nightbot-run.yml``).
``LABEL_PREFIX``
    A machine runner's label prefix (``nightbot-``).
``RUNNER_LABELS``
    Comma-separated labels: only jobs for these wake this machine (when several machines share
    the work). Without it, any label with the prefix does.
``GITHUB_TOKEN``
    Required for a private repository: a fine-grained token with read access to Actions on it,
    nothing else. Optional for a public one (it only raises the rate limit).
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping

API = "https://api.github.com"
#: A job waiting for a runner. (``waiting`` is a deployment approval, not a runner.)
QUEUED = ("queued", "pending", "requested")
#: The runs whose jobs may wait: GitHub can list a run as ``pending`` while its job is ``queued``
#: for a runner, and a run whose first job ran is ``in_progress`` while its next one waits.
RUN_STATUSES = ("queued", "pending", "in_progress")
MAX_WAIT = timedelta(hours=3)
DEFAULT_WORKFLOWS = "nightbot-run.yml"
DEFAULT_PREFIX = "nightbot-"
TIMEOUT = 20  # seconds, for every HTTP call

Get = Callable[[str], dict]
Opener = Callable[..., Any]  # urllib.request.urlopen's shape: (request, timeout=…) -> response


@dataclass(frozen=True)
class Config:
    repo: str  # OWNER/NAME or a numeric id
    workflows: tuple[str, ...]
    label_prefix: str
    only: frozenset[str] | None
    token: str


def _split(text: str | None) -> list[str]:
    return [part.strip() for part in (text or "").split(",") if part.strip()]


def env_config(env: Mapping[str, str] | None = None) -> Config:
    """The starter's settings from the environment (the module docstring lists them)."""
    env = os.environ if env is None else env
    repo = (env.get("REPO_ID") or "").strip() or (env.get("REPO") or "").strip()
    if not repo:
        raise ValueError("set REPO (OWNER/NAME) or REPO_ID (the repository's numeric id)")
    if not (repo.isdigit() or re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo)):
        raise ValueError(f"REPO={repo!r}: not OWNER/NAME")
    return Config(
        repo=repo,
        workflows=tuple(_split(env.get("WORKFLOWS")) or [DEFAULT_WORKFLOWS]),
        label_prefix=(env.get("LABEL_PREFIX") or "").strip() or DEFAULT_PREFIX,
        only=frozenset(_split(env.get("RUNNER_LABELS"))) or None,
        token=(env.get("GITHUB_TOKEN") or "").strip(),
    )


def request_json(method: str, url: str, headers: Mapping[str, str] | None = None,
                 opener: Opener | None = None) -> dict:
    """One HTTP call; its JSON body, or ``{}`` for an empty one. A POST sends an empty body."""
    request = urllib.request.Request(url, method=method, headers=dict(headers or {}),
                                     data=b"" if method == "POST" else None)
    with (opener or urllib.request.urlopen)(request, timeout=TIMEOUT) as response:
        body = response.read()
    return json.loads(body) if body else {}


def github_get(token: str = "", opener: Opener | None = None) -> Get:
    """A GET of GitHub's REST API: ``get("/repos/…")`` is the response's JSON."""
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
               "User-Agent": "nightbot-starter"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    def get(path: str) -> dict:
        return request_json("GET", API + path, headers, opener)
    return get


def _when(text: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")) if text else None
    except ValueError:
        return None


def waiting_jobs(repo: str, workflows: Iterable[str], get: Get, now: datetime,
                 label_prefix: str = DEFAULT_PREFIX,
                 only: Iterable[str] | None = None) -> list[dict]:
    """The jobs of the workflows' active runs that wait for a machine runner: queued, carrying a
    label with ``label_prefix`` (or, given ``only``, one of those labels), created less than
    :data:`MAX_WAIT` before ``now``. Each is ``{"run": <run id>, "job": <name>, "label": …}``."""
    base = f"/repositories/{repo}" if repo.isdigit() else f"/repos/{repo}"
    wanted = None if only is None else set(only)
    found: list[dict] = []
    seen: set[int] = set()
    for workflow in workflows:
        quoted = urllib.parse.quote(workflow, safe="")
        for status in RUN_STATUSES:
            runs = get(f"{base}/actions/workflows/{quoted}/runs?status={status}&per_page=30"
                       ).get("workflow_runs") or []
            for run in runs:
                if run["id"] in seen:
                    continue
                seen.add(run["id"])
                jobs = get(f"{base}/actions/runs/{run['id']}/jobs?filter=latest&per_page=100"
                           ).get("jobs") or []
                for job in jobs:
                    if job.get("status") not in QUEUED:
                        continue
                    labels = [label for label in job.get("labels") or []
                              if (label in wanted if wanted is not None
                                  else label.startswith(label_prefix))]
                    since = _when(job.get("created_at"))
                    if labels and (since is None or now - since < MAX_WAIT):
                        found.append({"run": run["id"], "job": job.get("name"),
                                      "label": labels[0]})
    return found


def decide(stopped: bool, waiting: list) -> bool:
    """Start the machine: it is stopped, and a job waits for it."""
    return stopped and bool(waiting)


def _describe(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}"
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 404:
        text += " (a private repository needs GITHUB_TOKEN; or REPO is wrong)"
    elif isinstance(exc, urllib.error.HTTPError) and exc.code in (401, 403, 429):
        text += " (the token is refused, or the rate limit is spent: set GITHUB_TOKEN)"
    return text


def tick(state: str, stopped: bool, start: Callable[[], Any], config: Config,
         get: Get | None = None, now: datetime | None = None,
         log: Callable[[str], Any] = print) -> dict:
    """One look: GitHub is asked only when the machine is ``stopped``; ``start()`` runs when a job
    waits. A machine on its way up or down is looked at again next time. An error reading GitHub
    (an outage, the rate limit) is logged and starts nothing."""
    result: dict[str, Any] = {"state": state, "started": False}
    if not stopped:
        return result
    get = get or github_get(config.token)
    now = now or datetime.now(timezone.utc)
    try:
        waiting = waiting_jobs(config.repo, config.workflows, get, now,
                               config.label_prefix, config.only)
    except (OSError, ValueError) as exc:  # URLError, HTTPError, timeouts; a bad JSON body
        result["error"] = _describe(exc)
        log(json.dumps(result))
        return result
    if not decide(stopped, waiting):
        return result
    start()
    result.update(started=True, waiting=waiting)
    log(json.dumps(result))
    return result
