# bb-harness image - Bright Bots Harness local mode (docs/delivery/DELIVERY-2-HANDOFF.md
# section 10.1; platform P1/P3). The package is NEVER copied in: local/run.ps1 bind-mounts the
# repository read-only at /harness so the loop cannot edit what judges it. Only the gate
# (entrypoint.sh) and pinned tools are baked in. The build context is the local/ directory
# itself, so a COPY of the package is impossible by construction, not by discipline.
#
# Rebuild-vs-restart: editing harness/ needs only .\bb-start.ps1; editing THIS file or
# entrypoint.sh needs .\bb-start.ps1 -Build. Nothing warns you otherwise (local/README.md).
FROM python:3.13-slim-bookworm

# System packages, one layer, cache cleaned: git for clones and commits, curl + xz for the Node
# tarball, ca-certificates for TLS to GitHub, npm and Anthropic.
RUN apt-get update && apt-get install -y --no-install-recommends \
      git ca-certificates curl xz-utils \
 && rm -rf /var/lib/apt/lists/*

# Node 20, pinned to an exact release from nodejs.org: the product repository's gate sequence runs
# npm ci, tsc, vite build and prettier. x64 only - the host is Docker Desktop on Windows x64.
ARG NODE_VERSION=20.18.1
RUN curl -fsSLo /tmp/node.tar.xz "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" \
 && tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 --no-same-owner \
 && rm -f /tmp/node.tar.xz /usr/local/CHANGELOG.md /usr/local/LICENSE /usr/local/README.md \
 && node --version && npm --version

# Claude Code CLI, pinned. It authenticates from CLAUDE_CODE_OAUTH_TOKEN in the process
# environment (run.ps1 passes it through the container_env.ps1 filter); no login state is baked in.
RUN npm install -g @anthropic-ai/claude-code@2.1.257 \
 && npm cache clean --force

# pytest for gate step 5 (tests/test_invariants.py). The harness itself has no runtime
# dependency (I-17), so this is the only Python package installed.
RUN pip install --no-cache-dir pytest==8.3.4

# The gate. Baked in because it runs before any check can (P3). CRLF is stripped so a Windows
# checkout with autocrlf cannot break the shebang.
COPY entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

# /harness is read-only, so bytecode must not be written there; /work is the only cwd that lasts.
ENV PYTHONPATH=/harness PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 BB_WORK_DIR=/work
WORKDIR /work
ENTRYPOINT ["/entrypoint.sh"]
