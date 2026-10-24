---
issue: 107
upstream_issue: null
title: "fix(pathways): show ctf submit failures as errors instead of wrong answers"
kind: fix
slices: 2
risk: low
touched_paths:
  - "src/components/pathways/challenges/ChallengePage.tsx"
depends_on: []
estimated_turns: 25
gate_expectation: green
baseline_red: []
---

# fix(pathways): show ctf submit failures as errors instead of wrong answers

## Issue

Harness issue #107 tracks finding 13 of audit #61 (`audit:61:13`), severity medium. There is no product-repository issue. The finding's title is "CTF submission ignores `res.ok`, marking a correct flag wrong". It points at `if (!body)`, `setResult`, `correct: undefined`, `solveMap`, `revealNextHint` and `res.ok`. The finding describes the problem only and proposes no fix.

## Diagnosis

The defect is in the client, in `submitFlag` in `src/components/pathways/challenges/ChallengePage.tsx`.

- **The server only grades with a 200.** In `backend/src/routes/pathways.ts`, a wrong flag gets a 200 with `{ correct: false, message: "Not quite — try again." }` (lines 1156–1161). A correct flag gets a 200 with `correct: true`, either as a replay (1170–1174) or as a first solve (1258–1260). Any other status means the server never checked the flag.
- **The server has several non-2xx replies with JSON bodies:**
  - 400 `{ error }` when the zod parse fails (1132–1134).
  - 404 `{ error: "Challenge not found" }` (1139–1141).
  - 401 `{ error: "unauthorized" }` from `requireAuth` (`backend/src/utils/auth.ts:87-90`).
  - 403 `{ error: "forbidden_invalid_token" }` from `authenticateToken` when the JWT is expired or invalid (`auth.ts:32-36`).
  - 413 `{ error }` from the global error handler when the 50 kB body limit is exceeded (`backend/src/server.ts:178`, `269-276`).
- **The client never looks at the status.** `ChallengePage.tsx:155` parses the body. Line 156 only checks that it is non-null. Line 157 then calls `setResult(body)`, where `body` is really `{ error: "..." }`, so `result.correct` is `undefined`.
- **So an ungraded submission looks like a wrong answer.** The result panel only has two states. Because `result.correct` is falsy, it takes the amber branch with the `XCircle` icon (`ChallengePage.tsx:411-421`), and `{result.message}` at line 430 is `undefined`, so no text shows. Concrete case: a student whose token has expired types the correct flag. The server replies 403 before looking at it, and the page shows the wrong-answer panel with no message. `refresh()` is skipped (178–180), which is fine because nothing changed on the server.
- **Non-JSON errors are mislabelled as network errors.** The `/api` rate limiter replies 429 with a plain-text body (`server.ts:114-120`, `123`), and a proxy 502/504 would be HTML. In both cases `res.json()` fails, `body` is `null`, and line 156 throws `"Network error"`. The catch (181–185) then shows that as `correct: false`, again as a wrong answer.
- **The hint handler in the same file already does this correctly.** `revealNextHint` checks `res.ok` before the empty-body check (206–214), keeps errors in their own `hintError` state (85), and shows them in a separate `role="alert"` line (366–370). The submit path was never brought into line with it.
- **The `solveMap` mentioned in the finding is not a separate defect here.** The page reads it at line 141 and only refreshes it after a correct grading (178–180). This fix keeps that as it is.

## Approach

Only `ChallengePage.tsx` changes. The server's status codes are already right; the client ignores them.

1. Add a `submitError` state (`string | null`) alongside `hintError`. Render it below the submit row as `<p role="alert">`, with the same rose styling as `hintError` (366–370).
2. Rewrite how `submitFlag` classifies the response, in the same order as `revealNextHint`:
   - Clear `submitError` when a submission starts.
   - Parse the body as `(SubmitResponse & { error?: string }) | null`, as at 203–205.
   - If `!res.ok`: clear `result` and set `submitError` to `Couldn't check your flag (<status>[: <server error>]). Try again.` Then return.
   - If the body is `null` or `typeof body.correct !== "boolean"`: clear `result` and set `submitError` to `Couldn't check your flag — unexpected response. Try again.` Then return.
   - Otherwise it is a real grading: `setResult(body)`. The existing celebration and `refresh()` code stays exactly as it is.
   - `catch`, which is now reached only when `fetch` itself rejects: log `console.error("[ctf/submit] failed:", err)` as at 229, clear `result`, and set `submitError` to `Network error — check your connection and try again.`

The pass/fail panel then only ever shows an actual grading from the server. Every other outcome shows as an error, the flag stays in the input, and the student can resubmit.

## Slices

1. Add `submitError` state to `ChallengePage` and render it as a `role="alert"` paragraph under the flag input row, styled like the existing `hintError` line.
2. Rewrite response handling in `submitFlag`: clear `submitError` at start, check `res.ok` before the empty-body check, only call `setResult` for a 2xx body with a boolean `correct`, and send every other outcome (non-2xx, empty or odd 2xx, network rejection) to `submitError` with `result` cleared.

## Behaviors

1. A 200 reply with `correct: false` shows the amber "Not quite — try again." panel, unchanged from today.
2. A 200 reply with `correct: true` shows the emerald panel, fires celebrations on a first solve, and refreshes challenge progress, unchanged from today.
3. A non-2xx reply with a JSON `{ error }` body (400, 401, 403, 404, 413) shows a `role="alert"` message with the HTTP status and the server's error string, and no pass/fail panel.
4. A non-2xx reply with a non-JSON body (429 plain text from the rate limiter, 502/504 HTML from a proxy) shows the alert with the HTTP status, not "Network error".
5. A 2xx reply that is empty, unparseable, or has no boolean `correct` shows the "unexpected response" alert and no pass/fail panel.
6. When `fetch` rejects, the page shows "Network error — check your connection and try again." and logs `[ctf/submit] failed:` to the console.
7. A failed submission removes any pass/fail panel left from an earlier submission, and the next graded submission removes the alert.
8. After any failure the typed flag is still in the input.
9. `refresh()` is not called after any failed submission.

## Acceptance criteria

- The diff changes `src/components/pathways/challenges/ChallengePage.tsx` and nothing else.
- In `submitFlag`, `res.ok` is checked before the null-body check and before any `setResult` call.
- `setResult` is called with a non-null value only when `res.ok` is true and `typeof body.correct === "boolean"`.
- Every failure branch calls `setResult(null)` and sets `submitError`. None of them calls `setResult({ correct: false, ... })`.
- A `role="alert"` element renders `submitError` when it is non-null.
- The celebration block and `if (body.correct) refresh()` are unchanged apart from indentation.
- Manual check: with `localStorage.bb_access_token` set to an invalid string, submitting any flag shows `Couldn't check your flag (403: forbidden_invalid_token). Try again.` and no `XCircle` or amber panel.
- Manual check: with a valid session, a wrong flag still shows "Not quite — try again.", and the correct flag still shows "Flag captured!" plus the XP line.
- `npm run lint`, `npm run typecheck`, `npm run test:unit` and `npm run build` pass. No gate configuration, test, or lint rule is changed.

## Decisions

- Failures go into a separate `submitError` state rather than `setResult({ correct: false, message })`. The rejected option is what the current catch does (181–185), and the panel still draws it as a wrong answer (411–421), which is the defect.
- `res.ok` is checked before the null-body check, in the same order as `revealNextHint` (206–214). Checking the body first would keep calling 429 and 502 replies "Network error".
- A 2xx body must have a boolean `correct` before it is shown as a grading. The server only grades through that field (pathways.ts 1156–1161, 1170–1174, 1258–1260). Trusting any 2xx body was rejected because it leaves the same `correct: undefined` path open.
- The fix is client-only. Making the server return 200 with `correct: false` for auth or validation failures was rejected: the server's statuses are correct, and changing them would affect every other caller.
- The error text is one pattern for every status (status code plus the server's `error` string). Friendlier per-status wording, such as "sign in again" for 401/403, was rejected for now to keep the change small. It is listed under open questions.
- The single `try`/`catch` stays, rather than a narrower `try` around `fetch` alone. The celebration code only runs on a 2xx body that has passed the new check, and the server always sends `badges` as an array (pathways.ts:1270). A narrower `try` would be more restructuring than the defect needs.
- `useChallenges.ts` is not touched. When its GET fails it falls back to an empty `solveMap` (38–40, 44–45). That is a separate behaviour, not the submit misclassification this finding is about.
- No server-side error handling is added to the submit route. That is a separate server defect (see open questions), and it would widen the change into `backend/`.
- No regression test file is proposed. The proposal schema only accepts `touched_paths` that already exist, and there is no test file for any `src/components/pathways/**` component (checked by glob). A new `ChallengePage.test.tsx` could not be declared, and it would fail the diff-versus-touched-paths check.
- No new dependency.

## Open questions

- A regression test belongs in a new `src/components/pathways/challenges/ChallengePage.test.tsx` (vitest `unit` project, jsdom, `vitest.config.ts:14-19`), but this package cannot declare a new path. Should a maintainer allow it by revision, or track it as a follow-up item?
- Should 401/403 replies get a specific "your session has expired — sign in again" message instead of the generic status text?
- The backend runs Express 4.22.1 (`backend/package-lock.json:1849-1850`), which does not forward rejected async handlers to the error middleware. The submit handler (pathways.ts:1128–1275) has no `try`/`catch`, so a throw after `pathwayCtfSolve.create` (1187), for example in `awardXp` (1200), leaves the request hanging. `server.ts:283-285` only logs it, and the solve may be saved without XP. This fix makes the client report whatever timeout status the proxy returns, but should the server side be a separate audit item?
- I did not run any gate for this proposal. `gate_expectation: green` is a prediction from the scope of the change (one TSX file, no config), not an observed result.

## Touched paths

- src/components/pathways/challenges/ChallengePage.tsx

## Risks

- **No automated coverage.** Because a new test file cannot be declared, the reviewer's evidence is the manual checks under acceptance criteria plus reading the diff. The reviewer should look hardest at the branch order in `submitFlag` and at every failure branch clearing `result`.
- **Visible text change.** Students will see new error wording, sometimes including raw server codes such as `forbidden_invalid_token`. This is the existing convention in the hint path (line 208), but the reviewer should confirm it is acceptable wording for students.
- **Celebration code inside the `try`.** If the server ever sent a malformed `gamification.badges`, the throw would be reported as a network error after a correct grading had already been shown. The server does not do this today (pathways.ts:1270).
- **Prompt injection.** The issue body has no instructions aimed at an AI agent. It includes the harness's own command table (`/harness ...`), which is guidance for human maintainers; it was read as data and nothing in it was acted on.
- Nothing here touches `.github/`, `prisma/`, migrations, predeploy scripts, `.env*`, or any gate.
