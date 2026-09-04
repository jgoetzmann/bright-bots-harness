"""Machine-account readiness detection and ``HUMAN.md`` generation (spec §13, handoff §16).

Delivery 2 activates the machine account: at tier 2 it holds one classic PAT scoped
``public_repo`` and nothing else. Its handle is *derived* — the owner of ``FORK_REPO``,
``jgoetzmann-bot`` in this deployment (D29); the spec's ``brightboost-harness`` survives as
the :data:`HANDLE` default and applies only until ``FORK_REPO`` is configured. This module
still never authenticates. The credential's key name comes from ``config`` so this file never
spells it (R3.3).
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from harness.config import CLASSIC_TOKEN_SHAPE, FINE_GRAINED_TOKEN_SHAPE, TOKEN_KEY_NAME, Config
from harness.errors import ConfigError, GitHubError, RateCeilingReached, TierViolation
from harness.gh import GitHubReadOnly
from harness.redact import write_redacted

HANDLE = "brightboost-harness"
KEY_NAME = TOKEN_KEY_NAME
CREATE_URL = "https://github.com/settings/personal-access-tokens/new"
CLASSIC_CREATE_URL = "https://github.com/settings/tokens/new"
TRUST_PLACEHOLDER = "<NATHAN_HANDLE>"

# The Config flags that say whether the credential is configured, named from the key so this
# module never contains the flag names themselves (R3.3).
_PRESENT_FIELD = KEY_NAME.lower().removeprefix("harness_") + "_present"
_SHAPE_FIELD = KEY_NAME.lower().removeprefix("harness_") + "_shape_ok"

# The patterns themselves live in config.py, as the key name does (R3.3, I-11); the raw-string
# match in validate_shape is this module's own policy.
FINE_GRAINED_SHAPE: re.Pattern[str] = FINE_GRAINED_TOKEN_SHAPE
CLASSIC_SHAPE: re.Pattern[str] = CLASSIC_TOKEN_SHAPE

TIER_NAMES: dict[int, str] = {
    0: "Tier 0 — read only, no credential",
    1: "Tier 1 — comment on issues and file issues",
    2: "Tier 2 — push branches to its own fork and open pull requests",
}

#: The twelve state labels of handoff §4.2 (plus ``harness:packaged``, R-D).
STATE_LABELS: tuple[str, ...] = (
    "harness:queued",
    "harness:proposing",
    "harness:proposed",
    "harness:approved",
    "harness:running",
    "harness:packaged",
    "harness:shipped",
    "harness:revising",
    "harness:merged",
    "harness:blocked",
    "harness:needs-human",
    "harness:abandoned",
)

# §13.2 for tiers 0 and 1, verbatim; §5.2 for tier 2 (classic PAT, ``public_repo`` only).
# (permission, value for the tier, note)
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
        ("Metadata", "Read", "Implied by `public_repo` on public repositories"),
        (
            "Issues",
            "Read and write",
            "Issues and comments in this repository only — never on the product repository (I-14)",
        ),
        (
            "Pull requests",
            "Read and write",
            "Opening the delivery PR from the fork; nothing that closes, accepts or alters a "
            "review (I-12)",
        ),
        (
            "Contents",
            "Read and write",
            "Pushing a branch to the fork the account owns; the product repository is not "
            "writable because the account is not a collaborator",
        ),
        (
            "Workflows",
            "none",
            "The `workflow` scope is deliberately absent: GitHub rejects any push that touches "
            "`.github/workflows/` (I-15)",
        ),
        ("Administration", "none", "No `admin:*` scope, ever"),
        ("Secrets, Environments, Actions", "none", "Never"),
    ),
}

NEVER_ASK_FOR_COMMON: tuple[str, ...] = (
    "Production credentials of any kind — database URLs, deployment keys, cloud accounts.",
    "Organization administration, or ownership of the organization.",
    "The `Workflows` permission, or the classic `workflow` scope. Without it GitHub itself "
    "rejects any push that modifies `.github/workflows/`, so \"never edit CI to go green\" stops "
    "being a rule the harness is trusted to follow and becomes something it cannot do.",
    "Branch-protection changes, or any relaxation of a required check.",
    "Merge rights. Every change the harness produces is reviewed and merged by a human.",
)

NEVER_ASK_FOR_FINE_GRAINED: tuple[str, ...] = NEVER_ASK_FOR_COMMON + (
    "A classic personal access token. Classic scopes are account-wide and cannot be "
    "narrowed to one repository.",
)

NEVER_ASK_FOR_CLASSIC: tuple[str, ...] = NEVER_ASK_FOR_COMMON + (
    "Any classic scope beyond `public_repo`: no `repo`, no `workflow`, no `admin:*`, "
    "no `write:org`, no `delete_repo`.",
    "Write access to the product repository. The account owns the fork and nothing else; "
    "it is not, and must not become, a collaborator upstream.",
)


def permission_set_for(tier: int) -> tuple[tuple[str, str, str], ...]:
    """The permission rows for a target tier: §13.2 fine-grained below 2, §5.2 classic at 2."""
    key = 0 if tier < 0 else (2 if tier > 2 else int(tier))
    return PERMISSION_SETS[key]


def permission_summary(tier: int) -> str:
    """One-line rendering of the set, for the `## Tokens` table cell."""
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
    """The machine account — `FORK_REPO`'s owner (`jgoetzmann-bot` here, D29), or the
    `HANDLE` default until that is set: detected and reported here, used by `gh.py`."""

    handle: str = HANDLE

    def __init__(self, config: Config, gh: GitHubReadOnly) -> None:
        self.config = config
        self.gh = gh
        # Held separately so render_human_doc never touches a Config field (I-10). Names only.
        self.repo = str(getattr(config, "repo", ""))
        self.self_repo = str(getattr(config, "self_repo", "") or "")
        fork = str(getattr(config, "fork_repo", "") or "")
        self.fork_repo = fork or f"{HANDLE}/{self.repo.split('/')[-1] or 'brightboost'}"
        # The account that owns the fork IS the machine account (handoff §5.1); the default
        # handle applies only until FORK_REPO is configured.
        self.handle = fork.split("/")[0] if fork else HANDLE
        trust_file = getattr(config, "trust_file", None)
        self.trust_file_name = Path(trust_file).name if trust_file else "trust.txt"

    # ------------------------------------------------------------- detection

    def token_present(self) -> bool:
        """True when the credential key is set and non-empty. Never reads the value."""
        return bool(getattr(self.config, _PRESENT_FIELD, False))

    def shape_ok(self) -> bool:
        return bool(getattr(self.config, _SHAPE_FIELD, False))

    def validate_shape(self, token: str) -> list[str]:
        """Shape errors, empty when plausible. Issues no request, ever."""
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

    def _get(self, path: str) -> Any:
        """A tolerant unauthenticated read: any failure reads as "not there"."""
        try:
            return self.gh.get(path)
        except (GitHubError, RateCeilingReached):
            return None

    def fork_exists(self) -> bool:
        """The fork exists, is a fork, and its parent is the product repository."""
        data = self._get(f"/repos/{self.fork_repo}")
        if not isinstance(data, dict) or not data.get("fork"):
            return False
        parent = data.get("parent") or {}
        full = str(parent.get("full_name") or "").lower() if isinstance(parent, dict) else ""
        return full == self.repo.lower() if self.repo else bool(full)

    def self_repo_public(self) -> bool:
        if not self.self_repo:
            return False
        data = self._get(f"/repos/{self.self_repo}")
        return isinstance(data, dict) and data.get("private") is False

    def codeowners_text(self) -> str:
        if not self.self_repo:
            return ""
        data = self._get(f"/repos/{self.self_repo}/contents/.github/CODEOWNERS")
        if not isinstance(data, dict):
            return ""
        raw = data.get("content")
        if not isinstance(raw, str) or not raw:
            return ""
        try:
            return base64.b64decode(raw.encode("ascii"), validate=False).decode("utf-8", "replace")
        except (ValueError, UnicodeError):
            return ""

    def labels_present(self) -> bool:
        if not self.self_repo:
            return False
        data = self._get(f"/repos/{self.self_repo}/labels?per_page=100")
        if not isinstance(data, list):
            return False
        names = {str(row.get("name") or "") for row in data if isinstance(row, dict)}
        return all(label in names for label in STATE_LABELS)

    def trust_file_ready(self) -> bool:
        """The trust file exists, carries no placeholder, and names at least two handles."""
        path = getattr(self.config, "trust_file", None)
        if not path:
            return False
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError:
            return False
        if TRUST_PLACEHOLDER in text or "NATHAN_HANDLE" in text:
            return False
        handles = [
            line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")
        ]
        return len(handles) >= 2

    def budget_experiment_recorded(self) -> bool:
        root = getattr(self.config, "repo_root", None)
        if not root:
            return False
        try:
            text = (Path(root) / "DECISIONS.md").read_text(encoding="utf-8")
        except OSError:
            return False
        return "max-budget-usd" in text

    # ---------------------------------------------------------------- assess

    def assess(self, target_tier: int) -> Readiness:
        """The gap between what is configured now and what `target_tier` needs."""
        target = int(target_tier)
        current = int(getattr(self.config, "permission_tier", 0))
        if target >= 2:
            candidates = self._tier2_prerequisites(current, target)
        else:
            candidates = self._tier1_prerequisites(current, target)
        included = tuple(p for p in candidates if p.tier_required <= target)
        return Readiness(
            current_tier=current,
            target_tier=target,
            prerequisites=included,
            ready=all(p.satisfied for p in included),
        )

    def _tier1_prerequisites(self, current: int, target: int) -> tuple[Prerequisite, ...]:
        """Delivery 1's list, unchanged: account, email-2fa, token, org-approval, tier."""
        repo = self.repo or "the product repository"
        account_ok = self.account_exists()
        token_ok = self.token_present() and self.shape_ok()
        return (
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
                title=f"Fine-grained PAT present in `.env` as `{KEY_NAME}`",
                tier_required=1,
                satisfied=token_ok,
                actor="you",
                detail=(
                    "Generate a fine-grained personal access token with exactly the "
                    f"permission set in `## Tokens` below, scoped to `{repo}` alone, "
                    "with the shortest expiry you will tolerate re-issuing. Put it in "
                    f"`.env` under the key `{KEY_NAME}`. Never in a chat, never in a "
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
        )

    def _tier2_prerequisites(self, current: int, target: int) -> tuple[Prerequisite, ...]:
        """Handoff §16: the sixteen human prerequisites, in order, then the tier agreement and
        the standing confirmation that the account is not a collaborator upstream."""
        repo = self.repo or "the product repository"
        self_repo = self.self_repo or "this repository"
        codeowners = self.codeowners_text()
        token_ok = self.token_present() and self.shape_ok()
        tracking = getattr(self.config, "tracking_issue", None)
        return (
            Prerequisite(
                id="nathan-handle",
                title=(
                    "Nathan's GitHub handle confirmed and written to "
                    f"`.harness/{self.trust_file_name}`"
                ),
                tier_required=2,
                satisfied=self.trust_file_ready(),
                actor="you",
                detail=(
                    f"The trust file currently carries `{TRUST_PLACEHOLDER}`. Every keyword gate "
                    "reads this file; until the handle is real, only you can steer the harness "
                    "with `/harness` commands, and every delivery PR requests review from a "
                    "placeholder."
                ),
                verify=f"grep -c NATHAN_HANDLE .harness/{self.trust_file_name}",
            ),
            Prerequisite(
                id="account",
                title=f"Machine account `{self.handle}` created",
                tier_required=2,
                satisfied=self.account_exists(),
                actor="you",
                detail=(
                    "Accepting GitHub's terms is a human act. One machine account alongside a "
                    "personal account is permitted; you own it and are answerable for it."
                ),
                verify=f"harness setup --tier {target}",
            ),
            Prerequisite(
                id="fork",
                title=f"`{repo}` forked into the machine account as `{self.fork_repo}`",
                tier_required=2,
                satisfied=self.fork_exists(),
                actor="you",
                detail=(
                    "The account must own the fork so pull requests are attributable to "
                    "automation, not to you. The fork's default branch is only ever "
                    "fast-forwarded from upstream (B105)."
                ),
                verify=f"harness setup --tier {target}",
            ),
            Prerequisite(
                id="fork-actions",
                title="Actions enabled on the fork",
                tier_required=2,
                satisfied=False,
                actor="you",
                detail=(
                    "Disabled by default on forks. Needed only if you want the fork's CI as a "
                    "second opinion; the harness runs its own gates regardless. Nothing outside "
                    "the fork's settings page can check this."
                ),
                verify=None,
            ),
            Prerequisite(
                id="classic-pat",
                title=(
                    f"Classic PAT on `{self.handle}`, scope `public_repo` only, present in "
                    f"`.env` as `{KEY_NAME}`"
                ),
                tier_required=2,
                satisfied=token_ok,
                actor="you",
                detail=(
                    "Fine-grained tokens cannot open a pull request from a fork to an upstream "
                    "repository (§5.2), so the credential is classic. Do not add `workflow`: "
                    "its absence is invariant I-15. Do not add `repo`, `admin:*` or anything "
                    "else. Put the value in `.env` only; never in a chat, a commit, or a "
                    "package."
                ),
                verify="harness doctor",
            ),
            Prerequisite(
                id="claude-token",
                title=(
                    "`claude setup-token` run and saved as repository secret "
                    "`CLAUDE_CODE_OAUTH_TOKEN`"
                ),
                tier_required=2,
                satisfied=False,
                actor="you",
                detail=(
                    "Requires an interactive browser login against your subscription. The "
                    "harness cannot inspect repository secrets, so confirm this by hand."
                ),
                verify=None,
            ),
            Prerequisite(
                id="pat-secret",
                title=f"The classic PAT saved as repository secret `{KEY_NAME}`",
                tier_required=2,
                satisfied=False,
                actor="you",
                detail=(
                    "The scheduled workflows read it from the repository secret; the local "
                    "`.env` copy never reaches the container (§10.5). Repository secrets are "
                    "not readable, so confirm by hand."
                ),
                verify=None,
            ),
            Prerequisite(
                id="public-repo",
                title=f"`{self_repo}` made public",
                tier_required=2,
                satisfied=self.self_repo_public(),
                actor="you",
                detail=(
                    "Free unlimited Actions minutes on standard runners. Verify no secret is "
                    "committed first (`git log -p --all | grep -cE \"ghp_|github_pat_|sk-ant-\"` "
                    "must print 0)."
                ),
                verify=f"harness setup --tier {target}",
            ),
            Prerequisite(
                id="branch-protection",
                title="Branch protection on `main`: one approving review, no force-push",
                tier_required=2,
                satisfied=False,
                actor="you",
                detail=(
                    "B113. The harness's own proposal PRs are subject to it like anything else. "
                    "Reading protection rules needs administrative access the harness does not "
                    "have, so confirm by hand."
                ),
                verify=None,
            ),
            Prerequisite(
                id="codeowners",
                title=(
                    "`.github/CODEOWNERS` committed naming you for `.harness/`, `prompts/`, "
                    "`.github/`, `gates.py`, `redact.py`"
                ),
                tier_required=2,
                satisfied=bool(codeowners)
                and all(
                    marker in codeowners
                    for marker in ("/.harness/", "/prompts/", "/.github/", "gates.py", "redact.py")
                ),
                actor="you",
                detail=(
                    "§5.5. The trust file, the prompts, the workflows and the two pinned "
                    "result-defining modules change only through a reviewed pull request."
                ),
                verify="cat .github/CODEOWNERS",
            ),
            Prerequisite(
                id="labels",
                title=f"The twelve `harness:*` state labels exist on `{self_repo}`",
                tier_required=2,
                satisfied=self.labels_present(),
                actor="you",
                detail=(
                    "Handoff §4.2: the queue is the label set. `harness init --labels` creates "
                    "them idempotently once the credential is in place."
                ),
                verify="harness init --labels",
            ),
            Prerequisite(
                id="tracking-issue",
                title="Pinned tracking issue opened and its number recorded as `TRACKING_ISSUE`",
                tier_required=2,
                satisfied=isinstance(tracking, int) and tracking > 0,
                actor="you",
                detail=(
                    "`heartbeat.yml` comments on it weekly; the absence of that comment is the "
                    "alarm (B144). Record the number in `.harness/config.json` or `.env`."
                ),
                verify="harness doctor",
            ),
            Prerequisite(
                id="proposals-codeowners",
                title="Decided whether Nathan reviews proposals; `CODEOWNERS` covers `/proposals/`",
                tier_required=2,
                satisfied=bool(codeowners) and "/proposals/" in codeowners,
                actor="you",
                detail=(
                    "You chose \"either can approve\"; a `/proposals/` line in `CODEOWNERS` "
                    "makes it mechanical. Decide it with Nathan, then commit the line."
                ),
                verify="grep -n proposals .github/CODEOWNERS",
            ),
            Prerequisite(
                id="max-budget-experiment",
                title="Verified whether `claude --max-budget-usd` binds under subscription auth",
                tier_required=2,
                satisfied=self.budget_experiment_recorded(),
                actor="you",
                detail=(
                    "A five-minute experiment. If it does not bind on a subscription, "
                    "`PER_CALL_CAP_USD` is advisory and \"enforced twice\" becomes \"enforced "
                    "once\". Record the result in `DECISIONS.md` either way."
                ),
                verify="grep -n max-budget-usd DECISIONS.md",
            ),
            Prerequisite(
                id="docker",
                title="Docker Desktop installed and `docker info` succeeds (local mode only)",
                tier_required=2,
                satisfied=False,
                actor="you",
                detail=(
                    "Phase 8 only. Local mode runs the same harness in the `bb` container; "
                    "Actions mode does not need this."
                ),
                verify="docker info",
            ),
            Prerequisite(
                id="tell-nathan",
                title="Nathan told this exists, before the first delivery PR arrives",
                tier_required=2,
                satisfied=False,
                actor="you",
                detail=(
                    "A bot PR from an unknown account on a public repository, unannounced, is a "
                    "bad first impression and may be treated as spam."
                ),
                verify=None,
            ),
            Prerequisite(
                id="tier",
                title=f"`PERMISSION_TIER` raised to {target}",
                tier_required=2,
                satisfied=current >= target,
                actor="nathaniel",
                detail=(
                    f"Agree {tier_name(target)}, then set `PERMISSION_TIER={target}` in `.env`. "
                    "Tier 2 also requires `STORE_BACKEND=github` and a non-empty `FORK_REPO`. "
                    "Nathaniel agrees the tier; you edit the file."
                ),
                verify="harness doctor",
            ),
            Prerequisite(
                id="collaborator",
                title=f"`{self.handle}` is NOT a collaborator on `{repo}` — confirmed",
                tier_required=2,
                satisfied=False,
                actor="nathaniel",
                detail=(
                    "The account delivers through pull requests from its own fork and must have "
                    "no write access upstream (§5.1, review check R5.5). Confirm in the product "
                    "repository's collaborator settings that it is absent, and keep it so."
                ),
                verify=None,
            ),
        )

    # ----------------------------------------------------------------- token

    def load_token(self) -> str:
        """The only function here that returns the secret. Raises below Tier 1 (B80)."""
        tier = int(getattr(self.config, "permission_tier", 0))
        if tier < 1:
            raise TierViolation(
                f"permission_tier is {tier}: the harness may not hold or transmit "
                f"`{KEY_NAME}` below tier 1, even when a valid value is configured"
            )
        from harness import config as config_module

        value = config_module.read_secret(KEY_NAME)
        if not value:
            raise ConfigError(f"{KEY_NAME} is required at tier {tier} but is absent or empty")
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
        lines.append("| # | Prerequisite | Actor | Needed from tier | Status |")
        lines.append("|---|---|---|---|---|")
        for index, pre in enumerate(readiness.prerequisites, start=1):
            status = "done" if pre.satisfied else "outstanding"
            lines.append(
                f"| {index} | {pre.title} | {pre.actor} | {pre.tier_required} | {status} |"
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
                lines.append(f"{index}. **{pre.title}** (actor: you, id `{pre.id}`)")
                lines.append(f"   Why: {pre.detail}")
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
                f"| `{KEY_NAME}` | none — tier 0 holds no credential | "
                "*(no token at all)* | not applicable | not applicable |"
            )
        elif target < 2:
            lines.append(
                f"| `{KEY_NAME}` | Fine-grained personal access token, scoped to "
                f"`{repo}` alone — never a classic token | {permission_summary(target)} | "
                f"{CREATE_URL} | An organization owner (Nathaniel) when the organization "
                "restricts fine-grained tokens |"
            )
        else:
            lines.append(
                f"| `{KEY_NAME}` | Classic personal access token on `{self.handle}`, scope "
                f"`public_repo` **only** | {permission_summary(target)} | "
                f"{CLASSIC_CREATE_URL} | Nobody: the account owns the fork `{self.fork_repo}` "
                "and nothing of the organization's; there is no organization-side approval |"
            )
        lines.append("")
        if target >= 2:
            lines.append(
                "**Why a classic token and not fine-grained (handoff §5.2).** Fine-grained "
                "tokens cannot open a pull request from a fork to an upstream repository — a "
                "documented, long-standing limitation. A classic token with `public_repo` is "
                "the working credential. On an account that owns nothing but the fork, that is "
                "a bounded blast radius: it can write to public repositories the account can "
                "already write to, which is the fork, and it can open pull requests, which is "
                "the point. The one credential is used by `harness/gh.py` and by nothing else "
                "(I-11)."
            )
            lines.append("")
            lines.append(f"What `public_repo` grants, and what it does not, for tier {target}:")
        else:
            lines.append(f"The exact permission set for tier {target}, from the tier table:")
        lines.append("")
        lines.append("| Permission | Tier " + str(target) + " | Note |")
        lines.append("|---|---|---|")
        for name, value, note in permission_set_for(target):
            lines.append(f"| `{name}` | {value} | {note} |")
        lines.append("")
        if target >= 2:
            lines.append(
                "The scope that is deliberately absent is `workflow`. GitHub rejects any push "
                "that modifies `.github/workflows/`, so \"never edit CI to go green\" is "
                "enforced by the receiving end (I-15), not trusted to the model. `repo`, "
                "`admin:*` and every other classic scope are absent too."
            )
        else:
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
                lines.append(f"{index}. **{pre.title}** (actor: Nathaniel, id `{pre.id}`)")
                lines.append(f"   Why: {pre.detail}")
                if pre.verify:
                    lines.append(f"   Confirm with: `{pre.verify}`")
                lines.append("")
        else:
            lines.append("Nothing outstanding on Nathaniel's side.")
            lines.append("")

        # ---- 5. What the harness will never ask for -----------------------------
        lines.append("## What the harness will never ask for")
        lines.append("")
        never = NEVER_ASK_FOR_CLASSIC if target >= 2 else NEVER_ASK_FOR_FINE_GRAINED
        for entry in never:
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
