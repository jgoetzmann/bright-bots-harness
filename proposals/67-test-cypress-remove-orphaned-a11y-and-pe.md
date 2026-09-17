---
issue: 67
upstream_issue: null
title: "test(cypress): remove orphaned a11y and performance commands never loaded by the support file"
kind: test
slices: 3
risk: low
touched_paths:
  - "cypress/support/index.js"
  - "cypress/e2e/legacy/classRoster.cy.ts"
  - "vitest.config.ts"
  - "docs/ops/ci.md"
depends_on: []
estimated_turns: 25
gate_expectation: green
baseline_red: []
---

# test(cypress): remove orphaned a11y and performance commands never loaded by the support file

## Issue

Harness issue [#67](https://github.com/jgoetzmann/bright-bots-harness/issues/67), promoted from finding 2 of audit [#61](https://github.com/jgoetzmann/bright-bots-harness/issues/61) (`audit:61:2`), severity high. No product-repository issue exists. The reporter's terms: the Cypress accessibility and performance checks have never executed. The named paths are `supportFile`, `cypress/support/e2e.ts`, `./commands`, `cypress-real-events`, `cypress/support/index.js`, `checkAccessibility`, `checkPerformance`, and `if (typeof cy.checkAccessibility === "function")`.

## Diagnosis

The reporter is right that the checks never run, and the chain has three independent breaks — any one of them alone is sufficient.

**1. The file that defines the commands is not a support file.** `cypress.config.ts:21` sets `supportFile: "cypress/support/e2e.ts"`. That entry point imports exactly two things: `./commands` (`cypress/support/e2e.ts:2`) and `cypress-real-events` (`cypress/support/e2e.ts:4`). It never imports `./index`. `cypress/support/index.js` is a Cypress-9-era default support file — its own header still describes itself as "processed and loaded automatically before your test files" (`cypress/support/index.js:2-14`), which stopped being true at Cypress 10; the project runs Cypress `^13.17.0` (`package.json:153`). A repository-wide grep for `cypress/support/index` finds no importer. So `Cypress.Commands.add("checkAccessibility", …)` (`cypress/support/index.js:23`) and `Cypress.Commands.add("checkPerformance", …)` (`cypress/support/index.js:33`) are never evaluated, and `import "cypress-axe"` (`cypress/support/index.js:20`) is never reached — `cypress-axe` (`package.json:154`) has no importer anywhere in the tree. The comment at `cypress.config.ts:3` records that the pre-#671 baseline was `supportFile: false`, i.e. no support file at all, so these commands were dead before the current entry point existed too.

**2. The only caller is a quarantined fossil.** The sole consumer is `cypress/e2e/legacy/classRoster.cy.ts`. `specPattern` is `cypress/e2e/*.cy.{ts,js}` (`cypress.config.ts:20`), which does not match `cypress/e2e/legacy/`. `docs/ops/ci.md:194-204` states this is deliberate: "Previous-generation specs live under `cypress/e2e/legacy/` and are **excluded** … They are not part of `test:e2e:ci`, `test:e2e:ci:flows`, or any required CI job." The only script that reaches them, `test:e2e:legacy` (`package.json:32`), is invoked by no workflow — `ci-cd.yml` runs `test:e2e:ci` (line 118), `waterworks-mobile.cy.ts` (line 121) and `test:e2e:ci:flows` (line 322), and nothing else.

**3. The call sites are written to pass when the command is absent.** All fourteen call sites are guarded by `typeof cy.checkAccessibility === "function"` or `typeof cy.checkPerformance === "function"` (`cypress/e2e/legacy/classRoster.cy.ts:129, 165, 179, 252, 266, 313, 373, 425, 465, 477, 499, 507, 542, 545`). Because break 1 makes the guard permanently false, every site takes the `else` branch and emits a reassuring log — "Accessibility testing ready - will activate when cypress-axe is confirmed working" (line 140), "Performance testing ready - will activate when custom commands are confirmed working" (line 187), and ten more. A test named `should log in successfully and pass all quality gates` (line 122) passes while asserting no quality gate at all. This is the false-assurance core of the finding.

Two further facts bear on what to do about it, and both argue against simply wiring the file up:

**`checkPerformance` cannot fail even if loaded.** `cypress/support/index.js:39-46` computes `domContentLoaded` as `domContentLoadedEventEnd - domContentLoadedEventStart` and `loadComplete` as `loadEventEnd - loadEventStart`. Those are the durations of the event *handlers*, typically single-digit milliseconds, not time-to-DOMContentLoaded or time-to-load. `firstContentfulPaint` falls back to `0` when the paint entry is missing (line 44-45). Measured against thresholds of 2000/3000/1500 ms (lines 49-53), the comparison at line 57 is vacuous: the command as written would pass on an arbitrarily slow page. Loading it would add a green light, not a check.

**Nothing type-checks these call sites.** The root `tsconfig.json:42` excludes `cypress` from `tsc --noEmit`, so `npm run typecheck` never sees `cy.checkAccessibility`, which has no `Chainable` declaration anywhere. `eslint.config.js` applies no type-aware rules. That is why fourteen references to a non-existent command have sat in a `.ts` file without a single gate complaining.

`vitest.config.ts:29` and `vitest.config.ts:45` both exclude `cypress/support/*.js`; `index.js` is the only file in that directory matching the pattern, so both entries exist solely for it.

## Approach

Delete the dead machinery and record the real gap, rather than wiring the orphan into the live support file.

Wiring is the obvious-looking fix and it is the wrong one here. It would add an `import "cypress-axe"` to the support bundle that the required per-PR shell smoke loads, which I cannot exercise in this environment (Cypress needs a live stack and `CYPRESS_SWA_URL`), in exchange for zero new coverage: the only caller is a spec that `docs/ops/ci.md:202` says must stay quarantined, and one of the two commands is vacuous by construction. Adding real accessibility assertions to a *runnable* spec is worth doing, but it is a different change — it needs a live stack to measure the current violation count before any assertion is committed, and it must not be smuggled in under a cleanup.

So: remove `cypress/support/index.js`; remove the fourteen always-false guards and their dead branches from the fossil, leaving every real assertion in place; drop the two `vitest.config.ts` exclusions whose only referent disappears; and state plainly in `docs/ops/ci.md` that no runnable Cypress suite asserts accessibility or performance. After this change, the repository no longer implies coverage it does not have, and the gap is written down where the next person will find it.

The fossil edit is deletion of provably dead branches, not repair of a fossil, so it does not conflict with the quarantine rule at `docs/ops/ci.md:202`. Nothing under `.github/` is touched and no dependency is added or removed.

## Slices

1. Delete `cypress/support/index.js`, and remove the two now-unreferenced `cypress/support/*.js` patterns at `vitest.config.ts:29` and `vitest.config.ts:45`.
2. In `cypress/e2e/legacy/classRoster.cy.ts`, remove all fourteen `typeof cy.checkAccessibility`/`typeof cy.checkPerformance` guards together with both branches of each, including any `cy.get("body").then(...)` wrapper that exists only to host one (lines 126-143 and 162-176), preserving every other assertion byte-for-byte.
3. Add a short subsection to `docs/ops/ci.md` under "Legacy fossils (quarantined)" recording that no runnable Cypress suite asserts accessibility or performance, that the Cypress-9-era `cypress/support/index.js` was removed as never-loaded, and that `cypress-axe` remains declared but unimported.

## Behaviors

1. `npm run test:e2e:ci` and `npm run test:e2e:ci:flows` behave exactly as before, because the support bundle they load (`cypress/support/e2e.ts`) is unchanged.
2. `npm run test:e2e:legacy` runs `classRoster.cy.ts` with the same set of executed assertions as before, since only permanently-false branches are removed.
3. A grep for `checkAccessibility`, `checkPerformance`, `injectAxe` or `checkA11y` across `cypress/` and `src/` returns no matches.
4. No file in the repository references `cypress/support/index.js`.
5. `npm run test:unit` collects the same test files and `npm run test:coverage:quiz` reports the same coverage numbers, because `cypress/support/*.js` matched only the deleted file.
6. A reader of `docs/ops/ci.md` learns that accessibility and performance are not asserted by any Cypress suite.

## Acceptance criteria

- `cypress/support/index.js` is absent from the tree and no tracked file mentions that path.
- `cypress/e2e/legacy/classRoster.cy.ts` contains no `typeof cy.` guard and none of the "ready for activation" / "ready to activate" log strings.
- Every `cy.visit`, `cy.get`, `cy.contains`, `cy.wait`, `cy.intercept` and `expect(...)` present in `classRoster.cy.ts` before the change is still present after it; the diff for that file is deletions only.
- `vitest.config.ts` contains no `cypress/support/*.js` pattern, and its `coverage.thresholds` block is unchanged.
- `npm run lint`, `npm run typecheck`, `backend: npm run typecheck`, `npm run test:unit` and `npm run build` are green.
- `npm run docs:check` is green (no `DC-002` broken relative link introduced by the new `docs/ops/ci.md` text).
- The diff contains no path under `.github/`, no change to `package.json` dependencies, and no change to `package-lock.json`.

## Decisions

- Delete `cypress/support/index.js` rather than import it from `cypress/support/e2e.ts`. Rejected the import because it would pull `cypress-axe` into the support bundle loaded by the required shell smoke — a change I cannot exercise here — while adding no coverage, since the only caller is quarantined by `docs/ops/ci.md:196-202`.
- Do not port `checkPerformance` anywhere. Rejected preserving it because `cypress/support/index.js:39-46` measures event-handler durations rather than elapsed times and falls back to `0` for a missing FCP entry, so it would pass on any page; keeping it would institutionalise a vacuous check.
- Do not add an accessibility assertion to a runnable spec in this package. Rejected because the current violation count cannot be measured without a live stack, and an unmeasured assertion dropped into a required job is a coin flip on CI; proposed as a separate work item instead.
- Edit the fossil `classRoster.cy.ts` despite `docs/ops/ci.md:202` ("Do not 'fix' fossils — quarantine only"). Rejected leaving it untouched because the guards would then reference commands defined nowhere in the repository, which is more misleading than today; the edit is pure deletion of dead branches, not repair, and the file is in no CI job so no gate is exposed.
- Keep `cypress-axe` in `devDependencies`. Rejected removing it because that means `package.json` plus `package-lock.json` churn on a gate-relevant file for an install-time-only cost, and it would have to be re-added by the follow-up that adds real coverage. Recorded as unimported in the docs and raised as an open question.
- Remove the two `vitest.config.ts` exclusions in the same slice as the file deletion. Rejected leaving them because a pattern pointing at a deleted file is the same class of stale pointer this issue is about; the removal is provably inert, as `index.js` is the only `cypress/support/*.js`.
- Leave `.github/workflows/cypress-staging.yml`'s fossil spec pointer (`docs/ops/ci.md:143`) alone. Rejected fixing it because the harness never publishes a change under `.github/` and it is a different defect.
- Do not bump the "Last verified against code: 2026-08-25" line at `docs/ops/ci.md:1`. Rejected bumping it because this change re-verifies one section, not the document, and a fresh date would assert more than was checked.

## Open questions

- Should `cypress-axe` be dropped from `devDependencies` now that nothing imports it, or held for the follow-up that adds real accessibility coverage? A maintainer should choose; this package keeps it.
- Does the team want automated accessibility coverage on a runnable spec (e.g. `cypress/e2e/smoke.cy.ts`)? That needs a live stack to baseline the current violations and should be its own work item.
- `docs/ops/ci.md:143` already flags that `.github/workflows/cypress-staging.yml` targets the fossil path `cypress/e2e/legacy/pilot-smoke.cy.ts`. The harness cannot touch `.github/`, so a human has to carry that.

## Touched paths

- cypress/support/index.js
- cypress/e2e/legacy/classRoster.cy.ts
- vitest.config.ts
- docs/ops/ci.md

## Risks

- **The issue body contains text addressed to an automated agent** — a `/harness …` command table with entries such as `/harness stop`, `/harness go` and `/harness halt`, plus links to a harness repository. It was read as data and not obeyed; nothing in this plan comes from it.
- **Biggest review target: the `classRoster.cy.ts` diff.** Each of the fourteen sites must lose only the guard and its two branches. Two of them (lines 126-143 and 162-176) sit inside a `cy.get("body").then(($body) => …)` wrapper that exists solely to host the check, so that wrapper goes too — but the visually similar wrappers at lines 220, 300, 403, 490 and 587 carry real assertions and must stay. The diff for this file should be deletions only; any added or modified line there deserves scrutiny.
- **`vitest.config.ts` is coverage-gate-adjacent.** Removing an exclusion pattern is inert only while no other `cypress/support/*.js` exists. Confirm the coverage numbers and the 90% thresholds at `vitest.config.ts:47-52` are unchanged.
- **Coverage gap is unchanged, not fixed.** This package removes a false signal; it does not add accessibility coverage. Anyone reading the merge as "accessibility is now tested" would be wrong, which is exactly why slice 3 writes the gap down.
- **Gates were not run.** The planning environment has no shell, so no gate output backs the `green` expectation — it is a prediction from the fact that nothing in the diff is reachable from `lint`, `typecheck`, `test:unit` or `build` inputs except the two inert `vitest.config.ts` lines. The implementer must actually run them and report real output.
- **Local behaviour change for anyone running the fossil suite:** the reassuring "ready for activation" log lines disappear from `npm run test:e2e:legacy` output. No assertion changes.
