"""可公开分发的虚构演示样本。

保留接口信封与字段形状，但所有数值、热度、资讯和成分关系都是人工构造；
证券代码仅用于演示参数过滤，绝不表示这些公司的真实财务或市场数据。
"""

from __future__ import annotations

from typing import Any

__all__ = ["public_payload"]

_AS_OF = 1792368000000


def _envelope(path: str, rows: list[dict[str, Any]], *, list_key: str = "item",
              timestamp: bool = True, total: bool = True) -> dict[str, Any]:
    data: dict[str, Any] = {list_key: rows}
    if timestamp:
        data["timestamp"] = _AS_OF
    if total:
        data["total"] = len(rows)
    return {
        "code": 0,
        "message": "success",
        "data": data,
        "request_id": f"public-demo-{path.rsplit('/', 1)[-1]}",
    }


def _demo_stocks() -> list[dict[str, Any]]:
    return [
        {"thscode": f"99{i:04d}.SH", "ticker": f"99{i:04d}",
         "name": f"虚构示例公司{i}", "last_price": float(10 + i)}
        for i in range(1, 11)
    ]


def public_payload(path: str) -> dict[str, Any] | None:
    """根据规范化 API 路径生成单次虚构响应；未知路径返回 None。"""
    if path == "/api/meta/tickers/search":
        return _envelope(path, [
            {"thscode": "600519.SH", "ticker": "600519", "name": "贵州茅台",
             "exchange": "SH", "asset_type": "a-share", "currency": "CNY",
             "end_date": None},
            {"thscode": "002371.SZ", "ticker": "002371", "name": "北方华创",
             "exchange": "SZ", "asset_type": "a-share", "currency": "CNY",
             "end_date": None},
            {"thscode": "881121.TI", "ticker": "881121", "name": "半导体",
             "exchange": None, "asset_type": "a-share-index", "currency": "CNY",
             "end_date": None},
        ])
    if path == "/api/a-share/prices/snapshot":
        return _envelope(path, [
            {"thscode": code, "ticker": code.split(".")[0], "last_price": price,
             "price_change": change, "price_change_ratio_pct": ratio,
             "open_price": price - 2, "high_price": price + 3,
             "low_price": price - 4, "prev_price": price - change,
             "volume": 1234500, "turnover": 123450000.0}
            for code, price, change, ratio in (
                ("600519.SH", 1200.0, 3.0, 0.25),
                ("002371.SZ", 400.0, -4.0, -0.99),
            )
        ])
    if path == "/api/a-share-index/prices/snapshot":
        return _envelope(path, [
            {"thscode": "881121.TI", "ticker": "881121", "last_price": 3210.0,
             "price_change": 10.0, "price_change_ratio_pct": 0.31,
             "volume": 1000000, "turnover": 50000000.0}
        ])
    if path == "/api/a-share/prices/historical":
        return _envelope(path, [
            {"date_ms": _AS_OF - i * 86400000, "open_price": float(100 + i),
             "high_price": float(104 + i), "low_price": float(98 + i),
             "close_price": float(102 + i), "volume": 100000 + i * 1000,
             "turnover": float(1000000 + i * 10000)}
            for i in range(3)
        ])
    if path == "/api/a-share/valuations/snapshot":
        return _envelope(path, [
            {"thscode": "600519.SH", "ticker": "600519", "name": "贵州茅台",
             "pe_ttm": 18.0, "pe_mrq": 18.5, "pb_mrq": 5.0,
             "ps_ttm": 7.0, "pcf_ttm": 17.0},
            {"thscode": "002371.SZ", "ticker": "002371", "name": "北方华创",
             "pe_ttm": 31.0, "pe_mrq": 32.0, "pb_mrq": 6.0,
             "ps_ttm": 8.0, "pcf_ttm": 29.0},
        ])
    if path == "/api/a-share/financials/income-statements":
        rows = []
        for code, base in (("600519.SH", 100_000_000_000), ("002371.SZ", 20_000_000_000)):
            for year, factor in ((2024, 1.0), (2025, 1.1)):
                income = base * factor
                rows.append({
                    "thscode": code, "ticker": code.split(".")[0],
                    "period": "annual", "fiscal_year": year, "fiscal_period": "FY",
                    "report_date_ms": _AS_OF, "period_end_ms": _AS_OF,
                    "currency": "CNY", "operating_income": income,
                    "operating_costs": income * 0.35, "sales_fee": income * 0.05,
                    "manage_fee": income * 0.04, "net_profit": income * 0.25,
                    "basic_eps": 5.0 if code == "600519.SH" else 2.0,
                })
        return _envelope(path, rows)
    if path == "/api/a-share-index/constituents/ths-stock-list":
        return _envelope(path, [
            {"thscode": "002371.SZ", "ticker": "002371", "name": "北方华创"},
            {"thscode": "990001.SH", "ticker": "990001", "name": "虚构示例公司1"},
        ])
    if path in {
        "/api/a-share/special-data/limit-up-pool",
        "/api/a-share/special-data/limit-down-pool",
        "/api/a-share/special-data/limit-break-pool",
    }:
        return _envelope(path, [
            {**stock, "price_change_ratio_pct": 9.9, "limit_up_time": "09:45:00",
             "limit_up_reason": "虚构事件", "continue_day_cnt": 1,
             "seal_money": 1000000.0} for stock in _demo_stocks()
        ])
    if path == "/api/a-share/special-data/hot-stock-list":
        return _envelope(path, [
            {**stock, "rank": rank, "heat": str(1000 - rank * 10),
             "rank_change": 0, "rank_trend": "flat"}
            for rank, stock in enumerate(_demo_stocks(), start=1)
        ], total=False)
    if path == "/api/a-share/calendar/trading-days":
        return _envelope(path, [
            {"date_ms": _AS_OF, "date": "20261019"},
            {"date_ms": _AS_OF + 86400000, "date": "20261020"},
        ])
    if path == "/api/news/flash/list":
        return _envelope(path, [], list_key="items", timestamp=False)
    if path == "/api/news/topic/hot-list":
        return _envelope(path, [
            {"id": "demo-topic-1", "title": "虚构示例：公司披露模拟产品结构",
             "url": None, "type": "DEEP_TOPIC", "source_created_at": None},
            {"id": "demo-topic-2", "title": "虚构示例：行业关注度变化",
             "url": None, "type": "DEEP_TOPIC", "source_created_at": None},
        ], list_key="items", timestamp=False)
    return None
