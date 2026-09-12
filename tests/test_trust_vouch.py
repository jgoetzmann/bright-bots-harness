"""D68: a trust.txt line may vouch for one exact GitHub account (B320-B331).

`2 BrightBoost-Tech vouch:193453438` -- a comment whose `user.login` is the handle AND whose
`user.id` is the vouched id passes the association half of the gate, whatever GitHub reports,
on every surface. Everything else is B131 as it was: the level still caps the verbs, a line
without a vouch still needs OWNER/MEMBER/COLLABORATOR, and a different account holding the login
is refused.

Comments here are built in the REST shape GitHub returns for issue comments, review comments
and reviews alike -- `user.login`, `user.id`, `author_association` -- because that is the shape
every production caller hands the gate.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import harness.__main__ as cli
from harness import keywords
from harness.errors import GitHubError, RateCeilingReached
from harness.ledger import Ledger
from harness.trust import comment_authorised, is_authorised, load_trust, parse_trust

REPO_ROOT = Path(__file__).resolve().parent.parent
SHIPPED = REPO_ROOT / ".harness" / "trust.txt"
SELF_REPO = "jgoetzmann/bright-bots-harness"
UPSTREAM = "Bright-Bots-Initiative/brightboost"
NATHAN = "BrightBoost-Tech"
#: `GET /users/BrightBoost-Tech` -> "id": 193453438 (read 2026-09-11).
NATHAN_ID = 193453438
OTHER_ID = 987654321
TRUST = f"3 jgoetzmann\n2 {NATHAN} vouch:{NATHAN_ID}\n2 unvouched-maintainer\n"


def rest_comment(*, login: str, uid: object, association: str, cid: int = 1,
                 body: str = "/harness ask what changed here") -> dict:
    """One comment as the REST API returns it (the fields the gate reads, plus the rest)."""
    return {
        "id": cid,
        "node_id": f"IC_{cid}",
        "user": {"login": login, "id": uid, "type": "User"},
        "author_association": association,
        "body": body,
        "created_at": "2026-09-01T11:30:00Z",
        "updated_at": "2026-09-01T11:30:00Z",
    }


def fresh_ledger() -> Ledger:
    ledger = Ledger.empty("2026-08-31T00:00:00Z")
    ledger.cursors["notifications_last_seen"] = "2026-09-01T00:00:00Z"
    return ledger


# --------------------------------------------------------------------------------------
# B320 - the line
# --------------------------------------------------------------------------------------


def test_B320_a_vouch_binds_the_handle_to_one_account_id():
    trusted = parse_trust(TRUST)

    assert trusted.level_of(NATHAN) == 2
    assert trusted.vouched_id(NATHAN) == NATHAN_ID
    assert trusted.vouched_id("@brightboost-tech") == NATHAN_ID, "the handle is case-folded"
    assert trusted.vouched_id("jgoetzmann") is None
    assert trusted.vouched_id("unvouched-maintainer") is None
    assert trusted.malformed == () and trusted.implicit == ()
    assert parse_trust("2 x VOUCH:5").vouched_id("x") == 5, "the word is case-insensitive too"


def test_B320_the_shipped_file_vouches_for_brightboost_tech_by_account_id():
    trusted = load_trust(SHIPPED)

    assert trusted.level_of(NATHAN) == 2
    assert trusted.vouched_id(NATHAN) == NATHAN_ID
    assert trusted.vouched_id("jgoetzmann") is None, "the operator is gated by B131 as before"
    assert trusted.malformed == (), f"the shipped file has a refused line: {trusted.malformed}"


# --------------------------------------------------------------------------------------
# B321 / B322 / B323 - who passes
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "association", ["CONTRIBUTOR", "NONE", "FIRST_TIME_CONTRIBUTOR", "", "MEMBER", "OWNER"]
)
def test_B321_the_vouched_account_passes_whatever_github_reports_its_association_as(association):
    """On the harness repository it is NONE (D30: not a collaborator); on the product
    repository its organisation membership is private, so it is CONTRIBUTOR. Both pass."""
    trusted = parse_trust(TRUST)
    comment = rest_comment(login=NATHAN, uid=NATHAN_ID, association=association)

    assert comment_authorised(comment, trusted) is True
    assert is_authorised(NATHAN, association, trusted, user_id=NATHAN_ID) is True
    # `harness ack` receives the id from the workflow as text.
    assert is_authorised(NATHAN, association, trusted, user_id=str(NATHAN_ID)) is True


@pytest.mark.parametrize("association", ["OWNER", "MEMBER", "COLLABORATOR", "CONTRIBUTOR", "NONE"])
@pytest.mark.parametrize(
    "uid", [OTHER_ID, None, 0, -NATHAN_ID, "", "abc", True, f"{NATHAN_ID}x", float(NATHAN_ID)]
)
def test_B322_the_same_login_on_any_other_account_is_denied(association, uid):
    """The login changed hands, or the id is missing or garbled. The line names one account,
    and a different one holding the name is not it -- even as OWNER, MEMBER or COLLABORATOR."""
    trusted = parse_trust(TRUST)

    assert comment_authorised(rest_comment(login=NATHAN, uid=uid, association=association),
                              trusted) is False
    assert is_authorised(NATHAN, association, trusted, user_id=uid) is False


def test_B322_the_vouched_id_under_another_login_is_not_the_line():
    """The vouch is login AND id together. A renamed account is refused under its new name
    (doctor says so), and the id does not lend itself to another listed handle."""
    trusted = parse_trust(TRUST)

    assert not comment_authorised(
        rest_comment(login="nathan-renamed", uid=NATHAN_ID, association="NONE"), trusted
    )
    assert not comment_authorised(
        rest_comment(login="unvouched-maintainer", uid=NATHAN_ID, association="CONTRIBUTOR"),
        trusted,
    )


@pytest.mark.parametrize("association", ["CONTRIBUTOR", "NONE", "FIRST_TIME_CONTRIBUTOR", ""])
def test_B323_a_line_without_a_vouch_is_B131_exactly(association):
    trusted = parse_trust(TRUST)
    unvouched = rest_comment(login="unvouched-maintainer", uid=12345, association=association)

    assert comment_authorised(unvouched, trusted) is False
    assert comment_authorised(
        rest_comment(login="jgoetzmann", uid=1, association=association), trusted
    ) is False
    assert comment_authorised(dict(unvouched, author_association="COLLABORATOR"), trusted)
    assert comment_authorised(
        rest_comment(login="jgoetzmann", uid=1, association="OWNER"), trusted
    )


# --------------------------------------------------------------------------------------
# B324 - the level still caps a vouched handle
# --------------------------------------------------------------------------------------


def test_B324_a_vouch_replaces_the_association_and_not_the_level():
    trusted = parse_trust(TRUST)
    body = (
        "/harness halt\n/harness resume\n/harness reject\n"
        "/harness work #5 --force\n/harness ask why\n"
    )
    comment = rest_comment(login=NATHAN, uid=NATHAN_ID, association="CONTRIBUTOR", body=body)

    commands = keywords.commands_from(
        comment, surface="delivery_pr", number=640, trusted=trusted, ledger=fresh_ledger()
    )

    assert is_authorised(NATHAN, "NONE", trusted, user_id=NATHAN_ID, min_level=3) is False
    refused = [c.args for c in commands if c.verb == "__denied__"]
    assert refused == [
        "/harness halt needs level 3; @BrightBoost-Tech is level 2",
        "/harness resume needs level 3; @BrightBoost-Tech is level 2",
        "/harness reject needs level 3; @BrightBoost-Tech is level 2",
    ]
    work = next(c for c in commands if c.verb == "work")
    assert work.force is False
    assert "`--force` needs level 3 and @BrightBoost-Tech is level 2" in work.note
    assert [c.verb for c in commands][-1] == "ask"
    assert {c.level for c in commands} == {2}


# --------------------------------------------------------------------------------------
# B325 - a malformed vouch refuses the whole line
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        f"2 {NATHAN} vouch:",
        f"2 {NATHAN} vouch:abc",
        f"2 {NATHAN} vouch:0",
        f"2 {NATHAN} vouch:-{NATHAN_ID}",
        f"2 {NATHAN} vouch:{NATHAN_ID}x",
        f"2 {NATHAN} vouch:{NATHAN_ID}.0",
        f"2 {NATHAN} vouch={NATHAN_ID}",
        f"2 {NATHAN} vouch {NATHAN_ID}",
        f"2 {NATHAN} vouch:1 vouch:{NATHAN_ID}",
        f"2 {NATHAN} vouch:{NATHAN_ID} vouch:{NATHAN_ID}",
        f"{NATHAN} vouch:{NATHAN_ID}x",
    ],
)
def test_B325_a_malformed_vouch_refuses_the_whole_line(line):
    """Read as "no vouch", the line would still grant level 2 -- to whoever GitHub calls a
    member. Refused, it grants nothing, and doctor names it."""
    trusted = parse_trust(f"3 jgoetzmann\n{line}\n")

    assert trusted.level_of(NATHAN) == 0
    assert trusted.vouched_id(NATHAN) is None
    assert trusted.malformed == (line,)
    assert trusted.level_of("jgoetzmann") == 3, "one bad line does not poison the file"
    for association in ("MEMBER", "CONTRIBUTOR"):
        assert not comment_authorised(
            rest_comment(login=NATHAN, uid=NATHAN_ID, association=association), trusted
        )


def test_B325_two_lines_vouching_for_different_accounts_refuse_the_handle():
    first, second = f"2 {NATHAN} vouch:{NATHAN_ID}", f"1 {NATHAN} vouch:{OTHER_ID}"
    trusted = parse_trust(f"3 jgoetzmann\n{first}\n{second}\n")

    assert NATHAN not in trusted
    assert trusted.vouched_id(NATHAN) is None
    assert trusted.malformed == (first, second)
    assert parse_trust(f"{first}\n3 {NATHAN} vouch:{NATHAN_ID}\n").vouched_id(NATHAN) == NATHAN_ID


def test_B325_a_vouch_with_no_handle_is_refused_rather_than_read_as_one():
    trusted = parse_trust(f"2 vouch:{NATHAN_ID}\n")

    assert len(trusted) == 0
    assert trusted.malformed == (f"2 vouch:{NATHAN_ID}",)


# --------------------------------------------------------------------------------------
# B331 - a vouched handle's lines must agree; a bare line beside a vouch refuses both
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("bare", [f"3 {NATHAN}", f"2 {NATHAN}", f"1 {NATHAN}", NATHAN])
@pytest.mark.parametrize("vouched_first", [True, False])
def test_B331_a_vouched_handle_named_again_without_the_vouch_is_refused_entirely(
    bare, vouched_first
):
    """Merged, `3 x` and `2 x vouch:<id>` gave the vouched account level 3 with no association:
    the level from one line and the association waiver from the other. Neither line grants that
    alone -- the bare line needs OWNER/MEMBER/COLLABORATOR, the vouched one caps at 2. Which
    account the operator meant is the question the vouch settles, so a disagreement about it
    refuses every line naming the handle, through the sweep's own gate."""
    vouched = f"2 {NATHAN} vouch:{NATHAN_ID}"
    pair = [vouched, bare] if vouched_first else [bare, vouched]
    trusted = parse_trust("3 jgoetzmann\n" + "\n".join(pair) + "\n")

    assert NATHAN not in trusted
    assert trusted.vouched_id(NATHAN) is None
    assert trusted.malformed == tuple(pair)
    assert trusted.conflicted == tuple(pair)
    assert trusted.implicit == ()
    assert trusted.level_of("jgoetzmann") == 3, "one refused handle does not poison the file"
    for association in ("OWNER", "COLLABORATOR", "CONTRIBUTOR"):
        for uid in (NATHAN_ID, OTHER_ID):
            comment = rest_comment(
                login=NATHAN, uid=uid, association=association, body="/harness halt"
            )
            heard = keywords.commands_from(
                comment, surface="issue", number=4, trusted=trusted, ledger=fresh_ledger()
            )
            assert heard == [], (association, uid)


def test_B331_lines_that_agree_about_the_vouch_still_merge_to_the_highest_level():
    """The refusal is for disagreement, not repetition: the same vouch twice is one account."""
    trusted = parse_trust(f"2 {NATHAN} vouch:{NATHAN_ID}\n3 {NATHAN} vouch:{NATHAN_ID}\n")

    assert trusted.level_of(NATHAN) == 3
    assert trusted.vouched_id(NATHAN) == NATHAN_ID
    assert trusted.malformed == trusted.conflicted == ()


# --------------------------------------------------------------------------------------
# B326 - `harness ack` and the sweep agree
# --------------------------------------------------------------------------------------


def _env(tmp_path: Path, trust: str) -> Path:
    trust_file = tmp_path / "trust.txt"
    trust_file.write_text(trust, encoding="utf-8")
    over = {
        "BACKEND": "fake",
        "PERMISSION_TIER": "0",
        "STORE_BACKEND": "sqlite",
        "TRUST_FILE": str(trust_file),
        "DB_PATH": str(tmp_path / "h.db"),
        "RUNS_DIR": str(tmp_path / "runs"),
        "PACKAGES_DIR": str(tmp_path / "pkgs"),
    }
    out = []
    for line in (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        key = line.split("=", 1)[0].strip()
        out.append(f"{key}={over.pop(key)}" if key in over else line)
    out += [f"{k}={v}" for k, v in over.items()]
    path = tmp_path / ".env"
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return path


def run_ack(tmp_path, capsys, body, *, actor, actor_id, association) -> dict:
    """`harness ack` exactly as ack.yml calls it."""
    body_file = tmp_path / "comment.txt"
    body_file.write_text(body, encoding="utf-8")
    argv = ["--config", str(_env(tmp_path, TRUST)), "ack", "--body-file", str(body_file),
            "--actor", actor, "--association", association]
    if actor_id is not None:
        argv += ["--actor-id", actor_id]
    assert cli.main(argv) == 0
    return json.loads(capsys.readouterr().out)


@pytest.mark.parametrize(
    "login,uid,association,heard",
    [
        (NATHAN, NATHAN_ID, "CONTRIBUTOR", True),
        (NATHAN, NATHAN_ID, "NONE", True),
        (NATHAN, OTHER_ID, "MEMBER", False),
        (NATHAN, "", "CONTRIBUTOR", False),
        (NATHAN, None, "MEMBER", False),
        ("unvouched-maintainer", 55, "CONTRIBUTOR", False),
        ("unvouched-maintainer", 55, "MEMBER", True),
        ("jgoetzmann", 1, "OWNER", True),
        ("mallory", NATHAN_ID, "NONE", False),
    ],
)
def test_B326_ack_and_the_sweep_agree_about_who_is_heard(
    tmp_path, capsys, login, uid, association, heard
):
    """A disagreement is public either way: an ack for a comment the sweep ignores tells a
    stranger they were heard, and a missing ack for one it answers looks like the harness is
    off. `None` is an older ack.yml that passes no id at all."""
    body = "/harness ask what does the registry do"
    out = run_ack(tmp_path, capsys, body, actor=login,
                  actor_id=None if uid is None else str(uid), association=association)
    swept = keywords.commands_from(
        rest_comment(login=login, uid=uid, association=association, body=body),
        surface="product_issue", number=633, trusted=load_trust(tmp_path / "trust.txt"),
        ledger=fresh_ledger(),
    )

    assert out["react"] is heard
    assert [c.verb for c in swept] == (["ask"] if heard else [])


def test_B326_ack_yml_hands_the_commenters_account_id_to_harness_ack():
    text = (REPO_ROOT / ".github" / "workflows" / "ack.yml").read_text(encoding="utf-8")
    step = text.split("- name: Decide what to say", 1)[1].split("- name:", 1)[0]

    assert "ACTOR_ID: ${{ github.event.comment.user.id }}" in step
    assert '--actor-id "$ACTOR_ID"' in step


# --------------------------------------------------------------------------------------
# B327 - the sweep, on both repositories
# --------------------------------------------------------------------------------------


def _thread(repo: str, number: int, kind: str, thread_id: str) -> dict:
    """A GET /notifications thread in GitHub's shape (subject.url carries the number)."""
    path = "pulls" if kind == "PullRequest" else "issues"
    return {
        "id": thread_id,
        "unread": True,
        "reason": "comment",
        "updated_at": "2026-09-01T11:30:00Z",
        "subject": {"title": f"thread {number}", "type": kind,
                    "url": f"https://api.github.com/repos/{repo}/{path}/{number}"},
        "repository": {"full_name": repo},
    }


class SweepGh:
    """The read surface `keywords.sweep` uses, served from dicts. Anything else is recorded and
    answers [] so a write attempt shows up rather than raising."""

    def __init__(self, threads, comments, review_comments):
        self.threads = list(threads)
        self.comments = dict(comments)
        self.review_comments = dict(review_comments)
        self.calls: list[str] = []

    def notifications(self, since):
        self.calls.append("notifications")
        return list(self.threads)

    def issue_comments(self, repo, number):
        self.calls.append("issue_comments")
        return [dict(c) for c in self.comments.get((repo, int(number)), [])]

    def pull_review_comments(self, repo, number):
        self.calls.append("pull_review_comments")
        return [dict(c) for c in self.review_comments.get((repo, int(number)), [])]

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append(name)
            return []

        return record


def test_B327_the_sweep_hears_the_vouched_account_on_both_repos_and_nobody_else_by_its_name():
    def real(cid, association):
        return rest_comment(login=NATHAN, uid=NATHAN_ID, association=association, cid=cid,
                            body="/harness ask what is left on this")

    def impostor(cid, association):
        return rest_comment(login=NATHAN, uid=OTHER_ID, association=association, cid=cid,
                            body="/harness stop")

    gh = SweepGh(
        threads=[
            _thread(UPSTREAM, 640, "PullRequest", "t1"),
            _thread(UPSTREAM, 633, "Issue", "t2"),
            _thread(SELF_REPO, 12, "PullRequest", "t3"),
        ],
        comments={
            (SELF_REPO, 1): [real(1, "NONE"), impostor(2, "NONE")],
            (UPSTREAM, 640): [real(3, "CONTRIBUTOR"), impostor(4, "MEMBER")],
            (UPSTREAM, 633): [real(5, "CONTRIBUTOR")],
            (SELF_REPO, 12): [real(6, "NONE")],
        },
        review_comments={
            (UPSTREAM, 640): [dict(real(7, "CONTRIBUTOR"), path="src/App.tsx", line=3)],
        },
    )

    commands = keywords.sweep(
        gh, ledger=fresh_ledger(), trusted=parse_trust(TRUST), now_iso="2026-09-01T12:00:00Z",
        self_repo=SELF_REPO, upstream_repo=UPSTREAM, inbox_issue=1, machine="jgoetzmann-bot",
    )

    assert sorted((c.surface, c.number, c.comment_id) for c in commands) == sorted([
        ("inbox", 1, "IC_1"),
        ("delivery_pr", 640, "IC_3"),
        ("delivery_pr", 640, "IC_7"),
        ("product_issue", 633, "IC_5"),
        ("proposal_pr", 12, "IC_6"),
    ])
    assert {(c.actor, c.verb, c.level) for c in commands} == {(NATHAN, "ask", 2)}


# --------------------------------------------------------------------------------------
# B329 - doctor
# --------------------------------------------------------------------------------------


class UsersGh:
    """`GET /users/<login>` as GitHub answers it; every other read refused the way GitHub
    refuses the collaborators endpoint to an account without push access."""

    def __init__(self, users: dict[str, int] | None = None, fail: Exception | None = None):
        self.users = dict(users or {})
        self.fail = fail
        self.asked: list[str] = []
        self.dry_run = False

    def get(self, path):
        self.asked.append(path)
        url = f"https://api.github.com{path}"
        if path.startswith("/users/"):
            if self.fail is not None:
                raise self.fail
            login = path.rsplit("/", 1)[1]
            if login.lower() not in self.users:
                raise GitHubError(f'github returned 404 for {url}: {{"message":"Not Found"}}')
            return {"login": login, "id": self.users[login.lower()], "type": "User"}
        raise GitHubError(f'github returned 403 for {url}: {{"message":"Must have push access"}}')

    def notifications(self, since):  # pragma: no cover - tier 0 never asks
        raise GitHubError("notifications need a token")


def doctor(tmp_path, monkeypatch, capsys, *, trust=TRUST, gh=None) -> tuple[int, str, dict]:
    """`harness doctor` twice -- text and --json -- over `trust`, with `gh` as its client."""
    from conftest import write_env

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".harness").mkdir(exist_ok=True)
    (tmp_path / ".harness" / "trust.txt").write_text(trust, encoding="utf-8")
    env = write_env(tmp_path / ".env", TRUST_FILE=".harness/trust.txt", SELF_REPO=SELF_REPO)
    client = gh if gh is not None else UsersGh({NATHAN.lower(): NATHAN_ID})
    monkeypatch.setattr(cli, "_context", lambda config, args, run_id: SimpleNamespace(gh=client))
    code = cli.main(["--config", str(env), "doctor"])
    out = capsys.readouterr().out
    assert cli.main(["--config", str(env), "--json", "doctor"]) == code
    return code, out, json.loads(capsys.readouterr().out)


def test_B329_doctor_lists_the_vouch_and_says_nothing_more_when_the_id_matches(
    tmp_path, monkeypatch, capsys
):
    code, out, payload = doctor(tmp_path, monkeypatch, capsys)

    assert f"vouched for one account (D68; association not required): @brightboost-tech = " \
           f"account {NATHAN_ID}" in out
    assert payload["trust"]["vouched"] == {
        "brightboost-tech": {"id": NATHAN_ID, "checked": True, "actual": NATHAN_ID}
    }
    assert not [w for w in payload["warnings"] if "vouch" in w]


def test_B329_a_login_now_held_by_another_account_is_a_warning_and_never_a_problem(
    tmp_path, monkeypatch, capsys
):
    """A doctor PROBLEM exits 3, and doctor gates every spending workflow (#27). A vouch gone
    stale stops one person's comments -- which the vouch is already doing, safely."""
    baseline, _, _ = doctor(tmp_path, monkeypatch, capsys)
    code, out, payload = doctor(
        tmp_path, monkeypatch, capsys, gh=UsersGh({NATHAN.lower(): OTHER_ID})
    )

    assert code == baseline
    assert "warnings (the harness still runs)" in out
    assert f"@brightboost-tech is account {OTHER_ID} today" in out
    assert [w for w in payload["warnings"] if "vouches for @brightboost-tech" in w]
    assert not [p for p in payload["problems"] if "vouch" in p]
    assert payload["trust"]["vouched"]["brightboost-tech"]["actual"] == OTHER_ID


def test_B329_a_login_that_no_longer_exists_is_a_warning(tmp_path, monkeypatch, capsys):
    baseline, _, _ = doctor(tmp_path, monkeypatch, capsys)
    code, out, payload = doctor(tmp_path, monkeypatch, capsys, gh=UsersGh({}))

    assert code == baseline
    assert "GitHub has no account by that login now" in out
    assert not [p for p in payload["problems"] if "vouch" in p]


@pytest.mark.parametrize(
    "fail",
    [
        RateCeilingReached("unauthenticated reads are capped at 60/hour per address"),
        GitHubError("github returned 502 for https://api.github.com/users/x: bad gateway"),
    ],
)
def test_B329_doctor_says_nothing_when_it_cannot_look(tmp_path, monkeypatch, capsys, fail):
    """"I could not check" is not a finding, and must not read as one."""
    _, out, payload = doctor(tmp_path, monkeypatch, capsys, gh=UsersGh(fail=fail))

    assert not [w for w in payload["warnings"] if "vouch" in w]
    assert payload["trust"]["vouched"]["brightboost-tech"]["checked"] is False


def test_B329_a_vouched_handle_is_not_reported_as_having_no_access(tmp_path, monkeypatch, capsys):
    """It has no access here on purpose (D30), and the vouch is what makes that fine."""
    monkeypatch.setattr(cli, "_doctor_trust_access",
                        lambda config, args, trusted, payload: ("brightboost-tech", "someone"))
    _, out, payload = doctor(tmp_path, monkeypatch, capsys)

    stranded = [w for w in payload["warnings"] if w.startswith("no access to")]
    assert len(stranded) == 1 and "@someone" in stranded[0]
    assert "brightboost-tech" not in stranded[0].lower()


def test_B329_doctor_names_a_malformed_vouch_like_a_malformed_level(tmp_path, monkeypatch, capsys):
    """B269's rule, applied to the vouch: refused, and said out loud."""
    line = f"2 {NATHAN} vouch:{NATHAN_ID}x"
    code, out, payload = doctor(tmp_path, monkeypatch, capsys, trust=f"3 jgoetzmann\n{line}\n")

    assert code == 3
    assert f"trust file line carries a vouch that is not one (D68) and was refused: {line!r}" in (
        payload["problems"]
    )


@pytest.mark.parametrize(
    "pair",
    [
        (f"3 {NATHAN}", f"2 {NATHAN} vouch:{NATHAN_ID}"),
        (f"2 {NATHAN} vouch:{NATHAN_ID}", f"2 {NATHAN} vouch:{OTHER_ID}"),
    ],
)
def test_B331_doctor_names_every_line_of_a_disagreement_as_one(tmp_path, monkeypatch, capsys, pair):
    """Each line on its own is well formed, so "not a level" or "not a vouch" would send the
    operator looking for a typo that is not there. The finding is the disagreement."""
    code, out, payload = doctor(
        tmp_path, monkeypatch, capsys, trust="3 jgoetzmann\n" + "\n".join(pair) + "\n"
    )

    assert code == 3
    for line in pair:
        assert (
            "trust file line disagrees with another line about which account its handle is (D68)"
            f" and was refused: {line!r}"
        ) in payload["problems"]
    assert not any("is not a level" in p for p in payload["problems"])


# --------------------------------------------------------------------------------------
# B330 - one gate
# --------------------------------------------------------------------------------------


_READS_ASSOCIATION = re.compile(r"""(?:get\(\s*|\[\s*)["']author_association["']""")
_CALLS_THE_INNER_JUDGE = re.compile(r"\bis_authorised\s*\(")


def test_B330_no_module_but_trust_py_reads_a_comments_author_association():
    """The vouch rule lives in `trust.comment_authorised`. A second place that reads the
    association and decides for itself is a place the vouch can be forgotten -- which is how
    revise came to flatten the trust file into bare handles and drop every vouch."""
    offenders = []
    for path in sorted((REPO_ROOT / "harness").rglob("*.py")):
        if path.name == "trust.py":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if _READS_ASSOCIATION.search(code) or _CALLS_THE_INNER_JUDGE.search(code):
                offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{number}: {line}")

    assert offenders == [], "\n".join(offenders)
