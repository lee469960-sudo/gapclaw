## MODIFIED Requirements

### Requirement: Claude Code 完成必须经现有 Verifier 和 Sealer 裁决

当 CodeAgent 使用 Claude Code runtime 时，Claude Code 的完成 SHALL 产生普通代码修改、测试和 diff 事实。系统 MAY 基于编辑页资源配置执行常规验证并展示结果，但 MUST NOT 要求 local 发布预检、发布确认、发布 ID、发布证据或发布专用 Sealer 才允许向用户显示任务结果。

#### Scenario: Claude Code 声称完成
- **WHEN** Claude Code 在持久 Sandbox 中完成编辑和测试
- **THEN** 系统展示实际修改、测试输出和验证事实
- **AND** 不因不存在发布证据而进入失败或重试

#### Scenario: Verifier 和 Sealer 通过
- **WHEN** 常规验证通过且存在可展示的 diff 或无变更事实
- **THEN** 系统展示普通验证和变更结果
- **AND** 不要求发布专用封存证据

#### Scenario: 测试失败
- **WHEN** 常规验证命令失败
- **THEN** 系统展示脱敏失败输出并允许 Claude Code 按现有 retry 配置修复
- **AND** 不启动 local 发布流程
