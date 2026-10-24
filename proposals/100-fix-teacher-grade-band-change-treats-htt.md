---
issue: 100
upstream_issue: null
title: "fix(teacher): grade-band change treats http errors as success"
kind: fix
slices: 4
risk: low
touched_paths:
  - "src/services/api.ts"
  - "src/pages/TeacherClassDetail.tsx"
  - "src/locales/en/common.json"
  - "src/locales/es/common.json"
  - "src/locales/vi/common.json"
  - "src/locales/zh-CN/common.json"
  - "src/pages/__tests__/TeacherAssignmentPicker.test.tsx"
depends_on: []
estimated_turns: 35
gate_expectation: green
baseline_red: []
---

# fix(teacher): grade-band change treats http errors as success

## Issue

Harness issue #100 tracks finding 6 of audit #61 (https://github.com/jgoetzmann/bright-bots-harness/issues/61), machine reference `audit:61:6`. There is no product-repository issue. Severity is listed as high. The reporter named these places: `updateCourseBand`, `res.json()`, `res.ok`, `setCourse`, `catch { /* ignore */ }`. The title says: "Grade-band change reports success on any HTTP error".

## Diagnosis

**1. The service never checks `res.ok`.** `src/services/api.ts:631-641` (`updateCourseBand`) sends `PUT /teacher/courses/:courseId/band` and returns `res.json()` at line 640 without checking the status. Other functions in the same object do check it:
- `getSpecialtyStatus` throws at `api.ts:488`.
- `getModule` throws a typed `ApiError` at `api.ts:537-548`.
- `completeActivity` throws at `api.ts:584-589`.

**2. The backend does return real error statuses.** The route at `backend/src/routes/moduleCatalog.ts:75-99` (mounted under `/api` at `backend/src/server.ts:203`) returns:
- `400 { error }` when validation fails (lines 81-83).
- `404 { error: "Course not found" }` when the course is missing or belongs to another teacher (lines 88-90).
- `{ id, gradeBand }` on success (line 97).
- whatever `requireAuth` / `requireRole("teacher")` send (lines 77-78). I did not read those bodies.

The backend is correct. The defect is entirely in the frontend.

**3. The page has one caller and hides every failure.** The only caller is the band `<select>` at `src/pages/TeacherClassDetail.tsx:636-647` (grep for `updateCourseBand` in `src/` finds only line 640). Its `onChange`:
- awaits `directApi.updateCourseBand(course.id, e.target.value)` (line 640),
- then calls `setCourse(... gradeBand: e.target.value ...)` (lines 641-643),
- and wraps both in `catch { /* ignore */ }` (lines 644-646).

That gives three failure modes, and the teacher is told nothing in any of them:
- **Error status with a JSON body** (the route's own 400/404): `res.json()` resolves, the service returns normally, and the page runs its success branch (`setCourse`). This is the finding as reported.
- **Error status with a non-JSON body** (for example an HTML page from a proxy): `res.json()` throws `SyntaxError`, and line 645 discards it.
- **Network failure**: `fetch` rejects, and line 645 discards it.

This page's other actions go through `useApi()`. That hook turns non-2xx into `ApiError` (`api.ts:348-364`) and shows toasts for network/5xx (`api.ts:386-401`); the page's comments at lines 379, 392, 516, 538 and 565 rely on it. The band select uses the shim `directApi` (`api.ts:483`) instead, so it gets none of that.

**4. Likely second defect on the success path (inferred from reading the code, not reproduced).** The `<select>` is controlled by `course.gradeBand` (line 637). The handler reads `e.target.value` a second time *after* the `await` (line 642). In React 18 (`package.json`: `react-dom ^18.3.1`), when a change handler does not set state before returning, React resets a controlled select's DOM value to its current prop as soon as the handler returns. So by the time the request resolves, `e.target.value` is probably the *old* band again. The API call itself gets the right value, because line 640 reads it before the `await`. The likely result: on success the server saves the new band but the select shows the old one until the page is reloaded. I did not run anything to confirm this (`node_modules` is not installed in this clone). The success-path test in slice 4 checks it either way.

**Other facts that shape the plan:**
- `enTranslate` in `src/test/i18nMock.ts:35-37,52-53` ignores `defaultValue`. A key that only exists as a `defaultValue` shows up as the raw key in tests.
- `src/i18n.ts:37` sets `fallbackLng: "en"`.
- `teacher.classDetail.*` keys exist in all four `common.json` files (for example `backToClasses` at en:551, es:547, vi:544, zh-CN:544).

## Approach

1. **Service.** Make `updateCourseBand` read the body with `res.json().catch(() => null)`. When `!res.ok`, throw `new ApiError(extractErrorMessage(body) || \`Request failed: ${res.status}\`, res.status)`; otherwise return the body. This is the `completeActivity` pattern (`api.ts:584-589`) plus the typed status from `getModule` (`api.ts:548`). `extractErrorMessage` already exists at `api.ts:24-32`.
2. **Page.** In the `onChange` at `TeacherClassDetail.tsx:638-647`:
   - Copy the chosen band into a local `const` before the `await`, and use it for both the API call and `setCourse`.
   - Replace `/* ignore */` with a destructive toast from `useToast()` (`@/hooks/use-toast`, the pattern at `src/pages/ModuleDetail.tsx:7,27,64-68`). Its title is a new localized key, `teacher.classDetail.bandUpdateFailed`.
   - Leave `course` unchanged on failure, so the controlled select keeps showing the saved band.
3. **Locales.** Add the new key under `teacher.classDetail` in all four `common.json` files, right after `backToClasses`.
4. **Tests.** Add regression tests to the only existing test that mounts this page. They cover the page's success and failure paths and the service's status handling, using a stubbed `fetch`.

No backend change is needed.

## Slices

1. In `src/services/api.ts`, make `updateCourseBand` throw an `ApiError` carrying the HTTP status and the backend's `error` message (fallback `Request failed: <status>`) when `!res.ok`, and return the parsed body on 2xx.
2. In `src/pages/TeacherClassDetail.tsx`, copy the selected band into a local before the `await`, use it for the request and for `setCourse`, and replace the silent `catch` with a destructive toast titled `t("teacher.classDetail.bandUpdateFailed")`.
3. Add `teacher.classDetail.bandUpdateFailed` to `src/locales/{en,es,vi,zh-CN}/common.json` at the same nesting, after `backToClasses`.
4. In `src/pages/__tests__/TeacherAssignmentPicker.test.tsx`:
   - add `updateCourseBand` to the mocked `api` and mock `useToast` with a hoisted spy;
   - add a `describe` block for the band select: success, rejection, and no toast on success;
   - add a `describe` block for the real `updateCourseBand` (via `vi.importActual`) against a stubbed `fetch`: 200, 404 with JSON body, 502 with non-JSON body, network rejection.

## Behaviors

1. When the band PUT returns 2xx, the select shows the chosen band without a reload.
2. When the band PUT returns 2xx, no error toast is shown.
3. When the band PUT returns a non-2xx status, the select keeps showing the previously saved band.
4. When the band PUT returns a non-2xx status, a destructive toast with the localized "could not change the grade band" message is shown.
5. When the band PUT fails at the network level, the select keeps showing the saved band and the same toast is shown.
6. `api.updateCourseBand` rejects with an `ApiError` whose `status` equals the response status whenever the response is not 2xx.
7. The rejection message is the backend's `error` string when the body is JSON with one (for example `Course not found` for 404), and `Request failed: <status>` when the body is not JSON.
8. `api.updateCourseBand` resolves with the parsed response body on 2xx.

## Acceptance criteria

- `updateCourseBand` in `src/services/api.ts` checks `res.ok` before returning and throws `ApiError` when it is false.
- No other function in `src/services/api.ts` changes.
- The band `onChange` in `src/pages/TeacherClassDetail.tsx` no longer contains `/* ignore */`, and its `catch` calls `toast` with `variant: "destructive"`.
- The band value is read from the event exactly once, before the `await`, and that local is used for both the request and `setCourse`.
- `teacher.classDetail.bandUpdateFailed` exists in all four `common.json` files at the same path, and each file is still valid JSON.
- The new page tests show the select at `g3_5` after a resolved update, at `k2` after a rejected update, and a toast only on rejection.
- The new service tests cover 200, 404 with a JSON body, 502 with a non-JSON body, and network rejection.
- The three existing tests in `TeacherAssignmentPicker.test.tsx` pass with their assertions unchanged.
- No file outside `## Touched paths` is changed; no backend, `prisma/`, or `.github/` file is touched.
- `npm run lint`, `npm run typecheck`, and `npm run test:unit` pass.
- Prettier is applied only to the touched files.
- Every commit message passes `@commitlint/config-conventional`, with header and body lines of 100 characters or fewer.

## Decisions

- The service throws; the page does not inspect the response. Rejected: checking `res.ok` in the page. The page never sees the `Response`, and every other checked function in `api` throws (`api.ts:488`, `548`, `586`).
- `ApiError` rather than plain `Error`. It carries the status, and the comment at `api.ts:545-548` explains why callers need that. Rejected: `new Error(...)` as in `completeActivity`, which loses the status.
- The body is read with `res.json().catch(() => null)` as at `api.ts:584`. Rejected: `safeJson` (`api.ts:473-481`). On a non-JSON body it returns `{ message: text }`, which would put raw HTML into the error message.
- The failure is shown with a toast. Rejected: an inline notice beside the select. It needs new state and markup, while toasts are how this page already reports action errors (comments at lines 379, 392, 516, 538, 565, and `ModuleDetail.tsx:64-68`).
- The toast shows a fixed localized message. Rejected: showing the backend's error string, which is English-only and may be a raw zod message (`moduleCatalog.ts:82`).
- The page keeps using `directApi.updateCourseBand`. Rejected: switching to `useApi().put`. That would bring in rate limiting, two automatic retries, and a second "Network Error" toast on 5xx (`api.ts:379-401`). It would still show nothing on 4xx, and it would leave `api.updateCourseBand` dead and still broken.
- The band is copied into a local before the `await`. This removes any dependence on the DOM value after the async gap. It is correct whether or not the inferred stale-value problem (Diagnosis point 4) reproduces. Rejected: keeping the second `e.target.value` read.
- On success, `setCourse` uses the band the teacher chose, not `gradeBand` from the response. The server only returns 2xx after validating the value against the same two-value enum (`moduleCatalog.ts:16,31-33,97`). Rejected: reading the response body, which adds a dependency on its shape with no behavior difference.
- No optimistic update and rollback. The current flow already updates state only after the request, which is the smaller change. Rejected: an optimistic update, which adds rollback logic.
- Several sibling shim functions have the same unchecked pattern (`getModuleFamilies`, `getModuleVariants`, `getCourseAssignments`, `assignModuleToClass`, `removeModuleAssignment`, at `api.ts:614-679`). They are not changed. A grep of `src/` finds no callers, so they cause no user-visible failure, and changing them goes beyond this finding.
- The select is not disabled while a request is in flight, and out-of-order responses from rapid changes are not handled. Neither is part of this finding, and both would add state.
- The tests go in the existing `src/pages/__tests__/TeacherAssignmentPicker.test.tsx`, not a new file. `touched_paths` may only list paths that exist now. This file is the only one that already mounts `TeacherClassDetail` with a mocked `api` (lines 27-39, 62-74, 100-108).
- The new key goes into all four locales with real translations. Rejected: English only with fallback (`i18n.ts:37`). Spanish is eagerly loaded (`i18n.ts:33`), and all four files already carry `teacher.classDetail`.
- The toast is observed through a hoisted `vi.fn()` passed into a mocked `useToast`. Rejected: rendering `<Toaster />` in the test. The toast store is module-global (`use-toast.ts:44`), so toasts could leak between tests.

## Open questions

- Should the regression tests live in a dedicated file (for example `src/pages/__tests__/TeacherClassDetailBand.test.tsx`)? The harness cannot propose a path that does not exist yet. A reviewer who wants that can ask with `/harness revise`.
- Should the Spanish, Vietnamese and Simplified Chinese strings for `bandUpdateFailed` be checked by a native speaker? The alternative is to follow the `attention.*` precedent (`vi/common.json:546-547`, `zh-CN/common.json:546-547`), where vi and zh-CN hold English text.
- I have not confirmed Diagnosis point 4 (the stale `e.target.value` after the `await`) by running code. If the success-path test passes on the untouched page, point 4 is wrong; copying the value into a local is still harmless and stays in the plan.
- I ran no gate on the untouched tree. `gate_expectation: green` is an assumption, not an observation.

## Touched paths

- src/services/api.ts
- src/pages/TeacherClassDetail.tsx
- src/locales/en/common.json
- src/locales/es/common.json
- src/locales/vi/common.json
- src/locales/zh-CN/common.json
- src/pages/__tests__/TeacherAssignmentPicker.test.tsx

## Risks

- **New mocks in a shared test file.** Adding a `useToast` mock and an `updateCourseBand` entry to the `vi.mock("@/services/api")` factory also affects the three existing picker tests. The reviewer should check that their assertions are unchanged and still pass. The existing `findByRole("combobox")` at line 116 relies on the open dialog hiding the band select, and nothing here should change that.
- **`vi.importActual` in a file that mocks the same module.** The service tests load the real `@/services/api` in a file that mocks it. Any `fetch` stub must be undone after each test (`vi.unstubAllGlobals()` or restoring `global.fetch`) so it does not leak into the page tests.
- **Locale JSON.** One stray comma in any `common.json` breaks the build and every test that imports `en/common.json` through `i18nMock`. Prettier should run only on the four changed files, never tree-wide.
- **A behavior change teachers will see.** On error, teachers will now get a toast where before they saw nothing. On success, if Diagnosis point 4 is right, the select will now show the new band where before it appeared to snap back. The reviewer should confirm this matches product intent.
- **Adjacent issue not addressed.** The launch-wizard module list is filtered by the class band when it is first opened and cached (`TeacherClassDetail.tsx:460-483`). It is not refreshed after a band change. That is out of scope and not fixed here.
- **Content of the issue body.** It holds no instructions aimed at an AI. It includes the harness's own table of `/harness …` steering commands for trusted maintainers. I treated that as data and acted on none of them.
