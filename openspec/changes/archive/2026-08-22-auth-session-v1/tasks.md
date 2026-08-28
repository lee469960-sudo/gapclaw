## 1. auth — 登录验证码防重复提交

- [x] 1.1 `Login.vue` `onLogin` 顶部加 `if (loading.value) return` 防抖守卫
- [x] 1.2 去掉验证码输入框 `@keyup.enter="onLogin"`，只保留表单 `@submit.prevent="onLogin"` 单一入口
- [x] 1.3 后端 `captcha.py` 一次性消费语义保持不变（不修改）

## 2. auth — 会话 TTL 默认 24 小时可配置

- [x] 2.1 `config.py` `session_ttl_hours` 默认 `720 → 24`
- [x] 2.2 `auth.py` `session_id` cookie `max_age` 从 `720*3600` 改为 `settings.session_ttl_hours * 3600`

## 3. auth — 前端主动 + 被动双保险过期跳转

- [x] 3.1 `session.js` 新增 `login_expires_at` 存取与清理（复用 `sessionStorage`，与 `clearShell` 同步清）
- [x] 3.2 `Login.vue` 登录成功后写 `login_expires_at = now + 24h`
- [x] 3.3 `router.js` `beforeEach` 增加过期检查：有值且已过期 → 清 shell → 跳 `/login`
- [x] 3.4 `api.js` 401 跳转兜底保留不变

## 4. 验证与回归

- [x] 4.1 验证码防重复提交：回车仅一次 `/login.cgi`，无「过期」误报（手动/自动化）
- [x] 4.2 TTL 24h：新登录 `expires_at` 为 24h、cookie `Max-Age` 为 86400
- [x] 4.3 过期跳转：`login_expires_at` 过期后导航跳 `/login`；401 仍跳 `/login`
- [x] 4.4 存量会话不受影响（改配置后旧会话仍按原到期）
