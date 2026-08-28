# Findings & Decisions

<!-- 记录执行中发现的问题/事实。需求与设计决策见 OpenSpec change，不在此重复。 -->

## Requirements

需求唯一来源：
- 需求输入：`docs/exploration/auth-session-v1.md`
- OpenSpec specs（验收标准 WHEN/THEN/AND）：`openspec/changes/auth-session-v1/specs/auth/spec.md`
- OpenSpec tasks（唯一正式任务）：`openspec/changes/auth-session-v1/tasks.md`

（登录验证码防重复提交 / 会话 TTL 默认 24 小时可配置 / 前端主动+被动双保险过期跳转 —— 细节不在此重述。）

## Research Findings

- **F1（双绑定已确认）**：`Login.vue:24` 验证码输入框 `@keyup.enter="onLogin"` 与 `Login.vue:10` 表单 `@submit.prevent="onLogin"` 双绑定；当前 `onLogin`（`Login.vue:86`）**无** `loading` 防抖守卫，回车会触发两次 `onLogin` → 两次 `/login.cgi`。第一次校验通过并 `store.pop` 消费验证码，第二次再查无条目 → 误报「验证码错误或已过期」。task 1.1/1.2 的落点准确。
- **F2（TTL 现状）**：`config.py:22` `session_ttl_hours = 720`（30 天）；`security.py:70-71` `session_expiry()` **已**用 `get_settings().session_ttl_hours` 计算 `expires_at`（**无需改**）；`deps.py:95-98` `create_session` 调 `session_expiry()`（**无需改**）。唯一硬编码 30 天是 `auth.py:42` cookie `max_age=720*3600`（task 2.2 改此）。故 task 2.1 仅改 config 默认值即让 `expires_at` 自动变 24h。
- **F3（跳转现状）**：`router.js:49-67` `beforeEach` 只读 `getShell()` 缓存，命中时**不**重新校验后端会话（闲置超时需等下一次请求才被 401 跳转）；`api.js:32-34` 401 被动跳转 `window.location.href='/login'` 已存在（task 3.4 保留）。`session.js:67-73` `clearShell` 只清 `SHELL_KEY/ROUTES_KEY/ROLES_KEY` 三个 key，task 3.1 需新增 `login_expires_at` 的存取与清理（同 `sessionStorage`）。
- **F4（写时间戳顺序）**：`Login.vue:86-101` `onLogin` 流程为 `clearShell()` → `postCgi('/login.cgi')` → `router.push('/')`。task 3.2「登录成功写 `login_expires_at`」应落在 `postCgi` 成功之后、`router.push` 之前（`clearShell` 先清再写，顺序天然正确，但必须在 `await postCgi` resolve 后再写，否则失败也写入）。
- **F5（验证码语义确认）**：`captcha.py:65-87` `verify_captcha` 成功即 `store.pop`（一次性消费）、校验错误保留可重试、过期 pop，与「后端一次性消费不变」一致。task 1.3 无需改动 `captcha.py`。

## Technical Decisions

（执行中产生的新决策记录于此；已锁定的设计决策 D1–D5 见 `openspec/changes/auth-session-v1/design.md`。）

| Decision | Rationale |
|----------|-----------|
| `login_expires_at` 用 `sessionStorage` 且由 `clearShell` 统一清理 | 对齐现有 shell 存储介质与清理入口，避免新增独立清理点 |
| 24h 语义固定（非滑动）：前端写 `Date.now() + 24*3600*1000`，仅登录时写一次 | D4，从登录起算 |

## Issues Encountered

| Issue | Resolution |
|-------|------------|
| | |

## Resources

- `openspec/changes/auth-session-v1/`（proposal / design / specs / tasks）
- `docs/exploration/auth-session-v1.md`（需求输入，根因复盘）
- 相关代码：`apps/web/src/views/Login.vue`、`apps/web/src/session.js`、`apps/web/src/router.js`、`apps/web/src/api.js`、`apps/api/app/config.py`、`apps/api/app/routers/auth.py`、`apps/api/app/security.py`、`apps/api/app/deps.py`、`apps/api/app/services/captcha.py`
