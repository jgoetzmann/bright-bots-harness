# shellcheck shell=bash
# Sourced by the azure/ scripts, ON YOUR COMPUTER (az signed in; `az account set` picks the
# subscription): where the machine is, and one helper.
#   RESOURCE_GROUP  nightbot      everything here goes in it
#   LOCATION        eastus        the region (az account list-locations -o table)
#   NAME            nightbot-box  the VM, and the prefix of what belongs to it
RESOURCE_GROUP="${RESOURCE_GROUP:-nightbot}"
LOCATION="${LOCATION:-eastus}"
NAME="${NAME:-nightbot-box}"
[[ "$NAME" =~ ^[a-z][a-z0-9-]{0,40}$ ]] || { echo "NAME=$NAME: lower-case letters, digits and -" >&2; exit 2; }

# azure_role ROLE PRINCIPAL SCOPE ACTION…: a custom role ROLE allowing the ACTIONs (made once,
# assignable in the resource group), given to the managed identity PRINCIPAL at SCOPE (once). A new
# role or identity takes a minute or two to be seen, so the assignment is retried.
azure_role() {
  local role="$1" principal="$2" scope="$3" group i
  shift 3
  group="$(az group show -n "$RESOURCE_GROUP" --query id -o tsv)"
  if [ -z "$(az role definition list --name "$role" --custom-role-only true --query '[0].id' -o tsv)" ]; then
    az role definition create --role-definition "$(printf '%s\n' "$@" | jq -R . | jq -cs \
      --arg n "$role" --arg g "$group" '{Name: $n, IsCustom: true, Actions: ., NotActions: [],
        Description: "Night bot: only what its name says", AssignableScopes: [$g]}')" --output none
  fi
  if [ -n "$(az role assignment list --scope "$scope" \
      --query "[?principalId=='$principal' && roleDefinitionName=='$role'].id" -o tsv)" ]; then
    return 0
  fi
  for i in $(seq 1 18); do
    az role assignment create --assignee-object-id "$principal" --assignee-principal-type ServicePrincipal \
      --role "$role" --scope "$scope" --output none 2>/dev/null && return 0
    [ "$i" != 1 ] || echo "waiting for the new role to be seen" >&2
    sleep 10
  done
  echo "could not give $principal the role '$role' at $scope" >&2
  return 1
}
