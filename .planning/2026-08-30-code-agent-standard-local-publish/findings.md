# Findings: code-agent-standard-local-publish

- `local_publish_command_not_allowed` 的直接原因是服务端 `CODE_LOCAL_PUBLISH_COMMANDS` 默认 `{}`；Manifest 中默认 command ID 不会自动登记。
- 现有 Local 发布 UI 复制了服务端注册表字段，导致 Manifest 值、注册表值和运行契约三处可能不一致。
- 现有 `dependencies` 只执行审计探测，不安装依赖；Claude Code Runtime 的网络 guard 也禁止 `apt/pip/npm` 下载。
- 标准方案需区分：镜像内固定系统工具与仅按仓库锁定文件安装的应用依赖。
- 冻结 Run 契约目前保留 `local_publish` 容器；它是后续平台档案冻结值的唯一载体，不需要恢复任何 Manifest 字段。
- 原有 `CODE_LOCAL_PUBLISH_COMMANDS` 已提供命令 argv、网络、验证与锁的服务端注册校验；标准项目档案可只绑定该固定命令和凭据，避免再复制一套可被 Manifest 覆盖的配置。
