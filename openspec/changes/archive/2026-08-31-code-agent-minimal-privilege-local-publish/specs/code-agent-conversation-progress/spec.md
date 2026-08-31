## MODIFIED Requirements

### Requirement: 对话必须展示 local 发布的受控过程与终态

CodeAgent 对话 SHALL 将 local 发布作为普通 Claude Code 任务展示：使用既有简体中文阶段事件（准备、运行、验证、封存、清理）与最终结果。系统 MUST NOT 增加平台 `publish_discovery`、`publish_dependencies`、preflight 或人工确认阶段。用户可见内容 MUST 脱敏；不得把工具安装成功、命令发现成功或仅 Claude Code 文本声明显示为发布成功。

#### Scenario: 用户确认后执行发布

- **WHEN** 用户在对话中明确请求 local 发布
- **THEN** 对话按普通 Claude Code 任务展示运行过程与验证摘要
- **AND** 不展示秘密值，也不等待平台确认门禁

#### Scenario: 发布未执行或失败

- **WHEN** 认证、依赖安装、仓库路径或验证阶段失败
- **THEN** 对话保留已完成步骤并显示稳定失败原因
- **AND** 不将 Run 显示为发布成功
