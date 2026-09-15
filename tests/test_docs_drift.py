"""Documentation-drift invariants: prose that names a constant, a path, a cron or an argv
element must still agree with the code it names.

Each test reads a document and the source it describes and compares them. Nothing here runs the
harness.

Stack: Python 3.13 standard library + pytest==8.3.4 only.
"""

from __future__ import annotations

import json
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
    """A number word, including a hyphenated compound like ``twenty-three``."""
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
# B386: docs/PACKAGE-FORMAT.md's PR-body table names every section the body has
# --------------------------------------------------------------------------------------


def _pr_body_table() -> str:
    doc = _read("docs/PACKAGE-FORMAT.md")
    start = doc.index("## 6. The delivery PR")
    end = doc.find("\n## ", start + 1)
    section = doc[start:] if end < 0 else doc[start:end]
    return "\n".join(line for line in section.splitlines() if line.startswith("|"))


def test_B386_every_pr_body_heading_and_collapsed_section_is_in_the_package_format_table():
    """The body is built from the golden inputs, self-audit line included. Every `## ` heading
    outside a collapsed section, and every collapsed section's title, is named in §6's table."""
    from types import SimpleNamespace

    from harness.stages.deliver import build_pr_body

    inputs = REPO_ROOT / "tests" / "fixtures" / "deliver" / "pr_body_cap0"
    kwargs = json.loads((inputs / "kwargs.json").read_bytes().decode("utf-8"))
    kwargs["config"] = SimpleNamespace(**kwargs["config"])
    kwargs["trusted"] = tuple(kwargs["trusted"])
    body = build_pr_body(
        inputs / "package", **kwargs, self_audit="**Self-audit: not run for this revision.**"
    )

    outside = re.sub(r"<details>.*?</details>", "", body, flags=re.S)
    headings = re.findall(r"^## (.+)$", outside, re.M)
    titles = re.findall(r"<summary>(.+?)</summary>", body)
    assert len(headings) >= 3 and len(titles) >= 5, (headings, titles)
    table = _pr_body_table()
    missing = [name for name in headings + titles if name not in table]
    assert missing == [], f"docs/PACKAGE-FORMAT.md §6's table does not name: {missing}"
    assert "Self-audit" in table, "the table must name the self-audit line (D70)"


# --------------------------------------------------------------------------------------
# .github/CODEOWNERS: the reviewed-change guarantee covers everything that decides a result
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    sorted(
        set(verify_pin.PINNED)
        | {"harness/verify_pin.py", "harness/trust.py", "harness/keywords.py"}
    ),
)
def test_codeowners_covers_the_pinned_set_and_the_actor_gate(path):
    """Every file in `verify_pin.PINNED` has an owner, and so do `verify_pin.py`, which defines
    PINNED and `.harness/PIN`'s path, and `trust.py` and `keywords.py`, the actor gate a keyword
    command passes through (B131)."""
    codeowners = _read(".github/CODEOWNERS")
    pattern = r"^\s*/" + re.escape(path) + r"\s+.*@jgoetzmann"
    assert re.search(pattern, codeowners, re.M), f"CODEOWNERS must assign /{path} to @jgoetzmann"


def test_codeowners_covers_the_prompts_half_of_the_pinned_set():
    """The other half of `verify_pin.pinned_files` is every file under `prompts/`; the directory
    line is what covers it."""
    assert verify_pin.PROMPTS_DIR == "prompts"
    assert re.search(r"^\s*/prompts/\s+.*@jgoetzmann", _read(".github/CODEOWNERS"), re.M)


# --------------------------------------------------------------------------------------
# .harness/config.json: a document that spells the knob count spells the true one
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("doc", ["docs/SAFETY.md", ".harness/README.md"])
def test_a_spelled_config_key_count_matches_len_config_json_keys(doc):
    """`load_config` accepts exactly `CONFIG_JSON_KEYS`. Where a document spells that number in
    words near the constant, the word must be the current count."""
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
# Crons: a time a document quotes is one a workflow wakes at
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
    """A backticked cron in a live document is one a workflow carries."""
    live = _live_crons()
    assert live, "no crons found in .github/workflows"
    quoted = re.findall(r"`((?:[0-9*/,\-]+\s+){4}[0-9*/,\-]+)`", _read(doc))
    for cron in quoted:
        assert cron in live, (
            f"{doc} quotes cron {cron!r}, which no workflow carries; live: {sorted(live)}"
        )


# --------------------------------------------------------------------------------------
# heartbeat.yml: one precedence rule between the repository variables and .harness/config.json
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["TRACKING_ISSUE", "FORK_REPO"])
def test_heartbeat_reads_config_json_before_the_repository_variable(key):
    """.harness/README.md: the repository variables `FORK_REPO` and `TRACKING_ISSUE` are used
    only where `.harness/config.json` leaves the knob empty. heartbeat.yml reads both sources
    itself, so it must consult the file first."""
    text = _read(".github/workflows/heartbeat.yml")
    cfg_at = text.index(f"cfg.{key}")
    env_at = text.index(f"process.env.{key} ||")
    assert cfg_at < env_at, (
        f"heartbeat.yml must read cfg.{key} before process.env.{key} (.harness/README.md)"
    )


@pytest.mark.parametrize("name", ["discover.yml", "implement.yml", "feedback.yml"])
def test_the_env_writers_fill_config_json_only_where_it_leaves_a_knob_empty(name):
    """The same rule on the other three workflows, which apply it to `.harness/config.json` before
    the harness loads it."""
    text = (WORKFLOW_DIR / name).read_text(encoding="utf-8")
    assert 'if not cfg.get("FORK_REPO")' in text
    assert 'if cfg.get("TRACKING_ISSUE") in (None, "")' in text


# --------------------------------------------------------------------------------------
# docs/SAFETY.md I-3: the argv the document describes is the argv the runner builds
# --------------------------------------------------------------------------------------


def test_safety_i3_names_every_argv_element_the_runner_can_add():
    """I-3's note lists the flags the runner does add. With usage capture on (B200), which
    `get_runner` enables for the real backend (B202), `--output-format stream-json --verbose`
    replaces `--output-format json`, so the note names both."""
    runner = _read("harness/runner/cli.py")
    safety = _read("docs/SAFETY.md")
    section = safety[safety.index("### I-3"):safety.index("### I-4")]
    for flag in ("--max-budget-usd", "--output-format", "stream-json", "--verbose"):
        assert flag in runner, f"{flag} is no longer in harness/runner/cli.py"
        assert flag in section, f"docs/SAFETY.md I-3 must name {flag}"


# --------------------------------------------------------------------------------------
# .env.example: a comment may not promise a check load_config does not make
# --------------------------------------------------------------------------------------


def test_env_example_does_not_claim_an_equality_load_config_never_checks():
    """`load_config` validates `UPSTREAM_REPO` and `REPO` independently, so `.env.example` may
    promise they are equal only if config.py compares them."""
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
# README.md: the behavior ranges it claims are the ranges the suite cites
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
    """Every number inside the ranges README's tests row names is cited by a test. B87–B99 were
    never defined, so no range covers them."""
    cited = _cited_behaviors()
    missing = [
        n for low, high in _readme_behavior_ranges() for n in range(low, high + 1) if n not in cited
    ]
    assert not missing, f"README claims these are cited by a test; they are not: {missing}"


#: B1–B86 and B100–B150: the behaviors the original delivery specifications defined.
SPECIFIED_BEHAVIORS = frozenset(range(1, 87)) | frozenset(range(100, 151))


def test_readme_behavior_ranges_leave_no_specified_behavior_out():
    """The other direction: every specified behavior falls inside a range README names."""
    ranges = _readme_behavior_ranges()
    outside = sorted(
        n for n in SPECIFIED_BEHAVIORS if not any(low <= n <= high for low, high in ranges)
    )
    assert not outside, f"README's ranges omit specified behaviors: {outside}"


# --------------------------------------------------------------------------------------
# FOR-MAINTAINERS.md: the page a maintainer reads first
# --------------------------------------------------------------------------------------


def _for_maintainers() -> str:
    return _read("docs/FOR-MAINTAINERS.md")


def test_for_maintainers_names_only_real_verbs():
    """Every verb the page names exists; a command that parses to nothing gets no reply."""
    from harness.keywords import VERBS

    named = {m.group(1).lower() for m in re.finditer(r"`/harness (\w+)", _for_maintainers())}

    # Guard against a vacuous pass on a page that names no verbs.
    assert len(named) >= 6, f"FOR-MAINTAINERS.md names almost no verbs: {sorted(named)}"
    # An alias is a real word to type, so the page may name one.
    from harness.keywords import ALIASES

    unknown = sorted(named - set(VERBS) - set(ALIASES))
    assert unknown == [], f"FOR-MAINTAINERS.md names verbs that do not exist: {unknown}"


def test_for_maintainers_names_only_real_stage_labels():
    from harness.store.sqlite import LABELS

    named = {m.group(0) for m in re.finditer(r"stage:[a-z-]+", _for_maintainers())}

    # The page names both stages where a maintainer acts.
    assert {"stage:needs-approval", "stage:needs-review"} <= named, (
        f"the page no longer names both human gates: {sorted(named)}"
    )
    unknown = sorted(named - set(LABELS.values()))
    assert unknown == [], f"FOR-MAINTAINERS.md names labels that do not exist: {unknown}"


def test_for_maintainers_links_the_inbox_the_config_actually_points_at():
    """The page hard-codes the inbox URL, so it must link the issue `INBOX_ISSUE` names."""
    config = json.loads(_read(".harness/config.json"))
    inbox = int(config.get("INBOX_ISSUE") or 0)
    if not inbox:
        pytest.skip("INBOX_ISSUE is not set; the doc has no number to agree with")

    linked = {int(m.group(1)) for m in re.finditer(r"/issues/(\d+)\)", _for_maintainers())}

    assert inbox in linked, (
        f"INBOX_ISSUE is {inbox} but FOR-MAINTAINERS.md links issues {sorted(linked)}"
    )


def test_for_maintainers_quotes_the_run_window_the_config_sets():
    """The page quotes the run window `.harness/config.json` sets."""
    config = json.loads(_read(".harness/config.json"))
    start = str(config.get("RUN_WINDOW_START", ""))
    end = str(config.get("RUN_WINDOW_END", ""))
    text = _for_maintainers().lower()

    for bound in (start, end):
        day, _, time = bound.partition(" ")
        assert day and time, f"unreadable run window bound in config.json: {bound!r}"
        assert day in text and time in text, (
            f"FOR-MAINTAINERS.md does not name the configured run window bound {bound!r}"
        )


# --------------------------------------------------------------------------------------
# COMMANDS.md: the page that documents every command
# --------------------------------------------------------------------------------------


def _commands_md() -> str:
    return _read("docs/COMMANDS.md")


def test_commands_md_documents_every_verb_and_invents_none():
    """The page documents every verb and names none that does not exist."""
    from harness.keywords import VERBS

    named = {m.group(1).lower() for m in re.finditer(r"/harness (\w+)", _commands_md())}
    documented = named & set(VERBS)

    assert set(VERBS) - documented == set(), f"undocumented verbs: {sorted(set(VERBS) - named)}"
    assert len(documented) == len(VERBS)


def test_commands_md_documents_every_cli_subcommand():
    """The page names every CLI subcommand in `harness.__main__.COMMANDS`."""
    from harness.__main__ import COMMANDS

    text = _commands_md()
    missing = sorted(name for name in COMMANDS if f"harness {name}" not in text)

    assert missing == [], f"CLI subcommands absent from COMMANDS.md: {missing}"


def test_commands_md_states_each_verbs_real_level():
    """Each verb appears in its level's row of the page's level table."""
    from harness.keywords import VERB_LEVEL

    text = _commands_md()
    # Rows are checked one by one: both words appearing somewhere on the page is not enough.
    rows = {
        int(m.group(1)): m.group(2)
        for m in re.finditer(r"^\|\s*\*\*(\d)\*\*\s*\|[^|]*\|([^|]*)\|", text, re.M)
    }
    assert set(rows) >= {1, 2, 3}, f"the level table is missing rows: {sorted(rows)}"

    for verb, level in VERB_LEVEL.items():
        assert verb in rows[level], (
            f"`{verb}` is level {level}; the table's level-{level} row does not name it"
        )


def test_commands_md_does_not_confuse_the_two_kill_switches():
    """`harness halt` writes `HALT_FILE`, the gitignored root file that stops a local run;
    `.harness/HALT` is committed and is what the workflows read."""
    text = _commands_md()

    assert "harness halt" in text
    # The page must not claim the CLI command creates the committed switch.
    assert not re.search(r"harness halt[^\n]*\.harness/HALT", text), (
        "COMMANDS.md says `harness halt` creates .harness/HALT; it creates HALT_FILE"
    )
    assert "HALT_FILE" in text, "the page must name the file `harness halt` actually writes"


# --------------------------------------------------------------------------------------
# The machine PAT's scopes: no live document says the token lacks one it carries
# --------------------------------------------------------------------------------------

#: GitHub's classic token scopes, so a backticked word can be told from a scope name.
CLASSIC_SCOPES = frozenset({
    "repo", "repo:status", "repo_deployment", "public_repo", "repo:invite", "security_events",
    "workflow", "write:packages", "read:packages", "delete:packages", "admin:org", "write:org",
    "read:org", "manage_runners:org", "admin:public_key", "write:public_key", "read:public_key",
    "admin:repo_hook", "write:repo_hook", "read:repo_hook", "admin:org_hook", "gist",
    "notifications", "user", "read:user", "user:email", "user:follow", "delete_repo",
    "write:discussion", "read:discussion", "admin:enterprise", "codespace", "project",
    "read:project", "admin:gpg_key", "write:gpg_key", "read:gpg_key", "audit_log", "copilot",
})

#: A run of backticked names: "`a`", "`a`, `b` and `c`".
_NAMES = r"((?:`[a-z_:]+`(?:\s*,\s*|\s+and\s+|\s+)?)+)"


def _carried() -> frozenset[str]:
    """What the token carries: the set doctor checks it for (B305)."""
    from harness.__main__ import EXPECTED_TOKEN_SCOPES

    return frozenset(EXPECTED_TOKEN_SCOPES)


def _scope_live_docs() -> list[str]:
    """Every document read as current. DECISIONS.md is excluded: it records what was true when
    each decision was written."""
    fixed = ["README.md", ".env.example", "local/README.md", ".harness/README.md"]
    globbed = [
        path.relative_to(REPO_ROOT).as_posix()
        for pattern in ("docs/*.md", "prompts/*.md", ".github/workflows/*.yml")
        for path in sorted(REPO_ROOT.glob(pattern))
    ]
    return fixed + globbed


def _false_scope_claims(text: str, carried: frozenset[str]) -> list[str]:
    """Each phrase in `text` that says the token lacks a scope in `carried`, names part of
    `carried` as all it holds, or credits GitHub rather than the harness with I-15."""
    flat = " ".join(text.split())
    found: list[str] = []
    for scope in sorted(carried):
        s = re.escape(scope)
        for pattern in (
            rf"\b(?:has|carries|holds) no `{s}`",
            rf"\bno `{s}`(?: scope|,)",
            rf"\bnever `{s}`",
            rf"`{s}` stays off",
            rf"`{s}`[^.]{{0,60}}(?:deliberately (?:absent|withheld)|whose absence)",
            rf"deliberately absent is `{s}`",
            rf"\b(?:token|PAT) lacks the `{s}` scope and",
            rf"\bDo not add `{s}`",
        ):
            found += [m.group(0) for m in re.finditer(pattern, flat, re.I)]
    # "only" must end the phrase ("`a` only, present in"), so "`a` only gates b" is not a claim.
    held = (
        r"\s*(?:\*\*)?\s*"
        r"(?:(?:only|ONLY)(?=[\s*]*(?:[,.;:)|—-]|$))|and nothing else|, nothing else)"
    )
    for pattern in (_NAMES + held, r"\bbeyond " + _NAMES):
        for m in re.finditer(pattern, flat):
            named = set(re.findall(r"`([a-z_:]+)`", m.group(1))) & CLASSIC_SCOPES
            if named and named < carried:
                found.append(m.group(0))
    for pattern in (
        r"enforced (?:twice )?by (?:GitHub|the receiving end)",
        r"\breceiving end\b",
        r"GitHub itself (?:rejects|refuses)",
        r"enforced twice by two things",
    ):
        found += [m.group(0) for m in re.finditer(pattern, flat)]
    return found


def _need_not_check_spans(text: str) -> list[str]:
    """Each "a reviewer need not check for these" paragraph, with the list that follows it."""
    blocks = re.split(r"\n[ \t]*\n", text)
    spans: list[str] = []
    for index, block in enumerate(blocks):
        if not re.search(r"(?:need not|do not need to|don't need to) check", block, re.I):
            continue
        span = [block]
        for follow in blocks[index + 1:]:
            if not re.match(r"\s*(?:[-*]|\d+\.)\s", follow):
                break
            span.append(follow)
        spans.append("\n\n".join(span))
    return spans


@pytest.mark.parametrize("doc", _scope_live_docs())
def test_b310_no_live_document_says_the_token_lacks_a_scope_it_carries(doc):
    """B310: the machine PAT carries `public_repo`, `notifications` and `workflow`. No live
    document says it holds `public_repo` alone, that `workflow`'s absence is I-15, or that GitHub
    enforces I-15. The set is the one doctor checks, so a new grant moves both together."""
    false = _false_scope_claims(_read(doc), _carried())

    assert false == [], f"{doc} says something D67 made false: {false}"


@pytest.mark.parametrize("doc", _scope_live_docs())
def test_b310_no_reviewer_is_told_a_github_change_needs_no_check(doc):
    """B310: GitHub does not refuse a `.github/` push for this token, so the harness's own check is
    the only one, and no document tells a reviewer a `.github` change needs no check."""
    spans = [span for span in _need_not_check_spans(_read(doc)) if ".github" in span]

    assert spans == [], f"{doc} tells a reviewer a .github change needs no check: {spans}"


@pytest.mark.parametrize(
    "stale",
    [
        "A classic PAT with `public_repo` and nothing else, on a machine account.",
        "No `workflow` scope, so GitHub itself rejects any push touching `.github/workflows/`.",
        "classic, scope `public_repo` only, nothing else. `workflow` stays off; its absence is "
        "invariant I-15.",
        "| `Workflows` | none | The `workflow` scope is deliberately absent: GitHub rejects |",
        "or ask you for any access beyond `public_repo` on its own account.",
        "`.github/**` (I-15 — the token lacks the `workflow` scope and GitHub refuses the push)",
        "| Push to `.github/**` anywhere | **no** | I-15, enforced by GitHub |",
        "- holds one classic PAT, scope **`public_repo` only**.",
        "Any classic scope beyond `public_repo`: no `repo`, no `workflow`, no `admin:*`.",
        "Do not add `workflow`: its absence is invariant I-15.",
    ],
)
def test_b310_the_matcher_catches_each_sentence_d67_corrected(stale):
    """B310, as for B105: a drift guard is trusted only once its matcher is shown to fire. Each
    stale sentence below must match."""
    assert _false_scope_claims(stale, _carried()), f"the matcher misses {stale!r}"


@pytest.mark.parametrize(
    "current",
    [
        "A classic PAT with `public_repo`, `notifications` and `workflow` and nothing else.",
        "scopes `public_repo`, `notifications` and `workflow` **only** | `Metadata`: Read",
        "Any classic scope beyond `public_repo`, `notifications` and `workflow`: no `repo`.",
        "A token rotated without `workflow` fails here; leaving `workflow` off only stalls it.",
        "`workflow` only ever gated workflow files.",
    ],
)
def test_b310_the_matcher_passes_what_is_true_now(current):
    assert _false_scope_claims(current, _carried()) == []


def test_b310_the_reviewer_matcher_catches_a_need_not_check_list():
    old = (
        "Three things you do not need to check for, because the code to do them does not "
        "exist:\n\n- The harness cannot merge, approve, or dismiss a review (I-12).\n"
        "- The PR cannot contain a change under `.github/**` (I-15).\n\nTo steer it instead."
    )
    assert any(".github" in span for span in _need_not_check_spans(old))


#: Every passage that tells a person or the model which scopes the token holds or should.
SCOPE_INSTRUCTIONS = (
    ("docs/OPERATIONS.md", "2. Generate a new one", "\n3. "),
    (".env.example", "# Classic GitHub PAT", "HARNESS_GITHUB_TOKEN="),
    ("README.md", "**One GitHub credential", "The only other secret"),
    ("docs/SAFETY.md", "holds one classic PAT", "\n- has"),
    ("prompts/system.md", "holds exactly one", "It is used by"),
)


@pytest.mark.parametrize("doc, start, end", SCOPE_INSTRUCTIONS)
def test_b311_every_scope_instruction_names_exactly_what_doctor_expects(doc, start, end):
    """B311: every passage that says what to grant names exactly the set doctor checks, so a
    token minted from it can sync the fork."""
    text = _read(doc)
    begin = text.index(start)
    passage = text[begin:text.index(end, begin + len(start))]

    named = set(re.findall(r"`([a-z_:]+)`", passage)) & CLASSIC_SCOPES

    assert named == set(_carried()), f"{doc} names {sorted(named)} for the token's scopes"


# --------------------------------------------------------------------------------------
# B351: the level table is a function of keywords.VERB_LEVEL, wherever it is written
# --------------------------------------------------------------------------------------


def _tiers() -> dict[int, set[str]]:
    """What each level may give, from the gate's own table."""
    from harness.keywords import VERB_LEVEL
    from harness.trust import tier_table

    return {tier.level: set(tier.verbs) for tier in tier_table(VERB_LEVEL)}


def _verbs_in(text: str, *, backticked: bool) -> set[str]:
    """The verbs a passage names, ignoring every word that is not one."""
    from harness.keywords import VERB_LEVEL

    found = re.findall(r"`([^`]+)`", text) if backticked else re.findall(r"[a-z]+", text)
    return {word for word in found if word in VERB_LEVEL}


def _commands_md_level_rows() -> dict[int, str]:
    rows = {
        int(m.group(1)): m.group(3)
        for m in re.finditer(r"^\|\s*\*\*(\d)\*\*\s*\|([^|]*)\|([^|]*)\|", _commands_md(), re.M)
    }
    assert set(rows) >= {1, 2, 3}, f"COMMANDS.md's level table is missing rows: {sorted(rows)}"
    return rows


def _commands_md_level_names() -> dict[int, str]:
    return {
        int(m.group(1)): m.group(2).strip()
        for m in re.finditer(r"^\|\s*\*\*(\d)\*\*\s*\|([^|]*)\|([^|]*)\|", _commands_md(), re.M)
    }


def _trust_file_level_rows() -> dict[int, tuple[str, str]]:
    """``(name, verbs)`` per level, from the header an operator reads while editing the file.

    Level names come from `trust.LEVEL_NAMES`, so a level renamed in code but not in the file
    stops matching.
    """
    from harness.trust import LEVEL_NAMES

    names = "|".join(re.escape(LEVEL_NAMES[level]) for level in (1, 2, 3))
    rows: dict[int, tuple[str, str]] = {}
    for line in _read(".harness/trust.txt").splitlines():
        match = re.match(rf"^#\s+([123])\s+({names})\s+(.*)$", line)
        if match:
            rows[int(match.group(1))] = (match.group(2), match.group(3))
    assert set(rows) == {1, 2, 3}, (
        ".harness/trust.txt's level table is missing rows, or calls a level something "
        f"`trust.LEVEL_NAMES` does not: {rows}"
    )
    return rows


def _readme_level_spans() -> dict[int, str]:
    text = _read("README.md")
    start = text.index("Who may give which is set by level in")
    paragraph = text[start:text.index("\n\n", start)]
    marks = list(re.finditer(r"level (\d)", paragraph))
    spans = {
        int(mark.group(1)): paragraph[
            mark.end():(marks[i + 1].start() if i + 1 < len(marks) else len(paragraph))
        ]
        for i, mark in enumerate(marks)
    }
    assert set(spans) >= {1, 2, 3}, f"README names no per-level verbs: {sorted(spans)}"
    return spans


@pytest.mark.parametrize("level", [1, 2, 3])
def test_b351_the_commands_md_level_table_names_exactly_its_levels_verbs(level):
    """The row names exactly its level's verbs, no more and no fewer."""
    named = _verbs_in(_commands_md_level_rows()[level], backticked=True)

    assert named == _tiers()[level], (
        f"COMMANDS.md's level-{level} row names {sorted(named)}; VERB_LEVEL says "
        f"{sorted(_tiers()[level])}"
    )


@pytest.mark.parametrize("level", [1, 2, 3])
def test_b351_the_trust_file_header_names_exactly_its_levels_verbs(level):
    """The header an operator reads while adding somebody names each level's verbs exactly."""
    named = _verbs_in(_trust_file_level_rows()[level][1], backticked=False)

    assert named == _tiers()[level], (
        f".harness/trust.txt's level-{level} line names {sorted(named)}; VERB_LEVEL says "
        f"{sorted(_tiers()[level])}"
    )


@pytest.mark.parametrize("level", [1, 2, 3])
def test_b351_readme_names_exactly_each_levels_verbs(level):
    """README's per-level clauses name each level's verbs exactly."""
    named = _verbs_in(_readme_level_spans()[level], backticked=True)

    assert named == _tiers()[level], (
        f"README's level-{level} clause names {sorted(named)}; VERB_LEVEL says "
        f"{sorted(_tiers()[level])}"
    )


@pytest.mark.parametrize("level", [1, 2, 3])
def test_b351_every_page_calls_a_level_by_the_name_the_code_gives_it(level):
    """Each page calls a level by its `trust.LEVEL_NAMES` name, which `harness trust show` and
    `harness doctor` render. "Trusted" covers levels 1 to 3, so it never names level 1."""
    from harness.trust import LEVEL_NAMES

    name = LEVEL_NAMES[level]

    assert _commands_md_level_names()[level] == name
    assert name in _readme_level_spans()[level], f"README does not call level {level} {name!r}"
    assert _trust_file_level_rows()[level][0] == name


def test_b351_the_matcher_would_notice_the_drift_it_was_written_for():
    """The matchers fire on stale wording and on a row that has lost a verb."""
    from harness.trust import LEVEL_NAMES

    assert _verbs_in("level 1 (trusted) `ask` alone", backticked=True) == {"ask"}
    assert _verbs_in("1  asker       ask only", backticked=False) == {"ask"}
    assert _verbs_in("`ask` and `status`", backticked=True) == {"ask", "status"}
    assert _tiers()[1] == {"ask", "status"}, "level 1 gives both verbs"
    # The name check rejects README's old wording for level 1 and accepts the current one.
    assert LEVEL_NAMES[1] not in " (trusted) `ask` and `status`, level 0"
    assert LEVEL_NAMES[1] in " (asker) `ask` and `status`, level 0"


def test_b351_every_verb_lands_in_exactly_one_tier():
    """`harness trust show` and three documents are checked against this table, so every verb
    lands in exactly one tier."""
    from harness.keywords import VERB_LEVEL

    tiers = _tiers()
    placed = [verb for verbs in tiers.values() for verb in verbs]

    assert sorted(placed) == sorted(VERB_LEVEL), "a verb is in no tier, or in two"
    assert tiers[0] == set(), "level 0 is the absence of a line and gives no verb"
