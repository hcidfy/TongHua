"""M2：数据与工具（03_BUILD_PLAN §3 M2）。

验收原文（§3 M2）：
    8 个工具均可用真实数据调用；断网/限流时能降级且产出 `degradation_notice`；
    每条 Evidence 必含 `source / as_of / unit / caliber`。

"真实数据" 的离线等价物是 `MockClient`（与真实响应**同构**：字段名 / 单位 / `null` 透传 /
`code/message/request_id/data` 信封，HANDOFF §6.4）——因此本文件**不触网、不依赖 api-key.txt**。

覆盖：
1. 契约数字（8 数据 + 1 内部 COMMIT = 9）；
2. 8 个工具逐个取数 → 裁剪（D4）+ 质量元数据四件套（C3）；
3. 断网 / 限流 / 过期缓存三条降级路径 → `DegradedResult.notice` / `ToolResult.notice`；
4. iFinD 备源：未配置 → `NotEnabled` 降级；配置了 → 文本 + 低置信度（D5）；
5. `Pipeline` 缺省装配把 9 个工具接进主链路。
"""

from __future__ import annotations

import json
import time
from dataclasses import replace
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.data import (
    IFIND_CONFIDENCE,
    MOCK_CONFIDENCE,
    SOURCE_FUYAO,
    SOURCE_IFIND,
    SOURCE_INTERNAL,
    SOURCE_MOCK,
    NotEnabled,
    SourceDown,
    TTLCache,
)
from app.data.mock_client import MockClient
from app.domain import Phase
from app.harness import SessionManager, ToolRegistry
from app.harness.pipeline import Pipeline
from app.harness.tool_executor import DegradedResult, ToolExecutor, ToolInvocation, ToolResult
from app.store.repo import TaskRepository
from app.tools import DATA_BLUEPRINTS, DATA_TOOL_NAMES, INTERNAL_TOOL_NAME, build_tools
from app.tools.impl import make_ifind_fallback
from m1_support import DATA_TOOLS, offline_settings

#: 9 个工具各自的合法业务入参（业务口径，非扶摇口径）。
CALL_ARGS: dict[str, dict[str, Any]] = {
    "search_ticker": {"q": "半导体", "limit": 10},
    "get_price_snapshot": {"thscodes": ["600519.SH"]},
    "get_price_history": {"thscode": "600519.SH", "start_ms": 1791000000000, "end_ms": 1792393286000},
    "get_index_constituents": {"thscode": "881121.TI"},
    "get_valuation": {"thscodes": ["600519.SH"]},
    "get_limit_up_stats": {"kind": "up"},
    "get_hot_rank": {"period": "day"},
    "get_news_flash": {"tab_id": 1},
    "get_financials": {"thscode": "600519.SH", "period": "annual", "limit": 4},
}

_BY_NAME = {bp.name: bp for bp in DATA_BLUEPRINTS}


# --------------------------------------------------------------------------- #
# 桩：断网主源 / 未配置备源 / 可用备源
# --------------------------------------------------------------------------- #
class _OfflineClient:
    """主源「断网」：任何请求都抛 `SourceDown`（研究 §9.3 第 ⑤ 步失败）。"""

    source = SOURCE_FUYAO

    def __init__(self, *, down: bool = True) -> None:
        self.inner = MockClient(offline_settings())
        self.down = down

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if self.down:
            raise SourceDown(f"模拟断网：{path}")
        return self.inner.get(path, params or {})


class _NoIfind:
    """等价于「未配置 iFinD」：`call_tool()` 抛 `NotEnabled`（§0 表 L28）。"""

    def query(self, query: str, *, tool: str | None = None) -> str:
        raise NotEnabled("未配置 iFinD 备源（离线测试）")


class _FakeIfind:
    """可用备源：返回自然语言文本（无字段级 schema，DATASOURCES §3）。"""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def query(self, query: str, *, tool: str | None = None) -> str:
        self.seen.append(query)
        return "半导体板块今日主力净流入 12.3 亿元（iFinD 文本）"


class _DenyLimiter:
    """令牌桶永远耗尽（第 ③ 步断言，不依赖真实速率）。"""

    def acquire(self, _key: str) -> bool:
        return False


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #
def _run_and_task(session: Session) -> tuple[str, str]:
    sm = SessionManager(session)
    thread = sm.create_thread(title="M2 工具测试", goal="g")
    run = sm.create_run(thread_id=thread.id)
    task = TaskRepository(session).create(
        run_id=run.id, seq=1, title="t", intent="i", status="RUNNING"
    )
    return run.id, task.id


def _new_task(session: Session, run_id: str, seq: int) -> str:
    return TaskRepository(session).create(
        run_id=run_id, seq=seq, title=f"t{seq}", intent="i", status="RUNNING"
    ).id


def _mock_registry(session: Session | None = None) -> ToolRegistry:
    """装配 9 个真实工具（主源 = MockClient，备源 = 未配置的 iFinD）。"""
    return build_tools(
        ToolRegistry(session), client=MockClient(offline_settings()), ifind=_NoIfind()
    )


def _executor(session: Session, registry: ToolRegistry, **kwargs: Any) -> ToolExecutor:
    kwargs.setdefault("tracer", None)
    kwargs.setdefault("settings", offline_settings())
    return ToolExecutor(registry, session=session, **kwargs)


# --------------------------------------------------------------------------- #
# 1. 契约数字（§10：数据工具 9 / 内部工具 1 / 注册项 10）
# --------------------------------------------------------------------------- #
def test_m2_contract_numbers_match_build_plan() -> None:
    assert len(DATA_TOOL_NAMES) == 9
    # 与 M1 规划/白名单用的同一批工具名（顺序亦一致，便于逐条对照）
    assert DATA_TOOL_NAMES == tuple(name for name, _ in DATA_TOOLS)
    assert INTERNAL_TOOL_NAME == "emit_artifact"

    registry = _mock_registry()
    assert len(registry.names()) == 10
    assert registry.names(phase=Phase.COMMIT) == ["emit_artifact"]
    assert set(registry.names(phase=Phase.EXPLORE)) == set(DATA_TOOL_NAMES)

    artifact = registry.get("emit_artifact")
    assert artifact is not None
    assert artifact.idempotent is False  # 产物提交不幂等（走审批，M3）
    # 9 个数据工具均可重试 + 幂等，且全部挂了备源
    for name in DATA_TOOL_NAMES:
        assert registry.get_fallback(name) is not None
    assert registry.get_fallback("emit_artifact") is None


# --------------------------------------------------------------------------- #
# 2. 9 个工具逐个取数 → C3 四件套 + 裁剪
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("tool_name", DATA_TOOL_NAMES)
def test_m2_each_tool_returns_evidence_with_quality_metadata(
    session: Session, tool_name: str
) -> None:
    run_id, task_id = _run_and_task(session)
    executor = _executor(session, _mock_registry(session))
    bp = _BY_NAME[tool_name]

    result = executor.invoke(run_id, task_id, ToolInvocation(tool_name, CALL_ARGS[tool_name]))

    assert isinstance(result, ToolResult), result
    # C3 强制字段：source / as_of / unit / caliber 一个都不能少
    assert result.source == SOURCE_MOCK
    assert result.as_of is not None and result.staleness_ms is not None
    assert result.unit == bp.unit
    assert result.caliber == bp.caliber
    assert result.raw_ref  # 证据可反查
    # Mock 来源必须与主源区分（C4 不得静默）→ 置信度随之降低
    assert result.confidence == MOCK_CONFIDENCE
    assert result.interface == bp.path_for(CALL_ARGS[tool_name])

    rows = result.data[bp.list_key]
    assert isinstance(rows, list)
    if bp.keep_fields is not None:  # 响应裁剪：只留白名单里**实际存在**的字段（D4）
        allowed = set(bp.keep_fields)
        assert all(set(row) <= allowed for row in rows)

    # 9 个工具均应有非空样本（`get_news_flash` 改指 `topic/hot-list` 后样本 12 条；
    # 该响应无 `data.timestamp` → `as_of` 回落抓取时刻，见 DATASOURCES §2.11）。
    assert rows and result.data["total"] >= 1


def test_m2_trim_drops_fields_outside_keep_fields(session: Session) -> None:
    """`meta/tickers/search` 的 `end_date` / `last_delivery_date` 不在白名单 → 必须被裁掉。"""
    run_id, task_id = _run_and_task(session)
    executor = _executor(session, _mock_registry(session))
    result = executor.invoke(run_id, task_id, ToolInvocation("search_ticker", {"q": "半导体"}))

    assert isinstance(result, ToolResult)
    rows = result.data["item"]
    assert rows and all("thscode" in row and "name" in row for row in rows)
    assert all("end_date" not in row and "last_delivery_date" not in row for row in rows)


def test_m2_total_falls_back_to_returned_row_count(session: Session) -> None:
    """上游没有 `data.total` 时取本次条数；`data.pagination.total` 不臆造成 `total`（D4）。"""
    run_id, task_id = _run_and_task(session)
    executor = _executor(session, _mock_registry(session))

    hot = executor.invoke(run_id, task_id, ToolInvocation("get_hot_rank", {"period": "day"}))
    assert isinstance(hot, ToolResult)
    assert hot.data["total"] == len(hot.data["item"]) == 10

    task2 = _new_task(session, run_id, 2)
    up = executor.invoke(run_id, task2, ToolInvocation("get_limit_up_stats", {"kind": "up"}))
    assert isinstance(up, ToolResult)
    assert up.data["total"] == len(up.data["item"]) == 10


def test_m2_emit_artifact_is_internal_commit_tool(session: Session) -> None:
    run_id, task_id = _run_and_task(session)
    executor = _executor(session, _mock_registry(session))
    payload = {
        "facts": ["半导体板块成分股 190 只"],
        "inferences": ["资金面偏暖"],
        "uncertainties": ["缺当日北向数据"],
        "citations": ["ev-1"],
    }
    result = executor.invoke(run_id, task_id, ToolInvocation("emit_artifact", payload))

    assert isinstance(result, ToolResult)
    assert result.source == SOURCE_INTERNAL
    assert result.confidence == 1.0
    assert result.as_of is not None
    assert result.caliber  # 内部工具亦须带口径
    # 三层分离原样交回，不触外网（产物落库归 M5）
    assert result.data["facts"] == payload["facts"]
    assert result.data["uncertainties"] == payload["uncertainties"]


# --------------------------------------------------------------------------- #
# 3. 降级路径 → degradation_notice（§11.2）
# --------------------------------------------------------------------------- #
def test_m2_offline_degrades_and_emits_notice(session: Session) -> None:
    run_id, task_id = _run_and_task(session)
    registry = build_tools(ToolRegistry(session), client=_OfflineClient(), ifind=_NoIfind())
    executor = _executor(session, registry)

    result = executor.invoke(run_id, task_id, ToolInvocation("get_price_snapshot", CALL_ARGS["get_price_snapshot"]))

    assert isinstance(result, DegradedResult), result
    # 主源真因不被备源异常覆盖（C4：说得出"为什么"）
    assert "主源与备源均失败" in result.reason
    assert "模拟断网" in result.reason
    assert "未配置 iFinD 备源" in result.reason
    assert result.attempts == 4  # 3 次主源（首次 + 指数退避 ×2）+ 1 次备源

    notice = result.notice
    assert notice["component"] == "degradation_notice"
    assert notice["props_json"]["scope"] == "get_price_snapshot"
    assert notice["props_json"]["impact"] == "high"
    assert notice["props_json"]["reason"] == result.reason


def test_m2_rate_limited_degrades_at_step3(session: Session) -> None:
    run_id, task_id = _run_and_task(session)
    executor = _executor(session, _mock_registry(session), limiter=_DenyLimiter())

    result = executor.invoke(run_id, task_id, ToolInvocation("get_hot_rank", {"period": "day"}))

    assert isinstance(result, DegradedResult)
    assert "触发本地限流" in result.reason and result.attempts == 0
    assert result.notice["props_json"]["impact"] == "high"


def test_m2_d1_violation_keeps_primary_reason(session: Session) -> None:
    """D1：单标的/多标的参数须为完整 thscode；纯 ticker 由映射层拦下并保留真因。"""
    run_id, task_id = _run_and_task(session)
    executor = _executor(session, _mock_registry(session))

    result = executor.invoke(run_id, task_id, ToolInvocation("get_price_snapshot", {"thscodes": ["600519"]}))

    assert isinstance(result, DegradedResult), result
    assert "非法 thscode" in result.reason


def test_m2_stale_cache_fallback_is_labelled(session: Session) -> None:
    """架构 §10.3：主源挂了但本地有（过期）缓存 → 用缓存 + 显式标注，而不是空手而归。"""
    run_id, task_id = _run_and_task(session)
    client = _OfflineClient(down=False)
    registry = build_tools(ToolRegistry(session), client=client, ifind=_NoIfind())
    cache = TTLCache(ttl_s=0.05)
    executor = _executor(session, registry, cache=cache)

    first = executor.invoke(run_id, task_id, ToolInvocation("get_hot_rank", {"period": "day"}))
    assert isinstance(first, ToolResult) and first.cached is False

    client.down = True
    time.sleep(0.1)  # 让缓存条目过期
    # 换一个 task：否则第 ② 步幂等会先命中，走不到第 ⑦ 步
    second = executor.invoke(
        run_id, _new_task(session, run_id, 2), ToolInvocation("get_hot_rank", {"period": "day"})
    )

    assert isinstance(second, ToolResult), second
    assert second.ok is False and second.cached is True and second.reused is True
    assert "改用本地缓存" in second.degrade_reason
    assert second.data["item"] == first.data["item"]  # 数据仍是那份可用证据
    assert second.notice["props_json"]["impact"] == "medium"


def test_m2_fresh_cache_hit_short_circuits_primary(session: Session) -> None:
    run_id, task_id = _run_and_task(session)
    client = _OfflineClient(down=False)
    registry = build_tools(ToolRegistry(session), client=client, ifind=_NoIfind())
    executor = _executor(session, registry)

    first = executor.invoke(run_id, task_id, ToolInvocation("get_hot_rank", {"period": "day"}))
    assert isinstance(first, ToolResult) and first.cached is False

    second = executor.invoke(
        run_id, _new_task(session, run_id, 2), ToolInvocation("get_hot_rank", {"period": "day"})
    )

    assert isinstance(second, ToolResult)
    assert second.cached is True and second.reused is True  # 第 ④ 步命中，未再打主源
    assert second.attempts == 0
    assert second.notice is None  # 未降级 → 不产噪声


# --------------------------------------------------------------------------- #
# 4. iFinD 备源（§3.1 `fallback: ifind`）
# --------------------------------------------------------------------------- #
def test_m2_ifind_fallback_yields_text_evidence_with_low_confidence(session: Session) -> None:
    """备源可用：拿到自然语言文本 + 低置信度（无字段级 schema，DATASOURCES §3）。"""
    run_id, task_id = _run_and_task(session)
    bp = replace(_BY_NAME["get_hot_rank"], ifind_tool="ifind_hot_rank")
    fake = _FakeIfind()
    registry = build_tools(
        ToolRegistry(session), client=_OfflineClient(), ifind=fake, register=True
    )
    registry.bind(bp.name, make_ifind_fallback(bp, fake))  # 只测备源实现本身
    executor = _executor(session, registry)

    result = executor.invoke(run_id, task_id, ToolInvocation(bp.name, {"period": "day"}))

    assert isinstance(result, ToolResult)
    assert result.ok is False  # 有 degrade_reason → 不算"正常"
    assert result.source == SOURCE_IFIND
    assert result.confidence == IFIND_CONFIDENCE
    assert result.caliber.endswith("（备源文本）")
    assert "已切换 iFinD 备源" in result.degrade_reason
    # 备源收到的是合法 JSON（而非 Python dict repr）
    assert json.loads(result.data["query"]) == bp.args_mapping({"period": "day"})
    assert result.notice["props_json"]["impact"] == "medium"  # 仍可用 → medium
    assert result.raw_ref.startswith("ifind://")


# --------------------------------------------------------------------------- #
# 5. Pipeline 缺省装配（M2 工具进入主链路）
# --------------------------------------------------------------------------- #
def test_m2_pipeline_autowires_the_ten_tools(session: Session) -> None:
    pipeline = Pipeline(session=session, settings=offline_settings())

    assert len(pipeline.registry.names()) == 10
    assert pipeline.registry.names(phase=Phase.COMMIT) == ["emit_artifact"]
    # 执行器与主循环用的是同一个注册表（否则工具绑定是"摆设"）
    assert pipeline.tool_executor.registry is pipeline.registry
    assert pipeline.agent_loop is not None
