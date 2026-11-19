# shellcheck shell=bash
# Sourced by the gcp/ scripts, ON YOUR COMPUTER (gcloud signed in): where the machine is.
#   PROJECT  the project: PROJECT, else gcloud's (gcloud config set project …)
#   ZONE     us-central1-a
#   REGION   the zone's region
#   NAME     nightbot-box: the VM, and the prefix of what belongs to it
PROJECT="${PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}"
[ -n "$PROJECT" ] || { echo "set PROJECT (or gcloud config set project …)" >&2; exit 2; }
ZONE="${ZONE:-us-central1-a}"
REGION="${REGION:-${ZONE%-*}}"
NAME="${NAME:-nightbot-box}"
[[ "$NAME" =~ ^[a-z][a-z0-9-]{0,40}$ ]] || { echo "NAME=$NAME: lower-case letters, digits and -" >&2; exit 2; }
g() { gcloud --project "$PROJECT" --quiet "$@"; }
