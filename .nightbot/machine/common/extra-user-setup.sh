#!/bin/bash
# The repository's per-user toolchain. Runs ON THE MACHINE, as each agent user (agent-<id>, the id
# as $1), from that user's home: setup.sh runs it on every run, so keep it idempotent. A failure
# stops setup.sh. Whatever lands in ~/.local/bin or ~/.cargo/bin is on the runners' PATH (setup.sh's
# RUNNER_PATH). Install a toolchain here, not in a job, so no job downloads one mid-run.
#
# Shipped as a harmless example: everything below is a comment. For example:
#
#   # Rust, with rustup in ~/.cargo:
#   if [ ! -x "$HOME/.cargo/bin/rustup" ]; then
#     curl --proto '=https' --tlsv1.2 -fsSL https://sh.rustup.rs \
#       | sh -s -- -y --no-modify-path --profile minimal --default-toolchain none
#   fi
#   "$HOME/.cargo/bin/rustup" toolchain install stable --profile minimal --component rustfmt,clippy
#   "$HOME/.cargo/bin/rustup" default stable
#
#   # uv (Python), into ~/.local/bin:
#   [ -x "$HOME/.local/bin/uv" ] || curl -LsSf https://astral.sh/uv/install.sh | sh
#
#   # A tool for one subscription's user only (npm's -g into ~/.local puts it in ~/.local/bin):
#   [ "$1" != codex ] || npm install -g --prefix "$HOME/.local" some-tool
#
# pnpm and yarn need nothing here: setup.sh enables Node's corepack.
set -euo pipefail
exit 0
