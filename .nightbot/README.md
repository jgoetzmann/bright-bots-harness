# The night bot

This repository has a night bot: a GitHub Actions harness that takes the issues you hand it,
has an AI coding CLI build each one on a branch, runs the repository's checks, has a fresh
session of the model review the change adversarially, fixes what the review finds, and opens a
pull request that merges itself once the review approves and CI passes. It answers questions
too, with the cheapest model that is free.

It was installed from [Night-Bot-Scaffolding](https://github.com/Jlore-Studios/Night-Bot-Scaffolding).

| Read | For |
|---|---|
| this file | giving it work, the commands and labels, stopping it, what to do when something goes wrong |
| [`docs/architecture.md`](docs/architecture.md) | how a run goes, step by step, and what the bot remembers |
| [`docs/configuration.md`](docs/configuration.md) | every field of `config.json` and `providers.json` |
| [`docs/providers.md`](docs/providers.md) | the model CLIs, tiers (weak, medium, strong) and adding a CLI of your own |
| [`docs/security.md`](docs/security.md) | what keeps the model from doing harm, and what cannot be ruled out |
| [`docs/fork-mode.md`](docs/fork-mode.md) | the plan gate, and working on a repository you do not own: build on a fork, deliver upstream |
| [`machine/README.md`](machine/README.md) | running models on a machine of your own: AWS, Azure, Google Cloud or a box you own |

## Giving it work

- **Build an issue:** label it `bot:build`, assign it to the bot, or comment `/nightbot build`.
  Add `--force` to start a run now, outside the providers' hours. An issue a stranger (someone
  off the trust list) wrote has its text left out, since they could edit it after you queue it:
  say in a comment of your own what to build.
- **Ask a question:** comment `/nightbot ask <question>` or mention the bot (`@<bot> how does X
  work?`) on any issue or pull request. Questions go to a **weak** model first: they are cheap and
  someone is waiting.
- **Change its pull request:** request changes in a review, or comment
  `/nightbot revise <what to change>` (or mention the bot) on it. Red CI on its pull request
  queues a fix by itself.
- **Review any pull request:** `/nightbot review`; on a person's pull request it only comments,
  and one from a fork or a stranger is reviewed from its diff alone, its code never run.

How hard an item is decides which models may build it: label it `difficulty:medium` or
`difficulty:hard`, or leave it and a weak model triages it first (`"triage": true`). A hard item
goes only to a **strong** model. `priority:high` and `priority:low` move it in the queue.

## Commands

Comment these on an issue or pull request. Only people on [`trust.txt`](trust.txt), the operator
in `config.json`, and (with `"trust_collaborators": true`) the repository's collaborators may
command the bot; everyone else's commands do nothing and their comments never reach the model.

| Command | What it does |
|---|---|
| `/nightbot build [--force]` | queue this issue (`--force`: start now) |
| `/nightbot ask <question>`, or `@<bot> <question>` | answer a question |
| `/nightbot revise <change>`, or a mention on the bot's pull request | change the bot's pull request |
| `/nightbot review [--force]` | an adversarial review of a pull request |
| `/nightbot stop` | take this item out of the queue and cancel its run |
| `/nightbot rebuild` | start this item over from the default branch (its branch and pull request go) |
| `/nightbot forget` | forget this item's failures |
| `/nightbot status` | what runs, the providers and the queue |
| `/nightbot halt` / `start` | stop starting new runs / start again |
| `/nightbot suspend <provider> [why]` / `resume <provider>` | stop / restart one provider |
| `/nightbot plan` / `approve` / `reject [why]` | with the plan gate: plan this issue / approve its plan / turn it down ([fork mode](docs/fork-mode.md)) |
| `/nightbot help` | this table |

The 👀 reaction means the bot read your command.

## Labels

| Label | Meaning |
|---|---|
| `bot:build` | queued to build (you add it) |
| `bot:building` | a run holds it now |
| `bot:revise` | the bot's pull request is queued for a revision |
| `bot:review` | queued for a review run (an audit, by another provider when one is free) |
| `bot:pr` | a pull request the bot opened |
| `bot:status` | the pinned **Night bot status** issue, rewritten every ten minutes |
| `human` | the bot keeps off it: it gave up, or you said so |
| `ready for merge` | the review approved it, but a person merges it (review-only paths, or no auto-merge) |
| `difficulty:medium`, `difficulty:hard` | the weakest tier that may build and approve it: medium or strong |
| `priority:high`, `priority:low` | taken first, or last |
| `bot:plan`, `bot:awaiting-approval`, `bot:plan-pr` | with the plan gate: queued to plan, its plan waits for an approver, a plan's pull request |
| `requested`, `auto-opened` | with the plan gate: an item a person asked for, one the bot took up from upstream by itself |

## What happens to a change

1. A run claims the item and a provider with a lane free, picks that provider's weakest model
   that is strong enough, and the model builds it on `nightbot/<number>-<title>`.
2. The repository's checks (`config.json` `checks`) run; the model fixes red ones (`fix_passes`).
3. A fresh, read-only session of the same model reviews the diff adversarially and gives a JSON
   verdict. Blocking findings are fixed and it reviews again, up to `review_rounds`.
4. The deliver job checks the change (no forbidden path, only moving the branch forward), pushes
   it and opens a pull request that closes the issue.
   - **Approved, checks green:** marked ready and auto-merge turned on, if the default branch
     requires the `required_checks`. A change to a `review_paths` file waits for a person
     (`ready for merge`).
   - **Rounds ran out:** a draft, queued for an audit by another provider (`bot:review`), up to
     `max_audits` times, then handed to a person (`human`).
5. CI runs on the pull request. Red CI queues a fix (`max_ci_fixes`); green CI merges it.

An item that fails `max_failures` times is handed to a person (`human`), with the reason.

## The merge queue

With a merge queue, every pull request is tested again on top of the newest default branch and
the pull requests queued ahead of it before it merges, so two changes that pass alone but break
together never both land. The bot works with one:

- **Turn it on** with `"merge_queue": true` in `config.json` (or `install.sh --merge-queue`), then
  `nightbot setup --repo-settings`: it writes the ruleset **nightbot: default branch**, which
  requires your `required_checks` and sends every merge through the queue. Merge queues need a
  repository an organization owns (public, or private on GitHub Enterprise Cloud).
- **Your CI must run on `merge_group`**: add `merge_group:` to its `on:`, or the queue waits for
  its checks until it times out. `nightbot doctor` checks.
- **An approved change joins the queue** through auto-merge, pinned to the commit the review
  approved; the queue's own rule sets the merge method.
- **When it fails in the queue**, GitHub takes it out, and the bot queues a revision that merges
  the newest default branch into the pull request first (a real merge commit; the model resolves
  any conflicts), then fixes what CI says, has it reviewed again, and puts it back in the queue.
- **A pull request that conflicts with the default branch** gets the same merge: the sweep looks
  every ten minutes. The bot never merges the default branch in otherwise: the queue tests every
  pull request on the newest one anyway, and each merge would start its CI again.

## Stopping it

| To stop | Do |
|---|---|
| every new run | `/nightbot halt` (undo: `/nightbot start`) |
| everything, in a way no comment can undo | commit a file `.nightbot/HALT` (delete it to start again) |
| one provider for a while | `/nightbot suspend <provider>`, or `"enabled": false` in `providers.json` for good |
| one item | `/nightbot stop`, or label it `human`, or close it |

A run that is going finishes its current step; `stop` cancels it outright.

## Setting it up

`Night-Bot-Scaffolding`'s README has the full quick start. In short:

1. **The bot's GitHub identity.** Pushes and pull requests made with the workflow's own
   `GITHUB_TOKEN` start no CI, so auto-merge would never fire. Give the bot one of:
   - a **GitHub App** (recommended): set the repository variable `NIGHTBOT_APP_ID` and the secret
     `NIGHTBOT_APP_PRIVATE_KEY`. Permissions: Contents, Issues and Pull requests read and write;
     Actions read. `bot_login` is then `<app-slug>[bot]`.
   - or a **bot account** with write access and a classic token with the `repo` scope as the
     secret `NIGHTBOT_GITHUB_TOKEN`. `bot_login` is the account's login.
2. **The model logins**, one repository secret per provider in `providers.json` (`secret`), or a
   machine login ([`machine/README.md`](machine/README.md)). Every provider secret must be listed
   in the `NIGHTBOT_SECRETS_SET` blocks of both workflows; `nightbot validate` says which is missing.
3. **Your checks**: `config.json` `checks` are the commands that prove a change works (tests,
   lint, build), and the work job needs your toolchain (the marked spot in `nightbot-run.yml`).
4. **Once, with an admin token**: labels, the state branch, auto-merge and branch protection:

   ```sh
   cd .nightbot/harness
   GITHUB_TOKEN=$(gh auth token) go run ./cmd/nightbot setup --repo-settings
   GITHUB_TOKEN=$(gh auth token) go run ./cmd/nightbot doctor
   ```

   On a **public** repository, an admin also allows `pull_request_target` for
   `.github/workflows/nightbot-events.yml` alone, in the repository's (or its organization's)
   Actions event policies: from 2026-12-02 GitHub blocks it in public repositories unless a policy
   allows it, and without it the bot never hears its pull requests close or a label put on one.
   The workflow checks out the default branch and runs none of the pull request's code.
5. **Try it**: open an issue, comment `/nightbot build --force`, and watch the `nightbot-run`
   workflow. Its manual run takes `dry_run`, which records every GitHub write instead of sending it.

## When something goes wrong

| Symptom | Look at |
|---|---|
| a command got no 👀 | the `nightbot-events` run for that comment: are you on `trust.txt`? did the workflow's `if` filter skip it? |
| an item never starts | `/nightbot status`: the queue says what each item waits on (a provider's hours, caps, a missing secret, no free lane) |
| a pull request's close or label got no answer | its `nightbot-events` run: one that failed at once, blocked by policy, means a public repository whose Actions event policies do not allow `pull_request_target` for `nightbot-events.yml` (Setting it up, step 4) |
| a run failed | the `nightbot-run` run linked from the bot's comment; its `work` artifact holds `result.json` and the change's bundle |
| a pull request sits in the merge queue and never merges | your CI must run on `merge_group`; `nightbot doctor` says whether it does |
| a provider is "parked" | its subscription refused for usage limits; it starts again at the reset time it gave (or after `park_minutes`) |
| no auto-merge | the bot's comment says why: no required checks on the default branch, a review-only path, or `auto_merge` off |
| the state looks wrong | the `_nightbot-state` branch's `state.json`; editing it by hand is safe while no run is going |

## Working on the harness

The harness is Go, standard library only: `harness/`. Its tests use an in-memory GitHub and real
git repositories:

```sh
cd .nightbot/harness
gofmt -l . && go vet ./... && go test ./...
go run ./cmd/nightbot validate
```

The bot cannot change `.nightbot/` or `.github/` (`forbidden_paths`): a change here is a person's.
