# Package format

Two artifacts, one after the other. `harness propose` produces the **work package**: a
plan, in Markdown, that a human reads and approves before anything is implemented.
`harness package` produces the **review package**: a directory a reviewer can act on
without having ever seen the harness.

In Delivery 2 both artifacts also travel as pull requests. The work package is the file in
a proposal PR against this repository (gate 1); the review package is the body of the
delivery PR against the product repository (gate 2, §6 below). The formats on disk are
unchanged; the PRs are made from them, not the other way round.

This document is extracted from the implementation spec so a reviewer need not read it.

---

## 1. The work package — output of `propose`

Markdown with exactly these sections, in this order. The parser depends on the headings,
so a stage that renames one breaks parsing rather than silently dropping content.

```markdown
# <type>(<scope>): <one-line statement of the defect or change>

## Issue
Link, number, and the problem in the reporter's terms.

## Diagnosis
What is actually wrong, with file and line citations. Evidence, not assertion.

## Approach
What will change and why this way.

## Slices
Numbered, independently describable units. Three or more enables the fullsend path.

## Behaviors
Numbered, testable, one line each. Fifteen or more enables the fullsend path.

## Acceptance criteria
What must be true for a reviewer to accept. Each line independently checkable.

## Decisions
Every decision made, with the alternatives rejected and why.

## Open questions
Anything that cannot be decided without a human. Non-empty blocks the fullsend path
and, if any question is load-bearing, blocks the item.

## Touched paths
Every path expected to change. Compared against the actual diff at package time.

## Risks
What could go wrong, and what the reviewer should look hardest at.
```

The work package is written to `runs/<run-id>/spec/<item>.md` and recorded on the work
item as `spec_path`. Three of its sections feed the fullsend fitness gate — `Slices`,
`Behaviors`, `Open questions` — and two of them, `Diagnosis` and `Acceptance criteria`,
are carried verbatim into the review package. `Touched paths` is compared against the
actual diff at package time, which is how a change that quietly grew beyond its plan is
caught.

### 1.1 The proposal file (Delivery 2)

In Delivery 2 the same work package is also written as `proposals/<issue>-<slug>.md` on a
branch `harness/propose-<issue>` and opened as a PR against this repository. **Merging that
PR is approval** — not reviewing it, not commenting on it. The file is the Delivery 1 body
above, preceded by YAML front matter with a bounded schema: every field required, every enum
closed, unknown keys rejected.

```yaml
---
issue: 816                      # int, must match an open issue in this repo
upstream_issue: 816             # int or null — the product repo issue, if any
title: "…"                      # str, 1..120
kind: fix | chore | test | docs # closed enum
slices: 1                       # int, 1..5
risk: low | medium | high       # closed enum
touched_paths:                  # list[str], 1..40, each must exist in the product repo
  - src/…
depends_on: []                  # list[int] — issue numbers that must reach harness:merged first
estimated_turns: 40             # int, 1..MAX_TURNS_IMPLEMENT
gate_expectation: green | known-red   # closed enum; known-red requires baseline_red below
baseline_red: []                # list[str] — gate names already red before the change
---
```

A proposal whose front matter fails validation is never opened as a PR: the stage retries
once with the errors appended to the prompt, then marks the item `harness:blocked` with the
errors in a comment (B103). Every `touched_paths` entry is checked against the product
repository at the pinned base commit; a path that does not exist fails validation (B104).
`depends_on` is read by the dispatcher, which will not start the item until every listed
issue is `harness:merged` (B107). The front matter is the only place the model's output
becomes an instruction, which is why it is a closed schema and not free text.

Under the sqlite store (local mode) the same file is written under `proposals/` next to the
database and no PR is opened; `harness approve <id>` is the gate instead.

---

## 2. The review package — output of `package`

```
runs/<run-id>/package/
├── README.md            entry point: what this is, what changed, how to verify
├── DIAGNOSIS.md         from the work package
├── DECISIONS.md         every decision, including fullsend-gate outcomes
├── EVIDENCE.md          verbatim gate output, baseline and post-change, with exit codes
├── ACCEPTANCE.md        the criteria, each marked met or not, with evidence
├── BASE                 the 40-char base sha, one line
├── manifest.json        machine-readable summary (schema below)
├── patches/
│   ├── 0001-<slug>.patch
│   └── ...
├── bundle.gitbundle     the branch, fetchable without any remote
└── transcript.jsonl     full redacted model transcript (excluded by archive unless asked)
```

**Exactly these entries and nothing else.** The packager prunes anything not on this list
before it finishes, so a stray scratch file cannot ride along into a review.

### The files, one by one

**`README.md`** — the entry point. Names the work item and external reference, the base
commit, the branch, the patch count and filenames, the paths the work package expected to
touch, and the two ways to reconstruct the tree. It states plainly that applying any of this
is a human action. That stays true in Delivery 2: `deliver` pushes the branch to a fork the
machine account owns and opens a PR from it, but nothing reaches the product repository's
default branch except by a human merge.

**`DIAGNOSIS.md`** — the `Diagnosis`, `Issue`, `Approach` and `Risks` sections of the work
package, carried through. This is the "what is actually wrong" half, with citations.

**`DECISIONS.md`** — three parts. The decisions declared in the work package; the fullsend
fitness gate as a table of the five conditions with whether each held and which path was
taken as a result; and every decision recorded during the run itself, appended as it
happened with a timestamp. A decision taken silently is a bug.

**`EVIDENCE.md`** — the load-bearing file. Verbatim output of the product repository's own
gate sequence, in two sections:

- *Baseline* — the untouched tree at `BASE`, run **before** any change. Anything red here
  is pre-existing, is not attributable to the change, and is never used to justify
  loosening anything.
- *Post-change* — the branch exactly as packaged.

Each gate gets its name, its argv, its exit code, a PASS/FAIL verdict, and the captured
tails of stdout and stderr in a fenced block. Nothing is summarised, trimmed for
readability, or re-worded — a summary of a gate is an opinion about a gate, and an opinion
is not evidence.

When the database gates (`npx prisma generate`, `bash scripts/check-prisma-drift.sh`) were
not run, the file opens with an explicit omission note saying so and naming them. The
harness does not claim parity with CI that it did not achieve.

**`ACCEPTANCE.md`** — each acceptance criterion from the work package, numbered, marked
`met` or `NOT met`, with the evidence the verdict rests on. A criterion is marked met only
when the post-change gate sequence is fully green; anything else is reported as not met
with the failing gates named. The harness does not mark its own work met on assertion.
The declared touched paths are listed at the end for comparison against the diff.

**`BASE`** — the 40-character base commit sha and a trailing newline. Nothing else, so
`git checkout "$(cat BASE)"` works without cleanup. In Delivery 2 this commit always exists
**upstream**, not only on the fork, because the fork's default branch is fast-forward-only
(B105) and every work branch is cut from it at a commit upstream already has (B106).

**`patches/`** — the series produced by `git format-patch <BASE>..HEAD`. Applies cleanly
onto `BASE` with `git am`, and onto no other commit. Zero patches is a legitimate outcome
and shows as `patch_count: 0`.

**`bundle.gitbundle`** — the branch as a git bundle, produced by
`git bundle create bundle.gitbundle <branch>`. Verifiable with `git bundle verify` and
clonable directly, so a reviewer with no network at all can still get the tree.

**`transcript.jsonl`** — the full model transcript, one JSON object per line, redacted.
Always present in the built package, possibly empty. `harness archive` leaves it out unless
`--with-transcript` is passed.

### `manifest.json`

Written with `indent=2`, keys in this order:

```json
{
  "schema": 1,
  "item_id": 1,
  "external_ref": "issue:816",
  "repo": "Bright-Bots-Initiative/brightboost",
  "base_sha": "…40 chars…",
  "branch": "harness/fix-816-bundle-size-esm",
  "created_at": "2026-09-01T00:00:00Z",
  "harness_version": "1.0.0",
  "backend": "cli",
  "fullsend": false,
  "fullsend_gate": {"F1": false, "F2": false, "F3": true, "F4": true, "F5": true},
  "stages": [{"stage": "propose", "turns": 12, "allowance_pct": 1.8}],
  "gates": [{"name": "npm run lint", "exit_code": 0}],
  "patch_count": 1,
  "touched_paths": ["scripts/check-bundle-size.js"]
}
```

| Key | Meaning |
|---|---|
| `schema` | Format version of this manifest. `1`. |
| `item_id` | The harness's internal work-item id. Under the GitHub store, the issue number in this repository |
| `external_ref` | `issue:<n>` — the upstream thing being worked; `self:<n>` for an item that is an issue here; `sub:<parent>:<i>` for a decomposed child |
| `repo` | The product repository, `owner/name` |
| `base_sha` | The 40-char commit the patches apply to. Matches `BASE` |
| `branch` | The branch inside the bundle |
| `created_at` | When the package was built, `YYYY-MM-DDTHH:MM:SSZ`, always UTC |
| `harness_version` | Which harness produced it |
| `backend` | `cli` for a real model run, `fake` for a replayed one. A `fake` package is a rehearsal |
| `fullsend` | Whether the parallel implementation path was taken |
| `fullsend_gate` | Each of the five fitness conditions and whether it held |
| `stages` | Every stage run: name, turns, allowance spent |
| `gates` | Every gate in the post-change sequence with its exit code |
| `patch_count` | Number of files in `patches/` |
| `touched_paths` | Paths the work package declared it would change |

`fullsend_gate` keys, spelled out:

| Key | Condition |
|---|---|
| F1 | At least 3 slices that can be described without reading each other |
| F2 | At least 15 numbered behaviors |
| F3 | No open questions — a decided spec, not discovery |
| F4 | No touched path under `prisma/`, `migrations/`, `backend/scripts/predeploy*`, or `.github/workflows/` |
| F5 | Fullsend enabled in configuration |

All five must hold for the fullsend path. Any failure drops to the ordinary single-agent
path, and the failing condition is recorded in `DECISIONS.md`.

---

## 3. The reconstruction contract

A reviewer with nothing but this directory MUST be able to run:

```bash
git clone https://github.com/Bright-Bots-Initiative/brightboost.git r && cd r
git checkout "$(cat ../BASE)"
git am ../patches/*.patch
```

On a machine whose global git config sets `core.autocrlf=true` (this Windows host does), a
fresh clone comes up with a dirty working tree and the `checkout` refuses. Clone with the
flag off instead:

```bash
git -c core.autocrlf=false clone https://github.com/Bright-Bots-Initiative/brightboost.git r
```

and obtain a tree identical to the harness's, with no network access to anything but the
public product repository.

The bundle is the offline alternative — no network at all:

```bash
git bundle verify bundle.gitbundle
git clone bundle.gitbundle -b <branch> r
```

Both routes produce the same tree. The bundle is there so that a package which has outlived
its branch, or a reviewer behind a firewall, still reconstructs.

In Delivery 2 there is a third route, which is just git: the branch is on the fork.

```bash
git clone https://github.com/Bright-Bots-Initiative/brightboost.git r && cd r
git fetch https://github.com/<machine-account>/brightboost.git <branch>
git checkout FETCH_HEAD
```

It produces the same tree as the other two, because the branch tip is exactly `BASE` plus
the patch series. If it does not, trust the patches and open a bug against the harness.

---

## 4. Promotion — `harness archive`

```bash
harness archive <item-id> [--with-transcript]
```

Copies the built package from `runs/<run-id>/package/` into
`packages/<item>-<yyyymmddThhmmssZ>/`, which is committed. `runs/` is not.

Two rules:

- It **refuses any item not in state `packaged`**, so a half-built or superseded package
  cannot be promoted by mistake.
- It copies everything **except `transcript.jsonl`**, which comes only when
  `--with-transcript` is passed. Transcripts are long, and most reviews do not want them;
  the evidence a review actually rests on is in `EVIDENCE.md`.

Every text file is re-scrubbed through the redactor on the way in, so a promoted package is
safe to commit by construction rather than by inspection.

In Actions mode the whole `runs/item-<n>/` directory is also uploaded as a workflow artifact
with `if: always()` (B126), so a cancelled or timed-out run still leaves its evidence, kept
for the artifact retention period. The artifact is for post-mortems; the package that matters
is the one in the PR body and, if archived, in `packages/`.

---

## 5. How to review one

1. Read `README.md` — what changed, and how big.
2. Read `DIAGNOSIS.md` — is the stated problem the real problem?
3. Read `DECISIONS.md` — do you agree with the calls, and are the rejected alternatives the
   ones you would have rejected?
4. Read `EVIDENCE.md` — did the gates actually pass? Was anything red in the baseline? Were
   the database gates omitted?
5. Read `ACCEPTANCE.md` — is each criterion genuinely met, or merely gate-green?
6. Reconstruct the tree with the commands above and read the diff.

If steps 4 and 6 disagree, trust step 6 and open a bug against the harness.

---

## 6. The delivery PR — the package as a PR body (Delivery 2)

`harness deliver <id>` runs only on an item in state `packaged` whose gates are green, or
honestly known-red per the proposal's `baseline_red`. It syncs the fork, rebases the work
branch onto the fork's main (a conflict here becomes a `revise --source conflict` item, not
a failure), pushes the branch to the fork, opens a PR from
`<machine-account>:harness/<kind>-<issue>-<slug>` into the product repository's default
branch, requests review from every handle in `.harness/trust.txt`, comments the PR URL on
the harness issue, and sets `harness:shipped`.

**The PR body is generated from the review package** (B108). It is not written by the
model; it is the package's own files, concatenated in this order and redacted by
`redact.py` before it is sent (I-13):

| # | Section | Source | What a reviewer gets |
|---|---|---|---|
| 1 | Summary | `README.md` | the work item and external reference, the **base commit**, the branch, the patch count and filenames, the declared touched paths |
| 2 | Diagnosis | `DIAGNOSIS.md` | the `Diagnosis`, `Issue`, `Approach` and `Risks` sections, with file and line citations |
| 3 | Evidence | `EVIDENCE.md` | the gate sequence **verbatim** — baseline and post-change, each gate with argv, exit code, verdict, and output tails — including the omission note when the database gates did not run |
| 4 | Reconstruction | §3 of this document | the clone/checkout/`git am` commands, the `core.autocrlf=false` variant, and the bundle commands, **included verbatim** with `BASE` and the branch name filled in |

Nothing is summarised on the way in. If the evidence is long, the PR body is long; a short
PR body would be an opinion about the gates, and an opinion is not evidence.

What is **not** in the PR body, and stays in the package on disk (and in `packages/` after
`harness archive`): `DECISIONS.md`, `ACCEPTANCE.md`, `manifest.json`, `patches/`,
`bundle.gitbundle`, `transcript.jsonl`. The PR's own diff is the patch series; the
reconstruction commands let a reviewer rebuild it without trusting the diff view.

Reviewing a delivery PR is §5 with the file names mapped to the sections above, and step 6
answered by any of the three routes in §3. The base commit named in section 1 exists
upstream — that is B105/B106 — so `git checkout <BASE>` in a plain upstream clone works
without touching the fork at all.

Three things the PR cannot do, so a reviewer need not check for them: it cannot be merged,
approved, or have a review dismissed by the harness (I-12); it cannot contain a change under
`.github/**` (I-15 — the token lacks the `workflow` scope and GitHub refuses the push); and
its evidence cannot come from a widened gate, because the sequence that produced it is pinned
by hash (`.harness/PIN`) and a mismatch stops the harness before it spends.

Feedback on the PR — a failing check, a merge conflict, or a review comment from a trusted
handle — becomes one bounded `revise` cycle that re-runs the **complete** gate sequence and
force-pushes the branch only if its tip is still a commit the harness authored (B136, B139).
A revised PR carries a fresh body built the same way from the fresh package.
