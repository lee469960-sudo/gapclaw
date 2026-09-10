# GAP Local Healthcheck Runbook

This runbook turns the old V1-V10 ad hoc checks into two repeatable local
commands:

```bash
./scripts/healthcheck-local.sh
./scripts/log-triage.sh
```

## Healthcheck

`scripts/healthcheck-local.sh` checks the current runtime state:

| Area | Signal | Notes |
| --- | --- | --- |
| API | `lsof` on `API_PORT`, then `GET /health` | Avoids relying on `pgrep`, which can fail in restricted environments. |
| Web | `lsof` on `WEB_PORT`, then `GET /` | Confirms the Vite server is listening and serving HTTP. |
| cloudflared | local listener, recent retry lines, latest trycloudflare URL | A running process is not enough; repeated retry lines mean degraded tunnel health. |
| Public tunnel | `curl --noproxy '*'` against the latest trycloudflare URL | Bypasses local proxy env so proxy failures are not mistaken for tunnel failures. |
| IM event DB | SQLite table `im_event_logs` count and latest `created_at` | `im_events` is the log source name; the actual table is `im_event_logs`. |

Exit codes:

| Code | Meaning |
| --- | --- |
| `0` | Green: no failed or degraded checks. |
| `1` | Yellow: runtime exists, but at least one degraded signal needs attention. |
| `2` | Red: a required local component is missing or unreadable. |

Useful overrides:

```bash
API_PORT=8000 WEB_PORT=5173 ./scripts/healthcheck-local.sh
GAP_DB_PATH=apps/api/data/gap.db ./scripts/healthcheck-local.sh
CLOUDFLARED_LOG=.local/logs/cloudflared.log ./scripts/healthcheck-local.sh
```

## Log Triage

`scripts/log-triage.sh` separates recent-window signals from all-time totals.
The default recent window is the last 1000 lines:

```bash
WINDOW_LINES=1000 ./scripts/log-triage.sh
```

It reports:

| Counter | Why It Matters |
| --- | --- |
| `telegram poll failed` | Poller instability or Telegram network/API issues. |
| `status=429` | LLM provider throttling, quota pressure, or retry pressure. |
| `ConnectError('')` | Ambiguous network connection failures that need provider/proxy context. |
| `check_status 304` | Expected polling noise; high volume can drown out useful logs. |
| `no_progress_hint` | Agent loop progress pressure; high volume should be aggregated in a later logging change. |
| top `*Error` tokens | Fast error distribution without reading the whole log. |

Use the recent count for "is it still happening now?" and the all-time count
for "has this been a repeated class of failure?"

Structured counters are emitted when the application logs include stable
key-value fields:

| Structured Counter | Matching Fields |
| --- | --- |
| `LLM throttling` | `component=llm class=throttling` |
| `LLM network` | `component=llm class=network_connectivity` |
| `Telegram failures` | `component=telegram class=<failure>` |
| `Agent no-progress` | `component=agent_runtime class=no_progress` |

## Interpreting Common Results

If cloudflared is listening but the public probe fails:

1. Check whether the script printed proxy env variables.
2. Confirm the public probe used `--noproxy '*'`.
3. Inspect the last cloudflared lines for repeated `Retrying connection` or
   `failed to serve tunnel connection`.
4. Restart only the tunnel if API/Web are otherwise healthy.

If DB probing says `im_event_logs` is old:

1. Treat it as "IM event log is stale", not necessarily "Telegram poller is
   down".
2. Compare with `telegram poll failed` in `scripts/log-triage.sh`.
3. If poller requests are succeeding but `im_event_logs` is stale, inspect the
   alert/log ingestion path rather than the Telegram API path.

If `status=429` is high:

1. Do not classify it as a code bug by default.
2. Check provider quota, concurrency, and retry behavior.
3. Prefer a follow-up logging/runtime change that adds provider-level
   backoff, aggregation, and user-visible degradation.

## Structured Runtime Fields

The local runtime keeps plain-text logs but adds grep-friendly structured
fields for common operational classes:

- API access logs suppress expected `check_status` HTTP 304 polling noise while
  preserving non-304 and unrelated access logs.
- Agent loop no-progress logs use `component=agent_runtime class=no_progress`
  with `agent`, `iter`, `streak`, `repeat_count`, and `interval`.
- Telegram poll failures use `component=telegram class=<failure>` with safe
  `channel`, `exception`, `repeat_count`, `retry_delay_s`, and `duration_s`
  fields; successful recovery emits `telegram poll recovered`.
- LLM retry logs use `component=llm class=throttling`,
  `class=transient_http`, or `class=network_connectivity`, with `provider`,
  `model`, `status`, `attempt`, `max_attempts`, `backoff_s`, `exception`, and a
  sanitized `endpoint` that omits query strings.
