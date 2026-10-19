"""Harness 内核：三段管线（架构 C1/C3 的装配体；03_BUILD_PLAN §3 M1）。

契约（03_BUILD_PLAN §3 M1）::

    Pipeline.run(run_id, goal) -> RunResult

管线（架构 §3.1）::

    [1] PreHook 链（顺序执行，可短路）
         ① ContextInject（M4 实现） ② BudgetCheck ③ GuardPre ④ Plan
    [2] 计划确认（审批属 M3 的 HITLGate，M1 不挂起）
    [3] 主循环：按 Task DAG 推进，每个 Task 一个 AgentLoop 会话
    [4] PostHook 链 —— **无条件执行**（`try/finally` 保障）
         ① EvidencePersist ② TraceFlush ③ MemoryWrite ④ CostSettle ⑤ GuardPost
    [5] 收口：synthesize span + 终态事件

**关键设计**：`[4] PostHook 无条件执行`保证了「LLM 全挂 / 用户中断 / 合规拦截」三种
失败路径下，**证据与审计信息依然完整**（架构 §3.1 的"不得静默失败"机制保障）。

M3 起本管线额外承担四件事（03_BUILD_PLAN §3 M3）：
- **状态迁移经状态机**：不再直接 `run.status = ...`，而是 `state_machine.apply_run()`，
  非法迁移被拒 + 每次迁移落事件（架构 §5.3）；
- **预算与停止规则**：把 `BudgetManager` / `StopState` / `StopRuleSet` **注入主循环**
  （同一组实例，不另建计量器），由 `AgentLoop._judge()` 每轮 ReAct 结束做 **Step 级**判定；
  命中即落 `StopRuleHit`（预算类另落 `BudgetExhausted`）、把 `runs.stop_reason` 落库、
  后续 Task 一律短路（M3 为 Task 级粒度，M4 细化为 Step 级）；
- **审批挂起出口（suspend）**：`await_plan_approval=True` 时，规划完成后经 `HITLGate`
  挂起，`RunResult.status = AWAITING_APPROVAL` 并**不推进任何 Task**（进程可退出）；
- **恢复续跑入口**：`run(..., resume_plan=...)` 跳过已完成 Task，从断点继续。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.compliance import Guard
from app.data.quality import IMPACT_HIGH, IMPACT_MEDIUM, degradation_notice
from app.domain import RunStatus, TaskStatus, now_ms
from app.observ import Tracer, sanitizer_from_settings
from app.store.repo import (
    ApprovalRepository,
    ArtifactRepository,
    CheckpointEventRepository,
    EvidenceRepository,
    LlmCallRepository,
    RunMetricRepository,
    RunRepository,
    TaskRepository,
    UIBlockRepository,
)

from .agent_loop import AgentLoop, LoopContext
from .artifact_builder import ArtifactBuilder
from .budget import BudgetLimits, BudgetManager
from .checkpoint import CheckpointStore
from .hitl import HITLGate
from .hooks import pre as pre_hooks_pkg
from .hooks import post as post_hooks_pkg
from .hooks.base import Hook, HookResult, PipelineContext
from .model_router import ModelRouter
from .planner import Planner, TaskDAG
from .session_manager import SessionManager
from .state_machine import RUN_TERMINAL, IllegalTransition, StateMachine
from .stop_rules import StopRuleSet, StopState
from .tool_executor import ToolExecutor
from .tool_registry import ToolRegistry
from .ui_renderer import UIRenderer

__all__ = ["Pipeline", "RunResult"]


@dataclass(slots=True)
class RunResult:
    """一次 Run 的执行结果（M1 版；M5 追加产物与动态 UI 的收口摘要）。"""

    run_id: str
    status: str
    stop_reason: str | None = None
    plan_source: str | None = None
    task_count: int = 0
    evidence_count: int = 0
    trace_spans: int = 0
    cost_total: float = 0.0
    tokens_total: int = 0
    degraded: bool = False
    blocked_reason: str | None = None
    error: str | None = None
    guard_verdict: str | None = None
    task_statuses: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    #: M3：Run 被审批冻结时给出的 `approval_id`（供前端弹出审批卡片）。
    approval_id: str | None = None
    #: M5：本次 Run 落库的产物 id（三层结构，推断必带切换条件）。
    artifact_id: str | None = None
    #: M5：本次 Run 落库的动态 UI 块数（`ui_blocks`）。
    ui_block_count: int = 0
    #: M5：其中被渲染层降级为 `FallbackJsonCard` 的块数（Trace 记 `render_status=failed`）。
    render_failed_count: int = 0


#: 任务终态 → checkpoint 事件名（架构 §4.2）。
_TASK_EVENT = {
    "DONE": "TaskCompleted",
    "DEGRADED": "TaskDegraded",
    "FAILED": "TaskFailed",
    "SKIPPED": "TaskSkipped",
}

#: Run 终态 → checkpoint 事件名（架构 §4.2）。
_RUN_EVENT = {
    "COMPLETED": "RunCompleted",
    "FAILED": "RunFailed",
    "STOPPED": "RunStopped",
}

#: **阶段搭桥**：架构 §5.1 的状态图要求逐跳迁移（如 `EXECUTING → SYNTHESIZING → COMPLETED`）。
#: 桥接跳只改状态、不落事件（避免为一个内部阶段凭空造事件类型），
#: 因此终态事件仍然只有一条。
_STAGE_BRIDGE: dict[tuple[str, str], str] = {
    (str(RunStatus.CREATED), str(RunStatus.EXECUTING)): str(RunStatus.PLANNING),
    (str(RunStatus.CREATED), str(RunStatus.COMPLETED)): str(RunStatus.PLANNING),
    (str(RunStatus.CREATED), str(RunStatus.STOPPED)): str(RunStatus.PLANNING),
    (str(RunStatus.PLANNING), str(RunStatus.COMPLETED)): str(RunStatus.EXECUTING),
    (str(RunStatus.PLANNING), str(RunStatus.SYNTHESIZING)): str(RunStatus.EXECUTING),
    (str(RunStatus.PLANNING), str(RunStatus.AWAITING_APPROVAL)): str(RunStatus.EXECUTING),
    (str(RunStatus.EXECUTING), str(RunStatus.COMPLETED)): str(RunStatus.SYNTHESIZING),
    (str(RunStatus.AWAITING_APPROVAL), str(RunStatus.COMPLETED)): str(RunStatus.EXECUTING),
    (str(RunStatus.AWAITING_APPROVAL), str(RunStatus.SYNTHESIZING)): str(RunStatus.EXECUTING),
}


class Pipeline:
    """PreHook → AgentLoop → PostHook 的装配与驱动。"""

    def __init__(
        self,
        *,
        session: Session | None = None,
        settings: Settings | None = None,
        registry: ToolRegistry | None = None,
        model_router: ModelRouter | None = None,
        tool_executor: ToolExecutor | None = None,
        tracer: Tracer | None = None,
        session_manager: SessionManager | None = None,
        planner: Planner | None = None,
        agent_loop: AgentLoop | None = None,
        pre_hooks: list[Hook] | None = None,
        post_hooks: list[Hook] | None = None,
        state_machine: StateMachine | None = None,
        checkpoint_store: CheckpointStore | None = None,
        hitl: HITLGate | None = None,
        budget: BudgetManager | None = None,
        stop_rules: StopRuleSet | None = None,
        await_plan_approval: bool = False,
        artifact_builder: ArtifactBuilder | None = None,
        ui_renderer: UIRenderer | None = None,
        guard: Guard | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()

        self.registry = registry if registry is not None else self._default_registry(session)
        self.model_router = model_router or ModelRouter(session, settings=self.settings)
        # M6：Trace 落库开关（`TRACE_ENABLED`）+ 落库前脱敏钩子（S2：Key / 隐私）。
        self.tracer = tracer or Tracer(
            session,
            enabled=bool(getattr(self.settings, "trace_enabled", True)),
            sanitize=sanitizer_from_settings(self.settings).mask_any,
        )
        self.tool_executor = tool_executor or ToolExecutor(
            self.registry, session=session, settings=self.settings, tracer=self.tracer
        )
        self.planner = planner or Planner(self.model_router)
        self.agent_loop = agent_loop or AgentLoop(
            self.model_router, self.tool_executor, session=session, tracer=self.tracer
        )
        self.session_manager = session_manager or SessionManager(session)

        self.pre_hooks = list(pre_hooks) if pre_hooks is not None else pre_hooks_pkg.default_pre_hooks()
        self.post_hooks = (
            list(post_hooks) if post_hooks is not None else post_hooks_pkg.default_post_hooks()
        )

        self._runs = RunRepository(session) if session is not None else None
        self._tasks = TaskRepository(session) if session is not None else None
        self._checkpoints = CheckpointEventRepository(session) if session is not None else None
        self._llm_calls = LlmCallRepository(session) if session is not None else None
        # --- M5：产物与动态 UI 的落点（C17/C18/C19）---
        self._evidences = EvidenceRepository(session) if session is not None else None
        self._artifacts = ArtifactRepository(session) if session is not None else None
        self._ui_blocks = UIBlockRepository(session) if session is not None else None
        self._approvals = ApprovalRepository(session) if session is not None else None
        #: M6：运行期指标落点（看板数据；`replace_for_run` 保证幂等）。
        self._run_metrics = RunMetricRepository(session) if session is not None else None

        # --- M3：状态机 / 检查点 / 审批 / 预算 / 停止规则 ---
        self.state_machine = state_machine or StateMachine(session)
        self.checkpoint_store = checkpoint_store or (
            CheckpointStore(session) if session is not None else None
        )
        self.hitl = hitl or (
            HITLGate(
                session,
                settings=self.settings,
                checkpoints=self.checkpoint_store,
                state_machine=self.state_machine,
            )
            if session is not None
            else None
        )
        self.budget = budget or BudgetManager(limits=BudgetLimits.from_settings(self.settings))
        self.stop_rules = stop_rules or StopRuleSet()
        self.stop_state = StopState.from_settings(self.settings, budget=self.budget)
        #: 规划完成后是否走人工审批（默认关；开启后 Run 会在 `AWAITING_APPROVAL` 挂起）。
        self.await_plan_approval = bool(await_plan_approval)

        # --- M5：产物构建器（C18）与动态 UI 渲染器（C19）---
        self.artifact_builder = artifact_builder or ArtifactBuilder(
            settings=self.settings, tracer=self.tracer
        )
        self.ui_renderer = ui_renderer or UIRenderer(
            settings=self.settings, tracer=self.tracer
        )
        # --- M6：产物层合规守卫（与 C19 渲染层拦截构成双保险，架构 §11.3-2）---
        self.guard = guard if guard is not None else Guard(
            settings=self.settings, tracer=self.tracer
        )

    # ------------------------------------------------------------------ #
    # 契约入口
    # ------------------------------------------------------------------ #
    @staticmethod
    def _default_registry(session: Session | None) -> ToolRegistry:
        """未注入注册表时的缺省装配：**10 个受控工具**（9 数据 + 1 内部 COMMIT）。

        由 `app/tools.build_tools` 统一注册 `ToolSpec` 并绑定 `impl` / iFinD 备源；
        延迟导入是因为 `app.tools` 反向依赖 `app.harness`（模块级导入会形成循环）。
        """
        from app.tools import build_tools

        return build_tools(ToolRegistry(session))

    def run(self, run_id: str, goal: str, *, resume_plan: Any = None) -> RunResult:
        """驱动一次完整 Run。**任何异常都会走完 PostHook 链**。

        `resume_plan`（M3）：由 `ResumeEngine` 传入的续跑计划；非 `None` 时**跳过 PreHook**
        （规划与护栏在崩溃前已跑过），直接沿用原计划、跳过已完成 Task 续跑。
        """
        run = self._runs.get(run_id) if self._runs is not None else None
        self.tracer.bind_run(run_id)
        if run is not None:
            run.started_at = run.started_at or now_ms()
            entry = RunStatus.PLANNING if resume_plan is None else RunStatus.EXECUTING
            self._set_run_status(
                run_id,
                entry,
                reason="开始规划" if resume_plan is None else "恢复续跑",
                trigger="pipeline" if resume_plan is None else "resume",
                extra={"goal": goal},
            )
        else:
            self._append(run_id, "RunStarted", {"goal": goal})

        # 预算注入：让 M1 预留的 BudgetCheckHook 正式生效（M3 接管）
        self.budget.reset()
        self.stop_state = StopState.from_settings(self.settings, budget=self.budget)

        loop_ctx = LoopContext(run_id=run_id, goal=goal, registry=self.registry, tracer=self.tracer)
        ctx = PipelineContext(
            run_id=run_id,
            goal=goal,
            thread_id=run.thread_id if run is not None else None,
            session=self.session,
            settings=self.settings,
            registry=self.registry,
            model_router=self.model_router,
            tool_executor=self.tool_executor,
            planner=self.planner,
            agent_loop=self.agent_loop,
            tracer=self.tracer,
            loop_ctx=loop_ctx,
        )
        ctx.extra["budget"] = self.budget.to_hook_budget()
        # 证据单一事实来源：主循环（LoopContext）与 Hook（PipelineContext）共享同一列表，
        # 否则 PostHook 的 EvidencePersist 看不到主循环产出的证据。
        ctx.evidences = loop_ctx.evidences
        # M4：把**同一组**计量器 / 停止规则注入主循环（HANDOFF §17.5「不要另建计量器」），
        # 由 `AgentLoop._judge()` 在每轮 ReAct 结束时做 Step 级判定；
        # `llm_totals` 的唯一数据来源是 `llm_calls` 聚合（与预算口径一致）。
        loop_ctx.budget = self.budget
        loop_ctx.stop_state = self.stop_state
        loop_ctx.stop_rules = self.stop_rules
        loop_ctx.llm_totals = lambda: self._sync_llm_totals(ctx)
        loop_ctx.started_ms = now_ms()

        status = "COMPLETED"
        error: str | None = None
        approval_id: str | None = None

        try:
            if resume_plan is not None:
                status = self._resume_from(ctx, loop_ctx, resume_plan)
                if status == "AWAITING_APPROVAL":
                    approval_id = resume_plan.pending_approval_id
            else:
                self._run_pre_hooks(ctx)
                if ctx.blocked:
                    status = "STOPPED"
                    ctx.stop_reason = "GUARD_REJECT"
                    self._append(run_id, "GuardRejected", {"reason": ctx.block_reason})
                    self._append(run_id, "StopRuleHit", {"reason": ctx.stop_reason})
                elif ctx.plan is None:
                    # 终态事件由 `_set_run_status` 经状态机统一落（见 finally）
                    status = "FAILED"
                    error = "规划失败：PreHook 未产出计划"
                elif self.await_plan_approval:
                    approval_id = self._suspend_for_plan_approval(ctx)
                    status = "AWAITING_APPROVAL" if approval_id else "COMPLETED"
                else:
                    self._execute_tasks(ctx, loop_ctx)
                    # M4：Step 级停止规则命中 → Run 以 STOPPED 收口（HANDOFF §17.5
                    # 「停止规则命中后……Run 以 STOPPED 收口」；未命中时保持 COMPLETED）。
                    if ctx.stop_reason:
                        status = "STOPPED"
        except Exception as exc:  # 不可恢复错误 → FAILED（但 PostHook 仍会执行）
            status = "FAILED"
            error = f"{type(exc).__name__}: {exc}"
            ctx.notes.append(f"管线异常：{error}")
        finally:
            # [4] PostHook 链：无条件执行（try/finally 保障）
            try:
                self._run_post_hooks(ctx)
            except Exception as exc:
                ctx.notes.append(f"PostHook 异常：{type(exc).__name__}: {exc}")
                if status == "COMPLETED":
                    status = "FAILED"
                    error = error or f"PostHook 失败：{exc}"

            if status == "COMPLETED" and not ctx.tasks:
                status = "FAILED"
                error = error or "无任务可执行"

            if run is not None and status == "AWAITING_APPROVAL":
                # 冻结：Run 停在 AWAITING_APPROVAL（由 HITLGate 落库），不收口
                run.stop_reason = None
                self.session.flush()  # type: ignore[union-attr]
            else:
                # 终态迁移 + 终态事件：唯一出处（架构 §5.3「每次迁移落事件」）
                self._set_run_status(
                    run_id,
                    status,
                    reason=ctx.stop_reason or error,
                    trigger="pipeline",
                    extra={
                        "tasks": len(ctx.tasks),
                        "evidences": len(ctx.evidences),
                        "cost": ctx.cost_total,
                        "error": error,
                    },
                )
                if run is not None:
                    run.error = error
                    self.session.flush()  # type: ignore[union-attr]

            # [5] 收口产物与动态 UI（M5）：状态已定稿后落 `artifacts` + `ui_blocks`。
            # 审批冻结（AWAITING_APPROVAL）时不建产物（尚未取证），只落 plan/approval 块。
            try:
                self._finalize_outputs(ctx, build_artifact=status != "AWAITING_APPROVAL")
            except Exception as exc:  # 产物收口失败不得吞掉 Run 的既有结论
                ctx.notes.append(f"产物/UI 收口异常：{type(exc).__name__}: {exc}")

        return RunResult(
            run_id=run_id,
            status=status,
            stop_reason=ctx.stop_reason,
            plan_source=ctx.plan.source if ctx.plan else None,
            task_count=len(ctx.tasks),
            evidence_count=len(ctx.persisted_evidence_ids),
            trace_spans=ctx.trace_spans,
            cost_total=ctx.cost_total,
            tokens_total=ctx.tokens_total,
            degraded=bool(ctx.plan and ctx.plan.degraded)
            or any(s != "DONE" for s in ctx.task_statuses.values()),
            blocked_reason=ctx.block_reason,
            error=error,
            guard_verdict=ctx.extra.get("guard_post_verdict"),
            task_statuses=dict(ctx.task_statuses),
            notes=list(ctx.notes),
            approval_id=approval_id,
            artifact_id=ctx.extra.get("artifact_id"),
            ui_block_count=int(ctx.extra.get("ui_block_count") or 0),
            render_failed_count=int(ctx.extra.get("render_failed_count") or 0),
        )

    # ------------------------------------------------------------------ #
    # M3：恢复 / 挂起
    # ------------------------------------------------------------------ #
    def _resume_from(self, ctx: PipelineContext, loop_ctx: LoopContext, plan: Any) -> str:
        """按 `ResumePlan` 续跑：沿用原计划、跳过已完成 Task。"""
        ctx.plan = TaskDAG(goal=plan.goal, tasks=list(plan.planned_tasks), source="resume")
        ctx.tasks = ctx.plan.tasks
        ctx.notes.append(
            f"恢复续跑：跳过 {len(plan.finished_seqs)} 个已完成 Task，"
            f"从 seq={plan.resume_from_seq} 继续（计划来源 {plan.source}）"
        )
        if plan.resume_from_seq is None:
            self._append(ctx.run_id, "RunResumed", {"noop": True, "reason": "全部 Task 已完成"})
            with self.tracer.span("synthesize", evidences_used=len(ctx.evidences)) as span:
                span.set(model=self.settings.llm_model_primary or "")
            return "COMPLETED"
        self._execute_tasks(ctx, loop_ctx, skip_seqs=plan.finished_seqs)
        return "STOPPED" if ctx.stop_reason else "COMPLETED"

    def _suspend_for_plan_approval(self, ctx: PipelineContext) -> str | None:
        """规划完成后挂起等待人工审批（架构 §6.1 第 2 类中断）。

        架构 §5.1 的挂起边只有 `EXECUTING ⇄ AWAITING_APPROVAL`，故必须先按状态图进入
        `EXECUTING`（`PLANNING → EXECUTING` 是合法直边），再挂起——否则 `HITLGate.request`
        会因表外迁移 `PLANNING → AWAITING_APPROVAL` 抛 `IllegalTransition`。
        """
        if self.hitl is None:
            return None
        self._set_run_status(
            ctx.run_id, RunStatus.EXECUTING, reason="计划已生成，进入执行阶段", trigger="pipeline"
        )
        approval_id = self.hitl.request(
            ctx.run_id,
            "plan",
            {
                "goal": ctx.goal,
                "source": ctx.plan.source if ctx.plan else None,
                "tasks": [
                    {"seq": t.seq, "title": t.title, "tools": list(t.tools)}
                    for t in (ctx.plan.tasks if ctx.plan else [])
                ],
            },
            reason="计划待人工审批",
        )
        ctx.notes.append(f"审批挂起：approval_id={approval_id}（冻结 Loop，不推进任何 Task）")
        return approval_id

    # ------------------------------------------------------------------ #
    # [1] PreHook
    # ------------------------------------------------------------------ #
    def _run_pre_hooks(self, ctx: PipelineContext) -> None:
        for hook in self.pre_hooks:
            result = self._guard(hook, ctx)
            if not result.ok or result.short_circuit:
                if not ctx.block_reason:
                    ctx.block_reason = result.reason
                ctx.blocked = True
                return
            if result.reason:
                ctx.notes.append(f"[{hook.name}] {result.reason}")

    # ------------------------------------------------------------------ #
    # [3] 主循环
    # ------------------------------------------------------------------ #
    def _execute_tasks(
        self,
        ctx: PipelineContext,
        loop_ctx: LoopContext,
        *,
        skip_seqs: Any = frozenset(),
    ) -> None:
        """按 Task DAG 推进；`skip_seqs` 内的 Task 视为已完成（恢复续跑）。"""
        assert ctx.plan is not None
        # M4 上下文 → 主循环：把记忆召回回填给 Reasoner（ContextInjectHook 已在 PreHook 跑过）。
        bundle = ctx.extra.get("context_bundle")
        loop_ctx.memory_lines = list(getattr(bundle, "memory_lines", None) or [])
        if not skip_seqs:
            self._append(
                ctx.run_id,
                "PlanGenerated",
                {
                    "source": ctx.plan.source,
                    "tasks": ctx.plan.task_count,
                    "tools": ctx.plan.tool_names,
                    "plan": [
                        {"seq": t.seq, "title": t.title, "tools": list(t.tools), "deps": list(t.deps)}
                        for t in ctx.plan.tasks
                    ],
                },
            )
        self._set_run_status(ctx.run_id, RunStatus.EXECUTING, reason="开始执行", trigger="pipeline")

        for planned in ctx.plan.tasks:
            if planned.seq in skip_seqs:
                ctx.notes.append(f"任务#{planned.seq} 已完成，跳过（恢复续跑）")
                continue
            if ctx.stop_reason:
                ctx.notes.append(f"任务#{planned.seq} 未执行：Run 已命中停止规则 {ctx.stop_reason}")
                break

            task_row = self._ensure_task(ctx, planned)
            # 架构 §5.2 逐跳迁移：PENDING → READY（阶段标记，不落事件）→ RUNNING（TaskStarted）
            if str(task_row.status) == str(TaskStatus.PENDING):
                self._state_task(task_row, TaskStatus.READY, reason="依赖就绪")
            self._state_task(task_row, TaskStatus.RUNNING, reason="开始执行任务",
                             extra={"seq": planned.seq, "title": planned.title})
            with self.tracer.span(
                f"task#{planned.seq}",
                title=planned.title,
                deps=planned.deps,
                tools=planned.tools,
            ) as span:
                outcome = self.agent_loop.run_task(task_row, loop_ctx, tools=planned.tools)
                span.set(
                    status=outcome.status,
                    rounds=outcome.rounds,
                    evidence=len(outcome.evidence_ids),
                )

            self._finish_task(ctx, task_row, outcome)
            # 每个 Task 结束即落证据：M3 的幂等恢复依赖 evidences 留档
            self._persist_evidence(ctx)
            # 同步四维预算（token/成本唯一来源是 `llm_calls`），并收口 Step 级命中的规则
            self._sync_llm_totals(ctx)
            ctx.extra["steps_used"] = self.budget.used.steps
            self._collect_stop(ctx, loop_ctx, planned)

        # [5] 收口
        with self.tracer.span("synthesize", evidences_used=len(ctx.evidences)) as span:
            span.set(model=self.settings.llm_model_primary or "")

    # ------------------------------------------------------------------ #
    # M3/M4：预算同步 / 停止规则收口
    # ------------------------------------------------------------------ #
    def _sync_llm_totals(self, ctx: PipelineContext) -> tuple[float, int]:
        """把本 Run 的 token / 成本从 `llm_calls` 聚合同步进 `ctx`，并回传给主循环计量。

        **唯一数据来源是 `llm_calls`**（HANDOFF §8.2）：主循环 `_judge()` 每轮调它，
        `charge_llm()` 收的是累计绝对值，因此重复调用安全。
        """
        if self._llm_calls is None:
            return ctx.cost_total, ctx.tokens_total
        cost, tokens = self._llm_calls.totals_for_run(ctx.run_id)
        ctx.cost_total, ctx.tokens_total = cost, tokens
        return cost, tokens

    def _collect_stop(self, ctx: PipelineContext, loop_ctx: LoopContext, planned: Any) -> None:
        """收口主循环 Step 级命中的停止规则：写 `ctx.stop_reason` + 落 `StopRuleHit`。

        Step 级计量的推进发生在 `AgentLoop._judge()`（每轮 ReAct 一次）；本方法**不重复计量**，
        只负责把命中结果落成事件，并让随后 Task 的循环因 `ctx.stop_reason` 而短路。
        """
        if ctx.stop_reason or not loop_ctx.stop_reason:
            return
        ctx.stop_reason = loop_ctx.stop_reason
        ctx.notes.append(
            f"命中停止规则 {loop_ctx.stop_rule}（{ctx.stop_reason}）："
            f"steps={self.budget.used.steps} tokens={self.budget.used.tokens} "
            f"cost={self.budget.used.cost:.4f} wall={self.budget.used.wall_time_s:.1f}s "
            f"repeat={self.stop_state.repeat_count} no_progress={self.stop_state.no_progress_streak}"
        )
        self._append(
            ctx.run_id,
            "StopRuleHit",
            {
                "reason": ctx.stop_reason,
                "rule": loop_ctx.stop_rule,
                "task_id": getattr(planned, "seq", None),
                "budget": self.budget.snapshot(),
            },
        )
        if ctx.stop_reason in {"MAX_TOKENS", "MAX_COST", "MAX_WALL_TIME"}:
            self._append(ctx.run_id, "BudgetExhausted", {"reason": ctx.stop_reason})

    def _ensure_task(self, ctx: PipelineContext, planned: Any) -> Any:
        """取或建 Task 行——续跑时必须**复用**已存在的行，否则会造重复 Task。"""
        if self._tasks is not None:
            for row in self._tasks.list_by_run(ctx.run_id):
                if int(row.seq) == int(planned.seq):
                    row.title = planned.title
                    row.intent = planned.intent
                    row.deps_json = list(planned.deps)
                    self.session.flush()  # type: ignore[union-attr]
                    ctx.task_rows.append(row)
                    return row
            row = self._tasks.create(
                run_id=ctx.run_id,
                seq=planned.seq,
                title=planned.title,
                intent=planned.intent,
                status=str(TaskStatus.PENDING),
                deps_json=list(planned.deps),
            )
        else:  # 无 session 时构造一个内存行（纯内存场景）
            from app.store.schema import TaskRow

            row = TaskRow(
                id=f"mem-task-{planned.seq}",
                run_id=ctx.run_id,
                seq=planned.seq,
                title=planned.title,
                intent=planned.intent,
                status=str(TaskStatus.PENDING),
                deps_json=list(planned.deps),
                attempts=0,
                evidence_count=0,
            )
        ctx.task_rows.append(row)
        return row

    def _state_task(
        self,
        task_row: Any,
        to_status: TaskStatus,
        *,
        reason: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Task 状态迁移（经状态机：校验 + 落事件）。"""
        self.state_machine.apply_task(task_row, to_status, reason=reason, trigger="pipeline",
                                      extra=extra)

    def _finish_task(self, ctx: PipelineContext, task_row: Any, outcome: Any) -> None:
        task_row.attempts = outcome.rounds
        task_row.evidence_count = len(outcome.evidence_ids)
        try:
            to_status = TaskStatus(outcome.status)
        except ValueError:
            to_status = TaskStatus.FAILED
        self._state_task(
            task_row,
            to_status,
            reason=outcome.degradation_reason,
            extra={
                "rounds": outcome.rounds,
                "evidences": len(outcome.evidence_ids),
                "reason": outcome.degradation_reason,
            },
        )
        ctx.task_statuses[task_row.id] = str(outcome.status)

    # ------------------------------------------------------------------ #
    # [4] PostHook（无条件执行）
    # ------------------------------------------------------------------ #
    def _run_post_hooks(self, ctx: PipelineContext) -> None:
        for hook in self.post_hooks:
            result = self._guard(hook, ctx)
            if result.reason:
                ctx.notes.append(f"[{hook.name}] {result.reason}")

    def _persist_evidence(self, ctx: PipelineContext) -> None:
        """每个 Task 后单独跑 EvidencePersist（幂等，`finally` 中还会再跑一次）。"""
        for hook in self.post_hooks:
            if hook.name == "evidence_persist":
                result = self._guard(hook, ctx)
                if result.reason:
                    ctx.notes.append(f"[{hook.name}] {result.reason}")
                return

    # ------------------------------------------------------------------ #
    # M5：产物与动态 UI 收口（C18 → C19）
    # ------------------------------------------------------------------ #
    def _finalize_outputs(self, ctx: PipelineContext, *, build_artifact: bool = True) -> None:
        """收口三件事：①建 `Artifact` 落 `artifacts`；②块描述经渲染器校验落 `ui_blocks`。

        渲染规则（架构 §11.2 / 03_BUILD_PLAN §3 M5 原文）由 `UIRenderer` 执行：
        `组件 ∈ 白名单(12) ∩ 已实现(7)`，否则整块降级为 `FallbackJsonCard` 并在 Trace 记
        `render_status=failed`。本方法只负责**供给块描述**与**落库**，不做校验。
        """
        built: Any = None
        if build_artifact:
            built = self.artifact_builder.build_from(
                ctx.run_id,
                goal=ctx.goal,
                evidences=list(ctx.evidences),
                payload=self._artifact_payload(ctx),
                version=self._next_artifact_version(ctx.run_id),
                with_detail=True,
            )
            ctx.extra["artifact_id"] = built.artifact.id
            if self._artifacts is not None:
                self._artifacts.create(
                    run_id=ctx.run_id,
                    artifact_id=built.artifact.id,
                    version=built.artifact.version,
                    facts_json=built.facts,
                    inferences_json=built.inferences,
                    uncertainties_json=built.uncertainties,
                    citations_json=built.citations,
                    created_at=built.artifact.created_at,
                )
            # `ArtifactBuilt` 是 M0 冻结的 26 类事件之一，此前无人落——M5 由 C18 补上，
            # 作为 SSE `artifact` 事件的唯一载体（不新增事件类型）。
            self._append(
                ctx.run_id,
                "ArtifactBuilt",
                {
                    "artifact_id": built.artifact.id,
                    "version": built.artifact.version,
                    "facts": len(built.facts),
                    "inferences": len(built.inferences),
                    "uncertainties": len(built.uncertainties),
                    "citations": len(built.citations),
                },
            )
            # M6：产物层合规检查（与 C19 渲染层双保险，架构 §11.3-2）。
            self._guard_artifact(ctx, built)

        descriptors = self._ui_descriptors(ctx, built)
        if not descriptors:
            ctx.extra.setdefault("ui_block_count", 0)
            ctx.extra.setdefault("render_failed_count", 0)
            self._write_run_metrics(ctx, [])
            return

        start_seq = self._ui_blocks.next_seq(ctx.run_id) if self._ui_blocks is not None else 1
        blocks = self.ui_renderer.render(descriptors, run_id=ctx.run_id, start_seq=start_seq)
        for block in blocks:
            if self._ui_blocks is not None:
                self._ui_blocks.create(
                    run_id=ctx.run_id,
                    source_type=str(block.source_type),
                    component=block.component,
                    props_json=block.props_json,
                    refs_json=block.refs_json,
                    render_status=block.render_status,
                    seq=block.seq,
                    block_id=block.id,
                    created_at=block.created_at,
                )
        failed = sum(1 for block in blocks if block.render_status != "ok")
        ctx.extra["ui_block_count"] = len(blocks)
        ctx.extra["render_failed_count"] = failed
        ctx.notes.append(
            f"产物与动态 UI 收口：artifact={ctx.extra.get('artifact_id') or '（未建）'}，"
            f"UIBlock {len(blocks)} 个（降级 {failed} 个）"
        )
        self._write_run_metrics(ctx, blocks)

    def _guard_artifact(self, ctx: PipelineContext, built: Any) -> None:
        """M6：产物层合规检查（三类检查见 `compliance/guard.py`）。

        与 C19 渲染层的「无引用 FACT 拦截」构成**双保险**（架构 §11.3-2）：
        C19 拦的是「能不能渲染成 UIBlock」，这里拦的是「产物本身能不能发布」。

        命中即落 `GuardRejected`（26 类事件之一，此前仅 PreHook 使用）并把判定写进
        `ctx.extra`，最终由 `RunResult.guard_verdict` 对外暴露——这就是「拦截并记录」。
        """
        if self.guard is None:
            return
        result = self.guard.inspect(built.artifact)
        ctx.extra["guard_result"] = result.to_dict()
        if self.tracer is not None:
            with self.tracer.span(
                "guard",
                verdict=str(result.verdict),
                findings=len(result.findings),
                artifact_id=built.artifact.id,
            ):
                pass
        if not result.rejected:
            return
        ctx.extra["guard_post_verdict"] = f"REJECTED（产物层 Guard：{result.summary()}）"
        ctx.extra["guard_blocked"] = True
        self._append(
            ctx.run_id,
            "GuardRejected",
            {
                "artifact_id": built.artifact.id,
                "stage": "artifact",
                "categories": result.categories,
                "summary": result.summary(),
            },
        )
        ctx.notes.append(f"产物层合规拦截：{result.summary()}")

    def _write_run_metrics(self, ctx: PipelineContext, blocks: list[Any]) -> None:
        """M6：落运行期指标（`run_metrics`，供看板；`replace_for_run` 保证幂等）。"""
        if self._run_metrics is None:
            return
        spans = list(getattr(self.tracer, "spans", []) or [])
        tool_calls = sum(1 for span in spans if getattr(span, "name", "") == "tool")
        guard_result = dict(ctx.extra.get("guard_result") or {})
        metrics: list[tuple[str, float, str | None]] = [
            ("evidence_count", float(len(ctx.persisted_evidence_ids)), "count"),
            ("ui_block_count", float(len(blocks)), "count"),
            ("render_failed_count", float(ctx.extra.get("render_failed_count") or 0), "count"),
            ("trace_spans", float(ctx.trace_spans), "count"),
            ("tool_calls", float(tool_calls), "count"),
            (
                "degraded_tasks",
                float(sum(1 for status in ctx.task_statuses.values() if status != "DONE")),
                "count",
            ),
            ("guard_findings", float(len(guard_result.get("findings") or [])), "count"),
            ("cost_cny", float(ctx.cost_total), "CNY"),
            ("tokens_total", float(ctx.tokens_total), "token"),
        ]
        self._run_metrics.replace_for_run(ctx.run_id, metrics)

    def _next_artifact_version(self, run_id: str) -> int:
        """重跑时版本递增（`artifacts.version` 唯一语义是「同一 Run 的第几版产物」）。"""
        if self._artifacts is None:
            return 1
        return len(self._artifacts.list_by_run(run_id)) + 1

    @staticmethod
    def _artifact_payload(ctx: PipelineContext) -> dict[str, Any] | None:
        """取 `emit_artifact` 交回的三层载荷（`ctx.extra` 优先，否则从证据回溯）。

        `emit_artifact` 是内部 COMMIT 工具，其 `ToolResult.data` 会被 `AgentLoop._observe`
        原样写成一条 `Evidence`（`interface = internal://emit_artifact`），故可从证据回溯。
        """
        payload = ctx.extra.get("artifact_payload")
        if isinstance(payload, dict):
            return payload
        for evidence in reversed(ctx.evidences):
            interface = str(getattr(evidence, "interface", "") or "")
            value = getattr(evidence, "value_json", None)
            if "emit_artifact" in interface and isinstance(value, dict):
                return value
        return None

    def _ui_descriptors(self, ctx: PipelineContext, built: Any) -> list[dict[str, Any]]:
        """把本次 Run 的「计划 / 证据 / 产物 / 审批 / 降级」供给为块描述（未校验）。"""
        items: list[dict[str, Any]] = []

        # ① 计划卡（有任务才出；空计划退化为澄清问题）
        if ctx.plan is not None and ctx.plan.tasks:
            items.append(
                {
                    "component": "plan_card",
                    "props_json": {
                        "goal": ctx.goal,
                        "source": ctx.plan.source,
                        "tasks": [
                            {
                                "seq": task.seq,
                                "title": task.title,
                                "intent": task.intent,
                                "tools": list(task.tools),
                                "deps": list(task.deps),
                            }
                            for task in ctx.plan.tasks
                        ],
                    },
                }
            )
        elif not ctx.evidences:
            items.append(
                {
                    "component": "clarify_question",
                    "props_json": {
                        "questions": [
                            {
                                "question": (
                                    f"目标「{ctx.goal}」未能拆解为可执行任务，"
                                    "请补充标的 / 时间范围 / 关注维度后重试。"
                                ),
                                "kind": "scope",
                            }
                        ]
                    },
                }
            )

        # ② 证据：总表（可点）+ 逐条卡片（来源 / 时点 / 单位 / 口径 / 值）
        evidence_ids = [str(ev.id) for ev in ctx.evidences if getattr(ev, "id", None)]
        if evidence_ids:
            items.append(
                {
                    "component": "evidence_table",
                    "props_json": {"evidence_ids": evidence_ids},
                    "refs_json": evidence_ids,
                }
            )
            for evidence in ctx.evidences:
                if not getattr(evidence, "id", None):
                    continue
                items.append(
                    {
                        "component": "evidence_card",
                        "props_json": {
                            "evidence_id": str(evidence.id),
                            "source": evidence.source,
                            "as_of": evidence.as_of,
                            "unit": evidence.unit,
                            "caliber": evidence.caliber,
                            "value": evidence.value_json,
                        },
                        "refs_json": [str(evidence.id)],
                    }
                )

        # ③ 结论卡：三层各一条（FACT 的 `refs` 由 C18 保证非空；空的由渲染层拦下）
        if built is not None:
            for fact in built.facts:
                items.append(
                    self._conclusion("FACT", fact.get("text"), fact.get("confidence"),
                                     fact.get("refs") or [])
                )
            for inference in built.inferences:
                items.append(
                    self._conclusion("INFERENCE", inference.get("text"),
                                     inference.get("confidence"), inference.get("refs") or [],
                                     condition=inference.get("condition"))
                )
            for uncertainty in built.uncertainties:
                items.append(
                    self._conclusion("UNCERTAIN", uncertainty.get("text"), None,
                                     uncertainty.get("refs") or [])
                )

        # ④ 降级通知：降级证据（medium）+ 降级任务（high）——C4 不得静默
        for evidence in ctx.evidences:
            reason = getattr(evidence, "degrade_reason", None)
            if reason:
                items.append(
                    degradation_notice(
                        str(reason),
                        scope=str(getattr(evidence, "interface", "") or "evidence"),
                        impact=IMPACT_MEDIUM,
                    )
                )
        for row in ctx.task_rows:
            reason = getattr(row, "degradation_reason", None)
            if reason:
                items.append(
                    degradation_notice(
                        str(reason),
                        scope=f"task#{getattr(row, 'seq', '?')}",
                        impact=IMPACT_HIGH,
                    )
                )

        # ⑤ 审批卡（仅当有挂起审批；归属 M5，逻辑来自 M3 的 HITLGate）
        if self._approvals is not None:
            approval = self._approvals.latest_pending(ctx.run_id)
            if approval is not None:
                payload = dict(getattr(approval, "payload_json", None) or {})
                items.append(
                    {
                        "component": "approval_card",
                        "props_json": {
                            "approval_id": approval.id,
                            "type": approval.type,
                            "summary": str(
                                payload.get("summary")
                                or f"{approval.type} 待人工审批：{payload.get('goal') or ctx.goal}"
                            ),
                        },
                    }
                )
        return items

    @staticmethod
    def _conclusion(
        layer: str,
        text: Any,
        confidence: Any,
        refs: list[Any],
        *,
        condition: str | None = None,
    ) -> dict[str, Any]:
        """构造一张 `conclusion_card` 块描述（`layer` 强制分层，推断附切换条件）。"""
        props: dict[str, Any] = {
            "layer": layer,
            "text": str(text or "").strip() or "（空结论）",
            "confidence": confidence,
            "refs": [str(r) for r in refs],
        }
        if condition:
            props["condition"] = condition
        return {"component": "conclusion_card", "props_json": props, "refs_json": list(props["refs"])}

    # ------------------------------------------------------------------ #
    # 辅助
    # ------------------------------------------------------------------ #
    def _guard(self, hook: Hook, ctx: PipelineContext) -> HookResult:
        """执行单个 Hook：Hook 内部异常**不吞掉**，转为记录并放行（不静默失败）。"""
        try:
            return hook.run(ctx)
        except Exception as exc:
            ctx.notes.append(f"Hook {hook.name} 异常：{type(exc).__name__}: {exc}")
            return HookResult.skip(f"Hook {hook.name} 异常：{exc}")

    def _set_run_status(
        self,
        run_id: str,
        to_status: Any,
        *,
        reason: str | None = None,
        trigger: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> Any:
        """把 Run 迁到 `to_status`：**校验 → 必要时阶段搭桥 → 落库落事件**。

        架构 §5.1 要求逐跳迁移，而管线只关心「当前阶段 → 目标阶段」；因此这里对
        「跨阶段的合法目标」（如 `PLANNING → COMPLETED`）按 `_STAGE_BRIDGE` **补中间跳**，
        中间跳只改状态不落事件（`emit=False`），终态事件因此仍只有一条。

        降级：无 Run 行（纯内存场景）时退化为直接落事件；表外迁移则拒绝并退化为
        「写状态 + 落终态事件」，绝不让 `finally` 抛出而丢掉收口。
        """
        to = str(to_status)
        run = self._runs.get(run_id) if self._runs is not None else None
        if run is None:
            event = _RUN_EVENT.get(to)
            if event is not None:
                self._append(run_id, event, {"status": to, "reason": reason, **(extra or {})})
            return None

        hops = 0
        while not self.state_machine.can_transition_run(str(run.status), to) and hops < 8:
            bridge = _STAGE_BRIDGE.get((str(run.status), to))
            if bridge is None:
                break
            self.state_machine.apply_run(
                run, bridge, reason="阶段搭桥（不落事件）", trigger=trigger, emit=False
            )
            hops += 1

        try:
            return self.state_machine.apply_run(
                run, to, reason=reason, trigger=trigger, extra=extra
            )
        except IllegalTransition as exc:  # 表外迁移：拒绝推进但保证收口不丢
            run.status = to
            if RunStatus(to) in RUN_TERMINAL:
                run.ended_at = run.ended_at or now_ms()
            if self.session is not None:
                self.session.flush()
            event = _RUN_EVENT.get(to)
            if event is not None:
                self._append(
                    run_id,
                    event,
                    {"status": to, "reason": str(exc), "illegal_from": str(run.status),
                     **(extra or {})},
                )
            return None

    def _append(self, run_id: str, event_type: str, payload: dict[str, Any]) -> None:
        if self._checkpoints is None:
            return
        self._checkpoints.append(run_id, event_type, payload)
