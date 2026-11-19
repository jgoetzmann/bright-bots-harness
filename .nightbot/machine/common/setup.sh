#!/bin/bash
# Sets up a night bot machine. Runs ON THE MACHINE, as root, on Ubuntu 22.04 or 24.04 (x86_64 or
# arm64) with systemd and apt. Idempotent: run it again to update the CLIs, change a setting or
# add a subscription; it never restarts a runner that is running a job. From your computer it goes
# through the cloud's on-machine.sh, which ships this directory and .nightbot/providers.json:
#
#   .nightbot/machine/aws/on-machine.sh .nightbot/machine/common/setup.sh --cloud aws
#   .nightbot/machine/aws/on-machine.sh .nightbot/machine/common/setup.sh --cloud aws codex IDLE_MINUTES=45
#
# Each id is a machine subscription: a providers.json entry whose runs_on starts with nightbot-;
# with no ids, every one. Each gets a Linux user agent-<id> (home mode 700, no sudo, no docker
# group), where a person logs its CLI in once, and the GitHub Actions runner unpacked in
# ~agent-<id>/actions-runner, which register-runners.sh registers. The machine gets git, curl, jq,
# build tools, python3, Node, the CLIs those entries name (`cli`), compressed swap in memory (zram)
# before a swapfile, a 200 MB journal, needrestart told never to restart a runner (that kills its
# job), the disk clean-up (clean.sh) after every job and every ten minutes, and the idle stop.
#
# Settings, as KEY=VALUE arguments (on-machine.sh hands the machine arguments, not your
# environment), or in this script's environment. A setting not given keeps what the last run set
# (/etc/nightbot/machine.conf), else its default:
#   IDLE_MINUTES  30         idle minutes before the machine stops
#   IDLE_STOP     how it stops: aws, gcp: poweroff; azure: deallocate; local: none (or suspend or
#                 poweroff). none on a cloud keeps the machine on for good.
#   SWAP_GB       8          the swapfile, made once; 0 for none
#   NODE_MAJOR    24         Node.js, from NodeSource
#   RUNNER_PATH   @HOME@/.local/bin:@HOME@/.cargo/bin:/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin
#                 the runners' PATH, @HOME@ standing for the user's home
#   LABEL_PREFIX  nightbot-  the runs_on prefix of a machine subscription
#   PROVIDERS     ../../providers.json from this script
#   CONFIG        ../../config.json from this script
#
# A CLI installs by config.json's "clis": {"<cli>": {"install": "..."}} (a shell command, run as root)
# if it has one, else by its name: claude, codex and gemini are known (npm, @latest, so a run of
# this updates them). Hooks for
# the repository's own toolchain, run when present beside this script: extra-setup.sh as root after
# the base install, extra-user-setup.sh as each agent user (its id as $1).
set -euo pipefail
die() { echo "setup.sh: $*" >&2; exit 1; }
usage() { echo "usage: setup.sh --cloud aws|azure|gcp|local [id …] [KEY=VALUE …]" >&2; exit 2; }
[ "$(id -u)" = 0 ] || die "run it as root"
cloud=""
ids=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --cloud) [ "$#" -ge 2 ] || usage; cloud="$2"; shift 2 ;;
    IDLE_MINUTES=*|IDLE_STOP=*|SWAP_GB=*|NODE_MAJOR=*|RUNNER_PATH=*|LABEL_PREFIX=*|PROVIDERS=*)
      export "${1%%=*}=${1#*=}"; shift ;;
    -*|*=*) usage ;;
    *) ids+=("$1"); shift ;;
  esac
done
set -- "${ids[@]}"
case "$cloud" in
  aws|gcp) default_stop=poweroff ;;
  azure) default_stop=deallocate ;;
  local) default_stop=none ;;
  *) usage ;;
esac
conf=/etc/nightbot/machine.conf
saved() { { [ -r "$conf" ] && sed -n "s/^$1=//p" "$conf" | tr -d "'" | tail -1; } || true; }
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
providers="${PROVIDERS:-$here/../../providers.json}"
config="${CONFIG:-$here/../../config.json}"
prefix="${LABEL_PREFIX:-nightbot-}"
IDLE_MINUTES="${IDLE_MINUTES:-$(saved IDLE_MINUTES)}"
IDLE_MINUTES="${IDLE_MINUTES:-30}"
[ "$(saved CLOUD)" != "$cloud" ] || IDLE_STOP="${IDLE_STOP:-$(saved IDLE_STOP)}"
IDLE_STOP="${IDLE_STOP:-$default_stop}"
SWAP_GB="${SWAP_GB:-8}"
NODE_MAJOR="${NODE_MAJOR:-$(saved NODE_MAJOR)}"
NODE_MAJOR="${NODE_MAJOR:-24}"
RUNNER_PATH="${RUNNER_PATH:-$(saved RUNNER_PATH)}"
RUNNER_PATH="${RUNNER_PATH:-@HOME@/.local/bin:@HOME@/.cargo/bin:/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin}"
[[ "$IDLE_MINUTES" =~ ^[1-9][0-9]*$ ]] || die "IDLE_MINUTES=$IDLE_MINUTES: a number of minutes"
[[ "$SWAP_GB" =~ ^[0-9]+$ ]] || die "SWAP_GB=$SWAP_GB: a number of GB"
[[ "$NODE_MAJOR" =~ ^[0-9]+$ ]] || die "NODE_MAJOR=$NODE_MAJOR: a major version"
[[ "$RUNNER_PATH" =~ ^[A-Za-z0-9@/._:-]+$ ]] || die "RUNNER_PATH=$RUNNER_PATH: directories and colons only"
case "$cloud:$IDLE_STOP" in
  aws:poweroff|gcp:poweroff|azure:deallocate|local:suspend|local:poweroff|*:none) ;;
  azure:poweroff) echo "warning: IDLE_STOP=poweroff on Azure leaves the VM stopped but allocated, and billed" >&2 ;;
  *) die "IDLE_STOP=$IDLE_STOP does not suit --cloud $cloud (see the header of this script)" ;;
esac
export DEBIAN_FRONTEND=noninteractive HOME=/root
umask 022
cd /
mkdir -p /etc/nightbot
date +%s > /run/nightbot-last-busy  # a long setup is not idle time

# Ubuntu's needrestart restarts every service whose libraries an apt run replaced, at the end of
# that run, unattended upgrades and this script's own included. A runner restarted in the middle
# of a job kills the job ("The runner has received a shutdown signal"). Written before the first
# apt run here, so the runners take the new libraries at their next restart instead.
mkdir -p /etc/needrestart/conf.d
cat > /etc/needrestart/conf.d/nightbot.conf <<'EOF'
# The night bot's runners (setup.sh): never restarted by apt, which would kill a running job.
$nrconf{override_rc}{qr(^actions\.runner\.)} = 0;
EOF

apt-get update -qq
apt-get install -y -qq git curl jq unzip zip build-essential ca-certificates gnupg python3 \
  python3-venv python3-pip bubblewrap util-linux sudo >/dev/null
[ "$cloud" = local ] || apt-get install -y -qq cloud-guest-utils >/dev/null

# The subscriptions: every id must be a machine subscription in providers.json.
[ -f "$providers" ] || die "no providers file at $providers (set PROVIDERS)"
jq -e '.providers | type == "object"' "$providers" >/dev/null || die "$providers: no \"providers\" object"
machine_ids=()
while IFS= read -r line; do [ -z "$line" ] || machine_ids+=("$line"); done < <(jq -r --arg p "$prefix" \
  '.providers | to_entries[] | select((.value.runs_on // "") | startswith($p)) | .key' "$providers")
[ "$#" -gt 0 ] || set -- "${machine_ids[@]}"
[ "$#" -gt 0 ] || die "no provider in $providers runs on ${prefix}*: nothing to set up"
for sub in "$@"; do
  [[ "$sub" =~ ^[a-z][a-z0-9_-]{0,25}$ ]] \
    || die "$sub: an id here must be lower-case letters, digits, - or _ (it names the user agent-$sub)"
  runs_on="$(jq -r --arg id "$sub" '.providers[$id].runs_on // ""' "$providers")"
  [[ "$runs_on" == "$prefix"* ]] || die "$sub: providers.json runs it on '${runs_on:-nothing}', not on a machine (${prefix}*)"
done

# Compressed swap in memory (zram), used before the swapfile: a few jobs building at once can
# overrun 8 GB, and paging to a network disk stalls the whole machine for minutes.
modprobe zram 2>/dev/null \
  || apt-get install -y -qq "linux-modules-extra-$(uname -r)" >/dev/null 2>&1 || true
if apt-get install -y -qq zram-tools >/dev/null 2>&1; then
  zram_conf='ALGO=zstd
PERCENT=50
PRIORITY=100'
  # Restarted only when it changes or is down: stopping it under a job's load swaps it all back.
  if [ "$(cat /etc/default/zramswap 2>/dev/null)" != "$zram_conf" ] \
     || ! systemctl is-active --quiet zramswap; then
    printf '%s\n' "$zram_conf" > /etc/default/zramswap
    systemctl enable zramswap >/dev/null 2>&1 || true
    systemctl restart zramswap || echo "zram swap did not start; the swapfile alone stays" >&2
  fi
fi
if [ "$SWAP_GB" -gt 0 ] && [ ! -f /swapfile ]; then
  if fallocate -l "${SWAP_GB}G" /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null \
     && swapon /swapfile; then
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
  else
    rm -f /swapfile
    echo "warning: no swapfile (this filesystem may not take one)" >&2
  fi
fi
# A cloud disk made larger is grown into at the next boot; this grows the partition and the
# filesystem now. Never on a machine of your own, whose disks are yours to lay out.
if [ "$cloud" != local ]; then
  root_src="$(findmnt -no SOURCE / || true)"
  root_disk="$(lsblk -no PKNAME "$root_src" 2>/dev/null | head -1 || true)"
  root_part="$(cat "/sys/class/block/${root_src##*/}/partition" 2>/dev/null || true)"
  if [ -n "$root_disk" ] && [ -n "$root_part" ] \
     && growpart "/dev/$root_disk" "$root_part" >/dev/null 2>&1; then
    resize2fs "$root_src" >/dev/null 2>&1 || true
  fi
fi
# The system journal may otherwise take a tenth of the disk (up to 4 GB).
mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=200M\n' > /etc/systemd/journald.conf.d/nightbot.conf
systemctl restart systemd-journald
# The CLIs' sandboxes (bubblewrap: Codex's, Claude Code's) need unprivileged user namespaces,
# which Ubuntu 24.04's AppArmor restricts by default. 22.04 has no such setting.
if [ -e /proc/sys/kernel/apparmor_restrict_unprivileged_userns ]; then
  echo 'kernel.apparmor_restrict_unprivileged_userns=0' > /etc/sysctl.d/60-nightbot-userns.conf
  sysctl -q -p /etc/sysctl.d/60-nightbot-userns.conf
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
chmod 755 "$work"  # the agent users read the runner and their hook from here

# Node (the known CLIs are npm packages), and pnpm and yarn through corepack.
if ! node --version 2>/dev/null | grep -q "^v${NODE_MAJOR}\."; then
  curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" -o "$work/nodesource.sh"
  bash "$work/nodesource.sh" >/dev/null
  apt-get install -y -qq nodejs >/dev/null
fi
corepack enable 2>/dev/null || true

# The CLIs, installed for every user; each user's login stays in its own home.
cli_install() {
  case "$1" in
    claude) echo 'npm install -g --loglevel=error @anthropic-ai/claude-code@latest' ;;
    codex) echo 'npm install -g --loglevel=error @openai/codex@latest' ;;
    gemini) echo 'npm install -g --loglevel=error @google/gemini-cli@latest' ;;
  esac
}
done_clis=" "
for sub in "$@"; do
  cli="$(jq -r --arg id "$sub" '.providers[$id].cli // ""' "$providers")"
  install=""
  if [ -f "$config" ]; then
    install="$(jq -r --arg cli "$cli" '.clis[$cli].install // ""' "$config")"
  fi
  key="${cli:-$sub}"
  [[ "$done_clis" != *" $key "* ]] || continue
  done_clis="$done_clis$key "
  [ -n "$install" ] || install="$(cli_install "$cli")"
  if [ -z "$install" ]; then
    echo "$sub: no installer known for cli '$cli': give it an \"install\" command under \"clis\" in config.json, or install it in extra-setup.sh" >&2
    continue
  fi
  bash -c "$install" >/dev/null || die "$sub: installing '$key' failed: $install"
done

if [ -f "$here/extra-setup.sh" ]; then
  bash "$here/extra-setup.sh" "$cloud" || die "extra-setup.sh failed"
fi

# The disk's clean-up, the runners' job-completed hook (the runner takes a hook only by its
# extension, .sh, and fails the job's last step if the hook fails), and the idle stop.
install -m 755 "$here/clean.sh" /usr/local/bin/nightbot-clean
install -m 755 "$here/idle-stop.sh" /usr/local/bin/nightbot-idle-stop
cat > /usr/local/bin/nightbot-job-done.sh <<'EOF'
#!/bin/bash
# The runners' job-completed hook (setup.sh), run as the job's user after every job: gives back
# that job's disk. It must never fail the job.
/usr/local/bin/nightbot-clean --job || true
exit 0
EOF
chmod 755 /usr/local/bin/nightbot-job-done.sh
cat > "$conf" <<EOF
# Written by setup.sh (run it again with KEY=VALUE to change these; a run keeps them otherwise);
# read by nightbot-idle-stop and by register-runners.sh's script on the machine.
CLOUD=$cloud
IDLE_MINUTES=$IDLE_MINUTES
IDLE_STOP=$IDLE_STOP
NODE_MAJOR=$NODE_MAJOR
RUNNER_PATH='$RUNNER_PATH'
EOF

# The newest GitHub Actions runner, unpacked once per user (it updates itself after that).
case "$(uname -m)" in
  x86_64) arch=x64 ;;
  aarch64|arm64) arch=arm64 ;;
  *) die "no GitHub runner for $(uname -m)" ;;
esac
need_runner=""
for sub in "$@"; do [ -x "/home/agent-$sub/actions-runner/config.sh" ] || need_runner=1; done
if [ -n "$need_runner" ]; then
  version="$(curl -fsSL https://api.github.com/repos/actions/runner/releases/latest | jq -r .tag_name)"
  version="${version#v}"
  curl -fsSL -o "$work/runner.tgz" \
    "https://github.com/actions/runner/releases/download/v${version}/actions-runner-linux-${arch}-${version}.tar.gz"
  chmod 644 "$work/runner.tgz"
fi
if [ -f "$here/extra-user-setup.sh" ]; then
  install -m 644 "$here/extra-user-setup.sh" "$work/extra-user-setup.sh"
fi

# A runner's job environment. The runner reads its .env (KEY=VALUE lines, no comments) and .path
# only when it starts. GEMINI_FORCE_FILE_STORAGE: Gemini CLI keeps its login in a file (there is
# no keyring here). DISABLE_AUTOUPDATER: Claude Code cannot update itself into the shared npm
# prefix; a run of this updates it.
runner_env='LANG=C.UTF-8
ACTIONS_RUNNER_HOOK_JOB_COMPLETED=/usr/local/bin/nightbot-job-done.sh
GEMINI_FORCE_FILE_STORAGE=true
DISABLE_AUTOUPDATER=1
COREPACK_ENABLE_DOWNLOAD_PROMPT=0'
write_if_changed() {  # FILE USER CONTENT: FILE written as USER unless it holds CONTENT; true if written
  [ "$(cat "$1" 2>/dev/null)" != "$3" ] || return 1
  printf '%s\n' "$3" | sudo -u "$2" tee "$1" >/dev/null
}
busy() {  # a job runs on the runner in $1 (a self-updated runner runs from where its bin links)
  pgrep -f "$1/bin/Runner.Worker" >/dev/null \
    || pgrep -f "$(readlink -f "$1/bin")/Runner.Worker" >/dev/null
}
deps=""
for sub in "$@"; do
  user="agent-$sub"
  home="/home/$user"
  id "$user" >/dev/null 2>&1 || useradd -m -s /bin/bash -K UMASK=077 "$user"
  chmod 700 "$home"
  if ! grep -q '# nightbot: setup.sh' "$home/.profile" 2>/dev/null; then
    # $HOME and $PATH are the user's shell's to expand.
    # shellcheck disable=SC2016
    printf '%s\n' '# nightbot: setup.sh. The same settings as the jobs of this user (its runners .env).' \
      'export GEMINI_FORCE_FILE_STORAGE=true DISABLE_AUTOUPDATER=1' \
      'export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"' >> "$home/.profile"
    chown "$user:$user" "$home/.profile"
  fi
  runner="$home/actions-runner"
  if [ ! -x "$runner/config.sh" ]; then
    sudo -u "$user" mkdir -p "$runner"
    sudo -u "$user" tar xzf "$work/runner.tgz" -C "$runner"
  fi
  if [ -z "$deps" ]; then
    "$runner/bin/installdependencies.sh" >/dev/null || die "the runner's installdependencies.sh failed"
    deps=1
  fi
  if [ -f "$work/extra-user-setup.sh" ]; then
    (cd "$home" && sudo -u "$user" -H bash "$work/extra-user-setup.sh" "$sub") \
      || die "$user: extra-user-setup.sh failed"
  fi
  path="${RUNNER_PATH//@HOME@/$home}"
  for dir in "$home"/actions-runner*; do
    [ -x "$dir/config.sh" ] || continue
    changed=""
    if write_if_changed "$dir/.env" "$user" "$runner_env"; then changed=1; fi
    if write_if_changed "$dir/.path" "$user" "$path"; then changed=1; fi
    if [ -z "$changed" ] || [ ! -f "$dir/.service" ]; then continue; fi
    if busy "$dir"; then
      echo "${dir##*/} of $user: busy with a job; it takes the new settings when it next restarts"
    else
      (cd "$dir" && ./svc.sh stop >/dev/null && ./svc.sh start >/dev/null)
    fi
  done
done

cat > /etc/systemd/system/nightbot-idle-stop.service <<'EOF'
[Unit]
Description=Stop the night bot machine when nothing has used it for a while
[Service]
Type=oneshot
ExecStart=/usr/local/bin/nightbot-idle-stop
EOF
cat > /etc/systemd/system/nightbot-idle-stop.timer <<'EOF'
[Unit]
Description=Check every five minutes whether the night bot machine is idle
[Timer]
OnBootSec=5min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
EOF
cat > /etc/systemd/system/nightbot-clean.service <<'EOF'
[Unit]
Description=Give back the disk the night bot's jobs can do without
[Service]
Type=oneshot
Nice=10
IOSchedulingClass=idle
ExecStart=/usr/local/bin/nightbot-clean
EOF
cat > /etc/systemd/system/nightbot-clean.timer <<'EOF'
[Unit]
Description=Clean the night bot machine's disk every ten minutes
[Timer]
OnBootSec=2min
OnUnitActiveSec=10min
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now nightbot-clean.timer >/dev/null 2>&1
if [ "$IDLE_STOP" = none ]; then
  systemctl disable --now nightbot-idle-stop.timer >/dev/null 2>&1 || true
else
  systemctl enable --now nightbot-idle-stop.timer >/dev/null 2>&1
fi
apt-get clean

# The summary.
login_hint() {
  case "$1" in
    claude) echo "claude    (then /login)" ;;
    codex) echo "codex login --device-auth" ;;
    gemini) echo "env NO_BROWSER=true gemini    (sign in with Google, then /quit)" ;;
    *) echo "${1:-<its cli>}    (its own login)" ;;
  esac
}
echo "cloud: $cloud; idle stop: $IDLE_STOP after $IDLE_MINUTES idle minutes"
for cli in node $(printf '%s\n' "$done_clis" | tr ' ' '\n' | sort -u); do
  command -v "$cli" >/dev/null && printf '%-8s %s\n' "$cli" "$("$cli" --version 2>&1 | head -1)"
done
for sub in "$@"; do
  user="agent-$sub"
  for dir in "/home/$user"/actions-runner*; do
    [ -x "$dir/config.sh" ] || continue
    state="unpacked, not registered (register-runners.sh)"
    if [ -f "$dir/.runner" ]; then
      state="registered as $(jq -r '.agentName // "?"' "$dir/.runner" 2>/dev/null)"
      [ ! -f "$dir/.service" ] || state="$state, $(systemctl is-active "$(cat "$dir/.service")" || true)"
    fi
    echo "$user ${dir##*/}: $state"
  done
  cli="$(jq -r --arg id "$sub" '.providers[$id].cli // ""' "$providers")"
  echo "  log in once (if not yet): sudo -iu $user $(login_hint "$cli")"
done
free -h | awk '/^Swap:/ {print "swap: " $2}'
df -h / | awk 'NR==2 {print "disk: " $4 " free of " $2}'
