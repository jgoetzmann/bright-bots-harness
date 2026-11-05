---
issue: 104
upstream_issue: null
title: "test(e2e): remove pilot-smoke fossil and correct docs on its unrunnable staging workflow"
kind: test
slices: 2
risk: low
touched_paths:
  - "cypress/e2e/legacy/pilot-smoke.cy.ts"
  - "docs/ops/ci.md"
  - "docs/staging.md"
depends_on: []
estimated_turns: 15
gate_expectation: green
baseline_red: []
---

# test(e2e): remove pilot-smoke fossil and correct docs on its unrunnable staging workflow

## Issue

Harness issue #104 tracks finding 10 of audit jgoetzmann/bright-bots-harness#61 (`audit:61:10`). There is no product issue. The finding's title says `pilot-smoke` can never reach an assertion, and its workflow targets a path that does not exist. The reporter lists these anchors: `return`, `ALLOW_DEV_HEADERS === "1"`, `CYPRESS_ALLOW_DEV_HEADERS: "0"`, `cypress/e2e/pilot-smoke.cy.ts` and `legacy/`. Only the last two are paths. The first three point at code inside the spec and the workflow.

## Diagnosis

There are three separate defects. Together they mean no assertion in this spec is ever reached and passed.

**1. The workflow names a file that does not exist.** `.github/workflows/cypress-staging.yml:36` runs `npx cypress run --config-file cypress.config.ts --spec "cypress/e2e/pilot-smoke.cy.ts"`. That file is not in the tree. The only `pilot-smoke` spec is `cypress/e2e/legacy/pilot-smoke.cy.ts`. So when the job is triggered (by the `pilot-smoke` label at line 14, or by `workflow_dispatch`), there is no spec for it to run, and no test is reached.

The docs get this wrong. `docs/ops/ci.md:161` says the workflow "still targets fossil `cypress/e2e/legacy/pilot-smoke.cy.ts`", but the workflow has no `legacy/` segment in its path. `docs/staging.md:22` says "The `cypress-staging.yml` workflow can run against the production Railway URL." Neither is true.

On pull requests from forks, where secrets are not passed, the run stops even earlier: `cypress.config.ts:10-14` throws when `CYPRESS_SWA_URL` is empty. That failure is correct and loud, not a defect.

**2. The spec's optional test passes without asserting anything under the workflow's own settings.**
- The workflow pins `CYPRESS_ALLOW_DEV_HEADERS: "0"` (`cypress-staging.yml:41`), and `cypress.config.ts:34` passes that value through.
- At `cypress/e2e/legacy/pilot-smoke.cy.ts:29-33` the test checks `String(Cypress.env("ALLOW_DEV_HEADERS")) === "1"`. When that is false it calls `cy.log(...)` and `return;`. Cypress reports a test that returns early as **passed**.
- So even if the path were corrected, the assertions at lines 42 and 56 would never run, and the test would still show green.
- That breaks the repository's own honesty rule at `docs/ops/ci.md:142-146`: a deliberately disabled optional feature must call `this.skip()`, and must never pass silently.
- The maintained replacement does this correctly: `cypress/e2e/staging/smoke.cy.ts:30-35` calls `this.skip()`.

**3. With the flag on, the same test cannot pass at all.**
- Headers: it sends `x-dev-student-id` and `x-dev-lesson-id` (`pilot-smoke.cy.ts:47-50`). Nothing in `backend/src` reads those headers. The dev shim only reads `x-role` and `x-user-id`, and only when `ALLOW_DEV_ROLE_HEADER === "1"` under `NODE_ENV` development or test (`backend/src/utils/auth.ts:52-53, 67-71`).
- Auth: the route is protected by `requireAuth` (`backend/src/routes/progress.ts:683-686`), which returns 401 when there is no user (`auth.ts:87-90`).
- Body: the request body `{ checkpoint: "intro", status: "complete" }` (line 51) would fail `checkpointSchema` anyway. That schema requires `studentId`, `moduleSlug`, `lessonId`, `activityId` and `timeSpentS` (`backend/src/validation/schemas.ts:59-69`).
- Result: the check `be.oneOf [200, 201, 204]` (line 56) cannot succeed. The checkpoint contract this test was written against never shipped in that shape.

**What remains of the spec duplicates the maintained smoke.**
- `pilot-smoke.cy.ts:7-18` (GET `stem-1` returns a slug) is the same as `cypress/e2e/staging/smoke.cy.ts:14-26`, except the maintained copy uses `requireEnv`.
- `pilot-smoke.cy.ts:20-26` (`/student` renders) matches `staging/smoke.cy.ts:4-12`.

**Where the real fix lives.** The defect that matters is one line in a file under `.github/`, which the harness never publishes. The rest is a fossil spec and two docs that misdescribe the job. Both of those can be fixed without touching CI.

**Not verified: may affect the follow-up.** `cypress.config.ts:20` sets `specPattern: "cypress/e2e/*.cy.{ts,js}"`, which matches only one directory level. I recall that Cypress 10 and later intersect `--spec` matches with `specPattern`. If 13.17.0 (pinned in `package-lock.json`) does that, then a spec under `legacy/` or `staging/` is never found through `--spec`. I could not check this: there is no Cypress install or binary cache in this environment. See Open questions.

## Approach

The delivery diff fixes what is in reach, and hands the workflow line to a person with the exact change.

1. **Delete `cypress/e2e/legacy/pilot-smoke.cy.ts`.** Removing a fossil is allowed by `docs/ops/ci.md:220` ("it does not forbid removing a single fossil whose feature never shipped in the shape it tests"). Its only test not covered elsewhere checks a checkpoint contract that never shipped (defect 3), and its other two tests are covered by `cypress/e2e/staging/smoke.cy.ts`.
   - Deleting the file also closes a trap. The obvious quick fix for the workflow is to insert `legacy/` into its path. That would bring back a job whose third test reports green without asserting anything (defect 2).
   - The deletion is recorded in the "Removed fossils" list (`docs/ops/ci.md:224-227`), with the evidence, following the existing `classRoster.cy.ts` entry.
2. **Correct the docs.**
   - Rewrite `docs/ops/ci.md:161` to say what the workflow actually runs: `cypress/e2e/pilot-smoke.cy.ts`, which does not exist, so the job has no spec to run. It should also say that its former target was removed, and that re-pointing the job is still a follow-up under `.github/`.
   - Rewrite the "Cypress Staging Smoke" section in `docs/staging.md:20-30` so it no longer says the workflow can run. It should point at the maintained smoke via a relative link to `staging-smoke.md`, a file that exists. The secrets table stays, because those are still the secrets the workflow reads.
   - Update the `Last verified` marker on `docs/ops/ci.md:1`, following the existing per-section dating style.
3. **Hand the workflow line to a person (not part of this delivery's diff).** The recommended change to `.github/workflows/cypress-staging.yml:36` is:
   `-        run: npx cypress run --config-file cypress.config.ts --spec "cypress/e2e/pilot-smoke.cy.ts"`
   `+        run: npm run test:e2e:staging`
   - `CYPRESS_ALLOW_DEV_HEADERS: "0"` at line 41 can stay. In the staging smoke it causes `this.skip()`, so the test shows as pending, which is honest.
   - Before relying on this change, check the specPattern question in Open questions.

Nothing in the delivery changes how any gate or CI job behaves. The deleted file is not run by any required job (`docs/ops/ci.md:220`), and the one workflow that names a `pilot-smoke` spec already names a path that does not exist.

## Slices

1. Delete `cypress/e2e/legacy/pilot-smoke.cy.ts`, and add an entry with the defect-2 and defect-3 evidence to the "Removed fossils" list in `docs/ops/ci.md` (Legacy fossils section, lines 224-227).
2. Correct what the docs say about `.github/workflows/cypress-staging.yml`: rewrite the workflow note at `docs/ops/ci.md:161`, rewrite the "Cypress Staging Smoke" section at `docs/staging.md:20-30`, and update the `Last verified` marker at `docs/ops/ci.md:1`.

## Behaviors

1. `cypress/e2e/legacy/pilot-smoke.cy.ts` is gone, and `npm run test:e2e:legacy`'s glob `cypress/e2e/legacy/**/*.cy.{ts,js}` no longer matches any `pilot-smoke` file.
2. No file in the repository refers to `cypress/e2e/legacy/pilot-smoke.cy.ts`, except the "Removed fossils" entry in `docs/ops/ci.md`.
3. The workflow note in `docs/ops/ci.md` names `cypress/e2e/pilot-smoke.cy.ts`, the path `cypress-staging.yml:36` actually runs, and says that path does not exist.
4. `docs/staging.md` no longer says `cypress-staging.yml` can run against production, and links to `staging-smoke.md` for the maintained staging smoke.
5. `npm run lint`, `npm run typecheck`, `npm run test:unit` and `npm run build` finish with the same result as on the untouched tree.
6. `npm run docs:check` reports no new DC-002 (broken link) or DC-004 (missing `Last verified`) finding for `docs/ops/ci.md` or `docs/staging.md`.

## Acceptance criteria

- `git diff --stat` against the base shows exactly three paths: `cypress/e2e/legacy/pilot-smoke.cy.ts` (deleted), `docs/ops/ci.md` and `docs/staging.md`.
- No path under `.github/` appears in the diff.
- Searching the tree for `legacy/pilot-smoke` finds only the "Removed fossils" entry in `docs/ops/ci.md`.
- The "Removed fossils" entry cites the early `return` under `ALLOW_DEV_HEADERS`, the workflow's `"0"` pin, and the unread `x-dev-*` headers.
- The rewritten `docs/ops/ci.md` workflow note contains the literal string `cypress/e2e/pilot-smoke.cy.ts` and states that the path does not exist.
- The rewritten section in `docs/staging.md` contains no claim that the workflow can run, and contains a relative link to `staging-smoke.md` that resolves.
- `docs/ops/ci.md:1` still starts with `> **Canonical for:** CI jobs and parity.` and still contains `Last verified`.
- `npm run test:unit` is green. No test file in the diff gains `.skip(` or `.only(`, and no test file is changed at all.
- The only formatting applied is to `docs/ops/ci.md` and `docs/staging.md`. No whole-tree formatter was run.

## Decisions

- Delete the fossil rather than repair it (for example, swapping the early `return` for `this.skip()`). `docs/ops/ci.md:220` forbids repairing a fossil to make it pass, and a repair would still leave the checkpoint test unpassable with the flag on (defect 3).
- Delete the fossil rather than leave it quarantined. Leaving it keeps the trap where inserting `legacy/` into the workflow path, the most obvious one-token fix, brings back a silent green on the third test.
- Do not create `cypress/e2e/pilot-smoke.cy.ts` to match the workflow's path. A new top-level spec would match the default `specPattern` (`cypress.config.ts:20`), so a staging-only spec would run under plain `npm run test:e2e`. It would also shape the product tree around a stale CI file, and the new path could not be declared under Touched paths.
- Do not widen `specPattern` in `cypress.config.ts`. That would pull every legacy fossil and the staging smoke into `npm run test:e2e` (contradicting `docs/ops/ci.md:214`), and it fixes a problem that is itself unverified (see Open questions).
- Do not add a unit guard to `scripts/__tests__/ciWiring.test.ts` asserting that `cypress-staging.yml`'s `--spec` path exists. It would be red on arrival, because this package cannot fix the workflow, and a red test cannot ship without a skip, which the never-list forbids. It becomes a good guard once a person has carried the workflow line, so it belongs to that follow-up.
- Hand the `.github/workflows/cypress-staging.yml:36` change to a person, with the exact two-line diff under Approach, rather than drop it. The harness never publishes under `.github/`, and leaving it out entirely would hide the actual defect.
- Include `docs/staging.md` even though it brings a prettier reformat of its unaligned table. Its line 22 makes a false claim about this exact workflow, and correcting it is worth the formatting noise.
- Keep the secrets table in `docs/staging.md` rather than remove it. Those are still the secrets `cypress-staging.yml:31-43` reads, and they will be needed once the job is re-pointed.
- Do not edit `docs/staging-smoke.md`, even though its line 27 ("otherwise defaults to `http://localhost:5173`") contradicts `cypress.config.ts:10-14`. It is a real staleness, but it is unrelated to this finding, so it is listed under Open questions rather than fixed here.
- Do not change any claim that `npm run test:e2e:staging` works. Whether it finds its spec is unverified, so the new text points at the maintained smoke and its doc rather than asserting it runs.
- Set `upstream_issue` to `null`, because the product issue is "none".
- Use `kind: test` and type `test(e2e)`. The substantive change is removing a test that passes without asserting; the docs edits record that removal and correct claims about the same job.

## Open questions

- Will a maintainer carry the one-line change to `.github/workflows/cypress-staging.yml:36`? Should this delivery land first or wait for it? Landing first does not change how the workflow behaves, because its target path is already missing.
- Does Cypress 13.17.0 intersect `--spec` matches with `e2e.specPattern` (`cypress.config.ts:20`)? I could not verify this here: there is no Cypress binary in this environment. If it does, then `npm run test:e2e:staging` and `npm run test:e2e:legacy` also find no specs, the recommended workflow re-point would fail the same way, and `docs/staging-smoke.md` and `docs/ops/ci.md:159` are wrong too. That would be its own work item. Running `CYPRESS_SWA_URL=… VITE_API_BASE=… npm run test:e2e:staging` once, or reading upstream `packages/data-context/src/sources/ProjectDataSource.ts` at `v13.17.0`, settles it.
- When the job is re-pointed, should the `pilot-smoke` label trigger (`cypress-staging.yml:14`, also named in `docs/team-workflow.md:34`) be renamed or kept? This is a maintainer's choice.
- Should the stale localhost-fallback claim at `docs/staging-smoke.md:27` become its own work item?

## Touched paths

- cypress/e2e/legacy/pilot-smoke.cy.ts
- docs/ops/ci.md
- docs/staging.md

## Risks

- **The delivery does not close the finding by itself.** The workflow stays broken until a person changes `.github/workflows/cypress-staging.yml:36`. A reviewer should not read a merged delivery as "the staging smoke job works now". The rewritten docs note says so explicitly.
- **Lost coverage for `test:e2e:legacy` runners.** Anyone running that script locally loses this spec's two GET/render tests. Both are duplicated in `cypress/e2e/staging/smoke.cy.ts:4-26` with stricter env handling. Check that duplication claim against the two files.
- **Formatting noise.** Touching `docs/staging.md` will make prettier realign its secrets table (lines 25-30). The diff will show that as formatting change. Review it as formatting, not content.
- **Gates were not run.** I did not run any gate while writing this proposal: the environment has no shell and no `node_modules`. `green` is an expectation, not an observation. It rests on the change being one spec deletion (nothing imports or references it: a search for `pilot-smoke` across code and tests found only docs and the workflow) and two Markdown edits.
- **The root `typecheck` gate does not cover this deletion.** If the root `tsc --noEmit` excludes `cypress/` (the #677 notes at `prompts/2026-07-15-ticket-677-honest-ci-cypress-gate.md:46` say `npm run typecheck` does not cover `cypress/`), then that gate says nothing about it. The text search above is the real evidence.
- **The doc wording must stay accurate where facts are unverified.** Reviewers should check that the new text does not claim `npm run test:e2e:staging` runs, and does not claim what exit code Cypress returns when no spec is found. Neither is verified.
- **Issue body.** It contains no instructions aimed at an agent. It does carry the harness's command table and links, which I treated as data and ignored. Its "Paths" list mixes real paths with code fragments (`return`, `ALLOW_DEV_HEADERS === "1"`, `CYPRESS_ALLOW_DEV_HEADERS: "0"`); I read those as anchors into `pilot-smoke.cy.ts:29-33` and `cypress-staging.yml:41`, not as files.
