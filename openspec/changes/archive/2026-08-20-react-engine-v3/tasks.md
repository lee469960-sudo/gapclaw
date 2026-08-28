## 1. 映射蒸馏检测（Q1）

- [x] 1.1 `runtime.py::_run_modular` 新增 `tool_call_tally: dict[str, int]`，按 `mcp:<tool_name>` 键控（`<tool_name>` 从 `MCP:` 行用 `re.match(r"MCP:\s*(\S+)")` 提取），在工具成功分支计数（与参数是否相同无关）
- [x] 1.2 阈值 `n >= 4 and (n-4) % 2 == 0` 时注入 `_distill_hint(tool_name, n)` 软提示（`cm.add_coach_hint`，无 return/break/中断）；新增 `_distill_hint` 中性文案（不硬编码具体工具名）
- [x] 1.3 模型输出新 PLAN（`_apply_plan`）或完成度复核后 Replanner 修订时 `tool_call_tally.clear()` 重置计数

## 2. 源端结构摘要（Q2）

- [x] 2.1 `mcp_client.py` 新增 `_json_keys_summary(text)`：dict → 顶层 key + 前 3 个数组值字段名；list → `[N 项]` + 首元素字段名；非 JSON / 空 list / 空对象 → 空串；≤320 字符；含 Python 3.13 `dict_keys` 不可切片的 `list(...)` 兼容
- [x] 2.2 `_materialize` 把摘要内联进「已写入 mcp_result_N.json」通知（空串时不加行）

## 3. 通用 SOP 回写（Q3）

- [x] 3.1 `system_prompt.py` 的 `mcp_reachable` 分支追加「资源映射」条目：映射必须蒸馏进 PLAN 或落盘、不要逐个 describe 大量资源；文案中性、不点名工具（遵守 D12）

## 4. 预算临近强化（Q4）

- [x] 4.1 `_budget_near_hint` 文案改为「先落盘中间产物再 FINAL，已落盘内容断点续跑会保留」，触发时机不变（`remaining ∈ {5,2}`）

## 5. 测试与回归

- [x] 5.1 新增 `_distill_hint` 单测（`test_stuck_fallback.py`）：断言提示含工具名与计数、中性文案
- [x] 5.2 新增 `test_mcp_json_summary.py`：dict / list / 非 JSON / 空 list 四例
- [x] 5.3 全量测试回归绿，无新增静态门禁 / 硬中断（仅软提示）
