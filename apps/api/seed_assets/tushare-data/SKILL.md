---
name: tushare-data
description: 面向中文自然语言的 Tushare 金融数据研究 Skill。适用于 A 股、指数、ETF/基金、财务、估值、资金流、板块概念与宏观数据等场景。优先配合 tushareMcp 使用 MCP 工具拉取数据。
author: tushare.pro
version: 1.1.16
---

# tushare-data

把自然语言财经数据请求，转成可执行的 Tushare 数据工作流。

## 使用方式

1. **优先使用 MCP**：Agent 已绑定 `tushareMcp` 时，通过 `MCP: <tool_name> {json}` 调用，例如：
   - `MCP: trade_cal {"exchange":"SSE","start_date":"20250701","end_date":"20250710","is_open":"1"}`
   - `MCP: daily {"ts_code":"600519.SH","start_date":"20250601","end_date":"20250701"}`
   - `MCP: stock_basic {"list_status":"L","fields":["ts_code","name","industry"]}`

2. **Python 脚本**（沙箱内）：需 `TUSHARE_TOKEN` 环境变量，使用 `tushare` 包。

## 典型场景

- 单票行情：近 20 交易日走势、涨跌幅、成交量
- 财务质量：最近 8 季度营收/净利润/ROE 趋势
- 板块轮动：行业/概念区间强弱排行
- 资金流：北向、主力、龙虎榜
- 宏观：CPI、PMI、利率
- 导出：CSV 供后续分析

## 输出规范

1. 一句话结论
2. 数据范围与口径
3. 关键指标表格
4. 限制说明（权限/积分/空数据原因）
5. 如有文件输出，给出 workplace 路径

## 常用 MCP 工具

- `stock_basic` — 股票列表
- `trade_cal` — 交易日历
- `daily` — 日线行情
- `daily_basic` — PE/PB/市值
- `fina_indicator` — 财务指标
- `income` / `balancesheet` / `cashflow` — 财报
- `moneyflow` / `moneyflow_hsgt` — 资金流
- `index_daily` — 指数行情
- `news` / `anns_d` — 新闻公告

## 注意事项

- 日期格式 `YYYYMMDD`
- 代码格式如 `600519.SH`
- 权限不足或积分不够时如实说明，不要编造数据
- 长区间数据应分段拉取并合并
