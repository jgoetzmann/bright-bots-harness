# bright-bots-harness

An automated harness that takes work on
[`Bright-Bots-Initiative/brightboost`](https://github.com/Bright-Bots-Initiative/brightboost)
from discovery to a reviewable pull request, and then stops. It runs on GitHub Actions as the
machine account `jgoetzmann-bot`, which owns the fork it pushes to; its own work queue is
GitHub issues in this repository. A human decides twice — whether a piece of work should
start, and whether the result should ship — and both decisions are ordinary pull requests you
can act on from a phone. Python 3.13, standard library only.

## The flow

| Step | What happens | Where you see it |
|---|---|---|
| `discover` | Takes a product-repository issue — assigned to the machine account, picked by triage, or named by number in directed mode — and opens a work item for it | An issue here, labelled `harness:queued` |
| `propose` | Reads the code at a pinned commit and writes a work package: issue, diagnosis with file and line citations, approach, slices, behaviors, acceptance criteria, decisions, open questions, touched paths, risks | A PR here adding `proposals/<id>-<slug>.md`, from `harness/propose-<id>` |
| **Gate 1 — you** | **Merging that PR is approval**: the push to `proposals/**` runs `harness approve`. Closing it without merging is rejection — nothing is implemented, because nothing reaches `approved` | The PR here |
| `implement` | Branch on the fork, under `harness/`; the product's own gate sequence runs on the untouched tree as a baseline, then again after the change | Commits on `jgoetzmann-bot/brightboost` |
| `package` | Assembles the review package — verbatim gate output with exit codes, patches, a git bundle, the base commit | `runs/item-N/package/`, uploaded as the run's artifact (14 days) |
| `deliver` | Opens the pull request upstream, with the gate evidence in its body | A PR on the product repository from `jgoetzmann-bot:harness/…` |
| **Gate 2 — a human** | Review and merge upstream. **The harness never merges anything** | The PR on the product repository |

Everything between the gates is bounded: a per-call cap, a weekly cap with a reserve, two
subscription-utilisation stops, a weekly run window, per-stage turn ceilings, a revise cap,
and a pinned gate sequence.

## What it will and will not do

- **It never merges.** No merge, approve, or dismiss endpoint exists in the code (I-12). Both
  gates are load-bearing because the code that would bypass them is absent.
- **One GitHub credential, scoped small.** A classic PAT with `public_repo` and nothing else,
  on a machine account that owns the fork and is not a collaborator on the product repository.
  No `workflow` scope, so GitHub itself rejects any push touching `.github/workflows/` (I-15).
  The only other secret is the subscription token the `claude` CLI authenticates with.
- **One door for that credential.** `harness/gh.py` is the only module that sends an
  `Authorization` header and the only one that can read the token (I-9, I-11); the `gh` CLI is
  banned outright (I-2′). Below `PERMISSION_TIER=2` no request carries a token at all — and 0
  and 2 are the only tiers this build accepts.
- **It stops on a committed file.** `.harness/HALT` on the default branch is the first step of
  every spending workflow — before checkout, before `doctor`, before the dispatcher — and the
  job exits 0 having spent nothing (B149/B150). `.harness/` is outside the harness's write
  roots, so it cannot remove its own kill switch (I-8).
- **Everything it says is redacted.** Every transcript, log line, package file, proposal file,
  ledger write and byte sent to GitHub passes `redact.redact()` first (I-13).
- **Gates are never widened.** The product's own sequence — `npx prisma generate`,
  `npm run lint`, `npm run typecheck`, the backend typecheck,
  `bash scripts/check-prisma-drift.sh`, `npm run test:unit`, `npm run build` — is the only
  definition of "it works" the harness accepts. A red it cannot fix honestly is a blocked item,
  not a loosened gate. `gates.py`, `packager.py`, `redact.py` and every file under `prompts/`
  are hashed into `.harness/PIN`; `harness doctor` exits non-zero on a mismatch, so
  `implement.yml` fails before spending and the container refuses to start.
- **Commands come from a list you control.** A `/harness` command is honoured only from a
  handle in `.harness/trust.txt` whose `author_association` is OWNER, MEMBER or COLLABORATOR —
  both, never either alone. Anyone else's comment is silently ignored and its body is never
  even parsed.

It will not push to the product repository, file an issue there, edit `.github/**` anywhere,
move the fork's default branch except to fast-forward it from upstream, write a file outside
its own roots, or ask you for any access beyond `public_repo` on its own account.
[docs/SAFETY.md](docs/SAFETY.md) states each guarantee with the command that checks it.

## The repositories

| Repository | What it is | What the harness does there |
|---|---|---|
| `jgoetzmann/bright-bots-harness` | This one: the code, the docs, and the work queue | Opens and labels issues, opens proposal PRs into `proposals/`, commits the ledger to the `harness-state` branch |
| `jgoetzmann-bot/brightboost` | The machine account's fork | Pushes every branch it creates, under `harness/`; fast-forwards the default branch from upstream and nothing else. At tier 2 it is also the clone source, synced first |
| `Bright-Bots-Initiative/brightboost` | The product | Reads and clones it, and opens one pull request into it from the fork. No branch, no issue, no other write |

## Steering it

From a comment on any harness issue or PR, `/harness <verb>` on its own line. These seven are
the whole list:

| Verb | Where | What it does |
|---|---|---|
| `revise` | Proposal PR | Returns the item to `proposing` and re-runs `propose` with your notes as guidance |
| `reject` | Proposal PR | Closes the PR and abandons the item |
| `fix` | Delivery PR | One revision cycle with source `review`, your notes as the feedback |
| `rebase` | Delivery PR | One revision cycle with source `conflict` |
| `stop` | Delivery PR | Closes the PR and abandons the item |
| `split` | Issue here | Decomposes it into at most `MAX_SUBISSUES` sub-issues, in this repository only |
| `queue` | Issue here | Returns the item to `discovered`; a no-op if it is already there |

A command is acted on once; editing the comment does not re-trigger it. On this repository the
comment events wake `feedback.yml` directly and latency is minutes. On the product repository
the harness gets no events, so commands are found by `harness sweep` on `feedback.yml`'s
schedule (`41 */3 * * 1-5`): up to `NOTIFY_POLL_HOURS` on a weekday, and until Monday for a
comment left on Saturday. To skip the wait, run `feedback.yml` from the Actions tab.

## Giving it work

**Assign `@jgoetzmann-bot` to an issue on the product repository.** That is the whole gesture
(D53): `feedback.yml` sweeps for assigned issues every three hours on a weekday, opens a work
item for each one, and leaves alone any it has already queued. No label to invent, no Actions
tab, no model call — which issues are assigned is a fact, not a judgement.

The sweep only queues. The proposal comes from the next `discover` run, which ranks the queue
and proposes every item in it — so an assigned issue becomes a proposal on the Sunday cron
unless you dispatch `discover` by hand.

Note the asymmetry: `--mode assigned` queues **every** open issue assigned to the account and
applies no label filter. The `intern-starter` / `large` / `architecture` exclusion lives in
`_rejection_reason`, which only triage calls — so in triage an assigned issue survives the
allowlist but is still excluded by those three labels, while the sweep would queue it. Do not
assign the bot to an issue you would not hand it.

The other two routes are for when you want a specific thing now, from the Actions tab.
`discover.yml` takes `mode` (`assigned`, `directed` or `triage`), `target` (a
product-repository issue number, required by `directed`), `lens` and `ignore_allowlist`.
Directed mode queues one named ticket and proposes it in the same run, skipping every triage
filter — naming a target is you asserting the judgement those filters exist to make. Triage
ranks whatever is already queued here, and only reaches for the product repository when that
queue is empty; there it still requires the `harness-ok` label (`ALLOWLIST_LABEL`), which
nothing carries, so triage on an empty queue finds nothing. That is the filter working, not a
fault — assignment is the route that replaced it.

`implement.yml` takes an `issue` number to run now, bypassing the run window. Every spending
workflow refuses to start while `.harness/HALT` exists on the default branch.

## What is in here

| Path | What it holds |
|---|---|
| `harness/` | The package. Standard library only; `harness --help` lists the subcommands |
| `tests/` | The suite. Every behavior B1–B86, B100–B150, B200–B234, B269–B271, B273 and B279 is cited by a test that names it |
| `prompts/` | What the model is asked, verbatim. Hashed into `.harness/PIN` with `gates.py`, `packager.py` and `redact.py` |
| `.github/workflows/` | `discover`, `implement`, `feedback`, `heartbeat`, `ops`, `selftest` |
| `proposals/` | Merged work packages. A merge into here is gate 1 |
| `.harness/` | `HALT` (the kill switch), `PIN`, `trust.txt`, `config.json`. Outside the harness's write roots |
| `docs/` | Everything below, plus `delivery/` — the frozen specifications each build was graded against |

## Where to go next

Everything below runs locally under `BACKEND=fake`, which replays fixtures and spends nothing;
`harness init` then `harness doctor` is the entry point, and `harness --help` lists the
subcommands.

| Document | When to read it |
|---|---|
| [docs/USING.md](docs/USING.md) | **Start here to operate it.** What arrives, where, and what you do about it — all of it from a browser or a phone |
| [docs/PROPOSALS.md](docs/PROPOSALS.md) | How work is found and aimed: the discovery modes, what a proposal contains, and how to change its mind |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Day to day and when something is wrong: reading the state (§1), a failed run, a stuck item, a diverged fork, a leaked secret, everyday actions (§11). §8 is how to stop everything |
| [docs/SAFETY.md](docs/SAFETY.md) | The tiers and the invariants, each with a command that proves it. Read before raising the tier |
| [docs/PACKAGE-FORMAT.md](docs/PACKAGE-FORMAT.md) | What a work package and a review package contain, and how to reconstruct a run from one |
| [docs/LOCAL-MODE.md](docs/LOCAL-MODE.md) | Running the same harness in the `bb` container, off the schedule |
| [DECISIONS.md](DECISIONS.md) | Why something is the way it is. D1–D54, the amendment log for the frozen specs |
| [docs/delivery/](docs/delivery/README.md) | The frozen specs and their runnable review protocols. For reviewing, not operating |

## Current status

**Live, and it has been all the way through once.** Actions mode at `PERMISSION_TIER=2`,
queue in GitHub issues, ledger on the `harness-state` branch. Item 4 (product issue #633) went
from directed discovery through a proposal pull request, gate 1, implement, package and
delivery, and opened **[`Bright-Bots-Initiative/brightboost#868`](https://github.com/Bright-Bots-Initiative/brightboost/pull/868)**
— the first pull request the harness has ever put on the product repository. All seven gates
were green on the runner. `proposals/4-chore-activities-delete-orphaned-sequenc.md` is the
file gate 1 merged.

Gate 2 is now the only thing standing between that branch and `main`, which is the whole
point. Nothing about it is automatic and nothing about it will be.

The dispatcher plans no new item outside `RUN_WINDOW_START` (mon 08:00) to `RUN_WINDOW_END`
(tue 20:00) UTC, and `implement.yml`'s crons follow that window. Two things start outside it:
`harness run --item N`, which is what a `workflow_dispatch` with an explicit issue number
invokes, and an item carried across a weekly reset, which runs on `OVERRUN_PCT` leeway
instead. Neither bypasses the usage stops. `discover.yml` is deliberately not window-gated —
one triage call is cheap — and stops only on a halt, a rate limit, the reserve, or a usage
stop.

**It took four attempts, and each one found a real defect.** None of them were in the change
being delivered; every one was in the harness, and each is a decision with its evidence in
`DECISIONS.md`:

| Attempt | Died at | Cause | Fix |
|---|---|---|---|
| 1 | `harness run --item 4` | `propose` recorded an absolute `runs/` path, and `runs/` does not survive between Actions runners — so the designed flow could never have completed on Actions | D46: read the work package from the committed `proposals/<id>-*.md`, which is the very thing gate 1 merges |
| 2 | `git push` to the fork | `npm ci` runs brightboost's `prepare`, which is husky, which reinstalls `.husky/pre-push`; it calls `scripts/check-bundle-size.js`, which crashes under Node 22 (`require` in a `"type": "module"` package) | D49: `core.hooksPath` set at `acquire` **and** repeated on the push command line, where `prepare` cannot put it back |
| 3 | the call after the PR opened | The self-imposed request ceiling still defaulted to 50 — right for Delivery 1's absent credential, wrong at tier 2, where the token raises GitHub's own limit to 5000. The PR opened and the next call, a label write, was refused by our own ceiling, leaving the item's label disagreeing with reality | D51: the ceiling follows the tier |
| 4 | nothing — it landed | | |

Two more defects were only visible once a human looked at #868. The work item had been
answering `self:4` instead of `issue:633`, so the delivery PR went out **without
`Closes #633`** and its package README described the work as `self:4` (D50). And the body was
52 KB, 40 KB of which was the verbatim stdout of seven gates that all passed — a wall nobody
scrolls, in the one place a reviewer has to read, and close to GitHub's 65 KB limit. It now
opens at the closing keyword, how to steer it by comment, and brightboost's own
`CONTRIBUTING.md` checklist with the three boxes the harness actually measured already ticked;
the evidence is a table of gate, phase and exit code, with any *failing* gate still printed
whole (D52). 40,542 characters of evidence render as 1,157.

**Known gaps, recorded and not fixed.** Five more places assume `runs/` survives between
Actions runs: `revise`'s baseline-red lookup, `item.package_path`, `HANDOFF.md`, `sweep`'s
shared run directory, and `heartbeat.yml`'s ledger fetch. Each is described in `DECISIONS.md`
under the Delivery 3 acceptance section, with the reason it needs its own durable source
chosen deliberately rather than a fix in the same change. `HUMAN.md` **overstates what is
left, and regenerating it does not help** — `harness setup --tier 2` reproduces the committed
file byte for byte. Several of its prerequisites are hardcoded unsatisfied because nothing the
harness can reach proves them: whether Actions is enabled on the fork, whether a repository
secret exists, whether Nathan has been told. Run from a checkout it also reads the local
`.env`, where `PERMISSION_TIER` is 0, so it reports the tier-0 → tier-2 gap rather than the
tier 2 the Actions runtime actually runs at. Items 6, 7 and 17 on that list are demonstrably
done — a live run opened #868 with both secrets in scope. Read `HUMAN.md` as the original
setup checklist, not as current state. The 1,423 passing tests recorded at that acceptance were a
Windows-local fact: `selftest.yml` had always specified both `ubuntu-latest` and
`windows-latest`, but it triggers only on `pull_request` and `workflow_dispatch` and neither
had happened. The first time it actually ran, it failed on both — D38 fixed the pin's line-ending
sensitivity, D39 the tests that assumed the ambient `init.defaultBranch`.

**Cost, measured once.** `harness propose 1` spent $2.37 against a `PER_CALL_CAP_USD` of $3.00
and took 22 minutes, most of it the model searching for a repository that was not there and
then reading one that should not have been reachable. D36 and D37 changed that path; the
figure should be re-measured on the next live run, not assumed.
