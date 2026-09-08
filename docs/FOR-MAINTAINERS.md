# For maintainers

You have been given level 2 on an automated agent that works on
[`Bright-Bots-Initiative/brightboost`](https://github.com/Bright-Bots-Initiative/brightboost). This
page is the whole of what you need to use it. It takes about five minutes and assumes you will never
open a terminal.

If you want the depth instead, [USING.md](USING.md) is the reference and this page is its front
door.

---

## 1. What it is

It takes an issue on brightboost from "somebody wants this" to a reviewable pull request, on its own,
and then stops and waits for a person. It writes code, runs the repository's real lint, typecheck,
build and test commands, and opens a pull request from a fork.

**It merges nothing. Ever.** Not its own plans, not its own code. It is not a collaborator on
brightboost and cannot become one; its own repository requires a review. Every path it can take ends
at a human clicking merge.

## 2. The one gesture

Comment on the pinned **[inbox issue](https://github.com/jgoetzmann/bright-bots-harness/issues/19)**:

```
/harness work make the activity cards keyboard reachable
```

A sentence is enough. A pasted link to a brightboost issue works too, and does the same thing as
naming that issue by hand:

```
/harness work https://github.com/Bright-Bots-Initiative/brightboost/issues/633
```

Within one sweep, a work item opens as its own issue and a reply lands in the inbox thread with a
link to it. Asking twice is one item, not two.

**Or assign `@jgoetzmann-bot` to a brightboost issue.** That is the same gesture in the place you
already work, and it needs no comment at all.

## 3. Your two moves

Everything else the harness does is bookkeeping. There are exactly two moments it needs you, and
both are labelled in orange so you can find them by scanning:

**`stage:needs-approval` — gate 1.** A pull request in the harness repository adding
`proposals/<id>-<slug>.md`. That file is a *plan*: the diagnosis, the approach, the files it intends
to touch, and how a reviewer will know it worked. No code has been written yet and nothing has been
spent on writing any.

- Merge it → the plan is approved and implementation starts.
- Close it → rejected. Nothing further is attempted.
- Comment `/harness revise <what is wrong>` → it rewrites the plan with your note as the brief.

**`stage:needs-review` — gate 2.** A pull request on brightboost, from the fork, with the real diff
and every gate's output in the body. Read it like any other contributor's PR.

- `/harness fix <what to change>` → one more implementation pass against your feedback, re-running
  the complete gate sequence.
- `/harness rebase` → same, after a conflict.
- `/harness stop` → close it and abandon the item.

Both of those verbs go **on the pull request**, as a normal review comment.

## 4. Reading the board at a glance

Three label families, because one flat list cannot answer three different questions.

| Family | Question it answers |
|---|---|
| `stage:` | where is it, and **whose move is it** |
| `kind:` | `product` (work) · `audit` (a findings report) · `ops` (a failed run) |
| `via:` | how it got here: `assigned` · `requested` · `suggested` · `audit` |

Two stages are loud on purpose — **`needs-approval`** and **`needs-review`** are the only ones
waiting on a person. Everything else is the harness's own move and you can ignore it.

`via:suggested` is worth knowing: it means **nobody asked for this**. The harness only proposes on
its own when the queue is empty and the week has budget left, it does at most five a week, and it
says so in the issue. Ignoring one is a complete answer.

## 5. The rest of the vocabulary

Put any of these at the **start of a line** in a comment.

| Command | Where | What it does |
|---|---|---|
| `/harness ask <question>` | anywhere | reads brightboost and answers, in the thread. **Changes nothing at all** |
| `/harness audit <lens>` | an issue in the harness repo | reads brightboost through one lens and opens **one** findings issue. Creates no work |
| `/harness promote 3` · `promote all` | on that audit issue | turns findings into work items |
| `/harness go` | a suggestion | green-lights it |
| `/harness split` | a work item | breaks it into sub-issues |
| `/harness queue` | a work item | puts a parked one back |

`ask` is the cheap one and the one to start with. It costs cents, changes nothing, and is the
fastest way to find out whether the thing understands the codebase — ask it something you already
know the answer to.

`audit` and `promote` are deliberately two steps. One sentence from you must not become eight
proposals nobody approved: an audit produces a *list*, and you choose which lines become work.

## 6. What you cannot do, and why

`reject` and `--force` are level 3 (Jack only).

`--force` starts work now instead of at the next run window. The window — Monday 08:00 to Tuesday
20:00 UTC — is the main thing standing between an enthusiastic week and an exhausted allowance.
You can queue as much work as you like; whether it happens tonight is the operator's call, because
it is the operator's subscription.

`reject` is the one verb that ends a work item for good.

If you use one anyway, nothing happens and you get a reply saying which level it needed. Refusals
are answered, never silent.

## 7. Latency — why nothing happened yet

On the **harness repository**, a comment wakes the job directly. Minutes.

On **brightboost**, the harness gets no events — it is not a collaborator there, and it must not be.
It finds your comment by reading its notifications every three hours on weekdays. A comment left on
Saturday afternoon waits until Monday morning. That is the design, not a fault. To skip the wait,
ask Jack, or run `feedback` from the Actions tab.

## 8. If something looks wrong

**To stop everything:** commit a file called `.harness/HALT` on `main`. Any content. Every spending
workflow then exits before the dispatcher and before a single token. Deleting the file resumes.
One commit either way, doable from a phone. You do not need permission and you will not break
anything by using it.

**A pull request that looks wrong** is just a pull request. Close it.

**Anything stranger** — a stuck item, a run that keeps failing, spend that looks off —
[OPERATIONS.md](OPERATIONS.md) has the diagnosis order, and §8 is how to stop everything.

## 9. What is not yet true

Being straight about the state of it:

- It has completed the whole flow **once**, live:
  [`brightboost#868`](https://github.com/Bright-Bots-Initiative/brightboost/pull/868), all six
  brightboost checks green. That took four attempts, and each failed attempt found a real defect in
  the harness rather than in the change it was delivering.
- Everything on this page after that first run has been tested against a fake model, a fake GitHub
  and a fake checkout. That is 1,635 passing tests, and it is genuinely weaker evidence than it
  sounds: every defect the four live attempts found was invisible to the suite, because each lived
  at a boundary the fakes replace.
- So: expect the first live run of `ask`, `audit` and the inbox to find something. Report it rather
  than working around it — that is the most useful thing you can do in the first week.
