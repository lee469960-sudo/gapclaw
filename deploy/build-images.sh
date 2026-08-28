#!/usr/bin/env bash
# 构建 GAP 平台镜像：gap-api-{arm|amd}:V* / gap-web-{arm|amd}:V*（版本见 gap.version）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEPLOY_DIR="${ROOT}/deploy"
GAP_VERSION="${GAP_VERSION:-$(tr -d '[:space:]' < "${DEPLOY_DIR}/gap.version")}"
PYTHON_IMAGE="${PYTHON_IMAGE:-python:3.12-slim}"
NODE_IMAGE="${NODE_IMAGE:-node:20-alpine}"
# 生产 Docker 默认同源反代，留空；仅直连 API 调试时设为 http://host:8000
VITE_API_BASE="${VITE_API_BASE:-}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [arm|amd|all]

  arm   构建 gap-api-arm / gap-web-arm
  amd   构建 gap-api-amd / gap-web-amd
  all   同时构建 arm 与 amd（需 Docker 支持多架构）
  (空)  自动检测当前机器架构

环境变量:
  GAP_VERSION     镜像版本，默认读取 deploy/gap.version (当前: ${GAP_VERSION})
  VITE_API_BASE   前端 API 地址，默认 ${VITE_API_BASE}
  PYTHON_IMAGE    API 基础镜像，默认 ${PYTHON_IMAGE}
  NODE_IMAGE      Web 基础镜像，默认 ${NODE_IMAGE}
EOF
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

build_arch() {
  local arch="$1"
  local platform
  platform="$(platform_for_arch "${arch}")"

  echo "==> [${arch}] gap-api-${arch}:${GAP_VERSION} (${platform})"
  docker build --pull=false --platform "${platform}" \
    --build-arg PYTHON_IMAGE="${PYTHON_IMAGE}" \
    --build-arg PIP_INDEX="${PIP_INDEX:-https://mirrors.aliyun.com/pypi/simple/}" \
    -t "gap-api-${arch}:${GAP_VERSION}" \
    -f "${ROOT}/apps/api/Dockerfile" \
    "${ROOT}" || return 1

  echo "==> [${arch}] gap-web-${arch}:${GAP_VERSION} (${platform})"
  docker build --pull=false --platform "${platform}" \
    --build-arg NODE_IMAGE="${NODE_IMAGE}" \
    --build-arg VITE_API_BASE="${VITE_API_BASE}" \
    -t "gap-web-${arch}:${GAP_VERSION}" \
    -f "${ROOT}/apps/web/Dockerfile" \
    "${ROOT}/apps/web" || return 1
}

TARGET="${1:-$(detect_arch)}"
FAILED=0
case "${TARGET}" in
  -h|--help|help) usage; exit 0 ;;
  arm|amd)
    build_arch "${TARGET}" || FAILED=1
    ;;
  all)
    build_arch arm || FAILED=1
    build_arch amd || {
      echo "WARN: amd 构建失败（常见于 Mac 上跨架构拉取 docker.io 超时）。" >&2
      echo "      可在 x86 机器执行: ./build-images.sh amd" >&2
      FAILED=1
    }
    ;;
  *)
    echo "无效参数: ${TARGET}" >&2
    usage
    exit 1
    ;;
esac

if [[ "${FAILED}" -ne 0 ]]; then
  echo "部分镜像构建失败。" >&2
  exit 1
fi

echo ""
echo "==> 已构建镜像 (${GAP_VERSION}):"
docker images --format 'table {{.Repository}}\t{{.Tag}}\t{{.Size}}\t{{.CreatedSince}}' \
  | awk 'NR==1 || $1 ~ /^gap-(api|web)-(arm|amd)$/ && $2 == "'"${GAP_VERSION}"'"'
