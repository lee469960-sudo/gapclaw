# Task Plan — react-engine-v10

> **任务唯一来源**：`tasks.md`（20 个 task）。本文件不重新定义需求，仅将 tasks.md 映射为可执行阶段，标注代码锚点与依赖。需求语义一律以 `specs/agent-runtime/spec.md` 与 `tasks.md` 为准。

## 阶段总览

| 阶段 | 需求 | tasks.md 条目 | 依赖 |
|------|------|--------------|------|
| A | R1 规划前置验证 | 1.1–1.3 | 无 |
| B | R2 工具结果去重复用 | 2.1–2.4 | 无 |
| C | R3 去重结果内容失效 | 3.1–3.2 | B（复用缓存形态 + 失效钩子） |
| D | R4 完成度复核交付物证据 | 4.1–4.2 | 无 |
| E | R5 可重试 HTTP 状态码退避重试 | 5.1–5.4 | 无 |
| F | 测试与回归 | 6.1–6.5 | A–E 全部 |

A / B / D / E 相互独立，可并行推进；C 依赖 B；F 收尾。

## 阶段 A — R1 规划前置验证（tasks 1.1–1.3）

- **锚点**：`runtime.py:1412 _apply_plan(ctx, state, cm, plan_text)`；`parse_subtasks` 来自 `tool_parser.py`。
- **落地**：
  - 1.1 在 `_apply_plan` 解析 `plan_text` 处（`parse_subtasks` 前后）加本地静态检查：提取带协议前缀的动作（`SHELL:`/`READ:`/`WRITE:`/`SEARCH:`/`MCP:` 等）→ 判是否 ∈ `allowed_actions`；并判计划空 / 不可解析。
  - 1.2 命中 → `cm.add_coach_hint(...)` 软提示（越权提示「本 agent 无 <action> 权限」；空计划提示「用带编号 / `[ ]` 清单格式」），不阻断、不 `chat_completion`。
  - 1.3 确认检查纯本地、无任何 LLM 调用（函数内同步完成）。
- **验收**：6.1 越权计划 → coach hint；权限内 → 无提示；零 LLM 调用（可用 mock 断言 `chat_completion` 未被调用）。

## 阶段 B — R2 工具结果去重复用（tasks 2.1–2.4）

- **锚点**：
  - 缓存容器 `loop_state.py:28 state.query_cache`（dict，`{dedup_key: {path, tool, size}}`）；MCP 已由 `McpSessionManager` 维护（`runtime.py:809 query_cache=state.query_cache`）。
  - MCP 命中指针形态：`mcp_client.py:1001 _cached_reference` →「该查询已缓存，结果见 …」；识别器 `runtime.py:1578 _is_cached_reference`（`startswith("该查询已缓存")`）。
  - READ/SEARCH 执行体：`agent_tools.py:347 _read_workplace`、`agent_tools.py:396 _search_workplace`。
- **落地**：
  - 2.1 `agent_tools.py` READ/SEARCH 返回结果带去重键标识（READ→路径，SEARCH→query），供 runtime 组键（或由 runtime 侧按 action+参数自行组键，锚定一处即可，不重复定义）。
  - 2.2 `runtime.py` 工具执行前查 `state.query_cache`（键 = action + 目标；用 `\x00` 分隔、以 action 词首段与 MCP 数字 mid 键隔离）；命中 → 回显「已缓存，请引用之前结果」指针，不重读/重跑/重入上下文。
  - 2.3 `context_manager.py` 接收/回显去重命中指针（`push_tool_result` 已可透传短串，命中指针直接作为 tool 结果入上下文）。
  - 2.4 `system_prompt.py` 加去重指针用法说明（引用缓存，不重读/重跑）。
- **验收**：6.2 重复 READ/SHELL/SEARCH → 指针且不重跑；首次 → 正常执行并写入缓存。

## 阶段 C — R3 去重结果内容失效（tasks 3.1–3.2）

- **锚点**：`runtime.py` file_write 执行段（`runtime.py:1141` 附近，v9 已在此处记 `state.saved_paths`）；README 缓存入口在阶段 B。
- **落地**：
  - 3.1 READ 结果写缓存时记「路径 + mtime/hash」（entry 增 `mtime`/`hash` 字段，仅对非超大文件算 hash）。
  - 3.2 `file_write` 命中同一路径 → 使该路径 READ 缓存失效（删 `read\x00<path>` 键）。
- **验收**：6.2 WRITE 后 → 缓存失效重读；未改写 → 命中指针不重读。

## 阶段 D — R4 完成度复核交付物证据（tasks 4.1–4.2）

- **锚点**：`runtime.py:475 _reflect_final(ctx, state, candidate)`；`runtime.py:51 _REFLECT_FAIL_CONVERGE = 3`；`runtime.py:955` 收敛分支。
- **落地**：
  - 4.1 `_reflect_final` 构建判定 prompt 时拼入 `state.saved_paths` 每个交付物的「前 N 行内容摘要」（N 温和，如 20；仅拼确实存在的文件）。
  - 4.2 不发起工具调用、不加 LLM 轮次、`_REFLECT_FAIL_CONVERGE=3` 与收敛逻辑不变。
- **验收**：6.3 交付物摘要进入 prompt；FAIL 依据内容而非候选文本。

## 阶段 E — R5 可重试 HTTP 状态码退避重试（tasks 5.1–5.4）

- **锚点**：
  - `llm_client.py:719 _post_with_transport_retry`（现有 transport 退避 2/4/8/16s + jitter）；`llm_client.py:707 resp.raise_for_status()` 抛 `httpx.HTTPStatusError`。
  - `llm_client.py:768-783` 现仅对 sensitive（1026/1027）做 slim 重试，其余直接 `raise LLMHTTPError`。
  - `llm_client.py:470 format_llm_http_error`；`llm_client.py:624-654 chat_completion` group failover。
- **落地**：
  - 5.1 将可重试状态码（529/429/502/503/504）接入指数退避重试：扩展 `_post_with_transport_retry`（或紧邻新增 sibling）捕获 `httpx.HTTPStatusError`，按状态判重试；复用 2/4/8/16s + jitter 骨架。
  - 5.2 529/429 → 3 次（约 2s→6s→18s）；5xx(502/503/504) → 5 次；400/401/2013/1026/1027 确定性失败不重试直接报错。
  - 5.3 `format_llm_http_error` 补 529/429/5xx 文案。
  - 5.4 group failover 代码不改（D6）；仅文档引导「Agent 绑 `type:"group"` 的 LLM 获得多模型降级」。
- **验收**：6.4 529/429/5xx 有 sleep + 次数上限；400/401/2013/1026/1027 直接报错。

## 阶段 F — 测试与回归（tasks 6.1–6.5）

- **锚点**：`apps/api/tests/` 新增 `test_react_engine_v10.py`（或按需拆分）。
- **落地**：
  - 6.1 前置验证单测；6.2 去重命中/失效单测；6.3 Verifier 证据单测；6.4 退避重试单测；6.5 全量 `pytest tests/ -q` 绿 + 无新增循环门禁/静态阈值。

## 记录约定

- 每完成一个 task：先更新 `progress.md`（代码 + 测试证据），再勾 `tasks.md`；不满足 acceptance criteria 不勾。
- 执行中发现的问题/张力/决策追加到 `findings.md`（F1、F2…）。
