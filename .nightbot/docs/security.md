# Security

An unattended model with a shell, a login and a path to `main` is worth defending carefully. These
are the defences, and what they cannot rule out.

## The defences

- **The model never holds a GitHub write token.** The `work` job gets the chosen provider's one
  login (picked by name: `secrets[...]`) and a token that can only read the repository and its
  Actions. The harness takes even those out of the model's environment, with every other
  variable whose name looks like a secret, and out of the checks' environment; the workflow
  `exec`s it, so no shell stays behind holding them, and it makes its own process non-dumpable,
  so the model's processes (the same user) cannot read them from `/proc`. Its fetches with the
  read-only token all happen before the first model call, and its git commands run with hooks
  and fsmonitor off. Pushing, commenting and labelling happen in `plan` and `deliver`, which run
  no model and see only *whether* each provider secret is set.
- **Deliver checks the work for itself.** The model job's only output is `result.json` and a git
  bundle. Deliver verifies the bundle against the repository, refuses it if any commit touches a
  `forbidden_paths` file, and pushes only a change that moves the branch forward
  (`--force-with-lease`), and only to a branch of the bot's own (`branch_prefix`) on a pull
  request the bot opened in this repository. Path names are read from git's own bytes, so an
  accent or a quote in a name cannot slip a forbidden path past the patterns. The item, the
  provider, the branch and the changed paths come from the plan job and from the bundle itself,
  never from anything the model job wrote. The model job only restores the cached `nightbot`
  binary, never saves it, so nothing it touched reaches the jobs that hold the bot's token.
- **It cannot change its own rules.** `.nightbot/` and `.github/` must be in `forbidden_paths`
  (validation fails otherwise), so the harness, its configuration, the prompts, the trust list
  and every workflow are a person's to change. The work job puts back any change to them before
  committing; deliver refuses a bundle that still has one.
- **Two gates before `main`.** A fresh, read-only session reviews every change adversarially and
  must approve it with no blocking finding; then every check the default branch requires must
  pass before auto-merge merges anything. Deliver turns auto-merge on only after reading the
  default branch's protection (or rulesets) and finding every `required_checks` entry there, and
  only for the commit the review approved (`expectedHeadOid`): any later push turns it off, and a
  revision turns it off before it pushes, so only a new approval of the new head turns it on again.
- **Some changes always wait for a person.** A change to a `review_paths` file (dependency
  manifests and locks, Dockerfiles, infrastructure, migrations by default) becomes a pull request
  labelled `ready for merge`, and a person merges it.
- **Text from GitHub is data.** Titles, issue text, comments, review bodies, CI logs, diffs and the
  builder's report reach the model inside `<untrusted-data>` fences it cannot close early (in any
  case or spacing), and the prompts say they are not instructions. Comments from people outside
  the trust list are left out entirely, and their commands do nothing. So is the text of an issue
  a stranger wrote, since they can edit it after you queue it: say in a comment of your own what
  to build. The prompts are read into memory before any model runs, so a change the builder makes
  to `.nightbot/prompts/` never reaches the reviewer.
- **Outside pull requests never run.** `/nightbot review` or a question on a pull request from
  another repository or from someone off the trust list reviews its diff from the trusted base:
  its code, its checks, its CLI settings (a `CLAUDE.md`, `.claude/` hooks) and its prompts never
  run, and the bot only comments on it.
- **Off switches.** `/nightbot halt`, `/nightbot suspend <provider>`, `/nightbot stop`, the
  `human` label, and a committed `.nightbot/HALT`, which only someone who can push to the default
  branch can lift.
- **On a machine,** each provider is a Linux user of its own whose home, with its login, no other
  user can read; none has `sudo` or Docker; its runner carries a label of its own, so it takes only
  its own jobs; the machine accepts no inbound connection where the cloud allows it
  ([`../machine/README.md`](../machine/README.md)).

- **In fork mode, nothing merges upstream.** The upstream repository's client is made unable to
  merge, and a person merges every delivery pull request there (gate 2); before anything is built
  an approver approved its plan (gate 1). The bot merges only its plans here and approved,
  CI-green changes into its own fork's sandbox. Upstream it pushes nothing: its branches are on
  the fork, and it opens pull requests from them. It reads an upstream issue's text even when its
  author is off the trust list, fenced as data, because a trusted person linked it or a
  maintainer put it in the pool, and because a person reads the plan made from it before anything
  is built. Comments upstream count only from people on the trust list.

## What it cannot rule out

- **The model has a shell and its login**, because it needs the login to work. A prompt injection
  that fools it could, in principle, read that login (from its own process's environment or its
  CLI's files) and leak it through anything it can reach: the network its build needs, or the
  pull request it writes, which is public on a public repository. Network tools are denied where
  the CLI can deny them, but that only slows a determined model down. On a machine, it could leave
  something in its own user's home for that provider's next run (never another provider's).
- **Everything the model writes into a pull request is published** once deliver pushes it.

- **On GitHub's hosted runners the model has `sudo`**, so a determined one can read anything on the
  runner, the harness's memory included. A machine of your own gives each provider a user with
  no `sudo`.
- **Anyone who can label issues can queue work.** Labels and assignment need triage access, and
  the bot acts on `bot:build` whoever added it. Give triage access only to people you would trust
  to start the bot.

So the defences upstream matter most: only trusted people can start work, text from anyone else is
left out, and each login is one you can revoke and replace at any time.

## Settings to keep

- **Actions → General → "Require approval for all external contributors"** (or stricter), always,
  and above all if you use self-hosted runners: every workflow in the repository can ask for their
  labels, and a stranger's pull request must not reach them.
- **Branch protection or a ruleset on the default branch** requiring your CI (`setup
  --repo-settings` does it). Without one, deliver leaves auto-merge off.
- **The bot's token scoped to this repository.** A GitHub App installed on this repository alone,
  or a bot account that is a collaborator here and nowhere it should not be.
- **Artifacts are readable by anyone who can read the repository's Actions.** The work job uploads
  `result.json` (the reports, review findings and check output, with the model's login redacted)
  and the change's bundle, never the model's session transcripts.
