#!/bin/bash
# shellcheck source-path=SCRIPTDIR
# Runs a script from your computer on the Azure VM, as root, and prints its output. Runs ON YOUR
# COMPUTER with the Azure CLI signed in. The script travels through Run Command (az vm run-command
# invoke, RunShellScript: the VM has no public IP and no inbound rule) with its whole directory and
# .nightbot's providers.json and config.json, as base64 inside the command (common/bundle.sh). A
# deallocated VM is started first, and deallocates itself again once idle.
#
#   .nightbot/machine/azure/on-machine.sh .nightbot/machine/common/setup.sh --cloud azure
#
# Env: RESOURCE_GROUP (nightbot), NAME (nightbot-box).
# Run Command's limits: it returns only the last 4,096 bytes of the output (all of it stays on the
# VM in /var/log/nightbot/<script>.log; the exit status is read from the last line), it runs one
# script at a time, it stops one after 90 minutes, and the script rides inline in the request: this
# refuses a pack over MAX_KB (256) KB, so keep big files out of the script's directory.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=env.sh
. "$here/env.sh"
# shellcheck source=../common/bundle.sh
. "$here/../common/bundle.sh"
script="${1:?usage: on-machine.sh SCRIPT [ARG …]}"
shift
[ -f "$script" ] || { echo "no script $script" >&2; exit 2; }
MAX_KB="${MAX_KB:-256}"
rg=(--resource-group "$RESOURCE_GROUP" --name "$NAME")

power() {
  az vm get-instance-view "${rg[@]}" \
    --query "instanceView.statuses[?starts_with(code, 'PowerState/')].code | [0]" -o tsv
}
for _ in $(seq 1 60); do  # on its way up or down: wait for it to get there
  case "$(power)" in PowerState/starting|PowerState/stopping|PowerState/deallocating) sleep 10 ;; *) break ;; esac
done
if [ "$(power)" != PowerState/running ]; then
  echo "starting $NAME" >&2
  az vm start "${rg[@]}" --output none
fi
for i in $(seq 1 30); do
  [ "$(az vm get-instance-view "${rg[@]}" --query 'instanceView.vmAgent.statuses[0].displayStatus' \
    -o tsv 2>/dev/null)" = Ready ] && break
  [ "$i" != 1 ] || echo "waiting for its VM agent" >&2
  sleep 10
done

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
nightbot_remote "$(nightbot_pack "$script" | base64 | tr -d '\n')" "$script" "$@" > "$work/command.sh"
size="$(wc -c < "$work/command.sh" | tr -d ' ')"
if [ "$size" -gt $((MAX_KB * 1024)) ]; then
  echo "the script and its directory come to $size bytes in base64, over the ${MAX_KB} KB this sends through Run Command; move what it does not need out of $(dirname "$script")" >&2
  exit 1
fi
out="$(az vm run-command invoke "${rg[@]}" --command-id RunShellScript --scripts "@$work/command.sh" \
  --query 'value[0].message' -o json | jq -r .)"
printf '%s\n' "$out"
status="$(printf '%s\n' "$out" | sed -n 's/^nightbot-exit=\([0-9][0-9]*\)$/\1/p' | tail -1)"
if [ -z "$status" ]; then
  echo "no exit status came back (Run Command stops a script after 90 minutes); see /var/log/nightbot/ on the VM" >&2
  exit 1
fi
exit "$status"
