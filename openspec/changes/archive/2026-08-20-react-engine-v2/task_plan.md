# Task Plan — react-engine-v2

> **唯一正式任务来源 = `tasks.md`**。本文件只把 `tasks.md` 映射为执行顺序与阶段，不新增、不删改、不重新定义任何需求或验收标准。
> 每条任务的验收标准以 `tasks.md` 原文 + `specs/agent-runtime/spec.md` 的 Scenario + `proposal.md` 的 What Changes 为准。

## 执行阶段

| Phase | 标题 | tasks | 依赖 |
|---|---|---|---|
| P1 | 运行时软提示（Q2） | 1.1–1.2 | 无 |
| P2 | 系统提示解耦（Q3） | 2.1 | 无（可与 P1 并行） |
| P3 | 测试与回归（Q4） | 3.1–3.4 | 依赖 P1（3.1–3.3 测 1.1 的行为；3.4 全量回归） |

## 执行顺序与要点

### P1 — 运行时软提示（核心改动）

- **1.1** 在 `runtime.py::_run_modular` 的 `if not tool_steps:` → `else:` 分支（`_rescue_leaked_code` 判定之后、`text_only_streak += 1` 之前）新增 `if plan_steps:` 注入「规划待执行」软提示。
  - 落点参照 design D1（直接复用 `plan_steps`，不新增变量）、D2（放 `else:` 而非顶部）、D3（文案中性化，见 design.md 落地文案）。
  - 复用 `cm.add_coach_hint(...)` 聚合机制，无新计数器、无硬门禁。
- **1.2** 核对 FINAL 分支在 `if not tool_steps:` 之前 `return`/`continue`，确认 PLAN+FINAL 同轮无需额外守卫。**仅核对，不改动。**

### P2 — 系统提示解耦（预防层，独立）

- **2.1** `system_prompt.py`「规划·子任务」条目追加「子任务只写目标、不写工具名 + 正反例」句。参照 design D6。

### P3 — 测试与回归

- **3.1** PLAN-only 一轮后必注入「规划待执行」hint（断言下一轮 LLM 上下文或 ContextManager 缓冲含该提示）。
- **3.2** PLAN+工具同轮不发该 hint。
- **3.3** 边界：PLAN+FINAL 同轮不发。
- **3.4** 全量 `python -m pytest tests/ -q` 回归绿，无新增静态门禁/阈值/计数器。

> 每个 Scenario 对应 `specs/agent-runtime/spec.md` 的一条 WHEN/THEN；3.x 测试直接覆盖这些 Scenario。
