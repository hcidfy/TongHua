"""Harness 内核：状态机（架构 §5；03_BUILD_PLAN §3 M3）。

职责：为 **Run / Task** 定义合法迁移表，拒绝非法迁移，并让**每次迁移落一条
`checkpoint_events`**——payload 固定含 `from / to / reason / trigger`（架构 §5.3
「这是『为什么没触发 / 为什么停』的答案来源」）。

事件类型只能取自 M0 冻结的 **26 类**（`app/domain/events.py`，不得增删），因此映射表
只覆盖有语义对应的事件；余下的**阶段标记迁移**（如 `EXECUTING → SYNTHESIZING`）
不单独落事件（`emitted=False`），其 `from/to` 会并入紧随其后的终态事件 payload，
避免为一个纯内部阶段凭空造事件类型。

设计取舍：
- `transition_run/_task` 是**纯函数**（只算不写），便于单测穷举迁移表；
- `apply_run/_task` 负责「校验 → 写 `runs`/`tasks` 行 → 落事件」三步；
- **自迁移（from == to）一律放行**（幂等重入）：恢复（resume）、重复调用同一阶段时
  不应被状态机拦下，但仍记录一条迁移以便追溯。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.domain import CheckpointEventType, RunStatus, TaskStatus, now_ms
from app.store.repo import CheckpointEventRepository

__all__ = [
    "IllegalTransition",
    "Transition",
    "RUN_TRANSITIONS",
    "TASK_TRANSITIONS",
    "RUN_TERMINAL",
    "TASK_TERMINAL",
    "RUN_STATUS_EVENT",
    "TASK_STATUS_EVENT",
    "StateMachine",
]

# ---------------------------------------------------------------------- #
# 迁移表（架构 §5.1 / §5.2 的图，逐边落表）
# ---------------------------------------------------------------------- #
RUN_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.CREATED: frozenset({RunStatus.PLANNING, RunStatus.FAILED}),
    RunStatus.PLANNING: frozenset({RunStatus.EXECUTING, RunStatus.STOPPED, RunStatus.FAILED}),
    RunStatus.EXECUTING: frozenset(
        {
            RunStatus.SYNTHESIZING,
            RunStatus.AWAITING_APPROVAL,
            RunStatus.STOPPED,
            RunStatus.FAILED,
        }
    ),
    RunStatus.AWAITING_APPROVAL: frozenset(
        {RunStatus.EXECUTING, RunStatus.STOPPED, RunStatus.FAILED}
    ),
    RunStatus.SYNTHESIZING: frozenset(
        {RunStatus.COMPLETED, RunStatus.STOPPED, RunStatus.FAILED}
    ),
    RunStatus.COMPLETED: frozenset(),
    RunStatus.STOPPED: frozenset(),
    RunStatus.FAILED: frozenset(),
}

TASK_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.PENDING: frozenset({TaskStatus.READY, TaskStatus.SKIPPED}),
    TaskStatus.READY: frozenset({TaskStatus.RUNNING, TaskStatus.SKIPPED}),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.DONE,
            TaskStatus.FAILED,
            TaskStatus.DEGRADED,
            TaskStatus.SKIPPED,
        }
    ),
    TaskStatus.DONE: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.DEGRADED: frozenset(),
    TaskStatus.SKIPPED: frozenset(),
}

#: 终态（不再有出边）。
RUN_TERMINAL: frozenset[RunStatus] = frozenset(
    {RunStatus.COMPLETED, RunStatus.STOPPED, RunStatus.FAILED}
)
TASK_TERMINAL: frozenset[TaskStatus] = frozenset(
    {TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.DEGRADED, TaskStatus.SKIPPED}
)

#: `(from, to) -> 事件类型`；未登记的组合表示「阶段标记迁移」，不单独落事件。
RUN_STATUS_EVENT: dict[tuple[RunStatus, RunStatus], CheckpointEventType] = {
    (RunStatus.CREATED, RunStatus.PLANNING): CheckpointEventType.RunStarted,
    (RunStatus.PLANNING, RunStatus.EXECUTING): CheckpointEventType.PlanApproved,
    (RunStatus.EXECUTING, RunStatus.AWAITING_APPROVAL): CheckpointEventType.RunPaused,
    (RunStatus.AWAITING_APPROVAL, RunStatus.EXECUTING): CheckpointEventType.RunResumed,
    (RunStatus.EXECUTING, RunStatus.COMPLETED): CheckpointEventType.RunCompleted,
    (RunStatus.SYNTHESIZING, RunStatus.COMPLETED): CheckpointEventType.RunCompleted,
    (RunStatus.EXECUTING, RunStatus.STOPPED): CheckpointEventType.RunStopped,
    (RunStatus.PLANNING, RunStatus.STOPPED): CheckpointEventType.RunStopped,
    (RunStatus.AWAITING_APPROVAL, RunStatus.STOPPED): CheckpointEventType.RunStopped,
    (RunStatus.SYNTHESIZING, RunStatus.STOPPED): CheckpointEventType.RunStopped,
    (RunStatus.CREATED, RunStatus.FAILED): CheckpointEventType.RunFailed,
    (RunStatus.PLANNING, RunStatus.FAILED): CheckpointEventType.RunFailed,
    (RunStatus.EXECUTING, RunStatus.FAILED): CheckpointEventType.RunFailed,
    (RunStatus.AWAITING_APPROVAL, RunStatus.FAILED): CheckpointEventType.RunFailed,
    (RunStatus.SYNTHESIZING, RunStatus.FAILED): CheckpointEventType.RunFailed,
}

TASK_STATUS_EVENT: dict[tuple[TaskStatus, TaskStatus], CheckpointEventType] = {
    (TaskStatus.READY, TaskStatus.RUNNING): CheckpointEventType.TaskStarted,
    (TaskStatus.RUNNING, TaskStatus.DONE): CheckpointEventType.TaskCompleted,
    (TaskStatus.RUNNING, TaskStatus.FAILED): CheckpointEventType.TaskFailed,
    (TaskStatus.RUNNING, TaskStatus.DEGRADED): CheckpointEventType.TaskDegraded,
    (TaskStatus.PENDING, TaskStatus.SKIPPED): CheckpointEventType.TaskSkipped,
    (TaskStatus.READY, TaskStatus.SKIPPED): CheckpointEventType.TaskSkipped,
    (TaskStatus.RUNNING, TaskStatus.SKIPPED): CheckpointEventType.TaskSkipped,
}


class IllegalTransition(ValueError):
    """非法状态迁移（架构 §5 表外边）——**拒绝**而非静默放过。"""

    def __init__(self, entity: str, from_status: str, to_status: str) -> None:
        super().__init__(f"非法状态迁移：{entity} {from_status} → {to_status}")
        self.entity = entity
        self.from_status = from_status
        self.to_status = to_status


@dataclass(slots=True)
class Transition:
    """一条迁移记录（也是落进 `checkpoint_events.payload_json` 的内容）。"""

    entity: str  # "run" | "task"
    entity_id: str
    from_status: str
    to_status: str
    #: 事件归属的 Run——Task 迁移也必须落在**该 Run 的事件流**里（`checkpoint_events.run_id`）。
    run_id: str | None = None
    reason: str | None = None
    trigger: str | None = None
    event_type: str | None = None
    emitted: bool = False
    event_id: str | None = None

    def as_payload(self) -> dict[str, Any]:
        """事件 payload（架构 §5.3 的固定四件套 + 归属信息）。"""
        return {
            "from": self.from_status,
            "to": self.to_status,
            "reason": self.reason,
            "trigger": self.trigger,
            "entity": self.entity,
            "entity_id": self.entity_id,
        }


class StateMachine:
    """Run / Task 状态迁移的校验与落库入口。"""

    def __init__(
        self,
        session: Session | None = None,
        *,
        checkpoints: CheckpointEventRepository | None = None,
    ) -> None:
        self.session = session
        self._checkpoints = checkpoints or (
            CheckpointEventRepository(session) if session is not None else None
        )
        #: 本次进程内的迁移轨迹（排障用；持久轨迹在 `checkpoint_events`）。
        self.history: list[Transition] = []

    # ------------------------------------------------------------------ #
    # 校验（纯函数）
    # ------------------------------------------------------------------ #
    @staticmethod
    def can_transition_run(from_status: str, to_status: str) -> bool:
        if from_status == to_status:
            return True  # 幂等重入放行
        try:
            src = RunStatus(from_status)
            dst = RunStatus(to_status)
        except ValueError:
            return False
        return dst in RUN_TRANSITIONS[src]

    @staticmethod
    def can_transition_task(from_status: str, to_status: str) -> bool:
        if from_status == to_status:
            return True
        try:
            src = TaskStatus(from_status)
            dst = TaskStatus(to_status)
        except ValueError:
            return False
        return dst in TASK_TRANSITIONS[src]

    @classmethod
    def assert_run(cls, from_status: str, to_status: str) -> None:
        if not cls.can_transition_run(from_status, to_status):
            raise IllegalTransition("run", str(from_status), str(to_status))

    @classmethod
    def assert_task(cls, from_status: str, to_status: str) -> None:
        if not cls.can_transition_task(from_status, to_status):
            raise IllegalTransition("task", str(from_status), str(to_status))

    # ------------------------------------------------------------------ #
    # 纯迁移（只算不写）
    # ------------------------------------------------------------------ #
    def transition_run(
        self,
        run_id: str,
        from_status: str,
        to_status: str,
        *,
        reason: str | None = None,
        trigger: str | None = None,
    ) -> Transition:
        self.assert_run(from_status, to_status)
        event = self._run_event(from_status, to_status)
        return Transition(
            entity="run",
            entity_id=run_id,
            run_id=run_id,
            from_status=str(from_status),
            to_status=str(to_status),
            reason=reason,
            trigger=trigger,
            event_type=str(event) if event else None,
        )

    def transition_task(
        self,
        task_id: str,
        from_status: str,
        to_status: str,
        *,
        run_id: str | None = None,
        reason: str | None = None,
        trigger: str | None = None,
    ) -> Transition:
        self.assert_task(from_status, to_status)
        event = TASK_STATUS_EVENT.get((TaskStatus(from_status), TaskStatus(to_status)))
        return Transition(
            entity="task",
            entity_id=task_id,
            run_id=run_id,
            from_status=str(from_status),
            to_status=str(to_status),
            reason=reason,
            trigger=trigger,
            event_type=str(event) if event else None,
        )

    @staticmethod
    def _run_event(from_status: str, to_status: str) -> CheckpointEventType | None:
        try:
            key = (RunStatus(from_status), RunStatus(to_status))
        except ValueError:
            return None
        return RUN_STATUS_EVENT.get(key)

    # ------------------------------------------------------------------ #
    # 落库（校验 → 写行 → 落事件）
    # ------------------------------------------------------------------ #
    def apply_run(
        self,
        run: Any,
        to_status: str,
        *,
        reason: str | None = None,
        trigger: str | None = None,
        extra: dict[str, Any] | None = None,
        emit: bool | None = None,
    ) -> Transition:
        """把 `run`（`RunRow`）迁移到 `to_status` 并落事件。

        `emit=False` 可强制不落事件（供阶段标记迁移）；`emit=None` 时按映射表自动决定。
        """
        transition = self.transition_run(
            getattr(run, "id", str(run)),
            str(getattr(run, "status", RunStatus.CREATED)),
            to_status,
            reason=reason,
            trigger=trigger,
        )
        run.status = str(to_status)
        if RunStatus(to_status) in RUN_TERMINAL:
            run.ended_at = run.ended_at or now_ms()
        if reason and str(to_status) in {str(RunStatus.STOPPED), str(RunStatus.FAILED)}:
            run.stop_reason = run.stop_reason or reason
        if self.session is not None:
            self.session.flush()
        should_emit = transition.event_type is not None if emit is None else emit
        self._record(transition, should_emit, extra=extra)
        return transition

    def apply_task(
        self,
        task: Any,
        to_status: str,
        *,
        reason: str | None = None,
        trigger: str | None = None,
        extra: dict[str, Any] | None = None,
        emit: bool | None = None,
    ) -> Transition:
        """把 `task`（`TaskRow`）迁移到 `to_status` 并落事件。"""
        transition = self.transition_task(
            getattr(task, "id", str(task)),
            str(getattr(task, "status", TaskStatus.PENDING)),
            to_status,
            run_id=str(getattr(task, "run_id", "") or "") or None,
            reason=reason,
            trigger=trigger,
        )
        task.status = str(to_status)
        if reason:
            task.degradation_reason = reason if str(to_status) != str(TaskStatus.DONE) else None
        if self.session is not None:
            self.session.flush()
        should_emit = transition.event_type is not None if emit is None else emit
        self._record(transition, should_emit, extra=extra)
        return transition

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _record(
        self,
        transition: Transition,
        emit: bool,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.history.append(transition)
        if not emit or self._checkpoints is None or transition.event_type is None:
            return
        payload = transition.as_payload()
        if extra:
            payload.update(extra)
        # 事件必须落在 Run 的事件流里：Task 迁移的 `run_id` 取自 `task.run_id`
        row = self._checkpoints.append(
            transition.run_id or transition.entity_id, transition.event_type, payload
        )
        transition.emitted = True
        transition.event_id = row.id

    def snapshot(self, limit: int = 10) -> dict[str, Any]:
        """可观测快照：最近若干条迁移（供 Trace / HANDOFF 验收记录）。"""
        return {
            "transitions": len(self.history),
            "recent": [
                {
                    "entity": t.entity,
                    "from": t.from_status,
                    "to": t.to_status,
                    "reason": t.reason,
                    "trigger": t.trigger,
                    "event": t.event_type,
                    "emitted": t.emitted,
                }
                for t in self.history[-limit:]
            ],
        }
