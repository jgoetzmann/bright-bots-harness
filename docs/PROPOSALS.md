# Finding work, and steering it

Discovery has three modes, one of them is refused outright, and only one of the other two puts new
work in the queue today. All of them end in the same place: a pull request in this repository adding
`proposals/<id>-<slug>.md`. Merging it is approval; closing it is rejection. This document covers the
half of the flow before that gate — how work is found, how to aim it at a ticket you care about, what
the proposal actually contains, and how to change its mind.

Commands run from a checkout with the venv active. The same commands work in local mode
(`STORE_BACKEND=sqlite`), with two differences: a work item is a row in `harness.db` rather than an
issue here, and `propose` writes the proposal file straight into `proposals/` instead of opening a
pull request — so gate 1 is `harness approve <id>` rather than a merge.

## The four modes

`harness discover --mode <mode>` is one stage with four branches (`harness/stages/discover.py`).

| Mode | What it does | Model calls | What it reads |
|---|---|---|---|
| `assigned` | every open product issue assigned to the machine account becomes a work item | none | the assigned issues |
| `directed` | one named product issue becomes one work item | none | that one issue |
| `triage` | ranks the queue already here; only when that queue is empty does it look at the product repository | one | the queue, or the product repo's open issues, pull requests and branches |
| `audit` | refused | none | nothing |

### assigned

**This is the route work normally arrives by.** Assign `@jgoetzmann-bot` to an issue on
`Bright-Bots-Initiative/brightboost` and it is queued here — no label, no Actions tab, no command.
`feedback.yml` runs `harness discover --mode assigned` every three hours on a weekday, so it happens
on its own; running it by hand from the Actions tab only makes it happen sooner.

It makes no model call: which issues are assigned is a fact, not a judgement. The sweep is
idempotent — an issue that already has a work item is skipped and named in the run's decisions, so
running it every three hours queues nothing twice.

**It applies no filters at all.** `_assigned` queues every open issue the assignee query returns; the
allowlist, the excluded labels, the in-flight-branch check and the assignee check all live in
`_rejection_reason`, which only triage calls. So an issue labelled `intern-starter` and assigned to
the bot is *excluded* if triage considers it and *queued* if the sweep finds it first. The
assignment is the decision — there is no second gate behind it on this path.

The account it looks for is derived from
`FORK_REPO`'s owner rather than configured separately, because a fork the machine account does not
own is not one it can push to, so a second key could only ever disagree. With `FORK_REPO` empty the
mode fails loudly and tells you to set it or use `--mode directed` instead.

Assignment is a statement by a maintainer on the ticket itself, in the place they already work, and
it is visible to everyone looking at the issue rather than buried in a label list. That is why it
replaced the allowlist label as the way in (D53).

### directed

`--mode directed --target N` creates exactly one work item for product issue `N` and stops. It makes
no model call and does no ranking. On a re-run it finds the existing item by its `issue:N` reference
and returns that id rather than opening a second one — that path reads nothing from the product
repository and spends nothing. If `N` turns out to be a pull request it fails with `#N is a pull
request, not an issue`.

Directed skips every triage filter. The allowlist label, the excluded labels, the assignee check and
the in-flight-branch check all live in `_rejection_reason`, which only triage calls. Naming a target
is you asserting the judgement those filters exist to make.

The issue it opens here links the product issue, quotes its body verbatim inside a fenced block
labelled "The issue, verbatim", carries `Machine reference, do not edit: issue:N`, and ends with the
signature that carries the steering table and the trusted handles (`harness/links.py`,
`work_item_body`). The `issue:N` reference is what later fills the proposal's `upstream_issue` field;
the marker in the body is the fallback for work items whose reference is not an upstream issue at all.

### triage

`--mode triage` does one of two different things depending on what is already queued.

If any work item is in state `discovered` (`harness:queued`), triage ranks **those** and touches the
product repository not at all. It makes one model call with the queued items and, for the first
`QUEUE_BODY_LIMIT = 20` of them, an excerpt of each body; it creates nothing, and returns the ids
that went in, best first. If the ranking comes back with no usable number it falls back to store
order.

Only with an empty queue does triage read the product repository. It fetches open issues, open pull
requests and branches, then drops each issue that fails any of, in order:

1. it is assigned to **somebody other than the machine account** (B55) — an issue assigned to
   `@jgoetzmann-bot` survives here and needs no allowlist label, which is how D53's route reaches
   triage as well as its own mode;
2. its number is claimed by an in-flight branch name or pull request title (B56);
3. it carries one of `EXCLUDED_LABELS` — `intern-starter`, `large`, `architecture` (B57);
4. it does not carry `ALLOWLIST_LABEL`, which is `harness-ok` (B58), unless `--ignore-allowlist`.

If nothing survives, no model call is made and the stage returns an empty list. Survivors go to one
ranking call (`prompts/discover_triage.md`), and work items are created for the ranked numbers. If
that call returns no usable number either, the survivors are queued in GitHub's own order.

`--lens` is accepted by the CLI and passed down the stage's own call chain, and then goes nowhere:
`discover_triage.md` has one substitution, `$candidates`, and neither `_triage` nor
`_triage_product_repo` puts the lens in it. Setting it changes nothing today.

### audit

`--mode audit` raises `NotImplementedInDelivery1("not implemented in delivery 1")` before any GitHub
read and before any model call (B59); only the halt check precedes it. There is no audit
implementation to switch on. The mode exists so that asking for it fails in one obvious place.

### Why the allowlist label is not the way in

Triage against the product repository needs `harness-ok` on the issue. **No issue on
`Bright-Bots-Initiative/brightboost` carries that label, and none is expected to.** A triage run with
an empty queue therefore filters everything out, makes no model call, and returns nothing — the
filter working as designed, not a fault.

The label was the only way in for a while, and it went unused, which is the problem D53 solved:
asking a maintainer to add a label the harness invented is a worse ask than letting them use the verb
GitHub already gives them for "this one is yours". Assignment says the same thing the label says.
Prefer it.

`--ignore-allowlist` removes that one filter and keeps the other three, so it will surface
unassigned, unclaimed, non-excluded issues. It is not a substitute for a person agreeing — what it
discards is the only signal that anyone did. Note that the global `--dry-run` is not a preview of it:
it makes `harness/gh.py` record writes instead of sending them, and the ranking call still runs and
still spends.

## Aiming it at a ticket

### From the Actions tab

`discover.yml` → **Run workflow**, with:

- `mode` — choice, `triage` (default), `directed` or `assigned`
- `target` — the **product repository's** issue number; the step fails with
  `mode=directed needs a target` when it is blank
- `lens` — free text, triage only, currently inert (see above)
- `ignore_allowlist` — boolean, triage only

The job runs `harness --json discover` with those inputs and then `harness propose <id>` for every id
it created, so directed mode queues the ticket **and** opens its proposal pull request in the same
run.

Four things can stop it before it spends. `.harness/HALT` on the default branch is fetched and
checked before checkout and exits the job cleanly. `harness doctor` and `harness sync-fork` both run
before any model call and fail the job loudly. Then `harness dispatch` runs as a spend gate: the run
stands down when the reason is `halted`, `reserve`, `rate limited …`, `weekly usage …`, `session
usage …` or `carry leeway …`. `outside run window` is deliberately not in that set — discovery is one
cheap call and is not window-gated (D32). A budget stop mid-run exits 0, not red: the remaining items
stay `discovered` for the next run.

### From your machine

```bash
harness --json discover --mode directed --target 633   # {"created": [4]}
harness propose 4                                      # writes the package, publishes it
```

The global `--json` must come **before** the subcommand — `discover` has no local `--json` of its
own. Without it, `discover` prints one bare item id per line, which is not JSON and must not be fed
to `jq`.

The id that comes back is the **harness** work item, not the product issue number. In Actions mode it
is the issue number in this repository; in local mode it is a `harness.db` row id. `propose` takes
that number.

Local mode, end to end. `BACKEND=fake` replays fixtures and spends no model capacity; the GitHub
reads and the clone are still real:

```bash
harness discover --mode directed --target 633
harness propose 1
harness approve 1
harness run --item 1
harness package 1
```

`harness propose` on the command line takes no notes argument. Revision notes only reach the prompt
through `/harness revise` (below).

### What comes back

`propose` acquires a read-only clone of the product repository at a pinned base commit, makes one
model call inside it with `Read`, `Glob` and `Grep` only — `Bash`, `Edit`, `Write`, `WebFetch` and
`WebSearch` are all in the disallowed list — validates the result, and publishes. Publication is a
branch `harness/propose-<id>` carrying exactly one file `proposals/<id>-<slug>.md`, and a pull
request into `main` titled `proposal: <work item title> (#<id>)` whose body inlines the whole
proposal so the gate can be judged without opening the diff. The item moves to `proposed` /
`harness:proposed`.

Merging that pull request is gate 1. `implement.yml` triggers on a push to `main` touching
`proposals/**`, reads the numeric prefix off each added or modified filename and runs `harness
approve <n>`. The item is then `approved` and waits for the dispatcher — which, outside
`RUN_WINDOW_START=mon 08:00` to `RUN_WINDOW_END=tue 20:00` UTC, returns an empty plan whose reason
names the window. To start it anyway, dispatch `implement.yml` with the harness issue number in its
`issue` input: the job then runs `harness run --item N`, which bypasses the window and only the
window — the usage stops are the governor's and every stage still passes through them. The one other
thing that may start outside the window is an item carried across a weekly reset (D33).

## What a work package contains

The proposal file is a fixed front matter followed by a fixed set of headings.

### The front matter

Eleven keys, all required, nothing else accepted, in this order (`PROPOSAL_KEYS`,
`validate_proposal` in `harness/stages/propose.py`):

| Key | Rule |
|---|---|
| `issue` | positive integer; must equal this work item, and the item must not be `merged` or `abandoned` |
| `upstream_issue` | positive integer or `null` — the product repository's issue number |
| `title` | non-empty string, at most 120 characters; the prompt asks for a conventional-commit header of at most 100 |
| `kind` | one of `fix`, `chore`, `test`, `docs` |
| `slices` | integer 1–5 |
| `risk` | one of `low`, `medium`, `high` |
| `touched_paths` | 1–40 repository-relative paths, every one of which must already exist |
| `depends_on` | list of harness issue numbers that must merge first; usually `[]` |
| `estimated_turns` | integer from 1 to the `MAX_TURNS_IMPLEMENT` ceiling, which is 80 |
| `gate_expectation` | `green` or `known-red` |
| `baseline_red` | list of gate names; non-empty exactly when `gate_expectation` is `known-red` |

The model emits these as a JSON object inside an HTML comment on the first line of its output. If it
emits none, the front matter is derived from the parsed document alone — `slices` from the count of
entries under `## Slices`, `touched_paths` from the bullets under `## Touched paths`, `kind` from the
title's conventional-commit type (`fix` when there is no header at all, `chore` when the type is not
one of the four), risk `medium`, `depends_on []`, `gate_expectation green`, `estimated_turns 40` —
and that fact is recorded as a decision. Whatever the model supplies overrides the derived value,
except `issue`, which is always forced to this work item.

Because the block wins where it is present, the schema never cross-checks the front matter against
the document: `slices: 4` over three bulleted slices validates. The prompt requires the two to agree;
nothing enforces it. What does read the parsed document is the fullsend fitness gate, below.

### touched_paths, and why it is checked against the product repo

Every path is verified with a contents read against the product repository **at the base commit
propose's own clone was taken at**, so the tree the model read and the tree validation checks are the
same one (B104, B219/D37). A path that does not resolve fails the proposal.

Two consequences worth knowing before you write a ticket. A path invented from memory kills the
proposal rather than surfacing later as a bad diff. And a change consisting only of new files cannot
pass: the list must be non-empty and every entry must already exist. Whatever the change adds, the
package has to name the existing files it also touches.

The declared list is carried into the review package — `manifest.json`, `README.md` and the end of
`ACCEPTANCE.md` — and sits there beside the patch series so a reviewer can compare the two. That
comparison is the reviewer's; the harness does not make it mechanically.

### gate_expectation and baseline_red

The harness runs a pinned sequence of seven gates (`gates._SEQUENCE`), and `baseline_red` may name
nothing outside it:

`npx prisma generate`, `npm run lint`, `npm run typecheck`, `backend: npm run typecheck`,
`bash scripts/check-prisma-drift.sh`, `npm run test:unit`, `npm run build`.

`gate_expectation: green` claims the sequence will be green after the change. `known-red` says some
gate is already failing on the untouched tree and will still be failing afterwards — and then
`baseline_red` must list which. A `known-red` with an empty list fails validation.

This is a declaration, not a measurement: `implement` runs the whole sequence on the untouched tree
before it changes anything, writes that result separately as the baseline, and judges the change by
new failures against it. The value of the declaration is that it makes a disagreement visible. A
package that says `green` on a tree that was already red tells you the diagnosis was done against
something other than what the harness will build. Neither value ever justifies loosening a gate.

### The body

Ten headings, in this order, plus a `# <type>(<scope>): <subject>` title line above them. The parser
matches headings by name and takes whatever sits between them; a heading it does not find comes back
empty, and the order is the prompt's rule rather than the parser's (`SECTIONS`, `parse_work_package`).

- `## Issue` — the link, the number, the problem in the reporter's terms.
- `## Diagnosis` — what is actually wrong, with file and line citations. Evidence, not assertion.
- `## Approach` — what will change and why this way rather than the alternatives.
- `## Slices` — numbered units of work, each describable without reading the others.
- `## Behaviors` — numbered, testable, one line each; an observable outcome, not a step.
- `## Acceptance criteria` — one per line, each independently checkable by a reviewer.
- `## Decisions` — each decision with the alternative rejected and why. The prompt asks for at least one.
- `## Open questions` — anything that cannot be settled without a human. `None` when there are none.
- `## Touched paths` — bulleted, and expected to match `touched_paths`.
- `## Risks` — what to look hardest at. Also where prompt-injection attempts found in the issue
  body are reported.

List sections are parsed from bullets and numbered lines only; a section whose whole content is
`None` parses as empty, and so does one whose entries carry no bullet or number. That matters for
`## Slices` and `## Behaviors`, which the fullsend gate counts, and for `## Touched paths`, which
becomes the validated list whenever the model emits no proposal block — an unbulleted path list is
then empty and fails the non-empty rule.

### When it does not validate

Validation errors are appended to the prompt and the call is made once more — the propose stage
allows exactly one retry. If it still fails, the item transitions to `blocked` with the error list as
its reason and **nothing is published**. There is no partly-valid proposal; gate 1 never sees a
package that failed the schema.

## Steering a proposal you do not like

Put `/harness <verb>` on its own line in a comment. The verbs are exactly `revise`, `reject`, `fix`,
`rebase`, `stop`, `split`, `queue` (`keywords.VERBS`). A command is honoured only when **both** halves
of the actor gate hold: the handle is in `.harness/trust.txt` — currently `jgoetzmann` and
`BrightBoost-Tech` — **and** GitHub reports the commenter's association with that repository as
`OWNER`, `MEMBER` or `COLLABORATOR`. Anyone else's comment is read, counted as denied, and ignored.
Each comment is acted on once; editing a comment does not re-trigger it, so post a new one.

That second half matters here. `BrightBoost-Tech` has no access to this repository (D30), so on
proposal pull requests the effective list is `jgoetzmann` alone; the trust-file entry is what makes
his commands count on delivery pull requests in the product repository, where he is a maintainer.

On this repository — which is where proposal pull requests live — `feedback.yml` fires on
`issue_comment` and `pull_request_review_comment`, and only when the body contains `/harness`. It
acts within the same run. Latency is minutes, not the three hours that applies to a command left on
the product repository, which waits for the sweep cron `41 */3 * * 1-5` — and therefore until Monday
if it is left at the weekend.

| What you want | Do this | What happens in code |
|---|---|---|
| reject the plan | close the proposal PR | gate 1's own mechanism; nothing further runs |
| reject and mark the item dead | `/harness reject <reason>` on the PR | closes the PR and transitions the item to `abandoned` |
| a different plan for the same ticket | `/harness revise <notes>` | item → `proposing`, `propose` re-runs with your notes in the prompt |
| break up an oversized item | `/harness split` on the **harness issue** | `decompose` creates sub-issues, parent → `blocked` |
| put a parked item back in the queue | `/harness queue` on the harness issue | item → `discovered` |

**Rejecting.** Closing the pull request is the documented gate action and needs no command. Use
`/harness reject` when you also want the work item marked terminal rather than left sitting at
`proposed`. `/harness stop` runs the identical code path. Both close the pull request only when the
GitHub client can write; the transition to `abandoned` happens either way.

**Revising.** `/harness revise <notes>` transitions the item back to `proposing` and calls `propose`
with your text as "revision notes from a trusted reviewer" — a block the prompt reads alongside the
issue body. It is a fresh model call against a fresh clone, not an edit of the existing document. A
re-propose under a changed title lands a second file beside the first; the later one is what a merge
approves, and `work_package_text` resolves the pair by mtime, then by name. Notes are the entire
lever here: "wrong file" or "do not touch the test" changes what comes back, an empty `/harness
revise` mostly buys you the same package again.

**Splitting.** `/harness split` calls `decompose` on the thread's own number, so it belongs on the
**harness issue**, not on the proposal pull request. It makes one model call, creates up to
`MAX_SUBISSUES` (8) new work items with `sub:<parent>:<n>` references — each queued here with its
paragraph in the body and `Split out of #<parent>` recorded — and moves the parent to `blocked` with
the list of children. Depth is one: a sub-issue names its parent, and asking to split one is refused
before any model call.

`/harness fix` and `/harness rebase` are not proposal verbs. They run a revision cycle on an item
that already has a delivery pull request open (`shipped`), or one parked at `needs-human` — `fix` for
review feedback, `rebase` for a conflict. The automatic loop is bounded by `MAX_REVISE_CYCLES` (3),
past which the item goes to `harness:needs-human` and is left alone; a trusted `/harness fix`
restarts it, because a command from a trusted actor always carries notes and notes are what lifts the
stop.

## What makes a good ticket

### What the machinery throws out on its own

Assigned issues, issues claimed by an in-flight branch or pull request title, and anything labelled
`intern-starter`, `large` or `architecture`. Those never reach ranking — in triage. Directed
discovery applies none of them. `intern-starter` in particular is excluded on purpose: work reserved
for a human learning the codebase is not work to hand a machine.

### What the ranking prompt is looking for

The triage prompt ranks highest the candidates that are **decided** (the text says what should
happen, not merely that something is wrong), **locally verifiable** (lint, typecheck, unit tests and
build can demonstrate the fix, with no live database, deployed environment or credential), **narrow**
(few files, no schema change, no migration, no CI change, no rippling dependency bump), and
**self-contained** (not waiting on another issue or branch). It is told to rank lowest or drop
entirely anything needing a product decision or organisation access, anything touching `prisma/`,
`migrations/`, `backend/scripts/predeploy*` or `.github/workflows/`, and anything whose correct
behaviour cannot be settled from the repository alone.

### What the schema refuses

These are validation errors; the proposal is never published.

- More than 40 touched paths, or fewer than one.
- More than 5 slices.
- A path that does not already exist at the base commit — so a ticket satisfied only by adding new
  files cannot be proposed as written.
- More than 80 estimated implementation turns.
- A `baseline_red` naming anything outside the seven gates, or an empty one under `known-red`.

### What is refused elsewhere

The schema does not check these; other things do.

- A workflow file under `.github/workflows/`. The prompt forbids listing one; `_reject_forbidden_diff`
  blocks a diff that touches one, deletions included; and the machine account's PAT carries no
  `workflow` scope, so GitHub rejects the push outright (I-15).
- A gate widened, skipped, lengthened or made non-blocking. `_reject_forbidden_diff` (B64) blocks a
  diff that adds `continue-on-error`, `.skip(`, `.only(`, `xdescribe(`, `xit(`, `eslint-disable`,
  `@ts-ignore` or `@ts-expect-error`, or that raises or introduces a timeout.
- A new runtime dependency, unless the issue is about a dependency, and anything touching `prisma/`,
  `migrations/` or `backend/scripts/predeploy*` unless the issue is squarely about that path. Those
  two are constraints in `prompts/propose.md` and nothing enforces them mechanically — they are for
  you to check at gate 1.

### What the fullsend fitness gate says about size

`evaluate_fullsend_gate` (`harness/stages/implement.py`) decides whether an approved package takes
the parallel implementation path. All five must hold, and each individual failure is recorded:

| | Condition | Failure reason recorded |
|---|---|---|
| F1 | at least 3 slices | fewer than three independently describable slices |
| F2 | at least 15 behaviors | fewer than fifteen numbered behaviors |
| F3 | no open questions | the proposal still carries open questions: discovery rather than a decided spec |
| F4 | no path matching `prisma/`, `migrations/`, `backend/scripts/predeploy`, `.github/workflows/` | the change set touches prisma, migrations, predeploy scripts, or CI workflows |
| F5 | `FULLSEND_ENABLED` | fullsend_enabled is false in .env |

Read it for what it is. It does not gate whether work happens — a failure just means the ordinary
single-agent prompt — and F5 is `false` today, so every item takes that path regardless. F1 and F2
are not a minimum size for a good ticket; small is better. F3 and F4 are the ones that generalise:
**a proposal carrying open questions is discovery, not a plan**, and infrastructure paths are not
this harness's work. If the package that comes back lists open questions, that is your cue to answer
them and `/harness revise`, not to merge.

### So what to hand it

The same judgement whether you assign the bot, name a target, or label `harness-ok` — assignment is
just the cheapest way to say it.

Hand it a ticket when it is a defect or a cleanup whose cause lives in files that already exist,
whose correct behaviour can be settled from the repository alone, whose fix is a handful of files,
and which no open branch is already chasing. Deletions of dead code, a misreported number in a
script, a test that asserts the wrong thing, a docs correction — these are the shape that works.

Keep it away from anything that needs a product decision, needs a deployed environment or a
credential to verify, touches `prisma/`, `migrations/`, `backend/scripts/predeploy*` or
`.github/workflows/`, bumps a dependency, or consists entirely of new files. `intern-starter`,
`large` and `architecture` are excluded automatically **in triage only** — assigning the bot to one
queues it anyway, so that judgement is yours at the moment you assign. And keep it away from anything
you would not merge if a stranger opened the same pull request, because that is exactly what you will
be looking at.

## What it costs

There is one measured figure and it is a warning, not a budget. `harness propose` on the cheapest
shape of ticket there is — item 4 / product issue #633, deleting two orphaned files, published as
`proposals/4-chore-activities-delete-orphaned-sequenc.md` — spent **$2.37 and 22 minutes**, 63 cents
short of `PER_CALL_CAP_USD`. Most of that went on the model hunting for a repository that was not
there and then reading one that should not have been reachable; D36 and D37 fixed both, and
DECISIONS says in terms that the cost of a proposal should be re-measured on the next live run rather
than assumed. There is no measured implement cost: the first live attempt died before the model call
(D46), and the run before that committed nothing (D42).

Until a stage has three observations in the ledger, the dispatcher sizes it from a static table
(`dispatcher.STATIC_USD`): discover $0.20, propose $0.50, implement $2.50, revise $1.00, decompose
$0.30, package $0.05. Those are the numbers it compares against the remaining budget, and on the
evidence above the propose entry is low.

What actually bounds the spending is configuration, not estimation: `PER_CALL_CAP_USD` ($3.00),
`WEEKLY_CAP_USD` ($400.00) less `RESERVE_PCT` (10%), the turn ceilings (`MAX_TURNS_PROPOSE=30`,
`MAX_TURNS_IMPLEMENT=80`), and the two subscription-utilization stops `WEEKLY_USAGE_STOP_PCT` (90)
and `SESSION_USAGE_STOP_PCT` (70). All of those except the turn ceilings are among the sixteen keys
`.harness/config.json` may override; it wins over `.env` for exactly the keys it names and rejects
any other, and the turn ceilings are `.env` only.

`harness ledger` prints what has been spent, the per-stage observation count and median, the observed
subscription usage against each configured stop and the distance to it, and the reset time (D41) —
and says plainly when nothing has been observed rather than printing a zero. `harness dispatch`
prints the remaining budget as a percentage and the reason it is or is not starting anything.

See [docs/OPERATIONS.md](OPERATIONS.md) for what to do when a run goes wrong,
[docs/SAFETY.md](SAFETY.md) for the boundaries, and [DECISIONS.md](../DECISIONS.md) for why any of
this is the way it is.
