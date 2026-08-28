## ADDED Requirements

### Requirement: 规划前置验证

当 `_apply_plan` 解析 PLAN 时，引擎 MUST 在本地（不额外调用 LLM）做静态前置检查：① 计划提到的动作（`SHELL:`/`READ:`/`WRITE:`/`SEARCH:`/`MCP:` 等协议前缀）MUST 都落在 `allowed_actions` 内；② 计划是否为空或无法解析出子任务。命中时 MUST 以软 `coach_hint` 提示，MUST NOT 阻断执行，MUST NOT 额外发起 LLM 调用。

#### Scenario: 越权动作软提示

- **WHEN** PLAN 正文提到某个协议动作且该动作不在 `allowed_actions`
- **THEN** 引擎追加一条 coach hint 提示「本 agent 无 <action> 权限」
- **AND** 不阻断执行、不额外调 LLM

#### Scenario: 权限内计划无提示

- **WHEN** PLAN 提到的动作都在 `allowed_actions` 内
- **THEN** 不追加前置验证提示，正常解析子任务

#### Scenario: 计划空或不可解析

- **WHEN** PLAN 为空或无法解析出子任务
- **THEN** 引擎追加一条 coach hint 提示使用带编号 / `[ ]` 清单的计划格式

### Requirement: 工具结果去重复用

当执行 READ / SHELL / SEARCH 动作时，引擎 MUST 先查去重缓存（去重键 = 动作 + 目标：READ 用路径、SHELL 用规范化命令、SEARCH 用 query）；命中且内容未变时 MUST 回显「已缓存，请引用之前结果」指针，MUST NOT 重读 / 重跑 / 把结果重新 push 进上下文。此缓存 MUST 与既有 MCP `query_cache` 形态一致（`{dedup_key: {path, tool, size}}`）。

#### Scenario: 命中回显指针

- **WHEN** 模型重复 READ 同一路径 / SHELL 同一命令 / SEARCH 同一 query，且内容未变
- **THEN** 引擎回显「已缓存，请引用之前结果」指针
- **AND** 不重新执行动作、不把结果重新 push 进上下文

#### Scenario: 未命中正常执行

- **WHEN** 动作 + 目标首次出现
- **THEN** 正常执行并把结果入上下文、写入去重缓存

### Requirement: 去重结果内容失效

READ 结果写缓存时 MUST 记「路径 + mtime/hash」；当 `file_write` 命中同一路径时 MUST 使该路径的 READ 缓存失效，避免文件被改写后仍回旧缓存。

#### Scenario: 改写后失效

- **WHEN** `file_write` 写入某路径，且该路径已存在 READ 缓存
- **THEN** 该路径的 READ 缓存失效，下次 READ 重新执行

#### Scenario: 未改写命中有效

- **WHEN** READ 缓存命中且路径的 mtime/hash 未变化
- **THEN** 返回缓存指针，不重读

### Requirement: 完成度复核交付物证据

`_reflect_final` 判定候选最终回复时，其 prompt MUST 附带 `saved_paths` 中每个交付物的「前 N 行内容摘要」（非仅路径名），使 Verifier 能核对实际数字 / SQL / 字段口径；MUST NOT 发起工具调用、MUST NOT 增加 LLM 轮次，MUST 保持纯裁判与 `_REFLECT_FAIL_CONVERGE=3` 收敛行为不变。

#### Scenario: 交付物摘要进入证据

- **WHEN** `_reflect_final` 构建判定 prompt
- **THEN** prompt 含 `saved_paths` 交付物的前 N 行内容摘要
- **AND** 不发起工具调用、不额外调 LLM

#### Scenario: 依据内容判 FAIL

- **WHEN** 候选回复的关键数字 / SQL / 字段口径与交付物内容不符
- **THEN** Verifier 判 FAIL 并给出修复清单

#### Scenario: 收敛行为不变

- **WHEN** 连续 FAIL 达 `_REFLECT_FAIL_CONVERGE`（3）
- **THEN** 接受候选、记录拒绝原因并收尾（现状不变）

### Requirement: 可重试 HTTP 状态码退避重试

当 LLM 请求返回可重试 HTTP 状态码（529 过载、429 限流、502/503/504 瞬态 5xx）时，引擎 MUST 做指数退避重试：529/429 重试 3 次（约 2s→6s→18s），5xx 重试 5 次，复用既有 `_post_with_transport_retry` 的 2/4/8/16s + jitter 骨架；确定性失败（400/401/2013/1026/1027）MUST NOT 重试，直接报错。组内降级（group failover）MUST 保持现状不改代码。

#### Scenario: 529/429 退避重试

- **WHEN** LLM 请求返回 529 或 429
- **THEN** 引擎按指数退避重试最多 3 次
- **AND** 每次重试之间有 sleep（约 2s→6s→18s）

#### Scenario: 5xx 退避重试

- **WHEN** LLM 请求返回 502/503/504
- **THEN** 引擎按指数退避重试最多 5 次

#### Scenario: 确定性失败不重试

- **WHEN** LLM 请求返回 400/401/2013/1026/1027
- **THEN** 不重试，直接报错

#### Scenario: 组内降级现状不变

- **WHEN** Agent 绑定 `type:"group"` 的 LLM 且某成员请求失败
- **THEN** 按既有 group failover 切换到下一成员，不新增健康度 / 轮换逻辑
