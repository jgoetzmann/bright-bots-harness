#!/bin/bash
# shellcheck source-path=SCRIPTDIR
# Creates the night bot's machine on Google Cloud: one Compute Engine VM, Ubuntu 24.04, with no
# external IP address, that stops itself when idle. Runs ON YOUR COMPUTER with gcloud signed in to
# a project with billing, as someone who may enable APIs and create networks and VMs (Owner, or
# Compute Admin plus Service Usage Admin). Idempotent: what exists is kept, so run it again after a
# failure.
#
#   PROJECT=my-project ZONE=us-central1-a .nightbot/machine/gcp/provision.sh
#
# What it makes: the Compute Engine and IAP APIs on; a VPC network NETWORK with one subnet in the
# region; a firewall rule that lets in only IAP's TCP forwarding range (35.235.240.0/20) on port 22,
# to this VM's tag, so a person or on-machine.sh reaches it through IAP (gcloud compute ssh
# --tunnel-through-iap); a Cloud Router with Cloud NAT, the VM's way out to GitHub; and the VM:
# no external IP, no service account (it calls no Google API: a guest shutdown stops it), Shielded
# VM, OS Login (your Google account's IAM roles decide who gets in; roles/compute.osAdminLogin
# gives sudo).
#
# Env, with defaults: PROJECT, ZONE, REGION, NAME (env.sh), MACHINE_TYPE (e2-standard-2: 2 vCPUs,
# 8 GB), DISK_GB (100, pd-balanced), IMAGE_FAMILY (ubuntu-2404-lts-amd64), NETWORK (nightbot; with
# NETWORK=default the default network and its subnet named default, whose own firewall rules stay
# as they are), SUBNET ($NETWORK), RANGE (10.42.0.0/24, for a new subnet).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=env.sh
. "$here/env.sh"
MACHINE_TYPE="${MACHINE_TYPE:-e2-standard-2}"
DISK_GB="${DISK_GB:-100}"
IMAGE_FAMILY="${IMAGE_FAMILY:-ubuntu-2404-lts-amd64}"
NETWORK="${NETWORK:-nightbot}"
SUBNET="${SUBNET:-$NETWORK}"
RANGE="${RANGE:-10.42.0.0/24}"
router="$NETWORK-$REGION"
tag=nightbot

g services enable compute.googleapis.com iap.googleapis.com
g compute networks describe "$NETWORK" >/dev/null 2>&1 \
  || g compute networks create "$NETWORK" --subnet-mode custom
g compute networks subnets describe "$SUBNET" --region "$REGION" >/dev/null 2>&1 \
  || g compute networks subnets create "$SUBNET" --network "$NETWORK" --region "$REGION" --range "$RANGE"
g compute firewall-rules describe "$NETWORK-allow-iap-ssh" >/dev/null 2>&1 \
  || g compute firewall-rules create "$NETWORK-allow-iap-ssh" --network "$NETWORK" \
       --direction INGRESS --action ALLOW --rules tcp:22 --source-ranges 35.235.240.0/20 \
       --target-tags "$tag" --description "IAP TCP forwarding to the night bot machine, nothing else"
g compute routers describe "$router" --region "$REGION" >/dev/null 2>&1 \
  || g compute routers create "$router" --network "$NETWORK" --region "$REGION"
g compute routers nats describe "$router-nat" --router "$router" --region "$REGION" >/dev/null 2>&1 \
  || g compute routers nats create "$router-nat" --router "$router" --region "$REGION" \
       --auto-allocate-nat-external-ips --nat-all-subnet-ip-ranges
if ! g compute instances describe "$NAME" --zone "$ZONE" >/dev/null 2>&1; then
  g compute instances create "$NAME" --zone "$ZONE" --machine-type "$MACHINE_TYPE" \
    --image-family "$IMAGE_FAMILY" --image-project ubuntu-os-cloud \
    --boot-disk-size "${DISK_GB}GB" --boot-disk-type pd-balanced \
    --subnet "$SUBNET" --no-address --tags "$tag" \
    --no-service-account --no-scopes \
    --shielded-secure-boot --shielded-vtpm --shielded-integrity-monitoring \
    --metadata enable-oslogin=TRUE
fi
cat <<EOF

$NAME is in $PROJECT ($ZONE), billed by the hour while it runs. Next (README.md, Google Cloud):
  .nightbot/machine/gcp/on-machine.sh .nightbot/machine/common/setup.sh --cloud gcp
EOF
