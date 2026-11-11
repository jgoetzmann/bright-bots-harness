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

**The inventory.** It was done by reading the code, not by running tests. For each suite, the trace followed its imports (skipping anything the suite mocks), collected every `t(key, {...})` reachable from what it renders, matched those keys against the placeholders in `en/common.json`, and read each assertion against the result. 44 files import the mock. One of them, `scripts/patch-i18n-en-mock.cjs`, is a codemod that rewrites mocks and never runs one. That leaves 43 test suites, and their verdicts split as follows:

- **36 suites render nothing interpolated that they assert on.** Either they call only plain keys, or the interpolated text renders but no assertion reads it. Examples:
  - `ActivityPlayerQuizGate` renders "+50 points" but never asserts it.
  - `TeacherClassesLoading` renders "N students" but asserts on exact class names.
  - `SkyShield` renders its reveal and exit copy but asserts only on exact emoji lanes.
- **One suite passes either way.** `GameShellReducedEffects.test.tsx:51` asserts `toHaveAccessibleDescription(/reduces motion/i)`. The description comes from `"Reduces motion, … Source: {{source}}."` (`common.json:1660`), so the regex matches with or without interpolation.
- **One suite has a comment that becomes false, while its assertions still hold.** `ActivityPlayerCompletionLatch.test.tsx:201-206` says the mock does not interpolate. The results screen calls `t("activityPlayer.points", { count })` (`ActivityPlayer.tsx:477-479`; `"+{{count}} points"` at `common.json:1072`). With interpolation, REPLAY-2 and REPLAY-3 render "+50 points" instead of "+{{count}} points", and that still satisfies:
  - `getByText(/points/)` at line 243, which matches one element because the toast is mocked and never renders;
  - `stringContaining("points")` at lines 255-257 and 263-265;
  - `queryByText(/points/)` being null on the replay branches at line 237, because those branches never call `points`.
- **Three suites carry local wrappers that the change would make redundant.**
  - `QuestionScreen.test.tsx:10-33` and `QuizSummary.test.tsx:7-30` spread `enMock()` and replace each `{{k}}` once with `String.replace`.
  - `SafeExplorationControls.test.tsx:19-44` builds its own mock and replaces every `{{name}}` with split/join. Its comment at lines 16-18 explains why the wrapper exists.
  - They assert interpolated text: "Question 1 of 3" (`QuestionScreen.test.tsx:55` ← `QuestionScreen.tsx:79-82` ← `common.json:1095`), "You got 2 of 3 right!" (`QuizSummary.test.tsx:44`), and the baseline announcements in SafeExplorationControls (lines 169, 286-288, 297-299 and 438).
  - No asserted key repeats a placeholder, so the first-occurrence and all-occurrence wrappers give the same result here.
- **One wrapper must stay.** `K2InstantFeedbackQuiz.test.tsx:49-66` resolves English and Spanish and handles `returnObjects` for the cheer pools. The shared mock does neither, so that wrapper is not redundant.
- **One skipped suite flips from fail to pass.** `GameShellAccessibility.test.tsx:70` names the region "{{title}} game area" under the shared mock (`common.json:1649`). #952 un-skips that suite and adds its own interpolating wrapper. After this change, that wrapper is redundant as well.
- **No suite goes from passing to failing.** No test asserts raw `{{` text, and none uses snapshots.

**The other options the mock ignores.**

- **Plurals.** `common.json` has exactly one `_one`/`_other` pair, `teacher.lessons.available_*` at lines 425-426. Its only caller is `src/components/TeacherDashboard/MainContent.tsx:78`, which both TeacherDashboard suites mock, and both suites are skipped. Every other `count` call these suites reach points at a single plain string. Interpolating `{{count}}` therefore matches what real i18next shows for those strings.
- **`returnObjects`.** `breakSuggestions` (`ActivityPlayer.tsx:189-191`) shows only in the every-third-completion break dialog, and every suite clears `sessionStorage`. No `enMock` suite answers a K-2 question, so none reaches the cheer pools. Nothing reads either value, so nothing depends on them.
- **`defaultValue`.** Calls at `GameShell.tsx:613`, `ModuleDetail.tsx:173` and `TryDemo.tsx:282` would fall back to a default, but none is asserted.

The recommendation follows from this: make the shared mock interpolate, delete the three redundant wrappers and the one #952 adds, keep K2's wrapper, and add no plural, `returnObjects` or `defaultValue` handling.

## Approach

- **Wait** for #90 (#952), which adds the GameShellAccessibility wrapper, and for #161, which edits `ActivityPlayerCompletionLatch.test.tsx`.
- **Record the baseline.** Run `npm run test:unit` on the untouched tree. Also run the five changed suites by path, plus `GameShellReducedEffects`, `K2InstantFeedbackQuiz` and `ActivityPlayerQuizGate`. Record their pass and skip counts.
- **Change `src/test/i18nMock.ts`.**
  - Change the signature to `enTranslate(key: string, options?: Record<string, unknown>)`.
  - Resolve the string as today.
  - When `options` is given and the string was found, replace every `{{name}}` whose `name` is an own key of `options` with a value that is not `undefined` by `String(value)`.
  - Leave every other placeholder in place, so a missing value stays visible in a failing assertion.
  - Leave the key fallback unchanged. Never interpolate into a returned key.
  - `enMock()` keeps `t: enTranslate`, so every user gets interpolation with no call-site change.
  - Update the file's doc comment to say that it fills `{{name}}` options and nothing else.
- **Delete the redundant wrappers.**
  - `QuestionScreen.test.tsx:10-33` and `QuizSummary.test.tsx:7-30` become `vi.mock("react-i18next", async () => { const { enMock } = await import("@/test/i18nMock"); return enMock(); })`. Keep their top-level `enTranslate` imports, which assertions use.
  - In `SafeExplorationControls.test.tsx`, lines 16-44 become the same three-line `enMock()` mock, and the stale comment goes.
  - In `GameShellAccessibility.test.tsx`, as #952 leaves it, the local `t` and its comment become the plain `enMock()` mock again.
- **Fix the stale comment** at `ActivityPlayerCompletionLatch.test.tsx:201-206` so it says what the REPLAY cases check. Change no assertion.
- **Leave alone:**
  - `K2InstantFeedbackQuiz.test.tsx`, because of Spanish and `returnObjects`;
  - every other suite;
  - every locale file;
  - all product code.
- **Prove the shared mock now does the work.**
  - Re-run the baseline set and show identical pass and skip counts, with the region query at `GameShellAccessibility.test.tsx:70` still passing.
  - Temporarily make `enTranslate` ignore `options` again. Record that "Question 1 of 3", "You got 2 of 3 right!", the SafeExploration announcements and the GameShell region query each fail.
  - Revert, and show them green. None of this sabotage is committed.

## Slices

1. Shared mock: `enTranslate` takes an optional options object and fills every `{{name}}` with a defined value from it. The key fallback is unchanged, and the doc comment is updated.
2. Remove the four redundant local wrappers (QuestionScreen, QuizSummary, SafeExplorationControls, GameShellAccessibility) in favour of `enMock()`, and fix the stale comment in ActivityPlayerCompletionLatch. No assertion changes.
3. Two-phase proof: show the same pass and skip counts as the baseline, then show that reverting only the shared interpolation fails the four formerly wrapped suites on their interpolated assertions, then revert and show green.

## Behaviors

1. `t("activityPlayer.questionOf", { current: 1, total: 3 })` under `enMock` returns "Question 1 of 3".
2. A placeholder whose option is absent or `undefined` stays as `{{name}}` in the output.
3. A key that is not in `en/common.json` returns the key unchanged, whatever options are passed.
4. QuestionScreen, QuizSummary, SafeExplorationControls and GameShellAccessibility pass on the shared mock alone, with no local `t`.
5. `npm run test:unit` reports the same pass and skip counts as the untouched tree.
6. K2InstantFeedbackQuiz's Spanish and `returnObjects` cases still pass on its own wrapper.

## Acceptance criteria

- The delivery evidence shows baseline and post-change pass and skip counts for `npm run test:unit` and for each suite named in the Approach, and they match.
- The evidence shows the temporary revert of the shared interpolation failing the four formerly wrapped suites on interpolated text, and none of that sabotage is in the diff.
- `src/test/i18nMock.ts` gains interpolation only. There is no plural, `returnObjects`, `defaultValue` or language handling, and the lookup and fallback are unchanged.
- QuestionScreen, QuizSummary, SafeExplorationControls and GameShellAccessibility no longer define a local `t`. Each mocks `react-i18next` with `enMock()`.
- `K2InstantFeedbackQuiz.test.tsx` and every locale file are not in the diff.
- The only change in `ActivityPlayerCompletionLatch.test.tsx` is the comment at lines 201-206.
- No assertion, matcher or expected value changes in any file. Formatter reflow is the only allowed incidental difference.
- No `.skip`, `.only`, `skipIf`, timeout or retry is added.
- `npm run lint`, `npm run typecheck` and `npm run test:unit` are green.

## Decisions

- Change the shared mock rather than leave the local copies in place. Four suites carry the same workaround, #952 adds the fourth, and the inventory finds no suite that the change would break.
- Rejected: loading the real `@/i18n` in more suites, as `LearningGameFrame.test.tsx:6` does. That swaps the whole backend for a one-feature gap, and it changes how every key resolves.
- Interpolation only. Rejected: plural suffixes, because the inventory finds one pair and only skipped suites reach it. Rejected: `returnObjects` and `defaultValue`, because no suite asserts through them. Each would widen the mock's behaviour without a test that needs it.
- Replace only defined, present values, and leave other placeholders visible. Rejected: i18next-style empty-string substitution, which would hide a missing value behind an assertion that still passes.
- Replace every occurrence, as `SafeExplorationControls.test.tsx:25` does. Rejected: the first-occurrence `replace` of QuestionScreen and QuizSummary, which is wrong for a string that repeats a placeholder.
- Keep `K2InstantFeedbackQuiz`'s wrapper. It exists for Spanish and `returnObjects`, not only for interpolation.
- Change no assertion. Rejected: tightening REPLAY-2 and REPLAY-3 to "+50 points" now. That is a test-strength change, and it belongs with a review of those cases rather than in a mock change.
- Depend on #90 and #161. Both edit files listed here, and #952's GameShell wrapper must exist before this can remove it.

## Open questions

- `QuestionScreen.test.tsx:90-106` (E-10, "i18nKeyFixture resolves via i18n mock") passes only through the missing-key fallback. `src/test/quizFixtures.ts:103-110` uses `games.rhymo.q1.*`, which is not in `en/common.json`; the real path is `content.rhymo.q1.*`. That is a separate defect in what E-10 proves, and this change does not affect it. It should be its own item.
- Should REPLAY-2 and REPLAY-3 assert the exact "+50 points" once the mock interpolates? Not done here; see Decisions.
- The inventory traced imports statically. A component that a suite reaches only through string-keyed lazy loading would have been missed. The baseline and post-change counts in the Approach are the check.
- No test was run for this proposal, because this session could not install dependencies.

## Touched paths

- src/test/i18nMock.ts
- src/components/activities/quiz/__tests__/QuestionScreen.test.tsx
- src/components/activities/quiz/__tests__/QuizSummary.test.tsx
- src/components/games/shared/safeExploration/__tests__/SafeExplorationControls.test.tsx
- src/components/games/__tests__/GameShellAccessibility.test.tsx
- src/pages/__tests__/ActivityPlayerCompletionLatch.test.tsx

## Risks

- **A suite the inventory misread.** The inventory comes from reading the code, so the baseline and post-change counts are the real check. If any suite changes result, stop and diagnose it. Do not adjust the test toward green.
- **Type checking.** `npm run typecheck` checks neither `src/test/` nor any test file, because the root `tsconfig.json` excludes `src/test`, `**/__tests__/**` and `**/*.test.tsx`. Vitest strips types without checking them. So nothing type-checks the new signature, and the suites that import it are the only check. Keep the signature compatible with every current `enTranslate(key)` call.
- **Scheduling.** This item waits for two others. If #952 changes before it merges, re-read the GameShellAccessibility wrapper before removing it.
- **Masking.** A reviewer should confirm that the new interpolation cannot make a missing key pass. A key that is not found must return the key unchanged, even when options are given.
- **Issue body.** The request contains no instructions aimed at the agent beyond the task itself.
