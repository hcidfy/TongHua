"""M1 验收（03_BUILD_PLAN §3 M1）——端到端管线。

验收原文：
    给定目标「半导体板块最近资金情况」，自动规划 ≥3 个 Task 并执行完成、产出 ≥5 条 Evidence；
    Trace 中每个 Task 有完整 Loop 记录；模拟 `429/4001` 时能退避重试并最终降级。

本文件用**离线降级路径**（未配置 LLM）跑通上述验收——这正是 03_BUILD_PLAN §10.3
「全部 LLM 不可用 → 降级研判」的落地形态：兜底规划仍要跑完主链路并取证。
「429/4001 退避重试」的精确断言在 `test_m1_tools.py`。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.harness import Pipeline, SessionManager
from app.store.repo import (
    CheckpointEventRepository,
    EvidenceRepository,
    MemoryRepository,
    RunRepository,
    TaskRepository,
    TraceRepository,
)
from m1_support import ACCEPTANCE_GOAL, FALLBACK_TOOLS, always_failing_impl, build_registry, offline_settings


def _bootstrap(session: Session) -> tuple[SessionManager, str, str]:
    """建 Thread + Run，返回 `(session_manager, thread_id, run_id)`。"""
    sm = SessionManager(session)
    thread = sm.create_thread(title="半导体板块研究", goal=ACCEPTANCE_GOAL)
    run = sm.create_run(thread_id=thread.id)
    return sm, thread.id, run.id


def _pipeline(session: Session, *, registry=None) -> Pipeline:
    return Pipeline(
        session=session,
        settings=offline_settings(),
        registry=registry or build_registry(session),
    )


# --------------------------------------------------------------------------- #
# 主链路验收
# --------------------------------------------------------------------------- #
def test_m1_acceptance_plans_three_tasks_and_five_evidences(session: Session) -> None:
    _, _, run_id = _bootstrap(session)
    result = _pipeline(session).run(run_id, ACCEPTANCE_GOAL)

    # ① ≥3 个 Task 且全部执行完成
    assert result.task_count >= 3
    assert len(result.task_statuses) == result.task_count
    assert all(status == "DONE" for status in result.task_statuses.values())
    assert result.status == "COMPLETED"

    # ② ≥5 条 Evidence 且 C3 强制字段齐全
    evidences = EvidenceRepository(session).list_by_run(run_id)
    assert len(evidences) >= 5
    assert result.evidence_count == len(evidences)
    for ev in evidences:
        assert ev.source, "C3：source 必填"
        assert ev.interface, "C3：interface 必填"
        assert ev.raw_ref, "C3：raw_ref 必填（可回溯原始响应）"
        assert ev.as_of is not None, "C3：as_of 必填（质量元数据第 8 步）"
        assert ev.confidence is not None

    # ③ 无 LLM → 走兜底规划（降级但不空转）
    assert result.plan_source == "fallback"
    assert result.degraded is True


def test_m1_trace_has_full_loop_record_per_task(session: Session) -> None:
    _, _, run_id = _bootstrap(session)
    _pipeline(session).run(run_id, ACCEPTANCE_GOAL)

    traces = TraceRepository(session).list_by_run(run_id)
    names = [t.name for t in traces]
    assert "synthesize" in names, "收口 Span 必须存在"

    tasks = TaskRepository(session).list_by_run(run_id)
    assert len(tasks) >= 3
    for task in tasks:
        task_span = next((t for t in traces if t.name == f"task#{task.seq}"), None)
        assert task_span is not None, f"Task#{task.seq} 缺少 task#N Span"
        assert task_span.attrs_json.get("status") == "DONE"

        loops = [t for t in traces if t.parent_span_id == task_span.span_id]
        assert loops, f"Task#{task.seq} 缺少 loop#M Span"
        assert all(t.name.startswith("loop#") for t in loops)

        # 有工具调用的 Loop 必须同时带 llm（Think）与 tool（Act）两类子 Span。
        # 末轮「计划内工具已用完 → 收口」不发起 LLM 与工具调用，允许无子 Span。
        act_loops = [loop for loop in loops if any(t.parent_span_id == loop.span_id for t in traces)]
        assert act_loops, f"Task#{task.seq} 没有任何含子 Span 的 Loop"
        for loop in act_loops:
            child_names = {t.name for t in traces if t.parent_span_id == loop.span_id}
            assert {"llm", "tool"} <= child_names, f"{task_span.name}/{loop.name} 子 Span 不完整"

        # llm Span 带角色/模型/用量，tool Span 带工具名与重试次数
        llm_spans = [t for t in traces if t.name == "llm"]
        assert all(t.attrs_json.get("role") == "Reasoner" for t in llm_spans)
        tool_spans = [t for t in traces if t.name == "tool"]
        assert all(t.attrs_json.get("name") in FALLBACK_TOOLS for t in tool_spans)
        assert all("attempts" in t.attrs_json for t in tool_spans)


def test_m1_checkpoint_events_and_post_hooks_landed(session: Session) -> None:
    _, thread_id, run_id = _bootstrap(session)
    _pipeline(session).run(run_id, ACCEPTANCE_GOAL)

    # 事件溯源：Run → Plan → Task → Run 终态（seq 单调，由 UNIQUE(run_id, seq) 兜底）
    events = CheckpointEventRepository(session).list_by_run(run_id)
    event_types = [e.event_type for e in events]
    for expected in (
        "RunStarted",
        "PlanGenerated",
        "TaskStarted",
        "TaskCompleted",
        "RunCompleted",
    ):
        assert expected in event_types, f"缺少 checkpoint 事件 {expected}"
    assert [e.seq for e in events] == list(range(1, len(events) + 1))

    # PostHook ③：SESSION 记忆写入
    memories = MemoryRepository(session).list_by_run(run_id)
    assert any(m.scope == "SESSION" and m.kind == "run_summary" for m in memories)
    assert all(m.thread_id == thread_id for m in memories)

    # Run 行终态
    run_row = RunRepository(session).get(run_id)
    assert run_row is not None
    assert run_row.status == "COMPLETED"
    assert run_row.ended_at is not None
    assert run_row.error is None


# --------------------------------------------------------------------------- #
# 降级路径
# --------------------------------------------------------------------------- #
def test_m1_all_tools_degraded_still_delivers_with_audit(session: Session) -> None:
    """全工具降级：不静默失败——显式 DEGRADED + 事件 + 审计留档。"""
    _, _, run_id = _bootstrap(session)
    registry = build_registry(
        session,
        impls={name: always_failing_impl(name) for name in FALLBACK_TOOLS},
    )
    result = _pipeline(session, registry=registry).run(run_id, ACCEPTANCE_GOAL)

    assert result.status == "COMPLETED"
    assert result.degraded is True
    assert result.evidence_count == 0
    assert all(status == "DEGRADED" for status in result.task_statuses.values())

    event_types = [
        e.event_type for e in CheckpointEventRepository(session).list_by_run(run_id)
    ]
    assert "TaskDegraded" in event_types
    assert "RunCompleted" in event_types
    # 降级原因必须落进 Trace 的 tool Span（可追溯）
    tool_spans = [t for t in TraceRepository(session).list_by_run(run_id) if t.name == "tool"]
    assert tool_spans and all(t.attrs_json.get("status") == "DEGRADED" for t in tool_spans)


def test_m1_post_hooks_run_even_when_loop_raises(session: Session, monkeypatch) -> None:
    """时序硬约束：PostHook **无条件执行**（`try/finally`）——崩溃路径下审计仍完整。"""
    _, _, run_id = _bootstrap(session)
    pipeline = _pipeline(session)

    def boom(*_args, **_kwargs):
        raise RuntimeError("模拟主循环崩溃")

    monkeypatch.setattr(pipeline.agent_loop, "run_task", boom)
    result = pipeline.run(run_id, ACCEPTANCE_GOAL)

    assert result.status == "FAILED"
    assert "RuntimeError" in (result.error or "")

    # PostHook 链已跑完：记忆写入 + 终态事件 + Run 行收口
    assert MemoryRepository(session).list_by_run(run_id)
    event_types = [
        e.event_type for e in CheckpointEventRepository(session).list_by_run(run_id)
    ]
    assert "RunFailed" in event_types
    run_row = RunRepository(session).get(run_id)
    assert run_row is not None and run_row.status == "FAILED" and run_row.ended_at is not None

    # 崩溃的 Task Span 被标为 ERROR（不吞异常、不静默）
    assert any(
        t.name.startswith("task#") and t.status == "ERROR"
        for t in TraceRepository(session).list_by_run(run_id)
    )


def test_m1_guard_pre_blocks_out_of_scope_goal(session: Session) -> None:
    """越界目标在 PreHook ③ 即被拦截：不启动 Task、不发工具调用、留事件。"""
    sm, thread_id, _ = _bootstrap(session)
    run = sm.create_run(thread_id=thread_id)
    result = _pipeline(session).run(run.id, "帮我买入半导体龙头并保证收益")

    assert result.status == "STOPPED"
    assert result.task_count == 0
    assert result.blocked_reason and "红线" in result.blocked_reason

    event_types = [
        e.event_type for e in CheckpointEventRepository(session).list_by_run(run.id)
    ]
    assert "GuardRejected" in event_types
    assert "RunStopped" in event_types
    assert TaskRepository(session).list_by_run(run.id) == []
