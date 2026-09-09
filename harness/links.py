"""The cross-links and the signature every issue and pull request the harness opens carries.

B227/D47. Everything the harness writes is read in GitHub's web UI, by a person who did not
write it and should not have to open a file to understand it. One module owns that presentation
so the work item, the proposal pull request and the delivery pull request agree with each other
about what this is, who may steer it, and where to read more.

Imports nothing from the rest of the package: `store/github.py` and `stages/deliver.py` both
reach it, and neither may reach the other.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from harness.trust import MAX_LEVEL, Trust

__all__ = [
    "REF_MARKER",
    "VERB_HELP",
    "DOC_LINKS",
    "repo_url",
    "issue_url",
    "issue_ref",
    "closes",
    "refs",
    "quote_as_data",
    "signature",
    "reply_pointer",
    "work_item_body",
    "proposal_pr_body",
]

#: What each `/harness <verb>` does, in the words a reviewer needs. The keys are exactly
#: `keywords.VERBS`; a test pins that, so a new verb cannot ship without a line here.
VERB_HELP: tuple[tuple[str, str], ...] = (
    ("work <what, or a link>", "open a work item for this and put it in the queue"),
    ("ask <question>", "answer a question about the code; changes nothing"),
    ("status", "show the spend, the queue, and when the next thing happens"),
    ("audit <what to look for>", "read the product repository and open one issue of findings"),
    ("promote <n>", "turn finding n of an audit into a work item of its own"),
    ("revise <notes>", "redo it with your notes: the plan on a proposal, the code on a delivery"),
    ("rebase", "redo it after a conflict with the product repository's main"),
    ("stop", "stop this item and close its pull request"),
    ("go", "proceed with this: green-light a suggestion, or requeue a stopped item"),
    ("split", "break this item into sub-issues and queue them separately"),
    ("halt", "stop the harness spending anything until it is resumed"),
    ("resume", "lift a halt"),
)

def usage_headline(ledger: Any, config: Any) -> list[str]:
    """How much of the subscription is left, in the units the subscription is actually sold in.

    The harness spent a delivery reporting dollars, and dollars are the wrong number. Nothing
    bills them: the estimate is derived from token counts, and what actually runs out is the
    utilization of two windows the API reports on the headers of every call — five-hour and
    seven-day. The first live run made the gap plain. One model call, **$0.28** estimated, and
    the seven-day window at **18%**: by the dollar figure the harness had used 0.08% of its
    allowance, and by the real one, nearly a fifth of the week.

    Most of that 18% was not the harness. **The allowance is shared with whatever else the
    operator does with the same subscription**, which is the single most important fact about
    reading these numbers and the one a dollar total hides completely.

    Dollars are kept, below and in smaller print, for the three things they are still good for:
    a sense of scale, the `--max-budget-usd` flag the runner really does enforce per call, and
    being the only signal at all before a real call has ever been made.
    """
    lines = ["**Allowance**"]
    weekly = _utilization(ledger, "seven_day")
    session = _utilization(ledger, "five_hour")
    weekly_stop = float(getattr(config, "weekly_usage_stop_pct", 90.0))
    session_stop = float(getattr(config, "session_usage_stop_pct", 70.0))

    if weekly is None and session is None:
        lines.append(
            "- **not measured yet** — the signal rides on the headers of a real model call, so "
            "a run that has not made one has nothing to report. Until then the dollar estimate "
            "below is all there is, and it is an estimate."
        )
    else:
        for label, used, stop in (
            ("this week", weekly, weekly_stop),
            ("this session", session, session_stop),
        ):
            if used is None:
                continue
            left = stop - used
            lines.append(
                f"- {label}: **{used:.0f}% used**, "
                + (
                    f"**{left:.0f} points** before the {stop:.0f}% stop"
                    if left > 0
                    else f"**at or past** the {stop:.0f}% stop — nothing will start"
                )
            )
        lines.append(
            "- shared with whatever else this subscription is used for, so this moves when the "
            "harness is doing nothing"
        )
    return lines


def spend_estimate(ledger: Any, config: Any) -> str:
    """The dollar line, said as the estimate it is."""
    window = getattr(ledger, "window", {}) or {}
    spent = float(window.get("spent_usd", 0.0) or 0.0)
    calls = int(window.get("calls", 0) or 0)
    cap = float(getattr(config, "weekly_cap_usd", 0.0) or 0.0)
    reserve = float(getattr(config, "reserve_pct", 0.0) or 0.0)
    ceiling = cap * (1.0 - reserve / 100.0)
    return (
        f"<sub>Rough scale: about **${spent:.2f}** of API-equivalent cost over {calls} "
        f"call(s) this window, against a ${ceiling:,.0f} backstop (${cap:,.0f} less "
        f"{reserve:.0f}% reserve). "
        "Estimated from token counts — nobody bills it, and it is not what runs out.</sub>"
    )


def _utilization(ledger: Any, key: str) -> float | None:
    """`key`'s utilization as a percentage, or None when it has never been observed."""
    window = getattr(ledger, "window", {}) or {}
    usage = window.get("usage")
    if not isinstance(usage, dict):
        return None
    reported = usage.get(key)
    if not isinstance(reported, dict):
        return None
    raw = reported.get("utilization")
    if raw is None:
        return None
    try:
        return float(raw) * 100.0
    except (TypeError, ValueError):
        return None


#: Roughly how long each verb takes, and what it is doing while you wait.
#:
#: Only ever an ORDER OF MAGNITUDE. The point is not accuracy, it is the difference between
#: "this is thinking" and "this is broken" -- the two look identical from a thread, and the
#: second is the one people act on. A verb that reads the store answers in seconds; one that
#: clones the product repository and calls a model does not, and saying so is the whole job.
VERB_WAIT: dict[str, tuple[str, str]] = {
    # verb: (how long, what it is doing)
    "status": ("seconds", "reading the ledger and the queue"),
    "go": ("seconds", "moving the item"),
    "stop": ("seconds", "closing the pull request and standing the item down"),
    "promote": ("seconds", "opening a work item per finding"),
    "work": ("under a minute", "opening the work item"),
    "resume": ("seconds", "lifting the halt"),
    "halt": ("seconds", "recording the halt"),
    "rebase": ("a few minutes", "rebasing onto the product repository and re-running the gates"),
    "split": ("a minute or two", "reading the item and deciding how it divides"),
    "ask": ("a couple of minutes", "cloning the product repository and reading it"),
    "revise": ("several minutes", "another implementation pass, then the complete gate sequence"),
    "audit": ("up to twenty minutes", "reading the product repository through your lens"),
}

#: Above this, an acknowledgement is worth its own comment; at or below it, the answer arrives
#: about as fast as the acknowledgement would, and posting both is just noise.
SLOW_WAITS: frozenset[str] = frozenset({
    "a minute or two", "a few minutes", "a couple of minutes", "several minutes",
    "up to twenty minutes",
})


def is_slow(verb: str) -> bool:
    """True when `verb` is worth acknowledging before it answers."""
    wait, _doing = VERB_WAIT.get(verb, ("", ""))
    return wait in SLOW_WAITS


def acknowledgement(verbs: "Iterable[str]") -> str:
    """"Working on it", naming each verb and how long it should take. "" when none is slow.

    Returned empty for a comment of fast verbs on purpose: an acknowledgement that lands two
    seconds before the answer it acknowledges has told the reader nothing and cost them a
    notification. The eyes reaction is the acknowledgement in that case.
    """
    seen: list[str] = []
    for verb in verbs:
        if verb not in VERB_WAIT or verb in seen:
            continue
        seen.append(verb)
    if not any(is_slow(v) for v in seen):
        return ""
    lines = ["**Working on it.**", ""]
    for verb in seen:
        wait, doing = VERB_WAIT[verb]
        lines.append(f"- `/harness {verb}` — {doing}. About **{wait}**.")
    lines += [
        "",
        "The answer replaces nothing; it arrives as a new comment on this thread. If it does "
        "not, `/harness status` says whether the harness is halted, whether the run window is "
        "open, and when the next sweep is.",
    ]
    return "\n".join(lines)


#: What to offer on each surface, most useful first. A reply listing all twelve is a wall nobody
#: reads; three that make sense where the reader is standing get tried.
SURFACE_HINTS: dict[str, tuple[str, ...]] = {
    "inbox": ("work <what>", "ask <question>", "status"),
    # An AUDIT issue is surface `issue` too, and carries no stage label by design -- so `go`
    # and `split` both answer "no work item" there. `promote` is the one that works, and the
    # two that do not are still right for a work item, so all three are offered and the reader
    # picks. Offering only the pair that fails was the worse of the two errors.
    "issue": ("go", "split", "promote <n>", "status"),
    "product_issue": ("work", "ask <question>", "status"),
    "proposal_pr": ("revise <notes>", "stop", "status"),
    "delivery_pr": ("revise <notes>", "rebase", "stop"),
}

#: Where a reader goes next. Relative to the harness repository's default branch.
DOC_LINKS: tuple[tuple[str, str], ...] = (
    ("Start here", "docs/FOR-MAINTAINERS.md"),
    ("Every command", "docs/COMMANDS.md"),
    ("How to use it", "docs/USING.md"),
    ("Finding work and steering it", "docs/PROPOSALS.md"),
    ("What it will and will not do", "docs/SAFETY.md"),
    ("When something is wrong", "docs/OPERATIONS.md"),
    ("What is in a review package", "docs/PACKAGE-FORMAT.md"),
)

#: The line that carries the machine-readable external reference in a work item's body. The
#: body is prose now (B227), so the reference needs a home a parser can find without depending
#: on it being the first line -- which is what `store.github._origin_ref` used to assume.
REF_MARKER = "Machine reference, do not edit:"

_GITHUB = "https://github.com"


def repo_url(repo: str) -> str:
    """``https://github.com/owner/name`` for an ``owner/name`` string."""
    return f"{_GITHUB}/{str(repo).strip().strip('/')}"


def issue_url(repo: str, number: int | str) -> str:
    """The canonical issue URL. GitHub redirects ``/issues/N`` to a pull request of the same
    number, so one form serves both."""
    return f"{repo_url(repo)}/issues/{number}"


def issue_ref(repo: str, number: int | str) -> str:
    """A cross-repository reference GitHub renders as a link and resolves in either direction."""
    return f"{str(repo).strip().strip('/')}#{number}"


def closes(repo: str, number: int | str | None, *, same_repo: bool = False) -> str:
    """A GitHub closing keyword, or ``""`` when there is nothing to close.

    Only ever used where merging really does resolve the thing named: a delivery pull request
    against the product repository closes the product issue. A proposal pull request does not
    close its work item -- the item is still to be implemented -- so that one gets `refs`.
    """
    if number in (None, ""):
        return ""
    target = f"#{number}" if same_repo else issue_ref(repo, number)
    return f"Closes {target}"


def refs(repo: str, number: int | str | None, *, same_repo: bool = False) -> str:
    """A non-closing cross-reference, which still shows up in the target's timeline."""
    if number in (None, ""):
        return ""
    target = f"#{number}" if same_repo else issue_ref(repo, number)
    return f"Refs {target}"


def quote_as_data(text: str, *, label: str) -> str:
    """Someone else's prose, fenced and labelled so no reader mistakes it for the harness's own.

    The same reasoning as `stages.data_block`, for a GitHub comment rather than a prompt: the
    fence is longer than any run of backticks inside, so the content cannot break out of it.
    """
    body = (text or "").rstrip() + "\n"
    longest = 0
    for line in body.splitlines():
        stripped = line.strip()
        if stripped and set(stripped) == {"`"}:
            longest = max(longest, len(stripped))
    fence = "`" * max(3, longest + 1)
    return f"**{label}**\n\n{fence}text\n{body}{fence}"


def _handles(trusted: Iterable[str] | None) -> str:
    names = sorted({str(h).strip().lstrip("@") for h in (trusted or ()) if str(h).strip()})
    if not names:
        return "nobody yet -- `.harness/trust.txt` is empty, so every command is ignored"
    return ", ".join(f"@{name}" for name in names)


def signature(
    config: Any,
    *,
    trusted: Iterable[str] | None = None,
    steerable: bool = True,
) -> str:
    """The footer every issue and pull request the harness opens ends with (B227).

    Names the author, says plainly that it merges nothing, lists the commands that steer it and
    who may give them, and links the documentation. `steerable=False` drops the command table
    for a surface where a comment would not be read -- an ops alert, say.
    """
    self_repo = str(getattr(config, "self_repo", "") or "")
    upstream = str(getattr(config, "upstream_repo", "") or getattr(config, "repo", "") or "")
    fork = str(getattr(config, "fork_repo", "") or "")

    lines: list[str] = ["---", ""]
    on_what = f"[`{upstream}`]({repo_url(upstream)})" if upstream else "the product repository"
    lines.append(
        "Written by the **Bright Bots Harness** — an automated agent that takes an issue on "
        f"{on_what} through to a reviewable pull request."
    )
    lines.append("")
    lines.append(
        "It **never merges anything**. Two human gates stand in the way: a proposal is "
        "approved by a person merging it, and a delivery is approved by a person merging that. "
        "Everything it writes is redacted, and a committed kill switch stops it mid-flight."
    )
    lines.append("")

    if steerable:
        lines.append("**Steering it.** Put one of these on its own line in a comment here:")
        lines.append("")
        lines.append("| command | what happens | who may |")
        lines.append("|---|---|---|")
        for verb, meaning in VERB_HELP:
            lines.append(f"| `/harness {verb}` | {meaning} | {_who(verb, trusted)} |")
        lines.append("")
        lines.append(
            "Add `--force` to run now instead of waiting for the next window; only the "
            "operator may. Nothing bypasses the kill switch, the usage stops or the two gates."
        )
        lines.append("")
        where_trust = (
            f" ([`.harness/trust.txt`]({repo_url(self_repo)}/blob/main/.harness/trust.txt))"
            if self_repo
            else " (`.harness/trust.txt`)"
        )
        lines.append(
            f"Honoured only from {_handles(trusted)}{where_trust}. "
            "Anyone else's comment is read and ignored, and saying so is the point: this is a "
            "public repository."
        )
        lines.append("")

    if self_repo:
        where = [
            f"[{title}]({repo_url(self_repo)}/blob/main/{path})" for title, path in DOC_LINKS
        ]
        lines.append("**Where things are.** " + " · ".join(where))
        lines.append("")
    # A missing repository name is left out rather than rendered as a link to github.com
    # with nothing after it. The footer degrades; it never points somewhere wrong.
    parts = [
        f"{label} [`{repo}`]({repo_url(repo)})"
        for label, repo in (("harness", self_repo), ("fork", fork), ("product", upstream))
        if repo
    ]
    if parts:
        lines.append("**Repositories.** " + " · ".join(parts))
    return "\n".join(lines)


def _who(entry: str, trusted: Iterable[str] | None) -> str:
    """The handles that may give this command, or the level it needs when none are known.

    Naming people rather than a number is the point: a reader of a public issue can see at a
    glance whether their own comment would be honoured, without first learning what a level is.
    """
    from harness.keywords import VERB_LEVEL  # imported here: `keywords` imports nothing of ours

    needed = VERB_LEVEL.get(entry.split()[0], MAX_LEVEL)
    if isinstance(trusted, Trust):
        handles = trusted.at_least(needed)
        if handles:
            return " · ".join(f"@{h}" for h in handles)
        return f"nobody listed at level {needed}"
    return f"level {needed}+"


def reply_pointer(config: Any, surface: str = "") -> str:
    """What else the reader of a reply can say *here*, and where the whole list is.

    Not part of `signature`: a command reply wants this without the full table, and an ops alert
    -- `steerable=False` -- wants neither, because nobody answers an alert.

    Offered by surface, because "what can I say" has a different answer on a delivery pull
    request than on the inbox, and the useful version of that answer is the short one.
    """
    self_repo = str(getattr(config, "self_repo", "") or "")
    hints = SURFACE_HINTS.get(surface) or ("status", "ask <question>", "work <what>")
    offered = " · ".join(f"`/harness {h}`" for h in hints)
    line = f"**You can also say:** {offered}"
    if not self_repo:
        return line + " — several may go in one comment, one per line; you get one reply."
    return (
        line + " — several may go in one comment, one per line; you get one reply.\n"
        f"[Every command]({repo_url(self_repo)}/blob/main/docs/COMMANDS.md) · "
        "`/harness-status` works too, if you prefer the hyphen."
    )


def work_item_body(
    config: Any,
    *,
    external_ref: str,
    upstream_number: int | None,
    upstream_title: str = "",
    upstream_body: str = "",
    parent: int | None = None,
    extra: str = "",
    trusted: Iterable[str] | None = None,
) -> str:
    """The body of the issue that *is* a work item (B227).

    It used to be the bare external reference — the string `issue:633` and nothing else — which
    told a reader on the web neither what the work was nor where it came from.
    """
    upstream = str(getattr(config, "upstream_repo", "") or getattr(config, "repo", "") or "")
    lines: list[str] = []
    if upstream_number is not None:
        link = f"[{issue_ref(upstream, upstream_number)}]({issue_url(upstream, upstream_number)})"
        heading = f"Tracking {link}"
        if upstream_title.strip():
            heading += f" — {upstream_title.strip()}"
        lines.append(heading)
    else:
        lines.append(f"Tracking `{external_ref}`.")
    lines.append("")
    if parent is not None:
        lines.append(f"Parent: #{parent}")
        lines.append("")
    if upstream_body.strip():
        lines.append(quote_as_data(upstream_body, label="The issue, verbatim"))
        lines.append("")
    if extra.strip():
        lines.append(extra.strip())
        lines.append("")
    lines.append("## What happens next")
    lines.append("")
    lines.append(
        "This issue is the work item. Its `harness:*` label is the state; the harness moves it. "
        "The next step is a **proposal** — a pull request here adding "
        "`proposals/<id>-<slug>.md`, which is a decided plan for the change. Merging that pull "
        "request approves it and implementation starts; closing it rejects the plan and nothing "
        "further is attempted."
    )
    lines.append("")
    lines.append(f"{REF_MARKER} `{external_ref}`")
    lines.append("")
    lines.append(signature(config, trusted=trusted))
    return "\n".join(lines)


def proposal_pr_body(
    config: Any,
    *,
    item_id: int,
    path: str,
    proposal_text: str,
    upstream_number: int | None = None,
    trusted: Iterable[str] | None = None,
) -> str:
    """The body of the proposal pull request — gate 1 (B227).

    The proposal itself is inlined, not merely linked. Gate 1 is a judgement about a plan, and
    the person making it should not have to open a file in a diff to read the plan.
    """
    self_repo = str(getattr(config, "self_repo", "") or "")
    upstream = str(getattr(config, "upstream_repo", "") or getattr(config, "repo", "") or "")

    lines: list[str] = [
        "<!-- opened by the Bright Bots Harness: gate 1, the proposal (B227) -->",
        "",
        "## Merging this approves the plan",
        "",
        (
            "**Merge** and the harness implements it: a branch on the fork, the product "
            "repository's full gate sequence, a review package, and a pull request there for "
            "you to review. **Close** and it is rejected; nothing further is attempted. "
            "Neither happens until you act."
        ),
        "",
        f"Adds `{path}`. Nothing else in this pull request.",
        "",
    ]
    refs_line = [refs(self_repo, item_id, same_repo=True)]
    if upstream_number is not None:
        link = f"[{issue_ref(upstream, upstream_number)}]({issue_url(upstream, upstream_number)})"
        refs_line.append(f"product issue {link}")
    lines.append("Work item " + ", ".join(part for part in refs_line if part) + ".")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## The proposal")
    lines.append("")
    lines.append(proposal_text.strip())
    lines.append("")
    lines.append(signature(config, trusted=trusted))
    return "\n".join(lines)
