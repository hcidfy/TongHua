"""M0 契约：领域模型（**13 个** Pydantic v2 实体）。

唯一来源：docs/HANDOFF.md §6.1 与 docs/02_ARCHITECTURE.md §4.1（两者一致）。
**产出即冻结**：本文件是"契约单点"，其它 7 个模块全部只读依赖，不得增删字段
（契约变更 = 全员返工，见 HANDOFF §1 第 3 条与 §5.1）。

13 个模型 ↔ 15 张表的映射：本文件 13 个模型 = 13 张实体表；另 2 张可观测性表
（`llm_calls` / `run_metrics`）无独立领域实体，由 store 层直接建表（见 HANDOFF §6.2）。

口径约束（HANDOFF §8.2）：
- D2：所有时间字段统一为**毫秒级 Unix 时间戳**（int，Asia/Shanghai）。
- D4：`null` 表示上游缺失，**透传不补零** → 相关字段一律 `| None`，默认 `None`。
- C1：`Artifact` 中**不设**任何"操作建议/涨跌预测/收益承诺"字段。
- C2：`Artifact` 的 `facts / inferences / uncertainties` **三个独立数组**，不可合并。
- C3：`Evidence` 的 `source / interface / as_of / unit / caliber / raw_ref` 为**强制字段**。
"""

from __future__ import annotations

import time
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .enums import Phase, PermissionLevel, RunStatus, StopReason, TaskStatus
from .events import CheckpointEventType

__all__ = [
    "StepType",
    "UIBlockSourceType",
    "MemoryScope",
    "ApprovalStatus",
    "Thread",
    "Run",
    "Task",
    "Step",
    "ToolCall",
    "Evidence",
    "Artifact",
    "UIBlock",
    "CheckpointEvent",
    "Memory",
    "Trace",
    "Approval",
    "ToolSpec",
    "DOMAIN_MODEL_COUNT",
    "DOMAIN_MODELS",
    "now_ms",
    "new_id",
]


def now_ms() -> int:
    """当前毫秒级 Unix 时间戳（D2）。"""
    return int(time.time() * 1000)


def new_id() -> str:
    """实体主键：32 位无连字符 UUID。"""
    return uuid4().hex


# 内部别名（保持字段定义处可读）
_now_ms = now_ms
_new_id = new_id


class _DomainModel(BaseModel):
    """领域模型基类：契约冻结后只读；宽松读取（忽略未知列），不静默丢字段。"""

    model_config = ConfigDict(extra="ignore", validate_assignment=False)


# --------------------------------------------------------------------------- #
# 局部枚举（契约 §6.1 中内联取值的显式化；不计入 enums.py 的「5 个枚举」契约）
# --------------------------------------------------------------------------- #
class StepType(StrEnum):
    """`Step.type` 取值（HANDOFF §6.1：THINK | ACT | OBSERVE）。"""

    THINK = "THINK"
    ACT = "ACT"
    OBSERVE = "OBSERVE"


class UIBlockSourceType(StrEnum):
    """`UIBlock.source_type` 取值（HANDOFF §6.1：PLAN | EVIDENCE | ARTIFACT | SYSTEM）。"""

    PLAN = "PLAN"
    EVIDENCE = "EVIDENCE"
    ARTIFACT = "ARTIFACT"
    SYSTEM = "SYSTEM"


class MemoryScope(StrEnum):
    """`Memory.scope` 取值（HANDOFF §6.1：WORKING | SESSION | LONG）。"""

    WORKING = "WORKING"
    SESSION = "SESSION"
    LONG = "LONG"


class ApprovalStatus(StrEnum):
    """`Approval.status` 取值（HANDOFF §6.1：PENDING | GRANTED | REJECTED | EXPIRED）。"""

    PENDING = "PENDING"
    GRANTED = "GRANTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


# --------------------------------------------------------------------------- #
# 1–13：13 个实体模型（字段顺序与 HANDOFF §6.1 逐行对齐）
# --------------------------------------------------------------------------- #
class Thread(_DomainModel):
    """研究线程（一次用户目标的上层容器）。"""

    id: str = Field(default_factory=_new_id)
    title: str
    goal: str
    status: str = "ACTIVE"
    created_at: int = Field(default_factory=_now_ms)
    updated_at: int = Field(default_factory=_now_ms)


class Run(_DomainModel):
    """一次可暂停 / 可恢复 / 可审计的执行。"""

    id: str = Field(default_factory=_new_id)
    thread_id: str
    status: RunStatus = RunStatus.CREATED
    parent_run_id: str | None = None
    resume_from_event_id: str | None = None
    stop_reason: StopReason | None = None
    cost_total: float = 0.0
    tokens_total: int = 0
    started_at: int | None = None
    ended_at: int | None = None
    error: str | None = None


class Task(_DomainModel):
    """计划中的一个任务节点（依赖 DAG 的结点）。"""

    id: str = Field(default_factory=_new_id)
    run_id: str
    seq: int
    title: str
    intent: str
    status: TaskStatus = TaskStatus.PENDING
    deps_json: list[str] = Field(default_factory=list)
    attempts: int = 0
    evidence_count: int = 0
    degradation_reason: str | None = None


class Step(_DomainModel):
    """主循环中的一步（THINK / ACT / OBSERVE）。"""

    id: str = Field(default_factory=_new_id)
    task_id: str
    seq: int
    type: StepType
    payload_json: dict[str, Any] = Field(default_factory=dict)
    llm_span_id: str | None = None
    tool_call_id: str | None = None
    created_at: int = Field(default_factory=_now_ms)


class ToolCall(_DomainModel):
    """一次工具调用；`dedupe_key` UNIQUE 是恢复时"不重复调用"的依据。"""

    id: str = Field(default_factory=_new_id)
    run_id: str
    task_id: str
    tool_name: str
    args_json: dict[str, Any] = Field(default_factory=dict)
    status: str = "PENDING"
    attempts: int = 0
    latency_ms: int | None = None
    error_code: int | None = None
    error_message: str | None = None
    idempotent: bool = True
    dedupe_key: str
    created_at: int = Field(default_factory=_now_ms)


class Evidence(_DomainModel):
    """一条可回溯的证据（核心结论必须能落回原始字段，C3）。"""

    id: str = Field(default_factory=_new_id)
    run_id: str
    task_id: str
    tool_call_id: str | None = None
    # --- C3 强制字段（无默认值：调用方必须显式给出，可为 None 以透传缺失） ---
    source: str
    interface: str
    as_of: int | None
    unit: str | None
    caliber: str | None
    raw_ref: str
    confidence: float | None
    # --- 可选字段 ---
    fetched_at: int | None = None
    staleness_ms: int | None = None
    value_json: Any = None
    degrade_reason: str | None = None
    created_at: int = Field(default_factory=_now_ms)


class Artifact(_DomainModel):
    """研究结论；事实 / 推断 / 不确定**三层分离**（C2），不含任何操作建议（C1）。"""

    id: str = Field(default_factory=_new_id)
    run_id: str
    version: int = 1
    facts_json: list[dict[str, Any]] = Field(default_factory=list)
    inferences_json: list[dict[str, Any]] = Field(default_factory=list)
    uncertainties_json: list[dict[str, Any]] = Field(default_factory=list)
    citations_json: list[dict[str, Any]] = Field(default_factory=list)
    created_at: int = Field(default_factory=_now_ms)


class UIBlock(_DomainModel):
    """动态 UI 增量块；`component` 必须命中白名单（架构 §11.2）。"""

    id: str = Field(default_factory=_new_id)
    run_id: str
    seq: int
    source_type: UIBlockSourceType
    component: str
    props_json: dict[str, Any] = Field(default_factory=dict)
    refs_json: list[Any] = Field(default_factory=list)
    render_status: str = "ok"
    created_at: int = Field(default_factory=_now_ms)


class CheckpointEvent(_DomainModel):
    """事件溯源的一条事件；`(run_id, seq)` UNIQUE 是单调序列的依据。"""

    id: str = Field(default_factory=_new_id)
    run_id: str
    seq: int
    event_type: CheckpointEventType
    payload_json: dict[str, Any] = Field(default_factory=dict)
    created_at: int = Field(default_factory=_now_ms)


class Memory(_DomainModel):
    """分层记忆（WORKING / SESSION / LONG）。"""

    id: str = Field(default_factory=_new_id)
    scope: MemoryScope
    thread_id: str | None = None
    run_id: str | None = None
    kind: str
    content: str
    embedding_ref: str | None = None
    created_at: int = Field(default_factory=_now_ms)


class Trace(_DomainModel):
    """Span 树的一个节点（扁平存储 + parent 指针，前端组树）。"""

    id: str = Field(default_factory=_new_id)
    run_id: str
    span_id: str = Field(default_factory=_new_id)
    parent_span_id: str | None = None
    name: str
    attrs_json: dict[str, Any] = Field(default_factory=dict)
    started_at: int = Field(default_factory=_now_ms)
    duration_ms: int | None = None
    status: str = "OK"


class Approval(_DomainModel):
    """人工审批（HITL）记录。"""

    id: str = Field(default_factory=_new_id)
    run_id: str
    type: str
    payload_json: dict[str, Any] = Field(default_factory=dict)
    status: ApprovalStatus = ApprovalStatus.PENDING
    requested_at: int = Field(default_factory=_now_ms)
    decided_at: int | None = None
    decided_by: str | None = None


class ToolSpec(_DomainModel):
    """工具注册项（对应 `tool_registry`，`name` 为主键）。"""

    name: str
    description: str
    schema_json: dict[str, Any] = Field(default_factory=dict)
    domain: str
    phase: Phase = Phase.EXPLORE
    idempotent: bool = True
    timeout_ms: int = 8000
    est_cost: float = 0.0
    permission_level: PermissionLevel = PermissionLevel.READ
    enabled: bool = True
    version: str = "1"


#: 契约数量（HANDOFF §6.1 / 03_BUILD_PLAN §10）：测试以此断言，防止误增删。
DOMAIN_MODEL_COUNT = 13

#: 13 个实体模型（有序），供建表 / 测试遍历。
DOMAIN_MODELS: tuple[type[_DomainModel], ...] = (
    Thread,
    Run,
    Task,
    Step,
    ToolCall,
    Evidence,
    Artifact,
    UIBlock,
    CheckpointEvent,
    Memory,
    Trace,
    Approval,
    ToolSpec,
)
