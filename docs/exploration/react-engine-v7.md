# React Engine v7 — 需求整理（OpenSpec 需求输入）

> 本文是 grill-me 阶段产出的**需求输入**，用于进入 OpenSpec 之前对齐范围与验收口径。
> 只陈述「要解决什么问题、怎么判定做对了」，不写实现细节；实现方案由后续 OpenSpec 产出。
>
> 背景驱动：三条独立问题——① 通过 MCP 获取数据时，相同 SQL 期望在一个轮次内取全，降低 LLM 轮次；② 输出文件完成统计后按附件方式发送；③ 查询时选择最优方案，提高效率、降低用户等待时间。
>
> **状态：需求已锁定**（grilling 全收敛：Q1=B / Q2=A / Q3=A / Q4=A / Q5=A / Q6=A / Q7=A / Q8=A / Q9=A）。

---

## 0. 铁律（最高优先级，贯穿全部需求）

> **无任何静态硬门禁。**
>
> - 不新增阈值 / 计数器 / 确定性强制停止。
> - 循环只由四类事件结束：① 模型输出 `FINAL:`/`done`；② 用户取消；③ LLM 错误；④ `max_iters` 预算耗尽。
> - 所有「纠正」走软 LLM 判断 + coach hint，不硬停。
> - 能删的冗余代码一律删除（删除优先于新增）。
>
> 本 V7 三条需求全部落在**软提示词 + 协议标注 + 参数透出**上，不引入任何循环级计数器 / 阈值 / 硬停；「取全」「最优方案」均为对模型的软引导，模型仍有完全自由裁量。

---

## 1. 已确认需求（3 条：R1–R3）

### R1 — 相同 SQL 一轮取全（提示词驱动分页）

**问题**：MCP 查询结果可能被源端截断（只回前 N 行），模型要再花一轮用 offset 补齐，拉高轮次。而当前工具目录只暴露一个通用 `mcp_tool_call` meta 工具（`system_prompt.py:338-346`），文本目录 `_format_mcp_tools_for_prompt`（`utils.py:81-101`）只输出工具名 + 80 字符描述 + `required` 字段名，**非 required 的 offset/limit 对模型不可见**；执行结果（`mcp_client.py:909-944`）也**无 total / has_more / truncated 信号**，引擎无从判断是否还有下一页。引擎已支持「一轮串行执行多条 `MCP:`」（`runtime.py:1077-1083`），这是同轮分页的地基。

**确认口径（Q1=B / Q5=A）**：

- **提示词驱动（Q1=B）**：**不引入引擎自动翻页**（无分页契约、无取全上限）；由模型在提示词引导下**同一轮**发出多条带不同 offset 的调用补齐。
- **工具支持 offset/limit（Q5=A）**：提示词引导「结果若被截断（返回行数 ≈ 你设的 limit），同轮用 offset 补齐剩余页」；并**在工具目录透出 offset/limit 参数**（辅助小改动：把 MCP 工具的 `inputSchema` 非 required 分页参数透给模型，使其知道能分页——非引擎翻页）。

### R2 — 输出文件按附件发送（显式标注 + TG 附件）

**问题**：后端已有一套出站附件链——`channels/runtime.py:364-399` 在 `run_agent` 后调 `format_im_completion_reply`（`reply.py:48-65`）自动挑「**一个**最新、根目录、xlsx/csv 类」文件 → `adapter.send_document`（TG 已实现，`telegram.py:435-468`，45MB 上限）。但模型**无法指定**发哪个文件；且 `saved_paths` 只捕获 `WRITE:` 的「已写入」返回串（`runtime.py:1132-1139`），**SHELL/pandas 直接落盘的文件不进 `saved_paths`**（追踪盲区）——统计产出（多为 SHELL 写盘）因此发不出去。

**确认口径（Q2=A / Q6=A / Q8=A / Q9=A）**：

- **仅 TG（Q2=A）**：复用 `send_document`，web 端暂不做文件流。
- **模型显式标注（Q6=A）**：新增协议——模型在 `FINAL:` 标注 `attach=<path1,path2>`（或独立 `ATTACH:` 行）；引擎按路径 `download_path(sandbox_id, rel)` 解析后逐个 `send_document`。显式路径**绕过** `saved_paths` 的 SHELL 盲区（模型在 SHELL 命令里就知道自己写了哪个路径）。
- **显式优先 + 自动兜底（Q8=A）**：模型标了 `attach` 就发标定的；未标则保留现有「挑一个最新文件」逻辑。
- **多附件（Q9=A）**：支持逗号分隔多路径或多个 `ATTACH:` 行，逐个发送。

### R3 — 查询时选最优方案（提示词强化）

**问题**：现 system prompt 完全没有 WHERE/LIMIT、避免全表扫描、按等待时间选最优查询的显式引导；最近的只有【取数效率】（`system_prompt.py:225-227`）里「优先用日期区间/聚合谓词一次取全」，偏「减少往返」而非「查询谓词优化」。

**确认口径（Q3=A / Q4=A / Q7=A）**：

- **纯提示词（Q3=A）**：不引入方案评估 / 成本模型等结构性机制。
- **指标 = 总等待时间（Q4=A）**：等待 ≈ 轮次数 × 每轮耗时，综合降轮次 + 降单次查询数据量/耗时。
- **新增独立【查询效率】段（Q7=A）**：显式写入 WHERE/LIMIT、避免全表扫描、聚合优先（COUNT/SUM 在 SQL 侧完成）、按总等待时间选最优查询。

---

## 2. 关键架构决策

| # | 决策 | 说明 |
|---|------|------|
| D1 | 提示词驱动分页 | 不引入引擎自动翻页，无契约/无上限（Q1=B） |
| D2 | 透出 offset/limit | 工具目录把 MCP `inputSchema` 分页参数透给模型（Q5=A） |
| D3 | 同轮补齐引导 | 提示词「截断 → 同轮 offset 补齐」（Q5=A） |
| D4 | 仅 TG 附件 | 复用 `send_document`，web 端暂不做（Q2=A） |
| D5 | 显式 attach 协议 | `FINAL:` 附 `attach=` / `ATTACH:` 行，模型指定文件（Q6=A） |
| D6 | 显式优先 + 兜底 | 标了发标定的；未标保留「挑最新文件」（Q8=A） |
| D7 | 多附件 | 逐个 `send_document`（Q9=A） |
| D8 | 纯提示词最优方案 | 无方案评估/成本模型（Q3=A） |
| D9 | 指标 = 总等待时间 | 综合降轮次 + 降数据量/耗时（Q4=A） |

---

## 3. 边界条件

| 边界 | 约束 |
|------|------|
| 铁律 | 无任何静态硬门禁；三条需求均为软提示词 + 协议 + 参数透出，无循环级计数器/阈值/硬停 |
| 分页 | 不新增引擎自动翻页；分页完全由模型在提示词下自主发起，引擎仅同轮串行执行（既有能力） |
| 参数透出 | 透出 MCP 工具的 `inputSchema` 分页参数（offset/limit/page），不改变 MCP 源端 |
| 附件渠道 | 仅 TG；web 端行为不变 |
| 附件协议 | `attach=<path1,path2>` 或 `ATTACH:` 行；路径经 `download_path` 防穿越解析 |
| 附件兜底 | 未标注时保留现有「挑一个最新根目录 xlsx/csv」逻辑，行为向后兼容 |
| 附件大小 | TG Bot `sendDocument` 45MB 硬限（现有约束，不新增） |
| 查询效率 | 仅提示词强化，不新增协议关键字 / 方案评估 / 成本模型 |

---

## 4. Acceptance Criteria

1. **R1**：system prompt 含「结果被截断时同轮用 offset 补齐」引导；MCP 工具目录向模型透出 offset/limit 等分页参数；引擎仍一轮串行执行多条 `MCP:`（既有能力），无引擎自动翻页逻辑。
2. **R2**：模型在 `FINAL:` 标注 `attach=<path>`（或 `ATTACH:` 行）时，引擎按路径逐个 `send_document` 发出；支持多路径；未标注时保留现有「挑最新文件」兜底；仅 TG 生效。
3. **R3**：system prompt 新增独立【查询效率】段，含 WHERE/LIMIT、避免全表扫描、聚合优先、按总等待时间选最优查询的显式引导。
4. **回归**：未标注 `attach` 的 TG 回复行为不变；纯文本/短任务路径不变；无新增循环门禁 / 静态阈值。

---

## 5. 遗留项（交给 OpenSpec 定实现，非需求分歧）

1. **分页**：透出分页参数的实现位置（`_format_mcp_tools_for_prompt` 还是 `build_tool_schemas` 的 meta 工具 description）、「截断」如何让模型感知（`[N 行]` shape hint 的措辞）、提示词具体措辞与位置。
2. **附件协议**：`attach=` 内联在 `FINAL:` 还是独立 `ATTACH:` 行、解析位置（`_final_text` / FINAL 分支 / `channels/runtime.py`）、多路径分隔符与路径白名单、与现有 `format_im_completion_reply` 的兜底合并方式。
3. **附件盲区**：是否顺带补强 SHELL 落盘追踪（`_capture_deliverable_paths` 扩大扫描范围/白名单）作为 `attach` 之外的兜底——R2 已用显式标注绕过，此项为可选的后续加固。
4. **查询效率**：【查询效率】段具体措辞、是否置于 `mcp_reachable` 分支内（现有【取数效率】被 `if mcp_reachable:` 包裹）。

---

## 6. 相关文档

- V6（TG 入站下载 / 传输重试 / 传输-400 区分 / SQL 降轮次）：[`react-engine-v6.md`](react-engine-v6.md)
- V5（MiniMax 2013 形状硬化 + 降级重试 + token 估算）：[`react-engine-v5.md`](react-engine-v5.md)
- V4（R1–R7，role:tool 原生回填 / 去重 / 进度回显）：[`react-engine-v4.md`](react-engine-v4.md)
- 引擎内部地图（现状）：[`../react-engine.md`](../react-engine.md)
