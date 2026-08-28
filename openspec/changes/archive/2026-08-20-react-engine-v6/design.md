# Design — react-engine-v6

## Context

动机见 `proposal.md`「Why」。三个独立分支，铁律贯穿：

- **铁律**：无任何静态硬门禁。本 V6 的「传输重试加强」「传输/400 区分计数」都是对**外部 LLM API** 的容错，不构成循环门禁；「传输连续 3 次 / 400 连续 2 次终止」是既有 `llm_failures` 机制的精化（LLM 错误本就在四类结束事件之内），不新增循环级阈值。
- TG 入站：`TelegramAdapter.handle_webhook`（`telegram.py:272-318`）只读 `text`/`caption`（`:279`），`not text` 即 `skip_agent=True`（`:293`）；出站 `send_document`（`:378-410`）是单向「发文件」，无对应「收文件」。
- 传输重试：`_post_with_transport_retry`（`llm_client.py:688-716`）3 次、`0.5/1/2s`、无抖动；`ConnectError` 是 `httpx.TransportError` 子类会走该重试；耗尽后 `raise RuntimeError("LLM 传输失败 …")`（`:714-716`），无端点信息。
- 传输/400 现状：统一进 `llm_failures`（`runtime.py:844-848`），连续 2 次终止，`continue` 用相同上下文重发。
- SQL 现状：`system_prompt.py:192-193` 已引导「批量输出互不依赖调用」，但措辞弱；引擎一轮可串行执行多条 MCP（`runtime.py:1027-1073`）；无「先写 SQL 清单再回放」的两阶段机制。

## Goals / Non-Goals

**Goals:**
- TG document 入站下载并交给 Agent（复用 `READ:`/`SHELL:` 与 workspace 落盘）。
- 传输错误跨过常见抖动窗口。
- 传输 vs 400 区分退避，报错可排障。
- 提示词强制同轮批量独立 SQL。

**Non-Goals:**
- 不新增循环门禁 / 阈值（铁律）。
- 不做 `photo`/`voice`/`video`/`audio` 下载（Q-TG1=B）。
- 不上 SQL 清单回放 / 依赖回填（Q-SQL1=A / Q-SQL2=A）。
- 不改 MCP 源端、不新增协议关键字。

## Decisions

### D1: TG 仅 document（Q-TG1=B）

只解析 `message["document"]["file_id"]`；`photo`/`voice`/`video`/`audio` 不处理、不报错。document（Excel/CSV/文本）是数据类 Agent 的核心诉求；图片 OCR、语音 ASR 属独立重活，先不做。

### D2: 落 sandbox workspace + 文本注入（Q-TG2=A）

`getFile` 拿 `file_path` → 下载到 `download_path(sandbox_id, ...)` → `inbound.text` 追加「附件已下载：`<path>`」。复用现有 `READ:`/`SHELL:` 协议与 workspace 落盘，Agent 侧零感知（只是多一行路径文本）。
- **为何**：改动最小、不引入新链路；`InboundMessage` / `message_meta` 结构不变。

### D3: 纯附件触发（Q-TG3=A）

去掉 `telegram.py:293` 的 `not text → skip`；纯 document 消息把「附件已下载：`<path>`」作为唯一文本触发 Agent。

### D4: 传输重试 5 次 + 退避 + 抖动（Q1=A）

`_post_with_transport_retry` 改为 5 次、退避 `2/4/8/16s`、带随机抖动；仍只在单请求内、仅 `httpx.TransportError` 触发，HTTP 状态错误不走此重试。
- **为何**：真实网络抖动通常 >3.5s，5 次 + 更长退避才能跨过；抖动避免多实例同时重试造成的 thundering herd。

### D5: 传输 vs 400 区分（Q2=A）

运行时区分「传输错误计数」与「400 计数」：传输错误连续 3 次终止、400 连续 2 次终止；报错文案带 `llm.base_url`。实现上需让循环能区分异常来源（如自定义 `LLMTransportError` / `LLMHTTPError` 子类，或在现有 `RuntimeError` 上带类型标记）。
- **为何**：传输错误可自愈、不该和参数性 400 一样快判死；端点信息让排障可见。

### D6: SQL 提示词强化（Q-SQL1=A）

在 `system_prompt.py` 取数效率引导附近，把「批量输出互不依赖调用」升级为强制措辞「互不依赖的 SQL 必须同一轮批量输出，禁止一轮一条」。
- **为何**：零代码快赢；真正的 SQL 清单落盘 + 回放需新协议 + 回放阶段，收益不明确，暂不做。

### D7: 依赖型 SQL 不工程化（Q-SQL2=A）

不引入「上一步输出 → 模板填入下一步参数」；依赖型查询保持「观察后再继续」（`system_prompt.py:193` 已有此引导）。
- **为何**：符合单循环 ReAct 哲学，避免流水线化风险。

## Risks / Trade-offs

- [风险] 纯附件触发可能让 Agent 收到「只有文件、无任务说明」的输入而不知做什么。→ 缓解：路径注入行 + 既有系统提示引导模型先 `READ` 文件再判断。
- [风险] 20MB 下载失败 / 超限。→ 缓解：`getFile` 前查 `file_size`，超限报错；下载失败记日志并提示。
- [风险] 传输重试 5 次 + 长退避可能拖慢「快速失败」场景。→ 缓解：仅传输错误触发；HTTP 400 不重试仍快速失败。
- [取舍] D5 引入第二个计数器（传输失败计数）。→ 缓解：它是 `llm_failures` 的精化、非循环门禁，触发即终止属「LLM 错误」结束事件之一。

## Open Questions

- TG 下载：扩展名白名单具体范围、`caption` 是否作为首条文本、`getFile` 下载复用哪个 `httpx.AsyncClient`（timeout 取值）。
- 传输重试：抖动范围、`resolved_timeout` 是否同步调整、退避是否封顶。
- 传输 vs 400：传输失败计数与 `llm_failures` 的数据结构 / 复位条件、异常类型如何携带区分标记。
- SQL 提示词具体措辞与放置位置。
