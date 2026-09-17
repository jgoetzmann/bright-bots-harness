---
issue: 66
upstream_issue: null
title: "fix(lambda): fail signup when the primary-schema user row is not written"
kind: fix
slices: 2
risk: medium
touched_paths:
  - "src/lambda/teacher-signup.ts"
  - "src/lambda/student-signup.ts"
depends_on: []
estimated_turns: 30
gate_expectation: green
baseline_red: []
---

# fix(lambda): fail signup when the primary-schema user row is not written

## Issue

Harness issue [#66](https://github.com/jgoetzmann/bright-bots-harness/issues/66), tracking `audit:61:1` (finding 1 of the audit in issue #61). No product-repository issue exists. The reporter's terms: "Signup swallows the primary-schema insert, then returns 201 with a token", severity high, pointing at `INSERT INTO "User"`, `catch (e) { console.error(...) }`, `users`, and `201`.

## Diagnosis

The report is accurate, and the consequence is larger than "a log line instead of an error".

Both signup lambdas write two tables. The Prisma-managed table `"User"` (`prisma/schema.prisma:32-37`; DEPLOYMENT.md:12 names Prisma the schema source of truth) is written inside a try/catch that discards every error — `src/lambda/teacher-signup.ts:262-276` and `src/lambda/student-signup.ts:260-274`. The legacy table `users` is then written unconditionally (`teacher-signup.ts:278-291`, `student-signup.ts:276-287`), and its returned row feeds `jwt.sign` (`teacher-signup.ts:300-309`) and the `201` response (`teacher-signup.ts:311-327`, `student-signup.ts:307-321`). The two writes are separate `pool.query` calls, so they are not even on the same connection, let alone in one transaction.

`"User"` is not a dead mirror. The lambda fleet is split across the two tables:

- reads/writes `"User"`: `src/lambda/profile.ts:126-129`, `src/lambda/edit-profile.ts:154-170`, `src/lambda/get-classes.ts:132-140`, `src/lambda/user-avatar.ts:126-129`
- reads/writes `users`: `src/lambda/login.ts:139-141`, `get-progress.ts:150`, `add-badge.ts:145`, `increment-streak.ts:134`, `break-streak.ts:134`

So when the swallowed insert fails, the caller gets `201` and a working token, login keeps working (it reads `users`), and every `"User"`-backed endpoint reports that the account does not exist: `profile.ts:131-137` returns 404 "User not found", `edit-profile.ts:172-178` returns 404, and `get-classes.ts:132-140` dereferences `id.rows[0].id` on an empty result, throws, and is caught into a misleading 404 "No matching courses found" (`get-classes.ts:141-147`). The only signal is one CloudWatch line.

The more serious failure is the one the duplicate check misses. The pre-existing-email check queries only the legacy table — `teacher-signup.ts:248-249`, `student-signup.ts:246-247`. A row can exist in `"User"` with no counterpart in `users`: `prisma/seed.cjs` creates users only through `prisma.user.create` (lines 142, 169, 196, 241, 312, …), and the Express backend does the same (`backend/src/routes/auth.ts:83-92`, `141-150`). Against such an email the sequence is: pre-check passes; the `"User"` insert violates `email @unique` (`prisma/schema.prisma:35`) and is swallowed; the `users` insert succeeds with the *caller's* password; `201` plus a token is returned. From then on `login.ts` authenticates that caller against `users`, while `profile.ts:126-128` and `edit-profile.ts:156-168` resolve the session by `decoded.email` against `"User"` — i.e. onto the pre-existing account, which the caller can now read and overwrite. That is a cross-schema account takeover, and it is exactly what the swallow hides.

The reverse ordering is a latent lockout too: because the writes are not atomic, a `"User"` insert that succeeds followed by a `users` insert that fails leaves an account that can never log in (`login.ts` finds nothing) and can never sign up again (the pre-check passes, the `"User"` insert now collides).

Scope note on where this runs. This is the legacy AWS Lambda path: DEPLOYMENT.md:165 states the Lambda deployment is "no longer the production path" (production is Railway + Express + Supabase), no workflow under `.github/` references `src/lambda`, and `src/lambda` is excluded from `npm run typecheck` (`tsconfig.json:41`). It is not unreachable, though — `src/services/api.ts:44-48` still accepts `VITE_AWS_API_URL` and routes `/signup/teacher` and `/signup/student` to it. The production Express routes have no dual-write and no swallow (`backend/src/routes/auth.ts:83-112`), so nothing needs changing there.

## Approach

Make the two inserts one atomic unit on a single pooled client, and stop discarding the error.

In each handler: keep `bcrypt.hash` where it is (before any transaction, so a ~300 ms cost-12 hash is not held across an open transaction); widen the duplicate-email pre-check to query `"User"` as well as `users`; then `db.connect()`, `BEGIN`, insert `"User"`, insert `users` with its existing `RETURNING`, `COMMIT`; on any error `ROLLBACK` and rethrow; `release()` the client in `finally`. `jwt.sign` and the `201` stay exactly where they are, after the commit.

No new error-mapping code is needed: the handler's existing outer catch already maps a message containing "duplicate key" to 409 (`teacher-signup.ts:331-340`) and everything else to 500, which is the right answer for a lost race and for a genuinely broken write respectively. Response bodies, headers, validation branches and the OPTIONS branch are untouched.

Alternatives rejected: dropping the legacy `users` write and making `"User"` the only store — that breaks `login.ts:139-141` and four other lambdas in the same deployment; leaving the writes sequential and merely removing the catch — that fixes the reported direction but creates the mirror lockout described above; extracting the shared logic into a new module used by both handlers — every file in `src/lambda` duplicates the 80-line `getDbConnection` verbatim, which is the directory's convention and strongly suggests per-file packaging, so a new shared import is a deployment risk on a path with no CI to catch it.

## Slices

1. `src/lambda/teacher-signup.ts` — widen the duplicate-email check to `"User"`; replace the swallowing try/catch and the two pool-level inserts with one `BEGIN`/`COMMIT` transaction on a single client, `ROLLBACK` and rethrow on failure, `release()` in `finally`; leave hashing, JWT, response bodies and status codes unchanged.
2. `src/lambda/student-signup.ts` — the same change, with the student handler's column list (`name, email, password, role, created_at, updated_at`) and `'student'` role literal.

## Behaviors

1. A signup whose `INSERT INTO "User"` fails returns an error status with no token, and leaves no row in `users`.
2. A signup whose `INSERT INTO users` fails leaves no row in `"User"`.
3. A signup for an email that exists in `"User"` but not in `users` returns 409 and writes nothing to either table.
4. A signup for an email that exists in `users` still returns 409, as today.
5. A signup that commits both rows still returns 201 with the same body shape and a token, as today.
6. A failed insert is reported through the handler's existing error path (`console.error("Teacher signup error:", …)` / `"Student signup error:"`) and the corresponding non-2xx status, not through a discarded catch.
7. Validation failures (missing body, missing field, bad email, short password) and the OPTIONS preflight return exactly what they return today, without opening a connection.

## Acceptance criteria

- Neither handler contains a catch block that logs a write error and continues; `console.error("Error inserting new user schema entry", e)` no longer appears in the repository.
- In each handler, both INSERT statements execute against one client obtained from `db.connect()`, between an explicit `BEGIN` and `COMMIT`.
- Each handler's failure path issues `ROLLBACK` and rethrows the original error; the client is released in a `finally` so no path — including the 409 early return and the rollback path — leaks a connection from the `max: 5` pool.
- `jwt.sign` and the `201` return are reachable only after `COMMIT` succeeds.
- The duplicate-email pre-check queries both `users` and `"User"` before hashing, and a hit on either returns the existing 409 body `{"error":"User with this email already exists"}`.
- `bcrypt.hash` is still called before the transaction is opened.
- Response bodies, status codes, `headers`, the OPTIONS branch, the validation branches, the `getDbConnection` function and the `tempId` construction are unchanged.
- The diff touches only the two files listed under `## Touched paths`.
- `npm run lint`, `npm run typecheck`, `backend: npm run typecheck`, `npx prisma generate`, `bash scripts/check-prisma-drift.sh`, `npm run test:unit` and `npm run build` are green.

## Decisions

- Keep writing both `"User"` and `users`, made atomic — rejected dropping the legacy write, because `src/lambda/login.ts:139-141` plus `get-progress.ts`, `add-badge.ts`, `increment-streak.ts` and `break-streak.ts` all read `users`, so signup-only accounts would become unable to log in on that deployment.
- Use one transaction on a single `PoolClient` — rejected merely deleting the catch and leaving two `pool.query` calls, because a `users` failure after a `"User"` success produces an account that cannot log in and cannot re-register.
- Let failures surface through the handler's existing outer catch — rejected inventing a new status or a "partial signup" response, which would change the API contract without helping the caller.
- Widen the duplicate-email pre-check to `"User"` — rejected relying only on the unique-constraint error, because the cross-schema collision is the account-takeover path and deserves an explicit check rather than message sniffing in the outer catch.
- Hash before opening the transaction — rejected checking for duplicates inside the transaction, which would hold a pooled connection open across a cost-12 bcrypt hash on a pool capped at 5.
- Keep `tempId = ${email}-${random}` — rejected switching to `crypto.randomUUID()` or a cuid: the id is unique whenever the email is unique, and changing its shape alters rows other tools key on without fixing the reported defect.
- Fix both handlers in one package — rejected splitting into two items; the defect and the fix are identical and splitting doubles review for no isolation.
- Leave the `"User"` insert's column list as-is — rejected also writing `school`/`subject` (teacher) and `level`/`accountMode` (student), which is a separate data-completeness defect; recorded under `## Open questions`.
- Keep each handler self-contained — rejected extracting a shared helper module, because every file in `src/lambda` duplicates `getDbConnection` verbatim and a new cross-file import could break whatever per-file packaging deploys these, which no CI here would catch.
- Ship no new test file — the harness's proposal schema only accepts touched paths that already exist, so this package cannot add one; and a behavioural test would need `pg` and `@aws-sdk/client-secrets-manager`, which are declared only in `src/lambda/package.json` and are absent from the root `package-lock.json`, so it would also require aliasing the shared `vitest.config.ts`. Rejected adding those devDependencies or global aliases for a legacy path; a follow-up item is proposed under `## Open questions`.

## Open questions

- Should the legacy Lambda API be retired rather than repaired? DEPLOYMENT.md:165 calls it "no longer the production path" and defers removal to a follow-up; this fix is worth doing only while `src/services/api.ts:44-48` still accepts `VITE_AWS_API_URL` as a base.
- After this change, a signup against a database whose `"User"` table is missing or column-mismatched returns 500 where it previously returned 201. Is any environment still in that state? `getDbConnection` creates `users` if absent (`teacher-signup.ts:85-112`) but never creates `"User"`, which is managed by `prisma migrate deploy`.
- The lambda-created `"User"` row is thinner than the Express-created one: no `school`/`subject` for teachers, and no `level`/`accountMode` for students, so an email/password student gets `accountMode = CLASS_CODE_ONLY` (`prisma/schema.prisma:49`). Separate work item?
- The two tables disagree on role case — `'teacher'`/`'student'` in `"User"` versus `'TEACHER'`/`'STUDENT'` in `users`, and the JWT carries the upper-case one. Separate work item?
- `src/lambda/user-avatar.ts:97` and `:128` read `decoded.userId`, which neither `login.ts:167-176` nor either signup handler ever sets, so that endpoint appears to 401 unconditionally. Observed while tracing the token, not touched here — worth its own item?
- Nothing in CI typechecks, builds or tests `src/lambda` (`tsconfig.json:41`; no `.github/` workflow references it). Do maintainers want a follow-up to add a lambda test harness, given it needs either those two devDependencies at the root or vitest aliases?

## Touched paths

- src/lambda/teacher-signup.ts
- src/lambda/student-signup.ts

## Risks

- **Behaviour change by design.** Signups that today return 201 against a database where the `"User"` insert fails will start returning 409 or 500. That is the point of the fix, but it is a live-traffic change on any deployment still pointed at the Lambda API, and it is the thing to weigh before merging.
- **Connection leaks.** The reviewer should look hardest at client release: every path, including the 409 early return and the rollback path, must reach `release()`. A leak exhausts the `max: 5` pool and turns into 503s under `connectionTimeoutMillis: 25000`.
- **Rollback masking the real error.** `ROLLBACK` on a broken connection can itself throw; it must not replace the original error, or the outer catch's duplicate-key → 409 mapping stops working.
- **Severity depends on co-tenancy.** The takeover scenario requires the Lambda API and a Prisma-seeded/Express-written database to be the same database. The `"User"`, `"Course"."teacherId"` and `"updatedAt"` references throughout `src/lambda` say these handlers target a Prisma-migrated database, but I could not verify from the repository that any environment has both writers. If they never coexist, the impact reduces to the half-created account and the misleading 404s.
- **No CI coverage and out-of-band deployment.** Nothing under `.github/` builds or deploys `src/lambda`, so a reviewer cannot assume the pipeline exercises this change; verification is manual against a staging API Gateway. This package also adds no test, for the reasons recorded under `## Decisions`.
- **Gates were not run in this stage.** I have no shell here, so `gate_expectation: green` and `baseline_red: []` are read off the configuration (`tsconfig.json:41` excludes `src/lambda` from typecheck; `eslint.config.js:11` lints it; `vitest.config.ts` collects no lambda tests), not measured.
- **Instructions inside repository content.** The issue body is a harness tracking issue quoting an audit finding, and it embeds a table of `/harness …` commands and links. None of it is addressed to an implementing agent and I acted on none of it; it is reproduced in the prompt as data. Nothing in it asked to bypass a gate or touch an unrelated path.
