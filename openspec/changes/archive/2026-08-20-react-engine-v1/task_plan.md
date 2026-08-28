# Task Plan — react-engine-v1

> 本文件是 **execution 阶段规划**,不是需求来源。
> **唯一正式任务来源 = `tasks.md`**;本文件只把 `tasks.md` 的 7 组任务映射为可执行的 phase,不新增、不删减、不重定义需求/验收口径。
> 需求与验收以 `proposal.md`(What Changes)、`specs/agent-runtime/spec.md`(9 条 Requirement 的 Scenario)为准。

## Phase 依赖关系

```
P1 软提示聚合+可观测 ──► P2 FINAL语义 ──► P3 MCP执行层 ──► P4 续跑层 ──► P5 SOP迁移 ──► P6 docs ──► P7 收尾
                        ▲                ▲
                        └──── 依赖 P1 ────┘  (FINAL 告警经聚合层注入)
```

- P1 是**基座**:所有后续「注入提示」的逻辑都依赖聚合层,必须先落地。
- P3 独立于 P4(会话复用不依赖 checkpoint);P4 的查询去重依赖 P3 的多 MCP 分派与 `mcp_sessions` 穿透。
- P5 依赖 P3 的多 MCP 分派落定(需补「按工具归属 MCP 选 SOP」指引)。
- P6 最后,反映 P1–P5 最终形态。

## 执行 Phases(映射自 tasks.md)

### P1 — 软提示聚合 + 可观测基座
- **tasks.md 任务**:`1.1` `1.2` `1.3` `1.4`
- **目标**:`coach_hint` 单槽改聚合;`close()` 异常吞掉;复用/失效/去重可观测。
- **验收映射**:spec `R9`(多软提示合并不互相覆盖);`R2` 的可观测场景。
- **测试门**:`1.4`(hint 聚合不丢提示;close on cancel 不残留子进程)。

### P2 — FINAL 语义
- **tasks.md 任务**:`2.1` `2.2`
- **目标**:FINAL 分支计算 `skipped`,经聚合层注入可见步骤 + 软提示。
- **验收映射**:spec `R1`(FINAL 与工具同轮告警,3 个场景)。
- **测试门**:`2.2`(SHELL+FINAL 告警;仅 FINAL 不告警;PLAN+FINAL 不告警)。

### P3 — MCP 执行层(会话复用 + 失效恢复 + 多 MCP 分派)
- **tasks.md 任务**:`3.1` `3.2` `3.3` `3.4` `3.5` `3.6`
- **目标**:per-run `McpSessionManager` 复用会话;失效重建重试一次;`execute_action` 加 `mcp_sessions`;多 MCP 按工具名分派。
- **验收映射**:spec `R2`(会话复用/关闭/失效恢复/可观测)、`R8`(多 MCP 分派)。
- **测试门**:`3.6`(复用只握手一次;结束关闭;失效重试一次;多 MCP 分派)。

### P4 — 续跑层(checkpoint 扩展 + 续跑注入 + 查询去重 + Verifier 增强)
- **tasks.md 任务**:`4.1` `4.2` `4.3` `4.4` `4.5` `4.6` `4.7` `4.8`
- **目标**:checkpoint 四字段扩展与恢复;续跑注入 plan;查询去重(两层生命周期);软子任务提示;`_reflect_final` 输入增强。
- **验收映射**:spec `R3`(checkpoint 保真)、`R4`(查询去重)、`R5`(续跑上下文注入)、`R6`(软子任务提示)、`R7`(Verifier 依据子任务完成度)。
- **测试门**:`4.8`(roundtrip 四字段保真;`test_resume_injects_context`;`test_query_cache_dedup`)。

### P5 — SOP 口径迁出引擎
- **tasks.md 任务**:`5.1` `5.2` `5.3` `5.4` `5.5`
- **目标**:业务口径迁 Skill references;引擎目录中性化并标注来源 MCP;多 MCP 分派指引入 Skill。
- **验收映射**:proposal `#9`(SOP 迁出,可观察行为变化);无独立 spec Requirement(口径卫生,非运行时行为)。
- **测试门**:`5.5`(目录断言:引擎不含 ads 硬编码)。

### P6 — 同步 docs/react-engine.md
- **tasks.md 任务**:`6.1`
- **目标**:文档重写为 v1 架构。
- **验收映射**:proposal `#10`(纯文档)。
- **测试门**:无(纯文档,人工复核)。

### P7 — 冗余清理 + 验证收尾
- **tasks.md 任务**:`7.1` `7.2` `7.3`
- **目标**:删失效 helper;全量测试;OpenSpec 校验。
- **验收映射**:全量 108 测试全绿 + 无新增静态门禁 + `openspec validate --strict react-engine-v1`。
- **测试门**:`7.2` `7.3`。

## 完成协议(与 progress.md 联动)

1. 开始一个 task → `progress.md` 标 `in_progress`。
2. 完成代码 + 通过对应测试 + 满足验收场景 → `progress.md` 标 `done` 并记录证据。
3. **然后**才在 `tasks.md` 勾选该 checkbox。
4. **禁止**仅因代码已修改就勾选;必须满足上述 acceptance criteria。

## 验收口径速查(引用,非重定义)

| spec Requirement | 场景 | 验证方式 | 对应 Phase |
|---|---|---|---|
| R1 FINAL+工具告警 | 3 场景 | 单测 + 可见步骤断言 | P2 |
| R2 MCP 会话复用 | 4 场景 | 单测(握手次数/close/失效/可观测) | P1(可观测)、P3 |
| R3 checkpoint 保真 | 2 场景 | roundtrip 单测 | P4 |
| R4 查询去重 | 2 场景 | dedup 单测 | P4 |
| R5 续跑上下文注入 | 1 场景 | resume 单测 | P4 |
| R6 软子任务提示 | 1 场景 | 单测 | P4 |
| R7 Verifier 依据完成度 | 1 场景 | 单测/人工 | P4 |
| R8 多 MCP 分派 | 1 场景 | 单测 | P3 |
| R9 软提示聚合 | 2 场景 | 单测 | P1 |
