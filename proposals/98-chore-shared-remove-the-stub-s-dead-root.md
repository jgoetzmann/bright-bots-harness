---
issue: 98
upstream_issue: null
title: "chore(shared): remove the stub's dead #root attribute and its source-only tests"
kind: chore
slices: 3
risk: low
touched_paths:
  - "src/main.tsx"
  - "shared/greatwork-engine/index.test.ts"
  - "backend/src/__tests__/sharedEngineProbe.test.ts"
depends_on: []
estimated_turns: 15
gate_expectation: green
baseline_red: []
---

# chore(shared): remove the stub's dead #root attribute and its source-only tests

## Issue

Harness issue #98 tracks `audit:64:13`, which is finding 13 of the audit in harness issue #64 (https://github.com/jgoetzmann/bright-bots-harness/issues/64). There is no product-repository issue. The finding is titled "Tests for a behaviourless stub, with production code bent to keep the stub alive", severity medium.

The **Paths** line of the body is garbled. Its backtick-delimited fragments are broken, and only `shared/greatwork-engine/index.ts` survives intact. The remaining text still shows the claim: two test files assert the resulting literal, and something "writes a dead ... attribute onto" something. The diagnosis below rebuilds the specific files from the source rather than from the damaged path list.

## Diagnosis

**The stub has no real behaviour.**
- `shared/greatwork-engine/types.ts:6` calls itself a "Structural stub for the #730 shared-engine build spike".
- `shared/greatwork-engine/index.ts:4` exports the constant `"greatwork-engine-stub-730"`.
- `index.ts:9-11` builds the string `` `${meta.id}@${meta.version}` `` and does nothing else.
- The design doc plans to replace `index.ts` with the real public API (`runMachine`, `runMachineTraced`) and put tests under `__tests__/` (`docs/games/the-great-work-design.md:905-915`).

**Production code is bent to keep the stub in the browser bundle (the dead attribute).**
- `src/main.tsx:6-9` imports the stub.
- `src/main.tsx:11-12` states the reason: "Keep the shared stub in the production bundle (W-01 / #730). Assign to a live DOM property so Rollup cannot DCE the import."
- `src/main.tsx:13-19` sets `rootEl.dataset.greatWorkEngine`. Every page load in every student's browser therefore writes `data-great-work-engine="greatwork-engine-stub-730@0.0.0"` onto `#root`.
- Nothing reads it. A repo-wide grep for `greatWorkEngine`, `great-work-engine` and `stub-730` finds only this write, the tests below, `backend/src/sharedEngineProbe.ts`, and prose in `docs/` and `prompts/`. No match turns up in `cypress/`, `scripts/` or any reader in `src/`.

**That reason no longer applies.**
- The W-01 proof was a one-off manual bundle grep recorded in the spike report (`docs/spikes/730-shared-engine.md:89-93`). No script or guard greps the bundle for the stub id.
- The `@shared/*` alias is now used by real production imports:
  - `src/constants/stemSets.ts:15,20,29`
  - `src/services/api.ts:2`
  - `src/lib/specialty.ts:6`
  - `src/lib/moduleAccess.ts:1`
- So `vite build` and `tsc --noEmit` (`tsconfig.json:26`, `vitest.config.ts:11`) exercise the alias without the stub.
- The handoff doc already records this block as needing a decision: "the `dataset.greatWorkEngine` DCE-defeat scaffold also needs an explicit keep-or-remove decision" (`docs/context/branch-status-2026-08-14.md:24`).

**Tests that assert the stub's literal.** Three files assert it, not two, and only one of them can catch anything.
- `shared/greatwork-engine/index.test.ts`:
  - Lines 8-10 assert that the constant equals the literal it is defined as (`index.ts:4`).
  - Lines 12-19 assert the one-line join at `index.ts:10`.
  - Its only other effect is resolving the `@shared` alias under Vitest. `src/constants/__tests__/stemSets.test.ts:10` and `src/constants/__tests__/stemSetIdsSeedParity.test.ts:22` already do that.
- `backend/src/__tests__/sharedEngineProbe.test.ts`:
  - Its own docstring (lines 4-9) says it "deliberately does NOT prove runtime/emit resolution" and "cannot detect the depth-fragility defect".
  - The spike's falsification run F3 (`docs/spikes/730-shared-engine.md:353`) recorded it staying green with the real defect in place.
  - Line 12 asserts `"greatwork-engine-stub-730@0.0.0"`.
- `backend/src/__tests__/sharedEngineProbe.emit.test.ts` is the one with teeth:
  - It rebuilds `shared/dist` from source (lines 61-65) and compiles the probe to its real depth (lines 67-86).
  - Line 103 asserts the same label (line 6) from the emitted artifact.
  - Its negative twin (lines 106-141) fails on the old relative specifier. That is the regression test the design doc cites at `docs/games/the-great-work-design.md:1437`.
  - Any change to `index.ts` that would turn either of the first two tests red also turns this test's phase 1 (line 94) red. The first two tests therefore add no detection.

**Where the finding over-reaches.** The backend half of the scaffold still has a job:
- `backend/src/sharedEngineProbe.ts:7-10` builds the probe label.
- `backend/src/server.ts:40` and `backend/src/server.ts:219-221` import it at boot and return it from `/health`.

The spike made `/health` deliberately load-bearing (`docs/spikes/730-shared-engine.md:141`, `:155-166`). It is the only runtime consumer of the package `main` entry (`shared/package.json:5-6`), and it is what the emit regression test compiles. The frontend write has no remaining job; the backend probe does.

## Approach

Remove what has no remaining job and keep what still catches a real defect:

1. Delete the stub import and the `dataset` write from `src/main.tsx` (lines 6-9 and 11-19, plus the blank separator line 10). Lines 1-5 and 21-31 stay byte-identical.
2. Delete `backend/src/__tests__/sharedEngineProbe.test.ts`. The emit test covers everything it covers, as its own docstring says.
3. Delete `shared/greatwork-engine/index.test.ts`. Its value assertions are covered by the emit test, and its alias resolution is covered by the `@shared/progression` tests.

Files deliberately left alone:
- `shared/greatwork-engine/index.ts` and `types.ts` stay, because they are the backend package `main`.
- `backend/src/sharedEngineProbe.ts`, the `/health` field in `backend/src/server.ts`, and `sharedEngineProbe.emit.test.ts` stay.
- No docs change.

Only Prettier on `src/main.tsx` is needed; the two deletions need no formatting.

## Slices

1. Remove the `@shared/greatwork-engine` import and the `rootEl.dataset.greatWorkEngine` block (lines 6-19) from `src/main.tsx`, leaving the analytics init and render path unchanged.
2. Delete `backend/src/__tests__/sharedEngineProbe.test.ts`, the source-only probe test that `sharedEngineProbe.emit.test.ts` strictly covers.
3. Delete `shared/greatwork-engine/index.test.ts`, whose assertions only restate the stub's own literals.

## Behaviors

1. After the app loads, the `#root` element has no `data-great-work-engine` attribute.
2. The production bundle from `npm run build` no longer contains the string `greatwork-engine-stub-730`.
3. The app still initialises analytics and renders `<App />` inside `#root` as before.
4. `GET /health` on the backend still returns `{"status":"ok","sharedEngine":"greatwork-engine-stub-730@0.0.0"}`.
5. All three cases in `sharedEngineProbe.emit.test.ts` still pass, and its negative twin still fails on the old relative specifier.
6. `npm run test:unit` collects exactly two fewer test files and three fewer tests than on the untouched tree, with every remaining test unchanged.

## Acceptance criteria

- `src/main.tsx` contains no import from `@shared/greatwork-engine` and no `dataset` assignment, and its remaining lines match the original lines 1-5 and 21-31.
- `shared/greatwork-engine/index.test.ts` and `backend/src/__tests__/sharedEngineProbe.test.ts` no longer exist.
- The diff touches exactly the three paths under `## Touched paths` and no other file.
- `shared/greatwork-engine/index.ts`, `shared/greatwork-engine/types.ts`, `shared/package.json`, `shared/tsconfig.json`, `backend/src/sharedEngineProbe.ts`, `backend/src/server.ts` and `backend/src/__tests__/sharedEngineProbe.emit.test.ts` are unchanged.
- `grep -rn "greatWorkEngine\|great-work-engine" src cypress scripts` returns no matches.
- After `npm run build`, `grep -rl "greatwork-engine-stub-730" dist/assets` returns no matches.
- `npm run lint`, `npm run typecheck`, `backend: npm run typecheck`, `npm run test:unit` and `npm run build` are green, and the delivery reports their actual output.
- Every commit message passes `@commitlint/config-conventional`, including the 100-character header and body-line limits.

## Decisions

- **Remove the frontend write now rather than leaving it to #720, as `docs/context/branch-status-2026-08-14.md:24` suggests.** Its stated purpose (`src/main.tsx:11-12`) is already met by real `@shared/progression` imports. It runs on every page load, and nothing reads it. Deferring leaves dead production code whose removal has no scheduled owner.
- **Keep the backend probe, the `/health` field and the emit test.** Removing the whole scaffold was rejected. It would delete the only regression test for the emit-depth `MODULE_NOT_FOUND` defect (`docs/games/the-great-work-design.md:1437`). It would change the public `/health` response shape, and it would leave `shared/package.json` `main` pointing at a file nothing imports. The real engine work (#720) replaces that entry point anyway.
- **Delete `sharedEngineProbe.test.ts` rather than rewrite it.** There is nothing meaningful to assert about a stub label from source, and its own docstring says the emit test owns the property it cannot see.
- **Delete `index.test.ts` rather than loosen it** (for example to `toContain("@")`). Any rewrite would still test a placeholder that #720 replaces with `runMachine`. Keeping it as an alias check was also rejected, because the `@shared/progression` tests already cover alias resolution in Vitest.
- **Keep `shared/greatwork-engine/index.ts` and `types.ts` unchanged.** They are the package `main`/`types` (`shared/package.json:5-6`) that the backend probe and emit test load.
- **Leave the `greatwork-engine/**/*.test.ts` exclude in `shared/tsconfig.json:15`,** even though it will match nothing for now. The planned `__tests__/` files (`docs/games/the-great-work-design.md:914`) need it, and removing then re-adding it is churn.
- **Edit no docs.**
  - `docs/spikes/730-shared-engine.md` is dated evidence of what the spike ran, and editing it would rewrite history.
  - `docs/context/branch-status-2026-08-14.md` is a dated snapshot that defers to later PRs (line 3).
  - `docs/architecture/shared-code.md` makes no claim about `src/main.tsx` or the deleted tests, and its "Stub + future simulation engine" row (line 37) is still true.
- **Use kind `chore` rather than `fix` or `test`.** No user-visible behaviour is broken. The primary change removes spike scaffolding from production code, and the test deletions follow from it.
- **Use three slices rather than one,** so a reviewer can accept the production change and reject either test deletion on its own.

## Open questions

- Should the backend half of the scaffold (the `/health` `sharedEngine` field and `backend/src/sharedEngineProbe.ts`) also go now, or stay until #720 replaces the stub? This plan keeps it, which means the emit regression test also stays.
- Does anything outside this repository read `data-great-work-engine` from production `#root`, such as a manual smoke checklist, a monitoring probe or a QA habit? No reader exists in the repository, but outside readers cannot be seen from here.
- Does the maintainer accept making the keep-or-remove decision here, instead of in #720 as `docs/context/branch-status-2026-08-14.md:24` records it?

## Touched paths

- src/main.tsx
- shared/greatwork-engine/index.test.ts
- backend/src/__tests__/sharedEngineProbe.test.ts

## Risks

- **Gates were not run on the untouched tree.** I did not run any gate while preparing this proposal. `gate_expectation: green` is a prediction from reading the source, not an observed baseline. The delivery must report the real results.
- **Deleting two passing tests lowers the unit-test count.** The reviewer should check that the emit test covers everything the deleted tests could catch: `sharedEngineProbe.emit.test.ts:61-65` rebuilds `shared/dist` from source and line 103 asserts the same label. The reviewer should also check that no guard counts unit tests or lists these files. I found none in `scripts/` or `docs/ops/`.
- **The frontend loses its only import of `shared/greatwork-engine`.** Afterwards only the `@shared/progression/*` imports exercise the `@shared` alias in the frontend. The alias maps the whole directory (`vite.config.ts`, `vitest.config.ts:11`, `tsconfig.json:26`), so it does not depend on which subdirectory is imported. #720 will reintroduce a frontend import of the engine.
- **Other scripts that touch `src/main.tsx`.** `scripts/verify-ci-shell-gate.sh:244-247` prepends a line to a sandbox copy, and `scripts/__tests__/ciWiring.test.ts:150-190` only watches for writes to the file. Neither depends on the removed lines.
- **Out of scope.** Nothing here touches `prisma/`, migrations, predeploy scripts, `.github/`, or any env file.
- **The issue body.** Its path list is mangled, so the specific files were rebuilt from the source; the reviewer should confirm they match what the auditor meant. The body contains no instructions aimed at the agent. The command table and links are the harness's own boilerplate and were treated as data.
