# Package format

Two artifacts, one after the other. `harness propose` produces the **work package**: a plan, in
Markdown, that a human reads and approves before anything is implemented. `harness package`
produces the **review package**: a directory a reviewer can act on without having seen the harness.

Both also travel as pull requests, built from the files on disk. The work package is the file in a
proposal PR against this repository (gate 1, §1.1); the review package is the source of the delivery
PR body against the product repository (gate 2, §6).

---

## 1. The work package — output of `propose`

A `# <type>(<scope>): <subject>` title line, then these sections in this order:

```markdown
## Issue
Link, number, and the problem in the reporter's terms.

## Diagnosis
What is actually wrong, with file and line citations. Evidence, not assertion.

## Approach
What will change and why this way.

## Slices
Numbered, independently describable units. Three or more is one fullsend condition.

## Behaviors
Numbered, testable, one line each. Fifteen or more is one fullsend condition.

## Acceptance criteria
What must be true for a reviewer to accept. Each line independently checkable.

## Decisions
Every decision made, with the alternatives rejected and why.

## Open questions
Anything that cannot be decided without a human. Non-empty blocks the fullsend path
and, if any question is load-bearing, blocks the item.

## Touched paths
Every path expected to change.

## Risks
What could go wrong, and what the reviewer should look hardest at.
```

The parser (`parse_work_package` in `harness/stages/propose.py`) matches headings by name and takes
whatever sits between them. A heading it does not find comes back empty; the order is the prompt's
rule, not the parser's. List sections, `Slices`, `Behaviors` and `Touched paths` among them, are
read from bulleted (`-`, `*`, `+`) and numbered (`1.`, `1)`) lines only: other lines are skipped, a
section whose whole content is `None` or `n/a` parses as empty, and a path written in backticks is
taken without them.

The work package is written to `runs/<run-id>/spec/<item>.md` and recorded on the work item as
`spec_path`. `Slices`, `Behaviors` and `Open questions` feed the fullsend fitness gate; `Diagnosis`
and `Acceptance criteria` are carried into the review package; and `Touched paths` is listed there
beside the patch series, for the reviewer to compare with the diff.

### 1.1 The proposal file

The work package is also written as `proposals/<issue>-<slug>.md` on a branch
`harness/propose-<issue>` and opened as a PR against this repository. Merging that PR is approval; a
review or a comment is not. The file is the body above preceded by YAML front matter with a closed
schema: eleven keys, all required, unknown keys rejected, in this order (`PROPOSAL_KEYS`,
`validate_proposal`):

```yaml
---
issue: 816                      # positive int: this work item, which must still be open
upstream_issue: 816             # positive int or null: the product repository's issue
title: "…"                      # non-empty str, at most 120 characters
kind: fix | chore | test | docs # closed enum
slices: 1                       # int, 1..5
risk: low | medium | high       # closed enum
touched_paths:                  # list[str], 1..40, each must exist in the product repo
  - src/…
depends_on: []                  # positive ints: issues that must reach stage:done first
estimated_turns: 40             # int, 1..MAX_TURNS_IMPLEMENT
gate_expectation: green | known-red   # closed enum
baseline_red: []                # gate names from gates._SEQUENCE, and nothing else
---
```

**Where the values come from.** The model emits the front matter as a JSON object inside an HTML
comment, `<!-- proposal: {...} -->`, and every key it supplies overrides a value derived from the
document: `slices` from the number of entries under `## Slices` (at least 1), `touched_paths` from
the entries under `## Touched paths`, `kind` from the title's conventional-commit type (`fix` when
the title has none, `chore` when the type is not one of the four), `upstream_issue` from the item's
product-issue reference, and the constants `risk: medium`, `depends_on: []`,
`gate_expectation: green`, `baseline_red: []` and `estimated_turns: 40` capped at
`MAX_TURNS_IMPLEMENT`. `issue` is always forced to this work item, and a block giving
`upstream_issue: null` falls back to the reference. When the model emits no block, the derivation is
recorded as a decision, and an unbulleted `## Touched paths` then yields an empty list, which fails
validation.

The schema never cross-checks the front matter against the document: `slices: 4` over three bulleted
slices validates. The prompt requires the two to agree, and nothing enforces it. The fullsend
fitness gate reads the parsed document, not the front matter.

**Validation.** A proposal whose front matter fails validation is never opened as a PR: the stage
retries once with the errors appended to the prompt, then marks the item `stage:blocked` with the
errors in a comment (B103). Every `touched_paths` entry is checked with a contents read against the
product repository at the base commit propose's own clone was taken at, so the tree the model read
is the tree validation checks (B104, B219). A path that does not exist fails validation, so the list
must name existing files even for a change that mostly adds new ones. `gate_expectation: known-red`
requires a non-empty `baseline_red` naming gates already failing on the untouched tree; `implement`
measures the baseline itself either way and judges the change by new failures against it.
`depends_on` is read by the dispatcher, which will not start the item until every listed issue is
`stage:done` (B107). The front matter is the only place the model's output becomes an instruction,
which is why it is a closed schema.

Under the sqlite store (local mode) the same file is written under `proposals/` and no PR is opened;
`harness approve <id>` is the gate instead.

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

The package holds these entries and nothing else: the packager prunes anything not on the list
before it finishes, so a stray scratch file cannot ride along into a review.

**`README.md`** names the work item and external reference, the base commit, the branch, the patch
count and filenames, the paths the work package expected to touch, and the two ways to reconstruct
the tree. It states that applying any of this is a human action: `deliver` pushes the branch to the
fork and opens a PR, and nothing reaches the product repository's default branch without a person
merging it. **`DIAGNOSIS.md`** carries the `Diagnosis`, `Issue`, `Approach` and `Risks` sections of
the work package. **`DECISIONS.md`** has three parts: the decisions declared in the work package;
the fullsend fitness gate as a table of the five conditions, with whether each held and which path
was taken; and every decision recorded during the run, appended as it happened with a timestamp.

**`EVIDENCE.md`** holds the verbatim output of the product repository's own gate sequence, in two
sections. *Baseline* is the untouched tree at `BASE`, run before any change: anything red there is
pre-existing, is not attributable to the change, and never justifies loosening anything.
*Post-change* is the branch exactly as packaged. Each gate gets its name, its argv, its exit code, a
PASS/FAIL verdict, and the captured tails of stdout and stderr in a fenced block. Nothing is
summarised, trimmed or reworded. When the database gates (`npx prisma generate`,
`bash scripts/check-prisma-drift.sh`) were not run, the file opens with a note naming them.

**`ACCEPTANCE.md`** lists each acceptance criterion from the work package, numbered, marked `met` or
`NOT met`, with the evidence the verdict rests on. A criterion is marked met only when the
post-change gate sequence is fully green; anything else is reported as not met with the failing
gates named. The declared touched paths are listed at the end for comparison against the diff.

**`BASE`** is the 40-character base commit sha and a trailing newline, so
`git checkout "$(cat BASE)"` works without cleanup. That commit always exists upstream, because the
fork's default branch is fast-forward-only (B105) and every work branch is cut from it at a commit
upstream already has (B106). **`patches/`** is the series produced by
`git format-patch <BASE>..HEAD`; it applies cleanly onto `BASE` with `git am` and onto no other
commit, and zero patches is a legitimate outcome that shows as `patch_count: 0`.
**`bundle.gitbundle`** is the branch as a git bundle, verifiable with `git bundle verify` and
clonable directly, so a reviewer with no network at all can still get the tree.
**`transcript.jsonl`** is the full model transcript, one JSON object per line, redacted: always
present in the built package, possibly empty, and left out by `harness archive` unless
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

The five fullsend conditions: **F1** at least 3 slices that can be described without reading each
other; **F2** at least 15 numbered behaviors; **F3** no open questions; **F4** no touched path under
`prisma/`, `migrations/`, `backend/scripts/predeploy*` or `.github/workflows/`; **F5** fullsend
enabled in configuration. All five must hold for the fullsend path. Any failure drops to the
ordinary single-agent path, and the failing condition is recorded in `DECISIONS.md`.

---

## 3. The reconstruction contract

A reviewer with nothing but this directory must be able to run:

```bash
git clone https://github.com/Bright-Bots-Initiative/brightboost.git r && cd r
git checkout "$(cat ../BASE)"
git am ../patches/*.patch
```

and obtain a tree identical to the harness's, with no network access to anything but the public
product repository. On a machine whose global git config sets `core.autocrlf=true`, a fresh clone
comes up with a dirty working tree and the `checkout` refuses; clone with the flag off instead. The
bundle is the offline route, for a package that has outlived its branch or a reviewer behind a
firewall, and the branch is also on the fork:

```bash
git -c core.autocrlf=false clone https://github.com/Bright-Bots-Initiative/brightboost.git r

git bundle verify bundle.gitbundle
git clone bundle.gitbundle -b <branch> r

git fetch https://github.com/<machine-account>/brightboost.git <branch> && git checkout FETCH_HEAD
```

All three routes produce the same tree, because the branch tip is exactly `BASE` plus the patch
series. If they disagree, trust the patches and open a bug against the harness.

---

## 4. Promotion — `harness archive`

```bash
harness archive <item-id> [--with-transcript]
```

Copies the built package from `runs/<run-id>/package/` into
`packages/<item>-<yyyymmddThhmmssZ>/`, which is committed. `runs/` is not. It refuses any item not
in state `packaged`, so a half-built or superseded package cannot be promoted by mistake, and it
copies everything except `transcript.jsonl`, which comes only with `--with-transcript`. Every text
file is re-scrubbed through the redactor on the way in, so a promoted package is safe to commit.

In Actions mode the whole `runs/item-<n>/` directory is also uploaded as a workflow artifact with
`if: always()` (B126), kept 14 days, so a cancelled or timed-out run still leaves its evidence. The
package a review rests on is the one in the PR body and, if archived, in `packages/`.

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

## 6. The delivery PR

`harness deliver <id>` runs only on an item in state `packaged` whose gates are green, or known-red
as the proposal's `baseline_red` declared. It syncs the fork, rebases the work branch onto the
fork's main (a conflict becomes a `revise --source conflict` item), pushes the branch to the fork,
opens a PR from `<machine-account>:harness/<kind>-<issue>-<slug>` into the product repository's
default branch, requests review from every handle in `.harness/trust.txt`, comments the PR URL on
the harness issue, and sets `stage:needs-review`.

The model writes none of the PR body (B108, B232): `deliver.build_pr_body` assembles it from the
package on disk and the item's configuration, and `redact.py` redacts it before it is sent (I-13).
What a reviewer needs first is uncollapsed; evidence sits behind a `<details>`, because the package
holds the authoritative copy. In order:

| # | Section | Source | What a reviewer gets |
|---|---|---|---|
| 1 | Closing line | the item's product issue | `Closes #N` when the item came from a product issue, so merging closes it; absent otherwise |
| 2 | Who opened it | `.harness/trust.txt`, the work item | the machine account, that the harness cannot merge and will not push again unasked, a link to the work item, and a mention of every trusted handle as the review request |
| 3 | Steering it from here | `harness/keywords.py` | the `/harness revise`, `rebase`, `stop` and `status` comments, and whose comments are honoured |
| 4 | If the checks are not running | fixed text | why GitHub may hold the first workflow run from the machine account, and the **Approve and run** button that releases it |
| 5 | Review checklist | `CONTRIBUTING.md`, `EVIDENCE.md` | the product's reviewer checklist, with build, lint and unit tests ticked only when the gate sequence measured them passing |
| 6 | What this change is, and why *(collapsed)* | `DIAGNOSIS.md` | the `Diagnosis`, `Issue`, `Approach` and `Risks` sections, with file and line citations |
| 7 | Gate results — this repository's own sequence, run on the branch *(collapsed)* | `EVIDENCE.md` | a digest: one row per gate per phase with its exit code and verdict, and **every failing gate's output kept whole** |
| 8 | Self-audit line (D70) | `runs/<run-id>/selfaudit.json` | one status line naming the commit the audit read — clean, notes only, blocking findings, or not run — then at most five blocking findings and a count of the rest, each cycle's fix-pass outcome, and a pointer to `DECISIONS.md`. A model reviewing its own diff: an opinion, labelled so, never a gate result. *Not run for this revision* when there is no record or the branch is no longer the audited commit; absent altogether when `MAX_SELF_AUDIT_CYCLES` is `0` |
| 9 | The review package, verbatim *(collapsed)* | `README.md` | the work item and external reference, the **base commit**, the branch, the patch count and filenames, the declared touched paths |
| 10 | Rebuild this exact tree yourself *(collapsed)* | §3 of this document | the upstream clone and fork fetch, the `git am` route and the bundle route, with `BASE` and the branch filled in |
| 11 | About the harness *(collapsed)* | `links.signature` | the footer every issue and pull request the harness opens ends with (B227) |

Gate output is summarised only here (B232, D52). A green gate's output stays in the package; a red
gate's is in the body whole. `EVIDENCE.md` carries every gate verbatim and is the record.

What stays in the package on disk (and in `packages/` after `harness archive`) and not in the body:
`DECISIONS.md` (which lists every self-audit finding, notes included), `ACCEPTANCE.md`,
`manifest.json`, `patches/`, `bundle.gitbundle`, `transcript.jsonl`. The PR's own diff is the patch
series; the reconstruction commands let a reviewer rebuild it without trusting the diff view.

Reviewing a delivery PR is §5 with the file names mapped to the sections above, and step 6 answered
by any of the three routes in §3. The base commit named in section 9 exists upstream (B105, B106),
so `git checkout <BASE>` in a plain upstream clone works without touching the fork.

The harness cannot merge the PR, approve it, or dismiss a review of it (I-12). Its evidence cannot
come from a widened gate, because the sequence that produced it is pinned by hash (`.harness/PIN`)
and a mismatch stops the harness before it spends.

Check the file list for any change under `.github/` (I-15). The harness refuses to push a commit of
its own that touches `.github/`, and that refusal is the only guard: the token carries the
`workflow` scope, so GitHub would accept the push. A delivery PR whose file list shows anything
under `.github/` is a harness bug: do not merge it.

A `/harness revise` or `/harness rebase` from a trusted handle starts one bounded revise cycle that
re-runs the complete gate sequence and force-pushes the branch only if its tip is still a commit the
harness authored (B136, B139).
