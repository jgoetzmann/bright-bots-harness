# Local mode: the `bb` container

Local mode runs the same harness, unattended, in a Docker container on a Windows machine: to
watch it work, to debug it, or to keep working when Actions is unavailable. The code cannot tell
which mode it is in (I-16); the store, the tier and the publisher are what differ.

Read the rebuild-or-restart table in [local/README.md](../local/README.md) before editing
anything: editing `harness/` needs a restart, editing `local/entrypoint.sh` or `local/Dockerfile`
needs `-Build`, and nothing warns you.

## Prerequisites

- Docker Desktop running (`docker info` succeeds).
- `.env` at the repository root with `CLAUDE_CODE_OAUTH_TOKEN` set (from `claude setup-token`).
  `HARNESS_GITHUB_TOKEN` may be set too; it never enters the container.
- `.harness/PIN` matching the tree (`python -m harness.verify_pin --check`).
- `python local/preflight.py` prints no `FAIL`.

## Commands

| Do | Command | Notes |
| --- | --- | --- |
| build the image, then start | `.\bb-start.ps1 -Build` | the first time, and after editing `local/entrypoint.sh` or `local/Dockerfile` |
| start (or hard-restart) | `.\bb-start.ps1` | removes a stale `bb-work\STOP`, runs `docker rm -f bb`, prints the image build time, restarts its own watchdog minimised |
| stop cleanly | `.\bb-stop.ps1` | writes `bb-work\STOP`, waits up to `-WaitMinutes 10` for the unit boundary, deletes `STOP`, stops the watchdog, pushes what is owed |
| stop now | `.\bb-stop.ps1 -Force` | `docker stop bb`; at most the unit in flight is lost |
| watch | `.\bb-watcher.ps1` | a read-only window; `-Here` for this terminal, `-Once` for one snapshot |
| configure | `python bb-configure.py show \| explain \| set k=v [--apply] \| reset` | writes `bb-config.json` and says what must restart |
| logs | `docker logs -f bb` | the gate's steps print as `gate n/5: …` |

Closing every terminal changes nothing: the container belongs to Docker and the watchdog runs in
its own minimised window. After a reboot, `.\bb-start.ps1` resumes from the same state.

## The container

| Setting | Value |
| --- | --- |
| Container / image | `bb` / `bb-harness:latest`: `python:3.13-slim-bookworm`, Node 20.18.1, `@anthropic-ai/claude-code@2.1.257`, `pytest==8.3.4` |
| Resources | 4 CPUs; 8 GB memory, the limit that kills (`vite build` plus `tsc` peak); 512 pids; cpu-shares 256 |
| Network | `bb-net`, a plain bridge with no egress allowlist; no host ports |
| Restart policy | `on-failure:5`: a kill self-heals, a clean exit or `docker stop` stays down |
| Env prefix | `BB_*`, read by `entrypoint.sh` and never by Python (I-4) |

| Host | Container | Mode | Holds |
| --- | --- | --- | --- |
| this repository root | `/harness` | read-only | the package, `prompts/`, `tests/`, `.harness/` |
| `bb-work/` (gitignored) | `/work` | read-write | `HEARTBEAT`, `STOP`, `HALT`, `.env` (generated), `runs/`, `packages/`, `state/ledger.json`, `proposals/` |
| named volume `bb-data` | `/data` | read-write | `harness.db`; SQLite locking on a Windows bind mount is unreliable |

Inside the container the loop runs
`python -m harness --config /work/.env local-loop --work /work --loop-seconds "$BB_LOOP_SECONDS"`,
so `repo_root` is `/work` and every write lands on the writable mount. `--config` is a global
flag and must come before the subcommand; after `local-loop`, argparse exits 2 and the container
dies having passed every gate. `BB_LOOP_SECONDS` carries `bb-config.json`'s `run.loop_seconds`,
and `entrypoint.sh` passes it on as `--loop-seconds`: it is the only `BB_*` knob the loop obeys.
The number of items per unit is `MAX_CONCURRENT_ITEMS` (B123).

`local/run.ps1` writes `/work/.env` on every start from the filtered host `.env`, with the
container shape forced: `DB_PATH=/data/harness.db`, `RUNS_DIR=/work/runs`,
`PACKAGES_DIR=/work/packages`, `HALT_FILE=/work/HALT`, `TRUST_FILE=/harness/.harness/trust.txt`,
`PERMISSION_TIER=0`, `STORE_BACKEND=sqlite` and `MAX_CONCURRENT_ITEMS=1`. It mirrors
`.harness/config.json` to `bb-work/.harness/config.json`,
so the knobs apply the same way in both modes. The process environment carries only
`CLAUDE_CODE_OAUTH_TOKEN`, `DB_PATH` and the `BB_*` settings, so `docker exec bb env` shows no
variable naming `GITHUB`; `load_config` reads everything else from `/work/.env`.

## What the container can and cannot do

| Can | Cannot |
| --- | --- |
| read the package and prompts | write to `/harness`: the mount is read-only and the gate checks it on every start |
| clone the fork, implement on a `harness/…` branch, run the gate sequence, commit | push, open a PR, comment or create an issue: it holds no GitHub credential, and `PERMISSION_TIER=0` keeps the token door shut (I-11) |
| call the model with `CLAUDE_CODE_OAUTH_TOKEN` | see `HARNESS_GITHUB_TOKEN`, which `local/container_env.ps1` drops |
| write `runs/`, `packages/`, `state/`, `proposals/`, `harness.db` | change its own pin, gates, redaction or prompts |
| stop itself at a unit boundary when `STOP` appears | reach the host: there is no Docker socket inside |

## The startup gate

`local/entrypoint.sh` runs these steps in order on every start, restarts included. Each one fails
closed with a `FATAL:` line and exit 1:

1. write `/work/HEARTBEAT`, so the watchdog's staleness clock starts fresh;
2. refuse a writable `/harness`;
3. `python -m harness.verify_pin --check` against `/harness/.harness/PIN`;
4. check that `/work`, `/data`, `/work/.env` and `/harness/.harness/config.json` exist, that the
   JSON parses, and that `BB_LOOP_SECONDS` is a whole number of seconds;
5. `python -m pytest -q tests/test_invariants.py`, timed, with a warning past 10 s;
6. `exec` the loop.

Between steps 5 and 6 a `sh` sidecar starts rewriting `HEARTBEAT` every 10 s; it dies with PID 1.
Under `on-failure:5` a failing gate retries five times and then stays down. To see a step fail
closed:

```powershell
# step 2: a writable package mount -> FATAL, exit 1
docker run --rm -v ${PWD}:/harness -v ${PWD}/bb-work:/work -v bb-data:/data bb-harness:latest
# step 3: a corrupt pin -> exit 1
Copy-Item .harness\PIN .harness\PIN.bak; Set-Content .harness\PIN ("0" * 64)
.\bb-start.ps1; Start-Sleep 20; docker inspect -f "{{.State.ExitCode}}" bb
Move-Item .harness\PIN.bak .harness\PIN -Force
# step 5: a failing invariant test -> exit 1
Add-Content tests\test_invariants.py "`ndef test_bb_break(): assert False"
.\bb-start.ps1; Start-Sleep 30; docker inspect -f "{{.State.ExitCode}}" bb
git checkout -- tests\test_invariants.py
```

## Publishing

`deliver` holds no write credential in the container, so it leaves the branch in the item's clone
and writes `runs/item-<id>/DELIVER.json`. `local/watchdog-bb.ps1` is the only publisher: every
`watchdog.push_minutes`, and once more on `bb-stop.ps1`, it pushes that branch to the fork with
the host's `HARNESS_GITHUB_TOKEN`. It pushes only branches under `harness/` whose tip author is
`harness@brightboost-harness` (B139), refuses a branch carrying a harness commit that touches
`.github/` (B304), and records the pushed sha in `runs/<item>/PUSHED`. Opening the upstream pull
request from that branch is then a person's job, or Actions mode's `deliver`.

`DELIVER.json` is the whole contract between the two halves. `deliver.py`'s `_write_record` is its
only producer, the watchdog its only consumer, and `preflight.py`'s `check_manifest_keys_agree`
checks that both name the same keys. The watchdog reads two of them:

| Key | Used for | If empty |
| --- | --- | --- |
| `branch` | what to push; it must be under `harness/` | the item is skipped |
| `fork_repo` | where to push it | `FORK_REPO` from the host `.env` |

The clone is always `<run dir>/clone`, where the run dir is the one holding the manifest.

The push is `--force-with-lease=refs/heads/<branch>:<sha>`, where `<sha>` is the first word of
`runs/<item>/PUSHED` (empty before the first push). A revise cycle's rewritten tip lands, and a
commit someone else pushed to the same branch is refused. The expected sha has to be explicit,
because a bare `--force-with-lease` reads a remote-tracking ref, and a push to a URL has none. On a
refusal the watchdog names both shas and stops. To accept the fork's tip as the new base, put its
sha first in `runs/<item>/PUSHED`.

## The watchdog

| Condition | Action | Recovers |
| --- | --- | --- |
| on battery | `docker pause` | automatically on AC |
| non-container host CPU above 50 % for 30 s | `docker pause` | automatically below 30 % |
| `HEARTBEAT` older than 180 s (never within the first 180 s of uptime) | `docker kill` | automatically: the restart policy re-runs the gate |
| free disk below 5 GB | `docker stop` | operator |
| every 10 min, and on every stop | push delivered branches | |

The watchdog reads pause state from `docker inspect` on every poll, so a restarted watchdog
adopts a paused container. Its parameters come from `bb-config.json` through `bb-start.ps1`.

rk's scripts kill every PowerShell process whose command line matches `*watchdog.ps1*`. This
watchdog's filename cannot match that pattern, and the `bb-*` scripts find it by its full path.
The container, image, network, volume, work dir and env prefix are all `bb`-specific, so starting
and stopping `bb` leaves `rk` untouched.

## Kill switches

- `bb-work\STOP`: a graceful stop at the next unit boundary (`bb-stop.ps1` writes and removes it).
- `bb-work\HALT`: every stage boundary refuses to proceed (B148).
- `.harness/HALT` on the default branch stops Actions mode only. A running container's package
  mount is whatever you have checked out.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| container exits `0` seconds after start, no work | stale `bb-work\STOP` | `.\bb-start.ps1` removes it; check that nothing else recreates it |
| the gate prints a step you removed from `entrypoint.sh` | stale image | `.\bb-start.ps1 -Build`, and compare the printed build time to your edit |
| `FATAL: /harness is writable` | the mount lost `:ro` | start through `bb-start.ps1`, never a hand-typed `docker run` |
| `FATAL: pin mismatch` | a pinned file changed | a reviewed PR updates `.harness/PIN` (B143); never edit the pin by hand to make it pass |
| container exits `2` right after `gate 5/5`, five times, then stays down | a bad `local-loop` argv, usually a global flag after the subcommand | `docker logs bb` shows the argparse usage line; fix `entrypoint.sh`, then `.\bb-start.ps1 -Build` |
| `push REFUSED … the lease failed` | someone pushed to the same fork branch since the watchdog last did | inspect the branch; to accept its tip as the base, put that sha first in `runs\<item>\PUSHED` |
| exit `137` | out of memory | raise `container.memory_gb`; it is the limit that kills |
| killed every ~3 min during implement | the heartbeat sidecar is not running (old image) | `-Build`, then check `docker logs bb` for `gate 5/5` followed by the loop's own lines |
| branches never appear on the fork | no watchdog | `.\bb-watcher.ps1 -Once` shows `watchdog: ABSENT`; `.\bb-start.ps1` restarts it |
| `push FAILED … 403` | `HARNESS_GITHUB_TOKEN` cannot write the fork | the fork must belong to the machine account whose token this is |
