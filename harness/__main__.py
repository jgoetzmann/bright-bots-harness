"""Command line entry point: every subcommand of HARNESS-SPEC §5.10 and the handoff §3.2."""

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

from harness import __version__, keywords, verify_pin
from harness import ledger as ledger_mod
from harness.clock import iso
from harness.clone import Lease, sync_fork
from harness.config import in_run_window, load_config
from harness.context import build_context
from harness.dispatcher import Candidate, Plan, plan as plan_dispatch
from harness.errors import (
    BudgetExhausted,
    ConfigError,
    ForkDiverged,
    Halted,
    HarnessError,
    NotImplementedInDelivery1,
    PinMismatch,
    RateLimited,
    RepoHalted,
)
from harness.halt import check_halt, check_repo_halt, disengage, engage, halted, repo_halted
from harness.identity import Identity, write_human_doc
from harness.packager import archive as archive_package
from harness.packager import build as build_package
from harness.redact import allowed_roots, guarded_write, set_write_roots
from harness.stages import STAGES
from harness.stages import deliver as deliver_stage
from harness.store import LABELS, STATES
from harness.trust import load_trust

LOG = logging.getLogger("harness")

# Injectables. Tests monkeypatch these rather than the stdlib.
WHICH = shutil.which
RUN = subprocess.run
SLEEP = time.sleep

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_UNIMPLEMENTED = 2
EXIT_DEGRADED = 3
EXIT_BUDGET = 4
EXIT_HALTED = 5
EXIT_SETUP_OUTSTANDING = 6

REQUIRED_BINARIES = ("git", "claude", "node", "npm", "npx")

MAX_TURNS_PROBE_ARGV = ["claude", "-p", "--max-turns", "1", "--output-format", "json", ""]

# Handoff §6.5 (A30) plus the §2 additions: every key doctor must name, with the Config field
# that carries it once the config has loaded. The five Delivery 3 knobs close the block:
# OPERATIONS §13.5 sends the operator to `harness doctor` to confirm exactly those
# values after a change, so every key of ``config.CONFIG_JSON_KEYS`` appears here.
CONFIG_KEYS: tuple[tuple[str, str], ...] = (
    ("WEEKLY_CAP_USD", "weekly_cap_usd"),
    ("PER_CALL_CAP_USD", "per_call_cap_usd"),
    ("RESERVE_PCT", "reserve_pct"),
    ("MAX_CONCURRENT_ITEMS", "max_concurrent_items"),
    ("MAX_REVISE_CYCLES", "max_revise_cycles"),
    ("FORK_REPO", "fork_repo"),
    ("UPSTREAM_REPO", "upstream_repo"),
    ("TRUST_FILE", "trust_file"),
    ("NOTIFY_POLL_HOURS", "notify_poll_hours"),
    ("MAX_SUBISSUES", "max_subissues"),
    ("SELF_REPO", "self_repo"),
    ("TRACKING_ISSUE", "tracking_issue"),
    ("STORE_BACKEND", "store_backend"),
    # Delivery 3 (RUN-DECISIONS-D3, the Config section): the two usage stops, the overrun
    # allowance and the run window.
    ("WEEKLY_USAGE_STOP_PCT", "weekly_usage_stop_pct"),
    ("SESSION_USAGE_STOP_PCT", "session_usage_stop_pct"),
    ("OVERRUN_PCT", "overrun_pct"),
    ("RUN_WINDOW_START", "run_window_start"),
    ("RUN_WINDOW_END", "run_window_end"),
)

# B147: an item left in a running state longer than this with no live run is reset.
STALE_RUNNING_HOURS = 3

# local-loop: HEARTBEAT cadence while sleeping between units (§16).
HEARTBEAT_SLICE_S = 10
DEFAULT_LOOP_SECONDS = 300

PROPOSE_BRANCH_RE = re.compile(r"harness/propose-(\d+)$")


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

    status = sub.add_parser("status", help="queue by state, budget remaining, in-flight runs")
    status.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    discover = sub.add_parser("discover", help="find work")
    discover.add_argument("--mode", required=True, choices=("triage", "directed", "audit"))
    discover.add_argument("--target", default=None, metavar="N", help="issue number for directed")
    discover.add_argument("--lens", default=None, metavar="L", help="optional triage lens")
    discover.add_argument("--ignore-allowlist", action="store_true", dest="ignore_allowlist")

    propose = sub.add_parser("propose", help="produce the work package for an item")
    propose.add_argument("item_id", type=int, metavar="item-id")

    approve = sub.add_parser("approve", help="proposed -> approved")
    approve.add_argument("item_id", type=int, metavar="item-id")
    approve.add_argument("--note", default=None, metavar="TEXT")

    run = sub.add_parser("run", help="serial loop over approved items")
    run.add_argument("--item", type=int, default=None, metavar="ID")
    run.add_argument("--session-pct", type=float, default=None, dest="session_pct", metavar="P")
    run.add_argument("--until", default=None, metavar="HH:MM")

    package = sub.add_parser("package", help="build the review package for an item")
    package.add_argument("item_id", type=int, metavar="item-id")

    archive = sub.add_parser("archive", help="promote a package into packages/")
    archive.add_argument("item_id", type=int, metavar="item-id")
    archive.add_argument("--with-transcript", action="store_true", dest="with_transcript")

    sub.add_parser("halt", help="create the halt file")
    sub.add_parser("resume", help="remove the halt file")

    # Delivery 2 (handoff §3.2)
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

    sub.add_parser("sweep", help="poll notifications, parse keywords, act on them")

    ledger = sub.add_parser("ledger", help="print spend, medians, window state")
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
    """The run window as the dispatcher names it in a reason: ``mon 08:00-tue 20:00 UTC``."""
    start = str(getattr(config, "run_window_start", "") or "")
    end = str(getattr(config, "run_window_end", "") or "")
    return f"{start}-{end} UTC" if start and end else "always open"


def _is_carried(config, item, carry: int | None) -> bool:
    """B215 routing: an item a handoff parked resumes where it stopped instead of starting
    over. Either the ledger still carries it, or its run directory holds the handoff note."""
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
    """B212-B214: park the item, say so, and let the run end cleanly (D3)."""
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
    """Create every missing `harness:*` state label in SELF_REPO. Idempotent (§14)."""
    if not ctx.gh.can_write:
        return {
            "created": [],
            "existing": [],
            "skipped": "no write credential; labels not created",
        }
    listing = ctx.gh.get(f"/repos/{config.self_repo}/labels?per_page=100")
    existing = {str(row.get("name", "")) for row in listing if isinstance(row, dict)}
    created: list[str] = []
    for state in STATES:
        name = LABELS[state]
        if name in existing:
            continue
        ctx.gh.create_label(
            config.self_repo,
            name=name,
            color="ededed",
            description=f"harness state: {state}",
        )
        created.append(name)
    return {
        "created": created,
        "existing": sorted(existing & set(LABELS.values())),
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
        # No config exists yet, so no context has set the write roots (I-8). Admit exactly
        # the file init is about to create; build_context below replaces the roots.
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
        # A doctor that crashes tells the operator nothing. Degrade and report.
        return None, f"claude --version failed: {exc}"
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if getattr(proc, "returncode", 1) != 0:
        return None, f"claude --version exited {proc.returncode}: {out[:200]}"
    return (out.splitlines()[0].strip() if out else ""), "ok"


def _probe_max_turns() -> tuple[bool, str]:
    """B70 / §3.1: --max-turns is accepted but undocumented, so it is probed, never trusted."""
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
    """A30: one entry per §6.5 key, from the config or, when it failed to load, the raw files."""
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
        # Whole-word: a typo'd WEEKLY_CAP_USDD (B112) must not also indict WEEKLY_CAP_USD.
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

    # Delivery 2: §6.5 keys (A30), .harness/HALT, .harness/PIN, the trust file.
    root = _repo_root(args, config)
    config_keys = _doctor_config_keys(args, config, config_error, problems)
    repo_halt_present = repo_halted(root)
    if repo_halt_present:
        problems.append(f".harness/HALT present under {root}")
    pin_state = _doctor_pin(root, problems)
    trust_path = config.trust_file if config is not None else root / ".harness" / "trust.txt"
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
    lines.append(f"  trust file: {len(trusted)} handle(s) ({trust_path})")
    if problems:
        lines.append("degraded:")
        lines.extend(f"  - {p}" for p in problems)
    else:
        lines.append("all checks passed")

    _emit(payload, "\n".join(lines), args)
    return EXIT_DEGRADED if problems else EXIT_OK


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


def cmd_status(args: argparse.Namespace) -> int:
    config = _load(args)
    ctx = _context(config, args, run_id="status")

    queue: dict[str, int] = {state: 0 for state in STATES}
    for item in ctx.store.list_work_items():
        queue[item.state] = queue.get(item.state, 0) + 1

    budget = {
        "weekly_remaining_pct": ctx.governor.remaining_weekly_pct(),
        "session_remaining_pct": ctx.governor.remaining_session_pct(),
        "spendable_pct": ctx.governor.spendable_pct(),
    }

    in_flight = [dataclasses.asdict(r) for r in ctx.store.list_stage_runs(status="running")]

    payload = {"queue": queue, "budget": budget, "in_flight": in_flight}

    lines = ["queue:"]
    lines.extend(f"  {state:<12} {queue[state]}" for state in STATES)
    lines.append("budget:")
    lines.append(f"  weekly remaining  {budget['weekly_remaining_pct']:.2f}%")
    lines.append(f"  session remaining {budget['session_remaining_pct']:.2f}%")
    lines.append(f"  spendable         {budget['spendable_pct']:.2f}%")
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


def cmd_approve(args: argparse.Namespace) -> int:
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
    # B149/B150: the repo-level kill switch precedes even the config load.
    check_repo_halt(_repo_root(args))
    config = _load(args)
    # B69: the kill switch is honoured before anything is selected, queue or no queue.
    check_halt(config.halt_file)
    until = _parse_hhmm(args.until) if args.until else None

    listing_ctx = _context(config, args, run_id="run")
    # B147: a crashed job cannot strand an item in a running state beyond three hours.
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
        # D3 (B209/B210): outside the run window only the carried item may start. `--item` is
        # a human at the keyboard, so it bypasses the window - and only the window: the usage
        # stops are the governor's, and every stage still passes through them.
        if not in_run_window(config, listing_ctx.clock.now()):
            window = _window_text(config)
            item_ids = [i for i in item_ids if carry is not None and int(i) == int(carry)]
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
    ctx = None

    try:
        for item_id in item_ids:
            ctx = _context(config, args, run_id=f"item-{item_id}")
            if args.session_pct is not None:
                ctx.governor.begin_session(args.session_pct)

            try:
                # implement, or continue where a handoff stopped (B215)
                check_halt(config.halt_file)
                if _past_until(until, ctx):
                    stopped_at_deadline = True
                    break
                resumed = _is_carried(config, _require_item(ctx, item_id), carry)
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

                # deliver (§14): only when a write credential exists; the branch is left for
                # the host otherwise.
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
                # D3: a usage stop, a budget stop and a rate limit are all normal outcomes,
                # not failures (B120). The item is handed off with its work committed and
                # carried in the ledger, and the run ends at 0 without starting anything else.
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
    }
    lines = [f"ran {len(ran)} item(s)"]
    lines.extend(f"  {p}" for p in packages)
    lines.extend(f"  delivered {url}" for url in delivered)
    lines.extend(f"  resumed item {i} from a handoff" for i in resumed_ids)
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
    _emit(
        {"halt_file": str(config.halt_file), "halted": False},
        f"resumed - {config.halt_file} removed",
        args,
    )
    return EXIT_OK


# --------------------------------------------------------------------------------------
# dispatch (B122 / A33)
# --------------------------------------------------------------------------------------


def _front_matter_depends_on(text: str) -> tuple[int, ...]:
    """`depends_on` from a proposal's YAML front matter (§4.3), inline or block list (I-17)."""
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


def _build_plan(ctx, config, args: argparse.Namespace) -> Plan:
    items = ctx.store.list_work_items(state="approved")
    candidates = [
        Candidate(
            issue=int(item.id),
            depends_on=_depends_on(config, item),
            stage="implement",
            created_at=str(getattr(item, "created_at", "") or ""),
        )
        for item in items
    ]
    return plan_dispatch(
        now=ctx.clock.now(),
        ledger=ctx.ledger,
        config=config,
        candidates=candidates,
        merged=ctx.store.merged_issues(),
        halted=repo_halted(_repo_root(args, config)) or halted(config.halt_file),
    )


def cmd_dispatch(args: argparse.Namespace) -> int:
    check_repo_halt(_repo_root(args))
    config = _load(args)
    ctx = _context(config, args, run_id="dispatch")
    print(_build_plan(ctx, config, args).to_json())
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
# sweep (B140 / B141 / §8.3)
# --------------------------------------------------------------------------------------


def _item_for_command(ctx, config, cmd) -> int | None:
    """Map a keyword command's thread (issue, proposal PR, delivery PR) to a work item id."""
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


def _act_on_command(ctx, config, cmd) -> str:
    """Apply one authorised keyword command (§8.3 table). Returns a one-line result."""
    reason = f"/harness {cmd.verb} by {cmd.actor}"
    if cmd.args:
        reason = f"{reason}: {cmd.args}"

    if cmd.verb == "queue":
        item = ctx.store.get_work_item(int(cmd.number))
        if item is None:
            return f"no work item {cmd.number}"
        if item.state == "discovered":
            return "already queued"
        ctx.store.transition(int(cmd.number), "discovered", reason=reason)
        return "discovered"

    if cmd.verb == "split":
        created = list(STAGES["decompose"](ctx, int(cmd.number)))
        return f"decomposed into {created}"

    item_id = _item_for_command(ctx, config, cmd)
    if item_id is None:
        return "no work item for this thread"

    if cmd.verb in ("stop", "reject"):
        repo = config.self_repo if cmd.surface == "proposal_pr" else config.upstream_repo
        closed = False
        if cmd.surface in ("proposal_pr", "delivery_pr") and ctx.gh.can_write:
            ctx.gh.close_pull(repo, cmd.number)
            closed = True
        ctx.store.transition(item_id, "abandoned", reason=reason)
        return f"item {item_id} abandoned" + ("; PR closed" if closed else "")

    if cmd.verb == "revise":
        ctx.store.transition(item_id, "proposing", reason=reason)
        spec_path = STAGES["propose"](ctx, item_id, notes=cmd.args)
        return f"item {item_id} re-proposed: {spec_path}"

    if cmd.verb == "fix":
        lease = STAGES["revise"](ctx, item_id, source="review", notes=cmd.args or "/harness fix")
        return f"item {item_id} revise(review): {'revised' if lease else 'not revised'}"

    if cmd.verb == "rebase":
        notes = cmd.args or "/harness rebase"
        lease = STAGES["revise"](ctx, item_id, source="conflict", notes=notes)
        return f"item {item_id} revise(conflict): {'revised' if lease else 'not revised'}"

    return f"unknown verb {cmd.verb}"


def cmd_sweep(args: argparse.Namespace) -> int:
    check_repo_halt(_repo_root(args))
    config = _load(args)
    check_halt(config.halt_file)
    ctx = _context(config, args, run_id="sweep")
    try:
        commands = keywords.sweep(
            ctx.gh,
            ledger=ctx.ledger,
            trusted=ctx.trusted,
            now_iso=iso(ctx.clock.now()),
            self_repo=config.self_repo,
            upstream_repo=config.upstream_repo,
        )
        for cmd in commands:
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
                record["result"] = _act_on_command(ctx, config, cmd)
            except Halted:
                raise
            except RateLimited as exc:
                record["result"] = f"rate limited until {exc.reset_at or 'unknown'}"
                print(json.dumps(record, sort_keys=False))
                break
            except HarnessError as exc:
                record["result"] = f"error: {exc}"
            print(json.dumps(record, sort_keys=False))
    finally:
        _save_ledger(ctx)
    return EXIT_OK


# --------------------------------------------------------------------------------------
# ledger (B116 / B117)
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


def cmd_ledger(args: argparse.Namespace) -> int:
    config = _load(args)
    ctx = _context(config, args, run_id="ledger")
    led = ctx.ledger
    rebuilt = False
    if getattr(args, "rebuild", False):
        led = ledger_mod.rebuild(_self_repo_comments(ctx, config))
        ledger_mod.save(led, ctx.ledger_path)
        rebuilt = True

    if _wants_json(args):
        print(led.to_json())
        return EXIT_OK

    now_iso = iso(ctx.clock.now())
    window = dict(led.window)
    lines = [f"ledger {ctx.ledger_path}" + ("  (rebuilt)" if rebuilt else "")]
    lines.append("window:")
    lines.append(f"  period_start        {window.get('period_start')}")
    lines.append(f"  spent_usd           {float(window.get('spent_usd') or 0.0):.2f}")
    lines.append(f"  calls               {int(window.get('calls') or 0)}")
    lines.append(f"  rate_limited_until  {window.get('rate_limited_until') or 'none'}")
    lines.append("observations:")
    if led.observations:
        for stage in sorted(led.observations):
            row = led.observations[stage]
            lines.append(
                f"  {stage:<12} n={int(row.get('n') or 0):<4} "
                f"median_usd={float(row.get('median_usd') or 0.0):.2f}"
            )
    else:
        lines.append("  (none)")
    limited = "yes" if led.rate_limited(now_iso) else "no"
    lines.append(f"rate limited now: {limited} (now {now_iso})")
    lines.append(f"history: {len(led.history)} entr{'y' if len(led.history) == 1 else 'ies'}")
    cursor = (led.cursors or {}).get("notifications_last_seen")
    lines.append(f"notifications cursor: {cursor or 'none'}")
    print("\n".join(lines))
    return EXIT_OK


# --------------------------------------------------------------------------------------
# sync-fork (B105 / A36)
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
    # The push callable IS the client's fast-forward-only push_ref (B105): one recorded call,
    # no force path, the token only ever inside gh.py. ForkDiverged propagates to main().
    push = functools.partial(ctx.gh.push_ref, remote_repo=config.fork_repo)
    sha = sync_fork(config, workdir=config.runs_dir / "sync-fork", push=push)
    _emit(
        {"fork": config.fork_repo, "upstream": config.upstream_repo, "sha": sha, "synced": True},
        f"fork {config.fork_repo} main at {sha} (upstream {config.upstream_repo})",
        args,
    )
    return EXIT_OK


# --------------------------------------------------------------------------------------
# local-loop (§10 / RUN-DECISIONS-D2 §16)
# --------------------------------------------------------------------------------------


def _write_heartbeat(work: Path) -> None:
    """Write `<work>/HEARTBEAT` (ISO-Z). Synchronous by design: no thread under harness/."""
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
            session_pct=None,
            until=None,
        )
        try:
            cmd_run(run_args)
        except (Halted, RepoHalted):
            raise
        except BudgetExhausted as exc:
            print(f"budget exhausted: {exc}; unit ends")
            return
        except HarnessError as exc:
            print(f"item {item_id} failed: {exc}", file=sys.stderr)
        _write_heartbeat(work)


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


COMMANDS = {
    "init": cmd_init,
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
    "ledger": cmd_ledger,
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
        # B149: the repo-level kill switch is a normal outcome, not an error.
        print("halted by .harness/HALT")
        return EXIT_OK
    except Halted as exc:
        print(f"halted: {exc}", file=sys.stderr)
        return EXIT_HALTED
    except BudgetExhausted as exc:
        print(f"budget exhausted: {exc}", file=sys.stderr)
        return EXIT_BUDGET
    except RateLimited as exc:
        # B120: the stage already returned the item to its prior state.
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
