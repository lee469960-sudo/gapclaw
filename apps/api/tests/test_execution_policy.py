from app.services.agent_runtime.execution_policy import (
    ExecutionMode,
    ExecutionPolicy,
    classify_request,
    extract_named_resource_mentions,
)


def test_policy_defaults_are_bounded_and_mode_specific():
    policy = ExecutionPolicy.from_mapping()
    assert policy.chat_max_turns == 1
    assert policy.chat_max_retries == 1
    assert policy.no_progress_limit == 2
    assert policy.max_turns(ExecutionMode.CHAT) == 1
    assert policy.max_turns(ExecutionMode.TASK) == 150


def test_policy_overrides_are_normalized():
    policy = ExecutionPolicy.from_mapping({
        "chat_max_turns": 0,
        "chat_max_retries": "3",
        "no_progress_limit": -4,
        "human_loop_on_uncertainty": False,
    })
    assert policy.chat_max_turns == 1
    assert policy.chat_max_retries == 3
    assert policy.no_progress_limit == 1
    assert policy.human_loop_on_uncertainty is False


def test_ordinary_prompts_use_chat_without_an_extra_classifier():
    assert classify_request("请解释一下什么是向量数据库") is ExecutionMode.CHAT
    assert classify_request("帮我翻译这段话") is ExecutionMode.CHAT


def test_explicit_operations_take_precedence_over_chat():
    assert classify_request("请读取 README.md 并总结") is ExecutionMode.TASK
    assert classify_request("查询数据库中的订单数据") is ExecutionMode.TASK
    assert classify_request("执行本地脚本并检查结果") is ExecutionMode.TASK
    assert classify_request("调用 clickhouse 工具查询视图") is ExecutionMode.TASK
    assert classify_request("根据内容生成一个 skill 包") is ExecutionMode.TASK
    assert classify_request("制作一个 SKILL 包，先调用 SKILL_MD，再使用 WRITE 写入") is ExecutionMode.TASK


def test_named_bound_skill_forces_task_route():
    assert classify_request(
        "请使用 skill-creator 生成一个 Skill 包",
        skill_names=["skill-creator"],
    ) is ExecutionMode.TASK


def test_high_risk_production_operation_waits_for_human():
    assert classify_request("请确认是否删除生产数据") is ExecutionMode.HUMAN_WAIT
    assert classify_request("解释一下生产环境是什么") is ExecutionMode.CHAT


def test_high_risk_examples_inside_skill_payload_do_not_pause_request():
    payload = """制作一个 Skill 包：\n```\n生产环境发布、修改数据库权限等高风险操作必须确认。\n```"""
    from app.services.agent_runtime.execution_policy import requires_human_wait

    assert not requires_human_wait(payload)


def test_skill_generation_payload_without_fences_does_not_pause_request():
    payload = "制作一个SKILL包：\n## 风险规则\n生产环境发布、修改数据库权限等规则都必须人工确认。"
    from app.services.agent_runtime.execution_policy import requires_human_wait

    assert not requires_human_wait(payload)


def test_explicit_mode_metadata_wins():
    assert classify_request("随便聊聊", explicit_mode="human_wait") is ExecutionMode.HUMAN_WAIT


def test_bound_mcp_capability_promotes_implicit_query_to_task():
    hints = [{
        "name": "okx-trader",
        "tags": "交易账户,持仓,余额,订单",
        "description": "查询 OKX 当前账户持仓和余额",
    }]
    assert classify_request("帮我看看okx当前持仓", capability_hints=hints) is ExecutionMode.TASK
    assert classify_request("解释一下什么是持仓", capability_hints=hints) is ExecutionMode.CHAT


def test_stock_selection_intent_promotes_bound_market_capability_to_task():
    hints = [{
        "name": "tushare",
        "tags": "沪深300,涨停,交易日历,选股",
        "description": "股票行情和指数成分数据",
    }]
    request = "选股，条件如下：沪深300，最近5个交易日中至少一日收盘涨停，不选ST，基准日20260918"
    assert classify_request(request, capability_hints=hints) is ExecutionMode.TASK
    assert classify_request("什么是涨停", capability_hints=hints) is ExecutionMode.CHAT
    assert classify_request("沪深300 最近涨停的股票", capability_hints=hints) is ExecutionMode.TASK
    assert classify_request("涨停是什么意思", capability_hints=hints) is ExecutionMode.CHAT


def test_structured_selection_request_routes_with_sparse_market_metadata():
    hints = [{
        "name": "tushareMcp",
        "tags": "金融,数据,A股,tushare,选股",
        "description": "Tushare 官方 MCP Server，提供 A 股、基金、指数、财务、宏观等金融数据接口。",
    }]
    request = "选出标的，条件如下：\n1.沪深300。\n2.最近5个交易日中至少一日收盘涨停。\n3.不选ST。\n4.基准日(20260918)"
    assert classify_request(request, capability_hints=hints) is ExecutionMode.TASK


def test_explicit_resource_name_is_operational_without_hardcoded_mcp_name():
    assert extract_named_resource_mentions("使用 okx-trader 查询当前持仓") == ["okx-trader"]
    assert classify_request("okx-trader 当前持仓") is ExecutionMode.TASK


def test_skill_protocol_markers_are_not_treated_as_unbound_mcp_names():
    assert extract_named_resource_mentions("调用 SKILL_MD，使用 WRITE 写入 dev 工作区") == []
