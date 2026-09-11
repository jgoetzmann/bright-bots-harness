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
link to it. Asking twice is one item, not two. That **queues** it; §9 says when it becomes a plan,
and when code.

**Or assign `@jgoetzmann-bot` to a brightboost issue it has already commented on.** That is the
same gesture in the place you already work, and it needs no comment. It works only there: GitHub
offers an account in the Assignees box when it is a collaborator, an organisation member or already
in the thread, and on brightboost the bot is only ever the third (D68). On a fresh issue, use the
inbox or the comment below.

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

- Merge it → the plan is approved and implementation starts. **The merge is Jack's.** You have
  no access to the harness repository, by design (D30, D68), so you can neither merge nor close
  the pull request. If the plan is right, say so on it; a comment without `/harness` is for
  people, and Jack reads it.
- Comment `/harness revise <what is wrong>` → it rewrites the plan with your note as the brief.
- Comment `/harness stop` → it closes the pull request and parks the item. Parked, not ended:
  `/harness go` puts it back in the queue for a fresh proposal.
- A proposal nobody asked for (`via:suggested`, §5) is the one place your word is asked for:
  `/harness go` on it is your yes. It is still built only after Jack merges it — see §5.

**`stage:needs-review` — gate 2.** A pull request on brightboost, from the fork, with the real diff
and every gate's output in the body. Read it like any other contributor's PR.

- `/harness revise <what to change>` → one more implementation pass against your feedback,
  re-running the complete gate sequence.
- `/harness rebase` → same, after a conflict.
- `/harness stop` → close it and stand down.

All of those go **on the pull request**: in the comment box at the foot of the Conversation tab, or
as an inline comment on a line of the diff. **Not in the *Review changes* summary box.** The harness
never reads that box for commands, so a `/harness` line typed there gets no reply (D68). Once a
`/harness revise` has arrived on a delivery pull request, it does read your reviews as feedback.

`revise` and `stop` are the
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

`via:suggested` is worth knowing: it means **nobody asked for this**. The harness picked it from
the pool in §5, and it says so in the issue. Ignoring one is a complete answer.

## 5. The pool — what it may pick up unasked

**Label a brightboost issue `harness-ok` and it is in the pool**: the only issues the harness may
choose for itself. Take the label off and it is out. That is a gesture on your own repository — no
command, no reply; the harness reads the labels when it next looks. Jack is labelling a first
batch of about forty, mostly `pod: build`.

It looks **once a week**, on `discover`'s Sunday 07:17 UTC run (or when Jack runs one by hand), and
even then suggests nothing unless both of these hold:

- **Nothing anybody asked for is still open.** Any requested, assigned or promoted item that is
  queued, being planned or built, or waiting at either gate holds the pool shut — and that
  includes a delivery pull request waiting for your review. While brightboost#868 waits on you,
  it alone keeps the pool shut.
- **The week has room.** Subscription usage under 50% for the week (`SUGGEST_MIN_HEADROOM_PCT`).
  The back half is kept for work somebody asked for.

Then one model call ranks the pool and **at most five** (`SUGGEST_MAX_PER_RUN`) become work items.
A labelled issue is skipped if it is assigned to anyone but the bot, if a branch or open pull
request already names it, or if it carries `intern-starter`, `large` or `architecture` — those
three beat the label.

Each suggestion is proposed in the same run, and the harness comments once on its brightboost
issue saying it has a plan and has not started. `/harness go` there is your yes; ignoring it is a
complete no. Either way **nothing is built until Jack merges the proposal at gate 1** — the plan
the build reads is the merged file, so a `go` before his merge only moves the item to
`stage:ready`, and the next build run fails looking for a plan that is not there yet (D68). Say
`go`, and tell him. The comment also says "Assign me"; on an issue it has already suggested,
assigning does nothing more — use `/harness go`.

## 6. The rest of the vocabulary

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
other words are understood too: `fix`, `queue` and `usage` mean `revise`, `go` and `status`, and
so do `ledger` and `help` — if you are not sure, `/harness help` is a real thing to type. The
sixth, `reject`, is the operator's terminal form of `stop`: it needs level 3, so from you it is
refused with a reply saying so. Your `stop` parks instead, which `/harness go` undoes.

## 7. What you cannot do, and why

`halt`, `resume`, `reject` and `--force` are level 3 (Jack only).

`--force` exempts one item from the run window — Monday 08:00 to Tuesday 20:00 UTC — which is the
main thing standing between an enthusiastic week and an exhausted allowance. You can queue as much
work as you like; whether it happens tonight is the operator's call, because it is the operator's
subscription.

`halt` stops the harness spending anything at all until it is resumed, which is why it sits at
the same level as the switch that starts work early.

If you use one anyway, nothing happens and you get a reply saying which level it needed. Refusals
are answered, never silent.

## 8. If you comment and *nothing at all* happens

Before assuming it is broken: the gate that lets you command it has two halves, and the second
is invisible from your side.

1. Your handle is in [`.harness/trust.txt`](../.harness/trust.txt), with a level.
2. GitHub reports you as `OWNER`, `MEMBER` or `COLLABORATOR` on the repository you commented on —
   which in practice means **you have been invited to it** — or your trust-file line vouches for
   your account (`vouch:` and your numeric GitHub user id), which stands in for this half on
   every repository (D68). `BrightBoost-Tech` is vouched, so this half is already met for it.

A comment that fails either half is read, counted as denied, and ignored. There is no reply,
because replying to anyone who types `/harness` on a public repository is how a bot becomes a
nuisance.

So total silence means one of these, most likely first:

1. **The kill switch is on.** If `.harness/HALT` exists on `main`, every spending workflow exits
   before it does anything — the run goes green and nothing happens. The weekly heartbeat says so
   in a banner at the top; that is the fastest way to check.
2. **You are missing half the gate** — most often the invite or the vouch, not the trust-file
   line.
3. **The sweep has not run yet.** On brightboost that is up to three hours, and over a weekend
   until Monday. See §9.

A verb you do not have the level for is *not* on this list: that one replies, and names the level
it needed.

`harness doctor` names anyone in the trust file who has neither access nor a vouch, which is the
only way to see that half without asking someone to test it for you.

## 9. How long one request takes

**Being heard.** On the **harness repository**, a comment wakes the job directly: a 👀 within
seconds, the answer in minutes. On **brightboost**, the harness gets no events — it is not a
collaborator there, and it must not be — so it reads its notifications every three hours on
weekdays (00:41, 03:41 … 21:41 UTC). A comment left after Friday 21:41 waits until Monday 00:41.
That is the design, not a fault. To skip the wait, ask Jack, or run `feedback` from the Actions tab.

**Then five steps, and only the first is quick:**

| Step | When |
|---|---|
| 1. `/harness work` is heard | minutes on the inbox, up to three hours on brightboost. It opens a `stage:queued` item and **only queues it** |
| 2. The plan | the next `discover` run — **Sunday 07:17 UTC** — proposes everything queued, unless Jack runs one sooner |
| 3. Gate 1 | whenever Jack merges the proposal |
| 4. The code | only inside the run window, **Monday 08:00 to Tuesday 20:00 UTC**. A merge inside it is built straight away; a merge outside it waits for Monday 08:17. Only Jack can make it sooner (`--force`, or starting the build by hand) |
| 5. Gate 2 | the delivery pull request on brightboost is yours |

One item is built per run and the window holds six scheduled runs, so a busy week queues.

**A Wednesday request, worst case, nothing stopped and nothing ahead of it:** queued on Wednesday,
planned Sunday morning, built Monday morning if Jack has merged the plan by then — **five days** to
a delivery pull request. If his merge misses Tuesday 20:00, the build waits for the next Monday:
**twelve days**. Longer only if something is halted, a usage stop is hit, or other work is ahead.

## 10. If something looks wrong

**To stop one thing:** `/harness stop` on its pull request or work item. At your level that parks
it, reversibly, and that is enough to keep anything out of brightboost.

**To stop everything, ask Jack.** Both fleet-wide switches are his. `/harness halt` is level 3.
The other is a commit of `.harness/HALT` on the harness repository's `main`, and that needs write
access you do not have, by design (D30). A pull request adding the file from a fork does nothing
until Jack merges it, because `.harness/` is protected.

**A pull request that looks wrong** is just a pull request. Close it.

**Anything stranger** — a stuck item, a run that keeps failing, spend that looks off —
[OPERATIONS.md](OPERATIONS.md) has the diagnosis order, and §8 is how to stop everything.

## 11. What is not yet true

Being straight about the state of it, as of 11 September 2026:

- It has delivered **once**:
  [`brightboost#868`](https://github.com/Bright-Bots-Initiative/brightboost/pull/868), open since
  4 September and **waiting for your review**. Its own seven gates were green on the runner. That
  took four attempts, and each failed attempt found a real defect in the harness rather than in
  the change it was delivering.
- On the inbox, `ask`, `status` and `go` have run live (9–10 September). `audit`, `promote`,
  `split`, a `revise` on a delivery pull request, a suggestion from the pool, and **any command
  from your account** have not — the vouch that lets you command it is new today.
- Your first `/harness revise` on a delivery pull request may end `stage:blocked`, blaming a gate
  that was already red on brightboost. The baseline it compares against is kept somewhere that
  does not survive between runs, so a pre-existing red reads as new — a known gap, recorded in
  DECISIONS.md. If that happens, tell Jack rather than retrying.
- Everything else has been tested against a fake model, a fake GitHub and a fake checkout. That
  is over 1,900 tests, and it is weaker evidence than it sounds: every defect the live runs found
  lived at a boundary the fakes replace.
- So: expect the first live run of each of those to find something. Report it rather than working
  around it — that is the most useful thing you can do in the first week.
