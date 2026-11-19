# Plan: chore(ci): wire or delete orphaned scripts/check-todos.sh

- For: https://github.com/jgoetzmann/bright-bots-harness/issues/175
- Answers: https://github.com/Bright-Bots-Initiative/brightboost/issues/766
- Written by claude-opus-5-5 on claude-day, against `main`

## Summary
Delete `scripts/check-todos.sh` and remove its row from `docs/ops/guards.md`. Don't wire it up. Nothing has run it since 2025-05-31, when its step was taken out of `pr-review-bot.yml`. It also doesn't work: it reports `success` for any TODO in a file below the repository root, and it exits 0 even when it does find one. Wiring it into CI would mean editing `.github/workflows/`, which the bot is not allowed to do, and the script would have to be rewritten before it could guard anything.

## Diagnosis
- **No runner.** `git grep -nI check-todos` on `main` finds only `docs/ops/guards.md:23` (the "None today" row), `prompts/2026-08-20-ticket-738-guard-registry.md:28` (a historical log) and the script itself (`scripts/check-todos.sh:8`). No npm script in `package.json` calls it, and no workflow does either.
- **The orphaning was deliberate.** The script arrived with #37 (`451337b5`) as a `pr-review-bot.yml` step (`bash scripts/check-todos.sh "<base sha>" "<head sha>"`, with `continue-on-error: true`). Commits `ce859a98` and `c68314a9` (both 2025-05-31) removed that step and the comment section that read its `todos_status` / `todos_output`.
- **It would not guard anything even if it were wired.** I ran it locally:
  - `scripts/check-todos.sh:23` builds `sed -e "s/^/[NEW] $file: /"` with the file path inside the expression. Any path containing `/` breaks the `s` command (`sed: -e expression #2, char 15: unknown option to 's'`). Nothing gets written to the temp file, so lines 32–35 report `todos_status=success`. Running it on `2ba6ae40^..2ba6ae40`, which adds `// TODO: translate Great Work…` under `src/components/games/greatWork/`, printed that sed error three times, wrote `todos_status=success` and exited 0. Only files at the repository root are ever detected.
  - `scripts/check-todos.sh:39` always runs `exit 0`, so findings never fail a step. They only show up through `$GITHUB_OUTPUT`, which nothing reads any more.
  - `scripts/check-todos.sh:4,8-9`: run locally without `GITHUB_OUTPUT`, the script appends to `""` and fails with `: No such file or directory`.
  - `scripts/check-todos.sh:28,30`: `s/\n/%0A/g` does nothing in line-based `sed`, and `tr -d '\n'` joins all findings onto one line.
- **The registry's "proof" doesn't test the property.** `docs/ops/guards.md:23` gives the proof as "`bash scripts/check-todos.sh` (omit SHAs) → exit 1". That only shows the argument check at lines 6–10 works. It says nothing about whether TODOs are detected.
- **No TODO policy is in force.** The tracked JS/TS tree has 11 `TODO:`/`FIXME:` lines today, and `2ba6ae40` added one recently. Making the check fail PRs would be a new policy, not a restored one.
- **The registry rules call this decoration.** `docs/ops/guards.md:9-11`: "A row without a real Runner is decoration."

## Approach
Take the issue's option 2: delete the script and remove the references that only exist because of it.

Options I rejected:
- **Wire it into CI.** This needs a change under `.github/workflows/`, which the bot cannot make. It would also need a rewrite: fix the sed delimiter, exit non-zero on findings, work without `GITHUB_OUTPUT`, and add a two-phase sabotage proof. On top of that, someone would have to decide that new TODOs block a PR, which is a policy call the repo has not made.
- **Wire it to an npm script only.** It would still have no CI runner, which is the class of problem `guards.md:9-11` describes. It would also give a false green on almost every real diff.
- **Fix the script and keep it unwired.** It would stay orphaned, so it fails the issue's acceptance item ("either an executable guard or gone").

The W-16 inventory (`docs/ops/guards.md:196`) still holds after the deletion. I checked every `scripts/verify-*.{sh,mjs}` and `scripts/check-*.sh`: each has a row or a justified omission, and `check-prisma-drift.sh` becomes the only `check-*.sh`. `scripts/docs-check.mjs` only flags references to deleted paths listed in its own `DELETED_PATHS` set (#753 docs), and both remaining mentions of the script are in backticks, not links. So the deletion triggers no DC-002 or DC-003 finding.

## Steps
1. `git rm scripts/check-todos.sh`.
2. In `docs/ops/guards.md`, delete the `scripts/check-todos.sh` table row (line 23).
3. In `docs/ops/guards.md`, add one sentence after line 7 ("The #720 row was added on 2026-10-14; …") following that pattern, for example: "The `scripts/check-todos.sh` row was removed on <build date> (#766): the script was deleted. It had had no runner since its PR Review Bot step was dropped on 2025-05-31, and it reported success for any file below the repository root." Do not bump the header's `Last verified against code` date, because the other rows were not re-verified (the same convention as the #720 note).
4. Run `npx prettier --write docs/ops/guards.md` and accept any table realignment. The Runner column of the deleted row may have set a column width.
5. Leave `prompts/2026-08-20-ticket-738-guard-registry.md` unchanged. It is a dated historical log, and rewriting it would falsify the record.
6. Add a prompt log `prompts/<build-date>-check-todos-removal.md`, per `docs/agents/rules/00-core.md` and `prompts/README.md` ("anything that produced a commit"). Record the sed false-green evidence and the 2025-05-31 removal commits.
7. Commit as `chore(ci): delete orphaned scripts/check-todos.sh` with `Refs #766`.

## Tests
No new automated test. The change removes an unwired script and a doc row, so no behaviour is left to test. A test asserting the file is absent would pass trivially and prove nothing, and `docs/agents/rules/10-testing.md` says not to write Red-Green tests for docs changes. The checks that do prove something:
- `git grep -nI "check-todos" -- ':!prompts/'` prints nothing. Before the change it prints `docs/ops/guards.md:23` and `scripts/check-todos.sh:8`.
- `for f in scripts/verify-*.sh scripts/verify-*.mjs scripts/check-*.sh; do grep -q "\`$f\`" docs/ops/guards.md || echo MISSING $f; done` prints nothing, so W-16 still holds.
- `npm run docs:check` and `npm run agent:check` report no findings, and `npm run format:check` passes on the changed files.
- The standard gates: install, `npx prisma generate`, `npm run lint`, `npm run typecheck`, backend typecheck, `bash scripts/check-prisma-drift.sh`, `npm run test:unit`, `npm run build`.

## Acceptance criteria
- `scripts/check-todos.sh` no longer exists in the tree.
- `docs/ops/guards.md` has no `scripts/check-todos.sh` row, and it has a dated sentence saying the row was removed under #766 and why.
- `git grep -nI "check-todos"` finds hits only under `prompts/`.
- Every `scripts/verify-*.{sh,mjs}` and `scripts/check-*.sh` is still a row or a justified omission in `docs/ops/guards.md`.
- `npm run docs:check`, `npm run agent:check` and `npm run format:check` are clean, and all required checks pass.
- No file under `.github/` or any other protected path is changed.

## Touched paths
- scripts/check-todos.sh (deleted)
- docs/ops/guards.md
- prompts/<build-date>-check-todos-removal.md (new)

## Risks
- **Losing a feature someone wants.** If the owners actually want TODO/FIXME reporting on PRs, deleting this loses nothing that works. The script cannot detect TODOs outside the repository root, so any replacement would be written from scratch anyway. A reviewer can re-check this by running `GITHUB_OUTPUT=$(mktemp) bash scripts/check-todos.sh 2ba6ae40^ 2ba6ae40` on `main`.
- **Prettier churn.** Removing the row may realign the whole table in `docs/ops/guards.md`. The reviewer should confirm the diff contains only whitespace changes besides the removed row and the new sentence.
- **Untrusted text.** The upstream issue thread asks for GitHub issue re-parenting (#709 → #739) and a note on #709. Those are requests to a person about issue structure. The bot can't do them because it has no `gh` or network access, and this plan doesn't attempt them. Nothing in the task text addresses an AI or asks for rules to be bypassed.

## Open questions
- **Delete or wire?** Do the owners want a working "no new TODO/FIXME" check on PRs? If yes, that is a separate task for a person: a rewritten script, a `.github/workflows/` step, a sabotage proof and a decision on whether it blocks merges. This plan assumes they don't, and deletes the script.
- **Issue linkage.** Should #766 move from #709 to #739, as its 2026-08-21 comment asks, with a note left on #709? The acceptance item "Linked from #709" depends on that answer, and only a person can make that change on GitHub.
