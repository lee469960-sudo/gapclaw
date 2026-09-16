# v1.0.32 生产发布验证

验证日期：2026-09-16。本文记录 `release-agent-lightweight-deploy` task 5.4 的实际证据，不新增或替代 OpenSpec 任务。

## 已完成：异步自更新与发布主链路

- 修复提交：`0dc804451d46e40570d185569b9c95ae5dbaf0b0`；不可变 tag：`v1.0.32`，与 `deploy/gap.version` 完全一致。
- [GitHub Actions run 35069277316](https://github.com/lee469960-sudo/gapclaw/actions/runs/35069277316) 最终为 `completed/success`，API/Web 双架构镜像构建、ACR 推送、manifest 上传及签名 Hook 全部成功。
- release：`v1.0.32-0dc80445`；delivery：`release-35069277316-1`。
- API digest：`sha256:bb0f2f9924fbc5003395d0a69bca40c58a923886e96631a53a50de0a85bb724d`。
- Web digest：`sha256:19f42da1b903779ae14a1387fc9461d05c922a84bc39fb006cf24eab8acb4f06`。
- Hook 步骤于 07:46:38 UTC 开始，约 1.2 秒后返回 `accepted`，Runner 响应中的 phase 是 `received`。此时没有把接收误标为成功。
- 07:47:32 UTC，新的 API 收到 Runner 终态回调，HTTP 200；数据库审计为该 release 的 `succeeded / ok / runner`，Hook delivery 为 `accepted`。
- Runner 独立 status/health：`succeeded/ok`，`last_known_healthy` 是同一 release，待回调数量为 0；API/Web 运行镜像与 manifest digest 一致，API/Web/DB 均为 healthy。
- 公网 `https://gapclaw.online/health` 返回 `{"status":"ok","version":"v1.0.32"}`；HTTPS 首页返回 200。

这次应用更新由 GitHub 的固定签名 Hook 触发，没有手工绕过 Hook 部署应用。主机受管 Runner 需独立升级：安装前确认旧文件指纹与仓库一致，备份代码和状态后只更新 `runtime.py`、`mtls.py`、`state.py`，保留 root:gap-runner 0640 权限，并重启 Runner 服务。代码和状态备份在 `/opt/gap-runner/backup-v1.0.32-async/`。

根因是原同步 deploy 等待自身 API 容器重建后才回答 Hook，导致 Caddy EOF/502；增加超时不能根治。修复为校验、持久化接收并发出 HTTP 202 后，由独立主机线程执行部署。健康门禁和终态回调仍是唯一成功判据；重启后的中断状态需要对账，不自动重跑。

## 已完成：安全边界与回归验证

- 无效 HMAC 签名：生产 Hook 返回 403 `release_hook_signature_invalid`，没有新增 intake。
- 已接受 delivery 的有效签名重放：返回 202 `duplicate`，Hook 记录数量和 Runner 发布状态不变，没有第二次部署。
- 未登录请求回滚：生产 API 返回 401。
- 真实 mTLS 回归测试证明阻塞部署期间可收到 202；非终态不会生成成功回调，健康通过后才记录成功。
- Runner 单元/契约测试 81 项、真实 mTLS 集成测试 3 项、API 发布测试 38 项全部通过；覆盖串行化、非法 manifest、重复/冲突、重启对账、失败非成功状态，以及已有健康失败自动回滚和回传重试契约。没有对生产注入失败镜像。
- Python 编译、`git diff --check` 和 OpenSpec 严格校验通过。
- 主机 Caddy 配置校验通过，既有配置未被本次修复覆盖或重载；当前主配置及 sites 另备份至 `/opt/gap-runner/backup-v1.0.32-async/caddy-config.tgz`（0600，不包含证书私钥）。既有 `/etc/caddy/Caddyfile.bak-*` 仍保留。
- 凭据、私钥和会话 cookie 不写入本文或验收输出。

## 尚未完成：管理员确认回滚生产演练

task 5.4 仍未勾选，OpenSpec 为 22/23 项完成。服务器配置中的管理员凭据与已存在账号的密码哈希不匹配；验收脚本在登录前退出，没有重置密码、创建管理员会话、绕过鉴权或实际发起回滚。

1. **用户需求**：真实管理员对界面显示的当前已知健康目标完成受确认回滚，证明操作受限且操作者、目标、时间和结果可审计。
2. **尚缺证据**：有效管理员会话下的错误确认拒绝、正确确认执行和相应生产回滚审计。单元测试及未登录拒绝不能替代这项证据。
3. **一次未执行动作**：获得授权的有效管理员会话后，执行携带该会话的 `POST /api/release-management/rollback`，提交当前显示目标及 `confirmation=ROLLBACK`。只使用 Runner 显示的当前健康基线，不手工选择旧 tag 或 digest。
4. **预期判定**：Runner 返回 `rolled_back`，审计记录包含真实管理员、目标和处理结果，独立 status/health 与公网 `/health` 仍正常；否则不勾选 task 5.4。

下一步需要可用管理员登录。不要为了完成演练修改现有账号密码，也不要重放已经接受的 CI delivery。
