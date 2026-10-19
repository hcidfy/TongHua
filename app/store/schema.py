"""存储层：ORM 表定义（**15 张** = 13 实体 + `llm_calls` + `run_metrics`）。

唯一来源：docs/02_ARCHITECTURE.md §4.1（字段）与 docs/HANDOFF.md §6.1 / §6.2（数量）。
**两条 UNIQUE 约束**（恢复机制的地基，M0 必须建）：
- `tool_calls.dedupe_key` UNIQUE
- `checkpoint_events(run_id, seq)` UNIQUE

除上述两条外**不再新增 UNIQUE**——表/约束数量属契约（03_BUILD_PLAN §10），不得擅改。
类名统一带 `Row` 后缀，以区别于 `app.domain.models` 里的 Pydantic 领域模型。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

__all__ = [
    "ThreadRow",
    "RunRow",
    "TaskRow",
    "StepRow",
    "ToolCallRow",
    "EvidenceRow",
    "ArtifactRow",
    "UIBlockRow",
    "CheckpointEventRow",
    "MemoryRow",
    "TraceRow",
    "ApprovalRow",
    "ToolRegistryRow",
    "LlmCallRow",
    "RunMetricRow",
    "TABLE_COUNT",
]

_ID = String(32)
_TS = BigInteger  # 毫秒级 Unix 时间戳（D2）


class ThreadRow(Base):
    __tablename__ = "threads"

    id: Mapped[str] = mapped_column(_ID, primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    created_at: Mapped[int] = mapped_column(_TS, nullable=False)
    updated_at: Mapped[int] = mapped_column(_TS, nullable=False)


class RunRow(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(_ID, primary_key=True)
    thread_id: Mapped[str] = mapped_column(ForeignKey("threads.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="CREATED")
    parent_run_id: Mapped[str | None] = mapped_column(_ID, nullable=True)
    resume_from_event_id: Mapped[str | None] = mapped_column(_ID, nullable=True)
    stop_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    cost_total: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    tokens_total: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    started_at: Mapped[int | None] = mapped_column(_TS, nullable=True)
    ended_at: Mapped[int | None] = mapped_column(_TS, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class TaskRow(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(_ID, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    intent: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    deps_json: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    degradation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class StepRow(Base):
    __tablename__ = "steps"

    id: Mapped[str] = mapped_column(_ID, primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)  # THINK | ACT | OBSERVE
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    llm_span_id: Mapped[str | None] = mapped_column(_ID, nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(_ID, nullable=True)
    created_at: Mapped[int] = mapped_column(_TS, nullable=False)


class ToolCallRow(Base):
    __tablename__ = "tool_calls"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_tool_calls_dedupe_key"),
    )

    id: Mapped[str] = mapped_column(_ID, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False, index=True)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    args_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[int] = mapped_column(_TS, nullable=False)


class EvidenceRow(Base):
    __tablename__ = "evidences"

    id: Mapped[str] = mapped_column(_ID, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False, index=True)
    tool_call_id: Mapped[str | None] = mapped_column(ForeignKey("tool_calls.id"), nullable=True)
    # C3 强制字段（库层同样 not null）
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    interface: Mapped[str] = mapped_column(String(128), nullable=False)
    as_of: Mapped[int | None] = mapped_column(_TS, nullable=True)
    fetched_at: Mapped[int | None] = mapped_column(_TS, nullable=True)
    staleness_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    caliber: Mapped[str | None] = mapped_column(String(128), nullable=True)
    value_json: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    raw_ref: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    degrade_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[int] = mapped_column(_TS, nullable=False)


class ArtifactRow(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(_ID, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # C2：事实 / 推断 / 不确定三个独立数组（另有引用清单）；C1：无任何"操作建议"字段
    facts_json: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    inferences_json: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    uncertainties_json: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    citations_json: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[int] = mapped_column(_TS, nullable=False)


class UIBlockRow(Base):
    __tablename__ = "ui_blocks"

    id: Mapped[str] = mapped_column(_ID, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)  # PLAN|EVIDENCE|ARTIFACT|SYSTEM
    component: Mapped[str] = mapped_column(String(64), nullable=False)
    props_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    refs_json: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    render_status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    created_at: Mapped[int] = mapped_column(_TS, nullable=False)


class CheckpointEventRow(Base):
    __tablename__ = "checkpoint_events"
    __table_args__ = (
        UniqueConstraint("run_id", "seq", name="uq_checkpoint_events_run_seq"),
    )

    id: Mapped[str] = mapped_column(_ID, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[int] = mapped_column(_TS, nullable=False)


class MemoryRow(Base):
    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(_ID, primary_key=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)  # WORKING|SESSION|LONG
    thread_id: Mapped[str | None] = mapped_column(_ID, nullable=True, index=True)
    run_id: Mapped[str | None] = mapped_column(_ID, nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[int] = mapped_column(_TS, nullable=False)


class TraceRow(Base):
    __tablename__ = "traces"

    id: Mapped[str] = mapped_column(_ID, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False, index=True)
    span_id: Mapped[str] = mapped_column(_ID, nullable=False, index=True)
    parent_span_id: Mapped[str | None] = mapped_column(_ID, nullable=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    attrs_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    started_at: Mapped[int] = mapped_column(_TS, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OK")


class ApprovalRow(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(_ID, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    requested_at: Mapped[int] = mapped_column(_TS, nullable=False)
    decided_at: Mapped[int | None] = mapped_column(_TS, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ToolRegistryRow(Base):
    __tablename__ = "tool_registry"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    schema_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    domain: Mapped[str] = mapped_column(String(32), nullable=False)
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    idempotent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    timeout_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=8000)
    est_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    permission_level: Mapped[str] = mapped_column(String(16), nullable=False, default="READ")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[str] = mapped_column(String(16), nullable=False, default="1")


class LlmCallRow(Base):
    """`runs.cost_total` / `runs.tokens_total` 的唯一数据来源（HANDOFF §6.2）。"""

    __tablename__ = "llm_calls"

    id: Mapped[str] = mapped_column(_ID, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False, index=True)
    task_id: Mapped[str | None] = mapped_column(_ID, nullable=True)
    step_id: Mapped[str | None] = mapped_column(_ID, nullable=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[int] = mapped_column(_TS, nullable=False)


class RunMetricRow(Base):
    """运行期指标（迭代数 / 工具调用数 / 降级次数 / 暂停次数等），供 M6 看板。"""

    __tablename__ = "run_metrics"

    id: Mapped[str] = mapped_column(_ID, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False, index=True)
    metric_name: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    tags_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[int] = mapped_column(_TS, nullable=False)


#: 契约数量（HANDOFF §6.2）：15 张表。
TABLE_COUNT = 15

#: 需要 Repository 的 6 张表（M0 只做这 6 张，见 03_BUILD_PLAN §3 M0）。
M0_REPO_TABLES: tuple[str, ...] = (
    "threads",
    "runs",
    "tasks",
    "steps",
    "evidences",
    "checkpoint_events",
)
