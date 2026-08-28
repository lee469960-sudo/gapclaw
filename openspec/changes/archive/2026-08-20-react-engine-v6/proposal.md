## Why

三个独立问题，共同指向「Agent 平台生产环境的可靠性与效率缺口」：

1. **TG 入站文件下载缺失**：`TelegramAdapter.handle_webhook`（`telegram.py:272-318`）只读 `text`/`caption`，纯附件消息在 `:293` 被 `skip_agent=True` 静默丢弃；全仓库零 `getFile` 调用、零 `api.telegram.org/file/...` 下载。用户通过 TG 发 Excel/CSV 无法进入 Agent——这是功能缺失，不是 bug。
2. **LLM 传输重试过弱**：`_post_with_transport_retry`（`llm_client.py:688-716`）仅 3 次、退避 `0.5/1/2s`、无抖动，总等待 ~3.5s，跨不过常见网络抖动（DNS 抖动 / 代理重启 / 服务端瞬时不可达）；第 44 轮 `ConnectError('')` 即因此放弃，且报错不含端点信息。
3. **SQL 降轮次提示词不够强制**：引擎已支持「一轮输出多条 `MCP:` 行 + 同轮串行执行」（`runtime.py:1027-1073`），提示词已有「批量输出互不依赖调用」引导（`system_prompt.py:192-193`）但措辞偏弱，模型可能一轮一条，浪费轮次。

## What Changes

- **R1 TG 入站 document 下载**：新增「解析 `file_id` → `getFile` 拿 `file_path` → 下载到 sandbox workspace → `inbound.text` 追加路径」链路；纯 document（无 caption）也触发 Agent；仅支持 `document`，`photo`/`voice`/`video`/`audio` 暂不做。
- **R2 LLM 传输重试加强**：单请求内重试 5 次、指数退避 `2/4/8/16s` + 随机抖动，仅 `httpx.TransportError` 触发，主循环不变。
- **R3 传输 vs 400 区分处理**：传输错误连续 3 次才终止、HTTP 400 连续 2 次终止；传输失败 / 400 报错文案带端点（`llm.base_url`）。
- **R4 SQL 降轮次提示词强化**：system prompt 强制「互不依赖 SQL 同轮批量、禁止一轮一条」；不上 SQL 清单落盘 / 回放 / 依赖回填机制。

## Capabilities

### New Capabilities

- `channels`: TG 入站 document 文件下载并交给 Agent。

### Modified Capabilities

- `agent-runtime`: 新增「LLM 传输重试加强」「传输错误与 400 区分处理」「SQL 降轮次提示词强化」三条需求。

## Impact

- `apps/api/app/services/channels/telegram.py`（`file_id` 解析 + `getFile` 下载 + 文本注入 + 纯附件触发）
- `apps/api/app/services/llm_client.py`（传输重试加强、报错带端点）
- `apps/api/app/services/agent_runtime/runtime.py`（传输 / 400 区分计数）
- `apps/api/app/services/agent_runtime/system_prompt.py`（SQL 批量输出强化）
- `apps/api/tests/`（新增单测）
