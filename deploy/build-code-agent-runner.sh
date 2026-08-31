#!/usr/bin/env bash
# Build or publish the trusted CodeAgent runner image.
#
# Local development:
#   deploy/build-code-agent-runner.sh
#
# Local arm + amd tags (same naming as gap-api-{arm|amd}):
#   deploy/build-code-agent-runner.sh --arch-tags v0.0.1
#
# Production multi-arch publish:
#   deploy/build-code-agent-runner.sh --push registry.example.com/ns/code-agent-runner:1
#   docker buildx imagetools inspect registry.example.com/ns/code-agent-runner:1
#
# Use the registry manifest-list digest as CODE_TRUSTED_IMAGE_DIGESTS:
#   ["registry.example.com/ns/code-agent-runner@sha256:<manifest-list-digest>"]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="code-agent-runner:1"
PUSH=0
ARCH_TAGS=""
PLATFORMS="${PLATFORMS:-linux/arm64,linux/amd64}"
PYTHON_IMAGE="${PYTHON_IMAGE:-python:3.12-slim}"
DEBIAN_MIRROR="${DEBIAN_MIRROR:-http://deb.debian.org/debian}"
DEBIAN_SECURITY_MIRROR="${DEBIAN_SECURITY_MIRROR:-http://deb.debian.org/debian-security}"
NPM_REGISTRY="${NPM_REGISTRY:-https://registry.npmjs.org}"
DBT_CORE_VERSION="${DBT_CORE_VERSION:-1.8.7}"
DBT_CLICKHOUSE_VERSION="${DBT_CLICKHOUSE_VERSION:-1.8.4}"
CLICKHOUSE_VERSION="${CLICKHOUSE_VERSION:-24.8.14.39}"
NODE_VERSION="${NODE_VERSION:-22.14.0}"
NODE_DIST="${NODE_DIST:-https://nodejs.org/dist}"
PIP_INDEX="${PIP_INDEX:-https://pypi.org/simple}"

usage() {
  cat <<EOF
Usage:
  $(basename "$0") [--local [IMAGE]]
  $(basename "$0") --arch-tags TAG
  $(basename "$0") --push IMAGE

Options:
  --local IMAGE     Build a local single-arch image. Default: ${IMAGE}
  --arch-tags TAG   Build code-agent-runner-arm:TAG and code-agent-runner-amd:TAG
  --push IMAGE      Build and push linux/arm64 + linux/amd64 with docker buildx.

Environment:
  PLATFORMS               Default: ${PLATFORMS}
  PYTHON_IMAGE            Default: ${PYTHON_IMAGE}
  DEBIAN_MIRROR           Default: ${DEBIAN_MIRROR}
  DEBIAN_SECURITY_MIRROR  Default: ${DEBIAN_SECURITY_MIRROR}
  NPM_REGISTRY            Default: ${NPM_REGISTRY}
  DBT_CORE_VERSION         Default: ${DBT_CORE_VERSION}
  DBT_CLICKHOUSE_VERSION   Default: ${DBT_CLICKHOUSE_VERSION}
  CLICKHOUSE_VERSION       Default: ${CLICKHOUSE_VERSION}
  NODE_VERSION             Default: ${NODE_VERSION}
  NODE_DIST                Default: ${NODE_DIST}
  PIP_INDEX                Default: ${PIP_INDEX}
EOF
}

case "${1:-}" in
  -h|--help|help)
    usage
    exit 0
    ;;
  --push)
    PUSH=1
    IMAGE="${2:-}"
    if [[ -z "${IMAGE}" ]]; then
      echo "ERROR: --push requires a registry image, e.g. registry.example.com/ns/code-agent-runner:1" >&2
      exit 1
    fi
    ;;
  --arch-tags)
    ARCH_TAGS="${2:-}"
    if [[ -z "${ARCH_TAGS}" ]]; then
      echo "ERROR: --arch-tags requires a tag, e.g. v0.0.1" >&2
      exit 1
    fi
    ;;
  --local)
    IMAGE="${2:-${IMAGE}}"
    ;;
  "")
    ;;
  *)
    IMAGE="$1"
    ;;
esac

build_args=(
  --pull=false
  --build-arg "PYTHON_IMAGE=${PYTHON_IMAGE}"
  --build-arg "DEBIAN_MIRROR=${DEBIAN_MIRROR}"
  --build-arg "DEBIAN_SECURITY_MIRROR=${DEBIAN_SECURITY_MIRROR}"
  --build-arg "NPM_REGISTRY=${NPM_REGISTRY}"
  --build-arg "DBT_CORE_VERSION=${DBT_CORE_VERSION}"
  --build-arg "DBT_CLICKHOUSE_VERSION=${DBT_CLICKHOUSE_VERSION}"
  --build-arg "CLICKHOUSE_VERSION=${CLICKHOUSE_VERSION}"
  --build-arg "NODE_VERSION=${NODE_VERSION}"
  --build-arg "NODE_DIST=${NODE_DIST}"
  --build-arg "PIP_INDEX=${PIP_INDEX}"
  -f "${ROOT}/deploy/code-agent-runner.Dockerfile"
)

if [[ "${PUSH}" -eq 1 ]]; then
  docker buildx build \
    --platform "${PLATFORMS}" \
    --tag "${IMAGE}" \
    --push \
    "${build_args[@]}" \
    "${ROOT}"
  echo ""
  echo "Inspect the multi-arch manifest-list digest:"
  echo "  docker buildx imagetools inspect ${IMAGE}"
elif [[ -n "${ARCH_TAGS}" ]]; then
  FAILED=0
  echo "==> [arm] code-agent-runner-arm:${ARCH_TAGS} (linux/arm64)"
  docker build --platform linux/arm64 \
    --tag "code-agent-runner-arm:${ARCH_TAGS}" \
    "${build_args[@]}" \
    "${ROOT}" || FAILED=1
  echo "==> [amd] code-agent-runner-amd:${ARCH_TAGS} (linux/amd64)"
  docker build --platform linux/amd64 \
    --tag "code-agent-runner-amd:${ARCH_TAGS}" \
    "${build_args[@]}" \
    "${ROOT}" || {
      echo "WARN: amd build failed (common on Mac when qemu/docker.io is slow)." >&2
      FAILED=1
    }
  if [[ "${FAILED}" -ne 0 ]]; then
    exit 1
  fi
  echo ""
  docker images --format '{{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.Size}}' \
    | grep -E "^code-agent-runner-(arm|amd):${ARCH_TAGS}" || true
else
  docker build \
    --tag "${IMAGE}" \
    "${build_args[@]}" \
    "${ROOT}"
  echo ""
  echo "Local image digest:"
  docker inspect "${IMAGE}" --format '{{index .RepoDigests 0}}'
fi
