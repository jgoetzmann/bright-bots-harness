---
issue: 89
upstream_issue: null
title: "test(e2e): gate the shell smoke on an axe WCAG 2.1 A/AA audit of the homepage"
kind: test
slices: 3
risk: medium
touched_paths:
  - "cypress/support/e2e.ts"
  - "cypress/e2e/smoke.cy.ts"
  - "src/pages/Index.tsx"
  - "docs/ops/ci.md"
depends_on: []
estimated_turns: 45
gate_expectation: green
baseline_red: []
---

# test(e2e): gate the shell smoke on an axe WCAG 2.1 A/AA audit of the homepage

## Issue

Harness issue #89 tracks finding 3 (`audit:63:3`, severity high) of audit jgoetzmann/bright-bots-harness#63, titled "No automated accessibility gate runs anywhere in CI". No product-repository issue exists. The finding names `cypress-axe`, `checkAccessibility`, `cypress/e2e/legacy/`, `test:e2e:ci`, `test:e2e:ci:flows` and `typeof cy.checkAccessibility === "function"`. It names a problem and leaves the remedy to this proposal.

## Diagnosis

**1. Nothing in CI runs an accessibility audit.**
- `cypress-axe` appears only in `package.json:154`, `package-lock.json` and the prose at `docs/ops/ci.md:231`.
- The Cypress support entry imports only `./commands` and `cypress-real-events` (`cypress/support/e2e.ts:2-4`). `cypress/support/commands.ts:17` registers only `loginAsTeacher`.
- No spec calls `injectAxe` or `checkA11y`.

**2. What each CI script actually checks.**
- `test:e2e:ci` runs one spec, `smoke.cy.ts` (`package.json:30`). That spec asserts that the shell mounts and logs no console errors, nothing more (`cypress/e2e/smoke.cy.ts:13-27`).
- `test:e2e:ci:flows` runs four flow specs (`package.json:31`). They assert product behaviour. The keyboard case in `pathways-consent.cy.ts:156-205` checks one behaviour; it does not audit a page.
- The Waterworks step (`.github/workflows/ci-cd.yml:120-121`, read only) checks layout overflow.

**3. Part of the finding is already resolved.**
- The fossil `classRoster.cy.ts`, the never-loaded `cypress/support/index.js`, and the `typeof cy.checkAccessibility === "function"` guards were removed earlier (`docs/ops/ci.md:224-233`).
- A search of `cypress/` finds no `checkAccessibility` and no `typeof cy.` guard.
- `cypress/e2e/legacy/` is outside `specPattern` (`cypress.config.ts:20`) and outside both CI scripts. There is nothing to change there.
- The remaining defect is the missing gate, plus a declared dependency that nothing imports.

**4. The tooling is already installed.**
- `cypress-axe` 1.7.0 is in the lockfile (`package-lock.json:9008-9021`).
- Its peer `axe-core` 4.11.1 is installed as an automatic peer (`package-lock.json:7696-7706`, `"peer": true`). There is no `.npmrc` that disables peer installs.

**5. Turning the gate on alone would fail on day one.**
- `brightboost.blue` is `#46B1E6` (`tailwind.config.ts:18`). I computed its contrast against white from the hex values with the WCAG luminance formula: about **2.4:1**. WCAG 1.4.3 requires 4.5:1 for normal text. No axe run produced this number.
- The four "Explore" links use it as text colour on opaque white cards (`src/pages/Index.tsx:295`; `Card` is `bg-white`, `src/components/ui/card.tsx:12`).
- "Send Feedback" uses it as the background behind white text (`src/pages/Index.tsx:350`).
- Both are `text-sm` bold, so the large-text exemption does not apply. Because the cards are opaque, axe can compute a definite result for these nodes and will report them as `color-contrast` violations.
- Elsewhere on the page, text sits over CSS gradients: the hero has an inline gradient at `Index.tsx:154`, and the page background is a gradient (`GameBackground.tsx:10`). axe reports text over a background image as `incomplete`, which does not fail.
- Reading the rest of `Index.tsx`, `LanguageToggle.tsx`, `MascotHeroVisual.tsx`, `FloatingBadge.tsx` and `Cloud.tsx`, I found no other likely WCAG A/AA violation. This is a prediction from source, not a run.

**6. Where a gate can live without touching `.github/`.**
- `test:e2e:ci` must name a single spec file. `scripts/__tests__/ciWiring.test.ts:18-26` and W-2 (`:84-92`) check that the whole `--spec "…"` string exists on disk, so a comma-separated list would break W-2.
- `smoke.cy.ts` runs three times in the required `build-and-test` job:
  - through `npm test` → ciWiring W-8 → `scripts/verify-ci-shell-gate.sh:186-191` (`ci-cd.yml:98-99`);
  - through `npm run verify:ci-gate` (`:107-108`);
  - directly (`:117-118`).
- W-8 is also in the Vitest `unit` project, because `vitest.config.ts:23-31` does not exclude `scripts/__tests__`. So the harness's `npm run test:unit` gate exercises the smoke spec.
- `build-and-test` uploads only `build-files` (`ci-cd.yml:126-129`). On failure, the terminal log is the only diagnostic.

## Approach

1. Register `cypress-axe` once in the support entry, the same way `cypress-real-events` is registered.
2. In `smoke.cy.ts`, after the existing console-error assertion, inject axe and run `cy.checkA11y` over the whole document with `runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] }`. There is no impact filter, no disabled rule, no excluded node and no `skipFailures`. The violation callback fails the test with a Chai `expect` whose message lists each rule id, its impact and its target selectors, so the `build-and-test` log says what broke.
3. Run this against the untouched homepage first and record the actual axe output, which is expected to be red.
4. Fix the two predicted contrast sites in `Index.tsx`, using the navy token the page already uses for its other solid buttons and links.
5. Correct `docs/ops/ci.md`, which currently says that nothing audits a page.

This makes the audit part of a required check without editing CI files, adding dependencies, or exempting known failures.

## Slices

1. Wire the gate. Add `import "cypress-axe";` to `cypress/support/e2e.ts`. In `cypress/e2e/smoke.cy.ts`, add `cy.injectAxe()` and `cy.checkA11y(...)` with WCAG 2.0/2.1 A+AA tags and an asserting violation callback, after the console-error check inside the existing P-01 `it`. Extend that `it` title to mention the audit. Run it on the untouched homepage and record the real violations it reports.
2. Fix the homepage. Commit a Prettier-only reformat of `src/pages/Index.tsx` (`npx prettier --write -- src/pages/Index.tsx`). In a separate commit, change `text-brightboost-blue` to `text-brightboost-navy` on the "Explore" links and `bg-brightboost-blue hover:bg-brightboost-blue/90` to `bg-brightboost-navy hover:bg-brightboost-navy/90` on "Send Feedback". Re-run until green. Then confirm locally that a temporary sabotage turns it red; do not commit the sabotage.
3. Correct `docs/ops/ci.md`:
   - the `build-and-test` row and the red-signal row;
   - the "Accessibility and performance are not asserted" section: the homepage is now audited for WCAG 2.1 A/AA, `incomplete` results do not fail, and performance is still unasserted;
   - the cheat-sheet row;
   - the "Last verified" date.

## Behaviors

1. `npm run test:e2e:ci` runs an axe audit of `/` limited to the `wcag2a`, `wcag2aa`, `wcag21a` and `wcag21aa` rule tags, and exits non-zero if axe reports any violation.
2. When the audit fails, the Cypress failure message printed to the terminal names every violated rule id, its impact, and its target selectors.
3. On the untouched homepage with the gate wired in, the audit fails on `color-contrast` for the "Explore" links and the "Send Feedback" button (predicted; the real output is recorded in slice 1).
4. After slice 2, the homepage audit reports zero violations and `npm run test:e2e:ci` exits 0.
5. With a temporary violation (for example, reverting one "Explore" link to `text-brightboost-blue`), `npm run test:e2e:ci` exits non-zero.
6. `npm run verify:ci-gate` still prints `PASS: CI shell gate has teeth.`
7. The "Explore" links and the "Send Feedback" button render navy (`#1C3D6C`) against white, about 10.9:1.
8. The `e2e-flows` subset, the legacy specs and the staging specs behave exactly as before.

## Acceptance criteria

- `git diff --name-only` against the base lists exactly `cypress/support/e2e.ts`, `cypress/e2e/smoke.cy.ts`, `src/pages/Index.tsx` and `docs/ops/ci.md`.
- `package.json`, `package-lock.json`, `cypress.config.ts`, every file under `.github/`, and every timeout and retry value are unchanged.
- `cypress/support/e2e.ts` contains `import "cypress-axe";` and no other new statement.
- The `checkA11y` call in `smoke.cy.ts`:
  - passes `runOnly` with exactly the four WCAG 2.0/2.1 A/AA tags;
  - passes no `includedImpacts`, no `rules`, no `exclude` context, and no `skipFailures` argument.
- `smoke.cy.ts` contains no `Cypress.env(` and no `cy.wrap({})…log(` (ciWiring W-3 and W-4 stay green).
- The delivery quotes the real axe output from the untouched homepage with the gate wired in, or states plainly that Cypress could not be run.
- Excluding the Prettier-only commit, the `Index.tsx` changes are exactly the two class-string swaps.
- `npm run lint`, `npm run format:check`, `npm run docs:check` and `npm run test:unit` pass. Within `test:unit`, ciWiring W-8 must pass inside its existing 180 s limit.
- `docs/ops/ci.md` no longer says that nothing audits a page or that `cypress-axe` is imported by nothing. It states the audit's scope: the homepage, WCAG 2.1 A/AA, and that `incomplete` results do not fail.

## Decisions

- Put the audit in `smoke.cy.ts`, the spec behind the required `build-and-test` job, which `npm run test:unit` also exercises. A new spec was rejected for three reasons: `touched_paths` may list only files that exist now; `test:e2e:ci` cannot name a second spec without breaking ciWiring W-2 (`ciWiring.test.ts:18-26`, `:84-92`); and running a new spec in CI would mean editing `.github/`.
- Leave the `e2e-flows` specs alone. They need Postgres and a seeded stack that the harness cannot run to set a baseline, and `pathways-consent` is stateful with `retries: 0`. Extending the audit to authenticated pages is a follow-up (see Open questions).
- Run the audit inside the existing P-01 `it`, not a second `it`. In the sabotaged phase of `verify-ci-shell-gate.sh`, a second test would also wait out `defaultCommandTimeout: 10000` twice (`cypress.config.ts:26-30`, `runMode: 1`). That adds roughly 20 s inside W-8's fixed 180 s budget (`ciWiring.test.ts:173`, `:192`), and raising that timeout is forbidden. The rule id in the failure message keeps the signal readable.
- Use the WCAG 2.0/2.1 A+AA tags only. axe's default set was rejected because its best-practice rules (`region`, `heading-order`, `landmark-*`) are advisory, not conformance requirements. `wcag22aa` was rejected because whether `target-size` passes on the footer links cannot be predicted from source; adding it is a later, separate step.
- Fail on every impact level. An `includedImpacts` filter would silently ignore moderate and minor conformance failures.
- Fix the contrast instead of exempting it. Excluding `color-contrast` or the affected nodes would ship a gate that is green over known failures. Changing the `brightboost.blue` token in `tailwind.config.ts` was rejected because it recolours every page that uses the token.
- Use `brightboost-navy` as the replacement. It is the page's existing solid-button and link colour (`Index.tsx:135`, `:245`, `:272`, `:401`). A new darker blue was rejected because it would add a colour outside the palette.
- Report violations by asserting in the violation callback, which matches the `expect(…, message)` style at `smoke.cy.ts:23-26`. Logging through `cy.task` was rejected because it needs `setupNodeEvents` in `cypress.config.ts`, a file that is kept self-contained on purpose (`cypress.config.ts:7-8`). cypress-axe's own count-only assertion was rejected because a bare count in the `build-and-test` log does not say what to fix.
- Import `cypress-axe` in `cypress/support/e2e.ts`, as is done for `cypress-real-events` (`e2e.ts:3-4`). A wrapper command in `commands.ts` was rejected for two reasons: there is only one call site, and `cypress/support/**/*.ts` counts toward the 90 % coverage floor, where functions sit at 90.9 % (`vitest.config.ts:34-51`).
- Do not add `axe-core` to `devDependencies`. It is already installed as an automatic peer, so declaring it would only churn the lockfile.
- Use `cy.injectAxe()`'s default `axe-core` path. If it fails to resolve under Cypress 13's preprocessor, the failure is loud (a `readFile` error). The decided fallback is `cy.injectAxe({ axeCorePath: "node_modules/axe-core/axe.min.js" })`, applied only if that happens and reported in the delivery.
- Format `Index.tsx` with Prettier in its own commit. The file does not follow Prettier's defaults (for example, line 126 exceeds 80 columns, and the repo has no Prettier config). `format:check` checks every changed file in full (`scripts/format-check.sh:25-36`), and so does the pre-commit `lint-staged` hook (`package.json:199-201`). A separate commit keeps the two class swaps reviewable on their own.
- Leave the "healthy baseline is RED" message in `verify-ci-shell-gate.sh:229-238` unchanged. The Cypress output just above it names the axe rule, and editing the gate script is out of scope.
- Do not add a performance budget. `docs/ops/ci.md:233` explicitly says not to restore the removed performance command.

## Open questions

- Is navy acceptable for the "Explore" links and the "Send Feedback" button, or does design want a darker blue that keeps the brand hue?
- Should the hero buttons be fixed in a follow-up? The white-on-gradient CTAs (`Index.tsx:171-195`) and the "Sign up free →" link (`:201`) use `#46B1E6`/`#2f92ca` at about 2.4–3.4:1. axe marks them `incomplete`, so this gate will not catch them.
- Should the audit later cover authenticated pages (the teacher dashboard in `auth-login.cy.ts`, `/pathways/join`) in `e2e-flows`, and should it move to WCAG 2.2 AA tags?
- Does the harness's environment have the Cypress binary and a free `:5173`? `npm run test:unit` runs W-8, which needs both. I have not run any gate. If they are missing, W-8 fails before this change, and the audit can only be proven in CI.
- How much headroom W-8 has under its 180 s limit is unmeasured.

## Touched paths

- cypress/support/e2e.ts
- cypress/e2e/smoke.cy.ts
- src/pages/Index.tsx
- docs/ops/ci.md

## Risks

- The predicted baseline (one rule, `color-contrast`, on about five nodes) comes from reading source, not from an axe run. If the run reports other violations, the implementation must stop and report them as a blocked item. It must not exclude rules or widen scope beyond the listed paths.
- The required `build-and-test` check gains a new failure mode in three places: W-8, `verify:ci-gate`, and the smoke step. That is intended. A homepage regression will show as "healthy baseline is RED" in `verify:ci-gate`, which can mislead someone reading only that line.
- Each healthy run adds a few seconds for injecting and running axe, three times per job. Check W-8's duration in the delivery.
- Animated layers (clouds, mascot) could make contrast results vary between runs. The predicted nodes sit on opaque cards, so they should be stable. Any variation between runs is a finding to report, not something to retry around.
- This is a visible change to the public homepage: four links and one button change from light blue to navy.
- The Prettier commit rewrites much of `Index.tsx`. The reviewer should read the class-swap commit on its own.
- A green audit does not mean the homepage is accessible. It covers automatable WCAG 2.1 A/AA rules on one page, and `incomplete` results (all text over the gradient) are not enforced.
- The issue body contains no instruction aimed at the agent. Its command table is addressed to maintainers and was not acted on. Its path list is partly stale (see Diagnosis 3).
