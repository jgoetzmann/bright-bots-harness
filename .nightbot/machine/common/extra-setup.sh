#!/bin/bash
# The repository's own system-wide toolchain for the checks its jobs run. Runs ON THE MACHINE, as
# root: setup.sh runs it after the base install (git, curl, jq, build tools, python3, Node and the
# CLIs) on every run, with the --cloud value as $1, so keep it idempotent. A failure stops setup.sh.
#
# Shipped as a harmless example: everything below is a comment. Uncomment or add what your
# repository's checks need, for example:
#
#   apt-get install -y -qq postgresql-client shellcheck >/dev/null
#
#   # GitHub's CLI, if your machine jobs call gh (cli.github.com's apt repository):
#   if [ ! -f /etc/apt/sources.list.d/github-cli.list ]; then
#     install -d -m 755 /etc/apt/keyrings
#     curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
#       -o /etc/apt/keyrings/githubcli-archive-keyring.gpg
#     chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
#     echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
#       > /etc/apt/sources.list.d/github-cli.list
#     apt-get update -qq
#   fi
#   apt-get install -y -qq gh >/dev/null
#
# Per-user tools (a language toolchain in each agent's home) go in extra-user-setup.sh instead.
set -euo pipefail
exit 0
