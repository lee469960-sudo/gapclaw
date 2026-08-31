## 1. 持久 Sandbox 与 Workspace

- [x] 1.1 复用 CodeAgent 编辑页既有 Sandbox 绑定，校验 Sandbox 运行状态；停止时返回手动启动提示，不自动创建/启动 Sandbox。
- [x] 1.2 为 Code Project 建立稳定 Workspace 挂载路径和项目级单写入者，并让 Sandbox 终端、Claude Code、Code Tools 与 Workspace 预览使用同一路径。
- [x] 1.3 将 Claude Code 与代码工具调度从每 run runner 切换到绑定持久 Sandbox，保留现有 Agent 编辑页模型、Skill、MCP、策略和资源配置。

## 2. 收敛 Manifest 与 UI

- [x] 2.1 将 Manifest API/发布校验收敛为 Git 来源、凭据、Ref、resolved commit 与快照；新任务不再依赖镜像、路径、验证计划、预算或运行策略字段。
- [x] 2.2 优化 Manifest UI：仅保留 Git 连接和版本基线，移除运行镜像、工具、路径、验证计划、预算、策略和 local 发布相关字段/提示。
- [x] 2.3 更新左侧 Workspace 与 CodeAgent 状态 UI，显示绑定 Sandbox 状态、稳定挂载目录和未运行时的手动启动提示。

## 3. 删除特化流程并保留开发反馈

- [x] 3.1 删除独立 runner、临时 root/bootstrap、local publish、命令发现、预检、确认、发布证据和专用事件/环境配置/测试；保留历史记录只读兼容。
- [x] 3.2 删除发布专用 Verifier/Sealer 门禁，使 Claude Code 结果展示普通文件变更、测试和脱敏验证事实；测试失败仍可按现有 retry 设置反馈 Claude Code。
- [x] 3.3 更新结果与对话输出，不再产生 `local_publish_*` 原因或发布型转圈状态。

## 4. 回归与文档

- [x] 4.1 更新 Sandbox/Workspace/Manifest 使用说明，明确工具由持久 Sandbox 维护、停止 Sandbox 需要用户手动启动。
- [x] 4.2 运行后端 Sandbox/Workspace/Manifest/Claude Code 回归、前端构建、`git diff --check` 与 OpenSpec 严格校验，并记录实际结果。

## 5. Manifest 轻量发布与 Sandbox 内 Git 同步

- [x] 5.1 将 Manifest publish 收敛为只发布 Git 来源、凭据引用和请求 Ref；不再导入仓库、扫描源码或封存 snapshot。
- [x] 5.2 创建 CodeAgent Run 时不再要求 resolved commit、source scan report 或 snapshot 证据；运行时保留仓库、凭据引用和请求 Ref。
- [x] 5.3 Workspace 准备阶段在绑定持久 Sandbox 内执行 git clone/fetch/checkout，并记录实际 commit；失败时返回 Git/凭据/网络相关原因，而不是 `source_scan_failed`。
- [x] 5.4 同步 Manifest/Workspace UI 和文档文案，移除发布时 snapshot/source scan 语义，并完成相关回归验证。
