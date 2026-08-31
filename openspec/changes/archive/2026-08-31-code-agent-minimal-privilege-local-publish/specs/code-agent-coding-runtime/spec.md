## ADDED Requirements

### Requirement: Claude Code 必须可在持久 Sandbox 内自行安装依赖

当 Claude Code 在 local 发布或其他仓库任务中识别到缺失的系统或应用依赖时，Claude Code SHALL 在已绑定的持久 Sandbox 权限允许时自行安装并继续执行。系统 MUST NOT 为此提供平台 lockfile 安装桥或一次性 runner 的 root 引导阶段。Claude Code MUST NOT 获得 Docker socket、宿主机访问或 privileged 容器。

#### Scenario: 缺少发布所需工具

- **WHEN** Claude Code 确定任务缺少可安装工具且 Sandbox 权限允许安装
- **THEN** Claude Code 可在该 Sandbox 内安装最小依赖并继续任务
- **AND** 安装不得写入业务 Git 交付物

#### Scenario: 依赖安装失败

- **WHEN** Sandbox 权限、网络策略或仓库事实导致无法安装所需工具
- **THEN** Claude Code 报告确切阻断原因并停止
- **AND** Run 不得将安装失败显示为发布成功
