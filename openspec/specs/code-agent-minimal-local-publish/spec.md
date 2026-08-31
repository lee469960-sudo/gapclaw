# code-agent-minimal-local-publish Specification

## Purpose

让 CodeAgent 在不要求 Manifest 或平台发布档案的前提下，从受管仓库发现并执行 local 发布，同时保留可审计的持久 Sandbox 边界和 Verifier 结论。

## Requirements

### Requirement: Claude Code local 发布必须以仓库为唯一配置来源

系统 SHALL 在用户明确请求 local 发布时直接启动当前 CodeAgent Workspace 的 Claude Code Runtime。Claude Code 使用仓库内的 CI 配置、发布脚本和锁定文件理解任务；系统 MUST NOT 要求 Manifest、项目档案、环境变量命令注册表或发布专用 UI 输入命令、网络、凭据、验证计划或锁。

#### Scenario: local 发布直接进入 Claude Code

- **WHEN** 用户明确请求 local 发布且 Workspace 已准备
- **THEN** 系统直接启动 Claude Code Runtime，不进行平台入口预扫描
- **AND** 对话显示 Claude Code 的实际过程、执行结果和验证结论

#### Scenario: Claude Code 无法确定发布路径

- **WHEN** Claude Code 阅读仓库后仍无法确定发布路径
- **THEN** Claude Code 在对话中报告缺失事实或请求澄清
- **AND** 平台不得以文件名规则返回 `local_publish_entry_not_found`

### Requirement: local 发布必须使用现有仓库认证上下文

系统 SHALL 只使用当前 run 已获授权的仓库/运行时认证上下文；系统 MUST NOT 要求用户新增发布档案、单独部署凭据或在对话、Manifest、事件和结果中暴露秘密值。

#### Scenario: 发布脚本使用已有认证

- **WHEN** 仓库发布脚本可通过当前 Sandbox 的已授权运行时认证完成请求
- **THEN** 系统允许该脚本在绑定 Sandbox 中运行
- **AND** 事件和结果对输出执行秘密脱敏

#### Scenario: 发布脚本缺少认证

- **WHEN** 发布脚本需要的认证不在当前已授权运行时上下文中
- **THEN** 系统以稳定认证失败原因结束
- **AND** 不提示用户将秘密填入 Manifest 或对话
