"""M1：工具执行器 **8 步降级阶梯**（架构 §9.3；03_BUILD_PLAN §3 M1）。

`test_m1_pipeline.py` 覆盖端到端主链路；阶梯的**逐步精确断言**在这里，尤其是验收原文的
「模拟 `429/4001` 时能退避重试并最终降级」。

阶梯顺序（严格）：
    ① 参数校验 ② 幂等检查 ③ 限流 ④ 缓存
    ⑤ 主源（重试×2 + 熔断计数） ⑥ 备源 ⑦ 降级 ⑧ 质量元数据注入
"""

from __future__ import annotations

import time

from sqlalchemy.orm import Session

from app.data.errors import NoPermission, RateLimited
from app.domain import Phase, ToolSpec, now_ms
from app.harness import SessionManager, ToolRegistry
from app.harness.tool_executor import (
    DegradedResult,
    ToolExecutor,
    ToolInvocation,
    ToolResult,
)
from app.observ import Tracer
from app.store.repo import EvidenceRepository, TaskRepository, ToolCallRepository
from m1_support import always_failing_impl, build_registry, flaky_impl, ok_impl


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #
def _run_and_task(session: Session) -> tuple[str, str]:
    """建 Thread + Run + 一个 RUNNING 的 Task（`tool_calls` 的两个外键都要求存在）。"""
    sm = SessionManager(session)
    thread = sm.create_thread(title="阶梯测试", goal="g")
    run = sm.create_run(thread_id=thread.id)
    task = TaskRepository(session).create(
        run_id=run.id, seq=1, title="t", intent="i", status="RUNNING"
    )
    return run.id, task.id


def _new_task(session: Session, run_id: str, seq: int) -> str:
    return TaskRepository(session).create(
        run_id=run_id, seq=seq, title=f"t{seq}", intent="i", status="RUNNING"
    ).id


def _executor(session: Session, registry: ToolRegistry, **kwargs) -> ToolExecutor:
    kwargs.setdefault("tracer", None)
    return ToolExecutor(registry, session=session, **kwargs)


class _DenyLimiter:
    """令牌桶永远耗尽（用于第 ③ 步断言，避免依赖真实速率）。"""

    def acquire(self, _key: str) -> bool:
        return False


# --------------------------------------------------------------------------- #
# ① 参数校验
# --------------------------------------------------------------------------- #
def test_m1_step1_arg_validation_rejects_missing_and_wrong_type(session: Session) -> None:
    run_id, task_id = _run_and_task(session)
    registry = ToolRegistry(session)
    registry.register(
        ToolSpec(
            name="get_price_snapshot",
            description="行情快照",
            schema_json={
                "type": "object",
                "properties": {"thscode": {"type": "string"}},
                "required": ["thscode"],
            },
            domain="price",
            phase=Phase.EXPLORE,
        )
    )
    registry.bind("get_price_snapshot", ok_impl("get_price_snapshot"))
    executor = _executor(session, registry)

    missing = executor.invoke(run_id, task_id, ToolInvocation("get_price_snapshot", {}))
    assert isinstance(missing, DegradedResult)
    assert "参数校验失败" in missing.reason and missing.attempts == 0

    wrong_type = executor.invoke(
        run_id, task_id, ToolInvocation("get_price_snapshot", {"thscode": 600519})
    )
    assert isinstance(wrong_type, DegradedResult)
    assert "应为字符串" in wrong_type.reason


def test_m1_step1_unregistered_and_disabled_tool_degrade(session: Session) -> None:
    run_id, task_id = _run_and_task(session)
    registry = build_registry(session, names=("get_hot_rank",))
    executor = _executor(session, registry)

    unknown = executor.invoke(run_id, task_id, ToolInvocation("not_registered"))
    assert isinstance(unknown, DegradedResult) and "工具未注册" in unknown.reason

    spec = registry.get("get_hot_rank")
    assert spec is not None
    spec.enabled = False
    disabled = executor.invoke(run_id, task_id, ToolInvocation("get_hot_rank"))
    assert isinstance(disabled, DegradedResult) and "工具已停用" in disabled.reason


# --------------------------------------------------------------------------- #
# ② 幂等检查（`dedupe_key`）
# --------------------------------------------------------------------------- #
def test_m1_step2_dedupe_hit_reuses_evidence_backed_result(session: Session) -> None:
    run_id, task_id = _run_and_task(session)
    registry = build_registry(session, names=("get_price_snapshot",))
    executor = _executor(session, registry)
    args = {"thscode": "600519.SH"}

    first = executor.invoke(run_id, task_id, ToolInvocation("get_price_snapshot", args))
    assert isinstance(first, ToolResult)
    assert first.reused is False
    first_call_id = first.tool_call_id
    assert first_call_id is not None

    # 幂等复用依赖「证据留档」复原结果（M3 恢复的核心路径）
    EvidenceRepository(session).create(
        run_id=run_id,
        task_id=task_id,
        tool_call_id=first_call_id,
        source=first.source,
        interface=first.interface,
        as_of=now_ms(),
        unit=first.unit,
        caliber=first.caliber,
        raw_ref=first.raw_ref,
        confidence=first.confidence,
    )

    second = executor.invoke(run_id, task_id, ToolInvocation("get_price_snapshot", args))
    assert isinstance(second, ToolResult)
    assert second.reused is True and second.cached is True
    assert second.tool_call_id == first_call_id
    # 幂等命中不再新建调用行（`dedupe_key` UNIQUE 保证不会重复副作用）
    assert len(ToolCallRepository(session).list_by_task(task_id)) == 1


def test_m1_step2_dedupe_hit_without_evidence_degrades(session: Session) -> None:
    run_id, task_id = _run_and_task(session)
    registry = build_registry(session, names=("get_hot_rank",))
    executor = _executor(session, registry)

    executor.invoke(run_id, task_id, ToolInvocation("get_hot_rank"))
    again = executor.invoke(run_id, task_id, ToolInvocation("get_hot_rank"))
    assert isinstance(again, DegradedResult)
    assert "幂等命中但无证据留档" in again.reason and again.cached is True


# --------------------------------------------------------------------------- #
# ③ 限流 / ④ 缓存
# --------------------------------------------------------------------------- #
def test_m1_step3_local_rate_limit_degrades(session: Session) -> None:
    run_id, task_id = _run_and_task(session)
    registry = build_registry(session, names=("get_news_flash",))
    executor = _executor(session, registry, limiter=_DenyLimiter())

    result = executor.invoke(run_id, task_id, ToolInvocation("get_news_flash"))
    assert isinstance(result, DegradedResult)
    assert "触发本地限流" in result.reason and result.attempts == 0


def test_m1_step4_cache_hit_across_tasks(session: Session) -> None:
    run_id, task_id = _run_and_task(session)
    calls = {"n": 0}
    base_impl = ok_impl("get_valuation")

    def counting_impl(args):
        calls["n"] += 1
        return base_impl(args)

    registry = build_registry(session, names=("get_valuation",), impls={"get_valuation": counting_impl})
    executor = _executor(session, registry)
    args = {"thscode": "600519.SH"}

    first = executor.invoke(run_id, task_id, ToolInvocation("get_valuation", args))
    assert isinstance(first, ToolResult) and first.cached is False
    first_tool_call_id = first.tool_call_id

    # 换一个 Task 复用同一入参：幂等键不同（含 task_id），但缓存键只含「工具 + 入参」→ 命中
    other_task_id = _new_task(session, run_id, 2)
    second = executor.invoke(run_id, other_task_id, ToolInvocation("get_valuation", args))
    assert isinstance(second, ToolResult)
    assert second.cached is True and second.reused is True
    assert calls["n"] == 1, "缓存命中不应再打主源"
    assert second.tool_call_id != first_tool_call_id  # 本次调用有自己的审计行
    row = ToolCallRepository(session).get(second.tool_call_id)
    assert row is not None and row.status == "OK" and row.attempts == 0


# --------------------------------------------------------------------------- #
# ⑤ 主源：退避重试 / 熔断
# --------------------------------------------------------------------------- #
def test_m1_step5_rate_limited_retries_with_backoff_then_succeeds(session: Session) -> None:
    """验收原文：模拟 `429/4001`（`RateLimited`）退避重试。"""
    run_id, task_id = _run_and_task(session)
    impl = flaky_impl("get_price_snapshot", fail_times=2)
    registry = build_registry(session, names=("get_price_snapshot",), impls={"get_price_snapshot": impl})
    executor = _executor(session, registry, base_backoff_s=0.05)

    started = time.monotonic()
    result = executor.invoke(run_id, task_id, ToolInvocation("get_price_snapshot"))
    elapsed = time.monotonic() - started

    assert isinstance(result, ToolResult) and result.ok is True
    assert result.attempts == 3 and impl.calls["calls"] == 3
    # 指数退避：0.05 + 0.1 = 0.15s。阈值取 0.12 以容忍 Windows `time.sleep` 的
    # ~15.6ms 粒度抖动，同时仍能把「指数退避 0.15」与「固定退避 0.10」区分开。
    assert elapsed >= 0.12, f"未观察到指数退避（elapsed={elapsed:.3f}s）"


def test_m1_step5_rate_limited_exhausts_retries_then_degrades(session: Session) -> None:
    """验收原文：退避重试后**最终降级**（显式原因 + 审计留档）。"""
    run_id, task_id = _run_and_task(session)
    impl = always_failing_impl("get_news_flash")
    registry = build_registry(session, names=("get_news_flash",), impls={"get_news_flash": impl})
    executor = _executor(session, registry, base_backoff_s=0.05)

    result = executor.invoke(run_id, task_id, ToolInvocation("get_news_flash"))
    assert isinstance(result, DegradedResult)
    assert result.attempts == 3 and impl.calls["calls"] == 3
    assert result.error_code == RateLimited.code == 4001
    assert "主源与备源均失败" in result.reason

    row = ToolCallRepository(session).get(result.tool_call_id)
    assert row is not None
    assert (row.status, row.attempts, row.error_code) == ("FAILED", 3, 4001)


def test_m1_step5_non_retryable_error_does_not_retry(session: Session) -> None:
    """`2003 无权限` 属业务错误：不重试、不熔断，直接进入降级。"""
    run_id, task_id = _run_and_task(session)
    impl = always_failing_impl("get_limit_up_stats", error_factory=NoPermission)
    registry = build_registry(
        session, names=("get_limit_up_stats",), impls={"get_limit_up_stats": impl}
    )
    executor = _executor(session, registry)

    result = executor.invoke(run_id, task_id, ToolInvocation("get_limit_up_stats"))
    assert isinstance(result, DegradedResult)
    assert result.attempts == 1 and impl.calls["calls"] == 1
    assert result.error_code == NoPermission.code == 2003


# --------------------------------------------------------------------------- #
# ⑥ 备源 / ⑦ 降级 / ⑧ 质量元数据
# --------------------------------------------------------------------------- #
def test_m1_step6_switches_to_fallback_source(session: Session) -> None:
    run_id, task_id = _run_and_task(session)
    registry = build_registry(
        session,
        names=("get_valuation",),
        impls={"get_valuation": always_failing_impl("get_valuation")},
        fallback_impls={"get_valuation": ok_impl("get_valuation", source="ifind")},
    )
    executor = _executor(session, registry, base_backoff_s=0.0)

    result = executor.invoke(run_id, task_id, ToolInvocation("get_valuation"))
    assert isinstance(result, ToolResult)
    assert result.source == "ifind"
    assert result.degrade_reason == "主源不可用，已切换备源"
    assert result.attempts == 4  # 主源 3 次 + 备源 1 次
    row = ToolCallRepository(session).get(result.tool_call_id)
    assert row is not None and row.status == "DEGRADED"


def test_m1_step8_quality_metadata_injected_on_success(session: Session) -> None:
    run_id, task_id = _run_and_task(session)
    registry = build_registry(session, names=("get_price_snapshot",))
    executor = _executor(session, registry)

    result = executor.invoke(run_id, task_id, ToolInvocation("get_price_snapshot"))
    assert isinstance(result, ToolResult)
    assert result.as_of is not None
    assert result.staleness_ms is not None and result.staleness_ms >= 0
    assert result.confidence is not None
    assert result.raw_ref.startswith("tool://get_price_snapshot#call=")
    assert (result.unit, result.caliber) == ("CNY", "快照价")

    row = ToolCallRepository(session).get(result.tool_call_id)
    assert row is not None
    assert row.status == "OK" and row.attempts == 1 and row.latency_ms is not None


# --------------------------------------------------------------------------- #
# Trace 埋点（时序硬约束 1 / R-8）
# --------------------------------------------------------------------------- #
def test_m1_tool_span_records_ladder_attrs(session: Session) -> None:
    run_id, task_id = _run_and_task(session)
    registry = build_registry(
        session,
        names=("get_price_snapshot", "get_news_flash"),
        impls={"get_news_flash": always_failing_impl("get_news_flash")},
    )
    tracer = Tracer(session)
    tracer.bind_run(run_id)
    executor = ToolExecutor(registry, session=session, tracer=tracer, base_backoff_s=0.0)

    executor.invoke(run_id, task_id, ToolInvocation("get_price_snapshot"))
    executor.invoke(run_id, task_id, ToolInvocation("get_news_flash"))

    spans = {s.attrs.get("name"): s for s in tracer.spans if s.name == "tool"}
    assert set(spans) == {"get_price_snapshot", "get_news_flash"}
    for span in spans.values():
        # 架构 §12.1：tool Span 的 attrs = name / args_hash / status / attempts / latency / degrade
        assert set(span.attrs) >= {"name", "args_hash", "status", "attempts", "latency", "degrade"}

    good = spans["get_price_snapshot"]
    assert good.attrs["status"] == "OK" and good.attrs["attempts"] == 1
    bad = spans["get_news_flash"]
    assert bad.attrs["status"] == "DEGRADED" and bad.attrs["attempts"] == 3
    assert "主源与备源均失败" in str(bad.attrs["degrade"])
