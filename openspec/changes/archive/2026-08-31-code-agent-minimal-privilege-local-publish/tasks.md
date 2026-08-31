## 1. 移除标准发布档案复杂度

- [x] 1.1 移除 `CODE_STANDARD_LOCAL_PUBLISH_PROFILES`、`CODE_LOCAL_PUBLISH_COMMANDS` 及其解析、控制面冻结和 Runtime 依赖；保留 Manifest 不含 local 发布字段，并通过控制面回归验证。
- [x] 1.2 移除平台档案相关的环境示例和 Manifest/UI 提示，改为说明仓库自动发现；通过前端组件与环境配置测试验证。

## 2. 持久 Sandbox 内的 Claude Code 执行

- [x] 2.1 让 Claude Code 在已绑定的持久 Sandbox 中运行；拒绝 privileged、Docker socket 与宿主挂载，并通过 runner 隔离测试验证。
- [x] 2.2 不由平台扫描 lockfile 安装依赖；Claude Code 在 Sandbox 权限允许时自行安装，且不得把安装产物当作业务 Git 交付。

## 3. 仓库驱动的自动 local 发布

- [x] 3.1 移除 local 发布的 Runtime 入口发现与 `local_publish_entry_not_found` 门禁；明确请求须直接进入 Claude Code Runtime，并通过 Runtime 单元测试验证。
- [x] 3.2 复用现有认证上下文、脱敏输出、Verifier、Sealer 和清理；未知结果不由 adapter 自动重试，由现有 Runtime/结果测试覆盖。

## 4. 可观察性与回归

- [x] 4.1 使用既有中文 CodeAgent 过程事件与最终结果，不新增平台 `publish_discovery`/`publish_dependencies` 阶段；由对话标签测试验证。
- [x] 4.2 更新 runner/Workspace 说明，使文档与持久 Sandbox + 仓库发现一致。
