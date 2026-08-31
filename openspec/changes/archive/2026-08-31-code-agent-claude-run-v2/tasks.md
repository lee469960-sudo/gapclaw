## 1. Local 发布契约与控制面

- [x] 1.1 在 Manifest 模型和校验中增加 `local_publish_command_id`、固定 `local` target、命令参数/依赖、网络目的地、Secret 引用、验证计划和并发锁字段，并用字段级拒绝测试验证非法配置不会创建 Run
- [x] 1.2 实现命令注册表与 canonical 入口解析，拒绝未登记命令、任意 Shell、非 local 环境和参数覆盖，并用策略单元测试验证拒绝原因稳定
- [x] 1.3 增加发布 Run 的 preflight/dry-run 状态和一次性人工确认门禁，确认取消、过期或未确认时不会启动副作用命令
- [x] 1.4 增加仓库+local 环境发布锁和 Run 生命周期状态，验证并发 Run 只能有一个正式发布执行者

## 2. Sandbox 与工具链

- [x] 2.1 构建 local 发布 runner 的 `amd64`/`arm64` 双架构镜像，预装 CodeAgent/Claude Code 基础工具；dbt、jq、clickhouse-client 等业务工具不作为运行环境必备，并验证镜像 digest 与可选依赖探测审计
- [x] 2.2 在现有 runner 策略中落实非 root、禁止提权、仅 Workspace/临时目录可写、无 Docker Socket/宿主机路径，并用 Sandbox 策略测试验证越界动作被拒绝
- [x] 2.3 为 local 发布增加 Manifest 授权的最小网络例外和 DNS/主机端口校验，验证未授权网络保持阻断且普通 CodeAgent 默认隔离不变
- [x] 2.4 通过现有 Secret 管理注入短期 local 发布凭证，验证凭证不进入仓库、命令参数、日志、模型上下文、Workspace 持久文件或工件

## 3. 发布执行与证据

- [x] 3.1 在现有 CodeAgent Runtime 中实现 local 发布阶段，按冻结契约执行 canonical 命令一次并捕获退出码、脱敏输出和发布 ID
- [x] 3.2 强制 local target 和 Git 副作用保护，验证发布过程不修改业务源文件且不会执行 `git add/commit/push`
- [x] 3.3 实现超时、网络中断和状态未知处理，验证副作用命令不自动重试并生成可行动的人工恢复信息
- [x] 3.4 将 preflight、确认、发布、证据、验证、清理和失败分类写入统一事件流，验证事件顺序、脱敏和历史回放一致

## 4. Verifier 与用户界面

- [x] 4.1 扩展 Verifier 以同时检查退出码、远端发布证据、local 目标状态、环境绑定、Secret 脱敏和 Git 约束，并覆盖证据不足/验证失败用例
- [x] 4.2 增加发布成功、拒绝、依赖缺失、网络拒绝、认证失败、发布失败、验证失败和状态未知的稳定终态映射，并验证 Claude 文本不能单独判定成功
- [x] 4.3 在 CodeAgent 对话中展示 preflight、人工确认、发布过程、发布 ID、验证摘要和清理结果，验证敏感参数不会显示
- [x] 4.4 为发布失败和状态未知提供可审计的恢复建议，验证不会误显示为成功或可采用 Patch

## 5. 集成验收与回滚

- [x] 5.1 为单一试点仓库登记 local canonical 命令和验证计划，执行 preflight/dry-run 集成测试并保存脱敏证据
- [x] 5.2 执行一次人工确认后的 local 发布端到端测试，验证命令、镜像、网络、Secret、锁、证据和 Verifier 闭环
- [x] 5.3 验证禁用 feature flag、撤销 Manifest 或镜像 digest 后，新 local 发布被 fail closed，普通 CodeAgent run 仍可用
- [x] 5.4 运行后端回归、Sandbox/Verifier 集成测试、Python 编译、前端构建和 `git diff --check`，记录结果到变更进度文档
