---
issue: 95
upstream_issue: null
title: "test(e2e): run the six unwired root-level cypress specs in the e2e-flows job"
kind: test
slices: 3
risk: medium
touched_paths:
  - "scripts/__tests__/ciWiring.test.ts"
  - "package.json"
  - "docs/ops/ci.md"
depends_on: []
estimated_turns: 35
gate_expectation: green
baseline_red: []
---

# test(e2e): run the six unwired root-level cypress specs in the e2e-flows job

## Issue

Harness issue #95 tracks finding 10 of audit #64 (`audit:64:10`, severity medium). There is no product issue. The finding's title: "Six root-level Cypress specs are not run by any workflow". It cites `smoke.cy.ts`, `waterworks-mobile.cy.ts` and `package.json:31` as the specs and script that CI does run, leaving the rest of `cypress/e2e/*.cy.ts` unrun.

## Diagnosis

The finding is correct. The count of six is correct, and so is the list of what runs.

- `cypress.config.ts:20` sets `specPattern: "cypress/e2e/*.cy.{ts,js}"`. That matches twelve root-level specs: `activity-complete`, `activity-open`, `auth-invalid`, `auth-login`, `auth-signup`, `dashboard-loads`, `dashboard-progress`, `pathways-consent`, `session`, `smoke`, `student-join`, `waterworks-mobile`.
- `.github/workflows/ci-cd.yml` runs Cypress in only three places:
  - `:118` runs `npm run test:e2e:ci`, which is `smoke.cy.ts` only (`package.json:30`).
  - `:121` runs `npx cypress run --spec "cypress/e2e/waterworks-mobile.cy.ts"`.
  - `:322` runs `npm run test:e2e:ci:flows`, which names four specs (`package.json:31`): `auth-login`, `activity-complete`, `dashboard-progress`, `pathways-consent`.
- The only other workflow that invokes Cypress is `.github/workflows/cypress-staging.yml:36`. It runs on a label or manual dispatch and points at `cypress/e2e/pilot-smoke.cy.ts`, which does not exist at the root. The file lives under `legacy/`, and `docs/ops/ci.md:143` already records this.
- A grep of `.github/` for `cypress|test:e2e|cy.ts|cy:run` finds nothing else. So `activity-open`, `auth-invalid`, `auth-signup`, `dashboard-loads`, `session` and `student-join` never run in CI.

The exclusion was never decided on the record:

- Outside the spec files themselves, no file in the repository mentions any of the six file names.
- `prompts/2026-08-07-ticket-671-cypress-rebuild.md:20-21` shows #671 wrote all of them ("smoke/auth/session/dashboard/student/activity") and ran them locally ("rebuilt Cypress suite 14/14").
- `docs/ops/ci.md:99` is also stale. It lists three specs for `test:e2e:ci:flows`; `package.json:31` has four, since `pathways-consent` was added by #874.

All six need the seeded live stack, so the per-PR shell job `build-and-test` (no backend, per `docs/ops/ci.md:14`) cannot host any of them:

- `auth-invalid.cy.ts:3,13-15` needs `E2E_TEACHER_EMAIL` and a real `/api/login` rejection.
- `auth-signup.cy.ts:17-22` posts a real teacher signup.
- `dashboard-loads.cy.ts:11,15` runs `npm run e2e:reset` and then `cy.loginAsTeacher()` (`cypress/support/commands.ts:24-28`, `POST /api/login`).
- `session.cy.ts:3` calls `cy.loginAsTeacher()`.
- `student-join.cy.ts:15-23,51-61` needs `CYPRESS_STUDENT_ID`, `CYPRESS_LESSON_ID` and the seeded class `E2E001`.
- `activity-open.cy.ts:13-29,51-60` calls `/api/classes/by-code`, `/api/auth/class-login` and `/api/module`.

The `e2e-flows` job (`ci-cd.yml:212-322`) already provides all of this: Postgres, migrate, `e2e:seed`, backend and frontend, and the `E2E_TEACHER_*` / `CYPRESS_*` env. It picks specs only through the npm script at `ci-cd.yml:322`.

**Likely reason the set was left small (my inference, not written down anywhere).** The #671 notes (`prompts/2026-08-07-ticket-671-cypress-rebuild.md:33`) say the auth rate limit needed a local E2E relax flag. At that time, class sign-in shared the 20-per-15-minute `authLimiter` with teacher `/login` (`student-join.cy.ts:5-6` still says so). #875 moved class sign-in into separate limiters that don't count successful requests (`backend/src/utils/security.ts:46-134`; `backend/src/routes/classLogin.ts:37,98-99`). No code references the relax flag any more.

**Rate-limit budget with the six added.** `authLimiter` (`security.ts:9-15`) counts every request to `/login`, `/signup/*`, `/forgot-password` and `/reset-password` (`backend/src/routes/auth.ts:68,126,182,515,578`). It allows 20 per IP per 15 minutes, and the job's timeout is 15 minutes (`ci-cd.yml:214`), so the whole run shares one window.

| Spec | Hits today | Hits with this change |
|---|---|---|
| `auth-login` | 1 | 1 |
| `dashboard-progress` (`:36-40`, `:101`) | 2 | 2 |
| `pathways-consent` (`:89-104`, `:233-239`) | 5 | 5 |
| `auth-invalid` | — | 1 |
| `auth-signup` | — | 1 |
| `dashboard-loads` | — | 1 |
| `session` | — | 1 |
| **Total, no retries** | **8** | **12** |

`retries.runMode: 1` (`cypress.config.ts:27-30`) can repeat each retryable test once, and `pathways-consent` sets `retries: 0`. If every retryable test retries, the total is 19 of the 20 allowed. `student-join` and `activity-open` use only the class sign-in limiters, which don't count successes. The global `apiLimiter` allows 1000 (`backend/src/server.ts:114-123`). I found no per-account lockout on `/login`.

## Approach

Add the six specs to `test:e2e:ci:flows` at `package.json:31`. The required `e2e-flows` job then runs them without any change under `.github/`, and the step it runs, `npm run test:e2e:ci:flows`, stays byte-identical to what `scripts/ci-required-steps.json:55` expects. The list stays explicit and is sorted alphabetically.

Add one guard test, `W-17`, to `scripts/__tests__/ciWiring.test.ts`. It fails when a root-level spec is not reachable from `ci-cd.yml`, so this defect cannot come back silently.

Update `docs/ops/ci.md` so it lists the same ten specs as `package.json`, records the rate-limit budget, and names the guard.

No spec file, support file, workflow, limiter or timeout changes.

## Slices

1. Add test `W-17` to `scripts/__tests__/ciWiring.test.ts`. It builds two sets and asserts that nothing in the first is missing from the second, naming any missing file:
   - every file directly under `cypress/e2e/` matching `*.cy.ts` or `*.cy.js`, mirroring `cypress.config.ts:20`;
   - every comma-separated path in each `--spec "…"` found in `.github/workflows/ci-cd.yml` or in a `package.json` script that `ci-cd.yml` calls as `npm run <name>`.

   On the current tree it must fail and name exactly the six files.
2. Change `package.json:31` so `test:e2e:ci:flows` names ten literal spec paths in alphabetical order: `activity-complete`, `activity-open`, `auth-invalid`, `auth-login`, `auth-signup`, `dashboard-loads`, `dashboard-progress`, `pathways-consent`, `session`, `student-join`.
3. Update `docs/ops/ci.md`:
   - line 16: make the `e2e-flows` coverage parenthetical cover all ten specs;
   - line 99: list the same ten specs as `package.json:31`;
   - line 101: describe what the ten cover and how the specs share seeded state;
   - add one short paragraph giving the `authLimiter` budget (12 without retries, 19 of 20 in the worst case) and saying that `W-17` requires every root-level spec to be wired into CI.

## Behaviors

1. `npm run test:e2e:ci:flows` runs ten specs: the four it runs today plus `activity-open`, `auth-invalid`, `auth-signup`, `dashboard-loads`, `session` and `student-join`.
2. The `e2e-flows` job reports those ten specs in its Cypress run summary, with no file under `.github/` changed.
3. `build-and-test` still runs exactly `smoke.cy.ts` and `waterworks-mobile.cy.ts`, the same as before.
4. `npm run test:unit` fails, naming the file, when a root-level `cypress/e2e/*.cy.{ts,js}` spec is not referenced by any `--spec` reachable from `ci-cd.yml`.
5. `npm run test:unit` passes on the tree after this change.
6. The spec list in `docs/ops/ci.md` for `test:e2e:ci:flows` is identical to the one in `package.json:31`.

## Acceptance criteria

- `git diff --name-only` lists exactly `scripts/__tests__/ciWiring.test.ts`, `package.json` and `docs/ops/ci.md`.
- The diff touches nothing under `.github/`, `prisma/`, `backend/` or `cypress/`.
- `package.json:31` names ten spec paths, uses no globs, is sorted alphabetically, and each path exists on disk.
- `package.json:30` (`test:e2e:ci`) is unchanged.
- No file under `cypress/` is modified: no `.skip(`, no `.only(`, no retry or timeout changes.
- The delivery includes the real output of `W-17` run twice:
  - with the `package.json:31` change reverted, it fails and names exactly the six files;
  - with the change applied, it passes.
- The `e2e-flows` check on the delivery pull request is green, and its log shows ten specs run and passed. If it is red, the delivery is reported as blocked with the failing spec and cause, and no spec is dropped from the list to get green.
- Step 7 of `docs/ops/ci.md` and `package.json:31` name the same ten specs in the same order.
- `npm run lint`, `npm run typecheck`, `npm run test:unit` and `npm run build` are green, or unchanged from a baseline taken on the untouched tree.

## Decisions

- Chose `package.json:31` over editing `ci-cd.yml`. `.github/` is off limits, the job already takes its spec list from the npm script (`ci-cd.yml:322`), and `scripts/ci-required-steps.json:55` pins that exact run line.
- Put all six in `e2e-flows` rather than `test:e2e:ci`. Each one calls `/api` or needs seeded data, and `build-and-test` has no backend (`ci-cd.yml:110-121`, `docs/ops/ci.md:14`).
- Kept an explicit list rather than removing `--spec` and using the default pattern. The default pattern would also re-run `smoke.cy.ts` and the viewport-heavy `waterworks-mobile.cy.ts`, which `build-and-test` already runs, inside the required job. That doubles their cost and their red signal. `W-17` covers new specs instead.
- Sorted the list alphabetically instead of appending. That matches the order a default-pattern run (#671's 14/14) would use, so the result does not depend on whether Cypress keeps command-line order.
- Did not delete or quarantine the six specs. They cover flows P-02, P-04, P-05, P-07, P-08 and P-11, which no CI spec covers today, and nothing shows them to be broken. Deleting them would fall under the never-list's "do not delete a failing test".
- Did not propose a separate extended or on-demand job. It would need `.github/`, and `docs/ops/ci.md:85` (rule 5) warns against checks that run on every PR without gating.
- Did not add a rate-limit relax flag or raise any limit. That would loosen a gate, and the worst-case budget (19 of 20) already fits.
- Put the guard in the existing `ciWiring.test.ts` rather than a new file. `touched_paths` may only list paths that exist, and this file is already the CI wiring guard and already reads `ci-cd.yml` (W-6, `:126-129`).
- Named the test `W-17`. `W-12` through `W-16` are in use (grep across the repository), and `W-17` is not.
- The guard reads only `ci-cd.yml`, not `cypress-staging.yml`. The staging workflow runs on label or dispatch against staging secrets, and its one spec path is already known to be stale (`docs/ops/ci.md:143`).
- The guard checks one direction only (every root spec is referenced). It does not check that referenced paths exist, because this finding is about specs that never run. `W-2` (`:84-92`) already checks existence for `test:e2e:ci`.
- Left the "Last verified against code: 2026-08-25" header in `docs/ops/ci.md` alone. Changing the date would claim the whole document was re-checked, and this change edits one section.
- Left the stale `authLimiter` comment at `student-join.cy.ts:5-6` alone. Fixing it would mean editing a spec for a comment, outside this package's scope. It is listed as an open question instead.

## Open questions

- I did not run any gate or Cypress spec while writing this proposal; the proposal step has only read access. `gate_expectation: green` is an expectation, not a measurement. `ciWiring.test.ts` W-8/W-9 (`:150-192`) start a real Cypress shell gate on `:5173`, so the `test:unit` baseline depends on the environment and should be taken on the untouched tree before slice 1.
- Do the six specs pass against today's stack? The last recorded run is #671's local 14/14 on 2026-08-07, before #874 and #875. I spot-checked their asserted strings and test ids in `src/` (`src/locales/en/common.json:147,226,230,239,365,2080,2105`; `TeacherNavbar.tsx:55`; `BottomNav.tsx:16`; `K2InstantFeedbackQuiz.tsx:136`; `QuestionScreen.tsx:77`) and all still exist, but that does not prove the specs pass. If one fails because the spec has aged, fixing it means editing a file outside `touched_paths`. Should that come as `/harness revise` adding the spec, or should that spec be dropped from the list by a person's decision?
- Owner #774 accepted a job of about 3.6 minutes (`docs/ops/ci.md:56`). Is the added time, estimated at one to three minutes and not measured, acceptable for every PR?
- Does Cypress 13 keep the command-line order of a comma-separated `--spec` list? I did not check. The alphabetical list makes the answer irrelevant here, but it matters for anyone who reorders the list later.
- Should `student-join.cy.ts:5-6` (the stale `authLimiter` comment) and `cypress-staging.yml:36` (the missing `pilot-smoke.cy.ts` path) get follow-up work items? The second needs a person, since it is under `.github/`.

## Touched paths

- scripts/__tests__/ciWiring.test.ts
- package.json
- docs/ops/ci.md

## Risks

- **Blocking every PR.** `e2e-flows` is required on every PR (`docs/ops/ci.md:36-40`). A flaky or failing test in any of the six will block all PRs once this merges, not just this one. `retries.runMode: 1` absorbs one flake per test. The reviewer should read the delivery's `e2e-flows` log, not just its green tick.
- **Almost no rate-limit headroom.** The worst case is 19 of 20 `authLimiter` requests in one 15-minute window. The next spec that logs in as a teacher, combined with a run that retries heavily, could get a 429 on a valid login. Slice 3 documents the budget so the next person adding a spec sees it.
- **Unmeasured duration.** The job has `timeout-minutes: 15` (`ci-cd.yml:214`), and the added time has not been measured. If the job runs out of time, the item is blocked. The timeout will not be raised.
- **Specs sharing seeded state.** `dashboard-loads` and `dashboard-progress` both run `e2e:reset`, which deletes and re-creates every E2E row (`scripts/e2e-seed.mjs:352-417`). That makes the `CYPRESS_STUDENT_ID` and `CYPRESS_LESSON_ID` values exported at `ci-cd.yml:278-308` stale for later specs. By reading the code, the later specs cope:
  - `student-join` E3-01 only checks that the ids are non-empty (`:14-24`), and its session test re-reads the id from the live roster (`:72-73`);
  - `activity-open` and `activity-complete` look up lesson and activity ids live (`activity-open.cy.ts:68-78`; `activity-complete.cy.ts:74-78`).

  Two more points for the reviewer: `activity-open` runs after `activity-complete` has left Student One's quiz completed, and `pathways-consent` runs after two resets.
- **Stale seeded env carried forward.** `auth-signup` creates a new `teacher+e1-<timestamp>@e2e.invalid` account on every run, and `resetE2E` does not remove it. That is harmless on CI's throwaway database but piles up in a long-lived local test database. The spec already does this; this change does not alter it.
- **Guard regex.** `W-17` parses `--spec "…"` with a regex over `ci-cd.yml` and the scripts. Check that it handles the `\"` escapes in `package.json` after `JSON.parse`, and that a spec named in a YAML comment is not counted as run.
- **Issue body.** The issue body contains no instructions aimed at an agent. The `/harness …` command table in it is the harness's own steering footer for trusted people on the harness repository, and I did not act on it.
