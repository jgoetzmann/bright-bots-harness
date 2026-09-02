# Bright Bots Harness — Delivery 2 Handoff

**Status:** specification, frozen on acceptance. Not yet built.
**Supersedes:** nothing. Delivery 1 (`HARNESS-SPEC.md`) remains true for everything it covers;
this document says what changes and what is added.
**Audience:** whoever implements this, who may be a model, and who has not read Delivery 1.
Read §1 and §3 first; they carry the shape.

---

## 0. The one-paragraph version

Delivery 1 is a local, manually-started tool that produces a review package on disk and touches
nothing. Delivery 2 keeps every guarantee that made that true and moves the *scheduling* and the
*compute* onto GitHub, where the queue is GitHub itself. Work is discovered from issues in this
repository, proposed as a pull request into `proposals/` that a human merges, implemented on a
branch of a fork of the product repository, and delivered as a pull request that a human approves.
Two human gates, both ordinary pull requests, both usable from a phone. The model never merges
anything. The same code also runs unattended in a container on a Windows host, for proof of
concept and for debugging, and the two modes differ only in where the queue lives.

---

## 1. What changes, and what does not

### 1.1 Does not change

These are the load-bearing properties of Delivery 1. Delivery 2 inherits every one of them, and
the acceptance protocol re-checks them rather than assuming them.

| Property | Where it lives today | Status in Delivery 2 |
| --- | --- | --- |
| Gates are never widened, skipped or retimed | `harness/gates.py`, `_SEQUENCE` | unchanged, and now additionally enforced by credential scope (§5.4) |
| The model implements; the gates score | `stages/implement.py` + `gates.py` | unchanged |
| Everything redacted before it hits disk | `harness/redact.py` | unchanged, and extended to everything posted to GitHub (I-13) |
| No writes outside declared roots | `redact.py:allowed_roots` | unchanged; the roots list gains nothing |
| Prompts are data, one file per stage | `prompts/` | unchanged; new stages add new files |
| The governor authorises before any spend | `harness/governor.py` | unchanged interface, new backing store (§6) |
| Package format: patch series pinned to a base commit | `docs/PACKAGE-FORMAT.md` | unchanged, and now the body of the delivery PR |
| A red the harness cannot fix honestly is a blocked item | `stages/implement.py` | unchanged |

### 1.2 Changes

| Delivery 1 | Delivery 2 |
| --- | --- |
| `PERMISSION_TIER=0`, no authenticated request | Tier 2, scoped: authenticated as a machine account with write access to a fork it owns, and no write access to the product repository (§5) |
| `gh` CLI banned outright (I-2) | still banned in the package; GitHub writes go through `harness/gh.py` with an explicit token (I-11 replaces I-2, §12) |
| Queue in SQLite (`harness.db`) | queue is GitHub issues and pull requests; `harness.db` is per-run scratch (§4) |
| Manually started, five commands | three scheduled workflows plus manual dispatch (§7) |
| One clone, `MAX_CONCURRENT_CLONES=1` | N independent branches on one fork, dispatcher-capped (§6.4) |
| No scheduler, no daemon (spec §1106) | scheduled workflows, and a container for local mode (§10) |
| No sub-issue decomposition | `decompose` stage (§4.6) |
| Budget as % of an allowance the operator eyeballs | spend-side ledger plus exhaustion handling (§6) |
| No push, no PR, no comment | pushes to a fork it owns; opens PRs; comments on its own PRs (§5.3) |

### 1.3 The three constraints that shaped this

1. **Claude exposes no remaining-allowance signal.** Verified against CLI 2.1.258: session
   files carry per-message `usage` but no `rate_limits` block, and `claude auth status` reports
   auth only. The reference implementation this design borrows from (`rk`) gates on Codex's
   `used_percent`; that signal does not exist here. Budget is therefore built on spend
   accounting plus exhaustion handling, never on a remaining percentage (§6).
2. **GitHub allows one fork per account per fork network.** Multiple concurrent work streams are
   branches, not forks (§4.4).
3. **A public product repository means anyone can comment.** Every keyword handler gates on a
   trust list before it spends anything (§8).

---

## 2. The two execution modes

One codebase, two ways to run it. The seam is deliberately narrow.

```
                        SHARED — byte-identical in both modes
   ┌──────────────────────────────────────────────────────────────────┐
   │  stages/   gates.py   packager.py   governor.py   redact.py       │
   │  prompts/  clone.py   commitmsg.py  prettier.py   collision.py    │
   └──────────────────────────────────────────────────────────────────┘
                  │                                     │
        ┌─────────┴──────────┐              ┌───────────┴───────────┐
        │  Store: GitHub     │              │  Store: SQLite        │
        │  Dispatcher: cron  │              │  Dispatcher: loop     │
        │  ACTIONS MODE      │              │  LOCAL MODE           │
        └────────────────────┘              └───────────────────────┘
```

**The rule that keeps this one harness and not two:** no module above the store may branch on
which mode it is in. A grep for `ACTIONS`, `CI`, `GITHUB_ACTIONS` or `mode ==` under
`harness/stages/`, `harness/gates.py`, `harness/packager.py` and `harness/governor.py` must return
nothing. This is invariant **I-16** and it has a test.

If a stage appears to need to know, the seam is in the wrong place. Move the difference into the
store or the dispatcher.

**Actions mode** is the product. **Local mode** is the proof of concept, the debugger, and the
answer to "GitHub is down / the schedule was disabled / I want to watch it work."

---

## 3. Target architecture — the complete file map

Everything. New files marked `NEW`, changed files marked `CHG`, everything else is untouched.

```
Bright-Bots-Harness/
│
├── .github/
│   ├── workflows/
│   │   ├── discover.yml              NEW  weekly: triage issues → proposal PRs
│   │   ├── implement.yml             NEW  every 6h: drain the approved queue
│   │   ├── feedback.yml              NEW  every 3h weekdays: notifications + keywords
│   │   ├── ops.yml                   NEW  on workflow_run failure: alert, bounded retry
│   │   ├── heartbeat.yml             NEW  weekly: prove the scheduler is alive
│   │   └── selftest.yml              NEW  on PR here: fake-backend suite + invariants
│   ├── CODEOWNERS                    NEW  §5.5
│   ├── ISSUE_TEMPLATE/
│   │   └── work-item.md              NEW  the shape discovery expects
│   └── pull_request_template.md      NEW
│
├── .harness/                         NEW  operator-editable configuration (§5.5)
│   ├── trust.txt                     NEW  GitHub handles whose keywords are honoured
│   ├── config.json                   NEW  budget, cadence, caps — P12 knobs only
│   └── README.md                     NEW  what may and may not go in here
│
├── harness/                          the package
│   ├── __init__.py
│   ├── __main__.py                   CHG  new subcommands (§3.2)
│   ├── clock.py
│   ├── clone.py                      CHG  fork URL, fast-forward-only sync (§4.4)
│   ├── collision.py
│   ├── commitmsg.py
│   ├── config.py                     CHG  reads .harness/config.json; new keys (§6.5)
│   ├── context.py
│   ├── dispatcher.py                 NEW  how many items may start this window (§6.4)
│   ├── errors.py                     CHG  RateLimited, TrustDenied, ForkDiverged
│   ├── gates.py                      unchanged — do not touch
│   ├── gh.py                         CHG  authenticated client, write methods (§5.3)
│   ├── governor.py                   CHG  ledger-backed; interface unchanged
│   ├── halt.py                       CHG  also honours a repo-level HALT (§11.4)
│   ├── identity.py                   CHG  the machine account is now used, not just detected
│   ├── keywords.py                   NEW  parse and authorise keyword commands (§8)
│   ├── ledger.py                     NEW  the one persistent state file (§6.2)
│   ├── packager.py                   unchanged — do not touch
│   ├── prettier.py
│   ├── redact.py                     unchanged — do not touch
│   ├── trust.py                      NEW  reads .harness/trust.txt, authorises actors (§8.2)
│   ├── runner/
│   │   ├── base.py                   CHG  RunResult gains reset_at
│   │   ├── cli.py                    CHG  --max-budget-usd, rate-limit detection (§6.3)
│   │   └── fake.py                   CHG  can replay a rate-limit outcome
│   ├── stages/
│   │   ├── discover.py               CHG  reads issues from this repo, not the product repo
│   │   ├── propose.py                CHG  emits proposals/NNN.md
│   │   ├── implement.py              unchanged — do not touch
│   │   ├── package.py                unchanged — do not touch
│   │   ├── decompose.py              NEW  split one issue into sub-issues (§4.6)
│   │   ├── revise.py                 NEW  CI failure / conflict / review feedback (§9)
│   │   └── deliver.py                NEW  push branch, open the upstream PR (§4.5)
│   └── store/                        NEW  was store.py; now a package
│       ├── __init__.py               NEW  the Store protocol — the only seam (§2)
│       ├── sqlite.py                 CHG  the existing store.py, moved verbatim
│       └── github.py                 NEW  issues and PRs as the queue (§4)
│
├── prompts/
│   ├── system.md                     CHG  identity and tier text updated
│   ├── discover_triage.md            CHG
│   ├── propose.md                    CHG  must emit the bounded proposal schema (§4.3)
│   ├── implement.md
│   ├── implement_fullsend.md
│   ├── diagnose_gate_failure.md
│   ├── decompose.md                  NEW
│   └── revise.md                     NEW
│
├── proposals/                        NEW  merged proposals — the approved queue (§4.3)
│   └── .gitkeep
│
├── state/                            NEW  the only persistent state (§6.2)
│   └── ledger.json                   NEW  spend, medians, reset times, cursors
│
├── local/                            NEW  the container control plane (§10)
│   ├── Dockerfile                    NEW
│   ├── entrypoint.sh                 NEW  the gate, in the frozen order (§10.3)
│   ├── run.ps1                       NEW  the docker run contract
│   ├── watchdog-bb.ps1               NEW  MUST NOT contain the substring "watchdog.ps1"
│   ├── container_env.ps1             NEW  the credential filter (§10.5)
│   ├── preflight.py                  NEW
│   └── README.md                     NEW  the rebuild-vs-restart trap (§10.2)
│
├── tests/
│   ├── conftest.py
│   ├── test_invariants.py            CHG  I-11..I-17 added
│   ├── test_store_github.py          NEW
│   ├── test_ledger.py                NEW
│   ├── test_dispatcher.py            NEW
│   ├── test_keywords.py              NEW
│   ├── test_trust.py                 NEW
│   ├── test_stages_revise.py         NEW
│   ├── test_stages_deliver.py        NEW
│   ├── test_stages_decompose.py      NEW
│   ├── fixtures/
│   │   ├── gh/                       CHG  fixtures for the new endpoints
│   │   └── runner/
│   │       └── revise.json           NEW
│   └── … (existing tests unchanged)
│
├── docs/
│   ├── SAFETY.md                     CHG  the tier table and every invariant (§12)
│   ├── PACKAGE-FORMAT.md             CHG  the package is now also a PR body
│   ├── OPERATIONS.md                 NEW  what to do when something fails (§11)
│   └── LOCAL-MODE.md                 NEW  the container, for an operator (§10)
│
├── bb-start.ps1                      NEW  local mode: start container + watchdog
├── bb-stop.ps1                       NEW  local mode: graceful / forced stop
├── bb-watcher.ps1                    NEW  local mode: live read-only view
├── bb-configure.py                   NEW  show / explain / set / reset
├── bb-config.json                    NEW  container, run, watchdog, watcher
├── bb-work/                          NEW  gitignored; bind-mounted at /work
│
├── DELIVERY-2-HANDOFF.md             this file
├── DELIVERY-2-REVIEW.md              NEW  the acceptance protocol
├── HARNESS-SPEC.md                   unchanged — Delivery 1, still true
├── HARNESS-REVIEW.md                 unchanged
├── DECISIONS.md                      CHG  append Delivery 2 rulings
├── HUMAN.md                          CHG  regenerated; §16
├── README.md                         CHG
├── .env.example                      CHG  new keys, §6.5
└── pyproject.toml                    CHG  no new runtime dependencies (I-17)
```

### 3.1 Directories that must not appear

- **No `submodules/`, no `.gitmodules`.** The fork is cloned at runtime. A submodule pins one
  commit and cannot represent N concurrent branches; adding a workflow file to the fork's default
  branch also breaks fast-forward sync, which silently destroys `base_sha` integrity (§4.4).
- **No `runs/` in git.** Unchanged from Delivery 1; still gitignored.
- **No vendored copy of the product repository.**

### 3.2 CLI surface after this delivery

Existing, unchanged: `init`, `doctor`, `setup`, `discover`, `propose`, `approve`, `run`,
`package`, `archive`, `status`, `halt`, `resume`.

New:

| Command | Does |
| --- | --- |
| `harness dispatch` | ask the dispatcher what may start now; emit a JSON plan; start nothing |
| `harness deliver <id>` | push the branch and open the upstream PR |
| `harness revise <id> --source ci\|conflict\|review` | one bounded revision cycle |
| `harness decompose <issue>` | split one issue into sub-issues |
| `harness sweep` | poll notifications, parse keywords, enqueue resulting actions |
| `harness ledger [--json]` | print spend, medians, window state |
| `harness sync-fork` | fast-forward the fork from upstream; fail loudly on divergence |

Every one of these must run under `BACKEND=fake` with no network. That is what makes the
acceptance protocol cheap enough to actually run.

---

## 4. The GitHub-as-queue state machine

### 4.1 Why the database is not the queue

`harness.db` is per-run scratch and is never persisted between workflow runs. The queue is GitHub.
This is not a convenience: it removes the entire class of problems where CI state diverges from
observable reality, it makes every transition human-inspectable and human-*editable*, and it means
a total loss of the runner leaves nothing to reconstruct.

The one exception is `state/ledger.json` (§6.2), which is small, mergeable, and reconstructible
from the record if lost.

### 4.2 States and their representation

A work item is **one issue in this repository**. Its state is the set of labels on it.

| State | Label | Set by | Means |
| --- | --- | --- | --- |
| queued | `harness:queued` | a human, or `decompose` | eligible for a proposal |
| proposing | `harness:proposing` | `discover.yml` | a propose job is in flight |
| proposed | `harness:proposed` | `propose` | a proposal PR is open; **human gate 1** |
| approved | `harness:approved` | `implement.yml` on proposal merge | eligible for implementation |
| running | `harness:running` | `implement.yml` | an implement job is in flight |
| shipped | `harness:shipped` | `deliver` | upstream PR open; **human gate 2** |
| revising | `harness:revising` | `revise` | a revision cycle is in flight |
| merged | `harness:merged` | `feedback.yml` | upstream PR merged — terminal |
| blocked | `harness:blocked` | any stage | gates red and honestly unfixable |
| needs-human | `harness:needs-human` | `revise` | revision cap reached |
| abandoned | `harness:abandoned` | a human, or `/harness stop` | terminal |

**B100** — Exactly one `harness:*` state label is present on an item at any time. A transition is
a single API call that removes the old label and adds the new one; a stage that finds two state
labels raises rather than guessing.

**B101** — Every transition also writes a comment on the issue naming the stage, the workflow run
URL, the cost in USD, and the resulting state. The issue thread is the event log.

**B102** — An item whose state label was changed by a human is honoured, not overwritten. A human
moving `harness:blocked` back to `harness:queued` is a re-queue and the harness treats it as one.

### 4.3 Gate 1 — the proposal PR

`propose` writes exactly one file, `proposals/<issue>-<slug>.md`, on a branch
`harness/propose-<issue>`, and opens a PR against this repository's default branch.

The file has YAML front matter, and the front matter is a **bounded schema** — every field
required, every enum closed, unknown keys rejected. This is the §7.6 rule from the platform
document ("the model may steer; it may not score") applied at the only place the model's output
becomes an instruction.

```yaml
---
issue: 816                      # int, must match an open issue in this repo
upstream_issue: 816             # int or null — the product repo issue, if any
title: "…"                      # str, 1..120
kind: fix | chore | test | docs # closed enum
slices: 1                       # int, 1..5
risk: low | medium | high       # closed enum
touched_paths:                  # list[str], 1..40, each must exist in the product repo
  - src/…
depends_on: []                  # list[int] — issue numbers that must reach harness:merged first
estimated_turns: 40             # int, 1..MAX_TURNS_IMPLEMENT
gate_expectation: green | known-red   # closed enum; known-red requires baseline_red below
baseline_red: []                # list[str] — gate names already red before the change
---
```

Below the front matter, prose: diagnosis with file and line citations, approach, numbered
behaviors, acceptance criteria, decisions, open questions, risks. Unchanged in substance from
Delivery 1's work package.

**B103** — A proposal whose front matter fails schema validation is never opened as a PR. The
stage retries once with the validation error appended to the prompt, then marks the item
`harness:blocked` with the error in a comment.

**B104** — `touched_paths` entries are checked against the product repository at the pinned base
commit. A path that does not exist fails validation. This is cheap and catches the most common
hallucination.

**Approval is merging the PR.** Not approving it — merging it. Approval without merge is
ambiguous; merge is not. Merging is what `implement.yml` listens for.

### 4.4 The fork, and fast-forward-only

One fork of the product repository, owned by the machine account (§5.1). It has exactly one
purpose: to hold work branches.

**B105** — The fork's default branch is **never** modified by the harness except by fast-forward
from upstream. No workflow file, no README change, no merge commit. `harness sync-fork` performs
`git fetch upstream && git push origin upstream/main:main` and **fails loudly** if that is not a
fast-forward.

**Why this is load-bearing:** the review package pins a `base_sha`. If the fork's main diverges,
that SHA exists only in the fork, and the patch series stops being reconstructible by anyone
reviewing it upstream. A non-fast-forward fork is a silently broken package format.

**B106** — Every work branch is cut from the fork's main at a commit that exists upstream, named
`harness/<kind>-<issue>-<slug>` by the existing `clone.branch_name_for`. Branches are independent;
none is cut from another.

**B107** — Dependencies between items are declared in `depends_on` and enforced by the dispatcher,
never by branching one work branch off another. Stacked git branches are not used.

### 4.5 Gate 2 — the delivery PR

`deliver` runs only after the gates are green (or honestly known-red per `baseline_red`).

1. `harness sync-fork`.
2. Rebase the work branch onto the fork's main. A conflict here is a `revise` item with
   `--source conflict`, not a failure.
3. Push the branch to the fork.
4. Open a PR from `<machine-account>:harness/…` into the product repository's default branch.
5. Request review from every handle in `.harness/trust.txt`.
6. Comment on the harness issue with the PR URL; set `harness:shipped`.

**B108** — The PR body is generated from the review package: the diagnosis, the base commit, the
gate evidence verbatim, and the reconstruction commands from `docs/PACKAGE-FORMAT.md`. It is
redacted by `redact.py` before it is sent (I-13).

**B109** — The harness never merges an upstream PR, never approves one, and never dismisses a
review. The code to do so does not exist. This is invariant **I-12**.

### 4.6 Decomposition

**B110** — `decompose` reads one issue and emits N sub-issues in this repository, each labelled
`harness:queued`, each linking to the parent, with the parent labelled `harness:blocked` and
commented with the list. It creates issues only in this repository; it never files an issue on the
product repository. That is invariant **I-14**.

**B111** — Decomposition is bounded: at most `max_subissues` (default 8) per parent, and a
sub-issue may not itself be decomposed. Depth is one.

---

## 5. Identity, credentials and trust

### 5.1 The machine account

`brightboost-harness`, already specified in `HARNESS-SPEC.md` §1149 and already detected by
`harness/identity.py`. Delivery 2 activates it.

GitHub's terms permit one machine account alongside a personal account, so this is the compliant
shape as well as the safe one. The account:

- **owns the fork.** Not the operator. PRs are then unambiguously attributable to automation.
- holds one classic PAT, scope **`public_repo` only**.
- has **no** write access to the product repository, and is not a collaborator on it.

### 5.2 Why a classic PAT and not fine-grained

Fine-grained tokens cannot open a pull request from a fork to an upstream repository — a
documented, long-standing limitation. A classic token with `public_repo` is the working
credential. On an account that owns nothing but the fork, that is a bounded blast radius: it can
write to public repositories the account can already write to, which is the fork, and it can open
PRs, which is the point.

**The scope that is deliberately absent is `workflow`.** GitHub rejects any push that modifies
`.github/workflows/`. Acceptance criterion A18 from Delivery 1 — *the harness must never edit CI
to go green* — therefore stops being a rule the model is trusted to follow and becomes something
the receiving end enforces. This is invariant **I-15**.

### 5.3 What the token may do

| Operation | Allowed | Where |
| --- | --- | --- |
| Read public repositories | yes | `gh.py` |
| Push a branch to the fork | yes | `deliver.py` |
| Open a PR fork → product repo | yes | `deliver.py` |
| Comment on a PR it opened | yes | `deliver.py`, `revise.py` |
| Create issues in **this** repository | yes | `decompose.py` |
| Create issues in the product repository | **no** | I-14 |
| Merge, approve, or dismiss any review | **no** | I-12 |
| Push to `.github/**` anywhere | **no** | I-15, enforced by GitHub |
| Push to the product repository directly | **no** | not a collaborator |

### 5.4 Secrets

| Secret | Where it lives | Reaches the container? | Reaches Actions? |
| --- | --- | --- | --- |
| `CLAUDE_CODE_OAUTH_TOKEN` | repo secret; host `.env` locally | **yes** — the loop needs it | yes |
| `HARNESS_GITHUB_TOKEN` | repo secret; host `.env` locally | **no** — filtered (§10.5) | yes |

In local mode this reproduces the platform document's P5 exactly: *the container commits, the host
pushes.* The containerised loop stays credential-free with respect to GitHub, so local mode is
still structurally incapable of publishing. The host watchdog does the pushing.

### 5.5 Trust, and who may steer

Two files under `.harness/`, both protected by `CODEOWNERS`:

```
# .harness/trust.txt — GitHub handles whose keyword commands are honoured.
# One handle per line. '#' starts a comment. Case-insensitive.
# Changing this file requires a reviewed PR (see .github/CODEOWNERS).
jgoetzmann
<NATHAN_HANDLE>
```

```
# .github/CODEOWNERS
/.harness/          @jgoetzmann
/prompts/           @jgoetzmann
/.github/           @jgoetzmann
/harness/gates.py   @jgoetzmann
/harness/redact.py  @jgoetzmann
```

**B112** — `.harness/config.json` carries operational knobs only. A key whose change would alter
what the harness *concludes* — the gate sequence, the redaction patterns, the proposal schema — is
not a config key. It is a code change, reviewed as one. (This is P12 from the platform document.)

**B113** — Branch protection on the default branch requires one approving review and disallows
force-push. The harness's own proposal PRs are subject to it like anything else.

---

## 6. Budget, the ledger and the dispatcher

### 6.1 The problem, stated honestly

There is no API, file, or command that reports how much of a Claude subscription allowance
remains. Any design that claims to "run only when there is excess" is therefore inferring, not
measuring. This design infers from four things and says so:

1. **Spend accounting.** `total_cost_usd` is returned on every call and already parsed into
   `RunResult.cost_usd`. Accumulate it.
2. **A hard per-call ceiling.** `claude --max-budget-usd <amount>`, which the CLI enforces itself.
   Independent of anything the harness computes — the platform document's "enforced twice."
3. **Exhaustion as an outcome.** A rate-limit response is not a failure; it carries a reset time
   and reschedules the item.
4. **Schedule placement.** Running at 03:00 local makes "excess" structurally likely rather than
   measured. This is the weakest of the four and the most reliable in practice.

**B114** — No module claims to know remaining allowance. `RunResult.allowance_pct` stays `None`
unless a future CLI supplies it, and no decision may depend on it being non-`None`.

### 6.2 `state/ledger.json`

The single persistent file. Committed by the workflow at the end of every run that spent anything.

```json
{
  "schema": 1,
  "window": {
    "period_start": "2026-09-07T00:00:00Z",
    "spent_usd": 12.41,
    "calls": 37,
    "rate_limited_until": null
  },
  "observations": {
    "propose":   {"n": 9,  "median_usd": 0.42},
    "implement": {"n": 14, "median_usd": 2.10},
    "revise":    {"n": 3,  "median_usd": 0.88}
  },
  "cursors": {
    "notifications_last_seen": "2026-09-02T09:14:00Z"
  },
  "history": [
    {"ts": "…", "stage": "implement", "issue": 816, "usd": 2.31, "run": "https://…"}
  ]
}
```

**B115** — Written temp-then-`os.replace`, then committed with `[skip ci]` in the message so the
commit does not re-trigger workflows.

**B116** — `history` is append-only and capped at 500 entries; older entries are summarised into
`observations` and dropped. The file must stay small enough to merge without conflict.

**B117** — The ledger is reconstructible. `harness ledger --rebuild` walks the issue comments
(which carry cost per B101) and regenerates it. Losing the file costs accuracy, not correctness.

**B118** — Concurrent runs are prevented from racing the ledger by a workflow-level
`concurrency: { group: harness-ledger, cancel-in-progress: false }` on every workflow that writes
it. A run that cannot acquire it waits rather than forcing.

### 6.3 Rate limiting as an outcome

**B119** — `runner/cli.py` classifies a non-zero exit whose stderr matches the CLI's usage-limit
signature as `RateLimited`, not a generic failure, and extracts the reset timestamp when present.

**B120** — A `RateLimited` stage returns its item to its **previous** state label, writes
`rate_limited_until` to the ledger, comments on the issue with the reset time, and exits 0. The
workflow succeeds. A rate limit is a normal condition, not an incident.

**B121** — The dispatcher starts nothing while `now < rate_limited_until`. `harness dispatch`
prints the reason and an empty plan.

### 6.4 The dispatcher

**B122** — `harness dispatch` emits a JSON plan and starts nothing. It is pure; the workflow acts
on the plan. This makes the hardest logic in the system testable with no network.

```json
{"start": [816, 823], "reason": "budget 62% remaining, 2 of max 3 slots",
 "skipped": {"819": "depends_on 816 not merged", "position": "rate limit clear"}}
```

Selection, in order:

1. If `now < rate_limited_until` → empty plan.
2. If the repo-level HALT file exists (§11.4) → empty plan.
3. If `spent_usd >= weekly_cap_usd * (1 - reserve_pct/100)` → empty plan, reason `reserve`.
4. Candidates = items labelled `harness:approved`, oldest first.
5. Drop any whose `depends_on` are not all `harness:merged`.
6. Drop any whose estimated cost (median, or the static table below three observations) would
   exceed the remaining window budget.
7. Take at most `max_concurrent_items` (default 3).

**B123** — `max_concurrent_items` is a config key, and above 1 it is only permitted in Actions
mode. Local mode keeps `MAX_CONCURRENT_CLONES=1` from Delivery 1; the container has one clone.

### 6.5 New configuration keys

Added to `.env.example` and `.harness/config.json`. Every key required, no defaults in code —
the Delivery 1 rule, unchanged.

| Key | Default | Meaning |
| --- | --- | --- |
| `WEEKLY_CAP_USD` | `25.00` | hard ceiling on accumulated spend per window |
| `PER_CALL_CAP_USD` | `3.00` | passed to `claude --max-budget-usd` |
| `RESERVE_PCT` | `10` | held back, never spent (unchanged meaning) |
| `MAX_CONCURRENT_ITEMS` | `3` | dispatcher slots, Actions mode only |
| `MAX_REVISE_CYCLES` | `3` | per delivery PR, before `harness:needs-human` |
| `FORK_REPO` | — | `<machine-account>/brightboost` |
| `UPSTREAM_REPO` | `Bright-Bots-Initiative/brightboost` | the product repository |
| `TRUST_FILE` | `.harness/trust.txt` | |
| `NOTIFY_POLL_HOURS` | `3` | feedback sweep cadence |
| `MAX_SUBISSUES` | `8` | decomposition bound |

**A30** — `harness doctor` reports every one of these, and exits 3 naming any that is missing or
out of range. A typo'd key remains a startup error, never a silently different budget.

---

## 7. The workflows

All five live in this repository. **None** is added to the fork — that would break B105.

### 7.1 Cadence, and why it is not all Sunday

A weekly cron gets exactly one usage window. Ten approved items will not fit in one, and a
weekly-only design spends six days with a backlog it cannot drain. Split by cost:

| Workflow | Trigger | Cost | Why this cadence |
| --- | --- | --- | --- |
| `discover.yml` | `cron: 17 7 * * 0` + dispatch | low | proposals are cheap; you review them during the week |
| `implement.yml` | `cron: 23 */6 * * *` + dispatch + proposal merge | high | drains across several usage windows |
| `feedback.yml` | `cron: 41 */3 * * 1-5` + dispatch | very low | a review comment must not wait a day |
| `ops.yml` | `workflow_run: completed` | none | alert and bounded retry |
| `heartbeat.yml` | `cron: 5 9 * * 1` | none | proves the scheduler is alive |
| `selftest.yml` | `pull_request` | none | fake backend, no secrets needed |

**B124** — Every cron minute is non-zero and non-round. Scheduled workflows queue behind everyone
else's at `:00` and are routinely delayed or dropped.

**B125** — Every job sets `timeout-minutes: 120`. The platform cap is 360; failing cleanly at 120
beats being killed at 360 with no evidence.

**B126** — Every job that can spend uploads `runs/item-*/` as an artifact with `if: always()`, so
a cancelled or timed-out run still leaves its evidence.

### 7.2 `implement.yml`, in outline

```yaml
on:
  schedule:    [{cron: "23 */6 * * *"}]
  workflow_dispatch:
    inputs: {issue: {description: "issue number, or blank for the dispatcher's plan"}}
  push:
    branches: [main]
    paths: ["proposals/**"]        # a merged proposal = approval

concurrency: {group: harness-implement, cancel-in-progress: false}

permissions: {contents: write, issues: write, pull-requests: write}
```

Steps, in order:

1. `actions/checkout` this repository.
2. Set up Python 3.13, `pip install -e .`.
3. `npm i -g @anthropic-ai/claude-code`.
4. `harness doctor` — fail the job here, before spending, if anything is missing.
5. `harness sync-fork` — fail loudly on divergence (B105).
6. `harness dispatch` → the plan.
7. For each item in the plan: `harness run --item N` then `harness package N` then
   `harness deliver N`.
8. Commit `state/ledger.json` with `[skip ci]`.
9. `if: always()` — upload artifacts, post the run summary to each touched issue.

**B127** — Step 4 runs before step 6. The harness never spends a token in a job whose environment
it has not verified.

**B128** — `CLAUDE_CODE_OAUTH_TOKEN` is passed as an environment variable to the job. The existing
`runner/cli.py` strips only `ANTHROPIC_API_KEY` from the child environment, so this passes through
untouched and requires no change to the runner's env handling.

### 7.3 The self-test workflow

**B129** — `selftest.yml` runs the whole pytest suite under `BACKEND=fake` on both
`ubuntu-latest` and `windows-latest`. The Windows job exists because `gates.py:_resolve_argv`,
`gates.py:_git_bash` and `clone.py:_on_rmtree_error` are Windows-only branches that Actions mode
never exercises; without it, local mode rots silently.

**B130** — `selftest.yml` uses no secrets and is the only workflow that runs on pull requests, so
a fork PR to this repository is safe by construction.

---

## 8. Keywords and the actor gate

### 8.1 The hole this closes

The product repository is public. Anyone may comment on a delivery PR. Without an authorisation
check, any GitHub user could spend the operator's Claude allowance, or steer what the model writes,
by typing a keyword.

### 8.2 The gate

**B131** — Every keyword command is authorised before anything is parsed further. A command is
honoured only if **both** hold:

1. the commenter's handle appears in `.harness/trust.txt` (case-insensitive), **and**
2. the comment's `author_association` is `OWNER`, `MEMBER` or `COLLABORATOR`.

Two independent checks because they fail differently: the trust file is the operator's intent, the
association is GitHub's assertion. Neither alone is sufficient.

**B132** — A command from an unauthorised actor is **silently ignored** — no reply, no reaction, no
log entry that quotes the comment body. Replying confirms the trigger exists and invites probing.
The event is counted in the ledger as `keyword_denied` with the handle only.

**B133** — Keyword parsing happens **after** authorisation, never before. The comment body of an
untrusted user is never passed to a model, never interpolated into a prompt, and never written to
disk unredacted.

### 8.3 The verbs

| Where | Command | Effect |
| --- | --- | --- |
| proposal PR | *merge it* | approve — the gate itself |
| proposal PR | `/harness revise <notes>` | re-run propose with the notes as added context |
| proposal PR | `/harness reject <why>` | close the PR, label the issue `harness:abandoned` |
| delivery PR | `/harness fix` | pull failing checks + review comments, run one revise cycle |
| delivery PR | `/harness rebase` | sync fork, rebase, re-run the full gate sequence |
| delivery PR | `/harness stop` | close the PR, label `harness:abandoned`, free the slot |
| any issue here | `/harness split` | run `decompose` |
| any issue here | `/harness queue` | label `harness:queued` |

**B134** — On this repository, commands are event-driven (`issue_comment`,
`pull_request_review_comment`) and act within the same run. On the product repository the harness
receives no events, so commands are found by the sweep (§9.2) and latency equals
`NOTIFY_POLL_HOURS`. This asymmetry is documented in `docs/OPERATIONS.md` because it will
otherwise be read as a bug.

**B135** — A command is acted on at most once. The comment's node ID is recorded in the ledger's
`cursors`; a replayed sweep is a no-op.

---

## 9. The revise loop

### 9.1 Three inputs, one mechanism

A delivery PR attracts three kinds of feedback, and all three become the same thing: a bounded
revision against an existing branch, charged to the budget like any other stage, gated by the same
gate sequence.

| Source | Detected by | Fed to the model as |
| --- | --- | --- |
| CI failure on the upstream PR | check runs on the PR head | the failing job's log tail, redacted |
| Merge conflict | rebase exit status | the conflicted hunks |
| Human review | review comments and review bodies from trusted actors | the comment text and its file/line anchor |

**B136** — A revision re-runs the **complete** gate sequence. A conflict resolved into a red tree
is `harness:blocked`, never pushed. There is no path that ships a tree whose gates were not run
after the last edit.

**B137** — Revisions are capped at `MAX_REVISE_CYCLES` (default 3) per delivery PR. On the cap,
the item is labelled `harness:needs-human`, a comment explains what was tried, and the harness
stops touching that PR until a trusted actor issues `/harness fix` explicitly.

**B138** — A repeated failure signature stops the loop immediately regardless of the cap. This
reuses the existing signature logic in the Delivery 1 gate retry loop rather than adding a second
implementation.

**B139** — The harness force-pushes only to branches it created, only under `harness/`, and only
when the branch tip is a commit it authored. A branch a human has pushed to is never force-pushed;
the item becomes `harness:needs-human`.

### 9.2 The sweep

**B140** — `harness sweep` reads the machine account's notifications (`GET /notifications`,
`since` = the ledger cursor), not a listing of pull requests. One call returns every change since
the cursor — review submissions, comments, mentions — which is both cheaper and impossible to miss
a thread with.

**B141** — The sweep is read-and-enqueue only. It labels items and writes the cursor; it spends
nothing. Anything requiring a model call becomes an item the dispatcher picks up on its own
cadence, subject to budget.

---

## 10. Local mode — the `bb` container

Implements the platform described in `Integration-Harness/harness-platform.md`. That document is
normative for this section; what follows is the instantiation table and the deviations.

### 10.1 Instantiation

| Parameter | Value |
| --- | --- |
| Short id | `bb` |
| Container / image | `bb` / `bb-harness:latest` |
| Package, mounted `:ro` at `/harness` | the repository root |
| Work dir, bind-mounted at `/work` | `bb-work/` (gitignored) |
| Named volume | `bb-data` → `/data`, holding `harness.db` |
| Env prefix | `BB_*` |
| Network | `bb-net`, plain bridge (§10.6) |
| Workspace scripts | `bb-start.ps1`, `bb-stop.ps1`, `bb-watcher.ps1`, `bb-configure.py` |
| Watchdog | `local/watchdog-bb.ps1` |
| Base image | `python:3.13-slim-bookworm` + Node 20 + `@anthropic-ai/claude-code` |
| CPUs / memory | 4 / 8 GB |
| Heartbeat staleness | 180 s |
| Host ports | none |

**Memory is 8 GB, not the platform default 6.** `vite build` and `tsc` on the product repository
are the peak, and `--memory` is the limit that *kills* (exit 137) rather than slows. Headroom here
is cheap; an OOM mid-implement costs a whole item.

**The watchdog filename is `watchdog-bb.ps1`.** It does not contain the substring `watchdog.ps1`,
so the `rk` harness's wildcard process-kill cannot match it. `bb` is not a substring of `rk` or
`jobs`. This is checked by **A44**.

### 10.2 The rebuild-vs-restart trap

The package is mounted; `entrypoint.sh` and the `Dockerfile` are baked in. They therefore have
different deployment procedures: editing `harness/` needs only a restart, editing `entrypoint.sh`
needs `-Build`. Nothing warns you — a plain restart silently keeps running the old entrypoint.
`local/README.md` must say this at the top, and `bb-start.ps1` prints the image's build timestamp
on every start so a stale image is visible.

### 10.3 `entrypoint.sh` — the frozen order

1. Write `HEARTBEAT` immediately, before anything else.
2. Probe that `/harness` is writable; if it is, print a fatal error and `exit 1`.
3. Verify the pinned hash of the result-defining files (§10.4); mismatch → `exit 1`.
4. Check `/work`, `/data`, and `.harness/config.json` exist and parse.
5. Run the fast test subset — the invariant tests and the gate-sequence golden test. Under 10 s.
6. `exec` the loop.

**A45** — Each of steps 2, 3 and 5 is individually demonstrated to fail closed, by breaking it on
purpose and observing `exit 1`.

### 10.4 What is hashed (platform P2)

The thing that defines a result here is **what the harness checks and what it refuses to say**.
The pinned set is:

```
harness/gates.py        the gate sequence and its timeouts
harness/packager.py     what evidence a package must contain
harness/redact.py       what never reaches disk
prompts/                every prompt, as data
```

**B142** — `harness/verify_pin.py --check` recomputes a SHA-256 over that set in sorted path order
and compares it to `.harness/PIN`. Mismatch is a startup failure in both modes — the container
gate refuses to start, and `implement.yml` fails at step 4.

**B143** — Changing the pin is a reviewed PR touching `.harness/PIN`, which `CODEOWNERS` protects.
The harness cannot change its own pin: the file is outside `allowed_roots()`.

### 10.5 The credential filter (P4/P5)

`local/container_env.ps1` copies `.env` to a temp file with `HARNESS_GITHUB_TOKEN` removed, prints
how many lines it dropped, and returns the path. `CLAUDE_CODE_OAUTH_TOKEN` passes through.

**A46** — The startup log shows a non-zero drop count, and `docker exec bb env` shows no GitHub
token. The container commits; `watchdog-bb.ps1` pushes.

### 10.6 Egress: plain bridge, deliberately

An allowlist is rejected. The product repository's `npm ci` pulls from the npm registry and its
CDN backers, whose addresses rotate; the platform document's own caveat is that hostnames resolve
once when rules are applied, so such an allowlist breaks unpredictably weeks later with no error.
A rule set that must be disabled the first time it bites is worse than none, because it is
believed. `bb-net` is a plain bridge and this paragraph is the ruling.

### 10.7 What local mode is for

Proof of concept, debugging, and continuity when the schedule is disabled. It reads the same
GitHub queue read-only and does the same work; the difference is that the human runs
`bb-start.ps1` and the watchdog pushes. It is **not** a second product, and I-16 forbids the code
from knowing which mode it is in above the store.

---

## 11. Observability, failure and retry

### 11.1 Silence is the enemy

The failure that matters is not a red run — it is a schedule that quietly stopped. Public
repositories have their scheduled workflows disabled after 60 days without activity, and a fork's
schedules are disabled by default.

**B144** — `heartbeat.yml` runs weekly, spends nothing, and posts a single comment to a pinned
tracking issue: queue depth per state, spend this window, last successful run of each workflow,
and the fork's divergence from upstream. **Absence of that comment is the alarm.**

### 11.2 Failure handling

**B145** — `ops.yml` triggers on `workflow_run: {types: [completed]}` for the three spending
workflows and acts when `conclusion == failure`:

1. Open, or update, an issue titled `ops: <workflow> failed` labelled `harness:ops`, containing
   the run URL, the failing step, and the last 50 log lines redacted.
2. If the failure is in a classified-transient set (network reset, npm registry 5xx, GitHub 5xx,
   runner eviction) **and** this is the first retry for that run, re-dispatch once.
3. Otherwise leave it for a human and stop.

**B146** — Auto-retry is capped at one attempt per run and never applies to a failure inside a
model call or a gate. A red gate is information, not a transient.

**B147** — Every item that a failed run left mid-flight is returned to its previous state label by
the next run's reconciliation step, so a crashed job cannot strand an item in `harness:running`
forever. Items in `harness:running` with no live workflow run older than 3 hours are reset.

### 11.3 What an operator does

`docs/OPERATIONS.md` covers, with exact commands: a failed run, a stuck item, a rate-limited
window, a diverged fork, a disabled schedule, a leaked secret, and how to stop everything.

### 11.4 The kill switch, in both modes

**B148** — Local mode keeps the Delivery 1 `HALT` file semantics, checked at stage boundaries.

**B149** — Actions mode reads `.harness/HALT` from the default branch as the first step of every
spending workflow. Present → the job logs why and exits 0 without spending. Creating that file is
a one-line commit, works from a phone, and requires no access to any runner.

**B150** — `.harness/HALT` is checked **before** the dispatcher and before `harness doctor`. It is
the first thing every spending job does.

---

## 12. Invariants

Delivery 1's I-1 … I-10 remain in force **except I-2**, which is amended. Each has a test in
`tests/test_invariants.py` that fails the build if the property stops holding.

| # | Invariant | Enforced by |
| --- | --- | --- |
| **I-2′** | The `gh` CLI is never invoked. GitHub access is `harness/gh.py` only, which is the single module permitted to send an `Authorization` header. | AST walk over every subprocess argv; grep for `gh ` |
| **I-11** | Exactly one module (`gh.py`) constructs an authenticated request, and it reads the token from exactly one function in `config.py`. | AST: no other module imports the token getter |
| **I-12** | No code path merges a PR, approves a review, or dismisses a review. | absence of the endpoints; AST scan for `/merges`, `PUT /pulls/*/merge`, `/reviews` with `event=APPROVE` |
| **I-13** | Nothing is sent to GitHub that has not passed `redact.redact()`. | AST: every `gh.py` write method's body routes its payload through `redact` |
| **I-14** | No issue is created outside this repository. | AST: the issue-create call site's repo argument is the configured self-repo constant |
| **I-15** | No push may modify `.github/**`. | the token has no `workflow` scope — GitHub rejects it; **and** the existing `_reject_forbidden_diff` (B64, `implement.py:379`) already blocks any diff that touches a CI workflow, disables a check, or raises a timeout. Do not add a third check in `deliver.py`; extend B64's `FORBIDDEN_DIFF_PATHS` if the path set needs widening |
| **I-16** | No module above the store branches on execution mode. | `grep -rnE "GITHUB_ACTIONS\|RUNNER_OS\|ACTIONS_MODE\|is_actions\|execution_mode" harness/stages/ harness/gates.py harness/packager.py harness/governor.py --include=*.py` — note that `mode ==` is **not** in the pattern: `discover.py` legitimately branches on *discovery* mode (audit/directed/triage), which is unrelated |
| **I-17** | The package has no runtime dependency outside the standard library. | `pyproject.toml` has an empty `dependencies`; import scan |

**A31** — Every invariant above is verifiable by a reviewer running one grep or one test, and
`DELIVERY-2-REVIEW.md` §R2 lists the exact command for each.

---

## 13. Migration plan

Ordered. Each phase ends at a state where the repository is coherent and the test suite is green.
Do not begin a phase before its predecessor's checkpoint passes.

### Phase 0 — human prerequisites (§16)

Nothing is built until `HUMAN.md` is fully discharged. The machine account, the fork, the token
and the trust list are all prerequisites, not deliverables.

**Checkpoint:** `harness setup` exits 0.

### Phase 1 — the store seam, no behaviour change

Move `store.py` → `store/sqlite.py` verbatim. Define the `Store` protocol in `store/__init__.py`
from the methods `sqlite.py` already exposes. Nothing else changes; the whole existing suite must
pass untouched.

**Checkpoint:** full suite green; `git diff --stat` shows a move plus one new file; I-16 test added
and passing.

### Phase 2 — the ledger and the dispatcher

Both pure, both fully testable offline. `harness ledger`, `harness dispatch`.

**Checkpoint:** `test_ledger.py` and `test_dispatcher.py` green, including the rate-limited,
reserve-exhausted, and unmet-dependency paths.

### Phase 3 — `store/github.py`

Behind the protocol. Fixture-driven; no live call in the suite.

**Checkpoint:** `test_store_github.py` green; the five-command walkthrough runs end to end under
`BACKEND=fake` with the GitHub store fed by fixtures.

### Phase 4 — trust, keywords, sweep

`trust.py`, `keywords.py`, `harness sweep`.

**Checkpoint:** `test_trust.py` and `test_keywords.py` green, including B132's silent denial and
B135's replay no-op.

### Phase 5 — the write path

`gh.py` write methods, `deliver.py`, `revise.py`, `decompose.py`. Tier rises to 2 here and not
before.

**Checkpoint:** invariants I-11 … I-15 green. A dry-run flag proves the argv and payloads without
sending.

### Phase 6 — the workflows

All six YAML files. `selftest.yml` first and merged on its own, so every later PR is checked by it.

**Checkpoint:** `selftest.yml` green on both OSes; `heartbeat.yml` has posted once;
`workflow_dispatch` of `implement.yml` with `BACKEND=fake` completes and commits a ledger.

### Phase 7 — first live item, supervised

One item, `MAX_CONCURRENT_ITEMS=1`, watched end to end by a human.

**Checkpoint:** a merged proposal PR here, a delivery PR upstream with green gates, and a ledger
entry whose cost is within 2× the static estimate.

### Phase 8 — local mode

The container, the four scripts, the pin. Last, because it is the proof of concept rather than the
product, and because it must implement the same store seam Phase 1 established.

**Checkpoint:** the platform document's §9 acceptance list, all eight items, including "every
other harness's container status is identical before and after."

---

## 14. Acceptance criteria

Numbered A30 onwards, continuing Delivery 1's series. `DELIVERY-2-REVIEW.md` is the runnable
protocol; this is the list.

| # | Criterion |
| --- | --- |
| **A30** | `harness doctor` names every new config key and exits 3 on any missing or out of range |
| **A31** | Every invariant I-11…I-17 is checkable by one command, listed in the review protocol |
| **A32** | The full suite passes under `BACKEND=fake` with no network, on Linux and Windows |
| **A33** | `harness dispatch` starts nothing and its plan is deterministic given a fixed ledger |
| **A34** | A proposal with invalid front matter never becomes a PR (B103) |
| **A35** | A `touched_paths` entry absent from the product repo fails validation (B104) |
| **A36** | `harness sync-fork` fails loudly on a non-fast-forward and changes nothing (B105) |
| **A37** | An untrusted `/harness` comment produces no reply, no reaction, and no model call (B132) |
| **A38** | An untrusted comment body never reaches a prompt or unredacted disk (B133) |
| **A39** | A rate limit returns the item to its prior label and exits 0 (B120) |
| **A40** | A revision re-runs the full gate sequence; a red tree is never pushed (B136) |
| **A41** | The revise cap produces `harness:needs-human` and no further automatic action (B137) |
| **A42** | No code path merges or approves anything (I-12), demonstrated by grep and by test |
| **A43** | `.harness/HALT` on the default branch stops every spending workflow before it spends (B149, B150) |
| **A44** | `watchdog-bb.ps1` does not contain `watchdog.ps1`; starting and stopping `bb` leaves `rk` untouched |
| **A45** | The container gate fails closed at each of steps 2, 3 and 5 (§10.3) |
| **A46** | No GitHub token is present inside the container; the drop count is non-zero in the log (§10.5) |
| **A47** | Absence of the weekly heartbeat comment is the documented alarm, and `docs/OPERATIONS.md` says what to do (B144) |
| **A48** | A killed run leaves no item stranded in `harness:running` beyond 3 hours (B147) |
| **A49** | The ledger rebuilds from issue comments and matches within rounding (B117) |
| **A50** | Delivery 1's A1…A21 still hold, re-run unchanged |

---

## 15. Explicitly out of scope

Named so that their absence is a decision rather than an oversight.

- Multiple forks or multiple machine accounts.
- Stacked git branches. Dependencies are declared, not branched (B107).
- Auto-merge of a delivery PR on green. Both gates stay human.
- Any write to the product repository other than a branch on the fork and a PR from it.
- Issue creation on the product repository (I-14).
- Editing `.github/**` anywhere (I-15).
- A web UI. The GitHub UI is the UI.
- Concurrency above 1 in local mode.
- Any attempt to measure remaining subscription allowance (B114).
- Self-hosted runners.

---

## 16. Human prerequisites

These go into `HUMAN.md`, regenerated by `harness setup`. Nothing in Phase 1 onwards begins until
every one is discharged. Items are ordered; later ones depend on earlier ones.

| # | What | Who | Why it cannot be automated |
| --- | --- | --- | --- |
| 1 | Confirm **Nathan's GitHub handle** and put it in `.harness/trust.txt` | Jack | Currently `<NATHAN_HANDLE>`. Every keyword gate reads this file; until it is real, only Jack can steer. |
| 2 | Create the machine account `brightboost-harness` | Jack | Accepting terms is a human act. One machine account alongside a personal account is permitted. |
| 3 | Fork `Bright-Bots-Initiative/brightboost` **into the machine account** | Jack | The account must own the fork so PRs are attributable to automation, not to you. |
| 4 | Enable Actions on the fork | Jack | Disabled by default on forks. Needed only if you want the fork's CI as a second opinion; the harness runs its own gates regardless. |
| 5 | Create a **classic** PAT on the machine account, scope `public_repo` **only** | Jack | Fine-grained tokens cannot open fork→upstream PRs. Do not add `workflow`; its absence is invariant I-15. |
| 6 | Run `claude setup-token`; save as repo secret `CLAUDE_CODE_OAUTH_TOKEN` | Jack | Requires an interactive browser login against your subscription. |
| 7 | Save the PAT as repo secret `HARNESS_GITHUB_TOKEN` | Jack | |
| 8 | Make this repository public | Jack | Free unlimited Actions minutes on standard runners. Verify no secret is committed first. |
| 9 | Set branch protection on `main`: one approving review, no force-push | Jack | B113. |
| 10 | Commit `.github/CODEOWNERS` naming yourself for `.harness/`, `prompts/`, `.github/`, `gates.py`, `redact.py` | Jack | §5.5. |
| 11 | Create the labels in §4.2 | Jack | Or run `harness init --labels`, which creates them idempotently. |
| 12 | Open the pinned tracking issue and record its number in `.harness/config.json` | Jack | `heartbeat.yml` comments on it; its silence is the alarm. |
| 13 | Decide whether Nathan reviews proposals as well as delivery PRs, and set `CODEOWNERS` on `proposals/` accordingly | Jack + Nathan | You chose "either can approve"; this makes it mechanical. |
| 14 | Verify `claude --max-budget-usd` actually binds under subscription auth | Jack | Five-minute experiment. The help text says "API calls"; if it does not bind on a subscription, `PER_CALL_CAP_USD` is advisory and §6.1's "enforced twice" becomes "enforced once". Record the result in `DECISIONS.md`. |
| 15 | Install Docker Desktop and confirm `docker info` succeeds — local mode only | Jack | Phase 8 only. |
| 16 | Tell Nathan this exists, before the first delivery PR arrives | Jack | A bot PR from an unknown account on a public repo, unannounced, is a bad first impression and may be treated as spam. |

---

## 17. Open questions

Each has a proposed answer. None blocks Phase 1.

1. **Does `--max-budget-usd` bind on subscription auth?** Proposed: assume it does not until item
   14 above proves otherwise, and size `WEEKLY_CAP_USD` so that the harness's own accounting is
   sufficient alone.
2. **Should the fork's CI be required before `deliver` opens the upstream PR?** Proposed: no. The
   harness runs the same seven gates itself and captures the evidence; requiring the fork's CI adds
   a dependency on a setting that can be silently disabled. Treat it as confirmation, not a gate.
3. **What happens when upstream's gate sequence changes?** Proposed: `gates.py:_SEQUENCE` is pinned
   (§10.4), so a change upstream produces a baseline red the harness reports honestly rather than
   adapting to. Updating the sequence is a reviewed PR here. This is correct but will be surprising
   the first time; `docs/OPERATIONS.md` must cover it.
4. **Retention of `runs/` artifacts.** Proposed: 30 days, since the package that matters is
   committed to `packages/` and the artifact is only for post-mortems.

---

## 18. What this delivery does not buy

It buys that the recorded gate results were produced by the gate sequence you pinned, that no
model output became an instruction without passing a bounded schema, and that nothing reached the
product repository without two human approvals.

It buys nothing about whether the issues being worked are worth working. A harness that reliably,
auditably, and tamper-evidently implements the wrong backlog is still implementing the wrong
backlog, and the rigour of the machinery makes that harder to notice rather than easier.

The check on that is not in this document. It is the proposal gate, used properly: **reject
proposals.** If in the first month nothing is rejected at gate 1, the gate is decorative and the
harness is choosing the work.
