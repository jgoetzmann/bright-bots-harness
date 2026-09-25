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
    "RETIRED_KEYS",
    "retired_keys_seen",
    "DAILY",
    "RUN_WINDOW_PATTERN",
    "WINDOW_DAYS",
    "in_run_window",
    "is_daily_window",
    "run_window_label",
    "EFFORT_LEVELS",
    "co_author_ok",
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
    "MAX_CONCURRENT_ITEMS",
    "MAX_REVISE_CYCLES",
    "FORK_REPO",
    "UPSTREAM_REPO",
    "TRUST_FILE",
    "MAX_SUBISSUES",
    "SELF_REPO",
    "TRACKING_ISSUE",
    "STORE_BACKEND",
    "SESSION_USAGE_STOP_PCT",
    "RUN_WINDOW_START",
    "RUN_WINDOW_END",
    "MODEL",
    "EFFORT",
    # Asking for work (D53).
    "INBOX_ISSUE",
    "SUGGEST_MAX_PER_RUN",
    "COMMENT_UPSTREAM",
    "ASK_MAX_PER_DAY",
    # D70: the adversarial self-audit before delivery.
    "MAX_SELF_AUDIT_CYCLES",
    # D82: the person credited on every commit the harness makes.
    "CO_AUTHOR",
    # D88: how many delivery pull requests may be open upstream at once.
    "MAX_OPEN_DELIVERIES",
)

#: The only field keys that may be absent from `.env` or empty (RUN-DECISIONS-D2 §2).
OPTIONAL_KEYS: tuple[str, ...] = (
    "FORK_REPO", "TRACKING_ISSUE", "CO_AUTHOR", "MAX_OPEN_DELIVERIES"
)

KNOWN_KEYS: tuple[str, ...] = FIELD_KEYS + SECRET_KEYS + PASSTHROUGH_KEYS

#: The operational knobs `.harness/config.json` may carry, and nothing else (B112).
CONFIG_JSON_KEYS: tuple[str, ...] = (
    "MAX_CONCURRENT_ITEMS",
    "MAX_REVISE_CYCLES",
    "MAX_SUBISSUES",
    "TRACKING_ISSUE",
    "FORK_REPO",
    "UPSTREAM_REPO",
    "TRUST_FILE",
    "SESSION_USAGE_STOP_PCT",
    "RUN_WINDOW_START",
    "RUN_WINDOW_END",
    "INBOX_ISSUE",
    "SUGGEST_MAX_PER_RUN",
    "COMMENT_UPSTREAM",
    "ASK_MAX_PER_DAY",
    "MAX_SELF_AUDIT_CYCLES",
    "CO_AUTHOR",
    "MAX_OPEN_DELIVERIES",
)

#: Keys D74 and D89 removed. Accepted wherever a key is accepted and ignored, so an existing
#: `.env` or `.harness/config.json` keeps loading; `harness doctor` names them as a warning
#: until they are deleted. Never re-used for a live key.
RETIRED_KEYS: tuple[str, ...] = (
    "WEEKLY_BUDGET_PCT",
    "SESSION_BUDGET_PCT",
    "RESERVE_PCT",
    "WEEKLY_RESET_DAY",
    "MAX_CONCURRENT_CLONES",
    "WEEKLY_CAP_USD",
    "PER_CALL_CAP_USD",
    "NOTIFY_POLL_HOURS",
    "AUDIT_CAP_USD",
    "ASK_CAP_USD",
    # D89: the account has a five-hour session limit and no weekly one.
    "WEEKLY_USAGE_STOP_PCT",
    "OVERRUN_PCT",
    "SUGGEST_MIN_HEADROOM_PCT",
    "AUDIT_MIN_HEADROOM_PCT",
)

#: The retired keys the last :func:`load_config` found, as ``(key, source)``.
_LAST_RETIRED: list[tuple[str, str]] = []

#: Where the override file lives, relative to the directory holding `.env`.
CONFIG_JSON_RELATIVE: Path = Path(".harness") / "config.json"

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

#: A run-window endpoint: a lowercase weekday, or ``daily``, and a 24-hour UTC time (D3, D72).
RUN_WINDOW_PATTERN = re.compile(
    r"^(mon|tue|wed|thu|fri|sat|sun|daily) ([01]\d|2[0-3]):[0-5]\d$"
)

#: The endpoint word for a window that repeats every day instead of once a week (D72).
DAILY = "daily"

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
    max_concurrent_items: int
    max_revise_cycles: int
    fork_repo: str
    upstream_repo: str
    trust_file: Path
    max_subissues: int
    self_repo: str
    tracking_issue: int | None
    store_backend: Literal["sqlite", "github"]
    repo_root: Path
    session_usage_stop_pct: float
    run_window_start: str
    run_window_end: str
    #: B225: the model alias and the reasoning effort every stage runs at. Pinned in `.env`
    #: rather than left to the CLI's default, so a change to that default cannot silently
    #: change what a work package or a diff is worth.
    model: str
    effort: Literal["low", "medium", "high", "xhigh", "max"]
    #: Delivery 4. The inbox issue in SELF_REPO; 0 disables it.
    inbox_issue: int
    suggest_max_per_run: int
    comment_upstream: bool
    ask_max_per_day: int
    #: D70: audit/fix cycles after the gates go green; 0 turns the self-audit off entirely.
    max_self_audit_cycles: int
    #: D82: ``Name <email>`` for the `Co-authored-by` trailer on every harness commit; "" is none.
    co_author: str
    #: D88: open delivery pull requests upstream at which no new item starts; 0 is no cap.
    max_open_deliveries: int


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
    """One run-window endpoint: ``"mon 08:00"`` or ``"daily 11:00"`` (UTC), or ``""`` for "no
    window" (D3, D72)."""
    raw = values.get(key, "").strip()
    if not raw:
        return ""
    if RUN_WINDOW_PATTERN.match(raw) is None:
        raise ConfigError(
            f"{key} must be a UTC weekday or 'daily' and a time, such as 'mon 08:00' or "
            f"'daily 11:00', or empty; got {values.get(key, '')!r}"
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
        # A retired key is tolerated at any value shape, so it is skipped before both the
        # unknown-key check and the scalar check (D74).
        if key in RETIRED_KEYS:
            _LAST_RETIRED.append((key, ".harness/config.json"))
            continue
        if key not in CONFIG_JSON_KEYS:
            raise ConfigError(f"unknown key in .harness/config.json: {key}")
        out[key] = _json_scalar(key, value)
    return out


#: `Name <email>`, the shape git and GitHub read in a `Co-authored-by` trailer (D82).
CO_AUTHOR_SHAPE: re.Pattern[str] = re.compile(r"[^<>]+ <[^<>@\s]+@[^<>@\s]+>")

#: The longest `Co-authored-by: <value>` line, so a commit never wraps the trailer.
CO_AUTHOR_MAX = 100


def co_author_ok(value: str) -> bool:
    """True when ``value`` is one printable ``Name <email>`` whose trailer fits on one line.

    ``isprintable`` refuses every control, separator and invisible format character, which
    are the ones a reader of the commit cannot see or a tool may split a line on.
    """
    return (
        value.isprintable()
        and bool(CO_AUTHOR_SHAPE.fullmatch(value))
        and len(f"Co-authored-by: {value}") <= CO_AUTHOR_MAX
    )


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

    _LAST_RETIRED.clear()
    file_values = parse_env_text(text)

    for key in file_values:
        if key not in KNOWN_KEYS and key not in RETIRED_KEYS:
            raise ConfigError(f"unknown key in .env: {key}")

    base_dir = path.resolve().parent

    # A retired key is dropped here, so it reaches neither validation nor `_LAST_ENV` (D74).
    values: dict[str, str] = {}
    for key, value in file_values.items():
        if key in RETIRED_KEYS:
            _LAST_RETIRED.append((key, ".env"))
            continue
        values[key] = value
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

    session_usage_stop_pct = _require_float(values, "SESSION_USAGE_STOP_PCT")
    if not 0 < session_usage_stop_pct <= 100:
        raise ConfigError(
            f"SESSION_USAGE_STOP_PCT must be in (0, 100]; got {session_usage_stop_pct}"
        )

    run_window_start = _require_window(values, "RUN_WINDOW_START")
    run_window_end = _require_window(values, "RUN_WINDOW_END")
    if bool(run_window_start) != bool(run_window_end):
        raise ConfigError(
            "RUN_WINDOW_START and RUN_WINDOW_END must both be set or both be empty; got "
            f"RUN_WINDOW_START={run_window_start!r}, RUN_WINDOW_END={run_window_end!r}"
        )
    # B409: "mon 08:00 to daily 15:00" has no single honest reading, so it is refused.
    if run_window_start and (
        run_window_start.startswith(f"{DAILY} ") != run_window_end.startswith(f"{DAILY} ")
    ):
        raise ConfigError(
            "RUN_WINDOW_START and RUN_WINDOW_END must both be 'daily' or both name a weekday; "
            f"got RUN_WINDOW_START={run_window_start!r}, RUN_WINDOW_END={run_window_end!r}"
        )

    if permission_tier == 2:
        if store_backend != "github":
            raise ConfigError(
                "PERMISSION_TIER=2 requires STORE_BACKEND=github; "
                f"got STORE_BACKEND={store_backend}"
            )
        if not fork_repo:
            raise ConfigError("PERMISSION_TIER=2 requires a non-empty FORK_REPO (owner/name)")

    # Asking for work (D53).
    inbox_issue = _require_int(values, "INBOX_ISSUE")
    if inbox_issue < 0:
        raise ConfigError(f"INBOX_ISSUE must be 0 or an issue number; got {inbox_issue}")

    suggest_max_per_run = _require_int(values, "SUGGEST_MAX_PER_RUN")
    if suggest_max_per_run < 0:
        raise ConfigError(
            f"SUGGEST_MAX_PER_RUN must be 0 or more; got {suggest_max_per_run}"
        )

    comment_upstream = _require_bool(values, "COMMENT_UPSTREAM")

    ask_max_per_day = _require_int(values, "ASK_MAX_PER_DAY")
    if ask_max_per_day < 0:
        raise ConfigError(f"ASK_MAX_PER_DAY must be 0 or more; got {ask_max_per_day}")

    max_self_audit_cycles = _require_int(values, "MAX_SELF_AUDIT_CYCLES")
    if max_self_audit_cycles < 0:
        raise ConfigError(
            f"MAX_SELF_AUDIT_CYCLES must be 0 or more; got {max_self_audit_cycles}"
        )

    co_author = values.get("CO_AUTHOR", "").strip()
    if co_author and not co_author_ok(co_author):
        raise ConfigError(
            "CO_AUTHOR must be one line, 'Name <email>', short enough for its trailer to fit "
            f"{CO_AUTHOR_MAX} characters; got {co_author!r}"
        )

    open_raw = values.get("MAX_OPEN_DELIVERIES", "").strip()
    try:
        max_open_deliveries = int(open_raw) if open_raw else 0
    except ValueError as exc:
        raise ConfigError(
            f"MAX_OPEN_DELIVERIES must be a whole number or empty; got {open_raw!r}"
        ) from exc
    if max_open_deliveries < 0:
        raise ConfigError(
            f"MAX_OPEN_DELIVERIES must be 0 or more; got {max_open_deliveries}"
        )

    # I-18 (D61): the harness never works on its own repository. A system that can rewrite the
    # rules it is governed by has no rules -- a change to gh.py or prompts/implement.md could
    # propose its way out of the kill switch, the credential door and the pin, and the
    # reviewer's only defence would be noticing. Refused here, before anything is built, so
    # every later check is a second line rather than the only one.
    if self_repo:
        for key, value in (("UPSTREAM_REPO", upstream_repo), ("REPO", repo)):
            if value and value.strip().lower() == self_repo.strip().lower():
                raise ConfigError(
                    f"{key} must not be SELF_REPO ({self_repo}): the harness does not work on "
                    "its own repository (I-18). Changes to the harness are made by a person."
                )

    config = Config(
        backend="cli" if backend == "cli" else "fake",
        repo=repo,
        permission_tier=permission_tier,
        allowlist_label=allowlist_label,
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
        max_concurrent_items=max_concurrent_items,
        max_revise_cycles=max_revise_cycles,
        fork_repo=fork_repo,
        upstream_repo=upstream_repo,
        trust_file=trust_file,
        max_subissues=max_subissues,
        self_repo=self_repo,
        tracking_issue=tracking_issue,
        store_backend=store_backend,
        repo_root=base_dir,
        session_usage_stop_pct=session_usage_stop_pct,
        run_window_start=run_window_start,
        run_window_end=run_window_end,
        model=model,
        effort=effort,
        inbox_issue=inbox_issue,
        suggest_max_per_run=suggest_max_per_run,
        comment_upstream=comment_upstream,
        ask_max_per_day=ask_max_per_day,
        max_self_audit_cycles=max_self_audit_cycles,
        co_author=co_author,
        max_open_deliveries=max_open_deliveries,
    )
    _LAST_CONFIG = config
    return config


def _window_minute(point: str) -> tuple[int, bool] | None:
    """``"mon 08:00"`` -> ``(minute of the UTC week, False)``; ``"daily 11:00"`` -> ``(minute of
    the UTC day, True)``; ``None`` when unparseable."""
    text = (point or "").strip().lower()
    if not text or RUN_WINDOW_PATTERN.match(text) is None:
        return None
    day, _, clock = text.partition(" ")
    hour, _, minute = clock.partition(":")
    of_day = int(hour) * 60 + int(minute)
    if day == DAILY:
        return of_day, True
    return WINDOW_DAYS.index(day) * _DAY_MINUTES + of_day, False


def in_run_window(config: Config, now: datetime) -> bool:
    """True when ``now`` (UTC) falls inside ``[run_window_start, run_window_end)`` (D3, D72).

    Pure and wrap-aware: a weekly window whose end is earlier in the week than its start runs
    past Sunday into the next week, and a daily one whose end is earlier in the day runs past
    midnight (B410). Both endpoints empty means the window is always open, and so does an
    endpoint the harness cannot parse, or a weekday paired with ``daily`` — the window narrows
    what runs, it never invents a stop.
    """
    start = _window_minute(getattr(config, "run_window_start", ""))
    end = _window_minute(getattr(config, "run_window_end", ""))
    if start is None or end is None or start[1] != end[1]:
        return True
    moment = as_utc(now)
    of_day = moment.hour * 60 + moment.minute
    # A daily window compares the minute of the day; a weekly one the minute of the week,
    # 0..10079 (weekday() is 0..6, so this cannot reach a week). The wrap past Sunday or past
    # midnight is the ``start > end`` branch below, not arithmetic here.
    current = of_day if start[1] else moment.weekday() * _DAY_MINUTES + of_day
    if start[0] <= end[0]:
        return start[0] <= current < end[0]
    return current >= start[0] or current < end[0]


def is_daily_window(config: Config) -> bool:
    """True when both run-window endpoints are ``daily`` (D72, B413)."""
    start = _window_minute(getattr(config, "run_window_start", ""))
    end = _window_minute(getattr(config, "run_window_end", ""))
    return start is not None and end is not None and start[1] and end[1]


def run_window_label(config: Config) -> str:
    """``mon 08:00-tue 20:00`` or ``daily 11:00-16:00`` (B411); ``""`` when always open."""
    start = str(getattr(config, "run_window_start", "") or "").strip()
    end = str(getattr(config, "run_window_end", "") or "").strip()
    if not start or not end:
        return ""
    prefix = f"{DAILY} "
    if start.startswith(prefix) and end.startswith(prefix):
        return f"{start}-{end[len(prefix):]}"
    return f"{start}-{end}"


def retired_keys_seen() -> tuple[tuple[str, str], ...]:
    """Every retired key the last :func:`load_config` read, as ``(key, source)`` (D74).

    ``source`` is ``".env"`` or ``".harness/config.json"``. ``harness doctor`` reports these as
    warnings, never as problems: a stale line in the operator's own file must not stop the fleet.
    """
    return tuple(_LAST_RETIRED)


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
