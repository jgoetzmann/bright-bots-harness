# Package format

Two artifacts, one after the other. `harness propose` produces the **work package**: a
plan, in Markdown, that a human reads and approves before anything is implemented.
`harness package` produces the **review package**: a directory a reviewer can act on
without having ever seen the harness.

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
touch, and the two ways to reconstruct the tree. It states plainly that the harness holds
no credentials and cannot push, and that applying any of this is a human action.

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
`git checkout "$(cat BASE)"` works without cleanup.

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
| `item_id` | The harness's internal work-item id |
| `external_ref` | `issue:<n>` — the upstream thing being worked |
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
