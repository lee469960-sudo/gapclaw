#!/bin/sh
set -eu

runner_root=/opt/gap-staging-runner
app_root=/opt/gap-staging

getent group gap-staging-runner >/dev/null || groupadd --system gap-staging-runner
id -u gap-staging-runner >/dev/null 2>&1 || useradd --system --gid gap-staging-runner --home-dir "$runner_root" --shell /usr/sbin/nologin gap-staging-runner

install -d -m 0750 -o root -g gap-staging-runner "$runner_root" "$runner_root/bin" "$runner_root/compose" "$runner_root/tls"
install -d -m 0750 -o gap-staging-runner -g gap-staging-runner "$runner_root/state"
install -d -m 0750 -o root -g gap-staging-runner "$app_root"
install -m 0640 -o root -g gap-staging-runner "$(dirname "$0")/gap-staging.compose.yml" "$runner_root/compose/gap-staging.compose.yml"
install -m 0640 -o root -g gap-staging-runner "$(dirname "$0")/runner-staging.env.example" "$runner_root/runner.env.example"
install -m 0640 -o root -g gap-staging-runner "$(dirname "$0")/gap-staging.env.example" "$app_root/.env.example"
install -d -m 0750 -o root -g gap-staging-runner "$app_root/caddy"
install -m 0640 -o root -g gap-staging-runner "$(dirname "$0")/Caddyfile.staging" "$app_root/caddy/Caddyfile.staging"
install -m 0644 -o root -g root "$(dirname "$0")/gap-deploy-runner-staging.service" /etc/systemd/system/gap-deploy-runner-staging.service
sh "$(dirname "$0")/install-runner-binaries.sh" "$runner_root"

printf '%s\n' 'Create /opt/gap-staging/.env, install staging-only CA/certificate/key, then run install-staging-caddy.sh before enabling the service.'
