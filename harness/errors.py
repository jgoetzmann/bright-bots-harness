"""Exception hierarchy; everything derives from :class:`HarnessError`."""

from __future__ import annotations

from typing import Sequence

__all__ = [
    "HarnessError",
    "ConfigError",
    "StoreError",
    "DuplicateWorkItem",
    "IllegalTransition",
    "BudgetExhausted",
    "RateCeilingReached",
    "GitHubError",
    "CloneError",
    "PreflightFailed",
    "Halted",
    "GateFailed",
    "TierViolation",
    "RunnerError",
    "PackageError",
    "NotImplementedInDelivery1",
    "WriteOutsideAllowedRoots",
    "RateLimited",
    "TrustDenied",
    "ForkDiverged",
    "ProposalInvalid",
    "PinMismatch",
    "RepoHalted",
]


class HarnessError(Exception):
    """Base class for every error the harness raises on purpose."""


class ConfigError(HarnessError):
    """Configuration is missing, unknown, malformed, or forbidden in this delivery."""


class StoreError(HarnessError):
    """The SQLite store could not satisfy a request."""


class DuplicateWorkItem(StoreError):
    """A work item with the same ``external_ref`` already exists."""


class IllegalTransition(StoreError):
    """A state transition absent from the legal-transition table was attempted."""


class BudgetExhausted(HarnessError):
    """The governor cannot fund the requested stage from the remaining allowance."""


class RateCeilingReached(HarnessError):
    """The unauthenticated GitHub call ceiling for the trailing hour would be exceeded."""


class GitHubError(HarnessError):
    """An unauthenticated GitHub read failed or returned an unusable response."""


class CloneError(HarnessError):
    """A disposable clone could not be created, branched, or released."""


class PreflightFailed(HarnessError):
    """Clone preflight found blockers: disk, a missing ``git``, or an engaged halt."""


class Halted(HarnessError):
    """The kill switch is engaged; no further stage may start."""


class GateFailed(HarnessError):
    """The product repository's gate sequence is red and cannot be honestly fixed."""


class TierViolation(HarnessError):
    """An action was attempted that the configured permission tier forbids."""


class RunnerError(HarnessError):
    """A model runner could not be constructed or produced an unusable result."""


class PackageError(HarnessError):
    """The review package could not be built or promoted."""


class NotImplementedInDelivery1(HarnessError):
    """A surface that exists in the spec but ships no implementation in Delivery 1."""


class WriteOutsideAllowedRoots(HarnessError):
    """A write was attempted outside the roots the harness is permitted to touch (I-8)."""


class RateLimited(HarnessError):
    """The model runner reported a usage or rate limit (B119/B120).

    ``reset_at`` is the ISO-Z instant the limit lifts, an ISO-8601 duration such as
    ``"+PT30M"`` when the runner only saw a relative phrase, or ``None`` when unknown.
    """

    def __init__(self, message: str = "rate limited", reset_at: str | None = None) -> None:
        super().__init__(message)
        self.reset_at: str | None = reset_at


class TrustDenied(HarnessError):
    """A keyword command came from an actor who is not trusted (B131)."""


class ForkDiverged(HarnessError):
    """The fork's default branch is not an ancestor of upstream's; nothing was pushed (B105)."""


class ProposalInvalid(HarnessError):
    """A proposal's front matter failed schema validation (B103/B104); ``errors`` lists why."""

    def __init__(self, message: str = "", errors: Sequence[str] | None = None) -> None:
        self.errors: list[str] = [str(item) for item in (errors or [])]
        text = message or "invalid proposal"
        if self.errors and not message:
            text = "invalid proposal: " + "; ".join(self.errors)
        super().__init__(text)


class PinMismatch(HarnessError):
    """The SHA-256 over the pinned set does not match ``.harness/PIN`` (B142)."""


class RepoHalted(HarnessError):
    """``.harness/HALT`` exists on the repository; distinct from the Delivery 1 ``Halted``."""
