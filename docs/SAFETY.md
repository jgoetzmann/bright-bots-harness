# Safety model

Nathaniel — this document assumes you have never seen this code and are not going to read
it. It exists to answer one question: **what can this thing actually do to your
repository?**

The short answer for Delivery 1 was *nothing*. The short answer for Delivery 2 is: **it can
open a pull request against your repository from a fork it owns, and that is all.** It
cannot push to your repository, cannot merge anything, cannot approve or dismiss a review,
cannot file an issue on your repository, and cannot touch a CI workflow anywhere — the last
one because GitHub itself rejects the push, not because the harness has been asked not to.
Everything it delivers arrives as an ordinary PR from `jgoetzmann-bot:harness/…`, and
nothing lands without a human merging it.

Below is every guarantee, what it means, how it is enforced, and how you can check it
yourself without trusting me. Where Delivery 2 amended a Delivery 1 guarantee, the amendment
is shown as one, with its reason, rather than silently replacing the old text. The exact
commands are the ones `delivery/DELIVERY-2-REVIEW.md` §D2-R3 grades by.

---

## The tiers

| Tier | What it may do | Status |
|---|---|---|
| **Tier 0** | Read public GitHub unauthenticated. Clone. Work locally. Produce a package on disk. No `Authorization` header is ever sent. | **Delivery 1, still the default** (`PERMISSION_TIER=0` in `.env.example`). Also what the local `bb` container runs at: the GitHub token is filtered out before the container starts. |
| Tier 1 | Comment and file issues under its own identity, no push | **Not a mode this build implements.** `PERMISSION_TIER=1` is a startup error. |
| **Tier 2** | Everything in Tier 0, plus: push branches to **a fork the machine account owns**; open PRs from that fork into the product repository; comment on issues in **this** repository and on PRs it opened; create issues in **this** repository only. | **Delivery 2, Actions mode.** Requires `STORE_BACKEND=github` and a non-empty `FORK_REPO`; anything else is a startup error. |

Tier is a number in `.env` (`PERMISSION_TIER`). The only accepted values are `0` and `2`.
At tier 0 every Delivery 1 guarantee holds unchanged — including that no request carries a
token — and that is checked by the same tests as before. At tier 2 exactly one module,
`harness/gh.py`, may send an `Authorization` header, and it obtains the token from exactly one
function, `config.github_token()`, which returns an empty string at any tier other than 2. A
valid token in `.env` at tier 0 changes nothing.

Raising the tier is not something the harness can do to itself: `.env` is gitignored, outside
the harness's write roots, and read once at startup.

**What is honestly possible at Tier 2**, as a list of things you might see appear:

- A branch on `<machine-account>/brightboost`, under `harness/`. Never a branch on
  `Bright-Bots-Initiative/brightboost`.
- A pull request on `Bright-Bots-Initiative/brightboost`, opened by `jgoetzmann-bot`,
  with a review requested from every handle in `.harness/trust.txt`.
- A comment on that PR, or on an issue in this repository, signed `jgoetzmann-bot`.
- An issue in **this** repository (a `decompose` sub-issue, or an `ops:` failure report).
- A commit to `state/ledger.json` in this repository with `[skip ci]`, and a proposal PR
  into `proposals/` here that you merge or close.

Nothing else. No merge, no review approval, no review dismissal, no branch on the product
repository, no issue on the product repository, no edit to `.github/**` anywhere, and no
change to the fork's default branch other than a fast-forward from upstream.

---

## The invariants

These are properties a reviewer confirms by *inspecting the source*, not by running the
program and hoping. Each has a test in `tests/test_invariants.py` that fails the build if
the property stops holding, which is what makes them structural rather than aspirational.

Two Delivery 1 invariants are amended below and are marked as such. No Delivery 1 invariant
test was deleted; `git diff <d1-sha> -- tests/test_invariants.py` shows additions only.

### I-1′ — Only `harness/gh.py` issues a non-GET HTTP request  *(amends I-1)*

**Delivery 1 said:** no module issues a non-GET request; POST, PUT, PATCH and DELETE appear
nowhere in the package, so the harness cannot create, modify, or delete anything on GitHub.

**Amended because:** Delivery 2 pushes branches and opens PRs, which are writes. Rather than
scatter them, every write verb is confined to one module, and that module is the same one
I-11 names as the only authenticated client. The guarantee moves from "there is no write
code" to "all write code is in one file a reviewer can read in one sitting" — and it is paired
with I-11 so that the only file that can write is also the only file that can hold the token.

**Guarantees:** a stage, a prompt result, or a model output cannot create, modify, or delete
anything on GitHub except by calling a named method on `gh.py`. Each of those methods redacts
its payload first (I-13), raises `TierViolation` when the tier is not 2, and records what it
sent in `gh.sent` whether or not `--dry-run` was passed.

**Verify:** `grep -rnE '"(POST|PUT|PATCH|DELETE)"' harness/` names only `harness/gh.py`.
The invariant test walks the AST, so a computed method string cannot slip past the grep.

### I-2′ — The `gh` CLI is never invoked; `gh.py` is the single authenticated module  *(amends I-2)*

**Delivery 1 said:** `gh` is banned outright because it authenticates transparently with
whatever token the machine has, so a single `gh issue list` would silently make the harness an
authenticated client using *your* identity.

**Amended because:** the ban stands, unchanged. What changes is that the harness now *does*
hold a credential at tier 2, so the invariant has to say where that credential may be used:
`harness/gh.py` and nowhere else. GitHub access is `gh.py`, over `urllib.request`, with an
explicit token from an explicit door — never a CLI that finds its own.

**Enforced by:** every subprocess in the package is `git`, `npm`, `npx`, `bash`, `python`, or
`claude`, spawned with `shell=False` and an explicit argv list, so there is no place for a `gh`
invocation to hide. `git push` carries the token through `-c http.extraheader`, never in a
remote URL and never in a log line.

**Verify (R3.1):** `grep -rnE "['\"]gh['\"]\|\bgh \b" harness/ --include=*.py` returns no
command invocation. The invariant test scans every subprocess argv construction.

### I-3 — Permission-skipping flags never appear

**Guarantees:** the model process always runs under the Claude CLI's own permission
prompting and edit-acceptance rules; it is never handed a blanket bypass.

**Enforced by:** the runner builds argv from a frozen template that has no branch capable
of adding `--dangerously-skip-permissions` or `--allow-dangerously-skip-permissions`. The
only new argv element in Delivery 2 is `--max-budget-usd <PER_CALL_CAP_USD>`, a ceiling.

**Verify:** `grep -rn "dangerously-skip-permissions" harness/` returns nothing.

### I-4 — `os.environ` is read only in `config.py`

**Guarantees:** there is exactly one door through which anything from your environment
enters the program, so there is exactly one place to audit. Delivery 2 adds a second
configuration source, `.harness/config.json`, and it is read by the same function
(`load_config`) and accepts exactly the closed list of named keys in
`config.CONFIG_JSON_KEYS` — sixteen of them since Delivery 3 added the five
usage-governance knobs (D31/D32); any other key is a startup error.

**Verify:** `grep -rn "os.environ" harness/` names only `harness/config.py`.

### I-5 — SQL exists only in `harness/store/sqlite.py`  *(path amended, DECISIONS D12)*

**Guarantees:** no stage, no prompt result, and no model output can reach the database
except through a small set of named methods with bound parameters. The Delivery 1 file
`harness/store.py` was moved verbatim to `harness/store/sqlite.py` so a second store (GitHub
issues as the queue) could sit behind the same protocol; the invariant's path moved with it.

**Verify:** `grep -rniE "select |insert |update |delete |create table" harness/` names only
`harness/store/sqlite.py`. `test ! -f harness/store.py` — the move was a move, not a copy.

### I-6 — `.gitignore` covers `.env`, `runs/`, `HALT`, and `bb-work/`

**Guarantees:** your configuration file, every disposable clone, every transcript, the kill
switch, and the container's working directory cannot be committed by accident. `.env` is the
only file that will ever hold a token, and it is the first entry.

**Verify:** `cat .gitignore`, and `git check-ignore -v .env runs/ HALT bb-work`.

### I-7 — `npm run format` and `prettier --check .` are never invoked

**Guarantees:** the harness never reformats files it did not change. Formatting is scoped to
the added and modified files in the diff, matching the product repository's own
`scripts/pr-review-prettier-check.sh`.

**Verify:** `grep -rn "run format" harness/` and `grep -rn "prettier --check ." harness/`
return nothing.

### I-8 — No file is written outside the harness's own directories  *(roots amended, DECISIONS D17)*

**Guarantees:** the harness cannot write to your home directory, your other repositories,
your shell profile, or anywhere else on the machine. The allowed set is `runs/`, `packages/`,
the configured database path, `HUMAN.md`, `.env`, and — new in Delivery 2 — `state/` (the
ledger) and `proposals/` (the proposal files). Those two are the only additions.

**What is deliberately *not* a root:** `.harness/`. The harness cannot write its own trust
list, its own config, its own pin (`.harness/PIN`, B143), or its own kill switch
(`.harness/HALT`). Every one of those is a human commit, reviewed under `CODEOWNERS`.

**Enforced by:** a write guard that every write path in the package routes through. It
resolves the destination and compares it to the allowed roots; anything else raises
`WriteOutsideAllowedRoots` before a byte is written.

**Verify:** the invariant test monkeypatches `open` and asserts nothing escapes. By hand:
`grep -n "state\|proposals" harness/redact.py` shows exactly those two additions and no
`.harness`.

### I-9 — The bot token has exactly one door, and it is shut below Tier 2

**Guarantees:** even with a valid PAT in `.env`, no request carries it unless
`PERMISSION_TIER=2`, and at tier 2 only `harness/gh.py` can obtain it.

**Enforced by:** the literal `HARNESS_GITHUB_TOKEN` appears in `harness/config.py` only.
`identity.py`, which reports whether a token is *present*, reaches the key name through a
constant and never sees the value. The one getter, `config.github_token()`, returns the token
only when the last-loaded config has tier 2 and returns `""` otherwise — the check is on the
tier, not on the token, so a valid token at tier 0 changes nothing. That getter is imported by
`gh.py` and by nothing else (this is I-11).

**Verify:** `grep -rn "github_token\|HARNESS_GITHUB_TOKEN" harness/ --include=*.py` — the
getter is defined in `config.py` and imported only by `gh.py`. After any run, every request
the harness made is in the `api_call` table of `harness.db`, and every write is in `gh.sent`.

### I-10 — `HUMAN.md` generation never interpolates an environment value

**Guarantees:** the setup document you are asked to read and act on can never contain a
secret, so it is safe to commit, safe to paste into a message, and safe to keep in the
repository. Delivery 2 adds sixteen items to it (handoff §16); the renderer is unchanged.

**Verify:** scan `HUMAN.md` with the redaction patterns; there is a test that does exactly
this and fails if anything matches, including with a token set.

### I-11 — Exactly one authenticated client, exactly one token door

**Guarantees:** the token cannot be used from anywhere a reviewer has not read. `gh.py` is
the only module that constructs an `Authorization` header, and it reads the token from the
one function in `config.py` described under I-9. Nothing else imports that function.

**Verify (R3.2):** `grep -rn "Authorization" harness/ --include=*.py` — matches in
`harness/gh.py` only. One honest note: `harness/governor.py` defines a dataclass named
`Authorization` (a budget authorisation, frozen by Delivery 1's spec, DECISIONS D4); read
those hits and confirm none sets a header.
**Verify (R3.3):** `grep -rn "github_token\|HARNESS_GITHUB_TOKEN" harness/ --include=*.py`
— the getter is defined in `config.py` and imported only by `gh.py`.

### I-12 — The harness never merges, approves, or dismisses

**Guarantees:** both human gates are load-bearing because the code that would bypass them
does not exist. There is no method in `gh.py` whose name contains `merge`, `approve`, or
`dismiss`, and no URL built anywhere ends in `/merge` or carries a review `event`.

**Verify (R3.4):** `grep -rnE "/merge\b\|event.*APPROVE\|dismiss" harness/ --include=*.py`
returns nothing.
**Verify (R3.5):** `pytest tests/test_invariants.py -k merge -q` passes; the AST walk finds
no merge endpoint.

### I-13 — Nothing is sent to GitHub that has not passed `redact.redact()`

**Guarantees:** a comment, a PR body, an issue body, or a file pushed to a proposal branch
can no more carry a secret than a transcript on disk can. Every write method in `gh.py`
routes its payload through the redactor before encoding it.

**Verify (R3.6):** `pytest tests/test_invariants.py -k redact -q` passes; the test walks
every `gh.py` write method's body.

### I-14 — No issue is created outside this repository

**Guarantees:** `decompose` and `ops.yml` can file issues here, where the queue lives, and
nowhere else. The issue-create method takes no repository argument at all; it always targets
`SELF_REPO`. There is no call site to get wrong.

**Verify (R3.7):** `pytest tests/test_invariants.py -k issue_repo -q` passes. By hand:
`grep -n "def create_issue" harness/gh.py` shows a signature with no `repo` parameter.

### I-15 — No push may modify `.github/**`

**Guarantees:** the harness cannot edit CI to go green, anywhere, and this is enforced twice
by two things that fail differently. The classic PAT has no `workflow` scope, so GitHub
rejects any push touching `.github/workflows/` — a capability guarantee the harness cannot
argue with. And `_reject_forbidden_diff` in `stages/implement.py` (B64, unchanged from
Delivery 1) blocks the subtler cases a token scope cannot see: a disabled check or a raised
timeout in a file outside `.github/`. There is deliberately no third copy of this check in
`deliver.py`; divergent copies of a safety check are worse than one.

**Verify (R3.8):** inspect the PAT in the machine account's settings — classic, `public_repo`
only, no `workflow`; then `pytest tests/test_stages.py -k B64 -q` selects four or more tests
and passes.

### I-16 — No module above the store branches on execution mode

**Guarantees:** Actions mode and local mode are one harness, not two. A stage that knew which
mode it was in would be tested in one and rot in the other. The difference between modes
lives in exactly two places — which `Store` is opened and who runs the dispatcher — and
nothing above them can tell.

**Verify (R3.9):**
`grep -rnE "GITHUB_ACTIONS\|RUNNER_OS\|ACTIONS_MODE\|is_actions\|execution_mode" harness/stages/ harness/gates.py harness/packager.py harness/governor.py --include=*.py`
returns nothing. (`discover.py` branches on *discovery* mode — audit, directed, triage —
which is unrelated and is why `mode ==` is not in the pattern.)

### I-17 — The package has no runtime dependency outside the standard library

**Guarantees:** what you audit is what runs. Nothing is fetched at install time that could
change what the harness does between the review and the run.

**Verify (R3.10):** `pytest tests/test_invariants.py -k stdlib -q` passes. By hand:
`python -c "import tomllib,pathlib;d=tomllib.loads(pathlib.Path('pyproject.toml').read_text());print(d['project'].get('dependencies'))"`
prints `[]` or `None`.

### Everything at once

**Verify (R3.11):** `pytest tests/test_invariants.py -q` — all pass, Delivery 1's and
Delivery 2's together. `PERMISSION_TIER` in `.env` is irrelevant to this suite; it runs
under `BACKEND=fake` with no network.

---

## Redaction

Every transcript, log line, package file, ledger write, proposal file, and — new — every
byte sent to GitHub is scrubbed first. The patterns cover Anthropic keys (`sk-ant-…`), all
GitHub token shapes (`ghp_`, `gho_`, `ghu_`, `ghs_`, `ghr_`, `github_pat_`), AWS access key
ids, PEM private key blocks, `authorization:` / `api_key=` / `secret=` / `token=` /
`password=` assignments in any case, `Bearer <token>`, and the literal value of every
secret-bearing key the config knows about — which now includes `CLAUDE_CODE_OAUTH_TOKEN`. A
match becomes `[REDACTED]` and the rest of the line survives, so redacted output stays
readable.

In Delivery 1 this was belt-and-braces. In Delivery 2 the harness holds two credentials, so
it is load-bearing, and `harness/redact.py` is one of the three pinned files (below).

---

## The identity, now active

The harness acts as `jgoetzmann-bot`, a machine user account, not as a person. GitHub's
terms permit one machine account alongside a personal account. The account:

- **owns the fork** `<machine-account>/brightboost`. Not you. PRs are then unambiguously
  attributable to automation, the whole audit trail filters by author, and revoking it is
  one action on your side rather than a conversation about someone's account.
- holds one classic PAT, scope **`public_repo` only**.
- has **no** write access to the product repository and is **not** a collaborator on it.
  Verify: product repo → Settings → Collaborators — the machine account is absent (R5.5).

### Token scope — a correction to Delivery 1's table

Delivery 1's version of this document said "never a classic token". That was wrong for the
purpose at hand and is corrected here rather than quietly rewritten. **Fine-grained tokens
cannot open a pull request from a fork into an upstream repository** — a documented,
long-standing limitation. A classic token with `public_repo` is the working credential, and
on an account that owns nothing but the fork its blast radius is bounded: it can write to
public repositories the account can already write to, which is the fork, and it can open PRs,
which is the point.

| Scope | Granted | Why |
|---|---|---|
| `public_repo` | **yes** | push to the fork, open PRs, comment, create issues here |
| `workflow` | **no** | its absence is invariant I-15 — GitHub rejects any push touching `.github/workflows/` |
| `repo` (private) | no | there are no private repositories involved |
| `admin:*`, `delete_repo`, `write:org`, anything else | no | |

Verify (R5.3): the machine account → Settings → Developer settings → Personal access tokens →
Tokens (classic) — exactly one token, exactly one scope.

### What the token may and may not do (handoff §5.3)

| Operation | Allowed | Where |
|---|---|---|
| Read public repositories | yes | `gh.py` |
| Push a branch to the fork | yes | `deliver.py`, `revise.py` |
| Open a PR fork → product repo | yes | `deliver.py` |
| Comment on a PR it opened | yes | `deliver.py`, `revise.py` |
| Create issues in **this** repository | yes | `decompose.py`, `ops.yml` |
| Create issues in the product repository | **no** | I-14 |
| Merge, approve, or dismiss any review | **no** | I-12 |
| Push to `.github/**` anywhere | **no** | I-15, enforced by GitHub |
| Push to the product repository directly | **no** | not a collaborator |
| Modify the fork's default branch | **no**, except fast-forward from upstream | B105; `sync-fork` fails loudly on anything else |
| Force-push a branch a human has pushed to | **no** | B139; the item becomes `needs-human` |

### Where each secret goes (handoff §5.4)

| Secret | Where it lives | Reaches the container? | Reaches Actions? |
|---|---|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | repo secret; host `.env` locally | **yes** — the loop needs it | yes |
| `HARNESS_GITHUB_TOKEN` | repo secret; host `.env` locally | **no** — `local/container_env.ps1` strips it and prints the count it dropped | yes |

The container therefore runs at tier 0 with respect to GitHub: it commits, and the host
watchdog pushes. Verify (R5.7): `docker exec bb env | grep -c GITHUB` prints `0`.

---

## The trust gate (handoff §8)

The product repository is public. Anyone may comment on a delivery PR. Without a gate, any
GitHub user could spend your Claude allowance, or steer what the model writes, by typing
`/harness fix`. So every keyword command is authorised **before** its body is parsed, and it
is honoured only if **both** hold:

1. the commenter's handle is in `.harness/trust.txt` (case-insensitive), **and**
2. the comment's `author_association` is `OWNER`, `MEMBER`, or `COLLABORATOR`.

Two independent checks because they fail differently: the trust file is your intent; the
association is GitHub's assertion. Neither alone is enough.

A command from anyone else is **silently ignored** (B132): no reply, no reaction, no log line
that quotes the body. Replying would confirm the trigger exists and invite probing. The
denial is counted in the ledger under the handle only. The body of an untrusted comment is
never passed to a model, never interpolated into a prompt, never written to disk unredacted
(B133), and a replayed comment is a no-op (B135).

`.harness/trust.txt` is protected by `CODEOWNERS`; changing it is a reviewed PR.

**Verify:** `pytest tests/test_keywords.py -k "denied or untrusted_body or replay" -q` and
`pytest tests/test_trust.py -k case -q`. Live (R8.4): comment `/harness revise x` from an
account not in the file — nothing whatsoever happens.

---

## What the harness will never ask you for

- Production credentials of any kind
- Organization administration
- The `workflow` scope, or any scope beyond `public_repo`
- A fine-grained token with `Workflows` or `Administration` permission
- Collaborator access to `brightboost` for the machine account
- Branch-protection changes, or an exception to them
- Merge rights, review-approval rights, or auto-merge
- Access to any repository other than `brightboost` and this one
- A self-hosted runner
- Your own GitHub token, `gh` login, or `GH_TOKEN`

If something claiming to be this harness asks for any of the above, it is not this harness.

---

## Gates are never widened

The product repository's own gate sequence is the only definition of "it works" the harness
accepts: `npx prisma generate`, `npm run lint`, `npm run typecheck`, the backend typecheck,
the prisma drift check, `npm run test:unit`, `npm run build`.

A baseline run on the untouched tree happens **first**. Anything already red there is
recorded as pre-existing, is not attributed to the change, and is never used to justify
loosening anything.

No gate may be widened, skipped, given a longer timeout, or marked `continue-on-error` to
reach green. No diff may touch `.github/workflows/`, add `continue-on-error`, add a `.skip(`,
or raise a timeout; such a diff is rejected and the item is marked `blocked`. A red the
harness cannot fix honestly is a blocked item, not a passed one — and `EVIDENCE.md` in the
package (and, in Delivery 2, in the body of the delivery PR) carries the verbatim output with
exit codes, never a summary. A revision re-runs the **complete** sequence; there is no path
that ships a tree whose gates were not run after the last edit (B136).

**The sequence is pinned.** `harness/gates.py`, `harness/packager.py`, `harness/redact.py`
and every file under `prompts/` are hashed into `.harness/PIN`. Both modes refuse to start on
a mismatch (B142): the container exits 1 at its gate, and `implement.yml` fails at `doctor`
before spending. The harness cannot change its own pin — `.harness/` is outside its write
roots and `CODEOWNERS`-protected (B143). Verify (R1.13): `git diff --quiet <d1-sha> --
harness/gates.py harness/packager.py harness/redact.py` prints nothing.

One honest caveat, recorded rather than papered over: whether a local throwaway Postgres
counts as "using secrets" is not yet decided. Until it is, the gate sequence runs the
non-database subset and says so in `EVIDENCE.md` rather than claiming full parity with CI.

---

## The kill switch, in both modes

**Actions mode:** commit a file at `.harness/HALT` on the default branch. Its contents are
never read. It is the **first** step of every spending workflow — before `doctor`, before the
dispatcher — and its presence makes the job log `halted by .harness/HALT` and exit 0 without
spending (B149, B150). `harness dispatch` returns an empty plan with reason `halted` while it
exists. Creating it is a one-line commit from a phone; it needs no access to any runner.

**Local mode:** create the file named by `HALT_FILE` (`HALT` at the repository root by
default) and the harness stops at the next stage boundary: the clone is released, the item is
left resumable, and the run exits with code 5. `harness halt` creates it; `harness resume`
removes it. It is also checked in clone preflight and inside `implement` between gate runs.

**The container:** `.\bb-stop.ps1` writes `STOP` into the work directory; the loop honours it
at the next unit boundary and exits 0.

`docs/OPERATIONS.md` has the full procedure, including the order to do things in when a
credential has leaked.

---

## The worst thing a single bad run can do

Take the case the design permits and no check prevents: one run whose model output is wrong
in every respect at once. It spends at most `PER_CALL_CAP_USD` per model call and at most
what remains of the window under `WEEKLY_CAP_USD` less the reserve; it pushes a bad branch to
the fork the machine account owns; it opens one pull request against
`Bright-Bots-Initiative/brightboost` whose body carries the verbatim output of a pinned gate
sequence — output that cannot read green for a broken tree, because a red tree is a
`blocked` item that never reaches `deliver`; it requests review from the trusted handles;
and it comments on the tracking issue here saying so. Then it stops, because the item is now
`shipped` and nothing moves it further without a trusted human typing a command. It cannot
merge that PR, approve it, dismiss a review of it, edit your CI, touch your default branch,
file an issue on your repository, or run again on that item without `/harness fix`. If
instead the bad run was a `propose`, the result is one proposal PR here that you close; if it
was a `decompose`, at most `MAX_SUBISSUES` queued issues here, each of which still needs its
own proposal PR merged before a token is spent on it. The residual harm is one bad PR that two
humans must approve and a few dollars of spend. If a reading of this document, or of the
code, ever finds a worse answer than that, the review protocol has failed somewhere — commit
`.harness/HALT` and find it before the next scheduled run.
