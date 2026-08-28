# auth-session-v1 需求文档

> 本轮目标：修复「登录验证码误报过期」+「会话 cookie 24 小时与超时跳转」两个登录/会话问题。

## 1. 背景与根因（代码实测）

| # | 现象 | 证据 | 根因 |
|---|---|---|---|
| 1 | 登录验证码**总是提示过期，但登录成功** | `Login.vue:24` 验证码输入框 `@keyup.enter="onLogin"` 与 `Login.vue:10` 表单 `@submit.prevent="onLogin"` 双绑定，回车触发 `onLogin` **两次** | 两次并发 `/login.cgi`：第一个校验通过并**一次性消费**验证码（`captcha.py` `verify_captcha` 成功即 `store.pop`），第二个再查已无此条目 → 返回「验证码错误或**已过期**」 |
| 2 | 会话默认 30 天，而非 24 小时 | `config.py:22` `session_ttl_hours = 720`；`auth.py:42` cookie 硬编码 `max_age=720*3600` | TTL 与 cookie 有效期两处都是 30 天，且 cookie 未跟随配置 |
| 3 | 无「超过 24h 主动跳转」 | `router.beforeEach`（`router.js:49`）只读缓存 `shell`，命中时不重新校验后端会话；`api.js:32` 仅 401 被动跳转 | 缺少「登录过期时间戳 + 导航检查」的主动判定，闲置超时后要等下一次请求才被跳转 |

> 关键约束：`session_id` 为 `httponly` cookie，**前端 JS 读不到**，无法直接判断 cookie 是否到期；只能靠「客户端存登录时间戳」+「后端 401（cookie 到期浏览器不发 → 401）」。

## 2. 决策汇总（grill 结果）

| # | 维度 | 决策 |
|---|---|---|
| Q1 | 验证码修复范围 | A：只改前端防抖，后端一次性消费不变 |
| Q2 | 会话 TTL | A：默认 24h 可配置（`session_ttl_hours`），cookie `max_age` 跟随 |
| Q3 | 超时跳转机制 | A：主动（导航检查）+ 被动（401 兜底）双保险 |
| Q4 | 「记住我」 | A：不加，单档固定 24h |
| Q5 | 存量 session | A：只影响新建，存量按原 `expires_at` 到期 |
| Q6 | 计时语义 | A：固定 24h（从登录起算，非滑动） |
| Q7 | 验证码形态 | A：不改图片，保持 4 位数字文本 |

## 3. 需求

### 需求 1：登录验证码防重复提交（治「过期」误报）

登录表单提交 MUST 只发出一次 `/login.cgi` 请求，消除「回车双触发」导致的验证码二次消费误报「已过期」。

- **WHEN** 用户在登录表单按下回车或点击登录按钮
- **THEN** 只发出一次登录请求
- **AND** 验证码不会因并发二次消费被误判「已过期」
- **落点**：`apps/web/src/views/Login.vue` —— `onLogin` 顶部加 `if (loading.value) return` 防抖守卫；去掉验证码输入框的 `@keyup.enter="onLogin"`，只保留表单 `@submit.prevent="onLogin"` 单一入口。后端 `captcha.py` 一次性消费语义不变。

### 需求 2：会话 TTL 默认 24 小时可配置

会话默认有效期 MUST 为 24 小时；登录 `session_id` cookie 的 `max_age` MUST 跟随该配置，不再硬编码 30 天。仅影响新建会话，存量会话按原 `expires_at` 到期。

- **WHEN** 用户登录成功创建会话
- **THEN** `expires_at = now + session_ttl_hours`（默认 24h，环境变量 `SESSION_TTL_HOURS` 可覆盖）
- **AND** `session_id` cookie `max_age = session_ttl_hours * 3600`，与 DB 会话有效期一致
- **落点**：`apps/api/app/config.py`（`session_ttl_hours` 默认 `720 → 24`）、`apps/api/app/routers/auth.py`（`max_age=720*3600` → `settings.session_ttl_hours * 3600`）。`security.py` 的 `session_expiry()` 已按 `session_ttl_hours` 计算，无需改。

### 需求 3：前端主动 + 被动双保险过期跳转

用户访问受保护页面时，前端 MUST 每次导航检查登录过期时间戳，已过期则清空会话并跳登录页；后端 MUST 继续以 401 兜底跳转。固定 24h 语义（从登录起算，非滑动）。

- **WHEN** 用户登录成功
- **THEN** 前端在 `sessionStorage` 写入 `login_expires_at = now + 24h`
- **AND** 每次路由导航检查：若 `login_expires_at` 已过期，清空 shell 并跳 `/login`
- **AND** 任一 API 返回 401 时仍跳转 `/login`（兜底）
- **落点**：`apps/web/src/session.js`（新增 `login_expires_at` 存取与清理，复用 `sessionStorage`）、`apps/web/src/views/Login.vue`（登录成功写时间戳）、`apps/web/src/router.js`（`beforeEach` 增加过期检查）。`api.js` 401 跳转保留不变。

## 4. 边界与铁律

- **不加「记住我」双档**：单档固定 24h。
- **不改图片验证码**：保持 4 位数字文本，图片验证码是独立需求。
- **不强制失效存量会话**：改配置只影响新建，存量按原 `expires_at` 到期。
- **不新增定时器轮询**：主动判定只挂路由导航检查，不做 `setInterval`。
- **后端一次性消费不变**：验证码 consume-once 语义保留，不为掩盖前端 bug 而放宽。

## 5. 下一步

- 落点：新建 `openspec/changes/auth-session-v1/`（自包含，新增 `auth` capability）。
- 待 `openspec validate auth-session-v1 --strict`。
