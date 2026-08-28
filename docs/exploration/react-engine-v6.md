# React Engine v6 — 需求整理（OpenSpec 需求输入）

> 本文是 grill-me 阶段产出的**需求输入**，用于进入 OpenSpec 之前对齐范围与验收口径。
> 只陈述「要解决什么问题、怎么判定做对了」，不写实现细节；实现方案由后续 OpenSpec 产出。
>
> 背景驱动：三条独立问题——① TG 收到的消息中文件不能下载；② LLM 推理第 44 轮 `ConnectError` 传输失败；③ 计划阶段先完成 SQL 是否降低轮次。
>
> **状态：需求已锁定**（三支 grilling 全收敛：连接 A/A、TG B/A/A、SQL A/A）。

---

## 0. 铁律（最高优先级，贯穿全部需求）

> **无任何静态硬门禁。**
>
> - 不新增阈值 / 计数器 / 确定性强制停止。
> - 循环只由四类事件结束：① 模型输出 `FINAL:`/`done`；② 用户取消；③ LLM 错误；④ `max_iters` 预算耗尽。
> - 所有「纠正」走软 LLM 判断 + coach hint，不硬停。
> - 能删的冗余代码一律删除（删除优先于新增）。
>
> **沿用 V5 边界（Q3=A）**：「有界重试」是对**外部 LLM API** 的有限容错，不是循环门禁；重试上限只作用于单次 `chat_completion` 请求，不参与循环内工具决策。本 V6 的传输重试加强、传输/400 区分计数，均为外部 API 容错，不引入任何循环级计数器 / 阈值 / 硬停。

---

## 1. 已确认需求（4 条：R1–R4）

### R1 — TG 入站 document 文件下载并交给 Agent

**问题**：`TelegramAdapter.handle_webhook`（`telegram.py:272-318`）只读 `text`/`caption`（`:279`），纯附件消息在 `:293-294` 被 `skip_agent=True` 静默丢弃；全仓库零 `getFile` 调用、零 `api.telegram.org/file/...` 下载、零 `file_id` 解析——入站附件下载是**功能缺失**，不是 bug。

**确认口径（Q-TG1=B / Q-TG2=A / Q-TG3=A）**：

- **范围（Q-TG1=B）**：仅支持 `document`（Excel/CSV/文本等 Agent 能直接读的文件）；`photo`/`voice`/`video`/`audio` 暂不做。
- **落盘与交接（Q-TG2=A）**：解析 `message["document"]["file_id"]` → 调 `getFile` 拿 `file_path` → 下载到 sandbox workspace（`download_path(sandbox_id, ...)`）→ 在 `inbound.text` 追加「附件已下载：`<path>`」，Agent 用现有 `READ:`/`SHELL:` 处理。
- **纯附件触发（Q-TG3=A）**：无 caption 的纯 document 消息也启动 Agent，文件路径作为唯一输入（改动 `telegram.py:293` 的 `not text` 丢弃逻辑）。

### R2 — LLM 传输重试加强

**问题**：`_post_with_transport_retry`（`llm_client.py:688-716`）仅 3 次、退避 `0.5/1/2s`、无抖动，总等待 ~3.5s，跨不过一次真实的网络抖动（DNS 抖动 / 代理重启 / 服务端瞬时不可达）。

**确认口径（Q1=A）**：

- 加强为 **5 次 + 指数退避 `2/4/8/16s` + 随机抖动**；仍只在单请求内 `_post_with_transport_retry` 完成，不改主循环。

### R3 — 传输错误与 400 区分处理

**问题**：传输错误（环境性、可自愈）与 400（参数性、需修复）当前统一进 `llm_failures`，连续 2 次即终止（`runtime.py:844-848`）；`ConnectError('')` 报错不含端点，排障看不出是哪个 base_url 挂了。

**确认口径（Q2=A）**：

- **区分计数**：传输错误单独计数、更宽容——连续 **3 次**传输失败才终止；400 仍按 `llm_failures=2` 终止。
- **报错带端点**：传输失败 / 400 的报错文案带上端点信息（`llm.base_url`），便于排障。

### R4 — SQL 降轮次提示词强化

**问题**：引擎已支持「一轮输出多条 `MCP:` 行 + 同轮串行执行」（`runtime.py:1027-1073`），且已有「批量输出互不依赖调用」的引导（`system_prompt.py:192-193`），但不够强制，模型可能一轮一条。而「先写完整 SQL 清单再脱离 LLM 回放执行」的两阶段机制当前不存在（SQL 文本不落盘；`mcp_result_*.json` 是结果不是待执行清单）。

**确认口径（Q-SQL1=A / Q-SQL2=A）**：

- **纯提示词强化（Q-SQL1=A）**：收紧 system prompt——「互不依赖的 SQL 必须同一轮批量输出，禁止一轮一条」；**不上** SQL 清单落盘 + 回放机制。
- **依赖型 SQL 不工程化（Q-SQL2=A）**：下一条依赖上一条结果的 SQL，保持「观察后再继续」，符合单循环 ReAct 哲学；不引入「上一步输出 → 模板填入下一步参数」的依赖回填。

---

## 2. 关键架构决策

| # | 决策 | 说明 |
|---|------|------|
| D1 | TG 仅 document | photo/voice/video 暂不做（Q-TG1=B） |
| D2 | 落 sandbox workspace + 文本注入 | `getFile`→下载→`inbound.text` 追加路径（Q-TG2=A） |
| D3 | 纯附件触发 Agent | 改动 `telegram.py:293` 丢弃逻辑（Q-TG3=A） |
| D4 | 传输重试 5 次 + 退避 + 抖动 | `2/4/8/16s` + jitter，单请求内（Q1=A） |
| D5 | 传输 vs 400 区分计数 | 传输连续 3 次终止、400 连续 2 次终止（Q2=A） |
| D6 | SQL 提示词强化 | 强制同轮批量独立 SQL，无新机制（Q-SQL1=A） |
| D7 | 依赖型 SQL 不工程化 | 保持观察后再继续（Q-SQL2=A） |

---

## 3. 边界条件

| 边界 | 约束 |
|------|------|
| 铁律 | 无任何静态硬门禁；有界重试只作用于单请求，不新增循环级计数器/阈值/硬停 |
| TG 下载上限 | `getFile` 下载 ≤ 20MB（Bot API 硬限），超限报错并提示 |
| TG 文件类型 | 仅 `document`；`photo`/`voice`/`video`/`audio` 保持现状（不下载、不处理、不报错） |
| TG 交接 | 路径注入 `inbound.text` 追加「附件已下载：`<path>`」；纯附件也触发 |
| 传输重试 | 单请求内 5 次、退避 `2/4/8/16s`、带抖动；仅 `httpx.TransportError` 触发 |
| 传输 vs 400 | 传输错误连续 3 次终止；400 连续 2 次终止（沿用 `llm_failures`）；报错带端点 |
| SQL 降轮次 | 仅提示词强化，不新增协议关键字 / SQL 清单 / 回放阶段 / 依赖回填 |
| 依赖型 SQL | 保持「观察后再继续」，不引入变量插值 |

---

## 4. Acceptance Criteria

1. **R1**：TG `document` 消息经 `getFile` 下载到 sandbox workspace，`inbound.text` 追加文件路径，Agent 可 `READ` 该路径；纯 document（无 caption）消息触发 Agent；`photo`/`voice`/`video` 行为不变（不处理、不报错）。
2. **R2**：传输错误重试 5 次、退避 `2/4/8/16s`、带抖动；仍只在单请求内，主循环逻辑不变。
3. **R3**：传输错误连续 3 次才终止 run；400 仍连续 2 次终止；报错文案含端点信息。
4. **R4**：system prompt 含「互不依赖 SQL 同轮批量、禁止一轮一条」的强制措辞；不新增 SQL 清单/回放/依赖回填机制。
5. **回归**：纯文本 TG 消息行为不变；非传输错误路径不变；无新增循环门禁 / 静态阈值。

---

## 5. 遗留项（交给 OpenSpec 定实现，非需求分歧）

1. TG 下载：`getFile` 与下载请求复用哪个 `httpx.AsyncClient`（现有 `_bot_api` timeout=30）、文本行格式、扩展名白名单、是否顺带读 `caption` 作为首条文本。
2. 传输重试：抖动范围、`resolved_timeout` 是否同步调整、退避是否封顶。
3. 传输 vs 400：新的传输失败计数与 `llm_failures` 如何共存（数据字段 / 复位条件）、端点从 `llm.base_url` 取。
4. SQL 提示词：具体措辞、放在 `system_prompt.py` 哪个位置（`mcp_reachable` 分支的取数效率引导附近）。

---

## 6. 相关文档

- V5（MiniMax 2013 形状硬化 + 降级重试）：[`react-engine-v5.md`](react-engine-v5.md)
- V4（R1–R7，role:tool 原生回填 / 去重 / 进度回显）：[`react-engine-v4.md`](react-engine-v4.md)
- 引擎内部地图（现状）：[`../react-engine.md`](../react-engine.md)
