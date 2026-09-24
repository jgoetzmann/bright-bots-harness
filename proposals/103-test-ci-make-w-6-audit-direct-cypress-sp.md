---
issue: 103
upstream_issue: null
title: "test(ci): make W-6 audit direct cypress specs instead of a false literal"
kind: test
slices: 2
risk: low
touched_paths:
  - "scripts/__tests__/ciWiring.test.ts"
depends_on: []
estimated_turns: 30
gate_expectation: green
baseline_red: []
---

# test(ci): make W-6 audit direct cypress specs instead of a false literal

## Issue

Harness issue #103 tracks finding 9 (`audit:61:9`, severity medium) of audit jgoetzmann/bright-bots-harness#61. There is no product issue. In the reporter's words, "`W-6` asserts an invariant that is false and can never fail". The reporter cites `ci-cd.yml`, the literal `--spec "cypress/e2e/smoke.cy.ts"`, `npm run test:e2e:ci`, `.github/workflows/ci-cd.yml:118` and `package.json:30`.

## Diagnosis

The test is in `scripts/__tests__/ciWiring.test.ts:125-129`. It reads `.github/workflows/ci-cd.yml` as raw text and asserts `not.toContain('--spec "cypress/e2e/smoke.cy.ts"')`.

**What the test title claims is false.** CI does run `smoke.cy.ts` through `--spec`:

- `.github/workflows/ci-cd.yml:117-118`: the `build-and-test` step "Run Cypress shell smoke" runs `npm run test:e2e:ci`.
- `package.json:30`: `"test:e2e:ci": "cypress run --spec \"cypress/e2e/smoke.cy.ts\""`.

So every PR runs exactly the invocation the title says the workflow does not contain. The test passes only because it reads the one file where the npm-script layer hides the spec.

**The rest of the suite requires the spec W-6 forbids.**

- W-2 (`ciWiring.test.ts:84-92`) requires the `test:e2e:ci` spec to exist.
- W-3 and W-4 (`:94-110`) audit that same spec as the honest CI shell spec.
- W-5 (`:113-123`) runs the step-presence guard, and its manifest requires `npm run test:e2e:ci` in `build-and-test` (`scripts/ci-required-steps.json:24`).
- If the title's claim were made true, CI would have to stop running its own shell smoke.

**It cannot fail on any change that matters.**

- It goes red only on that exact byte string: double quotes, one space, closing quote.
- Every other way of pointing Cypress at the file passes it: `--spec cypress/e2e/smoke.cy.ts`, `--spec 'cypress/e2e/smoke.cy.ts'`, `--spec=cypress/e2e/smoke.cy.ts`, `-s …`, or a comma list `--spec "cypress/e2e/smoke.cy.ts,…"`.
- Because it reads raw text, a commented-out line would turn it red while any active line in another form keeps it green. That is the reverse of the "active commands only" rule the step-presence core follows (`scripts/verify-ci-step-presence-core.mjs:1-7`, `:29-34`).
- No test anywhere shows it can go red. Its neighbours (W-5, W-8/W-9/W-11) are all two-phase proofs.

**How it got this way.** This is inferred from the repo's prompt logs, not from git history, which I could not read:

- The #677 log says the workflow-text wiring guards exist "so the gate cannot be pointed back at a skipping spec unnoticed" (`prompts/2026-07-15-ticket-677-honest-ci-cypress-gate.md:34`).
- At that point the CI shell spec was `cypress/e2e/ci-shell.cy.ts` (same file, `:27`). The forbidden `smoke.cy.ts` was the pre-#677 skipping spec; its defects are the `cy.wrap({}).log(` and `Cypress.env(` patterns W-3 and W-4 still look for.
- #671 then rebuilt `cypress/e2e/smoke.cy.ts` as the new shell smoke (`prompts/2026-08-07-ticket-671-cypress-rebuild.md:20`; the current file is `cypress/e2e/smoke.cy.ts:1-29`). `package.json:30` was pointed at it, and `ci-shell.cy.ts` no longer exists under `cypress/e2e/`.
- The literal survived, and its meaning flipped from "the bad spec" to "the gate spec". The comment at `:125` ("Restored from pre-U1 W-6: step-presence does not cover this negative") shows it was carried forward, not re-derived.

**The gap W-6 was meant to close is still open.** `ci-cd.yml:120-121` calls Cypress directly with `npx cypress run --spec "cypress/e2e/waterworks-mobile.cy.ts"`, with no npm script in between. W-2 to W-4 only read the spec named by `test:e2e:ci`, so nothing audits a spec the workflow names itself. Today `waterworks-mobile.cy.ts` has no silent-skip pattern: a search of `cypress/` for `cy\s*\.wrap\(\{\}\)` finds nothing.

## Approach

Rewrite W-6 in place in `scripts/__tests__/ciWiring.test.ts`. Its hard-coded filename becomes a derived property that is true today and can fail: **every spec that an active `run:` line in `ci-cd.yml` passes straight to `cypress run` exists and contains no silent-skip pattern.** Together with W-3, which covers the `npm run test:e2e:ci` route, the shell job can then not be pointed at a skipping spec by either route. That is the #677 purpose, with no filename to go stale.

- **Find the specs.** Parse the workflow with `yaml`, which is already a devDependency (`package.json:181`) and already used by `verify-ci-step-presence-core.mjs:18`.
  - Walk `jobs.*.steps[].run`.
  - Split each block with the exported `normalizeRunLines` (`verify-ci-step-presence-core.mjs:29`), loaded the same way `verify-ci-step-presence.test.ts:63-69` loads it. This drops blank and `#` lines.
  - Keep lines matching `cypress run`.
  - Pull out every `--spec` / `-s` value in double-quoted, single-quoted, bare and `=` forms, splitting comma lists.
- **Check the specs.**
  - Read each spec through an injected reader.
  - Report a spec that is missing, or that matches the existing `SILENT_SKIP_PATTERN` (`ciWiring.test.ts:29`).
  - Also report a direct `cypress run` line with no `--spec`, because this audit cannot list what it runs.
- **Healthy case.** The real `ci-cd.yml` with the real file reader must give no violations.
- **Falsification case.** Run the same audit on in-memory copies of the parsed workflow, each with one extra `build-and-test` step injected. A stub reader supplies a `cy.wrap({}).log(` body for the injected path. The injected spec must be reported in every quoting form. A commented-out line must not count. Nothing is written to disk. This follows the sandbox rule in `docs/ops/guards.md:23-42`: a guard never writes the checkout.

Only this one test file changes. The workflow, package scripts and docs stay as they are.

## Slices

1. In `scripts/__tests__/ciWiring.test.ts`, replace W-6's title, comment (`:125`) and body (`:126-129`). The new body parses `ci-cd.yml`, collects every active direct `cypress run` spec (`--spec`/`-s` in all forms, comma lists, missing-spec lines) and requires each to exist and not match `SILENT_SKIP_PATTERN`, using two small local helpers and the dynamically imported `normalizeRunLines`.
2. In the same file, add a W-6 falsification `it` that runs the same helpers on in-memory sabotaged workflows and asserts what is and is not reported. The inputs are: an injected skipping spec in double-quoted, single-quoted, bare, `--spec=` and `-s` forms; a comma list; a missing path; a `cypress run` with no `--spec`; and a commented-out line. No files are written.

## Behaviors

1. On the untouched tree, W-6 passes: the only direct spec, `cypress/e2e/waterworks-mobile.cy.ts`, exists and has no silent-skip pattern.
2. A workflow with an active step `npx cypress run --spec cypress/e2e/<x>.cy.ts`, where the spec source contains `cy.wrap({}).log(`, makes the W-6 audit report `cypress/e2e/<x>.cy.ts`.
3. Behavior 2 holds equally for the `"…"`, `'…'`, bare, `--spec=…` and `-s …` forms.
4. Behavior 2 holds for a skipping spec at any position of a comma-separated `--spec` list.
5. A direct `--spec` that names a path not on disk is reported as missing.
6. An active direct `cypress run` line with no `--spec` is reported.
7. A `cypress run --spec` line that is commented out inside a `run:` block is not counted.
8. W-6 no longer forbids the CI shell spec named at `package.json:30`; putting `cypress/e2e/smoke.cy.ts` on a direct `--spec` line is allowed, because that spec passes the audit.
9. Running W-6 and its falsification case changes no tracked or untracked file in the checkout.

## Acceptance criteria

- The diff touches exactly one file: `scripts/__tests__/ciWiring.test.ts`.
- The literal `'--spec "cypress/e2e/smoke.cy.ts"'` no longer appears in `scripts/__tests__/ciWiring.test.ts`.
- The W-6 title states the new property (direct `cypress run` specs exist and have no silent-skip pattern) and says nothing about `smoke.cy.ts`.
- The falsification `it` asserts that the violations list is non-empty and names the injected path for each of the five spec forms and for the comma list. A stubbed audit that always returns `[]` would therefore fail it.
- The falsification `it` asserts that the commented-out case gives no violations.
- The falsification `it` writes no files: no `writeFileSync`, `mkdtempSync` or other fs write call is added.
- W-1 to W-5 and W-7 to W-11 are byte-identical to before.
- No `.skip`, `.only`, `skipIf`, timeout change or ignore comment is added anywhere in the diff.
- `npx vitest run --project unit scripts/__tests__/ciWiring.test.ts -t "W-6"` passes.
- `npm run lint` and `npm run test:unit` are green, apart from any failure an unchanged test already shows on the untouched tree (see Risks).
- Prettier is applied to `scripts/__tests__/ciWiring.test.ts` only.

## Decisions

- **Rewrite W-6 rather than delete it.** Deleting fixes the false claim but also drops the only guard on direct workflow invocations, which the `:125` comment says step-presence does not cover. `ci-cd.yml:121` shows that route is in use.
- **Use a derived property instead of swapping in the new filename.** Rejected: forbidding `cypress/e2e/ci-shell.cy.ts` or some other literal would repeat the defect. A literal about a file goes stale when files are renamed, which is exactly how W-6 inverted after #671.
- **Guard against skipping specs, not duplicated spec paths.** Rejected: "the workflow never names the `test:e2e:ci` spec path directly". It is keyed on the current path, so it misses the historical failure (the workflow naming some other skipping spec). It only catches a duplicate run of an already-audited spec, which is harmless.
- **Parse YAML rather than raw text.** A raw-text check gets comments wrong in both directions, and the step-presence core already set the "active commands only" rule for this repo (`verify-ci-step-presence-core.mjs:5-7`).
- **Reuse `normalizeRunLines` rather than copying it,** so both guards agree on what an active line is. Load it with a dynamic `import()` of a file URL, matching `verify-ci-step-presence.test.ts:63-69`. Rejected: a static `.mjs` import, which would be a new pattern in `scripts/__tests__`.
- **Cover only direct `cypress run` lines, not `npm run <script>` indirection.** W-3 already covers `npm run test:e2e:ci`. Resolving npm scripts would widen W-6 into a second W-3 and pull in `test:e2e:ci:flows`, which is outside this finding (see Open questions).
- **Apply only the silent-skip audit to direct specs, not W-4's `Cypress.env(` ban.** W-4 reflects the no-env shell smoke. Direct invocations can legitimately appear in env-provisioned jobs, and adding that ban would be a new policy, not a fix.
- **Fail closed on spec values the audit cannot resolve** (a `$VAR`, a glob, a missing file, or no `--spec`). Rejected: skipping them silently, which is the "can never fail" shape this finding is about. Today's workflow has none of these, so this is green.
- **Include the `-s` short flag.** Cypress accepts `-s` for `--spec`; leaving it out would let the audit be bypassed.
- **Sabotage in memory with a stub reader,** not with a temp workflow file or a planted spec. Nothing needs to reach disk, and the repo's guard rule is never to write the checkout (`docs/ops/guards.md:23-42`).
- **Leave `.github/workflows/ci-cd.yml` unchanged.** Moving the Waterworks step behind an npm script would be tidier, but that path is off-limits to this harness and the fix does not need it.
- **Leave docs unchanged.** No document refers to ciWiring's W-6; the `W-06` in `docs/ops/guards.md:19` is the unrelated Storybook guard.

## Open questions

- Which invariant should W-6 hold? This package assumes "the workflow cannot point Cypress directly at a skipping spec", inferred from the #677 prompt log. If the maintainers meant something else, redirect with `/harness revise`: for example, a single source for the shell spec path, or deleting W-6 outright.
- Nothing checks the four specs run by `test:e2e:ci:flows` (`package.json:31`, via `ci-cd.yml:322`) for the silent-skip pattern. Should that become its own work item?
- `.github/workflows/cypress-staging.yml:36` points at `cypress/e2e/pilot-smoke.cy.ts`, which does not exist at that path; it is under `cypress/e2e/legacy/`. `docs/ops/ci.md:143` already records this as a follow-up. It is outside this package and under `.github/`, so a person would have to carry it.
- No gate was run while preparing this proposal, because no shell was available. `gate_expectation: green` is a prediction: the change is one test file, and its new assertions are pure (no spawn, no network). It is not an observation of the baseline.

## Touched paths

- scripts/__tests__/ciWiring.test.ts

## Risks

- **Same-file environment tests.** `ciWiring.test.ts` also holds W-8/W-9/W-11 (`:150-192`), which boot Vite on `:5173` and need the Cypress binary, and W-10 (`:196-220`). If the implementation environment lacks either, `npm run test:unit` can go red for reasons unrelated to this change. The reviewer should compare against the untouched tree before blaming W-6. Those tests are not modified.
- **Spec extraction.** The regex that pulls out `--spec` / `-s` values is the part most likely to be subtly wrong. The reviewer should check that the falsification case covers every form listed in Behaviors 3-7, and that an unresolvable value counts as a violation rather than being dropped.
- **Stricter than before.** The new W-6 is stricter than the old one: a future direct `cypress run` with a glob or `$VAR` spec will be reported. This is deliberate and recorded under Decisions, but it may surprise whoever next edits the workflow.
- **Inferred history.** The account of how the literal inverted comes from `prompts/*.md`, not from commits. If git history shows a different origin, the Approach still holds but the Diagnosis narrative should be corrected.
- **Issue body.** It contains no instructions aimed at an automated agent. The command table and "What happens next" text are harness boilerplate for trusted humans, and nothing in it was acted on.
