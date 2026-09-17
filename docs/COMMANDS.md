# Every command, with an example

Two surfaces. **Comment commands** (`/harness …`) operate the harness day to day, from a browser or
a phone. **CLI subcommands** (`harness …`) operate it from a machine or an Actions run, and are
mostly what the workflows call. This page is the reference for verbs, levels, labels and kill
switches.

- [What it can do, and where you say it](#what-it-can-do-and-where-you-say-it)
- [Where the harness is reading](#where-the-harness-is-reading)
- [How work reaches the queue](#how-work-reaches-the-queue)
- [Comment commands](#comment-commands)
- [Who may run what](#who-may-run-what)
- [CLI subcommands](#cli-subcommands)
- [Three kill switches](#three-kill-switches-and-what-each-one-stops)
- [Labels](#labels)

## What it can do, and where you say it

Most commands act on the thread they are written in, so the same words on the wrong thread do
nothing.

| I want to… | Say | Where |
|---|---|---|
| give it a job in words | `/harness work <what>` | the **inbox** issue |
| give it a specific ticket | `/harness work <link>` | the inbox, or **that product issue** |
| ask about the codebase (the lightest call) | `/harness ask <question>` | anywhere it reads |
| survey for problems (the heaviest call) | `/harness audit <lens>` | an issue **here** |
| turn a finding into work | `/harness promote <n>` | **that audit issue** |
| change a plan before code | `/harness revise <notes>` | the **proposal PR** |
| change the code after it | `/harness revise <notes>` · `/harness rebase` | the **delivery PR** |
| stop one thing | `/harness stop` | either PR, or the work item |
| see what is going on | `/harness status` | anywhere it reads |
| stop everything | `/harness halt` | anywhere it reads |
| start it again | `/harness resume` | anywhere it reads |
| give it time you are not using | `/harness block <n>` | anywhere it reads |

`go` and `split` make up the thirteen verbs. `/harness-<verb>` works as well as `/harness <verb>`.

## Where the harness is reading

| Thread | Heard? | How fast |
|---|---|---|
| **The inbox issue**, here | always; polled on every sweep | minutes |
| Any issue or PR **here** | yes; the comment event wakes the workflow | 👀 in seconds, answer in minutes |
| A **delivery PR** on brightboost | yes; the harness opened it | up to 3 h on a weekday |
| A brightboost issue it has **touched** | yes; it commented or was assigned | up to 3 h on a weekday |
| A **cold** brightboost issue | only if you `@`-mention the bot | up to 3 h on a weekday |

- **Naming the bot is a command form everywhere.** `@jgoetzmann-bot status` is the same command
  as `/harness status`, on every thread the harness reads. Only the machine account's handle
  counts: `@nathan status` is a sentence about Nathan.
- **On brightboost, `@jgoetzmann-bot` is also what delivers the comment.** The harness reads its
  notifications there, and a fresh issue sends it none unless the bot is mentioned.
- **Naming it with no verb gets a short reply** saying what to say here, rather than silence.
- **"Up to 3 hours" is a weekday figure.** The sweep's cron is `41 */3 * * 1-5`, so a comment left
  after Friday 21:41 UTC waits until Monday 00:41. To skip the wait, post any `/harness` command on
  the inbox: it wakes the same job, and its sweep reads the notifications.
- **On the harness repository**, `ack.yml` puts a 👀 on your comment within seconds and posts a
  short "Working on it" for a slow command. The answer can wait up to two hours while a build holds
  the shared lock.
- **Silence is ambiguous.** A comment that fails the trust gate is ignored with no reply.
  `/harness status` from a trusted handle tells that apart from the harness being asleep.
- **A scheduled run that never starts** is noticed by `watchdog.yml` within about four hours.

## How work reaches the queue

| Route | Label | Model call to create it? |
|---|---|---|
| `/harness work <link>`, `<number>` or `<sentence>` | `via:requested` | no |
| assigning `@jgoetzmann-bot` | `via:assigned` | no |
| `/harness promote <n>` from an audit | `via:audit` | no |
| daily triage of the `harness-ok` pool | `via:suggested` | one ranking call |

**Assigning the bot rarely helps.** GitHub offers an account as an assignee only when it is a
collaborator, an organisation member or already in the thread, and on brightboost the bot is only
ever the third (D68). Nearly every issue it has commented on already has a work item, which the
assigned sweep leaves alone. `/harness work <link>` on the inbox is the route.

**`via:suggested` is the only route the harness starts by itself.** On `discover`'s 11:07 UTC run it
suggests work only when nothing anybody asked for is outstanding (a delivery pull request awaiting
review counts), weekly usage is under `SUGGEST_MIN_HEADROOM_PCT` (50) or has never been observed,
and the issue carries `ALLOWLIST_LABEL` (`harness-ok`), the pool maintainers fill on brightboost. A
pool issue is still skipped if it is assigned to someone other than the bot, claimed by a branch or
open pull request, labelled `intern-starter`, `large` or `architecture`, or has ever had a work
item (B332). At most `SUGGEST_MAX_PER_RUN` (5) per run.

A suggestion is proposed first; then the harness comments on the issue saying it has a plan and has
not started. The operator's merge at gate 1 builds it whether or not anybody answered. A yes is a
plain comment on the proposal pull request; a no is `/harness stop` there. Do not answer with
`/harness go` (see [`go`](#go--proceed-with-this)).

**For every route**, one model call then writes a work package (diagnosis, approach, files,
behaviours, acceptance criteria), validates it, including that every path it names exists at the
base commit, and publishes it as a pull request adding `proposals/<id>-<slug>.md`. No code exists
until that pull request is merged.

## Comment commands

The form is any of these at the **start of a line**; the rest of the line is the argument, and
only that line — a multi-line note reaches the harness as its first line:

```
/harness work make the activity cards keyboard reachable
/harness-work make the activity cards keyboard reachable
@jgoetzmann-bot work make the activity cards keyboard reachable
```

The third form is the bot's own handle, and only the bot's: any other `@handle` at the start of a
line is prose. Naming it with no verb after it gets a short reply listing what makes sense there.

**Several commands go in one comment, one per line.** They run top to bottom and you get one reply,
with each answer under its command:

```
/harness-status
/harness-ask which component owns the activity cards
/harness-work make them keyboard reachable
```

That is a normal thing to send. A refused command does not stop the ones under it, and a line
whose verb is not a known one is skipped. At most ten commands are read from one comment, and
nothing inside a fenced code block is a command. Capitalisation does not matter and leading
`@mentions` are allowed, but `as discussed, /harness stop` is prose, not a command. Each comment is
acted on once; editing it does not re-run it.

### The thirteen

| Verb | What it does | Level |
|---|---|---|
| `work` | open a work item | 2 |
| `ask` | answer a question, change nothing | 1 |
| `status` | subscription usage, queue, and when the next thing happens | 1 |
| `audit` | read through one lens, open a findings issue | 2 |
| `promote` | turn findings into work items | 2 |
| `revise` | redo it with my notes | 2 |
| `rebase` | rebase onto the default branch, re-run the gates | 2 |
| `stop` | close it and stand down | 2 |
| `go` | proceed with this | 2 |
| `split` | break it into child issues | 2 |
| `halt` | stop all spending | 3 |
| `resume` | lift the halt | 3 |
| `block` | suspend the run window for n five-hour sessions | 3 |

Eight other words are understood: `fix` → `revise`, `reject` → `stop` (at level 3), `queue` → `go`,
`blocks` and `sessions` → `block`, and `usage`, `ledger` and `help` → `status`. There is
deliberately no `unblock`: an alias carries the verb and never the argument, so it would report a
block rather than cancel one. `/harness block 0` cancels.

### `work` — open a work item

```
/harness work make the activity cards keyboard reachable
/harness work https://github.com/Bright-Bots-Initiative/brightboost/issues/633
@jgoetzmann-bot /harness work
```

Opens a `stage:queued` issue in the harness repository and replies with a link. **It only queues.**
The plan comes from the next `discover` run (11:07 UTC daily), and the code from the first
run-window `implement` run after the proposal is merged; see
[FOR-MAINTAINERS.md §9](FOR-MAINTAINERS.md#9-how-long-one-request-takes).

A link is the same as `harness discover --mode directed --target 633`, and the same link twice is
one item. A sentence is the same item only when the same person sends the same words. A bare `#633`
counts only as the whole argument. On a brightboost issue, no argument means that issue.

### `ask` — answer, change nothing

```
/harness ask what does the game registry do
```

Reads the product repository and answers in the thread, naming the commit it read. No work item, no
branch, no state change. At most `ASK_MAX_PER_DAY` (20) across everyone; past that it says so and
makes no model call.

### `status` — what is going on

```
/harness status
```

Replies with the subscription left against both usage stops, the queue in priority order, what
GitHub Actions is running or has queued behind the ledger lock (and what it cancelled in the last
six hours), whether the harness is halted or blocked out, whether the run window is open, when the
next sweep runs, and whether suggested work is admitted. Changes nothing. Try it first when
nothing seems to be happening: it is what tells "queued behind a build" from "asleep".

### `audit` — one lens, one findings issue

```
/harness audit accessibility in src/components/activities
```

Opens one issue: a ranked findings report labelled `kind:audit`, with no stage label, so it never
enters the queue and creates no work. An audit cut short by its turn cap still opens the issue,
listing under `## Not reached` what it never opened. It is refused when seven-day usage is at or
above `AUDIT_MIN_HEADROOM_PCT` (75), and refused before any model call when the lens is missing.

### `promote` — turn findings into work

```
/harness promote 3
/harness promote 1,4,7
/harness promote all
```

**On the audit issue.** Each named finding becomes a `via:audit` work item, which goes through gate
1 like anything else, and is ticked off in the audit issue. Promoting a finding twice creates one
item. `all` takes every unticked finding, up to `SUGGEST_MAX_PER_RUN` (5).

### `revise` — redo it with my notes

```
/harness revise the migration has to be reversible; say how in the plan
```

| Where you say it | What happens |
|---|---|
| the **proposal** PR (gate 1) | back to `stage:planning`; a fresh `propose` call with your note as the brief |
| the **delivery** PR (gate 2) | one more implementation pass, then the complete gate sequence again |

On a delivery pull request it force-pushes to the fork only if the branch is under `harness/` and
its tip is still a commit the harness authored. Red gates afterwards block the item and push
nothing. The pull request body keeps the evidence it was opened with; the new gate run is in the
workflow artifact. Bounded by `MAX_REVISE_CYCLES` (3); at the cap the item goes
`stage:needs-human` and only a trusted `/harness revise` restarts it.

Write it in the Conversation tab's comment box or as an inline comment on the diff. A `/harness`
line in the *Review changes* summary box is never read (D68). Once a `revise` has arrived on a
delivery pull request, your review text is read as feedback too. The same applies to `rebase` and
`stop`.

### `rebase` — the same, after a conflict

```
/harness rebase
```

Rebases onto the product repository's default branch, then re-runs the gates. Same bounds as
`revise`.

### `stop` — close it and stand down

```
/harness stop we are removing this feature next sprint
```

Closes the pull request and stands the item down. How final it is depends on your level:

| Your level | What happens | Reversible |
|---|---|---|
| **2** (maintainer) | `stage:blocked`, parked; the branch and evidence stay | yes, `/harness go` |
| **3** (operator) | `stage:dropped`, ended, where the state machine allows it | no |

`/harness reject` is the level-3 form. The state machine adds two exceptions:

- An item with a delivery pull request open cannot be dropped, so it parks at either level.
- `stage:queued`, `stage:ready`, `stage:blocked` and `stage:needs-human` cannot move to blocked, so
  `stop` there ends the item at level 2 as well, and the reply says so. Asking again with the same
  link or words finds the ended item. A fresh `/harness work` sits in `stage:queued` for up to a
  day, so this is the likeliest `stop` a maintainer makes.

### `go` — proceed with this

```
/harness go
```

- On a **stopped or blocked** item: back to `stage:ready` on its branch if code had been written,
  otherwise back to `stage:queued`. This undoes a `stop`.
- On an ordinary **proposal**: nothing; the reply says that merging the proposal approves it.
- On a **suggestion** at gate 1: approves it. Do not use it there. Until the operator merges the
  proposal there is no plan on `main`, and every build run in the window fails looking for one
  (D68). After the merge `implement.yml` has already approved it.
- On the **inbox**: answers with the queue, like `/harness status`.

Already approved or already queued is a no-op that says so.

### `split` — break it up

```
/harness split
```

**On an issue in the harness repository.** One model call splits it into at most `MAX_SUBISSUES`
(8) child issues, each delivered separately; the parent goes `stage:blocked`. A child is never split
again, and children inherit the parent's `via:` label.

### `halt` and `resume`

```
/harness halt the usage looks wrong
/harness resume
```

`halt` stops every model call and every stage until `resume`, and records who stopped it and why.
Level 3 only. The workflows keep running, so the sweep still reads the `/harness resume`. The other
two switches are under [kill switches](#three-kill-switches-and-what-each-one-stops).

### `block` — give it sessions you are not using

```
/harness block 3
/harness block 1 away this afternoon
/harness block 0
```

**Level 3**, because it is the operator's subscription being spent — the same reason `--force` is.
It suspends the run window for the next `n` five-hour subscription sessions, so the dispatcher
stops holding approved work back until it expires. At most six sessions (thirty hours), and never
longer than the count however the session reading reads; more is refused and nothing changes,
because a longer dedication is a run-window change, which is a reviewed edit to
`.harness/config.json`.

The end is measured **once**, when the command is acted on: the remainder of the session in
progress plus `n − 1` whole ones when a reading exists, otherwise `n × 5 h` from now. A later
reading never moves it. It expires by itself — nothing has to run for it to end — and
`/harness block 0` cancels it early. With no argument it reports the block that stands, if any.

**It lifts the calendar and nothing else.** Both usage stops (90% weekly, 80% session),
`.harness/HALT`, `/harness halt`, the trust gate and both human gates still apply, and it does not
raise `MAX_CONCURRENT_ITEMS`: one item at a time, as always.

A block creates no workflow runs. It takes effect on runs that already happen — a gate-1 merge
starts one immediately, otherwise `feedback.yml`'s three-hourly weekday sweep or a manual dispatch
— so the reply names the next scheduled sweep. At a weekend `feedback.yml` does not run at all.

### `--force`

```
/harness work https://github.com/.../issues/633 --force
/harness go --force
```

Exempts the item from the run window (daily 11:00 → 15:00 UTC), and says so on the item, in the
ledger and in the reply. It is honoured by `implement`'s next run outside the window (a gate-1 merge
or the operator's dispatch), not by the sweep (D68), and proposes nothing sooner. Level 3 only;
below that the flag is dropped and the rest of the command runs. `.harness/HALT`, both usage stops
(90% weekly, 80% session), the turn caps and both human gates still apply.

## Who may run what

Two things must hold: your handle is in [`.harness/trust.txt`](../.harness/trust.txt) with a level,
**and** GitHub confirms who is typing, in one of two ways:

- **A vouch.** The line ends `vouch:<id>`, the numeric id from
  `GET https://api.github.com/users/<handle>`. It admits that one account on every repository,
  refuses any other account using the name, and still caps commands at the line's level (D68). This
  is the ordinary way to add somebody.
- **An association.** GitHub reports you as `OWNER`, `MEMBER` or `COLLABORATOR` on the repository
  you are commenting on, so it holds per repository. An organisation member whose membership is
  private reads as `CONTRIBUTOR` and needs a vouch.

A comment failing either half is ignored with no reply. `harness doctor` names anyone in the trust
file who has neither access nor a vouch.

| Level | Who | Commands |
|---|---|---|
| **3** | operator | everything below, plus `block`, `halt`, `resume`, `reject` and `--force` |
| **2** | maintainer | `audit` `go` `promote` `rebase` `revise` `split` `stop` `work` |
| **1** | asker | `ask` and `status` |
| **0** | not in the file | nothing; the comment body is never parsed |

`tests/test_docs_drift.py` checks this table against `keywords.VERB_LEVEL`. A verb above your level
gets a reply naming the level it needs.

## CLI subcommands

`harness <command>`. `--config`, `--json`, `--dry-run` and `--verbose` are global flags and go
before the subcommand: `harness --json discover …`, not `harness discover --json`.

### Looking

```bash
harness doctor      # binaries, versions, disk, halt, config, pin, trust levels
harness status      # queue by state, subscription usage, what is in flight, what Actions is doing
harness dispatch    # what may start now, the priority queue, why the head is not moving
harness block       # with no argument, the block that stands (if any)
harness ledger      # usage against each stop, calls made, window state
```

`dispatch` starts nothing. It answers "why is nothing happening":

```json
{
  "start": [],
  "reason": "outside run window (daily 11:00-15:00 UTC)",
  "skipped": {},
  "queue": [{ "class": "directed", "rank": 2, "item": 4, "state": "discovered" }],
  "head": { "item": 4, "reason": "waiting to be proposed" },
  "suggested": { "admitted": false, "reason": "work somebody asked for is still outstanding" }
}
```

### Adding somebody to the trust file

```bash
harness trust show                                # the file as the gate reads it
harness trust line nathan --level 2               # the exact line to paste
harness trust line invited --level 2 --no-vouch   # for somebody already a collaborator here
```

`trust line` looks up the login's numeric id and prints the line, for example
`2 BrightBoost-Tech vouch:193453438`. `--level` is required. If the id cannot be resolved, or the
login is an organisation, it prints nothing and exits non-zero. `trust show` prints each level, the
vouched entries, and every line being refused. Neither form writes anything or needs a working
`.env`; `.harness/` is outside the harness's write roots (B143), so the line reaches the file
through a reviewed pull request.

### Finding work

```bash
harness discover --mode directed --target 633   # one named product issue; no model call
harness discover --mode assigned                # every open issue assigned to the bot
harness discover --mode triage                  # rank the queue, or suggest from the pool
harness discover --mode audit --lens "accessibility in src/components"
```

- **`directed`** creates one work item for that product issue, or returns the existing one. A pull
  request number fails with `#N is a pull request, not an issue`. It skips every triage filter.
- **`assigned`** queues every open issue assigned to the machine account (the owner of `FORK_REPO`;
  with `FORK_REPO` empty it fails). No model call and no triage filter, so an assigned
  `intern-starter` issue is queued anyway. `feedback.yml` runs it on every sweep.
- **`triage`** ranks the items already in `stage:queued` in one model call and creates nothing.
  Only with an empty queue, and only when suggestion is admitted, does it read the product
  repository and apply the pool filters above. `--ignore-allowlist` removes only the label filter.
  `--lens` is ignored in this mode.
- **`audit`** is what `/harness audit` runs, and needs `--lens`.

The ids printed are harness work items, which `propose` takes. `--dry-run` records GitHub writes
instead of sending them, but the ranking call still runs. `discover.yml` takes the same `mode`,
`target`, `lens` and `ignore_allowlist` from the Actions tab and proposes each id it created.
`discover` is not held by the run window.

### Moving one item

```bash
harness propose 4       # the work package -> a proposal pull request (gate 1)
harness approve 4       # proposed -> approved by hand; normally the gate-1 merge does this
harness approve --merged   # approve every proposed item whose proposal file is on main (gate 1)
harness run --item 4    # implement, gates, package; bypasses the run window, not the usage stops
harness package 4       # build the review package
harness deliver 4       # push the branch to the fork and open the upstream PR (gate 2)
harness revise 4 --source review --notes "..."    # one bounded revision cycle
harness decompose 4     # split into sub-issues
```

### Operating

```bash
harness halt                 # create the local halt file (HALT_FILE, default ./HALT)
harness resume               # remove it
harness resume --commanded   # also lift a halt set by `/harness halt`
harness block 3              # suspend the run window for three five-hour sessions; 0 cancels
harness sweep                # poll notifications, parse /harness commands, act on them
harness tidy                 # rewrite the queue on the pinned issue; prune old bot comments
harness relabel              # migrate open issues from harness:* to stage:/kind:/via:
harness sync-fork            # fast-forward the fork from upstream; loud on divergence
harness init --labels        # create the nineteen labels
harness setup --tier 2       # what a tier still needs and who must act (writes HUMAN.md)
harness archive 4            # promote a review package from runs/ into packages/
harness local-loop           # the container loop: dispatch, run, sleep
harness ack --body-file comment.txt --actor jgoetzmann --association OWNER
```

See [LOCAL-MODE.md](LOCAL-MODE.md) for `local-loop`. `ack` is what `ack.yml` calls: it runs a
comment through the sweep's parser and trust gate and prints the "working on it" text, or nothing.
It never spends, never writes, and always exits 0. It answers `/harness status` itself, so that
one verb does not queue behind a build: the answer arrives in seconds, and your comment gets a
🚀 beside the 👀 once it has landed. That reaction is how the sweep knows not to answer the same
comment again minutes later. The fact lives in a reaction, which only the bot's own logins can
leave, rather than in the text of a reply — a reply is built partly from issue titles and model
output, which anybody can choose.

`tidy` is what `feedback.yml` calls after each sweep. It rewrites the queue between the
`<!-- queue:start -->` and `<!-- queue:end -->` markers on the pinned tracking issue, touching
nothing else in that body and sending no request when the text has not changed. Once a week it
also deletes the harness's **own** comments on the inbox and tracking issues, keeping the newest
twenty and anything under thirty days old. A comment is a candidate only when one of the two
logins the harness posts under — the machine account and `github-actions[bot]` — wrote it **and**
it carries the harness's marker, so no human comment can be reached. The marker alone would not
be enough: GitHub's quote-reply copies it into the comment of anybody who answers the harness
that way, and the author is what tells the two apart.

## Three kill switches, and what each one stops

| Switch | Set by | Stops | Cleared by |
|---|---|---|---|
| **`.harness/HALT`** on `main` | a commit; no command writes it | every spending workflow, first step | deleting the file |
| **the commanded halt** | `/harness halt` (level 3) | every model call and stage, on every runner | `/harness resume` |
| `HALT` at the repo root (`HALT_FILE`) | `harness halt` | a local run only | `harness resume` |

- **The committed file is the strongest.** Workflows check it before anything else, so it holds even
  if the ledger cannot be read. Anyone with write access to the harness repository can add or
  delete it in one commit. Maintainers have no such access (D30); their lever is `/harness stop`.
- **The root file is gitignored**, so an Actions runner never sees it. The CLI command stops a local
  run; the comment `/harness halt` stops the fleet.
- **If the sweep itself is broken**, `harness resume --commanded` lifts a commanded halt.
- **In local mode** the commanded halt travels in `state/ledger.json` on the `harness-state` branch,
  which a `local-loop` container does not read; stop the container instead.

[OPERATIONS.md](OPERATIONS.md) has the full procedure, including what each switch does not stop.

## Labels

**`stage:`** — where an item is, and whose move it is. Each item carries exactly one.

| Label | Means | Who moves it |
|---|---|---|
| `stage:queued` | eligible for a proposal | `discover` |
| `stage:planning` | a propose job is in flight | the job |
| **`stage:needs-approval`** | **proposal PR open — gate 1** | **the operator, by merging it** |
| `stage:ready` | approved; waiting for a runner | `implement` |
| `stage:building` | an implement job is in flight | the job |
| `stage:packaged` | review package built | the same job |
| **`stage:needs-review`** | **upstream PR open — gate 2** | **a human, by merging it** |
| `stage:revising` | a revision cycle is in flight | the job |
| `stage:done` | merged upstream — terminal | a person, by relabelling |
| `stage:blocked` | stopped; needs a decision | `/harness go`, or relabelling |
| `stage:needs-human` | revise cycles spent | a trusted `/harness revise` |
| `stage:dropped` | closed without shipping — terminal | — |

Nothing sets `stage:done` automatically. After a delivery pull request merges upstream, relabel its
harness issue by hand: `depends_on` waits on that label, so an item left at `stage:needs-review`
holds back its dependants.

**`kind:`** — `product` (work) · `audit` (a findings report) · `ops` (a failed run). There is no
`kind:harness`, because the harness never works on its own repository.

**`via:`** — `assigned` · `requested` · `suggested` · `audit`. `via:suggested` means nobody asked
for it.

Every transition posts a comment on the issue naming the stage, the workflow run and the new state,
so the issue thread is the item's log.

See also [FOR-MAINTAINERS.md](FOR-MAINTAINERS.md) (the five-minute version),
[OPERATIONS.md](OPERATIONS.md) (when something is wrong), [SAFETY.md](SAFETY.md) (the boundaries)
and [PACKAGE-FORMAT.md](PACKAGE-FORMAT.md) (what a proposal and a delivery contain).
