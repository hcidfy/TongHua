"""工具层：8 个数据工具的执行体（由 `ToolBlueprint` 派生；03_BUILD_PLAN §3 M2）。

一次数据工具调用 = 四件事（§3 组件表里 `tools/impl/*.py` 的内部任务）：

1. **参数映射**：`blueprint.args_mapping(业务参数)` → 扶摇查询参数（业务口径与扶摇口径解耦）；
2. **取数**：`client.get(path, params)` → `ApiData`（`MockClient` 与 `FuyaoClient` 同签名，
   由 `MOCK_DATA_MODE` 切换，见 `data/mock_client.py`）；
3. **响应裁剪**：只留 `keep_fields` 里**实际存在**的字段（D4：`null` 透传，不补零、不臆造）；
4. **Evidence 构造**：把 `source / as_of / unit / caliber / raw_ref / confidence` 一并带出，
   缺项由 `ToolExecutor` 第 ⑧ 步兜底补齐。

`as_of` 取响应 `data.timestamp`（毫秒级 Unix，D2）；响应无 `timestamp`（如 `news/topic/hot-list`）
时**保持 None**，交给执行器回落抓取时刻（约定见 `data/quality.py`）。
"""

from __future__ import annotations

import json
from typing import Any

from app.data import (
    IFIND_CONFIDENCE,
    MOCK_CONFIDENCE,
    SOURCE_FUYAO,
    SOURCE_IFIND,
    SOURCE_MOCK,
    NotEnabled,
    quality_fields,
)
from app.domain import now_ms
from app.harness.tool_executor import ToolResult

from ..blueprint import ToolBlueprint

__all__ = ["make_data_impl", "make_ifind_fallback", "source_of", "records_of", "trim_rows"]


def source_of(client: Any) -> str:
    """按客户端类型判定证据来源（`FuyaoClient.source` / `MockClient.source`）。"""
    return str(getattr(client, "source", SOURCE_FUYAO) or SOURCE_FUYAO)


def records_of(data: Any, list_key: str = "item") -> list[Any]:
    """从 `data` 中抽取记录列表（扶摇多数接口为 `item`，资讯类为 `items`）。"""
    if isinstance(data, list):
        return list(data)
    if isinstance(data, dict):
        rows = data.get(list_key)
        if isinstance(rows, list):
            return list(rows)
    return []


def trim_rows(rows: list[Any], keep_fields: tuple[str, ...] | None) -> list[Any]:
    """响应裁剪：`keep_fields=None` 原样透传；否则只留**实际存在**的字段。"""
    if keep_fields is None:
        return [dict(row) if isinstance(row, dict) else row for row in rows]
    allowed = set(keep_fields)
    return [
        {key: value for key, value in row.items() if key in allowed}
        if isinstance(row, dict)
        else row
        for row in rows
    ]


def _total_of(data: Any, rows: list[Any]) -> int:
    """总数取上游 `data.total`，缺失时回落为本次返回条数（分页信息见 `data.pagination`）。"""
    if isinstance(data, dict):
        total = data.get("total")
        if isinstance(total, (int, float)):
            return int(total)
    return len(rows)


def make_data_impl(bp: ToolBlueprint, client: Any) -> Any:
    """把蓝图编译成一个「扶摇 / Mock 取数」实现（供 `ToolRegistry.bind`）。"""

    def impl(args: dict[str, Any]) -> ToolResult:
        payload = dict(args or {})
        path = bp.path_for(payload)
        params = bp.args_mapping(payload)
        api = client.get(path, params)
        rows = records_of(api.data, bp.list_key)
        source = source_of(client)
        return ToolResult(
            tool_name=bp.name,
            data={"total": _total_of(api.data, rows), bp.list_key: trim_rows(rows, bp.keep_fields)},
            **quality_fields(
                source=source,
                interface=path,
                as_of=api.timestamp,
                unit=bp.unit,
                caliber=bp.caliber,
                raw_ref=api.raw_ref,
                confidence=MOCK_CONFIDENCE if source == SOURCE_MOCK else bp.confidence,
            ),
        )

    return impl


def make_ifind_fallback(bp: ToolBlueprint, ifind: Any) -> Any:
    """把蓝图编译成「iFinD 备源」实现（阶梯第 ⑥ 步）。

    §3.1 模板的 `fallback: ifind（不可用 / 无 schema 时直接降级）`：iFinD 返回**自然语言 / 表格
    文本、无字段级 schema**（DATASOURCES §3），因此在映射到具体 iFinD 工具名（`bp.ifind_tool`）
    之前一律抛 `NotEnabled` —— 由执行器第 ⑥→⑦ 步转成显式降级（不崩、不静默）。
    """

    def fallback(args: dict[str, Any]) -> ToolResult:
        if not bp.use_fallback or not bp.ifind_tool:
            raise NotEnabled(
                f"{bp.name} 未配置 iFinD 备源工具映射（§3.1：不可用 / 无 schema 时直接降级）"
            )
        # 备源收到的是**合法 JSON 文本**（而非 Python dict repr），便于 iFinD 侧解析意图；
        # `ensure_ascii=False` 保留中文，`sort_keys` 保证同一入参的查询串稳定可复现。
        query = json.dumps(bp.args_mapping(dict(args or {})), ensure_ascii=False, sort_keys=True)
        text = ifind.query(query, tool=bp.ifind_tool)
        return ToolResult(
            tool_name=bp.name,
            data={"text": text, "query": query},
            degrade_reason=(
                f"主源不可用，已切换 iFinD 备源（{bp.ifind_tool}）；"
                "备源返回自然语言文本，无字段级口径"
            ),
            **quality_fields(
                source=SOURCE_IFIND,
                interface=f"ifind://{bp.ifind_tool}",
                as_of=None,
                unit=bp.unit,
                caliber=f"{bp.caliber}（备源文本）",
                raw_ref=f"ifind://{bp.ifind_tool}#at={now_ms()}",
                confidence=IFIND_CONFIDENCE,
            ),
        )

    return fallback
