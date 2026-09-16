## Purpose

定义 GAP 在单台生产主机上从已构建 ACR 镜像进行轻量可信自动部署的边界，使发布输入可追溯、主机操作固定、失败可自动恢复。

## ADDED Requirements

### Requirement: 生产部署仅消费受验证的不可变发布清单
系统 SHALL 在 API/Web 镜像构建并推送成功后生成一份版本化发布清单，其中至少包含 Git tag、`deploy/gap.version`、提交 SHA、API 与 Web 的规范镜像仓库及其已解析 digest、目标标识和健康检查版本。系统 MUST 仅当 Git tag 与该提交中的 `deploy/gap.version` 完全相等时把清单标记为可部署。生产部署 MUST 以该清单中的 digest 为镜像输入，不得以可变 tag 或 `latest` 作为部署输入。

#### Scenario: tag 与发布版本一致
- **WHEN** `v1.2.3` 标签对应提交中的 `deploy/gap.version` 为 `v1.2.3`，且 API/Web 镜像均已解析为 digest
- **THEN** 系统生成包含两份 digest 与提交 SHA 的可部署发布清单

#### Scenario: tag 与发布版本不一致
- **WHEN** Git tag 与对应提交中的 `deploy/gap.version` 不完全相等
- **THEN** 系统拒绝创建可部署发布清单
- **AND** 不触发生产主机部署

### Requirement: 生产 Hook 触发与标签内容隔离
系统 SHALL 使用 GitHub-hosted CI 在构建成功后向固定 `POST /internal/release-hook` 投递已验证发布清单。CI MUST 以 GitHub secret 的 HMAC-SHA256 签名覆盖规范 manifest、delivery id 与时间戳，且不得把 ACR 凭据传递给 Hook。GAP MUST 在验签、时间窗口、delivery id 去重、target、tag/version 与 digest 校验全部通过后，才通过私网 mTLS 调用固定 Deploy Runner。生产服务器 MUST NOT 依赖 GitHub self-hosted runner、GitHub runner token 或 GitHub 出网。

#### Scenario: 构建成功后进入固定 Hook 链路
- **WHEN** 某个可部署发布清单已由构建链路产生
- **THEN** GitHub CI 仅向固定 Hook 投递签名 manifest
- **AND** GAP 仅在验签成功后调用固定 Deploy Runner

#### Scenario: Hook 重放或签名无效
- **WHEN** Hook 的签名、时间戳、delivery id 或固定 manifest 任一项无效，或 delivery id 已被接受
- **THEN** GAP 拒绝请求且不调用 Deploy Runner
- **AND** 当前运行版本保持不变

#### Scenario: 非记录镜像被请求部署
- **WHEN** 调用方请求 Deploy Runner 部署不在可部署发布清单中的镜像引用或 digest
- **THEN** Runner 拒绝该请求
- **AND** 当前运行中的 GAP 版本保持不变

### Requirement: Deploy Runner 以最小且固定的主机权限运行
Deploy Runner SHALL 作为主机受管服务提供受限的 `deploy`、`status`、`health` 与 `rollback` 操作。`deploy` MUST 仅接受来自 GAP 固定 mTLS 身份的完整已验证 manifest；它不得接受 URL、命令、tag、任意 image 或调用者目标。只有 Runner 可以执行 GAP 生产 Compose 操作；Release Agent、普通 Agent 运行时和 GitHub CI MUST NOT 获得任意 Docker、主机 shell 或 SSH 部署执行能力。Runner MUST 从主机本地受限配置读取 ACR pull-only 凭据；GitHub CI 不得接收、记录或传递该凭据。

#### Scenario: 部署工作流不含 ACR 拉取凭据
- **WHEN** 受保护部署工作流执行一次发布
- **THEN** 工作流环境和日志中不包含 ACR 用户名或密码
- **AND** Runner 使用主机本地受限配置完成镜像拉取

#### Scenario: Release Agent 请求状态
- **WHEN** GAP Release Agent 请求 Runner 状态或健康信息
- **THEN** Runner 只返回受限状态或健康结果
- **AND** 不向 GAP 提供任意主机命令执行接口

### Requirement: 发布须经健康判定并在失败时自动恢复
Deploy Runner SHALL 在应用一份发布清单后，使用生产 Compose 服务健康状态以及 GAP API `/health` 返回 `{"status":"ok"}` 共同判定该发布成功。Runner MUST 把成功发布写为最近一次已知健康版本。若部署或健康判定失败，Runner MUST 自动回滚到最近一次已知健康版本；若不存在该版本，MUST 明确记录不可自动恢复状态而非部署任意镜像。

#### Scenario: 新版本通过现有健康检查
- **WHEN** 新发布的 Compose 服务均处于健康运行状态，且 API `/health` 返回 `{"status":"ok"}`
- **THEN** Runner 将该清单标记为成功并更新最近一次已知健康版本

#### Scenario: 新版本健康检查失败
- **WHEN** 新发布未在健康检查窗口内满足 Compose 健康状态和 API `/health` 条件
- **THEN** Runner 自动恢复最近一次已知健康版本
- **AND** 将原发布记录为失败且包含回滚结果

### Requirement: 发布状态在 GAP 不可用时仍可被 Runner 独立判断
Deploy Runner MUST 在主机本地持久化当前发布状态、最近一次已知健康版本和至多五条最近成功发布记录。Runner 的 `status`、`health` 和自动回滚 MUST 不依赖 GAP 正常运行；向 GAP 的结果回传失败时，Runner MUST 保留待回传状态并在后续重试。

#### Scenario: GAP 暂时不可用
- **WHEN** 部署完成但 GAP 发布结果回调端点不可达
- **THEN** Runner 本地保存完整结果并重试回传
- **AND** `status` 与 `health` 仍能报告本地部署状态

#### Scenario: 成功记录超过保留上限
- **WHEN** 新成功发布使本地成功发布记录超过五条
- **THEN** Runner 保留最近五条成功记录
- **AND** 最近一次已知健康版本仍可用于自动或受控回滚

### Requirement: Runner 与 GAP 控制面使用双向认证的固定发布协议
Deploy Runner 与 GAP 发布管理控制面 SHALL 通过双向 mTLS 通信。GAP 到 Runner 的 `deploy`、`status`、`health` 和受限 `rollback` MUST 仅使用私有 `https://gap-runner.internal:9443`，该名称只从 GAP API 容器解析到受主机防火墙限制的 Docker gateway；它 MUST NOT 经过或暴露在公网 Caddy。Runner 的结果回传 MUST 仅发送至 Caddy 受管的 `https://runner.gapclaw.online/internal/release-runner/callback`，且该入口只在客户端和服务端证书均通过信任校验时转发。

#### Scenario: 经认证的结果回传
- **WHEN** Runner 向 GAP 回传发布结果
- **THEN** GAP 仅在客户端和服务端证书均通过信任校验时接受该结果
- **AND** 保存的审计记录包含固定发布状态字段而不包含秘密

#### Scenario: 未通过双向认证的请求
- **WHEN** 未受信任客户端调用 Runner，或未受信任服务端接收 Runner 回传
- **THEN** 接收方拒绝请求
- **AND** 不改变发布状态或触发部署、回滚操作

#### Scenario: GAP 访问私有 Runner 控制面
- **WHEN** GAP API 请求 Runner 的状态、健康或受限回滚
- **THEN** 请求仅经 `gap-runner.internal:9443` 的 mTLS 私有路径到达 Runner
- **AND** 公网 `runner.gapclaw.online` 不代理或暴露这些控制路径

#### Scenario: 自更新先确认接收再部署
- **WHEN** 固定 GAP 身份提交的完整 manifest 通过 Runner 校验
- **THEN** Runner 持久化非终态接收记录并返回 `202 accepted` 后独立执行部署
- **AND** API 容器重建不要求原 Hook 连接一直存活，接收响应不得被视为部署成功
- **AND** 仅健康门禁通过后的终态回调可记录成功；Runner 重启后的中断接收记录需要对账而不自动重跑

### Requirement: 主机 Caddy 按显式域名隔离 Compose 栈与 Runner mTLS 回调
系统 SHALL 以宿主机已安装的 Caddy 作为唯一公网 HTTP(S) 入口并独占 80/443。`gapclaw.online` SHALL 路由 GAP Web 及固定 API/WebSocket 路径；GAP API/Web MUST 仅绑定 host loopback，由 Caddy 访问。`runner.gapclaw.online` SHALL 仅暴露固定的 Runner 回调路径，并在转发前要求由受信任私有 CA 签发的客户端证书。后续 Compose 栈 MAY 使用独立的显式 Caddy site 和 loopback upstream，但 MUST 不得取得默认 catch-all、80/443 端口绑定或任意 GAP API 容器网络访问权。production Caddyfile MUST NOT 复用 staging 的域名、CA、证书或 site 配置，且不得启动或加载 Nginx。

#### Scenario: GAP 域名访问
- **WHEN** 用户访问 `https://gapclaw.online`
- **THEN** Caddy 将 Web 流量和预定义 API/WebSocket 路径路由到 GAP loopback 服务
- **AND** GAP API/Web 的容器端口不直接暴露给公网

#### Scenario: Runner 回调具有可信客户端证书
- **WHEN** Runner 使用私有 CA 信任的客户端证书向 `https://runner.gapclaw.online/internal/release-runner/callback` 发送固定发布结果
- **THEN** Caddy 仅在 mTLS 验证成功后向 GAP API 转发经其自身设置的认证结果
- **AND** GAP 保存固定发布字段而不接收任意命令或秘密

#### Scenario: 未认证客户端或未知入口
- **WHEN** 无客户端证书、证书不受信任的调用方访问 Runner 回调，或任一 Compose 栈尝试未声明域名/公网端口
- **THEN** Caddy 拒绝请求或配置检查失败
- **AND** 不调用 GAP 回调处理器且不改变发布状态
