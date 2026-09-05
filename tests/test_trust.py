"""Spec tests for ``harness.trust`` — Delivery 2 handoff §5.5 and §8.2 (B131).

Written from the spec before the implementation existed. Surface is frozen by
``.fullsend/RUN-DECISIONS-D2.md`` §6. Fixtures are inline on purpose.
Review selector: ``pytest tests/test_trust.py -k case`` (D2-R4.5).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from harness import trust
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
    """B131: a missing trust file trusts nobody and raises nothing.

    B269 changed the return type from a frozenset to a Trust carrying levels. What B131
    pins is unchanged, and is what is asserted here."""
    trusted = load_trust(tmp_path / "does-not-exist" / "trust.txt")
    assert trusted == frozenset()
    assert not trusted
    assert trusted.level_of("jgoetzmann") == 0
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


def test_B131_load_trust_returns_something_immutable(tmp_path):
    """B131: the trusted set is immutable - never a list or a mutable set. B269 made it
    a frozen Trust rather than a frozenset; immutability is what mattered."""
    import dataclasses

    trusted = load_trust(trust_file(tmp_path, "jgoetzmann" + chr(10)))
    assert "jgoetzmann" in trusted
    with pytest.raises(dataclasses.FrozenInstanceError):
        trusted.levels = {}
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


# --------------------------------------------------------------------------------------
# B269 / B271 / B273 - access levels (D60)
# --------------------------------------------------------------------------------------


def test_b269_a_level_is_read_from_the_line(tmp_path):
    """B269: `<level> <handle>`; the file says how much, not merely who."""
    trusted = load_trust(trust_file(tmp_path, "3 jgoetzmann\n2 BrightBoost-Tech\n"))

    assert trusted.level_of("jgoetzmann") == 3
    assert trusted.level_of("brightboost-tech") == 2
    assert trusted.implicit == ()


def test_b269_a_bare_handle_is_the_least_level_and_is_named(tmp_path):
    """B269: least privilege on ambiguity, loudly. A typo must never silently grant power, and
    must never silently take it away without saying so either."""
    trusted = load_trust(trust_file(tmp_path, "someone\n3 jgoetzmann\n"))

    assert trusted.level_of("someone") == trust.DEFAULT_LEVEL == 1
    assert trusted.implicit == ("someone",), "doctor has to be able to name it"


def test_b269_a_level_outside_the_range_is_not_a_level(tmp_path):
    """B269: guessing which end of the range the operator meant is worse than the default."""
    trusted = load_trust(trust_file(tmp_path, "9 someone\n0 nobody\n"))

    assert trusted.level_of("9") == 0, "the level must not be read as a handle"
    assert trusted.level_of("someone") == 0, "a two-token line with a bad level is not a grant"


def test_b269_a_handle_listed_twice_keeps_the_higher_level(tmp_path):
    trusted = load_trust(trust_file(tmp_path, "1 jgoetzmann\n3 jgoetzmann\n"))

    assert trusted.level_of("jgoetzmann") == 3


def test_b273_a_handle_absent_from_the_file_is_level_zero(tmp_path):
    trusted = load_trust(trust_file(tmp_path, "3 jgoetzmann\n"))

    assert trusted.level_of("stranger") == 0
    assert "stranger" not in trusted


@pytest.mark.parametrize("association", ["NONE", "CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", ""])
def test_b271_the_association_half_is_unchanged_by_levels(tmp_path, association):
    """B271: level 3 in the file with a non-member association is still refused. Neither half
    of the gate suffices alone, and adding levels did not make the file sufficient."""
    trusted = load_trust(trust_file(tmp_path, "3 jgoetzmann\n"))

    assert is_authorised("jgoetzmann", association, trusted, min_level=1) is False


def test_b270_a_level_below_the_requirement_is_refused(tmp_path):
    """B270: the level has to reach what the verb asks for."""
    trusted = load_trust(trust_file(tmp_path, "2 nathan\n"))

    assert is_authorised("nathan", "MEMBER", trusted, min_level=2) is True
    assert is_authorised("nathan", "MEMBER", trusted, min_level=3) is False


def test_b269_a_plain_set_of_handles_grants_only_the_least(tmp_path):
    """B269: a caller that has not been taught about levels cannot accidentally grant more."""
    assert is_authorised("x", "OWNER", {"x"}, min_level=1) is True
    assert is_authorised("x", "OWNER", {"x"}, min_level=2) is False


def test_b269_the_shipped_trust_file_names_its_levels():
    """B269: the file this repository ships must not rely on the default."""
    from pathlib import Path

    trusted = load_trust(Path(__file__).resolve().parent.parent / ".harness" / "trust.txt")

    assert trusted.implicit == (), f"level-less handles in the shipped file: {trusted.implicit}"
    assert trusted.level_of("jgoetzmann") == 3


def test_b269_a_malformed_level_line_is_refused_and_recorded(tmp_path):
    """B269: `9 someone` grants nothing. Neither clamping to a level nor reading `9` as a
    handle is an improvement on saying the line was refused."""
    trusted = load_trust(trust_file(tmp_path, "9 someone\n0 nobody\n3 jgoetzmann\n"))

    assert trusted.level_of("someone") == 0
    assert trusted.level_of("9") == 0
    assert trusted.malformed == ("9 someone", "0 nobody")
    assert trusted.level_of("jgoetzmann") == 3, "one bad line does not poison the file"
