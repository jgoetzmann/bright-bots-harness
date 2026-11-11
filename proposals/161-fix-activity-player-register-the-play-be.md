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
- Leave the two suites that #952 restores untouched.

Every observed failure is a CI `build-and-test` run that reported `expected "spy" to be called 1 times, but got 0 times`:

- #952 at head `7dc04f1` failed in `ActivityPlayerPersonalBestSync.test.tsx:126` (run 42147821426, attempt 1, Nov 6).
- The re-run of that job on Nov 11 failed in `ActivityPlayerPersonalBestSync.test.tsx` and in `ActivityPlayerCompletionLatch.test.tsx` LATCH-1.
- #953 at head `2bd2fd0` failed in LATCH-1 (run 42263231720, Nov 6). That branch carries none of #952's changes.

Before #950, none of the 23 CI runs from Oct 1 to Nov 4 failed this way. Every `main` push since has been green. The harness's own gate run of `npm run test:unit` passed on #952's head before delivery.

This plan is checked against Bright Boost `main` at `1a2437d` (after #957).

## Diagnosis

- `handleComplete` returns `false` unless `play.current` names the routed activity (`src/pages/ActivityPlayer.tsx:374-380`). #950 (`1afedce`) added that guard together with the `play` ref (`ActivityPlayer.tsx:193-197`). The guard ensures every POST carries the registered play's `attemptId` and `gradeBand` (`ActivityPlayer.tsx:388-391`).
- `play.current` is assigned only in a passive `useEffect` keyed on `[activity]` (`ActivityPlayer.tsx:284-315`). React runs passive effects after the commit. In the commit where `activity` first becomes non-null, the DOM already holds every control that reaches `handleComplete`, but `play.current` is still `null`. Those controls are:
  - the two Mark Complete buttons (`ActivityPlayer.tsx:669-674`, `908-913`);
  - the quiz `onComplete` callbacks (`ActivityPlayer.tsx:826-828`, `845`);
  - the game `onComplete` callbacks (`ActivityPlayer.tsx:863`, `871`, `878`).
- A click inside that window takes the early return silently. Nothing is posted, no toast is shown, and the latch is untouched: `completingRef` is set only after the guard (`ActivityPlayer.tsx:383-384`).
- The completion tests click as soon as the button exists:
  - `ActivityPlayerPersonalBestSync.test.tsx:123-126`, `156-159` and `174-177`.
  - `ActivityPlayerCompletionLatch.test.tsx:117-123` (LATCH-1), `141-146` (LATCH-2) and `186-188` (LATCH-3).
  - Every REPLAY case, through `completeWith` (`ActivityPlayerCompletionLatch.test.tsx:218-225`).

  Whether the passive effect has run by the time `findByRole` resolves depends on scheduler timing, so a busy runner sometimes loses the click. The failure DOM in the CI log fits this: an idle Mark Complete button with `aria-busy="false"`, and no results screen.
- The same effect resets the view state for a fresh play: `setMode("story")`, `setQuizVariant(null)` and the others (`ActivityPlayer.tsx:305-313`). A Start Quiz click (`ActivityPlayer.tsx:787-791`) that lands before the effect runs is therefore undone. `ActivityPlayerQuizGate.test.tsx:112-117,222-225` and `ActivityPlayerA11y.test.tsx:87-95` click Start Quiz right after the first slide appears. This comes from reading the code; no CI failure of these suites has been seen.
- `ModuleAccessDeepLink.test.tsx:175-179` asserts that `game_started` was tracked as soon as `findByText` resolves. That assertion also depends on the passive effect having run. This also comes from reading the code.
- For a person the window is short. A tap that lands inside it is still dropped with no feedback, so this is the same defect.
- The component already handles the same kind of timing elsewhere: the quiz variant is frozen in a `useLayoutEffect` (`ActivityPlayer.tsx:345-360`), so it is settled before the browser paints.

## Approach

- **Reproduce first.** Add a LATCH-4 case to `ActivityPlayerCompletionLatch.test.tsx`. It clicks Mark Complete in the first commit that renders the button, before passive effects can run:
  - Wrap the rendered tree in `<Profiler id="first-commit" onRender={...}>` from `react`. React calls `onRender` during the commit's layout phase, after the subtree's layout effects and before its passive effects.
  - In `onRender`, the first time `screen.queryByRole("button", { name: "Mark Complete" })` returns an element, call that element's native `click()` exactly once. Do not use `fireEvent`, which wraps the call in `act`.
  - Then `await waitFor(() => expect(api.completeActivity).toHaveBeenCalledTimes(1))`. Also assert that the call's `attemptId` equals the `attempt_id` of the single `game_started` call on the mocked `track`.
  - Run the file by path on the untouched tree five times. Record that LATCH-4 fails every time with 0 calls. If it ever passes on the untouched tree, stop and report. Do not ship the fix without this proof.
- **Fix.** Change the play-registration effect at `ActivityPlayer.tsx:284` from `useEffect` to `useLayoutEffect`. Keep its body, its `[activity]` deps, the guard at line 290 and the `eslint-disable` line. Layout effects run inside the commit, so by the time any event can reach a newly rendered control, `play.current` is set and the view resets are applied. `useLayoutEffect` is already imported (`ActivityPlayer.tsx:4`).
- **Keep the guard** in `handleComplete` (`ActivityPlayer.tsx:374-380`) exactly as it is, so nothing posts without a registered attempt.
- **Leave every existing suite unchanged:** PersonalBestSync, the other LATCH and REPLAY cases, ModuleAccessDeepLink, QuizGate and ActivityPlayerA11y. They become deterministic without edits.
- **Demonstrate:**
  - Run LATCH-4 five times by path after the change and record five passes.
  - Run `ActivityPlayerCompletionLatch.test.tsx`, `ActivityPlayerPersonalBestSync.test.tsx`, `ActivityPlayerQuizGate.test.tsx` and `ModuleAccessDeepLink.test.tsx` by path.
  - Then run `npm run test:unit`, `npm run lint` and `npm run typecheck`.

## Slices

1. Reproduction: add LATCH-4, a click from `Profiler` `onRender` on the first commit, to `ActivityPlayerCompletionLatch.test.tsx`, and record it failing five times out of five on the untouched tree with `completeActivity` called 0 times.
2. Fix: register the play in a layout effect by changing `useEffect` to `useLayoutEffect` at `ActivityPlayer.tsx:284`, then show LATCH-4 passing five times out of five and the existing completion, quiz and access suites passing.

## Behaviors

1. A Mark Complete click delivered in the same commit that first renders the button posts exactly one completion.
2. That completion's `attemptId` equals the `attempt_id` of the `game_started` event for the same play.
3. A second click while the first POST is in flight still sends nothing (LATCH-1).
4. A failed save still releases the latch, and the retry reuses the attempt id (LATCH-2).
5. A denied, hidden or wrong-grade module still tracks no `game_started` and posts nothing (`ModuleAccessDeepLink.test.tsx:144-150,203-229`).
6. Closing an unfinished tab and reopening it still records two plays with distinct attempt ids (`ActivityPlayerCompletionLatch.test.tsx:167-180`).
7. `npm run test:unit` reports no new failures and no new skips.

## Acceptance criteria

- The delivery evidence shows LATCH-4 failing on the untouched tree in five runs out of five with `expected "spy" to be called 1 times, but got 0 times`, recorded before the product change.
- The evidence shows LATCH-4 passing in five consecutive runs of the file by path after the change.
- In `ActivityPlayer.tsx` the only code change is `useEffect` to `useLayoutEffect` on the play-registration effect, plus a comment update if needed. The `handleComplete` guard is unchanged.
- No existing test case is edited. `ActivityPlayerPersonalBestSync.test.tsx`, `ActivityPlayerA11y.test.tsx` and `GameShellAccessibility.test.tsx` are not in the diff.
- No `waitFor` timeout, test timeout, retry, sleep, fake-timer change, `.skip`, `.only` or `skipIf` is added.
- LATCH-4 clicks once, with the element's native `click()`, from inside `Profiler` `onRender`.
- `npm run lint`, `npm run typecheck`, `npm run test:unit` and `npm run build` are green.

## Decisions

- Fix the component, not the tests. The button is live before it is armed, which is a product defect the tests expose.
- Rejected: making each test wait for `game_started` before clicking. Eleven test cases click this way today (three in PersonalBestSync, LATCH-1 to LATCH-3, and five REPLAY cases), every future completion test would need to know about this hidden precondition, and a real tap in the window would still be dropped.
- Use `useLayoutEffect` on the existing effect.
- Rejected: registering the play lazily inside `handleComplete`. It would duplicate the registration and the `game_started` track call, and the view resets would still race Start Quiz.
- Rejected: writing `play.current` during render. Refs must not be written during render, and StrictMode renders twice.
- Rejected: disabling the button until a "registered" state flips. That adds state and a render for something a layout effect already guarantees.
- Keep #950's guard. Rejected: dropping it to make the race go away. The POST would then carry no attempt id, which is what #950 exists to prevent.
- Reproduce with `Profiler` `onRender`, because it runs at a fixed point in the commit.
- Rejected: CPU load or a `performance.now` stub, which are both probabilistic.
- Rejected: `scheduler/unstable_mock`, which would mean reworking how React Testing Library drives React in this file.
- Put LATCH-4 in `ActivityPlayerCompletionLatch.test.tsx`. That file already owns the submission semantics, mocks `track` (lines 16-20) and has `renderPlayer` (lines 78-95). Rejected: a new test file, because `touched_paths` may only name paths that exist.
- Leave the `[slug, lessonId, activityId]` reset effect (`ActivityPlayer.tsx:202-205`) passive. On first mount it runs before the module can load, and the play effect already resets `completingRef` itself (line 313).
- Do not touch the two suites #952 restores. The request says so, and they need no change.

## Open questions

- The reproduction assumes that, in the installed React 18.3.1, `Profiler` `onRender` fires during the layout phase of a commit that a descendant's own state update started, after that descendant's layout effects and before its passive effects. That comes from React's commit order as I understand it, not from a run. The five-out-of-five red step is the check, and implementation stops if it does not hold.
- `game_started` moves from a passive effect into the commit, so it fires roughly one task earlier. `track` calls `posthog.capture` directly (`src/lib/analytics.ts:248-252`), and I found no consumer that depends on it firing after paint. A reviewer may want to confirm that against the analytics dashboard expectations in `docs/analytics.md`.
- No test or gate was run for this proposal, because this session could not install dependencies. Line numbers are from `1a2437d`.

## Touched paths

- src/pages/ActivityPlayer.tsx
- src/pages/__tests__/ActivityPlayerCompletionLatch.test.tsx

## Risks

- **Synchronous re-render.** The resets at `ActivityPlayer.tsx:305-313` now run in a layout effect, so on a fresh play they trigger a synchronous re-render before paint rather than after it. On first mount they set initial values. On in-place navigation they clear the previous activity's state before it can be seen, which is the intent.
- **Effect order with the quiz freeze.** Both effects are now layout effects, and the play effect runs first because it is declared first. In the first commit of a quiz-only activity, `setQuizVariant(null)` and then the freeze updater at `ActivityPlayer.tsx:330-332` combine into the same frozen variant. Previously the variant was frozen, reset, then frozen again one task later. Look hardest at QuizGate's freeze cases (`ActivityPlayerQuizGate.test.tsx:186-200,303-326`).
- **Flaky baseline.** The untouched tree's `npm run test:unit` can itself go red on this race on a busy runner. If the baseline gate is red, the evidence should name this race rather than treat the failure as new.
- **StrictMode replay.** Layout effects also replay under StrictMode in development. The existing guard at `ActivityPlayer.tsx:290` keeps that to one play.
- **Clicking during a commit** is unusual in a test. A reviewer should confirm that LATCH-4 clicks exactly once, with native `click()`, and asserts only the POST count and the attempt id.
- **Issue body.** The request contains no instructions aimed at the agent beyond the task itself.
