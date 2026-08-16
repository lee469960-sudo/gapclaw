#!/usr/bin/env bash
# 启动 Cloudflare Tunnel（默认 named tunnel；CLOUDFLARED_MODE=quick 才用临时域名）。
# 用法：
#   CLOUDFLARE_TUNNEL_TOKEN=... TUNNEL_PUBLIC_BASE_URL=https://api.example.com ./scripts/feishu-cloudflared.sh
#   CLOUDFLARED_MODE=quick ./scripts/feishu-cloudflared.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API_PORT="${API_PORT:-8000}"
TARGET_URL="http://127.0.0.1:${API_PORT}"
LOG_DIR="$ROOT/.local/logs"
PID_DIR="$ROOT/.local/pids"
LOG_FILE="$LOG_DIR/cloudflared.log"
PID_FILE="$PID_DIR/cloudflared.pid"
WATCH_PID_FILE="$PID_DIR/cloudflared-watch.pid"
MODE="${CLOUDFLARED_MODE:-named}"
TOKEN="${CLOUDFLARE_TUNNEL_TOKEN:-}"
PUBLIC_URL="${TUNNEL_PUBLIC_BASE_URL:-}"
CONFIG_FILE="${CLOUDFLARED_CONFIG:-$ROOT/deploy/cloudflared/config.yml}"

mkdir -p "$LOG_DIR" "$PID_DIR"

log() { printf '[cloudflared] %s\n' "$*"; }

_update_public_base_url() {
  local public_url="$1"
  local env_file="$ROOT/apps/api/.env"
  if [ -z "$public_url" ]; then
    return 0
  fi
  log "公网域名: $public_url"
  if [ -f "$env_file" ]; then
    if grep -q '^PUBLIC_BASE_URL=' "$env_file"; then
      if sed --version >/dev/null 2>&1; then
        sed -i "s|^PUBLIC_BASE_URL=.*|PUBLIC_BASE_URL=${public_url}|" "$env_file"
      else
        sed -i '' "s|^PUBLIC_BASE_URL=.*|PUBLIC_BASE_URL=${public_url}|" "$env_file"
      fi
      log "已自动更新 $env_file 中的 PUBLIC_BASE_URL"
    else
      echo "PUBLIC_BASE_URL=${public_url}" >>"$env_file"
      log "已追加 PUBLIC_BASE_URL 到 $env_file"
    fi
  else
    log "未找到 $env_file，请手动配置 PUBLIC_BASE_URL=${public_url}"
  fi
}

_start_watch() {
  if [ -f "$WATCH_PID_FILE" ]; then
    old="$(cat "$WATCH_PID_FILE" 2>/dev/null || true)"
    if [ -n "${old}" ] && kill -0 "$old" 2>/dev/null; then
      kill "$old" 2>/dev/null || true
    fi
    rm -f "$WATCH_PID_FILE"
  fi
  nohup env OPS_ALERT_SECRET="${OPS_ALERT_SECRET:-}" API_PORT="$API_PORT" \
    "$ROOT/scripts/cloudflared-watch.sh" >>"$LOG_DIR/cloudflared-watch.log" 2>&1 &
  echo $! >"$WATCH_PID_FILE"
  log "已启动 ERR watcher (pid=$(cat "$WATCH_PID_FILE"))"
}

if ! command -v cloudflared >/dev/null 2>&1; then
  log "未找到 cloudflared。安装示例："
  log "  macOS:  brew install cloudflare/cloudflare/cloudflared"
  exit 1
fi

if ! curl -sf "http://127.0.0.1:${API_PORT}/health" >/dev/null 2>&1; then
  log "警告: 本机 ${TARGET_URL}/health 不可达。请先启动 API。"
fi

if [ -f "$PID_FILE" ]; then
  old_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "${old_pid}" ] && kill -0 "$old_pid" 2>/dev/null; then
    log "已有 cloudflared 在运行 (pid=$old_pid)，先停止..."
    kill "$old_pid" 2>/dev/null || true
    sleep 1
  fi
  rm -f "$PID_FILE"
fi

: >"$LOG_FILE"

if [ "$MODE" = "quick" ]; then
  log "CLOUDFLARED_MODE=quick → Quick Tunnel → ${TARGET_URL}"
  nohup cloudflared tunnel --url "$TARGET_URL" --no-autoupdate >>"$LOG_FILE" 2>&1 &
  echo $! >"$PID_FILE"
  cf_pid="$(cat "$PID_FILE")"
  public_url=""
  for _ in $(seq 1 40); do
    if grep -Eo 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' "$LOG_FILE" >/dev/null 2>&1; then
      public_url="$(grep -Eo 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' "$LOG_FILE" | head -n1)"
      break
    fi
    if ! kill -0 "$cf_pid" 2>/dev/null; then
      log "cloudflared 已退出，日志："
      tail -n 40 "$LOG_FILE" || true
      exit 1
    fi
    sleep 0.5
  done
  if [ -z "$public_url" ]; then
    log "超时未解析到 trycloudflare.com 域名，请查看日志: $LOG_FILE"
    exit 1
  fi
  _update_public_base_url "$public_url"
else
  if [ -z "$TOKEN" ] && [ ! -f "$CONFIG_FILE" ]; then
    log "named tunnel 需要 CLOUDFLARE_TUNNEL_TOKEN，或可写的 $CONFIG_FILE"
    log "本地临时逃生：CLOUDFLARED_MODE=quick ./scripts/feishu-cloudflared.sh"
    exit 1
  fi
  if [ -z "$PUBLIC_URL" ] && [ -n "${TOKEN}" ]; then
    # Allow .env already holding PUBLIC_BASE_URL
    PUBLIC_URL="$(grep -E '^PUBLIC_BASE_URL=' "$ROOT/apps/api/.env" 2>/dev/null | cut -d= -f2- | tr -d '\r' || true)"
  fi
  if [ -z "$PUBLIC_URL" ]; then
    log "named tunnel 需要 TUNNEL_PUBLIC_BASE_URL（或 .env 中已有 PUBLIC_BASE_URL）"
    exit 1
  fi
  log "启动 named tunnel → ${TARGET_URL} public=${PUBLIC_URL}"
  if [ -n "$TOKEN" ]; then
    nohup cloudflared tunnel --no-autoupdate run --token "$TOKEN" >>"$LOG_FILE" 2>&1 &
  else
    nohup cloudflared tunnel --no-autoupdate --config "$CONFIG_FILE" run >>"$LOG_FILE" 2>&1 &
  fi
  echo $! >"$PID_FILE"
  sleep 1
  if ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    log "cloudflared 启动失败，日志："
    tail -n 40 "$LOG_FILE" || true
    exit 1
  fi
  _update_public_base_url "$PUBLIC_URL"
fi

_start_watch

log "pid=$(cat "$PID_FILE")  日志: $LOG_FILE"
echo
echo "飞书事件订阅 URL 示例："
echo "  ${PUBLIC_URL:-<PUBLIC_BASE_URL>}/hooks/channels/feishu/<channel_id>/<webhook_secret>"
echo "停止隧道: kill \$(cat $PID_FILE); kill \$(cat $WATCH_PID_FILE 2>/dev/null)"
echo
