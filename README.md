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
| `discover` | Takes a product-repository issue — picked by triage, or named by number in directed mode — and opens a work item for it | An issue here, labelled `harness:queued` |
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

From the Actions tab, `discover.yml` takes `mode` (`triage` or `directed`), `target` (a
product-repository issue number), `lens` and `ignore_allowlist`. Directed mode queues one
specific ticket and proposes it in the same run — which is how work actually starts today,
because triage only considers product issues carrying the `harness-ok` label
(`ALLOWLIST_LABEL`) and none carry it yet. `implement.yml` takes an `issue` number to run now,
bypassing the run window. Both refuse to start while `.harness/HALT` exists on the default
branch.

## What is in here

| Path | What it holds |
|---|---|
| `harness/` | The package. Standard library only; `harness --help` lists the subcommands |
| `tests/` | The suite. Every behavior B1–B86, B100–B150 and B200–B229 is cited by a test that names it |
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
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Day to day and when something is wrong: reading the state (§1), a failed run, a stuck item, a diverged fork, a leaked secret, everyday actions (§11). §8 is how to stop everything |
| [docs/SAFETY.md](docs/SAFETY.md) | The tiers and the invariants, each with a command that proves it. Read before raising the tier |
| [docs/PACKAGE-FORMAT.md](docs/PACKAGE-FORMAT.md) | What a work package and a review package contain, and how to reconstruct a run from one |
| [docs/LOCAL-MODE.md](docs/LOCAL-MODE.md) | Running the same harness in the `bb` container, off the schedule |
| [DECISIONS.md](DECISIONS.md) | Why something is the way it is. D1–D46, the amendment log for the frozen specs |
| [docs/delivery/](docs/delivery/README.md) | The frozen specs and their runnable review protocols. For reviewing, not operating |

## Current status

**Live.** Actions mode at `PERMISSION_TIER=2`, queue in GitHub issues, ledger on the
`harness-state` branch. Directed discovery and `propose` have run for real. Gate 1 has been
exercised once: the proposal for harness issue #4 (product issue #633) was merged, and
`proposals/4-chore-activities-delete-orphaned-sequenc.md` is the file it added. `implement`
has run on an Actions runner far enough to take a clone and pass all seven baseline gates
before failing on a defect (D46) that is now fixed. The kill switch is currently off —
`.harness/HALT` is parked as `HALT.suspended` — so the schedules will fire.

The dispatcher plans no new item outside `RUN_WINDOW_START` (mon 08:00) to `RUN_WINDOW_END`
(tue 20:00) UTC, and `implement.yml`'s crons follow that window. Two things start outside it:
`harness run --item N`, which is what a `workflow_dispatch` with an explicit issue number
invokes, and an item carried across a weekly reset, which runs on `OVERRUN_PCT` leeway
instead. Neither bypasses the usage stops. `discover.yml` is deliberately not window-gated —
one triage call is cheap — and stops only on a halt, a rate limit, the reserve, or a usage
stop.

**Exercised once, end to end, and it did not finish.** On 4 September 2026 item 4 (product
issue #633) went from directed discovery through a proposal pull request, gate 1, implement and
package on an Actions runner — all seven gates green on Linux — and then failed at the push to
the fork. The cause was not the change: `npm ci` installs the product repository's husky hooks,
and its `pre-push` runs `scripts/check-bundle-size.js`, which crashes under Node 22 because it
uses `require` in a `"type": "module"` package. D49 turns hooks off in a harness clone. **No
pull request has yet been opened on `Bright-Bots-Initiative/brightboost`, so gate 2 has never
happened.**

**Known gaps, recorded and not fixed.** Five more places assume `runs/` survives between
Actions runs: `revise`'s baseline-red lookup, `item.package_path`, `HANDOFF.md`, `sweep`'s
shared run directory, and `heartbeat.yml`'s ledger fetch. Each is described in `DECISIONS.md`
under the Delivery 3 acceptance section, with the reason it needs its own durable source
chosen deliberately rather than a fix in the same change. `HUMAN.md` is a generated gap report
and is stale relative to what has since been done — regenerate it with `harness setup` before
trusting its outstanding list. The 1,423 passing tests recorded at that acceptance were a
Windows-local fact: `selftest.yml` had always specified both `ubuntu-latest` and
`windows-latest`, but it triggers only on `pull_request` and `workflow_dispatch` and neither
had happened. The first time it actually ran, it failed on both — D38 fixed the pin's line-ending
sensitivity, D39 the tests that assumed the ambient `init.defaultBranch`.

**Cost, measured once.** `harness propose 1` spent $2.37 against a `PER_CALL_CAP_USD` of $3.00
and took 22 minutes, most of it the model searching for a repository that was not there and
then reading one that should not have been reachable. D36 and D37 changed that path; the
figure should be re-measured on the next live run, not assumed.
