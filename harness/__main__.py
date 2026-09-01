"""Command line entry point for the Bright Bots harness.

Every subcommand of HARNESS-SPEC §5.10 is parsed and dispatched here. The module owns
the exit-code contract and nothing else: all real work lives in the stage modules.

Exit codes
----------
0   success
1   any other ``HarnessError``
2   ``NotImplementedInDelivery1`` (``discover --mode audit``)
3   ``doctor`` degraded
4   ``BudgetExhausted``
5   ``Halted``
6   ``setup`` has outstanding prerequisites
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import shutil
import subprocess
import sys
from datetime import datetime, time as dtime
from pathlib import Path

from harness import __version__
from harness.clone import Lease
from harness.config import load_config
from harness.context import build_context
from harness.errors import (
    BudgetExhausted,
    ConfigError,
    Halted,
    HarnessError,
    NotImplementedInDelivery1,
)
from harness.halt import check_halt, disengage, engage, halted
from harness.identity import Identity, write_human_doc
from harness.packager import archive as archive_package
from harness.redact import guarded_write, set_write_roots
from harness.stages import STAGES
from harness.store import STATES

LOG = logging.getLogger("harness")

# Injectables. Tests monkeypatch these rather than the stdlib.
WHICH = shutil.which
RUN = subprocess.run

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_UNIMPLEMENTED = 2
EXIT_DEGRADED = 3
EXIT_BUDGET = 4
EXIT_HALTED = 5
EXIT_SETUP_OUTSTANDING = 6

REQUIRED_BINARIES = ("git", "claude", "node", "npm", "npx")

MAX_TURNS_PROBE_ARGV = ["claude", "-p", "--max-turns", "1", "--output-format", "json", ""]


# --------------------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harness",
        description="Bright Bots harness - discovery through review package, Tier 0.",
    )
    parser.add_argument("--config", metavar="PATH", default=None, help="path to the .env file")
    parser.add_argument("--verbose", action="store_true", help="debug logging to stderr")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--version", action="version", version=f"bright-bots-harness {__version__}")

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    sub.add_parser("init", help="create db, dirs and .env from .env.example when absent")

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


def _load(args: argparse.Namespace):
    return load_config(Path(args.config) if getattr(args, "config", None) else None)


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


# --------------------------------------------------------------------------------------
# init
# --------------------------------------------------------------------------------------


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

    ctx = build_context(config, run_id="init")
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
        "initialised",
    ]
    _emit(payload, "\n".join(lines), args)
    return EXIT_OK


# --------------------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------------------


def _probe_claude_version() -> tuple[str | None, str]:
    if WHICH("claude") is None:
        return None, "claude not on PATH"
    try:
        proc = RUN(
            ["claude", "--version"],
            capture_output=True,
            text=True,
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
    if WHICH("claude") is None:
        return False, "claude not on PATH; --max-turns unprobed"
    try:
        proc = RUN(
            list(MAX_TURNS_PROBE_ARGV),
            capture_output=True,
            text=True,
            timeout=120,
            shell=False,
        )
    except Exception as exc:
        return False, f"probe failed to run: {exc}"
    combined = ((proc.stdout or "") + (proc.stderr or "")).lower()
    if "unknown option" in combined:
        return False, "claude rejects --max-turns (unknown option)"
    return True, "claude accepts --max-turns"


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

    payload = {
        "harness_version": __version__,
        "ok": not problems,
        "binaries": dict(binaries),
        "claude_version": claude_version,
        "claude_version_detail": version_detail,
        "max_turns_probe": {"accepted": max_turns_ok, "detail": max_turns_detail},
        "disk": disk,
        "halt_present": halt_present,
        "config": {
            "valid": config_error is None,
            "error": config_error,
            "backend": None if config is None else config.backend,
            "repo": None if config is None else config.repo,
            "permission_tier": None if config is None else config.permission_tier,
        },
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
    config_word = "ok" if config_error is None else f"INVALID - {config_error}"
    lines.append(f"  config: {config_word}")
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
    ctx = build_context(config, run_id="setup")
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
    ctx = build_context(config, run_id="status")

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
    config = _load(args)
    ctx = build_context(config, run_id="discover")
    ids = STAGES["discover"](
        ctx,
        mode=args.mode,
        target=args.target,
        lens=args.lens,
        ignore_allowlist=args.ignore_allowlist,
    )
    if _wants_json(args):
        print(json.dumps({"created": list(ids)}, indent=2))
    else:
        for item_id in ids:
            print(item_id)
    return EXIT_OK


def cmd_propose(args: argparse.Namespace) -> int:
    config = _load(args)
    ctx = build_context(config, run_id=f"item-{args.item_id}")
    _require_item(ctx, args.item_id)
    spec_path = STAGES["propose"](ctx, args.item_id)
    _emit({"item_id": args.item_id, "spec_path": str(spec_path)}, str(spec_path), args)
    return EXIT_OK


def cmd_approve(args: argparse.Namespace) -> int:
    config = _load(args)
    ctx = build_context(config, run_id=f"item-{args.item_id}")
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
    config = _load(args)
    until = _parse_hhmm(args.until) if args.until else None

    listing_ctx = build_context(config, run_id="run")
    if args.item is not None:
        item = listing_ctx.store.get_work_item(args.item)
        if item is None:
            raise HarnessError(f"no work item {args.item}")
        if item.state != "approved":
            raise HarnessError(f"item {args.item} is {item.state}; run requires state approved")
        item_ids = [args.item]
    else:
        item_ids = [i.id for i in listing_ctx.store.list_work_items(state="approved")]

    if not item_ids:
        _emit({"ran": [], "packages": [], "stopped_at_deadline": False},
              "nothing approved to run", args)
        return EXIT_OK

    ran: list[int] = []
    packages: list[str] = []
    stopped_at_deadline = False

    for item_id in item_ids:
        ctx = build_context(config, run_id=f"item-{item_id}")
        if args.session_pct is not None:
            ctx.governor.begin_session(args.session_pct)

        # implement
        check_halt(config.halt_file)
        if _past_until(until, ctx):
            stopped_at_deadline = True
            break
        LOG.debug("run: implement item %s", item_id)
        lease = STAGES["implement"](ctx, item_id)

        # package
        check_halt(config.halt_file)
        if _past_until(until, ctx):
            stopped_at_deadline = True
            ran.append(item_id)
            break
        LOG.debug("run: package item %s", item_id)
        package_dir = STAGES["package"](ctx, item_id, lease)

        ran.append(item_id)
        packages.append(str(package_dir))

    payload = {"ran": ran, "packages": packages, "stopped_at_deadline": stopped_at_deadline}
    lines = [f"ran {len(ran)} item(s)"]
    lines.extend(f"  {p}" for p in packages)
    if stopped_at_deadline:
        lines.append(f"stopped: --until {args.until} reached; no new stage started")
    _emit(payload, "\n".join(lines), args)
    return EXIT_OK


# --------------------------------------------------------------------------------------
# package / archive
# --------------------------------------------------------------------------------------


def cmd_package(args: argparse.Namespace) -> int:
    config = _load(args)
    ctx = build_context(config, run_id=f"item-{args.item_id}")
    item = _require_item(ctx, args.item_id)
    lease = _lease_for(ctx, item)
    package_dir = STAGES["package"](ctx, args.item_id, lease)
    _emit({"item_id": args.item_id, "package": str(package_dir)}, str(package_dir), args)
    return EXIT_OK


def cmd_archive(args: argparse.Namespace) -> int:
    config = _load(args)
    ctx = build_context(config, run_id=f"item-{args.item_id}")
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
    except Halted as exc:
        print(f"halted: {exc}", file=sys.stderr)
        return EXIT_HALTED
    except BudgetExhausted as exc:
        print(f"budget exhausted: {exc}", file=sys.stderr)
        return EXIT_BUDGET
    except HarnessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
