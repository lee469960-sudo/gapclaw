# Progress — react-engine-v6

记录实际完成情况。勾选 `tasks.md` 前的落地依据（代码 + 测试 + 回归）。

## 状态总览

- **tasks.md 进度**：15 / 15（5 组，全部完成）
- **阶段**：A→E 全部完成，全量回归绿

## 完成记录

### 1.x TG 入站 document 下载（R1，阶段 A）

- **代码**：`apps/api/app/services/channels/telegram.py`（`download_document_to_workspace` + `handle_webhook` 纯 document 触发）、`apps/api/app/services/channels/runtime.py`（`process_inbound` 下载注入）
- **测试**：`test_download_document_to_workspace`、`test_download_document_oversize_rejected`、`test_pure_document_triggers_agent`、`test_non_document_attachment_still_skipped`
- **回归**：绿

### 2.x LLM 传输重试加强（R2，阶段 B）

- **代码**：`apps/api/app/services/llm_client.py` `_post_with_transport_retry`（5 次 + 2/4/8/16s 退避 + 抖动）
- **测试**：`test_transport_retry_five_attempts_backoff_jitter`
- **回归**：绿

### 3.x 传输 vs 400 区分（R3，阶段 C）

- **代码**：`apps/api/app/services/llm_client.py`（`LLMTransportError`/`LLMHTTPError` + 报错带端点）、`apps/api/app/services/agent_runtime/runtime.py`（`transport_failures` 阈值 3 vs `llm_failures` 阈值 2）
- **测试**：`test_transport_errors_stop_after_three`、`test_http_errors_stop_after_two`
- **回归**：绿

### 4.x SQL 降轮次提示词强化（R4，阶段 D）

- **代码**：`apps/api/app/services/agent_runtime/system_prompt.py`（取数效率引导加「互不依赖的 SQL / 查询必须同一轮批量输出，禁止一轮一条」）
- **测试**：无新增单测（软提示词）；由全量回归覆盖不破坏现有断言
- **回归**：绿

### 5.x 测试与回归（阶段 E）

- **全量回归**：`176 passed`（含 7 个新 v6 单测）
- **无新增循环门禁 / 静态阈值**：`transport_failures`/`llm_failures` 是既有「LLM 错误」结束事件的精化（见 findings F2）
