## ADDED Requirements

### Requirement: 无进展破局复盘提示

当循环连续 N 轮（默认 5）无进展（无工具执行成功、无文件写入、无进度新增、无子任务推进）时，引擎 MUST 注入一条模板化「破局复盘」软提示（非 LLM 生成），内容含「已完成：<进度>；仍缺：<未完成子任务>；请二选一：调用工具推进，或输出 `FINAL: <当前结论>`」。该提示 MUST NOT 终止循环、MUST NOT 作为硬门禁；有推进时无进展计数 MUST 清零。

#### Scenario: 连续无进展注入破局提示

- **WHEN** 连续 5 轮无任何推进
- **THEN** 注入一条模板化破局复盘提示（含已完成 / 仍缺 / 二选一）
- **AND** 不终止循环、不提前收尾

#### Scenario: 有推进清零

- **WHEN** 任务有推进（文件写入 / 进度新增 / 子任务推进 / 工具成功）
- **THEN** 无进展计数清零，不注入破局提示

#### Scenario: 提示为软性非门禁

- **WHEN** 破局提示被注入
- **THEN** 循环仍由 `FINAL` / 用户取消 / LLM 错误 / `max_iters` 决定结束，提示本身不强制停止

### Requirement: 真实会话上下文可用百分比

引擎 MUST 计算「会话上下文可用百分比」= `(1 − 已用输入 token / 模型 max_context_tokens) × 100%`；其中「已用输入 token」MUST 用 `estimate_tokens(系统提示 + 历史消息 + 任务上下文 + 工具结果)` 求和，且在 `fit_messages_to_context` 裁剪前计算；结果 MUST 写入消息 meta 的 `context_available_percent`，替换硬编码 100% 回退。

#### Scenario: 真实可用率计算

- **WHEN** 一次运行结束后
- **THEN** 消息 meta 的 `context_available_percent` 为裁剪前计算出的真实可用率（非硬编码 100%）

#### Scenario: 随占用增长下降

- **WHEN** 系统提示 + 历史 + 任务上下文 + 工具结果的总 token 占用增长
- **THEN** 可用率相应下降，越接近窗口上限越趋向 0%

#### Scenario: 口径一致

- **WHEN** 计算可用率
- **THEN** 分子分母复用既有 `estimate_tokens` 与 `llm.max_context_tokens`，不与其它 token 口径割裂

## REMOVED Requirements

### Requirement: 无进展软预算提前收尾

v14 R1「放弃」机制：连续无进展达到阈值即提前 `_distill_final` 收尾，掐断本可完成的任务。被「无进展破局复盘提示」取代。

### Requirement: 小任务跳过完成度复核

v14 R2：短任务（无子任务 + 无交付物）跳过 `_reflect_final`。改为所有 `FINAL` 一律复核。
