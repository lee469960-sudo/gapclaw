## Why

线上出现 `RuntimeError('模型组全部失败: 模型组全部失败: 模型组全部失败: …')` —— 同一个前缀被嵌套上千层。根因坐实：**LLM 组存在自引用 / 循环引用成员**，而 group failover 没有任何环检测 / 深度上限，`chat_completion`（`llm_client.py:634-654`）对成员递归调用时无限递归，撞 Python 递归上限后逐层 unwind，每一层 `except Exception` 都再包一层「模型组全部失败:」，最终报出可读性极差的嵌套错误。

两个缺口都无防护：

- **运行时**：`chat_completion` / `test_llm_chat` 的 group 分支（`llm_client.py:563-576, 634-654`）无 `visited` 集合、无深度上限。
- **写入时**：`routers/llm.py` 创建/更新 LLM 时 `item.members = json.dumps(body.members or [])`（`llm.py:92`）零校验——不检查成员是否存在、是否自引用、是否成环。

v10 的 R5 已处理 529 退避重试，但「模型组解析」本身仍是裸奔：一旦真成员全部失败、fall through 到自引用成员，就递归爆炸（本次是第 18 轮所有真成员 529 后首次踩到）。

## What Changes

- **R1 模型组成员校验（写入时）**：`routers/llm.py` 创建/更新 `type:"group"` 时校验 `members`——每个 id 必须对应已存在的 LLM 资源、必须为 `type:"llm"`（叶子，禁止组套组）、且不能是组自身；违反 → 返回可读错误、不保存。
- **R2 模型组解析环检测（运行时）**：`chat_completion` / `test_llm_chat` 的 group 分支加 `visited` 集合 + 深度上限，命中环 / 超深 → 抛可读「模型组存在循环引用」而非无限递归；兜底旧坏数据 / 直接改库绕过校验。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `agent-runtime`: 新增「模型组成员校验」「模型组解析环检测」两条需求。

## Impact

- `apps/api/app/routers/llm.py`（R1 成员校验）
- `apps/api/app/services/llm_client.py`（R2 环检测，`chat_completion` / `test_llm_chat`）
- `apps/api/tests/`（新增单测）
