<!-- version: 1 -->
# Implement the approved work package — fullsend variant

Repository: `$repo`
Branch: `$branch`

You are working inside a disposable clone at the current working directory. The branch is already
created and checked out. Do not create branches, do not switch branches, do not commit, and **do not
push** — the harness commits for you after formatting the files you changed.

This package cleared the fullsend fitness gate: it declares three or more independently describable
slices, fifteen or more numbered behaviors, no open questions, and no path under `prisma/`,
`migrations/`, `backend/scripts/predeploy*`, or `.github/workflows/`. That means you may build the
slices **fast and in parallel, with the typechecker turned off**, and reconcile afterwards.

## The approved work package

$spec_text

## How to work

Take the units under `## Slices` one at a time and write each one **top to bottom in a single pass**.

1. **Do not run the typechecker, the linter, the formatter, or the test runner while writing.** Every
   slice imports things the other slices have not written yet, so every error you would see is noise
   you cannot act on. Write the next file instead. The harness runs the real gates once, at the end,
   and every broken import surfaces in that one pass.
2. **Write each slice against the package's stated interfaces, not against the other slices' code.**
   Match the names, argument order, and return shapes given under `## Behaviors` and `## Acceptance
   criteria` exactly. Inside a slice, do what you like; at the boundaries the package is law.
3. **Import what you wish existed.** If a slice needs a helper another slice owns, import it at the
   path the package names, even if nothing is there yet.
4. **Duplicate small helpers on purpose** rather than stopping to coordinate. Collapsing two copies of
   a date formatter afterwards is cheap; blocking on which one to keep is not.
5. **No stubs, no TODOs, no `throw new Error("not implemented")`.** Write the real body every time. If
   a piece genuinely cannot be written, leave the function out entirely and list it in your report.
6. **Write the direct, boring implementation.** Two named cases means two branches, not a registry.
   Behaviors covered is what counts; configuration options and type parameters count against you.

Once every slice exists, make one reconciliation pass over the whole change: fix the imports that do
not line up, delete the duplicate helpers you no longer need, and make the whole thing coherent. Only
then is the change finished.

## Hard constraints

Identical to the ordinary path, and no less absolute because the pace is higher. A diff violating any
of these is rejected whole and the item is blocked.

- **Do not edit any file under `.github/workflows/`.**
- **Do not widen, skip, or disable a gate.** No `continue-on-error`, no `.skip(`, no `.only(`, no
  raised timeout, no new lint-ignore or type-ignore comment, no relaxed rule, no deleted test. Going
  fast is a licence to skip *checking your own work as you go*, never a licence to move the finish
  line.
- **Do not run `npm run format`, `prettier --write .`, or `prettier --check .`.**
- **Do not touch `.env`, `.env.*`, or any credential-bearing file.**
- **Do not touch `prisma/`, `migrations/`, or `backend/scripts/predeploy*`.**
- **Do not add a runtime dependency** unless the package explicitly calls for one.

## When you are finished

Write a short report, in plain prose:

- Every slice, and the files it produced.
- What the reconciliation pass changed, and what you deleted.
- Every decision you took that was not already settled in the package, with the alternative you
  rejected. Record it; never decide silently.
- Anything you left out, and why.
- Whether you believe the repository gates will pass, and which one worries you most.
