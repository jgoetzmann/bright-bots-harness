#!/bin/bash
# shellcheck source-path=SCRIPTDIR
# Creates a monthly cost budget on the night bot's resource group (the VM, its disk, the starter)
# with email alerts at 80% and 100% of it (actual spend). Runs ON YOUR COMPUTER with the Azure CLI
# signed in. Run it again to change the amount or the address. A budget only warns: nothing stops.
# It PUTs the budget, alerts and all, in one call to Azure Resource Manager (az rest; the
# Microsoft.Consumption/budgets resource).
#
#   BUDGET_USD=40 ALERT_EMAIL=you@example.com .nightbot/machine/azure/budget.sh
#
# Env: BUDGET_USD (required, in the billing currency), ALERT_EMAIL (required), BUDGET_NAME
# ($NAME, i.e. nightbot-box), RESOURCE_GROUP (env.sh).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=env.sh
. "$here/env.sh"
BUDGET_USD="${BUDGET_USD:?BUDGET_USD, the monthly budget, e.g. 40}"
ALERT_EMAIL="${ALERT_EMAIL:?ALERT_EMAIL, the address the alerts go to}"
BUDGET_NAME="${BUDGET_NAME:-$NAME}"
[[ "$BUDGET_USD" =~ ^[0-9]+(\.[0-9]+)?$ ]] || { echo "BUDGET_USD=$BUDGET_USD: a number" >&2; exit 2; }
subscription="$(az account show --query id -o tsv)"
url="https://management.azure.com/subscriptions/$subscription/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.Consumption/budgets/$BUDGET_NAME?api-version=2023-05-01"
# A budget keeps the start date it was made with: the first of a month.
start="$(az rest --method get --url "$url" --query properties.timePeriod.startDate -o tsv 2>/dev/null || true)"
[ -n "$start" ] || start="$(date -u +%Y-%m-01T00:00:00Z)"
body="$(jq -cn --argjson a "$BUDGET_USD" --arg s "$start" --arg e "$ALERT_EMAIL" '{properties: {
  category: "Cost", amount: $a, timeGrain: "Monthly", timePeriod: {startDate: $s},
  notifications: ([80, 100] | map({key: "Actual_GreaterThanOrEqualTo_\(.)_Percent", value: {
    enabled: true, operator: "GreaterThanOrEqualTo", threshold: ., contactEmails: [$e]}})
    | from_entries)}}')"
az rest --method put --url "$url" --body "$body" --output none
echo "budget $BUDGET_NAME on $RESOURCE_GROUP: $BUDGET_USD a month; $ALERT_EMAIL hears at 80% and 100%"
