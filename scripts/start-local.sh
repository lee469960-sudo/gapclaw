#!/usr/bin/env bash
# GAP 本地一键启动（API + Vite 前端，无需 Docker）
# 默认在 API 就绪后启动 cloudflared（有 CLOUDFLARE_TUNNEL_TOKEN 用 named，否则 quick）。
# 跳过隧道：ENABLE_CLOUDFLARED=0 ./scripts/start-local.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$ROOT/.local/logs"
PID_DIR="$ROOT/.local/pids"
API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-5173}"
ENABLE_CLOUDFLARED="${ENABLE_CLOUDFLARED:-1}"

mkdir -p "$LOG_DIR" "$PID_DIR" "$ROOT/.local"

log() { printf '[gap] %s\n' "$*"; }

# Optional access-log rotation (50MB × 7). Non-fatal if logrotate missing.
if command -v logrotate >/dev/null 2>&1 && [ -f "$ROOT/deploy/logrotate.d/gap-local" ]; then
  sed "s|__GAP_ROOT__|${ROOT}|g" "$ROOT/deploy/logrotate.d/gap-local" \
    >"$ROOT/.local/logrotate.conf"
  logrotate -s "$ROOT/.local/logrotate.status" "$ROOT/.local/logrotate.conf" 2>/dev/null || true
fi

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    log "缺少命令: $1"
    exit 1
  fi
}

start_api() {
  cd "$ROOT/apps/api"
  # Only watch app/ source — never data/workplaces (LLM may write task/*.py mid-run).
  # Use ** globs; after changing excludes: stop-local && start-local.
  # Prefer WATCHFILES_FORCE_POLLING=0; exclude nested workplaces explicitly.
  nohup env WATCHFILES_FORCE_POLLING="${WATCHFILES_FORCE_POLLING:-0}" \
    .venv/bin/uvicorn app.main:app \
    --host 127.0.0.1 \
    --port "$API_PORT" \
    --log-config "$ROOT/apps/api/logging_config.json" \
    --reload \
    --reload-dir app \
    --reload-include '*.py' \
    --reload-exclude '**/data/**' \
    --reload-exclude 'data/**' \
    --reload-exclude '**/workplaces/**' \
    --reload-exclude '**/workplace/**' \
    --reload-exclude '**/task/**' \
    --reload-exclude '**/tmp/**' \
    --reload-exclude '**/lessons/**' \
    --reload-exclude '*.pyc' \
    --reload-exclude '*.json' \
    --reload-exclude '*.xlsx' \
    --reload-exclude '*.md' \
    >"$LOG_DIR/api.log" 2>&1 &
  echo $! >"$PID_DIR/api.pid"
}

wait_api() {
  local ok=0
  for _ in $(seq 1 60); do
    if curl -sf "http://127.0.0.1:${API_PORT}/health" >/dev/null 2>&1; then
      ok=1
      break
    fi
    sleep 1
  done
  if [ "$ok" != 1 ]; then
    log "API 启动失败，查看日志: $LOG_DIR/api.log"
    tail -20 "$LOG_DIR/api.log" || true
    exit 1
  fi
}

need_cmd python3
need_cmd npm
need_cmd curl

# 若已在运行，先停止（含 cloudflared）
if [ -f "$PID_DIR/api.pid" ] || [ -f "$PID_DIR/web.pid" ] || [ -f "$PID_DIR/cloudflared.pid" ]; then
  log "检测到旧进程，正在停止..."
  "$ROOT/scripts/stop-local.sh" || true
fi

# --- API ---
log "准备 API 环境..."
cd "$ROOT/apps/api"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
if [ ! -f .env ]; then
  cp .env.example .env
  log "已创建 apps/api/.env"
fi
.venv/bin/pip install -q -r requirements.txt

log "启动 API :$API_PORT ..."
start_api
wait_api

# --- cloudflared → PUBLIC_BASE_URL → 重启 API ---
PUBLIC_URL=""
if [ "$ENABLE_CLOUDFLARED" = "1" ]; then
  if command -v cloudflared >/dev/null 2>&1; then
    if [ -z "${CLOUDFLARED_MODE:-}" ]; then
      if [ -n "${CLOUDFLARE_TUNNEL_TOKEN:-}" ]; then
        export CLOUDFLARED_MODE=named
      else
        export CLOUDFLARED_MODE=quick
        log "未配置 CLOUDFLARE_TUNNEL_TOKEN，本地使用 Quick Tunnel（生产请配置 named token）"
      fi
    fi
    log "启动 cloudflared (${CLOUDFLARED_MODE}) 并配置 PUBLIC_BASE_URL ..."
    if "$ROOT/scripts/feishu-cloudflared.sh"; then
      PUBLIC_URL="$(grep -E '^PUBLIC_BASE_URL=' "$ROOT/apps/api/.env" 2>/dev/null | cut -d= -f2- | tr -d '\r' || true)"
      log "重启 API 以加载 PUBLIC_BASE_URL=${PUBLIC_URL}"
      # 释放端口后重启（--reload 父进程需整停才能重读 .env）
      if [ -f "$PID_DIR/api.pid" ]; then
        kill "$(cat "$PID_DIR/api.pid")" 2>/dev/null || true
        sleep 0.5
        kill -9 "$(cat "$PID_DIR/api.pid")" 2>/dev/null || true
        rm -f "$PID_DIR/api.pid"
      fi
      if command -v lsof >/dev/null 2>&1; then
        pids="$(lsof -ti tcp:"$API_PORT" 2>/dev/null || true)"
        if [ -n "${pids}" ]; then
          kill $pids 2>/dev/null || true
          sleep 0.5
        fi
      fi
      start_api
      wait_api
    else
      log "cloudflared 配置失败，继续启动（Webhook 仍可用 Mock；飞书需手动跑 scripts/feishu-cloudflared.sh）"
    fi
  else
    log "未安装 cloudflared，跳过公网隧道（安装后重跑 start-local，或手动执行 scripts/feishu-cloudflared.sh）"
  fi
else
  log "已跳过 cloudflared（ENABLE_CLOUDFLARED=0）"
fi

# --- Web ---
log "准备 Web 依赖..."
cd "$ROOT/apps/web"
if [ ! -d node_modules ]; then
  npm install
fi

log "启动 Web :$WEB_PORT ..."
nohup npm run dev -- --host 127.0.0.1 --port "$WEB_PORT" \
  >"$LOG_DIR/web.log" 2>&1 &
echo $! >"$PID_DIR/web.pid"

# --- 等待就绪 ---
log "等待 Web 就绪..."
web_ok=0
for _ in $(seq 1 60); do
  curl -sf "http://127.0.0.1:${WEB_PORT}/" >/dev/null 2>&1 && web_ok=1 && break
  sleep 1
done

if [ "$web_ok" != 1 ]; then
  log "Web 启动失败，查看日志: $LOG_DIR/web.log"
  tail -20 "$LOG_DIR/web.log" || true
  exit 1
fi

LOGIN_URL="http://127.0.0.1:${WEB_PORT}/login"

cat <<EOF

============================================
  GAP 本地环境已启动
============================================
  打开:  ${LOGIN_URL}
  Web:   http://127.0.0.1:${WEB_PORT}
  API:   http://127.0.0.1:${API_PORT}
  公网:  ${PUBLIC_URL:-（未配置 PUBLIC_BASE_URL）}
  账号:  admin / admin123
  日志:  ${LOG_DIR}/
  停止:  ${ROOT}/scripts/stop-local.sh
============================================

EOF

if command -v open >/dev/null 2>&1; then
  open "$LOGIN_URL" 2>/dev/null || true
fi
