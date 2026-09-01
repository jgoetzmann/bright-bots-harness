<!-- version: 1 -->
# Propose a work package

Repository: `$repo`
Issue: **#$issue_number** — $issue_title

## Issue body, verbatim

$issue_body

## Your task

Read the repository at the current working directory and produce a **work package**: a decided,
reviewable plan for fixing this issue. You are not implementing it now. You are producing the document
that a human approves before implementation starts, and that a reviewer later checks the diff against.

Diagnose from the source. Cite files and line numbers. Do not restate the issue back as a diagnosis,
and do not assert a cause you have not read the code for. If the real defect turns out to be somewhere
other than where the reporter thought, say so and show why.

## Required output format

Output Markdown with **exactly these headings, in exactly this order, spelled exactly this way**. A
parser depends on them. Emit every section even when it is short; write `None` under a section that is
genuinely empty. Do not add sections, do not reorder, do not wrap the document in a code fence.

```
# <type>(<scope>): <one-line statement of the defect or change>

## Issue
## Diagnosis
## Approach
## Slices
## Behaviors
## Acceptance criteria
## Decisions
## Open questions
## Touched paths
## Risks
```

What belongs in each:

- **Title line.** A conventional-commit header: `fix(scripts): ...`, `chore(ci): ...`, `feat(api): ...`.
  Lower-case subject, no trailing period, at most 100 characters including the type and scope.
- **`## Issue`** — the link, the number, and the problem in the reporter's own terms.
- **`## Diagnosis`** — what is actually wrong, with file and line citations. Evidence, not assertion.
- **`## Approach`** — what will change and why this way rather than the alternatives.
- **`## Slices`** — numbered units of work, each describable without reading the others. One per line,
  numbered `1.`, `2.`, `3.` …
- **`## Behaviors`** — numbered, testable, one line each. A behavior states an observable outcome, not
  an implementation step. One per line, numbered.
- **`## Acceptance criteria`** — one per line, each independently checkable by a reviewer.
- **`## Decisions`** — every decision taken, with the alternative rejected and why. At least one entry.
  One per line, `-` bulleted.
- **`## Open questions`** — anything that cannot be decided without a human. One per line. Write `None`
  when there are none. Be honest here: a fabricated certainty costs far more than a listed question.
- **`## Touched paths`** — every repository-relative path expected to change, one per line, `-`
  bulleted. This list is compared against the actual diff at package time, so it must be complete and
  must not include paths you do not intend to touch.
- **`## Risks`** — what could go wrong, and what the reviewer should look hardest at.

## Constraints on the plan you propose

- No path under `.github/workflows/` may appear in `## Touched paths`, ever.
- No gate may be widened, skipped, given a longer timeout, or marked `continue-on-error`.
- Nothing touching `prisma/`, `migrations/`, or `backend/scripts/predeploy*` unless the issue is
  squarely about that path and you say so explicitly under `## Risks`.
- No new runtime dependency unless the issue is about a dependency.
- Prefer the smallest change that fixes the actual defect.
