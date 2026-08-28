# Progress — react-engine-v11

记录实际完成情况。勾选 `tasks.md` 前的落地依据（代码 + 测试 + 回归）。

## 状态总览

- **tasks.md 进度**：10 / 10（3 组全部完成）
- **阶段**：A–C 全部完成
- **回归**：`python -m pytest tests/ -q` → **242 passed**（含新增 `test_react_engine_v11.py` 9 项）

## 完成记录

### 阶段 A — R1 模型组成员校验（1.1–1.3）
- **1.1** `routers/llm.py` create/update 分支：`type:"group"` 时逐成员校验——`mid == item.id`（自引用）→ 不存在 → 非 `type:"llm"`（组套组）；见 findings F1（自引用先于组套组判定）。
- **1.2** 任一违反 → `return fail(<可读错误>)`，不 `db.commit()`（沿用既有「先 `db.add` 后 return fail 即不落库」模式）。
- **1.3** `type:"llm"` 时 `item.members = json.dumps([])` 清空。
- **测试** 3.1：`test_member_not_exist_rejected` / `test_member_self_reference_rejected` / `test_member_group_nested_rejected` / `test_leaf_members_saved` / `test_llm_members_cleared`。

### 阶段 B — R2 模型组解析环检测（2.1–2.3）
- **2.1 / 2.3** `llm_client.py`：新增 `LLMGroupCycleError(RuntimeError)` + `_GROUP_MAX_DEPTH = 8`；`chat_completion` group 分支加 `_visited`/`_depth`，命中 `llm.id in visited` 或 `_depth >= 8` → `raise LLMGroupCycleError("模型组存在循环引用或嵌套过深")`，循环内 `except LLMGroupCycleError: raise`（见 findings F2）。
- **2.2** `test_llm_chat` group 分支同构加 `_visited`/`_depth`，环 / 超深 → `return "模型组存在循环引用或嵌套过深"`（见 findings F3）。
- **测试** 3.2：`test_group_self_reference_raises_cycle_error` / `test_group_cycle_a_b_a_raises_cycle_error` / `test_group_depth_limit_raises_cycle_error`。

### 阶段 C — 测试与回归（3.1–3.4）
- **3.1–3.3** 新增 `tests/test_react_engine_v11.py`（9 项，全绿）。
  - 3.3 `test_group_flat_failures_single_layer_message`：叶子成员全 529 → `msg.count("模型组全部失败") == 1`（单层，不嵌套爆炸）。
- **3.4** `python -m pytest tests/ -q` → **242 passed**；无新增循环门禁 / 静态阈值（铁律保持，group failover「顺序切换」语义不变）。
