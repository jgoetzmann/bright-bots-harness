"""The deliver stage (handoff §4.5): sync the fork, rebase, push, open the upstream PR, ship."""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
from pathlib import Path
from typing import Any, Iterable, Sequence

from harness import clone as clone_mod
from harness import gates, links, redact
from harness.clock import iso
from harness.clone import Lease
from harness.context import Context
from harness.errors import GitHubError, HarnessError, IllegalTransition
from harness.halt import check_halt
from harness.redact import write_redacted
from harness.stages.propose import parse_work_package, work_package_text


def _spec_text(ctx: Context, item: Any) -> str:
    """B226: the work package, from wherever it still exists; empty when it exists nowhere."""
    try:
        return work_package_text(item, repo_root=ctx.config.repo_root)
    except HarnessError:
        return ""




__all__ = [
    "DECISION_TAIL_LINES",
    "HANDOFF_FROM_STATES",
    "HANDOFF_NAME",
    "MAX_BODY_CHARS",
    "MAX_COMMENT_CHARS",
    "SYNC_FORK",
    "build_handoff_body",
    "build_pr_body",
    "build_pr_title",
    "deliver",
    "handoff",
    "lease_from_store",
]

log = logging.getLogger("harness")

#: GitHub refuses a pull-request body above 65536 characters. The evidence is kept whole up to
#: here; the run artifact carries the rest.
MAX_BODY_CHARS = 60000
MAX_TITLE_CHARS = 100

#: ``runs/item-<id>/HANDOFF.md`` — the note a stopped run leaves for the run that resumes it.
HANDOFF_NAME = "HANDOFF.md"

#: The same GitHub ceiling applies to the handoff comment as to a pull-request body.
MAX_COMMENT_CHARS = 60000

#: How much of the run's reasoning the handoff carries forward (B213).
DECISION_TAIL_LINES = 20

#: B214: the states a half-finished item can be handed off from. Anything else is either
#: already parked (blocked, needs-human) or already gone (shipped, merged, abandoned).
HANDOFF_FROM_STATES: tuple[str, ...] = ("implementing", "packaged", "revising")

#: B314/D67: where a handoff puts the fork's `main`, fetched at check time. A ref of the
#: harness's own, force-updated by that fetch, so nothing the model left in the clone -- a
#: moved `refs/remotes/origin/main` included -- can stand in for it.
GUARD_FORK_MAIN = "refs/harness-guard/fork-main"

#: B315: the commits a handoff withheld, as a patch series beside HANDOFF.md. An Actions run
#: uploads `runs/` but not the clone, which does not outlive the run, so this is what a person
#: can still read afterwards.
WITHHELD_NAME = "WITHHELD.patch"
MAX_WITHHELD_CHARS = 2_000_000

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

# The one module-level injectable, as in ``implement.py``: tests replace it so no test can
# reach the network. The push has no injectable of its own — it goes through ``ctx.gh``, which
# every test already fakes. Every git command in this module runs through ``gates.run_command``
# inside the lease's clone.
SYNC_FORK = clone_mod.sync_fork


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


def _reference_lines(
    *, self_repo: str, upstream_repo: str, item_id: int, upstream_issue: int | None
) -> str:
    """The closing keyword and the back-reference, as GitHub understands them (B227).

    ``Closes`` only where merging really does resolve the thing named: this pull request is
    against the product repository, so merging it closes the product issue. The harness work
    item lives in another repository and is referenced, not closed -- it closes when the item
    itself reaches its terminal state, not when this merges.
    """
    lines = []
    closing = links.closes(upstream_repo, upstream_issue, same_repo=True)
    if closing:
        lines.append(closing)
    lines.append(
        f"Harness work item: [{links.issue_ref(self_repo, item_id)}]"
        f"({links.issue_url(self_repo, item_id)})"
    )
    return "\n".join(lines)


#: How much of a gate's output is worth inlining when it passed: none. The verbatim capture
#: lives in the review package, which is attached to the run and rebuildable from the public
#: repositories alone.
EVIDENCE_TAIL_CHARS = 6000

#: `### <gate> — exit code <n> (PASS|FAIL)`, as `packager._gate_section` writes it.
_GATE_HEADING = re.compile(
    r"^###\s+(?P<name>.+?)\s+[-–—]\s+exit code\s+"
    r"(?P<code>\d+)\s*\((?P<verdict>PASS|FAIL)\)\s*$"
)


_PHASE_SPLIT = re.compile(r"\s+[-–—]\s+")


def evidence_digest(evidence: str) -> str:
    """The gate results as a table, with any failure kept whole (B232/D52).

    The first delivery pull request was 52 KB, 40 KB of which was the verbatim stdout of seven
    gates that all passed — a wall nobody scrolls, in the one place a reviewer has to read.
    What a reviewer needs from a green run is that it was green; what they need from a red one
    is all of it. The complete capture is in the review package either way, and the package is
    the artifact of record.
    """
    text = (evidence or "").strip()
    if not text:
        return "_EVIDENCE.md was missing from the package._"

    sections: list[tuple[str, str, str, str, list[str]]] = []
    preamble: list[str] = []
    phase = ""
    for line in text.splitlines():
        if line.startswith("## "):
            # `## Baseline - untouched tree at BASE` and its siblings. Without this the
            # table lists every gate twice with nothing to say which run is which.
            phase = _PHASE_SPLIT.split(line[3:].strip(), maxsplit=1)[0]
        match = _GATE_HEADING.match(line)
        if match:
            sections.append(
                (phase, match.group("name"), match.group("code"), match.group("verdict"), [])
            )
        elif sections:
            sections[-1][4].append(line)
        else:
            preamble.append(line)

    if not sections:
        return text[:EVIDENCE_TAIL_CHARS]

    out = [line for line in preamble if line.strip().startswith("- ")]
    out.append("")
    out.append("| when | gate | exit | |")
    out.append("|---|---|---|---|")
    for phase_name, name, code, verdict, _body in sections:
        mark = "PASS" if verdict == "PASS" else "**FAIL**"
        out.append(f"| {phase_name or 'the run'} | `{name}` | {code} | {mark} |")

    failures = [section for section in sections if section[3] != "PASS"]
    for phase_name, name, code, _verdict, body in failures:
        out.append("")
        out.append(f"#### {phase_name}: {name} — exit code {code}")
        out.append(chr(10).join(body).strip()[:EVIDENCE_TAIL_CHARS])
    if not failures:
        out.append("")
        out.append(
            "Every gate passed. The verbatim output of each is in the review package "
            "(`EVIDENCE.md`), attached to the run and rebuildable with the commands below."
        )
    return "\n".join(out).strip()


def _collapsed(summary: str, body: str) -> str:
    """A `<details>` block, so the pull request opens at something a person can read."""
    return f"<details>\n<summary>{summary}</summary>\n\n{body.strip()}\n\n</details>"


def _trusted_handles(trusted: Iterable[str] | None) -> str:
    names = sorted({str(h).strip().lstrip("@") for h in (trusted or ()) if str(h).strip()})
    if not names:
        return "nobody (the trust file is empty)"
    return ", ".join(f"@{name}" for name in names)


def _checklist(evidence: str) -> str:
    """`CONTRIBUTING.md`'s reviewer checklist, with what the harness measured already ticked."""
    verdicts: dict[str, bool] = {}
    for line in (evidence or "").splitlines():
        match = _GATE_HEADING.match(line)
        if match:
            verdicts[match.group("name").strip()] = match.group("verdict") == "PASS"

    def tick(gate: str) -> str:
        return "x" if verdicts.get(gate) else " "

    return "\n".join(
        [
            f"- [{tick('npm run build')}] Does it build? — `npm run build`, on this branch",
            f"- [{tick('npm run lint')}] Does it pass lint? — `npm run lint`",
            f"- [{tick('npm run test:unit')}] Does it pass tests? — `npm run test:unit`",
            "- [ ] Hardcoded English rather than i18n keys — yours to check",
            "- [ ] Mobile responsive — yours to check",
            "- [ ] Matches existing code patterns — yours to check",
            (
                "- [ ] Agent prompt logged — the prompt is `prompts/implement.md` in the harness "
                "repository, pinned by content hash; the full transcript is `transcript.jsonl` "
                "in the review package"
            ),
        ]
    )


def build_pr_body(
    package_dir: Path,
    *,
    upstream_repo: str,
    fork_repo: str,
    branch: str,
    base_sha: str,
    self_repo: str,
    item_id: int,
    upstream_issue: int | None = None,
    config: Any = None,
    trusted: Iterable[str] | None = None,
) -> str:
    """B108/B232: the pull request a reviewer actually reads.

    What they need first is on top and uncollapsed — what this closes, how to steer it, why CI
    may be waiting, and the checklist `CONTRIBUTING.md` asks them to work through. Everything
    that is evidence rather than argument sits behind a `<details>`, because the review package
    holds the authoritative copy and a pull request is not the place to reproduce it.
    """
    readme = _read(package_dir / "README.md").strip()
    diagnosis = _read(package_dir / "DIAGNOSIS.md").strip()
    evidence = _read(package_dir / "EVIDENCE.md").strip()
    base = _read(package_dir / "BASE").strip() or base_sha
    fork_owner = fork_repo.split("/")[0] if fork_repo else "the fork"

    rebuild = [
        (
            f"Base commit `{base}` exists here in `{upstream_repo}`; the branch `{branch}` on "
            f"`{fork_repo}` was rebased onto the fork's main, which is a fast-forward of this "
            "repository (B105)."
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
    ]

    parts: list[str] = [
        "<!-- opened by the Bright Bots Harness; generated from the review package (B108) -->",
    ]
    closing = links.closes(upstream_repo, upstream_issue, same_repo=True)
    if closing:
        parts.extend([closing, ""])
    parts.extend(
        [
            (
                f"Opened by the **Bright Bots Harness** from `{fork_owner}`. It cannot merge "
                "this and will not push again unless you ask it to. Work item: "
                f"[{links.issue_ref(self_repo, item_id)}]({links.issue_url(self_repo, item_id)})."
            ),
            "",
            # B234: the review request is also made through the API, but that needs push access
            # the machine account does not have on this repository. A mention notifies either
            # way, and it survives the request being refused.
            f"Review requested from {_trusted_handles(trusted)}.",
            "",
            "## Steering it from here",
            "",
            "Put one of these on its own line in a comment on this pull request:",
            "",
            "| comment | what happens |",
            "|---|---|",
            "| `/harness revise <notes>` | one more implementation pass, your notes as the brief |",
            "| `/harness rebase` | rebase onto this repository's current `main`, then push again |",
            "| `/harness stop` | close this and park the work item; `/harness go` puts it back |",
            "| `/harness status` | spend, queue, and when the next thing happens |",
            "",
            (
                f"Honoured only from {_trusted_handles(trusted)}, and only when GitHub also "
                "reports you as an owner, member or collaborator here. Everyone else's comments "
                "are read and ignored. A command is acted on once — editing a comment does not "
                "re-fire it, so post a new one."
            ),
            "",
            "## If the checks are not running",
            "",
            (
                "GitHub holds workflow runs from an account with no merged contribution here, "
                "so the first pull request from this one needs a maintainer to press **Approve "
                "and run** in the checks list on this page. Later ones start on their own."
            ),
            "",
            "## Review checklist",
            "",
            "From `CONTRIBUTING.md`. The harness ran the first three itself, on this branch:",
            "",
            _checklist(evidence),
            "",
            _collapsed("What this change is, and why", diagnosis or "_DIAGNOSIS.md was missing._"),
            "",
            _collapsed(
                "Gate results — this repository's own sequence, run on the branch",
                evidence_digest(evidence),
            ),
            "",
            _collapsed(
                "The review package, verbatim",
                readme or "_README.md was missing from the package._",
            ),
            "",
            _collapsed("Rebuild this exact tree yourself", "\n".join(rebuild)),
            "",
            _collapsed(
                "About the harness",
                links.signature(config, trusted=trusted, steerable=False),
            ),
        ]
    )
    body = redact.redact("\n".join(parts))
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS] + TRUNCATION_NOTE
    return body


# --------------------------------------------------------------------------------------------
# the stage
# --------------------------------------------------------------------------------------------


def deliver(ctx: Context, item_id: int, *, lease: Lease | None = None) -> str:
    """Push the branch and open the upstream PR; its URL, or ``""`` when the client cannot write."""
    ctx.check_halt()
    # B282/I-18: first, before the state check and long before the push. A delivery targets the
    # product repository; one that targeted the harness would be the harness proposing a change
    # to itself, with only a reviewer's attention between it and the rules that govern it.
    # `load_config` refuses this configuration outright -- this catches every other way
    # `upstream_repo` could have been set.
    if ctx.config.self_repo and (
        str(ctx.config.upstream_repo).strip().lower()
        == str(ctx.config.self_repo).strip().lower()
    ):
        raise HarnessError(
            f"refusing to deliver into {ctx.config.upstream_repo}: it is the harness's own "
            "repository, and the harness does not work on itself (I-18). Changes to the "
            "harness are made by a person."
        )

    item = ctx.store.get_work_item(item_id)
    if item is None:
        raise HarnessError(f"no work item {item_id}")
    if item.state not in ("packaged", "revising"):
        raise IllegalTransition(
            f"illegal transition {item.state} -> shipped for work item {item_id}: deliver "
            "requires packaged (or revising, from a revise cycle)"
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

    # B226: same fallback as implement and the packager -- the PR body must not lose the work
    # package just because propose ran on a different runner.
    spec_text = _spec_text(ctx, item)
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
        upstream_issue=upstream_issue,
        config=ctx.config,
        trusted=ctx.trusted,
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

    default_branch = _default_branch(ctx, upstream)
    record["base"] = default_branch
    if not nested:
        # 1. sync-fork (B105): fast-forward only, loud on divergence, nothing pushed otherwise.
        #    A clone whose origin is not on github.com (local remotes) has no fork to sync and
        #    must never reach the network.
        if _github_origin(the_lease):
            fork_sha = SYNC_FORK(
                ctx.config,
                workdir=ctx.run_dir / "fork-sync",
                push=lambda path, refspec: ctx.gh.push_ref(path, refspec, remote_repo=fork),
            )
            ctx.record_decision(f"fork main is a fast-forward of upstream at {fork_sha}")
        ctx.check_halt()

        # 2. rebase the work branch onto upstream's default branch, inside the clone.
        conflicted = _rebase(the_lease, upstream, default_branch)
        if conflicted:
            ctx.record_decision(
                f"rebase onto {default_branch} conflicted in {len(conflicted)} file(s): "
                + ", ".join(conflicted)
            )
            from harness.stages.revise import revise  # lazy: revise imports deliver

            revise(ctx, item_id, source="conflict", lease=the_lease)
            return _recorded_url(ctx)
        ctx.record_decision(
            f"rebased {the_lease.branch} onto upstream/{default_branch} cleanly"
        )
        ctx.check_halt()

        # 3. push the branch to the fork — never to upstream (§5.3). B313/D67: and only once no
        #    commit the push would send that upstream does not already hold -- whoever
        #    authored it -- touches `.github/`.
        _refuse_github_changes(ctx, the_lease)
        ctx.gh.push_branch(the_lease.path, the_lease.branch, remote_repo=fork)
        ctx.record_decision(f"pushed {the_lease.branch} to {fork}")
    record["pushed"] = True

    # 4. the pull request, fork -> upstream default branch.
    pr = ctx.gh.create_pull(upstream, head=head, base=default_branch, title=title, body=body)
    ctx.record_decision(f"opened pull request #{pr.get('number')} on {upstream} from {head}")
    number = int(pr.get("number") or 0)
    url = str(pr.get("html_url") or "")
    record["pr_number"] = number or None
    record["pr_url"] = url

    # 5. reviewers = every trusted handle. A refusal (not a collaborator yet) is recorded.
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


def _github_origin(lease: Lease) -> bool:
    """True when the clone's ``origin`` is on github.com — a real run. A test clone's origin is
    a local path, and nothing here may reach the network on its behalf."""
    code, out, _ = gates.run_command(["git", "remote", "get-url", "origin"], lease.path)
    return code == 0 and out.strip().startswith("https://github.com/")


def _rebase(lease: Lease, upstream_repo: str, default_branch: str) -> list[str]:
    """Rebase the branch onto upstream's default branch, fetched into the clone. Returns the
    conflicted paths, and leaves the rebase in progress when there are any so ``revise`` can
    resolve them. A real clone comes from the fork and gains the ``upstream`` remote here; a
    test clone brings its own."""
    code, _, _ = gates.run_command(["git", "remote", "get-url", "upstream"], lease.path)
    if code != 0 and _github_origin(lease):
        gates.run_command(
            ["git", "remote", "add", "upstream", f"https://github.com/{upstream_repo}.git"],
            lease.path,
        )
    code, _, err = gates.run_command(["git", "fetch", "upstream", default_branch], lease.path)
    if code != 0:
        raise HarnessError(f"git fetch upstream {default_branch} failed: {err.strip()[-2000:]}")
    code, out, err = gates.run_command(["git", *GIT_IDENTITY, "rebase", "FETCH_HEAD"], lease.path)
    if code == 0:
        return []
    conflicted = _conflicted_files(lease)
    if conflicted:
        return conflicted
    gates.run_command(["git", "rebase", "--abort"], lease.path)
    raise HarnessError(f"git rebase failed without conflicts: {(err or out).strip()[-2000:]}")


def _refuse_github_changes(ctx: Context, lease: Lease) -> None:
    """B313/D67: raise unless no commit on the branch above the upstream commit `_rebase` just
    put it on touches `.github/`, whoever authored it.

    The push guard's walk stops at the first commit a harness email did not author, and the
    model, holding Bash, can commit under any name; B64 read the working tree, and the branch a
    push sends is a ref the model could have moved away from it. This reads exactly the commits
    the push sends that upstream does not hold. Its anchor is `FETCH_HEAD`, which `_rebase`
    fetched after the model's last call, so nothing the model left in the clone stands in for
    it; upstream's own `.github/` commits sit below it and are relayed, as B299 requires. When
    the range cannot be read, nothing is pushed.
    """
    try:
        held = clone_mod.protected_paths_above(
            lease.path, lease.branch, ["FETCH_HEAD"], gates.run_command
        )
    except HarnessError as exc:
        ctx.record_decision(f"deliver did not push {lease.branch}: its commits were not read")
        raise HarnessError(
            f"refusing to push {lease.branch}: the commits on it above upstream could not be "
            f"read, so none of them was checked (D67): {exc}"
        ) from exc
    if held:
        listed = ", ".join(held[:10])
        ctx.record_decision(
            f"deliver did not push {lease.branch}: commits on it above upstream touch {listed}"
        )
        raise HarnessError(
            f"refusing to push {lease.branch}: commits on it that upstream does not hold touch "
            f"{listed}, whoever authored them; the harness never publishes a change under "
            ".github/ (I-15, D67)"
        )
    ctx.record_decision(
        f"author-blind check passed: no commit on {lease.branch} above upstream touches "
        ".github/ (D67)"
    )


def _conflicted_files(lease: Lease) -> list[str]:
    code, out, _ = gates.run_command(
        ["git", "diff", "--name-only", "--diff-filter=U"], lease.path
    )
    if code != 0:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def _tip_sha(lease: Lease) -> str:
    code, out, _ = gates.run_command(["git", "rev-parse", "HEAD"], lease.path)
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


# --------------------------------------------------------------------------------------------
# handoff (B212-B214): parking a half-finished item so a later run can resume it
# --------------------------------------------------------------------------------------------


def build_handoff_body(
    *,
    item_id: int,
    title: str,
    reason: str,
    branch: str,
    base_sha: str,
    fork: str,
    self_repo: str,
    clone: Path,
    gates_label: str,
    gate_lines: Sequence[str],
    decisions: Sequence[str],
    acceptance: Sequence[str],
    pushed: bool,
    committed: bool,
    now_iso: str,
    withheld: Sequence[str] = (),
    kept: str = "",
) -> str:
    """B213: everything the next run (or a human) needs, and nothing that needs a credential.

    Pure: the caller gathers the pieces, this renders them. The last section is the exact
    command that picks the work up again. ``withheld`` names the `.github/` paths that kept
    the branch off the fork (B301), so the note does not blame a missing credential; then the
    item is blocked, not carried, and the last section says what a person decides instead
    (B315). ``kept`` names the patch of the withheld commits beside this note, if one was
    written.
    """
    if pushed:
        push_note = f"pushed to `{fork}`"
    elif withheld:
        push_note = (
            "not pushed - withheld for "
            + ", ".join(f"`{item}`" for item in list(withheld)[:5])
            + "; the harness never publishes a change under `.github/` (I-15, D67)"
        )
    else:
        push_note = "not pushed - no write credential, so the branch exists only in the clone"
    intro = (
        (
            "The harness stopped part-way through this item, and its branch holds something the "
            "harness never publishes, so the push was withheld and the item is blocked until a "
            "person decides. Nothing was merged and nothing was pushed."
        )
        if withheld
        else (
            "The harness stopped part-way through this item and handed it back. Nothing was "
            "merged and nothing was force-pushed: every commit made so far is on the branch "
            "below, and one command resumes the work exactly where it stopped."
        )
    )
    parts: list[str] = [
        f"# Handoff - item {item_id}",
        "",
        intro,
        "",
        f"- **Reason:** {reason}",
        f"- **Item:** `{self_repo}#{item_id}` - {title or '(no title recorded)'}",
        f"- **Branch:** `{branch or '(none recorded)'}` - {push_note}",
        f"- **Base commit:** `{base_sha or '(none recorded)'}`",
        f"- **Fork:** `{fork or '(none configured)'}`",
        f"- **Clone:** `{clone}`",
        "- **Uncommitted work at the stop:** "
        + ("committed as a wip commit" if committed else "none - the tree was clean"),
        f"- **Handed off at:** {now_iso}",
    ]
    if withheld:
        parts.append(
            "- **Withheld work:** "
            + (
                f"`{kept}` beside this note - the branch's commits above the fork's `main`, "
                "as a patch series"
                if kept
                else "not kept - no patch of it could be written"
            )
            + ". An Actions run does not upload the clone, and the clone does not outlive the "
            "run."
        )
    parts.extend(["", "## Gate results", ""])
    if gate_lines:
        parts.append(f"The last recorded sequence (`gates/{gates_label}.json`):")
        parts.append("")
        parts.extend(str(line) for line in gate_lines)
    else:
        parts.append("_No gate sequence had been recorded when the harness stopped._")

    parts.extend(["", f"## Last {DECISION_TAIL_LINES} decisions", ""])
    if decisions:
        parts.extend(str(line) for line in decisions)
    else:
        parts.append("_No decisions were recorded for this run._")

    parts.extend(["", "## Remaining work", ""])
    if acceptance:
        parts.append(
            "The work package's acceptance criteria, verbatim. The item never reached a "
            "review package, so none of them is confirmed met:"
        )
        parts.append("")
        parts.extend(f"- [ ] {str(line).strip()}" for line in acceptance)
    else:
        parts.append("_The work package listed no acceptance criteria._")

    if withheld:
        parts.extend(
            [
                "",
                "## What happens next",
                "",
                "The item is `stage:blocked`, and nothing resumes it by itself. A maintainer "
                "reads the withheld work and decides; a change under `.github/` that is wanted "
                "is made by a person. `/harness go` returns the item to the queue, and it "
                "starts again from what the fork holds, which does not include the withheld "
                "commits.",
                "",
            ]
        )
        return "\n".join(parts)
    parts.extend(
        [
            "",
            "## Next command",
            "",
            "```bash",
            f"harness revise {item_id} --source continue",
            "```",
            "",
        ]
    )
    return "\n".join(parts)


def handoff(ctx: Context, item_id: int, *, reason: str) -> Path:
    """B212-B214: park a half-finished item and carry it into the next run.

    A usage stop is a normal outcome, not a failure. The work in the clone is committed,
    pushed to the fork when there is a credential (never to upstream, never forced), written
    up in ``runs/item-<id>/HANDOFF.md``, and the item goes back to ``approved`` with the
    ledger carrying it. ``harness revise <id> --source continue`` is the other half.
    """
    item = ctx.store.get_work_item(item_id)
    if item is None:
        raise HarnessError(f"no work item {item_id}")

    run_dir = Path(ctx.config.runs_dir) / f"item-{item_id}"
    clone = run_dir / "clone"
    if not clone.is_dir():
        raise HarnessError(
            f"no clone for item {item_id} at {clone}; there is no work in progress to hand off"
        )

    branch = str(item.branch_name or "")
    fork = str(ctx.config.fork_repo or "")
    self_repo = str(ctx.config.self_repo)
    now_iso = iso(ctx.clock.now())

    # 1. Nothing the model already wrote may be lost to a usage stop.
    committed = _commit_wip(ctx, clone, reason)
    # 2. B212: to the fork, only with a credential, and never with force. B301/D67: and never
    #    carrying anything under `.github/` -- the push is withheld, and B315 keeps the withheld
    #    commits where a person can still read them.
    pushed, withheld, anchor = _push_handoff(ctx, clone, branch, fork)
    kept = (
        _keep_withheld(ctx, run_dir, clone, branch, anchor or str(item.base_sha or ""))
        if withheld
        else ""
    )

    gates_label, gate_lines = _gate_summary(run_dir)
    body = redact.redact(
        build_handoff_body(
            item_id=item_id,
            title=str(item.title or ""),
            reason=reason,
            branch=branch,
            base_sha=str(item.base_sha or ""),
            fork=fork,
            self_repo=self_repo,
            clone=clone,
            gates_label=gates_label,
            gate_lines=gate_lines,
            decisions=_decisions_tail(run_dir),
            acceptance=_acceptance(ctx, item),
            pushed=pushed,
            committed=committed,
            now_iso=now_iso,
            withheld=withheld,
            kept=kept,
        )
    )
    path = run_dir / HANDOFF_NAME
    write_redacted(path, body)

    # 3. Say so where a human is watching, and where the next run will look.
    if ctx.gh.can_write:
        try:
            ctx.gh.comment(self_repo, item_id, body[:MAX_COMMENT_CHARS])
        except HarnessError as exc:
            ctx.record_decision(f"handoff could not comment on {self_repo}#{item_id}: {exc}")
    ctx.store.append_event(item_id, "warn", f"handoff: {reason}")
    if withheld:
        # B315: not carried. Nothing a resume could pick up reached the fork -- in Actions mode
        # `continue` would re-acquire a branch that is not there, or one without this work --
        # so the item waits for a person instead, like any other forbidden diff.
        ctx.record_decision(
            f"blocked item {item_id} on its handoff ({reason}): {branch} holds what the harness "
            f"never publishes, so it was not pushed; {path} says what"
            + (f", and {run_dir / kept} keeps the withheld commits" if kept else "")
        )
        current = ctx.store.get_work_item(item_id) or item
        if current.state in HANDOFF_FROM_STATES:
            try:
                ctx.store.transition(
                    item_id, "blocked", reason=f"handoff withheld a .github/ change: {reason}"
                )
            except HarnessError as exc:
                ctx.record_decision(f"could not block item {item_id} on handoff: {exc}")
        log.info("handed off item %s, blocked with its push withheld: %s", item_id, reason)
        return path
    ctx.record_decision(
        f"handed item {item_id} off ({reason}); the branch {branch or '(none)'} is "
        + ("on the fork" if pushed else "in the clone only")
        + f" and {path} says how to resume it with `harness revise {item_id} --source continue`"
    )

    # 4. B214: back to approved, and into the ledger's carry slot.
    current = ctx.store.get_work_item(item_id) or item
    if current.state in HANDOFF_FROM_STATES:
        try:
            ctx.store.transition(item_id, "approved", reason=f"handed off: {reason}")
        except HarnessError as exc:
            ctx.record_decision(f"could not return item {item_id} to approved on handoff: {exc}")
    if ctx.ledger is not None:
        ctx.ledger.set_carry(item_id, now_iso, reason)
        try:
            ctx.save_ledger()
        except (HarnessError, OSError) as exc:
            ctx.record_decision(f"could not persist the ledger after the handoff: {exc}")
    log.info("handed off item %s: %s", item_id, reason)
    return path


def _commit_wip(ctx: Context, clone: Path, reason: str) -> bool:
    """Commit whatever the stopped stage left behind, and only then. Returns whether it did."""
    code, out, err = gates.run_command(["git", "status", "--porcelain"], clone)
    if code != 0:
        ctx.record_decision(
            f"handoff could not read git status in {clone}: {(err or out).strip()[:200]}"
        )
        return False
    if not out.strip():
        return False
    from harness.stages import implement as implement_mod  # lazy: implement imports stages

    try:
        implement_mod.COMMIT(clone, "wip: handoff (" + reason + ")")
    except HarnessError as exc:
        ctx.record_decision(f"handoff could not commit the working tree in {clone}: {exc}")
        return False
    ctx.record_decision(f"handoff committed the uncommitted work in {clone} as a wip commit")
    return True


def _push_handoff(
    ctx: Context, clone: Path, branch: str, fork: str
) -> tuple[bool, list[str], str]:
    """B212: the carried branch goes to the fork, never upstream, and never with force.

    B301/D67: and never with a change under `.github/`. Only the push is withheld -- HANDOFF.md
    and the comment still happen, and B315 blocks the item and keeps the withheld commits --
    and the paths come back so the note can say why. `push_branch` would refuse some of it too;
    this names the paths first, and also sees what the push guard's walk cannot
    (`_unpublishable`). Returns ``(pushed, withheld, anchor)``; ``anchor`` is the ref the
    author-blind check read above, or "" when it had none.
    """
    if not ctx.gh.can_write or not branch:
        return False, [], ""
    held, anchor = _unpublishable(ctx, clone, branch)
    if held:
        ctx.record_decision(
            f"handoff withheld the push of {branch} for {', '.join(held[:10])}; the harness "
            "never publishes a change under .github/ (I-15, D67)"
        )
        return False, held, anchor
    try:
        ctx.gh.push_branch(clone, branch, remote_repo=fork, force=False)
    except HarnessError as exc:
        ctx.record_decision(f"handoff could not push {branch} to {fork}: {exc}")
        return False, [], anchor
    ctx.record_decision(f"handoff pushed {branch} to {fork} (never upstream, never forced)")
    return True, [], anchor


def _unpublishable(ctx: Context, clone: Path, branch: str) -> tuple[list[str], str]:
    """B301/B314: the `.github/` paths the carried branch holds, a placeholder when it cannot
    tell, and the anchor the author-blind half read above ("" when it had none).

    A handoff is the one push that follows a model call no check has seen -- the usage stop
    lands inside the call, before implement's B64 -- so it asks two questions. The push guard's
    own commit walk, which stops at the first commit a harness email did not author. And,
    because the model holds Bash and a commit it makes carries whatever name it gives, the
    author-blind one: every commit on the branch that the fork's `main` does not hold. That
    `main` is fetched now, into a ref of the harness's own (B314): `refs/remotes/origin/main`
    is a file in the clone the model could have moved onto its own commit, and an anchor it
    moved would hide exactly what this looks for. When either question cannot be answered, the
    push is withheld: a false alarm costs one withheld push, a miss publishes a workflow change.
    """
    try:
        walk = clone_mod.walk_harness_commits(clone, branch, gates.run_command)
    except HarnessError as exc:
        ctx.record_decision(f"handoff could not walk the commits on {branch}: {exc}")
        return ["(the branch's commits could not be read)"], ""
    held = [path for _sha, path in walk.protected()]
    if walk.capped:
        held.append(f"(over {clone_mod.PROTECTED_SCAN_COMMITS} harness commits, unchecked)")
    code, out, err = gates.run_command(
        ["git", "-c", f"core.hooksPath={clone_mod.HOOKS_OFF}", "fetch", "--no-tags", "--quiet",
         "origin", f"+refs/heads/main:{GUARD_FORK_MAIN}"],
        clone,
    )
    if code != 0:
        ctx.record_decision(
            f"handoff could not fetch the fork's main ({code}): {(err or out).strip()[-300:]}"
        )
        return sorted(set(held)) + ["(the fork's main could not be fetched to check against)"], ""
    try:
        held.extend(
            clone_mod.protected_paths_above(clone, branch, [GUARD_FORK_MAIN], gates.run_command)
        )
    except HarnessError as exc:
        ctx.record_decision(f"handoff could not read {branch} above the fork's main: {exc}")
        held.append("(the branch's commits above the fork's main could not be read)")
    return sorted(set(held)), GUARD_FORK_MAIN


def _keep_withheld(ctx: Context, run_dir: Path, clone: Path, branch: str, anchor: str) -> str:
    """B315: write the commits a handoff withheld where a person can still read them.

    The file's name, or "" when there was nothing to anchor the range on or git could not
    produce it. Redacted like every other write, and capped: it is evidence, not a transport.
    """
    if not anchor or not branch:
        ctx.record_decision("handoff kept no patch of the withheld work: nothing to anchor it on")
        return ""
    code, out, err = gates.run_command(
        [*clone_mod.GUARD_GIT, "format-patch", "--stdout", f"{anchor}..{branch}"], clone
    )
    if code != 0 or not out.strip():
        ctx.record_decision(
            f"handoff could not write a patch of the withheld work ({code}): "
            f"{(err or out).strip()[-300:]}"
        )
        return ""
    if len(out) > MAX_WITHHELD_CHARS:
        out = out[:MAX_WITHHELD_CHARS] + f"\n\n[truncated at {MAX_WITHHELD_CHARS} characters]\n"
    write_redacted(run_dir / WITHHELD_NAME, out)
    ctx.record_decision(f"handoff kept the withheld commits as {run_dir / WITHHELD_NAME}")
    return WITHHELD_NAME


def _gate_summary(run_dir: Path) -> tuple[str, list[str]]:
    """The last gate sequence the run recorded: `gates/final.json`, else `gates/baseline.json`."""
    for label in ("final", "baseline"):
        raw = _read(run_dir / "gates" / f"{label}.json").strip()
        if not raw:
            continue
        try:
            rows = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(rows, list):
            continue
        lines: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = row.get("exit_code")
            verdict = "green" if code in (0, None) else f"RED (exit {code})"
            lines.append(f"- `{row.get('name')}` - {verdict}")
        if lines:
            return label, lines
    return "", []


def _decisions_tail(run_dir: Path) -> list[str]:
    """The last ``DECISION_TAIL_LINES`` of the run's DECISIONS.md, so the next run inherits
    the reasoning (B213). The same constant labels the section in ``build_handoff_body``."""
    lines = [line.rstrip() for line in _read(run_dir / "DECISIONS.md").splitlines()]
    return [line for line in lines if line.strip()][-DECISION_TAIL_LINES:]


def _acceptance(ctx: Context, item: Any) -> list[str]:
    """The work package's acceptance criteria, verbatim; empty when there is no spec.

    B226: resolved against ``config.repo_root``, not the process cwd. Its only caller is
    ``handoff``, which has the Context; on Actions the two happen to coincide, and in local
    and container mode they do not.
    """
    spec_text = _spec_text(ctx, item)
    if not spec_text.strip():
        return []
    try:
        return [line for line in parse_work_package(spec_text).acceptance if str(line).strip()]
    except HarnessError:
        return []
