#!/bin/bash
# Registers runners with GitHub and runs them as services. Runs ON THE MACHINE, as root, sent there
# by register-runners.sh, which writes the GitHub tokens into ./tokens beside this script (the
# registration token, then the removal token), so neither is ever on a command line another user
# could read with ps. Arguments: OWNER/REPO, the box's name suffix (may be empty), then one
# id=label=lanes per subscription. Needs setup.sh run first (/etc/nightbot/machine.conf).
#
# Lane 1 of agent-<id> is ~/actions-runner, named <label>[-<box>]; lane n is ~/actions-runner-n,
# <label>-n[-<box>], a copy of lane 1's unpacked runner. Each carries the one label <label>, so
# only that subscription's jobs land on it, and runs as a systemd service under its user. A lane
# past the count is unregistered and removed, unless it holds a job. Nothing here restarts a
# runner that is running a job (that kills the job): it says so, and a later run finishes.
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$1"; box="$2"; shift 2
{ read -r token; read -r remove_token; } < "$here/tokens"
conf=/etc/nightbot/machine.conf
[ -r "$conf" ] || { echo "no $conf: run setup.sh on this machine first"; exit 1; }
# shellcheck source=/dev/null
. "$conf"
date +%s > /run/nightbot-last-busy
failed=0

# A runner's self-update leaves its bin and externals links to bin.<version> and
# externals.<version>, and the runner runs from where its bin really is: its Worker runs there,
# and it reads its registration (.runner) and writes its svc.sh in that directory.
busy() {  # a job runs on the runner in $1
  pgrep -f "$1/bin/Runner.Worker" >/dev/null \
    || pgrep -f "$(readlink -f "$1/bin")/Runner.Worker" >/dev/null
}
linked() {  # $1, a copy, has a bin that links into another runner's directory: it runs as that one
  [ "$1" != "$first" ] && [ -L "$1/bin" ] || return 1
  case "$(readlink -f "$1/bin")" in "$1"/*) return 1 ;; esac
}
config() {  # USER TOKEN ARG…: ./config.sh ARGs as USER, with the token in its environment only and
  # the runner's PATH (config.sh writes .path from the PATH it runs with)
  local user="$1" secret="$2"
  shift 2
  ACTIONS_RUNNER_INPUT_TOKEN="$secret" sudo --preserve-env=ACTIONS_RUNNER_INPUT_TOKEN -u "$user" \
    env PATH="$path" ./config.sh "$@" 2>&1
}
stop_service() { [ ! -f .service ] || { ./svc.sh stop >/dev/null 2>&1; ./svc.sh uninstall >/dev/null 2>&1; }; }

for entry in "$@"; do
  sub="${entry%%=*}"; rest="${entry#*=}"; label="${rest%%=*}"; lanes="${rest#*=}"
  user="agent-$sub"; home="/home/$user"; first="$home/actions-runner"
  path="${RUNNER_PATH//@HOME@/$home}"
  if [ ! -x "$first/config.sh" ]; then
    echo "$label: no runner unpacked for $user; run setup.sh with $sub first"; failed=1; continue
  fi
  # A copy `linked` into another runner (a copy of a self-updated runner's links, not of what they
  # point to) has no registration of its own: it wrote the other one's. It goes, with its service
  # unless another runner of this user holds that service's name, and its lane is copied again.
  for dir in "$first"-*; do
    linked "$dir" || continue
    if busy "$dir"; then
      echo "${dir##*/}: runs as ${first##*/}, busy with a job; run this again once it ends"
      continue
    fi
    unit="$(cat "$dir/.service" 2>/dev/null || true)"; shared=""
    for other in "$first"/.service "$first"-*/.service; do
      [ "$other" != "$dir/.service" ] && [ -n "$unit" ] && [ "$(cat "$other" 2>/dev/null)" = "$unit" ] && shared=1
    done
    if [ -n "$unit" ] && [ -z "$shared" ]; then (cd "$dir" && stop_service); fi
    rm -rf "$dir"
    echo "${dir##*/}: a copy that ran as ${first##*/}, removed"
  done
  # Past the count: unregistered and gone, unless it holds a job.
  for dir in "$first" "$first"-*; do
    lane="${dir#"$first"}"; lane="${lane#-}"; lane="${lane:-1}"
    if ! [[ "$lane" =~ ^[0-9]+$ ]] || [ "$lane" -le "$lanes" ] || [ ! -f "$dir/.runner" ]; then continue; fi
    gone="$(jq -r '.agentName // empty' "$dir/.runner" 2>/dev/null || true)"; gone="${gone:-${dir##*/}}"
    if busy "$dir"; then
      echo "$gone: busy with a job, so still registered; run this again once it ends"; continue
    fi
    (cd "$dir" && stop_service)
    if ! out="$(cd "$dir" && config "$user" "$remove_token" remove)"; then
      echo "$gone: removal failed:"; echo "$out" | tail -4; failed=1; continue
    fi
    [ "$dir" = "$first" ] || rm -rf "$dir"  # the unpacked runner stays, the source of every copy
    echo "$gone: removed from this box"
  done
  for lane in $(seq 1 "$lanes"); do
    dir="$first"; name="$label${box:+-$box}"
    if [ "$lane" -gt 1 ]; then dir="$first-$lane"; name="$label-$lane${box:+-$box}"; fi
    if linked "$dir"; then  # still there: it held a job above
      echo "$name: runs as ${first##*/} until its job ends; run this again then"; continue
    fi
    if [ "$dir" != "$first" ] && [ ! -x "$dir/config.sh" ]; then
      # A fresh copy of the unpacked runner, unregistered: the directories its bin and externals
      # link to, never the links (`linked`), and no svc.sh, which config.sh writes with this
      # runner's own service name.
      sudo -u "$user" mkdir -p "$dir"
      sudo -u "$user" cp -a "$(readlink -f "$first/bin")" "$dir/bin"
      sudo -u "$user" cp -a "$(readlink -f "$first/externals")" "$dir/externals"
      (cd "$first" && sudo -u "$user" cp -a ./*.sh ./*.template .env .path "$dir/")
      rm -f "$dir/svc.sh"
    fi
    cd "$dir" || continue
    held="$(jq -r '.agentName // empty' .runner 2>/dev/null || true)"
    if [ -n "$held" ] && [ "$held" != "$name" ]; then
      # Another runner's registration left here: its files go; the registration on GitHub is that
      # runner's own lane's to take over (--replace), so the order of the ids does not matter.
      if busy "$dir"; then
        echo "$name: holds $held's registration, busy with a job; run this again once it ends"
        continue
      fi
      stop_service
      rm -f .runner .credentials .credentials_rsaparams .service
      echo "$name: dropped $held's registration, which was here"
    fi
    if [ ! -f .runner ]; then
      if ! out="$(config "$user" "$token" --unattended --url "https://github.com/$repo" \
          --name "$name" --labels "$label" --no-default-labels --work _work --replace)"; then
        echo "$name: registration failed:"; echo "$out" | tail -4; failed=1
        continue
      fi
    fi
    [ -f .service ] || ./svc.sh install "$user" >/dev/null
    service="$(cat .service)"
    # The PATH setup.sh gives the runners (RUNNER_PATH). A runner reads .path only when it starts,
    # so a running one restarts with it, unless it holds a job, which a restart would kill.
    note=""
    if [ "$(cat .path 2>/dev/null)" != "$path" ]; then
      if systemctl is-active --quiet "$service" && busy "$dir"; then
        echo "$name: busy with a job, and its PATH is not RUNNER_PATH yet; run this again once it ends"
        continue
      fi
      echo "$path" | sudo -u "$user" tee .path >/dev/null
      if systemctl is-active --quiet "$service"; then ./svc.sh stop >/dev/null; fi
      note=", PATH set"
    fi
    # Started only if it is not running: a restart would kill the job a running one holds.
    systemctl is-active --quiet "$service" || ./svc.sh start >/dev/null
    echo "$name: $(systemctl is-active "$service") as $user$note"
  done
done
exit "$failed"
