# Task Plan — react-engine-v3

> **唯一正式任务来源 = `tasks.md`**。本文件只把 `tasks.md` 映射为执行顺序与阶段，不新增、不删改、不重新定义任何需求或验收标准。
> 每条任务的验收标准以 `tasks.md` 原文 + `specs/agent-runtime/spec.md` 的 Scenario + `proposal.md` 的 What Changes 为准。

## 执行阶段

| Phase | 标题 | tasks | 依赖 |
|---|---|---|---|
| P1 | 映射蒸馏检测（Q1） | 1.1–1.3 | 无 |
| P2 | 源端结构摘要（Q2） | 2.1–2.2 | 无（可与 P1 并行） |
| P3 | 通用 SOP 回写（Q3） | 3.1 | 无 |
| P4 | 预算临近强化（Q4） | 4.1 | 无 |
| P5 | 测试与回归 | 5.1–5.3 | 依赖 P1（5.1 测 1.2 的 `_distill_hint`）、P2（5.2 测 2.1 的 `_json_keys_summary`）；5.3 全量依赖 P1–P4 |

## 执行顺序与要点

### P1 — 映射蒸馏检测（核心改动）

- **1.1** 在 `runtime.py::_run_modular` 的循环前初始化 `tool_call_tally: dict[str, int] = {}`；在工具成功分支（`else:`）内，`action == "mcp_tool_call"` 时用 `re.match(r"MCP:\s*(\S+)")` 提取真实工具名，按 `mcp:<tool_name>` 计数。落点参照 design D1（不复用 `action:args`）、D2（阈值）、D7（铁律口径：软提示-only）。
- **1.2** 阈值 `n >= 4 and (n-4) % 2 == 0` 时 `cm.add_coach_hint(_distill_hint(tool_name, n))`；新增 `_distill_hint(tool, count)` 中性文案（不硬编码工具名）。复用软提示聚合，无 return/break/中断。
- **1.3** `_apply_plan` 处与 FINAL 复核 Replanner 修订处 `tool_call_tally.clear()`（design D3：新 PLAN 即「蒸馏」代理信号）。

### P2 — 源端结构摘要（独立）

- **2.1** `mcp_client.py` 新增 `_json_keys_summary(text, max_keys=24, max_chars=320)`：dict → 顶层 key + 前 3 个数组值字段名；list → `[N 项]` + 首元素字段名；非 JSON / 空 list / 空对象 → 空串。含 Python 3.13 `dict_keys` 不可切片的 `list(...)` 兼容（design D4）。
- **2.2** `_materialize` 在「已写入」通知里内联摘要（`keys_summary` 为空串则不加行）。

### P3 — 通用 SOP 回写（独立）

- **3.1** `system_prompt.py` 的 `mcp_reachable` 分支追加「资源映射」条目；文案中性、不点名工具（遵守 D12 / design D5）。

### P4 — 预算临近强化（独立）

- **4.1** `_budget_near_hint` 文案改为「先落盘中间产物再 FINAL，已落盘内容断点续跑会保留」；触发时机不变 `remaining in (5, 2)`（design D6）。

### P5 — 测试与回归

- **5.1** `test_stuck_fallback.py` 新增 `_distill_hint` 单测：断言提示含工具名与计数、文案中性。
- **5.2** 新增 `test_mcp_json_summary.py`：dict / list / 非 JSON / 空 list 四例。
- **5.3** 全量 `python -m pytest tests/ -q` 回归绿，无新增静态门禁 / 硬中断。

> 每个 Scenario 对应 `specs/agent-runtime/spec.md` 的一条 WHEN/THEN；5.x 测试直接覆盖 Q1/Q2 的场景。
