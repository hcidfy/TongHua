"""工具层：10 个工具的**声明**（9 个数据工具 + 1 个内部 COMMIT 工具；03_BUILD_PLAN §3 M2 / §10）。

- 9 个数据工具：扶摇主源（§3 工具表 + 后置 4 工具中的 `get_financials`），逐个走
  「业务参数 → 扶摇参数」映射 + 响应裁剪；
- 1 个内部工具 `emit_artifact`：`phase = COMMIT`，不触外网（C1/C2：产物三层分离）。

口径以 `docs/DATASOURCES.md` §2 为准。**已知文档冲突一处**：`get_price_history` 的扶摇路径
在 03_BUILD_PLAN §3 表里写作 `/api/a-share/prices/history`，DATASOURCES §2.3 实测为
`/api/a-share/prices/historical` —— 此处以**实测为准**（`historical`）。

**`get_financials`（财报）原属「后置 4 工具」，此处提前落地**：它是「收入架构变化」类研究
目标的核心数据源，缺失会导致 Planner 只能落到行情/估值/热榜（见 HANDOFF §21）。当前只接
利润表 `income-statements`，`period` / `limit` 与 DATASOURCES §2.5 一致。
"""

from __future__ import annotations

from typing import Any

from app.data.errors import FuyaoError
from app.domain import Phase

from ..blueprint import ToolBlueprint, join_thscodes, optional_int, single_thscode

__all__ = [
    "DATA_BLUEPRINTS",
    "INTERNAL_BLUEPRINTS",
    "DATA_TOOL_NAMES",
    "INTERNAL_TOOL_NAME",
]

# --------------------------------------------------------------------------- #
# ① search_ticker（meta，前置）
# --------------------------------------------------------------------------- #
_SEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "q": {"type": "string", "description": "检索关键词：证券代码或名称"},
        "exchange": {"type": "string", "description": "市场过滤，如 SH / SZ / BJ"},
        "asset_type": {"type": "string", "description": "资产类型过滤，如 stock / index"},
        "limit": {"type": "number", "description": "返回条数 1..50，默认 10"},
    },
    "required": ["q"],
}


def _map_search(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("q") or "").strip()
    if not query:
        raise FuyaoError("search_ticker 缺少必填参数 q（DATASOURCES §2.1）")
    params: dict[str, Any] = {"q": query}
    for key in ("exchange", "asset_type"):
        value = args.get(key)
        if value:
            params[key] = value
    limit = optional_int(args.get("limit"), field="limit", minimum=1, maximum=50)
    if limit is not None:
        params["limit"] = limit
    return params


# --------------------------------------------------------------------------- #
# ② get_price_snapshot（price；个股 / 指数两个端点由参数派生）
# --------------------------------------------------------------------------- #
_SNAPSHOT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "thscodes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "完整 thscode 列表，如 ['600519.SH']（D1：不接受纯 ticker）",
        },
        "index": {"type": "boolean", "description": "true 走指数快照端点，默认 false（个股）"},
        "limit": {"type": "number", "description": "返回条数上限"},
        "offset": {"type": "number", "description": "分页偏移，从 0 开始"},
    },
    "required": ["thscodes"],
}


def _snapshot_path(args: dict[str, Any]) -> str:
    if args.get("index"):
        return "/api/a-share-index/prices/snapshot"
    return "/api/a-share/prices/snapshot"


def _map_snapshot(args: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {"thscodes": join_thscodes(args.get("thscodes"))}
    limit = optional_int(args.get("limit"), field="limit", minimum=1)
    if limit is not None:
        params["limit"] = limit
    offset = optional_int(args.get("offset"), field="offset", minimum=0)
    if offset is not None:
        params["offset"] = offset
    return params


# --------------------------------------------------------------------------- #
# ③ get_price_history（price；单标的 + 毫秒区间 + 复权口径）
# --------------------------------------------------------------------------- #
_HISTORY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "thscode": {"type": "string", "description": "单个完整 thscode（不接受逗号，D1）"},
        "start_ms": {"type": "number", "description": "起始毫秒级 Unix 时间戳（D2）"},
        "end_ms": {"type": "number", "description": "结束毫秒级 Unix 时间戳（D2）"},
        "interval": {"type": "string", "description": "周期，目前仅支持 1d"},
        "adjust": {"type": "string", "description": "复权口径：none / forward（默认）/ backward"},
    },
    "required": ["thscode", "start_ms", "end_ms"],
}

_ADJUSTS = {"none", "forward", "backward"}


def _map_history(args: dict[str, Any]) -> dict[str, Any]:
    thscode = single_thscode(args.get("thscode"))
    start = args.get("start_ms")
    end = args.get("end_ms")
    if start is None or end is None:
        raise FuyaoError("get_price_history 需 start_ms / end_ms（毫秒级 Unix，D2）")
    interval = str(args.get("interval") or "1d").strip().lower()
    if interval != "1d":
        raise FuyaoError("interval 目前仅支持 1d（DATASOURCES §2.3）")
    adjust = str(args.get("adjust") or "forward").strip().lower()
    if adjust not in _ADJUSTS:
        raise FuyaoError(f"adjust 仅支持 {sorted(_ADJUSTS)}")
    return {
        "thscode": thscode,
        "interval": interval,
        "start": int(start),
        "end": int(end),
        "adjust": adjust,
    }


# --------------------------------------------------------------------------- #
# ④ get_index_constituents（market）
# --------------------------------------------------------------------------- #
_CONSTITUENTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "thscode": {"type": "string", "description": "指数完整 thscode，如 '881121.TI'（半导体）"},
    },
    "required": ["thscode"],
}


def _map_constituents(args: dict[str, Any]) -> dict[str, Any]:
    return {"thscode": single_thscode(args.get("thscode"))}


# --------------------------------------------------------------------------- #
# ⑤ get_valuation（fundamental）
# --------------------------------------------------------------------------- #
_VALUATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "thscodes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "完整 thscode 列表，单次最多 100 个",
        },
        "limit": {"type": "number", "description": "返回条数上限"},
    },
    "required": ["thscodes"],
}


def _map_valuation(args: dict[str, Any]) -> dict[str, Any]:
    codes = join_thscodes(args.get("thscodes"))
    if len(codes.split(",")) > 100:
        raise FuyaoError("估值快照单次最多 100 个 thscode（DATASOURCES §2.3）")
    params: dict[str, Any] = {"thscodes": codes}
    limit = optional_int(args.get("limit"), field="limit", minimum=1)
    if limit is not None:
        params["limit"] = limit
    return params


# --------------------------------------------------------------------------- #
# ⑥ get_limit_up_stats（heat；涨 / 跌 / 炸板 三池）
# --------------------------------------------------------------------------- #
_LIMIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "description": "池别：up（涨停，默认）/ down（跌停）/ break（炸板）"},
        "date_ms": {"type": "number", "description": "交易日毫秒级时间戳，缺省为最新交易日"},
        "page": {"type": "number", "description": "页码，从 1 开始"},
        "size": {"type": "number", "description": "每页条数 1..200，默认 50"},
        "sort_field": {"type": "string", "description": "排序字段（各池默认值不同）"},
        "sort_dir": {"type": "string", "description": "排序方向 asc / desc"},
    },
    "required": [],
}

_LIMIT_POOLS = {
    "up": "/api/a-share/special-data/limit-up-pool",
    "down": "/api/a-share/special-data/limit-down-pool",
    "break": "/api/a-share/special-data/limit-break-pool",
}


def _limit_kind(args: dict[str, Any]) -> str:
    kind = str(args.get("kind") or "up").strip().lower()
    if kind not in _LIMIT_POOLS:
        raise FuyaoError(f"kind 仅支持 {sorted(_LIMIT_POOLS)}")
    return kind


def _limit_path(args: dict[str, Any]) -> str:
    return _LIMIT_POOLS[_limit_kind(args)]


def _map_limit(args: dict[str, Any]) -> dict[str, Any]:
    _limit_kind(args)  # 提前校验，避免打无谓的请求
    params: dict[str, Any] = {}
    for key, bounds in (("date_ms", {}), ("page", {"minimum": 1}), ("size", {"minimum": 1, "maximum": 200})):
        value = optional_int(args.get(key), field=key, **bounds)
        if value is not None:
            params[key] = value
    for key in ("sort_field", "sort_dir"):
        value = args.get(key)
        if value:
            params[key] = value
    return params


# --------------------------------------------------------------------------- #
# ⑦ get_hot_rank（heat）
# --------------------------------------------------------------------------- #
_HOT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "period": {"type": "string", "description": "周期：day（默认）/ hour"},
        "limit": {"type": "number", "description": "返回条数 1..100，默认 30"},
    },
    "required": [],
}

_PERIODS = {"day", "hour"}


def _map_hot(args: dict[str, Any]) -> dict[str, Any]:
    period = str(args.get("period") or "day").strip().lower()
    if period not in _PERIODS:
        raise FuyaoError(f"period 仅支持 {sorted(_PERIODS)}")
    limit = optional_int(args.get("limit"), field="limit", minimum=1, maximum=100)
    return {"period": period, "limit": 30 if limit is None else limit}


# --------------------------------------------------------------------------- #
# ⑧ get_news_flash（event）
#
# 端点改判（2026-10-19 真实 Key 实测）：原 `/api/news/flash/list` 的 `seq` + `tag_id`
# **均为必填**，且给任意合法取值恒返 0 条（需真实游标才可能有数据）→ M2 无法独立可用；
# `/api/news/flash/refresh` 另强制未文档化的 `create_time_ms`，不接入。
# 改为指向 `/api/news/topic/hot-list`（资讯热榜 / 深度专题）：`tab_id` **必填**，
# 实测 `tab_id=1` 返回 12 条真实条目（`items` / `next_cursor` / `has_more`）。
# 详见 DATASOURCES.md §2.11 与 HANDOFF.md §12.3 第 2 条。
# --------------------------------------------------------------------------- #
_NEWS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tab_id": {"type": "number", "description": "资讯热榜 tab，必填；实测 tab_id=1 有数据，默认 1"},
    },
    "required": [],
}


def _map_news(args: dict[str, Any]) -> dict[str, Any]:
    tab_id = optional_int(args.get("tab_id"), field="tab_id", minimum=1)
    return {"tab_id": 1 if tab_id is None else tab_id}


# --------------------------------------------------------------------------- #
# ⑨ get_financials（fundamental；利润表 income-statements，DATASOURCES §2.5）
#
# 提前落地的后置工具：题目「近三年公司收入架构变化」必须靠它才能取证。
# 三个报表接口入参契约一致，本期只接利润表；`balance-sheets` / `cash-flow-statements`
# 按需再加（同一模式，只换 path 与 keep_fields）。
# --------------------------------------------------------------------------- #
_FINANCIALS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "thscode": {"type": "string", "description": "单个完整 thscode（不接受逗号，D1）"},
        "period": {
            "type": "string",
            "description": "报告期：annual（仅年报，默认）/ quarterly（每个季度末）",
        },
        "limit": {"type": "number", "description": "最近 N 期，默认 4，范围 [1,20]"},
    },
    "required": ["thscode"],
}

_FINANCIAL_PERIODS = {"annual", "quarterly"}


def _map_financials(args: dict[str, Any]) -> dict[str, Any]:
    thscode = single_thscode(args.get("thscode"))
    period = str(args.get("period") or "annual").strip().lower()
    if period not in _FINANCIAL_PERIODS:
        raise FuyaoError(f"period 仅支持 {sorted(_FINANCIAL_PERIODS)}（DATASOURCES §2.5）")
    limit = optional_int(args.get("limit"), field="limit", minimum=1, maximum=20)
    return {"thscode": thscode, "period": period, "limit": 4 if limit is None else limit}


# --------------------------------------------------------------------------- #
# ⑩ emit_artifact（内部 COMMIT 工具；唯一 phase=COMMIT 的注册项）
# --------------------------------------------------------------------------- #
_ARTIFACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "facts": {"type": "array", "description": "事实层：可由证据直接支持（C2）"},
        "inferences": {"type": "array", "description": "推断层：由事实推出的判断（C2）"},
        "uncertainties": {"type": "array", "description": "不确定层：缺口与假设（C2）"},
        "citations": {"type": "array", "description": "引用层：证据 id / 来源引用"},
    },
    "required": [],
}


def _map_identity(args: dict[str, Any]) -> dict[str, Any]:
    return dict(args or {})


# --------------------------------------------------------------------------- #
# 声明表
# --------------------------------------------------------------------------- #
DATA_BLUEPRINTS: tuple[ToolBlueprint, ...] = (
    ToolBlueprint(
        name="search_ticker",
        description="按关键词检索证券标的（代码 / 名称），返回完整 thscode、市场与币种。",
        domain="meta",
        path="/api/meta/tickers/search",
        args_mapping=_map_search,
        schema_json=_SEARCH_SCHEMA,
        keep_fields=(
            "thscode",
            "ticker",
            "name",
            "exchange",
            "asset_type",
            "currency",
            "list_date",
            "last_trade_date",
        ),
        unit=None,
        caliber="标的检索结果（代码/名称/市场/币种）",
    ),
    ToolBlueprint(
        name="get_price_snapshot",
        description="获取 A 股个股或指数的实时行情快照（最新价、涨跌幅、成交量额）。",
        domain="price",
        path=_snapshot_path,
        args_mapping=_map_snapshot,
        schema_json=_SNAPSHOT_SCHEMA,
        keep_fields=(
            "thscode",
            "ticker",
            "last_price",
            "price_change",
            "price_change_ratio_pct",
            "open_price",
            "high_price",
            "low_price",
            "prev_price",
            "volume",
            "turnover",
        ),
        unit="CNY",
        caliber="快照价",
    ),
    ToolBlueprint(
        name="get_price_history",
        description="获取单个标的的日线 K 线（含复权口径），入参为毫秒级起止时间戳。",
        domain="price",
        path="/api/a-share/prices/historical",
        args_mapping=_map_history,
        schema_json=_HISTORY_SCHEMA,
        keep_fields=(
            "date_ms",
            "open_price",
            "high_price",
            "low_price",
            "close_price",
            "volume",
            "turnover",
        ),
        unit="CNY",
        caliber="日线 K 线（前复权，adjust=forward）",
    ),
    ToolBlueprint(
        name="get_index_constituents",
        description="获取指数成分股名单（板块 → 个股的下钻入口）。",
        domain="market",
        path="/api/a-share-index/constituents/ths-stock-list",
        args_mapping=_map_constituents,
        schema_json=_CONSTITUENTS_SCHEMA,
        keep_fields=("thscode", "ticker", "name"),
        unit=None,
        caliber="指数成分股名单",
    ),
    ToolBlueprint(
        name="get_valuation",
        description="获取标的估值快照（PE/PB/PS/PCF 等倍数，TTM 与 MRQ 口径）。",
        domain="fundamental",
        path="/api/a-share/valuations/snapshot",
        args_mapping=_map_valuation,
        schema_json=_VALUATION_SCHEMA,
        keep_fields=("thscode", "ticker", "name", "pe_ttm", "pe_mrq", "pb_mrq", "ps_ttm", "pcf_ttm"),
        unit="倍",
        caliber="估值倍数（pe_ttm/pe_mrq/pb_mrq/ps_ttm/pcf_ttm）",
    ),
    ToolBlueprint(
        name="get_limit_up_stats",
        description="获取涨停 / 跌停 / 炸板池数据（含封单额、连板数、首次封板时间等）。",
        domain="heat",
        path=_limit_path,
        args_mapping=_map_limit,
        schema_json=_LIMIT_SCHEMA,
        keep_fields=(
            "thscode",
            "ticker",
            "name",
            "last_price",
            "price_change_ratio_pct",
            "limit_up_time",
            "first_limit_time",
            "last_limit_time",
            "limit_up_reason",
            "continue_day_text",
            "continue_day_cnt",
            "seal_money",
            "max_seal_money",
            "open_times",
            "turnover",
            "turnover_ratio_pct",
            "is_st",
            "is_new",
        ),
        unit=None,
        caliber="涨跌停池记录（封单额单位：元；换手率与涨跌幅为百分数原值）",
    ),
    ToolBlueprint(
        name="get_hot_rank",
        description="获取个股热度榜（日榜 / 小时榜），含排名、热度值与排名变化。",
        domain="heat",
        path="/api/a-share/special-data/hot-stock-list",
        args_mapping=_map_hot,
        schema_json=_HOT_SCHEMA,
        keep_fields=("thscode", "ticker", "name", "rank", "heat", "rank_change", "rank_trend"),
        unit=None,
        caliber="热度排名（day/hour 周期）",
    ),
    ToolBlueprint(
        name="get_news_flash",
        description="获取资讯热榜 / 深度专题（事件面），按 tab 分类返回条目。",
        domain="event",
        path="/api/news/topic/hot-list",
        args_mapping=_map_news,
        schema_json=_NEWS_SCHEMA,
        list_key="items",
        keep_fields=(
            "id",
            "title",
            "cover",
            "url",
            "type",
            "seo_key",
            "propagation_chain",
            "important_flag",
            "pinned_flag",
            "pin_label",
            "source_created_at",
            "source_updated_at",
        ),
        unit=None,
        caliber="资讯热榜 / 深度专题（topic）",
    ),
    ToolBlueprint(
        name="get_financials",
        description="获取标的利润表（营业收入 / 营业成本 / 各项费用 / 净利润 / 每股收益，含同比期数）。",
        domain="fundamental",
        path="/api/a-share/financials/income-statements",
        args_mapping=_map_financials,
        schema_json=_FINANCIALS_SCHEMA,
        keep_fields=(
            "thscode",
            "ticker",
            "period",
            "fiscal_year",
            "fiscal_period",
            "report_date_ms",
            "period_end_ms",
            "currency",
            "operating_income",
            "operating_costs",
            "operating_expenses",
            "sales_fee",
            "manage_fee",
            "research_and_development_expenses",
            "operating_profit",
            "interest_expenses",
            "profit_total",
            "income_tax_expense",
            "net_profit",
            "parent_holder_net_profit",
            "basic_eps",
        ),
        unit="CNY",
        caliber="利润表（合并报表；金额单位为原币元，basic_eps 为元/股）",
    ),
)

INTERNAL_BLUEPRINTS: tuple[ToolBlueprint, ...] = (
    ToolBlueprint(
        name="emit_artifact",
        description="提交结构化研究结论：事实 / 推断 / 不确定三层分离（C1：不含任何操作建议）。",
        domain="output",
        path="internal://emit_artifact",
        args_mapping=_map_identity,
        schema_json=_ARTIFACT_SCHEMA,
        keep_fields=None,
        unit=None,
        caliber="平台内部产物（事实/推断/不确定三层分离，C2）",
        phase=Phase.COMMIT,
        confidence=1.0,
        idempotent=False,
        use_fallback=False,
    ),
)

#: 9 个数据工具名（§10 契约数字：数据工具 = 8 → **9**，用户确认放开并纳入 `get_financials`）。
DATA_TOOL_NAMES: tuple[str, ...] = tuple(bp.name for bp in DATA_BLUEPRINTS)

#: 1 个内部 COMMIT 工具名。
INTERNAL_TOOL_NAME: str = INTERNAL_BLUEPRINTS[0].name
