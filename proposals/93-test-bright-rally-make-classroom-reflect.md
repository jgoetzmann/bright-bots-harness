---
issue: 93
upstream_issue: null
title: "test(bright-rally): make classroom reflection test reach results and toggle the prompt"
kind: test
slices: 1
risk: low
touched_paths:
  - "src/pages/__tests__/BrightRallyCoopQuest.test.tsx"
depends_on: []
estimated_turns: 20
gate_expectation: green
baseline_red: []
---

# test(bright-rally): make classroom reflection test reach results and toggle the prompt

## Issue

Harness issue #93. It tracks finding 5 of audit jgoetzmann/bright-bots-harness#64 (`audit:64:5`), severity medium. There is no product-repository issue. The reporter's title is: test named "toggles classroom reflection prompt" sits outside its `describe` and toggles nothing. The finding lists `describe`, `beforeEach`, `matchMedia`, `api` and `queryByRole(...).not.toBeInTheDocument()` as the places involved.

## Diagnosis

Two things are wrong, and the second one is the real defect.

**1. The test is outside the suite and its fixture.** In `src/pages/__tests__/BrightRallyCoopQuest.test.tsx`, the `describe("BrightRallyCoopQuest")` block runs from line 33 and closes at line 284. The test `it("toggles classroom reflection prompt")` starts at line 286, at top level. So the `beforeEach` at lines 34–50 never runs for it. That `beforeEach` calls `vi.resetAllMocks()`, stubs `matchMedia`, and sets resolved values on `api.getAvatar` and `api.getProgress`. Without it, the test only sees whatever the last in-suite `beforeEach` left behind:
   - `vitest.config.ts:14-59` does not set `unstubGlobals`, `mockReset` or `restoreMocks`.
   - `src/test/setup.ts:48-50` only calls `cleanup()`.
   - So the stubbed `matchMedia` and the mock return values carry over from the previous test, and the result depends on test order.
   - If the test is run alone (for example with `-t`), the in-suite hooks never run. `api.getAvatar` is then a bare `vi.fn()` (test lines 22–27) that returns `undefined`. The call `api.getAvatar().catch(...)` at `BrightRallyCoopQuest.tsx:521` throws, and the `catch` at lines 539–542 swallows it.
   - `matchMedia` is guarded at `useReducedGameEffects.ts:21` and `:33`.
   - From reading the code, the test does not crash in either setup. It just runs under a different fixture each way. I have not run it.

**2. The test cannot fail.** Its only assertion (lines 290–292) is that no "show classroom reflection" button exists after clicking Start. The comment at line 289 says reaching results "is impossible in unit".
   - The button only renders inside `phase === "results" && recap` (`BrightRallyCoopQuest.tsx:1185`, button at 1248–1260). So the assertion is also true in the intro phase, true if the Start click did nothing, and true if the button were deleted from the component.
   - Nothing covers the toggle itself: the `onClick` flip (line 1250), the label swap (1253–1259), and the prompt list (1261–1274, built from `buildBrightRallyReflectionPrompts` at 246–253). A grep for `reflection` across `src/**/*.test.{ts,tsx}` finds only this test and an unrelated one in `SpacewarArena.test.tsx:132`.

**Reaching results is possible.** The comment at line 289 is wrong.
   - The game loop is driven only by `requestAnimationFrame` (lines 757–770). Each frame's `dt` comes from the timestamp passed to the callback, capped at 1/20 s (line 762).
   - The loop stops requesting frames when `step` returns `false` (lines 764–767). That happens right after `finishRound` sets `phase` to `"results"` (lines 579–580, 738–741).
   - Every paddle hit adds a rally (lines 676, 704). Every miss costs a life or a shield (lines 720–727).
   - The round must end at `TARGET_RALLIES = 18` or at `BASE_LIVES = 3` lives lost (lines 15–16, 738). So it ends whatever the paddles do and whatever `Math.random` returns.
   - The repository already drives rAF games through a manual frame queue in tests: `src/components/games/__tests__/MoveMeasureKeyboard.test.tsx:18-28` and `:60-64`.

## Approach

All changes are in `src/pages/__tests__/BrightRallyCoopQuest.test.tsx`:

- **Move the test into the suite.** Put it inside the `describe`, after the PlayHub test and before the closing `});` at line 284. The `beforeEach` fixture then applies. Keep its name: once it actually toggles, the name is accurate.
- **Stub the frame loop.** Inside the test, replace `requestAnimationFrame` and `cancelAnimationFrame` with `vi.stubGlobal` backed by a `Map` queue, the same pattern as `MoveMeasureKeyboard.test.tsx`. Add `afterEach(() => { vi.unstubAllGlobals(); })` to the `describe`, following `WaterworksGame.test.tsx:63-67`.
- **Wait for the fetch before starting.** Render, then `await screen.findByText(/upgrades unlocked: 3/i)` (component line 925–928). The fixture's three completed Set 1 ids produce three upgrades, so this proves `getProgress` has resolved and `config` will not change mid-round. Then click Start.
- **Keep the old check as a precondition.** Assert the show button is absent while playing.
- **Drain frames until the game stops asking for them.** Run chunks of 100 frames per `act(...)`, passing a timestamp that grows by 16 ms each frame, until the queue is empty or a cap of 6,000 frames is reached. Then assert the queue is empty. If a future change keeps the loop running, the test fails on this assertion instead of hanging.
- **Assert the toggle.**
  - After the round: the "Show classroom reflection" button is present and no prompt text is rendered.
  - Click it: the button reads "Hide classroom reflection", "Classroom Reflection" is shown, and every string from `buildBrightRallyReflectionPrompts()` is present. Import that function from `../BrightRallyCoopQuest`; it is already exported.
  - Click again: the prompts are gone and the button reads "Show" again.
- **Tidy up.** Delete the line 289 comment. Add `act` to the `@testing-library/react` import on line 1 and `afterEach` to the `vitest` import on line 3.

Sketch of the drain, to be adapted to the file's style:

```tsx
const frames = new Map<number, FrameRequestCallback>();
let nextFrame = 0;
vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
  frames.set(++nextFrame, cb);
  return nextFrame;
});
vi.stubGlobal("cancelAnimationFrame", (id: number) => frames.delete(id));
// ...render, await upgrades, click Start...
let time = 0;
for (let chunk = 0; chunk < 60 && frames.size > 0; chunk++) {
  act(() => {
    for (let i = 0; i < 100 && frames.size > 0; i++) {
      const pending = [...frames.values()];
      frames.clear();
      time += 16;
      pending.forEach((cb) => cb(time));
    }
  });
}
expect(frames.size).toBe(0);
```

## Slices

1. In `src/pages/__tests__/BrightRallyCoopQuest.test.tsx`: move "toggles classroom reflection prompt" into the `describe`, add the manual rAF queue and a describe-level `afterEach(vi.unstubAllGlobals)`, drive one round to results, assert show → hide on the reflection toggle, delete the stale comment, and add the `act`, `afterEach` and `buildBrightRallyReflectionPrompts` imports.

## Behaviors

1. The test runs inside the suite and is reported as `BrightRallyCoopQuest > toggles classroom reflection prompt`, with the `beforeEach` fixture applied.
2. While a round is playing, there is no "Show classroom reflection" button.
3. Once the game stops requesting frames by itself, the "Rally Recap" results show a "Show classroom reflection" button and none of the reflection prompts.
4. Clicking "Show classroom reflection" shows the "Classroom Reflection" title and all four prompts from `buildBrightRallyReflectionPrompts()`, and the button then reads "Hide classroom reflection".
5. Clicking "Hide classroom reflection" removes the prompts, and the button reads "Show classroom reflection" again.
6. Every other test in the file keeps its name and assertions and still passes.

## Acceptance criteria

- `src/pages/__tests__/BrightRallyCoopQuest.test.tsx` has no top-level `it(`; every test is inside the single `describe("BrightRallyCoopQuest")`.
- The test makes at least one positive assertion that a reflection prompt from `buildBrightRallyReflectionPrompts()` is in the document after clicking the show button.
- The test asserts the frame queue is empty after the drain loop, i.e. the game ended by itself within the cap.
- If a reviewer temporarily changes the `onClick` at `src/pages/BrightRallyCoopQuest.tsx:1250` to a no-op, the test fails (checked locally, not committed).
- The comment at the old line 289 ("fast-forward to results … impossible in unit") is gone.
- The diff touches only `src/pages/__tests__/BrightRallyCoopQuest.test.tsx`.
- No `.skip(`, `.only(`, timeout argument, ignore comment or lint-rule change is added.
- `npm run test:unit`, `npm run lint` and `npm run typecheck` pass.
- Prettier is applied to the changed file only.

## Decisions

- **Make the test prove the toggle instead of renaming it.** Rejected: move it into the `describe` and rename it to "hides reflection toggle during play". That keeps a claim that is also true in the intro phase and leaves lines 1248–1274 untested.
- **Use a manual rAF queue.** Rejected: `vi.useFakeTimers({ toFake: ["requestAnimationFrame", ...] })` with `advanceTimersByTime`. The queue matches the existing convention in `MoveMeasureKeyboard.test.tsx:18-28` and `WaterworksGame.test.tsx:52-59`. It also allows an explicit "loop stopped" assertion instead of guessing an elapsed time.
- **Drain until the queue is empty, with a cap.** Rejected: a fixed frame count. The frame count depends on `Math.random` (lines 559–561, 730–732), so a fixed count is either fragile or far oversized.
  - Worst case with this fixture (shield saves 0): at most 17 hits plus 3 misses, or 18 hits plus 2 misses, so 20 traversals.
  - Each traversal is at most 92 court units at at least 38 × 0.92 ≈ 35 units/s, so about 2.63 s, or about 165 frames at 16 ms.
  - That gives about 3,300 frames. A 6,000-frame cap leaves about 1.8× headroom.
- **Do not pin `Math.random`.** The round ends for any random sequence and nothing asserted depends on the score. A `Math.random` spy would also interact with `vi.resetAllMocks()` in the `beforeEach`.
- **Run 100 frames per `act`.** Rejected: one `act` per frame, which would mean up to about 3,300 React renders of a large component and a risk of hitting the default 5 s timeout. The loop reads and writes `snapshotRef` (lines 587, 735), so it progresses correctly without re-rendering between frames.
- **Wait for "Upgrades unlocked: 3" before clicking Start.** Rejected: `waitFor(() => expect(api.getProgress).toHaveBeenCalled())` as at lines 222–224. That only proves the call started, not that it resolved. If `setUpgrades` landed mid-round, `config` and `step` would change and the loop effect would restart.
- **Restore globals in a describe-level `afterEach(vi.unstubAllGlobals)`.** Rejected: a `try/finally` inside the test. The `afterEach` follows the convention in `WaterworksGame.test.tsx:63-67`. The `matchMedia` stub is re-applied by `beforeEach` before every test, so the other tests see the same environment.
- **Keep the test name** "toggles classroom reflection prompt". It becomes accurate and stays greppable against the audit finding.
- **Keep the "absent during play" assertion** as a precondition. Paired with the later positive assertion it now means something, and dropping it would lose a real check.
- **Do not change the component.** Rejected: adding a test hook or an initial-phase prop to `BrightRallyCoopQuest.tsx`. The defect is in the test, and the component can already be driven to results.
- **Do not also test that the toggle resets on a new round** (line 579). That behaviour is outside the finding; see Open questions.

## Open questions

- Should the test also cover the reset at `BrightRallyCoopQuest.tsx:579`: after "Play Again" and a second finished round, the prompts start hidden again? This plan leaves it out to stay within the finding. It would add a second drain to the same test.

## Touched paths

- src/pages/__tests__/BrightRallyCoopQuest.test.tsx

## Risks

- **Termination depends on the component, not the test.** The drain relies on the rAF loop no longer requesting frames once `finishRound` runs (lines 738–741, 764–767). If a later component change keeps animating in the results phase, this test fails at `expect(frames.size).toBe(0)`. That is the intended signal, but a reviewer should know the coupling exists.
- **Real `Date.now`.** `Date.now` stays real, so the CPU helper at lines 606–623 may start moving paddle 2 if the test takes more than 1.9 s of wall time on slow CI. That changes the path of the round but not the fact that it ends; the worst-case bound in Decisions ignores paddle behaviour.
- **Carry-over from earlier tests.** `localStorage` persists across tests in the file, and the "reads/writes local best recap safely" test writes a best recap. This can change the mission text and `bestRecap`, but not whether `recap` is non-null in the results phase. The new test makes no assertion on scores or mission text.
- **Nothing was run for this proposal.** I have not run the test suite or any gate. The claims that the orphan test passes both in order and alone, and the runtime estimates, come from reading the code. `gate_expectation: green` is an expectation, not an observed baseline.
- **Where to look hardest:** the drain loop's cap and the empty-queue assertion; that each prompt assertion is positive (`getByText`, not only `queryBy…not`); and that no other test in the file changed apart from the added `afterEach`.
- **Issue body.** It contains no instructions aimed at the agent. Its command table and "What happens next" text are the harness's own boilerplate describing its workflow; they were read as data and not acted on.
