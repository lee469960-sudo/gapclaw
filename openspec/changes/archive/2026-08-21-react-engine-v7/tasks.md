## 1. R1 — MCP 分页透出 + 同轮补齐（agent-runtime）

- [x] 1.1 `utils.py` `_format_mcp_tools_for_prompt` / `system_prompt.py` `build_tool_schemas` 透出 MCP 工具 `inputSchema` 的分页参数（offset/limit/page/page_size，含非 required）
- [x] 1.2 `system_prompt.py` 引导「结果被截断（行数 ≈ limit）则同轮用 offset 补齐剩余页」
- [x] 1.3 确认无引擎自动翻页逻辑（不注入 offset、不新增续拉循环）

## 2. R2 — 交付附件标注引导（agent-runtime）

- [x] 2.1 `system_prompt.py` 引导模型在 `FINAL:` 标注 `attach=<path>`（或独立 `ATTACH:` 行）

## 3. R3 — 查询效率提示词（agent-runtime）

- [x] 3.1 `system_prompt.py` 新增独立【查询效率】段：WHERE/LIMIT、避免全表扫描、聚合优先、按总等待时间选最优查询
- [x] 3.2 不新增方案评估 / 成本模型等结构性机制

## 4. R4 — TG 附件发送（channels）

- [x] 4.1 `channels/runtime.py` 解析 `attach=`（或 `ATTACH:` 行）→ `download_path` 规范化（防穿越）→ 逐个 `send_document`
- [x] 4.2 显式优先 + 兜底：未标注 `attach` 时保留现有「挑最新文件」逻辑
- [x] 4.3 非 TG 渠道行为不变（web / 未实现 `send_document` 的 IM 不发附件）

## 5. 测试与回归

- [x] 5.1 工具目录透出分页参数单测
- [x] 5.2 `attach` 协议解析单测（单路径 / 多路径 / 防穿越 / 未标注）
- [x] 5.3 TG 多附件发送 + 兜底单测
- [x] 5.4 提示词含【查询效率】段与分页引导断言
- [x] 5.5 全量回归绿；无新增循环门禁 / 静态阈值
