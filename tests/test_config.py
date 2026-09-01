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


@pytest.mark.parametrize("tier", ["1", "2", "3"])
def test_b4_a_non_zero_permission_tier_is_rejected(tmp_path, write_env, tier):
    """B4: Delivery 1 is Tier 0; any other tier is a startup error."""
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
