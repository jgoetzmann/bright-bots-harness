"""Spec tests for ``harness.trust`` — Delivery 2 handoff §5.5 and §8.2 (B131).

Written from the spec before the implementation existed. Surface is frozen by
``.fullsend/RUN-DECISIONS-D2.md`` §6. Fixtures are inline on purpose.
Review selector: ``pytest tests/test_trust.py -k case`` (D2-R4.5).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from harness.trust import AUTHOR_ASSOCIATIONS, is_authorised, load_trust

SHIPPED_TRUST_FILE = (
    "# .harness/trust.txt — GitHub handles whose keyword commands are honoured.\n"
    "# One handle per line. '#' starts a comment. Case-insensitive.\n"
    "# Changing this file requires a reviewed PR (see .github/CODEOWNERS).\n"
    "jgoetzmann\n"
    "<NATHAN_HANDLE>\n"
)


def trust_file(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "trust.txt"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


TRUSTED = frozenset({"jgoetzmann"})


# ---------------------------------------------------------------------------
# B131 — both conditions
# ---------------------------------------------------------------------------

def test_B131_trusted_owner_is_authorised():
    """B131: handle in the trust file AND author_association OWNER → authorised."""
    assert is_authorised("jgoetzmann", "OWNER", TRUSTED) is True


def test_B131_trusted_member_and_collaborator_are_authorised():
    """B131: MEMBER and COLLABORATOR are the other two accepted associations."""
    assert is_authorised("jgoetzmann", "MEMBER", TRUSTED) is True
    assert is_authorised("jgoetzmann", "COLLABORATOR", TRUSTED) is True


def test_B131_trusted_handle_with_contributor_association_is_denied():
    """B131: trust-file membership alone is not sufficient — a trusted handle whose comment
    carries CONTRIBUTOR is denied."""
    assert is_authorised("jgoetzmann", "CONTRIBUTOR", TRUSTED) is False


def test_B131_untrusted_owner_is_denied():
    """B131: the association alone is not sufficient — an OWNER not in the trust file is denied."""
    assert is_authorised("mallory", "OWNER", TRUSTED) is False
    assert is_authorised("mallory", "MEMBER", TRUSTED) is False
    assert is_authorised("mallory", "COLLABORATOR", TRUSTED) is False


def test_B131_none_association_is_denied_even_when_trusted():
    """B131: NONE is GitHub's assertion of no relationship — denied regardless of the file."""
    assert is_authorised("jgoetzmann", "NONE", TRUSTED) is False
    assert is_authorised("mallory", "NONE", TRUSTED) is False


def test_B131_first_time_contributor_and_mannequin_are_denied():
    """B131: every association outside OWNER/MEMBER/COLLABORATOR is denied."""
    assert is_authorised("jgoetzmann", "FIRST_TIME_CONTRIBUTOR", TRUSTED) is False
    assert is_authorised("jgoetzmann", "FIRST_TIMER", TRUSTED) is False
    assert is_authorised("jgoetzmann", "MANNEQUIN", TRUSTED) is False


def test_B131_empty_association_is_denied():
    """B131: a missing/empty association is not one of the three accepted values."""
    assert is_authorised("jgoetzmann", "", TRUSTED) is False


def test_B131_empty_trust_set_denies_everyone():
    """B131: with nobody trusted, not even an OWNER is authorised — the gate fails closed."""
    assert is_authorised("jgoetzmann", "OWNER", frozenset()) is False
    assert is_authorised("", "OWNER", frozenset()) is False


def test_B131_empty_handle_is_denied():
    """B131: an empty handle never matches a trust entry."""
    assert is_authorised("", "OWNER", TRUSTED) is False


def test_B131_author_associations_constant_is_exactly_the_three():
    """B131: the accepted associations are OWNER, MEMBER, COLLABORATOR — nothing more."""
    assert AUTHOR_ASSOCIATIONS == frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
    assert isinstance(AUTHOR_ASSOCIATIONS, frozenset)


# ---------------------------------------------------------------------------
# B131 — case-insensitive handles (D2-R4.5: -k case)
# ---------------------------------------------------------------------------

def test_B131_case_insensitive_handle_matches_trust_file(tmp_path):
    """B131: JGoetzmann matches a trust file containing jgoetzmann (D2-R4.5)."""
    trusted = load_trust(trust_file(tmp_path, "jgoetzmann\n"))
    assert is_authorised("JGoetzmann", "OWNER", trusted) is True
    assert is_authorised("JGOETZMANN", "OWNER", trusted) is True
    assert is_authorised("jgoetzmann", "OWNER", trusted) is True


def test_B131_case_insensitive_load_trust_lowercases_mixed_case_entries(tmp_path):
    """B131: load_trust lower-cases what it reads, so a mixed-case file entry matches a
    lower-case login and vice versa."""
    trusted = load_trust(trust_file(tmp_path, "JGoetzmann\nNathanielHandle\n"))
    assert trusted == frozenset({"jgoetzmann", "nathanielhandle"})
    assert is_authorised("jgoetzmann", "OWNER", trusted) is True
    assert is_authorised("nathanielhandle", "MEMBER", trusted) is True
    assert is_authorised("NATHANIELHANDLE", "MEMBER", trusted) is True


def test_B131_case_insensitivity_does_not_widen_the_association_check(tmp_path):
    """B131: a case-variant of a trusted handle is still denied when the association is wrong —
    case folding applies to the handle, not to the gate."""
    trusted = load_trust(trust_file(tmp_path, "jgoetzmann\n"))
    assert is_authorised("JGoetzmann", "CONTRIBUTOR", trusted) is False
    assert is_authorised("JGoetzmann", "NONE", trusted) is False


def test_B131_case_variant_of_an_untrusted_handle_is_still_denied(tmp_path):
    """B131: case folding never turns an untrusted handle into a trusted one."""
    trusted = load_trust(trust_file(tmp_path, "jgoetzmann\n"))
    assert is_authorised("Mallory", "OWNER", trusted) is False
    assert is_authorised("JGOETZMANN2", "OWNER", trusted) is False


# ---------------------------------------------------------------------------
# B131 — trust file format (§5.5)
# ---------------------------------------------------------------------------

def test_B131_load_trust_ignores_comment_lines_and_blank_lines(tmp_path):
    """B131/§5.5: '#' lines and blank lines are not handles."""
    text = "# operator\n\njgoetzmann\n\n   \n# nathan goes below\n\n"
    trusted = load_trust(trust_file(tmp_path, text))
    assert trusted == frozenset({"jgoetzmann"})
    assert "" not in trusted
    assert not any(h.startswith("#") for h in trusted)


def test_B131_load_trust_ignores_the_nathan_handle_placeholder(tmp_path):
    """B131/RUN-DECISIONS-D2 §15: the shipped file's <NATHAN_HANDLE> placeholder is not a handle —
    it is neither loaded nor authorisable."""
    trusted = load_trust(trust_file(tmp_path, SHIPPED_TRUST_FILE))
    assert trusted == frozenset({"jgoetzmann"})
    assert "<nathan_handle>" not in trusted
    assert is_authorised("<NATHAN_HANDLE>", "OWNER", trusted) is False
    assert is_authorised("<nathan_handle>", "OWNER", trusted) is False


def test_B131_load_trust_missing_file_is_empty_and_denies(tmp_path):
    """B131: a missing trust file yields an empty frozenset — nobody is trusted, nothing raises."""
    trusted = load_trust(tmp_path / "does-not-exist" / "trust.txt")
    assert trusted == frozenset()
    assert isinstance(trusted, frozenset)
    assert is_authorised("jgoetzmann", "OWNER", trusted) is False


def test_B131_load_trust_empty_file_is_empty(tmp_path):
    """B131: an empty file trusts nobody."""
    assert load_trust(trust_file(tmp_path, "")) == frozenset()
    assert load_trust(trust_file(tmp_path, "\n\n")) == frozenset()


def test_B131_load_trust_comments_only_file_is_empty(tmp_path):
    """B131: a file of comments trusts nobody."""
    assert load_trust(trust_file(tmp_path, "# jgoetzmann\n# mallory\n")) == frozenset()


def test_B131_load_trust_strips_surrounding_whitespace(tmp_path):
    """B131/§5.5 'one handle per line': leading/trailing whitespace and CRLF line endings are not
    part of the handle."""
    path = tmp_path / "trust.txt"
    path.write_bytes(b"  jgoetzmann  \r\n\tnathan\r\n")
    trusted = load_trust(path)
    assert trusted == frozenset({"jgoetzmann", "nathan"})
    assert is_authorised("jgoetzmann", "OWNER", trusted) is True


def test_B131_load_trust_returns_a_frozenset(tmp_path):
    """B131: the trusted set is immutable — a frozenset, never a list or a mutable set."""
    trusted = load_trust(trust_file(tmp_path, "jgoetzmann\n"))
    assert isinstance(trusted, frozenset)
    with pytest.raises(AttributeError):
        trusted.add("mallory")  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# B131 - the shipped .harness/trust.txt itself, read from disk.
# Appended; nothing above is edited. `SHIPPED_TRUST_FILE` above is a hand copy
# of the file as it was written, so every test over it passes whatever the real
# governance file says today. This one opens the real file.
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
SHIPPED_TRUST_PATH = REPO_ROOT / ".harness" / "trust.txt"


def test_B131_the_shipped_trust_file_parses_to_real_handles_with_no_placeholder_left():
    """B131 / handoff 5.5: the governance file as shipped. `load_trust` must return at least two
    live handles - `identity.trust_file_ready()` gates tier readiness on exactly that count - and
    not one of them may still be a `<...>` placeholder. Who may command the harness is a decision;
    this is where changing it has to be made deliberately."""
    assert SHIPPED_TRUST_PATH.is_file(), f"{SHIPPED_TRUST_PATH} is the governance file"
    text = SHIPPED_TRUST_PATH.read_text(encoding="utf-8")
    trusted = load_trust(SHIPPED_TRUST_PATH)

    assert len(trusted) >= 2, f"the shipped file must name at least two handles: {sorted(trusted)}"
    assert "jgoetzmann" in trusted, f"the operator must stay trusted: {sorted(trusted)}"
    assert is_authorised("jgoetzmann", "OWNER", trusted) is True

    left = sorted(h for h in trusted if "<" in h or ">" in h or "_HANDLE" in h.upper())
    assert left == [], f"a placeholder is still being trusted: {left}"
    # `Identity.trust_file_ready()` rejects the whole file when the placeholder survives
    # anywhere in it, comments included, so the file is read the same way here.
    assert "NATHAN_HANDLE" not in text.upper(), "the shipped placeholder is still in the file"
    for handle in trusted:
        assert handle, "the empty string is not a handle"
        assert handle == handle.strip().lower(), f"not a normalised handle: {handle!r}"
        assert all(c.isalnum() or c == "-" for c in handle), f"not a GitHub handle: {handle!r}"
