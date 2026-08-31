## Purpose

为 CodeAgent 提供一次性、仅面向 local 环境的受控发布能力，复用仓库和 CI 已批准的 canonical 命令，并在现有 Sandbox、审计与验证边界内安全地产生可追溯发布结果。

## ADDED Requirements

### Requirement: Local 发布必须使用已登记的 canonical 命令

系统 SHALL 仅允许 CodeAgent 执行 Manifest 中登记的 `local_publish_command_id`，命令实现可来自仓库脚本但不得由模型临时拼接。发布任务 SHALL 强制绑定 `local` 环境，禁止通过参数或环境变量切换到其他环境。

#### Scenario: 执行已批准的 local 命令

- **WHEN** 有效 Manifest 登记了 local 发布命令且任务请求执行 local 发布
- **THEN** 系统仅执行该命令 ID 对应的 canonical 命令
- **AND** 运行契约固定目标环境为 `local`

#### Scenario: 请求未登记或非 local 命令

- **WHEN** 任务请求未登记的命令或尝试选择 `dev`、`pre`、`prod` 等环境
- **THEN** 系统拒绝执行并返回稳定的策略拒绝原因
- **AND** 不启动发布进程

### Requirement: Local 发布必须经过 preflight 与一次性人工确认

系统 SHALL 在产生外部副作用前检查命令、依赖、Workspace、网络、Secret 引用、资源预算和目标环境，并执行无副作用的 dry-run（若命令支持）。正式发布 SHALL 等待当前 Run 的一次性人工确认。

#### Scenario: Preflight 通过并确认发布

- **WHEN** preflight/dry-run 通过且用户确认当前 local 发布 Run
- **THEN** 系统启动正式 canonical 命令一次
- **AND** 确认仅对当前 Run 有效并记录审计事实

#### Scenario: Preflight 失败或未确认

- **WHEN** 任一前置检查失败或用户未确认
- **THEN** 系统不得产生发布副作用
- **AND** Run 以可行动的非成功原因结束或保持待确认状态

### Requirement: Local 发布必须在受限现有 Sandbox 中执行

系统 SHALL 在当前 CodeAgent 专用 runner 内以非 root 身份执行 local 发布，允许访问的文件仅限当前 Workspace 和运行时临时目录。网络仅允许已发布 Manifest 明确声明的目的地，且不得暴露 Docker Socket、宿主机路径或 Secret Store。

#### Scenario: 基础运行环境可用且业务依赖不阻塞启动

- **WHEN** runner 启动 local 发布 Run
- **THEN** CodeAgent/Claude Code 运行和受控命令执行所需基础工具来自平台批准的固定 digest 镜像
- **AND** dbt、jq、clickhouse-client 等项目/CI 业务工具不是 runner 启动必备项
- **AND** 对 Manifest 登记依赖的探测结果仅作为审计事实，缺失不得阻塞 Workspace 或 Runtime 启动
- **AND** 运行期间不得通过 apt、pip 或 npm 动态安装依赖

#### Scenario: 发布请求越过 Sandbox 边界

- **WHEN** 命令尝试提权、访问未授权路径或连接未授权网络
- **THEN** 系统阻止该动作并记录稳定策略原因
- **AND** 不回退到 API 进程或宿主机执行

### Requirement: 发布凭证必须短暂注入且隔离

系统 SHALL 通过现有 Secret 管理向发布进程注入仅具备 local 权限的凭证。凭证值 MUST NOT 出现在仓库文件、命令参数、日志、模型上下文、Workspace 持久数据或工件中。

#### Scenario: 使用有效发布 Secret

- **WHEN** Manifest 引用了有效且有权使用的 local 发布 Secret
- **THEN** 系统仅在发布进程生命周期内注入该凭证
- **AND** 运行结束后撤销或清理其可用性

#### Scenario: Secret 不可用

- **WHEN** Secret 缺失、失效或权限不足
- **THEN** 系统在 preflight 阶段拒绝发布
- **AND** 不启动 canonical 命令且不泄露凭证内容

### Requirement: Local 发布结果必须可验证且禁止不确定重试

系统 SHALL 以进程退出码、发布结果证据和 local 目标环境验证共同判定成功。命令超时、网络中断或状态未知时 MUST 停止并转人工处理，不得自动重试或宣称成功；默认不得执行 Git 提交操作。

#### Scenario: 发布与验证均成功

- **WHEN** canonical 命令成功退出并产生发布证据，且 local 环境验证通过
- **THEN** Run 标记为成功并展示发布 ID、验证摘要和脱敏审计事件

#### Scenario: 发布状态未知

- **WHEN** 发布超时、连接中断或无法确认远端状态
- **THEN** Run 进入状态未知的非成功终态
- **AND** 系统保留证据和恢复建议，不自动重试
