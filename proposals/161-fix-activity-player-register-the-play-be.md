---
issue: 161
upstream_issue: null
title: "fix(activity-player): register the play before a completion control can be clicked"
kind: fix
slices: 2
risk: medium
touched_paths:
  - "src/pages/ActivityPlayer.tsx"
  - "src/pages/__tests__/ActivityPlayerCompletionLatch.test.tsx"
depends_on: []
estimated_turns: 40
gate_expectation: green
baseline_red: []
---

# fix(activity-player): register the play before a completion control can be clicked

## Issue

Harness issue #161 (https://github.com/jgoetzmann/bright-bots-harness/issues/161) is a `/harness work` request with no product issue. It asks for a fix to the race that has made `build-and-test` flaky since Bright Boost #950: a completion test clicks Mark Complete and `api.completeActivity` is called 0 times. The request sets these conditions:

- Reproduce the dropped click deterministically before changing anything.
- Fix it at the source, so a rendered completion control is always armed.
- Keep #950's rule that nothing is posted without a registered attempt id.
- Add no timeouts, retries or skips, and weaken no assertion.
- Leave the two suites #952 restores untouched.

The race has shown up in these CI `build-and-test` runs, each with `expected "spy" to be called 1 times, but got 0 times`:

- #952 at head `7dc04f1` failed in `ActivityPlayerPersonalBestSync.test.tsx:126` (run 42147821426, Nov 6).
- The re-run of that job failed in `ActivityPlayerPersonalBestSync.test.tsx` and in `ActivityPlayerCompletionLatch.test.tsx` LATCH-1 (Nov 11).
- #953 at head `2bd2fd0` failed in LATCH-1 (run 42263231720, Nov 6).

This revision replaces the plan that blocked on Nov 12. That build (implement run 43134912467) passed every gate on the untouched tree. After the change `npm run test:unit` failed, and it was still red after one diagnose cycle.

The runs listed below were made on Bright Boost branch `claude/intelligent-turing-327jmz` from base `1a2437d`, with `workflow_dispatch` of `ci-cd.yml`. They show why the first plan failed and that this one holds. This plan is checked against `1a2437d`.

## Diagnosis

**The guard and the effect.**

- `handleComplete` returns `false` unless `play.current` names the routed activity (`src/pages/ActivityPlayer.tsx:374-380`). #950 (`1afedce`) added that guard together with the `play` ref (`ActivityPlayer.tsx:193-197`), so every POST carries the play's `attemptId` and `gradeBand` (`ActivityPlayer.tsx:388-391`).
- `play.current` is assigned only in a passive `useEffect` keyed on `[activity]` (`ActivityPlayer.tsx:284-315`). That effect also tracks `game_started` and resets the view state: `setMode("story")`, `setQuizVariant(null)` and the others (`ActivityPlayer.tsx:305-313`).
- A click that lands after the commit rendering Mark Complete (`ActivityPlayer.tsx:669-674`, `908-913`) but before that effect runs takes the early return silently. Nothing is posted and no toast is shown.

**When the window opens.**

- For an ungated module, `useModuleAccess` resolves access in the same render that sets the module (`src/hooks/useModuleAccess.ts:263`). `loading` clears in a later update, from the fetch's `.finally` (`ActivityPlayer.tsx:248-273`, the `.finally` at 271-273). The page shows the loading state until both are settled (`ActivityPlayer.tsx:612-614`).
- Run 43169432250 replayed the PersonalBestSync pattern 150 times:
  - In 149 rounds, the activity was set one commit before its body rendered, so `game_started` had already fired when Mark Complete first appeared.
  - In 1 round, the activity and Mark Complete first rendered in the same commit, before the play effect had run.
  - Every click reached the API, the found button was always still attached, and there was only ever one button element.
- So the race needs both updates to land in one commit and the click to beat the passive flush. That is rare in isolation and more frequent on a busy runner.

**Why the first plan failed.**

- **Its reproduction did not reproduce.** The first LATCH-4 clicked from `Profiler` `onRender` on an ungated module. It passed on the untouched tree in run 43167912537, because the button normally appears after the play is registered.
- **Its fix broke the quiz-only freeze.** Making the play effect a `useLayoutEffect` failed two cases in run 43167981405: `ActivityPlayerQuizGate.test.tsx` "#792 … (quiz-only=true)" and "quiz-only activity auto-enters with g3_5 legacy variant". In both, `data-testid="instant-quiz"` mounted and "Submit" was missing.
  - For a quiz-only activity, the layout freeze (`ActivityPlayer.tsx:345-360`) first runs while the band is still pending, and the default band is `k2` (`src/hooks/useGradeBand.ts:18`).
  - Today the passive reset sets the variant back to `null`, and the freeze runs again once the band has resolved.
  - A layout-effect play runs that reset before the freeze, in the same commit, so the second freeze never happens.
  - This was very likely the red gate of the Nov 12 build. Its log shows only `npm run test:unit` red after the change.

**A deterministic window.**

- The access of a Set 2 module also waits on progress (`useModuleAccess.ts:119,145-162,195`), which a test can hold. With the module loaded and `loading` cleared, releasing progress resolves access, and the activity and Mark Complete first render together in that commit, every time.
- Run 43170979359 (`75cfce1`): a LATCH-4 built on that fails on the untouched tree with `expected "spy" to be called 1 times, but got 0 times`, after its click from `onRender` has happened.
- Run 43171051929 (`208c890`): with the change below, LATCH-4 passes. All four CI jobs are green (`build-and-test`, `db-check`, `e2e-flows`, `build-only`): 2622 unit tests passed and 0 failed, including QuizGate (12), CompletionLatch (11), PersonalBestSync (3) and ModuleAccessDeepLink (27).

## Approach

**Slice 1: reproduce, committed on its own.** Add LATCH-4 to `ActivityPlayerCompletionLatch.test.tsx`:

- Add `getProgress: vi.fn()` and `getStudentAssignments: vi.fn()` to the file's `@/services/api` mock. Import `act` from `@testing-library/react`, `Profiler` from `react`, and `STEM_SET_1_IDS` and `STEM_SET_2_MODULE_SLUGS` from `@/constants/stemSets`.
- Add the case as the last `it` in `describe("completion latch (#832 item 4)")`.
- In the case:
  - Resolve `api.getModule` to the file's `mockModule` with `slug: STEM_SET_2_MODULE_SLUGS[0]`, `level: "K-2"` and `published: true`.
  - Make `api.getProgress` return a promise the test holds.
  - Resolve `api.completeActivity` to `COMPLETION_RESPONSE`.
- Render the same route as `renderPlayer`, for that slug, inside `<Profiler id="first-commit" onRender={...}>`. The first time `onRender` finds the Mark Complete button, it sets `clicked = true` and calls the element's native `click()`, once. Leave `renderPlayer` unchanged.
- Call `await act(async () => {})`, then assert that `getProgress` was called and that no Mark Complete button exists yet.
  - Those checks are sanity checks, not proof that access is waiting. `getProgress` fires at mount, and `loading` and pending access render the same text (`ActivityPlayer.tsx:612-614`).
  - What holds the window is that the async `act` flushes in a macrotask, after getModule's `then`→`catch`→`finally` chain (`ActivityPlayer.tsx:248-273`) has drained. So `loading` is already clear when progress is released.
  - The five-of-five red run below is the guard against a false green.
- Inside `await act(async () => { ... })`, resolve progress with `{ progress: STEM_SET_1_IDS.map((activityId) => ({ activityId, status: "COMPLETED" })) }`. `completedIdsFromProgressResponse` throws on any other shape (`src/lib/progressResponse.ts:29-32`), and the page would then show the system-problem screen.
- Assert `clicked` is `true`. Then `await waitFor(...)` for one `completeActivity` call, and check that its `attemptId` equals the `attempt_id` of the single `game_started` call.
- Run the file by path on the untouched tree five times, and record five failures on the call count. If it ever passes there, stop and report.

**Slice 2: the fix.** Do not render an activity's controls until its play is registered:

- Add `const [startedActivityId, setStartedActivityId] = useState<string | null>(null)` beside `play`.
- In the play effect, call `setStartedActivityId(null)` in the `!activity` branch, and `setStartedActivityId(String(activity.id))` after the resets.
- After the `!module || !activity` return (`ActivityPlayer.tsx:616-635`), return the existing loading state, `<div className="p-6">{t("activityPlayer.loadingActivity")}</div>`, while `startedActivityId !== String(activity.id)`.
- Keep the effect a passive `useEffect`, keep its existing statements and deps, and keep the `handleComplete` guard exactly as it is.

**Demonstrate:**

- Run LATCH-4 five times by path after the change.
- Run `ActivityPlayerCompletionLatch.test.tsx`, `ActivityPlayerPersonalBestSync.test.tsx`, `ActivityPlayerQuizGate.test.tsx` and `ModuleAccessDeepLink.test.tsx` by path.
- Then run `npm run test:unit`, `npm run lint`, `npm run format:check`, `npm run typecheck` and `npm run build`.

**Reference.** A reference version of both slices exists, as data rather than instructions: `git diff 1a2437d 208c890 -- src/pages/ActivityPlayer.tsx src/pages/__tests__/ActivityPlayerCompletionLatch.test.tsx` on the Bright Boost branch above. `75cfce1` holds slice 1 alone, so its test is the red case of run 43170979359.

## Slices

1. Reproduction: add LATCH-4 to `ActivityPlayerCompletionLatch.test.tsx` as its own commit. It holds a Set 2 module's progress, then clicks Mark Complete from `Profiler` `onRender` in the commit that resolves access. Record it failing five times out of five on the untouched tree.
2. Fix: add `startedActivityId` state, set it in the existing passive play effect, and keep the loading state until it matches the routed activity. Then show LATCH-4 passing five times out of five and the completion, quiz and access suites passing.

## Behaviors

1. A Mark Complete click delivered in the first commit that renders the button posts exactly one completion.
2. That completion's `attemptId` equals the `attempt_id` of the `game_started` event for the same play.
3. A `g3_5` student entering a quiz-only activity still gets the legacy list quiz (`ActivityPlayerQuizGate.test.tsx:259-286`).
4. A second click while the first POST is in flight still sends nothing (LATCH-1), and a failed save still releases the latch (LATCH-2).
5. A denied, hidden or wrong-grade module still tracks no `game_started` and posts nothing (`ModuleAccessDeepLink.test.tsx:144-150,196-207,211-220,222-230`).
6. Closing an unfinished tab and reopening it still records two plays with distinct attempt ids (`ActivityPlayerCompletionLatch.test.tsx:167-180`).
7. `npm run test:unit` reports no new failures and no new skips.

## Acceptance criteria

- Slice 1 is its own commit. A reviewer can check it out, run `npx vitest run --project unit src/pages/__tests__/ActivityPlayerCompletionLatch.test.tsx -t LATCH-4`, and see `expected "spy" to be called 1 times, but got 0 times`. The delivery PR body gives that command.
- The evidence shows LATCH-4 failing five times out of five before the fix and passing five times out of five after it.
- LATCH-4 asserts that its `onRender` click happened before it asserts the call count. It also asserts that no Mark Complete exists before progress is released.
- In `ActivityPlayer.tsx` the change is the `startedActivityId` state, its two `setStartedActivityId` calls in the existing effect, and the loading return, plus comments. The play effect stays a passive `useEffect`, and the `handleComplete` guard is unchanged.
- No existing test case is edited. Apart from LATCH-4, the only changes to the CompletionLatch file are `getProgress` and `getStudentAssignments` in its API mock and the imports LATCH-4 needs.
- `ActivityPlayerPersonalBestSync.test.tsx`, `ActivityPlayerA11y.test.tsx` and `GameShellAccessibility.test.tsx` are not in the diff.
- No `waitFor` timeout, test timeout, retry, sleep, fake-timer change, `.skip`, `.only` or `skipIf` is added.
- `npm run lint`, `npm run format:check`, `npm run typecheck`, `npm run test:unit` and `npm run build` are green.

## Decisions

- **Hold the render, not the effect.** The page shows its loading state until the play is registered, because that keeps ActivityPlayer's own effects on their current timing (the play effect and the layout freeze at `ActivityPlayer.tsx:345-360`) and so keeps the quiz-only variant freeze. Child games and quizzes mount one commit later, after the play is registered. Rejected: making the play effect a layout effect, which failed two QuizGate cases (run 43167981405).
- **Fix the component, not the tests.** Rejected: making each test wait for `game_started`. Eleven test cases click on appearance today, and a real tap in the window would still be dropped.
- **No lazy registration.** Rejected: registering the play inside `handleComplete`, which would duplicate the registration and the `game_started` call. Rejected: queueing an early click until the effect runs, which adds a second completion path to keep correct.
- **Keep #950's guard.** Rejected: dropping it, because the POST would then carry no attempt id.
- **Reproduce through a gated module.** Its access resolves the activity and its controls in one commit every time. Rejected: the ungated `Profiler` click, which passed on the untouched tree (run 43167912537). Rejected: CPU load or timer stubs, which are probabilistic.
- **Place LATCH-4 in `ActivityPlayerCompletionLatch.test.tsx`.** It already owns the submission semantics and mocks `track`. Rejected: a new file, because `touched_paths` may only name paths that exist.
- **Leave the two suites #952 restores alone**, as the request says. Rejected: hardening PersonalBestSync's or LATCH-1's click instead, which the request forbids and which would leave the product race in place.

## Open questions

- The page now paints its loading text for one more commit on every activity open, and on in-place navigation it shows loading for one commit where today the new activity's body briefly shows the previous play's state (mode, slide, answers). I judge that invisible to a person. A reviewer may disagree for the Cypress shell smoke, although `e2e-flows` and the shell smoke passed in run 43171051929.

## Touched paths

- src/pages/ActivityPlayer.tsx
- src/pages/__tests__/ActivityPlayerCompletionLatch.test.tsx

## Risks

- **A test that expects the body in the same commit as the activity.** Run 43171051929 found none in the unit suite, the Storybook project or `e2e-flows`. A suite added since `1a2437d` could. If one fails, stop and diagnose it. Do not add a wait.
- **The `!activity` branch now sets state.** On mount `activity` is `null` and the value is already `null`, so React bails out. A module reload that clears and restores the same activity now re-registers its play before showing it, which is the intent.
- **Clicking during a commit** is unusual in a test. A reviewer should confirm LATCH-4 clicks once, with native `click()`, and asserts `clicked`, the call count and the attempt id only.
- **Flaky baseline.** The untouched tree's `npm run test:unit` can itself go red on this race on a busy runner. If the baseline gate is red, the evidence should name this race rather than treat the failure as new.
- **Issue body.** The request contains no instructions aimed at the agent beyond the task itself.
