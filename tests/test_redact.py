"""B49-B52 and the I-8 write guard: harness.redact (HARNESS-SPEC 5.8, 9 I-8)."""

from __future__ import annotations

import pytest

from harness.errors import WriteOutsideAllowedRoots
from harness.redact import (
    REDACTION,
    allowed_roots,
    guarded_write,
    redact,
    redact_json,
    set_write_roots,
    write_redacted,
)

SK_ANT = "sk-ant-api03-" + "A" * 40
GHP = "ghp_" + "B" * 36
GHO = "gho_" + "C" * 32
GHU = "ghu_" + "E" * 34
GH_PAT = "github_pat_" + "D" * 40
AKIA = "AKIAIOSFODNN7EXAMPLE"
PEM = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIBOgIBAAJBAKtotallyfakekeymaterial\n"
    "-----END RSA PRIVATE KEY-----"
)

VENDOR_SECRETS = [SK_ANT, GHP, GHO, GHU, GH_PAT, AKIA]


@pytest.fixture(autouse=True)
def restore_write_roots():
    """Every test starts with the guard disabled and leaves the process as it found it."""
    saved = allowed_roots()
    set_write_roots([])
    yield
    set_write_roots(list(saved))


# --- B49: every pattern in 5.8 ----------------------------------------------------------------


@pytest.mark.parametrize("secret", VENDOR_SECRETS)
def test_B49_each_vendor_key_pattern_is_replaced_with_the_redaction_marker(secret):
    result = redact(f"before {secret} after")

    assert secret not in result
    assert REDACTION in result
    assert result == f"before {REDACTION} after"


@pytest.mark.parametrize(
    "line,secret",
    [
        ("api_key=abc123XYZdef", "abc123XYZdef"),
        ("API-KEY: zzz999QQQ", "zzz999QQQ"),
        ("apikey = pppp7777", "pppp7777"),
        ("secret: s3cr3tvalue", "s3cr3tvalue"),
        ("token = tk987654321", "tk987654321"),
        ("password: hunter2ABCDEF", "hunter2ABCDEF"),
    ],
)
def test_B49_each_labelled_credential_assignment_is_redacted(line, secret):
    result = redact(line)

    assert secret not in result
    assert REDACTION in result


def test_B49_an_authorization_header_line_is_redacted():
    result = redact("Authorization: Bearer x")

    assert REDACTION in result
    assert "Bearer" not in result
    assert "Authorization:" not in result


def test_B49_a_pem_private_key_block_is_redacted_through_its_footer():
    result = redact(f"leading line\n{PEM}\ntrailing line")

    assert REDACTION in result
    assert "MIIBOgIBAAJBAKtotallyfakekeymaterial" not in result
    assert "BEGIN RSA PRIVATE KEY" not in result
    assert "END RSA PRIVATE KEY" not in result
    assert "leading line" in result
    assert "trailing line" in result


def test_B49_ordinary_prose_is_left_exactly_as_it_was():
    text = "cloned brightboost at 2026-09-01 and ran 7 gates, all green\n"

    assert redact(text) == text


# --- B50: mid-line redaction ------------------------------------------------------------------


def test_B50_a_key_embedded_mid_line_is_redacted_without_losing_the_line():
    line = f"2026-09-01T12:00:00Z WARN leaked {GHP} while cloning the repo"

    result = redact(line)

    assert GHP not in result
    assert REDACTION in result
    assert result.startswith("2026-09-01T12:00:00Z WARN leaked ")
    assert result.endswith(" while cloning the repo")


def test_B50_two_keys_on_one_line_are_both_redacted_and_the_rest_survives():
    line = f"first {GHP} then {SK_ANT} end"

    result = redact(line)

    assert GHP not in result
    assert SK_ANT not in result
    assert result.count(REDACTION) == 2
    assert result == f"first {REDACTION} then {REDACTION} end"


def test_B50_only_the_offending_line_of_a_multi_line_text_changes():
    text = f"line one\nline two {AKIA}\nline three\n"

    result = redact(text).splitlines()

    assert result[0] == "line one"
    assert result[2] == "line three"
    assert AKIA not in result[1]
    assert REDACTION in result[1]


# --- B51: nested structures -------------------------------------------------------------------


def test_B51_redact_json_walks_nested_dicts_and_lists():
    obj = {
        "outer": {"key": GHP, "safe": "plain text"},
        "items": ["plain", SK_ANT, {"deep": AKIA}],
    }

    result = redact_json(obj)

    assert result["outer"]["key"] == REDACTION
    assert result["outer"]["safe"] == "plain text"
    assert result["items"][0] == "plain"
    assert result["items"][1] == REDACTION
    assert result["items"][2]["deep"] == REDACTION


def test_B51_redact_json_leaves_non_string_leaves_untouched():
    obj = {"n": 7, "f": 3.5, "flag": True, "nothing": None, "list": [1, None, False]}

    result = redact_json(obj)

    assert result == obj
    assert result["nothing"] is None
    assert result["flag"] is True
    assert result["list"] == [1, None, False]


def test_B51_redact_json_redacts_a_bare_string_leaf():
    assert redact_json(GH_PAT) == REDACTION
    assert redact_json("nothing secret here") == "nothing secret here"


# --- B52: write_redacted ----------------------------------------------------------------------


def test_B52_write_redacted_writes_no_unredacted_byte(tmp_path):
    target = tmp_path / "runs" / "transcript.jsonl"

    write_redacted(target, f"prefix {GHP} suffix\n")

    data = target.read_bytes()
    assert GHP.encode("utf-8") not in data
    assert REDACTION.encode("utf-8") in data
    assert b"prefix " in data and b" suffix" in data


def test_B52_write_redacted_scrubs_every_pattern_in_one_file(tmp_path):
    target = tmp_path / "runs" / "log.txt"
    body = "\n".join(VENDOR_SECRETS + ["password: hunter2ABCDEF", PEM]) + "\n"

    write_redacted(target, body)

    text = target.read_text(encoding="utf-8")
    for secret in VENDOR_SECRETS:
        assert secret not in text
    assert "hunter2ABCDEF" not in text
    assert "MIIBOgIBAAJBAKtotallyfakekeymaterial" not in text
    assert REDACTION in text


def test_B52_write_redacted_creates_the_parent_directories(tmp_path):
    target = tmp_path / "runs" / "item-1" / "transcript" / "propose.jsonl"

    write_redacted(target, "clean line\n")

    assert target.is_file()
    assert target.read_text(encoding="utf-8") == "clean line\n"


# --- I-8: the write guard ---------------------------------------------------------------------


def test_I8_guarded_write_outside_the_allowed_roots_raises_and_writes_nothing(tmp_path):
    set_write_roots([tmp_path / "runs"])
    outside = tmp_path / "elsewhere.txt"

    with pytest.raises(WriteOutsideAllowedRoots):
        guarded_write(outside, "x")

    assert not outside.exists()


def test_I8_guarded_write_under_an_allowed_root_succeeds_and_creates_parents(tmp_path):
    set_write_roots([tmp_path / "runs"])
    target = tmp_path / "runs" / "item-1" / "gates" / "baseline.json"

    guarded_write(target, "[]")

    assert target.read_text(encoding="utf-8") == "[]"


def test_I8_guarded_write_to_a_path_that_is_itself_a_root_succeeds(tmp_path):
    target = tmp_path / "HUMAN.md"
    set_write_roots([tmp_path / "runs", target])

    guarded_write(target, "# HUMAN.md\n")

    assert target.read_text(encoding="utf-8") == "# HUMAN.md\n"


def test_I8_a_sibling_whose_name_merely_starts_with_a_root_is_refused(tmp_path):
    set_write_roots([tmp_path / "runs"])
    sibling = tmp_path / "runs-elsewhere" / "loot.txt"

    with pytest.raises(WriteOutsideAllowedRoots):
        guarded_write(sibling, "x")

    assert not sibling.exists()


def test_I8_write_redacted_outside_the_allowed_roots_raises_and_writes_nothing(tmp_path):
    set_write_roots([tmp_path / "runs"])
    outside = tmp_path / "packages" / "README.md"

    with pytest.raises(WriteOutsideAllowedRoots):
        write_redacted(outside, "clean line\n")

    assert not outside.exists()


def test_I8_an_empty_root_list_leaves_the_guard_disabled(tmp_path):
    set_write_roots([])
    target = tmp_path / "anywhere" / "note.txt"

    guarded_write(target, "ok")

    assert target.read_text(encoding="utf-8") == "ok"


def test_I8_guarded_write_does_not_redact_what_it_is_given(tmp_path):
    set_write_roots([tmp_path])
    target = tmp_path / "raw.txt"

    guarded_write(target, GHP)

    assert target.read_text(encoding="utf-8") == GHP


def test_I8_allowed_roots_reports_the_roots_that_were_set(tmp_path):
    root = tmp_path / "runs"
    set_write_roots([root])

    reported = allowed_roots()

    assert len(reported) == 1
    assert reported[0].resolve() == root.resolve()
