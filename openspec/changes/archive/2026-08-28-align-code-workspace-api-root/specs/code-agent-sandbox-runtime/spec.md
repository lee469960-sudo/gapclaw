## MODIFIED Requirements

### Requirement: Docker 部署必须验证 Workspace bind path 映射

当 API 运行在容器内并通过外部 Docker daemon 启动 runner 时，系统 SHALL 将 API 容器可见的 Workspace 路径显式映射为 daemon 主机可见且位于批准根目录内的 bind path。启动前 SHALL 验证映射存在、规范化后未逃逸、指向预期 run Workspace 且以预期读写模式挂载；映射无效时不得启动 runner。API 可见 Workspace 路径 MUST 位于与 Workspace 物化相同的配置 API root（或规范 fallback）之下；因物化根与映射根不一致而导致路径无法通过 containment 校验时，系统 MUST 以 `workspace_mount_invalid` fail closed。

#### Scenario: Compose 环境正确挂载 Workspace

- **WHEN** API 容器内 Workspace 路径具有有效的 daemon 主机路径映射
- **THEN** runner 在约定容器路径看到同一 run 的源码与写入结果
- **AND** 真实 Docker 集成验证可证明 API、主机与 runner 三方路径身份一致

#### Scenario: Compose 映射缺失或错误

- **WHEN** 主机路径映射缺失、不存在、逃逸批准根目录或指向其他 run
- **THEN** 系统以 `workspace_mount_invalid` 终止准备
- **AND** 不使用空目录、API 容器路径或猜测路径启动 runner

#### Scenario: Workspace 物化根与映射 API root 不一致

- **WHEN** 已准备的 Workspace 路径不在当前配置的 Workspace API root（或规范 fallback）之下
- **THEN** 系统在启动 runner 前以 `workspace_mount_invalid` 终止
- **AND** 不把该路径作为 Docker bind source 交给 daemon
