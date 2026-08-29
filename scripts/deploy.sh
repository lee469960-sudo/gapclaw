#!/usr/bin/env bash
# Pull tagged ACR images and recreate the production stack. Never builds or git-pulls.
# Usage: ./scripts/deploy.sh v1.0.0
set -euo pipefail

usage() {
  echo "Usage: $0 vMAJOR.MINOR.PATCH" >&2
  echo "Example: $0 v1.0.0" >&2
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

TAG="${1:-${IMAGE_TAG:-}}"
if [[ -z "${TAG}" ]]; then
  usage
  exit 1
fi
case "${TAG}" in
  [vV][0-9]*.[0-9]*.[0-9]*) ;;
  *)
    echo "Invalid version '${TAG}'; expected vMAJOR.MINOR.PATCH (e.g. v1.0.0 or V0.0.3)" >&2
    exit 1
    ;;
esac

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-${ROOT}/deploy/docker-compose.prod.yml}"
DEPLOY_ENV_FILE="${DEPLOY_ENV_FILE:-/opt/gap/.env}"
if [[ ! -f "${DEPLOY_ENV_FILE}" && -f "${ROOT}/deploy/.env" ]]; then
  DEPLOY_ENV_FILE="${ROOT}/deploy/.env"
fi
if [[ ! -f "${DEPLOY_ENV_FILE}" ]]; then
  echo "Missing env file. Copy deploy/.env.example to ${DEPLOY_ENV_FILE:-/opt/gap/.env} and fill secrets." >&2
  exit 1
fi
if [[ ! -f "${COMPOSE_FILE}" ]]; then
  echo "Missing compose file: ${COMPOSE_FILE}" >&2
  exit 1
fi

# Preserve coordinates injected by GitHub Actions (do not let an empty .env key wipe them).
CI_IMAGE_API="${IMAGE_API:-}"
CI_IMAGE_WEB="${IMAGE_WEB:-}"
CI_ALIYUN_REGISTRY="${ALIYUN_REGISTRY:-}"
CI_ALIYUN_IMAGE="${ALIYUN_IMAGE:-}"
CI_ALIYUN_USER="${ALIYUN_REGISTRY_USERNAME:-}"
CI_ALIYUN_PASS="${ALIYUN_REGISTRY_PASSWORD:-}"

set -a
# shellcheck disable=SC1090
source "${DEPLOY_ENV_FILE}"
set +a

ALIYUN_REGISTRY="${CI_ALIYUN_REGISTRY:-${ALIYUN_REGISTRY:-}}"
ALIYUN_IMAGE="${CI_ALIYUN_IMAGE:-${ALIYUN_IMAGE:-}}"
ALIYUN_REGISTRY_USERNAME="${CI_ALIYUN_USER:-${ALIYUN_REGISTRY_USERNAME:-}}"
ALIYUN_REGISTRY_PASSWORD="${CI_ALIYUN_PASS:-${ALIYUN_REGISTRY_PASSWORD:-}}"
IMAGE_API="${CI_IMAGE_API:-${IMAGE_API:-}}"
IMAGE_WEB="${CI_IMAGE_WEB:-${IMAGE_WEB:-}}"

export IMAGE_TAG="${TAG}"

if [[ -z "${IMAGE_API:-}" ]]; then
  if [[ -n "${ALIYUN_REGISTRY:-}" && -n "${ALIYUN_IMAGE:-}" ]]; then
    IMAGE_API="${ALIYUN_REGISTRY}/${ALIYUN_IMAGE}"
  fi
fi
if [[ -z "${IMAGE_WEB:-}" ]]; then
  if [[ -n "${ALIYUN_REGISTRY:-}" && -n "${ALIYUN_IMAGE:-}" ]]; then
    if [[ "${ALIYUN_IMAGE}" != "${ALIYUN_IMAGE%-api}" ]]; then
      IMAGE_WEB="${ALIYUN_REGISTRY}/${ALIYUN_IMAGE%-api}-web"
    else
      IMAGE_WEB="${ALIYUN_REGISTRY}/${ALIYUN_IMAGE}-web"
    fi
  fi
fi
if [[ -z "${IMAGE_DB:-}" && -n "${GAP_IMAGE_DB:-}" ]]; then
  IMAGE_DB="${GAP_IMAGE_DB}"
fi

export IMAGE_API IMAGE_WEB IMAGE_DB IMAGE_TAG

missing=()
[[ -z "${IMAGE_API:-}" ]] && missing+=("IMAGE_API (or ALIYUN_REGISTRY + ALIYUN_IMAGE)")
[[ -z "${IMAGE_WEB:-}" ]] && missing+=("IMAGE_WEB")
[[ -z "${IMAGE_DB:-}" ]] && missing+=("IMAGE_DB")
if ((${#missing[@]})); then
  echo "Missing required image settings: ${missing[*]}" >&2
  exit 1
fi

if [[ -z "${ALIYUN_REGISTRY:-}" || -z "${ALIYUN_REGISTRY_USERNAME:-}" || -z "${ALIYUN_REGISTRY_PASSWORD:-}" ]]; then
  echo "ACR login requires ALIYUN_REGISTRY, ALIYUN_REGISTRY_USERNAME, ALIYUN_REGISTRY_PASSWORD" >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is not installed" >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "docker compose plugin is required" >&2
  exit 1
fi

compose() {
  docker compose -p gap --env-file "${DEPLOY_ENV_FILE}" -f "${COMPOSE_FILE}" "$@"
}

echo "==> Login ${ALIYUN_REGISTRY}"
echo "${ALIYUN_REGISTRY_PASSWORD}" | docker login "${ALIYUN_REGISTRY}" -u "${ALIYUN_REGISTRY_USERNAME}" --password-stdin >/dev/null

echo "==> Pull ${IMAGE_API}:${IMAGE_TAG} and ${IMAGE_WEB}:${IMAGE_TAG}"
compose pull

echo "==> Recreate stack"
compose up -d --remove-orphans --pull never

API_PORT="${API_PORT:-8000}"
HEALTH_URL="http://127.0.0.1:${API_PORT}/health"
echo "==> Health check ${HEALTH_URL}"

ok=0
for _ in $(seq 1 36); do
  if curl -sf "${HEALTH_URL}" | grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"'; then
    ok=1
    break
  fi
  sleep 5
done

echo "==> docker compose ps"
compose ps

if [[ "${ok}" -ne 1 ]]; then
  echo "Health check failed: ${HEALTH_URL} did not return status=ok" >&2
  compose logs --tail 80 api web db || true
  exit 1
fi

set +e
fmt_out="$(compose ps --format '{{.Service}} {{.Health}} {{.State}}' 2>/dev/null)"
fmt_rc=$?
set -e
if [[ "${fmt_rc}" -eq 0 && -n "${fmt_out}" ]]; then
  unhealthy="$(printf '%s\n' "${fmt_out}" | awk '$2 == "unhealthy" || $3 ~ /exited|dead/ { print }' || true)"
  if [[ -n "${unhealthy}" ]]; then
    echo "Unhealthy or stopped services:" >&2
    echo "${unhealthy}" >&2
    compose logs --tail 80 || true
    exit 1
  fi
fi

echo "==> Prune dangling images"
docker image prune -f >/dev/null

echo "Deployed ${IMAGE_TAG}"
echo "  API: ${IMAGE_API}:${IMAGE_TAG}"
echo "  WEB: ${IMAGE_WEB}:${IMAGE_TAG}"
echo "  Health: ${HEALTH_URL}"
