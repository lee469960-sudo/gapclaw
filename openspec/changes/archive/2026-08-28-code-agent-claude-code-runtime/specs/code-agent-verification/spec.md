## ADDED Requirements

### Requirement: Claude Code 完成必须经现有 Verifier 和 Sealer 裁决

当 CodeAgent 使用 Claude Code runtime 时，Claude Code coding completed SHALL 只表示编码阶段结束。系统 SHALL 在每次 coding completed 后运行现有 CodeAgent Verifier，并仅在 Verifier 通过且现有 Sealer 成功封存 canonical diff、Verifier report、策略和工件哈希后进入 `patch_ready`。

#### Scenario: Claude Code 声称完成

- **WHEN** Claude Code 返回成功退出码或在文本中声明任务完成
- **THEN** 系统不得直接标记 `patch_ready`
- **AND** 必须运行现有 Verifier 与 Sealer 后才能产生成功交付

#### Scenario: Verifier 和 Sealer 通过

- **WHEN** Claude Code coding completed 后现有 Verifier 通过且 Sealer 成功封存工件
- **THEN** 系统进入 `patch_ready`
- **AND** 结果展示包含 Verifier 与 sealed artifact 证据

### Requirement: Verifier 失败必须触发受控 Claude Code 自修复闭环

当 Claude Code coding completed 后 Verifier 失败且 retry 预算未耗尽时，系统 SHALL 将脱敏 Verifier report、失败命令、失败摘要、相关变更文件和策略失败事实反馈给同一 Claude Code session 继续修复。默认最多允许 2 次 verifier retry；达到上限或预算耗尽后 SHALL 进入稳定非成功终态。

#### Scenario: Verifier 失败后继续同一 session

- **WHEN** Verifier 失败且 run 仍有 retry、时间、工具调用和资源预算
- **THEN** 系统将脱敏失败反馈注入同一 Claude Code session
- **AND** Claude Code 在同一 Workspace 与相同冻结策略下继续修复

#### Scenario: Retry 后通过

- **WHEN** Claude Code 在 retry 后完成修复且 Verifier 与 Sealer 均通过
- **THEN** 系统进入 `patch_ready`
- **AND** 审计记录 retry 次数、失败摘要和最终通过事实

#### Scenario: Retry 耗尽

- **WHEN** Verifier retry 达到上限或预算耗尽后仍未通过
- **THEN** 系统进入 `verification_failed`、`budget_exhausted` 或对应稳定非成功终态
- **AND** 未验证 patch 不得被呈现为可直接采用

### Requirement: Verifier feedback 和 runtime evidence 必须脱敏

系统 SHALL 在向 Claude Code 回传 Verifier failure、向用户展示 runtime evidence、保存 transcript 或写入审计前执行脱敏。脱敏不得替代 source、patch 和 artifact 的现有秘密扫描；扫描与 Verifier 仍为最终安全门禁。

#### Scenario: Verifier report 包含敏感样式内容

- **WHEN** Verifier failure feedback 中包含 secret-like 内容、路径敏感信息或未授权配置
- **THEN** 系统只向 Claude Code 和用户展示脱敏版本
- **AND** 保留足够的失败类型、命令和位置摘要用于修复

#### Scenario: Transcript 需要审计

- **WHEN** Claude Code runtime 产生 transcript
- **THEN** 系统保存可审计的脱敏 transcript 或摘要
- **AND** 原始 transcript 如被保留，必须限制在 run-local 短期保留或受控审计范围内
