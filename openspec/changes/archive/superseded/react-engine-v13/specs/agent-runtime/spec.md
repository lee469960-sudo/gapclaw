## ADDED Requirements

### Requirement: 无进展软预算提前收尾

当循环持续无进展（连续多轮无工具执行成功、无文件写入、无进度新增、无子任务推进）达到阈值（默认 5 轮）时，引擎 MUST 提前触发一次 LLM 诚实总结（复用 `_distill_final`，明确标注已完成与缺口）收尾，而非空转到 `max_iters`；有推进（任一推进信号）时 MUST 清零无进展计数并继续执行。此收尾 MUST 由 LLM 生成、非粗暴截断。

#### Scenario: 持续无进展触发软收尾

- **WHEN** 连续多轮无任何推进（无工具成功、无写文件、无进度新增、无子任务推进）且达到阈值
- **THEN** 引擎触发 `_distill_final` 生成诚实总结并收尾，不再空转到 `max_iters`

#### Scenario: 有推进不触发

- **WHEN** 任务仍在推进（有文件写入 / 进度新增 / 子任务推进 / 工具成功）
- **THEN** 无进展计数清零，继续执行，不提前收尾

#### Scenario: 收尾为 LLM 诚实总结

- **WHEN** 触发软收尾
- **THEN** 总结由 LLM 生成，明确标注已完成部分与缺口，非截断 / 硬停

### Requirement: 小任务跳过完成度复核

当 `FINAL` 出现且任务为短任务（无多子任务 PLAN、无交付物：`subtasks` 为空且 `saved_paths`/`files_written` 为空）时，引擎 MUST 跳过 `_reflect_final`，直接接受该 `FINAL` 收尾；有子任务或已产生交付物时 MUST 仍走 `_reflect_final` 复核。

#### Scenario: 短任务直接收尾

- **WHEN** 输出 `FINAL` 且 `subtasks` 为空、无交付物（`saved_paths`/`files_written` 为空）
- **THEN** 跳过 `_reflect_final`，直接接受 `FINAL`

#### Scenario: 长任务仍复核

- **WHEN** 输出 `FINAL` 且有多子任务或已产生交付物
- **THEN** 仍调用 `_reflect_final` 复核

### Requirement: 数据视图目录缓存与注入

引擎 MUST 缓存 MCP `list_ads_views` 的视图名清单（跨会话复用，带失效策略），并把该清单连同蒸馏出的 `view→字段/口径` 映射注入 task_context，使 PLAN 阶段能按语义一步匹配候选视图；模型 MUST 只对命中的 top 候选调用 `describe_ads_view` 确认字段，而非逐个 describe 全部视图。

#### Scenario: 视图名清单缓存

- **WHEN** 首次调用 `list_ads_views` 取得视图名清单
- **THEN** 结果缓存，跨会话复用，不每轮 / 每次运行重复列举

#### Scenario: 注入 task_context

- **WHEN** 构建任务上下文（PLAN 阶段）
- **THEN** 注入视图名清单及已蒸馏的 `view→字段/口径` 映射

#### Scenario: 一步语义匹配

- **WHEN** 具体需求到来
- **THEN** 模型按语义在视图清单中定位候选视图，`describe_ads_view` 只确认 top 候选
- **AND** 不逐个 describe 全部视图
