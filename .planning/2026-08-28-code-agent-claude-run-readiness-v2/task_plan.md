# Task Plan: code-agent-claude-run-readiness-v2（Execution Map）

## Goal

按 `openspec/changes/code-agent-claude-run-readiness-v2/tasks.md` 的既定顺序实施并验证全部任务，保持该文件为唯一正式任务来源。

## Current Phase

Phase 0: Planning initialized（complete）

## Next Step

等待用户明确进入 apply/implementation 阶段；下一条真正执行任务是 OpenSpec task 1.1。

## Phases

### Phase 0: Planning initialization

- **OpenSpec task mapping:** none
- **Depends on:** proposal/design/specs/tasks 已存在
- **Status:** complete

### Phase 1: Direct execution entry

- **OpenSpec task mapping:** 1.1–1.3
- **Depends on:** Phase 0
- **Status:** complete

### Phase 2: Claude Code workspace root readiness

- **OpenSpec task mapping:** 2.1–2.5
- **Depends on:** Phase 1
- **Status:** complete

### Phase 3: Runtime terminal results

- **OpenSpec task mapping:** 3.1–3.4
- **Depends on:** Phase 2
- **Status:** complete

### Phase 4: LLM binding guidance and readiness UI

- **OpenSpec task mapping:** 4.1–4.3
- **Depends on:** Phase 3
- **Status:** in_progress

### Phase 5: Host validation output

- **OpenSpec task mapping:** 5.1–5.2
- **Depends on:** Phase 3
- **Status:** pending

### Phase 6: Acceptance and regression

- **OpenSpec task mapping:** 6.1–6.4
- **Depends on:** Phases 1–5
- **Status:** pending

## Boundaries

- `openspec/changes/code-agent-claude-run-readiness-v2/tasks.md` 是唯一正式任务来源。
- 本文件只映射 execution phases、依赖和执行状态；不复制、不扩展、不重新定义 OpenSpec 需求。
- `proposal.md`、`design.md` 与 `specs/` 提供范围、设计和行为约束；发生冲突时不得通过本文件修改正式任务。
- 本次初始化只建立 planning files，不开始 implementation，不修改业务代码，不勾选 `tasks.md`。
- 不自动创建或配置 Cloud Claude LLMResource。
- 不自动改绑 `dbt-test.llm_id`。
- 不修改 `dbt-clickhouse-gamestat` Skill。
- 不迁移历史 run workspace。

## Per-Task Completion Gate

每完成一个 OpenSpec task，严格按以下顺序执行：

1. 完成该 task 对应代码、测试或交付物。
2. 运行该 task 声明的测试、命令或可观察验证，并确认通过。
3. 在 `progress.md` 写入实际变更、验证命令和结果。
4. 重新核对代码与测试证据。
5. 最后勾选 `openspec/changes/code-agent-claude-run-readiness-v2/tasks.md` 对应 checkbox。

如果代码或测试证据不足，即使实现看似完成，也不得勾选 OpenSpec task。
