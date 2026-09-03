#!/bin/sh
# Container entrypoint - the gate, in the FROZEN order of DELIVERY-2-HANDOFF.md section 10.3
# (platform section 5.2). Baked into the image (P3); the package it gates is mounted read-only at
# /harness (P1). Hand-written; never machine-generated.
#   1. HEARTBEAT immediately          4. /work, /data, /work/.env, .harness/config.json exist and parse
#   2. /harness must be read-only     5. the fast test subset: tests/test_invariants.py, under ~10 s
#   3. pinned hash check (B142)       6. exec the loop
# Every step fails closed with exit 1 (A45). The gate runs on every start, restarts included.
set -eu

HARNESS="${BB_HARNESS_DIR:-/harness}"
WORK="${BB_WORK_DIR:-/work}"
DATA="${BB_DATA_DIR:-/data}"

# 1. Heartbeat first. The gate below can outlast the watchdog's staleness window, and the file left
#    on disk by the previous run predates this start; the host must never mistake gate time for a
#    hang (rk once had a fresh container killed 14 s after start for exactly this reason).
date -u +"%Y-%m-%dT%H:%M:%SZ" > "$WORK/HEARTBEAT" 2>/dev/null || true
echo "gate 1/5: heartbeat written to $WORK/HEARTBEAT"

# 2. The package mount must be read-only. This is the load-bearing control: a hash computed at
#    startup cannot see an edit made an hour later, but a read-only mount prevents one.
if touch "$HARNESS/.rw-probe" 2>/dev/null; then
  rm -f "$HARNESS/.rw-probe"
  echo "FATAL: $HARNESS is writable; mount it read-only (-v <repo>:/harness:ro)" >&2
  exit 1
fi
echo "gate 2/5: $HARNESS is read-only"

cd "$HARNESS" || { echo "FATAL: cannot cd to $HARNESS" >&2; exit 1; }

# 3. Pinned SHA-256 over gates.py, packager.py, redact.py and prompts/ against .harness/PIN. A
#    mismatch means recorded results would not mean what the pin says they mean; refuse to start.
python -m harness.verify_pin --check || { echo "FATAL: pin mismatch against $HARNESS/.harness/PIN; refusing to start" >&2; exit 1; }
echo "gate 3/5: pin verified"

# 4. Required paths and config exist and parse.
[ -d "$WORK" ] || { echo "FATAL: $WORK missing (bind-mount bb-work/ at /work)" >&2; exit 1; }
[ -d "$DATA" ] || { echo "FATAL: $DATA missing (named volume bb-data at /data)" >&2; exit 1; }
[ -f "$WORK/.env" ] || { echo "FATAL: $WORK/.env missing (local/run.ps1 writes it from the filtered host .env)" >&2; exit 1; }
[ -f "$HARNESS/.harness/config.json" ] || { echo "FATAL: $HARNESS/.harness/config.json missing" >&2; exit 1; }
python -c 'import json, sys; json.load(open(sys.argv[1], encoding="utf-8"))' "$HARNESS/.harness/config.json" \
  || { echo "FATAL: $HARNESS/.harness/config.json does not parse" >&2; exit 1; }
echo "gate 4/5: $WORK, $DATA, $WORK/.env and .harness/config.json present"

# The loop commits (never pushes) inside clones under /work, which the host user owns: git needs an
# identity and must trust the mounted directories. The author is the harness identity, which is
# the only tip the host watchdog will push (B139).
git config --global user.email "harness@brightboost-harness" 2>/dev/null || true
git config --global user.name "jgoetzmann-bot" 2>/dev/null || true
git config --global --add safe.directory '*' 2>/dev/null || true

# 5. The fast test subset: the invariant tests (I-1..I-17, including the gate-sequence golden).
#    Any failure -> exit 1. Timed, so a creeping gate is visible in the log.
start=$(date +%s)
python -m pytest -q -p no:cacheprovider -o cache_dir=/tmp/pc tests/test_invariants.py \
  || { echo "FATAL: tests/test_invariants.py failed; refusing to run" >&2; exit 1; }
elapsed=$(( $(date +%s) - start ))
echo "gate 5/5: invariants green in ${elapsed}s"
[ "$elapsed" -le 10 ] || echo "WARN: the gate took ${elapsed}s; keep it under 10 s (platform section 5.2)" >&2

# Heartbeat sidecar (P7: once before the gate, then every 10 s). The loop writes HEARTBEAT at each
# unit boundary but may spend many minutes inside one unit, and nothing under harness/ may start a
# thread (RUN-DECISIONS-D2 section 17). This shell loop dies with PID 1, so it guards exactly what a
# daemon thread would: process death, not slow work.
( while :; do sleep 10; date -u +"%Y-%m-%dT%H:%M:%SZ" > "$WORK/HEARTBEAT" 2>/dev/null || true; done ) &

# 6. exec: the loop is PID 1 and receives signals directly. STOP is polled at each unit boundary
#    (P8); a clean exit 0 stays down under --restart on-failure:5.
exec python -m harness local-loop --work "$WORK" --config "$WORK/.env" "$@"
