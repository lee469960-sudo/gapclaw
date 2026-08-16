#!/usr/bin/env bash
# 根据 .env 生成 GAP_IMAGE_API / GAP_IMAGE_WEB / GAP_IMAGE_DB（兼容 docker-compose v1）
set -euo pipefail
DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${DEPLOY_DIR}/.env"
EXAMPLE="${DEPLOY_DIR}/.env.example"

if [[ ! -f "${ENV_FILE}" ]]; then
  cp "${EXAMPLE}" "${ENV_FILE}"
fi

get_var() {
  local key="$1"
  local default="${2:-}"
  local line
  line="$(grep -E "^${key}=" "${ENV_FILE}" 2>/dev/null | tail -1 || true)"
  if [[ -z "${line}" ]]; then
    echo "${default}"
    return
  fi
  echo "${line#*=}"
}

detect_arch() {
  case "$(uname -m)" in
    aarch64|arm64) echo arm ;;
    x86_64|amd64) echo amd ;;
    *) echo amd ;;
  esac
}

HOST_ARCH="$(detect_arch)"
GAP_ARCH_FORCE="$(get_var GAP_ARCH_FORCE "")"
if [[ "${GAP_ARCH_FORCE}" == "1" ]]; then
  GAP_ARCH="$(get_var GAP_ARCH amd)"
else
  GAP_ARCH="${HOST_ARCH}"
  env_arch="$(get_var GAP_ARCH "")"
  if [[ -n "${env_arch}" && "${env_arch}" != "${GAP_ARCH}" ]]; then
    echo "WARN: .env 中 GAP_ARCH=${env_arch} 与本机 $(uname -m) → ${HOST_ARCH} 不一致，已改用 ${GAP_ARCH}。" >&2
    echo "      若确需固定架构，请设置 GAP_ARCH_FORCE=1。" >&2
  fi
fi
GAP_VERSION="$(get_var GAP_VERSION V0.0.1)"
GAP_REGISTRY="$(get_var GAP_REGISTRY "")"
GAP_NAMESPACE="$(get_var GAP_NAMESPACE tools_claw)"
GAP_DB_NAMESPACE="$(get_var GAP_DB_NAMESPACE tools_dba)"
GAP_DB_TAG="$(get_var GAP_DB_TAG 16-alpine)"
GAP_DATA_DIR="$(get_var GAP_DATA_DIR "${DEPLOY_DIR}/data")"
DOCKER_DATA_HOST_PATH="$(get_var DOCKER_DATA_HOST_PATH "${GAP_DATA_DIR}")"
mkdir -p "${GAP_DATA_DIR}"

image_name() {
  local component="$1"
  local name="gap-${component}-${GAP_ARCH}:${GAP_VERSION}"
  if [[ -n "${GAP_REGISTRY}" ]]; then
    echo "${GAP_REGISTRY}/${GAP_NAMESPACE}/${name}"
  else
    echo "${name}"
  fi
}

db_image_name() {
  if [[ -n "${GAP_REGISTRY}" ]]; then
    echo "${GAP_REGISTRY}/${GAP_DB_NAMESPACE}/postgres-${GAP_ARCH}:${GAP_DB_TAG}"
  else
    echo "postgres:${GAP_DB_TAG}"
  fi
}

API_IMAGE="$(image_name api)"
WEB_IMAGE="$(image_name web)"
DB_IMAGE="$(db_image_name)"

python3 - "${ENV_FILE}" "${API_IMAGE}" "${WEB_IMAGE}" "${DB_IMAGE}" "${GAP_DATA_DIR}" "${DOCKER_DATA_HOST_PATH}" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
api_image, web_image, db_image = sys.argv[2], sys.argv[3], sys.argv[4]
gap_data_dir, docker_data_host_path = sys.argv[5], sys.argv[6]
lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
updates = {
    "GAP_IMAGE_API": api_image,
    "GAP_IMAGE_WEB": web_image,
    "GAP_IMAGE_DB": db_image,
    "GAP_DATA_DIR": gap_data_dir,
    "DOCKER_DATA_HOST_PATH": docker_data_host_path,
}
seen = set()
out = []
for line in lines:
    key = line.split("=", 1)[0] if "=" in line and not line.startswith("#") else ""
    if key in updates:
        out.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        out.append(line)
for key, val in updates.items():
    if key not in seen:
        out.append(f"{key}={val}")
path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
PY

echo "GAP_IMAGE_API=${API_IMAGE}"
echo "GAP_IMAGE_WEB=${WEB_IMAGE}"
echo "GAP_IMAGE_DB=${DB_IMAGE}"
echo "GAP_DATA_DIR=${GAP_DATA_DIR}"
echo "DOCKER_DATA_HOST_PATH=${DOCKER_DATA_HOST_PATH}"
