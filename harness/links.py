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
    "work_item_body",
    "proposal_pr_body",
]

#: What each `/harness <verb>` does, in the words a reviewer needs. The keys are exactly
#: `keywords.VERBS`; a test pins that, so a new verb cannot ship without a line here.
VERB_HELP: tuple[tuple[str, str], ...] = (
    ("revise <notes>", "redo the work with your notes as the brief"),
    ("reject <reason>", "abandon this item; nothing further is attempted"),
    ("fix <notes>", "one more implementation pass against the same package"),
    ("rebase", "rebase the branch onto the product repository's current main"),
    ("stop", "park this item now, leaving the branch and the evidence in place"),
    ("split", "break this item into sub-issues and queue them separately"),
    ("queue", "put this item back in the queue after it was parked or blocked"),
)

#: Where a reader goes next. Relative to the harness repository's default branch.
DOC_LINKS: tuple[tuple[str, str], ...] = (
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
        lines.append("| command | what happens |")
        lines.append("|---|---|")
        for verb, meaning in VERB_HELP:
            lines.append(f"| `/harness {verb}` | {meaning} |")
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
