"""B1-B6 plus B22's config-level rejection, and the B49/B79/B81 config surface.

HARNESS-SPEC section 5.1 and RUN-DECISIONS "Config extras" are the contract.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from harness import config as config_module
from harness.config import Config, load_config
from harness.errors import ConfigError

# Inline fixtures (duplicated on purpose; reconcile collapses them).
# Shapes come from RUN-DECISIONS "Identity":
#   ^github_pat_[A-Za-z0-9_]{40,}$ and ^ghp_[A-Za-z0-9]{30,}$
VALID_PAT = "github_pat_" + "A1b2C3d4E5" * 5
VALID_GHP = "ghp_" + "Z9y8X7w6V5" * 4


# --------------------------------------------------------------------------
# B1 - load_config reads .env from the given path, falling back to ./.env
# --------------------------------------------------------------------------


def test_b1_load_config_reads_every_key_from_the_given_env_path(tmp_path, env_file):
    """B1: every .env key from RUN-DECISIONS lands on the matching Config field."""
    config = load_config(env_path=env_file, environ={})

    assert isinstance(config, Config)
    assert config.backend == "fake"
    assert config.repo == "Bright-Bots-Initiative/brightboost"
    assert config.permission_tier == 0
    assert config.allowlist_label == "harness-ok"
    assert config.weekly_budget_pct == pytest.approx(40.0)
    assert config.session_budget_pct == pytest.approx(15.0)
    assert config.reserve_pct == pytest.approx(10.0)
    assert config.weekly_reset_day == "monday"
    assert config.max_concurrent_clones == 1
    assert config.max_retries_gates == 2
    assert config.github_api_ceiling_per_hour == 50
    assert config.min_free_disk_gb == pytest.approx(5.0)
    assert config.fullsend_enabled is False
    assert Path(config.db_path).resolve() == (tmp_path / "harness.db").resolve()
    assert Path(config.runs_dir).resolve() == (tmp_path / "runs").resolve()
    assert Path(config.packages_dir).resolve() == (tmp_path / "packages").resolve()
    assert Path(config.halt_file).resolve() == (tmp_path / "HALT").resolve()


def test_b1_max_turns_keys_collapse_into_one_mapping(env_file):
    """B1: MAX_TURNS_<STAGE> keys become the max_turns mapping of section 5.1."""
    config = load_config(env_path=env_file, environ={})

    assert dict(config.max_turns) == {
        "discover": 10,
        "propose": 30,
        "implement": 80,
        "package": 10,
    }


def test_b1_falls_back_to_dot_env_in_the_current_directory(tmp_path, monkeypatch, write_env):
    """B1: with no env_path, load_config reads ./.env."""
    write_env(tmp_path / ".env", REPO="fallback-owner/fallback-repo")
    monkeypatch.chdir(tmp_path)

    config = load_config(environ={})

    assert config.repo == "fallback-owner/fallback-repo"


def test_b1_missing_dot_env_raises_config_error(tmp_path, monkeypatch):
    """B1: no .env at ./.env and no env_path is a ConfigError, not a default Config."""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)

    with pytest.raises(ConfigError):
        load_config(environ={})


def test_b1_missing_env_path_raises_config_error(tmp_path):
    """B1: an env_path that does not exist is a ConfigError."""
    with pytest.raises(ConfigError):
        load_config(env_path=tmp_path / "nowhere" / ".env", environ={})


def test_b1_quotes_comments_and_blank_lines_are_parsed(tmp_path, default_env):
    """B1: the .env grammar of RUN-DECISIONS - comments, blanks, optional quotes."""
    values = dict(default_env)
    values["REPO"] = '"Bright-Bots-Initiative/brightboost"'
    values["ALLOWLIST_LABEL"] = "'harness-ok'"
    lines = ["# leading comment", ""]
    for key, value in values.items():
        lines.append(f"{key}={value}")
        lines.append("")
    lines.append("# trailing comment")
    path = tmp_path / ".env"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    config = load_config(env_path=path, environ={})

    assert config.repo == "Bright-Bots-Initiative/brightboost"
    assert config.allowlist_label == "harness-ok"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("true", True),
        ("TRUE", True),
        ("True", True),
        ("1", True),
        ("yes", True),
        ("YES", True),
        ("false", False),
        ("FALSE", False),
        ("0", False),
        ("no", False),
        ("No", False),
    ],
)
def test_b1_boolean_values_parse_case_insensitively(tmp_path, write_env, raw, expected):
    """B1: FULLSEND_ENABLED accepts true/false/1/0/yes/no, case-insensitively."""
    path = write_env(tmp_path / ".env", FULLSEND_ENABLED=raw)

    config = load_config(env_path=path, environ={})

    assert config.fullsend_enabled is expected


def test_b1_environ_override_wins_over_the_env_file(env_file):
    """B1: the environ mapping overrides .env key for key."""
    config = load_config(
        env_path=env_file,
        environ={"WEEKLY_BUDGET_PCT": "12.5", "REPO": "other/repo", "BACKEND": "cli"},
    )

    assert config.weekly_budget_pct == pytest.approx(12.5)
    assert config.repo == "other/repo"
    assert config.backend == "cli"
    # untouched keys still come from the file
    assert config.session_budget_pct == pytest.approx(15.0)


def test_b1_an_explicit_empty_environ_ignores_the_process_environment(env_file, monkeypatch):
    """B1: environ={} means no overrides; os.environ MUST NOT leak in."""
    monkeypatch.setenv("PERMISSION_TIER", "1")
    monkeypatch.setenv("WEEKLY_BUDGET_PCT", "99")

    config = load_config(env_path=env_file, environ={})

    assert config.permission_tier == 0
    assert config.weekly_budget_pct == pytest.approx(40.0)


def test_b1_relative_paths_resolve_against_the_env_file_directory(tmp_path, write_env):
    """B1: DB_PATH/RUNS_DIR/PACKAGES_DIR/HALT_FILE are relative to the .env dir."""
    env_dir = tmp_path / "nested" / "cfg"
    path = write_env(
        env_dir / ".env",
        DB_PATH="data/h.db",
        RUNS_DIR="work/runs",
        PACKAGES_DIR="work/packages",
        HALT_FILE="flags/HALT",
    )

    config = load_config(env_path=path, environ={})

    assert Path(config.db_path).is_absolute()
    assert Path(config.db_path).resolve() == (env_dir / "data" / "h.db").resolve()
    assert Path(config.runs_dir).resolve() == (env_dir / "work" / "runs").resolve()
    assert Path(config.packages_dir).resolve() == (env_dir / "work" / "packages").resolve()
    assert Path(config.halt_file).resolve() == (env_dir / "flags" / "HALT").resolve()


def test_b1_absolute_paths_are_left_alone(tmp_path, write_env):
    """B1: an absolute DB_PATH is not re-rooted under the .env directory."""
    absolute = (tmp_path / "elsewhere" / "custom.db").resolve()
    path = write_env(tmp_path / ".env", DB_PATH=str(absolute))

    config = load_config(env_path=path, environ={})

    assert Path(config.db_path).resolve() == absolute


# --------------------------------------------------------------------------
# B2 - a missing required key raises ConfigError naming the key
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "missing",
    [
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
    ],
)
def test_b2_a_missing_required_key_raises_config_error_naming_it(tmp_path, write_env, missing):
    """B2: every key is required, and the error names the key that is missing."""
    path = write_env(tmp_path / ".env", **{missing: None})

    with pytest.raises(ConfigError) as excinfo:
        load_config(env_path=path, environ={})

    assert missing in str(excinfo.value)


def test_b2_an_empty_env_file_raises_config_error(tmp_path):
    """B2: an empty .env is missing every key, not an all-defaults Config."""
    path = tmp_path / ".env"
    path.write_text("", encoding="utf-8", newline="\n")

    with pytest.raises(ConfigError):
        load_config(env_path=path, environ={})


# --------------------------------------------------------------------------
# B3 - an unknown key in .env raises ConfigError naming the key
# --------------------------------------------------------------------------


@pytest.mark.parametrize("unknown", ["WEEKLY_BUDGET_PTC", "MAX_TURNS_SHIP", "NOT_A_SETTING"])
def test_b3_an_unknown_key_raises_config_error_naming_it(tmp_path, write_env, unknown):
    """B3: a typo'd key is an error, not a silent default."""
    path = write_env(tmp_path / ".env", **{unknown: "40"})

    with pytest.raises(ConfigError) as excinfo:
        load_config(env_path=path, environ={})

    assert unknown in str(excinfo.value)


def test_b3_the_two_secret_keys_are_accepted_and_not_stored_on_config(tmp_path, write_env):
    """B3: HARNESS_GITHUB_TOKEN and ANTHROPIC_API_KEY are known keys, not unknown ones."""
    path = write_env(
        tmp_path / ".env", HARNESS_GITHUB_TOKEN=VALID_PAT, ANTHROPIC_API_KEY="sk-ant-example"
    )

    config = load_config(env_path=path, environ={})

    field_names = {f.name for f in dataclasses.fields(config)}
    assert "harness_github_token" not in field_names
    assert "anthropic_api_key" not in field_names
    assert VALID_PAT not in repr(config)


def test_b3_unknown_keys_in_environ_are_ignored_rather_than_rejected(env_file):
    """B3: the unknown-key rule is about .env; environ is filtered to known keys."""
    config = load_config(env_path=env_file, environ={"TOTALLY_UNRELATED": "x", "PATH": "y"})

    assert config.repo == "Bright-Bots-Initiative/brightboost"


# --------------------------------------------------------------------------
# B4 - permission_tier other than 0 raises ConfigError in Delivery 1
# --------------------------------------------------------------------------


@pytest.mark.parametrize("tier", ["1", "3"])
def test_b4_a_non_zero_permission_tier_is_rejected(tmp_path, write_env, tier):
    """B4: Delivery 1 is Tier 0; any other tier is a startup error.

    Amended in Delivery 2 (DECISIONS D14): tier 2 is now a mode of the harness and is accepted;
    tier 1 and anything above 2 remain startup errors.
    """
    path = write_env(tmp_path / ".env", PERMISSION_TIER=tier)

    with pytest.raises(ConfigError):
        load_config(env_path=path, environ={})


def test_b4_a_non_zero_permission_tier_from_environ_is_also_rejected(env_file):
    """B4: the tier check applies to the merged value, not only the file value."""
    with pytest.raises(ConfigError):
        load_config(env_path=env_file, environ={"PERMISSION_TIER": "1"})


# --------------------------------------------------------------------------
# B5 - budget bounds
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("WEEKLY_BUDGET_PCT", "0"),
        ("WEEKLY_BUDGET_PCT", "0.0"),
        ("WEEKLY_BUDGET_PCT", "-1"),
        ("WEEKLY_BUDGET_PCT", "100.0001"),
        ("WEEKLY_BUDGET_PCT", "1000"),
        ("SESSION_BUDGET_PCT", "0"),
        ("SESSION_BUDGET_PCT", "-0.5"),
        ("SESSION_BUDGET_PCT", "100.0001"),
        ("RESERVE_PCT", "-0.0001"),
        ("RESERVE_PCT", "-10"),
        ("RESERVE_PCT", "100"),
        ("RESERVE_PCT", "100.0001"),
    ],
)
def test_b5_out_of_range_budget_values_raise_config_error(tmp_path, write_env, key, value):
    """B5: weekly/session must be in (0, 100]; reserve must be in [0, 100)."""
    path = write_env(tmp_path / ".env", **{key: value})

    with pytest.raises(ConfigError):
        load_config(env_path=path, environ={})


def test_b5_the_inclusive_boundaries_are_accepted(tmp_path, write_env):
    """B5: weekly=100, session=100 and reserve=0 are inside the ranges."""
    path = write_env(
        tmp_path / ".env",
        WEEKLY_BUDGET_PCT="100",
        SESSION_BUDGET_PCT="100",
        RESERVE_PCT="0",
    )

    config = load_config(env_path=path, environ={})

    assert config.weekly_budget_pct == pytest.approx(100.0)
    assert config.session_budget_pct == pytest.approx(100.0)
    assert config.reserve_pct == pytest.approx(0.0)


def test_b5_the_smallest_admissible_budgets_are_accepted(tmp_path, write_env):
    """B5: anything strictly above 0 is a legal budget; reserve may approach 100."""
    path = write_env(
        tmp_path / ".env",
        WEEKLY_BUDGET_PCT="0.0001",
        SESSION_BUDGET_PCT="0.0001",
        RESERVE_PCT="99.9999",
    )

    config = load_config(env_path=path, environ={})

    assert config.weekly_budget_pct == pytest.approx(0.0001)
    assert config.reserve_pct == pytest.approx(99.9999)


# --------------------------------------------------------------------------
# B6 - Config is frozen
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("weekly_budget_pct", 1.0),
        ("permission_tier", 1),
        ("repo", "someone/else"),
        ("db_path", Path("other.db")),
        ("fullsend_enabled", True),
    ],
)
def test_b6_config_attribute_assignment_raises(sample_config, field, value):
    """B6: Config is a frozen dataclass; assignment raises FrozenInstanceError."""
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(sample_config, field, value)


def test_b6_config_attribute_deletion_raises(sample_config):
    """B6: a frozen dataclass refuses attribute deletion too."""
    with pytest.raises(dataclasses.FrozenInstanceError):
        del sample_config.repo


def test_b6_setting_an_unknown_attribute_also_raises(sample_config):
    """B6: frozen means no new attributes either."""
    with pytest.raises(dataclasses.FrozenInstanceError):
        sample_config.invented_field = 1


# --------------------------------------------------------------------------
# B22 - max_concurrent_clones > 1 is a startup error (config half)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["2", "4", "16"])
def test_b22_load_config_rejects_more_than_one_concurrent_clone(tmp_path, write_env, value):
    """B22: concurrency above one is refused at load time in Delivery 1."""
    path = write_env(tmp_path / ".env", MAX_CONCURRENT_CLONES=value)

    with pytest.raises(ConfigError):
        load_config(env_path=path, environ={})


def test_b22_exactly_one_concurrent_clone_is_accepted(tmp_path, write_env):
    """B22: one is the only legal value, and it loads."""
    path = write_env(tmp_path / ".env", MAX_CONCURRENT_CLONES="1")

    config = load_config(env_path=path, environ={})

    assert config.max_concurrent_clones == 1


# --------------------------------------------------------------------------
# B79 / B81 - token presence and shape are reported without reading the value
# --------------------------------------------------------------------------


def test_b79_token_present_is_false_when_the_key_is_empty(env_file):
    """B79: an empty HARNESS_GITHUB_TOKEN counts as absent."""
    config = load_config(env_path=env_file, environ={})

    assert config.github_token_present is False
    assert config.github_token_shape_ok is False


def test_b79_token_present_is_false_when_the_key_is_missing_from_env(tmp_path, write_env):
    """B79: an absent HARNESS_GITHUB_TOKEN is not an error and reports absent."""
    path = write_env(tmp_path / ".env", HARNESS_GITHUB_TOKEN=None)

    config = load_config(env_path=path, environ={})

    assert config.github_token_present is False
    assert config.github_token_shape_ok is False


def test_b79_token_present_is_true_when_the_env_file_sets_it(tmp_path, write_env):
    """B79: a value in .env sets github_token_present without using the value."""
    path = write_env(tmp_path / ".env", HARNESS_GITHUB_TOKEN=VALID_PAT)

    config = load_config(env_path=path, environ={})

    assert config.github_token_present is True


def test_b79_token_present_is_true_when_environ_sets_it(env_file):
    """B79: the environ override participates in presence detection."""
    config = load_config(env_path=env_file, environ={"HARNESS_GITHUB_TOKEN": VALID_GHP})

    assert config.github_token_present is True


@pytest.mark.parametrize("token", [VALID_PAT, VALID_GHP])
def test_b81_valid_token_shapes_set_shape_ok(tmp_path, write_env, token):
    """B81: github_pat_... and ghp_... shapes are accepted."""
    path = write_env(tmp_path / ".env", HARNESS_GITHUB_TOKEN=token)

    config = load_config(env_path=path, environ={})

    assert config.github_token_present is True
    assert config.github_token_shape_ok is True


@pytest.mark.parametrize(
    "token",
    [
        "ghp_short",
        "github_pat_tooshort",
        "gho_" + "A" * 40,
        "not-a-token-at-all",
        "sk-ant-" + "A" * 40,
        "GITHUB_PAT_" + "A" * 45,
    ],
)
def test_b81_bad_token_shapes_are_present_but_not_shape_ok(tmp_path, write_env, token):
    """B81: an implausible token is reported present with shape_ok false."""
    path = write_env(tmp_path / ".env", HARNESS_GITHUB_TOKEN=token)

    config = load_config(env_path=path, environ={})

    assert config.github_token_present is True
    assert config.github_token_shape_ok is False


# --------------------------------------------------------------------------
# B49 - secret_values() feeds redaction of section 5.8's env-value pattern
# --------------------------------------------------------------------------


def test_b49_secret_values_includes_a_token_sourced_from_the_env_file(
    tmp_path, write_env, monkeypatch
):
    """B49: a secret that only ever lived in .env is still redactable."""
    monkeypatch.delenv("HARNESS_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    path = write_env(tmp_path / ".env", HARNESS_GITHUB_TOKEN=VALID_PAT)

    load_config(env_path=path, environ={})

    assert VALID_PAT in config_module.secret_values()


def test_b49_secret_values_omits_empty_secrets(tmp_path, write_env, monkeypatch):
    """B49: an unset secret contributes no empty string, which would match everywhere."""
    monkeypatch.delenv("HARNESS_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    path = write_env(tmp_path / ".env")

    load_config(env_path=path, environ={})

    assert "" not in config_module.secret_values()


def test_b49_secret_keys_names_exactly_the_two_secret_bearing_keys():
    """B49: SECRET_KEYS is the list section 5.8 redaction reads."""
    assert tuple(config_module.SECRET_KEYS) == ("HARNESS_GITHUB_TOKEN", "ANTHROPIC_API_KEY")


def test_b49_read_secret_returns_empty_string_for_an_absent_key(tmp_path, write_env, monkeypatch):
    """B49: read_secret never raises for an absent key; it returns the empty string."""
    monkeypatch.delenv("HARNESS_GITHUB_TOKEN", raising=False)
    path = write_env(tmp_path / ".env", HARNESS_GITHUB_TOKEN="")

    load_config(env_path=path, environ={})

    assert config_module.read_secret("HARNESS_GITHUB_TOKEN") == ""


# --------------------------------------------------------------------------
# Delivery 2 — A30 / B112 / B123 and RUN-DECISIONS-D2 §2 (new keys, .harness/config.json,
# the I-11 token door). Appended by the D2 spec-tester (T3); additions only (D2-R12.3).
# --------------------------------------------------------------------------

import json

# RUN-DECISIONS-D2 §2 — the new keys with their .env.example values (inline, on purpose).
D2_ENV: dict[str, str] = {
    "WEEKLY_CAP_USD": "25.00",
    "PER_CALL_CAP_USD": "3.00",
    "MAX_CONCURRENT_ITEMS": "1",
    "MAX_REVISE_CYCLES": "3",
    "FORK_REPO": "",
    "UPSTREAM_REPO": "Bright-Bots-Initiative/brightboost",
    "TRUST_FILE": ".harness/trust.txt",
    "NOTIFY_POLL_HOURS": "3",
    "MAX_SUBISSUES": "8",
    "SELF_REPO": "jgoetzmann/bright-bots-harness",
    "TRACKING_ISSUE": "",
    "STORE_BACKEND": "sqlite",
    "WEEKLY_USAGE_STOP_PCT": "90",
    "SESSION_USAGE_STOP_PCT": "70",
    "OVERRUN_PCT": "10",
    "RUN_WINDOW_START": "",
    "RUN_WINDOW_END": "",
    "MODEL": "opus",
    "EFFORT": "xhigh",
    "INBOX_ISSUE": "0",
    "AUDIT_CAP_USD": "20.00",
    "SUGGEST_MAX_PER_RUN": "5",
    "COMMENT_UPSTREAM": "true",
    "ASK_CAP_USD": "0.50",
    "ASK_MAX_PER_DAY": "20",
    "SUGGEST_MIN_HEADROOM_PCT": "50",
}
# Every new key is required except the two that may be empty.
D2_REQUIRED_KEYS = tuple(key for key in D2_ENV if key not in ("FORK_REPO", "TRACKING_ISSUE"))
# RUN-DECISIONS-D2 §2 — appended after github_token_shape_ok, in this order.
D2_NEW_FIELDS_IN_ORDER = (
    "weekly_cap_usd",
    "per_call_cap_usd",
    "max_concurrent_items",
    "max_revise_cycles",
    "fork_repo",
    "upstream_repo",
    "trust_file",
    "notify_poll_hours",
    "max_subissues",
    "self_repo",
    "tracking_issue",
    "store_backend",
    "repo_root",
    "weekly_usage_stop_pct",
    "session_usage_stop_pct",
    "overrun_pct",
    "run_window_start",
    "run_window_end",
)
FORK = "brightboost-harness/brightboost"


@pytest.fixture
def write_d2_env(write_env):
    """conftest's write_env plus the Delivery 2 keys; overrides win, None removes."""

    def _write(path: Path, **overrides: object) -> Path:
        values: dict[str, object] = dict(D2_ENV)
        values.update(overrides)
        return write_env(path, **values)

    return _write


def write_config_json(directory: Path, payload: object) -> Path:
    """Write <directory>/.harness/config.json; a str payload is written verbatim."""
    harness_dir = directory / ".harness"
    harness_dir.mkdir(parents=True, exist_ok=True)
    path = harness_dir / "config.json"
    text = payload if isinstance(payload, str) else json.dumps(payload, indent=2)
    path.write_text(text + "\n", encoding="utf-8", newline="\n")
    return path


# --------------------------------------------------------------------------
# A30 - every new §6.5 key is read, required, and range-checked
# --------------------------------------------------------------------------


def test_a30_every_new_key_lands_on_the_matching_config_field(tmp_path, write_d2_env):
    """A30 / RUN-DECISIONS-D2 §2: the .env.example values map onto the new Config fields."""
    path = write_d2_env(tmp_path / ".env")

    config = load_config(env_path=path, environ={})

    assert config.weekly_cap_usd == pytest.approx(25.0)
    assert config.per_call_cap_usd == pytest.approx(3.0)
    assert config.max_concurrent_items == 1
    assert config.max_revise_cycles == 3
    assert config.fork_repo == ""
    assert config.upstream_repo == "Bright-Bots-Initiative/brightboost"
    assert Path(config.trust_file).is_absolute()
    assert Path(config.trust_file).resolve() == (tmp_path / ".harness" / "trust.txt").resolve()
    assert config.notify_poll_hours == 3
    assert config.max_subissues == 8
    assert config.self_repo == "jgoetzmann/bright-bots-harness"
    assert config.tracking_issue is None
    assert config.store_backend == "sqlite"
    assert Path(config.repo_root).resolve() == tmp_path.resolve()


def test_a30_repo_root_is_the_directory_containing_the_env_file(tmp_path, write_d2_env):
    """A30 / RUN-DECISIONS-D2 §2: repo_root is where .env lives, also where .harness/ and state/ live."""
    env_dir = tmp_path / "nested" / "root"
    path = write_d2_env(env_dir / ".env")

    config = load_config(env_path=path, environ={})

    assert Path(config.repo_root).resolve() == env_dir.resolve()
    assert Path(config.trust_file).resolve() == (env_dir / ".harness" / "trust.txt").resolve()


def test_a30_an_absolute_trust_file_is_left_alone(tmp_path, write_d2_env):
    """A30 / RUN-DECISIONS-D2 §2: TRUST_FILE is relative to the .env directory only when relative."""
    absolute = (tmp_path / "elsewhere" / "trust.txt").resolve()
    path = write_d2_env(tmp_path / ".env", TRUST_FILE=str(absolute))

    config = load_config(env_path=path, environ={})

    assert Path(config.trust_file).resolve() == absolute


@pytest.mark.parametrize("missing", D2_REQUIRED_KEYS)
def test_a30_a_missing_new_key_raises_config_error_naming_it(tmp_path, write_d2_env, missing):
    """A30 (handoff §6.5): every new key is required, no defaults in code; the error names it."""
    path = write_d2_env(tmp_path / ".env", **{missing: None})

    with pytest.raises(ConfigError) as excinfo:
        load_config(env_path=path, environ={})

    assert missing in str(excinfo.value)


def test_a30_fork_repo_and_tracking_issue_may_be_empty(tmp_path, write_d2_env):
    """A30 / RUN-DECISIONS-D2 §2: FORK_REPO= and TRACKING_ISSUE= are legal at tier 0."""
    path = write_d2_env(tmp_path / ".env", FORK_REPO="", TRACKING_ISSUE="")

    config = load_config(env_path=path, environ={})

    assert config.fork_repo == ""
    assert config.tracking_issue is None


def test_a30_tracking_issue_parses_to_an_int(tmp_path, write_d2_env):
    """A30 / RUN-DECISIONS-D2 §2: TRACKING_ISSUE=816 becomes the int 816."""
    path = write_d2_env(tmp_path / ".env", TRACKING_ISSUE="816")

    config = load_config(env_path=path, environ={})

    assert config.tracking_issue == 816


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("WEEKLY_CAP_USD", "0"),
        ("WEEKLY_CAP_USD", "0.0"),
        ("WEEKLY_CAP_USD", "-5"),
        ("WEEKLY_CAP_USD", "twenty-five"),
        ("PER_CALL_CAP_USD", "0"),
        ("PER_CALL_CAP_USD", "-0.01"),
        ("PER_CALL_CAP_USD", "three"),
        ("MAX_CONCURRENT_ITEMS", "0"),
        ("MAX_CONCURRENT_ITEMS", "-1"),
        ("MAX_CONCURRENT_ITEMS", "two"),
        ("MAX_REVISE_CYCLES", "-1"),
        ("MAX_REVISE_CYCLES", "many"),
        ("NOTIFY_POLL_HOURS", "0"),
        ("NOTIFY_POLL_HOURS", "-3"),
        ("MAX_SUBISSUES", "0"),
        ("MAX_SUBISSUES", "51"),
        ("MAX_SUBISSUES", "-8"),
        ("MAX_SUBISSUES", "eight"),
        ("STORE_BACKEND", "postgres"),
        ("STORE_BACKEND", ""),
        ("STORE_BACKEND", "SQLITE"),
        ("TRACKING_ISSUE", "abc"),
        ("TRACKING_ISSUE", "-1"),
        ("UPSTREAM_REPO", ""),
        ("SELF_REPO", ""),
    ],
)
def test_a30_an_out_of_range_or_malformed_new_key_raises_config_error_naming_it(
    tmp_path, write_d2_env, key, value
):
    """A30 (handoff §6.5, RUN-DECISIONS-D2 §2): out-of-range or wrong-typed values are startup
    errors naming the key, never a silently different budget."""
    path = write_d2_env(tmp_path / ".env", **{key: value})

    with pytest.raises(ConfigError) as excinfo:
        load_config(env_path=path, environ={})

    assert key in str(excinfo.value)


def test_a30_the_boundary_values_are_accepted(tmp_path, write_d2_env):
    """A30 / RUN-DECISIONS-D2 §2: the inclusive ends of every range load."""
    path = write_d2_env(
        tmp_path / ".env",
        WEEKLY_CAP_USD="0.01",
        PER_CALL_CAP_USD="0.01",
        MAX_CONCURRENT_ITEMS="1",
        MAX_REVISE_CYCLES="0",
        NOTIFY_POLL_HOURS="1",
        MAX_SUBISSUES="1",
    )

    config = load_config(env_path=path, environ={})

    assert config.weekly_cap_usd == pytest.approx(0.01)
    assert config.per_call_cap_usd == pytest.approx(0.01)
    assert config.max_concurrent_items == 1
    assert config.max_revise_cycles == 0
    assert config.notify_poll_hours == 1
    assert config.max_subissues == 1


def test_a30_max_subissues_fifty_is_the_upper_bound(tmp_path, write_d2_env):
    """A30 / RUN-DECISIONS-D2 §2: MAX_SUBISSUES 1..50 — fifty loads, fifty-one does not."""
    ok = write_d2_env(tmp_path / "ok" / ".env", MAX_SUBISSUES="50")
    assert load_config(env_path=ok, environ={}).max_subissues == 50

    bad = write_d2_env(tmp_path / "bad" / ".env", MAX_SUBISSUES="51")
    with pytest.raises(ConfigError):
        load_config(env_path=bad, environ={})


def test_a30_upstream_repo_must_equal_repo(tmp_path, write_d2_env):
    """A30 / RUN-DECISIONS-D2 §2: UPSTREAM_REPO must equal REPO; when both change together it loads."""
    path = write_d2_env(
        tmp_path / ".env", REPO="Other-Org/product", UPSTREAM_REPO="Other-Org/product"
    )

    config = load_config(env_path=path, environ={})

    assert config.repo == "Other-Org/product"
    assert config.upstream_repo == "Other-Org/product"


def test_b1_environ_overrides_the_env_file_for_a_new_key(tmp_path, write_d2_env):
    """B1 (D1 rule, unchanged) applied to A30's keys: environ overrides .env key for key."""
    path = write_d2_env(tmp_path / ".env")

    config = load_config(env_path=path, environ={"WEEKLY_CAP_USD": "50", "MAX_SUBISSUES": "4"})

    assert config.weekly_cap_usd == pytest.approx(50.0)
    assert config.max_subissues == 4


def test_a30_the_new_fields_follow_github_token_shape_ok_in_the_frozen_order(
    tmp_path, write_d2_env
):
    """RUN-DECISIONS-D2 §2: the new Config fields are appended after github_token_shape_ok in
    exactly this order."""
    path = write_d2_env(tmp_path / ".env")
    config = load_config(env_path=path, environ={})

    names = [f.name for f in dataclasses.fields(config)]

    assert "github_token_shape_ok" in names
    after = names[names.index("github_token_shape_ok") + 1 :]
    # Each delivery appends its own block after this one, in its own frozen order: D3 added
    # five, B225 added model and effort. What D2 pins is that its five come first and in order.
    assert tuple(after[: len(D2_NEW_FIELDS_IN_ORDER)]) == D2_NEW_FIELDS_IN_ORDER


def test_b6_the_new_fields_are_frozen_too(tmp_path, write_d2_env):
    """B6 (unchanged): Config stays a frozen dataclass; the new fields cannot be assigned."""
    path = write_d2_env(tmp_path / ".env")
    config = load_config(env_path=path, environ={})

    with pytest.raises(dataclasses.FrozenInstanceError):
        config.weekly_cap_usd = 1.0
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.store_backend = "github"


# --------------------------------------------------------------------------
# B123 - concurrency above one is Actions-mode (github store) only
# --------------------------------------------------------------------------


@pytest.mark.parametrize("items", ["2", "3", "8"])
def test_b123_max_concurrent_items_above_one_requires_the_github_store(
    tmp_path, write_d2_env, items
):
    """B123 (handoff §6.4): MAX_CONCURRENT_ITEMS > 1 with STORE_BACKEND=sqlite is a ConfigError."""
    path = write_d2_env(tmp_path / ".env", MAX_CONCURRENT_ITEMS=items, STORE_BACKEND="sqlite")

    with pytest.raises(ConfigError) as excinfo:
        load_config(env_path=path, environ={})

    assert "MAX_CONCURRENT_ITEMS" in str(excinfo.value)


def test_b123_max_concurrent_items_above_one_is_accepted_with_the_github_store(
    tmp_path, write_d2_env
):
    """B123 (handoff §6.4): the handoff default of 3 loads under the github store."""
    path = write_d2_env(tmp_path / ".env", MAX_CONCURRENT_ITEMS="3", STORE_BACKEND="github")

    config = load_config(env_path=path, environ={})

    assert config.max_concurrent_items == 3
    assert config.store_backend == "github"


def test_b123_the_github_store_is_allowed_at_tier_0(tmp_path, write_d2_env):
    """B123 / RUN-DECISIONS-D2 §2: nothing ties STORE_BACKEND=github to tier 2."""
    path = write_d2_env(tmp_path / ".env", STORE_BACKEND="github")

    config = load_config(env_path=path, environ={})

    assert config.store_backend == "github"
    assert config.permission_tier == 0


# --------------------------------------------------------------------------
# B4 (amended, RUN-DECISIONS-D2 R-B/R-C) - tier 2 exists and has preconditions
# --------------------------------------------------------------------------


def test_b4_d2_tier_2_is_accepted_with_the_github_store_and_a_fork(tmp_path, write_d2_env):
    """B4 as amended (RUN-DECISIONS-D2 R-B, §2): PERMISSION_TIER=2 loads with STORE_BACKEND=github
    and a non-empty FORK_REPO."""
    path = write_d2_env(
        tmp_path / ".env", PERMISSION_TIER="2", STORE_BACKEND="github", FORK_REPO=FORK
    )

    config = load_config(env_path=path, environ={})

    assert config.permission_tier == 2
    assert config.fork_repo == FORK


def test_b4_d2_tier_2_with_the_sqlite_store_is_a_config_error(tmp_path, write_d2_env):
    """B4 as amended (RUN-DECISIONS-D2 §2): tier 2 requires store_backend == github."""
    path = write_d2_env(
        tmp_path / ".env", PERMISSION_TIER="2", STORE_BACKEND="sqlite", FORK_REPO=FORK
    )

    with pytest.raises(ConfigError):
        load_config(env_path=path, environ={})


def test_b4_d2_tier_2_without_a_fork_is_a_config_error(tmp_path, write_d2_env):
    """B4 as amended (RUN-DECISIONS-D2 §2): tier 2 requires a non-empty FORK_REPO."""
    path = write_d2_env(
        tmp_path / ".env", PERMISSION_TIER="2", STORE_BACKEND="github", FORK_REPO=""
    )

    with pytest.raises(ConfigError) as excinfo:
        load_config(env_path=path, environ={})

    assert "FORK_REPO" in str(excinfo.value)


@pytest.mark.parametrize("tier", ["1", "3", "-1", "two", ""])
def test_b4_d2_only_tiers_0_and_2_exist(tmp_path, write_d2_env, tier):
    """B4 as amended (RUN-DECISIONS-D2 R-B): PERMISSION_TIER outside {0, 2} is a ConfigError."""
    path = write_d2_env(
        tmp_path / ".env", PERMISSION_TIER=tier, STORE_BACKEND="github", FORK_REPO=FORK
    )

    with pytest.raises(ConfigError):
        load_config(env_path=path, environ={})


# --------------------------------------------------------------------------
# B112 - .harness/config.json overrides .env for the eleven knobs and nothing else
# --------------------------------------------------------------------------


def test_b112_config_json_overrides_env_for_weekly_cap_usd(tmp_path, write_d2_env):
    """B112 / RUN-DECISIONS-D2 §2: .harness/config.json wins over .env for WEEKLY_CAP_USD."""
    path = write_d2_env(tmp_path / ".env", WEEKLY_CAP_USD="25.00")
    write_config_json(tmp_path, {"WEEKLY_CAP_USD": 40.0})

    config = load_config(env_path=path, environ={})

    assert config.weekly_cap_usd == pytest.approx(40.0)


def test_b112_config_json_overrides_reserve_pct_and_tracking_issue(tmp_path, write_d2_env):
    """B112 / RUN-DECISIONS-D2 §2: RESERVE_PCT and TRACKING_ISSUE are among the eleven knobs."""
    path = write_d2_env(tmp_path / ".env", RESERVE_PCT="10", TRACKING_ISSUE="")
    write_config_json(tmp_path, {"RESERVE_PCT": 20, "TRACKING_ISSUE": 816})

    config = load_config(env_path=path, environ={})

    assert config.reserve_pct == pytest.approx(20.0)
    assert config.tracking_issue == 816


# The Config field each knob key lands on (harness/config.py, load_config's tail).
KNOB_KEY_TO_FIELD: dict[str, str] = {
    "WEEKLY_CAP_USD": "weekly_cap_usd",
    "PER_CALL_CAP_USD": "per_call_cap_usd",
    "RESERVE_PCT": "reserve_pct",
    "MAX_CONCURRENT_ITEMS": "max_concurrent_items",
    "MAX_REVISE_CYCLES": "max_revise_cycles",
    "NOTIFY_POLL_HOURS": "notify_poll_hours",
    "MAX_SUBISSUES": "max_subissues",
    "TRACKING_ISSUE": "tracking_issue",
    "FORK_REPO": "fork_repo",
    "UPSTREAM_REPO": "upstream_repo",
    "TRUST_FILE": "trust_file",
    "WEEKLY_USAGE_STOP_PCT": "weekly_usage_stop_pct",
    "SESSION_USAGE_STOP_PCT": "session_usage_stop_pct",
    "OVERRUN_PCT": "overrun_pct",
    "RUN_WINDOW_START": "run_window_start",
    "RUN_WINDOW_END": "run_window_end",
}


# B112 / RUN-DECISIONS-D2 §2 + RUN-DECISIONS-D3 "Config": one override per knob
# `config.CONFIG_JSON_KEYS` admits. Every value here differs from the value the `.env`
# carries for the same key (D2_ENV above, plus conftest's DEFAULT_ENV for RESERVE_PCT),
# so an override that were silently dropped would leave the .env value behind and fail the
# matching assertion. `test_b112_the_config_json_overrides_all_differ_from_the_env_values`
# holds that property; without it an override could pass while doing nothing.
ALL_KNOB_OVERRIDES: dict[str, object] = {
    "WEEKLY_CAP_USD": 30.0,
    "PER_CALL_CAP_USD": 2.5,
    "RESERVE_PCT": 15,
    "MAX_CONCURRENT_ITEMS": 2,
    "MAX_REVISE_CYCLES": 1,
    "NOTIFY_POLL_HOURS": 6,
    "MAX_SUBISSUES": 5,
    "TRACKING_ISSUE": 42,
    "FORK_REPO": FORK,
    "UPSTREAM_REPO": "Bright-Bots-Initiative/other-repo",
    "TRUST_FILE": "trust/list.txt",
    "WEEKLY_USAGE_STOP_PCT": 80,
    "SESSION_USAGE_STOP_PCT": 60,
    "OVERRUN_PCT": 5,
    "RUN_WINDOW_START": "wed 09:30",
    "RUN_WINDOW_END": "thu 21:45",
    # Delivery 4 (DELIVERY-4-HANDOFF section 7).
    "INBOX_ISSUE": 7,
    "AUDIT_CAP_USD": 12.5,
    "SUGGEST_MAX_PER_RUN": 3,
    "COMMENT_UPSTREAM": False,
    "ASK_CAP_USD": 0.25,
    "ASK_MAX_PER_DAY": 9,
    "SUGGEST_MIN_HEADROOM_PCT": 40,
}


def test_b112_config_json_may_set_every_one_of_the_knobs(tmp_path, write_d2_env):
    """B112 / RUN-DECISIONS-D2 §2 + RUN-DECISIONS-D3 "Config": every key
    `config.CONFIG_JSON_KEYS` admits is accepted together with the rest, and each one takes
    effect. The knob set is read from the source of truth, so a knob added there without an
    override and an assertion here fails this test rather than going untested.

    MAX_CONCURRENT_ITEMS > 1 requires STORE_BACKEND=github (harness/config.py), and
    STORE_BACKEND is not a knob, so it is set in the `.env` — which is the point: the
    config.json override still has to beat a different `.env` value."""
    path = write_d2_env(tmp_path / ".env", STORE_BACKEND="github")
    assert set(ALL_KNOB_OVERRIDES) == set(config_module.CONFIG_JSON_KEYS), (
        "every knob config.json admits must be exercised here: "
        f"missing {sorted(set(config_module.CONFIG_JSON_KEYS) - set(ALL_KNOB_OVERRIDES))}, "
        f"unknown {sorted(set(ALL_KNOB_OVERRIDES) - set(config_module.CONFIG_JSON_KEYS))}"
    )
    write_config_json(tmp_path, ALL_KNOB_OVERRIDES)

    config = load_config(env_path=path, environ={})

    assert config.weekly_cap_usd == pytest.approx(30.0)
    assert config.per_call_cap_usd == pytest.approx(2.5)
    assert config.reserve_pct == pytest.approx(15.0)
    assert config.max_concurrent_items == 2
    assert config.max_revise_cycles == 1
    assert config.notify_poll_hours == 6
    assert config.max_subissues == 5
    assert config.tracking_issue == 42
    assert config.fork_repo == FORK
    assert config.upstream_repo == "Bright-Bots-Initiative/other-repo"
    assert Path(config.trust_file).resolve() == (tmp_path / "trust" / "list.txt").resolve()
    assert config.weekly_usage_stop_pct == pytest.approx(80.0)
    assert config.session_usage_stop_pct == pytest.approx(60.0)
    assert config.overrun_pct == pytest.approx(5.0)
    assert config.run_window_start == "wed 09:30"
    assert config.run_window_end == "thu 21:45"


def test_b112_the_config_json_overrides_all_differ_from_the_env_values(tmp_path, write_d2_env):
    """Every override above must be distinguishable from the `.env` value it beats, or the
    assertion for that knob would pass whether the override was honoured or dropped. Loading
    the same `.env` with no config.json at all must therefore disagree with the loaded-with-
    overrides config on every one of the knobs."""
    path = write_d2_env(tmp_path / ".env", STORE_BACKEND="github")
    plain = load_config(env_path=path, environ={})
    write_config_json(tmp_path, ALL_KNOB_OVERRIDES)
    overridden = load_config(env_path=path, environ={})

    same = [
        key
        for key, field in KNOB_KEY_TO_FIELD.items()
        if getattr(plain, field) == getattr(overridden, field)
    ]
    assert same == [], (
        f"these overrides are byte-identical to the .env value they must beat: {same}"
    )




def test_b112_config_json_is_read_from_repo_root_not_cwd(tmp_path, write_d2_env, monkeypatch):
    """B112 / RUN-DECISIONS-D2 §2: the file is `repo_root/.harness/config.json`, repo_root being
    the .env directory — a config.json in the cwd is not consulted."""
    env_dir = tmp_path / "repo"
    path = write_d2_env(env_dir / ".env", WEEKLY_CAP_USD="25.00")
    write_config_json(env_dir, {"WEEKLY_CAP_USD": 40.0})
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    write_config_json(elsewhere, {"WEEKLY_CAP_USD": 99.0})
    monkeypatch.chdir(elsewhere)

    config = load_config(env_path=path, environ={})

    assert config.weekly_cap_usd == pytest.approx(40.0)


@pytest.mark.parametrize(
    "unknown",
    [
        "GATE_SEQUENCE",
        "REDACTION_PATTERNS",
        "PERMISSION_TIER",
        "BACKEND",
        "STORE_BACKEND",
        "SELF_REPO",
        "WEEKLY_BUDGET_PCT",
        "HARNESS_GITHUB_TOKEN",
        "weekly_cap_usd",
    ],
)
def test_b112_an_unknown_key_in_config_json_raises_config_error_naming_it(
    tmp_path, write_d2_env, unknown
):
    """B112 (handoff §5.5): config.json carries operational knobs only; anything else — a gate
    key, the tier, a D1 key, a secret, a lower-cased knob — is a ConfigError naming it."""
    path = write_d2_env(tmp_path / ".env")
    write_config_json(tmp_path, {"WEEKLY_CAP_USD": 25.0, unknown: "1"})

    with pytest.raises(ConfigError) as excinfo:
        load_config(env_path=path, environ={})

    assert unknown in str(excinfo.value)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("WEEKLY_CAP_USD", 0),
        ("WEEKLY_CAP_USD", -1),
        ("PER_CALL_CAP_USD", 0),
        ("MAX_SUBISSUES", 51),
        ("MAX_SUBISSUES", 0),
        ("NOTIFY_POLL_HOURS", 0),
        ("MAX_REVISE_CYCLES", -1),
        ("MAX_CONCURRENT_ITEMS", 0),
        ("TRACKING_ISSUE", "abc"),
    ],
)
def test_b112_an_out_of_range_value_in_config_json_raises_config_error_naming_it(
    tmp_path, write_d2_env, key, value
):
    """B112 / A30: the same range rules apply to config.json overrides."""
    path = write_d2_env(tmp_path / ".env")
    write_config_json(tmp_path, {key: value})

    with pytest.raises(ConfigError) as excinfo:
        load_config(env_path=path, environ={})

    assert key in str(excinfo.value)


def test_b112_config_json_that_is_not_json_is_a_config_error(tmp_path, write_d2_env):
    """B112: a malformed config.json is a startup error, not an ignored file."""
    path = write_d2_env(tmp_path / ".env")
    write_config_json(tmp_path, "{ this is not json")

    with pytest.raises(ConfigError):
        load_config(env_path=path, environ={})


def test_b112_config_json_that_is_not_an_object_is_a_config_error(tmp_path, write_d2_env):
    """B112 / RUN-DECISIONS-D2 §2: the file must be a JSON object."""
    path = write_d2_env(tmp_path / ".env")
    write_config_json(tmp_path, ["WEEKLY_CAP_USD", 40])

    with pytest.raises(ConfigError):
        load_config(env_path=path, environ={})


def test_b112_max_concurrent_items_from_config_json_still_needs_the_github_store(
    tmp_path, write_d2_env
):
    """B112 / B123: a knob override cannot sidestep the sqlite concurrency rule."""
    path = write_d2_env(tmp_path / ".env", STORE_BACKEND="sqlite")
    write_config_json(tmp_path, {"MAX_CONCURRENT_ITEMS": 3})

    with pytest.raises(ConfigError) as excinfo:
        load_config(env_path=path, environ={})

    assert "MAX_CONCURRENT_ITEMS" in str(excinfo.value)


# --------------------------------------------------------------------------
# I-11 - the token door: github_token() opens only at tier 2
# --------------------------------------------------------------------------


def test_i11_github_token_returns_empty_at_tier_0_even_with_a_valid_token(
    tmp_path, write_d2_env
):
    """I-11 / RUN-DECISIONS-D2 R-C, §2: at tier 0 the door is shut whatever .env holds."""
    path = write_d2_env(tmp_path / ".env", PERMISSION_TIER="0", HARNESS_GITHUB_TOKEN=VALID_PAT)

    config = load_config(env_path=path, environ={})

    assert config.permission_tier == 0
    assert config.github_token_shape_ok is True
    assert config_module.github_token() == ""


def test_i11_github_token_returns_the_value_at_tier_2(tmp_path, write_d2_env):
    """I-11 / RUN-DECISIONS-D2 §2: at tier 2 github_token() returns HARNESS_GITHUB_TOKEN."""
    path = write_d2_env(
        tmp_path / ".env",
        PERMISSION_TIER="2",
        STORE_BACKEND="github",
        FORK_REPO=FORK,
        HARNESS_GITHUB_TOKEN=VALID_PAT,
    )

    load_config(env_path=path, environ={})

    assert config_module.github_token() == VALID_PAT


def test_i11_github_token_at_tier_2_reads_the_environ_override(tmp_path, write_d2_env):
    """I-11 / B1: the last-loaded env is the merged one, so an environ token wins at tier 2."""
    path = write_d2_env(
        tmp_path / ".env",
        PERMISSION_TIER="2",
        STORE_BACKEND="github",
        FORK_REPO=FORK,
        HARNESS_GITHUB_TOKEN=VALID_PAT,
    )

    load_config(env_path=path, environ={"HARNESS_GITHUB_TOKEN": VALID_GHP})

    assert config_module.github_token() == VALID_GHP


def test_i11_github_token_returns_empty_at_tier_2_when_no_token_is_set(tmp_path, write_d2_env):
    """I-11 / RUN-DECISIONS-D2 §2: tier 2 with an empty token yields "" — never a placeholder."""
    path = write_d2_env(
        tmp_path / ".env",
        PERMISSION_TIER="2",
        STORE_BACKEND="github",
        FORK_REPO=FORK,
        HARNESS_GITHUB_TOKEN="",
    )

    load_config(env_path=path, environ={})

    assert config_module.github_token() == ""


def test_i11_github_token_tracks_the_last_loaded_config(tmp_path, write_d2_env):
    """I-11 / RUN-DECISIONS-D2 §2: a later tier-0 load shuts the door a tier-2 load opened."""
    tier2 = write_d2_env(
        tmp_path / "t2" / ".env",
        PERMISSION_TIER="2",
        STORE_BACKEND="github",
        FORK_REPO=FORK,
        HARNESS_GITHUB_TOKEN=VALID_PAT,
    )
    tier0 = write_d2_env(tmp_path / "t0" / ".env", HARNESS_GITHUB_TOKEN=VALID_PAT)

    load_config(env_path=tier2, environ={})
    assert config_module.github_token() == VALID_PAT

    load_config(env_path=tier0, environ={})
    assert config_module.github_token() == ""


def test_i11_the_token_is_still_not_stored_on_config_at_tier_2(tmp_path, write_d2_env):
    """I-11 / B3: tier 2 does not put the token value onto the frozen Config."""
    path = write_d2_env(
        tmp_path / ".env",
        PERMISSION_TIER="2",
        STORE_BACKEND="github",
        FORK_REPO=FORK,
        HARNESS_GITHUB_TOKEN=VALID_PAT,
    )

    config = load_config(env_path=path, environ={})

    assert VALID_PAT not in repr(config)
    assert "harness_github_token" not in {f.name for f in dataclasses.fields(config)}


def test_i11_token_key_name_is_the_env_key(tmp_path, write_d2_env):
    """RUN-DECISIONS-D2 §2: TOKEN_KEY_NAME is the constant identity.py uses instead of the literal."""
    assert config_module.TOKEN_KEY_NAME == "HARNESS_GITHUB_TOKEN"
    assert config_module.TOKEN_KEY_NAME in config_module.SECRET_KEYS


# --------------------------------------------------------------------------
# A7 / R4.1 — the shipped .env.example is a loadable configuration
# --------------------------------------------------------------------------


def test_a7_the_shipped_env_example_loads_without_error(tmp_path):
    """A7 / D2-R4.1: `harness init` copies .env.example to .env; every key it ships must be known
    to load_config (including the subscription token key CLAUDE_CODE_OAUTH_TOKEN, a secret key
    that is accepted, never stored on Config, and scrubbed by redact)."""
    example = Path(__file__).resolve().parent.parent / ".env.example"
    target = tmp_path / ".env"
    target.write_text(example.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")

    config = load_config(env_path=target, environ={})

    assert config.permission_tier == 0
    assert config.store_backend == "sqlite"
    assert "CLAUDE_CODE_OAUTH_TOKEN" in config_module.KNOWN_KEYS
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in config_module.SECRET_KEYS  # D1 B49 pins two
    assert not any(name.lower().endswith(("_token", "_key")) for name in config.__dataclass_fields__)


# --------------------------------------------------------------------------
# Delivery 3 — RUN-DECISIONS-D3 "Config": the five usage-governance keys, their ranges, the
# .harness/config.json knob set, and the pure in_run_window helper.
# Appended by the D3 spec-tester (T1); additions only.
# --------------------------------------------------------------------------

from datetime import datetime, timezone

# RUN-DECISIONS-D3 "Config" — the new keys with their .env.example values (inline, on purpose).
D3_ENV: dict[str, str] = {
    "WEEKLY_USAGE_STOP_PCT": "90",
    "SESSION_USAGE_STOP_PCT": "70",
    "OVERRUN_PCT": "10",
    "RUN_WINDOW_START": "mon 08:00",
    "RUN_WINDOW_END": "tue 20:00",
}
# Every one of the five is required in .env; the window pair may be empty, but only together.
D3_REQUIRED_KEYS = tuple(D3_ENV)
# Appended to Config in this order, after the Delivery 2 fields.
D3_NEW_FIELDS_IN_ORDER = (
    "weekly_usage_stop_pct",
    "session_usage_stop_pct",
    "overrun_pct",
    "run_window_start",
    "run_window_end",
)
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@pytest.fixture
def write_d3_env(write_env):
    """conftest's write_env plus the Delivery 2 and Delivery 3 keys; overrides win, None removes."""

    def _write(path: Path, **overrides: object) -> Path:
        values: dict[str, object] = {**D2_ENV, **D3_ENV}
        values.update(overrides)
        return write_env(path, **values)

    return _write


def utc(day: int, hour: int, minute: int = 0) -> datetime:
    """A UTC instant in September 2026: the 7th is a Monday, the 5th a Saturday."""
    return datetime(2026, 9, day, hour, minute, tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# The five keys are read, required, and land on the Config in the frozen order
# --------------------------------------------------------------------------


def test_d3_every_new_key_lands_on_the_matching_config_field(tmp_path, write_d3_env):
    """RUN-DECISIONS-D3 "Config": the .env.example values map onto the five new fields —
    percentages as floats, the window as the raw "day HH:MM" strings."""
    path = write_d3_env(tmp_path / ".env")

    config = load_config(env_path=path, environ={})

    assert config.weekly_usage_stop_pct == pytest.approx(90.0)
    assert config.session_usage_stop_pct == pytest.approx(70.0)
    assert config.overrun_pct == pytest.approx(10.0)
    assert config.run_window_start == "mon 08:00"
    assert config.run_window_end == "tue 20:00"


def test_d3_the_new_fields_are_appended_last_in_the_frozen_order(tmp_path, write_d3_env):
    """RUN-DECISIONS-D3 "Config": Config fields appended in this order —
    weekly_usage_stop_pct, session_usage_stop_pct, overrun_pct, run_window_start,
    run_window_end."""
    path = write_d3_env(tmp_path / ".env")
    config = load_config(env_path=path, environ={})

    names = [f.name for f in dataclasses.fields(config)]

    # Each delivery appends its own block: B225 added model and effort after the D3 five, and
    # Delivery 4 added seven more after those. What D3 pins is that its five come together and
    # in order, after github_token_shape_ok.
    start = names.index(D3_NEW_FIELDS_IN_ORDER[0])
    assert tuple(names[start : start + 5]) == D3_NEW_FIELDS_IN_ORDER
    assert tuple(names[start + 5 : start + 7]) == ("model", "effort")
    assert names.index("weekly_usage_stop_pct") > names.index("github_token_shape_ok")


def test_d3_the_new_fields_are_frozen_too(tmp_path, write_d3_env):
    """B6 (unchanged): Config stays frozen; the usage knobs cannot be reassigned at runtime."""
    path = write_d3_env(tmp_path / ".env")
    config = load_config(env_path=path, environ={})

    with pytest.raises(dataclasses.FrozenInstanceError):
        config.weekly_usage_stop_pct = 10.0
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.run_window_start = "sun 00:00"


@pytest.mark.parametrize("missing", D3_REQUIRED_KEYS)
def test_d3_a_missing_new_key_raises_config_error_naming_it(tmp_path, write_d3_env, missing):
    """RUN-DECISIONS-D3 "Config": all five are required in .env — no defaults in code, and the
    error names the key that is missing."""
    path = write_d3_env(tmp_path / ".env", **{missing: None})

    with pytest.raises(ConfigError) as excinfo:
        load_config(env_path=path, environ={})

    assert missing in str(excinfo.value)


def test_d3_environ_overrides_the_env_file_for_a_new_key(tmp_path, write_d3_env):
    """B1 (D1 rule, unchanged) applied to the D3 keys: environ overrides .env key for key."""
    path = write_d3_env(tmp_path / ".env")

    config = load_config(
        env_path=path, environ={"WEEKLY_USAGE_STOP_PCT": "80", "RUN_WINDOW_END": "wed 06:00"}
    )

    assert config.weekly_usage_stop_pct == pytest.approx(80.0)
    assert config.run_window_end == "wed 06:00"


# --------------------------------------------------------------------------
# Ranges: 0 < stop <= 100, 0 <= OVERRUN_PCT < WEEKLY_USAGE_STOP_PCT
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("WEEKLY_USAGE_STOP_PCT", "0"),
        ("WEEKLY_USAGE_STOP_PCT", "0.0"),
        ("WEEKLY_USAGE_STOP_PCT", "-1"),
        ("WEEKLY_USAGE_STOP_PCT", "100.0001"),
        ("WEEKLY_USAGE_STOP_PCT", "101"),
        ("WEEKLY_USAGE_STOP_PCT", "ninety"),
        ("WEEKLY_USAGE_STOP_PCT", ""),
        ("SESSION_USAGE_STOP_PCT", "0"),
        ("SESSION_USAGE_STOP_PCT", "-0.5"),
        ("SESSION_USAGE_STOP_PCT", "100.0001"),
        ("SESSION_USAGE_STOP_PCT", "seventy"),
        ("SESSION_USAGE_STOP_PCT", ""),
        ("OVERRUN_PCT", "-0.0001"),
        ("OVERRUN_PCT", "-10"),
        ("OVERRUN_PCT", "ten"),
        ("OVERRUN_PCT", ""),
    ],
)
def test_d3_an_out_of_range_or_malformed_usage_key_raises_config_error_naming_it(
    tmp_path, write_d3_env, key, value
):
    """RUN-DECISIONS-D3 "Config": the stop percentages are 0 < x <= 100 and the leeway is
    0 <= x < the weekly stop; anything else is a startup error naming the key, never a silently
    different threshold."""
    path = write_d3_env(tmp_path / ".env", **{key: value})

    with pytest.raises(ConfigError) as excinfo:
        load_config(env_path=path, environ={})

    assert key in str(excinfo.value)


def test_d3_the_usage_boundary_values_are_accepted(tmp_path, write_d3_env):
    """RUN-DECISIONS-D3 "Config": the inclusive ends load — 100 % stops and a zero leeway."""
    path = write_d3_env(
        tmp_path / ".env",
        WEEKLY_USAGE_STOP_PCT="100",
        SESSION_USAGE_STOP_PCT="100",
        OVERRUN_PCT="0",
    )

    config = load_config(env_path=path, environ={})

    assert config.weekly_usage_stop_pct == pytest.approx(100.0)
    assert config.session_usage_stop_pct == pytest.approx(100.0)
    assert config.overrun_pct == pytest.approx(0.0)


def test_d3_the_smallest_admissible_stops_are_accepted(tmp_path, write_d3_env):
    """RUN-DECISIONS-D3 "Config": anything strictly above 0 is a legal stop."""
    path = write_d3_env(
        tmp_path / ".env",
        WEEKLY_USAGE_STOP_PCT="0.5",
        SESSION_USAGE_STOP_PCT="0.0001",
        OVERRUN_PCT="0.25",
    )

    config = load_config(env_path=path, environ={})

    assert config.weekly_usage_stop_pct == pytest.approx(0.5)
    assert config.session_usage_stop_pct == pytest.approx(0.0001)
    assert config.overrun_pct == pytest.approx(0.25)


@pytest.mark.parametrize(
    ("weekly", "overrun"),
    [("90", "90"), ("90", "95"), ("90", "100"), ("20", "25"), ("10", "10")],
)
def test_d3_overrun_at_or_above_the_weekly_stop_is_rejected(
    tmp_path, write_d3_env, weekly, overrun
):
    """RUN-DECISIONS-D3 "Config": OVERRUN_PCT is 0 <= x < WEEKLY_USAGE_STOP_PCT — a leeway that
    reaches the weekly stop would let a carried item run past the very line the stop draws."""
    path = write_d3_env(
        tmp_path / ".env", WEEKLY_USAGE_STOP_PCT=weekly, OVERRUN_PCT=overrun
    )

    with pytest.raises(ConfigError) as excinfo:
        load_config(env_path=path, environ={})

    assert "OVERRUN_PCT" in str(excinfo.value)


def test_d3_an_overrun_just_below_the_weekly_stop_is_accepted(tmp_path, write_d3_env):
    """RUN-DECISIONS-D3 "Config": the bound is strict on one side only — 89.9999 < 90 loads."""
    path = write_d3_env(
        tmp_path / ".env", WEEKLY_USAGE_STOP_PCT="90", OVERRUN_PCT="89.9999"
    )

    config = load_config(env_path=path, environ={})

    assert config.overrun_pct == pytest.approx(89.9999)


def test_d3_a_lowered_weekly_stop_narrows_the_leeway_range(tmp_path, write_d3_env):
    """RUN-DECISIONS-D3 "Config": the leeway is checked against the configured weekly stop, not
    against the shipped 90 — with the stop at 20 a leeway of 15 loads and 25 does not."""
    ok = write_d3_env(tmp_path / "ok" / ".env", WEEKLY_USAGE_STOP_PCT="20", OVERRUN_PCT="15")
    assert load_config(env_path=ok, environ={}).overrun_pct == pytest.approx(15.0)

    bad = write_d3_env(tmp_path / "bad" / ".env", WEEKLY_USAGE_STOP_PCT="20", OVERRUN_PCT="25")
    with pytest.raises(ConfigError):
        load_config(env_path=bad, environ={})


# --------------------------------------------------------------------------
# The run window: "^(mon|tue|wed|thu|fri|sat|sun) ([01]\\d|2[0-3]):[0-5]\\d$"
# --------------------------------------------------------------------------


@pytest.mark.parametrize("day", WEEKDAYS)
def test_d3_every_weekday_name_is_accepted_in_the_window(tmp_path, write_d3_env, day):
    """RUN-DECISIONS-D3 "Config": the seven lower-case three-letter names are the vocabulary."""
    path = write_d3_env(tmp_path / ".env", RUN_WINDOW_START=f"{day} 08:00",
                        RUN_WINDOW_END="tue 20:00")

    config = load_config(env_path=path, environ={})

    assert config.run_window_start == f"{day} 08:00"


@pytest.mark.parametrize("value", ["mon 00:00", "sun 23:59", "sat 22:00", "wed 09:05",
                                   "fri 19:30", "thu 23:00"])
def test_d3_a_well_formed_window_time_is_accepted(tmp_path, write_d3_env, value):
    """RUN-DECISIONS-D3 "Config": hours are 00-23 and minutes 00-59, both zero-padded."""
    path = write_d3_env(tmp_path / ".env", RUN_WINDOW_START=value)

    config = load_config(env_path=path, environ={})

    assert config.run_window_start == value


@pytest.mark.parametrize(
    "value",
    ["Mon 08:00", "MON 08:00", "monday 08:00", "mon 8:00", "mon 24:00", "mon 08:60",
     "mon 08:0", "mon08:00", "mon 08.00", "08:00 mon", "mon", "mon 08:00:00",
     "mon 08:00 UTC", "tomorrow 08:00"],
)
def test_d3_a_malformed_run_window_raises_config_error_naming_the_key(
    tmp_path, write_d3_env, value
):
    """RUN-DECISIONS-D3 "Config": the window is matched against the frozen regex; anything else
    is a startup error naming the key, never a window the operator did not mean."""
    path = write_d3_env(tmp_path / ".env", RUN_WINDOW_START=value)

    with pytest.raises(ConfigError) as excinfo:
        load_config(env_path=path, environ={})

    assert "RUN_WINDOW_START" in str(excinfo.value)


def test_d3_a_malformed_window_end_is_named_too(tmp_path, write_d3_env):
    """RUN-DECISIONS-D3 "Config": both ends are validated, and the error names the one at fault."""
    path = write_d3_env(tmp_path / ".env", RUN_WINDOW_END="tue 25:00")

    with pytest.raises(ConfigError) as excinfo:
        load_config(env_path=path, environ={})

    assert "RUN_WINDOW_END" in str(excinfo.value)


def test_d3_both_window_ends_empty_is_accepted(tmp_path, write_d3_env):
    """RUN-DECISIONS-D3 "Config": both empty = always open — the only way to switch the window
    off, and the empty strings reach the Config unchanged."""
    path = write_d3_env(tmp_path / ".env", RUN_WINDOW_START="", RUN_WINDOW_END="")

    config = load_config(env_path=path, environ={})

    assert config.run_window_start == ""
    assert config.run_window_end == ""


@pytest.mark.parametrize("empty_key", ["RUN_WINDOW_START", "RUN_WINDOW_END"])
def test_d3_only_one_empty_window_end_is_a_config_error(tmp_path, write_d3_env, empty_key):
    """RUN-DECISIONS-D3 "Config": the exemption is for the pair — half a window is not a window,
    and the error names the empty key."""
    path = write_d3_env(tmp_path / ".env", **{empty_key: ""})

    with pytest.raises(ConfigError) as excinfo:
        load_config(env_path=path, environ={})

    assert empty_key in str(excinfo.value)


# --------------------------------------------------------------------------
# B112 - the five join the .harness/config.json knob set
# --------------------------------------------------------------------------


def test_b112_config_json_may_set_the_five_usage_knobs(tmp_path, write_d3_env):
    """B112 / RUN-DECISIONS-D3 "Config": add the five to the allowed set — config.json overrides
    .env for each of them."""
    path = write_d3_env(tmp_path / ".env")
    write_config_json(
        tmp_path,
        {
            "WEEKLY_USAGE_STOP_PCT": 80,
            "SESSION_USAGE_STOP_PCT": 55,
            "OVERRUN_PCT": 5,
            "RUN_WINDOW_START": "sat 22:00",
            "RUN_WINDOW_END": "mon 08:00",
        },
    )

    config = load_config(env_path=path, environ={})

    assert config.weekly_usage_stop_pct == pytest.approx(80.0)
    assert config.session_usage_stop_pct == pytest.approx(55.0)
    assert config.overrun_pct == pytest.approx(5.0)
    assert config.run_window_start == "sat 22:00"
    assert config.run_window_end == "mon 08:00"


def test_b112_an_out_of_range_usage_knob_in_config_json_raises_config_error_naming_it(
    tmp_path, write_d3_env
):
    """B112 / RUN-DECISIONS-D3: the same range rules apply to a config.json override."""
    path = write_d3_env(tmp_path / ".env")
    write_config_json(tmp_path, {"SESSION_USAGE_STOP_PCT": 0})

    with pytest.raises(ConfigError) as excinfo:
        load_config(env_path=path, environ={})

    assert "SESSION_USAGE_STOP_PCT" in str(excinfo.value)


def test_b112_a_malformed_window_in_config_json_raises_config_error_naming_it(
    tmp_path, write_d3_env
):
    """B112 / RUN-DECISIONS-D3: the window regex applies to config.json values too."""
    path = write_d3_env(tmp_path / ".env")
    write_config_json(tmp_path, {"RUN_WINDOW_START": "Monday 8am"})

    with pytest.raises(ConfigError) as excinfo:
        load_config(env_path=path, environ={})

    assert "RUN_WINDOW_START" in str(excinfo.value)


def test_b112_the_leeway_is_checked_against_the_merged_weekly_stop(tmp_path, write_d3_env):
    """B112 / RUN-DECISIONS-D3: a knob override cannot sidestep the OVERRUN_PCT rule — lowering
    the weekly stop in config.json below the .env leeway is a startup error."""
    path = write_d3_env(tmp_path / ".env", WEEKLY_USAGE_STOP_PCT="90", OVERRUN_PCT="10")
    write_config_json(tmp_path, {"WEEKLY_USAGE_STOP_PCT": 5})

    with pytest.raises(ConfigError) as excinfo:
        load_config(env_path=path, environ={})

    assert "OVERRUN_PCT" in str(excinfo.value)


# --------------------------------------------------------------------------
# in_run_window - pure, UTC, wrap-aware, empty window always True
# --------------------------------------------------------------------------


@pytest.fixture
def window_config(tmp_path, write_d3_env):
    """A factory for a Config with a given window (both ends, verbatim)."""

    def _make(start: str, end: str, name: str = "w"):
        path = write_d3_env(tmp_path / name / ".env", RUN_WINDOW_START=start,
                            RUN_WINDOW_END=end)
        return load_config(env_path=path, environ={})

    return _make


def test_d3_in_run_window_is_true_inside_the_configured_window(window_config):
    """RUN-DECISIONS-D3 "Config": in_run_window(config, now) — UTC weekday and time. The window
    mon 08:00 → tue 20:00 is open at its start, through Monday, and on Tuesday morning."""
    from harness.config import in_run_window

    config = window_config("mon 08:00", "tue 20:00")

    assert in_run_window(config, utc(7, 8, 0)) is True      # Monday 08:00, the start
    assert in_run_window(config, utc(7, 8, 1)) is True      # Monday 08:01
    assert in_run_window(config, utc(7, 23, 59)) is True    # Monday night
    assert in_run_window(config, utc(8, 0, 0)) is True      # Tuesday midnight
    assert in_run_window(config, utc(8, 19, 59)) is True    # Tuesday 19:59


def test_d3_in_run_window_is_false_outside_the_configured_window(window_config):
    """RUN-DECISIONS-D3 "Config": before the start, after the end, and every day in between."""
    from harness.config import in_run_window

    config = window_config("mon 08:00", "tue 20:00")

    assert in_run_window(config, utc(7, 7, 59)) is False    # Monday 07:59
    assert in_run_window(config, utc(8, 20, 1)) is False    # Tuesday 20:01
    assert in_run_window(config, utc(9, 12, 0)) is False     # Wednesday noon
    assert in_run_window(config, utc(5, 12, 0)) is False     # Saturday noon
    assert in_run_window(config, utc(6, 12, 0)) is False     # Sunday noon


def test_d3_in_run_window_wraps_past_sunday(window_config):
    """RUN-DECISIONS-D3 "Config": the window may wrap past Sunday — sat 22:00 → mon 08:00 is
    open on Saturday night, all Sunday, and Monday before 08:00."""
    from harness.config import in_run_window

    config = window_config("sat 22:00", "mon 08:00", name="wrap")

    assert in_run_window(config, utc(5, 22, 0)) is True      # Saturday 22:00, the start
    assert in_run_window(config, utc(5, 23, 30)) is True     # Saturday night
    assert in_run_window(config, utc(6, 0, 0)) is True       # Sunday midnight
    assert in_run_window(config, utc(6, 12, 0)) is True      # Sunday noon
    assert in_run_window(config, utc(7, 7, 59)) is True      # Monday 07:59
    assert in_run_window(config, utc(5, 21, 59)) is False    # Saturday 21:59
    assert in_run_window(config, utc(7, 9, 0)) is False      # Monday 09:00
    assert in_run_window(config, utc(9, 12, 0)) is False     # Wednesday noon


def test_d3_in_run_window_is_always_true_for_an_empty_window(window_config):
    """RUN-DECISIONS-D3 "Config": both empty → True, every hour of every day."""
    from harness.config import in_run_window

    config = window_config("", "", name="always")

    for day in range(1, 8):
        for hour in (0, 6, 12, 18, 23):
            assert in_run_window(config, utc(day, hour)) is True


def test_d3_in_run_window_is_pure(window_config):
    """RUN-DECISIONS-D3 "Config": pure — the same config and instant answer the same, twice, and
    nothing about the config changes."""
    from harness.config import in_run_window

    config = window_config("mon 08:00", "tue 20:00")
    before = repr(config)

    first = in_run_window(config, utc(7, 9))
    second = in_run_window(config, utc(7, 9))

    assert first is second is True
    assert repr(config) == before


# --------------------------------------------------------------------------
# A7 - the shipped .env.example carries the five keys and the raised USD cap
# --------------------------------------------------------------------------


def test_d3_the_shipped_env_example_carries_the_usage_keys(tmp_path):
    """RUN-DECISIONS-D3 "Config": .env.example ships the five keys with the documented values,
    and WEEKLY_CAP_USD rises to 400.00 so the USD backstop cannot bind before the usage stop."""
    example = Path(__file__).resolve().parent.parent / ".env.example"
    target = tmp_path / ".env"
    target.write_text(example.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")

    config = load_config(env_path=target, environ={})

    assert config.weekly_usage_stop_pct == pytest.approx(90.0)
    assert config.session_usage_stop_pct == pytest.approx(70.0)
    assert config.overrun_pct == pytest.approx(10.0)
    assert config.run_window_start == "mon 08:00"
    assert config.run_window_end == "tue 20:00"
    assert config.weekly_cap_usd == pytest.approx(400.0)


# --------------------------------------------------------------------------------------
# B225 - MODEL and EFFORT are required .env keys, like every other knob
# --------------------------------------------------------------------------------------


def test_b225_model_and_effort_are_read_from_the_env(tmp_path, write_d3_env):
    """B225: pinned in `.env` rather than left to the CLI's default, so a change to that
    default cannot silently change what a work package or a diff is worth."""
    path = write_d3_env(tmp_path / ".env", MODEL="claude-opus-5", EFFORT="max")
    config = load_config(env_path=path, environ={})

    assert config.model == "claude-opus-5"
    assert config.effort == "max"


@pytest.mark.parametrize("bad", ["ultra", "", "1", "none", "very high"])
def test_b225_an_effort_outside_the_documented_levels_is_a_config_error(
    tmp_path, write_d3_env, bad
):
    """B225: the levels are the CLI's own; anything else is a startup error, not a silently
    different amount of thinking. (Case and surrounding space are normalised, not rejected --
    see the next test.)"""
    path = write_d3_env(tmp_path / ".env", EFFORT=bad)

    with pytest.raises(ConfigError) as excinfo:
        load_config(env_path=path, environ={})

    assert "EFFORT" in str(excinfo.value)


def test_b225_effort_is_case_and_space_insensitive(tmp_path, write_d3_env):
    """B225: `XHIGH ` in a hand-edited .env is the level the operator meant."""
    path = write_d3_env(tmp_path / ".env", EFFORT=" XHigh ")

    assert load_config(env_path=path, environ={}).effort == "xhigh"


def test_b225_an_empty_model_is_a_config_error(tmp_path, write_d3_env):
    """B225: an empty MODEL would hand the choice back to the CLI without saying so."""
    path = write_d3_env(tmp_path / ".env", MODEL="   ")

    with pytest.raises(ConfigError) as excinfo:
        load_config(env_path=path, environ={})

    assert "MODEL" in str(excinfo.value)


def test_b225_both_keys_are_in_the_example_file():
    """B225: `.env.example` is the only place defaults live (no defaults in code)."""
    import re

    text = (Path(__file__).resolve().parent.parent / ".env.example").read_text(encoding="utf-8")

    assert re.search(r"^MODEL=\S+", text, re.MULTILINE)
    assert re.search(r"^EFFORT=(low|medium|high|xhigh|max)\s*$", text, re.MULTILINE)


# --------------------------------------------------------------------------------------
# B279 - I-18: the harness never works on its own repository (D61)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["UPSTREAM_REPO", "REPO"])
def test_b279_pointing_the_harness_at_itself_is_refused(tmp_path, write_d3_env, key):
    """B279/I-18: a system that can rewrite the rules it is governed by has no rules. A change
    to gh.py or prompts/implement.md could propose its way out of the kill switch, the
    credential door and the pin, and the reviewer's only defence would be noticing."""
    path = write_d3_env(tmp_path / ".env", SELF_REPO="me/harness", **{key: "me/harness"})

    with pytest.raises(ConfigError) as excinfo:
        load_config(env_path=path, environ={})

    message = str(excinfo.value)
    assert key in message
    assert "I-18" in message


def test_b279_a_different_product_repository_is_fine(tmp_path, write_d3_env):
    """B279: the check is equality with SELF_REPO, not a ban on configuring a product repo."""
    path = write_d3_env(
        tmp_path / ".env",
        SELF_REPO="me/harness",
        REPO="them/product",
        UPSTREAM_REPO="them/product",
    )

    config = load_config(env_path=path, environ={})

    assert config.self_repo == "me/harness"
    assert config.upstream_repo == "them/product"


def test_b279_the_comparison_ignores_case(tmp_path, write_d3_env):
    """B279: GitHub repository names are case-insensitive; the refusal must be too."""
    path = write_d3_env(tmp_path / ".env", SELF_REPO="Me/Harness", UPSTREAM_REPO="me/harness")

    with pytest.raises(ConfigError):
        load_config(env_path=path, environ={})
