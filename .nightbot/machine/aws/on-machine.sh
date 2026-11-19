#!/bin/bash
# shellcheck source-path=SCRIPTDIR
# Runs a script from your computer on the AWS machine, as root, and prints its output. Runs ON YOUR
# COMPUTER with the AWS CLI v2 signed in. The script travels through Systems Manager Run Command
# (the machine has no inbound port and no key pair) with its whole directory and .nightbot's
# providers.json and config.json (common/bundle.sh). A stopped machine is started first, and stops
# itself again once idle.
#
#   .nightbot/machine/aws/on-machine.sh .nightbot/machine/common/setup.sh --cloud aws
#
# Env: NAME (nightbot-box, the instance's Name tag) or INSTANCE_ID; REGION (env.sh).
# Run Command keeps only the first 24,000 characters of the output; all of it stays on the machine
# in /var/log/nightbot/<script>.log. An interactive shell there is
# `aws ssm start-session --target <instance>` (with the CLI's Session Manager plugin).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=env.sh
. "$here/env.sh"
# shellcheck source=../common/bundle.sh
. "$here/../common/bundle.sh"
script="${1:?usage: on-machine.sh SCRIPT [ARG …]}"
shift
[ -f "$script" ] || { echo "no script $script" >&2; exit 2; }
instance="$(aws_instance)"

state() {
  aws ec2 describe-instances --instance-ids "$instance" \
    --query 'Reservations[0].Instances[0].State.Name' --output text
}
case "$(state)" in
  running) ;;
  pending) aws ec2 wait instance-running --instance-ids "$instance" ;;
  stopping|stopped)
    aws ec2 wait instance-stopped --instance-ids "$instance"
    aws ec2 start-instances --instance-ids "$instance" >/dev/null
    aws ec2 wait instance-running --instance-ids "$instance"
    echo "started $instance" >&2
    ;;
  *) echo "$instance is $(state)" >&2; exit 1 ;;
esac
for i in $(seq 1 60); do
  online="$(aws ssm describe-instance-information --filters "Key=InstanceIds,Values=$instance" \
    --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null || true)"
  [ "$online" = Online ] && break
  [ "$i" != 1 ] || echo "waiting for its Systems Manager agent" >&2
  sleep 10
done

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
nightbot_remote "$(nightbot_pack "$script" | base64 | tr -d '\n')" "$script" "$@" > "$work/command"
jq -n --rawfile c "$work/command" '{commands: [$c], executionTimeout: ["3600"]}' > "$work/params.json"
id="$(aws ssm send-command --instance-ids "$instance" --document-name AWS-RunShellScript \
  --comment "nightbot $(basename "$script")" --timeout-seconds 600 \
  --parameters "file://$work/params.json" --query Command.CommandId --output text)"
while :; do
  sleep 5
  status="$(aws ssm get-command-invocation --command-id "$id" --instance-id "$instance" \
    --query Status --output text 2>/dev/null || echo Pending)"
  case "$status" in Pending|InProgress|Delayed) continue ;; esac
  break
done
out="$(aws ssm get-command-invocation --command-id "$id" --instance-id "$instance" \
  --query StandardOutputContent --output text)"
printf '%s\n' "$out"
if [ "${#out}" -ge 24000 ]; then
  echo "(Run Command cut the output at 24,000 characters; all of it is in /var/log/nightbot/$(basename "$script").log on the machine)" >&2
fi
errors="$(aws ssm get-command-invocation --command-id "$id" --instance-id "$instance" \
  --query StandardErrorContent --output text)"
[ -z "$errors" ] || echo "$errors" >&2
[ "$status" = Success ] || { echo "Run Command: $status" >&2; exit 1; }
