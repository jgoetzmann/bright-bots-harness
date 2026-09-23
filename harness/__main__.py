"""Command line entry point for every `harness` subcommand."""

from __future__ import annotations

import argparse
import dataclasses
import functools
import json
import logging
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path

from harness import __version__, keywords, links, verify_pin
from harness import config as config_mod
from harness import ledger as ledger_mod
from harness.clock import iso, parse_iso
from harness.clone import Lease, sync_fork
from harness.config import in_run_window, is_daily_window, load_config, run_window_label
from harness.context import build_context, ledger_path_for
from harness.dispatcher import Candidate, Plan, plan as plan_dispatch
from harness.errors import (
    BudgetExhausted,
    ConfigError,
    ForkDiverged,
    GitHubError,
    Halted,
    HarnessError,
    NotImplementedInDelivery1,
    PinMismatch,
    RateCeilingReached,
    RateLimited,
    RepoHalted,
)
from harness.gh import machine_logins, mark_machine_written, public_reader
from harness.halt import check_halt, check_repo_halt, disengage, engage, halted, repo_halted
from harness.identity import Identity, write_human_doc
from harness import priority
from harness.packager import archive as archive_package
from harness.packager import build as build_package
from harness.redact import allowed_roots, guarded_write, set_write_roots
from harness.stages import STAGES
from harness.stages import deliver as deliver_stage
from harness.stages import discover as discover_stage
from harness.store import (
    KIND_LABELS,
    LABELS,
    LABEL_SPECS,
    LEGACY_LABELS,
    STATES,
    TRANSITIONS,
    VIA_LABELS,
)
# The GitHub backend's label vocabulary, not re-exported by `harness.store`. `relabel` needs it
# because it reads both label families (B265).
from harness.store.github import STATE_OF_LABEL, _label_names
from harness.priority import via_of
from harness.trust import MAX_LEVEL
from harness import trust as trust_mod
from harness.trust import load_trust

LOG = logging.getLogger("harness")

# Injectables. Tests monkeypatch these rather than the stdlib.
WHICH = shutil.which
RUN = subprocess.run
SLEEP = time.sleep
#: The unauthenticated client `harness trust line` resolves an account id with. It is not built
#: from a `Context`, so the command runs in a checkout with no `.env` and no database (D69).
PUBLIC_READER = public_reader

#: The `TRUST_FILE` default from `.env.example`, for reading that file with no configuration.
DEFAULT_TRUST_FILE = Path(".harness") / "trust.txt"

#: `user.type` for a person. Only a person authors a comment, so only a person's account id can
#: match the `comment.user.id` a vouch is checked against (D69).
ACCOUNT_TYPE_PERSON = "User"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_UNIMPLEMENTED = 2
EXIT_DEGRADED = 3
EXIT_BUDGET = 4
EXIT_HALTED = 5
EXIT_SETUP_OUTSTANDING = 6

REQUIRED_BINARIES = ("git", "claude", "node", "npm", "npx")

MAX_TURNS_PROBE_ARGV = ["claude", "-p", "--max-turns", "1", "--output-format", "json", ""]

# Every key doctor reports, with the Config field that carries it once the config has loaded.
# Every key of ``config.CONFIG_JSON_KEYS`` is here, so doctor confirms a knob change.
CONFIG_KEYS: tuple[tuple[str, str], ...] = (
    ("MAX_CONCURRENT_ITEMS", "max_concurrent_items"),
    ("MAX_REVISE_CYCLES", "max_revise_cycles"),
    ("FORK_REPO", "fork_repo"),
    ("UPSTREAM_REPO", "upstream_repo"),
    ("TRUST_FILE", "trust_file"),
    ("MAX_SUBISSUES", "max_subissues"),
    ("SELF_REPO", "self_repo"),
    ("TRACKING_ISSUE", "tracking_issue"),
    ("STORE_BACKEND", "store_backend"),
    # The two usage stops, the overrun allowance and the run window.
    ("WEEKLY_USAGE_STOP_PCT", "weekly_usage_stop_pct"),
    ("SESSION_USAGE_STOP_PCT", "session_usage_stop_pct"),
    ("OVERRUN_PCT", "overrun_pct"),
    ("RUN_WINDOW_START", "run_window_start"),
    ("RUN_WINDOW_END", "run_window_end"),
    # What the model calls run as; a changed model or effort changes the work (B225).
    ("MODEL", "model"),
    ("EFFORT", "effort"),
    # What may ask for work, and what bounds the answers.
    ("INBOX_ISSUE", "inbox_issue"),
    ("SUGGEST_MAX_PER_RUN", "suggest_max_per_run"),
    ("COMMENT_UPSTREAM", "comment_upstream"),
    ("ASK_MAX_PER_DAY", "ask_max_per_day"),
    ("SUGGEST_MIN_HEADROOM_PCT", "suggest_min_headroom_pct"),
    ("AUDIT_MIN_HEADROOM_PCT", "audit_min_headroom_pct"),
    # How many audit/fix cycles run before delivery; 0 is off (D70).
    ("MAX_SELF_AUDIT_CYCLES", "max_self_audit_cycles"),
    # Who every harness commit credits as co-author (D82).
    ("CO_AUTHOR", "co_author"),
)

# An item left in a running state longer than this with no live run is reset (B147).
STALE_RUNNING_HOURS = 3

# local-loop: HEARTBEAT cadence while sleeping between units.
HEARTBEAT_SLICE_S = 10
DEFAULT_LOOP_SECONDS = 300

PROPOSE_BRANCH_RE = re.compile(r"harness/propose-(\d+)$")

#: `proposals/<id>-<slug>.md` — the committed record a merged proposal leaves on main (D46). It
#: is what `harness approve --merged` reconciles against, because the file survives the run that
#: merged it while a push diff exists only inside that one event (D78).
PROPOSAL_FILE_RE = re.compile(r"^(\d+)-.*\.md$")

#: How many workflow runs the Actions section asks GitHub for: one page, newest first, and the
#: largest page the endpoint serves. A page that comes back full means older runs went unread,
#: which `_actions_rows` reports rather than rendering as an idle queue (D79).
ACTIONS_PER_PAGE = 100


# --------------------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harness",
        description="Bright Bots harness - discovery through delivery PR.",
    )
    parser.add_argument("--config", metavar="PATH", default=None, help="path to the .env file")
    parser.add_argument("--verbose", action="store_true", help="debug logging to stderr")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="record every GitHub write instead of sending it",
    )
    parser.add_argument("--version", action="version", version=f"bright-bots-harness {__version__}")

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    init = sub.add_parser("init", help="create db, dirs and .env from .env.example when absent")
    init.add_argument(
        "--labels",
        action="store_true",
        help="also create the harness:* state labels in SELF_REPO (idempotent)",
    )

    doctor = sub.add_parser("doctor", help="probe binaries, versions, --max-turns, disk, halt")
    doctor.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    setup = sub.add_parser("setup", help="assess identity readiness and regenerate HUMAN.md")
    setup.add_argument("--tier", type=int, default=1, metavar="N", help="target tier (default 1)")

    status = sub.add_parser("status", help="queue by state, subscription usage, in-flight runs")
    status.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    discover = sub.add_parser("discover", help="find work")
    discover.add_argument(
        "--mode", required=True, choices=("triage", "directed", "assigned", "audit")
    )
    discover.add_argument("--target", default=None, metavar="N", help="issue number for directed")
    discover.add_argument("--lens", default=None, metavar="L", help="optional triage lens")
    discover.add_argument("--ignore-allowlist", action="store_true", dest="ignore_allowlist")

    propose = sub.add_parser("propose", help="produce the work package for an item")
    propose.add_argument("item_id", type=int, metavar="item-id")

    approve = sub.add_parser("approve", help="proposed -> approved (gate 1)")
    approve.add_argument("item_id", type=int, nargs="?", default=None, metavar="item-id")
    approve.add_argument("--note", default=None, metavar="TEXT")
    approve.add_argument(
        "--merged",
        action="store_true",
        help="approve every proposed item whose proposal file is on main (D78)",
    )

    run = sub.add_parser("run", help="serial loop over approved items")
    run.add_argument("--item", type=int, default=None, metavar="ID")
    run.add_argument("--until", default=None, metavar="HH:MM")

    package = sub.add_parser("package", help="build the review package for an item")
    package.add_argument("item_id", type=int, metavar="item-id")

    archive = sub.add_parser("archive", help="promote a package into packages/")
    archive.add_argument("item_id", type=int, metavar="item-id")
    archive.add_argument("--with-transcript", action="store_true", dest="with_transcript")

    sub.add_parser("halt", help="create the halt file")
    resume = sub.add_parser("resume", help="remove the halt file")
    resume.add_argument(
        "--commanded",
        action="store_true",
        help="also lift a halt set by `/harness halt` (recorded in the ledger)",
    )

    block = sub.add_parser(
        "block", help="suspend the run window for the next N five-hour sessions (0 cancels)"
    )
    block.add_argument("sessions", nargs="?", default=None, metavar="N")
    block.add_argument("--reason", default="", metavar="TEXT")

    sub.add_parser("dispatch", help="ask the dispatcher what may start now; start nothing")

    deliver = sub.add_parser("deliver", help="push the branch and open the upstream PR")
    deliver.add_argument("item_id", type=int, metavar="item-id")

    revise = sub.add_parser("revise", help="one bounded revision cycle")
    revise.add_argument("item_id", type=int, metavar="item-id")
    revise.add_argument(
        "--source", required=True, choices=("ci", "conflict", "review", "continue")
    )
    revise.add_argument("--notes", default="", metavar="TEXT")

    decompose = sub.add_parser("decompose", help="split one issue into sub-issues")
    decompose.add_argument("issue", type=int, metavar="issue")

    sweep = sub.add_parser("sweep", help="poll notifications, parse keywords, act on them")
    sweep.add_argument(
        "--thread",
        type=int,
        default=0,
        help="also read this issue or pull request here directly (the comment event's)",
    )

    ack = sub.add_parser(
        "ack", help="say 'working on it' for one comment, before the work starts"
    )
    ack.add_argument("--body-file", required=True, help="file holding the comment body")
    ack.add_argument("--actor", required=True, help="the commenter's login")
    ack.add_argument("--association", default="", help="GitHub author_association")
    ack.add_argument(
        "--actor-id", default="", help="the commenter's numeric GitHub user id (D68)"
    )
    ack.add_argument("--repo", default="", help="repository the comment is on")
    ack.add_argument("--number", type=int, default=0, help="issue or pull request number")

    sub.add_parser(
        "tidy",
        help="mark merged deliveries done; rewrite the pinned queue; prune old bot comments",
    )

    trust_cmd = sub.add_parser(
        "trust", help="print the trust.txt line for a login, or the current file interpreted"
    )
    trust_sub = trust_cmd.add_subparsers(dest="trust_action", metavar="ACTION")
    trust_line = trust_sub.add_parser("line", help="the exact line to paste, for one login")
    trust_line.add_argument("login", help="the person's GitHub login")
    trust_line.add_argument(
        "--level",
        required=True,
        metavar="N",
        help="1 asker, 2 maintainer, 3 operator (the name works too); no default",
    )
    trust_line.add_argument(
        "--no-vouch",
        action="store_true",
        dest="no_vouch",
        help="the association route, for somebody already invited to this repository",
    )
    trust_sub.add_parser("show", help="the trust file as the gate reads it")

    sub.add_parser(
        "relabel", help="migrate open issues from the harness:* labels to stage:/kind:/via:"
    )

    ledger = sub.add_parser("ledger", help="print the window, the calls and the measured usage")
    ledger.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    ledger.add_argument(
        "--rebuild",
        action="store_true",
        help="regenerate the ledger from SELF_REPO issue comments, then save it",
    )

    sub.add_parser("sync-fork", help="fast-forward the fork from upstream; loud on divergence")

    local_loop = sub.add_parser("local-loop", help="container loop: dispatch, run, sleep")
    local_loop.add_argument("--once", action="store_true", help="run one unit and exit")
    local_loop.add_argument(
        "--loop-seconds",
        type=int,
        default=DEFAULT_LOOP_SECONDS,
        dest="loop_seconds",
        metavar="N",
        help=f"sleep between units (default {DEFAULT_LOOP_SECONDS})",
    )
    local_loop.add_argument(
        "--work",
        default=None,
        metavar="PATH",
        help="directory holding HEARTBEAT and STOP (default: cwd)",
    )

    return parser


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


def _configure_logging(verbose: bool) -> None:
    LOG.setLevel(logging.DEBUG if verbose else logging.INFO)
    if not LOG.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        LOG.addHandler(handler)
    for handler in LOG.handlers:
        handler.setLevel(logging.DEBUG if verbose else logging.INFO)


def _env_path(args: argparse.Namespace) -> Path:
    if getattr(args, "config", None):
        return Path(args.config)
    return Path.cwd() / ".env"


def _repo_root(args: argparse.Namespace, config=None) -> Path:
    """The directory holding .env, .harness/ and state/ (from the config, else --config or cwd)."""
    if config is not None:
        return Path(config.repo_root)
    if getattr(args, "config", None):
        return Path(args.config).resolve().parent
    return Path.cwd()


def _load(args: argparse.Namespace):
    return load_config(Path(args.config) if getattr(args, "config", None) else None)


def _context(config, args: argparse.Namespace, *, run_id: str):
    """build_context plus the global ``--dry-run`` switch on the GitHub client."""
    ctx = build_context(config, run_id=run_id)
    if getattr(args, "dry_run", False):
        ctx.gh.dry_run = True
    return ctx


def _save_ledger(ctx) -> None:
    """Persist the ledger; a failure to save must never mask the error that got us here."""
    try:
        ctx.save_ledger()
    except HarnessError as exc:
        LOG.warning("ledger not saved: %s", exc)


def _wants_json(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "json", False))


def _emit(payload: object, text: str, args: argparse.Namespace) -> None:
    if _wants_json(args):
        print(json.dumps(payload, indent=2, sort_keys=False))
    else:
        print(text)


def _parse_hhmm(value: str) -> dtime:
    try:
        parsed = datetime.strptime(value.strip(), "%H:%M")
    except ValueError as exc:
        raise HarnessError(f"--until expects HH:MM, got {value!r}") from exc
    return dtime(hour=parsed.hour, minute=parsed.minute)


def _past_until(until: dtime | None, ctx) -> bool:
    """True once the local wall clock has reached the deadline. No new stage may start."""
    if until is None:
        return False
    local_now = ctx.clock.now().astimezone()
    return local_now.time() >= until


def _carry_issue(ledger) -> int | None:
    """The item the ledger is carrying across a usage stop, if any (D3). ``None`` when the
    ledger has never carried anything, or predates the carry slot."""
    getter = getattr(ledger, "carry_issue", None)
    if getter is None:
        return None
    value = getter()
    return int(value) if value is not None else None


def _window_text(config) -> str:
    """The run window as the dispatcher names it in a reason: ``mon 08:00-tue 20:00 UTC`` or
    ``daily 11:00-15:00 UTC`` (B411)."""
    label = run_window_label(config)
    return f"{label} UTC" if label else "always open"


def _is_carried(config, item, carry: int | None) -> bool:
    """True when a handoff parked the item, so it resumes instead of starting over (B215).
    The ledger still carries it, or its run directory holds the handoff note."""
    if not getattr(item, "branch_name", ""):
        return False
    if carry is not None and int(carry) == int(item.id):
        return True
    return (Path(config.runs_dir) / f"item-{item.id}" / deliver_stage.HANDOFF_NAME).exists()


def _package(ctx, item_id: int, lease: Lease) -> Path:
    """The review package for one item.

    A resumed item is already ``packaged`` when ``continue`` hands it back (B215), and
    ``packaged -> packaged`` is not a legal transition, so the artifact is built directly.
    Every other item goes through the package stage unchanged.
    """
    item = ctx.store.get_work_item(item_id)
    if item is not None and item.state == "packaged":
        path = build_package(ctx, item_id, lease)
        ctx.store.append_event(item_id, "info", f"packaged into {path}")
        ctx.record_decision(
            f"built the review package for the resumed item {item_id} at {path}; the item was "
            "already packaged by continue"
        )
        return path
    return STAGES["package"](ctx, item_id, lease)


def _hand_off(ctx, item_id: int, exc: HarnessError) -> dict:
    """Park the item, say so, and let the run end cleanly (B212)."""
    reason = str(exc) or exc.__class__.__name__
    record = {"item": int(item_id), "reason": reason, "handoff": None}
    try:
        path = deliver_stage.handoff(ctx, item_id, reason=reason)
    except HarnessError as inner:
        LOG.warning("item %s could not be handed off: %s", item_id, inner)
        print(f"{reason}; item {item_id} could not be handed off: {inner}")
        return record
    record["handoff"] = str(path)
    print(f"{reason}; item {item_id} handed off, see {path}")
    return record


def _priority_refusal(ctx, config, item) -> str | None:
    """Why the priority gate would refuse implementing this item now, or ``None`` (D81).

    The question ``run_model`` asks at the item's first call. `implement` has no class of its
    own, so the item's `via:` decides it.
    """
    cls = priority.class_of("implement", via=priority.via_of(item))
    return priority.admit(
        cls, store=ctx.store, ledger=ctx.ledger, config=config, now=ctx.clock.now()
    )


def _lease_for(ctx, item) -> Lease:
    run_id = f"item-{item.id}"
    return Lease(
        run_id=run_id,
        path=ctx.config.runs_dir / run_id / "clone",
        base_sha=item.base_sha or "",
        branch=item.branch_name or "",
    )


def _require_item(ctx, item_id: int):
    item = ctx.store.get_work_item(item_id)
    if item is None:
        raise HarnessError(f"no work item {item_id}")
    return item


def _read_env_raw(path: Path) -> dict[str, str]:
    """KEY=VALUE lines of a .env, for doctor's per-key report when the config cannot load."""
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _read_knob_overrides(root: Path) -> dict[str, str]:
    """`.harness/config.json` values as strings, for the same report. Unparseable → nothing."""
    path = root / ".harness" / "config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): "" if value is None else str(value) for key, value in data.items()}


# --------------------------------------------------------------------------------------
# init
# --------------------------------------------------------------------------------------


def _ensure_labels(ctx, config) -> dict:
    """Create every missing `stage:`, `kind:` and `via:` label in SELF_REPO. Idempotent."""
    if not ctx.gh.can_write:
        return {
            "created": [],
            "existing": [],
            "skipped": "no write credential; labels not created",
        }
    listing = ctx.gh.get(f"/repos/{config.self_repo}/labels?per_page=100")
    existing = {str(row.get("name", "")) for row in listing if isinstance(row, dict)}
    # Each label is created with its colour and description (B268).
    created: list[str] = []
    for name, (colour, description) in LABEL_SPECS.items():
        if name in existing:
            continue
        ctx.gh.create_label(
            config.self_repo, name=name, color=colour, description=description
        )
        created.append(name)
    return {
        "created": created,
        "existing": sorted(existing & set(LABEL_SPECS)),
        "skipped": None,
    }


def cmd_init(args: argparse.Namespace) -> int:
    env_path = _env_path(args)
    example = env_path.parent / ".env.example"
    created_env = False
    if env_path.exists():
        # B66: never overwrite an existing .env.
        LOG.debug("init: %s already exists, leaving it alone", env_path)
    elif example.exists():
        # No config exists yet, so no context has set the write roots (I-8). Admit only the
        # file init is about to create; build_context below replaces the roots.
        set_write_roots([env_path])
        guarded_write(env_path, example.read_text(encoding="utf-8"))
        created_env = True
    else:
        raise ConfigError(f"no {env_path} and no {example} to copy from")

    config = load_config(env_path)
    config.runs_dir.mkdir(parents=True, exist_ok=True)
    config.packages_dir.mkdir(parents=True, exist_ok=True)
    if str(config.db_path) != ":memory:":
        config.db_path.parent.mkdir(parents=True, exist_ok=True)

    ctx = _context(config, args, run_id="init")
    ctx.store.migrate()

    payload = {
        "env": str(env_path),
        "env_created": created_env,
        "runs_dir": str(config.runs_dir),
        "packages_dir": str(config.packages_dir),
        "db_path": str(config.db_path),
    }
    suffix = "  (created from .env.example)" if created_env else "  (kept)"
    lines = [
        f"env          {env_path}{suffix}",
        f"runs_dir     {config.runs_dir}",
        f"packages_dir {config.packages_dir}",
        f"db           {config.db_path}",
    ]

    if getattr(args, "labels", False):
        labels = _ensure_labels(ctx, config)
        payload["labels"] = labels
        if labels["skipped"]:
            lines.append(f"labels       {labels['skipped']}")
        else:
            lines.append(
                f"labels       {len(labels['created'])} created, "
                f"{len(labels['existing'])} already present in {config.self_repo}"
            )
            lines.extend(f"  + {name}" for name in labels["created"])

    lines.append("initialised")
    _emit(payload, "\n".join(lines), args)
    return EXIT_OK


# --------------------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------------------


def _probe_claude_version() -> tuple[str | None, str]:
    claude = WHICH("claude")
    if claude is None:
        return None, "claude not on PATH"
    try:
        # The resolved path: on Windows ``claude`` is a .CMD shim that CreateProcess cannot
        # find by bare name.
        proc = RUN(
            [claude, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            shell=False,
        )
    except Exception as exc:
        # Degrade and report instead of crashing.
        return None, f"claude --version failed: {exc}"
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if getattr(proc, "returncode", 1) != 0:
        return None, f"claude --version exited {proc.returncode}: {out[:200]}"
    return (out.splitlines()[0].strip() if out else ""), "ok"


def _probe_max_turns() -> tuple[bool, str]:
    """--max-turns is accepted but undocumented, so doctor probes for it (B70)."""
    claude = WHICH("claude")
    if claude is None:
        return False, "claude not on PATH; --max-turns unprobed"
    try:
        proc = RUN(
            [claude, *MAX_TURNS_PROBE_ARGV[1:]],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            shell=False,
        )
    except Exception as exc:
        return False, f"probe failed to run: {exc}"
    combined = ((proc.stdout or "") + (proc.stderr or "")).lower()
    if "unknown option" in combined:
        return False, "claude rejects --max-turns (unknown option)"
    return True, "claude accepts --max-turns"


def _doctor_config_keys(
    args: argparse.Namespace, config, config_error: str | None, problems: list[str]
) -> dict[str, str | None]:
    """One entry per CONFIG_KEYS key, from the config or, if it failed to load, the raw files."""
    keys: dict[str, str | None] = {}
    if config is not None:
        for key, attr in CONFIG_KEYS:
            value = getattr(config, attr, None)
            keys[key] = "" if value is None else str(value)
        return keys
    root = _repo_root(args)
    raw = {**_read_env_raw(_env_path(args)), **_read_knob_overrides(root)}
    error_text = config_error or ""
    for key, _attr in CONFIG_KEYS:
        if key not in raw:
            keys[key] = None
            problems.append(f"missing config key: {key}")
            continue
        keys[key] = raw[key]
        # Whole-word match, so a typo'd MAX_SUBISSUESS does not also indict MAX_SUBISSUES.
        if re.search(rf"\b{re.escape(key)}\b", error_text):
            problems.append(f"config key invalid or out of range: {key}")
    return keys


def _doctor_pin(root: Path, problems: list[str]) -> str:
    pin_path = root / ".harness" / "PIN"
    if not pin_path.exists():
        return "absent"
    try:
        verify_pin.check(root)
    except PinMismatch as exc:
        problems.append(f".harness/PIN mismatch: {exc}")
        return f"MISMATCH - {exc}"
    except HarnessError as exc:
        problems.append(f".harness/PIN check failed: {exc}")
        return f"error - {exc}"
    return "ok"


def cmd_doctor(args: argparse.Namespace) -> int:
    problems: list[str] = []
    #: Findings that do not stop the harness. Kept apart from `problems` because a problem
    #: makes doctor exit 3, and that exit code gates the spending workflows.
    warnings: list[str] = []

    binaries: dict[str, str | None] = {}
    for name in REQUIRED_BINARIES:
        found = WHICH(name)
        binaries[name] = found
        if found is None:
            problems.append(f"missing binary: {name}")

    claude_version, version_detail = _probe_claude_version()
    if claude_version is None:
        problems.append(f"claude version unknown: {version_detail}")

    max_turns_ok, max_turns_detail = _probe_max_turns()
    if not max_turns_ok:
        problems.append(f"--max-turns probe failed: {max_turns_detail}")

    config = None
    config_error: str | None = None
    try:
        config = _load(args)
    except HarnessError as exc:
        config_error = str(exc)
        problems.append(f"config invalid: {exc}")
    if config is not None:
        # A stale line in the operator's own .env warns rather than stopping anything: a problem
        # exits 3, and that exit code gates the spending workflows (D74).
        for key, where in config_mod.retired_keys_seen():
            warnings.append(
                f"retired config key ignored: {key} (in {where}) -- D74 removed it; delete it"
            )

    disk: dict[str, object] = {}
    halt_present = False
    if config is not None:
        probe_dir = config.runs_dir if config.runs_dir.exists() else Path.cwd()
        try:
            usage = shutil.disk_usage(str(probe_dir))
            free_gb = usage.free / (1024**3)
            disk = {
                "path": str(probe_dir),
                "free_gb": round(free_gb, 2),
                "min_free_gb": config.min_free_disk_gb,
            }
            if free_gb < config.min_free_disk_gb:
                problems.append(
                    f"free disk {free_gb:.2f} GB below min_free_disk_gb {config.min_free_disk_gb}"
                )
        except OSError as exc:
            disk = {"path": str(probe_dir), "error": str(exc)}
            problems.append(f"disk check failed: {exc}")

        halt_present = halted(config.halt_file)
        if halt_present:
            problems.append(f"halt file present: {config.halt_file}")

    # Config keys, .harness/HALT, .harness/PIN and the trust file.
    root = _repo_root(args, config)
    config_keys = _doctor_config_keys(args, config, config_error, problems)
    repo_halt_present = repo_halted(root)
    if repo_halt_present:
        problems.append(f".harness/HALT present under {root}")
    pin_state = _doctor_pin(root, problems)
    trust_path = config.trust_file if config is not None else root / DEFAULT_TRUST_FILE
    trusted = load_trust(Path(trust_path))

    payload = {
        "harness_version": __version__,
        "ok": not problems,
        "binaries": dict(binaries),
        "claude_version": claude_version,
        "claude_version_detail": version_detail,
        "max_turns_probe": {"accepted": max_turns_ok, "detail": max_turns_detail},
        "disk": disk,
        "halt_present": halt_present,
        "repo_halt_present": repo_halt_present,
        "config": {
            "valid": config_error is None,
            "error": config_error,
            "backend": None if config is None else config.backend,
            "repo": None if config is None else config.repo,
            "permission_tier": None if config is None else config.permission_tier,
        },
        "config_keys": config_keys,
        "pin": pin_state,
        "trust": {"path": str(trust_path), "handles": len(trusted)},
        "problems": problems,
    }

    lines = [f"harness {__version__}"]
    for name in REQUIRED_BINARIES:
        found = binaries[name]
        state = f"ok  {found}" if found else "MISSING"
        lines.append(f"  {name:<6} {state}")
    shown_version = claude_version if claude_version else "unknown"
    lines.append(f"  claude version: {shown_version} ({version_detail})")
    probe_word = "accepted" if max_turns_ok else "FAILED"
    lines.append(f"  --max-turns probe: {probe_word} ({max_turns_detail})")
    if disk:
        if "error" in disk:
            lines.append(f"  disk: error {disk['error']}")
        else:
            lines.append(f"  disk: {disk['free_gb']} GB free (min {disk['min_free_gb']})")
    lines.append(f"  halt file: {'PRESENT' if halt_present else 'absent'}")
    lines.append(f"  .harness/HALT: {'PRESENT' if repo_halt_present else 'absent'}")
    config_word = "ok" if config_error is None else f"INVALID - {config_error}"
    lines.append(f"  config: {config_word}")
    lines.append("  config keys:")
    for key, _attr in CONFIG_KEYS:
        value = config_keys.get(key)
        shown = "MISSING" if value is None else (value if value != "" else "(empty)")
        lines.append(f"    {key:<22} {shown}")
    lines.append(f"  .harness/PIN: {pin_state}")
    by_level = ", ".join(
        f"{trust_mod.LEVEL_NAMES.get(lvl, lvl)} {sorted(h for h, l in trusted.levels.items() if l == lvl)}"
        for lvl in sorted({l for l in trusted.levels.values()}, reverse=True)
    )
    lines.append(f"  trust file: {len(trusted)} handle(s) ({trust_path})")
    if by_level:
        lines.append(f"    {by_level}")
    for bad, what in trust_mod.refusals(trusted):
        # Each line that grants nothing is named with the reason that says which fix it needs
        # (B269). A warning: the parser grants nothing either way, and a problem would exit 3,
        # which stops discover.yml, feedback.yml and implement.yml under `set -e`.
        warnings.append(f"trust file line {what} and was refused: {bad!r}")
    for repeated in getattr(trusted, "duplicated", ()):
        warnings.append(
            f"@{repeated} is named by more than one line in the trust file. The highest level "
            "wins, so a line added to LOWER it does nothing at all -- change its one line "
            "instead."
        )
    if getattr(trusted, "implicit", ()):  # a level-less handle reads as level 1; say so
        named = ", ".join(f"@{h}" for h in trusted.implicit)
        lines.append(
            f"    no level given for {named}; read as level {trust_mod.DEFAULT_LEVEL} "
            f"({trust_mod.LEVEL_NAMES[trust_mod.DEFAULT_LEVEL]})"
        )
    vouched = dict(getattr(trusted, "vouched", {}) or {})
    if vouched:
        named = ", ".join(f"@{h} = account {i}" for h, i in sorted(vouched.items()))
        lines.append(f"    vouched for one account (D68; association not required): {named}")
    # The handles that depend on the association half, listed on every run: a comment refused
    # by that half is ignored without a reply (D69).
    association_route = sorted(h for h in trusted.levels if h not in vouched)
    payload["trust"]["association_route"] = association_route
    if association_route:
        named = ", ".join(f"@{h}" for h in association_route)
        lines.append(
            f"    heard only where GitHub reports them OWNER/MEMBER/COLLABORATOR: {named}"
        )
    # A trust-file line alone grants nothing: GitHub must also report the commenter as OWNER,
    # MEMBER or COLLABORATOR, and a refused commenter gets no reply (B131). A vouched handle
    # does not need that half, so it is left out.
    stranded = tuple(
        h for h in _doctor_trust_access(config, args, trusted, payload) if h.lower() not in vouched
    )
    if "without_access" in payload["trust"]:
        payload["trust"]["without_access"] = list(stranded)
    if association_route and not payload["trust"].get("access_checked"):
        # The collaborators endpoint needs push access, so tier 0 cannot check. Printing nothing
        # would read as "checked, nobody is stranded".
        lines.append(
            "    could not check who has access here (that read needs push access), so the "
            "association half above is unverified"
        )
    warnings.extend(_doctor_vouches(config, args, trusted, payload))
    if stranded:
        named = ", ".join(f"@{h}" for h in stranded)
        # A warning: it silences only these people, and a problem would stop every workflow
        # that gates on doctor.
        warnings.append(
            f"no access to {config.self_repo}: {named}. The association half of the gate is "
            f"per-repository, so their commands are ignored ON {config.self_repo} -- the inbox, "
            "proposal pull requests and work items. If GitHub reports them as a member or "
            f"collaborator on {config.upstream_repo} their commands still work THERE, on "
            "product issues and delivery pull requests. Invite them here only if you want them "
            "to steer the harness's own threads too."
        )
    # `sweep` finds product-repository comments through the notifications feed, which needs the
    # `notifications` scope. A warning: the inbox is polled directly and keeps working.
    feed = _doctor_notifications(config, args, payload)
    if feed:
        warnings.append(feed)
    # What `/harness status` reports about Actions, checked before a run rather than discovered
    # in a reply (D79). A warning, never a problem.
    actions = _doctor_actions(config, args, payload)
    if actions:
        warnings.append(actions)
    # What the token holds, against what it is expected to hold (B305).
    warnings.extend(_doctor_token_scopes(config, args, payload, feed_unreadable=bool(feed)))
    scopes = payload.get("token_scopes") or {}
    if scopes.get("checked"):
        lines.append(
            "  token scopes: "
            + (", ".join(scopes["granted"]) or "(none)")
            + f" (expected {', '.join(scopes['expected'])})"
        )
    payload["warnings"] = list(warnings)
    if warnings:
        lines.append("warnings (the harness still runs):")
        lines.extend(f"  - {w}" for w in warnings)
    if problems:
        lines.append("degraded:")
        lines.extend(f"  - {p}" for p in problems)
    elif not warnings:
        lines.append("all checks passed")

    _emit(payload, "\n".join(lines), args)
    return EXIT_DEGRADED if problems else EXIT_OK


def _doctor_notifications(config, args, payload) -> str:
    """Empty when the notifications feed reads; otherwise the warning to file.

    Never a problem: without the feed only mentions on untouched product issues are missed,
    because the inbox is polled directly.
    """
    payload["notifications"] = {"readable": None}
    if config is None or getattr(config, "permission_tier", 0) < 2:
        return ""  # tier 0 has no token; "I could not check" is not a finding
    try:
        ctx = _context(config, args, run_id="doctor")
        # Bounded to now: whether the feed answers is the question, and an unbounded read
        # would page through every unread thread the account has.
        ctx.gh.notifications(iso(ctx.clock.now()))
    except GitHubError as exc:
        payload["notifications"] = {"readable": False, "error": str(exc)[:200]}
        return (
            "the notifications feed is not readable, so a comment on a product issue the "
            "machine account has never touched will not be seen. The inbox issue is polled "
            "directly and still works, as does every thread the harness has already written to. "
            "Add the `notifications` scope to the machine PAT to close the gap."
        )
    except Exception:  # pragma: no cover - a diagnostic must not fail the diagnosis
        return ""
    payload["notifications"] = {"readable": True}
    return ""


def _doctor_actions(config, args, payload) -> str:
    """Empty when the Actions run list reads; otherwise the warning to file (D79).

    Never a problem: a problem exits 3, and that exit code gates the three spending workflows.
    Losing a status line must not stop the fleet, which is the rule D74/B305 already applies to
    the scope check and the notifications feed.
    """
    payload["actions"] = {"readable": None}
    if config is None or getattr(config, "permission_tier", 0) < 2:
        return ""  # tier 0 reads this unauthenticated on demand; nothing to check up front
    try:
        ctx = _context(config, args, run_id="doctor")
        rows, error, _ = _actions_rows(ctx.gh, str(getattr(config, "self_repo", "") or ""))
    except Exception:  # pragma: no cover - a diagnostic must not fail the diagnosis
        return ""
    if error:
        payload["actions"] = {"readable": False, "error": error[:200]}
        return (
            f"the Actions run list is not readable ({error}), so `/harness status` cannot say "
            "what is running or queued. Everything else still works, and a public repository "
            "needs no extra scope for this read."
        )
    payload["actions"] = {"readable": True, "runs": len(rows)}
    return ""


#: The classic scopes the machine PAT is expected to carry, each with what fails without it.
#: Doctor warns, and does not degrade, when one is missing or another is present (B305).
EXPECTED_TOKEN_SCOPES: dict[str, str] = {
    "public_repo": "it cannot push to the fork or open a pull request, and nothing is delivered",
    "notifications": (
        "the notifications feed is closed to it, and a mention on a product issue the machine "
        "account has never touched goes unseen"
    ),
    "workflow": (
        "`harness sync-fork` cannot fast-forward the fork past an upstream commit that changes "
        "`.github/workflows/`, so the fork falls behind and nothing can be delivered"
    ),
}


#: A classic scope that GitHub grants as part of a broader one. The broader scope satisfies the
#: expectation and is still reported as unexpected (D81).
_SCOPE_PARENT: dict[str, str] = {"public_repo": "repo"}


def _doctor_token_scopes(config, args, payload, *, feed_unreadable: bool = False) -> list[str]:
    """The machine PAT's scopes, read off `X-OAuth-Scopes`; returns the warnings to file (B305).

    The token holds `workflow` (D67), so GitHub does not refuse a push that edits a workflow.
    Checking before every spending run catches a token that gains `delete_repo` or `admin:org`.
    Warnings only, because a problem exits 3 and stops the workflows that gate on doctor.
    """
    payload["token_scopes"] = {"checked": False}
    if config is None or getattr(config, "permission_tier", 0) < 2:
        return []  # tier 0 holds no token; "I could not check" is not a finding
    try:
        ctx = _context(config, args, run_id="doctor")
        if not ctx.gh.can_write:
            return []
        scopes = ctx.gh.token_scopes()
    except HarnessError as exc:
        payload["token_scopes"] = {"checked": False, "error": str(exc)[:200]}
        return [f"could not read the machine PAT's scopes, so none was checked: {exc}"[:400]]
    except Exception:  # pragma: no cover - a diagnostic must not fail the diagnosis
        return []
    expected = sorted(EXPECTED_TOKEN_SCOPES)
    if scopes is None:
        payload["token_scopes"] = {"checked": False, "error": "no X-OAuth-Scopes header"}
        return [
            "GitHub sent no X-OAuth-Scopes header for the machine PAT, which is what a "
            "fine-grained token does, so doctor cannot confirm it holds "
            + ", ".join(f"`{s}`" for s in expected)
            + " and nothing more (D67 expects a classic token)"
        ]
    missing = [
        scope
        for scope in expected
        if scope not in scopes and _SCOPE_PARENT.get(scope) not in scopes
    ]
    extra = sorted(set(scopes) - set(expected))
    payload["token_scopes"] = {
        "checked": True,
        "granted": list(scopes),
        "expected": expected,
        "missing": missing,
        "unexpected": extra,
    }
    warnings = [
        f"the machine PAT lacks the `{scope}` scope, so {EXPECTED_TOKEN_SCOPES[scope]}"
        for scope in missing
        # The feed probe has already said so, and what still works without it.
        if not (scope == "notifications" and feed_unreadable)
    ]
    if extra:
        warnings.append(
            "the machine PAT carries "
            + ", ".join(f"`{s}`" for s in extra)
            + " beyond the expected "
            + ", ".join(f"`{s}`" for s in expected)
            + ". Nothing the harness does needs them, and a leaked token is worth more with "
            "them; drop them at the next rotation (docs/OPERATIONS.md)"
        )
    return warnings


def _doctor_vouches(config, args, trusted, payload) -> list[str]:
    """Each vouched id against the account that holds the login today. Warnings only (D68).

    A login that changed hands is already refused, because the new holder's id does not match.
    This catches a person who renamed and is now refused under the new login. A lookup that
    fails (tier 0's rate ceiling, a network error) reports nothing.
    """
    vouched = dict(getattr(trusted, "vouched", {}) or {})
    report = {h: {"id": i, "checked": False} for h, i in sorted(vouched.items())}
    payload["trust"]["vouched"] = report
    if not vouched or config is None:
        return []
    import urllib.parse

    try:
        gh = _context(config, args, run_id="doctor").gh
    except Exception:  # pragma: no cover - a check that cannot run must not break doctor
        return []
    found: list[str] = []
    for handle, pinned in sorted(vouched.items()):
        try:
            data = gh.get(f"/users/{urllib.parse.quote(handle, safe='')}")
        except GitHubError as exc:
            if "returned 404 " not in str(exc):
                continue
            report[handle].update(checked=True, actual=None)
            found.append(
                f"trust.txt vouches for @{handle} (account {pinned}), but GitHub has no account "
                "by that login now -- renamed or deleted. Nothing is admitted under that line "
                "until it names the account's current login (D68)."
            )
            continue
        except Exception:  # rate ceiling, network: not a finding
            continue
        actual = trust_mod.parse_user_id(data.get("id")) if isinstance(data, dict) else None
        kind = str((data.get("type") if isinstance(data, dict) else "") or "").strip()
        if actual is None:
            continue
        report[handle].update(checked=True, actual=actual, type=kind or None)
        if actual != pinned:
            found.append(
                f"trust.txt vouches for @{handle} as account {pinned}, but @{handle} is account "
                f"{actual} today. The vouch binds the account, not the name, so whoever holds "
                f"@{handle} now is refused. If the person renamed, put their new login on the "
                "line; if the login changed hands, remove it (D68)."
            )
        elif kind and kind != ACCOUNT_TYPE_PERSON:
            # The id matches but the account is not a person, so the line admits nobody.
            # `harness trust line` refuses to print one; this catches a hand-written line.
            found.append(
                f"trust.txt vouches for @{handle} as account {pinned}, which GitHub reports as "
                f"a {kind} account rather than a person. Only a person authors a comment, so "
                "nothing is ever admitted under that line and nobody is told (D69)."
            )
    return found


def _doctor_trust_access(config, args, trusted, payload) -> tuple[str, ...]:
    """Trusted handles GitHub would refuse anyway; () when there are none or it cannot be told.

    Never a problem: the collaborators endpoint needs push access, so tier 0 has no answer.
    """
    payload["trust"]["access_checked"] = False
    if config is None or not getattr(config, "self_repo", ""):
        return ()
    try:
        ctx = _context(config, args, run_id="doctor")
        stranded = Identity(config, ctx.gh).trusted_without_access(trusted)
    except HarnessError:
        return ()
    except Exception:  # pragma: no cover - a check that cannot run must not break doctor
        return ()
    if stranded is None:
        return ()
    payload["trust"]["access_checked"] = True
    payload["trust"]["without_access"] = list(stranded)
    return stranded


# --------------------------------------------------------------------------------------
# setup
# --------------------------------------------------------------------------------------


def cmd_setup(args: argparse.Namespace) -> int:
    config = _load(args)
    ctx = _context(config, args, run_id="setup")
    identity = Identity(config, ctx.gh)
    readiness = identity.assess(args.tier)
    content = identity.render_human_doc(readiness)
    target = Path.cwd() / "HUMAN.md"
    write_human_doc(target, content)

    outstanding = [p for p in readiness.prerequisites if not p.satisfied]
    payload = {
        "human_md": str(target),
        "current_tier": readiness.current_tier,
        "target_tier": readiness.target_tier,
        "ready": readiness.ready,
        "outstanding": [
            {"id": p.id, "title": p.title, "actor": p.actor, "detail": p.detail}
            for p in outstanding
        ],
    }
    lines = [
        f"wrote {target}",
        f"tier {readiness.current_tier} -> {readiness.target_tier}",
    ]
    if outstanding:
        lines.append(f"{len(outstanding)} prerequisite(s) outstanding:")
        lines.extend(f"  - [{p.actor}] {p.title}" for p in outstanding)
    else:
        lines.append("all prerequisites satisfied")
    _emit(payload, "\n".join(lines), args)
    return EXIT_OK if readiness.ready else EXIT_SETUP_OUTSTANDING


# --------------------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------------------


def _as_pct(fraction: float | None) -> float | None:
    """A utilization fraction as a percentage, or None where this window has no live reading."""
    return None if fraction is None else float(fraction) * 100.0


def cmd_status(args: argparse.Namespace) -> int:
    config = _load(args)
    ctx = _context(config, args, run_id="status")

    queue: dict[str, int] = {state: 0 for state in STATES}
    for item in ctx.store.list_work_items():
        queue[item.state] = queue.get(item.state, 0) + 1

    now = ctx.clock.now()
    usage = {
        "weekly_pct": _as_pct(ctx.ledger.weekly_utilization(now)),
        "session_pct": _as_pct(ctx.ledger.session_utilization(now)),
        "rate_limited_until": dict(ctx.ledger.window).get("rate_limited_until"),
    }

    in_flight = [dataclasses.asdict(r) for r in ctx.store.list_stage_runs(status="running")]

    halt = ctx.ledger.halt_request()
    # What Actions is actually doing, so "nothing is happening" can be told from "it is queued
    # behind the lock" (D79).
    runs, actions_error, actions_cut = _actions_rows(ctx.gh, config.self_repo)
    payload = {
        "queue": queue,
        "usage": usage,
        "in_flight": in_flight,
        "halt": halt,
        "block": ctx.ledger.block_grant(),
        "actions": {"runs": runs, "error": actions_error or None, "truncated": actions_cut},
    }

    # The halt comes first, so what follows is not read as a running harness.
    lines = _halt_lines(ctx.ledger)
    standing = links.block_line(ctx.ledger, now)
    if standing:
        lines.append(standing)
    lines.append("queue:")
    lines.extend(f"  {state:<12} {queue[state]}" for state in STATES)
    lines.extend(_usage_lines(ctx.ledger, config, now))
    lines.extend(links.actions_lines(runs, now, error=actions_error, truncated=actions_cut))
    lines.append(f"in flight: {len(in_flight)}")
    for row in in_flight:
        lines.append(
            f"  item {row.get('work_item_id')} {row.get('stage')} since {row.get('started_at')}"
        )

    _emit(payload, "\n".join(lines), args)
    return EXIT_OK


# --------------------------------------------------------------------------------------
# discover / propose / approve
# --------------------------------------------------------------------------------------


def cmd_discover(args: argparse.Namespace) -> int:
    check_repo_halt(_repo_root(args))
    config = _load(args)
    ctx = _context(config, args, run_id="discover")
    try:
        ids = STAGES["discover"](
            ctx,
            mode=args.mode,
            target=args.target,
            lens=args.lens,
            ignore_allowlist=args.ignore_allowlist,
        )
    finally:
        _save_ledger(ctx)
    if _wants_json(args):
        print(json.dumps({"created": list(ids)}, indent=2))
    else:
        for item_id in ids:
            print(item_id)
    return EXIT_OK


def cmd_propose(args: argparse.Namespace) -> int:
    check_repo_halt(_repo_root(args))
    config = _load(args)
    ctx = _context(config, args, run_id=f"item-{args.item_id}")
    _require_item(ctx, args.item_id)
    try:
        spec_path = STAGES["propose"](ctx, args.item_id)
    finally:
        _save_ledger(ctx)
    _emit({"item_id": args.item_id, "spec_path": str(spec_path)}, str(spec_path), args)
    return EXIT_OK


def _proposal_on_main(directory: Path, item_id: int) -> str | None:
    """The committed proposal file for `item_id`, by name, or None (D46)."""
    if not directory.is_dir():
        return None
    for path in sorted(directory.iterdir()):
        if not path.is_file():
            continue
        match = PROPOSAL_FILE_RE.match(path.name)
        if match and int(match.group(1)) == int(item_id):
            return path.name
    return None


def _approve_merged(args: argparse.Namespace) -> int:
    """Approve every `proposed` item whose proposal file is on main (D78).

    Bounded by the queue rather than by `proposals/`, which only grows: one label query under
    the GitHub store, and only `proposed -> approved` is ever made. What keeps a stopped item
    from being resurrected is the state machine together with the fact that nothing puts an
    item back into `proposed`: an item stopped after its proposal merged is `blocked` or
    `abandoned`, so it is not `proposed` and is left alone.
    """
    config = _load(args)
    ctx = _context(config, args, run_id="approve")
    directory = Path(config.repo_root) / "proposals"
    approved: list[int] = []
    skipped: dict[str, str] = {}
    failed: dict[str, str] = {}
    for item in ctx.store.list_work_items(state="proposed"):
        item_id = int(item.id)
        name = _proposal_on_main(directory, item_id)
        if name is None:
            skipped[str(item_id)] = "no proposal file on main"
            continue
        try:
            ctx.store.transition(
                item_id, "approved", reason=f"gate 1: proposals/{name} is on main"
            )
        except HarnessError as exc:
            # One item that cannot move must not strand the others: this pass is what recovers a
            # burst, and stopping at the first failure would lose the rest of it.
            LOG.warning("approve --merged: item %s did not transition: %s", item_id, exc)
            failed[str(item_id)] = str(exc)
            continue
        approved.append(item_id)
    head = f"approved {len(approved)} item(s)"
    if approved:
        head += ": " + ", ".join(f"#{n}" for n in approved)
    lines = [head]
    lines.extend(f"  skipped #{key}: {why}" for key, why in sorted(skipped.items()))
    lines.extend(f"  failed #{key}: {why}" for key, why in sorted(failed.items()))
    for key, why in sorted(failed.items()):
        # On stderr, so `--json` stdout stays parseable; Actions reads a workflow command from
        # either stream.
        print(
            f"::warning::harness approve --merged: item {key} did not transition: {why}",
            file=sys.stderr,
        )
    _emit({"approved": approved, "skipped": skipped, "failed": failed}, "\n".join(lines), args)
    # Never non-zero. This runs before `harness dispatch` and, in feedback.yml, before
    # `harness sweep`, so failing it would take the keyword surface down over one stuck item,
    # and a warning must not take the fleet down (D74/B305). The next run reconciles again.
    return EXIT_OK


def cmd_approve(args: argparse.Namespace) -> int:
    """`proposed -> approved`. With `--merged`, reconcile every proposal on main (D78).

    Merging a proposal pull request is gate 1, and the push it makes starts an implement run.
    GitHub keeps one run in progress and one pending per concurrency group, so a burst of merges
    cancels the pending ones — and the approval each carried died with it, because the old step
    read its own push diff, which exists only inside that event. Reconciling the committed files
    against item state instead makes an approval recoverable by any later run.
    """
    if getattr(args, "merged", False):
        if args.item_id is not None:
            raise HarnessError(
                "harness approve --merged takes no item id; it reconciles every proposal on main"
            )
        return _approve_merged(args)
    if args.item_id is None:
        raise HarnessError("harness approve needs an item id, or --merged")
    config = _load(args)
    ctx = _context(config, args, run_id=f"item-{args.item_id}")
    item = _require_item(ctx, args.item_id)
    if item.state != "proposed":
        raise HarnessError(f"item {args.item_id} is {item.state}; approve requires state proposed")
    reason = args.note if args.note else "approved by operator"
    ctx.store.transition(args.item_id, "approved", reason=reason)
    _emit(
        {"item_id": args.item_id, "state": "approved", "note": args.note},
        f"item {args.item_id} approved",
        args,
    )
    return EXIT_OK


# --------------------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> int:
    # The repo-level kill switch is checked before the config loads (B149).
    check_repo_halt(_repo_root(args))
    config = _load(args)
    # The halt file is checked before anything is selected, queue or no queue (B69).
    check_halt(config.halt_file)
    until = _parse_hhmm(args.until) if args.until else None

    listing_ctx = _context(config, args, run_id="run")
    # A crashed job cannot strand an item in a running state beyond three hours (B147).
    stale_before = iso(listing_ctx.clock.now() - timedelta(hours=STALE_RUNNING_HOURS))
    reset_ids = list(listing_ctx.store.reconcile_stale_running(stale_before))
    if reset_ids:
        LOG.info("run: reset %d stale running item(s): %s", len(reset_ids), reset_ids)

    carry = _carry_issue(listing_ctx.ledger)
    if args.item is not None:
        item = listing_ctx.store.get_work_item(args.item)
        if item is None:
            raise HarnessError(f"no work item {args.item}")
        if item.state != "approved":
            raise HarnessError(f"item {args.item} is {item.state}; run requires state approved")
        item_ids = [args.item]
    else:
        item_ids = [i.id for i in listing_ctx.store.list_work_items(state="approved")]
        # Outside the run window only the carried item may start (B210). `--item` bypasses the
        # window and nothing else: every stage still passes through the governor's usage stops.
        # A block opens the window here exactly as it does in the plan, and lifts nothing else
        # (D77).
        now = listing_ctx.clock.now()
        if not in_run_window(config, now) and not listing_ctx.ledger.block_open(now):
            window = _window_text(config)
            # A daily window holds the carried item back too, as in dispatcher.plan (B413).
            carry_exempt = carry is not None and not is_daily_window(config)
            item_ids = [i for i in item_ids if carry_exempt and int(i) == int(carry)]
            if not item_ids:
                _emit(
                    {
                        "ran": [],
                        "packages": [],
                        "delivered": [],
                        "resumed": [],
                        "reset": reset_ids,
                        "stopped_at_deadline": False,
                        "rate_limited_until": None,
                        "handed_off": None,
                        "waiting": {},
                    },
                    f"outside run window ({window}); nothing started",
                    args,
                )
                return EXIT_OK
            LOG.info("run: outside the run window (%s); only carried item %s starts", window, carry)

    if not item_ids:
        _emit(
            {
                "ran": [],
                "packages": [],
                "delivered": [],
                "resumed": [],
                "reset": reset_ids,
                "stopped_at_deadline": False,
                "rate_limited_until": None,
                "handed_off": None,
                "waiting": {},
            },
            "nothing approved to run",
            args,
        )
        return EXIT_OK

    ran: list[int] = []
    packages: list[str] = []
    delivered: list[str] = []
    resumed_ids: list[int] = []
    stopped_at_deadline = False
    rate_limited_until: str | None = None
    handed_off: dict | None = None
    waiting: dict[str, str] = {}
    ctx = None

    try:
        for item_id in item_ids:
            ctx = _context(config, args, run_id=f"item-{item_id}")

            try:
                # implement, or continue where a handoff stopped (B215)
                check_halt(config.halt_file)
                if _past_until(until, ctx):
                    stopped_at_deadline = True
                    break
                item = _require_item(ctx, item_id)
                resumed = _is_carried(config, item, carry)
                # Asked before the clone, as the first model call would ask: work refused there
                # has nothing to hand off, and a handoff takes the carry slot (D81). A priority
                # refusal is this item's alone; a usage stop or rate limit refuses every item.
                gate = None if resumed else _priority_refusal(ctx, config, item)
                stop = ctx.governor.refusal(item_id) if gate is None else None
                if gate is not None or stop is not None:
                    LOG.info("run: item %s waits: %s", item_id, gate or stop)
                    waiting[str(item_id)] = str(gate or stop)
                    if stop is not None:
                        break
                    continue
                if resumed:
                    LOG.debug("run: continue item %s", item_id)
                    lease = STAGES["revise"](ctx, item_id, source="continue")
                    if lease is None:
                        # The stage parked or blocked the item and said why on the issue.
                        LOG.info("run: item %s did not resume; moving on", item_id)
                        _save_ledger(ctx)
                        continue
                    resumed_ids.append(item_id)
                else:
                    LOG.debug("run: implement item %s", item_id)
                    lease = STAGES["implement"](ctx, item_id)

                # package
                check_halt(config.halt_file)
                if _past_until(until, ctx):
                    stopped_at_deadline = True
                    ran.append(item_id)
                    break
                LOG.debug("run: package item %s", item_id)
                package_dir = _package(ctx, item_id, lease)

                ran.append(item_id)
                packages.append(str(package_dir))

                # deliver: only when a write credential exists; the branch is left for the host
                # otherwise.
                if ctx.gh.can_write:
                    check_halt(config.halt_file)
                    if _past_until(until, ctx):
                        stopped_at_deadline = True
                        break
                    LOG.debug("run: deliver item %s", item_id)
                    pr_url = STAGES["deliver"](ctx, item_id)
                    if pr_url:
                        delivered.append(pr_url)
            except (BudgetExhausted, RateLimited) as exc:
                # A declined call and a rate limit are normal outcomes (B120): the item is handed
                # off with its work committed and carried in the ledger, and the run exits 0
                # without starting anything else.
                if isinstance(exc, RateLimited):
                    rate_limited_until = exc.reset_at or "unknown"
                handed_off = _hand_off(ctx, item_id, exc)
                break
            _save_ledger(ctx)
    finally:
        if ctx is not None:
            _save_ledger(ctx)

    payload = {
        "ran": ran,
        "packages": packages,
        "delivered": delivered,
        "resumed": resumed_ids,
        "reset": reset_ids,
        "stopped_at_deadline": stopped_at_deadline,
        "rate_limited_until": rate_limited_until,
        "handed_off": handed_off,
        "waiting": waiting,
    }
    lines = [f"ran {len(ran)} item(s)"]
    lines.extend(f"  {p}" for p in packages)
    lines.extend(f"  delivered {url}" for url in delivered)
    lines.extend(f"  resumed item {i} from a handoff" for i in resumed_ids)
    lines.extend(f"  item {i} waits: {why}" for i, why in waiting.items())
    if stopped_at_deadline:
        lines.append(f"stopped: --until {args.until} reached; no new stage started")
    if handed_off is not None:
        lines.append(
            f"handed off item {handed_off['item']}: {handed_off['reason']}; "
            "nothing further started"
        )
    _emit(payload, "\n".join(lines), args)
    return EXIT_OK


# --------------------------------------------------------------------------------------
# package / archive
# --------------------------------------------------------------------------------------


def cmd_package(args: argparse.Namespace) -> int:
    config = _load(args)
    ctx = _context(config, args, run_id=f"item-{args.item_id}")
    item = _require_item(ctx, args.item_id)
    lease = _lease_for(ctx, item)
    package_dir = STAGES["package"](ctx, args.item_id, lease)
    _emit({"item_id": args.item_id, "package": str(package_dir)}, str(package_dir), args)
    return EXIT_OK


def cmd_archive(args: argparse.Namespace) -> int:
    config = _load(args)
    ctx = _context(config, args, run_id=f"item-{args.item_id}")
    _require_item(ctx, args.item_id)
    dest = archive_package(ctx, args.item_id, with_transcript=bool(args.with_transcript))
    _emit(
        {
            "item_id": args.item_id,
            "archive": str(dest),
            "with_transcript": bool(args.with_transcript),
        },
        str(dest),
        args,
    )
    return EXIT_OK


# --------------------------------------------------------------------------------------
# halt / resume
# --------------------------------------------------------------------------------------


def cmd_halt(args: argparse.Namespace) -> int:
    config = _load(args)
    engage(config.halt_file)
    _emit(
        {"halt_file": str(config.halt_file), "halted": True},
        f"halted - {config.halt_file} created",
        args,
    )
    return EXIT_OK


def cmd_resume(args: argparse.Namespace) -> int:
    config = _load(args)
    disengage(config.halt_file)
    lines = [f"resumed - {config.halt_file} removed"]
    lifted = None
    if getattr(args, "commanded", False):
        # `/harness resume` lifts a commanded halt through the sweep; this lifts it when the
        # sweep itself is broken.
        ctx = _context(config, args, run_id="resume")
        lifted = ctx.ledger.halt_request()
        if lifted is not None:
            ctx.ledger.clear_halt()
            ctx.save_ledger()
            lines.append(f"commanded halt by @{lifted.get('by', 'someone')} lifted")
        else:
            lines.append("no commanded halt was set")
    _emit(
        {"halt_file": str(config.halt_file), "halted": False, "commanded_halt_lifted": lifted},
        "\n".join(lines),
        args,
    )
    return EXIT_OK


# --------------------------------------------------------------------------------------
# dispatch (B122)
# --------------------------------------------------------------------------------------


def _front_matter_depends_on(text: str) -> tuple[int, ...]:
    """`depends_on` from a proposal's YAML front matter, inline or block list (I-17)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return ()
    found: list[int] = []
    in_block = False
    for raw in lines[1:]:
        line = raw.rstrip()
        if line.strip() == "---":
            break
        if in_block:
            stripped = line.strip()
            if stripped.startswith("- "):
                token = stripped[2:].split("#", 1)[0].strip()
                if token.isdigit():
                    found.append(int(token))
                continue
            if stripped == "" or line.startswith(" "):
                continue
            break
        if line.startswith("depends_on:"):
            value = line[len("depends_on:"):].split("#", 1)[0].strip()
            if value.startswith("["):
                inner = value.strip("[]")
                for token in inner.split(","):
                    token = token.strip()
                    if token.isdigit():
                        found.append(int(token))
                break
            if value == "":
                in_block = True
                continue
            if value.isdigit():
                found.append(int(value))
            break
    return tuple(found)


def _depends_on(config, item) -> tuple[int, ...]:
    """The item's `depends_on` from its proposal under proposals/, else ()."""
    dirs = [Path(config.repo_root) / "proposals"]
    if str(config.db_path) != ":memory:":
        scratch = Path(config.db_path).parent / "proposals"
        if scratch not in dirs:
            dirs.append(scratch)
    for directory in dirs:
        if not directory.is_dir():
            continue
        matches = sorted(directory.glob(f"{int(item.id)}-*.md"))
        if not matches:
            continue
        try:
            text = matches[0].read_text(encoding="utf-8")
        except OSError:
            continue
        return _front_matter_depends_on(text)
    return ()


#: `_build_plan`'s default: ask the priority gate only when a suggested candidate exists.
_ASK = object()


def _build_plan(ctx, config, args: argparse.Namespace, suggested_refused=_ASK) -> Plan:
    """The dispatcher's plan for the approved items; ``suggested_refused`` is passed in when the
    caller has already asked ``priority.admit("suggested", ...)``."""
    items = ctx.store.list_work_items(state="approved")
    forced = set(ctx.ledger.forced())
    candidates = [
        Candidate(
            issue=int(item.id),
            depends_on=_depends_on(config, item),
            created_at=str(getattr(item, "created_at", "") or ""),
            forced=int(item.id) in forced,
            cls=priority.class_of("", via=priority.via_of(item)),
        )
        for item in items
    ]
    now = ctx.clock.now()
    if suggested_refused is _ASK:
        suggested_refused = (
            priority.admit("suggested", store=ctx.store, ledger=ctx.ledger, config=config, now=now)
            if any(c.cls == "suggested" for c in candidates)
            else None
        )
    return plan_dispatch(
        now=now,
        ledger=ctx.ledger,
        config=config,
        candidates=candidates,
        merged=ctx.store.merged_issues(),
        halted=repo_halted(_repo_root(args, config)) or halted(config.halt_file),
        suggested_refused=suggested_refused,
    )


#: What the head of the queue is waiting for, by the stage it is in. `approved` is absent
#: because the dispatcher's plan answers for that state.
_HEAD_WAIT: dict[str, str] = {
    "discovered": "waiting to be proposed; `discover` ranks and proposes the queue",
    "proposing": "a propose job is in flight",
    "proposed": "waiting for a person to merge or close the proposal (gate 1)",
    "implementing": "an implement job is in flight",
    "packaged": "waiting for delivery",
    "shipped": "waiting for a person on the upstream pull request (gate 2)",
    "revising": "a revision cycle is in flight",
    "blocked": "stopped; it needs a decision",
    "needs-human": "retries spent; the harness will not try again on its own",
}


def _head_reason(plan: Plan, rows, config) -> dict:
    """One sentence on the top of the queue: what it is, and why it is or is not starting."""
    if not rows:
        return {"item": None, "reason": "the queue is empty"}
    head = rows[0]
    number = head.item_id
    if number is not None and number in plan.start:
        return {"item": number, "reason": "starting now"}
    if number is not None and str(number) in plan.skipped:
        return {"item": number, "reason": plan.skipped[str(number)]}
    waiting = _HEAD_WAIT.get(head.note or "")
    if waiting:
        return {"item": number, "reason": waiting}
    # `approved` and anything unmapped: the dispatcher is the authority, so quote it.
    return {"item": number, "reason": plan.reason}


def cmd_dispatch(args: argparse.Namespace) -> int:
    check_repo_halt(_repo_root(args))
    config = _load(args)
    ctx = _context(config, args, run_id="dispatch")
    # Asked once: the plan skips suggested work with it and the payload reports it.
    blocked = priority.admit(
        "suggested", store=ctx.store, ledger=ctx.ledger, config=config, now=ctx.clock.now()
    )
    plan = _build_plan(ctx, config, args, suggested_refused=blocked)
    # The plan says what starts; the queue says what waits behind it and why (B292). Both go in
    # the plan's JSON document, because workflows parse this stdout as a single document.
    payload = json.loads(plan.to_json())
    rows = priority.queue(store=ctx.store, ledger=ctx.ledger)
    payload["queue"] = [
        {
            "class": row.cls,
            "rank": priority.rank(row.cls),
            "item": row.item_id,
            "label": row.label,
            "state": row.note,
            "forced": row.forced,
        }
        for row in rows
    ]
    # Why the head of the queue is or is not moving. `plan.reason` and `plan.skipped` cover only
    # `approved` candidates, and the head is often in `discovered` or `proposing`.
    payload["head"] = _head_reason(plan, rows, config)
    # Spelled out with `admitted`, since a bare `null` reason would read as "no suggestion".
    payload["suggested"] = {"admitted": blocked is None, "reason": blocked}
    # The block is reported here rather than in `Plan.reason`: a new reason literal would have to
    # be classified as stopping or proceeding and would change `discover.yml`'s `case` contract,
    # while the plan already tells the truth by starting the work (D77).
    payload["block"] = {
        "open": ctx.ledger.block_open(ctx.clock.now()),
        "grant": ctx.ledger.block_grant(),
    }
    print(json.dumps(payload, indent=2, sort_keys=False))
    return EXIT_OK


# --------------------------------------------------------------------------------------
# deliver / revise / decompose
# --------------------------------------------------------------------------------------


def cmd_deliver(args: argparse.Namespace) -> int:
    check_repo_halt(_repo_root(args))
    config = _load(args)
    check_halt(config.halt_file)
    ctx = _context(config, args, run_id=f"item-{args.item_id}")
    _require_item(ctx, args.item_id)
    try:
        pr_url = STAGES["deliver"](ctx, args.item_id)
    finally:
        _save_ledger(ctx)
    if pr_url:
        text = pr_url
    else:
        text = (
            f"item {args.item_id}: no write credential; branch left for the host "
            f"(runs/item-{args.item_id}/DELIVER.json)"
        )
    _emit({"item_id": args.item_id, "pr_url": pr_url, "delivered": bool(pr_url)}, text, args)
    return EXIT_OK


def cmd_revise(args: argparse.Namespace) -> int:
    check_repo_halt(_repo_root(args))
    config = _load(args)
    check_halt(config.halt_file)
    ctx = _context(config, args, run_id=f"item-{args.item_id}")
    _require_item(ctx, args.item_id)
    try:
        lease = STAGES["revise"](ctx, args.item_id, source=args.source, notes=args.notes or "")
    finally:
        _save_ledger(ctx)
    item = ctx.store.get_work_item(args.item_id)
    state = item.state if item is not None else "unknown"
    branch = lease.branch if lease is not None else None
    payload = {
        "item_id": args.item_id,
        "source": args.source,
        "revised": lease is not None,
        "branch": branch,
        "state": state,
    }
    if lease is not None:
        text = f"item {args.item_id} revised ({args.source}) on {branch}; state {state}"
    else:
        text = f"item {args.item_id} not revised ({args.source}); state {state}"
    _emit(payload, text, args)
    return EXIT_OK


def cmd_decompose(args: argparse.Namespace) -> int:
    check_repo_halt(_repo_root(args))
    config = _load(args)
    check_halt(config.halt_file)
    ctx = _context(config, args, run_id=f"item-{args.issue}")
    try:
        ids = list(STAGES["decompose"](ctx, args.issue))
    finally:
        _save_ledger(ctx)
    if _wants_json(args):
        print(json.dumps({"parent": args.issue, "created": ids}, indent=2))
    else:
        for item_id in ids:
            print(item_id)
    return EXIT_OK


# --------------------------------------------------------------------------------------
# sweep (B140)
# --------------------------------------------------------------------------------------


def _item_for_command(ctx, config, cmd) -> int | None:
    """Map a keyword command's thread to a work item id.

    A `product_issue` resolves through the store, never by number: the harness and product
    repositories number their issues independently (B243).
    """
    if cmd.surface == "inbox":
        # The inbox is never a work item, so no steering verb can target it (B241).
        return None
    if cmd.surface == "product_issue":
        item = ctx.store.find_by_ref(f"issue:{int(cmd.number)}")
        if item is not None:
            return int(item.id)
        # An issue a delivery filed names its work item in a marker (D83).
        try:
            issue = ctx.gh.issue(int(cmd.number))
        except (GitHubError, RateCeilingReached):
            return None
        return discover_stage.filed_for(ctx, issue)
    if cmd.surface == "issue":
        return int(cmd.number)
    repo = config.self_repo if cmd.surface == "proposal_pr" else config.upstream_repo
    pull = ctx.gh.pull(repo, cmd.number)
    head = pull.get("head") if isinstance(pull, dict) else None
    head_ref = str((head or {}).get("ref") or "")
    if cmd.surface == "proposal_pr":
        match = PROPOSE_BRANCH_RE.search(head_ref)
        return int(match.group(1)) if match else None
    if not head_ref:
        return None
    for item in ctx.store.list_work_items():
        if item.branch_name and item.branch_name == head_ref:
            return int(item.id)
    return None


#: How the item arrived, for the `via:` label (B264). Every `/harness work` is `requested`,
#: wherever it was typed. `assigned` means only that the machine account was put in the
#: Assignees box, which `discover --mode assigned` reads. `via:` feeds the priority queue.
_VIA_BY_SURFACE: dict[str, str] = {}


def _next_scheduled(now) -> str:
    """The next scheduled `feedback.yml` sweep after `now`, as an ISO timestamp."""
    # feedback.yml's cron `41 */3 * * 1-5`: minute 41 of hours 0, 3, ..., 21, Monday to Friday.
    nxt = now.replace(minute=41, second=0, microsecond=0)
    while nxt <= now or nxt.hour % 3 != 0 or nxt.weekday() > 4:
        nxt = nxt + timedelta(hours=1)
    return iso(nxt)


def _actions_rows(gh, repo: str) -> tuple[list[dict], str, bool]:
    """`(rows, error, truncated)` for the Actions section (D79).

    "Nothing is running" and "I could not look" call for opposite actions, so an empty list never
    stands in for a failure; `truncated` is the third answer, because one page is all that is read
    and a full page says nothing about the runs behind it. A client that cannot list runs at all
    is unreadable rather than idle, the rule `keywords.answered_by_ack` applies to
    `comment_reactions`. Every failure costs one line and leaves the rest whole.
    """
    reader = getattr(gh, "workflow_runs", None)
    if not callable(reader) or not repo:
        return [], "could not be read (this client cannot list workflow runs)", False
    try:
        rows = reader(repo, per_page=ACTIONS_PER_PAGE)
    except RateCeilingReached:
        return [], "not read (GitHub request ceiling reached)", False
    except HarnessError as exc:
        return [], f"could not be read ({str(exc)[:120]})", False
    except Exception:  # pragma: no cover - a status line must never fail the command it is in
        return [], "could not be read", False
    if not isinstance(rows, list):
        return [], "could not be read (the run listing had an unexpected shape)", False
    return [row for row in rows if isinstance(row, dict)], "", len(rows) >= ACTIONS_PER_PAGE


def _usage_report(ctx, config, now) -> str:
    """The `/harness status` reply: usage, the queue, and what happens next.

    Built from the sources `harness status`, `harness ledger` and `harness dispatch` read, so
    the reply and the CLI agree.
    """
    led = ctx.ledger
    lines: list[str] = []

    halt = led.halt_request()
    if halt is not None:
        why = f" — {halt['reason']}" if halt.get("reason") else ""
        lines.append(
            f"> **Halted** by @{halt.get('by', 'someone')} at {halt.get('at', 'unknown')}{why}. "
            "Nothing will spend until `/harness resume`."
        )
        lines.append("")

    standing = links.block_line(led, now)
    if standing:
        lines.append(standing)
        lines.append("")

    lines.extend(links.usage_headline(led, config, now))
    lines.append("")

    try:
        rows = priority.queue(store=ctx.store, ledger=led)
        readable = True
    except Exception as exc:  # pragma: no cover - a store that cannot answer says so
        rows, readable = [], False
        lines.append(f"**Queue** — could not be read: {exc}")
    if readable:
        # The same renderer the pinned issue uses, so the reply and the pinned queue cannot
        # disagree about what is waiting (D76).
        lines.extend(links.queue_lines(rows, limit=QUEUE_ROWS))
    lines.append("")

    runs, actions_error, actions_cut = _actions_rows(ctx.gh, config.self_repo)
    lines.extend(links.actions_lines(runs, now, error=actions_error, truncated=actions_cut))
    lines.append("")

    blocked = priority.admit("suggested", store=ctx.store, ledger=led, config=config, now=now)
    lines.append("**Next**")
    if halt is not None:
        lines.append(
            "- nothing spends while the halt stands. `/harness resume` lifts it — comments here "
            f"are read within minutes, and the next scheduled sweep is **{_next_scheduled(now)}**"
        )
    else:
        lines.append(
            f"- comments **here** wake it within minutes; on `{config.upstream_repo}` the next "
            f"scheduled sweep is **{_next_scheduled(now)}**"
        )
        lines.append(
            f"- run window `{config.run_window_start}` → `{config.run_window_end}` UTC"
            + ("; open now" if in_run_window(config, now) else "; closed now")
        )
        lines.append(f"- suggested work: {blocked or 'admitted'}")
    # Audits are gated on the allowance too, so a declined audit's reason shows here.
    audit_blocked = priority.admit("audit", store=ctx.store, ledger=led, config=config, now=now)
    lines.append(f"- audits: {audit_blocked or 'admitted'}")
    return "\n".join(lines)


def _via_for(cmd) -> str:
    return _VIA_BY_SURFACE.get(cmd.surface, "requested")


def _forced(ctx, cmd, item_id: int | None) -> str:
    """Record `--force` on the item; the reply names what it lifts and what still applies."""
    if not getattr(cmd, "force", False) or item_id is None:
        return ""
    # The dispatcher reads the exemption from the ledger.
    ctx.ledger.force(int(item_id))
    ctx.store.append_event(
        int(item_id), "info", f"forced by @{cmd.actor}: exempt from the run window"
    )
    return (
        f" — forced by @{cmd.actor}: it starts on the next sweep, even outside the run window. "
        "Halts, usage stops and both human gates still apply."
    )


def _reply(ctx, config, cmd, message: str) -> None:
    """Answer in the thread the command came from (B236).

    `COMMENT_UPSTREAM=false` silences replies on the product repository only.
    """
    if not message or not ctx.gh.can_write:
        return
    outward = cmd.surface in ("product_issue", "delivery_pr")
    if outward and not config.comment_upstream:
        return
    repo = config.upstream_repo if outward else config.self_repo
    # `steerable=False` drops the command table from the signature; the one-line pointer says
    # what else can be asked and where the full list is.
    body = "\n\n".join(
        [
            message.strip(),
            links.reply_pointer(config, cmd.surface),
            links.signature(config, trusted=ctx.trusted, steerable=False),
        ]
    )
    try:
        ctx.gh.comment(repo, int(cmd.number), body)
    except HarnessError as exc:
        # A reply that cannot be posted must not undo work that already happened.
        logging.getLogger("harness").warning(
            "could not reply on %s#%s: %s", repo, cmd.number, exc)


#: States in which `revise` means another implementation pass: the entry states
#: `stages.revise` accepts from a comment. In any other state there is no code yet, so
#: revising rewrites the plan.
_REVISE_STATES = frozenset({"shipped", "needs-human"})

#: What to say instead of an IllegalTransition when `go` is aimed at a state it cannot move.
_GO_DEAD_ENDS: dict[str, str] = {
    "needs-human": (
        "It hit the revision cap, so it is waiting for a person: `/harness revise <notes>` on "
        "the delivery pull request restarts it."
    ),
    "merged": "It is already delivered and merged.",
    "abandoned": "It was stopped for good; `/harness work` opens a fresh item.",
}


#: What a block still cannot do, said on every reply that grants one. It lifts the calendar and
#: nothing else, and the surest way to be misread is to leave that implicit.
_BLOCK_KEEPS = (
    "Still in force: both usage stops, `.harness/HALT`, `/harness halt`, the trust gate and "
    "both human gates. One item at a time, as always."
)


def _block_command(ctx, cmd) -> str:
    """`/harness block <n>` — suspend the run window for the next n sessions (D77).

    The operator lending the harness sessions they are not going to use. Level 3, because it is
    their subscription being spent and because `--force` already needs that level to lift the
    window for one item.
    """
    now = ctx.clock.now()
    raw = (cmd.args or "").strip()
    if not raw:
        # No count is a question, not a guess at one.
        standing = links.block_line(ctx.ledger, now)
        if standing:
            return standing
        return (
            "No block stands, so work waits for the run window as usual. `/harness block 3` "
            f"gives it the next three five-hour sessions (at most {ledger_mod.MAX_BLOCK_SESSIONS})."
        )
    token = raw.split()[0]
    reason = raw[len(token):].strip()
    if not (token.isascii() and token.isdigit()):
        return (
            f"`{token}` is not a number of sessions, so nothing was changed. The form is "
            f"`/harness block <n>`, where n is 0 to {ledger_mod.MAX_BLOCK_SESSIONS} five-hour "
            "sessions; `/harness block 0` cancels a block that stands."
        )
    sessions = int(token)
    if sessions == 0:
        was = ctx.ledger.block_grant()
        if was is None:
            return "No block was standing, so there was nothing to cancel."
        ctx.ledger.clear_block()
        return (
            f"**Block cancelled** by @{cmd.actor}. The block @{was.get('by', 'someone')} set is "
            "lifted, and work waits for the run window again."
        )
    if sessions > ledger_mod.MAX_BLOCK_SESSIONS:
        # Refused rather than clamped: a clamp grants something other than what was asked for,
        # which is the rule `trust.parse_trust` applies to a level out of range.
        hours = ledger_mod.MAX_BLOCK_SESSIONS * ledger_mod.SESSION_HOURS
        return (
            f"{sessions} sessions is more than the {ledger_mod.MAX_BLOCK_SESSIONS} one block may "
            f"cover ({hours} hours), so nothing was changed. Ask for fewer, or change the run "
            "window in `.harness/config.json`, which is a reviewed pull request."
        )
    grant = ctx.ledger.request_block(cmd.actor, sessions, now, reason)
    anchor = str(grant.get("anchor") or "")
    word = "session" if sessions == 1 else "sessions"
    lines = [
        f"**Blocked out {sessions} {word}.** The run window is suspended until "
        f"**{grant['until']}**."
    ]
    if anchor:
        more = "" if sessions == 1 else f", plus {sessions - 1} more"
        lines.append(
            f"- measured from the five-hour session that resets {anchor}{more} — once, and not "
            "again: a later reading does not move it."
        )
    else:
        lines.append(
            f"- no session reading yet, so this is measured from now: {sessions} × "
            f"{ledger_mod.SESSION_HOURS} h. The first reading will not move it."
        )
    lines.append(
        "- work starts on the next run: a merged proposal starts one immediately, otherwise the "
        f"next scheduled sweep is **{_next_scheduled(now)}**."
    )
    lines.append("- it expires by itself. `/harness block 0` cancels it.")
    lines.append(f"- {_BLOCK_KEEPS}")
    if ctx.ledger.halt_request() is not None:
        lines.append(
            "- **the harness is halted**, so nothing spends until `/harness resume`, block or no "
            "block."
        )
    return "\n".join(lines)


def _act_on_command(ctx, config, cmd) -> str:
    """Apply one authorised keyword command. Returns the reply text."""
    # The typed word, so the log says `reject` when `reject` was typed.
    said = getattr(cmd, "typed", "") or cmd.verb
    reason = f"/harness {said} by {cmd.actor}"
    if cmd.args:
        reason = f"{reason}: {cmd.args}"

    if cmd.verb == "__denied__":
        # The refusal is the whole action; it is recorded and answered (B270).
        return cmd.args

    if cmd.verb == "work":
        # The one verb that creates an item instead of steering one (B235).
        existing = _item_for_command(ctx, config, cmd)
        if existing is not None:
            return f"work item #{existing} already tracks this"
        text = cmd.args
        if not text and cmd.surface == "product_issue":
            # `/harness work` on the issue itself means "this one" (A3).
            text = str(int(cmd.number))
        item_id, message = STAGES["request"](ctx, text=text, actor=cmd.actor, via=_via_for(cmd))
        return message + _forced(ctx, cmd, item_id)

    if cmd.verb == "status":
        return _usage_report(ctx, config, ctx.clock.now())

    if cmd.verb == "halt":
        # A third switch, separate from `.harness/HALT` and `HALT_FILE`. It lives in the ledger
        # every runner fetches, so a comment sets it without a commit, and it gates model calls
        # instead of jobs, so the sweep still hears `/harness resume`.
        if ctx.ledger.halt_request() is not None:
            return "already halted"
        ctx.ledger.request_halt(cmd.actor, cmd.args, iso(ctx.clock.now()))
        return (
            f"**Halted** by @{cmd.actor}. Nothing will spend until `/harness resume`. This does "
            "not stop the workflows themselves — for that, commit a file at `.harness/HALT`."
        )

    if cmd.verb == "resume":
        was = ctx.ledger.halt_request()
        if was is None:
            return "not halted"
        ctx.ledger.clear_halt()
        return f"resumed by @{cmd.actor}; the halt set by @{was.get('by', 'someone')} is lifted"

    if cmd.verb == "block":
        # No item is involved, so this answers before `_item_for_command` is consulted (D77).
        return _block_command(ctx, cmd)

    if cmd.verb == "ask":
        # An answer only; no item is involved (B274).
        return STAGES["ask"](ctx, question=cmd.args, actor=cmd.actor)

    if cmd.verb == "audit":
        number = STAGES["audit"](ctx, lens=cmd.args, actor=cmd.actor)
        return f"audit opened as #{number}"

    if cmd.verb == "promote":
        # Only on the audit issue that holds the findings, so it cannot reach another audit
        # (B252).
        if cmd.surface != "issue":
            return "promote only works on an audit issue in this repository"
        created = STAGES["promote"](
            ctx, issue_number=int(cmd.number), which=cmd.args, actor=cmd.actor
        )
        if not created:
            return "already promoted; no new work items"
        return "created work item(s) " + ", ".join(f"#{n}" for n in created)

    item_id = _item_for_command(ctx, config, cmd)

    if cmd.verb == "split":
        if item_id is None:
            return f"no work item for {cmd.surface} {cmd.number}"
        created = list(STAGES["decompose"](ctx, item_id))
        return f"decomposed into {created}"

    if item_id is None:
        if cmd.verb == "go" and cmd.surface in ("inbox", "issue"):
            # With no item on this thread, `go` (or its alias `queue`) answers with the queue.
            return _usage_report(ctx, config, ctx.clock.now())
        return "no work item for this thread"

    if cmd.verb == "go":
        # "Proceed with this", which depends on the item's state: a suggestion waiting for a
        # green light is approved (B262), and a stopped or blocked item goes back in the queue.
        item = ctx.store.get_work_item(item_id)
        if item is None:
            return f"no work item {item_id}"
        state = item.state
        if state == "blocked":
            # `blocked` is reachable from both sides of gate 1. A branch exists only after
            # `implement`, which runs only after the proposal merged, so a branch means the item
            # was approved once. Sending it back to `discovered` would orphan the branch.
            if item.branch_name:
                ctx.store.transition(item_id, "approved", reason=reason)
                return f"item {item_id} back to approved" + _forced(ctx, cmd, item_id)
            ctx.store.transition(item_id, "discovered", reason=reason)
            return f"item {item_id} back in the queue" + _forced(ctx, cmd, item_id)
        if state == "approved":
            return (
                f"item {item_id} is already approved and waiting for a runner"
                + _forced(ctx, cmd, item_id)
            )
        if state == "discovered":
            return f"item {item_id} is already in the queue" + _forced(ctx, cmd, item_id)
        if state == "proposed":
            # Gate 1. `go` releases only work the harness suggested on its own (B262). Any
            # other proposal is approved by merging it, and `go` must not bypass that.
            if via_of(item) != "suggested":
                return (
                    f"item {item_id} has a proposal waiting for gate 1. Merging the proposal "
                    "pull request is what approves it — `go` only releases work the harness "
                    "suggested on its own. `/harness stop` if you would rather it did not."
                )
            ctx.store.transition(item_id, "approved", reason=reason)
            return f"item {item_id} approved" + _forced(ctx, cmd, item_id)
        if "approved" not in TRANSITIONS.get(state, frozenset()):
            # Answered instead of raised: `needs-human` and the terminal states cannot reach
            # `approved`, and an IllegalTransition traceback in a reply is no answer.
            return (
                f"item {item_id} is `{state}`, which `go` cannot move. "
                + _GO_DEAD_ENDS.get(state, "Say what you want to happen and I will say if I can.")
            )
        ctx.store.transition(item_id, "approved", reason=reason)
        return f"item {item_id} approved" + _forced(ctx, cmd, item_id)

    if cmd.verb == "stop":
        item = ctx.store.get_work_item(item_id)
        if item is None:
            return f"no work item {item_id}"
        state = item.state
        # The target is chosen from the legal edges. `shipped -> abandoned` is illegal (D1), so
        # `stop` on a delivery PR lands in `blocked`, which `/harness go` reverses.
        legal = TRANSITIONS.get(state, frozenset())
        if not legal:
            # `merged` and `abandoned` are terminal. Answered instead of raised, since stopping
            # an item twice is the likeliest way here.
            return f"item {item_id} is already `{state}`; there is nothing left to stop."
        # The level decides how final this is: level 2 parks (reversible with `/harness go`),
        # level 3 ends it, so a maintainer cannot end an item that still has somewhere to park.
        ends_it = cmd.level >= MAX_LEVEL
        if ends_it and "abandoned" in legal:
            target = "abandoned"
        elif "blocked" in legal:
            target = "blocked"
        elif "abandoned" in legal:
            # Nowhere to park: `discovered` has no `blocked` edge (`proposed` does), so the item
            # ends and the reply says so.
            target = "abandoned"
        else:
            return f"item {item_id} is `{state}`, which `stop` cannot move."
        # Transition first, so a refused transition never leaves a closed pull request on a live
        # item.
        ctx.store.transition(item_id, target, reason=reason)
        repo = config.self_repo if cmd.surface == "proposal_pr" else config.upstream_repo
        closed = False
        if cmd.surface in ("proposal_pr", "delivery_pr") and ctx.gh.can_write:
            ctx.gh.close_pull(repo, cmd.number)
            closed = True
        note = "; PR closed" if closed else ""
        if target == "blocked":
            note += ". Parked, not ended — `/harness go` puts it back."
        elif not ends_it:
            note += (
                f". This state has nowhere to park, so it ended the item. Level {MAX_LEVEL} is "
                "what that normally takes."
            )
        return f"item {item_id} {target}" + note

    if cmd.verb == "revise":
        item = ctx.store.get_work_item(item_id)
        # Before code exists, revising rewrites the plan; after, it is another implementation
        # pass. The item's state decides which: `needs-human` shows only on the harness issue,
        # where the operator is told to type this, and `needs-human -> proposing` is illegal.
        if item is not None and item.state in _REVISE_STATES:
            lease = STAGES["revise"](
                ctx, item_id, source="review", notes=cmd.args or "/harness revise"
            )
            return f"item {item_id} re-implemented: {'revised' if lease else 'not revised'}"
        ctx.store.transition(item_id, "proposing", reason=reason)
        spec_path = STAGES["propose"](ctx, item_id, notes=cmd.args)
        return f"item {item_id} re-proposed: {spec_path}"

    if cmd.verb == "rebase":
        notes = cmd.args or "/harness rebase"
        lease = STAGES["revise"](ctx, item_id, source="conflict", notes=notes)
        return f"item {item_id} revise(conflict): {'revised' if lease else 'not revised'}"

    return f"unknown verb {cmd.verb}"


def _by_comment(commands) -> list[list]:
    """`commands` split into runs sharing a comment id, in the order they arrived.

    Grouped without sorting, so commands run in the order they were typed.
    """
    groups: list[list] = []
    for cmd in commands:
        if groups and groups[-1][0].comment_id == cmd.comment_id:
            groups[-1].append(cmd)
        else:
            groups.append([cmd])
    return groups


def run_command(ctx, config, cmd) -> tuple[dict, bool]:
    """Act on one command, answer in its thread, and say whether the batch may continue."""
    record, answer, keep_going = _outcome(ctx, config, cmd)
    _reply(ctx, config, cmd, answer)
    return record, keep_going


def run_comment(ctx, config, cmds) -> tuple[list[dict], bool]:
    """Every command from one comment, answered in one reply.

    One reply per comment keeps the thread readable and spends fewer writes against GitHub's
    content-creation limit.
    """
    records: list[dict] = []
    parts: list[str] = []
    keep_going = True
    for cmd in cmds:
        record, answer, keep_going = _outcome(ctx, config, cmd)
        records.append(record)
        if answer:
            said = getattr(cmd, "typed", "") or cmd.verb
            # Labelled only when the comment carried more than one command.
            parts.append(f"**`/harness {said}`** — {answer}" if len(cmds) > 1 else answer)
        if not keep_going:
            break
    if parts:
        _reply(ctx, config, cmds[0], "\n\n".join(parts))
    return records, keep_going


def _outcome(ctx, config, cmd) -> tuple[dict, str, bool]:
    """`(record, what to say, may the batch continue)` for one command. Writes no comment."""
    record = {
        "verb": cmd.verb,
        "args": cmd.args,
        "surface": cmd.surface,
        "number": cmd.number,
        "comment_id": cmd.comment_id,
        "actor": cmd.actor,
        "result": "",
    }
    try:
        result = _act_on_command(ctx, config, cmd)
        # A note for the actor, such as a refused `--force`, is appended to the reply and never
        # reaches the stage (B284).
        if getattr(cmd, "note", ""):
            result = "\n\n".join(part for part in (result, cmd.note) if part)
        record["result"] = result
        return record, result, True
    except Halted as exc:
        # A commanded halt refuses this command without aborting the batch. Every command is
        # marked seen when the batch is collected, so aborting would consume the rest, including
        # a `/harness resume` that lifts the halt.
        record["result"] = str(exc)
        return record, str(exc), True
    except RateLimited as exc:
        # The next command would fail the same way, so the batch stops here.
        record["result"] = f"rate limited until {exc.reset_at or 'unknown'}"
        return record, "", False
    except RateCeilingReached as exc:
        # GitHub's rate ceiling, separate from the model's, arrives as an ordinary error. Stop
        # here as for RateLimited, so the remaining commands do not post refused replies.
        record["result"] = f"github rate ceiling reached: {exc}"
        return record, "", False
    except BudgetExhausted as exc:
        # A usage stop is a normal outcome, like a closed run window (D3), so the reply says
        # "Not now" and reports no failure.
        record["result"] = f"declined: {exc}"
        return record, f"Not now — {exc}", True
    except HarnessError as exc:
        # A failed command is answered in the thread as well as recorded.
        record["result"] = f"error: {exc}"
        return record, f"that did not work: {exc}", True
    return record, "", True


def _ack_halt_reason(config) -> str:
    """Why nothing is going to happen, or "" when something will.

    Reads the checkout and the ledger file, and never raises.
    """
    try:
        if repo_halted(Path(".")):
            return (
                "**The harness is halted** (`.harness/HALT` is committed). This command was "
                "read, but nothing runs until that file is removed."
            )
    except Exception:  # pragma: no cover - a diagnostic must not fail the diagnosis
        pass
    try:
        # `Ledger` has no `load` and `Config` has no `ledger_path`; both raised, the bare
        # `except` below swallowed it, and a commanded halt was never reported here (D76).
        led = ledger_mod.load(ledger_path_for(config))
        halt = led.halt_request()
    except Exception:  # pragma: no cover - same
        return ""
    if not halt:
        return ""
    who = str(halt.get("by") or halt.get("actor") or "someone")
    why = str(halt.get("reason") or "").strip()
    return (
        f"**The harness is halted** by @{who}"
        + (f": {why}" if why else "")
        + ". This command was read, but nothing spends until `/harness resume`."
    )


def _ack_surface(config, args: argparse.Namespace) -> str:
    """Which `links.SURFACE_HINTS` entry fits the thread this comment is on.

    `ack` is handed a repository and a number and cannot tell an issue from a pull request, so
    it names only the three surfaces it can tell apart; the hints fall back for the rest.
    """
    repo = str(getattr(args, "repo", "") or "").strip().lower()
    self_repo = str(getattr(config, "self_repo", "") or "").strip().lower()
    if repo and self_repo and repo != self_repo:
        return "product_issue"
    number = int(getattr(args, "number", 0) or 0)
    if number and number == int(getattr(config, "inbox_issue", 0) or 0):
        return "inbox"
    return "issue"


def _ack_fast_status(config, args: argparse.Namespace) -> str:
    """The `status` answer `ack` gives without the ledger lock, or "" when it cannot (B440).

    Read-only on every path, and it never saves: `ack.yml` stays outside the `harness-ledger`
    group, so the ledger keeps exactly one writer group (B118). An unreadable ledger returns
    "" and the ordinary acknowledgement is posted instead (B444). What tells the sweep this
    was answered is the reaction `ack.yml` leaves on the comment, never anything in the text.
    """
    try:
        led = ledger_mod.load(ledger_path_for(config))
        now = datetime.now(timezone.utc)
        # A block opens the window, so the line that reports the window has to say so (D77).
        open_now = in_run_window(config, now) or led.block_open(now)
        window = (
            f"`{config.run_window_start}` → `{config.run_window_end}` UTC"
            + ("; open now" if open_now else "; closed now")
        )
        # What Actions is doing, read unauthenticated so `ack` stays tier 0, takes no lock and
        # carries no credential (B440). A failure here omits the section and nothing else.
        try:
            runs, actions_error, actions_cut = _actions_rows(
                PUBLIC_READER(), str(getattr(config, "self_repo", "") or "")
            )
        except Exception:  # pragma: no cover - a courtesy inside a courtesy
            runs, actions_error, actions_cut = None, "", False
        if actions_error:
            # A failure here omits the section (D79). This read is unauthenticated against a
            # ceiling shared by every job on the runner's address, so it is refused routinely,
            # and an error line about a read nobody asked for is worse than no line.
            runs, actions_error, actions_cut = None, "", False
        text = links.fast_status(
            config,
            led,
            now=now,
            window=window,
            next_sweep=_next_scheduled(now),
            queue_issue=getattr(config, "tracking_issue", 0) or 0,
            actions=runs,
            actions_error=actions_error,
            actions_truncated=actions_cut,
        )
    except Exception:  # pragma: no cover - a courtesy must never fail the run it precedes
        return ""
    if not text.strip():
        return ""
    return mark_machine_written(text)


def cmd_ack(args: argparse.Namespace) -> int:
    """Decide what to say about one comment before any work starts (B293).

    The sweep takes minutes; this says within seconds whether the comment will be acted on.
    Prints one JSON object, the same three keys on every path:

    - ``react``: the sweep is going to act on this.
    - ``comment``: the acknowledgement, the answer itself when `ack` gave one, or "" when every
      verb is fast enough that the answer arrives first.
    - ``answered``: that comment *is* the answer, so `ack.yml` marks the thread's comment with
      `gh.ANSWERED_REACTION` once it has landed and the sweep leaves it alone (D76).

    Uses the sweep's own parser and trust gate. Never spends, never writes, and exits 0 on
    every path, so it cannot fail the workflow.
    """
    def _say(react: bool = False, comment: str = "", answered: bool = False) -> int:
        print(
            json.dumps({"react": bool(react), "comment": comment, "answered": bool(answered)})
        )
        return EXIT_OK

    try:
        body = Path(args.body_file).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        LOG.warning("ack: could not read %s: %s", args.body_file, exc)
        return _say()

    actor = str(args.actor or "").lstrip("@")
    try:
        config = _load(args)
        trusted = trust_mod.load_trust(Path(config.trust_file))
    except HarnessError as exc:
        # Nothing to say without a config and trust file; the sweep does its own checking.
        LOG.warning("ack: %s", exc)
        return _say()

    # Every harness reply mentions `/harness`, so the machine account's own comments are
    # skipped first.
    machine = discover_stage.machine_account(config)
    if machine and actor.lower() == str(machine).lstrip("@").lower():
        return _say()

    # The call `keywords.authorise` makes, over the comment shape the sweep reads from the REST
    # API: a level in the trust file, and either a GitHub association or the vouched account id
    # (D68). An untrusted commenter is never told they were heard.
    as_comment = {
        "user": {"login": actor, "id": getattr(args, "actor_id", "")},
        "author_association": str(args.association or ""),
    }
    if not trust_mod.comment_authorised(as_comment, trusted):
        return _say()

    # Naming the machine account is a command form of its own, so the parser is given the
    # handle here exactly as the sweep gives it (B435).
    parsed = keywords.parse_typed(body, mention=machine)
    # Only the verbs this actor may give: the sweep refuses the rest per `VERB_LEVEL`.
    level = trusted.level_of(actor) if hasattr(trusted, "level_of") else 1
    verbs = [
        verb for verb, _args, typed in parsed
        # The typed word's level when it has one: `reject` is level 3 but resolves to `stop` (2).
        if level >= keywords.VERB_LEVEL.get(typed or verb, keywords.VERB_LEVEL.get(verb, 3))
    ]
    if not verbs:
        # Addressed the bot and gave it no verb. `mentions_without_command` is False whenever
        # anything parsed, so an actor whose verbs were all above their level still gets the
        # sweep's refusal rather than a nudge that ignores what they asked for (B436).
        if keywords.mentions_without_command(body, machine):
            return _say(
                react=True,
                comment=mark_machine_written(
                    links.nudge(config, _ack_surface(config, args), mention=machine)
                ),
            )
        return _say()

    # A halted harness does none of this. Both switches count: the committed file stops the
    # workflows, and the commanded halt stops the spending.
    stopped = _ack_halt_reason(config)
    if not stopped and "audit" in verbs:
        # The gate `stages/audit` applies at entry, so the acknowledgement never promises an
        # audit the sweep will decline. Read-only and never raised.
        try:
            led = ledger_mod.load(ledger_path_for(config))
            refused = priority.admit("audit", store=None, ledger=led, config=config)
        except Exception:  # pragma: no cover - a diagnostic must not fail the diagnosis
            refused = None
        if refused:
            stopped = f"**Not now** — {refused}"
    if stopped:
        return _say(react=True, comment=mark_machine_written(stopped))

    # The fast lane (B440). `status` costs nothing and needs no lock, so a comment asking only
    # for it is answered here rather than queued behind whatever holds `harness-ledger`.
    # Judged on every verb the comment carries rather than only the ones this actor may give:
    # a mixed comment must still reach the sweep, which owns the refusal for the rest.
    if parsed and {verb for verb, _a, _t in parsed} <= keywords.ACK_ANSWERS:
        answer = _ack_fast_status(config, args)
        if answer:
            return _say(react=True, comment=answer, answered=True)

    text = links.acknowledgement(verbs)
    # The workflow posts this through `github-script`, which does not mark it the way
    # `gh.comment` does. Unmarked, a list of `/harness` commands would wake the workflows again.
    return _say(react=True, comment=mark_machine_written(text) if text else "")


def cmd_sweep(args: argparse.Namespace) -> int:
    check_repo_halt(_repo_root(args))
    config = _load(args)
    check_halt(config.halt_file)
    ctx = _context(config, args, run_id="sweep")
    # A block ends by the clock, so nothing depends on this running; clearing a spent grant here
    # only keeps it off the surfaces that report one (D77).
    if ctx.ledger.block_grant() is not None and not ctx.ledger.block_open(ctx.clock.now()):
        ctx.ledger.clear_block()
    try:
        commands = keywords.sweep(
            ctx.gh,
            ledger=ctx.ledger,
            trusted=ctx.trusted,
            now_iso=iso(ctx.clock.now()),
            self_repo=config.self_repo,
            upstream_repo=config.upstream_repo,
            inbox_issue=config.inbox_issue,
            machine=discover_stage.machine_account(config),
            thread=int(getattr(args, "thread", 0) or 0),
        )
        for group in _by_comment(commands):
            records, keep_going = run_comment(ctx, config, group)
            for record in records:
                print(json.dumps(record, sort_keys=False))
            if not keep_going:
                break
    finally:
        _save_ledger(ctx)
    return EXIT_OK


# --------------------------------------------------------------------------------------
# tidy: the pinned queue, and the harness's own old comments (D76)
# --------------------------------------------------------------------------------------

#: At most this many queue rows are listed; the rest are counted.
QUEUE_ROWS = 10
#: The newest machine comments kept on a swept issue, whatever their age.
PRUNE_KEEP = 20
#: A machine comment is a candidate for deletion only once it is older than this.
PRUNE_AFTER_DAYS = 30
#: How often the prune half runs. The queue half runs on every sweep.
PRUNE_EVERY_DAYS = 7


def _queue_block_lines(ctx, config, now) -> list[str]:
    """What the pinned issue says: the halt, the allowance, the queue, and what happens next."""
    led = ctx.ledger
    lines: list[str] = []
    halt = led.halt_request()
    if halt is not None:
        why = f" — {halt['reason']}" if halt.get("reason") else ""
        lines.append(
            f"> **Halted** by @{halt.get('by', 'someone')} at {halt.get('at', 'unknown')}{why}. "
            "Nothing will spend until `/harness resume`."
        )
        lines.append("")
    standing = links.block_line(led, now)
    if standing:
        lines.append(standing)
        lines.append("")
    lines.extend(links.usage_headline(led, config, now))
    lines.append("")
    try:
        rows = priority.queue(store=ctx.store, ledger=led)
    except Exception as exc:  # pragma: no cover - a store that cannot answer says so
        lines.append(f"**Queue** — could not be read: {exc}")
    else:
        lines.extend(links.queue_lines(rows, limit=QUEUE_ROWS))
    lines.append("")
    runs, actions_error, actions_cut = _actions_rows(ctx.gh, config.self_repo)
    lines.extend(links.actions_lines(runs, now, error=actions_error, truncated=actions_cut))
    lines.append("")
    lines.append("**Next**")
    lines.append(
        f"- run window `{config.run_window_start}` → `{config.run_window_end}` UTC"
        + ("; open now" if in_run_window(config, now) else "; closed now")
    )
    lines.append(f"- next scheduled sweep **{_next_scheduled(now)}**")
    lines.append("")
    lines.append(
        f"_Written by `harness tidy` at {iso(now)}. Anything typed between these two markers "
        "is overwritten; the rest of this issue is yours._"
    )
    return lines


def _publish_queue(ctx, config) -> str:
    """Rewrite the span between the markers on the pinned issue. Returns what happened."""
    number = int(getattr(config, "tracking_issue", 0) or 0)
    if not number:
        return "no tracking issue configured"
    if not ctx.gh.can_write:
        return "no write credential"
    block = links.queue_block(_queue_block_lines(ctx, config, ctx.clock.now()))
    try:
        issue = ctx.gh.get(f"/repos/{config.self_repo}/issues/{number}")
    except HarnessError as exc:
        LOG.warning("tidy: could not read #%s: %s", number, exc)
        return f"could not read #{number}"
    body = str((issue or {}).get("body") or "") if isinstance(issue, dict) else ""
    updated = links.replace_queue_block(body, block)
    if updated is None:
        # Never appended and never guessed at: the rest of that body is a person's prose.
        LOG.warning("tidy: #%s carries no queue markers; nothing written", number)
        return f"no queue markers on #{number}; nothing written"
    if updated == body:
        # Byte-identical, so no request and no edit in the issue's timeline.
        return "unchanged"
    ctx.gh.update_issue_body(config.self_repo, number, updated)
    return f"written to #{number}"


def _prune_machine_comments(ctx, config) -> tuple[list[int], str]:
    """Delete the harness's own oldest comments on the two pinned issues (D76).

    A comment is a candidate only when one of the logins the harness posts under wrote it *and*
    it carries the machine marker. The marker alone is not an author test: GitHub's quote-reply
    copies the source markdown, HTML comments included, so a person who quote-replies the
    harness carries the marker in a comment they wrote, and the marker alone would delete it.
    """
    now = ctx.clock.now()
    last = ctx.ledger.pruned_at()
    if last:
        try:
            if now - parse_iso(last) < timedelta(days=PRUNE_EVERY_DAYS):
                return [], f"last pruned {last}"
        except ValueError:
            pass  # an unreadable cursor is no reason to skip
    if not ctx.gh.can_write:
        return [], "no write credential"
    numbers: list[int] = []
    for key in ("inbox_issue", "tracking_issue"):
        number = int(getattr(config, key, 0) or 0)
        if number and number not in numbers:
            numbers.append(number)
    if not numbers:
        return [], "no issue to prune"
    logins = machine_logins(discover_stage.machine_account(config))
    deleted: list[int] = []
    for number in numbers:
        deleted.extend(_prune_one_issue(ctx, config.self_repo, number, now, logins))
    ctx.ledger.mark_pruned(iso(now))
    return deleted, ""


def _prune_one_issue(ctx, repo: str, number: int, now, logins: frozenset[str]) -> list[int]:
    """The harness's own comments on one issue that are old enough and not recent context.

    `logins` is `gh.machine_logins`. Both halves are required, because the marker travels into
    a person's own comment whenever they answer with GitHub's quote-reply (D76).
    """
    from harness.gh import MACHINE_MARKER

    try:
        comments = list(ctx.gh.issue_comments(repo, number))
    except HarnessError as exc:
        LOG.warning("tidy: could not read comments on %s#%s: %s", repo, number, exc)
        return []
    mine: list[dict] = []
    for row in comments:
        author = str(((row.get("user") or {}).get("login")) or "").lstrip("@").lower()
        if author in logins and MACHINE_MARKER in str(row.get("body") or ""):
            mine.append(row)
    # Oldest first, so the tail is the recent context that survives whatever its age.
    mine.sort(key=lambda row: str(row.get("created_at") or ""))
    candidates = mine[:-PRUNE_KEEP] if len(mine) > PRUNE_KEEP else []
    cutoff = now - timedelta(days=PRUNE_AFTER_DAYS)
    deleted: list[int] = []
    for comment in candidates:
        try:
            if parse_iso(str(comment.get("created_at") or "")) >= cutoff:
                continue
        except ValueError:
            continue  # an unreadable timestamp is not evidence of age
        ident = int(comment.get("id") or 0)
        if ident <= 0:
            continue
        try:
            ctx.gh.delete_issue_comment(repo, ident)
        except HarnessError as exc:
            LOG.warning("tidy: could not remove comment %s: %s", ident, exc)
            continue
        deleted.append(ident)
    return deleted


def cmd_tidy(args: argparse.Namespace) -> int:
    """Mark merged deliveries done, publish the queue on the pinned issue, and prune the
    harness's own old comments (D76, D86).

    Spends nothing: no model call and no clone. The first two run on every sweep, so the pinned
    issue is fresh within minutes of anything changing; the prune is behind a weekly cursor in
    the ledger.
    """
    check_repo_halt(_repo_root(args))
    config = _load(args)
    check_halt(config.halt_file)
    ctx = _context(config, args, run_id="tidy")
    queue = ""
    pruned: list[int] = []
    skipped = ""
    done: list[int] = []
    try:
        try:
            done = deliver_stage.mark_merged(ctx)
        except HarnessError as exc:
            # Marking is a courtesy to the queue below, which must publish regardless.
            LOG.warning("tidy: marking merged deliveries failed: %s", exc)
        queue = _publish_queue(ctx, config)
        pruned, skipped = _prune_machine_comments(ctx, config)
    finally:
        _save_ledger(ctx)
    payload = {"done": done, "queue": queue, "pruned": pruned, "skipped": skipped}
    lines = [f"done: {', '.join(f'#{n}' for n in done) or 'none'}", f"queue: {queue}"]
    lines.append(f"pruned: {len(pruned)} comment(s)" + (f" ({skipped})" if skipped else ""))
    _emit(payload, "\n".join(lines), args)
    return EXIT_OK


# --------------------------------------------------------------------------------------
# ledger (B116)
# --------------------------------------------------------------------------------------


def _self_repo_comments(ctx, config) -> list[dict]:
    """Every comment on every issue of SELF_REPO, shaped for `ledger.rebuild`."""
    listing = ctx.gh.get(f"/repos/{config.self_repo}/issues?state=all&per_page=100")
    comments: list[dict] = []
    for issue in listing if isinstance(listing, list) else []:
        if not isinstance(issue, dict) or "pull_request" in issue:
            continue
        number = int(issue.get("number", 0) or 0)
        if number <= 0:
            continue
        for comment in ctx.gh.issue_comments(config.self_repo, number):
            comments.append(
                {
                    "body": str(comment.get("body", "") or ""),
                    "created_at": str(comment.get("created_at", "") or ""),
                    "issue": number,
                }
            )
    return comments


def _halt_lines(led) -> list[str]:
    """The commanded halt, shown at the top of `harness status` and `harness ledger`."""
    halt = led.halt_request()
    if halt is None:
        return []
    why = f": {halt['reason']}" if halt.get("reason") else ""
    return [
        f"HALTED by @{halt.get('by', 'someone')} at {halt.get('at', 'unknown')}{why}",
        "  nothing will spend until `/harness resume` (or `harness resume --commanded`)",
    ]


def _usage_lines(led, config, now=None) -> list[str]:
    """The measured subscription usage and how far it is from each stop (B221).

    ``now`` marks a reading whose window has reset since. The governor and the dispatcher stop
    refusing at that instant, so this view stops saying STOPPED too (B406).
    """
    usage = (dict(led.window).get("usage") or {}) if led.window else {}
    if not usage:
        return [
            "subscription:",
            "  (not measured yet -- the signal rides on the headers of a real model call, so",
            "   until one has been made there is nothing to report. B114: no decision may",
            "   DEPEND on it. What bounds a call meanwhile is the run window, the turn caps,",
            "   both halts and the subscription's own refusal.)",
        ]
    rows = [
        ("session (5h) ", "five_hour", float(config.session_usage_stop_pct)),
        ("weekly  (7d) ", "seven_day", float(config.weekly_usage_stop_pct)),
    ]
    # The allowance is shared with the rest of the account, so it moves while the harness sleeps.
    lines = ["subscription (shared with everything else this account does):"]
    for label, key, stop in rows:
        window = usage.get(key) or {}
        raw = window.get("utilization")
        if raw is None:
            lines.append(f"  {label}       (not reported)")
            continue
        pct = float(raw) * 100.0
        if now is not None and ledger_mod.window_has_reset(window, now):
            verdict = "window reset since; no longer stops anything"
        else:
            verdict = "STOPPED" if pct >= stop else f"{stop - pct:.1f} to go"
        lines.append(
            f"  {label}{pct:5.1f}%  stop at {stop:.0f}%  ({verdict})"
            f"  resets {window.get('resets_at') or 'unknown'}"
        )
    lines.append(f"  observed at   {usage.get('observed_at') or 'unknown'}")
    return lines


#: Relabelling while a job is mid-flight would race the job's own label write (B267).
RELABEL_BUSY_STATES: tuple[str, ...] = ("proposing", "implementing", "revising")


def cmd_relabel(args: argparse.Namespace) -> int:
    """Move every open issue from the `harness:*` family to `stage:*` (B266).

    Idempotent, and it changes no state: an issue keeps its stage and gains the `kind:` and
    `via:` labels its history implies. Refuses while a job is in flight, because the job's own
    state-label write would race it.
    """
    config = _load(args)
    ctx = _context(config, args, run_id="relabel")

    if not ctx.gh.can_write:
        _emit({"relabelled": [], "skipped": "no write credential"},
              "no write credential; nothing relabelled", args)
        return EXIT_OK

    # Every open issue, read once and classified locally: `list_work_items(state=...)` filters
    # on the new `stage:` label, so it never returns an unmigrated issue. `paginate`, because
    # `get` is one request and would stop at the first hundred issues.
    listing = ctx.gh.paginate(f"/repos/{config.self_repo}/issues?state=open&per_page=100")
    inbox = int(getattr(config, "inbox_issue", 0) or 0)
    found: list[tuple[int, str, list[str]]] = []
    ambiguous: list[int] = []
    for issue in listing if isinstance(listing, list) else []:
        if not isinstance(issue, dict) or "pull_request" in issue:
            continue
        number = int(issue.get("number", 0) or 0)
        if number <= 0 or number == inbox:
            continue
        # `_label_names` rather than a hand-rolled comprehension: GitHub serves a label as an
        # object here and as a bare string elsewhere, and that helper already handles both.
        names = _label_names(issue)
        states = {STATE_OF_LABEL[n] for n in names if n in STATE_OF_LABEL}
        if len(states) > 1:
            # Refused, as `_state_of` refuses it: picking one would depend on GitHub's array
            # order, and could move the item or leave it with two `stage:` labels.
            ambiguous.append(number)
            continue
        if states:
            found.append((number, states.pop(), names))

    if ambiguous:
        raise HarnessError(
            f"issue(s) {sorted(ambiguous)} carry more than one state label, so their stage is "
            "ambiguous and relabelling them would have to guess. Leave one state label on each "
            "and run this again."
        )

    busy = sorted(n for n, state, _names in found if state in RELABEL_BUSY_STATES)
    if busy:
        raise HarnessError(
            f"items {sorted(busy)} are mid-flight ({', '.join(RELABEL_BUSY_STATES)}); "
            "relabel would race the job's own label write. Wait for them, or halt first."
        )

    legacy_of = {label: state for state, label in LEGACY_LABELS.items()}
    relabelled: list[dict] = []
    for number, state, names in sorted(found):
        kept = [n for n in names if n not in legacy_of and n != LABELS[state]]
        wanted = [LABELS[state]]
        if not any(n.startswith("kind:") for n in kept):
            # Every work item is product work (I-18); an audit issue is not a work item.
            wanted.append(KIND_LABELS["product"])
        if not any(n.startswith("via:") for n in kept):
            # An item with no `via:` label predates them; a human caused it, so `requested`.
            wanted.append(VIA_LABELS["requested"])
        final = sorted(set(kept + wanted))
        if sorted(set(names)) == final:
            continue
        ctx.gh.set_labels(config.self_repo, number, final)
        relabelled.append({"item": number, "state": state, "labels": final})

    _emit(
        {"relabelled": relabelled, "skipped": None},
        f"relabelled {len(relabelled)} issue(s)"
        + (": " + ", ".join(f"#{r['item']}" for r in relabelled) if relabelled else ""),
        args,
    )
    return EXIT_OK


def _rebuilt(current, comments):
    """`--rebuild`'s ledger: the replayed history and call count, over the window on disk.

    A transition comment carries the history and nothing else, so the state no comment can
    reconstruct -- the period start, the usage reading, the carry, the rate limit and the
    cursors -- is kept rather than reset to an empty ledger (D74).
    """
    replayed = ledger_mod.rebuild(comments)
    current.history = list(replayed.history)
    current.window["calls"] = int(replayed.window.get("calls", 0) or 0)
    return current


def cmd_ledger(args: argparse.Namespace) -> int:
    config = _load(args)
    ctx = _context(config, args, run_id="ledger")
    led = ctx.ledger
    rebuilt = False
    if getattr(args, "rebuild", False):
        led = _rebuilt(led, _self_repo_comments(ctx, config))
        ledger_mod.save(led, ctx.ledger_path)
        rebuilt = True

    if _wants_json(args):
        print(led.to_json())
        return EXIT_OK

    now_iso = iso(ctx.clock.now())
    window = dict(led.window)
    lines = [f"ledger {ctx.ledger_path}" + ("  (rebuilt)" if rebuilt else "")]
    lines.extend(_halt_lines(led))
    standing = links.block_line(led, ctx.clock.now())
    if standing:
        lines.append(standing)
    lines.append("window:")
    lines.append(f"  period_start        {window.get('period_start')}")
    lines.append(f"  calls               {int(window.get('calls') or 0)}")
    lines.append(f"  rate_limited_until  {window.get('rate_limited_until') or 'none'}")
    lines.extend(_usage_lines(led, config, ctx.clock.now()))
    limited = "yes" if led.rate_limited(now_iso) else "no"
    lines.append(f"rate limited now: {limited} (now {now_iso})")
    lines.append(f"history: {len(led.history)} entr{'y' if len(led.history) == 1 else 'ies'}")
    cursor = (led.cursors or {}).get("notifications_last_seen")
    lines.append(f"notifications cursor: {cursor or 'none'}")
    forced = led.forced()
    if forced:
        lines.append(
            "window-exempt (--force): " + ", ".join(f"#{n}" for n in forced)
        )
    print("\n".join(lines))
    return EXIT_OK


# --------------------------------------------------------------------------------------
# sync-fork (B105)
# --------------------------------------------------------------------------------------


def cmd_sync_fork(args: argparse.Namespace) -> int:
    config = _load(args)
    if not config.fork_repo:
        _emit(
            {"fork": "", "upstream": config.upstream_repo, "sha": None, "synced": False},
            "no FORK_REPO configured; nothing to sync",
            args,
        )
        return EXIT_OK
    ctx = _context(config, args, run_id="sync-fork")
    # The client's fast-forward-only push_ref (B105): no force path, and the token stays in
    # gh.py. ForkDiverged propagates to main().
    push = functools.partial(ctx.gh.push_ref, remote_repo=config.fork_repo)
    sha = sync_fork(config, workdir=config.runs_dir / "sync-fork", push=push)
    _emit(
        {"fork": config.fork_repo, "upstream": config.upstream_repo, "sha": sha, "synced": True},
        f"fork {config.fork_repo} main at {sha} (upstream {config.upstream_repo})",
        args,
    )
    return EXIT_OK


# --------------------------------------------------------------------------------------
# local-loop
# --------------------------------------------------------------------------------------


def _write_heartbeat(work: Path) -> None:
    """Write `<work>/HEARTBEAT` as an ISO-Z timestamp, synchronously."""
    roots = allowed_roots()
    if roots:
        resolved = work.resolve()
        inside = False
        for root in roots:
            root_resolved = Path(root).resolve()
            if resolved == root_resolved or root_resolved in resolved.parents:
                inside = True
                break
        if not inside:
            set_write_roots([*roots, work])
    guarded_write(work / "HEARTBEAT", iso(datetime.now(timezone.utc)) + "\n")


def _stop_requested(work: Path) -> bool:
    return (work / "STOP").exists()


def _was_handed_off(ledger_path: Path | None, item_id: int) -> bool:
    """True when the run that just finished parked ``item_id`` across a usage stop (D3).

    ``cmd_run`` absorbs ``BudgetExhausted``/``RateLimited`` and returns ``EXIT_OK``, so the
    outcome is read from the ledger's carry slot: ``deliver.handoff`` sets it and a green
    ``revise --source continue`` clears it. The ledger is re-read from disk because ``cmd_run``
    builds its own contexts, which leaves the dispatch context's copy stale.
    """
    if ledger_path is None:
        return False
    return _carry_issue(ledger_mod.load(Path(ledger_path))) == int(item_id)


def _local_unit(args: argparse.Namespace, work: Path) -> None:
    """One unit: dispatch, then `run --item` (implement → package → deliver) per plan."""
    config = _load(args)
    check_halt(config.halt_file)
    ctx = _context(config, args, run_id="dispatch")
    unit_plan = _build_plan(ctx, config, args)
    print(unit_plan.to_json())
    for item_id in unit_plan.start:
        if _stop_requested(work):
            print("STOP present; unit cut short")
            return
        _write_heartbeat(work)
        run_args = argparse.Namespace(
            config=args.config,
            verbose=getattr(args, "verbose", False),
            json=False,
            dry_run=getattr(args, "dry_run", False),
            item=int(item_id),
            until=None,
        )
        try:
            cmd_run(run_args)
        except (Halted, RepoHalted):
            raise
        except HarnessError as exc:
            print(f"item {item_id} failed: {exc}", file=sys.stderr)
        _write_heartbeat(work)
        # A usage stop is global: the governor refuses the next item's first call too, and each
        # further handoff would overwrite the single carry slot. The unit ends instead.
        if _was_handed_off(ctx.ledger_path, item_id):
            print(f"item {item_id} was handed off; unit ends")
            return


def cmd_local_loop(args: argparse.Namespace) -> int:
    check_repo_halt(_repo_root(args))
    work = Path(args.work) if args.work else Path.cwd()
    loop_seconds = max(0, int(args.loop_seconds))
    _write_heartbeat(work)
    while True:
        if _stop_requested(work):
            print("STOP present; exiting")
            return EXIT_OK
        if repo_halted(_repo_root(args)):
            print("halted by .harness/HALT")
            return EXIT_OK
        _write_heartbeat(work)
        _local_unit(args, work)
        if args.once:
            return EXIT_OK
        slept = 0
        while slept < loop_seconds:
            if _stop_requested(work):
                print("STOP present; exiting")
                return EXIT_OK
            SLEEP(min(HEARTBEAT_SLICE_S, loop_seconds - slept))
            slept += HEARTBEAT_SLICE_S
            _write_heartbeat(work)


def _trust_level(value: object) -> int:
    """``2`` or ``maintainer`` as a level. Raises on anything else; there is no default."""
    text = str(value or "").strip().lower()
    # ASCII digits only: `str.isdigit()` is true for a superscript two, which `int()` then
    # rejects with an unhandled ValueError.
    if text.isascii() and text.isdigit() and 1 <= int(text) <= trust_mod.MAX_LEVEL:
        return int(text)
    for level, name in trust_mod.LEVEL_NAMES.items():
        if level and name == text:
            return int(level)
    named = ", ".join(
        f"{lvl} {trust_mod.LEVEL_NAMES[lvl]}" for lvl in range(1, trust_mod.MAX_LEVEL + 1)
    )
    raise HarnessError(f"--level must be one of: {named}; got {value!r}")


def _trust_grants(level: int) -> str:
    """What `level` may actually give, read off the gate's own table."""
    verbs = [
        verb
        for tier in trust_mod.tier_table(keywords.VERB_LEVEL)
        if tier.level and tier.level <= level
        for verb in tier.verbs
    ]
    return ", ".join(f"/harness {verb}" for verb in sorted(verbs)) or "nothing"


def _gate_accepts(line: str, handle: str, level: int, account: int | None = None) -> bool:
    """True when `parse_trust` reads `line` back as exactly the grant it was printed to make.

    The output is pasted unedited, so it is checked against the parser itself.
    """
    trusted = trust_mod.parse_trust(line)
    return trusted.level_of(handle) == level and trusted.vouched_id(handle) == account


def _refuse_own_line(line: str) -> int:
    """Print nothing to stdout when the gate would not honour what we were about to print."""
    print(
        f"refusing to print {line!r}: the gate does not read it back as the grant it was meant "
        "to be, and a line that looks finished and admits nobody is worse than no line at all. "
        "That is a harness bug rather than anything you typed; please report it.",
        file=sys.stderr,
    )
    return EXIT_ERROR


def _trust_line(args: argparse.Namespace) -> int:
    """The one line to paste, on stdout; what it means, on stderr, so stdout copies cleanly.

    Reads no configuration and opens no database: the lookup is one unauthenticated GET, so
    the command runs in an unprovisioned checkout.
    """
    raw = str(getattr(args, "login", "") or "").strip().lstrip("@")
    level = _trust_level(getattr(args, "level", ""))
    if not trust_mod.handle_shaped(raw):
        print(
            f"{raw!r} is not a GitHub login (letters, digits and hyphens), so no line was "
            "printed: a handle of that shape can never match a commenter and would be refused "
            "for ever, silently.",
            file=sys.stderr,
        )
        return EXIT_ERROR

    name = trust_mod.LEVEL_NAMES.get(level, str(level))
    if getattr(args, "no_vouch", False):
        bare = f"{level} {raw}"
        if not _gate_accepts(bare, raw, level):
            return _refuse_own_line(bare)
        print(bare)
        print(
            f"Level {level} ({name}): {_trust_grants(level)}.\n"
            f"This is the association route: it works only where GitHub reports @{raw} as "
            "OWNER, MEMBER or COLLABORATOR, which in practice means inviting them to the "
            "repository they comment on. If they must not have access here (D30), drop "
            "--no-vouch and this will vouch for their account id instead.",
            file=sys.stderr,
        )
        return EXIT_OK

    import urllib.parse

    refusal = (
        "No line printed. An unvouched line is exactly the entry that gets silently denied, "
        "so printing one after a failure would hand you a line that looks finished and grants "
        "nothing off this repository. Try again, or use --no-vouch deliberately if they are a "
        "collaborator here."
    )
    try:
        data = PUBLIC_READER().get(f"/users/{urllib.parse.quote(raw, safe='')}")
    except Exception as exc:  # noqa: BLE001 - any failure to read means the same thing
        print(f"could not resolve @{raw}'s account id: {exc}", file=sys.stderr)
        print(refusal, file=sys.stderr)
        return EXIT_ERROR

    account = trust_mod.parse_user_id(data.get("id")) if isinstance(data, dict) else None
    if account is None:
        print(
            f"GitHub returned no usable account id for @{raw}, so no line was printed.",
            file=sys.stderr,
        )
        return EXIT_ERROR
    login = str((data.get("login") if isinstance(data, dict) else "") or raw)
    kind = str((data.get("type") if isinstance(data, dict) else "") or "").strip()
    if kind and kind != ACCOUNT_TYPE_PERSON:
        # An organisation has a valid account id, but a vouch is checked against
        # `comment.user.id` and an organisation authors no comments, so the line would admit
        # nobody. Only a type GitHub returned is refused; a missing type proves nothing.
        print(
            f"GitHub says @{login} is not a person: that login is a {kind} account, so no "
            f"line was printed. Only a person authors a comment, so a vouch for account "
            f"{account} could never match one and the line would be refused everywhere, "
            "silently. If you meant a person, check the login.",
            file=sys.stderr,
        )
        return EXIT_ERROR

    vouched = f"{level} {login} vouch:{account}"
    if not _gate_accepts(vouched, login, level, account):
        return _refuse_own_line(vouched)
    print(vouched)
    print(
        f"Level {level} ({name}): {_trust_grants(level)}.\n"
        f"The vouch pins this line to account {account} — @{login} as GitHub knows them "
        "today. That account is heard on every repository whatever its association, and any "
        "other account holding this login is refused, so it needs no invitation and gets no "
        "access to this repository.\n"
        "Paste it into .harness/trust.txt and open a pull request: the file is "
        "CODEOWNERS-protected, and the harness cannot write it (B143). The next sweep reads "
        "it fresh — no restart, no secret.",
        file=sys.stderr,
    )
    return EXIT_OK


def _trust_show(args: argparse.Namespace) -> int:
    """The trust file as the gate reads it: who, at what level, by which route, and what is
    being refused without anybody being told.

    With no usable `.env` it reads the default path and says so, since it needs only that file.
    """
    note = ""
    try:
        path = Path(_load(args).trust_file)
    except Exception as exc:  # noqa: BLE001 - one text file must not need a whole configuration
        path = _repo_root(args) / DEFAULT_TRUST_FILE
        note = f"no configuration was read ({exc}), so this is the default path"
    trusted = load_trust(path)
    entries = trust_mod.describe(trusted)
    refused = trust_mod.refusals(trusted)
    table = trust_mod.tier_table(keywords.VERB_LEVEL)

    payload = {
        "path": str(path),
        "note": note or None,
        "handles": len(trusted),
        "entries": [
            {
                "handle": e.handle,
                "level": e.level,
                "level_name": e.level_name,
                "route": e.route,
                "vouched_id": e.vouched_id,
            }
            for e in entries
        ],
        "refused": [{"line": line, "why": why} for line, why in refused],
        "duplicated": list(trusted.duplicated),
        "tiers": [
            {"level": t.level, "name": t.name, "verbs": list(t.verbs)} for t in table
        ],
    }

    lines = [f"{path} — {len(trusted)} handle(s)"]
    if note:
        lines.append(f"  ({note})")
    for entry in entries:
        if entry.route == "vouch":
            how = f"vouched for account {entry.vouched_id}; heard on every repository"
        else:
            how = (
                "heard only where GitHub reports them OWNER, MEMBER or COLLABORATOR "
                "(the association half — invisible from their side)"
            )
        lines.append(f"  {entry.level} {entry.level_name:<10} @{entry.handle} — {how}")
    if not entries:
        lines.append("  (nobody: every /harness command is read, denied and ignored)")
    for handle in trusted.duplicated:
        lines.append(
            f"  ! @{handle} is named by more than one line; the highest level wins, so a line "
            "added to lower it does nothing"
        )
    if refused:
        lines.append("")
        lines.append("refused — these grant nothing, and nobody is told:")
        for line, why in refused:
            lines.append(f"  ! {line!r} {why}")
    lines.append("")
    lines.append("what each level may give:")
    for tier in table:
        verbs = ", ".join(f"/harness {v}" for v in tier.verbs) or "nothing"
        lines.append(f"  {tier.level} {tier.name:<10} {verbs}")
    lines.append(
        "  (level 0 is the absence of a line: the comment is read, denied and ignored)"
    )

    _emit(payload, "\n".join(lines), args)
    return EXIT_OK


def cmd_trust(args: argparse.Namespace) -> int:
    """Turn a login into the exact trust.txt line to commit, or read the current file back.

    It never writes: `.harness/` is outside the write roots (B143), so the trust list changes
    only through a reviewed pull request. `--level` has no default, so no grant is implicit.
    """
    if (getattr(args, "trust_action", "") or "show") == "line":
        return _trust_line(args)
    return _trust_show(args)


def cmd_block(args: argparse.Namespace) -> int:
    """Suspend the run window for the next N five-hour sessions; `0` cancels (D77).

    The CLI half of `/harness block`, for the operator at a keyboard. It writes the same grant
    in the same place, so the two forms cannot come to mean different things.
    """
    config = _load(args)
    ctx = _context(config, args, run_id="block")
    now = ctx.clock.now()
    raw = getattr(args, "sessions", None)
    if raw is None:
        text = links.block_line(ctx.ledger, now) or "no block stands"
        _emit(
            {"block": ctx.ledger.block_grant(), "open": ctx.ledger.block_open(now)}, text, args
        )
        return EXIT_OK
    token = str(raw).strip()
    if not (token.isascii() and token.isdigit()):
        raise HarnessError(
            f"sessions must be a whole number 0..{ledger_mod.MAX_BLOCK_SESSIONS}; got {raw!r}"
        )
    sessions = int(token)
    if sessions > ledger_mod.MAX_BLOCK_SESSIONS:
        raise HarnessError(
            f"{sessions} sessions is more than the {ledger_mod.MAX_BLOCK_SESSIONS} one block may "
            "cover; nothing was changed"
        )
    if sessions == 0:
        was = ctx.ledger.block_grant()
        ctx.ledger.clear_block()
        ctx.save_ledger()
        _emit(
            {"block": None, "cleared": was},
            "block cancelled" if was else "no block was standing",
            args,
        )
        return EXIT_OK
    grant = ctx.ledger.request_block(
        "operator", sessions, now, str(getattr(args, "reason", "") or "")
    )
    ctx.save_ledger()
    _emit(
        {"block": grant, "open": True},
        f"run window suspended for {sessions} session(s), until {grant['until']}\n"
        f"  {_BLOCK_KEEPS}",
        args,
    )
    return EXIT_OK


COMMANDS = {
    "init": cmd_init,
    "block": cmd_block,
    "doctor": cmd_doctor,
    "setup": cmd_setup,
    "status": cmd_status,
    "discover": cmd_discover,
    "propose": cmd_propose,
    "approve": cmd_approve,
    "run": cmd_run,
    "package": cmd_package,
    "archive": cmd_archive,
    "halt": cmd_halt,
    "resume": cmd_resume,
    "dispatch": cmd_dispatch,
    "deliver": cmd_deliver,
    "revise": cmd_revise,
    "decompose": cmd_decompose,
    "sweep": cmd_sweep,
    "tidy": cmd_tidy,
    "ack": cmd_ack,
    "trust": cmd_trust,
    "ledger": cmd_ledger,
    "relabel": cmd_relabel,
    "sync-fork": cmd_sync_fork,
    "local-loop": cmd_local_loop,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    _configure_logging(bool(getattr(args, "verbose", False)))

    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_OK

    try:
        return COMMANDS[args.command](args)
    except NotImplementedInDelivery1 as exc:
        print(str(exc) or "not implemented in delivery 1", file=sys.stderr)
        return EXIT_UNIMPLEMENTED
    except RepoHalted:
        # The repo-level kill switch is a normal outcome, so it exits 0 (B149).
        print("halted by .harness/HALT")
        return EXIT_OK
    except Halted as exc:
        print(f"halted: {exc}", file=sys.stderr)
        return EXIT_HALTED
    except BudgetExhausted as exc:
        # The harness declined to start a model call: a usage stop, a stored rate limit or a
        # priority refusal. This exit code and this phrase are a workflow contract (D74).
        print(f"budget exhausted: {exc}", file=sys.stderr)
        return EXIT_BUDGET
    except RateLimited as exc:
        # B120: the stage already returned the item to its prior state.
        if _wants_json(args):
            # `--json` promises a JSON document on stdout: discover.yml feeds it to jq under
            # `set -e`, where a bare line fails the step (B398).
            print(json.dumps({"rate_limited_until": exc.reset_at}, indent=2))
        else:
            print(f"rate limited until {exc.reset_at or 'unknown'}")
        return EXIT_OK
    except ForkDiverged as exc:
        print(f"fork diverged: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except HarnessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
