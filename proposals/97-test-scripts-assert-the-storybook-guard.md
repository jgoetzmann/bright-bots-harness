---
issue: 97
upstream_issue: null
title: "test(scripts): assert the storybook guard's own sabotage exit mapping, not a copy"
kind: test
slices: 2
risk: low
touched_paths:
  - "scripts/verify-storybook-empty-suite.mjs"
  - "scripts/__tests__/verify-storybook-empty-suite.test.ts"
depends_on: []
estimated_turns: 15
gate_expectation: green
baseline_red: []
---

# test(scripts): assert the storybook guard's own sabotage exit mapping, not a copy

## Issue

This is harness work item #97, tracking finding 12 of audit jgoetzmann/bright-bots-harness#64 (machine reference `audit:64:12`). No issue exists on `Bright-Bots-Initiative/brightboost`.

The reporter's words: severity **medium**, paths `scripts/verify-storybook-empty-suite.mjs:703-718`, `exitForSabotageClass`. The title states the problem: the Storybook guard's test checks a classification→exit-code mapping that the test writes itself.

## Diagnosis

The finding cites two places as one. `exitForSabotageClass` is not in `scripts/verify-storybook-empty-suite.mjs`. It is a local function in the test file, and lines 703-718 of the script are a separate, inline copy of the same mapping. The two copies have nothing linking them.

- **The test's copy.** `scripts/__tests__/verify-storybook-empty-suite.test.ts:19-27` defines `exitForSabotageClass(label, mod)`:
  - `"pass"` → `mod.EXIT_OK`
  - `"false"` → `mod.EXIT_FALSE`
  - anything else → `mod.EXIT_CANNOT_CHECK`

  It reads only the three constants from the module (`verify-storybook-empty-suite.mjs:37-39`).
- **Where the test uses it.** The U3-05 cases feed `mod.classifySabotageResult(...)` into this local helper and check the result: lines 223-225, 231-233 and 235-237. The only production code these assertions reach is `classifySabotageResult` (`.mjs:132-136`) and the constant values. The label→exit step they claim to cover is the test's own code.
- **The production copy.** The mapping the guard actually uses is inline in `runPhases`, `.mjs:699-718`:
  - `"cannot-check"` → `phaseExit = EXIT_CANNOT_CHECK` (707)
  - `"false"` → `phaseExit = EXIT_FALSE` (712)
  - `else` → `phaseExit = EXIT_OK` (717)

  `phaseExit` is then returned at line 727.
- **Why U3-05 can't catch a regression.** Suppose lines 707 and 712 were swapped, so a no-op sabotage exits 1 and a toothless sabotage exits 2. Every U3-05 assertion would still pass, because none of them executes `runPhases`.
- **The copies already disagree.** Production's final branch is a bare `else` that reports `PASS` / `EXIT_OK` (`.mjs:713-717`). It fails open on any label it doesn't recognise. The test helper's fallthrough returns `EXIT_CANNOT_CHECK` (test `:26`), so it fails closed. The two only agree on the three labels `classifySabotageResult` can return today. The mismatch can't happen now, but it shows the test is not describing the production code.
- **A precedent in the same module does this correctly.** The healthy-phase mapping is an exported function, `exitForHealthyCount` (`.mjs:119-125`). The test calls it directly (`test.ts:40, 48, 201-202`).
- **The production mapping is not entirely untested.** `scripts/__tests__/guard-sandbox-isolation.test.ts` drives `runStorybookEmptySuiteGuard` with a synthetic probe (`syntheticProbe`, `:234-246`):
  - healthy 15 / sabotaged 0 → exit 0, at `:1488-1538`
  - 15/7 → 1 and 15/15 → 2, with and without cleanup failure, at `:1540-1576`

  So a swap like the one above would turn that file red. The real defect is narrower than "untested". The unit test named for this property (U3-05) is circular and gets credit for coverage it doesn't provide, and the mapping has no named, directly testable home.
- **Gates.** Both files are collected by the `unit` Vitest project (`vitest.config.ts:15`; `package.json:26` `test:unit`). Neither is in the root `tsc` program: `tsconfig.json:29` includes only `src` and `shared`, and `:35-37` exclude tests.

## Approach

1. **Add a named mapping to the script.** Add an exported `exitForSabotageClass(classification)` to `scripts/verify-storybook-empty-suite.mjs`, placed directly after `classifySabotageResult` (`:132-136`) and following the `exitForHealthyCount` pattern:
   - `"pass"` → `EXIT_OK`
   - `"false"` → `EXIT_FALSE`
   - any other value → `EXIT_CANNOT_CHECK`

   Give it a JSDoc block in the style of its neighbours.
2. **Use it in `runPhases`.** At `.mjs:699-718`, compute `phaseExit = exitForSabotageClass(classification)` once. Choose the log line from `phaseExit` rather than from `classification`: `EXIT_CANNOT_CHECK` logs the no-op line, `EXIT_FALSE` the FAIL line, otherwise the PASS line. The three log strings stay byte-identical. No other line of `runPhases` changes.
3. **Point the test at the real function.** In `scripts/__tests__/verify-storybook-empty-suite.test.ts`:
   - Delete the local helper at `:19-27`.
   - Make the U3-05 assertions call `mod.exitForSabotageClass(...)`.
   - Add direct checks for each label, plus one for an unrecognised label.
4. **Leave the integration tests alone.** `guard-sandbox-isolation.test.ts:1488-1576` is not changed. It remains the behavioural proof that `runPhases` is wired to the exported function.

This is the smallest change that makes U3-05 test production code. It moves nothing else and changes no reachable exit code.

## Slices

1. In `scripts/verify-storybook-empty-suite.mjs`, add and export `exitForSabotageClass` next to `classifySabotageResult`. Then replace the three `phaseExit = EXIT_*` assignments in `runPhases` (`:703-718`) with a single `phaseExit = exitForSabotageClass(classification)`. The log branch is chosen by `phaseExit`, and its text is unchanged.
2. In `scripts/__tests__/verify-storybook-empty-suite.test.ts`, delete the local `exitForSabotageClass` helper (`:19-27`) and route the U3-05 assertions through `mod.exitForSabotageClass`. Add direct assertions for `"pass"`→0, `"false"`→1, `"cannot-check"`→2 and an unrecognised label→2.

## Behaviors

1. `exitForSabotageClass("pass")` exported from `scripts/verify-storybook-empty-suite.mjs` returns `EXIT_OK` (0).
2. `exitForSabotageClass("false")` returns `EXIT_FALSE` (1).
3. `exitForSabotageClass("cannot-check")` returns `EXIT_CANNOT_CHECK` (2).
4. `exitForSabotageClass` given any other value returns `EXIT_CANNOT_CHECK` (2), never 0.
5. With a synthetic probe, `runStorybookEmptySuiteGuard` still returns 0 for healthy 15 / sabotaged 0, 1 for 15/7, and 2 for 15/15.
6. The guard's sabotage-phase log line for each of those three outcomes is byte-identical to today's (`.mjs:705`, `:710`, `:715`).
7. Changing the mapping inside the production `exitForSabotageClass` makes U3-05 in `verify-storybook-empty-suite.test.ts` fail.

## Acceptance criteria

- `scripts/__tests__/verify-storybook-empty-suite.test.ts` no longer defines any function that maps sabotage labels to exit codes. `grep -n "function exitForSabotageClass" scripts/` matches only `scripts/verify-storybook-empty-suite.mjs`.
- `scripts/verify-storybook-empty-suite.mjs` exports `exitForSabotageClass`.
- Inside the classification branch of `runPhases`, `phaseExit` is assigned exactly once, and the value comes from `exitForSabotageClass(classification)`.
- The three sabotage-phase log template strings in the diff are unchanged character for character.
- Every U3-05 exit-code assertion calls `mod.exitForSabotageClass`. At least one assertion covers an unrecognised label → `EXIT_CANNOT_CHECK`.
- `npm run test:unit` passes, including the unmodified `#815 CI-27 verify-storybook-empty-suite` cases in `guard-sandbox-isolation.test.ts`.
- `npm run lint` passes.
- The implementer reports a falsification run with its actual output, then reverts it and shows the file matches the intended final content:
  1. Temporarily make the production `exitForSabotageClass` return `EXIT_OK` for `"false"`.
  2. Run `npx vitest run --project unit scripts/__tests__/verify-storybook-empty-suite.test.ts`.
  3. Observe U3-05 fail.
- The diff touches exactly the two paths listed under `## Touched paths`. Nothing under `.github/` is touched, and no test is skipped or loosened.
- Prettier was applied to the two changed files only.
- The commit header passes `@commitlint/config-conventional`, and every body line is ≤ 100 characters.

## Decisions

- **Export the mapping from production and test it there.** Rejected alternative: deleting the exit-code assertions from U3-05 and relying on `guard-sandbox-isolation.test.ts:1540-1576`. That avoids editing the guard script, but leaves the mapping inline and unnamed. It also leaves U3-05's test names ("→ EXIT_CANNOT_CHECK (2)", "→ EXIT_FALSE") claiming things the test no longer checks.
- **Rejected: driving `runStorybookEmptySuiteGuard` with fakes inside U3-05.** That needs a real sandbox (copies of `src`, `shared`, `.storybook`), which `guard-sandbox-isolation.test.ts` already builds with 180 s timeouts. Duplicating it in the unit file would add cost with no new coverage.
- **Keep the name `exitForSabotageClass` and make it a function next to `classifySabotageResult`.** It follows `exitForHealthyCount` (`.mjs:119-125`). Rejected: a lookup-table constant. Looking up an unrecognised key returns `undefined`, which would become the process exit code, and no other mapping in the module is a table.
- **Unrecognised labels map to `EXIT_CANNOT_CHECK`.** This matches the test helper's current fallthrough (`test.ts:26`) and `phaseExit`'s initial value (`.mjs:665`). Production's current `else → EXIT_OK` (`.mjs:713-717`) is rejected because a guard must never report PASS for a verdict it doesn't recognise. `classifySabotageResult` can only return the three known labels, so no reachable exit code changes.
- **Choose the log branch from `phaseExit`, not from `classification`.** This way the printed verdict can never disagree with the returned exit code, e.g. `PASS` printed alongside exit 2 for an unrecognised label. Rejected: keeping the `classification` branches and adding a separate assignment, which would leave two decision points that have to be kept in step.
- **Log text stays byte-identical.** CI logs and anyone scanning them depend on these strings. Changing the wording is out of scope.
- **No source-regex "wiring" assertion like the ones in `guard-sandbox-isolation.test.ts:1700-1732`.** Rejected: it would be brittle, and the wiring is already proven by behaviour at `:1488-1576`. A regex would only restate that.
- **Leave the other inline exits in `runPhases` (`.mjs:682-696`) and `selectMode` alone.** They aren't part of this finding. Folding them in would be a refactor nobody asked for.
- **`kind` is `test`, not `fix`.** The defect is test fidelity. The production edit is an extraction with no reachable behaviour change.
- **Do not edit `guard-sandbox-isolation.test.ts`.** Its CI-27 cases are the regression check that this change left the guard's verdicts intact, so they must run unmodified.

## Open questions

None

## Touched paths

- scripts/verify-storybook-empty-suite.mjs
- scripts/__tests__/verify-storybook-empty-suite.test.ts

## Risks

- **This edits a live CI guard.** `scripts/verify-storybook-empty-suite.mjs` runs as the CI-27 step, both as its own `build-and-test` step and in parity, according to the comment at `guard-sandbox-isolation.test.ts:1690-1691`. A slip in `runPhases` would change a gate's verdict on every PR. The reviewer should check two things in the `runPhases` hunk:
  - it changes only the `phaseExit` assignment and the three branch conditions;
  - the log strings and the `finally` / `dropProbeTmp` structure (`.mjs:720-722`) are untouched.
- **One deliberate semantic change on an unreachable path.** An unrecognised classification moves from exit 0 to exit 2. It can't happen today (`.mjs:132-136`), but the reviewer should confirm they want fail-closed.
- **No gates were run for this proposal.** I only had read access to the tree and ran no command. `gate_expectation: green` is inferred: the change is confined to one script and its unit test, `tsc` doesn't cover either file (`tsconfig.json:29-37`), and no test is removed or relaxed. The implementation must run the gates and report real output.
- **Formatting.** The repo's `format` script is whole-tree (`package.json:16`). The implementer must run Prettier only on the two touched files. lint-staged checks staged files (`package.json:199-201`).
- **The finding's citation is partly wrong.** It puts `exitForSabotageClass` in the `.mjs` at `:703-718`. The function of that name is in the test file (`:19-27`); `:703-718` is the inline production copy. The Diagnosis covers both, and the reviewer shouldn't expect a function of that name in the script before this change.
- **Coverage claim.** The audit rates this medium. Because `guard-sandbox-isolation.test.ts:1540-1576` already catches a broken production mapping, the fix mainly makes the U3-05 unit test honest rather than closing a coverage gap. The reviewer can weigh priority accordingly.
- **The issue body contains no instructions aimed at an automated agent.** The steering-command table and links are harness boilerplate for maintainers. I read them as data and did not act on any of them.
