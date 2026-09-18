# Release Agent 版本同步与发布通知

## 工作流

可信 Deploy Runner 回传 `succeeded` 且健康检查结果为 `ok`、`healthy`、`passed` 或 `success` 后，GAP 才会把已验签 manifest 的 `version/tag` 写入站点设置 `version`。重复回调按 `release_id + transition` 幂等处理，不会重复覆盖或产生新的同步记录。

`failed`、`rolled_back` 和 `reconciliation_required` 只产生发布通知，不会更新站点版本。

## HTTP MCP 固定能力

HTTP MCP 接口动作 `release_version_sync` 仅接受：

- `agent_id: release-agent`
- 已落库且通过健康校验的 `release_id`
- 固定的 `target_id`

URL、命令、镜像、凭据和用户传入的版本号不会被使用。非管理员或非 Release Agent 来源会被拒绝。

## 飞书机器人配置

在“消息渠道”中新建一个独立的飞书渠道：

1. 填写现有飞书机器人所需的 App ID、App Secret、Verification Token。
2. 绑定 Agent 选择“Release Agent（仅发布通知）”。
3. 填写 `通知 Chat ID`（配置字段 `release_chat_id`）。
4. 配置并验证公网 HTTPS Webhook，启用渠道。

该渠道不会接管其他 Agent 的会话。没有配置渠道时，发布和版本同步仍继续，只记录通知缺失告警。

## 通知与审计

通知包含版本/tag、commit SHA、目标、API/Web digest、健康结果、回滚结果和失败摘要，不包含 HMAC、机器人密钥、ACR 凭据或完整签名 payload。

审计表记录：

- `release_version_syncs`：版本同步状态、版本、commit、transition 和时间。
- `release_notification_deliveries`：渠道、transition、状态、脱敏 payload、错误和投递时间。

同一 `release_id + transition + channel_id` 只发送一次；失败可重试，不会重新部署或改变 Runner 的权威状态。

## 禁用与回退

可停用 Release Agent 飞书渠道，或移除 `release_chat_id`；这只停止通知，不影响部署和版本同步。若版本同步需要人工处理，可暂时移除站点版本覆盖，让站点回退到 `deploy/gap.version` 的构建版本。Release Agent 始终不获得主机 shell、Docker、SSH 或 ACR 写入权限。
