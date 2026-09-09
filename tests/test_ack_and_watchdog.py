"""B293/B294: saying "working on it" in seconds, and noticing a schedule that stopped firing.

Both exist for the same failure, seen from opposite ends. From a thread, a harness that is
thinking and a harness that is off look identical, and the second is the one people act on —
they comment again, or they stop using it. `ack` says which it is before the work starts;
`watchdog` catches the case where the answer really is "off", and nothing failed to say so.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from harness import links
from harness.keywords import VERBS

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def _wf(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------------------
# The wait table
# --------------------------------------------------------------------------------------


def test_every_verb_has_an_estimate_and_none_is_invented():
    """The same drift guard `VERB_HELP` has. A verb with no estimate acknowledges nothing, which
    is the failure this feature exists to fix; an estimate for a verb that does not exist is a
    promise about something nobody can type."""
    assert set(links.VERB_WAIT) == set(VERBS), (
        f"VERB_WAIT and VERBS disagree: {set(links.VERB_WAIT) ^ set(VERBS)}"
    )


def test_every_estimate_is_one_the_slow_test_can_classify():
    """`is_slow` compares the wait against a fixed set. A wait phrased any other way silently
    reads as fast, so a twenty-minute audit would acknowledge nothing at all."""
    fast = {"seconds", "under a minute"}
    for verb, (wait, doing) in links.VERB_WAIT.items():
        assert wait in links.SLOW_WAITS or wait in fast, f"{verb}: unclassifiable wait {wait!r}"
        assert doing and doing[0].islower(), f"{verb}: `doing` reads mid-sentence"


def test_the_verbs_that_clone_or_call_a_model_are_the_slow_ones():
    """Not a restatement of the table: this is the CLAIM the table has to encode. `ask` clones
    the product repository, `audit` reads it through a lens, `revise` re-implements and re-runs
    every gate. If one of those ever reads as fast, somebody is being told to expect an answer
    in seconds that takes twenty minutes."""
    for verb in ("ask", "audit", "revise", "rebase", "split"):
        assert links.is_slow(verb), f"{verb} calls a model; it cannot be 'seconds'"
    for verb in ("status", "go", "stop", "promote", "halt", "resume"):
        assert not links.is_slow(verb), f"{verb} only touches the store; it must not be slow"


# --------------------------------------------------------------------------------------
# What it says, and when it says nothing
# --------------------------------------------------------------------------------------


def test_an_acknowledgement_names_each_verb_and_its_wait():
    text = links.acknowledgement(["ask", "work"])

    assert "Working on it" in text
    assert "/harness ask" in text and "a couple of minutes" in text
    assert "/harness work" in text and "under a minute" in text
    assert "cloning the product repository" in text, "it says what it is doing, not just how long"


def test_a_comment_of_only_fast_verbs_is_not_acknowledged():
    """An acknowledgement that lands two seconds before the answer it acknowledges has told the
    reader nothing and cost them a notification. The eyes reaction covers that case."""
    assert links.acknowledgement(["status"]) == ""
    assert links.acknowledgement(["status", "go", "promote"]) == ""


def test_one_slow_verb_is_enough_to_acknowledge_the_whole_comment():
    """And the fast ones are still listed, because the reader asked for them and wants to know
    they were seen — the point of listing is completeness, not just the long pole."""
    text = links.acknowledgement(["status", "audit"])

    assert text
    assert "/harness status" in text and "/harness audit" in text


def test_a_repeated_verb_is_named_once():
    """Three `/harness status` lines in one comment must not produce three identical bullets."""
    text = links.acknowledgement(["audit", "status", "status", "status"])

    bullets = [ln for ln in text.splitlines() if ln.startswith("- `/harness status`")]
    assert len(bullets) == 1, text


def test_an_unknown_verb_is_left_out_rather_than_guessed_at():
    assert links.acknowledgement(["frobnicate"]) == ""
    text = links.acknowledgement(["frobnicate", "audit"])
    assert "frobnicate" not in text and "/harness audit" in text


def test_the_acknowledgement_says_the_answer_is_a_separate_comment():
    """Otherwise the reader watches this comment for an edit that never comes."""
    text = links.acknowledgement(["ask"])

    assert "new comment" in text
    assert "/harness status" in text, "and it says what to do if the answer never arrives"


# --------------------------------------------------------------------------------------
# `harness ack` — the gate, exercised through the CLI the workflow actually calls
# --------------------------------------------------------------------------------------


def _run_ack(tmp_path, capsys, body: str, actor="jgoetzmann", association="OWNER") -> dict:
    """`harness ack` through the CLI the workflow actually calls, as the dict it prints."""
    import json

    from harness.__main__ import main

    body_file = tmp_path / "comment.txt"
    body_file.write_text(body, encoding="utf-8")
    code = main([
        "--config", str(_env(tmp_path)), "ack",
        "--body-file", str(body_file), "--actor", actor, "--association", association,
    ])
    assert code == 0, "ack must never fail the run it precedes"
    out = capsys.readouterr().out
    # Always exactly one JSON object, on every path. The workflow parses this with a `{}`
    # fallback, so a run that printed nothing would silently mean "say nothing" -- which is
    # right, but it would hide a crash rather than report one.
    return json.loads(out)


def _env(tmp_path) -> Path:
    trust = tmp_path / "trust.txt"
    trust.write_text("3 jgoetzmann\n2 nathan\n", encoding="utf-8")
    src = (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    over = {
        "BACKEND": "fake",
        "PERMISSION_TIER": "0",
        "STORE_BACKEND": "sqlite",
        "TRUST_FILE": str(trust),
        "DB_PATH": str(tmp_path / "h.db"),
        "RUNS_DIR": str(tmp_path / "runs"),
        "PACKAGES_DIR": str(tmp_path / "pkgs"),
    }
    out = []
    for line in src:
        key = line.split("=", 1)[0].strip()
        out.append(f"{key}={over.pop(key)}" if key in over else line)
    out += [f"{k}={v}" for k, v in over.items()]
    path = tmp_path / ".env"
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return path


def test_ack_acknowledges_a_trusted_slow_command(tmp_path, capsys):
    out = _run_ack(tmp_path, capsys, "/harness ask what does the registry do")

    assert out["react"] is True
    assert "Working on it" in out["comment"] and "/harness ask" in out["comment"]


def test_ack_reacts_but_says_nothing_for_a_fast_command(tmp_path, capsys):
    """The two decisions are separate on purpose. The sweep IS going to act, so the reaction is
    earned; the answer arrives about as fast as a comment would, so the comment is not."""
    out = _run_ack(tmp_path, capsys, "/harness-status")

    assert out["react"] is True
    assert out["comment"] == ""


def test_ack_never_reacts_to_the_machine_account(tmp_path, capsys):
    """Every reply the harness writes carries a pointer that mentions `/harness`, so without
    this it would react to its own answers on every thread, for ever. The workflow's `if:`
    cannot catch it either -- the machine account is an ordinary user, not a `Bot` type."""
    out = _run_ack(tmp_path, capsys, "/harness ask x", actor="jgoetzmann-bot")

    assert out == {"react": False, "comment": ""}


def test_ack_says_nothing_to_an_untrusted_commenter(tmp_path, capsys):
    """A public repository. Acknowledging a comment the sweep will then ignore is the worst of
    both: it tells somebody they were heard when they were not, and it does so where everybody
    can see it."""
    assert _run_ack(tmp_path, capsys, "/harness ask x", actor="mallory",
                    association="NONE") == {"react": False, "comment": ""}


def test_ack_applies_the_association_half_of_the_gate_too(tmp_path, capsys):
    """B131 is BOTH halves. A handle in the trust file whom GitHub does not vouch for on this
    repository is refused by the sweep, so the acknowledgement must refuse it identically."""
    assert _run_ack(tmp_path, capsys, "/harness ask x",
                    association="CONTRIBUTOR") == {"react": False, "comment": ""}


def test_ack_uses_the_same_parser_as_the_sweep(tmp_path, capsys):
    """Not a second, drifting copy of it. A fenced block is the case that proves it: the docs
    ship a paste-me block, and an acknowledgement that read it would announce work the sweep is
    never going to do."""
    body = "look:\n```\n/harness-audit accessibility\n```\n"
    assert _run_ack(tmp_path, capsys, body) == {"react": False, "comment": ""}


def test_ack_reads_the_hyphenated_form_and_the_aliases(tmp_path, capsys):
    out = _run_ack(tmp_path, capsys, "/harness-fix the null check moved")

    assert "/harness revise" in out["comment"], "the alias resolves before the estimate"


def test_ack_is_silent_rather_than_loud_when_it_cannot_tell(tmp_path, capsys):
    """Every path that cannot confirm what will happen prints nothing and exits 0. An
    acknowledgement that can break the run it precedes is a worse bargain than no
    acknowledgement at all."""
    silent = {"react": False, "comment": ""}
    assert _run_ack(tmp_path, capsys, "no commands here at all") == silent
    assert _run_ack(tmp_path, capsys, "") == silent


def test_ack_survives_a_body_file_that_is_not_there(tmp_path, capsys):
    """And still prints the object, so the workflow reads a decision rather than a blank."""
    import json

    from harness.__main__ import main

    code = main([
        "--config", str(_env(tmp_path)), "ack",
        "--body-file", str(tmp_path / "nope.txt"), "--actor", "jgoetzmann",
        "--association", "OWNER",
    ])

    assert code == 0
    assert json.loads(capsys.readouterr().out) == {"react": False, "comment": ""}


# --------------------------------------------------------------------------------------
# ack.yml — the properties that make it fast, and safe on a public repository
# --------------------------------------------------------------------------------------


def test_ack_does_not_share_the_ledger_lock():
    """The whole point. `harness-ledger` serialises every workflow that writes state, so a job
    in that group can sit queued behind a twenty-minute implement run — and an acknowledgement
    that waits twenty minutes is not one."""
    text = _wf("ack.yml")

    groups = re.findall(r"^  group: (.+)$", text, re.M)
    assert groups, "ack.yml declares no concurrency group at all"
    assert "harness-ledger" not in groups
    assert groups == ["ack-${{ github.event.comment.id }}"]


def test_ack_never_interpolates_the_comment_body_into_a_shell():
    """`${{ }}` is substituted before bash sees the line, so a backtick in a stranger's comment
    becomes a command on the runner. The body reaches python through the environment, and the
    only `${{ }}` uses left are GitHub's own fields."""
    text = _wf("ack.yml")

    for line in text.splitlines():
        if "github.event.comment.body" in line and "contains(" not in line:
            assert line.strip().startswith("COMMENT_BODY:"), (
                f"the comment body is interpolated outside the env block: {line.strip()}"
            )
    assert 'os.environ["COMMENT_BODY"]' in text


def test_ack_cannot_spend_or_authenticate():
    """It runs before the gate that decides whether anything may be spent, so it must not be
    able to spend. Tier 0, fake backend, and no secret beyond the run's own GITHUB_TOKEN."""
    text = _wf("ack.yml")

    assert '"PERMISSION_TIER": "0"' in text
    assert '"BACKEND": "fake"' in text
    live = [ln for ln in text.splitlines()
            if "secrets." in ln and not ln.lstrip().startswith("#")]
    assert live == [], f"no secret belongs in a workflow that only says hello: {live}"


def test_the_reaction_is_gated_on_the_harnesss_own_decision():
    """Not on `always()`, and not on the workflow guessing. The machine account is an ordinary
    user rather than a `Bot` type, so a `user.type` test in the `if:` would not see it -- and
    every reply the harness writes mentions `/harness` in its pointer, so the workflow would
    react to its own answers on every thread. `harness ack` refuses it by login."""
    text = _wf("ack.yml")

    assert "if: steps.ack.outputs.react == 'true'" in text
    always = [ln for ln in text.splitlines() if "if: always()" in ln]
    assert always == [], f"a step that always runs will react to the harness itself: {always}"


def test_ack_installs_nothing():
    """The harness is stdlib-only, so PYTHONPATH is enough. `pip install -e .` is about twenty
    seconds, which is most of the budget for a thing whose only job is to be quick."""
    text = _wf("ack.yml")

    live = [ln for ln in text.splitlines()
            if "pip install" in ln and not ln.lstrip().startswith("#")]
    assert live == [], f"ack.yml installs something: {live}"
    assert "PYTHONPATH: ." in text


# --------------------------------------------------------------------------------------
# watchdog.yml — the run that never started
# --------------------------------------------------------------------------------------


def test_the_watchdog_runs_on_a_different_schedule_from_the_thing_it_watches():
    """A watchdog sharing a cron expression with its subject is watching itself fail. Different
    hour and different minute, so one scheduler wedge is less likely to take both."""
    watch = re.search(r'- cron: "([^"]+)"', _wf("watchdog.yml")).group(1)
    feedback = re.search(r'- cron: "([^"]+)"', _wf("feedback.yml")).group(1)

    assert watch != feedback
    assert watch.split()[0] != feedback.split()[0], "same minute is the same queue"
    assert watch.split()[1] != feedback.split()[1], "same hours is the same wedge"


def test_the_watchdog_does_not_sweep_at_weekends():
    """`feedback` is scheduled `1-5` on purpose, and the docs say a Friday-evening comment waits
    for Monday. A watchdog that dispatched all weekend would quietly change that policy — and it
    would look like a bug fix, which is how policy changes get in."""
    text = _wf("watchdog.yml")

    assert "getUTCDay() >= 1 && now.getUTCDay() <= 5" in text
    assert "if (!weekday)" in text


def test_the_watchdog_treats_a_failed_run_as_proof_of_life():
    """It is looking for the ABSENCE of runs. ops.yml owns failures, and a watchdog that also
    re-dispatched on failure would fight it — each re-running what the other just started."""
    text = _wf("watchdog.yml")

    assert "r.status === 'completed' || r.status === 'in_progress'" in text
    live = [ln for ln in text.splitlines()
            if "conclusion === 'failure'" in ln and not ln.lstrip().startswith("//")]
    assert live == [], f"the watchdog must not react to failures; ops.yml owns those: {live}"


def test_the_watchdog_files_an_issue_on_a_dead_schedule_not_a_late_one():
    """One miss is a hiccup the dispatch already fixed, and an issue per hiccup is how an ops
    list becomes noise. The evidence for a stoppage is specific and different: no run triggered
    by `schedule` for half a day. A dispatch of the watchdog's own resets the first clock and
    never that one, so the two cannot be confused."""
    text = _wf("watchdog.yml")

    assert "r.event === 'schedule'" in text
    assert "DEAD_HOURS" in text
    assert "scheduledAgeHours < DEAD_HOURS" in text


def test_the_watchdog_names_the_sixty_day_rule():
    """The two causes need different actions and only one self-corrects. An operator who does not
    know GitHub disables schedules after 60 days without a push will wait for a recovery that is
    never coming."""
    text = _wf("watchdog.yml")

    assert "60 days" in text
    assert "Actions tab" in text


def test_the_watchdog_labels_its_issue_the_way_ops_does():
    """So it shows up in the same list, and so `ops recovered` and any human filter find it."""
    assert "labels: ['kind:ops']" in _wf("watchdog.yml")


# --------------------------------------------------------------------------------------
# ops.yml — the retry cap
# --------------------------------------------------------------------------------------


def test_transient_failures_retry_more_than_once():
    """One retry was not enough: the failures that actually happen are network and registry
    blips, and those cluster — a single retry lands inside the same bad minute often enough to
    be no retry at all."""
    text = _wf("ops.yml")

    assert "const MAX_ATTEMPTS = 3;" in text
    assert "attempt < MAX_ATTEMPTS" in text


def test_the_retry_cap_is_counted_per_run_not_per_issue():
    """The old cap was a label on the ops issue, so one transient failure in August spent the
    retry for every failure of that workflow afterwards, until a human closed the issue."""
    text = _wf("ops.yml")

    gates = [ln for ln in text.splitlines()
             if "alreadyRetried" in ln and not ln.lstrip().startswith("//")
             and "const alreadyRetried" not in ln]
    assert gates == [], f"the retry cap must not be a label on the issue: {gates}"
    assert "run.run_attempt" in text


def test_a_model_or_gate_failure_still_never_retries():
    """The one rule the higher cap must not loosen. Those cost money and fail for reasons a
    retry cannot fix, so more attempts would only mean more spend on the same wrong answer."""
    text = _wf("ops.yml")

    assert "const modelOrGate = /run|revise|propose|gate/i.test(failingStep);" in text
    assert "const transient = !modelOrGate &&" in text


@pytest.mark.parametrize("name", ["ack.yml", "watchdog.yml"])
def test_the_new_workflows_bound_their_own_runtime(name):
    """B125. A workflow with no timeout is one that can hold a runner for six hours."""
    assert "timeout-minutes:" in _wf(name)


# --------------------------------------------------------------------------------------
# The marker — the harness must not wake itself
#
# Found on the live inbox, not by a test: four full `feedback` runs in twenty-seven seconds,
# each triggered by a reply the harness had just posted. The replies carry a pointer naming
# `/harness status`, and both workflows wake on `contains(comment.body, '/harness')`. Every one
# of those runs did a checkout, an install, a doctor, a fork sync and a sweep, found nothing —
# `keywords.commands_from` skips the machine account — and posted nothing, having taken the
# ledger lock to do it.
# --------------------------------------------------------------------------------------


def test_every_comment_the_harness_posts_carries_the_marker(tmp_path):
    """Applied at the transport, so a call site added later cannot forget it. Three exist today
    and only two of them go through code that knows about `links` at all."""
    from harness.gh import MACHINE_MARKER, mark_machine_written

    assert mark_machine_written("hello").endswith(MACHINE_MARKER)
    assert MACHINE_MARKER.startswith("<!--"), "it has to render as nothing"


def test_marking_is_idempotent():
    """A retry, or a body assembled from a piece that was already marked, must not stack them."""
    from harness.gh import MACHINE_MARKER, mark_machine_written

    once = mark_machine_written("hello")
    assert mark_machine_written(once) == once
    assert once.count(MACHINE_MARKER) == 1


def test_the_transport_marks_it_rather_than_the_caller(tmp_path):
    """Driven through the client, because the claim is about `gh.comment` and not about a helper
    somebody remembered to call."""
    from tests.test_stages import FakeGh

    gh = FakeGh()
    gh.comment("owner/repo", 19, "the queue is empty")

    body = gh.comments_posted[-1][2]
    assert "the queue is empty" in body
    assert "<!-- bright-bots-harness -->" in body


def test_the_acknowledgement_is_marked_too(tmp_path, capsys):
    """`ack.yml` posts through `github-script`, not through `gh.comment`, so the transport does
    not mark it — and an acknowledgement is the worst possible unmarked comment, because its
    entire content is a list of `/harness` commands."""
    out = _run_ack(tmp_path, capsys, "/harness ask what does the registry do")

    assert "<!-- bright-bots-harness -->" in out["comment"]


@pytest.mark.parametrize("name", ["ack.yml", "feedback.yml"])
def test_both_comment_driven_workflows_skip_the_harnesss_own_comments(name):
    """The two that wake on `contains(body, '/harness')`. Either one missing the guard is the
    live bug back again, and it costs a full workflow run per comment the harness writes."""
    from harness.gh import MACHINE_MARKER

    text = _wf(name)
    job = text.split("jobs:", 1)[1]
    condition = job.split("runs-on:", 1)[0]

    assert "contains(github.event.comment.body, '/harness')" in condition
    assert f"!contains(github.event.comment.body, '{MACHINE_MARKER}')" in condition, (
        f"{name} will wake on the harness's own comments"
    )
