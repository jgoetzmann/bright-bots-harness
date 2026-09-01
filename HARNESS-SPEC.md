# Bright Bots Harness — Implementation Spec

**Version:** 1.1 · **Delivery:** 1 (phases P0–P2) · **Target repo:** `jgoetzmann/Bright-Bots-Harness` (private, to be created)
**Product repo under test:** `Bright-Bots-Initiative/brightboost` (public)
**Status:** frozen for implementation. Changes during the run are the next run's problem.

---

## 0. How to read this

This document is the single source of truth for Delivery 1. It is written to serve three readers:

- **An implementing agent.** §3–§6 are the build. §5 freezes every cross-module signature so slices can be
  written in parallel without reading each other.
- **Fullsend.** §1, §5, §6, §11 map onto Phase 0's required `## Goal`, `## Surface`, `## Behaviors`,
  `## Out of scope`, `## Stack`. Copy them to `.fullsend/SPEC.md` verbatim if running fullsend on this build.
  The behavior count (78) exceeds fullsend's suggested 15–40 because this is a nine-slice system, not one
  slice; per slice the density is right.
- **A reviewer.** §10 is the acceptance gate. The companion `HARNESS-REVIEW.md` turns it into a runnable
  protocol.

Behaviors are numbered `B1`–`B78` and are the currency of the run: tests cite them, review scores against
them, and anything reaching neither a behavior nor a §4 manifest row is out of scope by construction.

**Requirement levels.** MUST / MUST NOT are acceptance-gating. SHOULD is a strong default that may be
overridden with a recorded reason in `DECISIONS.md`. MAY is free choice.

---

## 1. Goal

A locally-run, manually-started Python service that takes work on the `brightboost` repository from discovery
through to a human-reviewable package, spending a metered amount of Claude capacity and touching nothing
outside its own directory.

At the end of Delivery 1, this command sequence works end to end and produces an artifact a maintainer who
has never seen the harness can act on:

```
harness discover --mode directed --target 816
harness propose 1
harness approve 1
harness run --item 1
harness package 1
```

The output is a directory containing a diagnosis, a patch series pinned to an exact base commit, verbatim
evidence that the repository's own gates pass, and a log of every decision taken to get there.

**Delivery 1 is Tier 0: the harness performs no authenticated request and no write of any kind against
GitHub.** That is enforced structurally (§9), not by policy.

---

## 2. Scope and non-goals

### In scope

| Capability | Notes |
|---|---|
| Config from `.env` | §5.1 |
| Durable work queue + state machine | SQLite, §5.2 |
| Budget accounting and admission control | §5.3 |
| Claude CLI runner + deterministic fake runner | §5.4 |
| Unauthenticated GitHub reads with caching | §5.5 |
| Collision detection from branch names | §5.6 |
| Disposable clone lifecycle | §5.7 |
| Secret redaction | §5.8 |
| Stages: discover, propose, implement, package | §5.9 |
| CLI | §5.10 |
| Review package format | §7 |

### Explicit non-goals for Delivery 1

- **Ship and Watch stages.** No push, no PR, no comment, no issue filing. The code to do so MUST NOT exist
  yet (§9, I-1).
- **Concurrency.** The scheduler is serial. `max_concurrent_clones` is read and validated but a value above 1
  MUST cause a startup error (§6, B22).
- **Audit discovery mode.** `--mode audit` is accepted by the CLI parser but MUST exit 2 with
  `not implemented in delivery 1`.
- **Sub-issue decomposition and stacking.** `parent_id` and `depends_on` columns exist; nothing populates them.
- **The `api` runner backend.** The `Runner` protocol admits it; no implementation ships.
- **Any *use* of the bot identity.** The identity is fully specified in §13 and its readiness is *detected* and
  reported, but Delivery 1 cannot authenticate as it, cannot comment, and cannot push. A token present in
  `.env` MUST be refused at Tier 0 (§6, B80). This is deliberate: the identity has to be designed before it is
  needed, and it must be impossible to use before it is agreed.

---

## 3. Stack (frozen)

| Choice | Value | Rationale |
|---|---|---|
| Language | Python **3.13** | Matches the machine (3.13.5) |
| Runtime dependencies | **None.** Standard library only | A harness whose selling point is "holds no credentials" should not pull a supply chain to read a `.env` file. `sqlite3`, `urllib.request`, `argparse`, `subprocess`, `pathlib`, `dataclasses`, `tomllib`, `re`, `json`, `logging` cover everything. |
| Dev dependencies | `pytest==8.3.4` only | |
| Packaging | `pyproject.toml`, setuptools backend, `pip install -e .` | No extra build tool |
| Entry point | `harness` console script → `harness.__main__:main` | |
| Line length | 100 | Matches the product repo's commitlint ceiling; one number to remember |
| Formatting | Not enforced by CI in Delivery 1 | Do not add a formatter dependency; keep the tree hand-clean |
| External binaries | `git`, `claude`, `node`, `npm`, `npx` | Probed by `harness doctor` |
| Deliberately NOT used | **`gh` CLI** | `gh` authenticates transparently with the user's token. Using it would silently void the Tier 0 guarantee. All GitHub reads go through `urllib.request` with no `Authorization` header. This is an invariant (§9, I-2), not a preference. |

### 3.1 Verified environment facts

These were confirmed on the target machine on 2026-09-01 and MUST be re-probed by `harness doctor` rather
than trusted:

| Fact | Value | How it was verified |
|---|---|---|
| `claude` CLI version | 2.1.251 | `claude --version` |
| `claude --max-turns` | **Accepted, but undocumented in `--help`** | `claude -p --max-turns 1 --output-format json ""` returns the *input-missing* error, not an unknown-option error; a control test with `--definitely-not-a-flag` does produce `error: unknown option`. |
| `claude -p --output-format json` | Supported | `--help` |
| Python | 3.13.5 | `python --version` |
| Node | v22.18.0 (clones pin `engines: 20.x`) | `node --version` |
| `brightboost` visibility | **public** | GitHub API |
| `brightboost` CI secrets | **none** — zero `secrets.*` references across all four jobs | source inspection of `.github/workflows/ci-cd.yml` |
| Commit rules | `commitlint.config.cjs` extends `@commitlint/config-conventional`, enforced by `.husky/commit-msg` | source inspection |
| Pre-push hook | `npm run verify -- --skip-install --allow-skips` | `.husky/pre-push` |

Because `--max-turns` is undocumented, `harness doctor` MUST probe it (§6, B70) and the CLI runner MUST fail
loudly rather than silently running unbounded if the probe fails.

---

## 4. Repository layout

Every file in Delivery 1. A file not listed here MUST NOT be created; a row here MUST exist at review time.
Paths are relative to the repository root.

### 4.1 Package source

| Path | Purpose | Acceptance |
|---|---|---|
| `harness/__init__.py` | Version constant only | Exports `__version__: str`; no other symbol |
| `harness/__main__.py` | `argparse` CLI, subcommand dispatch, exit codes | Every subcommand in §5.10 parses; `main(argv: list[str] \| None = None) -> int` |
| `harness/config.py` | `.env` parsing → frozen `Config` | Only module in the package that reads `os.environ` (§9, I-4) |
| `harness/errors.py` | Exception hierarchy | All harness exceptions derive from `HarnessError` |
| `harness/store.py` | SQLite schema, migrations, all queries | No SQL string exists outside this module (§9, I-5) |
| `harness/governor.py` | Budget periods, estimates, admission | No stage may spend without an `Authorization` from here |
| `harness/collision.py` | Branch/title → claimed issue numbers | Pure functions, no I/O |
| `harness/redact.py` | Secret scrubbing | Applied to every artifact before it touches disk |
| `harness/gh.py` | Unauthenticated, cached, read-only GitHub client | No non-GET request anywhere (§9, I-1); MUST NOT import `identity` (§9, I-9) |
| `harness/identity.py` | Bot-identity readiness detection and `HUMAN.md` generation | Detects and reports; never authenticates in Delivery 1 (§13) |
| `harness/clone.py` | Disposable clone acquire/release, disk guard | Every clone it creates is under `runs/` |
| `harness/halt.py` | Kill-switch check | One function, called at every stage boundary |
| `harness/context.py` | `Context` object passed to stages | Wires store, config, governor, runner, gh, clock |
| `harness/clock.py` | Injectable time source | Tests never sleep or read the wall clock |
| `harness/gates.py` | Runs the product repo's gate sequence, captures verbatim output | Never modifies the tree it inspects |
| `harness/commitmsg.py` | Conventional-commit generation under the 100-char rules | Validated against the rules in §5.11 |
| `harness/prettier.py` | PR-scoped prettier invocation | Never invokes `npm run format` (§9, I-7) |
| `harness/packager.py` | Builds the review package (§7) | Output conforms byte-for-byte to §7.2 |
| `harness/runner/__init__.py` | Re-export `Runner`, `RunRequest`, `RunResult`, `get_runner` | |
| `harness/runner/base.py` | Protocol + dataclasses | No I/O |
| `harness/runner/cli.py` | `ClaudeCliRunner` | Builds argv per §5.4.3 |
| `harness/runner/fake.py` | `FakeRunner` reading fixtures | Deterministic; no network, no subprocess |
| `harness/stages/__init__.py` | Stage registry `STAGES: dict[str, StageFn]` | |
| `harness/stages/discover.py` | `discover()` | Triage + directed only |
| `harness/stages/propose.py` | `propose()` | Emits the work-package spec |
| `harness/stages/implement.py` | `implement()` | Gate loop, commits, fullsend fitness gate |
| `harness/stages/package.py` | `package()` | Delegates to `packager` |

### 4.2 Prompts

Prompts are data, not code. One file per stage, versioned, loaded by filename.

| Path | Purpose |
|---|---|
| `prompts/system.md` | Shared system prompt: identity, the never-list, house rules |
| `prompts/discover_triage.md` | Rank candidates from an issue/PR listing |
| `prompts/propose.md` | Produce the work package (§7.1) |
| `prompts/implement.md` | Implement against an approved spec |
| `prompts/implement_fullsend.md` | Fullsend variant, used only when the §5.9.3 fitness gate passes |
| `prompts/diagnose_gate_failure.md` | Read gate output, propose a fix |
| `prompts/README.md` | How prompts are versioned and what each receives |

### 4.3 Tests

| Path | Covers |
|---|---|
| `tests/conftest.py` | Fixtures: temp store, frozen clock, fake runner, sample config |
| `tests/test_config.py` | B1–B6 |
| `tests/test_store.py` | B7–B15 |
| `tests/test_governor.py` | B16–B23 |
| `tests/test_runner_cli.py` | B24–B30 (argv construction and parsing only; no live calls) |
| `tests/test_runner_fake.py` | Fake determinism |
| `tests/test_gh.py` | B31–B37 (against recorded fixtures, never the network) |
| `tests/test_collision.py` | B38–B41 |
| `tests/test_clone.py` | B42–B48 |
| `tests/test_redact.py` | B49–B52 |
| `tests/test_identity.py` | B79–B86 |
| `tests/test_stages.py` | B53–B64, driven by `FakeRunner` |
| `tests/test_cli.py` | B65–B72 |
| `tests/test_packager.py` | B73–B78 |
| `tests/test_invariants.py` | §9 I-1 … I-8, by source inspection |
| `tests/fixtures/gh/*.json` | Recorded GitHub responses |
| `tests/fixtures/runner/*.json` | Canned runner results keyed by stage |
| `tests/fixtures/gates/*.txt` | Sample gate output, green and red |

### 4.4 Root files

| Path | Purpose | Acceptance |
|---|---|---|
| `pyproject.toml` | Project metadata, console script, pytest config | `pip install -e .` succeeds on a clean venv |
| `.env.example` | Every key in §5.1 with a safe default | Contains no real value; `ANTHROPIC_API_KEY=` is present but empty |
| `.gitignore` | MUST contain `.env`, `runs/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `HALT` | §9, I-6 |
| `README.md` | What it is, install, the five-command walkthrough from §1, the safety model | Under 200 lines |
| `HALT.example` | Empty file documenting the kill switch | |
| `docs/SAFETY.md` | The invariants of §9, written for Nathaniel | Standalone; assumes no context |
| `docs/PACKAGE-FORMAT.md` | §7, extracted so a reviewer need not read this spec | |
| `HUMAN.md` | **Generated** by `harness setup` (§13.4). The list of things only a human can do: accounts, tokens, permissions, approvals | Committed. Contains no secret value, only names and scopes (§9, I-10) |

### 4.5 Generated at runtime (never committed)

| Path | Contents |
|---|---|
| `runs/<run-id>/` | Transcripts, raw logs, working state, the disposable clone |
| `runs/<run-id>/clone/` | The clone itself |
| `runs/<run-id>/transcript/<stage>.jsonl` | Full model transcript, redacted |
| `harness.db` | SQLite store (path configurable) |

### 4.6 Generated and committed

| Path | Contents |
|---|---|
| `packages/<item>-<yyyymmddThhmmssZ>/` | Promoted review package (§7.2). Written only by `harness archive`. |

---

## 5. Surface

Every signature below is frozen. Slices may be built in parallel against them without reading each other.

### 5.1 `harness/config.py`

```python
@dataclass(frozen=True)
class Config:
    backend: Literal["cli", "fake"]
    repo: str                        # "Bright-Bots-Initiative/brightboost"
    permission_tier: int             # MUST be 0 in delivery 1
    allowlist_label: str             # "harness-ok"
    weekly_budget_pct: float         # 0 < x <= 100
    session_budget_pct: float        # 0 < x <= 100
    reserve_pct: float               # 0 <= x < 100
    weekly_reset_day: str            # lowercase weekday name
    max_concurrent_clones: int       # MUST be 1 in delivery 1
    max_turns: Mapping[str, int]     # keys: discover, propose, implement, package
    max_retries_gates: int
    github_api_ceiling_per_hour: int
    min_free_disk_gb: float
    db_path: Path
    runs_dir: Path
    packages_dir: Path
    halt_file: Path
    fullsend_enabled: bool

def load_config(env_path: Path | None = None, *, environ: Mapping[str, str] | None = None) -> Config: ...
```

`.env` keys map to fields by upper-snake-case name (`WEEKLY_BUDGET_PCT` → `weekly_budget_pct`). Unknown keys
in `.env` are an error, not a warning — a typo'd budget key that silently defaults is exactly the failure this
system cannot have.

### 5.2 `harness/store.py`

```python
@dataclass(frozen=True)
class WorkItem:
    id: int
    kind: Literal["issue", "pr", "audit_finding"]
    external_ref: str          # "issue:816"
    title: str
    state: str
    parent_id: int | None
    depends_on: int | None
    tier_required: int
    spec_path: str | None
    package_path: str | None
    base_sha: str | None
    branch_name: str | None
    attempts: int
    created_at: str
    updated_at: str

@dataclass(frozen=True)
class StageRun:
    id: int | None
    work_item_id: int
    stage: str
    backend: str
    status: str
    started_at: str
    ended_at: str | None
    turns: int | None
    allowance_pct: float | None
    cost_usd: float | None
    exit_reason: str | None
    transcript_path: str | None

class Store:
    def __init__(self, db_path: Path) -> None: ...
    def migrate(self) -> None: ...
    def create_work_item(self, *, kind: str, external_ref: str, title: str,
                         tier_required: int = 0) -> int: ...
    def get_work_item(self, item_id: int) -> WorkItem | None: ...
    def find_by_ref(self, external_ref: str) -> WorkItem | None: ...
    def list_work_items(self, *, state: str | None = None) -> list[WorkItem]: ...
    def transition(self, item_id: int, to_state: str, *, reason: str) -> None: ...
    def update_work_item(self, item_id: int, **fields: object) -> None: ...
    def start_stage_run(self, work_item_id: int, stage: str, backend: str) -> int: ...
    def finish_stage_run(self, run_id: int, *, status: str, turns: int | None,
                         allowance_pct: float | None, cost_usd: float | None,
                         exit_reason: str | None, transcript_path: str | None) -> None: ...
    def append_event(self, work_item_id: int | None, level: str, message: str) -> None: ...
    def budget_period(self, unit: str, period_start: str) -> tuple[float, float]: ...
    def consume_budget(self, unit: str, period_start: str, amount: float) -> None: ...
    def cache_get(self, url: str) -> tuple[str | None, str] | None: ...   # (etag, body)
    def cache_put(self, url: str, etag: str | None, body: str) -> None: ...
    def record_api_call(self, url: str, status: int, cached: bool) -> None: ...
    def api_calls_since(self, iso_ts: str) -> int: ...
    def close(self) -> None: ...
```

#### 5.2.1 Schema (DDL, verbatim)

```sql
CREATE TABLE schema_version (version INTEGER NOT NULL);

CREATE TABLE work_item (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  kind          TEXT    NOT NULL CHECK (kind IN ('issue','pr','audit_finding')),
  external_ref  TEXT    NOT NULL,
  title         TEXT    NOT NULL,
  state         TEXT    NOT NULL CHECK (state IN
                  ('discovered','proposed','approved','implementing',
                   'packaged','shipped','blocked','abandoned')),
  parent_id     INTEGER REFERENCES work_item(id),
  depends_on    INTEGER REFERENCES work_item(id),
  tier_required INTEGER NOT NULL DEFAULT 0,
  spec_path     TEXT,
  package_path  TEXT,
  base_sha      TEXT,
  branch_name   TEXT,
  attempts      INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT    NOT NULL,
  updated_at    TEXT    NOT NULL,
  UNIQUE (external_ref)
);

CREATE TABLE stage_run (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  work_item_id    INTEGER NOT NULL REFERENCES work_item(id),
  stage           TEXT    NOT NULL CHECK (stage IN ('discover','propose','implement','package')),
  backend         TEXT    NOT NULL,
  status          TEXT    NOT NULL CHECK (status IN
                    ('running','ok','failed','halted','budget_exhausted','timeout')),
  started_at      TEXT    NOT NULL,
  ended_at        TEXT,
  turns           INTEGER,
  allowance_pct   REAL,
  cost_usd        REAL,
  exit_reason     TEXT,
  transcript_path TEXT
);

CREATE TABLE budget_period (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  unit         TEXT NOT NULL CHECK (unit IN ('allowance_pct','usd')),
  period_start TEXT NOT NULL,
  period_end   TEXT NOT NULL,
  allocated    REAL NOT NULL,
  consumed     REAL NOT NULL DEFAULT 0,
  UNIQUE (unit, period_start)
);

CREATE TABLE event (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  work_item_id INTEGER REFERENCES work_item(id),
  ts           TEXT NOT NULL,
  level        TEXT NOT NULL CHECK (level IN ('debug','info','warn','error')),
  message      TEXT NOT NULL
);

CREATE TABLE http_cache (
  url        TEXT PRIMARY KEY,
  etag       TEXT,
  body       TEXT NOT NULL,
  fetched_at TEXT NOT NULL
);

CREATE TABLE api_call (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  ts     TEXT    NOT NULL,
  url    TEXT    NOT NULL,
  status INTEGER NOT NULL,
  cached INTEGER NOT NULL
);

CREATE INDEX idx_work_item_state ON work_item(state);
CREATE INDEX idx_stage_run_item  ON stage_run(work_item_id);
CREATE INDEX idx_event_item      ON event(work_item_id);
CREATE INDEX idx_api_call_ts     ON api_call(ts);
```

#### 5.2.2 Legal state transitions

Any transition not in this table MUST raise `IllegalTransition`.

| From | To |
|---|---|
| *(new)* | `discovered` |
| `discovered` | `proposed`, `abandoned` |
| `proposed` | `approved`, `blocked`, `abandoned` |
| `approved` | `implementing`, `abandoned` |
| `implementing` | `packaged`, `blocked`, `abandoned`, `approved` *(reset for retry)* |
| `packaged` | `approved` *(revise)*, `abandoned`, `shipped` *(Tier 2, not in Delivery 1)* |
| `blocked` | `approved`, `abandoned` |
| `shipped` | *(terminal)* |
| `abandoned` | *(terminal)* |

### 5.3 `harness/governor.py`

```python
@dataclass(frozen=True)
class Authorization:
    id: str
    work_item_id: int
    stage: str
    granted_pct: float
    max_turns: int

class Governor:
    def __init__(self, store: Store, config: Config, clock: Clock) -> None: ...
    def current_period(self) -> tuple[str, str]: ...          # (start_iso, end_iso)
    def remaining_weekly_pct(self) -> float: ...
    def remaining_session_pct(self) -> float: ...
    def spendable_pct(self) -> float: ...                     # min(weekly, session) minus reserve
    def estimate(self, stage: str) -> float: ...
    def can_fund(self, stage: str) -> bool: ...
    def authorize(self, work_item_id: int, stage: str) -> Authorization: ...   # raises BudgetExhausted
    def record(self, auth: Authorization, *, allowance_pct: float, cost_usd: float | None) -> None: ...
    def begin_session(self, session_pct: float | None = None) -> None: ...
```

Estimates start from a static table and are refined by observed medians once at least three completed
`stage_run` rows exist for that stage:

| Stage | Initial estimate (% of weekly allowance) |
|---|---|
| `discover` | 0.5 |
| `propose` | 2.0 |
| `implement` | 8.0 |
| `package` | 0.5 |

### 5.4 `harness/runner/`

#### 5.4.1 `base.py`

```python
@dataclass(frozen=True)
class RunRequest:
    stage: str
    prompt: str
    system_prompt: str | None
    allowed_tools: tuple[str, ...]
    disallowed_tools: tuple[str, ...]
    max_turns: int
    cwd: Path
    timeout_s: int
    add_dirs: tuple[Path, ...] = ()

@dataclass(frozen=True)
class RunResult:
    ok: bool
    text: str
    turns: int | None
    cost_usd: float | None
    allowance_pct: float | None
    duration_ms: int | None
    session_id: str | None
    exit_code: int
    transcript: tuple[dict, ...]
    error: str | None

class Runner(Protocol):
    name: str
    def run(self, request: RunRequest) -> RunResult: ...

def get_runner(config: Config) -> Runner: ...
```

#### 5.4.2 `fake.py`

`FakeRunner` loads `tests/fixtures/runner/<stage>.json` (path injectable) and returns it verbatim. It MUST
NOT import `subprocess` or `urllib`. It exists so the whole state machine, governor and packager can be
exercised in CI at zero token cost.

#### 5.4.3 `cli.py` — argv construction (frozen)

```
claude
  --print
  --output-format json
  --max-turns <request.max_turns>
  --permission-mode acceptEdits
  --allowed-tools <comma-joined request.allowed_tools>
  [--disallowed-tools <comma-joined>]        # omitted when empty
  [--system-prompt <request.system_prompt>]  # omitted when None
  [--add-dir <dir> ...]                      # one flag per dir
  --                                         # end of options
  <request.prompt>
```

Rules:

- Invoked with `cwd=request.cwd`, `timeout=request.timeout_s`, `shell=False`, argv as a list.
- The environment passed to the subprocess MUST be a copy of the parent's with `ANTHROPIC_API_KEY` **removed**
  (Delivery 1 is subscription-backed; a stray key in the environment would silently change the billing pool).
- `--dangerously-skip-permissions` and `--allow-dangerously-skip-permissions` MUST NOT appear (§9, I-3).
- The JSON result is parsed defensively: `result`/`text`, `num_turns`, `total_cost_usd`, `duration_ms`,
  `session_id`, `is_error` are all optional. A missing field yields `None`, never a crash.
- Non-zero exit or unparseable stdout yields `RunResult(ok=False, error=<stderr tail, redacted>)`.

### 5.5 `harness/gh.py`

```python
class GitHubReadOnly:
    def __init__(self, repo: str, store: Store, clock: Clock, ceiling_per_hour: int) -> None: ...
    def issue(self, number: int) -> dict: ...
    def issues(self, *, state: str = "open", labels: Sequence[str] = ()) -> list[dict]: ...
    def pulls(self, *, state: str = "open") -> list[dict]: ...
    def branches(self) -> list[str]: ...
    def rate_budget_remaining(self) -> int: ...
```

- Base URL `https://api.github.com`. **No `Authorization` header is ever set.**
- `User-Agent: bright-bots-harness/<version>` and `Accept: application/vnd.github+json`.
- Every request first consults `http_cache`; a cached ETag is sent as `If-None-Match`. A `304` serves the
  cached body.
- Every request is recorded in `api_call`. If calls in the trailing hour would exceed
  `github_api_ceiling_per_hour`, raise `RateCeilingReached` **before** issuing the request.
- The module MUST NOT import `subprocess`.

### 5.6 `harness/collision.py`

```python
def issue_numbers_from_branch(branch: str) -> set[int]: ...
def issue_numbers_from_title(title: str) -> set[int]: ...
def claimed_issue_numbers(branches: Iterable[str], pr_titles: Iterable[str]) -> set[int]: ...
```

Branch patterns that MUST be recognised, drawn from live observation of the repository:

| Pattern | Example | Yields |
|---|---|---|
| `agent-<n>/...` | `agent-737/qtr-ceiling` | `737` |
| `fix-<n>/...` | `fix-801/ci-shell-gate-isolation` | `801` |
| `<word>-<letter>/<n>-...` | `agent-b/782-required-step-coverage` | `782` |
| `<owner>/<type>-<n>-...` | `jack/chore-740-parity-guards` | `740` |

Titles yield numbers only from explicit `#<n>` references and the GitHub closing keywords
(`close[sd]`, `fix(e[sd])`, `resolve[sd]`), case-insensitive.

**Why this module exists.** As of 2026-09-01, eight of the nine open PRs on the repository carry no
`closingIssuesReferences` at all, yet six of them are plainly working an open issue, identifiable only from
the branch name. Selecting on the GitHub link graph alone would duplicate another contributor's in-flight
work. This is the single highest-consequence correctness requirement in Delivery 1.

### 5.7 `harness/clone.py`

```python
@dataclass(frozen=True)
class Lease:
    run_id: str
    path: Path
    base_sha: str
    branch: str

class CloneManager:
    def __init__(self, config: Config, clock: Clock) -> None: ...
    def preflight(self) -> list[str]: ...          # empty list == ok
    def acquire(self, item: WorkItem) -> Lease: ...
    def release(self, lease: Lease, *, keep: bool) -> None: ...
```

- `acquire` clones `https://github.com/<repo>.git` — **https, unauthenticated, never ssh** — into
  `runs/<run-id>/clone`, records the resolved `HEAD` sha as `base_sha`, and creates branch
  `harness/<type>-<issue>-<slug>` without checking anything out elsewhere.
- `preflight` returns human-readable blockers: insufficient free disk, `git` missing, a `HALT` file present.
- `release(keep=False)` removes the clone directory in full. `keep=True` leaves it and records the path.
- The manager MUST NOT touch any path outside `config.runs_dir` (§9, I-8).

### 5.8 `harness/redact.py`

```python
REDACTION = "[REDACTED]"
def redact(text: str) -> str: ...
def redact_json(obj: object) -> object: ...
def write_redacted(path: Path, text: str) -> None: ...
```

Patterns that MUST be matched (case-sensitive where the vendor prefix is):
`sk-ant-[A-Za-z0-9_\-]{20,}`, `gh[pousr]_[A-Za-z0-9]{30,}`, `github_pat_[A-Za-z0-9_]{40,}`,
`AKIA[0-9A-Z]{16}`, `-----BEGIN [A-Z ]*PRIVATE KEY-----` through its matching footer,
`(?i)(authorization|api[_-]?key|secret|token|password)\s*[:=]\s*\S+`, and any value of an environment
variable named in `config`'s secret-bearing keys.

Every path that writes a transcript, log line, or package file MUST route through this module.

### 5.9 `harness/stages/`

```python
@dataclass
class Context:
    config: Config
    store: Store
    governor: Governor
    runner: Runner
    gh: GitHubReadOnly
    clones: CloneManager
    clock: Clock
    run_id: str

def discover(ctx: Context, *, mode: str, target: str | None, lens: str | None) -> list[int]: ...
def propose(ctx: Context, item_id: int) -> Path: ...
def implement(ctx: Context, item_id: int) -> Lease: ...
def package(ctx: Context, item_id: int, lease: Lease) -> Path: ...
```

#### 5.9.1 `discover`

- `mode="directed"`: fetch that one issue, create or return the work item. No model call.
- `mode="triage"`: fetch open issues and PRs and branches; exclude assigned issues, issues in
  `claimed_issue_numbers()`, and anything labelled `intern-starter`, `large`, or `architecture`; require the
  `allowlist_label` unless `--ignore-allowlist` is passed; then one model call ranks the survivors.
- `mode="audit"`: exit 2, `not implemented in delivery 1`.

#### 5.9.2 `propose`

One model call producing the work package of §7.1. Writes `runs/<run-id>/spec/<item>.md`, sets
`work_item.spec_path`, transitions `discovered → proposed`.

#### 5.9.3 `implement` and the fullsend fitness gate

Before implementing, evaluate the gate. **All five conditions MUST hold** for the fullsend path:

| # | Condition | Source of truth |
|---|---|---|
| F1 | The proposal declares ≥ 3 slices that can be described without reading each other | `spec.slices` |
| F2 | The proposal enumerates ≥ 15 numbered behaviors | `spec.behaviors` |
| F3 | The proposal is a decided spec, not discovery | `spec.open_questions` is empty |
| F4 | No path in the change set matches `prisma/`, `migrations/`, `backend/scripts/predeploy*`, or `.github/workflows/` | `spec.touched_paths` |
| F5 | `config.fullsend_enabled` is true | `.env` |

Fail any of these and the ordinary single-agent path is used, with the failing condition recorded in
`DECISIONS.md`. This is deliberate: fullsend's own guidance excludes single-file changes, existing production
code, and infrastructure others depend on, which describes most of the allowlisted queue.

The implement loop, either path:

1. Acquire a clone.
2. Run the implementation call.
3. Prettier the changed files (§5.11).
4. Generate a commit message (§5.11) and commit.
5. Run gates (§5.10 `gates.run_sequence`). Green → done.
6. Red → up to `max_retries_gates` diagnose-and-fix cycles. **A repeated failure signature terminates
   immediately** rather than consuming the remaining retries.
7. Still red → transition to `blocked`, keep the clone, record everything.

**A gate MUST NEVER be widened, skipped, given a longer timeout, or marked `continue-on-error` to reach
green.** A red the harness cannot fix honestly is a blocked item, not a passed one.

#### 5.9.4 `package`

Delegates to `packager.build()`; transitions `implementing → packaged`.

### 5.10 CLI (`harness/__main__.py`)

| Command | Effect | Exit codes |
|---|---|---|
| `harness init` | Create db, dirs, copy `.env.example` → `.env` if absent | 0, 1 |
| `harness doctor` | Probe binaries, versions, `--max-turns`, disk, halt file, config validity | 0 ok, 3 degraded |
| `harness setup [--tier N]` | Assess identity readiness and (re)generate `HUMAN.md` for the target tier, default `1` | 0 ready, 6 actions outstanding |
| `harness status [--json]` | Queue by state, budget remaining, in-flight runs | 0 |
| `harness discover --mode {triage,directed,audit} [--target N] [--lens L] [--ignore-allowlist]` | §5.9.1 | 0, 2 unimplemented |
| `harness propose <item-id>` | §5.9.2 | 0, 1 |
| `harness approve <item-id> [--note TEXT]` | `proposed → approved` | 0, 1 |
| `harness run [--item ID] [--session-pct P] [--until HH:MM]` | Serial loop over approved items | 0, 4 budget exhausted, 5 halted |
| `harness package <item-id>` | §5.9.4 | 0, 1 |
| `harness archive <item-id> [--with-transcript]` | Promote package into `packages/` | 0, 1 |
| `harness halt` | Create the halt file | 0 |
| `harness resume` | Remove the halt file | 0 |

Global flags: `--config PATH`, `--verbose`, `--json`.

### 5.11 House rules — `commitmsg.py`, `prettier.py`, `gates.py`

```python
# commitmsg.py
MAX_HEADER = 100
MAX_BODY_LINE = 100
def build(type_: str, scope: str | None, subject: str, body: str, footers: Sequence[str]) -> str: ...
def validate(message: str) -> list[str]: ...   # empty == valid
```

The product repo enforces `@commitlint/config-conventional` via `.husky/commit-msg`. That config caps the
**header at 100 characters and also caps every body line and every footer line at 100** — the body half is the
one commonly missed. It further requires a conventional type, a non-empty lower-case subject, and no trailing
period. `build()` MUST hard-wrap the body at 100 and MUST regenerate rather than truncate an over-long
subject.

```python
# prettier.py
def changed_paths(clone: Path, base_sha: str) -> list[str]: ...
def write_and_check(clone: Path, paths: Sequence[str]) -> tuple[bool, str]: ...
```

Runs `npx prettier --write --ignore-unknown -- <paths>` then `--check` on the same paths, scoped to added and
modified files versus the merge base — matching `scripts/pr-review-prettier-check.sh`. It MUST NOT invoke
`npm run format` or `prettier --check .`; whole-tree checks over-report badly on this machine because
`core.autocrlf=true` with no `.gitattributes`.

```python
# gates.py
@dataclass(frozen=True)
class GateResult:
    name: str
    argv: tuple[str, ...]
    exit_code: int
    stdout_tail: str
    stderr_tail: str

def run_sequence(clone: Path, *, baseline: bool) -> list[GateResult]: ...
def signature(results: Sequence[GateResult]) -> str: ...
```

The sequence, in order: `npx prisma generate`, `npm run lint`, `npm run typecheck`,
`(cd backend && npm run typecheck)`, `bash scripts/check-prisma-drift.sh`, `npm run test:unit`,
`npm run build`. A **baseline run on the untouched tree happens first**; any red present in the baseline is
recorded as pre-existing and MUST NOT be attributed to the change, nor used to justify loosening anything.
`signature()` produces a stable hash of the failing gate names and their first error lines, used to detect a
repeated failure.

### 5.12 `harness/identity.py`

```python
@dataclass(frozen=True)
class Prerequisite:
    id: str                      # "account", "token", "org-approval", "collaborator"
    title: str
    tier_required: int
    satisfied: bool
    actor: Literal["you", "nathaniel"]
    detail: str                  # what to do, in plain words
    verify: str | None           # a command that confirms it, when one exists

@dataclass(frozen=True)
class Readiness:
    current_tier: int
    target_tier: int
    prerequisites: tuple[Prerequisite, ...]
    ready: bool

class Identity:
    handle: str = "brightboost-harness"
    def __init__(self, config: Config, gh: GitHubReadOnly) -> None: ...
    def token_present(self) -> bool: ...
    def validate_shape(self, token: str) -> list[str]: ...    # never transmits
    def account_exists(self) -> bool: ...                     # unauthenticated GET /users/<handle>
    def assess(self, target_tier: int) -> Readiness: ...
    def load_token(self) -> str: ...                          # raises TierViolation when tier < 1
    def render_human_doc(self, readiness: Readiness) -> str: ...

def write_human_doc(path: Path, content: str) -> None: ...
```

`load_token` is the only function that returns the secret, and in Delivery 1 it always raises. `assess` and
`render_human_doc` work entirely from presence and shape — they never read the value. `account_exists` uses
the existing unauthenticated client, so checking whether the handle is taken costs no credential.

---

## 6. Behaviors

Numbered, testable, one line each. Tests cite these numbers.

### Config

- **B1.** `load_config` reads `.env` from the given path, falling back to `./.env`.
- **B2.** A missing required key raises `ConfigError` naming the key.
- **B3.** An unknown key in `.env` raises `ConfigError` naming the key.
- **B4.** `permission_tier` other than `0` raises `ConfigError` in Delivery 1.
- **B5.** `weekly_budget_pct`, `session_budget_pct` outside `(0, 100]` and `reserve_pct` outside `[0, 100)` raise `ConfigError`.
- **B6.** `Config` is frozen; attribute assignment raises.

### Store

- **B7.** `migrate()` on an empty file creates every table in §5.2.1 and sets `schema_version` to 1.
- **B8.** `migrate()` is idempotent; running it twice changes nothing.
- **B9.** `create_work_item` with a duplicate `external_ref` raises `DuplicateWorkItem`.
- **B10.** `transition` accepts every pair in §5.2.2 and updates `updated_at`.
- **B11.** `transition` rejects any pair absent from §5.2.2 with `IllegalTransition`, leaving state unchanged.
- **B12.** `transition` writes an `event` row recording the from-state, to-state and reason.
- **B13.** `start_stage_run` inserts with status `running`; `finish_stage_run` sets a terminal status and `ended_at`.
- **B14.** `consume_budget` is atomic: a failure mid-transaction leaves `consumed` unchanged.
- **B15.** `cache_put` then `cache_get` round-trips the ETag and body exactly.

### Governor

- **B16.** `current_period` returns the week bounded by `weekly_reset_day`, using the injected clock.
- **B17.** Crossing the reset boundary starts a fresh period with `consumed = 0`.
- **B18.** `spendable_pct` equals `min(weekly_remaining, session_remaining) − (allocated × reserve_pct)`.
- **B19.** `authorize` raises `BudgetExhausted` when `estimate(stage) > spendable_pct()`, and records nothing.
- **B20.** `authorize` returns `max_turns` from `config.max_turns[stage]`.
- **B21.** `record` increases `consumed` by the observed amount, not the estimate.
- **B22.** `max_concurrent_clones > 1` raises `ConfigError` at startup in Delivery 1.
- **B23.** After three completed runs of a stage, `estimate` returns the observed median rather than the static default.

### Runner

- **B24.** `get_runner` returns `ClaudeCliRunner` for `backend="cli"` and `FakeRunner` for `backend="fake"`.
- **B25.** `ClaudeCliRunner` builds argv exactly as §5.4.3, in that order.
- **B26.** The subprocess environment omits `ANTHROPIC_API_KEY` even when the parent sets it.
- **B27.** Neither skip-permissions flag ever appears in argv.
- **B28.** A well-formed JSON result populates `turns`, `cost_usd`, `duration_ms`, `session_id`.
- **B29.** Missing optional JSON fields yield `None` rather than raising.
- **B30.** Non-zero exit or unparseable stdout yields `ok=False` with a redacted stderr tail.

### GitHub read-only

- **B31.** No request carries an `Authorization` header.
- **B32.** A cached ETag is sent as `If-None-Match`, and a `304` serves the cached body.
- **B33.** Every request appends an `api_call` row, cached or not.
- **B34.** Exceeding `github_api_ceiling_per_hour` raises `RateCeilingReached` before any request is issued.
- **B35.** `issues(labels=[...])` sends the labels as a comma-joined query parameter.
- **B36.** `branches()` follows pagination to completion.
- **B37.** A `403` carrying a rate-limit body raises `RateCeilingReached`, not a generic HTTP error.

### Collision

- **B38.** Every pattern in §5.6's table extracts the expected number.
- **B39.** A branch with no issue number yields the empty set.
- **B40.** Titles yield numbers from `#N` and from closing keywords, case-insensitively.
- **B41.** Given the live 2026-09-01 fixture, `claimed_issue_numbers` returns `{681, 700, 734, 735, 736, 737, 801, 810, 640}` — the set derivable from branch names, none of which GitHub's own link graph reports.

### Clone

- **B42.** `acquire` clones over `https://` and the remote URL contains no credential and no `ssh`.
- **B43.** The clone lands under `config.runs_dir` and nowhere else.
- **B44.** `base_sha` equals the resolved `HEAD` of the clone at creation.
- **B45.** The created branch name starts with `harness/`.
- **B46.** `preflight` returns a blocker when free disk is below `min_free_disk_gb`.
- **B47.** `preflight` returns a blocker when the halt file exists.
- **B48.** `release(keep=False)` removes the directory; `release(keep=True)` leaves it and records the path.

### Redaction

- **B49.** Each pattern in §5.8 is replaced with `[REDACTED]`.
- **B50.** A key embedded mid-line is redacted without destroying the rest of the line.
- **B51.** `redact_json` walks nested dicts and lists, redacting string leaves.
- **B52.** `write_redacted` never writes an unredacted byte, verified by writing a known key and reading back.

### Stages

- **B53.** `discover --mode directed --target 816` creates exactly one work item in state `discovered` and makes no model call.
- **B54.** Re-running the same directed discover returns the existing item rather than creating a duplicate.
- **B55.** `discover --mode triage` excludes assigned issues.
- **B56.** `discover --mode triage` excludes every issue in `claimed_issue_numbers()`.
- **B57.** `discover --mode triage` excludes `intern-starter`, `large` and `architecture`.
- **B58.** `discover --mode triage` requires `allowlist_label` unless `--ignore-allowlist` is given.
- **B59.** `discover --mode audit` exits 2 without a model call.
- **B60.** `propose` writes a spec file, sets `spec_path`, and transitions to `proposed`.
- **B61.** The fullsend gate passes only when all five of F1–F5 hold; each individual failure is recorded in `DECISIONS.md`.
- **B62.** `implement` runs a baseline gate sequence before making any change, and records its result separately.
- **B63.** `implement` stops immediately when a gate failure signature repeats, without consuming remaining retries.
- **B64.** `implement` never emits a diff that modifies a CI workflow, a test timeout, or adds `continue-on-error`; such a diff is rejected and the item is blocked.

### CLI

- **B65.** Every subcommand in §5.10 parses and dispatches.
- **B66.** `harness init` is idempotent and never overwrites an existing `.env`.
- **B67.** `harness doctor` exits 3 and names each missing binary when one is absent.
- **B68.** `harness status --json` emits valid JSON with queue counts and budget remaining.
- **B69.** `harness run` checks the halt file before each stage and exits 5 when present.
- **B70.** `harness doctor` probes `--max-turns` acceptance and reports it explicitly, since the flag is undocumented in `claude --help` at 2.1.251.
- **B71.** `harness run --until HH:MM` stops starting new stages after that local time.
- **B72.** `harness archive` refuses an item not in state `packaged`.

### Packager

- **B73.** The package directory contains every file in §7.2, and nothing else.
- **B74.** `BASE` contains exactly the 40-character base sha and a trailing newline.
- **B75.** The patch series applies cleanly to `BASE` in a fresh clone and to no other commit.
- **B76.** `bundle.gitbundle` verifies with `git bundle verify`.
- **B77.** `EVIDENCE.md` contains verbatim gate output with exit codes, never a summary.
- **B78.** `harness archive` copies everything except `transcript.jsonl` unless `--with-transcript` is passed.

### Identity and human setup

- **B79.** `token_present()` is false when `HARNESS_GITHUB_TOKEN` is absent or empty, and true when it is set, without reading the value elsewhere.
- **B80.** `load_token()` raises `TierViolation` whenever `permission_tier < 1`, **even when a valid token is present**.
- **B81.** `validate_shape()` accepts `github_pat_…` and `ghp_…` shapes and rejects others, without issuing any request.
- **B82.** `harness setup` writes `HUMAN.md` to the repository root and exits 6 when any prerequisite is outstanding, 0 when none are.
- **B83.** `HUMAN.md` contains no secret value: scanning it with the §5.8 patterns yields no match, and the token's value never appears even when set.
- **B84.** A satisfied prerequisite is rendered as done rather than repeated as an outstanding action.
- **B85.** `HUMAN.md` names the exact fine-grained PAT permission set for the target tier, taken from §13.2.
- **B86.** `harness setup` is deterministic: the same readiness produces byte-identical output, so re-running it creates no spurious diff.

---

## 7. Artifacts

### 7.1 The work package (output of `propose`)

Markdown with exactly these sections, in this order. The parser depends on the headings.

```markdown
# <type>(<scope>): <one-line statement of the defect or change>

## Issue
Link, number, and the problem in the reporter's terms.

## Diagnosis
What is actually wrong, with file and line citations. Evidence, not assertion.

## Approach
What will change and why this way.

## Slices
Numbered, independently describable units. Three or more enables the fullsend path.

## Behaviors
Numbered, testable, one line each. Fifteen or more enables the fullsend path.

## Acceptance criteria
What must be true for a reviewer to accept. Each line independently checkable.

## Decisions
Every decision made, with the alternatives rejected and why.

## Open questions
Anything that cannot be decided without a human. Non-empty blocks the fullsend path
and, if any question is load-bearing, blocks the item.

## Touched paths
Every path expected to change. Compared against the actual diff at package time.

## Risks
What could go wrong, and what the reviewer should look hardest at.
```

### 7.2 The review package (output of `package`)

```
runs/<run-id>/package/
├── README.md            entry point: what this is, what changed, how to verify
├── DIAGNOSIS.md         from the work package
├── DECISIONS.md         every decision, including fullsend-gate outcomes
├── EVIDENCE.md          verbatim gate output, baseline and post-change, with exit codes
├── ACCEPTANCE.md        the criteria, each marked met or not, with evidence
├── BASE                 the 40-char base sha, one line
├── manifest.json        machine-readable summary (schema below)
├── patches/
│   ├── 0001-<slug>.patch
│   └── ...
├── bundle.gitbundle     the branch, fetchable without any remote
└── transcript.jsonl     full redacted model transcript (excluded by archive unless asked)
```

`manifest.json`:

```json
{
  "schema": 1,
  "item_id": 1,
  "external_ref": "issue:816",
  "repo": "Bright-Bots-Initiative/brightboost",
  "base_sha": "…40 chars…",
  "branch": "harness/fix-816-bundle-size-esm",
  "created_at": "2026-09-01T00:00:00Z",
  "harness_version": "1.0.0",
  "backend": "cli",
  "fullsend": false,
  "fullsend_gate": {"F1": false, "F2": false, "F3": true, "F4": true, "F5": true},
  "stages": [{"stage": "propose", "turns": 12, "allowance_pct": 1.8}],
  "gates": [{"name": "npm run lint", "exit_code": 0}],
  "patch_count": 1,
  "touched_paths": ["scripts/check-bundle-size.js"]
}
```

**Reconstruction contract.** A reviewer with nothing but this directory MUST be able to run:

```bash
git clone https://github.com/Bright-Bots-Initiative/brightboost.git r && cd r
git checkout "$(cat ../BASE)"
git am ../patches/*.patch
```

and obtain a tree identical to the harness's, with no network access to anything but the public product repo.

---

## 8. Prompts

Prompts live in `prompts/` as Markdown, are loaded by filename, and are rendered with a strict
`string.Template` substitution — no f-strings, so a stray brace in repository content cannot break rendering.

`prompts/system.md` MUST state: the harness's identity and Tier; that it has no credentials and cannot push;
the never-list (no widening a gate, no touching `.env`, no editing CI workflows, no mass-formatting); the
commit rules of §5.11; and that every decision must be recorded rather than made silently.

---

## 9. Invariants

These are the properties a reviewer checks by inspecting source, not by running the program. Each has a test
in `tests/test_invariants.py`.

| # | Invariant | How it is checked |
|---|---|---|
| **I-1** | No module issues a non-GET HTTP request | AST scan: no `method=` argument other than `"GET"`; no literal `"POST"`, `"PUT"`, `"PATCH"`, `"DELETE"` in `harness/` |
| **I-2** | The `gh` CLI is never invoked | Source scan for `"gh "`, `["gh"`, `'gh'` in any subprocess argv construction |
| **I-3** | Permission-skipping flags never appear | Source scan for `dangerously-skip-permissions` |
| **I-4** | `os.environ` is read only in `config.py` | AST scan across `harness/` |
| **I-5** | SQL exists only in `store.py` | Source scan for `SELECT`, `INSERT`, `UPDATE`, `DELETE`, `CREATE TABLE` |
| **I-6** | `.gitignore` covers `.env`, `runs/`, `HALT` | File content assertion |
| **I-7** | `npm run format` and `prettier --check .` are never invoked | Source scan |
| **I-8** | No file is written outside `runs_dir`, `packages_dir`, the configured db path, or `HUMAN.md` | Runtime assertion in a wrapper used by every write path, plus a test that monkeypatches `open` |
| **I-9** | The bot token is never transmitted in Delivery 1 | `HARNESS_GITHUB_TOKEN` is referenced only in `config.py` and `identity.py`; `gh.py` does not import `identity`; no request-building code reads it |
| **I-10** | `HUMAN.md` generation never interpolates an environment value | AST scan of `render_human_doc`: no `os.environ`, no `config` field whose name ends `_token` or `_key` |

---

## 10. Acceptance criteria

Delivery 1 is accepted when **all** of the following hold. `HARNESS-REVIEW.md` is the runnable form.

### 10.1 Structural

- **A1.** Every path in §4.1–§4.4 exists. No file exists under `harness/`, `prompts/` or `tests/` that is not
  listed in §4.
- **A2.** `pip install -e .` succeeds in a clean virtual environment with no network access beyond PyPI for
  `pytest`.
- **A3.** `python -c "import harness"` succeeds with zero third-party imports resolved.

### 10.2 Behavioral

- **A4.** `pytest` passes with zero failures and zero skips. A skip is a failure for acceptance purposes.
- **A5.** Every behavior `B1`–`B78` is cited by at least one test, verified by a coverage report mapping
  behavior IDs to test names.
- **A6.** `tests/test_invariants.py` passes, covering `I-1` through `I-8`.

### 10.3 Functional, zero-cost

Run entirely on `backend=fake`; spends no Claude capacity.

- **A7.** `harness init` on an empty directory creates the db and directories and exits 0.
- **A8.** `harness doctor` exits 0 on the target machine and names the `claude` version and the `--max-turns`
  probe result.
- **A9.** The five-command sequence of §1 completes against the fake backend and produces a package directory
  conforming to §7.2.
- **A10.** `harness halt` during a run causes exit 5 with clones released and the item left resumable.

### 10.4 Functional, live

Budgeted; run once.

- **A11.** `harness discover --mode directed --target 816` creates the item using at most two GitHub API
  calls.
- **A12.** `harness propose 1` produces a work package with every §7.1 section populated and at least one
  entry under `## Decisions`.
- **A13.** `harness run --item 1` produces a green gate sequence in the clone, or a `blocked` item with a
  recorded reason. Both outcomes are acceptable; a *silently* green one that widened a check is not.
- **A14.** The resulting package satisfies the reconstruction contract of §7.2 on a machine that has never
  seen the harness.

### 10.5 Safety

- **A15.** Across the entire live run, the process made zero authenticated requests. Verified by inspecting
  every `api_call` row and confirming `gh.py` sets no auth header.
- **A16.** No `.env`, key, or token appears in any file under `runs/` or `packages/`. Verified by scanning
  every artifact with the §5.8 patterns.
- **A17.** No commit produced by the harness violates the commit rules of §5.11, verified by running
  `npx commitlint --from <base>` inside the clone.
- **A18.** The diff touches no path under `.github/workflows/`, and adds no `continue-on-error`, no
  `.skip(`, and no increased timeout.

### 10.6 Identity

- **A19.** With a syntactically valid `HARNESS_GITHUB_TOKEN` set in `.env` and `permission_tier=0`, a full
  live run completes and **no request carries the token**. Verified by `identity.load_token()` raising and by
  inspecting every `api_call` row.
- **A20.** `harness setup` produces a `HUMAN.md` containing every required section of §13.4, with each
  outstanding action naming its actor and its exact values.
- **A21.** Scanning `HUMAN.md` with the §5.8 redaction patterns yields no match.

---

## 11. Out of scope

Explicit, so cull is fast: the Ship and Watch stages; any GitHub write; concurrency above one; audit
discovery; sub-issues and stacking; the `api` runner backend; any *use* of the bot identity (§13 specifies it;
Delivery 1 only detects readiness and generates `HUMAN.md`); a web UI;
scheduling or cron; multi-repository support; Windows-service or daemon packaging; retry across process
restarts beyond what the state machine already gives.

---

## 12. Questions carried into the build

Neither blocks Delivery 1; both are recorded so the answer lands somewhere.

1. **Does a local throwaway Postgres count as "using secrets"?** Decides whether seed and database tickets are
   ever in scope. Not required for #816, which touches no database. Until answered, `gates.run_sequence` runs
   the non-database subset and records the omission in `EVIDENCE.md` rather than claiming full parity.
2. **Are issue comments in scope?** This is the Tier 0 / Tier 1 boundary. Until answered, the harness cannot
   claim an issue before working it, so `discover --mode triage` MUST re-check `claimed_issue_numbers()`
   immediately before `implement` begins, not only at selection time.

---

## 13. Bot identity and human setup

### 13.1 The decision, and why it is specified now but inert

The harness will eventually act under its own GitHub identity, `brightboost-harness`, rather than under a
person's account. A **machine user account** is the right vehicle — not a GitHub App. An App is the better
permission model, but it reacts only through webhooks, and a laptop that runs overnight has no public endpoint
to receive one. A machine account can be *polled* for mentions on each run, which fits a tool that is started
deliberately.

The reason to specify it now and forbid its use until Tier 1 is that the identity is the moment the harness
first holds a credential. Today it holds none, which is most of why Tier 0 was easy to agree to: it cannot
leak what it was never given. That argument survives only if the token cannot be used before the tier that
needs it, so the refusal lives in code (B80), not in a policy document.

The strongest argument for the identity is not convenience. It is **honesty**: agent-written comments posted
from a person's account read to every reviewer, the intern cohort included, as that person's words. Posted
from `brightboost-harness` they are labelled as what they are, the whole audit trail filters by author, and
revoking it is one action on the maintainer's side rather than a conversation about someone's account.

### 13.2 Token scopes by tier

A **fine-grained** personal access token, scoped to `Bright-Bots-Initiative/brightboost` alone. Never a
classic token — classic scopes are account-wide and cannot be narrowed to one repository.

| Permission | Tier 0 | Tier 1 (comment, file) | Tier 2 (open PRs) | Notes |
|---|---|---|---|---|
| *(no token at all)* | ✅ | — | — | Delivery 1 |
| `Metadata` | — | Read | Read | Mandatory for every fine-grained token |
| `Issues` | — | Read and write | Read and write | Comments and issue creation |
| `Pull requests` | — | Read and write | Read and write | PR review comments |
| `Contents` | — | **none** | Read and write | Pushing a branch |
| `Workflows` | — | **none** | **none** | Deliberately withheld — see below |
| `Administration` | — | **none** | **none** | Never |
| `Secrets`, `Environments`, `Actions` | — | **none** | **none** | Never |

**Withholding `Workflows` is a capability guarantee, not a policy.** A fine-grained token without it cannot
push any commit that modifies `.github/workflows/`; GitHub itself rejects the push. Acceptance criterion A18
says the harness must never edit CI to go green. At Tier 2 that stops being a rule the harness is trusted to
follow and becomes something it is incapable of doing.

### 13.3 What the harness does with the identity

- **Delivery 1 (Tier 0).** Detects whether the account exists (an unauthenticated `GET /users/brightboost-harness`,
  costing no credential), whether a token is present, and whether its shape is plausible. Reports all of it
  through `harness setup` and `harness doctor`. Uses none of it.
- **Tier 1.** Posts diagnoses as issue comments and files issues from audits, each carrying a footer naming
  the operator and stating that the comment is automated. Polls
  `GET /search/issues?q=repo:<repo>+mentions:brightboost-harness+updated:>=<cursor>` on each run and queues
  matches as directed work items. Latency equals run cadence, which is the correct trade for a tool that is
  started deliberately.
- **Tier 2.** Pushes branches and opens PRs, and runs the Watch stage's CI remediation loop.

### 13.4 `HUMAN.md` — the generated setup document

`harness setup` writes `HUMAN.md` at the repository root. It is a **gap report**, not a static README: it
describes the distance between what is configured now and what the target tier needs, and it shrinks as
prerequisites are satisfied. Required sections, in order:

| Section | Contents |
|---|---|
| `## Current state` | Current tier, target tier, and which prerequisites are already satisfied |
| `## What you need to do` | Numbered outstanding actions, each naming the actor, the exact values, and why it is needed |
| `## Tokens` | A table: `.env` key, token type, exact permission set from §13.2, where to create it, who must approve it |
| `## What needs Nathaniel` | Actions requiring organization or repository admin, separated so they can be sent as one request |
| `## What the harness will never ask for` | The explicit negative list — production credentials, organization admin, `Workflows` permission, branch-protection changes, merge rights |
| `## Verification` | Commands that confirm each step worked, so the document checks itself |

Two hard rules: it MUST contain no secret value, only names and scopes (B83, I-10); and it MUST be
deterministic, so re-running `harness setup` produces no spurious diff (B86).

### 13.5 What only a human can do

These cannot be automated and belong in `HUMAN.md`'s outstanding list until done:

| # | Action | Actor | Notes |
|---|---|---|---|
| 1 | Create the `brightboost-harness` GitHub account | You | Confirm current GitHub terms permit the machine account and that you own it and are answerable for it |
| 2 | Verify its email and enable 2FA | You | Required for organization membership |
| 3 | Generate the fine-grained PAT with exactly the §13.2 set for the target tier | You | Scope it to `brightboost` only; set the shortest expiry you will tolerate re-issuing |
| 4 | Put it in `.env` as `HARNESS_GITHUB_TOKEN` | You | Never in a chat, never in a commit, never in a package |
| 5 | Approve the fine-grained token for the organization, if the org restricts them | **Nathaniel** | Organizations can require owner approval before a fine-grained token reaches their repositories |
| 6 | Agree Tier 1, and raise `PERMISSION_TIER` to `1` | **Nathaniel**, then you | Without this the token is refused in code regardless of what is in `.env` |
| 7 | Invite the account as a collaborator — Tier 2 only | **Nathaniel** | Not needed for commenting on a public repository; needed to push a branch |
| 8 | Decide whether the account is added to the organization or left an outside collaborator | **Nathaniel** | Affects visibility and seat count |

---

*Spec frozen 2026-09-01, revised 1.1 to add §13. Environment facts in §3.1 were verified on that date and must
be re-probed by `harness doctor`, not trusted.*
