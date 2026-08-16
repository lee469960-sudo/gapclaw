# IM 消息渠道接入指南

GAP 支持将外部 IM（飞书 / 钉钉 / Telegram / QQ·OneBot / 企业微信）消息转发到平台 Agent，并回发文本回复。

## 架构

1. 管理台配置渠道并绑定 Agent（菜单：**智能平台 → 消息渠道**）
2. 各平台将回调指向：

```text
https://<你的API域名>/hooks/channels/<provider>/<channel_id>/<webhook_secret>
```

3. 收到消息后：幂等去重 → 限流 → 映射 IM 会话到 Agent session → 异步跑 ReAct → `send_text` 回发

本地开发也可用 **Mock** 渠道：无需公网，点「测试」即可。

## 本地 / 生产：cloudflared 公网域名

飞书事件订阅**不能填** `127.0.0.1` / `localhost`，必须公网 HTTPS。

### Named Tunnel（推荐生产）

1. 在 Cloudflare Zero Trust 创建 Named Tunnel，拿到 token 与固定域名
2. 配置环境变量后执行：

```bash
export CLOUDFLARE_TUNNEL_TOKEN=...
export TUNNEL_PUBLIC_BASE_URL=https://your-fixed-host.example.com
# 可选 ERR→IM：OPS_ALERT_SECRET / OPS_ALERT_CHANNEL_ID（渠道 config 需 ops_chat_id 或 default_chat_id）
./scripts/feishu-cloudflared.sh
```

模板见 [`deploy/cloudflared/config.yml`](../deploy/cloudflared/config.yml)（`retries: 5` / `grace-period: 30s`）。  
脚本会旁路启动 `scripts/cloudflared-watch.sh`：日志出现 `\bERR\b` 时立即 `POST /hooks/ops/alert`。

### Quick Tunnel（仅本地逃生）

未配置 token 时，`start-local.sh` 会自动 `CLOUDFLARED_MODE=quick`：

```bash
CLOUDFLARED_MODE=quick ./scripts/feishu-cloudflared.sh
# 或
ENABLE_CLOUDFLARED=0 ./scripts/start-local.sh   # 跳过隧道
```

Quick Tunnel 域名会变；生产请改用 Named Tunnel。

## 通用步骤

1. 准备一个已配置 LLM 的 Agent
2. 新建渠道 → 选择类型 → 绑定 Agent → 填写密钥 → 启用
3. 复制卡片上的 Webhook URL 到对应开放平台（须公网 HTTPS；本地见上节 cloudflared）
4. 先用私聊发一条文本验证；查看渠道「日志」

密钥保存在服务端加密字段；编辑时若仍是掩码（`****`）不会被覆盖。

渠道支持与 Agent 相同的 **公共 / 私有** 权限：列表可按「所有 / 我的」筛选；私有渠道可配置授权用户。Webhook 入站仍按 URL 密钥鉴权，不受管理台可见性影响。

## 飞书

配置项：`app_id`、`app_secret`、`verification_token`

**绑定关系**：创建/编辑渠道时必须选择「绑定 Agent」。手机端对飞书机器人发的每条消息，都会进入该 Agent 的 **「飞书」会话**（Web 对话页可选该会话查看；渠道卡片可点「打开对话」）。

### 开通步骤

1. 飞书开放平台创建企业自建应用，开通机器人能力与「获取与发送单聊、群组消息」等权限
2. 事件订阅：请求地址填管理台卡片上的 Webhook（**须公网 HTTPS**；本地用 cloudflared，见上文）
3. **必须**订阅事件 **`im.message.receive_v1`（接收消息）**，并**发布应用版本**（只订阅「进入会话」不够，发文字不会回调）
4. 建议关闭 Encrypt Key（当前仅支持明文事件）
5. 发布应用版本；在飞书里找到该机器人

打开私聊时若收到欢迎语，说明回发链路正常；此时再发文字仍无回复，几乎一定是未订阅 / 未发布 `im.message.receive_v1`。

### 群聊 @机器人

私聊已通、群里 `@机器人` 仍无反应时，**飞书不会推送群消息**（渠道日志不会出现「收到消息」）。请核对：

1. **把该开放平台应用机器人拉进目标群**（不是自定义 Webhook 机器人）
2. **权限管理**开通并**创建版本 → 发布**（缺一不可）：
   - `im:message.p2p_msg:readonly` — 读取用户发给机器人的单聊消息（私聊用）
   - **`im:message.group_at_msg:readonly` — 接收群聊中@机器人消息事件（群 @ 用，最常漏）**
   - `im:message:send_as_bot` — 以应用的身份发消息
3. 事件订阅：`im.message.receive_v1`（接收消息）
4. 群里发送：`@机器人名 打送一次每日巡检信息`

自检：再 @ 一次后打开渠道「日志」——若仍没有「收到消息」，说明权限/进群/发布未生效，不是 Agent 问题。

回发群消息走群 `chat_id`，无需额外 Webhook。

1. 管理台 → **消息渠道** → 新建「飞书」→ **绑定目标 Agent** → 填写 App 凭证 → 启用 → 保存  
2. 本地先跑 `./scripts/feishu-cloudflared.sh` 并重启 API，确认「公网基址」与「复制 Webhook」为 `https://…trycloudflare.com/...`（**不是** 127.0.0.1）  
3. 复制卡片 Webhook，粘贴到飞书事件订阅并保存通过 challenge  
4. 手机飞书搜索该机器人，发「你好」  
5. 预期：先收到「已收到，正在由 Agent「xxx」处理…」，随后收到 Agent 正式回复；渠道「日志」可见「收到消息 … agent=…」「已回发 agent=…」  
6. 将渠道改绑到另一个 Agent 后，同一飞书会话的后续消息应走新 Agent  

若日志有「请先绑定 Agent」：编辑渠道补选 Agent。若只有 challenge 成功、无业务日志：检查事件订阅是否已发布、Webhook 是否指向当前环境。若飞书报 URL 无效：确认隧道仍在跑、PUBLIC_BASE_URL 与飞书后台一致。

## 钉钉

配置项：`app_secret`（HTTP 回调签名）

1. 钉钉开放平台创建机器人，选择 **HTTP 模式**
2. 消息接收地址填 Webhook
3. 回发依赖回调中的 `sessionWebhook`（平台异步推送）

## Telegram

配置项：`bot_token`、`mode`（`webhook` | `polling`）、`allowed_chat_ids`（可选）

### 开通步骤

1. 在 Telegram 找 [@BotFather](https://t.me/BotFather) 创建机器人，复制 **Bot Token**
2. 管理台 → **消息渠道** → 新建「Telegram」→ 绑定 Agent → 填 Token → 选择模式 → 启用 → 保存
3. **保存时平台会自动同步**：
   - **Polling（默认，适合本地）**：调用 Bot API `deleteWebhook`，由 API 进程后台 `getUpdates` 拉取
   - **Webhook（需公网 HTTPS）**：调用 `setWebhook`，URL 为卡片上的 Webhook（依赖 `PUBLIC_BASE_URL`）
4. 卡片可点「同步 Webhook」在更换公网基址或轮换密钥后重新注册
5. 停用渠道时会自动 `deleteWebhook`

Webhook 与 Polling **互斥**：同一 Bot 不能同时使用。群聊建议配置 `allowed_chat_ids` 白名单。

本地无公网时请用 **Polling**；若坚持 Webhook，先配置 `PUBLIC_BASE_URL`（见上文 cloudflared）。

## QQ（OneBot）

配置项：`onebot_http_url`、`access_token`、`secret`、`allowed_group_ids`

默认对接 **OneBot v11** HTTP 正向/反向：

- 平台 Webhook 接收 OneBot 上报的 `message` 事件
- 回发调用 `{onebot_http_url}/send_private_msg` 或 `send_group_msg`

## 企业微信

配置项：`corp_id`、`agent_id`、`secret`、`token`、`encoding_aes_key`

1. 管理后台创建自建应用，设置接收消息 URL 为 Webhook（GET 验签 + POST 解密）
2. 应用需具备发消息权限；当前实现为**企业内部应用私聊文本**

> 个人微信协议不在支持范围。

## 限流与幂等

- 同一渠道约 **20 条 / 60 秒**；超限记日志并丢弃
- 相同 `msg_id` 不重复跑 Agent
- 同 chat 并发处理中时跳过新消息（避免打爆沙箱）

## 排障

| 现象 | 排查 |
|------|------|
| 404 not found | channel_id / webhook_secret 是否匹配；是否改过「轮换」 |
| 渠道日志「处理失败」 | Agent/LLM 配置、沙箱、密钥 |
| 飞书 URL 无效 / 不能填 127.0.0.1 | 启动 cloudflared；设置 `PUBLIC_BASE_URL`；重启 API；复制 HTTPS Webhook |
| 飞书 challenge 失败 | verification_token；隧道是否指向本机 :8000 |
| 飞书能收事件无回复 | 是否绑定 Agent；日志是否「已回发」；机器人发消息权限；私聊 open_id 回发 |
| 钉钉无法回发 | 是否 HTTP 回调机器人（需 sessionWebhook） |
| Telegram 无消息 | webhook 未自动注册成功（看渠道日志 / 点「同步 Webhook」）；或改用 polling；确认 bot_token 与 PUBLIC_BASE_URL |

## API 参考（管理）

需登录 Cookie 或 Bearer Token：

- `POST /pages/page_channel.cgi` `action=list|create|update|delete|logs|test_mock|providers|sync_telegram|rotate_webhook`
