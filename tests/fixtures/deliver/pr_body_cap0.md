<!-- opened by the Bright Bots Harness; generated from the review package (B108) -->
Closes #633

Opened by the **Bright Bots Harness** from `jgoetzmann-bot`. It cannot merge this and will not push again unless you ask it to. Work item: [jgoetzmann/bright-bots-harness#4](https://github.com/jgoetzmann/bright-bots-harness/issues/4).

Review requested from @BrightBoost-Tech, @jgoetzmann.

## Steering it from here

Put one of these on its own line in a comment on this pull request:

| comment | what happens |
|---|---|
| `/harness revise <notes>` | one more implementation pass, your notes as the brief |
| `/harness rebase` | rebase onto this repository's current `main`, then push again |
| `/harness stop` | close this and park the work item; `/harness go` puts it back |
| `/harness status` | usage, queue, and when the next thing happens |

Honoured only from @BrightBoost-Tech, @jgoetzmann, and only when GitHub also reports you as an owner, member or collaborator here, or `.harness/trust.txt` vouches for your exact account. Everyone else's comments are read and ignored. A command is acted on once — editing a comment does not re-fire it, so post a new one.

## If the checks are not running

GitHub holds workflow runs from an account with no merged contribution here, so the first pull request from this one needs a maintainer to press **Approve and run** in the checks list on this page. Later ones start on their own.

## Review checklist

From `CONTRIBUTING.md`. The harness ran the first three itself, on this branch:

- [x] Does it build? — `npm run build`, on this branch
- [x] Does it pass lint? — `npm run lint`
- [ ] Does it pass tests? — `npm run test:unit`
- [ ] Hardcoded English rather than i18n keys — yours to check
- [ ] Mobile responsive — yours to check
- [ ] Matches existing code patterns — yours to check
- [ ] Agent prompt logged — the prompt is `prompts/implement.md` in the harness repository, pinned by content hash; the full transcript is `transcript.jsonl` in the review package

<details>
<summary>What this change is, and why</summary>

# Diagnosis

`scripts/check-bundle-size.js:42` globs `dist/*.js` and never sees `dist/assets/*.mjs`, so the
measured total is wrong on every esm build.

</details>

<details>
<summary>Gate results — this repository's own sequence, run on the branch</summary>

- Base commit: `0123456789abcdef0123456789abcdef01234567`
- Branch: `harness/fix-633-bundle-size`

| when | gate | exit | |
|---|---|---|---|
| Baseline | `npm run lint` | 0 | PASS |
| Baseline | `npm run test:unit` | 1 | **FAIL** |
| Post-change | `npm run lint` | 0 | PASS |
| Post-change | `npm run build` | 0 | PASS |
| Post-change | `npm run test:unit` | 1 | **FAIL** |

#### Baseline: npm run test:unit — exit code 1
stdout (verbatim tail):

```text
1 failing: a flaky clock test, red before any change
```

## Post-change — the branch as packaged

#### Post-change: npm run test:unit — exit code 1
stdout (verbatim tail):

```text
1 failing: a flaky clock test, red before any change
```

</details>

<details>
<summary>The review package, verbatim</summary>

# Review package - item 4

A fixed package used only to pin `build_pr_body`'s output (D70, T7).

- Branch: `harness/fix-633-bundle-size`
- Base commit: `0123456789abcdef0123456789abcdef01234567`

</details>

<details>
<summary>Rebuild this exact tree yourself</summary>

Base commit `0123456789abcdef0123456789abcdef01234567` exists here in `Bright-Bots-Initiative/brightboost`; the branch `harness/fix-633-bundle-size` on `jgoetzmann-bot/brightboost` was rebased onto the fork's main, which is a fast-forward of this repository (B105).

```bash
git -c core.autocrlf=false clone https://github.com/Bright-Bots-Initiative/brightboost.git r && cd r
git checkout 0123456789abcdef0123456789abcdef01234567
git fetch https://github.com/jgoetzmann-bot/brightboost.git harness/fix-633-bundle-size
git checkout FETCH_HEAD
```

With the review package on disk instead (`docs/PACKAGE-FORMAT.md` §3):

```bash
git -c core.autocrlf=false clone https://github.com/Bright-Bots-Initiative/brightboost.git r && cd r
git checkout "$(cat ../BASE)"
git am ../patches/*.patch
```

Or from the bundle, with no network at all:

```bash
git bundle verify bundle.gitbundle
git clone bundle.gitbundle -b harness/fix-633-bundle-size r
```

</details>

<details>
<summary>About the harness</summary>

---

Posted by the **Bright Bots Harness**, an automated agent that turns issues on [`Bright-Bots-Initiative/brightboost`](https://github.com/Bright-Bots-Initiative/brightboost) into reviewable pull requests.

It never merges anything: a person approves the plan by merging the proposal, and the change by merging the delivery. A committed kill switch stops it.

**Where things are.** [Start here](https://github.com/jgoetzmann/bright-bots-harness/blob/main/docs/FOR-MAINTAINERS.md) · [Every command](https://github.com/jgoetzmann/bright-bots-harness/blob/main/docs/COMMANDS.md) · [What it will and will not do](https://github.com/jgoetzmann/bright-bots-harness/blob/main/docs/SAFETY.md) · [When something is wrong](https://github.com/jgoetzmann/bright-bots-harness/blob/main/docs/OPERATIONS.md) · [What is in a review package](https://github.com/jgoetzmann/bright-bots-harness/blob/main/docs/PACKAGE-FORMAT.md)

**Repositories.** harness [`jgoetzmann/bright-bots-harness`](https://github.com/jgoetzmann/bright-bots-harness) · fork [`jgoetzmann-bot/brightboost`](https://github.com/jgoetzmann-bot/brightboost) · product [`Bright-Bots-Initiative/brightboost`](https://github.com/Bright-Bots-Initiative/brightboost)

</details>