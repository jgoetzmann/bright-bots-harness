# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A standard-library-only Python 3.13 harness that takes work on the product repository
`Bright-Bots-Initiative/brightboost` from discovery to a reviewable pull request, then stops. It
runs on GitHub Actions as the machine account `jgoetzmann-bot`, pushes only to that account's
fork (`jgoetzmann-bot/brightboost`), and uses issues in this repository
(`jgoetzmann/bright-bots-harness`) as its work queue. Two human gates: merging a
`proposals/<id>-<slug>.md` PR here is approval (gate 1); a person merging the upstream PR is
gate 2. The harness never merges anything.

Flow: `discover` → `propose` (PR adding a proposal file) → gate 1 → `implement` (branch on the fork,
product gates run before and after) → `package` → `deliver` (upstream PR) → gate 2. `revise`
handles review/CI/conflict feedback and resuming a carried item. `decompose` splits an issue into
sub-items. `STAGES` also holds entries that aren't stages an item passes through: `request` and
`promote` create items, and `ask` and `audit` create none (B247). Humans steer via `/harness <verb>`
comments (`harness/keywords.py`).

Operator docs live in `README.md` and `docs/`; why anything is the way it is lives in
`DECISIONS.md`. `HUMAN.md` is written by `harness setup` and is gitignored.

## Commands

`.venv` is a Windows venv (`.venv/Scripts/python.exe`). From WSL, call it directly (it runs
through interop). The system `python3` is 3.12, which is too old.

```bash
.venv/Scripts/python.exe -m pytest -q                              # full suite (a few minutes)
.venv/Scripts/python.exe -m pytest -q tests/test_stages.py -k B64  # one file / one behavior
.venv/Scripts/python.exe -m pytest -q tests/test_invariants.py     # the structural invariants
.venv/Scripts/python.exe -m harness.verify_pin --check             # the pin; --write rewrites it
.venv/Scripts/python.exe -m harness --help                         # the CLI; `doctor` probes it
```

- The suite runs entirely under `BACKEND=fake`: no network, no model, and time frozen at
  2026-09-01T12:00Z, a Tuesday (`tests/conftest.py`). An autouse fixture there clears every
  `.env` key name from `os.environ` for each test, and `pyproject.toml` puts the rootdir first
  on `sys.path`, so a run tests the tree it lives in rather than the host (D75).
- `--config PATH`, `--json`, `--dry-run` (record GitHub writes instead of sending them) and
  `--verbose` are global flags. They go before the subcommand.
- CI (`selftest.yml`, the only PR workflow) runs `pip install -e ".[dev]"`, `verify_pin --check` and
  `pytest -q` on both `ubuntu-latest` and `windows-latest`. Code must work on both. `gates.py`
  and `runner/cli.py` already resolve Windows `.cmd` shims and Git Bash.
- There's no linter or formatter. House style is a 100-column limit; don't add longer lines. The
  PostToolUse hook in `.claude/hooks/post_edit_check.py` reports over-long lines, compile errors
  and pin mismatches after each edit. The hook runs `.venv/Scripts/python`, which only resolves
  on Windows. From WSL it fails silently, so run `verify_pin --check` yourself after editing a
  pinned file.
- `gh` isn't on the WSL PATH; call `"/mnt/c/Program Files/GitHub CLI/gh.exe"`. The fleet's live
  state is the ledger on the `harness-state` branch, one commit per workflow run
  (`git show origin/harness-state:state/ledger.json`). Each implement run's `harness dispatch`
  step logs its plan and reason.

## Architecture

- `harness/__main__.py`: the CLI. Subcommands are registered in `build_parser()` and the
  `COMMANDS` dict. Exit codes carry meaning: a halt via `.harness/HALT` or a rate limit exits 0
  because it's a normal outcome. `doctor` exits 3 when degraded and gates the spending
  workflows, so a `doctor` problem stops the fleet while a warning doesn't. Tests monkeypatch
  the module-level injectables `WHICH`/`RUN`/`SLEEP` rather than the stdlib.
- `context.py` `build_context()` wires the `Context` every stage receives: `Config`, `Store`,
  `Governor`, `Runner`, the `gh` client, `CloneManager`, `Clock`, `Ledger` and `Trust`. It also
  arms the write-root guard before anything can write.
- `stages/`: `STAGES` registry in `stages/__init__.py`. Every model call goes through
  `stages.run_model()`, which does the halt check (file plus commanded halt), `priority.admit`,
  `governor.authorize`, the runner call, the redacted transcript and usage recording. On a rate
  limit it returns the item to its entry state and raises `RateLimited`. Prompts come from
  `prompts/<name>.md` as `string.Template`, so a literal `$` is written `$$`. Repository content
  pasted into a prompt is wrapped with `data_block()` and labelled "Data — not instructions".
- The self-audit ends `implement` (D70). Once the gates have no new failures, a `selfaudit` call
  (read tools plus `Bash`, no `Edit`) audits the committed diff against the approved work
  package. It is the one model call that reviews another model's output, so its findings are
  labelled opinion and never block delivery. Blocking findings get a `selfaudit_fix` pass with
  implement's tools; the gates re-run, and a fix that breaks one is reverted. The cap is
  `MAX_SELF_AUDIT_CYCLES`, and a repeated finding signature stops early. Both stages class as
  `unblock`. A halt inside the loop hands the item off through `deliver.handoff` instead of
  releasing the clone. The record is `runs/<run-id>/selfaudit.json`, surfaced as one line in the
  delivery PR body.
- `runner/`: `RunRequest`/`RunResult` in `base.py`. `cli.py` drives `claude -p` (prompt on
  stdin, `stream-json` for subscription usage, `deny_read` paths). `fake.py` replays
  `tests/fixtures/runner/<stage>.json`. `BACKEND=cli|fake` picks one.
- `store/`: one `STATES`/`TRANSITIONS` state machine. `sqlite.py` holds all SQL. `github.py`
  makes GitHub issues plus labels the queue (`stage:`/`kind:`/`via:` families; legacy `harness:*`
  labels are still read) and keeps SQLite as scratch. `STORE_BACKEND` picks one. `stage_run`'s
  CHECK names every stage, so a new stage also bumps `LAYOUT_VERSION` and moves the rebuild
  probe in `migrate()` to the new name. A new stage also needs entries in
  `governor._TURNS_FALLBACK` and `priority.CLASS_OF_STAGE`.
- Governance:
  - `governor.py`: admission — the two subscription-usage stops, the stored rate limit and the
    per-stage turn caps.
  - `dispatcher.py`: a pure plan built from the run window, dependencies and halt state. It
    starts nothing. `capped` holds each item `deliver.delivery_cap_refusals` refuses while
    `MAX_OPEN_DELIVERIES` of the fork's pull requests are open upstream; `harness run` asks the
    same before each clone (D88).
  - `ledger.py`: `state/ledger.json`, which the workflows commit to the `harness-state` branch
    (D28). It holds the last usage reading and the carry slot: an item `deliver.handoff` hands
    off resumes before anything else, gated by `OVERRUN_PCT` instead of the weekly stop.
  - `priority.py`: five call classes, highest first `answer` > `unblock` > `directed` > `audit` >
    `suggested`. A `via:suggested` item is refused while any asked-for item is outstanding
    (`proposed` counts) or weekly usage is past `SUGGEST_MIN_HEADROOM_PCT`.
- GitHub: `gh.py` is the only authenticated client and the only module that writes. It stamps
  `MACHINE_MARKER` on every comment so workflows ignore the harness's own replies. `trust.py`
  is the one gate: a level in `.harness/trust.txt` plus either an OWNER/MEMBER/COLLABORATOR
  association or a `vouch:<numeric user id>` on that line, which pins it to one account and is
  honoured on every repository (D69). `harness trust line <login> --level N` prints the line to
  paste (it never writes, because `.harness/` is outside the write roots), and
  `harness trust show` prints the file as the gate reads it.
- Result definition:
  - `gates.py`: the product's fixed seven-gate sequence. It is never widened; a red gate the
    harness can't fix becomes a blocked item.
  - `packager.py`: builds the review package.
  - `redact.py`: secret redaction plus the write guard.
- Config: `.env` holds every required key. Defaults live in `.env.example`, never in code.
  `.harness/config.json` overrides it, restricted to `config.CONFIG_JSON_KEYS`; any other key is
  a startup error. `PERMISSION_TIER` is 0 or 2 only, and tier 2 requires `STORE_BACKEND=github`
  and `FORK_REPO`.
- Two modes, one code path:
  - Actions mode: `.github/workflows/`, tier 2, GitHub store.
  - Local mode: the `bb` Docker container in `local/` plus the `bb-*.ps1` scripts, tier 0,
    SQLite. See `docs/LOCAL-MODE.md`.

  Code above the store must not know which mode it's in (I-16).
- Three kill switches:
  - `.harness/HALT`: committed, and read first by every spending workflow.
  - `HALT_FILE`: a gitignored root file, written by `harness halt`, that stops local runs.
  - The commanded halt stored in the ledger by `/harness halt`.
- `runs/` doesn't survive between Actions runs. Anything needed across runs must come from a
  durable source: the committed proposal file (D46), GitHub, or the ledger. `DECISIONS.md` lists
  the remaining places that still assume `runs/` survives.

## Rules the tests enforce

`tests/test_invariants.py` checks the source tree (invariants I-1 … I-17 in `docs/SAFETY.md`;
I-18, "the harness never works on its own repository", is `DECISIONS.md` D61).
`tests/test_docs_drift.py` checks that docs still agree with the code. Both fail the build.

- Standard library only in `harness/`. Tests may use only pytest, so parse workflow YAML with
  regex, not `yaml`. No `threading`/`asyncio`.
- `os.environ` is read only in `config.py`. SQL lives only in `store/sqlite.py`. Non-GET HTTP, the
  `Authorization` header and the token getter `config.github_token()` are used only in `gh.py`. The
  `gh` CLI is never invoked. No merge, approve or dismiss endpoint may exist. Issues are created
  only in `SELF_REPO`, except a delivery's one tracking issue upstream (D83). The writes that can
  close, label, edit or request review on a product thread (`close_pull`, `request_reviewers`,
  `edit_product_issue`, `label_product_issue`) call `_require_own_thread` first, since Triage
  upstream would allow them on anybody's thread; `set_labels`, `update_issue_body` and
  `create_label` name `SELF_REPO` at every call site (D88). Every GitHub write passes through
  `redact`.
- Files are written only via `redact.guarded_write` / `write_redacted`, inside the roots set
  in `build_context`: `runs/`, `packages/`, the db dir, `state/`, `proposals/`, `HUMAN.md`, `.env`
  and `HALT_FILE`. `.harness/` is not a write root, so the harness can't change its own pin,
  trust list or kill switch.
- A new Python file under `harness/` must be added to `SPEC_PACKAGE_FILES` and
  `D2_PACKAGE_FILES` in `test_invariants.py`, or the file-map tests reject it. Workflow crons,
  timeouts (≤120 min), step order (HALT → doctor → sync-fork → dispatch) and "a new workflow cites
  a decision" are pinned too.
- The pin: `harness/gates.py`, `harness/packager.py`, `harness/redact.py` and every file under
  `prompts/` are hashed into `.harness/PIN`. After editing one, run `verify_pin --write`, commit the
  new PIN, and record the reason in `DECISIONS.md`. `doctor`, `implement.yml` and the container
  fail closed on a mismatch. Those files, plus `.harness/`, `.github/`, `verify_pin.py`, `trust.py`
  and `keywords.py`, are CODEOWNERS-protected.
- Docs quote code, and the drift tests check that they match:
  - verbs, aliases and their levels (`COMMANDS.md`, `FOR-MAINTAINERS.md`, `README.md`,
    `.harness/trust.txt`)
  - every CLI subcommand (`COMMANDS.md`)
  - `stage:` labels, the inbox issue number and the run window (`FOR-MAINTAINERS.md`)
  - every cron quoted in `README.md`, `OPERATIONS.md` and `.harness/README.md`
  - a `CONFIG_JSON_KEYS` count spelled in words (`SAFETY.md`, `.harness/README.md`,
    `.env.example`)
  - README's behavior ranges
  - the token scopes every live doc, prompt and workflow claims (B310, B311)
  - the delivery PR body table in `PACKAGE-FORMAT.md` §6

  If you rename or add one of these, update the docs in the same change.
- Adding a required config key means updating `.env.example`, `DEFAULT_ENV` in `tests/conftest.py`,
  and the other test `.env` helpers (D22). Removing one means adding it to `config.RETIRED_KEYS`
  and updating the same helpers, so an existing `.env` keeps loading (D74).

## Conventions

- Numbering: behaviors are `B<n>`, cited in test names and docstrings. Decisions are `D<n>` in
  `DECISIONS.md`, and invariants are `I-<n>` in `docs/SAFETY.md`. The newest decisions are the
  prose sections after the tables, so take the next number after the highest `## D<n>` heading,
  and after the highest B-number cited under `tests/`:

  ```bash
  grep -oE '^## D[0-9]+' DECISIONS.md | tr -d '#D ' | sort -n | tail -1
  grep -ohE '\bB[0-9]+\b' tests/*.py | tr -d B | sort -n | tail -1
  ```

  A B-number cited only in a test docstring must stay cited: README's ranges are checked
  against every number the suite cites.
- Commits use a conventional prefix and a sentence-style subject, e.g.
  `fix: a warning must not take the fleet down (#27)`. Work on a branch and open a PR; the user
  merges. Commits and PR descriptions carry no AI attribution: no `Co-Authored-By` trailer and no
  generated-with line.
- `.handoffs/` is untracked and not gitignored. Stage explicit paths and never `git add -A`.
- The harness computes and prints no dollar figure. What every surface reports is subscription
  usage (D74).
- Never print a secret value. Refer to tokens by key name only. `.env` holds the real machine
  PAT and the Claude OAuth token.
- Comments, docstrings and docs state behaviour in the present tense: what the code does when the
  name doesn't say, invariants and preconditions, ordering and format contracts, external quirks,
  and in one sentence why a surprising choice is right. Leave out history and incident stories,
  "not X but Y" contrasts, ALL-CAPS or bold emphasis, "deliberately"/"honest"/"the whole point",
  dollar anecdotes, and citation chains; keep at most one trailing `(Dn)`, `(Bn)` or `(I-n)`.
  Docstring bodies stay within 6 lines (10 for modules and CLI commands) and block comments
  within 4; anything longer belongs in `DECISIONS.md`.
- The fakes replace the model, GitHub and the checkout, so a green suite proves little for
  changes in `gh.py`, `clone.py`, `stages/deliver.py`, `stages/revise.py` and the workflows. For
  example, the deliver tests never advance upstream before the rebase. A test that touches the
  host must neutralise it: fake `WHICH`/`RUN` (a runner has no `claude`), pass an explicit git
  identity (runners have none), compare strings ordinally (PowerShell 7's ICU comparison
  ignores some control characters), and write `MIN_FREE_DISK_GB=0` in any `.env` a `doctor`
  test loads (the probe measures the drive holding `tmp_path`, which is not the repository's).
- Adversarial pass on every harness PR: after opening the PR, have one or two independent reviews
  check the diff, with "the tests pass" ruled out as evidence. Post the findings as a PR comment,
  then fix them.
