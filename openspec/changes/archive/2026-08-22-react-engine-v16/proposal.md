## Why

`[dba]` Agent 三次真实运行（session 89f73ff2，`max_iterations=200`）暴露四类轮次浪费，逐轮复盘根因（详见 `docs/exploration/react-engine-v16.md`）：

1. **空转 94 轮**：模型在 103 轮输出 FINAL 被 Verifier 拒绝后，**再也不输出 `FINAL:`/`done`**，107→200 轮反复写「任务已完结/最终交付 xlsx」自然语言。引擎只认协议行，没有「完成信号→结束」识别。
2. **瑕疵漏检**：「渠道名称没有映射」在两轮 Verifier 拒绝后仍漏过。`_reflect_final` 把 goal 截到 1500 字、写死「小瑕疵一律 PASS」，不逐条核对 16 列口径。
3. **重新定位浪费**：resume 后 13 轮反复 `awk/cat` 重读 `build_report.py`、`ls mcp_result_*.json`。无「已完成清单」注入。
4. **SQL 精确重复 31%**：`execute_ads_sql` 86 次 → 27 次精确重复（主查询同一 SQL 跑 16 次）。去重未覆盖 SQL 执行；大结果截断后无「已落盘 path，请 READ 取回」指针。

## What Changes

- **[agent-runtime] 完成信号软转换**：正向完成声明 → 连续 2 轮 + LLM 确认 → 当 FINAL 送 Verifier，治空转。
- **[agent-runtime] 需求感知完成度复核**：现场按 goal 拆 checklist 逐条核对，数据口径必须精确，FAIL 只定向补，治瑕疵漏检 + 过度拒绝。
- **[agent-runtime] 已完成清单注入**：引擎零 LLM 拼清单每轮回显，清单永不裁，治重新定位。
- **[agent-runtime] MCP 工具调用去重与大结果取回指引**：所有 MCP 工具「工具名+归一化参数」去重（`execute_ads_sql` 为 SQL 归一化特例）+ 大结果落盘回显取回路径，治 SQL 重复。

## Capabilities

### Modified Capabilities

- `agent-runtime`: 新增「完成信号软转换」「需求感知完成度复核」「已完成清单注入」「MCP 工具调用去重与大结果取回指引」。

## Impact

- `apps/api/app/services/agent_runtime/runtime.py`（完成信号检测与软转换、`_reflect_final` 逐条核对 + 定向补、已完成清单注入、MCP 工具去重接线）
- `apps/api/app/services/agent_runtime/loop_state.py`（完成信号疑似计数；已完成清单字段）
- `apps/api/app/services/agent_runtime/context_manager.py`（task_context 稳定层回显清单；清单豁免裁剪）
- `apps/api/app/services/agent_tools.py` / `agent_runtime/utils.py`（MCP 工具「工具名+归一化参数」key 与 query_cache 接入）
- `apps/api/app/services/agent_runtime/system_prompt.py`（Verifier 逐条核对引导、完成声明需 `FINAL:` 的软提示）
- `apps/api/tests/`（新增单测）
