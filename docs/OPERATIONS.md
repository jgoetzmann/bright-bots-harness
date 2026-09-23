# Operations

The operator's runbook: how to read the state, what to do when something goes wrong, and the exact
commands. §8 is how to stop everything.

Actions mode is the product and local mode is the fallback: the scheduled workflows in
`.github/workflows/` do the work, and the `bb` container (`docs/LOCAL-MODE.md`) drains the same
queue when the schedule is down. Two files stop everything: `.harness/HALT` committed to `main`
stops Actions mode before it spends, and `HALT` at the repository root stops a local run at the next
boundary.

Commands prefixed `harness` run from the repository root with the venv active. The harness never
invokes the `gh` CLI; the `gh workflow run` lines in §11 are an optional shortcut for the Actions
tab's **Run workflow** button.

---

## 1. Reading the state

The queue is GitHub. An item is an issue in this repository carrying exactly one `stage:*` label,
and the issue thread is the event log: every transition posts a comment naming the stage, the
workflow run URL and the new state (B101).

| Label | Means | Who moves it on |
|---|---|---|
| `stage:queued` | eligible for a proposal | `discover.yml` |
| `stage:planning` | a propose job is in flight | the job |
| `stage:needs-approval` | a proposal PR is open (gate 1) | you, by merging the PR |
| `stage:ready` | eligible for implementation | `implement.yml` |
| `stage:building` | an implement job is in flight | the job |
| `stage:packaged` | package built, delivery pending | the same job |
| `stage:needs-review` | upstream PR open (gate 2) | a maintainer, by merging upstream |
| `stage:revising` | a revise cycle is in flight | the job |
| `stage:done` | upstream PR merged; terminal | the harness, on its next sweep (below) |
| `stage:blocked` | gates red and not fixable | you, relabelling `stage:queued` or `stage:ready` |
| `stage:needs-human` | revise cap reached | a trusted `/harness revise` |
| `stage:dropped` | terminal | — |

`harness tidy`, which every feedback run calls, closes the harness issue as completed and moves the
item to `stage:done` once the newest delivery PR of an item at `stage:needs-review` or
`stage:needs-human` has merged upstream (D86). The dispatcher's `depends_on` check waits on that
label. A delivery PR closed without merging leaves the item where it is; `/harness stop` parks it
at `stage:blocked`.

```bash
harness status --json     # the queue as this store sees it, and what Actions is doing
harness ledger            # subscription usage, calls made, rate-limit state, cursors
harness dispatch          # what would start now, and why not; starts nothing
harness doctor            # every config key with its value; exit 3 names any missing one
harness block             # the standing block, if any; `harness block 0` cancels one
```

`harness status`, the pinned issue and `/harness status` each carry an **Actions** section: what is
running, what is queued behind the `harness-ledger` lock, and what was cancelled in the last six
hours (D79). It is what tells "queued behind a build" apart from "asleep", and a burst of
cancelled runs is what a burst of merges looks like from the outside. It reads the newest 100 runs
and says so when that page comes back full, because a bound on what was read is not a claim about
what exists. When the run list cannot be read the section says so in one line rather than
reporting an empty queue as idle; in `ack`'s fast answer it is left out altogether.

`harness dispatch` is pure: run twice against an unchanged ledger, it prints identical plans. Its
`reason` string is the fastest diagnosis: `halted`, `rate limited until …`,
`weekly usage 91% >= 90%`, `session usage 82% >= 80%`, `carry leeway 10% reached`,
`outside run window (daily 11:00-19:00 UTC)`, or `k of max n slots` (with
`; weekly 49%, session 7%` appended when subscription usage is known). The usage stops, the leeway
and the window are §13.

---

## 2. A failed run

`ops.yml` fires on every completed run of the three spending workflows. On `failure` it opens or
updates an issue here titled `ops: <workflow> failed`, labelled `kind:ops`, with the run URL, the
failing step name, up to 20 of the failing job's lines in the harness's error forms (`error:`,
`::error::`, `rate limited`, `budget exhausted`) and the last 50 log lines, all redacted (B401). It
closes that issue itself on the workflow's next green run. It re-runs the failed job, up to three
attempts in all, only when the failing step is one that runs before anything is spent (the HALT
check, checkout and setup, `harness doctor`, `harness sync-fork`, the merged-proposal approval,
`harness dispatch`) and the log
matches a transient cause: a network reset, a registry or GitHub 5xx, or runner eviction (B145,
B146). A failure in a model call, a gate or the ledger commit is left for a human.

Read the `kind:ops` issue before the Actions log; the newest one names the failing step.

- **`doctor`**: a config key is missing or out of range, or `.harness/PIN` no longer matches. Run
  `harness doctor` locally; it exits 3 and names the key. For a pin mismatch see §10.
- **`sync-fork`**: the fork diverged. See §5.
- **`harness run --item N` or `revise`**: read the evidence. The run's `runs/item-N/` directory is
  uploaded as an artifact with `if: always()` (B126), under Actions → the run → Artifacts, and
  `EVIDENCE.md` inside has the verbatim gate output. The item is either `stage:blocked` with the
  reason in a comment, or reset to its previous state by the next run's reconciliation (§3).
- **The ledger commit**: `state/ledger.json` conflicted on `harness-state`. Rebuild it there with
  `harness ledger --rebuild` and §12's worktree recipe, never on `main`, whose copy is only the seed
  and is protected (B113). The rebuild replays the transition comments into the history and the call
  count and keeps the window it finds on disk: the reading, the carry, the cursors and any stored
  rate limit (B430).

To re-run one item by hand once the cause is fixed, run `harness run --item N` from a checkout whose
`.env` has `STORE_BACKEND=github` and `PERMISSION_TIER=2`, or Actions → `implement.yml` → Run
workflow with `issue: N`. The global `--dry-run` (`harness --dry-run run --item N`) records every
write the run would send in `gh.sent` without sending any.

---

## 3. A stuck item (`stage:building` for more than 3 hours)

An implement job is capped at `timeout-minutes: 120`, so an item still labelled `stage:building`
three hours after the label was applied, with no live workflow run, was left by a killed or
timed-out job.

Every `harness run`, scheduled or manual, starts by returning such items to their previous state
label (B147). `feedback.yml` reaches them: its last step before the ledger commit is a bare
`harness run`, on `41 */3 * * 1-5`. `implement.yml` reconciles too, but only on a tick where the
dispatcher gives it an item, and its crons fall inside the run window (§13.3).

To do it now, first confirm nothing is live (Actions → `implement.yml` has no run in progress; if
one is, wait for it to finish or time out). Then either run `implement.yml` with `issue` blank,
which reconciles before following the dispatcher's plan, or relabel the issue by hand from
`stage:building` to `stage:ready` — a label a human sets is honoured (B102). Finally check the fork
for a half-pushed branch, `harness/<kind>-N-<slug>`: a run that died before `deliver` pushed
nothing, and one that died after leaves a branch and no PR, which the next run of the item re-cuts
from the fork's main.

An item that returns to `stage:building` and dies again at the same point is failing: see §2 and
read the artifact.

---

## 4. A rate-limited window

A Claude usage-limit response is a normal outcome (B119, B120). The stage returns the item to the
label it had at entry, writes `rate_limited_until` into `state/ledger.json`, comments the reset time
on the issue, and the job exits 0 with a green run. Until the reset, `harness dispatch` prints an
empty plan whose reason is `rate limited until <time>`, and every scheduled tick reads that reason
and exits without spending (B121).

If the limit lifted early, clear the field on `harness-state` (§12). Editing `main`'s copy does
nothing, because the next Actions tick loads the ledger from `harness-state`:

```bash
git fetch origin harness-state
git worktree add ../hs FETCH_HEAD        # the state branch, detached
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

When the CLI reports a relative reset (`resets in 30 minutes`), the stage stores the absolute time
computed from its clock, and the issue comment shows the same value.

---

## 5. A diverged fork

`harness sync-fork` runs before every dispatch. It fast-forwards the fork's `main` from upstream and
does nothing else (B105). When the fork's `main` holds a commit upstream does not, it exits 1,
pushes nothing, and names both shas:

```
fork main 3f2a… is not an ancestor of upstream main 9c41…; pushed nothing
```

Every spending workflow then fails at that step and `ops.yml` opens an `ops:` issue. The harness
never repairs this itself: a non-fast-forward fork means some `base_sha` exists only on the fork, so
packages pinned to it cannot be reconstructed from upstream. Repair it from your machine, with the
machine account's credential:

```bash
git clone https://github.com/<machine-account>/brightboost.git fork && cd fork
git remote add upstream https://github.com/Bright-Bots-Initiative/brightboost.git
git fetch upstream main
git log --oneline upstream/main..origin/main      # commits only the fork has; read them
git push --force origin upstream/main:main        # once you have decided none of them matters
cd .. && harness sync-fork                        # expect exit 0 and the upstream sha
```

Those commits are usually a workflow file GitHub added when someone enabled Actions on the fork, or
a commit pushed to the wrong remote. Afterwards check every `stage:needs-review` item: a delivery PR
whose base commit was one of the discarded ones needs `/harness rebase` from a trusted account,
which rebases onto upstream's `main` and re-runs the full gate sequence before pushing (B136). If
the fork is unrecoverable, close the open work PRs, delete it, and fork again from the machine
account.

---

## 6. A disabled schedule

GitHub disables every scheduled workflow in a public repository after 60 days without repository
activity, and no run fails because none starts. The weekly heartbeat is the alarm (B144):
`heartbeat.yml` runs every Monday at 09:05 UTC (`5 9 * * 1`), spends nothing, and posts one comment
on the tracking issue named by `TRACKING_ISSUE` in `.harness/config.json`, carrying queue depth per
stage label, the last observed subscription usage, the run window and any carried item, the last
successful run of each workflow, the fork's divergence from upstream, and a banner when
`.harness/HALT` is on.

If Monday passes with no new comment on the tracking issue, the scheduler is off. Open Actions and
click **Enable workflow** on each of `discover.yml`, `implement.yml`, `feedback.yml`,
`heartbeat.yml` and `watchdog.yml` that shows the disabled banner, then run `heartbeat.yml` by hand:
a comment within minutes confirms it. If the workflows are enabled and still not running, check that
this repository is not itself a fork (forks have schedules disabled by default) and that `main` is
the default branch.

While the schedule is down, `.\bb-start.ps1` on the Windows host drains the same queue, or run
`implement.yml` by hand. The ledger commit at the end of every spending run counts as activity, as
does merging a proposal PR, so a repository with work in it stays active.

### When a scheduled run never starts

A run that fails files a `kind:ops` issue (§2). A run that never starts files nothing, and the only
symptom is that comments on the product repository go unanswered. `watchdog.yml` runs on
`17 */4 * * *` and, on a weekday, dispatches `feedback` when no `feedback` run of any kind has
started in six hours.

- **GitHub dropped the cron under load:** one dispatch and no issue. Nothing to do.
- **Four weekday slots missed in a row:** an issue titled `ops: scheduled runs are not firing`,
  labelled `kind:ops`. Re-enable the schedule on the Actions tab.
- **No repository activity for 60 days:** nothing from the watchdog, which GitHub disables along
  with every other schedule. GitHub emails the repository owner, and the weekly heartbeat comment
  stops appearing. Push any commit, then re-enable the workflows on the Actions tab.

The watchdog measures missing runs only: a failed run counts as a run, and `ops.yml` handles it. It
does not dispatch at weekends, because `feedback`'s cron is weekdays only and a comment left on
Friday evening waits for Monday.

---

## 7. A leaked secret

Two secrets exist. Each is revoked with one action, and the harness keeps no state that depends on
the old value. Do the steps in this order.

**First, stop spending.** GitHub UI → this repository → Add file → Create new file → name
`.harness/HALT`, any content → Commit directly to `main`. Every spending workflow now exits 0 at its
first step (B149). Locally, `harness halt`; for the container, `.\bb-stop.ps1`.

**If `HARNESS_GITHUB_TOKEN` leaked** (the classic PAT on `jgoetzmann-bot`):

1. Sign in as the machine account → Settings → Developer settings → Personal access tokens →
   Tokens (classic) → **Delete** the token. Every request carrying it fails from then on.
2. Generate a new one: classic, scopes `public_repo`, `notifications` and `workflow`, nothing
   else (D67). `public_repo` pushes to the fork and opens the pull request; `notifications` lets
   the sweep see a mention on a product issue; `workflow` lets `harness sync-fork` fast-forward the
   fork past upstream's own CI changes, and without it nothing can be delivered once upstream
   touches `.github/workflows/`. I-15 holds either way, because the harness refuses to push
   `.github/` itself. After step 3, `harness doctor` prints the scopes it reads off the new token
   and warns on a missing or extra one.
3. This repository → Settings → Secrets and variables → Actions → `HARNESS_GITHUB_TOKEN` → Update.
   Also update the host `.env` if local mode is in use; the container never receives this token.

**If `CLAUDE_CODE_OAUTH_TOKEN` leaked** (the subscription token the CLI uses): revoke it from the
Claude account `claude setup-token` signed you into, run `claude setup-token` again for a new value,
then update the repository secret and the host `.env` and restart the container, which reads `.env`
at start through `local/container_env.ps1`.

**Then find out where it went.** The redactor should have caught it everywhere the harness writes:

```bash
git log -p --all | grep -cE "ghp_|github_pat_|sk-ant-"                          # expect 0
grep -rlE "ghp_|github_pat_|sk-ant-" runs/ packages/ state/ proposals/ 2>/dev/null  # expect nothing
```

Also read the most recent run artifacts and the comments the harness posted. A value in any of them
is a bug in `harness/redact.py`, a pinned file: fix it in a PR that updates `.harness/PIN` in the
same change (§10).

**Finally**, remove `.harness/HALT` with another commit. If the leak came from a workflow log, also
delete that run's logs (Actions → the run → ⋯ → Delete all logs).

---

## 8. How to stop everything

Three switches, one per place work can happen. Use all three if you are not sure which is running.

| Switch | Stops | How | Takes effect |
|---|---|---|---|
| `.harness/HALT` on `main` | Actions mode | one commit, from anywhere — UI, phone, `git` | the first step of every spending job; before `doctor`, before the dispatcher (B150) |
| `HALT` at the repo root | a local `harness run` | `harness halt` (or `touch HALT`) | next stage boundary, and inside `implement` between gate runs; exit 5, clone released, item resumable |
| `bb-work/STOP` | the `bb` container | `.\bb-stop.ps1` | next unit boundary; container exits 0, `STOP` cleaned up |

```bash
# Actions mode, from a checkout
git pull && echo halt > .harness/HALT
git add .harness/HALT && git commit -m "halt" && git push

harness halt          # local: writes HALT_FILE; `harness resume` removes it
.\bb-stop.ps1         # container (PowerShell): graceful, then forced
```

`discover`, `implement` and `feedback` check `.harness/HALT` as their first step, before checkout,
`harness doctor` and the dispatcher. The job logs `halted by .harness/HALT` and exits 0: nothing
spent, no label moved, no comment posted, no ops issue opened. `heartbeat`, `ack`, `watchdog`, `ops`
and `selftest` do not check it; the heartbeat comment and the `ack` reply both say the harness is
halted.

On the command line the two switches are checked in different places:

| Command | `.harness/HALT` (exit 0) | `HALT_FILE` (exit 5) |
|---|---|---|
| `discover`, `propose`, `dispatch` | at entry | not checked |
| `run` | at entry | at entry, and between stages and gate runs |
| `deliver`, `revise`, `decompose`, `sweep` | at entry | at entry |
| `local-loop` | at entry and before each unit | in each unit's `run` |
| `sync-fork` | not checked | at entry |
| `status`, `ledger` | not checked | not checked |

`harness doctor` lists either switch as a problem when it is present.

A job already past its first step when `.harness/HALT` lands finishes its current item; it does not
re-read the file. To kill it now: Actions → the run → Cancel workflow. Reconciliation (§3) resets
the item it was holding, and the run's evidence still uploads (`if: always()`).

To confirm from your machine without waiting for a tick: with `.harness/HALT` present,
`harness dispatch` prints `halted by .harness/HALT` and exits 0 before it builds a plan; with only
`HALT_FILE` present, it prints a plan whose reason is `halted`.

To resume: delete `.harness/HALT` with a commit; `harness resume`; `.\bb-start.ps1`.

---

## 9. Why a comment on the product repository takes up to three hours

Commands are event-driven on this repository and polled on the product repository (B134). Here,
`issue_comment` and `pull_request_review_comment` trigger `feedback.yml`, which authorises, parses
and acts in the same run, so latency is minutes. On the product repository the harness receives no
events, because the machine account is not a collaborator there: `harness sweep` finds commands by
reading the machine account's notifications since the ledger cursor (B140) on `feedback.yml`'s
schedule, `41 */3 * * 1-5`. Latency there is up to three hours on a weekday,
and until Monday for a comment left at the weekend.

So `/harness revise` on an upstream PR at 14:00 UTC on a Friday is acted on at about 15:41; at 20:00
Friday, at about 21:41; at 22:00 Friday, not until about 00:41 Monday, the 51-hour worst case. A
`/harness` line in an inline review comment is read the same way. A review is never a command by
itself: its text reaches the model as feedback only once a `/harness revise` arrives, and a command
typed in a review's summary box is not read at all (D68).

To skip the wait, run `feedback.yml` from the Actions tab, post any `/harness` command on this
repository (it starts the same job, whose sweep reads the notifications too), or run `harness sweep`
then `harness dispatch` from your machine. Reading notifications spends nothing (B141), but `ask`,
`audit`, `split`, `revise`, `rebase` and a re-proposal call the model from inside `harness sweep`,
under the same usage stops as any stage.

A command is acted on once (B135). Editing a comment does not re-trigger it; post a new one.

---

## 10. When upstream's gate sequence changes

The harness runs the gate sequence pinned in `harness/gates.py`, whatever the product repository's
`package.json` says. If upstream renames `npm run typecheck`, adds a gate or drops one, the baseline
run on the untouched tree goes red for the renamed or removed script (`npm ERR! missing script`) and
`EVIDENCE.md` records it as pre-existing with the verbatim output. Every item then proposes with
`gate_expectation: known-red` naming that gate, or lands `stage:blocked` when the red is not one the
proposal declared in `baseline_red`. Nothing is loosened, skipped or swapped.

The fix is a reviewed code change here, not a config key (B112). Edit `harness/gates.py:_SEQUENCE`
to match upstream, run `python -m harness.verify_pin --write` to update `.harness/PIN` (only the
operator runs this; the harness cannot write that file, B143), and open one PR containing both:
`CODEOWNERS` routes it to you, and `selftest.yml` runs the gate-sequence test on both OSes. Actions
mode picks the change up on the next tick after merge; the container reads `harness/` from a
read-only mount, so `.\bb-stop.ps1` then `.\bb-start.ps1` is enough for its pin check to pass. Until
that PR merges, every run reports the new red and nothing ships against it.

### Pin mismatch

`doctor` fails (Actions) or the container exits 1 at gate step 3 (local) with a `PinMismatch` naming
the expected and actual hash. One of `harness/gates.py`, `harness/packager.py`, `harness/redact.py`
or a file under `prompts/` changed without `.harness/PIN` changing with it.

```bash
python -m harness.verify_pin --check     # exit 1 on mismatch, 0 ok
git log --oneline -5 -- harness/gates.py harness/packager.py harness/redact.py prompts/ .harness/PIN
```

An intended change should have carried the pin in the same PR. Revert an unintended one, such as a
prompt edited directly on `main`. The harness stays stopped until one of the two happens.

---

## 11. Everyday actions

| You want to | Do |
|---|---|
| Queue an issue | label it `stage:queued`, or comment `/harness go` |
| Approve a proposal | merge its PR; `implement.yml` listens for the merge, not for a review |
| Approve several at once | merge them all; a cancelled run loses no approval (§13.6) |
| Give it time you are not using | `/harness block <n>`, or `harness block <n>` (§13.3) |
| Send a proposal back | comment `/harness revise <notes>` on the proposal PR |
| Reject a proposal | comment `/harness stop <why>`; the PR closes, the issue goes `stage:dropped` |
| Split a big issue | comment `/harness split`; up to `MAX_SUBISSUES` children, parent goes `stage:blocked` |
| Get a delivery PR fixed | comment `/harness revise <notes>` on it upstream (§9 for timing) |
| Rebase a conflicted delivery PR | comment `/harness rebase` |
| Drop a delivery PR | comment `/harness stop`; the PR closes, the slot is freed |
| Un-block an item | relabel it `stage:ready` (or `stage:queued` for a fresh proposal) |
| Wake a `stage:needs-human` item | comment `/harness revise` from a trusted account |
| Create the labels | `harness init --labels`; idempotent, and a no-op without a token |
| Add somebody to the trust file | `harness trust line <login> --level 2`, paste the line it prints into `.harness/trust.txt`, open a PR |
| See who is trusted, and what is refused | `harness trust show` |

Commands are honoured only from a handle in `.harness/trust.txt`, at a level the verb reaches, and
confirmed by GitHub (B131, B132). The ordinary line is vouched (D69) and looks like
`2 their-github-login vouch:their-numeric-account-id`; `harness trust line` prints it with the
account id filled in. Neither `trust` command writes anything or needs a working `.env`, and the
reviewed PR that adds the line is the security boundary. `docs/SAFETY.md` describes the gate in
full.

### Running workflows by hand

Actions → pick the workflow → **Run workflow** → branch `main` → fill the inputs.

| Workflow | Runs on its own | Inputs |
|---|---|---|
| `discover` | `7 11,13 * * *` | `mode`, `target`, `lens`, `ignore_allowlist` |
| `implement` | `23 11-18 * * *`, and a push to `proposals/**` on `main` | `issue` |
| `feedback` | `41 */3 * * 1-5`, and any `/harness` comment here | none |
| `ack` | any `/harness` comment here | none |
| `heartbeat` | `5 9 * * 1` | none |
| `watchdog` | `17 */4 * * *` | none |
| `selftest` | every pull request here | none |
| `ops` | a completed spending run | not dispatchable |

`discover` finds work and proposes each item it created; `implement` approves merged proposals, asks
the dispatcher, then implements, packages and delivers; `feedback` sweeps notifications for
commands, queues issues assigned to the bot and reconciles stuck items. Those three spend, and
refuse to start while `.harness/HALT` exists (§8). `ack` replies within seconds that a command was
read; `heartbeat` posts the weekly comment (§6); `watchdog` restarts a stalled `feedback` schedule
(§6); `selftest` runs the pin check and the suite under `BACKEND=fake` on Linux and Windows; `ops`
files or closes the `ops:` issue (§2).

```bash
# one product issue: queued and proposed in the same run
gh workflow run discover.yml -R jgoetzmann/bright-bots-harness -f mode=directed -f target=633
# every open product issue assigned to the machine account
gh workflow run discover.yml -R jgoetzmann/bright-bots-harness -f mode=assigned
# triage without the harness-ok allowlist label; the other filters still apply
gh workflow run discover.yml -R jgoetzmann/bright-bots-harness \
  -f mode=triage -f ignore_allowlist=true
# one item now, by harness issue number; bypasses the run window, not the usage stops
gh workflow run implement.yml -R jgoetzmann/bright-bots-harness -f issue=4
gh workflow run feedback.yml  -R jgoetzmann/bright-bots-harness   # act on upstream comments now
gh run list -R jgoetzmann/bright-bots-harness -w implement -L 5    # what happened
```

`mode: directed` needs `target`, the product repository's issue number, and skips every triage
filter: the allowlist, the excluded labels, the assignee check and the in-flight-branch check.
`mode: assigned` applies no label filter, so it queues every open issue assigned to the machine
account, including ones labelled `intern-starter`, `large` or `architecture` that triage would
exclude; it only queues, and the next `discover` run proposes. `implement`'s `issue` is the harness
issue number, the item must already be `stage:ready`, and blank takes the dispatcher's plan. From a
checkout the equivalents are `harness discover --mode directed --target 633` then
`harness propose <id>`, `harness run --item 4`, and `harness sweep`.

---

## 12. Where the ledger lives (D28)

`main` is protected (one approving review, no force-push), so the workflows keep `state/ledger.json`
on the unprotected branch `harness-state`. Every spending job loads the latest copy from there
before `harness doctor` and pushes the updated file back at the end with `[skip ci]`, which keeps
the push from triggering a workflow (B115). `main`'s copy is the seed that local mode starts from,
and a ledger change committed there has no effect on Actions mode.

Read it with `git fetch origin harness-state && git show FETCH_HEAD:state/ledger.json`. Rebuild or
edit it through a worktree on that branch: `git worktree add ../hs FETCH_HEAD`, edit or copy in a
rebuilt `state/ledger.json`, commit with `[skip ci]`, push to `HEAD:refs/heads/harness-state`, then
`git worktree remove ../hs`. §4 is a worked example. Never protect `harness-state`; the Actions
token writes it.

---

## 13. Usage-aware governance

What stops work is the utilization of two subscription windows, five-hour (session) and seven-day
(weekly), which the API reports on every call. The allowance is shared with everything else the
account does, including the operator's own Claude Code sessions, so it moves while the harness is
idle. Read this section when the queue is full, nothing is halted or rate limited, and
`harness dispatch` still starts nothing.

### 13.1 The signal

`claude -p --output-format stream-json --verbose` emits one `rate_limit_event` per call, whose
`unifiedWindows` carries a `utilization` fraction (0..1) and a `resetsAt` for `five_hour` and
`seven_day`. It rides on the inference response headers, so the long-lived `setup-token` used in
Actions mode receives it too. The runner keeps the last one of a call, the stage stamps
`observed_at` from the clock, and the governor stores it under `window.usage`:

```bash
harness ledger --json                       # window.usage, window.carry, rate-limit state
git fetch origin harness-state && git show FETCH_HEAD:state/ledger.json   # the copy Actions uses
```

The weekly heartbeat comment prints the same numbers under **allowance**, read from the live copy on
`harness-state` rather than the seed on `main`, and says which one it read (B402).

A reading describes its window until that window's `resets_at` and nothing after (B399). A 100%
seven-day reading stops work until the reset and then stops nothing, with no command needed; a
refused call also sets `rate_limited_until` to the same instant (B396), which lifts with it.
`harness ledger` and `harness status` then print `window reset since; no longer stops anything` for
that window instead of STOPPED (B406).

**Nothing depends on the signal** (B114, as D74 amends it). No decision may *depend* on the usage
signal being present, and none falls back to a dollar figure — there is no dollar figure. With no
reading in force — a fake backend, an older CLI, a call that never reached inference, or a reading
whose window has reset — the usage stops and the headroom gates admit: unknown is not a stop. What
bounds a call then is the run window, `MAX_CONCURRENT_ITEMS`, the `MAX_TURNS_*` ceilings, both kill
switches and the commanded halt, and the subscription's own refusal, which D71 records as
`rate_limited_until` and which ends at its reset.

### 13.2 The two stops

| Knob | `.env.example` | `.harness/config.json` | `reason` when it trips |
|---|---|---|---|
| `WEEKLY_USAGE_STOP_PCT` | `90` | `90` | `weekly usage 91% >= 90%` |
| `SESSION_USAGE_STOP_PCT` | `70` | `80` | `session usage 82% >= 80%` |

`.harness/config.json` overrides `.env`, so Actions mode stops new session calls at 80%. A stop
trips when that window's `utilization * 100` is at or above the knob.

The governor raises `BudgetExhausted(reason)` before it authorises a call, and the dispatcher
applies the same rule in its own order: rate limit → halted → commanded halt → carry → usage
stop → run window → candidates. A
stop is a normal outcome: the command exits 0, the item is handed off (§13.4), and the next window
picks it up. `harness dispatch` prints an empty `start` with the reason, the item keeps its label,
and no comment claims failure. To get the work done anyway, wait for the reset in `window.usage`, or
raise the knob in a reviewed PR (§13.5), knowing that your own Claude use competes for the same
allowance.

### 13.3 The run window

`.harness/config.json` sets `RUN_WINDOW_START=daily 11:00` and `RUN_WINDOW_END=daily 19:00` (D72,
widened by D80). Both ends are UTC and take either a lowercase three-letter weekday, for a weekly
window that may wrap past Sunday (the `.env.example` default is `mon 08:00` to `tue 20:00`), or
`daily`, for a window that repeats every day and may wrap past midnight. Mixing the two is a
startup error. Outside the window no new item starts, and the reason is
`outside run window (daily 11:00-19:00 UTC)`. Both keys empty means always open.

The window is not the schedule. `discover.yml` carries `7 11,13 * * *` and `implement.yml` carries
`23 11-18 * * *`: the times GitHub wakes the jobs. The window is what the dispatcher enforces once
they are awake. Move both together; `tests/test_invariants.py` (B412) fails the build when a daily
window stops containing those crons, and B493 fails it when either workflow is down to a single
firing.

The window is eight hours wide because GitHub delivers every scheduled run late and drops some
entirely: measured lateness over two days ran from 2 to 264 minutes, median 72 for implement and
264 for discover, and one such run arriving 34 minutes after a four-hour window shut cost a whole
day's builds (D80). The width is what a late run lands in; the repeated firings are what a dropped
run is caught by.

The hours still follow the subscription's five-hour session, which is shared with your own use and
opens with the first model call of the day: discover's triage call at 11:07 when there is something
to triage, otherwise the first item a build starts. A build starting near the far edge therefore
runs into a second session and into the operator's own Pacific noon; what bounds it is
`SESSION_USAGE_STOP_PCT` (80 in Actions), which stops new calls before a session is spent, rather
than the window's tail. A carried item waits for the window too (B413). GitHub cron is always UTC;
11:00 UTC is 04:00 PDT and 03:00 PST, so nothing needs moving when the clocks change.

`harness run --item N` and `implement.yml`'s `issue` input bypass the window. They do not bypass the
usage stops.

**Lending it a session.** `/harness block <n>` (level 3), or `harness block <n>` from a terminal,
suspends the window for the next `n` five-hour sessions, at most six. The end is measured once,
when the command is acted on: the remainder of the session in progress plus `n − 1` whole ones, or
`n × 5 h` from now when no reading exists. A later reading never moves it, it expires by itself,
and `/harness block 0` cancels it. It lifts the window only — both usage stops, all three kill
switches, the trust gate and both human gates are untouched, and `MAX_CONCURRENT_ITEMS` is
unchanged. It creates no workflow runs, so it takes effect on the next run that happens anyway: a
gate-1 merge (immediately), the three-hourly weekday sweep, or a manual dispatch. `harness dispatch`
reports it under `block`, and every status surface carries one line while it stands.

### 13.4 The leeway, the handoff, and the continue

A stop, usage or rate limit, inside `implement`, `continue`, `package` or `deliver` triggers a
handoff: uncommitted work is committed as `wip: handoff (<reason>)`, the branch is pushed to the
fork (never upstream, never forced), `runs/item-N/HANDOFF.md` is written and posted as a comment,
the item returns to `stage:ready`, the ledger records a carry (`window.carry`: issue, since,
reason), and the command exits 0. `HANDOFF.md` holds the reason, the branch, the base sha, the fork,
the last gate results, the last 20 `DECISIONS.md` lines, the acceptance criteria not yet met, and
the next command, `harness revise <id> --source continue`.

The carried item is the first thing the next run starts. A weekly run window does not hold it back;
a daily one does, until it opens (D72). Outside a weekly window it may spend `OVERRUN_PCT` (`10`)
of the fresh week; when that leeway is used up the reason is `carry leeway 10% reached`, the item
is handed off again on the same branch, and it waits for the window. Inside the window, and under a
daily window, it is held to `WEEKLY_USAGE_STOP_PCT` like any other item, and a block counts as
the window being open (D81). Green gates then
move it to `stage:packaged`, clear the carry, and run the ordinary package and deliver steps; red
gates block it with nothing pushed (B136).

A handoff follows a stop inside work that has started. Work refused before it starts is not
started at all: `harness dispatch` skips suggested work the priority gate refuses, with the
refusal as its reason, and `harness run` asks the priority gate and the usage stops before each
clone and prints `item N waits: <reason>` for an item they refuse (D81).

Only one item is carried at a time. To look at it, or to resume it by hand:

```bash
harness ledger --json                # window.carry names the issue, when, and why
cat runs/item-42/HANDOFF.md          # after a run on this machine
harness revise 42 --source continue  # what the run loop does for you
```

To drop a carry instead of resuming it, relabel the issue `stage:blocked` and delete
`runs/item-N/HANDOFF.md`; the next dispatch then treats it as an ordinary blocked item.

### 13.5 Changing the knobs

The five knobs live in `.env` and may be overridden in `.harness/config.json`, which is
CODEOWNERS-protected, so a change is a reviewed PR. After merging, confirm what the harness loaded
with `harness doctor` (every key with its value; exit 3 names any missing or out-of-range one) and
`harness dispatch`, whose reason string reflects the new knobs immediately and which starts nothing.

Ranges are enforced at startup: `0 < WEEKLY_USAGE_STOP_PCT <= 100`,
`0 < SESSION_USAGE_STOP_PCT <= 100`, `0 <= OVERRUN_PCT < WEEKLY_USAGE_STOP_PCT`, and both window
keys either empty or matching `^(mon|tue|wed|thu|fri|sat|sun|daily) ([01]\d|2[0-3]):[0-5]\d$`, with
both ends `daily` or both weekdays. A typo is a `harness doctor` failure naming the key. The knobs
cannot make the harness merge anything, move a gate, or lift the turn caps, the kill switches and
the gate sequence that apply underneath them.

### 13.6 A burst of merges

Merging several proposal pull requests at once is safe. Each merge pushes to `proposals/**` and
starts an `implement` run in the `harness-ledger` group, which never cancels a run in progress but
does replace the one run GitHub keeps pending — so most of a burst is cancelled before it executes
a step. The approval does not ride on those runs: every `implement` and `feedback` run reconciles
the committed proposal files against item state with `harness approve --merged`, so whichever run
survives approves all of them, and any later run repairs whatever a cancelled run never did (D78).
An item that was stopped after its proposal merged is not `proposed`, so it is never resurrected.

One item that cannot be moved — a locked issue, a transferred one, a 403 — is warned about and the
others are still approved. The step exits 0 either way and carries `continue-on-error`, because
the keyword sweep runs after it in `feedback.yml`: a stuck item must never be what stops
`/harness halt` being read.
