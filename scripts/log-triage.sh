#!/usr/bin/env bash
# GAP local log triage: recent-window and all-time counters for noisy failures.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${LOG_DIR:-$ROOT/.local/logs}"
API_LOG="${API_LOG:-$LOG_DIR/api.log}"
WINDOW_LINES="${WINDOW_LINES:-1000}"

section() { printf '\n## %s\n' "$*"; }
count_in_file() {
  local pattern="$1"
  local file="$2"
  grep -c "$pattern" "$file" 2>/dev/null || true
}
count_in_recent() {
  local pattern="$1"
  local file="$2"
  tail -n "$WINDOW_LINES" "$file" 2>/dev/null | grep -c "$pattern" || true
}
count_egrep_in_file() {
  local pattern="$1"
  local file="$2"
  grep -Ec "$pattern" "$file" 2>/dev/null || true
}
count_egrep_in_recent() {
  local pattern="$1"
  local file="$2"
  tail -n "$WINDOW_LINES" "$file" 2>/dev/null | grep -Ec "$pattern" || true
}

if [ ! -f "$API_LOG" ]; then
  printf '[FAIL] api log missing: %s\n' "$API_LOG"
  exit 2
fi

printf '[INFO] api log: %s\n' "$API_LOG"
printf '[INFO] recent window: last %s lines\n' "$WINDOW_LINES"

section "Focused Counters"
printf '%-28s recent=%-8s all=%s\n' \
  'telegram poll failed' \
  "$(count_in_recent 'telegram poll failed' "$API_LOG")" \
  "$(count_in_file 'telegram poll failed' "$API_LOG")"
printf '%-28s recent=%-8s all=%s\n' \
  'LLM status=429' \
  "$(count_in_recent 'status=429' "$API_LOG")" \
  "$(count_in_file 'status=429' "$API_LOG")"
printf '%-28s recent=%-8s all=%s\n' \
  "ConnectError('')" \
  "$(count_in_recent "ConnectError('')" "$API_LOG")" \
  "$(count_in_file "ConnectError('')" "$API_LOG")"
printf '%-28s recent=%-8s all=%s\n' \
  'check_status 304' \
  "$(tail -n "$WINDOW_LINES" "$API_LOG" 2>/dev/null | grep 'check_status' | grep -c ' 304' || true)" \
  "$(grep 'check_status' "$API_LOG" 2>/dev/null | grep -c ' 304' || true)"
printf '%-28s recent=%-8s all=%s\n' \
  'no_progress_hint' \
  "$(count_in_recent 'no_progress_hint' "$API_LOG")" \
  "$(count_in_file 'no_progress_hint' "$API_LOG")"

section "Structured Counters"
printf '%-28s recent=%-8s all=%s\n' \
  'LLM throttling' \
  "$(count_egrep_in_recent 'component=llm class=throttling' "$API_LOG")" \
  "$(count_egrep_in_file 'component=llm class=throttling' "$API_LOG")"
printf '%-28s recent=%-8s all=%s\n' \
  'LLM network' \
  "$(count_egrep_in_recent 'component=llm class=network_connectivity' "$API_LOG")" \
  "$(count_egrep_in_file 'component=llm class=network_connectivity' "$API_LOG")"
printf '%-28s recent=%-8s all=%s\n' \
  'Telegram failures' \
  "$(count_egrep_in_recent 'component=telegram class=' "$API_LOG")" \
  "$(count_egrep_in_file 'component=telegram class=' "$API_LOG")"
printf '%-28s recent=%-8s all=%s\n' \
  'Agent no-progress' \
  "$(count_egrep_in_recent 'component=agent_runtime class=no_progress' "$API_LOG")" \
  "$(count_egrep_in_file 'component=agent_runtime class=no_progress' "$API_LOG")"

section "Recent Top Error Tokens"
tail -n "$WINDOW_LINES" "$API_LOG" 2>/dev/null \
  | grep -oE '[A-Za-z_.]*Error\b' \
  | sort \
  | uniq -c \
  | sort -rn \
  | head -10 || true

section "All-Time Top Error Tokens"
grep -oE '[A-Za-z_.]*Error\b' "$API_LOG" 2>/dev/null \
  | sort \
  | uniq -c \
  | sort -rn \
  | head -10 || true

section "Recent ERROR/WARNING Lines"
tail -n "$WINDOW_LINES" "$API_LOG" 2>/dev/null \
  | grep -Ei 'ERROR|WARNING|Traceback|Exception' \
  | tail -40 || true
