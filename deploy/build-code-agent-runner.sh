#!/usr/bin/env bash
# Build or publish the trusted CodeAgent runner image.
#
# Local development:
#   deploy/build-code-agent-runner.sh
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
PLATFORMS="${PLATFORMS:-linux/arm64,linux/amd64}"
PYTHON_IMAGE="${PYTHON_IMAGE:-python:3.12-slim}"
DEBIAN_MIRROR="${DEBIAN_MIRROR:-http://deb.debian.org/debian}"
DEBIAN_SECURITY_MIRROR="${DEBIAN_SECURITY_MIRROR:-http://deb.debian.org/debian-security}"
NPM_REGISTRY="${NPM_REGISTRY:-https://registry.npmjs.org}"

usage() {
  cat <<EOF
Usage:
  $(basename "$0") [--local [IMAGE]]
  $(basename "$0") --push IMAGE

Options:
  --local IMAGE   Build a local single-arch image. Default: ${IMAGE}
  --push IMAGE    Build and push linux/arm64 + linux/amd64 with docker buildx.

Environment:
  PLATFORMS               Default: ${PLATFORMS}
  PYTHON_IMAGE            Default: ${PYTHON_IMAGE}
  DEBIAN_MIRROR           Default: ${DEBIAN_MIRROR}
  DEBIAN_SECURITY_MIRROR  Default: ${DEBIAN_SECURITY_MIRROR}
  NPM_REGISTRY            Default: ${NPM_REGISTRY}
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
else
  docker build \
    --tag "${IMAGE}" \
    "${build_args[@]}" \
    "${ROOT}"
  echo ""
  echo "Local image digest:"
  docker inspect "${IMAGE}" --format '{{index .RepoDigests 0}}'
fi
