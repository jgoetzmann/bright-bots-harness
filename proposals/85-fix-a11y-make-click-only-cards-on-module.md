---
issue: 85
upstream_issue: null
title: "fix(a11y): make click-only cards on modules, pd hub and experiments keyboard-operable"
kind: fix
slices: 3
risk: low
touched_paths:
  - "src/components/admin/ExperimentDashboard.tsx"
  - "src/pages/TeacherPDHub.tsx"
  - "src/pages/Modules.tsx"
  - "src/pages/__tests__/Modules.test.tsx"
depends_on: []
estimated_turns: 30
gate_expectation: green
baseline_red: []
---

# fix(a11y): make click-only cards on modules, pd hub and experiments keyboard-operable

## Issue

Harness issue #85 tracks finding 24 of audit jgoetzmann/bright-bots-harness#60 (`audit:60:24`). There is no product-repository issue. The reporter's terms: "Further click-only cards across student, teacher and admin surfaces", severity medium, paths `onClick`, `Card`, `<div>`, `Card`. The finding names the problem, not a plan.

## Diagnosis

`Card` (`src/components/ui/card.tsx:7-19`) is a plain `<div>` that passes every prop straight through. An `onClick` on it therefore produces an element that a mouse can use and a keyboard cannot: no tab stop, no Enter/Space handling, no role. Searching `src/**/*.tsx` for `<Card …onClick` and `<div …onClick` finds one such element on each of the three surfaces the finding names:

- **Admin, `/admin/experiments`** (`src/App.tsx:330-337`). `src/components/admin/ExperimentDashboard.tsx:521-525`: each experiment in the list is a `<Card className="cursor-pointer …" onClick={() => setSelectedId(exp.id)}>` with no `role`, `tabIndex` or `onKeyDown`. The card holds only non-interactive content (h3, Badge, code, p; lines 526-543). The only way to reach an experiment's detail view (lines 471-480), and so the results and the complete action, is to click a card. A keyboard-only staff user cannot open any experiment.
- **Teacher, PD hub** (`src/App.tsx:272`, route `pd`). `src/pages/TeacherPDHub.tsx:516-523`: each session's header is a `<div className="… cursor-pointer" onClick={() => setExpandedSession(…)}>`, again with no role, tab stop, key handler or `aria-expanded`. The session's notes, action items and related modules only render when it is expanded (lines 541 onward). A keyboard user cannot open them, and a screen-reader user is not told the header is a disclosure.
- **Student, `/student/modules`** (`src/App.tsx:289`). `src/pages/Modules.tsx:699-706`: a locked module card is `<Card className="… cursor-pointer" onClick={locked ? onLockedClick : undefined}>`, and the handlers (lines 375-396) show a "Set N is Locked" toast. The card's only control is the "Locked" `Button`, which is made inert on purpose with `tabIndex={-1}` and `pointer-events-none` (lines 742-749). Mouse clicks on that button fall through to the card. Keyboard focus never reaches the card or the button, so keyboard users cannot trigger the explanation. The loss is smaller here: the same `set2LockedMessage` / `set3LockedMessage` text is already printed under the section heading (lines 511-518, 558-565). Still, all five Set 2 locked cards (`src/constants/stemSets.ts:204-210`) show the screen-reader user a button named "Locked" that they cannot tab to, and the pointer-only behaviour conflicts with `docs/safe-exploration-accessibility.md:32` ("Every essential action is operable with keyboard alone").

"Further" fits the code. The two student-dashboard elements of the same kind are already fixed with `role="button"`, `tabIndex={0}` and an Enter/Space `onKeyDown`: the My Star tile at `src/pages/StudentDashboard.tsx:435-446` and the Join a Class card at `src/pages/StudentDashboard.tsx:642-653`. This package uses that pattern.

Nothing lint-based catches this. `eslint.config.js:1-44` loads no `jsx-a11y` plugin, and `package.json` has no axe matcher.

Other `onClick`-on-non-control sites the search found, all left out of this package (reasons under `## Decisions`):
- `src/components/pathways/TracksOverview.tsx:40-48` and `src/components/pathways/TrackDetail.tsx:121-156` (pathways surface)
- `src/components/ui/BadgeSlot.tsx:50-53` (imported nowhere)
- `src/components/TeacherDashboard/Assignments/AssignmentsTable.tsx:40-48` (a `<tr>`; `AssignmentsPage` is not routed in `src/App.tsx`)
- `src/components/games/SkyShieldGame.tsx:259-263` (core game play)
- `src/components/unity/UnityWebGL.tsx:273-296` (game container)
- backdrop dismiss layers: `NewAssignmentDrawer.tsx:79-81`, `Sidebar.tsx:106-108`, `facilitator/pages/Tracks.tsx:133-135`, `CelebrationContext.tsx:108-109`, `MobileToolbox.tsx:54-56`

## Approach

Make each of the three elements keyboard-operable with the smallest change that follows the pattern the repository already uses.

1. **ExperimentDashboard card** (`ExperimentDashboard.tsx:521-525`):
   - Add `role="button"`, `tabIndex={0}`, and an `onKeyDown` that calls `setSelectedId(exp.id)` on `Enter` or `" "` after `e.preventDefault()`. This is the shape of `StudentDashboard.tsx:647-652`.
   - Add `focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2`. These are the classes at `StudentDashboard.tsx:436`, and `docs/agents/learned/accessibility.md:35-39` requires them because `role="button"` divs get no default focus ring.
2. **TeacherPDHub session header** (`TeacherPDHub.tsx:516-523`):
   - Add the same `role`, `tabIndex`, Enter/Space `onKeyDown` and focus-ring classes.
   - The `onKeyDown` repeats the existing toggle expression.
   - Also add `aria-expanded={expandedSession === session.id}` so assistive technology hears the disclosure state.
3. **Modules locked card** (`Modules.tsx:741-749`):
   - Do not turn the card into a `role="button"`: it contains a real `<button>`, and that would nest interactive elements. Make the existing "Locked" `Button` the keyboard control instead.
   - Remove `tabIndex={-1}` and `pointer-events-none`, and keep `opacity-60`.
   - Give it `onClick={(e) => { e.stopPropagation(); onLockedClick?.(); }}`, which mirrors the unlocked branch's `stopPropagation` at lines 752-755 so the card's own `onClick` does not show the toast a second time.
   - Give it an `aria-label` built from the existing `modules.locked` key plus `translateContentName(m.title)`, the same way line 757 builds the Start Learning label, so the five buttons have different names.
   - The card keeps its `onClick`, so clicking anywhere on a locked card behaves as it does today.
4. **Test** (`src/pages/__tests__/Modules.test.tsx`):
   - Add one case that renders a locked Set 2 module, finds the button named "Locked <title>", checks it is not `tabindex="-1"`, clicks it, and asserts one toast with the Set 2 title.
   - This needs a `vi.mock("@/hooks/use-toast")`, and the file's `Button` mock (lines 39-45) must pass `tabIndex` through as well.

No new i18n keys, no new dependency, no shared helper, no change to `card.tsx`.

## Slices

1. Admin: make each experiment card in `src/components/admin/ExperimentDashboard.tsx` focusable and openable with Enter/Space, with a visible focus ring.
2. Teacher: make each PD session header in `src/pages/TeacherPDHub.tsx` focusable, toggleable with Enter/Space, exposing `aria-expanded`, with a visible focus ring.
3. Student: make the "Locked" button on locked module cards in `src/pages/Modules.tsx` keyboard-reachable and able to show the set-locked toast once, with a per-module accessible name, and cover it with a new case in `src/pages/__tests__/Modules.test.tsx`.

## Behaviors

1. On `/admin/experiments`, pressing Tab moves focus onto each experiment card in turn, and the focused card shows a visible focus ring.
2. Pressing Enter on a focused experiment card opens that experiment's detail view, the same view a mouse click opens.
3. Pressing Space on a focused experiment card opens the detail view and does not scroll the page.
4. Clicking an experiment card with the mouse still opens its detail view.
5. On the teacher PD hub Sessions tab, pressing Tab moves focus onto each session header in turn, and the focused header shows a visible focus ring.
6. Pressing Enter or Space on a focused session header expands it if collapsed and collapses it if expanded.
7. A session header's `aria-expanded` is `true` while its details are shown and `false` otherwise.
8. Clicking a session header with the mouse still toggles it.
9. On `/student/modules` with Set 2 locked, each locked Set 2 card's "Locked" button is in the tab order.
10. Each locked card's "Locked" button has the accessible name "Locked <module title>" (English locale).
11. Pressing Enter or Space on a focused "Locked" button, or clicking it, shows the "Set 2 is Locked" toast (or "Set 3 is Locked" for Set 3) exactly once.
12. Clicking elsewhere on a locked module card still shows the same toast once.
13. Unlocked module cards and their "Start Learning" buttons behave exactly as before.

## Acceptance criteria

- `git diff --name-only` against the base lists exactly `src/components/admin/ExperimentDashboard.tsx`, `src/pages/TeacherPDHub.tsx`, `src/pages/Modules.tsx`, `src/pages/__tests__/Modules.test.tsx`.
- The experiment `Card` in `ExperimentDashboard.tsx`:
  - has `role="button"` and `tabIndex={0}`;
  - has an `onKeyDown` that on `"Enter"` or `" "` calls `e.preventDefault()` and `setSelectedId(exp.id)`;
  - has the classes `focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2`;
  - keeps its existing `onClick`.
- The session header `div` in `TeacherPDHub.tsx`:
  - has `role="button"`, `tabIndex={0}` and `aria-expanded={expandedSession === session.id}`;
  - has an Enter/Space `onKeyDown` with `preventDefault` that performs the same toggle as its `onClick`;
  - has the same four focus-visible classes;
  - keeps its existing `onClick`.
- The locked `Button` in `ModuleCard` (`Modules.tsx`):
  - no longer has `tabIndex={-1}` or the `pointer-events-none` class;
  - has an `onClick` that calls `e.stopPropagation()` and then `onLockedClick`;
  - has an `aria-label` built from `t("modules.locked", …)` and `translateContentName(m.title)`.
- The locked `Card`'s `onClick={locked ? onLockedClick : undefined}` is unchanged.
- `Modules.test.tsx` has a new case that:
  - renders a module with a Set 2 slug (for example `k2-stem-maze-maps`) and empty progress;
  - finds the button by accessible name "Locked <title>";
  - asserts it has no `tabindex="-1"`;
  - clicks it and asserts the mocked `toast` was called exactly once with title "Set 2 is Locked".
- The five existing cases in `Modules.test.tsx` still pass unmodified, apart from the `Button` mock now forwarding `tabIndex`.
- No file under `src/locales/` or `src/i18n/` changes; no new translation key is introduced.
- `npm run lint`, `npm run typecheck`, `npm run test:unit` and `npm run build` pass on the changed tree.
- No `.skip(`, `.only(`, eslint-disable comment, timeout change or other gate relaxation appears in the diff.
- A manual keyboard-only pass (Tab, Enter, Space, no mouse) on `/admin/experiments`, the teacher PD hub Sessions tab and `/student/modules` with Set 2 locked reproduces Behaviors 1-3, 5-7 and 9-11.

## Decisions

- Scope is the three sites on the three surfaces the finding names: admin `ExperimentDashboard`, teacher `TeacherPDHub`, student `Modules`. The alternative, fixing every `onClick` on a non-control in `src/`, was rejected because it would pull in pathways, game and dead-code sites the finding does not name. Those are listed under `## Open questions` instead.
- `StudentDashboard.tsx:435-446` and `:642-653` are not touched. They already carry `role="button"`, `tabIndex={0}` and Enter/Space handling, which is presumably the earlier fix that "Further" refers to.
- For ExperimentDashboard and TeacherPDHub I chose `role="button"` + `tabIndex` + `onKeyDown` on the existing element over converting it to a native `<button>`. A native button would have to contain the existing `<div>`/`<h3>` children, and a button's content model does not allow those. Restructuring the markup to allow it is a larger visual and DOM change than the defect needs, and the repo already uses the role-based pattern.
- The Enter/Space handler is inline at each site, not in a shared hook or wrapper component. The existing sites inline it too, and house style rules out an abstraction the defect does not need.
- `aria-expanded` is added to the PD session header, but `aria-controls` is not. The details panel is conditionally rendered (`TeacherPDHub.tsx:541`), so `aria-controls` would point at a missing id whenever the session is collapsed.
- For the Modules locked card, the inner "Locked" `Button` becomes the keyboard control rather than the card becoming `role="button"`. Making the card a button would nest the real `<button>` inside a `role="button"`, which assistive technology handles badly.
- The locked `Button` gets an explicit `onClick` with `stopPropagation` instead of relying on its click bubbling up to the card. It mirrors the unlocked branch (`Modules.tsx:752-755`), it is testable through the file's existing `Card` mock, which drops `onClick`, and it makes the single-toast guarantee visible in the code.
- The locked card keeps its `onClick`, so mouse users can still click anywhere on it. Removing it would shrink the pointer target to the button and change behaviour for mouse users.
- The locked `Button` does not get `aria-disabled`. It performs a real action (showing the explanation), and its "Locked <title>" name already conveys the state.
- No `aria-label` is added to the experiment cards; their accessible name comes from their content, as the Join a Class card's does. Adding `aria-label={exp.name}` would hide the status, slug and counts from screen-reader users.
- The Modules toast is not replaced with inline text. The explanation already appears inline at `Modules.tsx:511-518` and `:558-565`, and the defect is only that the toast cannot be reached by keyboard.
- Only `Modules.test.tsx` gets a new test. The harness requires every touched path to exist now, so no new test file can be created for ExperimentDashboard or TeacherPDHub, neither of which has one. Those two sites are verified by the acceptance criteria and manual keyboard check instead; see `## Open questions`.
- The `Button` mock in `Modules.test.tsx` is extended to forward `tabIndex` instead of dropping the mock. The other five cases rely on the mock, and forwarding one more prop changes nothing for them.

## Open questions

- Should the two pathways click-only cards be handled here or by a separate item? They are `src/components/pathways/TracksOverview.tsx:40-48` and `src/components/pathways/TrackDetail.tsx:121-156`. This package assumes a sibling audit finding covers the pathways surface; I could not read audit #60 to confirm that.
- Do reviewers want dedicated test files for ExperimentDashboard and TeacherPDHub? Neither has one, and this package cannot create new paths, so they would need a follow-up item.
- `ExperimentDashboard.tsx:471-480` swaps the list for the detail view without moving focus, so a keyboard user who opens an experiment ends up with focus on the document body. The same happens on "Back". This already happens with the mouse and is left alone here. Should it be its own item?
- `src/components/ui/BadgeSlot.tsx` (click-only flip at lines 50-53, imported nowhere) and `src/components/TeacherDashboard/Assignments/AssignmentsTable.tsx` (click-only `<tr>` at lines 40-48, its `AssignmentsPage` not routed in `src/App.tsx`) are unreachable today. Should they be fixed, deleted, or left?

## Touched paths

- src/components/admin/ExperimentDashboard.tsx
- src/pages/TeacherPDHub.tsx
- src/pages/Modules.tsx
- src/pages/__tests__/Modules.test.tsx

## Risks

- Toast count on the Modules card depends on the `stopPropagation` in the locked `Button`'s `onClick`. The test's `Card` mock drops `onClick` (`Modules.test.tsx:28-33`), so the unit test cannot catch a double toast caused by a missing `stopPropagation`. Reviewers should check that line by eye.
- A `role="button"` card that contains an `<h3>` (experiment cards) or `<h3>` + `<p>` (PD headers) flattens those children's semantics, so screen-reader heading navigation skips them. This is the accepted trade-off of the role-based pattern already used at `StudentDashboard.tsx:642-672`, and only the admin tool and the teacher PD list are affected.
- The PD session header adds one tab stop per session, and locked Set 2 cards add up to five (`stemSets.ts:204-210`). This is expected, but it makes those pages longer to tab through.
- Running prettier on `TeacherPDHub.tsx`, `ExperimentDashboard.tsx` or `Modules.tsx` could reformat lines this package does not otherwise change, if those files are not already prettier-clean. Implementation formats only these four files, and any unrelated reformatting should be reverted rather than shipped.
- I ran no gate, lint or test for this proposal; no shell was available. `gate_expectation: green` and the empty `baseline_red` are predictions, not observations. If the untouched tree turns out red, implementation will report it rather than work around it.
- The issue body contains no instructions aimed at the agent. The only imperative text is the harness's own `/harness …` command table, which is addressed to trusted maintainers. I did not act on it.
