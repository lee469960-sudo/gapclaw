## 1. 可信发布清单与 GitHub 部署边界

- [x] 1.1 定义可版本化的 GAP release manifest 模型及严格校验（tag/version、提交 SHA、目标、允许仓库与 API/Web digest），并以单元测试覆盖有效、tag 不一致、可变 tag 与非法 digest 被拒绝的情形。
- [x] 1.2 改造 tag CI：生成规范 digest manifest、delivery id 和 HMAC 签名，并向固定 GAP Hook 投递；验证不一致、无签名或 ACR 凭据泄漏均不会产生可部署请求。
- [x] 1.3 移除 `workflow_run`、production Environment/self-hosted runner 与 GitHub Runner 注册依赖；验证生产服务器无需 GitHub 出网或 runner token。
- [x] 1.4 更新工作流检查：CI 只投递固定签名 envelope，tag 内容无法改变主机命令、目标或 Compose 路径。

## 2. 主机 Deploy Runner 与可恢复发布状态机

- [x] 2.1 创建可独立测试的 Deploy Runner 包与本地 `deploy` CLI，固定支持 manifest 驱动的 deploy、status、health 与 rollback 状态模型；验证未知操作、任意命令输入和未允许镜像均被拒绝。
- [x] 2.2 实现 Runner 主机受管 Compose 资产与配置加载，使用 `repository@sha256` 镜像引用并仅从主机受限 env 文件读取 ACR pull-only 凭据；验证 Compose 渲染不含 `latest`、不会读取 GitHub 凭据且不依赖 tag checkout 文件。
- [x] 2.3 实现持久化 release-state、单目标互斥锁、最近已知健康版本和五条成功历史保留；以单元测试覆盖状态 round-trip、并发部署串行化、保留裁剪及中断后的 reconciliation 状态。
- [x] 2.4 实现部署后的 Compose 服务健康与 API `/health` 联合判定，并在失败时自动回滚到最近已知健康 manifest；以 mock Compose/HTTP 测试覆盖成功、健康超时、自动回滚成功及无基线不可恢复情形。
- [x] 2.5 实现 Runner 的 mTLS `deploy`、`status`、`health`、受限 `rollback` 接口；`deploy` 仅接受 GAP 固定身份提交的完整 manifest，以集成测试验证可信部署、未受信任客户端和任意输入被拒绝。
- [x] 2.6 提供 Runner systemd 单元、主机目录/权限初始化和配置示例；验证安装检查能发现缺失的本地 env、证书、Docker Compose 或不安全文件权限。
- [x] 2.7 使用宿主机已安装的 Caddy 提供 production Caddyfile、安装/校验资产、`gapclaw.online` 固定 Hook 路由与 `runner.gapclaw.online` 固定 mTLS 回调；验证 API/Web 仅绑定 loopback、未知主机/路径与未认证客户端被拒绝、不加载 Nginx，并保留第二 Compose 的显式子域名扩展位。

## 3. GAP Release Agent 控制面

- [x] 3.1 新增发布清单、生命周期审计、回滚请求和回调投递状态的持久化模型与迁移，并以数据库测试验证幂等终态回传、敏感字段不落库及历史查询排序。
- [x] 3.2 实现 GitHub 固定签名 Hook intake、delivery 去重、受控发布任务审计及 GAP→Runner 双向 mTLS deploy；保留固定回调重试/对账，以 API 测试覆盖签名/时间/重放拒绝、可信投递和状态不一致。
- [x] 3.3 注册确定性的内置「Release Agent」和只读发布状态/历史接口，确保它不进入通用 ReAct/MCP/命令执行路径；以测试验证其只能读取和解释已存档的发布记录。
- [x] 3.4 实现管理员专属的受确认回滚 API：校验显示的已知健康目标与确认短语，记录操作者和结果，并仅调用 Runner 的受限 rollback；以授权测试验证非管理员、错误确认和无健康基线均不触发 Runner 调用。
- [x] 3.5 为发布管理配置增加目标、Runner 地址、CA/客户端证书引用和超时的受控加载与脱敏展示；验证 ACR 凭据、私钥和应用秘密不出现在 API 响应、日志或审计记录中。

## 4. 发布管理界面

- [x] 4.1 新增 Release Agent/发布管理视图，展示当前发布、API/Web digest、健康结果、自动回滚结果、历史记录及 `reconciliation_required` 状态；以前端组件测试验证状态、失败与未同步展示。
- [x] 4.2 新增管理员回滚交互，仅在管理员上下文显示控制，展示 Runner 已知健康目标并要求目标与确认短语；以前端测试验证非管理员不可见且不完整确认不能提交。
- [x] 4.3 在发布界面实施敏感字段脱敏和 Runner 不可达的可行动提示；验证页面不渲染凭据/证书内容，并能提示稍后对账而非显示虚假成功。

## 5. 文档、端到端验证与上线保障

- [x] 5.1 更新部署与运维文档，说明 GitHub CI Hook secret、Caddy 固定 Hook、服务器无 GitHub Runner、tag 规则、主机 ACR 凭据、mTLS 轮换、首个健康基线与权限边界；验证文档命令与配置路径同实现一致。
- [x] 5.2 增加 CI Hook manifest 到固定 Runner 的端到端/契约测试，覆盖签名、重放拒绝、digest 部署、健康成功/失败回滚和回传重试。
- [x] 5.3 执行 API、Runner、Web、CI Hook 和 production Caddy/Compose 配置检查；记录命令和结果，并修复本变更引入的失败。
- [ ] 5.4 在用户已明确授权的 production 主机完成一次受控 CI Hook 发布验证，保留 Caddy 备份、健康门槛与自动回滚；验证不暴露 ACR 拉取凭据、签名/重放保护、Runner 独立 status/health、管理员确认回滚与审计闭环，并将结果记录为上线证据。
