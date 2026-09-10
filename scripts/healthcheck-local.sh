#!/usr/bin/env bash
# GAP local runtime healthcheck: API, Web, cloudflared, public URL, and IM event DB.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${LOG_DIR:-$ROOT/.local/logs}"
DB_PATH="${GAP_DB_PATH:-$ROOT/apps/api/data/gap.db}"
API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-5173}"
CLOUDFLARED_LOG="${CLOUDFLARED_LOG:-$LOG_DIR/cloudflared.log}"

overall=0

info() { printf '[INFO] %s\n' "$*"; }
pass() { printf '[PASS] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*"; [ "$overall" -lt 1 ] && overall=1; return 0; }
fail() { printf '[FAIL] %s\n' "$*"; overall=2; }

has_cmd() {
  command -v "$1" >/dev/null 2>&1
}

stat_mtime() {
  local path="$1"
  if stat -f '%Sm' "$path" >/dev/null 2>&1; then
    stat -f '%Sm' "$path"
  else
    stat -c '%y' "$path" 2>/dev/null || printf 'unknown'
  fi
}

listen_pids() {
  local port="$1"
  if has_cmd lsof; then
    lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
  else
    return 1
  fi
}

http_probe() {
  local url="$1"
  curl --noproxy '*' -sS -o /dev/null \
    -w 'http_code=%{http_code} exit=%{exitcode} err=%{errormsg}' \
    --max-time 10 "$url" 2>/dev/null || true
}

latest_public_url() {
  if [ ! -f "$CLOUDFLARED_LOG" ]; then
    return 0
  fi
  grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$CLOUDFLARED_LOG" | tail -1 || true
}

check_port() {
  local name="$1"
  local port="$2"
  local url="$3"
  if listen_pids "$port" | grep -q 'LISTEN'; then
    pass "$name listens on 127.0.0.1:$port"
  else
    fail "$name is not listening on port $port"
    return
  fi

  local probe
  probe="$(http_probe "$url")"
  case "$probe" in
    *'http_code=2'*|*'http_code=3'*)
      pass "$name HTTP probe ok ($probe)"
      ;;
    *)
      warn "$name HTTP probe did not return 2xx/3xx ($probe)"
      ;;
  esac
}

check_cloudflared() {
  if [ ! -f "$CLOUDFLARED_LOG" ]; then
    fail "cloudflared log missing: $CLOUDFLARED_LOG"
    return
  fi

  info "cloudflared log mtime: $(stat_mtime "$CLOUDFLARED_LOG")"

  if has_cmd lsof && lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null | grep -qi '^cloudflar'; then
    pass "cloudflared has a local listening socket"
  else
    warn "cloudflared listening socket not found via lsof"
  fi

  local recent_retry_count
  recent_retry_count="$(tail -80 "$CLOUDFLARED_LOG" | grep -Ec 'Retrying connection|failed to serve tunnel connection|Serve tunnel error' || true)"
  if [ "$recent_retry_count" -gt 0 ]; then
    warn "cloudflared recent retry/error lines in last 80 log lines: $recent_retry_count"
  else
    pass "cloudflared recent log has no retry/error lines"
  fi

  local public_url
  public_url="$(latest_public_url)"
  if [ -z "$public_url" ]; then
    fail "cloudflared public trycloudflare URL not found in log"
    return
  fi
  info "cloudflared public URL: $public_url"

  local proxy_vars
  proxy_vars="$(env | grep -E '^(HTTP_PROXY|HTTPS_PROXY|ALL_PROXY|NO_PROXY)=' || true)"
  if [ -n "$proxy_vars" ]; then
    info "proxy env present; public probe uses --noproxy '*'"
    printf '%s\n' "$proxy_vars"
  fi

  local probe
  probe="$(http_probe "$public_url")"
  case "$probe" in
    *'http_code=2'*|*'http_code=3'*)
      pass "public tunnel HTTP probe ok ($probe)"
      ;;
    *)
      warn "public tunnel HTTP probe failed or non-2xx/3xx ($probe)"
      ;;
  esac
}

check_db() {
  if [ ! -f "$DB_PATH" ]; then
    fail "SQLite DB missing: $DB_PATH"
    return
  fi
  if ! has_cmd sqlite3; then
    warn "sqlite3 is not installed; skipping DB probe"
    return
  fi

  if ! sqlite3 "$DB_PATH" "SELECT 1 FROM sqlite_master WHERE type='table' AND name='im_event_logs';" | grep -q 1; then
    fail "DB is readable but table im_event_logs is missing"
    return
  fi

  local row
  row="$(sqlite3 "$DB_PATH" "SELECT COUNT(*), COALESCE(MAX(created_at),'') FROM im_event_logs;" 2>/dev/null || true)"
  if [ -z "$row" ]; then
    warn "im_event_logs exists but could not read count/latest timestamp"
    return
  fi
  pass "im_event_logs readable (count|max_created_at=$row)"
}

info "GAP local healthcheck root: $ROOT"
check_port "API" "$API_PORT" "http://127.0.0.1:${API_PORT}/health"
check_port "Web" "$WEB_PORT" "http://127.0.0.1:${WEB_PORT}/"
check_cloudflared
check_db

case "$overall" in
  0)
    pass "overall health: green"
    ;;
  1)
    warn "overall health: yellow"
    ;;
  *)
    fail "overall health: red"
    ;;
esac

exit "$overall"
