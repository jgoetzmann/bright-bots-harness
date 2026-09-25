# Safety model

What can this harness do to the product repository? It can open a pull request against
`Bright-Bots-Initiative/brightboost` from a fork the machine account owns, and nothing more. It
cannot push to the product repository, merge anything, approve or dismiss a review, or file an issue
there, and it refuses to publish a change under `.github/` anywhere (I-15). Its token carries
GitHub's `workflow` scope so the fork can keep up with upstream CI changes, so GitHub would accept
such a push; the harness's own check refuses it before it is sent. Everything it delivers arrives as
an ordinary PR from `jgoetzmann-bot:harness/…`, and nothing lands without a person merging it.

Below is every guarantee, how it is enforced, and a command that checks it.

---

## The tiers

`PERMISSION_TIER` in `.env` accepts `0` and `2`; any other value is a startup error.

- **Tier 0**, the default in `.env.example`: read public GitHub unauthenticated, clone, work
  locally, produce a package on disk. No request carries an `Authorization` header. The local `bb`
  container runs at tier 0: the GitHub token is filtered out before it starts.
- **Tier 1** (comment and file issues, no push) is not implemented.
- **Tier 2**, Actions mode: everything in tier 0, plus push branches to a fork the machine account
  owns, open PRs from that fork into the product repository, comment on issues in this repository
  and on PRs it opened, and create issues in this repository only. It requires
  `STORE_BACKEND=github` and a non-empty `FORK_REPO`.

At tier 2 exactly one module, `harness/gh.py`, may send an `Authorization` header, and it obtains
the token from one function, `config.github_token()`, which returns an empty string at any other
tier. A valid token in `.env` at tier 0 changes nothing. `.env` is gitignored and read once at
startup.

At tier 2 you may see a branch under `harness/` on `<machine-account>/brightboost`; a pull request
on `Bright-Bots-Initiative/brightboost` opened by `jgoetzmann-bot`, with a review requested from
every handle in `.harness/trust.txt`; an issue on the product repository from that account, filed
when it delivers an item the product repository had no issue for and labelled `harness-tracking`
(I-14); a comment from that account on such a PR or on an issue here; an issue in this
repository (a `decompose` sub-issue, or an `ops:` failure report); a commit to
`state/ledger.json` on this repository's `harness-state` branch with `[skip ci]`; and a proposal
PR into `proposals/` here that you merge or close.

Nothing else: no merge, no review approval or dismissal, no branch on the product repository and
no other issue there, no change under `.github/` published anywhere, and no change to the fork's
default branch other than a fast-forward from upstream.

---

## The invariants

Each is a property a reviewer confirms by inspecting the source, and each has a test in
`tests/test_invariants.py` that fails the build if it stops holding.

### I-1 — Only `harness/gh.py` issues a non-GET HTTP request

A stage, a prompt result or a model output cannot create, modify or delete anything on GitHub except
by calling a named method on `gh.py`, the module I-11 names as the only authenticated client. Each
of those methods redacts its payload first (I-13), raises `TierViolation` when the tier is not 2,
and records what it sent in `gh.sent` whether or not `--dry-run` was passed.

**Verify:** `grep -rnE '"(POST|PUT|PATCH|DELETE)"' harness/` names only `harness/gh.py`. The
invariant test walks the AST, so a computed method string cannot slip past the grep.

### I-2 — The `gh` CLI is never invoked

The `gh` CLI authenticates with whatever token the machine has, so one call would make the harness
an authenticated client under someone else's identity. GitHub access is `gh.py` over
`urllib.request`, with the token from `config.github_token()` and nowhere else. Every subprocess in
the package is `git`, `npm`, `npx`, `bash`, `python` or `claude`, spawned with `shell=False` and an
explicit argv list, and `git push` carries the token through `-c http.extraheader`, never in a
remote URL or a log line.

**Verify:** `grep -rnE "['\"]gh['\"]\|\bgh \b" harness/ --include=*.py` returns no command
invocation. The invariant test scans every subprocess argv construction.

### I-3 — Permission-skipping flags never appear

The model process always runs under the Claude CLI's own permission prompting and edit-acceptance
rules; it is never handed a blanket bypass. The runner builds argv from a frozen template with no
branch that can add `--dangerously-skip-permissions` or `--allow-dangerously-skip-permissions`. The
other elements are restrictions: `--max-turns <MAX_TURNS_*>`, the per-call turn ceiling;
`--output-format stream-json --verbose` in place of `--output-format json` when usage capture is on,
so the CLI's `rate_limit_event` lines reach the governor (B200, `ClaudeCliRunner.build_argv` in
`harness/runner/cli.py`), which `get_runner` turns on for the real `cli` backend (B202); the prompt
on stdin rather than in argv (B216); and `--setting-sources ""` followed by a `--settings` document
holding a `permissions.deny` list (B218):

```
--setting-sources "" --settings {"permissions":{"deny":["Read(<root>/.env)", …]}}
```

`harness.stages.deny_read_paths` builds that list from the repository root and the operator's home
directory. `Read` is not confined to the working directory and can reach the harness's own `.env`,
which on an Actions runner sits above every stage's `cwd`. Emptying `--setting-sources` stops the
operator's `~/.claude` and this repository's `.claude/` from widening what a stage may touch. The
list closes the credential path and is not a sandbox: the CLI's rule syntax cannot express "nothing
outside this directory", and a stage can still read an unrelated checkout on the same disk.

**Verify:** `grep -rn "dangerously-skip-permissions" harness/` returns nothing.
`python -m pytest tests/test_runner_cli.py -k B218` pins the flags and the rule form.

### I-4 — `os.environ` is read only in `config.py`

Anything from your environment enters the program through one place. The second configuration
source, `.harness/config.json`, is read by the same function (`load_config`) and accepts only the
closed list of keys in `config.CONFIG_JSON_KEYS`; any other key is a startup error. The one
exception is `config.RETIRED_KEYS`, the keys D74 and D89 removed: they are accepted and ignored
wherever a key is accepted, so an existing `.env` keeps loading, and `harness doctor` names each
one it finds as a warning.

**Verify:** `grep -rn "os.environ" harness/` names only `harness/config.py`.

### I-5 — SQL exists only in `harness/store/sqlite.py`

No stage, prompt result or model output can reach the database except through a small set of named
methods with bound parameters (D12).

**Verify:** `grep -rniE "select |insert |update |delete |create table" harness/` names only
`harness/store/sqlite.py`.

### I-6 — `.gitignore` covers `.env`, `runs/`, `HALT`, and `bb-work/`

Your configuration file, every disposable clone, every transcript, the local kill switch and the
container's working directory cannot be committed by accident. `.env` is the only file that holds a
token, and it is the first entry.

**Verify:** `cat .gitignore`, and `git check-ignore -v .env runs/ HALT bb-work`.

### I-7 — `npm run format` and `prettier --check .` are never invoked

The harness never reformats files it did not change. Formatting is scoped to the added and modified
files in the diff, matching the product repository's own `scripts/pr-review-prettier-check.sh`.

**Verify:** `grep -rn "run format" harness/` and `grep -rn "prettier --check ." harness/` return
nothing.

### I-8 — No file is written outside the harness's own directories

The harness cannot write to your home directory, your other repositories, your shell profile, or
anywhere else on the machine. The allowed roots are `runs/`, `packages/`, the configured database
directory, `state/` (the ledger), `proposals/`, the `HUMAN.md` setup report, `.env` and `HALT_FILE`
(D17). `.harness/` is not a root, so the harness cannot write its own trust list, config, pin
(`.harness/PIN`, B143) or kill switch (`.harness/HALT`); each of those is a human commit reviewed
under `CODEOWNERS`. Every write routes through a guard that resolves the destination and compares it
with the allowed roots; anything else raises `WriteOutsideAllowedRoots` before a byte is written.

**Verify:** the invariant test monkeypatches `open` and asserts nothing escapes. By hand,
`grep -n "state\|proposals" harness/context.py` shows the roots, and no `.harness`.

### I-9 — The bot token has one door, shut below tier 2

Even with a valid PAT in `.env`, no request carries it unless `PERMISSION_TIER=2`, and at tier 2
only `harness/gh.py` can obtain it. The literal `HARNESS_GITHUB_TOKEN` appears in
`harness/config.py` only; `identity.py`, which reports whether a token is present, reaches the
key name through a constant and never sees the value. The getter `config.github_token()` returns
the token only when the last-loaded config has tier 2, and `""` otherwise: it checks the tier,
not the token.

**Verify:** `grep -rn "github_token\|HARNESS_GITHUB_TOKEN" harness/ --include=*.py`: the getter is
defined in `config.py` and imported only by `gh.py`. After any run, every request the harness made
is in the `api_call` table of `harness.db`, and every write is in `gh.sent`.

### I-10 — The setup report never interpolates an environment value

The prerequisites report `harness setup` writes to `HUMAN.md` cannot contain a secret, so it is safe
to commit or paste into a message.

**Verify:** `pytest tests/test_identity.py -k B83 -q` renders the report with and without a token
set and scans it with the redaction patterns.

### I-11 — One authenticated client, one token door

The token cannot be used from anywhere a reviewer has not read. `gh.py` is the only module that
constructs an `Authorization` header, and it reads the token from the `config.py` function described
under I-9. Nothing else imports that function.

**Verify:** `grep -rn "Authorization" harness/ --include=*.py` matches in `harness/gh.py`, and in
`harness/governor.py`, whose dataclass named `Authorization` admits one model call (D4); read
those hits and confirm none sets a header.

### I-12 — The harness never merges, approves, or dismisses

There is no method in `gh.py` whose name contains `merge`, `approve` or `dismiss`, and no URL built
anywhere ends in `/merge` or carries a review `event`, so both human gates need a person.

**Verify:** `grep -rnE "/merge\b\|event.*APPROVE\|dismiss" harness/ --include=*.py` returns nothing,
and `pytest tests/test_invariants.py -k merge -q` passes; its AST walk finds no merge endpoint.

### I-13 — Nothing is sent to GitHub that has not passed `redact.redact()`

A comment, a PR body, an issue body or a file pushed to a proposal branch can no more carry a secret
than a transcript on disk can. Every write method in `gh.py` routes its payload through the redactor
before encoding it.

**Verify:** `pytest tests/test_invariants.py -k redact -q` passes; the test walks every `gh.py`
write method's body.

### I-14 — No issue is created outside this repository, except one per delivered item

`decompose` and `ops.yml` can file issues here, where the queue lives. The issue-create method
takes no repository argument; it always targets `SELF_REPO`. The one exception is a delivery of an
item the product repository has no issue for: `deliver` files one issue there, found again by its
marker on a later delivery, so the pull request has an issue to close (D83).
`create_product_issue` also takes no repository argument, always targets the product repository
the client reads, and has no other caller. `COMMENT_UPSTREAM=false` turns it off.

The machine account holds Triage on the product repository, which would let it label or close
anybody's thread there. So `close_pull`, `request_reviewers`, `edit_product_issue` and
`label_product_issue`, the `gh.py` writes that can reach a product thread's state, first read who
opened the thread and refuse one this account did not open. `label_product_issue` takes no
repository argument, and only `deliver` names it. The queue's own `set_labels`,
`update_issue_body` and `create_label` name `SELF_REPO` at every call site (D88).

**Verify:** `pytest tests/test_invariants.py -k "i14 or B518" -q` passes. By hand,
`grep -n "def create_issue\|def create_product_issue" harness/gh.py` shows two signatures with
no `repo` parameter, and `grep -n "_require_own_thread" harness/gh.py` shows the check in the four
writes above.

### I-15 — No push may modify `.github/**`

The harness cannot edit CI to go green, anywhere. The token carries the `workflow` scope (D67),
because without it `harness sync-fork` cannot fast-forward the fork past upstream's own workflow
commits, so GitHub would accept a push that touches `.github/workflows/`. The harness's own code
enforces I-15. One predicate over one path set, all of `.github/` (workflows, composite actions,
`dependabot.yml`, `CODEOWNERS`), is defined in `harness/clone.py` (`PROTECTED_PUSH_PATHS`,
`protected_paths_in`, `walk_harness_commits`), and every check reads it:

- **Before every push**, `gh.push_branch` walks the branch from its tip over the commits the harness
  authored and refuses if any of them touches `.github/`, deletions included (B298). It asks which
  commits the harness wrote, not what the branch differs by from a recorded base, so a rebase onto
  an upstream that changed its own CI still delivers (B299, B300). It runs under `--dry-run` too,
  and refuses when git cannot answer.
- **Before `deliver` pushes**, an author-blind check reads every commit the push would send that
  upstream does not already hold, from `FETCH_HEAD`, and refuses on any `.github/` path; the model
  holds `Bash`, so a commit it makes can name any author (B313). A handoff runs the same check
  against the fork's `main`, fetched at check time so a moved local ref cannot mislead it (B314).
- **Before implement or revise commits its work**, `_reject_forbidden_diff` in `stages/implement.py`
  (B64) blocks a diff that touches `.github/`, and the cases no path check can see: a disabled check
  or a raised timeout in a file outside `.github/`. `revise` fixes its diff base before the model
  runs, so a commit the model makes itself is still inside the diff (B302, B303). A handoff instead
  commits interrupted work unchecked, so nothing is lost, and withholds the push when the branch
  carries a `.github/` path: the item is blocked rather than carried, and the withheld commits are
  kept beside the note (B301, B315).
- **Every guard-side read is replace-proof.** The walk, B64's diffs and the author-blind checks all
  begin with `git --no-replace-objects -c core.commitGraph=false`, so a `refs/replace/` entry or a
  stale commit-graph cannot show a check a history the push does not send. A clone carrying a
  `refs/replace/` ref, a grafts file or a shallow history is refused (B312). In local mode
  `local/watchdog-bb.ps1`, the only publisher there, runs the same walk with the same prefix, author
  emails and substitution checks before it pushes (B304).

`implement.py`, `deliver.py`, `revise.py` and `gh.py` all read the one path set in `clone.py`, and
`tests/test_invariants.py` fails the build if that changes (B308). `local/preflight.py` and a test
that runs both walks over the same branch hold the PowerShell copy to it. `harness doctor` reads the
token's real scopes before every spending run and warns when they differ from `public_repo`,
`notifications`, `workflow` (B305).

**Verify:** `pytest tests/test_stages.py -k B64 -q` selects four or more tests and passes;
`pytest tests/test_gh.py tests/test_stages_deliver.py -k "B298 or B299 or B301" -q` passes; and
`harness doctor` prints `token scopes: notifications, public_repo, workflow` with no scope warning.

### I-16 — No module above the store branches on execution mode

Actions mode and local mode are one harness. The difference between them lives in two places, which
`Store` is opened and who runs the dispatcher, and nothing above those can tell which mode it is in.

**Verify:**
`grep -rnE "GITHUB_ACTIONS\|RUNNER_OS\|ACTIONS_MODE\|is_actions\|execution_mode" harness/stages/ harness/gates.py harness/packager.py harness/governor.py --include=*.py`
returns nothing. `discover.py` branches on discovery mode (audit, directed, triage), which is
unrelated, so `mode ==` is not in the pattern.

### I-17 — The package has no runtime dependency outside the standard library

What you audit is what runs. Nothing is fetched at install time that could change what the harness
does between the review and the run.

**Verify:** `pytest tests/test_invariants.py -k stdlib -q` passes. By hand:
`python -c "import tomllib,pathlib;d=tomllib.loads(pathlib.Path('pyproject.toml').read_text());print(d['project'].get('dependencies'))"`
prints `[]` or `None`.

`pytest tests/test_invariants.py -q` runs all of them. `PERMISSION_TIER` in `.env` is irrelevant to
that suite; it runs under `BACKEND=fake` with no network.

---

## Redaction

Every transcript, log line, package file, ledger write, proposal file and byte sent to GitHub is
scrubbed first. The patterns cover Anthropic keys (`sk-ant-…`), all GitHub token shapes (`ghp_`,
`gho_`, `ghu_`, `ghs_`, `ghr_`, `github_pat_`), AWS access key ids, PEM private key blocks,
`authorization:` / `api_key=` / `secret=` / `token=` / `password=` assignments in any case,
`Bearer <token>`, and the literal value of every secret-bearing key the config knows about,
including `CLAUDE_CODE_OAUTH_TOKEN`. A match becomes `[REDACTED]` and the rest of the line survives,
so redacted output stays readable. `harness/redact.py` is one of the pinned files (below).

---

## The identity

The harness acts as `jgoetzmann-bot`, a machine user account. GitHub's terms permit one machine
account alongside a personal account. The account:

- **owns the fork** `<machine-account>/brightboost`, so its PRs are attributable to automation, the
  audit trail filters by author, and revoking it is one action on your side.
- holds one classic PAT, scopes **`public_repo`, `notifications` and `workflow`**, nothing
  else (D67).
- has **no** write access to the product repository. It holds the Triage role there, which lets
  it label its tracking issues and request review on its pull requests, and which cannot push,
  merge, lock a conversation or create a label. Verify: product repo → Settings → Collaborators;
  the machine account is listed with the Triage role and nothing higher.

The token is classic because a fine-grained token cannot open a pull request from a fork into an
upstream repository. On an account that owns nothing but the fork, its blast radius is the fork and
the pull requests it opens.

| Scope | Granted | Why |
|---|---|---|
| `public_repo` | **yes** | push to the fork, open PRs, comment, create issues here |
| `notifications` | **yes** | read the notifications feed, so a mention on a product issue the machine account has never touched is seen |
| `workflow` | **yes** | fast-forward the fork past upstream's own CI commits (I-15) |
| `repo` (private) | no | there are no private repositories involved |
| `admin:*`, `delete_repo`, `write:org`, anything else | no | `harness doctor` warns if one appears |

Verify: `harness doctor` reads the scopes off the token before every spending run, prints
`token scopes: notifications, public_repo, workflow`, and warns on a missing or extra one. By hand:
the machine account → Settings → Developer settings → Personal access tokens → Tokens (classic)
shows exactly one token with those three scopes.

| Operation | Allowed | Where |
|---|---|---|
| Read public repositories | yes | `gh.py` |
| Push a branch to the fork | yes | `deliver.py`, `revise.py` |
| Open a PR fork → product repo | yes | `deliver.py` |
| Comment on a PR it opened | yes | `deliver.py`, `revise.py` |
| Create issues in **this** repository | yes | `decompose.py`, `ops.yml` |
| Create issues in the product repository | one per delivery, for an item with none | I-14 |
| Label or request review on a product thread | only on one the machine account opened | I-14, D88 |
| Close or relabel somebody else's thread | **no** | I-14, D88; each write checks who opened it |
| Merge, approve, or dismiss any review | **no** | I-12 |
| Push to `.github/**` anywhere | **no** | I-15, the harness's check before every push |
| Push to the product repository directly | **no** | Triage cannot push |
| Modify the fork's default branch | **no**, except fast-forward from upstream | B105; `sync-fork` fails loudly on anything else |
| Force-push a branch a human has pushed to | **no** | B139; the item becomes `needs-human` |

`CLAUDE_CODE_OAUTH_TOKEN` lives in a repository secret and, locally, the host `.env`; it reaches
both Actions and the container, which needs it. `HARNESS_GITHUB_TOKEN` lives in the same two places
and reaches Actions only: `local/container_env.ps1` strips it before the container starts and prints
the count it dropped. The container therefore runs at tier 0 with respect to GitHub — it commits,
and the host watchdog pushes. Verify: `docker exec bb env | grep -c GITHUB` prints `0`.

---

## The trust gate

The product repository is public, so anyone may comment on a delivery PR. Every keyword command is
authorised before its body is parsed, and honoured only if both hold:

1. the commenter's handle is in `.harness/trust.txt` (case-insensitive) at a level the verb reaches,
   and
2. GitHub confirms the identity behind it: either an `author_association` of `OWNER`, `MEMBER` or
   `COLLABORATOR`, or the account id the line vouches for (D68).

The trust file states your intent; the second condition is GitHub's assertion about who is typing.
The level caps what either may do.

The vouched form is the ordinary one (D69). A line ending `vouch:<id>`, the numeric GitHub user id
of one account, pins the line to that account: it passes wherever that account comments, whatever
its association, and is refused when the id does not match. A login can be renamed and claimed by
somebody else, and an association is granted per repository; an account id is immutable and never
reused. So a vouched line admits one account everywhere and grants it no repository access, which
lets a maintainer steer the harness without an invitation (D30). A line without a vouch relies on
the association, for somebody who is already a collaborator.

A line that would grant less than it says is refused whole: a level outside 1–3, a handle that is
not a GitHub login, a malformed vouch, a stray token after the handle, or two lines disagreeing
about which account a handle is. The line grants nothing, and `harness doctor` and
`harness trust show` name it. The sweep, `harness ack` and revise's review filter all decide through
one function, `trust.comment_authorised`, and a test fails the build if any other module reads the
association or calls the inner judge itself.

A command from anyone else is ignored with no reply, no reaction and no log line that quotes the
body (B132); a reply would confirm the trigger exists. The denial is counted in the ledger under the
handle only. The body of an untrusted comment is never passed to a model, never interpolated into a
prompt, never written to disk unredacted (B133), and a replayed comment is a no-op (B135).
`.harness/trust.txt` is protected by `CODEOWNERS`; changing it is a reviewed PR.

**Verify:** `pytest tests/test_keywords.py -k "denied or untrusted_body or replay" -q` and
`pytest tests/test_trust.py -k case -q`. Live: comment `/harness revise x` from an account not in
the file, and nothing happens.

---

## What the harness will never ask you for

- Production credentials of any kind
- Organization administration
- Any classic scope beyond `public_repo`, `notifications` and `workflow`
- A fine-grained token with `Workflows` or `Administration` permission
- Any role above Triage on `brightboost` for the machine account
- Branch-protection changes, or an exception to them
- Merge rights, review-approval rights, or auto-merge
- Access to any repository other than `brightboost` and this one
- A self-hosted runner
- Your own GitHub token, `gh` login, or `GH_TOKEN`

If something claiming to be this harness asks for any of the above, it is not this harness.

---

## Gates are never widened

The product repository's own gate sequence is the only definition of "it works" the harness accepts:
`npx prisma generate`, `npm run lint`, `npm run typecheck`, the backend typecheck, the prisma drift
check, `npm run test:unit`, `npm run build`. A baseline run on the untouched tree happens first, and
anything already red there is recorded as pre-existing, is not attributed to the change, and never
justifies loosening anything.

No gate may be widened, skipped, given a longer timeout, or marked `continue-on-error` to reach
green. A diff that touches anything under `.github/`, adds `continue-on-error` or a `.skip(`, or
raises a timeout is rejected and the item is marked `blocked`. A red the harness cannot fix is a
blocked item. `EVIDENCE.md` in the package carries the verbatim gate output with exit codes, and
opens with a note naming the database gates when they were not run; the delivery PR body carries a
digest that keeps every failing gate's output whole. A revision re-runs the complete sequence, so no
tree ships whose gates were not run after the last edit (B136).

**The sequence is pinned.** `harness/gates.py`, `harness/packager.py`, `harness/redact.py` and every
file under `prompts/` are hashed into `.harness/PIN`. Both modes refuse to start on a mismatch
(B142): the container exits 1 at its gate, and `implement.yml` fails at `doctor` before spending.
The harness cannot change its own pin, because `.harness/` is outside its write roots and
`CODEOWNERS`-protected (B143). Verify: `python -m harness.verify_pin --check` exits 0.

---

## The self-audit is advisory (D70)

Once an item's gates have no new failures, `implement` makes the one model call in the harness that
reviews another model's output: `selfaudit` reads the committed diff against the approved work
package and answers whether the change does what was approved, and only that. Every other check here
is mechanical, so this one is held to these limits:

- **Its findings are opinion.** A finding never blocks delivery and never sits beside gate output as
  a measurement. Blocking findings get a `selfaudit_fix` pass, up to `MAX_SELF_AUDIT_CYCLES` (`0`
  turns the audit off), and a repeated finding stops the loop. Whatever survives reaches the
  reviewer as one line in the delivery PR, labelled "a model reviewing its own diff; an opinion, not
  a gate result", and every finding is in the package's `DECISIONS.md`. An answer the harness cannot
  read, a failed call or an unreadable diff is recorded as *not run* and blocks nothing.
- **It never loosens a gate.** A fix pass is re-gated in full, and one that turns a green gate red
  is reverted to the pre-fix commit, whose results are written back. A fix pass whose diff touches
  `.github/`, or adds anything else B64 refuses, blocks the item like any other forbidden diff.
- **The tools.** The auditor holds `Read`, `Glob`, `Grep` and `Bash`, with `Edit`, `Write`,
  `WebFetch` and `WebSearch` denied (`implement.SELFAUDIT_ALLOWED_TOOLS`). The fix pass holds
  implement's tools.
- **The credential exposure.** The deny rules bind the read tool only, and the machine credential
  sits in `$GITHUB_WORKSPACE/.env`, three directories above the clone, so every call holding `Bash`
  can reach it: `implement`, `revise`, `selfaudit` and `selfaudit_fix`. This is not mitigated.
  Giving the auditor `ask.py`'s read-only tool tuples would take its shell away.
- **The tree guard.** An auditor holding `Bash` can still write to the clone or move its branch.
  The change set against the audited commit, and the branch and HEAD commit, are read before and
  after the call. If either changed, HEAD, the index and the tree are put back on that commit,
  anything the auditor introduced is removed, and its audit is discarded as *not run: the auditor
  modified the tree*. A clone that still differs afterwards blocks the item. A fix pass may edit but
  not commit: a branch it moved is put back on the tip, with its edits left for B64, the formatter
  and the gates.
- **A halt inside the loop hands the item off** with its committed work (`deliver.handoff`) rather
  than releasing the clone. A usage stop or a rate limit is not caught there, and ends the run as it
  does everywhere else.

---

## The kill switches

`.harness/HALT` committed on the default branch stops Actions mode at the first step of every
spending workflow (B149, B150). `HALT_FILE` stops a local run at the next stage boundary with exit
code 5. `.\bb-stop.ps1` stops the container at the next unit boundary. `docs/OPERATIONS.md` §8 has
the procedure, and §7 the order of steps when a credential leaks.

`/harness block <n>` is not a fourth switch, and it is not the reverse of one. It suspends the run
window for a few five-hour sessions so the operator can lend the harness time they are not going to
use (D77), and it lifts the calendar alone: the usage stop, all three kill switches, the trust
gate and the two human gates apply exactly as they did, no extra item may run at once, and the
grant expires by itself without anything having to run. It is capped at six sessions, a count
above that is refused rather than quietly reduced, and no grant runs longer than the count
whatever the session reading says.

---

## The worst a single bad run can do

Take one run whose model output is wrong in every respect at once. It runs within its turn cap and
the usage stop. It pushes a bad branch to the fork the machine account owns, and opens one pull
request against `Bright-Bots-Initiative/brightboost` whose body carries the output of a pinned gate
sequence. That output cannot read green for a broken tree, because a red tree is a `blocked` item
that never reaches `deliver`. It requests review from the trusted handles and comments on its issue
here. Then it stops: the item is `stage:needs-review`, and nothing moves it further without a
trusted person typing a command.

It cannot merge that PR, approve it, dismiss a review of it, edit your CI, touch your default
branch, file an issue on your repository, or run again on that item without `/harness revise`. A bad
`propose` produces one proposal PR here that you close; a bad `decompose` produces at most
`MAX_SUBISSUES` queued issues here, each of which still needs its own proposal PR merged before it
is implemented. The residual harm is one bad PR that two people must approve. If a reading of this
document or the code finds a worse outcome, commit `.harness/HALT` and find the cause before the
next scheduled run.
