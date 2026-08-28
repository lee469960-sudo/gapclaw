# Progress Log

<!-- 记录实际完成情况。每完成一个 OpenSpec task：在此记录 → 确认代码+测试完成 → 再勾选 tasks.md。 -->

## Session: 2026-08-22

### Current Status
- **Phase:** 4 - 验证与回归（已完成；全部 4 个 phase 完成）
- **Started:** 2026-08-22
- **OpenSpec change:** `auth-session-v1`

### Actions Taken
- **task 1.1（防抖守卫）**：`Login.vue` `onLogin` 顶部加 `if (loading.value) return`，杜绝并发二次提交。
- **task 1.2（去双入口）**：去掉验证码输入框 `@keyup.enter="onLogin"`，只保留表单 `@submit.prevent="onLogin"` 单一入口。
- **task 1.3（后端不变）**：`captcha.py` `verify_captcha` 一次性消费语义保持原样，未改动。
- **task 2.1（TTL 默认）**：`config.py` `session_ttl_hours` 默认 `720 → 24`；`security.py` `session_expiry()` 已按 `session_ttl_hours` 计算，无需改。
- **task 2.2（cookie 跟随）**：`auth.py` 新增 `from app.config import get_settings`，`session_id` cookie `max_age` 从 `720*3600` 改为 `get_settings().session_ttl_hours * 3600`。
- **task 3.1（存取与清理）**：`session.js` 新增 `EXPIRES_KEY = 'login_expires_at'` + `setLoginExpiry()` / `getLoginExpiry()`，`clearShell()` 同步 `removeItem(EXPIRES_KEY)`。
- **task 3.2（登录成功写时间戳）**：`Login.vue` 导入 `setLoginExpiry`，`await postCgi('/login.cgi')` 成功后、`router.push('/')` 前写 `setLoginExpiry(Date.now() + 24*3600*1000)`。
- **task 3.3（导航过期检查）**：`router.js` 导入 `getLoginExpiry/clearShell`，`beforeEach` 在 public 检查后加「有值且已过期 → `clearShell()` → `next('/login')`」。
- **task 3.4（401 兜底保留）**：`api.js` 401 跳转逻辑未改动。
- **task 4.1–4.4（验证与回归）**：API 全量测试 `269 passed`；`test_captcha.py` `3 passed`（一次性消费语义回归）；`python -c` 确认 `session_ttl_hours = 24`；`node --check` 校验 `session.js`/`router.js`/`api.js` 语法通过；代码审阅确认回车仅一次 `/login.cgi`、cookie `Max-Age = 24*3600 = 86400`、过期导航跳 `/login`、`create_session` 仍按 `session_expiry()` 计算（存量会话 DB `expires_at` 不受配置默认值变更影响）。

### Test Results

| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| API 全量回归（`pytest tests/`） | 无回归 | 269 passed | ✅ |
| `test_captcha.py`（一次性消费/错误重试/落盘存活） | 无回归 | 3 passed | ✅ |
| `get_settings().session_ttl_hours` | 24 | 24 | ✅ |
| `node --check session.js / router.js / api.js` | 语法通过 | OK | ✅ |
| 回车仅一次 `/login.cgi`（代码审阅） | 单入口 + loading 守卫 | 满足 | ✅ |
| cookie `Max-Age = 24h = 86400`（代码审阅） | 跟随配置 | 满足 | ✅ |

### Errors

| Error | Resolution |
|-------|------------|
| `pytest`（无路径）收集到 `scripts/smoke_test.py` 报 JSONDecodeError（需连活服务器） | 属既有 smoke 脚本非单测；改用 `pytest tests/` 跑真实单测套件，269 passed |
