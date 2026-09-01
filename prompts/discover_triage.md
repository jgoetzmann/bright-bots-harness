<!-- version: 1 -->
# Triage: rank the candidate issues

Below is the list of open issues on `Bright-Bots-Initiative/brightboost` that survived mechanical
filtering. Every issue here is already known to be unassigned, unclaimed by any in-flight branch or
pull request, free of the excluded labels, and carrying the allowlist label.

Your job is to rank them for a Tier 0 harness that can produce a patch series and a review package but
**cannot push, cannot comment, and cannot ask anyone a question**.

## Candidates

$candidates

## How to rank

Rank highest the issues that are:

1. **Decided.** The issue states what should happen, not merely that something is wrong. No design
   question is left open.
2. **Locally verifiable.** The repository's own gates — lint, typecheck, unit tests, build — can
   demonstrate the fix. Nothing needs a live database, a deployed environment, or a credential.
3. **Narrow.** A small number of files, no schema change, no migration, no CI change, no dependency
   bump that ripples.
4. **Self-contained.** The fix does not depend on another open issue or another in-flight branch
   landing first.

Rank lowest, or drop entirely, anything that needs a product decision, needs organization access,
touches `prisma/`, `migrations/`, `backend/scripts/predeploy*`, or `.github/workflows/`, or whose
correct behaviour cannot be settled from the repository alone.

## Output format

Output **only** the ranked issue numbers, best first, one number per line, nothing else. No prose, no
headings, no explanation, no blank lines between them. A leading `#` is accepted. Omit any candidate
you judge unsuitable rather than ranking it last.

Example of the exact shape expected:

```
816
742
709
```
