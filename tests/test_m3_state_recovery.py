"""M3 验收（03_BUILD_PLAN §3 M3）——状态与恢复。

验收原文（逐字对应本文件的三个用例）：

1. 执行至第 3 个 Task 中途 kill → 重启 → **前 2 个 Task 的工具调用不重复**
   （以 `tool_calls` 记录数证明）；
2. 审批挂起时刷新页面 → 状态不丢；
3. 构造死循环 → **3 次重复内终止**并给出命中规则名。

其余用例覆盖 M3 的 6 个接口契约与落地口径（状态机迁移表 / 事件 payload 四件套 /
检查点快照折叠 / 预算四维顺序 / 审批超时 / 恢复幂等 / 孤儿扫描）。

**离线确定性**：不触网、不读 `api-key.txt`、不依赖真实 LLM——沿用 `m1_support`
的 `offline_settings()` + 桩工具集。
"""

from __future__ import annotations

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.domain import ApprovalStatus, RunStatus, StopReason, TaskStatus
from app.harness import (
    BudgetExhausted,
    BudgetLimits,
    BudgetManager,
    CheckpointStore,
    HITLGate,
    IllegalTransition,
    Pipeline,
    ResumeEngine,
    SessionManager,
    StateMachine,
    StepView,
    StopRuleSet,
    StopState,
    Usage,
)
from app.harness.stop_rules import action_signature
from app.store.db import session_scope
from app.store.repo import (
    CheckpointEventRepository,
    RunRepository,
    TaskRepository,
    ToolCallRepository,
)
from m1_support import ACCEPTANCE_GOAL, build_registry, offline_settings

#: 离线兜底模板的任务数（`planner.FALLBACK_TEMPLATE`），最后一个即「崩溃点」所在任务。
_TOTAL_TASKS = 4


def _bootstrap(session: Session) -> tuple[SessionManager, str, str]:
    """建 Thread + Run，返回 `(session_manager, thread_id, run_id)`。"""
    sm = SessionManager(session)
    thread = sm.create_thread(title="半导体板块研究", goal=ACCEPTANCE_GOAL)
    run = sm.create_run(thread_id=thread.id)
    return sm, thread.id, run.id


def _pipeline(session: Session, **kwargs: object) -> Pipeline:
    return Pipeline(
        session=session,
        settings=offline_settings(),
        registry=build_registry(session),
        **kwargs,
    )


class _Killed(BaseException):
    """模拟进程被杀：**不是** `Exception`，因此绝不会被 `except Exception` 吞掉。"""


# --------------------------------------------------------------------------- #
# 验收 ①：中途 kill → 重启续跑，前 2 个 Task 的工具调用不重复
# --------------------------------------------------------------------------- #
def test_m3_resume_skips_finished_tasks_without_repeat_tool_calls(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, run_id = _bootstrap(session)
    pipeline = _pipeline(session)

    real_run_task = pipeline.agent_loop.run_task
    seen = {"n": 0}

    def crash_on_third(task, ctx, *, tools=None):  # type: ignore[no-untyped-def]
        seen["n"] += 1
        if seen["n"] >= _TOTAL_TASKS:
            raise _Killed()  # 最后一个 Task 执行中途「进程被杀」
        return real_run_task(task, ctx, tools=tools)

    real_set_status = pipeline._set_run_status

    def suppress_terminal(run_id_: str, to_status: object, **kwargs: object):  # type: ignore[no-untyped-def]
        """真实 kill 时 `finally` 不会执行——屏蔽终态写入以还原「崩溃瞬间」的库状态。"""
        if str(to_status) in {"COMPLETED", "STOPPED", "FAILED"}:
            return None
        return real_set_status(run_id_, to_status, **kwargs)

    monkeypatch.setattr(pipeline.agent_loop, "run_task", crash_on_third)
    monkeypatch.setattr(pipeline, "_set_run_status", suppress_terminal)

    with pytest.raises(_Killed):
        pipeline.run(run_id, ACCEPTANCE_GOAL)

    # --- 崩溃瞬间的库状态：Run 非终态（可恢复），前 N-1 个 Task 已 DONE ---
    run = RunRepository(session).get(run_id)
    assert run is not None
    assert run.status == str(RunStatus.EXECUTING), "崩溃的 Run 必须停在非终态才能被续跑"
    assert run.ended_at is None

    tasks = TaskRepository(session).list_by_run(run_id)
    assert [t.status for t in tasks] == [str(TaskStatus.DONE)] * (_TOTAL_TASKS - 1) + [
        str(TaskStatus.RUNNING)
    ]

    before = ToolCallRepository(session).list_by_run(run_id)
    before_keys = {row.dedupe_key for row in before}
    assert before_keys, "前 2 个 Task 应当已产生工具调用记录（验收证据基线）"

    # --- 重启：全新 Pipeline + ResumeEngine（内存对象全换，DB 是唯一事实来源）---
    engine_ = ResumeEngine(session, settings=offline_settings(), pipeline=_pipeline(session))
    assert run_id in engine_.scan_orphans(), "非终态 Run 必须被启动扫描发现"

    result = engine_.resume(run_id)

    assert result.status == str(RunStatus.COMPLETED)
    assert result.task_count == _TOTAL_TASKS

    # --- 验收断言：工具调用**不重复发生**（以 `tool_calls` 记录数证明）---
    after = ToolCallRepository(session).list_by_run(run_id)
    after_keys = [row.dedupe_key for row in after]
    assert len(after_keys) == len(set(after_keys)), "任何工具调用都不得重复落库（UNIQUE + ②幂等复用）"
    assert before_keys <= set(after_keys), "前 2 个 Task 的调用记录一条都不能丢"

    task12 = {task.id for task in tasks if task.seq <= 2}
    assert sum(1 for row in after if row.task_id in task12) == sum(
        1 for row in before if row.task_id in task12
    ), "前 2 个 Task 的工具调用记录数必须不变（即未重新调用）"

    # 续跑只补做了最后一个 Task：新增记录全部属于它
    new_rows = [row for row in after if row.dedupe_key not in before_keys]
    last_task = next(task for task in tasks if task.seq == _TOTAL_TASKS)
    assert new_rows, "崩溃的那个 Task 必须被真正执行（续跑不是空转）"
    assert {row.task_id for row in new_rows} == {last_task.id}

    # 终态收口
    assert all(t.status == str(TaskStatus.DONE) for t in TaskRepository(session).list_by_run(run_id))
    final_run = RunRepository(session).get(run_id)
    assert final_run is not None and final_run.status == str(RunStatus.COMPLETED)
    assert final_run.ended_at is not None


def test_m3_resume_is_idempotent_on_terminal_run(session: Session) -> None:
    """已终态的 Run 再 resume → 幂等空操作，绝不重跑工具。"""
    _, _, run_id = _bootstrap(session)
    _pipeline(session).run(run_id, ACCEPTANCE_GOAL)
    before = len(ToolCallRepository(session).list_by_run(run_id))

    result = ResumeEngine(session, settings=offline_settings()).resume(run_id)

    assert result.status == str(RunStatus.COMPLETED)
    assert any("幂等" in note for note in result.notes)
    assert len(ToolCallRepository(session).list_by_run(run_id)) == before


# --------------------------------------------------------------------------- #
# 验收 ②：审批挂起 → 刷新页面状态不丢
# --------------------------------------------------------------------------- #
def test_m3_approval_suspend_survives_page_refresh(engine: Engine) -> None:
    approval_id: str = ""
    run_id: str = ""

    # —— 第一次请求：规划完成后挂起（冻结 Loop，不推进任何 Task）——
    with session_scope(engine) as sess:
        _, _, run_id = _bootstrap(sess)
        result = _pipeline(sess, await_plan_approval=True).run(run_id, ACCEPTANCE_GOAL)

        assert result.status == str(RunStatus.AWAITING_APPROVAL)
        assert result.approval_id
        assert result.task_statuses == {}, "挂起即冻结：不得推进任何 Task（无任何 Task 终态）"
        assert TaskRepository(sess).list_by_run(run_id) == []
        assert RunRepository(sess).get(run_id).status == str(RunStatus.AWAITING_APPROVAL)
        approval_id = result.approval_id

    # —— 「刷新页面」：全新 Session，状态全在 DB，不靠内存 ——
    with session_scope(engine) as sess:
        gate = HITLGate(sess, settings=offline_settings())
        assert gate.awaiting(run_id) is True
        assert gate.status(approval_id) == str(ApprovalStatus.PENDING)

    # —— 人工裁决：通过 → Run 回 EXECUTING ——
    with session_scope(engine) as sess:
        HITLGate(sess, settings=offline_settings()).resume(approval_id, "GRANTED")

    with session_scope(engine) as sess:
        assert HITLGate(sess, settings=offline_settings()).awaiting(run_id) is False
        assert RunRepository(sess).get(run_id).status == str(RunStatus.EXECUTING)

        types = [e.event_type for e in CheckpointEventRepository(sess).list_by_run(run_id)]
        for expected in ("ApprovalRequested", "RunPaused", "ApprovalGranted", "RunResumed"):
            assert expected in types, f"审批链路缺少事件 {expected}"


def test_m3_resume_after_approval_replans_and_finishes(engine: Engine) -> None:
    """挂起发生在规划落库前（`tasks` 表为空）→ 恢复时重新规划并把活干完，不空转收口。"""
    with session_scope(engine) as sess:
        _, _, run_id = _bootstrap(sess)
        result = _pipeline(sess, await_plan_approval=True).run(run_id, ACCEPTANCE_GOAL)
        assert result.status == str(RunStatus.AWAITING_APPROVAL)
        approval_id = result.approval_id

    with session_scope(engine) as sess:
        HITLGate(sess, settings=offline_settings()).resume(approval_id, "GRANTED")

    with session_scope(engine) as sess:
        result = ResumeEngine(
            sess, settings=offline_settings(), pipeline=_pipeline(sess)
        ).resume(run_id)

        assert result.status == str(RunStatus.COMPLETED)
        assert result.task_count == _TOTAL_TASKS
        tasks = TaskRepository(sess).list_by_run(run_id)
        assert [t.seq for t in tasks] == list(range(1, _TOTAL_TASKS + 1))
        assert all(t.status == str(TaskStatus.DONE) for t in tasks)


def test_m3_hitl_expires_pending_approval_after_timeout(session: Session) -> None:
    """超时结算：`expire_due()` 把仍 PENDING 的审批置 EXPIRED，并把 Run 收口为 STOPPED。"""
    _, _, run_id = _bootstrap(session)
    sm = StateMachine(session)
    run = RunRepository(session).get(run_id)
    sm.apply_run(run, RunStatus.PLANNING)
    sm.apply_run(run, RunStatus.EXECUTING)

    gate = HITLGate(session, settings=offline_settings(), timeout_s=0.0)  # 立即超时
    approval_id = gate.request(run_id, "expensive_tool", {"tool": "get_price_history"})
    assert gate.awaiting(run_id) is True

    assert gate.expire_due() == [approval_id]

    assert gate.awaiting(run_id) is False
    assert gate.status(approval_id) == str(ApprovalStatus.EXPIRED)
    assert RunRepository(session).get(run_id).status == str(RunStatus.STOPPED)


# --------------------------------------------------------------------------- #
# 验收 ③：死循环 3 次重复内终止，并给出命中规则名
# --------------------------------------------------------------------------- #
def test_m3_loop_detected_within_three_repeats_and_names_hit_rule() -> None:
    rules = StopRuleSet()
    assert len(rules.rules) == 8, "FR-08 固定 8 条停止规则"

    state = StopState(budget=BudgetManager())
    signature = action_signature("get_hot_rank", {"tag_id": 1})
    step = StepView(signature=signature, new_evidence=0)

    assert rules.observe(state, step) is None  # 第 1 次
    assert rules.observe(state, step) is None  # 第 2 次
    reason = rules.observe(state, step)  # 第 3 次 → 命中死循环

    assert reason is StopReason.LOOP_DETECT
    assert rules.hit_rule == "loop_detect", "必须能报出命中的规则名"
    assert state.repeat_count == 3
    assert rules.rule_for(StopReason.LOOP_DETECT) == "loop_detect"

    # 换了动作签名 → 计数归零，不再误判
    other = StepView(signature=action_signature("get_valuation", {}), new_evidence=1)
    assert rules.observe(state, other) is None
    assert state.repeat_count == 1


# --------------------------------------------------------------------------- #
# 接口契约：状态机 / 检查点 / 预算
# --------------------------------------------------------------------------- #
def test_m3_state_machine_rejects_illegal_transition_and_records_event(session: Session) -> None:
    _, _, run_id = _bootstrap(session)
    sm = StateMachine(session)

    # 纯函数：迁移表判定
    assert StateMachine.can_transition_run(str(RunStatus.CREATED), str(RunStatus.PLANNING)) is True
    assert StateMachine.can_transition_run(str(RunStatus.CREATED), str(RunStatus.COMPLETED)) is False
    assert StateMachine.can_transition_run(str(RunStatus.COMPLETED), str(RunStatus.EXECUTING)) is False

    # 合法迁移：写状态 + 落事件（payload 四件套齐全）
    run = RunRepository(session).get(run_id)
    sm.apply_run(run, RunStatus.PLANNING, reason="开始规划", trigger="test")
    assert run.status == str(RunStatus.PLANNING)

    event = CheckpointEventRepository(session).list_by_run(run_id)[-1]
    assert event.event_type == "RunStarted"
    payload = event.payload_json
    assert (payload["from"], payload["to"]) == (str(RunStatus.CREATED), str(RunStatus.PLANNING))
    assert payload["reason"] == "开始规划"
    assert payload["trigger"] == "test"

    # 表外迁移：拒绝而非静默放过，且不污染当前状态
    with pytest.raises(IllegalTransition):
        sm.apply_run(run, RunStatus.COMPLETED)
    assert run.status == str(RunStatus.PLANNING)


def test_m3_task_transition_event_lands_on_own_run_stream(session: Session) -> None:
    """回归护栏：Task 迁移事件必须落在**该 Run** 的事件流里（否则违反 `checkpoint_events` 外键）。"""
    _, _, run_id = _bootstrap(session)
    task = TaskRepository(session).create(run_id=run_id, seq=1, title="t", intent="i")

    sm = StateMachine(session)
    sm.apply_task(task, TaskStatus.READY, reason="依赖就绪")
    sm.apply_task(task, TaskStatus.RUNNING, reason="开始执行")

    events = CheckpointEventRepository(session).list_by_run(run_id)
    assert events[-1].event_type == "TaskStarted"
    assert events[-1].payload_json["to"] == str(TaskStatus.RUNNING)
    assert events[-1].payload_json["entity_id"] == task.id
    assert [e.seq for e in events] == list(range(1, len(events) + 1))


def test_m3_checkpoint_store_folds_snapshots_and_replays_state(session: Session) -> None:
    _, _, run_id = _bootstrap(session)
    store = CheckpointStore(session, snapshot_every=2)
    store.append(run_id, "RunStarted", goal=ACCEPTANCE_GOAL)
    store.append(run_id, ("PlanGenerated", {"source": "fallback", "tasks": _TOTAL_TASKS}))
    store.append(run_id, ("StopRuleHit", {"reason": str(StopReason.LOOP_DETECT)}))

    stream = store.load(run_id)
    assert stream.snapshot is not None and stream.snapshot.through_seq == 2
    # 裁剪：只回放最后一个快照之后的增量事件
    assert stream.count == 1 and stream.types == ["StopRuleHit"]

    state = stream.state()
    assert state["goal"] == ACCEPTANCE_GOAL  # 来自快照
    assert state["plan_source"] == "fallback"
    assert state["stop_rule_hit"] == str(StopReason.LOOP_DETECT)  # 来自增量

    full = store.load(run_id, clipped=False)
    assert full.count == 3
    assert store.snapshot(run_id)["snapshots"] == 1


def test_m3_budget_charges_and_reports_exhaustion_in_order() -> None:
    limits = BudgetLimits(max_steps=2, max_tokens=10, max_cost=1.0, max_wall_time_s=5.0)
    manager = BudgetManager(limits=limits)

    manager.charge(Usage(steps=1, tokens=5, cost=0.5, wall_time_ms=1000))
    assert manager.exhausted() is None
    assert manager.remaining()["steps"] == 1.0
    assert manager.remaining()["wall_time_s"] == 4.0

    manager.charge(steps=2)
    assert manager.exhausted() is StopReason.MAX_STEPS  # ① steps 优先

    manager.charge(steps=0, tokens=10)
    assert manager.exhausted() is StopReason.MAX_TOKENS  # ② tokens

    manager.charge(tokens=0, cost=1.0)
    assert manager.exhausted() is StopReason.MAX_COST  # ③ cost

    manager.charge(cost=0.0, wall_time_ms=5000)
    assert manager.exhausted() is StopReason.MAX_WALL_TIME  # ④ wall_time

    with pytest.raises(BudgetExhausted) as excinfo:
        manager.guard()
    assert excinfo.value.reason is StopReason.MAX_WALL_TIME
