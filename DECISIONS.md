# Decisions — Delivery 1 build

Recorded reasons for every place the implementation goes beyond, or reads between the lines of,
the Delivery 1 spec, `HARNESS-SPEC.md` v1.1, whose §0 asks for this file. The frozen delivery
specs were removed in D73 and remain in git history.

| # | Decision | Why |
|---|---|---|
| D1 | A dependency-install step (`npm ci` at the root and in `backend/`, only where a lockfile exists) runs before the baseline gate sequence. | On a fresh clone every one of the seven gates is vacuously red (`eslint is not recognized`, `npx` fetching a random `prisma@8-rc`). The product's own pre-push hook assumes an installed tree (`--skip-install`). The step is recorded in `EVIDENCE.md` under its own heading and in `manifest.json` it is absent from `gates`; the gate sequence itself is unchanged (§5.11). |
| D2 | A post-change sequence whose only reds are pre-existing is recorded as "no new failures versus baseline; NOT green", never as "green". | §5.9.3 says baseline reds are not attributed to the change; it does not say they are cured. Calling the sequence green would be the "silently green" outcome A13 forbids. |
| D3 | `bash scripts/check-prisma-drift.sh` is red on the untouched tree on this host (it wants a database). | §12 Q1 is open. The red is carried as pre-existing; nothing is loosened. Answering Q1 (a throwaway Postgres) would turn it green. |
| D4 | `governor.Authorization` keeps its spec-frozen name although `HARNESS-REVIEW.md` R2.3 greps `harness/` for the literal `Authorization`. | §5.3 freezes the dataclass name. R2.3's intent is the HTTP header; the only hits are the dataclass and its four uses in `governor.py`, and `gh.py` sets no such header (B31). The reviewer should read those hits, as R2.7 already instructs for its own grep. |
| D5 | On Windows, `doctor` and the CLI runner resolve `claude`/`npm`/`npx` through `shutil.which` before spawning. | The entry points are `.CMD` shims and `CreateProcess` will not find them by bare name. Injected spawns still receive the unresolved argv so B25 stays testable. |
| D6 | Halt is honoured inside `implement` (after install, after baseline, after the post-change gates, before each diagnose cycle), not only at stage boundaries. On halt the clone is released and the item reset to `approved`. | A10 and R7.7. A halt that waits for a 30-minute gate run to finish is not a kill switch. |
| D7 | `Config` carries two extra trailing fields, `github_token_present` and `github_token_shape_ok`, and `config.py` exposes `environ_snapshot()`, `secret_values()`, `read_secret()`. | I-4 confines `os.environ` to `config.py`, yet the CLI runner must build a child environment (B26), the redactor must scrub configured secret values (§5.8), and `identity` must detect presence without reading the value (§5.12). These are the narrowest seams that satisfy all three. |
| D8 | The session budget lives in memory on the `Governor`, per process. D74 removed it: admission is the two subscription-usage stops and the stored rate limit. | §5.3 gives no persistence for it and every command is a fresh process; the weekly budget is the durable one and is in the store. |
| D9 | Directed discover leaves the row in `discovered` as created; `packaged → shipped` is accepted by the store's state machine. | §5.2.2 lists the pair and B10 requires every listed pair to be accepted. The stage that would use it does not exist, which is the actual Tier-2 guard (§9 I-1). |
| D10 | The two secret keys (`HARNESS_GITHUB_TOKEN`, `ANTHROPIC_API_KEY`) are optional in `.env`; every other key is required. | B79 needs "absent" to be a legal state; B3 needs typo'd budget keys to fail. |
| D11 | `redact()` replaces the whole match of the generic `key: value` pattern, key name included, and also scrubs `Bearer <token>`. | B49 says the pattern is replaced with `[REDACTED]`. The spec's own `\S+` stops at the space after `Bearer`, which would leave the token behind. |

Open questions carried forward unchanged from §12: Q1 (local throwaway Postgres), Q2 (issue comments /
Tier 1). The collision re-check before implement (Q2's stop-gap) is live and blocked a real item during
acceptance (`#801`, claimed by `fix-801/ci-shell-gate-isolation`).

---

# Decisions — Delivery 2 build

Recorded reasons for every place the Delivery 2 implementation amends Delivery 1, reads between
the lines of `DELIVERY-2-HANDOFF.md`, or records something the handoff asked to be recorded here
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
| D19 | `claude --max-budget-usd` **binds under subscription auth** — verified 2026-09-03 on CLI 2.1.257: a 700-word essay with `--max-budget-usd 0.001` returned `is_error: true`, `subtype: error_max_budget_usd`, an empty result and exit 1, while the uncapped control cost $0.116. The cap is enforced at the **turn boundary**: the over-budget turn completes and is charged ($0.153 here), then the run stops. So `PER_CALL_CAP_USD` bounds a multi-turn `implement` to roughly one turn past the cap, not to the cap exactly; the harness's own accounting (WEEKLY_CAP_USD, the ledger) remains the primary control and the CLI cap the backstop — §6.1's "enforced twice" holds. The session JSON carries `usage`/`modelUsage` but no remaining-allowance signal (§1.3 confirmed). D74 removed the flag and `PER_CALL_CAP_USD`; the `is_error`-with-a-JSON-body branch this experiment justified stays, because an `error_max_turns` result needs it to report its turns (B432). |
| D20 | No `threading` and no `asyncio` anywhere under `harness/`. The `local-loop` command's heartbeat is synchronous: the entrypoint writes the first `HEARTBEAT`, and the loop rewrites it at the top of every unit and every 10 s between units from a plain sleep loop. | R9.3 scans `harness/` for `threading`; a daemon thread in the CLI would fail it. A synchronous heartbeat with a 180 s staleness window and units that poll `STOP` at their boundaries is enough for the watchdog, and it keeps the package single-threaded, which is what every Delivery 1 invariant test assumes. |
| D21 | An issue on the upstream (product) repository is never read as a command source. Keyword commands are honoured from exactly three surfaces: pull requests on the product repository (delivery PRs the harness opened), and issues and pull requests on this repository. `keywords.sweep` filters notifications to those surfaces. | Handoff §8.3 lists the surfaces as "proposal PR", "delivery PR", and "any issue here". The machine account owns no issue upstream and is not a collaborator there, so a command on an upstream issue has no item to act on and would only widen the surface an untrusted actor can probe. The trust gate (B131) would still deny it; not reading it at all is cheaper and leaves nothing to get wrong. |

Open questions carried forward from the handoff §17: Q2 (fork CI as a second opinion — treated as
confirmation, not a gate), Q4 (`runs/` artifact retention — 30 days). Delivery 1's Q1 (throwaway
Postgres) remains open; the database gates are still reported as omitted.

## Delivery 2 — build-time rulings (appended at the end of the fullsend run)

| # | Decision | Why |
|---|---|---|
| D22 | Every Delivery 1 test helper that writes a `.env` (conftest `DEFAULT_ENV`, test_cli `ENV_BODY`, test_stages `write_env`, test_packager `_env_text`, test_clone/test_identity env dicts) gained the thirteen Delivery 2 keys with the `.env.example` values. | Handoff §6.5: "every key required, no defaults in code". Without this every D1 test fails at `load_config`. Data constants only; no test function changed. |
| D23 | `DELIVERY-2-REVIEW.md` R3.3 (`grep "github_token\|HARNESS_GITHUB_TOKEN" harness/`) will hit `config.github_token_present` in `config.py` and `identity.py`. | That field is Delivery 1-frozen (RUN-DECISIONS "Config extras", B79). The token *door* `github_token()` is defined in `config.py` and imported by `gh.py` alone; the field carries a boolean, never the value. Read the hits, as R2.7/D4 already instruct. |
| D24 | `CLAUDE_CODE_OAUTH_TOKEN` is a pass-through key (`config.PASSTHROUGH_KEYS`): accepted in `.env`, never stored on `Config`, included in `secret_values()` so `redact` scrubs it. It is not added to `SECRET_KEYS`, which Delivery 1's B49 test pins to two names. | `.env.example` must ship it (handoff §5.4) and `harness init` must load what it ships (A7, D2-R4.1). |
| D25 | Six spec-quoted test corrections were made during reconcile, each logged with the quoted sentence in `.fullsend/notes/test-corrections-d2.md` (summarised here because that directory is not committed): I-11 test scans string constants, not identifiers (D1 §5.3 `Authorization` dataclass); B143 expects D1's eight roots plus two; two `UPSTREAM_REPO == REPO` parametrizations dropped (never in the handoff); two GitHub-store tests take the legal `proposing` hop before `blocked` (D1 §5.2.2); one test double returned `""` where the frozen `DIFF_LINES` contract is a 2-tuple. | Fullsend's rule: a test loses only to a quoted spec line. |
| D26 | One transition table serves both stores: Delivery 1's §5.2.2 plus the Delivery 2 states, minus `discovered→blocked`, `approved→blocked`, `shipped→abandoned` (pinned illegal by D1's B11 test). `decompose` labels the parent `discovered→proposing→blocked`; `/harness stop` on a shipped item goes `shipped→blocked→abandoned`. | Two tables in two stores is the drift the store seam (§2) exists to prevent. |
| D27 | `DELIVERY-2-REVIEW.md` R5.1 (`git log -p --all \| grep -cE "ghp_\|github_pat_\|sk-ant-"`) cannot read `0` for this repository: the redaction tests (Delivery 1's `test_redact.py` onward) necessarily name those prefixes, and two Delivery 2 tests carry a synthetic `ghp_FAKE0…` value so the B108 redaction path is exercised. | The intent — no *real* credential committed — is checked with a shape-aware form that ignores single-token filler: `git log -p --all \| grep -E "ghp_[A-Za-z0-9]{36}\|github_pat_[A-Za-z0-9_]{82}" \| grep -vE "(FAKE0|A{36}|x{36})"` → no output. GitHub push protection validates the checksum in a real PAT's tail; synthetic strings do not carry one. |
| D28 | `state/ledger.json` is committed by the workflows to a dedicated unprotected branch `harness-state` (loaded at job start, pushed at job end from a one-file worktree). The checked-in copy on `main` is the initial ledger and local mode's baseline. | B113 (main requires one approving review) and B115 (the workflow commits the ledger directly) cannot both hold on `main`: the Actions token cannot open or approve a PR for itself. The state branch keeps both properties without a bypass rule. |
| D29 | The machine account is `jgoetzmann-bot` (fork `jgoetzmann-bot/brightboost`), not the spec's `brightboost-harness`. `Identity.handle` is derived from the owner of `FORK_REPO` when it is set; the spec's default name applies only until then. The harness's commit-author email stays `harness@brightboost-harness` — an internal marker for B139, not a mailbox. | The operator chose the name (2026-09-03); the fork owner IS the machine account (handoff §5.1), so one setting rules both. |
| D30 | Nathan (`BrightBoost-Tech`) has **no access** to `jgoetzmann/bright-bots-harness`: not a collaborator, not in CODEOWNERS. He stays in `.harness/trust.txt`, which is what makes his comments on delivery PRs in the product repository count (B131: trust file AND his OWNER/MEMBER association *there*). Proposal review (gate 1) is therefore "only jgoetzmann". | A write collaborator on this repo could put code on `main` that a spending workflow runs with both secrets in scope; the design needs nothing from him here — gate 2 lives upstream, where he is already the maintainer. Decided 2026-09-03. |
| D34 | (Ruled after Delivery 3; filed in this table because it concerns the Delivery 1 and Delivery 2 documents.) The four delivery documents — `HARNESS-SPEC.md`, `HARNESS-REVIEW.md`, `DELIVERY-2-HANDOFF.md`, `DELIVERY-2-REVIEW.md` — move (`git mv`) from the repository root to `docs/delivery/`, with `docs/delivery/README.md` as their index. `HUMAN.md` and this file stay at the root. Cross-references between the four stay bare filenames (they are siblings now); the one path that actually broke, D2-R2's `pathlib.Path("DELIVERY-2-HANDOFF.md")`, is repointed. The frozen §3 file map in the handoff still shows the old root layout and is **not** edited — this row is its amendment, as D31 was for §1.3. D73 later deleted all of `docs/delivery/` (the documents remain in git history) and took `HUMAN.md` out of git; `harness setup` still writes it at the root. | The root had eleven markdown files and a reader could not tell the two operator entry points from the four frozen delivery artefacts. `HUMAN.md` cannot move: B82 requires `harness setup` to write it at the repository root and five tests assert that path. `DECISIONS.md` cannot move either: `identity.budget_experiment_recorded()` reads `<repo root>/DECISIONS.md`, the pull-request template names it as the place a pin change is justified, and fifteen files cite it by bare name. Moving what nothing resolves and leaving what something does is the whole of the rule. D74 deleted `identity.budget_experiment_recorded()`; the pull-request template and the bare-name citations still hold this file at the root. |

---

# Decisions — Delivery 3 build

Recorded reasons for the usage-aware governance delivery (`.fullsend/RUN-DECISIONS-D3.md`,
behaviors B200–B215). Numbering continues from D30. Every earlier decision, D1–D30, stays in force.

| # | Decision | Why |
|---|---|---|
| D31 | The subscription **does** expose its remaining allowance, and the harness now reads it: `claude -p --output-format stream-json --verbose` emits one `rate_limit_event` per call carrying `five_hour` and `seven_day` utilization as fractions 0..1 (shape quoted in full below). It comes from the inference response headers, so the long-lived `setup-token` receives it in Actions mode too. `seven_day.resetsAt` is the subscription's weekly reset. This supersedes DELIVERY-2-HANDOFF §1.3's "no signal exists" and D19's closing sentence. **B114 is kept, restated as a must-not-depend rule**: no decision may DEPEND on the signal being present. With `usage=None` — fake backend, older CLI, a call that never reached inference — the USD path (`WEEKLY_CAP_USD`, `RESERVE_PCT`, `PER_CALL_CAP_USD`) governs exactly as in Delivery 2, and `Governor.usage_stop_reason` returns `None` rather than guessing (B207). D74 removed that USD path; the amended B114 in D74 says what bounds a call when no reading is in force. | Verified 2026-09-03 on the CLI. A signal that is present on every real call and absent on every fake one cannot be made a precondition without making the fake backend a different program; keeping B114 as "must not depend" is what lets the same code path serve both, and it is the difference between reading a number and trusting it. Recorded as an amendment with its evidence rather than a silent reversal, per R12.3 — the same treatment D13 gave I-1. `WEEKLY_CAP_USD`'s default rises 25.00 → 400.00 for the same reason: a dollar cap sized for Delivery 2 would bind first and the usage stop would never be reached, which would make the new signal decorative. |
| D32 | The run window for this account is `RUN_WINDOW_START=mon 08:00` to `RUN_WINDOW_END=tue 20:00` UTC, and `implement.yml` runs three crons inside it: `17 8,14,20 * * 1`, `17 2,8,14 * * 2`, `23 20 * * 2`. The window is enforced by the dispatcher; the crons only decide when GitHub wakes the job. The DST drift is documented in the workflow and in OPERATIONS §13.3 and deliberately **not** corrected in code. | The subscription's weekly allowance resets Tuesday 20:00 UTC (13:00 PT). Spreading work across the whole week meant hitting the seven-day ceiling on a random Thursday with a branch half-written; concentrating it at the head of the window means a fresh allowance and a known reset to plan against, and the Tuesday 20:23 row is the wrap-up that spends what is left before it evaporates. Round-the-clock `23 */6 * * *` also competed with interactive use every single day. On DST: GitHub cron is UTC and never shifts while the reset is quoted in Pacific time, so for the PST months the wrap-up fires 37 minutes early, sees the old window, and spends nothing extra; the following Monday picks the new one up. A skipped wrap-up per winter is cheaper than a timezone table in a cron file, and an operator who cares moves that row and `RUN_WINDOW_END` together. |
| D33 | A usage stop or rate limit inside `implement`/`continue`/`package`/`deliver` is a **handoff**, not a failure: uncommitted work is committed as `wip: handoff (<reason>)`, the branch is pushed to the fork only (never upstream, never forced — B212), `runs/item-N/HANDOFF.md` is written and posted as a comment, the item returns to `approved`, the ledger records one `carry`, and the command exits 0. The carried item is the first thing the next run starts — even outside the run window — via `harness revise <id> --source continue`, spending against `OVERRUN_PCT` rather than `WEEKLY_USAGE_STOP_PCT` until it is green. D81 narrows this: the leeway applies only while the carry runs outside a weekly window, and a stop met before an item's first call is no handoff. | Delivery 2's answer to running out mid-item was to leave the item where it stood; with a weekly reset that lands in the middle of an implementation, that is a branch abandoned halfway every week. Carrying it costs one ledger field and one file, and `HANDOFF.md` is the same evidence a human would need anyway. Continuing outside the window is the one exception the window has, because the alternative is holding a half-finished branch for six days. The leeway is bounded (`OVERRUN_PCT`, default 10 %) so a carry cannot quietly consume the new week, and only one item is ever carried. Exit 0 because B120 already settled that a limit is a normal outcome: a red exit code here would page someone for the scheduler working as designed. |

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
| D47 | Every issue and pull request the harness opens carries the same signature and the same cross-links, built in one module (`harness/links.py`, B227): who wrote it, that it merges nothing, the `/harness` commands, who may give them, and links to the documents and the three repositories. The work-item issue quotes the product issue it tracks; the proposal pull request inlines the proposal itself; the delivery pull request carries `Closes <product>#N` and a link back to the work item. | Everything the harness writes is read in a browser by someone who did not write it. Before this the work-item issue body was the string `issue:633` and nothing else, and the proposal pull request was one sentence pointing at a file — so judging gate 1 meant opening a diff, and a reader arriving cold had no way to tell what the thing was, who was allowed to steer it, or where to look next. One module owns that presentation so the three surfaces cannot drift apart, and a test pins `VERB_HELP` against `keywords.VERBS` so a new command cannot ship undocumented and another checks that every document the footer links actually exists. `Closes` is used only where merging really does resolve the thing named: the delivery pull request closes the product issue, and the proposal pull request only *refs* its work item, because approving a plan is not finishing the work. Making the body prose meant the machine-readable reference needed a home a parser could find, so it moved to a marked line and `_origin_ref` reads that first, falling back to the old first-line convention for items opened before the change. |
| D48 | `discover.yml` takes `mode`, `target`, `lens` and `ignore_allowlist` as workflow inputs (B228). | There was no way to aim the harness at a specific ticket from GitHub. Triage is the only thing the schedule runs, triage only considers product issues carrying `harness-ok`, and no issue carries it — so the weekly discovery found nothing and would have kept finding nothing. Seeding the queue meant a CLI on a developer's machine holding the machine account's token, which defeats the point of the thing running on Actions. `directed` mode queues one named issue and proposes it in the same run, which is the route that actually works today. The conditionals in that step are written as `if` rather than `[ … ] && …`: under `set -e` a false test in the second form is a non-zero command and would abort the job. |
| D49 | No git hook runs in a harness clone: `acquire` sets `core.hooksPath` to a directory that holds none, **and the push repeats it on the command line**, where nothing can override it (B229). A failed push reports git's own lines rather than the tail of whatever a hook printed. | `npm ci` runs the product repository's `prepare` script, which is `husky`, which installs `.husky/pre-commit`, `commit-msg` and `pre-push` into the clone. The harness's own `git push` then ran the product's pre-push hook — inside an environment the product does not test, duplicating a gate the harness had already run explicitly and recorded verbatim. Measured on the first live delivery: brightboost's pre-push calls `scripts/check-bundle-size.js`, which crashes under Node 22 with `require is not defined in ES module scope` because the package is `"type": "module"`, so the push was refused after implement and package had both succeeded and every one of the seven gates was green. The reviewer's error message was two thousand characters of vite chunking advice with the actual cause nowhere in it. This widens nothing: the seven pinned gates remain the only definition of "it works" the harness accepts, and a developer convenience installed as a side effect of an install is not one of them. **Setting it at `acquire` alone was not enough, and the second live attempt proved it**: `npm ci` runs `prepare`, which is husky, which sets `core.hooksPath` straight back to `.husky/_` — after `acquire` and before any push. So `_git_push` now passes `-c core.hooksPath=...` itself. The diagnostic changed with it: the old message took the last two thousand characters of the failure, which for a hook is whatever its last command printed — a vitest browser stack, both times — so it now prefers the lines git itself writes (`error:`, `fatal:`, `remote:`, `hint:`). |
| D50 | A work item keeps the reference it was created with: `_item_from` resolves it from the meta comment, then from the issue body, and only names the issue itself when neither survives (B230). | It hardcoded `self:<number>`, and that is quietly wrong in a way that reached the product repository. `WorkItem.issue_number` reads the digits out of that string, so for harness issue 4 tracking product issue 633 it answered **4**. `deliver` asks whether the reference starts `issue:` before writing a closing keyword — it did not, so the first delivery pull request went out **without `Closes #633`**, and its package README described the work as `self:4`. The guard is the only reason it did not write `Closes #4` on somebody else's repository. Both sources were already there: `create_work_item` records `external_ref` in the hidden meta comment, and the body carries it too. |
| D51 | The self-imposed GitHub request ceiling follows the tier: `GITHUB_API_CEILING_PER_HOUR` at tier 0, at least 5000 at tier 2 (`gh.ceiling_for`, B231). | The key defaults to 50, a margin under GitHub's unauthenticated 60 — the right number for Delivery 1, which held no credential. At tier 2 the machine account's token raises GitHub's own limit to 5000, and holding the harness to the unauthenticated figure is not caution, it is a stop in the middle of a run. Measured on the first successful delivery: the pull request was opened upstream and the **very next call**, a label write, was refused by our own ceiling — after every token had been spent, leaving the item's state label disagreeing with reality. The configured value still wins when it is higher, because an operator who raised it meant it. |
| D52 | The delivery pull request opens at what a reviewer needs and collapses the rest (B232): the closing keyword, how to steer it by comment, why CI may be waiting for approval, and `CONTRIBUTING.md`'s own review checklist with the three boxes the harness measured already ticked. The gate evidence becomes a table of every gate, its phase and its exit code, with any failing gate kept whole. | The first one was **52 KB**, and **40 KB of that was the verbatim stdout of seven gates that all passed** — a wall nobody scrolls, sitting in the one place a reviewer has to read, and close enough to GitHub's 65 KB body limit to be a real risk. What a reviewer needs from a green run is that it was green; from a red one they need all of it, so a failure is still printed in full. The complete capture is unaffected: it is in the review package, which is attached to the run and rebuildable from the public repositories alone, and the package is the artifact of record. The table names each gate's phase because the sequence runs twice, and a list of sixteen rows with every gate appearing twice and nothing to say which is which is a puzzle rather than evidence. The checklist is `Bright-Bots-Initiative/brightboost`'s own, so the reviewer works through the list they already use; the harness ticks build, lint and tests because it ran exactly those on exactly this branch, and leaves i18n, responsiveness and pattern-matching blank because it did not. The note about **Approve and run** is there because GitHub holds workflow runs from an account with no merged contribution, which is every first delivery — and a reviewer who does not know that sees a pull request with no checks and draws the wrong conclusion. |
| D53 | Assigning the machine account to an issue on the product repository queues it. A new `discover --mode assigned` lists them and creates the work items; in triage, an issue assigned to the machine account survives and needs no allowlist label, while one assigned to anybody else stays excluded as B55 always had it. `feedback.yml` runs the sweep every three hours on a weekday (B233). | The allowlist label was the only way in, and **nothing on the product repository carries it** — so weekly discovery found nothing and would have gone on finding nothing. Asking a maintainer to add a label the harness invented is a worse ask than letting them use the verb GitHub already gives them for "this one is yours". Assignment says exactly what the label says, on the ticket itself, in the place they already work, and it is visible to everyone looking at the issue rather than buried in a label list. Making it an inclusion in triage rather than a separate universe keeps one rule: `intern-starter`, `large` and `architecture` still win, because work reserved for a human learning the codebase does not stop being that when somebody assigns a bot to it. The account is derived from `FORK_REPO`'s owner rather than configured again — a fork the account does not own is not one it can push to, so a second key could only ever disagree. No model call: which issues are assigned is a fact. The sweep is idempotent, so running it every three hours costs one request and queues nothing twice. |
| D54 | The delivery pull request names its reviewers in the body as well as requesting them through the API (B234). | `deliver` has always called `request_reviewers` with every trusted handle, and swallowed the refusal. The refusal is the normal case: requesting a reviewer needs push access to the repository, and the machine account is deliberately not a collaborator on the product repository (D30) — that is the whole point of delivering from a fork. So the API request is the path that works if it ever gains access, and the mention is the one that works today. A mention notifies, which is what asking for a review is for. |

**`runs/` assumptions still open.** D46 fixed the reader that broke the live run. An audit of the
same assumption, that `runs/` survives between Actions runs, confirmed five more. Each needs its
own durable source, so they are recorded here:

| Where | What breaks |
|---|---|
| `revise._baseline_red` (revise.py:673) | reads `runs/item-N/gates/baseline.json`; `_read` swallows the missing file and returns `""`, so on a second runner the pre-existing-red set is empty and **every gate that was already red becomes a "new failure"**, blocking the item. Defeats the whole point of `_new_failures`. The durable source needs choosing: the proposal's declared `baseline_red` is an expectation, not the measurement. |
| `item.package_path` (deliver.py:225, packager.py:547) | an absolute `runs/item-N/package` path. Harmless in `harness run --item`, where implement→package→deliver share one process; fatal for an item left in `packaged` and picked up by a later run, and for `harness archive`. |
| `HANDOFF.md` (revise.py:557) | the note whose entire purpose is to cross runs, written to ephemeral `runs/` and read back with no fallback — while `deliver.handoff` already posts the same body as an issue comment. The durable copy exists and nothing reads it. |
| `harness sweep` (__main__.py:1310) | every item's revise/propose runs under one `run_id="sweep"` Context, so per-item `run_dir` paths collide. |
| `heartbeat.yml:92` | read the seed ledger instead of `harness-state`. Fixed by D71 (B402). |

Fixed with D46: `_acceptance` resolves the proposal against `config.repo_root` instead of
`Path.cwd()`; the `proposals/<id>-*.md` lookup no longer picks the stale file when a re-propose has
landed a second one, and `int(item.id)` sits inside the error guard; `implement.yml` no longer
truncates `state/ledger.json` before a fetch that may fail.

**Review of the Delivery 3 acceptance fixes.** Three defects in `propose`'s lease (D37), each
pinned by a test: a failed model call's revert takes `_enter`'s entry state, so an item found in
`proposing` returns to `discovered`; a `preflight()` or `acquire()` failure after `_enter` is
reverted too; the leftover count in `acquire`'s error is best-effort. `harness ledger`'s "N to go"
shows one decimal, and the argv-ceiling message names the argument to shorten.

**`prettier.py` is not pinned.** It defines what counts as a change, which makes it as
result-defining as `gates.py`, `packager.py` and `redact.py`, but `verify_pin.PINNED` is frozen and
asserted verbatim by a test. Adding it is a spec decision for a later revision.

---

# Decisions after Delivery 3

D55-D63 were built together as behaviours B235-B292. Each entry below has the decision, a short
reason, and, where they exist, the rejected alternatives, the review findings and the open gaps.

## D55 - the inbox is not the record

Decision:
- A pinned inbox issue on this repository takes requests as comments. The harness replies in the
  thread and opens an ordinary work item.
- The inbox is polled on every sweep and de-duplicated by `ledger.seen(comment_id)`.

Why: the queue is one issue per item with one stage label, so a request becomes one of those.
`keywords.sweep` reads notifications, and an account is not subscribed to an issue it has never
touched, so the first request on the inbox would never arrive without the poll.

## D56 / B264-B268 - three label families

Decision:
- Work items carry `stage:`, `kind:` and `via:` labels. Legacy `harness:*` labels are still read.
- `via` reaches the priority queue on both stores: a column on sqlite (an additive `ALTER TABLE`,
  default `'requested'`) and the label on GitHub.

## D57 - audits produce a list of findings

Decision:
- An audit posts one findings issue labelled `kind:audit` and no `stage:` label, so the store never
  sees it as a work item.
- `/harness promote` turns chosen findings into work items, each through the proposal gate.

Why: with no stage label an audit issue cannot enter the queue, and no skip rule has to remember it.

## D58 - suggested work waits for a green light

Decision:
- Discovery suggests work only when nothing anybody asked for is outstanding, queues at most
  `SUGGEST_MAX_PER_RUN`, and comments once on each unassigned product issue it picks up.
- The comment says nothing starts without a green light (`/harness go`, B262).

## D59 / B242 - product issues and the inbox are surfaces of their own

Decision:
- Commands on a `product_issue` or the `inbox` never resolve through the thread number.

Why: the two repositories number issues independently. `/harness stop` on brightboost #633 must not
park harness item #633, and a stray `stage:` label on the inbox must not queue the inbox.

## D60 / B269-B273 - trust levels

Decision:
- A `trust.txt` line is `<level> <handle>`. A bare handle is level 1.
- A digit-first token with a level outside 1-3 refuses the whole line.

Why: least privilege when the level is missing, and a bad level must not register a handle
literally named `9`. D68 and D69 extend the line format.

## D61 / B279-B280 - I-18, the harness never works on its own repository

Decision:
- Refused at four entry points: `load_config` (`UPSTREAM_REPO` or `REPO` equal to `SELF_REPO`),
  `discover.request` (a pasted link), `clone._source_repo` (the checkout) and `deliver` (the pull
  request, before the state check and the push).

Why: a system that can rewrite the rules that govern it has no rules. Each entry point is a
different way such work could arrive.

## D62 / B283-B285 - `--force`

Decision:
- Level 3 only, recorded in the ledger beside the carry (B209).
- It lifts the run window and nothing else, and the reply says so.

Why: forcing is a scheduling exemption and says nothing about the work, so neither store needs a
column. The reply names the limit because a flag called `force` suggests it lifts more.

## D63 / B287-B292 - one queue for every model call

Decision:
- `run_model` classes every call (`answer`, `unblock`, `directed`, `audit`, `suggested`) and admits
  it by priority, then asks the governor.
- Priority decides what runs next and never admits a call the governor would refuse, so a class-0
  `ask` past a usage stop does not run.

Review: `harness dispatch` prints the queue as a `queue` key inside its one JSON plan, because
`dispatch.yml` parses that stream; unobserved usage does not refuse suggested work, because the two
usage stops guard the allowance on every call.

## D64 - twelve verbs, hyphenated, several to a comment

Decision:
- Fifteen verbs become twelve: `fix` merges into `revise`, `reject` into `stop`, `queue` into `go`,
  and `usage` is renamed `status`. The old names stay in `keywords.ALIASES`.
- `go` reads the item's state. On `blocked`, an existing branch means the proposal was approved, so
  the item resumes at `approved`; without one it returns to `discovered`. `needs-human`, `merged`
  and `abandoned` get an answer and no transition. `go` at `proposed` applies only to
  `via:suggested`.
- `revise` routes on the item's state. The level gate reads the typed word, so `reject` stays level
  3; `stop` parks the item at level 2 and ends it at level 3.
- `/harness-<verb>` parses like `/harness <verb>`. Every `/harness` line in a comment runs, top to
  bottom, each gated on its own level, at most `MAX_COMMANDS_PER_COMMENT` (10), with fenced code
  ignored. The comment is marked seen once, after parsing. A line with an unknown verb is skipped.
- A failed command is answered. GitHub's rate ceiling and the model's rate limit both stop the
  batch. The thread records the typed verb.
- Every reply ends with the other commands for that surface and a link to `docs/COMMANDS.md`.
- A 403 on the notifications feed keeps the inbox commands already collected and does not advance
  the notification cursor; `doctor` warns when the token lacks the `notifications` scope.

Why: each merged pair named a distinction the thread already carries (which pull request, which
gate, which state), so the second word only added a way to be wrong. Aliases keep commands already
posted on open pull requests working.

Review: two independent reviews and the first live sweep found the routing, level, fence, rate
ceiling and notifications defects above; each has a test.

## D65 / B293-B294 - an answer within seconds, and a watchdog for runs that never start

Decision:
- `ack.yml` (B293) reacts to a `/harness` comment on this repository within seconds and, when a verb
  takes more than a moment, posts one comment naming each verb and roughly how long it takes. Fast
  verbs get the reaction only.
- It runs `harness ack` with `PYTHONPATH=.`: no install, no `harness-ledger` lock, tier 0, fake
  backend, exit 0 on every path. `harness ack` reuses the command parser and the trust gate, checks
  both halt switches, and checks each verb's level and the audit gate before promising anything.
- The comment body reaches Python through the environment, never through `${{ }}` in a `run:`
  block, where a backtick in a stranger's comment would run on the runner.
- `ack.yml` concurrency is keyed per commenter. Comment-driven workflows check out the default
  branch, never the pull request's merge ref.
- `watchdog.yml` (B294) runs every four hours on weekdays. If no `feedback` run has started in six
  hours it dispatches one. A failed run counts as a run; skipped, cancelled, stale and
  startup-failure runs do not, and a queued dispatch is seen. It opens an issue only when scheduled
  runs have stopped, counted in weekday slots, and it does not sweep at weekends.
- `ops.yml` retries up to three attempts, counted by `run_attempt`, and only for a failure in a step
  on an allow-list of steps that run before anything is spent. A test classifies every step of the
  spending workflows.
- `gh.comment` appends `MACHINE_MARKER` (`<!-- bright-bots-harness -->`) to every body, and
  `harness ack` marks its own output. Comment-driven workflows skip marked comments unless the
  marker is quoted (`> <!-- ... -->`), which is a person's quote-reply. `FakeGh.comment` marks too.

Why: a thread cannot tell a harness that is thinking from one that is broken, and a run that never
starts leaves no failure to retry. Network and registry blips cluster, so a single retry often
lands in the same bad minute. A workflow `if:` cannot read the machine account's name from config,
and that account is an ordinary user rather than a Bot, so the harness marks its own comments.

Rejected: acknowledging from `feedback.yml`, whose install, `doctor` and ledger lock put its first
output minutes away; a deny-list of spending steps, because a retry re-runs the whole job and a
lost `Commit state/ledger.json` push would pay for the same model call twice.

Review: the harness's own replies woke `feedback.yml` (hence the marker); a
`pull_request_review_comment` checkout ran the pull request's code beside the PAT; the watchdog read
skipped runs as proof of life and flagged Monday mornings; `ack` promised work a halt or a level
would refuse.

Open: the watchdog cannot report GitHub's 60-day schedule disablement, which disables the watchdog
too. The missing weekly heartbeat comment is that signal (B144).

## D66 / B295 - report subscription usage first

Decision:
- Reports lead with subscription utilization: percent used and the headroom before the stop, and a
  line saying the allowance is shared (`links.usage_headline`, `_usage_lines`, `_usage_suffix`, the
  heartbeat).
- An unobserved window reads as unmeasured, never as 0%.
- `harness status` labels the harness's own budget-unit accounting apart from the subscription.
- `/harness audit` needs `AUDIT_MIN_HEADROOM_PCT` (75) of headroom, checked before the clone and by
  `ack`. `/harness status` reports the gate beside the suggestion gate (`SUGGEST_MIN_HEADROOM_PCT`,
  50).
- `reserve` keeps its exact token (B122) and carries the subscription reading once both windows are
  observed.
- A declined call is answered "Not now", as a normal outcome.
- B114 stands: no decision depends on the usage signal being present, and the runner's
  `--max-budget-usd` cap still binds on a single call.

Why: the `$` figures are an API-equivalent estimate from token counts, and nobody bills them. What
runs out is the five-hour and seven-day utilization reported with every call, and it is shared with
the operator's own use, so it moves while the harness is idle. The audit floor is looser than the
suggestion floor because somebody asked for the audit.

Review: every reader goes through the ledger's accessors, so last window's reading is never
reported as this window's and cannot refuse every audit after a roll; the tests build real `Ledger`
objects and roll a window; a figure under one point says so; the heartbeat drops a stale reading.

D74 removed the dollar machinery three of those bullets describe: `harness status` has no
budget-unit accounting, `reserve` is no longer a reason, and the runner passes no `--max-budget-usd`
cap. Subscription usage leads every report because it is now the only measure there is.

## D67 / B296-B315 - the token carries `workflow`; the harness guards `.github/` itself

Decision:
- The machine PAT carries `public_repo`, `notifications` and `workflow`, so `harness sync-fork` can
  fast-forward the fork past upstream's own workflow changes.
- One path set, `clone.PROTECTED_PUSH_PATHS = ("/.github/",)`, covers all of `.github/`, and B64's
  `implement.FORBIDDEN_DIFF_PATHS` is the same object (B296). The harness refuses any change under
  `.github/` on the product repository; a person makes those.
- `clone.walk_harness_commits` walks the branch from its tip (`--first-parent`) and stops at the
  first commit a harness email did not author (B297). `gh.push_branch` runs it before every push,
  under `--dry-run` too, takes no base, and refuses when git cannot answer (B298). `push_ref`, which
  relays upstream's `main` to the fork fast-forward only, is outside it.
- `deliver` and the handoff also read every commit the push would send that upstream lacks,
  whatever its author, anchored on the rebase's `FETCH_HEAD` or the fork's `main` fetched at check
  time (B313, B314).
- Guard-side git reads use `clone.GUARD_GIT` (`git --no-replace-objects -c core.commitGraph=false`)
  and refuse a clone carrying a `refs/replace/` ref, a grafts file or a shallow history (B312).
- The handoff withholds only the push, when the branch carries a `.github/` path or the check
  cannot answer. The item is blocked and the withheld commits are kept as `WITHHELD.patch` beside
  the note (B301, B315).
- `revise` fixes its diff base before the model runs, judges a resumed item from its fork point,
  and walks the branch before shipping, blocking with the clone kept (B302, B303).
- Local mode's watchdog runs the same walk (`Get-UnpublishablePaths`) before its push (B304).
- `doctor` reads `X-OAuth-Scopes` from `GET /user` and warns on a missing, extra or absent scope;
  a warning, because a problem exits 3 and stops the fleet (B305).
- B105 reads the fork from config (B306). The PR body and prompts state the enforced check (B307).
  I-15 names the one path set (B308). The documents name all three scopes (B309); B310 fails the
  build on a false scope claim in a live document, and B311 holds every scope list to
  `EXPECTED_TOKEN_SCOPES`.

Why: without `workflow`, sync-fork failed whenever upstream changed a workflow, and a stale fork
stops every delivery. With it GitHub no longer refuses workflow pushes, so the harness's check is
the only layer. The walk follows authorship because a rebase keeps upstream's authors, while a diff
from the lease base includes every upstream change since, CI files included.

Rejected: a diff from `lease.base_sha` (it refuses every delivery once upstream touches CI);
fast-forwarding by hand or re-forking whenever upstream touches CI; recording a walk that hits its
100-commit cap instead of refusing.

Review (B312-B315): three reviews found that replace refs and grafts could hide commits from the
guard, that a model commit under another author passed the walk, that the handoff's anchor was a
ref the model could move, and that a withheld handoff left a carry that could not resume.

Open: a model that points `origin` elsewhere in `.git/config`, or replaces the `git` binary, is
outside any in-clone check. The frozen Delivery 2 and 4 documents said the token had no `workflow`
scope; this entry amends them (they were removed in D73 and remain in git history).

## D68 / B320-B332 - vouch for an account, not a name

Decision:
- A trust line may carry `vouch:<numeric user id>`, as in `2 some-login vouch:12345678`. A comment
  passes the association half of the gate on every surface when its `user.login` matches the handle
  and its `user.id` equals the vouched id. Any other account holding that login is refused, even an
  OWNER.
- The level still caps the verbs. A line without `vouch:` is B131 unchanged.
- The token is `vouch:<positive integer>`, at most one per line, case-insensitive. Any other token
  starting `vouch` refuses the whole line into `Trust.malformed`, and `doctor` names it.
- Lines that disagree about a vouched handle's id, or a vouched and a bare line for the same handle,
  refuse every line naming it (`Trust.conflicted`, B331).
- A missing, zero, non-numeric or boolean `user.id` never matches a vouch.
- `trust.comment_authorised` is the one gate, used by the sweep, `harness ack` and revise's review
  filter, which takes the whole `Trust` (B328). B330 fails the build if another module reads
  `author_association` or calls `is_authorised`.
- `ack.yml` passes the commenter's id as `--actor-id`; without it a vouched handle gets no ack.
- `doctor` lists vouches and checks each id against `GET /users/<login>` when it can. A different id
  or a 404 is a warning.
- Triage skips any product issue whose `issue:<n>` ref already has a work item, in any state (B332).

Why: D30 keeps the product maintainer off this repository, and a private organisation membership
makes GitHub report him as CONTRIBUTOR on the product repository, so the association half denied
him everywhere. An account id is immutable and never reused, so it names the person more exactly
than an association, which guards only a login that can be renamed and re-registered.

Review: the mixed-line escalation (B331) and five maintainer-doc promises that a vouched maintainer
without access cannot keep.

Open:
- Assigning the bot works only on issues it already participates in; GitHub assigns only
  collaborators, organisation members and participants.
- `keywords.sweep` does not read pull request review summaries. Revise reads them as feedback.
- `/harness go` on an unmerged suggestion moves it to `approved`, but on Actions implement finds no
  work package until the proposal is merged (D46). The docs give the yes as a plain comment and the
  no as `/harness stop`.
- A closed work item still wearing an outstanding `stage:` label holds the suggestion pool shut.
- A level-2 `stop` on `discovered`, `approved`, `blocked` or `needs-human` ends the item, because
  none has an edge to `blocked`.

## D69 / B340-B352 - the trust file is the boundary, so one line must work everywhere

Decision:
- The levels stay: 3 is `halt`, `resume` and `reject`; 2 the eight steering verbs; 1 `ask` and
  `status`, and `DEFAULT_LEVEL`; 0 no line. Level 1 is not free: `ask` clones and calls the model.
- `trust.tier_table()` builds the level table from the verb-to-level mapping passed in. B351 checks
  COMMANDS.md, README.md and `.harness/trust.txt`'s header against it, level names included.
- A vouched line is the documented way to add anyone: `2 their-login vouch:their-account-id`. The
  association route stays for unvouched handles.
- Every token after the handle is read, and one the gate does not understand refuses the line.
  Handles must be login-shaped. Placeholders go to `Trust.skipped`. Duplicate handles are named;
  the level merge itself stays (B269).
- `harness trust line <login> --level N` resolves the id and prints the line; `trust show` prints
  the file as the gate reads it. Neither writes, `--level` is required, and a failed lookup prints
  no line and exits non-zero.
- A refused trust line is a `doctor` warning. The suite fails any pull request whose `trust.txt`
  carries a refused, skipped or duplicated entry (B350).
- The GitHub store keeps the whole `Trust`, so work-item and proposal footers name the handles
  (B352).

Why: only the operator edits `trust.txt`, through a CODEOWNERS-reviewed pull request, so the file is
the security boundary and each line must grant exactly what it says on every surface. A refused
line already grants nothing, so a fleet-wide stop over one added no safety and made a typo an
outage.

Rejected: a fourth tier splitting `status` from `ask` (it renumbers the hand-edited file); inviting
maintainers as collaborators (breaks D30); dropping the association route (changes B131); resolving
logins to ids at runtime (a network read on every comment); a generated "spends" column (not
derivable from `VERB_LEVEL`).

Review (B353-B358): a login beginning `vouch` is a login; the level column is ASCII digits and
`load_trust` never raises; `trust line` refuses an Organization account; `trust line` and
`trust show` need no `.env` or `Context` (`gh.public_reader()`); `trust line` prints only a line the
parser grants as promised; `Identity.trust_file_ready()` reads `Trust.skipped`.

Open: `deliver` requests review from every trusted handle, level 1 included, and records a refusal
only in the run record (D54).

## D70 / B359-B394 - an adversarial self-audit before delivery

Decision:
- Once the gates show no new failures by the `_new_failures` rule, `implement` runs `selfaudit`: a
  separate model call that reads the committed diff against the approved work package (acceptance
  criteria, behaviours, touched paths) and answers whether the change does what was approved, and
  only that.
- Blocking findings get a `selfaudit_fix` pass and the gates run again, up to
  `MAX_SELF_AUDIT_CYCLES` (default 3). `0` turns it off: no call, no record, no line in the pull
  request. A repeated finding signature ends the loop.
- The audit is advisory. A finding is labelled a model's opinion and never blocks delivery. Two
  things block the item: a fix pass whose diff B64 refuses (such as a `.github/` path), and a clone
  the tree guard cannot put back (B391).
- The auditor holds `Read`, `Glob`, `Grep` and `Bash` (`implement.SELFAUDIT_ALLOWED_TOOLS`); the fix
  pass holds implement's tools. Both stages class as `unblock`, which `priority.admit` never
  refuses.
- A fix pass that breaks a gate is reset to the tip read at the start of the cycle, never to the
  base, and the green results are written back to `gates/final.json`. A tip that cannot be read
  ends the audit as not run.
- The tree guard compares the change set against the audited tip, and HEAD's branch and commit,
  before and after the audit call, a rate-limited call included. On any change it restores the
  clone and discards the audit as *not run: the auditor modified the tree*. A fix pass may edit and
  not commit: a branch it moved goes back on the tip with its edits left for B64, the formatter and
  the gates (B387, B388). Change sets are read NUL-separated (B390).
- A halt inside the loop first puts back an unjudged fix, then hands the item off through
  `deliver.handoff`. It resumes through `revise --source continue`.
- An unreadable answer, a failed call or an unavailable diff records *not run: <reason>*. A
  finding's `where` must name a changed path, a touched path, or `acceptance:<n>`/`behavior:<n>`.
- `runs/<run-id>/selfaudit.json` holds every cycle, redacted field by field before it is cut or
  serialised (B392). Every finding is a decision line in the package's `DECISIONS.md`, with mentions
  neutralised (B393). The delivery PR body carries one status line after the gate results, with at
  most five blocking findings, for the tip read before the rebase; B386 holds
  `docs/PACKAGE-FORMAT.md` §6 to the body.
- `stage_run`'s CHECK gains both stages at `LAYOUT_VERSION` 3, probed by `'selfaudit_fix'`. The two
  new prompts are pinned. `tests/fixtures/deliver/pr_body_cap0.md` holds the PR body byte for byte
  with the audit off.

Why: every other check is mechanical, and none asks whether a green change does what gate 1
approved. A model reviewing a model is weaker evidence than a gate, so it advises. The fix pass is
its own stage because `priority.class_of` routes `implement` through the item's `via` and could
refuse a suggested item's fix at 50% weekly usage, abandoning green work. `audit` was already taken.

Rejected: auditing only when every gate exits 0 (it skips every known-red item); a `SELFAUDIT.md` in
the package (it edits the pinned `packager.py`, and `runs/` does not survive between runs anyway).

Review: the behaviour tests (B359-B386) found that a credential quoted in a finding broke
`selfaudit.json` under `write_redacted`, so the record goes through `redact_json` first. The
adversarial pass (B387-B394) found the guard ignored HEAD, a halt on the fix call hid that cycle's
findings (B389), quoted file names escaped the restore, and no test ran the restoring git on a real
clone (B394).

Open:
- Every call holding `Bash` (`implement`, `revise`, `selfaudit`, `selfaudit_fix`) can reach the
  machine credential in `$GITHUB_WORKSPACE/.env`; the deny rules bind the read tool only. Giving the
  auditor `ask.py`'s read-only tools would remove its shell.
- The guard does not detect a rebase or merge left in progress, or an edited `.git/config`.
- `implement._write_gates`, `DELIVER.json` and the transcripts serialise JSON through
  `write_redacted`, so a keyed secret in gate output can leave them unparseable.
- `revise` does not run the audit, and carried items are delivered unaudited.
- The record is same-process only (D46). Elsewhere, and after `/harness revise`, the PR line reads
  *not run for this revision* or names the older sha it audited.

## D71 / B395-B408 - a subscription refusal is a rate limit that ends at its reset

Decision:
- A failed result's error is its redacted `result` message, then stderr, then the subtype (B395).
- A failed result whose usage status is `rejected`, and which was not running on extra usage, is
  rate-limited at any exit code (B396, B403). Its reset is the earliest `resets_at` of a unified
  window at or over 1.0, else the event's own `resetsAt` for other limit types (B404).
- An `is_error` result whose message matches `RATE_LIMIT_PATTERN` is rate-limited at exit 0 too.
  With a result line, the pattern and the reset are read from the message and stderr only, never
  from tool output (B397, B405).
- With `--json`, a refusal prints `{"rate_limited_until": ...}`; `discover.yml` ends green on it,
  and its propose loop stops on `rate limited until` (B398, B407).
- A stored window reading expires at that window's own `resets_at`. The dispatcher, governor,
  `priority.admit`, `headroom_pct`, `run_model` and the status reply pass their clocks (B399).
  `harness ledger` and `harness status` print `window reset since` (B406). The dispatcher's
  `; weekly N%, session N%` suffix decides nothing and keeps no expiry (B211).
- The spending workflows upload `runs/**/*.jsonl` (B400).
- `ops.yml` puts the failing job's error lines first in the issue, redacted and capped at 20 lines
  (B401).
- The heartbeat reads the live ledger from `harness-state` through the contents API and says which
  ledger it read (B402, B408).

Why: when the subscription allowance runs out, the CLI exits 0 with `subtype: "success"` and
`is_error: true`. The harness read that as an ordinary failure, turned the scheduled discover red,
and kept a 100% reading that refused the one call able to replace it after the reset. The
allowance is shared with the operator's own use (D66).

Rejected: rolling the window inside the plan, which is pure; treating an expired reading as 0%;
a separate "rejected means stop" dispatcher rule, since `rate_limited_until` already lifts at the
same instant.

Review: B403-B408 came from an independent review of the first cut.

B114 still holds: a result without a `rate_limit_event` is classified by `reset_at` and the wording;
only `rejected` without extra usage adds a classification; a reading without `resets_at`, or a
caller without a clock, behaves as before.

## D72 / B409-B413 - one subscription session a day, opened before dawn

Decision:
- A run-window endpoint may be `daily HH:MM` on both ends. A weekday paired with `daily` is a
  startup error naming both keys. The weekday form stays the `.env.example` default (B409).
- A daily window compares the minute of the UTC day, is the same every day, and may wrap past
  midnight. A mixed pair that bypassed `load_config` reads as unparseable, which means open (B410).
- A daily window is named `outside run window (daily 11:00-15:00 UTC)` by the dispatcher and by
  `harness run`. B210's weekly wording is unchanged (B411).
- `.harness/config.json` sets `daily 11:00` to `daily 15:00` and `SESSION_USAGE_STOP_PCT` 80.
  `discover.yml` runs at `7 11 * * *` and `implement.yml` at `23 11-14 * * *`; a test fails when a
  daily window stops containing those crons (B412).
- Outside a daily window the carried item waits too, in the plan and in `harness run`. A weekly
  window keeps D33's exemption (B413).
- Unchanged: `WEEKLY_USAGE_STOP_PCT` stays 90 and binds only with a seven-day reading.
  `feedback.yml` (`41 */3 * * 1-5`), the watchdog and the heartbeat keep their schedules; outside
  the window the sweep starts no item and spends only on a `/harness` command. `--force` still lifts
  the window, which now matters only for a gate-1 merge or a manual dispatch outside it.

Why: the account's plan has a five-hour session limit, shared with the operator, and no weekly
limit, so D32's Monday-Tuesday window left six days of sessions unused. 11:00 UTC is 03:00-04:00
Pacific all year, so nothing moves at a clock change. The session opens with the first model call,
usually discover's triage at 11:07. Starts end at 15:00 so a 120-minute implement run overruns a
session discover opened by under an hour, and the 80% stop leaves the operator a fifth of it.

Rejected: a local-time window through `zoneinfo`, which the UTC cron cannot follow; every session
round the clock, since the daytime sessions are the operator's; moving feedback into the window,
which would make human requests wait up to a day.

Review: B413 came from the review of the first cut.

## D73 - the repository sweep

Decision:
- `docs/delivery/` (the frozen Delivery 1-4 specs, review protocols, live-trial plan and index) is
  deleted; it remains in git history. D1-D54 cite those documents by name as history. This
  supersedes D34.
- `docs/USING.md`, `docs/PROPOSALS.md` and `.github/ISSUE_TEMPLATE/work-item.md` are deleted. Their
  unique content moves into `docs/COMMANDS.md`, `docs/FOR-MAINTAINERS.md`, `docs/OPERATIONS.md` and
  `docs/PACKAGE-FORMAT.md`.
- `HUMAN.md` leaves git and is gitignored. `harness setup` still generates it at the root (B82).
- `links.DOC_LINKS` drops the USING and PROPOSALS entries, so every issue and pull request footer
  links two fewer documents, and the golden PR body fixture is regenerated.
- Comments, docstrings and published text follow one rubric: behaviour in present tense, no
  history, at most one trailing citation. Entries from D55 on have the decision, a short reason,
  and the rejected alternatives, review findings and open gaps where they exist. D70's entry, lost
  in the D71 merge, is restored.
- `prompts/README.md` is rewritten, and `.harness/PIN` is regenerated. No model prompt changes.
- Commits and pull requests carry no AI attribution.
- Configuration cleanup: D74.

Why: the deleted documents were frozen build specs or further copies of topics other pages cover,
and several copies had drifted from the code. One page per topic is what the drift tests can hold.

## D74 / B414-B432 - no dollar figure anywhere

Decision:
- Every dollar mechanism is removed: the weekly USD budget and the reserve under it, the per-call
  `claude --max-budget-usd` cap, the static per-stage estimates and their observed medians, the
  session budget, the `budget_period` table, the cost line on transition comments, the dollar
  figures in `harness status`, `harness ledger` and `/harness status`, and the local watchdog's
  spend stop. What bounds a call is the run window, `MAX_CONCURRENT_ITEMS`, the `MAX_TURNS_*`
  ceilings, the five priority classes, both kill switches and the commanded halt, the two
  subscription-usage stops, and the subscription's own refusal (D71).
- Ten keys go with them: `WEEKLY_BUDGET_PCT`, `SESSION_BUDGET_PCT`, `RESERVE_PCT`,
  `WEEKLY_RESET_DAY`, `MAX_CONCURRENT_CLONES`, `WEEKLY_CAP_USD`, `PER_CALL_CAP_USD`,
  `NOTIFY_POLL_HOURS`, `AUDIT_CAP_USD` and `ASK_CAP_USD`. `config.CONFIG_JSON_KEYS` holds nineteen,
  `.harness/config.json` ships thirteen, and `.env.example` ships forty-one keys and no
  `ANTHROPIC_API_KEY` line - that key stays known, redacted and stripped from the model's
  environment.
- `config.RETIRED_KEYS` carries those ten names. They are accepted wherever a key is accepted and
  ignored, in `.env`, in `.harness/config.json` and in the environment, so an existing file keeps
  loading. `harness doctor` names each one as a warning and never as a problem, because a problem
  exits 3 and stops the fleet, and a stale line in the operator's own file must not.
- `Governor(config, clock, ledger)` takes no store and requires the ledger. `authorize` checks the
  usage stop, then the stored rate limit, and returns `Authorization(id, work_item_id, stage,
  max_turns)`. `record` observes the usage before it counts the call, because `observe_usage` zeroes
  `window["calls"]` on a seven-day turnover (B431).
- The dispatcher's ordinary reason is `<k> of max <n> slots`, with `; weekly X%, session Y%`
  appended once both windows are observed. `reserve` leaves the reason vocabulary,
  `discover.yml`'s gate and `MUST_STOP_REASON_PREFIXES`, and `skipped` carries only
  `depends_on N not merged`, `outside run window` and `slots full`.
- The ledger keeps `schema` 1 and loses `window.spent_usd`, the `observations` map and
  `history[].usd`. `Ledger.record(ts, stage, issue, run)` appends one entry and counts one call;
  `roll_window` is gone, because `observe_usage`'s turnover is what moves `period_start`. The B101
  transition comment's `cost:` line becomes an optional group, so the comments already on live
  issues still parse. `harness ledger --rebuild` replays the history and the call count into the
  window it finds on disk, keeping `period_start`, the cursors, `window.usage`, `window.carry` and
  `rate_limited_until` (B430).
- Kept: `EXIT_BUDGET = 4`, `errors.BudgetExhausted` and the stderr line
  `budget exhausted: <reason>`, which now mean the harness declined to start a model call - a usage
  stop, a stored rate limit or a priority refusal, and never a dollar figure. The exit code and the
  phrase are a contract with `discover.yml`, `feedback.yml` and `ops.yml`'s `ERROR_LINE_RE`.
- Kept: `runner/cli.py`'s branch for an `is_error` result carrying a complete JSON body. It is what
  keeps an `error_max_turns` result reporting its turns and its own message rather than a stderr
  dump (B432).
- `harness status --json` replaces its `budget` block with `usage` (`weekly_pct`, `session_pct`,
  `rate_limited_until`), and its `in_flight` rows lose `allowance_pct` and `cost_usd` with the
  `StageRun` fields. `harness run --session-pct` is gone. `packager.py`'s manifest `stages` entries
  become `{stage, turns}`, which is an edit to a pinned file, so `.harness/PIN` is regenerated in
  the same change.
- `store/sqlite.py` drops the `budget_period` DDL, the four budget methods and the two `stage_run`
  columns, with no `LAYOUT_VERSION` bump and no `DROP`: `migrate()` stays additive, an existing
  database keeps the unused columns and the orphan table, and nothing reads them. This amends the
  "§5.2.1 verbatim" claim the store's docstrings make.

**B114, as amended.** No decision may *depend* on the subscription usage signal being present, and
none falls back to a dollar figure - there is no dollar figure. With no reading in force (a fake
backend, an older CLI, a call that never reached inference, or a reading whose window has reset),
the usage stops and the headroom gates admit: unknown is not a stop. What bounds a call then is the
run window, `MAX_CONCURRENT_ITEMS`, the `MAX_TURNS_*` ceilings, both kill switches and the
commanded halt, and the subscription's own refusal, which D71 records as `rate_limited_until` and
which ends at its reset. When a reading is present it stops work exactly as before
(`WEEKLY_USAGE_STOP_PCT`, `SESSION_USAGE_STOP_PCT`, `OVERRUN_PCT`, `AUDIT_MIN_HEADROOM_PCT`,
`SUGGEST_MIN_HEADROOM_PCT`).

Why: the dollar figures were an API-equivalent estimate computed from token counts, on a
subscription that bills none of them, so each was a number the harness invented and then governed
itself with. Two of them could stop real work: a weekly cap sized for Delivery 2 and the reserve
line under it. D31 brought in the signal that measures what actually runs out and D66 put it first
in every report; removing the estimate beside it leaves one measure instead of two that disagree.
The `--max-budget-usd` cap goes with them, because a per-call ceiling denominated in dollars binds
on the same invented number.

Consequences, stated plainly:
- The local watchdog loses its independent spend stop. The container's remaining external stops are
  heartbeat staleness, free disk, battery and sustained host CPU.
- A pre-D74 ledger loads unchanged and is saved without `spent_usd`, `observations` or
  `history[].usd`, while `period_start`, the cursors, the reading, the carry, the rate limit and the
  history order survive. Pre-D74 code reading a D74 file also loads, and loses only accumulated
  spend, which no longer means anything.
- `dispatcher.Candidate` loses its `stage` field, and every construction drops the keyword.
- `B419` scans `harness/` for a dollar figure. Two exemptions are expected, each only in the file
  it belongs to: the `RETIRED_KEYS` tuple in `config.py`, and `ledger.py`'s
  `window.pop("spent_usd", None)`, which is how a ledger written before D74 sheds the field the
  next time it is saved. Any further exemption the scan turns up is recorded here.

Amends B5, B16-B19, B21-B23, B101, B114, B116, B117, B119, B122 and B211, and annotates D8 (the
session budget), D19 (the `--max-budget-usd` experiment), D31 (the USD path it fell back to), D34
(`identity.budget_experiment_recorded()`, one of its two reasons this file stays at the root) and
D66 (the budget-unit line, the `reserve` token and the per-call cap). Allocates B414-B432.

Rejected: keeping `WEEKLY_CAP_USD` as an advisory figure, which leaves a number nothing may act on;
renaming `EXIT_BUDGET` and its stderr phrase, which three workflows parse; deleting the `cost:` line
from the B101 parser instead of making it optional, which would stop the comments already on live
issues from rebuilding; bumping `LAYOUT_VERSION` to drop two unused columns, which rebuilds the
table for no behaviour.

## D75 / B433-B434 - the suite tests the tree it lives in, not the host

Decision:
- `tests/conftest.py` carries an autouse fixture that removes every name in
  `config.KNOWN_KEYS + config.RETIRED_KEYS` from `os.environ` for the duration of each test. The
  names are read from `harness.config`, so a key added later is covered the day it is added. The
  fixture is set up before the test body, so `monkeypatch.setenv` inside a test still decides what
  that test sees, and the two `tests/test_usage_refusal.py` helpers that already clear the keys by
  hand keep working unchanged. The fixture is function-scoped, so a fixture of wider scope is set
  up before it and still reads the host value. B433's own module-scoped export is exactly that,
  and it is the only fixture in the suite wider than a function.
- `pyproject.toml` sets `pythonpath = ["."]` under `[tool.pytest.ini_options]`, so the rootdir is
  first on `sys.path` for a bare `pytest` as well as for `python -m pytest`.
- `harness/config.py` is unchanged. `os.environ` still overrides `.env` key for key (B1), and
  `config.py` stays the only module that reads it (I-4).

Why: `load_config` merges `os.environ` over the file it reads and nothing cleared those names, so
any variable a developer's shell or a runner happened to export - `BACKEND`, `PERMISSION_TIER`,
`MIN_FREE_DISK_GB`, `REPO`, `MODEL` - silently changed what the suite tested, and every test that
goes through the CLI loads its config that way. The import path had the same shape: the editable
install maps `harness` to the main checkout through a meta-path finder it appends, which only a
`sys.path` entry beats, so a bare `pytest` inside a worktree exercised the main checkout's source
while reporting on the worktree's tests. Both are the failure that made five trust tests flap one
layer down, where `doctor` measured free disk on the host's C: drive rather than on anything the
test had set up.

B433 proves the first: a module-scoped fixture exports `MIN_FREE_DISK_GB=99` before the autouse
fixture runs, and the test asserts that a config loaded with no `environ` argument reads the `.env`
value of 5. Without the autouse fixture it reads 99. B434 proves the second: `harness.__file__`
resolves inside the repository root the tests live in, and `pyproject.toml` still declares the path
entry.

Rejected: having `load_config` ignore `os.environ` under a test flag, which puts test-only
behaviour in the one module the invariants keep smallest; clearing the keys in each test that needs
it, which is what `tests/test_usage_refusal.py` does twice already and what every later test would
have to remember; setting `PYTHONSAFEPATH` in CI, which hides the import-path hole behind an option
nobody sets locally.

Allocates B433-B434.

## D76 / B435-B456 - the inbox answers, and the pinned issues keep themselves

Decision:
- **Naming the machine account is a command.** `@jgoetzmann-bot <verb> [args]` at the start of a
  line is `/harness <verb> [args]`, on every surface. `keywords._mention_re` matches only the one
  handle, so `@nathan status` stays a sentence about Nathan, and `(\w+)` cannot match `/harness`,
  so `@jgoetzmann-bot /harness work` parses once rather than twice. `commands_from` and `sweep`
  already carried the machine account as `machine`; that one parameter now feeds the mention form
  too, so the handle the sweep refuses as an author and the handle it accepts as a mention cannot
  drift apart. `parse_typed`, `parse_all` and `parse` take `mention=""`, which is the mention form
  off, so every existing caller and test is unchanged.
- **The filters had to move first.** Both `ack.yml` and `feedback.yml` gated on
  `contains(comment.body, '/harness')`. A job condition is evaluated before any step runs, so the
  two `@jgoetzmann-bot audit <lens>` comments on the live inbox produced two SKIPPED runs and total
  silence: no parser was ever reached, and a `keywords.py` change alone would have been provably
  inert. Both `if:` expressions now also wake on the mention. The handle is hard-coded there
  because a workflow expression cannot read `.harness/config.json`, exactly as `MACHINE_MARKER` is
  hard-coded; B437 pins it to `stages.discover.machine_account`'s own derivation, the owner of
  `FORK_REPO`, so renaming the fork fails the build rather than silently deafening the bot.
- **The runner-minutes trade, accepted deliberately.** Any comment naming the bot now starts a run,
  prose included. `ack.yml` absorbs most of it: five-minute timeout, no install, no lock, tier 0.
  The machine-marker clause still guards the self-wake loop. There is no new exposure, because a
  stranger could already wake a full `feedback` run by typing `/harness`; the mention is a second
  word that does the same thing. Traffic here is low single digits a day.
- **A mention with no verb gets one nudge** naming the two or three verbs that make sense on that
  surface (`links.nudge` over `SURFACE_HINTS`). It is marked, so it does not wake the workflows.
  `mentions_without_command` is false whenever anything parsed, so an actor whose verbs were all
  above their level still gets the sweep's refusal, which names the level they needed, rather than
  a nudge that ignores what they asked for.

**The cancelled command lost nothing, and here is the proof.** The operator read the CANCELLED
`/harness status` run as a command discarded by the `/harness help` that followed. It was not.
`keywords.sweep` reads the inbox *before* `gh.notifications(since)` and independently of the
cursor: `if inbox_issue: read(self_repo, "inbox", int(inbox_issue))`. `since` bounds only the
notifications feed. The cancelled run was cancelled while queued, so it executed no step, marked
nothing seen and committed no ledger; the comment stayed unseen, and the next run's sweep found
both comments and answered both. What was real is the silence, and the latency behind it.

The one genuine loss window is narrower and is knowingly left alone: `commands_from` marks a
comment seen at *parse* time, before `_act_on_command` runs, and `cmd_sweep`'s `finally` saves the
ledger while `feedback.yml`'s `if: always()` step commits it. A run killed *after it has started*
therefore consumes commands it collected but never acted on. Moving `mark_seen` after the act
would reopen double-execution on a crash between acting and saving - paying for the same model
call twice - which is the worse failure. Recorded, not fixed.

- **The fast lane lives in `ack.yml`, and B118 is untouched.** The real defect behind complaint 4
  is that `status` and `help`, which spend nothing, queue behind a twenty-minute audit because
  `feedback.yml` is in the `harness-ledger` group. Sharding that group is off the table: every
  workflow that writes `state/ledger.json` shares one group and never cancels a run. Instead the
  free verbs move to the workflow that writes no ledger at all. `ack.yml` already takes no lock.
  `harness ack` gains a **read-only** ledger load - the contents API read of `harness-state` that
  `heartbeat.yml` already does with the run's own token - and never calls `ledger.save`. The proof
  obligation is discharged by B440, which asserts that `harness ack` writes no `state/ledger.json`
  and that `ack.yml` carries no commit step, so the ledger still has exactly one writer group.
- **What tells the sweep a comment is answered is a reaction, not text.** `ack.yml` leaves
  `gh.ANSWERED_REACTION` (`rocket`) on the comment it answered, under whichever login posted the
  answer, and only once `Say it` has succeeded: marking a comment nobody answered is silence,
  which is the failure this surface exists to prevent, while a reaction that fails to land costs
  a repeated answer. `keywords.answered_by_ack` reads that comment's reactions and skips it when
  one of `gh.machine_logins` left that content. It is asked only of a comment whose commands are
  all in `keywords.ACK_ANSWERS`, and `commands_from` has already marked the comment seen, so the
  cost is one read per status comment and never one per sweep. Nobody can react as those logins,
  and no text the harness republishes can become a reaction (B455). `eyes` means read, not
  answered, which is why the two contents differ. `ack` claims a comment only when *every* verb
  in it resolves to `status` - a mixed comment is left whole to the sweep, which owns the
  refusals for the rest.
- **The queue is published on the pinned issue.** A new `harness tidy` subcommand, called from one
  new `feedback.yml` step after the sweep and before the ledger commit, rewrites only the span
  between `<!-- queue:start -->` and `<!-- queue:end -->` on `TRACKING_ISSUE` through the existing
  `gh.update_issue_body`. If either marker is missing it writes nothing and says so - it never
  appends, because the rest of that body is a person's prose. If the rendered block is
  byte-identical it sends no request, so there is no edit noise in the timeline. The rows come
  from `links.queue_lines`, which `/harness status` now uses too, so the thread and the pinned
  issue cannot disagree. Cadence is every `feedback` run: the three-hourly cron, every command and
  every mention.
- **Hygiene, on the same command.** `gh.delete_issue_comment` deletes a comment only when **both**
  halves hold: one of the `gh.machine_logins` wrote it, *and* its body carries `MACHINE_MARKER`.
  This decision first said the marker alone was sufficient, and that was wrong. GitHub's **Quote
  reply** copies the source comment's raw markdown, HTML comments included, so a person who
  quote-replies the harness is carrying the marker in a comment they wrote - and the marker alone
  deleted it. Both comment-driven workflow `if:` filters already carried a `> <!-- ... -->` clause
  for exactly that reason. Nobody but the harness can post as either login, so author-and-marker
  is a test a person cannot pass. Only `INBOX_ISSUE` and `TRACKING_ISSUE` are swept. A comment is deleted only when it
  is **both** older than `PRUNE_AFTER_DAYS` (30) **and** outside the newest `PRUNE_KEEP` (20)
  machine comments on that issue, so recent context survives regardless of age and an old thread
  never empties. The prune runs at most every `PRUNE_EVERY_DAYS` (7), from a new ledger cursor
  `cursors["pruned_at"]`, which `to_json` renders only once it is set.

**Three defects this fixes, all found by reading the code rather than by a failing test.**
1. `harness/__main__.py` called `ledger_mod.Ledger.load(Path(config.ledger_path))` in two places.
   `Ledger` has no `load` classmethod - `load` is a module-level function - and `Config` has no
   `ledger_path`, which lives on `Context`. Both raised, and both were swallowed by a bare
   `except Exception`. So `_ack_halt_reason` never reported a **commanded** halt (its passing test
   only exercised the `.harness/HALT` branch above it, which returns first), and the audit-headroom
   check never fired, so `ack` promised "up to twenty minutes" for an audit `priority.admit` would
   refuse. Both now call `ledger_mod.load(ledger_path_for(config))`, and B438 covers each.
   `_ack_halt_reason` also read `halt["actor"]`, which `request_halt` never writes; it is `by`.
2. `heartbeat.yml` posts through `github-script`, so the transport never marked its comment, and
   its body names `` `/harness work <what>` ``. Every weekly heartbeat therefore woke both
   comment-driven workflows. The marker literal is now appended to the body (B454).
3. That unmarked heartbeat was also recorded by the sweep as a `keyword_denied` entry for
   `github-actions[bot]`, and was unprunable. Both follow from the fix.

**Three more the adversarial pass found in the first cut of this change, fixed before it merged.**
All three come back to one missing fact: the harness speaks under *two* logins, and the marker
alone identifies neither. `gh.machine_logins` is now the one place that names them.
1. The prune chose its candidates by `MACHINE_MARKER in body` with no author check. A reviewer ran
   the committed code against a quote-reply and a person's comment was deleted. Both halves are
   required now, at both sites that ask "did we write this?".
2. `keywords.answered_ids` required the machine account, which `ack.yml` never posts as, so the
   `answered:` marker was honoured only under the fake and never in production. Same helper.
3. `links.queue_block` interpolated row labels - which are issue titles, and on the product
   repository anybody can choose one - without neutralising the markers. A title carrying
   `<!-- queue:end -->` anchored the next splice inside the block and stranded a row below it on
   every sweep, without bound. Both markers are now defused in the rendered rows at that one
   choke point, before wrapping, as the entity form that renders the same and matches neither.

**The first cut of this decision put the answered fact in text, and that was the defect.** The
fast answer carried `<!-- answered:<comment id> -->` and `keywords.answered_ids` harvested the ids
out of harness-authored comments. Author-checking the harvest was not enough, because the attacker
never posts the marker - *the harness does*. It repeats untrusted text verbatim: a
product-repository issue title reaches a `/harness status` reply as a queue row label through
`priority.queue` and `links.queue_lines`, and a model's answer reaches a thread through any stage
reply. So an issue titled `fix the cards <!-- answered:IC_boss -->` made the harness publish the
marker itself, in its own marked comment, and the next sweep read a maintainer's command as
already answered and marked it seen for ever. A reviewer executed it end to end, with a clean
title as the control. Defusing the marker wherever it is published would have been a third round
of the same chase, so the fact left text altogether: `ANSWERED_RE`, `answered_ids`, the marker and
`ack --comment-id` are gone. `cmd_ack`'s `"answered"` key, dropped in the first cut as a key no
step read, is back and is now read - it is what `ack.yml` writes the reaction from - so the
command prints the same three keys on every path.

Rejected: sharding the `harness-ledger` concurrency group, which is B118 and would let two runs
write `state/ledger.json` at once; having `ack` record the answered fact in the ledger, which puts
it in that group and costs the fast lane the one thing it exists for; having the sweep write it
there instead, which is where the fact already ends up - the skip marks the comment seen - but
which answers where to keep it rather than how the sweep learns it, and so still needs a channel
of its own; `cancel-in-progress: true` on `feedback`, which would discard
in-flight work rather than queued work; deleting comments by age alone, which would eventually
reach a person's; a `queue.yml` workflow, which costs an `ADDED_WORKFLOWS` entry, a cron that must
dodge B124 and `FROZEN_CRONS`, and a second holder of the ledger lock, for a job one step does;
putting the queue in a comment rather than in the issue body, which grows the thread without bound
and is the thing the hygiene half exists to stop; reacting to a bare mention instead of replying,
which tells somebody they were heard when nothing will act on it - the failure
`test_ack_says_nothing_to_an_untrusted_commenter` exists to prevent.

Allocates B435-B456.

## D77 / B459-B471 - a block: the operator lends the harness sessions

Decision:
- `/harness block <n>` suspends the **run window** for the next `n` five-hour subscription
  sessions. Level 3 and a thirteenth verb, with `blocks` and `sessions` as aliases. There is no
  `unblock`: an alias carries the verb and never the argument, so it would resolve to a bare
  `block` and *report* rather than cancel. `/harness block 0` cancels, and every reply says so.
- The grant lives in `state/ledger.json` under `window["block"]`, beside `window["carry"]` and
  `window["halt"]` - the same class of fact, a scheduling exemption no store backend needs a
  column for. `to_json` writes it only once granted, so a ledger that has never seen one is
  byte-identical to the file it was.
- The end is measured **once**, when the command is acted on. With a live `five_hour.resets_at`
  it is that reset plus `n - 1` whole sessions; with no reading, an unreadable one, or one
  already past, it is `n x SESSION_HOURS` from now. `anchor` is recorded for the reply and never
  read again: re-measuring against each new observation would walk the block forward for ever.
- A missing signal is never unlimited, and a bad one cannot stretch a grant. `block_until` has no
  branch that returns nothing and clamps to `now + n x SESSION_HOURS`, so a reading the ledger
  copied verbatim - a seven-day value landing in the five-hour slot - grants n sessions and not
  days (B488). `block_open` is False when `until` is absent or unreadable - a block lifts a
  restriction, so anything unreadable about it must mean "not lifted". `MAX_BLOCK_SESSIONS` is
  6 (thirty hours), and a larger count is **refused** rather than clamped, because a clamp
  grants something other than what was asked for (the rule `trust.parse_trust` applies to a
  level out of range). `SESSION_HOURS` and `MAX_BLOCK_SESSIONS` are module constants, not config
  keys, as `HISTORY_CAP` and `PRUNE_KEEP` are.
- It lifts the window and nothing else: `dispatcher.plan` reads
  `in_run_window(...) or ledger.block_open(now)` on one line, and `cmd_run`'s no-`--item` branch
  does the same. `governor.authorize`'s two usage stops, `priority.admit`, `ctx.check_halt()`,
  `.harness/HALT`, the trust gate, both human gates and `MAX_CONCURRENT_ITEMS` are untouched. The
  usage stop is checked **before** the window branch in `plan`, so a block can never outlive one.
- It adds **no dispatcher reason literal**. A new one would have to be classified in
  `MUST_STOP_REASON_PREFIXES`/`MAY_PROCEED_REASON_PREFIXES` and would change `discover.yml`'s
  `case "$reason"` contract; the plan already tells the truth by starting the work. The block is
  reported in `cmd_dispatch`'s payload under `block`, which no workflow reads.
- It expires by the clock alone - nothing has to run for it to end. `cmd_sweep` additionally
  clears a spent grant when it happens to run, purely so the surfaces stay tidy; no decision
  depends on that cleanup happening, and `dispatcher.plan` stays pure and only reads.
- One renderer, `links.block_line`, is consumed by `/harness status`, `links.fast_status`, the
  pinned issue, `harness status` and `harness ledger`, so no two surfaces can disagree - the
  `queue_lines` pattern. `usage_headline` is untouched.

Why: the account's plan is five-hour sessions shared with the operator's own use, and D72 set the
window to the one session a day they are least likely to want (11:00-15:00 UTC). An operator with
an afternoon they are not going to use had no way to say so short of editing
`.harness/config.json` in a reviewed pull request, which is the wrong instrument for "today".

The honest limitation, stated in the reply and here: a block creates no workflow runs. Outside
11:23-14:23 UTC no `implement.yml` run is scheduled, so it takes effect on runs that already
happen - a gate-1 merge (immediately), `feedback.yml`'s three-hourly weekday sweep, which calls
`harness run` with no `--item`, or a manual dispatch. At a weekend `feedback.yml` does not run at
all, so the reply names `_next_scheduled(now)` and the operator sees the real latency.

Rejected: level 2 with a per-actor session cap, which splits "who spends the allowance" into two
rules that can disagree; widening `implement.yml`'s cron, which B412 makes a window change rather
than a cron change; giving the harness an Actions-dispatch write, a new write surface for a
scheduling convenience; re-measuring the end against each new reading, which never ends; clamping
an over-large count instead of refusing it.

Allocates B459-B471, and B488 for the clamp.

## D78 / B472-B479 - an approval survives the run that was cancelled

Decision:
- `harness approve --merged` reconciles every `proposed` work item against the proposal files on
  `main`: `list_work_items(state="proposed")`, then `proposals/<id>-*.md` by `PROPOSAL_FILE_RE`,
  then `proposed -> approved` with the reason `gate 1: proposals/<name> is on main`, which is the
  B101 comment the thread records. The item id becomes optional; `--merged` with an id is an
  error.
- **Only `proposed -> approved`.** What prevents a resurrection is the state machine together
  with the fact that nothing puts an item back into `proposed`: an item stopped after its proposal
  merged is `blocked` or `abandoned`, so it is left alone. The transition table does not itself
  forbid re-entry into `proposed`, and a `stage:` label set by hand is the route that reaches it,
  so a stage that ever moves an item back there makes a merged proposal a standing approval and
  has to be weighed against this.
- A failure on one item is logged, warned about, and the rest continue, and the command exits
  **0** (B489). It runs before `harness dispatch` and, in `feedback.yml`, before `harness sweep`,
  so a non-zero exit over one locked or transferred issue would stop `/harness halt`,
  `/harness resume` and `/harness block` being read at all, on every three-hourly run, while
  `ack.yml` kept acknowledging - and `ops.yml` retries neither an `IllegalTransition` nor a 403.
  `feedback.yml` carries `continue-on-error: true` on the step besides. The `failed` key stays in
  the payload, and an item that stops moving shows up in the pinned queue `harness tidy` rewrites
  and in `heartbeat.yml`'s weekly queue depth per `stage:` label. `watchdog.yml` does not report
  it: that one watches whether `feedback.yml`'s schedule fired at all (B294).
- It runs on **every** `implement.yml` run, not only on a push: the `github.event_name == 'push'`
  condition and the `BEFORE`/`AFTER` shell loop are gone, and the step keeps its place between
  sync-fork and dispatch (B127/B150). The same step is added to `feedback.yml` in the same
  position, so a burst merged outside the implement window is approved within three hours rather
  than waiting for 11:23 UTC. `ops.yml`'s `RETRYABLE_STEPS` names the new step, which runs before
  any spend in both workflows.
- Bounded by the queue rather than by `proposals/`, which only grows: one label query under the
  GitHub store, and an item that is no longer `proposed` is never revisited.

Why, with the evidence: the operator merged five proposal pull requests within 44 seconds. Each
merge pushed to `proposals/**` and started an `implement` run in the `harness-ledger` group.
`cancel-in-progress` is false, so GitHub kept one run in progress and **one** pending, and each new
arrival cancelled the previously pending one: four of five runs died without executing a step. The
approve step was gated on `github.event_name == 'push'` and read its own push diff
(`BEFORE..AFTER`) - a diff that exists only inside that event payload - so the four cancelled runs
never recorded their approvals, and all five items stayed at `stage:needs-approval` with nothing to
recover them. The cancellation is not the bug; the bug is that a cancelled run held the only copy
of work nothing else would redo. This is the same class as D46 and "`runs/` does not survive":
anything needed across runs must come from a durable source, and the committed proposal file is
one.

Against today's incident: the one run that survives checks out a `main` that already carries all
five files and approves all five. Every later run - cron, sweep, dispatch - re-reconciles for free,
so even if every push run had died the next scheduled run repairs it.

**B118 is untouched, and the proof is B479.** No workflow is added, no concurrency group is added
or changed, `cancel-in-progress` is still `false` on all three, and no new writer of
`state/ledger.json` exists: `harness approve --merged` runs inside jobs that already hold the lock.
The only new writer outside Actions is the operator's terminal, the seat `harness resume
--commanded` already occupies. Gate 1 is unchanged: merging the proposal is still the approval and
still takes effect on the run that merge triggers; it now also takes effect on the next run if that
one dies.

Rejected: a queued-run-aware trigger or a debounce, which needs state the push run writes - and the
push run is the thing being cancelled, often before any step runs; a second workflow outside the
group, which costs an `ADDED_WORKFLOWS` entry and makes a second ledger writer (B118);
`cancel-in-progress: true`, which discards in-flight work and was already rejected in D76;
sharding `harness-ledger`, which is B118 and off the table; "idempotent implement, let the next run
catch up" on its own, which fixes nothing, because what was lost was the *approval* and not the
implement work.

Out of scope and unchanged: D76's narrow comment-path window. `commands_from` marks a comment seen
at parse time, so a run killed *after starting* consumes commands it never acted on; moving
`mark_seen` after the act reopens paying twice for one model call. The operator's two cancelled
`/harness promote all` runs were cancelled while **queued**, executed no step and marked nothing
seen, so the next sweep re-read them - the comment path self-heals from its cursor where the push
path could not.

Allocates B472-B479, and B489 for the exit code.

## D79 / B480-B487 - status says what Actions is doing

Decision:
- `GitHubReadOnly.workflow_runs(repo, per_page=100)` reads
  `GET /repos/{repo}/actions/runs` and takes the list out of the object's `workflow_runs` key. A
  **read**: one GET through the same ETag-cached, metered path every other read takes, so nothing
  joins `GH_WRITE_METHODS` and I-13, which governs writes, does not apply. It is on
  `GitHubReadOnly` rather than `GitHubClient`, so `gh.public_reader()` inherits it. One page and
  no pagination - the endpoint lists newest first, and walking a history that grows with every
  comment is what `watchdog.yml` already refuses to do - so the page asked for is the largest the
  endpoint serves. Thirty rows was minutes on a busy morning, which put a live run off the page
  and reported "nothing running or queued" as fact during exactly the burst an operator would be
  investigating (B490).
- One renderer, `links.actions_lines`, between the queue and **Next** on every status surface:
  what is running, what is queued (marked "behind the ledger lock" for a workflow in
  `links.LEDGER_GROUP_WORKFLOWS`, which a drift test pins to the `group:` lines the workflow files
  declare, as B437 pins the machine handle), and one grouped line for what was cancelled in the
  last `CANCELLED_WINDOW_HOURS` (6). That last line is the operator's direct view of a burst
  (D78). Skipped runs are never listed: `feedback.yml` skips at job level on every unrelated
  comment, and that is the noise `watchdog.yml` already filters. Five rows, then `…and N more`.
- **An empty list never stands in for a failure**, and neither does a full page.
  `__main__._actions_rows` returns `(rows, error, truncated)`: "nothing is running", "I could not
  look" and "that is all I read" call for three different answers, so a page that comes back full
  is rendered as a bound - "nothing running or queued in the newest 100 runs" - and never as an
  idle queue.
  A `GitHubError`, a `RateCeilingReached`, a shape with no `workflow_runs` key, and a client with
  no `workflow_runs` attribute at all each cost exactly one line and leave the rest of the answer
  whole. The attribute test is `getattr(gh, "workflow_runs", None)`, the pattern
  `keywords.answered_by_ack` already uses for `comment_reactions`, which is why no existing test
  double needed a new method.
- `links.fast_status` carries the section too, because `ACK_ANSWERS == {"status"}` means the
  operator's own `/harness status` comment is answered by `ack` and skipped by the sweep - without
  it the one surface they asked about would never show it. `ack` reads it through
  `gh.public_reader()`: unauthenticated, no token, no store, no lock, tier 0 preserved, `ack.yml`
  still carrying no secret beyond `GITHUB_TOKEN` and still writing no ledger, so B440 and B118 are
  intact. Any failure there omits the section rather than spending the answer on an error line
  about a read nobody asked for: 60/hour is shared by every job on the runner's address, so a 403
  is routine rather than exceptional. `public_reader` bounds each read with
  `PUBLIC_READ_TIMEOUT_S`, since `urlopen` without one waits for ever and `ack.yml`'s job timeout
  was the only other limit (B491).
- `harness doctor` gains a probe beside `_doctor_notifications`: a **warning** only, never a
  problem, because a problem exits 3 and that exit code gates the spending workflows (D74/B305's
  rule). It is skipped below tier 2, where there is no token and nothing to check up front.

Why: from a thread, a harness that is thinking and a harness that is off look identical, and D65
answered that with `ack`. What it could not answer is the third state the operator actually hit:
queued behind the `harness-ledger` lock, or cancelled by a newer arrival. `/harness status` could
report the allowance, the queue and the window and still not say why nothing was moving, because
the reason was in the Actions tab.

Cost: one GET per status answer, on demand - nothing polls. At tier 2 it is metered against a
5000/hour ceiling and ETag-cached; in `ack` it is one unauthenticated request against the shared
60/hour, which is why every failure there is silent. The suite reaches no network: the three
`harness status` tests and the two `ack` helpers neutralise the reader, as the house rule requires
of any test that would otherwise read the host.

Rejected: paginating the run list, which grows without bound; putting the section behind a tier
gate, which would silence it exactly where the operator runs it locally; treating an empty list as
"idle", which is the failure this decision exists to prevent; a new write surface for a
workflow-dispatch, which nothing here needs.

Allocates B480-B487, and B490-B492 for the page, the omitted section and the defused rows.

## D80 / B493 - the window absorbs GitHub's lateness, the crons absorb its drops

Decision:
- `.harness/config.json` sets `RUN_WINDOW_END` to `daily 19:00`. `RUN_WINDOW_START` stays
  `daily 11:00`, so the window is eight hours wide rather than four.
- `implement.yml` fires at `23 11-18 * * *`: eight hourly passes, 11:23 to 18:23 UTC, was four.
- `discover.yml` fires at `7 11,13 * * *`: a second attempt, so a dropped 11:07 does not cost the
  day's triage.
- Unchanged: `feedback.yml` (`41 */3 * * 1-5`), `heartbeat.yml` (`5 9 * * 1`), `watchdog.yml`
  (`17 */4 * * *`) and event-driven `ack.yml`. Both usage stops, all three kill switches, the
  trust gate, both human gates and `MAX_CONCURRENT_ITEMS` are untouched.
- B412 is unchanged in its logic and still enforces the relationship: every discover and implement
  firing sits inside the configured window. Only the frozen values move. B493 is new - each
  spending workflow schedules at least two distinct firings.
- No reason literal moves. `dispatcher._window_reason` builds the string from the config through
  `config.run_window_label`, so it reads `outside run window (daily 11:00-19:00 UTC)` by itself.

Why, measured rather than assumed. GitHub delivers scheduled runs late, and drops some entirely.
Eleven scheduled runs over two days, each timed against the cron that should have produced it:

| Workflow | Arrived (UTC) | Cron due | Late |
|---|---|---|---|
| feedback | 09-16 17:16 | 15:41 | 96 min |
| watchdog | 09-16 19:35 | 16:17 | 198 min |
| feedback | 09-16 21:42 | 21:41 | 2 min |
| watchdog | 09-16 23:00 | 20:17 | 163 min |
| watchdog | 09-17 04:59 | 04:17 | 42 min |
| feedback | 09-17 05:23 | 03:41 | 102 min |
| feedback | 09-17 12:05 | 09:41 | 144 min |
| watchdog | 09-17 13:34 | 12:17 | 77 min |
| discover | 09-17 15:31 | 11:07 | 264 min |
| implement | 09-17 15:34 | 14:23 | 72 min |
| feedback | 09-17 17:15 | 15:41 | 95 min |

Medians: feedback 96, watchdog 163, discover 264, implement 72. Nothing arrived on time. Drops as
well: four implement firings produced one run on 09-17, and feedback fired twice in eight weekday
slots.

What that cost: on 09-17 the only implement run arrived at 15:34, 34 minutes after a window that
shut at 15:00. The dispatcher refused it with `outside run window (daily 11:00-15:00 UTC)` -
correctly, by D72's rule - and five approved items built nothing all day. D72's four-hour window
was narrower than the delivery lateness it had to survive, which nothing in D72 had measured.

Both halves are one decision because neither works alone. A wider window still needs a firing to
survive, and a day whose firings are all dropped builds nothing however wide it is. More firings
do not help by themselves either: lateness is correlated, since a run is late because GitHub's
scheduler is behind, so extra firings arrive late together and pile up past a boundary that has
not moved. Width absorbs the lateness; the repeated firings absorb the drops.

Cost, stated plainly: a build starting at the far edge of the window runs into a second five-hour
session and into the operator's Pacific noon - 19:00 UTC is 12:00 PDT, 11:00 PST - rather than
finishing before they are awake, which is part of what D72 bought. What bounds it is
`SESSION_USAGE_STOP_PCT` at 80, which stops new calls before a session is spent, and
`MAX_CONCURRENT_ITEMS` at 1. An operator who wants the quiet afternoon back moves `RUN_WINDOW_END`
and the implement cron together, as D72 and this decision both did.

This supersedes the schedule half of D72: its window (`daily 11:00` to `daily 15:00`) and its
crons (`7 11 * * *`, `23 11-14 * * *`) are history. The rest of D72 stands - the `daily` form, the
mixed-pair startup error, the wrap rule, the reason wording and B413's carry behaviour.

It also reverses one line of D77, which rejected "widening `implement.yml`'s cron, which B412
makes a window change rather than a cron change". That reasoning is why this is a window change
*and* a cron change, moved together in one commit, with B412 unchanged and still enforcing the
relationship between them. What D77 lacked was the table above.

Rejected: leaving the window at four hours and relying on `/harness block`, a manual instrument
for an outage that recurs daily; firing every 20 minutes, which multiplies runs queued against the
`harness-ledger` lock without making any one of them earlier; treating the 15:34 run as an
anomaly, which eleven measurements refuse; moving the window later rather than widening it, which
trades a morning the harness reliably gets for an afternoon it competes for.

The same lock argument applies to this change at a smaller scale, and is accepted rather than
dismissed. `discover`, `feedback` and `implement` share `harness-ledger`, GitHub keeps one pending
run per group and cancels the older, so doubling implement's firings widens the span in which a
queued `feedback.yml` run can be displaced: the slots at risk go from 12:41 and 15:41 to those two
plus 18:41. That matters because the keyword sweep is what applies a commanded `/harness halt` —
`ack.yml` only acknowledges, and `cmd_ack` writes nothing. The other two kill switches are
untouched: `.harness/HALT` is read in implement's first step, before checkout, and `HALT_FILE` is
local. `watchdog.yml` already counts a cancelled `feedback` run as a missed slot and pages at four.

Allocates B493.

## D81 / B494-B497 - the carry leeway bounds the carry's exemption, and refused work never starts

Decision:
- `dispatcher.usage_stop(carry=True, now=...)` applies `OVERRUN_PCT` only while the carried item
  runs on its exemption from the run window: the window is weekly and closed, and no block
  stands. Otherwise the carried item is held to `WEEKLY_USAGE_STOP_PCT` and
  `SESSION_USAGE_STOP_PCT` like any other item, so under a daily or unset window `OVERRUN_PCT`
  has no effect. The plan and the governor both pass their clock and read this one rule; without
  a clock the leeway applies.
- `dispatcher.plan` skips the carried item with its stop reason when that rule refuses it,
  forced or not, because the governor judges it as the carry either way.
- `dispatcher.plan` takes `suggested_refused`, the answer of `priority.admit("suggested", ...)`,
  and skips each suggested candidate with it. `harness dispatch` asks once and reports the same
  answer; the local loop asks only when a suggested candidate exists.
- `harness run` asks, before each clone, what the item's first model call would ask: the
  priority gate for an item that is not resumed, then `Governor.refusal`, which is `authorize`'s
  own check. A refused item prints `item N waits: <reason>`. A priority refusal belongs to one
  item and the loop goes on; a usage stop or rate limit refuses every item and the loop ends.
- `doctor` counts `repo` as holding `public_repo`, because GitHub grants it as part of `repo`.
  `repo` is still reported as broader than the harness needs.

This narrows D33 in two places. The leeway bounds the carry only outside a weekly window, and a
stop met before an item's first call is no handoff, because nothing has been done to hand off.
A stop inside work that has started is still a handoff and still carries.

Why. On 09-17 at 12:10Z item 50 (`via:suggested`) was planned, cloned, and refused at its first
model call by the priority gate, because #66-#70 were outstanding. The refusal is a
`BudgetExhausted`, so the run handed the item off, and the handoff put it in the carry slot. From
then on every in-window run did the same thing. The plan found the leeway spent (weekly usage at
27%, then 87%, against 10%), so the carry got no priority, but usage was under the 90% stop, so it
planned item 50 again as the oldest ordinary candidate. The governor judged it as the carry,
refused it on the leeway, and the run handed it off again. Eleven runs from 09-18 to 09-22 ended
this way, and #51-#54 waited behind it with `slots full`.

The two halves disagreed about what the leeway is. B209 says a carry past its leeway "waits like
everything else"; B206 and B208 held the carried item to the leeway at every hour. D33 grants the
carried item a resume "even outside the run window", and bounds that grant so it cannot quietly
consume the new week. Inside the window there is no grant to bound, and applying the leeway there
made the carry the one item held tighter than all the others. The B206 and B208 leeway tests now
run outside a weekly window, where the rule and its reason are unchanged.

One gap is accepted. A carry that starts inside a weekly window and is still running when the
window closes meets the leeway from that moment, and may be handed off; it goes first when the
window reopens. A daily window never switches, because under one the carry has no exemption.

Starting work that admission refuses cost a clone and `npm ci` on every run, and the handoff that
followed took the carry slot. The carry resumes through `continue`, which is class `unblock`, so a
carried suggested item also steps around the gate that refused it. Asking first starts nothing
and carries nothing. Because a priority refusal no longer ends `harness run`, one run can reach
several approved items after it, as a run with no refusal already could.

Item 50 stays in the ledger's carry slot. Its proposal was approved at gate 1, and the carry is
how it resumes its fork branch, so it goes first in the next window.

Rejected: skipping a carry the plan cannot admit inside the window, which holds that item until
the next weekly reset while everything else runs; clearing the carry once its leeway is spent,
which loses the `continue` path for work already on the fork; handing off without a carry on a
priority refusal, which still clones and comments on every run.

Allocates B494-B497.

## D82 / B498-B500 - every harness commit credits the operator as co-author

Decision:
- A new optional key, `CO_AUTHOR`, holds one `Name <email>`. When it is set, every commit the
  harness makes ends with `Co-authored-by: <CO_AUTHOR>`: the implementation commit, each gate-fix
  and self-audit-fix commit, the fallback message, and a handoff's wip commit. Empty or absent
  adds nothing, so an existing `.env` keeps loading.
- The value must be printable, match `Name <email>` exactly, and give a trailer of at most 100
  characters, so `commitmsg.build` never wraps it and no value can add a line to a commit.
- `CO_AUTHOR` joins `CONFIG_JSON_KEYS`, which now holds twenty keys, and `.harness/config.json`
  sets it to `jgoetzmann <95732896+jgoetzmann@users.noreply.github.com>`. Like `INBOX_ISSUE`,
  who the repository credits is state every runner agrees on, so it is committed.
- The commit author stays `Bright Bots Harness`. The push guard recognises the harness's own
  commits by author email (B139, B297), so the credit goes in a trailer.

Why. GitHub credits a squash commit to the pull request's author, which for a delivery is
`jgoetzmann-bot`, and to each `Co-authored-by` trailer in its message. Brightboost's squash
default keeps the commit messages as the body, and multi-commit squashes there already carry
their trailers into `main`. The noreply address is the one GitHub ties to the account without
publishing a mailbox.

The credit depends on whoever merges keeping that body. The one delivery merged so far, #868,
lost its whole body at merge, `Refs: #4` included, so a trailer would not have reached `main`
either. Nothing the harness writes survives an edited squash message.

Rejected: making the operator the commit author, which breaks the push guard's walk and claims
work the operator did not type; deriving the name and address from `.harness/trust.txt` and the
GitHub API, which adds a network read to every commit for a value that changes about never.

Allocates B498-B500.

## D83 / B501-B503 - a delivery lists its work on the product repository too

Decision:
- When `deliver` opens the pull request for an item the product repository has no issue for (its
  reference is not `issue:<n>`: an audit finding, a request made in words, a decomposed part), it
  first files one issue there. The issue carries the proposal's diagnosis, a link to the harness
  issue, and a marker naming the work item. The harness issue gets a comment naming it, and the
  pull request's title and `Closes` line name it, so merging closes it.
- A later delivery of the same item looks among the issues the machine account opened for that
  marker and reuses the issue it finds, so a revise cycle or another runner files nothing more.
  Nothing new is stored: the marker on GitHub is the record.
- An item that came from a product issue is listed there already and files nothing, and so is
  a decomposed part whose parent came from one. `COMMENT_UPSTREAM=false` turns this off with the
  harness's other writes on product threads. A failure to file, or a dry run's issue 0, is
  recorded, and the pull request opens without the `Closes` line.
- A filed issue is never new work. `links.item_of_product_issue` reads its marker, for an issue
  the machine account opened, and assigned and directed discovery, triage, and a `/harness`
  command on that thread all resolve it to the item it was filed for.
- The issue is titled `harness-tracking(#<item>): <title>` and requests the label
  `harness-tracking`. It opens with a note saying it is a harness tracking issue, linking the
  approved plan and, once the pull request opens, the pull request, and asking for comments there
  rather than on the issue. After the pull request opens, `deliver` rewrites that note through
  `edit_product_issue`, the second method the exception allows. The body promises no closing,
  since a pull request can fail to open or be stopped.
- The label and a locked conversation both need triage or write access on the product
  repository, which the machine account does not hold. GitHub drops the label in silence without
  it, and nothing locks the issue, so the note does that job until a maintainer grants access.
- I-14 gains this one exception. `GitHubClient.create_product_issue` and `edit_product_issue`
  take no repository argument, write only to `/repos/{self.repo}/issues` and to one issue under
  it, and are named by no module but `deliver.py`; a test pins all three and that no other gh.py
  write reaches an `/issues` collection. Both join I-13's write set, with `update_issue_body`,
  which that set had missed.
- `prompts/system.md` says the harness files this one issue itself, so `.harness/PIN` is
  regenerated.

Why. Work the harness found on its own reached brightboost as a pull request with nothing to
close, so it appeared in the product repository's issue list nowhere, and a maintainer triaging
issues never saw it. The operator asked for each item to be listed on both repositories. Filing
at delivery, rather than at discovery or proposal, lists only work a person approved at gate 1
and the harness has finished, so the product repository never collects issues for work nobody
has agreed to.

Rejected: filing at proposal time, which lists work that gate 1 may refuse; storing the filed
number on the work item, which needs a store column and a layout bump for a fact the marker
already keeps on GitHub; a separate config switch, when `COMMENT_UPSTREAM` already governs the
harness's writes on product threads.

Review findings kept as they stand: an issue filed for an item whose pull request never opens,
or is stopped, stays open, naming the harness issue. It describes a problem a person approved at
gate 1, which is worth listing whether or not this fix lands.

Allocates B501-B504.

## D84 / B505-B506 - a comment is never lost to a notification that arrives late

Decision:
- `keywords.sweep` asks the notifications feed from `SWEEP_OVERLAP_MINUTES` (30) before its
  cursor, not from the cursor itself. The cursor still moves to the run's start. Seen comment
  ids, which are never pruned, keep the overlap from answering a comment twice.
- `harness sweep --thread N` also reads issue or pull request N in this repository directly, as
  a proposal when it is a pull request. `feedback.yml` passes the number of the issue or pull
  request its comment event names, so a command is answered by the run it starts.

Why. The run a comment starts reaches the sweep about a minute later, and GitHub can deliver the
comment's notification later than that. The sweep then saw no thread, moved the cursor past the
comment, and no later sweep asked the feed for anything that old. Two `/harness promote`
commands were lost this way, on #60 on 09-17 and on #61 on 09-23, each with no reply. The
overlap lets a later sweep find a comment the first one missed; the direct read answers it in
the same run.

Rejected: holding the cursor at the newest comment seen, which cannot move on a quiet feed and
re-reads it all; waiting before the sweep, which slows every command to cover a delay GitHub
does not bound.

Allocates B505-B506.

## D85 / B507 - the sweep reads threads already marked read, inside its window

Decision:
- `keywords.sweep` asks the notifications feed for read threads as well as unread ones
  (`all=true`), inside the window D84 set: 30 minutes before the cursor, or the first sweep's
  3-hour lookback. `GitHubClient.notifications` takes `include_read` for this.
- A thread the feed names is read inside that window only: a comment updated before it is
  skipped. The inbox and the thread a comment event names are still read whole, as before.
- `doctor` probes the feed with `since` set to now, so it asks one page, not every unread thread.

Why. GitHub marks a thread's notification read when the account itself acts on that thread, as
the harness does when it replies. A command posted on such a thread just before was hidden from
every later sweep, which asked for unread threads only. The #64 promote on 09-23 was lost this
way: D84's re-read reached back to it, but its thread was already marked read. D84's direct read
covers a harness thread only when the run its comment starts is the one that sweeps, and the
`harness-ledger` group can replace a queued run; brightboost threads are read by the scheduled
sweep alone. Reading read threads covers both.

Reading them removes what the unread flag used to hide: threads the harness has already
answered. Seen comment ids answer each comment once, but a lost ledger forgets them all, and the
next sweep would then replay every old command on those threads. Bounding feed threads to the
window keeps a replay inside it.

Rejected: trusting the unread flag, which the account's own activity changes; reading every
thread the harness knows on each sweep, which multiplies requests to cover what the window
already bounds; bounding the inbox too, which B439 keeps whole so a command whose run was
cancelled is still found.

Allocates B507.

## D86 / B508 - a merged delivery marks its item done

Decision:
- `deliver.mark_merged` looks up the delivery pull requests of each item at `shipped` or
  `needs-human`, on the upstream repository, from the branch the machine account pushed. It keeps
  only rows whose head ref and owner match, whatever GitHub's filter returned, and judges the
  newest. Once that one has merged, the harness issue is closed as completed through
  `GitHubClient.close_issue`, which takes no repository and closes only in `SELF_REPO`, and then
  the item moves to `merged` (`stage:done`), by way of `shipped` from `needs-human`.
- The close comes first: it is idempotent and a closed issue still lists, so an item a failure
  leaves behind is finished by the next run. Every failure is recorded and never fatal.
- `harness tidy` calls it first and on its own, so the queue publishes whatever it raises. One
  read per item beyond the listing. No workflow changes.
- A delivery pull request closed without merging leaves the item where it is; `/harness stop`
  parks it at `stage:blocked`. The harness issue is closed, not deleted: GitHub lets only an admin
  delete an issue, and the issue is the item's record.

Why. Nothing set `stage:done`; the operator relabelled each issue by hand. Until they did, an
item stayed outstanding, so `depends_on` waited on it and suggested work stayed behind it. An
item at `needs-human` whose pull request a maintainer finished and merged had the same problem.

Rejected: deleting the harness issue once done, which needs admin rights the machine account does
not hold and removes the record `depends_on`, the store and the ledger read; a `to:delete` label,
which the operator dropped in favour of `stage:done` alone; trusting GitHub's head filter and any
merged row, which lets an older merged pull request finish an item whose newer one is open.

Allocates B508.

## D87 / B509 - a timer call is not a timeout

Decision:
- B64's timeout arm no longer reads a timer call (`setTimeout(…)`, `window.setTimeout(…)`,
  `clearTimeout(…)`) as a timeout. `jest.setTimeout(n)`, `timeout: n` and `testTimeout: n` are
  still caught, `0` included, since some runners read `0` as no limit.

Why. The arm matched `timeout` followed by a number anywhere on an added line, so
`setTimeout(resolve, 0)` in #54's regression test blocked a change whose four gates had passed,
as "introduces a timeout (added 0, previous none)". A timer call schedules work after a delay and
bounds nothing; the arm exists to stop a change from buying time from a test runner or a job.

Rejected: ignoring a zero, which some runners read as "never time out"; limiting the arm to test
files, which would miss a raised limit in a runner's config.

Allocates B509.
