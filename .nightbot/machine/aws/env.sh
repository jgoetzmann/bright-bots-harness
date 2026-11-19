# shellcheck shell=bash
# Sourced by the aws/ scripts, ON YOUR COMPUTER: the Region and the machine's name.
#   REGION  the Region: REGION, else AWS_REGION, AWS_DEFAULT_REGION, the CLI's own, else us-east-1
#   NAME    nightbot-box: the instance's Name tag, and the name of its role, instance profile
#           and security group
REGION="${REGION:-${AWS_REGION:-${AWS_DEFAULT_REGION:-}}}"
[ -n "$REGION" ] || REGION="$(aws configure get region 2>/dev/null || true)"
REGION="${REGION:-us-east-1}"
export AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION" AWS_PAGER=""
NAME="${NAME:-nightbot-box}"
[[ "$NAME" =~ ^[A-Za-z0-9][A-Za-z0-9-]{0,50}$ ]] || { echo "NAME=$NAME: letters, digits and -" >&2; exit 2; }

aws_instance() {  # INSTANCE_ID, else the one instance tagged Name=$NAME that is not terminated
  local found="${INSTANCE_ID:-}"
  if [ -z "$found" ]; then
    found="$(aws ec2 describe-instances --filters "Name=tag:Name,Values=$NAME" \
      Name=instance-state-name,Values=pending,running,stopping,stopped \
      --query 'Reservations[].Instances[].InstanceId' --output text)"
  fi
  if ! [[ "$found" =~ ^i-[0-9a-f]+$ ]]; then
    echo "no single instance tagged Name=$NAME in $REGION (found: ${found:-none}); set NAME, REGION or INSTANCE_ID" >&2
    return 1
  fi
  printf '%s\n' "$found"
}
