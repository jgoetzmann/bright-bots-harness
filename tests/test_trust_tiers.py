"""D69: the trust file is the security boundary, so one line must work everywhere (B340-B358).

D68 made `vouch:<id>` the way ONE account was heard without repository access. D69 makes it the
ordinary way anybody is added, and closes the ways a hand-edited line could grant nothing while
looking right: a trailing token nobody reads, a handle that is not a login, a placeholder that
vanishes, a second line that silently loses to the first.

The gate itself is unchanged. What changes is that a line either means what it says or is
refused and named -- never accepted into something quieter than its author meant.

B353-B358 are D69's adversarial pass, and every one of them is a way the first cut still ended
in a silent refusal: a real login the parser would not read, a level column that granted or
crashed, an account that can never comment, and a helper that needed a provisioned machine
before it would help.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import harness.__main__ as cli
from harness import keywords
from harness import trust as trust_mod
from harness.errors import GitHubError, RateCeilingReached
from harness.keywords import VERB_LEVEL
from harness.trust import comment_authorised, load_trust, parse_trust

REPO_ROOT = Path(__file__).resolve().parent.parent
SHIPPED = REPO_ROOT / ".harness" / "trust.txt"
NATHAN = "BrightBoost-Tech"
NATHAN_ID = 193453438
OTHER_ID = 987654321


def rest_comment(*, login: str, uid: object, association: str, body: str = "/harness ask why"):
    """One comment in the REST shape every production caller hands the gate."""
    return {
        "id": 1,
        "node_id": "IC_1",
        "user": {"login": login, "id": uid, "type": "User"},
        "author_association": association,
        "body": body,
    }


# --------------------------------------------------------------------------------------
# B340 - a trailing token nobody reads is a grant nobody meant
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        f"2 nathan {NATHAN_ID}",    # the id pasted with the keyword left off
        "2 nathan vouch",           # the keyword with the id left off
        "2 nathan (maintainer)",    # a note to the reader
        "2 nathan level:2",         # the level written twice, the second time as a token
        "2 nathan vouch:1 extra",   # a good vouch with something after it
    ],
)
def test_B340_an_unrecognised_trailing_token_refuses_the_whole_line(line):
    """Before D69 only tokens beginning `vouch` were inspected and every other token was
    discarded, so `2 nathan 193453438` parsed to a plain level-2 line: it looked like a vouch,
    granted nothing off a repository he was a collaborator on, and nothing said so.

    Refused rather than guessed at, for the same reason D68 refuses a malformed vouch: read as
    "no vouch" the line still grants a level, which is not what its author meant either.
    """
    trusted = parse_trust(f"3 jgoetzmann\n{line}\n")

    assert trusted.level_of("nathan") == 0, "a line with an unread token must grant nothing"
    assert trusted.vouched_id("nathan") is None
    assert line.split("#", 1)[0].strip() in trusted.malformed
    assert trusted.level_of("jgoetzmann") == 3, "one bad line does not poison the file"


def test_B340_a_trailing_comment_is_a_comment_and_never_a_vouch():
    """`#` still starts a comment, so `2 nathan #193453438` is the bare line `2 nathan` -- which
    grants level 2 by the association route and vouches for nobody. Pinned because it is the
    near-miss of the line above: the same digits, one character apart, and the opposite reading.
    """
    trusted = parse_trust("2 nathan #193453438\n")

    assert trusted.level_of("nathan") == 2
    assert trusted.vouched_id("nathan") is None, "a commented id is not a vouch"
    assert trusted.malformed == ()


def test_B340_a_well_formed_vouch_is_still_the_ordinary_line():
    """The rule tightens what is refused; it must not narrow what works."""
    trusted = parse_trust(f"3 jgoetzmann\n2 {NATHAN} vouch:{NATHAN_ID}\n2 invited-maintainer\n")

    assert trusted.level_of(NATHAN) == 2
    assert trusted.vouched_id(NATHAN) == NATHAN_ID
    assert trusted.level_of("invited-maintainer") == 2
    assert trusted.malformed == () and trusted.skipped == () and trusted.duplicated == ()


# --------------------------------------------------------------------------------------
# B341 - a handle that can never match a login
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "2 nathan@example.com",   # an email address
        "2 nathan,",              # a comma-separated list that lost its spaces
        "2 nathan_handle",        # underscores are not legal in a GitHub login
        "2 https://github.com/nathan",
    ],
)
def test_B341_a_handle_that_is_not_a_github_login_refuses_the_line(line):
    """These registered literally and were then refused for ever, silently: no GitHub login can
    equal `nathan@example.com`, so the line was a grant to nobody."""
    trusted = parse_trust(f"3 jgoetzmann\n{line}\n")

    assert len(trusted) == 1 and "jgoetzmann" in trusted
    assert trusted.malformed == (line,)


@pytest.mark.parametrize("handle", ["jgoetzmann", "BrightBoost-Tech", "a", "a-b-c", "user123"])
def test_B341_a_real_login_shape_is_accepted(handle):
    """Letters, digits and hyphens, which is what GitHub allows. The guard must not refuse
    anybody the file legitimately names -- `@` and case are still normalised away."""
    trusted = parse_trust(f"2 {handle}\n2 @{handle.upper()}\n")

    assert trusted.level_of(handle) == 2
    assert trusted.malformed == ()


# --------------------------------------------------------------------------------------
# B342 - a placeholder is recorded, not vanished
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("line", ["2 <NEW_MAINTAINER>", "<NATHAN_HANDLE>", "2 nathan <id here>"])
def test_B342_a_placeholder_line_is_recorded_as_skipped(line):
    """It still grants nothing -- B131 pins that. What is new is that it leaves a trace: before
    D69 it vanished with no entry anywhere, so `doctor` could not name it, while the same line
    failed `Identity.trust_file_ready()` for a reason nothing connected to it."""
    trusted = parse_trust(f"3 jgoetzmann\n{line}\n")

    assert trusted.skipped == (line,)
    assert line.strip("<> ").lower() not in trusted
    assert trusted.level_of("jgoetzmann") == 3


def test_B342_a_placeholder_is_not_reported_as_malformed():
    """Two different findings with two different fixes: one is a line to correct, the other is a
    line to finish. Telling the operator a placeholder "is not a level" sends them hunting."""
    trusted = parse_trust("2 <NEW_MAINTAINER>\n")

    assert trusted.skipped == ("2 <NEW_MAINTAINER>",)
    assert trusted.malformed == ()


# --------------------------------------------------------------------------------------
# B343 - a handle named twice
# --------------------------------------------------------------------------------------


def test_B343_a_handle_named_by_two_lines_is_recorded():
    """The highest level still wins (B269 pins it), so a line added to DEMOTE somebody does
    nothing at all. That is worth saying out loud: it is the one edit whose failure looks
    exactly like success."""
    trusted = parse_trust("3 jack\n1 jack\n")

    assert trusted.level_of("jack") == 3, "B269: the highest level still wins"
    assert trusted.duplicated == ("jack",)


def test_B343_one_line_per_handle_is_not_a_duplicate():
    trusted = parse_trust(f"3 jgoetzmann\n2 {NATHAN} vouch:{NATHAN_ID}\n")

    assert trusted.duplicated == ()


# --------------------------------------------------------------------------------------
# B344 - the vouched line is the ordinary way in, and B131 is untouched without one
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("association", ["NONE", "CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", ""])
def test_B344_one_vouched_line_is_heard_with_no_repository_access(association):
    """The whole point of D69: adding somebody is one line, and it works without an invite."""
    trusted = parse_trust("3 jgoetzmann\n2 newcomer vouch:4242\n")

    assert comment_authorised(
        rest_comment(login="newcomer", uid=4242, association=association), trusted
    ) is True
    assert comment_authorised(
        rest_comment(login="newcomer", uid=OTHER_ID, association="OWNER"), trusted
    ) is False


@pytest.mark.parametrize("association", ["NONE", "CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", ""])
def test_B344_an_unvouched_line_is_still_B131_exactly(association):
    """Generalising the vouch must not weaken the association route for anybody using it."""
    trusted = parse_trust("3 jgoetzmann\n2 invited-maintainer\n")
    comment = rest_comment(login="invited-maintainer", uid=55, association=association)

    assert comment_authorised(comment, trusted) is False
    assert comment_authorised(dict(comment, author_association="COLLABORATOR"), trusted) is True


def test_B344_the_level_still_caps_a_vouched_line():
    """A vouch replaces the association half and nothing else."""
    trusted = parse_trust("2 newcomer vouch:4242\n")
    comment = rest_comment(login="newcomer", uid=4242, association="NONE", body="/harness halt")

    heard = keywords.commands_from(
        comment, surface="issue", number=4, trusted=trusted,
        ledger=_ledger(),
    )

    assert [c.verb for c in heard] == ["__denied__"]
    assert "needs level 3" in heard[0].args


def _ledger():
    from harness.ledger import Ledger

    led = Ledger.empty("2026-08-31T00:00:00Z")
    led.cursors["notifications_last_seen"] = "2026-09-01T00:00:00Z"
    return led


# --------------------------------------------------------------------------------------
# B345 - the file, interpreted
# --------------------------------------------------------------------------------------


def test_B345_describe_says_the_level_and_the_route_for_each_handle():
    trusted = parse_trust(f"3 jgoetzmann\n2 {NATHAN} vouch:{NATHAN_ID}\n1 asker-only\n")

    entries = {e.handle: e for e in trust_mod.describe(trusted)}

    assert entries["jgoetzmann"].level == 3
    assert entries["jgoetzmann"].route == "association"
    assert entries["jgoetzmann"].vouched_id is None
    assert entries[NATHAN.lower()].route == "vouch"
    assert entries[NATHAN.lower()].vouched_id == NATHAN_ID
    assert entries["asker-only"].level_name == "asker"


def test_B345_describe_is_ordered_most_privileged_first():
    trusted = parse_trust("1 asker-only\n3 jgoetzmann\n2 middle\n")

    assert [e.handle for e in trust_mod.describe(trusted)] == [
        "jgoetzmann", "middle", "asker-only"
    ]


def test_B345_every_refused_line_is_reported_with_a_reason_of_its_own():
    """The four ways a line grants nothing have four different fixes, so they get four
    different sentences rather than one bucket."""
    text = (
        "3 jgoetzmann\n"
        "9 out-of-range\n"
        f"2 {NATHAN} vouch:{NATHAN_ID}x\n"
        "2 <PLACEHOLDER>\n"
        "2 nathan@example.com\n"
    )

    reasons = dict(trust_mod.refusals(parse_trust(text)))

    assert "is not a level" in reasons["9 out-of-range"]
    assert "vouch" in reasons[f"2 {NATHAN} vouch:{NATHAN_ID}x"]
    assert "placeholder" in reasons["2 <PLACEHOLDER>"]
    assert "login" in reasons["2 nathan@example.com"]
    assert "3 jgoetzmann" not in reasons, "an accepted line is not a refusal"


def test_B345_a_disagreement_about_the_account_keeps_its_own_reason():
    """B331's wording, preserved: each line is well formed on its own, so "not a level" would
    send the operator looking for a typo that is not there."""
    pair = (f"3 {NATHAN}", f"2 {NATHAN} vouch:{NATHAN_ID}")

    reasons = dict(trust_mod.refusals(parse_trust("\n".join(pair) + "\n")))

    for line in pair:
        assert "disagrees with another line" in reasons[line]


# --------------------------------------------------------------------------------------
# B346 - the tier table is a function of the verb levels, not a copy of them
# --------------------------------------------------------------------------------------


def test_B346_the_tier_table_is_built_from_the_mapping_it_is_given():
    """Passed in rather than imported, so `trust.py` keeps importing nothing of ours --
    `keywords` imports `trust`, and the cycle is what `links._who` already dodges by hand."""
    table = {tier.level: tier for tier in trust_mod.tier_table({"a": 1, "b": 2, "c": 2})}

    assert table[1].verbs == ("a",)
    assert table[2].verbs == ("b", "c")
    assert table[3].verbs == ()
    assert table[0].verbs == ()
    assert table[2].name == "maintainer"


def test_B346_the_real_table_covers_every_verb_and_invents_none():
    table = trust_mod.tier_table(VERB_LEVEL)

    assert sorted(v for tier in table for v in tier.verbs) == sorted(VERB_LEVEL)
    for tier in table:
        for verb in tier.verbs:
            assert VERB_LEVEL[verb] == tier.level


def test_B346_level_zero_is_the_absence_of_a_line_and_grants_no_verb():
    """0 is not a tier anybody writes: `0 someone` is refused (B269), and an absent handle is
    level 0 already. The table says so rather than leaving a gap."""
    zero = next(t for t in trust_mod.tier_table(VERB_LEVEL) if t.level == 0)

    assert zero.verbs == ()
    assert parse_trust("0 nobody\n").level_of("nobody") == 0


# --------------------------------------------------------------------------------------
# B347 - `harness trust line`: the exact line to paste
# --------------------------------------------------------------------------------------


class UsersGh:
    """`GET /users/<login>` as GitHub answers it, unauthenticated.

    `kinds` is the `type` field: GitHub returns "User" for a person and "Organization" for an
    organisation, and the real endpoint answers 200 for both (B355).
    """

    def __init__(self, users=None, fail=None, kinds=None):
        self.users = {k.lower(): v for k, v in (users or {}).items()}
        self.kinds = {k.lower(): v for k, v in (kinds or {}).items()}
        self.fail = fail
        self.asked: list[str] = []
        self.dry_run = False

    def get(self, path):
        self.asked.append(path)
        if self.fail is not None:
            raise self.fail
        login = path.rsplit("/", 1)[1]
        if login.lower() not in self.users:
            raise GitHubError(
                f'github returned 404 for https://api.github.com{path}: {{"message":"Not Found"}}'
            )
        return {
            "login": login,
            "id": self.users[login.lower()],
            "type": self.kinds.get(login.lower(), "User"),
        }


def _no_context(config, args, run_id):
    """`harness trust` must not build one. Asserted in every trust test rather than described.

    B356: the lookup is a single unauthenticated GET and `show` reads one local text file, so a
    `Context` -- store, governor, ledger, clone manager, write guard -- is a machine the
    command has no use for and cannot assume. Building one meant a fresh checkout answered
    "could not start up to look @x up: no .env file at .env" and never asked GitHub anything.
    """
    raise AssertionError(
        "`harness trust` built a Context; it needs a GitHub client, not a provisioned machine"
    )


def run_trust(tmp_path, monkeypatch, capsys, argv, *, trust="3 jgoetzmann\n", gh=None,
              configured=True):
    """`harness trust ...` in a repository whose trust file holds `trust`.

    `configured=False` is a checkout with no `.env` at all -- the state somebody is in exactly
    when they want this command most.
    """
    from conftest import write_env

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".harness").mkdir(exist_ok=True)
    (tmp_path / ".harness" / "trust.txt").write_text(trust, encoding="utf-8")
    client = gh if gh is not None else UsersGh({NATHAN: NATHAN_ID})
    monkeypatch.setattr(cli, "PUBLIC_READER", lambda: client)
    monkeypatch.setattr(cli, "_context", _no_context)
    argv = ["trust", *argv]
    if configured:
        env = write_env(tmp_path / ".env", TRUST_FILE=".harness/trust.txt")
        argv = ["--config", str(env), *argv]
    code = cli.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_B347_trust_line_prints_the_line_to_paste_and_nothing_else_on_stdout(
    tmp_path, monkeypatch, capsys
):
    """The whole value is that it can be copied without editing, so stdout carries the line and
    the explanation goes to stderr."""
    code, out, err = run_trust(tmp_path, monkeypatch, capsys, ["line", NATHAN, "--level", "2"])

    assert code == 0
    assert out.strip() == f"2 {NATHAN} vouch:{NATHAN_ID}"
    assert parse_trust(out).level_of(NATHAN) == 2, "the line it prints must be one it accepts"
    assert "maintainer" in err


def test_B347_the_printed_line_grants_exactly_what_it_promises(tmp_path, monkeypatch, capsys):
    """Round trip: the text this prints, parsed back, admits that account and no other."""
    _, out, _ = run_trust(tmp_path, monkeypatch, capsys, ["line", NATHAN, "--level", "2"])
    trusted = parse_trust(out)

    assert comment_authorised(
        rest_comment(login=NATHAN, uid=NATHAN_ID, association="NONE"), trusted
    ) is True
    assert comment_authorised(
        rest_comment(login=NATHAN, uid=OTHER_ID, association="OWNER"), trusted
    ) is False


@pytest.mark.parametrize(
    "fail",
    [
        None,  # a 404: no such login
        RateCeilingReached("unauthenticated reads are capped at 60/hour per address"),
        GitHubError("github returned 502 for https://api.github.com/users/x: bad gateway"),
    ],
)
def test_B347_a_failed_lookup_prints_no_line_at_all(tmp_path, monkeypatch, capsys, fail):
    """An unvouched line is precisely the entry that gets silently denied. Printing one after
    failing to resolve the id would manufacture the defect this command exists to prevent."""
    gh = UsersGh({}, fail=fail)
    code, out, err = run_trust(tmp_path, monkeypatch, capsys, ["line", NATHAN, "--level", "2"],
                               gh=gh)

    assert code != 0
    assert out.strip() == "", f"a line was printed after a failed lookup: {out!r}"
    assert NATHAN.lower() in err.lower()


def test_B347_a_failed_lookup_says_which_account_it_could_not_resolve(
    tmp_path, monkeypatch, capsys
):
    """The sentence has to name the thing that failed. Its ancestor reported "could not resolve
    @<login>'s account id" for a database that would not open, sending somebody to check a
    login that was never the problem; B356 removed the database instead of the sentence."""
    gh = UsersGh({}, fail=GitHubError("github returned 502 for /users/x: bad gateway"))
    code, out, err = run_trust(
        tmp_path, monkeypatch, capsys, ["line", NATHAN, "--level", "2"], gh=gh
    )

    assert code != 0 and out.strip() == ""
    assert f"could not resolve @{NATHAN}'s account id" in err
    assert "bad gateway" in err, "the real reason has to survive"


def test_B347_no_vouch_prints_the_association_line_without_asking_github(
    tmp_path, monkeypatch, capsys
):
    """For somebody who IS a collaborator the bare line is right, and needs no id."""
    gh = UsersGh({})
    code, out, err = run_trust(
        tmp_path, monkeypatch, capsys, ["line", "invited", "--level", "2", "--no-vouch"], gh=gh
    )

    assert code == 0
    assert out.strip() == "2 invited"
    assert gh.asked == [], "the association route needs no account id"
    assert "collaborator" in err.lower()


def test_B347_the_level_must_be_given(tmp_path, monkeypatch, capsys):
    """Defaulting somebody's authority is exactly the silent misgrant this work removes."""
    with pytest.raises(SystemExit):
        run_trust(tmp_path, monkeypatch, capsys, ["line", NATHAN])


@pytest.mark.parametrize("level,expected", [("3", 3), ("operator", 3), ("maintainer", 2)])
def test_B347_the_level_may_be_a_digit_or_the_name(tmp_path, monkeypatch, capsys, level, expected):
    _, out, _ = run_trust(tmp_path, monkeypatch, capsys, ["line", NATHAN, "--level", level])

    assert parse_trust(out).level_of(NATHAN) == expected


def test_B347_a_login_that_is_not_a_login_is_refused_before_any_lookup(
    tmp_path, monkeypatch, capsys
):
    gh = UsersGh({})
    code, out, err = run_trust(
        tmp_path, monkeypatch, capsys, ["line", "not a login", "--level", "2"], gh=gh
    )

    assert code != 0 and out.strip() == "" and gh.asked == []


def test_B347_trust_never_writes_the_trust_file(tmp_path, monkeypatch, capsys):
    """`.harness/` is outside the write roots on purpose (B143): the command produces text a
    human commits through a reviewed pull request."""
    before = "3 jgoetzmann\n"
    run_trust(tmp_path, monkeypatch, capsys, ["line", NATHAN, "--level", "2"], trust=before)

    assert (tmp_path / ".harness" / "trust.txt").read_text(encoding="utf-8") == before


# --------------------------------------------------------------------------------------
# B348 - `harness trust show`: the file as the gate reads it
# --------------------------------------------------------------------------------------


def test_B348_show_names_each_handle_its_level_and_its_route(tmp_path, monkeypatch, capsys):
    trust = f"3 jgoetzmann\n2 {NATHAN} vouch:{NATHAN_ID}\n"
    code, out, _ = run_trust(tmp_path, monkeypatch, capsys, ["show"], trust=trust)

    assert code == 0
    assert "@jgoetzmann" in out and "operator" in out
    assert f"@{NATHAN.lower()}" in out and str(NATHAN_ID) in out
    assert "association" in out, "the invisible half has to be named for the handles that need it"


def test_B348_show_names_every_entry_that_would_be_silently_refused(
    tmp_path, monkeypatch, capsys
):
    trust = "3 jgoetzmann\n9 out-of-range\n2 <PLACEHOLDER>\n2 nathan@example.com\n"
    _, out, _ = run_trust(tmp_path, monkeypatch, capsys, ["show"], trust=trust)

    for refused in ("9 out-of-range", "2 <PLACEHOLDER>", "2 nathan@example.com"):
        assert refused in out, f"{refused!r} grants nothing and must be named"


def test_B348_show_prints_the_tier_table(tmp_path, monkeypatch, capsys):
    _, out, _ = run_trust(tmp_path, monkeypatch, capsys, ["show"])

    for verb, level in VERB_LEVEL.items():
        assert verb in out, f"the table must name {verb}"
    assert "operator" in out and "maintainer" in out and "asker" in out


def test_B348_bare_trust_is_show(tmp_path, monkeypatch, capsys):
    _, bare, _ = run_trust(tmp_path, monkeypatch, capsys, [])
    _, shown, _ = run_trust(tmp_path, monkeypatch, capsys, ["show"])

    assert bare == shown


def test_B348_show_is_a_registered_command():
    assert "trust" in cli.COMMANDS


# --------------------------------------------------------------------------------------
# B349 - doctor
# --------------------------------------------------------------------------------------


def doctor(tmp_path, monkeypatch, capsys, *, trust, gh=None):
    from conftest import write_env

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".harness").mkdir(exist_ok=True)
    (tmp_path / ".harness" / "trust.txt").write_text(trust, encoding="utf-8")
    env = write_env(tmp_path / ".env", TRUST_FILE=".harness/trust.txt")
    client = gh if gh is not None else UsersGh({NATHAN: NATHAN_ID})
    monkeypatch.setattr(cli, "_context", lambda config, args, run_id: SimpleNamespace(gh=client))
    code = cli.main(["--config", str(env), "doctor"])
    out = capsys.readouterr().out
    assert cli.main(["--config", str(env), "--json", "doctor"]) == code
    return code, out, json.loads(capsys.readouterr().out)


def test_B349_doctor_names_a_placeholder_line(tmp_path, monkeypatch, capsys):
    """It vanished entirely before D69, while failing the tier check for a reason nothing
    connected to it."""
    _, out, payload = doctor(tmp_path, monkeypatch, capsys,
                             trust="3 jgoetzmann\n2 <NEW_MAINTAINER>\n")

    assert any("<NEW_MAINTAINER>" in w for w in payload["warnings"])
    assert "<NEW_MAINTAINER>" in out


def test_B349_doctor_names_a_duplicated_handle(tmp_path, monkeypatch, capsys):
    _, out, payload = doctor(tmp_path, monkeypatch, capsys, trust="3 jack\n1 jack\n")

    assert any("jack" in w and "more than one line" in w for w in payload["warnings"])


def test_B349_a_refused_line_is_a_warning_and_no_longer_stops_the_fleet(
    tmp_path, monkeypatch, capsys
):
    """D69 moves this from `problems` to `warnings`. `doctor` gates discover.yml, feedback.yml
    and implement.yml under `set -e` and exits 3 on any problem, so one typo in a hand-edited
    file stopped everything -- the failure #27 already fixed once for the stranded-access
    warning. The line grants nothing at the gate either way, so the fleet-wide stop bought no
    safety; the compensating check is the build-time one in test_docs_drift.py, which fails the
    pull request that would ship such a line.
    """
    line = f"2 {NATHAN} vouch:{NATHAN_ID}x"
    code, out, payload = doctor(tmp_path, monkeypatch, capsys, trust=f"3 jgoetzmann\n{line}\n")

    assert code == 0, "a bad trust line must not take the fleet down"
    assert not [p for p in payload["problems"] if "trust" in p]
    assert [w for w in payload["warnings"] if line in w]
    assert "warnings (the harness still runs)" in out


def test_B349_a_refused_line_still_grants_nothing(tmp_path, monkeypatch, capsys):
    """Fail-closed is the parser's job, and it is untouched by the severity move."""
    trusted = parse_trust(f"3 jgoetzmann\n2 {NATHAN} vouch:{NATHAN_ID}x\n")

    assert trusted.level_of(NATHAN) == 0
    assert comment_authorised(
        rest_comment(login=NATHAN, uid=NATHAN_ID, association="MEMBER"), trusted
    ) is False


def test_B349_doctor_says_which_handles_depend_on_the_association(tmp_path, monkeypatch, capsys):
    """The invisible half, named in the report written to make it visible."""
    trust = f"3 jgoetzmann\n2 {NATHAN} vouch:{NATHAN_ID}\n"
    _, out, payload = doctor(tmp_path, monkeypatch, capsys, trust=trust)

    assert "@jgoetzmann" in out
    assert payload["trust"]["association_route"] == ["jgoetzmann"]
    assert NATHAN.lower() not in payload["trust"]["association_route"]


def test_B349_doctor_says_out_loud_when_it_could_not_check_access(tmp_path, monkeypatch, capsys):
    """Tier 0 can never read the collaborators endpoint, and printing nothing at all read as
    "checked, all fine"."""
    _, out, payload = doctor(tmp_path, monkeypatch, capsys, trust="3 jgoetzmann\n")

    assert payload["trust"]["access_checked"] is False
    assert "could not check" in out.lower()


# --------------------------------------------------------------------------------------
# B350 - the shipped file
# --------------------------------------------------------------------------------------


def test_B350_the_shipped_trust_file_has_no_entry_that_grants_nothing():
    """The compensating check for the severity move above: a refused, skipped or duplicated
    line fails the build, at review time, where the operator is standing."""
    trusted = load_trust(SHIPPED)

    assert trusted.malformed == (), f"refused lines in the shipped file: {trusted.malformed}"
    assert trusted.skipped == (), f"placeholder lines in the shipped file: {trusted.skipped}"
    assert trusted.duplicated == (), f"handles named twice: {trusted.duplicated}"
    assert trusted.implicit == (), f"level-less handles: {trusted.implicit}"


def test_B350_the_shipped_file_still_names_the_operator_and_the_vouched_maintainer():
    trusted = load_trust(SHIPPED)

    assert trusted.level_of("jgoetzmann") == 3
    assert trusted.level_of(NATHAN) == 2
    assert trusted.vouched_id(NATHAN) == NATHAN_ID


# --------------------------------------------------------------------------------------
# B352 - the footer names people on every surface, not only on replies
# --------------------------------------------------------------------------------------


def test_B352_the_github_store_keeps_the_levels_so_footers_name_handles():
    """`store/__init__.py` loads a whole `Trust` and `store/github.py` used to flatten it with
    `tuple(trusted)`, which discarded the levels. `links._who` then fell back to "level 2+" on
    every work item and proposal pull request, while replies -- which pass `ctx.trusted` --
    named the handles. The footer exists so a reader of a public thread can see whether their
    own comment would be honoured without first learning what a level is.

    Checked through the store, because a fake that passes a `Trust` would hide it.
    """
    from harness.store.github import GitHubStore
    from harness.clock import FrozenClock, parse_iso
    from harness import links

    trusted = parse_trust(f"3 jgoetzmann\n2 {NATHAN} vouch:{NATHAN_ID}\n")
    store = GitHubStore(
        object(),
        self_repo="jgoetzmann/bright-bots-harness",
        scratch=None,
        clock=FrozenClock(parse_iso("2026-09-01T12:00:00Z")),
        trusted=trusted,
    )

    body = links.work_item_body(
        SimpleNamespace(self_repo="jgoetzmann/bright-bots-harness", upstream_repo="o/p",
                        fork_repo="f/p"),
        external_ref="issue:1",
        upstream_number=1,
        trusted=store.trusted,
    )

    assert "@jgoetzmann" in body
    assert "level 2+" not in body, "the levels were flattened away again"


def test_B352_a_store_built_without_a_trust_file_still_renders():
    """The parameter is optional, and the footer degrades rather than raising."""
    from harness.store.github import GitHubStore
    from harness.clock import FrozenClock, parse_iso

    store = GitHubStore(
        object(), self_repo="a/b", scratch=None,
        clock=FrozenClock(parse_iso("2026-09-01T12:00:00Z")),
    )

    assert list(store.trusted) == []


# --------------------------------------------------------------------------------------
# B353 - a login that begins `vouch` is a login
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "handle,account",
    [
        ("vouched", 57787098),      # a real GitHub account
        ("vouchio", 30939362),      # another
        ("voucherifyio", 19346225),  # and another
        ("vouch", 45102943),        # the bare word is itself a registered login
    ],
)
def test_B353_a_login_beginning_vouch_is_read_as_a_handle(handle, account):
    """The first cut refused any handle whose first five letters were `vouch`, which is a rule
    about a substring rather than about a shape. These logins exist; the operator would have
    pasted a line that granted nothing, and `refusals` would have blamed the vouch -- the one
    well-formed half -- sending them to fix the wrong end of the line.
    """
    trusted = parse_trust(f"3 jgoetzmann\n2 {handle} vouch:{account}\n")

    assert trusted.level_of(handle) == 2
    assert trusted.vouched_id(handle) == account
    assert trusted.malformed == ()
    assert comment_authorised(
        rest_comment(login=handle, uid=account, association="NONE"), trusted
    ) is True


def test_B353_a_vouch_with_no_handle_in_front_of_it_is_still_refused():
    """The case the deleted rule existed for. `_HANDLE_RE` refuses it, because a login has no
    colon in it -- the rule that knows what a login is, rather than a rule about five letters."""
    trusted = parse_trust(f"3 jgoetzmann\n2 vouch:{NATHAN_ID}\n")

    assert len(trusted) == 1 and "jgoetzmann" in trusted
    assert trusted.malformed == (f"2 vouch:{NATHAN_ID}",)


def test_B353_the_refusal_names_the_handle_when_the_handle_is_what_is_wrong():
    """Two halves, two fixes. `carries a vouch that is not one` was said of a line whose vouch
    was perfect and whose handle was missing."""
    reasons = dict(trust_mod.refusals(parse_trust(f"2 vouch:{NATHAN_ID}\n")))

    assert "not a GitHub login" in reasons[f"2 vouch:{NATHAN_ID}"]


def test_B353_a_malformed_vouch_still_blames_the_vouch():
    """The reorder must not take the D68 wording from the line it was written for."""
    line = f"2 {NATHAN} vouch:{NATHAN_ID}x"

    reasons = dict(trust_mod.refusals(parse_trust(line + "\n")))

    assert "carries a vouch that is not one (D68)" == reasons[line]


# --------------------------------------------------------------------------------------
# B354 - the level column is ASCII digits, or it is not a level
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "level",
    [
        "٣",  # Arabic-Indic three: `isdigit()`, and `int()` reads it as 3
        "２",  # fullwidth two: the same
        "²",  # superscript two: `isdigit()`, and `int()` RAISES on it
    ],
)
def test_B354_a_digit_that_is_not_ascii_is_not_a_level(level):
    """Both halves were live defects. The first granted OPERATOR level from a character the
    handle rule beside it would have refused, with nothing in `malformed` for doctor to name.
    The second raised ValueError out of the parser."""
    trusted = parse_trust(f"3 jgoetzmann\n{level} nathan\n")

    assert trusted.level_of("nathan") == 0, "a level nobody can type is not a level"
    assert trusted.malformed == (f"{level} nathan",), "and it is named rather than dropped"
    assert trusted.level_of("jgoetzmann") == 3, "one bad line does not poison the file"


def test_B354_load_trust_returns_rather_than_raising_on_any_line(tmp_path):
    """`load_trust` is called from `build_context`, so an exception here is not a refused line
    but a dead command -- and `doctor` gates three spending workflows under `set -e`, so it is
    a dead fleet (#27). A superscript two did exactly that."""
    path = tmp_path / "trust.txt"
    path.write_text("² jack\n3 jgoetzmann\n", encoding="utf-8")

    trusted = load_trust(path)

    assert trusted.level_of("jgoetzmann") == 3, "the good line below it survived too"
    assert trusted.malformed == ("² jack",)


def test_B354_an_ordinary_level_is_untouched():
    """The guard adds ASCII-ness and nothing else: a long run of digits is still an attempt at
    a level, and still refused as out of range rather than read as a handle of digits."""
    assert parse_trust("3 jgoetzmann\n").level_of("jgoetzmann") == 3
    assert parse_trust("2 nathan\n").level_of("nathan") == 2
    assert parse_trust("9" * 21 + " someone\n").malformed == ("9" * 21 + " someone",)
    assert parse_trust("9" * 21 + "\n").malformed == ("9" * 21,)


# --------------------------------------------------------------------------------------
# B355 - a vouch names an account that can author a comment, or it names nobody
# --------------------------------------------------------------------------------------


def test_B355_trust_line_refuses_an_account_that_never_authors_a_comment(
    tmp_path, monkeypatch, capsys
):
    """An organisation resolves to a perfectly good id, and `comment.user.id` is the account
    that TYPED. Such a line parses, reads as vouched in `trust show`, and passes doctor's id
    check, while admitting nobody anywhere for ever -- the silent denial this command exists to
    prevent, manufactured by the command itself. `dependabot` is a real Organization account.
    """
    gh = UsersGh({"dependabot": 27347476}, kinds={"dependabot": "Organization"})
    code, out, err = run_trust(
        tmp_path, monkeypatch, capsys, ["line", "dependabot", "--level", "2"], gh=gh
    )

    assert code != 0
    assert out.strip() == "", f"a line was printed for an organisation: {out!r}"
    assert "Organization" in err and "not a person" in err


def test_B355_a_person_is_still_the_ordinary_case(tmp_path, monkeypatch, capsys):
    """The check must refuse only what GitHub actually called something else."""
    code, out, _ = run_trust(tmp_path, monkeypatch, capsys, ["line", NATHAN, "--level", "2"])

    assert code == 0 and out.strip() == f"2 {NATHAN} vouch:{NATHAN_ID}"


def test_B355_an_account_of_unknown_type_is_not_refused(tmp_path, monkeypatch, capsys):
    """Absent is not evidence. A payload with no `type` is unknown, and unknown must not turn
    into a refusal to add somebody."""
    class NoType(UsersGh):
        def get(self, path):
            data = super().get(path)
            data.pop("type")
            return data

    code, out, _ = run_trust(
        tmp_path, monkeypatch, capsys, ["line", NATHAN, "--level", "2"],
        gh=NoType({NATHAN: NATHAN_ID}),
    )

    assert code == 0 and out.strip() == f"2 {NATHAN} vouch:{NATHAN_ID}"


def test_B355_doctor_names_a_vouch_for_an_account_that_is_not_a_person(
    tmp_path, monkeypatch, capsys
):
    """`trust line` refuses to print such a line now, but one committed by hand before that
    check reads as healthy everywhere else: it parses, `trust show` calls it vouched, and the
    id matches the account it names."""
    gh = UsersGh({"acme": 4242}, kinds={"acme": "Organization"})
    code, out, payload = doctor(
        tmp_path, monkeypatch, capsys, trust="3 jgoetzmann\n2 acme vouch:4242\n", gh=gh
    )

    assert code == 0, "a bad trust line must not take the fleet down"
    named = [w for w in payload["warnings"] if "@acme" in w and "person" in w]
    assert named, f"nothing named the vouch that admits nobody: {payload['warnings']}"
    assert "Organization" in named[0]
    assert payload["trust"]["vouched"]["acme"]["type"] == "Organization"


# --------------------------------------------------------------------------------------
# B356 - the helper runs where the file is edited: no .env, no database
# --------------------------------------------------------------------------------------


def test_B356_trust_line_resolves_an_id_with_no_configuration_at_all(
    tmp_path, monkeypatch, capsys
):
    """One unauthenticated GET. Binding it to a `Context` made every way a machine can be
    unprovisioned a way to fail to add somebody: in a fresh checkout the command answered
    "could not start up to look @x up: no .env file at .env" and never asked GitHub anything.
    """
    code, out, err = run_trust(
        tmp_path, monkeypatch, capsys, ["line", NATHAN, "--level", "2"], configured=False
    )

    assert code == 0
    assert out.strip() == f"2 {NATHAN} vouch:{NATHAN_ID}"
    assert not (tmp_path / ".env").exists(), "the test itself must not have written one"


def test_B356_trust_show_reads_the_default_path_with_no_configuration(
    tmp_path, monkeypatch, capsys
):
    """It reads one local text file, so it asks for no more than that: the moment somebody
    wants this report is while editing that file."""
    text = f"3 jgoetzmann\n2 {NATHAN} vouch:{NATHAN_ID}\n"
    code, out, _ = run_trust(
        tmp_path, monkeypatch, capsys, ["show"], trust=text, configured=False
    )

    assert code == 0
    assert "@jgoetzmann" in out and f"@{NATHAN.lower()}" in out
    assert "default path" in out, "it has to say which file it fell back to"


def test_B356_neither_form_writes_a_database(tmp_path, monkeypatch, capsys):
    """COMMANDS.md says neither form writes anything. `build_context` opened the store, so the
    lookup created `harness.db` beside the checkout."""
    run_trust(tmp_path, monkeypatch, capsys, ["line", NATHAN, "--level", "2"])
    run_trust(tmp_path, monkeypatch, capsys, ["show"])

    assert list(tmp_path.rglob("*.db")) == [], "`harness trust` wrote a database"


# --------------------------------------------------------------------------------------
# B357 - it never prints a line the gate refuses
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("argv", [["--level", "2"], ["--level", "2", "--no-vouch"]])
def test_B357_every_line_it_prints_parses_back_to_the_grant_it_promises(
    tmp_path, monkeypatch, capsys, argv
):
    """The output is meant to be pasted unedited, so the command checks it against the parser
    rather than trusting the two to agree. They did not: a login beginning `vouch` was refused
    by the parser while this printed it with every appearance of success."""
    gh = UsersGh({"vouched": 57787098})
    code, out, _ = run_trust(
        tmp_path, monkeypatch, capsys, ["line", "vouched", *argv], gh=gh
    )
    trusted = parse_trust(out)

    assert code == 0 and out.strip()
    assert trusted.level_of("vouched") == 2, f"the gate refuses the line it printed: {out!r}"


def test_B357_a_line_the_gate_would_refuse_is_not_printed(tmp_path, monkeypatch, capsys):
    """The check itself, driven by a parser that refuses everything: stdout stays empty and the
    exit code is non-zero, because a line that looks finished and admits nobody is worse than
    no line at all."""
    monkeypatch.setattr(cli.trust_mod, "parse_trust", lambda text: trust_mod.Trust())
    code, out, err = run_trust(tmp_path, monkeypatch, capsys, ["line", NATHAN, "--level", "2"])

    assert code != 0
    assert out.strip() == ""
    assert "refusing to print" in err


# --------------------------------------------------------------------------------------
# B358 - one definition of an unfinished line
# --------------------------------------------------------------------------------------


def _identity(tmp_path, text):
    from harness.identity import Identity

    path = tmp_path / "trust.txt"
    path.write_text(text, encoding="utf-8")
    return Identity(SimpleNamespace(trust_file=path, self_repo="a/b", repo="c/d"), None)


def test_B358_any_placeholder_makes_the_trust_file_unready(tmp_path):
    """The file's own header warns that a placeholder fails `trust_file_ready()` and takes the
    real handles with it. It tested for two literal spellings, so every other placeholder
    passed -- the operator was warned about a consequence that would not happen."""
    ready = _identity(tmp_path, "3 jgoetzmann\n2 <NEW_MAINTAINER>\n").trust_file_ready()

    assert ready is False


def test_B358_the_legacy_placeholder_still_fails_it(tmp_path):
    """`NATHAN_HANDLE` without its brackets is not a placeholder by the parser's definition, so
    the literal check stays beside the general one."""
    assert _identity(tmp_path, "3 jgoetzmann\n2 <NATHAN_HANDLE>\n").trust_file_ready() is False
    assert _identity(tmp_path, "3 jgoetzmann\n2 NATHAN_HANDLE\n").trust_file_ready() is False


def test_B358_a_finished_file_is_ready(tmp_path):
    assert _identity(
        tmp_path, f"3 jgoetzmann\n2 {NATHAN} vouch:{NATHAN_ID}\n"
    ).trust_file_ready() is True


def test_B358_a_file_naming_one_real_handle_is_not_ready(tmp_path):
    """Counting accepted handles rather than non-comment lines: a refused line is not a handle,
    and reporting the setup step done on the strength of one is how it went unnoticed."""
    assert _identity(tmp_path, "3 jgoetzmann\n2 nathan@example.com\n").trust_file_ready() is False
