# Delivery 4 — asking for work, and knowing what you are looking at

> Frozen 2026-09-04. Behaviors **B235–B268**. Decisions continue from **D54**.
> Supersedes nothing. Every invariant I-1…I-17 and every earlier behavior stays in force.

## 1. Why

Three separate complaints, one shape underneath them.

**You cannot ask for work.** The harness finds work one way — a weekly triage of product-repository
issues carrying the `harness-ok` allowlist label — and *nothing carries that label*. Delivery 3's
acceptance added assignment (B233) as a second way in, which works, but it only says "this exact
issue". There is no way to say "look at this area", "here is a link, do that one", or "go and find
me something".

**Work it finds cannot reach you where you are.** When the harness proposes something, the proposal
lands in this repository. If the work came from a product-repository issue, nobody watching that
issue learns anything. And the harness cannot hear a reply on a thread it has never spoken on:
`keywords.sweep` reads *notifications*, so a `/harness` comment on a product issue the harness is
not subscribed to is not ignored — it is never seen.

**You cannot tell what you are looking at.** Every harness issue wears one label from one flat
family. `harness:proposed` and `harness:shipped` both mean "somebody has to do something" but do
not say who. `harness:ops` is not a stage at all. Nothing distinguishes work on the *product* from
work on the *harness itself*, or work you asked for from work it guessed at.

## 2. The one new concept

A **request**: something said "work on this", and the harness must turn it into work.

Four sources, differing only in how much judgement the turning takes:

| Source | Judgement | Status |
|---|---|---|
| Assign the machine account to a product issue | none — you named it | shipped (B233) |
| Comment `/harness` on a product issue or PR | none | **new** |
| Comment on the inbox issue here | some — prose, not a link | **new** |
| `/harness audit <lens>` | a lot — it has to *find* the work | **new** |

All four converge on the queue that already exists: one issue in this repository per unit of work,
carrying its stage label. Nothing new is invented to hold work.

### 2.1 The inbox is not the record — D55

**Decision.** A pinned issue in this repository is an **inbox**. You comment on it; the harness
replies in-thread with a link and opens a **normal work-item issue**. The thread holds the
conversation; the issue holds the state.

**Why, and why not the alternatives.** The queue is already "one issue, one stage label", and the
dispatcher, the store, `find_by_ref`, the sweep and every workflow are built on that. A request that
becomes work should become one of those, not a new species. Sub-issues would still be issues, so the
queue would work — but the parent link buys nothing a label and a back-reference do not already give,
at the cost of a newer API surface. A comment thread *as* the record is the worst of the three: no
per-item state, no labels, unbounded growth, and two concurrent requests interleave into one
unreadable log.

A hand-opened issue in this repository carrying a stage label is already a valid work item and stays
one. The inbox is for when a sentence is easier than a form.

## 3. Tagging — three axes, three questions — D56

One flat family cannot answer three different questions, so there are three families. Every issue
and pull request the harness opens carries exactly one label from each.

### 3.1 `stage:` — where is it, and whose move is it?

Renamed from `harness:*`. The state machine, `STATES` and `TRANSITIONS` are **unchanged**; only the
label strings move, and `LABELS` is the single map that has to change.

| state (unchanged) | old label | **new label** | means |
|---|---|---|---|
| `discovered` | `harness:queued` | `stage:queued` | found; nothing spent yet |
| `proposing` | `harness:proposing` | `stage:planning` | writing the plan |
| `proposed` | `harness:proposed` | **`stage:needs-approval`** | **your move** — gate 1 |
| `approved` | `harness:approved` | `stage:ready` | approved; waiting for a runner |
| `implementing` | `harness:running` | `stage:building` | implementing now |
| `packaged` | `harness:packaged` | `stage:packaged` | built; delivery pending |
| `shipped` | `harness:shipped` | **`stage:needs-review`** | **a human's move** — gate 2 |
| `revising` | `harness:revising` | `stage:revising` | reworking after feedback |
| `merged` | `harness:merged` | `stage:done` | merged upstream |
| `blocked` | `harness:blocked` | `stage:blocked` | stopped; needs a decision |
| `needs-human` | `harness:needs-human` | `stage:needs-human` | retries spent; will not retry |
| `abandoned` | `harness:abandoned` | `stage:dropped` | closed without shipping |

The two renames that carry their weight are `needs-approval` and `needs-review`. Everything else is
the harness's business; those two are yours. `proposed` and `shipped` never said so.

### 3.2 `kind:` — what is this thing?

| label | means |
|---|---|
| `kind:product` | work on `Bright-Bots-Initiative/brightboost` |
| `kind:harness` | meta work on the harness itself |
| `kind:audit` | a findings report, not a unit of work |
| `kind:ops` | a failed run; replaces `harness:ops` |

### 3.3 `via:` — how did it get here?

| label | means |
|---|---|
| `via:assigned` | the machine account was assigned to the product issue |
| `via:requested` | a trusted human asked, in a comment |
| `via:suggested` | the harness found it unprompted |
| `via:audit` | it came out of an audit's findings |

`via:suggested` is the one that governs behaviour, not just display: see §5.

### 3.4 Migration

The rename is breaking for anything holding a label string. One migration command,
`harness relabel`, rewrites every open issue in this repository from the old family to the new one
and creates the `kind:`/`via:` labels. It is idempotent, it never changes a state, and it refuses to
run while a job is in flight. `init --labels` creates the full set for a fresh repository.

## 4. Audits — D57

### 4.1 Shape: findings first, work second

**Decision.** An audit produces **one issue** — a ranked findings report labelled `kind:audit` —
and nothing else. You turn findings into work items by replying to it. Each then goes through the
ordinary proposal gate.

**Why.** One sentence from you must not become eight proposals and eight implement runs
unsupervised. Both human gates stay intact and the "do a part, propose the rest" loop falls out for
free: the audit issue stays open with its findings ticked off as they are promoted, and it is the
record of what remains.

### 4.2 Scope and budget

**Decision.** A separate `AUDIT_CAP_USD`, default **20.00**, and the command takes a scope.

```
/harness audit accessibility in src/components/activities
/harness audit redundant code
```

Bounded by money and by surface area. `PER_CALL_CAP_USD` does not apply to the audit call; every
other cap and both usage stops do. An audit that stops on its cap says what it did **not** reach, so
the next one can continue from there rather than starting over.

### 4.3 Findings

Each finding is one line with a title, the paths it concerns, a severity, and one sentence on why it
matters. A finding is **not** a work package — it names a problem, not a plan. Promoting one creates
a work item; proposing that item is what writes the plan, with the full machinery that already has.

## 5. Suggested work, and asking for the green light — D58

**Decision.** Weekly discovery runs only when the queue holds nothing actionable, and may queue and
propose **at most five** items, labelled `via:suggested`.

For each one that came from an unassigned product-repository issue, the harness comments **on that
issue**:

> I have worked out how I would implement this — a plan is at `<link>`. I have not started, and I
> will not without a green light. Assign me, or reply `/harness go`.

**Why this is worth the outward write.** It is the one thing that makes the harness discoverable to
the people who own the work, and it is honest about its own status: a proposal, not a change, and a
request, not a claim. It is also load-bearing mechanically — see §6.1.

**The bound that makes it safe:** at most five per week, only when the queue is idle, only on issues
nobody is assigned to, and never twice on the same issue.

## 6. Reaching the harness from the product repository — D59

**Decision.** `/harness` commands are honoured on product-repository issues and pull requests, not
only here.

### 6.1 The subscription problem, and why commenting solves it

`keywords.sweep` reads `GET /notifications` and then the comments of each notified thread. The
harness is notified about a product thread only when it is assigned, @-mentioned, or already
participating. So:

- A `/harness` comment on a thread the harness has never touched **is never seen** — not ignored,
  unseen.
- A comment the harness posts subscribes it to that thread. Every later reply then reaches it.

This is why §5's green-light comment is mechanism and not decoration: it is what makes the reply it
asks for arrive. An `@jgoetzmann-bot` mention does the same for a cold thread, and the documentation
must say so plainly.

### 6.2 The bug this must not inherit

`keywords.thread_target` returns `None` for an `Issue` notification from any repository but this
one, and `_item_for_command` maps `surface == "issue"` straight to `int(cmd.number)`. Loosening the
first without fixing the second would make `/harness queue` on product issue **#633** act on harness
work item **#633** — different repository, same number, wrong item. A new surface,
`product_issue`, resolves through `find_by_ref("issue:<n>")` instead.

## 7. Configuration

| Key | Default | Meaning |
|---|---|---|
| `INBOX_ISSUE` | *(empty)* | issue number in this repository used as the request inbox; empty disables it |
| `AUDIT_CAP_USD` | `20.00` | ceiling for one audit call; `PER_CALL_CAP_USD` does not apply |
| `SUGGEST_MAX_PER_RUN` | `5` | most items weekly discovery may queue and propose when idle |
| `COMMENT_UPSTREAM` | `true` | whether the harness may comment on product-repository threads |

`INBOX_ISSUE` and `AUDIT_CAP_USD` join the sixteen keys `.harness/config.json` may override.
`COMMENT_UPSTREAM=false` must disable every outward comment without disabling delivery.

## 8. Behaviors

Each must be cited by a test that names it.

### Requests and the inbox
- **B235.** A trusted comment on `INBOX_ISSUE` containing `/harness work <text>` creates one work item, `kind:product` `via:requested` `stage:queued`.
- **B236.** The harness replies in the inbox thread with a link to the item it created.
- **B237.** An untrusted comment on the inbox creates nothing and is recorded as denied.
- **B238.** `/harness work <url>` naming a product-repository issue creates the item with `external_ref="issue:<n>"`, exactly as directed discovery would, and does not duplicate an existing item.
- **B239.** `/harness work` with neither text nor a resolvable link replies asking for one and creates nothing.
- **B240.** `INBOX_ISSUE` empty: the inbox is inert and nothing errors.
- **B241.** The inbox issue is never itself treated as a work item, whatever labels it carries.

### Commands from the product repository
- **B242.** `thread_target` returns surface `product_issue` for an `Issue` notification from `UPSTREAM_REPO`.
- **B243.** A command on a `product_issue` resolves to the work item through `find_by_ref("issue:<n>")`, never through the issue number.
- **B244.** A command on a product issue with no work item and verb `work` creates one; any other verb replies that there is nothing to steer.
- **B245.** The trust gate is unchanged on the new surface: handle in `trust.txt` **and** an OWNER/MEMBER/COLLABORATOR association, both.
- **B246.** A command on a product **pull request** that is not a harness delivery is ignored.

### Audits
- **B247.** `discover --mode audit --lens <text>` no longer raises; it opens one issue labelled `kind:audit` `via:requested`.
- **B248.** The audit issue lists findings, each with a title, paths, severity and one sentence.
- **B249.** The audit call is authorised against `AUDIT_CAP_USD`, not `PER_CALL_CAP_USD`.
- **B250.** An audit stopped by its cap records what it did not reach, and the issue says so.
- **B251.** An audit creates **no** work items.
- **B252.** `/harness promote <n>[,<n>…]` on an audit issue creates one work item per named finding, `via:audit`, and ticks them off in the issue body.
- **B253.** `/harness promote all` promotes every unticked finding, subject to `SUGGEST_MAX_PER_RUN`.
- **B254.** Promoting a finding twice creates one item, not two.
- **B255.** An audit issue is never a work item and never enters the queue.
- **B256.** `--mode audit` with no lens is refused before any model call.

### Suggested work and the green light
- **B257.** Weekly discovery does nothing when any item is in an actionable stage.
- **B258.** When idle it queues at most `SUGGEST_MAX_PER_RUN` items, labelled `via:suggested`.
- **B259.** For each suggestion from an unassigned product issue, one comment is posted there naming the proposal and asking for a green light.
- **B260.** That comment is posted once per issue, ever; a second run does not repeat it.
- **B261.** No comment is posted on an issue that has an assignee.
- **B262.** `/harness go` on such an issue moves the item to `approved`; nothing else does.
- **B263.** `COMMENT_UPSTREAM=false` suppresses every outward comment and changes nothing else.

### Tagging
- **B264.** Every work item carries exactly one `stage:`, one `kind:` and one `via:` label.
- **B265.** A transition swaps the `stage:` label only, in one call, leaving `kind:` and `via:` untouched.
- **B266.** `harness relabel` rewrites every open issue from `harness:*` to `stage:*`, is idempotent, and changes no state.
- **B267.** `relabel` refuses while any item is in `building`, `planning` or `revising`.
- **B268.** `init --labels` creates all three families with their descriptions and colours.

## 9. Acceptance

Run in order. Each must hold exactly as written.

**A1 — the inbox.** Comment `/harness work make the activity cards keyboard reachable` on
`INBOX_ISSUE`. Within one `feedback.yml` run: a new issue exists labelled `stage:queued`
`kind:product` `via:requested`, its body carries the text, and the inbox thread has a reply linking
it.

**A2 — a link.** Comment `/harness work https://github.com/Bright-Bots-Initiative/brightboost/issues/633`.
The item created has `external_ref="issue:633"`. Repeat it: no second item, and the reply says so.

**A3 — from the product repository.** On a brightboost issue, comment
`@jgoetzmann-bot /harness work`. Within one sweep an item exists for that issue, and the harness has
replied **on the brightboost issue**. Then comment `/harness reject not now` there: the item becomes
`stage:dropped`.

**A4 — the number trap.** With harness work item #4 open and unrelated to brightboost #4, comment
`/harness stop` on brightboost #4. Harness #4 is untouched and the reply says there is nothing to
steer.

**A5 — an audit.** Comment `/harness audit accessibility in src/components/activities`. One issue
appears, `kind:audit`, listing findings with paths and severities. **No work item is created.** The
ledger shows one call charged against `AUDIT_CAP_USD`.

**A6 — promotion.** Reply `/harness promote 1,3`. Two work items appear, `via:audit`, and findings 1
and 3 are ticked in the audit issue. Reply `/harness promote 1` again: nothing new.

**A7 — suggestion and the green light.** With the queue empty, run `discover.yml`. At most five
items appear, `via:suggested`, each with a proposal PR. Each unassigned source issue on brightboost
has exactly one comment asking for a green light. Run it again: **no second comment.** Reply
`/harness go` on one: that item reaches `stage:ready`.

**A8 — the queue is not idle.** With one item at `stage:ready`, run `discover.yml`. It queues
nothing and says why.

**A9 — tagging.** Every open harness issue carries one label from each family. Take one item from
`queued` to `needs-approval` and confirm the `kind:` and `via:` labels are unchanged and exactly one
`stage:` label is present throughout.

**A10 — relabel.** On a repository still using `harness:*`, run `harness relabel`. Every open issue
moves to `stage:*`; no state changes; running it twice is a no-op.

**A11 — the switch.** Set `COMMENT_UPSTREAM=false` and repeat A7. Items and proposals are created;
**no comment appears on brightboost.**

**A12 — nothing regressed.** The full suite passes, `harness doctor` is clean, `.harness/PIN`
verifies, and `selftest` is green on `ubuntu-latest` and `windows-latest`.

## 10. Invariants

Unchanged and re-checked: I-2′ (no `gh` CLI), I-4 (`os.environ` only in `config.py`), I-8 (the
harness cannot remove its own kill switch), I-9/I-11 (one credential door), I-12 (**it still cannot
merge, approve or dismiss anything**), I-13 (everything redacted), I-14, I-15 (no `workflow` scope),
I-17 (standard library only).

**I-14 is widened, deliberately and narrowly.** It said issues are only ever filed in `SELF_REPO`.
That stands — no issue is ever filed on the product repository. What is new is **comments**, bounded
by: only on a thread the harness already has work for or was addressed on, or one of at most
`SUGGEST_MAX_PER_RUN` green-light requests per week; never more than once per issue; never on an
assigned issue; redacted; and disabled entirely by `COMMENT_UPSTREAM=false`.

## 11. Out of scope

Sub-issues. Cross-repository project boards. Any second product repository. Auto-promoting audit
findings. Anything that lets the harness merge anything, ever.

## 12. Open questions

1. **Does the green-light comment need a quiet period?** Five a week is a bound on volume, not on
   annoyance. A repository with few issues would see the harness comment on a large share of them.
2. **Who may say `/harness go`?** Currently the trust file plus association. The issue's *author*
   is arguably the right person to ask, and is not necessarily either.
3. **Should `kind:harness` work exist at all yet?** The label is cheap and the concept is clear, but
   nothing yet points the harness at its own repository, and doing so raises a question this
   document does not answer: what stops it proposing changes to the code that governs it?
