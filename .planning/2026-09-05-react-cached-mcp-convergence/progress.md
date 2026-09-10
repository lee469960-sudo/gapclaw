# Progress: react-cached-mcp-convergence

## Session Start

- Date: 2026-09-05
- Request: explain how to fix and initialize a plan.
- Constraint: initialize planning first; no runtime code changes in this step.

## Completed

- Created planning directory `.planning/2026-09-05-react-cached-mcp-convergence/`.
- Created `task_plan.md`, `findings.md`, and `progress.md`.
- Captured known failure mechanics from runtime code, logs, and chat message metadata.
- Added regression test for exact `READ:` recovery hint after MCP cached-reference.
- Added regression test ensuring repeated `file_read` cache hits do not trigger the MCP cached-reference hard-stop.
- Implemented cached-reference path extraction and recovery hint injection in `AgentRuntime`.
- Scoped cached-reference hard-stop counting to MCP cached-reference hits.
- Diagnosed single-MiniMax run `chat_messages.id=1851`: many empty previews were executable native tool calls; terminal failure was MiniMax 400 `(2013) tool call result does not follow tool call`.
- Added regression coverage for native `done`/FINAL verifier rejection backfilling `role=tool`.
- Updated LLM step preview for native tool calls to show tool actions instead of the empty/unexecutable placeholder.
- Backfilled native FINAL tool-call result when completion verifier returns an effective FAIL and the loop continues.
- Investigated stop/restart behavior without code changes. Confirmed the observed local Ollama calls came from enabled `log-analyst` scheduled ticks (`8bf6fbf6:8bf6fbf6`), including a `*/5 * * * *` tick, after a prior `stop_chat` request.
- Implemented manual-stop auto-start blocking in `agent_runtime.hub`, `agent_chat`, `tick_scheduler`, and `channels.runtime`.
- Added tests proving manual stop blocks automatic starts until manual clear, and scheduled ticks skip blocked chats.
- Investigated continuing token burn. Found old `log-analyst` scheduled run still executing with 429 retry fanout across MiniMax-M3/M2.7/M2.5.
- Implemented LLM 429 circuit breaker and group fanout abort via `LLMProviderThrottled`.
- Changed 429 retry budget from 3 to 1; retained retry behavior for 529 and transient 5xx.
- Changed runtime to pause immediately on `LLMProviderThrottled`.
- Added scheduled tick already-running guard.
- Emergency mitigation: disabled `agent_ticks.tick_id=f911e61b` and restarted local API/Web with cloudflared disabled.
- Fixed over-strict 429 circuit behavior so model groups continue past throttled paid endpoints to local Ollama fallback instead of aborting the whole group.
- Removed redundant CodeAgent model config persistence from new `effective_policy` snapshots. New Code runs now store model binding only in the frozen `task_contract.model_config`; runtime still supports `effective_policy.model_config` as a compatibility fallback for historical runs.

## Verification Log

| Check | Result |
|---|---|
| Planning files initialized | complete |
| Initial cached-reference recovery test before fix | failed as expected |
| `apps/api/.venv/bin/pytest apps/api/tests/test_react_engine_v16.py::test_cached_mcp_reference_injects_exact_read_recovery_hint apps/api/tests/test_react_engine_v16.py::test_repeated_cached_mcp_reference_hard_stops_before_budget_exhaustion` | 2 passed, 5 warnings |
| `apps/api/.venv/bin/pytest apps/api/tests/test_react_engine_v16.py apps/api/tests/test_mcp_full_materialize.py apps/api/tests/test_resume_and_dedup.py apps/api/tests/test_llm_native_tools.py` | 46 passed, 19 warnings |
| `python3 -m compileall -q apps/api/app/services/agent_runtime/runtime.py apps/api/tests/test_react_engine_v16.py` | passed |
| `git diff --check` | passed |
| `apps/api/.venv/bin/pytest apps/api/tests/test_react_engine_v5.py::test_native_final_rejected_by_reflect_backfills_tool_result` before fix | failed as expected |
| `apps/api/.venv/bin/pytest apps/api/tests/test_react_engine_v5.py::test_native_final_rejected_by_reflect_backfills_tool_result apps/api/tests/test_modular_steps_visible.py::test_native_tool_step_preview_lists_tool_actions apps/api/tests/test_react_engine_v5.py apps/api/tests/test_react_engine_v4.py apps/api/tests/test_react_engine_v16.py apps/api/tests/test_llm_native_tools.py` | 63 passed, 46 warnings |
| `python3 -m compileall -q apps/api/app/services/agent_runtime/runtime.py apps/api/tests/test_react_engine_v5.py apps/api/tests/test_modular_steps_visible.py` | passed |
| `git diff --check` after MiniMax native pairing fix | passed |
| `apps/api/.venv/bin/pytest apps/api/tests/test_tick_scheduler.py` | 4 passed, 1 warning |
| `apps/api/.venv/bin/pytest apps/api/tests/test_code_agent_authorization.py apps/api/tests/test_check_status_etag.py apps/api/tests/test_single_loop_e2e.py` | 16 passed, 25 warnings |
| `apps/api/.venv/bin/pytest apps/api/tests/test_telegram_group_channel.py apps/api/tests/test_react_engine_v6.py apps/api/tests/test_react_engine_v7.py` | 37 passed, 5 warnings |
| `python3 -m compileall -q apps/api/app/services/agent_runtime/hub.py apps/api/app/routers/agent_chat.py apps/api/app/services/tick_scheduler.py apps/api/app/services/channels/runtime.py apps/api/tests/test_tick_scheduler.py` | passed |
| `git diff --check` after manual-stop auto-start block fix | passed |
| `apps/api/.venv/bin/pytest apps/api/tests/test_llm_transport_retry.py apps/api/tests/test_react_engine_v10.py::test_retryable_status_attempts apps/api/tests/test_react_engine_v10.py::test_chat_completion_retries_retryable_http_status apps/api/tests/test_react_engine_v11.py::test_group_throttling_aborts_without_fanout apps/api/tests/test_tick_scheduler.py` | 13 passed, 1 warning |
| `apps/api/.venv/bin/pytest apps/api/tests/test_llm_transport_retry.py apps/api/tests/test_react_engine_v10.py apps/api/tests/test_react_engine_v11.py apps/api/tests/test_tick_scheduler.py apps/api/tests/test_react_engine_v6.py apps/api/tests/test_react_engine_v7.py apps/api/tests/test_react_engine_v16.py apps/api/tests/test_llm_native_tools.py` | 97 passed, 21 warnings |
| `python3 -m compileall -q apps/api/app/services/llm_client.py apps/api/app/services/agent_runtime/runtime.py apps/api/app/services/tick_scheduler.py apps/api/tests/test_llm_transport_retry.py apps/api/tests/test_react_engine_v10.py apps/api/tests/test_react_engine_v11.py apps/api/tests/test_tick_scheduler.py apps/api/tests/test_llm_native_tools.py` | passed |
| `git diff --check` after 429 circuit-breaker fix | passed |
| Post-restart log check at `2026-09-06 14:40:37 CST` | no MiniMax/deepseek/LLM retry logs after `2026-09-06 14:38:16`; high-frequency tick `f911e61b` disabled |
| `apps/api/.venv/bin/pytest apps/api/tests/test_react_engine_v11.py::test_group_throttling_skips_same_endpoint_but_reaches_local_fallback apps/api/tests/test_llm_transport_retry.py apps/api/tests/test_react_engine_v10.py::test_chat_completion_retries_retryable_http_status apps/api/tests/test_llm_native_tools.py` | 23 passed, 1 warning |
| `apps/api/.venv/bin/pytest apps/api/tests/test_llm_transport_retry.py apps/api/tests/test_react_engine_v10.py apps/api/tests/test_react_engine_v11.py apps/api/tests/test_tick_scheduler.py apps/api/tests/test_react_engine_v6.py apps/api/tests/test_react_engine_v7.py apps/api/tests/test_react_engine_v16.py apps/api/tests/test_llm_native_tools.py` after local fallback refinement | 97 passed, 21 warnings |
| `python3 -m compileall -q apps/api/app/services/llm_client.py apps/api/tests/test_react_engine_v11.py` and `git diff --check` after local fallback refinement | passed |
| `apps/api/.venv/bin/pytest apps/api/tests/test_code_agent_policy_layers.py apps/api/tests/test_code_agent_control_plane.py apps/api/tests/test_code_agent_claude_code_runtime.py apps/api/tests/test_code_agent_runtime_context.py` after CodeAgent model config dedupe | 143 passed, 59 warnings |
| `python3 -m compileall -q apps/api/app/services/code_agent/control_plane.py apps/api/tests/test_code_agent_policy_layers.py apps/api/tests/test_code_agent_control_plane.py` and `git diff --check` after CodeAgent model config dedupe | passed |

## Next Action

Run a live `得到大脑` task with MinMax/qwen to confirm runtime behavior outside unit tests.
