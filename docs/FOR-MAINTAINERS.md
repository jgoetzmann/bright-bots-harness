# For maintainers

You have been given level 2 on an automated agent that works on
[`Bright-Bots-Initiative/brightboost`](https://github.com/Bright-Bots-Initiative/brightboost). This
page is the whole of what you need to use it. It takes about five minutes and assumes you will never
open a terminal.

If you want the depth instead, [COMMANDS.md](COMMANDS.md) has every command with a worked
example, and [USING.md](USING.md) is the reference behind both.

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

**Or comment on the brightboost issue itself**, addressing the bot:

```
@jgoetzmann-bot /harness work
```

The mention is not politeness there — it is the delivery mechanism. The harness finds comments on
brightboost by reading its notifications, and it is not subscribed to an issue it has never touched,
so the mention is the only thing that makes the thread visible to it at all. On *this* repository
you do not need it.

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

- `/harness revise <what to change>` → one more implementation pass against your feedback,
  re-running the complete gate sequence.
- `/harness rebase` → same, after a conflict.
- `/harness stop` → close it and stand down.

All of those go **on the pull request**, as a normal review comment. `revise` and `stop` are the
same two verbs you use at gate 1 — the pull request you are standing on says whether "redo it" means
rewrite the plan or rewrite the code, so there is no second word to remember.

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

Put any of these at the **start of a line** in a comment — after an `@mention` if you are
addressing the bot, and in any capitalisation your phone gives you. `/harness-ask` works as well as
`/harness ask`, and **several commands go in one comment, one per line.**

| Command | Where | What it does |
|---|---|---|
| `/harness ask <question>` | anywhere | reads brightboost and answers, in the thread. **Changes nothing at all** |
| `/harness audit <lens>` | an issue in the harness repo | reads brightboost through one lens and opens **one** findings issue. Creates no work |
| `/harness promote 3` · `promote all` | on that audit issue | turns findings into work items |
| `/harness go` | a suggestion, or a parked item | green-lights it, or puts it back in the queue |
| `/harness split` | a work item | breaks it into sub-issues |
| `/harness status` | anywhere | spend, queue, and when the next thing happens |

`ask` is the cheap one and the one to start with. It costs cents, changes nothing, and is the
fastest way to find out whether the thing understands the codebase — ask it something you already
know the answer to.

`audit` and `promote` are deliberately two steps. One sentence from you must not become eight
proposals nobody approved: an audit produces a *list*, and you choose which lines become work.

`status` is the one to reach for when nothing seems to be happening — it says whether the harness is
halted, whether the run window is open, and when the next sweep is.

There are twelve verbs in all, and every reply the harness sends points at the rest of them. Six
other words are understood too: `fix`, `reject`, `queue` and `usage` mean `revise`, `stop`, `go`
and `status`, and so do `ledger` and `help` — if you are not sure, `/harness help` is a real
thing to type.

## 6. What you cannot do, and why

`halt`, `resume` and `--force` are level 3 (Jack only).

`--force` starts work now instead of at the next run window. The window — Monday 08:00 to Tuesday
20:00 UTC — is the main thing standing between an enthusiastic week and an exhausted allowance.
You can queue as much work as you like; whether it happens tonight is the operator's call, because
it is the operator's subscription.

`halt` stops the harness spending anything at all until it is resumed, which is why it sits at
the same level as the switch that starts work early.

If you use one anyway, nothing happens and you get a reply saying which level it needed. Refusals
are answered, never silent.

## 7. If you comment and *nothing at all* happens

Before assuming it is broken: the gate that lets you command it has two halves, and the second
is invisible from your side.

1. Your handle is in [`.harness/trust.txt`](../.harness/trust.txt), with a level.
2. GitHub reports you as `OWNER`, `MEMBER` or `COLLABORATOR` on the repository you commented on —
   which in practice means **you have been invited to it**.

A comment that fails either half is read, counted as denied, and ignored. There is no reply,
because replying to anyone who types `/harness` on a public repository is how a bot becomes a
nuisance.

So total silence means one of these, most likely first:

1. **The kill switch is on.** If `.harness/HALT` exists on `main`, every spending workflow exits
   before it does anything — the run goes green and nothing happens. The weekly heartbeat says so
   in a banner at the top; that is the fastest way to check.
2. **You are missing half the gate** — most often the invite, not the trust-file line.
3. **The sweep has not run yet.** On brightboost that is up to three hours, and over a weekend
   until Monday. See below.

A verb you do not have the level for is *not* on this list: that one replies, and names the level
it needed.

`harness doctor` names anyone in the trust file who has no access, which is the only way to see
that half without asking someone to test it for you.

## 8. Latency — why nothing happened yet

On the **harness repository**, a comment wakes the job directly. Minutes.

On **brightboost**, the harness gets no events — it is not a collaborator there, and it must not be.
It finds your comment by reading its notifications every three hours on weekdays. A comment left on
Saturday afternoon waits until Monday morning. That is the design, not a fault. To skip the wait,
ask Jack, or run `feedback` from the Actions tab.

## 9. If something looks wrong

**To stop everything:** commit a file called `.harness/HALT` on `main`. Any content. Every spending
workflow then exits before the dispatcher and before a single token. Deleting the file resumes.
One commit either way, doable from a phone. You do not need permission and you will not break
anything by using it.

**A pull request that looks wrong** is just a pull request. Close it.

**Anything stranger** — a stuck item, a run that keeps failing, spend that looks off —
[OPERATIONS.md](OPERATIONS.md) has the diagnosis order, and §8 is how to stop everything.

## 10. What is not yet true

Being straight about the state of it:

- It has completed the whole flow **once**, live:
  [`brightboost#868`](https://github.com/Bright-Bots-Initiative/brightboost/pull/868), all six
  brightboost checks green. That took four attempts, and each failed attempt found a real defect in
  the harness rather than in the change it was delivering.
- Everything on this page after that first run has been tested against a fake model, a fake GitHub
  and a fake checkout. That is over 1,600 passing tests, and it is genuinely weaker evidence than
  it sounds: every defect the four live attempts found was invisible to the suite, because each lived
  at a boundary the fakes replace.
- So: expect the first live run of `ask`, `audit` and the inbox to find something. Report it rather
  than working around it — that is the most useful thing you can do in the first week.
