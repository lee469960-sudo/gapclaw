# Design — auth-session-v1

## Context

登录验证码误报「过期」与「会话默认 24 小时 + 超时跳转」两个独立问题的修复。根因与证据见 `docs/exploration/auth-session-v1.md`。

## Goals / Non-Goals

**Goals:**
- 消除「回车双触发 → 验证码二次消费 → 误报过期」。
- 会话默认 24 小时，cookie 有效期与 DB 会话有效期一致（均跟随 `session_ttl_hours`）。
- 前端每次导航主动判定「登录已过 24h」并跳转，401 被动兜底。

**Non-Goals:**
- 不加「记住我」双档（单档固定 24h）。
- 不改图片验证码（保持 4 位数字文本）。
- 不强制失效存量会话（只影响新建）。
- 不新增定时器轮询（主动判定只挂路由导航）。

## Decisions

| # | 决策 | 内容 |
|---|---|---|
| D1 | 验证码修复 | 前端防抖：`onLogin` 顶部 `if (loading.value) return` + 去掉 `@keyup.enter`；后端一次性消费不变 |
| D2 | TTL | 默认 24h 可配置，`session_ttl_hours` 默认 24，cookie `max_age` 跟随 |
| D3 | 跳转机制 | 主动（导航检查 `login_expires_at`）+ 被动（401）双保险 |
| D4 | 计时语义 | 固定 24h，从登录起算，非滑动 |
| D5 | 存量会话 | 只影响新建，存量按原 `expires_at` 到期 |

## Rejected Alternatives（不进入 v1）

| 废弃项 | 废弃原因 |
|---|---|
| 后端验证码改 TTL 内可重复验证（Q1=B） | 同一验证码可被重复用，降低一次性强度；双提交是前端 bug，不应放宽后端 |
| 「记住我」双档（Q4=B） | 需求未提第二档，YAGNI，扩大表单与 TTL 分档复杂度 |
| 滑动续期（Q6=B） | 需每次请求续期 DB + 重发 cookie，且 `httponly` cookie 前端无法感知是否续期，复杂度高收益不明确 |
| 图片验证码（Q7=B） | 与「过期」误报无关，混入会扩大范围 |

## Risks / Trade-offs

- [风险] 前端 `login_expires_at` 依赖客户端时钟，改系统时钟可绕过 → 后端 cookie `max_age=24h` + `expires_at` 才是硬到期点，前端时间戳仅作「同一 tab 内即时」增强，绕过不影响安全。
- [风险] 存量 session 在改配置后仍按 30 天到期 → 明确接受（Non-Goal），重启后端自然过渡。
- [风险] 前端时间戳缺失（旧 sessionStorage 无此字段）→ 检查仅在有值时触发，缺失回落到既有 `fetchShell`/401 路径，不误登出。

## Open Questions

- `login_expires_at` 是否与 `clearShell()` 同步清理（当前设计：是，复用同一 `sessionStorage` 清理函数）。
- 是否需要在 `/login` 页面对「已登录用户」做「直接跳首页」处理（本次不含，属既有行为）。
