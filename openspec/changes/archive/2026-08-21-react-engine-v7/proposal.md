## Why

三条独立问题，共同指向「降低 LLM 轮次 / 降低用户等待时间 / 交付物触达用户」：

1. **MCP 取数一轮取全**：相同 SQL 结果被源端截断时，模型要再花一轮用 offset 补齐。而 MCP 工具只暴露一个通用 `mcp_tool_call` meta 工具，工具目录 `_format_mcp_tools_for_prompt`（`utils.py:81-101`）只输出工具名 + 80 字符描述 + `required` 字段名，**offset/limit 等非 required 分页参数对模型不可见**；结果也无 total/has_more 信号。引擎已支持一轮串行执行多条 `MCP:`（`runtime.py:1077-1083`），这是同轮分页的地基。
2. **输出文件按附件发送**：后端已有一套出站附件链（`channels/runtime.py:364-399` → `format_im_completion_reply` `reply.py:48-65` → `adapter.send_document`），但它只自动挑「一个最新、根目录、xlsx/csv 类」文件，模型**无法指定**发哪个；且 `saved_paths` 只捕获 `WRITE:` 的「已写入」返回串（`runtime.py:1132-1139`），**SHELL/pandas 直接落盘的文件不进 `saved_paths`**（追踪盲区），统计产出因此发不出去。
3. **查询时选最优方案**：现 system prompt 无 WHERE/LIMIT、避免全表扫描、按等待时间选最优查询的显式引导（最近只有【取数效率】`system_prompt.py:225-227` 的「日期区间/聚合谓词一次取全」，偏减少往返而非谓词优化）。

## What Changes

- **R1 MCP 分页参数透出与同轮补齐引导**：工具目录透出 MCP 工具 `inputSchema` 的分页参数（offset/limit/page，含非 required 字段）；系统提示引导「结果被截断则同轮用 offset 补齐」。**不新增引擎自动翻页**（无契约、无上限）。
- **R2 交付附件标注引导**：系统提示引导模型在 `FINAL:` 标注 `attach=<path1,path2>`（或独立 `ATTACH:` 行）。
- **R3 查询效率提示词强化**：新增独立【查询效率】段——WHERE/LIMIT、避免全表扫描、聚合优先、按总等待时间选最优查询。
- **R4 TG 附件发送**：解析 `attach` 交付路径 → `download_path` 规范化 → 逐个 `send_document`；显式优先，未标注保留现有「挑最新文件」兜底；仅 TG。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `agent-runtime`: 新增「MCP 分页参数透出与同轮补齐引导」「交付附件标注引导」「查询效率提示词强化」三条需求。
- `channels`: 新增「TG 附件发送」一条需求（`channels` 由 react-engine-v6 引入，本 change 继续扩展）。

## Impact

- `apps/api/app/services/agent_runtime/system_prompt.py`（R1/R2/R3 提示词与透出）
- `apps/api/app/services/agent_runtime/utils.py`（R1 工具目录透出分页参数 `_format_mcp_tools_for_prompt`）
- `apps/api/app/services/channels/runtime.py`（R4 解析 `attach` → 发送）
- `apps/api/app/services/channels/reply.py`（R4 `format_im_completion_reply` 兜底合并）
- `apps/api/app/services/channels/telegram.py`（R4 多附件 `send_document`）
- `apps/api/tests/`（新增单测）
