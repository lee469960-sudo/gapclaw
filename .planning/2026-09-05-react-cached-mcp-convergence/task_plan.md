# Task Plan: react-cached-mcp-convergence

## Goal

修复 ReAct 模型在 MCP 结果已缓存/已落盘后仍重复调用同一 MCP tool+args，最终触发“连续 3 次重复请求已缓存的 MCP 结果”自动停止的问题。

## Scope

- 重点支持 `得到大脑` Agent 下的 MinMax、qwen 本地 Ollama、以及包含 deepseek 的模型组。
- 保留 MCP dedup 的资源保护价值：不能简单关闭缓存或取消硬停。
- 优先让弱 ReAct 模型明确消费已落盘文件，或在已有足够材料时生成阶段性/最终产物。
- 不改变 MCP 后端协议、不修改得到 MCP 服务。

## Non-goals

- 不重新实现 MCP client。
- 不移除 query_cache / materialization。
- 不把 deepseek 固定为唯一模型。
- 不在没有测试的情况下扩大 native tools 路径。

## Current Phase

Phase 5: Regression suite complete

## Phases

### Phase 0: Planning initialization

- Status: complete
- Output: task_plan.md, findings.md, progress.md

### Phase 1: Reproduce and pin failure mechanics

- Status: complete
- Inspect runtime tests around cached MCP hard-stop.
- Extract a minimal failing sequence from `chat_messages.id=1807`.
- Identify whether failure is caused by prompt, parser, native tools, MCP dedup response, or context hygiene.
- Verification: added a focused characterization test for cached MCP recovery.

### Phase 2: Fix cached-reference recovery path

- Status: complete
- Preferred fix: when MCP returns cached reference, inject stronger next-step guidance with exact READ/SHELL action and cached path.
- Consider whether hard-stop threshold should require repeated same cached path/tool, not any cached MCP hit.
- Consider whether cached MCP reference should be pushed as a tool result plus structured progress/checklist item.
- Verification: cached-reference recovery test now sees exact `READ: task/...` hint and completes before hard-stop.

### Phase 3: Fix provider/model routing risk

- Status: complete
- Confirm current Ollama leaf behavior no longer receives OpenAI native `tools`.
- Confirm group fallback semantics: first successful model wins; deepseek only helps when earlier members fail.
- Decide whether model groups need policy knobs for “fallback on unusable ReAct output” versus only exception fallback.
- Verification: existing LLM native tools tests confirm Ollama leaf models still omit native `tools`.

### Phase 4: Strengthen prompt/system hints for local Ollama and MinMax

- Status: complete
- Make cached-result instructions explicit in system/tool catalog: after cached reference, do not repeat MCP; use READ/SHELL path.
- Keep wording short to avoid prompt bloat.
- Verification: runtime test validates exact executable READ recovery hint in next LLM context.

### Phase 5: Regression suite

- Status: complete
- Run focused ReAct runtime tests.
- Run LLM native tools tests.
- Run relevant MCP materialization/dedup tests.
- Run `git diff --check`.

## Implemented Fix

- Runtime now parses the cached-reference path from MCP dedup responses.
- After an MCP cached-reference hit, runtime injects a high-priority coach hint with one exact executable recovery action:
  - `READ: <cached-path>` when `file_read` is allowed.
  - `SHELL: cat <cached-path>` when only shell is allowed.
  - `FINAL: <...>` fallback when neither file read nor shell is allowed.
- The hard-stop remains active when the model ignores recovery hints and continues repeating the same cached MCP call.
- Cached-reference hard-stop counting is now scoped to MCP cached-reference hits, so ordinary READ/SHELL cache hits do not incorrectly report “重复 MCP”.

## Candidate Fix Options

### Option A: Prompt-only hardening

Add clearer instructions to the system prompt/tools catalog. Lowest risk, but weakest for qwen/MinMax because they already ignored similar cached-reference text.

### Option B: Runtime cached-reference recovery hint

When `_is_cached_reference(result_text)` is true, parse the cached path and inject a high-priority coach hint with an exact next action:

```text
READ: task/.../mcp_result_N.json
```

This is likely the best first fix: surgical, preserves dedup, directly addresses the observed failure.

### Option C: Auto-convert repeated cached MCP into READ

After first cached-reference hit, runtime could automatically enqueue a READ of the cached path instead of waiting for the model. Stronger and more deterministic, but changes control flow more.

### Option D: Fallback on unusable/repeated ReAct output

For model groups, treat repeated unproductive 200 responses as a model-level failure and try next group member. This would explain why deepseek helps, but it is a broader semantic change and needs careful tests.

## Recommended Initial Implementation

Start with Option B, then evaluate whether Option C or D is needed.

Reasoning:

- The current hard-stop is useful and should stay.
- The immediate model error is failure to consume a known cached path.
- A precise `READ:` hint is easier for qwen/MinMax than a prose instruction.
- It does not change MCP result correctness or provider routing.

## Success Criteria

- A repeated cached MCP call no longer reaches hard-stop if the cached path is available and the model can be nudged to READ/SHELL.
- Existing hard-stop still triggers when the model ignores recovery hints three times.
- OpenAI/MiniMax native tools behavior remains covered.
- Ollama leaf models still omit native `tools`.
- No change to unrelated OpenSpec completed tasks.
