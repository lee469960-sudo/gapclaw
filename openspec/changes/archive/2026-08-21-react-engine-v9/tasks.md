## 1. R1 — 无依赖同轮批量引导（agent-runtime）

- [x] 1.1 `system_prompt.py` 工具目录 / 协议说明加「无依赖独立步骤可同轮输出多个工具调用」
- [x] 1.2 `runtime.py` coach hints 措辞：把「调用一个工具」「二选一」「单独一轮」改为「无依赖可同轮多个」
- [x] 1.3 确认无引擎改动（`for step in tool_steps` 已支持同轮多工具）

## 2. R2 — 大结果落盘回显（agent-runtime）

- [x] 2.1 `runtime.py` 工具结果落盘判定（`push_tool_result` 前，复用 `run_ts`/`save_dir`，阈值 4000）
- [x] 2.2 `context_manager.py` 支持接收「已落盘回显」串（路径 + 预览 + 长度）
- [x] 2.3 `system_prompt.py` 落盘回显用法说明（按需 READ/SEARCH 取回）
- [x] 2.4 确认 SEARCH 不落盘（已 cap N 条）

## 3. R3 — token 估算安全余量（agent-runtime）

- [x] 3.1 `llm_client.py` `allowed_out` 追加安全余量

## 4. 测试与回归

- [x] 4.1 同轮批量提示词断言（含「无依赖可同轮」，不含「一次一个」）
- [x] 4.2 落盘回显单测（超阈值落盘+预览、未超阈值原文、SEARCH 不落盘、按需取回）
- [x] 4.3 `allowed_out` 安全余量单测
- [x] 4.4 长任务（多轮 shell/READ）不再触发 2013 回归
- [x] 4.5 全量回归绿；无新增循环门禁 / 静态阈值
