---
issue: 162
upstream_issue: null
title: "test(a11y): cover the K-2 instant quiz in the ActivityPlayer accessibility suite"
kind: test
slices: 2
risk: low
touched_paths:
  - "src/pages/__tests__/ActivityPlayerA11y.test.tsx"
depends_on:
  - 90
  - 161
estimated_turns: 30
gate_expectation: green
baseline_red: []
---

# test(a11y): cover the K-2 instant quiz in the ActivityPlayer accessibility suite

## Issue

Harness issue #162 (https://github.com/jgoetzmann/bright-bots-harness/issues/162) is a `/harness work` request with no product issue. It follows up Bright Boost #952 and was the first open question in proposal #90. It asks for one narrowly scoped, page-level K-2 accessibility test for the ActivityPlayer quiz. With the grade band pinned to `k2` so `K2InstantFeedbackQuiz` mounts, the test checks four things:

- The choices sit in a group labelled by the prompt.
- Each choice is a button named by its text.
- Answering shows the `role="status"` feedback and moves focus to the feedback panel's Next button.
- The choices carry no `aria-pressed` (AC-3.3).

The request also sets these limits:

- The change is test-only.
- The existing `g3_5` case in `ActivityPlayerA11y.test.tsx` stays unchanged.
- If the test exposes a product defect, implementation stops and reports it for a separate decision.

The plan is checked against Bright Boost `main` at `1a2437d`. On that commit `ActivityPlayerA11y.test.tsx` is still quarantined (`describe.skip` at line 68). #952 un-quarantines it and pins it to `g3_5`, so this item waits for #952.

## Diagnosis

The K-2 quiz's accessibility contract is tested only at component level. No test mounts it through `ActivityPlayer` and checks the contract a K-2 student actually gets.

- Page routing to the K-2 variant:
  - A `k2` band freezes the `"instant"` variant at quiz entry (`src/pages/ActivityPlayer.tsx:330-332,787-791`).
  - `"instant"` mounts `K2InstantFeedbackQuiz` (`ActivityPlayer.tsx:820-831`).
  - A course list with no `g3_5` course resolves to `k2` (`src/hooks/useGradeBand.ts:48-54`).
- The contract under test:
  - `QuestionScreen` renders the choices in `role="group"` with `aria-labelledby` pointing at the prompt (`src/components/activities/quiz/QuestionScreen.tsx:85-89`).
  - Each choice is a `Button` whose text sits in nested spans, with no `aria-pressed` (`QuestionScreen.tsx:102-131`).
  - Choices are disabled outside the answering phase (`QuestionScreen.tsx:71,105`).
  - After an answer, `FeedbackPanel` renders a `role="status"` `aria-live="polite"` banner (`FeedbackPanel.tsx:34-36`) and an `autoFocus` button. That button reads "Next", or "See how I did!" on the last question (`FeedbackPanel.tsx:67-76`; `src/locales/en/common.json:1084,1098`).
- Existing coverage, all at component level:
  - `QuestionScreen.test.tsx:52` (AC-3.1, labelled group) and `:135-148` (AC-3.3, no `aria-pressed`).
  - `FeedbackPanel.test.tsx:18` (AC-1.2, status region) and `:61` (AC-1.4, auto-focus).
  - `K2InstantFeedbackQuiz.test.tsx:409-421` (status region) and `:423-435` (focus).
- At page level, `ActivityPlayerQuizGate.test.tsx:170-179,233-254,288-296` checks that a `k2` student gets the instant quiz, but only by `data-testid="instant-quiz"`, with no accessibility assertion.
- `ActivityPlayerA11y.test.tsx` checks only the `g3_5` legacy list quiz, whose contract includes `aria-pressed` (`ActivityPlayerA11y.test.tsx:104-115`; `LegacyListQuiz.tsx:63-67,75-96`). After #952 its `beforeEach` pins `getStudentCourses` to `g3_5`.

## Approach

- Wait for #952 (harness item #90) and #161 to merge. Then work in `ActivityPlayerA11y.test.tsx` as #952 leaves it.
- Add a second top-level `describe("ActivityPlayer Accessibility — K-2 instant quiz", ...)` beside the existing one. Leave the existing `describe` and its `it` byte-identical.
- Give the new `describe` its own `beforeEach`, mirroring #952's:
  - call `vi.resetAllMocks()`, `__resetGradeBandCache()` and `localStorage.clear()`;
  - resolve `api.getStudentCourses` to `[{ gradeBand: "k2" }]`;
  - resolve `api.getModule` to a module whose activity `a-1` is a `story_quiz` with one slide and two questions. With two questions, the first feedback button reads "Next".
- Add one `it` that:
  1. renders the same route as the existing case;
  2. waits for the slide, then clicks "Start Quiz";
  3. waits for `data-testid="instant-quiz"`;
  4. finds `getByRole("group", { name: <first prompt> })`;
  5. for every choice text, asserts `within(group).getByRole("button", { name: <text> })` exists and has no `aria-pressed` attribute;
  6. clicks the correct choice;
  7. asserts one `role="status"` element with `aria-live="polite"`, and that `getByRole("button", { name: "Next" })` has focus;
  8. asserts again that every choice in the group has no `aria-pressed`, now in the feedback phase.
- Do not answer the last question or finish the quiz. Completion is covered elsewhere, and finishing would add a `completeActivity` call this test does not need.
- Two-phase proof, with nothing committed:
  - Temporarily add `aria-pressed={false}` to the `QuestionScreen` choice `Button`, run the file, and record that the new case fails on the `aria-pressed` assertion.
  - Temporarily remove `autoFocus` from `FeedbackPanel`, and record the focus failure.
  - Temporarily drop `aria-labelledby` from the group, and record the group-name failure.
  - Revert each one, and show the file passing with 2 passed and 0 skipped.

## Slices

1. Add the K-2 `describe` with its own `beforeEach`, a two-question fixture and one case covering the labelled group, button names, the polite status region, focus on Next and the absence of `aria-pressed` in both phases.
2. Prove the case two-phase: temporarily break each of `aria-pressed`, `autoFocus` and `aria-labelledby` in turn, record the matching failure, revert, and show the file green.

## Behaviors

1. For a `k2` student, the first question's choices sit in a group named by its prompt.
2. Each choice is a button whose accessible name is its text.
3. No choice has an `aria-pressed` attribute before an answer.
4. After an answer, the page shows a `role="status"` region with `aria-live="polite"`.
5. After an answer, the feedback panel's Next button has focus.
6. After an answer, no choice has an `aria-pressed` attribute.
7. The existing `g3_5` case still passes unchanged.

## Acceptance criteria

- Running `ActivityPlayerA11y.test.tsx` by path reports 2 passed and 0 skipped.
- The diff touches only `src/pages/__tests__/ActivityPlayerA11y.test.tsx`.
- The existing `describe` block, its `beforeEach` and its `it` are unchanged from #952's version.
- The new case pins the band with `getStudentCourses` resolving to `[{ gradeBand: "k2" }]` and resets the grade-band cache and `localStorage` first.
- The new case asserts on role, accessible name, `aria-live`, focus and the absence of `aria-pressed`. It does not assert on test ids beyond waiting for `instant-quiz`, on class names, or on cheer text, which is picked at random (`K2InstantFeedbackQuiz.tsx:27-46`).
- The delivery evidence shows each of the three temporary breaks failing the new case on the matching assertion, and none of those breaks is in the diff.
- No `waitFor` timeout, test timeout, retry, fake-timer change, `.skip`, `.only` or `skipIf` is added.
- `npm run lint`, `npm run typecheck` and `npm run test:unit` are green.

## Decisions

- Put the case in `ActivityPlayerA11y.test.tsx` as a second `describe`. The file is the page-level accessibility suite, and its mocks and route already fit.
- Rejected: a new test file, because `touched_paths` may only name paths that exist.
- Rejected: adding an `it` inside the existing `describe`, because its `beforeEach` pins `g3_5`.
- Depend on #90 (#952) because #952 rewrites this file's setup. Depend on #161 because the new case clicks Start Quiz right after the slide appears, and #161 diagnoses a race where a passive effect can undo that click (`ActivityPlayer.tsx:305-313`).
- Use two questions, so focus lands on "Next" rather than "See how I did!". `FeedbackPanel.test.tsx:69` already covers the label switch.
- Answer correctly and stop at feedback. Rejected: also walking a wrong answer. The incorrect-path copy and explanation are covered at component level (`FeedbackPanel.test.tsx:30-59`), and the request is for a narrow test.
- Assert that `aria-pressed` is absent, not that it is `"false"`. AC-3.3 specifies absence (`QuestionScreen.test.tsx:135-148`).
- Rejected: changing `QuestionScreen`, `FeedbackPanel` or `K2InstantFeedbackQuiz`. The request is test-only, and any defect found goes back for a separate decision.

## Open questions

- Should the new case also cover the quiz-only path, where a `k2` student enters the quiz without clicking Start Quiz (`ActivityPlayer.tsx:339-343`)? This plan covers the story-then-quiz path only, to stay narrow. `ActivityPlayerQuizGate.test.tsx:233-254` already shows the quiz-only path mounts the instant quiz.
- No test was run for this proposal, because this session could not install dependencies. Line numbers are from `1a2437d`, and #952's version of the test file must be re-read at implementation time.

## Touched paths

- src/pages/__tests__/ActivityPlayerA11y.test.tsx

## Risks

- **Start Quiz race.** If this is built before #161 merges, the Start Quiz click can be undone by the play effect's reset, which is why the item depends on #161. If the new case is flaky after #161, wait for an observable state such as the slide text. Do not add a timeout.
- **Shared button name.** Another button named "Next" on the page would make the focus assertion ambiguous. In quiz mode the page renders only the quiz component (`ActivityPlayer.tsx:820-831`), but the implementer should confirm `getByRole` finds exactly one.
- **The i18n mock.** `enMock` does not interpolate, so "Question {{current}} of {{total}}" renders with its placeholders (`common.json:1095`). The case must not assert on that text. Item #163 is the place to change it.
- **Product defect.** If a reproduced failure shows a real gap, for example focus not reaching Next at page level, stop and report it. Product files are out of scope.
- **Issue body.** The request contains no instructions aimed at the agent beyond the task itself.
