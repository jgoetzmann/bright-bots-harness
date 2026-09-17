---
issue: 53
upstream_issue: 800
title: "fix(sky-shield): guard g3-5 challenge catch against double-tap double-scoring"
kind: fix
slices: 3
risk: low
touched_paths:
  - "src/components/games/SkyShieldGame.tsx"
  - "src/components/games/__tests__/SkyShield.test.ts"
depends_on: []
estimated_turns: 40
gate_expectation: green
baseline_red: []
---

# fix(sky-shield): guard g3-5 challenge catch against double-tap double-scoring

## Issue

https://github.com/Bright-Bots-Initiative/brightboost/issues/800 — `bug(sky-shield): g3_5 doCatch lacks re-entrancy guard — rapid taps within the 1200ms window double-bump a round`. The reporter found it while fixing #735 (PR #798) and deliberately left it out of that change. In their terms: the g3_5 challenge path's `doCatch` has no re-entrancy guard unlike the K-2 path's `chReady`, so rapid taps inside the 1200 ms window score the same round twice; pre-existing, not a #798 regression.

## Diagnosis

The reporter is right about the symptom, and the code shows a sharper cause than "no guard": the guard is **half-wired already**.

`g35Ready` exists as state (`src/components/games/SkyShieldGame.tsx:222`) and its *reset* is wired (`:699`, inside `nextRound`), and one consumer reads it (`:704`, `doPredict`'s `if (!g35Ready || g35Scanned) return;`). But nothing in the file ever calls `setG35Ready(false)` — the only two references are the `useState(true)` initialiser and the `setG35Ready(true)` reset. `g35Ready` is therefore permanently `true`, and `doPredict`'s check on it is inert.

The K-2 path shows what the complete shape looks like: state at `:218`, handler guard `if (!chReady) return;` at `:625`, latch `setChReady(false)` at `:630`, reset `setChReady(true)` at `:642`, and the control disabled at `:673` (`disabled={!chReady}`).

The g3_5 `doCatch` (`:716-721`) has none of those three: no entry guard, no latch, and neither of its two Catch buttons (`:771-776` for normal drops, `:815-820` for the post-reveal mystery branch) passes `disabled`. Two consequences follow from one tap-tap:

1. **Double scoring.** `bump(PT.catch, caught)` at `:718` runs twice. `bump` (`:226-238`) increments `total.current` (`:236`) and adds `pts` to `maxScore.current` (`:237`) unconditionally, and adds to `score`/`streak` when `caught`. `maxScore.current` is the denominator `buildSkyShieldCompletionPayload` reports as `total` (`:108`), so the run's reported total gains a spurious `PT.catch` and `roundsCompleted` (`:113`) gains a spurious round. This is exactly the denominator inflation #735 was about, re-entering through a different door — the reporter's note that #798 changed the magnitude (flat 20 → the round's own value) matches `bump`'s `maxScore.current += pts`.

2. **A stale round-advance timer.** `doCatch` ends with `setTimeout(nextRound, 1200)` (`:720`). Two taps schedule two timers. Both closures captured the same `chIdx`, so both compute the same `n` and `setChIdx(n)` is idempotent — but the second timer fires up to a full tap-gap *into the next round* and re-runs `nextRound`'s else branch (`:696-700`), clearing `prediction` and `g35Scanned`. A prediction the learner has already made in the new round is silently wiped. Same root cause, same fix.

Note the defect is reachable on every g3_5 challenge round, mystery or normal, because both Catch buttons are always enabled. It is not timing-sensitive in the usual racy sense: nothing disables the control, so a second click *always* re-enters.

The adjacent `doScan` (`:709-714`) also lacks a guard, but is not reachable twice: its button is conditionally rendered on `!g35Scanned` (`:751`) and React flushes the state update between two discrete click events, so the node is gone before a second click can be delivered. See `## Risks`.

## Approach

Mirror the K-2 shape exactly — finish the wiring `g35Ready` was written for rather than introduce a new mechanism:

- add `if (!g35Ready) return;` as the first line of `doCatch`,
- add `setG35Ready(false)` immediately after it,
- add `disabled={!g35Ready}` to both Catch buttons, matching `:673`.

`nextRound` already restores `g35Ready` (`:699`), and the end-of-run branch (`:692-694`) leaves the phase, so no reset is needed there. This is a three-line behavioural change that makes an existing, already-reset piece of state actually do its job — and it fixes the stale-timer clobber as a side effect, because only one timer can ever be scheduled per round.

Rejected: a `useRef` latch (duplicates state the file already has and would leave `g35Ready`'s reset dangling); storing and clearing the timeout id (the entry guard already prevents the second timer, so a timeout ref adds state with no additional observable effect).

Coverage: the issue asks for a unit test that a second tap does not bump score or maxScore twice. `score` is observable in the HUD (`:284-286`); `maxScore` is a ref, observable only through the completion payload, so pinning it needs a driven run to the results screen. Both are added as component tests driving the real `SkyShieldGame` in jsdom (the repo's established pattern — see `src/components/games/biotrail/__tests__/gameFlow.test.tsx` and `src/components/games/__tests__/GameShellRetry.test.tsx`).

## Slices

1. Wire the re-entrancy guard on the g3_5 challenge catch path: entry guard and `setG35Ready(false)` in `doCatch` (`src/components/games/SkyShieldGame.tsx:716`), and `disabled={!g35Ready}` on the two Catch buttons (`:771`, `:815`).
2. Add a test that drives a g3-5 run to the first challenge round (guaranteed a normal drop, since `mkChallenge` places every mystery at index ≥ 2, `:65`), taps Catch twice inside the 1200 ms window, and asserts the HUD score advanced by exactly `PT.catch` and the Catch control is disabled after the first tap.
3. Add a test that completes a full g3-5 run containing one double tap and asserts the payload handed to `onComplete` reports `total: 370` and `roundsCompleted: 26` — the same figures a clean run produces, pinning that `maxScore` and `total.current` were not double-bumped.

## Behaviors

1. On a g3-5 challenge round, the first Catch tap scores the round exactly once.
2. A second Catch tap inside the 1200 ms advance window changes neither the displayed score nor the streak.
3. The Catch control is disabled from the first tap until the next round begins.
4. A g3-5 run that contains a double tap reports the same `total` and `roundsCompleted` as a run without one.
5. Lane prediction is inert during the 1200 ms window, because `doPredict`'s existing `g35Ready` check now has an effect.
6. Exactly one round advance is scheduled per catch, so a prediction made in the following round is not cleared by a stale timer.
7. The K-2 challenge path scores, disables, and advances exactly as it does today.
8. A normal (non-rapid) g3-5 playthrough reaches the exit ticket and results screen unchanged.

## Acceptance criteria

- `src/components/games/SkyShieldGame.tsx` contains `setG35Ready(false)`; `g35Ready` is no longer write-only-true.
- Both g3_5 Catch buttons carry `disabled={!g35Ready}`, matching the K-2 button at `:673`.
- `doCatch` on the g3_5 path returns early when `!g35Ready`, before calling `bump`.
- Reverting only the source change makes the new tests fail; this is stated in the test file's comment and verifiable by the reviewer.
- `npm run test:unit` passes, including the existing `SkyShield.test.ts` and `SkyShieldBands.test.ts` assertions.
- `npm run lint`, `npm run typecheck` and `npm run build` pass.
- No `.skip`/`.only`, no changed timeout, no relaxed threshold, and no file under `.github/` in the diff.
- The diff touches only the two paths listed under `## Touched paths`.

## Decisions

- Complete the existing `g35Ready` wiring rather than add a new ref latch — the state and its reset already exist (`:222`, `:699`) and one consumer already reads it (`:704`); a second mechanism would leave two half-used guards. Rejected: `useRef<boolean>` latch, `useRef` timeout id.
- Guard `doCatch` only, not `doScan` — `doScan`'s button unmounts on `g35Scanned` (`:751`) and React does not deliver a discrete click to a removed node, so a guard there would be code no test can exercise. Rejected: adding `if (g35Scanned) return;` to `doScan` as untestable defensive code; flagged under `## Risks` for the reviewer instead.
- Leave `<Lanes ... landed={false} />` (`:740`) alone — K-2 passes `landed={!chReady}` (`:659`), so `landed={!g35Ready}` would now be possible, but that is a drop-animation change nobody asked for. Rejected as scope creep.
- New tests go in the existing `src/components/games/__tests__/SkyShield.test.ts`, rendering via `React.createElement` (that file is `.ts`, so JSX is not available) — the proposal schema requires every declared path to exist in the tree today, so a new `.tsx` file cannot be declared, and an undeclared file in the diff fails the touched-paths check at package time. Rejected: a new `SkyShieldChallengeG35.test.tsx`; raised under `## Open questions`.
- Tests read the game state out of the DOM rather than stubbing `Math.random` — a constant stub makes `mkChallenge` spin forever, because its `while (mi.size < content.mysteryDrops)` loop (`:64-66`) needs 5 distinct indices from `2 + floor(random * 8)` and a constant yields one. Rejected: `vi.spyOn(Math, "random").mockReturnValue(...)`. The DOM already exposes what the test needs — the drop's lane is identified by its emoji (`LABELS` are distinct per lane, `:23`, `:269`), the pattern sequence is rendered during `patAsking` (`:490-497`), and the scan reveal shows `LABELS[hiddenColor]` (`:551`).
- Do not export `SkyShieldPlayfield` for direct mounting — rendering the default export and clicking through GameShell's briefing costs one extra click and keeps the module's public surface unchanged.
- Slice 3's assertions are `total` and `roundsCompleted`, not `score` — `maxScore` accumulates every round's value regardless of correctness, so `total` is deterministic at 370 for a g3-5 run (30 practice + 60 pattern + 60 scan + 200 challenge, + `PT.predict` for the exit ticket at `:108`), which the existing `perfectRunPoints` helper independently pins at `SkyShield.test.ts:137`. `score` depends on mystery-lane predictions the test cannot know before the scan reveal.
- Keep the fix and the tests in separate slices so slice 1 stands alone if the reviewer wants the driven run trimmed.

## Open questions

- Is `React.createElement` inside the existing `.ts` test file acceptable, or would the reviewer rather take a new `SkyShieldChallengeG35.test.tsx`? The latter cannot be declared in this proposal's `touched_paths`, so it needs an explicit human decision before implementation.
- Slice 3 drives roughly 25 interactions to reach the results screen. If the reviewer considers that too much machinery for one denominator assertion, slices 1 and 2 alone satisfy the "does not bump score twice" half of the issue's stated scope, but leave `maxScore` unpinned.
- Should `roundsCompleted` inflation be treated as a separate analytics concern? It is fixed by the same change, but I have not traced which consumers read it, so I do not claim what downstream effect the current inflation has.

## Touched paths

- src/components/games/SkyShieldGame.tsx
- src/components/games/\_\_tests\_\_/SkyShield.test.ts

## Risks

- **No gate was run during planning.** This environment has no shell, so I executed none of `npm run lint`, `npm run typecheck`, `npm run test:unit` or `npm run build`. `gate_expectation: green` and `baseline_red: []` are the expectation for the implementation, not a report of observed output. If the untouched tree is already red on any gate, that will surface at implementation time and be reported then.
- **Test flakiness is the main risk in this package, not the source change.** Slice 3 drives nested `setTimeout` chains — the pattern reveal alone is 400 + 700×7 + 500 ms (`:426-429`) — under fake timers. If advancing the clock proves unreliable, the honest outcome is to drop slice 3 and say so, not to add a retry or lengthen a timeout.
- **`doScan` remains unguarded** (`:709`). I argue above that React cannot deliver a second click to the unmounted Scan button, but that reasoning depends on React's discrete-event flushing; this is the thing worth a reviewer's closest look. If someone can demonstrate a double `PT.predict` bump, it is a one-line follow-up of the same shape.
- **`disabled` on a shadcn `Button`** must reach the underlying `<button>` for behavior 3 to hold. The K-2 path at `:673` already relies on this, so the risk is low, but the test asserts the disabled state directly rather than trusting it.
- **The issue body contained no instructions aimed at an AI agent**, no prompt-injection attempt, and no request to change anything outside `SkyShieldGame.tsx`. It is a plain, accurate bug report; the only correction this package makes to it is that the guard is half-wired rather than absent.
- Nothing in this package touches `.github/`, `prisma/`, `migrations/`, `backend/scripts/predeploy*`, CI config, or dependencies, and it adds no runtime dependency.
