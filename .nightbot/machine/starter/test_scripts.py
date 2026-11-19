"""The machine's shell scripts, run against a fake home with the system's commands stubbed
(sudo, systemctl, pgrep, curl, shutdown …): what register-runners.sh hands the machine, how
runners-on-box.sh lays out and registers lanes, when idle-stop.sh stops the machine and how, and
what clean.sh gives back. No root, no network, no cloud.

    cd .nightbot/machine/starter && python3 -m unittest -v
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

MACHINE = Path(__file__).resolve().parent.parent
COMMON = MACHINE / "common"


def stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/bash\n{body}\n")
    path.chmod(0o755)


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        # Nothing of the machine the tests run on leaks in: a CI runner sets RUNNER_TEMP (which
        # makes clean.sh think it is a job's own hook) and has a real cloud-init.
        inherited = {k: v for k, v in os.environ.items()
                     if not k.startswith(("RUNNER_", "GITHUB_", "ACTIONS_", "NIGHTBOT_"))}
        self.env = {**inherited, "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}"}


class ParseTests(unittest.TestCase):
    def test_every_script_parses(self):
        for script in sorted(MACHINE.rglob("*.sh")):
            done = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, f"{script}: {done.stderr}")

    def test_the_scripts_people_run_are_executable(self):
        for script in sorted(MACHINE.rglob("*.sh")):
            if script.name not in ("env.sh", "bundle.sh"):
                self.assertTrue(os.access(script, os.X_OK), script)


PROVIDERS = {"providers": {
    "codex": {"enabled": True, "cli": "codex", "login": "machine", "runs_on": "nightbot-codex",
              "lanes": 2},
    "gemini": {"enabled": True, "cli": "gemini", "login": "machine", "runs_on": "nightbot-gemini"},
    "claude": {"enabled": True, "cli": "claude", "login": "secret", "runs_on": "ubuntu-latest"}}}


class RegisterRunnersTests(Sandbox):
    """register-runners.sh with gh and the cloud's on-machine.sh stubbed."""

    def setUp(self):
        super().setUp()
        tree = self.tmp / "nightbot"
        shutil.copytree(COMMON, tree / "machine" / "common")
        (tree / "machine" / "aws").mkdir()
        (tree / "providers.json").write_text(json.dumps(PROVIDERS))
        # The machine gets the script's directory and the arguments: the stub keeps both.
        stub(tree / "machine" / "aws", "on-machine.sh",
             'printf "%s\\n" "$@" > "$OUT/args"; cp -R "$(dirname "$1")" "$OUT/shipped"')
        stub(self.bin, "gh", 'case "$*" in *registration-token*) echo REG;; '
                             '*remove-token*) echo REMOVE;; esac')
        self.script = tree / "machine" / "common" / "register-runners.sh"
        self.env.update(OUT=str(self.tmp), CLOUD="aws")

    def run_it(self, *args, **env):
        return subprocess.run(["bash", str(self.script), "octo/widgets", *args],
                              env={**self.env, **env}, capture_output=True, text=True)

    def handed(self, *args, **env):
        done = self.run_it(*args, **env)
        self.assertEqual(done.returncode, 0, done.stderr)
        return (self.tmp / "args").read_text().splitlines()[1:]

    def test_with_no_ids_every_machine_subscription_gets_its_lanes(self):
        handed = self.handed()
        self.assertEqual(handed, ["octo/widgets", "", "codex=nightbot-codex=2",
                                  "gemini=nightbot-gemini=1"])

    def test_ids_set_this_machines_counts_and_box_its_suffix(self):
        self.assertEqual(self.handed("codex=0", "gemini=3", BOX="b2"),
                         ["octo/widgets", "b2", "codex=nightbot-codex=0", "gemini=nightbot-gemini=3"])
        self.assertEqual(self.handed("gemini")[2:], ["gemini=nightbot-gemini=1"])

    def test_the_tokens_travel_in_a_file_never_as_arguments(self):
        handed = self.handed()
        self.assertNotIn("REG", "\n".join(handed))
        shipped = self.tmp / "shipped"
        self.assertEqual((shipped / "tokens").read_text(), "REG\nREMOVE\n")
        self.assertEqual(sorted(p.name for p in shipped.iterdir()), ["runners-on-box.sh", "tokens"])

    def test_it_refuses_what_it_cannot_do(self):
        for args, env, message in ((("nobody",), {}, "nobody: no provider"),
                                   (("claude",), {}, "claude: no provider"),
                                   (("codex=two",), {}, "must be a number"),
                                   (("codex",), {"BOX": "B 2"}, "BOX=B 2"),
                                   (("codex",), {"CLOUD": ""}, "set CLOUD"),
                                   (("codex",), {"CLOUD": "mars"}, "set CLOUD")):
            done = self.run_it(*args, **env)
            self.assertNotEqual(done.returncode, 0, args)
            self.assertIn(message, done.stderr, args)
        self.assertNotEqual(subprocess.run(["bash", str(self.script), "not a repo"], env=self.env,
                                           capture_output=True).returncode, 0)


#: A runner's config.sh as the on-box script sees it: its root is where its bin really is (a
#: self-update leaves bin a link to bin.<version>); it takes its token from the environment
#: (ACTIONS_RUNNER_INPUT_TOKEN), writes .path from its PATH, its registration in its root, and
#: an svc.sh named for the runner.
FAKE_CONFIG = r'''#!/bin/bash
root="$(dirname "$(readlink -f ./bin)")"
echo "${ACTIONS_RUNNER_INPUT_TOKEN:-}|$*" >> "$CALLS"
if [ "$1" = remove ]; then
  [ "${ACTIONS_RUNNER_INPUT_TOKEN:-}" = REMOVE ] || { echo "bad token"; exit 1; }
  rm -f "$root/.runner"; exit 0
fi
[ "${ACTIONS_RUNNER_INPUT_TOKEN:-}" = REG ] || { echo "bad token"; exit 1; }
[ -f "$root/.runner" ] && { echo "already configured"; exit 1; }
while [ $# -gt 0 ]; do case "$1" in --name) n="$2"; shift;; --labels) l="$2"; shift;; esac; shift; done
printf '{"agentName":"%s","labels":"%s"}\n' "$n" "$l" > "$root/.runner"
echo "$PATH" > .path
cat > "$root/svc.sh" <<SVC
#!/bin/bash
name="actions.runner.$n.service"
case "\$1" in
  install) echo "\$name" > .service; touch "\$UNITS/\$name";;
  uninstall) rm -f .service "\$UNITS/\$name";;
  start) touch "\$SVCSTATE/\$name";;
  stop) rm -f "\$SVCSTATE/\$name";;
esac
SVC
chmod +x "$root/svc.sh"
'''


class OnBoxTests(Sandbox):
    """runners-on-box.sh against a fake home, on a runner that has updated itself (its bin a link
    to bin.<version>): copying that link would make every other lane run as the first."""

    def setUp(self):
        super().setUp()
        for name in ("units", "svc"):
            (self.tmp / name).mkdir()
        self.home = self.tmp / "home" / "agent-codex"
        self.first = self.home / "actions-runner"
        for name in ("bin", "externals"):
            (self.first / f"{name}.2.0").mkdir(parents=True)
            (self.first / f"{name}.2.0" / "x").write_text("x")
            (self.first / name).symlink_to(self.first / f"{name}.2.0")
        (self.first / "config.sh").write_text(FAKE_CONFIG)
        for name in ("env.sh", "run.sh", "run-helper.sh.template", ".env", ".path"):
            (self.first / name).write_text("")
        for path in (self.first / "config.sh", self.first / "env.sh", self.first / "run.sh"):
            path.chmod(0o755)
        stub(self.bin, "sudo", 'while [ $# -gt 0 ]; do case "$1" in -u) shift 2;; '
                               '-H|--preserve-env=*) shift;; *) break;; esac; done\nexec "$@"')
        stub(self.bin, "systemctl", 'name="${@: -1}"\nif [ -f "$SVCSTATE/$name" ]; then '
                                    '[ "$2" = "--quiet" ] || echo active; exit 0; fi\n'
                                    '[ "$2" = "--quiet" ] || echo inactive; exit 3')
        stub(self.bin, "pgrep", '[ -n "${BUSY:-}" ] && case "$*" in *"$BUSY"*) exit 0;; esac\nexit 1')
        (self.tmp / "machine.conf").write_text(
            "CLOUD=aws\nIDLE_MINUTES=30\nIDLE_STOP=poweroff\nRUNNER_PATH='@HOME@/.local/bin:/usr/bin:/bin'\n")
        script = (COMMON / "runners-on-box.sh").read_text()
        script = (script.replace("/etc/nightbot/machine.conf", str(self.tmp / "machine.conf"))
                  .replace("/run/nightbot-last-busy", str(self.tmp / "last-busy"))
                  .replace("/home/", f"{self.tmp}/home/"))
        self.remote = self.tmp / "remote" / "runners-on-box.sh"
        self.remote.parent.mkdir()
        self.remote.write_text(script)
        (self.remote.parent / "tokens").write_text("REG\nREMOVE\n")
        self.calls = self.tmp / "calls"
        self.env.update(SVCSTATE=str(self.tmp / "svc"), UNITS=str(self.tmp / "units"),
                        CALLS=str(self.calls))

    def register(self, *entries, box="", **env):
        return subprocess.run(["bash", str(self.remote), "octo/widgets", box, *entries],
                              env={**self.env, **env}, capture_output=True, text=True)

    def runner(self, name):
        directory = self.home / name
        registered = directory / ".runner"
        return (directory.joinpath("bin").is_symlink(),
                json.loads(registered.read_text())["agentName"] if registered.exists() else "",
                (directory / ".service").read_text().strip()
                if (directory / ".service").exists() else "")

    def units(self):
        return sorted(p.name for p in (self.tmp / "units").iterdir())

    def test_each_lane_is_a_runner_of_its_own(self):
        done = self.register("codex=nightbot-codex=2")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.runner("actions-runner"),
                         (True, "nightbot-codex", "actions.runner.nightbot-codex.service"))
        self.assertEqual(self.runner("actions-runner-2"),
                         (False, "nightbot-codex-2", "actions.runner.nightbot-codex-2.service"))
        self.assertEqual(sorted(p.name for p in (self.tmp / "svc").iterdir()), self.units())
        self.assertEqual(done.stdout.count("active as agent-codex"), 2)
        # One label, the token in the environment and never an argument, and RUNNER_PATH.
        calls = self.calls.read_text().splitlines()
        self.assertEqual(len(calls), 2)
        for call in calls:
            token, args = call.split("|")
            self.assertEqual(token, "REG")
            self.assertNotIn("REG", args)
            self.assertIn("--labels nightbot-codex --no-default-labels", args)
        self.assertEqual((self.home / "actions-runner-2" / ".path").read_text().strip(),
                         f"{self.home}/.local/bin:/usr/bin:/bin")
        # Run again, it changes nothing.
        again = self.register("codex=nightbot-codex=2")
        self.assertEqual((again.returncode, again.stdout.count("active as agent-codex")), (0, 2))
        self.assertEqual(len(self.calls.read_text().splitlines()), 2)

    def test_a_box_suffix_ends_every_name(self):
        self.assertEqual(self.register("codex=nightbot-codex=2", box="b2").returncode, 0)
        self.assertEqual(self.units(), ["actions.runner.nightbot-codex-2-b2.service",
                                        "actions.runner.nightbot-codex-b2.service"])

    def test_lanes_past_the_count_go_unless_busy(self):
        self.register("codex=nightbot-codex=3")
        busy = self.register("codex=nightbot-codex=1", BUSY="actions-runner-3/bin/Runner.Worker")
        self.assertIn("nightbot-codex-2: removed from this box", busy.stdout)
        self.assertIn("nightbot-codex-3: busy with a job, so still registered", busy.stdout)
        self.assertFalse((self.home / "actions-runner-2").exists())
        self.assertEqual(self.runner("actions-runner-3")[1], "nightbot-codex-3")
        self.assertIn("REMOVE|remove", self.calls.read_text())
        done = self.register("codex=nightbot-codex=0")
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertEqual(self.units(), [])
        self.assertTrue((self.first / "config.sh").exists())  # the unpacked runner stays
        self.assertEqual(self.runner("actions-runner")[1], "")

    def test_a_copy_linked_into_the_first_runner_is_made_again(self):
        self.register("codex=nightbot-codex=1")
        copy = self.home / "actions-runner-2"
        copy.mkdir()
        (copy / "bin").symlink_to(self.first / "bin.2.0")
        shutil.copy(self.first / "config.sh", copy / "config.sh")
        done = self.register("codex=nightbot-codex=2")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("a copy that ran as actions-runner, removed", done.stdout)
        self.assertEqual(self.runner("actions-runner-2")[:2], (False, "nightbot-codex-2"))
        self.assertEqual(self.runner("actions-runner")[1], "nightbot-codex")

    def test_nothing_without_setup(self):
        (self.tmp / "machine.conf").unlink()
        done = self.register("codex=nightbot-codex=1")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("run setup.sh on this machine first", done.stdout)


class IdleStopTests(Sandbox):
    """idle-stop.sh with what it looks at and what it calls stubbed."""

    def setUp(self):
        super().setUp()
        self.log = self.tmp / "log"
        for name in ("logger", "shutdown", "systemctl"):
            stub(self.bin, name, f'echo "{name} $*" >> "$LOG"')
        stub(self.bin, "cloud-init", 'echo "status: ${CLOUD_INIT:-done}"')
        stub(self.bin, "pgrep", '[ -n "${BUSY:-}" ] && case "$*" in *"$BUSY"*) exit 0;; esac\nexit 1')
        stub(self.bin, "who", 'printf "%s" "${WHO:-}"')
        stub(self.bin, "curl", 'echo "curl $*" >> "$LOG"\n[ -z "${CURL_FAILS:-}" ] || exit 22\n'
                               'case "$*" in *oauth2/token*) echo \'{"access_token":"tok"}\';;\n'
                               '*compute/resourceId*) printf %s /subscriptions/s/resourceGroups/g/'
                               'providers/Microsoft.Compute/virtualMachines/v;; esac')
        self.stamp = self.tmp / "stamp"
        self.env.update(LOG=str(self.log), NIGHTBOT_CONF=str(self.tmp / "conf"),
                        NIGHTBOT_STAMP=str(self.stamp))

    def run_it(self, cloud, stop, idle_minutes=40, **env):
        (self.tmp / "conf").write_text(f"CLOUD={cloud}\nIDLE_MINUTES=30\nIDLE_STOP={stop}\n")
        self.stamp.write_text(str(int(time.time()) - idle_minutes * 60))
        if self.log.exists():
            self.log.unlink()
        done = subprocess.run(["bash", str(COMMON / "idle-stop.sh")], env={**self.env, **env},
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        return self.log.read_text() if self.log.exists() else ""

    def test_idle_long_enough_powers_off(self):
        for cloud in ("aws", "gcp"):
            self.assertIn("shutdown -h now", self.run_it(cloud, "poweroff"))

    def test_busy_or_not_idle_long_enough_does_nothing(self):
        for env in ({"BUSY": "Runner.Worker"}, {"BUSY": "ssm-user"}, {"WHO": "me pts/0"},
                    {"BUSY": "sshd"}, {"BUSY": "nightbot-run"}, {"BUSY": "dpkg"}, {"CLOUD_INIT": "running"}):
            self.assertEqual(self.run_it("aws", "poweroff", **env), "", env)
            self.assertLess(int(time.time()) - int(self.stamp.read_text()), 5)  # stamped now
        self.assertEqual(self.run_it("aws", "poweroff", idle_minutes=20), "")

    def test_azure_deallocates_itself_with_its_managed_identity(self):
        log = self.run_it("azure", "deallocate")
        self.assertNotIn("shutdown", log)
        self.assertIn("-H Metadata:true http://169.254.169.254/metadata/identity/oauth2/token?"
                      "api-version=2018-02-01&resource=https://management.azure.com/", log)
        self.assertIn("-X POST -H Authorization: Bearer tok -H Content-Length: 0 "
                      "https://management.azure.com/subscriptions/s/resourceGroups/g/providers/"
                      "Microsoft.Compute/virtualMachines/v/deallocate?api-version=2024-07-01", log)

    def test_azure_shuts_down_loudly_when_it_cannot_deallocate(self):
        log = self.run_it("azure", "deallocate", CURL_FAILS="1")
        self.assertIn("DEALLOCATE FAILED", log)
        self.assertIn("shutdown -h now", log)

    def test_a_machine_of_your_own_stops_only_when_asked(self):
        self.assertEqual(self.run_it("local", "none", idle_minutes=600), "")
        self.assertIn("systemctl suspend", self.run_it("local", "suspend"))


class CleanTests(Sandbox):
    """clean.sh as an agent user (id stubbed: the tests may run as root)."""

    def setUp(self):
        super().setUp()
        self.home = self.tmp / "home" / "agent-codex"
        self.runner = self.home / "actions-runner"
        old = time.time() - 30 * 86400
        for path in ("bin.2.1/x", "bin.2.0/x", "_work/_temp/left/x", "_work/widgets/widgets/.git/x",
                     "_work/fresh/fresh/.git/x", "_diag/Runner_old.log", "_diag/Runner_new.log"):
            (self.runner / path).parent.mkdir(parents=True, exist_ok=True)
            (self.runner / path).write_text("x")
        (self.runner / "bin").symlink_to(self.runner / "bin.2.1")
        for path in ("bin.2.0", "_work/_temp/left", "_work/widgets", "_work/widgets/widgets",
                     "_work/widgets/widgets/.git", "_work/widgets/widgets/.git/x",
                     "_diag/Runner_old.log"):
            os.utime(self.runner / path, (old, old))
        (self.home / ".npm" / "_cacache").mkdir(parents=True)
        (self.home / ".codex" / "sessions").mkdir(parents=True)
        (self.home / ".codex" / "auth.json").write_text("{}")
        stub(self.bin, "id", 'case "$1" in -u) echo 1000;; -un) echo agent-codex;; *) exit 1;; esac')
        stub(self.bin, "pgrep", 'if [ -n "${WORKER:-}" ]; then echo "4242 $WORKER"; exit 0; fi\nexit 1')
        stub(self.bin, "git", "exit 0")
        self.env.update(HOME=str(self.home), NIGHTBOT_TMP=str(self.tmp / "tmp"))
        (self.tmp / "tmp").mkdir()

    def clean(self, *args, **env):
        done = subprocess.run(["bash", str(COMMON / "clean.sh"), *args], env={**self.env, **env},
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def exists(self, path):
        return (self.runner / path).exists()

    def test_an_idle_user_gets_back_what_comes_back_by_itself(self):
        self.assertIn("nightbot-clean: agent-codex: disk", self.clean())
        self.assertEqual([self.exists(p) for p in ("bin.2.1", "bin.2.0", "_work/_temp/left",
                                                   "_work/widgets", "_work/fresh",
                                                   "_diag/Runner_old.log", "_diag/Runner_new.log")],
                         [True, False, False, False, True, False, True])
        self.assertFalse((self.home / ".npm" / "_cacache").exists())
        self.assertTrue((self.home / ".codex" / "auth.json").exists())

    def test_a_runner_with_a_job_keeps_its_work_and_the_caches_stay(self):
        self.clean(WORKER=f"{self.runner}/bin/Runner.Worker spawnclient 1 2")
        self.assertTrue(self.exists("_work/widgets"))
        self.assertTrue(self.exists("_work/_temp/left"))
        self.assertTrue((self.home / ".npm" / "_cacache").exists())
        self.assertFalse(self.exists("bin.2.0"))  # an old runner version is no job's

    def test_the_job_hook_empties_that_jobs_temp(self):
        temp = self.tmp / "runner-temp"
        (temp / "a").mkdir(parents=True)
        self.clean("--job", RUNNER_TEMP=str(temp))
        self.assertEqual(list(temp.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
