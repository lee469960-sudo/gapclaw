#!/usr/bin/env bash
# Seed MinMax LLM + dba Agent by restarting API (reads apps/api/.env)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/apps/api/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE — copy from apps/api/.env.example first."
  exit 1
fi

# shellcheck disable=SC1090
source "$ENV_FILE" 2>/dev/null || true

if [[ -z "${MINIMAX_API_KEY:-}" ]]; then
  echo "MINIMAX_API_KEY is not set in $ENV_FILE"
  echo "Add: MINIMAX_API_KEY=sk-cp-..."
  exit 1
fi

echo "Restarting API to run seed (MinMax LLM + dba-sandbox + dba Agent)..."
"$ROOT/scripts/stop-local.sh" 2>/dev/null || true
"$ROOT/scripts/start-local.sh"

sleep 3

API="http://127.0.0.1:8000"
COOKIE_JAR=$(mktemp)
trap 'rm -f "$COOKIE_JAR"' EXIT

login=$(curl -s -c "$COOKIE_JAR" -X POST "$API/login.cgi" \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}')
if ! echo "$login" | grep -q '"code":0'; then
  echo "Login failed: $login"
  exit 1
fi

agents=$(curl -s -b "$COOKIE_JAR" "$API/pages/page_agent.cgi?action=list&scope=all")
dba_id=$(echo "$agents" | python3 -c "
import json,sys
d=json.load(sys.stdin)
for a in d.get('data') or []:
    if a.get('name')=='dba':
        print(a['id']); break
" 2>/dev/null || true)

llms=$(curl -s -b "$COOKIE_JAR" "$API/pages/page_llm.cgi?action=list&scope=all")
minmax_id=$(echo "$llms" | python3 -c "
import json,sys
d=json.load(sys.stdin)
for x in d.get('data') or []:
    if x.get('name')=='MinMax':
        print(x['id']); break
" 2>/dev/null || true)

echo ""
echo "=== Seed result ==="
echo "MinMax LLM ID: ${minmax_id:-not found}"
echo "dba Agent ID:  ${dba_id:-not found (check SEED_DBA_AGENT=true)}"
echo ""
echo "Web UI:"
echo "  LLMs:   http://127.0.0.1:5173/llms"
echo "  Agents: http://127.0.0.1:5173/agents"
if [[ -n "${dba_id:-}" ]]; then
  echo "  Chat:   http://127.0.0.1:5173/agents/${dba_id}/chat"
fi
