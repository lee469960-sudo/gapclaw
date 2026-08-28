# Task Plan — react-engine-v4（剩余工作执行映射）

> 需求与验收口径的唯一正式来源是 `tasks.md` 与 `specs/agent-runtime/spec.md`。
> 本文件不重新定义需求，只把 `tasks.md` 中**尚未完成**的任务映射为可执行的实现阶段与验证方式。

## 剩余任务清单（来自 tasks.md）

| # | 任务 | 归属 | 验证方式 |
|---|------|------|----------|
| 1.6 | `_materialize` 去 `>6000` 门禁，所有非空结果落盘+入 cache | R1/R6/D10 | 单测 8.5 |
| 1.7 | 落盘摘要补「元素个数/行数」 | R1/D13 | 单测 8.5 |
| 6.1 | 确认 `_dedup_key` 与「规范化参数」一致 | R6/D11 | 单测 8.6 |
| 6.2 | 命中 cache 回传落盘 `path` | R6/D11 | 单测 8.6 |
| 6.3 | 仅 MCP 去重，RAG/httpmcp 预留统一接口 | R6 | 代码审查 |
| 7.1 | 重复命中/重发 PLAN 时回显已落盘清单 | R7/D12 | 单测 8.7 |
| 7.2 | 复用 add_progress/add_coach_hint，不新增计数器 | R7/D12 | 代码审查 |
| 8.5 | MCP 全量落盘单测（≤6000 也落盘+入 cache） | 测试 | 绿 |
| 8.6 | 查询去重软提示单测 | 测试 | 绿 |
| 8.7 | 进度回显单测 | 测试 | 绿 |
| 8.8 | 全量回归绿 + 无新增硬门禁 | 测试 | 绿 |

## 执行阶段

### Phase A — MCP 全量落盘 + 摘要（1.6 / 1.7）
- 文件：`apps/api/app/services/mcp_client.py`
- 动作：
  1. `call_tool` 去掉 `len(text) > large_result_chars` 条件：所有非空结果都调 `_materialize`（副作用：写盘 + 入 cache）。
  2. 返回语义：大结果（> `large_result_chars`）仍回传「已写入 path」引用；小结果仍回传原文（上下文内联不丢），落盘仅作持久化副作用（D10「`tool_result_clip` 截断不变，只影响展示」）。
  3. 新增 `_result_shape(text)` 提取「元素个数/行数」；`mcp_results` 条目与 `query_cache` 条目补 `shape` 字段；`_cached_reference` 消息补 shape。
- 关键张力：不得让「全量落盘」把小结果也替换成「请 READ」消息——那会丢失内联内容、让模型每轮多一次 READ 往返。

### Phase B — 去重签名 + 预留接口（6.1 / 6.2 / 6.3）
- 文件：`apps/api/app/services/mcp_client.py`
- 动作：
  1. 确认 `_dedup_key` 已满足「键保留、值参与、忽略空白/键序」（`json.dumps(sort_keys=True, ensure_ascii=False, default=str)` 已归一空白与键序，值参与签名）。
  2. 确认 `_cached_reference` 已回传 `path`（现有实现已含，1.7 补 shape）。
  3. 在 `_dedup_key`/`call_tool` 附近加注释：仅 MCP 参与去重；RAG/httpmcp 预留 `query_cache` 统一接口但暂不实现。

### Phase C — 进度回显（7.1 / 7.2）
- 文件：`apps/api/app/services/agent_runtime/runtime.py`
- 动作：
  1. 新增 helper `_echo_materialized_manifest(state, cm)`：把 `state.mcp_results`（tool → path）回显进 coach hint / progress，不新增计数器。
  2. 在「重复命中」（result_text 命中 `_cached_reference` 的缓存标记）与「重发 PLAN」（`_apply_plan` 被调用）两个触发点调用该 helper。
  3. 复用 `state.add_progress` / `cm.add_coach_hint`，不新增阈值/硬停。

### Phase D — 测试（8.5 / 8.6 / 8.7 / 8.8）
- 新增 `tests/test_mcp_full_materialize.py`（8.5 + 8.6）与 `tests/test_progress_echo.py`（8.7）。
- 修复受去重扩展影响的既有单测（`test_mcp_session_manager.py` 的同参复用测试需改用不同参数以避开去重命中）。
- 全量 `python -m pytest tests/ -q` 回归绿。
