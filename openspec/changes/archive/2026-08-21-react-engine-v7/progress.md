# Progress — react-engine-v7

记录实际完成情况。勾选 `tasks.md` 前的落地依据（代码 + 测试 + 回归）。

## 状态总览

- **tasks.md 进度**：14 / 14（5 组，全部完成）
- **阶段**：A→E 全部完成，全量回归绿（190 passed）

## 完成记录

### 1.x MCP 分页透出 + 同轮补齐（R1，阶段 A）

- **代码**：`utils.py`（`_PAGINATION_PARAM_KEYS` + `_tool_pagination_params` + `_format_mcp_tools_for_prompt` 追加 `分页参数=[...]`）、`system_prompt.py`（`if mcp_reachable:` 内新增【分页补齐】引导）
- **测试**：`test_format_mcp_tools_surfaces_pagination_params`、`test_format_mcp_tools_no_pagination_when_absent`
- **1.3 确认**：无引擎自动翻页（见 findings F4）

### 2.x 交付附件标注引导（R2，阶段 B）

- **代码**：`system_prompt.py`（base lines 新增【附件标注】引导 `attach=<路径1,路径2>` / `ATTACH:` 行）
- **测试**：`test_system_prompt_has_query_efficiency_pagination_attach`（断言 `attach=` 存在）

### 3.x 查询效率提示词（R3，阶段 C）

- **代码**：`system_prompt.py`（`if mcp_reachable:` 内新增独立【查询效率】段：WHERE/LIMIT、避免全表扫描、聚合优先、按总等待时间选最优）
- **3.2 确认**：无方案评估 / 成本模型等结构性机制（仅提示词软引导）

### 4.x TG 附件发送（R4，阶段 D）

- **代码**：`reply.py`（`parse_attach_paths` / `strip_attach_markers` / `resolve_attach_paths`）、`channels/runtime.py`（`process_inbound` 显式 attach 优先 + 未标注兜底）
- **测试**：`test_parse_attach_*`、`test_strip_attach_*`、`test_resolve_attach_paths_*`、`test_push_channel_documents_sends_multiple`、`test_format_im_completion_reply_fallback_picks_newest`

### 5.x 测试与回归（阶段 E）

- **新增单测**：`tests/test_react_engine_v7.py`（14 个，全绿）
- **全量回归**：`190 passed`（含 14 个新 v7 单测）
- **无新增循环门禁 / 静态阈值**：R1–R3 均为软提示词 + 协议标注 + 参数透出（见 findings F2）
