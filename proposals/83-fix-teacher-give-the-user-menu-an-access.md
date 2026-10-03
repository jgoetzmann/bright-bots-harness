---
issue: 83
upstream_issue: null
title: "fix(teacher): give the user menu an accessible name and a keyboard focus ring distinct from hover"
kind: fix
slices: 3
risk: low
touched_paths:
  - "src/components/TeacherDashboard/TeacherNavbar.tsx"
depends_on: []
estimated_turns: 15
gate_expectation: green
baseline_red: []
---

# fix(teacher): give the user menu an accessible name and a keyboard focus ring distinct from hover

## Issue

Harness issue [#83](https://github.com/jgoetzmann/bright-bots-harness/issues/83) tracks finding 22 of audit [#60](https://github.com/jgoetzmann/bright-bots-harness/issues/60) (`audit:60:22`), severity medium. There is no product-repository issue. In the reporter's words: "The teacher user menu has no accessible name and no focus indicator distinct from hover." The finding points at `role="menu"`, `aria-labelledby="user-menu-button"`, `id`, `role="menuitem"`, `focus:outline-none`, `focus:bg-gray-50` and `hover:bg-gray-50`.

## Diagnosis

All three defects are in `src/components/TeacherDashboard/TeacherNavbar.tsx`. That component is rendered once per page, from `src/components/TeacherDashboard/TeacherLayout.tsx:118`.

**1. The menu's `aria-labelledby` points at an id that does not exist.**
- The dropdown at `TeacherNavbar.tsx:78-82` has `role="menu"` and `aria-labelledby="user-menu-button"`.
- The trigger `<button>` at `TeacherNavbar.tsx:50-56` has no `id` attribute.
- A search of the repository for `user-menu-button` finds only line 81, so the IDREF resolves to nothing.
- Under the accessible-name algorithm, an `aria-labelledby` whose targets are all missing contributes nothing. The `menu` role does not take its name from its content, so the menu has no accessible name.
- The trigger button itself is named, by `aria-label` at line 55 (`User menu for ${userName}`). The reporter's "no accessible name" is accurate for the menu container, not for the button.

**2. Keyboard focus on the menu items looks exactly like hover, and both are nearly invisible.**
- Lines 88 and 99 use `hover:bg-gray-50 … focus:outline-none focus:bg-gray-50`.
- Line 111 (Logout) uses `hover:bg-red-50 … focus:outline-none focus:bg-red-50`.
- `focus:outline-none` overrides the global `button:focus, button:focus-visible { outline: 4px auto -webkit-focus-ring-color; }` at `src/index.css:148-151`. The utility selector has specificity 0,2,0 and `button:focus` has 0,1,1, so the utility wins.
- That leaves the hover background as the only focus cue: gray-50 `#F9FAFB` on white is about 1.05:1, and red-50 `#FEF2F2` on white is about 1.09:1. WCAG 1.4.11 requires 3:1 for visual state indicators.
- Hover actually gets more than focus. `src/index.css:130` gives every `button` a `1px solid transparent` border, and `src/index.css:144-146` changes it to `#646cff` on `:hover`. No rule changes it on focus.

**3. The trigger's focus ring uses a colour token that does not exist.**
- Line 52 uses `focus:ring-2 focus:ring-brightboost-light`.
- `tailwind.config.ts:16-22` defines `brightboost` as `navy`, `blue`, `lightblue`, `yellow` and `green`. There is no `light`. `package-lock.json:16828` pins Tailwind 3.4.19, which generates no rule for an unknown colour, so the ring falls back to Tailwind 3's default ring colour.
- That default is documented as blue-500 at 50% opacity. `node_modules` is not installed in this clone, so I did not read it from source.
- Blended over the navy navbar (`#1C3D6C`, line 34), that ring comes out at roughly 1.8:1. It is distinct from the trigger's hover (`hover:bg-brightboost-blue/20`), but it is faint.

**Related, out of scope.** Line 64 uses the same missing token for the initials avatar (`bg-brightboost-light`). That is a separate visibility defect, not a focus or name defect, and it is listed under Open questions. The menu also advertises the WAI-ARIA menu pattern (`role="menu"` / `role="menuitem"`) but has no arrow-key, Escape or outside-click handling. The outside-click handler at `TeacherLayout.tsx:52-67` does nothing. Tab still reaches the items because they are plain buttons that follow the trigger in DOM order, and that is where the new focus ring will be seen.

## Approach

Only `TeacherNavbar.tsx` changes: one new attribute and some class strings.

1. Add `id="user-menu-button"` to the trigger button. The existing `aria-labelledby` then resolves, and the menu is named from the button's existing `aria-label`, "User menu for <name>". The `aria-label` text stays as it is.
2. On each of the three menu items, keep `focus:outline-none` and the existing `hover:`/`focus:` backgrounds, and add `focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brightboost-navy`.
   - The ring appears only for keyboard-style focus, so it is distinct from hover.
   - Navy `#1C3D6C` measures about 10:1 against white, gray-50 and red-50.
   - Keeping `focus:outline-none` stops the global `button:focus` outline from also painting on pointer focus. Tailwind 3's `outline-none` leaves a transparent 2px outline, which still shows in forced-colors mode.
3. On the trigger, replace the undefined `focus:ring-brightboost-light` with `focus:ring-brightboost-lightblue` (`#8BD2ED`, `tailwind.config.ts:19`). That is about 6.5:1 against the navy navbar.

The file is not Prettier-clean today. Several lines exceed the default 80-column width and there is no `.prettierrc`. `.husky/pre-commit` runs `prettier --check` on staged files, so the touched file has to be formatted. Only this one file will be formatted; no whole-tree formatter will be run.

## Slices

1. Name the menu: add `id="user-menu-button"` to the trigger `<button>` at `TeacherNavbar.tsx:50-56` so the menu's `aria-labelledby` at line 81 resolves.
2. Give the three `role="menuitem"` buttons (lines 88, 99, 111) a keyboard-only inset navy focus ring (`focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brightboost-navy`), leaving their hover and existing focus classes unchanged.
3. Replace the undefined `focus:ring-brightboost-light` on the trigger (line 52) with `focus:ring-brightboost-lightblue`.

## Behaviors

1. With the menu open, the element with `role="menu"` has the accessible name "User menu for <userName>".
2. Exactly one element in the rendered document has `id="user-menu-button"`, and it is the trigger button.
3. Moving focus to a menu item with Tab draws a 2px navy ring inside the item's edges.
4. Hovering a menu item with the pointer, without keyboard focus, does not draw that ring.
5. The menu-item focus ring has at least 3:1 contrast against the item's background: white, gray-50 or red-50.
6. Moving focus to the trigger with Tab draws a light-blue (`#8BD2ED`) ring with at least 3:1 contrast against the navy navbar.
7. Activating View Profile, Edit Profile or Logout still calls its handler and closes the menu, as before.
8. The trigger's accessible name is still "User menu for <userName>".

## Acceptance criteria

- The trigger `<button>` in `src/components/TeacherDashboard/TeacherNavbar.tsx` has `id="user-menu-button"`, the same value as `aria-labelledby` on the `role="menu"` element.
- A search for `user-menu-button` under `src/` returns exactly two occurrences, both in `TeacherNavbar.tsx`.
- Each of the three `role="menuitem"` buttons has `focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brightboost-navy` in its `className`.
- Their `hover:bg-gray-50` / `hover:bg-red-50`, `focus:outline-none` and `focus:bg-*` classes are unchanged.
- The trigger's `className` contains `focus:ring-brightboost-lightblue` and no longer contains `focus:ring-brightboost-light`.
- The `bg-brightboost-light` on the avatar fallback (line 64) is unchanged.
- The trigger's `aria-label` expression is unchanged.
- The diff touches no file other than `src/components/TeacherDashboard/TeacherNavbar.tsx`.
- Every hunk outside the lines above is Prettier output only, with no change in meaning.
- `npx prettier --check -- src/components/TeacherDashboard/TeacherNavbar.tsx` passes.
- `npm run lint`, `npm run typecheck`, `npm run test:unit` and `npm run build` pass.
- In a browser on `/teacher/dashboard`, the accessibility tree shows the open menu named "User menu for <name>". Tabbing from the trigger into the items shows a visible navy ring that pointer hover does not produce.

## Decisions

- **Static `id="user-menu-button"` rather than `React.useId()`.** Line 81 already names that literal, and `TeacherNavbar` is rendered once (`TeacherLayout.tsx:118`). The repo has no other element with that id. `useId` only matters for multiple instances, which do not exist.
- **Fix the missing `id` rather than replace `aria-labelledby` with an `aria-label` on the menu.** The alternative would duplicate the untranslated string from line 55 and leave two names that could drift apart.
- **Keep `focus:outline-none` rather than switch to `focus-visible:outline-none`.** With the switch, the unlayered global rule at `src/index.css:148-151` would paint the browser outline on pointer focus, giving two different focus looks.
- **Use a `focus-visible:` ring rather than a `focus:` ring.** It matches the repo's convention (`src/components/ui/tabs.tsx:30`, `src/components/ui/toggle.tsx:8`, `src/pages/StudentDashboard.tsx:436`), and keeping the ring off pointer interaction is what makes it distinct from hover.
- **Use an inset ring rather than an outer ring.** The items are full-width rows with no gap between them, so an outer ring would overlap neighbouring items and the menu border. There is precedent at `src/components/games/RhymeRideGame.tsx:243`.
- **Use navy for all three items, including Logout, rather than `ring-red-600` for Logout.** One focus appearance across the menu, and navy clears 3:1 against red-50 at about 10:1.
- **Add the ring and keep the existing `focus:bg-*` backgrounds rather than remove them.** They do no harm once the ring exists, and keeping them makes the diff smaller.
- **Use `brightboost-lightblue` for the trigger ring rather than add a `light` token to `tailwind.config.ts`.** A new token would also change rendering at `ProfileModal.tsx:117`, `EditProfileModal.tsx:160`, `ExportGradesButton.tsx:127` and `TeacherNavbar.tsx:64`. That is a design choice for a person and outside this finding.
- **Leave the avatar fallback's `bg-brightboost-light` (line 64) alone.** It is a different defect, and three other files share it, so it should be fixed consistently in one separate change.
- **Do not implement the full WAI-ARIA menu-button keyboard pattern** (arrow keys, Home/End, Escape, focus on open, outside-click close). The finding covers the accessible name and the focus indicator; the keyboard pattern is a larger behavioural change.
- **Keep the trigger's English-only `aria-label` as it is.** `cypress/e2e/auth-login.cy.ts:18` and `cypress/e2e/session.cy.ts:10,24,32` select on the `User menu for` prefix.
- **No new unit test file.** The proposal schema accepts only paths that already exist, and the delivery diff is checked against that list, so `src/components/TeacherDashboard/__tests__/TeacherNavbar.test.tsx` cannot be declared.
  - Rejected: putting TeacherNavbar tests in `__tests__/StudentRoster.test.tsx`, which is an unrelated suite.
  - Rejected: adding assertions to `cypress/e2e/session.cy.ts`. Cypress is not a harness gate, so the assertion would ship unverified.
- **Do not update `docs/frontend/accessibility-improvements.md:120-137`.** It already describes a different `aria-label` ("User menu"), and correcting it is outside this finding.

## Open questions

- Should the delivery add a jsdom unit test, e.g. `getByRole("menu", { name: /User menu for/ })` after clicking the trigger? It would need a new file, `src/components/TeacherDashboard/__tests__/TeacherNavbar.test.tsx`, which this proposal cannot list. A `/harness revise` asking for it would settle this.
- Is `brightboost-lightblue` the colour the author meant by `brightboost-light`? Or should a `light` token be added to `tailwind.config.ts`, which would also restore the backgrounds at the four other sites that use it?
- Should the invisible initials avatar at `TeacherNavbar.tsx:64` become its own work item? Its background class is never generated, so navy initials sit on the navy navbar.
- Should this menu either implement the full WAI-ARIA menu-button pattern or drop `role="menu"`/`role="menuitem"` in favour of a plain disclosure list of buttons? Today it advertises a pattern it does not implement.
- Should the trigger's `aria-label` (`User menu for ${userName}`, line 55) be translated like the rest of the navbar? That would require updating the Cypress selectors that match its English prefix.

## Touched paths

- src/components/TeacherDashboard/TeacherNavbar.tsx

## Risks

- **Prettier hunks.** Formatting the file, which `.husky/pre-commit` requires, may rewrap lines outside the edited ones, likely 18, 43 and 70, which exceed 80 columns. I did not run Prettier in this step, so the exact hunks are not known. The reviewer should check that every such hunk is whitespace or line-wrapping only.
- **No gates were run.** I ran no gates on the untouched tree and have no shell in this step. `green` is an inference from the change being one JSX attribute and class-string edits, not observed output.
- **jsdom cannot check focus visuals.** It computes no Tailwind CSS, so behaviors 3 to 6 need a manual Tab-through in a browser. Look hardest at whether the ring shows on keyboard focus and not on hover.
- **Tailwind class generation.** Tailwind only emits classes that appear literally in source. All new classes will be written in full and use colours defined at `tailwind.config.ts:17` and `:19`, so they should be generated. `npm run build` plus a browser check confirms it.
- **The trigger's ring colour changes visibly**, from faint blue to light blue. That is a visual change a designer may want to weigh in on.
- **Issue body.** It is harness-generated tracking text and contains no instructions aimed at an agent. The sentence "A finding names a problem, not a plan" describes the process and was treated as data.
