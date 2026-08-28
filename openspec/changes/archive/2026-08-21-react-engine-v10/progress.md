# Progress — react-engine-v10

记录实际完成情况。勾选 `tasks.md` 前的落地依据（代码 + 测试 + 回归）。

## 状态总览

- **tasks.md 进度**：20 / 20（6 组全部完成）
- **阶段**：A–F 全部完成
- **回归**：`python -m pytest tests/ -q` → **233 passed**（含新增 `test_react_engine_v10.py` 20 项）

## 完成记录

### 阶段 A — R1 规划前置验证（1.1–1.3）
- **1.1 / 1.2** `runtime.py`：新增 `_PLAN_ACTION_PREFIXES`/`_PLAN_ACTION_RE`/`_plan_mentioned_actions`/`_plan_preflight_hints`；`_apply_plan` 解析后逐条 `cm.add_coach_hint(...)`（越权→「本 agent 无 `<action>` 权限」；空→「PLAN 为空」；多行未解析→「未解析出子任务」）。
- **1.3** `_apply_plan` 为同步函数（`inspect.iscoroutinefunction == False`），preflight 纯本地零 LLM。
- **测试** 6.1：`test_preflight_*` + `test_apply_plan_preflight_is_local_no_llm`。

### 阶段 B — R2 工具结果去重复用（2.1–2.4）
- **2.1** `agent_tools.py`：`read_cache_key`/`_norm_cmd`/`dedup_descriptor`（READ→`read\x00<rel>`+mtime；SHELL→`shell\x00<规范化命令>`；SEARCH→`search\x00<query>`）。
- **2.2** `runtime.py`：`_DEDUP_TOOL_LABEL`/`_dedup_entry`/`_dedup_entry_valid`/`_dedup_hit_pointer`；工具执行循环在 `else` 分支先查 `state.query_cache`，命中→回显「该结果已缓存」指针并跳过 `execute_action`。
- **2.3** 无需改 `context_manager.py`（指针为短文本，`push_tool_result` 已透传）——见 findings F1。
- **2.4** `system_prompt.py`：追加「去重复用」说明（引用缓存、不重读/重跑）。
- **测试** 6.2：`test_dedup_descriptor_*` / `test_dedup_entry_valid_*` / `test_dedup_hit_pointer_text`。

### 阶段 C — R3 去重结果内容失效（3.1–3.2）
- **3.1** READ 条目带 `mtime`（`dedup_descriptor` 读目标文件 `st_mtime`）；`_dedup_entry_valid` 比较 mtime 不一致→失效。
- **3.2** `runtime.py` `file_write` 分支：`state.query_cache.pop(read_cache_key(path), None)` 使同路径 READ 缓存失效。
- **测试** 6.2：`test_dedup_descriptor_read_mtime_fingerprint` / `test_write_invalidation_key_matches_read_dedup_key`。

### 阶段 D — R4 完成度复核交付物证据（4.1–4.2）
- **4.1** `runtime.py`：`_deliverable_evidence(paths, sandbox, max_lines=20, max_chars=1500)` 读 `saved_paths` 各文件前 N 行；`_reflect_final` prompt 拼入「交付物内容摘要」块（`getattr(ctx, "sandbox", None)` 防御式访问）。
- **4.2** 纯裁判：仅文件读（无工具、无额外 LLM 轮），`_REFLECT_FAIL_CONVERGE` 未改动。
- **测试** 6.3：`test_deliverable_evidence_*` / `test_reflect_final_prompt_includes_deliverable_evidence`。

### 阶段 E — R5 可重试 HTTP 状态码退避重试（5.1–5.4）
- **5.1 / 5.2** `llm_client.py`：`_retryable_status_attempts`（529/429→3、502/503/504→5、其余→0）；`_post_with_transport_retry` 捕获 `httpx.HTTPStatusError`，retryable 用固定预算 + `2*(2**attempt)` 指数退避 + jitter，确定性失败（400/401/2013/1026/1027）`raise` 直通调用方。
- **5.3** `format_llm_http_error`：补 529/429/502/503/504 文案（含「已自动退避重试」）。
- **5.4** group failover 代码未改；`docs/react-engine.md` §5.5 记录 transport 重试 + 「绑 group 获得多模型降级」。
- **测试** 6.4：`test_retryable_status_attempts` / `test_format_llm_http_error_transient_messages` / `test_chat_completion_retries_retryable_http_status`（529/429/502/503/504 次数上限）/ `test_chat_completion_no_retry_deterministic_status`（400 直通）。

### 阶段 F — 测试与回归（6.1–6.5）
- **6.1–6.4** 新增 `tests/test_react_engine_v10.py`（20 项，全绿）。
- **6.5** `python -m pytest tests/ -q` → **233 passed**；无新增循环门禁/静态阈值（铁律保持）。
