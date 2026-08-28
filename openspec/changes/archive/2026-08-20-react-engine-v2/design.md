## Context

`_run_modular`（`runtime.py`）单循环内，`PLAN` 步骤在解析后即被从 `tool_steps` 剔除（`tool_steps = [s for s in tool_steps if s.action != "plan"]`）。因此一轮只输出 `PLAN` 时，`tool_steps` 为空，控制流落入 `if not tool_steps:` 分支；该分支当前的 rescue/dup/raw-code/text-only 四个判定对「PLAN 正文」都不命中，结果零提示、空转。FINAL 分支在 `if not tool_steps:` 之前 `return`/`continue`，故 PLAN+FINAL 同轮天然到不了该分支。

软提示聚合已就绪：`cm.add_coach_hint(...)` 累积到缓冲，下一轮顶 `flush_coach_hints()` 合并渲染。本改动只复用这条机制，不新增计数器或硬门禁。

## Goals / Non-Goals

**Goals:**
- 在「PLAN-only 且确无任何可执行内容」的轮次注入一条定向软提示，把模型从规划态拉回执行态。
- 提示文本保持引擎中性（不硬编码业务工具名 / 协议前缀），不回退已完成的「SOP 口径迁出引擎」。

**Non-Goals:**
- 不自动派发：不从 PLAN 子任务解析并执行工具（保持「引擎只执行显式协议行」）。
- 不改 `text_only_streak` 的计数语义；不新增阈值/计数器/硬门禁。
- 不改 FINAL 优先语义、不做完成度判定。

## Decisions

### D1 — 复用 `plan_steps`，不新增 `planned_this_round` 变量

`plan_steps`（`[s for s in tool_steps if s.action == "plan"]`）是 loop 局部变量，到 `if not tool_steps:` 处仍在作用域内；且此刻已处于「PLAN 被剔除、无其它工具」的上下文，`plan_steps` 非空 ⟺ 本轮 PLAN-only。直接在 `if not tool_steps:` 内判 `if plan_steps:` 即可。

- 备选：新增 `planned_this_round = bool(plan_steps)`。可行但需**无条件**在 `if plan_steps:` 之前赋值，否则下一轮纯文字时残留 `True` 误发提示——徒增作用域风险，无收益。弃用。

### D2 — 提示放在 `if not tool_steps:` 的 `else:` 分支（救援判定之后）

`if not tool_steps:` 里第一段是 `_rescue_leaked_code` 自动救援。若提示放在字面顶部，当「PLAN + 裸代码」被救援成工具执行时，会一边说「未调用任何工具」一边实际执行——自相矛盾。将 `if plan_steps:` 提示置于 `else:`（确认无任何可执行内容）之后、`text_only_streak += 1` 之前。

- 备选：放 `if not tool_steps:` 顶部。会产生上述矛盾。弃用。

### D3 — 提示文案中性化，不硬编码工具名 / 前缀

提示只写「对第一个 `[ ]` 子任务输出实际的工具调用行（格式见上方工具目录）」，不写 `MCP: list_ads_views {…}`、不写死 `MCP:` 前缀。

- 备选：沿用原稿「如 `MCP: list_ads_views {…}`」。会把 ads 例子写回引擎（回退「SOP 口径迁出引擎」的中性目录边界），且对未绑 MCP、只有 shell 的 agent 是错误指引。弃用。
- 具体文案（落地时以此为准）：
  > 【规划待执行】你刚输出了 PLAN 但本轮没有调用任何工具。PLAN 只记录计划，工具要靠单独一行的协议调用才会真正执行：现在对第一个 `[ ]` 子任务输出实际的工具调用行（格式见上方工具目录），执行后再回来把该子任务标成 `[x]`。不要重复输出 PLAN。

### D4 — 不自动派发（确认 Q1）

PLAN 子任务只作记忆 + 回显，引擎仍只执行显式 `MCP:`/`SHELL:` 行。从 PLAN 里解析工具名并自动执行，会把 LLM 自有的 PLAN 变成命令式脚本，破坏「模型自主决策、引擎只做协议解析 + 执行 + 软提示」的边界。此条作为非目标固化，不改代码。

### D5 — `text_only_streak` 仍递增，让两类提示共现

PLAN-only 轮次继续 `text_only_streak += 1`。第二轮 PLAN-only 会同时触发本提示与现有「连续 N 轮只输出文字」通用提示；聚合层将两条 join 而非覆盖，互补（通用提示说「没推进」、本提示说「去执行第一个子任务」）。

- 备选：PLAN-only 不递增 `text_only_streak`。语义上「PLAN 是有被捕获的」更精确，但属超范围行为改动、增加 diff 与回归面。弃用，保持最小改动。

### D6 — 系统提示「规划·子任务」追加解耦句（预防层）

在 `system_prompt.py` 的「规划·子任务」条目（约 line 210-212）追加一句：子任务文本只写目标、不写工具名（正例 `- [ ] 找到白名单视图`，反例 `- [ ] list_ads_views → 找白名单视图`），工具调用单独用协议行发出。与 D3 形成「预防 + 纠正」两层。

## Risks / Trade-offs

- [提示误发：`plan_steps` 作用域] → 用 D1 直接复用 loop 局部变量，无跨轮残留风险。
- [提示与实际执行矛盾] → D2 将提示置于救援判定之后，仅「确无可执行内容」时发。
- [引擎目录中性化回退] → D3 不写任何具体工具名 / 前缀，回退风险归零。
- [多提示同轮冗余] → D5 依赖聚合层 join，功能不坏；若实测文案重复扰人，可在后续单独调优，不阻塞本改动。
