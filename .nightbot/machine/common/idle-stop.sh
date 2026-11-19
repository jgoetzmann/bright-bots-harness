#!/bin/bash
# Stops the machine once nothing has used it for IDLE_MINUTES. Runs ON THE MACHINE, as root, every
# five minutes from nightbot-idle-stop.timer; setup.sh installs it as /usr/local/bin/nightbot-idle-stop
# and writes its settings to /etc/nightbot/machine.conf (CLOUD, IDLE_MINUTES, IDLE_STOP).
#
# Busy is: a job running (Runner.Worker); a person on the machine (an AWS Session Manager session,
# any login `who` lists, any ssh session); a script on-machine.sh sent running; cloud-init's first
# boot; apt or dpkg at work. Each check that finds it busy stamps /run/nightbot-last-busy, which a
# boot clears. Once the stamp is IDLE_MINUTES old, by IDLE_STOP:
#
#   poweroff    shutdown -h now. AWS: the instance stops (its shutdown behaviour must be stop, or
#               it is terminated). GCP: the instance stops (TERMINATED, no vCPU billed).
#   deallocate  Azure: the VM deallocates itself through Azure Resource Manager, with a token of
#               its system-assigned managed identity (provision.sh lets it do that and no more);
#               an OS shutdown would leave it "Stopped", still allocated and billed. If that call
#               fails, it shuts the OS down anyway, and says so loudly.
#   suspend     systemctl suspend (a machine of your own; wake it yourself)
#   none        nothing
#
#   sudo nightbot-idle-stop; journalctl -t nightbot     # by hand, and what it has done
set -uo pipefail
CLOUD=local IDLE_MINUTES=30 IDLE_STOP=none
conf="${NIGHTBOT_CONF:-/etc/nightbot/machine.conf}"   # (both overridable for the tests)
stamp="${NIGHTBOT_STAMP:-/run/nightbot-last-busy}"
# shellcheck source=/dev/null
[ ! -r "$conf" ] || . "$conf"
[ "$IDLE_STOP" != none ] || exit 0

busy() {
  pgrep -f Runner.Worker >/dev/null && return
  pgrep -u ssm-user >/dev/null 2>&1 && return
  [ -n "$(who 2>/dev/null)" ] && return
  pgrep -f '^sshd(-session)?: [^ ]+@' >/dev/null && return
  pgrep -f 'nightbot-run\.' >/dev/null && return
  cloud-init status 2>/dev/null | grep -q running && return
  pgrep -x dpkg >/dev/null || pgrep -x apt-get >/dev/null \
    || pgrep -f '^/usr/bin/python3 /usr/bin/unattended-upgrade' >/dev/null
}

deallocate() {  # Azure: the VM deallocates itself (ARM, with its managed identity's token)
  local imds=http://169.254.169.254/metadata token vm
  token="$(curl -fsS --noproxy '*' -H Metadata:true \
    "$imds/identity/oauth2/token?api-version=2018-02-01&resource=https://management.azure.com/" \
    | jq -r '.access_token // empty')" || return 1
  vm="$(curl -fsS --noproxy '*' -H Metadata:true \
    "$imds/instance/compute/resourceId?api-version=2021-02-01&format=text")" || return 1
  [ -n "$token" ] && [[ "$vm" == /subscriptions/* ]] || return 1
  curl -fsS -X POST -H "Authorization: Bearer $token" -H "Content-Length: 0" \
    "https://management.azure.com${vm}/deallocate?api-version=2024-07-01" >/dev/null
}

now="$(date +%s)"
if busy; then echo "$now" > "$stamp"; exit 0; fi
last="$(cat "$stamp" 2>/dev/null)"
[[ "$last" =~ ^[0-9]+$ ]] || { echo "$now" > "$stamp"; exit 0; }
idle=$(( (now - last) / 60 ))
[ "$idle" -ge "$IDLE_MINUTES" ] || exit 0

logger -t nightbot "idle for $idle minutes; stopping the machine ($CLOUD: $IDLE_STOP)"
case "$IDLE_STOP" in
  deallocate)
    if deallocate; then
      logger -t nightbot "asked Azure to deallocate this VM"
    else
      logger -t nightbot -p user.err "DEALLOCATE FAILED: shutting the OS down instead. The VM stays allocated and BILLED until it is deallocated (az vm deallocate, or the portal); check its managed identity's role (provision.sh)"
      shutdown -h now
    fi
    ;;
  poweroff) shutdown -h now ;;
  suspend)
    date +%s > "$stamp"  # waking up is not idle time
    systemctl suspend
    ;;
  *) logger -t nightbot "IDLE_STOP=$IDLE_STOP: nothing to do" ;;
esac
exit 0
