"""Starts the night bot's EC2 machine when a job waits for its runners (an AWS Lambda, python3.13).

EventBridge runs :func:`handler` every five minutes; ``deploy-starter.sh`` deploys it with
``core.py`` beside it. It calls EC2 for two things only, which are all its role allows: describing
the instance, and starting it. Nothing reaches it from outside.

Environment: ``INSTANCE_ID``, and ``core.py``'s (``REPO`` or ``REPO_ID``, ``WORKFLOWS``,
``LABEL_PREFIX``, ``RUNNER_LABELS``, ``GITHUB_TOKEN``).
"""

from __future__ import annotations

import os
from typing import Any

import core


def handler(event: Any = None, context: Any = None, ec2: Any = None, get: Any = None,
            now: Any = None, env: Any = None) -> dict:
    """The Lambda entry point; ``ec2`` (a boto3 EC2 client), ``get``, ``now`` and ``env`` are
    the tests' to inject."""
    env = os.environ if env is None else env
    config = core.env_config(env)
    instance = env["INSTANCE_ID"]
    if ec2 is None:
        import boto3  # in the Lambda runtime

        ec2 = boto3.client("ec2")
    reservations = ec2.describe_instances(InstanceIds=[instance])["Reservations"]
    state = reservations[0]["Instances"][0]["State"]["Name"]
    # Only a stopped instance starts: pending, stopping and the rest are looked at next time.
    return core.tick(state, state == "stopped",
                     lambda: ec2.start_instances(InstanceIds=[instance]), config, get=get, now=now)
