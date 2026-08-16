"""ADS metric档案 — soft seed / fallback only（非查数热路径权威）。

查数路由：LLM MetricIntent → 绑定 MCP 的 list/describe → ResourceBinding
（见 mcp_resource_bind）。本模块 VIEW_MAP / aliases 仅在目录缺失或绑定
置信低时作为 soft seed；不得覆盖 live schema。

export_column_plan 仍可引用 registry 片段；运行时字段以 describe 为准。
Cohort vs metric time fields (ADS 档案):
- cohort_time_field (pay crowd): create_time
- metric success pay amount/count/cards: finish_time
- cash success: update_time
- refund flag: status=4 + update_time
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


VIEW_MAP = {
    "user": "view_result_user_info",
    "pay": "view_result_pay_order_log",
    "cash": "view_result_cash_order_log",
    "bet": "view_result_user_bet_log",
    "bet_daily": "view_result_gameuser_betstat_everyday_bygame",
    "channel": "view_result_config_channel",
    "game": "view_result_config_game",
}

# Explicit separation (do not conflate in SQL builders)
COHORT_TIME_FIELD = "create_time"  # pay crowd: window creation
PAY_METRIC_TIME_FIELD = "finish_time"  # successful pay amount/count/cards
CASH_METRIC_TIME_FIELD = "update_time"
REFUND_TIME_FIELD = "update_time"


@dataclass(frozen=True)
class MetricSpec:
    metric_id: str
    column_name: str
    aliases: tuple[str, ...]
    role: str
    view: str
    source_fields: tuple[str, ...]
    join_key: str = "uid"
    fetch_mode: str = "agg"
    time_field: str = ""
    filter_sql: str = ""
    select_expr: str = ""
    group_by: str = "uid"
    unit_scale: int = 1
    output_name: str = ""
    description: str = ""
    validation_rules: tuple[str, ...] = ()
    cost_level: str = "light"  # light | heavy
    failure_policy: str = "optional"  # core | optional | soft_fail
    kind: str = "aggregate"
    needs_game_dim: bool = False
    flag_label: str = ""
    depends_on: tuple[str, ...] = ()
    post: str = ""
    note: str = ""
    incomplete_ok: bool = False
    extra_sources: tuple[dict[str, Any], ...] = ()
    compute: str = ""
    统计方法: str = ""
    数据来源: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _pay_amt(**kwargs: Any) -> MetricSpec:
    return MetricSpec(
        role="pay",
        view=VIEW_MAP["pay"],
        source_fields=("uid", "price", "status", "finish_time"),
        fetch_mode="agg",
        time_field=PAY_METRIC_TIME_FIELD,
        filter_sql="status = 2",
        select_expr="uid, sum(price) / 100 AS pay_sum",
        group_by="uid",
        unit_scale=100,
        validation_rules=("status=2", "finish_time", "sum(price)/100"),
        failure_policy="core",
        cost_level="light",
        kind="aggregate",
        compute="sum(price)/100",
        统计方法="status=2，finish_time 窗内 → SUM(price)/100",
        数据来源="`view_result_pay_order_log`",
        **kwargs,
    )


# Ordered: longer / more specific aliases first via match_metric sort
METRICS: tuple[MetricSpec, ...] = (
    MetricSpec(
        metric_id="register_time",
        column_name="注册时间",
        aliases=("注册时间",),
        role="user",
        view=VIEW_MAP["user"],
        source_fields=("uid", "register_time"),
        fetch_mode="field",
        time_field="",
        select_expr="uid, register_time, channel_id, sc, ban_type, limit_type",
        group_by="",
        failure_policy="core",
        kind="field",
        统计方法="register_time 格式化",
        数据来源="`view_result_user_info`",
        description="用户注册时间",
    ),
    MetricSpec(
        metric_id="user_id",
        column_name="用户ID",
        aliases=("用户ID", "用户Id", "用户id", "uid"),
        role="user",
        view=VIEW_MAP["user"],
        source_fields=("uid",),
        fetch_mode="field",
        select_expr="uid, register_time, channel_id, sc, ban_type, limit_type",
        group_by="",
        failure_policy="core",
        kind="field",
        统计方法="uid",
        数据来源="`view_result_user_info`",
        description="用户主键",
    ),
    MetricSpec(
        metric_id="first_name",
        column_name="姓",
        aliases=("姓",),
        role="user",
        view=VIEW_MAP["user"],
        source_fields=("uid", "first_name", "surname"),
        fetch_mode="field",
        select_expr="uid, first_name, surname",
        group_by="",
        failure_policy="optional",
        kind="field",
        compute="first_name",
        统计方法="first_name/surname",
        数据来源="`view_result_user_info`",
        description="用户姓氏",
    ),
    MetricSpec(
        metric_id="last_name",
        column_name="名",
        aliases=("名",),
        role="user",
        view=VIEW_MAP["user"],
        source_fields=("uid", "last_name", "name"),
        fetch_mode="field",
        select_expr="uid, last_name, name",
        group_by="",
        failure_policy="optional",
        kind="field",
        compute="last_name",
        统计方法="last_name/name",
        数据来源="`view_result_user_info`",
        description="用户名字",
    ),
    MetricSpec(
        metric_id="phone",
        column_name="手机",
        aliases=("手机",),
        role="user",
        view=VIEW_MAP["user"],
        source_fields=("uid", "mobile", "phone"),
        fetch_mode="field",
        select_expr="uid, mobile, phone",
        group_by="",
        failure_policy="optional",
        kind="field",
        compute="mobile",
        统计方法="mobile/phone",
        数据来源="`view_result_user_info`",
        description="用户手机号",
    ),
    MetricSpec(
        metric_id="email",
        column_name="邮箱",
        aliases=("邮箱",),
        role="user",
        view=VIEW_MAP["user"],
        source_fields=("uid", "email"),
        fetch_mode="field",
        select_expr="uid, email",
        group_by="",
        failure_policy="optional",
        kind="field",
        compute="email",
        统计方法="email",
        数据来源="`view_result_user_info`",
        description="用户邮箱",
    ),
    MetricSpec(
        metric_id="register_channel",
        column_name="注册渠道",
        aliases=("注册渠道", "渠道名称", "渠道名", "渠道"),
        role="user",
        view=VIEW_MAP["user"],
        source_fields=("uid", "channel_id"),
        fetch_mode="lookup",
        join_key="channel_id",
        select_expr="",
        group_by="",
        failure_policy="optional",
        kind="lookup",
        extra_sources=(
            {"role": "user", "fields": ["uid", "channel_id", "register_time"]},
            {"role": "channel", "fields": ["channel_id", "channel_name", "name"]},
        ),
        compute="channel_name",
        统计方法="channel_id join 渠道维表 → 渠道名称",
        数据来源="`view_result_user_info` + `view_result_config_channel`",
        description="注册渠道名",
    ),
    _pay_amt(
        metric_id="pay_sum",
        column_name="总充值金额",
        aliases=("总充值金额", "总充值", "充值金额", "充值"),
        output_name="总充值金额",
        description="成功充值总额（美元）",
    ),
    MetricSpec(
        metric_id="cash_sum",
        column_name="总提现金额",
        aliases=("总提现金额", "总提现", "提现金额", "提现"),
        role="cash",
        view=VIEW_MAP["cash"],
        source_fields=("uid", "amount", "status", "update_time"),
        fetch_mode="agg",
        time_field=CASH_METRIC_TIME_FIELD,
        filter_sql="status = 2",
        select_expr="uid, sum(amount) / 100 AS cash_sum",
        unit_scale=100,
        validation_rules=("status=2", "update_time", "sum(amount)/100"),
        failure_policy="optional",
        kind="aggregate",
        compute="sum(amount)/100",
        统计方法="status=2，update_time 窗内 → SUM(amount)/100",
        数据来源="`view_result_cash_order_log`",
        description="成功提现总额（美元）",
    ),
    MetricSpec(
        metric_id="balance_sc",
        column_name="当前余额(SC)",
        aliases=("当前余额(SC)", "当前余额", "SC余额", "余额"),
        role="user",
        view=VIEW_MAP["user"],
        source_fields=("uid", "sc"),
        fetch_mode="field",
        select_expr="uid, sc",
        group_by="",
        unit_scale=100,
        failure_policy="core",
        kind="field",
        compute="sc/100",
        统计方法="sc/100",
        数据来源="`view_result_user_info`",
        description="当前 SC 余额",
    ),
    MetricSpec(
        metric_id="bet_sum_sc",
        column_name="总下注金额(SC)",
        aliases=(
            "总下注金额(SC)",
            "总下注金额",
            "总下注",
            "下注金额",
            "总投注金额",
            "总投注",
            "投注金额",
            "投注",
        ),
        role="bet",
        view=VIEW_MAP["bet_daily"],
        source_fields=("uid", "bet_value", "goods_type"),
        fetch_mode="agg",
        time_field="stat_date",
        filter_sql="goods_type IN (1, 4)",
        select_expr="uid, sum(bet_value) / 100 AS bet_sum",
        unit_scale=100,
        validation_rules=("everyday_bygame", "goods_type IN (1, 4)"),
        failure_policy="optional",
        kind="aggregate",
        compute="sum(bet_value)/100",
        统计方法="goods_type IN (1,4) → SUM(bet_value)/100",
        数据来源="`view_result_gameuser_betstat_everyday_bygame`",
        description="SC 下注总额",
    ),
    MetricSpec(
        metric_id="win_sum_sc",
        column_name="总返奖金额(SC)",
        aliases=(
            "总返奖金额(SC)",
            "总返奖金额",
            "总返奖",
            "返奖金额",
            "总返浆金额(SC)",
            "总返浆金额",
            "总返浆",
            "返浆金额",
            "返浆",
            "总返奖",
            "返奖",
        ),
        role="bet",
        view=VIEW_MAP["bet_daily"],
        source_fields=("uid", "result_value", "goods_type"),
        fetch_mode="agg",
        time_field="stat_date",
        filter_sql="goods_type IN (1, 4)",
        select_expr="uid, sum(result_value) / 100 AS win_sum",
        unit_scale=100,
        validation_rules=("everyday_bygame", "goods_type IN (1, 4)"),
        failure_policy="optional",
        kind="aggregate",
        统计方法="goods_type IN (1,4) → SUM(result_value)/100",
        数据来源="`view_result_gameuser_betstat_everyday_bygame`",
        description="SC 返奖总额",
    ),
    MetricSpec(
        metric_id="bet_cnt",
        column_name="下注次数",
        aliases=("下注次数", "投注次数", "SC投注次数"),
        role="bet",
        view=VIEW_MAP["bet_daily"],
        source_fields=("uid", "bet_nums", "goods_type"),
        fetch_mode="agg",
        time_field="stat_date",
        filter_sql="goods_type IN (1, 4)",
        select_expr="uid, sum(bet_nums) AS bet_cnt",
        validation_rules=("everyday_bygame", "goods_type IN (1, 4)"),
        failure_policy="optional",
        kind="aggregate",
        统计方法="goods_type IN (1,4) → SUM(bet_nums)",
        数据来源="`view_result_gameuser_betstat_everyday_bygame`",
        description="SC 投注次数",
    ),
    MetricSpec(
        metric_id="turnover_ratio",
        column_name="流水倍数",
        aliases=("流水倍数",),
        role="pay",
        view="",
        source_fields=(),
        fetch_mode="derived",
        select_expr="",
        group_by="",
        failure_policy="optional",
        kind="compute",
        depends_on=("总下注", "总充值"),
        compute="总下注金额/总充值金额",
        统计方法="总下注金额 / 总充值金额（分母为 0 则留空）",
        数据来源="派生（总下注 / 总充值）",
        description="时间段内总下注 / 总充值",
        validation_rules=("derived", "bet_sum/pay_sum"),
    ),
    MetricSpec(
        metric_id="pay_streak",
        column_name="连续充值次数",
        aliases=("连续充值次数", "连续充值"),
        role="pay",
        view=VIEW_MAP["pay"],
        source_fields=("uid", "create_time", "status"),
        fetch_mode="sequence",
        time_field="create_time",
        select_expr="uid, create_time, status",
        group_by="",
        cost_level="heavy",
        failure_policy="soft_fail",
        incomplete_ok=True,
        kind="sequence",
        note="有界事件流；预算内完不成则标未完整",
        统计方法="两次游戏行为之间的最大连续充值次数（heavy/soft_fail；完不成标未完整）",
        数据来源="`view_result_pay_order_log` + bet 时序",
        description="连续充值（可降级未完整）",
    ),
    MetricSpec(
        metric_id="top_game_by_bet",
        column_name="SC投注金额最多的游戏",
        aliases=("SC投注金额最多的游戏", "投注金额最多的游戏", "最多游戏", "游戏名", "游戏"),
        role="bet",
        view=VIEW_MAP["bet"],
        source_fields=("uid", "game_id", "bet_sc", "type"),
        fetch_mode="top_n",
        time_field="create_time",
        filter_sql="type = 1",
        select_expr="uid, game_id, sum(bet_sc) AS amt",
        group_by="uid, game_id",
        cost_level="heavy",
        failure_policy="soft_fail",
        incomplete_ok=True,
        kind="lookup",
        needs_game_dim=True,
        post="per_uid_argmax",
        extra_sources=(
            {"role": "bet", "fields": ["uid", "game_id", "bet_sc", "type"]},
            {"role": "game", "fields": ["game_id", "game_name", "name"]},
        ),
        统计方法="type=1 → 按 uid 取下注额最大 game_id，join 游戏名",
        数据来源="`view_result_user_bet_log` + `view_result_config_game`",
        description="下注最多的游戏",
    ),
    MetricSpec(
        metric_id="banned",
        column_name="是否被封禁",
        aliases=("是否被封禁", "封禁状态", "被封禁", "封禁"),
        role="user",
        view=VIEW_MAP["user"],
        source_fields=("uid", "ban_type", "limit_type"),
        fetch_mode="field",
        select_expr="uid, ban_type, limit_type",
        group_by="",
        failure_policy="core",
        kind="field",
        统计方法="ban_type/limit_type → 封禁标签",
        数据来源="`view_result_user_info`",
        description="封禁标记",
    ),
    MetricSpec(
        metric_id="refund_amount",
        column_name="退款金额",
        aliases=("总退款金额", "退款金额", "退款总额"),
        role="pay",
        view=VIEW_MAP["pay"],
        source_fields=("uid", "price", "amount", "status", "update_time"),
        fetch_mode="agg",
        time_field=REFUND_TIME_FIELD,
        filter_sql="status = 4",
        select_expr="uid, sum(price) / 100 AS refund_sum",
        unit_scale=100,
        validation_rules=("status=4", "update_time", "sum(price)/100"),
        failure_policy="optional",
        kind="aggregate",
        compute="sum(price)/100",
        统计方法="status=4，update_time 窗内 → SUM(price)/100",
        数据来源="`view_result_pay_order_log`",
        description="成功退款总额（美元）",
    ),
    MetricSpec(
        metric_id="has_refund",
        column_name="是否有退款",
        aliases=("是否有退款", "是否退款"),
        role="pay",
        view=VIEW_MAP["pay"],
        source_fields=("uid", "status", "update_time"),
        fetch_mode="flag",
        time_field=REFUND_TIME_FIELD,
        filter_sql="status = 4",
        select_expr="uid, count() AS refund_cnt",
        flag_label="有",
        failure_policy="optional",
        kind="flag",
        统计方法="status=4，update_time 窗内 → 显示「有」",
        数据来源="`view_result_pay_order_log`",
        description="成功退款标记",
        validation_rules=("status=4", "update_time"),
    ),
    MetricSpec(
        metric_id="pay_card_cnt",
        column_name="充值银行卡数量",
        aliases=("充值银行卡数量", "充值银行卡"),
        role="pay",
        view=VIEW_MAP["pay"],
        source_fields=(
            "uid", "account", "sub_channel", "channel_id", "status", "finish_time",
        ),
        fetch_mode="agg",
        time_field=PAY_METRIC_TIME_FIELD,
        filter_sql=(
            "channel_id = 11 AND upper(toString(sub_channel)) = 'CARD' AND status = 2"
        ),
        select_expr=(
            "uid, count(DISTINCT JSONExtractString(toString(account), 'cardNumber')) "
            "AS card_cnt"
        ),
        validation_rules=("JSONExtractString", "cardNumber", "channel_id=11"),
        failure_policy="optional",
        kind="aggregate",
        统计方法=(
            "status=2，finish_time 窗内，channel_id=11 AND sub_channel=CARD → "
            "DISTINCT JSONExtractString(account, cardNumber)"
        ),
        数据来源="`view_result_pay_order_log`",
        description="APTPAY 充值卡排重",
    ),
    MetricSpec(
        metric_id="cash_card_cnt",
        column_name="提现银行卡数量",
        aliases=("提现银行卡数量", "提现银行卡"),
        role="cash",
        view=VIEW_MAP["cash"],
        source_fields=(
            "uid", "account", "sub_channel", "channel_id", "status", "update_time",
        ),
        fetch_mode="agg",
        time_field=CASH_METRIC_TIME_FIELD,
        filter_sql=(
            "channel_id = 11 AND upper(toString(sub_channel)) = 'CARD' AND status = 2"
        ),
        select_expr=(
            "uid, count(DISTINCT JSONExtractString(toString(account), "
            "'disbursementNumber')) AS card_cnt"
        ),
        validation_rules=("JSONExtractString", "disbursementNumber", "channel_id=11"),
        failure_policy="optional",
        kind="aggregate",
        统计方法=(
            "status=2，update_time 窗内，channel_id=11 AND sub_channel=CARD → "
            "DISTINCT JSONExtractString(account, disbursementNumber)"
        ),
        数据来源="`view_result_cash_order_log`",
        description="APTPAY 提现卡排重",
    ),
    # Extra: pay count (not always in 16-col brief but same口径 family)
    MetricSpec(
        metric_id="pay_cnt",
        column_name="充值次数",
        aliases=("充值次数",),
        role="pay",
        view=VIEW_MAP["pay"],
        source_fields=("uid", "status", "finish_time"),
        fetch_mode="agg",
        time_field=PAY_METRIC_TIME_FIELD,
        filter_sql="status = 2",
        select_expr="uid, count() AS pay_cnt",
        validation_rules=("status=2", "finish_time", "count"),
        failure_policy="optional",
        kind="aggregate",
        统计方法="status=2，finish_time 窗内 → COUNT(*)（与总充值金额同成功单口径）",
        数据来源="`view_result_pay_order_log`",
        description="成功充值次数",
    ),
)


GOLDEN_16_HEADERS: tuple[str, ...] = (
    "注册时间",
    "用户ID",
    "注册渠道",
    "总充值金额",
    "总提现金额",
    "当前余额(SC)",
    "总下注金额(SC)",
    "总返奖金额(SC)",
    "下注次数",
    "流水倍数",
    "连续充值次数",
    "SC投注金额最多的游戏",
    "是否被封禁",
    "是否有退款",
    "充值银行卡数量",
    "提现银行卡数量",
)


def match_metric(header: str) -> MetricSpec | None:
    """Soft-seed lookup only. First match wins by longest alias in header."""
    text = str(header or "").strip()
    if not text:
        return None
    best: MetricSpec | None = None
    best_len = -1
    for m in METRICS:
        for alias in m.aliases:
            a = str(alias or "").strip()
            if not a or a not in text:
                continue
            if len(a) > best_len:
                best = m
                best_len = len(a)
    return best


def metric_to_column_fragment(m: MetricSpec) -> dict[str, Any]:
    """Convert MetricSpec → export_column_plan rule fragment."""
    sources: list[dict[str, Any]] = list(m.extra_sources) if m.extra_sources else []
    if not sources and m.role:
        src_item: dict[str, Any] = {
            "role": m.role,
            "fields": list(m.source_fields),
        }
        if m.view:
            src_item["view"] = m.view
        sources = [src_item]
    else:
        # Ensure contract view is present on primary role source when missing
        for s in sources:
            if (
                isinstance(s, dict)
                and str(s.get("role") or "") == m.role
                and m.view
                and not str(s.get("view") or "").strip()
            ):
                s["view"] = m.view

    mode = m.fetch_mode
    agg: dict[str, Any] = {}
    if mode == "derived":
        agg = {
            "mode": "derived",
            "formula": "bet_sum / nullIf(pay_sum, 0)",
        }
    elif mode == "lookup" and not m.select_expr:
        agg = {"mode": "lookup", "roles": ["user", "channel"]}
    elif mode in ("agg", "flag", "field", "top_n", "sequence"):
        agg = {
            "role": m.role,
            "view": m.view,
            "mode": mode,
            "select": m.select_expr,
            "group_by": m.group_by,
            "filter_sql": m.filter_sql,
            "scale": m.unit_scale,
        }
        if m.time_field:
            agg["time_field"] = m.time_field
        if m.flag_label:
            agg["flag_label"] = m.flag_label
        if m.needs_game_dim:
            agg["needs_game_dim"] = True
        if m.post:
            agg["post"] = m.post
        if m.note:
            agg["note"] = m.note
        if m.incomplete_ok:
            agg["incomplete_ok"] = True
    frag: dict[str, Any] = {
        "kind": m.kind,
        "fetch_mode": mode,
        "sources": sources,
        "join_on": m.join_key,
        "filter": {},
        "compute": m.compute or m.description,
        "口径": m.description or m.compute,
        "time_field": m.time_field,
        "agg_spec": agg,
        "数据来源": m.数据来源 or f"`{m.view}`" if m.view else "—",
        "统计方法": m.统计方法 or m.description,
        "metric_id": m.metric_id,
        "cost_level": m.cost_level,
        "failure_policy": m.failure_policy,
        "validation_rules": list(m.validation_rules),
    }
    if m.depends_on:
        frag["depends_on"] = list(m.depends_on)
    return frag


def list_metrics_by_policy(policy: str) -> list[MetricSpec]:
    return [m for m in METRICS if m.failure_policy == policy]
