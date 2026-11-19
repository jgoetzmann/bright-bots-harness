"""The starter: which jobs wake a machine (core.py), and each cloud's handler with its calls faked
(aws/starter.py, azure/starter.py, gcp/main.py). Standard library only, no network.

    cd .nightbot/machine/starter && python3 -m unittest -v
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True  # no __pycache__ beside the handlers, which ship as they are

import core  # noqa: E402


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


aws = load("aws_starter", HERE.parent / "aws" / "starter.py")
azure = load("azure_starter", HERE.parent / "azure" / "starter.py")
gcp = load("gcp_main", HERE.parent / "gcp" / "main.py")

NOW = datetime(2026, 11, 2, 18, 0, tzinfo=timezone.utc)
ENV = {"REPO": "octo/widgets"}


def job(name, status="queued", labels=("nightbot-codex",), created="2026-11-02T17:50:00Z"):
    return {"name": name, "status": status, "labels": list(labels), "created_at": created}


class FakeGitHub:
    """GitHub's two listings: runs by status, and a run's jobs."""

    def __init__(self, runs: dict[str, list[int]] | None = None,
                 jobs: dict[int, list[dict]] | None = None, error: Exception | None = None):
        self.runs, self.jobs, self.error, self.calls = runs or {}, jobs or {}, error, []

    def __call__(self, path: str) -> dict:
        self.calls.append(path)
        if self.error:
            raise self.error
        if "/jobs" in path:
            return {"jobs": self.jobs.get(int(path.split("/runs/")[1].split("/")[0]), [])}
        status = path.split("status=")[1].split("&")[0]
        return {"workflow_runs": [{"id": run} for run in self.runs.get(status, [])]}


class FakeOpener:
    """urllib.request.urlopen's shape: answers by (method, URL prefix), and keeps every request."""

    def __init__(self, routes: dict[tuple[str, str], dict]):
        self.routes, self.requests = routes, []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        for (method, prefix), body in self.routes.items():
            if request.get_method() == method and request.full_url.startswith(prefix):
                return io.BytesIO(json.dumps(body).encode())
        raise AssertionError(f"unexpected {request.get_method()} {request.full_url}")

    def calls(self) -> list[tuple[str, str]]:
        return [(r.get_method(), r.full_url) for r in self.requests]

    def headers(self, index: int) -> dict[str, str]:
        return {k.lower(): v for k, v in self.requests[index].header_items()}


WAITING = FakeGitHub({"queued": [7]}, {7: [job("build")]})


class Quiet(unittest.TestCase):
    """The handlers log each start as a line on stdout: kept out of the test output."""

    def setUp(self):
        quiet = contextlib.redirect_stdout(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)


class WaitingJobsTests(unittest.TestCase):
    def find(self, github, **kwargs):
        return core.waiting_jobs("octo/widgets", ["nightbot-run.yml"], github, NOW, **kwargs)

    def test_a_queued_job_for_a_machine_label_waits(self):
        github = FakeGitHub({"queued": [7]}, {7: [job("build")]})
        self.assertEqual(self.find(github), [{"run": 7, "job": "build", "label": "nightbot-codex"}])
        self.assertIn("/repos/octo/widgets/actions/workflows/nightbot-run.yml/runs?status=queued"
                      "&per_page=30", github.calls)
        self.assertIn("/repos/octo/widgets/actions/runs/7/jobs?filter=latest&per_page=100",
                      github.calls)

    def test_pending_and_in_progress_runs_are_read_too(self):
        github = FakeGitHub({"pending": [8], "in_progress": [9]},
                            {8: [job("a", "pending")], 9: [job("b", "requested"),
                                                           job("done", "completed")]})
        self.assertEqual([found["job"] for found in self.find(github)], ["a", "b"])

    def test_a_run_listed_under_two_statuses_counts_once(self):
        github = FakeGitHub({"queued": [7], "in_progress": [7]}, {7: [job("build")]})
        self.assertEqual(len(self.find(github)), 1)
        self.assertEqual(sum("/runs/7/jobs" in call for call in github.calls), 1)

    def test_only_waiting_statuses_count(self):
        jobs = [job(status, status) for status in ("in_progress", "completed", "waiting")]
        self.assertEqual(self.find(FakeGitHub({"queued": [7]}, {7: jobs})), [])

    def test_hosted_labels_do_not_wake_a_machine(self):
        jobs = [job("hosted", labels=["ubuntu-latest"]), job("none", labels=[])]
        self.assertEqual(self.find(FakeGitHub({"queued": [7]}, {7: jobs})), [])

    def test_the_prefix_and_only_choose_the_labels(self):
        jobs = [job("x", labels=["self-hosted", "box-x"]), job("codex")]
        github = FakeGitHub({"queued": [7]}, {7: jobs})
        self.assertEqual([f["label"] for f in self.find(github, label_prefix="box-")], ["box-x"])
        self.assertEqual([f["job"] for f in self.find(github, only={"nightbot-gemini"})], [])
        self.assertEqual([f["job"] for f in self.find(github, only={"box-x"})], ["x"])

    def test_a_job_waiting_past_max_wait_is_left_alone(self):
        jobs = [job("old", created="2026-11-02T14:59:00Z"), job("new", created="2026-11-02T15:01:00Z"),
                job("undated", created=None), job("garbled", created="yesterday")]
        found = [f["job"] for f in self.find(FakeGitHub({"queued": [7]}, {7: jobs}))]
        self.assertEqual(found, ["new", "undated", "garbled"])

    def test_every_workflow_is_read_and_an_id_reads_by_id(self):
        github = FakeGitHub()
        core.waiting_jobs("123456", ["a.yml", "b.yml"], github, NOW)
        self.assertEqual(len(github.calls), 6)
        self.assertTrue(all(call.startswith("/repositories/123456/actions/workflows/")
                            for call in github.calls))
        self.assertIn("/b.yml/runs", github.calls[-1])


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        config = core.env_config({"REPO": " octo/widgets "})
        self.assertEqual(config, core.Config("octo/widgets", ("nightbot-run.yml",), "nightbot-",
                                             None, ""))

    def test_every_setting(self):
        config = core.env_config({"REPO": "octo/widgets", "REPO_ID": "42",
                                  "WORKFLOWS": " a.yml, ,b.yml ", "LABEL_PREFIX": "box-",
                                  "RUNNER_LABELS": "box-a,box-b,", "GITHUB_TOKEN": "t"})
        self.assertEqual(config, core.Config("42", ("a.yml", "b.yml"), "box-",
                                             frozenset({"box-a", "box-b"}), "t"))

    def test_a_repository_is_required_and_checked(self):
        for env in ({}, {"REPO": "  "}, {"REPO": "widgets"}, {"REPO": "a/b/c"}, {"REPO": "a/b c"}):
            with self.assertRaises(ValueError, msg=env):
                core.env_config(env)


class GitHubTests(unittest.TestCase):
    def test_a_token_is_sent_only_when_given(self):
        opener = FakeOpener({("GET", core.API): {"ok": 1}})
        self.assertEqual(core.github_get("secret", opener)("/repos/o/r"), {"ok": 1})
        core.github_get("", opener)("/repos/o/r")
        self.assertEqual(opener.calls(), [("GET", "https://api.github.com/repos/o/r")] * 2)
        self.assertEqual(opener.headers(0)["authorization"], "Bearer secret")
        self.assertNotIn("authorization", opener.headers(1))
        self.assertEqual(opener.headers(1)["accept"], "application/vnd.github+json")


class TickTests(unittest.TestCase):
    config = core.env_config(ENV)

    def tick(self, stopped, github, **kwargs):
        started, logged = [], []
        result = core.tick("s", stopped, lambda: started.append(1), self.config, github, NOW,
                           log=logged.append, **kwargs)
        return result, started, logged

    def test_a_machine_that_is_not_stopped_asks_github_nothing(self):
        github = FakeGitHub({"queued": [7]}, {7: [job("build")]})
        self.assertEqual(self.tick(False, github), ({"state": "s", "started": False}, [], []))
        self.assertEqual(github.calls, [])

    def test_a_stopped_machine_starts_only_when_a_job_waits(self):
        result, started, logged = self.tick(True, FakeGitHub({"queued": [7]}, {7: [job("build")]}))
        self.assertEqual((result["started"], started), (True, [1]))
        self.assertEqual(result["waiting"][0]["label"], "nightbot-codex")
        self.assertEqual(json.loads(logged[0])["started"], True)
        self.assertEqual(self.tick(True, FakeGitHub())[:2], ({"state": "s", "started": False}, []))

    def test_github_failing_starts_nothing(self):
        for error in (urllib.error.HTTPError("u", 404, "Not Found", {}, None),
                      urllib.error.URLError("down"), TimeoutError(), ValueError("bad json")):
            result, started, logged = self.tick(True, FakeGitHub(error=error))
            self.assertEqual((result["started"], started), (False, []))
            self.assertIn(type(error).__name__, result["error"])
            self.assertEqual(len(logged), 1)
        result = self.tick(True, FakeGitHub(error=urllib.error.HTTPError("u", 404, "x", {}, None)))[0]
        self.assertIn("GITHUB_TOKEN", result["error"])

    def test_decide(self):
        self.assertEqual([core.decide(s, w) for s, w in ((True, [1]), (True, []), (False, [1]))],
                         [True, False, False])


class FakeEC2:
    def __init__(self, state):
        self.state, self.started = state, []

    def describe_instances(self, InstanceIds):
        return {"Reservations": [{"Instances": [{"InstanceId": InstanceIds[0],
                                                 "State": {"Name": self.state}}]}]}

    def start_instances(self, InstanceIds):
        self.started.extend(InstanceIds)


class AwsTests(Quiet):
    env = {**ENV, "INSTANCE_ID": "i-0abc"}

    def test_a_stopped_instance_starts_for_a_waiting_job(self):
        ec2 = FakeEC2("stopped")
        result = aws.handler({}, None, ec2=ec2, get=WAITING, now=NOW, env=self.env)
        self.assertEqual((result["state"], result["started"], ec2.started),
                         ("stopped", True, ["i-0abc"]))

    def test_any_other_state_or_no_job_starts_nothing(self):
        for state, github in (("running", WAITING), ("stopping", WAITING), ("pending", WAITING),
                              ("stopped", FakeGitHub())):
            ec2 = FakeEC2(state)
            result = aws.handler({}, None, ec2=ec2, get=github, now=NOW, env=self.env)
            self.assertEqual((result["started"], ec2.started), (False, []), state)


VM = "/subscriptions/s/resourceGroups/nightbot/providers/Microsoft.Compute/virtualMachines/nightbot-box"


class AzureTests(Quiet):
    env = {**ENV, "VM_ID": VM, "IDENTITY_ENDPOINT": "http://localhost:8081/msi/token",
           "IDENTITY_HEADER": "h3ader"}

    def opener(self, power):
        return FakeOpener({
            ("GET", "http://localhost:8081/msi/token"): {"access_token": "tok"},
            ("GET", f"{azure.ARM}{VM}/instanceView"): {"statuses": [
                {"code": "ProvisioningState/succeeded"}, {"code": f"PowerState/{power}"}]},
            ("POST", f"{azure.ARM}{VM}/start"): {}})

    def test_a_deallocated_or_stopped_vm_starts_for_a_waiting_job(self):
        for power in ("deallocated", "stopped"):
            opener = self.opener(power)
            result = azure.tick(self.env, opener, WAITING, NOW)
            self.assertEqual((result["state"], result["started"]), (power, True))
            self.assertEqual(opener.calls()[1:], [
                ("GET", f"https://management.azure.com{VM}/instanceView?api-version=2024-07-01"),
                ("POST", f"https://management.azure.com{VM}/start?api-version=2024-07-01")])
            self.assertEqual(opener.headers(0)["x-identity-header"], "h3ader")
            self.assertIn("resource=https%3A%2F%2Fmanagement.azure.com%2F", opener.calls()[0][1])
            self.assertIn("api-version=2019-08-01", opener.calls()[0][1])
            self.assertEqual(opener.headers(2)["authorization"], "Bearer tok")

    def test_a_running_vm_or_no_job_starts_nothing(self):
        for power, github in (("running", WAITING), ("deallocating", WAITING),
                              ("starting", WAITING), ("deallocated", FakeGitHub())):
            opener = self.opener(power)
            self.assertFalse(azure.tick(self.env, opener, github, NOW)["started"], power)
            self.assertNotIn("POST", [method for method, _ in opener.calls()])


GCE = "https://compute.googleapis.com/compute/v1/projects/p/zones/us-central1-a/instances/nightbot-box"


class GcpTests(Quiet):
    env = {**ENV, "PROJECT": "p", "ZONE": "us-central1-a", "INSTANCE": "nightbot-box"}

    def opener(self, status):
        return FakeOpener({("GET", gcp.TOKEN): {"access_token": "tok"},
                           ("POST", f"{GCE}/start"): {"kind": "compute#operation"},
                           ("GET", GCE): {"status": status}})

    def test_a_terminated_instance_starts_for_a_waiting_job(self):
        opener = self.opener("TERMINATED")
        result = gcp.tick(self.env, opener, WAITING, NOW)
        self.assertEqual((result["state"], result["started"]), ("TERMINATED", True))
        self.assertEqual(opener.calls(), [("GET", gcp.TOKEN), ("GET", GCE), ("POST", f"{GCE}/start")])
        self.assertEqual(opener.headers(0)["metadata-flavor"], "Google")
        self.assertEqual(opener.headers(2)["authorization"], "Bearer tok")

    def test_any_other_status_or_no_job_starts_nothing(self):
        for status, github in (("RUNNING", WAITING), ("STOPPING", WAITING),
                               ("SUSPENDED", WAITING), ("TERMINATED", FakeGitHub())):
            opener = self.opener(status)
            self.assertFalse(gcp.tick(self.env, opener, github, NOW)["started"], status)
            self.assertNotIn("POST", [method for method, _ in opener.calls()])


if __name__ == "__main__":
    unittest.main()
