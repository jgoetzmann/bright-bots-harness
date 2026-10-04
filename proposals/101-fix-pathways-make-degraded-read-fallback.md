---
issue: 101
upstream_issue: null
title: "fix(pathways): make degraded read fallbacks distinguishable from real progress"
kind: fix
slices: 4
risk: medium
touched_paths:
  - "backend/src/routes/pathways.ts"
  - "backend/src/routes/__tests__/pathwaysConsent.test.ts"
  - "src/components/pathways/onboarding/useOnboarding.ts"
  - "src/components/pathways/gamification/useGamification.ts"
  - "src/components/pathways/PathwaysHome.tsx"
  - "src/components/pathways/PathwaysProfile.tsx"
  - "src/components/pathways/glossary/GlossaryPage.tsx"
  - "src/locales/en/pathways.json"
  - "src/locales/es/pathways.json"
  - "src/locales/vi/pathways.json"
  - "src/locales/zh-CN/pathways.json"
depends_on: []
estimated_turns: 50
gate_expectation: green
baseline_red: []
---

# fix(pathways): make degraded read fallbacks distinguishable from real progress

## Issue

Harness issue #101 tracks finding 7 of audit harness#61 (`audit:61:7`), severity high. There is no product-repository issue. The finding's title is: "Read routes answer 200 with empty payloads indistinguishable from real data". It has no body beyond its "Paths" field, which lists two code fragments rather than file paths: `earnedAt: null` and `degraded: true`. In this repository `earnedAt: null` only appears as a literal in the badge-catalog fallback (`backend/src/routes/pathways.ts:975`). `degraded: true` appears at the seven fallback sites in the same file (485, 936, 997, 1029, 1075, 1397, 1492). So the reporter is pointing at the `catch` blocks in the Pathways read routes, which answer 200 with default data when the database query fails or times out.

## Diagnosis

The defect has two halves. HTTP 200 is not the defect on its own.

**1. Three read fallbacks carry no marker at all.** In `backend/src/routes/pathways.ts`:
- `GET /pathways/gamification/me/badges` (catch at 965-978) returns the full `BADGES` catalog with every `earnedAt: null`. The success path (956-964) returns exactly that shape for a learner who has earned no badges.
- `GET /pathways/gamification/me/events` (catch at 1050-1053) returns `[]`. The success path returns the same for a learner with no XP events.
- `GET /pathways/glossary/me/stats` (catch at 1510-1513) returns `{ totalViewed: 0, viewedSlugs: [] }`. The success path (1506-1509) returns the same for a learner who has viewed no terms.

In all three cases, neither the status nor the body lets a client tell "the query failed" from "this learner has nothing".

**2. Where a marker exists, the clients ignore it.** Five GET fallbacks do send `degraded: true`: `/student/home` (485), `/gamification/me` (936), `/gamification/me/daily-goals` (997), `/gamification/me/level` (1029) and `/onboarding/me` (1397). The client reads the flag in exactly one place, `src/components/pathways/PathwaysHome.tsx:87`, and only from the home payload. Elsewhere:
- `src/components/pathways/gamification/useGamification.ts:23-50` has no `degraded` field on either payload type. `load()` at 120-127 stores any 200 body (`if (s) setState(s)`), and `fetchJson` only returns `null` on `!res.ok` (104). A degraded answer therefore replaces the learner's real level, XP and streak with Level 1 / 0 XP. That includes the second `load()` the heartbeat triggers mid-session (152).
- `src/components/pathways/onboarding/useOnboarding.ts:66` stores the degraded onboarding row as the learner's state. That row (`pathways.ts:1387-1398`) has `completedAt: null`, `skillsTourViewed: false` and `avatarChosen: false`, which meets every redirect condition at `PathwaysHome.tsx:95-99`. The only guard, at line 87, checks `homeData?.degraded`, not the onboarding payload. So when `/onboarding/me` degrades but `/student/home` succeeds, a learner who already finished onboarding is sent to `/pathways/welcome` (`PathwaysHome.tsx:106`), once per session. This is the most concrete harm in the finding: the fallback drives navigation, not just display.
- `src/components/pathways/PathwaysProfile.tsx:89-100` checks neither `r.ok` nor `degraded` for badges, events or stats. It shows an all-locked badge grid with a "0/N" count (272-313) and "0 / N terms learned" (214-221) as if they were the learner's record.
- `src/components/pathways/glossary/GlossaryPage.tsx:42-44` shows no "viewed" checkmarks and a 0% bar. It cannot tell a failure from a new learner.
- When the home payload is degraded, `PathwaysHome.tsx:168-171,307` computes the Next Task from empty milestones and points a learner who is mid-track at the first module.

**Why 200 was chosen, and why it still matters for object payloads.** The comments at `pathways.ts:477-478` and `932-934` say the goal is to render the dashboard instead of hanging. The hang is actually prevented by `withTimeout` (63-76), not by the status. The status matters for another reason: `PathwaysHome.tsx:126-131` treats `!res.ok` as the full error screen. With `homeData` left `null`, the #874 redirect guard at line 87 (`homeData?.degraded`) could no longer suppress the welcome redirect. So a 200 carrying a body marker is load-bearing for `/student/home`. For the two array routes, a body field would change the response shape.

I did not read git history for these blocks. No tool I have runs `git log`, so the rationale above comes only from in-code comments and `prompts/2026-09-06-ticket-874-pathways-enrollment-consent.md:199-200`.

## Approach

Complete the existing `degraded` contract instead of replacing it.

Server (`backend/src/routes/pathways.ts`), three catch blocks only:
- The badges and events fallbacks answer **503** with `{ error: "progress_unavailable", degraded: true }`. A bare array cannot carry a field without breaking its shape, and the only consumer (`PathwaysProfile.tsx:89-96`) already reduces a non-array body to `[]`. The all-locked catalog disappears as a result, and that catalog is exactly the misleading output.
- The glossary-stats fallback keeps 200 and its body, and adds `degraded: true`. This matches the object-payload convention already used at five sites.
- All other fallbacks stay as they are.

Client:
- `useOnboarding` treats a degraded payload as unknown and does not store it. `state` stays `null` on the first load, or keeps the last real row. The redirect at `PathwaysHome.tsx:95-106` requires a truthy `onboarding`, so it cannot fire on a fallback. This also covers the other readers of the hook: `ChallengePage.tsx:77` and the welcome steps.
- `useGamification` never overwrites state with a degraded payload. It returns a new `degraded` boolean, true when its latest load got no real answer (either `null` or `degraded: true`) for `/gamification/me` or `/daily-goals`.
- One new i18n key, `pathways.progressUnavailable`, feeds a single notice: on the home page (home payload or gamification degraded), on the profile (gamification, badges, events or stats unavailable) and on the glossary page (stats unavailable). The home page also skips `NextTaskCard` while the home payload is degraded.
- Existing numbers are otherwise left as they render today; the notice makes them distinguishable. The change is limited to the three server catch blocks, two hooks and three render sites.

## Slices

1. Backend fallbacks and tests: in `backend/src/routes/pathways.ts`, the badges (965-978) and events (1050-1053) catch blocks answer 503 `{ error: "progress_unavailable", degraded: true }`, and the glossary-stats catch (1510-1513) adds `degraded: true`. In `backend/src/routes/__tests__/pathwaysConsent.test.ts`, extend the hoisted `prismaMock` with the `pathwayBadge`, `pathwayXpEvent`, `pathwayGlossaryView`, `pathwayOnboarding` and `pathwayGamification` delegates, then add a `describe` block covering the three changed fallbacks, their unchanged success paths, and the existing marked fallbacks of `/onboarding/me` and `/gamification/me`.
2. Onboarding hook: `src/components/pathways/onboarding/useOnboarding.ts` adds `degraded?: boolean` to `OnboardingState`, and `load()` does not call `setState` when the payload has `degraded: true`.
3. Gamification hook and home page: `useGamification.ts` adds `degraded?: boolean` to `GamificationState` and `DailyGoalsPayload`, skips `setState`/`setGoals` for degraded payloads, and returns `degraded`. Add `pathways.progressUnavailable` to `src/locales/{en,es,vi,zh-CN}/pathways.json`. `PathwaysHome.tsx` renders one `role="status"` notice with that key when `homeData?.degraded` or the hook's `degraded` is true, and does not render `NextTaskCard` when `homeData?.degraded` is true.
4. Profile and glossary pages: `PathwaysProfile.tsx` treats a non-ok badges or events response, and a non-ok or `degraded` stats response, as unavailable, and renders the `pathways.progressUnavailable` notice when any of those or the hook's `degraded` is set. `GlossaryPage.tsx` renders the same notice when the stats response is non-ok or `degraded`.

## Behaviors

1. `GET /api/pathways/gamification/me/badges` answers 503 with `{ error: "progress_unavailable", degraded: true }` when the badge query rejects or times out.
2. `GET /api/pathways/gamification/me/events` answers 503 with `{ error: "progress_unavailable", degraded: true }` when the event query rejects or times out.
3. `GET /api/pathways/glossary/me/stats` answers 200 with `{ totalViewed: 0, viewedSlugs: [], degraded: true }` when the view query rejects.
4. When their queries succeed, the badges, events and glossary-stats routes answer exactly as before, with no `degraded` key.
5. The fallbacks of `/student/home`, `/gamification/me`, `/gamification/me/daily-goals`, `/gamification/me/level` and `/onboarding/me` still answer 200 with `degraded: true`.
6. A learner whose `/onboarding/me` answer is degraded is not redirected to `/pathways/welcome`, even when `/student/home` succeeds.
7. A degraded `/gamification/me` or `/daily-goals` answer never replaces gamification values already loaded in the page.
8. The Pathways home page shows a "progress couldn't be loaded" notice when the home payload is degraded, or when gamification data is degraded or unreachable.
9. The Pathways home page does not show the Next Task card while the home payload is degraded.
10. The profile page shows the notice, and no all-locked badge grid, when gamification, badges, events or glossary stats are unavailable.
11. The glossary page shows the notice when glossary stats are unavailable.

## Acceptance criteria

- `backend/src/routes/__tests__/pathwaysConsent.test.ts` contains tests for behaviors 1-5. The delivery reports that the tests for behaviors 1-3 fail on the untouched tree and pass with the change.
- In `backend/src/routes/pathways.ts` the diff is confined to the catch blocks of the badges, events and glossary-stats routes. No other route's status or body changes.
- `useOnboarding.ts` `load()` has no path that calls `setState` with a payload whose `degraded` is `true`.
- `useGamification.ts` returns `degraded` from the hook, and `load()` has no path that stores a payload whose `degraded` is `true`.
- `PathwaysHome.tsx` still returns early at the existing guard when `homeData?.degraded` is true (line 87 behaviour preserved), and wraps `NextTaskCard` in a `!homeData?.degraded` condition.
- `pathways.progressUnavailable` exists in all four `src/locales/*/pathways.json` files. The `es`, `vi` and `zh-CN` values are the English string verbatim, and no other key in those files changes value.
- No path under `.github/`, `prisma/`, `migrations/` or `backend/scripts/predeploy*` appears in the diff.
- `npm run lint`, `npm run typecheck`, `backend: npm run typecheck`, `npm run test:unit` and `npm run build` are green. The delivery reports their actual output.
- Any formatting-only hunks are confined to the touched files and are called out in the delivery as formatter output.

## Decisions

- Object fallbacks keep 200 with a body marker instead of moving everything to 503. `PathwaysHome.tsx:126-131` turns any non-ok answer into the error screen and leaves `homeData` null, which would disable the #874 redirect guard at line 87.
- The two array fallbacks (badges, events) answer 503. Rejected: wrapping them in `{ items, degraded }`, which breaks the response shape; and a custom `X-…-Degraded` response header, which has no precedent in the repository and would add a second signal to maintain.
- The badges fallback drops the locked catalog. Keeping it was rejected: an all-locked catalog at 200 is exactly what the finding calls indistinguishable.
- The 503 body uses `error: "progress_unavailable"`, following the file's snake_case error codes (`failed_to_save` at `pathways.ts:1441`).
- `useOnboarding` handles a degraded payload by not storing it. Rejected: adding `|| onboarding?.degraded` to `PathwaysHome.tsx:87`, which protects only one caller. `ChallengePage.tsx` and the welcome steps also read this state.
- `useGamification` counts its own fetch failure (`null`) as unavailable, not only a server-declared `degraded`. The client already shows the identical zeros (`FALLBACK_STATE`, 64-75) for that case, for the same reason.
- Rendering changes are limited to a notice, plus hiding `NextTaskCard`. Rejected: hiding every fallback-derived number (stat cards, track bars, strip), a larger layout change than the defect needs. The Next Task card is the exception because it steers the learner to restart at module 1 (`PathwaysHome.tsx:306` calls it "the single most important thing on the page").
- The notice copy goes through one i18n key, following `docs/i18n.md:43-47`. English is copied verbatim into `es`, `vi` and `zh-CN` per `docs/i18n.md:46`. Rejected: translations the harness cannot verify, and hard-coded English (even though `PathwaysHome.tsx:183` does that).
- The notice copy will not claim that nothing is lost. A degraded answer can come from a missing table (`pathways.ts:932`), so the text only says the progress could not be loaded and suggests trying again later.
- Backend tests go into the existing `pathwaysConsent.test.ts`, which already mocks Prisma and drives `app` through supertest. A new file was rejected because `touched_paths` may only list paths that exist now.
- No frontend unit tests are added. No test file exists for any Pathways component or hook, and the same constraint rules out creating one. Behaviors 6-11 rest on typecheck, lint, build and review.
- Out of scope, left unchanged: the activity heartbeat POST (`useGamification.ts:146-151`), the glossary view POST (`pathways.ts:1492`), the missing `withTimeout` on glossary stats (`pathways.ts:1502`), and `/gamification/me/level`, which has no client consumer.

## Open questions

- Should the new route tests live in a dedicated file (for example `backend/src/routes/__tests__/pathwaysDegraded.test.ts`) instead of `pathwaysConsent.test.ts`? The harness cannot create it under the current schema; a maintainer could move the block.
- Do maintainers want real `es`, `vi` and `zh-CN` translations of the notice in this change, rather than the English placeholder the i18n guide allows?
- Should the heartbeat stop recording "pinged today" in localStorage when `/gamification/me/activity` answers degraded or non-ok (`useGamification.ts:146-151`)? As written, a degraded tick is never retried that day. It is a POST, so it is left for a follow-up item.
- Should `/pathways/glossary/me/stats` get the same `withTimeout` bound as its siblings (`pathways.ts:1502`)? Today a hung query there hangs the request instead of degrading.
- Should the home stat cards and track progress bars also be hidden while the home payload is degraded, rather than only annotated by the notice?
- The finding's "Paths" field lists two code fragments, not files. Is this set of eight GET fallbacks what the auditor meant, or did the audit have other routes in view?

## Touched paths

- backend/src/routes/pathways.ts
- backend/src/routes/__tests__/pathwaysConsent.test.ts
- src/components/pathways/onboarding/useOnboarding.ts
- src/components/pathways/gamification/useGamification.ts
- src/components/pathways/PathwaysHome.tsx
- src/components/pathways/PathwaysProfile.tsx
- src/components/pathways/glossary/GlossaryPage.tsx
- src/locales/en/pathways.json
- src/locales/es/pathways.json
- src/locales/vi/pathways.json
- src/locales/zh-CN/pathways.json

## Risks

- **Status change on two endpoints.** Badges and events move from 200 to 503 on failure. In this repository the only consumer is `PathwaysProfile.tsx:89-96`; nothing in `cypress/` or elsewhere in `src/` references these routes. A client outside the repository that expects a 200 array would break. During a database outage these routes now produce 5xx responses, which monitoring may alert on; that is intended but may be noticed.
- **Formatting noise.** lint-staged runs `prettier --check` on every staged file (`package.json:199-201`), there is no prettier config, and so the default print width of 80 applies. Several touched files look like they are not prettier-clean today:
  - `PathwaysProfile.tsx:13` is about 97 columns.
  - `useGamification.ts:81-83` have long object literals.
  - `useOnboarding.ts:42` is about 84 columns.

  Formatting only the changed files is still likely to produce unrelated whitespace hunks in them, and possibly in the locale JSON files. The reviewer should separate those hunks from the logic change and confirm no whole-tree formatter was run.
- **Untested frontend behaviours.** Behaviors 6-11 have no automated test. The reviewer should read the `useOnboarding` and `useGamification` changes closely, especially that the #874 redirect guard at `PathwaysHome.tsx:87` is unchanged, and that `load()` still updates state on a real answer after an earlier degraded one.
- **Notice on auth failures.** An expired token (401) makes `useGamification`'s fetch return `null`, which now shows the notice. That is arguably accurate but is a visible change.
- **Test mock changes.** Extending the hoisted `prismaMock` in `pathwaysConsent.test.ts` must not change behaviour for the existing consent tests. The new delegates are only added, and `vi.clearAllMocks()` already runs in `beforeEach`.
- **Nothing was run.** No gate and no git command was run while writing this proposal; I had no shell tool. `gate_expectation: green` is a prediction, not an observation. The history behind the 200 fallbacks was not inspected.
- **Issue body.** It contains no instructions aimed at the agent. Its harness boilerplate (the command table and links) was read as data and nothing in it was acted on.
