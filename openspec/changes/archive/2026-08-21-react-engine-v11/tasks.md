## 1. R1 — 模型组成员校验（agent-runtime）

- [x] 1.1 `routers/llm.py` create/update 时：`type:"group"` 校验每个成员 id 存在、为 `type:"llm"`、非组自身
- [x] 1.2 违反任一 → 返回可读错误、不保存（不 commit）
- [x] 1.3 `type:"llm"` 时 `members` 清空/忽略（叶子无成员概念）

## 2. R2 — 模型组解析环检测（agent-runtime）

- [x] 2.1 `llm_client.py` `chat_completion` group 分支加 `visited` 集合（按 id）+ 深度上限（默认 8）
- [x] 2.2 `test_llm_chat` group 分支同样加 `visited` / 深度
- [x] 2.3 命中环 / 超深 → `raise RuntimeError("模型组存在循环引用或嵌套过深")`，不无限递归

## 3. 测试与回归

- [x] 3.1 成员校验单测（成员不存在 / 自引用 / 组套组 → 报错不保存；叶子成员 → 保存成功）
- [x] 3.2 环检测单测（自引用组 → 单层可读错误、不无限递归；循环组 A→B→A → 同）
- [x] 3.3 单层失败单测（叶子成员全 529 → 单层「模型组全部失败: <底层错误>」，不嵌套爆炸）
- [x] 3.4 全量回归绿；无新增循环门禁 / 静态阈值
