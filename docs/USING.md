# How to use it

Day-to-day operation of the harness from the GitHub web UI. Everything here can be done from a browser or a phone; the CLI equivalents are given where they are faster or where the UI has no button for it.

Three repositories are involved:

| | |
|---|---|
| This repo, the harness | `jgoetzmann/bright-bots-harness` — the queue (issues), gate 1 (proposal PRs), the workflows |
| The product repo | `Bright-Bots-Initiative/brightboost` — where the work lands, gate 2 |
| The fork | `jgoetzmann-bot/brightboost` — owned by the machine account `jgoetzmann-bot`; every work branch is pushed here and nowhere else |

When something is broken rather than merely waiting, go to [docs/OPERATIONS.md](OPERATIONS.md). This document is the normal path.

---

## The one-minute version

Five kinds of thing arrive. Four of them want something from you.

| What arrives | Where | What you do |
|---|---|---|
| An issue labelled `harness:queued` | this repo | Nothing. It becomes a proposal on the next `discover` run. Relabel or comment `/harness reject` if you disagree with the pick |
| A PR adding `proposals/<issue>-<slug>.md` | this repo | **Gate 1.** Merge it to approve. Close it to withhold approval. This is the cheapest place to disagree |
| A PR from a `jgoetzmann-bot:harness/…` branch | the product repo | **Gate 2.** Review it and merge, or steer it with a `/harness` comment. The harness cannot merge it |
| A comment on issue #2, every Monday ~09:05 UTC | this repo | Skim it. It carries the queue depth per label, the last observed subscription usage, the last successful run of each workflow, and how far the fork has drifted from upstream. **Its absence is the alarm** |
| An issue titled `ops: <workflow> failed`, labelled `harness:ops` | this repo | Something went red. [OPERATIONS §2](OPERATIONS.md#2-a-failed-run) |

Every item is one issue in this repo carrying exactly one `harness:*` label — a transition removes the previous one — and its thread is the event log: each transition posts a comment naming the stage, the new state, the cost of the last recorded call, and the reason.

| Label | Means | Who moves it on |
|---|---|---|
| `harness:queued` | eligible for a proposal | `discover.yml` |
| `harness:proposing` | a propose job is in flight | the job |
| `harness:proposed` | a proposal PR is open — **gate 1** | you, by merging it |
| `harness:approved` | eligible for implementation | `implement.yml` |
| `harness:running` | an implement job is in flight | the job |
| `harness:packaged` | package built, delivery pending | the same job |
| `harness:shipped` | upstream PR open — **gate 2** | you or Nathan (`BrightBoost-Tech`), by merging upstream |
| `harness:revising` | a revise cycle is in flight | the job |
| `harness:merged` | terminal | — (**you set this by hand**, see below) |
| `harness:blocked` | gates red and honestly unfixable | you, by relabelling `harness:queued` or `harness:approved` |
| `harness:needs-human` | revise cap reached | a trusted `/harness fix` |
| `harness:abandoned` | terminal | — |

Nothing sets `harness:merged` for you: the label is read by the store and written by nobody. After you merge a delivery PR upstream, relabel its harness issue by hand — the dispatcher's `depends_on` waits on exactly that label, so an unrelabelled item silently blocks its dependants.

---

## Reading a proposal PR

The PR is titled `proposal: <work item title> (#<issue>)`, comes from the branch `harness/propose-<issue>`, and adds exactly one file. The body inlines the whole proposal — gate 1 is a judgement about a plan, and you should not have to open a file in a diff to read one — followed by a footer naming the trusted handles and the `/harness` verbs. The file under `proposals/` is the artifact the merge approves.

The file is a work package with YAML front matter. A real one, `proposals/4-chore-activities-delete-orphaned-sequenc.md`:

```yaml
---
issue: 4
upstream_issue: 633
title: "chore(activities): delete orphaned SequenceDragDropGame component"
kind: chore
slices: 2
risk: low
touched_paths:
  - "src/components/activities/SequenceDragDropGame.tsx"
  - "src/components/activities/SequenceDragDropGame.test.tsx"
depends_on: []
estimated_turns: 10
gate_expectation: green
baseline_red: []
---
```

Below the front matter, ten sections in a fixed order: `Issue`, `Diagnosis`, `Approach`, `Slices`, `Behaviors`, `Acceptance criteria`, `Decisions`, `Open questions`, `Touched paths`, `Risks`. The full schema and what each section is for is in [docs/PACKAGE-FORMAT.md](PACKAGE-FORMAT.md) §1.

What to check, in the order that saves the most time:

1. **`Diagnosis`.** Is the stated problem the real problem? It cites files and lines; open one or two. Everything downstream rests on this being right.
2. **`upstream_issue`.** Does it point at the product issue you meant? `null` is legitimate, but then the harness's own issue body is the whole brief — and if the prose disagrees with the front matter, as it does in the proposal above, that alone is worth a `/harness revise`.
3. **`Open questions`.** Non-empty here means the spec is not decided. It blocks the fullsend path, and if any question is load-bearing it blocks the item. A proposal with real open questions wants `/harness revise`, not a merge.
4. **`Acceptance criteria`.** Each line has to be independently checkable, because `ACCEPTANCE.md` in the delivery marks each one met or not met against the gate output.
5. **`gate_expectation` and `baseline_red`.** `known-red` means the harness expects the product repo's gates to be red before it touches anything, and names which. If that surprises you, the gate sequence and upstream have drifted apart — [OPERATIONS §10](OPERATIONS.md#10-when-upstreams-gate-sequence-changes).
6. **`touched_paths` and `depends_on`.** Every path was already checked to exist in the product repo at the pinned base commit; a proposal whose front matter fails validation is never opened as a PR at all — the stage retries once with the errors in the prompt, then blocks the item. `depends_on` lists issue numbers that must be `harness:merged` before the dispatcher will start this one.

**Merging is approval.** Approving the PR without merging does nothing — `implement.yml` listens for the push to `proposals/**` on `main`, not for a review.

Within a minute or so of the merge, `implement.yml` starts. It reads the number off the filename (`proposals/4-…md` → issue #4), runs `harness approve 4` so the issue goes `harness:approved`, then asks the dispatcher. If the run window is open and the usage stops are clear, it implements, packages and delivers in that same run. If not, the plan is empty with a reason and the next `implement` cron inside the window picks it up. Either way the issue thread gets a comment.

One exception worth knowing: if `.harness/HALT` exists when you merge, the HALT check is the first step of the job, so **nothing happens at all** — the label does not even move, and no later run re-reads that push. After lifting the halt, relabel the issue `harness:approved` by hand (label moves are honoured, not overwritten) or run `harness approve 4` from a checkout.

**Closing is rejection**, but only in the sense that nothing implements: no workflow listens for a closed PR, so the issue sits on `harness:proposed` indefinitely. To record it properly, comment on the PR:

```
/harness reject the component is still referenced by the onboarding flow
```

which closes the PR and moves the issue to `harness:abandoned` within minutes. To send it back instead of killing it:

```
/harness revise cover the onboarding import path in the diagnosis
```

That re-runs the proposal with your note as extra instruction and costs a model call.

---

## Reading a delivery PR on the product repo

It comes from a branch in the fork's `harness/` namespace — `harness/fix-<n>-<slug>` in practice, since every work item is created with kind `issue` — into brightboost's default branch. Review is requested automatically from every handle in `.harness/trust.txt`; a handle GitHub refuses (not a collaborator upstream) is recorded as a refusal, not a failure. The URL is commented on the harness issue, which goes `harness:shipped`.

The body is not written by the model. It is the review package's own files concatenated and redacted — four sections in this order, then the same footer every harness surface carries ([PACKAGE-FORMAT §6](PACKAGE-FORMAT.md#6-the-delivery-pr--the-package-as-a-pr-body-delivery-2)):

| # | Section | What is in it |
|---|---|---|
| 1 | Summary | the work item and external reference, the **base commit**, the branch, the patch count and filenames, the declared touched paths |
| 2 | Diagnosis | the `Diagnosis`, `Issue`, `Approach` and `Risks` sections of the approved proposal, with citations |
| 3 | Evidence | the product repo's own gate sequence **verbatim** — baseline and post-change, each gate with its argv, exit code, PASS/FAIL and output tails |
| 4 | Reconstruction | the clone/checkout/`git am`/bundle commands with `BASE` and the branch filled in |

Section 3 is the load-bearing one, and the two halves are not interchangeable. *Baseline* is the untouched tree at the base commit, run before any change: anything red there is pre-existing and is not attributable to this PR. *Post-change* is the branch as packaged. Nothing is summarised on the way in — if the evidence is long, the body is long, because a summary of a gate is an opinion about a gate. Above 60,000 characters the body is cut and points at the run artifact.

Check for the omission note. When the database gates (`npx prisma generate`, `bash scripts/check-prisma-drift.sh`) were not run, section 3 opens by saying so and naming them. The harness does not claim parity with CI it did not achieve.

### Reconstructing the tree yourself

You need nothing but the two public repos and the base commit named in section 1. That commit always exists upstream, so the first route never touches the fork:

```bash
BASE=<the base commit from section 1>
BRANCH=<the branch from section 1>
git -c core.autocrlf=false clone https://github.com/Bright-Bots-Initiative/brightboost.git r
cd r
git fetch https://github.com/jgoetzmann-bot/brightboost.git "$BRANCH"
git checkout FETCH_HEAD
git diff "$BASE"..HEAD
```

The `-c core.autocrlf=false` matters on a Windows host with `core.autocrlf=true` globally: without it a fresh clone comes up dirty and the checkout refuses.

If you would rather not trust the fork at all, apply the patch series onto the base commit — the package on disk (workflow artifact, or `packages/` after `harness archive`) has `BASE` and `patches/`:

```bash
git checkout "$(cat ../BASE)"
git am ../patches/*.patch
```

and offline, from the bundle in the same package:

```bash
git bundle verify bundle.gitbundle
git clone bundle.gitbundle -b "$BRANCH" r
```

All three produce the same tree, because the branch tip is exactly `BASE` plus the patch series. If they disagree, trust the patches and open a bug against the harness. Full commands and caveats: [PACKAGE-FORMAT §3](PACKAGE-FORMAT.md#3-the-reconstruction-contract).

What is **not** in the PR body and stays in the package: `DECISIONS.md`, `ACCEPTANCE.md`, `manifest.json`, `patches/`, `bundle.gitbundle`, `transcript.jsonl`. In Actions mode the run directory `runs/item-<n>/` is uploaded as a workflow artifact on every run, cancelled ones included, kept 14 days — minus the disposable clone, the workspace and `node_modules`, which are excluded from the upload.

Three things you do not need to check for, because the code to do them does not exist:

- The harness cannot merge, approve, or dismiss a review (I-12). Gate 2 is you or Nathan, always.
- The PR cannot contain a change under `.github/**` (I-15): the machine account's PAT has no `workflow` scope, so GitHub itself refuses the push, and `_reject_forbidden_diff` in `stages/implement.py` catches the subtler cases a token scope cannot see.
- The evidence cannot come from a widened gate: `harness/gates.py`, where the sequence lives, is hashed into `.harness/PIN` along with `packager.py`, `redact.py` and every file under `prompts/`. Every spending workflow runs `harness doctor` before its work step, and a pin mismatch fails it there.

To steer it instead of merging, see the next section — `/harness fix`, `/harness rebase`, `/harness stop`. Note the latency: comments on the product repo are polled, not pushed.

---

## The `/harness` comment commands

The form is `/harness <verb> [args]` at the start of a line (leading whitespace is allowed). Everything after the verb to the end of that line is the argument, and is passed to the stage as notes.

| Verb | What it does | Put it on | Picked up within |
|---|---|---|---|
| `queue` | Moves the issue to `harness:queued` so the next `discover` run proposes it. Already-queued is a no-op | an issue **here** | minutes |
| `split` | Runs `decompose`: up to `MAX_SUBISSUES` (8) child issues here, parent goes `harness:blocked`. A child is never split again | an issue **here** | minutes; costs a model call |
| `revise <notes>` | Re-runs `propose` with your notes appended and publishes the new work package | a proposal PR **here** | minutes; costs a model call |
| `reject <why>` | Closes the PR and moves the item to `harness:abandoned`. Terminal | a proposal PR **here**, or a delivery PR upstream | minutes here, up to 3 h upstream |
| `fix <notes>` | One bounded revise cycle, source `review`: re-implements against the feedback, re-runs the **complete** gate sequence, and force-pushes to the fork only if the branch is under `harness/` and its tip is still a commit the harness authored | a delivery PR upstream | up to 3 h |
| `rebase <notes>` | The same cycle, source `conflict`: rebases onto upstream's default branch, then re-runs the gates | a delivery PR upstream | up to 3 h |
| `stop` | Closes the PR and moves the item to `harness:abandoned`, freeing the concurrency slot | a delivery PR upstream | up to 3 h |

`reject` and `stop` are the same action in code — close the pull request, abandon the item. The difference is what you mean by it: `reject` reads as a refusal at gate 1, `stop` as an abort at gate 2.

A successful `fix` or `rebase` moves the branch and nothing else. Nothing in `gh.py` can edit a pull request body, so the PR's Evidence section stays as first posted while its diff shows the revised tip; the new gate run is in `runs/item-<n>/gates/final.json` in the workflow artifact, and the decisions are on the issue.

Red gates after a `fix` or `rebase` block the item and push nothing.

`fix` and `rebase` are bounded by `MAX_REVISE_CYCLES` (3). At the cap the item goes `harness:needs-human`, and only a keyword revise carrying notes from a trusted handle — `/harness fix` in practice, `/harness rebase` equally — restarts the loop.

`queue` and `split` act on the number of the thread the comment is on, so putting either on a pull request aims it at the wrong number. Keep them on issues in this repo.

### Who may use them

Both of these must hold, or the comment is read and silently ignored (the denial is counted in the ledger, and the body never reaches a prompt — authorisation happens before parsing):

1. The handle is in [`.harness/trust.txt`](../.harness/trust.txt) — currently `jgoetzmann` and `BrightBoost-Tech`. Adding one is a reviewed PR; the file is CODEOWNERS-protected and the harness cannot write it.
2. GitHub reports the commenter's `author_association` as `OWNER`, `MEMBER` or `COLLABORATOR`. `CONTRIBUTOR`, `FIRST_TIME_CONTRIBUTOR` and `NONE` are refused.

### Three parsing rules that bite

- Only the **first** `/harness …` line in a comment is read. If its verb is not one of the seven above, the whole comment is discarded — a typo does not fall through to the next line.
- A command is acted on **once**, keyed on the comment's node id. Editing a comment does not re-trigger it. Post a new one.
- The argument is a single line. A multi-line note becomes the first line only.

### Why upstream comments take up to three hours

On **this** repo, commands are event-driven: `feedback.yml` triggers on `issue_comment` and `pull_request_review_comment` whenever the body contains `/harness`, and the sweep in that same run acts on it. Latency is minutes.

On the **product** repo the harness receives no events — it is not a collaborator there, and it must not be. Commands on a delivery PR are found by `harness sweep`, which reads the machine account's notifications since the ledger cursor on `feedback.yml`'s schedule, `41 */3 * * 1-5`. That matches `NOTIFY_POLL_HOURS=3` in `.env` and `.harness/config.json`, which is the documented cadence for the same thing.

So `/harness fix` on an upstream PR at 14:00 UTC on a Friday is acted on around 17:41 that day; the same comment at 20:00 Friday waits until about 09:41 Monday, because the cron does not run at weekends. To skip the wait, run `feedback.yml` by hand.

---

## Running things by hand, from the Actions tab

Actions → pick the workflow on the left → **Run workflow** → choose the branch (`main`) and fill the inputs. The three spending workflows — `discover`, `implement`, `feedback` — refuse to start while `.harness/HALT` exists on `main`. `heartbeat`, `selftest` and `ops` have no HALT check.

| Workflow | Runs on its own | Inputs | What it does |
|---|---|---|---|
| `discover` | `17 7 * * 0` — Sunday 07:17 UTC | `mode`, `target`, `lens`, `ignore_allowlist` | Finds work and proposes every item it created, in one run. Spends |
| `implement` | `17 8,14,20 * * 1`, `17 2,8,14 * * 2`, `23 20 * * 2` — inside the run window; and on any push to `proposals/**` on `main` | `issue` | Approves merged proposals, asks the dispatcher, then implements, packages and delivers. The expensive one |
| `feedback` | `41 */3 * * 1-5`; and on any `/harness` comment here | none | Sweeps notifications for keyword commands, then reconciles items stranded in `harness:running` |
| `heartbeat` | `5 9 * * 1` — Monday 09:05 UTC | none | Posts the weekly comment on issue #2. Spends nothing: fake backend, tier 0, no model call |
| `selftest` | every pull request here | none | Checks `.harness/PIN` when present, then the whole pytest suite under `BACKEND=fake`, on Linux and Windows. No secrets |
| `ops` | on a failed `discover`/`implement`/`feedback` run | — | Opens or updates `ops: <workflow> failed` labelled `harness:ops`, with the redacted log tail, and re-runs failed jobs once on transient network causes only. **Cannot be dispatched by hand** |

### Directed discovery — how you actually queue work today

`mode: directed` with `target: <product issue number>` creates one work item for that issue and proposes it in the same run. The discovery half makes no model call and is idempotent: re-running it on an issue already known returns the existing item rather than a duplicate. `target` is required in this mode and refused as an error when blank; `lens` and `ignore_allowlist` are ignored here.

```bash
gh workflow run discover.yml -R jgoetzmann/bright-bots-harness \
  -f mode=directed -f target=633
```

That yields an issue here labelled `harness:queued`, then a proposal PR against this repo. Gate 1 is then yours.

### Triage, and why it usually finds nothing

`mode: triage` (the default) has two halves, and the order matters:

- If **anything** here is already `harness:queued`, triage ranks those and reads nothing from the product repo. No new issues are created; the ids that went in come back, best first.
- Only when the local queue is empty does it look at brightboost. There it drops issues that are assigned, issues claimed by an in-flight branch or PR title, issues labelled `intern-starter`, `large` or `architecture` — and everything **not** carrying the allowlist label `harness-ok` (`ALLOWLIST_LABEL` in `.env`).

Today no brightboost issue carries `harness-ok`, so plain triage on an empty queue finds nothing and makes no model call. That is the filter working, not a fault. `ignore_allowlist: true` drops that one filter for a run; the other three still apply.

```bash
gh workflow run discover.yml -R jgoetzmann/bright-bots-harness \
  -f mode=triage -f ignore_allowlist=true
```

`lens` is carried on the command line as an optional free-text focus, but the pinned triage prompt (`prompts/discover_triage.md`) substitutes only the candidate list, so it does not currently reach the model. Treat it as reserved.

### Implementing one item now

The `issue` input is the **harness** issue number, not the product one, and it bypasses the run window — that is how you drive one item on a Thursday. It does not bypass the usage stops, and the item must already be `harness:approved`.

```bash
gh workflow run implement.yml -R jgoetzmann/bright-bots-harness -f issue=4
```

Leave the input blank to make the job take the dispatcher's plan instead.

### The other two

```bash
gh workflow run feedback.yml   -R jgoetzmann/bright-bots-harness   # act on upstream comments now
gh workflow run heartbeat.yml  -R jgoetzmann/bright-bots-harness   # a status comment on demand
gh run list -R jgoetzmann/bright-bots-harness -w implement -L 5    # what happened
```

From a checkout instead of the Actions tab, the same three things are `harness discover --mode directed --target 633` then `harness propose <id>`, `harness run --item 4`, and `harness sweep`.

---

## The kill switch

`.harness/HALT` is a file on the **default branch of this repo**. Its contents are never read — existence is the whole switch.

**To engage it from the browser:** Add file → Create new file → path `.harness/HALT` → any body → commit directly to `main`. That is one commit, and it works from a phone.

From a checkout:

```bash
git pull && echo halt > .harness/HALT
git add .harness/HALT && git commit -m "halt" && git push
```

**To lift it:** delete the file in another commit. This repo's own convention is to rename rather than delete — `.harness/HALT.suspended` is what a lifted switch looks like here, and the commits say `chore: suspend the kill switch` / `chore: restore the kill switch`. Any name but `HALT` is lifted, because the check is `test -f .harness/HALT` and nothing else.

**What it stops.** `discover`, `implement` and `feedback` check it as their **first** step — before checkout, before `harness doctor`, before the dispatcher. The job logs `halted by .harness/HALT` and exits 0. Green, not red: nothing spent, no label moved, no comment posted, no ops issue opened.

**What it does not stop:**

- `heartbeat` — no HALT check. The Monday comment keeps arriving, which is how you know the scheduler is still alive while you are halted.
- `selftest` on pull requests, and `ops` triage of an already-failed run.
- A job **already past its first step**. It finishes the item it is holding and does not re-read the file mid-run. To kill that one: Actions → the run → Cancel workflow. Reconciliation returns the item to its previous label within three hours, and the run's evidence still uploads.

Locally there is a second, separate switch: `HALT` at the repository root (`HALT_FILE` in `.env`), created by `harness halt` and removed by `harness resume`, which stops a local run at the next stage boundary with exit 5 and leaves the item resumable.

The two switches are checked in different places. `.harness/HALT` is checked at the entry of `discover`, `propose`, `run`, `dispatch`, `deliver`, `revise`, `decompose`, `sweep` and `local-loop`, and exits 0. The local `HALT` file is checked at the entry of `run`, `deliver`, `revise`, `decompose`, `sweep` and each `local-loop` unit — not `discover`, `propose` or `dispatch` — and exits 5. `status`, `ledger` and `doctor` check neither and still work while halted.

Either way you can confirm from your machine without waiting for a tick: with `.harness/HALT` present, `harness dispatch` prints `halted by .harness/HALT` and exits 0 before it builds a plan at all; with only the local `HALT` file, it prints the plan with `"reason": "halted"`.

Full procedure, including the container: [OPERATIONS §8](OPERATIONS.md#8-how-to-stop-everything).

---

## Watching the spend

```bash
harness ledger          # spend, usage, medians, rate-limit state, cursors
harness ledger --json   # the whole ledger, including window.carry and window.usage raw
harness status          # the queue by state, the remaining percentages, and anything in flight
harness dispatch        # what would start now, and why not — starts nothing
```

The block that matters is `usage:`. Its shape:

```
usage:
  session (5h)  12.0%  stop at 70%  (58.0 to go)  resets 2026-09-04T18:00:00Z
  weekly  (7d)  49.0%  stop at 90%  (41.0 to go)  resets 2026-09-08T20:00:00Z
  observed at   2026-09-04T09:41:07Z
```

Read it as: the subscription's own utilization for that window, the configured stop, and the distance to it in **percentage points of utilization, not dollars**. When a window is past its stop the parenthesis says `STOPPED` instead.

The two stops both ship in `.env` and may be overridden in `.harness/config.json`, which is CODEOWNERS-protected — so changing either is a reviewed PR:

| Knob | Ships as | Trips when | The `reason` you will see |
|---|---|---|---|
| `SESSION_USAGE_STOP_PCT` | `70` | `five_hour.utilization * 100 >= 70` | `session usage 72% >= 70%` |
| `WEEKLY_USAGE_STOP_PCT` | `90` | `seven_day.utilization * 100 >= 90` | `weekly usage 91% >= 90%` |

The tighter of the two wins. A stop is a **normal outcome**, not an incident: the command exits 0, the item is handed off, nothing claims failure, and the next window picks it up. If you need the work anyway, the honest options are to wait for the reset time printed above, or to raise the knob in a PR and accept that your own interactive Claude use that week is competing for the same allowance.

If the block instead says:

```
usage:
  (never observed; the USD path governs - B114)
```

that is not an error. No real model call has yet reported utilization into this ledger — a fake backend, an older CLI, or a call that never reached inference. The dollar path governs on its own exactly as it did before the signal existed: `WEEKLY_CAP_USD` 400.00, `RESERVE_PCT` 10, `PER_CALL_CAP_USD` 3.00. Nothing anywhere in the harness *depends* on the usage number being present, which is why it can be missing without changing any decision.

### The run window is a separate thing

`RUN_WINDOW_START=mon 08:00` to `RUN_WINDOW_END=tue 20:00`, UTC. Outside it no new item starts and the plan reads:

```json
{
  "start": [],
  "reason": "outside run window (mon 08:00-tue 20:00 UTC)",
  "skipped": {}
}
```

The window is not the schedule. The crons in `implement.yml` are when GitHub wakes the job up; the window is what the dispatcher enforces once it is awake. Move both together or the job wakes to find nothing eligible. `implement.yml`'s `issue` input and `harness run --item N` bypass the window deliberately; **nothing** bypasses the usage stops.

The window closes at this account's weekly reset (Tue 20:00 UTC = 13:00 PT), which is why the last cron is a wrap-up 23 minutes after it.

### Carry and handoff

A stop or a rate limit **in the middle of** `implement` / `continue` / `package` / `deliver` is a handoff, not a failure:

1. Uncommitted work is committed as `wip: handoff (<reason>)` and the branch is pushed to the fork only, never upstream, never forced.
2. `runs/item-N/HANDOFF.md` is written and posted as a comment on the harness issue: the reason, the branch, the base sha, the last gate results, the acceptance criteria not yet met, and the exact next command.
3. The item returns to `harness:approved`, the ledger records a **carry**, and the command exits 0.

The carried item is the **first** thing the next run starts, **even outside the run window**, and it spends against `OVERRUN_PCT` (10) of the fresh week rather than waiting for `WEEKLY_USAGE_STOP_PCT`. Exhaust that leeway and the reason becomes `carry leeway 10% reached` and it is handed off again onto the same branch, with nothing lost. Only one item is carried at a time.

`harness ledger`'s text output does not print the carry — `harness ledger --json` does, under `window.carry` (`issue`, `since`, `reason`):

```bash
harness ledger --json | jq .window.carry
cat runs/item-4/HANDOFF.md            # after a run on this machine
harness revise 4 --source continue    # what the next run does for you
```

To drop a carry instead of resuming it, relabel the issue `harness:blocked` and delete `runs/item-N/HANDOFF.md`.

### Where the numbers actually live

In Actions mode the ledger is **not** on `main` — it lives on the unprotected `harness-state` branch, loaded near the start of every spending job and pushed back at the end with `[skip ci]`:

```bash
git fetch origin harness-state && git show FETCH_HEAD:state/ledger.json
```

The phone-readable view is the Monday heartbeat comment on issue #2, which prints the same last-observed utilization. The full procedure, including rebuilding a corrupted ledger with `harness ledger --rebuild`, is [OPERATIONS §12](OPERATIONS.md#12-where-the-ledger-actually-lives-d28) and [§13](OPERATIONS.md#13-usage-aware-governance).

---

## When something looks wrong

First command, every time: `harness dispatch`. Its `reason` string is the fastest diagnosis in the system, and it starts nothing. Then go to the matching section of [docs/OPERATIONS.md](OPERATIONS.md) rather than guessing.

| Symptom | Section |
|---|---|
| A red `discover`/`implement`/`feedback` run, and an `ops:` issue | §2 |
| An item stuck on `harness:running` for more than three hours | §3 |
| `reason` is `rate limited until …` | §4 |
| `harness sync-fork` exits 1; the fork has diverged | §5 |
| The schedules have simply stopped | §6 |
| A token or secret appeared in a comment or a log | §7 |
| Stop everything, now | §8 |
| A `/harness` comment upstream did nothing for hours | §9 |
| Every baseline gate red after upstream renamed a script; `PinMismatch` | §10 |
| `dispatch` starts nothing and nothing is halted or rate limited | §13 |

Two documents sit behind this one. [docs/PACKAGE-FORMAT.md](PACKAGE-FORMAT.md) is what the proposal and the delivery PR actually contain, section by section. [docs/SAFETY.md](SAFETY.md) is every invariant with a command that verifies it yourself.
