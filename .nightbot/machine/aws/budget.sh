#!/bin/bash
# shellcheck source-path=SCRIPTDIR
# Creates a monthly cost budget for the AWS account with email alerts at 80% and 100% of it (actual
# spend). Runs ON YOUR COMPUTER with the AWS CLI v2 signed in to the account. Run it again to change
# the amount; the alerts are made once (to change the address: aws budgets delete-budget
# --account-id <id> --budget-name <name>, then run this again). A budget only warns: nothing stops.
# It covers the whole account, so give the night bot an account of its own to see its cost alone.
#
#   BUDGET_USD=40 ALERT_EMAIL=you@example.com .nightbot/machine/aws/budget.sh
#
# Env: BUDGET_USD (required), ALERT_EMAIL (required), BUDGET_NAME ($NAME, i.e. nightbot-box).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=env.sh
. "$here/env.sh"
BUDGET_USD="${BUDGET_USD:?BUDGET_USD, the monthly budget in US dollars, e.g. 40}"
ALERT_EMAIL="${ALERT_EMAIL:?ALERT_EMAIL, the address the alerts go to}"
BUDGET_NAME="${BUDGET_NAME:-$NAME}"
[[ "$BUDGET_USD" =~ ^[0-9]+(\.[0-9]+)?$ ]] || { echo "BUDGET_USD=$BUDGET_USD: a number" >&2; exit 2; }
account="$(aws sts get-caller-identity --query Account --output text)"
budget="$(jq -cn --arg n "$BUDGET_NAME" --arg a "$BUDGET_USD" \
  '{BudgetName: $n, BudgetLimit: {Amount: $a, Unit: "USD"}, TimeUnit: "MONTHLY", BudgetType: "COST"}')"
if aws budgets describe-budget --account-id "$account" --budget-name "$BUDGET_NAME" >/dev/null 2>&1; then
  aws budgets update-budget --account-id "$account" --new-budget "$budget"
  echo "budget $BUDGET_NAME: now \$$BUDGET_USD a month (its alerts are as they were)"
else
  alerts="$(jq -cn --arg e "$ALERT_EMAIL" '[80, 100] | map({
    Notification: {NotificationType: "ACTUAL", ComparisonOperator: "GREATER_THAN", Threshold: .,
                   ThresholdType: "PERCENTAGE"},
    Subscribers: [{SubscriptionType: "EMAIL", Address: $e}]})')"
  aws budgets create-budget --account-id "$account" --budget "$budget" \
    --notifications-with-subscribers "$alerts"
  echo "budget $BUDGET_NAME: \$$BUDGET_USD a month; $ALERT_EMAIL hears at 80% and 100%"
fi
