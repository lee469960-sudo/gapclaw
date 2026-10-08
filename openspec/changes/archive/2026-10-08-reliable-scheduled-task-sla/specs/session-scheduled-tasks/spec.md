## ADDED Requirements

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
