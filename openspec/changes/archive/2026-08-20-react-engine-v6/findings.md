# Findings — react-engine-v6（执行中记录）

记录执行过程中发现的问题、张力与决策。与 `progress.md` 互补：本文件记录「问题/决策」，`progress.md` 记录「完成事实」。

## F1: TG 下载落点与「telegram.py」归属的张力（R1）

- **发现**：`design.md` D2 写「下载到 `download_path(sandbox_id, ...)`」，但 `workplace.download_path(sandbox_id, rel)` 是**读**助手（把相对路径解析为绝对路径，供出站 `send_document` 用），不是入站写入目标。入站写入要用 `workplace.upload_file(sandbox_id, rel_dir, filename, content)`。
- **更关键**：`TelegramAdapter.handle_webhook`（`telegram.py`）拿不到 `sandbox_id`——它只持有 `channel_id` + `config`，而 `sandbox_id` 在 `Agent` 行上，只有 `channels/runtime.py::process_inbound` 在解析 agent 后才能拿到。且轮询路径 `telegram_poller._poll_once` 直接构造 `TelegramAdapter` 调 `handle_webhook`，**不经过** `dispatch_webhook`，若只在 `dispatch_webhook` 注入 sandbox_id 会漏掉轮询。
- **决策**：把「下载 + 路径注入」放在 `process_inbound`（webhook 与 poller 的共同咽喉，且已解析 `agent.sandbox_id`）；TG 专属的 `file_id → getFile → 下载字节 → 写 workspace` 逻辑留在 `telegram.py` 的模块函数 `download_document_to_workspace(token, document, sandbox_id)`。`handle_webhook` 只做「解析 + 纯 document 触发」（去掉 `not text → skip` 的误伤）。这偏离了 tasks 1.3「telegram.py 在 inbound.text 追加」的字面（追加由 process_inbound 用 helper 返回值完成），但保留了 spec 的行为要求（路径注入 inbound.text、Agent 可 READ），并符合 design 的「改动最小 / InboundMessage 结构不变」约束。

## F2: 传输/400 计数是 `llm_failures` 的精化，非新门禁（R3）

- **决策**：新增 `transport_failures`（阈值 3）与既有 `llm_failures`（阈值 2，涵盖 HTTP 400 等非传输错误）并列，二者仍属「LLM 错误」这一结束事件，不新增循环级硬门禁。异常类型标记用 `LLMTransportError` / `LLMHTTPError`（均继承 `RuntimeError`），运行时按 `isinstance` 分流。

## F3: 抖动实现（R2）

- **决策**：退避 `2/4/8/16s`，抖动取 `random.uniform(0, 0.25) * base`（上界 +25%），仅在 `httpx.TransportError` 触发、单请求内；HTTP 状态错误路径不变。

## F4: `edited_message` 下的 document 解析一致性（R1，verify 阶段发现并修复）

- **发现**：`handle_webhook` 用 `data.get("message") or data.get("edited_message")` 解析消息，但 `process_inbound` 最初只读 `raw["message"]`，导致「编辑消息（`edited_message`）携带 document」时下载被漏掉。
- **修复**：`process_inbound` 改为镜像 `handle_webhook` 的解析（`message || edited_message`），保证两条路径一致。
