# Progress — react-engine-v9

记录实际完成情况。勾选 `tasks.md` 前的落地依据（代码 + 测试 + 回归）。

## 状态总览

- **tasks.md 进度**：13 / 13（全部完成）
- **阶段**：A（R1）✓  B（R2）✓  C（R3）✓  D（测试回归）✓
- **回归**：`pytest tests/ -q` → 213 passed；`pytest tests/test_react_engine_v9.py -q` → 8 passed

## 完成记录

### 阶段 A — R1 无依赖同轮批量引导

- **1.1** `system_prompt.py::build_tools_desc` 「【重要】」行改写为「无依赖的独立步骤可同轮输出多个工具调用（如多个 READ/SEARCH、多个独立 SHELL、一次 describe 多个 view）；一旦下一步依赖上一步结果，就停下来等观察后再继续（有依赖仍分轮）」。依据见 F3（措辞强化）。
- **1.2** `runtime.py` 3 处 coach hints：「请调用一个工具继续」→「请调用工具（无依赖可同轮多个）继续」；「调用一个工具推进任务」→「调用工具（无依赖可同轮多个）推进任务」；「调用一个具体工具继续推进」→「调用工具（无依赖可同轮多个）继续推进」。依据见 F2（保留「二选一」/「单独一轮」）。
- **1.3** 确认 `for step in tool_steps`（`runtime.py`）已串行执行同轮多工具，无引擎改动。

### 阶段 B — R2 大结果落盘回显

- **2.1** `runtime.py` 新增 `_LARGE_RESULT_CHARS=4000`、`_LARGE_RESULT_PREVIEW_LINES=10`、`_materialize_tool_result()`（返回 `(ctx_text, dumped_path|None)`），在 `push_tool_result` 前调用；复用 `run_ts`/`sandbox`，落盘到 `workplace_root/task/<run_ts>/<action>_result_<seq>.txt`。
- **2.2** 无功能改动（`push_tool_result` 已可接收回显短串），由 4.2/4.4 透传覆盖。依据见 F1。
- **2.3** `system_prompt.py` 大结果落盘说明改为覆盖 READ/SHELL + MCP：「READ/SHELL 结果过大（超 4000 字符）或 MCP 查询返回超大结果时……上下文只回显『路径 + 前几行预览 + 总长度』；请用 READ/SEARCH 按需取回」。
- **2.4** 确认 `file_search` 不落盘（`action not in ("file_read","shell")` 直接原样返回），SEARCH 已有 200 条 / 6000 字符 cap。

### 阶段 C — R3 token 估算安全余量

- **3.1** `llm_client.py::fit_messages_to_context` 返回改为 `allowed_out = max(256, min(out_cap, ctx - used - 256 - max(512, used // 10)))`。依据见 F5。

### 阶段 D — 测试与回归

- **4.1** `tests/test_react_engine_v9.py::test_tools_desc_prompts_batch_and_forbids_single` + `test_coach_hint_prompts_batch_not_single` — 断言含「无依赖可同轮」、不含「一次只输出一个工具」/「调用一个」。
- **4.2** `test_materialize_over_threshold_dumps_and_echoes` / `test_materialize_under_threshold_unchanged` / `test_materialize_search_never_dumps` / `test_materialize_read_dumps_to_read_result` — 落盘 + 预览 + 长度回显、未超阈值原文、SEARCH 不落盘、按需取回引导。
- **4.3** `test_allowed_out_shaves_safety_margin` — `allowed_out < 8192`。
- **4.4** `test_long_task_context_stays_bounded` — 20 轮大 shell 落盘后回显，`fit_messages_to_context` 总估计 < 30000 且 `allowed_out > 0`。
- **4.5** 全量回归 213 passed；无新增静态门禁 / 循环阈值（落盘为软性上下文卫生，非硬门禁）。
