# Safety model

Nathaniel — this document assumes you have never seen this code and are not going to read
it. It exists to answer one question: **what can this thing actually do to your
repository?**

The short answer for Delivery 1 is *nothing*. It reads your public repository the way a
logged-out browser does, clones it, works in the clone, and writes a directory on a laptop.
It cannot push, cannot open a pull request, cannot comment, cannot file an issue. Not
because it has been told not to — because the code that would do those things has not been
written, and the credential that would authorise them is refused before it is loaded.

Below is every guarantee, what it means, how it is enforced, and how you can check it
yourself without trusting me.

---

## The tiers

| Tier | What it may do | Status |
|---|---|---|
| **Tier 0** | Read public GitHub unauthenticated. Clone. Work locally. Produce a package on disk. | **This is Delivery 1.** |
| Tier 1 | Post issue comments, file issues, under its own identity | Specified, not built, not agreed |
| Tier 2 | Push branches, open PRs | Specified, not built, not agreed |

Tier is a number in `.env` (`PERMISSION_TIER`). In Delivery 1 any value other than `0` is a
startup error, and the function that returns the bot token raises unconditionally while the
tier is below 1. Raising the tier is not something the harness can do to itself; it is an
edit you make on purpose, to a file that is not committed.

---

## The invariants

These are properties a reviewer confirms by *inspecting the source*, not by running the
program and hoping. Each has a test in `tests/test_invariants.py` that fails the build if
the property stops holding, which is what makes them structural rather than aspirational.

### I-1 — No module issues a non-GET HTTP request

**Guarantees:** the harness cannot create, modify, or delete anything on GitHub, because
every write on the GitHub API requires POST, PATCH, PUT, or DELETE and none of those verbs
appear anywhere in the package.

**Enforced by:** the absence of the code. The GitHub client has five read methods and one
request path, and that path never sets a method other than GET.

**Verify:** `grep -rnE '"(POST|PUT|PATCH|DELETE)"' harness/` returns nothing. The
invariant test does the same by walking the AST, so a computed method string cannot slip
past a grep.

### I-2 — The `gh` CLI is never invoked

**Guarantees:** the harness cannot borrow your GitHub credentials.

**Why this is its own invariant:** `gh` authenticates transparently with whatever token the
machine has. A single `gh issue list` would silently make the harness an authenticated
client using *your* identity, and nothing in the output would say so. Tier 0 would be void
and no one would know. So `gh` is banned outright rather than used carefully.

**Enforced by:** every subprocess in the package is `git`, `npm`, `npx`, `bash`, or
`claude`, spawned with `shell=False` and an explicit argv list — never a shell string, so
there is no place for a `gh` invocation to hide.

**Verify:** `grep -rn "gh " harness/` and `grep -rn "'gh'" harness/` return nothing that is
a command. The invariant test scans every subprocess argv construction.

### I-3 — Permission-skipping flags never appear

**Guarantees:** the model process always runs under the Claude CLI's own permission
prompting and edit-acceptance rules; it is never handed a blanket bypass.

**Enforced by:** the runner builds argv from a frozen template that has no branch capable
of adding `--dangerously-skip-permissions` or `--allow-dangerously-skip-permissions`.

**Verify:** `grep -rn "dangerously-skip-permissions" harness/` returns nothing.

### I-4 — `os.environ` is read only in `config.py`

**Guarantees:** there is exactly one door through which anything from your environment
enters the program, so there is exactly one place to audit.

**Enforced by:** every other module receives a frozen `Config` object. Nothing else imports
`os.environ`, and the test walks the AST of the whole package to confirm it.

**Verify:** `grep -rn "os.environ" harness/` names only `harness/config.py`.

### I-5 — SQL exists only in `store.py`

**Guarantees:** no stage, no prompt result, and no model output can reach the database
except through a small set of named methods with bound parameters.

**Enforced by:** a source scan for `SELECT`, `INSERT`, `UPDATE`, `DELETE`, and
`CREATE TABLE` across the package, which must match only `harness/store.py`.

**Verify:** `grep -rniE "select |insert |update |delete |create table" harness/` names only
the store.

### I-6 — `.gitignore` covers `.env`, `runs/`, and `HALT`

**Guarantees:** your configuration file, every disposable clone, every transcript, and the
kill switch cannot be committed by accident. `.env` is the only file that will ever hold a
token, and it is the first entry.

**Verify:** `cat .gitignore`, and `git check-ignore -v .env runs/ HALT`.

### I-7 — `npm run format` and `prettier --check .` are never invoked

**Guarantees:** the harness never reformats files it did not change.

**Why:** whole-tree prettier checks over-report badly on a Windows machine with
`core.autocrlf=true` and no `.gitattributes`, and a mass-formatting commit is unreviewable.
Formatting is scoped to the added and modified files in the diff, matching the product
repository's own `scripts/pr-review-prettier-check.sh`.

**Verify:** `grep -rn "run format" harness/` and `grep -rn "prettier --check ." harness/`
return nothing.

### I-8 — No file is written outside the harness's own directories

**Guarantees:** the harness cannot write to your home directory, your other repositories,
your shell profile, or anywhere else on the machine. The allowed set is `runs/`,
`packages/`, the configured database path, `HUMAN.md`, and `.env`.

**Enforced by:** a write guard that every write path in the package routes through. It
resolves the destination and compares it to the allowed roots; anything else raises
`WriteOutsideAllowedRoots` before a byte is written. Git and SQLite are the two exceptions,
and both are pointed at paths inside those roots.

**Verify:** the invariant test monkeypatches `open` and asserts nothing escapes. By hand:
watch `runs/` during a run and confirm nothing appears elsewhere.

### I-9 — The bot token is never transmitted in Delivery 1

**Guarantees:** even if you put a valid fine-grained PAT in `.env` today, no request will
carry it.

**Enforced by:** `HARNESS_GITHUB_TOKEN` is referenced in exactly two modules, `config.py`
and `identity.py`. The GitHub client does not import `identity`, so no request-building
code can reach the value. The one function that returns it raises `TierViolation` whenever
the tier is below 1 — the check is on the tier, not on the token, so a valid token changes
nothing.

**Verify:** `grep -rn "HARNESS_GITHUB_TOKEN" harness/` names two files.
`grep -n "identity" harness/gh.py` returns nothing. After any run, every request the
harness made is in the `api_call` table of `harness.db`.

### I-10 — `HUMAN.md` generation never interpolates an environment value

**Guarantees:** the setup document you are asked to read and act on can never contain a
secret, so it is safe to commit, safe to paste into a message, and safe to keep in the
repository.

**Enforced by:** the renderer is a pure function of a readiness assessment made of booleans
and fixed strings. It reads no environment and no config field whose name ends in `_token`
or `_key`. It emits no timestamp either, so re-running `harness setup` produces no spurious
diff.

**Verify:** scan `HUMAN.md` with the redaction patterns; there is a test that does exactly
this and fails if anything matches, including with a token set.

---

## Redaction

Every transcript, log line, and package file is scrubbed before it touches disk. The
patterns cover Anthropic keys (`sk-ant-…`), all GitHub token shapes (`ghp_`, `gho_`,
`ghu_`, `ghs_`, `ghr_`, `github_pat_`), AWS access key ids, PEM private key blocks,
`authorization:` / `api_key=` / `secret=` / `token=` / `password=` assignments in any case,
and the literal value of every secret-bearing key the config knows about. A match becomes
`[REDACTED]` and the rest of the line survives, so redacted output stays readable.

This is belt-and-braces: at Tier 0 there is no credential to leak. It is there so the
guarantee does not depend on that staying true.

---

## The identity, and why it is specified but inert

The harness will eventually act as `brightboost-harness`, a machine user account, rather
than as a person. Delivery 1 detects whether that account exists — an unauthenticated
`GET /users/brightboost-harness`, costing no credential — reports whether a token is
present and whether its shape is plausible, and uses none of it.

Specifying it now and forbidding its use until Tier 1 is deliberate. The identity is the
moment the harness first holds a credential. Today it holds none, which is most of why Tier
0 was easy to agree to: it cannot leak what it was never given. That argument survives only
if the token cannot be used before the tier that needs it, so the refusal lives in code, not
in this document.

The strongest argument for the identity is not convenience. It is honesty: agent-written
comments posted from a person's account read to every reviewer, the intern cohort included,
as that person's words. Posted from `brightboost-harness` they are labelled as what they
are, the whole audit trail filters by author, and revoking it is one action on your side
rather than a conversation about someone's account.

### Token scopes, when the time comes

A **fine-grained** personal access token scoped to `Bright-Bots-Initiative/brightboost`
alone. Never a classic token — classic scopes are account-wide and cannot be narrowed to one
repository.

| Permission | Tier 0 | Tier 1 (comment, file) | Tier 2 (open PRs) |
|---|---|---|---|
| *(no token at all)* | yes | — | — |
| `Metadata` | — | Read | Read |
| `Issues` | — | Read and write | Read and write |
| `Pull requests` | — | Read and write | Read and write |
| `Contents` | — | **none** | Read and write |
| `Workflows` | — | **none** | **none** |
| `Administration` | — | **none** | **none** |
| `Secrets`, `Environments`, `Actions` | — | **none** | **none** |

**Withholding `Workflows` is a capability guarantee, not a policy.** A fine-grained token
without it cannot push any commit that modifies `.github/workflows/` — GitHub itself rejects
the push. The acceptance criteria say the harness must never edit CI to go green. At Tier 2
that stops being a rule the harness is trusted to follow and becomes something it is
incapable of doing.

### What needs you specifically

| # | Action | Notes |
|---|---|---|
| 5 | Approve the fine-grained token for the organization, if the org restricts them | Organizations can require owner approval before a fine-grained token reaches their repositories |
| 6 | Agree Tier 1, so `PERMISSION_TIER` can be raised to `1` | Without this the token is refused in code regardless of what is in `.env` |
| 7 | Invite the account as a collaborator — Tier 2 only | Not needed for commenting on a public repository; needed to push a branch |
| 8 | Decide whether the account joins the organization or stays an outside collaborator | Affects visibility and seat count |

Everything else — creating the account, verifying its email, enabling 2FA, generating the
PAT, putting it in `.env` — is the operator's, and none of it is automatable.
`harness setup` regenerates `HUMAN.md` as a gap report that shrinks as these are done.

---

## What the harness will never ask you for

- Production credentials of any kind
- Organization administration
- The `Workflows` permission
- Branch-protection changes, or an exception to them
- Merge rights
- A classic personal access token
- Access to any repository other than `brightboost`

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
package carries the verbatim output with exit codes, never a summary, so you can see for
yourself.

One honest caveat, recorded rather than papered over: whether a local throwaway Postgres
counts as "using secrets" is not yet decided. Until it is, the gate sequence runs the
non-database subset and says so in `EVIDENCE.md` rather than claiming full parity with CI.

---

## The kill switch

Create the file named by `HALT_FILE` — `HALT` at the repository root by default — and the
harness stops at the next stage boundary: the clone is released, the item is left resumable,
and the run exits with code 5. `harness halt` creates it; `harness resume` removes it. The
contents are never read; only the file's existence matters, so `touch HALT` from any shell is
enough. It is also checked in clone preflight, so nothing is cloned while it is there.
