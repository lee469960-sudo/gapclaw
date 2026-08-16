# system-logs MCP 工具说明

| 工具 | 参数 | 说明 |
|------|------|------|
| `list_log_sources` | 无 | 列出 api/web/cloudflared/im_events |
| `tail_log` | `source`, `lines`, `grep` | 尾部读取 |
| `search_log` | `source`, `query`, `level`, `minutes`, `limit` | 关键字/级别搜索 |
| `log_stats` | `source`, `minutes` | 计数与分布 |

环境变量：

- `LOG_DIR`：日志目录（默认仓库 `.local/logs`）
- `DATABASE_URL`：可选，sqlite 时支持 `im_events`
