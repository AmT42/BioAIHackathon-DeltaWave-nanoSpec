#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/backend/.env}"
RENDER_API_BASE="${RENDER_API_BASE:-https://api.render.com/v1}"
BACKEND_SERVICE_NAME="${BACKEND_SERVICE_NAME:-hackathon-agent-core-backend}"
FRONTEND_SERVICE_NAME="${FRONTEND_SERVICE_NAME:-hackathon-agent-core-frontend}"

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing required command: $cmd" >&2
    exit 1
  fi
}

read_env_value() {
  local key="$1"
  local line
  line="$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 || true)"
  if [[ -z "$line" ]]; then
    return 1
  fi
  local value="${line#*=}"
  value="${value%\"}"
  value="${value#\"}"
  value="${value%\'}"
  value="${value#\'}"
  printf "%s" "$value"
}

api_get() {
  local path="$1"
  curl -fsS \
    -H "Authorization: Bearer ${RENDER_API_KEY}" \
    "${RENDER_API_BASE}${path}"
}

api_put_json() {
  local path="$1"
  local payload="$2"
  curl -fsS -X PUT \
    -H "Authorization: Bearer ${RENDER_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "$payload" \
    "${RENDER_API_BASE}${path}" >/dev/null
}

api_post_json() {
  local path="$1"
  local payload="$2"
  curl -fsS -X POST \
    -H "Authorization: Bearer ${RENDER_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "$payload" \
    "${RENDER_API_BASE}${path}" >/dev/null
}

lookup_service_id_by_name() {
  local name="$1"
  local services_json="$2"
  jq -r --arg name "$name" '
    (if type == "array" then . else (.services // .items // []) end)
    | map(select((.name // .service.name // "") == $name))
    | .[0]
    | (.id // .service.id // empty)
  ' <<<"$services_json"
}

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Env file not found: $ENV_FILE" >&2
  exit 1
fi

if [[ -z "${RENDER_API_KEY:-}" ]]; then
  echo "RENDER_API_KEY is required." >&2
  exit 1
fi

require_cmd curl
require_cmd jq
require_cmd grep

gemini_api_key="$(read_env_value GEMINI_API_KEY || true)"
openalex_mailto="$(read_env_value OPENALEX_MAILTO || true)"

if [[ -z "$gemini_api_key" ]]; then
  echo "GEMINI_API_KEY missing in $ENV_FILE" >&2
  exit 1
fi

if [[ -z "$openalex_mailto" ]]; then
  echo "OPENALEX_MAILTO missing in $ENV_FILE" >&2
  exit 1
fi

echo "Fetching Render services..."
services_json="$(api_get "/services")"
backend_service_id="$(lookup_service_id_by_name "$BACKEND_SERVICE_NAME" "$services_json")"
frontend_service_id="$(lookup_service_id_by_name "$FRONTEND_SERVICE_NAME" "$services_json")"

if [[ -z "$backend_service_id" ]]; then
  echo "Backend service not found: $BACKEND_SERVICE_NAME" >&2
  exit 1
fi

if [[ -z "$frontend_service_id" ]]; then
  echo "Frontend service not found: $FRONTEND_SERVICE_NAME" >&2
  exit 1
fi

echo "Updating backend env vars on $BACKEND_SERVICE_NAME..."
api_put_json "/services/${backend_service_id}/env-vars/GEMINI_API_KEY" "$(jq -cn --arg value "$gemini_api_key" '{value: $value}')"
api_put_json "/services/${backend_service_id}/env-vars/OPENALEX_MAILTO" "$(jq -cn --arg value "$openalex_mailto" '{value: $value}')"

echo "Resolving backend external URL..."
backend_url="$(api_get "/services/${backend_service_id}/env-vars/RENDER_EXTERNAL_URL" | jq -r '.value // empty')"
if [[ -n "$backend_url" ]]; then
  echo "Updating frontend NEXT_PUBLIC_BACKEND_URL..."
  api_put_json "/services/${frontend_service_id}/env-vars/NEXT_PUBLIC_BACKEND_URL" "$(jq -cn --arg value "$backend_url" '{value: $value}')"
fi

echo "Triggering deploys..."
api_post_json "/services/${backend_service_id}/deploys" '{}'
api_post_json "/services/${frontend_service_id}/deploys" '{}'

echo "Render env sync complete."
