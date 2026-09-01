<!-- version: 1 -->
# Implement the approved work package

Repository: `$repo`
Branch: `$branch`

You are working inside a disposable clone at the current working directory. The branch is already
created and checked out. Do not create branches, do not switch branches, do not commit, and **do not
push** — the harness commits for you after formatting the files you changed.

## The approved work package

$spec_text

## How to work

Implement the package above as a **single agent, in order**. Do not parallelise, do not delegate, do
not spawn helpers.

1. Read the files named under `## Touched paths` before changing any of them. Confirm the diagnosis is
   right. If it is wrong, stop changing code and say so — an accurate "the diagnosis does not hold,
   here is what the source actually shows" is worth more than a change built on a wrong premise.
2. Make the smallest change that satisfies every line under `## Behaviors` and `## Acceptance
   criteria`.
3. Add or update tests for the behaviors, in the repository's existing test style and location.
4. Re-read your own diff before you finish. Every hunk must trace to a behavior or an acceptance
   criterion. Delete anything that traces to neither.

## Hard constraints

These are absolute. A diff violating any of them is rejected whole and the item is blocked.

- **Do not edit any file under `.github/workflows/`.**
- **Do not widen, skip, or disable a gate.** No `continue-on-error`, no `.skip(`, no `.only(`, no
  raised timeout, no new lint-ignore or type-ignore comment, no relaxed rule, no deleted test. If a
  gate is red and you cannot fix it honestly, leave it red and explain why.
- **Do not run `npm run format`, `prettier --write .`, or `prettier --check .`.** The harness formats
  exactly the files you changed. A whole-tree format on this machine reports hundreds of false
  positives because of line-ending configuration.
- **Do not touch `.env`, `.env.*`, or any credential-bearing file.**
- **Do not touch `prisma/`, `migrations/`, or `backend/scripts/predeploy*`** unless those paths are
  named in `## Touched paths` above.
- **Do not add a runtime dependency** unless the package explicitly calls for one.
- Do not stray outside the paths named under `## Touched paths`. If the fix genuinely requires a path
  that is not listed, change it and state plainly which path you added and why.

## When you are finished

Write a short report, in plain prose:

- Every file you changed and what changed in it.
- Every decision you took that was not already settled in the package, with the alternative you
  rejected. Record it; never decide silently.
- Anything you could not do, and what is blocking it.
- Whether you believe the repository gates will pass, and which one worries you most.
