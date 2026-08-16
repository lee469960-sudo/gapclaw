#!/usr/bin/env bash
# Watch cloudflared.log for ERR lines and POST /hooks/ops/alert.
# Usage: OPS_ALERT_SECRET=... ./scripts/cloudflared-watch.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_FILE="${CLOUDFLARED_LOG:-$ROOT/.local/logs/cloudflared.log}"
API_PORT="${API_PORT:-8000}"
ALERT_URL="${OPS_ALERT_URL:-http://127.0.0.1:${API_PORT}/hooks/ops/alert}"
PID_FILE="$ROOT/.local/pids/cloudflared-watch.pid"
SECRET="${OPS_ALERT_SECRET:-}"

mkdir -p "$(dirname "$LOG_FILE")" "$ROOT/.local/pids"
echo $$ >"$PID_FILE"

log() { printf '[cf-watch] %s\n' "$*"; }

if [ -z "$SECRET" ]; then
  log "OPS_ALERT_SECRET 未设置，watcher 仅打印 ERR（不告警）"
fi

touch "$LOG_FILE"
log "watching $LOG_FILE → $ALERT_URL"

# Dedup: same line within 60s
last_key=""
last_ts=0

while IFS= read -r line; do
  case "$line" in
    *ERR*|*error*|*Error*)
      ;;
    *)
      continue
      ;;
  esac
  # Prefer cloudflared level token ERR
  if ! printf '%s' "$line" | grep -Eq '\bERR\b'; then
    continue
  fi
  now="$(date +%s)"
  key="$(printf '%s' "$line" | head -c 200)"
  if [ "$key" = "$last_key" ] && [ $((now - last_ts)) -lt 60 ]; then
    continue
  fi
  last_key="$key"
  last_ts="$now"
  log "ERR: $line"
  if [ -n "$SECRET" ]; then
    curl -sf -X POST "$ALERT_URL" \
      -H "Content-Type: application/json" \
      -H "X-Ops-Secret: ${SECRET}" \
      -d "$(python3 -c 'import json,sys; print(json.dumps({"source":"cloudflared","message":sys.argv[1][:500],"detail":sys.argv[1][:2000],"level":"error"}))' "$line")" \
      >/dev/null 2>&1 || log "alert POST failed"
  fi
done < <(tail -n0 -F "$LOG_FILE")
