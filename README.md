# bright-bots-harness

A Python service that takes work on
[`Bright-Bots-Initiative/brightboost`](https://github.com/Bright-Bots-Initiative/brightboost)
from an issue through a diagnosis, an implementation checked by the product repository's own
gates, and a review package pinned to an exact base commit — and, in Delivery 2, delivers it
as a pull request that a human merges. Two human gates, both ordinary PRs, both usable from
a phone. The model never merges anything.

## Two ways to run it

| | **Actions mode — the product** | **Local mode — proof of concept, debugger** |
|---|---|---|
| Queue | GitHub issues in this repository, one `harness:*` label each | `harness.db` (SQLite) on your disk |
| Scheduler | `.github/workflows/`: discover weekly, implement three times inside the subscription's weekly window (Mon 08:00 – Tue 20:00 UTC), feedback every 3 h | you, at a terminal — or the `bb` container's loop |
| Credentials | `HARNESS_GITHUB_TOKEN` (machine account, `public_repo` only) and `CLAUDE_CODE_OAUTH_TOKEN`, as repo secrets | the Claude token only; the GitHub token is filtered out of the container |
| Publishes | pushes to a fork it owns, opens PRs upstream, comments here | nothing; the package stays on disk |
| `.env` | `PERMISSION_TIER=2`, `STORE_BACKEND=github` | `PERMISSION_TIER=0`, `STORE_BACKEND=sqlite` — the defaults |
| Read | [docs/OPERATIONS.md](docs/OPERATIONS.md) | [docs/LOCAL-MODE.md](docs/LOCAL-MODE.md) |

One codebase. No module above the store knows which mode it is in (invariant I-16); the only
differences are which store is opened and who runs the dispatcher. Start in local mode: the
five commands below work with no token, no network, and no spend.

## The two human gates

1. **Proposal.** `propose` opens a PR into `proposals/` here: a bounded front matter and a
   diagnosis with file and line citations. **Merging it is approval.** Nothing implements
   until you do. Reject proposals; if none are rejected, the gate is decorative.
2. **Delivery.** `deliver` pushes a branch to the machine account's fork and opens a PR
   upstream with the verbatim gate evidence in its body. Merging it is Nathan's or yours.
   The code to merge, approve, or dismiss a review does not exist (I-12).

Everything between the gates is bounded: a per-call cap (`PER_CALL_CAP_USD`), a weekly cap
(`WEEKLY_CAP_USD`) with a reserve, two subscription-utilization stops
(`WEEKLY_USAGE_STOP_PCT`, `SESSION_USAGE_STOP_PCT`), a weekly run window, a revise cap
(`MAX_REVISE_CYCLES`), a pinned gate sequence, and a trust list for anyone who wants to
steer it. See [docs/SAFETY.md](docs/SAFETY.md).

## Requirements

- Python 3.13, `git` on `PATH`
- `node`, `npm`, `npx` — used only inside the disposable clone, to run the product's gates
- The `claude` CLI, only for `BACKEND=cli`. The `fake` backend needs nothing.
- Docker Desktop, only for the `bb` container.

No runtime Python dependencies. Standard library only, on purpose (I-17).

On Windows, `claude`, `npm` and `npx` are `.CMD` shims; the harness resolves them through
`PATHEXT` itself. This machine's global `core.autocrlf=true` dirties fresh clones of the
product repo; the reconstruction commands in
[docs/PACKAGE-FORMAT.md](docs/PACKAGE-FORMAT.md) show the flag to pass.

## Install

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS / Linux
pip install -e ".[dev]"
```

## First run

```bash
harness init      # creates harness.db, runs/, packages/, and .env from .env.example
harness doctor    # probes git, claude, node, npm, npx, disk, the halt files, the pin, config
harness setup     # assesses machine-account readiness and writes HUMAN.md
```

`harness init` never overwrites an existing `.env`. `harness doctor` exits 3 when anything is
missing or degraded and names it — every Delivery 2 key included (A30). `harness setup`
exits 6 while any human prerequisite is outstanding and lists them in `HUMAN.md`.

Edit `.env` before doing anything real. `BACKEND=fake` is the default and spends nothing.

## The five-command walkthrough

Local mode, unchanged from Delivery 1, still the fastest way to see the whole state machine:

```bash
harness discover --mode directed --target 816
harness propose 1
harness approve 1
harness run --item 1
harness package 1
```

1. **discover** creates one work item for issue 816. No model call in directed mode.
2. **propose** makes one model call and writes the work package — diagnosis, approach,
   slices, behaviors, acceptance criteria, decisions, open questions, touched paths, risks —
   now with the validated front matter, to `proposals/`. Read it. This is the cheapest place
   to disagree.
3. **approve** is you, deliberately. In Actions mode this step is merging the proposal PR.
4. **run** clones into `runs/item-1/clone`, runs the gate sequence on the untouched tree as
   a baseline, implements, prettiers only the changed files, commits under the product's
   conventional-commit rules, and runs the gates again. Red gets bounded diagnose-and-fix
   cycles; a repeated failure signature stops it; still red means `blocked`, never a widened
   gate.
5. **package** assembles `runs/item-1/package/`, described in
   [docs/PACKAGE-FORMAT.md](docs/PACKAGE-FORMAT.md).

Then `harness archive 1 [--with-transcript]` promotes it into `packages/`.

## The Delivery 2 commands

```bash
harness dispatch                  # JSON plan of what may start now; starts nothing
harness deliver 1                 # push the branch to the fork, open the upstream PR
harness revise 1 --source ci      # one bounded revision cycle: ci | conflict | review | continue
harness decompose 42              # split issue 42 into sub-issues, here only
harness sweep                     # poll notifications, parse trusted keywords, enqueue
harness ledger [--json] [--rebuild]   # spend, medians, window, rate-limit state
harness sync-fork                 # fast-forward the fork from upstream; exit 1 on divergence
harness init --labels             # create the twelve harness:* labels, idempotently
harness --dry-run <command>       # record every GitHub write in gh.sent, send none
```

Every one of them runs under `BACKEND=fake` with no network. In Actions mode the workflows
run them in a fixed order: `.harness/HALT` check, `doctor`, `sync-fork`, `dispatch`, then
`run`/`package`/`deliver` per item, then a `[skip ci]` commit of `state/ledger.json`.

Steering, from a trusted handle only: `/harness revise|reject` on a proposal PR,
`/harness fix|rebase|stop` on a delivery PR, `/harness split|queue` on an issue here.
Comments on the product repository are polled, not pushed, so they take up to
`NOTIFY_POLL_HOURS` — see OPERATIONS §9 before reporting that as a bug.

## Budget

Every stage asks the governor before it spends. The governor is backed by
`state/ledger.json`: accumulated `total_cost_usd` per window against `WEEKLY_CAP_USD`,
`RESERVE_PCT` held back, a median per stage once three observations exist, and
`rate_limited_until` when the CLI reports a usage limit — which returns the item to its
previous state and exits 0, not an incident.

Delivery 3 adds the subscription's own numbers. Every `claude` call under
`--output-format stream-json` reports a `rate_limit_event` carrying `five_hour` and
`seven_day` utilization; the runner keeps the last one, the ledger stores it as
`window.usage`, and the weekly heartbeat prints it. Four things read it:

| Knob | Ships as | Effect |
|---|---|---|
| `WEEKLY_USAGE_STOP_PCT` | `90` | nothing new starts once seven-day utilization reaches it |
| `SESSION_USAGE_STOP_PCT` | `70` | the same for the rolling five-hour window |
| `RUN_WINDOW_START` / `RUN_WINDOW_END` | `mon 08:00` – `tue 20:00` UTC | outside it no new item starts |
| `OVERRUN_PCT` | `10` | leeway for one **carried** item after a weekly reset |

A stop is a normal outcome, never an incident: the half-finished item is handed off — branch
committed and pushed to the fork, `runs/item-N/HANDOFF.md` written and posted, item back to
`harness:approved`, exit 0 — and the next window resumes it first with
`harness revise <id> --source continue`, even outside the run window.

The USD caps stay underneath and stay hard, because **no decision depends on the signal
being present** (B114): with no `usage` observed, the dollar path governs exactly as it did
in Delivery 2. `WEEKLY_CAP_USD` ships at `400.00` so the dollar backstop cannot bind before
the usage stop when the signal is there. The whole procedure is
[docs/OPERATIONS.md](docs/OPERATIONS.md) §13.

## The kill switch

Actions mode: commit `.harness/HALT` to `main`; it is the first step of every spending job.
Local: `harness halt` / `harness resume` (`HALT` at the root, exit 5). Container:
`.\bb-stop.ps1`. All three in [docs/OPERATIONS.md](docs/OPERATIONS.md) §8.

## Safety, in brief

- **Tier 2, scoped.** A classic PAT with `public_repo` only, on a machine account that owns
  nothing but the fork and is not a collaborator upstream. No `workflow` scope, so GitHub
  itself refuses any push touching `.github/workflows/` (I-15).
- **One authenticated module.** `harness/gh.py` is the only file that sends an
  `Authorization` header, and the only one that can read the token (I-11). The `gh` CLI
  stays banned (I-2′).
- **Never merges.** No merge, approve, or dismiss endpoint exists (I-12).
- **Trust gate.** A `/harness` command is honoured only from a handle in
  `.harness/trust.txt` with `author_association` OWNER/MEMBER/COLLABORATOR; anything else
  is silently ignored and its body never reaches a prompt (B131–B133).
- **Everything redacted** before disk and before GitHub (I-13).
- **Gates pinned.** `gates.py`, `packager.py`, `redact.py` and `prompts/` are hashed into
  `.harness/PIN`; a mismatch stops both modes before they spend (B142).
- **Tier 0 still holds.** With `PERMISSION_TIER=0` no request carries a token, exactly as
  in Delivery 1 — and that is what the container runs at.

The full list, each with a verify-it-yourself command, is in [docs/SAFETY.md](docs/SAFETY.md).

## Layout

| Path | What it is |
|---|---|
| `harness/` | The package. `store/` (sqlite + github behind one protocol), `dispatcher.py`, `ledger.py`, `trust.py`, `keywords.py`, `gh.py`, `stages/` |
| `prompts/` | Prompts as data, one file per stage, pinned |
| `.github/workflows/` | discover, implement, feedback, ops, heartbeat, selftest |
| `.harness/` | Operator-only: `trust.txt`, `config.json`, `PIN`, `HALT`. Not writable by the harness |
| `proposals/` | Merged proposals — the approved queue |
| `state/ledger.json` | The one persistent state file |
| `local/`, `bb-*.ps1`, `bb-config.json` | The container control plane — [docs/LOCAL-MODE.md](docs/LOCAL-MODE.md) |
| `docs/` | `SAFETY.md`, `OPERATIONS.md`, `PACKAGE-FORMAT.md`, `LOCAL-MODE.md` |
| `docs/delivery/` | The two frozen specs and their runnable review protocols — [docs/delivery/README.md](docs/delivery/README.md) |
| `tests/` | pytest; every specified behavior — B1–B86 (spec), B100–B150 (handoff), B200–B215 (D31–D33) — is cited by at least one test |
| `HUMAN.md` | Generated by `harness setup`: the sixteen things only a human can do |
| `DECISIONS.md` | Every ruling D1–D34, the amendment log for the frozen specs |

## What this does not do

No merge, no review approval, no auto-merge on green. No push to the product repository —
only to the fork. No issue on the product repository. No edit to `.github/**` anywhere. No
second fork, no stacked branches. No web UI; GitHub is the UI. No concurrency above one in
local mode. No self-hosted runners. It reads the subscription's reported utilization,
but nothing it does depends on that number being there.
