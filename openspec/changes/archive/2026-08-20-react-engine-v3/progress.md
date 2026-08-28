# Progress — react-engine-v3

> 实际完成情况记录。**唯一正式任务来源 = `tasks.md`**；本文件只跟踪状态与证据，不定义任务。
> 状态字典：`pending`（未开始）/ `in_progress`（进行中）/ `done`（完成）。

## 完成协议（强制）

1. 开始一个 task → 本文件标 `in_progress`。
2. **代码完成 + 对应测试通过 + 满足 acceptance criteria（spec Scenario / proposal What Changes）**，三者齐备 → 本文件标 `done`，并在「证据」列记录。
3. **然后**才在 `tasks.md` 勾选该 checkbox。
4. 仅代码修改、测试未跑或验收未满足的 task **不得**标 `done`，**不得**勾选 `tasks.md`。

## Phase 总览

| Phase | 标题 | tasks | 进度 |
|---|---|---|---|
| P1 | 映射蒸馏检测（Q1） | 1.1–1.3 | 3/3 |
| P2 | 源端结构摘要（Q2） | 2.1–2.2 | 2/2 |
| P3 | 通用 SOP 回写（Q3） | 3.1 | 1/1 |
| P4 | 预算临近强化（Q4） | 4.1 | 1/1 |
| P5 | 测试与回归 | 5.1–5.3 | 3/3 |
| **合计** | | **10** | **10/10** |

## 任务明细

### P1 — 映射蒸馏检测

| Task | 描述（tasks.md 为准） | 状态 | 证据（代码/测试/验收） |
|---|---|---|---|
| 1.1 | `tool_call_tally` 按 `mcp:<tool_name>` 键控、成功分支计数 | done | `runtime.py:789` 初始化 `tool_call_tally: dict[str, int]`；`:1042-1047` 成功分支 `re.match(r"MCP:\s*(\S+)")` 提取真实工具名、按 `mcp:<tool_name>` 计数（与参数是否相同无关） |
| 1.2 | 阈值 `>=4, +2` 注入 `_distill_hint` 中性软提示 | done | `runtime.py:1048-1049` `n >= 4 and (n-4) % 2 == 0` → `cm.add_coach_hint(_distill_hint(tool_name, n))`；`:1501-1512` `_distill_hint` 用 `{tool}` 动态回显、模板无硬编码工具名；软提示、无 return/break |
| 1.3 | 新 PLAN / Replanner 修订时 `clear()` 重置 | done | `runtime.py:865` `_apply_plan` 后 `tool_call_tally.clear()`；`:902` Replanner `revised` 时 `clear()` |

### P2 — 源端结构摘要

| Task | 描述 | 状态 | 证据 |
|---|---|---|---|
| 2.1 | `_json_keys_summary`（dict/list/非JSON/空 + py3.13 兼容） | done | `mcp_client.py:787-817`：dict→顶层 key + 前 3 数组字段名；list→`[N 项]`+首元素字段；非 JSON/空 list/空对象→空串；≤320 字符；`list(...)[:max_keys]` py3.13 兼容 |
| 2.2 | `_materialize` 内联摘要（空串不加行） | done | `mcp_client.py:984-990` `_materialize` 内联 `keys_summary`，空串时 `summary_line=""` 不加行 |

### P3 — 通用 SOP 回写

| Task | 描述 | 状态 | 证据 |
|---|---|---|---|
| 3.1 | `mcp_reachable` 追加「资源映射」中性条目 | done | `system_prompt.py:222-224` `mcp_reachable` 分支追加「资源映射」条目，中性、不点名工具（遵守 D12） |

### P4 — 预算临近强化

| Task | 描述 | 状态 | 证据 |
|---|---|---|---|
| 4.1 | `_budget_near_hint` 文案含落盘保留语义（时机不变） | done | `runtime.py:1638-1648` 文案含「落盘中间产物」「已落盘内容断点续跑时保留」；`remaining not in (5,2)` 返回 None（时机不变） |

### P5 — 测试与回归

| Task | 描述 | 状态 | 证据 |
|---|---|---|---|
| 5.1 | `_distill_hint` 单测 | done | `test_stuck_fallback.py:115-119` `test_distill_hint_mentions_tool_and_count`：断言工具名、`4 次`、`映射蒸馏`。通过 |
| 5.2 | `test_mcp_json_summary.py` 四例 | done | dict（含嵌套数组字段）/ list / 非 JSON / 空 list 四例。通过 |
| 5.3 | 全量回归绿、无新增硬门禁 | done | `cd apps/api && python -m pytest tests/ -q` → **134 passed**（较 129 增 5 条新用例）；无新增硬门禁/硬中断（仅 `cm.add_coach_hint` 软提示） |
