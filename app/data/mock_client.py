"""数据层：离线 Mock 客户端（断网可演示 / 失败降级，题目硬要求）。

与 `FuyaoClient` **完全同签名**（`get` / `raw`），由 `MOCK_DATA_MODE` 切换，可无缝替换。
默认使用可公开分发的虚构数据（`public_mock.py`），保持响应信封与字段形状；
本地原始接口快照不会随仓库发布。显式传入 `mock_dir` 时才读取指定目录。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.domain.models import now_ms

from .errors import MockDataMissing
from .fuyao_client import FuyaoClient, RawResponse
from .public_mock import public_payload
from .quality import SOURCE_MOCK

__all__ = ["MockClient", "build_client", "MOCK_DIR", "MOCK_PATH_MAP"]

#: Mock 数据集目录（相对本文件 → `app/data/mock`）。
MOCK_DIR = Path(__file__).resolve().parent / "mock"

#: 扶摇接口路径 → Mock 文件名（未登记路径 = 无样本）。
MOCK_PATH_MAP: dict[str, str] = {
    "/api/meta/tickers/search": "meta_tickers_search.json",
    "/api/a-share/prices/snapshot": "a_share_prices_snapshot.json",
    # 日线 K 线：真实路径是 `historical`（DATASOURCES §2.3；03_BUILD_PLAN §3 表里的 `history` 为笔误）
    "/api/a-share/prices/historical": "a_share_prices_historical.json",
    "/api/a-share-index/prices/snapshot": "a_share_index_prices_snapshot.json",
    "/api/a-share/valuations/snapshot": "a_share_valuations_snapshot.json",
    "/api/a-share/financials/income-statements": "a_share_financials_income_statements.json",
    "/api/a-share-index/constituents/ths-stock-list": (
        "a_share_index_constituents_ths_stock_list.json"
    ),
    "/api/a-share/special-data/limit-up-pool": "a_share_limit_up_pool.json",
    "/api/a-share/special-data/limit-down-pool": "a_share_limit_down_pool.json",
    "/api/a-share/special-data/limit-break-pool": "a_share_limit_break_pool.json",
    "/api/a-share/special-data/hot-stock-list": "a_share_hot_stock_list.json",
    "/api/a-share/calendar/trading-days": "a_share_trading_days.json",
    "/api/news/flash/list": "news_flash_list.json",
    "/api/news/topic/hot-list": "news_topic_hot_list.json",
}

#: 不做逐条 `thscode` 过滤的路径（`thscode` 是父标的 / 条目内无该字段，见 `_filter_by_thscode`）。
_NO_THSCODE_FILTER_PATHS: frozenset[str] = frozenset(
    {
        "/api/a-share/prices/historical",
        "/api/a-share-index/constituents/ths-stock-list",
    }
)


def _normalize_path(path: str) -> str:
    """去掉 base URL 与查询串，保留以 `/` 开头的路径。"""
    cleaned = path.split("?", 1)[0]
    if cleaned.startswith("http"):
        cleaned = "/" + cleaned.split("//", 1)[-1].split("/", 1)[-1]
    return "/" + cleaned.strip("/")


def _filter_by_thscode(payload: dict[str, Any], params: dict[str, Any], path: str) -> dict[str, Any]:
    """按 `thscode` / `thscodes` 过滤 `data.item`，贴近真实接口的按标的返回语义。

    例外：**条目不是请求标的本身的接口**不做逐条过滤，否则会把样本整段清空——

    - 日线 K 线 `prices/historical`：标的由请求参数决定，而 `PriceBarItem` 无 `thscode` 字段；
    - 指数成分股 `constituents/ths-stock-list`：`thscode` 是**父标的（指数）**，条目是**子标的**。
    """
    if _normalize_path(path) in _NO_THSCODE_FILTER_PATHS:
        return payload
    wanted = params.get("thscode") or params.get("thscodes")
    if not wanted:
        return payload
    codes = {c.strip() for c in str(wanted).split(",") if c.strip()}
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("item"), list):
        return payload
    if not any(isinstance(it, dict) and "thscode" in it for it in data["item"]):
        return payload
    items = [it for it in data["item"] if isinstance(it, dict) and it.get("thscode") in codes]
    filtered = dict(payload)
    filtered_data = dict(data)
    filtered_data["item"] = items
    filtered_data["total"] = len(items)
    filtered["data"] = filtered_data
    return filtered


def _filter_search(payload: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """离线检索也必须尊重 q；未知公司不能命中固定样本里的茅台。"""
    query = str(params.get("q") or "").strip().lower()
    data = payload.get("data")
    if not query or not isinstance(data, dict) or not isinstance(data.get("item"), list):
        return payload
    matches = [
        row for row in data["item"]
        if isinstance(row, dict) and any(
            str(row.get(key) or "").lower() in query or query in str(row.get(key) or "").lower()
            for key in ("thscode", "ticker", "name") if row.get(key)
        )
    ]
    filtered = dict(payload)
    filtered["data"] = {**data, "item": matches[:int(params.get("limit") or 50)],
                        "total": len(matches)}
    return filtered


class MockClient(FuyaoClient):
    """离线客户端：复用父类的信封解析与错误映射，只替换 `raw()` 的数据来源。"""

    #: 来源标识与主源**显式区分**（C4：不得静默；`confidence` 亦随之降低）。
    source: str = SOURCE_MOCK

    def __init__(self, settings: Settings | None = None, *, mock_dir: Path | None = None) -> None:
        self.settings = settings or get_settings()
        self._mock_dir = Path(mock_dir) if mock_dir else MOCK_DIR
        self._public_only = mock_dir is None
        self._owns_client = False  # 无 HTTP 连接可关

    def close(self) -> None:  # pragma: no cover - 无资源
        return None

    def _resolve(self, path: str) -> Path:
        normalized = _normalize_path(path)
        filename = MOCK_PATH_MAP.get(normalized)
        if filename is None:
            raise MockDataMissing(
                f"Mock 数据集未覆盖该接口：{normalized}；可用接口见 MOCK_PATH_MAP",
                payload={"path": normalized, "available": sorted(MOCK_PATH_MAP)},
            )
        target = self._mock_dir / filename
        if not target.is_file():
            raise MockDataMissing(f"Mock 文件缺失：{target}")
        return target

    def raw(self, path: str, params: dict[str, Any] | None = None) -> RawResponse:
        """默认返回虚构样本；仅显式目录模式读取本地原始文件。"""
        normalized = _normalize_path(path)
        if self._public_only:
            payload = public_payload(normalized)
            if payload is None:
                raise MockDataMissing(f"Mock 数据集未覆盖该接口：{normalized}")
        else:
            target = self._resolve(path)
            payload = json.loads(target.read_text(encoding="utf-8"))
        if normalized == "/api/meta/tickers/search":
            payload = _filter_search(payload, params or {})
        payload = _filter_by_thscode(payload, params or {}, path)
        return RawResponse(
            url=f"mock://{normalized}",
            status_code=200,
            payload=payload,
            fetched_at=now_ms(),
        )

    # `get()` 直接继承：剥信封与 `code != 0` 抛错的行为与真实客户端逐字一致。


def build_client(settings: Settings | None = None) -> FuyaoClient:
    """按 `MOCK_DATA_MODE`（或无密钥降级）返回 Mock 或真实客户端。"""
    settings = settings or get_settings()
    return MockClient(settings) if settings.is_mock else FuyaoClient(settings)
