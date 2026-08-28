# Progress — react-engine-v4（实际完成情况）

记录每个 OpenSpec task 的实际完成状态。约定：勾选 `tasks.md` 前必须先满足该任务的 acceptance criteria（代码 + 测试就绪），本文件是勾选前的核对记录。

## 状态总览

- 已勾选（前序会话）：1.1–1.5, 2.1–2.4, 3.1–3.2, 4.1–4.2, 5.1–5.6, 8.1–8.4
- 本会话已完成：1.6, 1.7, 6.1, 6.2, 6.3, 7.1, 7.2, 8.5, 8.6, 8.7, 8.8

## 逐任务核对

### 1.6 `_materialize` 去 `>6000` 门禁 ✅
- 代码：`call_tool` 所有非空结果执行 `_materialize`（写盘 + 入 cache）；大结果（> `large_result_chars`）回传引用、小结果内联（findings F1）。见 `mcp_client.py:910`。
- 测试：`test_mcp_full_materialize.py::test_small_result_materializes_and_keeps_inline` + `test_oversized_result_still_returns_reference`。

### 1.7 落盘摘要补「元素个数/行数」 ✅
- 代码：`_result_shape`（`mcp_client.py`）；`mcp_results`/`query_cache` 条目与 `_cached_reference`、大结果引用消息补 shape。
- 测试：`test_result_shape_list_and_dict` + `test_same_query_hits_cache_with_path_and_shape`（断言 `[3 项]` 出现在命中消息）。

### 6.1 确认 `_dedup_key` 规范化一致 ✅
- 验证：已确认满足（findings F3），无改码。
- 测试：`test_dedup_key_ignores_key_order` + `test_dedup_key_values_participate`。

### 6.2 命中 cache 回传 path ✅
- 验证：已确认（findings F4），1.7 补 shape。
- 测试：`test_same_query_hits_cache_with_path_and_shape`（断言 `mcp_result_0.json` 在命中消息中）。

### 6.3 仅 MCP 去重，RAG/httpmcp 预留接口 ✅
- 代码：`_dedup_key` 前加注释说明 `query_cache` 签名接口通用、RAG/httpmcp 可后续复用、暂不实现。
- 验证：代码审查。

### 7.1 重复命中/重发 PLAN 回显已落盘清单 ✅
- 代码：`_echo_materialized_manifest`（`runtime.py`）+ 两个触发点：工具成功分支去重命中（`_is_cached_reference`）与 `_apply_plan` 重发（`had_plan`）。
- 测试：`test_echo_manifest_*` + `test_apply_plan_echoes_only_on_reissue`。

### 7.2 复用 add_progress/add_coach_hint，不新增计数器 ✅
- 验证：`_echo_materialized_manifest` 仅用 `state.add_progress` + `cm.add_coach_hint`；无新计数器/阈值/硬停。

### 8.5–8.8 测试 ✅
- 8.5 `test_mcp_full_materialize.py`（≤6000 也落盘 + 入 cache）✅
- 8.6 `test_mcp_full_materialize.py`（相同签名命中、不同参数不命中、键序不敏感）✅
- 8.7 `test_progress_echo.py`（重复命中/重发 PLAN 回显）✅
- 8.8 全量 `python -m pytest tests/ -q` → **159 passed** ✅

## 回归修复记录

- `test_mcp_session_manager.py` 两处同参调用测试（`test_stdio_session_reused_across_calls`、`test_legacy_http_is_not_cached`）因去重扩展到小结果而改用不同参数，并 patch `workplace_root` 到 `tmp_path`（findings F2）。
