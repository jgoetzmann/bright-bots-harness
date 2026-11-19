# Revise #{{.number}}

You are changing the work on the branch `{{.branch}}` (it starts from `{{.base}}`; see
`git log {{.base}}..HEAD` and `git diff {{.base}}...HEAD` for what is there). Its difficulty is
**{{.difficulty}}**.

## What to do

{{.request}}

## Review findings to address

{{.findings}}

## The checks, as they last ran

{{.checks}}

## The original task

{{.thread}}

{{.plan}}

## How to work

- Address every point above, each in the smallest change that fixes its cause. A finding you are
  sure is wrong may be declined: say why in your report, with evidence.
- Never weaken, skip or delete a test or a check to make it pass.
- Run the checks again before you finish:

{{.checks_list}}

End with a short report: each point and what you did about it.
