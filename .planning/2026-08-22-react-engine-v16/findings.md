# Findings & Decisions

<!-- 记录执行中发现的问题/事实。需求与设计决策见 OpenSpec change，不在此重复。 -->

## Requirements

需求唯一来源：
- 需求输入：`docs/exploration/react-engine-v16.md`
- OpenSpec specs（验收标准 WHEN/THEN/AND）：`openspec/changes/react-engine-v16/specs/agent-runtime/spec.md`
- OpenSpec tasks（唯一正式任务）：`openspec/changes/react-engine-v16/tasks.md`

（完成信号软转换 / 需求感知完成度复核 / 已完成清单注入 / MCP 工具调用去重与大结果取回指引 —— 细节不在此重述。）

## Research Findings

- **F1（loop_state 新字段缺口）**：`loop_state.py` 现有 `saved_paths / progress_lines / subtasks / query_cache / no_progress_streak`，但缺 v16 需新增的两个字段——① `completion_signal_streak`（完成信号疑似计数，task 1.2）；② `tool_call_tally`（已尝试工具摘要，task 3.3 清单来源之一）。均非门禁字段。
- **F2（完成信号软转换插桩点）**：`runtime.py` 纯文本分支以 `text_only_streak`（行 858、1064–1115）跟踪连续纯文本轮次，是完成信号检测的落点；`_apply_no_progress_hint`（642，v15 R1′ 破局提示）与 `_distill_final`（561）为相邻机制，须避免重复注入。转换后的 FINAL 候选去处为 `_reflect_final`（488）。
- **F3（_reflect_final 现状）**：`_reflect_final` prompt 现写死「排版、措辞、小瑕疵一律 PASS」（行 508–509），且 goal 截 1500 字（`goal[:1500]`）。task 2.1/2.2 需改为「现场按 goal 逐条拆 checklist、逐项判 PASS/FAIL，字段口径/数字/映射必须精确，仅排版措辞豁免」。
- **F4（MCP 工具去重现状缺口，已修正）**：执行时发现 MCP 去重**并非**完全缺失——`McpSessionManager._dedup_key`（`mcp_client.py`）已对**所有** MCP 工具做「mcp_id + 工具名 + `json.dumps(args, sort_keys=True)`」去重（与 `state.query_cache` 同引用）。真正缺口是 **`execute_ads_sql` 的 SQL 归一化**：同一 SQL 的空白/大小写/尾分号差异会产生不同 key（对应 31% 精确重复）。故 Phase 4 只改 `_dedup_key` 加 SQL 归一化特例，并校准命中回显措辞，未新增第二套 MCP 去重。

## Technical Decisions

（执行中产生的新决策记录于此；已锁定的设计决策 D1–D8 见 `openspec/changes/react-engine-v16/design.md`。）

| Decision | Rationale |
|----------|-----------|
| ④ MCP 去重范围：所有 MCP 工具「工具名+归一化参数」去重（`execute_ads_sql` 的 SQL 归一化为特例） | 用户确认；已同步回写 spec/proposal/tasks/design |
| ① 完成信号计数：新增独立 `completion_signal_streak`，仅疑似完成声明时自增（与 `text_only_streak` 并存） | 默认 C（未异议） |
| ② SQL 归一化：去空白/大小写/尾分号；LIMIT/OFFSET 不同视为不同（不归一化字符串字面量、不去注释——对齐 spec.md 最终措辞） | 已按 spec 落地 |
| ③ 已尝试工具摘要体量：工具名+调用次数+最后结果路径，上限 ~12 行，截头留尾 | 默认（未异议） |

## Issues Encountered

| Issue | Resolution |
|-------|------------|
| | |

## Resources

- `openspec/changes/react-engine-v16/`（proposal / design / specs / tasks）
- `docs/exploration/react-engine-v16.md`（需求输入，根因复盘）
- `docs/react-engine.md`（引擎内部地图，现状）
- v15 归档：`openspec/changes/archive/2026-08-22-react-engine-v15/`
