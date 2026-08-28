# Task Plan: code-agent-secure-workspace-runtime（Execution Map）

## Goal

按 `openspec/changes/code-agent-secure-workspace-runtime/tasks.md` 的既定顺序实施并验证全部任务，保持该文件为唯一正式任务来源。

## Next Step

Change 已 verify、main specs 已 sync，并归档到 `openspec/changes/archive/2026-08-25-code-agent-secure-workspace-runtime/`。本计划闭合；无剩余 OpenSpec 任务。

## Current Phase

Phase 9: 集成、安全回归与发布门禁（complete）

## Phases

### Phase 1: 数据模型与安全配置基线

- **OpenSpec task mapping:** 1.1–1.4
- **Depends on:** none
- **Status:** complete

### Phase 2: 权限、Secret 引用与有效策略

- **OpenSpec task mapping:** 2.1–2.4
- **Depends on:** Phase 1
- **Status:** complete

### Phase 3: 受控 Repository Importer 与 sealed snapshot

- **OpenSpec task mapping:** 3.1–3.6
- **Depends on:** Phases 1–2
- **Status:** complete

### Phase 4: Manifest 发布、控制面与 readiness

- **OpenSpec task mapping:** 4.1–4.4
- **Depends on:** Phases 1–3
- **Status:** complete

### Phase 5: Snapshot Workspace 与 Compose 路径映射

- **OpenSpec task mapping:** 5.1–5.4
- **Depends on:** Phases 1, 3–4
- **Status:** complete

### Phase 6: 全容器化 Code Tool runtime

- **OpenSpec task mapping:** 6.1–6.6
- **Depends on:** Phases 1–2, 5
- **Status:** complete

### Phase 7: Fail-closed 源码与 patch 验证

- **OpenSpec task mapping:** 7.1–7.4
- **Depends on:** Phases 3–6
- **Status:** complete

### Phase 8: 容器与 Workspace 持久化生命周期

- **OpenSpec task mapping:** 8.1–8.4
- **Depends on:** Phases 1, 5–7
- **Status:** complete

### Phase 9: 集成、安全回归与发布门禁

- **OpenSpec task mapping:** 9.1–9.5
- **Depends on:** Phases 1–8
- **Status:** complete

## Boundaries

- `openspec/changes/code-agent-secure-workspace-runtime/tasks.md` 是唯一正式任务来源；本文件只映射 phase、依赖和执行状态，不复制或重新定义需求。
- `proposal.md`、`design.md` 与 `specs/` 提供范围、设计和行为约束；发生冲突时不得通过本文件修改正式任务。
- 本次初始化不修改业务代码、不运行 implementation task，也不预先勾选 `tasks.md`。
- Skill revision/context 与用户文档/黄金案例属于后续独立 changes，本 change 不抢跑实现。

## Per-Task Completion Gate

对每一个 OpenSpec task 必须严格执行：

1. 完成该 task 对应代码或交付物。
2. 运行该 task 声明的测试、命令或可观察验证，并确认通过。
3. 在 `progress.md` 写入实际变更、验证命令和结果。
4. 重新核对代码与测试证据后，最后勾选 `tasks.md` 对应 checkbox。

任何一步缺失时，该 task 保持未勾选。

## Errors Encountered

| Error | Attempt | Resolution |
|---|---:|---|
| Verifier MODIFIED requirement 改名导致原 scenario 被判定遗漏 | 1 | 恢复既有 scenario 标识，并保留扩展后的 fail-closed 行为 |
| zsh 未匹配 `docker-compose*.y*` 导致只读搜索失败 | 1 | 改用 `rg` 路径过滤；未产生文件变更 |
| Task 1.4 control-plane UI 回归中 3 个旧草稿发布测试缺少 secure evidence | 1 | 保持 fail-closed 实现；测试在 publish 前模拟 Importer 填充 snapshot/scan/digest 证据 |
| Task 2.1 首次授权回归有 7 个旧 ACL-only 假设失败 | 1 | 保持服务层门禁；将 fixture/预期迁移为显式 owner/operator/reviewer 与统一脱敏拒绝原因 |
| `rg` 负向 look-ahead 查询不受默认 regex engine 支持 | 1 | 改用简单调用点列表、语法检查和测试覆盖完成审计 |
| 在 `apps/api` cwd 下误用仓库根相对路径，导致只读 `sed`/`py_compile` 报不存在 | 1 | 改用 cwd 相对的 `app/...` 与 `tests/...` 路径重新执行；语法检查通过 |
| `pytest -q` 收集了需要已运行服务器的 `scripts/smoke_test.py`，sandbox 中连接被拒绝 | 1 | 改为 `pytest -q tests` 执行正式测试目录；smoke script 不属于 task 2.1 单元回归 |
| Task 2.3 在 `apps/api` cwd 下再次使用 `apps/api/tests/test_code_agent*.py` glob，zsh 无匹配 | 1 | 使用 `rg ... tests -g 'test_code_agent*.py'`，不再混用仓库根与子目录路径 |
| Task 4.1 first UI patch context was stale | 1 | Re-read the exact component anchors and applied smaller verified patches |
| Task 4.1 transient Manifest integer default was `None` before flush | 1 | Normalize the transient value with `(value or 0)` and retain the database default; focused and full reruns passed |
| Planning catch-up user-level script path was absent | 1 | Used the repository-installed `.codex/skills/planning-with-files/scripts/session-catchup.py`; catch-up reported no unsynced context |
| Task 6.6 first scoped `git diff --check` used repository-root pathspecs from `apps/api`, so it inspected no files | 1 | Re-ran from repository root; because the files are untracked, used Python compilation plus targeted trailing-whitespace checks as the actual file validation |
| Task 7.3 first canonical patch run expected one finding when both changed file and canonical diff contained the same secret | 1 | Kept both redacted locations as required coverage and corrected the assertion to expect file + canonical-diff findings |
| Task 7.4 artifact evidence gate rejected the canonical-deletion fixture at source evidence before patch scanning | 1 | Kept the source gate; made the test explicitly model an older source rule with no finding so the current patch scanner's defense-in-depth path is exercised |
| Task 7.4 CodeAgent regression found a result-API fixture still built the legacy four-file artifact bundle | 1 | Migrated the synthetic fixture to the v2 six-file bundle with source/patch report hashes and frozen snapshot/image evidence; kept strict review rejection for incomplete bundles |
| Task 8.1 combined patch used a stale runtime cleanup-call context and did not apply | 1 | Split model/lifecycle/runtime changes and re-read exact runtime anchors; no partial change resulted from the failed patch |
| Task 9.1 dedicated runner image build stalled repeatedly while downloading the Debian arm64 package index | 1 | Cancelled the incomplete build and used the already-local pinned Python base for the bind-only real-daemon gate; did not claim the runner image build as passed |
| Task 9.1 first real-Docker test used an unavailable pytest-asyncio marker | 1 | Converted the test to synchronous execution with `asyncio.run` only for cleanup; rerun passed all 3 real Docker cases |
| Task 9.5 runner image build cannot fetch Debian trixie arm64 main package index under default or host BuildKit networking | 4 | Probed three reachable mirrors, added portable build args with official defaults, built the helper+Git image, resolved its RepoDigest and passed all exact-digest release gates |
| Task 9.5 first combined deploy test command transiently reported the deploy-config test path missing | 1 | Re-listed the test directory and reran the config/deploy pair successfully (22 passed); strict OpenSpec validation had already passed independently |
