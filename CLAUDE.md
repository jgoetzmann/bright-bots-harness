# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

The night bot for `Bright-Bots-Initiative/brightboost`: an install of
[Night-Bot-Scaffolding](https://github.com/Jlore-Studios/Night-Bot-Scaffolding) in fork mode. This
repository (`jgoetzmann/bright-bots-harness`) holds the queue (issues labelled `requested` or
`auto-opened`), the plans (`plans/`, each a draft pull request an approver approves with
`/nightbot approve`: gate 1) and the status issue. The bot, `jgoetzmann-bot`, builds on its fork
`jgoetzmann-bot/brightboost`, merges approved, CI-green changes into the fork's `main` (the
sandbox), and opens a pull request upstream, which a person merges (gate 2). It never merges
upstream. `README.md` is the operator's view; `.nightbot/docs/fork-mode.md` the whole flow;
`DECISIONS.md` the why.

## Commands

```bash
cd .nightbot/harness
gofmt -l . && go vet ./... && go test ./...   # the suite (in-memory GitHub, real git, a fake model CLI)
go test ./internal/bot/ -run TestFork          # fork mode's end-to-end tests
go run ./cmd/nightbot validate                 # .nightbot/ and the workflows, offline
go run ./cmd/nightbot --help                   # every subcommand
```

The suite needs Go 1.24+ and git, nothing else. `nightbot-selftest.yml` runs the same on every
change to `.nightbot/` or the workflows.

## Architecture

- `.nightbot/harness/` is Go, standard library only. `internal/bot` holds the GitHub side (plan,
  deliver, events, the ten-minute sweep, the status issue); `gate.go` is the plan gate and
  `fork.go` fork mode. `internal/work` is the model job, `internal/sched` who takes what,
  `internal/state` the memory (`state.json` on the `_nightbot-state` branch).
- Three jobs per run (`nightbot-run.yml`): `plan` (the bot's token, no model), `work` (one model
  login, a read-only token, the product cloned at `$NIGHTBOT_PRODUCT` with the fork as the remote
  `fork`), `deliver` (the bot's token, no model: verifies the bundle and pushes to the fork). The
  model never holds a GitHub write token.
- `nightbot-events.yml` answers commands, labels and reviews here, and every ten minutes sweeps:
  syncs the sandbox, takes up `harness-ok` issues, merges green fork pull requests, opens upstream
  pull requests under the cap, and reads upstream CI, conflicts, reviews and comments, since
  brightboost sends this repository no events.
- `.nightbot/config.json` holds the gates (brightboost's seven, plus `npm ci`), the approvers, the
  upstream cap and the pool; `.nightbot/providers.json` the model (`claude-opus-5-5`, `xhigh`) and
  its hours; `.nightbot/trust.txt` who may command it.

## Rules

- The harness is the scaffolding's. Change it in Night-Bot-Scaffolding first and bring it here with
  `install.sh --upgrade`; a change made only here is lost on the next upgrade. Configuration,
  prompts, workflows and docs here are this repository's own.
- `.nightbot/` and `.github/` are forbidden paths: the bot can never change them, and CODEOWNERS
  gives them to `@jgoetzmann`.
- Never print a secret value. Refer to tokens by name: `CLAUDE_CODE_OAUTH_TOKEN`,
  `NIGHTBOT_GITHUB_TOKEN` (or `HARNESS_GITHUB_TOKEN`).
- Commits use a conventional prefix and a sentence-style subject, e.g.
  `fix: a plan approved twice builds once`. Work on a branch and open a pull request; the owner
  merges. Commits and pull request descriptions carry no AI attribution.
- Go code is `gofmt`ed. Comments state behaviour in the present tense, briefly.
