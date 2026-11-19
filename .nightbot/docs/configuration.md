# Configuration

Three files in `.nightbot/`, checked by `nightbot validate` and the selftest workflow. Unknown
fields are errors, so a typo never silently does nothing.

## `config.json`

| Field | Default | What |
|---|---|---|
| `repo` | `$GITHUB_REPOSITORY` | `OWNER/NAME` |
| `default_branch` | `main` | the branch work starts from and merges into |
| `bot_login` | (required) | the login the bot comments and pushes as: an account, or `<app-slug>[bot]` for a GitHub App |
| `operator` | | the person the bot @-mentions when it needs one; always trusted |
| `timezone` | `UTC` | an IANA zone, for the providers' `schedule` windows |
| `command_prefix` | `/nightbot` | the commands' first word (also in the events workflow's `if` filter) |
| `label_prefix` | `bot:` | the queue labels' prefix (`bot:build`, …) |
| `branch_prefix` | `nightbot/` | the bot's branches (also in the events workflow's `if` filter) |
| `state_branch` | `_nightbot-state` | where `state.json` lives |
| `run_workflow` | `nightbot-run.yml` | the run workflow's file, which the bot starts |
| `trust_collaborators` | `false` | also trust anyone GitHub calls an owner, member or collaborator |
| `triage` | `false` | rate unrated items with a weak model before they are built |
| `ci_workflow` | `CI` | the `name:` of your CI workflow; its failures on the bot's branches queue fixes (also in the events workflow's `workflow_run`) |
| `required_checks` | `[]` | the checks the default branch must require before the bot turns auto-merge on; `setup --repo-settings` requires them (in a ruleset with `merge_queue`, in branch protection without) |
| `auto_merge` | `true` | turn on auto-merge for an approved change; off, a person merges every one |
| `merge_method` | `squash` | `squash`, `merge` or `rebase` (with a merge queue, the queue's rule) |
| `merge_queue` | `false` | merge through a merge queue: `setup --repo-settings` writes a ruleset with the required checks and the queue ([README](../README.md#the-merge-queue)); your CI must run on `merge_group` |
| `checks` | `[]` | `{name, run, timeout_minutes}`: shell commands that prove a change works, run after every change in a clean environment |
| `fix_passes` | `2` | how many times the builder may fix red checks in a row |
| `review_rounds` | `{"medium": 3, "hard": 6}` | reviews per run, by difficulty |
| `max_audits` | `2` | audits by another provider after the rounds run out, before a person gets it |
| `max_ci_fixes` | `3` | CI fixes on one pull request before a person gets it |
| `max_failures` | `3` | failed runs on one item before a person gets it |
| `forbidden_paths` | (must cover `.nightbot/` and `.github/`) | paths the bot may never change: put back in the work job, refused by deliver |
| `review_paths` | `[]` | paths whose changes the bot may make but a person must merge (`ready for merge`) |
| `max_turns` | build 300, fix 200, review 100, ask 40, triage 30 | the CLI's turn limit per kind of call, where it has one |
| `call_timeout_minutes` | `120` | one model call's limit |
| `job_budget_minutes` | `300` | the work job's limit; keep it under the workflow's `timeout-minutes` (330) |
| `park_minutes` | `60` | how long a refused provider waits when the refusal names no reset time |
| `max_diff_chars` | `60000` | how much of the diff the reviewer is shown inline (it can read the rest with git) |
| `status_issue_title` | `Night bot status` | |
| `clis` | `{}` | model CLIs added or replaced ([`providers.md`](providers.md)) |
| `plan_gate` | `false` | plan every item first, as a draft pull request, and build nothing until an approver approves it ([`fork-mode.md`](fork-mode.md)) |
| `approvers` | `[]` | who may approve a plan; each must be on `trust.txt`. Empty: the operator |
| `plans_dir` | `plans` | where plans are written in this repository |
| `upstream` | `null` | fork mode: the bot builds on its fork of another repository and delivers there (below) |

### `upstream` (fork mode)

| Field | Default | What |
|---|---|---|
| `repo` | (required) | `OWNER/NAME` of the product: delivery pull requests go there, and a person merges them |
| `fork` | (required) | the bot's fork of it: its branches, and the sandbox |
| `default_branch` | `main` | upstream's branch work starts from; the fork's branch of the same name is the sandbox |
| `fork_merge_method` | `merge` | how a change merges into the sandbox: `merge`, `squash` or `rebase` |
| `max_open_prs` | `2` | the bot's pull requests open upstream at once (its earlier ones count); ready work waits in the fork |
| `reviewers` | `[]` | asked to review an upstream pull request once its CI is green |
| `auto_open` | `{"labels": [], "assigned": false, "max_open": 2}` | upstream issues the bot takes up by itself: open ones with one of `labels`, or assigned to the bot, at most `max_open` of its own open here |
| `no_ci_minutes` | `30` | how long a head goes with no check reporting before the bot stops waiting for the fork's CI, or marks a draft upstream pull request ready so its CI runs |

Path patterns are globs from the repository root: `*` and `?` stay in one directory, `**` crosses
directories, and a pattern ending in `/` is everything under that directory (`.github/`,
`**/package-lock.json`, `db/migrations/**`).

## `providers.json`

A provider is one subscription: one login of one CLI, with the models it may run.

```json
{
  "max_parallel": 3,
  "priority": ["claude", "gemini"],
  "providers": {
    "claude": {
      "enabled": true,
      "cli": "claude",
      "models": [
        { "model": "opus", "effort": "high", "tier": "strong" },
        { "model": "sonnet", "tier": "medium" },
        { "model": "haiku", "tier": "weak" }
      ],
      "login": "secret",
      "secret": "CLAUDE_CODE_OAUTH_TOKEN",
      "runs_on": "ubuntu-latest",
      "lanes": 2,
      "schedule": { "mode": "window", "start": "22:00", "end": "07:00" },
      "limits": { "five_hour_minutes": 240, "seven_day_minutes": 2400 }
    }
  }
}
```

| Field | What |
|---|---|
| `max_parallel` | runs at once across every provider |
| `priority` | the order ties go in; providers not named come after, by id |
| `enabled` | `false` keeps it out for good (`/nightbot suspend` for a while) |
| `cli` | a built-in CLI (`claude`, `codex`, `gemini`) or one under `clis` in `config.json` |
| `models` | `{model, effort, tier}`: each model it may run and its tier, `weak`, `medium` or `strong` |
| `login` | `secret`: a repository secret handed to that run's model job alone; `machine`: a login made once in the provider's own user on a machine of yours |
| `secret` | the secret's name (default: the CLI's `secret_env`). List it in both workflows' `NIGHTBOT_SECRETS_SET` |
| `runs_on` | the model job's runner: `ubuntu-latest`, or your machine's label `nightbot-<id>` |
| `lanes` | runs it may hold at once |
| `schedule` | `{"mode": "always"}`, or `{"mode": "window", "start": "HH:MM", "end": "HH:MM"}` in `timezone` (a window may cross midnight). `--force` ignores it |
| `limits` | `five_hour_minutes` and `seven_day_minutes`: caps on the model minutes the harness counts itself over rolling windows (a run in progress counts from its start). Empty: no cap; a refusal still parks it |
| `actions` | the only actions it takes (`plan`, `ask`, `triage`, `build`, `revise`, `review`); empty: all. Two providers on one login can split a subscription: plans and questions all day, builds at night |
| `note` | for people |

## `trust.txt`

One GitHub login a line; `#` starts a comment. These people, the operator, and with
`trust_collaborators` the repository's collaborators, may command the bot. Comments from anyone
else are left out of everything the model reads.

## In the workflows

| Where | What to change |
|---|---|
| both workflows, `NIGHTBOT_SECRETS_SET` | one line per provider secret (`nightbot validate` checks) |
| `nightbot-run.yml`, the marked spot in `work` | your toolchain's setup steps on GitHub's runners |
| `nightbot-events.yml`, `workflow_run.workflows` | your CI workflow's name (`install.sh --ci-workflow` sets it) |
| `nightbot-events.yml`, the `if` | `/nightbot` and `nightbot/` if you change `command_prefix` or `branch_prefix` |
| repository variable `NIGHTBOT_APP_ID`, secret `NIGHTBOT_APP_PRIVATE_KEY` | a GitHub App as the bot; or the secret `NIGHTBOT_GITHUB_TOKEN` |
