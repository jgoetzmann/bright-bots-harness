---
issue: 99
upstream_issue: null
title: "test: replace four assertions that cannot fail with falsifiable ones"
kind: test
slices: 4
risk: low
touched_paths:
  - "scripts/__tests__/guard-sandbox-isolation.test.ts"
  - "src/components/activities/quiz/__tests__/types.test.ts"
  - "src/components/games/__tests__/RegistrySet2Coverage.test.ts"
  - "backend/tests/security.test.ts"
depends_on: []
estimated_turns: 30
gate_expectation: green
baseline_red: []
---

# test: replace four assertions that cannot fail with falsifiable ones

## Issue

Harness issue #99 tracks finding 14 of audit jgoetzmann/bright-bots-harness#64 (`audit:64:14`), severity medium. There is no product issue. The finding is titled "A cluster of assertions that cannot fail." Its `Paths` field lists identifiers rather than file paths: `matrix.length`, `RegistrySet2Coverage`, `toBeDefined()`, `types.test.ts`, `satisfies`, `npm run typecheck`, `GET /health`, `200`. I used these as search terms. They point to four assertions in four test files.

## Diagnosis

**1. `matrix.length` is compared with itself.** In `scripts/__tests__/guard-sandbox-isolation.test.ts:729-733` the matrix is built as `baseInputs` plus `hardLinkInputs` if `hardlinked` plus `symLinkInputs` if `links.available`. Lines 760-767 then check that `matrix.length` equals `baseInputs.length + (hardlinked ? hardLinkInputs.length : 0) + (links.available ? symLinkInputs.length : 0)`. That is the same sum, using the same two flags, so the check always passes.

The comment at 746-747 promises that "the matrix must not silently shrink". The per-group checks at 750-758 only fix the sizes of the three array literals. Nothing stops the matrix shrinking because a capability probe failed:
- `trySymlinks` (294-311) turns any error into `{ available: false }`.
- `tryHardlink` (314-321) turns any error into `false`.

If either probe fails on the Linux runner, 4 or 1 escape cases drop out and the test still passes. The separate check at 1284-1298 does not cover this. It tests a different probe, `SYMLINKS_AVAILABLE` (263-277), run in its own temp directory, not the `planted` directory the matrix uses.

**2. `types.test.ts` checks nothing at runtime or at compile time.**
- The only real check in `src/components/activities/quiz/__tests__/types.test.ts` is `satisfies QuizQuestion`, at lines 15 and 29. Vitest strips types without checking them.
- `npm run typecheck` is `tsc --noEmit` (`package.json:44`). It compiles the root `tsconfig.json`, which excludes `**/__tests__/**` and `**/*.test.ts` (`tsconfig.json:35-36`). So this file is never type-checked.
- The runtime assertions read back values the test has just written: `expect(legacy.id).toBe("q1")` (16) and `expect(i18nKey.prompt).toEqual({ i18nKey: … })` (30). Neither can fail.
- The repo already documents this trap in `src/components/games/dataDashAttrsExhaustiveness.ts:4-7`: "an assertion placed in the test file is never seen by tsc --noEmit and silently does nothing."
- The runtime behaviour its E-10 case refers to (an i18nKey-shaped question renders) is already tested with a real render in `src/components/activities/quiz/__tests__/QuestionScreen.test.tsx:89-107`, under the same `covered-by AC-6.2` tag.

**3. `RegistrySet2Coverage` can fail, but only one way.** Here the finding overstates. `GAME_COMPONENTS` is a `Record<string, ComponentType<GameProps>>` (`src/components/games/gameRegistry.ts:31`). Removing one of the seven keys makes `toBeDefined()` see `undefined` and fail (`src/components/games/__tests__/RegistrySet2Coverage.test.ts:6-12`). `null` is not a practical risk: root `strict: true` (`tsconfig.json:18`) rejects it in `gameRegistry.ts`, which is type-checked. What the test cannot catch is a key wired to the wrong component, for example `sky_shield: FastLaneGame`. The repo already checks registry wiring by identity in `src/components/games/__tests__/BounceBuds.test.ts:38-40`.

**4. `GET /health` → `200` proves nothing about rate limiting.** `backend/tests/security.test.ts:36-39` is named "should NOT enforce rate limiting on non-API routes". It sends one request and checks for `200`. The limiter allows 1000 requests per 15 minutes (`backend/src/server.ts:114-120`), so one request returns 200 whether the limiter is mounted at `/api` (123) or on the whole app. The test cannot fail on the property it names.

The limiter sets `standardHeaders: true` (117), so every request it handles gets `RateLimit-*` response headers, whether or not the limit is reached. Those headers are the observable sign of whether the limiter touched a request. The file already calls `/api/modules` (44), which passes through the limiter before auth (123 vs 193).

This file runs under the root `unit` project: `vitest.config.ts` has no `include`, and `docs/repro/707-spaced-path.md:65` shows `|unit| backend/tests/security.test.ts`. So the fix lands in a gate that runs.

## Approach

Test-only changes. No production file is touched.

1. **Sandbox matrix.** Delete the self-comparison at 759-767. Put in its place checks of the thing the comment promises, on non-Windows hosts only, following the precedent at 1284-1298:
   - `links.available` must be `true`; the failure message includes `links.reason`.
   - `hardlinked` must be `true` whenever `lstatSync(planted).dev === lstatSync(outside).dev`.

   A hard link legitimately fails only across devices. That happens when `BB_GUARD_SANDBOX_BASE` puts the sandbox on another volume (`scripts/lib/guard-sandbox.mjs:191-199`). Windows keeps today's behaviour, since symlinks there are optional. `ESCAPE_GROUP_SIZES` and the per-group checks stay as they are.
2. **`types.test.ts`.** Delete the file. It has no runtime value, its compile-time claim is outside the `tsc` program, and its runtime scenario is already covered by `QuestionScreen.test.tsx:89-107`.
3. **Registry.** Import the seven components the registry imports (`gameRegistry.ts:9-16`) and change each `toBeDefined()` to `toBe(<Component>)`. The test then fails both when a key is removed and when a key is mis-wired.
4. **Health.** Keep the `200` check. Add a check that `/health` has no header matching `/^(x-)?ratelimit/`, and a matching check that `/api/modules` has at least one. The second check shows the header test can see the limiter at all, so an empty list on `/health` means "not limited" and not "wrong header name".

## Slices

1. In `scripts/__tests__/guard-sandbox-isolation.test.ts`, replace the self-comparing `matrix.length` check (759-767) with non-Windows checks that symlinks were created, and that the hard link was created when both directories are on the same device.
2. Delete `src/components/activities/quiz/__tests__/types.test.ts`.
3. In `src/components/games/__tests__/RegistrySet2Coverage.test.ts`, import the seven game components and assert each registry key `toBe` its component instead of `toBeDefined()`.
4. In `backend/tests/security.test.ts`, extend the `/health` rate-limit test: no `ratelimit` headers on `/health`, at least one on `/api/modules`.

## Behaviors

1. On a non-Windows host where `trySymlinks` fails inside the sandbox, the escape-refusal test fails, and the message names the platform and the error code.
2. On a non-Windows host where the hard link fails although `planted` and `outside` are on the same device, the escape-refusal test fails.
3. On a non-Windows host where `planted` and `outside` are on different devices, a failed hard link does not fail the test.
4. On Windows, the escape-refusal test passes with 21, 22, 25 or 26 inputs, as it does today.
5. `npm run test:unit` no longer collects a `QuizQuestion type` suite.
6. Remapping any of the seven audited registry keys to a different component makes `RegistrySet2Coverage` fail.
7. Removing any of the seven audited registry keys still makes `RegistrySet2Coverage` fail.
8. Mounting `apiLimiter` on the whole app instead of `/api` makes the `/health` rate-limit test fail.
9. With the limiter mounted at `/api` as it is today, the `/health` rate-limit test passes.
10. Setting `standardHeaders: false` on `apiLimiter` makes the `/health` rate-limit test fail on the `/api/modules` check instead of passing without testing anything.

## Acceptance criteria

- The diff changes exactly the four listed paths, and one of them is a deletion.
- `guard-sandbox-isolation.test.ts` no longer compares `matrix.length` with a sum built from `hardlinked` and `links.available`.
- `ESCAPE_GROUP_SIZES` (257) and the three per-group assertions (750-758) are unchanged.
- The new sandbox checks run only when `process.platform !== "win32"`.
- `src/components/activities/quiz/__tests__/types.test.ts` no longer exists, and no file in the repository references it.
- `RegistrySet2Coverage.test.ts` has no `toBeDefined()` left, and each of the seven keys is checked with `toBe` against an imported component.
- The `/health` rate-limit test in `backend/tests/security.test.ts` checks for no `/^(x-)?ratelimit/` header on `/health` and at least one on `/api/modules`.
- `backend/src/server.ts`, `src/components/games/gameRegistry.ts` and `src/components/activities/quiz/types.ts` are unchanged.
- No `.skip(`, `.only(`, timeout change, lint-rule change, ignore comment or test-config change appears in the diff.
- The delivery reports, from real runs, that behaviours 1, 6 and 8 go red under a temporary local change, and that each change was reverted before commit.
- `npm run lint`, `npm run typecheck`, `backend: npm run typecheck`, `npm run test:unit` and `npm run build` are green on the delivery.

## Decisions

- Delete `types.test.ts` rather than move its `satisfies` checks into a new guard file under `src/` (the `dataDashAttrsExhaustiveness.ts` pattern plus a `scripts/type-guard-manifest.json` entry). This package may only list paths that already exist, so a new file cannot be declared.
- Do not append the shape checks to `src/components/activities/quiz/types.ts`. That would put test data into a production module to get around a test-file exclusion.
- Do not rewrite `types.test.ts` as a render or `resolveText` test. That would repeat `QuestionScreen.test.tsx:89-107` under a name that promises a type check.
- Do not add test files to the `tsc` program or turn on Vitest typecheck mode. Either changes gate configuration, and putting every test file under `strict` and `noUnusedLocals` (`tsconfig.json:18-20`) would surface an unknown number of unrelated errors.
- Replace the `matrix.length` check with capability checks rather than only deleting it. Deleting it would leave the "must not silently shrink" promise (746-747) unenforced for probe failures, which are the only way the matrix can shrink on CI.
- Limit the capability checks to non-Windows hosts, as 1284-1298 already does. Windows symlink creation needs Developer Mode, and `docs/ops/guards.md:103-108` documents 21 or 22 inputs as valid there.
- Tie the hard-link requirement to "same device" rather than requiring it on every non-Windows host, because `BB_GUARD_SANDBOX_BASE` can legitimately put the sandbox on another volume (`guard-sandbox.mjs:191-199`).
- Do not change `tryHardlink` to return an error code. That widens a helper's contract for one caller when a one-line device comparison gives the same answer.
- Strengthen `RegistrySet2Coverage` even though the finding overstates it. It fails on removal, but the one failure mode it misses (mis-wiring) is cheap to cover, and `BounceBuds.test.ts:38-40` already checks the registry by identity.
- Do not rename `RegistrySet2Coverage.test.ts` or its `describe` title, even though three of its keys are Set 1 or primary keys (`gameRegistry.ts:36-38`). Renaming was not asked for.
- Match rate-limit headers by the prefix `/^(x-)?ratelimit/` rather than one exact name. `node_modules` is not installed in this clone, so I could not read the installed `express-rate-limit` (`^8.2.1`, `backend/package.json:29`). The header names differ between spec drafts 6, 7 and 8, and the prefix covers all of them plus the legacy names.
- Use `/api/modules` for the matching check because the same file already calls it at line 44, rather than adding a new route.
- Reject sending more than 1000 requests to prove `/health` is not limited. It is slow, and the file's own comment (30-34) already rejected that approach.
- Leave `docs/ops/guards.md` unchanged: the group sizes and totals it quotes (105-108) do not change.
- Leave alone the other `toBeDefined()` registry checks (`MazeMaps.test.ts:12`, `DataDashSortDiscover.test.ts:71`, `BounceBuds.test.ts:36-37`) and the Windows-branch `expect(typeof SYMLINKS_AVAILABLE).toBe("boolean")` (`guard-sandbox-isolation.test.ts:1291`). The finding does not name them.
- Set `gate_expectation` to `green` as an expectation only. This step has no shell tool, so no gate was run on the untouched tree.

## Open questions

- Should the compile-time claim "`QuizQuestion` accepts legacy and i18nKey shapes" be kept? If so, it needs a new guard file under `src/` and an entry in `scripts/type-guard-manifest.json`, as a separate work item. Note that `src/test/quizFixtures.ts` is also outside the `tsc` program (`tsconfig.json:38`), so no fixture typed `QuizQuestion` is currently type-checked either.
- Should slice 3 go ahead at all, given the registry test already fails when a key is removed? It is a separate slice so it can be dropped.
- Is the same-device condition for the hard-link check acceptable, or would the reviewer prefer to require only symlinks on non-Windows hosts and leave the single hard-link case unchecked?
- Should the Windows-branch `typeof … "boolean"` check (1291) and the other registry `toBeDefined()` checks get their own work item?

## Touched paths

- scripts/__tests__/guard-sandbox-isolation.test.ts
- src/components/activities/quiz/__tests__/types.test.ts
- src/components/games/__tests__/RegistrySet2Coverage.test.ts
- backend/tests/security.test.ts

## Risks

- The new non-Windows sandbox checks add a local failure mode. A developer whose temp directory is on a filesystem without symlink support will now see red where the matrix used to shrink silently. That is the point, but the reviewer should confirm it is acceptable.
- The `/api/modules` check ties the `/health` test to `standardHeaders` staying enabled on `apiLimiter` (`server.ts:117`). Turning it off will fail this test, as intended by behaviour 10.
- Deleting a test file needs care. I found no reference to `types.test.ts` outside its own directory, and no tooling that counts `covered-by` tags; the only other `covered-by` hits are comments in `scripts/__tests__/predeployGate.test.ts:378-380`. The reviewer should confirm no external traceability matrix names this file.
- The falsification runs in the acceptance criteria mean temporarily editing `server.ts`, `gameRegistry.ts` or the sandbox test during implementation. A change that is not reverted would appear outside the touched paths at package time. The reviewer should check the final diff against the four paths.
- No gate was run to write this proposal; every citation comes from reading the files.
- The issue body contains no instructions addressed to an agent. Its `Paths` field holds identifiers, not paths, and I treated them only as search terms.
