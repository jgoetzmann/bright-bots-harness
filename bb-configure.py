#!/usr/bin/env python3
"""Edit local mode's operational settings (bb-config.json next to this file).

    python bb-configure.py show                      current values
    python bb-configure.py explain                   every key, meaning, range, and what must restart
    python bb-configure.py set run.loop_seconds=120 container.cpus=6
    python bb-configure.py reset [key ...]           back to the defaults (all keys, or the given ones)
    python bb-configure.py set ... --apply           also bb-stop.ps1 + bb-start.ps1 so the change takes effect

Keys are dotted: <section>.<name>. Values are parsed as JSON where possible (true/false/null,
numbers), otherwise taken as strings. The file is written atomically. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "bb-config.json"

# key -> (default, type, (min, max) or choices, restart, help)
# A key belongs here only when a reader exists. `run.max_items_per_unit` sat here with none: it
# reached the container as BB_MAX_ITEMS_PER_UNIT, no Python read a BB_ variable (I-4 confines
# os.environ to config.py), and `local-loop` has no such flag - so it printed a restart hint and
# the watcher showed it live while the loop ignored it. Per-unit item count is the dispatcher's
# MAX_CONCURRENT_ITEMS, pinned to 1 in local mode by local/run.ps1's overrides (B123).
SCHEMA: dict[str, tuple] = {
    "container.cpus":             (4, float, (0.5, 64), "container", "CPU cores for the container (docker --cpus)"),
    "container.memory_gb":        (8, float, (1, 512), "container", "memory limit in GB (docker --memory); this one KILLS (exit 137) - vite build + tsc peak on the product repo, so 8 not 6"),
    "container.pids_limit":       (512, int, (64, 65536), "container", "max processes in the container (fork-bomb guard)"),
    "container.cpu_shares":       (256, int, (2, 262144), "container", "CPU weight vs other processes; 1024 = default, 256 = a quarter"),
    "run.loop_seconds":           (300, int, (10, 86400), "container", "seconds the loop sleeps between units when the dispatcher starts nothing (BB_LOOP_SECONDS -> --loop-seconds)"),
    "watchdog.poll_seconds":      (10, int, (2, 600), "watchdog", "how often the host watchdog checks everything"),
    "watchdog.heartbeat_stale_seconds": (180, int, (30, 3600), "watchdog", "docker kill when HEARTBEAT is older than this (also the startup grace period)"),
    "watchdog.min_free_gb":       (5.0, float, (0.5, 1000), "watchdog", "docker stop when free disk on the work drive drops below this"),
    "watchdog.push_minutes":      (10, int, (1, 1440), "watchdog", "push delivered branches from the host this often (the watchdog is the only publisher)"),
    "watchdog.battery_guard":     (True, bool, None, "watchdog", "pause the container while the laptop is on battery"),
    "watchdog.cpu_pause_high_percent": (50, int, (5, 100), "watchdog", "pause when non-container host CPU stays above this"),
    "watchdog.cpu_pause_low_percent":  (30, int, (0, 99), "watchdog", "unpause when host CPU stays below this"),
    "watchdog.cpu_pause_sustain_seconds": (30, int, (5, 3600), "watchdog", "how long the CPU condition must hold before pausing/unpausing"),
    "watcher.refresh_seconds":    (5, int, (1, 300), "watcher", "watcher window refresh interval"),
    "watcher.events_tail":        (25, int, (5, 200), "watcher", "how many recent events / log lines the watcher shows"),
}
RESTART_HINT = {
    "container": "takes effect on the next start: python bb-configure.py ... --apply  (or .\\bb-stop.ps1 then .\\bb-start.ps1)",
    "watchdog": "takes effect when the watchdog restarts (.\\bb-start.ps1 restarts it)",
    "watcher": "takes effect when the watcher window restarts (.\\bb-watcher.ps1)",
}


def defaults() -> dict:
    out: dict = {}
    for key, (default, *_rest) in SCHEMA.items():
        sec, name = key.split(".", 1)
        out.setdefault(sec, {})[name] = default
    return out


def load() -> dict:
    cfg = defaults()
    if CONFIG.exists():
        try:
            data = json.loads(CONFIG.read_text(encoding="utf-8"))
        except ValueError as e:
            sys.exit(f"bb-config.json is not valid JSON: {e}")
        for sec, vals in data.items():
            if sec.startswith("_") or not isinstance(vals, dict):
                continue
            for name, v in vals.items():
                if f"{sec}.{name}" in SCHEMA:
                    cfg.setdefault(sec, {})[name] = v
    return cfg


def save(cfg: dict) -> None:
    body = {"_comment": "Operational settings for local mode (the bb container). Edit with bb-configure.py (python bb-configure.py explain). "
                        "Anything whose change would alter what the harness concludes - the gate sequence, redaction, the proposal schema, "
                        "the pinned files - is NOT here on purpose (B112, platform P12): that is a code change, reviewed as one."}
    body.update(cfg)
    tmp = CONFIG.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, CONFIG)


def coerce(key: str, raw) -> object:
    default, typ, rng, _restart, _help = SCHEMA[key]
    if isinstance(raw, str):
        try:
            val = json.loads(raw)
        except ValueError:
            val = raw
    else:
        val = raw
    if typ == "int_or_null":
        if val is None or val == "null":
            return None
        if isinstance(val, bool) or not isinstance(val, (int, float)) or int(val) != val:
            raise ValueError(f"{key} must be an integer or null")
        val = int(val)
        lo, hi = rng
        if not lo <= val <= hi:
            raise ValueError(f"{key} must be between {lo} and {hi}")
        return val
    if typ is bool:
        if isinstance(val, bool):
            return val
        if isinstance(val, str) and val.lower() in ("true", "yes", "on", "1"):
            return True
        if isinstance(val, str) and val.lower() in ("false", "no", "off", "0"):
            return False
        raise ValueError(f"{key} must be true or false")
    if typ is int:
        if isinstance(val, bool) or not isinstance(val, (int, float)) or int(val) != val:
            raise ValueError(f"{key} must be an integer")
        val = int(val)
    elif typ is float:
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            raise ValueError(f"{key} must be a number")
        val = float(val)
        if val == int(val):
            val = int(val)
    elif typ is str:
        val = str(val)
    if isinstance(rng, tuple) and typ in (int, float):
        lo, hi = rng
        if not lo <= val <= hi:
            raise ValueError(f"{key} must be between {lo} and {hi}")
    if isinstance(rng, tuple) and typ is str and val not in rng:
        raise ValueError(f"{key} must be one of {', '.join(rng)}")
    if key == "watchdog.cpu_pause_low_percent" or key == "watchdog.cpu_pause_high_percent":
        pass
    return val


def get(cfg: dict, key: str):
    sec, name = key.split(".", 1)
    return cfg[sec][name]


def put(cfg: dict, key: str, val) -> None:
    sec, name = key.split(".", 1)
    cfg.setdefault(sec, {})[name] = val


def cmd_show(cfg: dict) -> None:
    width = max(len(k) for k in SCHEMA)
    for key in SCHEMA:
        val = get(cfg, key)
        default = SCHEMA[key][0]
        mark = "" if val == default else f"   (default {json.dumps(default)})"
        print(f"{key.ljust(width)}  {json.dumps(val)}{mark}")
    print(f"\nfile: {CONFIG}")


def cmd_explain() -> None:
    for key, (default, typ, rng, restart, help_) in SCHEMA.items():
        if isinstance(rng, tuple) and typ in (int, float, "int_or_null"):
            span = f"{rng[0]}..{rng[1]}"
        elif isinstance(rng, tuple):
            span = "|".join(rng)
        else:
            span = {bool: "true|false", str: "text"}.get(typ, "")
        print(f"{key}\n    {help_}\n    default {json.dumps(default)}   allowed {span}\n    {RESTART_HINT[restart]}")


def cmd_set(cfg: dict, pairs: list[str]) -> set[str]:
    touched: set[str] = set()
    for pair in pairs:
        if "=" not in pair:
            sys.exit(f"expected key=value, got {pair!r}")
        key, raw = pair.split("=", 1)
        key = key.strip()
        if key not in SCHEMA:
            sys.exit(f"unknown key {key!r}; run: python bb-configure.py explain")
        try:
            val = coerce(key, raw.strip())
        except ValueError as e:
            sys.exit(str(e))
        put(cfg, key, val)
        touched.add(SCHEMA[key][3])
        print(f"{key} = {json.dumps(val)}")
    low, high = cfg["watchdog"]["cpu_pause_low_percent"], cfg["watchdog"]["cpu_pause_high_percent"]
    if low >= high:
        sys.exit(f"watchdog.cpu_pause_low_percent ({low}) must be below cpu_pause_high_percent ({high})")
    save(cfg)
    for r in sorted(touched):
        print(f"-> {RESTART_HINT[r]}")
    return touched


def cmd_reset(cfg: dict, keys: list[str]) -> None:
    if not keys:
        save(defaults())
        print("all keys reset to defaults")
        return
    for key in keys:
        if key not in SCHEMA:
            sys.exit(f"unknown key {key!r}")
        put(cfg, key, SCHEMA[key][0])
        print(f"{key} = {json.dumps(SCHEMA[key][0])}")
    save(cfg)


def apply() -> None:
    ps = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
    print("stopping (graceful, at the next unit boundary) ...")
    subprocess.run(ps + [str(ROOT / "bb-stop.ps1")], check=False)
    print("starting ...")
    subprocess.run(ps + [str(ROOT / "bb-start.ps1")], check=False)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("show")
    sub.add_parser("explain")
    p_set = sub.add_parser("set")
    p_set.add_argument("pairs", nargs="+")
    p_set.add_argument("--apply", action="store_true", help="bb-stop.ps1 + bb-start.ps1 afterwards")
    p_reset = sub.add_parser("reset")
    p_reset.add_argument("keys", nargs="*")
    p_reset.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    cfg = load()
    if args.cmd in (None, "show"):
        cmd_show(cfg)
    elif args.cmd == "explain":
        cmd_explain()
    elif args.cmd == "set":
        cmd_set(cfg, args.pairs)
        if args.apply:
            apply()
    elif args.cmd == "reset":
        cmd_reset(cfg, args.keys)
        if args.apply:
            apply()
    return 0


if __name__ == "__main__":
    sys.exit(main())
