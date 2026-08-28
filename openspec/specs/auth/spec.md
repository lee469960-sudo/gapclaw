# auth Specification

## Purpose

Web 登录与会话管理：保证登录表单只发出一次 `/login.cgi`（消除验证码并发二次消费的误报），会话默认 24 小时到期且 cookie 与数据库会话有效期一致，前端每次导航主动检查过期、后端 401 被动兜底跳转登录页。

## Requirements

### Requirement: 登录验证码防重复提交

登录表单提交 MUST 只发出一次 `/login.cgi` 请求，消除「回车双触发」导致的验证码二次消费误报。验证码 MUST 保持一次性消费语义（成功即消费），前端通过防抖守卫保证单次提交，不为掩盖前端重复提交而放宽后端校验。

#### Scenario: 回车仅触发一次登录

- **WHEN** 用户在登录表单（含验证码输入框）按下回车
- **THEN** 只发出一次 `/login.cgi` 请求
- **AND** 不产生「验证码错误或已过期」的误报

#### Scenario: 点击登录按钮仅触发一次

- **WHEN** 用户点击登录按钮
- **THEN** 只发出一次 `/login.cgi` 请求

#### Scenario: 后端一次性消费不变

- **WHEN** 一次 `/login.cgi` 请求校验验证码成功
- **THEN** 验证码条目被消费（一次性），后续重复提交因无此条目而失败
- **AND** 校验失败时条目保留，可在有效期内重试

### Requirement: 会话 TTL 默认 24 小时可配置

登录会话默认有效期 MUST 为 24 小时（可配置）；登录 `session_id` cookie 的 `max_age` MUST 跟随该配置，与数据库会话 `expires_at` 一致，不再硬编码 30 天。该变更 MUST 只影响新建会话，存量会话按原 `expires_at` 到期。

#### Scenario: 新会话默认 24 小时

- **WHEN** 用户登录成功创建会话
- **THEN** `expires_at = now + session_ttl_hours`，且 `session_ttl_hours` 默认值为 24
- **AND** `session_id` cookie `Max-Age` 为 `session_ttl_hours * 3600` 秒（24h = 86400）

#### Scenario: TTL 可配置

- **WHEN** 环境变量覆盖 `session_ttl_hours`
- **THEN** 新建会话与 cookie 有效期按覆盖值计算

#### Scenario: 存量会话不受影响

- **WHEN** 修改 TTL 默认值
- **THEN** 已存在会话按原 `expires_at` 到期，不强制登出

### Requirement: 前端主动 + 被动双保险过期跳转

用户访问受保护页面时，前端 MUST 每次导航检查登录过期时间戳，已过期则清空会话状态并跳转登录页；任一 API 返回 401 时 MUST 仍跳转登录页兜底。过期语义为固定 24 小时（从登录时刻起算，非滑动续期）。

#### Scenario: 登录成功写入过期时间戳

- **WHEN** 用户登录成功
- **THEN** 前端在会话存储写入 `login_expires_at = 登录时刻 + 24 小时`

#### Scenario: 导航检查过期主动跳转

- **WHEN** 用户导航到受保护页面，且 `login_expires_at` 已过期
- **THEN** 清空会话状态（shell）并跳转 `/login`

#### Scenario: 时间戳缺失不误登出

- **WHEN** 会话存储中无 `login_expires_at`
- **THEN** 不触发主动过期跳转，回落到既有 shell 获取 / 401 校验路径

#### Scenario: 401 兜底跳转

- **WHEN** 任一 API 返回 401
- **THEN** 跳转 `/login`（保留既有行为）
