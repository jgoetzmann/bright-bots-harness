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


def _run_ack(tmp_path, capsys, body: str, actor="jgoetzmann", association="OWNER",
             trust="3 jgoetzmann\n2 nathan\n") -> dict:
    """`harness ack` through the CLI the workflow actually calls, as the dict it prints."""
    import json

    from harness.__main__ import main

    body_file = tmp_path / "comment.txt"
    body_file.write_text(body, encoding="utf-8")
    code = main([
        "--config", str(_env(tmp_path, trust=trust)), "ack",
        "--body-file", str(body_file), "--actor", actor, "--association", association,
    ])
    assert code == 0, "ack must never fail the run it precedes"
    out = capsys.readouterr().out
    # Always exactly one JSON object, on every path. The workflow parses this with a `{}`
    # fallback, so a run that printed nothing would silently mean "say nothing" -- which is
    # right, but it would hide a crash rather than report one.
    return json.loads(out)


def _env(tmp_path, trust="3 jgoetzmann\n2 nathan\n") -> Path:
    trust_file = tmp_path / "trust.txt"
    trust_file.write_text(trust, encoding="utf-8")
    src = (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
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


def test_the_ack_concurrency_key_is_a_throttle_rather_than_a_formality():
    """Keyed per COMMENT the key is unique every time, so the group blocks nothing: sixty
    comments become sixty parallel jobs, which fills the account's concurrent-job allowance and
    queues the spending workflows behind an acknowledgement mechanism. Per commenter, one
    person's comments serialise and nobody else is affected."""
    group = re.findall(r"^  group: (.+)$", _wf("ack.yml"), re.M)[0]

    assert "github.event.comment.id" not in group, "a per-comment key throttles nothing"
    assert "github.event.comment.user.login" in group


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

    assert "d.getUTCDay() >= 1 && d.getUTCDay() <= 5" in text
    assert "if (!weekday(now))" in text


def test_the_watchdog_treats_a_failed_run_as_proof_of_life():
    """It is looking for the ABSENCE of runs. ops.yml owns failures, and a watchdog that also
    re-dispatched on failure would fight it — each re-running what the other just started."""
    text = _wf("watchdog.yml")

    live = [ln for ln in text.splitlines()
            if "conclusion === 'failure'" in ln and not ln.lstrip().startswith("//")]
    assert live == [], f"the watchdog must not react to failures; ops.yml owns those: {live}"


def test_a_skipped_run_is_not_proof_of_life():
    """`status: completed` is also true of a run whose only job was skipped -- and feedback.yml
    creates one of those for EVERY comment on the repository, including every reply the harness
    writes. Counting them meant any comment traffic in a six-hour window silently convinced the
    watchdog the schedule was fine, which is precisely when somebody is looking at it because it
    is not."""
    text = _wf("watchdog.yml")

    assert "'skipped'" in text and "DEAD_CONCLUSIONS" in text
    for dead in ("skipped", "cancelled", "stale", "startup_failure"):
        assert f"'{dead}'" in text, f"a {dead} run reads as a run that did something"


def test_a_queued_dispatch_stops_the_watchdog_dispatching_again():
    """A dispatch can sit `queued` for hours behind the shared `harness-ledger` lock. Dispatching
    over it cancels it and starts the clock again, so the watchdog would keep the sweep it is
    trying to cause from ever running -- and the cancelled corpses would then read as proof it
    had worked."""
    text = _wf("watchdog.yml")

    assert "'queued'" in text and "somethingPending" in text
    assert "} else if (somethingPending) {" in text


def test_the_dead_schedule_check_is_not_behind_the_staleness_check():
    """The one failure this workflow exists for was the one it could not see: a person notices
    nothing is answering and comments, which leaves a run behind, which makes the staleness gate
    return early -- taking the dead-schedule check with it."""
    text = _wf("watchdog.yml")

    dead_at = text.index("const lastScheduled")
    dispatch_at = text.index("createWorkflowDispatch")
    assert dead_at < dispatch_at, (
        "the schedule check must be asked on every tick, before any early return"
    )


def test_the_watchdog_files_an_issue_on_a_dead_schedule_not_a_late_one():
    """One miss is a hiccup the dispatch already fixed, and an issue per hiccup is how an ops
    list becomes noise. The evidence for a stoppage is specific and different: no run triggered
    by `schedule` for half a day. A dispatch of the watchdog's own resets the first clock and
    never that one, so the two cannot be confused."""
    text = _wf("watchdog.yml")

    assert "r.event === 'schedule'" in text
    # Counted in SLOTS, not hours. Friday 21:41 to Monday 00:41 is fifty-one hours with nothing
    # scheduled in them, so an hours-based threshold files a false stoppage every Monday.
    assert "DEAD_SLOTS" in text and "missedSlots" in text
    assert "missed < DEAD_SLOTS" in text


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
             and "const alreadyRetried" not in ln
             and "`- attempts:" not in ln]  # the issue body may still REPORT it
    assert gates == [], f"the retry cap must not be a label on the issue: {gates}"
    assert "run.run_attempt" in text


def test_only_steps_that_run_before_any_spend_are_retryable():
    """An ALLOW-list, and the difference is money.

    `reRunWorkflowRunFailedJobs` re-runs the whole JOB, and all three spending workflows are one
    job — so "did the failing STEP spend?" is the wrong question. `Commit state/ledger.json`
    runs AFTER the spend and matches no model-or-gate pattern, so a denial list let it through:
    a lost push to `harness-state` meant a retry that reloaded the PRE-RUN ledger, found the
    same comment unseen, and paid for the same model call again — invisibly to the weekly cap,
    because the ledger recording the first attempt was exactly what failed to save.
    """
    text = _wf("ops.yml")

    assert "const RETRYABLE_STEPS = [" in text
    assert "const preSpend = RETRYABLE_STEPS.includes(failingStep);" in text
    assert "const modelOrGate = !preSpend;" in text
    assert "'Commit state/ledger.json'" not in text, "the step that makes a run durable"
    assert "'Upload run artifacts'" not in text, "runs after the spend too"


def test_every_step_of_every_spending_workflow_is_classified():
    """The guard that makes the allow-list safe. A step added to one of the three spending
    workflows and named in neither list would default to "not retryable", which is the safe
    side — but silently, and the next person to wonder why their infrastructure blip was not
    retried has nothing to read. Failing here forces the decision to be made once, out loud."""
    import re as _re

    ops = _wf("ops.yml")
    allow = set(_re.findall(r"^\s+'([^']+)',$", ops.split("RETRYABLE_STEPS = [")[1]
                            .split("];")[0], _re.M))
    assert allow, "the allow-list did not parse"

    # Everything after the spend, plus the spending steps themselves. Named here so the two
    # lists together have to cover every step, and neither can silently shrink.
    never = {
        "Discover and propose (harness discover, harness propose)",
        "Run planned items (harness run --item)",
        "Sweep keywords (harness sweep; may run revise/propose)",
        "Queue issues assigned to the bot (harness discover --mode assigned)",
        "Reconcile stale harness:running items (harness run)",
        "Commit state/ledger.json",
        "Upload run artifacts",
    }

    # Six spaces is the step level in all three files; a `- name:` at any other depth is a job
    # name or something inside a `with:`, neither of which is a step. Read rather than parsed
    # because the harness is stdlib-only and a YAML parser is not available to the suite.
    for name in ("discover.yml", "implement.yml", "feedback.yml"):
        labels = _re.findall(r"^      - name: (.+)$", _wf(name), _re.M)
        assert len(labels) >= 10, f"{name}: only {len(labels)} steps found; the regex is wrong"
        for label in labels:
            label = label.strip()
            assert label in allow or label in never, (
                f"{name}: step {label!r} is in neither list. Decide whether a re-run of the "
                "whole job after it fails is safe, and add it to RETRYABLE_STEPS in ops.yml "
                "or to `never` here."
            )


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


# --------------------------------------------------------------------------------------
# What the adversarial pass on PR #32 found
# --------------------------------------------------------------------------------------


def test_a_halted_harness_says_so_instead_of_promising_twenty_minutes(tmp_path, capsys):
    """`.harness/HALT` stops every workflow before its first step, so `feedback.yml` goes green
    having done nothing. An acknowledgement that promises an audit anyway is worse than silence
    — and the escape hatch it offers, `/harness status`, is refused by the same halt."""
    import harness.__main__ as main_mod

    env = _env(tmp_path)
    (tmp_path / ".harness").mkdir(exist_ok=True)
    (tmp_path / ".harness" / "HALT").write_text("stop", encoding="utf-8")

    body = tmp_path / "c.txt"
    body.write_text("/harness audit accessibility", encoding="utf-8")

    import os

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        assert main_mod.main([
            "--config", str(env), "ack", "--body-file", str(body),
            "--actor", "jgoetzmann", "--association", "OWNER",
        ]) == 0
    finally:
        os.chdir(cwd)

    import json

    out = json.loads(capsys.readouterr().out)
    assert out["react"] is True, "it was read, and saying so is the point"
    assert "halted" in out["comment"].lower()
    assert "twenty minutes" not in out["comment"]


def test_the_ack_only_promises_verbs_the_actor_may_actually_give(tmp_path, capsys):
    """The sweep applies `VERB_LEVEL` per verb and refuses the rest. Acknowledging all of them
    promises a level-1 asker twenty minutes of audit and then denies it — the exact failure
    `cmd_ack` exists to avoid, performed in public."""
    out = _run_ack(tmp_path, capsys, "/harness audit accessibility\n/harness ask what is this\n",
                   actor="asker", association="MEMBER", trust="1 asker\n3 jgoetzmann\n")

    assert "/harness ask" in out["comment"]
    assert "/harness audit" not in out["comment"], "level 1 cannot audit; do not promise it"


def test_a_level_one_actor_with_nothing_allowed_is_answered_by_the_sweep_not_here(
    tmp_path, capsys
):
    """When every verb is above them there is nothing to acknowledge, and the sweep's refusal is
    the right answer — it names the level they needed, which this cannot."""
    out = _run_ack(tmp_path, capsys, "/harness audit accessibility", actor="asker",
                   association="MEMBER", trust="1 asker\n")

    assert out == {"react": False, "comment": ""}


@pytest.mark.parametrize("name", ["ack.yml", "feedback.yml"])
def test_a_quote_reply_to_the_harness_is_still_a_persons_comment(name):
    """GitHub's quote-reply copies the source comment's raw markdown, HTML comments included, as
    `> <!-- ... -->`. Skipping on the bare marker dropped the most natural way to answer the
    harness: quote its reply and add a command. The quoted form is what tells them apart."""
    from harness.gh import MACHINE_MARKER

    text = _wf(name)
    condition = text.split("jobs:", 1)[1].split("runs-on:", 1)[0]

    assert f"!contains(github.event.comment.body, '{MACHINE_MARKER}')" in condition
    assert f"contains(github.event.comment.body, '> {MACHINE_MARKER}')" in condition, (
        f"{name} drops a human who quote-replies to the harness"
    )


@pytest.mark.parametrize("name", ["ack.yml", "feedback.yml"])
def test_a_comment_driven_workflow_checks_out_the_default_branch_not_the_pull_request(name):
    """On `pull_request_review_comment` GitHub sets GITHUB_REF to `refs/pull/N/merge`, so a bare
    checkout lands the PULL REQUEST'S tree — and both of these then run that tree's `harness/`
    code. On a public repository that is a stranger's Python on the runner, and in
    `feedback.yml` it is a stranger's Python beside the machine account's PAT."""
    text = _wf(name)
    checkout = text.split("actions/checkout@v4", 1)[1].split("- name:", 1)[0]

    assert "ref: ${{ github.event.repository.default_branch }}" in checkout, (
        f"{name} checks out whatever the pull request contains"
    )


def test_neither_comment_step_can_red_somebody_elses_thread():
    """Both writes are courtesies. `issues.createComment` 403s on a locked issue, on a fork pull
    request's read-only token, and on GitHub's secondary content-creation limit — none of which
    is the commenter's fault, and all of which would put a red cross on their thread."""
    text = _wf("ack.yml")

    for step in ("React", "Say it"):
        block = text.split(f"- name: {step}\n", 1)[1].split("uses:", 1)[0]
        assert "continue-on-error: true" in block, f"{step} can fail the run"


def test_the_watchdog_does_not_claim_to_catch_its_own_disablement():
    """GitHub's 60-day rule disables EVERY schedule on a quiet public repository, this one
    included — so it would not be running to report it. Promising otherwise sends an operator
    looking for an issue that cannot arrive, while the signal that does survive (the weekly
    heartbeat going missing, B144) goes unmentioned."""
    text = _wf("watchdog.yml")
    docs = (REPO_ROOT / "docs" / "OPERATIONS.md").read_text(encoding="utf-8")

    assert "heartbeat" in text, "the workflow must name the alarm that outlives it"
    assert "60 days" in docs and "heartbeat" in docs.split("60 days")[1][:800]
