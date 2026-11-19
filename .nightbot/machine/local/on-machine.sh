#!/bin/bash
# shellcheck source-path=SCRIPTDIR
# Runs a script on a machine of your own, as root, and prints its output as it comes: a home
# server, a spare PC, a Linux VM on your Mac (UTM, Multipass, OrbStack, …). The machine needs
# Ubuntu 22.04 or 24.04 (systemd and apt) for setup.sh. Runs ON YOUR COMPUTER, or on the machine
# itself. The script travels with its whole directory and .nightbot's providers.json and
# config.json (common/bundle.sh): with HOST empty it runs here through sudo; with HOST, over ssh
# with `sudo -n`, so that user needs sudo without a password on HOST (a sudoers line, or run this
# on the machine itself instead). Nothing here wakes a sleeping machine: there is no starter.
#
#   HOST=me@homeserver .nightbot/machine/local/on-machine.sh .nightbot/machine/common/setup.sh --cloud local
#   .nightbot/machine/local/on-machine.sh .nightbot/machine/common/setup.sh --cloud local     # on it
#
# Env: HOST (user@host or a name from ~/.ssh/config; empty: this computer).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../common/bundle.sh
. "$here/../common/bundle.sh"
script="${1:?usage: on-machine.sh SCRIPT [ARG …]}"
shift
[ -f "$script" ] || { echo "no script $script" >&2; exit 2; }
command="$(nightbot_remote stdin "$script" "$@")"
if [ -z "${HOST:-}" ]; then
  nightbot_pack "$script" | sudo sh -c "$command"
else
  nightbot_pack "$script" | ssh -T "$HOST" "sudo -n sh -c $(nightbot_quote "$command")"
fi
