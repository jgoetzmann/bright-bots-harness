#!/bin/bash
# shellcheck source-path=SCRIPTDIR
# Deploys the starter as an Azure Function (Python 3.12, v2 programming model: function_app.py and
# starter.py, with ../starter/core.py) on a timer every five minutes: when the VM is deallocated and
# a job of the night bot's workflow waits for one of its runners, it starts the VM. Runs ON YOUR
# COMPUTER with the Azure CLI signed in as someone who may create role definitions and assignments,
# and python3 (to zip). Idempotent: run it again after changing the code or a setting.
#
#   REPO=OWNER/REPO .nightbot/machine/azure/deploy-starter.sh
#   REPO=OWNER/REPO STARTER_GITHUB_TOKEN=github_pat_… .nightbot/machine/azure/deploy-starter.sh   # private repo
#
# It makes, in RESOURCE_GROUP: a storage account (the Functions runtime's own), the function app on
# the Flex Consumption plan (else, where Flex is not offered, the Consumption plan), with a
# system-assigned managed identity whose only role may read this VM and start it; then deploys the
# code as a zip, built remotely (pip installs requirements.txt there).
#
# Env: REPO (OWNER/NAME, required; with gh signed in, the repository's id is stored too, which
# survives a rename), RESOURCE_GROUP, LOCATION, NAME (env.sh), STARTER_NAME (the function app,
# unique in all of Azure: $NAME-starter-<a number from the subscription and group>), STORAGE (its
# storage account: nbstart<that number>), PLAN (flex, consumption, or empty to try flex first),
# STARTER_GITHUB_TOKEN (a fine-grained token with read access to Actions on the repository;
# required for a private one; a deploy without it keeps the one set before), WORKFLOWS,
# LABEL_PREFIX, RUNNER_LABELS (../starter/core.py).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=env.sh
. "$here/env.sh"
REPO="${REPO:?REPO=OWNER/NAME, the repository whose jobs wake the machine}"
PLAN="${PLAN:-}"
rg=(--resource-group "$RESOURCE_GROUP")
vm_id="$(az vm show "${rg[@]}" -n "$NAME" --query id -o tsv)"
subscription="$(az account show --query id -o tsv)"
number="$(printf '%s' "$subscription/$RESOURCE_GROUP/$NAME" | cksum | cut -d' ' -f1)"
APP="${STARTER_NAME:-$NAME-starter-$number}"
STORAGE="${STORAGE:-nbstart$number}"
repo_id="${REPO_ID:-$(gh api "repos/$REPO" --jq .id 2>/dev/null || true)}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

az storage account show "${rg[@]}" -n "$STORAGE" --output none 2>/dev/null \
  || az storage account create "${rg[@]}" -n "$STORAGE" -l "$LOCATION" --sku Standard_LRS \
       --kind StorageV2 --min-tls-version TLS1_2 --allow-blob-public-access false --output none
if ! az functionapp show "${rg[@]}" -n "$APP" --output none 2>/dev/null; then
  if [ "$PLAN" != consumption ] && az functionapp create "${rg[@]}" -n "$APP" --storage-account "$STORAGE" \
      --flexconsumption-location "$LOCATION" --runtime python --runtime-version 3.12 --output none; then
    echo "$APP: on the Flex Consumption plan"
  elif [ "$PLAN" = flex ]; then
    echo "$APP: the Flex Consumption plan refused it (above)" >&2; exit 1
  else
    echo "$APP: no Flex Consumption here; the Consumption plan instead" >&2
    az functionapp create "${rg[@]}" -n "$APP" --storage-account "$STORAGE" \
      --consumption-plan-location "$LOCATION" --os-type Linux --runtime python --runtime-version 3.12 \
      --functions-version 4 --output none
  fi
fi

# Its identity may read this VM and start it, nothing else.
principal="$(az functionapp identity assign "${rg[@]}" -n "$APP" --query principalId -o tsv)"
azure_role "Nightbot start ${subscription:0:8}/$RESOURCE_GROUP/$NAME" "$principal" "$vm_id" \
  Microsoft.Compute/virtualMachines/read Microsoft.Compute/virtualMachines/instanceView/read \
  Microsoft.Compute/virtualMachines/start/action

settings=("VM_ID=$vm_id" "REPO=$REPO")
[ -z "$repo_id" ] || settings+=("REPO_ID=$repo_id")
[ -z "${STARTER_GITHUB_TOKEN:-}" ] || settings+=("GITHUB_TOKEN=$STARTER_GITHUB_TOKEN")
[ -z "${WORKFLOWS:-}" ] || settings+=("WORKFLOWS=$WORKFLOWS")
[ -z "${LABEL_PREFIX:-}" ] || settings+=("LABEL_PREFIX=$LABEL_PREFIX")
[ -z "${RUNNER_LABELS:-}" ] || settings+=("RUNNER_LABELS=$RUNNER_LABELS")
az functionapp config appsettings set "${rg[@]}" -n "$APP" --settings "${settings[@]}" --output none

cp "$here/function_app.py" "$here/starter.py" "$here/host.json" "$here/requirements.txt" \
  "$here/../starter/core.py" "$work/"
(cd "$work" && python3 -m zipfile -c app.zip function_app.py starter.py core.py host.json requirements.txt)
az functionapp deployment source config-zip "${rg[@]}" -n "$APP" --src "$work/app.zip" \
  --build-remote true --output none
echo "deployed $APP for $NAME; it looks every five minutes."
echo "its log: the function app's Log stream or Application Insights in the portal"
