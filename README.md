# bright-bots-harness

A locally-run, manually-started Python service that takes work on the
[`Bright-Bots-Initiative/brightboost`](https://github.com/Bright-Bots-Initiative/brightboost)
repository from discovery through to a human-reviewable package, spending a metered
amount of Claude capacity and touching nothing outside its own directory.

It does not push. It does not open pull requests. It does not comment. It holds no
credentials at all. What it produces is a directory on your disk containing a diagnosis,
a patch series pinned to an exact base commit, verbatim evidence that the repository's
own gates pass, and a log of every decision taken to get there. Applying any of it is a
human action.

Delivery 1 is **Tier 0**: the harness performs no authenticated request and no write of
any kind against GitHub. That is enforced by structure, not by policy — see
[docs/SAFETY.md](docs/SAFETY.md).

## Requirements

- Python 3.13
- `git` on `PATH`
- `node`, `npm`, `npx` — used only inside the disposable clone, to run the product
  repository's own gates
- The `claude` CLI, only if you run the `cli` backend. The `fake` backend needs nothing.

No runtime Python dependencies. Standard library only, on purpose.

On Windows, `claude`, `npm` and `npx` are `.CMD` shims; the harness resolves them through
`PATHEXT` itself, so nothing needs to be on `PATH` as an `.exe`. This machine's global
`core.autocrlf=true` dirties fresh clones of the product repo; the harness's own repo pins
`core.autocrlf=false` locally, and the reconstruction commands in
[docs/PACKAGE-FORMAT.md](docs/PACKAGE-FORMAT.md) show the flag to pass.

## Install

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -e ".[dev]"
```

## First run

```bash
harness init      # creates harness.db, runs/, packages/, and .env from .env.example
harness doctor    # probes git, claude, node, npm, npx, disk, the halt file, and config
harness setup     # assesses bot-identity readiness and writes HUMAN.md
```

`harness init` never overwrites an existing `.env`. `harness doctor` exits 3 when
anything is missing or degraded and names what; it also probes whether this `claude`
build accepts `--max-turns`, which is undocumented in `claude --help` at 2.1.251 and so
must be checked rather than assumed. `harness setup` exits 6 while any human prerequisite
is outstanding and writes them into `HUMAN.md` as a gap report.

Edit `.env` before doing anything real. `BACKEND=fake` is the default and spends nothing.

## The five-command walkthrough

```bash
harness discover --mode directed --target 816
harness propose 1
harness approve 1
harness run --item 1
harness package 1
```

1. **discover** creates one work item for issue 816. Directed mode makes no model call —
   it reads the issue over the unauthenticated public API and stores it.
2. **propose** makes one model call and writes a work package: diagnosis with file and
   line citations, approach, slices, numbered behaviors, acceptance criteria, decisions,
   open questions, touched paths, risks. Read it. This is the cheapest place to disagree.
3. **approve** is you, deliberately, moving the item from `proposed` to `approved`.
   Nothing implements without it.
4. **run** clones the repository into `runs/item-1/clone`, runs the gate sequence on the
   untouched tree first as a baseline, implements, prettiers only the changed files,
   commits under the repository's conventional-commit rules, and runs the gates again.
   Green is done. Red gets a bounded number of diagnose-and-fix cycles, and a repeated
   failure signature stops it immediately. Still red means the item is marked `blocked`
   with everything recorded — never a widened gate.
5. **package** assembles `runs/item-1/package/`, the review package described in
   [docs/PACKAGE-FORMAT.md](docs/PACKAGE-FORMAT.md).

Then, when you want to keep it:

```bash
harness archive 1                    # promotes it into packages/, without the transcript
harness archive 1 --with-transcript  # includes the full redacted model transcript
```

Other commands: `harness status [--json]` for the queue and remaining budget,
`harness halt` and `harness resume` for the kill switch.

## Gates and the install step

`harness run` clones the product repo fresh, so before the baseline it runs `npm ci` at the
root and in `backend/` wherever a `package-lock.json` exists. That is an install, not a gate:
it checks nothing, it is recorded in `EVIDENCE.md` under its own heading, and the seven gates
of the spec run unchanged after it. Without it every gate is vacuously red on an empty tree.

A gate red in the baseline is pre-existing. It is carried into the post-change evidence as
what it is and never attributed to the change — but the harness also never calls such a
sequence "green". `DECISIONS.md` in the run directory says which it was.

## The fake backend

`BACKEND=fake` replays canned runner results from `tests/fixtures/runner/` instead of
calling a model. The whole state machine — discovery, proposal, the governor, the gate
loop, the packager — runs end to end at zero token cost and with no network. Use it to
try the five commands, to reproduce a bug, and in CI. Nothing in the fake path imports
`subprocess` or `urllib`.

## Budget

Every stage asks the governor for an authorization before it spends anything, and the
governor refuses when the estimate exceeds what is spendable. Budgets are expressed as
percentages of your weekly Claude allowance: `WEEKLY_BUDGET_PCT` for the week,
`SESSION_BUDGET_PCT` for one `harness run`, and `RESERVE_PCT` held back and never spent.
Estimates start from a static table and switch to the observed median once a stage has
three completed runs behind it.

## The kill switch

Create the file named by `HALT_FILE` (default `HALT` at the repository root) and the
harness stops at the next stage boundary: the clone is released, the item stays
resumable, and `harness run` exits 5. `harness halt` creates it, `harness resume` removes
it. See [HALT.example](HALT.example).

## Safety, in brief

- **Tier 0.** No authenticated request, no write to GitHub, in Delivery 1. The code to
  push, open a PR, or comment does not exist yet.
- **No credentials.** The harness holds none. A `HARNESS_GITHUB_TOKEN` in `.env` is
  refused in code while `PERMISSION_TIER=0`, even when the token is perfectly valid.
- **No `gh` CLI.** `gh` authenticates transparently with your token, which would void
  Tier 0 without anyone noticing. Every GitHub read goes through `urllib.request` with no
  `Authorization` header.
- **No writes outside its own directories.** Every write path is guarded and rejects any
  destination that is not under `runs/`, `packages/`, the configured database, `HUMAN.md`,
  or `.env`.
- **Everything redacted before it hits disk.** Transcripts, logs, and every file in a
  package pass through the secret scrubber first.
- **Gates are never widened.** No skipped check, no lengthened timeout, no
  `continue-on-error`, no touched CI workflow. A red the harness cannot fix honestly is a
  blocked item, not a passed one.

The full list, with how each is enforced and how to verify it yourself, is in
[docs/SAFETY.md](docs/SAFETY.md).

## Layout

| Path | What it is |
|---|---|
| `harness/` | The package. Config, store, governor, runner, GitHub client, stages, packager |
| `prompts/` | Prompts as data, one file per stage, loaded by filename |
| `tests/` | pytest suite; every behavior in the spec is cited by at least one test |
| `docs/SAFETY.md` | The invariants, written for a maintainer with no prior context |
| `docs/PACKAGE-FORMAT.md` | The work package and the review package, and how to rebuild a tree from one |
| `HUMAN.md` | Generated by `harness setup`: the things only a human can do |
| `runs/` | Disposable clones, transcripts, working state. Never committed |
| `packages/` | Promoted review packages. Committed |
| `HARNESS-SPEC.md` | The frozen implementation spec |
| `HARNESS-REVIEW.md` | The runnable acceptance protocol |

## What this does not do

No push, no pull request, no comment, no issue filing. No concurrency above one clone.
No audit discovery mode. No sub-issue decomposition. No API runner backend. No web UI, no
scheduler, no daemon. No multi-repository support. Any use of the `brightboost-harness`
bot identity — it is fully specified and its readiness is detected and reported, but it
cannot be used before the tier that needs it is agreed.
