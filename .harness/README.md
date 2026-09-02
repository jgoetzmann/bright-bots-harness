# `.harness/` — operator-editable configuration

Everything in this directory is owned by the operator (see `.github/CODEOWNERS`) and changed only
through a reviewed pull request. The harness reads these files; it never writes them. `.harness/`
is **not** one of the harness's allowed write roots (B143): a stage that tried to touch it would be
refused by `redact.allowed_roots()` before any I/O.

## What lives here

| File | Purpose | Written by |
| --- | --- | --- |
| `trust.txt` | GitHub handles whose `/harness …` keyword commands are honoured. One per line, `#` comments, case-insensitive. A handle here is necessary but not sufficient: the commenter's `author_association` must also be `OWNER`, `MEMBER` or `COLLABORATOR` (B131). | a human, via PR |
| `config.json` | Operational knobs only (P12, B112). Exactly these keys, upper-snake: `WEEKLY_CAP_USD`, `PER_CALL_CAP_USD`, `RESERVE_PCT`, `MAX_CONCURRENT_ITEMS`, `MAX_REVISE_CYCLES`, `NOTIFY_POLL_HOURS`, `MAX_SUBISSUES`, `TRACKING_ISSUE`, `FORK_REPO`, `UPSTREAM_REPO`, `TRUST_FILE`. A value here overrides the same key in `.env`; any other key is a startup error naming it. | a human, via PR |
| `HALT` | The kill switch for Actions mode (B149/B150). If this file exists on the default branch, every spending workflow logs `halted by .harness/HALT` and exits 0 as its **first** step — before checkout, before `harness doctor`, before the dispatcher. Creating it is a one-line commit that works from a phone. Delete it to resume. | a human, via commit |
| `PIN` | sha256 over the pinned result definition (`harness/gates.py`, `harness/packager.py`, `harness/redact.py`, every file under `prompts/`). Checked by `python -m harness.verify_pin --check` in the container entrypoint and in `selftest`. | the orchestrator, `python -m harness.verify_pin --write` |

## What may go in `config.json`

A knob whose change alters **how much** or **how often** the harness works: budget caps, reserve,
concurrency, revise cycles, poll cadence, decomposition bound, the fork and upstream names, the
tracking issue number, the trust file path.

`FORK_REPO` and `TRACKING_ISSUE` ship empty/`null`. Fill them in here once the machine account's
fork exists and the pinned tracking issue is open (HUMAN.md items 3 and 12). The workflows also
accept repository variables `FORK_REPO` and `TRACKING_ISSUE` and use them only where this file
leaves the knob empty.

## What may not go here

A knob whose change alters **what the harness concludes** is not configuration; it is a code
change, reviewed as one (B112):

- the gate sequence or any gate's timeout, command or threshold (`harness/gates.py`);
- the redaction patterns or the allowed write roots (`harness/redact.py`);
- the proposal front-matter schema or its validation (`harness/stages/propose.py`);
- prompts (`prompts/**`);
- the state machine, its labels or its legal transitions (`harness/store/`);
- anything that would let a keyword comment bypass the actor gate (`harness/trust.py`,
  `harness/keywords.py`).

Adding such a key to `config.json` does not silently take effect: `load_config` rejects any key
outside the eleven above, so the run fails at `harness doctor` naming the key (A30).

## Notes

- `trust.txt` currently contains the placeholder `<NATHAN_HANDLE>`. Until it is replaced with a
  real handle only `jgoetzmann` can steer; the acceptance check R4.9 stays `Blocked` by design.
- The harness never merges, approves or dismisses anything (I-12). Nothing in this directory can
  change that.
