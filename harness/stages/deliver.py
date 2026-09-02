"""The deliver stage (handoff §4.5): sync the fork, rebase, push the branch, open the upstream
pull request from the review package, request trusted reviewers, and ship the item.

Everything that leaves this machine goes through ``harness.gh`` (I-11) and through ``redact``
(I-13). Nothing here merges, approves, or dismisses anything (I-12). Nothing here inspects the
diff for forbidden paths: that is B64 in ``implement.py``, and there is exactly one copy of it.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
from pathlib import Path
from typing import Any, Callable

from harness import clone as clone_mod
from harness import gates, redact
from harness.clone import Lease
from harness.context import Context
from harness.errors import GitHubError, HarnessError
from harness.halt import check_halt
from harness.redact import write_redacted
from harness.stages.propose import parse_work_package

__all__ = [
    "GIT",
    "MAX_BODY_CHARS",
    "SYNC_FORK",
    "build_pr_body",
    "build_pr_title",
    "deliver",
    "lease_from_store",
]

log = logging.getLogger("harness")

#: GitHub refuses a pull-request body above 65536 characters. The evidence is kept whole up to
#: here; the run artifact carries the rest.
MAX_BODY_CHARS = 60000
MAX_TITLE_CHARS = 100

TRUNCATION_NOTE = (
    "\n\n> **Truncated here.** The complete `EVIDENCE.md` is in the run artifact "
    "(`runs/item-<id>/package/`) uploaded by the workflow that opened this pull request.\n"
)

#: Author identity for the rebase (a rebase rewrites committer, not author).
GIT_IDENTITY = (
    "-c",
    "user.name=Bright Bots Harness",
    "-c",
    "user.email=harness@brightboost-harness",
)


def _sync_fork(config: Any, *, workdir: Path, push: Callable[[Path, str], None]) -> str:
    """B105: fast-forward the fork's main from upstream, or raise ``ForkDiverged``."""
    return clone_mod.sync_fork(config, workdir=workdir, push=push)


# Module-level injectables, as in ``implement.py``. Tests replace these; production uses git.
GIT = gates.run_command
SYNC_FORK = _sync_fork


# --------------------------------------------------------------------------------------------
# pure pieces
# --------------------------------------------------------------------------------------------


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def lease_from_store(ctx: Context, item: Any) -> Lease:
    """Rebuild the implement-time lease from the store, as ``harness package <id>`` does."""
    if not item.branch_name:
        raise HarnessError(f"item {item.id} has no branch recorded; run implement first")
    if not item.base_sha:
        raise HarnessError(f"item {item.id} has no base sha recorded; run implement first")
    run_id = f"item-{item.id}"
    return Lease(
        run_id=run_id,
        path=Path(ctx.config.runs_dir) / run_id / "clone",
        base_sha=str(item.base_sha),
        branch=str(item.branch_name),
    )


def build_pr_title(spec_title: str, fallback: str, upstream_issue: int | None) -> str:
    """The work package's conventional-commit header, with the upstream issue when it fits."""
    first = (spec_title or "").strip().splitlines()
    title = first[0].strip() if first else ""
    title = title or (fallback or "").strip() or "harness change"
    if upstream_issue is not None:
        with_ref = f"{title} (#{upstream_issue})"
        if len(with_ref) <= MAX_TITLE_CHARS:
            return with_ref
    return title[:MAX_TITLE_CHARS]


def build_pr_body(
    package_dir: Path,
    *,
    upstream_repo: str,
    fork_repo: str,
    branch: str,
    base_sha: str,
    self_repo: str,
    item_id: int,
) -> str:
    """B108: README + DIAGNOSIS + EVIDENCE from the package, verbatim, plus the reconstruction
    commands of ``docs/PACKAGE-FORMAT.md`` §3 — redacted, and capped at GitHub's limit."""
    readme = _read(package_dir / "README.md").strip()
    diagnosis = _read(package_dir / "DIAGNOSIS.md").strip()
    evidence = _read(package_dir / "EVIDENCE.md").strip()
    base = _read(package_dir / "BASE").strip() or base_sha
    patches_dir = package_dir / "patches"
    patches = sorted(p.name for p in patches_dir.glob("*.patch")) if patches_dir.is_dir() else []
    fork_owner = fork_repo.split("/")[0] if fork_repo else "the fork"

    parts: list[str] = [
        "<!-- opened by the Bright Bots Harness; generated from the review package (B108) -->",
        (
            f"_Automated pull request from `{fork_owner}`. Nothing here merges itself: a trusted "
            f"human reviews and merges, or closes. Harness work item: `{self_repo}#{item_id}`._"
        ),
        "",
        readme or "# Review package\n\n_README.md was missing from the package._",
        "",
        "---",
        "",
        diagnosis or "# Diagnosis\n\n_DIAGNOSIS.md was missing from the package._",
        "",
        "---",
        "",
        evidence or "# Evidence\n\n_EVIDENCE.md was missing from the package._",
        "",
        "---",
        "",
        "## Reconstruction",
        "",
        (
            f"Base commit `{base}` exists upstream in `{upstream_repo}`; the branch `{branch}` "
            f"on `{fork_repo}` was rebased onto the fork's main, which is a fast-forward of "
            "upstream (B105). To rebuild the exact tree from the public repositories alone:"
        ),
        "",
        "```bash",
        f"git -c core.autocrlf=false clone https://github.com/{upstream_repo}.git r && cd r",
        f"git checkout {base}",
        f"git fetch https://github.com/{fork_repo}.git {branch}",
        "git checkout FETCH_HEAD",
        "```",
        "",
        "With the review package on disk instead (`docs/PACKAGE-FORMAT.md` §3):",
        "",
        "```bash",
        f"git -c core.autocrlf=false clone https://github.com/{upstream_repo}.git r && cd r",
        'git checkout "$(cat ../BASE)"',
        "git am ../patches/*.patch",
        "```",
        "",
        "Or from the bundle, with no network at all:",
        "",
        "```bash",
        "git bundle verify bundle.gitbundle",
        f"git clone bundle.gitbundle -b {branch} r",
        "```",
        "",
    ]
    if patches:
        parts.append(f"Patch series ({len(patches)}):")
        parts.append("")
        parts.extend(f"- `patches/{name}`" for name in patches)
        parts.append("")
    body = redact.redact("\n".join(parts))
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS] + TRUNCATION_NOTE
    return body


# --------------------------------------------------------------------------------------------
# the stage
# --------------------------------------------------------------------------------------------


def deliver(ctx: Context, item_id: int, *, lease: Lease | None = None) -> str:
    """Push the branch and open the upstream PR. Returns its URL.

    Returns ``""`` when the client cannot write: the branch stays in the clone for the host to
    push and ``runs/<id>/DELIVER.json`` records what would have been sent. ``lease`` is passed
    by ``revise`` when the clone is already held; otherwise it is rebuilt from the store.
    """
    check_halt(ctx.config.halt_file)

    item = ctx.store.get_work_item(item_id)
    if item is None:
        raise HarnessError(f"no work item {item_id}")
    if item.state not in ("packaged", "revising"):
        raise HarnessError(
            f"item {item_id} is in state {item.state!r}; deliver requires packaged "
            "(or revising, from a revise cycle)"
        )
    nested = lease is not None
    the_lease = lease if lease is not None else lease_from_store(ctx, item)

    package_dir = Path(item.package_path) if item.package_path else ctx.run_dir / "package"
    if not package_dir.is_dir():
        raise HarnessError(f"no review package for item {item_id} at {package_dir}")

    upstream = str(ctx.config.upstream_repo)
    fork = str(ctx.config.fork_repo or "")
    fork_owner = fork.split("/")[0] if fork else ""
    self_repo = str(ctx.config.self_repo)
    reviewers = sorted(str(handle) for handle in ctx.trusted)

    spec_text = _read(Path(item.spec_path)) if item.spec_path else ""
    pkg = parse_work_package(spec_text)
    upstream_issue = item.issue_number if item.external_ref.startswith("issue:") else None
    title = build_pr_title(pkg.title, item.title, upstream_issue)
    body = build_pr_body(
        package_dir,
        upstream_repo=upstream,
        fork_repo=fork,
        branch=the_lease.branch,
        base_sha=the_lease.base_sha,
        self_repo=self_repo,
        item_id=item_id,
    )
    head = f"{fork_owner}:{the_lease.branch}"
    record: dict[str, Any] = {
        "schema": 1,
        "item_id": item_id,
        "branch": the_lease.branch,
        "base_sha": the_lease.base_sha,
        "head": head,
        "base": "main",
        "upstream_repo": upstream,
        "fork_repo": fork,
        "title": title,
        "reviewers": reviewers,
        "body_chars": len(body),
        "pushed": False,
        "pr_number": None,
        "pr_url": "",
    }

    if not ctx.gh.can_write:
        _write_record(ctx, record)
        ctx.record_decision(
            f"deliver: no credential, so nothing was pushed or opened; branch {the_lease.branch} "
            f"stays in {the_lease.path} for the host to push and DELIVER.json carries the PR "
            "title, body size and reviewers"
        )
        ctx.store.append_event(
            item_id, "info", "deliver skipped: no credential; branch left for the host"
        )
        return ""

    # 1. sync-fork (B105): fast-forward only, loud on divergence, nothing pushed otherwise.
    fork_sha = SYNC_FORK(
        ctx.config,
        workdir=ctx.run_dir / "fork-sync",
        push=lambda path, refspec: ctx.gh.push_ref(path, refspec, remote_repo=fork),
    )
    ctx.record_decision(f"fork main is a fast-forward of upstream at {fork_sha}")
    check_halt(ctx.config.halt_file)

    # 2. rebase the work branch onto the fork's main.
    default_branch = _default_branch(ctx, upstream)
    record["base"] = default_branch
    conflicted = _rebase(the_lease, default_branch)
    if conflicted:
        ctx.record_decision(
            f"rebase onto {default_branch} conflicted in {len(conflicted)} file(s): "
            + ", ".join(conflicted)
        )
        if nested:
            raise HarnessError(
                f"item {item_id}: rebase conflicted again after a revise cycle; leaving the "
                "clone mid-rebase for inspection"
            )
        from harness.stages.revise import revise  # lazy: revise imports deliver

        revise(ctx, item_id, source="conflict", lease=the_lease)
        return _recorded_url(ctx)
    ctx.record_decision(f"rebased {the_lease.branch} onto {default_branch} cleanly")
    check_halt(ctx.config.halt_file)

    # 3. push the branch to the fork — never to upstream (§5.3).
    ctx.gh.push_branch(the_lease.path, the_lease.branch, remote_repo=fork)
    record["pushed"] = True
    ctx.record_decision(f"pushed {the_lease.branch} to {fork}")

    # 4. the pull request, fork -> upstream default branch; reused when one is already open.
    existing = _find_open_pull(ctx, upstream, head)
    if existing is not None:
        pr = existing
        tip = _tip_sha(the_lease)
        ctx.gh.comment(
            upstream,
            int(pr.get("number") or 0),
            redact.redact(
                f"Branch `{the_lease.branch}` updated by the harness"
                + (f" (tip `{tip}`)" if tip else "")
                + f"; gates re-run, evidence in the run artifact for `{self_repo}#{item_id}`."
            ),
        )
        ctx.record_decision(f"reused open pull request #{pr.get('number')} for {head}")
    else:
        pr = ctx.gh.create_pull(upstream, head=head, base=default_branch, title=title, body=body)
        ctx.record_decision(f"opened pull request #{pr.get('number')} on {upstream} from {head}")
    number = int(pr.get("number") or 0)
    url = str(pr.get("html_url") or "")
    record["pr_number"] = number or None
    record["pr_url"] = url

    # 5. reviewers = every trusted handle. A refusal (not a collaborator yet) is recorded, not fatal.
    if reviewers and number:
        try:
            ctx.gh.request_reviewers(upstream, number, reviewers)
            ctx.record_decision(f"requested review from {', '.join(reviewers)}")
        except GitHubError as exc:
            ctx.record_decision(f"could not request reviewers {reviewers}: {exc}")

    # 6. the harness issue: comment with the URL (the store's transition comment) and ship.
    _write_record(ctx, record)
    ctx.store.append_event(item_id, "info", f"delivery PR {url or number}")
    ctx.store.transition(item_id, "shipped", reason=f"delivery PR opened: {url}")
    log.info("delivered item %s -> %s", item_id, url)
    return url


# --------------------------------------------------------------------------------------------
# git and GitHub helpers
# --------------------------------------------------------------------------------------------


def _rebase(lease: Lease, default_branch: str) -> list[str]:
    """Rebase the branch onto ``origin/<default_branch>``. Returns the conflicted paths, and
    leaves the rebase in progress when there are any so ``revise`` can resolve them."""
    code, _, err = GIT(["git", "fetch", "origin", default_branch], lease.path)
    if code != 0:
        raise HarnessError(f"git fetch origin {default_branch} failed: {err.strip()[-2000:]}")
    code, out, err = GIT(["git", *GIT_IDENTITY, "rebase", "FETCH_HEAD"], lease.path)
    if code == 0:
        return []
    code2, names, _ = GIT(["git", "diff", "--name-only", "--diff-filter=U"], lease.path)
    conflicted = [n.strip() for n in names.splitlines() if n.strip()] if code2 == 0 else []
    if conflicted:
        return conflicted
    GIT(["git", "rebase", "--abort"], lease.path)
    raise HarnessError(f"git rebase failed without conflicts: {(err or out).strip()[-2000:]}")


def _tip_sha(lease: Lease) -> str:
    code, out, _ = GIT(["git", "rev-parse", "HEAD"], lease.path)
    return out.strip()[:12] if code == 0 else ""


def _default_branch(ctx: Context, upstream: str) -> str:
    try:
        data = ctx.gh.get(f"/repos/{upstream}")
    except GitHubError:
        return "main"
    name = data.get("default_branch") if isinstance(data, dict) else None
    return str(name) if name else "main"


def _find_open_pull(ctx: Context, upstream: str, head: str) -> dict | None:
    query = urllib.parse.urlencode(
        [("head", head), ("state", "open"), ("per_page", "100")], quote_via=urllib.parse.quote
    )
    try:
        data = ctx.gh.get(f"/repos/{upstream}/pulls?{query}")
    except GitHubError:
        return None
    if isinstance(data, list):
        for row in data:
            if isinstance(row, dict) and row.get("number"):
                return row
    return None


def _write_record(ctx: Context, record: dict[str, Any]) -> None:
    write_redacted(ctx.run_dir / "DELIVER.json", json.dumps(record, indent=2) + "\n")


def _recorded_url(ctx: Context) -> str:
    raw = _read(ctx.run_dir / "DELIVER.json").strip()
    if not raw:
        return ""
    try:
        data = json.loads(raw)
    except ValueError:
        return ""
    return str(data.get("pr_url") or "") if isinstance(data, dict) else ""
