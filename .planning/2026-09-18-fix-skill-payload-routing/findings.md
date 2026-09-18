# Findings

- 根因：`classify_request()` 仍扫描原文并直接返回 HUMAN_WAIT；运行入口只在 requires_human_wait 为 true 时覆盖分类，不会撤销错误的 HUMAN_WAIT。
- 之前局部 tests 只断言 requires_human_wait=false，没有调用 AgentRuntime.run，导致假通过。
- 之前 blanket Skill 豁免还绕过了显式人工确认，需要删除。
- 完整 Skill 内容须与外层指令分离；不能将生成正文中的风险规则当作当前真实操作。
