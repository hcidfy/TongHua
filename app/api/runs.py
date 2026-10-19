"""`api/runs.py`：Run 的 REST + 指令端点（03_BUILD_PLAN §3 M5）。

覆盖架构 §13 的：
`GET /api/runs/{id}`、`GET /api/runs/{id}/evidences|artifacts|traces|cost`、
`POST /api/runs/{id}/pause|resume|abort`、`POST /api/runs/{id}/plan`。

另含 **Run 驱动**（`start_run` / `drive_run` / `start_background_run`）——`POST
/api/threads/{id}/runs` 的实际执行体也在这里，供 `threads.py` 复用。

驱动口径（离线确定性）：
- 工具注册表由**注入的 `Settings`** 构造（`build_client(settings)`），
  不经过全局 `get_settings()`，因此测试注入 `offline_settings()` 后**不读 `api-key.txt`**；
- `mock_data_mode=True`（默认）时取数走本地 Mock，**不触网**；
- 未配置 LLM 时 `ModelRouter` 一律降级为兜底规划，**不依赖真实 LLM**。
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.domain import now_ms
from app.harness import Pipeline
from app.harness.planner import PlannedTask
from app.harness.resume import ResumePlan
from app.harness.state_machine import IllegalTransition, StateMachine
from app.harness.tool_registry import ToolRegistry
from app.store.repo import (
    ArtifactRepository,
    CheckpointEventRepository,
    EvidenceRepository,
    LlmCallRepository,
    RunRepository,
    StepRepository,
    TaskRepository,
    ThreadRepository,
    ToolCallRepository,
    TraceRepository,
    UIBlockRepository,
)
from app.store.schema import StepRow, TaskRow

from .common import (
    RUN_TERMINAL_STATUSES,
    RunCreate,
    artifact_dict,
    evidence_dict,
    get_app_settings,
    get_db,
    not_found,
    run_dict,
    step_dict,
    task_dict,
    trace_dict,
)

__all__ = ["router", "drive_run", "start_run", "start_background_run", "build_registry"]

router = APIRouter(prefix="/api/runs", tags=["runs"])


class PlanTaskIn(BaseModel):
    seq: int
    title: str = Field(min_length=1)
    intent: str = ""
    tools: list[str] = Field(default_factory=list)
    deps: list[int] = Field(default_factory=list)


class PlanOverride(BaseModel):
    """人工改判后的任务清单（`goal` 缺省沿用 Thread 的目标）。"""

    tasks: list[PlanTaskIn] = Field(min_length=1)
    goal: str | None = None


# --------------------------------------------------------------------------- #
# Run 驱动
# --------------------------------------------------------------------------- #
def build_registry(session: Session, settings: Settings) -> ToolRegistry:
    """按**注入的** `Settings` 装配 10 个受控工具（9 数据 + 1 内部 `emit_artifact`）。"""
    from app.data import build_client, build_ifind_client
    from app.tools import build_tools

    return build_tools(
        ToolRegistry(session),
        client=build_client(settings),
        ifind=build_ifind_client(settings),
    )


def drive_run(
    *,
    session: Session,
    settings: Settings,
    run_id: str,
    goal: str,
    resume_plan: Any = None,
) -> Any:
    """驱动一次 Run（PreHook → AgentLoop → PostHook → M5 产物/UI 收口）。"""
    pipeline = Pipeline(
        session=session,
        settings=settings,
        registry=build_registry(session, settings),
    )
    return pipeline.run(run_id, goal, resume_plan=resume_plan)


def start_background_run(
    *,
    session_factory: Callable[[], Any],
    settings: Settings,
    run_id: str,
    goal: str,
) -> threading.Thread:
    """后台线程驱动 Run（前端随即接 SSE 观察增量）。

    线程内**自建 Session**（Session 不跨线程共享）；异常不静默——写回 Run 行，
    SSE 侧以 `error` 事件收口。
    """

    def _worker() -> None:
        try:
            with session_factory() as session:
                drive_run(session=session, settings=settings, run_id=run_id, goal=goal)
        except Exception as exc:  # 后台线程兜底：必须留下痕迹
            try:
                with session_factory() as session:
                    RunRepository(session).update_status(
                        run_id,
                        status="FAILED",
                        error=f"{type(exc).__name__}: {exc}",
                        ended_at=now_ms(),
                    )
            except Exception:  # pragma: no cover - 连兜底写库都失败时不再抛
                pass

    thread = threading.Thread(target=_worker, name=f"run-{run_id}", daemon=True)
    thread.start()
    return thread


def start_run(
    payload: RunCreate,
    *,
    thread_row: Any,
    session: Session,
    settings: Settings,
    session_factory: Callable[[], Any],
) -> dict[str, Any]:
    """创建 Run 行并（可选）后台驱动；返回 `{run_id, status, goal, background}`。"""
    goal = (payload.goal or thread_row.goal or "").strip()
    if not goal:
        raise HTTPException(status_code=422, detail="缺少研究目标（goal）")

    run = RunRepository(session).create(thread_id=thread_row.id, status="CREATED")
    session.flush()

    if payload.background:
        start_background_run(
            session_factory=session_factory, settings=settings, run_id=run.id, goal=goal
        )
        status = "RUNNING"
    else:
        # 同步执行：同一请求内跑完（测试用；离线确定性）
        result = drive_run(session=session, settings=settings, run_id=run.id, goal=goal)
        status = str(result.status)

    return {
        "run_id": run.id,
        "thread_id": thread_row.id,
        "status": status,
        "goal": goal,
        "background": bool(payload.background),
    }


# --------------------------------------------------------------------------- #
# 读
# --------------------------------------------------------------------------- #
def _require_run(session: Session, run_id: str) -> Any:
    run = RunRepository(session).get(run_id)
    if run is None:
        raise not_found("Run", run_id)
    return run


def _goal_of(session: Session, run: Any) -> str | None:
    thread = ThreadRepository(session).get(getattr(run, "thread_id", "")) if run else None
    return getattr(thread, "goal", None)


@router.get("/{run_id}")
def get_run(run_id: str, session: Session = Depends(get_db)) -> dict[str, Any]:
    """Run 状态快照（含目标 / 成本 / 任务清单）。"""
    run = _require_run(session, run_id)
    data = run_dict(run, goal=_goal_of(session, run))
    data["tasks"] = [
        task_dict(row) for row in TaskRepository(session).list_by_run(run_id)
    ]
    data["ui_block_count"] = UIBlockRepository(session).count_by_run(run_id)
    data["artifact_id"] = getattr(ArtifactRepository(session).latest(run_id), "id", None)
    return data


@router.get("/{run_id}/evidences")
def list_evidences(run_id: str, session: Session = Depends(get_db)) -> dict[str, Any]:
    """证据列表（C3 强制字段逐条给出，可回溯 `raw_ref`）。"""
    _require_run(session, run_id)
    rows = EvidenceRepository(session).list_by_run(run_id)
    calls = ToolCallRepository(session).list_by_run(run_id)
    by_id = {call.id: call for call in calls}
    evidences = []
    for row in rows:
        item = evidence_dict(row)
        call = by_id.get(row.tool_call_id)
        item["tool_name"] = call.tool_name if call is not None else None
        evidences.append(item)
    return {
        "run_id": run_id,
        "count": len(rows),
        "evidences": evidences,
        "tool_calls": [
            {
                "task_id": call.task_id,
                "tool_name": call.tool_name,
                "status": call.status,
                "error_message": call.error_message,
            }
            for call in calls
        ],
    }


@router.get("/{run_id}/artifacts")
def list_artifacts(run_id: str, session: Session = Depends(get_db)) -> dict[str, Any]:
    """产物列表（三层分区 + citations；重跑时版本递增）。"""
    _require_run(session, run_id)
    rows = ArtifactRepository(session).list_by_run(run_id)
    latest = rows[-1] if rows else None
    return {
        "run_id": run_id,
        "count": len(rows),
        "artifacts": [artifact_dict(r) for r in rows],
        "latest": artifact_dict(latest) if latest is not None else None,
    }


@router.get("/{run_id}/traces")
def list_traces(run_id: str, session: Session = Depends(get_db)) -> dict[str, Any]:
    """Span 树（扁平存 + `parent_span_id` 指针，前端组树）。"""
    _require_run(session, run_id)
    rows = TraceRepository(session).list_by_run(run_id)
    failed = sum(1 for row in rows if str(getattr(row, "status", "OK")) != "OK")
    return {
        "run_id": run_id,
        "count": len(rows),
        "render_failed": failed,
        "spans": [trace_dict(r) for r in rows],
    }


@router.get("/{run_id}/cost")
def get_cost(run_id: str, session: Session = Depends(get_db)) -> dict[str, Any]:
    """成本汇总（口径唯一来源：`llm_calls` 聚合，HANDOFF §6.2）。"""
    run = _require_run(session, run_id)
    repo = LlmCallRepository(session)
    cost, tokens = repo.totals_for_run(run_id)
    calls = repo.list_by_run(run_id)
    by_model: dict[str, dict[str, float]] = {}
    for call in calls:
        bucket = by_model.setdefault(
            str(getattr(call, "model", "")), {"calls": 0.0, "tokens": 0.0, "cost": 0.0}
        )
        bucket["calls"] += 1
        bucket["tokens"] += float(getattr(call, "total_tokens", 0) or 0)
        bucket["cost"] += float(getattr(call, "cost", 0.0) or 0.0)
    return {
        "run_id": run_id,
        "status": str(getattr(run, "status", "")),
        "cost_total": cost,
        "tokens_total": tokens,
        "llm_calls": len(calls),
        "by_model": by_model,
    }


# --------------------------------------------------------------------------- #
# 指令：暂停 / 恢复 / 终止
# --------------------------------------------------------------------------- #
def _apply_transition(run: Any, to_status: str, *, reason: str, session: Session) -> dict[str, Any]:
    try:
        StateMachine(session).apply_run(run, to_status, reason=reason, trigger="api")
    except IllegalTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return run_dict(run)


@router.post("/{run_id}/pause")
def pause_run(run_id: str, session: Session = Depends(get_db)) -> dict[str, Any]:
    """暂停：`EXECUTING → AWAITING_APPROVAL`（架构 §5.1 的挂起边，落 `RunPaused`）。"""
    run = _require_run(session, run_id)
    if str(run.status) in RUN_TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail=f"Run 已处于终态 {run.status}，无法暂停")
    return _apply_transition(run, "AWAITING_APPROVAL", reason="用户暂停", session=session)


@router.post("/{run_id}/resume")
def resume_run(run_id: str, session: Session = Depends(get_db)) -> dict[str, Any]:
    """恢复：`AWAITING_APPROVAL → EXECUTING`（落 `RunResumed`）。"""
    run = _require_run(session, run_id)
    return _apply_transition(run, "EXECUTING", reason="用户恢复", session=session)


@router.post("/{run_id}/abort")
def abort_run(run_id: str, session: Session = Depends(get_db)) -> dict[str, Any]:
    """终止：迁到 `STOPPED` 并记 `StopReason.USER_ABORT`（服务端硬需求「可终止」）。"""
    run = _require_run(session, run_id)
    if str(run.status) in RUN_TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail=f"Run 已处于终态 {run.status}，无法终止")
    run.stop_reason = "USER_ABORT"
    data = _apply_transition(run, "STOPPED", reason="用户终止", session=session)
    data["stop_reason"] = "USER_ABORT"
    return data


@router.post("/{run_id}/plan")
def override_plan(
    run_id: str,
    payload: PlanOverride,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    """人工改判计划后**重新执行**：按给定任务清单续跑当前 Run（`ResumePlan` 通道）。

    复用 M3 的续跑通道（`Pipeline.run(resume_plan=...)`）：不新增事件、不新增表，
    已 `DONE` 的 Task 由 `seq` 跳过（`finished_seqs`）。
    """
    run = _require_run(session, run_id)
    if str(run.status) == "COMPLETED":
        raise HTTPException(status_code=409, detail="Run 已完成，无需改判计划")

    goal = (payload.goal or _goal_of(session, run) or "").strip()
    finished = {
        int(row.seq)
        for row in TaskRepository(session).list_by_run(run_id)
        if str(getattr(row, "status", "")) == "DONE"
    }
    planned = [
        PlannedTask(
            seq=int(item.seq),
            title=item.title,
            intent=item.intent,
            tools=list(item.tools),
            deps=list(item.deps),
        )
        for item in payload.tasks
    ]
    rest = sorted({task.seq for task in planned})
    resume_plan = ResumePlan(
        run_id=run_id,
        goal=goal,
        planned_tasks=planned,
        finished_seqs=finished,
        unfinished_seqs=rest,
        resume_from_seq=rest[0] if rest else None,
        state={"source": "api_plan_override"},
        source="api",
        note="人工改判计划",
    )
    result = drive_run(session=session, settings=settings, run_id=run_id, goal=goal, resume_plan=resume_plan)
    return {
        "run_id": run_id,
        "status": str(result.status),
        "task_count": int(result.task_count),
        "evidence_count": int(result.evidence_count),
        "artifact_id": result.artifact_id,
        "ui_block_count": int(result.ui_block_count),
        "render_failed_count": int(result.render_failed_count),
        "skipped_seqs": sorted(finished),
    }


# --------------------------------------------------------------------------- #
# 内部：`step` 事件取数（供 stream.py 复用）
# --------------------------------------------------------------------------- #
def list_steps(session: Session, run_id: str) -> list[StepRow]:
    """按 Run 取全部 Step（`steps` 表无 `run_id`，经 `tasks` 关联）。"""
    stmt = (
        select(StepRow)
        .join(TaskRow, TaskRow.id == StepRow.task_id)
        .where(TaskRow.run_id == run_id)
        .order_by(StepRow.created_at, StepRow.seq)
    )
    return list(session.scalars(stmt).all())


def step_payloads(session: Session, run_id: str) -> list[dict[str, Any]]:
    return [step_dict(row) for row in list_steps(session, run_id)]


def pending_events(session: Session, run_id: str, after_seq: int) -> list[Any]:
    """`checkpoint_events` 增量（SSE 重放/续传用）。"""
    return CheckpointEventRepository(session).list_by_run(run_id, after_seq=after_seq)
