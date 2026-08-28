## Why

react-engine 经 v1–v4 已补上协议处置、MCP 复用、checkpoint、查询去重、describe-空转检测与工具结果物化。但通读当前单循环（`runtime.py`）+ 测试后仍暴露 8 处缺陷，其中两处高危会在真实长任务里**静默失败**：

1. checkpoint 只在最后一次「≥2 子任务的 PLAN」时写入——预算耗尽 / LLM 连续失败两类「应续跑」的退出路径都不落盘，续跑时所有 PLAN 之后的 `mcp_results` / `query_cache` / `progress_lines` 全部丢失，模型重复查询、进度不可见。
2. 原生 `tool_calls` 被权限阻止或被 FINAL 同轮跳过时没有配对 `role:tool` 结果，下一轮发给 OpenAI/MiniMax/DeepSeek 的消息违反协议（HTTP 400），任务中途断掉。

其余为：动态软提示/进度块被 system 头截断丢弃、完成度复核拒绝循环空转到预算耗尽、复核修复清单被丢弃、`mcp_results` 无界增长、原生结果不裁剪、失效文案与死分支。

## What Changes

- **非 FINAL 退出前持久化最新运行状态**：预算耗尽与 LLM 连续失败两条退出路径在返回前落盘最新 `mcp_results` / `query_cache` / `progress_lines` / `saved_paths`，续跑真正恢复全部已落盘状态（不再只回到最后一次 PLAN 的旧状态）。
- **原生 `tool_calls` 全程配对**：被权限阻止 / 被 FINAL 同轮跳过的原生调用也回填合成 `role:tool` 结果，保证每条 assistant `tool_calls` 都有配对 tool 消息，消除下一轮协议 400。
- **动态软提示与进度块不被截断丢弃**：system 块超限时优先裁静态目录层，`coach_hint` 与 `progress_block` 完整保留。
- **完成度复核连续拒绝后收敛**：连续 FAIL 达到阈值后接受候选或强制停止，不再空转到预算耗尽丢弃正确答案。
- **复核拒绝时回灌修复清单**：把 verifier 产出的 `fix_list` / 修订计划随提示回灌给模型。
- **`mcp_results` 有界**：persist 时对 `mcp_results` 设上限（镜像 `query_cache` 的 LRU）。
- **原生 `role:tool` 结果 in-loop 裁剪**：裁剪覆盖原生 assistant+tool 原子组，不再只对文本 `tool_result` 层生效。
- **失效文案与死分支清理**：失败退出文案匹配真实持久化语义；删除预算耗尽处 `state.final` 死分支。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `agent-runtime`: 新增「非 FINAL 退出前持久化最新状态」「动态软提示/进度块不被截断丢弃」「完成度复核连续拒绝后收敛」「复核拒绝回灌修复清单」四条需求；修改「原生 tool_calls 全程配对」需求（补充被阻止/跳过时也回填合成结果）。

## Impact

- `apps/api/app/services/agent_runtime/runtime.py`（checkpoint 持久化时机、原生孤儿配对、复核收敛与 fix_list 回灌、预算耗尽死分支清理）
- `apps/api/app/services/agent_runtime/context_manager.py`（原生 role:tool 结果 in-loop 裁剪）
- `apps/api/app/services/llm_client.py`（system 头截断保护动态层）
- `apps/api/app/services/mcp_client.py`（`mcp_results` 有界，persist 侧）
- `apps/api/tests/`（新增单测）
