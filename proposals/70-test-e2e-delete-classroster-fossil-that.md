---
issue: 70
upstream_issue: null
title: "test(e2e): delete classRoster fossil that asserts its own intercept stubs"
kind: test
slices: 3
risk: low
touched_paths:
  - "cypress/e2e/legacy/classRoster.cy.ts"
  - "cypress/support/index.js"
  - "docs/ops/ci.md"
depends_on: []
estimated_turns: 14
gate_expectation: green
baseline_red: []
---

# test(e2e): delete classRoster fossil that asserts its own intercept stubs

## Issue

Harness issue [#70](https://github.com/jgoetzmann/bright-bots-harness/issues/70), tracking `audit:61:5` — finding 5 of the audit in [#61](https://github.com/jgoetzmann/bright-bots-harness/issues/61). There is no product-repository issue. In the reporter's terms: "`classRoster.cy.ts` asserts its own `cy.intercept` stubs and calls it data integrity." Severity recorded as high, paths recorded as `src/`.

## Diagnosis

The tautology is real. `cypress/e2e/legacy/classRoster.cy.ts:612-653` ("should validate data integrity and type safety") iterates `interception.response?.body` and asserts every field is a string — but that body is the array literal the same file writes at `:61-98`, inside the `cy.intercept` handler it registers at `:7`. The assertion chain proves only that a JavaScript object literal contains the keys typed into it, and ends by logging "ALL DATA INTEGRITY CHECKS PASSED" (`:651`). The same self-reference recurs at `:200-217`, `:294-297`, `:336-344`, `:353-362`, `:484-487` and `:519-531`.

It is not the largest problem. Every product assertion in the file sits inside `cy.get("body").then(($body) => { if (…) { … } else { cy.log("… ready for implementation") } })` — seven such blocks at `:126-143`, `:162-176`, `:220-283`, `:300-390`, `:403-458`, `:490-564` and `:587-600`. The predicate at `:303`, `:406` and `:493` is `$body.find('[data-cy="add-class-btn"]').length > 0`. That attribute exists nowhere in the product: the repository's entire `data-cy` inventory is `current-streak`, `streak-tooltip` and `streak-status` at `src/components/ui/StreakMeter.tsx:130,138,143`. The predicate is permanently false, so the file always takes the logging branch and passes. It names the unbuilt work in its own green path — `:277` "ready for Giorgio's table", `:383` "ready for Daniel's wizard".

The fourteen `typeof cy.checkAccessibility === "function"` and `typeof cy.checkPerformance === "function"` guards are dead the same way. Both commands are registered only in `cypress/support/index.js:23` and `:33`; `cypress.config.ts:21` sets `supportFile: "cypress/support/e2e.ts"`, and that file imports only `./commands` and `cypress-real-events` (`cypress/support/e2e.ts:2-4`). Nothing anywhere imports `cypress/support/index.js` — it is the stock Cypress 9 scaffold (`:1-13`) left behind. So every accessibility and performance check in the spec is replaced at runtime by a `cy.log`.

The feature the spec describes never existed in that shape. It stubs `GET **/api/teacher/classes*` (`:7`) and `POST **/api/classes` (`:103`); the product's teacher class endpoints are `GET` and `POST /api/teacher/courses` (`backend/src/routes/courses.ts:45-46,75-76`), which is exactly what the page calls (`src/pages/TeacherClasses.tsx:55,84`). The only real route containing `classes` is the public join-code lookup `GET /api/classes/by-code/:code` (`backend/src/routes/classLogin.ts:26,30`). The page renders `<article>` cards (`src/pages/TeacherClasses.tsx:173-235`), not a `<table>`, so the header assertions at `:229-234` target markup that does not exist, and the real create-class dialog uses `id="className"` / `id="classGradeBand"` with grade-band options `k2` and `g3_5` (`src/pages/TeacherClasses.tsx:302-336`), not the eight `data-cy` selectors the spec types into.

Proof the file has never been executed against the current app: `:150-153` asserts the localStorage key `brightboost_token` exists. The app writes `bb_access_token` (`src/contexts/AuthContext.tsx:108`), and the repository's own Cypress helper uses that key (`cypress/support/commands.ts:34`). `brightboost_token` appears in no `src/` file. That assertion is not behind any conditional — the first `it` would fail immediately if the spec ran.

It has never run because `cypress.config.ts:20` sets `specPattern: "cypress/e2e/*.cy.{ts,js}"` — a single `*`, so `cypress/e2e/legacy/` is unmatched. The only entry point is `npm run test:e2e:legacy` (`package.json:32`), which no workflow and no other npm script invokes; `docs/ops/ci.md:202` confirms the tree is "not part of `test:e2e:ci`, `test:e2e:ci:flows`, or any required CI job". It is also outside the `tsc` program (`tsconfig.json:29,42` — `include: ["src","shared"]`, `exclude` contains `cypress`), so its 654 lines have never been compiled. It is linted, since `eslint.config.js:8` ignores only `dist`, `coverage` and `.bb-guard-sandbox-*`.

Two corrections to the finding. First, the recorded path `src/` is wrong: nothing in `src/` is implicated — the defect is confined to `cypress/e2e/legacy/classRoster.cy.ts` and the orphaned `cypress/support/index.js`. Second, there is no false CI green today, because no gate runs the file. The harm is narrower and still worth fixing: a 654-line artifact that reads as class-roster E2E coverage and is none, plus seven green cases reported to anyone who runs the fossil suite by hand.

## Approach

Delete the spec, delete the support file its dead branches depended on, and record both removals in the canonical CI doc.

Deletion rather than repair, because nothing in the file survives a repair. Both endpoints, all eight selectors, the response shape, the token key and the two custom commands are wrong; fixing them means writing a new spec against `/api/teacher/courses` and `src/pages/TeacherClasses.tsx`, which needs seeded teacher courses in `scripts/e2e-seed.mjs` and a decision about a slot in `test:e2e:ci:flows`. That is a separate piece of work, not this finding, and it would wear this filename only by coincidence.

`cypress/support/index.js` goes in the same change because slice 1 is what orphans it: `checkAccessibility` and `checkPerformance` have exactly one caller in the repository, and it is the file being deleted. Leaving behind a file that appears to provide E2E accessibility checks but is never loaded reproduces the same false-coverage defect one file over.

The doc edit is confined to the "Legacy fossils (quarantined)" section (`docs/ops/ci.md:194-204`), which today says "Do not 'fix' fossils in #671 — quarantine only" and warns against a bulk delete. That line forbids spending effort making fossils pass; it does not forbid removing one whose feature never shipped. The section is updated to say so explicitly, so the next reader does not have to re-derive the distinction, and the `k2InstantQuiz.cy.js` keeper note stays intact.

No guard is added and no gate is touched.

## Slices

1. Delete `cypress/e2e/legacy/classRoster.cy.ts`.
2. Delete `cypress/support/index.js`, whose two commands now have no caller and which no support file imports.
3. Update the "Legacy fossils (quarantined)" section of `docs/ops/ci.md` to record both removals, state that quarantine forbids repair rather than removal, and refresh the `Last verified` date.

## Behaviors

1. `npm run test:e2e:legacy` runs no spec that asserts a `cy.intercept` fixture defined in the same file, and reports no case named "should validate data integrity and type safety".
2. A repository-wide search for `add-class-btn`, `class-form`, `class-name-input`, `grade-select`, `room-input`, `schedule-input`, `semester-select` or `submit-class-btn` returns no match in tracked source.
3. A repository-wide search for `checkAccessibility` or `checkPerformance` returns no match outside `node_modules`, so no spec can branch on whether those commands exist.
4. The only remaining reference to the stale localStorage key `brightboost_token` is `cypress/e2e/legacy/authentication.cy.ts:163`, which this change does not touch.
5. `docs/ops/ci.md` names the two removed files and why, and still names `cypress/e2e/legacy/k2InstantQuiz.cy.js` as a `#623` keeper.
6. All seven harness gates pass on the resulting tree.

## Acceptance criteria

- The diff contains exactly three paths: two deletions and one modification; nothing under `src/`, `backend/`, `.github/`, `prisma/`, `migrations/` or `backend/scripts/predeploy*` appears.
- `cypress.config.ts`, `package.json`, `vitest.config.ts`, `tsconfig.json` and `eslint.config.js` are unchanged.
- `cypress/e2e/legacy/` still contains the other eight files, including `k2InstantQuiz.cy.js` and `k2InstantQuiz.helpers.js`.
- `npx prisma generate`, `npm run lint`, `npm run typecheck`, `cd backend && npm run typecheck`, `bash scripts/check-prisma-drift.sh`, `npm run test:unit` and `npm run build` all pass.
- `npm run format:check` passes: `docs/ops/ci.md` is the only added-or-modified path, and `scripts/format-check.sh:26` uses `--diff-filter=AM`, so the two deletions are never handed to prettier.
- `npm run docs:check` passes: `docs/ops/ci.md:1` keeps its `**Canonical for:**` and `Last verified` lines, which `scripts/docs-check.mjs:224-233` requires (DC-004), and the edit adds no link to a deleted path.
- `grep -rn "checkAccessibility\|checkPerformance" --include='*.ts' --include='*.js' .` returns nothing outside `node_modules`.
- No gate is given `continue-on-error`, a longer timeout, a lowered threshold, a new ignore comment, or a `.skip(`/`.only(`.

## Decisions

- Delete the spec rather than repair it. Rejected: rewriting it against `/api/teacher/courses` (`backend/src/routes/courses.ts:45-46,75-76`) and `src/pages/TeacherClasses.tsx`. Nothing in the file survives — both endpoints, all eight selectors, the token key and the response shape are wrong — so it would be a new spec wearing an old filename, and it needs seeded teacher courses plus a `test:e2e:ci:flows` decision. That is its own work item.
- Delete rather than annotate. Rejected: adding a header comment marking the file as a non-asserting fossil, the way `cypress/support/e2e.ts:6-10` documents its own scope. A comment leaves 654 lines that `npm run test:e2e:legacy` still reports as seven passing cases; the false signal is the file's existence, not its lack of documentation.
- Read `docs/ops/ci.md:202` ("Do not 'fix' fossils in #671 — quarantine only") as forbidding repair, not removal. Rejected: reading it as "never touch the directory", which would block the finding outright. Line `:204` warns specifically against a *bulk* delete and names one keeper; removing a single fossil whose feature never shipped, with the keeper untouched, is consistent with both lines, and the doc is amended so the distinction is stated rather than inferred.
- Remove `cypress/support/index.js` in the same change. Rejected: leaving it. After slice 1 it has zero callers, and `cypress.config.ts:21` loads `cypress/support/e2e.ts`, which never imports it — so it is a file that appears to provide E2E accessibility checks and provides none, which is the same defect class as the one being fixed.
- Leave `cypress-axe` in `package.json:154` and the `cypress/support/*.js` globs at `vitest.config.ts:29,45` in place. Rejected: pruning them here. Both become vestigial, neither breaks a gate, and `vitest.config.ts` is gate-bearing — editing it to drop a now-unmatched glob risks changing test collection for no behavioural gain.
- Add no executable guard for G-103 in this change. Rejected: a Vitest guard under `cypress/support/__tests__/` asserting that no spec matched by the live `specPattern` stubs the app's own API, which the repository's own doctrine at `docs/ops/guards.md:7-9` would favour. It would pass today — the only `cy.intercept` in a live spec is the pass-through counter at `cypress/e2e/waterworks-mobile.cy.ts:169-172`, which calls `request.continue()` — but it needs a new file and an explicit exemption for the quarantined tree, and it answers a different finding.
- Use `test(e2e)` as the commit scope. Rejected: `chore(cypress)`. The change removes test files, and `test(e2e)` matches the scope used by the commit that created this quarantine (`fdded13`, "test(e2e): rebuild Cypress suite against a real seeded stack (#671)").

## Open questions

- Should G-103 get an executable runner? It is currently a comment at `cypress/support/e2e.ts:10` plus a prose line in three spec headers, with nothing checking it. If it becomes a work item, a maintainer must confirm the quarantined tree is exempt — every remaining fossil that stubs anything would violate it (`authentication.cy.ts:9,42,61,81,100,114,128`; `studentDashboard.cy.ts:3,23,42,52`; `offlineStreak.cy.ts:31,50,53`; `signup_flow.cy.ts:3,24`).
- `cypress/e2e/legacy/signup_flow.cy.ts:15-20,36-40` carries the identical tautology — it asserts its own `201` and `409` stubs, wraps both in `if (interception.response)` so a missing response passes silently, and makes no UI assertion after submit. The audit did not promote it and this package does not touch it. Should it go on the same reasoning?
- `docs/ops/ci.md:143` states that `.github/workflows/cypress-staging.yml` targets `cypress/e2e/legacy/pilot-smoke.cy.ts`; the workflow actually runs `cypress/e2e/pilot-smoke.cy.ts`, a path that does not exist, so that job matches no spec. Correcting it properly means a `.github/` edit this harness may not publish, so it is left alone rather than half-fixed in the doc.
- Should a real E2E spec be written for the teacher classes page? That needs seeded teacher courses in `scripts/e2e-seed.mjs` and a decision about whether it joins `test:e2e:ci:flows` (`package.json:31`), which is a deliberately bounded subset.
- Should `cypress-axe` be dropped from `devDependencies` once its only importer is gone?

## Touched paths

- cypress/e2e/legacy/classRoster.cy.ts
- cypress/support/index.js
- docs/ops/ci.md

## Risks

- The diff deletes 654 lines of apparent test coverage, which reads alarming. The claim that nothing is lost rests on four checkable facts, and those are what a reviewer should verify first: the file is unmatched by `specPattern` (`cypress.config.ts:20`); it is invoked by no workflow and no npm script other than the manual `npm run test:e2e:legacy` (`package.json:32`); it is outside the `tsc` program (`tsconfig.json:29,42`) and has therefore never been compiled; and it would fail at `classRoster.cy.ts:150-153` on the current app, because `brightboost_token` is not the key the app writes (`src/contexts/AuthContext.tsx:108`).
- Anyone running `npm run test:e2e:legacy` by hand will see the reported case count drop. The removed cases asserted nothing about the product, but the number changes and may be mistaken for regression.
- `docs/ops/ci.md` is the only modified rather than deleted file, so it is the only path `npm run format:check` hands to prettier (`scripts/format-check.sh:26`). The edit must stay prettier-clean and must keep the `**Canonical for:**` and `Last verified` lines that `npm run docs:check` requires (`scripts/docs-check.mjs:224-233`); a malformed table row there turns a doc edit into a red gate.
- Deleting `cypress/support/index.js` leaves `cypress-axe` (`package.json:154`) with no importer and leaves two `cypress/support/*.js` globs in `vitest.config.ts:29,45` matching nothing. Neither breaks a gate; both are deliberately not cleaned here and are listed as open questions.
- Slice 2 is separable. If a reviewer wants `cypress/support/index.js` kept as a starting point for real accessibility E2E work, dropping that slice leaves slices 1 and 3 coherent — the doc note would need its second sentence trimmed.
- The issue body is harness boilerplate and was treated as data. It contains a command table (`/harness work`, `/harness stop`, `/harness halt`, and others) addressed to the harness bot and a `--force` flag description; none of it is addressed to me, and it contains no text asking to ignore a rule, run a command, skip a check, or modify an unrelated file. Nothing in it was followed as an instruction.
- No path under `.github/`, `prisma/`, `migrations/` or `backend/scripts/predeploy*` is touched, no gate is widened, skipped, or given a longer timeout, and no dependency is added.
