## Why

ReAct 引擎在模型组场景下把「HTTP 200 但空 content / 无可执行 tool_calls」当成硬失败：`extract_chat_response_text` raise → 组内成员全部失败 → 连续 2 次 `llm_failures` 暂停任务，用户看到 `模型组全部失败: LLM 响应缺少 content…`。同时引擎不读 `finish_reason`，上下文满时 `allowed_out` 可压到 256，`finish_reason=length` 的残篇被当完整 FINAL 发出。需要把空响应与输出截断收成**同一条请求级管道**，在铁律不变的前提下保证 FINAL 不切半、空 200 不提前杀死任务。

## What Changes

- **[agent-runtime] 空 HTTP 200 软空回**：无可执行产出时不 raise、不计入 `llm_failures`、不换组内下一个成员；同请求内抬输出预算重试 1 次后仍空则返回空回复，循环走既有 empty-reply coach。
- **[agent-runtime] `finish_reason=length` 请求级续写**：已有文本时最多续写 2 次并拼接；每次重试裁输入保证 `allowed_out ≥ 2048`。
- **[agent-runtime] 截断阻断 FINAL**：2 次续写后仍 length 时返回文本并标记 `output_truncated`；循环不得将该轮作为 FINAL / 完成信号结束。
- **[agent-runtime] 残缺 tool_calls 不执行**：JSON 截断或全部无法映射的 native tool_calls 视同空，禁止执行半截工具。

## Capabilities

### New Capabilities

<!-- none -->

### Modified Capabilities

- `agent-runtime`: 新增空响应软容错、length 续写、截断阻断 FINAL、残缺 tool_calls 拒绝执行；明确「空 200」不属于模型组 failover 失败语义。

## Impact

- `apps/api/app/services/llm_client.py`（`ChatResult.output_truncated`、`fit_messages_to_context(min_allowed_out=…)`、`chat_completion` 请求级管道）
- `apps/api/app/services/agent_runtime/runtime.py`（`output_truncated` 时阻断 FINAL / Verifier）
- `apps/api/tests/`（空 200、length 续写、截断阻 FINAL、组不因空互踢）
- 不改：循环结束四类事件、MiniMax `reasoning_split`、工具结果落盘/clip、Web WS 500 字预览、默认 `max_iterations`
