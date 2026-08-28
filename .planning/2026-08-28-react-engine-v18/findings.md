# Findings: react-engine-v18

## Pointers（需求不写在本文件）

- 范围 / Why：`openspec/changes/react-engine-v18/proposal.md`
- 设计 D1–D4：`openspec/changes/react-engine-v18/design.md`
- 行为契约：`openspec/changes/react-engine-v18/specs/agent-runtime/spec.md`
- 正式任务清单：`openspec/changes/react-engine-v18/tasks.md`

## During execution

- Apply 发生在 Planning Files 初始化之前：`runtime.py` / `loop_state.py` / `test_react_engine_v18.py` 与既有夹具已在磁盘上；本目录只做 execution map，不重新定义需求。
- 证据门会改变「PLAN 含 `[ ]` 且同轮 FINAL」的旧收尾语义；`test_plan_only_nudge.py` / `test_final_semantics.py` 要收尾的场景已改为 `[x]`。`test_react_engine_v5.py` 收敛夹具子任务改为 `done`，否则永远进不了 `_reflect_final`。
- `reflect_fail_count` 按 design D3 仍为进程内变量，不写入 checkpoint；只有 `fix_only_until_final` 持久化。
- WatchFiles 对 `apps/api/app/services/agent_runtime/runtime.py` 嵌套路径经常不 reload；对话验证前必须整进程重启 API。
- `.planning/.active_plan` 原先指向 `2026-08-26-code-agent-runner-sop-claude`；本 init 已切换到 `2026-08-28-react-engine-v18`，不把本 change 折进 SOP 计划。

## Open questions

None — `design.md` Decisions 已覆盖证据门、空话 PASS、修复期 checkpoint、计数清零；Non-Goals 排除独立裁判 / 第一次 FAIL 即停 / Code Profile。
