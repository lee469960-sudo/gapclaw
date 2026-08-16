#!/usr/bin/env bash
# 将官方 postgres:16-alpine 按架构推送到 GAP 镜像仓库（tools_dba/postgres-{arm|amd}）
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

GAP_DB_TAG="$(get_var GAP_DB_TAG 16-alpine)"
GAP_REGISTRY="$(get_var GAP_REGISTRY "")"
GAP_DB_NAMESPACE="$(get_var GAP_DB_NAMESPACE tools_dba)"
# 国内拉 docker.io 超时时，用镜像前缀（如 docker.1ms.run/library）
POSTGRES_MIRROR="${POSTGRES_MIRROR:-docker.1ms.run/library}"

if [[ -z "${GAP_REGISTRY}" ]]; then
  echo "请在 deploy/.env 中设置 GAP_REGISTRY" >&2
  exit 1
fi

usage() {
  cat <<EOF
Usage: $(basename "$0") [arm|amd|all]

  从 docker.io/library/postgres:${GAP_DB_TAG} 拉取指定架构并推送到:
    \${GAP_REGISTRY}/\${GAP_DB_NAMESPACE}/postgres-{arch}:${GAP_DB_TAG}

  建议在目标架构机器上执行（x86 服务器推 amd，ARM 机器推 arm），避免推错架构。
EOF
}

push_arch() {
  local arch="$1"
  local platform remote tmpdir
  platform="$(platform_for_arch "${arch}")"
  remote="${GAP_REGISTRY}/${GAP_DB_NAMESPACE}/postgres-${arch}:${GAP_DB_TAG}"
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "${tmpdir}"' RETURN

  cat > "${tmpdir}/Dockerfile" <<EOF
FROM ${POSTGRES_MIRROR}/postgres:${GAP_DB_TAG}
EOF

  echo "==> [${arch}] push ${remote} (${platform})"
  docker buildx build \
    --platform "${platform}" \
    --tag "${remote}" \
    --push \
    --progress=plain \
    "${tmpdir}"

  echo "==> [${arch}] verify"
  docker buildx imagetools inspect "${remote}" | sed -n '/Manifests:/,$p'
}

TARGET="${1:-$(detect_arch)}"
case "${TARGET}" in
  -h|--help|help) usage; exit 0 ;;
  arm|amd) push_arch "${TARGET}" ;;
  all)
    push_arch arm
    push_arch amd
    ;;
  *)
    echo "无效参数: ${TARGET}" >&2
    usage
    exit 1
    ;;
esac

echo "OK postgres 镜像已推送 (${TARGET})"
