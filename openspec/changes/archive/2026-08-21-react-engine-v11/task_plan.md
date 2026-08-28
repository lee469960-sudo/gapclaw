# Task Plan — react-engine-v11

> **任务唯一来源**：`tasks.md`（10 个 task）。本文件不重新定义需求，仅将 tasks.md 映射为可执行阶段，标注代码锚点与依赖。需求语义一律以 `specs/agent-runtime/spec.md` 与 `tasks.md` 为准。

## 阶段总览

| 阶段 | 需求 | tasks.md 条目 | 依赖 |
|------|------|--------------|------|
| A | R1 模型组成员校验（写入时） | 1.1–1.3 | 无 |
| B | R2 模型组解析环检测（运行时） | 2.1–2.3 | 无 |
| C | 测试与回归 | 3.1–3.4 | A + B 全部 |

A / B 相互独立（写入校验 vs 运行时兜底），可并行推进；C 收尾。

## 阶段 A — R1 模型组成员校验（tasks 1.1–1.3）

- **锚点**：
  - `apps/api/app/routers/llm.py:73-100` create/update 分支；`llm.py:92` `item.members = json.dumps(body.members or [])`（当前零校验直接落库）。
  - `llm.py:16-32` `LLMBody`（`type` 默认 `"llm"`、`members: list[str] | None`）。
  - `app.models.LLMResource`（`type` / `members` 列，叶子 `type:"llm"`、组 `type:"group"`）。
- **落地**：
  - 1.1 在 `item.members` 赋值前，当 `body.type == "group"` 时校验：每个 member id 经 `db.query(LLMResource).filter(id==mid).first()` 存在；且其 `type == "llm"`；且 `mid != body.id`（自引用）。
  - 1.2 任一违反 → `return fail(<可读错误>)`，**不 `db.commit()`**（当前 `item` 已 `db.add`，直接 return 即不落库；update 分支同理）。
  - 1.3 当 `body.type == "llm"` 时 `members` 清空（`item.members = json.dumps([])`）或忽略传入的 `body.members`。
- **验收**（对应 tasks 3.1）：成员不存在 / 自引用 / 组套组 → 报错不保存；叶子成员 → 保存成功；`type:"llm"` 时 members 被清空。

## 阶段 B — R2 模型组解析环检测（tasks 2.1–2.3）

- **锚点**：
  - `apps/api/app/services/llm_client.py:593-606` `test_llm_chat` group 分支（`if llm.type == "group":` 遍历 `members` 递归 `test_llm_chat(child, ...)`，无 visited/深度）。
  - `apps/api/app/services/llm_client.py:654` `chat_completion` group 分支（`llm_client.py:664-684`：遍历 `members` 递归 `chat_completion(child, ...)`，`except Exception → last_err → continue`，全部失败 `raise RuntimeError(f"模型组全部失败: {last_err}")`）。
- **落地**：
  - 2.1 `chat_completion` 增加内部可选参数（如 `_visited: set | None = None, _depth: int = 0`），group 分支：进入时 `visited = visited or set()`；命中 `llm.id in visited` 或 `_depth >= 8` → 抛可读错误；否则 `visited.add(llm.id)` 并 `_depth + 1` 传给递归调用。递归调用是 `chat_completion(child, ..., _visited=visited, _depth=_depth+1)`。
  - 2.2 `test_llm_chat` 同构：加 `_visited`/`_depth`，递归 `test_llm_chat(child, message, db, _visited=..., _depth=...)`。
  - 2.3 命中环/超深 → `raise RuntimeError("模型组存在循环引用或嵌套过深")`（`test_llm_chat` 可用等价的 `return "模型组存在循环引用或嵌套过深"` 或抛错后由调用方转成可读文案——以 `routers/llm.py:113` 的 `test` 动作能返回可读文案为准）。
- **验收**（对应 tasks 3.2 / 3.3）：自引用组 / A→B→A 循环组 → 单层可读错误、不无限递归；叶子成员全 529 → 单层「模型组全部失败: <底层错误>」，不嵌套叠加前缀。

## 阶段 C — 测试与回归（tasks 3.1–3.4）

- **锚点**：`apps/api/tests/` 新增 `test_react_engine_v11.py`（或并入既有 LLM 测试），复用 `test_react_engine_v6.py` / `test_llm_transport_retry.py` 的 `_make_db()`（sqlite 内存库）+ `_StatusClient` mock 模式。
- **落地**：
  - 3.1 成员校验单测：构造 `LLMResource` 行（叶子 / 组 / 自引用 / 组套组），断言 create/update 的报错与不落库。
  - 3.2 环检测单测：自引用组、A→B→A 循环组 → 单层可读错误，`RecursionError` 不出现。
  - 3.3 单层失败单测：叶子成员全 529（mock httpx 返回 529）→ 断言错误消息只含一层「模型组全部失败」，不含重复前缀。
  - 3.4 `python -m pytest tests/ -q` 全绿；无新增循环门禁 / 静态阈值（铁律）。
- **验收**：tasks 3.1–3.4 逐条对应；3.4 为最终回归。
