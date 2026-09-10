# Findings: react-cached-mcp-convergence

## Known Failure

- User-facing failure message:
  `任务未完成，已自动结束（连续 3 次重复请求已缓存的 MCP 结果，自动停止避免资源浪费）。`
- Runtime log marker:
  `modular_loop cached_reference_converged agent=73040749 ... streak=3`
- Affected Agent:
  `73040749 | 得到大脑`
- Current LLM binding:
  `36bee1c3 | M3 AND V4`
- Current group members:
  `MiniMax-M3 -> deepseek-v4-pro -> qwen2.5-coder:7b`

## Observed Mechanics

- Model group dispatch returns the first successful child response.
- Because MiniMax is first, deepseek only participates when MiniMax raises after retries; a low-quality MiniMax 200 response blocks fallback.
- MCP dedup key is `mcp_id + tool_name + normalized JSON(args)`.
- A repeated MCP call returns a cached-reference string:
  `该结果已缓存/已落盘 <path>（...）。请 READ 取回该文件，勿重跑；或用 SHELL 继续处理。`
- Runtime increments `cached_reference_streak` for cached MCP hits and hard-stops at 3.

## Failed Run Evidence

- Failed chat message: `chat_messages.id=1807`.
- User request: `请梳理一下 2026年08月30日的 录音笔记`.
- The run successfully found 2026-08-30 notes and two recorder_audio note IDs.
- The model then repeatedly requested `get_note_transcript` for already materialized IDs.
- It did not switch to READ/SHELL to consume the cached files.

## Successful Continuation Evidence

- Continuation chat message: `chat_messages.id=1816`.
- The model used existing artifacts/list-note content and wrote final files.
- This indicates the data path can succeed; the failure is ReAct convergence/recovery, not MCP availability.

## Provider-Specific Notes

- MiniMax shows repeated `finish_reason=length`, which can increase truncated or repeated actions.
- qwen local Ollama previously produced empty/unexecutable ReAct outputs when forced through native-tool-like shapes.
- Local Ollama support should stay on text protocol (`MCP:` / `SHELL:` / `FINAL:`) and avoid native OpenAI `tools`.

## Open Questions

- Should repeated cached MCP count be global, per cached path, or per exact MCP signature? Current implementation keeps the existing streak model but scopes it to MCP cached-reference hits only.
- Should runtime auto-read cached paths, or only provide stronger hints? Current implementation uses stronger hints first; auto-read remains a future option if MinMax/qwen still ignore exact `READ:` lines.
- Should model groups fallback when a child returns repeated unusable ReAct output rather than only on exceptions?

## 2026-09-10 Completion-Liveness Review

- A later failure mode is independent of MCP cache recovery: `_run_modular` rejects every `FINAL` before calling `_reflect_final` whenever any `state.subtasks` entry remains `pending` (`final_blocked_open_subtasks`). The injected hint mandates another tool call, even when the pending text is an already-resolved semantic question such as whether `ReplacingMergeTree` requires `FINAL`.
- This early hard gate precedes the existing bounded verifier convergence (`_REFLECT_FAIL_CONVERGE`), so repeated equivalent reasoning can consume the full iteration budget without ever reaching that convergence path.
- The completion contract is currently conflated with LLM-owned `PLAN` state: a non-executable or redundant pending subtask blocks delivery as strongly as an unmet user requirement. A fix needs a product decision about which source of tasks is authoritative and when an honest, qualified final answer is allowed.

## 2026-09-10 Confirmed Completion Contract

- A conditional, evidence-backed conclusion is a valid deliverable for read-only/analysis work. The final answer must state conditions, evidence, and residual risk; it must not loop solely because a model wants another semantic review.
- Only two sources can block delivery: (A) an unmet, verifiable requirement from the original user goal, and (B) a concrete verifier evidence gap. LLM-authored `PLAN` subtasks are advisory and cannot independently veto `FINAL`.
- A verifier gap is executable only when it contains all four fields: the user requirement being checked, evidence not yet held, one concrete *unexecuted* tool action, and that action's expected decision criterion.
- The verifier must produce structured gap cards. Runtime validates all fields and deduplicates canonical `tool + normalized args`; invalid, already-satisfied, already-executed, or cache-materialized gaps are non-blocking notes.
- Per accepted gap, allow at most two evidence-gaining attempts with different canonical tool signatures. If neither resolves it, end read-only/analysis work with a qualified final; do not repeat reasoning or actions.
- Risk is enforced by runtime, not model self-classification. Read-only/analysis work may use qualified finalization. Write/delete/send/deploy/payment work requires execution evidence or explicit user confirmation; when unresolved, present evidence and ask for confirmation/authorization.
- Shell commands are statically classified by command: read/query commands can remain low risk; write/delete/network egress/deployment commands are high risk; ambiguous commands default high risk.

## Implemented Findings

- Prose-only cached-reference messages were insufficient for weaker ReAct models. They need an exact protocol line.
- Injecting `READ: <path>` as a coach hint is lower risk than disabling dedup or auto-executing READ.
- Existing code counted any cache hit toward `cached_reference_streak`; this made READ/SHELL cache hits eligible for an MCP-specific hard-stop message. The implemented fix now counts only MCP cached-reference hits.
- Single-MiniMax runs can show `模型返回空正文/不可执行工具调用` even when MinMax emitted executable native `tool_calls`; the UI preview used assistant content only, and native tool-call content is often intentionally empty.
- MiniMax 400 `tool call result does not follow tool call (2013)` can occur when a native `done`/`final` tool call is rejected by the completion verifier and the runtime continues without backfilling a matching `role=tool` result for that `tool_call_id`.
- The repair is to keep native pairing complete on the verifier-FAIL continuation path, and to preview native tool calls by action names instead of labeling them empty/unexecutable.
- A user `stop_chat` request only flips the in-memory `_running[agent_id:session_id]` flag to `False`; it is not a durable pause/disable signal for future scheduled/channel runs.
- The observed "stop does not really stop" case was a scheduled `agent_tick` restart for `log-analyst` (`8bf6fbf6:8bf6fbf6`), not `得到大脑` continuing: `agent_ticks` has enabled `*/5 * * * *` and `0 9 * * *` entries for `8bf6fbf6`.
- Logs show the sequence: `2026-09-06 11:41:09` `POST stop_chat` returned 200, then `2026-09-06 11:45:00` APScheduler ran `_run_tick` and started a fresh `agent_run modular agent=8bf6fbf6 session=8bf6fbf6 max_iters=150`.
- Because `log-analyst` is bound to group `36bee1c3` whose members include `qwen2.5-coder:7b`, the restarted scheduled run kept reaching local Ollama and logging repeated `LLM empty/unexecutable response; retry once`.
- `stop_chat` currently returns success without proving that a live key was actually stopped, and `AgentRuntime.run` unconditionally sets `_running[key] = True` at run start. This leaves two risks: wrong-key/no-op stops are hard to see, and any later scheduler/channel trigger can restart immediately.
- Implemented stop/restart containment as an in-memory auto-start block: manual `stop_chat` blocks future automatic tick/channel starts for the same `agent_id:session_id`; manual `submit_chat` clears the block. This preserves configured ticks and lets the user explicitly resume.
- Token-burn root cause after further investigation: a scheduled `log-analyst` run kept looping under old code. Each ReAct iteration called the model group; MiniMax-M3, MiniMax-M2.7, and MiniMax-M2.5 each retried 429 three times, so one failed iteration could emit 9 MiniMax HTTP requests. Runtime then allowed another LLM-failure iteration, and qwen empty/unexecutable responses kept the loop alive.
- Current `36bee1c3` model group no longer contains `deepseek-v4-pro`; earlier deepseek spend likely came from the prior group configuration when deepseek was still in the fallback chain. The same fanout pattern would have allowed MiniMax failure to cascade into deepseek spend.
- Added an LLM throttle circuit: first 429 opens a 5-minute endpoint circuit, aborts model-group fanout, and surfaces `LLMProviderThrottled`.
- Runtime now treats `LLMProviderThrottled` as threshold=1, pausing the task immediately instead of attempting a second ReAct iteration.
- Scheduled tick startup now also checks `is_running(agent_id, session_id)` before calling `run_agent`, preventing the scheduler from interrupting/restarting an already-running chat key.
- Emergency mitigation applied: disabled high-frequency `log-analyst` tick `f911e61b` (`*/5 * * * *`) in `apps/api/data/gap.db`, then restarted local API/Web with `ENABLE_CLOUDFLARED=0 ./scripts/start-local.sh`. Post-restart logs showed no MiniMax/deepseek LLM calls after `2026-09-06 14:38:16`.
- Follow-up: the initial throttle circuit aborted the entire model group on `LLMProviderThrottled`, so qwen local fallback was not reached even though group `36bee1c3` includes `7a179f79 | qwen2.5-coder:7b` at the end. Revised behavior: a 429 opens/skips the throttled endpoint, but the group continues to later members with different endpoints/providers, including local Ollama.
- qwen empty/unexecutable follow-up: logs show qwen is reached (`POST http://127.0.0.1:11434/v1/chat/completions 200 OK`), but the Ollama response is produceless for the ReAct parser: no content and no executable tool call. The runtime labels this as `模型返回空正文/不可执行工具调用`. This is different from "not falling back"; fallback works, but the local model's response format/content is unusable for the current ReAct turn.
- CodeAgent MiniMax follow-up: `dbt-test` (`8c862281`) is bound to LLM resource `e554b0c2 | Claude-MiniMax-M3 | provider=anthropic | model=MiniMax-M3 | base_url=https://api.minimaxi.com/anthropic`. This is the correct Claude/Anthropic-compatible binding for the Claude Code runtime; the OpenAI-compatible `MinMax`/`MiniMax-M2.x` resources remain separate standard LLM resources.
- CodeAgent did not create extra `.claude/settings.json` model config under `apps/api/data/code-agent`; the duplicated config was JSON stored in each new Code run: both `task_contract.model_config` and `effective_policy.model_config`.
- Historical Code runs for `dbt-test` include frozen `model_config` values such as `model_ref=sonnet` in both `task_contract` and `effective_policy`. Those are immutable audit snapshots and should not be edited in place. New runs use the current DB LLM model (`MiniMax-M3`).
- “Wrong model config could still reply” is explained by weak validation and provider compatibility: Claude Code preflight checks only that `provider=cloud_claude` has a non-empty `model_ref`; it does not prove the remote endpoint accepts that exact model string before execution. MiniMax's Anthropic-compatible endpoint may also accept/alias/ignore older model names such as `sonnet`, so a stale frozen model string did not necessarily prevent replies.
