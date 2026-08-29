## ADDED Requirements

### Requirement: Local 发布 runner 必须提供固定最小工具链

支持 local 发布的 runner image SHALL 由平台批准、固定 digest 并同时提供 `amd64` 与 `arm64` 变体。镜像仅预装 canonical 命令所需的固定版本工具，运行期间不得动态安装依赖。

#### Scenario: 双架构镜像通过 preflight

- **WHEN** local 发布 Run 选择批准的 runner digest
- **THEN** runner 在目标架构上可用且 preflight 能确认工具版本
- **AND** 工具版本和镜像 digest 进入审计事实

### Requirement: Local 发布网络与权限必须受限

Local 发布 runner SHALL 以非 root、禁止提权和仅 Workspace 可写的配置运行。网络例外仅限 Manifest 明确声明的发布目的地，其他 CodeAgent Run 的默认隔离行为不得改变。

#### Scenario: 访问未授权网络

- **WHEN** 发布进程连接 Manifest 未声明的主机或端口
- **THEN** Sandbox 阻止连接并记录网络策略失败
- **AND** 不允许通过重试或更换网络模式绕过限制
