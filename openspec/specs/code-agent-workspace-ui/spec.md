# code-agent-workspace-ui Specification

## Purpose

为 CodeAgent 提供与当前 Run 绑定的仓库 Workspace 预览，使用户能够查看真实代码工作区，而不是误把通用 `/workplace` 当作代码仓库。

## Requirements

### Requirement: CodeAgent Workspace 必须绑定当前 Run

系统 SHALL 在 CodeAgent 会话中展示当前项目挂载到绑定持久 Sandbox 的 Workspace，并使用项目访问授权和路径校验。左侧预览根目录 SHALL 对应 Claude Code 与 Sandbox 终端使用的同一项目目录。

#### Scenario: 展示已准备的仓库工作区
- **WHEN** 绑定 Sandbox 运行且项目 Workspace 已挂载
- **THEN** 左侧 Workspace 展示该仓库文件树、预览和变更
- **AND** 该内容与 Sandbox 终端和 Claude Code 可见内容一致

#### Scenario: 绑定 Sandbox 未运行
- **WHEN** 用户查看或启动任务时绑定 Sandbox 未运行
- **THEN** UI 显示 Sandbox 未运行及手动启动提示
- **AND** 不伪装为已挂载或展示其他通用工作目录

#### Scenario: 工作区未准备时拒绝伪装为已挂载
- **WHEN** 项目 Workspace 缺失或路径校验失败
- **THEN** UI 显示未准备或挂载失败状态
- **AND** 不展示通用工作目录作为仓库内容

### Requirement: Workspace 预览必须遵守 Run 权限和脱敏规则

系统 SHALL 只允许有权访问当前 Run 的用户读取 Workspace 预览，并 SHALL 过滤运行时目录、Git 内部元数据和敏感内容。

#### Scenario: 无权用户读取被拒绝

- **WHEN** 用户不具备当前 CodeAgent Run 的访问权限
- **THEN** Workspace API 返回授权错误
- **AND** 不返回仓库文件内容
