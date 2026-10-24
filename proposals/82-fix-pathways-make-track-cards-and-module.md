---
issue: 82
upstream_issue: null
title: "fix(pathways): make track cards and module launches keyboard-operable links"
kind: fix
slices: 3
risk: low
touched_paths:
  - "src/components/pathways/TracksOverview.tsx"
  - "src/components/pathways/TrackDetail.tsx"
  - "src/components/__tests__"
depends_on: []
estimated_turns: 35
gate_expectation: green
baseline_red: []
---

# fix(pathways): make track cards and module launches keyboard-operable links

## Issue

Harness issue #82 tracks audit finding `audit:60:21`, which is finding 21 of audit jgoetzmann/bright-bots-harness#60. There is no product-repository issue. The finding is rated high severity and gives these paths: `<div onClick={isActive ? () => navigate(...)}>` and `window.open`. It is titled "Pathways track browsing and module launching are entirely click-only". In the reporter's words: a learner can only open a Pathways track or launch a module by clicking with a pointer.

## Diagnosis

The finding is correct, and the source confirms it in two components.

**Track browsing: `src/components/pathways/TracksOverview.tsx`**
- Lines 40–48: each track card is a plain `<div>` with `onClick={isActive ? () => navigate(`/pathways/tracks/${track.slug}`) : undefined}`.
- The card has no `tabIndex`, `role`, `href` or key handler. A `<div>` without `tabindex` is not in the tab order, so Tab skips it, Enter and Space do nothing, and screen readers see a generic group rather than something they can activate.
- The only hint that the card can be activated is `cursor-pointer` on line 44, which only mouse users see.

**Module launching: `src/components/pathways/TrackDetail.tsx`**
- Lines 121–156: each module row is a `<div>` whose `onClick` does three things:
  - returns early for coming-soon modules (line 131);
  - for an external module with a URL, calls `window.open(mod.externalUrl, "_blank")` (line 133) and then POSTs a `completed` milestone (lines 134–151);
  - otherwise calls `navigate(`/pathways/tracks/${track.slug}/${mod.slug}`)` (line 154).
- Again there is no `tabIndex`, `role` or key handler, and the `PlayCircle` at lines 201–203 is only an icon, not a control.

**Why this matters now**
- `cyber-launch` is the only active track (`src/constants/pathwayTracks.ts:29-36`), and all 7 of its modules are active (lines 38–44).
- So a keyboard or screen-reader learner cannot open the one track that exists from the tracks page, and cannot start any of its modules from the track page.
- The only keyboard route into a module is `NextTaskCard` on the Pathways home page (`src/components/pathways/NextTaskCard.tsx:199`), which is a `<Link>` but shows a single recommended module.
- The facilitator catalog links to the same learner route with a real `<Link>` (`src/components/pathways/facilitator/pages/Tracks.tsx:117-122`). The facilitator can reach it by keyboard; the learner cannot.

**A related defect on the same line, not named in the finding**
- `window.open(url, "_blank")` at `TrackDetail.tsx:133` passes no `noopener` feature. The opened third-party page therefore gets a `window.opener` handle back to the BrightBoost tab.
- An `<a target="_blank">` implies `noopener` in current browsers; `window.open` without the feature string does not.
- Other Pathways code already uses the safe form: `rel="noopener noreferrer"` in `src/components/pathways/labs/PasswordStrengthLab.tsx:300-311`.

**Why the gates never caught it**
- `eslint.config.js:22-25` loads only `react-hooks` and `react-refresh`. There is no `jsx-a11y` plugin, so no gate flags a clickable `div`.
- `src/components/pathways/` has no tests at all: no `*.test.tsx` under it, and no test imports from it.

**Other `window.open` call sites are out of this finding**
- `src/components/pathways/facilitator/pages/CohortDetail.tsx:1033` (CSV export), `src/components/pathways/facilitator/data/worksheets.ts:825` (print window) and `src/pages/TeacherResources.tsx:97` (print window) are handler functions that open export or print windows.
- None of them is the click-only card pattern this finding describes.

## Approach

Replace the clickable `<div>`s with native link elements, so the browser provides focus, Enter activation, the correct role, new-tab and context-menu behaviour. No hand-written key handling is needed.

1. **`TracksOverview.tsx`**
   - Active tracks render the card as a react-router `<Link to={`/pathways/tracks/${track.slug}`}>`, with the same classes plus the Pathways focus-ring classes already used in `JoinCohort.tsx:326` (`focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-indigo-500`).
   - Coming-soon tracks keep a plain, non-interactive `<div>`.
   - The card's inner markup is written once and wrapped by whichever element applies.
   - `useNavigate` and its import become unused and are removed.
2. **`TrackDetail.tsx`**, inside the module `map`:
   - The shared row body is built once, and the wrapper depends on the module:
     - coming soon: `<div>`, non-interactive as today;
     - external with `externalUrl`: `<a href={mod.externalUrl} target="_blank" rel="noopener noreferrer" onClick={...}>`;
     - everything else: `<Link to={`/pathways/tracks/${track.slug}/${mod.slug}`}>`.
   - The milestone POST and `setMilestones` update (lines 134–151) move unchanged into a named handler on the external anchor's `onClick`.
   - Pressing Enter on a focused anchor fires `click`, so keyboard launches record the milestone the same way mouse launches do.
   - `window.open` is removed from this file.
   - Current routing rules stay as they are: completed modules still link to their route (today only coming-soon returns early), and an external module without a URL still falls back to the internal route (the line 132 condition).
   - `navigate` stays, because the back buttons at lines 52 and 76 use it.
3. **Tests**: one new test file in `src/components/__tests__/` covering both components:
   - MemoryRouter with a sentinel route to detect navigation;
   - `vi.stubGlobal("fetch", ...)` for the milestone GET and POST;
   - `@testing-library/user-event` (already a devDependency, `package.json:135`) for Tab and Enter;
   - a local `react-i18next` mock that returns the default string. The shared `enMock` only reads `common.json` (`src/test/i18nMock.ts:20`), but Pathways strings live in `pathways.json` (`src/i18n.ts:5`).

Styling classes, i18n keys, API calls and routes stay exactly as they are.

## Slices

1. `TracksOverview.tsx`: render active track cards as `<Link>` with the Pathways focus ring, keep coming-soon cards as non-interactive `<div>`, and remove the now-unused `useNavigate`.
2. `TrackDetail.tsx`: render internal modules as `<Link>`, external modules as `<a target="_blank" rel="noopener noreferrer">` whose `onClick` runs the existing milestone POST, and coming-soon modules as non-interactive `<div>`. Remove `window.open`.
3. Add `src/components/__tests__/PathwaysTrackNavigation.test.tsx` with keyboard, role, href and milestone tests for both components.

## Behaviors

1. On `/pathways/tracks`, the active `cyber-launch` card is exposed as a link and is reached by pressing Tab.
2. Pressing Enter on the focused `cyber-launch` card navigates to `/pathways/tracks/cyber-launch`.
3. Clicking the `cyber-launch` card with the mouse still navigates to `/pathways/tracks/cyber-launch`.
4. The four coming-soon track cards are not links and are skipped by Tab.
5. On `/pathways/tracks/cyber-launch`, each of the six internal modules is a link whose `href` is `/pathways/tracks/cyber-launch/<module-slug>`.
6. Pressing Enter on a focused internal module navigates to that module's route.
7. The `cisco-netacad-link` module is a link whose `href` is its `externalUrl`, with `target="_blank"` and a `rel` containing both `noopener` and `noreferrer`.
8. Activating the external module by click or by Enter sends one POST to `/api/pathways/student/milestones` with `{ trackSlug, moduleSlug, status: "completed", score: 100 }`, and the row then shows the Completed label.
9. A completed internal module is still a link to its module route.
10. On a coming-soon track, for example `/pathways/tracks/money-moves`, no module row is a link and none is reached by Tab.
11. A keyboard-focused track card or module link shows a visible indigo focus ring.

## Acceptance criteria

- `TracksOverview.tsx` contains no `onClick` on a `div` and does not import `useNavigate`.
- `TrackDetail.tsx` contains no `window.open` and no `onClick` on the module-row `div`.
- The external module anchor has `target="_blank"` and `rel="noopener noreferrer"`.
- Active track cards and internal module rows are react-router `<Link>` elements; coming-soon cards and rows are `<div>` elements with no `tabIndex`, `role` or handler.
- The existing visual classes for each state (active, inactive, completed, coming soon) are unchanged. The only class additions are `focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-indigo-500` on the interactive wrappers.
- The milestone POST body, headers and the follow-up `setMilestones` update are the same as `TrackDetail.tsx:134-151` today.
- `src/components/__tests__/PathwaysTrackNavigation.test.tsx` exists, covers behaviors 1–10, and passes under `npm run test:unit`.
- No test in the new file uses `.skip(` or `.only(`, and no timeout is raised.
- `npm run lint`, `npm run typecheck`, `npm run test:unit` and `npm run build` pass.
- The diff touches only `src/components/pathways/TracksOverview.tsx`, `src/components/pathways/TrackDetail.tsx` and the one new file under `src/components/__tests__/`.

## Decisions

- Use native `<Link>` and `<a>` elements rather than adding `role="button" tabIndex={0} onKeyDown` to the existing divs (the pattern in `src/pages/StudentDashboard.tsx:435-445`). These cards navigate, so link semantics are correct; the browser then handles focus, Enter, middle-click, new tab and the context menu, with no key handler to get wrong.
- Space will not activate the cards, because native links do not respond to Space. I am not adding a Space handler so they behave like every other link on the site.
- Wrap the whole card in the link rather than using a "stretched link" inside the heading. The whole-card wrapper is the pattern already used for `NextTaskCard.tsx:199`. The cost is a long accessible name (all the card's text), which is recorded under Risks.
- Replace `window.open(url, "_blank")` with `<a target="_blank" rel="noopener noreferrer">` rather than keeping `window.open` behind a key handler. An anchor is keyboard-operable with no extra code, and it removes the `window.opener` exposure, matching `PasswordStrengthLab.tsx:300-311`.
- Record the external milestone in the anchor's `onClick` only, not also in `onAuxClick`. `auxclick` also fires for non-primary buttons, including right-click, which would mark a module complete just because someone opened the context menu. As a result middle-click opens the link without recording anything; today middle-click does nothing at all.
- Keep the existing routing exactly: completed modules stay links, and an external module without `externalUrl` falls back to the internal route. Today's handler at `TrackDetail.tsx:130-155` behaves this way, and changing it is outside this finding.
- Use the Pathways focus-ring classes from `JoinCohort.tsx:326` rather than relying on the browser's default outline. `src/index.css:149-150` only restyles `button:focus-visible`, so there is no project-wide link focus style.
- Do not add "opens in a new tab" screen-reader text to the external link. It would need a new i18n key in four locale files (`src/locales/{en,es,vi,zh-CN}/pathways.json`), which widens the change beyond the defect. The existing `ExternalLink` icon (line 175) stays as the visual cue.
- Leave the back buttons (lines 51–56 and 75–80) alone; they are already `<button>` elements and keyboard-operable.
- Leave the other `window.open` call sites (`CohortDetail.tsx:1033`, `worksheets.ts:825`, `TeacherResources.tsx:97`) alone. They are export or print windows opened from handler functions, not the click-only card pattern in this finding.
- Put the tests in the existing `src/components/__tests__/` directory rather than a new `src/components/pathways/__tests__/`. The proposal validator accepts only paths that already exist, and `src/components/pathways/__tests__/` does not.
- Use a local `react-i18next` mock that returns the `t` default argument rather than the shared `enMock`. `enMock` only resolves `common.json` keys (`src/test/i18nMock.ts:20`), so Pathways names would come back as raw keys.

## Open questions

- `## Touched paths` lists the directory `src/components/__tests__`, because the new file `src/components/__tests__/PathwaysTrackNavigation.test.tsx` does not exist yet and the validator rejects paths that do not exist. If the package-time diff check matches exact file paths rather than directory prefixes, a reviewer needs to accept that one new file under that directory. The alternative is to tell the harness to allow new-file entries.
- Should tests for Pathways components live in a new colocated `src/components/pathways/__tests__/` directory instead? That fits the `<area>/__tests__/` convention better (e.g. `src/components/teacher/__tests__/`), but could not be listed here for the reason above.

## Touched paths

- src/components/pathways/TracksOverview.tsx
- src/components/pathways/TrackDetail.tsx
- src/components/__tests__

## Risks

- **Gates not run.** I did not run any gate on the untouched tree. `gate_expectation: green` with an empty `baseline_red` is an expectation, not a measurement.
- **Behaviour change for keyboard users.** Opening the external module now marks it completed with score 100 for keyboard users too, as it already does for mouse users (`TrackDetail.tsx:140-145`). The rule that opening the link counts as completion is existing product behaviour that this change does not alter. The handler also adds a duplicate state entry and ignores the response status if the module was already completed; that also exists today and is not fixed here.
- **Long accessible names.** A link wrapping the whole card is announced with all of its text (name, tagline, bands, description, module count, "Start"). The reviewer should decide whether that is acceptable or whether a stretched link on the heading is preferred.
- **Reviewer checks in `TrackDetail.tsx`.** The reviewer should confirm that `key={mod.slug}` sits on the outermost element in all three branches, and that the class string for each state is unchanged apart from the added focus-ring classes.
- **Layout.** `<a>` and `<Link>` render inline by default. The existing `flex` classes on both cards make them flex containers, so layout should not change. A visual check of the tracks grid and module list in light and dark mode is still worth doing.
- **Focus ring in dark mode.** `focus-visible:ring-offset-2` draws a white offset in dark mode, the same as the existing `JoinCohort.tsx` buttons.
- **jsdom noise in tests.** Clicking an anchor with `target="_blank"` in jsdom can log "Not implemented: navigation". The test should cancel the default action (for example with a document-level click listener calling `preventDefault`) rather than hide the message.
- **No instructions in the issue body.** The command table in the body is harness boilerplate describing commands for trusted users. It is not addressed to this agent and was not acted on.
