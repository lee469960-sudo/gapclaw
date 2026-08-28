# Design — react-engine-v3

## Context

动机见 `proposal.md`「Why」。相关现状与约束：

- `same_sig_run`（`runtime.py`）只按 `action:args` 精确匹配，重复调用**同一参数**才触发「无进展检测」；108 个不同 view 各算一次、永不聚合，是 Q1 要补的盲区。
- `_materialize`（`mcp_client.py`）只在 `len(text) > large_result_chars(6000)` 时触发，返回「已全量写入 mcp_result_N.json」通知，模型看不到字段名。
- 已有软提示聚合机制 `cm.add_coach_hint`/`flush_coach_hints` 与「多软提示同轮合并」需求（react-engine-v1），本轮所有新提示都复用它。
- D12（react-engine-v1）：业务口径迁出引擎，引擎目录/提示保持中性、不点名具体工具。

## Goals / Non-Goals

**Goals:**
- 引擎能检测「同一 MCP 工具被大量不同参数调用」的空转，并软性引导蒸馏映射。
- 超大 JSON 结果的字段名可内联获取，模型不必 READ 大文件就能建 `view→字段/口径` 映射。
- 四层互补（检测/根治/预防/兜底），不引入硬门禁。

**Non-Goals:**
- 不改 MCP 源端（外部 MCP 服务器返回什么不是本引擎能控的）——只在引擎侧做摘要。
- 不自动帮模型蒸馏、不自动执行「先 list 再 describe」——纯软提示，行为由模型决定。
- 不新增任何循环中断、强制阈值门禁、或新计数器导致的 return/break。

## Decisions

### D1: 检测键用 `mcp:<tool_name>`，不复用 `action:args`

`tool_call_tally` 以 `mcp:<tool_name>` 为键（`tool_name` 从 `MCP:` 行用 `re.match(r"MCP:\s*(\S+)")` 提取），在工具成功分支计数。
- **为何**：`same_sig_run` 已覆盖「同参数重复」；盲区是「不同参数（不同 view）同工具」无法聚合。按工具名键控正好补盲区。
- **备选（弃）**：放宽 `same_sig_run` 做模糊匹配——会污染它「精确无进展」的语义，且两件事（无进展 vs 映射蒸馏）提示内容不同，分开键控更清晰。

### D2: 阈值 `>= 4 and (n-4) % 2 == 0`

- **为何**：4 容忍少量合理 describe；`+2` 节奏避免每次调用都刷提示。与 `same_sig_run` 的 `>=3, +2` 模式对称。
- **铁律**：只触发 `cm.add_coach_hint(_distill_hint(...))`，无 return/break/中断。

### D3: 「蒸馏」的重置信号 = 新 PLAN

模型输出新 PLAN（`_apply_plan`）或完成度复核后 Replanner 修订计划时 `tool_call_tally.clear()`。
- **为何**：新 PLAN 是「模型已蒸馏」的最弱可用代理。软提示-only 约束下无法硬核验「映射是否真写进 PLAN」。
- **局限**：模型可能重发浅层 PLAN 而不蒸馏，从而重置计数、规避检测——见 Risks。

### D4: 源端摘要只内联、不新增产物

`_json_keys_summary(text, max_keys=24, max_chars=320)` 在 `_materialize` 里内联进通知：
- dict → 顶层 key + 前 3 个数组值字段名；list → `[N 项]` + 首元素字段名；非 JSON / 空 list / 空对象 → 空串（不加行）。
- **为何**：把「字段名」直接给模型，从根上砍掉「读→截断→丢→重读」。只在内联通知加一行，不动物化产物。
- 附带 Python 3.13 兼容：`dict_keys` 不可切片，统一 `list(...)[:max_keys]`。

### D5: 资源映射指引保持中性（遵守 D12）

`system_prompt` 的 `mcp_reachable` 分支追加「资源映射」条目：只讲「映射蒸馏进 PLAN 或落盘、不要逐个 describe 大量资源」，不点名 `describe_ads_view` 等具体工具。
- **为何**：遵守 D12 的中性目录/提示原则，适用于任意 MCP（ads / tushare / …）。

### D6: 预算临近提示只强化文案、不改触发时机

`_budget_near_hint` 仍只在 `remaining in (5, 2)` 触发，文案增加「先落盘中间产物再 FINAL，已落盘内容断点续跑会保留」。
- **为何**：让「耗尽预算前映射已持久化」变成显式指引；时机不变，避免新阈值。

### D7: 铁律口径澄清 ——「软提示-only」，而非「无任何计数器/阈值」

本项目反复引用的「铁律」（无静态硬门禁、无新增阈值/计数器）在措辞上过严：`same_sig_run`、`tool_fail_streak`、`_budget_near_hint(remaining∈{5,2})` 本就都是「计数器+阈值」。**真实不变式**是：新增计数/阈值只能产出软性 coach hint，禁止任何 return/break/循环中断。
- **为何**：Q1 的 `tool_call_tally` + 阈值在字面上违反「无新增阈值/计数器」，但完全符合「软提示-only」。本设计据此把铁律口径定为后者，Q1 视为合规。
- 若后续要规范化，建议把铁律措辞从「无新增阈值/计数器」改为「新增计数/阈值仅可触发软提示，禁止硬门禁」。

## Risks / Trade-offs

- [风险] 同参数重复调用时 `same_sig_run` 与 `tool_call_tally` **同时**递增，模型会既收「无进展检测」又收「映射蒸馏」两条提示（`n=4` 蒸馏、`n=5` 无进展、`n=6` 蒸馏…交错）。
  → 缓解：已有「多软提示同轮合并」需求兜底，两条不互相覆盖、只是略显冗余；属可接受，后续可考虑蒸馏计数跳过 `same_sig_run` 已命中的调用。
- [风险] 重置信号是「PLAN 存在」的代理而非「映射已蒸馏」的验证；模型重发浅层 PLAN 即可规避计数。
  → 缓解：软提示-only 下无法硬核验，接受为局限；目标场景（逐个 describe 大量 view、不重发 PLAN）仍会累积计数并触发。
- [取舍] `_json_keys_summary` dict 分支会把前 3 个顶层 key 各出现两次（一次裸 key、一次 `key[]: 字段`），占 320 字符预算。
  → 缓解：仅美观问题，可后续去重。

## Open Questions

- `_distill_hint` 文案里的 `task/<ts>/view_map.json` 是字面占位符 `<ts>`，而「文件名规范」禁止 `<`/`>`。是否改为传真实 `run_ts` 或去掉具体路径（「落盘到 task/ 下」）——可稍后在不改 specs/任务分解的前提下打磨。
