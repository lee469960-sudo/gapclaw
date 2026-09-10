# Findings: local-runtime-log-noise-observability

## Initial OpenSpec Context

- Change root: `openspec/changes/local-runtime-log-noise-observability/`.
- Proposal scope: local runtime log noise reduction, structured operational error classification, and diagnostics compatibility.
- Design constraint: preserve existing plain-text log files and system-logs MCP sources; do not introduce an external logging backend.
- New capability: `local-runtime-observability`.
- Modified capabilities: `agent-runtime`, `channels`.
- OpenSpec `tasks.md` is the only formal task source for implementation.

## Baseline Observations From Prior Triage

- `pgrep` is unreliable in the current environment (`sysmond service not found`), so process checks should not rely on it as the only signal.
- `lsof` showed local listeners for cloudflared, API, and Web during triage.
- `cloudflared.log` showed repeated tunnel serve failures/retry loops, so process existence alone is insufficient for tunnel health.
- The external source name is `im_events`, but the actual SQLite table is `im_event_logs`.
- `scripts/log-triage.sh` showed recent-window analysis is more useful than all-time grep counts for current health.
- High-volume `check_status` 304 polling and `no_progress_hint` can dominate `api.log`.

## Code Location Hints

- API access logging config: `apps/api/logging_config.json`.
- `check_status` endpoint behavior: `apps/api/app/routers/agent_chat.py`.
- Agent runtime `no_progress_hint` logging: `apps/api/app/services/agent_runtime/runtime.py`.
- Telegram poller error logging: `apps/api/app/services/channels/telegram_poller.py`.
- LLM retry/error handling: `apps/api/app/services/llm_client.py`.
- system-logs MCP implementation: `apps/api/mcp_servers/system_logs/tools.py`.

## Post-Acceptance Ollama ReAct Finding

- Direct local Ollama calls to `qwen2.5-coder:7b` can produce usable text-protocol ReAct output (`MCP:` / `FINAL:`) when not forced through OpenAI native tool schemas.
- The active model group can be `provider=openai` while its resolved leaf member is `provider=ollama`; request-body decisions must therefore be made at the leaf LLM resource, not at the group label.
- Sending OpenAI-style `tools` to an Ollama leaf caused unstable outputs such as fenced JSON wrappers, malformed/multiple JSON objects, or empty/unexecutable ReAct turns.
- Local Ollama support should prefer the existing text protocol prompt path and omit native `tools` from the HTTP request body.

## Post-Acceptance ReAct Cached-MCP Stop Analysis

- `得到大脑` currently binds LLM group `M3 AND V4`; members are ordered `MiniMax-M3`, `deepseek-v4-pro`, then `qwen2.5-coder:7b`.
- Group dispatch returns on the first successful child response. With the current order, deepseek only participates when MiniMax raises after retries; a 200 response from MiniMax, even if strategically poor, prevents fallback.
- The message `连续 3 次重复请求已缓存的 MCP 结果` is emitted by the ReAct loop after `cached_reference_streak >= 3`; it is a resource-protection stop, not an MCP transport failure.
- MCP dedup keys are `mcp_id + tool + normalized JSON args`. Repeating the same MCP tool and arguments returns a cached-reference string instead of re-calling the MCP.
- Failed run `chat_messages.id=1807` for `得到大脑` found the 2026-08-30 notes and some transcript artifacts, but repeatedly called `get_note_transcript` for the same IDs after prior materialization. The runtime then hit `cached_reference_converged`.
- Successful continuation `chat_messages.id=1816` completed by consuming existing artifacts / list-note content and writing files instead of continuing to insist on the same detail MCP calls.
- MiniMax logs show repeated `finish_reason=length` around these runs; this can increase malformed/empty/unexecutable or repeated-tool rounds. qwen historically also produced empty/unexecutable ReAct turns when driven through native-tool-like shapes.
- `得到大脑.history_length=30`, so a failed assistant message is available to a continuation run. Stronger models may use that history to recover; weaker ReAct planners may repeat the same MCP query anyway.

## Open Issues

- Need to inspect existing test structure before choosing exact test files for access-log filtering, no-progress aggregation, Telegram streaks, and LLM retry classification.
- Need to decide during implementation whether `scripts/log-triage.sh` requires updates after structured field names are finalized.
- Need to confirm if system-logs MCP needs code changes or if existing search/stats remain sufficient after structured log fields are added.

## Errors Encountered

| Error | Attempt | Resolution |
|---|---|---|
| None yet | Planning initialization | N/A |
| `sed apps/api/app/services/agent_runtime/state.py` failed because the state file is actually `loop_state.py` | Phase 2 inspection | Re-ran inspection against `apps/api/app/services/agent_runtime/loop_state.py` |
| `sed apps/api/tests/test_channel_push_intent.py` failed because the test file is not present | Phase 3 inspection | Used `rg` to locate Telegram poller references and added a focused poller observability test file |
| LLM structured log tests initially asserted against unformatted `logger.warning` Mock args | Phase 4 verification | Added `_render_log_calls()` so tests validate the actual formatted log output |
