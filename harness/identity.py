"""Bot-identity readiness detection and ``HUMAN.md`` generation (spec §13).

Delivery 1 detects and reports; it never authenticates. ``load_token`` is the
only function that would return the secret and at Tier 0 it always refuses
(B80) — the refusal lives in code, not in a policy document, because the whole
Tier 0 argument is that the harness cannot leak what it was never given.

``render_human_doc`` is pure: it reads the ``Readiness`` and nothing else, so
the generated document can never interpolate an environment value (§9, I-10).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from harness.config import Config
from harness.errors import ConfigError, GitHubError, TierViolation
from harness.gh import GitHubReadOnly
from harness.redact import write_redacted

HANDLE = "brightboost-harness"
ENV_KEY = "HARNESS_GITHUB_TOKEN"
CREATE_URL = "https://github.com/settings/personal-access-tokens/new"

FINE_GRAINED_SHAPE: re.Pattern[str] = re.compile(r"^github_pat_[A-Za-z0-9_]{40,}$")
CLASSIC_SHAPE: re.Pattern[str] = re.compile(r"^ghp_[A-Za-z0-9]{30,}$")

TIER_NAMES: dict[int, str] = {
    0: "Tier 0 — read only, no credential",
    1: "Tier 1 — comment on issues and file issues",
    2: "Tier 2 — push branches and open pull requests",
}

# §13.2, verbatim. (permission, value for the tier, note)
PERMISSION_SETS: dict[int, tuple[tuple[str, str, str], ...]] = {
    0: (
        ("(no token at all)", "not applicable", "Delivery 1 holds no credential"),
    ),
    1: (
        ("Metadata", "Read", "Mandatory for every fine-grained token"),
        ("Issues", "Read and write", "Comments and issue creation"),
        ("Pull requests", "Read and write", "Pull-request review comments"),
        ("Contents", "none", "No branch is pushed at this tier"),
        ("Workflows", "none", "Deliberately withheld — see below"),
        ("Administration", "none", "Never"),
        ("Secrets, Environments, Actions", "none", "Never"),
    ),
    2: (
        ("Metadata", "Read", "Mandatory for every fine-grained token"),
        ("Issues", "Read and write", "Comments and issue creation"),
        ("Pull requests", "Read and write", "Pull-request review comments"),
        ("Contents", "Read and write", "Pushing a branch"),
        ("Workflows", "none", "Deliberately withheld — see below"),
        ("Administration", "none", "Never"),
        ("Secrets, Environments, Actions", "none", "Never"),
    ),
}

NEVER_ASK_FOR: tuple[str, ...] = (
    "Production credentials of any kind — database URLs, deployment keys, cloud accounts.",
    "Organization administration, or ownership of the organization.",
    "The `Workflows` permission. Without it GitHub itself rejects any push that "
    "modifies `.github/workflows/`, so \"never edit CI to go green\" stops being a "
    "rule the harness is trusted to follow and becomes something it cannot do.",
    "Branch-protection changes, or any relaxation of a required check.",
    "Merge rights. Every change the harness produces is reviewed and merged by a human.",
    "A classic personal access token. Classic scopes are account-wide and cannot be "
    "narrowed to one repository.",
)


def permission_set_for(tier: int) -> tuple[tuple[str, str, str], ...]:
    """The §13.2 fine-grained permission rows for a target tier."""
    key = 0 if tier < 0 else (2 if tier > 2 else int(tier))
    return PERMISSION_SETS[key]


def permission_summary(tier: int) -> str:
    """One-line rendering of the §13.2 set, for the `## Tokens` table cell."""
    rows = permission_set_for(tier)
    return "; ".join(f"`{name}`: {value}" for name, value, _note in rows)


def tier_name(tier: int) -> str:
    return TIER_NAMES.get(int(tier), f"Tier {int(tier)}")


@dataclass(frozen=True)
class Prerequisite:
    id: str
    title: str
    tier_required: int
    satisfied: bool
    actor: Literal["you", "nathaniel"]
    detail: str
    verify: str | None


@dataclass(frozen=True)
class Readiness:
    current_tier: int
    target_tier: int
    prerequisites: tuple[Prerequisite, ...]
    ready: bool


class Identity:
    """The `brightboost-harness` machine account: detected, reported, never used."""

    handle: str = HANDLE

    def __init__(self, config: Config, gh: GitHubReadOnly) -> None:
        self.config = config
        self.gh = gh
        # Held separately so render_human_doc never touches a Config field (I-10).
        self.repo = str(getattr(config, "repo", ""))

    # ------------------------------------------------------------- detection

    def token_present(self) -> bool:
        """True when `HARNESS_GITHUB_TOKEN` is set and non-empty. Never reads the value."""
        return bool(getattr(self.config, "github_token_present", False))

    def shape_ok(self) -> bool:
        return bool(getattr(self.config, "github_token_shape_ok", False))

    def validate_shape(self, token: str) -> list[str]:
        """Shape errors, empty when plausible. Issues no request, ever."""
        if token is None:
            return ["no value supplied"]
        # The raw string, unstripped: surrounding whitespace is a malformed value, not noise.
        value = str(token)
        if value == "":
            return ["no value supplied"]
        if FINE_GRAINED_SHAPE.fullmatch(value):
            return []
        if CLASSIC_SHAPE.fullmatch(value):
            return []
        errors = [
            "does not match the fine-grained shape `github_pat_` followed by 40 or more "
            "of [A-Za-z0-9_]",
            "does not match the classic shape `ghp_` followed by 30 or more of [A-Za-z0-9]",
        ]
        return errors

    def account_exists(self) -> bool:
        """Unauthenticated `GET /users/<handle>`. Costs no credential."""
        try:
            data = self.gh.get(f"/users/{self.handle}")
        except GitHubError:
            return False
        if not isinstance(data, dict):
            return False
        return bool(data.get("login"))

    # ---------------------------------------------------------------- assess

    def assess(self, target_tier: int) -> Readiness:
        """The gap between what is configured now and what `target_tier` needs."""
        target = int(target_tier)
        current = int(getattr(self.config, "permission_tier", 0))
        repo = self.repo or "the product repository"

        account_ok = self.account_exists()
        token_ok = self.token_present() and self.shape_ok()

        candidates = (
            Prerequisite(
                id="account",
                title=f"GitHub account `{self.handle}` exists",
                tier_required=1,
                satisfied=account_ok,
                actor="you",
                detail=(
                    f"Create the `{self.handle}` GitHub account. Confirm the current "
                    "GitHub terms permit a machine account, that you own it, and that "
                    "you are answerable for what it does."
                ),
                verify=f"harness setup --tier {target}",
            ),
            Prerequisite(
                id="email-2fa",
                title="Account email verified and two-factor authentication enabled",
                tier_required=1,
                satisfied=False,
                actor="you",
                detail=(
                    f"Verify the email address on `{self.handle}` and enable two-factor "
                    "authentication. Required before the account can join an "
                    "organization. Nothing outside the account can check this, so "
                    "confirm it by hand and treat this line as a reminder, not a failure."
                ),
                verify=None,
            ),
            Prerequisite(
                id="token",
                title=f"Fine-grained PAT present in `.env` as `{ENV_KEY}`",
                tier_required=1,
                satisfied=token_ok,
                actor="you",
                detail=(
                    "Generate a fine-grained personal access token with exactly the "
                    f"permission set in `## Tokens` below, scoped to `{repo}` alone, "
                    "with the shortest expiry you will tolerate re-issuing. Put it in "
                    f"`.env` under the key `{ENV_KEY}`. Never in a chat, never in a "
                    "commit, never in a review package."
                ),
                verify="harness doctor",
            ),
            Prerequisite(
                id="org-approval",
                title="Fine-grained token approved for the organization",
                tier_required=1,
                satisfied=False,
                actor="nathaniel",
                detail=(
                    "Approve the fine-grained token for the organization that owns "
                    f"`{repo}`. Organizations can require an owner to approve a "
                    "fine-grained token before it reaches their repositories; until "
                    "that happens the token reads as valid but returns 404 on every "
                    "repository path."
                ),
                verify=None,
            ),
            Prerequisite(
                id="tier",
                title=f"`PERMISSION_TIER` raised to {target}",
                tier_required=1,
                satisfied=current >= target,
                actor="nathaniel",
                detail=(
                    f"Agree {tier_name(target)}, then set `PERMISSION_TIER={target}` in "
                    "`.env`. Nathaniel agrees the tier; you edit the file. Without this "
                    "the harness refuses the token in code no matter what `.env` holds."
                ),
                verify="harness doctor",
            ),
            Prerequisite(
                id="collaborator",
                title=f"`{self.handle}` invited as a collaborator",
                tier_required=2,
                satisfied=False,
                actor="nathaniel",
                detail=(
                    f"Invite `{self.handle}` as a collaborator on `{repo}` so it can "
                    "push a branch. Not needed to comment on a public repository. "
                    "Decide at the same time whether the account is added to the "
                    "organization or left an outside collaborator — it affects "
                    "visibility and seat count."
                ),
                verify=None,
            ),
        )

        included = tuple(p for p in candidates if p.tier_required <= target)
        return Readiness(
            current_tier=current,
            target_tier=target,
            prerequisites=included,
            ready=all(p.satisfied for p in included),
        )

    # ----------------------------------------------------------------- token

    def load_token(self) -> str:
        """The only function that returns the secret. Raises below Tier 1 (B80)."""
        tier = int(getattr(self.config, "permission_tier", 0))
        if tier < 1:
            raise TierViolation(
                f"permission_tier is {tier}: the harness may not hold or transmit "
                f"`{ENV_KEY}` below tier 1, even when a valid value is configured"
            )
        from harness import config as config_module

        value = config_module.read_secret(ENV_KEY)
        if not value:
            raise ConfigError(f"{ENV_KEY} is required at tier {tier} but is absent or empty")
        return value

    # ------------------------------------------------------------- rendering

    def render_human_doc(self, readiness: Readiness) -> str:
        """Render `HUMAN.md` from the readiness alone. Pure and deterministic (B86)."""
        target = int(readiness.target_tier)
        current = int(readiness.current_tier)
        repo = self.repo or "the product repository"
        lines: list[str] = []

        lines.append("# HUMAN.md — Bright Bots Harness setup")
        lines.append("")
        lines.append(
            "This is a gap report, regenerated by `harness setup`. It describes the "
            "distance between what is configured now and what the target tier needs, "
            "and it shrinks as prerequisites are satisfied. It contains no secret "
            "value — only key names, scopes, and who must act."
        )
        lines.append("")

        # ---- 1. Current state -------------------------------------------------
        lines.append("## Current state")
        lines.append("")
        lines.append(f"- Current tier: **{current}** — {tier_name(current)}")
        lines.append(f"- Target tier: **{target}** — {tier_name(target)}")
        lines.append(
            f"- Overall: **{'ready' if readiness.ready else 'not ready'}** — "
            f"{sum(1 for p in readiness.prerequisites if p.satisfied)} of "
            f"{len(readiness.prerequisites)} prerequisites satisfied"
        )
        lines.append("")
        lines.append("| Prerequisite | Actor | Needed from tier | Status |")
        lines.append("|---|---|---|---|")
        for pre in readiness.prerequisites:
            status = "done" if pre.satisfied else "outstanding"
            lines.append(
                f"| {pre.title} | {pre.actor} | {pre.tier_required} | {status} |"
            )
        lines.append("")
        done = [p for p in readiness.prerequisites if p.satisfied]
        if done:
            lines.append("Already satisfied, nothing further to do:")
            lines.append("")
            for pre in done:
                lines.append(f"- **{pre.title}** — done.")
        else:
            lines.append("No prerequisite is satisfied yet.")
        lines.append("")

        # ---- 2. What you need to do -------------------------------------------
        lines.append("## What you need to do")
        lines.append("")
        yours = [p for p in readiness.prerequisites if not p.satisfied and p.actor == "you"]
        if yours:
            for index, pre in enumerate(yours, start=1):
                lines.append(f"{index}. **{pre.title}** (actor: you)")
                lines.append(f"   {pre.detail}")
                if pre.verify:
                    lines.append(f"   Confirm with: `{pre.verify}`")
                lines.append("")
        else:
            lines.append("Nothing. Every action that is yours is already done.")
            lines.append("")

        # ---- 3. Tokens ---------------------------------------------------------
        lines.append("## Tokens")
        lines.append("")
        lines.append(
            "| `.env` key | Type | Permission set for tier "
            f"{target} | Where to create it | Who must approve it |"
        )
        lines.append("|---|---|---|---|---|")
        if target < 1:
            lines.append(
                f"| `{ENV_KEY}` | none — tier 0 holds no credential | "
                "*(no token at all)* | not applicable | not applicable |"
            )
        else:
            lines.append(
                f"| `{ENV_KEY}` | Fine-grained personal access token, scoped to "
                f"`{repo}` alone — never a classic token | {permission_summary(target)} | "
                f"{CREATE_URL} | An organization owner (Nathaniel) when the organization "
                "restricts fine-grained tokens |"
            )
        lines.append("")
        lines.append(f"The exact permission set for tier {target}, from the tier table:")
        lines.append("")
        lines.append("| Permission | Tier " + str(target) + " | Note |")
        lines.append("|---|---|---|")
        for name, value, note in permission_set_for(target):
            lines.append(f"| `{name}` | {value} | {note} |")
        lines.append("")
        lines.append(
            "Classic tokens are refused by design: classic scopes are account-wide and "
            "cannot be narrowed to one repository."
        )
        lines.append("")

        # ---- 4. What needs Nathaniel -------------------------------------------
        lines.append("## What needs Nathaniel")
        lines.append("")
        lines.append(
            "Everything below needs organization or repository administration. It is "
            "gathered here so it can be sent as one request."
        )
        lines.append("")
        theirs = [
            p for p in readiness.prerequisites if not p.satisfied and p.actor == "nathaniel"
        ]
        if theirs:
            for index, pre in enumerate(theirs, start=1):
                lines.append(f"{index}. **{pre.title}** (actor: Nathaniel)")
                lines.append(f"   {pre.detail}")
                if pre.verify:
                    lines.append(f"   Confirm with: `{pre.verify}`")
                lines.append("")
        else:
            lines.append("Nothing outstanding on Nathaniel's side.")
            lines.append("")

        # ---- 5. What the harness will never ask for -----------------------------
        lines.append("## What the harness will never ask for")
        lines.append("")
        for entry in NEVER_ASK_FOR:
            lines.append(f"- {entry}")
        lines.append("")

        # ---- 6. Verification ----------------------------------------------------
        lines.append("## Verification")
        lines.append("")
        lines.append(
            "Each command below confirms one step, so this document checks itself. "
            "Run them from the harness repository root."
        )
        lines.append("")
        lines.append("| Prerequisite | Command | Expected |")
        lines.append("|---|---|---|")
        for pre in readiness.prerequisites:
            command = f"`{pre.verify}`" if pre.verify else "*(no automated check)*"
            expected = (
                f"`{pre.id}` reported as done"
                if pre.verify
                else "confirm by hand in the GitHub interface"
            )
            lines.append(f"| {pre.title} | {command} | {expected} |")
        lines.append("")
        lines.append(
            f"- `harness setup --tier {target}` exits 0 when every prerequisite above is "
            "satisfied, and 6 while any remains outstanding."
        )
        lines.append(
            "- `harness doctor` re-probes the environment rather than trusting anything "
            "recorded here."
        )
        lines.append(
            "- Scanning this file with the harness redaction patterns must yield no "
            "match; it names keys and scopes only."
        )
        lines.append("")

        return "\n".join(lines)


def write_human_doc(path: Path, content: str) -> None:
    """Write `HUMAN.md` through the redactor, with `\\n` newlines (B83, I-8)."""
    text = str(content).replace("\r\n", "\n").replace("\r", "\n")
    if not text.endswith("\n"):
        text += "\n"
    write_redacted(Path(path), text)
