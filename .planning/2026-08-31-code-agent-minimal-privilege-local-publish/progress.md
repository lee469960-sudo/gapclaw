# Progress: code-agent-minimal-privilege-local-publish

## 2026-08-31

- 初始化执行记录，读取 proposal、design、全部 specs 和 tasks。
- `openspec instructions apply`：0/8 完成，状态 `ready`。
- 已核对工作树：存在来自上一轮标准 local 发布方案的未提交改动；将仅在当前 OpenSpec 任务范围内替换这些实现。
- 完成 1.1：移除命令注册表、标准项目档案配置、Compose 注入及控制面冻结依赖；保留 Manifest 不含 local 发布字段。验证：控制面/UI 与 Verifier 聚焦回归 `86 passed`。
- 完成 1.2：Manifest 页面改为仓库自动发现说明，环境示例删除平台档案配置。验证：前端 `npm run build` 通过（仅既有 Rollup chunk-size 提示）。
- 完成 2.1–2.2：runner 仅对明确 local 发布任务开放固定包白名单的 root `apt-get` argv；普通工具与发布仍走非 root `exec_argv`。Python/Node 锁定依赖安装在 `/tmp/code-agent-run-local`。验证：runner 与依赖准备回归 `29 passed`，`compileall` 通过。
- 根据用户明确决定修订 OpenSpec：删除平台入口发现门禁。完成 3.1：local 发布直接进入 Claude Code Runtime，移除运行时追加发布阶段；Claude Code 的 local 发布提示不再包含平台受控发布禁令。验证：Claude/发布/控制面回归 `123 passed`，OpenSpec 严格校验通过。
- 根据用户明确决定，Claude Code runner 改为容器内 root：不再使用受限安装桥接；Claude Code、Code Tool 和仓库命令可直接安装依赖。容器仍非 privileged、无 Docker socket/宿主挂载。验证：runner 回归 `26 passed`、`compileall` 通过。
- 清理旧平台发布遗留：删除 Verifier 的 `release_id`/`local_publish_evidence_missing` 专项门禁、相关事件与失败映射，移除 Manifest 的自动发现提示。验证：相关后端回归 `84 passed`，前端构建通过。
- Claude Code local 发布提示已明确要求：在 root 一次性沙箱中遇到缺命令时自行安装系统/应用依赖，重试该命令并继续任务；无最终文字但有 stream 工具事件时输出稳定中文摘要。验证：Claude Runtime、runner、控制面回归 `145 passed`，`compileall` 与 `git diff --check` 通过。
