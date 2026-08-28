# Trusted CodeAgent runner image: a minimal, fixed-version sandbox that hosts the
# code tool helper, pinned Claude Code CLI, pinned OpenSpec CLI, and baked SOP
# skills (propose/apply/verify/archive + planning-with-files).
#
# The helper is baked in at build time and pinned to the helper version the API
# validates against (runner_protocol.RUNNER_HELPER_VERSION). The image is launched
# non-root / read-only-rootfs / cap-drop ALL / no-new-privileges by
# CodeContainerRunner (see task 6.3); the two static dirs here are mount points,
# the actual runtime binds are provided by Docker at start.
#
# Build context is the repository root (same as the API image):
#   docker build -f deploy/code-agent-runner.Dockerfile -t code-agent-runner:1 .
ARG PYTHON_IMAGE=python:3.12-slim
ARG CLAUDE_CODE_VERSION=2.1.246
ARG OPENSPEC_VERSION=1.10.0

FROM ${PYTHON_IMAGE}
ARG CLAUDE_CODE_VERSION
ARG OPENSPEC_VERSION

# Keep official Debian/npm endpoints as portable defaults while allowing
# release environments to select reachable package mirrors explicitly.
ARG DEBIAN_MIRROR=http://deb.debian.org/debian
ARG DEBIAN_SECURITY_MIRROR=http://deb.debian.org/debian-security
ARG NPM_REGISTRY=https://registry.npmjs.org

# git is required for the read-only status/diff/log helper against the sanitized
# /workspace/.git metadata. Claude Code is installed at build time with an explicit
# version; runtime startup only verifies the baked binary with `claude --version`.
RUN sed -i \
        -e "s|http://deb.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g" \
        -e "s|http://deb.debian.org/debian|${DEBIAN_MIRROR}|g" \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git nodejs npm \
    && npm install -g --registry="${NPM_REGISTRY}" @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION} \
    && npm install -g --registry="${NPM_REGISTRY}" @fission-ai/openspec@${OPENSPEC_VERSION} \
    && claude --version \
    && openspec --version \
    && npm cache clean --force \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /workspace /source.git

COPY apps/api/app/services/code_agent/runner_helper.py /opt/code-agent/runner_helper.py
COPY apps/api/app/services/code_agent/sop/ /opt/code-agent/sop/

ENV CODE_AGENT_WORKSPACE=/workspace \
    CODE_AGENT_GIT_DIR=/workspace/.git \
    CODE_AGENT_TIMEOUT=1800 \
    CLAUDE_CODE_VERSION=${CLAUDE_CODE_VERSION} \
    OPENSPEC_VERSION=${OPENSPEC_VERSION} \
    DISABLE_AUTOUPDATER=1

# Keep-alive default; each tool call runs the baked helper via a container exec
# (request on stdin, response on stdout) — no shell concatenation.
CMD ["sleep", "infinity"]
