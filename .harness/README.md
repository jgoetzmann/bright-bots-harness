# `.harness/`: operator-owned configuration

Everything here is CODEOWNERS-protected and changed only through a reviewed pull request. The
harness reads these files and never writes them: `.harness/` is not one of its write roots
(B143).

| File | Purpose | Changed by |
| --- | --- | --- |
| `trust.txt` | Who may give `/harness` commands, and at which level: `<level> <handle> [vouch:<id>]` per line. Its header explains the format and the levels. `harness trust line <login> --level 2` prints a line to paste, `harness trust show` reads the file back as the gate sees it, and `harness doctor` names any line the gate refuses. | a PR |
| `config.json` | Operational knobs. The allowed keys are `config.CONFIG_JSON_KEYS`, twenty of them; any other key is a startup error naming it. A value here overrides the same key in `.env`. | a PR |
| `HALT` | The Actions-mode kill switch. While it exists on the default branch, every spending workflow logs `halted by .harness/HALT` and exits 0 as its first step. Delete it to resume. | a commit |
| `PIN` | sha256 over `harness/gates.py`, `harness/packager.py`, `harness/redact.py` and every file under `prompts/`. Checked by `harness doctor`, `selftest` and the container entrypoint. | `python -m harness.verify_pin --write`, in a PR |

## What may go in `config.json`

A knob that changes how much or how often the harness works: concurrency, revise cycles, the
self-audit cap, the decomposition bound, the usage stops and carry leeway, the run window, the
suggestion and ask limits and the headroom floors under suggested work and `/harness audit`,
whether it may comment upstream, the fork and upstream names, the inbox and tracking issues,
and the trust file path.
[docs/OPERATIONS.md](../docs/OPERATIONS.md) explains the usage stops, the run window and the
leeway, and how to change them.

Moving `RUN_WINDOW_START`/`RUN_WINDOW_END` does not move the schedule: the crons in
`.github/workflows/discover.yml` and `implement.yml` decide when a job wakes, and the window
decides what it may start once awake. Move both together.

The workflows also accept repository variables `FORK_REPO` and `TRACKING_ISSUE`, and use them
only where this file leaves the knob empty.

## What may not go here

A change to what the harness concludes is a code change, reviewed as one (B112):

- the gate sequence or any gate's timeout, command or threshold (`harness/gates.py`);
- the redaction patterns or the allowed write roots (`harness/redact.py`);
- the proposal front-matter schema or its validation (`harness/stages/propose.py`);
- prompts (`prompts/**`);
- the state machine, its labels or its legal transitions (`harness/store/`);
- anything that would let a keyword comment bypass the actor gate (`harness/trust.py`,
  `harness/keywords.py`).

`load_config` rejects any key outside `config.CONFIG_JSON_KEYS`, so such a key fails at
`harness doctor`.
