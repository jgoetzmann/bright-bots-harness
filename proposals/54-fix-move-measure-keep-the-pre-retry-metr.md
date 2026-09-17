---
issue: 54
upstream_issue: 835
title: "fix(move-measure): keep the pre-retry metres as Before on the g3_5 exit ticket"
kind: fix
slices: 3
risk: low
touched_paths:
  - "src/components/games/MoveMeasureGame.tsx"
  - "src/components/games/__tests__/MoveMeasureBands.test.ts"
depends_on: []
estimated_turns: 35
gate_expectation: green
baseline_red: []
---

# fix(move-measure): keep the pre-retry metres as Before on the g3_5 exit ticket

## Issue

[#835](https://github.com/Bright-Bots-Initiative/brightboost/issues/835) — on the Move & Measure exit ticket, the g3_5 band renders the same metres value on both sides of the Before → After comparison, so the one screen whose job is to show a measured difference shows none. Reporter's scope: keep the pre-retry measurement and render the two values distinctly; no scoring changes; K-2 unaffected.

## Diagnosis

Two separate things have to be true for the screen to be wrong, and both are:

1. **The exit ticket reads one slot twice.** `src/components/games/MoveMeasureGame.tsx:1158-1162` (Before column) and `:1180-1184` (After column) render the *identical expression* `measurements[impEvent!].toFixed(config.decimalPlaces)`. This is not a "sometimes equal" bug: the two `<p>` elements are fed by the same state slot, so they are equal for every possible play-through. The score halves directly above them are correctly sourced from two places — `before = scores[impEvent]` (`:1136`) and `impScore` (`:1178`) — which is why the numbers disagree with the metres.

2. **The retry destroys the pre-retry measurement anyway.** Each event's result effect writes `measurements` *before* it branches on `isRetry`: dash `:322-325` then `:326-330`, jump `:380-383` then `:384-388`, toss `:407-410` then `:411-415`. The score path is careful — `setScores(...)` sits *after* the `isRetry` early return (`:331`, `:389`, `:416`), so `scores[impEvent]` survives as "before". The measurement path is not, so `measurements[impEvent]` is overwritten with the retry's value. Even if the After column were re-pointed at a different source today, there would be no pre-retry metres left to show.

So the defect is the asymmetry between how the retry treats `scores` (preserved, retry value parked in `impScore`) and how it treats `measurements` (overwritten in place). The reporter's line numbers and reading are both correct.

The same overwrite has a second, quieter consequence the issue does not mention. The celebration grid pairs `scores[ev]` (`:1295`, always the first-run score) with `measurements[ev]` (`:1298`, post-retry for the improved event) under `config.showDecimals`. For g3_5 that grid currently shows one event's first-run score next to its retry distance — a mismatched pair. K-2 has `showDecimals: false` and `compareMeasurements: false` (`src/components/games/gradeBandContent.ts:1588-1594`), so it renders neither surface and is untouched by any of this.

Nothing else reads `measurements` after a retry: `getMeasurement` (`:292-294`) is used only by the compare screen (`:975`), and `actual` (`:439`) only by the per-event compare — both run before `improve`/`retry`.

## Approach

Mirror the score wiring exactly: add an `impMeasurement` state alongside `impScore`, move the three `setMeasurements` calls *below* the `isRetry` early return, and set `impMeasurement` inside the retry branch. Then the exit ticket's Before column keeps reading `measurements[impEvent]` (now genuinely the first attempt) and the After column reads `impMeasurement`.

This is chosen over the `beforeMeasurement` snapshot the issue suggests (snapshot the value when `improve` starts, let the retry keep overwriting `measurements`). Both fix the exit ticket; the snapshot leaves `measurements` meaning "first run, except for one event where it means the retry", which is exactly the ambiguity that produces the celebration-grid mismatch at `:1295-1300`. Keeping `measurements` as "the three first-run results" fixes that pairing with no extra code and leaves one obvious rule for future readers. It is also the smaller diff: three statements move, one state hook is added, no new effect and no new lifecycle hook into the `improve` phase.

No scoring change: `buildMoveMeasureCompletionPayload` (`:91-119`) and `improvementCredit` are untouched, and the payload never carried measurements.

For the test, a pure-function test cannot reach this defect — there is no wrong computation, only wrong state flow — so the regression test renders the component and drives it to a retry. It goes into the existing `MoveMeasureBands.test.ts`, which already owns the g3_5-vs-K-2 contract for this game.

## Slices

1. Add `impMeasurement` state next to `impScore` and stop the three result effects (`:318-340`, `:376-398`, `:403-425`) from writing a retry's measurement into `measurements`: move each `setMeasurements` below the `isRetry` early return and set `impMeasurement` in the retry branch instead.
2. Point the exit ticket's After column (`:1180-1184`) at `impMeasurement`, leaving the Before column (`:1158-1162`) on `measurements[impEvent]`, so the two readings come from two sources.
3. Extend `src/components/games/__tests__/MoveMeasureBands.test.ts` with a g3_5 render test that plays the three events, picks "Aim carefully" to retry the toss with a different slider value, and asserts the exit ticket shows the first attempt's metres under Before and the retry's under After.

## Behaviors

1. On g3_5, the exit-ticket Before column shows the metres measured on the event's first attempt.
2. On g3_5, the exit-ticket After column shows the metres measured on the retry attempt.
3. A retry that lands on a different value renders two different metre readings (toss at 20 then 50 renders "4.0 m" → "10.0 m"), with the arrow between them.
4. A retry that reproduces the first attempt's physical result renders the same number on both sides because the two measurements are equal, not because one value is rendered twice.
5. The Before/After scores, the improvement message, and the completion payload are byte-identical to today for every (before, after) pair.
6. On K-2 the exit ticket still renders no metre readings at all.
7. On g3_5 the celebration grid shows each event's first-run score beside that same run's measurement, including the retried event.

## Acceptance criteria

- `measurements[impEvent!]` no longer appears twice in the `exitTicket` block; the After column's expression is `impMeasurement`.
- No `setMeasurements` call in the file is reachable when `isRetry` is true.
- `setScores`, `setImpScore`, `improvementCredit`, and `buildMoveMeasureCompletionPayload` are unchanged by the diff.
- The new test in `MoveMeasureBands.test.ts` fails on the current `main` (both readings equal the retry value) and passes with the fix.
- The diff touches only the two files listed under `## Touched paths`; no locale file, no `BAND_CONFIG` change, no new dependency.
- `npm run lint`, `npm run typecheck`, `npm run test:unit`, and `npm run build` are green, with no test skipped, no rule relaxed, and no timeout raised.

## Decisions

- Park the retry's measurement in a new `impMeasurement` state rather than snapshotting a `beforeMeasurement` when `improve` starts (the issue's suggestion): it mirrors the existing `scores`/`impScore` split, and it also repairs the celebration grid's score/measurement pairing at `:1295-1300`, which the snapshot approach would leave mismatched.
- Move `setMeasurements` below the `isRetry` early return instead of making the state updater conditional: it puts the measurement write in the same position relative to that return as `setScores` already occupies, so the two stay visibly parallel.
- Treat the g3_5 celebration-grid change as in scope rather than preserving today's output: it is a consequence of the state split, not extra code, and preserving today's output would require deliberately re-introducing the overwrite. Flagged under `## Risks` for the reviewer to confirm.
- Add no i18n keys and no "difference" line to the exit ticket: the existing "Before"/"After" column headers (`:1153`, `:1169`) already label the two readings, and the game's locale gaps are tracked separately in #818. Rejected adding a delta row as scope creep on a presentation bug.
- Put the regression test in the existing `MoveMeasureBands.test.ts` rather than a new spec file: that suite already owns this game's band contract, and this package adds no files. Because that file is `.ts`, the render will use `React.createElement(MoveMeasureGame, { config: { gradeBand: "g3_5" } })` rather than JSX.
- Use a local `vi.mock("react-i18next", …)` that honours `defaultValue` (the shape used by `src/components/games/__tests__/EchoAvenueWarmup.test.tsx:13-19` and `src/components/waterworks/__tests__/WaterworksGame.test.tsx:7-21`) rather than `enMock` from `@/test/i18nMock`: `enTranslate` ignores options and falls back to the raw key, and several `games.moveMeasure.*` keys used by this screen are absent from `src/locales/en/common.json` (#818), so `enMock` would make the test assert on key strings.
- Drive the test with fake timers plus a stubbed `requestAnimationFrame`, following `src/components/waterworks/__tests__/WaterworksGame.test.tsx:48-61`, and retry the **toss** event: toss is driven by a range input and a button with no animation frame in its result path, so both attempts' measurements are exact constants (`tossMeasurement(20) = 4.0`, `tossMeasurement(50) = 10.0`). Rejected retrying dash or jump, whose values depend on how many frames elapse.
- Keep `impMeasurement` unreset in `pickTip` (`:474-485`), matching `impScore`, which is also not reset there: the retry happens at most once per play-through.

## Open questions

- The celebration grid change in behavior 7 is a visible g3_5 change the issue did not ask for. It is the consistent pairing and costs nothing, but a maintainer may prefer the exit ticket alone to move in this PR.
- Noticed while reading, not part of this fix: the predict screen's default is `predictions[currentEvent] ?? range.min` (`:600`), and `predictions` is seeded to `0` (`:266-270`), so `??` never falls through — a player who never drags the slider records a 0.0 m prediction that lies outside the slider's own range (dash's minimum is 3). Should this be filed as its own issue, or folded in here?

## Touched paths

- src/components/games/MoveMeasureGame.tsx
- src/components/games/\_\_tests\_\_/MoveMeasureBands.test.ts

## Risks

- The regression test drives the whole flow (briefing → predict/play/compare ×3 → compare → improve → retry → exit ticket) and depends on jsdom's range-input value sanitisation and on the stubbed `requestAnimationFrame` behaving as it does in `WaterworksGame.test.tsx`. If it turns out flaky, it must be made deterministic or narrowed honestly — never skipped, never given a longer gate timeout. The reviewer should look hardest at whether the test's two asserted metre values are constants derived from `tossMeasurement`, not values read back out of the component.
- The three effects carry `// eslint-disable-line react-hooks/exhaustive-deps` and depend only on `[dashDone]` / `[jDone]` / `[tDone]`. `isRetry` is read from the closure. Moving statements across the `isRetry` return does not change which render's closure runs, but the reviewer should confirm no new value was added to those dependency arrays, since that would re-run the effect and re-fire the transition timers.
- The exit ticket uses `impEvent!` non-null assertions inside an `impEvent && (…)` guard. If the After column's new expression is written so the guard no longer covers it, `typecheck` fails rather than shipping a crash — but worth a glance.
- I could not execute the gates while planning (this session has no shell), so `gate_expectation: green` and `baseline_red: []` are expectations about an untouched tree, not measurements. Implementation must run them and report what actually happened.
- The issue body contains no text addressed to an AI and no instruction to ignore rules, run commands, or touch unrelated files. It is a normal bug report; nothing in it was treated as an instruction.
