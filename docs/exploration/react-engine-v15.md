# react-engine-v15 需求分析

> 状态：需求分析（grill 已收敛）。仅记录需求，未改任何业务代码、未落 openspec。
> 上一轮：react-engine-v14（`openspec/changes/react-engine-v14/`，尚未 apply）。
> 本轮定位：对 v14 的 R1/R2 做**修正**，并新增「真实会话上下文百分比」。整体原则——**以完成任务为第一目标**。

## 一、背景与根因

### 问题一：短任务跳过复核 → TG 显示「任务失败」

- 现象：短任务走 v14 R2「跳过完成度复核」后，TG 端显示「任务失败」。
- 根因：「任务失败」不是代码字符串（全仓搜无），是**模型自己输出的 FINAL 答案**（模型在短任务里直接放弃/答错）。`runtime.py:993-1015` 的 R2 跳过条件是「无子任务 + 无交付物」，但 `_reflect_final`（`runtime.py:481-552`）校验的是**答案是否满足目标实质要求**（数字/口径/结论对不对），不是只看有没有交付文件。问答型短任务照样可能输出错误/放弃的答案，跳过复核 → 坏答案被直接接受 → 原样发到 TG。
- 本质：R2 用「无交付物」这个**错误信号**跳过了「答案正确性」校验，牺牲正确性换 +1 轮效率。

### 问题二：连续无进展 → 触发诚实总结收尾

- 现象：任务没完成就提前用「诚实总结」收尾，用户期望尽量完成任务。
- 根因（触发链，已定位到代码）：
  - `made_progress` 每轮重置 False（`runtime.py:869`），只有两处置 True——子任务推进（959）、工具执行成功且非缓存命中且非失败结果（1220）。
  - 连续 5 轮 False（`_NO_PROGRESS_BUDGET=5`，行 57）→ `_soft_budget_check` 调 `_distill_final` **放弃**收尾（行 634-667）。
  - 因此「纯文本轮 / PLAN-only 轮 / 工具失败或空结果 / 缓存命中 / 复核拒绝后的 replan 轮」**全都不算进展**。真实任务很容易中招：模型连续 describe 各视图时某次命中缓存、或陷入 replan、或纯文本推演 5 轮，就被「诚实总结」掐断。
- 本质：R1 是**「放弃」机制**，不是「破局」机制，与「完成任务优先」正面冲突。

### 问题三：会话上下文百分比不真实

- 现象：UI 显示「会话上下文可用 X%」，用户质疑其真实性。
- 根因：后端**从不写** `context_available_percent` / `progress_percent` / `context_continuity`（全仓 `.py` 搜不到，只有前端读）。前端读不到就回退：`会话上下文可用 100%`（硬编码默认，`AgentChat.vue:556`）、`任务进度` = `5 + 完成步骤数/总步骤×10`（封顶 18%，是基于「步骤完成数」的启发式）。
- 结论：**不真实**。但后端已有可复用的真实口径：`estimate_tokens`（≈2 字符/token，`llm_client.py:307`）、`fit_messages_to_context`（按 `llm.max_context_tokens` 裁剪，默认 128000，`llm_client.py:348-450`）。

## 二、决策汇总（grill 结论，6 项）

| # | 决策 | 内容 |
|---|---|---|
| Q1 | 撤销 R2 | 所有 `FINAL` 一律过 `_reflect_final`，不复核即接受候选的行为移除 |
| Q2 | 撤销 R1 终止 | 回到 `max_iters` 唯一硬预算；不提前收尾 |
| Q3 | 真实上下文百分比 | 后端按真实 token 计算并下发 |
| Q4 | 交付形态 | 先出需求文档（`docs/exploration/`），暂不落 openspec |
| Q5 | 破局提示形态 | 模板化「破局复盘」提示（非 LLM、便宜、覆盖所有无进展场景） |
| Q6 | 上下文口径 | 可用率 = `(1 − 已用输入 token / max_context_tokens) × 100%`，裁剪前算 |

## 三、相对 v14 的变更

| v14 内容 | 本轮处置 |
|---|---|
| agent-config：绑定 LLM 组（来自 v12） | **保留**，无改动 |
| agent-runtime R1：无进展软预算（终止） | **撤销终止动作**，改为「破局复盘」软提示（见 R1′） |
| agent-runtime R2：短任务跳过复核 | **撤销**，全程复核（见 R2′） |
| agent-runtime R3：数据视图目录缓存与注入 | **保留**，无改动 |
| （新增）会话上下文百分比 | **新增**（见 R4） |

## 四、需求（Requirements）

> 命名沿用 openspec 风格（MUST / WHEN / THEN），后续可直接落 spec。

### R1′ — 无进展破局复盘提示（替换 R1 的终止）

当循环连续 N 轮（默认 5）无进展（无工具执行成功、无文件写入、无进度新增、无子任务推进）时，引擎 MUST 注入一条**模板化**「破局复盘」软提示（非 LLM 生成），内容含「已完成：<进度行>；仍缺：<未 done 子任务>；请二选一：调用工具推进，或输出 `FINAL: <当前结论>`」。该提示 MUST NOT 终止循环、MUST NOT 作为硬门禁；有推进时无进展计数 MUST 清零。触发为软性、与既有 text_only/stuck/repeat/distill 软提示同级。

**Scenario: 连续无进展注入破局提示**

- **WHEN** 连续 5 轮无任何推进
- **THEN** 注入一条模板化破局复盘提示（含已完成 / 仍缺 / 二选一）
- **AND** 不终止循环、不提前收尾

**Scenario: 有推进清零**

- **WHEN** 任务有推进（文件写入 / 进度新增 / 子任务推进 / 工具成功）
- **THEN** 无进展计数清零，不注入破局提示

**Scenario: 提示为软性非门禁**

- **WHEN** 破局提示被注入
- **THEN** 循环仍由 `FINAL` / 用户取消 / LLM 错误 / `max_iters` 决定结束，提示本身不强制停止

### R2′ — 完成度复核全程保留（撤销 R2）

对任意 `FINAL`（无论有无子任务、有无交付物），引擎 MUST 调用 `_reflect_final` 完成度复核后才接受候选；「短任务跳过复核、直接接受 FINAL」的行为 MUST 移除。复核拒绝时 MUST 仍按既有逻辑回灌修复清单 / 修订 PLAN 并 replan，连续拒绝仍按既有收敛（`_REFLECT_FAIL_CONVERGE=3`）。

**Scenario: 短任务也复核**

- **WHEN** 短任务（无子任务、无交付物）输出 `FINAL`
- **THEN** 仍调用 `_reflect_final` 复核，不直接接受

**Scenario: 复核拒绝回灌**

- **WHEN** 复核返回 FAIL 且产出修复清单 / 修订 PLAN
- **THEN** 回灌修复清单并 replan，而非直接收尾

### R3 — 数据视图目录缓存与注入（保留，无改动）

沿用 v14 R3 原文：缓存 MCP `list_ads_views` 视图名清单（跨会话复用，带失效策略），连同蒸馏出的 `view→字段/口径` 映射注入 task_context，使 PLAN 阶段能按语义一步匹配候选视图；模型只对命中的 top 候选调用 `describe_ads_view` 确认字段，而非逐个 describe 全部视图。

### R4 — 真实会话上下文可用百分比

引擎 MUST 计算「会话上下文可用百分比」= `(1 − 已用输入 token / 模型 max_context_tokens) × 100%`；其中「已用输入 token」MUST 用 `estimate_tokens(系统提示 + 历史消息 + 任务上下文 + 工具结果)` 求和，且在 `fit_messages_to_context` **裁剪前**计算；结果 MUST 写入消息 meta 的 `context_available_percent`（前端 `AgentChat.vue` 已读该字段），替换当前硬编码 100% 回退。

**Scenario: 真实可用率计算**

- **WHEN** 一次运行结束后
- **THEN** 消息 meta 的 `context_available_percent` 为裁剪前计算出的真实可用率（非硬编码 100%）

**Scenario: 随占用增长下降**

- **WHEN** 系统提示 + 历史 + 任务上下文 + 工具结果的总 token 占用增长
- **THEN** 可用率相应下降，越接近窗口上限越趋向 0%

**Scenario: 口径一致**

- **WHEN** 计算可用率
- **THEN** 分子分母复用既有 `estimate_tokens` 与 `llm.max_context_tokens`，不与其它 token 口径割裂

## 五、铁律

- **无静态硬门禁**：唯一硬预算为 `max_iters`（默认 50，可经 Agent `max_iterations` 配置）。R1′ 的「连续 5 轮」是**软提示触发点**，不是门禁——与既有 `text_only_streak>=2`、`same_sig_run>=3`、`tool_call_tally>=4` 同级，不终止循环。
- 所有纠正走软 LLM 判断 / 模板提示，循环仍只由 `FINAL` / 用户取消 / LLM 错误 / `max_iters` 结束。

## 六、边界（Non-goals）

- 不新增任何「仍在推进就截断」的硬停。
- 不改 MCP / Skill 内容（视图目录机制在引擎侧）。
- 不改 group failover / 环检测 / 退避重试（v10/v11 已定）。
- 不触及 Executor / Checkpoint-Recovery / Retry / Concurrency / Observability（百分比之外）/ Benchmark 各维。

## 七、下一步

按 Q4=C，本轮只出需求文档。待确认后：
1. 落 `openspec/changes/react-engine-v15/`（或在 v14 原地修订，二选一需再确认）。
2. 涉及文件（预估，供参考）：
   - `runtime.py`：删除 `_soft_budget_check` 终止 + `no_progress_streak` 门禁；改注入 R1′ 模板提示；删除短任务跳过分支（恢复全程 `_reflect_final`）；计算并写入 `context_available_percent`。
   - `loop_state.py`：移除或降级 `no_progress_streak`（仅作提示触发计数）。
   - `llm_client.py` / `context_manager.py`：复用 `estimate_tokens` 求和，产出可用率。
   - `AgentChat.vue`：确认读取真实 `context_available_percent`（前端已具备，无需大改）。
   - 测试：R1′ 提示注入单测、R2′ 短任务仍复核单测、R4 可用率口径单测。
