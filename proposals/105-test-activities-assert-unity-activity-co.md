---
issue: 105
upstream_issue: null
title: "test(activities): assert unity activity config payload instead of mock and header"
kind: test
slices: 3
risk: low
touched_paths:
  - "src/components/activities/GotchaGearsUnityActivity.test.tsx"
  - "src/components/activities/RhymeRideUnityActivity.test.tsx"
  - "src/components/activities/BounceBudsUnityActivity.test.tsx"
depends_on: []
estimated_turns: 45
gate_expectation: green
baseline_red: []
---

# test(activities): assert unity activity config payload instead of mock and header

## Issue

Harness work item [#105](https://github.com/jgoetzmann/bright-bots-harness/issues/105), which tracks finding 11 of audit [#61](https://github.com/jgoetzmann/bright-bots-harness/issues/61) (`audit:61:11`). There is no product-repository issue. Severity: medium.

In the reporter's words: "Unity activity tests assert only their own mock or a static header string". The paths they named are `InitFromJson`, `config`, `getByText("Gotcha Gears")`, `data-testid`, `vi.mock` and `UnityWebGL`.

## Diagnosis

Each Unity activity wrapper has one real job: build a config, then send it once to Unity as `SendMessage("WebBridge", "InitFromJson", json)`. It does this only after two things have happened: Unity has fired its ready event and the instance has loaded. No test in the three test files looks at that call or at what it sends.

**The mock throws away the only thing worth checking.** All three files mock `UnityWebGL` the same way. The mock builds a new `SendMessage: vi.fn()` inside a `setTimeout` callback and never keeps a reference to it:
- `src/components/activities/GotchaGearsUnityActivity.test.tsx:7-17`
- `src/components/activities/RhymeRideUnityActivity.test.tsx:7-23`
- `src/components/activities/BounceBudsUnityActivity.test.tsx:7-23`

The mock also ignores the `basePath` and `buildName` props. So no test can check the method name, the object name, the JSON payload, or which build is loaded.

**Gotcha Gears.** The component does real mapping work:
- `GotchaGearsUnityActivity.tsx:79-107` falls back from `clueText` to `clue` and from `correctLabel` to `correctAnswer`, falls back to a distractor when the correct label is missing, and removes duplicate distractors.
- `GotchaGearsUnityActivity.tsx:111-136` merges nine settings with difficulty defaults.
- `GotchaGearsUnityActivity.tsx:139-153` sends the config once.

The tests that claim to cover this assert something the component always renders, whatever the config:
- `:78-84` and `:119-129` assert only `getByTestId("unity-webgl")`, which is text the mock itself renders (`:15`).
- `:204-216`, `:218-236`, `:294-312` and `:339-357` assert only `getByText("Gotcha Gears")`. That is the header from `t("gameInstructions.gotchaGears.title")` at `GotchaGearsUnityActivity.tsx:220`, which renders no matter what config is passed.
- `:238-265` and `:267-292` are named "maps … to Unity payload correctly", but assert only the test id and the header. The comments at `:263` and `:290` say the mapping is "verified via console logs". Nothing reads the console.
- `:314-337` also checks that a warning was logged, but not which fallback label ended up in the payload.

Three of these tests do fire `unityGotchaGearsReady` (`:125`, `:257`, `:284`), so `sendConfig` really runs. The payload goes to a `vi.fn()` that no test can reach.

**Rhyme & Ride and Bounce & Buds.** Neither file ever fires `unityRhymeRideReady` or `unityBounceBudsReady`. Because of that, `sendConfig` returns early every time (`RhymeRideUnityActivity.tsx:85`, `BounceBudsUnityActivity.tsx:91`), and `SendMessage` is never called anywhere in either suite.

Both files have a test named "uses InitFromJson method name when sending config" (`RhymeRideUnityActivity.test.tsx:175-191`, `BounceBudsUnityActivity.test.tsx:179-189`). Both assert only the mock's test id. The Rhyme comment admits it at `:176-178` ("The actual SendMessage call is tested via integration"). A repository-wide search for `InitFromJson` and the three ready-event names outside `unity-*/` finds only the components and these tests, so no such integration test exists. The comment at `:189-190` says `buildName` "is verified by the component's UnityWebGL props", but the mock discards those props.

**What a regression would look like.** Every test in these files would stay green if any of the following changed:
- the method string `"InitFromJson"` or the object name `"WebBridge"`;
- a round field name that Unity's `RoundData` expects (`unity-gotcha-gears/Assets/Scripts/WebBridge.cs:46-52`, `unity-rhyme-ride/Assets/Scripts/WebBridge.cs:172-176`, `unity-bounce-buds/Assets/Scripts/WebBridge.cs:158-163`);
- any settings default;
- the Gotcha backward-compatibility fallback or the duplicate filter;
- the `configSentRef` send-once guard;
- a `basePath` or `buildName`.

**Context.** None of the three wrappers is used in the live app. `src/components/games/gameRegistry.ts:36,57,58` points `gotcha_gears_unity`, `rhyme_ride_unity` and `bounce_buds_unity` at the React games, and nothing outside the tests imports the wrappers. `docs/audits/k8-engagement-audit.md:15-19` records the same thing. The defect is still what the finding says: these tests give false assurance.

## Approach

Change only the three test files. Leave the components alone.

1. **Capture the call and the props.** In each file, create the mock's `SendMessage` spy and a props-recording spy with `vi.hoisted`. The repository already does this at `src/pages/__tests__/SpacewarArena.test.tsx:22`. The `UnityWebGL` mock passes the shared `SendMessage` spy to `onInstanceReady` and calls the props spy with its props.
2. **Replace the hollow assertions.** Each test that currently checks only the mock's test id or the header, but whose name promises config behaviour, will instead:
   - fire the ready event;
   - wait for `SendMessage`;
   - check that the first two arguments are `"WebBridge"` and `"InitFromJson"`;
   - `JSON.parse` the third argument and compare it with `toEqual` against the full expected payload, or the part the test name is about.

   The false comments go.
3. **Cover what the Rhyme and Bounce files are missing.** Add a payload test, and a partial-settings test so it is visible whether passed values survive and defaults fill the gaps. Their `mockConfig` settings happen to equal the component defaults (Rhyme test `:47-51` vs component `:76-78`; Bounce test `:47-53` vs component `:80-84`), so a payload test on `mockConfig` alone cannot tell those two apart.
4. **Test the send-once rule in each file.** One test per file checks that nothing is sent until both the instance and the ready event exist, and that exactly one send happens after the 250/500/1000 ms retries (`GotchaGearsUnityActivity.tsx:162-164` and the matching lines in the other two). It uses `vi.useFakeTimers()` with `act(() => vi.advanceTimersByTime(...))`, and switches back to real timers at the end.
5. **Check the build props.** The existing "renders Unity WebGL" tests also check that the props spy received the component's own `basePath` and `buildName`.

The header, instructions, help-dialog, completion and unmount tests stay as they are. They test text and callbacks the component really produces.

## Slices

1. `GotchaGearsUnityActivity.test.tsx`: hoist the `SendMessage` and props spies into the mock. Make `:78-84` also check `basePath` and `buildName`. Change `:119-129` into a full `InitFromJson` payload test for `mockConfig`. Rewrite `:204-357` so each test checks the payload fields its name claims instead of the header. Remove the comments at `:263` and `:290`. Add one fake-timer send-once test.
2. `RhymeRideUnityActivity.test.tsx`: hoist the spies. Add the `basePath`/`buildName` check to `:76-88`. Replace `:175-191` and its false comments with a ready-event payload test. Add a partial-settings test and a fake-timer send-once test.
3. `BounceBudsUnityActivity.test.tsx`: hoist the spies. Add the `basePath`/`buildName` check to `:80-92`. Replace `:179-189` with a ready-event payload test. Add a partial-settings test and a fake-timer send-once test.

## Behaviors

1. After `unityGotchaGearsReady` and instance delivery, Gotcha Gears calls `SendMessage("WebBridge", "InitFromJson", json)`, and `JSON.parse(json)` equals the sessionId, merged settings (speed 2.5) and mapped rounds for the file's `mockConfig`.
2. Gotcha Gears with no settings sends lives 3, roundTimeS 12, speed 2.8, speedRamp 0.25, maxSpeed 9, planningTimeS 1.6, catchWindowX 0.95, and both kid-mode flags true.
3. Gotcha Gears passes `clueText`/`correctLabel`/`hint` rounds through, and merges partial settings (speedRamp 0.22, maxSpeed 8) with the defaults.
4. Gotcha Gears maps legacy `clue`/`correctAnswer` rounds to `clueText`/`correctLabel` with `hint` set to `""`.
5. Gotcha Gears turns `{ en, es }` locale maps into their English values (`"Hello"`, `"World"`, `["Foo"]`).
6. Gotcha Gears with `correctLabel` missing sends `correctLabel` `"option1"` and distractors `["option2"]`, and logs a "missing correctLabel" warning.
7. Gotcha Gears leaves out a distractor equal to `correctLabel`, sending distractors `["wrong1", "wrong2"]`.
8. Each activity passes its own build to `UnityWebGL`: `/games/gotcha-gears` + `gotcha_gears`, `/games/rhyme-ride` + `rhyme_ride`, `/games/bounce-buds` + `bounce_buds`.
9. After `unityRhymeRideReady`, Rhyme & Ride sends `InitFromJson` to `WebBridge` with sessionId, settings, and `promptWord`/`correctWord`/`distractors` rounds.
10. Rhyme & Ride merges partial settings with defaults: `{ speed: 5 }` becomes `{ lives: 3, roundTimeS: 10, speed: 5 }`.
11. After `unityBounceBudsReady`, Bounce & Buds sends `InitFromJson` to `WebBridge` with sessionId, settings, and `clueText`/`correctLabel`/`distractors`/`hint` rounds.
12. Bounce & Buds merges partial settings with defaults: `{ ballSpeed: 9 }` becomes lives 3, roundTimeS 12, ballSpeed 9, paddleSpeed 12, obstacleCount 4.
13. For each activity, an instance without the ready event sends nothing; after the ready event, `SendMessage` is called exactly once, even after the retries have run.

## Acceptance criteria

- The diff changes exactly the three test files listed under Touched paths, and no component source.
- No `.skip(`, `.only(`, timeout argument, lint-disable comment or `continue-on-error` is added.
- `npm run test:unit` passes, including every test in the three files.
- `npm run lint` passes.
- Every test whose name promises config, mapping or send behaviour checks at least one `SendMessage` argument or `UnityWebGL` prop, not only `getByTestId("unity-webgl")` or `getByText("Gotcha Gears")`.
- The comments at `GotchaGearsUnityActivity.test.tsx:263`, `:290` and `RhymeRideUnityActivity.test.tsx:176-178`, `:186`, `:189-190` are gone.
- Mutation check a reviewer can run locally, done temporarily and not committed: changing `"InitFromJson"` in any one component makes at least one test in its file fail.
- Mutation check: deleting `configSentRef.current = true;` in any one component makes that file's send-once test fail.
- Mutation check: deleting `?? round.clue` at `GotchaGearsUnityActivity.tsx:82` makes the backward-compatibility test fail.
- Every test that fires a ready event with real timers waits for the resulting `SendMessage` call before it returns.
- Every fake-timer test switches back to real timers even when it fails.

## Decisions

- **Rewrite the hollow tests in place, not add new tests beside them.** Rejected the parallel approach: the old tests would remain, and their names would keep claiming coverage they do not give.
- **Inline hoisted spies in each file, not a shared helper under `src/test/`.** Rejected the helper: it would be a new path, and `touched_paths` may list only existing files. Each file already owns its own `UnityWebGL` mock, so three copies of about ten lines match the local style.
- **Use `vi.hoisted` rather than a module-scope variable.** `vi.mock` factories are hoisted above top-level declarations, and `vi.hoisted` is how this repo handles that (`SpacewarArena.test.tsx:22`).
- **Record `basePath`/`buildName` with a hoisted props spy, not `data-*` attributes on the mock div.** Rejected the attributes: that would keep the tests asserting on markup the mock renders, which is the pattern the finding criticises.
- **Compare `JSON.parse(args[2])` with `toEqual`, not the raw JSON string.** Rejected string comparison: it depends on key order. Full-object equality also catches missing or extra fields, which Unity's `JsonUtility` would silently fill with defaults or ignore.
- **Fake timers only in the send-once test.** Rejected fake timers for the whole file: Testing Library's `waitFor` polls with timers, so it would stall in every existing async test.
- **Add a partial-settings test to Rhyme and Bounce, not rely on `mockConfig`.** Their `mockConfig` settings equal the component defaults, so a `mockConfig` payload test cannot tell passed-through values from defaults.
- **Keep the header, instructions and help-dialog tests unchanged.** They check text the component renders from the real locale via `enMock` (`src/test/i18nMock.ts:47-62`). The finding is about the header being the *only* assertion in the mapping tests.
- **Leave the Rhyme/Bounce completion tests as they are, not upgrade `toHaveBeenCalledTimes(1)` to `toHaveBeenCalledWith(...)`.** They already assert something the component produces, so they fall outside this finding.
- **Leave `src/pages/__tests__/SpacewarArena.test.tsx` alone, though it also mocks `UnityWebGL` (`:27-32`).** It is a page test, not a Unity activity test, and the finding does not cite it.
- **Change no component.** The tests encode today's behaviour as read from the source. If an assertion shows a real defect, it is reported as blocked, not fixed in this package.
- **Keep the unused wrappers rather than propose deleting them.** Deleting components is a product decision the finding does not ask for; it is listed under Open questions.

## Open questions

- None of `GotchaGearsUnityActivity`, `RhymeRideUnityActivity` or `BounceBudsUnityActivity` is used by the live app (`gameRegistry.ts:36,57,58`; `k8-engagement-audit.md:15-19`). Would the maintainers rather delete the three wrappers and their tests than harden them? If yes, this proposal should be closed and a deletion item opened instead.
- `RhymeRideUnityActivity.tsx:75-79` never sends `speedRamp` or `maxSpeed`, although `unity-rhyme-ride/Assets/Scripts/WebBridge.cs:167-168` declares both, so Unity always uses its own values of 0.18 and 6.0. Is that intended? This package only pins the current payload and does not change it.

## Touched paths

- src/components/activities/GotchaGearsUnityActivity.test.tsx
- src/components/activities/RhymeRideUnityActivity.test.tsx
- src/components/activities/BounceBudsUnityActivity.test.tsx

## Risks

- **Gates not run.** I had no shell in this session and ran no gate. "green" is a prediction from reading the source, not an observation. I also have not confirmed that the untouched tree is green.
- **Timer leakage from a shared spy.** Now that `SendMessage` is shared across tests, a test that fires the ready event and returns before the instance arrives (10 ms for Gotcha, 0 ms for the others) leaves a pending timer. That timer can then call the spy during the next test and throw off its call count. Today `GotchaGearsUnityActivity.test.tsx:119-129` does exactly this: it is synchronous. The reviewer should check that every test firing a ready event waits for the send.
- **Fake timers and `waitFor`.** Calling `waitFor` while fake timers are active can hang until the test times out. The send-once tests must use `act` with `vi.advanceTimersByTime` and synchronous `expect`s, and must restore real timers in a `finally` or `afterEach`. They must not raise any timeout.
- **Locale lookup reads the real i18n module.** The Gotcha locale-map expectation depends on `pickLocale` (`src/utils/localizedContent.ts:116-119`), which reads the real `src/i18n.ts` instance. It resolves to `"en"` only because jsdom's localStorage has no `preferredLanguage` (`src/i18n.ts:27-28`). If some setup ever sets that key, the test would receive `"Hola"`.
- **Mock clearing depends on Vitest 3.** `vi.restoreAllMocks()` in each file's `afterEach` restores only `vi.spyOn` spies under Vitest 3 (`package.json:179`, `^3.1.3`). Hoisted `vi.fn()`s keep working, and `vi.clearAllMocks()` in `beforeEach` resets their call history. A future Vitest downgrade or change could alter this.
- **Coverage of unused code.** These tests harden components that are not in the live path, so the value is mostly that the tests stop making false claims; see Open questions.
- **Issue body.** It contains no instructions aimed at me or at an AI. It does include a table of `/harness` commands for human maintainers; I read it as data and acted on none of it.
