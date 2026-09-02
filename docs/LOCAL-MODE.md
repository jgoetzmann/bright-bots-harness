# Local mode — the `bb` container, for an operator

Local mode runs the same harness, unattended, in a Docker container on your Windows machine. It
is the proof of concept, the debugger, and the answer to "GitHub is down / the schedule was
disabled / I want to watch it work" (handoff §10.7). It is not a second product: the code cannot
tell which mode it is in (I-16); only the store and the dispatcher differ.

**Read `local/README.md` first** for the one trap that bites everyone: editing `harness/` needs a
restart, editing `local/entrypoint.sh` or `local/Dockerfile` needs `-Build`, and nothing warns you.

## Prerequisites

- Docker Desktop running (`docker info` succeeds) — HUMAN.md item 15.
- `.env` at the repository root with `CLAUDE_CODE_OAUTH_TOKEN` set (from `claude setup-token`).
  `HARNESS_GITHUB_TOKEN` may be set too; it never enters the container.
- `.harness/PIN` present and matching the tree (`python -m harness.verify_pin --check`).
- `python local/preflight.py` prints no `FAIL`.

## The four verbs

| Do | Command | Notes |
| --- | --- | --- |
| build the image, then start | `.\bb-start.ps1 -Build` | first time, and after editing `local/entrypoint.sh` or `local/Dockerfile` |
| start (or hard-restart) | `.\bb-start.ps1` | removes a stale `bb-work\STOP`, `docker rm -f bb`, prints the image build time, restarts **its own** watchdog minimised |
| stop cleanly | `.\bb-stop.ps1` | writes `bb-work\STOP`, waits up to `-WaitMinutes 10` for the unit boundary, exit `0`, deletes `STOP`, stops the watchdog, pushes what is owed |
| stop now | `.\bb-stop.ps1 -Force` | `docker stop bb`; at most the unit in flight is lost |
| watch | `.\bb-watcher.ps1` | dedicated read-only window; `-Here` for this terminal; `-Once` for one snapshot |
| configure | `python bb-configure.py show \| explain \| set k=v [--apply] \| reset` | writes `bb-config.json`; tells you what must restart |
| logs | `docker logs -f bb` | the gate's five steps print as `gate n/5: …` |

Closing every terminal changes nothing: the container is Docker's, the watchdog is its own
minimised window. After a reboot, `.\bb-start.ps1` resumes from the same state.

## What is mounted where

| Host | Container | Mode | Holds |
| --- | --- | --- | --- |
| this repository root | `/harness` | **read-only** | the package, `prompts/`, `tests/`, `.harness/` (`PIN`, `config.json`, `trust.txt`) |
| `bb-work/` (gitignored) | `/work` | read-write | `HEARTBEAT`, `STOP`, `HALT`, `.env` (generated), `runs/`, `packages/`, `state/ledger.json`, `proposals/` |
| named volume `bb-data` | `/data` | read-write | `harness.db` — SQLite on a named volume because bind-mount locking on Windows is unreliable |

Inside the container the loop runs `python -m harness local-loop --work /work --config /work/.env`,
so `repo_root` is `/work` and every write lands on the writable mount. `local/run.ps1` generates
`/work/.env` from the filtered host `.env` on every start with the container shape forced:
`DB_PATH=/data/harness.db`, `RUNS_DIR=/work/runs`, `PACKAGES_DIR=/work/packages`,
`HALT_FILE=/work/HALT`, `TRUST_FILE=/harness/.harness/trust.txt`, `PERMISSION_TIER=0`,
`STORE_BACKEND=sqlite`, `MAX_CONCURRENT_ITEMS=1`, `MAX_CONCURRENT_CLONES=1`. `.harness/config.json`
is mirrored to `bb-work/.harness/config.json` so the P12 knobs apply in both modes.

Resources: 4 CPUs, **8 GB** memory (the limit that kills; `vite build` + `tsc` peak), 512 pids,
cpu-shares 256, network `bb-net` (plain bridge, no egress allowlist — §10.6 ruling), no host ports,
`--restart on-failure:5`.

## What the container can and cannot do

| Can | Cannot |
| --- | --- |
| read the package and prompts | write to `/harness` — the mount is read-only and the gate proves it on every start |
| clone the fork, implement on a `harness/…` branch, run the full gate sequence, commit | **push, open a PR, comment, create an issue** — it holds no GitHub credential; `PERMISSION_TIER=0` keeps the token door shut (I-11) |
| call the model with `CLAUDE_CODE_OAUTH_TOKEN` | see `HARNESS_GITHUB_TOKEN` — `local/container_env.ps1` drops it |
| write `runs/`, `packages/`, `state/`, `proposals/`, `harness.db` | change its own pin, gates, redaction or prompts |
| stop itself at a unit boundary when `STOP` appears | be signalled by anything inside — there is no Docker socket and no path back to the host |

**Publishing is the host's job.** When `deliver` runs with no write credential it leaves the branch
in the item's clone and writes `runs/<item>/DELIVER.json`. `local/watchdog-bb.ps1` — the only
publisher — pushes that branch to the fork every `watchdog.push_minutes` (and once more on
`bb-stop.ps1`) using the host's `HARNESS_GITHUB_TOKEN`, only for branches under `harness/` whose tip
author is `harness@brightboost-harness` (B139). It records the pushed sha in `runs/<item>/PUSHED`.
Opening the upstream pull request from that branch is then a human act, or Actions mode's `deliver`.

## Kill switches

- `bb-work\STOP` — graceful stop at the next unit boundary (`bb-stop.ps1` writes and removes it).
- `bb-work\HALT` — Delivery 1 semantics: every stage boundary refuses to proceed (B148).
- `.harness/HALT` on the default branch stops Actions mode (B149/B150); it does not reach a running
  container, whose package mount is whatever you checked out.

## The startup gate

`docker logs bb` shows, in this order, on every start including restart-policy retries:

```
gate 1/5: heartbeat written to /work/HEARTBEAT
gate 2/5: /harness is read-only
gate 3/5: pin verified
gate 4/5: /work, /data, /work/.env and .harness/config.json present
gate 5/5: invariants green in Ns
```

A `FATAL:` line and exit `1` at any step means the container refused to start; under
`--restart on-failure:5` it retries five times and stays down. Fix the cause, then `.\bb-start.ps1`.

## The watchdog (`local/watchdog-bb.ps1`)

| Condition | Action | Recovers |
| --- | --- | --- |
| on battery | `docker pause` | automatically on AC |
| non-container host CPU > 50 % for 30 s | `docker pause` | automatically below 30 % |
| `HEARTBEAT` older than 180 s (never within the first 180 s of uptime) | `docker kill` | automatically — the restart policy re-runs the gate |
| `state/ledger.json` spend above `WEEKLY_CAP_USD` | `docker stop` | operator |
| free disk below 5 GB | `docker stop` | operator |
| every 10 min, and on every stop | push delivered branches | — |

Pause state is read from `docker inspect` on every poll, so a restarted watchdog adopts a paused
container instead of killing it. Its parameters come from `bb-config.json` via `bb-start.ps1`.

## The acceptance checks, with their exact commands

**A44 — coexistence with `rk`.**

```powershell
Select-String -Path local\watchdog-bb.ps1 -Pattern "watchdog.ps1" | Measure-Object | Select-Object -ExpandProperty Count   # 0
docker inspect -f "{{.State.Status}}" rk                        # record it
.\bb-start.ps1; .\bb-stop.ps1
docker inspect -f "{{.State.Status}}" rk                        # identical
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*watchdog*" } | Select-Object ProcessId, CommandLine   # rk's watchdog still there
```

The review's literal form is `grep -c "watchdog.ps1" local/watchdog-bb.ps1` → `0` (R9.12).

**A45 — the gate fails closed at steps 2, 3 and 5.**

```powershell
# step 2: writable package mount -> FATAL, exit 1
docker run --rm -v ${PWD}:/harness -v ${PWD}/bb-work:/work -v bb-data:/data bb-harness:latest; echo "exit $LASTEXITCODE"
# step 3: corrupt the pin -> exit 1
Copy-Item .harness\PIN .harness\PIN.bak; Set-Content .harness\PIN ("0" * 64); .\bb-start.ps1; Start-Sleep 20
docker inspect -f "{{.State.ExitCode}}" bb; Move-Item .harness\PIN.bak .harness\PIN -Force
# step 5: break an invariant test -> exit 1
Add-Content tests\test_invariants.py "`ndef test_bb_break(): assert False"; .\bb-start.ps1; Start-Sleep 30
docker inspect -f "{{.State.ExitCode}}" bb; git checkout -- tests\test_invariants.py
```

**A46 — no GitHub token inside; the drop count is non-zero.**

```powershell
.\local\container_env.ps1                                        # prints "dropped N line(s)", N > 0
docker exec bb env | Select-String GITHUB | Measure-Object | Select-Object -ExpandProperty Count   # 0
docker exec bb sh -c 'test -n "$CLAUDE_CODE_OAUTH_TOKEN" && echo present'                          # present
docker exec bb python -c "import harness.gh"                     # imports; any write raises: no credential
```

The review's literal forms: `docker exec bb env | grep -c GITHUB` → `0` (R5.7) and the `sh -c` test
→ `present` (R5.8).

**R9 — backgrounded, in order.**

```powershell
.\bb-start.ps1 -Build                                   # R9.1: running; docker logs bb shows gate 1/5..5/5
docker inspect bb --format "{{.HostConfig.Binds}}"      # R9.2: /harness:ro, /work, bb-data:/data only
.\bb-watcher.ps1 -Once                                  # R9.6: heartbeat age < 30 s
.\bb-stop.ps1                                           # R9.8: exited (0) within 10 min; STOP gone
.\bb-start.ps1                                          # R9.9: resumes; harness.db on bb-data is intact
```

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| container exits `0` seconds after start, no work | stale `bb-work\STOP` | `.\bb-start.ps1` removes it; check nothing else recreates it |
| the gate prints a step you removed from `entrypoint.sh` | stale image | `.\bb-start.ps1 -Build` — compare the printed build time to your edit |
| `FATAL: /harness is writable` | mount lost `:ro` | run through `bb-start.ps1`, never a hand-typed `docker run` |
| `FATAL: pin mismatch` | a pinned file changed | that is a reviewed PR touching `.harness/PIN` (B143); never edit the pin by hand to make it pass |
| exit `137` | out of memory | raise `container.memory_gb`; it is the limit that kills |
| killed every ~3 min during implement | heartbeat sidecar not running — old image | `-Build`; check `docker logs bb` for `gate 5/5` then the loop's own lines |
| branches never appear on the fork | no watchdog | `.\bb-watcher.ps1 -Once` shows `watchdog: ABSENT`; `.\bb-start.ps1` restarts it |
| `push FAILED … 403` | `HARNESS_GITHUB_TOKEN` cannot write the fork | the machine account must own the fork (HUMAN.md items 2–5) |
