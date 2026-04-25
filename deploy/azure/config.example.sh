# Copy this file to config.sh and fill in names.
# config.sh is gitignored by deploy/azure/.gitignore.

# --- Identity ---
export AZ_SUBSCRIPTION=""                  # leave blank to use the az CLI's default
export GITHUB_REPO="vijayshankarrealdeal/ticket"   # owner/repo for OIDC federated cred + `gh` sync

# --- Location / naming ---
# IMPORTANT: pick a stable suffix once and reuse it across the
# globally-unique names below. $RANDOM re-rolls every shell invocation,
# so using it directly creates a new orphan ACR/KV/PG on every retry.
export SUFFIX="$(date +%s | tail -c 5)"     # replace with any 4-6 char tag
export LOCATION="eastus"
export RG="workspacex-rg"
export ACR_NAME="workspacexacr${SUFFIX}"   # globally unique, 5-50 alphanumeric
export LAW_NAME="workspacex-logs"
export KV_NAME="workspacex-kv-${SUFFIX}"   # globally unique

# --- Data plane ---
export PG_SERVER="workspacex-pg-${SUFFIX}" # globally unique
export PG_ADMIN="workspacex"
export PG_VERSION="16"
export PG_SKU="Standard_B1ms"              # cheapest burstable; bump for prod
export PG_STORAGE_GB="32"

export REDIS_NAME="workspacex-redis-${SUFFIX}"
export REDIS_SKU="Basic"
export REDIS_SIZE="C0"                     # C0 = 250MB Basic

export STORAGE_ACCOUNT="wxstor${SUFFIX}"   # lowercase, 3-24 chars, globally unique
export STORAGE_CONTAINER="workspacex"

# --- Compute ---
export CAE_NAME="workspacex-env"           # Container Apps environment
export CA_API="workspacex-api"
export CA_WORKER="workspacex-worker"
export CA_LANGFLOW="workspacex-langflow"
export CA_MINIO="workspacex-minio"         # MinIO shim — remove once s3.py uses Blob SAS

# --- SPA ---
export SWA_NAME="workspacex-web"
# Free SKU is fine until you bind workspacex.com — bump to Standard then.
export SWA_SKU="Free"
# Static Web Apps is only available in:
#   westus2, centralus, eastus2, westeurope, eastasia
# Pick whichever is closest to your $LOCATION (compute can be elsewhere).
export SWA_LOCATION="eastus2"

# --- Custom domain ---
# Toggle: "0" = ship on Azure-provided FQDNs only (*.azurecontainerapps.io,
# *.azurestaticapps.net). "1" = also bind workspacex.com + subdomains.
# Switch to "1" any time later, then re-run:
#   bash deploy/azure/provision.sh domains-stage
#   bash deploy/azure/provision.sh domains-bind
export USE_CUSTOM_DOMAIN="0"

# Used only when USE_CUSTOM_DOMAIN="1". Subdomain plan:
#   workspacex.com / www.workspacex.com -> SPA
#   api.workspacex.com                  -> api Container App
#   flow.workspacex.com                 -> langflow Container App
export DOMAIN="workspacex.com"
export API_HOST="api.${DOMAIN}"
export LANGFLOW_HOST="flow.${DOMAIN}"
export SPA_APEX="${DOMAIN}"
export SPA_WWW="www.${DOMAIN}"
