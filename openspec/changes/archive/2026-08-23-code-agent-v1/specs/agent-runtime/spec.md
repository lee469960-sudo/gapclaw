## ADDED Requirements

### Requirement: Runtime 在入口解析 Profile 而不改变 Standard 执行语义
统一 Runtime SHALL 在运行入口解析任务的显式 Profile，并将其编译为统一的执行上下文、能力集合与生命周期约束。对于未声明或选择 Standard Profile 的任务，Runtime SHALL 保持既有工具路由、运行行为、事件和终态语义。

#### Scenario: Standard Profile 与 Code Profile 并存
- **WHEN** 系统同时运行一个 Standard 任务和一个 Code Profile 任务
- **THEN** Standard 任务继续使用既有执行行为
- **AND** Code Profile 的专属上下文与能力仅作用于该 Code run

### Requirement: Code Profile 事件必须复用统一 Runtime 事件契约
Runtime SHALL 将 Code Profile 的准备、验证、审批/拒绝、封存和终结事件写入既有统一事件流，并允许携带版本化的 Profile 专属 payload。既有消费者 SHALL 能忽略未知 Profile payload 而继续处理通用事件字段。

#### Scenario: 旧事件消费者接收 Code run 事件
- **WHEN** 不识别 Code Profile 专属 payload 的既有消费者接收统一事件
- **THEN** 该消费者仍可读取通用事件字段
- **AND** 不因未知 Profile payload 失败

### Requirement: Code run 的终结必须进入统一清理生命周期
Runtime SHALL 将 Code run 的取消、超时、异常和正常结束纳入既有终结生命周期，并在结束时触发 Code Profile 所需的资源回收和审计封存。Code run 的清理失败 SHALL 产生可观察的基础设施异常。

#### Scenario: Code run 在工具执行中超时
- **WHEN** Code Profile 运行在工具执行过程中超时
- **THEN** Runtime 阻止新的工具动作并进入统一终结流程
- **AND** 记录资源回收或清理失败的结果

#### Scenario: 受管容器命令超过冻结 deadline
- **WHEN** 探索测试、验证基线或最终 Verifier 的受管容器命令超过该 run 的剩余冻结时限
- **THEN** 系统终止该命令并阻止新的执行动作
- **AND** run 以显式超时结果进入统一清理流程，而不是等待命令自行返回

#### Scenario: Runner 启动在 Workspace 准备后失败
- **WHEN** 系统已分配可写 Workspace，但 runner 在统一执行循环开始前启动失败
- **THEN** run 记录 `infrastructure_error` 并执行已分配资源的补偿清理
- **AND** 不保留可恢复执行的 `pending` run
