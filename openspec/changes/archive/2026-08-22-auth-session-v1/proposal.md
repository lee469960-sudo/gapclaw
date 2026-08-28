## Why

登录与会话存在两个问题（根因详见 `docs/exploration/auth-session-v1.md`）：

1. **验证码误报过期**：`Login.vue` 验证码输入框 `@keyup.enter="onLogin"` 与表单 `@submit.prevent="onLogin"` 双绑定，回车触发两次 `/login.cgi`；验证码一次性消费（`verify_captcha` 成功即 `store.pop`），第二次请求再查已无此条目 → 误报「验证码错误或已过期」，但第一个请求已登录成功。
2. **会话 30 天而非 24 小时**：`session_ttl_hours = 720`（30 天）、登录 cookie 硬编码 `max_age=720*3600`（30 天），与「默认 24 小时」目标不符；且前端无「超过 24h 主动跳转」，只有 401 被动跳转。

## What Changes

- **[auth] 登录验证码防重复提交**：登录表单只发一次 `/login.cgi`，消除「回车双触发」导致的验证码二次消费误报。
- **[auth] 会话 TTL 默认 24 小时可配置**：`session_ttl_hours` 默认 `720 → 24`；登录 `session_id` cookie `max_age` 跟随配置，消除硬编码 30 天。
- **[auth] 前端主动 + 被动双保险过期跳转**：登录成功写 `login_expires_at`；每次导航检查过期则清 shell 跳登录；保留 401 兜底。

## Capabilities

### New Capabilities

- `auth`: Web 登录验证码、会话 TTL 与过期跳转。

## Impact

- `apps/web/src/views/Login.vue`（`onLogin` 防抖守卫 + 去掉 `@keyup.enter`；登录成功写 `login_expires_at`）
- `apps/web/src/session.js`（`login_expires_at` 存取与清理）
- `apps/web/src/router.js`（`beforeEach` 增加过期检查）
- `apps/api/app/config.py`（`session_ttl_hours` 默认 24）
- `apps/api/app/routers/auth.py`（`session_id` cookie `max_age` 跟随 `session_ttl_hours`）
