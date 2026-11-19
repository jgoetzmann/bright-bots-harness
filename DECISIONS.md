# Decisions

Why the harness is the way it is. The newest decision is last; cite one as `D<n>`.

## D1. The night bot in fork mode replaces the Python harness

The Python harness (decisions D1–D102 of its own, in the history up to `ce13f23`) took a brightboost
issue to a proposal, a merge of the proposal as gate 1, an implementation and an upstream pull
request, and stopped there. This repository is now an install of Night-Bot-Scaffolding in fork mode,
which keeps both human gates and adds what the old harness lacked:

- **The same harness as JackiOh.** One Go binary from Night-Bot-Scaffolding, upgraded with
  `install.sh --upgrade`, instead of a second harness to maintain. Changes to the harness itself go
  to the scaffolding first.
- **Gate 1 is a comment.** `/nightbot approve` from Jack or Nathan on the plan's draft pull request
  (or an approving review), so approving does not need a merge and a plan can be rewritten in place
  with `/nightbot revise` until it is right.
- **A sandbox before upstream.** An approved build goes through brightboost's own CI on the fork and
  merges into the fork's `main`, so people can try changes with a keyboard before anything reaches
  brightboost, and upstream only ever sees changes that were green once already.
- **The bot follows its upstream pull request to the end.** It merges `main` in on a conflict, fixes
  red CI, and revises on a changes-requested review or a `/nightbot revise` there, until a person
  merges or closes it. The old harness stopped at delivery.
- **Two intake labels.** `requested` for work a person asked for from the template, `auto-opened`
  for work the bot took from the `harness-ok` pool, two at a time, so the bot can never flood the
  queue on its own.
- **A tracker.** The status issue shows every item's stage and what it waits on, which is what the
  old heartbeat issue did not.

What it gives up: the old harness's per-account `vouch:` pinning (the trust list is logins), its
redaction of every write and its pinned prompts (the model holds no GitHub write token, and
`.nightbot/` and `.github/` are forbidden paths the bot can never change), and its quiet-subscription
wait (replaced by fixed hours before JackiOh's, and parking on a usage-limit refusal).

## D2. Opus 5.5 at xhigh effort, plans by day and builds by night

Every call runs `claude-opus-5-5` at `xhigh` effort: the plan, the build, the self-review rounds and
the fixes. Two provider entries share the one subscription: `claude-day` (13:00–23:00 UTC) only
plans and answers questions, so a plan is up for approval the same day; `claude-night`
(01:30–06:30 UTC) builds and revises, half an hour before the JackiOh night bot's hours. One run at
a time across both.
