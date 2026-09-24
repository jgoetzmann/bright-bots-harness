# For maintainers

You have been given level 2 on an automated agent that works on
[`Bright-Bots-Initiative/brightboost`](https://github.com/Bright-Bots-Initiative/brightboost). This
page is what you need to use it, without a terminal. [COMMANDS.md](COMMANDS.md) has every command
with an example.

## 1. What it is

It takes an issue on brightboost to a reviewable pull request and then waits for a person. It writes
code, runs the repository's lint, typecheck, build and test commands, and opens a pull request from
a fork.

**It merges nothing**, neither its plans nor its code. On brightboost it holds the Triage role,
which cannot push or merge, and its own repository requires a review. Every path ends at a human
clicking merge.

**It touches only its own threads on brightboost.** It labels the tracking issues it files and
requests your review on its own pull requests; it never closes, relabels or locks anybody else's
issue, and each of those writes checks who opened the thread first. While two of its pull
requests are waiting for you, it starts implementing nothing new; plans and revisions go on.

## 2. The one gesture

Comment on the pinned [inbox issue](https://github.com/jgoetzmann/bright-bots-harness/issues/19):

```
/harness work make the activity cards keyboard reachable
/harness work https://github.com/Bright-Bots-Initiative/brightboost/issues/633
```

A sentence or a link to a brightboost issue is enough. Within one sweep a work item opens as its own
issue and a reply in the inbox links to it. The same words or link find that item again. That
**queues** it; §9 says when it becomes a plan, and when code.

**Never put a credential, token or private data in a work item.** What you write is read by a model
and copied into the run's transcript, and the item itself is a public issue.

**Or comment on the brightboost issue itself**, mentioning the bot: `@jgoetzmann-bot /harness work`.
The harness finds brightboost comments through its notifications, and an issue it has never touched
sends none without the mention. Assigning the bot rarely helps: GitHub offers it as an assignee only
where it has access or is already in the thread (D68), and nearly every issue it is in already has
a work item.

### What to hand it

Good tickets are defects or cleanups where:

- the cause lives in files that already exist (every path a proposal names must exist at the base
  commit, so a change made only of new files cannot be proposed);
- the correct behaviour can be settled from the repository alone, and lint, typecheck, unit tests
  and build can show the fix without a live database, a deployed environment or a credential;
- the fix is a handful of files, with no schema change, migration, CI change or dependency bump;
- no open branch or pull request is already chasing it.

Deleting dead code, a misreported number, a test that asserts the wrong thing and a docs fix are the
shape that works. Keep it away from anything that needs a product decision or touches `prisma/`,
`migrations/`, `backend/scripts/predeploy*` or `.github/workflows/`. The harness refuses changes
under `.github/`; nothing enforces the rest, so look for them at gate 1. `intern-starter`, `large`
and `architecture` keep an issue out of the pool (§5), but `/harness work` on one queues it anyway.
And keep it away from anything you would not merge if a stranger opened the same pull request.

## 3. Your two moves

The harness needs you at two moments, and both labels are orange.

**`stage:needs-approval` — gate 1.** A pull request in the harness repository adding
`proposals/<id>-<slug>.md`: a plan with the diagnosis, the approach, the files it will touch, and
how a reviewer will know it worked. No code has been written.

- **The merge is Jack's.** You have no access to the harness repository (D30, D68), so you cannot
  merge or close the pull request. If the plan is right, say so in a plain comment; Jack reads it.
- `/harness revise <what is wrong>` rewrites the plan with your note as the brief.
- `/harness stop` closes the pull request and parks the item; `/harness go` puts it back.
- On a proposal nobody asked for (`via:suggested`), answer as §5 says.

**`stage:needs-review` — gate 2.** A pull request on brightboost, from the fork, with the diff and
the gate results in the body. Review it like any other contributor's PR. When the work came
without a brightboost issue, such as an audit finding, the harness files one as it delivers,
titled `harness-tracking(#<item>): …` and labelled `harness-tracking` once that label exists on
brightboost, names it on the harness issue, and the pull request closes it when merged. Comment on
the pull request, not on that tracking issue.

- `/harness revise <what to change>`: one more implementation pass, then the complete gate sequence.
- `/harness rebase`: the same, after a conflict.
- `/harness stop`: close it and stand down.

Put commands in the Conversation tab's comment box or in an inline comment on the diff. The *Review
changes* summary box is never read for commands (D68). Once a `/harness revise` has arrived on a
delivery pull request, your reviews are read as feedback too.

### Reading a proposal

The pull request body contains the whole proposal. Check, in this order:

1. **Diagnosis.** Is the stated problem the real one? It cites files and lines; open one or two.
2. **`upstream_issue`.** Does it point at the issue you meant?
3. **Open questions.** Anything listed means the plan is not decided. Answer them with
   `/harness revise` rather than approving.
4. **Acceptance criteria.** Each line must be checkable on its own; the delivery marks each one met
   or not met against the gate output.
5. **`gate_expectation` and `baseline_red`.** `known-red` means some brightboost gates are expected
   to be red before any change, and names them. If that surprises you, tell Jack.
6. **`depends_on`.** Issues that must reach `stage:done` first.

[PACKAGE-FORMAT.md](PACKAGE-FORMAT.md) describes every section.

## 4. Reading the board at a glance

`stage:` says where an item is and whose move it is, `kind:` what it is (`product`, `audit`, `ops`),
and `via:` how it arrived (`assigned`, `requested`, `suggested`, `audit`). The two orange stages are
the gates. Two red ones also wait on a person:

- **`stage:blocked`**: stopped, needs a decision. Items you parked with `stop` are here, and so are
  items whose gates went red in a way the harness could not fix. `/harness go` sends one round
  again; if you do not know why it stopped, ask Jack.
- **`stage:needs-human`**: a delivery pull request has used its three revision rounds. Only
  `/harness revise <notes>` on that pull request restarts it.

The other stages are the harness's own moves; [COMMANDS.md](COMMANDS.md#labels) lists them all.

## 5. The pool — what it may pick up unasked

**Label a brightboost issue `harness-ok` and it is in the pool**, the only issues the harness may
choose for itself. Remove the label and it is out.

It looks once a day, on `discover`'s 11:07 UTC run, and suggests nothing unless both hold:

- **Nothing anybody asked for is still open**, including a delivery pull request waiting for your
  review.
- **The week has room**: subscription usage under 50% (`SUGGEST_MIN_HEADROOM_PCT`), or not yet
  observed.

One model call ranks the pool and at most five (`SUGGEST_MAX_PER_RUN`) become work items. An issue
is skipped if it is assigned to anyone but the bot, already named by a branch or open pull request,
labelled `intern-starter`, `large` or `architecture`, or has ever had a work item, so a suggestion
you turned down is not offered again.

Each suggestion is proposed in the same run, and the harness comments on its brightboost issue
saying it has a plan and has not started. **Jack's merge at gate 1 builds it, whether or not you
answered.**

- **Yes:** a plain comment on the proposal pull request, or tell Jack. Do not type `/harness go`,
  even though the comment invites it: before his merge it approves an item with no plan to build
  from, and every build run in the window fails until he merges (D68).
- **No:** `/harness stop` on the proposal pull request, which closes it and parks the item. The same
  command on the brightboost issue leaves the pull request open. Removing `harness-ok` does not
  withdraw a proposal already open.

## 6. The rest of the vocabulary

Commands go at the **start of a line**, in any capitalisation, and several can go in one comment,
one per line. **Naming the bot is a command too**: `@jgoetzmann-bot status` does what
`/harness status` does, anywhere the harness reads, and naming it with no verb gets a short reply
telling you what to say there.

| Command | Where | What it does |
|---|---|---|
| `/harness ask <question>` | anywhere | reads brightboost and answers in the thread; changes nothing |
| `/harness audit <lens>` | an issue in the harness repo | opens one findings issue; creates no work |
| `/harness promote 3` · `promote all` | on that audit issue | turns findings into work items |
| `/harness go` | a parked item | puts it back in the queue; not on a suggestion (§5) |
| `/harness split` | a work item | breaks it into sub-issues |
| `/harness status` | anywhere | usage, queue, halt state, run window, next sweep |

Start with `ask`: asking something you already know shows whether it understands the codebase.
Reach for `status` when nothing seems to be happening. `fix`, `queue` and `usage` also work, as
`revise`, `go` and `status`, and so do `ledger` and `help`.

## 7. What you cannot do

`block`, `halt`, `resume`, `reject` and `--force` are level 3 (Jack only). `--force` exempts one
item from the run window, daily 11:00 to 19:00 UTC; `halt` stops all spending; `block` does the
window's job in reverse, suspending it for a few five-hour sessions so the harness can use time the
operator is not going to. You can queue as much work as you like, but when it runs is the operator's
call, because it is the operator's subscription. If you use one of these, the reply names the level
needed; a `--force` is dropped and the rest of the command still runs.

## 8. If you comment and *nothing at all* happens

The gate that lets you command it has two halves:

1. Your handle is in [`.harness/trust.txt`](../.harness/trust.txt), with a level.
2. GitHub confirms who is typing: **either** your line vouches for your numeric account id
   (`vouch:<id>`), which works on every repository, **or** GitHub reports you as `OWNER`, `MEMBER`
   or `COLLABORATOR` on the repository you commented on.

Jack adds a vouched line with `harness trust line <your-login> --level 2`. A comment that fails
either half gets no reply. So silence means one of these, most likely first:

1. **The kill switch is on.** With `.harness/HALT` on `main`, every spending workflow exits before
   doing anything; the weekly heartbeat comment says so in a banner. On the harness repository your
   comment still gets its 👀.
2. **You are missing half the gate**, usually the vouch.
3. **It never saw the comment**: a `/harness` line in a *Review changes* summary box (§3), or a
   comment on an untouched brightboost issue without the `@jgoetzmann-bot` mention (§2).
4. **The sweep has not run yet**: up to three hours on brightboost, longer over a weekend (§9).

A verb above your level is not on this list: that one gets a reply.

## 9. How long one request takes

**Being heard.** On the **harness repository** a comment wakes the job directly: a 👀 within
seconds, the answer in minutes, or up to two hours while a build holds the shared lock. On
**brightboost** the harness reads its notifications every three hours on weekdays (00:41, 03:41 …
21:41 UTC); a comment left after Friday 21:41 waits until Monday 00:41. To skip the wait, post any
`/harness` command on the inbox (`/harness status` will do), which reads your brightboost
notifications on the way.

| Step | When |
|---|---|
| 1. `/harness work` is heard | minutes on the inbox, up to three hours on brightboost; it opens a `stage:queued` item |
| 2. The plan | the next `discover` run, **11:07 UTC every day**, unless Jack runs one sooner |
| 3. Gate 1 | whenever Jack merges the proposal |
| 4. The code | inside the run window, **daily 11:00 to 19:00 UTC** (3–4 a.m. to 11 a.m.–noon Pacific); a merge outside it waits for the next 11:23 UTC run unless Jack starts it |
| 5. Gate 2 | the delivery pull request on brightboost is yours |

Each scheduled build run starts one item, and on weekdays the 12:41 UTC sweep also builds what is
approved. A busy morning is limited by the subscription session, which the harness stops using at
80%. Worst case, with nothing halted or ahead of it, a request queued just after 11:07 UTC and
merged after the window closes reaches a delivery pull request in about two days; a merge before
19:00 UTC saves a day.

## 10. If something looks wrong

**To stop one thing:** `/harness stop` on its pull request or work item.

- On a proposal or delivery pull request, and on anything `stage:planning`, `stage:building`,
  `stage:packaged` or `stage:revising`, it **parks** the item in `stage:blocked`; `/harness go`
  puts it back.
- On an item that is `stage:queued`, `stage:ready`, `stage:blocked` or `stage:needs-human` there is
  nowhere to park it, so it **ends it for good**, and the reply says so. A fresh `/harness work`
  sits in `stage:queued` for up to a day, so this is the stop you are most likely to make. If you
  only want it later, tell Jack instead.

**To stop everything, ask Jack.** `/harness halt` is level 3, and committing `.harness/HALT` needs
write access to the harness repository (D30). **A pull request that looks wrong** can simply be
closed. For anything stranger, [OPERATIONS.md](OPERATIONS.md) has the diagnosis order and
[SAFETY.md](SAFETY.md) what the harness will never do.
