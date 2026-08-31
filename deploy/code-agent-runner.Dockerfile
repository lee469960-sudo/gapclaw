# Trusted CodeAgent runner image: a minimal, fixed-version sandbox that hosts the
# code tool helper, pinned Claude Code CLI, pinned OpenSpec CLI, fixed local
# publish system tools (dbt/jq/yq and the ClickHouse `clickhouse client`
# command, not the legacy hyphenated client package), and baked SOP skills
# (propose/apply/verify/archive + planning-with-files).
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
ARG DBT_CORE_VERSION=1.8.7
ARG DBT_CLICKHOUSE_VERSION=1.8.4
ARG CLICKHOUSE_VERSION=24.8.14.39
ARG NODE_VERSION=22.14.0
ARG PIP_INDEX=https://pypi.org/simple

FROM ${PYTHON_IMAGE}
ARG TARGETARCH
ARG CLAUDE_CODE_VERSION
ARG OPENSPEC_VERSION
ARG DBT_CORE_VERSION
ARG DBT_CLICKHOUSE_VERSION
ARG CLICKHOUSE_VERSION
ARG NODE_VERSION
ARG PIP_INDEX

# Keep official Debian/npm endpoints as portable defaults while allowing
# release environments to select reachable package mirrors explicitly.
ARG DEBIAN_MIRROR=http://deb.debian.org/debian
ARG DEBIAN_SECURITY_MIRROR=http://deb.debian.org/debian-security
ARG NPM_REGISTRY=https://registry.npmjs.org
ARG NODE_DIST=https://nodejs.org/dist

# git is required for the read-only status/diff/log helper against the sanitized
# /workspace/.git metadata. Claude Code is installed at build time with an explicit
# version; runtime startup only verifies the baked binary with `claude --version`.
RUN sed -i \
        -e "s|http://deb.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g" \
        -e "s|http://deb.debian.org/debian|${DEBIAN_MIRROR}|g" \
        /etc/apt/sources.list.d/debian.sources \
    && echo 'Acquire::Retries "5";' > /etc/apt/apt.conf.d/80-retries \
    && echo 'Acquire::http::Pipeline-Depth "0";' >> /etc/apt/apt.conf.d/80-retries \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git jq yq \
    && case "${TARGETARCH}" in \
         amd64) CH_ARCH=amd64; NODE_ARCH=x64 ;; \
         arm64) CH_ARCH=arm64; NODE_ARCH=arm64 ;; \
         *) echo "unsupported TARGETARCH=${TARGETARCH}" >&2; exit 1 ;; \
       esac \
    && curl -fsSL --retry 5 --retry-all-errors --retry-delay 2 \
         "${NODE_DIST}/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${NODE_ARCH}.tar.gz" \
         | tar -xz -C /usr/local --strip-components=1 \
    && python -m pip install --no-cache-dir --index-url "${PIP_INDEX}" "dbt-core==${DBT_CORE_VERSION}" "dbt-clickhouse==${DBT_CLICKHOUSE_VERSION}" \
    && npm install -g --registry="${NPM_REGISTRY}" @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION} \
    && npm install -g --registry="${NPM_REGISTRY}" @fission-ai/openspec@${OPENSPEC_VERSION} \
    && curl -fsSL --retry 5 --retry-all-errors --retry-delay 2 -A "Mozilla/5.0 (compatible; DockerBuild)" \
         "https://packages.clickhouse.com/tgz/lts/clickhouse-common-static-${CLICKHOUSE_VERSION}-${CH_ARCH}.tgz" \
         | tar -xz -C /tmp \
    && mv "/tmp/clickhouse-common-static-${CLICKHOUSE_VERSION}/usr/bin/clickhouse" /usr/local/bin/clickhouse \
    && chmod +x /usr/local/bin/clickhouse \
    && rm -rf "/tmp/clickhouse-common-static-${CLICKHOUSE_VERSION}" \
    && node --version \
    && claude --version \
    && openspec --version \
    && curl --version \
    && python --version \
    && dbt --version \
    && jq --version \
    && yq --version \
    && clickhouse --version \
    && clickhouse client --help >/dev/null \
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
    DBT_CORE_VERSION=${DBT_CORE_VERSION} \
    DBT_CLICKHOUSE_VERSION=${DBT_CLICKHOUSE_VERSION} \
    CLICKHOUSE_VERSION=${CLICKHOUSE_VERSION} \
    DISABLE_AUTOUPDATER=1

# Keep-alive default; each tool call runs the baked helper via a container exec
# (request on stdin, response on stdout) — no shell concatenation.
CMD ["sleep", "infinity"]
