# Bright Bots Harness — Delivery 1 Review Protocol

**Reviews:** an implementation claiming to satisfy `HARNESS-SPEC.md` v1.1
**Run from:** the root of the `Bright-Bots-Harness` repository
**Reviewer:** a human, or an agent instructed to follow this file literally

---

## How to use this

Work the sections in order. **R1 through R4 cost nothing and require no Claude capacity** — do all of them
before spending anything on R5. A failure in R1–R3 means stop and return the work; there is no point spending
budget proving that a system with a missing module also fails live.

Every check has an **ID**, a **command**, an **expected result**, and a **failure meaning**. Record each as
`PASS`, `FAIL`, or `N/A` with evidence pasted verbatim. A check whose command errors for an unrelated reason
is `BLOCKED`, not `PASS`.

**Do not fix what you are reviewing.** A reviewer who repairs a defect loses the ability to report it. Record
and return.

### Verdict rubric

| Verdict | Condition |
|---|---|
| **Accepted** | Every check in R1–R4 and R6–R8 passes, and R5 was run with the recorded outcome being either green gates or a properly `blocked` item |
| **Accepted with findings** | All of the above, plus one or more `minor` findings that do not touch §9 invariants or §10.5 safety |
| **Returned** | Any `major` finding, any R2 invariant failure, or any R7 safety failure |
| **Blocked** | The environment prevented a check from running and the gap is load-bearing |

A single R2 or R7 failure is `Returned` regardless of how much else passes. Those sections are the reason the
scope was agreed to in the first place.

---

## R0 — Preconditions

| ID | Command | Expected |
|---|---|---|
| R0.1 | `python --version` | `3.13.x` |
| R0.2 | `git --version` | any |
| R0.3 | `claude --version` | prints a version; record it |
| R0.4 | `node --version` | `v22.x` (clones pin `20.x`; note the mismatch, do not treat as a defect) |
| R0.5 | `git status --porcelain` | empty — review a clean tree, not a work in progress |
| R0.6 | `git log --oneline -1` | record the exact commit under review |

Record the commit sha at R0.6 in the sign-off block. Every finding is against that sha.

---

## R1 — Structural conformance

Checks the file manifest of spec §4. The spec is exhaustive in both directions: every listed file must exist,
and no unlisted file may exist under the source trees.

| ID | Command | Expected | Failure means |
|---|---|---|---|
| R1.1 | `python - <<'PY'` … *(manifest script below)* | `MISSING: 0` | A module was not built |
| R1.2 | same script | `UNLISTED: 0` | Scope crept, or a file was invented |
| R1.3 | `test -f pyproject.toml && test -f .env.example && test -f .gitignore && test -f README.md` | all present | Root files missing |
| R1.4 | `test -f docs/SAFETY.md && test -f docs/PACKAGE-FORMAT.md` | present | The two documents a maintainer reads first are missing |
| R1.5 | `ls prompts/*.md \| wc -l` | `7` | Prompt set incomplete |
| R1.6 | `test ! -d harness/stages/ship && test ! -f harness/ship.py` | both absent | **Major.** Delivery 1 must contain no ship path at all |

```python
# R1 manifest check — paste as a heredoc, or save as tools/check_manifest.py
import pathlib, sys

EXPECTED = {
 "harness/__init__.py","harness/__main__.py","harness/config.py","harness/errors.py",
 "harness/store.py","harness/governor.py","harness/collision.py","harness/redact.py",
 "harness/gh.py","harness/identity.py","harness/clone.py","harness/halt.py",
 "harness/context.py","harness/clock.py",
 "harness/gates.py","harness/commitmsg.py","harness/prettier.py","harness/packager.py",
 "harness/runner/__init__.py","harness/runner/base.py","harness/runner/cli.py","harness/runner/fake.py",
 "harness/stages/__init__.py","harness/stages/discover.py","harness/stages/propose.py",
 "harness/stages/implement.py","harness/stages/package.py",
 "prompts/system.md","prompts/discover_triage.md","prompts/propose.md","prompts/implement.md",
 "prompts/implement_fullsend.md","prompts/diagnose_gate_failure.md","prompts/README.md",
}
root = pathlib.Path(".")
actual = {
    str(p).replace("\\", "/")
    for base in ("harness", "prompts")
    for p in root.joinpath(base).rglob("*")
    if p.is_file() and "__pycache__" not in str(p)
}
missing  = sorted(EXPECTED - actual)
unlisted = sorted(actual - EXPECTED)
print("MISSING:", len(missing));  [print("   -", m) for m in missing]
print("UNLISTED:", len(unlisted)); [print("   +", u) for u in unlisted]
sys.exit(1 if (missing or unlisted) else 0)
```

---

## R2 — Invariants

**This section is the reason the scope was agreed to.** Any failure is `Returned`, full stop. These check
properties of the source, not of a run, so a passing R2 holds for every future run too.

| ID | Spec | Command | Expected | Severity |
|---|---|---|---|---|
| R2.1 | I-1 | `grep -rnE '"(POST\|PUT\|PATCH\|DELETE)"' harness/` | no output | major |
| R2.2 | I-1 | `grep -rn 'method=' harness/ \| grep -v '"GET"'` | no output | major |
| R2.3 | I-1 | `grep -rn 'Authorization' harness/` | no output | major |
| R2.4 | I-2 | `grep -rnE "\[[\"']gh[\"']\|\"gh \|'gh " harness/` | no output | major |
| R2.5 | I-3 | `grep -rn 'dangerously-skip-permissions' harness/` | no output | major |
| R2.6 | I-4 | `grep -rn 'os.environ\|getenv' harness/ \| grep -v '^harness/config.py'` | no output | major |
| R2.7 | I-5 | `grep -rniE 'select \|insert \|update \|delete \|create table' harness/ \| grep -v '^harness/store.py'` | no output | minor |
| R2.8 | I-6 | `grep -cE '^\.env$' .gitignore; grep -cE '^runs/$' .gitignore; grep -cE '^HALT$' .gitignore` | each `1` | major |
| R2.9 | I-7 | `grep -rn 'npm run format\|prettier --check \.' harness/` | no output | major |
| R2.10 | I-8 | `pytest tests/test_invariants.py -v` | all pass | major |
| R2.11 | §5.4.3 | `grep -n 'ANTHROPIC_API_KEY' harness/runner/cli.py` | one hit, in a line that **removes** it from the child environment | major |
| R2.12 | §3 | `pip freeze \| grep -viE 'pytest\|pluggy\|iniconfig\|packaging\|^-e '` | no output | minor — a runtime dependency was added |
| R2.13 | I-9 | `grep -rn 'HARNESS_GITHUB_TOKEN' harness/` | hits only in `config.py` and `identity.py` | major |
| R2.14 | I-9 | `grep -n 'identity' harness/gh.py` | no output — the read-only client must not be able to reach the token | major |
| R2.15 | I-10 | `pytest tests/test_identity.py -k human_doc -v` | passes; `render_human_doc` reads no environment value | major |

R2.7 is minor only because a stray SQL keyword in a comment is harmless; confirm by reading the hit before
recording it.

---

## R3 — Behavior coverage

Spec §6 defines `B1`–`B86`. Acceptance criterion A5 requires every one to be cited by a test.

| ID | Command | Expected |
|---|---|---|
| R3.1 | `pytest -q` | zero failures, **zero skips** — a skip is a failure for acceptance |
| R3.2 | `python - <<'PY'` … *(coverage script below)* | `UNCITED: 0` |
| R3.3 | `pytest -q --tb=no \| tail -1` | record the count |

```python
# R3 behavior-citation check
import pathlib, re, sys
cited = set()
for p in pathlib.Path("tests").rglob("*.py"):
    cited |= set(re.findall(r"\bB(\d{1,2})\b", p.read_text(encoding="utf-8")))
expected = {str(n) for n in range(1, 87)}
uncited = sorted(expected - cited, key=int)
print("UNCITED:", len(uncited), [f"B{n}" for n in uncited])
sys.exit(1 if uncited else 0)
```

Spot-check three behaviors by reading the test rather than trusting the citation. **B41 is the one to read
first** — it asserts the live collision set, and it is the single highest-consequence correctness requirement
in the delivery. A test that cites B41 but asserts an empty set passes the script and fails the system.

---

## R4 — Functional, zero cost

Run with `HARNESS_BACKEND=fake`. Spends no Claude capacity. Use a scratch directory so the real store is
untouched.

| ID | Command | Expected | Failure means |
|---|---|---|---|
| R4.1 | `harness init` in an empty dir | exit 0; `harness.db`, `runs/`, `.env` created | Init broken |
| R4.2 | `harness init` again | exit 0; existing `.env` **unmodified** (compare hash before/after) | B66 violated — a re-init would clobber configuration |
| R4.3 | `harness doctor` | exit 0; output names `claude` version and the `--max-turns` probe result | B70 violated |
| R4.4 | `harness doctor` with `git` removed from `PATH` | exit 3; names `git` | B67 violated |
| R4.5 | `harness status --json \| python -m json.tool` | valid JSON with queue counts and budget remaining | B68 violated |
| R4.6 | `harness discover --mode audit` | exit 2, `not implemented in delivery 1` | B59 violated |
| R4.7 | Full §1 sequence on fake backend | package dir matching §7.2 | A9 violated |
| R4.8 | Set `PERMISSION_TIER=1` in `.env`, run any command | `ConfigError` naming the key | B4 violated — **major**, the tier guard is load-bearing |
| R4.9 | Set `MAX_CONCURRENT_CLONES=2`, run any command | `ConfigError` | B22 violated |
| R4.10 | Add `NONSENSE_KEY=1` to `.env` | `ConfigError` naming `NONSENSE_KEY` | B3 violated — a typo'd budget key must not silently default |
| R4.11 | `touch HALT`, then `harness run` | exit 5; no clone left behind under `runs/` | B69 / A10 violated |
| R4.12 | Attempt an illegal transition via the store API | `IllegalTransition`, state unchanged | B11 violated |
| R4.13 | Set `HARNESS_GITHUB_TOKEN=github_pat_<40 chars of filler>`, keep `PERMISSION_TIER=0`, run the §1 sequence | Completes normally; `identity.load_token()` raises `TierViolation`; **no request carries the token** | **Major.** B80 / A19 violated — the credential guard is the whole Tier 0 argument |
| R4.14 | `harness setup --tier 1` | Exits 6; writes `HUMAN.md` with every §13.4 section | B82 violated |
| R4.15 | `harness setup --tier 1` twice, diff the outputs | Byte-identical | B86 violated — re-running would create spurious diffs |
| R4.16 | With the filler token still set, `grep -F 'github_pat_' HUMAN.md` | no output | **Major.** B83 violated — the generated doc leaked a secret |
| R4.17 | Read `HUMAN.md` | Each outstanding action names its actor (`you` or `nathaniel`), exact values, and a verification command where one exists | B84/B85 violated |

For R4.11, verify cleanup explicitly: `ls runs/*/clone 2>/dev/null` must be empty after the halt.

For R4.13, use a **syntactically valid but fake** token — never a real one. The point is to prove the refusal
fires on shape alone, and a real token in a review environment is a needless risk.

---

## R5 — Functional, live

**Budgeted. Run once.** Everything above must be green first.

| ID | Command | Expected | Notes |
|---|---|---|---|
| R5.1 | `harness discover --mode directed --target 816` | one item, state `discovered`, ≤ 2 API calls | Verify the count with `sqlite3 harness.db "select count(*) from api_call"` |
| R5.2 | `sqlite3 harness.db "select url, status, cached from api_call"` | every URL on `api.github.com`, none carrying a token | A15 |
| R5.3 | `harness propose 1` | work package with **every** §7.1 heading present and non-empty | Read it. An empty `## Decisions` or `## Open questions` section is a fail — the whole point of the stage is surfacing decisions before implementing |
| R5.4 | Read `## Diagnosis` | cites specific files and lines, not general assertions | A proposal that says "the script has a module system problem" without naming `scripts/check-bundle-size.js` is a fail |
| R5.5 | `harness approve 1 && harness run --item 1` | green gates, or `blocked` with a recorded reason | Both acceptable; see R5.6 |
| R5.6 | `git -C runs/*/clone diff <base>..HEAD` | **no** change to `.github/workflows/`, no `continue-on-error`, no `.skip(`, no raised timeout | A18. **Major** if violated — this is "went green dishonestly" |
| R5.7 | Read `EVIDENCE.md` | contains a **baseline** section from before the change, and verbatim output with exit codes | B62, B77. A summary instead of verbatim output is a fail |
| R5.8 | `harness package 1` | package dir per §7.2 | |

If R5.5 ends `blocked`, that is a legitimate outcome and the review continues — check that the block reason is
specific and that the clone was retained.

---

## R6 — Package conformance

Run against the package produced in R5.8.

| ID | Command | Expected |
|---|---|---|
| R6.1 | `ls runs/*/package` | exactly the §7.2 file set, nothing extra |
| R6.2 | `wc -c < runs/*/package/BASE` | `41` (40 sha chars plus newline) |
| R6.3 | `python -m json.tool < runs/*/package/manifest.json` | valid; `schema` is `1`; `fullsend_gate` names all five of F1–F5 |
| R6.4 | `git bundle verify runs/*/package/bundle.gitbundle` | `is okay` |
| R6.5 | **Reconstruction** — see script below | tree identical to the harness's |
| R6.6 | `harness archive 1` then `ls packages/*/` | `transcript.jsonl` **absent** |
| R6.7 | `harness archive 1 --with-transcript` | `transcript.jsonl` present |
| R6.8 | `harness archive` on an item not in `packaged` | refused | B72 |

```bash
# R6.5 reconstruction — the contract a maintainer actually exercises
PKG=$(ls -d runs/*/package)
tmp=$(mktemp -d)
git clone --quiet https://github.com/Bright-Bots-Initiative/brightboost.git "$tmp/r"
git -C "$tmp/r" checkout --quiet "$(cat "$PKG/BASE")"
git -C "$tmp/r" am "$PKG"/patches/*.patch
# expect: applies cleanly, and the resulting tree matches
diff -r --exclude=.git "$tmp/r" runs/*/clone && echo "RECONSTRUCTION OK"
```

R6.5 is the check that matters most to the person receiving this work. If it fails, nothing else in the
package is worth reviewing.

---

## R7 — Safety audit

| ID | Check | Command | Severity |
|---|---|---|---|
| R7.1 | No credential in any artifact | `grep -rnE 'sk-ant-\|gh[pousr]_\|github_pat_\|AKIA[0-9A-Z]{16}\|BEGIN [A-Z ]*PRIVATE KEY' runs/ packages/` → no output | major |
| R7.2 | No `.env` content leaked | `grep -rn 'SESSION_SECRET\|DATABASE_URL=' runs/*/package/` → no output | major |
| R7.3 | Redaction actually fires | Write a fake `sk-ant-` key into a transcript path and confirm it reads back redacted | major |
| R7.4 | Nothing written outside the sandbox | `python tools/audit_writes.py` (or the I-8 test) — every write under `runs/`, `packages/`, or the db path | major |
| R7.5 | No authenticated request occurred | `sqlite3 harness.db "select count(*) from api_call"` reconciles with `gh.py` having no auth header | major |
| R7.6 | Clone remote carries no credential | `git -C runs/*/clone remote -v` → plain `https://github.com/...`, no `x-access-token`, no `ssh` | major |
| R7.7 | Halt is honoured mid-stage, not only between stages | Create `HALT` while a stage is running; confirm exit 5 and clone release | minor |
| R7.8 | `HUMAN.md` is secret-free | `grep -rnE 'sk-ant-\|gh[pousr]_\|github_pat_\|AKIA[0-9A-Z]{16}' HUMAN.md` → no output | major |
| R7.9 | The bot token never reached the wire | `sqlite3 harness.db "select url from api_call"` shows only public endpoints, and R2.13/R2.14 hold | major |

---

## R8 — House rules

These are the product repository's own rules. Violating them means the work cannot land regardless of quality.

| ID | Check | Command | Expected |
|---|---|---|---|
| R8.1 | Commit headers ≤ 100 | `git -C runs/*/clone log --format='%s' <base>..HEAD \| awk '{ if (length($0) > 100) print "TOO LONG:", length($0), $0 }'` | no output |
| R8.2 | Body and footer lines ≤ 100 | `git -C runs/*/clone log --format='%b' <base>..HEAD \| awk 'length($0) > 100 { print "TOO LONG:", length($0) }'` | no output — **this is the half usually missed** |
| R8.3 | Conventional format | `cd runs/*/clone && npx commitlint --from <base>` | exit 0 |
| R8.4 | Subject case and no trailing period | covered by R8.3 | |
| R8.5 | Prettier clean on changed files only | `cd runs/*/clone && npx prettier --check --ignore-unknown -- $(git diff --name-only <base>..HEAD)` | exit 0 |
| R8.6 | No mass-format | `git -C runs/*/clone diff --stat <base>..HEAD` | file count matches `manifest.json.touched_paths`; a diff touching hundreds of files means the whole tree was formatted |
| R8.7 | Branch name | `git -C runs/*/clone branch --show-current` | starts with `harness/` |

R8.6 exists because `core.autocrlf=true` with no `.gitattributes` on this machine makes a whole-tree prettier
run rewrite almost everything. A large `--stat` is the symptom.

---

## R9 — Spec drift

Things the spec explicitly forbids. Each is a scope violation rather than a bug, and each is `major` because
scope creep in this particular system is how the safety argument gets lost.

| ID | Check | Expected |
|---|---|---|
| R9.1 | No Ship or Watch implementation | R1.6 covers the obvious form; also `grep -rn 'pr create\|pulls\|comment' harness/` → no write-shaped call |
| R9.2 | No audit-mode implementation | `harness discover --mode audit` exits 2 |
| R9.3 | No concurrency | No `threading`, `asyncio`, `multiprocessing`, or `concurrent.futures` import in `harness/` |
| R9.4 | No `api` runner | `harness/runner/api.py` does not exist |
| R9.5 | No credential handling beyond removal | The only `ANTHROPIC_API_KEY` reference is the removal in `cli.py` (R2.11) |
| R9.6 | No stacking logic | `parent_id` and `depends_on` exist in the schema and are read nowhere |
| R9.7 | The identity is specified but inert | `harness/identity.py` exists and exposes `assess` / `render_human_doc`; `load_token` raises at Tier 0; no module calls it for authentication |
| R9.8 | No mention-polling yet | `grep -rn 'mentions:' harness/` → no output; that is Tier 1 |

---

## Findings template

Record every finding in this shape. One block per finding.

```
### F-<n> · <severity: major | minor> · <check id>

**What:** one sentence stating the defect.
**Where:** file:line, or the command that produced it.
**Evidence:**
    <verbatim output, not a summary>
**Expected:** what the spec requires, cited by section or behavior number.
**Impact:** what breaks, or what guarantee is lost.
```

Severity is `major` when it touches §9 invariants, §10.5 safety, R2, R7, or R9; when it makes the package
unusable (R6.5); or when a behavior is claimed by a test that does not actually assert it. Everything else is
`minor`.

---

## Sign-off

```
Commit reviewed:      <sha from R0.6>
Reviewer:             <name or agent id>
Date:                 <iso date>

R1 structural         PASS / FAIL      ____ findings
R2 invariants         PASS / FAIL      ____ findings
R3 behaviors          PASS / FAIL      ____ uncited
R4 functional (free)  PASS / FAIL      ____ findings
R5 functional (live)  PASS / FAIL / SKIPPED
R6 package            PASS / FAIL      reconstruction: OK / FAILED
R7 safety             PASS / FAIL      ____ findings
R8 house rules        PASS / FAIL      ____ findings
R9 spec drift         PASS / FAIL      ____ findings

Claude capacity spent on this review: ____%

VERDICT:  Accepted / Accepted with findings / Returned / Blocked
```

---

## Notes for whoever reviews next

Three things are easy to wave through and expensive to get wrong.

**B41 and the collision set.** Eight of the nine open PRs on `brightboost` as of 2026-09-01 carry no
`closingIssuesReferences`, yet six are demonstrably working an open issue — visible only in the branch name.
If `collision.py` is wrong, the harness's first real act is duplicating someone else's in-flight work. Read
that test's assertions, do not trust its name.

**The baseline gate run.** A harness that reports green without a baseline cannot distinguish its own red from
the repository's pre-existing red. That distinction is the entire reason the evidence is trustworthy. Check
R5.7 by reading, not by grepping for the word "baseline".

**R2 in full.** Every one of those checks exists because the agreed scope was "it cannot do X," not "it is
configured not to do X." A tier flag can be edited. A codebase with no write path cannot be, without a commit
that shows up in review.

**The identity is meant to be inert, and R4.13 is how you prove it.** The spec deliberately ships a designed,
detected, documented bot identity that Delivery 1 cannot use. That is easy to implement wrongly in a way no
other check catches: a `load_token` that returns the value and leaves the tier check to a caller reads fine
and passes every test that does not specifically set a token at Tier 0. Run R4.13 with a fake token actually
present. An implementation that completes the run without raising has quietly moved the guarantee from the
code into the configuration, which is exactly the move this whole design exists to prevent.
