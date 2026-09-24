---
issue: 108
upstream_issue: null
title: "test(bottom-nav): assert the router location after a nav button click"
kind: test
slices: 1
risk: low
touched_paths:
  - "src/components/__tests__/BottomNav.test.tsx"
depends_on: []
estimated_turns: 15
gate_expectation: green
baseline_red: []
---

# test(bottom-nav): assert the router location after a nav button click

## Issue

Harness issue #108, which tracks finding 14 of audit #61 (`audit:61:14`,
https://github.com/jgoetzmann/bright-bots-harness/issues/61). The product repository has no issue for this.

The reporter's words: "navigates to the correct path when clicked" clicks the button and ends with a comment saying "we trust the buttons are rendered", so navigation could be removed entirely and the test still passes. Severity medium. Path given: `src/components/__tests__/BottomNav.test.tsx:39`.

## Diagnosis

The reporter is right, and the defect is where they said it is.

- `src/components/__tests__/BottomNav.test.tsx:39-46`: the test renders, looks up the Learn button (line 42), clicks it (line 43), and stops. It has no `expect(...)` call. The only thing that could fail is `getByText("Learn")` at line 42, and the first test already covers that (line 34). Lines 44-45 are comments explaining why nothing is checked: "MemoryRouter without a location display … here we trust the buttons are rendered".
- The code this test should protect is `src/components/BottomNav.tsx:46`, `onClick={() => navigate(item.path)}`, with `navigate` from `useNavigate()` at line 7. The Learn item's target is `/student/modules` (line 15). If line 46 were deleted or became a no-op, all three tests in the file would still pass:
  - test 1 (lines 31-37) only checks labels;
  - test 3 (lines 48-58) sets the location through `initialEntries` (line 50), not by clicking, so it tests `aria-current` (line 53) and never tests navigation.
- `renderWithRouter` (lines 22-28) wraps `BottomNav` in a `MemoryRouter` and renders nothing that shows the current location. That is the gap the comment on line 44 describes. The test has no way to see where the router went.
- The repo already has two ways to observe navigation in tests:
  - `src/pages/__tests__/BiomeBuddyPages.test.tsx:65-70` defines a `LocationProbe` that renders `useLocation()` into `data-testid="location"`. It sits inside the `MemoryRouter` at line 78.
  - `src/pages/__tests__/StudentDashboardContinue.test.tsx:26-29` and `src/pages/__tests__/ModulesGuidedChoice.test.tsx:24-27` replace `useNavigate` with a mock via `vi.mock("react-router-dom", …)`.
- Versions from `package-lock.json`: `react-router-dom` 6.30.2 (lines 15499-15500), React `^18.3.1` (`package.json:105`), `@testing-library/react` `^16.3.0` (`package.json:134`). `fireEvent` is wrapped in `act`, and `MemoryRouter` here gets no `future` flags (`BottomNav.test.tsx:24`), so the location update should be flushed by the time `fireEvent.click` returns. An assertion straight after the click should see the new path.
- The file is in the `unit` vitest project (`vitest.config.ts:15`; `test:unit` is `vitest run --project unit`, `package.json:25`). BottomNav is not in the coverage `include` list (`vitest.config.ts:35-40`), so no coverage threshold depends on this file.

## Approach

Make the test observe the real router location, and assert that clicking Learn moves it from `/` to `/student/modules`.

1. Add a small `LocationProbe` component to the test file, modelled on `BiomeBuddyPages.test.tsx:65-70`. It renders `useLocation().pathname` into `<span data-testid="location">`. Import `useLocation` next to `MemoryRouter` on line 3.
2. In `renderWithRouter` (lines 22-28), render `<LocationProbe />` next to `<BottomNav />` inside the `MemoryRouter`.
3. In the navigation test (lines 39-46):
   - Keep the render and the click as they are.
   - Before the click, assert `screen.getByTestId("location")` has text `/`. This precondition shows the test starts somewhere other than the target, so it cannot pass on the initial entry alone.
   - After the click, assert the probe has text `/student/modules`.
   - Delete the two comments on lines 44-45; they describe the gap this change closes and would be false afterwards.

This checks what a user actually gets (the app's route changes), not how `BottomNav` produces it. If `BottomNav` later used `<Link>` or `<NavLink>` instead of `useNavigate`, the test would still be valid.

No production code changes. `BottomNav.tsx` behaves correctly; only the test is missing an assertion.

## Slices

1. In `src/components/__tests__/BottomNav.test.tsx`: add a `LocationProbe` that renders `useLocation().pathname`, render it inside `renderWithRouter`'s `MemoryRouter`, and replace the two trailing comments in "navigates to the correct path when clicked" with assertions that the location is `/` before the click and `/student/modules` after it.

## Behaviors

1. With `BottomNav` rendered at `/`, clicking the Learn button changes the router location to `/student/modules`, and the test asserts it.
2. If clicking a `BottomNav` button no longer changes the router location, "navigates to the correct path when clicked" fails.
3. If the Learn button's target path changes away from `/student/modules`, "navigates to the correct path when clicked" fails.
4. "renders all navigation items" and "indicates the active page with aria-current" pass unchanged, with the same assertions.

## Acceptance criteria

- `src/components/__tests__/BottomNav.test.tsx` is the only file in the diff.
- The test "navigates to the correct path when clicked" contains at least one `expect(...)` on the router location taken after `fireEvent.click`, and one on the location before the click.
- The comments at the old lines 44-45 ("Since we are using MemoryRouter without a location display…", "…here we trust the buttons are rendered.") are gone.
- `npm run test:unit` passes, and all three tests in `BottomNav.test.tsx` run and pass: no `.skip`, no `.only`, no changed timeout.
- Local mutation check, not committed: changing `src/components/BottomNav.tsx:46` to `onClick={() => {}}` makes "navigates to the correct path when clicked" fail.
- The other two tests in the file keep their assertions exactly as they are.
- `npm run lint` and `npm run typecheck` pass. Formatting is applied only to `src/components/__tests__/BottomNav.test.tsx`.

## Decisions

- Observe the real `MemoryRouter` location with a `LocationProbe`, and do not mock `useNavigate` (the pattern in `StudentDashboardContinue.test.tsx:26-29`). A mock only checks that one hook was called with one string. It would break on a harmless switch to `<Link>`, and would not prove the router location changed. The probe checks the outcome and matches the real-router setup test 3 already uses.
- Define `LocationProbe` inside `BottomNav.test.tsx`, and do not move it into `src/test/` as a shared helper. It is five lines, it has one existing copy (`BiomeBuddyPages.test.tsx:66-70`), and extracting it would mean editing a second test file this finding does not cover.
- Render the probe in `renderWithRouter` for every test, and do not add a separate render helper just for the navigation test. The probe only adds a span showing a path. It does not match any `getByText` query in tests 1 or 3 ("Learn", "My Star", "Play"), and one helper keeps the file simpler.
- Assert only the Learn button, and do not use `it.each` over all four items. Every item shares the same click handler (`BottomNav.tsx:46`), so one item proves the click-to-navigate wiring the finding says is untested. Testing the other three paths would mostly repeat the literal table at `BottomNav.tsx:15,21,27,33`. If a reviewer wants per-item coverage, `/harness revise` can ask for it.
- Keep the file's `getByText(...).closest("button")` lookup (lines 42, 52-53) and `fireEvent`, and do not switch to `getByRole` or `userEvent`. This follows the surrounding code and keeps the diff to the missing assertion.
- Add the before-click assertion (`/`), even though the initial entry is already `/`. It makes the test show a change in location, not just a final state, so a later edit to `initialEntry` cannot make it pass trivially.
- Leave two nearby problems alone because this finding does not cover them. "renders all navigation items" (lines 31-37) does not check the "Classes" item (`BottomNav.tsx:19-23`). The comment "This expectation is expected to FAIL before the fix" (line 55) is out of date.

## Open questions

- Should the incomplete "renders all navigation items" test (no "Classes" assertion) and the stale comment on line 55 get their own work item? This package leaves both untouched.

## Touched paths

- src/components/__tests__/BottomNav.test.tsx

## Risks

- I did not run any gate while writing this proposal: I had no shell, only file-reading tools. `gate_expectation: "green"` means I read nothing that suggests a gate is already red on the untouched tree. It is not an observed result. The implementation run has to establish the baseline itself.
- The post-click assertion is synchronous. That depends on `fireEvent`'s `act` wrapper flushing the router update, which should hold for react-router 6.30.2 with no `v7_startTransition` flag. If the assertion does not see the new path straight after the click, the implementer should find out why before switching to `findByTestId` / `waitFor`. Using those is a legitimate async wait, but only with the default timeout: raising the timeout is not allowed.
- Reviewers should check that the new test really fails when navigation is removed (the mutation check above). A new assertion that passes either way would bring this finding straight back.
- Rendering the probe in all three tests adds one DOM node. Reviewers should confirm no `getByText` in tests 1 or 3 now finds more than one element.
- The issue body contains no instructions aimed at an AI agent. The harness command table and links in it are the harness's own boilerplate; I treated them as data and acted on none of them.
- This package does not touch `.github/`, `prisma/`, migrations, predeploy scripts, `.env*`, or dependencies.
