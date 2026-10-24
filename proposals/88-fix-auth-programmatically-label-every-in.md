---
issue: 88
upstream_issue: null
title: "fix(auth): programmatically label every input on the live login routes"
kind: fix
slices: 5
risk: low
touched_paths:
  - "src/pages/TeacherLogin.tsx"
  - "src/pages/LoginSelection.tsx"
  - "src/pages/StudentClassLogin.tsx"
  - "src/pages/__tests__/StudentClassLogin.test.tsx"
depends_on: []
estimated_turns: 40
gate_expectation: green
baseline_red: []
---

# fix(auth): programmatically label every input on the live login routes

## Issue

Harness issue #88 tracks finding 2 (`audit:63:2`) of audit https://github.com/jgoetzmann/bright-bots-harness/issues/63. No product-repository issue exists.

The reporter's words: severity **high**. Paths named: `/student-login`, `/teacher-login`, `/class-login`, `<label>`, `htmlFor`, `LoginInput`, `id`. Title: "Every live login route has programmatically unlabelled fields."

## Diagnosis

The three live login routes are set up in `src/App.tsx:136-138`:
- `/teacher-login` renders `TeacherLogin`.
- `/student-login` renders `LoginSelection`.
- `/class-login` renders `StudentClassLogin`.

The legacy paths redirect to these (`src/App.tsx:139-151`). `src/pages/StudentLogin.tsx` and `src/pages/Login.tsx` are not routed (`src/App.tsx:23`). `StudentLogin.tsx` already links its labels correctly (`StudentLogin.tsx:93-100`), but it is dead code and not part of this defect.

**The shared component is not the cause.** `LoginInput` (`src/components/auth/LoginCard.tsx:43-51`) spreads `{...props}` onto the `<input>`, so an `id` passed by a caller reaches the DOM already. The defect is at the call sites: none of them pass an `id`, and none link a label.

**`/teacher-login`, `src/pages/TeacherLogin.tsx:41-48`**
- A visible `<label>Email</label>` (line 42) and `<label>Password</label>` (line 46) sit next to their `LoginInput`s (lines 43, 47), not around them.
- The labels have no `htmlFor` and the inputs have no `id`, so there is no explicit or implicit link.
- The only name left is the placeholder. A screen reader announces the email field as "teacher@school.com" rather than "Email".
- Clicking the label text does not focus the field.

**`/student-login` main view, `src/pages/LoginSelection.tsx:262-297`**
- The email (line 268), password (line 269) and code (lines 286-291) `LoginInput`s have no label element, `aria-label` or `aria-labelledby`. Only placeholders: "Email address", "Password", "Class code or cohort code".
- The section headings at 263-266 and 281-284 are plain `<div>`s, not linked to the inputs.
- Some automated checkers accept a placeholder as a last-resort name. But a placeholder disappears once the user types, and testing-library's `getByLabelText` does not find these fields.

**`/student-login` Pathways cohort view, `src/pages/LoginSelection.tsx:160-256`**
This view renders on the same route once a cohort code resolves.
- The returning-user password input (lines 191-197) has only a placeholder.
- The four registration labels ("Your Name" 219, "Email (optional)" 225, "Password" 231, "Birth Year" 237) have no `htmlFor`. Their inputs (220, 226, 232, 238) have no `id`.

**`/class-login` code step, `src/pages/StudentClassLogin.tsx:199-225`**
- The class-code `<input>` (lines 213-224) has no label. Its only name is the placeholder "ABC123", which is an example, not a label.
- The visible question "What's your class code?" is an `<h1>` (lines 204-206) that is not linked to the input.

**`/class-login` PIN step, `src/pages/StudentClassLogin.tsx:302-357`**
- `InputOTP` (lines 329-337) gets no `aria-label` or `aria-labelledby`.
- `InputOTPSlot` renders plain `<div>`s (`src/components/ui/input-otp.tsx:31-56`). So the only focusable element is the library's own `<input>`, and it gets no placeholder either. It has no accessible name at all.
- The visible prompt "Type your secret number" (lines 323-325) is not linked.

**Existing selectors that must keep working.** Cypress and the unit test find these inputs by type and placeholder, not by label:
- `cypress/e2e/auth-login.cy.ts:12-13`
- `cypress/e2e/pathways-consent.cy.ts:235-236`
- `cypress/e2e/student-join.cy.ts:33`
- `cypress/e2e/legacy/k2InstantQuiz.helpers.js:183-184`
- `src/pages/__tests__/StudentClassLogin.test.tsx:66,79`

So the fix must keep every existing `type` and `placeholder`.

## Approach

Add the missing links at each call site using attributes and label elements only. Placeholders, input types, handlers and visible layout stay as they are.

1. **`TeacherLogin.tsx`.** Add `htmlFor="teacher-login-email"` / `id="teacher-login-email"` and `htmlFor="teacher-login-password"` / `id="teacher-login-password"` to the existing label/input pairs at lines 42-43 and 46-47. The visible labels become the accessible names.
2. **`LoginSelection.tsx` main view.**
   - Wrap each of the three `LoginInput`s in a `<div>`, as `TeacherLogin.tsx:41-48` already does.
   - Put a visually hidden `<label className="sr-only" htmlFor=…>` before each input: ids `student-login-email`, `student-login-password`, `student-login-code`.
   - Label text copies the placeholder: "Email address", "Password", "Class code or cohort code". The spoken name then matches what sighted users see (WCAG 2.5.3).
   - The `<div>` wrapper keeps the new label from becoming a direct child of the `space-y-3` form, so input spacing does not shift.
3. **`LoginSelection.tsx` Pathways view.**
   - Add `htmlFor`/`id` pairs to the four registration label/input pairs: `pathways-register-name`, `pathways-register-email`, `pathways-register-password`, `pathways-register-birth-year`.
   - Wrap the returning-user password input with a visually hidden label "Enter your password" (`pathways-roster-password`) in a `<div>`.
4. **`StudentClassLogin.tsx`.**
   - Give the code-step `<h1>` `id="class-login-code-heading"` and the code input `aria-labelledby="class-login-code-heading"`.
   - Give the PIN prompt `<p>` `id="class-login-pin-prompt"` and pass `aria-labelledby="class-login-pin-prompt"` to `InputOTP`.
   - This reuses text that is already translated (`classLogin.title`, `classLogin.enterPin`) in all four locales, and adds no strings.
5. **Tests.** Add a regression `describe` block to the existing `src/pages/__tests__/StudentClassLogin.test.tsx`. It renders each route's page and checks with `getByLabelText` that every field listed above resolves to the right `<input>`.

## Slices

1. `src/pages/TeacherLogin.tsx`: link the existing "Email" and "Password" labels to their inputs with `htmlFor`/`id`.
2. `src/pages/LoginSelection.tsx` main view: wrap the email, password and code inputs in `<div>`s and add visually hidden labels linked by `htmlFor`/`id`.
3. `src/pages/LoginSelection.tsx` Pathways cohort view: link the four registration labels with `htmlFor`/`id`, and add a visually hidden linked label to the returning-user password input.
4. `src/pages/StudentClassLogin.tsx`: name the class-code input and the PIN `InputOTP` with `aria-labelledby`, pointing at the existing visible heading and prompt.
5. `src/pages/__tests__/StudentClassLogin.test.tsx`: add regression tests that look up every field on all three routes by its accessible label.

## Behaviors

1. On `/teacher-login`, the email input's accessible name is "Email" and the password input's is "Password".
2. On `/teacher-login`, clicking the visible "Email" or "Password" label text moves focus to that input.
3. On `/student-login`, the email, password and code inputs have the accessible names "Email address", "Password" and "Class code or cohort code".
4. The `/student-login` main view looks the same as before: the new labels are visually hidden and input spacing is unchanged.
5. On `/student-login`, after a Pathways cohort code resolves, the registration inputs are named "Your Name", "Email (optional)", "Password" and "Birth Year".
6. On `/student-login`, after a Pathways cohort code resolves and a roster name is chosen, the returning-user password input is named "Enter your password".
7. On `/class-login` code step, the class-code input's accessible name is the heading text ("What's your class code?" in English) and follows the active language.
8. On `/class-login` PIN step, the PIN input's accessible name is the prompt text ("Type your secret number" in English).
9. Every existing placeholder, input `type`, submit handler and navigation behaves exactly as before.

## Acceptance criteria

- The diff touches exactly the four files under `## Touched paths` and no others.
- `src/pages/TeacherLogin.tsx` has `htmlFor`/`id` pairs `teacher-login-email` and `teacher-login-password`, and no other changes.
- Every `<input>` / `LoginInput` / `InputOTP` rendered by `LoginSelection.tsx` and `StudentClassLogin.tsx` has an `id` referenced by a `<label htmlFor>`, or an `aria-labelledby` pointing at an element that exists in the same render.
- No placeholder string or input `type` attribute changes in any touched page (check the diff).
- No locale JSON file changes and no new i18n key is added.
- No new dependency and no change to `package.json` or lockfiles.
- The new tests find each field from Behaviors 1, 3, 5, 6, 7 and 8 with `screen.getByLabelText(<name>)` and check it is an `INPUT` element of the expected type.
- A new test checks Behavior 2 by clicking the "Email" label on `/teacher-login` and asserting the email input has focus.
- The two existing tests in `src/pages/__tests__/StudentClassLogin.test.tsx` still pass unchanged.
- `npm run lint`, `npm run typecheck` and `npm run test:unit` pass.

## Decisions

- Fix the call sites, not `LoginInput`. `LoginInput` already forwards `id` (`LoginCard.tsx:47-48`). Rejected: a `label` prop on `LoginInput`. It would change a shared API, cover only 5 of the 12 affected fields (the Pathways view and `/class-login` do not use it), and change the visible layout of `TeacherLogin`.
- Use fixed, page-prefixed ids (`teacher-login-email`, …), as `HomeAccessAccept.tsx:194-201` does. Rejected: `React.useId()`. Each page mounts once per route, so fixed ids cannot collide, and they are readable in tests and devtools. Also rejected: bare `email`/`password`. Unrouted legacy pages use those ids, and legacy Cypress selectors (`cypress/e2e/legacy/demoFlow.cy.ts:38`) look for `input#email`, so reusing them would create an accidental coupling.
- Visually hidden (`sr-only`) labels on the `/student-login` main view. Rejected: visible labels, because they change the child-facing layout, which is a design decision (listed under Open questions). The visible labels that already exist on `TeacherLogin` and the Pathways registration form are linked, not replaced.
- Hidden-label text copies each placeholder word for word. Rejected: new i18n keys, which would mean edits to four locale files for strings the page itself shows untranslated (`LoginSelection.tsx:268-269,288`). Also rejected: reusing `studentLogin.form.*` keys, which belong to the unrouted `StudentLogin.tsx`.
- `/class-login` uses `aria-labelledby` pointing at the existing heading and prompt. Rejected: a new `<label>` or `aria-label`. Those would duplicate visible text, add strings, and could drift from what is shown. The referenced text is already translated in all four locales.
- Each new hidden label goes in a `<div>` with its input. Rejected: putting the `<label>` straight into the `space-y-3` containers, where it could become a direct child and move margins onto the following input.
- Include the Pathways cohort view (`LoginSelection.tsx:160-256`). It renders at `/student-login`, which the finding names, and has the same defect. Leaving it would make the finding's "every" untrue after the fix.
- Exclude unrouted `StudentLogin.tsx` and `Login.tsx` (`App.tsx:23,139-151`): they are not live routes.
- Exclude linking error messages (`aria-describedby`, `role="alert"`) and `autoComplete` tokens (WCAG 1.3.5). Both are real improvements, but the finding does not name them and they would widen the change.
- Put the regression tests in the existing `src/pages/__tests__/StudentClassLogin.test.tsx`. Rejected: new `TeacherLogin.test.tsx` / `LoginSelection.test.tsx` files. The work-package schema only accepts touched paths that exist now, and undeclared files would not match the diff check. That test file already mocks `react-i18next`, `@/contexts/AuthContext` and `LanguageToggle` (lines 7-21), which are the same modules `TeacherLogin` and `LoginCard` import.
- Tests assert on hardcoded English label text, not on `t()` output for `auth.*` keys. `enTranslate` ignores `defaultValue` (`src/test/i18nMock.ts:35-37`), so those keys resolve to raw key strings under test.

## Open questions

- Do maintainers want the `/student-login` main-view labels visible rather than visually hidden? That is a design change this plan deliberately avoids.
- Should the `/student-login` label and placeholder text be translated? Both are hardcoded English today. If so, it should be its own work item covering the whole page.
- The icon-only back buttons on `/class-login` (`StudentClassLogin.tsx:260-265` and `306-314`) have no accessible name. They are controls, not fields, so they are outside this finding. Should they be promoted to a separate work item?
- Are regression tests for `TeacherLogin` and `LoginSelection` acceptable inside `StudentClassLogin.test.tsx`, or should they live in new per-page files? New files would need a `/harness revise`, because the schema rejects undeclared new paths.

## Touched paths

- src/pages/TeacherLogin.tsx
- src/pages/LoginSelection.tsx
- src/pages/StudentClassLogin.tsx
- src/pages/__tests__/StudentClassLogin.test.tsx

## Risks

- **`InputOTP` prop forwarding is not verified here.** `node_modules` is not installed in this clone, so I could not read the source of `input-otp@^1.4.2` (`package.json:99`). The plan relies on the library's documented behaviour of spreading extra props onto its `<input>`. The PIN-step test (Behavior 8) checks this directly: it asserts that `getByLabelText` returns an `INPUT`.
- **`InputOTP` may not render under jsdom without help.** No existing test renders `InputOTP`, and `src/test/setup.ts` has no `ResizeObserver` stub. If it needs one, the stub goes locally in the new `describe` block, never in shared setup. If the PIN test still cannot run honestly, the delivery reports Behavior 8 as not covered by tests; it will not be skipped.
- **Selector regressions.** Check that no `placeholder` or `type` changed. Those attributes are what Cypress and the existing unit test select on (see Diagnosis). Also check that no new second password input shares the `/student-login` main view with the existing one, because `pathways-consent.cy.ts:236` expects exactly one.
- **Visual regression on `/student-login`.** Check that each new hidden label is wrapped with its input in a `<div>`, as described in Approach, so no input's top margin changes.
- **Accessible names now come from the label, not the placeholder.** The email field on `/teacher-login` will be announced as "Email" instead of "teacher@school.com". That is the point of the change, but any tool or test that matched the old placeholder-derived name would see a different string. I found none in `src/` or `cypress/`.
- **Gates were not run.** I had no way to execute commands in this session. `gate_expectation: green` is a judgement from reading the code: the changes add attributes, wrapper `<div>`s and label elements, and should add no lint warnings or type errors. I have not measured the baseline.
- **Issue body.** It contains no instructions aimed at an automated agent. The command table and links in it are the harness's own boilerplate for human maintainers. I read them as data and did not act on them.
