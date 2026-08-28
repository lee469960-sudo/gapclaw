## Why

模型会在探索阶段陷入「describe 空转」：对大量不同视图逐个调用 `describe_*`（如 108 个 view），每个结果因过大被物化到 `mcp_result_N.json` 并在上下文里截断，`view→字段/口径` 映射始终不落地，于是模型反复「读→截断→丢→重读」，迟迟无法进入取数子任务。引擎目前对此**零检测**——`same_sig_run` 只按 `action:args` 精确匹配，108 个不同 view 各算一次、永不聚合计数。

## What Changes

- **Q1 映射蒸馏检测（兜底）**：新增 `tool_call_tally`，按 `mcp:<tool_name>` 键控（从 `MCP:` 行提取真实工具名），工具成功分支计数；达到 4 次起、每 +2 次注入一条「映射蒸馏」软提示；模型输出新 PLAN（含 Replanner 修订）时重置计数。补上 `same_sig_run` 的盲区。
- **Q2 源端结构摘要（根治）**：MCP 结果物化时，若为 JSON，提取顶层 key + 数组元素字段名（≤320 字符）内联进「已写入 mcp_result_N.json」通知，模型无需 READ 大文件即可建映射。非 JSON / 空 payload 返回空串、不加行。
- **Q3 通用 SOP 回写（预防）**：`system_prompt` 的 `mcp_reachable` 分支追加「资源映射」指引——映射必须蒸馏进 PLAN 或落盘、不要逐个 describe 大量资源。中性文案，不点名工具，不违反 D12（SOP 迁出引擎）。
- **Q4 budget-near 强化（兜底）**：`_budget_near_hint` 文案改为提醒「先落盘中间产物再 FINAL，已落盘内容断点续跑会保留」，仍只在 `remaining ∈ {5,2}` 触发，纯文案强化。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `agent-runtime`: 新增 describe 空转检测、源端 JSON 结构摘要、资源映射指引、预算临近落盘保留四条需求。

## Impact

- `apps/api/app/services/agent_runtime/runtime.py`（Q1 `tool_call_tally`/`_distill_hint`、Q4 `_budget_near_hint`）
- `apps/api/app/services/mcp_client.py`（Q2 `_json_keys_summary` + `_materialize` 内联）
- `apps/api/app/services/agent_runtime/system_prompt.py`（Q3「资源映射」条目）
- `apps/api/tests/test_stuck_fallback.py`（`_distill_hint` 单测）、`apps/api/tests/test_mcp_json_summary.py`（Q2 四例）
- 附带一个 Python 3.13 `dict_keys` 不可切片的兼容修复（`_json_keys_summary` 内 `list(...)` 包装）。
