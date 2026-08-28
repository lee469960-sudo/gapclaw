# Design — react-engine-v7

## Context

动机见 `proposal.md`「Why」与 `docs/exploration/react-engine-v7.md`。铁律贯穿：

- **铁律**：无任何静态硬门禁。本 V7 三条需求全部落在**软提示词 + 协议标注 + 参数透出**上，不引入任何循环级计数器 / 阈值 / 硬停；「取全」「最优方案」均为对模型的软引导，模型仍有完全自由裁量。
- MCP 现状：工具只暴露 `mcp_tool_call` meta 工具（`system_prompt.py:338-346`），文本目录 `_format_mcp_tools_for_prompt`（`utils.py:81-101`）只显示 name + 80 字符描述 + `required` 字段；执行结果（`mcp_client.py:909-944`）无 total / has_more / truncated；一轮多 `MCP:` 串行已支持（`runtime.py:1077-1083`）。
- 附件现状：`channels/runtime.py:364-399` 在 `run_agent` 后调 `format_im_completion_reply`（`reply.py:48-65`）自动挑「一个最新、根目录、xlsx/csv 类」文件 → `adapter.send_document`（TG 已实现，`telegram.py:435-468`，45MB）；`saved_paths` 只捕获 `WRITE:` 返回串（`runtime.py:1132-1139`），SHELL 落盘是盲区。
- 提示词现状：无 WHERE/LIMIT / 避免全表扫描 / 选最优查询的措辞；最接近的【取数效率】（`system_prompt.py:225-227`）被 `if mcp_reachable:` 包裹。

## Goals / Non-Goals

**Goals:**
- 模型在同轮内用 offset 补齐被截断的 MCP 结果（透出分页参数 + 提示词引导）。
- 模型能显式指定「哪个产出文件」作为附件，经 TG `send_document` 发给用户（多附件 + 兜底）。
- 提示词显式引导「选最优查询」以降低总等待时间。

**Non-Goals:**
- 不新增引擎自动翻页（无分页契约、无取全上限）。
- 不改 MCP 源端、不新增协议关键字以外的结构性机制。
- 不做 web 端文件流（Q2=A，仅 TG）。
- 不上方案评估 / 成本模型（Q3=A）。

## Decisions

### D1: 分页走提示词，不引擎翻页（Q1=B）

不引入引擎自动翻页、无分页契约、无取全上限。分页完全由模型在提示词下自主发起，引擎仅按既有能力同轮串行执行多条 `MCP:`。
- **为何**：引擎层看不到 MCP 工具的分页参数、结果也无 total/has_more，通用自动翻页不可行；提示词驱动零引擎复杂度。

### D2: 透出 offset/limit（Q5=A）

工具目录把 MCP 工具 `inputSchema` 的非 required 分页参数（offset/limit/page 等）透给模型，使其知道能分页。
- **为何**：当前目录只显示 `required` 字段，模型根本不知道工具支持分页，提示词引导也无从谈起。

### D3: 同轮补齐引导（Q5=A）

系统提示引导「结果若被截断（返回行数 ≈ 所设 limit），同一轮用 offset 补齐剩余页」。

### D4: attach 显式标注协议（Q6=A）

系统提示引导模型在 `FINAL:` 标注 `attach=<path1,path2>`（或独立 `ATTACH:` 行）；引擎从最终回复解析交付路径列表。显式路径**绕过** `saved_paths` 的 SHELL 盲区（模型在 SHELL 命令里就知道自己写了哪个路径）。

### D5: 显式优先 + 兜底（Q8=A）

模型标了 `attach` 就发标定的；未标则保留现有「挑一个最新文件」逻辑，向后兼容。

### D6: 多附件（Q9=A）

支持逗号分隔多路径 / 多个 `ATTACH:` 行，逐个 `send_document`。

### D7: 仅 TG（Q2=A）

复用 `send_document`；非 TG 渠道行为不变（web 端暂不做文件流）。

### D8: 纯提示词查询效率 + 独立段（Q3=A / Q7=A）

新增独立【查询效率】段，显式写入 WHERE/LIMIT、避免全表扫描、聚合优先、按总等待时间选最优查询；无结构性机制。

### D9: 指标 = 总等待时间（Q4=A）

等待 ≈ 轮次数 × 每轮耗时；综合降轮次 + 降单次查询数据量/耗时。

### D10: attach 解析落在 channels 边界

`attach=` 的解析与发送放在 `channels/runtime.py`（复用 `format_im_completion_reply` 的后处理位置），不改变 `run()` 的纯文本返回值；`agent-runtime` 只负责系统提示引导标注格式。
- **为何**：现有 `channels/runtime.py` 已在 `run_agent` 后后处理 reply + saved_paths，此处解析 `attach` 最贴实现、改动最小。

## Risks / Trade-offs

- [风险] 透出完整 `inputSchema` 可能把无关参数也塞给模型，增大上下文。→ 缓解：只透出分页相关参数（offset/limit/page/page_size/cursor），不全量展开。
- [风险] 提示词驱动的分页仍依赖模型自觉，可能漏补。→ 缓解：这是 Q1=B 的既定取舍；引擎不翻页，漏补时下一轮仍可补。
- [风险] `attach` 路径可能指向不存在/越界文件。→ 缓解：`download_path` 既有 `_path_under_root` 防穿越 + `find_files` 回退；解析失败记日志并落兜底。
- [风险] 多附件逐个 `send_document` 可能触发 TG 速率限制。→ 缓解：附件数通常 ≤2-3；必要时串行 + 小间隔（OpenSpec 遗留项）。
- [取舍] V7 与 V6 都扩展 `channels`。→ 缓解：按 V6 → V7 顺序 apply，两者 ADDED 需求无重叠冲突。

## Open Questions

- 分页透出：只透出分页参数还是全量 `inputSchema`；「截断」如何让模型感知（`[N 行]` shape hint 的措辞）；提示词位置。
- attach 协议：`attach=` 内联 `FINAL:` 还是独立 `ATTACH:` 行；多路径分隔符与路径白名单；与 `format_im_completion_reply` 兜底的具体合并。
- 附件盲区：是否顺带补强 SHELL 落盘追踪（`_capture_deliverable_paths` 扩大扫描/白名单）作为 attach 之外的兜底（V7 已用显式标注绕过，此项可选）。
- 查询效率：【查询效率】段具体措辞；是否置于 `mcp_reachable` 分支内。
