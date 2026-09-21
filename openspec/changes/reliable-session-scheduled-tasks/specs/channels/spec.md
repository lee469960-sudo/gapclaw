## ADDED Requirements

### Requirement: 定时任务通知与 Agent 执行解耦
当会话定时任务启用渠道通知时，系统 MUST 在其 Agent 执行成功写回会话后创建独立、可审计的通知投递。通知失败 MUST 不改变 Agent 执行的成功状态，也 MUST 不重跑 Agent；系统 MUST 对临时通知失败进行有限重试，并将最终投递失败作为会话中的可见告警记录。

#### Scenario: 通知失败不重跑已完成任务
- **WHEN** 定时任务已成功写回会话，但其 IM 通知暂时发送失败
- **THEN** 执行保持 `succeeded`，系统只重试通知投递且不再次启动 Agent

#### Scenario: 最终通知失败对用户可见
- **WHEN** 通知达到重试上限仍失败
- **THEN** 系统在原会话记录脱敏投递失败告警，并保留可查询的投递状态
