# Decisions — Delivery 1 build

Recorded reasons for every place the implementation goes beyond, or reads between the lines of,
`docs/delivery/HARNESS-SPEC.md` v1.1. §0 of the spec asks for exactly this file.

| # | Decision | Why |
|---|---|---|
| D1 | A dependency-install step (`npm ci` at the root and in `backend/`, only where a lockfile exists) runs before the baseline gate sequence. | On a fresh clone every one of the seven gates is vacuously red (`eslint is not recognized`, `npx` fetching a random `prisma@8-rc`). The product's own pre-push hook assumes an installed tree (`--skip-install`). The step is recorded in `EVIDENCE.md` under its own heading and in `manifest.json` it is absent from `gates`; the gate sequence itself is unchanged (§5.11). |
| D2 | A post-change sequence whose only reds are pre-existing is recorded as "no new failures versus baseline; NOT green", never as "green". | §5.9.3 says baseline reds are not attributed to the change; it does not say they are cured. Calling the sequence green would be the "silently green" outcome A13 forbids. |
| D3 | `bash scripts/check-prisma-drift.sh` is red on the untouched tree on this host (it wants a database). | §12 Q1 is open. The red is carried as pre-existing; nothing is loosened. Answering Q1 (a throwaway Postgres) would turn it green. |
| D4 | `governor.Authorization` keeps its spec-frozen name although `docs/delivery/HARNESS-REVIEW.md` R2.3 greps `harness/` for the literal `Authorization`. | §5.3 freezes the dataclass name. R2.3's intent is the HTTP header; the only hits are the dataclass and its four uses in `governor.py`, and `gh.py` sets no such header (B31). The reviewer should read those hits, as R2.7 already instructs for its own grep. |
| D5 | On Windows, `doctor` and the CLI runner resolve `claude`/`npm`/`npx` through `shutil.which` before spawning. | The entry points are `.CMD` shims and `CreateProcess` will not find them by bare name. Injected spawns still receive the unresolved argv so B25 stays testable. |
| D6 | Halt is honoured inside `implement` (after install, after baseline, after the post-change gates, before each diagnose cycle), not only at stage boundaries. On halt the clone is released and the item reset to `approved`. | A10 and R7.7. A halt that waits for a 30-minute gate run to finish is not a kill switch. |
| D7 | `Config` carries two extra trailing fields, `github_token_present` and `github_token_shape_ok`, and `config.py` exposes `environ_snapshot()`, `secret_values()`, `read_secret()`. | I-4 confines `os.environ` to `config.py`, yet the CLI runner must build a child environment (B26), the redactor must scrub configured secret values (§5.8), and `identity` must detect presence without reading the value (§5.12). These are the narrowest seams that satisfy all three. |
| D8 | The session budget lives in memory on the `Governor`, per process. | §5.3 gives no persistence for it and every command is a fresh process; the weekly budget is the durable one and is in the store. |
| D9 | Directed discover leaves the row in `discovered` as created; `packaged → shipped` is accepted by the store's state machine. | §5.2.2 lists the pair and B10 requires every listed pair to be accepted. The stage that would use it does not exist, which is the actual Tier-2 guard (§9 I-1). |
| D10 | The two secret keys (`HARNESS_GITHUB_TOKEN`, `ANTHROPIC_API_KEY`) are optional in `.env`; every other key is required. | B79 needs "absent" to be a legal state; B3 needs typo'd budget keys to fail. |
| D11 | `redact()` replaces the whole match of the generic `key: value` pattern, key name included, and also scrubs `Bearer <token>`. | B49 says the pattern is replaced with `[REDACTED]`. The spec's own `\S+` stops at the space after `Bearer`, which would leave the token behind. |

Open questions carried forward unchanged from §12: Q1 (local throwaway Postgres), Q2 (issue comments /
Tier 1). The collision re-check before implement (Q2's stop-gap) is live and blocked a real item during
acceptance (`#801`, claimed by `fix-801/ci-shell-gate-isolation`).

---

# Decisions — Delivery 2 build

Recorded reasons for every place the Delivery 2 implementation amends Delivery 1, reads between
the lines of `docs/delivery/DELIVERY-2-HANDOFF.md`, or records something the handoff asked to be recorded here
(§16 item 14, §10.6). Numbering continues from D11. Delivery 1's D1–D11 stay in force.

| # | Decision | Why |
|---|---|---|
| D12 | `harness/store.py` is moved verbatim (`git mv`) to `harness/store/sqlite.py`; `harness/store/__init__.py` holds the `Store` protocol and re-exports `Store`, `WorkItem`, `StageRun`, `STATES`, `SqliteStore`. Invariant I-5's path amendment: SQL exists only in `harness/store/sqlite.py`. The existing I-5 test is amended to the new path, not deleted. | Handoff §2 and §3 make the store the single seam between the two modes; a second store (`store/github.py`) has to sit behind the same protocol. A move rather than a copy so two stores cannot drift (R1.2). `from harness.store import Store` keeps working and `Store(db_path, clock)` stays constructible, so no Delivery 1 test changes beyond the path. |
| D13 | I-1 ("no module issues a non-GET request") becomes **I-1′**: only `harness/gh.py` may issue a non-GET request. Paired with the new I-11: `gh.py` is the only module that constructs an `Authorization` header and the only importer of the one token getter, `config.github_token()`. The I-1 test is amended to exempt `gh.py` and the I-11 test is added alongside it; neither is deleted. | Delivery 2 writes (push, PR, comment, label, issue) are the point of the delivery, and they have to live somewhere. Confining every write verb to the one module that also holds the credential keeps the reviewable surface at one file. Recorded as an amendment with its reason rather than a silent relaxation, per R12.3. |
| D14 | `PERMISSION_TIER` accepts exactly `{0, 2}`; `1` and everything else is a `ConfigError`. Tier 2 additionally requires `STORE_BACKEND=github` and a non-empty `FORK_REPO`. The B4 test is amended to accept 2 and to reject 1. | The handoff builds Tier 2 and does not build Tier 1 (comment-only) as a mode; accepting a tier that has no implementation would be a mode that is neither tested nor refused. At tier 0 every Delivery 1 guarantee holds unchanged, which is what the container runs at. |
| D15 | Delivery 1's A1 manifest check (the file list) is superseded by the Delivery 2 file map in handoff §3; the A1 test is amended to the new map. | §3 is normative for structure and adds `store/`, `dispatcher.py`, `ledger.py`, `keywords.py`, `trust.py`, three stages, six workflows, `local/`, and the `bb-*` scripts. A manifest test pinned to the Delivery 1 list would fail on every one of them for no safety reason. |
| D16 | The `packaged` state is kept from Delivery 1 and represented by the label `harness:packaged`, sitting between `harness:running` and `harness:shipped`. `STATES` and `LABELS` carry twelve entries; `harness init --labels` creates all twelve. | The handoff's §4.2 table omits it, but `package` is a distinct stage with a distinct state in Delivery 1 (`archive` refuses anything else), and `deliver` needs a state to require. Dropping it would change `packager.py`'s contract, which is a do-not-touch file. |
| D17 | `redact.allowed_roots()` gains exactly two roots: `<repo root>/state` and `<repo root>/proposals`. `.harness/` — including `PIN` and `HALT` — is deliberately **not** a root. | The ledger (§6.2) and the proposal files (§4.3) are the two new things the harness writes in the repository, and R11.7 asks that each addition be deliberate and named. B143 requires the pin to be outside the roots so the harness cannot change what it is measured against; the same reasoning covers the trust list, the config, and the repo-level kill switch. Handoff §1.1 says "the roots list gains nothing"; the handoff's own §4.3, §6.2 and R11.7 contradict that, and the two roots are the minimum that makes those sections implementable. |
| D18 | `bb-net` is a plain bridge. No egress allowlist. | Handoff §10.6, verbatim: "An allowlist is rejected. The product repository's `npm ci` pulls from the npm registry and its CDN backers, whose addresses rotate; the platform document's own caveat is that hostnames resolve once when rules are applied, so such an allowlist breaks unpredictably weeks later with no error. A rule set that must be disabled the first time it bites is worse than none, because it is believed. `bb-net` is a plain bridge and this paragraph is the ruling." |
| D19 | `claude --max-budget-usd` **binds under subscription auth** — verified 2026-09-03 on CLI 2.1.257: a 700-word essay with `--max-budget-usd 0.001` returned `is_error: true`, `subtype: error_max_budget_usd`, an empty result and exit 1, while the uncapped control cost $0.116. The cap is enforced at the **turn boundary**: the over-budget turn completes and is charged ($0.153 here), then the run stops. So `PER_CALL_CAP_USD` bounds a multi-turn `implement` to roughly one turn past the cap, not to the cap exactly; the harness's own accounting (WEEKLY_CAP_USD, the ledger) remains the primary control and the CLI cap the backstop — §6.1's "enforced twice" holds. The session JSON carries `usage`/`modelUsage` but no remaining-allowance signal (§1.3 confirmed). |
| D20 | No `threading` and no `asyncio` anywhere under `harness/`. The `local-loop` command's heartbeat is synchronous: the entrypoint writes the first `HEARTBEAT`, and the loop rewrites it at the top of every unit and every 10 s between units from a plain sleep loop. | R9.3 scans `harness/` for `threading`; a daemon thread in the CLI would fail it. A synchronous heartbeat with a 180 s staleness window and units that poll `STOP` at their boundaries is enough for the watchdog, and it keeps the package single-threaded, which is what every Delivery 1 invariant test assumes. |
| D21 | An issue on the upstream (product) repository is never read as a command source. Keyword commands are honoured from exactly three surfaces: pull requests on the product repository (delivery PRs the harness opened), and issues and pull requests on this repository. `keywords.sweep` filters notifications to those surfaces. | Handoff §8.3 lists the surfaces as "proposal PR", "delivery PR", and "any issue here". The machine account owns no issue upstream and is not a collaborator there, so a command on an upstream issue has no item to act on and would only widen the surface an untrusted actor can probe. The trust gate (B131) would still deny it; not reading it at all is cheaper and leaves nothing to get wrong. |

Open questions carried forward from the handoff §17: Q2 (fork CI as a second opinion — treated as
confirmation, not a gate), Q4 (`runs/` artifact retention — 30 days). Delivery 1's Q1 (throwaway
Postgres) remains open; the database gates are still reported as omitted.

## Delivery 2 — build-time rulings (appended at the end of the fullsend run)

| # | Decision | Why |
|---|---|---|
| D22 | Every Delivery 1 test helper that writes a `.env` (conftest `DEFAULT_ENV`, test_cli `ENV_BODY`, test_stages `write_env`, test_packager `_env_text`, test_clone/test_identity env dicts) gained the thirteen Delivery 2 keys with the `.env.example` values. | Handoff §6.5: "every key required, no defaults in code". Without this every D1 test fails at `load_config`. Data constants only; no test function changed. |
| D23 | `docs/delivery/DELIVERY-2-REVIEW.md` R3.3 (`grep "github_token\|HARNESS_GITHUB_TOKEN" harness/`) will hit `config.github_token_present` in `config.py` and `identity.py`. | That field is Delivery 1-frozen (RUN-DECISIONS "Config extras", B79). The token *door* `github_token()` is defined in `config.py` and imported by `gh.py` alone; the field carries a boolean, never the value. Read the hits, as R2.7/D4 already instruct. |
| D24 | `CLAUDE_CODE_OAUTH_TOKEN` is a pass-through key (`config.PASSTHROUGH_KEYS`): accepted in `.env`, never stored on `Config`, included in `secret_values()` so `redact` scrubs it. It is not added to `SECRET_KEYS`, which Delivery 1's B49 test pins to two names. | `.env.example` must ship it (handoff §5.4) and `harness init` must load what it ships (A7, D2-R4.1). |
| D25 | Six spec-quoted test corrections were made during reconcile, each logged with the quoted sentence in `.fullsend/notes/test-corrections-d2.md` (summarised here because that directory is not committed): I-11 test scans string constants, not identifiers (D1 §5.3 `Authorization` dataclass); B143 expects D1's eight roots plus two; two `UPSTREAM_REPO == REPO` parametrizations dropped (never in the handoff); two GitHub-store tests take the legal `proposing` hop before `blocked` (D1 §5.2.2); one test double returned `""` where the frozen `DIFF_LINES` contract is a 2-tuple. | Fullsend's rule: a test loses only to a quoted spec line. |
| D26 | One transition table serves both stores: Delivery 1's §5.2.2 plus the Delivery 2 states, minus `discovered→blocked`, `approved→blocked`, `shipped→abandoned` (pinned illegal by D1's B11 test). `decompose` labels the parent `discovered→proposing→blocked`; `/harness stop` on a shipped item goes `shipped→blocked→abandoned`. | Two tables in two stores is the drift the store seam (§2) exists to prevent. |
| D27 | `docs/delivery/DELIVERY-2-REVIEW.md` R5.1 (`git log -p --all \| grep -cE "ghp_\|github_pat_\|sk-ant-"`) cannot read `0` for this repository: the redaction tests (Delivery 1's `test_redact.py` onward) necessarily name those prefixes, and two Delivery 2 tests carry a synthetic `ghp_FAKE0…` value so the B108 redaction path is exercised. | The intent — no *real* credential committed — is checked with a shape-aware form that ignores single-token filler: `git log -p --all \| grep -E "ghp_[A-Za-z0-9]{36}\|github_pat_[A-Za-z0-9_]{82}" \| grep -vE "(FAKE0|A{36}|x{36})"` → no output. GitHub push protection validates the checksum in a real PAT's tail; synthetic strings do not carry one. |
| D28 | `state/ledger.json` is committed by the workflows to a dedicated unprotected branch `harness-state` (loaded at job start, pushed at job end from a one-file worktree). The checked-in copy on `main` is the initial ledger and local mode's baseline. | B113 (main requires one approving review) and B115 (the workflow commits the ledger directly) cannot both hold on `main`: the Actions token cannot open or approve a PR for itself. The state branch keeps both properties without a bypass rule. |
| D29 | The machine account is `jgoetzmann-bot` (fork `jgoetzmann-bot/brightboost`), not the spec's `brightboost-harness`. `Identity.handle` is derived from the owner of `FORK_REPO` when it is set; the spec's default name applies only until then. The harness's commit-author email stays `harness@brightboost-harness` — an internal marker for B139, not a mailbox. | The operator chose the name (2026-09-03); the fork owner IS the machine account (handoff §5.1), so one setting rules both. |
| D30 | Nathan (`BrightBoost-Tech`) has **no access** to `jgoetzmann/bright-bots-harness`: not a collaborator, not in CODEOWNERS. He stays in `.harness/trust.txt`, which is what makes his comments on delivery PRs in the product repository count (B131: trust file AND his OWNER/MEMBER association *there*). Proposal review (gate 1) is therefore "only jgoetzmann" (HUMAN.md item 13's other branch). | A write collaborator on this repo could put code on `main` that a spending workflow runs with both secrets in scope; the design needs nothing from him here — gate 2 lives upstream, where he is already the maintainer. Decided 2026-09-03. |
| D34 | (Ruled after Delivery 3; filed in this table because it concerns the Delivery 1 and Delivery 2 documents.) The four delivery documents — `HARNESS-SPEC.md`, `HARNESS-REVIEW.md`, `DELIVERY-2-HANDOFF.md`, `DELIVERY-2-REVIEW.md` — move (`git mv`) from the repository root to `docs/delivery/`, with `docs/delivery/README.md` as their index. `HUMAN.md` and this file stay at the root. Cross-references between the four stay bare filenames (they are siblings now); the one path that actually broke, D2-R2's `pathlib.Path("DELIVERY-2-HANDOFF.md")`, is repointed. The frozen §3 file map in the handoff still shows the old root layout and is **not** edited — this row is its amendment, as D31 was for §1.3. | The root had eleven markdown files and a reader could not tell the two operator entry points from the four frozen delivery artefacts. `HUMAN.md` cannot move: B82 requires `harness setup` to write it at the repository root and five tests assert that path. `DECISIONS.md` cannot move either: `identity.budget_experiment_recorded()` reads `<repo root>/DECISIONS.md`, the pull-request template names it as the place a pin change is justified, and fifteen files cite it by bare name. Moving what nothing resolves and leaving what something does is the whole of the rule. |

---

# Decisions — Delivery 3 build

Recorded reasons for the usage-aware governance delivery (`.fullsend/RUN-DECISIONS-D3.md`,
behaviors B200–B215). Numbering continues from D30. Every earlier decision, D1–D30, stays in force.

| # | Decision | Why |
|---|---|---|
| D31 | The subscription **does** expose its remaining allowance, and the harness now reads it: `claude -p --output-format stream-json --verbose` emits one `rate_limit_event` per call carrying `five_hour` and `seven_day` utilization as fractions 0..1 (shape quoted in full below). It comes from the inference response headers, so the long-lived `setup-token` receives it in Actions mode too. `seven_day.resetsAt` is the subscription's weekly reset. This supersedes DELIVERY-2-HANDOFF §1.3's "no signal exists" and D19's closing sentence. **B114 is kept, restated as a must-not-depend rule**: no decision may DEPEND on the signal being present. With `usage=None` — fake backend, older CLI, a call that never reached inference — the USD path (`WEEKLY_CAP_USD`, `RESERVE_PCT`, `PER_CALL_CAP_USD`) governs exactly as in Delivery 2, and `Governor.usage_stop_reason` returns `None` rather than guessing (B207). | Verified 2026-09-03 on the CLI. A signal that is present on every real call and absent on every fake one cannot be made a precondition without making the fake backend a different program; keeping B114 as "must not depend" is what lets the same code path serve both, and it is the difference between reading a number and trusting it. Recorded as an amendment with its evidence rather than a silent reversal, per R12.3 — the same treatment D13 gave I-1. `WEEKLY_CAP_USD`'s default rises 25.00 → 400.00 for the same reason: a dollar cap sized for Delivery 2 would bind first and the usage stop would never be reached, which would make the new signal decorative. |
| D32 | The run window for this account is `RUN_WINDOW_START=mon 08:00` to `RUN_WINDOW_END=tue 20:00` UTC, and `implement.yml` runs three crons inside it: `17 8,14,20 * * 1`, `17 2,8,14 * * 2`, `23 20 * * 2`. The window is enforced by the dispatcher; the crons only decide when GitHub wakes the job. The DST drift is documented in the workflow and in OPERATIONS §13.3 and deliberately **not** corrected in code. | The subscription's weekly allowance resets Tuesday 20:00 UTC (13:00 PT). Spreading work across the whole week meant hitting the seven-day ceiling on a random Thursday with a branch half-written; concentrating it at the head of the window means a fresh allowance and a known reset to plan against, and the Tuesday 20:23 row is the wrap-up that spends what is left before it evaporates. Round-the-clock `23 */6 * * *` also competed with interactive use every single day. On DST: GitHub cron is UTC and never shifts while the reset is quoted in Pacific time, so for the PST months the wrap-up fires 37 minutes early, sees the old window, and spends nothing extra; the following Monday picks the new one up. A skipped wrap-up per winter is cheaper than a timezone table in a cron file, and an operator who cares moves that row and `RUN_WINDOW_END` together. |
| D33 | A usage stop or rate limit inside `implement`/`continue`/`package`/`deliver` is a **handoff**, not a failure: uncommitted work is committed as `wip: handoff (<reason>)`, the branch is pushed to the fork only (never upstream, never forced — B212), `runs/item-N/HANDOFF.md` is written and posted as a comment, the item returns to `approved`, the ledger records one `carry`, and the command exits 0. The carried item is the first thing the next run starts — even outside the run window — via `harness revise <id> --source continue`, spending against `OVERRUN_PCT` rather than `WEEKLY_USAGE_STOP_PCT` until it is green. | Delivery 2's answer to running out mid-item was to leave the item where it stood; with a weekly reset that lands in the middle of an implementation, that is a branch abandoned halfway every week. Carrying it costs one ledger field and one file, and `HANDOFF.md` is the same evidence a human would need anyway. Continuing outside the window is the one exception the window has, because the alternative is holding a half-finished branch for six days. The leeway is bounded (`OVERRUN_PCT`, default 10 %) so a carry cannot quietly consume the new week, and only one item is ever carried. Exit 0 because B120 already settled that a limit is a normal outcome: a red exit code here would page someone for the scheduler working as designed. |

**D31, in full — the `rate_limit_event` shape, verbatim as observed:**

```json
{"type":"rate_limit_event","rate_limit_info":{"status":"allowed","resetsAt":1788519600,
 "rateLimitType":"five_hour","overageStatus":"rejected","isUsingOverage":false,
 "unifiedWindows":{"five_hour":{"utilization":0.07,"resetsAt":1788519600},
                   "seven_day":{"utilization":0.49,"resetsAt":1788897600}}}}
```

Stored in `state/ledger.json` as `window.usage` in the harness's own shape
(`{"five_hour": {"utilization", "resets_at"}, "seven_day": {…}, "status", "observed_at"}`),
where `observed_at` is stamped by the stage from the clock, not by the runner.

Open questions carried forward unchanged: Delivery 1's Q1 (throwaway Postgres for the database
gates) and the handoff's Q2 and Q4. Nothing in Delivery 3 touches them.

## Delivery 3 acceptance — the two trial runs (2026-09-04)

Recorded reasons for the five defects the first end-to-end runs surfaced (behaviors B216–B219).
Numbering continues from D34. Every earlier decision, D1–D33, stays in force. Each of these was
invisible to 1423 passing tests because every one of them lives at a boundary the fake backend
replaces: the process spawn, the checkout, and the model's own filesystem access.

| # | Decision | Why |
|---|---|---|
| D35 | The prompt is passed to `claude` on **stdin**, not as the last argv element, and the `--` option terminator is gone with it (B216). `build_argv` now returns flags only, and `ClaudeCliRunner.run` passes `input=request.prompt`. A pre-flight ceiling refuses an oversized argv legibly instead of letting the platform do it (`argv_too_long`, `ARGV_LIMIT_WINDOWS = 8191`). | The first real `harness propose` on Windows failed with `The command line is too long.` — a bare cmd.exe message that arrives as a non-zero exit and reads like a failed model call. `claude` is an npm `.CMD` shim, so the whole command line passes through cmd.exe, whose ceiling is 8191 characters; the propose argv was 12,761. The prompt is also the one argument with no bound: an implement prompt carries a diff and a gate log, and would eventually breach even the 128 KB POSIX per-argument limit. Moving it to stdin removes the unbounded term and makes the terminator unnecessary — nothing follows the flags, so a prompt starting with `-` can no longer be misread. The remaining ceiling is checked before the spawn because the failure it replaces was silent about its own cause. |
| D36 | Every model call carries a `permissions.deny` list through `--settings`, built by `stages.deny_read_paths` from the repository root and the operator's home, and `--setting-sources` is emptied first (B218). | `Read` is **not** confined to the working directory. Measured, not assumed: a `propose` call whose `cwd` was an empty run directory read `D:/Programming-Projects/Bright-Bots/brightboost-707` — an unrelated checkout — and, in a direct probe under the same flags, read the harness's own `.env` and reported that it holds a live `ghp_` PAT and an `sk-ant-oat01-` token. On an Actions runner the generated `.env` sits two directories above every stage's `cwd`, so that path is live in production, not just on a developer box. The rule form matters and was measured too: a bare absolute path is enforced, a `//`-prefixed one is accepted and matches nothing, and a relative glob like `**/.env` matches nothing. Emptying `--setting-sources` stops the operator's own `~/.claude` and this repository's `.claude/` from widening what a stage may touch. This is an enumerated deny list, not a sandbox, and it is documented as one: it closes the credential path, which is the part that cannot be allowed to stay open. |
| D37 | `propose` acquires a read-only clone of the product repository, runs the model with `cwd` and `--add-dir` set to it, validates `touched_paths` against **that** commit, and releases it with `keep=False` (B219). | `prompts/propose.md` says "Read the repository at the current working directory", requires citations with line numbers, and forbids listing a path the model has not seen. The stage passed `cwd=ctx.run_dir` — a directory containing only `transcript/`. On a runner that means no source at all, so the package is guesswork and `touched_paths` fails validation; on a developer box it meant the model went looking and found a *different* checkout, which is worse, because the resulting package cited real-looking line numbers from a tree the harness will never touch. The pinned prompt is the frozen artifact and it is right; the wiring is what drifted. Validating against the lease's own base makes `_path_checker`'s "at the pinned base commit" true rather than aspirational. The cost is one extra `--no-tags` clone per proposal, released immediately so `MAX_CONCURRENT_ITEMS`/`MAX_CONCURRENT_CLONES` are untouched. |
| D38 | The pin hashes **normalised** content: CRLF and CR fold to LF before the digest (`verify_pin.normalise`, B217), and a `.gitattributes` with `* text=auto eol=lf` keeps the working tree itself consistent. | `selftest` on `windows-latest` failed at the pin step, before a single test ran: `.harness/PIN` has `14a2a5…`, computed `2a0324…`. `core.autocrlf` defaults to true on Git for Windows and `actions/checkout` inherits it, so one commit lands as CRLF on a Windows runner and LF on a Linux one. Hashing raw bytes therefore made the pin — which `doctor`, `implement.yml` and the container all fail closed on — reject every Windows checkout. That is a false alarm, not the tamper the pin exists to catch, and a fail-closed check that cries wolf on a whole platform is worse than no check, because the fix people reach for is to stop running it. A line ending is not a semantic change to Python source or to a prompt; every other byte still moves the hash (pinned by a test). The digest on an LF tree is unchanged, so `.harness/PIN` keeps the value it had. |
| D39 | Every `git init` in the test suite names its initial branch (`init -q -b main`). | `selftest` on `ubuntu-latest` failed four `B105` sync-fork tests with `src refspec main does not match any`. The helpers created bare repositories with a plain `git init`, so HEAD followed the ambient `init.defaultBranch` — `main` on this machine, `master` on a runner — and a clone of a bare repo whose HEAD points at a branch that does not exist checks out nothing. The suite had never run on CI: `selftest` triggers only on `pull_request` and `workflow_dispatch`, and neither had happened. "1423 passing" was a Windows-local fact, not a portable one. |
| D40 | The fork is the clone source only at tier 2 (B220). Below it, clones come from the product repository. | `sync_fork` fast-forwards the fork through `gh.push_ref`, which needs a write credential, so at tier 0 the fork is frozen wherever it was last left. The trial cloned `jgoetzmann-bot/brightboost` at `9e01ff7` while upstream was at `a00b8c9` — twelve commits stale — and reported it as the product repository. Every citation, every `touched_paths` check and every gate run would have been against a tree nobody is going to merge into. With no push there is no reason to prefer the fork at all, and at tier 2 `implement.yml` syncs it before anything is cloned, so the tier-2 behaviour is unchanged. |
| D41 | `harness ledger` prints the observed subscription usage, the configured stop for each window, the distance to it, and the reset time (B221). | Delivery 3 made both governance stops depend on the `rate_limit_event` signal and then printed neither the signal nor the distance to the stop, so the one question an operator asks the ledger — "how close am I?" — had no answer in its output, and the first live confirmation that the plumbing works at all had to come from reading `state/ledger.json` by hand. When nothing has been observed the line says so rather than printing a zero, which is B114's must-not-depend rule showing through to the operator. |
| D42 | The change set and the format list are two different lists (B222). `prettier.all_changed_paths` reports every path that differs from the base, deletions included, and is what `implement.CHANGED_PATHS` now binds to; `prettier.changed_paths` keeps its `--diff-filter=AM` and is used only for the paths actually handed to prettier. | `changed_paths` was written for prettier and is right for prettier: formatting a file that no longer exists is an error, so it filters deletions out. `implement` then reused that same list for two questions it does not answer — "did anything change?" and "does this diff touch anything forbidden?". Issue #633 is a deletion-only ticket, so the first live implement run deleted exactly the two files its work package named, recorded `changed paths versus a00b8c99703c: (none)`, committed nothing, and packaged **zero patches** while printing `implemented item 1` and exiting 0. The second consequence is worse than the first: the forbidden-diff guard (I-15, no `.github/**`) was fed the same filtered list, so a change that *deletes* a CI workflow passed the check that exists to stop exactly that. Deleting a workflow is not milder than editing one. |
| D43 | An implement call that leaves the tree unchanged blocks the item and raises; it no longer proceeds to the post-change gates (B223). The block reason names the paths the work package expected to touch. | The D42 run's exit code was 0. Everything downstream agreed: the gates were green (they ran against a tree that had the deletions in it, just uncommitted), the packager wrote a well-formed package, and the only trace was the line `Patches: 0` sitting under a list of two paths the package said it would change. A silent success is the one failure a reviewer cannot catch by reading the exit code, and it is exactly what the harness exists to prevent. This guard would have caught D42 on its own, and it catches the general case — a model that says it is done and did nothing — for which there is no other check. Blocking rather than failing quietly also keeps the clone (`keep=True`) so the tree can be inspected. |
| D44 | `shutil.rmtree` of a clone goes through the Windows extended-length path form, and `_on_rmtree_error` re-raises instead of returning when a path still will not go. `acquire` then refuses a directory it could not clear, naming how much survived (B224). | The handler existed to clear git's read-only bit, and both of its retries ended in a bare `return` — so `rmtree` reported success over a partial delete. Measured: 1943 files survived a "successful" removal of a clone that had had `npm ci` run in it. The cause is `MAX_PATH`: nested `node_modules` chains run to ~185 characters on their own, and the deepest surviving path was 324. The failure did not appear where it happened; it appeared one step later as `git clone`'s "destination path already exists and is not an empty directory", which names neither the leftovers nor the reason. This is the path every re-acquire takes — `revise --source ci`, `--source review`, `--source continue`, and any retry after a block — so on Windows an item could be worked exactly once. The same removal with the extended prefix cleared the tree the old code could not. |
| D45 | The model and the reasoning effort are pinned in `.env` as `MODEL` and `EFFORT`, and reach the CLI as `--model` / `--effort` placed between the output format and `--max-turns` (B225). `doctor` reports both. | Left unset, the CLI picks. That makes the harness's output a function of somebody else's default: the same ticket, the same money and the same green gates could produce a materially different work package next month with nothing in the ledger or the package to say why. Every other knob that changes what a run is worth already lives in `.env` with no default in code, and these two belong in the same place. `EFFORT` is validated against the CLI's own levels (`low, medium, high, xhigh, max`, read from `claude --help`) so a typo is a startup error rather than a quietly smaller amount of thinking; `MODEL` takes an alias or a full name and only has to be non-empty, because the list of valid aliases is not ours to freeze. Position in argv follows one rule: what the session *is* precedes what it may spend, which precedes what it may do, which precedes what it may read. Both are omitted when unset, so the Delivery 2 argv shape is still exactly what a request without them produces. Set to `opus` / `xhigh` for this account: these stages are diagnosis and design, where the thinking is the product. |
| D46 | The work package is looked up from wherever it still exists: the recorded `spec_path` when that file is on this disk, else the committed `proposals/<id>-*.md`, found by glob on the id prefix and stripped of its front matter (`stages.propose.work_package_text`, B226). All four readers go through it — `implement._read_spec`, `packager.build`, and `deliver`'s two sites. | `propose` writes `runs/item-N/spec/N.md` and records that **absolute** path in the store. `runs/` is ephemeral per Actions runner and is never committed — only uploaded as an artifact that nothing downloads. And gate 1 is a human merging the proposal PR, which necessarily puts `propose` (discover.yml) and `implement` (implement.yml) in different runs, days apart in the intended weekly cadence. So the designed flow could never have completed on Actions. Measured on the first live attempt: all seven baseline gates passed on the runner and then `harness run --item 4` died with `spec file missing for item 4: /home/runner/work/.../runs/item-4/spec/4.md`, before the model call, before anything was spent. The durable copy was already there and is the very thing gate 1 merges. The slug in that filename comes from the proposal's own front-matter title rather than the item's, so the two do not match and the lookup globs the id prefix instead of deriving the name; `70-*.md` cannot satisfy item 7. The error when neither source exists now names both places it looked and says why, because the old message named only the dead path and read like a local bug. In the packager the lookup moved *above* the first `mkdir` so B73 still holds: a missing work package leaves no half-built package behind. `packager.py` is pinned, so this carries a re-pin to `c657d1d4`. |

**The rest of the cross-run family, confirmed and NOT yet fixed.** D46 fixed the reader that
broke the live run. A 34-agent audit over the same assumption — that `runs/` survives between
Actions runs — confirmed five more, each verified against the source by an adversarial second
pass. They are recorded here rather than fixed in the same change, because each needs its own
durable source chosen deliberately:

| Where | What breaks |
|---|---|
| `revise._baseline_red` (revise.py:673) | reads `runs/item-N/gates/baseline.json`; `_read` swallows the missing file and returns `""`, so on a second runner the pre-existing-red set is empty and **every gate that was already red becomes a "new failure"**, blocking the item. Defeats the whole point of `_new_failures`. The durable source needs choosing: the proposal's declared `baseline_red` is an expectation, not the measurement. |
| `item.package_path` (deliver.py:225, packager.py:547) | an absolute `runs/item-N/package` path. Harmless in `harness run --item`, where implement→package→deliver share one process; fatal for an item left in `packaged` and picked up by a later run, and for `harness archive`. |
| `HANDOFF.md` (revise.py:557) | the note whose entire purpose is to cross runs, written to ephemeral `runs/` and read back with no fallback — while `deliver.handoff` already posts the same body as an issue comment. The durable copy exists and nothing reads it. |
| `harness sweep` (__main__.py:1310) | every item's revise/propose runs under one `run_id="sweep"` Context, so per-item `run_dir` paths collide. |
| `heartbeat.yml:92` | never fetches `harness-state`, so the weekly alarm always reports the committed seed ledger rather than the live one. |

Fixed alongside D46 because they were one line each or were introduced by D46 itself:
`_acceptance` resolved the proposal against `Path.cwd()` instead of `config.repo_root` (its only
caller, `handoff`, has the Context — the docstring claiming otherwise was wrong); the
`proposals/<id>-*.md` glob took `sorted(...)[0]`, which picks the *stale* file when a re-propose
has landed a second one, and `int(item.id)` sat outside the error guard; and
`implement.yml`'s `git show FETCH_HEAD:state/ledger.json > state/ledger.json` truncated the
target before git ran, so a failed fetch would have left an empty ledger behind.
| D47 | Every issue and pull request the harness opens carries the same signature and the same cross-links, built in one module (`harness/links.py`, B227): who wrote it, that it merges nothing, the `/harness` commands, who may give them, and links to the documents and the three repositories. The work-item issue quotes the product issue it tracks; the proposal pull request inlines the proposal itself; the delivery pull request carries `Closes <product>#N` and a link back to the work item. | Everything the harness writes is read in a browser by someone who did not write it. Before this the work-item issue body was the string `issue:633` and nothing else, and the proposal pull request was one sentence pointing at a file — so judging gate 1 meant opening a diff, and a reader arriving cold had no way to tell what the thing was, who was allowed to steer it, or where to look next. One module owns that presentation so the three surfaces cannot drift apart, and a test pins `VERB_HELP` against `keywords.VERBS` so a new command cannot ship undocumented and another checks that every document the footer links actually exists. `Closes` is used only where merging really does resolve the thing named: the delivery pull request closes the product issue, and the proposal pull request only *refs* its work item, because approving a plan is not finishing the work. Making the body prose meant the machine-readable reference needed a home a parser could find, so it moved to a marked line and `_origin_ref` reads that first, falling back to the old first-line convention for items opened before the change. |
| D48 | `discover.yml` takes `mode`, `target`, `lens` and `ignore_allowlist` as workflow inputs (B228). | There was no way to aim the harness at a specific ticket from GitHub. Triage is the only thing the schedule runs, triage only considers product issues carrying `harness-ok`, and no issue carries it — so the weekly discovery found nothing and would have kept finding nothing. Seeding the queue meant a CLI on a developer's machine holding the machine account's token, which defeats the point of the thing running on Actions. `directed` mode queues one named issue and proposes it in the same run, which is the route that actually works today. The conditionals in that step are written as `if` rather than `[ … ] && …`: under `set -e` a false test in the second form is a non-zero command and would abort the job. |
| D49 | No git hook runs in a harness clone: `acquire` sets `core.hooksPath` to a directory that holds none, **and the push repeats it on the command line**, where nothing can override it (B229). A failed push reports git's own lines rather than the tail of whatever a hook printed. | `npm ci` runs the product repository's `prepare` script, which is `husky`, which installs `.husky/pre-commit`, `commit-msg` and `pre-push` into the clone. The harness's own `git push` then ran the product's pre-push hook — inside an environment the product does not test, duplicating a gate the harness had already run explicitly and recorded verbatim. Measured on the first live delivery: brightboost's pre-push calls `scripts/check-bundle-size.js`, which crashes under Node 22 with `require is not defined in ES module scope` because the package is `"type": "module"`, so the push was refused after implement and package had both succeeded and every one of the seven gates was green. The reviewer's error message was two thousand characters of vite chunking advice with the actual cause nowhere in it. This widens nothing: the seven pinned gates remain the only definition of "it works" the harness accepts, and a developer convenience installed as a side effect of an install is not one of them. **Setting it at `acquire` alone was not enough, and the second live attempt proved it**: `npm ci` runs `prepare`, which is husky, which sets `core.hooksPath` straight back to `.husky/_` — after `acquire` and before any push. So `_git_push` now passes `-c core.hooksPath=...` itself. The diagnostic changed with it: the old message took the last two thousand characters of the failure, which for a hook is whatever its last command printed — a vitest browser stack, both times — so it now prefers the lines git itself writes (`error:`, `fatal:`, `remote:`, `hint:`). |
| D50 | A work item keeps the reference it was created with: `_item_from` resolves it from the meta comment, then from the issue body, and only names the issue itself when neither survives (B230). | It hardcoded `self:<number>`, and that is quietly wrong in a way that reached the product repository. `WorkItem.issue_number` reads the digits out of that string, so for harness issue 4 tracking product issue 633 it answered **4**. `deliver` asks whether the reference starts `issue:` before writing a closing keyword — it did not, so the first delivery pull request went out **without `Closes #633`**, and its package README described the work as `self:4`. The guard is the only reason it did not write `Closes #4` on somebody else's repository. Both sources were already there: `create_work_item` records `external_ref` in the hidden meta comment, and the body carries it too. |
| D51 | The self-imposed GitHub request ceiling follows the tier: `GITHUB_API_CEILING_PER_HOUR` at tier 0, at least 5000 at tier 2 (`gh.ceiling_for`, B231). | The key defaults to 50, a margin under GitHub's unauthenticated 60 — the right number for Delivery 1, which held no credential. At tier 2 the machine account's token raises GitHub's own limit to 5000, and holding the harness to the unauthenticated figure is not caution, it is a stop in the middle of a run. Measured on the first successful delivery: the pull request was opened upstream and the **very next call**, a label write, was refused by our own ceiling — after every token had been spent, leaving the item's state label disagreeing with reality. The configured value still wins when it is higher, because an operator who raised it meant it. |
| D52 | The delivery pull request opens at what a reviewer needs and collapses the rest (B232): the closing keyword, how to steer it by comment, why CI may be waiting for approval, and `CONTRIBUTING.md`'s own review checklist with the three boxes the harness measured already ticked. The gate evidence becomes a table of every gate, its phase and its exit code, with any failing gate kept whole. | The first one was **52 KB**, and **40 KB of that was the verbatim stdout of seven gates that all passed** — a wall nobody scrolls, sitting in the one place a reviewer has to read, and close enough to GitHub's 65 KB body limit to be a real risk. What a reviewer needs from a green run is that it was green; from a red one they need all of it, so a failure is still printed in full. The complete capture is unaffected: it is in the review package, which is attached to the run and rebuildable from the public repositories alone, and the package is the artifact of record. The table names each gate's phase because the sequence runs twice, and a list of sixteen rows with every gate appearing twice and nothing to say which is which is a puzzle rather than evidence. The checklist is `Bright-Bots-Initiative/brightboost`'s own, so the reviewer works through the list they already use; the harness ticks build, lint and tests because it ran exactly those on exactly this branch, and leaves i18n, responsiveness and pattern-matching blank because it did not. The note about **Approve and run** is there because GitHub holds workflow runs from an account with no merged contribution, which is every first delivery — and a reviewer who does not know that sees a pull request with no checks and draws the wrong conclusion. |
| D53 | Assigning the machine account to an issue on the product repository queues it. A new `discover --mode assigned` lists them and creates the work items; in triage, an issue assigned to the machine account survives and needs no allowlist label, while one assigned to anybody else stays excluded as B55 always had it. `feedback.yml` runs the sweep every three hours on a weekday (B233). | The allowlist label was the only way in, and **nothing on the product repository carries it** — so weekly discovery found nothing and would have gone on finding nothing. Asking a maintainer to add a label the harness invented is a worse ask than letting them use the verb GitHub already gives them for "this one is yours". Assignment says exactly what the label says, on the ticket itself, in the place they already work, and it is visible to everyone looking at the issue rather than buried in a label list. Making it an inclusion in triage rather than a separate universe keeps one rule: `intern-starter`, `large` and `architecture` still win, because work reserved for a human learning the codebase does not stop being that when somebody assigns a bot to it. The account is derived from `FORK_REPO`'s owner rather than configured again — a fork the account does not own is not one it can push to, so a second key could only ever disagree. No model call: which issues are assigned is a fact. The sweep is idempotent, so running it every three hours costs one request and queues nothing twice. |
| D54 | The delivery pull request names its reviewers in the body as well as requesting them through the API (B234). | `deliver` has always called `request_reviewers` with every trusted handle, and swallowed the refusal. The refusal is the normal case: requesting a reviewer needs push access to the repository, and the machine account is deliberately not a collaborator on the product repository (D30) — that is the whole point of delivering from a fork. So the API request is the path that works if it ever gains access, and the mention is the one that works today. A mention notifies, which is what asking for a review is for. |

**On what the trial runs cost.** `harness propose 1` spent **$2.37** and 22 minutes on a
two-file deletion — 79 cents short of `PER_CALL_CAP_USD`. The bulk of that was the model
searching for a repository that was not there (D37) and then reading one that should not have
been reachable (D36). Both fixes cut the search; the cost of a proposal should be re-measured
on the next live run rather than assumed.

**Review of this branch (2026-09-04).** Reviewing the fixes above found three more defects in
them, all in `propose`'s new lease (D37) and all introduced by moving the stage body into a
helper:

- `_propose_leased` recomputed `entry_state` from `item.state` instead of taking `_enter`'s
  answer. Those two differ for exactly the case `_enter` has a comment about: an item found
  already `proposing` must be returned to `discovered`, because `proposing` is not a state it
  can be returned to. The revert on a failed model call would have been a silent no-op.
- `preflight()` and `acquire()` run *after* `_enter` has moved the item to `proposing`, and
  neither was covered by the revert. A clone failure — a network blip — would have stranded the
  item mid-flight with nothing to resume from.
- `acquire`'s new "could not clear the previous clone" message counted the leftovers with
  `rglob`, which whatever defeated the removal can defeat too; a failure to count would have
  replaced the legible error with a traceback. The count is best-effort now.

Three tests pin them. Also in the review: the "N to go" figure in `harness ledger` gained a
decimal (at 69.6% against a 70% stop, `:.0f` printed "0 to go", which reads as stopped when it
is not), the argv-ceiling message now names which argument to shorten rather than always
blaming the system prompt, and `tests/test_gh.py` — the one file in the tree committed with
CRLF — was normalised so the new `.gitattributes` has nothing left to convert.

**One thing deliberately not changed.** `prettier.py` now demonstrably defines *what counts as
a change*, which makes it as result-defining as the three modules in `verify_pin.PINNED`
(`gates.py`, `packager.py`, `redact.py`). Adding it to the pinned set is a one-line change, but
`PINNED` is frozen by RUN-DECISIONS-D2 §10 and asserted verbatim by a test, so widening it is a
spec decision rather than a defect fix. Flagged here for the next spec revision.

---

## D55–D63 — Delivery 4: asking for work, and an order to the queue

Implemented 2026-09-05 on `feat/delivery-4-labels`. The handoff
([docs/delivery/DELIVERY-4-HANDOFF.md](docs/delivery/DELIVERY-4-HANDOFF.md)) carries the full
argument for each; what follows is what changed in the code and the two places the design was
wrong until the tests said so.

**D55 — the inbox is not the record.** A pinned issue you comment on; the harness replies in-thread
and opens an ordinary work item. The queue is already "one issue, one stage label" and everything is
built on that, so a request that becomes work should become one of those rather than a new species.

One thing the handoff did not anticipate: `keywords.sweep` reads *notifications*, and an account is
not subscribed to an issue it has never touched. On the notification feed alone, the very first
request ever made is the one that silently vanishes — which is precisely the failure the inbox
exists to prevent. The inbox is therefore **polled** on every sweep, and de-duplicated by the same
`ledger.seen(comment_id)` that guards every other command.

**D56 — three label families.** Shipped in the previous change (B264–B268). `via:` now also reaches
the priority queue, which needed a work item to be able to say how it arrived on *both* store
backends — so `via` became a real column on sqlite (an additive `ALTER TABLE`, default
`'requested'`) and is read off the label on GitHub.

**D57 — audits produce a list, not work.** One findings issue, `kind:audit`, and deliberately **no
stage label**: without one the store cannot see it, so an audit issue can never enter the queue.
That is structural, rather than a rule somewhere that remembers to skip it. Findings become work
only through `/harness promote`, and each then goes through the ordinary proposal gate.

**D58 — suggested work, and the green light.** Weekly discovery runs only when nothing anybody asked
for is outstanding, queues at most `SUGGEST_MAX_PER_RUN`, and comments once — ever — on each
unassigned product issue it picked up, saying plainly that it has not started and will not without a
green light.

**D59 — `product_issue` and `inbox` are surfaces of their own.** Neither resolves through the thread
number. The two repositories number independently, so `/harness stop` on brightboost #633 must not
park harness work item #633, and a stray `stage:` label on the inbox must not queue the inbox.

**D60 — levels.** `trust.txt` is `<level> <handle>`; a bare handle is level 1, least privilege on
ambiguity. A digit-first token with a level outside 1–3 refuses the whole line rather than guessing
— a test caught the first version of this registering a handle literally named `9`.

**D61 — I-18, the harness never works on itself.** Refused in four places, each a different way of
arriving: `load_config` (`UPSTREAM_REPO`/`REPO` == `SELF_REPO`), `discover.request` (a pasted link),
`clone._source_repo` (the checkout) and `deliver` (the pull request, before the state check and long
before the push). A system that can rewrite the rules it is governed by has no rules.

**D62 — `--force`.** Level 3 only, recorded in the **ledger** beside the B209 carry rather than on
the item, because it is a scheduling exemption and not a property of the work — the same item forced
on Thursday and left alone on Friday is the same piece of work. That also means neither store needed
a column for it. It lifts the calendar and nothing else, and the reply says so, because a flag named
`force` invites the assumption that it lifts more.

**D63 — one queue for every model call.** `run_model` is now the single admission point: every call
is classed (`answer`, `unblock`, `directed`, `audit`, `suggested`) and admitted before the governor
is consulted. Priority decides what runs *next*; it never decides that something may run which the
governor would have refused, and the order is priority-then-governor so a class-0 `ask` past a usage
stop still does not run.

### Two things this change got wrong first

Both were caught by the existing suite, and both are the same mistake — a new feature quietly
changing an old contract:

- **`harness dispatch` stopped emitting JSON.** Printing the queue after the plan produced two
  documents on one stream, and `dispatch.yml` parses that stream. The queue is now a `queue` key
  *inside* the plan, with `start`, `reason` and `skipped` unchanged and still first.
- **Suggested work was refused on *unobserved* usage.** The subscription signal arrives on the
  headers of a real model call, so a fresh ledger, every tier-0 run and every local run have none.
  Treating unknown as "no headroom" would have silently disabled the discovery route that has worked
  since Delivery 1. Unknown is not zero, but it is not empty either: the two usage stops are what
  guard the allowance, and they are checked on every call regardless.

## D64 — twelve verbs, hyphenated, several to a comment

Implemented 2026-09-09 on `feat/simpler-command-surface`.

**Fifteen verbs became twelve.** Three pairs were merged and one was renamed:

| Gone | Now | Why it was never a real choice |
|---|---|---|
| `fix` | `revise` | They differed only in whether code existed yet — which is exactly what "proposal pull request" versus "delivery pull request" already says |
| `reject` | `stop` | They differed only in which gate you were at, and the thread you are on says which |
| `queue` | `go` | Both mean "proceed with this"; which one applied depended on the item's state, which the person cannot see |
| `usage` | `status` | One name, and it is the word people reach for |

Each of the three pairs named a distinction the **surface already carried**. Asking someone to pick
the right word for a fact the harness could read off the thread gave them a way to be wrong and gave
the harness nothing. The four old names are kept in `keywords.ALIASES` and resolve to the verb they
became, so comments already sitting on open pull requests do not silently stop working.

The merge moved two decisions from the person into the code, and both had to be made honest about
their edges:

- `go` now reads the item's state, and `blocked` is the interesting one: it is reachable from both
  sides of gate 1, so "proceed" points in two directions. A **branch** exists only once `implement`
  has run, which is only after the proposal was merged — so it is the honest test for "this was
  approved once", and it decides between `approved` (resume on the branch) and `discovered` (back in
  the queue). `needs-human`, `merged` and `abandoned` have no useful edge, and are **answered**
  rather than raised: an `IllegalTransition` traceback in a comment reply tells nobody anything.
- `stop` on a terminal item says there is nothing left to stop, for the same reason.

**`/harness-<verb>` parses as well as `/harness <verb>`.** The hyphen makes a command a single
token, which is what makes the next part readable.

**Every `/harness` line in a comment is read, not just the first.** They run top to bottom and each
gets its own answer, each gated on *its own* level — so a level-2 handle sending `status` and `halt`
in one comment gets the first and a refusal for the second. The comment is marked seen **once**,
after parsing: marking per command would make the second command of a two-command comment look like
a replay of the first. A line whose verb is not real is now skipped rather than discarding the
comment, which is what the first-line rule used to do to anyone who made a typo.

**Every reply carries a pointer to the other commands** (`links.reply_pointer`), offered by surface —
`revise`/`rebase`/`stop` on a delivery pull request, `work`/`ask`/`status` on the inbox — plus the
link to [docs/COMMANDS.md](docs/COMMANDS.md). An answer that says only what happened leaves the
reader knowing one command and not that there are eleven others.

### What this got wrong first

- **`go` requeued from `needs-human` and `abandoned`.** Neither has an edge to `discovered`, so the
  documented gesture would have raised after the reply had already been composed.
- **A failed command was recorded and never said.** The sweep loop caught `HarnessError`, wrote it
  to stdout, and replied nothing — which from the commenter's side is indistinguishable from the
  harness being asleep, the one failure this whole surface exists to avoid. The loop is now
  `run_command`, split out so that behaviour is driven by a test rather than read off the source.
  (The test that guarded it *was* reading the source with `inspect.getsource`, and passed happily
  for a loop that caught the exception and did nothing with it.)

### What the adversarial pass then found

Two independent reviews of the branch, neither of which could use "the tests pass" as evidence.
Between them they found eight things the merge broke and the suite did not see — because the tests
written alongside the merge exercised the surfaces the merge was thinking about. In severity order:

1. **`revise` routed on the surface, not the state.** `stage:needs-human` is visible only on the
   harness issue, and `docs/OPERATIONS.md`, `docs/USING.md` and `stages/revise.py` all tell the
   operator to type the verb *there* — which fell through to a re-propose and raised, because
   `needs-human → proposing` is not a legal edge. The documented recovery answered with a
   traceback. It routes on the item's state now; the surface was only ever standing in for it.
2. **`go` could walk an ordinary proposal past gate 1.** `go` is B262's green light for work
   *nobody asked for*; for everything else the gate is the merge. Since `queue` now resolves to
   `go`, and `queue` on a proposal used to mean the *opposite*, this was also a live inversion.
   `go` at `proposed` is now restricted to `via:suggested` and says so otherwise.
3. **`reject` silently dropped from level 3 to level 2.** Resolving the alias before the level gate
   handed every maintainer a terminal verb as a side effect of a rename. The gate reads the **typed**
   word's level now, and `stop`'s own target is level-dependent: level 2 parks (`blocked`,
   reversible), level 3 ends it. That is the distinction `reject`'s name used to carry.
4. **`go` on a blocked item restarted it from scratch**, orphaning the branch and buying a second
   proposal. See the branch test above.
5. **`go` on the inbox lost the queue report** — a capability quietly lost to a rename, on the one
   thread the sweep polls unconditionally.
6. **A fenced code block parsed as commands.** `docs/COMMANDS.md` ships a three-command block under
   the words "that is a normal thing to send"; pasting it to explain the syntax would have run all
   three. Fences are stripped before parsing, and one comment now yields at most ten commands.
7. **GitHub's own rate ceiling did not stop the batch.** `RateCeilingReached` is a different
   ceiling from the model's `RateLimited` and arrived as an ordinary error, so the loop ground on
   posting replies that were themselves refused and swallowed — consuming any `/harness resume`
   behind the comment that tripped it. Both ceilings stop the batch now.
8. **The thread recorded the resolved verb, not the typed one.** The issue thread is the log, and
   per (3) which word was used decides what the commenter was allowed to do.

Nine tests were added, one per finding plus the green-light case beside its new guard, so none of
them can come back quietly.

### And what the first live run found

Running the sweep against the live repository — the first time it had ever got that far — the
notifications call returned **403, "Missing the `notifications` scope."** The machine PAT then held
`public_repo` and nothing else, so the token as configured was refused there. (It has since gained
`notifications`, the fix below, and `workflow`, D67.) The exception
came out of `sweep` and took the **inbox commands it had already collected** with it: the surface
used by people who have read no documentation, lost because a feed nobody sees was refused.

`sweep` now keeps what the inbox gave it and logs the refusal, and does **not** advance the
notification cursor over a feed that never arrived — advancing it would skip that window for good.
`harness doctor` reports the missing scope as a warning, so the gap is visible before it is a
silence. Adding `notifications` to the PAT closes it; without that, cold product-issue mentions are
the only thing missed.

## D65 — the second answer, and the run that never started

Implemented 2026-09-09 on `feat/instant-ack-and-ops-retries`. Three changes, all about the same
thing from different angles: **a thread cannot tell thinking from broken, and people act on the
second reading.**

**B293 — `ack.yml`, an answer within seconds.** A `/harness` comment on the harness repository
already wakes `feedback.yml` on the event, but that workflow installs the package, runs `doctor`,
syncs the fork and takes the `harness-ledger` lock — so its first useful output is minutes away, and
can be much further if an `implement` run holds the lock. For all of those minutes the thread shows
nothing. Now it shows a 👀 reaction immediately, and — when anything asked for takes more than a
moment — one short comment naming each verb, what it is doing, and roughly how long.

Four things make it worth having rather than noise:

- **It takes no lock and installs nothing.** Not a first step of `feedback.yml`, because that job's
  latency is exactly the problem. `harness-ledger` serialises every workflow that writes state, and
  an acknowledgement that queues behind a twenty-minute implement run is not an acknowledgement. The
  harness is stdlib-only, so `PYTHONPATH=.` replaces a twenty-second `pip install`.
- **It reuses the parser and the trust gate**, via a `harness ack` subcommand rather than a script
  in the workflow. A second copy of either would drift, and the way that surfaces is somebody being
  told they were heard when they were not — on a public repository. A fenced block, an unknown verb,
  an untrusted commenter or a wrong `author_association` all produce silence.
- **Fast verbs get the reaction and no comment.** An acknowledgement that lands two seconds before
  the answer has told the reader nothing and cost them a notification.
- **It cannot spend and cannot fail the run.** Tier 0, fake backend, no secret beyond
  `GITHUB_TOKEN`, and exit 0 on every path including a missing file.

The comment body reaches Python through the environment, never through `${{ }}` inside a `run:`
block: that substitution happens before bash sees the line, so a backtick in a stranger's comment
would otherwise be a command on the runner.

**B294 — `watchdog.yml`, for the run that never started.** A run that *fails* files a `kind:ops`
issue and is retried. A run that never *starts* does neither, because nothing fired, and the only
symptom is that comments on the product repository go unanswered — indistinguishable from nobody
having commented. Every four hours, on a weekday, if no `feedback` run of any kind has started in
six, the watchdog dispatches one.

Three decisions inside it are worth the words:

- **A failed run counts as proof of life.** It measures the *absence* of runs. `ops.yml` owns
  failures, and two things re-dispatching the same workflow would fight.
- **It does not sweep at weekends.** `feedback`'s cron is `1-5` deliberately and the documentation
  says a Friday-evening comment waits for Monday. A watchdog that dispatched all weekend would
  change that policy while looking like a bug fix — which is how policy changes get in.
- **An issue only for a stoppage, not a gap.** One miss is a hiccup the dispatch already fixed, and
  an issue per hiccup is how an ops list becomes noise nobody reads. The evidence for a stoppage is
  different and specific: no run triggered by `schedule` for twelve hours. The watchdog's own
  dispatches reset the first clock and never that one. The issue names both causes, because only one
  self-corrects — GitHub disables schedules after 60 days without a push, and waiting for that to
  recover is waiting forever.

**Retries go from one to three attempts.** One was not enough: the failures that actually happen
here are network and registry blips, and those cluster, so a single retry lands inside the same bad
minute often enough to be no retry at all. The cap now counts `run_attempt` rather than a `retried`
label on the ops issue — the label capped retries *per issue*, so one transient failure in August
spent the retry for every later failure of that workflow until a human closed the issue, and nobody
did. What did not change: a failure inside a model call or a gate still never retries at all. Those
cost money and fail for reasons a retry cannot fix, so more attempts would only buy more spend on
the same wrong answer.

### The harness was already waking itself

Found on the live inbox while building the above, not by a test: **four full `feedback` runs in
twenty-seven seconds**, each triggered by a reply the harness had just posted.

The cause is D64's reply pointer meeting `feedback.yml`'s trigger. Every reply now ends with
"**You can also say:** `/harness status` · …", and the workflow wakes on
`contains(github.event.comment.body, '/harness')`. So each reply woke another run — checkout,
install, doctor, sync-fork, sweep — which correctly found nothing, because `commands_from` skips
the machine account, and correctly posted nothing, having taken the `harness-ledger` lock to do it.
Depth one rather than a loop, only because a run that posts nothing triggers nothing.

`ack.yml` would have made it worse in the obvious way and in one that is worse than obvious: the
acknowledgement's entire content is a *list* of `/harness` commands.

**The fix is a marker, not a login test.** `gh.comment` appends `<!-- bright-bots-harness -->` to
every body it posts, and both comment-driven workflows skip comments carrying it. A marker because
a workflow `if:` cannot read `.harness/config.json` to learn the machine account's name, and
because that account is an ordinary user rather than the `Bot` type GitHub would filter for us. An
HTML comment renders as nothing, so it costs the reader nothing.

Applied at the **transport** rather than at the three call sites, so a fourth added later cannot
forget it — and because the site that most needed it, `deliver.handoff`, builds its body from a
template and never touches `links`. `ack.yml` posts through `github-script` rather than through
`gh.comment`, so `harness ack` marks its own output explicitly.

`FakeGh.comment` marks too. A fake that did not would let the marker be deleted with a green
suite, which is the failure mode this repository keeps finding.

### What the adversarial pass on D65 found

Two independent reviews. Eight things, and the two worst were mine to have caught.

**The retry raise could have paid for a model call twice.** `reRunWorkflowRunFailedJobs` re-runs
the whole **job**, and all three spending workflows are one job — so "did the failing *step*
spend?" was the wrong question. `Commit state/ledger.json` runs *after* the spend and matches no
model-or-gate pattern, so a denial list let it through: a lost push to `harness-state` meant a
retry that reloaded the **pre-run** ledger, found the same comment unseen (B135's guard lives in
`seen_comment_ids`, which reaches the branch only through that push), and paid for the same model
call again — invisibly to `WEEKLY_CAP_USD`, because the ledger recording the first attempt was
exactly what failed to save. Raising the cap from one to three would have made it twice.

Now an **allow-list** of steps known to run before anything is spent, drawn from the real step
names, with a test asserting that every step of all three workflows is classified one way or the
other. A step added later fails the suite until somebody decides, rather than defaulting to
"re-run a job that spends".

**A comment-driven workflow checked out the pull request's code.** On
`pull_request_review_comment` GitHub sets `GITHUB_REF` to `refs/pull/N/merge`, so a bare
`actions/checkout` lands the *pull request's* tree — and both `ack.yml` and (pre-existing on main)
`feedback.yml` then run that tree's `harness/` code. On a public repository that is a stranger's
Python on the runner, and in `feedback.yml` it is a stranger's Python beside the machine account's
PAT. Both now pin `ref` to the default branch. This one was live before this branch existed.

The rest:

- **A skipped run read as proof of life.** `status: completed` includes a run whose only job was
  skipped, and `feedback.yml` creates one for *every* comment on the repository. So any comment
  traffic inside a six-hour window convinced the watchdog the schedule was fine — precisely when
  somebody is looking at it because it is not. Worse, the dead-schedule check sat *behind* that
  gate, so it never ran. It is asked on every tick now, and skipped, cancelled, stale and
  startup-failure runs no longer count.
- **A queued dispatch was invisible**, so the watchdog would dispatch over its own pending run,
  cancel it, and then read the cancelled corpse as success — keeping the sweep it was trying to
  cause from ever running.
- **`DEAD_HOURS` did not know about the weekend.** Friday 21:41 to Monday 00:41 is fifty-one hours
  with nothing scheduled in them, so a twelve-hour threshold filed a false stoppage on any Monday
  where the 00:41 run was late. Counted in weekday *slots* now.
- **A quote-reply is a person, not the harness.** GitHub's quote-reply copies the source comment's
  raw markdown, HTML comments included — so the marker introduced above dropped the most natural
  way to answer the harness. The quoted form (`> <!-- ... -->`) is what tells them apart.
- **`ack` promised work a halted harness will not do**, and offered `/harness status` as the way to
  find out, which the same halt refuses. It checks both switches now and says which one is on.
- **`ack` gated on membership, not on the verb's level**, so a level-1 asker would have been
  promised twenty minutes of audit and then denied it in public.
- **The per-comment concurrency key throttled nothing** — unique every time, so sixty comments
  meant sixty parallel jobs, filling the account's allowance and queueing the spending workflows
  behind the acknowledgement mechanism. Keyed per commenter now.
- **`Say it` could turn somebody's comment red.** `createComment` 403s on a locked issue, a fork
  pull request's read-only token, and the secondary content-creation limit.

And one claim withdrawn: the watchdog **cannot** report GitHub's 60-day disablement, because that
rule disables every schedule on the repository including the watchdog itself. The documentation
said it would. The signal that actually survives is the weekly heartbeat comment going missing,
which is what B144 built it for.


## D66 / B295 — the subscription is the metric, not the dollars

Implemented 2026-09-09 on `feat/usage-not-spend`, prompted by the operator reading a live status
report and saying the quiet part: *spend is probably the wrong metric, you should be using usage.*

They were right, and the first live run had already shown it without anyone noticing. One model
call: **$0.28** estimated, and the seven-day subscription window at **18%**. By the dollar figure
the harness had used 0.08% of its allowance. By the number that actually governs, nearly a fifth of
the week.

**Why the dollars were never the constraint.** The `$` figures are an *estimate of API-equivalent
cost*, derived from token counts. Nobody bills them. What runs out is the utilization of two
windows the API reports on the headers of every call — five-hour and seven-day — and the harness
has watched those since Delivery 3 (D31/D32). It simply never *led* with them: every report opened
with a dollar total, and the two usage lines were a footnote under it.

**And the allowance is shared.** Most of that 18% was not the harness at all — it is the same
subscription the operator uses for their own work, so the number moves while the harness is asleep.
A dollar total the harness accumulates itself cannot show that, and it is the single most important
fact about reading these figures.

What changed:

- **`links.usage_headline`** renders the comment reply: utilization first, the **headroom left**
  rather than only the amount gone ("18% used, 72 points before the 90% stop"), and a line saying
  the allowance is shared. It is *a* renderer, not the only one — `harness status` and `harness
  ledger` go through `_usage_lines`, `harness dispatch` through `_usage_suffix`, and the heartbeat
  builds its own in JavaScript. What they share is the **guarded read**, which is the part that
  can drift and did (see below).
- **Unmeasured is said as unmeasured**, never as zero. None is not zero: the signal rides on the
  headers of a real model call, so a fresh ledger, every tier-0 run and every local run have none.
  A headline reading "0% used" would be the most confident possible way of being wrong.
- **The dollar line survives in smaller print, labelled.** It is kept for exactly three things: a
  sense of scale, the `--max-budget-usd` flag the runner really does enforce on a single call, and
  being the only bound that exists before a real call has ever been made. B114 is unchanged — no
  decision may *depend* on the signal being present.
- **`harness status` stopped printing two different percentages as if they were one thing.** The
  block it called `budget:` is the harness's own accounting in budget units, which is not the
  subscription; both now say which they are.
- **An audit is gated on usage** (`AUDIT_MIN_HEADROOM_PCT`, 75). It is the longest single call the
  harness makes — twenty minutes — and it was bounded only by `AUDIT_CAP_USD`, which on a
  subscription is a fiction. Starting a twenty-minute read with a fifth of the week left is how the
  operator finds the allowance gone the next time *they* need it. The floor is looser than
  `SUGGEST_MIN_HEADROOM_PCT` (50) on purpose: somebody asked for the audit, and nobody asked for
  suggested work.
- **`reserve` keeps its exact token** — B122 pins it and `implement.yml` echoes it — but now
  carries the subscription reading beside it. An operator seeing "reserve" alone cannot tell
  whether the thing that actually runs out is anywhere near its limit, and those are very different
  problems. `_usage_suffix` is empty until both windows have been observed, so the bare token
  survives everywhere nothing has been measured.

### What the adversarial pass on D66 found

Six things, and the first two are the same root cause — a guard I did not know was load-bearing.

**`roll_window` deliberately leaves the last observation in place**, and `Ledger._utilization`'s
staleness check is the only thing that makes that safe: an observation older than `period_start`
is *last* window's, so it reports `None`. Both of the readers this change leaned on had grown
their own raw read of `window["usage"]` and skipped it.

- **The status comment would have reported last week's figure as this week's.** A Friday reading
  of 88%, a Monday roll, and `/harness status` says "88% used, 2 points before the 90% stop" on a
  week nothing has been spent in — while `harness dispatch`, reading through the ledger, correctly
  says nothing is stopped. The docstring claiming the two "cannot disagree" was written in the same
  change that made them.
- **Worse for the new audit gate**, because `headroom_pct` has the same raw read and B295 made it
  the *sole* bound on `/harness audit`. Every audit refused for an entire fresh window — failing
  closed at exactly the moment there is the most room, and nothing clears it until some other stage
  happens to make a real model call.

Both readers go through the ledger's accessors now, with the same rule applied by hand for a
ledger-shaped object that lacks them, so a test double cannot be more permissive than the real
thing. **Which is exactly how this was missed:** every test in `tests/test_usage_not_spend.py`
built its ledger as a `SimpleNamespace`, so the suite structurally could not reach the guard. The
tests build real `Ledger` objects now, and one of them rolls a window.

The rest:

- **The audit gate cloned first and refused second.** `run_model` is the last line of defence, but
  by the time it says no, a full fresh clone of the product repository has been made. Every other
  stage checks at its own entry for this reason; this gate was the one arriving late.
- **`ack` promised twenty minutes of audit the sweep was about to decline** — the exact failure
  `cmd_ack` exists to avoid, performed in public. It consults the gate now, and `/harness status`
  reports it beside the suggestion gate, so a maintainer whose audit was declined can find out why
  without reading the source.
- **A declined call was answered as "that did not work".** D3 is explicit that a usage stop is a
  normal outcome, like a closed run window. `BudgetExhausted` now answers "Not now — …", because
  dressing the governor doing its job as a failure teaches people to read a working system as a
  broken one.
- **"0 points before the stop" while nothing was stopped.** `{:.0f}` of 0.4 is "0", and the branch
  was chosen on the unrounded value — so the comment claimed a stop the CLI, rendering one decimal,
  correctly showed 0.4 short of. Under a point now says so.
- **`docs/USING.md` quoted both renamed blocks verbatim**, and the weekly heartbeat — the one
  report that goes out unprompted, and B144's alarm channel — still led with dollars and quoted the
  raw cap rather than the cap less the reserve. Both fixed; the heartbeat drops a stale reading for
  the same reason the accessors do.


## D67 / B296–B315 — the token carries `workflow`; the harness guards `.github/` itself

Implemented 2026-09-11 on `fix/workflow-scope-hardening`, from a design handed off on 2026-09-10
(untracked, in `.handoffs/`).

**The grant.** `harness sync-fork` failed on every run from the moment brightboost changed
`.github/workflows/ci-cd.yml`: GitHub will not let a token without the classic `workflow` scope
create or update a workflow file, and fast-forwarding the fork past upstream's own commit is exactly
that. The fork sat five commits behind with nothing unique to it, and `implement.yml` and
`discover.yml` hard-fail on a stale fork — correctly — so nothing could be delivered. Of the three
ways out (fast-forward by hand whenever upstream touches CI, re-fork, grant the scope) the operator
chose the scope, told plainly that it gives up half of I-15. The machine PAT now carries
`public_repo`, `notifications` and `workflow`; the operator confirmed it on 2026-09-11, and
sync-fork has succeeded on every run since 2026-09-10 16:40Z. The plan was harden first, then
rotate. The rotation came first, so this lands into a window in which the credential was already
permissive and the code had not caught up.

**What that took away.** I-15 was "enforced twice by two things that fail differently": the missing
scope, a capability guarantee, and B64. The first is gone, and a survey found the second thinner
than it read:

- **Hole A.** `handoff` committed `git add -A` over the whole tree and pushed it with no B64 check
  at all. A usage stop landing while the model had a workflow open published it; the missing scope
  was the only thing in the way.
- **Hole B.** `revise` read its diff base *after* the model call. The model holds Bash; a commit it
  made itself became the tip, the diff came back empty, and every arm of B64 passed a change nobody
  had looked at.
- B105's static test spelled the fork `brightboost-harness/brightboost`, which does not exist, and
  passed unconditionally.
- Nothing read the token's real scopes.
- `local/watchdog-bb.ps1`, local mode's only publisher, pushes with the same PAT and checked no path.

**What replaces it.** One predicate, moved to the boundary.

1. **One path set, widened (B296).** `clone.PROTECTED_PUSH_PATHS = ("/.github/",)`, with
   `normalise_repo_path` (moved from `implement._normalise`, now also stripping git's C-quote) and
   `protected_paths_in`. All of `.github/`, not only workflows: composite actions, `dependabot.yml`
   and `CODEOWNERS` steer CI and review too, and GitHub's scope only ever gated workflow files. B64
   reads it — `implement.FORBIDDEN_DIFF_PATHS` is the same object. The operator accepted the cost:
   the harness can never be asked to change anything under `.github/` on brightboost; it refuses,
   and a person carries it.
2. **Commit-authorship-scoped, not diff-range-scoped (B297).** `clone.walk_harness_commits` walks
   the branch from its tip (`git log --first-parent --diff-merges=first-parent --name-only
   --no-renames`) and stops at the first commit a harness email did not author. It asks which
   commits the harness wrote and what is in them — not what the branch changes against a recorded
   base. **Why:** the first design diffed from `lease.base_sha`, and `deliver` pushes after
   rebasing onto upstream, so that diff reports every path upstream changed since — `ci-cd.yml`,
   the very file the scope was granted for. It would have refused every delivery from the day of
   the grant, and the suite would have shipped it green, because no deliver test moved upstream
   before the rebase (B299 and B300 do now). A rebase rewrites the committer and keeps the author,
   so upstream's commits keep upstream's authors and the walk stops at the first of them.
3. **The backstop (B298).** `gh.push_branch` runs the walk before every push, through the injected
   runner, under `--dry-run` too, and refuses when git cannot answer. It takes no `base`: nothing
   for a caller to get wrong. `push_ref` stays outside it on purpose — its one caller relays
   upstream's own `main` to the fork, fast-forward only.
4. **Hole A closed (B301, B315).** `deliver._push_handoff` withholds the push — only the push;
   HANDOFF.md and the comment still happen — when the branch carries a `.github/` path by the walk
   *or* by the author-blind check against the fork's `main`. When either cannot be answered, it
   withholds. The item is then blocked, not carried, and the withheld commits are kept as
   `WITHHELD.patch` beside the note (B315), because in Actions mode the clone does not outlive the
   run and `continue` would find no branch to resume.
5. **Hole B closed (B302, B303).** `revise` fixes its base before the model runs. A resumed item is
   judged from its fork point, because nothing it carries was ever checked. And it walks the branch
   before shipping, so a `.github/` commit the branch already carries blocks the item with the
   clone kept, rather than failing at the push.
6. **The local publisher (B304).** `Get-UnpublishablePaths` in the watchdog runs the same `git log`
   with the same prefix, emails and cap, before its push. `local/preflight.py` and
   `tests/test_local_mode.py` hold the two together, and one test runs the PowerShell function over
   the same fixture branch as the Python walk.
7. **Doctor reads the scopes (B305).** `X-OAuth-Scopes` from an unconditional `GET /user`, reported
   on every run against the expected set: `public_repo`, `notifications`, `workflow`. A missing
   one, any other, or no header at all (a fine-grained token) is a **warning, never a problem**: a
   problem exits 3 and stops the fleet (#27).
8. **B105 de-vacuumed (B306):** the fork slug comes from `.harness/config.json`. **The delivery PR
   body and the prompts say what is true (B307).** **The I-15 invariant is widened (B308)** to one
   path set, defined in `clone.py`, read by implement, gh, deliver and revise.
9. **The spoof and clone-ref substitution, closed (B312, B313, B314).** The walk trusts the author
   field, and a commit the model made under any name it likes stops the walk before it. So the two
   pushes that send a branch — `deliver` and the handoff — do not rely on the walk alone: each also
   reads every commit the push would send that upstream does not already hold, whoever authored it,
   and refuses any `.github/` path (B313). `deliver` reads from `FETCH_HEAD`, the commit `_rebase`
   just put the branch on; the handoff fetches the fork's `main` at check time (B314), so a model
   that ran `git update-ref refs/remotes/origin/main HEAD` onto its own commit cannot move the
   anchor the check reads. And every guard-side git read — the walk, B64's diffs, the author-blind
   checks — begins with `git --no-replace-objects -c core.commitGraph=false` and refuses a clone
   carrying a `refs/replace/` ref, a grafts file or a shallow history (B312): those show git a
   history the clone's files claim but its objects do not hold, while a push sends the real objects
   either way.

**Where this departs from the handoff, and why.**

- **The cap refuses.** The handoff said to record a walk that hits its 100-commit cap. A guard that
  is now the only layer should not publish commits it has not read, and no real branch comes near
  100.
- **Author spoofing, and clone-ref substitution.** The walk's stop rule trusts the author field,
  and the model can commit under any name — plausibly, since a harness clone has no `user.email`
  and the model must pick one. So every check that follows a model call is author-blind: implement's
  B64 diffs from a fixed base (unchanged), revise's from the tip it gave the model, a resumed item's
  from its fork point, and — the point on which this diverges from the handoff — both pushes that
  send a branch add an author-blind read of every commit over an anchor the model cannot move (B313,
  B314, item 9 above). An earlier draft conceded that "a model that rewrote the clone's refs could
  still mislead the handoff's diff … is beyond what a check inside the clone can promise"; an
  adversarial pass showed that was not so. A `refs/replace/` entry, a grafts file or a shallow file
  each rewrite what git *reads* without changing what a push *sends*; the checks now read with
  `--no-replace-objects -c core.commitGraph=false` and refuse a clone that carries any of the three
  (B312), and the handoff's anchor is fetched from the fork at check time rather than read from a
  ref in the clone. What remains outside any in-clone check is the transport itself — a model that
  rewrote `.git/config` to point `origin` elsewhere, or replaced the `git` binary — which is a
  property of the runner, not of a diff.
- **`doctor` never "warns that `workflow` is absent"** (handoff §9.3): the rotation came first. The
  check proves itself by reporting all three scopes and warning on none.
- **`packager.py` and `prompts/` are corrected here**, not left to the documentation pass. The PR
  body told every brightboost reviewer the token had no `workflow` scope, and the prompts told the
  model the rule was `.github/workflows/` when the guard blocks all of `.github/` — a prompt
  narrower than its check wastes a run. `.harness/PIN` is rewritten for both. F4's
  `FULLSEND_FORBIDDEN_PATHS` keeps `.github/workflows/`: it routes a proposal to the parallel
  prompt and is not a safety check.

**The documentation pass (B309–B311).** Every live statement that the token lacks `workflow`,
that I-15 is enforced by GitHub or "twice", or that a reviewer need not check for a `.github/`
change now says what is true:

- `docs/SAFETY.md`: I-15 names the shared predicate and says plainly that it is enforced once,
  by the harness's own code, with doctor's scope report in place of the capability half. The
  scope table, the operations table and the never-ask list follow.
- `docs/USING.md` and `docs/PACKAGE-FORMAT.md`: a `.github/` change moves out of "you need not
  check" and into "still check".
- `docs/OPERATIONS.md`: the rotation runbook names all three scopes and says that leaving
  `workflow` off only stalls the fork.
- Also corrected: `README.md`, `docs/PROPOSALS.md`, `.env.example` and the `continue-on-error`
  rationale in `feedback.yml`.
- `harness/identity.py` and the `HUMAN.md` it generates (B309). `HUMAN.md` was re-rendered from
  the statuses it already recorded, with no network call, so only the scope lines changed.
- The delivery PR body (B307) states only the check that is enforced, the refusal to push.
  "Rejected before it is committed" overclaimed: a handoff commits interrupted work and
  withholds only the push. `prompts/system.md` changed with it, and `.harness/PIN` is rewritten.

B310 is the drift guard. No live document may say the token lacks a scope doctor expects,
credit GitHub with I-15, or put `.github/` in a list a reviewer need not check. Its matcher is
tested against the sentences corrected here, so it cannot pass vacuously the way B105 did. B311
holds every passage that says which scopes to grant to exactly `EXPECTED_TOKEN_SCOPES`.

**The adversarial pass (B312–B315).** Three independent reviews of the branch found the guard
thinner than it read, and this closes each real finding:

- The walk and B64's diffs ran plain `git diff`/`git log`, which honour `refs/replace/` and
  the commit-graph and grafts caches — files the model can write with Bash to show a check a
  history the push does not send. Every guard-side read now uses `clone.GUARD_GIT`
  (`--no-replace-objects -c core.commitGraph=false`) and refuses a clone carrying a
  `refs/replace/` ref, a grafts file or a shallow history (`clone.substituted_history`, B312),
  in Python and in the watchdog.
- The walk stops at the first non-harness author, so a `.github/` commit the model signs as
  someone else, or one the model committed itself, would pass the push guard. `deliver` and the
  handoff each add an author-blind read of the commits the push would send that upstream does
  not hold (`clone.protected_paths_above`, B313), anchored on a ref the model cannot move — the
  rebase's `FETCH_HEAD` for deliver, the fork's `main` fetched at check time for the handoff
  (B314). The handoff's earlier anchor, `refs/remotes/origin/main`, was a ref in the clone the
  model could move onto its own commit.
- A withheld handoff previously parked the item `approved` with a carry and told a person to
  resume it with `continue`, but in Actions mode the clone does not survive the run and the
  branch never reached the fork, so the resume would fail and the evidence was gone. A withheld
  handoff now blocks the item and writes the withheld commits as `WITHHELD.patch` in
  `runs/item-<n>/`, which the run artifact keeps (B315).
- B105 read the fork by its literal slug, which no workflow spells — they reach the fork
  through the `FORK_REPO` repository variable — so the scan still inspected no real line even
  after B306. It now counts that variable as naming the fork and asserts it saw at least one
  line that does.

**Frozen documents, read as amended.** `docs/delivery/` is not edited in place. Read these as
amended by D67: in `DELIVERY-2-HANDOFF.md`, §5.2 ("one classic PAT, scope `public_repo` only"),
the I-15 row of the invariant table ("the token has no `workflow` scope — GitHub rejects it")
and human prerequisite 5; in `DELIVERY-2-REVIEW.md`, R3.8 ("inspect the PAT scopes") and R5.3
("classic, `public_repo` only; no `workflow`"), both of which `harness doctor`'s scope report
now verifies, with R3.8's tests widened to B298–B301; and `DELIVERY-4-HANDOFF.md`'s summary of
I-15 as "no `workflow` scope". The classic token carries `public_repo`, `notifications` and
`workflow`, and I-15 is the harness's own check.

## D68 / B320–B331 — vouch for an account, not a name

Decided by the operator's delegate; implemented 2026-09-11 on
`feat/vouched-trust-and-maintainer-docs`. B320–B339 are this branch's numbers.

**The problem.** Nathan — GitHub login `BrightBoost-Tech`, numeric user id **193453438**
(`GET /users/BrightBoost-Tech`) — maintains `Bright-Bots-Initiative/brightboost` and is level 2 in
`.harness/trust.txt`. B131 requires a level **and** an `author_association` of OWNER, MEMBER or
COLLABORATOR, and he had that association nowhere it mattered:

- **Here**, he is deliberately not a collaborator. D30 stands: a write collaborator on a
  personal-account repository can push a branch whose workflow runs with the Claude OAuth token
  and the bot PAT in scope. Every comment of his on this repository was read, denied and ignored.
- **On brightboost**, his organisation membership is private, so GitHub reports him as
  CONTRIBUTOR: all 65 of his recent comments read that way. He does have push access there
  (`GET /repos/Bright-Bots-Initiative/brightboost/assignees/BrightBoost-Tech` → 204), but the
  association does not show it. So his commands there, gate-2 steering on delivery PRs included,
  were very likely denied too, and revise's review filter kept his review feedback from the model.
  The trust file's comment said GitHub reports him as a member there, and no read we can make
  confirms it.

**The decision.** Keep D30, so he still has no access to this repository, and let a trust line
**vouch** for one exact account:

```
2 BrightBoost-Tech vouch:193453438
```

A comment passes the association half of the gate whatever its `author_association`, on every
surface, when its `user.login` matches the handle **and** its `user.id` equals the vouched id.

This isn't a weakening. The association was guarding a *name*: a login can be renamed away and
claimed by somebody else, and a per-repository association was what said "this is still the person
the line meant". An account id is immutable and never reused, so it says the same thing more
exactly. A vouched handle held by any other account is **refused**, even when that account is an
OWNER, which is stricter than B131 was.

**What doesn't change:**
- The level still caps the verbs. A vouched level-2 handle is refused `halt`, `resume` and `reject`
  with "needs level 3; @BrightBoost-Tech is level 2", and a `--force` from it is dropped with the
  same explanation.
- A line without `vouch:` is B131 exactly.
- `Identity.trust_file_ready()`'s placeholder rule is untouched.

**The rules:**
- **The token.** It is `vouch:<positive integer>`, at most one per line, and case-insensitive. A
  token that starts with `vouch` but isn't exactly that refuses the **whole line**: `vouch:`,
  `vouch:12x`, `vouch=…`, `vouch:0`, two vouches, or a vouch with no handle. The line grants
  nothing and is recorded in `Trust.malformed`, and doctor names it the way B269 names a bad
  level. Read as "no vouch", the line would still grant level 2 to anyone GitHub calls a member,
  which isn't what its author meant either.
- **A vouched handle's lines must agree (B331).** Two lines vouching for *different* ids, or a
  vouched line beside a bare line for the same handle, refuse **every** line naming it; they go
  to `Trust.malformed` and `Trust.conflicted`, and doctor names each as disagreeing about which
  account the handle is. Merging them the way levels merge would grant what neither line does:
  `3 x` beside `2 x vouch:1` gave account 1 level 3 with no association, the level from one line
  and the waiver from the other. The same vouch on several lines is one account and still takes
  the highest level.
- **A missing or garbled id.** For a vouched handle, an absent, zero, non-numeric or boolean
  `user.id` is unknown, and unknown never matches.
- **One gate.** `trust.comment_authorised(comment, trusted)` reads `user.login`, `user.id` and
  `author_association` from a REST payload and calls `is_authorised`, which gained a `user_id`
  keyword. The sweep (`keywords.authorise`), `harness ack` and revise's review filter all go
  through it. `tests/test_trust_vouch.py` B330 fails the build if any other module reads
  `author_association` or calls `is_authorised` directly, so the rule can't be applied in one
  place and forgotten in another.
- **`ack.yml`** passes `github.event.comment.user.id` to `harness ack --actor-id` through the env
  block. An ack run with no id, as from the previous workflow, fails closed: it stays silent for a
  vouched handle.
- **`harness doctor`** lists every vouch and drops vouched handles from the "no access" warning,
  since having no access here is the point. When a client is available it checks each id against
  `GET /users/<login>`. A different id, or a 404 (renamed or deleted), is a **warning** and never a
  problem: a problem exits 3 and gates every spending workflow (#27), and a stale vouch stops one
  person's comments, which it is already doing safely. A read that fails (the rate ceiling, the
  network) says nothing.

**A defect this turned up.** Revise's `_review_feedback` flattened `ctx.trusted` into a frozenset
of lower-cased handles before filtering. That dropped the levels, which was harmless because a set
grants level 1 and that is what the filter asks for. It would also have dropped every vouch: this
was the exact place the rule could be taught to the sweep and forgotten. It passes the `Trust`
itself now, and B328 checks it through the stage.

**Two limits, recorded here and not fixed:**
- **D53's "assign the bot" gesture only reaches threads the bot is already in.** `jgoetzmann-bot`
  can't be assigned on a brightboost issue it hasn't commented on:
  `GET /repos/Bright-Bots-Initiative/brightboost/assignees/jgoetzmann-bot` → 404, because GitHub
  assigns only collaborators, organisation members and the thread's own participants.
- **Plain PR review summaries aren't read by the sweep.** `keywords.sweep` reads issue comments
  and inline review comments only (`keywords.py`, the `read()` helper in `sweep`), so a `/harness`
  line typed into a review's summary box is never seen as a command. Revise does read review
  bodies as feedback, through the same gate.

**What the adversarial review changed.** The mixed-line escalation above (B331) was found in
review, not in the first cut. So were five places where the maintainer docs promised a gesture a
vouched maintainer without access cannot make. FOR-MAINTAINERS told him to merge or close the
gate-1 pull request, to commit `.harness/HALT` "without permission", to assign the bot on any
issue, to put commands "in a normal review comment", and that `reject` means `stop`. The merge,
the commit and `halt` are the operator's under D30. Assignment is limited as above. The summary
box is not read. A typed `reject` is level 3. Each page now says so, and says what he can do
instead: `/harness revise`, and `/harness stop`, which at level 2 parks even a proposal, since
`proposed` has a `blocked` edge. COMMANDS' per-repository paragraph now applies only to unvouched
lines, and OPERATIONS' latency example matches the cron (21:41 Friday, 00:41 Monday).

**Found while bringing the maintainer page to 2026-09-11, recorded and not fixed:**
- **`go` on a suggestion does not replace the merge.** It moves an unmerged `via:suggested`
  proposal to `approved`, but implement reads the work package from `runs/` or from the merged
  `proposals/<id>-*.md` (D46), and on Actions only the second can exist. The next in-window
  build clones, installs and runs the baseline gates, then raises in `_read_spec` with the item
  already `implementing`: a red run, repeated after B147's reconciliation, until the operator
  merges. That build is not only `implement.yml`'s: `feedback.yml`'s "Reconcile" step runs
  `harness run` with no `--item`, which inside the window builds **every** approved item in one
  loop, so the red run recurs on the three-hourly sweep too — and, since the loop catches only
  usage stops, it ends the loop for the approved items after it. A sixth member of the `runs/`
  family. After the merge `go` is useless as well: `implement.yml`'s `harness approve` has already
  approved the item, green light or none. So the docs tell a maintainer not to `go` a suggestion,
  and give the yes as a plain comment and the no as `/harness stop`.
- **The green-light comment's "Assign me" does nothing** on an issue the harness has already
  suggested: `discover --mode assigned` skips a reference it already has. `/harness go` works.
- **`_forced` replies that a forced item "starts on the next sweep".** The sweep's `harness run`
  honours only the run window and the carry; the dispatcher plan that honours `--force` runs in
  `implement.yml` (its crons, a gate-1 merge, or a dispatch). COMMANDS says so.
- **A delivery pull request awaiting review shuts the `harness-ok` pool**, because `shipped` is in
  `priority.OUTSTANDING_STATES`. As designed: suggestions wait for every open request.
  brightboost#868 does not: its work item (#4) is closed and carries only the legacy
  `harness:packaged` label, and `list_work_items` filters on the `stage:` label, so the first
  labelled batch is eligible on the next Sunday run. Recorded, not fixed: `_issues` reads
  `state=all`, so a *closed* work item left wearing an outstanding `stage:` label would hold the
  pool shut indefinitely. None does today (searched 2026-09-11).

**What the review of the maintainer page changed (B332).** One defect fixed in code, and the page
corrected wherever it promised something that does not happen.

- **Triage re-picked issues it already had (B332, fixed).** `_triage_product_repo` skipped
  assigned, claimed and excluded-label issues, but not an issue that already had a work item: a
  suggestion still waiting at gate 1, one a maintainer had parked with `stop`, one ended. A
  suggested item does not hold the pool shut, and its proposal pull request is in this repository,
  so nothing on brightboost claims the issue. If the ranking picked it again, `_ensure_item`
  returned the old id, which took one of the five slots, and `discover.yml`'s
  `harness propose <id>` then raised `IllegalTransition` in `_enter` — a red run and an ops issue,
  and a maintainer's no undone by the next Sunday's ranking. Triage now reads the store once and
  skips any issue whose `issue:<n>` ref it already holds, in any state; the product-repository
  path runs only when nothing is `discovered`, so that is every known issue. Traced, not
  reproduced live: whether it bit depended on the ranking.
- **The page's corrections.** A maintainer cannot run a workflow, so "run `feedback` from the
  Actions tab" became "post any `/harness` command on the inbox", whose run sweeps his
  notifications too. `go` on a suggestion is harmful before the merge and useless after it, and
  silence is not a no, since the merge builds a suggestion regardless; the yes is now a plain
  comment and the no is `/harness stop`. The claim that one item is built per run and the window
  holds six runs is gone (see the `go` note above). `stage:blocked` and `stage:needs-human` are
  named as waiting on a person. A `--force` from level 2 still runs the rest of the command. A
  halted harness still acks on this repository. An inbox answer can wait up to two hours behind a
  build, because both take the `harness-ledger` lock. Assigning the bot helps only on an issue
  where it answered without opening an item. "Asking twice is one item" is true only for the same
  link, or the same words from the same account. USING's latency example matches the cron.
  README lists all six aliases and all three ways to start outside the window. The command table's
  dollar column is now relative weight (D66).
- **Recorded, not fixed: a level-2 `stop` has nowhere to park four stages.** `discovered`,
  `approved`, `blocked` and `needs-human` have no edge to `blocked`, so `_act_on_command` ends the
  item (`abandoned`) and says so. A fresh `/harness work` is `discovered` until Sunday, so this is
  the stop a maintainer is likeliest to make. Asking again with the same link, or the same words
  from the same account, finds the ended item through `find_by_ref`, which scans closed issues too,
  so `_GO_DEAD_ENDS`'s "`/harness work` opens a fresh item" holds only for a reworded request. The
  docs say all of this. Adding `blocked` edges would change D1's state machine, and that change
  deserves its own decision.

## D69 / B340–B352 — the trust file is the boundary, so one line must work everywhere

Decided by the operator's delegate; implemented 2026-09-12 on `feat/trust-tiers`, stacked on D68.
The brief was "figure out the trust system — multiple tiers, and only I will be adding people to it
manually, so that should be good." Read as: **the hand-curated file IS the security boundary**,
because only the operator can change it and only through a CODEOWNERS-reviewed pull request. Every
decision below follows from taking that seriously.

**The tiers are right; keep them.** Tabulated from `keywords.VERB_LEVEL`, level 3 is
`halt`/`resume`/`reject`, level 2 the eight steering verbs, level 1 `ask`/`status`, and level 0 the
absence of a line. Nothing is added, removed or renumbered. Level 1 looks empty in the shipped file
but is load-bearing: it is `DEFAULT_LEVEL`, the least-privilege reading of a line that omits its
level, so a typo falls to asking rather than to steering. Splitting `status` (free) from `ask`
(spends) into a fourth tier was rejected — it would renumber the file the operator hand-edits,
which is the one thing that must stay stable across a manual workflow, and would demote the only
verb the "asker" tier is named for.

What was actually wrong was legibility, in two ways the code contradicted:

- **Level 1 is not free.** `ask` clones the product repository and calls the model
  (`stages/ask.py`); `status` costs nothing. Both pages that described the tier said "changes no
  state", which is true, and implied "costs nothing", which is not.
- **The 2/3 line is partly fictional.** In `discovered`, `approved`, `blocked` and `needs-human` a
  level-2 `stop` ends the item for good, because none has an edge to `blocked` — the authority
  `reject` was reserved for (recorded under D68 and unchanged here).

Both are properties to display, not tiers to add. The table is now built by `trust.tier_table()`
from the verb→level mapping **passed in as a parameter** — `keywords` imports `trust`, so reaching
back for `VERB_LEVEL` inside `trust.py` would be the import cycle `links._who` already sidesteps by
hand — and `tests/test_docs_drift.py` checks COMMANDS.md, README.md and `.harness/trust.txt`'s own
header against it in both directions (B351). The drift had already happened: the trust file said
level 1 was "`ask` only" and README said "`ask` alone", both wrong since `status` joined it, while
COMMANDS.md was right precisely because a test read it.

*Deviation from the plan, recorded:* the table was to carry a "spends" column generated from code.
It does not. That would have meant a new per-verb cost constant in `keywords.py`, and the claim it
encodes is not derivable from `VERB_LEVEL`, so the table would have asserted something the drift
test could not actually check — the failure mode this section exists to remove. The table stays a
pure function of `VERB_LEVEL`; the spending fact is stated in prose next to `ask`'s existing cap.

**The vouched line is now the ordinary way to add anyone.** D68 introduced `vouch:<id>` as the
workaround for one account. D69 makes it the documented default, and a bare line the special case
for somebody already invited:

```
2 their-github-login vouch:their-numeric-account-id
```

One line, one file, one reviewed PR, and it works on **every** surface — this repository and the
product repository — with no invitation and no silent denial. The association route is kept exactly
as B131 defined it for unvouched handles, so an invited collaborator still needs no id.

This is not a weakening, and the direction matters: the association guards a **name**, which can be
renamed away and claimed by somebody else, and it is granted per-repository and is invisible from
the commenter's side. An account id is immutable and never reused. A vouched line therefore admits
one account everywhere and refuses any other account holding that login **even when GitHub calls it
OWNER** — stricter on identity than what it replaces — while granting no repository access at all,
so D30 stands untouched. Rejected: inviting every maintainer as a collaborator (breaks D30, since a
write collaborator on a personal-account repository can push a branch whose workflow runs with the
Claude token and the bot PAT in scope); dropping the association route (would force an id on people
for whom the invite already works, and would change B131); and resolving logins to ids at runtime (a
network read on the hot path of every comment, failing open or closed unpredictably).

**Anything that would grant less than it says is now refused outright.** All five were found by
probing the shipped parser, not by reading it:

- `2 nathan 193453438` — the id pasted with the keyword left off — parsed to a plain level-2 line
  with no vouch and nothing in `malformed`. It looked right, vouched for nobody, and granted
  nothing anywhere he was not already a collaborator. Only tokens beginning `vouch` were ever
  inspected; **every** token after the handle is now read, and one the gate does not understand
  refuses the whole line.
- `2 Jack Goetzmann` silently registered the handle `jack`.
- `2 nathan@example.com` and `2 nathan,` registered literally and could never match a login —
  refused for ever, silently. A handle must now be GitHub-login-shaped.
- `2 <NEW_MAINTAINER>` vanished entirely: no entry in `malformed`, `implicit` or anywhere else, so
  `doctor` could not name it, while the same line failed `Identity.trust_file_ready()` for a reason
  nothing connected back to it. Placeholders are now recorded in `Trust.skipped`.
- `3 jack` beside `1 jack` silently kept level 3, so a line added to **demote** somebody did
  nothing. The merge is unchanged (B269 pins it); the duplication is now recorded and named.

Each refusal takes the whole line, for D68's reason: read as something smaller, the line still
grants a level, which is not what its author meant either. Fail-closed is unchanged throughout.

**`harness trust`, which prints and never writes.** `trust line <login> --level N` resolves the
account id from the public API and prints the exact line on stdout with what it grants on stderr,
so it can be copied without editing. `trust show` prints the file as the gate reads it: who, at
what level, by which route, and every entry being refused. Neither writes: `.harness/` is outside
the write roots on purpose (B143) so the harness cannot change its own trust list, and the review
is the boundary. Two details are deliberate — `--level` is **required**, because defaulting
somebody's authority is exactly the silent misgrant this work removes; and a failed id lookup
prints **no line at all** and exits non-zero, because an unvouched line is precisely the entry that
gets silently denied, so emitting one after failing to look the account up would manufacture the
defect the command exists to prevent. It lives in `__main__.py` plus the existing `trust.py`, so
`SPEC_PACKAGE_FILES` and `D2_PACKAGE_FILES` are untouched; the refusal logic sits in `trust.py`
because B330 fails the build if any other module decides who is heard.

**A refused trust line now warns instead of stopping the fleet.** It was a `doctor` *problem*,
which exits 3 — and `doctor` gates discover.yml, feedback.yml and implement.yml under `set -e`. So
one typo in a hand-edited file stopped everything, which is precisely the failure #27 fixed once
before, when a stranded-access diagnostic took the fleet down within an hour of go-live. The line
already grants nothing at the gate, so the fleet-wide stop bought no safety it did not already
have. The loudness moved to review time, where the operator is standing: the suite fails any pull
request whose `.harness/trust.txt` carries a refused, skipped or duplicated entry (B350). `doctor`
also now names placeholders and duplicates, and always says which handles depend on the association
half — including saying "could not check" out loud at tier 0, where that read needs push access and
printing nothing at all read as "checked, nobody is stranded".

**A defect this turned up.** `store/__init__.py` loads a whole `Trust` and `store/github.py` then
did `tuple(trusted)`, discarding the levels. `links._who` falls back to "level 2+" for anything but
a `Trust`, so every work item and proposal pull request the harness opened showed a number while
replies — which pass `ctx.trusted` — named the handles. The footer exists so a reader of a public
thread can see whether their own comment would be honoured without first learning what a level is.
Invisible in tests because the fakes pass a `Trust`, which is the boundary class of defect CLAUDE.md
warns about; B352 checks it through the store.

**Recorded, not fixed.** `stages/deliver.py` requests review from every handle in the file,
including level-1 askers, and a refusal — the normal case for a vouched non-collaborator — is
swallowed into `ctx.record_decision`, so it is in the run record and never surfaced to the person
expecting a review request. Pre-existing, documented under D54, and out of scope here.
