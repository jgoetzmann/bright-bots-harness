"""`.env` parsing into a frozen :class:`Config`; the only module that reads ``os.environ`` (I-4)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Mapping

from harness.clock import as_utc
from harness.errors import ConfigError

__all__ = [
    "Config",
    "load_config",
    "SECRET_KEYS",
    "secret_values",
    "read_secret",
    "environ_snapshot",
    "github_token",
    "TOKEN_KEY_NAME",
    "FINE_GRAINED_TOKEN_SHAPE",
    "CLASSIC_TOKEN_SHAPE",
    "CONFIG_JSON_KEYS",
    "CONFIG_JSON_RELATIVE",
    "RUN_WINDOW_PATTERN",
    "WINDOW_DAYS",
    "in_run_window",
    "EFFORT_LEVELS",
]

#: The reasoning-effort levels `claude --effort` accepts, in order (B225).
EFFORT_LEVELS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")

SECRET_KEYS: tuple[str, ...] = ("HARNESS_GITHUB_TOKEN", "ANTHROPIC_API_KEY")
#: Accepted in .env for the claude CLI, never a Config field, scrubbed like a secret (D24).
PASSTHROUGH_KEYS: tuple[str, ...] = ("CLAUDE_CODE_OAUTH_TOKEN",)

#: The `.env` key holding the machine-account token. Other modules (``identity.py``) refer to
#: the key through this name so the literal lives in exactly one module (I-11).
TOKEN_KEY_NAME: str = "HARNESS_GITHUB_TOKEN"

#: The `.env` as most recently parsed by :func:`load_config`, so :func:`secret_values`
#: can redact secrets that came from the file rather than from the environment.
_LAST_ENV: dict[str, str] = {}

#: Every `.env` key that maps onto a :class:`Config` field. All are required; defaults
#: live in `.env.example`, never in code. The Delivery 2 keys follow the Delivery 1 keys in
#: the order the fields are appended to :class:`Config`.
FIELD_KEYS: tuple[str, ...] = (
    "BACKEND",
    "REPO",
    "PERMISSION_TIER",
    "ALLOWLIST_LABEL",
    "WEEKLY_BUDGET_PCT",
    "SESSION_BUDGET_PCT",
    "RESERVE_PCT",
    "WEEKLY_RESET_DAY",
    "MAX_CONCURRENT_CLONES",
    "MAX_TURNS_DISCOVER",
    "MAX_TURNS_PROPOSE",
    "MAX_TURNS_IMPLEMENT",
    "MAX_TURNS_PACKAGE",
    "MAX_RETRIES_GATES",
    "GITHUB_API_CEILING_PER_HOUR",
    "MIN_FREE_DISK_GB",
    "DB_PATH",
    "RUNS_DIR",
    "PACKAGES_DIR",
    "HALT_FILE",
    "FULLSEND_ENABLED",
    "WEEKLY_CAP_USD",
    "PER_CALL_CAP_USD",
    "MAX_CONCURRENT_ITEMS",
    "MAX_REVISE_CYCLES",
    "FORK_REPO",
    "UPSTREAM_REPO",
    "TRUST_FILE",
    "NOTIFY_POLL_HOURS",
    "MAX_SUBISSUES",
    "SELF_REPO",
    "TRACKING_ISSUE",
    "STORE_BACKEND",
    "WEEKLY_USAGE_STOP_PCT",
    "SESSION_USAGE_STOP_PCT",
    "OVERRUN_PCT",
    "RUN_WINDOW_START",
    "RUN_WINDOW_END",
    "MODEL",
    "EFFORT",
)

#: The only field keys that may be absent from `.env` or empty (RUN-DECISIONS-D2 §2).
OPTIONAL_KEYS: tuple[str, ...] = ("FORK_REPO", "TRACKING_ISSUE")

KNOWN_KEYS: tuple[str, ...] = FIELD_KEYS + SECRET_KEYS + PASSTHROUGH_KEYS

#: The operational knobs `.harness/config.json` may carry, and nothing else (B112).
CONFIG_JSON_KEYS: tuple[str, ...] = (
    "WEEKLY_CAP_USD",
    "PER_CALL_CAP_USD",
    "RESERVE_PCT",
    "MAX_CONCURRENT_ITEMS",
    "MAX_REVISE_CYCLES",
    "NOTIFY_POLL_HOURS",
    "MAX_SUBISSUES",
    "TRACKING_ISSUE",
    "FORK_REPO",
    "UPSTREAM_REPO",
    "TRUST_FILE",
    "WEEKLY_USAGE_STOP_PCT",
    "SESSION_USAGE_STOP_PCT",
    "OVERRUN_PCT",
    "RUN_WINDOW_START",
    "RUN_WINDOW_END",
)

#: Where the override file lives, relative to the directory holding `.env`.
CONFIG_JSON_RELATIVE: Path = Path(".harness") / "config.json"

_WEEKDAYS: tuple[str, ...] = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

_STAGE_KEYS: tuple[str, ...] = ("discover", "propose", "implement", "package")

_TRUE_WORDS: frozenset[str] = frozenset({"true", "1", "yes"})
_FALSE_WORDS: frozenset[str] = frozenset({"false", "0", "no"})

#: The two shapes a machine-account token may take (RUN-DECISIONS "Identity", B81). Defined
#: here for the same reason as :data:`TOKEN_KEY_NAME`: ``identity.py`` binds its
#: ``FINE_GRAINED_SHAPE``/``CLASSIC_SHAPE`` to these so the patterns live in one module. The
#: two callers keep their own whitespace policies — :func:`token_shape_ok` strips first,
#: ``Identity.validate_shape`` matches the raw string, where surrounding whitespace is a
#: malformed value rather than noise.
FINE_GRAINED_TOKEN_SHAPE: re.Pattern[str] = re.compile(r"^github_pat_[A-Za-z0-9_]{40,}$")
CLASSIC_TOKEN_SHAPE: re.Pattern[str] = re.compile(r"^ghp_[A-Za-z0-9]{30,}$")

_TOKEN_SHAPES: tuple[re.Pattern[str], ...] = (FINE_GRAINED_TOKEN_SHAPE, CLASSIC_TOKEN_SHAPE)

#: The run window's three-letter UTC weekday names, in ``datetime.weekday()`` order (D3).
WINDOW_DAYS: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

#: A run-window endpoint: a lowercase weekday and a 24-hour UTC time (D3 config table).
RUN_WINDOW_PATTERN = re.compile(r"^(mon|tue|wed|thu|fri|sat|sun) ([01]\d|2[0-3]):[0-5]\d$")

#: Minutes in one day, for the wrap-aware window comparison.
_DAY_MINUTES = 24 * 60

_ALLOWED_TIERS: tuple[int, ...] = (0, 2)
_STORE_BACKENDS: tuple[str, ...] = ("sqlite", "github")

#: The two quote characters `.env` values may be wrapped in (single, double).
_QUOTE_CHARS: tuple[str, str] = ("\x27", "\x22")


@dataclass(frozen=True)
class Config:
    """Frozen harness configuration (B6: attribute assignment raises)."""

    backend: Literal["cli", "fake"]
    repo: str
    permission_tier: int
    allowlist_label: str
    weekly_budget_pct: float
    session_budget_pct: float
    reserve_pct: float
    weekly_reset_day: str
    max_concurrent_clones: int
    max_turns: Mapping[str, int]
    max_retries_gates: int
    github_api_ceiling_per_hour: int
    min_free_disk_gb: float
    db_path: Path
    runs_dir: Path
    packages_dir: Path
    halt_file: Path
    fullsend_enabled: bool
    github_token_present: bool
    github_token_shape_ok: bool
    weekly_cap_usd: float
    per_call_cap_usd: float
    max_concurrent_items: int
    max_revise_cycles: int
    fork_repo: str
    upstream_repo: str
    trust_file: Path
    notify_poll_hours: int
    max_subissues: int
    self_repo: str
    tracking_issue: int | None
    store_backend: Literal["sqlite", "github"]
    repo_root: Path
    weekly_usage_stop_pct: float
    session_usage_stop_pct: float
    overrun_pct: float
    run_window_start: str
    run_window_end: str
    #: B225: the model alias and the reasoning effort every stage runs at. Pinned in `.env`
    #: rather than left to the CLI's default, so a change to that default cannot silently
    #: change what a work package or a diff is worth.
    model: str
    effort: Literal["low", "medium", "high", "xhigh", "max"]


#: The :class:`Config` most recently returned by :func:`load_config`. ``None`` until a load
#: succeeds; reset at the start of every load so a failed load closes the token door.
_LAST_CONFIG: Config | None = None


def parse_env_text(text: str) -> dict[str, str]:
    """Parse `.env` text: ``KEY=VALUE``, ``#`` comments, blank lines, optional quotes."""
    out: dict[str, str] = {}
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigError(f".env line {lineno} is not KEY=VALUE: {raw_line!r}")
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            raise ConfigError(f".env line {lineno} has an empty key: {raw_line!r}")
        out[key] = _unquote(value.strip())
    return out


def _unquote(value: str) -> str:
    """Strip one layer of matching single or double quotes."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in _QUOTE_CHARS:
        return value[1:-1]
    return value


def _require_int(values: Mapping[str, str], key: str) -> int:
    raw = values[key].strip()
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer, got {raw!r}") from exc


def _require_float(values: Mapping[str, str], key: str) -> float:
    raw = values[key].strip()
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number, got {raw!r}") from exc


def _require_bool(values: Mapping[str, str], key: str) -> bool:
    raw = values[key].strip().lower()
    if raw in _TRUE_WORDS:
        return True
    if raw in _FALSE_WORDS:
        return False
    raise ConfigError(f"{key} must be one of true/false/1/0/yes/no, got {values[key]!r}")


def _require_path(values: Mapping[str, str], key: str, base_dir: Path) -> Path:
    raw = values[key].strip()
    if not raw:
        raise ConfigError(f"{key} must not be empty")
    path = Path(raw)
    if not path.is_absolute():
        path = base_dir / path
    return Path(os.path.normpath(str(path)))


def _repo_shaped(value: str) -> bool:
    """True for ``owner/name`` with both halves non-empty and no further slash."""
    owner, sep, name = value.partition("/")
    return bool(sep) and bool(owner.strip()) and bool(name.strip()) and "/" not in name


def _require_repo(values: Mapping[str, str], key: str) -> str:
    raw = values[key].strip()
    if not _repo_shaped(raw):
        raise ConfigError(f"{key} must be owner/name; got {values[key]!r}")
    return raw


def _require_window(values: Mapping[str, str], key: str) -> str:
    """One run-window endpoint: ``"mon 08:00"`` (UTC) or ``""`` for "no window" (D3)."""
    raw = values.get(key, "").strip()
    if not raw:
        return ""
    if RUN_WINDOW_PATTERN.match(raw) is None:
        raise ConfigError(
            f"{key} must be a UTC weekday and time such as 'mon 08:00', or empty; "
            f"got {values.get(key, '')!r}"
        )
    return raw


def _json_scalar(key: str, value: object) -> str:
    """Render one `.harness/config.json` value as the `.env` text it overrides."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    if isinstance(value, str):
        return value
    raise ConfigError(f"{key} in .harness/config.json must be a string, number, or null")


def read_config_json(path: Path) -> dict[str, str]:
    """Parse `.harness/config.json` (B112): knob keys only, as `.env`-style strings."""
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"could not read {path}: {exc}") from exc
    except ValueError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must be a JSON object of config keys")
    out: dict[str, str] = {}
    for key, value in raw.items():
        if key not in CONFIG_JSON_KEYS:
            raise ConfigError(f"unknown key in .harness/config.json: {key}")
        out[key] = _json_scalar(key, value)
    return out


def token_shape_ok(token: str) -> bool:
    """True when ``token`` matches a fine-grained or classic personal-token shape."""
    candidate = token.strip()
    return any(pattern.match(candidate) for pattern in _TOKEN_SHAPES)


def load_config(
    env_path: Path | None = None, *, environ: Mapping[str, str] | None = None
) -> Config:
    """Read `.env`, then `.harness/config.json`, then ``environ``; validate; return a frozen
    Config."""
    global _LAST_CONFIG

    path = Path(env_path) if env_path is not None else Path(".env")
    if not path.is_file():
        raise ConfigError(f"no .env file at {path}")

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"could not read .env at {path}: {exc}") from exc

    file_values = parse_env_text(text)

    for key in file_values:
        if key not in KNOWN_KEYS:
            raise ConfigError(f"unknown key in .env: {key}")

    base_dir = path.resolve().parent

    values: dict[str, str] = dict(file_values)
    values.update(read_config_json(base_dir / CONFIG_JSON_RELATIVE))

    source: Mapping[str, str] = os.environ if environ is None else environ
    for key in KNOWN_KEYS:
        if key in source:
            values[key] = source[key]

    _LAST_ENV.clear()
    _LAST_ENV.update(values)
    _LAST_CONFIG = None

    for key in FIELD_KEYS:
        if key not in values and key not in OPTIONAL_KEYS:
            raise ConfigError(f"missing required key: {key}")

    backend = values["BACKEND"].strip().lower()
    if backend not in ("cli", "fake"):
        raise ConfigError(f"BACKEND must be one of cli, fake; got {values['BACKEND']!r}")

    repo = values["REPO"].strip()
    if "/" not in repo:
        raise ConfigError(f"REPO must be owner/name; got {values['REPO']!r}")

    permission_tier = _require_int(values, "PERMISSION_TIER")
    if permission_tier not in _ALLOWED_TIERS:
        raise ConfigError(f"PERMISSION_TIER must be 0 or 2; got {permission_tier}")

    allowlist_label = values["ALLOWLIST_LABEL"].strip()
    if not allowlist_label:
        raise ConfigError("ALLOWLIST_LABEL must not be empty")

    weekly_budget_pct = _require_float(values, "WEEKLY_BUDGET_PCT")
    if not 0 < weekly_budget_pct <= 100:
        raise ConfigError(f"WEEKLY_BUDGET_PCT must be in (0, 100]; got {weekly_budget_pct}")

    session_budget_pct = _require_float(values, "SESSION_BUDGET_PCT")
    if not 0 < session_budget_pct <= 100:
        raise ConfigError(f"SESSION_BUDGET_PCT must be in (0, 100]; got {session_budget_pct}")

    reserve_pct = _require_float(values, "RESERVE_PCT")
    if not 0 <= reserve_pct < 100:
        raise ConfigError(f"RESERVE_PCT must be in [0, 100); got {reserve_pct}")

    weekly_reset_day = values["WEEKLY_RESET_DAY"].strip()
    if weekly_reset_day not in _WEEKDAYS:
        raise ConfigError(
            f"WEEKLY_RESET_DAY must be a lowercase weekday name; got {weekly_reset_day!r}"
        )

    max_concurrent_clones = _require_int(values, "MAX_CONCURRENT_CLONES")
    if max_concurrent_clones < 1:
        raise ConfigError(
            f"MAX_CONCURRENT_CLONES must be at least 1; got {max_concurrent_clones}"
        )
    if max_concurrent_clones > 1:
        raise ConfigError(
            f"MAX_CONCURRENT_CLONES must be 1 in delivery 1; got {max_concurrent_clones}"
        )

    turns: dict[str, int] = {}
    for stage in _STAGE_KEYS:
        key = f"MAX_TURNS_{stage.upper()}"
        value = _require_int(values, key)
        if value < 1:
            raise ConfigError(f"{key} must be at least 1; got {value}")
        turns[stage] = value
    max_turns: Mapping[str, int] = MappingProxyType(turns)

    max_retries_gates = _require_int(values, "MAX_RETRIES_GATES")
    if max_retries_gates < 0:
        raise ConfigError(f"MAX_RETRIES_GATES must be >= 0; got {max_retries_gates}")

    github_api_ceiling_per_hour = _require_int(values, "GITHUB_API_CEILING_PER_HOUR")
    if github_api_ceiling_per_hour < 1:
        raise ConfigError(
            f"GITHUB_API_CEILING_PER_HOUR must be at least 1; got {github_api_ceiling_per_hour}"
        )

    min_free_disk_gb = _require_float(values, "MIN_FREE_DISK_GB")
    if min_free_disk_gb < 0:
        raise ConfigError(f"MIN_FREE_DISK_GB must be >= 0; got {min_free_disk_gb}")

    db_path = _require_path(values, "DB_PATH", base_dir)
    runs_dir = _require_path(values, "RUNS_DIR", base_dir)
    packages_dir = _require_path(values, "PACKAGES_DIR", base_dir)
    halt_file = _require_path(values, "HALT_FILE", base_dir)

    fullsend_enabled = _require_bool(values, "FULLSEND_ENABLED")

    token = values.get("HARNESS_GITHUB_TOKEN", "").strip()
    github_token_present = bool(token)
    github_token_shape_ok = github_token_present and token_shape_ok(token)

    # --- Delivery 2 keys (handoff §6.5, RUN-DECISIONS-D2 §2), in Config field order ---

    weekly_cap_usd = _require_float(values, "WEEKLY_CAP_USD")
    if weekly_cap_usd <= 0:
        raise ConfigError(f"WEEKLY_CAP_USD must be > 0; got {weekly_cap_usd}")

    per_call_cap_usd = _require_float(values, "PER_CALL_CAP_USD")
    if per_call_cap_usd <= 0:
        raise ConfigError(f"PER_CALL_CAP_USD must be > 0; got {per_call_cap_usd}")

    max_concurrent_items = _require_int(values, "MAX_CONCURRENT_ITEMS")
    if max_concurrent_items < 1:
        raise ConfigError(f"MAX_CONCURRENT_ITEMS must be at least 1; got {max_concurrent_items}")

    max_revise_cycles = _require_int(values, "MAX_REVISE_CYCLES")
    if max_revise_cycles < 0:
        raise ConfigError(f"MAX_REVISE_CYCLES must be >= 0; got {max_revise_cycles}")

    fork_repo = values.get("FORK_REPO", "").strip()
    if fork_repo and not _repo_shaped(fork_repo):
        raise ConfigError(f"FORK_REPO must be owner/name or empty; got {fork_repo!r}")

    upstream_repo = _require_repo(values, "UPSTREAM_REPO")

    trust_file = _require_path(values, "TRUST_FILE", base_dir)

    notify_poll_hours = _require_int(values, "NOTIFY_POLL_HOURS")
    if notify_poll_hours < 1:
        raise ConfigError(f"NOTIFY_POLL_HOURS must be at least 1; got {notify_poll_hours}")

    max_subissues = _require_int(values, "MAX_SUBISSUES")
    if not 1 <= max_subissues <= 50:
        raise ConfigError(f"MAX_SUBISSUES must be in 1..50; got {max_subissues}")

    self_repo = _require_repo(values, "SELF_REPO")

    tracking_raw = values.get("TRACKING_ISSUE", "").strip()
    tracking_issue: int | None = None
    if tracking_raw:
        try:
            tracking_issue = int(tracking_raw)
        except ValueError as exc:
            raise ConfigError(
                f"TRACKING_ISSUE must be an issue number or empty; got {tracking_raw!r}"
            ) from exc
        if tracking_issue < 1:
            raise ConfigError(
                f"TRACKING_ISSUE must be a positive issue number; got {tracking_issue}"
            )

    model = values["MODEL"].strip()
    if not model:
        raise ConfigError("MODEL must not be empty; use an alias such as opus, or a full name")

    effort_raw = values["EFFORT"].strip().lower()
    if effort_raw not in EFFORT_LEVELS:
        raise ConfigError(
            f"EFFORT must be one of {', '.join(EFFORT_LEVELS)}; got {values['EFFORT']!r}"
        )
    effort: Any = effort_raw

    store_backend_raw = values["STORE_BACKEND"].strip()
    if store_backend_raw not in _STORE_BACKENDS:
        raise ConfigError(
            f"STORE_BACKEND must be one of sqlite, github; got {values['STORE_BACKEND']!r}"
        )
    store_backend: Literal["sqlite", "github"] = (
        "github" if store_backend_raw == "github" else "sqlite"
    )

    if max_concurrent_items > 1 and store_backend != "github":
        raise ConfigError(
            "MAX_CONCURRENT_ITEMS above 1 requires STORE_BACKEND=github; "
            f"got MAX_CONCURRENT_ITEMS={max_concurrent_items}, STORE_BACKEND={store_backend}"
        )

    # --- Delivery 3 keys (RUN-DECISIONS-D3 "Config"), in Config field order ---

    weekly_usage_stop_pct = _require_float(values, "WEEKLY_USAGE_STOP_PCT")
    if not 0 < weekly_usage_stop_pct <= 100:
        raise ConfigError(
            f"WEEKLY_USAGE_STOP_PCT must be in (0, 100]; got {weekly_usage_stop_pct}"
        )

    session_usage_stop_pct = _require_float(values, "SESSION_USAGE_STOP_PCT")
    if not 0 < session_usage_stop_pct <= 100:
        raise ConfigError(
            f"SESSION_USAGE_STOP_PCT must be in (0, 100]; got {session_usage_stop_pct}"
        )

    overrun_pct = _require_float(values, "OVERRUN_PCT")
    if not 0 <= overrun_pct < weekly_usage_stop_pct:
        raise ConfigError(
            f"OVERRUN_PCT must be in [0, WEEKLY_USAGE_STOP_PCT); got {overrun_pct} "
            f"with WEEKLY_USAGE_STOP_PCT={weekly_usage_stop_pct}"
        )

    run_window_start = _require_window(values, "RUN_WINDOW_START")
    run_window_end = _require_window(values, "RUN_WINDOW_END")
    if bool(run_window_start) != bool(run_window_end):
        raise ConfigError(
            "RUN_WINDOW_START and RUN_WINDOW_END must both be set or both be empty; got "
            f"RUN_WINDOW_START={run_window_start!r}, RUN_WINDOW_END={run_window_end!r}"
        )

    if permission_tier == 2:
        if store_backend != "github":
            raise ConfigError(
                "PERMISSION_TIER=2 requires STORE_BACKEND=github; "
                f"got STORE_BACKEND={store_backend}"
            )
        if not fork_repo:
            raise ConfigError("PERMISSION_TIER=2 requires a non-empty FORK_REPO (owner/name)")

    config = Config(
        backend="cli" if backend == "cli" else "fake",
        repo=repo,
        permission_tier=permission_tier,
        allowlist_label=allowlist_label,
        weekly_budget_pct=weekly_budget_pct,
        session_budget_pct=session_budget_pct,
        reserve_pct=reserve_pct,
        weekly_reset_day=weekly_reset_day,
        max_concurrent_clones=max_concurrent_clones,
        max_turns=max_turns,
        max_retries_gates=max_retries_gates,
        github_api_ceiling_per_hour=github_api_ceiling_per_hour,
        min_free_disk_gb=min_free_disk_gb,
        db_path=db_path,
        runs_dir=runs_dir,
        packages_dir=packages_dir,
        halt_file=halt_file,
        fullsend_enabled=fullsend_enabled,
        github_token_present=github_token_present,
        github_token_shape_ok=github_token_shape_ok,
        weekly_cap_usd=weekly_cap_usd,
        per_call_cap_usd=per_call_cap_usd,
        max_concurrent_items=max_concurrent_items,
        max_revise_cycles=max_revise_cycles,
        fork_repo=fork_repo,
        upstream_repo=upstream_repo,
        trust_file=trust_file,
        notify_poll_hours=notify_poll_hours,
        max_subissues=max_subissues,
        self_repo=self_repo,
        tracking_issue=tracking_issue,
        store_backend=store_backend,
        repo_root=base_dir,
        weekly_usage_stop_pct=weekly_usage_stop_pct,
        session_usage_stop_pct=session_usage_stop_pct,
        overrun_pct=overrun_pct,
        run_window_start=run_window_start,
        run_window_end=run_window_end,
        model=model,
        effort=effort,
    )
    _LAST_CONFIG = config
    return config


def _window_minute(point: str) -> int | None:
    """``"mon 08:00"`` -> the minute of the UTC week; ``None`` when unparseable."""
    text = (point or "").strip().lower()
    if not text or RUN_WINDOW_PATTERN.match(text) is None:
        return None
    day, _, clock = text.partition(" ")
    hour, _, minute = clock.partition(":")
    return WINDOW_DAYS.index(day) * _DAY_MINUTES + int(hour) * 60 + int(minute)


def in_run_window(config: Config, now: datetime) -> bool:
    """True when ``now`` (UTC) falls inside ``[run_window_start, run_window_end)`` (D3).

    Pure and wrap-aware: a window whose end is earlier in the week than its start runs past
    Sunday into the next week. Both endpoints empty means the window is always open, and so
    does an endpoint the harness cannot parse — the window narrows what runs, it never
    invents a stop.
    """
    start = _window_minute(getattr(config, "run_window_start", ""))
    end = _window_minute(getattr(config, "run_window_end", ""))
    if start is None or end is None:
        return True
    moment = as_utc(now)
    # 0..10079: weekday() is 0..6, so this cannot reach a week. The wrap past Sunday is the
    # ``start > end`` branch below, not arithmetic here.
    current = moment.weekday() * _DAY_MINUTES + moment.hour * 60 + moment.minute
    if start <= end:
        return start <= current < end
    return current >= start or current < end


def github_token() -> str:
    """The token door (I-11): the token only when the last-loaded config is at tier 2."""
    config = _LAST_CONFIG
    if config is None or config.permission_tier != 2:
        return ""
    return _LAST_ENV.get(TOKEN_KEY_NAME, "").strip()


def secret_values() -> tuple[str, ...]:
    """Every non-empty secret value known from ``os.environ`` and the last-loaded `.env`."""
    found: list[str] = []
    for key in SECRET_KEYS + PASSTHROUGH_KEYS:
        for source in (os.environ, _LAST_ENV):
            raw = source.get(key, "")
            value = raw.strip() if isinstance(raw, str) else ""
            if value and value not in found:
                found.append(value)
    return tuple(found)


def read_secret(key: str) -> str:
    """Return the raw secret value for ``key``, or the empty string when absent.

    Only ``identity.load_token`` calls this, and only once the tier permits it.
    """
    value = os.environ.get(key, "")
    if not value:
        value = _LAST_ENV.get(key, "")
    return value


def environ_snapshot() -> Mapping[str, str]:
    """Return a copy of the process environment for subprocess construction.

    The only sanctioned way for another module to obtain the parent environment (I-4).
    """
    return dict(os.environ)
