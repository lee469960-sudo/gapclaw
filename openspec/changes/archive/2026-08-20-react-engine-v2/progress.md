# Progress — react-engine-v2

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
| P1 | 运行时软提示（Q2） | 1.1–1.2 | 2/2 |
| P2 | 系统提示解耦（Q3） | 2.1 | 1/1 |
| P3 | 测试与回归（Q4） | 3.1–3.4 | 4/4 |
| **合计** | | **7** | **7/7** |

## 任务明细

### P1 — 运行时软提示

| Task | 描述（tasks.md 为准） | 状态 | 证据（代码/测试/验收） |
|---|---|---|---|
| 1.1 | `_run_modular` `else:` 分支注入「规划待执行」软提示 | done | `runtime.py` `if not tool_steps:` 的 `else:` 分支（`_rescue_leaked_code` 判定之后、`text_only_streak += 1` 之前）新增 `if plan_steps:` 注入中性化提示（无工具名/前缀，复用 `add_coach_hint` 聚合、无新计数器/硬门禁）。测试 `test_plan_only_nudge.py` 3 条全绿 |
| 1.2 | 核对 FINAL 分支在 `if not tool_steps:` 之前 return/continue | done | 核对 `runtime.py` FINAL 分支（`final_step is not None`，L867-909）在 `if not tool_steps:`（L913）之前 `return`（L895）/`continue`（L909）；PLAN+FINAL 同轮到不了提示分支，无需额外守卫。无代码改动 |

### P2 — 系统提示解耦

| Task | 描述 | 状态 | 证据 |
|---|---|---|---|
| 2.1 | 「规划·子任务」追加只写目标不写工具名 | done | `system_prompt.py`「规划·子任务」条目追加「子任务文本只写目标、不写工具名（正例 `- [ ] 找到白名单视图`，反例 `- [ ] list_ads_views → 找白名单视图`），工具调用要单独用协议行发出」 |

### P3 — 测试与回归

| Task | 描述 | 状态 | 证据 |
|---|---|---|---|
| 3.1 | 单测：PLAN-only 注入 hint | done | `test_plan_only_nudge.py::test_plan_only_injects_nudge_hint`：PLAN-only 一轮后下一轮 LLM 上下文含「规划待执行」。通过 |
| 3.2 | 单测：PLAN+工具同轮不发 hint | done | `test_plan_only_nudge.py::test_plan_plus_tool_no_nudge_hint`：PLAN+SHELL 同轮，SHELL 执行（`exec_mock.assert_awaited`）且不发 hint。通过 |
| 3.3 | 边界：PLAN+FINAL 同轮不发 hint | done | `test_plan_only_nudge.py::test_plan_plus_final_no_nudge_hint`：PLAN+FINAL 同轮，FINAL return、`chat_mock.await_count == 1`、不发 hint。通过。注：原 spec「PLAN+被救援裸代码」场景不可达已移除，见 findings F1 |
| 3.4 | 全量测试回归绿 | done | `cd apps/api && python -m pytest tests/ -q` → **129 passed**（较 126 增 3 条新用例）；无新增静态门禁/阈值/计数器 |
