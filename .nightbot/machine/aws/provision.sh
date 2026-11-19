#!/bin/bash
# shellcheck source-path=SCRIPTDIR
# Creates the night bot's machine on AWS: one EC2 instance, Ubuntu 24.04, that stops itself when
# idle and that nothing can reach from outside. Runs ON YOUR COMPUTER with the AWS CLI v2 signed in
# to the account (as someone who may create IAM roles and EC2 instances). Idempotent: what exists
# is kept, so run it again after a failure; an instance already there only has its shutdown
# behaviour checked.
#
#   REGION=us-east-1 .nightbot/machine/aws/provision.sh
#
# What it makes, each named NAME (nightbot-box): an IAM role and instance profile holding only
# AmazonSSMManagedInstanceCore (Session Manager and Run Command are the only way in); a security
# group with no inbound rule; the instance, with no key pair, IMDSv2 required, an encrypted gp3
# root disk, shutdown behaviour stop (the idle stop powers it off from inside; terminate would
# delete it), and the tag Name=NAME. It gets a public IPv4 address in a default subnet, for its way
# out to GitHub and to Systems Manager; with no inbound rule nothing comes in by it.
#
# Env, with defaults: NAME (nightbot-box), REGION (env.sh), INSTANCE_TYPE (m7i-flex.large: 2 vCPUs,
# 8 GB; Graviton types such as m7g.large get the arm64 image), DISK_GB (100), SUBNET_ID (a default
# subnet of the default VPC, in a zone that offers the type), PUBLIC_IP (1; 0 for a private subnet
# that reaches the internet through a NAT gateway or reaches Systems Manager through VPC endpoints).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=env.sh
. "$here/env.sh"
die() { echo "provision.sh: $*" >&2; exit 1; }
TYPE="${INSTANCE_TYPE:-m7i-flex.large}"
DISK_GB="${DISK_GB:-100}"
PUBLIC_IP="${PUBLIC_IP:-1}"
[[ "$DISK_GB" =~ ^[0-9]+$ ]] || die "DISK_GB=$DISK_GB: a number"

existing="$(aws ec2 describe-instances --filters "Name=tag:Name,Values=$NAME" \
  Name=instance-state-name,Values=pending,running,stopping,stopped \
  --query 'Reservations[].Instances[].InstanceId' --output text)"
if [ -n "$existing" ]; then
  [[ "$existing" =~ ^i-[0-9a-f]+$ ]] || die "more than one instance is tagged Name=$NAME: $existing"
  aws ec2 modify-instance-attribute --instance-id "$existing" \
    --instance-initiated-shutdown-behavior Value=stop
  echo "$existing (Name=$NAME, $REGION) is already there; its shutdown behaviour is stop."
  exit 0
fi

# The instance's role: Systems Manager, nothing else.
if ! aws iam get-role --role-name "$NAME" >/dev/null 2>&1; then
  aws iam create-role --role-name "$NAME" --description "The night bot machine: Systems Manager only" \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow",
      "Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
fi
aws iam attach-role-policy --role-name "$NAME" \
  --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
if ! aws iam get-instance-profile --instance-profile-name "$NAME" >/dev/null 2>&1; then
  aws iam create-instance-profile --instance-profile-name "$NAME" >/dev/null
fi
if [ -z "$(aws iam get-instance-profile --instance-profile-name "$NAME" \
    --query 'InstanceProfile.Roles[].RoleName' --output text)" ]; then
  aws iam add-role-to-instance-profile --instance-profile-name "$NAME" --role-name "$NAME"
fi

# Where it goes: a default subnet in a zone that offers the type, unless SUBNET_ID says.
if [ -z "${SUBNET_ID:-}" ]; then
  vpc="$(aws ec2 describe-vpcs --filters Name=is-default,Values=true --query 'Vpcs[0].VpcId' --output text)"
  [ "$vpc" != None ] || die "no default VPC in $REGION: set SUBNET_ID to a subnet with a way out"
  zones="$(aws ec2 describe-instance-type-offerings --location-type availability-zone \
    --filters "Name=instance-type,Values=$TYPE" --query 'InstanceTypeOfferings[].Location' \
    --output text | tr -s '\t ' ',')"
  [ -n "$zones" ] || die "$TYPE is not offered in $REGION"
  SUBNET_ID="$(aws ec2 describe-subnets --filters "Name=vpc-id,Values=$vpc" \
    Name=default-for-az,Values=true "Name=availability-zone,Values=$zones" \
    --query 'Subnets[0].SubnetId' --output text)"
  [ "$SUBNET_ID" != None ] || die "no default subnet in a zone that offers $TYPE ($zones): set SUBNET_ID"
else
  vpc="$(aws ec2 describe-subnets --subnet-ids "$SUBNET_ID" --query 'Subnets[0].VpcId' --output text)"
fi

# A security group with no inbound rule (a new one allows all outbound traffic).
sg="$(aws ec2 describe-security-groups --filters "Name=vpc-id,Values=$vpc" "Name=group-name,Values=$NAME" \
  --query 'SecurityGroups[0].GroupId' --output text)"
if [ "$sg" = None ]; then
  sg="$(aws ec2 create-security-group --group-name "$NAME" --vpc-id "$vpc" \
    --description "The night bot machine: no inbound rules" --query GroupId --output text)"
fi
inbound="$(aws ec2 describe-security-groups --group-ids "$sg" --query 'length(SecurityGroups[0].IpPermissions)' --output text)"
[ "$inbound" = 0 ] || echo "warning: security group $sg has $inbound inbound rule(s); the machine needs none" >&2

# Ubuntu 24.04, from Canonical's public parameter, for the type's architecture.
arch=amd64
if aws ec2 describe-instance-types --instance-types "$TYPE" \
    --query 'InstanceTypes[0].ProcessorInfo.SupportedArchitectures' --output text | grep -q arm64; then
  arch=arm64
fi
ami="$(aws ssm get-parameter --name "/aws/service/canonical/ubuntu/server/24.04/stable/current/$arch/hvm/ebs-gp3/ami-id" \
  --query Parameter.Value --output text)"
root="$(aws ec2 describe-images --image-ids "$ami" --query 'Images[0].RootDeviceName' --output text)"
disks="$(jq -cn --arg d "$root" --argjson s "$DISK_GB" \
  '[{DeviceName: $d, Ebs: {VolumeSize: $s, VolumeType: "gp3", Encrypted: true, DeleteOnTermination: true}}]')"
public=--associate-public-ip-address
[ "$PUBLIC_IP" = 1 ] || public=--no-associate-public-ip-address

# A new instance profile takes a few seconds before EC2 accepts it.
err="$(mktemp)"
trap 'rm -f "$err"' EXIT
for attempt in 1 2 3 4 5 6 7 8 9; do
  if instance="$(aws ec2 run-instances --image-id "$ami" --instance-type "$TYPE" --count 1 \
      --subnet-id "$SUBNET_ID" --security-group-ids "$sg" "$public" \
      --iam-instance-profile "Name=$NAME" \
      --metadata-options HttpTokens=required,HttpEndpoint=enabled,HttpPutResponseHopLimit=1 \
      --block-device-mappings "$disks" --instance-initiated-shutdown-behavior stop \
      --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME}]" \
        "ResourceType=volume,Tags=[{Key=Name,Value=$NAME}]" \
      --query 'Instances[0].InstanceId' --output text 2>"$err")"; then
    break
  fi
  if ! grep -qi 'instance profile' "$err" || [ "$attempt" = 9 ]; then cat "$err" >&2; exit 1; fi
  sleep 10
done
echo "launched $instance ($TYPE, Ubuntu 24.04 $arch, ${DISK_GB} GB) in $REGION; waiting for it to run"
aws ec2 wait instance-running --instance-ids "$instance"
echo "waiting for its Systems Manager agent (a few minutes)"
for _ in $(seq 1 60); do
  online="$(aws ssm describe-instance-information --filters "Key=InstanceIds,Values=$instance" \
    --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null || true)"
  [ "$online" = Online ] && break
  sleep 10
done
[ "$online" = Online ] || echo "warning: its Systems Manager agent is not online yet; on-machine.sh waits for it" >&2
cat <<EOF

$instance is running, and billed by the hour until it stops. Next (README.md, AWS):
  .nightbot/machine/aws/on-machine.sh .nightbot/machine/common/setup.sh --cloud aws
EOF
