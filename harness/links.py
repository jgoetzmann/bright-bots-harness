"""The cross-links and the signature every issue and pull request the harness opens carries.

Everything the harness writes is read in GitHub's web UI, by a person who did not write it and
should not have to open a file to understand it. One module owns that presentation, so the work
item, the proposal pull request and the delivery pull request agree with each other about what
this is, who may steer it, and where to read more (B227).

Imports only `trust` from the rest of the package: `store/github.py` and `stages/deliver.py`
both reach this module, and neither may reach the other.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from harness.trust import MAX_LEVEL, Trust

__all__ = [
    "REF_MARKER",
    "VERB_HELP",
    "DOC_LINKS",
    "QUEUE_START",
    "QUEUE_END",
    "repo_url",
    "issue_url",
    "issue_ref",
    "closes",
    "refs",
    "quote_as_data",
    "signature",
    "reply_pointer",
    "nudge",
    "fast_status",
    "queue_lines",
    "queue_block",
    "replace_queue_block",
    "work_item_body",
    "proposal_pr_body",
]

#: What each `/harness <verb>` does, in the words a reviewer needs. The keys are exactly
#: `keywords.VERBS`; a test pins that, so a new verb cannot ship without a line here.
VERB_HELP: tuple[tuple[str, str], ...] = (
    ("work <what, or a link>", "open a work item for this and put it in the queue"),
    ("ask <question>", "answer a question about the code; changes nothing"),
    ("status", "show subscription usage, the queue, and when the next thing happens"),
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


def usage_headline(ledger: Any, config: Any, now: Any = None) -> list[str]:
    """How much of the subscription allowance is left: the five-hour and seven-day windows.

    The API reports both on the headers of every model call. The allowance is shared with
    whatever else the same subscription is used for, so the headline says so.

    ``now`` drops a reading whose window has reset since, so a refusal that has lifted is not
    reported as "at or past the stop — nothing will start" (B399).
    """
    lines = ["**Allowance**"]
    weekly = _utilization(ledger, "seven_day", now)
    session = _utilization(ledger, "five_hour", now)
    weekly_stop = float(getattr(config, "weekly_usage_stop_pct", 90.0))
    session_stop = float(getattr(config, "session_usage_stop_pct", 70.0))

    if weekly is None and session is None:
        lines.append(
            "- **not measured yet** — the reading arrives with a real model call, and none has "
            "been made in this window."
        )
    else:
        for label, used, stop in (
            ("this week", weekly, weekly_stop),
            ("this session", session, session_stop),
        ):
            if used is None:
                continue
            left = stop - used
            if left <= 0:
                room = f"**at or past** the {stop:.0f}% stop — nothing will start"
            elif left < 1:
                # `{:.0f}` of 0.4 is "0", and "0 points before the stop" reads as stopped while
                # work continues. Below a point, say so rather than round to a claim.
                room = f"**under a point** before the {stop:.0f}% stop"
            else:
                room = f"**{left:.0f} points** before the {stop:.0f}% stop"
            lines.append(f"- {label}: **{used:.0f}% used**, {room}")
        lines.append(
            "- shared with whatever else this subscription is used for, so this moves when the "
            "harness is doing nothing"
        )
    return lines


#: The ledger accessor for each window. A window turnover moves `period_start` and leaves the
#: last observation in place, and the accessors' staleness check is what stops last week's
#: figure being reported as this week's, so these are used rather than `window["usage"]`.
_LEDGER_ACCESSOR = {"seven_day": "weekly_utilization", "five_hour": "session_utilization"}


def _utilization(ledger: Any, key: str, now: Any = None) -> float | None:
    """`key`'s utilization as a percentage, or None when it is unknown for this window.

    Unknown means never observed, not reported, or observed before the window rolled. A fresh
    week still carrying Friday's 88% would otherwise read as "2 points before the stop".
    """
    accessor = getattr(ledger, _LEDGER_ACCESSOR[key], None)
    if callable(accessor):
        fraction = accessor() if now is None else accessor(now)
        return None if fraction is None else float(fraction) * 100.0
    # A ledger-shaped object without the accessors gets the same guard applied by hand, so a
    # test double cannot be more permissive than the real thing.
    return _raw_utilization(getattr(ledger, "window", {}) or {}, key, now)


def _raw_utilization(window: Any, key: str, now: Any = None) -> float | None:
    """`_utilization`'s fallback: the same rule, applied to a plain window mapping."""
    if not isinstance(window, dict):
        return None
    usage = window.get("usage")
    if not isinstance(usage, dict):
        return None
    observed_at, start = usage.get("observed_at"), window.get("period_start")
    if observed_at and start and str(observed_at) < str(start):
        return None  # ISO-8601 UTC sorts lexicographically, which is all this needs
    reported = usage.get(key)
    if not isinstance(reported, dict):
        return None
    resets_at = reported.get("resets_at")
    if now is not None and resets_at and hasattr(now, "strftime"):
        if now.strftime("%Y-%m-%dT%H:%M:%SZ") >= str(resets_at):
            return None  # B399: the window this reading describes has reset
    raw = reported.get("utilization")
    if raw is None:
        return None
    try:
        return float(raw) * 100.0
    except (TypeError, ValueError):
        return None


#: Roughly how long each verb takes, and what it is doing while you wait. An order of magnitude
#: only: enough to tell a verb that is thinking from one that is stuck, which look identical
#: from a thread.
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

#: Waits long enough to be worth their own acknowledgement. A faster answer arrives about as
#: soon as the acknowledgement would.
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

    A comment of only fast verbs gets the eyes reaction instead, since an acknowledgement
    landing two seconds before the answer costs a notification and tells the reader nothing.
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
        "The answer arrives as a new comment on this thread. If it does not, `/harness status` "
        "says whether the harness is halted, whether the run window is open, and when the next "
        "sweep is.",
    ]
    return "\n".join(lines)


#: What to offer on each surface, most useful first. Three that make sense where the reader is
#: standing get tried, where a reply listing all twelve is a wall nobody reads.
SURFACE_HINTS: dict[str, tuple[str, ...]] = {
    "inbox": ("work <what>", "ask <question>", "status"),
    # An audit issue is surface `issue` too and carries no stage label, so `go` and `split`
    # answer "no work item" there while `promote` works. All three are offered, because the
    # other two are right for a work item, which is the same surface.
    "issue": ("go", "split", "promote <n>", "status"),
    "product_issue": ("work", "ask <question>", "status"),
    "proposal_pr": ("revise <notes>", "stop", "status"),
    "delivery_pr": ("revise <notes>", "rebase", "stop"),
}

#: Where a reader goes next. Relative to the harness repository's default branch.
DOC_LINKS: tuple[tuple[str, str], ...] = (
    ("Start here", "docs/FOR-MAINTAINERS.md"),
    ("Every command", "docs/COMMANDS.md"),
    ("What it will and will not do", "docs/SAFETY.md"),
    ("When something is wrong", "docs/OPERATIONS.md"),
    ("What is in a review package", "docs/PACKAGE-FORMAT.md"),
)

#: The line that carries the machine-readable external reference in a work item's body. The
#: body is prose, so `store.github._origin_ref` finds the reference by this marker rather than
#: by its position (B227).
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

    Used only where merging resolves the thing named: a delivery pull request against the
    product repository closes the product issue. A proposal pull request leaves its work item
    to be implemented, so that one gets `refs`.
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

    The fence is longer than any run of backticks inside, so the content cannot break out of
    it. `stages.data_block` does the same for a prompt.
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

    Names the author, says that it merges nothing, lists the commands that steer it and who may
    give them, and links the documentation. `steerable=False` drops the command table for a
    surface where a comment would not be read, such as an ops alert.
    """
    self_repo = str(getattr(config, "self_repo", "") or "")
    upstream = str(getattr(config, "upstream_repo", "") or getattr(config, "repo", "") or "")
    fork = str(getattr(config, "fork_repo", "") or "")

    lines: list[str] = ["---", ""]
    on_what = f"[`{upstream}`]({repo_url(upstream)})" if upstream else "the product repository"
    lines.append(
        "Posted by the **Bright Bots Harness**, an automated agent that turns issues on "
        f"{on_what} into reviewable pull requests."
    )
    lines.append("")
    lines.append(
        "It never merges anything: a person approves the plan by merging the proposal, and the "
        "change by merging the delivery. A committed kill switch stops it."
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
            f"Commands are honoured only from {_handles(trusted)}{where_trust}. "
            "Anyone else's comment is read and ignored."
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

    Names rather than a number, so a reader of a public issue can see whether their own comment
    would be honoured without first learning what a level is.
    """
    from harness.keywords import VERB_LEVEL  # function-local: this module imports only `trust`

    needed = VERB_LEVEL.get(entry.split()[0], MAX_LEVEL)
    if isinstance(trusted, Trust):
        handles = trusted.at_least(needed)
        if handles:
            return " · ".join(f"@{h}" for h in handles)
        return f"nobody listed at level {needed}"
    return f"level {needed}+"


def reply_pointer(config: Any, surface: str = "") -> str:
    """What else the reader of a reply can say *here*, and where the whole list is.

    Separate from `signature`, because a command reply wants this without the full table and an
    ops alert (`steerable=False`) wants neither. Offered by surface: "what can I say" has a
    different answer on a delivery pull request than on the inbox.
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


def nudge(config: Any, surface: str = "", mention: str = "") -> str:
    """What to say when somebody names the bot and gives it no verb (B436).

    A mention is how a person asks for attention, so the answer is the two or three commands
    that make sense where they are standing rather than the whole table of twelve.
    """
    hints = SURFACE_HINTS.get(surface) or ("status", "ask <question>", "work <what>")
    offered = " · ".join(f"`/harness {hint}`" for hint in hints)
    handle = str(mention or "").strip().lstrip("@")
    forms = f"`/harness <verb>`, or `@{handle} <verb>`" if handle else "`/harness <verb>`"
    lines = [
        f"**Read — but there was no command in that.** A command is {forms}, at the start of "
        "a line.",
        "",
        f"Here, these are the ones that make sense: {offered}",
    ]
    self_repo = str(getattr(config, "self_repo", "") or "")
    if self_repo:
        lines += ["", f"[Every command]({repo_url(self_repo)}/blob/main/docs/COMMANDS.md)."]
    return "\n".join(lines)


def fast_status(
    config: Any,
    ledger: Any,
    *,
    now: Any = None,
    window: str = "",
    next_sweep: str = "",
    queue_issue: int | str = 0,
) -> str:
    """The answer to a `status`-only comment, given in seconds instead of minutes (B440).

    Everything here comes from the ledger and the configuration, both of which `ack` can read
    without a lock and without a credential. The queue itself needs an authenticated store
    read that tier-0 `ack` cannot make, so the live queue is linked rather than inlined — which
    is what the pinned issue exists to make possible (D76).
    """
    lines: list[str] = []
    halt = ledger.halt_request() if hasattr(ledger, "halt_request") else None
    if halt:
        why = f" — {halt.get('reason')}" if halt.get("reason") else ""
        lines.append(
            f"> **Halted** by @{halt.get('by', 'someone')} at {halt.get('at', 'unknown')}{why}. "
            "Nothing will spend until `/harness resume`."
        )
        lines.append("")
    lines.extend(usage_headline(ledger, config, now))
    lines.append("")
    lines.append("**Next**")
    if window:
        lines.append(f"- run window {window}")
    if next_sweep:
        lines.append(f"- next scheduled sweep **{next_sweep}**")
    self_repo = str(getattr(config, "self_repo", "") or "")
    try:
        number = int(queue_issue or 0)
    except (TypeError, ValueError):
        number = 0
    if self_repo and number > 0:
        lines.append(
            f"- the live queue is on [{issue_ref(self_repo, number)}]"
            f"({issue_url(self_repo, number)}), rewritten on every sweep"
        )
    return "\n".join(lines)


#: The markers bounding the harness's block on the pinned tracking issue. Everything outside
#: them is written by a person and is never touched (D76).
QUEUE_START = "<!-- queue:start -->"
QUEUE_END = "<!-- queue:end -->"


def queue_lines(rows: Iterable[Any], *, limit: int = 10) -> list[str]:
    """The queue in priority order, as both `/harness status` and the pinned issue show it.

    One renderer for both, so the answer in a thread and the answer on the pinned issue cannot
    disagree about what is waiting. `rows` are already-computed `priority.Waiting` values, so
    this module still imports nothing but `trust`.
    """
    items = list(rows)
    lines = [f"**Queue** — {len(items)} waiting"]
    if not items:
        lines.append("- empty")
    for row in items[:limit]:
        mark = " · **forced**" if getattr(row, "forced", False) else ""
        note = f" · {row.note}" if getattr(row, "note", "") else ""
        lines.append(f"- `{row.cls}` {row.label}{note}{mark}")
    if len(items) > limit:
        lines.append(f"- …and {len(items) - limit} more")
    return lines


def queue_block(lines: Iterable[str]) -> str:
    """`lines` wrapped in the two markers that bound the queue on the pinned issue."""
    body = "\n".join(str(line) for line in lines).strip()
    return f"{QUEUE_START}\n{body}\n{QUEUE_END}"


def replace_queue_block(body: str, block: str) -> str | None:
    """`body` with the span between the markers replaced, or None when either is missing.

    None rather than appending: the rest of that issue is a person's prose, and a harness that
    guesses where its own block belongs rewrites what somebody wrote (D76).
    """
    text = str(body or "")
    start = text.find(QUEUE_START)
    end = text.find(QUEUE_END, start + len(QUEUE_START)) if start >= 0 else -1
    if start < 0 or end < 0:
        return None
    return text[:start] + block + text[end + len(QUEUE_END):]


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
    """The body of the issue that *is* a work item: what the work is, and where it came from."""
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
        "This issue is the work item; its `stage:` label is its state, which the harness moves. "
        "The next step is a **proposal** — a pull request here adding "
        "`proposals/<id>-<slug>.md`, which is a decided plan for the change. Merging that pull "
        "request approves the plan and implementation starts; closing it rejects the plan and "
        "nothing further is attempted."
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

    The proposal is inlined, so the person judging the plan need not open a file in a diff to
    read it.
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
