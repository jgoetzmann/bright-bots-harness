---
issue: 68
upstream_issue: null
title: "test(auth): assert signup status so email-lowercasing check cannot pass vacuously"
kind: test
slices: 1
risk: low
touched_paths:
  - "backend/src/routes/security_validation.test.ts"
depends_on: []
estimated_turns: 12
gate_expectation: green
baseline_red: []
---

# test(auth): assert signup status so email-lowercasing check cannot pass vacuously

## Issue

Harness issue [#68](https://github.com/jgoetzmann/bright-bots-harness/issues/68), tracking `audit:61:3` (finding 3 of audit [#61](https://github.com/jgoetzmann/bright-bots-harness/issues/61)). No product-repository issue number. Severity as filed: high. Path as filed: `backend/src/routes/security_validation.test.ts:70`.

In the reporter's terms: "the email-lowercasing check runs zero assertions and still reports green whenever the route returns anything other than 201, which is exactly the regression it exists to catch."

## Diagnosis

The shape the reporter describes is real, but the severity is latent rather than active, and one premise written into the file itself is stale. Both matter for what the fix should say.

**The guard.** `backend/src/routes/security_validation.test.ts:70-83` is the whole assertion body of `should lowercase email on signup`:

```
70  if (response.status === 201) {
71    expect(prismaMock.user.create).toHaveBeenCalledWith(...)
78  } else {
79-82  // three comment lines
83  }
```

The `else` body at lines 79-82 contains only comments — no statement, no assertion, no failure. Vitest fails a test only on a thrown assertion, so any response status other than 201 takes a branch that executes nothing and the test reports green. This is the only assertion in the test; there is no `expect.assertions(n)` and no global `expect.hasAssertions()` (the root setup file is `src/test/setup.ts`, referenced at `vitest.config.ts:18`, and nothing in the file registers an assertion-count guard).

**It is not vacuous today.** The route does return 201 for this request, so the assertion at lines 71-77 currently runs. Tracing `POST /api/signup/student` (`backend/src/routes/auth.ts:66-121`) with the test's mocks:

- `studentSignupSchema.parse` (`auth.ts:71`) accepts the payload. `nameSchema` and `passwordSchema` (`auth.ts:36-42`) are satisfied by `"Test User"` / `"Password123"`. No `ZodError`, so no 400.
- `authLimiter` is 20 requests / 15 min (`backend/src/utils/security.ts:9-15`); this file issues two auth requests. No 429.
- `backend/src/utils/prisma.ts:33` constructs `new PrismaClient(...)`, and the test's hoisted `@prisma/client` mock (`security_validation.test.ts:22-30`) returns `prismaMock` from the constructor, so the route's `prisma` *is* `prismaMock`. `user.findUnique` resolves `null`, so no 409 at `auth.ts:77`.
- `logAudit` (`backend/src/utils/audit.ts:9-26`) is try/caught and awaits the mocked `auditLog.create`; `generateToken` (`backend/src/utils/token.ts:36-43`) falls back to `"default_dev_secret"` when `SESSION_SECRET` is unset. Neither can throw a 500.
- `auth.ts:108` returns 201.

So the correct statement of the defect is **vacuous-on-regression**, not vacuous-now: the test asserts nothing precisely on the runs where something has gone wrong. Every realistic future change that flips the status — tightening `passwordSchema` (400), a leaked non-null `findUnique` default (409), lowering `authLimiter` (429), any throw in the route (500) — silences this test instead of failing it.

**The stale premise.** Lines 80-82 tell a future reader `"current code does NOT lowercase. So this test should FAIL expectation if we run it now."` That is no longer true. `emailSchema` at `backend/src/validation/schemas.ts:27-31` is `z.string().email().max(255).toLowerCase()`, and `auth.ts:71` parses through it before both the `findUnique` lookup (`auth.ts:73-75`) and the `create` call (`auth.ts:83-92`). Normalization is implemented. The conditional is leftover TDD scaffolding from a period when it was not, and the comments now actively mislead.

**Corroboration that 201 is the right thing to pin.** Two sibling suites use the identical hoisted-mock shape against the same route and assert 201 unconditionally: `backend/src/routes/auth.test.ts:53` and `backend/src/routes/security_xss.test.ts:76`. Whatever makes those two green makes an unconditional `toBe(201)` green here.

The defect is exactly where the reporter said it was. Nothing in the product code needs to change.

## Approach

Replace the conditional with two unconditional assertions in the order a reader wants them to fail: pin the status first, then the normalized email.

```
expect(response.status).toBe(201);
expect(prismaMock.user.create).toHaveBeenCalledWith(
  expect.objectContaining({ data: expect.objectContaining({ email: "student@test.com" }) }),
);
```

The `else` branch and the three stale comment blocks (lines 67-69 and 79-82) are deleted and replaced by one line stating the invariant the test defends: mixed-case input must reach `prisma.user.create` already lower-cased. Status-first ordering matters — if the route starts returning 400, the failure message reads "expected 400 to be 201" rather than the much less legible "number of calls: 0" from a mock matcher.

Why this and not the alternatives: `expect.assertions(1)` would catch the silence but not tell the reader *why* the route failed, and it is a test-framework trick where a plain assertion on the real observable is clearer. `else { expect.unreachable() }` reaches the same end with a branch that can now never execute. Keeping the conditional and adding a status assertion outside it leaves dead code behind. Asserting `findUnique` was also called with the lower-cased email is redundant: both call sites read the same parsed `data.email`, so removing `.toLowerCase()` fails the `create` assertion already.

Product code is not touched. The route is correct; only the test that guards it is unsound.

## Slices

1. In `backend/src/routes/security_validation.test.ts`, replace the `if (response.status === 201) { … } else { … }` block at lines 67-83 of `should lowercase email on signup` with an unconditional `expect(response.status).toBe(201)` followed by the existing `prismaMock.user.create` email assertion; delete the comment-only `else` branch and the stale TDD notes, and leave one comment naming the invariant.

## Behaviors

1. `should lowercase email on signup` fails when `POST /api/signup/student` returns any status other than 201 for a valid mixed-case payload.
2. `should lowercase email on signup` fails when `prisma.user.create` is called with an email that is not fully lower-cased.
3. The test evaluates at least two assertions on every run, whatever status the route returns — there is no execution path through it that asserts nothing.
4. `npm run test:unit` still passes against unmodified product code, because `emailSchema` already lower-cases.
5. Removing `.toLowerCase()` from `backend/src/validation/schemas.ts:31` makes this test fail rather than pass.

## Acceptance criteria

- `backend/src/routes/security_validation.test.ts` contains no `if (response.status` guard wrapping an assertion, and no `else` branch whose body is only comments.
- `expect(response.status).toBe(201)` appears in `should lowercase email on signup` and precedes the `prismaMock.user.create` assertion.
- The `prismaMock.user.create` assertion still pins `email: "student@test.com"` against the mixed-case input `"Student@Test.com"` sent at line 63.
- The comments claiming "current code does NOT lowercase" and "this test should FAIL expectation if we run it now" are gone.
- The diff touches exactly one file, adds no dependency, and introduces no `.skip(`, no `.only(`, no `continue-on-error`, no timeout change, and no lint-ignore comment.
- `npm run lint`, `backend: npm run typecheck` and `npm run test:unit` are run during implementation and their real output is reported.
- No file under `.github/`, `prisma/`, `migrations/` or `backend/scripts/predeploy*` appears in the diff.

## Decisions

- Assert `toBe(201)` rather than `expect.assertions(1)` — rejected the assertion-count guard because it reports "1 assertion expected, 0 received" without naming the status that actually came back, which is the fact a reviewer needs.
- Assert the status before the mock call — rejected the reverse order because a non-201 response means `create` was never called, and "number of calls: 0" is a worse first failure message than "expected 400 to be 201".
- Delete the `else` branch outright — rejected `else { expect.unreachable() }` and `else { throw new Error(...) }` because once the status is pinned the branch is unreachable dead code, and the smallest honest change removes it.
- Do not add a second assertion that `findUnique` was called with the lower-cased email — rejected as redundant: `auth.ts:73-75` and `auth.ts:83-92` both read the same post-parse `data.email`, so one regression fails both. A redundant assertion is not the smallest change that fixes the defect.
- Do not change product code — rejected touching `schemas.ts` or `auth.ts` because `emailSchema` at `schemas.ts:27-31` already implements normalization correctly; the defect is entirely in the test's control flow.
- Leave `should lowercase email on login` (lines 86-109) alone — rejected folding it in because it is not vacuous: its `toHaveBeenCalledWith` fails if the route short-circuits before the lookup, so it has no silent-pass path. Raised under Open questions instead.
- Leave `should reject timeSpentS > 86400` (lines 113-127) alone — rejected uncommenting line 126 because line 125 is already a real, unconditional assertion; that test has no silent-pass path and is outside the cited finding.
- Leave the unused `response` binding at line 96 of the login test alone — rejected the tidy-up as unrelated churn in a diff that a reviewer should be able to read as one idea.
- Scope the commit `test(auth)` with type `test` — `commitlint.config.cjs:1` extends `@commitlint/config-conventional` with no `scope-enum`, so the scope is unconstrained; `auth` names the route under test more usefully than `backend`.

## Open questions

- Should `should lowercase email on login` (lines 86-109) also pin its response status? It is not vacuous today — its mock-call assertion fails if the route short-circuits — but it would pass on a 401 or 429 that happened to still reach `findUnique`. Left out to keep the diff to the cited line; a reviewer who wants it folded in should say so on this proposal rather than on the delivery.

## Touched paths

- `backend/src/routes/security_validation.test.ts`

## Risks

- **No gate was run while preparing this plan.** This environment has no shell: `npm` and `git` require interactive approval, and `node_modules` is absent at the repo root, in `backend/` and in `shared/`. I did not run `npm run test:unit`, `npm run lint`, or any other gate, and I am not reporting output for any of them. The `gate_expectation: green` in the proposal block is a prediction derived from reading `auth.ts`, `schemas.ts`, `security.ts`, `token.ts`, `audit.ts` and `vitest.config.ts`, corroborated by two sibling tests that assert 201 under an identical mock (`auth.test.ts:53`, `security_xss.test.ts:76`). `baseline_red` is `[]` on the same basis — asserted from reading, not measured. The implementation run must execute the gates and report their real output; if `npm run test:unit` is already red on the untouched tree, that is a fact this plan does not know and the delivery must state it.
- This change converts a latent silence into a visible failure, which is the point, but it means any pre-existing flakiness in reaching 201 will now surface as a red instead of a green. The most plausible source would be `authLimiter` state bleeding across files if vitest isolation were disabled; `backend/src/routes/auth_limit.test.ts` exercises the 429 path in a separate file, so the reviewer should confirm the full suite is green rather than just this one file.
- Under the root `unit` project these backend tests run in `jsdom` (`vitest.config.ts:17`), not the `node` environment that `backend/vitest.config.ts` would give them. That is pre-existing and unchanged by this diff, but it means "passes when run from `backend/`" is not the same evidence as "passes under `npm run test:unit`". The delivery should run the root gate.
- The file is not in the coverage `include` list (`vitest.config.ts:35-40`), so the 90% thresholds at lines 47-52 are not affected in either direction.
- **On the issue body.** It ends with the harness's own template — a table of `/harness …` commands, a trust list, and repository links. Those are directed at the harness's comment parser, not at me, and I did not treat any of them as instructions. Beyond that template the body contains no text addressed to an AI and no attempt to redirect behaviour, disable a check, or reach a path outside the one it names. The only substantive content is the quoted audit finding, which I treated as a claim to verify against the source — and it checks out, with the one correction recorded under `## Diagnosis`: the test is vacuous on regression, not vacuous today, and the file's own comments about normalization being unimplemented are stale.
- What to look hardest at in the delivery: that `expect(response.status).toBe(201)` precedes the `create` assertion, that the `objectContaining` matcher still pins the `email` field (weakening it to `expect.anything()` or dropping the nested `data` would restore the silence in a subtler form), and that only the one test file appears in the diff.
