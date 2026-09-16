# bright-bots-harness

An automated harness that takes work on
[`Bright-Bots-Initiative/brightboost`](https://github.com/Bright-Bots-Initiative/brightboost)
from discovery to a reviewable pull request, and then stops. It runs on GitHub Actions as the
machine account `jgoetzmann-bot`, which owns the fork it pushes to; its work queue is GitHub
issues in this repository. A person decides twice: whether a piece of work starts, and whether
the result ships. Both decisions are pull request merges. Python 3.13, standard library only.

## The flow

| Step | What happens | Where you see it |
|---|---|---|
| `discover` | Takes a product-repository issue (assigned to the machine account, picked by triage, or named by number in directed mode) and opens a work item for it | An issue here, labelled `stage:queued` |
| `propose` | Reads the code at a pinned commit and writes a work package: diagnosis with file and line citations, approach, slices, behaviors, acceptance criteria, open questions, touched paths, risks | A PR here adding `proposals/<id>-<slug>.md` |
| Gate 1 | Merging that PR is approval: the push to `proposals/**` runs `harness approve`. Closing it unmerged rejects the work | The PR here |
| `implement` | Branch on the fork under `harness/`; the product's gate sequence runs on the untouched tree as a baseline and again after the change, then a self-audit call reviews the diff against the approved package | Commits on `jgoetzmann-bot/brightboost` |
| `package` | Assembles the review package: gate output with exit codes, patches, a git bundle, the base commit | `runs/item-N/package/`, uploaded as the run's artifact |
| `deliver` | Opens the pull request upstream, with the gate evidence in its body | A PR on the product repository from `jgoetzmann-bot:harness/…` |
| Gate 2 | A person reviews and merges upstream. The harness never merges anything | The PR on the product repository |

Between the gates, work is bounded by two subscription-usage stops, a run window, per-stage turn
ceilings, one item at a time, a revise cap and a pinned gate sequence.

## What it will and will not do

- **It never merges.** No merge, approve or dismiss endpoint exists in the code (I-12).
- **One GitHub credential.** A classic PAT with `public_repo`, `notifications` and `workflow`, on
  a machine account that owns the fork and is not a collaborator on the product repository.
  `workflow` lets the fork fast-forward past upstream's own CI changes (D67); the harness itself
  refuses to push a commit of its own that touches `.github/` (I-15). `harness doctor` reads the
  token's scopes before every spending run and warns if they drift. The only other secret is the
  subscription token the `claude` CLI authenticates with.
- **One door for that credential.** `harness/gh.py` is the only module that sends an
  `Authorization` header or reads the token, and the `gh` CLI is never invoked (I-11). Below
  `PERMISSION_TIER=2` no request carries a token; 0 and 2 are the only accepted tiers.
- **It stops on a committed file.** Every spending workflow checks for `.harness/HALT` on the
  default branch first, before checkout, `doctor` or the dispatcher, and exits 0 having spent
  nothing. `.harness/` is outside the harness's write roots, so it cannot remove its own kill
  switch.
- **Everything it writes is redacted.** Transcripts, logs, package and proposal files, ledger
  writes and every request to GitHub pass `redact.redact()` first (I-13).
- **Gates are never widened.** The product's sequence (`npx prisma generate`, `npm run lint`,
  `npm run typecheck`, the backend typecheck, `bash scripts/check-prisma-drift.sh`,
  `npm run test:unit`, `npm run build`) is the only definition of "it works". A red gate the
  harness cannot fix becomes a blocked item. `gates.py`, `packager.py`, `redact.py` and every
  file under `prompts/` are hashed into `.harness/PIN`; on a mismatch `harness doctor` fails, so
  `implement.yml` stops before spending and the container refuses to start.
- **Commands come from a list you control.** A `/harness` command is honoured only from a handle
  in `.harness/trust.txt` that GitHub also confirms, either by an `author_association` of OWNER,
  MEMBER or COLLABORATOR or by a `vouch:<id>` on that line matching the commenter's account id
  (D68). Anyone else's comment is ignored.

It will not push to the product repository, file an issue there, publish a change under
`.github/` anywhere, move the fork's default branch except to fast-forward it from upstream,
write a file outside its own roots, or ask you for any access beyond `public_repo`,
`notifications` and `workflow` on its own account.
[docs/SAFETY.md](docs/SAFETY.md) states each guarantee with the command that checks it.

## The repositories

| Repository | What it is | What the harness does there |
|---|---|---|
| `jgoetzmann/bright-bots-harness` | This one: the code, the docs and the work queue | Opens and labels issues, opens proposal PRs into `proposals/`, commits the ledger to the `harness-state` branch |
| `jgoetzmann-bot/brightboost` | The machine account's fork | Pushes the branches it creates, under `harness/`; fast-forwards the default branch from upstream. At tier 2 it is also the clone source |
| `Bright-Bots-Initiative/brightboost` | The product | Reads and clones it, and opens pull requests into it from the fork. No branch, no issue, no other write |

## Steering it

Write `/harness <verb>` (or `/harness-<verb>`) on its own line in a comment on any harness issue
or PR; several may go in one comment, one per line. The verbs are `work`, `ask`, `status`,
`audit`, `promote`, `revise`, `rebase`, `stop`, `go`, `split`, `halt` and `resume`.
[docs/COMMANDS.md](docs/COMMANDS.md) says what each does, where it applies, and which aliases it
accepts.

Who may give which is set by level in [`.harness/trust.txt`](.harness/trust.txt): level 3
(operator) adds `halt`, `resume` and `reject`, level 2 (maintainer) `audit`, `go`, `promote`,
`rebase`, `revise`, `split`, `stop` and `work`, level 1 (asker) `ask` and `status`, level 0
nothing. A listed commenter whose level is too low gets a reply naming the level the verb needs.
To add somebody, commit the line that `harness trust line` prints.

Append `--force` to start something now instead of at the next run window. It is for level 3
only and lifts only the calendar: halts, usage stops, the turn caps and both gates still apply.

A command is acted on once; editing the comment does not re-trigger it. Comments on this
repository wake `feedback.yml` within minutes. The product repository sends the harness no
events, so commands there are found by `harness sweep` on `feedback.yml`'s schedule
(`41 */3 * * 1-5`): up to three hours on a weekday, and not until Monday for a comment
left on Saturday. Running `feedback.yml` from the Actions tab skips the wait.

## Giving it work

Comment `/harness work <what you want>` on the pinned
[inbox issue](https://github.com/jgoetzmann/bright-bots-harness/issues/19). A pasted
product-repository issue link tracks that issue; a sentence becomes an item of its own. On a
product issue, `@jgoetzmann-bot /harness work` does the same. Maintainers can also label product
issues `harness-ok`, the pool triage draws from when nothing anybody asked for is outstanding.
[docs/FOR-MAINTAINERS.md](docs/FOR-MAINTAINERS.md) covers assigning the bot, the pool and what
makes a good request.

The dispatcher starts new items only inside `RUN_WINDOW_START` to `RUN_WINDOW_END` (daily 11:00
to 15:00 UTC in `.harness/config.json`), and `implement.yml`'s crons follow that window.
`harness run --item N` and `--force` start work outside it; neither bypasses the usage stops.
`discover.yml` is not window-gated.

## What is in here

| Path | What it holds |
|---|---|
| `harness/` | The package. `harness --help` lists the subcommands |
| `tests/` | The suite. Every behavior B1–B87, B99–B150, B200–B236, B238–B239, B241–B242, B244, B247, B250–B251, B253, B255–B271, B273–B274, B276–B280, B282–B283, B286–B288, B290, B292–B315, B320–B332, B340–B359, B385–B388, B390 and B394–B432 is cited by a test that names it |
| `prompts/` | What the model is asked, verbatim. Hashed into `.harness/PIN` with `gates.py`, `packager.py` and `redact.py` |
| `.github/workflows/` | `discover`, `implement`, `feedback`, `ack`, `watchdog`, `heartbeat`, `ops`, `selftest` |
| `proposals/` | Merged work packages. A merge into here is gate 1 |
| `.harness/` | `HALT`, `PIN`, `trust.txt`, `config.json`; outside the harness's write roots ([README](.harness/README.md)) |
| `local/` | The `bb` container for local mode |
| `docs/` | The documents below |

## Where to go next

`harness init` then `harness doctor` is the entry point. Under `BACKEND=fake` every stage replays
fixtures and spends nothing.

| Document | When to read it |
|---|---|
| [docs/FOR-MAINTAINERS.md](docs/FOR-MAINTAINERS.md) | Start here. No terminal needed: how to give it work, your two moves, how to stop it |
| [docs/COMMANDS.md](docs/COMMANDS.md) | Every comment verb and CLI subcommand, with examples, levels and latency |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Reading the state, a failed run, a stuck item, a diverged fork, a leaked secret, stopping everything, usage governance |
| [docs/SAFETY.md](docs/SAFETY.md) | The tiers and the invariants, each with the command that checks it. Read before raising the tier |
| [docs/PACKAGE-FORMAT.md](docs/PACKAGE-FORMAT.md) | What a work package, a review package and a delivery PR contain, and how to reconstruct a run |
| [docs/LOCAL-MODE.md](docs/LOCAL-MODE.md) | Running the harness in the `bb` container |
| [DECISIONS.md](DECISIONS.md) | Why something is the way it is |
