# Bright Bots Harness — Delivery 2 Review Protocol

**Reviews:** an implementation claiming to satisfy `DELIVERY-2-HANDOFF.md`
**Run from:** the root of the `Bright-Bots-Harness` repository
**Reviewer:** a human, or an agent instructed to follow this file literally

---

## How to use this

Work the sections in order. **D2-R1 through D2-R7 cost nothing, require no Claude capacity, and
make no authenticated request.** Do all of them before spending anything on D2-R8, and before
letting the implementation near the product repository at all.

A failure in D2-R1 through D2-R4 means stop and return the work. There is no point proving that a
system with a broken trust gate also fails live — and in this delivery, unlike Delivery 1, running
it live has consequences that are visible to other people.

Every check has an **ID**, a **command**, an **expected result**, and a **failure meaning**. Record
each as `PASS`, `FAIL`, or `N/A` with evidence pasted verbatim. A check whose command errors for an
unrelated reason is `BLOCKED`, not `PASS`.

**Do not fix what you are reviewing.** A reviewer who repairs a defect loses the ability to report
it. Record and return.

**This protocol does not replace `HARNESS-REVIEW.md`.** Delivery 1's guarantees are still claimed.
D2-R12 re-runs that protocol in full, and a regression there is a `major` finding here.

### Verdict rubric

| Verdict | Condition |
|---|---|
| **Accepted** | Every check in D2-R1…D2-R7 and D2-R9…D2-R12 passes, and D2-R8 was run with the recorded outcome being a delivery PR with green gates or a properly `blocked` item |
| **Accepted with findings** | All of the above, plus one or more `minor` findings that touch neither §12 invariants nor §5 credentials nor §8 trust |
| **Returned** | Any `major` finding, any D2-R3 invariant failure, any D2-R4 trust failure, or any D2-R5 credential failure |
| **Blocked** | The environment prevented a check from running and the gap is load-bearing |

A single failure in D2-R3, D2-R4 or D2-R5 is `Returned` regardless of how much else passes. Those
three sections are the entire reason it was acceptable to give this thing a credential.

---

## D2-R0 — Preconditions

| ID | Command | Expected |
|---|---|---|
| R0.1 | `python --version` | `3.13.x` |
| R0.2 | `git --version` | any |
| R0.3 | `claude --version` | prints a version; record it |
| R0.4 | `node --version` | record it |
| R0.5 | `git status --porcelain` | empty |
| R0.6 | `git log --oneline -1` | record the exact commit under review |
| R0.7 | `gh auth status` | **record, do not require.** If the reviewer's own `gh` is authenticated, note it — some checks below must be run in a shell where it is not, to prove the harness is not borrowing it |
| R0.8 | `test -f .harness/PIN && cat .harness/PIN` | a sha256; record it |

Record the sha from R0.6 in the sign-off. Every finding is against that sha.

---

## D2-R1 — Structural conformance

The file map in handoff §3 is normative. A file in the wrong place is a `minor` finding; a file
that should not exist at all is `major`.

| ID | Command | Expected | A failure means |
|---|---|---|---|
| R1.1 | `test -d harness/store && test -f harness/store/__init__.py && test -f harness/store/sqlite.py && test -f harness/store/github.py` | all present | the store seam (§2) was not built; I-16 is unenforceable |
| R1.2 | `test ! -f harness/store.py` | absent | the move was a copy; two stores will drift |
| R1.3 | `ls harness/ \| grep -E '^(dispatcher\|ledger\|keywords\|trust)\.py$' \| wc -l` | `4` | a §3 module is missing |
| R1.4 | `ls harness/stages/ \| grep -E '^(revise\|deliver\|decompose)\.py$' \| wc -l` | `3` | a new stage is missing |
| R1.5 | `ls .github/workflows/*.yml \| wc -l` | `6` | §7's workflow set is incomplete |
| R1.6 | `test -f .github/CODEOWNERS && test -f .harness/trust.txt && test -f .harness/config.json` | all present | §5.5 governance is absent |
| R1.7 | `test ! -f .gitmodules && test ! -d submodules` | absent | §3.1 — a submodule cannot represent N branches and breaks fast-forward sync |
| R1.8 | `ls prompts/ \| grep -E '^(decompose\|revise)\.md$' \| wc -l` | `2` | new stages have no prompt file; prompts-as-data was violated |
| R1.9 | `ls bb-*.ps1 bb-config.json bb-configure.py 2>/dev/null \| wc -l` | `5` | local mode's control plane is incomplete |
| R1.10 | `test -d local && ls local/ \| wc -l` | ≥ 7 | container plumbing missing |
| R1.11 | `git check-ignore -q bb-work runs && echo ignored` | `ignored` | working state would be committed |
| R1.12 | `python -c "import tomllib,pathlib;d=tomllib.loads(pathlib.Path('pyproject.toml').read_text());print(d['project'].get('dependencies'))"` | `[]` or absent | I-17 broken: a runtime dependency was added |

**R1.13 — manifest check.** Every file in handoff §3 marked `NEW` exists, and every file marked
"unchanged — do not touch" is byte-identical to its state at the Delivery 1 tag.

```bash
# expect: no output
for f in harness/gates.py harness/packager.py harness/redact.py; do
  git diff --quiet <delivery-1-sha> -- "$f" || echo "MODIFIED (must not be): $f"
done
```

A modification to any of those three is `major` — they are the pinned result definition (§10.4),
and changing them silently changes what every recorded result means.

---

## D2-R2 — Behavior coverage

Handoff §4 through §11 define behaviors B100…B150. Every one must be cited by at least one test.

```bash
# D2-R2 behavior-citation check
python - <<'PY'
import pathlib, re
cited = set()
for p in pathlib.Path("tests").rglob("*.py"):
    cited |= set(re.findall(r"\bB1[0-5][0-9]\b", p.read_text(encoding="utf-8", errors="replace")))
spec = set(re.findall(r"\*\*B(1[0-5][0-9])\*\*", pathlib.Path("DELIVERY-2-HANDOFF.md").read_text(encoding="utf-8")))
spec = {f"B{n}" for n in spec}
missing = sorted(spec - cited, key=lambda s: int(s[1:]))
print(f"defined={len(spec)} cited={len(spec & cited)} uncited={len(missing)}")
print("UNCITED:", ", ".join(missing) if missing else "none")
PY
```

| ID | Expected | A failure means |
|---|---|---|
| R2.1 | `uncited: none` | a specified behavior has no test; it is an intention, not a property |
| R2.2 | For each cited B-number, the test **asserts** it | a docstring naming a behavior while the test asserts something else is `major`, per Delivery 1's rule |

Spot-check at minimum B103, B105, B120, B132, B136, B139, B149 by reading the test bodies. Those
seven are the ones where a plausible-looking test can fail to assert the actual property.

---

## D2-R3 — Invariants

The reason a credential was acceptable. **A single failure here is `Returned`.**

| ID | Invariant | Command | Expected |
|---|---|---|---|
| R3.1 | I-2′ — no `gh` CLI | `grep -rnE "['\"]gh['\"]\|\bgh \b" harness/ --include=*.py` | no command invocation |
| R3.2 | I-11 — one authenticated client | `grep -rn "Authorization" harness/ --include=*.py` | matches in `harness/gh.py` only |
| R3.3 | I-11 — one token door | `grep -rn "github_token\|HARNESS_GITHUB_TOKEN" harness/ --include=*.py` | the getter is defined in `config.py` and imported only by `gh.py` |
| R3.4 | I-12 — never merges | `grep -rnE "/merge\b\|event.*APPROVE\|dismiss" harness/ --include=*.py` | nothing |
| R3.5 | I-12 — by test | `pytest tests/test_invariants.py -k merge -q` | passes; the AST walk finds no merge endpoint |
| R3.6 | I-13 — everything redacted | `pytest tests/test_invariants.py -k redact -q` | passes; every `gh.py` write method routes its payload through `redact` |
| R3.7 | I-14 — no upstream issues | `pytest tests/test_invariants.py -k issue_repo -q` | passes |
| R3.8 | I-15 — no `.github` writes | inspect the PAT scopes; then `pytest tests/test_stages.py -k B64 -q` (expect 4+ selected, 0 deselected-to-zero) | the PAT has no `workflow` scope **and** `_reject_forbidden_diff` (B64, `implement.py:379`) still blocks a CI-workflow path, a disabled check, and a raised timeout |
| R3.9 | I-16 — no mode branching | `grep -rnE "GITHUB_ACTIONS\|RUNNER_OS\|ACTIONS_MODE\|is_actions\|execution_mode" harness/stages/ harness/gates.py harness/packager.py harness/governor.py --include=*.py` | nothing |
| R3.10 | I-17 — stdlib only | `pytest tests/test_invariants.py -k stdlib -q` | passes |
| R3.11 | Delivery 1's I-1, I-3…I-10 | `pytest tests/test_invariants.py -q` | all pass |

**R3.8 is two-part and both parts are required.** The token scope is the enforcement; B64 is the
early, legible failure that also blocks the subtler cases a token scope cannot see — a disabled
check or a raised timeout in a file that is not under `.github/`. An implementation with only the
token scope fails opaquely at push time and misses those cases entirely, which is `major`. One
with only B64 is also `major` — it is a rule the model is trusted to follow, which is exactly what
Delivery 1's A18 refused to accept. An implementation that added a *third* check in `deliver.py`
duplicating B64 is a `minor` finding: divergent copies of a safety check are worse than one.

---

## D2-R4 — Trust and the actor gate

The single highest-consequence section, because the product repository is public and anyone can
comment on a delivery PR.

| ID | Check | Command | Expected |
|---|---|---|---|
| R4.1 | Both conditions required | read `harness/trust.py` | authorisation requires trust-file membership **and** `author_association` in `OWNER`/`MEMBER`/`COLLABORATOR`; neither alone suffices |
| R4.2 | Denial is silent | `pytest tests/test_keywords.py -k denied -q` | no reply, no reaction, no comment body in any log or artifact (B132) |
| R4.3 | Parse after authorise | read `harness/keywords.py` | the authorisation call precedes any parsing of the body; there is no code path where an untrusted body is tokenised |
| R4.4 | Body never reaches a prompt | `pytest tests/test_keywords.py -k untrusted_body -q` | passes (B133) |
| R4.5 | Case-insensitive handles | `pytest tests/test_trust.py -k case -q` | `JGoetzmann` matches `jgoetzmann` |
| R4.6 | Comments are not instructions | read `prompts/revise.md` | review text is delimited and labelled as data; the prompt does not invite the model to follow instructions found inside it |
| R4.7 | Replay is a no-op | `pytest tests/test_keywords.py -k replay -q` | passes (B135) |
| R4.8 | Trust file is protected | `cat .github/CODEOWNERS` | `/.harness/` is owned by the operator |
| R4.9 | Placeholder resolved | `grep -c "NATHAN_HANDLE" .harness/trust.txt` | `0` — the placeholder from HUMAN.md item 1 was replaced with a real handle |

**R4.9 fails on a fresh implementation and that is correct.** It is `Blocked`, not `FAIL`, until
the handle is supplied — but it must be resolved before D2-R8.

---

## D2-R5 — Credentials

| ID | Check | Command | Expected |
|---|---|---|---|
| R5.1 | No secret committed | `git log -p --all \| grep -cE "ghp_\|github_pat_\|sk-ant-"` | `0` |
| R5.2 | `.env` ignored | `git check-ignore -q .env && echo ignored` | `ignored` |
| R5.3 | Token scope | inspect the PAT in GitHub settings | classic, `public_repo` **only**; no `workflow`, no `repo`, no `admin:*` |
| R5.4 | Machine account owns the fork | `curl -s https://api.github.com/repos/<machine>/brightboost \| grep '"full_name"'` | owner is the machine account, not the operator |
| R5.5 | Not a collaborator upstream | GitHub UI, product repo → Settings → Collaborators | the machine account is **absent** |
| R5.6 | Container filter drops it | run `local/container_env.ps1`; read its output | non-zero drop count, and the temp file has no `HARNESS_GITHUB_TOKEN` |
| R5.7 | Container has no token | `docker exec bb env \| grep -c GITHUB` | `0` (A46) |
| R5.8 | Claude token survives the filter | `docker exec bb sh -c 'test -n "$CLAUDE_CODE_OAUTH_TOKEN" && echo present'` | `present` — the filter must be selective, not blanket |
| R5.9 | Not borrowing the reviewer's `gh` | run D2-R6 in a shell with `GH_TOKEN` unset and `gh auth logout` done | every check still passes |

**R5.9 is the check that catches the most dangerous class of defect**, and it is the reason R0.7
asked you to record your own auth state. A harness that quietly works because *your* credentials
are on the machine has not been reviewed.

---

## D2-R6 — Functional, zero cost, no network

Everything here runs under `BACKEND=fake` with fixtures. Nothing authenticates.

| ID | Command | Expected |
|---|---|---|
| R6.1 | `pytest -q` | all green |
| R6.2 | `BACKEND=fake harness doctor` | exits 0, names every new §6.5 key |
| R6.3 | `harness doctor` with `WEEKLY_CAP_USD` removed from `.env` | exits 3, names the missing key (A30) |
| R6.4 | `BACKEND=fake harness dispatch` | emits a JSON plan; **starts nothing** (A33) |
| R6.5 | `harness dispatch` twice with an unchanged ledger | byte-identical plans — the dispatcher is pure |
| R6.6 | `harness dispatch` with `rate_limited_until` in the future | empty plan, reason names the rate limit (B121) |
| R6.7 | `harness dispatch` with spend at the reserve boundary | empty plan, reason `reserve` |
| R6.8 | `harness dispatch` with an item whose `depends_on` is unmerged | that item skipped, reason names the dependency (B107) |
| R6.9 | `harness ledger --rebuild` against fixture comments | matches the fixture ledger within rounding (A49) |
| R6.10 | propose with invalid front matter | no PR call is made; item goes `harness:blocked` (A34, B103) |
| R6.11 | propose with a `touched_paths` entry absent upstream | validation fails (A35, B104) |
| R6.12 | `harness sync-fork` against a diverged fixture | exits non-zero, changes nothing, message names the divergence (A36, B105) |
| R6.13 | a fake `RateLimited` outcome | item returns to its prior label, job exits 0, ledger records the reset (A39, B120) |
| R6.14 | `revise` on a tree whose gates go red | nothing pushed, item `harness:blocked` (A40, B136) |
| R6.15 | `revise` at `MAX_REVISE_CYCLES` | `harness:needs-human`, no further action (A41, B137) |
| R6.16 | `.harness/HALT` present, then any spending entry point | exits 0 before the dispatcher and before `doctor`; nothing spent (A43, B149, B150) |
| R6.17 | two state labels on one item | raises rather than guessing (B100) |
| R6.18 | `decompose` on a sub-issue | refused; depth is one (B111) |

**R6.16 must be checked at every spending entry point**, not just one. List them and check each.

---

## D2-R7 — Workflows, read only

Read the YAML. Do not run it.

| ID | Check | Expected |
|---|---|---|
| R7.1 | `grep -c "cron:" .github/workflows/*.yml` | every cron minute is non-zero and non-round (B124) |
| R7.2 | `grep "timeout-minutes" .github/workflows/*.yml` | present on every job, ≤ 120 (B125) |
| R7.3 | `grep -B2 -A2 "upload-artifact" .github/workflows/*.yml` | guarded by `if: always()` (B126) |
| R7.4 | `grep -A3 "concurrency" .github/workflows/*.yml` | `cancel-in-progress: false` on everything that writes the ledger (B118) |
| R7.5 | step order in `implement.yml` | HALT check → `doctor` → `sync-fork` → `dispatch` → work (B127, B150) |
| R7.6 | `grep "permissions" -A5 .github/workflows/*.yml` | least privilege; no `write-all` |
| R7.7 | `selftest.yml` | runs on `pull_request`, uses **no** secrets, matrixes Linux and Windows (B129, B130) |
| R7.8 | `grep -rn "pull_request_target" .github/workflows/` | **nothing** — on a public repo this is the standard privilege-escalation vector |
| R7.9 | `ops.yml` | retries at most once, never on a gate failure or a model call (B146) |
| R7.10 | `heartbeat.yml` | spends nothing, comments on the pinned issue (B144) |
| R7.11 | reconciliation | some workflow resets `harness:running` items older than 3 h (A48, B147) |
| R7.12 | no workflow targets the fork | `grep -rn "<machine>/brightboost" .github/workflows/` shows clone/push targets only, never a workflow file written there (B105) |

---

## D2-R8 — Functional, live, supervised

**Do not begin until D2-R1…D2-R7 pass and R4.9 is resolved.** This section is visible to other
people. Run it with `MAX_CONCURRENT_ITEMS=1` and watch it.

| ID | Step | Expected |
|---|---|---|
| R8.1 | Open one issue here, label `harness:queued` | — |
| R8.2 | `workflow_dispatch` `discover.yml` | a proposal PR appears against this repository |
| R8.3 | Read the proposal | front matter validates; `touched_paths` are real; the diagnosis cites files and lines |
| R8.4 | Comment `/harness revise …` **from an untrusted account** | **nothing happens.** No reply, no reaction, no run. If anything at all happens, stop the review — this is `Returned` |
| R8.5 | Comment `/harness revise …` from a trusted account | a new commit on the proposal branch reflecting the note |
| R8.6 | Merge the proposal PR | `implement.yml` triggers; the issue becomes `harness:approved` then `harness:running` |
| R8.7 | Watch the run | gates run on the untouched tree first; the baseline is recorded before any edit |
| R8.8 | On green | a branch on the fork, and a delivery PR upstream with the evidence in its body |
| R8.9 | Inspect the delivery PR | base commit exists **upstream**; the patch reconstructs per `docs/PACKAGE-FORMAT.md` |
| R8.10 | Check the fork | its default branch is unchanged from upstream — `git log origin/main..upstream/main` and the reverse are both empty (B105) |
| R8.11 | Leave a review comment on the delivery PR | within `NOTIFY_POLL_HOURS`, a revise cycle addresses it |
| R8.12 | `.harness/HALT` mid-flight | the next spending workflow exits 0 without spending |
| R8.13 | Ledger | `state/ledger.json` committed, cost within 2× the static estimate |
| R8.14 | Close the loop | `/harness stop` closes the PR and frees the slot |

**R8.4 is the check this whole section exists for.** Everything else can be re-run; a trust gate
that leaks cannot be un-leaked once the repository is public.

---

## D2-R9 — Local mode

| ID | Command | Expected |
|---|---|---|
| R9.1 | `.\bb-start.ps1 -Build` | container `running`; `docker logs bb` shows every gate step passing |
| R9.2 | `docker inspect bb --format '{{.HostConfig.Binds}}'` | `/harness` is `:ro`; `/work` and `/data` are the only writable paths |
| R9.3 | break the read-only mount deliberately, restart | `exit 1` with the fatal message (A45, step 2) |
| R9.4 | corrupt `.harness/PIN`, restart | `exit 1` (A45, step 3) |
| R9.5 | break a golden test, restart | `exit 1` (A45, step 5) |
| R9.6 | `.\bb-watcher.ps1 -Once` | snapshot; heartbeat age < 30 s |
| R9.7 | close every terminal | container unaffected |
| R9.8 | `.\bb-stop.ps1` | returns within its wait window; `exited (0)`; `STOP` cleaned up |
| R9.9 | `.\bb-start.ps1` again | resumes; no work repeated, none lost |
| R9.10 | reboot, then `.\bb-start.ps1` | same |
| R9.11 | `Get-Process \| ... watchdog` | starting/stopping `bb` leaves `rk`'s container and watchdog **identical** (A44) |
| R9.12 | `grep -c "watchdog.ps1" local/watchdog-bb.ps1` filename check | the filename does not contain `watchdog.ps1` (A44) |
| R9.13 | `docker exec bb python -c "import harness.gh"` then attempt a write | fails — no credential (A46, R5.7) |

R9.1 through R9.11 are the platform document's §9 acceptance list. All eight of its items must
hold, including the last one about other harnesses being unaffected.

---

## D2-R10 — Documentation

| ID | Check | Expected |
|---|---|---|
| R10.1 | `docs/SAFETY.md` | the tier table says Tier 2 and describes what is now possible, honestly; every invariant I-11…I-17 has a "verify it yourself" command |
| R10.2 | `docs/OPERATIONS.md` | covers a failed run, a stuck item, a rate-limited window, a diverged fork, a disabled schedule, a leaked secret, and the stop procedure |
| R10.3 | `docs/OPERATIONS.md` | states the event-driven vs polled asymmetry (B134) so it is not read as a bug |
| R10.4 | `docs/OPERATIONS.md` | states what happens when upstream's gate sequence changes (handoff §17.3) |
| R10.5 | `local/README.md` | the rebuild-vs-restart trap is at the top (§10.2) |
| R10.6 | `HUMAN.md` | all 16 items from §16, each with who and why |
| R10.7 | `DECISIONS.md` | records the `--max-budget-usd` experiment result (§16 item 14) and the plain-bridge egress ruling (§10.6) |
| R10.8 | `README.md` | the five-command walkthrough still works, and the two modes are distinguished in the first screen |

---

## D2-R11 — Safety audit

Read, do not run. This is the section a maintainer with no context would want done.

| ID | Question | Where to look |
|---|---|---|
| R11.1 | Can the harness merge anything? | I-12; `grep` for merge endpoints; read `deliver.py` end to end |
| R11.2 | Can it edit CI anywhere? | I-15; token scope **and** the path check |
| R11.3 | Can an untrusted person spend the budget? | D2-R4 in full |
| R11.4 | Can an issue body steer the model into something the gates would not catch? | read `prompts/propose.md` and `prompts/implement.md`; issue text must be delimited data. The gates and the two human approvals are the backstops — confirm they are actually load-bearing and not bypassed on any path |
| R11.5 | Can it widen a gate? | `gates.py` is byte-identical (R1.13) and pinned (R9.4) |
| R11.6 | Can it push to the product repository? | not a collaborator (R5.5); every push target is the fork |
| R11.7 | Can it write outside its roots? | `redact.allowed_roots`; `state/` and `proposals/` were added — confirm they were added deliberately and nothing else was |
| R11.8 | Can it change its own pin? | `.harness/PIN` is outside `allowed_roots()` and CODEOWNERS-protected (B143) |
| R11.9 | What happens if the OAuth token leaks? | documented in `docs/OPERATIONS.md`; revocation is one action |
| R11.10 | What is the worst thing a single bad run can do? | write it down in one paragraph. If the answer is worse than "opens a bad PR that two humans must approve," something in this protocol failed |

---

## D2-R12 — Delivery 1 regression

| ID | Command | Expected |
|---|---|---|
| R12.1 | Run `HARNESS-REVIEW.md` in full | every section that was `PASS` at Delivery 1 is still `PASS` (A50) |
| R12.2 | `pytest tests/ -q` | the Delivery 1 tests are unmodified and green |
| R12.3 | `git diff <d1-sha> -- tests/test_invariants.py` | additions only; no Delivery 1 invariant test was weakened or deleted |

**R12.3 is where a well-intentioned implementation usually fails.** Relaxing an invariant by
deleting its test, rather than by amending the invariant with a stated reason, is a `major`
finding even when the relaxation itself was correct. I-2 → I-2′ is the one amendment this delivery
authorises, and it must appear as an amendment.

---

## Findings template

```
### F-<n> · <severity: major | minor> · <check id>

**What:** one sentence stating the defect.
**Where:** file:line, or the command that produced it.
**Evidence:**
    <verbatim output, not a summary>
**Expected:** what the handoff requires, cited by section, behavior, or invariant number.
**Impact:** what breaks, or what guarantee is lost.
```

Severity is `major` when it touches §12 invariants, §5 credentials, §8 trust, D2-R3, D2-R4, D2-R5,
D2-R11 or D2-R12; when it makes a delivery package unusable; or when a behavior is claimed by a
test that does not actually assert it. Everything else is `minor`.

---

## Sign-off

```
Commit reviewed:        <sha from R0.6>
Pin at review:          <sha from R0.8>
Reviewer:               <name or agent id>
Date:                   <iso date>

D2-R1  structural       PASS / FAIL      ____ findings
D2-R2  behaviors        PASS / FAIL      ____ uncited
D2-R3  invariants       PASS / FAIL      ____ findings
D2-R4  trust gate       PASS / FAIL      ____ findings
D2-R5  credentials      PASS / FAIL      ____ findings
D2-R6  functional free  PASS / FAIL      ____ findings
D2-R7  workflows        PASS / FAIL      ____ findings
D2-R8  functional live  PASS / FAIL / SKIPPED
D2-R9  local mode       PASS / FAIL      ____ findings
D2-R10 documentation    PASS / FAIL      ____ findings
D2-R11 safety audit     PASS / FAIL      ____ findings
D2-R12 D1 regression    PASS / FAIL      ____ findings

Claude capacity spent on this review: ____%
Live PRs opened during D2-R8:         ____   (all closed? Y / N)

VERDICT:  Accepted / Accepted with findings / Returned / Blocked
```

---

## Notes for whoever reviews next

Three things about this delivery that are easy to review badly.

**The trust gate is the whole ballgame.** Delivery 1 was safe because it held no credential.
Delivery 2 is safe because two humans approve everything and untrusted actors are ignored. If you
have time for one section, do D2-R4 and R8.4.

**Check that local mode and Actions mode are one harness.** I-16 exists because the cheapest way
to make both modes work is to branch on the environment, and the result is two half-tested
systems that diverge quietly. A single `if` in a stage is a `major` finding even if it works.

**Do not accept a green D2-R8 as proof of anything except that the happy path works.** The
interesting states are rate-limited, conflicted, gate-red, and untrusted-commenter. Those are
covered offline in D2-R6 precisely because they are hard to provoke live — so if D2-R6 was rushed,
D2-R8 passing tells you very little.
