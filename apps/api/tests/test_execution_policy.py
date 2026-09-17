from app.services.agent_runtime.execution_policy import (
    ExecutionMode,
    ExecutionPolicy,
    classify_request,
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


def test_high_risk_production_operation_waits_for_human():
    assert classify_request("请确认是否删除生产数据") is ExecutionMode.HUMAN_WAIT
    assert classify_request("解释一下生产环境是什么") is ExecutionMode.CHAT


def test_explicit_mode_metadata_wins():
    assert classify_request("随便聊聊", explicit_mode="human_wait") is ExecutionMode.HUMAN_WAIT
