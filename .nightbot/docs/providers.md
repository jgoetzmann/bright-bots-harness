# Providers, CLIs and tiers

## Tiers: weak, medium, strong

Every model a provider runs has a tier. Every piece of work needs one:

| Work | Needs | Goes to |
|---|---|---|
| a question (`/nightbot ask`, a mention) | weak | the weakest model free: a weak one first, a medium or strong one only when no weak one is |
| triage of an unrated item (`"triage": true`) | weak | the same |
| building, revising and reviewing a **medium** item | medium | the weakest medium-or-better model free |
| building, revising and reviewing a **hard** item | strong | a strong model only |

So the cheap, fast models (Haiku, Gemini Flash, a small local model) answer the **weak queries**
(questions and triage), and the expensive ones are kept for work that needs them. One provider
with three models, like a Claude subscription with Opus, Sonnet and Haiku, does all three kinds
of work, each on the model it needs. An item's own review always runs on the model that built it,
in a fresh session; an audit after the rounds run out prefers another provider.

An unrated item builds as medium. With `"triage": true` a weak model reads it first and labels it
`difficulty:medium` or `difficulty:hard` (torn, it picks hard); a person's label always wins.

## The built-in CLIs

| `cli` | Install | Secret login | Machine login | Notes |
|---|---|---|---|---|
| `claude` | `npm install -g @anthropic-ai/claude-code` | `CLAUDE_CODE_OAUTH_TOKEN`: the token `claude setup-token` prints (Pro or Max); or set `secret_env` to `ANTHROPIC_API_KEY` | `claude` then `/login`, as the provider's user | models `opus`, `sonnet`, `haiku` or full ids; `effort` passes `--effort` |
| `codex` | `npm install -g @openai/codex` | an API key: override `secret_env` to `OPENAI_API_KEY`; or `~/.codex/auth.json`'s content (`secret_file`), which refreshes itself, so a ChatGPT plan is best logged in on a machine | `codex login --device-auth` | `effort` sets `model_reasoning_effort` |
| `gemini` | `npm install -g @google/gemini-cli` | `GEMINI_API_KEY` from Google AI Studio | `gemini`, then sign in with Google | |

Each runs non-interactively, with edits allowed for build and fix calls and denied (or thrown away)
for review, ask and triage calls. Network tools (`curl`, `wget`, `ssh`, `gh`, `git push`) are
denied where the CLI can deny them.

CLIs change their flags. If one changes, override it in `config.json` (below) rather than
editing the harness.

## Adding or changing a CLI

`config.json` `clis` adds a CLI by a new name or replaces a built-in one whole:

```json
"clis": {
  "aider": {
    "install": "pipx install aider-chat",
    "argv": ["aider", "--model", "{model}", "--yes-always", "--no-auto-commits", "--message-file", "{prompt_file}"],
    "read_args": ["--dry-run"],
    "prompt": "file",
    "answer": "stdout",
    "secret_env": "OPENAI_API_KEY",
    "rate_limit": ["(?i)RateLimitError"]
  }
}
```

| Field | What |
|---|---|
| `install` | a shell command that installs it on a fresh GitHub-hosted runner |
| `argv` | the command; then `write_args` (a call that may edit) or `read_args` (one that may not), `effort_args` (when the model has an `effort`), and `tail` |
| placeholders | `{model}`, `{effort}`, `{max_turns}`, `{cwd}`, `{prompt}`, `{prompt_file}`, `{system_file}`, `{answer_file}` |
| `prompt` | `stdin` (default), `arg` (`{prompt}`) or `file` (`{prompt_file}`) |
| `system` | `file` when `argv` names `{system_file}`; otherwise the system prompt opens the prompt |
| `answer` | `stdout` (default), `json:<field>` (a field of the JSON object stdout ends with) or `file` (`{answer_file}`) |
| `secret_env`, `secret_file` | where a secret login goes: an environment variable, or a file (`~` is the home) |
| `env` | extra environment for the CLI |
| `rate_limit` | extra regular expressions that mark a usage-limit refusal |

The harness finds a usage-limit refusal by its words (`usage limit`, `rate limit`, `quota
exceeded`, `too many requests`, …) in a call that failed, reads the reset time when the message
gives one (`resets at 3pm (America/New_York)`, `try again in 30 minutes`, an ISO time, a Unix
time), and parks the provider until then. Your prompts and the bot's tests do not depend on the CLI.

## A local model for the weak queries

A small open model on a machine of yours can answer the questions and triage for nothing, keeping
your subscriptions for the builds. For example, the Codex CLI's `--oss` mode runs a model through
[Ollama](https://ollama.com) on the same box (check `codex exec --help` on your version):

```json
"clis": {
  "codex-local": {
    "install": "npm install -g @openai/codex && curl -fsSL https://ollama.com/install.sh | sh",
    "argv": ["codex", "exec", "--oss", "--skip-git-repo-check", "--cd", "{cwd}", "--model", "{model}",
             "--output-last-message", "{answer_file}"],
    "write_args": ["--sandbox", "workspace-write"],
    "read_args": ["--sandbox", "read-only"],
    "tail": ["-"],
    "answer": "file"
  }
}
```

```json
"local": {
  "enabled": true,
  "cli": "codex-local",
  "models": [{ "model": "gpt-oss:20b", "tier": "weak" }],
  "login": "machine",
  "runs_on": "nightbot-local",
  "lanes": 1
}
```

Give the machine a runner labelled `nightbot-local` ([`../machine/README.md`](../machine/README.md#a-machine-of-your-own),
the local guide; `setup.sh` runs the `install` above), pull the model once (`ollama pull
gpt-oss:20b`), and put
`local` first in `priority`: weak models tie on tier, ties go by priority, so the planner sends
every weak query there while it has a lane free, and to a subscription's weak model when it has not. A model good enough to build with can take a `medium`
tier the same way.

## Where a provider runs

`runs_on: ubuntu-latest` (or any GitHub-hosted label) installs the CLI in every run, and suits a
login that travels as a secret. A login that cannot (a ChatGPT plan, a Google sign-in), a toolchain
that takes long to install, or a model on your own hardware needs a machine: a self-hosted runner
per provider, each a Linux user of its own. [`../machine/README.md`](../machine/README.md) sets one
up on AWS, Azure, Google Cloud or a computer you own, waking it when work waits and powering it
off when idle.
