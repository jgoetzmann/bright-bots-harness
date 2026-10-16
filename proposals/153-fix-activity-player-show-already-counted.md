---
issue: 153
upstream_issue: 791
title: "fix(activity-player): show already-counted copy instead of +0 points on replays"
kind: fix
slices: 3
risk: low
touched_paths:
  - "src/pages/ActivityPlayer.tsx"
  - "src/locales/en/common.json"
  - "src/locales/es/common.json"
  - "src/locales/vi/common.json"
  - "src/locales/zh-CN/common.json"
  - "src/pages/__tests__/ActivityPlayerCompletionLatch.test.tsx"
depends_on: []
estimated_turns: 25
gate_expectation: green
baseline_red: []
---

# fix(activity-player): show already-counted copy instead of +0 points on replays

## Issue

Product issue #791 (https://github.com/Bright-Bots-Initiative/brightboost/issues/791), harness issue #153. It was split out of #640. In the reporter's words, replaying an activity gives 0 XP on purpose, and that must not change. The problem is the screen: the results screen says "You earned 0 points" and the toast falls back to "Saved", which looks like a bug to students. The reporter asks for copy-only changes through i18n keys, at least en and es, and vi/zh-CN when possible. No changes to scoring or XP.

## Diagnosis

The issue's line numbers are out of date. The code they point to still exists, at different lines.

- **The backend replay reply.** `respondRewardFree` is at `backend/src/routes/progress.ts:417-445`. It is used by the already-completed branch (`progress.ts:447-450`) and by the lost-claim branch (`progress.ts:572-581`). It always sets `message` to `"Already completed"` or `"Already completed (avatar backfilled)"` (`progress.ts:429-431`). `reward.xpDelta` is `0`, or `backfilledXp` when the avatar was backfilled (`progress.ts:434`). The issue cites `progress.ts:177-221`, but that range is now the end of `reconcilePersonalBest` and the async-error helper.
- **The first-completion reply.** It is at `progress.ts:620-633` and never sets `message`. Inside `complete-activity`, these are the only two success replies (the only `res.json` calls in the handler are at lines 428 and 620). Error replies make `api.completeActivity` throw (`src/services/api.ts:606-610`), so they never reach the results screen.
- **`xpDelta === 0` does not mean "replay".** A first completion can also give 0 XP. When `result.roundsCompleted` is 0, `xpAward` is `Math.round(0 / total * XP_PER_ACTIVITY) = 0` (`progress.ts:455-474`), and `xpDelta` is 0 (`progress.ts:597`). Any fix keyed on `xpDelta` alone would show "already counted" on a genuine first play.
- **The toast.** `src/pages/ActivityPlayer.tsx:368-373` shows `t("activityPlayer.points", …)` when `xpDelta` is truthy, and `t("activity.saved")` otherwise. On a replay `xpDelta` is 0, so the student sees "Awesome, saved!" (`src/locales/en/common.json:757`). The reporter's "Saved" is a paraphrase of that.
- **The results screen.** `ActivityPlayer.tsx:420-429` always shows the `activityPlayer.youEarned` label ("You earned", `en/common.json:1069`) over a star pill with `t("activityPlayer.points", { count: reward?.xpDelta ?? 0 })`, which is "+{{count}} points" (`en/common.json:1070`). On a replay the student sees "You earned" / "+0 points". Nothing in the component looks at `message`: `completionData` is `useState<any>` (`ActivityPlayer.tsx:156`), and only `reward` is read from it (`ActivityPlayer.tsx:398`).
- **The same keys in other languages.** es is at `es/common.json:1025-1026`, vi at `vi/common.json:1002-1003`, zh-CN at `zh-CN/common.json:1002-1003`. Missing keys fall back to en (`src/i18n.ts:37`).
- **Test coverage.** No frontend test checks the results-screen or toast copy today. A search of `__tests__` for "You earned", "points" and "Awesome, saved" finds nothing for ActivityPlayer.

So the defect is only in how `ActivityPlayer.tsx` presents the reply. The backend already tells a replay apart through `message`, and the frontend ignores it.

## Approach

1. **Add one i18n key.** Add `activityPlayer.alreadyCounted` next to `activityPlayer.points` in all four `common.json` files:
   - en: "Already counted — nice replay!" (the reporter's suggestion)
   - es: "Tus puntos ya se contaron — ¡buena repetición!"
   - vi: "Điểm đã được tính rồi — chơi lại hay lắm!"
   - zh-CN: "分数已经算过啦——重玩真棒！"
2. **Detect a replay in `ActivityPlayer.tsx`.** Add a small local predicate. A reply counts as a replay when `message` is a string starting with `"Already completed"` and `reward.xpDelta` is falsy. Add a one-line comment citing `backend/src/routes/progress.ts` `respondRewardFree` as the source of that contract.
3. **Use the predicate in the toast.** Branch order: if `xpDelta` is truthy, show `activityPlayer.points` as today. Otherwise, if it is a replay, show `activityPlayer.alreadyCounted`. Otherwise show `activity.saved`.
4. **Use the predicate on the results screen.** On a replay, replace both the "You earned" label and the star/points pill with one line: `activityPlayer.alreadyCounted` in `text-2xl font-black text-brightboost-navy`, without the Star. Every other reply renders exactly as today.
5. **Add tests to the existing completion test file.** Hoist the toast mock so calls can be checked, and add tests for replay, first completion and backfilled replay.

No backend file, response shape, request payload, scoring path or XP value changes.

## Slices

1. Add `activityPlayer.alreadyCounted` to `src/locales/{en,es,vi,zh-CN}/common.json`, right after the `points` key in each `activityPlayer` block. No other key changes.
2. In `src/pages/ActivityPlayer.tsx`, add the replay predicate and use it in the toast description (lines 368-373) and the results screen (lines 420-429). The first-completion and backfilled-with-XP rendering stays unchanged.
3. In `src/pages/__tests__/ActivityPlayerCompletionLatch.test.tsx`:
   - Replace the per-render `vi.fn()` toast mock (lines 36-38) with a `vi.hoisted` spy. This is the pattern already used in `src/pages/__tests__/TryDemo.test.tsx:21`.
   - Add a `describe` block for #791 covering replay, first completion and backfilled replay.

## Behaviors

1. When the completion reply has `message: "Already completed"` and `reward.xpDelta: 0`, the results screen shows "Already counted — nice replay!" and shows neither "You earned" nor a points pill.
2. When the completion reply has `message: "Already completed"` and `reward.xpDelta: 0`, the toast description is "Already counted — nice replay!", not "Awesome, saved!".
3. When the completion reply has no `message` and `reward.xpDelta > 0`, the results screen shows "You earned" and the points pill, and the toast shows the points string, as today.
4. When the reply has `message: "Already completed (avatar backfilled)"` and `reward.xpDelta > 0`, the results screen and toast show points as today.
5. When the reply has no `message` and `reward.xpDelta: 0` (a first completion with 0 rounds), the results screen shows "You earned" / "+0 points" and the toast shows "Awesome, saved!", as today.
6. With the UI language set to es, vi or zh-CN, a replay shows that language's `activityPlayer.alreadyCounted` string instead of the English fallback.
7. The request sent to `/progress/complete-activity` is the same before and after the change.

## Acceptance criteria

- The diff changes only the six paths under `## Touched paths`, and no file under `backend/` is modified.
- `activityPlayer.alreadyCounted` exists in `src/locales/en/common.json`, `src/locales/es/common.json`, `src/locales/vi/common.json` and `src/locales/zh-CN/common.json`. The en value is exactly "Already counted — nice replay!".
- Each locale file's diff is a single added line inside the `activityPlayer` block. No existing key is renamed, removed or reworded.
- `ActivityPlayer.tsx` decides "replay" only from `message` and `reward.xpDelta`, and nothing in `handleComplete`'s request construction (lines 340-346) changes.
- The new tests in `ActivityPlayerCompletionLatch.test.tsx` cover Behaviors 1–5 and pass under `npm run test:unit`.
- LATCH-1, LATCH-2 and LATCH-3 keep their current assertions unchanged. The only change to the shared setup is hoisting the toast mock.
- `npm run lint`, `npm run typecheck`, `npm run test:unit` and `npm run build` pass on the change.

## Decisions

- **How to detect a replay.** Use `message` starting with `"Already completed"` together with a falsy `xpDelta`.
  - Rejected `xpDelta === 0` alone: it is also true for a genuine first completion with `roundsCompleted: 0` (`progress.ts:455-474`).
  - Rejected adding a boolean such as `alreadyCompleted` to the backend reply. That widens a copy-only issue into a backend contract change plus backend test updates, while `message` is already checked by backend tests (`progressGameSpecific.test.ts:425,708,747,812`, `progressScoringRegression.test.ts:499,599,701`, `progress_analytics.test.ts:187`).
- **`startsWith` rather than exact match.** A backfilled replay whose backfill added 0 XP has `message` "Already completed (avatar backfilled)" and `xpDelta` 0. It is a replay and should get the replay copy. Rejected `=== "Already completed"` because it would miss that case.
- **Backfilled replays that report XP keep showing points.** That XP was really added to the avatar in this request (`progress.ts:434`, `ensureAvatarWithBackfill`), so "+N points" is true. Rejected forcing "already counted" on every replay because it would hide XP the student actually got.
- **One key, reused by the toast.** Rejected separate toast and screen strings, because one sentence says it in both places and every extra string has to be translated four times. The key goes under `activityPlayer` rather than `activity`, because the results screen reads that block and the toast already mixes in `activityPlayer.points` (`ActivityPlayer.tsx:371`).
- **One `text-2xl` line instead of the pill.** On a replay, the label and pill are replaced by a single `text-2xl` line. Rejected putting the sentence inside the existing `text-4xl` rounded pill: a ~30-character sentence at 36px wraps inside the `max-w-md` card (`ActivityPlayer.tsx:405`), and a star pill reads as "points earned", which is the confusion being fixed.
- **Include vi and zh-CN now.** Rejected relying on the en fallback (`src/i18n.ts:37`), because the issue asks for vi/zh-CN when possible and the strings are short.
- **No change to the 0-XP first-completion copy (Behavior 5).** The issue is limited to replays. Changing that case would be a separate presentation decision.
- **No change to the toast title** `activity.completed` ("Activity Completed!", `en/common.json:761`). It is still true on a replay.
- **Tests go in the existing `ActivityPlayerCompletionLatch.test.tsx`.** LATCH-3 already drives the results screen with the same render harness and response fixture (lines 52-84). The harness requires every touched path to exist now, which rules out a new test file. Also rejected `ActivityPlayerPersonalBestSync.test.tsx`, which is scoped to cache sync.
- **`src/components/activities/quiz/__tests__/i18nParity.test.ts` is left alone.** Its key list is the quiz feature's §5.6.1 set, and the new key is not part of that spec.

## Open questions

- The es, vi and zh-CN strings above were written without a native-speaker review. Should a maintainer or translator confirm them before merge?
- The reporter's en string was given as "e.g.". Is "Already counted — nice replay!" the copy the product owner wants?
- Would maintainers prefer a typed boolean on the reward-free reply as a follow-up, so the frontend stops reading the human-readable `message` string? That would be a backend issue of its own.
- Should a first completion that earns 0 XP (`roundsCompleted: 0`) also get different copy? It still shows "You earned" / "+0 points", and this package leaves it alone on purpose.

## Touched paths

- src/pages/ActivityPlayer.tsx
- src/locales/en/common.json
- src/locales/es/common.json
- src/locales/vi/common.json
- src/locales/zh-CN/common.json
- src/pages/__tests__/ActivityPlayerCompletionLatch.test.tsx

## Risks

- **Coupling to an English backend string.** The predicate depends on `message` text. If the backend ever rewords it, the screen silently falls back to today's copy. That is not a crash or a scoring change. Eight backend assertions currently check the string, so a rewording would be a deliberate act. The reviewer should check that the code comment names the backend source.
- **Formatting the locale JSON files.** The repo has no `.prettierignore`, and the harness formats every changed file. If any of the four `common.json` files is not already Prettier-clean, formatting it could rewrite unrelated lines. The reviewer should confirm each locale diff is the single added line.
- **Non-ASCII characters.** The em dash, `¡` and the CJK full-width punctuation must be saved as UTF-8 in the JSON files.
- **Test mock change.** Hoisting the toast mock changes setup shared by LATCH-1 to LATCH-3. `vi.clearAllMocks()` in `beforeEach` (test file line 87) resets the hoisted spy between tests, so there should be no leakage. The reviewer should confirm the LATCH tests are otherwise untouched.
- **Interpolation in tests.** The test i18n mock ignores interpolation options (`src/test/i18nMock.ts:35-37`), so "+{{count}} points" renders literally in tests. Tests should check for the presence or absence of "You earned" and of the new string, not a rendered count.
- **Gates not run.** No gate was run while writing this proposal. `green` is an expectation based on reading the code, not an observed result, and `baseline_red` is empty because none was observed, not because one was confirmed absent.
- **Issue body.** It contains no instructions aimed at an automated agent. Its line references (`progress.ts:177-221`, `ActivityPlayer.tsx:335-364`) are stale and were not used as evidence. The correct locations are in `## Diagnosis`.
