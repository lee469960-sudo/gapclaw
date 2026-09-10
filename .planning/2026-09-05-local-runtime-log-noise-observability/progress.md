# Progress: local-runtime-log-noise-observability

## 2026-09-05

### Planning Initialization

- Read `openspec/changes/local-runtime-log-noise-observability/proposal.md`.
- Read `openspec/changes/local-runtime-log-noise-observability/design.md`.
- Read all delta specs under `openspec/changes/local-runtime-log-noise-observability/specs/`.
- Read `openspec/changes/local-runtime-log-noise-observability/tasks.md`.
- Ran Planning with Files session catchup; no unsynced context was reported.
- Created this planning workspace under `.planning/2026-09-05-local-runtime-log-noise-observability/`.
- Set execution rule: after each OpenSpec task, update this file and verify code/tests before checking off `tasks.md`.

### Current Status

- Phase 6 complete.
- OpenSpec tasks 1.1 through 6.4 completed and checked off after verification.
- Implementation is complete; next step is OpenSpec verify/archive if desired.

### Phase 1: Access Log Noise Control

- Added `app.logging_filters.SuppressCheckStatus304Filter`.
- Bound the filter to the `uvicorn.access` handler in `apps/api/logging_config.json`.
- Added `apps/api/tests/test_logging_filters.py` covering expected 304 suppression, non-304 `check_status` preservation, and unrelated access log preservation.
- Verified existing `check_status` endpoint ETag/304 behavior still passes.

### Phase 2: Agent Runtime Progress Logging

- Added `app.services.agent_runtime.observability.NoProgressHintLogAggregator`.
- Replaced the raw `modular_loop no_progress_hint` log call with aggregate structured fields: `component=agent_runtime`, `class=no_progress`, `agent`, `iter`, `streak`, `repeat_count`, and `interval`.
- Kept the existing coach hint cadence and Agent loop behavior unchanged.
- Added `apps/api/tests/test_agent_runtime_observability.py` for aggregate fields, progress reset, and per-agent isolation.

### Phase 3: Telegram Poller Observability

- Added per-channel Telegram poll failure streak state in `apps/api/app/services/channels/telegram_poller.py`.
- First failure and failure-class changes log structured context with traceback for real exceptions.
- Repeated same-class failures log compact aggregate lines with `component=telegram`, `class`, `channel`, `exception`, `repeat_count`, `retry_delay_s`, and `duration_s`.
- Successful polling after failures emits a recovery log and clears the streak.
- Added `apps/api/tests/test_telegram_poller_observability.py` for first failure, repeated same-class failure, changed failure class, and recovery.

### Phase 4: LLM Error Classification

- Added structured LLM retry logging in `apps/api/app/services/llm_client.py`.
- HTTP 429/529 logs use `class=throttling`; 502/503/504 use `class=transient_http`; transport errors use `class=network_connectivity`.
- Retry logs include `component=llm`, `provider`, `model`, `status`, `attempt`, `max_attempts`, `backoff_s`, `exception`, and sanitized `endpoint`.
- Sanitized endpoint category strips query strings from `base_url`, preventing token-bearing URL leakage in logs.
- Extended `apps/api/tests/test_llm_transport_retry.py` for 429 throttling logs, transport exhaustion logs, and secret-free formatted output.

### Phase 5: Diagnostics Consumers

- Updated `scripts/log-triage.sh` to keep old text counters and add structured counters for LLM throttling, LLM network failures, Telegram failures, and Agent no-progress logs.
- Added `structured_class_counts` to `apps/api/mcp_servers/system_logs/tools.py` as an additive `log_stats` field.
- Added `apps/api/tests/test_system_logs_structured_stats.py` to verify structured class counting and empty counts for old plain logs.
- Updated `docs/ops/local-healthcheck.md` and `docs/system-log-analyst.md` with the final structured field names and compatibility notes.
- Verified `scripts/log-triage.sh` against current logs and a temporary old-format log.

### Phase 6: Acceptance

- Ran targeted backend tests covering access-log filtering, Agent runtime aggregation, Telegram poller streaks, LLM retry classification, and system-logs compatibility.
- Ran `./scripts/log-triage.sh` against the current local `api.log`; output distinguishes recent-window counts from all-time totals and includes structured counters.
- Ran `./scripts/healthcheck-local.sh`; it returned yellow with explicit runtime degradation signals: local listener present but HTTP probes failed, cloudflared retry lines present, public tunnel DNS failed, and `im_event_logs` readable. This indicates current runtime state rather than a script or table-name failure.
- Ran strict OpenSpec validation successfully.
- Ran `git diff --check` successfully.

### Post-Acceptance: Local Ollama ReAct Fix

- Added `supports_native_tools()` in `apps/api/app/services/llm_client.py`.
- Changed chat request construction so resolved Ollama leaf models omit OpenAI-style `tools`, even when reached through an OpenAI-flavored model group.
- Kept OpenAI/MiniMax native tools behavior unchanged.
- Added regression coverage in `apps/api/tests/test_llm_native_tools.py` for direct Ollama and group→Ollama routing.

### Post-Acceptance: Cached MCP Stop Diagnosis

- Inspected runtime stop path for `cached_reference_converged`.
- Inspected `得到大脑` agent and `M3 AND V4` LLM group configuration.
- Inspected failed chat message `1807` and successful continuation `1816`.
- Confirmed the failure mode is repeated cached MCP calls after materialization, not MCP connectivity failure.
- No code changes were made for this diagnosis.

## Verification Log

| Check | Result |
|---|---|
| OpenSpec artifacts read | complete |
| Planning files initialized | complete |
| `python3 -m json.tool apps/api/logging_config.json >/dev/null` | passed |
| `apps/api/.venv/bin/pytest apps/api/tests/test_logging_filters.py apps/api/tests/test_check_status_etag.py` | 4 passed, 1 warning |
| `python3 -m compileall -q apps/api/app/services/agent_runtime/observability.py apps/api/app/services/agent_runtime/runtime.py` | passed |
| `apps/api/.venv/bin/pytest apps/api/tests/test_agent_runtime_observability.py apps/api/tests/test_react_engine_v15.py` | 9 passed, 9 warnings |
| `python3 -m compileall -q apps/api/app/services/channels/telegram_poller.py` | passed |
| `apps/api/.venv/bin/pytest apps/api/tests/test_telegram_poller_observability.py` | 4 passed, 1 warning |
| `python3 -m compileall -q apps/api/app/services/llm_client.py apps/api/tests/test_llm_transport_retry.py` | passed |
| `apps/api/.venv/bin/pytest apps/api/tests/test_llm_transport_retry.py apps/api/tests/test_react_engine_v10.py` | 25 passed, 1 warning |
| `python3 -m compileall -q apps/api/mcp_servers/system_logs/tools.py apps/api/tests/test_system_logs_structured_stats.py` | passed |
| `apps/api/.venv/bin/pytest apps/api/tests/test_system_logs_structured_stats.py apps/api/tests/test_im_events_source_meta.py` | 3 passed |
| `bash -n scripts/log-triage.sh scripts/healthcheck-local.sh` | passed |
| `./scripts/log-triage.sh` | passed against current local log |
| `API_LOG=<tmp-old-format-log> WINDOW_LINES=100 ./scripts/log-triage.sh` | passed against old text log |
| `apps/api/.venv/bin/pytest apps/api/tests/test_logging_filters.py apps/api/tests/test_check_status_etag.py apps/api/tests/test_agent_runtime_observability.py apps/api/tests/test_react_engine_v15.py apps/api/tests/test_telegram_poller_observability.py apps/api/tests/test_llm_transport_retry.py apps/api/tests/test_react_engine_v10.py apps/api/tests/test_system_logs_structured_stats.py apps/api/tests/test_im_events_source_meta.py` | 45 passed, 9 warnings |
| `./scripts/healthcheck-local.sh` | completed with yellow runtime state |
| `openspec validate local-runtime-log-noise-observability --strict` | valid |
| `git diff --check` | passed |
| OpenSpec tasks checked off | 1.1 through 6.4 |
| `apps/api/.venv/bin/pytest apps/api/tests/test_llm_native_tools.py apps/api/tests/test_llm_transport_retry.py apps/api/tests/test_tool_parser_mcp_formats.py apps/api/tests/test_single_loop_e2e.py` | 35 passed, 7 warnings |
| `python3 -m compileall -q apps/api/app/services/llm_client.py apps/api/app/services/tool_parser.py` | passed |
| `git diff --check` after Ollama fix | passed |
| `openspec status --change "local-runtime-log-noise-observability" --json` on 2026-09-08 | complete; spec-driven change, 17/17 tasks done |
| `apps/api/.venv/bin/pytest apps/api/tests/test_logging_filters.py apps/api/tests/test_agent_runtime_observability.py apps/api/tests/test_telegram_poller_observability.py apps/api/tests/test_llm_transport_retry.py apps/api/tests/test_system_logs_structured_stats.py` on 2026-09-08 | 17 passed, 1 warning |
| `./scripts/log-triage.sh .local/logs/api.log` on 2026-09-08 | completed; recent/all-time and structured counters visible |
| `./scripts/healthcheck-local.sh` on 2026-09-08 | completed with yellow runtime state: API/Web HTTP probes failed, cloudflared retry/DNS degraded, `im_event_logs` readable |
| `openspec validate local-runtime-log-noise-observability --strict` on 2026-09-08 | valid |
| `python3 -m compileall -q apps/api/app/services/code_agent/control_plane.py apps/api/tests/test_code_agent_policy_layers.py apps/api/tests/test_code_agent_control_plane.py` and `git diff --check` on 2026-09-08 | passed |
| `openspec archive local-runtime-log-noise-observability --yes --json` on 2026-09-08 | archived as `openspec/changes/archive/2026-09-08-local-runtime-log-noise-observability`; specs updated with 7 added requirements |
| `openspec validate --specs --strict` after archive | 21 passed, 0 failed |
