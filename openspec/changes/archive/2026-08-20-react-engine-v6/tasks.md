## 1. TG 入站 document 下载（R1）

- [x] 1.1 `telegram.py` 解析 `message["document"]["file_id"]`，调用 `getFile` 拿 `file_path`（复用 `_bot_api`）
- [x] 1.2 `telegram.py` 下载文件到 sandbox workspace（`download_path`），下载前校验 20MB 上限
- [x] 1.3 `telegram.py` 在 `inbound.text` 追加「附件已下载：<path>」；纯 document（无 caption）也触发 Agent（改 `:293` 丢弃逻辑）
- [x] 1.4 非 document 附件（photo/voice/video/audio）行为不变：不下载、不处理、不报错

## 2. LLM 传输重试加强（R2）

- [x] 2.1 `llm_client.py` `_post_with_transport_retry` 改为 5 次 + 指数退避 `2/4/8/16s` + 随机抖动
- [x] 2.2 仍仅 `httpx.TransportError` 触发、单请求内；HTTP 状态错误路径不变

## 3. 传输 vs 400 区分（R3）

- [x] 3.1 `runtime.py` 区分传输错误计数与 400 计数：传输连续 3 次终止、400 连续 2 次终止（异常需带类型标记）
- [x] 3.2 `llm_client.py` 传输失败 / 400 报错文案带端点（`llm.base_url`）

## 4. SQL 降轮次提示词强化（R4）

- [x] 4.1 `system_prompt.py` 取数效率引导处，强化「互不依赖 SQL 同轮批量、禁止一轮一条」
- [x] 4.2 不新增 SQL 清单 / 回放 / 依赖回填机制

## 5. 测试与回归

- [x] 5.1 TG document 下载 + 路径注入单测（mock `getFile` + 下载）
- [x] 5.2 纯 document 触发 Agent 单测
- [x] 5.3 传输重试 5 次 + 退避 + 抖动单测
- [x] 5.4 传输 vs 400 区分终止阈值单测
- [x] 5.5 全量测试回归绿；无新增循环门禁 / 静态阈值
