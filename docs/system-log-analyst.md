# 系统日志分析 Agent

用 **log-analyst** Agent + **system-logs** MCP + **system-log-analyst** Skill，分析本机 GAP 运行日志并给出优化建议。

## 日志来源

| source | 文件 |
|--------|------|
| `api` | `.local/logs/api.log` |
| `web` | `.local/logs/web.log` |
| `cloudflared` | `.local/logs/cloudflared.log` |
| `im_events` | DB `im_event_logs`（需可访问的 SQLite/PostgreSQL `DATABASE_URL`） |

Agent **不能**直接读宿主机路径；必须通过 MCP 工具。

## 启动后自动 seed

API 启动时若 `SEED_SYSTEM_LOGS=true`（默认），会注册：

- MCP：`system-logs`（stdio：`python -m mcp_servers.system_logs`）
- Skill：`system-log-analyst`
- Agent：`log-analyst`（绑定上述 MCP/Skill）

关闭：在 `apps/api/.env` 设 `SEED_SYSTEM_LOGS=false`。

## 使用

1. 确保本地已跑过 `./start.sh`（产生 `.local/logs/`）
2. Web → Agents → 打开 **log-analyst**
3. 示例问法：
   - 「分析一下最近系统日志并给出优化建议」
   - 「api 最近有哪些 Traceback？」
   - 「看下 web.log 的代理错误」
   - 「统计最近的 component/class 错误分布」

## 结构化字段

运行日志保持 plain-text 格式，同时为常见故障追加稳定 key-value 字段：

| component | class 示例 | 含义 |
|-----------|------------|------|
| `llm` | `throttling` | LLM provider 429/529 限流或过载退避 |
| `llm` | `network_connectivity` | LLM transport/connectivity failure |
| `telegram` | `network_connectivity` / `timeout` / `polling_error` | Telegram poller 失败类别 |
| `agent_runtime` | `no_progress` | Agent loop 连续无进展聚合提示 |

`log_stats` 会额外返回 `structured_class_counts`，例如
`llm:throttling`、`telegram:network_connectivity`。旧日志没有结构化字段时该
字段为空，原有 `level_counts`、`http_status_counts`、`top_exceptions` 仍可用。

## 手动绑定（若 seed 未跑）

1. MCP 管理新增 stdio：
   - command：当前 API 的 Python 解释器
   - args：`["-m","mcp_servers.system_logs"]`
   - env：`LOG_DIR=<repo>/.local/logs`，`PYTHONPATH=<repo>/apps/api`，`DATABASE_URL=<GAP 数据库 URL>`
2. Skills 上传 `seed_assets/system-log-analyst`
3. Agent 绑定 MCP + Skill，开启 `mcp_tool_call` / `skill_read_md`
