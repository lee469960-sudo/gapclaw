# Findings & Decisions

<!-- 记录执行中发现的问题/事实。需求与设计决策见 OpenSpec change，不在此重复。 -->

## Requirements（指针，不重述）

| 来源 | 路径 |
|------|------|
| 正式任务（唯一勾选源） | `openspec/changes/react-engine-v17/tasks.md` |
| 验收标准 WHEN/THEN/AND | `openspec/changes/react-engine-v17/specs/agent-runtime/spec.md` |
| 设计决策 D1–D6 | `openspec/changes/react-engine-v17/design.md` |
| 动机与范围 | `openspec/changes/react-engine-v17/proposal.md` |
| Grill 需求输入 | `docs/exploration/react-engine-v17.md` |

## Research Findings

- **F1（WIP 已核对并勾选）**：apply 时发现实现已在 propose 前落地。按纪律逐项对照 specs + `pytest` 48 passed 后写入 `progress.md`，再勾选 `tasks.md`。apply 过程未再改引擎代码。
- **F2（主改文件）**：执行落点与 proposal Impact 一致——`apps/api/app/services/llm_client.py`、`apps/api/app/services/agent_runtime/runtime.py`、`apps/api/tests/test_react_engine_v17.py`（及回归套件）。
- **F3（OpenSpec 已 validate）**：change `react-engine-v17` 规划产物 4/4 完成且 `openspec validate react-engine-v17 --strict` 曾通过；本会话仅初始化 planning-with-files，未改 OpenSpec 产物。

## Technical Decisions

（执行中产生的新决策记录于此；已锁定的设计决策见 `design.md`。）

| Decision | Rationale |
|----------|-----------|
| Planning 目录命名 `2026-08-24-react-engine-v17` | 与既有 `.planning/YYYY-MM-DD-<change>` 惯例一致 |
| `.active_plan` 指向本目录 | 切换自 `code-agent-secure-workspace-runtime`，本轮执行焦点为 v17 |
| apply 不重写已满足 specs 的 WIP | 核对+测试绿即可勾选；避免无意义 diff |

## Issues Encountered

| Issue | Resolution |
|-------|------------|
| `session-catchup.py` 在 `~/.codex/skills/planning-with-files/scripts/` 不存在 | 跳过 catchup；改用 git diff + 文件存在性核对初始化 |

## Resources

- `openspec/changes/react-engine-v17/`
- `docs/exploration/react-engine-v17.md`
- `docs/react-engine.md`
- 前序：`openspec/changes/archive/2026-08-22-react-engine-v16/`
