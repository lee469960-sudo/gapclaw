#!/usr/bin/env bash
# 本地一键：构建当前架构 GAP 镜像并启动 compose
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}/deploy"

detect_arch() {
  case "$(uname -m)" in
    aarch64|arm64) echo arm ;;
    x86_64|amd64) echo amd ;;
    *) echo amd ;;
  esac
}

export GAP_ARCH="${GAP_ARCH:-$(detect_arch)}"
export GAP_VERSION="${GAP_VERSION:-$(tr -d '[:space:]' < "${ROOT}/deploy/gap.version")}"
export PYTHON_IMAGE="${PYTHON_IMAGE:-python:3.12-slim}"
export NODE_IMAGE="${NODE_IMAGE:-node:20-alpine}"
export VITE_API_BASE="${VITE_API_BASE:-}"

echo "==> GAP 本地构建: arch=${GAP_ARCH} version=${GAP_VERSION}"
"${ROOT}/deploy/build-images.sh" "${GAP_ARCH}"

echo "==> Render .env (local images)..."
GAP_REGISTRY="" "${ROOT}/deploy/render-env.sh"

echo "==> Starting stack..."
docker compose up -d --no-build 2>/dev/null || docker-compose up -d --no-build

echo "Done."
echo "  Web:  http://localhost:5173"
echo "  API:  http://localhost:8000"
echo "  镜像: gap-api-${GAP_ARCH}:${GAP_VERSION}  gap-web-${GAP_ARCH}:${GAP_VERSION}"
echo "  账号: admin / admin123"
