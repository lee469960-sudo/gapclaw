## MODIFIED Requirements

### Requirement: Claude Code runtime 事件必须汇入 CodeAgent 统一事件流

系统 SHALL 将 Claude Code runtime 的关键过程映射为 CodeAgent 统一事件流中的稳定事件。事件至少 SHALL 包含 `runtime_started`、`skill_loaded`、`mcp_loaded`、`tool_call`、`file_changed`、`test_run`、`verifier_failed_retrying`、`verifier_passed` 与 `artifact_sealed`。用户可见事件、阶段代码片段和 transcript SHALL 经过脱敏、UTF-8 清洗与长度限制，不得直接透传未经处理的原始输出；Claude `stream-json` 的 `system/init` 等协议元数据 SHALL 不得作为用户摘要展示。

#### Scenario: 用户查看运行过程

- **WHEN** 使用 Claude Code runtime 的 CodeAgent run 正在执行或已结束
- **THEN** 用户可在现有 CodeAgent 对话/结果界面看到 runtime 启动、Skill/MCP 加载、工具调用、文件变化、测试、Verifier retry 和 artifact 封存状态
- **AND** EDIT/TEST 阶段可查看有限的脱敏代码片段或测试输出
- **AND** 事件内容不暴露秘密值或未授权配置

#### Scenario: runtime 原始输出包含敏感样式内容

- **WHEN** Claude Code 输出或工具日志中包含 secret-like 内容
- **THEN** 事件管道和 transcript 保存路径 SHALL 对用户可见内容执行脱敏
- **AND** patch、source 与 artifact 仍由现有扫描和 Verifier 链路独立检查

#### Scenario: runtime 输出包含协议初始化记录

- **WHEN** Claude `stream-json` 输出 `system/init` 或其他协议生命周期记录
- **THEN** 系统不得将该记录作为对话摘要或最终任务结果展示
- **AND** 用户可见摘要仅保留 assistant 文本、工具结果或明确的运行失败说明
