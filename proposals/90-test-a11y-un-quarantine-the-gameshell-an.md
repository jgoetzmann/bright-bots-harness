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
estimated_turns: 20
gate_expectation: green
baseline_red: []
---

# test(a11y): un-quarantine the GameShell and ActivityPlayer accessibility suites

## Issue

Harness issue #90 (https://github.com/jgoetzmann/bright-bots-harness/issues/90) tracks finding 4 of audit #63 (`audit:63:4`). There is no product issue. The finding is rated high severity and titled "Both dedicated accessibility test suites are quarantined". It cites `describe.skip`, `GameShell`, and `src/components/games/shared/GameShell.tsx:420-430`. The finding describes a problem, not a plan.

## Diagnosis

The two suites are `src/components/games/__tests__/GameShellAccessibility.test.tsx:26` and `src/pages/__tests__/ActivityPlayerA11y.test.tsx:68`. Both are `describe.skip`. Both carry a `TODO(green-ci-recovery)` comment, and neither comment gives the real cause. The product code is not at fault. Each test has stale setup or a stale assertion.

What the skip leaves unguarded: `GameShell.tsx:418-432` moves focus to the Start button, then the game region, then the results heading. Only the skipped suite tests this. A search for `toHaveFocus` in `src/` finds GameShell only at `GameShellAccessibility.test.tsx:66,70,73`.

**GameShellAccessibility.** The TODO at lines 19-25 says the briefing is now a stepper that hides "Start Mission" behind slides. That is not true of the current component:
- The briefing branch at `GameShell.tsx:436-509` is a single view.
- The `Start Mission` button carries `ref={startButtonRef}` directly (`GameShell.tsx:497-504`).
- `Button` forwards its ref (`src/components/ui/button.tsx:44-69`).
- A search for `stepper|briefingStep|slideIndex|nextSlide` under `src/components/games` matches only the TODO comment itself.

The assertion that actually breaks is line 73, `getByRole("heading", { name: /games\.shared\.amazing/i })`:
- The file mocks i18n with `enMock` (lines 6-9). `enTranslate` returns the English string from `en/common.json` (`src/test/i18nMock.ts:35-37,52-53`).
- `games.shared.amazing` is `"Amazing!"` (`src/locales/en/common.json:1629`).
- A score of 1/1 gives 100%, which is 3 stars with the default thresholds (`GameShell.tsx:391,410-411`). So the heading at `GameShell.tsx:287-294` reads "Amazing!", not the raw key.
- Lines 64-65 were already updated to the English "Start Mission". Line 73 was left on the old raw-key assertion.

The other assertions line up with the source:
- On-mount focus: `GameShell.tsx:419-421`.
- Instructions text: `ControlInstructions.tsx:52` via `controlInstructionsData.ts:24-27`.
- Game region: `role="region"`, `aria-label={`${title} game area`}`, `tabIndex={-1}` (`GameShell.tsx:550-556`).

**ActivityPlayerA11y.** The TODO at lines 62-67 blames `getModule` returning undefined. But the test already sets `getModule` before rendering, at line 74. The real failures are two others.

1. **The mock reset wipes the `getStudentCourses` stub.**
   - `beforeEach` calls `vi.resetAllMocks()` (lines 69-71). Under vitest `^3.1.3` (`package.json:179`), that drops the `.mockResolvedValue([])` set on `getStudentCourses` in the mock factory (line 16). It then returns `undefined`.
   - `ActivityPlayer` calls `useGradeBand()` (`ActivityPlayer.tsx:78`), and `useModuleAccess` calls `useGradeBandState` (`useModuleAccess.ts:112`). Both reach `loadBand`, which runs `api.getStudentCourses().then(...)` (`src/hooks/useGradeBand.ts:55`) inside an effect (`useGradeBand.ts:111`). With `undefined` returned, that throws "Cannot read properties of undefined". This is the error the TODO quotes, but the TODO pins it on the wrong call.
   - The active sibling suite `ActivityPlayerQuizGate.test.tsx:117-123` re-arms `getStudentCourses` after the same reset.
   - `getAvatar` is harmless. It is only called for specialization slugs, and the call is wrapped in `Promise.resolve` (`useModuleAccess.ts:117,133`).
2. **The assertions describe the grade 3–5 quiz, but the test renders the K-2 quiz.**
   - Even with the stub restored as `[]`, the band resolves to `k2` (`useGradeBand.ts:57-58`). The variant freezes to `"instant"` (`ActivityPlayer.tsx:283-285`), which mounts `K2InstantFeedbackQuiz` (`ActivityPlayer.tsx:755-767`).
   - Its choices are rendered in `QuestionScreen.tsx:102-131`. They have no `aria-pressed`, and the label text sits in a nested `<span>` (lines 116-130). So `getByText("4")` returns the span, and the `aria-pressed` checks at lines 112 and 115 fail.
   - Leaving `aria-pressed` off is deliberate. `QuestionScreen.test.tsx:135-148` (AC-3.3) asserts that K-2 choices do not have it.
   - The attributes this test checks (`role="group"` labelled by the prompt, plus `aria-pressed` on a `Button` whose text is a direct child) exist in `LegacyListQuiz.tsx:68-97`. That component is mounted for `g3_5` only (`ActivityPlayer.tsx:769-785`).
   - `LegacyListQuiz.test.tsx:104-105` names this suite as the legacy variant's "assertion diff empty" check.

## Approach

Change only the two test files. Keep every behavioural assertion. Fix the setup, fix the one stale matcher, remove `.skip`, and replace the wrong TODO comments.

- **GameShellAccessibility.test.tsx:**
  - Change `describe.skip` to `describe`.
  - Delete the TODO at lines 19-25.
  - Change the line 73 matcher from `/games\.shared\.amazing/i` to `/amazing/i`, the same English-string style as line 65.
  - Leave all three `toHaveFocus()` assertions as they are.
- **ActivityPlayerA11y.test.tsx:**
  - Change `describe.skip` to `describe`.
  - After `vi.resetAllMocks()`, add `__resetGradeBandCache()` (imported from `@/hooks/useGradeBand`), `localStorage.clear()`, and `vi.mocked(api.getStudentCourses).mockResolvedValue([{ gradeBand: "g3_5" }])`. This matches `ActivityPlayerQuizGate.test.tsx:118-122`.
  - Replace the TODO at lines 62-67 with a short comment: the band is pinned to `g3_5` because these assertions describe the legacy list quiz's contract.
  - Leave lines 73-116 unchanged.

This fixes the tests rather than the product because, on reading, the product already behaves as the tests expect. Changing the product would be wrong in both cases:
- In `GameShell`, the focus code is correct.
- In `ActivityPlayer`, adding `aria-pressed` to the K-2 choices would break an existing spec (AC-3.3).

## Slices

1. Un-skip `GameShellAccessibility.test.tsx`: remove the stale TODO, change the results-heading matcher to the English string, and keep all focus assertions.
2. Un-skip `ActivityPlayerA11y.test.tsx`: re-arm `getStudentCourses` after `vi.resetAllMocks()`, pinned to the `g3_5` band; reset the grade-band cache and `localStorage`; replace the stale TODO; leave all assertions unchanged.

## Behaviors

1. Running `GameShellAccessibility.test.tsx` executes one test, and it passes. It is not reported as skipped.
2. When `GameShell` mounts with a briefing, the "Start Mission" button has focus and the briefing's control instructions are visible.
3. After "Start Mission" is activated, the region named "Access Test game area" has focus.
4. After the game finishes with a full score, the "Amazing!" results heading has focus.
5. Running `ActivityPlayerA11y.test.tsx` executes one test, and it passes. It is not reported as skipped.
6. For a `g3_5` student, the quiz choices sit in a group named by the question prompt.
7. After a `g3_5` student picks a choice, that choice has `aria-pressed="true"` and an unpicked choice has `aria-pressed="false"`.
8. `npm run test:unit` reports two fewer skipped tests, and no previously passing test fails.

## Acceptance criteria

- Neither file contains `describe.skip`, `it.skip`, `.skip(`, or `.only(`.
- The diff touches exactly the two files under `## Touched paths`. No product source changes.
- In `GameShellAccessibility.test.tsx`, the only assertion change is the name matcher on the results-heading query. The three `toHaveFocus()` calls are still there.
- In `ActivityPlayerA11y.test.tsx`, the body of the `it(...)` block (lines 73-116 today) has no changes.
- The `beforeEach` in `ActivityPlayerA11y.test.tsx` re-arms `api.getStudentCourses` after `vi.resetAllMocks()` and resolves it to a `g3_5` course.
- Neither file keeps a `TODO(green-ci-recovery)` comment.
- No `waitFor` timeout, test timeout, or retry option is added or raised.
- `npm run lint`, `npm run typecheck`, and `npm run test:unit` are green.

## Decisions

- Fix the tests, not the product. `GameShell.tsx:418-432` already produces the focus order the suite asserts. A product change would have no defect to fix.
- Delete both TODO comments instead of editing them. Their stated causes are wrong: there is no stepper briefing (`GameShell.tsx:436-509`), and `getModule` is already mocked (`ActivityPlayerA11y.test.tsx:74`).
- Use `/amazing/i` for the heading matcher, not a raw i18n key and not `enTranslate(...)` imported into the file. It matches the English-literal style of line 65. Going back to a key-echoing mock was rejected because `i18nMock.ts:1-18` documents the move away from asserting on keys.
- Pin the ActivityPlayer suite to the `g3_5` band instead of rewriting its assertions for K-2. Its assertions are the legacy variant's recorded contract (`LegacyListQuiz.test.tsx:104-105`). K-2 choices are specified to have no `aria-pressed` (`QuestionScreen.test.tsx:135-148`).
- Rejected: adding `aria-pressed` to `QuestionScreen` choices. It would contradict AC-3.3 and change product behaviour to satisfy a test.
- Keep `vi.resetAllMocks()` and re-arm the stubs after it, as `ActivityPlayerQuizGate.test.tsx:117-123` does. Switching to `vi.clearAllMocks()` was rejected: the band would still resolve to K-2, and it would diverge from the sibling suite.
- Add `__resetGradeBandCache()` and `localStorage.clear()` even though the file has only one test. The grade band is now an explicit input to this test, the band cache is module-level state (`useGradeBand.ts:25,31`), and the sibling suite resets both.
- Leave `(api.getModule as any)` at `ActivityPlayerA11y.test.tsx:74` alone. It is not part of the defect.
- Leave the other quarantined suites (`TeacherDashboard`, `StudentSignup`, `AvatarPicker`, `LanguageToggle`) out of scope. They are not the accessibility suites this finding names.
- Don't add a K-2 page-level case to `ActivityPlayerA11y`. It goes beyond un-quarantining. It is raised under Open questions instead.

## Open questions

- Should `ActivityPlayerA11y` also cover the default K-2 instant variant at page level (labelled group, focus moving to feedback)? Today that is covered only at component level (`QuestionScreen.test.tsx:52-65`, `K2InstantFeedbackQuiz.test.tsx:434`). `docs/audits/k8-engagement-audit.md:286` once planned to extend this suite when K-2 feedback shipped.
- No test or gate was run for this proposal. There was no shell available at this stage. The failure causes above come from reading the source, and `gate_expectation: green` assumes the untouched tree is green in CI.

## Touched paths

- src/components/games/__tests__/GameShellAccessibility.test.tsx
- src/pages/__tests__/ActivityPlayerA11y.test.tsx

## Risks

- **Race in the ActivityPlayer test.** The `g3_5` band has to resolve before the "Start Quiz" click freezes the quiz variant (`ActivityPlayer.tsx:283-285,723-726`). Access does not wait on the band for modules with no `level` (`useModuleAccess.ts:196`, `moduleAccess.ts:306-310`). The band request is started first and settles one microtask ahead of `setLoading(false)`. The active `g3_5` case in `ActivityPlayerQuizGate.test.tsx:137-147` runs the same sequence. If the test is flaky, fix it by waiting for an observable state. Raising a timeout would break the never-list.
- **Unverified diagnosis.** The failure causes come from reading the source, not from running the tests. If an un-skipped suite shows another failure, it must be diagnosed and not worked around by loosening assertions. If the failure is a real defect in `GameShell.tsx:418-432` or in the quiz components, stop and re-propose, because those paths are not in scope.
- **Formatting hunks.** Formatting the two changed files may reflow existing lines that aren't prettier-clean today (for example `GameShellAccessibility.test.tsx:28-32,56` and `ActivityPlayerA11y.test.tsx:47-48,59,84`). Expect whitespace-only hunks in these two files and nowhere else.
- **Where to look hardest.** Check that no assertion was weakened: the three `toHaveFocus()` calls stay, the ActivityPlayer `it` body is unchanged, and no skip, `.only`, or timeout was added.
- **Issue body.** It contains no instructions aimed at the agent. The command table and links are the harness's own boilerplate. None of it was acted on.
