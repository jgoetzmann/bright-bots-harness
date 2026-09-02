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
    assert tuple(after) == D2_NEW_FIELDS_IN_ORDER


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


def test_b112_config_json_may_set_every_one_of_the_eleven_knobs(tmp_path, write_d2_env):
    """B112 / RUN-DECISIONS-D2 §2: all eleven keys are accepted together and each takes effect."""
    path = write_d2_env(tmp_path / ".env")
    write_config_json(
        tmp_path,
        {
            "WEEKLY_CAP_USD": 30.0,
            "PER_CALL_CAP_USD": 2.5,
            "RESERVE_PCT": 15,
            "MAX_CONCURRENT_ITEMS": 1,
            "MAX_REVISE_CYCLES": 1,
            "NOTIFY_POLL_HOURS": 6,
            "MAX_SUBISSUES": 5,
            "TRACKING_ISSUE": 42,
            "FORK_REPO": FORK,
            "UPSTREAM_REPO": "Bright-Bots-Initiative/brightboost",
            "TRUST_FILE": "trust/list.txt",
        },
    )

    config = load_config(env_path=path, environ={})

    assert config.weekly_cap_usd == pytest.approx(30.0)
    assert config.per_call_cap_usd == pytest.approx(2.5)
    assert config.reserve_pct == pytest.approx(15.0)
    assert config.max_concurrent_items == 1
    assert config.max_revise_cycles == 1
    assert config.notify_poll_hours == 6
    assert config.max_subissues == 5
    assert config.tracking_issue == 42
    assert config.fork_repo == FORK
    assert Path(config.trust_file).resolve() == (tmp_path / "trust" / "list.txt").resolve()


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
