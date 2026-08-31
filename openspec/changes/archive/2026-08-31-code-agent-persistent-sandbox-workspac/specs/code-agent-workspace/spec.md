## MODIFIED Requirements

### Requirement: 每个 CodeAgent run 使用独立且固定基线的 Workspace

系统 SHALL 为每个 Code Project 维护可挂载到其绑定持久 Sandbox 的受管 Workspace，并在 Run 启动时通过绑定 Sandbox 将 Manifest 中的 Git 仓库同步到该 Workspace。任务 MAY 在该持久 Workspace 上连续执行；系统 MUST NOT 在每次任务结束时删除 Workspace 或 Sandbox。并发任务 MUST NOT 同时写入同一项目 Workspace。

#### Scenario: 两个 run 操作同一仓库
- **WHEN** 两个任务请求写入同一项目 Workspace
- **THEN** 系统只允许一个任务持有写锁
- **AND** 另一个任务拒绝或等待

#### Scenario: 固定基线准备
- **WHEN** 同一项目在同一持久 Sandbox 上执行后续任务
- **THEN** 系统在同一 Workspace 内 clone 或 fetch Manifest 配置的仓库和 ref
- **AND** 后续任务看到此前保留的工具环境和 Workspace 文件状态
- **AND** 左侧预览继续指向该项目 Workspace

#### Scenario: API 进程尝试源码操作
- **WHEN** API 进程需要执行仓库命令
- **THEN** 系统通过绑定 Sandbox 调度该操作
- **AND** API 进程不得直接在宿主 Workspace 中执行命令

#### Scenario: 同一 Workspace 已有写入任务
- **WHEN** 新任务尝试写入已有活动任务的项目 Workspace
- **THEN** 系统拒绝或排队该新任务并说明 Workspace 正忙
- **AND** 不并发修改同一份代码
