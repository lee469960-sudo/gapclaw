# code-agent-local-publish Specification

## Purpose

为 CodeAgent 提供一次性、仅面向 local 环境的受控发布能力，复用仓库和 CI 已批准的 canonical 命令，并在现有 Sandbox、审计与验证边界内安全地产生可追溯发布结果。

## Requirements

### Requirement: Local 发布必须使用已登记的 canonical 命令

系统 SHALL 将 local 发布作为普通 Claude Code 任务执行。系统 MUST NOT 要求 Manifest `local_publish_command_id`、平台命令注册表或环境变量命令表。发布入口由 Claude Code 阅读仓库 CI/脚本后决定。

#### Scenario: 执行已批准的 local 命令

- **WHEN** 用户明确请求 local 发布且 Workspace 已准备
- **THEN** 系统直接启动 Claude Code Runtime
- **AND** 不解析或冻结平台 canonical 命令 ID

#### Scenario: 请求未登记或非 local 命令

- **WHEN** 仓库没有可执行的发布路径或 Claude Code 无法确定入口
- **THEN** Claude Code 报告缺失事实或请求澄清
- **AND** 平台不得返回 `local_publish_entry_not_found`

### Requirement: Local 发布必须经过 preflight 与一次性人工确认

系统 SHALL NOT 在 local 发布前增加平台 preflight/dry-run 或一次性人工确认门禁。用户在对话中提出任务即进入 Claude Code Runtime。

#### Scenario: Preflight 通过并确认发布

- **WHEN** 用户在对话中明确请求 local 发布
- **THEN** 系统启动 Claude Code，不等待平台确认令牌
- **AND** 不产生独立的 preflight 发布阶段

#### Scenario: Preflight 失败或未确认

- **WHEN** Workspace、Sandbox 或认证上下文不可用
- **THEN** 系统以现有 CodeAgent 准备失败原因结束
- **AND** 不进入已废弃的待确认发布状态

### Requirement: Local 发布必须在受限现有 Sandbox 中执行

系统 SHALL 在当前绑定的持久 Sandbox 内执行 local 发布。允许访问的文件仅限该 Workspace 与平台批准挂载。系统 MUST NOT 暴露 Docker Socket、宿主机路径或 Secret Store。dbt、jq、`clickhouse client` 等业务工具不是启动必备项。

#### Scenario: 基础运行环境可用且业务依赖不阻塞启动

- **WHEN** Claude Code 在绑定 Sandbox 启动 local 发布任务
- **THEN** 基础运行来自该 Sandbox 镜像
- **AND** 缺失业务工具不阻塞 Runtime 启动
- **AND** Claude Code 可在权限允许时自行安装最小依赖

#### Scenario: 发布请求越过 Sandbox 边界

- **WHEN** 命令尝试 privileged、Docker socket 或未批准宿主路径
- **THEN** 适配器阻止该动作并记录稳定策略原因
- **AND** 不回退到 API 进程或宿主机执行

### Requirement: 发布凭证必须短暂注入且隔离

系统 SHALL 只使用当前 run 已获授权的运行时认证上下文。系统 MUST NOT 要求 Manifest 引用专用 local 发布 Secret，也 MUST NOT 把秘密值写入仓库文件、命令参数、日志、模型上下文或工件。

#### Scenario: 使用有效发布 Secret

- **WHEN** 仓库脚本可使用当前 Sandbox 已有授权完成请求
- **THEN** 系统允许该脚本运行
- **AND** 输出继续脱敏

#### Scenario: Secret 不可用

- **WHEN** 所需认证不在当前已授权上下文中
- **THEN** 系统以稳定认证失败原因结束
- **AND** 不提示把秘密填入 Manifest 或对话

### Requirement: Local 发布结果必须可验证且禁止不确定重试

系统 SHALL 继续由 Verifier 裁决任务成功。Claude Code 文本或进程退出码不得单独判定成功。超时、认证失败或状态未知时 MUST 停止；adapter MUST NOT 自动重试未知结果。

#### Scenario: 发布与验证均成功

- **WHEN** Claude Code 完成且 Verifier 通过
- **THEN** Run 按现有封存/结果路径标记成功
- **AND** 展示脱敏验证摘要

#### Scenario: 发布状态未知

- **WHEN** 执行超时、连接中断或无法确认远端状态
- **THEN** Run 进入非成功终态
- **AND** 系统不自动重试该未知结果
