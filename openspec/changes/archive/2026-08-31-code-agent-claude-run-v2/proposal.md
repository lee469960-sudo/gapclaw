## Why

CodeAgent 目前能够在 Sandbox 中完成代码任务，但缺少受控执行 CI canonical local 发布命令的能力。需要在不重建 Task、Repository、Workspace、Sandbox 或 Verifier 架构的前提下，为单一仓库增加可审计、最小权限且仅面向 local 环境的发布闭环。

## What Changes

- 增加按 Manifest 登记的 `local_publish_command_id`，只允许执行已批准的 canonical 发布命令。
- 在现有 CodeAgent runner 中提供发布所需的固定基础工具链，并支持 `amd64`/`arm64` 双架构 digest 固定镜像；dbt、jq、clickhouse-client 等业务工具不是运行环境必备项。
- 为发布 run 增加非 root、禁止提权、受限文件路径和最小网络访问策略；禁止 Docker Socket 与宿主机路径暴露。
- 通过现有 Secret 管理运行时注入 local 发布凭证，禁止秘密进入仓库、命令参数、日志和模型上下文。
- 增加发布前 preflight/dry-run 与一次性人工确认；强制 local target，不允许切换到其他环境。
- 发布默认不执行 `git add/commit/push`，不自动重试副作用操作；超时或状态未知时转人工处理。
- 使用退出码、发布证据和 local 环境验证共同决定成功，继续沿用现有审计、Verifier 和结果展示链路。

## Capabilities

### New Capabilities

- `code-agent-local-publish`: 在现有 CodeAgent Sandbox 中执行受控 local 发布命令。

### Modified Capabilities

- `code-agent-control-plane`: Manifest、发布前门禁和人工确认增加 local 发布命令配置。
- `code-agent-project-policy`: 增加发布命令、网络、凭证、环境和并发策略约束。
- `code-agent-sandbox-runtime`: 为 local 发布 runner 固定工具链、最小网络和非 root 执行边界。
- `code-agent-verification`: 增加 local 发布结果证据与目标环境验证要求。
- `code-agent-conversation-progress`: 展示 preflight、确认、发布过程、证据和终态结果。

## Impact

- API：Manifest 校验、发布 Run 调度、Secret 注入、审计事件和验证结果序列化。
- Runner 镜像：增加经批准且固定版本的发布依赖，构建 `amd64` 与 `arm64` 变体。
- Sandbox 策略：增加仅针对 local 发布的网络/命令权限，不改变普通 CodeAgent 默认隔离。
- 前端：展示发布前检查、人工确认、脱敏执行过程、发布 ID 和验证证据。
- 兼容性：未选择 local 发布能力的现有 CodeAgent run 行为保持不变。
