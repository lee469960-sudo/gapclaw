# Findings: code-agent-persistent-sandbox-workspac

## 2026-08-31

- Manifest 发布主路径已改为 Git-config-only：校验 source/source_type/repository/credential_ref/requested_ref，发布时清空 `source_scan_report_id`、`base_commit`、`resolved_commit`、`snapshot_id` 和 `snapshot_hash`。
- `manifest_publish.py` 仍残留旧 import/scan/seal 类和构造依赖；已删除，避免后续维护或测试把旧管线拉回。
- `test_code_agent_manifest_publish.py` 仍验证旧 import → scan → seal 顺序；已改为验证 Git 配置发布、无仓库内容检查、失败无 scan/snapshot 副作用。
- `test_code_agent_control_plane.py` 旧 fixture 未绑定运行中 Sandbox；新架构下这会错误地命中 `sandbox_not_configured`。已更新 fixture 绑定 running Sandbox，并将 Run 合同断言调整为 requested ref 作为启动基线，实际 commit 由运行时 Git sync 写回。
- Manifest 历史 UI 原先用整条 JSON 展示，会暴露旧 `snapshot/source_scan/allowed_paths/budgets` 字段；已改为只展示 Git 配置摘要。
- `AgentChat.vue` 仍有旧 `publish: 执行 local 发布` 和“封存工件”文案；已改为中性运行步骤/结果工件文案。
- 线上复现的空 Workspace 不是仓库地址不可达：绑定持久沙箱 `/workplace/code/ae834a47/workspace` 存在但为空，Run 失败在 `startup_failed`；原因是 `CodeContainerRunner.start()` 在 Git sync 前先执行 `claude --version`，绑定沙箱缺少 Claude CLI 时启动直接失败，后续仓库同步不会执行。
- `get_code_workspace` 原来只要目录存在就返回 `ready`，对 `persistent_git_sync` 但未形成 `.git` 的空目录会误导前端显示“文件树为空 / git_metadata_missing”。该状态应显示为 `workspace_not_prepared` 或终态失败时的 `workspace_prepare_failed`。
- CodeAgent 左侧 Workspace 需要保留普通沙箱进入能力；入口应使用 Agent 编辑页绑定的 sandbox id，Workspace ready 后优先使用 run metadata 中的 sandbox/workspace mount。
- `serialize_code_result()` 原先只根据 `workspace_path` 和 `workspace_state` 判断 `workspace.available`；对 `persistent_git_sync` 未完成的空目录会返回可用，导致页面刷新后又恢复旧 `startup_failed` run 到左侧 Workspace。
- 绑定沙箱 `70d7c281` 当前状态 running、`/workplace` 可写、git 已安装；旧 run `f7dcd497` 的 workspace 仍为空目录，属于历史失败状态，不会自动补 Git sync。
- 新 run `459be054` 的 `startup_failed` 发生在 runner 启动阶段，日志显示 `container.image` 触发 Docker SDK inspect 镜像 digest：本机缺少 `sha256:aed8f...` 镜像对象，抛 `ImageNotFound`。绑定持久沙箱已经是 running container，不应为了展示 image tag 再依赖本地 image object。
- 新 run `6676da3e` 已越过 runner start，失败在 `sync_repository_in_sandbox()` 的 commit 解析：审计摘要显示 `git fetch/checkout` 实际执行到 `HEAD is now at a9787bc...`，沙箱内 `git rev-parse HEAD` 确认为 `a9787bc492b1bff0dc3d8f5bace2037e469ed0fd`。平台报 `repository_sync_failed` 是同步命令成功后未从合并输出中解析出完整 commit 的误判。
- 持久沙箱 Run 失败后后台 janitor 仍按临时 runner 逻辑尝试删除 `container_id`，而绑定沙箱容器没有 `code_agent.run_id` 标签，会触发 `container_binding_mismatch` 并把 run 标成 `cleanup_failed`。持久沙箱模式应释放 Run 路由，不删除/锁定共享沙箱和 Workspace。
- 后续 run `8e493e57` 的审计详情包含完整 commit `a9787bc492b1bff0dc3d8f5bace2037e469ed0fd`，但仍抛 `repository_sync_failed`；原因是 Docker SDK tuple 结果中的 bytes 被 `str(bytes)` 转成 `b'...\\n...'` 字面量，业务层无法按真实换行解析 commit。
- 最新 run `3d6e8daa` / `54c92575` 已进入 `workspace_state=prepared`，`repo_root_mode=sandbox_git_synced`，沙箱内 `/workplace/code/ae834a47/workspace` 已有 `.git`、`.gitlab-ci.yml`、`README.md`、`gamestat`、`profiles.yml`，说明仓库挂载/同步问题已解决。
- 左侧 Workspace 之前仍显示等待，是因为页面只依赖当前 active run id；刷新或 run 失败后若当前消息没有 active run id，就不会主动读取 session 的最新可用 workspace。前端需要在无 active run id 时从 `get_code_result(agent_id, session_id)` 回填最近一次 `workspace.available=true` 的 run id。
- `claude_code_cli_unavailable` 当前不是新镜像未安装，而是绑定沙箱 `70d7c281` 的运行中容器仍使用旧镜像 `code-agent-runner:1`；该容器内 `claude/node/npm` 均不存在。新构建镜像 `code-agent-runner-arm:v0.0.1` 已验证包含 `/usr/local/bin/node`、`/usr/local/bin/npm` 和 `/usr/local/bin/claude`，`claude --version` 返回 `2.1.246 (Claude Code)`。
- 数据库中沙箱 `70d7c281` 的 `image` 字段已是 `code-agent-runner-arm:v0.0.1`，但 Docker 正在运行的容器 `gap-sandbox-70d7c281` 仍是 `Config.Image=code-agent-runner:1`。这说明只是修改了沙箱记录/配置，没有重建容器；Docker 不会把已存在容器自动升级到新镜像。
- 最新 run `fbc9070a` 已使用新沙箱 `38683766/code-agent-dbt` 和新镜像 `code-agent-runner-arm:v0.0.1`，Claude Code preflight 通过，Claude 实际修改了 `gamestat/dbt_project.yml`，但最终输出显示“变更文件 无 / infrastructure_error”。根因有三层：`allowed_paths=["."]` 未被当作仓库根目录全量允许，导致 runtime 写入被误判为 `workspace_external_write`；外层异常处理把已写入的具体状态覆盖成泛化 `failed`；结果层在 `verifier_report={}` 时没有从 Claude `file_changed` 事件兜底提取变更文件。
