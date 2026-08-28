# Findings — react-engine-v9（执行中记录）

记录执行过程中发现的问题、张力与决策。与 `progress.md` 互补：本文件记录「问题/决策」，`progress.md` 记录「完成事实」。

## F1 — task 2.2（context_manager 接收回显串）无需功能改动

`ContextManager.push_tool_result(tool_output, *, clip, action)` 本就把 `tool_output` 当作任意字符串处理：经 `label_map` 打标、超 `clip` 截断、`f"{label}:\n{content.strip()}"` 包装。落盘回显串「路径 + 前几行预览 + 总长度」就是一个普通短字符串，直接作为 `tool_output` 传入即可，`clip=6000` 不会触发截断。因此 2.2 无新增代码，由 4.2/4.4 的透传测试覆盖。

## F2 — task 1.2 保留「二选一」与「单独一轮」

1.2 的措辞改动只针对「一次只能输出一个工具」类约束（3 处 coach hints 已改）。「二选一」（进度 vs 收尾）与 FINAL+tool 的「单独一轮」是「进度态/收尾态」的语义约束，不是「一次一个工具」的同轮约束，故刻意保留，未改动。

## F3 — task 1.1 为措辞强化而非新增能力

`build_tools_desc` 原本已写有「可在一轮内输出 PLAN 与首个工具，或批量输出多个**互不依赖**的工具调用」，已具备批量引导。1.1 把它改写得更显式（「无依赖的独立步骤可同轮输出多个工具调用……有依赖仍分轮」），属措辞强化，非新语义。

## F4 — R2 落盘阈值与范围

- 阈值以模块常量 `_LARGE_RESULT_CHARS = 4000` 表达，预览行数 `_LARGE_RESULT_PREVIEW_LINES = 10`。
- 仅 `action in ("file_read", "shell")` 落盘；`file_search` 明确排除（task 2.4：SEARCH 已 cap 200 条 / 6000 字符，不落盘）。
- 落盘路径复用 `workplace_root(sid)/task/<run_ts>/<action>_result_<seq>.txt`，与 MCP `mcp_result_*.json` 同目录机制一致。
- 全量原文仍经 `cm.push_archive` 归档，上下文只推回显串。

## F5 — R3 安全余量取 `max(512, used // 10)`

`estimate_tokens`（≈ len/2）对符号密集的 shell/code 输出系统性低估，故 `allowed_out` 在固定 256 守护之外，再扣 `max(512, used//10)`。用 60×2000 字符的消息把 `used` 压到预算附近，可稳定观察到 `allowed_out < out_cap`（而非停留在 cap 上限）。
