#!/usr/bin/env bash
# WorkspaceX Azure provisioner.
#
# Idempotent: re-runs skip resources that already exist. Each step is a
# function so you can comment one out or invoke it alone for a partial run:
#   source config.sh && source provision.sh && fn_postgres
#
# Prereqs: az, gh, docker (optional), jq, psql, a Container Apps-enabled sub.
# Run from repo root:
#   cp deploy/azure/config.example.sh deploy/azure/config.sh  # edit names
#   cp .env.example .env                                       # fill app secrets
#   bash deploy/azure/provision.sh

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"

# ---------- config ----------
if [[ ! -f "$HERE/config.sh" ]]; then
  echo "Missing $HERE/config.sh — copy config.example.sh and fill it in." >&2
  exit 1
fi
# shellcheck source=/dev/null
source "$HERE/config.sh"

# Load app secrets from repo-root .env if present. These land in Key Vault
# and Container Apps secrets, never in env vars on the runner.
if [[ -f "$REPO_ROOT/.env" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$REPO_ROOT/.env"
  set +a
fi

: "${LOCATION:?}"; : "${RG:?}"; : "${ACR_NAME:?}"
: "${GITHUB_REPO:?}"

USE_CUSTOM_DOMAIN="${USE_CUSTOM_DOMAIN:-0}"
if [[ "$USE_CUSTOM_DOMAIN" == "1" ]]; then
  : "${DOMAIN:?}"; : "${API_HOST:?}"; : "${LANGFLOW_HOST:?}"
  : "${SPA_APEX:?}"; : "${SPA_WWW:?}"
fi

# Public URLs (CORS, SPA build, Langflow). Resolved lazily once the
# resources exist — see compute_public_urls below.
PUBLIC_API_URL=""
PUBLIC_LANGFLOW_URL=""
PUBLIC_SPA_URLS=""

# Resolves canonical public URLs based on USE_CUSTOM_DOMAIN. Safe to call
# repeatedly; tolerates resources not existing yet (leaves vars empty).
compute_public_urls() {
  CAE_DOMAIN="$(az containerapp env show -g "$RG" -n "$CAE_NAME" \
    --query properties.defaultDomain -o tsv 2>/dev/null || echo '')"
  SWA_HOSTNAME="$(az staticwebapp show -g "$RG" -n "$SWA_NAME" \
    --query defaultHostname -o tsv 2>/dev/null || echo '')"

  if [[ "$USE_CUSTOM_DOMAIN" == "1" ]]; then
    PUBLIC_API_URL="https://${API_HOST}"
    PUBLIC_LANGFLOW_URL="https://${LANGFLOW_HOST}"
    PUBLIC_SPA_URLS="https://${SPA_APEX},https://${SPA_WWW}"
  else
    [[ -n "$CAE_DOMAIN" ]]    && PUBLIC_API_URL="https://${CA_API}.${CAE_DOMAIN}"
    [[ -n "$CAE_DOMAIN" ]]    && PUBLIC_LANGFLOW_URL="https://${CA_LANGFLOW}.${CAE_DOMAIN}"
    [[ -n "$SWA_HOSTNAME" ]]  && PUBLIC_SPA_URLS="https://${SWA_HOSTNAME}"
  fi
}

# --- Persistent secret state ---
# Generated secrets (PG admin password, Langflow superuser password, etc.)
# must survive across runs of this script — otherwise every retry mints
# new values that won't match what's already stored on the server.
# We keep them in a gitignored state file. First run creates it; later
# runs source it and reuse the same values.
STATE_DIR="$HERE/.state"
STATE_FILE="$STATE_DIR/secrets.env"
mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR"
if [[ -f "$STATE_FILE" ]]; then
  chmod 600 "$STATE_FILE"
  set -a; source "$STATE_FILE"; set +a
fi

# Sets $key to the existing env value if any; otherwise generates and
# appends to STATE_FILE so subsequent runs reuse it.
load_or_generate() {
  local key="$1" generator="$2"
  if [[ -z "${!key:-}" ]]; then
    local val
    val="$(eval "$generator")"
    printf -v "$key" '%s' "$val"
    export "$key"
    printf '%s=%q\n' "$key" "$val" >> "$STATE_FILE"
    chmod 600 "$STATE_FILE"
  fi
}

load_or_generate PG_PASSWORD                'openssl rand -base64 32 | tr -d "=+/" | head -c 32'
load_or_generate LANGFLOW_SUPERUSER_PASSWORD 'openssl rand -base64 32 | tr -d "=+/" | head -c 32'
load_or_generate WORKSPACEX_SECRET_KEY       'openssl rand -hex 32'
load_or_generate WORKSPACEX_INTERNAL_KEY     'openssl rand -hex 32'
LANGFLOW_SUPERUSER="${LANGFLOW_SUPERUSER:-admin}"

# ---------- helpers ----------
log()  { printf "\n\033[1;34m==>\033[0m %s\n" "$*"; }
skip() { printf "    \033[2m(exists, skipping)\033[0m %s\n" "$*"; }

az_account() {
  if [[ -n "${AZ_SUBSCRIPTION:-}" ]]; then
    az account set --subscription "$AZ_SUBSCRIPTION"
  fi
  SUB_ID="$(az account show --query id -o tsv)"
  TENANT_ID="$(az account show --query tenantId -o tsv)"
  log "Subscription: $SUB_ID  |  Tenant: $TENANT_ID"
}

# ---------- steps ----------
fn_rg() {
  log "Resource group: $RG"
  if az group show -n "$RG" >/dev/null 2>&1; then skip "$RG"; return; fi
  az group create -n "$RG" -l "$LOCATION" -o none
}

fn_acr() {
  log "ACR: $ACR_NAME"
  if az acr show -n "$ACR_NAME" >/dev/null 2>&1; then skip "$ACR_NAME"
  else az acr create -g "$RG" -n "$ACR_NAME" --sku Basic --admin-enabled false -o none
  fi
}

fn_law() {
  log "Log Analytics: $LAW_NAME"
  if az monitor log-analytics workspace show -g "$RG" -n "$LAW_NAME" >/dev/null 2>&1; then
    skip "$LAW_NAME"
  else
    az monitor log-analytics workspace create -g "$RG" -n "$LAW_NAME" -l "$LOCATION" -o none
  fi
  LAW_ID="$(az monitor log-analytics workspace show -g "$RG" -n "$LAW_NAME" --query customerId -o tsv)"
  LAW_KEY="$(az monitor log-analytics workspace get-shared-keys -g "$RG" -n "$LAW_NAME" --query primarySharedKey -o tsv)"
}

fn_keyvault() {
  log "Key Vault: $KV_NAME"
  if az keyvault show -n "$KV_NAME" >/dev/null 2>&1; then skip "$KV_NAME"
  else
    az keyvault create -g "$RG" -n "$KV_NAME" -l "$LOCATION" \
      --enable-rbac-authorization true -o none
  fi
  ME_OID="$(az ad signed-in-user show --query id -o tsv)"
  KV_ID="$(az keyvault show -n "$KV_NAME" --query id -o tsv)"
  az role assignment create --assignee "$ME_OID" \
    --role "Key Vault Secrets Officer" \
    --scope "$KV_ID" \
    -o none 2>/dev/null || true

  # Wait for the role assignment to propagate through Azure's auth backplane.
  # Usually 10-60s; rarely up to 5 min. The Key Vault stash is only a backup
  # of secrets — the live app secrets go directly into Container Apps later
  # (fn_containerapps), so if KV won't accept writes we warn and continue
  # rather than block the whole provision. Set SKIP_KEYVAULT_SECRETS=1 to
  # bypass the wait entirely on retries.
  if [[ "${SKIP_KEYVAULT_SECRETS:-0}" == "1" ]]; then
    log "Skipping Key Vault secret stash (SKIP_KEYVAULT_SECRETS=1)"
    return 0
  fi

  log "Stashing secrets in Key Vault (waits for RBAC propagation)"
  local attempt=0 max_attempts=18   # 18 * 10s = 3 min
  # Use the first real secret as the propagation probe — avoids leaving a
  # soft-deleted "probe" secret that blocks future runs (KV soft-delete
  # holds the name for 90 days under default settings).
  local first_pair="pg-admin-password=$PG_PASSWORD"
  local first_k="${first_pair%%=*}" first_v="${first_pair#*=}"
  until az keyvault secret set --vault-name "$KV_NAME" \
        --name "$first_k" --value "$first_v" -o none 2>/dev/null; do
    attempt=$((attempt + 1))
    if (( attempt > max_attempts )); then
      cat >&2 <<MSG

WARNING: Key Vault RBAC did not propagate within 3 minutes — skipping
secret stash and continuing. App secrets are still set directly on the
Container Apps (in fn_containerapps), so this only affects the backup
copy in Key Vault.

To fix later (and re-stash):
  az role assignment create \\
    --assignee-object-id $ME_OID --assignee-principal-type User \\
    --role "Key Vault Administrator" --scope $KV_ID
  # then re-run — secrets in deploy/azure/.state/secrets.env will be reused:
  bash deploy/azure/provision.sh

MSG
      return 0
    fi
    sleep 10
  done

  # Once one set succeeds, the rest go through immediately.
  for pair in \
      "langflow-superuser-password=$LANGFLOW_SUPERUSER_PASSWORD" \
      "workspacex-secret-key=$WORKSPACEX_SECRET_KEY" \
      "workspacex-internal-key=$WORKSPACEX_INTERNAL_KEY"; do
    k="${pair%%=*}"; v="${pair#*=}"
    az keyvault secret set --vault-name "$KV_NAME" --name "$k" --value "$v" -o none
  done
}

fn_postgres() {
  log "Postgres Flexible Server: $PG_SERVER"
  if az postgres flexible-server show -g "$RG" -n "$PG_SERVER" >/dev/null 2>&1; then
    skip "$PG_SERVER"
    # Idempotently re-sync the admin password to whatever's in STATE_FILE.
    # Cheap and safe — Azure no-ops if the password is already this value
    # but resolves the "ran the script before STATE_FILE existed" case.
    log "Re-syncing Postgres admin password from local state"
    az postgres flexible-server update \
      -g "$RG" -n "$PG_SERVER" --admin-password "$PG_PASSWORD" -o none
  else
    az postgres flexible-server create \
      --resource-group "$RG" --name "$PG_SERVER" --location "$LOCATION" \
      --admin-user "$PG_ADMIN" --admin-password "$PG_PASSWORD" \
      --sku-name "$PG_SKU" --tier Burstable \
      --storage-size "$PG_STORAGE_GB" --version "$PG_VERSION" \
      --public-access 0.0.0.0 \
      --yes -o none
  fi

  # Allow all Azure services (good enough for Container Apps without VNet).
  az postgres flexible-server firewall-rule create \
    -g "$RG" -n "$PG_SERVER" -r AllowAllAzure \
    --start-ip-address 0.0.0.0 --end-ip-address 0.0.0.0 -o none 2>/dev/null || true

  # Open to the operator's public IP so the init psql below works.
  MY_IP="$(curl -s https://api.ipify.org)"
  az postgres flexible-server firewall-rule create \
    -g "$RG" -n "$PG_SERVER" -r "client-$(date +%s)" \
    --start-ip-address "$MY_IP" --end-ip-address "$MY_IP" -o none 2>/dev/null || true

  # Allow-list every extension our schema CREATEs. Azure Database for
  # PostgreSQL Flexible Server blocks `CREATE EXTENSION` unless the name
  # is on the `azure.extensions` server parameter — pgcrypto, citext and
  # pg_trgm are all required by backend/app/schema.sql; VECTOR is for the
  # opt-in pgvector migration. Setting the parameter restarts the server,
  # so we only set when the desired set isn't already present.
  REQUIRED_EXTS="VECTOR,PGCRYPTO,CITEXT,PG_TRGM"
  EXISTING="$(az postgres flexible-server parameter show \
    -g "$RG" -s "$PG_SERVER" --name azure.extensions --query value -o tsv 2>/dev/null || echo '')"
  NEEDS_UPDATE=0
  for ext in ${REQUIRED_EXTS//,/ }; do
    if [[ ",${EXISTING^^}," != *",${ext}," ]]; then NEEDS_UPDATE=1; break; fi
  done
  if [[ "$NEEDS_UPDATE" == "1" ]]; then
    log "  enabling extensions: $REQUIRED_EXTS (server will restart)"
    az postgres flexible-server parameter set \
      -g "$RG" -s "$PG_SERVER" --name azure.extensions --value "$REQUIRED_EXTS" -o none
    # `parameter set` restarts the server. Wait for it to come back so the
    # psql calls below don't race the restart.
    for _ in $(seq 1 30); do
      state="$(az postgres flexible-server show -g "$RG" -n "$PG_SERVER" \
        --query state -o tsv 2>/dev/null || echo '')"
      [[ "$state" == "Ready" ]] && break
      sleep 5
    done
  fi

  PG_HOST="$PG_SERVER.postgres.database.azure.com"
  export PGPASSWORD="$PG_PASSWORD"

  # Create DBs idempotently.
  psql "host=$PG_HOST port=5432 dbname=postgres user=$PG_ADMIN sslmode=require" <<SQL
SELECT 'CREATE DATABASE workspacex OWNER $PG_ADMIN' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname='workspacex')\gexec
SELECT 'CREATE DATABASE langflow OWNER $PG_ADMIN'   WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname='langflow')\gexec
SQL
  # Pre-create the extensions while connected as the admin role. The app
  # boot still runs `CREATE EXTENSION IF NOT EXISTS …`, but doing it here
  # under the admin avoids a permissions edge case on first startup.
  psql "host=$PG_HOST port=5432 dbname=workspacex user=$PG_ADMIN sslmode=require" <<SQL
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;
SQL
  unset PGPASSWORD
}

fn_redis() {
  log "Redis: $REDIS_NAME"
  if az redis show -g "$RG" -n "$REDIS_NAME" >/dev/null 2>&1; then skip "$REDIS_NAME"
  else
    az redis create -g "$RG" -n "$REDIS_NAME" -l "$LOCATION" \
      --sku "$REDIS_SKU" --vm-size "$REDIS_SIZE" -o none
  fi
  REDIS_HOST="$(az redis show -g "$RG" -n "$REDIS_NAME" --query hostName -o tsv)"
  REDIS_KEY="$(az redis list-keys -g "$RG" -n "$REDIS_NAME" --query primaryKey -o tsv)"
  REDIS_URL="rediss://:${REDIS_KEY}@${REDIS_HOST}:6380"
}

fn_storage() {
  log "Storage account: $STORAGE_ACCOUNT"
  if az storage account show -g "$RG" -n "$STORAGE_ACCOUNT" >/dev/null 2>&1; then
    skip "$STORAGE_ACCOUNT"
  else
    az storage account create -g "$RG" -n "$STORAGE_ACCOUNT" -l "$LOCATION" \
      --sku Standard_LRS --allow-blob-public-access false -o none
  fi
  STORAGE_KEY="$(az storage account keys list -g "$RG" -n "$STORAGE_ACCOUNT" \
    --query '[0].value' -o tsv)"
  az storage container create --account-name "$STORAGE_ACCOUNT" \
    --account-key "$STORAGE_KEY" --name "$STORAGE_CONTAINER" -o none 2>/dev/null || true
}

fn_cae() {
  log "Container Apps environment: $CAE_NAME"
  if az containerapp env show -g "$RG" -n "$CAE_NAME" >/dev/null 2>&1; then
    skip "$CAE_NAME"
  else
    az containerapp env create -g "$RG" -n "$CAE_NAME" -l "$LOCATION" \
      --logs-workspace-id "$LAW_ID" \
      --logs-workspace-key "$LAW_KEY" -o none
  fi
}

fn_oidc() {
  log "GitHub OIDC app registration"
  APP_ID="$(az ad app list --display-name "gh-$RG-deployer" --query '[0].appId' -o tsv)"
  if [[ -z "$APP_ID" ]]; then
    APP_ID="$(az ad app create --display-name "gh-$RG-deployer" --query appId -o tsv)"
    az ad sp create --id "$APP_ID" -o none
  else
    skip "gh-$RG-deployer ($APP_ID)"
  fi

  SP_OID="$(az ad sp show --id "$APP_ID" --query id -o tsv)"

  az role assignment create --assignee-object-id "$SP_OID" --assignee-principal-type ServicePrincipal \
    --role AcrPush --scope "$(az acr show -n "$ACR_NAME" --query id -o tsv)" -o none 2>/dev/null || true
  az role assignment create --assignee-object-id "$SP_OID" --assignee-principal-type ServicePrincipal \
    --role Contributor --scope "/subscriptions/$SUB_ID/resourceGroups/$RG" -o none 2>/dev/null || true

  # One federated cred per trust path; skip if already present.
  for pair in "main:refs/heads/main" "pr:pull_request"; do
    cred_name="gh-${pair%%:*}"
    subject_suffix="${pair#*:}"
    if [[ "$subject_suffix" == "pull_request" ]]; then
      subject="repo:${GITHUB_REPO}:pull_request"
    else
      subject="repo:${GITHUB_REPO}:ref:${subject_suffix}"
    fi
    if az ad app federated-credential list --id "$APP_ID" --query "[?name=='$cred_name']" -o tsv | grep -q .; then
      skip "$cred_name"
      continue
    fi
    az ad app federated-credential create --id "$APP_ID" --parameters "{
      \"name\":\"$cred_name\",
      \"issuer\":\"https://token.actions.githubusercontent.com\",
      \"subject\":\"$subject\",
      \"audiences\":[\"api://AzureADTokenExchange\"]
    }" -o none
  done
}

fn_images() {
  log "Building initial images in ACR"
  az acr build --registry "$ACR_NAME" \
    --image "workspacex-backend:initial" --image "workspacex-backend:latest" \
    --file "$REPO_ROOT/backend/Dockerfile" "$REPO_ROOT/backend"
  az acr build --registry "$ACR_NAME" \
    --image "workspacex-langflow:initial" --image "workspacex-langflow:latest" \
    --file "$REPO_ROOT/deploy/langflow/Dockerfile" "$REPO_ROOT/deploy/langflow"
}

# Joins arguments with commas, dropping empty values. Avoids leading or
# trailing commas in CORS_ORIGINS when some hostnames aren't known yet.
_csv_join() {
  local out=""
  local part
  for part in "$@"; do
    [[ -z "$part" ]] && continue
    out="${out:+$out,}$part"
  done
  printf "%s" "$out"
}

# Shared env builder for api + worker. Call compute_public_urls() first so
# PUBLIC_*_URL are populated.
_backend_env_vars() {
  local role="$1"
  # `sslmode=require` (libpq-style) — asyncpg parses this as a connection
  # option. `ssl=require` makes asyncpg pass it through as a server_setting
  # and Postgres errors with "parameter \"ssl\" cannot be changed now".
  DATABASE_URL="postgresql+asyncpg://${PG_ADMIN}:${PG_PASSWORD}@${PG_HOST}:5432/workspacex?sslmode=require"
  local cors
  cors="$(_csv_join "$PUBLIC_SPA_URLS" "$PUBLIC_LANGFLOW_URL" "$PUBLIC_API_URL")"
  cat <<EOF
APP_ROLE=$role
PORT=8000
DATABASE_URL=$DATABASE_URL
REDIS_URL=$REDIS_URL
S3_ENDPOINT=https://${CA_MINIO}.internal
S3_PUBLIC_URL=https://${CA_MINIO}.${CAE_DOMAIN}
S3_BUCKET=workspacex
LANGFLOW_URL=https://${CA_LANGFLOW}.internal
LANGFLOW_PUBLIC_URL=$PUBLIC_LANGFLOW_URL
LANGFLOW_SUPERUSER=$LANGFLOW_SUPERUSER
# AUTO_MIGRATE=1 reapplies schema.sql on every container start. Safe because
# every statement is IF NOT EXISTS, but a CI-driven one-shot migration job is
# preferred for prod. Override AUTO_MIGRATE=0 once schema is stable in the
# target environment to avoid a partial-deploy race where the new schema
# lands before the new code does. Numbered migrations under
# backend/app/migrations/ run only when RUN_NUMBERED_MIGRATIONS=1.
AUTO_MIGRATE=${AUTO_MIGRATE:-1}
AUTO_SEED=0
RUN_NUMBERED_MIGRATIONS=${RUN_NUMBERED_MIGRATIONS:-0}
CORS_ORIGINS=$cors
AZURE_ENDPOINT=${AZURE_ENDPOINT:-}
GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID:-${Client_ID:-}}
EOF
}

_set_backend_secrets() {
  local app="$1"
  az containerapp secret set -g "$RG" -n "$app" --secrets \
    "pg-password=$PG_PASSWORD" \
    "redis-url=$REDIS_URL" \
    "langflow-superuser-password=$LANGFLOW_SUPERUSER_PASSWORD" \
    "workspacex-secret-key=$WORKSPACEX_SECRET_KEY" \
    "workspacex-internal-key=$WORKSPACEX_INTERNAL_KEY" \
    "azure-openai-key=${AZURE_OPEN_AI_KEY:-unset}" \
    "google-client-secret=${GOOGLE_CLIENT_SECRET:-${Client_Secret:-unset}}" \
    "s3-access-key=workspacex" \
    "s3-secret-key=$(openssl rand -base64 24 | tr -d '=+/' | head -c 24)" \
    -o none
}

fn_containerapps() {
  log "Container Apps environment domain lookup"
  compute_public_urls   # populates CAE_DOMAIN + PUBLIC_* (SWA may still be empty here)

  # Grant the Container Apps managed identity AcrPull on the registry.
  ACR_ID="$(az acr show -n "$ACR_NAME" --query id -o tsv)"

  # --- MinIO (S3 shim) ---
  log "Container App: $CA_MINIO (S3 shim)"
  if ! az containerapp show -g "$RG" -n "$CA_MINIO" >/dev/null 2>&1; then
    local minio_pw
    minio_pw="$(openssl rand -base64 24 | tr -d '=+/' | head -c 24)"
    # --secrets and --env-vars must go together in the same create call —
    # otherwise secretref resolution fails before the secret is set.
    az containerapp create -g "$RG" -n "$CA_MINIO" \
      --environment "$CAE_NAME" \
      --image minio/minio:latest \
      --ingress internal --target-port 9000 --transport auto \
      --min-replicas 1 --max-replicas 1 \
      --cpu 0.5 --memory 1Gi \
      --command "/bin/sh" \
      --args "-c,minio server /data" \
      --secrets "minio-pw=$minio_pw" \
      --env-vars \
        "MINIO_ROOT_USER=workspacex" \
        "MINIO_ROOT_PASSWORD=secretref:minio-pw" \
      -o none
  else skip "$CA_MINIO"; fi

  # --- api ---
  log "Container App: $CA_API"
  if ! az containerapp show -g "$RG" -n "$CA_API" >/dev/null 2>&1; then
    az containerapp create -g "$RG" -n "$CA_API" \
      --environment "$CAE_NAME" \
      --image "$ACR_NAME.azurecr.io/workspacex-backend:latest" \
      --registry-server "$ACR_NAME.azurecr.io" \
      --registry-identity system \
      --system-assigned \
      --ingress external --target-port 8000 --transport auto \
      --min-replicas 1 --max-replicas 3 \
      --cpu 0.5 --memory 1Gi \
      -o none
  else skip "$CA_API"; fi
  _set_backend_secrets "$CA_API"
  API_MI="$(az containerapp show -g "$RG" -n "$CA_API" --query identity.principalId -o tsv)"
  az role assignment create --assignee-object-id "$API_MI" --assignee-principal-type ServicePrincipal \
    --role AcrPull --scope "$ACR_ID" -o none 2>/dev/null || true
  # Apply env (mix of plain + secretref).
  mapfile -t API_ENV < <(_backend_env_vars api)
  az containerapp update -g "$RG" -n "$CA_API" \
    --set-env-vars "${API_ENV[@]}" \
      "LANGFLOW_SUPERUSER_PASSWORD=secretref:langflow-superuser-password" \
      "WORKSPACEX_SECRET_KEY=secretref:workspacex-secret-key" \
      "WORKSPACEX_INTERNAL_KEY=secretref:workspacex-internal-key" \
      "AZURE_OPEN_AI_KEY=secretref:azure-openai-key" \
      "GOOGLE_CLIENT_SECRET=secretref:google-client-secret" \
      "S3_ACCESS_KEY=secretref:s3-access-key" \
      "S3_SECRET_KEY=secretref:s3-secret-key" \
    -o none

  # --- worker ---
  log "Container App: $CA_WORKER"
  if ! az containerapp show -g "$RG" -n "$CA_WORKER" >/dev/null 2>&1; then
    # No --ingress flag = no inbound traffic (worker-style).
    az containerapp create -g "$RG" -n "$CA_WORKER" \
      --environment "$CAE_NAME" \
      --image "$ACR_NAME.azurecr.io/workspacex-backend:latest" \
      --registry-server "$ACR_NAME.azurecr.io" \
      --registry-identity system \
      --system-assigned \
      --min-replicas 1 --max-replicas 2 \
      --cpu 0.5 --memory 1Gi \
      -o none
  else skip "$CA_WORKER"; fi
  _set_backend_secrets "$CA_WORKER"
  WK_MI="$(az containerapp show -g "$RG" -n "$CA_WORKER" --query identity.principalId -o tsv)"
  az role assignment create --assignee-object-id "$WK_MI" --assignee-principal-type ServicePrincipal \
    --role AcrPull --scope "$ACR_ID" -o none 2>/dev/null || true
  mapfile -t WK_ENV < <(_backend_env_vars worker)
  az containerapp update -g "$RG" -n "$CA_WORKER" \
    --set-env-vars "${WK_ENV[@]}" \
      "LANGFLOW_SUPERUSER_PASSWORD=secretref:langflow-superuser-password" \
      "WORKSPACEX_SECRET_KEY=secretref:workspacex-secret-key" \
      "WORKSPACEX_INTERNAL_KEY=secretref:workspacex-internal-key" \
      "AZURE_OPEN_AI_KEY=secretref:azure-openai-key" \
      "GOOGLE_CLIENT_SECRET=secretref:google-client-secret" \
      "S3_ACCESS_KEY=secretref:s3-access-key" \
      "S3_SECRET_KEY=secretref:s3-secret-key" \
    -o none

  # --- langflow ---
  log "Container App: $CA_LANGFLOW"
  LANGFLOW_DB_URL="postgresql://${PG_ADMIN}:${PG_PASSWORD}@${PG_HOST}:5432/langflow?sslmode=require"
  if ! az containerapp show -g "$RG" -n "$CA_LANGFLOW" >/dev/null 2>&1; then
    az containerapp create -g "$RG" -n "$CA_LANGFLOW" \
      --environment "$CAE_NAME" \
      --image "$ACR_NAME.azurecr.io/workspacex-langflow:latest" \
      --registry-server "$ACR_NAME.azurecr.io" \
      --registry-identity system \
      --system-assigned \
      --ingress external --target-port 7860 --transport auto \
      --min-replicas 1 --max-replicas 2 \
      --cpu 1.0 --memory 2Gi \
      -o none
  else skip "$CA_LANGFLOW"; fi
  az containerapp secret set -g "$RG" -n "$CA_LANGFLOW" \
    --secrets \
      "lf-db-url=$LANGFLOW_DB_URL" \
      "lf-superuser-pw=$LANGFLOW_SUPERUSER_PASSWORD" \
      "wx-internal-key=$WORKSPACEX_INTERNAL_KEY" \
    -o none
  LF_MI="$(az containerapp show -g "$RG" -n "$CA_LANGFLOW" --query identity.principalId -o tsv)"
  az role assignment create --assignee-object-id "$LF_MI" --assignee-principal-type ServicePrincipal \
    --role AcrPull --scope "$ACR_ID" -o none 2>/dev/null || true
  az containerapp update -g "$RG" -n "$CA_LANGFLOW" --set-env-vars \
    "LANGFLOW_DATABASE_URL=secretref:lf-db-url" \
    "LANGFLOW_AUTO_LOGIN=false" \
    "LANGFLOW_SUPERUSER=$LANGFLOW_SUPERUSER" \
    "LANGFLOW_SUPERUSER_PASSWORD=secretref:lf-superuser-pw" \
    "LANGFLOW_NEW_USER_IS_ACTIVE=false" \
    "LANGFLOW_CONFIG_DIR=/app/langflow" \
    "LANGFLOW_CORS_ORIGINS=$PUBLIC_API_URL,$PUBLIC_SPA_URLS" \
    "WORKSPACEX_API_URL=$PUBLIC_API_URL" \
    "WORKSPACEX_INTERNAL_KEY=secretref:wx-internal-key" \
    -o none
}

fn_swa() {
  log "Static Web App: $SWA_NAME"
  # SWA isn't available in every Azure region — has its own location var.
  local swa_loc="${SWA_LOCATION:-$LOCATION}"
  if ! az staticwebapp show -g "$RG" -n "$SWA_NAME" >/dev/null 2>&1; then
    az staticwebapp create -g "$RG" -n "$SWA_NAME" -l "$swa_loc" --sku "$SWA_SKU" -o none
  else skip "$SWA_NAME"; fi
  SWA_HOSTNAME="$(az staticwebapp show -g "$RG" -n "$SWA_NAME" --query defaultHostname -o tsv)"
  SWA_TOKEN="$(az staticwebapp secrets list -g "$RG" -n "$SWA_NAME" \
    --query properties.apiKey -o tsv)"
}

fn_github_sync() {
  log "Pushing vars + secrets to GitHub ($GITHUB_REPO)"
  if ! command -v gh >/dev/null; then
    echo "gh not installed — skipping. Install: https://cli.github.com/"
    return
  fi
  if ! gh auth status >/dev/null 2>&1; then
    echo "gh not logged in — run 'gh auth login' and re-run this step."
    return
  fi

  # Resolve PUBLIC_API_URL and friends. Required when this fn is invoked
  # via the `github-sync` subcommand without a full provision having run
  # in this shell (compute_public_urls would otherwise be skipped).
  compute_public_urls

  for pair in \
      "AZURE_CLIENT_ID=$APP_ID" \
      "AZURE_TENANT_ID=$TENANT_ID" \
      "AZURE_SUBSCRIPTION_ID=$SUB_ID" \
      "ACR_NAME=$ACR_NAME" \
      "RESOURCE_GROUP=$RG" \
      "CONTAINERAPP_API=$CA_API" \
      "CONTAINERAPP_WORKER=$CA_WORKER" \
      "CONTAINERAPP_LANGFLOW=$CA_LANGFLOW" \
      "VITE_API_URL=$PUBLIC_API_URL"; do
    k="${pair%%=*}"; v="${pair#*=}"
    if [[ -z "$v" ]]; then
      echo "  WARNING: $k has no value — skipping (fn_containerapps may not have run yet)" >&2
      continue
    fi
    gh variable set "$k" --body "$v" --repo "$GITHUB_REPO"
  done

  if [[ -n "${SWA_TOKEN:-}" ]]; then
    gh secret set AZURE_STATIC_WEB_APPS_API_TOKEN --body "$SWA_TOKEN" --repo "$GITHUB_REPO"
  else
    echo "  WARNING: SWA_TOKEN empty — fn_swa may not have run; skipping secret" >&2
  fi
}

fn_domains_stage() {
  log "Staging custom hostnames (no certs yet — needs DNS first)"

  CAE_VERIFICATION_ID="$(az containerapp env show -g "$RG" -n "$CAE_NAME" \
    --query properties.customDomainConfiguration.customDomainVerificationId -o tsv 2>/dev/null || true)"

  # --- Container Apps: register hostname so we can read back the
  #     per-hostname validationToken. Idempotent: `hostname add` is a
  #     no-op if the hostname is already attached.
  for pair in "$API_HOST:$CA_API" "$LANGFLOW_HOST:$CA_LANGFLOW"; do
    host="${pair%%:*}"; app="${pair##*:}"
    az containerapp hostname add -g "$RG" -n "$app" --hostname "$host" -o none 2>/dev/null || true
  done

  API_TARGET="$(az containerapp show -g "$RG" -n "$CA_API"      --query properties.configuration.ingress.fqdn -o tsv)"
  LF_TARGET="$(az containerapp show  -g "$RG" -n "$CA_LANGFLOW" --query properties.configuration.ingress.fqdn -o tsv)"
  SWA_TARGET="$(az staticwebapp show -g "$RG" -n "$SWA_NAME"    --query defaultHostname -o tsv)"

  cat <<EOF

================ DNS records to add at your registrar ================
Create these on the public DNS zone for $DOMAIN, then run:
  bash deploy/azure/provision.sh domains-bind

# --- API (api.$DOMAIN) -> Container App ---
CNAME  api                       $API_TARGET
TXT    asuid.api                 $CAE_VERIFICATION_ID

# --- Langflow (flow.$DOMAIN) -> Container App ---
CNAME  flow                      $LF_TARGET
TXT    asuid.flow                $CAE_VERIFICATION_ID

# --- SPA (workspacex.com + www) -> Static Web App ---
# Apex (workspacex.com) — pick ONE of:
#   (a) ALIAS / ANAME (Cloudflare, Route 53, DNSimple):
ALIAS  @                         $SWA_TARGET
#   (b) Plain DNS (no ALIAS support) — A + TXT:
#       Look up the A record after running 'domains-bind' (SWA prints it).
CNAME  www                       $SWA_TARGET
=======================================================================

EOF
}

fn_domains_bind() {
  log "Binding managed TLS certs (requires DNS records above to be live)"

  # --- API ---
  az containerapp hostname bind -g "$RG" -n "$CA_API" \
    --hostname "$API_HOST" --environment "$CAE_NAME" \
    --validation-method CNAME -o none

  # --- Langflow ---
  az containerapp hostname bind -g "$RG" -n "$CA_LANGFLOW" \
    --hostname "$LANGFLOW_HOST" --environment "$CAE_NAME" \
    --validation-method CNAME -o none

  # --- SPA: apex + www ---
  # `--validation-method cname-delegation` works for www; apex uses
  # `dns-txt-token` if you don't have ALIAS/ANAME support.
  az staticwebapp hostname set -g "$RG" -n "$SWA_NAME" \
    --hostname "$SPA_WWW" -o none
  az staticwebapp hostname set -g "$RG" -n "$SWA_NAME" \
    --hostname "$SPA_APEX" -o none

  log "Custom domains bound. Browse to:"
  echo "  https://$SPA_APEX"
  echo "  https://$API_HOST/healthz"
  echo "  https://$LANGFLOW_HOST"
}

fn_summary() {
  compute_public_urls
  cat <<EOF

================ Provision complete ================
Resource group : $RG
ACR            : $ACR_NAME.azurecr.io
Postgres       : $PG_HOST (admin: $PG_ADMIN — password in Key Vault: pg-admin-password)
Redis          : $REDIS_HOST:6380 (TLS)
Blob           : $STORAGE_ACCOUNT / $STORAGE_CONTAINER
API            : $PUBLIC_API_URL
Langflow       : $PUBLIC_LANGFLOW_URL
SPA            : ${PUBLIC_SPA_URLS%%,*}
Key Vault      : $KV_NAME
====================================================

Next:
  1. First deploy happens when you push to main — workflows already wired.
  2. Rotate any placeholder secrets:
       az containerapp secret set -g $RG -n $CA_API --secrets azure-openai-key=<real>
  3. When ready, migrate s3.py from MinIO-on-Container-Apps to real Azure Blob
     with SAS tokens, then delete Container App: $CA_MINIO.
EOF

  if [[ "$USE_CUSTOM_DOMAIN" != "1" ]]; then
    cat <<EOF

----- To bind workspacex.com later -----
  1. In deploy/azure/config.sh:
       export USE_CUSTOM_DOMAIN="1"
       export SWA_SKU="Standard"
  2. Bump the SWA SKU once (custom domains require Standard):
       az staticwebapp update -g $RG -n $SWA_NAME --sku Standard
  3. bash deploy/azure/provision.sh domains-stage   # prints the DNS records
  4. (add the records at your registrar, wait ~5 min for propagation)
  5. bash deploy/azure/provision.sh domains-bind    # issues managed TLS certs
  6. bash deploy/azure/provision.sh github-sync     # repushes VITE_API_URL
EOF
  fi

  echo
}

# ---------- run ----------
main() {
  command -v az >/dev/null || { echo "az CLI required" >&2; exit 1; }
  command -v psql >/dev/null || { echo "psql required (for DB/extension setup)" >&2; exit 1; }
  az_account
  fn_rg
  fn_law
  fn_acr
  fn_keyvault
  fn_postgres
  fn_redis
  fn_storage
  fn_cae
  fn_oidc
  fn_images
  fn_containerapps
  fn_swa
  fn_finalize_cors
  if [[ "$USE_CUSTOM_DOMAIN" == "1" ]]; then
    fn_domains_stage
  fi
  fn_github_sync
  fn_summary
}

# After fn_swa, re-resolve URLs (now SWA hostname is known) and patch
# CORS_ORIGINS on api + worker so the SPA origin is allowed.
fn_finalize_cors() {
  log "Finalizing CORS_ORIGINS on api + worker"
  compute_public_urls
  local cors
  cors="$(_csv_join "$PUBLIC_SPA_URLS" "$PUBLIC_LANGFLOW_URL" "$PUBLIC_API_URL")"
  for app in "$CA_API" "$CA_WORKER"; do
    az containerapp update -g "$RG" -n "$app" \
      --set-env-vars "CORS_ORIGINS=$cors" -o none
  done
}

# Subcommands let you run just one phase. Examples:
#   bash provision.sh                 # full provision (everything except cert binding)
#   bash provision.sh domains-stage   # re-print DNS records to add
#   bash provision.sh domains-bind    # bind managed certs (run AFTER DNS is live)
#   bash provision.sh github-sync     # re-push GitHub vars/secrets only
#   bash provision.sh images          # rebuild + push initial images only
dispatch() {
  case "${1:-all}" in
    all)            main ;;
    domains-stage)  az_account; fn_domains_stage ;;
    domains-bind)   az_account; fn_domains_bind ;;
    github-sync)    az_account; fn_oidc; fn_swa; fn_github_sync ;;
    images)         az_account; fn_images ;;
    containerapps)  # provisions just the 4 Container Apps (api, worker,
                    # langflow, minio) + SWA + final CORS update. Use when
                    # the rest of the stack is in place but apps failed.
                    az_account
                    # fn_redis sets REDIS_URL/PG_HOST referenced by env vars.
                    PG_HOST="$PG_SERVER.postgres.database.azure.com"
                    REDIS_HOST="$(az redis show -g "$RG" -n "$REDIS_NAME" --query hostName -o tsv)"
                    REDIS_KEY="$(az redis list-keys -g "$RG" -n "$REDIS_NAME" --query primaryKey -o tsv)"
                    REDIS_URL="rediss://:${REDIS_KEY}@${REDIS_HOST}:6380"
                    fn_containerapps
                    fn_swa
                    fn_finalize_cors
                    ;;
    summary)        az_account; fn_summary ;;
    *) echo "Unknown subcommand: $1" >&2; exit 2 ;;
  esac
}

# Allow sourcing (no auto-run) so individual fns can be invoked from shell.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then dispatch "$@"; fi
