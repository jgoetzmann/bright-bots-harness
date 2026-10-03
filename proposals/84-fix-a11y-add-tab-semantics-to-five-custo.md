---
issue: 84
upstream_issue: null
title: "fix(a11y): add tab semantics to five custom tab bars and fix the homepage audience picker"
kind: fix
slices: 5
risk: low
touched_paths:
  - "src/pages/TeacherModulePrep.tsx"
  - "src/pages/TeacherPDHub.tsx"
  - "src/components/pathways/facilitator/pages/CohortDetail.tsx"
  - "src/components/pathways/challenges/MobileToolbox.tsx"
  - "src/pages/Index.tsx"
depends_on: []
estimated_turns: 35
gate_expectation: green
baseline_red: []
---

# fix(a11y): add tab semantics to five custom tab bars and fix the homepage audience picker

## Issue

This is harness issue #84. It tracks finding 23 of audit jgoetzmann/bright-bots-harness#60 (machine reference `audit:60:23`). No product issue exists (`upstream_issue: null`). The finding is rated medium. Its title is "Four custom tab bars ship no tab semantics and a fifth is half-built". It names `<button>`, `role="tablist"`, `role="tab"`, `aria-selected`, `role="tabpanel"` and `aria-controls`. The issue body says nothing more about where these are.

## Diagnosis

The repository has one complete hand-built tab bar to compare against: `src/components/pathways/facilitator/ProgramOverview.tsx:60-94`.
- The container has `role="tablist"` and an `aria-label`.
- Each button has `role="tab"`, `aria-selected`, `aria-controls` and an `id`.
- The content wrapper has `role="tabpanel"` and `aria-labelledby`.

I found five bars with none of this, one more than the finding counts. In each of them the selected tab is shown only by a class change. Assistive technology hears a row of plain buttons with no selected state and no link to the content they switch. That fails WCAG 1.3.1 and 4.1.2, because the state is not programmatically determinable.

1. **`src/pages/TeacherModulePrep.tsx:173-187`.** A bare `div` with four `button`s. The active tab is marked only by the class ternary at :179-181. The four panels at :192-384 have no role or id. Their root elements are :193 (`div`), :252 (`section`), :293 (`div`) and :344 (`div`).
2. **`src/pages/TeacherPDHub.tsx:318-332`.** The same pattern with four buttons, active only by class at :324-326. Each panel has its own root `<div className="space-y-4">` at :336, :601, :800 and :873.
3. **`src/components/pathways/facilitator/pages/CohortDetail.tsx:248-268`.** Seven buttons, active only by class at :258-262. Clicking one also sets `window.location.hash = tt.key` (:256). The panels are components rendered at :270-284, so this file has no panel root element of its own.
4. **`CohortDetail.tsx:369-392`, the `SubTabBar` inside `EngagementTab`.** An Overview/Challenges switch with state at :336 and active only by class. It is rendered in two branches:
   - challenges: :394-401, next to `<EngagementChallengesPanel>`;
   - overview: :418-548, as the first child of a `space-y-5` div followed by the metric content.
   It sits inside bar 3's content and also has an `overview` key.
5. **`src/components/pathways/challenges/MobileToolbox.tsx:71-94`.** This is the one the audit did not count. The file's own header says the sheet opens "with two tabs" (:8). It has two buttons, active only by class, and a single content `div` at :97-105.

**The "half-built" one is `src/pages/Index.tsx:311-328`, but the finding misreads what is wrong with it.** It has `role="tablist"`, `role="tab"` and `aria-selected`, with no `tabpanel` or `aria-controls`. Adding those would be wrong, because nothing here is a panel:
- `feedbackAudience` (:30) is read only in the class ternary at :320 and by `track(...)` at :117.
- The textarea (:330-338) and email field (:340-348) are the same whichever chip is chosen.

This control is a single-choice form input. The defect is the tab role itself: it tells assistive technology to expect a panel that does not exist.

`src/components/ui/tabs.tsx` (Radix) is already used correctly by `src/pages/PlayHub.tsx:1-39` and is out of scope.

## Approach

Apply the `ProgramOverview.tsx` pattern to the five bars without changing layout, and replace the misused tablist on the homepage.

- **Tab bars.** The container gets `role="tablist"` and an `aria-label`. Each tab button gets `role="tab"`, `aria-selected={<the condition that already picks the active class>}`, and an `id` and `aria-controls` built from a `useId()` prefix plus the tab key. Click handlers, class strings and conditional rendering do not change.
- **Panels, no wrapper.** Where a panel has its own root element, that element gets `role="tabpanel"`, an `id` and `aria-labelledby`. This applies to TeacherModulePrep :193/:252/:293/:344, TeacherPDHub :336/:601/:800/:873 and MobileToolbox :97. No lines are re-indented.
- **Panels, one wrapper.** A single wrapper `div` with the same attributes is added only where there is no such element:
  - around the conditional components at CohortDetail.tsx:270-284;
  - around `<EngagementChallengesPanel>` in the challenges branch;
  - around the metric content after `{SubTabBar}` in the overview branch, carrying `className="space-y-5"` so spacing stays the same.
- **Accessible names reuse strings already in each file**, so no locale files change:
  - `t("teacher.modulePrep.title", { module: moduleTitle })` (as at TeacherModulePrep.tsx:157);
  - `t("teacher.pdHub.title")` (TeacherPDHub.tsx:277);
  - `cohort.name` (CohortDetail.tsx:235);
  - `"Engagement"` (the fallback at CohortDetail.tsx:213; the rest of `EngagementTab` is untranslated English);
  - `"Challenge tools"` (MobileToolbox.tsx:59).
- **Homepage.** In `Index.tsx`, `role="tablist"` becomes `role="group"` (the `aria-label` stays). `role="tab"` and `aria-selected` are removed from each chip, and each chip gets `aria-pressed={feedbackAudience === tab.key}`. This is the pattern the repo already uses for single-choice chips in `src/components/games/echoAvenue/EchoAvenueGame.tsx:659`, `:838` and `:1096`.
- `useId` is the only new import, added to the existing `react` import in four files. `Index.tsx` needs no import change.

## Slices

1. `src/pages/TeacherModulePrep.tsx`: tablist, tab and tabpanel attributes on the tab bar (:173-187) and on the four existing panel roots (:193, :252, :293, :344), with a `useId()` prefix declared with the other hooks before the early returns at :115.
2. `src/pages/TeacherPDHub.tsx`: tablist, tab and tabpanel attributes on the tab bar (:318-332) and the four existing panel roots (:336, :601, :800, :873), with a `useId()` prefix declared before the early return at :262.
3. `src/components/pathways/facilitator/pages/CohortDetail.tsx`: the seven-tab bar (:248-268) plus one tabpanel wrapper around :270-284, then the Engagement `SubTabBar` (:369-392) plus a tabpanel wrapper in each branch. Each bar has its own `useId()` prefix declared before its early returns (:184, :349).
4. `src/components/pathways/challenges/MobileToolbox.tsx`: tablist and tab attributes on :71-94, and tabpanel attributes on the existing content div at :97.
5. `src/pages/Index.tsx`: replace `role="tablist"`, `role="tab"` and `aria-selected` at :311-317 with `role="group"` and a per-chip `aria-pressed`.

## Behaviors

1. On the module-prep page, the four section buttons are exposed as `tab`s in one `tablist` named after the page title.
2. On the PD hub page, the four section buttons are exposed as `tab`s in one `tablist` named with `teacher.pdHub.title`.
3. On a cohort detail page, the seven section buttons are exposed as `tab`s in one `tablist` named with the cohort name.
4. With the cohort Engagement tab selected, Overview and Challenges are exposed as `tab`s in a second `tablist` named "Engagement", inside the cohort's tabpanel.
5. In the mobile challenge-tools sheet, Toolbox and Scratch Pad are exposed as `tab`s in a `tablist` named "Challenge tools".
6. In every one of these tablists, exactly one tab has `aria-selected="true"`, and it is the tab whose content is showing.
7. Clicking a tab moves `aria-selected="true"` to it and shows its content, exactly as before.
8. Every visible tab content region has role `tabpanel`, and its `aria-labelledby` resolves to the selected tab.
9. The selected tab's `aria-controls` resolves to the tabpanel that is showing.
10. With Engagement selected, no two elements on the cohort page share an `id`, even though both bars have an `overview` tab.
11. Clicking a cohort tab still updates `location.hash` (e.g. `#roster`), and the page does not scroll to any element.
12. The homepage feedback section has no `tablist` or `tab`. It has a `group` named "Feedback audience type" containing four buttons, exactly one with `aria-pressed="true"`.
13. Choosing a homepage audience chip and submitting still sends `feedback_submitted` with that audience.
14. Every tab and chip stays in the Tab order and is activated by Enter or Space, as today.
15. None of the affected tab bars, chips or panels looks any different.

## Acceptance criteria

- The diff changes exactly the five files under `## Touched paths`: nothing under `.github/`, `prisma/` or `src/locales/`, and no `package.json` or lockfile.
- Each of the five bar containers has `role="tablist"` and the `aria-label` named in `## Approach`.
- Every button inside those containers has `role="tab"`, an `id`, an `aria-controls`, and an `aria-selected` whose expression is the same as the condition that picks its active class.
- Every `role="tabpanel"` element has an `id` equal to the `aria-controls` of the tab that renders it, and an `aria-labelledby` equal to that tab's `id`.
- No existing `className` string changes. The only new `className` is `"space-y-5"` on the Engagement overview-branch wrapper; the other new wrappers have none.
- `ids` come from `useId()`, and no generated `id` equals a bare tab key such as `roster`.
- `src/pages/Index.tsx` contains no `role="tablist"`, `role="tab"` or `aria-selected`. The chip container has `role="group"` and `aria-label="Feedback audience type"`, and each chip has `aria-pressed={feedbackAudience === tab.key}`.
- `ProgramOverview.tsx`, `src/components/ui/tabs.tsx` and `PlayHub.tsx` are unchanged.
- No new translation keys. The only new import is `useId` from `react`.
- `npm run lint`, `npm run typecheck`, `npm run test:unit` and `npm run build` pass on the delivered tree.
- Rendered and queried by role:
  - module prep has 4 `tab`s;
  - the PD hub has 4;
  - cohort detail has 7, or 9 with Engagement selected;
  - the open mobile tools sheet has 2;
  - the homepage has 0.

## Decisions

- **Hand-written ARIA following `ProgramOverview.tsx:60-94`, rather than moving to Radix via `src/components/ui/tabs.tsx`.** Rejected Radix for four reasons:
  - The wrapper's default classes (tabs.tsx:15 and :30) would restyle every bar.
  - No file outside `src/components/ui/` imports `@radix-ui/*` directly, so using the bare primitive breaks convention.
  - `TabsContent` controls mounting itself, which conflicts with TeacherModulePrep's `hidden print:block` print path (:192-193, :292-293, :343-344).
  - It would re-indent about 700 lines of TeacherPDHub panels.
- **No arrow-key navigation or roving tabindex in this package.** `ProgramOverview.tsx` has none either. Every tab stays a native button in the Tab order, so keyboard users keep full access. Rejected two options:
  - copying a key handler into five places (duplication);
  - a new shared helper file (a path that does not exist yet, which the proposal schema rejects, and a wider change).
  This is raised under `## Open questions`.
- **Homepage chips become `role="group"` with `aria-pressed`, not a completed tablist and not a `radiogroup`.** There is no panel to point at (Index.tsx:117, :330-348). `radiogroup` implies an arrow-key contract this package does not build. `aria-pressed` chips already exist in `EchoAvenueGame.tsx`.
- **`useId()` prefixes rather than ProgramOverview's static `tab-${key}`.** CohortDetail renders two bars on one page that both have an `overview` key, so static ids would clash. `useId` is already used at `src/components/biomeBuddy/ScienceCard.tsx:42-43` and `src/components/biomeBuddy/screens/TestLearnScreen.tsx:96`. It also rules out an `id` equal to a bare hash key, which would make the `location.hash` assignment at CohortDetail.tsx:256 scroll the page.
- **`role="tabpanel"` goes on existing panel roots where they exist, with a wrapper only where they do not.** Rejected one wrapper around all panels everywhere, because it re-indents hundreds of lines in TeacherPDHub and TeacherModulePrep for no semantic gain.
- **`aria-controls` goes on every tab (APG and ProgramOverview), so unselected tabs point at panels that are not mounted.** Rejected rendering every panel hidden: it would mount every cohort tab on load and fire their fetches (e.g. `EngagementTab` at CohortDetail.tsx:338-347).
- **No `tabIndex={0}` on panels, unlike ProgramOverview.tsx:93.** Without roving tabindex it only adds a Tab stop. Copying it would also mean copying `focus:outline-none` (:92), which hides the focus ring. This goes with the keyboard follow-up.
- **Accessible names reuse strings already in each file**, so no locale files are touched. For `EngagementTab` that means hard-coded English "Engagement", matching that component's other untranslated strings (:379, :389).
- **MobileToolbox is included even though the finding counts four bars.** Its defect is identical, and its own comment calls the buttons tabs (:8).
- **No new test files.** The proposal schema only accepts paths that exist now, and no existing test file covers these five components. Verification rests on the acceptance criteria and the gates, and this is raised under `## Open questions`.
- **`ProgramOverview.tsx` is not changed**; it is the reference and outside the finding.

## Open questions

- Should arrow keys, Home/End and roving tabindex (plus a focusable panel with a visible focus ring) be a separate work item covering all six bars? That would include `ProgramOverview.tsx` and its `focus:outline-none` panel at :92-93.
- Do the maintainers want role and state tests for these five components? They would be new files, so they would need a follow-up item or a `/harness revise` that allows new paths.
- The homepage donation-amount chips (Index.tsx:368-383) also expose no selected state. Should the same `aria-pressed` fix be added here or tracked separately?
- CohortDetail's hash deep-link list (:170-178) leaves out `engagement`, so `#engagement` does not select that tab. This is outside this finding. Should it be tracked separately?
- The audit counted four tab bars; this plan covers five. Can the auditor confirm that MobileToolbox and the Engagement sub-bar are both meant to be in scope?

## Touched paths

- src/pages/TeacherModulePrep.tsx
- src/pages/TeacherPDHub.tsx
- src/components/pathways/facilitator/pages/CohortDetail.tsx
- src/components/pathways/challenges/MobileToolbox.tsx
- src/pages/Index.tsx

## Risks

- **No gate has been run.** This proposal step had no shell, so `gate_expectation: green` is an expectation, not a measurement, and `baseline_red` is empty because nothing was measured.
- **Formatting noise.** `scripts/format-check.sh:36` and the pre-commit `lint-staged` hook (`package.json:199-201`) check each changed file as a whole. No Prettier config is present, so the default 80-column width applies. Several lines exceed it, such as `TeacherModulePrep.tsx:53`, `:139`, `:192` and `Index.tsx:23-26`, `:311`. Formatting those files may reflow lines that have nothing to do with this fix. Reviewers should read the diff with whitespace ignored.
- **Re-indentation in `CohortDetail.tsx`.** Wrapping the Engagement overview branch re-indents about 125 lines (:421-546). Check that the wrapper carries `space-y-5` and that nothing moved outside it.
- **Spacing.** The new wrappers replace the panel as the parent's child in a `space-y-*` stack. By reading the code, the margin lands on the wrapper and the inner spacing is unchanged, but I have not rendered it.
- **Button role change.** Changing a button's role to `tab` would break any query by `getByRole("button")` on these labels. I searched `src/**` tests and `cypress/**` and found none, but automation outside the repo was not checked.
- **No arrow keys.** Screen-reader users in focus mode may try arrow keys on a `tab` and get no response. Tab still works. This is the same state ProgramOverview already ships.
- **`useId` format.** React 18 ids contain colons, which is fine for `id` and ARIA references but would need escaping in CSS selectors. The plan does not query them.
- **Issue body.** It is a harness-generated tracking stub. Its `/harness …` command table is boilerplate for maintainers; I did not act on it, and it contains no instructions aimed at the agent.
