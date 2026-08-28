# Task Plan: auth-session-v1（OpenSpec 执行计划）

<!--
  本文件是 planning-with-files 的执行计划，把 OpenSpec tasks.md 映射为执行 phases。
  ⚠️ 正式任务来源 = openspec/changes/auth-session-v1/tasks.md（唯一，勾选以它为准）。
  ⚠️ 验收标准来源 = openspec/changes/auth-session-v1/specs/auth/spec.md（WHEN/THEN/AND）。
  ⚠️ 需求输入 = docs/exploration/auth-session-v1.md。
  本文件不重新定义需求/任务，只做 phase 分组 + 状态跟踪 + 决策/错误记录。
  每完成一个 OpenSpec task：先更新 progress.md → 确认代码+测试完成 → 再勾选 tasks.md。
-->

## Goal

实施 OpenSpec change `auth-session-v1`（登录验证码防重复提交 / 会话 TTL 默认 24 小时可配置 / 前端主动+被动双保险过期跳转），全部满足 specs 验收标准、测试绿、不加「记住我」双档、不改图片验证码、不强制失效存量会话、不新增定时器轮询。

## Next Step

全部 4 个 phase 已完成（13/13 tasks 勾选）。下一步：`/opsx:verify auth-session-v1` 复核后 `/opsx:archive auth-session-v1`。

## Current Phase

Phase 4（已完成）

## Phases

### Phase 1: 登录验证码防重复提交（tasks 1.1–1.3）
- 1.1 `Login.vue` `onLogin` 顶部加 `if (loading.value) return` 防抖守卫
- 1.2 去掉验证码输入框 `@keyup.enter="onLogin"`，只保留表单 `@submit.prevent="onLogin"` 单一入口
- 1.3 后端 `captcha.py` 一次性消费语义保持不变（不修改）
- **Status:** done

### Phase 2: 会话 TTL 默认 24 小时可配置（tasks 2.1–2.2）
- 2.1 `config.py` `session_ttl_hours` 默认 `720 → 24`
- 2.2 `auth.py` `session_id` cookie `max_age` 从 `720*3600` 改为 `settings.session_ttl_hours * 3600`
- **Status:** done

### Phase 3: 前端主动 + 被动双保险过期跳转（tasks 3.1–3.4）
- 3.1 `session.js` 新增 `login_expires_at` 存取与清理（复用 `sessionStorage`，与 `clearShell` 同步清）
- 3.2 `Login.vue` 登录成功后写 `login_expires_at = now + 24h`
- 3.3 `router.js` `beforeEach` 增加过期检查：有值且已过期 → 清 shell → 跳 `/login`
- 3.4 `api.js` 401 跳转兜底保留不变
- **Status:** done

### Phase 4: 验证与回归（tasks 4.1–4.4）
- 4.1 验证码防重复提交：回车仅一次 `/login.cgi`，无「过期」误报
- 4.2 TTL 24h：新登录 `expires_at` 为 24h、cookie `Max-Age` 为 86400
- 4.3 过期跳转：`login_expires_at` 过期后导航跳 `/login`；401 仍跳 `/login`
- 4.4 存量会话不受影响（改配置后旧会话仍按原到期）
- **Status:** done

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| （执行中产生的新决策记录于此；设计决策见 design.md D1–D5） | |

## Errors Encountered

| Error | Resolution |
|-------|------------|
| （执行中产生） | |
