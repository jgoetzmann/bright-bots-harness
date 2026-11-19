#!/bin/bash
# shellcheck source-path=SCRIPTDIR
# Creates a monthly budget for the project on its billing account, with alerts at 80% and 100% of
# it (actual spend). Runs ON YOUR COMPUTER with gcloud signed in as someone who may manage the
# billing account's budgets (Billing Account Administrator, or Costs Manager). Run it again to
# change the amount; the alerts are set when it is made (to change them, delete the budget in the
# console's Budgets & alerts and run this again). A budget only warns: nothing stops. The alerts
# go by email to the billing account's administrators and users; with ALERT_EMAIL, to that address
# too (through a Cloud Monitoring email channel in the project, made once, with `gcloud beta`).
#
#   BILLING_ACCOUNT=012345-6789AB-CDEF01 BUDGET_USD=40 .nightbot/machine/gcp/budget.sh
#
# Env: BILLING_ACCOUNT (required: gcloud billing accounts list), BUDGET_USD (required, in the
# billing account's currency), ALERT_EMAIL (optional), BUDGET_NAME ($NAME, i.e. nightbot-box),
# PROJECT (env.sh).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=env.sh
. "$here/env.sh"
BILLING_ACCOUNT="${BILLING_ACCOUNT:?BILLING_ACCOUNT, the billing account id (gcloud billing accounts list)}"
BUDGET_USD="${BUDGET_USD:?BUDGET_USD, the monthly budget, e.g. 40}"
BUDGET_NAME="${BUDGET_NAME:-$NAME}"
[[ "$BUDGET_USD" =~ ^[0-9]+(\.[0-9]+)?$ ]] || { echo "BUDGET_USD=$BUDGET_USD: a number" >&2; exit 2; }
g services enable billingbudgets.googleapis.com monitoring.googleapis.com
number="$(g projects describe "$PROJECT" --format 'value(projectNumber)')"
amount=(--budget-amount "${BUDGET_USD}USD")
rules=(--threshold-rule percent=0.8 --threshold-rule percent=1.0)
if [ -n "${ALERT_EMAIL:-}" ]; then
  channel="$(g beta monitoring channels list --filter "type=\"email\" AND labels.email_address=\"$ALERT_EMAIL\"" \
    --format 'value(name)' | head -1)"
  [ -n "$channel" ] || channel="$(g beta monitoring channels create --type email \
    --display-name "Night bot budget: $ALERT_EMAIL" --channel-labels "email_address=$ALERT_EMAIL" \
    --format 'value(name)')"
  rules+=(--notifications-rule-monitoring-notification-channels "$channel")
fi
existing="$(gcloud billing budgets list --billing-account "$BILLING_ACCOUNT" \
  --filter "displayName=\"$BUDGET_NAME\"" --format 'value(name)' | head -1)"
if [ -n "$existing" ]; then
  gcloud billing budgets update "$existing" --billing-account "$BILLING_ACCOUNT" "${amount[@]}" >/dev/null
  echo "budget $BUDGET_NAME: now $BUDGET_USD a month (its alerts are as they were)"
else
  gcloud billing budgets create --billing-account "$BILLING_ACCOUNT" --display-name "$BUDGET_NAME" \
    --filter-projects "projects/$number" "${amount[@]}" "${rules[@]}" >/dev/null
  echo "budget $BUDGET_NAME on $PROJECT: $BUDGET_USD a month; alerts at 80% and 100%"
fi
