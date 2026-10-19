"""Harness 内核（03_BUILD_PLAN §3 M1）：项目的心脏。

契约（03_BUILD_PLAN §3 M1 接口契约）::

    Pipeline.run(run_id, goal) -> RunResult
    ToolRegistry.register(spec) / list() / get(name)
    ToolExecutor.invoke(run_id, task_id, call) -> ToolResult | DegradedResult
    AgentLoop.iterate(task, ctx) -> Step
    ModelRouter.complete(role, messages, schema) -> LLMResult

管线（架构 §3.1）::

    PreHook → AgentLoop（ReAct）→ PostHook（**无条件执行**）

**内核自研**（ADR-1）：不依赖 LangGraph 等外部编排框架。
"""

from __future__ import annotations

from .agent_loop import AgentLoop, LoopContext, TaskOutcome
from .artifact_builder import ArtifactBuilder, BuildResult
from .budget import BudgetExhausted, BudgetLimits, BudgetManager, Usage
from .checkpoint import CheckpointStore, EventStream, Snapshot, fold_state
from .compactor import KEEP_RECENT_STEPS, CompactResult, Compactor
from .context import (
    CONTEXT_LAYER_COUNT,
    SYSTEM_PROMPT,
    ContextBundle,
    ContextManager,
    EvidenceRef,
    Messages,
    StepDigest,
    estimate_tokens,
    evidence_ref,
)
from .hitl import HITLGate
from .memory import MS_PER_HOUR, RECENCY_WEIGHT, MemoryHit, MemoryStore, tokenize
from .model_router import AI_ROLES, ROLE_MODEL_TIER, LLMError, LLMResult, ModelRouter
from .pipeline import Pipeline, RunResult
from .planner import PlannedTask, Planner, TaskDAG
from .resume import OrphanReport, ResumeEngine, ResumePlan
from .session_manager import LaneBusy, SessionManager
from .state_machine import (
    RUN_STATUS_EVENT,
    RUN_TRANSITIONS,
    TASK_STATUS_EVENT,
    TASK_TRANSITIONS,
    IllegalTransition,
    StateMachine,
    Transition,
)
from .stop_rules import STOP_RULE_COUNT, StopRuleSet, StopState, StepView
from .tool_executor import DegradedResult, ToolExecutor, ToolInvocation, ToolResult
from .tool_registry import ToolRegistry
from .ui_renderer import (
    DEFAULT_UI_BLOCK_MAX,
    FALLBACK_COMPONENT,
    IMPLEMENTED_COMPONENT_COUNT,
    IMPLEMENTED_COMPONENTS,
    REQUIRED_PROPS,
    UI_COMPONENT_WHITELIST,
    UI_COMPONENT_WHITELIST_COUNT,
    UIRenderer,
)

__all__ = [
    # 管线
    "Pipeline",
    "RunResult",
    # 主循环
    "AgentLoop",
    "LoopContext",
    "TaskOutcome",
    # 规划
    "Planner",
    "PlannedTask",
    "TaskDAG",
    # 工具
    "ToolRegistry",
    "ToolExecutor",
    "ToolInvocation",
    "ToolResult",
    "DegradedResult",
    # 模型
    "ModelRouter",
    "LLMResult",
    "LLMError",
    "AI_ROLES",
    "ROLE_MODEL_TIER",
    # 会话
    "SessionManager",
    "LaneBusy",
    # --- M3：状态与恢复 ---
    "StateMachine",
    "Transition",
    "IllegalTransition",
    "RUN_TRANSITIONS",
    "TASK_TRANSITIONS",
    "RUN_STATUS_EVENT",
    "TASK_STATUS_EVENT",
    "CheckpointStore",
    "EventStream",
    "Snapshot",
    "fold_state",
    "ResumeEngine",
    "ResumePlan",
    "OrphanReport",
    "HITLGate",
    "BudgetManager",
    "BudgetLimits",
    "BudgetExhausted",
    "Usage",
    "StopRuleSet",
    "StopState",
    "StepView",
    "STOP_RULE_COUNT",
    # --- M4：记忆与上下文 ---
    "ContextManager",
    "ContextBundle",
    "EvidenceRef",
    "StepDigest",
    "Messages",
    "CONTEXT_LAYER_COUNT",
    "SYSTEM_PROMPT",
    "estimate_tokens",
    "evidence_ref",
    "Compactor",
    "CompactResult",
    "KEEP_RECENT_STEPS",
    "MemoryStore",
    "MemoryHit",
    "tokenize",
    "RECENCY_WEIGHT",
    "MS_PER_HOUR",
    # --- M5：产物与动态 UI ---
    "ArtifactBuilder",
    "BuildResult",
    "UIRenderer",
    "UI_COMPONENT_WHITELIST",
    "UI_COMPONENT_WHITELIST_COUNT",
    "IMPLEMENTED_COMPONENTS",
    "IMPLEMENTED_COMPONENT_COUNT",
    "REQUIRED_PROPS",
    "FALLBACK_COMPONENT",
    "DEFAULT_UI_BLOCK_MAX",
]
