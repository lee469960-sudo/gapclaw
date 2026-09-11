#!/bin/sh
set -eu

runner_root=/opt/gap-runner
app_root=/opt/gap

getent group gap-runner >/dev/null || groupadd --system gap-runner
id -u gap-runner >/dev/null 2>&1 || useradd --system --gid gap-runner --home-dir "$runner_root" --shell /usr/sbin/nologin gap-runner

install -d -m 0750 -o root -g gap-runner "$runner_root" "$runner_root/bin" "$runner_root/compose" "$runner_root/tls"
install -d -m 0750 -o gap-runner -g gap-runner "$runner_root/state"
install -d -m 0750 -o root -g gap-runner "$app_root"
install -d -m 0750 -o root -g gap-runner "$app_root/caddy"
install -m 0640 -o root -g gap-runner "$(dirname "$0")/gap-prod.compose.yml" "$runner_root/compose/gap-prod.compose.yml"
install -m 0640 -o root -g gap-runner "$(dirname "$0")/runner.env.example" "$runner_root/runner.env.example"
install -m 0640 -o root -g gap-runner "$(dirname "$0")/Caddyfile.production" "$app_root/caddy/Caddyfile.production"
install -m 0644 -o root -g root "$(dirname "$0")/gap-deploy-runner.service" /etc/systemd/system/gap-deploy-runner.service
sh "$(dirname "$0")/install-runner-binaries.sh" "$runner_root"

printf '%s\n' 'Create /opt/gap/.env, install CA/certificate/key, then run install-production-caddy.sh before enabling the service.'
