# local/: the bb container

The files for local mode: `Dockerfile`, `entrypoint.sh` (the startup gate), `run.ps1` (the
`docker run` contract), `container_env.ps1` (the credential filter), `watchdog-bb.ps1` (the host
watchdog and publisher) and `preflight.py` (host checks). How to run and operate the container is
in [docs/LOCAL-MODE.md](../docs/LOCAL-MODE.md).

## Rebuild or restart

`harness/`, `prompts/`, `tests/` and `.harness/` are mounted read-only at `/harness`, while
`entrypoint.sh` and the `Dockerfile` are baked into the image. Nothing warns you if you pick the
wrong one:

| You edited | Run |
| --- | --- |
| anything under `harness/`, `prompts/`, `tests/`, `.harness/` | `.\bb-start.ps1` |
| `local/entrypoint.sh` or `local/Dockerfile` | `.\bb-start.ps1 -Build` |

`bb-start.ps1` prints the image's build time on every start. If it is older than your last edit
to either baked-in file, the container is running the old one; rebuild.
