## Why

本地运行日志已经能暴露 Telegram、LLM、cloudflared、轮询和 Agent loop 问题，但当前信号分散且噪声过高：`check_status` 304 与 `no_progress_hint` 会淹没真正错误，`status=429`、`ConnectError` 和 Telegram poll 失败也缺少结构化归因。需要把日志从“grep 累计次数”收敛为可判断当前健康、趋势和失败类别的运行可观测性能力。

## What Changes

- 新增本地运行可观测性契约，定义健康检查与日志分析应区分当前窗口、全量趋势和组件状态。
- 对高频 `check_status` 304 access log 进行抑制、采样或降级，避免轮询噪声淹没故障日志。
- 聚合 `no_progress_hint` 日志，保留调试价值但避免每轮重复刷屏。
- 为 Telegram poll、LLM 429、网络连接失败和 cloudflared 重连输出结构化日志字段，至少包含组件、错误类别、provider/channel/tunnel 上下文和退避状态。
- 为 LLM 429 引入明确的 provider 级退避/降级可观测信号，避免把配额或限流误判为业务代码错误。
- 保持现有日志文件路径、system-logs MCP source 名称和本地诊断脚本兼容。

## Capabilities

### New Capabilities

- `local-runtime-observability`: 本地 API/Web/cloudflared/IM/LLM 运行健康、日志降噪、结构化错误分类和窗口化故障统计。

### Modified Capabilities

- `agent-runtime`: Agent loop 的 `no_progress_hint` 可观测行为从逐次刷屏收敛为聚合/节流日志。
- `channels`: Telegram poller 失败日志增加结构化错误类别、渠道上下文和退避状态。

## Impact

- 后端日志：API access log 过滤/采样、Agent runtime progress 日志、Telegram poller 日志、LLM client 错误日志。
- 运维诊断：`system-logs` MCP、`scripts/log-triage.sh` 和 `docs/ops/local-healthcheck.md` 可继续读取现有日志路径，并受益于结构化字段。
- 运行行为：LLM 429 需要明确退避/降级状态；不改变 Agent 协议、聊天 API、CodeAgent Workspace 或 IM 附件处理语义。
- 兼容性：保留 `.local/logs/api.log`、`.local/logs/web.log`、`.local/logs/cloudflared.log` 和 `im_events` source 名称；真实 DB 表仍为 `im_event_logs`。
