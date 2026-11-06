---
issue: 102
upstream_issue: null
title: "fix(pathways): report failed activity and glossary writes instead of a fabricated 200"
kind: fix
slices: 4
risk: low
touched_paths:
  - "backend/src/routes/pathways.ts"
  - "backend/src/routes/__tests__/pathwaysConsent.test.ts"
  - "src/components/pathways/gamification/useGamification.ts"
depends_on: []
estimated_turns: 30
gate_expectation: green
baseline_red: []
---

# fix(pathways): report failed activity and glossary writes instead of a fabricated 200

## Issue

Harness issue #102 tracks finding 8 of audit jgoetzmann/bright-bots-harness#61 (`audit:61:8`). No product repository issue exists. The finding is rated medium and points at three fragments: `streak: 0`, `totalViewed === 100` and `cyber_linguist`. In the reporter's words: "Write routes swallow the write and reply 200 with fabricated counters". The finding gives the problem, not a plan.

## Diagnosis

All three fragments are in `backend/src/routes/pathways.ts`. Two POST routes catch every error and still reply `200` with a body the server made up.

**POST `/api/pathways/gamification/me/activity`** (`pathways.ts:1057-1079`)
- The route awaits `recordActivity(userId)` inside `withTimeout(..., 5000)` (`pathways.ts:1062-1066`).
- On any rejection, including the 5 s timeout, the catch at `pathways.ts:1068-1077` replies `res.status(200).json({ streak: 0, longestStreak: 0, freezeUsed: false, degraded: true })`.
- Those are counters the server never read. They tell the caller the learner's streak is zero, when the failure means nothing is known.
- The body does not even match the success shape: `ActivityResult` also has `milestoneBadges` (`backend/src/services/gamification.ts:435-440`).

**POST `/api/pathways/glossary/view`** (`pathways.ts:1452-1495`)
- The `findUnique`, the `create`, the `count` and up to three `awardBadge` calls all run in one try block (`pathways.ts:1463-1487`). The `awardBadge` calls are at `totalViewed === 25 / 50 / 100` → `word_collector` / `vocab_builder` / `cyber_linguist`.
- Any failure in that block reaches `pathways.ts:1488-1493`, which replies `res.status(200).json({ alreadyViewed: false, degraded: true })`. That claims a fresh view was handled when it may never have been stored.

**The same file handles failures correctly elsewhere.** The other write route in this section, PATCH `/pathways/onboarding/me`, answers `res.status(500).json({ error: "failed_to_save" })` (`pathways.ts:1439-1442`). The app-wide error handler also answers a JSON 500 for anything unexpected (`backend/src/server.ts:262-271`). In the backend, only these two write routes reply 200 on failure. Every other `status(200)` in a catch block belongs to a GET route (`pathways.ts:480, 936, 968, 997, 1024, 1052, 1387, 1512`).

**Why it matters: lost writes are invisible and are not retried.**
- Every lost write shows up as a success in HTTP status metrics. Only a `console.error` records it.
- The only caller of the activity route, `src/components/pathways/gamification/useGamification.ts:140-156`, does not check the status.
  - It awaits `fetch`, which resolves on 4xx/5xx without throwing. It then unconditionally calls `localStorage.setItem(ACTIVITY_KEY, today)` (line 151).
  - So a failed tick counts as done and is not retried that day. The learner's next visit sees a 2-day gap, which burns a freeze or resets the streak (`gamification.ts:473-496`).
  - Fixing the server alone would not change this, because the client ignores the status either way.
- The glossary caller, `src/components/pathways/glossary/GlossaryTerm.tsx:59-71`, marks the term viewed in `sessionStorage` before sending and ignores the response. A failed view is naturally re-sent in the next session. Only the server side needs changing there.

**Retrying is safe.**
- `recordActivity` is idempotent within a UTC day (`gamification.ts:460-467`).
- `PathwayGlossaryView` has `@@unique([userId, termSlug])` (`backend/prisma/schema.prisma:989`).
- `withTimeout` (`pathways.ts:69-76`) only races the promise and does not cancel the Prisma call. So a timed-out write may still commit, and a failure reply means "not confirmed", not "not written".

**A side effect of the fix.** Two concurrent first views of the same term can both pass the `findUnique` at `pathways.ts:1464-1466`. The loser's `create` then fails with P2002. Today that is hidden by the blanket 200. After this fix it would become a 500 for a view that is in fact recorded.

## Approach

Change only the two catch blocks, add one race branch, and make the single client caller respect the status.

1. **Activity route.** Replace the fabricated 200 at `pathways.ts:1071-1076` with `res.status(500).json({ error: "failed_to_save" })`. Keep the `console.error` and the `withTimeout` wrapper. Update the comment at 1069 so it no longer calls the tick best-effort on the server.
2. **Glossary route.**
   - Replace the fabricated 200 at `pathways.ts:1492` with the same 500 body, and update the comment at 1490-1491.
   - Wrap only the `create` at 1471-1473 in a try/catch. On `code === "P2002"`, return `res.json({ alreadyViewed: true })`; rethrow anything else to the existing outer catch.
   - This follows the existing P2002 idiom (`backend/src/routes/progress.ts:173`, `backend/src/routes/benchmarks.ts:97`).
3. **Client heartbeat.** In `useGamification.ts`, keep the `fetch` result and only call `localStorage.setItem(ACTIVITY_KEY, today)` when `res.ok`. A non-2xx then gets retried on the next mount the same day, just as network errors and aborts already are (they throw into the catch at line 154 before line 151 runs). The `load()` call after the ping is unchanged.
4. **Tests.** Add route tests for both failure paths, the P2002 branch and the unchanged success paths. They go in `backend/src/routes/__tests__/pathwaysConsent.test.ts`, the only existing unit test file that boots the pathways router against a hoisted Prisma mock (lines 23-55). They go in a clearly labelled `describe` block. The mock gains `pathwayGamification` and `pathwayGlossaryView` members, and each test arms its own mocks with `...Once` variants so nothing leaks into the #874 cases.

## Slices

1. `backend/src/routes/pathways.ts` activity route: the catch at 1068-1077 replies `500 { error: "failed_to_save" }` instead of the fabricated streak body, and the comment at 1069 is corrected.
2. `backend/src/routes/pathways.ts` glossary route: the catch at 1488-1493 replies `500 { error: "failed_to_save" }`, and a P2002 from the insert at 1471-1473 replies `200 { alreadyViewed: true }`.
3. `src/components/pathways/gamification/useGamification.ts`: the heartbeat marks the day as sent only when the response is 2xx.
4. `backend/src/routes/__tests__/pathwaysConsent.test.ts`: a new labelled `describe` block with the activity and glossary route cases from `## Behaviors` 1-6, plus the Prisma mock members those routes need.

## Behaviors

1. When `recordActivity` rejects or exceeds the 5 s server timeout, POST `/api/pathways/gamification/me/activity` answers `500` with body `{ "error": "failed_to_save" }`.
2. That failure body has no `streak`, `longestStreak`, `freezeUsed` or `degraded` field.
3. When `recordActivity` resolves, the activity route answers `200` with exactly the `ActivityResult` it returned, as today.
4. When the glossary lookup, insert, count or badge award rejects with anything other than P2002 on the insert, POST `/api/pathways/glossary/view` answers `500 { "error": "failed_to_save" }` with no `alreadyViewed` or `degraded` field.
5. When the glossary insert rejects with P2002, the route answers `200 { "alreadyViewed": true }`.
6. These glossary paths are unchanged:
   - a new view answers `200 { alreadyViewed: false, totalViewed, badgeAwarded }`;
   - an existing view answers `200 { alreadyViewed: true }`;
   - a malformed body answers `400`.
7. After the activity POST gets a non-2xx response, `bb_pathways_activity_ts` is not set to today, so the next mount of `useGamification` that day sends the heartbeat again.
8. After the activity POST gets a 2xx response, `bb_pathways_activity_ts` is set to today and no second heartbeat is sent that day.
9. A failed heartbeat still never throws out of `useGamification` or holds up its `loading` flag.

## Acceptance criteria

- The diff touches exactly `backend/src/routes/pathways.ts`, `backend/src/routes/__tests__/pathwaysConsent.test.ts` and `src/components/pathways/gamification/useGamification.ts`.
- In `pathways.ts`, neither the catch block of POST `/pathways/gamification/me/activity` nor that of POST `/pathways/glossary/view` contains `status(200)`.
- The GET-route degraded fallbacks at `pathways.ts:480, 936, 968, 997, 1024, 1052, 1387, 1512` are unchanged.
- The glossary insert's P2002 branch checks `code === "P2002"` and rethrows every other error.
- In `useGamification.ts`, `localStorage.setItem(ACTIVITY_KEY, today)` runs only on a branch guarded by `res.ok`.
- The new tests cover Behaviors 1-6. The implementer reports that the activity-failure and glossary-failure tests fail on the untouched route (they get 200) and pass after the change. This matches the "RED evidence" convention in the header of `pathwaysConsent.test.ts` (lines 16-18).
- The existing #874 tests in `pathwaysConsent.test.ts` are not modified and still pass.
- `npm run lint`, `npm run typecheck`, `backend: npm run typecheck` and `npm run test:unit` are green on the changed tree.
- No file under `.github/`, `prisma/`, `backend/prisma/` or `backend/scripts/` is changed.

## Decisions

- Reply `500 { error: "failed_to_save" }`, not 503 or 504, because that is the body and status of the neighbouring write route (`pathways.ts:1441`) and the app error handler (`server.ts:269-271`). A timeout-specific 504 was rejected: no route in the file uses one, and the only client does not tell them apart.
- Keep `withTimeout` on the activity route and change only what the catch replies. Removing the timeout was rejected: it exists so a hung pool cannot hold the request open (`pathways.ts:63-68`).
- Answer P2002 on the glossary insert with `200 { alreadyViewed: true }`. Leaving it to the new 500 was rejected, because a racing request has already stored the view and a 500 would be the reverse misreport. Checking before inserting cannot close the race; the unique constraint is the only reliable arbiter.
- Include the one-line client guard in `useGamification.ts`. A server-only fix was rejected: the only caller ignores the status (line 151), so the learner would lose the day's tick exactly as before.
- Leave `GlossaryTerm.tsx` alone. Moving `markSessionView` after a successful response was rejected: it would open a same-tab double-POST window (hover and focus both call `show`), and a failed view is already re-sent next session.
- Leave the GET-route `degraded: true` fallbacks as they are. They are reads, the finding names write routes, and `PathwaysHome.tsx:87` relies on the `degraded` flag of the student-home payload.
- Put the route tests in the existing `pathwaysConsent.test.ts` rather than a new file. The proposal schema only accepts touched paths that already exist, and this is the only unit test file that already mounts the pathways router on a Prisma mock.
- Do not unit-test the timeout branch with fake timers. It reaches the same catch as a rejection, and faking `setTimeout` under supertest's real sockets risks flaky tests. Adding a real 5 s wait was also rejected, since it would slow the suite.
- Do not add a unit test for the client guard. No test file for `useGamification` exists and the schema forbids listing a new one. The change is a single `res.ok` condition that lint and typecheck cover; see `## Open questions`.
- Keep this package to the status and body of the reply. Making the badge thresholds or the multi-write sequences retry-safe is a separate defect and is raised as a question.

## Open questions

- The glossary badges use strict equality (`totalViewed === 25/50/100`, `pathways.ts:1480-1485`), and so do the streak milestones (`newStreak === 3/7/30`, `gamification.ts:520, 524, 535`). A failed award at the exact threshold is never tried again, even after this fix. Should that become `>=` with the idempotent `awardBadge`, as a separate work item?
- `recordActivity` and the glossary route run several writes with no transaction, so a 500 can follow a committed row. The badge or milestone that should have followed is then skipped on retry, because the day is already recorded or the view already exists. Should these be made atomic as a separate item?
- Do the maintainers want a hook-level test for the heartbeat guard? It would need a new file (for example `src/components/pathways/gamification/__tests__/useGamification.test.ts`), which this proposal cannot list.
- GET `/pathways/onboarding/me` (`pathways.ts:1383-1398`) fabricates `completedAt: null` for a learner who may have finished onboarding. It is a read and outside this finding's title. Should it be handled in its own item?
- I could not read audit #61 itself, so I inferred the finding's scope from the three quoted fragments, all of which are in these two routes. If finding 8 meant other routes too, the scope needs widening.

## Touched paths

- backend/src/routes/pathways.ts
- backend/src/routes/__tests__/pathwaysConsent.test.ts
- src/components/pathways/gamification/useGamification.ts

## Risks

- **More 5xx.** Error and uptime dashboards will start showing 5xx on these two endpoints during database trouble. That is the intended effect, but an alert threshold could fire where it did not before.
- **More retries during an outage.** With the client guard, every mount of `PathwaysHome` or `PathwaysProfile` (the only `useGamification` callers) re-sends the heartbeat until it succeeds. Each attempt can hold a server request for up to 5 s. It never blocks rendering, because it runs after `setLoading(false)`, and recording activity is idempotent within a day.
- **A 500 does not mean nothing was written.** After a timeout the row may still commit, because `withTimeout` does not cancel the Prisma call. The reviewer should confirm the new comments say "not confirmed" and do not claim nothing was stored.
- **Changes to the shared test file.** Adding members to the hoisted `prismaMock` in `pathwaysConsent.test.ts` must not change the #874 tests. The reviewer should check that the new cases use `mockResolvedValueOnce` / `mockRejectedValueOnce`, because `vi.clearAllMocks()` in its `beforeEach` (line 76) does not reset implementations.
- **The P2002 branch.** It must match only the insert's P2002 and rethrow everything else, or it reopens the same swallow in a narrower form.
- **No gates were run.** I only read the source while writing this proposal. `gate_expectation: green` is an expectation, not a measured baseline.
- **Issue content.** The issue body has no instructions aimed at an agent. Its command table and links are harness boilerplate for human maintainers, and I did not act on them.
