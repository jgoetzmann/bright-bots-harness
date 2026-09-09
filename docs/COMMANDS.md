# Every command, with an example

Two surfaces. **Comment commands** (`/harness …`) are how the harness is operated day to day, from
a browser or a phone. **CLI subcommands** (`harness …`) are how it is operated from a machine or an
Actions run, and are mostly what the workflows call for you.

If you only read one section, read [Comment commands](#comment-commands). The CLI is not required to
use the harness.

- [What it can do, and where you say it](#what-it-can-do-and-where-you-say-it)
- [Where the harness is reading](#where-the-harness-is-reading)
- [How it decides what to propose](#how-it-decides-what-to-propose)
- [Comment commands](#comment-commands)
  - [Asking for work](#asking-for-work) · [Steering work](#steering-work-that-exists) ·
    [Asking questions](#asking-questions) · [Audits](#audits) ·
    [Looking and stopping](#looking-and-stopping) · [`--force`](#--force)
- [Who may run what](#who-may-run-what)
- [CLI subcommands](#cli-subcommands)
- [Reading the result](#reading-the-result)

---

## What it can do, and where you say it

Ten things, and the place you say each one. **Where matters**: most commands act on the thread
they are written in, so the same words on the wrong thread do nothing.

| I want to… | Say | Where | Costs |
|---|---|---|---|
| give it a job in words | `/harness work <what>` | the **inbox** issue | — |
| give it a specific ticket | `/harness work <link>` · or **assign the bot** | the inbox, or **that product issue** | — |
| ask about the codebase | `/harness ask <question>` | **anywhere it reads** | cents |
| survey for problems | `/harness audit <lens>` | an issue **here** | up to $20 |
| turn a finding into work | `/harness promote <n>` | **that audit issue** | — |
| change a plan before code | `/harness revise <notes>` | the **proposal PR** | ~$0.50, a fresh `propose` |
| change the code after it | `/harness fix <notes>` · `/harness rebase` | the **delivery PR** | ~$1.00, a `revise` cycle |
| stop one thing | `/harness stop` · `/harness reject` | that PR | — |
| see what is going on | `/harness usage` | **anywhere it reads** | — |
| stop everything | `/harness halt` | **anywhere it reads** | — |
| start it again | `/harness resume` | **anywhere it reads** | — |

`go`, `queue` and `split` round out the fifteen verbs; all of them are in the tables below.

## Where the harness is reading

It cannot answer where it cannot hear you, and the two repositories work differently.

| Thread | Heard? | How, and how fast |
|---|---|---|
| **The inbox issue**, here | **always** | **polled** on every sweep, whether or not anyone is subscribed — this is why the inbox is the reliable front door |
| Any issue or PR **here** | yes | the comment event wakes the workflow directly · **minutes** |
| A **delivery PR** on brightboost | yes | the harness opened it, so it is subscribed · **up to 3 h on a weekday** |
| A brightboost issue it has **touched** | yes | it commented or was assigned, so it is subscribed · **same** |
| A **cold** brightboost issue | **only if you `@`-mention it** | nothing else generates a notification for an account that has never touched the thread |

Two consequences worth knowing:

- **On brightboost, `@jgoetzmann-bot` is not politeness — it is the delivery mechanism.** Without it
  a comment on a fresh issue is never seen at all.
- **"Up to 3 hours" is a weekday figure.** The sweep's cron is `41 */3 * * 1-5`, so a comment left on
  brightboost after Friday evening waits until Monday morning — around **51 hours** at worst.
  `/harness usage` prints the next scheduled sweep, so you never have to work it out.
- **Silence is ambiguous.** A comment from someone outside `trust.txt`, or from someone not invited
  to the repository, is read, counted as denied, and ignored **with no reply**. That looks exactly
  like the harness being asleep. `/harness usage` from a trusted handle is the quickest way to tell
  the difference — if it answers, the harness is listening and the problem was your permissions.

## How it decides what to propose

Work reaches the queue four ways, and **three of the four are somebody asking**:

| Route | Label | Model call to create it? |
|---|---|---|
| `/harness work <link>` or a bare number | `via:requested` | **no** — it just records the pointer |
| `/harness work <sentence>` | `via:requested` | **no** |
| assigning `@jgoetzmann-bot` | `via:assigned` | **no** — which issues are assigned is a fact, not a judgement |
| `/harness promote <n>` from an audit | `via:audit` | **no** — the audit already did the reading |

The fourth is the harness's own idea, and it is fenced:

**`via:suggested`** — weekly discovery, and the only route the harness starts by itself. Three
conditions, all required: nothing anybody asked for is outstanding, weekly usage is under
`SUGGEST_MIN_HEADROOM_PCT` (50), and the issue carries `ALLOWLIST_LABEL` (`harness-ok`) — which
nothing on brightboost does today, so in practice this route finds nothing unless someone passes
`ignore_allowlist`. It queues at most `SUGGEST_MAX_PER_RUN` (5).

Be clear about what the green light does and does not gate: the **proposal is written first**, and
*then* the harness comments on the unassigned issue saying it has a plan, has not started the
implementation, and will not without a green light. So a suggestion costs one `propose` call before
anybody is asked. `/harness go` gates the expensive half — the implement run — not the cheap one.
Ignoring the comment is a complete answer.

**Then, for every route alike**, one model call turns the item into a *work package*: the diagnosis,
the approach, the exact files it intends to touch, the behaviours it will add, and how a reviewer
will know it worked. That package is validated before anyone sees it — including that every path it
names **actually exists at the base commit** — and published as a pull request adding
`proposals/<id>-<slug>.md`.

**No code has been written at that point.** That is the whole design: the cheap artefact comes
first, you approve or redirect it, and only a merge turns it into an implement run.

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
one of the fifteen, the whole comment is discarded rather than falling through to the next line.

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

#### `stop` — halt it

```
/harness stop
```

**On the delivery pull request.** Closes it and moves the item to `stage:blocked`, freeing the
concurrency slot. The branch and the evidence stay where they are, and `/harness queue` puts it
back.

Not `stage:dropped`: an item with a delivery pull request open is *stopped for a decision*, not
discarded, and `shipped → abandoned` is a transition the state machine refuses on purpose.

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

Returns a blocked item to `stage:queued`. A no-op if it is already there. This is how you undo a
`stop`, or restart something the gates blocked once you have dealt with the cause.

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

### Looking and stopping

#### `usage` — what is going on

```
/harness usage
```

Replies in the thread with the spend against both usage stops, the queue in priority order, and
**when the next thing happens** — the next sweep, whether the run window is open, and whether
suggested work is admitted. Changes nothing, and level 1 can run it.

This is the first thing to try when nothing seems to be happening. `/harness queue` on the inbox
does the same, because there is no work item there to put back.

#### `halt` — stop everything

```
/harness halt the spend looks wrong
```

Stops the harness **spending anything** until it is resumed. Recorded in the ledger with who
stopped it and why, and reported at the top of `/harness usage` and by `harness dispatch`. **Level 3
only.**

It stops the model calls, not the workflow runs — so the sweep keeps listening, which is what lets
`/harness resume` lift it the same way it went on.

```
/harness resume
```

**To stop the workflows themselves**, not just the spending, commit a file at `.harness/HALT`. That
is the switch that runs before anything else, and no command writes it. There are three switches
in total — see [the table below](#three-kill-switches-and-what-each-one-stops).

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
`OWNER`, `MEMBER` or `COLLABORATOR` on **the repository you are commenting on**.

That second half is **per-repository**, which is more useful than it sounds: someone who is a member
of `Bright-Bots-Initiative` but not of the harness repository can steer work where it lands — `fix`,
`rebase`, `stop`, `ask` on brightboost issues and delivery pull requests — while the harness's own
threads (the inbox, proposal pull requests, work items) stay with the people who run it. That is a
deliberate arrangement, not a misconfiguration, and `harness doctor` reports it as a warning rather
than a problem.

A comment failing either half is read, counted as denied, and ignored **with no reply**, so it looks
exactly like the harness being asleep. `harness doctor` names anyone in the trust file who has no
access.

| Level | Who | Commands |
|---|---|---|
| **3** | operator | everything, including `reject`, `halt`, `resume` and `--force` |
| **2** | maintainer | `work` `queue` `go` `audit` `promote` `revise` `fix` `rebase` `split` `stop` |
| **1** | asker | `ask`, `usage` |
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

`harness <command>`. Most of these are what the workflows call; you rarely need them by hand.

`--json` is a **global** flag and goes before the subcommand — `harness --json discover …`, not
`harness discover --json`. Not every subcommand varies its output for it.

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
  "queue": [
    { "class": "directed", "rank": 2, "item": 4, "label": "#4 widen the bundle glob",
      "state": "discovered", "forced": false }
  ],
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

`triage` reaches the product repository **only** when nothing anybody asked for is outstanding, and
weekly usage is under `SUGGEST_MIN_HEADROOM_PCT` (50) — or has never been observed, which is not
treated as "no headroom". Even then it still requires `ALLOWLIST_LABEL` (`harness-ok`) on the issue
unless you pass `--ignore-allowlist`, and nothing on the product repository carries that label
today. Assignment and `/harness work` are the routes that work.

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
harness halt            # create the LOCAL halt file (HALT_FILE, default ./HALT)
harness resume          # remove it
harness resume --commanded   # also lift a halt set by `/harness halt`
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

### Three kill switches, and what each one stops

This trips people, so it is worth being exact.

| Switch | Set by | Stops | Cleared by |
|---|---|---|---|
| **`.harness/HALT`**, committed on `main` | **a commit** — no command writes it | **every spending workflow**, before the dispatcher and before a single token | deleting the file |
| **the commanded halt** | `/harness halt` (level 3) | **every model call and every stage**, on every runner that reads the ledger | `/harness resume` |
| `HALT` at the repo root (`HALT_FILE`) | `harness halt` | a **local** `harness run` only | `harness resume` |

`/harness resume` is the ordinary way to lift a commanded halt, but it arrives through the sweep —
so if the *sweep* is what is broken, `harness resume --commanded` lifts it from a terminal without
depending on the thing that is stuck.

The root file is gitignored, so an Actions runner never sees it — the **CLI** `harness halt` does
not stop the fleet, while the **comment** `/harness halt` does.

**The strongest is the commit.** `.harness/HALT` runs before the job does anything at all, so it
holds even if the ledger cannot be read. Any content. Deleting it resumes. One commit either way,
from a phone, and you never need permission to use it.

> **Local mode caveat.** The commanded halt travels in `state/ledger.json`, which Actions runners
> fetch from the `harness-state` branch. A container running `local-loop` against its own local
> ledger will not see a halt set from a comment — stop that with `harness halt` or by stopping the
> container.

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
