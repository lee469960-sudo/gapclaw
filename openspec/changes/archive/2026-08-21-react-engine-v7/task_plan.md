# Task Plan — react-engine-v7

> **唯一正式任务来源**：`tasks.md`。本文件不重新定义需求，仅将 `tasks.md` 的 14 个任务映射为可执行的 execution phases，并标注每阶段触及的文件与验收要点（引用 tasks/specs，不新增约束）。

## 需求 → 阶段映射

| Requirement（spec） | Capability | Phase |
|---|---|---|
| R1 MCP 分页参数透出与同轮补齐引导 | agent-runtime | A |
| R2 交付附件标注引导 | agent-runtime | B |
| R3 查询效率提示词强化 | agent-runtime | C |
| R4 TG 附件发送 | channels | D |

## 执行阶段

### Phase A — MCP 分页透出 + 同轮补齐（tasks 1.1–1.3）
- **文件**：`apps/api/app/services/agent_runtime/utils.py`（`_format_mcp_tools_for_prompt`）、`apps/api/app/services/agent_runtime/system_prompt.py`（`build_tool_schemas` / 取数引导处）
- **任务**：
  - 1.1 工具目录透出 `inputSchema` 分页参数（offset/limit/page/page_size，含非 required）
  - 1.2 系统提示引导「结果被截断（行数 ≈ limit）则同轮 offset 补齐」
  - 1.3 确认无引擎自动翻页（不注入 offset、不新增续拉循环）
- **验收要点**：工具目录含分页参数且模型可见；提示含同轮补齐引导；无自动翻页逻辑（对应 spec agent-runtime「MCP 分页参数透出与同轮补齐引导」三 scenario）。

### Phase B — 交付附件标注引导（task 2.1）
- **文件**：`apps/api/app/services/agent_runtime/system_prompt.py`
- **任务**：
  - 2.1 引导模型在 `FINAL:` 标注 `attach=<path>`（或独立 `ATTACH:` 行）
- **验收要点**：提示含 attach 标注格式说明；未标注不强制、不报错（spec「交付附件标注引导」）。

### Phase C — 查询效率提示词（tasks 3.1–3.2）
- **文件**：`apps/api/app/services/agent_runtime/system_prompt.py`
- **任务**：
  - 3.1 新增独立【查询效率】段（WHERE/LIMIT、避免全表扫描、聚合优先、按总等待时间选最优查询）
  - 3.2 不新增方案评估 / 成本模型等结构性机制
- **验收要点**：提示含独立【查询效率】段；无评分/选优/拦截结构（spec「查询效率提示词强化」）。

### Phase D — TG 附件发送（tasks 4.1–4.3）
- **文件**：`apps/api/app/services/channels/runtime.py`、`apps/api/app/services/channels/reply.py`、`apps/api/app/services/channels/telegram.py`
- **任务**：
  - 4.1 解析 `attach=`（或 `ATTACH:` 行）→ `download_path` 规范化（防穿越）→ 逐个 `send_document`
  - 4.2 显式优先 + 兜底（未标注保留「挑最新文件」）
  - 4.3 非 TG 渠道行为不变
- **验收要点**：多附件逐个发送；显式优先 + 兜底；仅 TG（spec channels「TG 附件发送」三 scenario）。
- **依赖**：attach 协议格式由 Phase B（spec）确定。

### Phase E — 测试与回归（tasks 5.1–5.5）
- **文件**：`apps/api/tests/`
- **任务**：
  - 5.1 工具目录透出分页参数单测
  - 5.2 attach 协议解析单测（单路径/多路径/防穿越/未标注）
  - 5.3 TG 多附件发送 + 兜底单测
  - 5.4 提示词含【查询效率】段与分页引导断言
  - 5.5 全量回归绿；无新增循环门禁/静态阈值

## 依赖关系

```
A ─┐
B ─┼─→ D ──→ E
C ─┘
```

- A/B/C 均改 `system_prompt.py`（agent-runtime 提示词层），按 A→B→C 顺序落地避免同一文件反复改。
- D 依赖 B 的 attach 协议格式，但代码边界独立（channels 层），可紧随 B 完成。
- E 在 A–D 全部落地后执行；5.4 覆盖 A/B/C 的提示词，5.2/5.3 覆盖 D 的发送链。
