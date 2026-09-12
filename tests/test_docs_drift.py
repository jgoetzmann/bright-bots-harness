"""Documentation-drift invariants: prose that names a constant, a path, a cron or an argv
element must still agree with the code it names.

Every test here reads both sides — the document and the source it describes — and compares them,
so the claim in the document cannot go stale silently. Nothing here runs the harness.

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


# --------------------------------------------------------------------------------------
# FOR-MAINTAINERS.md — the page a second maintainer reads first, and often the only one
# --------------------------------------------------------------------------------------


def _for_maintainers() -> str:
    return _read("docs/FOR-MAINTAINERS.md")


def test_for_maintainers_names_only_real_verbs():
    """It is the first page anybody reads, so a verb it names that does not exist is a person's
    first command doing nothing — and a command that parses to nothing gets no reply."""
    from harness.keywords import VERBS

    named = {m.group(1).lower() for m in re.finditer(r"`/harness (\w+)", _for_maintainers())}

    # Guard against the vacuous pass: if the page stops naming any verb at all, the subtraction
    # below is empty and this test would go green on a page that had lost its whole point.
    assert len(named) >= 6, f"FOR-MAINTAINERS.md names almost no verbs: {sorted(named)}"
    # An alias is a real word to type, so naming one is not drift -- naming something that is
    # neither is.
    from harness.keywords import ALIASES

    unknown = sorted(named - set(VERBS) - set(ALIASES))
    assert unknown == [], f"FOR-MAINTAINERS.md names verbs that do not exist: {unknown}"


def test_for_maintainers_names_only_real_stage_labels():
    from harness.store.sqlite import LABELS

    named = {m.group(0) for m in re.finditer(r"stage:[a-z-]+", _for_maintainers())}

    # The two "your move" stages are the reason the page exists; it must still name both.
    assert {"stage:needs-approval", "stage:needs-review"} <= named, (
        f"the page no longer names both human gates: {sorted(named)}"
    )
    unknown = sorted(named - set(LABELS.values()))
    assert unknown == [], f"FOR-MAINTAINERS.md names labels that do not exist: {unknown}"


def test_for_maintainers_links_the_inbox_the_config_actually_points_at():
    """The doc hard-codes the inbox issue's URL, because a link is what a reader can follow.
    If `INBOX_ISSUE` moves and the link does not, the page sends its only audience to the wrong
    thread — and comments on the wrong thread are read by nothing."""
    config = json.loads(_read(".harness/config.json"))
    inbox = int(config.get("INBOX_ISSUE") or 0)
    if not inbox:
        pytest.skip("INBOX_ISSUE is not set; the doc has no number to agree with")

    linked = {int(m.group(1)) for m in re.finditer(r"/issues/(\d+)\)", _for_maintainers())}

    assert inbox in linked, (
        f"INBOX_ISSUE is {inbox} but FOR-MAINTAINERS.md links issues {sorted(linked)}"
    )


def test_for_maintainers_quotes_the_run_window_the_config_sets():
    """§6 tells a maintainer when work runs, and that is the number they will plan around."""
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
# COMMANDS.md — the page that claims to document every command
# --------------------------------------------------------------------------------------


def _commands_md() -> str:
    return _read("docs/COMMANDS.md")


def test_commands_md_documents_every_verb_and_invents_none():
    """Its whole claim is completeness. A verb missing from it is a capability nobody learns
    about; a verb in it that does not exist is a command that silently does nothing when typed."""
    from harness.keywords import VERBS

    named = {m.group(1).lower() for m in re.finditer(r"/harness (\w+)", _commands_md())}
    documented = named & set(VERBS)

    assert set(VERBS) - documented == set(), f"undocumented verbs: {sorted(set(VERBS) - named)}"
    assert len(documented) == len(VERBS)


def test_commands_md_documents_every_cli_subcommand():
    """Same claim, other surface. Checked against the parser rather than a hand-kept list."""
    from harness.__main__ import COMMANDS

    text = _commands_md()
    missing = sorted(name for name in COMMANDS if f"harness {name}" not in text)

    assert missing == [], f"CLI subcommands absent from COMMANDS.md: {missing}"


def test_commands_md_states_each_verbs_real_level():
    """The level table is the one thing a reader acts on before trying a command, and getting it
    wrong wastes somebody's time on a refusal they were told would not happen."""
    from harness.keywords import VERB_LEVEL

    text = _commands_md()
    # The page carries a "who may run what" table. Its rows are checked individually, because
    # "the word `reject` and the string `level 3` both appear somewhere on the page" is not the
    # same claim as "the table says reject is level 3".
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
    """`harness halt` writes `HALT_FILE` (the gitignored root file, which stops a local run);
    `.harness/HALT` is committed and is what the workflows read. An operator told the CLI command
    stops the fleet would believe they had switched it off while it kept spending."""
    text = _commands_md()

    assert "harness halt" in text
    # The page must not claim the CLI command creates the committed switch.
    assert not re.search(r"harness halt[^\n]*\.harness/HALT", text), (
        "COMMANDS.md says `harness halt` creates .harness/HALT; it creates HALT_FILE"
    )
    assert "HALT_FILE" in text, "the page must name the file `harness halt` actually writes"


# --------------------------------------------------------------------------------------
# The machine PAT's scopes (D67) — no live document says the token lacks one it carries
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
    """What the token carries is what doctor checks it for (B305); one set, read, not copied."""
    from harness.__main__ import EXPECTED_TOKEN_SCOPES

    return frozenset(EXPECTED_TOKEN_SCOPES)


def _scope_live_docs() -> list[str]:
    """Every document read as the present truth. Not `docs/delivery/` (frozen, amended by
    DECISIONS.md) and not DECISIONS.md, which records what was true when it was written."""
    fixed = ["README.md", "HUMAN.md", ".env.example", "local/README.md", ".harness/README.md"]
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
    """B310 / D67: the machine PAT carries `public_repo`, `notifications` and `workflow`. Some
    forty statements said it held `public_repo` alone, that `workflow`'s absence was I-15, or
    that GitHub enforced I-15 -- including the rotation runbook, which would have reverted D67
    at the next rotation, and the body of every delivery PR. This is what stops the census being
    needed twice: the set is the one doctor checks, so the next grant moves both together."""
    false = _false_scope_claims(_read(doc), _carried())

    assert false == [], f"{doc} says something D67 made false: {false}"


@pytest.mark.parametrize("doc", _scope_live_docs())
def test_b310_no_reviewer_is_told_a_github_change_needs_no_check(doc):
    """B310 / D67: USING.md and PACKAGE-FORMAT.md filed `.github/**` under "things you do not
    need to check for", because GitHub refused the push. It no longer does; the harness's own
    check is the only one, so a reviewer who skips the file list skips the one backstop left."""
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
    """B310: a drift guard whose matcher never fires passes forever (B105's did, until D67).
    Each of these is a sentence this change corrected, verbatim or nearly."""
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


def test_b310_the_reviewer_matcher_catches_the_old_using_md_list():
    old = (
        "Three things you do not need to check for, because the code to do them does not "
        "exist:\n\n- The harness cannot merge, approve, or dismiss a review (I-12).\n"
        "- The PR cannot contain a change under `.github/**` (I-15).\n\nTo steer it instead."
    )
    assert any(".github" in span for span in _need_not_check_spans(old))


#: Every passage that tells a person -- or the model -- which scopes the token holds or should.
SCOPE_INSTRUCTIONS = (
    ("docs/OPERATIONS.md", "2. Generate a new one", "\n3. "),
    (".env.example", "# Classic GitHub PAT", "HARNESS_GITHUB_TOKEN="),
    ("README.md", "**One GitHub credential", "The only other secret"),
    ("docs/SAFETY.md", "holds one classic PAT", "\n- has"),
    ("prompts/system.md", "holds exactly one", "It is used by"),
    ("HUMAN.md", "Classic personal access token on", "https://"),
)


@pytest.mark.parametrize("doc, start, end", SCOPE_INSTRUCTIONS)
def test_b311_every_scope_instruction_names_exactly_what_doctor_expects(doc, start, end):
    """B311 / D67: the rotation runbook said "`public_repo` only; `workflow` stays off". A person
    following it after a leak would mint a token that cannot sync the fork, and nothing would
    say why until doctor's warning. Each place that says what to grant names doctor's set."""
    text = _read(doc)
    begin = text.index(start)
    passage = text[begin:text.index(end, begin + len(start))]

    named = set(re.findall(r"`([a-z_:]+)`", passage)) & CLASSIC_SCOPES

    assert named == set(_carried()), f"{doc} names {sorted(named)} for the token's scopes"


# --------------------------------------------------------------------------------------
# D69 / B351 — the tier table is a function of keywords.VERB_LEVEL, wherever it is written
# --------------------------------------------------------------------------------------
#
# The drift had already happened and only the tested page escaped it: `.harness/trust.txt` said
# level 1 was "`ask` only" and README said "`ask` alone", while VERB_LEVEL gives level 1 both
# `ask` and `status`. COMMANDS.md was right precisely because a test read it. The trust file's
# own header is the first thing anybody reads before editing it, so it is the worst page of the
# three to leave unchecked.


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


def _trust_file_level_rows() -> dict[int, str]:
    rows: dict[int, str] = {}
    for line in _read(".harness/trust.txt").splitlines():
        match = re.match(r"^#\s+([123])\s+(?:operator|maintainer|asker)\s+(.*)$", line)
        if match:
            rows[int(match.group(1))] = match.group(2)
    assert set(rows) == {1, 2, 3}, f".harness/trust.txt's level table is missing rows: {rows}"
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
    """Both directions. A verb missing from its row is a capability nobody is told they have;
    a verb in the wrong row sends somebody to type a command that will be refused."""
    named = _verbs_in(_commands_md_level_rows()[level], backticked=True)

    assert named == _tiers()[level], (
        f"COMMANDS.md's level-{level} row names {sorted(named)}; VERB_LEVEL says "
        f"{sorted(_tiers()[level])}"
    )


@pytest.mark.parametrize("level", [1, 2, 3])
def test_b351_the_trust_file_header_names_exactly_its_levels_verbs(level):
    """The header an operator reads while adding somebody. It said level 1 was `ask` only."""
    named = _verbs_in(_trust_file_level_rows()[level], backticked=False)

    assert named == _tiers()[level], (
        f".harness/trust.txt's level-{level} line names {sorted(named)}; VERB_LEVEL says "
        f"{sorted(_tiers()[level])}"
    )


@pytest.mark.parametrize("level", [1, 2, 3])
def test_b351_readme_names_exactly_each_levels_verbs(level):
    """README said level 1 was "`ask` alone", which has been wrong since `status` joined it."""
    named = _verbs_in(_readme_level_spans()[level], backticked=True)

    assert named == _tiers()[level], (
        f"README's level-{level} clause names {sorted(named)}; VERB_LEVEL says "
        f"{sorted(_tiers()[level])}"
    )


def test_b351_the_matcher_would_notice_the_drift_it_was_written_for():
    """A drift guard whose matcher never fires passes for ever (B105's did, until D67). These
    are the two sentences this change corrected, and a row that has lost a verb."""
    assert _verbs_in("level 1 (trusted) `ask` alone", backticked=True) == {"ask"}
    assert _verbs_in("1  asker       ask only", backticked=False) == {"ask"}
    assert _verbs_in("`ask` and `status`", backticked=True) == {"ask", "status"}
    assert _tiers()[1] == {"ask", "status"}, "level 1 is both verbs, which is the whole point"


def test_b351_every_verb_lands_in_exactly_one_tier():
    """The table cannot quietly lose one: `harness trust show` and three documents are all
    generated from it, so a verb absent here is a verb absent from every page at once."""
    from harness.keywords import VERB_LEVEL

    tiers = _tiers()
    placed = [verb for verbs in tiers.values() for verb in verbs]

    assert sorted(placed) == sorted(VERB_LEVEL), "a verb is in no tier, or in two"
    assert tiers[0] == set(), "level 0 is the absence of a line and gives no verb"
