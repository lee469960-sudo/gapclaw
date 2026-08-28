# Design — react-engine-v11

## Context

动机与根因见 `proposal.md`「Why」。铁律贯穿：**无任何静态硬门禁**——本 V11 是「写入校验 + 运行时兜底」的健壮性修复，不引入循环级阈值 / 计数器 / 硬停，也不改变 group failover 的语义（成员失败仍顺序切换）。

- 现状：`chat_completion`（`llm_client.py:634-654`）group 分支遍历 `members`、逐成员递归 `chat_completion`，`except Exception` 吞错后 `continue`，全部失败 `raise RuntimeError(f"模型组全部失败: {last_err}")`；无 `visited` / 深度上限。
- `test_llm_chat`（`llm_client.py:563-576`）同构递归，被 `routers/llm.py` 的「test」动作（`llm.py:113`）调用，同样裸奔。
- `routers/llm.py:81,92` 创建/更新时 `item.type` / `item.members` 直接落库，零校验。
- v10 R5 已入主 spec：可重试状态码退避重试，「组内降级现状不变」场景（主 spec 第 698-701 行）仍成立——本 V11 不改变「失败切换下一成员」语义，只加环防护。

## Goals / Non-Goals

**Goals:**
- 自引用 / 循环引用组不再无限递归，报出单层可读错误。
- 坏配置在保存时即被拦截（前置校验），旧坏数据在运行时被兜底。
- group failover 的「平铺多模型降级」语义不变。

**Non-Goals:**
- 不改 group failover 的「顺序切换」语义。
- 不加 group 健康度 / 轮换 / 优先级（v10 D6 已排除）。
- 不迁移既有坏数据（靠运行时兜底 + 用户重新保存修复）。

## Decisions

### D1: 双保险

写入时校验（R1，防新坏数据）+ 运行时环检测（R2，兜底旧坏数据 / 直接改库）。两层独立、各自可测，任一失效都有另一层接住。
- **为何**：只做写入校验，既有的自引用组在重新保存前仍会炸；只做运行时兜底，UI 仍能保存出环。双保险才根治。

### D2: 禁止组套组（成员必须为叶子 `type:"llm"`）

R1 校验要求每个成员必须是 `type:"llm"` 的叶子模型，禁止成员是 group。
- **为何**：group failover 的语义就是「平铺多模型降级」；组套组无真实需求，却引入递归复杂度和本次这类 bug。禁止后 group 只解析一层，递归天然消失。
- 若后续确有「组套组」需求，可放宽 R1 为「允许 group 成员」，仅靠 R2 环检测兜底（见 Open Questions）。

### D3: 运行时 `visited` + 深度上限

R2 在 `chat_completion` / `test_llm_chat` 的 group 递归中传入 `visited` 集合（按 LLM id）与深度上限（默认 8 层）。命中已访问 id 或超深 → `raise RuntimeError("模型组存在循环引用或嵌套过深")`。
- **为何**：兜底旧坏数据与「直接改库」绕过 R1 的场景；深度上限 8 足够覆盖合理嵌套（即便未来放宽允许组套组），又挡住无限递归。

### D4: 铁律不变

纯健壮性修复，无静态硬门禁；group failover 的软语义（顺序切换、无健康度/轮换）保持不变。

## Risks / Trade-offs

- [风险] 禁止组套组可能挡住未来「组套组」需求。→ 缓解：R2 深度上限仍支持合理嵌套；真需要时放宽 R1 一行校验即可。
- [风险] `visited` 集合需透传递归参数，改动面略增。→ 缓解：仅 `chat_completion` / `test_llm_chat` 两个函数，`visited` 作为可选参数默认空集，不破坏既有调用。
- [风险] 深度上限 8 若被误判为「嵌套过深」。→ 缓解：8 已远超实际用途；报错文案清晰可定位。

## Open Questions

- 是否需要支持「组套组」（若需要，R1 放宽为允许 group 成员，仅靠 R2 环检测兜底）。
- 成员校验错误返回的具体文案与 HTTP 状态码。
- 深度上限取值（默认 8）是否需要可配置。
