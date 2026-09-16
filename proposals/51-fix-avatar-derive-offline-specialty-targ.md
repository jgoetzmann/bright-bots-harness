---
issue: 51
upstream_issue: 863
title: "fix(avatar): derive offline specialty targets from canonical set sizes"
kind: fix
slices: 2
risk: low
touched_paths:
  - "src/pages/Avatar.tsx"
depends_on: []
estimated_turns: 16
gate_expectation: green
baseline_red: []
---

# fix(avatar): derive offline specialty targets from canonical set sizes

## Issue

https://github.com/Bright-Bots-Initiative/brightboost/issues/863 — #863. The reporter found this during #855's review rounds: `SET_CONFIG` in `src/pages/Avatar.tsx` carries dead `target: 15/30/50` values, and the fallback stats object renders them, while `/student/stats` returns `target: 5` per set. The specialty meters read "/15, /30, /50" — wrong by 3-10×. They propose aligning the fallback with the canonical set sizes (`@shared/progression/stemSetIds` after #855) or rendering an explicit loading state instead.

## Diagnosis

The reporter is right about the defect and slightly wrong about when it fires. Line numbers have drifted since filing; the two constructs they cite as `:43-47` and `:99-107` are now at `:79-83` and `:164-177`.

Two separate pieces of stale data:

1. **Dead field.** `src/pages/Avatar.tsx:79-83` declares `SET_CONFIG = [{key:"set1", target:15}, {key:"set2", target:30}, {key:"set3", target:50}]`. Grepping the whole repository for `SET_CONFIG` returns exactly three hits — the declaration at `:79`, and `:322`/`:325` in the render, both of which read only `.key`. No call site anywhere reads `set.target`. The field is inert, not wrong-on-screen.

2. **The field that actually renders.** `src/pages/Avatar.tsx:164-177` builds the `stats ?? { … }` fallback with its own hardcoded `target: 15 / 30 / 50` inside `specialtyProgress`. That object *is* read, at two sites: `:349` renders `` `${sp.current}/${sp.target}` `` for each of the three meters, and `:411-412` renders `{s.specialtyProgress.set3.current}/{s.specialtyProgress.set3.target}` in the locked-specialization card.

The canonical truth: `backend/src/routes/studentStats.ts:157-164` computes `target: ids.length` over `STEM_SET_1_IDS`, `STEM_SET_2_IDS`, `STEM_SET_3_IDS` (imported from the shared canon at `:6-10`, used at `:166-170`). Each of those arrays holds five IDs — `shared/progression/stemSetIds.ts:21-27`, `:34-40`, `:69-75`. So the live API always answers `target: 5`, three times over, and the fallback disagrees by 3×, 6× and 10×.

**Correction to the report:** this does not happen "during load." `src/pages/Avatar.tsx:150-162` returns a `Skeleton` tree while `loading` is true, and `loading` is only cleared in the effect's `finally` at `:141` — the fallback at `:164` is unreachable until the effect has settled. It is reached only when `Promise.all` at `:131-134` rejects, because the catch at `:138-140` just `console.error`s and leaves `stats` null. The defect is exclusively on the API-failure / offline path, not the loading path.

That same path also leaves `avatar` null, so `getStudentArchetype(null)` returns null (`src/lib/moduleAccess.ts:82-83`) and the page takes the locked branch at `:404-415` — which is why the bogus "0/50" shows up twice on one screen, once as a meter label and once as "Progress: 0/50".

The progress bars themselves are unaffected: `current` is 0 in the fallback, so `pct` at `:327` is 0 regardless of the target. The defect is confined to the two numeric labels.

`src/constants/stemSets.ts:9-15` already re-exports the three canonical arrays from `@shared/progression/stemSetIds`, so the frontend has a supported door onto exactly the numbers the backend uses.

## Approach

Replace the three fabricated targets in the fallback with `STEM_SET_1_IDS.length`, `STEM_SET_2_IDS.length` and `STEM_SET_3_IDS.length`, imported from `@/constants/stemSets`, and delete the dead `target` field from `SET_CONFIG`. One file, roughly six lines.

Deriving rather than writing `5` matters because the backend derives too (`backend/src/routes/studentStats.ts:161` is literally `target: ids.length`); importing the same arrays reproduces the API's answer today and keeps reproducing it when a Set 3 placeholder is replaced or a set grows. Hardcoding `5` would be the same class of defect one curriculum change later.

Rejected: rebuilding the failed-load branch into an explicit error state. The issue offers it as an alternative, and it would fix a real second problem — an API failure currently renders a fully plausible "you have no progress" page — but it needs new `en` and `es` copy and a product call on whether to offer a retry. That is a different package; it is recorded under Open questions.

Rejected: changing the `Stats` type at `:13-26`. `target` genuinely comes from the API on the success path and must keep doing so.

## Slices

1. Replace the hardcoded `target: 15 / 30 / 50` in the fallback `specialtyProgress` (`src/pages/Avatar.tsx:172-176`) with the lengths of the canonical `STEM_SET_1_IDS` / `STEM_SET_2_IDS` / `STEM_SET_3_IDS` arrays, imported from `@/constants/stemSets`.
2. Delete the dead `target` field from `SET_CONFIG` (`src/pages/Avatar.tsx:79-83`), leaving the `key` entries and the `:322`/`:325` call sites untouched.

## Behaviors

1. When `/student/stats` or `getAvatar` rejects, each of the three specialty meter labels reads `0/5` instead of `0/15`, `0/30`, `0/50`.
2. When that same failure leaves the specialization card locked, it reads `Progress: 0/5` instead of `Progress: 0/50`.
3. When `/student/stats` succeeds, every meter renders the API's own `current` and `target` verbatim, exactly as before this change.
4. On the failure path `set3.complete` stays false, so the specialization picker remains locked and the "pick your path" branch at `:391-402` is not reachable.
5. Editing a canonical array in `shared/progression/stemSetIds.ts` moves the fallback targets with it, with no edit to `src/pages/Avatar.tsx`.
6. No numeric literal in `src/pages/Avatar.tsx` is used as a specialty-set target after the change.

## Acceptance criteria

- The fallback's three `target` values in `src/pages/Avatar.tsx` are `STEM_SET_1_IDS.length`, `STEM_SET_2_IDS.length` and `STEM_SET_3_IDS.length`; no `15`, `30` or `50` remains as a set target anywhere in the file.
- The import is added from `@/constants/stemSets`, matching the specifier style used by the eight existing frontend tests and modules that consume the canon.
- `SET_CONFIG` contains only `key` entries; `src/pages/Avatar.tsx:322` and `:325` are otherwise byte-unchanged.
- The `Stats` type at `:13-26` is unchanged, and no change is made to `backend/src/routes/studentStats.ts` or to any file under `shared/`.
- Forcing `/student/stats` to reject shows `0/5` in all three meters and `Progress: 0/5` in the locked card.
- The diff touches exactly one file: `src/pages/Avatar.tsx`.
- `npm run lint`, `npm run typecheck`, `npm run test:unit` and `npm run build` pass, and formatting is applied only to the one changed file.

## Decisions

- Derive the targets from `STEM_SET_n_IDS.length` rather than writing the literal `5` three times. Rejected the literal: `backend/src/routes/studentStats.ts:161` computes `ids.length`, so a literal is correct only until the canon moves — the same failure mode being fixed.
- Import from `@/constants/stemSets` rather than `@shared/progression/stemSetIds` directly. Rejected the direct `@shared` specifier: `src/constants/stemSets.ts:9-15` is the established frontend door onto the canon and every existing frontend consumer goes through it; both resolve to the same module, so a second entry point would be inconsistency for nothing.
- Delete `target` from `SET_CONFIG` rather than repointing it at `STEM_SET_n_IDS.length`. Rejected repointing: nothing reads it (only `.key` at `:322`/`:325`), and a live-looking field with no consumer is what seeded this bug in the first place.
- Keep the name `SET_CONFIG` even though it reduces to a list of keys. Rejected renaming it to `SET_KEYS`: it would churn three call sites for no behavior change and widen the diff a reviewer has to read.
- Keep the fallback object inline at `:164-177` rather than hoisting it to a module-level constant. Rejected hoisting: larger diff, no behavior difference — the object is never mutated on either shape.
- Keep the `stats ?? { … }` zero-state shape and fix only the numbers. Rejected replacing it with an explicit error or retry UI: it needs new `en`/`es` strings and a product decision, and the issue is about fabricated targets. Raised under Open questions instead.
- Ship no new test file. Rejected adding `src/pages/__tests__/Avatar.test.tsx` — the conventional home, and `vitest.config.ts` has no `include` so it would be collected automatically — because the proposal schema accepts only `touched_paths` that already exist and `## Touched paths` is diff-matched at package time; a new file cannot be declared here. Also rejected parking the assertions in `src/constants/__tests__/stemSets.test.ts`: that suite pins the canon arrays, is a `.ts` file with no JSX, and a page-render test would be misfiled there.

## Open questions

- Should the failed-load path show an explicit error rather than a plausible zero-state? After this fix the numbers are honest, but the page still reads "Power Level 0 / Rookie / all meters empty" when the API is simply unreachable — `:138-140` only `console.error`s. That needs copy in `src/locales/en/common.json` and `src/locales/es/common.json` and a call on whether to offer a retry.
- Should a follow-up work package add `src/pages/__tests__/Avatar.test.tsx` to pin the failure-path labels at `0/5`? I could not declare a new file in this proposal, so the fix ships unpinned by an automated test.

## Touched paths

- src/pages/Avatar.tsx

## Risks

- The issue body cites `src/pages/Avatar.tsx:43-47` and `:99-107`; the file has grown and the same constructs are now at `:79-83` and `:164-177`. A reviewer diffing against the quoted line numbers will not find them there. The issue body contained no instruction addressed to an AI and nothing that had to be disregarded.
- The changed output is only visible on a path nobody exercises routinely. The reviewer should force `api.getAvatar()` or `authApi.get("/student/stats")` to reject and confirm `0/5` in all three meters *and* in the locked card at `:411-412` — that second site is easy to miss.
- `STEM_SET_3_IDS` still holds three reserved placeholder slots (`shared/progression/stemSetIds.ts:63-75`), so the honest Set 3 target is 5 even though only `track-maker` and `echo-avenue` are shippable games today. This package deliberately mirrors whatever the API returns rather than reporting "reachable" games; narrowing that is #676's business and changing it here would desynchronise the fallback from `/student/stats` again.
- If a canonical set array were ever emptied, `pct` at `:327` would evaluate `0 / 0` to `NaN`. Not reachable today — all three arrays are non-empty `as const` literals — and this package adds no guard, to keep the diff to the actual defect.
- No path under `.github/` is touched. Nothing under `prisma/`, `migrations/` or `backend/scripts/predeploy*` is touched. No gate is widened, skipped or retimed, no dependency is added, and no whole-tree formatter is run.
