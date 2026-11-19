"""Starts the night bot's Azure VM when a job waits for its runners (the Function's logic).

``function_app.py`` runs :func:`tick` every five minutes; ``deploy-starter.sh`` deploys both with
``core.py`` beside them. It calls Azure Resource Manager with a token of the Function's
system-assigned managed identity, whose only role lets it read that one VM and start it. Plain
REST: no Azure SDK.

Environment: ``VM_ID`` (the VM's resource id, ``/subscriptions/…/virtualMachines/<name>``),
``IDENTITY_ENDPOINT`` and ``IDENTITY_HEADER`` (set by Azure Functions for its managed identity),
and ``core.py``'s (``REPO`` or ``REPO_ID``, ``WORKFLOWS``, ``LABEL_PREFIX``, ``RUNNER_LABELS``,
``GITHUB_TOKEN``).
"""

from __future__ import annotations

import os
import urllib.parse
from typing import Any

import core

ARM = "https://management.azure.com"
API_VERSION = "2024-07-01"
#: A VM that is off: deallocated (the idle stop's way), or stopped by an OS shutdown (still billed).
STOPPED = ("deallocated", "stopped")


def tick(env: Any = None, opener: Any = None, get: Any = None, now: Any = None) -> dict:
    env = os.environ if env is None else env
    config = core.env_config(env)
    vm = env["VM_ID"].rstrip("/")
    query = urllib.parse.urlencode({"resource": ARM + "/", "api-version": "2019-08-01"})
    token = core.request_json("GET", f"{env['IDENTITY_ENDPOINT']}?{query}",
                              {"X-IDENTITY-HEADER": env["IDENTITY_HEADER"]}, opener)["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    view = core.request_json("GET", f"{ARM}{vm}/instanceView?api-version={API_VERSION}", auth,
                             opener)
    state = next((status["code"].split("/", 1)[1] for status in view.get("statuses") or []
                  if str(status.get("code", "")).startswith("PowerState/")), "unknown")
    return core.tick(state, state in STOPPED,
                     lambda: core.request_json("POST", f"{ARM}{vm}/start?api-version={API_VERSION}",
                                               auth, opener),
                     config, get=get, now=now)
