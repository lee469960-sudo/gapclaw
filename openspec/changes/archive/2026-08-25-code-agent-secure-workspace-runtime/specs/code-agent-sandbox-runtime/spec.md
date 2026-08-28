## Purpose

定义 CodeAgent Code Tools 的单一容器执行边界、部署路径映射、运行时加固和资源生命周期，使代码操作不能退回 API 进程或越过 Workspace。

## ADDED Requirements

### Requirement: 所有 Code Tools 必须在每 run 专用容器内执行

`read`、`search`、`edit`、`git`、`shell` 和 `test` 能力 SHALL 仅在绑定该 Code run 的专用 runner 容器内执行。API 进程 SHALL 只执行鉴权、策略计算、调度、审计和工件协调，MUST NOT 直接读取、搜索、修改或执行 Workspace 内容；runner 不可用时系统 MUST fail closed，不得回退宿主执行。

#### Scenario: 执行读取到测试的工具序列

- **WHEN** CodeAgent 在有效 run 中调用任一允许的 Code Tool
- **THEN** 调用在该 run 的同一专用 runner 边界和冻结 Workspace 内执行
- **AND** 审计事实记录 run、容器、工具、策略版本和结果

#### Scenario: runner 启动失败

- **WHEN** runner 镜像不可用、digest 不匹配或容器无法安全启动
- **THEN** 系统以稳定的基础设施或策略原因终止准备
- **AND** 不在 API 进程或其他容器中重试该 Code Tool

### Requirement: runner 必须使用不可绕过的隔离配置

runner SHALL 使用管理员批准且按 digest 固定的镜像、非特权身份、只读 root filesystem、全部 capability drop、`no-new-privileges` 和默认无网络配置。仅该 run 的 Workspace 可写；Docker socket、Secret Store、API 文件系统、其他 run Workspace 和未批准宿主路径 MUST NOT 被挂载或暴露。任何网络例外 SHALL 同时获得平台和已发布 Manifest 明确授权，并限制到声明的目标或隔离 sidecar。

#### Scenario: 默认启动隔离 runner

- **WHEN** 有效 Code run 启动且未声明获批网络例外
- **THEN** runner 以固定镜像 digest、只读 rootfs、无额外 capabilities、禁止提权和无网络方式运行
- **AND** 除当前 Workspace 外不存在可写挂载

#### Scenario: 请求未批准网络或宿主挂载

- **WHEN** Manifest、任务或工具请求未获平台批准的网络目标、Docker socket 或宿主路径
- **THEN** 系统在容器启动或动作执行前拒绝请求
- **AND** 记录不可被任务设置覆盖的策略拒绝

### Requirement: 运行资源限制必须取所有层级的最严格值

runner 的 CPU、内存、进程数、磁盘、执行时间和输出限制 SHALL 取平台、组织、项目、Manifest、Profile 与任务各层有效值中的最严格值。达到任一限制时系统 SHALL 停止新的工具动作，保留可允许的审计事实，并以稳定原因结束或取消 run。

#### Scenario: Manifest 预算高于平台上限

- **WHEN** Manifest 请求的资源预算高于平台硬上限
- **THEN** 有效 runner 配置使用平台上限
- **AND** 审计记录请求值、有效值和限制来源

#### Scenario: runner 达到资源限制

- **WHEN** runner 超过冻结的 CPU、内存、进程、磁盘、时间或输出限制
- **THEN** 系统停止容器与后续工具动作并标记对应的 `budget_exhausted` 或 `resource_limit_exceeded`
- **AND** 不把未完成验证的修改标记为 `patch_ready`

### Requirement: Docker 部署必须验证 Workspace bind path 映射

当 API 运行在容器内并通过外部 Docker daemon 启动 runner 时，系统 SHALL 将 API 容器可见的 Workspace 路径显式映射为 daemon 主机可见且位于批准根目录内的 bind path。启动前 SHALL 验证映射存在、规范化后未逃逸、指向预期 run Workspace 且以预期读写模式挂载；映射无效时不得启动 runner。

#### Scenario: Compose 环境正确挂载 Workspace

- **WHEN** API 容器内 Workspace 路径具有有效的 daemon 主机路径映射
- **THEN** runner 在约定容器路径看到同一 run 的源码与写入结果
- **AND** 真实 Docker 集成验证可证明 API、主机与 runner 三方路径身份一致

#### Scenario: Compose 映射缺失或错误

- **WHEN** 主机路径映射缺失、不存在、逃逸批准根目录或指向其他 run
- **THEN** 系统以 `workspace_mount_invalid` 终止准备
- **AND** 不使用空目录、API 容器路径或猜测路径启动 runner

### Requirement: runner 容器必须在所有终态立即移除

系统 SHALL 在成功、验证失败、策略拒绝、取消、超时、预算耗尽或基础设施失败后停止并立即移除该 run 的 runner 容器及临时网络。清理失败 SHALL 产生可观察且可重试的清理记录，容器不得继续接受工具动作。

#### Scenario: run 正常或异常结束

- **WHEN** Code run 进入任一终态
- **THEN** 系统撤销 runner 执行资格并立即请求停止和移除容器及临时网络
- **AND** Workspace 是否保留由独立保留策略决定

#### Scenario: 容器删除失败

- **WHEN** runner 停止或删除操作失败
- **THEN** 系统记录 `sandbox_cleanup_failed` 并触发受控重试或告警
- **AND** 不将残留容器视为可运行资源
