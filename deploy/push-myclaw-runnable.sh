#!/usr/bin/env bash
# 构建并推送 myclaw-base-runnable-{arm|amd} 到 GAP 镜像仓库（无需服务器访问 docker.io）
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

platform_for_arch() {
  case "$1" in
    arm) echo linux/arm64 ;;
    amd) echo linux/amd64 ;;
    *) echo "未知架构: $1" >&2; exit 1 ;;
  esac
}

GAP_REGISTRY="$(get_var GAP_REGISTRY "")"
GAP_NAMESPACE="$(get_var GAP_NAMESPACE tools_claw)"
PYTHON_MIRROR="${PYTHON_MIRROR:-docker.1ms.run/library}"

if [[ -z "${GAP_REGISTRY}" ]]; then
  echo "请在 deploy/.env 中设置 GAP_REGISTRY" >&2
  exit 1
fi

push_arch() {
  local arch="$1"
  local platform
  platform="$(platform_for_arch "${arch}")"
  local base_remote="${GAP_REGISTRY}/${GAP_NAMESPACE}/myclaw-base-${arch}:latest"
  local runnable_remote="${GAP_REGISTRY}/${GAP_NAMESPACE}/myclaw-base-runnable-${arch}:latest"
  local python_from="${PYTHON_MIRROR}/python:3.12-slim"

  echo "==> [${arch}] build ${runnable_remote} (base=${base_remote}, platform=${platform})"

  docker buildx build --pull=false --platform "${platform}" \
    --build-arg BASE_IMAGE="${base_remote}" \
    --build-arg PYTHON_IMAGE="${python_from}" \
    -f "${DEPLOY_DIR}/myclaw-runnable.Dockerfile" \
    -t "${runnable_remote}" \
    --push \
    "${DEPLOY_DIR}"

  echo "==> [${arch}] pushed ${runnable_remote}"
}

TARGET="${1:-$(detect_arch)}"
case "${TARGET}" in
  -h|--help|help)
    echo "Usage: $(basename "$0") [arm|amd|all]"
    exit 0
    ;;
  arm|amd) push_arch "${TARGET}" ;;
  all)
    push_arch arm
    push_arch amd
    ;;
  *)
    echo "无效参数: ${TARGET}" >&2
    exit 1
    ;;
esac
