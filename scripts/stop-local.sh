#!/usr/bin/env bash
# 停止 GAP 本地开发进程
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PID_DIR="$ROOT/.local/pids"
API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-5173}"

stop_pid() {
  local name="$1"
  local file="$PID_DIR/$name.pid"
  if [ -f "$file" ]; then
    local pid
    pid="$(cat "$file")"
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      sleep 0.5
      kill -9 "$pid" 2>/dev/null || true
      printf '[gap] 已停止 %s (pid %s)\n' "$name" "$pid"
    fi
    rm -f "$file"
  fi
}

stop_pid api
stop_pid web
stop_pid cloudflared
stop_pid cloudflared-watch

# 兜底：按端口清理残留
for port in "$API_PORT" "$WEB_PORT"; do
  if command -v lsof >/dev/null 2>&1; then
    pids="$(lsof -ti tcp:"$port" 2>/dev/null || true)"
    if [ -n "$pids" ]; then
      kill $pids 2>/dev/null || true
      printf '[gap] 已释放端口 %s\n' "$port"
    fi
  fi
done

# 兜底：残留 cloudflared / watcher
if command -v pkill >/dev/null 2>&1; then
  pkill -f "cloudflared tunnel --url http://127.0.0.1:${API_PORT}" 2>/dev/null || true
  pkill -f "cloudflared tunnel.*run" 2>/dev/null || true
  pkill -f "scripts/cloudflared-watch.sh" 2>/dev/null || true
fi

printf '[gap] 本地服务已停止\n'
