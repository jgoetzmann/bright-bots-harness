"""Documentation-drift invariants: prose that names a constant, a path, a cron or an argv
element must still agree with the code it names.

Every test here reads both sides — the document and the source it describes — and compares them,
so the claim in the document cannot go stale silently. Nothing here runs the harness.

Stack: Python 3.13 standard library + pytest==8.3.4 only.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from harness import verify_pin
from harness.config import CONFIG_JSON_KEYS

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

#: Spelled counts a document might use for a set whose size the code decides.
NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30,
}


def _spelled(word: str) -> int | None:
    """A number word, including a hyphenated compound like ``twenty-three`` (Delivery 4)."""
    parts = word.split("-")
    if len(parts) == 1:
        return NUMBER_WORDS.get(parts[0])
    if len(parts) == 2:
        tens, units = NUMBER_WORDS.get(parts[0]), NUMBER_WORDS.get(parts[1])
        if tens is not None and units is not None and tens % 10 == 0 and 1 <= units <= 9:
            return tens + units
    return None


def _read(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


# --------------------------------------------------------------------------------------
# .github/CODEOWNERS — the reviewed-change guarantee covers everything that decides a result
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    sorted(
        set(verify_pin.PINNED)
        | {"harness/verify_pin.py", "harness/trust.py", "harness/keywords.py"}
    ),
)
def test_codeowners_covers_the_pinned_set_and_the_actor_gate(path):
    """Every code file in `verify_pin.PINNED` needs an owner, or a member of the pinned set can be
    changed without review; so does `verify_pin.py`, which *defines* PINNED and `.harness/PIN`'s
    path; so do `trust.py` and `keywords.py`, the actor gate a keyword command passes through
    (B131), which `.harness/README.md` already calls a code change rather than a knob."""
    codeowners = _read(".github/CODEOWNERS")
    pattern = r"^\s*/" + re.escape(path) + r"\s+.*@jgoetzmann"
    assert re.search(pattern, codeowners, re.M), f"CODEOWNERS must assign /{path} to @jgoetzmann"


def test_codeowners_covers_the_prompts_half_of_the_pinned_set():
    """The other half of `verify_pin.pinned_files` is every file under `prompts/`; the directory
    line is what covers it."""
    assert verify_pin.PROMPTS_DIR == "prompts"
    assert re.search(r"^\s*/prompts/\s+.*@jgoetzmann", _read(".github/CODEOWNERS"), re.M)


# --------------------------------------------------------------------------------------
# .harness/config.json — a document that spells the knob count must spell the true one
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("doc", ["docs/SAFETY.md", ".harness/README.md"])
def test_a_spelled_config_key_count_matches_len_config_json_keys(doc):
    """`load_config` accepts exactly `CONFIG_JSON_KEYS`. Where a document spells that number in
    words near the constant, the word must be the current one — D31/D32 took it from eleven to
    sixteen and left two documents behind."""
    text = _read(doc)
    expected = len(CONFIG_JSON_KEYS)
    for match in re.finditer(r"CONFIG_JSON_KEYS", text):
        window = text[max(0, match.start() - 200):match.end() + 200].lower()
        # Compounds first, so `twenty-three` is not read as `twenty` and then `three`.
        for word in re.findall(r"[a-z]+(?:-[a-z]+)?", window):
            value = _spelled(word)
            if value is not None and 10 <= value <= 40:
                assert value == expected, (
                    f"{doc} says {word!r} near CONFIG_JSON_KEYS; len(CONFIG_JSON_KEYS) is "
                    f"{expected}"
                )


# --------------------------------------------------------------------------------------
# Crons — an operator following a runbook must be given a time a workflow actually wakes at
# --------------------------------------------------------------------------------------


def _live_crons() -> set[str]:
    crons: set[str] = set()
    for path in sorted(WORKFLOW_DIR.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"""^\s*-\s*cron:\s*["']([^"']+)["']""", text, re.M):
            crons.add(match.group(1).strip())
    return crons


@pytest.mark.parametrize("doc", ["docs/OPERATIONS.md", "README.md", ".harness/README.md"])
def test_every_cron_a_live_document_quotes_is_one_a_workflow_carries(doc):
    """docs/OPERATIONS.md §3 told the operator to wait for `23 */6 * * *`, which D32 replaced with
    implement.yml's three window crons — a Wednesday reader would have waited until Monday. The
    frozen delivery documents under docs/delivery/ are exempt: they record the superseded value on
    purpose."""
    live = _live_crons()
    assert live, "no crons found in .github/workflows"
    quoted = re.findall(r"`((?:[0-9*/,\-]+\s+){4}[0-9*/,\-]+)`", _read(doc))
    for cron in quoted:
        assert cron in live, (
            f"{doc} quotes cron {cron!r}, which no workflow carries; live: {sorted(live)}"
        )


# --------------------------------------------------------------------------------------
# heartbeat.yml — one precedence rule between the repository variables and .harness/config.json
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["TRACKING_ISSUE", "FORK_REPO"])
def test_heartbeat_reads_config_json_before_the_repository_variable(key):
    """.harness/README.md: the repository variables `FORK_REPO` and `TRACKING_ISSUE` are used
    "only where this file leaves the knob empty". heartbeat.yml reads both sources itself, so it
    must consult `.harness/config.json` first; reading `process.env` first would let a variable
    changed without the file point the heartbeat at another issue and another fork."""
    text = _read(".github/workflows/heartbeat.yml")
    cfg_at = text.index(f"cfg.{key}")
    env_at = text.index(f"process.env.{key} ||")
    assert cfg_at < env_at, (
        f"heartbeat.yml must read cfg.{key} before process.env.{key} (.harness/README.md)"
    )


@pytest.mark.parametrize("name", ["discover.yml", "implement.yml", "feedback.yml"])
def test_the_env_writers_fill_config_json_only_where_it_leaves_a_knob_empty(name):
    """The same rule on the other three workflows, which apply it to `.harness/config.json` before
    the harness loads it. This is the behaviour heartbeat.yml was made to match."""
    text = (WORKFLOW_DIR / name).read_text(encoding="utf-8")
    assert 'if not cfg.get("FORK_REPO")' in text
    assert 'if cfg.get("TRACKING_ISSUE") in (None, "")' in text


# --------------------------------------------------------------------------------------
# docs/SAFETY.md I-3 — the argv the document describes is the argv the runner builds
# --------------------------------------------------------------------------------------


def test_safety_i3_names_every_argv_element_the_runner_can_add():
    """I-3's guarantee is that no permission-skipping flag can appear. The note under it lists what
    *does* appear; D31 swapped `--output-format json` for `--output-format stream-json --verbose`
    when usage capture is on (B200) and `get_runner` turns capture on for the real backend (B202),
    so a reviewer reading I-3 as the current argv must be told about the pair."""
    runner = _read("harness/runner/cli.py")
    safety = _read("docs/SAFETY.md")
    section = safety[safety.index("### I-3"):safety.index("### I-4")]
    for flag in ("--max-budget-usd", "--output-format", "stream-json", "--verbose"):
        assert flag in runner, f"{flag} is no longer in harness/runner/cli.py"
        assert flag in section, f"docs/SAFETY.md I-3 must name {flag}"


# --------------------------------------------------------------------------------------
# .env.example — a comment may not promise a check load_config does not make
# --------------------------------------------------------------------------------------


def test_env_example_does_not_claim_an_equality_load_config_never_checks():
    """`.env.example` asserted "UPSTREAM_REPO must equal REPO" while `load_config` validates the
    two independently (`_require_repo` on each, no comparison) — and D25 records the two
    `UPSTREAM_REPO == REPO` parametrizations being dropped because the rule was never in the
    frozen handoff. Either the check exists in config.py or the comment must not promise it."""
    config_src = _read("harness/config.py")
    enforced = re.search(
        r"upstream_repo\s*(==|!=)\s*repo\b|\brepo\s*(==|!=)\s*upstream_repo\b", config_src
    )
    if enforced:
        return
    example = _read(".env.example").lower()
    assert "must equal upstream_repo" not in example
    assert "must equal repo" not in example


# --------------------------------------------------------------------------------------
# README.md — the behavior ranges it claims are the ranges the suite actually cites
# --------------------------------------------------------------------------------------


def _readme_behavior_ranges() -> list[tuple[int, int]]:
    row = next(
        line for line in _read("README.md").splitlines() if line.startswith("| `tests/`")
    )
    ranges = [(int(a), int(b)) for a, b in re.findall(r"B(\d{1,3})[–-]B(\d{1,3})", row)]
    assert ranges, f"README's tests row names no behavior range: {row!r}"
    return ranges


def _cited_behaviors() -> set[int]:
    cited: set[int] = set()
    for path in sorted((REPO_ROOT / "tests").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        cited.update(int(m.group(1)) for m in re.finditer(r"[Bb](\d{1,3})\b", text))
    return cited


def test_readme_behavior_ranges_are_every_one_the_suite_cites():
    """README's layout table claimed "every behavior B1–B150", which stopped being the whole story
    when D31–D33 added B200–B215 (and was never the whole story for B87–B99, which no delivery
    document defines). Every number inside the ranges the table names must be cited by a test."""
    cited = _cited_behaviors()
    missing = [
        n for low, high in _readme_behavior_ranges() for n in range(low, high + 1) if n not in cited
    ]
    assert not missing, f"README claims these are cited by a test; they are not: {missing}"


def test_readme_behavior_ranges_leave_no_specified_behavior_out():
    """The other direction: a behavior defined in a delivery document must fall inside one of the
    ranges README names, so the next series cannot be quietly excluded from the claim."""
    spec = _read("docs/delivery/HARNESS-SPEC.md")
    handoff = _read("docs/delivery/DELIVERY-2-HANDOFF.md")
    defined = {int(m.group(1)) for m in re.finditer(r"\*\*B(\d{1,3})\.?\*\*", spec + handoff)}
    assert defined, "no behaviors found in the delivery documents"
    ranges = _readme_behavior_ranges()
    outside = sorted(n for n in defined if not any(low <= n <= high for low, high in ranges))
    assert not outside, f"README's ranges omit specified behaviors: {outside}"
