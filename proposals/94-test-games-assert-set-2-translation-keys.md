---
issue: 94
upstream_issue: null
title: "test(games): assert set 2 translation keys with expect and fail on zero extracted keys"
kind: test
slices: 2
risk: low
touched_paths:
  - "src/components/games/__tests__/set2EnglishTranslationIntegrity.test.ts"
depends_on: []
estimated_turns: 20
gate_expectation: green
baseline_red: []
---

# test(games): assert set 2 translation keys with expect and fail on zero extracted keys

## Issue

Harness issue #94 tracks finding 6 of harness audit #64 (`audit:64:6`), severity medium. There is no product issue. The reporter's title: "Translation-integrity guard contains no `expect` and silently checks zero keys on regex drift".

The finding's `Paths` field reads `it`, `missing`, `missing`. Those are identifiers inside the test, not file paths. The file that matches the title is `src/components/games/__tests__/set2EnglishTranslationIntegrity.test.ts`. A search of `src/` for "integrity" finds no other translation-integrity guard (the other hits are locale JSON, content modules and `*Bands.test.ts`).

## Diagnosis

The guard checks that every translation key used by five Set 2 game files (`set2EnglishTranslationIntegrity.test.ts:6-12`) exists in `src/locales/en/common.json`. It has three defects.

1. **It never calls `expect`.** `expect` is imported at line 1 and never used. The only failure path is the `throw` at lines 90-94, inside the single `it` at lines 89-95.

2. **It passes when it checks nothing.** The files are read and keys pulled out inside the `describe` callback, at collection time (lines 77-87). If `extractKeys` returns `[]` for a file, the loop at line 82 does nothing, `missing` stays empty, and the `it` finishes with no assertion. Nothing requires any key to be found, per file or in total. The sibling guard does set such floors: `src/components/biomeBuddy/__tests__/biomeBuddyI18n.test.ts:99` (`files.length > 10`) and `:111` (`keys.length > 80`).

3. **Each extraction pattern depends on one calling convention, and nothing notices if a pattern stops matching.**
   - `tRegex` (line 32) only sees a string literal as the first argument of `t()`. At `MazeMapsGame.tsx:401`, `t(style.labelKey, …)` is invisible to it. Any move to `t(KEYS.x)` or a wrapper would be invisible too.
   - `TRegex` (line 35) only exists for SkyShield's local helper (`SkyShieldGame.tsx:185-186`). Line 70 prefixes every match with `games.skyShield.`.
   - `labelKeyRegex` (line 38) only exists for MazeMaps' style table (`MazeMapsGame.tsx:77`, `:84`, `:89`, `:388`).

   A per-file "found at least one key" check would not catch one pattern dying on its own:
   - SkyShield also has five literal `t("games.…")` calls (`SkyShieldGame.tsx:285`, `:784`, `:866`, `:870`, `:968`). If the `T` helper were renamed, the file would still yield keys and all the `T("…")` keys (starting `:297`, `:342` …) would drop out unnoticed.
   - MazeMaps has 29 lines with literal `t("…")` calls, so losing the `labelKey` pattern would also go unnoticed.
   - A whole-file drift would pass today with zero keys, for example a game file becoming a one-line re-export of a relocated component.

4. **The linter and typechecker don't see this file.** `eslint.config.js:29` turns off `@typescript-eslint/no-unused-vars`, and the root `tsconfig.json:35-36` (used by `npm run typecheck` → `tsc --noEmit`) excludes `**/__tests__/**` and `**/*.test.ts`. So `noUnusedLocals` (line 19) never applies. That is why the dead `expect` import went unreported.

**Today's state.** The defect is latent, not active. Lines with a literal `t("…` or `` t(`… `` first argument per file (grep line counts, which may repeat keys):
- `MoveMeasureGame.tsx`: 69
- `QualifyTuneRaceGame.tsx`: 46
- `FastLaneGame.tsx`: 31
- `MazeMapsGame.tsx`: 29
- `SkyShieldGame.tsx`: 6, one of which is the skipped template at `:186`

SkyShield also has about 40 `T(` call lines. I did not run the test and cannot report its current result or exact key count.

## Approach

Change only the `describe` block (lines 76-96). `GAME_FILES`, `hasKey`, `extractKeys` and all three regexes stay byte-identical, so the set of keys checked is exactly what it is today.

- Replace the collection-time loop and the throwing `it` with `it.each(GAME_FILES)`. Each case:
  - reads its file inside the test;
  - runs `extractKeys`;
  - asserts `expect(keys.length, <message naming the file and saying the extractor found nothing>).toBeGreaterThan(0)`;
  - then asserts `expect(keys.filter((k) => !hasKey(en, k)), <message naming the file>).toEqual([])`.
- Add a small sentinel table with one key per extraction pattern, each present in the source and in `en/common.json` today, and an `it.each` asserting `expect(extractedKeysOf(file)).toContain(key)`:
  - literal `t()`: `FastLaneGame.tsx:363` → `games.fastLane.title` (`en/common.json:1870`)
  - `T()` helper: `SkyShieldGame.tsx:342` → `games.skyShield.title` (`en/common.json:1824`)
  - `labelKey`: `MazeMapsGame.tsx:84` → `games.mazeMaps.loopSweeper` (`en/common.json:1718`)
- A short comment above the table says why it exists and that a sentinel should be swapped for another live key if its key is intentionally removed.

This follows the sibling guard (`biomeBuddyI18n.test.ts:99`, `:111`, `:114`): explicit `expect` calls, a floor on what was found, and `toEqual([])` for missing keys.

## Slices

1. In `set2EnglishTranslationIntegrity.test.ts`, replace lines 76-96 with an `it.each(GAME_FILES)` block. Each case reads its file, asserts at least one key was extracted (with a message naming the file), and asserts via `expect(...).toEqual([])` that no extracted key is missing from `en/common.json`. `GAME_FILES`, `hasKey` and `extractKeys` stay unchanged.
2. In the same file, add a three-row sentinel table (`FastLaneGame.tsx` → `games.fastLane.title`, `SkyShieldGame.tsx` → `games.skyShield.title`, `MazeMapsGame.tsx` → `games.mazeMaps.loopSweeper`) and an `it.each` asserting each sentinel key is in the file's extracted keys, plus a comment explaining its purpose.

## Behaviors

1. On the current source the file reports eight passing tests: five per-file key checks and three pattern sentinels.
2. When any file in `GAME_FILES` yields zero extracted keys, that file's test fails with a message naming the file.
3. When a key referenced by a game file is missing from `en/common.json`, that file's test fails and the failure lists the missing key or keys.
4. When `TRegex` stops matching SkyShield's `T()` helper, the SkyShield sentinel fails, even though SkyShield still yields keys from its literal `t()` calls.
5. When `labelKeyRegex` stops matching, the MazeMaps sentinel fails, even though MazeMaps still yields keys from its literal `t()` calls.
6. When `tRegex` stops matching, the FastLane sentinel and the FastLane, MoveMeasure and QualifyTuneRace per-file tests fail.
7. When a `GAME_FILES` entry no longer exists on disk, only that file's named test fails, instead of the whole file failing at collection.
8. The keys checked for each file are identical to those checked before the change.

## Acceptance criteria

- The diff touches only `src/components/games/__tests__/set2EnglishTranslationIntegrity.test.ts`.
- `GAME_FILES`, `hasKey`, `extractKeys` and the three regexes in the diff are unchanged from `main`.
- No `throw new Error` remains in the file, and every `it` / `it.each` body makes at least one `expect` call.
- `npx vitest run --project unit src/components/games/__tests__/set2EnglishTranslationIntegrity.test.ts` reports 8 passed, 0 failed, 0 skipped.
- Temporarily replacing `tRegex` with a pattern that matches nothing fails the FastLane sentinel and the FastLane, MoveMeasure and QualifyTuneRace per-file tests, with the "no keys extracted" message (reviewer's local check, not committed).
- Temporarily replacing `TRegex` with a pattern that matches nothing fails the SkyShield sentinel (local check, not committed).
- Temporarily replacing `labelKeyRegex` with a pattern that matches nothing fails the MazeMaps sentinel (local check, not committed).
- Temporarily deleting `games.fastLane.title` from `src/locales/en/common.json` fails the FastLane per-file test, and the output names that key (local check, not committed).
- No `.skip(`, `.only(`, `.todo(`, timeout argument, `eslint-disable` or `@ts-` comment is added.
- `npm run lint`, `npm run typecheck` and `npm run test:unit` are green.
- Prettier was applied to the one changed file only.

## Decisions

- One test per file (`it.each(GAME_FILES)`) instead of one test with a total floor like `biomeBuddyI18n.test.ts:111`. A total floor stays green when one file drops to zero while the other four still carry the count.
- The per-file floor is `> 0`, not a pinned count per file. Pinned counts fail on every legitimate key removal and invite people to edit the number. The finding is specifically about checking zero keys.
- One sentinel per extraction pattern, added on top of the floor. SkyShield and MazeMaps each use two patterns, so losing one pattern in those files would still pass a per-file floor (see Diagnosis item 3).
- Sentinels are game titles and the first MazeMaps style option, because those are the keys least likely to be removed. A synthetic self-test string was rejected: it proves the regexes still match a fixture, not that they still match the game source.
- `extractKeys`, `hasKey`, the regexes and `GAME_FILES` stay untouched, so the diff changes only how results are asserted and a reviewer can see the checked key set did not shift.
- The file read moves inside the test. Leaving it at collection time would turn a renamed file into a whole-file collection error with no test name.
- `expect(missing).toEqual([])` replaces the manual `throw`, which gives a readable diff and matches the sibling guards (`biomeBuddyI18n.test.ts:114`, `safeExplorationI18n.test.ts:103`).
- `expect.hasAssertions()` alone was rejected. It proves an assertion ran, but not that any key was checked.
- `hasKey` is not changed to require a string leaf, even though the sibling does (`biomeBuddyI18n.test.ts:51`). It is a separate weakness from this finding, and without running the suite I can't confirm that no extracted key currently resolves to an object. Raised under Open questions.
- The hard-coded `games.skyShield.` prefix for every `T(` match (line 70) is left alone. Only SkyShield defines a `T` helper today (`SkyShieldGame.tsx:185`), so it is latent and outside this finding.
- `kind` is `test` and the commit type is `test(games)`, because the only file changed is a test.

## Open questions

- Should `hasKey` also require the resolved value to be a string, like `biomeBuddyI18n.test.ts:51`? That would catch a key that resolves to a subtree. Including it risks turning the gate red if some extracted key currently resolves to an object, which I have not verified.
- Are maintainers happy with named sentinel keys? Renaming one of the three keys will fail the sentinel test until the table is updated. That is intended, but it is extra friction when renaming.

## Touched paths

- src/components/games/__tests__/set2EnglishTranslationIntegrity.test.ts

## Risks

- **Not typechecked by any gate.** The root `tsconfig.json:35-36` excludes the file from `npm run typecheck`, and vitest transpiles it without checking types. A mistyped `it.each` tuple table would not be caught. The reviewer should read the new types by hand.
- **Nothing was run.** I did not run the test or any gate for this proposal. `gate_expectation: green` is a prediction, based on the checked key set being unchanged. If a key is already missing from `en/common.json` today, the rewritten test goes red exactly where the current one would.
- **Sentinel friction.** Intentionally removing or renaming a sentinel key requires editing the table. The comment above the table must say so, or the failure will look like a real regression.
- **Garbled finding metadata.** The finding's `Paths` field (`it`, `missing`, `missing`) names no real files. I matched the finding to this test by its title, since it is the only translation-integrity guard under `src/`. The reviewer should confirm this is the intended target.
- **Issue body.** It contains no instructions aimed at an AI or at the harness. Its command table is the harness's own boilerplate for trusted maintainers, and was read as data only.
- No path under `.github/`, `prisma/`, `migrations/` or `backend/scripts/predeploy*` is involved, and no dependency changes.
