#!/bin/bash
# shellcheck source-path=SCRIPTDIR
# Deploys the starter (main.py, with ../starter/core.py) as a Cloud Run function (2nd gen, Python
# 3.12, HTTP, no unauthenticated calls) that Cloud Scheduler calls every five minutes: when the VM
# is stopped and a job of the night bot's workflow waits for one of its runners, it starts the VM.
# Runs ON YOUR COMPUTER with gcloud signed in as someone who may enable APIs, create service
# accounts, custom roles, functions and scheduler jobs (Owner will do). Idempotent: run it again
# after changing the code or a setting.
#
#   REPO=OWNER/REPO .nightbot/machine/gcp/deploy-starter.sh
#   REPO=OWNER/REPO STARTER_GITHUB_TOKEN=github_pat_… .nightbot/machine/gcp/deploy-starter.sh   # private repo
#
# Two service accounts: STARTER_NAME, the function's own, whose only role (a custom one, ROLE_ID:
# compute.instances.get and compute.instances.start) is granted on this one VM; and
# STARTER_NAME-cron, Scheduler's, which may only invoke the function (roles/run.invoker on it).
# If the build fails on a permission, the project's build service account lacks
# roles/cloudbuild.builds.builder (README.md, Google Cloud).
#
# Env: REPO (OWNER/NAME, required; with gh signed in, the repository's id is stored too, which
# survives a rename), PROJECT, ZONE, REGION, NAME (env.sh), STARTER_NAME ($NAME-starter; at most
# 25 characters), ROLE_ID (nightbotStarter), STARTER_GITHUB_TOKEN (a fine-grained token with read
# access to Actions on the repository; required for a private one; a deploy without it keeps the
# one set before), WORKFLOWS, LABEL_PREFIX, RUNNER_LABELS (../starter/core.py).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=env.sh
. "$here/env.sh"
REPO="${REPO:?REPO=OWNER/NAME, the repository whose jobs wake the machine}"
FUNCTION="${STARTER_NAME:-$NAME-starter}"
ROLE_ID="${ROLE_ID:-nightbotStarter}"
[[ "$FUNCTION" =~ ^[a-z][a-z0-9-]{4,24}$ ]] || { echo "STARTER_NAME=$FUNCTION: 6 to 25 lower-case letters, digits and -" >&2; exit 2; }
runner_sa="$FUNCTION@$PROJECT.iam.gserviceaccount.com"
cron_sa="$FUNCTION-cron@$PROJECT.iam.gserviceaccount.com"
repo_id="${REPO_ID:-$(gh api "repos/$REPO" --jq .id 2>/dev/null || true)}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

g services enable cloudfunctions.googleapis.com run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com cloudscheduler.googleapis.com iam.googleapis.com
g iam service-accounts describe "$runner_sa" >/dev/null 2>&1 \
  || g iam service-accounts create "$FUNCTION" --display-name "Night bot starter: starts $NAME"
g iam service-accounts describe "$cron_sa" >/dev/null 2>&1 \
  || g iam service-accounts create "$FUNCTION-cron" --display-name "Night bot starter's scheduler"
g iam roles describe "$ROLE_ID" >/dev/null 2>&1 \
  || g iam roles create "$ROLE_ID" --title "Night bot starter" \
       --description "Read a VM and start it, nothing else" \
       --permissions compute.instances.get,compute.instances.start --stage GA
# A new service account takes a moment before IAM accepts it in a policy.
for i in $(seq 1 12); do
  g compute instances add-iam-policy-binding "$NAME" --zone "$ZONE" \
    --member "serviceAccount:$runner_sa" --role "projects/$PROJECT/roles/$ROLE_ID" >/dev/null && break
  [ "$i" != 12 ] || exit 1
  sleep 10
done

token="${STARTER_GITHUB_TOKEN:-}"
if [ -z "$token" ]; then
  token="$(g functions describe "$FUNCTION" --gen2 --region "$REGION" \
    --format 'value(serviceConfig.environmentVariables.GITHUB_TOKEN)' 2>/dev/null || true)"
fi
# JSON is YAML: the environment, commas and all.
jq -n --arg p "$PROJECT" --arg z "$ZONE" --arg i "$NAME" --arg r "$REPO" --arg id "$repo_id" \
  --arg t "$token" --arg w "${WORKFLOWS:-}" --arg x "${LABEL_PREFIX:-}" --arg l "${RUNNER_LABELS:-}" \
  '{PROJECT: $p, ZONE: $z, INSTANCE: $i, REPO: $r, REPO_ID: $id, GITHUB_TOKEN: $t, WORKFLOWS: $w,
    LABEL_PREFIX: $x, RUNNER_LABELS: $l} | with_entries(select(.value != ""))' > "$work/env.yaml"
mkdir "$work/src"
cp "$here/main.py" "$here/requirements.txt" "$here/../starter/core.py" "$work/src/"
g functions deploy "$FUNCTION" --gen2 --region "$REGION" --runtime python312 \
  --source "$work/src" --entry-point handler --trigger-http --no-allow-unauthenticated \
  --service-account "$runner_sa" --env-vars-file "$work/env.yaml" \
  --memory 256Mi --timeout 60s --max-instances 1
url="$(g functions describe "$FUNCTION" --gen2 --region "$REGION" --format 'value(serviceConfig.uri)')"
# A 2nd gen function is a Cloud Run service of the same name.
g run services add-iam-policy-binding "$FUNCTION" --region "$REGION" \
  --member "serviceAccount:$cron_sa" --role roles/run.invoker >/dev/null

job=(--location "$REGION" --schedule "*/5 * * * *" --uri "$url" --http-method POST
     --oidc-service-account-email "$cron_sa" --oidc-token-audience "$url" --attempt-deadline 120s)
if g scheduler jobs describe "$FUNCTION" --location "$REGION" >/dev/null 2>&1; then
  g scheduler jobs update http "$FUNCTION" "${job[@]}" >/dev/null
else
  g scheduler jobs create http "$FUNCTION" "${job[@]}" \
    --description "Start $NAME when a night bot job waits for its runners" >/dev/null
fi
g scheduler jobs run "$FUNCTION" --location "$REGION"
echo "deployed $FUNCTION for $NAME; it is called every five minutes."
echo "its log: gcloud functions logs read $FUNCTION --gen2 --region $REGION --project $PROJECT"
