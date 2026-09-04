# local/ — the bb container control plane

## READ THIS FIRST: rebuild vs restart

The package (`harness/`, `prompts/`, `tests/`, `.harness/`) is **mounted** read-only at `/harness`.
`entrypoint.sh` and the `Dockerfile` are **baked into the image**. They have different deployment
procedures, and nothing warns you when you use the wrong one:

| You edited | You must run | A plain restart does |
| --- | --- | --- |
| anything under `harness/`, `prompts/`, `tests/`, `.harness/` | `.\bb-start.ps1` | the right thing |
| `local/entrypoint.sh` or `local/Dockerfile` | `.\bb-start.ps1 -Build` | **silently keeps running the old gate** |

`bb-start.ps1` prints the image's build timestamp on every start. If that timestamp is older than
your last edit to `entrypoint.sh` or the `Dockerfile`, the container is running the old one.
Rebuild.

## Instantiation (handoff §10.1; platform §1)

| Parameter | Value |
| --- | --- |
| Short id | `bb` |
| Container / image | `bb` / `bb-harness:latest` |
| Package, mounted `:ro` at `/harness` | this repository root |
| Work dir, bind-mounted at `/work` | `bb-work/` (gitignored; created by `bb-start.ps1`) |
| Named volume | `bb-data` → `/data`, holding `harness.db` |
| Env prefix | `BB_*` (`BB_WORK_DIR`, `BB_LOOP_SECONDS`) — read by `entrypoint.sh`, never by Python (I-4) |
| Network | `bb-net`, plain bridge — no egress allowlist, by ruling (§10.6) |
| Workspace scripts | `bb-start.ps1`, `bb-stop.ps1`, `bb-watcher.ps1`, `bb-configure.py`, `bb-config.json` (repo root) |
| Watchdog | `local/watchdog-bb.ps1` — never contains the substring rk's wildcard kill matches (A44) |
| Base image | `python:3.13-slim-bookworm` + Node 20.18.1 + `@anthropic-ai/claude-code@2.1.257` + `pytest==8.3.4` |
| CPUs / memory | 4 / **8 GB** (`vite build` + `tsc` peak; `--memory` kills at 137, so headroom is cheap) |
| Heartbeat staleness | 180 s |
| Host ports | none |
| Restart policy | `on-failure:5` — a kill self-heals; a clean exit or `docker stop` stays down |

## Files

| File | Role |
| --- | --- |
| `Dockerfile` | the image: base + pinned Node, claude CLI, pytest; `COPY`s only `entrypoint.sh` (P1). Build context is this directory, so copying the package is impossible |
| `entrypoint.sh` | the gate, in the frozen §10.3 order; `exec`s `python -m harness --config /work/.env local-loop --work /work --loop-seconds $BB_LOOP_SECONDS` |
| `run.ps1` | the `docker run` contract (§5.3): network, volume, `docker rm -f bb`, the credential filter, `/work/.env`, the flags |
| `watchdog-bb.ps1` | host-side policing (§6.5) and the **only publisher**: pushes delivered branches every `push_minutes`, always under a `--force-with-lease` against the sha in `runs/<item>/PUSHED` |
| `container_env.ps1` | the credential filter (P4/P5): drops `HARNESS_GITHUB_TOKEN`, prints `dropped N line(s)` |
| `preflight.py` | host-side checks before the first start; stdlib only. Also holds the six cross-file invariants no Python test can see (see below) |

## `DELIVER.json`, the one manifest the watchdog parses

The container's `deliver` stage holds no GitHub credential, so it leaves the branch in its clone
and writes `runs/item-<id>/DELIVER.json`. That file is the whole contract between the two halves:
`harness/stages/deliver.py`'s `_write_record` is the only producer, `watchdog-bb.ps1`'s
`Push-Delivered` the only consumer, and they are in different languages, so no test sees both.
The watchdog reads exactly two of its keys:

| Key | Used for | If empty |
| --- | --- | --- |
| `branch` | what to push; must be under `harness/` or the watchdog refuses | the item is skipped |
| `fork_repo` | where to push it | falls back to `FORK_REPO` in the host `.env` (the container's copy is blank when the fork is unset) |

The clone is **not** in the record and is not guessed: it is always `<run dir>/clone`, the run dir
being the one holding the manifest — the same path `clone.py`, `deliver.py` and `revise.py` build.
A path from the record would be a container path (`/work/...`) and useless on the host anyway.

The watchdog used to probe `branch_name`, `remote_repo`, `clone_path`, `clone` and `workdir` ahead
of these, and `_write_record` has never written one of them: each read `$null` and fell through to
the candidate below it, so the fallback chains read like tolerance for several manifest versions
while exactly one shape was ever parsed. `preflight.py`'s `check_manifest_keys_agree` now asserts
that every `$d.<key>` in the watchdog is a key the producer writes, so the two cannot drift again.

## The gate (`entrypoint.sh`), frozen order

1. write `/work/HEARTBEAT` — before anything else, so the watchdog's grace window starts fresh
2. probe `/harness` for writability — writable → `FATAL`, `exit 1`
3. `python -m harness.verify_pin --check` against `/harness/.harness/PIN` — mismatch → `exit 1`
4. `/work`, `/data`, `/work/.env`, `/harness/.harness/config.json` exist; the JSON parses; `BB_LOOP_SECONDS` is a whole number of seconds (argparse would otherwise reject it at step 6, after the gate has already passed)
5. `python -m pytest -q -p no:cacheprovider -o cache_dir=/tmp/pc tests/test_invariants.py` — red → `exit 1`; timed, warns past 10 s
6. `exec python -m harness --config /work/.env local-loop --work /work --loop-seconds "$BB_LOOP_SECONDS"`

`--config` is a **global** flag on the top-level parser, so it must precede the subcommand;
argparse rejects it after `local-loop` and the container dies at `exec` with exit 2, burning all
five restarts on a container that passed every gate. `--loop-seconds` is `local-loop`'s own flag —
it is how `bb-config.json`'s `run.loop_seconds` reaches the loop, because nothing under `harness/`
reads a `BB_*` variable and nothing may (I-4 confines `os.environ` to `config.py`). A `BB_*` knob
is live only when this line forwards it; there is exactly one. Per-unit item count is not a knob:
it is the dispatcher's `MAX_CONCURRENT_ITEMS`, pinned to 1 in local mode by `run.ps1` (B123).

Between 5 and 6 the entrypoint starts a `sh` sidecar that rewrites `HEARTBEAT` every 10 s. It dies
with PID 1, so it guards process death exactly as a daemon thread would — and nothing under
`harness/` may start a thread (RUN-DECISIONS-D2 §17).

### A45 — prove each step fails closed

```powershell
# step 2: mount the package writable, expect exit 1 and the FATAL line
docker run --rm -v ${PWD}:/harness -v ${PWD}/bb-work:/work -v bb-data:/data bb-harness:latest; echo "exit $LASTEXITCODE"

# step 3: corrupt the pin, restart, expect exit 1
Copy-Item .harness\PIN .harness\PIN.bak; Set-Content .harness\PIN "0000000000000000000000000000000000000000000000000000000000000000"
.\bb-start.ps1; Start-Sleep 20; docker inspect -f "{{.State.ExitCode}}" bb     # 1
Move-Item .harness\PIN.bak .harness\PIN -Force

# step 5: break an invariant test, restart, expect exit 1
Add-Content tests\test_invariants.py "`ndef test_bb_break(): assert False"
.\bb-start.ps1; Start-Sleep 30; docker inspect -f "{{.State.ExitCode}}" bb     # 1
git checkout -- tests\test_invariants.py
```

Under `--restart on-failure:5` a failing gate retries five times, then stays down; `docker logs bb`
shows the same FATAL line each time.

## What reaches the container

| Thing | Path | Mode |
| --- | --- | --- |
| the package | `/harness` | read-only |
| `bb-work/` | `/work` | read-write — `HEARTBEAT`, `STOP`, `HALT`, `.env`, `runs/`, `packages/`, `state/ledger.json`, `proposals/` |
| `bb-data` volume | `/data` | read-write — `harness.db` |
| `CLAUDE_CODE_OAUTH_TOKEN` | process environment | from the filter's output only |
| `HARNESS_GITHUB_TOKEN` | **nowhere** | dropped by `container_env.ps1`; the host watchdog pushes |

`run.ps1` writes `/work/.env` from the filtered host `.env` with the container shape forced:
`DB_PATH=/data/harness.db`, `RUNS_DIR=/work/runs`, `PACKAGES_DIR=/work/packages`,
`HALT_FILE=/work/HALT`, `TRUST_FILE=/harness/.harness/trust.txt`, `PERMISSION_TIER=0`,
`STORE_BACKEND=sqlite`, `MAX_CONCURRENT_ITEMS=1`, `MAX_CONCURRENT_CLONES=1`. `repo_root` inside
the container is therefore `/work`, so every write the harness makes lands on the writable mount.
`.harness/config.json` is mirrored to `bb-work/.harness/config.json` on every start so the P12
knobs apply identically in both modes.

The process environment is deliberately *not* the whole `.env`: `docker exec bb env | grep -c GITHUB`
must print `0` (A46), and the harness's own `GITHUB_API_CEILING_PER_HOUR` would otherwise match.
`load_config` reads `/work/.env` as a file, so nothing is lost.

## Coexistence with rk (A44)

rk's scripts kill every PowerShell process whose command line matches `*watchdog.ps1*`. This
harness's watchdog is `watchdog-bb.ps1`, which that wildcard cannot match, and `bb-start.ps1`,
`bb-stop.ps1` and `bb-watcher.ps1` find it by its **full path** only. Container name, image,
network, volume, work dir, `STOP` path and env prefix are all `bb`-specific. Starting and stopping
`bb` must leave `rk`'s container status and watchdog process identical — check with
`docker inspect -f "{{.State.Status}}" rk` before and after.
