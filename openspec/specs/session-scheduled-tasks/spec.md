# session-scheduled-tasks Specification

## Purpose

为每个会话提供可配置、可审计且在重启和多副本部署下仍可靠的自动任务，使任务结果可回到其原始会话并由任务所有者管理。

## Requirements

### Requirement: 空执行拦截与会话内实时执行过程
系统 MUST 拒绝空白触发配置，并在 Worker 入口再次拦截遗留空白触发；空白结果 MUST NOT 标记成功。系统 MUST 跨 Worker/API 进程持久化并展示实际执行步骤与工具状态，刷新页面后可恢复，结束后像普通 assistant 回复一样展示原会话结果或安全质量说明。每次定时执行结束后，系统 MUST 记录一个可恢复的终态结果，明确区分 `succeeded`、`failed`、`cancelled`、排队超时、no-progress 自动结束和通知失败；终态展示不得留下无法随会话清空一起消失的额外会话 UI。此能力不包含输出删除。

#### Scenario: 独立 Worker 正在调用工具
- **WHEN** 定时任务在独立 Worker 中执行模型或工具步骤
- **THEN** 有会话权限的用户能在原会话看到步骤进度，刷新后仍可读取，完成后看到结果

#### Scenario: 空白执行被拦截
- **WHEN** 配置或遗留任务的触发消息为空白，或运行产生空白结果
- **THEN** 空白触发不调用 Agent，空白结果不记录成功，执行历史包含明确原因

#### Scenario: 定时执行结束后刷新会话
- **WHEN** 定时任务从 `running` 进入任一终态且用户刷新或重新打开原会话
- **THEN** 会话时间线像普通对话一样展示该执行的最终回复或安全质量说明，执行历史展示状态、步骤摘要、失败原因和通知状态
- **AND** 用户不需要手动根据 `chat_message_id` 查找数据库或重新触发任务才能看到最终结果

#### Scenario: no-progress 自动结束不是成功完成
- **WHEN** Agent 因连续重复输出且没有新的工具、文件或计划进展而被 no-progress 保护停止
- **THEN** 执行记录为非成功终态并展示最后有效步骤、最后原始输出或安全中间结果摘要
- **AND** 系统不得把该终态显示为“任务完成”

### Requirement: 会话拥有者可配置受限的定时任务
系统 MUST 允许会话拥有者或具有会话编辑权的协作者为该会话创建一次性、固定间隔或 Cron 任务。任务 MUST 持久化会话、Agent 和所有者归属；Cron 任务 MUST 持久化 IANA 时区，默认 `Asia/Shanghai`。服务端 MUST 拒绝无效 Cron/时区、少于五分钟的重复间隔、每会话超过 20 个启用任务或每用户超过 100 个启用任务的请求。

#### Scenario: 使用默认时区创建 Cron 任务
- **WHEN** 有编辑权的用户未指定时区而为会话创建有效 Cron 任务
- **THEN** 系统以 `Asia/Shanghai` 持久化该任务并返回按该时区计算的下次执行时间

#### Scenario: 不合法的调度配置被拒绝
- **WHEN** 用户提交无效时区、无效 Cron、少于五分钟的重复间隔或超过配额的启用任务
- **THEN** 系统拒绝创建或更新且返回可操作的校验错误

### Requirement: 定时任务管理受会话权限和生命周期约束
系统 MUST 对任务的读取、修改、启停、删除、立即执行、重试、停止当前运行和执行历史查询同时校验当前用户、Agent、会话与任务归属。任务删除 MUST 为软删除并保留历史；会话归档、删除或用户失去会话访问权时，系统 MUST 取消未启动执行并停用该任务。用户显式停止周期任务时，系统 MUST 停用未来触发、取消未启动实例，并向正在运行实例持久化发送取消请求。

#### Scenario: 无权用户不能操作已知任务标识
- **WHEN** 不拥有任务且没有其会话编辑权的用户使用有效任务标识请求读取或修改
- **THEN** 系统拒绝请求且不暴露任务配置或执行历史

#### Scenario: 删除任务不抹除审计历史
- **WHEN** 有权限用户删除一个任务
- **THEN** 系统阻止其未来调度、取消未启动实例并保留既有执行记录

#### Scenario: 停止正在运行的周期任务
- **WHEN** 有权限用户在原会话停止一个正在运行的周期任务
- **THEN** 系统停用该任务、取消尚未启动实例，并使独立 Worker 在其下一个可取消边界终止当前 Agent 运行；该运行记录为 `cancelled`，不会创建成功通知或再次触发

### Requirement: 每个计划实例具有可审计的唯一执行结果
系统 MUST 为每个计划触发和立即执行创建可查询的执行记录，并以任务与计划触发时间的唯一标识防止同一实例重复启动 Agent。执行记录 MUST 公开 `pending`、`running`、`succeeded`、`failed`、`skipped` 或 `cancelled` 状态，以及计划、开始、结束时间、尝试次数、失败摘要、通知状态和关联会话消息（如有）。执行历史 MUST 返回足够的安全结果预览，使界面能展示最近执行的终态和最终内容摘要，而不仅是消息标识。

#### Scenario: 多个调度副本竞争同一触发
- **WHEN** 多个 Scheduler 副本同时发现同一个到期任务
- **THEN** 仅一个副本启动对应的 Agent 执行，其他副本不创建重复执行

#### Scenario: 用户查看失败原因
- **WHEN** 一次任务因最终失败而结束
- **THEN** 有权限用户能在该会话中看到失败状态、尝试次数和脱敏失败摘要

#### Scenario: 用户查看最近成功结果
- **WHEN** 一次任务成功写回原会话但用户当前没有看到该消息
- **THEN** 任务列表和执行历史返回该执行的安全结果预览、关联消息标识和可用于定位或展开结果的状态

#### Scenario: 通知失败但会话结果成功
- **WHEN** 定时任务已成功写回原会话结果但 IM 或消息渠道通知最终失败
- **THEN** 执行记录保持 `succeeded`
- **AND** 会话终态视图和执行历史展示通知失败警告及脱敏原因
- **AND** 系统不得因为通知失败而重新运行 Agent 或把任务本身显示为失败

### Requirement: 调度可恢复、顺序执行且有限重试
系统 MUST 在服务恢复后于配置的补跑窗口内为每个错过任务补跑一次，并记录补跑来源。对于同一会话，自动执行 MUST 顺序排队且同一时刻至多一个运行；超过最大排队时长的实例 MUST 以可见失败终止。仅临时基础设施失败可自动执行最多三次指数退避重试；业务失败 MUST 不自动重跑。

#### Scenario: 重启后补跑一次错过触发
- **WHEN** Scheduler 在补跑窗口内恢复且发现任务错过一次计划触发
- **THEN** 系统创建且仅创建一个补跑执行记录，并在会话中留下结果

#### Scenario: 会话正运行时的新触发排队
- **WHEN** 同一会话已有自动任务正在运行且新的实例到期
- **THEN** 新实例保持 `pending` 直到可顺序运行，或在超过最大排队时长后可见地失败

### Requirement: 定时执行复用会话运行时并可验证完成
系统 MUST 将定时触发注入原会话的正式 Agent 运行路径，复用该会话当前 Agent、模型和已绑定能力；任务可选择使用创建时运行时配置快照。Agent 正常结束且其结果成功写入原会话消息流时，执行才 MUST 标记为 `succeeded`。运行时快照或实际配置版本 MUST 与执行记录关联。定时执行 MUST 使用有界的定时任务上下文，避免同一会话的历史定时触发、重复结果和通知警告无限进入模型上下文；该上下文 MUST 保留当前触发消息、会话归属、Agent 配置、已绑定能力和必要的最近摘要。

#### Scenario: 成功结果回写原会话
- **WHEN** 定时任务成功完成 Agent 运行
- **THEN** 结果作为原会话中的可见消息写回，执行记录关联该消息并标记为 `succeeded`

#### Scenario: 锁定快照的任务在配置变更后运行
- **WHEN** 用户为任务启用配置快照，随后更新会话的模型或绑定能力
- **THEN** 后续定时执行使用任务创建时的快照，并在执行历史中标明该版本

#### Scenario: 取消不伪造成功结果
- **WHEN** Worker 在 Agent 运行期间观察到已持久化的取消请求
- **THEN** Worker 终止该运行并记录 `cancelled`，释放会话执行槽；不绑定成功消息、不创建通知 outbox，且不把取消作为可自动重试的基础设施错误

#### Scenario: 重复定时不会污染后续运行上下文
- **WHEN** 同一个会话中的同一个定时任务已经执行多次且产生相似触发消息和结果
- **THEN** 后续定时执行仍使用有界上下文运行，不把全部历史定时用户消息、结果消息、进度卡片和通知警告完整注入模型
- **AND** 执行不会仅因为历史重复定时内容增长而更容易触发 no-progress 自动结束

#### Scenario: 未绑定能力的 Agent 行为保持可解释
- **WHEN** 定时任务使用的 Agent 没有绑定完成目标所需的 Skill、MCP 或渠道能力
- **THEN** 有界上下文不伪造能力，运行结果必须明确说明缺失能力或可用降级路径，并以成功或非成功终态按实际写回和完成情况记录

### Requirement: 用户可在会话内观察和控制任务
系统 MUST 在会话任务界面提供一次性、间隔和 Cron 配置，提供可搜索的全量 IANA 时区下拉列表并默认 `Asia/Shanghai`。界面 MUST 展示下次执行、最近状态、执行历史、最终结果预览、通知状态和失败详情，并支持立即执行、启停、删除与对最终失败实例的手动重试；立即执行 MUST 使用与正式调度相同的队列、执行台账和回写语义。会话时间线 MUST 在定时任务运行中展示实时进度，并在运行结束后通过普通 assistant 会话消息展示可恢复的终态结果，而不是只显示临时执行中卡片、额外残留终态卡片或消息编号。

#### Scenario: 立即执行遵循正式执行路径
- **WHEN** 有权限用户请求立即执行一个任务
- **THEN** 系统创建可审计执行记录，并按同会话顺序与正式任务相同的语义运行

#### Scenario: 终态结果在会话中可见
- **WHEN** 定时任务完成、失败、取消或 no-progress 自动结束
- **THEN** 用户在原会话时间线能看到一条普通 assistant 回复形式的最终回复或安全质量说明
- **AND** 刷新页面、切换会话再返回或轮询错过状态变化后仍能恢复显示

#### Scenario: 执行历史可定位最终回复
- **WHEN** 用户在定时任务列表或历史中点击最近一次执行
- **THEN** 界面能定位、展开或显示该执行的最终结果内容，并同时显示执行状态和通知状态

### Requirement: 既有 Tick 配置安全迁移且不双重执行
系统 MUST 将可归属的既有 Tick 配置迁移到会话定时任务，使用 `Asia/Shanghai` 作为缺失时区的默认值。迁移后旧 Tick 写接口 MUST 不再创建独立调度行为；兼容期内旧接口仅可读取迁移结果。无法安全归属的旧任务 MUST 被禁用并记录迁移原因。

#### Scenario: 迁移后的 Cron 只执行一次
- **WHEN** 已迁移的旧 Cron 到期且新旧服务均部署
- **THEN** 仅新的会话任务执行路径启动 Agent，旧接口不会启动第二次执行

### Requirement: 到期任务必须按 SLA 准时入队
When scheduled-task Workers are healthy and the database is reachable, the system SHALL create a durable run record for each due enabled task within 15 seconds of its scheduled time. This SLA applies to run creation, not to Agent completion. Agent execution duration, LLM latency, MCP latency, notification delivery, and prior same-session runs MUST NOT prevent the scheduler from scanning due tasks and recording their due state. If the scheduler cannot satisfy the SLA, the task and run history MUST expose the delay as a scheduling health issue.

#### Scenario: 健康 Worker 准时创建 run
- **WHEN** an enabled task reaches `next_run_at` and a healthy scheduler Worker is active
- **THEN** the system creates a durable run within 15 seconds of that scheduled time
- **AND** the run records the original `scheduled_for` timestamp separately from execution start time

#### Scenario: 长 Agent 执行不阻塞后续入队
- **WHEN** one scheduled Agent execution is still running or retrying external LLM/MCP calls
- **THEN** due occurrences for other tasks are still discovered and recorded within the scheduling SLA
- **AND** delayed execution is represented as executor delay rather than missing scheduling

#### Scenario: 调度延迟可诊断
- **WHEN** a run is created more than 15 seconds after its `scheduled_for` timestamp
- **THEN** task history exposes a scheduling delay value and a health reason
- **AND** the UI does not make the task appear simply "not running" without diagnostic context

### Requirement: 调度器和执行器必须解耦且同会话执行保持有界
The system SHALL separate due-occurrence creation from Agent execution. Scheduler work MUST remain lightweight and MUST NOT synchronously wait for Agent completion, LLM retries, MCP discovery, or notification delivery. Executor work SHALL process runnable occurrences independently while preserving the rule that the same session has at most one scheduled Agent execution running at a time. If a repeated task reaches its next occurrence while the previous same-task or same-session occurrence is still active, the system SHALL skip or coalesce overlapping periodic occurrences rather than building an unbounded backlog.

#### Scenario: Scheduler 不等待 executor
- **WHEN** an executor is busy running a scheduled Agent task
- **THEN** the scheduler can still create, skip, or mark due occurrences according to policy
- **AND** scheduler heartbeat and due-run creation continue without waiting for the active Agent run to finish

#### Scenario: 同会话严格串行
- **WHEN** multiple scheduled occurrences for the same session are due
- **THEN** at most one scheduled Agent execution for that session runs at a time
- **AND** later occurrences are either pending within bounds, skipped, or coalesced with explicit state

#### Scenario: 重复周期不无限排队
- **WHEN** a periodic task becomes due again while an earlier occurrence for the same task is still pending or running
- **THEN** the system does not accumulate an unbounded queue of every missed interval
- **AND** skipped or coalesced occurrences are visible in execution history

### Requirement: 恢复、僵尸运行和超时必须安全收敛
On Worker recovery, the system SHALL create at most the latest missed occurrence per task and mark older missed windows as `skipped` or `missed` rather than replaying every interval. A `running` occurrence whose lease has expired and whose worker heartbeat is stale SHALL be finalized as `failed` with a stale-run reason before the latest missed occurrence is considered for compensation. A scheduled Agent execution that exceeds the default 10-minute runtime budget SHALL be terminated and finalized as `failed` with a timeout reason.

#### Scenario: 恢复后只补偿最近一次
- **WHEN** a task missed multiple periodic windows while Workers were stopped
- **THEN** the system creates at most one compensation run for the latest missed scheduled time
- **AND** older missed windows are recorded or summarized as skipped/missed without Agent execution

#### Scenario: 过期 running 不直接重跑旧副作用
- **WHEN** a run remains `running` after its lease expires and its owning Worker is unhealthy
- **THEN** the system marks that run as `failed` with a stale-run reason
- **AND** it does not blindly re-execute the stale run before applying the latest-missed compensation policy

#### Scenario: 单次执行超过 10 分钟
- **WHEN** a scheduled Agent execution runs longer than the default 10-minute timeout
- **THEN** the system requests cancellation or terminates the execution at the next safe boundary
- **AND** the run is finalized as `failed` with a timeout summary
- **AND** future occurrences remain schedulable after the session slot is released

### Requirement: Worker 健康、部署状态和运行诊断必须可见
The system SHALL record scheduler and executor Worker heartbeat information. A Worker that has not produced a heartbeat for 60 seconds SHALL be considered unhealthy. The scheduled-task API and UI SHALL expose enough diagnostics to distinguish "not configured", "not scheduled yet", "queued", "running", "delayed", "timed out", "stale", "failed", "succeeded", and "notification failed". Task list and run history responses SHALL include `next_run`, `last_scheduled`, `last_started`, `last_finished`, `last_status`, scheduling delay, executor delay when available, and Worker health. Docker and cloud deployment configuration SHALL make scheduled-task Workers explicit and verifiable.

#### Scenario: Worker 未运行时界面告警
- **WHEN** scheduled-task Workers are disabled, missing, or have no heartbeat for at least 60 seconds
- **THEN** the task UI shows a Worker health warning
- **AND** task configuration remains visible without implying that future occurrences will execute

#### Scenario: 通知失败不改变任务成功
- **WHEN** an Agent run succeeds and writes the session result but notification delivery fails
- **THEN** the run remains `succeeded`
- **AND** notification state is shown separately as failed with a redacted reason

#### Scenario: 运行诊断字段可见
- **WHEN** a user views scheduled tasks or a run history entry
- **THEN** the response includes timing and health diagnostics for next run, last scheduled time, start time, finish time, status, delay, and Worker health
- **AND** these diagnostics are safe for users with session access and do not expose secrets, prompts, or internal credentials

#### Scenario: 部署缺少 Worker 可被验证
- **WHEN** the API is running but the scheduled-task scheduler or executor Worker is not deployed or not enabled
- **THEN** operational health checks and UI/API diagnostics report the missing Worker
- **AND** users are not left with enabled tasks that silently never scan
