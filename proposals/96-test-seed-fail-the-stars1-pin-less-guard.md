---
issue: 96
upstream_issue: null
title: "test(seed): fail the STARS1 PIN-less guard loudly when its roster anchor is missing"
kind: test
slices: 2
risk: low
touched_paths:
  - "prisma/__tests__/seedStars1.test.ts"
depends_on: []
estimated_turns: 15
gate_expectation: green
baseline_red: []
---

# test(seed): fail the STARS1 PIN-less guard loudly when its roster anchor is missing

## Issue

Harness issue #96 (`jgoetzmann/bright-bots-harness`) tracks finding 11 of audit #64 (`audit:64:11`). There is no product issue. The reporter rated it medium and pointed to `src.indexOf("starStudents")`, `-1`, `src.slice(-1, 599)`, `expect(block).not.toMatch(/loginPin/)` and `prisma/seed.cjs`. In their words: the seed regression suite checks source text, and one of its cases passes without testing anything if the name it searches for is renamed.

## Diagnosis

The defect is in the test, not in the seed.

- `prisma/__tests__/seedStars1.test.ts:46-52` ("keeps STARS1 students PIN-less") builds its window as `src.slice(src.indexOf("starStudents"), src.indexOf("starStudents") + 600)` and then only checks `expect(block).not.toMatch(/loginPin/)`. The start index is never checked, and nothing in the window is required to be present.
- If `starStudents` is renamed, `indexOf` returns `-1` and the call becomes `src.slice(-1, 599)`. `String.prototype.slice` reads a negative start as `length - 1`. `prisma/seed.cjs` runs past 4,138 lines (`main()` is called at line 4138), so the start is far beyond 599. When start is at or past end, `slice` returns `""`. `expect("").not.toMatch(/loginPin/)` always passes, so the PIN-less guard would check nothing and still pass.
- No other case in the file catches the rename. Line 42 uses `/star-nova|starStudents/`, which still matches through the `star-nova` id at `prisma/seed.cjs:307`. The gradeBand case at lines 27-36 is anchored on `joinCode: "STARS1"`, not on the roster. The whole file would stay green.
- A second weakness makes the same case miss real PINs, not just renames: the window is a fixed 600 characters. By a count I did by hand from reading the file (not measured with code, so approximate), the window starts at `prisma/seed.cjs:306` and ends near line 326. That covers the user upsert's `create` (314-321) and `update` (322) with about 80 characters to spare. Adding a comment or a couple of fields to that upsert would push `update:` out of the window, and a `loginPin` added there would go unseen.
- The seed itself is correct today. `loginPin` appears nowhere in `prisma/seed.cjs` (grep finds no matches). The star roster at lines 306-331 sets only `id`, `name`, `role`, `loginIcon`, `xp` and `level`. The field the guard protects is real: `prisma/schema.prisma:45` (`loginPin String?`).
- The other unanchored slices do not have this problem. `seedStars1.test.ts:29-35` and `prisma/__tests__/seedFixtures.test.ts:217-223` also slice at an unchecked `indexOf`, but each makes a positive `toMatch` assertion. That assertion fails on an empty window, so neither case can pass when its anchor is missing.

## Approach

All changes are in `prisma/__tests__/seedStars1.test.ts`. No seed, schema or product file changes.

1. Add a small local function, `starRosterSection(source)`, that returns the text between two anchors:
   - start: the roster declaration `const starStudents` (`prisma/seed.cjs:306`)
   - end: the section's closing log text `Seeded K-2 emoji-login class STARS1` (`prisma/seed.cjs:333`), searched only after the start
   - It throws an `Error` that names the missing anchor if either one is absent. The window covers the whole roster section (array, loop, user upsert, enrollment upsert) however long it grows, with no fixed character count.
2. Rewrite the PIN-less case to call `starRosterSection(src)`. It first requires the window to contain the write a PIN would be added to (`prisma.user.upsert(` and `loginIcon: s.loginIcon`), then keeps the existing `not.toMatch(/loginPin/)`.
3. Add one case that runs `starRosterSection` on the real seed text with the roster renamed in memory. It asserts that the call throws, which proves in CI that the empty-window path is closed. `prisma/seed.cjs` is never written.

The test keeps checking source text rather than running the seed. The file's header (lines 10-15) records that choice on purpose, and running `prisma/seed.cjs` means running `main()` at require time (line 4138), with a real `PrismaClient`, `bcryptjs` and `seedAdvanced.cjs`.

## Slices

1. In `prisma/__tests__/seedStars1.test.ts`, add `starRosterSection(source)` with both anchors checked, and rewrite the "keeps STARS1 students PIN-less" case to use it, with the positive `prisma.user.upsert(` / `loginIcon: s.loginIcon` assertions before the `loginPin` check.
2. In the same file, add a case that calls `starRosterSection` on `src` with `starStudents` replaced in memory (for example `src.replace(/starStudents/g, "emojiStudents")`) and expects it to throw an error naming `const starStudents`.

## Behaviors

1. On the current `prisma/seed.cjs`, the PIN-less case passes.
2. With the roster declaration `const starStudents` absent from the seed text, the PIN-less guard fails with an error naming that anchor instead of checking an empty string.
3. With the closing log line `Seeded K-2 emoji-login class STARS1` absent, or placed before the roster declaration, the guard fails with an error naming that anchor.
4. A `loginPin` anywhere between the roster declaration and the section's closing log line, including in the user upsert's `create` or `update` at any length, fails the case.
5. A window that does not contain the roster's `prisma.user.upsert(` call and `loginIcon: s.loginIcon` fails the case.
6. The new self-test case passes on the current tree and fails if the anchor check in `starRosterSection` is removed.
7. Every other case in `seedStars1.test.ts` behaves exactly as before.

## Acceptance criteria

- The diff touches only `prisma/__tests__/seedStars1.test.ts`.
- Neither `prisma/seed.cjs` nor `backend/prisma/seed.cjs` appears in the diff, and the byte-identity case at `seedStars1.test.ts:61-63` still passes.
- The PIN-less case no longer uses `indexOf(...) + 600` or any other fixed-length window.
- Both anchors are checked for `-1` before slicing, and the end anchor is searched from the start index.
- The PIN-less case contains at least one positive assertion on the window before `not.toMatch(/loginPin/)`.
- A new case exercises the renamed-anchor path against in-memory text and expects a throw.
- `npx vitest run --project unit prisma/__tests__/seedStars1.test.ts` passes, and its output is quoted in the delivery.
- `npm run lint` and `npm run test:unit` are green, and their output is quoted in the delivery.
- Prettier was applied to `prisma/__tests__/seedStars1.test.ts` only (`npx prettier --write prisma/__tests__/seedStars1.test.ts`), not to the whole tree.
- No `.skip(`, `.only(`, raised timeout, lint-disable comment or deleted case appears in the diff.

## Decisions

- Fix the test, not the seed. `prisma/seed.cjs:306-331` is correct and has no `loginPin`. Editing the seed would also force the same edit to `backend/prisma/seed.cjs` because of the byte-identity invariant (`seedStars1.test.ts:62`), and no product behavior is wrong.
- Keep checking source text rather than running the seed. The rejected alternative is moving the star roster into `prisma/seedFixtures.cjs` and testing its payload with a stub, as `seedFixtures.test.ts` does for enrollments. That would touch four files under `prisma/` and `backend/prisma/` to fix one false-green case.
- Bound the window with two anchors instead of keeping `+ 600` and only adding a `-1` check. The 600-character window has about 80 characters of slack (hand count), and overflowing it is the same false-green failure: a PIN outside the window goes unseen.
- End the window at the section's closing log line (`prisma/seed.cjs:333`). Rejected `prisma.enrollment.upsert`, which would exclude the rest of the loop body, and `fixtureEnrollments`, which starts the #700 section and is already an anchor for `seedFixtures.test.ts:210`.
- Start at `const starStudents` rather than the bare `starStudents`. It pins the declaration explicitly, not whatever occurrence happens to come first.
- Have `starRosterSection` throw a plain `Error` rather than use the inline `expect(idx).toBeGreaterThan(-1)` style of `seedFixtures.test.ts:211,545`. The self-test needs something it can call on mutated text and catch with `toThrow`, and the thrown message tells a future editor which anchor to update.
- Commit the self-test rather than rely on a one-off mutation check. A one-off check would mean temporarily editing `prisma/seed.cjs`, which is outside the touched paths, and it would leave no lasting guard against someone reverting the anchor check.
- Leave the gradeBand case (`seedStars1.test.ts:27-36`) and `seedFixtures.test.ts:217-223` alone. Their positive `toMatch` assertions already fail on an empty window, so neither passes vacuously, and changing them would go beyond this finding.
- Leave line 42 (`/star-nova|starStudents/`) as it is. It is a whole-file presence check, and a rename is now caught by the new anchor check.

## Open questions

- Do the maintainers want a separate work item to move seed-roster checks from source text to behavioral tests (roster extracted into `seedFixtures.cjs` and tested with a stub)? This package deliberately does not do that.
- Should the two other unchecked `indexOf` slices (`seedStars1.test.ts:29-32`, `seedFixtures.test.ts:217-220`) get explicit anchor checks, just for clearer failure messages? They are not vacuous today, so this package leaves them alone.

## Touched paths

- prisma/__tests__/seedStars1.test.ts

## Risks

- **Path under `prisma/`.** The only touched path, `prisma/__tests__/seedStars1.test.ts`, sits under `prisma/`. I am naming it explicitly because the finding is directly about this file. No schema, migration, seed, `seedFixtures.cjs` or predeploy file changes.
- **Gates were not run for this proposal.** I had no shell, so `gate_expectation: green` is an inference from reading the code, not a measurement. The character count for the 600-character window was done by hand from the source and is approximate.
- **Type-checking.** The test file is not covered by `npm run typecheck`: `tsconfig.json:29` includes only `src` and `shared`. Only `npm run lint` (`eslint.config.js:8` ignores just `dist`, `coverage` and the guard sandbox) and `npm run test:unit` check it. The reviewer should check the new function's types by eye.
- **End anchor is a log message.** Rewording the log at `prisma/seed.cjs:333` will make this case fail. That is intended, but it may surprise whoever rewords it, which is why the error names the anchor to update.
- **Limit of text checking.** A PIN set outside the roster section, or through a shared object spread into the upsert, would still not be seen by a text search. That limit is why the behavioral rewrite is listed as an open question.
- **Issue body.** It contained no instructions aimed at the agent. It includes a command table addressed to human maintainers, which I read as data and did not act on.
- **Where to look hardest.** Check that the end-anchor search starts from the start index, and that the positive assertions come before `not.toMatch(/loginPin/)` in the same window.
