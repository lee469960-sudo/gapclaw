## Purpose

为 CodeAgent 提供与当前 Run 绑定的仓库 Workspace 预览，使用户能够查看真实代码工作区，而不是误把通用 `/workplace` 当作代码仓库。

## ADDED Requirements

### Requirement: CodeAgent Workspace 必须绑定当前 Run

系统 SHALL 在 CodeAgent 会话中展示当前 CodeAgent Run 的 Workspace，并使用该 Run 的权限、路径校验和生命周期状态。

#### Scenario: 展示已准备的仓库工作区

- **WHEN** 当前 Run 已完成 Workspace preparation 且 `workspace_path` 有效
- **THEN** 左侧 Workspace 展示该 Run 的仓库文件树和 Git 元数据
- **AND** 展示的根目录对应 CodeAgent `/workspace`，而不是通用 `/workplace`

#### Scenario: 工作区未准备时拒绝伪装为已挂载

- **WHEN** 当前 Run 缺少有效 `workspace_path` 或路径校验失败
- **THEN** Workspace 显示明确的未准备/挂载失败状态
- **AND** 不展示通用 `/workplace/task/` 作为仓库内容

### Requirement: Workspace 预览必须遵守 Run 权限和脱敏规则

系统 SHALL 只允许有权访问当前 Run 的用户读取 Workspace 预览，并 SHALL 过滤运行时目录、Git 内部元数据和敏感内容。

#### Scenario: 无权用户读取被拒绝

- **WHEN** 用户不具备当前 CodeAgent Run 的访问权限
- **THEN** Workspace API 返回授权错误
- **AND** 不返回仓库文件内容
