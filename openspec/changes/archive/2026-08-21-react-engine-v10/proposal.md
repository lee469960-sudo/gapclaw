## Why

目标升级为「**用最少的轮次精准完成任务**」，触发信号是 MiniMax **529（2064 集群过载）**。前序 v8 修了 shell/工具动作路由、v9 压住 2013（上下文超窗）；v10 收三个剩余维度（grill 多选）加一个正交项，根因已坐实（见 `docs/exploration/react-engine-v10.md`）：

- **规划前置验证 = 零验证**：`_apply_plan`（`runtime.py:1412`）只解析回显，不检查「计划动作是否在权限内 / 是否可执行」，跑偏/越权计划先白跑一轮再被 FINAL 驳回。
- **结果去重复用 = 只有 MCP 有**：`query_cache`（`loop_state.py:28`）由 `McpSessionManager` 维护，READ/SHELL/SEARCH 重复执行无缓存命中，重读、重跑、重进上下文。
- **验证收敛精度 = Verifier 只看候选文本**：`_reflect_final`（`runtime.py:475`）不读交付物内容，有「误收」风险。
- **529 无退避**：529 是 `httpx.HTTPStatusError`，现有退避 `_post_with_transport_retry`（`llm_client.py:719-752`）只对 `TransportError` 生效；529 直接 `raise LLMHTTPError`，runtime 层 `llm_failures` 阈值 2 无 sleep 连试 2 次即放弃。组内降级（group failover）已在 `chat_completion`（`llm_client.py:634-654`）。

## What Changes

- **R1 规划前置验证**：`_apply_plan` 解析时做零 LLM 成本的本地静态检查（计划动作 ∈ `allowed_actions`、计划可解析），命中软 `coach_hint`，不阻断。
- **R2 工具结果去重复用**：把 `query_cache` 去重从 MCP-only 扩展到 READ/SHELL/SEARCH，命中回显「已缓存，请引用」指针。
- **R3 去重结果内容失效**：READ 记「路径 + mtime/hash」，`file_write` 命中即失效。
- **R4 完成度复核交付物证据**：`_reflect_final` 拼入 `saved_paths` 交付物「前 N 行内容摘要」，零额外轮次，压误收。
- **R5 可重试 HTTP 状态码退避重试**：529/429/5xx（502/503/504）指数退避重试（529/429 3 次、5xx 5 次）；确定性失败不重试；组内降级不改代码、仅文档引导。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `agent-runtime`: 新增「规划前置验证」「工具结果去重复用」「去重结果内容失效」「完成度复核交付物证据」「可重试 HTTP 状态码退避重试」五条需求。

## Impact

- `apps/api/app/services/agent_runtime/runtime.py`（R1 前置检查 + R2/R3 去重/失效 + R4 Verifier 证据）
- `apps/api/app/services/agent_runtime/context_manager.py`（R2 缓存命中指针回显）
- `apps/api/app/services/agent_runtime/system_prompt.py`（R2 去重指针用法说明，可选）
- `apps/api/app/services/agent_tools.py`（R2/R3 READ/SEARCH 结果带去重键标识）
- `apps/api/app/services/llm_client.py`（R5 可重试状态码退避 + 529/429/5xx 文案）
- `apps/api/tests/`（新增单测）
