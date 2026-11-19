#!/bin/bash
# shellcheck source-path=SCRIPTDIR
# Runs a script from your computer on the Google Cloud VM, as root, and prints its output as it
# comes. Runs ON YOUR COMPUTER with gcloud signed in as someone OS Login lets in with sudo
# (roles/compute.osAdminLogin, and roles/iap.tunnelResourceAccessor for the tunnel; Owner has
# both). The script travels over SSH through IAP (gcloud compute ssh --tunnel-through-iap: the VM
# has no external IP) with its whole directory and .nightbot's providers.json and config.json, on
# SSH's stdin (common/bundle.sh). A stopped VM is started first, and stops itself again once idle.
#
#   .nightbot/machine/gcp/on-machine.sh .nightbot/machine/common/setup.sh --cloud gcp
#
# Env: PROJECT, ZONE, NAME (env.sh). A shell there:
#   gcloud compute ssh nightbot-box --zone ZONE --tunnel-through-iap
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=env.sh
. "$here/env.sh"
# shellcheck source=../common/bundle.sh
. "$here/../common/bundle.sh"
script="${1:?usage: on-machine.sh SCRIPT [ARG …]}"
shift
[ -f "$script" ] || { echo "no script $script" >&2; exit 2; }

status() { g compute instances describe "$NAME" --zone "$ZONE" --format 'value(status)'; }
for _ in $(seq 1 60); do  # on its way down: wait for it to get there
  case "$(status)" in STOPPING|SUSPENDING) sleep 10 ;; *) break ;; esac
done
case "$(status)" in
  RUNNING|PROVISIONING|STAGING) ;;
  SUSPENDED) echo "resuming $NAME" >&2; g compute instances resume "$NAME" --zone "$ZONE" ;;
  *) echo "starting $NAME" >&2; g compute instances start "$NAME" --zone "$ZONE" ;;
esac
ssh=(compute ssh "$NAME" --zone "$ZONE" --tunnel-through-iap --ssh-flag=-T)
for i in $(seq 1 30); do
  g "${ssh[@]}" --command true </dev/null >/dev/null 2>&1 && break
  [ "$i" != 1 ] || echo "waiting for SSH through IAP" >&2
  sleep 10
done
command="$(nightbot_remote stdin "$script" "$@")"
nightbot_pack "$script" | g "${ssh[@]}" --command "sudo -n sh -c $(nightbot_quote "$command")"
