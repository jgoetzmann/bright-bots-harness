# Every command, with an example

Two surfaces. **Comment commands** (`/harness …`) are how the harness is operated day to day, from
a browser or a phone. **CLI subcommands** (`harness …`) are how it is operated from a machine or an
Actions run, and are mostly what the workflows call for you.

If you only read one section, read [Comment commands](#comment-commands). The CLI is not required to
use the harness.

- [Comment commands](#comment-commands)
  - [Asking for work](#asking-for-work) · [Steering work](#steering-work-that-exists) ·
    [Asking questions](#asking-questions) · [Audits](#audits) · [`--force`](#--force)
- [Who may run what](#who-may-run-what)
- [CLI subcommands](#cli-subcommands)
- [Reading the result](#reading-the-result)

---

## Comment commands

The form is `/harness <verb> [args]` at the **start of a line**. Everything after the verb to the end
of that line is the argument.

Three things are forgiven, because all three are what people actually type:

| You type | Read as |
|---|---|
| `/Harness work …` | fine — capitalisation does not matter (phones capitalise the first word) |
| `@jgoetzmann-bot /harness work …` | fine — leading `@mentions` are allowed |
| `as discussed, /harness stop` | **not a command** — prose that mentions it is still prose |

One more that bites: **only the first `/harness` line in a comment is read**, and if its verb is not
one of the twelve, the whole comment is discarded rather than falling through to the next line.

### Asking for work

#### `work` — open a work item

```
/harness work make the activity cards keyboard reachable
```

Opens an issue in the harness repository, queued, and replies in the thread with a link to it.

```
/harness work https://github.com/Bright-Bots-Initiative/brightboost/issues/633
```

Same, but tracking that issue — identical to `discover --mode directed --target 633`. **Asking twice
is one item, never two.** A bare `#633` works too, but only when it is the *whole* argument:
`fix 3 of the cards` is a sentence, not a pointer to issue 3.

**Where:** the pinned inbox issue, or the product issue itself (mention the bot there — see below).

```
@jgoetzmann-bot /harness work
```

On a brightboost issue, with no argument, this means *this one*. The mention is not politeness: the
harness finds product-repository comments by reading its notifications, and it is not subscribed to a
thread it has never touched, so the mention is the only thing that makes a cold issue visible to it.

#### `go` — green-light a suggestion

```
/harness go
```

When the queue is empty and the week has budget, the harness may propose work nobody asked for. It
comments on the product issue saying so and waits. `go` is the only thing that releases it.

Nothing else does — ignoring that comment is a complete answer.

### Steering work that exists

#### `revise` — change the plan (gate 1)

```
/harness revise the migration has to be reversible; say how in the plan
```

**On the proposal pull request.** Returns the item to `planning` and rewrites the work package with
your note as the brief. No code has been written yet at this point, so this is the cheap place to
change direction.

#### `fix` — change the code (gate 2)

```
/harness fix the null check belongs in the caller, not the helper
```

**On the delivery pull request.** One more implementation pass against your feedback, then the
**complete** gate sequence again — not just the tests that were failing. Force-pushes to the fork
only if the branch is under `harness/` and its tip is still a commit the harness authored.

Bounded by `MAX_REVISE_CYCLES` (3). At the cap the item goes `stage:needs-human` and only a trusted
`/harness fix` restarts it.

#### `rebase` — the same, after a conflict

```
/harness rebase
```

Rebases onto the product repository's default branch, then re-runs the gates.

#### `stop` — abandon it

```
/harness stop
```

**On the delivery pull request.** Closes it and abandons the item, freeing the concurrency slot. The
branch and the evidence stay where they are.

#### `reject` — refuse the plan

```
/harness reject we are removing this feature next sprint
```

**On the proposal pull request.** Same action as `stop` in code — close and abandon — but it means
something different: a refusal at gate 1 rather than an abort at gate 2. **Level 3 only.**

#### `split` — break it up

```
/harness split
```

**On an issue in the harness repository.** Decomposes it into at most `MAX_SUBISSUES` (8) child
issues, each delivered separately; the parent goes `stage:blocked`. A child is never split again.
Costs a model call.

Sub-issues inherit how the parent arrived, so splitting a suggestion produces suggestions.

#### `queue` — put it back

```
/harness queue
```

Returns a parked or blocked item to `stage:queued`. A no-op if it is already there.

### Asking questions

#### `ask` — answer, change nothing

```
/harness ask what does the game registry do
```

Reads the product repository and answers **in the thread**. No work item, no branch, no pull
request, no state change of any kind — it acquires a read-only clone and cuts no branch.

Charged against `ASK_CAP_USD` (0.50), not the per-call cap, and bounded by `ASK_MAX_PER_DAY` (20)
across everyone. Past the cap it says so and makes **no model call**.

The answer names the commit it read and says it is a reading, not a decision. **This is the cheapest
way to find out whether the harness understands the codebase — ask it something you already know.**

### Audits

#### `audit` — one lens, one findings issue

```
/harness audit accessibility in src/components/activities
/harness audit redundant code
```

Reads the product repository through that one lens and opens **one** issue: a ranked findings report
labelled `kind:audit`, deliberately with **no stage label** — so the store cannot see it and it can
never enter the queue.

It creates **no work items**. Charged against `AUDIT_CAP_USD` (20.00). An audit stopped by its cap
still opens the issue, marked incomplete, listing under `## Not reached` what it never opened.

**A lens is required.** `/harness audit` with nothing after it is refused before any model call —
"audit everything" is the one scope the budget cannot bound.

#### `promote` — turn findings into work

```
/harness promote 3
/harness promote 1,4,7
/harness promote all
```

**On the audit issue.** Each named finding becomes an ordinary work item labelled `via:audit`, which
then goes through the proposal gate like anything else. Promoted findings are ticked off in the audit
issue, so it stays open as the record of what remains.

Promoting the same finding twice creates one item, not two. `all` takes every *unticked* finding, up
to `SUGGEST_MAX_PER_RUN` (5).

The two steps are deliberate: one sentence from you must not become eight proposals nobody approved.

### `--force`

```
/harness work https://github.com/.../issues/633 --force
/harness go --force
```

Starts the work now instead of waiting for the run window (Mon 08:00 → Tue 20:00 UTC). Recorded on
the item and named in the reply, so "why did this run on a Thursday" has an answer in the issue.

**Level 3 only.** Below that the flag is ignored, the command still stands, and the reply says why it
will wait — the request itself is untouched.

It lifts **the calendar and nothing else**:

| Still applies |
|---|
| `.harness/HALT` — the kill switch is not a scheduling preference |
| Both usage stops — 90% weekly, 70% session |
| `WEEKLY_CAP_USD`, `RESERVE_PCT`, every per-call cap |
| **Both human gates.** `--force` on a proposal does not merge it |

---

## Who may run what

Two things must hold, and **the second is invisible**: your handle must be in
[`.harness/trust.txt`](../.harness/trust.txt) with a level, **and** GitHub must report you as
`OWNER`, `MEMBER` or `COLLABORATOR` — which in practice means you have been invited to the
repository.

A comment failing either half is read, counted as denied, and ignored **with no reply**, so it looks
exactly like the harness being asleep. `harness doctor` names anyone in the trust file who has no
access.

| Level | Who | Commands |
|---|---|---|
| **3** | operator | everything, including `reject` and `--force` |
| **2** | maintainer | `work` `queue` `go` `audit` `promote` `revise` `fix` `rebase` `split` `stop` |
| **1** | asker | `ask` |
| **0** | not in the file | nothing; the comment body is never parsed |

Using a verb above your level is answered, not silent: the reply names the level it needed.

### Latency

| Where | How fast |
|---|---|
| Harness repository | minutes — the comment wakes the workflow directly |
| Product repository | up to 3 hours on a weekday; a Saturday comment waits until Monday |

The harness gets no events on the product repository — it is not a collaborator there and must not
be. It reads its notifications on `feedback.yml`'s schedule (`41 */3 * * 1-5`). To skip the wait, run
`feedback` from the Actions tab.

---

## CLI subcommands

`harness <command>`. Add `--json` for machine-readable output. Most of these are what the workflows
call; you rarely need them by hand.

### Looking

```bash
harness doctor          # binaries, versions, disk, halt, config, trust levels — the health check
harness status          # queue by state, budget remaining, what is in flight
harness dispatch        # what may start now, the priority queue, and why the head is or is not moving
harness ledger          # spend, per-stage medians, window state, distance to each usage stop
```

`dispatch` starts nothing. It is the answer to "why is nothing happening":

```json
{
  "start": [],
  "reason": "budget 90% remaining, 0 of max 1 slots",
  "skipped": {},
  "queue": [ { "class": "directed", "rank": 2, "item": 4, "state": "discovered", "forced": false } ],
  "head": { "item": 4, "reason": "waiting to be proposed; `discover` ranks and proposes the queue" },
  "suggested": { "admitted": false, "reason": "work somebody asked for is still outstanding (#4)" }
}
```

### Finding work

```bash
harness discover --mode directed --target 633     # one named product issue; no model call
harness discover --mode assigned                  # every open issue assigned to the bot; no model call
harness discover --mode triage                    # rank the queue, or find work when it is empty
harness discover --mode audit --lens "accessibility in src/components"
```

`triage` reaches the product repository **only** when nothing anybody asked for is outstanding *and*
weekly usage is under `SUGGEST_MIN_HEADROOM_PCT` (50).

### Moving one item

```bash
harness propose 4       # the work package -> a proposal pull request (gate 1)
harness approve 4       # proposed -> approved, by hand; normally the gate-1 merge does this
harness run --item 4    # implement, gates, package — bypasses the run window
harness package 4       # build the review package
harness deliver 4       # push the branch to the fork and open the upstream PR (gate 2)
harness revise 4 --source review --notes "..."    # one bounded revision cycle
harness decompose 4     # split into sub-issues
```

### Operating

```bash
harness halt            # create .harness/HALT — stops every spending workflow
harness resume          # remove it
harness sweep           # poll notifications, parse /harness commands, act on them
harness relabel         # migrate open issues from harness:* to stage:/kind:/via:
harness sync-fork       # fast-forward the fork from upstream; loud on divergence
harness init --labels   # create the nineteen labels
```

### The remaining three

```bash
harness setup --tier 2  # assess what a tier still needs and rewrite HUMAN.md with the checklist
harness archive 4       # promote a review package from runs/ into packages/, for the record
harness local-loop      # the container loop: dispatch, run, sleep — what the `bb` image runs
```

`setup` is the one to run when raising the tier: it reports each prerequisite, who has to satisfy it
(you or the harness), and refuses to claim readiness the credential does not have. See
[LOCAL-MODE.md](LOCAL-MODE.md) for `local-loop`.

**`harness halt` is the one to remember.** It is also just a file: committing anything at
`.harness/HALT` on `main` stops everything, and deleting it resumes. One commit either way, from a
phone, and you never need permission to use it.

---

## Reading the result

Three label families, because one flat list cannot answer three questions.

**`stage:`** — where is it, and whose move is it? Two are loud on purpose:

| Label | Means | Who moves it |
|---|---|---|
| `stage:queued` | eligible for a proposal | `discover` |
| `stage:planning` | a propose job is in flight | the job |
| **`stage:needs-approval`** | **proposal PR open — gate 1** | **you, by merging it** |
| `stage:ready` | approved; waiting for a runner | `implement` |
| `stage:building` | an implement job is in flight | the job |
| `stage:packaged` | review package built | the same job |
| **`stage:needs-review`** | **upstream PR open — gate 2** | **a human, by merging it** |
| `stage:revising` | a revision cycle is in flight | the job |
| `stage:done` | merged upstream — terminal | — |
| `stage:blocked` | stopped; needs a decision | you, by relabelling |
| `stage:needs-human` | retries spent | a trusted `/harness fix` |
| `stage:dropped` | closed without shipping — terminal | — |

**`kind:`** — `product` (work) · `audit` (a findings report) · `ops` (a failed run). There is
deliberately no `kind:harness`: the harness never works on its own repository, so that kind of work
does not exist and must not be nameable.

**`via:`** — `assigned` · `requested` · `suggested` · `audit`. `via:suggested` means **nobody asked
for this**; ignoring it is a complete answer.

Every transition posts a comment on the issue naming the stage, the workflow run, the cost and the
new state. **The issue thread is the log.**

---

See [FOR-MAINTAINERS.md](FOR-MAINTAINERS.md) for the five-minute version,
[USING.md](USING.md) for the reference behind this page, and [OPERATIONS.md](OPERATIONS.md) when
something is wrong.
