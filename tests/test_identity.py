"""B79-B86: harness.identity (HARNESS-SPEC 5.12, 13).

Delivery 1 is Tier 0: nothing here authenticates, and no test prints a token value.
"""

from __future__ import annotations

import pytest

from harness.config import load_config
from harness.errors import GitHubError, TierViolation, WriteOutsideAllowedRoots
from harness.identity import Identity, write_human_doc
from harness.redact import allowed_roots, redact, set_write_roots

HANDLE = "brightboost-harness"
REPO = "Bright-Bots-Initiative/brightboost"
VALID_PAT = "github_pat_" + "D" * 40
VALID_GHP = "ghp_" + "B" * 36

SECTIONS = (
    "## Current state",
    "## What you need to do",
    "## Tokens",
    "## What needs Nathaniel",
    "## What the harness will never ask for",
    "## Verification",
)

ENV = {
    "BACKEND": "fake",
    "REPO": REPO,
    "PERMISSION_TIER": "0",
    "ALLOWLIST_LABEL": "harness-ok",
    "WEEKLY_BUDGET_PCT": "40",
    "SESSION_BUDGET_PCT": "15",
    "RESERVE_PCT": "10",
    "WEEKLY_RESET_DAY": "monday",
    "MAX_CONCURRENT_CLONES": "1",
    "MAX_TURNS_DISCOVER": "10",
    "MAX_TURNS_PROPOSE": "30",
    "MAX_TURNS_IMPLEMENT": "80",
    "MAX_TURNS_PACKAGE": "10",
    "MAX_RETRIES_GATES": "2",
    "GITHUB_API_CEILING_PER_HOUR": "50",
    "MIN_FREE_DISK_GB": "5",
    "DB_PATH": "harness.db",
    "RUNS_DIR": "runs",
    "PACKAGES_DIR": "packages",
    "HALT_FILE": "HALT",
    "FULLSEND_ENABLED": "false",
    "WEEKLY_CAP_USD": "25.00",
    "PER_CALL_CAP_USD": "3.00",
    "MAX_CONCURRENT_ITEMS": "1",
    "MAX_REVISE_CYCLES": "3",
    "FORK_REPO": "",
    "UPSTREAM_REPO": REPO,
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
    "HARNESS_GITHUB_TOKEN": "",
    "ANTHROPIC_API_KEY": "",
}


def write_env(directory, *, drop=(), **overrides):
    values = dict(ENV)
    values.update(overrides)
    for key in drop:
        values.pop(key, None)
    path = directory / ".env"
    body = "".join(f"{key}={value}\n" for key, value in values.items())
    path.write_text(body, encoding="utf-8", newline="\n")
    return path


def make_config(directory, *, drop=(), **overrides):
    return load_config(env_path=write_env(directory, drop=drop, **overrides), environ={})


class FakeGh:
    """Records the paths it is asked for; returns a canned body or raises."""

    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def get(self, path):
        self.calls.append(path)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def account_found():
    return FakeGh({"login": HANDLE, "id": 424242})


def account_missing():
    return FakeGh(GitHubError("404 Not Found"))


def section_body(doc, heading):
    lines = []
    inside = False
    for line in doc.splitlines():
        if line.startswith("## "):
            inside = line.strip() == heading
            continue
        if inside:
            lines.append(line)
    return "\n".join(lines)


def prerequisite(readiness, prerequisite_id):
    for item in readiness.prerequisites:
        if item.id == prerequisite_id:
            return item
    raise AssertionError(f"no prerequisite with id {prerequisite_id!r}")


@pytest.fixture(autouse=True)
def restore_write_roots():
    saved = allowed_roots()
    set_write_roots([])
    yield
    set_write_roots(list(saved))


# --- B79: token presence ----------------------------------------------------------------------


def test_B79_token_present_is_false_when_the_env_value_is_empty(tmp_path):
    config = make_config(tmp_path, HARNESS_GITHUB_TOKEN="")
    identity = Identity(config, account_found())

    assert identity.token_present() is False
    assert config.github_token_present is False


def test_B79_token_present_is_false_when_the_key_is_absent_from_env(tmp_path):
    config = make_config(tmp_path, drop=("HARNESS_GITHUB_TOKEN",))
    identity = Identity(config, account_found())

    assert identity.token_present() is False


def test_B79_token_present_is_true_when_a_token_is_set(tmp_path):
    config = make_config(tmp_path, HARNESS_GITHUB_TOKEN=VALID_PAT)
    identity = Identity(config, account_found())

    assert identity.token_present() is True
    assert config.github_token_present is True
    assert config.github_token_shape_ok is True


def test_B79_a_malformed_token_is_present_but_its_shape_is_not_ok(tmp_path):
    config = make_config(tmp_path, HARNESS_GITHUB_TOKEN="ghp_short")

    assert config.github_token_present is True
    assert config.github_token_shape_ok is False


# --- B80: the token is refused at Tier 0 ------------------------------------------------------


def test_B80_load_token_raises_TierViolation_at_tier_0_with_a_valid_token(tmp_path):
    config = make_config(tmp_path, HARNESS_GITHUB_TOKEN=VALID_PAT)
    identity = Identity(config, account_found())

    with pytest.raises(TierViolation):
        identity.load_token()


def test_B80_load_token_raises_TierViolation_at_tier_0_with_no_token(tmp_path):
    config = make_config(tmp_path)
    identity = Identity(config, account_found())

    with pytest.raises(TierViolation):
        identity.load_token()


def test_B80_load_token_issues_no_request_before_refusing(tmp_path):
    config = make_config(tmp_path, HARNESS_GITHUB_TOKEN=VALID_PAT)
    gh = account_found()
    identity = Identity(config, gh)

    with pytest.raises(TierViolation):
        identity.load_token()

    assert gh.calls == []


# --- B81: shape validation --------------------------------------------------------------------


@pytest.mark.parametrize("token", [VALID_PAT, VALID_GHP, "github_pat_" + "x" * 60])
def test_B81_validate_shape_accepts_the_fine_grained_and_classic_prefixes(tmp_path, token):
    identity = Identity(make_config(tmp_path), account_found())

    assert identity.validate_shape(token) == []


@pytest.mark.parametrize(
    "token",
    [
        "",
        "ghp_short",
        "sk-ant-api03-" + "A" * 40,
        "github_pat_short",
        "a" * 40,
        "ghp_" + "B" * 36 + "!",
        "  " + "ghp_" + "B" * 36,
    ],
)
def test_B81_validate_shape_rejects_anything_else(tmp_path, token):
    identity = Identity(make_config(tmp_path), account_found())

    errors = identity.validate_shape(token)

    assert errors
    assert all(isinstance(error, str) for error in errors)


def test_B81_validate_shape_issues_no_request(tmp_path):
    gh = account_found()
    identity = Identity(make_config(tmp_path), gh)

    identity.validate_shape(VALID_PAT)
    identity.validate_shape("ghp_short")

    assert gh.calls == []


# --- B82: readiness ---------------------------------------------------------------------------


def test_B82_a_failed_account_lookup_leaves_the_account_unsatisfied_and_not_ready(tmp_path):
    gh = account_missing()
    identity = Identity(make_config(tmp_path), gh)

    readiness = identity.assess(1)

    assert prerequisite(readiness, "account").satisfied is False
    assert readiness.ready is False
    assert any(HANDLE in call for call in gh.calls)


def test_B82_account_exists_is_false_when_the_lookup_raises_GitHubError(tmp_path):
    identity = Identity(make_config(tmp_path), account_missing())

    assert Identity.handle == HANDLE
    assert identity.account_exists() is False


def test_B82_assess_for_tier_1_lists_the_tier_1_prerequisites_in_order(tmp_path):
    identity = Identity(make_config(tmp_path), account_found())

    readiness = identity.assess(1)

    assert tuple(item.id for item in readiness.prerequisites) == (
        "account",
        "email-2fa",
        "token",
        "org-approval",
        "tier",
    )
    assert readiness.current_tier == 0
    assert readiness.target_tier == 1


def test_B82_assess_for_tier_2_adds_the_collaborator_prerequisite(tmp_path):
    identity = Identity(make_config(tmp_path), account_found())

    readiness = identity.assess(2)

    assert tuple(item.id for item in readiness.prerequisites)[-1] == "collaborator"
    assert prerequisite(readiness, "collaborator").satisfied is False
    assert readiness.ready is False


def test_B82_the_tier_prerequisite_is_unsatisfied_while_permission_tier_is_below_target(tmp_path):
    identity = Identity(make_config(tmp_path), account_found())

    readiness = identity.assess(1)

    assert prerequisite(readiness, "tier").satisfied is False
    assert prerequisite(readiness, "tier").actor == "nathaniel"
    assert readiness.ready is False


def test_B82_the_generated_doc_has_exactly_the_six_required_headings_in_order(tmp_path):
    identity = Identity(make_config(tmp_path), account_found())

    doc = identity.render_human_doc(identity.assess(1))

    assert [line.strip() for line in doc.splitlines() if line.startswith("## ")] == list(SECTIONS)


# --- B84: satisfied prerequisites are not repeated as actions ---------------------------------


def test_B84_a_satisfied_prerequisite_is_reported_as_done_not_as_an_outstanding_action(tmp_path):
    identity = Identity(make_config(tmp_path), account_found())
    readiness = identity.assess(1)
    account = prerequisite(readiness, "account")
    assert account.satisfied is True

    doc = identity.render_human_doc(readiness)

    current = section_body(doc, "## Current state")
    todo = section_body(doc, "## What you need to do")
    assert account.title in current or account.id in current
    assert account.title not in todo


def test_B84_an_outstanding_prerequisite_is_listed_under_what_you_need_to_do(tmp_path):
    identity = Identity(make_config(tmp_path), account_found())
    readiness = identity.assess(1)
    outstanding = prerequisite(readiness, "email-2fa")
    assert outstanding.satisfied is False

    doc = identity.render_human_doc(readiness)

    assert outstanding.title in section_body(doc, "## What you need to do")


# --- B85: the token table -----------------------------------------------------------------


def test_B85_the_tokens_section_names_the_tier_1_permission_set(tmp_path):
    identity = Identity(make_config(tmp_path), account_found())

    doc = identity.render_human_doc(identity.assess(1))

    tokens = section_body(doc, "## Tokens")
    assert "Metadata" in tokens
    assert "Issues" in tokens
    assert "Pull requests" in tokens
    contents = [line for line in tokens.splitlines() if "Contents" in line]
    workflows = [line for line in tokens.splitlines() if "Workflows" in line]
    assert contents and any("none" in line.lower() for line in contents)
    assert workflows and any("none" in line.lower() for line in workflows)


def test_B85_the_tokens_section_names_the_env_key_that_carries_the_token(tmp_path):
    identity = Identity(make_config(tmp_path), account_found())

    doc = identity.render_human_doc(identity.assess(1))

    assert "HARNESS_GITHUB_TOKEN" in section_body(doc, "## Tokens")


def test_B85_workflows_stays_none_at_tier_2_while_contents_becomes_readable(tmp_path):
    identity = Identity(make_config(tmp_path), account_found())

    doc = identity.render_human_doc(identity.assess(2))

    tokens = section_body(doc, "## Tokens")
    contents = [line for line in tokens.splitlines() if "Contents" in line]
    workflows = [line for line in tokens.splitlines() if "Workflows" in line]
    assert contents and any("read" in line.lower() for line in contents)
    assert workflows and any("none" in line.lower() for line in workflows)


# --- B83: no secret value ever reaches the document -------------------------------------------


def test_B83_the_document_survives_its_own_redaction_patterns_with_a_token_set(tmp_path):
    config = make_config(tmp_path, HARNESS_GITHUB_TOKEN=VALID_PAT)
    identity = Identity(config, account_found())

    doc = identity.render_human_doc(identity.assess(1))

    assert VALID_PAT not in doc
    assert redact(doc) == doc


def test_B83_the_document_survives_its_own_redaction_patterns_with_no_token(tmp_path):
    identity = Identity(make_config(tmp_path), account_found())

    doc = identity.render_human_doc(identity.assess(2))

    assert redact(doc) == doc


# --- B86: determinism -------------------------------------------------------------------------


def test_B86_two_renders_of_an_equal_readiness_are_byte_identical(tmp_path):
    identity = Identity(make_config(tmp_path), account_found())
    first_readiness = identity.assess(1)
    second_readiness = identity.assess(1)
    assert first_readiness == second_readiness

    first = identity.render_human_doc(first_readiness)
    second = identity.render_human_doc(second_readiness)

    assert first.encode("utf-8") == second.encode("utf-8")


def test_B86_a_fresh_identity_over_the_same_config_renders_the_same_document(tmp_path):
    config = make_config(tmp_path)
    first = Identity(config, account_found())
    second = Identity(config, account_found())

    doc_one = first.render_human_doc(first.assess(1))
    doc_two = second.render_human_doc(second.assess(1))

    assert doc_one == doc_two


# --- I-8: write_human_doc obeys the write guard ------------------------------------------------


def test_I8_write_human_doc_refuses_a_path_outside_the_allowed_roots(tmp_path):
    identity = Identity(make_config(tmp_path), account_found())
    doc = identity.render_human_doc(identity.assess(1))
    set_write_roots([tmp_path / "allowed"])
    outside = tmp_path / "outside" / "HUMAN.md"

    with pytest.raises(WriteOutsideAllowedRoots):
        write_human_doc(outside, doc)

    assert not outside.exists()


def test_I8_write_human_doc_writes_the_document_verbatim_under_an_allowed_root(tmp_path):
    identity = Identity(make_config(tmp_path), account_found())
    doc = identity.render_human_doc(identity.assess(1))
    target = tmp_path / "allowed" / "HUMAN.md"
    set_write_roots([tmp_path / "allowed"])

    write_human_doc(target, doc)

    assert target.read_text(encoding="utf-8") == doc
    assert b"\r\n" not in target.read_bytes()
