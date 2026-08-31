# code-agent-persistent-sandbox-binding Specification

## Purpose
让 CodeAgent 复用编辑页已绑定的持久 Sandbox，使 Claude Code、终端和代码预览操作同一份持久 Workspace。

## Requirements

### Requirement: CodeAgent 必须复用编辑页已绑定的持久 Sandbox

系统 SHALL 使用 CodeAgent 编辑页已有的 Sandbox 绑定作为唯一执行环境，MUST NOT 为 Code run 创建替代 Sandbox 或专用 runner。该 Sandbox 的镜像、用户、网络、工具和生命周期 SHALL 继续由现有 Sandbox 功能管理。

#### Scenario: 已绑定且运行中的 Sandbox 执行任务
- **WHEN** CodeAgent 已绑定一个运行中的持久 Sandbox 并发起任务
- **THEN** Claude Code 与代码工具在该 Sandbox 中执行
- **AND** 用户从 Sandbox 页面进入终端时看到相同的运行环境

#### Scenario: 绑定 Sandbox 未运行
- **WHEN** 用户发起任务时已绑定的 Sandbox 处于停止状态
- **THEN** 系统拒绝启动该任务并显示要求用户手动启动 Sandbox 的可行动提示
- **AND** 系统不得自动启动 Sandbox 或创建替代 Sandbox

### Requirement: 同一项目 Workspace 必须挂载到绑定 Sandbox

系统 SHALL 将 Code Project 的受管 Workspace 挂载到绑定 Sandbox 的稳定项目目录，使 Claude Code、Sandbox 终端和左侧预览读取同一份仓库内容。

#### Scenario: 用户在终端安装工具或修改文件
- **WHEN** 用户在绑定 Sandbox 的终端安装工具或修改 Workspace 文件
- **THEN** 后续 CodeAgent 任务可以使用该工具并读取该文件状态
- **AND** 左侧 Workspace 预览展示相同的文件变更
