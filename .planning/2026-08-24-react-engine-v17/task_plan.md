# Task Plan: react-engine-v17（OpenSpec 执行计划）

<!--
  本文件是 planning-with-files 的执行计划，把 OpenSpec tasks.md 映射为执行 phases。
  ⚠️ 正式任务来源 = openspec/changes/react-engine-v17/tasks.md（唯一，勾选以它为准）。
  ⚠️ 验收标准来源 = openspec/changes/react-engine-v17/specs/agent-runtime/spec.md（WHEN/THEN/AND）。
  ⚠️ 需求输入 = docs/exploration/react-engine-v17.md；动机/范围 = proposal.md；设计决策 = design.md。
  本文件不重新定义需求/任务，只做 phase 分组 + 状态跟踪 + 决策/错误记录。
  每完成一个 OpenSpec task：先更新 progress.md → 确认代码+测试完成 → 再勾选 tasks.md。
-->

## Goal

按 OpenSpec change `react-engine-v17` 的 `tasks.md` 完成空 HTTP 200 软空回、`finish_reason=length` 请求级续写、截断阻断 FINAL、残缺 tool_calls 不执行，并满足 specs 验收与回归绿。

## Next Step

已归档到 `openspec/changes/archive/2026-08-24-react-engine-v17/`；主 spec `agent-runtime` 已同步。

## Current Phase

Phase 4（complete）

## Phases

### Phase 1: llm_client — ChatResult 与空响应软路径（tasks 1.1–1.3）

映射自 `openspec/changes/react-engine-v17/tasks.md` §1。

- 1.1 扩展 `ChatResult` 增加 `output_truncated: bool = False`；验证字段可被 runtime 读取
- 1.2 `extract_chat_response_text`：空 content / 无可执行 tool_calls 时返回 `""`，不再 raise；验证纯空与 reasoning-only 均不抛错
- 1.3 `_build_chat_result`：只保留可映射的 tool_calls；不可映射或 `finish_reason=length` 的 tool_calls 清空且不执行；验证单测覆盖
- **Status:** complete

### Phase 2: llm_client — 输出预算与请求级管道（tasks 2.1–2.6）

映射自 `openspec/changes/react-engine-v17/tasks.md` §2。

- 2.1 `fit_messages_to_context` 增加 `min_allowed_out`；重试路径 ≥2048
- 2.2 叶子管道：无可执行产出 → 抬预算重试 1 次 → 软空返回
- 2.3 `finish_reason=length|max_tokens` 最多续写 2 次并拼接
- 2.4 续写用尽仍 length → `output_truncated=True`
- 2.5 无 length 类 finish_reason 不启发式续写
- 2.6 模型组空 200 不换员；真错误仍 failover
- **Status:** complete

### Phase 3: runtime — 截断阻断 FINAL（tasks 3.1–3.2）

映射自 `openspec/changes/react-engine-v17/tasks.md` §3。

- 3.1 `_run_modular` 在 FINAL / 完成信号前检查 `output_truncated`；coach 后 `continue`
- 3.2 下一轮完整 FINAL 仍可正常结束
- **Status:** complete

### Phase 4: 测试与回归（tasks 4.1–4.3）

映射自 `openspec/changes/react-engine-v17/tasks.md` §4。

- 4.1 新增 `tests/test_react_engine_v17.py` 覆盖核心场景
- 4.2 跑指定 pytest 套件全绿
- 4.3 确认铁律 / reasoning_split / llm_failures 行为
- **Status:** complete

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| （执行中产生的新决策记录于此；设计决策见 design.md D1–D6） | |

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| （执行中产生） | | |

## Notes

- 勾选纪律：`progress.md` 记录 → 代码+测试确认 → 才改 `tasks.md` `- [ ]` → `- [x]`
- 不在本文件重写 WHEN/THEN；验收以 specs 为准
- 工作区可能已有 WIP 实现，仍须按 task 逐项验证后再勾选，不得批量空勾
