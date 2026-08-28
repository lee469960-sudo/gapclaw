## Why

单引擎 ReAct 在上一次 consolidation 之后又演进了一代,但有两个问题没有跟上:(1) 引擎存在一个**已确认的静默失败缺陷**——同一轮里 FINAL 与工具步骤共存时,工具被无告警地丢弃;(2) 引擎内部文档 `docs/react-engine.md` 与代码已脱节一代,且 `system_prompt.py` 里硬编码了业务 SOP,违反引擎自身「业务口径写 Skill、不写引擎」的原则。

## What Changes

1. **FINAL + 工具同轮静默丢弃防护(行为变更)**
   - 现状:LLM 在同一轮输出 `FINAL` 与任一非 `PLAN` 工具步骤时,主循环在工具执行循环之前就命中 FINAL 分支,`return`(PASS)或 `continue`(FAIL),被丢弃的工具**既不执行,也不产生任何 coach hint 或步骤告警**。模型若意图「先执行再收尾」,会得到"已完成"但工作实际没做。
   - 变更:FINAL 与非 PLAN 工具步骤同轮时,保留 FINAL 优先的契约(工具仍不执行),但**注入一条软性教练提示 + 一条可见步骤告警**,点明「本轮 FINAL 与工具同时出现,工具未执行」,消除无声失败。不改 FINAL 优先语义,不加硬门禁。

2. **同步 `docs/react-engine.md`(纯文档)**
   - 重写至与当前代码一致:删除已合并/删除的模块(`decision_engine.py` / `tool_router.py` / `tool_executor.py` / `intent_types.py`)、移除已废弃的「硬门禁」阈值表与中文 FINAL 别名、补记新增机制(`_reflect_final` 完成度复核器、`_distill_final` 预算耗尽收尾、`_rescue_leaked_code` 裸代码救援、native function-calling、`AgentRunState` 断点续跑)。

3. **SOP 口径迁出引擎(重构,含可观察行为变化)**
   - `system_prompt.py` 的 `build_tools_desc` 硬编码了 ads 视图 SOP(`list_ads_views → describe_ads_view → query_ads_view`)、「报表 FINAL 四要素」、pandas/xlsx 生成示例、依赖自装提示等业务口径,违反文档第 10 节「业务口径/导出 SOP 写 Skill `references/*.md`,不要写进引擎」。
   - 变更:将这些业务口径迁到对应 Skill 的 `references/*.md`,引擎侧只保留中性、通用的工具/格式/路径引导。
   - **注意**:这会让「绑定了 MCP 但未绑定对应 Skill」的 agent 不再自动获得 ads 导出 SOP(此前由引擎隐式注入)。这是有意的口径收口,需在任务里标注并验证。

## Capabilities

### New Capabilities

- `agent-runtime`:单 LLM 驱动的 ReAct 运行时。本次新增一条需求——协议回复中 FINAL 与工具步骤共现时的处置行为(必须显式告警被跳过的工具)。

### Modified Capabilities

(无——`openspec/specs/` 当前为空,没有既有能力规格。)

## Impact

- **代码**
  - `apps/api/app/services/agent_runtime/runtime.py`:`_run_modular` 的 FINAL 分支增加「FINAL+工具共现」检测与告警(coach hint + step)。
  - `apps/api/app/services/agent_runtime/system_prompt.py`:`build_tools_desc` / `build_minimal_tools_desc` 删除硬编码业务 SOP 文案,替换为中性引导。
  - `apps/api/app/services/agent_runtime/utils.py`:若 `_MCP_TOOL_EXAMPLES` 中 ads 专用示例属于业务口径,一并迁出。
  - Skill `references/*.md`:承接被迁出的业务口径(需确认目标 Skill 存在并有对应 references 文件)。
- **文档**
  - `docs/react-engine.md`:全文重写为当前架构。
- **测试**
  - 新增/更新:同一轮「FINAL+工具」的解析与告警行为测试;SOP 迁出后 `system_prompt` 不再包含 ads 硬编码口径的断言。
- **兼容性**
  - FINAL+工具防护为**增量告警**,不改变工具被丢弃的事实、不改 FINAL 优先契约,向后兼容。
  - SOP 迁出是**可观察行为变化**(见 What Changes #3),对依赖隐式 SOP 的存量 agent 需评估影响。
