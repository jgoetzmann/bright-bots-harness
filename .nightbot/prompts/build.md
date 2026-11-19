# Build {{.kind}} #{{.number}}

You are building this {{.kind}} (its title and text are in the task below) into a change on the
branch `{{.branch}}`, which starts from `{{.base}}`. Its difficulty is **{{.difficulty}}**. When
you finish, the harness commits what you changed, runs the checks below, and a separate reviewer
reads the diff adversarially; anything it finds comes back to you. If it approves and CI passes,
the change may merge with no person reading it first, so the bar is a change you would sign.

{{.continue}}

## The task

{{.thread}}

{{.plan}}

## How to work

1. Understand the task and the code it touches before editing. Find the existing helpers,
   patterns and tests you should reuse.
2. Make the smallest complete change that does what the task asks: no unrelated refactors, no
   speculative features. Add or update tests that prove the new behaviour.
3. Run the repository's checks yourself and make them pass:

{{.checks_list}}

4. If the task is unclear, contradictory or needs a decision only a person can make (an account,
   a secret, a product choice), make no change and explain exactly what is needed instead.

## Your report

End with a short report in Markdown: what you changed and why, how you tested it, anything you
were unsure of, and anything left undone. The report goes on the pull request.
