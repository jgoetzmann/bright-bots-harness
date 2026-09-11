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

| I want to… | Say | Where | Weight |
|---|---|---|---|
| give it a job in words | `/harness work <what>` | the **inbox** issue | — |
| give it a specific ticket | `/harness work <link>` | the inbox, or **that product issue** | — |
| ask about the codebase | `/harness ask <question>` | **anywhere it reads** | the lightest call |
| survey for problems | `/harness audit <lens>` | an issue **here** | the heaviest call; refused once the week is 75% used |
| turn a finding into work | `/harness promote <n>` | **that audit issue** | — |
| change a plan before code | `/harness revise <notes>` | the **proposal PR** | one fresh `propose` call |
| change the code after it | `/harness revise <notes>` · `/harness rebase` | the **delivery PR** | one `revise` cycle |
| stop one thing | `/harness stop` | either PR | — |
| see what is going on | `/harness status` | **anywhere it reads** | — |
| stop everything | `/harness halt` | **anywhere it reads** | — |
| start it again | `/harness resume` | **anywhere it reads** | — |

`go` and `split` round out the twelve verbs; all of them are in the tables below, and
`/harness-<verb>` works as well as `/harness <verb>`.

**Assigning the bot is a way in on paper, and rarely in practice.** GitHub offers an account as an
assignee only when it is a collaborator, an organisation member or already in the thread, and on
brightboost the bot is only ever the third (D68). Nearly every issue it has commented on already
has a work item, which the assigned sweep leaves alone, so assignment helps only where the bot
answered without opening one — after an `ask`, say. `/harness work <link>` on the inbox is the
route.

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
  `/harness status` prints the next scheduled sweep, so you never have to work it out.
- **Silence is ambiguous.** A comment from someone outside `trust.txt`, or from someone neither
  invited to the repository nor vouched for, is read, counted as denied, and ignored **with no
  reply**. That looks exactly
  like the harness being asleep. `/harness status` from a trusted handle is the quickest way to tell
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

**`via:suggested`** — weekly discovery (`discover`'s Sunday 07:17 UTC run, in triage mode), and
the only route the harness starts by itself. Three conditions, all required: nothing anybody asked
for is outstanding — a delivery pull request awaiting review counts — weekly usage is under
`SUGGEST_MIN_HEADROOM_PCT` (50), and the issue carries `ALLOWLIST_LABEL` (`harness-ok`). That label
is **the pool**: a maintainer adds it to a brightboost issue, or takes it off, on brightboost
itself, with no command. Even in the pool, an issue assigned to someone else, claimed by a branch
or open pull request, labelled `intern-starter`, `large` or `architecture`, or that has ever had a
work item in any state (B332) is skipped. It queues at most `SUGGEST_MAX_PER_RUN` (5) per run.

Be clear about what the green light does and does not gate: the **proposal is written first**, and
*then* the harness comments on the unassigned issue saying it has a plan, has not started the
implementation, and will not without a green light. So a suggestion costs one `propose` call before
anybody is asked. What builds it is the operator's merge at gate 1, because the plan implement
reads is the merged file — and `implement.yml` approves a merged proposal whether or not anybody
said yes, so ignoring the comment is **not** a no. A maintainer's yes is a plain comment on the
proposal pull request; a no is `/harness stop` there, which parks the item, and B332 keeps the
issue from being suggested again. `/harness go` is no yes at all while D68's defect stands (see
`go`, below).

**Then, for every route alike**, one model call turns the item into a *work package*: the diagnosis,
the approach, the exact files it intends to touch, the behaviours it will add, and how a reviewer
will know it worked. That package is validated before anyone sees it — including that every path it
names **actually exists at the base commit** — and published as a pull request adding
`proposals/<id>-<slug>.md`.

**No code has been written at that point.** That is the whole design: the cheap artefact comes
first, you approve or redirect it, and only a merge turns it into an implement run.

---

## Comment commands

There are **twelve**, and the form is either of these at the **start of a line**:

```
/harness work make the activity cards keyboard reachable
/harness-work make the activity cards keyboard reachable
```

Everything after the verb to the end of that line is the argument. The hyphenated spelling makes a
command a single token, which is what makes the next part read cleanly.

**Several commands go in one comment, one per line.** They run top to bottom, and the harness
answers once, with each answer labelled:

```
/harness-status
/harness-ask which component owns the activity cards
/harness-work make them keyboard reachable
```

That is a normal thing to send. They run top to bottom and **you get one reply**, with each answer
under the command that asked for it. A command that is refused — wrong level, wrong surface — does
not stop the ones under it.

Two limits apply, and neither is one you would type into: **at most ten commands from one comment**,
and **nothing inside a fenced code block is a command** — so quoting the example above to explain it
to somebody does not run it.

Three things are forgiven, because all three are what people actually type:

| You type | Read as |
|---|---|
| `/Harness work …` | fine — capitalisation does not matter (phones capitalise the first word) |
| `@jgoetzmann-bot /harness work …` | fine — leading `@mentions` are allowed |
| `as discussed, /harness stop` | **not a command** — prose that mentions it is still prose |

A line whose verb is not one of the twelve is skipped, and the other lines still run.

### The twelve

| Verb | What it does | Level |
|---|---|---|
| `work` | open a work item | 2 |
| `ask` | answer a question, change nothing | 1 |
| `status` | allowance left, queue, and when the next thing happens | 1 |
| `audit` | read through one lens, open a findings issue | 2 |
| `promote` | turn findings into work items | 2 |
| `revise` | redo it with my notes | 2 |
| `rebase` | rebase onto the default branch, re-run the gates | 2 |
| `stop` | close it and stand down | 2 |
| `go` | proceed with this | 2 |
| `split` | break it into child issues | 2 |
| `halt` | stop all spending | 3 |
| `resume` | lift the halt | 3 |

Six other words are understood and mean the verb they became, so comments already written keep
working: `fix` → `revise`, `reject` → `stop`, `queue` → `go`, `usage` → `status`. So do
`ledger` → `status` and `help` → `status`, which are not old verbs at all — they are what people
reach for. `/harness ledger` was typed twice on the live inbox before anyone noticed it parsed as
nothing.

**Why four names went away.** Each of them named a distinction the *thread you are standing on*
had already made. `fix` and `revise` differed only in whether code existed yet — which is exactly
what "proposal pull request" versus "delivery pull request" says. `reject` and `stop` differed only
in which gate you were at. `queue` and `go` were both "proceed with this", and which one applied
depended on a state nobody could see. Asking someone to pick the right word for a fact the surface
already carries only gives them a way to be wrong.

### Asking for work

#### `work` — open a work item

```
/harness work make the activity cards keyboard reachable
```

Opens an issue in the harness repository, queued, and replies in the thread with a link to it.
**It only queues.** The plan comes from the next `discover` run — Sunday 07:17 UTC, unless the
operator dispatches one sooner — and the code from the first run-window `implement` run after the
proposal is merged. [FOR-MAINTAINERS.md §9](FOR-MAINTAINERS.md#9-how-long-one-request-takes) walks
through a Wednesday request end to end.

```
/harness work https://github.com/Bright-Bots-Initiative/brightboost/issues/633
```

Same, but tracking that issue — identical to `discover --mode directed --target 633`. **The same
link twice is one item, never two**, whoever sends it; a sentence is the same item only when the
same person sends the same words. A bare `#633` works too, but only when it is the *whole* argument:
`fix 3 of the cards` is a sentence, not a pointer to issue 3.

**Where:** the pinned inbox issue, or the product issue itself (mention the bot there — see below).

```
@jgoetzmann-bot /harness work
```

On a brightboost issue, with no argument, this means *this one*. The mention is not politeness: the
harness finds product-repository comments by reading its notifications, and it is not subscribed to a
thread it has never touched, so the mention is the only thing that makes a cold issue visible to it.

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

#### `status` — what is going on

```
/harness status
```

Replies in the thread with **how much of the subscription is left** against both stops, the
queue in priority order, and
**when the next thing happens** — the next sweep, whether the run window is open, and whether
suggested work is admitted. Changes nothing, and level 1 can run it.

This is the first thing to try when nothing seems to be happening. `/harness usage` is the same
command under its old name.

### Audits

#### `audit` — one lens, one findings issue

```
/harness audit accessibility in src/components/activities
/harness audit redundant code
```

Reads the product repository through that one lens and opens **one** issue: a ranked findings report
labelled `kind:audit`, deliberately with **no stage label** — so the store cannot see it and it can
never enter the queue.

It creates **no work items**. An audit stopped by its cap still opens the issue, marked
incomplete, listing under `## Not reached` what it never opened.

**Two bounds, and only one of them is real.** `AUDIT_CAP_USD` (20.00) is passed to the runner as
`--max-budget-usd` and does stop a single call — but it is an estimate of API-equivalent cost, and
on a subscription nobody bills that. The bound that matters is `AUDIT_MIN_HEADROOM_PCT` (75): an
audit is refused outright when the seven-day subscription usage is already at or above it. It is
the longest single call the harness makes, and **the allowance is shared with everything else this
account does** — so starting a twenty-minute read with a fifth of the week left is how you find it
gone the next time you need it yourself.

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

### Steering work that exists

#### `revise` — redo it with my notes

```
/harness revise the migration has to be reversible; say how in the plan
/harness revise the null check belongs in the caller, not the helper
```

One verb, and **the pull request you are on decides what it means**:

| Where you say it | What happens |
|---|---|
| the **proposal** pull request (gate 1) | back to `planning`; the work package is rewritten with your note as the brief. No code exists yet, so this is the cheap place to change direction. |
| the **delivery** pull request (gate 2) | one more implementation pass against your feedback, then the **complete** gate sequence again — not just the tests that were failing. |

On a delivery pull request it force-pushes to the fork only if the branch is under `harness/` and its
tip is still a commit the harness authored. Bounded by `MAX_REVISE_CYCLES` (3); at the cap the item
goes `stage:needs-human` and only a trusted `/harness revise` restarts it.

`/harness fix` is the same command under its old name.

**Where on the pull request.** In the comment box at the foot of the Conversation tab, or as an
inline comment on a line of the diff — **not** in the *Review changes* summary box. The sweep reads
comments and inline review comments, never a review's summary, so a `/harness` line typed there is
never seen and gets no reply (D68). On a delivery pull request, once a `revise` has arrived, the
text of your reviews is read as feedback along with it. The same holds for `rebase` and `stop`.

#### `rebase` — the same, after a conflict

```
/harness rebase
```

Rebases onto the product repository's default branch, then re-runs the gates.

#### `stop` — close it and stand down

```
/harness stop we are removing this feature next sprint
```

Closes the pull request and stands the item down, freeing the concurrency slot. On the
**proposal** pull request this is a refusal at gate 1; on the **delivery** pull request it is an
abort at gate 2. The thread says which, so there is no second word to remember.

**How final it is depends on your level**, which is the distinction the old name `reject` carried:

| Your level | What happens | Reversible |
|---|---|---|
| **2** (maintainer) | `stage:blocked` — parked. The branch and the evidence stay where they are | yes, `/harness go` |
| **3** (operator) | `stage:dropped` — ended, where the state machine allows it | no |

Level 2 is enough to keep something out of a product repository, which is what a product maintainer
needs; ending a work item for good stays with the operator. `/harness reject` is the old spelling
of the level-3 version and still needs level 3.

Two exceptions, one each way, because the state machine decides where an item *can* go:

- **An item with a delivery pull request open cannot be dropped** (`shipped → abandoned` is refused
  on purpose), so it parks at whichever level you are — it is *stopped for a decision*, not
  discarded.
- **Four stages have nowhere to park**, so `stop` there ends the item at level 2 as well:
  `stage:queued`, `stage:ready`, `stage:blocked` and `stage:needs-human` have no edge to `blocked`.
  The reply says it ended the item. Asking again with the same link, or the same person using the
  same words, finds the ended item rather than opening a new one. A fresh `/harness work` sits in
  `stage:queued` until Sunday, so this is the likeliest `stop` a maintainer makes.

#### `go` — proceed with this

```
/harness go
```

Means one thing to say and two things to do, and **where the item already is** decides which:

- a **suggestion** waiting for a green light becomes approved — and that is a defect, not a
  feature, so **do not use `go` on a suggestion**. Until the operator merges the proposal there is
  no plan on `main` to build from, and every build run inside the window — `implement`'s and the
  three-hourly sweep's — fails looking for one until the merge (D68). After the merge
  `implement.yml` has already approved it and `go` only says so. A maintainer's yes is a plain
  comment on the proposal pull request; a no is `/harness stop`. Assigning the bot to an issue it
  has already suggested does nothing further.
- an item that was **stopped or blocked** picks up where it left off. If code had already been
  written it returns to `stage:ready` on the branch it already has; if not, it goes back to
  `stage:queued`. This is how you undo a `stop`.
- on the **inbox**, where there is no item to proceed with, it answers with the queue — the same
  report as `/harness status`.

**`go` is not a way past gate 1.** On an ordinary proposal it says so and changes nothing: merging
the proposal pull request is what approves that. The green light above applies only to work the
harness suggested on its own, and even there the merge is what lets it be built.

Already approved, or already queued, is a no-op that says so. `/harness queue` is the old name.

#### `split` — break it up

```
/harness split
```

**On an issue in the harness repository.** Decomposes it into at most `MAX_SUBISSUES` (8) child
issues, each delivered separately; the parent goes `stage:blocked`. A child is never split again.
Costs a model call.

Sub-issues inherit how the parent arrived, so splitting a suggestion produces suggestions.

### Stopping everything

#### `halt` — stop all spending

```
/harness halt the spend looks wrong
```

Stops the harness **spending anything** until it is resumed. Recorded in the ledger with who
stopped it and why, and reported at the top of `/harness status` and by `harness dispatch`.
**Level 3 only.**

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

Exempts the item from the run window (Mon 08:00 → Tue 20:00 UTC). Recorded on the item and in the
ledger and named in the reply, so "why did this run on a Thursday" has an answer in the issue. The
exemption is honoured by `implement`'s next run — a gate-1 merge starts one, so does the Tue
20:23 UTC cron, or the operator dispatches it — not by the three-hourly sweep, whatever the
reply's "next sweep" says (D68). It
proposes nothing sooner: a forced `/harness work` still waits for `discover` and for the merge.

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
`OWNER`, `MEMBER` or `COLLABORATOR` on **the repository you are commenting on** — or your line
must vouch for your account (below).

For a line **without** a vouch, that second half is **per-repository**: someone who is a public
member of `Bright-Bots-Initiative` but not of the harness repository can steer work where it
lands — `revise`, `rebase`, `stop`, `ask` on brightboost issues and delivery pull requests — while
the harness's own threads (the inbox, proposal pull requests, work items) stay with the people who
run it. For such a line that is a deliberate arrangement, not a misconfiguration, and
`harness doctor` reports it as a warning rather than a problem.

It has a trap: an organisation member whose membership is **private** is reported as
`CONTRIBUTOR`, which is how `BrightBoost-Tech` reads on brightboost. For someone like that, or
someone who must not have access to the harness repository (D30), the trust line ends
`vouch:<id>` instead: their numeric account id, the `id` field of
`GET https://api.github.com/users/<handle>`. A vouched line admits that one account on every
repository whatever GitHub reports, refuses any other account using the name, and still caps the
commands at the line's level (D68). `BrightBoost-Tech` is vouched, so it is heard on both
repositories — the inbox and proposal pull requests here as well as brightboost — and doctor does
not warn about it.

A comment failing either half is read, counted as denied, and ignored **with no reply**, so it looks
exactly like the harness being asleep. `harness doctor` names anyone in the trust file who has
neither access nor a vouch, and lists every vouch.

| Level | Who | Commands |
|---|---|---|
| **3** | operator | everything, including `halt`, `resume`, `reject` (the terminal form of `stop`) and `--force` |
| **2** | maintainer | `work` `go` `audit` `promote` `revise` `rebase` `split` `stop` |
| **1** | asker | `ask`, `status` |
| **0** | not in the file | nothing; the comment body is never parsed |

Using a verb above your level is answered, not silent: the reply names the level it needed.

### Latency, and how you know it heard you

| Where | How fast |
|---|---|
| Harness repository | 👀 within seconds, then the answer in minutes — up to two hours while a build holds the shared lock |
| Product repository | up to 3 hours on a weekday; a Saturday comment waits until Monday |

**On the harness repository you get an answer twice.** Within a few seconds a 👀 reaction appears on
your comment, and if anything you asked for takes more than a moment, a short comment says what it is
doing and roughly how long:

> **Working on it.**
>
> - `/harness ask` — cloning the product repository and reading it. About **a couple of minutes**.
> - `/harness work` — opening the work item. About **under a minute**.

That is `ack.yml`, and it exists because silence and thinking look identical from a thread — and
silence is the one people act on, by commenting again or by concluding the thing is off. It runs
before any work starts, takes no lock, installs nothing, and cannot spend. A comment of only fast
verbs gets the reaction and no comment, because an acknowledgement that lands two seconds before the
answer is just a second notification.

It uses the same parser and the same trust gate as the real thing, so it cannot promise work that
will not happen: a fenced block, an unknown verb or an untrusted commenter gets nothing.

**On the product repository there is no reaction and no acknowledgement.** The harness gets no
events there — it is not a collaborator and must not be. It reads its notifications on
`feedback.yml`'s schedule (`41 */3 * * 1-5`). To skip the wait, post any `/harness` command on the
inbox — `/harness status` will do. That wakes the same job, and its sweep reads the notifications
on the way, so a maintainer with no access to this repository has the lever too. The operator can
also run `feedback` from the Actions tab.

**If a scheduled run never happens**, `watchdog.yml` notices within about four hours and starts one.
That covers the case nothing else does: a run that *fails* files an ops issue and is retried, but a
run that never *starts* does neither, because nothing fired. GitHub drops scheduled runs under load,
and disables schedules outright on a repository with no pushes for 60 days — the watchdog tells those
two apart and says which it found.

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
unless you pass `--ignore-allowlist`: that label is the pool maintainers fill on the product
repository. At most `SUGGEST_MAX_PER_RUN` (5) are queued per run.

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

### The remaining four

```bash
harness setup --tier 2  # assess what a tier still needs and rewrite HUMAN.md with the checklist
harness archive 4       # promote a review package from runs/ into packages/, for the record
harness local-loop      # the container loop: dispatch, run, sleep — what the `bb` image runs
harness ack --body-file comment.txt --actor jgoetzmann --association OWNER
```

`setup` is the one to run when raising the tier: it reports each prerequisite, who has to satisfy it
(you or the harness), and refuses to claim readiness the credential does not have. See
[LOCAL-MODE.md](LOCAL-MODE.md) for `local-loop`.

`ack` is the one you will never type. It is what `ack.yml` calls to decide whether a comment deserves
a "working on it", and it prints the text or nothing at all. It exists as a subcommand rather than as
script inside the workflow so that it uses the *same* parser and the *same* trust gate as the sweep —
a second copy of either would eventually disagree with the first, and the way you would find out is
somebody being told the harness heard them when it did not. It never spends, never writes, and always
exits 0: an acknowledgement that can fail the run it precedes is a worse bargain than no
acknowledgement.

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
from a phone, for anyone with write access to the harness repository, and it needs nobody's
permission. A maintainer has no such access (D30), so a maintainer's levers are `/harness stop` on
the thing that looks wrong, which parks it (or, on a queued or ready item, ends it), and asking the
operator for the rest.

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
for this**. Ignoring one is not a no — the operator's merge builds it regardless — so a no is
`/harness stop` on its proposal pull request.

Every transition posts a comment on the issue naming the stage, the workflow run, the cost and the
new state. **The issue thread is the log.**

---

See [FOR-MAINTAINERS.md](FOR-MAINTAINERS.md) for the five-minute version,
[USING.md](USING.md) for the reference behind this page, and [OPERATIONS.md](OPERATIONS.md) when
something is wrong.
