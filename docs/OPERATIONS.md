# Operations

What to do when something goes wrong, with the exact commands. Written for an operator who
has the repository checked out, a configured `.env`, and access to the GitHub UI — and who
may be reading this on a phone at the point where the only thing that matters is §8.

Two facts before anything else:

1. **Actions mode is the product; local mode is the fallback.** The scheduled workflows in
   `.github/workflows/` do the work. The `bb` container (`docs/LOCAL-MODE.md`) does the same
   work from the same queue when the schedule is down or you want to watch it. Nothing below
   assumes one over the other unless it says so.
2. **Two files stop everything.** `.harness/HALT` committed to `main` stops Actions mode
   before it spends. `HALT` at the repository root stops a local run at the next boundary.
   §8 has the full procedure.

Commands prefixed `harness` run in a shell with the venv active, from the repository root.
Where a step is a click in the GitHub UI, it says so. None of the commands here uses the
`gh` CLI; the harness does not, and the procedures do not need it.

---

## 1. Reading the state

The queue is GitHub. An item is an issue in this repository carrying exactly one
`harness:*` label, and the issue thread is the event log: every transition posts a comment
naming the stage, the workflow run URL, the cost, and the new state (B101).

| Label | Means | Who moves it on |
|---|---|---|
| `harness:queued` | eligible for a proposal | `discover.yml` |
| `harness:proposing` | a propose job is in flight | the job |
| `harness:proposed` | a proposal PR is open — **gate 1** | you, by merging the PR |
| `harness:approved` | eligible for implementation | `implement.yml` |
| `harness:running` | an implement job is in flight | the job |
| `harness:packaged` | package built, delivery pending | the same job |
| `harness:shipped` | upstream PR open — **gate 2** | Nathan or you, by merging upstream |
| `harness:revising` | a revise cycle is in flight | the job |
| `harness:merged` | upstream PR merged — terminal | — |
| `harness:blocked` | gates red and honestly unfixable | you, by relabelling `harness:queued` or `harness:approved` |
| `harness:needs-human` | revise cap reached | a trusted `/harness fix` |
| `harness:abandoned` | terminal | — |

From your machine:

```bash
harness status --json     # the queue as this store sees it
harness ledger            # window spend, medians, rate-limit state, cursors
harness dispatch          # what would start now, and why not — starts nothing
harness doctor            # every config key with its value; exit 3 names any missing one
```

`harness dispatch` is pure: run twice against an unchanged ledger it prints byte-identical
plans. Its `reason` string is the fastest diagnosis in the system — `halted`, `reserve`,
`rate limited until …`, `weekly usage 91% >= 90%`, `session usage 72% >= 70%`,
`carry leeway 10% reached`, `outside run window (mon 08:00-tue 20:00 UTC)`, or
`budget N% remaining, k of max n slots` (with `; weekly 49%, session 7%` appended when the
subscription's utilization is known). The last four are §13.

---

## 2. A failed run

`ops.yml` fires on every completed run of the three spending workflows. On `failure` it
opens (or updates) an issue here titled `ops: <workflow> failed`, labelled `harness:ops`,
with the run URL, the failing step name, and the last 50 log lines redacted. If the failure
is in the transient set — network reset, npm registry 5xx, GitHub 5xx, runner eviction — and
it is the first retry for that run, it re-dispatches once (B145). It never retries a job
whose failing step name contains `run`, `revise`, `propose`, or `gate` (B146): a red gate is
information, not a transient.

So the first thing to read is the `harness:ops` issue, not the Actions log.

1. Open Issues → label `harness:ops`. The newest one names the step.
2. If the step is `doctor`: a config key is missing or out of range, or `.harness/PIN` no
   longer matches. Run `harness doctor` locally; it exits 3 and names the key. For a pin
   mismatch see §10.
3. If the step is `sync-fork`: the fork diverged. §5.
4. If the step is `harness run --item N` or `revise`: read the evidence. The run's
   `runs/item-N/` directory was uploaded as an artifact with `if: always()` (B126) —
   Actions → the run → Artifacts. `EVIDENCE.md` inside has verbatim gate output. The item
   itself will be `harness:blocked` with the reason in a comment, or will be reset to its
   previous state by the next run's reconciliation (§3).
5. If the step is the ledger commit: `state/ledger.json` conflicted on **`harness-state`**,
   the branch the workflows keep it on (§12, D28). Do **not** rebuild it on `main`:
   `main`'s copy is only the initial ledger, and B113 protects `main`, so the push would be
   refused after you had already overwritten the local file. Rebuild it on the state branch
   with §12's worktree recipe:

   ```bash
   git fetch origin harness-state
   git worktree add ../hs FETCH_HEAD        # the state branch, detached; nothing else in it
   harness ledger --rebuild                 # rewrites state/ledger.json in THIS checkout
   cp state/ledger.json ../hs/state/ledger.json
   git -C ../hs commit -am "ledger: rebuild [skip ci]"
   git -C ../hs push origin HEAD:refs/heads/harness-state
   git worktree remove ../hs
   ```

   The `[skip ci]` matters (B115); without it the push triggers a workflow. Losing the file
   costs accuracy, not correctness (B117).
6. Close the `ops:` issue when you have acted on it. `ops.yml` reopens or updates it if the
   failure repeats.

To re-run a single item by hand once the cause is fixed, either

```bash
harness run --item N
```

from a checkout whose `.env` has `STORE_BACKEND=github` and `PERMISSION_TIER=2`, or
Actions → `implement.yml` → Run workflow → `issue: N`. Add `--dry-run` to the local form to
see every write the run would send (`gh.sent`) without sending any of them.

---

## 3. A stuck item (`harness:running` for more than 3 hours)

An implement job is capped at `timeout-minutes: 120`. An item still labelled
`harness:running` three hours after the label was applied, with no live workflow run, was
left mid-flight by a killed or timed-out job.

**The harness fixes this itself.** Every `harness run` — scheduled or manual — begins with a
reconciliation step that returns such items to their previous state label (B147, A48). The
tick that reaches it is `feedback.yml`'s: its last step before the ledger commit is a bare
`harness run`, there for exactly this, on `41 */3 * * 1-5` — every three hours, Monday to
Friday. `implement.yml` reconciles too, but only on a tick where the dispatcher gives it an
item to start, and its three crons are Monday and Tuesday only (§13.3) — so on a Wednesday
`feedback.yml` is the one that reaches it.

To do it now:

1. Confirm nothing is live: Actions → `implement.yml` → no run in progress. If one is in
   progress, wait; it will finish or time out.
2. Either trigger a run — Actions → `implement.yml` → Run workflow, `issue` blank — which
   reconciles first and then follows the dispatcher's plan; or relabel the issue by hand:
   remove `harness:running`, add `harness:approved`. A label a human sets is honoured, not
   overwritten (B102).
3. Check the fork for a half-pushed branch, `harness/<kind>-N-<slug>`. A run that died
   before `deliver` pushed nothing. One that died after leaves a branch and no PR; the next
   run of the item re-cuts the branch from the fork's main.

If the item comes back `harness:running` and dies again at the same point, it is not stuck,
it is failing — §2, and read the artifact.

---

## 4. A rate-limited window

A Claude usage-limit response is an outcome, not an incident (B119, B120). The stage returns
the item to the label it had at entry, writes `rate_limited_until` into
`state/ledger.json`, comments on the issue with the reset time, and the job exits 0. The
workflow shows green. Until the reset time, `harness dispatch` prints an empty plan:

```json
{"start": [], "reason": "rate limited until 2026-09-14T03:00:00Z", "skipped": {}}
```

Nothing to do but wait. Every scheduled tick between now and then runs `dispatch`, sees the
reason, and exits without spending (B121).

If you know the limit has lifted early and want the next tick to work, clear the field on
**`harness-state`** — the branch the ledger actually lives on (§12, D28). Editing
`main`'s copy does nothing: it is the initial ledger, the next Actions tick reloads from
`harness-state` anyway, and B113 refuses the push. Use §12's worktree recipe:

```bash
git fetch origin harness-state
git worktree add ../hs FETCH_HEAD        # the state branch, detached; nothing else in it
python - <<'PY'
import json, pathlib
p = pathlib.Path("../hs/state/ledger.json")
d = json.loads(p.read_text(encoding="utf-8"))
d["window"]["rate_limited_until"] = None
p.write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")
PY
git -C ../hs commit -am "ledger: clear rate_limited_until [skip ci]"
git -C ../hs push origin HEAD:refs/heads/harness-state
git worktree remove ../hs
```

The `[skip ci]` matters (B115); without it the push would itself trigger a workflow. If the
reset time the CLI reported was relative (`resets in 30 minutes`), the ledger holds an
absolute time computed by the stage from its clock; the comment on the issue shows the same
value. `harness ledger` prints the current state without editing anything.

---

## 5. A diverged fork

`harness sync-fork` runs before every dispatch. It fast-forwards the fork's `main` from
upstream and does nothing else (B105). When the fork's `main` holds a commit upstream does
not, it exits 1, pushes nothing, and its message names both shas:

```
$ harness sync-fork
fork main 3f2a… is not an ancestor of upstream main 9c41…; pushed nothing
```

Every spending workflow then fails at that step and `ops.yml` opens an `ops:` issue for it.

**The harness will never repair this itself.** A non-fast-forward fork means some
`base_sha` exists only on the fork, so every package pinned to it stops being reconstructible
by anyone reviewing upstream. Repairing it is a human act, from your machine, using the
machine account's credential (not yours — the fork is not yours):

```bash
git clone https://github.com/<machine-account>/brightboost.git fork && cd fork
git remote add upstream https://github.com/Bright-Bots-Initiative/brightboost.git
git fetch upstream main
git log --oneline upstream/main..origin/main      # commits only the fork has — read them
```

Those commits are the divergence. Usually they are a workflow file GitHub added when someone
clicked "enable Actions" on the fork, or a commit pushed to the wrong remote. Once you know
what they are and have decided none of them matters:

```bash
git push --force origin upstream/main:main        # with the machine account's PAT
cd .. && harness sync-fork                        # expect exit 0 and the upstream sha
```

Then check every `harness:shipped` item. A delivery PR whose base commit was one of the
discarded ones needs `/harness rebase` from a trusted account; the item re-syncs, rebases
onto the real upstream `main`, and re-runs the full gate sequence before pushing (B136).

If the fork is unrecoverable, delete it and fork again from the machine account — but every
open work branch dies with it, so close their PRs first.

---

## 6. A disabled schedule

GitHub disables every scheduled workflow in a public repository after **60 days without
repository activity**, and does it silently. A repository whose queue happened to be empty
for two months stops for good, and no run fails because no run starts.

**The heartbeat is the alarm, and its absence is the signal (B144, A47).** `heartbeat.yml`
runs every Monday at 09:05 UTC (`5 9 * * 1`), spends nothing, and posts one comment on the
tracking issue named by `TRACKING_ISSUE` in `.harness/config.json`: queue depth per state,
spend this window, the last successful run of each workflow, and the fork's divergence from
upstream.

**If Monday passes and the tracking issue has no new comment**, the scheduler is off. Do not
wait for a second Monday.

1. Actions → each of `discover.yml`, `implement.yml`, `feedback.yml`, `heartbeat.yml`. A
   disabled one shows a banner ("This scheduled workflow is disabled because there has been
   no activity…") with an **Enable workflow** button. Click it on each.
2. Actions → `heartbeat.yml` → Run workflow. A comment appears within minutes; that is your
   confirmation.
3. If the workflows are enabled and still not running: check that this repository is not
   itself a fork (forks have schedules disabled by default), and that `main` is the default
   branch — schedules only run from it.

While the schedule is down, local mode is continuity: `.\bb-start.ps1` on the Windows host
drains the same queue (`docs/LOCAL-MODE.md`). Or trigger `implement.yml` by hand.

The ledger commit at the end of every spending run counts as activity, so a repository with
any work in it will not go quiet. An idle one will. Merging a proposal PR — any commit — also
resets the 60-day clock.

---

## 7. A leaked secret

Two secrets exist. Each is revoked with one action, and revocation is the whole fix — the
harness holds no other state that depends on the old value. Do the steps in this order.

**First, stop spending.** GitHub UI → this repository → Add file → Create new file →
name `.harness/HALT`, any content → Commit directly to `main`. Every spending workflow now
exits 0 at its first step (B149). Locally, `harness halt`; for the container, `.\bb-stop.ps1`.

**If `HARNESS_GITHUB_TOKEN` leaked** — the classic PAT on `jgoetzmann-bot`:

1. Sign in as the machine account → Settings → Developer settings → Personal access tokens →
   Tokens (classic) → **Delete** the token. Every request carrying it fails from that second.
2. Generate a new one: classic, scope `public_repo` only, nothing else. `workflow` stays
   off; its absence is invariant I-15.
3. This repository → Settings → Secrets and variables → Actions → `HARNESS_GITHUB_TOKEN`
   → Update. And the host `.env` if local mode is in use — the container never had it
   (R5.7), so nothing there changes.

**If `CLAUDE_CODE_OAUTH_TOKEN` leaked** — the subscription token the CLI needs:

1. Revoke it from the Claude account it was issued against — the same account
   `claude setup-token` signed you into.
2. On your machine, `claude setup-token`; it opens a browser login and prints a new value.
3. Update the repo secret `CLAUDE_CODE_OAUTH_TOKEN`, and the host `.env`. Restart the
   container — it reads `.env` at start, through `local/container_env.ps1`.

**Then find out where it went.** The redactor should have caught it everywhere the harness
writes; check that it did:

```bash
git log -p --all | grep -cE "ghp_|github_pat_|sk-ant-"                          # expect 0 (R5.1)
grep -rlE "ghp_|github_pat_|sk-ant-" runs/ packages/ state/ proposals/ 2>/dev/null  # expect nothing
```

Also read the most recent run artifacts and the comments the harness posted. If any of them
carries the value, that is a bug against `harness/redact.py` — a pinned file — and the pin
protects the fix: open a PR, `CODEOWNERS` routes it to you, and `.harness/PIN` is updated in
the same PR (§10).

**Finally**, remove `.harness/HALT` with another commit. If the leak came from a workflow
log, also delete that run's logs (Actions → the run → ⋯ → Delete all logs).

---

## 8. How to stop everything

Three switches, one per place work can happen. Use all three if you are not sure which is
running.

| Switch | Stops | How | Takes effect |
|---|---|---|---|
| `.harness/HALT` on `main` | Actions mode | one commit, from anywhere — UI, phone, `git` | the first step of every spending job; before `doctor`, before the dispatcher (B150) |
| `HALT` at the repo root | a local `harness run` | `harness halt` (or `touch HALT`) | next stage boundary, and inside `implement` between gate runs; exit 5, clone released, item resumable |
| `bb-work/STOP` | the `bb` container | `.\bb-stop.ps1` | next unit boundary; container exits 0, `STOP` cleaned up |

```bash
# Actions mode, from a checkout
git pull && echo halt > .harness/HALT
git add .harness/HALT && git commit -m "halt" && git push

# local
harness halt          # writes HALT_FILE; `harness resume` removes it

# container (PowerShell)
.\bb-stop.ps1         # graceful within its wait window, then forced
```

A job already past its first step when `.harness/HALT` lands finishes its current item; it
does not re-read the file mid-run. To kill it now: Actions → the run → Cancel workflow. The
item it was holding is reset by reconciliation (§3), and the run's evidence is still uploaded
(`if: always()`).

`harness dispatch` reports `halted` while either HALT file exists, so you can confirm from
your machine without waiting for a tick.

To resume: delete `.harness/HALT` with a commit; `harness resume`; `.\bb-start.ps1`.

---

## 9. Why a comment on the product repository takes up to three hours

This is by design (B134), and it will look like a bug the first time.

- **On this repository**, `/harness` commands are event-driven: `issue_comment` and
  `pull_request_review_comment` trigger `feedback.yml`, which authorises, parses, and acts
  within that same run. Latency is minutes.
- **On the product repository**, the harness receives no events — it is not a collaborator
  there, and it must not be. Commands on a delivery PR are found by `harness sweep`, which
  reads the machine account's notifications since the ledger cursor (B140) on
  `feedback.yml`'s schedule, `41 */3 * * 1-5`. Latency is up to `NOTIFY_POLL_HOURS`
  (three hours) on a weekday, and until Monday for a comment left on Saturday.

So `/harness fix` on an upstream PR at 14:00 UTC Friday is acted on by about 17:41 Friday;
at 20:00 Friday, by about 09:41 Monday. Review comments from trusted handles are picked up
the same way and become `revise` items on the same schedule.

To skip the wait: Actions → `feedback.yml` → Run workflow, or from your machine
`harness sweep` followed by `harness dispatch`. The sweep spends nothing (B141); anything
that needs a model call becomes an item the dispatcher starts on its own cadence.

A command is acted on once (B135). Editing a comment does not re-trigger it; post a new one.

---

## 10. When upstream's gate sequence changes

The harness runs **the gate sequence pinned in `harness/gates.py`**, not whatever the product
repository's `package.json` says this week. If upstream renames `npm run typecheck`, adds a
gate, or drops one, the harness does not adapt (handoff §17.3). What you will see instead:

- The baseline run on the untouched tree goes red for the renamed or removed script
  (`npm ERR! missing script`), and `EVIDENCE.md` records it as **pre-existing** with the
  verbatim output.
- Every item then proposes with `gate_expectation: known-red` naming that gate, or lands
  `harness:blocked` if the red is not one the proposal declared in `baseline_red`.
- Nothing is loosened, skipped, or silently swapped. That is the invariant working.

The fix is a reviewed code change here, not a config key (B112):

1. Edit `harness/gates.py:_SEQUENCE` to match upstream.
2. `python -m harness.verify_pin --print` to see the new hash, then
   `python -m harness.verify_pin --write` to update `.harness/PIN`. This is the one command
   in this document that only the operator runs; the harness cannot write that file (B143).
3. One PR containing both. `CODEOWNERS` routes it to you; `selftest.yml` runs the golden
   gate-sequence test on both OSes.
4. After merge: Actions mode picks it up on the next tick. The container reads `harness/`
   from the read-only mount, so a plain restart (`.\bb-stop.ps1` then `.\bb-start.ps1`) is
   enough — its pin check will pass again.

Until that PR merges, every run reports the new red honestly and nothing ships against it.
That is correct, and it is going to be surprising the first time.

### Pin mismatch

`doctor` fails (Actions) or the container exits 1 at gate step 3 (local) with a
`PinMismatch` naming the expected and actual hash. One of `harness/gates.py`,
`harness/packager.py`, `harness/redact.py`, or a file under `prompts/` changed without
`.harness/PIN` changing with it.

```bash
python -m harness.verify_pin --check     # exit 1 on mismatch, 0 ok
git log --oneline -5 -- harness/gates.py harness/packager.py harness/redact.py prompts/ .harness/PIN
```

If the change was intended, it should have carried the pin in the same PR. If it was not — a
prompt edited directly on `main`, say — revert it. The harness stays stopped either way,
which is the point.

---

## 11. Everyday actions

| You want to | Do |
|---|---|
| Queue an issue | label it `harness:queued`, or comment `/harness queue` |
| Approve a proposal | **merge** its PR. Approving without merging does nothing; merge is what `implement.yml` listens for |
| Send a proposal back | comment `/harness revise <notes>` on the proposal PR |
| Reject a proposal | comment `/harness reject <why>`; the PR closes, the issue goes `harness:abandoned` |
| Split a big issue | comment `/harness split`; up to `MAX_SUBISSUES` children, parent goes `harness:blocked` |
| Get a delivery PR fixed | review it upstream, or comment `/harness fix` there (§9 for timing) |
| Rebase a conflicted delivery PR | comment `/harness rebase` |
| Drop a delivery PR | comment `/harness stop`; the PR closes, the slot is freed |
| Un-block an item | relabel it `harness:approved` (or `harness:queued` for a fresh proposal) |
| Wake a `harness:needs-human` item | comment `/harness fix` from a trusted account; nothing else touches it |
| Create the twelve labels | `harness init --labels` (idempotent; a no-op message without a token) |

Every command is honoured only from a handle in `.harness/trust.txt` whose comment carries
`author_association` OWNER, MEMBER, or COLLABORATOR — both, or it is silently ignored
(B131, B132). Adding a handle is a reviewed PR to `.harness/trust.txt`.

## 12. Where the ledger actually lives (D28)

`main` is protected (one approving review, no force-push), so the workflows do **not** commit
`state/ledger.json` to `main`. They keep it on the branch **`harness-state`**: every spending job
loads the latest copy from there before `harness doctor`, and pushes the updated file back at the
end with `[skip ci]`. `main`'s copy is the initial ledger and what local mode starts from.

- Read it: `git fetch origin harness-state && git show FETCH_HEAD:state/ledger.json`
- Rebuild it after a corruption: `harness ledger --rebuild`, then commit the result to `harness-state`
  by hand (`git worktree add ../hs origin/harness-state`, copy, commit, push).
- Never protect `harness-state`; it is written by the Actions token.
- Every procedure in this document that writes the ledger goes through the recipe above:
  §2 step 5 (rebuild after a conflicted ledger commit) and §4 (clear
  `rate_limited_until`). If you find one that edits `state/ledger.json` on `main`, it is
  a documentation bug — D28 is the ruling, this section is the procedure.

## 13. Usage-aware governance

Since Delivery 3 the harness watches the subscription's **own** utilization, not only the dollars
it has counted. This section is what to read when the queue is full, nothing is halted, nothing is
rate limited, and `harness dispatch` still starts nothing.

### 13.1 The signal

`claude -p --output-format stream-json --verbose` emits one `rate_limit_event` per call:

```json
{"type":"rate_limit_event","rate_limit_info":{"status":"allowed","resetsAt":1788519600,
 "rateLimitType":"five_hour","overageStatus":"rejected","isUsingOverage":false,
 "unifiedWindows":{"five_hour":{"utilization":0.07,"resetsAt":1788519600},
                   "seven_day":{"utilization":0.49,"resetsAt":1788897600}}}}
```

`utilization` is a fraction, 0..1, of the subscription's allowance for that window. It comes from
the inference response headers, so the long-lived `setup-token` used in Actions mode receives it
too. The runner keeps the last one of a call; the stage stamps `observed_at` from the clock and the
governor stores it in `state/ledger.json` under `window.usage`:

```bash
harness ledger --json                       # window.usage, window.carry, medians, rate-limit state
git fetch origin harness-state && git show FETCH_HEAD:state/ledger.json   # the copy Actions uses (§12)
```

The weekly heartbeat comment prints the same numbers under **subscription usage (last observed)**,
so the tracking issue is the phone-readable view.

**Nothing depends on the signal.** With `usage` absent — a fake backend, an older CLI, a call that
never reached inference — every decision falls back to the USD path (`WEEKLY_CAP_USD`,
`RESERVE_PCT`, `PER_CALL_CAP_USD`) and behaves exactly as it did in Delivery 2. That is B114, kept
as a "must not depend" rule rather than the "no signal exists" claim it started as (DECISIONS D31).
`WEEKLY_CAP_USD` therefore ships at `400.00`: high enough that the dollar backstop does not bind
before the usage stop when the signal is present, and still hard when it is not.

### 13.2 The two stops

| Knob | Ships as | Trips when | `reason` |
|---|---|---|---|
| `WEEKLY_USAGE_STOP_PCT` | `90` | `seven_day.utilization * 100 >= 90` | `weekly usage 91% >= 90%` |
| `SESSION_USAGE_STOP_PCT` | `70` | `five_hour.utilization * 100 >= 70` | `session usage 72% >= 70%` |

The governor raises `BudgetExhausted(reason)` **before** the USD checks, and the dispatcher applies
the same rule in its own order: rate limit → halted → **usage stop** → reserve (USD) → run window →
candidates. A stop is a normal outcome, not an incident: the command exits 0, the item is handed off
(§13.4), and the next window picks it up.

What it looks like: `harness dispatch` prints an empty `start` with one of those reasons; the item
keeps its label; no comment claims failure. Nothing to do. If you need the work anyway, the honest
options are to wait for the reset in `window.usage.seven_day.resets_at`, or to raise the knob in a
reviewed PR (§13.5) and accept that your own interactive Claude use that week is competing with the
harness for the same allowance.

### 13.3 The run window

`RUN_WINDOW_START=mon 08:00` and `RUN_WINDOW_END=tue 20:00` are UTC, lowercase three-letter weekday,
and may wrap past Sunday. Outside the window **no new item starts**; the plan's reason is
`outside run window (mon 08:00-tue 20:00 UTC)`. Both keys empty means always open.

The window is not the schedule. `.github/workflows/implement.yml` carries three crons —
`17 8,14,20 * * 1`, `17 2,8,14 * * 2`, `23 20 * * 2` — which are when GitHub wakes the job up; the
window is what the dispatcher enforces once it is awake. **Move both together**, or the job wakes to
find nothing eligible and burns a minute of Actions time saying so.

The last cron is the wrap-up run, 23 minutes after this account's weekly reset (Tue 20:00 UTC =
13:00 PT). GitHub cron is always UTC and never shifts, while the reset is quoted in Pacific time, so
when Pacific leaves daylight time the reset moves to 21:00 UTC and that row fires before it rather
than after: one skipped wrap-up per winter, no spend, and the following Monday picks the new window
up. The comment in `implement.yml` says the same thing. Correcting it means moving that row and
`RUN_WINDOW_END` by an hour, together.

`harness run --item N` bypasses the window on purpose — that is how you drive one item by hand on a
Thursday. It does **not** bypass the usage stops.

### 13.4 The leeway, the handoff, and the continue

A weekly reset in the middle of an implementation used to mean a branch abandoned halfway. Now:

1. A stop (usage or rate limit) inside `implement` / `continue` / `package` / `deliver` triggers a
   **handoff**: anything uncommitted is committed as `wip: handoff (<reason>)`, the branch is pushed
   to the **fork** (never upstream, never forced), `runs/item-N/HANDOFF.md` is written and posted as
   a comment, the item returns to `harness:approved`, the ledger records a **carry**
   (`window.carry` = issue, since, reason), and the command exits **0**.
2. `HANDOFF.md` is the operator's page: the reason, the branch, the base sha, the fork, the last gate
   results, the last 20 `DECISIONS.md` lines, the acceptance criteria not yet met, and the exact next
   command — `harness revise <id> --source continue`.
3. The carried item is the **first** thing the next run starts, **even outside the run window**, and
   it may spend against `OVERRUN_PCT` (`10`) of the fresh week instead of waiting for
   `WEEKLY_USAGE_STOP_PCT`. When that leeway is used up the reason is `carry leeway 10% reached` and
   the item is handed off again — same branch, same file, no work lost.
4. Green gates → the item goes `harness:packaged`, the carry is cleared, and the ordinary package and
   deliver steps run. Red → `harness:blocked`, nothing pushed, as always (B136).

Only one item is carried at a time. To look at it, or to resume it by hand:

```bash
harness ledger --json                # window.carry names the issue, when, and why
cat runs/item-42/HANDOFF.md          # after a run on this machine
harness revise 42 --source continue  # what the run loop does for you
```

To drop a carry instead of resuming it, relabel the issue `harness:blocked` and delete
`runs/item-N/HANDOFF.md`; the next dispatch then treats it as an ordinary blocked item.

### 13.5 Changing the knobs

All five live in `.env` and may be overridden in `.harness/config.json`, which is CODEOWNERS-
protected — so changing them is a reviewed PR, which is the point. `.harness/README.md` carries the
table of ranges. After merging, confirm what the harness actually loaded:

```bash
harness doctor      # every key with its value; exit 3 names any missing or out-of-range one
harness dispatch    # the reason string reflects the new knobs immediately, and starts nothing
```

Ranges are enforced at startup, not at spend time: `0 < WEEKLY_USAGE_STOP_PCT <= 100`,
`0 < SESSION_USAGE_STOP_PCT <= 100`, `0 <= OVERRUN_PCT < WEEKLY_USAGE_STOP_PCT`, and both window
keys either empty or matching `^(mon|tue|wed|thu|fri|sat|sun) ([01]\d|2[0-3]):[0-5]\d$`. A typo is
a `harness doctor` failure naming the key, never a silently different budget.

Three things these knobs deliberately cannot do: make the harness merge anything, move a gate, or
let it spend past `WEEKLY_CAP_USD` — the USD cap and the reserve still apply underneath, and a usage
stop never removes them.
