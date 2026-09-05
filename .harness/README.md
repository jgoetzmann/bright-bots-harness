# `.harness/` — operator-editable configuration

Everything in this directory is owned by the operator (see `.github/CODEOWNERS`) and changed only
through a reviewed pull request. The harness reads these files; it never writes them. `.harness/`
is **not** one of the harness's allowed write roots (B143): a stage that tried to touch it would be
refused by `redact.allowed_roots()` before any I/O.

## What lives here

| File | Purpose | Written by |
| --- | --- | --- |
| `trust.txt` | GitHub handles whose `/harness …` keyword commands are honoured. One per line, `#` comments, case-insensitive. A handle here is necessary but not sufficient: the commenter's `author_association` must also be `OWNER`, `MEMBER` or `COLLABORATOR` (B131). | a human, via PR |
| `config.json` | Operational knobs only (P12, B112). Exactly these keys, upper-snake: `WEEKLY_CAP_USD`, `PER_CALL_CAP_USD`, `RESERVE_PCT`, `MAX_CONCURRENT_ITEMS`, `MAX_REVISE_CYCLES`, `NOTIFY_POLL_HOURS`, `MAX_SUBISSUES`, `TRACKING_ISSUE`, `FORK_REPO`, `UPSTREAM_REPO`, `TRUST_FILE`, `WEEKLY_USAGE_STOP_PCT`, `SESSION_USAGE_STOP_PCT`, `OVERRUN_PCT`, `RUN_WINDOW_START`, `RUN_WINDOW_END`. A value here overrides the same key in `.env`; any other key is a startup error naming it. | a human, via PR |
| `HALT` | The kill switch for Actions mode (B149/B150). If this file exists on the default branch, every spending workflow logs `halted by .harness/HALT` and exits 0 as its **first** step — before checkout, before `harness doctor`, before the dispatcher. Creating it is a one-line commit that works from a phone. Delete it to resume. | a human, via commit |
| `PIN` | sha256 over the pinned result definition (`harness/gates.py`, `harness/packager.py`, `harness/redact.py`, every file under `prompts/`). Checked by `python -m harness.verify_pin --check` in the container entrypoint and in `selftest`. | the orchestrator, `python -m harness.verify_pin --write` |

## What may go in `config.json`

A knob whose change alters **how much** or **how often** the harness works: budget caps, reserve,
concurrency, revise cycles, poll cadence, decomposition bound, the fork and upstream names, the
tracking issue number, the trust file path, and the five usage-governance knobs below.

### The usage-governance knobs (Delivery 3)

These five read the subscription's own utilization, which the `claude` CLI reports on every call
as a `rate_limit_event`. Nothing DEPENDS on that signal: when it is absent the USD path
(`WEEKLY_CAP_USD`, `RESERVE_PCT`, `PER_CALL_CAP_USD`) governs exactly as in Delivery 2 (B114,
DECISIONS D31). Full procedure in [`docs/OPERATIONS.md`](../docs/OPERATIONS.md) §13.

| Knob | Ships as | Range | What changing it does |
| --- | --- | --- | --- |
| `WEEKLY_USAGE_STOP_PCT` | `90` | `0 < x <= 100` | Nothing new starts once the seven-day utilization reaches this. Lower it to leave more of the week for interactive use; raise it to spend nearer the wall. |
| `SESSION_USAGE_STOP_PCT` | `70` | `0 < x <= 100` | The same for the rolling five-hour window. The tighter of the two stops wins. |
| `OVERRUN_PCT` | `10` | `0 <= x < WEEKLY_USAGE_STOP_PCT` | Leeway granted to a **carried** item after a weekly reset, so a half-finished branch reaches a delivery PR instead of being abandoned. Applies to that one item; everything else still waits for `WEEKLY_USAGE_STOP_PCT`. |
| `RUN_WINDOW_START` | `mon 08:00` | `^(mon\|tue\|wed\|thu\|fri\|sat\|sun) HH:MM$`, UTC | Opens the weekly window in which new items may start. |
| `RUN_WINDOW_END` | `tue 20:00` | same shape; may wrap past Sunday | Closes it — here, at this account's weekly reset (Tue 20:00 UTC = 13:00 PT). Both empty = always open. |

Changing `RUN_WINDOW_START`/`RUN_WINDOW_END` does **not** move the schedule: the three crons in
`.github/workflows/implement.yml` are the times GitHub wakes the job up, and the window is what the
dispatcher enforces once it is awake. Move both together, or the job wakes to find nothing eligible.
`harness run --item N` bypasses the window on purpose; it never bypasses the usage stops.

`FORK_REPO` and `TRACKING_ISSUE` ship empty/`null` in `.env.example`. Fill them in here once the
machine account's fork exists and the pinned tracking issue is open (HUMAN.md items 3 and 12). The workflows also
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
outside the list above (`config.CONFIG_JSON_KEYS`, twenty-three keys since the Delivery 4 knobs),
so the run fails at `harness doctor` naming the key (A30).

## Notes

- `trust.txt` carries real handles now — `jgoetzmann` and `BrightBoost-Tech`; the
  `<NATHAN_HANDLE>` placeholder it shipped with is gone. `Identity.trust_file_ready()` is what
  reads that: it wants the file present, at least two handles, and no placeholder left. Adding
  or removing a handle is a reviewed PR, and `tests/test_trust.py` pins the shipped file.
- The harness never merges, approves or dismisses anything (I-12). Nothing in this directory can
  change that.
