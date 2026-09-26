---
issue: 86
upstream_issue: null
title: "fix(spacewar): add on-screen touch buttons so mobile steering is not drag-only"
kind: fix
slices: 3
risk: medium
touched_paths:
  - "src/pages/SpacewarArena.tsx"
  - "src/pages/__tests__/SpacewarArena.test.tsx"
  - "src/locales/en/common.json"
  - "src/locales/es/common.json"
  - "src/locales/vi/common.json"
  - "src/locales/zh-CN/common.json"
depends_on: []
estimated_turns: 40
gate_expectation: green
baseline_red: []
---

# fix(spacewar): add on-screen touch buttons so mobile steering is not drag-only

## Issue

This is harness issue #86, tracking finding 25 (severity medium) of audit jgoetzmann/bright-bots-harness#60 (`audit:60:25`). There is no product-repository issue.

The finding is titled "Mobile steering in Spacewar Arena is drag-only". It points at the full-canvas gesture layer in `src/pages/SpacewarArena.tsx`: `absolute inset-0 z-30`, `touchAction: "none"`, and its `onPointerDown` / `Move` / `Up` handlers.

The issue body gives only that title and those anchors. This plan reads "drag-only" to mean: on a touch device, the only way to steer or thrust is to drag a finger across the canvas, and there is no control you can see or press.

## Diagnosis

**1. Every touch action goes through one invisible layer that follows one finger.**
- On touch devices the page draws a transparent div over the whole canvas (`src/pages/SpacewarArena.tsx:574-585`). It is the only touch input there is.
- The handlers follow a single pointer (`activePointerIdRef`, :107, set at :292).
- Steering and thrust exist only as the drag distance from where the finger first touched: `setRotate(-dx / ROTATE_SENS_PX)` at :305 and `setThrust(dy < -THRUST_SENS_PX)` at :307.
- Fire and hyperspace exist only as a finger lift after less than 12 px of movement (:322-333), with a double-tap under 260 ms meaning hyperspace (:324-327).

What this means in play:
- **No way to play without dragging.** Nothing on screen marks where to press. The first-run coach (:596-625) was added in bbb02f0 (#608) because, in that commit's words, the gestures "are invisible affordances".
- **You cannot fire while steering or thrusting.** Firing needs the finger lifted, and the lift clears rotate and thrust (:336-337). Holding a turn and shooting, the basic Spacewar move, is impossible.
- **Fast tapping triggers hyperspace.** Two fire taps within 260 ms become the risky jump (:324).

**2. The input pump stalls while the finger is moving, and does not start when Unity becomes ready.** The reporter did not cite this, but it blocks any button-based fix.
- The 30 fps pump (:208-235) lists `[isTouch, rotate, thrust]` as its dependencies (:235), so the 33 ms interval is cleared and recreated every time `rotate` changes.
- `rotate` is a continuous value set on every `pointermove` (:305) until it reaches ±1 at 80 px. While a drag is still moving inside that range, the timer is reset more often than every 33 ms and never ticks, so no `SetPlayer1Input` goes out until the finger pauses or reaches full turn.
- The pump only starts if `unityInstanceRef.current` is already set when the effect runs (:210). Unity readiness sets a ref, not state (:191), so nothing re-runs the effect when Unity finishes loading.
- A tap made before the first drag therefore sets `fireTapRef` (:330), sends nothing, and fires one late shot once some later drag starts the pump.
- Any button that only writes refs, which is how fire and hyperspace already work (:105-106), would never start the pump. A pad that uses only buttons would be dead unless this is fixed.

**3. The mobile "How to Play" section describes buttons that no longer exist.** I found this in git history, which I read without changing anything:
- 11d6555 (#489) added visible hold buttons: ◀ ▶ and THRUST on the left, FIRE and HYPER on the right. e261353 (#497) polished them.
- f2f1636 (#499) removed them. Its message says "Replace two-cluster button layout (left: rotate/thrust, right: fire/hyper) with single full-screen gesture capture layer" under "One-thumb gesture controls". It names no problem with the buttons.
- 2b7dde4 (#520) later deleted the modal that described the gestures, which left the button-era Dialog from d26437b (#495).
- So `SpacewarArena.tsx:500-507` still shows `◀ / ▶`, `THRUST`, `FIRE` and `HYPER`, with the last three hard-coded English.

**What the Unity side does.** It already accepts separate rotate, thrust, fire and hyperspace values through `WebBridge.SetPlayer1Input` (`unity-spacewar/Assets/Scripts/WebBridge.cs:276-292`, where positive rotate means left).
- `ShipController.SetExternalInput` stores each value until the next message (`ShipController.cs:602-608`).
- Firing is limited by a cooldown (`TryFire`, :294).
- With external control on, keyboard input is ignored (:123-138).

So the fix needs no Unity change or rebuild. The Unity score HUD sits in the top-left and top-right corners (`unity-spacewar/Assets/Editor/SpacewarSceneBuilder.cs:207-220`), which leaves the bottom edge free.

## Approach

Add a visible, multi-touch button pad on top of the canvas, next to the existing gesture layer rather than replacing it. Then fix the pump so every input source reaches Unity at a steady 30 fps.

**Pump**
- Replace the `rotate` / `thrust` `useState` (:103-104) with refs: `dragRotateRef` and `dragThrustRef`, written by the gesture handlers.
- Add pad refs `padRef = { left, right, thrust }`.
- Change the pump's dependencies to `[isTouch]` and drop the instance check at :210. The per-tick `if (!instance) return;` at :214 already covers readiness.
- Each tick sends `rotate = padRotate !== 0 ? padRotate : dragRotate`, where `padRotate = (left ? 1 : 0) + (right ? -1 : 0)`, and `thrust = pad.thrust || dragThrust`, plus the existing one-shot fire and hyperspace values.

**Pad**
- Two corner groups, copying the two-cluster layout that #489 and #497 shipped and the help dialog still describes:
  - bottom-left: ◀ and ▶ in a row, with ▲ (thrust) under them;
  - bottom-right: ⚡ (fire) above ✨ (hyperspace).
- Glyphs rather than English words. ⚡ and ✨ are the glyphs the coach already uses for fire and hyperspace (:608, :612).
- Every button is at least 56 px (`w-14 h-14` or larger), the D-pad size referenced in `docs/safe-exploration-accessibility.md:68`.
- The pad is rendered after the gesture layer, also at `z-30`, so it paints above the layer and below the `z-40` coach. The group wrappers are `pointer-events-none` and the buttons `pointer-events-auto` (the pattern in `f2f1636^`), so gaps between buttons still reach the gesture layer.
- ◀, ▶ and ▲ are hold buttons. `onPointerDown` calls `preventDefault`, takes pointer capture with `?.()` (the jsdom-safe form at `src/components/games/MoveMeasureGame.tsx:797`) and sets the ref. Up, cancel or lost capture clears it.
- ⚡ and ✨ set the existing `fireTapRef` / `hyperspaceTapRef` on `pointerdown`: one pulse per press.
- Buttons carry `touchAction: "none"`, `select-none` and `active:` press styling (the pattern at `QualifyTuneRaceGame.tsx:517`).
- Accessible names: `aria-label` from new keys `spacewar.rotateLeftLabel` / `spacewar.rotateRightLabel`, and existing `thrustLabel`, `fireLabel`, `hyperspaceLabel`. The group gets `role="group"` with the existing `spacewar.mobileControls` as its label.

**Other edits**
- Move the gesture hint (:587) from `bottom-4` to `top-2` so the pad does not cover it.
- In the mobile help section (:500-506), replace `THRUST` / `FIRE` / `HYPER` with the pad glyphs so the dialog matches what is on screen.
- The gesture layer's drag, tap and double-tap behaviour stays the same. Only its storage moves from state to refs, and its two pointer-capture calls (:291, :338) become `?.()` calls so jsdom tests can drive it.

**Why not the alternatives**
- Going back to buttons only would reverse #499's one-thumb scheme for players who learned it.
- A settings toggle adds persisted state and UI for a choice nobody asked for.
- Fixing only the gesture layer's multi-touch handling still leaves steering drag-only.

## Slices

1. Input pump: in `SpacewarArena.tsx`, replace the `rotate`/`thrust` state with drag refs written by the existing gesture handlers, give the pump effect `[isTouch]` as its only dependency with readiness checked per tick, and add fake-timer tests showing a moving drag and a tap before any drag both reach `SetPlayer1Input`.
2. Touch control pad: add the two-group button pad (◀ ▶ ▲ at bottom-left, ⚡ ✨ at bottom-right, ≥56 px, above the gesture layer, below the coach) writing pad refs the pump combines with the drag refs; move the gesture hint to the top edge; add `spacewar.rotateLeftLabel` and `spacewar.rotateRightLabel` to the en, es, vi and zh-CN `common.json`; add tests for render conditions, hold/release, pulses and combining pad with drag.
3. Help dialog: replace the hard-coded `THRUST` / `FIRE` / `HYPER` labels in the mobile "How to Play" section with the pad glyphs, and add a test that the touch-device dialog shows them.

## Behaviors

1. On a touch device, a group named "Mobile Controls" with five buttons (Rotate left, Rotate right, Thrust forward, Fire missile, Hyperspace) renders over the game canvas.
2. On a non-touch device, the pad does not render and no `SetPlayer1Input` message is sent.
3. While Rotate left is held, every pump tick sends `rotate: 1`; after release, ticks send `rotate: 0`.
4. While Rotate right is held, every pump tick sends `rotate: -1`; after release, ticks send `rotate: 0`.
5. While Thrust is held, ticks send `thrust: true`; after pointerup or pointercancel, ticks send `thrust: false`.
6. Each press of Fire produces exactly one tick with `fire: true`, and that tick has `hyperspace: false`.
7. Each press of Hyperspace produces exactly one tick with `hyperspace: true`, and that tick has `fire: false`.
8. Holding Thrust on the pad while pressing Fire gives `thrust: true` on every tick and one tick with `fire: true`, so a player can thrust and shoot at once.
9. With pad Thrust held and a drag on the canvas, ticks send `thrust: true` with the drag's rotate value; with a pad rotate held, the pad's rotate value wins over the drag's.
10. A drag that keeps moving within the 80 px steering range sends `SetPlayer1Input` at the ~33 ms cadence while it moves, not only after it pauses.
11. A canvas tap made before any drag sends a `fire: true` tick within one pump interval of the tap, provided Unity is ready.
12. The existing gestures behave as before: drag left/right steers, drag up past 35 px thrusts, a tap fires, a double-tap within 260 ms jumps to hyperspace.
13. The touch-device "How to Play" dialog lists `◀ / ▶`, `▲`, `⚡` and `✨` against the rotate, thrust, fire and hyperspace labels, and no longer shows `THRUST`, `FIRE` or `HYPER`.

## Acceptance criteria

- `npm run test:unit` passes, and `src/pages/__tests__/SpacewarArena.test.tsx` has at least one test for each of behaviors 1-11 and 13.
- The behavior-10 test fails on the untouched tree and passes with slice 1 applied (reviewer can confirm by reverting the `SpacewarArena.tsx` pump hunk).
- `npm run lint`, `npm run typecheck` and `npm run build` pass.
- `SpacewarArena.tsx` no longer has `useState` for `rotate` or `thrust`, and the pump effect's dependency list is exactly `[isTouch]`.
- Every pad button has an accessible name from `t()`, and no visible pad text is an English word.
- Every pad button is at least 56 px in both dimensions in its class list.
- Each of the four `common.json` files gains exactly two keys, `spacewar.rotateLeftLabel` and `spacewar.rotateRightLabel`, and no other key changes.
- No file under `unity-spacewar/`, `.github/`, `prisma/` or `backend/` appears in the diff.
- The diff touches only the paths under `## Touched paths`.
- On a real phone with the deployed Unity build, holding ▲ with one thumb while tapping ⚡ with the other makes the ship thrust and fire at the same time (manual, reviewer).

## Decisions

- **Add the pad next to the gesture layer rather than replacing it.** f2f1636 (#499) chose one-thumb gestures on purpose and gave no problem with buttons as the reason. Keeping both leaves one-thumb play intact and adds a way to play without dragging. Rejected: restoring buttons only (reverses #499 for current players) and a scheme toggle (new persisted setting and UI nobody asked for).
- **Fix the input pump as part of this item, not as a follow-up.** Buttons that only write refs never start the current pump (Diagnosis 2), so the pad would be dead without it.
- **Keep all live input in refs, not state.** Refs let the pump read current values without being torn down on each change, and remove a whole-page re-render on every `pointermove`. Rejected: keeping state and leaving the interval out of the dependencies, which would send stale closure values.
- **A held pad rotate overrides the drag's rotate; thrust is an OR of both.** Predictable, and it avoids the two cancelling each other. Rejected: summing and clamping, which lets a stray drag weaken a held button.
- **Fire is one shot per press, not auto-fire while held.** This matches keyboard Space (`Input.GetKeyDown`, `ShipController.cs:265`) and the existing tap, so the Unity fire-rate and projectile limits play as tuned. The removed #489 pad used hold-to-fire; see Open questions.
- **Visible labels are glyphs (◀ ▶ ▲ ⚡ ✨), with names coming from `aria-label`.** `docs/agents/rules/20-i18n.md:3` forbids hard-coded English in JSX, and ⚡ / ✨ already mean fire and hyperspace in the coach. Rejected: the old `THRUST` / `FIRE` / `HYPER` text.
- **Reuse `thrustLabel`, `fireLabel`, `hyperspaceLabel` and `mobileControls`, and add only the two rotate keys.** The existing `rotateLabel` covers both directions and cannot name two buttons.
- **Add the two new keys to all four locales, not just en and es.** The adjacent `spacewar.*Label` keys already exist in all four, and the vi/zh-CN values come straight from their existing `rotateLabel` strings ("Xoay trái / phải", "向左/向右转").
- **Use the stacked two-group layout (about 120 px wide on the left, 56 px on the right)**, not a single row of five buttons. A single row is about 304 px plus margins and would overlap on 320 px phones and at the ~240 px width floor in `docs/safe-exploration-accessibility.md:70`.
- **Move the gesture hint to the top edge rather than drop it.** The bottom edge becomes the pad; Unity's only top-edge UI is the corner score text (`SpacewarSceneBuilder.cs:207-220`). Rejected: squeezing the hint between the two groups, where it would wrap to three lines on a 375 px screen.
- **Make pointer capture calls `?.()`, including the two existing ones at :291 and :338.** jsdom has no Pointer Capture API, and the repo already uses this form (`MoveMeasureGame.tsx:797`). Browser behaviour does not change. Rejected: stubbing prototypes in the test file, which leaks across tests.
- **Do not change the gesture layer's single-finger logic or double-tap hyperspace.** With the pad, the common two-finger case is one finger on a button and one on the canvas, which are separate elements. Changing gesture semantics goes beyond the finding.
- **Do not change the coach copy.** The pad is visible, which is the point of the fix, and "Tap anywhere to fire" stays true.
- **Do not change the Unity C# code.** The bridge already accepts separate rotate, thrust, fire and hyperspace values (`WebBridge.cs:286-292`).

## Open questions

- Do the maintainers accept bringing on-screen buttons back beside the one-thumb gestures that #499 chose? If they want gestures only, this plan should be rejected, and the item becomes "fix the pump and the stale help text".
- Should Fire auto-fire while held, as the removed #489 pad did ("Hold to shoot"), instead of one shot per press?
- Should double-tap hyperspace on the canvas stay, now that there is a dedicated ✨ button? Two quick fire taps currently trigger the risky jump by accident (:324-327).
- Should a second finger on the open canvas stop taking over from the first? Today `handleGesturePointerDown` overwrites the tracked pointer unconditionally (:292-293), which leaves the first finger dead until lifted. This is left unchanged here.
- Out of scope, seen while reading: `isTouchDevice` counts any `navigator.maxTouchPoints > 0` device (:40), including touchscreen Chromebooks. On those, `EnableTouchControls` (:198-199) switches Unity's Player 1 to external control, which ignores the keyboard (`ShipController.cs:123-138`). Is that intended for classroom laptops?
- Out of scope, seen while reading: `handleInstanceReady` depends on `difficulty` (:205), and `UnityWebGL`'s load effect depends on `onInstanceReady` (`src/components/unity/UnityWebGL.tsx:238`). From reading the code, changing difficulty appears to quit and reload the Unity instance. Should that be its own finding?
- The vi and zh-CN values for the two new keys come from existing strings but have not been checked by a native speaker.

## Touched paths

- src/pages/SpacewarArena.tsx
- src/pages/__tests__/SpacewarArena.test.tsx
- src/locales/en/common.json
- src/locales/es/common.json
- src/locales/vi/common.json
- src/locales/zh-CN/common.json

## Risks

- **CI cannot check layout or touch.** jsdom does no hit testing or stacking, so tests prove the input logic, not that the pad paints above the gesture layer or that thumbs can reach it. Also, the Unity build is not in the repository (`public/games/spacewar/Build/.gitkeep`), so a local dev server shows the "Game Not Available" panel under the pad. The reviewer should check on a real phone against the deployed build:
  - the pad is above the canvas layer and below the first-run coach;
  - thrust and fire work at the same time;
  - the moved hint does not collide with the corner scores.
- **Some play area is lost.** The two groups cover the bottom corners of the canvas. The ship can fly under them, and one-thumb players lose those corners as drag starting points. Semi-transparent backgrounds reduce but do not remove this.
- **`:active` press styling can be unreliable on iOS Safari.** If it does not show, pressing still works; only the visual feedback is missing.
- **Long-press behaviour may leak through.** Mobile browsers may still show a long-press callout or menu on a held button despite `touchAction: "none"` and `select-none`. The reviewer should try a long hold on ▲ on iOS and Android.
- **The pump change touches the path every touch player already uses.** Slice 1 changes how drag input reaches Unity for all touch players. The reviewer should look hardest at the pump effect and the rotate/thrust combination, and confirm the gesture handlers write the same values as before (Behavior 12).
- **Pump tests use fake timers.** The cadence tests need `vi.useFakeTimers()`, and Testing Library's `findBy*` does not advance Vitest's fake timers. The implementation must flush the mocked API promises with `act` instead. Tests that drive the gesture layer depend on how jsdom supports pointer events.
- **Gate baseline not checked.** I did not run any gate on the untouched tree; I had no shell in this session. `gate_expectation: green` is an expectation, not something observed.
- **Issue-body content.** The issue body holds no instructions addressed to the agent. It does hold the harness's `/harness` command table, which is for trusted maintainers; I treated it as data and acted on none of it.
