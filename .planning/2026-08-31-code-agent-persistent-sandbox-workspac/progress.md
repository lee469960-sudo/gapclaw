# Progress: code-agent-persistent-sandbox-workspac

## 2026-08-31

- 完成 OpenSpec 5.1：`ManifestPublishService` 收敛为 Git 配置发布，不再构造 repository acquirer、source scanner 或 snapshot store。
- 完成 OpenSpec 5.2：CodeAgent Run 创建不再要求 resolved commit、source scan report 或 snapshot 证据；合同保留 repository、credential_ref、requested_ref，运行时再解析实际 commit。
- 完成 OpenSpec 5.3：Workspace 准备后在绑定持久 Sandbox 内执行 Git sync，并记录实际 commit；Git/凭据/网络错误返回对应 repository 类原因。
- 完成 OpenSpec 5.4：Manifest UI 历史视图改为 Git 摘要；对话 UI 移除 local publish/封存误导文案；文档同步为 Manifest/Sandbox/Workspace 新边界。

## Verification

- `pytest -q apps/api/tests/test_code_agent_manifest_publish.py apps/api/tests/test_code_agent_control_plane_ui.py apps/api/tests/test_code_agent_control_plane.py`：95 passed。
- `python -m compileall apps/api/app/services/code_agent apps/api/app/routers apps/api/app/services/agent_runtime`：通过。
- `pytest -q apps/api/tests/test_code_agent_manifest_publish.py apps/api/tests/test_code_agent_control_plane.py apps/api/tests/test_code_agent_control_plane_ui.py apps/api/tests/test_code_agent_runtime_context.py apps/api/tests/test_code_agent_workspace*.py apps/api/tests/test_code_agent_runner.py apps/api/tests/test_code_agent_results.py apps/api/tests/test_code_agent_artifacts.py apps/api/tests/test_code_agent_claude_code_runtime.py apps/api/tests/test_code_agent_secure_schema.py`：295 passed。
- `npm --prefix apps/web run build`：通过；仅保留既有 Rollup pure annotation/chunk size warnings。
- `git diff --check`：通过。
- `openspec validate code-agent-persistent-sandbox-workspac --strict`：通过。
- `openspec instructions apply --change code-agent-persistent-sandbox-workspac --json`：15/15 complete，state=`all_done`。

## Post-implementation defect fix: empty persistent workspace

- 修复绑定持久沙箱时的启动顺序：`CodeContainerRunner.start()` 不再在 Git sync 前探测 `claude --version`，避免缺少 Claude CLI 时阻断仓库同步；Claude CLI 可用性继续由后续 Claude preflight 暴露。
- 修复 Workspace 状态判断：`persistent_git_sync` 模式下目录存在但未形成 `.git` 时，不再返回 `ready`；运行中显示 `workspace_not_prepared`，终态失败显示 `workspace_prepare_failed` 并带上失败原因。
- 修复 CodeAgent 左侧沙箱入口：`CodeWorkspacePanel` 增加“打开沙箱终端”，使用 Agent 编辑页绑定的 sandbox id，ready 后工作目录指向 run 的 workspace mount。
- 修复刷新恢复逻辑的数据源：`serialize_code_result()` 对 `persistent_git_sync` 未完成的空目录返回 `workspace.available=false`，避免旧 `startup_failed` run 继续占住左侧 Workspace。
- Verification：`pytest -q apps/api/tests/test_code_agent_runner.py apps/api/tests/test_code_agent_control_plane_ui.py --maxfail=5`：95 passed。
- Verification：新增结果层回归后，`pytest -q apps/api/tests/test_code_agent_runner.py apps/api/tests/test_code_agent_control_plane_ui.py --maxfail=5`：96 passed。
- Verification：`npm --prefix apps/web run build`：通过；仅保留既有 Rollup pure annotation/chunk size warnings。
- Verification：`git diff --check`：通过。
- Verification：用当前代码序列化历史失败 run `f7dcd497`，结果为 `workspace.available=false`，不会再被刷新恢复为左侧可用 Workspace。
- 修复新 run `459be054` 的 runner 启动失败原因：绑定持久沙箱启动路径不再访问 `container.image`，改用 Docker inspect 的 `Config.Image` 或沙箱配置 image 记录事实，避免本机缺少旧 digest 镜像对象导致 `ImageNotFound -> startup_failed`。
- Verification：新增缺失 image object 回归后，`pytest -q apps/api/tests/test_code_agent_runner.py apps/api/tests/test_code_agent_control_plane_ui.py --maxfail=5`：97 passed。
- Verification：`npm --prefix apps/web run build`、`python -m compileall apps/api/app/services/code_agent apps/api/app/routers`、`git diff --check` 均通过。
- 修复新 run `6676da3e` 的 `repository_sync_failed` 误判：Git 同步命令 exit 0 但输出中缺少可解析完整 commit 时，额外执行 `git rev-parse HEAD` 读取当前 HEAD 作为兜底。
- 修复持久沙箱 Run 的后台清理：janitor 识别 `workspace_mode=persistent_sandbox` 后只释放 Run 路由为 `released/completed`，不删除绑定沙箱 container，不把 Workspace retain/read-only。
- Verification：新增 Git HEAD 兜底和持久沙箱 janitor 回归后，`pytest -q apps/api/tests/test_code_agent_workspace.py apps/api/tests/test_code_agent_lifecycle_janitor.py apps/api/tests/test_code_agent_runner.py apps/api/tests/test_code_agent_control_plane_ui.py --maxfail=5`：114 passed。
- Verification：`python -m compileall apps/api/app/services/code_agent apps/api/app/routers`、`git diff --check` 均通过。
- 修复 Docker SDK tuple bytes 输出解码：`CodeContainerRunner.exec()` 不再对 tuple 输出使用 `str(bytes)`，改为 UTF-8 decode 并保留原始换行；同时 `_decode_exec_output()` 不再全局 strip，避免裁剪命令输出。
- Verification：新增 tuple bytes 输出回归后，`pytest -q apps/api/tests/test_code_agent_workspace.py apps/api/tests/test_code_agent_lifecycle_janitor.py apps/api/tests/test_code_agent_runner.py apps/api/tests/test_code_agent_control_plane_ui.py --maxfail=5`：115 passed。
- Verification：`python -m compileall apps/api/app/services/code_agent apps/api/app/routers`、`git diff --check` 均通过。
- 修复左侧 Workspace 预览恢复：`AgentChat.vue` 向 `CodeWorkspacePanel` 传入 `sessionId`；`CodeWorkspacePanel` 在无 active `codeRunId` 时通过 `get_code_result(agent_id, session_id)` 回填最近一次 `workspace.available=true` 的 Code run，从而沙箱中已有仓库时刷新/失败后仍可直接预览。
- Verification：`pytest -q apps/api/tests/test_code_agent_workspace.py apps/api/tests/test_code_agent_lifecycle_janitor.py apps/api/tests/test_code_agent_runner.py apps/api/tests/test_code_agent_control_plane_ui.py --maxfail=5`：115 passed。
- Verification：`python -m compileall apps/api/app/services/code_agent apps/api/app/routers`、`git diff --check`、`openspec validate code-agent-persistent-sandbox-workspac --strict` 均通过。
- Verification：`npm --prefix apps/web run build`：通过；仅保留既有 Rollup pure annotation/chunk size warnings。
- 运行时诊断：绑定沙箱当前容器仍为旧镜像 `code-agent-runner:1`，容器内缺少 `claude/node/npm`，所以 Claude Code preflight 返回 `claude_code_cli_unavailable`。新镜像 `code-agent-runner-arm:v0.0.1` 已验证包含 Claude Code CLI、Node 和 npm；需要把 Agent 绑定沙箱切换/重建到该镜像后，Claude Code Runtime 才能启动。
- 修复 Claude 已改文件但最终输出“变更文件 无 / infrastructure_error”：`allowed_paths=["."]` 现在正确表示允许仓库根目录下所有路径；运行异常处理保留 Verifier/Workspace 已写入的具体终态，不再统一覆盖为 `failed`；结果层在 verifier 报告缺失时从 Claude `file_changed` 事件兜底提取变更文件，并把“编码完成但验证未落证据”的旧失败展示为 `verification_inconclusive`。
- Verification：实际旧 run `fbc9070a` 重新序列化后显示 `gamestat/dbt_project.yml`，状态为“验证不充分”，不再输出 `infrastructure_error`。
- Verification：`pytest -q apps/api/tests/test_code_agent_workspace.py::test_integrity_guard_treats_dot_allowed_path_as_workspace_root apps/api/tests/test_code_agent_results.py::test_patch_output_falls_back_to_file_changed_events_when_verifier_report_missing --maxfail=5`：2 passed。
- Verification：`pytest -q apps/api/tests/test_code_agent_workspace.py apps/api/tests/test_code_agent_results.py apps/api/tests/test_code_agent_control_plane_ui.py apps/api/tests/test_code_agent_runner.py apps/api/tests/test_code_agent_claude_code_runtime.py --maxfail=5`：174 passed。
- Verification：`python -m compileall apps/api/app/services/code_agent apps/api/app/services/agent_runtime apps/api/app/routers`、`git diff --check`、`openspec validate code-agent-persistent-sandbox-workspac --strict` 均通过。
- 已归档 OpenSpec change `code-agent-persistent-sandbox-workspac` 至 `openspec/changes/archive/2026-08-31-code-agent-persistent-sandbox-workspac/`；CLI 同步主规格，统计为 added=3、modified=5、removed=3、renamed=0。
- Archive verification：`openspec validate --specs --strict`：18 passed；归档路径存在，原 active change 目录已移除；`git diff --check openspec/specs openspec/changes/archive/2026-08-31-code-agent-persistent-sandbox-workspac` 通过。`openspec validate --changes --strict` 中 `code-agent-standard-local-publish` 仍为无关既有失败，不属于本次归档目标。
