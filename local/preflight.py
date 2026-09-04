#!/usr/bin/env python3
"""Host-side preflight for local mode (docs/delivery/DELIVERY-2-HANDOFF.md section 10).
Run BEFORE bb-start.ps1.

    python local/preflight.py            every check
    python local/preflight.py --quick    skip the docker daemon checks (paths, .env, pin, filter only)

Checks, each PASS / FAIL / WARN / SKIP: docker present and up; the image; the repository paths the
container mounts and gates on; .env and its two credentials; .harness/PIN and the pin itself;
bb-config.json; the credential filter's output (A46); A44's filename rule; LF line endings on the
baked-in entrypoint; the entrypoint's exec line (global flags before the subcommand); that every
BB_* variable run.ps1 sets has a reader; that bb-config.json and bb-configure.py's SCHEMA hold the
same keys; and that the two publishers still agree on --force-with-lease. Exit 1 on any FAIL.
Standard library only (I-17).

Those last four are text assertions on purpose: PowerShell and the shell entrypoint have no Python
test suite, so an invariant they share with harness/ can only be held here.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CMD_C_GIT = 'cmd /c "git'   # the broken form: see check_publishers_agree
ROOT = Path(__file__).resolve().parent.parent
LOCAL = ROOT / "local"
WORK = ROOT / "bb-work"
FAILS = 0


def report(status: str, item: str, detail: str = "") -> None:
    global FAILS
    if status == "FAIL":
        FAILS += 1
    print(f"[{status:4}] {item:36} {detail}")


def check(cond: bool, item: str, ok: str = "", bad: str = "") -> bool:
    report("PASS" if cond else "FAIL", item, ok if cond else bad)
    return cond


def run(argv: list[str], timeout: int = 60) -> tuple[int, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=ROOT)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)
    return p.returncode, (p.stdout + p.stderr).strip()


def env_value(text: str, key: str) -> str | None:
    """Last assignment wins, surrounding quotes stripped; None when the key is absent."""
    val = None
    for line in text.splitlines():
        m = re.match(r"^\s*" + re.escape(key) + r"\s*=\s*(.*)$", line)
        if m:
            val = m.group(1).strip().strip("\"'")
    return val


def check_paths() -> None:
    for rel in ("harness", "tests/test_invariants.py", "prompts", "local/Dockerfile",
                "local/entrypoint.sh", "local/run.ps1", "local/watchdog-bb.ps1",
                "local/container_env.ps1", "bb-start.ps1", "bb-stop.ps1", "bb-watcher.ps1",
                "bb-configure.py", "bb-config.json"):
        check((ROOT / rel).exists(), f"path {rel}", "present", "missing")
    if WORK.exists():
        report("PASS", "path bb-work/", "present (bind-mounted at /work)")
    else:
        report("WARN", "path bb-work/", "absent; bb-start.ps1 creates it")
    if (WORK / "STOP").exists():
        report("WARN", "bb-work/STOP", "stale STOP present; bb-start.ps1 removes it")
    gi = ROOT / ".gitignore"
    text = gi.read_text(encoding="utf-8", errors="replace") if gi.exists() else ""
    ignored = any(line.strip().rstrip("/") == "bb-work" for line in text.splitlines())
    check(ignored, ".gitignore bb-work/", "ignored", "bb-work/ is not ignored (review R1.11)")


def check_harness_dir() -> None:
    cfg = ROOT / ".harness" / "config.json"
    if check(cfg.exists(), ".harness/config.json", "present", "missing - gate step 4 fails"):
        try:
            json.loads(cfg.read_text(encoding="utf-8"))
            report("PASS", ".harness/config.json parses", "")
        except ValueError as e:
            report("FAIL", ".harness/config.json parses", str(e))
    pin = ROOT / ".harness" / "PIN"
    if check(pin.exists(), ".harness/PIN", "present",
             "missing - gate step 3 fails (B142); it is written by a reviewed PR"):
        first = pin.read_text(encoding="utf-8").split()
        shape_ok = bool(first) and re.fullmatch(r"[0-9a-f]{64}", first[0]) is not None
        check(shape_ok, ".harness/PIN shape", "sha256", f"not a sha256: {first[:1]}")
        code, out = run([sys.executable, "-m", "harness.verify_pin", "--check"])
        check(code == 0, "pin matches the tree", "verify_pin --check ok",
              f"verify_pin --check exit {code}: {out[-200:]}")
    check((ROOT / ".harness" / "trust.txt").exists(), ".harness/trust.txt", "present", "missing")


def check_env() -> None:
    env = ROOT / ".env"
    if not check(env.exists(), ".env", "present", "missing - copy .env.example and fill it in"):
        return
    text = env.read_text(encoding="utf-8", errors="replace")
    claude = env_value(text, "CLAUDE_CODE_OAUTH_TOKEN")
    if claude:
        report("PASS", "CLAUDE_CODE_OAUTH_TOKEN", "set (passes the filter; the loop needs it)")
    else:
        report("WARN", "CLAUDE_CODE_OAUTH_TOKEN",
               "empty: the container cannot call the model (BACKEND=fake still runs)")
    gh = env_value(text, "HARNESS_GITHUB_TOKEN")
    if gh:
        report("PASS", "HARNESS_GITHUB_TOKEN",
               "set on the host (filtered out of the container; the watchdog pushes with it)")
    else:
        report("WARN", "HARNESS_GITHUB_TOKEN",
               "empty: the watchdog will push with the host's own git credentials")
    report("PASS", "BACKEND", env_value(text, "BACKEND") or "(unset)")
    code, _ = run(["git", "-C", str(ROOT), "check-ignore", "-q", ".env"])
    check(code == 0, ".env ignored by git", "ignored", "NOT ignored (review R5.2)")


def check_filter() -> None:
    env = ROOT / ".env"
    if not env.exists():
        report("SKIP", "container_env.ps1", "no .env")
        return
    out_file = Path(tempfile.mkdtemp(prefix="bb-preflight-")) / "container.env"
    code, out = run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                     str(LOCAL / "container_env.ps1"), "-EnvFile", str(env),
                     "-OutFile", str(out_file)])
    m = re.search(r"dropped (\d+) line", out)
    dropped = int(m.group(1)) if m else -1
    filtered = out_file.read_text(encoding="utf-8", errors="replace") if out_file.exists() else ""
    check(code == 0 and out_file.exists(), "container_env.ps1 runs", f"exit {code}",
          f"exit {code}: {out[-200:]}")
    check(dropped > 0, "filter drop count", f"dropped {dropped} line(s) (A46)",
          f"drop count {dropped}; A46 requires non-zero")
    check("HARNESS_GITHUB_TOKEN" not in filtered, "filtered file clean",
          "no HARNESS_GITHUB_TOKEN (R5.6)", "HARNESS_GITHUB_TOKEN survived the filter")
    host_has_claude = env_value(env.read_text(encoding="utf-8", errors="replace"),
                                "CLAUDE_CODE_OAUTH_TOKEN") is not None
    check("CLAUDE_CODE_OAUTH_TOKEN" in filtered or not host_has_claude, "filter is selective",
          "CLAUDE_CODE_OAUTH_TOKEN passes through (R5.8)", "the filter dropped CLAUDE_CODE_OAUTH_TOKEN")
    shutil.rmtree(out_file.parent, ignore_errors=True)


def check_local_files() -> None:
    wd = LOCAL / "watchdog-bb.ps1"
    if wd.exists():
        body = wd.read_text(encoding="utf-8", errors="replace")
        clean = re.search(r"watchdog.ps1", wd.name) is None and re.search(r"watchdog.ps1", body) is None
        check(clean, "A44 watchdog name", "no 'watchdog.ps1' in the filename or the file",
              "'watchdog.ps1' would match rk's wildcard kill")
    ep = LOCAL / "entrypoint.sh"
    if ep.exists():
        raw = ep.read_bytes()
        check(b"\r\n" not in raw, "entrypoint.sh line endings", "LF",
              "CRLF - the Dockerfile strips it at build, but fix the checkout (.gitattributes)")
        check(raw.startswith(b"#!/bin/sh"), "entrypoint.sh shebang", "#!/bin/sh", "missing #!/bin/sh")
    df = LOCAL / "Dockerfile"
    if df.exists():
        text = df.read_text(encoding="utf-8", errors="replace")
        copies = [ln.strip() for ln in text.splitlines() if re.match(r"^\s*(COPY|ADD)\s", ln)]
        check(all("entrypoint.sh" in c for c in copies), "P1 only the gate is COPYed",
              "; ".join(copies), f"package copied into the image: {copies}")
    cfg = ROOT / "bb-config.json"
    if cfg.exists():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            missing = [s for s in ("container", "run", "watchdog", "watcher") if s not in data]
            check(not missing, "bb-config.json sections", "container, run, watchdog, watcher",
                  f"missing {missing}")
        except ValueError as e:
            report("FAIL", "bb-config.json parses", str(e))


def check_entrypoint_invocation() -> None:
    """The container's last act. `--config` is defined on the TOP-LEVEL parser only, so argparse
    rejects it after the subcommand: the wrong order passes all five gates and then dies at exec
    with exit 2, burning every restart. `--loop-seconds` is how run.ps1's BB_LOOP_SECONDS reaches
    the loop; without it the knob is inert and 300 s is the only speed the loop has."""
    ep = LOCAL / "entrypoint.sh"
    if not ep.exists():
        return
    body = ep.read_text(encoding="utf-8", errors="replace")
    execs = [ln for ln in body.splitlines() if ln.startswith("exec ")]
    if not check(len(execs) == 1, "entrypoint.sh exec line", "one", f"{len(execs)} exec lines"):
        return
    line = execs[0]
    if not check("local-loop" in line, "entrypoint.sh execs local-loop", "yes", line[:80]):
        return
    sub = line.index("local-loop")
    pos = line.find("--config")
    check(0 <= pos < sub, "entrypoint.sh --config placement",
          "before the subcommand, where a global flag belongs",
          "--config is missing or follows local-loop; argparse would exit 2")
    check("--loop-seconds" in line, "entrypoint.sh --loop-seconds",
          "the loop is told how long to sleep",
          "run.loop_seconds cannot reach the loop; it would always sleep the 300 s default")


def check_bb_env_has_readers() -> None:
    """Nothing under harness/ reads a BB_ variable and nothing may: I-4 confines os.environ to
    config.py, whose key list has no BB_ entry. So a BB_* variable run.ps1 puts in the container
    is live only if entrypoint.sh reads it. One that nothing reads is a knob that lies -
    bb-configure.py prints a restart hint for it and bb-watcher.ps1 shows it as in sync."""
    run_ps1, ep = LOCAL / "run.ps1", LOCAL / "entrypoint.sh"
    if not (run_ps1.exists() and ep.exists()):
        return
    text = run_ps1.read_text(encoding="utf-8", errors="replace")
    passed = sorted(set(re.findall(r'"(BB_[A-Z0-9_]+)=', text)))
    gate = ep.read_text(encoding="utf-8", errors="replace")
    orphans = [name for name in passed if name not in gate]
    check(not orphans, "BB_* variables have a reader",
          f"{', '.join(passed) or 'none'} (read by entrypoint.sh)",
          f"run.ps1 sets {orphans}, which entrypoint.sh never reads - a dead knob")


def check_config_schema_agrees() -> None:
    """bb-config.json and bb-configure.py's SCHEMA are two halves of one table. A file key with no
    SCHEMA entry is silently dropped by `load`; a SCHEMA key with no file entry still shows a
    value in `show`. Either way the operator is told something that is not so."""
    cfg_path, conf_path = ROOT / "bb-config.json", ROOT / "bb-configure.py"
    if not (cfg_path.exists() and conf_path.exists()):
        return
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except ValueError:
        return  # check_local_files already reported the parse failure
    in_file = {
        f"{sec}.{name}"
        for sec, vals in data.items()
        if not sec.startswith("_") and isinstance(vals, dict)
        for name in vals
    }
    schema_src = conf_path.read_text(encoding="utf-8", errors="replace")
    in_schema = set(re.findall(r'^\s*"([a-z_]+\.[a-z_0-9]+)":', schema_src, re.M))
    check(in_file == in_schema, "bb-config.json matches SCHEMA", f"{len(in_file)} keys",
          f"only in the file: {sorted(in_file - in_schema)}; "
          f"only in SCHEMA: {sorted(in_schema - in_file)}")


# A force with no lease. Two shapes, because the two publishers build argv differently:
#   ARG_FORCE   the flag as a quoted argument - "--force" / '-f' - which is how gh.py writes it,
#               several lines away from the word "push".
#   LINE_FORCE  the flag as a bare token on a line that also says push, which is how PowerShell
#               writes it. Requiring "push" on the line keeps -Force (capital F, a different
#               thing entirely) and prose out of it.
# Neither matches --force-with-lease, and neither matches ``-f`` in a docstring's backticks.
ARG_FORCE = re.compile(r"""['"](?:--force(?!-with-lease)|-f)['"]""")
LINE_FORCE = re.compile(r"--force(?!-with-lease)|(?<![\w-])-f(?![\w-])")


def _bare_force_lines(body: str, comment: str) -> list[str]:
    out = []
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith(comment):
            continue
        if ARG_FORCE.search(line) or ("push" in line and LINE_FORCE.search(line)):
            out.append(line)
    return out


def check_publishers_agree() -> None:
    """local/watchdog-bb.ps1 is the ONLY publisher in local mode (P5); harness/gh.py is the one in
    Actions mode. gh.push_branch documents "``force`` uses ``--force-with-lease``, never ``-f``"
    and a Python test pins its argv - but that test cannot see the PowerShell copy, which once
    pushed with a bare `--force` and would discard a commit a human pushed to the same fork
    branch. These assertions are the only thing holding the two publishers together.

    The lease must also carry an explicit expected sha. A bare `--force-with-lease` reads a
    remote-TRACKING ref; the watchdog pushes to a URL, which has none, and git then refuses every
    push with "stale info" - verified against a local bare repository."""
    wd = LOCAL / "watchdog-bb.ps1"
    if wd.exists():
        body = wd.read_text(encoding="utf-8", errors="replace")
        bare = _bare_force_lines(body, "#")
        check(not bare, "watchdog pushes with a lease", "no bare --force",
              f"bare force push, against gh.push_branch's rule: {bare}")
        check("--force-with-lease=" in body, "watchdog lease is explicit",
              "--force-with-lease=<ref>:<sha>",
              "a bare --force-with-lease has no tracking ref when pushing to a URL, so every "
              "push would fail with 'stale info'")
        code = [ln for ln in body.splitlines() if not ln.strip().startswith("#")]
        check(not any(CMD_C_GIT in ln for ln in code), "watchdog calls git directly",
              "& git, not cmd /c",
              "cmd /c inside an interpolated subexpression hands git a path with a leading "
              "space and exits 128 - the publisher would push nothing at all")
    gh = ROOT / "harness" / "gh.py"
    if gh.exists():
        body = gh.read_text(encoding="utf-8", errors="replace")
        bare = _bare_force_lines(body, "#")
        check(not bare, "gh.py pushes with a lease", "no bare --force",
              f"bare force push: {bare}")


def check_docker() -> None:
    exe = shutil.which("docker")
    if exe is None:
        report("FAIL", "docker on PATH", "not found - install Docker Desktop (HUMAN.md item 15)")
        return
    report("PASS", "docker on PATH", exe)
    code, out = run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=90)
    if not check(code == 0, "docker daemon", f"server {out}", "not running - start Docker Desktop"):
        return
    code, out = run(["docker", "image", "inspect", "--format", "{{.Created}}", "bb-harness:latest"])
    if code == 0:
        report("PASS", "image bb-harness:latest",
               f"built {out} (rebuild after editing local/entrypoint.sh or local/Dockerfile)")
    else:
        report("WARN", "image bb-harness:latest", "absent; the first start needs .\\bb-start.ps1 -Build")
    code, out = run(["docker", "inspect", "-f", "{{.State.Status}}", "bb"])
    report("PASS", "container bb", out if code == 0 else "absent")
    code, out = run(["docker", "inspect", "-f", "{{.State.Status}}", "rk"])
    report("PASS", "container rk (A44 baseline)",
           out if code == 0 else "absent - record this; it must be identical after bb starts and stops")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true", help="skip the docker daemon checks")
    args = ap.parse_args(argv)
    print(f"bb preflight: {ROOT}")
    check_paths()
    check_harness_dir()
    check_env()
    check_filter()
    check_local_files()
    check_entrypoint_invocation()
    check_bb_env_has_readers()
    check_config_schema_agrees()
    check_publishers_agree()
    if args.quick:
        report("SKIP", "docker", "--quick")
    else:
        check_docker()
    print(f"\n{'FAIL' if FAILS else 'OK'}: {FAILS} failing check(s)")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
