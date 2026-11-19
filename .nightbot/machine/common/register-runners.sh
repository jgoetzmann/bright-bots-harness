#!/bin/bash
# Registers each machine subscription's runners with GitHub and starts them as services under that
# subscription's own user. Runs ON YOUR COMPUTER, with gh signed in as an admin of the repository,
# jq, and the CLI of the cloud CLOUD names signed in (aws, azure, gcp; local needs none). It gets a
# registration and a removal token with `gh api`, then runs runners-on-box.sh on the machine
# through CLOUD's on-machine.sh; setup.sh must have run there first.
#
#   CLOUD=aws .nightbot/machine/common/register-runners.sh OWNER/REPO              # every one, its lanes
#   CLOUD=gcp .nightbot/machine/common/register-runners.sh OWNER/REPO codex gemini=2
#   CLOUD=local HOST=me@server .nightbot/machine/common/register-runners.sh OWNER/REPO codex=0
#
# A machine subscription is a providers.json entry whose runs_on starts with nightbot-. Each gets
# one runner per lane (`lanes`, default 1; `id=n` sets the count on this machine, `id=0` removes
# its runners): ~agent-<id>/actions-runner named <runs_on>, ~agent-<id>/actions-runner-2 named
# <runs_on>-2 and so on, all with the one label <runs_on>. Runners past the count are unregistered,
# unless busy with a job. Safe to run again: a registered, running runner is left as it is.
#
# Env: CLOUD (aws|azure|gcp|local, required), whatever that cloud's on-machine.sh takes (NAME,
# REGION, …), BOX (a suffix for every runner name here, when the same labels are spread over
# several machines: GitHub allows one runner of a name per repository), PROVIDERS
# (../../providers.json from here), LABEL_PREFIX (nightbot-).
set -euo pipefail
die() { echo "register-runners.sh: $*" >&2; exit 2; }
repo="${1:-}"
[[ "$repo" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] \
  || die "usage: CLOUD=aws|azure|gcp|local register-runners.sh OWNER/REPO [id[=lanes] …]"
shift
here="$(cd "$(dirname "$0")" && pwd)"
cloud="${CLOUD:-}"
case "$cloud" in aws|azure|gcp|local) ;; *) die "CLOUD=$cloud: set CLOUD to aws, azure, gcp or local" ;; esac
providers="${PROVIDERS:-$here/../../providers.json}"
prefix="${LABEL_PREFIX:-nightbot-}"
box="${BOX:-}"
[[ "$box" =~ ^[a-z0-9]*$ ]] || die "BOX=$box: lower-case letters and digits only"
[ -f "$providers" ] || die "no providers file at $providers (set PROVIDERS)"
all="$(jq -r --arg p "$prefix" '.providers | to_entries[]
  | select((.value.runs_on // "") | startswith($p))
  | "\(.key)=\(.value.runs_on)=\(.value.lanes // 1)"' "$providers")"
[ -n "$all" ] || die "no provider in $providers runs on ${prefix}*"

entries=()
if [ "$#" -eq 0 ]; then
  while IFS= read -r line; do entries+=("$line"); done <<<"$all"
else
  for arg in "$@"; do
    sub="${arg%%=*}"
    [[ "$sub" =~ ^[a-z][a-z0-9_-]*$ ]] || die "$arg: not an id"
    line="$(printf '%s\n' "$all" | grep -m1 "^$sub=" || true)"
    [ -n "$line" ] || die "$sub: no provider in $providers runs it on ${prefix}*"
    lanes="${line##*=}"
    [ "$arg" = "$sub" ] || lanes="${arg#*=}"
    entries+=("${line%=*}=$lanes")
  done
fi
for entry in "${entries[@]}"; do
  [[ "${entry##*=}" =~ ^[0-9]+$ ]] || die "${entry%%=*}: its count of runners must be a number, not ${entry##*=}"
  [[ "$entry" =~ ^[a-z][a-z0-9_-]*=[A-Za-z0-9_.-]+=[0-9]+$ ]] || die "$entry: an id, a label and a count, please"
done

token="$(gh api -X POST "repos/$repo/actions/runners/registration-token" --jq .token)"
remove_token="$(gh api -X POST "repos/$repo/actions/runners/remove-token" --jq .token)"
# A directory of its own, which on-machine.sh ships whole: the script and the tokens beside it.
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
cp "$here/runners-on-box.sh" "$scratch/runners-on-box.sh"
(umask 077 && printf '%s\n%s\n' "$token" "$remove_token" > "$scratch/tokens")
"$here/../$cloud/on-machine.sh" "$scratch/runners-on-box.sh" "$repo" "$box" "${entries[@]}"
echo
gh api --paginate "repos/$repo/actions/runners" \
  --jq '.runners[] | "\(.name)  \(.status)\(if .busy then ", busy" else "" end)  \([.labels[].name] | join(","))"'
