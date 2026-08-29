# Progress Log: code-agent-workspace-conversation-ui

## Session: 2026-08-29

### Phase 0: Planning initialization

- Status: complete
- Read OpenSpec artifacts:
  - `proposal.md`
  - `design.md`
  - all files under `specs/`
  - `tasks.md`
- Created Planning with Files:
  - `task_plan.md`
  - `findings.md`
  - `progress.md`
- No implementation started.
- No OpenSpec task checkbox updated.

### Phase 1: CodeAgent Workspace API

- **Status:** in_progress
- Implemented task 1.1 read-only `get_code_workspace` metadata action bound to `code_run_id`, with Run/project authorization and explicit workspace readiness states.
- Verification: `python -m py_compile apps/api/app/routers/agent_chat.py && pytest apps/api/tests/test_code_agent_control_plane_ui.py -q` → `43 passed, 94 warnings`.
- Implemented task 1.2 read-only file tree and text preview operations with run binding, path containment, depth/size limits, hidden runtime directories, binary rejection, and redaction.
- Verification: `pytest apps/api/tests/test_code_agent_workspace_preview.py -q` → 2 passed.
- Implemented task 1.3 `get_code_workspace_git` with explicit `git -C <run workspace>` status/branch queries and filtered changed files.
- Verification: `pytest tests/test_code_agent_workspace_preview.py -q && python -m py_compile app/routers/agent_chat.py` → 3 passed.
- Implemented task 1.4 unified readiness handling across metadata, file preview, and Git preview; invalid mounts require `.git` and never fall back to `/workplace/task/`.
- Verification: `pytest tests/test_code_agent_workspace_preview.py -q && pytest tests/test_code_agent_control_plane_ui.py::test_agent_chat_exposes_run_bound_code_workspace_metadata -q` → 5 passed total.

### Phase 2: CodeAgent Conversation Progress

- **Status:** in_progress
- Implemented task 2.1 `get_code_events`, exposing persisted Claude Code runtime history with bounded pagination, sequence numbers, Run authorization, and string redaction.
- Verification: `pytest apps/api/tests/test_code_agent_control_plane_ui.py::test_agent_chat_exposes_redacted_code_runtime_events -q` → passed.
- Implemented task 2.2 incremental event cursor support via `since`/`next_since`; reconnect clients can request only events after the last applied sequence without duplicating history.
- Verification: `pytest tests/test_code_agent_control_plane_ui.py::test_agent_chat_exposes_redacted_code_runtime_events -q && python -m py_compile app/routers/agent_chat.py` → passed.
- Implemented task 2.3 by adding a server-authored `result_card` to CodeAgent result serialization, preserving terminal status, severity, verification, and adoption eligibility.
- Verification: `pytest tests/test_code_agent_results.py tests/test_code_agent_control_plane_ui.py::test_code_result_exposes_terminal_result_card -q` → 9 passed.

### Phase 3: Frontend Workspace and Conversation UI

- **Status:** in_progress
- Implemented task 3.1 with a CodeAgent-only `CodeWorkspacePanel` bound to `activeCodeRunId`; Standard Agent continues rendering `WorkplacePanel`.
- Verification: `pytest tests/test_code_agent_control_plane_ui.py::test_agent_chat_uses_code_workspace_panel_only_for_code_profile -q` → passed.
- Implemented task 3.2 with repository tree, text preview, Git branch/clean state, changed-file count, and distinct empty/expired/mount/permission/error labels.
- Verification: `pytest tests/test_code_agent_control_plane_ui.py::test_code_workspace_panel_renders_distinct_preview_states -q` → passed.
- Implemented task 3.3 by rehydrating persisted CodeAgent events through `get_code_events`, merging them into the conversation, and deduplicating by Run/sequence during WebSocket reconnect.
- Verification: `pytest tests/test_code_agent_control_plane_ui.py::test_agent_chat_rehydrates_and_deduplicates_code_runtime_events -q` → passed.
- Implemented task 3.4 by rendering the server-authored `result_card` summary in the CodeAgent result area; Patch actions remain gated by `directly_adoptable && artifact`.
- Verification: `pytest tests/test_code_agent_control_plane_ui.py::test_agent_chat_renders_server_result_card_and_gates_patch_actions -q` → passed.

### Phase 4: Integration, Security, and Compatibility

- **Status:** in_progress
- Task 4.1 authorization/containment review: all Code Workspace and event actions bind to `CodeAgentRun`, require project reviewer authorization, and resolve only `run.workspace_path`.
- Verification: `pytest tests/test_code_agent_control_plane_ui.py::test_code_workspace_and_events_endpoints_require_run_bound_reviewer -q` → passed.
- Implemented task 4.2 verification across preview, change-path, Verifier, and sealed-artifact boundaries. Workspace tree/file preview rejects and hides `.git/**` and `.claude/**`; Git preview filters runtime metadata; existing `workspace_changed_paths`, Verifier, and Sealer regression tests confirm these paths cannot enter verifier inputs or `patch.diff`.
- Verification: `pytest tests/test_code_agent_workspace_preview.py tests/test_code_agent_artifacts.py::test_runtime_only_claude_files_are_excluded_from_changed_paths_and_sealed_patch tests/test_code_agent_artifacts.py::test_flattened_claude_workspace_does_not_leak_root_rewrite_into_sealed_patch -q` → 7 passed.
- Implemented task 4.3 regression coverage for concurrent CodeAgent Runs alongside a Standard Agent session. Backend assertions prove each `code_run_id` returns only its own runtime events and workspace tree; frontend assertions prove CodeAgent uses `CodeWorkspacePanel` while Standard Agent retains `/workplace` and Code event hydration is code-profile/run gated.
- Verification: `pytest tests/test_code_agent_control_plane_ui.py::test_concurrent_standard_and_code_sessions_keep_workspace_and_event_streams_isolated -q` → passed.
- Implemented task 4.4 compatibility handling: Code Workspace treats a missing/404 preview API as an explicit unsupported state with an upgrade/result-view message, while the existing Standard Agent `WorkplacePanel` and `/workplace` route remain the unconditional non-CodeAgent path. This keeps rollback additive and avoids mapping an unavailable Code Workspace to generic repository content.
- Verification: `pytest tests/test_code_agent_control_plane_ui.py::test_code_workspace_panel_renders_distinct_preview_states tests/test_code_agent_control_plane_ui.py::test_legacy_agent_chat_workspace_flow_remains_the_standard_agent_fallback -q` → 2 passed.
- Implemented task 5.1 mock acceptance coverage spanning run-bound repository metadata/tree/Git preview, persisted progress events, terminal `patch_ready` adoption eligibility, and blocked `target_not_found` results. Existing runtime, Verifier, and Sealer tests remain the boundary checks for execution and artifact behavior.
- Verification: `pytest tests/test_code_agent_control_plane_ui.py::test_code_agent_workspace_conversation_mock_acceptance_covers_preview_progress_and_terminal_results -q` → passed.
- Implemented task 5.2 documentation in `docs/code-agent-workspace.md`, covering CodeAgent selection/Run startup, Workspace and progress inspection, terminal result review, and the strict `/workspace` versus `/workplace` boundary including unsupported/expired states.
- Verification: `pytest tests/test_code_agent_control_plane_ui.py::test_code_agent_workspace_usage_documentation_defines_run_flow_and_root_boundaries -q` → passed.
- Implemented task 5.3 focused regression run and recorded exact results. Backend: `pytest tests/test_code_agent_control_plane_ui.py tests/test_code_agent_workspace_preview.py tests/test_code_agent_results.py tests/test_code_agent_artifacts.py -q` → **94 passed, 148 warnings in 18.33s**. Frontend: `npm run build` in `apps/web` → **Vite build succeeded** (1901 modules transformed; only existing Rollup annotation and chunk-size warnings).
- Implemented task 5.4 OpenSpec consistency validation.
- Verification: `openspec validate code-agent-workspace-conversation-ui --strict` → **Change 'code-agent-workspace-conversation-ui' is valid**.

### Completion

- All 16 OpenSpec tasks are implemented, verified, and checked in `tasks.md`.
- Main specs were synced and validated before archive.
- Change archived to `openspec/changes/archive/2026-08-29-code-agent-workspace-conversation-ui/`.

### Post-archive bug fixes

- 修复 Workspace 预览在 retained workspace 缺失 `.git` 时误报 `workspace_mount_invalid`：业务树/文件仍可读，Git 元数据单独显示不可用。
- 修复左侧目录无法展开：目录改为按需加载并递归展示；Git changed files 映射为树节点高亮和“已修改”标记。
- Verification: 控制面回归 **57 passed**；`npm run build` **成功**；`git diff --check` 无输出。

### Post-archive runtime progress fix

- 修复 AgentChat Code Runtime 事件状态：`runtime_started: started` 显示为已启动；同一阶段的 `started → completed/failed` 更新原步骤，避免旧步骤持续显示 loading。
- Verification: `pytest apps/api/tests/test_code_agent_control_plane_ui.py`（在 `apps/api` 目录执行为 `57 passed`）；`npm run build`（`apps/web`）成功；`git diff --check` 无输出。

### Post-archive workspace readiness fix

- 修复发送消息后 Workspace 请求早于后台仓库挂载的竞态：`workspace_not_prepared` 显示为“正在准备，等待仓库挂载”，面板自动重试并在 ready 后加载树；重试有 60 次上限，过期/权限/挂载错误不重试。
- Verification: `pytest tests/test_code_agent_control_plane_ui.py`（在 `apps/api` 目录执行为 `57 passed`）；`npm run build`（`apps/web`）成功。

### Post-archive workspace preview correctness fix

- 修复 Git 预览向上层宿主仓库漂移的问题：严格校验 Git root 等于当前 Workspace；存在 Run 基线时按 Workspace 文件快照返回真实新增/删除/修改文件，避免 `Git snapshot · 454` 误报。保留态无 `.git` 时继续显示 Git metadata unavailable。
- Verification: `pytest tests/test_code_agent_workspace_preview.py tests/test_code_agent_control_plane_ui.py`（在 `apps/api` 目录执行为 `64 passed`）；前端此前构建成功；`git diff --check` 无输出。

### Post-archive runtime history and failure visibility fix

- 持久化脱敏 Code profile 事件到 `runner_facts.code_profile_events`，`get_code_events` 优先返回该历史；发送后主动回放事件，补充 `runtime_result` 阶段显示 Claude Code 成功/失败原因。保留 Claude Code 失败时不产生虚假文件修改。
- Verification: `pytest tests/test_code_agent_runtime_context.py::test_code_profile_events_keep_common_envelope_with_versioned_payload tests/test_code_agent_claude_code_runtime.py tests/test_code_agent_control_plane_ui.py tests/test_code_agent_workspace_preview.py` → **108 passed**；`npm run build`（`apps/web`）成功；`git diff --check` 无输出。

### Post-archive Claude terminal event and durable process fix

- Claude Code 独立分支现在在清理后发送共享 `done` 事件，并将用户/助手结果与脱敏 Runtime 步骤写入聊天历史；前端将持久化事件以独立 Runtime 过程面板展示，刷新后仍可见，不再依赖正在运行的 spinner。
- `prepare`/`runtime_started` 的 started 事件按启动确认处理为完成态；Claude 失败仍展示失败原因且不会伪造文件修改。
- Verification: focused backend/UI regression **101 passed**；`python -m compileall` 成功；`npm run build`（`apps/web`）成功；`git diff --check` 无输出。
- Cleanup failure path was included in the terminal-event handling; focused regression after this adjustment: **60 passed** for control-plane/runtime checks, compileall and `git diff --check` clean.
- Active-run polling now replays persisted events as a WebSocket race fallback; latest control-plane regression: **58 passed** and frontend Vite build succeeded.
- Claude Code model binding now rejects OpenAI-compatible resources (including MiniMax-M3) during preflight with actionable `select_anthropic_llm` guidance, before Workspace coding starts. Regression: Claude runtime/results/UI tests **110 passed**.
- Reused the existing LLM management flow only: added an Anthropic Claude preset and contextual configuration hint; no new LLM type, gateway, fallback, or routing feature. Regression: **111 passed**; frontend build succeeded.
- Claude API `401`/`unrecognized_model` output is now classified as `model_unavailable` before presenting the result as a coding failure. Regression: **113 passed**, compileall and `git diff --check` clean.
- Claude direct Workspace edits are now authorized only after baseline/checkout/containment validation and only inside frozen `allowed_paths`; missing Claude resume sessions fall back to a fresh retry session with verifier feedback. Regression: **116 passed**, compileall and `git diff --check` clean.
- 修复 CodeAgent 左侧预览未刷新的问题：`AgentChat` 绑定 `CodeWorkspacePanel` 引用，并统一在 WebSocket `done`、运行结束轮询和状态转为空闲后重新加载 run-bound Workspace；标准 Agent 仍保留原 `WorkplacePanel`。
- Verification: `pytest tests/test_code_agent_control_plane_ui.py tests/test_code_agent_workspace.py tests/test_code_agent_claude_code_runtime.py -q` → **115 passed**；`npm run build`（`apps/web`）成功；`python -m compileall -q app` 与 `git diff --check` 通过。
- 将 `workspace_integrity_error` 设为 Verifier 的不可重试终态，避免安全边界失败后继续 Claude 修复并再次触发无效 session resume；新增回归测试验证只执行初始 coding attempt。
- Verification: 新增测试单独通过；相关整组回归 **142 passed, 1 pre-existing failure**（`test_workspace_prepare_failure_cleans_partial_allocation_and_is_terminal` 使用旧 `_materialize` mock 签名，与本次修改无关）。
- 排除该已知旧 mock 签名测试后复跑整组回归：**142 passed, 1 deselected**；`python -m compileall -q app` 与 `git diff --check` 通过。
- 修复封存后 Workspace 被清理导致左侧预览失效：`cleanup_allocated_workspace` 现在对 `sealed` 与 `prepared` 一样按现有 retention TTL 保留为 `retained_read_only`；仅到期后清理。对历史上已删除的 `sealed` 目录，元数据 API 返回 `workspace_expired` 而非误报 `workspace_mount_invalid`。
- Verification: `test_allocated_sealed_workspace_is_retained_for_run_bound_preview`、`test_sealed_run_without_retained_workspace_reports_expired_not_mount_invalid` 与现有 acceptance 测试 **3 passed**。
- 最新实际 Run `f513eb3a` 已确认：状态为 `patch_ready`，但 `workspace_state=sealed` 且目录已被旧清理逻辑删除；因此旧 Run 无法恢复左侧树，只能显示已过期并通过封存 artifact 查看补丁。修复已覆盖后续 Run。
- 修复 Claude SOP 注入时序：`/workspace/.claude/CLAUDE.md` 现在在启动 Code Sandbox 前写入，避免 Docker 后端在容器启动时捕获空的 Workspace；移除启动后的重复写入。新增顺序回归测试确认 SOP 先于 `runner.start`。
- Verification: Claude preflight/顺序、Standard Agent 兼容、sealed Workspace retention、历史状态映射 **4 passed**；compileall 与 `git diff --check` 通过。
- 增强执行中可见性：Claude CLI 阻塞调用前立即发布 `runtime_result: started`，完成后以同一阶段更新为 completed，并回放 Adapter 返回的脱敏 tool/test/file 事件；实时对话过程面板因此在任务执行期间显示“Claude Code Runtime 执行中”，而非仅在终态汇总。
- Verification: Claude retry/live-event 回归 **2 passed**；compileall 与 `git diff --check` 通过。
- 修复 Anthropic LLM 测试请求错误使用 OpenAI `/chat/completions` 导致 404：现按 provider 分流到原生 `/v1/messages`，发送 Anthropic 鉴权头并解析原生 content 响应；OpenAI/MiniMax 路径保持不变。
- Verification: `pytest tests/test_llm_anthropic.py tests/test_llm_native_tools.py tests/test_llm_transport_retry.py -q` → **18 passed**；`python -m compileall -q app` 与 `git diff --check` 通过。
- Claude Code 的 `ANTHROPIC_BASE_URL` 同步采用 Anthropic 基地址规范化，兼容已有带 endpoint 后缀的 LLM 配置。
- Verification: Anthropic、Claude SOP 运行环境、原生工具调用与传输重试回归合计 **34 passed**；compileall 与 `git diff --check` 通过。
- 移除独立的 Claude Code Runtime 过程 UI；复用现有对话“执行过程”卡片承载实时及历史 Code profile 事件，保留终态结果与证据。
- Verification: `pytest tests/test_code_agent_control_plane_ui.py` → **60 passed**；`npm run build`（`apps/web`）成功；`git diff --check` 通过。
- 增加 Claude Code 流式过程：Runner 支持可选 Docker exec 输出回调；Claude Adapter 在流式模式下解析 `stream-json` 工具块，运行时即时发布脱敏的 tool/test/file profile 事件，继续复用对话执行过程卡片。
- Verification: `pytest tests/test_code_agent_runner.py tests/test_code_agent_claude_code_runtime.py tests/test_code_agent_control_plane_ui.py` → **127 passed**；`python -m compileall -q app`、`git diff --check` 与 `npm run build` 均通过。
- 文档已补充 Claude Code 流式步骤说明；执行过程仍统一显示在对话消息卡片中。
- 修复流式 Docker exec 的 `runner_exec_failed`：使用容器对象 ID 创建 exec，并在旧 Docker daemon 无法协商流式输出时安全回退同步执行。
- Verification: 流式成功/回退测试 **3 passed**；Runner、Claude Runtime、控制面回归 **128 passed**；compileall、`git diff --check` 和前端构建均通过。
- 修复流式 EOF/观察者异常被误报为 `runner_exec_failed`：隔离 UI 事件回调、容忍 Docker stream 在进程完成后的连接重置，并轮询 `exec_inspect` 获取终态退出码；Runner 结果附带脱敏异常类型。
- Verification: `pytest tests/test_code_agent_runner.py tests/test_code_agent_claude_code_runtime.py` → **71 passed**；待部署环境重新执行一次 CodeAgent 任务确认 Docker 后端行为。
- Verification refresh: after bounded `exec_inspect` retry handling, the same Runner/Claude Runtime tests remain **71 passed**; `python -m compileall -q app` and `git diff --check` pass.
- 针对实际 `runner_exec_failed (OSError)` 增加流对象关闭异常隔离；即使 Docker socket 在 EOF 后的 `close()` 抛出 OSError，也继续使用 exec inspect 的退出码完成结果解析。
- Verification: Runner、Claude Runtime、控制面回归 **132 passed**；`python -m compileall -q app` 与 `git diff --check` 通过。
- 为 Docker stream 断连后仍在收尾的进程增加最多 30 秒的 `exec_inspect` 宽限轮询，避免 1 秒窗口过早报告 `OSError`。
- Verification refresh: Runner/Claude Runtime **72 passed**；`python -m compileall -q app` 与 `git diff --check` 通过。
- Runner 的 OSError 诊断现在额外保留 errno（仍不记录命令或凭证内容）；回归测试保持通过。
- 每次 Claude Code 执行的 assistant 输出追加 Patch 验证摘要；不自动执行 commit/push，明确提示通过下一条对话操作。执行阶段详情支持同一行显示脱敏摘要并以省略号截断。
- Verification: `tests/test_code_agent_results.py` **9 passed**；前端 `npm run build` 成功；新增 UI 断言覆盖 inline stage detail。
- Verification refresh: Claude Runtime、结果序列化与控制面测试 **118 passed**；`python -m compileall -q app`、`git diff --check` 与 `apps/web` 前端构建均通过。
- 移除 AgentChat 顶部独立 Code result banner 与工件审阅弹窗；将完整 Patch 验证结果（运行状态、Manifest、Runtime/Skills/MCP、Verifier/Artifact 证据、失败信息、Host 验证命令）作为助手消息 Markdown 输出。终态 WebSocket 改为传递完整结果，不再截断为 500 字。
- Verification: CodeAgent 结果与控制面回归 **70 passed**；Claude Runtime/结果回归 **57 passed**；`python -m compileall -q app`、`git diff --check` 与 `apps/web` `npm run build` 均通过。
- Verifier evidence 在进入对话 Markdown 前同样经过脱敏；结果序列化回归追加 secret scanner excerpt 覆盖，`tests/test_code_agent_results.py` **9 passed**。
- 修复第二轮澄清交互清空左侧 Workspace 的问题：无新 Run ID 时保留上一轮 Workspace，有新 Run 时切换；页面初始化和会话切换会恢复当前会话最新 Run。控制面回归 **71 passed**，前端构建成功。
- 修复成功 `patch_ready` Run 被错误显示为 `infrastructure_error · Stage internal`：兼容历史 `failure_reason=patch_ready` 终态标记，不再生成虚假内部故障。结果/控制面/Claude Runtime 回归 **119 passed**，compileall 与 diff 检查通过。
- 修正 Claude Runtime 成功结果中“模型预检通过但 reason=model_preflight_failed”的矛盾展示；失败原因仅在预检失败时填充。结果回归 **9 passed**。
- 修复刷新后 CodeAgent 执行过程不可见：加载历史后自动展开最近一条执行卡片并恢复完整 message steps；运行中的 Run 仍回放持久化 profile events。控制面/UI **63 passed**，前端构建成功。
- 修复 Runtime 长时间 started 的两个根因：无网络 Sandbox 禁止 Claude 反复安装依赖并为包管理器设置非交互超时；服务重启恢复 pending/running Run 时补写 `runtime_result: failed` 与 cleanup 终态事件。生命周期/控制面/Claude Runtime 回归 **115 passed**。
- 对已终止但历史只留下 `runtime_result: started` 的 Run，事件接口动态补发终态失败事件，避免刷新后继续显示转圈。相关生命周期/控制面/Claude Runtime 回归 **131 passed**。
- 增加 Patch 验证结果前的阶段代码片段：EDIT 展示修改前后、TEST/工具结果展示安全输出；片段经过 UTF-8 清洗、脱敏和长度限制，前端以代码块原样渲染避免乱码。验证：Claude Runtime/结果测试 **59 passed**，`python -m compileall -q app`、`git diff --check` 与 `apps/web` `npm run build` 均通过。
- 修复 Claude `stream-json` 的 `system/init` 原始 JSON 泄漏到用户消息：摘要解析过滤协议事件，历史执行步骤保留阶段片段且继续脱敏/限长。验证：Claude Runtime、结果与控制面 **123 passed**，compileall、`git diff --check` 和前端构建均通过。
- 增加历史消息兼容过滤：前端隐藏已落库的 Claude `system/init` 协议 JSON，避免旧消息继续显示原始初始化对象。前端构建成功。
- 兼容被旧摘要长度限制截断的 `system/init` JSON 残片；前端构建成功。
- CodeAgent 对话现在默认直接使用 Claude Code 引擎创建 Run，不再要求输入“开始执行:”；旧前缀仍可选兼容。左侧 Workspace 文件预览改为 Markdown 美化显示。验证：相关后端回归 **139 passed**，compileall、`git diff --check` 和前端构建均通过。
- 修复左侧 Markdown 预览代码块复制按钮：添加事件委托、剪贴板权限失败回退和成功/失败提示。前端构建成功。
- 新建并验证 OpenSpec change `code-agent-workspace-markdown-direct-runtime`：proposal/specs/design/tasks 完整，8/8 tasks 已完成；`openspec validate --strict` 通过。基于实现、回归测试和构建结果完成 verify，未发现阻断性问题。
- 已将 `code-agent-workspace-markdown-direct-runtime` 的 delta specs 同步至主规格并完成严格校验，随后归档至 `openspec/changes/archive/2026-08-30-code-agent-workspace-markdown-direct-runtime/`。
- 已创建 OpenSpec change `code-agent-claude-run-v2`，完成 proposal、6 份 spec delta、design 与 tasks；严格校验通过，尚未执行 implementation。

### Post-archive refresh execution-history fix

- 刷新后「执行过程」只显示 `CodeAgent 运行阶段 · started/completed`：历史卡片读的是落库精简 steps（通用标题且丢掉 snippet）。现改为优先用 `get_code_events` 按与实时相同的阶段标签回放；新消息落库也使用阶段标题并保留 code snippet。
- Verification: 新增步骤标题测试与刷新回放断言通过；`test_code_agent_control_plane_ui.py` 相关用例 **60 passed**。4 个失败是沙箱内 `git init` 无权限，与本次改动无关。

## OpenSpec Task Status

| Task range | Phase | Status |
|---|---|---|
| 1.1–1.4 | CodeAgent Workspace API | completed |
| 2.1–2.3 | CodeAgent Conversation Progress | completed |
| 3.1–3.4 | Frontend Workspace and Conversation UI | completed |
| 4.1–4.4 | Integration, Security, and Compatibility | completed |
| 5.1–5.4 | Acceptance and Documentation | completed |
