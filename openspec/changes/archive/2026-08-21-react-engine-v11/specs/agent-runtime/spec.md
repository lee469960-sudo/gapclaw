## ADDED Requirements

### Requirement: 模型组成员校验

当创建 / 更新 `type:"group"` 的 LLM 资源时，引擎 MUST 校验其 `members`：每个成员 id MUST 对应已存在的 LLM 资源；每个成员 MUST 为 `type:"llm"`（叶子模型，禁止组套组）；成员列表 MUST NOT 包含组自身 id（禁止自引用）。任一违反时 MUST 返回可读错误并拒绝保存（不 commit）。`type:"llm"` 时 `members` MUST 清空 / 忽略。

#### Scenario: 成员不存在

- **WHEN** 保存 `type:"group"` 且某成员 id 在 LLM 资源中查不到
- **THEN** 返回可读错误，拒绝保存

#### Scenario: 成员自引用

- **WHEN** 保存 `type:"group"` 且 `members` 包含组自身 id
- **THEN** 返回可读错误，拒绝保存

#### Scenario: 成员为组（组套组）

- **WHEN** 保存 `type:"group"` 且某成员是 `type:"group"`
- **THEN** 返回可读错误，拒绝保存

#### Scenario: 叶子成员保存成功

- **WHEN** 保存 `type:"group"` 且所有成员均为存在且 `type:"llm"` 的叶子模型
- **THEN** 保存成功

### Requirement: 模型组解析环检测

`chat_completion` / `test_llm_chat` 解析 `type:"group"` 时 MUST 携带 `visited` 集合（按 LLM id）与深度上限（默认 8 层）；命中已访问 id 或超过深度上限时 MUST 抛出可读错误（如「模型组存在循环引用或嵌套过深」），MUST NOT 无限递归。单层叶子成员全部失败时 MUST 抛单层「模型组全部失败: <底层错误>」，不嵌套叠加前缀。

#### Scenario: 自引用 / 循环引用组

- **WHEN** 解析的组存在自引用或 A→B→A 循环
- **THEN** 抛出「模型组存在循环引用或嵌套过深」
- **AND** 不无限递归、不产生上千层嵌套错误消息

#### Scenario: 嵌套过深

- **WHEN** 组嵌套层级超过深度上限
- **THEN** 抛出「模型组存在循环引用或嵌套过深」

#### Scenario: 单层成员全失败

- **WHEN** 组的所有叶子成员都失败（如 529）
- **THEN** 抛出单层「模型组全部失败: <底层错误>」
- **AND** 错误消息不含重复叠加的「模型组全部失败」前缀

#### Scenario: 顺序切换语义不变

- **WHEN** 组某成员请求失败
- **THEN** 仍按既有顺序切换到下一成员（不改变 group failover 语义）
