---
issue: 163
upstream_issue: null
title: "test(i18n): interpolate options in the shared English test mock"
kind: test
slices: 3
risk: low
touched_paths:
  - "src/test/i18nMock.ts"
  - "src/components/activities/quiz/__tests__/QuestionScreen.test.tsx"
  - "src/components/activities/quiz/__tests__/QuizSummary.test.tsx"
  - "src/components/games/shared/safeExploration/__tests__/SafeExplorationControls.test.tsx"
  - "src/components/games/__tests__/GameShellAccessibility.test.tsx"
  - "src/pages/__tests__/ActivityPlayerCompletionLatch.test.tsx"
depends_on:
  - 90
  - 161
estimated_turns: 40
gate_expectation: green
baseline_red: []
---

# test(i18n): interpolate options in the shared English test mock

## Issue

Harness issue #163 (https://github.com/jgoetzmann/bright-bots-harness/issues/163) is a `/harness work` request with no product issue. It raises the second open question from proposal #90.

`enTranslate` in `src/test/i18nMock.ts` ignores interpolation options. Several suites therefore wrap it in a local interpolating `t`:

- SafeExplorationControls;
- QuestionScreen;
- QuizSummary;
- GameShellAccessibility, once Bright Boost #952 lands.

The request asks for an assessment before any change:

- Inventory every `enMock` / `enTranslate` user.
- Say which suites would change behaviour if the shared mock filled `{{name}}` options, including any assertion that depends on the raw `{{…}}` text.
- Recommend whether and how to change the mock globally.

There must be no global change until this proposal is approved. The inventory below is that assessment. Merging this proposal approves its recommendation.

The plan is checked against Bright Boost `main` at `1a2437d`.

## Diagnosis

**The mock.**

- `enTranslate(key)` walks `src/locales/en/common.json` and returns the string, or the key when no string is found (`src/test/i18nMock.ts:22-37`).
- It takes no options, and `enMock().useTranslation().t` is `enTranslate` itself (`i18nMock.ts:47-61`). So `{{name}}` placeholders render literally. `returnObjects`, `count` plural suffixes and `defaultValue` are ignored too.
- Some code reached through `enMock` passes a default string as the second argument instead of an options object, for example `t("dashboard.unlocked", "Unlocked")` (`src/pages/StudentDashboard.tsx:984`).

**The inventory.** It was done by reading the code, not by running tests. For each suite, the trace followed its imports (skipping anything the suite mocks), collected every `t(key, {...})` reachable from what it renders, matched those keys against the placeholders in `en/common.json`, and read each assertion against the result. An independent review re-checked the method and more than 15 of the verdicts.

44 files import the mock. One of them, `scripts/patch-i18n-en-mock.cjs`, is a codemod that rewrites mocks and never runs one. That leaves 43 test suites. Four BiomeBuddy suites (for example `src/components/biomeBuddy/__tests__/BiomeBuddyGame.test.tsx:21-34`) carry their own interpolating mocks without the shared one, so they are out of scope.

- **36 suites render nothing interpolated that they assert on.** Either they call only plain keys, or the interpolated text renders but no assertion reads it. Examples:
  - `ActivityPlayerQuizGate` renders "+50 points" but never asserts it.
  - `TeacherClassesLoading` renders "N students" but asserts on exact class names.
  - `SkyShield` renders its reveal and exit copy but asserts only on exact emoji lanes.
- **One suite passes either way.** `GameShellReducedEffects.test.tsx:51` asserts `toHaveAccessibleDescription(/reduces motion/i)`. The description comes from `"Reduces motion, … Source: {{source}}."` (`common.json:1660`), so the regex matches with or without interpolation.
- **One suite has a comment that becomes false, while its assertions still hold.** The doc comment above `describe("replay results copy (#791)")` (`ActivityPlayerCompletionLatch.test.tsx:201-206` today) says the mock does not interpolate. The results screen calls `t("activityPlayer.points", { count })` (`ActivityPlayer.tsx:477-479`; `"+{{count}} points"` at `common.json:1072`). With interpolation, REPLAY-2 and REPLAY-3 render "+50 points" and REPLAY-5 renders "+0 points" (`reply(undefined, 0)`, lines 273-277), instead of "+{{count}} points". That still satisfies:
  - `getByText(/points/)` at line 243, which matches one element because the toast is mocked and never renders;
  - `stringContaining("points")` at lines 255-257 and 263-265;
  - `queryByText(/points/)` being null on the replay branches at line 237, because those branches never call `points`.
- **Three suites carry local wrappers that the change would make redundant.**
  - `QuestionScreen.test.tsx:10-33` and `QuizSummary.test.tsx:7-30` spread `enMock()` and replace each `{{k}}` once with `String.replace`.
  - `SafeExplorationControls.test.tsx:19-44` builds its own mock and replaces every `{{name}}` with split/join. Its comment at lines 16-18 explains why the wrapper exists.
  - They assert interpolated text: "Question 1 of 3" (`QuestionScreen.test.tsx:55` ← `QuestionScreen.tsx:79-82` ← `common.json:1095`), "You got 2 of 3 right!" (`QuizSummary.test.tsx:44`), and the baseline announcements in SafeExplorationControls (lines 169, 286-288, 297-299 and 438).
  - No string in `en/common.json` repeats a placeholder, so the first-occurrence and all-occurrence wrappers give the same result.
- **One wrapper must stay.** `K2InstantFeedbackQuiz.test.tsx:49-67` resolves English and Spanish and handles `returnObjects` for the cheer pools. The shared mock does neither, so that wrapper is not redundant.
- **#952's version of GameShellAccessibility flips from fail to pass.** On `main` that suite is skipped and fails for other reasons too, such as the raw-key heading matcher at line 73. #952 fixes those, un-skips the suite and adds its own interpolating wrapper, because under the shared mock the region is named "{{title}} game area" (`common.json:1649`). After this change, that wrapper is redundant as well.
- **No suite goes from passing to failing.** No test asserts raw `{{` text, none uses snapshots, and no placeholder is named after an i18next option (`defaultValue`, `returnObjects`, `context`).

**The other options the mock ignores.**

- **Plurals.** `common.json` has exactly one `_one`/`_other` pair, `teacher.lessons.available_*` at lines 425-426. Its only caller is `src/components/TeacherDashboard/MainContent.tsx:78`, which both TeacherDashboard suites mock, and both suites are skipped. Every other `count` call these suites reach points at a single plain string. Interpolating `{{count}}` therefore matches what real i18next shows for those strings.
- **`returnObjects`.** `breakSuggestions` (`ActivityPlayer.tsx:189-191`) shows only in the every-third-completion break dialog. The suites that complete activities clear `sessionStorage` between cases (`ActivityPlayerCompletionLatch.test.tsx:101`, `ActivityPlayerQuizGate.test.tsx:124`, `ActivityPlayerPersonalBestSync.test.tsx:96`), so none reaches it.
  - On `main`, no `enMock` suite answers a K-2 question, so none reaches the cheer pools (`K2InstantFeedbackQuiz.tsx:40-41`).
  - Item #162 adds one that does. Under `enMock` its cheer is the raw key, and that case does not assert on cheer text.
- **`defaultValue`.** Calls at `GameShell.tsx:613`, `ModuleDetail.tsx:173` and `TryDemo.tsx:282` would fall back to a default, but none is asserted.

The recommendation follows from this: make the shared mock interpolate, delete the three redundant wrappers and the one #952 adds, keep K2's wrapper, and add no plural, `returnObjects` or `defaultValue` handling.

## Approach

- **Wait** for #90 (#952), which adds the GameShellAccessibility wrapper, and for #161, which edits `ActivityPlayerCompletionLatch.test.tsx`.
- **Record the baseline.** Run `npm run test:unit` on the untouched tree. Also run the five changed suites by path, plus `GameShellReducedEffects`, `K2InstantFeedbackQuiz`, `ActivityPlayerQuizGate` and `ActivityPlayerA11y` (#162 adds a K-2 case there). Record their pass and skip counts.
- **Change `src/test/i18nMock.ts`.**
  - Change the signature to `enTranslate(key: string, options?: unknown)`.
  - Resolve the string as today.
  - Interpolate only when the string was found and `options` is a non-null object that is not an array. A string second argument, such as `t(key, "Default")`, is ignored, as today.
  - Then replace every `{{name}}` whose `name` is an own key of `options` with a value that is neither `undefined` nor `null`, using `String(value)`.
  - Leave every other placeholder in place, so a missing value stays visible in a failing assertion.
  - Leave the key fallback unchanged. Never interpolate into a returned key.
  - `enMock()` keeps `t: enTranslate`, so every user gets interpolation with no call-site change.
  - Update the file's doc comment to say that it fills `{{name}}` options and nothing else.
- **Pin the semantics with one new test.** Add one `it("enTranslate fills options and nothing else")` to `QuestionScreen.test.tsx`, which already imports `enTranslate` and asserts interpolated text. It checks five things:
  - `enTranslate("activityPlayer.questionOf", { current: 1, total: 3 })` is "Question 1 of 3";
  - an absent, `undefined` or `null` option leaves `{{total}}` visible;
  - a string second argument returns the uninterpolated string;
  - a missing key with options returns the key unchanged;
  - a number `0` is filled as "0".
- **Delete the redundant wrappers.**
  - `QuestionScreen.test.tsx:10-33` and `QuizSummary.test.tsx:7-30` become `vi.mock("react-i18next", async () => { const { enMock } = await import("@/test/i18nMock"); return enMock(); })`.
  - Keep QuestionScreen's top-level `enTranslate` import, which its assertions use (lines 102 and 105).
  - Delete QuizSummary's import (line 5), which only the wrapper used. Lint would not catch it, because `@typescript-eslint/no-unused-vars` is off and `tsc` excludes tests.
  - In `SafeExplorationControls.test.tsx`, lines 16-44 become the same three-line `enMock()` mock, and the stale comment goes.
  - In `GameShellAccessibility.test.tsx`, as #952 leaves it, the local `t` and its comment become the plain `enMock()` mock again.
- **Fix the stale comment** above `describe("replay results copy (#791)")` in `ActivityPlayerCompletionLatch.test.tsx`, so it says what the REPLAY cases check. #161 adds LATCH-4 earlier in that file, so find the comment by that `describe`, not by line number. Change no assertion.
- **Leave alone:**
  - `K2InstantFeedbackQuiz.test.tsx`, because of Spanish and `returnObjects`;
  - the BiomeBuddy suites;
  - every other suite;
  - every locale file;
  - all product code.
- **Prove the shared mock now does the work.**
  - Re-run the baseline set and show identical pass and skip counts, plus the one new `it`. The region query in `GameShellAccessibility.test.tsx` must still pass.
  - Temporarily make `enTranslate` ignore `options` again. Record that the new `it` fails on interpolation, and that "Question 1 of 3", "You got 2 of 3 right!", the SafeExploration announcements and the GameShell region query each fail.
  - Temporarily make it interpolate into the key fallback as well. Record that the new `it` fails on the missing-key assertion.
  - Revert both, and show them green. None of this sabotage is committed.

## Slices

1. Shared mock: `enTranslate` takes an optional options argument and fills every `{{name}}` from a plain object's defined, non-null values. A string argument is ignored, the key fallback is unchanged, and the doc comment is updated. One new `it` in `QuestionScreen.test.tsx` pins these semantics.
2. Remove the four redundant local wrappers (QuestionScreen, QuizSummary, SafeExplorationControls, GameShellAccessibility) in favour of `enMock()`. Delete QuizSummary's now-unused import, and fix the stale comment in ActivityPlayerCompletionLatch. No existing assertion changes.
3. Two-phase proof: show the baseline pass and skip counts plus the new case, then show that reverting the shared interpolation fails the new case and the four formerly wrapped suites. Show that interpolating into the key fallback fails the new case. Revert and show green.

## Behaviors

1. `t("activityPlayer.questionOf", { current: 1, total: 3 })` under `enMock` returns "Question 1 of 3".
2. A placeholder whose option is absent, `undefined` or `null` stays as `{{name}}` in the output.
3. A key that is not in `en/common.json` returns the key unchanged, whatever options are passed.
4. A string second argument leaves the resolved string uninterpolated.
5. QuestionScreen, QuizSummary, SafeExplorationControls and GameShellAccessibility pass on the shared mock alone, with no local `t`.
6. `npm run test:unit` reports the baseline pass and skip counts plus the one new case.

## Acceptance criteria

- The delivery evidence shows baseline and post-change pass and skip counts for `npm run test:unit` and for each suite named in the Approach. They match, apart from the one new `it`.
- The evidence shows both temporary breaks failing the cases named in the Approach, and none of that sabotage is in the diff.
- `src/test/i18nMock.ts` gains interpolation only, under the rules in the Approach. There is no plural, `returnObjects`, `defaultValue` or language handling, and the lookup and fallback are unchanged.
- QuestionScreen, QuizSummary, SafeExplorationControls and GameShellAccessibility no longer define a local `t`. Each mocks `react-i18next` with `enMock()`. QuizSummary no longer imports `enTranslate`.
- `K2InstantFeedbackQuiz.test.tsx`, the BiomeBuddy suites and every locale file are not in the diff.
- The only change in `ActivityPlayerCompletionLatch.test.tsx` is the doc comment above `describe("replay results copy (#791)")`.
- No existing assertion, matcher or expected value changes in any file. The one addition is the new `it` in `QuestionScreen.test.tsx`, and formatter reflow is the only allowed incidental difference.
- No `.skip`, `.only`, `skipIf`, timeout or retry is added.
- `npm run lint`, `npm run format:check`, `npm run typecheck` and `npm run test:unit` are green.

## Decisions

- Change the shared mock, because four suites carry the same workaround, #952 adds the fourth, and the inventory finds no suite the change would break. Rejected: leaving the local copies in place, which keeps the duplication. Rejected: loading the real `@/i18n` in more suites, as `LearningGameFrame.test.tsx:6` does, which swaps the whole backend for a one-feature gap and changes how every key resolves.
- Interpolation only. Rejected: plural suffixes, because the inventory finds one pair and only skipped suites reach it. Rejected: `returnObjects` and `defaultValue`, because no suite asserts through them, and each would widen the mock without a test that needs it.
- Interpolate only when the options argument is a plain object, because code reached through `enMock` also passes a default string there (`StudentDashboard.tsx:984`). Rejected: indexing into whatever is passed, which would walk a string's characters.
- Leave a placeholder visible when its option is absent, `undefined` or `null`. A missing value then shows up in a failing assertion. i18next 25.7.3 (the locked version) is recalled, not checked against the installed package, to keep `{{name}}` for an absent option and to render an explicit `undefined` or `null` as "". Rejected: copying that empty-string case, because it would hide a missing value behind an assertion that still passes. Rejected: `String(null)`, because "null" on a learner-facing string is never what a test means.
- Replace every occurrence, as `SafeExplorationControls.test.tsx:25` does. Rejected: the first-occurrence `replace` of QuestionScreen and QuizSummary, which would be wrong for a string that repeats a placeholder.
- Pin the semantics with one new `it` in `QuestionScreen.test.tsx`, because Behaviors 2 to 4 are otherwise unobservable from the evidence. Rejected: a new file for the helper, because `touched_paths` may only name paths that exist. Rejected: leaving them to code review alone.
- Keep `K2InstantFeedbackQuiz`'s wrapper, because it exists for Spanish and `returnObjects`, not only for interpolation.
- Change no existing assertion. Rejected: tightening REPLAY-2, REPLAY-3 and REPLAY-5 to exact "+50 points" and "+0 points" now, which is a test-strength change that belongs with a review of those cases.
- Depend on #90 and #161, because both edit files listed here, and #952's GameShell wrapper must exist before this can remove it.

## Open questions

- `QuestionScreen.test.tsx:90-106` (E-10, "i18nKeyFixture resolves via i18n mock") passes only through the missing-key fallback. `src/test/quizFixtures.ts:103-110` uses `games.rhymo.q1.*`, which is not in `en/common.json`; the real path is `content.rhymo.q1.*`. That is a separate defect in what E-10 proves, and this change does not affect it. Should it be its own item?
- Should REPLAY-2, REPLAY-3 and REPLAY-5 assert the exact "+50 points" and "+0 points" once the mock interpolates? Not done here; see Decisions.

## Touched paths

- src/test/i18nMock.ts
- src/components/activities/quiz/__tests__/QuestionScreen.test.tsx
- src/components/activities/quiz/__tests__/QuizSummary.test.tsx
- src/components/games/shared/safeExploration/__tests__/SafeExplorationControls.test.tsx
- src/components/games/__tests__/GameShellAccessibility.test.tsx
- src/pages/__tests__/ActivityPlayerCompletionLatch.test.tsx

## Risks

- **A suite the inventory misread.** The inventory traced imports statically, so a component reached only through string-keyed lazy loading would have been missed. The baseline and post-change counts are the real check. If any suite changes result, stop and diagnose it. Do not adjust the test toward green.
- **Type checking.** `npm run typecheck` checks neither `src/test/` nor any test file, because the root `tsconfig.json` excludes `src/test`, `**/__tests__/**` and `**/*.test.tsx`. Vitest strips types without checking them. So nothing type-checks the new signature, and the suites that import it are the only check. Keep the signature compatible with every current `enTranslate(key)` call.
- **Scheduling.** This item waits for two others. If #952 or #161 changes before it merges, re-read the GameShellAccessibility wrapper and the CompletionLatch comment before editing them.
- **Masking.** A reviewer should confirm that the new interpolation cannot make a missing key pass. A key that is not found must return the key unchanged, even when options are given. The new `it` and the second break exist to show that.
- **Unrun plan.** No test was run for this proposal, because this session could not install dependencies.
- **Issue body.** The request contains no instructions aimed at the agent beyond the task itself.
