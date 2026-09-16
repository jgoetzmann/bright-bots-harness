---
issue: 50
upstream_issue: 866
title: "fix(hooks): resolve useGradeBand's band load to failed when the courses call throws synchronously"
kind: fix
slices: 2
risk: low
touched_paths:
  - "src/hooks/useGradeBand.ts"
  - "src/hooks/__tests__/useGradeBand.test.tsx"
depends_on: []
estimated_turns: 12
gate_expectation: green
baseline_red: []
---

# fix(hooks): resolve useGradeBand's band load to failed when the courses call throws synchronously

## Issue

https://github.com/Bright-Bots-Initiative/brightboost/issues/866 — the reporter found, while implementing #842, that `src/hooks/useGradeBand.ts:55` invokes `api.getStudentCourses()` with no protection against a synchronous throw. The asynchronous rejection path resolves to `status: "failed"`; the synchronous path escapes the effect instead. Their stated impact: consumers cannot mount without mocking the courses API, and a synchronous throw crashes the component tree rather than degrading to the k2 fallback.

## Diagnosis

The report is accurate about the mechanism, and narrower than it reads about the trigger.

`loadBand` (`src/hooks/useGradeBand.ts:53-70`) builds its promise by invoking the API and chaining off the result in one expression:

```ts
const promise = api.getStudentCourses().then((courses: any[]) => { ... });   // :55
inFlight = { userKey, promise };                                            // :62
```

Both of the things that can go wrong on line 55 happen *before* any promise exists:

1. `api.getStudentCourses` throws synchronously — or is `undefined`, in which case calling it is a synchronous `TypeError`.
2. `api.getStudentCourses()` returns a non-thenable (a bare `vi.fn()` with no configured implementation returns `undefined`), so `.then` of `undefined` is a synchronous `TypeError`.

In either case control never reaches line 62, so `inFlight` is never populated and the rejection-handling machinery at lines 64-68 never attaches. The throw propagates out of `loadBand` into its only caller, the effect body at `src/hooks/useGradeBand.ts:111`. The `.catch` at lines 115-119 — the one that produces `{ band: "k2", status: "failed" }` — is an argument to a method that is never reached, so it cannot catch this. A throw from a `useEffect` callback surfaces during React's passive-effect flush and, with no error boundary above it, unmounts the tree. That is exactly the "crash instead of degrade" the reporter describes.

Two consequences beyond the crash, both from `inFlight` never being set:

- The in-flight dedupe documented at lines 27-31 is defeated on this path. Two consumers on one page (ActivityPlayer mounts `useGradeBand` for content banding at `src/pages/ActivityPlayer.tsx:78` while `useModuleAccess` mounts `useGradeBandState` for the gate at `src/hooks/useModuleAccess.ts:112`) each call `api.getStudentCourses()` and each throw.
- The failure is not observable as a failure. `useModuleAccess` reads `bandStatus === "failed"` to return its honest `ERROR` state for band-discriminating content (`src/hooks/useModuleAccess.ts:196-199`), and `StudentDashboard` reads the same status at `src/pages/StudentDashboard.tsx:95`. A throw bypasses that whole contract.

Where the report overstates: in production this is currently unreachable. `getStudentCourses` is declared `async` (`src/services/api.ts:681-686`), and an `async` function cannot throw synchronously — it always returns a promise. The reachable triggers today are a test double (`vi.fn()` unconfigured, or an `@/services/api` mock that omits the method) and any future refactor of the api surface away from `async`. So this is a robustness/testability defect with a real present cost in test setup, not a live production crash. The evidence for the test-setup cost is in the tree: every consumer suite that renders a component using the hook must stub `getStudentCourses` (`src/pages/__tests__/ActivityPlayerA11y.test.tsx:16`, `ActivityPlayerCompletionLatch.test.tsx:26`, `ModuleDetailHiddenGuard.test.tsx:32`, and nine others), and `src/layouts/__tests__/StudentLayout.test.tsx:20-21` sidesteps it entirely by mocking the hook module.

Existing coverage confirms the gap is real: `src/hooks/__tests__/useGradeBand.test.tsx:49-57` exercises `mockRejectedValue` only. Nothing exercises a synchronous throw.

One further finding the reporter did not mention, and that shapes the fix: the sibling call sites in `src/hooks/useModuleAccess.ts` use `Promise.resolve(api.getAvatar())` (`:133`), `Promise.resolve(api.getProgress(...))` (`:149`) and `Promise.resolve(api.getStudentAssignments())` (`:243`). That pattern normalizes a non-thenable *return value*, but it does **not** guard a synchronous throw — the call is evaluated as an argument, before `Promise.resolve` runs. So the house pattern is not a correct template here, and those three sites carry the same latent defect (see `## Open questions`).

## Approach

Change the one expression in `loadBand` so the call itself happens inside a promise callback:

```ts
const promise = Promise.resolve()
  .then(() => api.getStudentCourses())
  .then((courses: any[]) => { ...unchanged... });
```

This converts both synchronous failure modes into a rejection of `promise`, which is the path the hook already handles correctly. Everything downstream is untouched: `inFlight` is still assigned synchronously on the very next line, so the dedupe holds and concurrent consumers share one failure; the `.catch().finally()` cleanup at lines 64-68 still clears the slot so a `reloadKey` retry re-requests; the effect's `.catch` at line 115 produces `{ band: "k2", status: "failed" }` exactly as it does for an async rejection.

Rejected alternatives, and why:

- `Promise.resolve(api.getStudentCourses())` — matching `useModuleAccess.ts:133` verbatim. Rejected: as shown above, the argument is evaluated first, so it fixes the non-thenable case and leaves the synchronous throw untouched. It would look like a fix and not be one.
- `try { promise = api.getStudentCourses().then(...) } catch (e) { promise = Promise.reject(e) }`. Rejected: more lines, an unused binding to declare, and it still needs a second guard for the non-thenable return. The deferred-call form covers both in one expression.
- Wrapping `loadBand(userKey)` in a `try`/`catch` at the effect call site (line 111). Rejected: it patches the symptom at one caller while `loadBand` stays a function that throws, and — because the throw would still happen before line 62 — the dedupe and retry bookkeeping would still be skipped.

Then add regression coverage for the synchronous path to the existing hook test file.

## Slices

1. `src/hooks/useGradeBand.ts` — defer the `api.getStudentCourses()` invocation into a promise callback inside `loadBand` (lines 53-70), with a short comment naming why the call is deferred. No other line changes.
2. `src/hooks/__tests__/useGradeBand.test.tsx` — add regression tests for the synchronous-throw path: `status: "failed"` with `band: "k2"` and no escaped error, one shared request across two concurrent consumers, and a fresh request after a `reloadKey` bump.

## Behaviors

1. A consumer mounts without error when `api.getStudentCourses` throws synchronously; the error does not escape the effect and the tree stays mounted.
2. After a synchronous throw, `useGradeBandState()` reports `{ band: "k2", status: "failed" }` — the same observable state an async rejection produces.
3. `useGradeBand()` returns `"k2"` after a synchronous throw, matching its existing behaviour on an async rejection.
4. When `api.getStudentCourses` is absent from the api surface (calling it is a `TypeError`), the hook settles rather than throwing.
5. Two consumers mounting in the same commit cause exactly one `api.getStudentCourses` call on the synchronously-throwing path, not one per consumer.
6. Bumping `reloadKey` after a synchronous failure issues a fresh `api.getStudentCourses` call, and a now-succeeding call resolves to `status: "resolved"` with the returned band.
7. The pre-existing async paths are unchanged: `g3_5` when any enrolled course is `g3_5`, no cross-user cache leak after a student switch, and `k2` on an async rejection.

## Acceptance criteria

- The diff touches exactly `src/hooks/useGradeBand.ts` and `src/hooks/__tests__/useGradeBand.test.tsx`, and nothing else.
- Within `src/hooks/useGradeBand.ts`, the change is confined to `loadBand` (lines 53-70); the `useGradeBandState` effect, the cache, the user-key logic and all exported signatures are byte-identical.
- `api.getStudentCourses` is no longer invoked in statement position inside `loadBand` — it is called from within a promise callback.
- `inFlight` is still assigned synchronously in `loadBand`, before any `await` or callback boundary, so the dedupe comment at lines 27-31 remains true.
- Each new test fails against the untouched `useGradeBand.ts` and passes with the change; a reviewer can confirm by reverting slice 1 and re-running `npm run test:unit`.
- `npm run lint`, `npm run typecheck`, `npm run test:unit` and `npm run build` pass.
- No `.skip(`, no `.only(`, no `eslint-disable`, no `@ts-expect-error`, no timeout or threshold change, no file under `.github/` in the diff.
- No new dependency; no change to `src/services/api.ts` or to any consumer.

## Decisions

- Defer the call (`Promise.resolve().then(() => api.getStudentCourses())`) rather than wrap it (`Promise.resolve(api.getStudentCourses())`). Rejected the wrap because the argument is evaluated before `Promise.resolve` runs, so it does not catch a synchronous throw — it would be a fix in appearance only, and it is the pattern already in `useModuleAccess.ts:133`, so copying it would spread the mistake.
- Fix inside `loadBand` rather than at the effect call site (`useGradeBand.ts:111`). Rejected the call-site `try`/`catch` because it leaves `loadBand` throwing for any future caller and, since the throw still precedes line 62, the `inFlight` dedupe and the retry-slot cleanup would both still be skipped.
- Chose the deferred form over an explicit `try`/`catch` inside `loadBand`. Rejected the `try`/`catch` because it needs a mutable binding and a second guard for a non-thenable return, where the deferred form handles both in one expression and reads closer to the surrounding code.
- Keep the `courses?.some(...)` tolerance at line 57 exactly as it is. Rejected adding an `Array.isArray` guard that would turn a resolved-but-null body into `status: "failed"`: that changes the access gate's answer for a malformed response, is not what #866 reports, and is a product call (see `## Open questions`).
- Limit the change to `useGradeBand.ts`. Rejected fixing the three identical hazards in `useModuleAccess.ts` (`:133`, `:149`, `:243`) in the same diff: it triples the surface, shifts the call timing of three more effects that consumer suites depend on, and the issue names one hook. Raised as an open question instead.
- Add the regression tests to the existing `src/hooks/__tests__/useGradeBand.test.tsx` rather than a new file, matching where the async-failure test already lives (lines 49-57).
- Assert the new cases through `useGradeBandState`, not `useGradeBand`. Rejected asserting via `useGradeBand` alone because it returns only `band` (line 129), so a test through it could not distinguish `failed` from a legitimately resolved `k2` — the exact distinction the hook's own doc comment at lines 10-21 says matters.
- Report `gate_expectation: "green"` with an empty `baseline_red`. This is a prediction, not a measurement: `node_modules` is absent in this clone, so no gate was run during planning (see `## Risks`).

## Open questions

- Should the three sibling call sites in `src/hooks/useModuleAccess.ts` (`:133` avatar, `:149` progress, `:243` assignments) get the same deferral? They carry the identical defect and their `Promise.resolve(...)` wrapper does not prevent it. I propose a separate issue rather than widening this diff, but if the reviewer wants one sweep, that changes the scope of this package.
- Should a resolved-but-non-array `/student/courses` body be `failed` rather than a silent `resolved` `k2`? Today `courses?.some` (line 57) turns `null`/`undefined` into `k2` with `status: "resolved"`, which an access consumer reads as a real answer. After this fix, an unconfigured `vi.fn()` returning `undefined` takes that path instead of crashing — arguably a quieter failure than the crash it replaces, in a test-only scenario. Changing it affects the gate's behaviour on a malformed production response, so it needs a product decision; out of scope here.
- Does the project want a lint rule or shared helper (e.g. a `deferredCall` wrapper in `src/services/`) to make this class of defect unrepeatable? Not proposed here — it is an architectural call, and one call site does not justify an abstraction.

## Touched paths

- src/hooks/useGradeBand.ts
- src/hooks/__tests__/useGradeBand.test.tsx

## Risks

- **Call timing shifts by one microtask.** `api.getStudentCourses()` now runs in a promise callback instead of synchronously during the effect. Any test asserting the call happened without awaiting would break. The hook's own tests use `waitFor` (`useGradeBand.test.tsx:45`, `:55`), so they are safe, but this is the thing to look hardest at: consumer suites that queue `mockResolvedValueOnce` sequences (`ActivityPlayerQuizGate.test.tsx:126-138`, `ModuleAccessDeepLink.test.tsx:312-319`) depend on call *order*. Order among `getStudentCourses` calls is preserved — the dedupe still makes them a single call — but ordering relative to other effects' requests moves. `npm run test:unit` is the check.
- **Dedupe is the subtle part.** If the implementation moves the `inFlight` assignment (line 62) into the deferred callback, two consumers would each fire a request before the slot is set — a regression the async tests would not catch. Behavior 5 exists to pin this; the reviewer should confirm `inFlight = { userKey, promise }` is still the statement immediately after the promise is constructed.
- **The fix converts a loud failure into a quiet one for one input shape.** An unconfigured mock returning `undefined` currently crashes the test; afterwards it resolves to `k2`/`resolved`. That is the intended direction (degrade, do not crash), but a suite that was passing only because a crash masked a missing stub would now silently assert against the k2 default. Covered by the second open question.
- **No gate was run during planning.** `node_modules` is not installed in this clone (`node_modules/.bin/vitest` is absent), so `lint`, `typecheck`, `test:unit` and `build` were not executed and no baseline red was measured. `gate_expectation: "green"` is an expectation from reading the code, not an observation. Implementation must run the gates and report what they actually do.
- **The issue body contained no instructions aimed at an AI agent** — no attempt to redirect rules, run commands, or touch unrelated paths. It is a normal bug report, and I treated it as data regardless. Its one prescriptive line ("move the call inside the try") does not match the code, which has no `try` in `loadBand`; I did not follow it literally.
- Nothing under `.github/`, `prisma/`, `migrations/`, or `backend/scripts/predeploy*` is touched, and no dependency is added.
