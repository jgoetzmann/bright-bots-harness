"""`.env` parsing into a frozen :class:`Config`.

This is the only module in the package permitted to read ``os.environ`` (§9, I-4).
Everything else receives values through :class:`Config` or through
:func:`secret_values` / :func:`read_secret`.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Mapping

from harness.errors import ConfigError

__all__ = ["Config", "load_config", "SECRET_KEYS", "secret_values", "read_secret"]

SECRET_KEYS: tuple[str, ...] = ("HARNESS_GITHUB_TOKEN", "ANTHROPIC_API_KEY")

#: The `.env` as most recently parsed by :func:`load_config`, so :func:`secret_values`
#: can redact secrets that came from the file rather than from the environment.
_LAST_ENV: dict[str, str] = {}

#: Every `.env` key that maps onto a :class:`Config` field. All are required; defaults
#: live in `.env.example`, never in code.
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
)

KNOWN_KEYS: tuple[str, ...] = FIELD_KEYS + SECRET_KEYS

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

_TOKEN_SHAPES: tuple[re.Pattern[str], ...] = (
    re.compile(r"^github_pat_[A-Za-z0-9_]{40,}$"),
    re.compile(r"^ghp_[A-Za-z0-9]{30,}$"),
)


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


def parse_env_text(text: str) -> dict[str, str]:
    """Parse `.env` text: ``KEY=VALUE``, ``#`` comments, blank lines, optional quotes."""
    out: dict[str, str] = {}
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
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
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
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


def token_shape_ok(token: str) -> bool:
    """True when ``token`` matches a fine-grained or classic personal-token shape."""
    candidate = token.strip()
    return any(pattern.match(candidate) for pattern in _TOKEN_SHAPES)


def load_config(
    env_path: Path | None = None, *, environ: Mapping[str, str] | None = None
) -> Config:
    """Read `.env` (B1), validate it (B2-B5, B22), and return a frozen Config (B6)."""
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

    source: Mapping[str, str] = os.environ if environ is None else environ
    values: dict[str, str] = dict(file_values)
    for key in KNOWN_KEYS:
        if key in source:
            values[key] = source[key]

    _LAST_ENV.clear()
    _LAST_ENV.update(values)

    for key in FIELD_KEYS:
        if key not in values:
            raise ConfigError(f"missing required key: {key}")

    base_dir = path.resolve().parent

    backend = values["BACKEND"].strip().lower()
    if backend not in ("cli", "fake"):
        raise ConfigError(f"BACKEND must be one of cli, fake; got {values['BACKEND']!r}")

    repo = values["REPO"].strip()
    if "/" not in repo:
        raise ConfigError(f"REPO must be owner/name; got {values['REPO']!r}")

    permission_tier = _require_int(values, "PERMISSION_TIER")
    if permission_tier != 0:
        raise ConfigError(f"PERMISSION_TIER must be 0 in delivery 1; got {permission_tier}")

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
    missing_stages = [stage for stage in _STAGE_KEYS if stage not in turns]
    if missing_stages:
        raise ConfigError(f"max_turns is missing stage keys: {', '.join(missing_stages)}")
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

    return Config(
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
    )


def secret_values() -> tuple[str, ...]:
    """Every non-empty secret value known from ``os.environ`` and the last-loaded `.env`."""
    found: list[str] = []
    for key in SECRET_KEYS:
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
