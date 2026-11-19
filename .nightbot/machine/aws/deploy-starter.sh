#!/bin/bash
# shellcheck source-path=SCRIPTDIR
# Deploys the starter (starter.py with ../starter/core.py) as an AWS Lambda that EventBridge runs
# every five minutes: when the machine is stopped and a job of the night bot's workflow waits for
# one of its runners, it starts the machine. Runs ON YOUR COMPUTER with the AWS CLI v2 signed in,
# in the machine's Region, and python3 (to zip). Idempotent: run it again after changing the code
# or a setting.
#
#   REPO=OWNER/REPO .nightbot/machine/aws/deploy-starter.sh
#   REPO=OWNER/REPO STARTER_GITHUB_TOKEN=github_pat_… .nightbot/machine/aws/deploy-starter.sh   # private repo
#
# Its role may write its logs, describe instances, and start this one instance: nothing else.
# Env: REPO (OWNER/NAME, required; with gh signed in, the repository's id is stored too, which
# survives a rename), NAME (nightbot-box) or INSTANCE_ID, REGION (env.sh), STARTER_NAME
# ($NAME-starter: the function, its role and its rule), STARTER_GITHUB_TOKEN (a fine-grained token
# with read access to Actions on the repository; required for a private one; a deploy without it
# keeps the one deployed before), WORKFLOWS, LABEL_PREFIX, RUNNER_LABELS (../starter/core.py).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=env.sh
. "$here/env.sh"
REPO="${REPO:?REPO=OWNER/NAME, the repository whose jobs wake the machine}"
instance="$(aws_instance)"
FUNCTION="${STARTER_NAME:-$NAME-starter}"
repo_id="${REPO_ID:-$(gh api "repos/$REPO" --jq .id 2>/dev/null || true)}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
account="$(aws sts get-caller-identity --query Account --output text)"

# The function's role: its logs, describing instances, and starting this one instance only.
if ! aws iam get-role --role-name "$FUNCTION" >/dev/null 2>&1; then
  aws iam create-role --role-name "$FUNCTION" --assume-role-policy-document '{"Version":"2012-10-17",
    "Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},
    "Action":"sts:AssumeRole"}]}' >/dev/null
  aws iam attach-role-policy --role-name "$FUNCTION" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
fi
aws iam put-role-policy --role-name "$FUNCTION" --policy-name start-the-machine \
  --policy-document "$(jq -cn --arg arn "arn:aws:ec2:$REGION:$account:instance/$instance" '{
    Version: "2012-10-17",
    Statement: [{Effect: "Allow", Action: "ec2:StartInstances", Resource: $arn},
                {Effect: "Allow", Action: "ec2:DescribeInstances", Resource: "*"}]}')"

cp "$here/starter.py" "$here/../starter/core.py" "$work/"
(cd "$work" && python3 -m zipfile -c starter.zip starter.py core.py)
token="${STARTER_GITHUB_TOKEN:-}"
exists=""
if aws lambda get-function --function-name "$FUNCTION" >/dev/null 2>&1; then
  exists=1
  if [ -z "$token" ]; then
    token="$(aws lambda get-function-configuration --function-name "$FUNCTION" \
      --query Environment.Variables.GITHUB_TOKEN --output text)"
    [ "$token" != None ] || token=""
  fi
fi
environment="$(jq -cn --arg i "$instance" --arg r "$REPO" --arg id "$repo_id" --arg t "$token" \
  --arg w "${WORKFLOWS:-}" --arg p "${LABEL_PREFIX:-}" --arg l "${RUNNER_LABELS:-}" '{Variables:
  ({INSTANCE_ID: $i, REPO: $r, REPO_ID: $id, GITHUB_TOKEN: $t, WORKFLOWS: $w, LABEL_PREFIX: $p,
    RUNNER_LABELS: $l} | with_entries(select(.value != "")))}')"
if [ -n "$exists" ]; then
  aws lambda update-function-code --function-name "$FUNCTION" \
    --zip-file "fileb://$work/starter.zip" >/dev/null
  aws lambda wait function-updated --function-name "$FUNCTION"
  aws lambda update-function-configuration --function-name "$FUNCTION" --environment "$environment" \
    --runtime python3.13 --timeout 60 --memory-size 256 >/dev/null
  aws lambda wait function-updated --function-name "$FUNCTION"
else
  # A new role takes a few seconds before Lambda may assume it.
  for attempt in 1 2 3 4 5 6; do
    if aws lambda create-function --function-name "$FUNCTION" --runtime python3.13 \
        --handler starter.handler --role "arn:aws:iam::$account:role/$FUNCTION" \
        --zip-file "fileb://$work/starter.zip" --timeout 60 --memory-size 256 \
        --environment "$environment" \
        --description "Starts $NAME when a night bot job waits for its runners" >/dev/null 2>"$work/err"; then
      break
    fi
    if ! grep -q 'cannot be assumed' "$work/err" || [ "$attempt" = 6 ]; then cat "$work/err" >&2; exit 1; fi
    sleep 10
  done
  aws lambda wait function-active --function-name "$FUNCTION"
fi
aws logs create-log-group --log-group-name "/aws/lambda/$FUNCTION" >/dev/null 2>&1 || true
aws logs put-retention-policy --log-group-name "/aws/lambda/$FUNCTION" --retention-in-days 14

# Every five minutes. A rule's target needs only a permission on the function, no role.
rule_arn="$(aws events put-rule --name "$FUNCTION" --schedule-expression "rate(5 minutes)" \
  --description "Look for night bot jobs waiting for $NAME" --query RuleArn --output text)"
aws lambda add-permission --function-name "$FUNCTION" --statement-id every-five-minutes \
  --action lambda:InvokeFunction --principal events.amazonaws.com --source-arn "$rule_arn" \
  >/dev/null 2>&1 || true
function_arn="$(aws lambda get-function --function-name "$FUNCTION" \
  --query Configuration.FunctionArn --output text)"
aws events put-targets --rule "$FUNCTION" --targets "Id=starter,Arn=$function_arn" >/dev/null

aws lambda invoke --function-name "$FUNCTION" "$work/out.json" >/dev/null
echo "deployed $FUNCTION for $instance; a test call returned: $(cat "$work/out.json")"
echo "its log: aws logs tail /aws/lambda/$FUNCTION --follow"
