#!/bin/sh
set -eu

source_file=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/Caddyfile.production
main_file=/etc/caddy/Caddyfile
site_dir=/etc/caddy/sites
site_file="$site_dir/gap-production.Caddyfile"

command -v caddy >/dev/null 2>&1 || { echo 'caddy_production_binary_missing' >&2; exit 1; }
test -f "$main_file" || { echo 'caddy_production_main_config_missing' >&2; exit 1; }
test -f /etc/caddy/production/runner-ca.crt || { echo 'caddy_production_callback_ca_missing' >&2; exit 1; }
grep -F 'import /etc/caddy/sites/*.Caddyfile' "$main_file" >/dev/null || {
  echo 'caddy_production_import_missing' >&2; exit 1;
}

install -d -m 0750 -o root -g caddy "$site_dir"
install -m 0640 -o root -g caddy "$source_file" "$site_file"
caddy validate --config "$main_file" --adapter caddyfile
systemctl reload caddy
