"""Harness 内核：恢复引擎（架构 §4.3 / §6；03_BUILD_PLAN §3 M3）。

契约（03_BUILD_PLAN §3 M3 接口契约）::

    ResumeEngine.resume(run_id) -> RunResult
    ResumeEngine.scan_orphans() -> list[run_id]        # 启动时扫描（取代 recovery_scanner）

恢复算法（架构 §4.3，逐字落地）：
`load(run_id)` → 按 seq 重放 → 重建内存态 → 定位最后一个**未完成**的 Task →
从该 Task 起点续跑（已完成的 Task 直接跳过）。

**「不重复调用工具」靠什么保证**：已完成的 Task 直接从 `tasks` 表读出终态并跳过（连
`Step`/工具调用都不会重发）；即便某 Task 是「执行到一半崩的」（行仍 `RUNNING`），
`ToolExecutor` 在这些 Task 上的每一跳也会先查 `tool_calls.dedupe_key`
（架构 §6.2 第 1 层）——命中即复用证据、不再打网络。二者叠加，`tool_calls` 的记录数
就是验收证据（M3 验收：「前 2 个 Task 的工具调用不重复发生」）。

`scan_orphans()` 的三件事：
1. 先用 `HITLGate.expire_due()` 结算超时审批（避免「冻结中的 Run」被误当孤儿续跑）；
2. 扫 `RunRepository.list_resumable()`（非终态且 `ended_at IS NULL`）；
3. 把**仍被审批冻结**的 Run 标为「需人工恢复」，其余标为「可自动恢复」。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.domain import CheckpointEventType, RunStatus, TaskStatus
from app.store.repo import RunRepository, TaskRepository, ThreadRepository, ToolCallRepository

from .checkpoint import CheckpointStore
from .hitl import HITLGate
from .planner import PlannedTask
from .state_machine import StateMachine

__all__ = ["ResumePlan", "OrphanReport", "ResumeEngine"]

#: 任务未完成态（「最后一个未完成的 Task」的判定口径）。
UNFINISHED_TASK_STATUSES: frozenset[str] = frozenset(
    {str(TaskStatus.PENDING), str(TaskStatus.READY), str(TaskStatus.RUNNING)}
)

#: Run 终态。
TERMINAL_RUN_STATUSES: frozenset[str] = frozenset(
    {str(RunStatus.COMPLETED), str(RunStatus.STOPPED), str(RunStatus.FAILED)}
)


@dataclass(slots=True)
class ResumePlan:
    """从事件 + `tasks` 表重建出的续跑计划。"""

    run_id: str
    goal: str
    #: 全部计划任务（已完成的也保留，便于对照 seq 跳过）。
    planned_tasks: list[PlannedTask] = field(default_factory=list)
    #: 已终态的任务 seq（续跑时直接跳过）。
    finished_seqs: set[int] = field(default_factory=set)
    #: 未完成的任务 seq（升序）。
    unfinished_seqs: list[int] = field(default_factory=list)
    #: 续跑起点（第一个未完成 Task 的 seq）；全部完成则为 `None`。
    resume_from_seq: int | None = None
    #: 当前仍待决的审批（非 `None` 时**不得**自动续跑）。
    pending_approval_id: str | None = None
    #: 内存态（由事件重放得到）。
    state: dict[str, Any] = field(default_factory=dict)
    #: 计划来源：`db_rows`（优先，忠实于崩溃前的计划）或 `planner`（无行时重新规划）。
    source: str = "db_rows"
    note: str | None = None

    @property
    def resumable(self) -> bool:
        return self.resume_from_seq is not None and self.pending_approval_id is None

    def remaining_tasks(self) -> list[PlannedTask]:
        return [t for t in self.planned_tasks if t.seq not in self.finished_seqs]


@dataclass(slots=True)
class OrphanReport:
    """启动扫描结果（`scan_orphans()` 的可观测形态）。"""

    auto_resumable: list[str] = field(default_factory=list)
    needs_manual: list[str] = field(default_factory=list)
    expired_approvals: list[str] = field(default_factory=list)

    @property
    def run_ids(self) -> list[str]:
        return [*self.auto_resumable, *self.needs_manual]


class ResumeEngine:
    """事件重放 + 续跑 + 孤儿扫描。"""

    def __init__(
        self,
        session: Session | None = None,
        *,
        store: CheckpointStore | None = None,
        hitl: HITLGate | None = None,
        state_machine: StateMachine | None = None,
        runs: RunRepository | None = None,
        tasks: TaskRepository | None = None,
        threads: ThreadRepository | None = None,
        tool_calls: ToolCallRepository | None = None,
        settings: Settings | None = None,
        pipeline: Any = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self._store = store or (CheckpointStore(session) if session is not None else None)
        self._hitl = hitl or (HITLGate(session, settings=self.settings) if session is not None else None)
        self._sm = state_machine or (StateMachine(session) if session is not None else None)
        self._runs = runs or (RunRepository(session) if session is not None else None)
        self._tasks = tasks or (TaskRepository(session) if session is not None else None)
        self._threads = threads or (ThreadRepository(session) if session is not None else None)
        self._tool_calls = tool_calls or (
            ToolCallRepository(session) if session is not None else None
        )
        self._pipeline = pipeline

    # ------------------------------------------------------------------ #
    # 契约入口
    # ------------------------------------------------------------------ #
    def resume(self, run_id: str, *, pipeline: Any = None) -> Any:
        """续跑一个中断的 Run；返回 `RunResult`。

        - Run 已是终态 → **幂等返回**当前状态，不重跑；
        - Run 仍被审批冻结 → 返回 `AWAITING_APPROVAL`，不自动推进；
        - 否则：跳过已完成 Task，从第一个未完成 Task 起点续跑。
        """
        run = self._runs.get(run_id) if self._runs is not None else None
        if run is None:
            raise KeyError(f"Run 不存在：{run_id}")

        from .pipeline import RunResult  # 延迟导入：避免 harness 内部环

        engine = pipeline if pipeline is not None else self._pipeline
        plan = self.build_plan(run_id)
        if str(run.status) in TERMINAL_RUN_STATUSES:
            return RunResult(
                run_id=run_id,
                status=str(run.status),
                stop_reason=run.stop_reason,
                task_count=len(plan.planned_tasks),
                notes=["Run 已处于终态，恢复为幂等空操作"],
            )
        if plan.pending_approval_id is not None:
            return RunResult(
                run_id=run_id,
                status=str(RunStatus.AWAITING_APPROVAL),
                stop_reason=None,
                approval_id=plan.pending_approval_id,
                task_count=len(plan.planned_tasks),
                notes=["Run 仍被审批冻结，需人工裁决后再恢复"],
            )

        if engine is None:
            from .pipeline import Pipeline  # 延迟导入

            engine = Pipeline(session=self.session, settings=self.settings)
        self._replan_if_empty(plan, engine)
        self._fill_tools_from_planner(plan, engine)
        self._emit_resumed(run_id, plan)
        return engine.run(run_id, plan.goal, resume_plan=plan)

    def scan_orphans(self) -> list[str]:
        """扫描需要恢复的 Run（启动时调用）。返回其 `run_id` 列表。"""
        return self.scan().run_ids

    def scan(self) -> OrphanReport:
        """`scan_orphans()` 的详细形态：区分「可自动恢复」与「需人工恢复」。"""
        report = OrphanReport()
        if self._runs is None:
            return report
        if self._hitl is not None:
            report.expired_approvals = self._hitl.expire_due()
        for run in self._runs.list_resumable():
            plan = self.build_plan(run.id)
            if plan.pending_approval_id is not None:
                report.needs_manual.append(run.id)
            else:
                report.auto_resumable.append(run.id)
        return report

    # ------------------------------------------------------------------ #
    # 重放与定位
    # ------------------------------------------------------------------ #
    def replay(self, run_id: str) -> dict[str, Any]:
        """事件重放重建内存态（`{run_status, task_statuses, evidence_ids, ...}`）。"""
        if self._store is None:
            return {}
        return self._store.load(run_id).state()

    def unfinished_tasks(self, run_id: str) -> list[Any]:
        """未完成的 Task 行（按 seq 升序）——「最后一个未完成 Task」的判定基础。"""
        if self._tasks is None:
            return []
        return [
            row
            for row in self._tasks.list_by_run(run_id)
            if str(row.status) in UNFINISHED_TASK_STATUSES
        ]

    def build_plan(self, run_id: str) -> ResumePlan:
        """重建续跑计划（优先用 `tasks` 表，忠实于崩溃前的计划）。"""
        state = self.replay(run_id)
        goal = self._resolve_goal(run_id, state)
        rows = self._tasks.list_by_run(run_id) if self._tasks is not None else []
        planned: list[PlannedTask] = []
        finished: set[int] = set()
        pending_seq: list[int] = []
        for row in rows:
            seq = int(row.seq)
            planned.append(
                PlannedTask(
                    seq=seq,
                    title=str(row.title),
                    intent=str(row.intent or ""),
                    tools=[],
                    deps=list(row.deps_json or []),
                )
            )
            if str(row.status) in UNFINISHED_TASK_STATUSES:
                pending_seq.append(seq)
            else:
                finished.add(seq)
        pending_seq.sort()
        plan = ResumePlan(
            run_id=run_id,
            goal=goal,
            planned_tasks=planned,
            finished_seqs=finished,
            unfinished_seqs=pending_seq,
            resume_from_seq=pending_seq[0] if pending_seq else None,
            pending_approval_id=state.get("pending_approval_id"),
            state=state,
            source="db_rows",
        )
        if not planned:
            plan.note = "无任务行（崩溃发生在规划完成前）——需重新规划"
        elif plan.resume_from_seq is None:
            plan.note = "全部 Task 已完成，仅需收口"
        return plan

    def reusable_calls(self, run_id: str) -> list[str]:
        """已成功落库的 `dedupe_key`（续跑时会复用、不再打网络的那批）。"""
        if self._tool_calls is None:
            return []
        return [
            str(row.dedupe_key)
            for row in self._tool_calls.list_by_run(run_id)
            if str(row.status) == "OK"
        ]

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _replan_if_empty(self, plan: ResumePlan, engine: Any) -> None:
        """`tasks` 表无行（崩溃/挂起发生在规划落库前）→ **重新规划**再走续跑路径。

        典型场景：计划审批挂起时进程退出——此时计划只在事件与内存里，`tasks` 表还是空的；
        审批通过后必须重规划才能真的把活干完，而不是空转收口。
        """
        if plan.planned_tasks:
            return
        planner = getattr(engine, "planner", None)
        registry = getattr(engine, "registry", None)
        if planner is None or registry is None:
            return
        dag = planner.plan(plan.goal, tools=list(registry.names()), run_id=plan.run_id)
        plan.planned_tasks = list(dag.tasks)
        plan.finished_seqs = set()
        plan.unfinished_seqs = [task.seq for task in dag.tasks]
        plan.resume_from_seq = plan.unfinished_seqs[0] if plan.unfinished_seqs else None
        plan.source = f"replan({dag.source})"
        note = f"tasks 表无行（规划前中断）→ 用 {dag.source} 重新规划 {len(dag.tasks)} 个 Task"
        plan.note = f"{plan.note}；{note}" if plan.note else note

    def _fill_tools_from_planner(self, plan: ResumePlan, engine: Any) -> None:
        """给「未完成 Task」补齐工具清单。

        `tasks` 表没存 `tools`（M0 契约里没有该列），所以未完成 Task 的候选工具必须**重新规划**：
        用同一个 `Planner`（LLM 不可用时走确定性兜底模板）+ `ToolRegistry` 白名单重算一次，
        再按 `seq`（缺失时按标题）匹配回原计划。已完成的 Task 不填——它们本来就会被跳过。
        """
        if plan.resume_from_seq is None:
            return
        planner = getattr(engine, "planner", None)
        registry = getattr(engine, "registry", None)
        if planner is None or registry is None:
            return
        missing = [
            task
            for task in plan.planned_tasks
            if task.seq not in plan.finished_seqs and not task.tools
        ]
        if not missing:  # 工具清单已齐（如 `_replan_if_empty` 刚重规划过）→ 不必再规划一次
            return
        dag = planner.plan(plan.goal, tools=list(registry.names()), run_id=plan.run_id)
        by_seq = {task.seq: task for task in dag.tasks}
        by_title = {task.title: task for task in dag.tasks}
        filled = 0
        for task in plan.planned_tasks:
            if task.seq in plan.finished_seqs:
                continue
            source = by_seq.get(task.seq) or by_title.get(task.title)
            if source is not None:
                task.tools = list(source.tools)
                filled += 1
        if filled:
            plan.source = f"db_rows+planner({dag.source})"
            note = f"用 {dag.source} 重新规划补齐 {filled} 个未完成 Task 的工具清单"
            plan.note = f"{plan.note}；{note}" if plan.note else note

    def _resolve_goal(self, run_id: str, state: dict[str, Any]) -> str:
        if state.get("goal"):
            return str(state["goal"])
        if self._runs is not None and self._threads is not None:
            run = self._runs.get(run_id)
            if run is not None:
                thread = self._threads.get(run.thread_id)
                if thread is not None:
                    return str(thread.goal)
        return ""

    def _emit_resumed(self, run_id: str, plan: ResumePlan) -> None:
        if self._store is None:
            return
        self._store.append(
            run_id,
            (
                str(CheckpointEventType.RunResumed),
                {
                    "from_seq": plan.state.get("last_seq"),
                    "resume_from_seq": plan.resume_from_seq,
                    "finished_seqs": sorted(plan.finished_seqs),
                    "pending": plan.unfinished_seqs,
                },
            ),
        )
