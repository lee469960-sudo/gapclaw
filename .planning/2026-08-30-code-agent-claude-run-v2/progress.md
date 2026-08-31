# Progress Log: code-agent-claude-run-v2

## Session: 2026-08-30

### Phase 0: Planning initialization

- **Status:** complete
- 已读取 OpenSpec change：
  - `proposal.md`
  - `design.md`
  - `specs/` 下全部 6 份 delta spec
  - `tasks.md`
- 已创建 Planning with Files：
  - `task_plan.md`
  - `findings.md`
  - `progress.md`
- 已将 `tasks.md` 映射为 5 个 execution phases；未重新定义需求。
- OpenSpec 严格校验：`openspec validate code-agent-claude-run-v2 --type change --strict --json` → 通过。
- 尚未执行 implementation；`tasks.md` 中 20 条任务均保持未勾选。

### Phase Status

| Phase | OpenSpec tasks | Status |
|---|---|---|
| Phase 1 | 1.1–1.4 | pending |
| Phase 2 | 2.1–2.4 | pending |
| Phase 3 | 3.1–3.4 | pending |
| Phase 4 | 4.1–4.4 | pending |
| Phase 5 | 5.1–5.4 | pending |

### Task 1.1

- **Status:** complete
- 扩展 `CodeProjectManifest` 与控制面请求/响应，增加 local 发布命令、target、依赖、网络、Secret、验证计划和锁字段；增加启动迁移。
- 新增 `local_publish` 命令注册与 Manifest 校验模块，未启用 local 发布的 Manifest 保持兼容。
- Verification: `pytest -q tests/test_code_agent_local_publish.py tests/test_code_agent_manifest_publish.py tests/test_code_agent_control_plane.py` → **40 passed**；`python -m compileall -q app` 通过。

### Task 1.2

- **Status:** complete
- `local_publish` 注册表支持固定 argv/依赖/网络/验证元数据；仅允许 Manifest 引用已登记 command ID，拒绝 shell 注入、非 local target 和超出登记范围的依赖/网络。
- Verification: `pytest -q tests/test_code_agent_local_publish.py` → **6 passed**。

### Task 1.3

- **Status:** complete
- 增加 local publish preflight 结果、Workspace/Secret/网络/命令检查和一次性确认状态；新增 Agent Chat `preflight_local_publish`/`confirm_local_publish` 操作。
- Verification: `pytest -q tests/test_code_agent_local_publish.py` → **7 passed**。

### Task 1.4

- **Status:** complete
- 新增 `CodePublishLock` 持久化表和 acquire/release 辅助逻辑，按 project + local + lock_key 串行化正式发布；确认阶段获取锁，冲突返回稳定原因。
- Verification: `pytest -q tests/test_code_agent_local_publish.py` → **8 passed**。

### Task 2.1

- **Status:** complete
- `deploy/code-agent-runner.Dockerfile` 增加固定版本的 curl、dbt-core、dbt-clickhouse，并在镜像构建阶段执行 Claude/OpenSpec/curl/python/dbt 版本检查。
- `deploy/build-code-agent-runner.sh` 保持 linux/arm64、linux/amd64 buildx 发布，并透传 dbt 固定版本参数和 manifest-list digest 指引。
- Runner 启动时根据冻结 local publish 契约预检登记依赖，记录脱敏工具版本到 runner facts；未登记依赖 fail closed。
- Verification: `pytest -q tests/test_code_agent_runner.py tests/test_code_agent_local_publish.py` → **34 passed**。

### Task 2.2

- **Status:** complete
- 复用现有 runner 的非 root、read-only rootfs、cap-drop ALL、no-new-privileges、单一 Workspace rw mount 与受限 `/tmp` tmpfs；启动参数拒绝 network/privileged/nested container/额外挂载，inspect facts 漂移时 fail closed。
- Verification: `pytest -q tests/test_code_agent_runner.py` → **28 passed**，覆盖 Docker Socket/宿主机路径额外挂载、提权和 inspect 漂移拒绝。

### Task 2.3

- **Status:** complete
- local publish runner 仅在冻结 local 契约存在时使用 bridge，并将 Manifest 网络目的地写入 RunnerSpec；默认/legacy CodeAgent 继续使用 `network_mode=none`。
- 新增 HTTP(S) host/port、无凭证、无 query/fragment、无 wildcard 的目标校验，默认端口规范化，非法目标在 Manifest/preflight 阶段拒绝。
- Verification: `pytest -q tests/test_code_agent_local_publish.py tests/test_code_agent_runner.py` → **36 passed**。

### Task 2.4

- **Status:** complete
- 通过现有 `DeployTokenSecretStore.resolve_for_importer` 以 project-scoped read-only Secret lease 注入发布环境，契约只保存 reference_id 和安全的环境变量名。
- Lease 关闭时清零内存材料并清空环境映射；Secret 不进入命令 argv、task contract、日志或 Workspace 文件。
- Verification: `pytest -q tests/test_code_agent_local_publish.py` → **11 passed**，覆盖 project scope、短期环境注入和关闭清理。

### Task 3.1

- **Status:** complete
- 增加 `Runner.exec_argv` 直接 argv 执行通道与 `execute_local_publish`，仅按冻结 canonical argv 执行一次，捕获退出码、脱敏输出和 release/publish ID，并写入 `publish_result`。
- Runtime 在共享 CodeAgent 生命周期中加入 local publish 阶段与事件，不另建执行平面。
- Verification: `python -m compileall -q app`；相关回归 **126 passed**。

### Task 3.2

- **Status:** complete
- local publish 强制 target=local、拒绝 git add/commit/push/tag/reset/clean 等直接副作用命令；执行前后对 Workspace 指纹核对，发现源文件变化即失败。
- Verification: local publish/runner/control-plane/UI 回归 **126 passed**。

### Task 3.3

- **Status:** complete
- Runner 超时不重试并转 unknown；执行异常按 runner 可用性区分失败/状态未知，持久化人工查询远端状态的恢复建议。
- Verification: local publish/runner 回归 **38 passed**。

### Task 3.4

- **Status:** complete
- preflight、confirmation、publish、publish_evidence、verify/seal/cleanup 统一写入 code profile event 流，历史回放沿用既有脱敏与序列机制。
- Verification: `pytest -q tests/test_code_agent_local_publish.py tests/test_code_agent_runner.py tests/test_code_agent_control_plane.py tests/test_code_agent_control_plane_ui.py` → **126 passed**。

### Task 4.1

- **Status:** complete
- Verifier 增加 local 结果裁决：必须为 local target、退出码 0、存在 release ID、Secret 输出已脱敏；Workspace/Git 约束继续由既有完整性与变更检查执行。
- Verification: `pytest -q tests/test_code_agent_verifier.py tests/test_code_agent_failures.py` → **通过**。

### Task 4.2

- **Status:** complete
- 增加 local publish 拒绝、依赖/网络/认证/命令失败、验证失败和状态未知的稳定 failure taxonomy 与终态展示；Claude 文本不参与裁决。
- Verification: `pytest -q tests/test_code_agent_failures.py tests/test_code_agent_results.py` → **通过**。

### Task 4.3

- **Status:** complete
- 对话事件标签覆盖 preflight、人工确认、发布执行、发布证据、验证和清理；终态 Markdown 输出展示退出码、发布 ID 和恢复信息，敏感内容经脱敏。
- Verification: `pytest -q tests/test_code_agent_control_plane_ui.py tests/test_code_agent_results.py` → **通过**。

### Task 4.4

- **Status:** complete
- 状态未知/失败结果带人工恢复建议，结果卡和 Markdown 均保持“不可直接采用”，不会误显示为成功或 Patch。
- Verification: `pytest -q tests/test_code_agent_results.py tests/test_code_agent_failures.py` → **通过**。

### Task 5.1

- **Status:** complete
- 增加 `deploy/code-agent-local-publish.commands.example.json` 作为单一 `dbt-gamestat-local` 试点登记模板，固定 local argv、依赖、网络目的地、Secret 环境名和验证计划。
- Verification: 试点 command registry 解析/preflight 测试通过。

### Task 5.2

- **Status:** complete
- 使用 project-scoped Secret、冻结命令、锁和 fake runner 完成一次确认后 local publish 闭环集成测试，验证单次 argv、脱敏输出、release ID 和生命周期状态。
- Verification: `pytest -q tests/test_code_agent_local_publish.py` → **13 passed**。

### Task 5.3

- **Status:** complete
- `CODE_LOCAL_PUBLISH_ENABLED` 默认关闭；preflight/execute 在关闭时 fail closed。Manifest/镜像 digest 仍沿用既有发布校验，普通 CodeAgent 路径未改变。
- Verification: feature flag 与既有 manifest/control-plane 回归通过。

### Task 5.4

- **Status:** complete
- `python -m compileall -q app`、`git diff --check`、前端 `npm run build` 均通过；CodeAgent 相关回归达到 **544 passed, 4 skipped** 后，完整模式被仓库内长时间数据生成测试中断，未发现本变更失败。
- 重点回归（local publish/runner/verifier/failures/results/control-plane/UI）最终 **190 passed**。

### Post-verification adjustment: runtime business tools are optional

- **Status:** complete
- 按用户确认，dbt、jq、clickhouse-client 不再是 runner 运行环境必备项；基础 runner 镜像移除 dbt/dbt-clickhouse 安装与 DBT 构建参数。
- Runner 对 Manifest 登记依赖改为 best-effort 审计探测：可用版本写入 `local_publish_tool_versions`，缺失写入 `local_publish_tool_missing`，不再阻塞 Workspace/Runtime 启动。
- OpenSpec `code-agent-claude-run-v2` proposal/design/spec/tasks 已同步为“业务依赖不阻塞启动”。
- Claude Code runtime prompt 增加明确规则：dbt、jq、clickhouse-client 缺失不触发查找替代安装/虚拟环境，只有具体仓库命令直接依赖时才作为任务阶段阻塞。
- Claude Code runtime prompt 增加发布边界：编码循环内禁止执行仓库 deploy/publish/release/CI 发布命令；真实 local 发布只能走平台-controlled preflight/confirmation/publish stage。

### Post-verification fix: local publish confirmation handoff

- **Status:** complete
- 修复 publish-only 任务仍依赖 Claude Code coding loop 的问题：当任务目标同时包含 local 与发布/部署/publish/deploy/CI 语义且 Run 契约包含 `local_publish` 时，跳过 Claude 模型预检和 coding loop，直接进入平台 local publish preflight/confirmation gate。
- 修复未确认 local publish Run 过早 cleanup 的问题：preflight 通过后 Run 进入 `needs_user_decision` + `publish_state=awaiting_confirmation`，保留 active Workspace/Runner，等待同一对话确认后再清理。
- 修复确认后不执行发布的问题：同一 CodeAgent 会话中用户回复“确认/是/继续/confirm”等会复用上一条待确认 Run，获取发布锁后调度后台 `execute_local_publish`，随后执行 Verifier、Sealer、cleanup，并将完整 Markdown 结果写回对话。
- 修复 Run 级发布状态 ORM 映射缺失：`CodeAgentRun` 增加 `publish_state`、`publish_preflight`、`publish_confirmation`、`publish_result`；旧 SQLite 迁移同时补齐 `code_agent_runs` 和保留 `code_scan_reports` 兼容列。
- Verification:
  - `python -m compileall -q apps/api/app` → 通过。
  - `pytest -q tests/test_code_agent_control_plane_ui.py -k local_publish_run` → **1 passed**。
  - `pytest -q tests/test_code_agent_local_publish.py tests/test_code_agent_control_plane_ui.py -k "local_publish or confirmation"` → **17 passed**。
  - `pytest -q tests/test_code_agent_runtime_context.py::test_claude_code_preflight_uses_existing_runner_container_and_workspace tests/test_code_agent_runtime_context.py::test_claude_code_patch_ready_requires_verifier_pass_and_sealer_success` → **2 passed**。
  - `pytest -q tests/test_code_agent_secure_schema.py tests/test_code_agent_local_publish.py tests/test_code_agent_control_plane_ui.py::test_chat_confirmation_reuses_awaiting_local_publish_run` → **24 passed**。
  - Broad runtime selector `pytest -q tests/test_code_agent_runtime_context.py -k "claude_code or patch_ready or needs_user_decision"` 手动中断于既有长耗时路径；中断前 **4 passed**，未出现失败。
  - `git diff --check` → 通过。

### Post-verification fix: Manifest local publish UI fields

- **Status:** complete
- 在 Code Project Manifest 抽屉新增「Local 发布配置」区域，暴露后端已有字段：
  - `local_publish_command_id`
  - `local_publish_target`
  - `local_publish_dependencies`
  - `local_publish_network_targets`
  - `local_publish_secret_ref`
  - `local_publish_verification_plan`
  - `local_publish_lock_key`
- `local_publish_dependencies`、`local_publish_network_targets`、`local_publish_verification_plan` 按 JSON 数组校验；保存草稿 payload 直接传给后端。
- 默认空配置保持普通 Manifest 行为不变；页面提示区分“触发 CI API”与“Sandbox 内直接跑仓库发布脚本”的依赖填写差异。
- Verification:
  - `pytest -q tests/test_code_agent_control_plane_ui.py -k "manifest_component"` → **5 passed**。
  - `npm run build` in `apps/web` → 通过（仅既有 chunk size / Rollup 注释 warning）。
  - `git diff --check` → 通过。

### Post-verification fix: Manifest local publish UI defaults

- **Status:** complete
- 为 Manifest 编辑页新增 `DEFAULT_LOCAL_PUBLISH`，默认填充当前试点配置：
  - command id / lock key: `dbt-gamestat-local`
  - target: `local`
  - dependencies: `curl`, `python3`
  - network target: `https://g.testskydata.com:443`
  - verification plan: `release_id` from stdout + local healthcheck
- 打开已有 Manifest 时，已保存的 local publish 值优先；缺失时才使用默认值。Secret 引用不设置默认值，避免误用凭据。
- Verification:
  - `pytest -q tests/test_code_agent_control_plane_ui.py -k "manifest_component"` → **5 passed**。
  - `npm run build` in `apps/web` → 通过（仅既有 chunk size / Rollup 注释 warning）。
  - `git diff --check` → 通过。
