---
issue: 90
upstream_issue: null
title: "test(a11y): un-quarantine the GameShell and ActivityPlayer accessibility suites"
kind: test
slices: 2
risk: low
touched_paths:
  - "src/components/games/__tests__/GameShellAccessibility.test.tsx"
  - "src/pages/__tests__/ActivityPlayerA11y.test.tsx"
depends_on: []
estimated_turns: 30
gate_expectation: green
baseline_red: []
---

# test(a11y): un-quarantine the GameShell and ActivityPlayer accessibility suites

## Issue

Harness issue #90 (https://github.com/jgoetzmann/bright-bots-harness/issues/90) tracks finding 4 of audit #63 (`audit:63:4`). There is no product issue. The finding is rated high severity and titled "Both dedicated accessibility test suites are quarantined". Its "Paths" field lists `describe.skip`, `GameShell`, and `src/components/games/shared/GameShell.tsx:420-430`. That line range is out of date: the focus logic now sits at `GameShell.tsx:525-539`. The finding describes a problem, not a plan.

This revision is checked against the clone's `main` at `1afedce` (`.git/logs/HEAD:1`, cloned from the fork `jgoetzmann-bot/brightboost`). The reviewer says this is main after #947. It is partial recovery for #106: it un-quarantines two suites and leaves the rest of #106 open (see Decisions).

## Diagnosis

The two suites are `src/components/games/__tests__/GameShellAccessibility.test.tsx:26` and `src/pages/__tests__/ActivityPlayerA11y.test.tsx:68`. Both use `describe.skip`, and both carry a `TODO(green-ci-recovery)` comment that names the wrong cause. On reading, neither failure is a product defect. Each comes from stale test setup or a stale assertion.

What the skip leaves unguarded: `GameShell.tsx:525-539` moves focus to the Start button, then the game region, then the results heading. Under `src/components/games`, `toHaveFocus` hits GameShell only in this suite (`GameShellAccessibility.test.tsx:66,70,73`). The other hits are `MoveMeasureKeyboard.test.tsx` and a Storybook story for a different component.

**GameShellAccessibility: two stale pieces, not a stepper.**
- The TODO at lines 19-25 says the briefing is a stepper that hides "Start Mission". It is not. The briefing branch (`GameShell.tsx:543-633`) is a single view, and the `Start Mission` `Button` carries `ref={startButtonRef}` directly (`GameShell.tsx:620-628`). `Button` forwards its ref (`src/components/ui/button.tsx:44,69`).
- **First failure: line 70, the region name.**
  - The region's label is now `t("games.shared.area", { title, defaultValue: "{{title}} game area" })` (`GameShell.tsx:678-687`). The earlier proposal cited a template literal here, so this code has changed since that proposal was written.
  - The file mocks i18n with the shared `enMock` (lines 6-9), whose `t` is `enTranslate` (`src/test/i18nMock.ts:52-53`). `enTranslate(key)` takes no options and does no interpolation (`i18nMock.ts:35-37`).
  - `games.shared.area` is `"{{title}} game area"` (`src/locales/en/common.json:1649`), so the region is named literally `{{title}} game area`. `getByRole("region", { name: /access test game area/i })` finds nothing and throws.
  - The gap is already known in the repo. `SafeExplorationControls.test.tsx:16-18` says enMock "ignores interpolation", and it, `QuestionScreen.test.tsx:10-35` and `QuizSummary.test.tsx:12-16` each wrap `enTranslate` with a local interpolating `t`. `LearningGameFrame.test.tsx:57` makes the same region query and passes only because it loads the real i18n (`LearningGameFrame.test.tsx:6`).
- **Second failure: line 73, the heading name.** It is hidden behind the first, because line 70 throws first.
  - `getByRole("heading", { name: /games\.shared\.amazing/i })` expects the raw key. enMock returns `"Amazing!"` (`common.json:1640`).
  - A 1/1 score is 100%, which earns 3 stars under the default thresholds `[30, 60, 90]` (`GameShell.tsx:477,499-523`). So the results `h2` reads "Amazing!" (`GameShell.tsx:335-348`).
  - Lines 64-65 were already moved to the English string "Start Mission". Line 73 was missed.
- The remaining assertions match the source:
  - On-mount focus: `GameShell.tsx:526-528`.
  - Instructions text: rendered by `ControlInstructions` at `GameShell.tsx:604-608`. The custom `keyboard` list passes through `controlInstructionsData.ts:15-17`.
  - Region focus: `GameShell.tsx:531-533` with `tabIndex={-1}` at 686.
  - Heading focus: `GameShell.tsx:536-538` with `tabIndex={-1}` at 337.
  - The `matchMedia` stub (lines 28-32) supplies the `addEventListener` that `useReducedGameEffects.ts:33-41` calls.

**ActivityPlayerA11y: the test renders the K-2 quiz, but its assertions describe the grade 3-5 quiz.**
- The TODO at lines 62-67 blames `getModule`. That is wrong: the test already sets `getModule` at line 74, before rendering.
- **The `getStudentCourses` stub is wiped.**
  - `beforeEach` calls `vi.resetAllMocks()` (lines 69-71). Under vitest `^3.1.3` (`package.json:179`), that drops the `.mockResolvedValue([])` set in the factory (line 16), so the call returns `undefined`.
  - Grade-band loading has changed since the TODO was written. `withRequestDeadline` defers the call (`src/lib/requestDeadline.ts:25-28`), and `loadCourses` rejects a non-array with `"Invalid course list"` (`src/hooks/useGradeBand.ts:63-73`).
  - The hook catches that rejection and reports `status: "failed"` with the `k2` fallback band (`useGradeBand.ts:18,161-164`). So the TODO's quoted "Cannot read properties of undefined" should no longer occur on this path. This is from reading the code; it has not been run.
- **The page still renders, as `k2`.**
  - `mockModule` has no `level`, so `gradeBandAffectsAccess(undefined)` is false (`src/lib/moduleAccess.ts:287-295,306-310`). A failed band therefore does not block access (`src/hooks/useModuleAccess.ts:196-199`).
  - `test-module` is not specialization-gated or progression-gated, so neither `getAvatar` nor `getProgress` is called (`useModuleAccess.ts:117-119`).
  - The story renders. Clicking "Start Quiz" freezes the variant from the band at that moment (`src/pages/ActivityPlayer.tsx:330-332,787-791`): `k2` gives `"instant"`, which mounts `K2InstantFeedbackQuiz` (`ActivityPlayer.tsx:820-832`).
  - Restoring the stub as `[]` would still give `k2` (`useGradeBand.ts:48-53`).
- **Where it breaks.**
  - The K-2 `QuestionScreen` does have the labelled group (`QuestionScreen.tsx:85-89`), so line 104 passes.
  - Its choice `Button` has no `aria-pressed`, and the label sits in a nested `<span>` (`QuestionScreen.tsx:102-131`, span at 129). So `getByText("4")` returns the span, and line 112 fails.
  - Leaving `aria-pressed` off is specified: `QuestionScreen.test.tsx:135-148` (AC-3.3) asserts K-2 choices do not have it.
  - The contract this test checks exists in `LegacyListQuiz.tsx`: `role="group"` labelled by the prompt (63-67), and `aria-pressed={selected}` on a `Button` whose text is a direct child (75-96). `LegacyListQuiz` is mounted only when the band is `g3_5` (`ActivityPlayer.tsx:834-850`).
  - `LegacyListQuiz.test.tsx:106-107` names this suite as the legacy variant's "assertion diff empty" check.
- The active sibling `ActivityPlayerQuizGate.test.tsx` re-arms the stubs after the same reset (lines 120-127). Its `g3_5` case (186-200) uses the same render, story, and "Start Quiz" sequence as this suite.

**No product defect found.** The `k2` content fallback on a failed band is deliberate (`useGradeBand.ts:104`), and so is freezing the quiz variant at quiz entry (`ActivityPlayerQuizGate.test.tsx:303-326`).

## Approach

Only the two test files change. Every focus, accessible-group, and selection-state assertion is kept. No timeout or retry is added. Implementation must first reproduce the diagnosed failures and only then fix the setup.

- **Reproduce first, in each file.**
  - Remove only `.skip`. Run the file by path (`npx vitest run --project unit <path>`) and record the first failing assertion and its message in the delivery evidence.
  - Expected results:
    - GameShell: the `getByRole("region", …)` query at line 70 throws. RTL lists a region named `{{title}} game area`.
    - ActivityPlayer: line 112 fails because the element is `<span>4</span>` with no `aria-pressed`.
  - If the observed failure differs, stop and re-diagnose. Do not adjust the test toward green.
- **GameShellAccessibility.test.tsx:**
  - Change the `react-i18next` mock to keep `enMock()`'s exports but use a local `t`. That `t` resolves through `enTranslate` and replaces each `{{name}}` with the matching option value, following `SafeExplorationControls.test.tsx:19-29`.
  - Re-run and record that line 73 now fails, with the heading named "Amazing!".
  - Change the line 73 matcher from `/games\.shared\.amazing/i` to `/amazing/i`, the same English-string style as line 65. Of the four result headings (`common.json:1640-1643`), only the 3-star one contains "amazing".
  - Delete the TODO at lines 19-25 and replace `describe.skip` with `describe`.
  - Leave lines 64-73 as they are apart from that one matcher. The `/access test game area/i` region matcher and all three `toHaveFocus()` calls stay.
- **ActivityPlayerA11y.test.tsx:**
  - In `beforeEach`, after `vi.resetAllMocks()`, add three calls, matching `ActivityPlayerQuizGate.test.tsx:121-126`:
    - `__resetGradeBandCache()`, imported from `@/hooks/useGradeBand`.
    - `localStorage.clear()`.
    - `vi.mocked(api.getStudentCourses).mockResolvedValue([{ gradeBand: "g3_5" }])`.
  - Replace the TODO at lines 62-67 with a short comment: the band is pinned to `g3_5` because these assertions are the legacy list quiz's contract.
  - Replace `describe.skip` with `describe`. Leave the `it(...)` body (lines 73-116) unchanged.
- **Demonstrate.**
  - Run each file by path and record "1 passed, 0 skipped" for each.
  - Run `npm run test:unit` and record that the skipped count dropped by two and no previously passing test fails.
  - Run `npm run lint` and `npm run typecheck`.

## Slices

1. GameShellAccessibility: remove `.skip`, reproduce the line-70 region-name failure, add a local interpolating `t` over `enTranslate`, reproduce the line-73 heading failure, change that matcher to `/amazing/i`, delete the stale TODO, keep every focus assertion.
2. ActivityPlayerA11y: remove `.skip`, reproduce the line-112 `aria-pressed` failure on the K-2 span, re-arm `getStudentCourses` to a `g3_5` course after `vi.resetAllMocks()` together with `__resetGradeBandCache()` and `localStorage.clear()`, replace the stale TODO, leave the `it` body unchanged.

## Behaviors

1. Running `GameShellAccessibility.test.tsx` executes one test, it passes, and it is not reported as skipped.
2. When `GameShell` mounts with a briefing, the "Start Mission" button has focus and the briefing's control instructions are visible.
3. After "Start Mission" is activated, the region named "Access Test game area" has focus.
4. After the game finishes with a full score, the "Amazing!" results heading has focus.
5. Running `ActivityPlayerA11y.test.tsx` executes one test, it passes, and it is not reported as skipped.
6. For a `g3_5` student, the quiz choices sit in a group named by the question prompt ("What is 2+2?").
7. After a `g3_5` student picks "4", that choice has `aria-pressed="true"` and the unpicked "3" has `aria-pressed="false"`.
8. `npm run test:unit` reports two fewer skipped tests than the untouched tree, and no previously passing test fails.

## Acceptance criteria

- The delivery evidence shows, for each file, the failing output observed with only `.skip` removed. It matches the Diagnosis: the GameShell region query at line 70, then the heading query at line 73; the ActivityPlayer `aria-pressed` assertion at line 112.
- The delivery evidence shows each file run by path with 1 passed and 0 skipped, plus the `npm run test:unit` totals.
- Neither file contains `describe.skip`, `it.skip`, `.skip(`, `.only(`, or `skipIf`.
- The diff touches exactly the two files under `## Touched paths`. No product source, `src/test/i18nMock.ts`, or locale file changes.
- In `GameShellAccessibility.test.tsx`, the only matcher change is on the results-heading query. The `/start mission/i` and `/access test game area/i` matchers and the three `toHaveFocus()` calls remain.
- The GameShell i18n mock still resolves strings through `enTranslate`. It only adds `{{name}}` replacement from the options object.
- In `ActivityPlayerA11y.test.tsx`, the queries, matchers, and expected values in the `it(...)` block (lines 73-116 today) are unchanged. Formatter reflow is the only allowed difference.
- The `beforeEach` in `ActivityPlayerA11y.test.tsx` calls `vi.resetAllMocks()`, `__resetGradeBandCache()` and `localStorage.clear()`, then resolves `api.getStudentCourses` to `[{ gradeBand: "g3_5" }]`.
- Neither file keeps a `TODO(green-ci-recovery)` comment.
- No `waitFor` timeout, test timeout, retry option, or fake-timer change is added.
- `npm run lint`, `npm run typecheck`, and `npm run test:unit` are green.

## Decisions

- Fix the tests, not the product. `GameShell.tsx:525-539` already produces the focus order the suite asserts, and real i18next interpolates `games.shared.area`. There is no product defect to fix.
- Add an interpolating `t` local to `GameShellAccessibility.test.tsx`, built on `enTranslate`, rather than loosening the region matcher to `/game area/i`. The looser matcher would stop checking that the title appears in the region's name.
- Rejected: changing the shared `src/test/i18nMock.ts` to interpolate. It is outside the two-file scope, and it would change behaviour for every enMock user. It is raised under Open questions.
- Rejected: importing the real `@/i18n` as `LearningGameFrame.test.tsx:6` does. It swaps the whole translation backend for a two-key problem and differs from the file's existing mock.
- Use `/amazing/i` for the heading, not the raw key and not an imported `enTranslate(...)` call. It matches line 65's English-literal style and still picks out only the 3-star heading (`common.json:1640-1643`).
- Pin ActivityPlayerA11y to the `g3_5` band rather than rewriting its assertions for K-2. Its assertions are the legacy variant's recorded contract (`LegacyListQuiz.test.tsx:106-107`), and K-2 choices are specified to have no `aria-pressed` (`QuestionScreen.test.tsx:135-148`).
- Rejected: adding `aria-pressed` to `QuestionScreen` choices. It would contradict AC-3.3 and change product behaviour to satisfy a test.
- Keep `vi.resetAllMocks()` and re-arm after it, as the sibling suite does (`ActivityPlayerQuizGate.test.tsx:120-127`). Switching to `vi.clearAllMocks()` was rejected: the `[]` stub still yields `k2` (`useGradeBand.ts:48-53`), and it would diverge from the sibling.
- Add `__resetGradeBandCache()` and `localStorage.clear()` even though there is one test. The band is now an explicit input, the course cache and in-flight map are module-level state keyed on `localStorage` (`useGradeBand.ts:20,26,29-40`), and the sibling resets both.
- Make reproduction a required step before any fix, as the reviewer asked. The diagnosis comes from reading code. The GameShell line-70 cause was missed by the previous proposal because the source changed since it was written.
- Delete both TODO comments rather than editing them. Their stated causes are wrong: there is no stepper (`GameShell.tsx:543-633`), and `getModule` is already mocked (`ActivityPlayerA11y.test.tsx:74`).
- Leave `(api.getModule as any)` at `ActivityPlayerA11y.test.tsx:74` alone. It is not part of the defect.
- Treat this as partial recovery for #106. The suites still quarantined there stay out of scope and open:
  - `src/pages/tests/TeacherDashboard.test.tsx:65`
  - `src/pages/__tests__/TeacherDashboard.test.tsx:69`
  - `src/pages/__tests__/StudentSignup.test.tsx:37`
  - `src/components/__tests__/AvatarPicker.test.tsx:32`
  - `src/components/tests/LanguageToggle.test.tsx:66`
  - the `it.skip` at `src/pages/__tests__/ModuleDetail.test.tsx:78`
- Don't add a K-2 page-level case to ActivityPlayerA11y. It goes beyond un-quarantining and is raised under Open questions.

## Open questions

- Is the fork's `main` at `1afedce` the same as upstream main after #947? This session had no shell, so I could not read git history to confirm which commit #947 is or what it changed. If upstream has moved further, the line numbers above need rechecking at implementation time.
- I could not open #106 in this session. The reference to it, and the list of remaining suites, follow the reviewer's notes and the current `describe.skip` / `it.skip` hits in `src/`.
- Should the shared `enMock` interpolate `{{name}}` options, so suites stop carrying local copies (`QuestionScreen.test.tsx:10-35`, `SafeExplorationControls.test.tsx:19-29`, and now this one)? That is a separate scope decision.
- Should ActivityPlayerA11y also cover the default K-2 instant variant at page level (labelled group, focus moving to feedback)? Today that is covered only at component level (`QuestionScreen.test.tsx:52-65`, `K2InstantFeedbackQuiz.test.tsx:434`). `docs/audits/k8-engagement-audit.md:286` once planned to extend this suite when K-2 feedback shipped.
- No test or gate was run for this proposal. The failure causes come from reading the source, and `gate_expectation: green` assumes the untouched tree is green in CI.

## Touched paths

- src/components/games/__tests__/GameShellAccessibility.test.tsx
- src/pages/__tests__/ActivityPlayerA11y.test.tsx

## Risks

- **Race in the ActivityPlayer test.** The `g3_5` band must resolve before the "Start Quiz" click freezes the variant (`ActivityPlayer.tsx:330-332,787-791`). Access does not wait on the band for a module with no `level` (`useModuleAccess.ts:196-199`). The band chain is microtasks only (`requestDeadline.ts:25-29`, `useGradeBand.ts:64-77,157-160`), and the active `g3_5` case at `ActivityPlayerQuizGate.test.tsx:186-200` relies on the same ordering. If the test is flaky, wait for an observable state. Raising a timeout would break the never-list.
- **Masked failures.** The GameShell heading failure only shows once the region query passes, and other masked failures may exist. Each one surfaced must be diagnosed the same way, not loosened.
- **Product defect.** If a reproduced failure turns out to be a real defect in `GameShell.tsx:525-539`, `QuestionScreen.tsx` or `LegacyListQuiz.tsx`, stop and report it for a separate scope decision. Those paths are not in scope.
- **Local mock drift.** The GameShell file will carry its own interpolating `t`. A reviewer should check it still resolves through `enTranslate` and does nothing beyond `{{name}}` replacement, so it cannot make a missing key pass.
- **Formatting hunks.** Formatting the two changed files may reflow lines that aren't prettier-clean today, for example `GameShellAccessibility.test.tsx:28-32,56,70,73` and `ActivityPlayerA11y.test.tsx:47-48,59,80,84`. Expect whitespace and trailing-comma hunks in these two files and nowhere else.
- **Where to look hardest.**
  - The three `toHaveFocus()` calls and the `/access test game area/i` matcher stay.
  - The ActivityPlayer `it` body is semantically unchanged.
  - No skip, `.only`, or timeout is added.
  - The reproduction output in the evidence matches the Diagnosis.
- **Issue body.** It contains no instructions aimed at the agent. The command table and links are the harness's own boilerplate. None of it was acted on.
