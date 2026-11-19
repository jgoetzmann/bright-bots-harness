#!/bin/bash
# shellcheck source-path=SCRIPTDIR
# Creates the night bot's machine on Azure: one VM, Ubuntu 24.04, with no public IP address and no
# inbound rule, that deallocates itself when idle. Runs ON YOUR COMPUTER with the Azure CLI signed
# in (az login; az account set --subscription …) as someone who may create role definitions and
# assignments (Owner, or Contributor plus User Access Administrator). Idempotent: what exists is
# kept, so run it again after a failure.
#
#   LOCATION=eastus .nightbot/machine/azure/provision.sh
#
# What it makes, in RESOURCE_GROUP: a network security group with no inbound rule (only Azure's
# defaults, which refuse the internet), a VNet and subnet behind it, and the VM: no public IP, no
# NSG of its own, a system-assigned managed identity, boot diagnostics (for the serial console, the
# way in for a person), and an admin user with no usable credential until you give it a password
# (README.md). The identity gets a custom role that may read and deallocate this VM, nothing else:
# the idle stop deallocates it, since an OS shutdown would leave it allocated and billed. Scripts
# reach it through Run Command (on-machine.sh), which the VM's Linux agent serves.
#
# Its way out to GitHub: OUTBOUND=default gives the subnet Azure's default outbound access (free;
# Azure calls it a legacy path and makes new subnets private by default). If the VM cannot reach
# GitHub (the last step checks), run this again with OUTBOUND=nat: a NAT gateway with a static
# public IP, about $35 a month whether the VM runs or not.
#
# Env, with defaults: RESOURCE_GROUP (nightbot), LOCATION (eastus), NAME (nightbot-box), SIZE
# (Standard_D2as_v5: 2 vCPUs, 8 GB), DISK_GB (100), DISK_SKU (StandardSSD_LRS; Premium_LRS is
# faster and dearer), ADMIN (nightbot-admin), OUTBOUND (default, or nat).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=env.sh
. "$here/env.sh"
SIZE="${SIZE:-Standard_D2as_v5}"
DISK_GB="${DISK_GB:-100}"
DISK_SKU="${DISK_SKU:-StandardSSD_LRS}"
ADMIN="${ADMIN:-nightbot-admin}"
OUTBOUND="${OUTBOUND:-default}"
case "$OUTBOUND" in default|nat) ;; *) echo "OUTBOUND=$OUTBOUND: default or nat" >&2; exit 2 ;; esac
rg=(--resource-group "$RESOURCE_GROUP")
vnet="$NAME-vnet" subnet="$NAME-subnet" nsg="$NAME-nsg"

az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none
az network nsg show "${rg[@]}" -n "$nsg" --output none 2>/dev/null \
  || az network nsg create "${rg[@]}" -n "$nsg" -l "$LOCATION" --output none
az network vnet show "${rg[@]}" -n "$vnet" --output none 2>/dev/null \
  || az network vnet create "${rg[@]}" -n "$vnet" -l "$LOCATION" --address-prefixes 10.42.0.0/24 --output none
if ! az network vnet subnet show "${rg[@]}" --vnet-name "$vnet" -n "$subnet" --output none 2>/dev/null; then
  # Default outbound access can only be chosen when the subnet is made.
  az network vnet subnet create "${rg[@]}" --vnet-name "$vnet" -n "$subnet" \
    --address-prefixes 10.42.0.0/26 --network-security-group "$nsg" \
    --default-outbound-access "$([ "$OUTBOUND" = default ] && echo true || echo false)" --output none
fi
if [ "$OUTBOUND" = nat ]; then
  az network public-ip show "${rg[@]}" -n "$NAME-nat-ip" --output none 2>/dev/null \
    || az network public-ip create "${rg[@]}" -n "$NAME-nat-ip" -l "$LOCATION" --sku Standard \
         --allocation-method Static --output none
  az network nat gateway show "${rg[@]}" -n "$NAME-nat" --output none 2>/dev/null \
    || az network nat gateway create "${rg[@]}" -n "$NAME-nat" -l "$LOCATION" \
         --public-ip-addresses "$NAME-nat-ip" --output none
  az network vnet subnet update "${rg[@]}" --vnet-name "$vnet" -n "$subnet" --nat-gateway "$NAME-nat" --output none
fi

if ! az vm show "${rg[@]}" -n "$NAME" --output none 2>/dev/null; then
  # The admin user's SSH key is thrown away: nothing reaches port 22, and the serial console
  # takes a password you set when you need it (README.md).
  keys="$(mktemp -d)"
  trap 'rm -rf "$keys"' EXIT
  ssh-keygen -q -t rsa -b 4096 -N '' -C "$NAME" -f "$keys/key"
  az vm create "${rg[@]}" -n "$NAME" -l "$LOCATION" --image Canonical:ubuntu-24_04-lts:server:latest \
    --size "$SIZE" --os-disk-size-gb "$DISK_GB" --storage-sku "$DISK_SKU" \
    --vnet-name "$vnet" --subnet "$subnet" --nsg "" --public-ip-address "" \
    --assign-identity '[system]' --admin-username "$ADMIN" --ssh-key-values "$keys/key.pub" \
    --output none
fi
az vm boot-diagnostics enable "${rg[@]}" -n "$NAME" --output none

# The VM may deallocate itself (the idle stop) and nothing else.
vm_id="$(az vm show "${rg[@]}" -n "$NAME" --query id -o tsv)"
principal="$(az vm show "${rg[@]}" -n "$NAME" --query identity.principalId -o tsv)"
[ -n "$principal" ] || principal="$(az vm identity assign "${rg[@]}" -n "$NAME" --query systemAssignedIdentity -o tsv)"
subscription="$(az account show --query id -o tsv)"
azure_role "Nightbot deallocate ${subscription:0:8}/$RESOURCE_GROUP/$NAME" "$principal" "$vm_id" \
  Microsoft.Compute/virtualMachines/read Microsoft.Compute/virtualMachines/deallocate/action

# Run Command answers, and the VM reaches GitHub.
if [ "$(az vm get-instance-view "${rg[@]}" -n "$NAME" \
    --query "instanceView.statuses[?starts_with(code, 'PowerState/')].code | [0]" -o tsv)" != PowerState/running ]; then
  az vm start "${rg[@]}" -n "$NAME" --output none
fi
reach="$(az vm run-command invoke "${rg[@]}" -n "$NAME" --command-id RunShellScript \
  --scripts "curl -fsS -o /dev/null -m 20 https://api.github.com && echo GITHUB-REACHABLE" \
  --query 'value[0].message' -o tsv 2>&1 || true)"
if [[ "$reach" == *GITHUB-REACHABLE* ]]; then
  echo "$NAME is running in $RESOURCE_GROUP ($LOCATION), reaches GitHub, and is billed until it deallocates."
  echo "Next (README.md, Azure): .nightbot/machine/azure/on-machine.sh .nightbot/machine/common/setup.sh --cloud azure"
else
  echo "$NAME runs, but Run Command did not show it reaching GitHub:" >&2
  printf '%s\n' "$reach" | tail -5 >&2
  echo "If the subnet has no way out, run this again with OUTBOUND=nat." >&2
  exit 1
fi
