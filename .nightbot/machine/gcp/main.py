"""Starts the night bot's Compute Engine VM when a job waits for its runners (a Cloud Run
function, python312, HTTP).

Cloud Scheduler calls :func:`handler` every five minutes, with an OIDC token of its own invoker
service account (the function takes no unauthenticated call); ``deploy-starter.sh`` deploys it
with ``core.py`` beside it. It calls the Compute Engine API with a token of the function's own
service account, from the metadata server; that account's only role, on that one instance, lets
it read the instance and start it. Plain REST: no Google client library.

Environment: ``PROJECT``, ``ZONE``, ``INSTANCE`` (the VM's name), and ``core.py``'s (``REPO`` or
``REPO_ID``, ``WORKFLOWS``, ``LABEL_PREFIX``, ``RUNNER_LABELS``, ``GITHUB_TOKEN``).
"""

from __future__ import annotations

import json
import os
from typing import Any

import core

TOKEN = ("http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/"
         "default/token")
COMPUTE = "https://compute.googleapis.com/compute/v1"


def tick(env: Any = None, opener: Any = None, get: Any = None, now: Any = None) -> dict:
    env = os.environ if env is None else env
    config = core.env_config(env)
    instance = f"{COMPUTE}/projects/{env['PROJECT']}/zones/{env['ZONE']}/instances/{env['INSTANCE']}"
    token = core.request_json("GET", TOKEN, {"Metadata-Flavor": "Google"}, opener)["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    state = core.request_json("GET", instance, auth, opener).get("status", "unknown")
    # TERMINATED is a stopped instance. SUSPENDED would need a resume, which the idle stop never
    # leaves it in; STOPPING and the rest are looked at next time.
    return core.tick(state, state == "TERMINATED",
                     lambda: core.request_json("POST", f"{instance}/start", auth, opener),
                     config, get=get, now=now)


def handler(request: Any = None) -> tuple[str, int, dict]:
    """The HTTP entry point (``--entry-point handler``); the request itself carries nothing."""
    return json.dumps(tick()), 200, {"Content-Type": "application/json"}
