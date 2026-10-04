---
issue: 87
upstream_issue: null
title: "fix(a11y): darken brightboost blue token so white text on it meets wcag aa"
kind: fix
slices: 3
risk: medium
touched_paths:
  - "tailwind.config.ts"
  - "src/__tests__/iconControlContrast.test.ts"
  - "docs/brightboost-style-reference.md"
depends_on: []
estimated_turns: 25
gate_expectation: green
baseline_red: []
---

# fix(a11y): darken brightboost blue token so white text on it meets wcag aa

## Issue

Harness issue #87, which tracks finding 1 of audit harness#63 (`audit:63:1`), severity high. There is no product-repository issue. The finding is titled "Primary brand button colour fails 1.4.3 at 2.42:1". It names `brightboost-blue`, `#46B1E6`, `tailwind.config.ts:18` and `.game-button`. It describes the problem and proposes no fix.

## Diagnosis

The finding is correct, and the defect sits where it says: in the token value.

- `tailwind.config.ts:18` defines `brightboost.blue: "#46B1E6"`. Its WCAG 2.x relative luminance is 0.3846, so white on it gives (1.05)/(0.3846+0.05) = **2.42:1**. I computed this by hand with the same formula as `src/__tests__/iconControlContrast.test.ts:49-65`.
- `src/App.css:46-48`: `.game-button` applies `bg-brightboost-blue text-white font-bold` with no text size, so it renders at 16px bold. That is not "large text", so 1.4.3 needs 4.5:1. At 2.42 it also fails the 3:1 large-text floor, so text size cannot rescue it: `src/pages/Home.tsx:20` is `text-lg` bold, which is 18px, still under 18.66px.
- Routed users of `.game-button`: `src/pages/PrivacyPolicy.tsx:88`, `src/pages/NotFound.tsx:26` and `src/pages/TermsOfService.tsx:75`, imported at `src/App.tsx:25,53,54`. I found no import of `src/pages/Home.tsx` under `src/`.
- `src/App.css:62-64`: `.badge-points` uses the same `bg-brightboost-blue text-white` pair. I found no users.
- `.game-button` is not the main problem, only one instance of it. The same fill-plus-white-text pair is written inline on about 50 lines. Examples: `src/pages/TeacherClasses.tsx:139`, `src/pages/StudentClassLogin.tsx:234`, `src/components/modules/GuidedChoicePanel.tsx:286`, `src/components/FeedbackFab.tsx:58`, `src/components/TeacherDashboard/TeacherLayout.tsx:91` (the skip link) and `src/pages/JoinClass.tsx:106`. `text-brightboost-blue` is also used as link text on white on about 30 lines, all at the same 2.42:1. Examples: `src/pages/StudentLogin.tsx:142`, `src/pages/ForgotPassword.tsx:74`, `src/pages/ParentGuide.tsx:109`, `src/components/teacher/PrintLoginCards.tsx:67`.
- Four sites lighten the fill on hover with `hover:bg-brightboost-blue/90`: `src/pages/ResetPassword.tsx:153`, `src/pages/ForgotPassword.tsx:110`, `src/pages/Index.tsx:350` and `src/components/ui/StreakMeter.tsx:210`. Any fix has to hold in that state too.
- Two things are not the cause. The shared `<Button variant="primary">` uses `bg-blue-600` (`src/components/ui/button.tsx:11`), not the brand token. The semantic `--primary` is `222.2 47.4% 11.2%` (`src/index.css:16`), a near-black.
- The hex is also hard-coded, bypassing the token, in `src/pages/Index.tsx:173,231,261`, `src/pages/PlanDetail.tsx:104`, `src/pages/TryDemo.tsx:260`, `src/components/RobotCharacter.tsx:24-36`, `src/components/biomeBuddy/StatBars.tsx:29`, `src/components/home/MascotHeroVisual.tsx:31,46`, `src/components/ui/BadgeSlot.tsx:60` and `backend/src/routes/resources.ts:46`. A token change does not reach these.
- One of those hard-coded sites fails in the same way. `src/pages/Index.tsx:173` is the homepage teacher call-to-action: a gradient from `#46B1E6` to `#2f92ca` under white text (`chunkyPrimary`, `src/pages/Index.tsx:121`), at about 2.42 to 3.45:1.

## Approach

Change the value of `brightboost.blue` at `tailwind.config.ts:18` from `#46B1E6` to `#156D99`. The new value keeps the brand hue (about 200°) and saturation (about 76%) and lowers lightness from 59% to 34%. One line fixes every Tailwind-class use:

- `.game-button` and `.badge-points`
- the ~50 inline buttons
- the ~30 text links
- focus rings: a darker ring on white is easier to see

My hand-computed figures:

- white on `#156D99`: about 5.71:1
- white on the `/90` hover composite over white: about 4.66:1
- navy hover (`#1C3D6C`) on `.game-button`: about 10.9:1, unchanged

Add a guard that is always on in `npm run test:unit`. It reads `brightboost.blue` from the live Tailwind config and checks it against both thresholds. It also asserts that the old value fails, following the #810 "gray-400 fails, gray-500 passes" pattern at `src/__tests__/iconControlContrast.test.ts:81-89`. If the palette drifts, the test fails instead of the colour regressing silently.

Update the one document that claims to mirror `tailwind.config.ts` (`docs/brightboost-style-reference.md:5-9`) so it does not go stale.

Rejected alternatives are recorded under Decisions.

## Slices

1. `tailwind.config.ts:18`: change `brightboost.blue` from `"#46B1E6"` to `"#156D99"`. Change nothing else in the `brightboost` block.
2. `src/__tests__/iconControlContrast.test.ts`: add a separate `describe` block for `audit:63:1` that imports `tailwind.config.ts` and reuses the file's `relativeLuminance`/`contrastOnWhite` helpers. It asserts that `brightboost.blue` is at least 4.5:1 against white, that its 90%-over-white composite is at least 4.5:1, and that the old `#46B1E6` is below 3:1. Leave the existing #810 cases untouched.
3. `docs/brightboost-style-reference.md:9`: change the documented `brightboost.blue` hex to `#156D99`. Add one line saying it was darkened for WCAG 1.4.3 with white text, and that `#46B1E6` remains hard-coded in illustration and mascot components.

## Behaviors

1. White text on any `bg-brightboost-blue` fill, including `.game-button` on `/privacy`, `/terms` and the 404 page, measures at least 4.5:1.
2. `text-brightboost-blue` text on a white surface measures at least 4.5:1.
3. White text on a `hover:bg-brightboost-blue/90` fill over white measures at least 4.5:1.
4. The `.game-button` hover state (navy fill, white text) looks exactly as it does today.
5. Setting `tailwind.config.ts:18` back to `#46B1E6` makes `npm run test:unit` fail in the new `audit:63:1` block.
6. Elements coloured with a hard-coded `#46B1E6` (the mascot, stat bars, homepage gradient, print wordmark) render exactly as they do today.
7. `docs/brightboost-style-reference.md` gives the same `brightboost.blue` hex as `tailwind.config.ts`.

## Acceptance criteria

- The diff touches exactly `tailwind.config.ts`, `src/__tests__/iconControlContrast.test.ts` and `docs/brightboost-style-reference.md`, and nothing else.
- `tailwind.config.ts:18` reads `blue: "#156D99",` and the other four `brightboost` keys are byte-identical.
- The new test takes the colour from the imported Tailwind config, not from a hex literal copied into the test (the old `#46B1E6` literal is allowed only in the "old value fails" assertion).
- `npm run test:unit` passes. Reverting line 18 locally makes the new block fail at the 4.5:1 assertion.
- The two pre-existing #810 test cases and the `ALLOWED` set in `src/__tests__/iconControlContrast.test.ts` are unchanged.
- `npm run lint`, `npm run typecheck` and `npm run build` pass.
- In the built CSS, the `.game-button` background-color is `rgb(21 109 153 …)`.
- No `.skip(`, `.only(`, threshold, timeout or lint-rule change appears anywhere in the diff, and no path under `.github/`.

## Decisions

- **Change the token's value; don't add a second darker token and migrate call sites.** Migration would touch ~35 files (~50 fill lines and ~30 text lines), would be easy to leave incomplete, and would leave the failing colour as the default for new code.
- **Don't fix only `.game-button`/`.badge-points` in `src/App.css`.** That repairs three routed links and leaves the same 2.42:1 pair on about 80 other lines. It also would not reliably change `src/pages/Home.tsx:20`, which repeats `bg-brightboost-blue` inline next to the class.
- **Don't switch `.game-button` text to navy on the current blue.** Navy on `#46B1E6` computes to 4.496:1, which fails strictly and fails worse in any lighter state.
- **Chose `#156D99`: brand hue and saturation, lightness lowered to 34%.** I rejected `#1673A2` (36%): it passes at rest (about 5.24:1) but fails in the `/90` hover state (about 4.34:1). I rejected Tailwind `sky-700` `#0369A1` (about 5.93:1): it passes too, but it moves saturation from 76% to 96%, and there is no reason to borrow a palette value when one derived from the brand colour passes.
- **The guard goes in the existing `src/__tests__/iconControlContrast.test.ts`, not a new file.** It reuses the file's contrast helpers. Also, the harness only accepts touched paths that already exist, so a new file could not be declared. The cost is that the file name refers to #810; the new block gets its own header comment.
- **The guard checks the arithmetic, not a render.** The Chromium-based pattern in `src/__tests__/globalCss.colorScheme.test.ts:94-108` skips when Playwright's browser is absent, so it would not run under `npm run test:unit` everywhere.
- **The hard-coded `#46B1E6` sites stay as they are.** Most are illustration, mascot or 20%-opacity border tints, and `backend/src/routes/resources.ts:57` colours a logotype, which 1.4.3 exempts. The one text-bearing site, `src/pages/Index.tsx:173`, is listed under Open questions.
- **The `/80` hover sites stay as they are:** `src/pages/QuantumDemo.tsx:152`, `src/pages/Stem1.tsx:195`, and the text hover at `src/pages/Index.tsx:201`. They improve, but still fail on hover at about 3.82:1. Fixing them means editing per-site hover styles, which is outside this finding.
- **Update `docs/brightboost-style-reference.md` but not `docs/brightboost-homepage-v2-spec.md:105,390`.** The first says it mirrors `tailwind.config.ts`. The second is the homepage v2 design spec, and the homepage hero still uses the hard-coded `#46B1E6`.

## Open questions

- Does the brand owner accept `#156D99`, which turns sky blue into a deeper blue across the app's UI? If they want a different hex, it must be at least 4.5:1 against white and at least 4.5:1 at 90% over white. The new test enforces both.
- Should the homepage teacher call-to-action gradient (`src/pages/Index.tsx:173`, white on `#46B1E6` to `#2f92ca`, about 2.42 to 3.45:1, `hover:brightness-110`) be added to this package via `/harness revise`, or promoted as its own item? It needs a new two-stop gradient on the homepage hero.
- The rocket icon in the teacher navbar (`src/components/BrightBoostRobot.tsx:18`, on navy at `src/components/TeacherDashboard/TeacherNavbar.tsx:34,42`) drops from about 4.50:1 to about 1.90:1. It is a logo glyph beside the wordmark heading, so 1.4.11 does not require it, but it will look dimmer. Accept that, or open a follow-up?
- Should the `/80` hover sites listed under Decisions become a separate item?
- `src/pages/Home.tsx` appears to be unrouted. If it is live, its line 26 pairs white text with `brightboost-lightblue` at about 1.67:1, which is a separate finding.
- I did not run any gate on the untouched tree. `gate_expectation: green` is an expectation, not a measurement.

## Touched paths

- tailwind.config.ts
- src/__tests__/iconControlContrast.test.ts
- docs/brightboost-style-reference.md

## Risks

- **Large visual change from a one-line diff.** About 100 class uses across roughly 50 files change colour at once, including decorative ones:
  - progress bars: `src/components/StudentDashboard/XPProgress.tsx:67`, `src/components/ui/XPProgressBar.tsx:207`, `src/pages/StudentBenchmark.tsx:136`, `src/components/TeacherDashboard/CSVImportModal.tsx:169`
  - step dots: `src/components/teacher/TeacherTutorial.tsx:98`, `src/pages/ActivityPlayer.tsx:652`
  - the leaderboard rank badge at `src/components/LeaderboardCard.tsx:38`
  - the `/5` to `/30` tints and the illustration strokes in `src/theme/activityIllustrations.tsx`

  The reviewer should look at the teacher dashboard, the student login and a legal page before and after, not only at the diff.
- **Two blues will coexist.** The token moves; the hard-coded `#46B1E6` sites (mascot, homepage, badge gradient) do not.
- **One known regression on a dark surface:** the navbar rocket, as described under Open questions. I checked the other dark-surface uses I found (the `hover:bg-brightboost-blue/20` tint on navy at `src/components/TeacherDashboard/TeacherNavbar.tsx:52` and the `bg-black/50` modal overlays); white-text contrast on them stays the same or improves.
- **My contrast figures are hand-computed.** The new test is authoritative. If it disagrees, revise the hex, never the threshold.
- **The test imports `tailwind.config.ts` into a node-environment Vitest file.** The config has only a type import, so this should resolve. If it does not, use `tailwindcss/resolveConfig`; a hex copied into the test is not an acceptable fallback. `__tests__` is excluded from `tsc` (`tsconfig.json:35`), so `npm run typecheck` does not check the test.
- **No instructions aimed at an automated agent appear in the issue body.** The command table in it is the harness's own boilerplate for trusted maintainers; I read it as data and did not act on it.
- Nothing touches `prisma/`, migrations, predeploy scripts, `.env*` or `.github/`.
