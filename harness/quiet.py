"""The quiet check: start spending only while nobody else is using the subscription (D93).

Two usage readings an interval apart, each from the smallest model call there is (the ping).
The pair is quiet when no window's utilization rose between them. A rise is excused while the
partner bot that shares the subscription was inside one of its spending steps, and any doubt
about the partner excuses nothing. The sampler, the sleep and the GitHub readers are passed in,
so this module decides and never spends, sleeps or reads on its own.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from harness.clock import parse_iso
from harness.errors import GitHubError
from harness.ledger import USAGE_WINDOWS
from harness.runner.base import exhausted_reset, usage_rejected

__all__ = [
    "Outcome",
    "Partner",
    "Sample",
    "compare",
    "partner_spending",
    "partner_steps",
    "wait_until_quiet",
]

#: A pair's verdicts.
QUIET = "quiet"
ROSE = "rose"
RESET = "reset"
REFUSED = "refused"
UNREAD = "unread"

#: The statuses on which a read of the partner with the token is made once more without it,
#: since the partner's repository may be public where the token has no access.
RETRY_WITHOUT_TOKEN: frozenset[int] = frozenset({401, 403, 404})

#: How many of the partner workflow's newest runs are listed for one rise.
PARTNER_RUNS = 10

#: A step's conclusion when GitHub skipped it. It carries both timestamps and spent nothing.
SKIPPED = "skipped"

#: What the partner check says when no partner is configured, and when it found no step.
NO_PARTNER = "no partner is configured"
PARTNER_IDLE = "the partner was not spending"


@dataclass(frozen=True)
class Sample:
    """One reading: when the ping returned, the usage it reported, and why it reported none."""

    at: str
    usage: Mapping[str, Any] | None
    error: str | None = None

    def to_json(self) -> dict:
        usage = dict(self.usage) if isinstance(self.usage, Mapping) else None
        return {"at": self.at, "usage": usage, "error": self.error}


@dataclass(frozen=True)
class Partner:
    """The bot sharing the subscription: its repository, one workflow file, and the prefixes
    of the step names during which it spends."""

    repo: str
    workflow: str
    prefixes: tuple[str, ...]


@dataclass
class Outcome:
    """What the check found. ``quiet`` is the one field a workflow acts on."""

    quiet: bool
    reason: str
    samples: list[Sample] = field(default_factory=list)
    excused: bool = False
    partner_steps: list[str] = field(default_factory=list)
    partner_note: str = ""
    forced: bool = False
    #: The sample whose reading says the subscription refused the ping, when one did.
    refused: Sample | None = None

    def to_json(self) -> dict:
        return {
            "quiet": bool(self.quiet),
            "reason": self.reason,
            "forced": bool(self.forced),
            "excused": bool(self.excused),
            "samples": [sample.to_json() for sample in self.samples],
            "partner": {"steps": list(self.partner_steps), "note": self.partner_note},
        }


def _levels(usage: object) -> dict[str, float]:
    """Each window's utilization in a reading, for the windows that report a number."""
    levels: dict[str, float] = {}
    if not isinstance(usage, Mapping):
        return levels
    for name in USAGE_WINDOWS:
        window = usage.get(name)
        if not isinstance(window, Mapping):
            continue
        value = window.get("utilization")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            levels[name] = float(value)
    return levels


def _five_hour_reset(usage: object) -> object:
    window = usage.get("five_hour") if isinstance(usage, Mapping) else None
    return window.get("resets_at") if isinstance(window, Mapping) else None


def compare(first: Sample, second: Sample) -> tuple[str, tuple[str, ...]]:
    """The pair's verdict and the windows that rose (B526).

    ``refused`` when either reading says the subscription refused the ping; ``unread`` when the
    two share no window with a utilization; ``reset`` when the five-hour ``resets_at`` changed
    between them, which leaves the pair inconclusive (B528); else ``rose`` or ``quiet``.
    """
    if usage_rejected(first.usage) or usage_rejected(second.usage):
        return REFUSED, ()
    before, after = _levels(first.usage), _levels(second.usage)
    common = [name for name in USAGE_WINDOWS if name in before and name in after]
    if not common:
        return UNREAD, ()
    if "five_hour" in common and _five_hour_reset(first.usage) != _five_hour_reset(second.usage):
        return RESET, ()
    rose = tuple(name for name in common if after[name] > before[name])
    return (ROSE, rose) if rose else (QUIET, ())


def partner_steps(reader: Any, partner: Partner, start: datetime, end: datetime) -> list[str]:
    """The partner's spending steps that overlap ``[start, end]``; raises when a read fails.

    Only a run still going, or updated at or after ``start``, is opened. A step counts when its
    name starts with one of the prefixes, it started by ``end``, it had not finished before
    ``start``, and GitHub did not skip it (B531).
    """
    found: list[str] = []
    for run in reader.workflow_file_runs(partner.repo, partner.workflow, per_page=PARTNER_RUNS):
        updated = run.get("updated_at")
        if run.get("status") == "completed" and updated and parse_iso(str(updated)) < start:
            continue
        run_id = int(run["id"])
        for job in reader.run_jobs(partner.repo, run_id):
            for step in job.get("steps") or ():
                if not isinstance(step, Mapping):
                    continue
                name = str(step.get("name") or "")
                if not name.startswith(partner.prefixes) or step.get("conclusion") == SKIPPED:
                    continue
                began = step.get("started_at")
                if not began or parse_iso(str(began)) > end:
                    continue
                ended = step.get("completed_at")
                if ended and parse_iso(str(ended)) < start:
                    continue
                until = ended or "now"
                found.append(f"{partner.repo} run {run_id}: {name} from {began} to {until}")
    return found


def partner_spending(
    partner: Partner | None,
    start: datetime,
    end: datetime,
    *,
    reader: Any,
    public: Callable[[], Any] | None = None,
) -> tuple[list[str], str]:
    """``(steps, note)``: the partner's spending steps in ``[start, end]``, and when there are
    none, why.

    ``reader`` goes first. ``public`` builds a reader that sends no token, tried once when the
    first answer was a 401, 403 or 404. Any failure to read is a note and excuses nothing
    (B531).
    """
    if partner is None:
        return [], NO_PARTNER
    try:
        steps = partner_steps(reader, partner, start, end)
    except GitHubError as exc:
        if public is None or exc.status not in RETRY_WITHOUT_TOKEN:
            return [], f"the partner could not be read: {exc}"
        try:
            steps = partner_steps(public(), partner, start, end)
        except Exception as retry:  # any failure excuses nothing
            return [], f"the partner could not be read: {exc}; nor without the token: {retry}"
    except Exception as exc:  # any failure excuses nothing
        return [], f"the partner could not be read: {str(exc) or exc.__class__.__name__}"
    return steps, "" if steps else PARTNER_IDLE


def _pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def _rise(first: Sample, second: Sample, rose: tuple[str, ...]) -> str:
    before, after = _levels(first.usage), _levels(second.usage)
    moved = ", ".join(f"{name} {_pct(before[name])} -> {_pct(after[name])}" for name in rose)
    return f"{moved} from {first.at} to {second.at}"


def _unread(sample: Sample) -> str:
    why = sample.error or "no rate_limit_event"
    return (
        f"the ping at {sample.at} reported no usage ({why}); unknown is not a stop, so this run "
        "goes ahead (B114)"
    )


def _refused(sample: Sample) -> str:
    reset = exhausted_reset(sample.usage)
    until = f" until {reset}" if reset else ""
    return f"usage stop: the subscription refused the ping at {sample.at}{until}"


def wait_until_quiet(
    *,
    sample: Callable[[], Sample],
    sleep: Callable[[float], None],
    interval_s: float,
    intervals: int,
    partner_check: Callable[[datetime, datetime], tuple[list[str], str]],
) -> Outcome:
    """Sample, wait ``interval_s``, sample, until a pair is quiet or ``intervals`` are spent.

    A rise the partner check explains is quiet (B531) and one it does not explain waits another
    interval, as does a reset pair (B528); after the last interval the run gives up (B527). A
    refusal stops at once (B529), and a sample with no reading admits (B530).
    """
    first = sample()
    samples = [first]
    if usage_rejected(first.usage):
        return Outcome(False, _refused(first), samples, refused=first)
    if not _levels(first.usage):
        return Outcome(True, _unread(first), samples)
    rounds = max(1, int(intervals))
    last = ""
    note = ""
    for _ in range(rounds):
        sleep(interval_s)
        second = sample()
        samples.append(second)
        verdict, rose = compare(first, second)
        if verdict == QUIET:
            return Outcome(True, f"no window rose from {first.at} to {second.at}", samples)
        if verdict == REFUSED:
            return Outcome(False, _refused(second), samples, refused=second)
        if verdict == UNREAD:
            return Outcome(True, _unread(second), samples)
        if verdict == ROSE:
            steps, note = partner_check(parse_iso(first.at), parse_iso(second.at))
            if steps:
                return Outcome(
                    True,
                    f"{_rise(first, second, rose)} while the partner was spending, which "
                    "excuses it",
                    samples,
                    excused=True,
                    partner_steps=steps,
                )
            last = f"{_rise(first, second, rose)}, and {note}"
        else:
            last = f"the five-hour window reset between {first.at} and {second.at}"
        first = second
    waited = round(rounds * interval_s / 60)
    return Outcome(
        False,
        f"not quiet after {waited} minutes: {last}; nothing starts this run",
        samples,
        partner_note=note,
    )
