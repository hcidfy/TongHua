"""M0 首版迁移：建 15 张表 + 2 条 UNIQUE 约束。

Revision ID: 0001_m0_initial
Revises: None
Create Date: 2026-10-19

契约来源：docs/02_ARCHITECTURE.md §4.1 / docs/HANDOFF.md §6.2。
- 15 张表 = 13 实体 + llm_calls + run_metrics
- UNIQUE(tool_calls.dedupe_key)
- UNIQUE(checkpoint_events(run_id, seq))
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_m0_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TS = sa.BigInteger()


def upgrade() -> None:
    op.create_table(
        "threads",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("updated_at", _TS, nullable=False),
    )

    op.create_table(
        "runs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("thread_id", sa.String(32), sa.ForeignKey("threads.id"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="CREATED"),
        sa.Column("parent_run_id", sa.String(32), nullable=True),
        sa.Column("resume_from_event_id", sa.String(32), nullable=True),
        sa.Column("stop_reason", sa.String(32), nullable=True),
        sa.Column("cost_total", sa.Float(), nullable=False, server_default="0"),
        sa.Column("tokens_total", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("started_at", _TS, nullable=True),
        sa.Column("ended_at", _TS, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_runs_thread_id", "runs", ["thread_id"])

    op.create_table(
        "tasks",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("run_id", sa.String(32), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("intent", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("deps_json", sa.JSON(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("evidence_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("degradation_reason", sa.Text(), nullable=True),
    )
    op.create_index("ix_tasks_run_id", "tasks", ["run_id"])

    op.create_table(
        "steps",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("task_id", sa.String(32), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("llm_span_id", sa.String(32), nullable=True),
        sa.Column("tool_call_id", sa.String(32), nullable=True),
        sa.Column("created_at", _TS, nullable=False),
    )
    op.create_index("ix_steps_task_id", "steps", ["task_id"])

    op.create_table(
        "tool_calls",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("run_id", sa.String(32), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("task_id", sa.String(32), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("tool_name", sa.String(64), nullable=False),
        sa.Column("args_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("idempotent", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("dedupe_key", sa.String(64), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.UniqueConstraint("dedupe_key", name="uq_tool_calls_dedupe_key"),
    )
    op.create_index("ix_tool_calls_run_id", "tool_calls", ["run_id"])
    op.create_index("ix_tool_calls_task_id", "tool_calls", ["task_id"])

    op.create_table(
        "evidences",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("run_id", sa.String(32), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("task_id", sa.String(32), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("tool_call_id", sa.String(32), sa.ForeignKey("tool_calls.id"), nullable=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("interface", sa.String(128), nullable=False),
        sa.Column("as_of", _TS, nullable=True),
        sa.Column("fetched_at", _TS, nullable=True),
        sa.Column("staleness_ms", sa.BigInteger(), nullable=True),
        sa.Column("unit", sa.String(32), nullable=True),
        sa.Column("caliber", sa.String(128), nullable=True),
        sa.Column("value_json", sa.JSON(), nullable=True),
        sa.Column("raw_ref", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("degrade_reason", sa.Text(), nullable=True),
        sa.Column("created_at", _TS, nullable=False),
    )
    op.create_index("ix_evidences_run_id", "evidences", ["run_id"])
    op.create_index("ix_evidences_task_id", "evidences", ["task_id"])

    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("run_id", sa.String(32), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("facts_json", sa.JSON(), nullable=False),
        sa.Column("inferences_json", sa.JSON(), nullable=False),
        sa.Column("uncertainties_json", sa.JSON(), nullable=False),
        sa.Column("citations_json", sa.JSON(), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
    )
    op.create_index("ix_artifacts_run_id", "artifacts", ["run_id"])

    op.create_table(
        "ui_blocks",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("run_id", sa.String(32), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("component", sa.String(64), nullable=False),
        sa.Column("props_json", sa.JSON(), nullable=False),
        sa.Column("refs_json", sa.JSON(), nullable=False),
        sa.Column("render_status", sa.String(16), nullable=False, server_default="ok"),
        sa.Column("created_at", _TS, nullable=False),
    )
    op.create_index("ix_ui_blocks_run_id", "ui_blocks", ["run_id"])

    op.create_table(
        "checkpoint_events",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("run_id", sa.String(32), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.UniqueConstraint("run_id", "seq", name="uq_checkpoint_events_run_seq"),
    )
    op.create_index("ix_checkpoint_events_run_id", "checkpoint_events", ["run_id"])

    op.create_table(
        "memories",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("thread_id", sa.String(32), nullable=True),
        sa.Column("run_id", sa.String(32), nullable=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding_ref", sa.String(128), nullable=True),
        sa.Column("created_at", _TS, nullable=False),
    )
    op.create_index("ix_memories_thread_id", "memories", ["thread_id"])
    op.create_index("ix_memories_run_id", "memories", ["run_id"])

    op.create_table(
        "traces",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("run_id", sa.String(32), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("span_id", sa.String(32), nullable=False),
        sa.Column("parent_span_id", sa.String(32), nullable=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("attrs_json", sa.JSON(), nullable=False),
        sa.Column("started_at", _TS, nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="OK"),
    )
    op.create_index("ix_traces_run_id", "traces", ["run_id"])
    op.create_index("ix_traces_span_id", "traces", ["span_id"])

    op.create_table(
        "approvals",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("run_id", sa.String(32), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("requested_at", _TS, nullable=False),
        sa.Column("decided_at", _TS, nullable=True),
        sa.Column("decided_by", sa.String(64), nullable=True),
    )
    op.create_index("ix_approvals_run_id", "approvals", ["run_id"])

    op.create_table(
        "tool_registry",
        sa.Column("name", sa.String(64), primary_key=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("schema_json", sa.JSON(), nullable=False),
        sa.Column("domain", sa.String(32), nullable=False),
        sa.Column("phase", sa.String(16), nullable=False),
        sa.Column("idempotent", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("timeout_ms", sa.Integer(), nullable=False, server_default="8000"),
        sa.Column("est_cost", sa.Float(), nullable=False, server_default="0"),
        sa.Column("permission_level", sa.String(16), nullable=False, server_default="READ"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.String(16), nullable=False, server_default="1"),
    )

    op.create_table(
        "llm_calls",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("run_id", sa.String(32), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("task_id", sa.String(32), nullable=True),
        sa.Column("step_id", sa.String(32), nullable=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost", sa.Float(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_code", sa.Integer(), nullable=True),
        sa.Column("request_ref", sa.String(128), nullable=True),
        sa.Column("created_at", _TS, nullable=False),
    )
    op.create_index("ix_llm_calls_run_id", "llm_calls", ["run_id"])

    op.create_table(
        "run_metrics",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("run_id", sa.String(32), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("metric_name", sa.String(64), nullable=False),
        sa.Column("metric_value", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(16), nullable=True),
        sa.Column("tags_json", sa.JSON(), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
    )
    op.create_index("ix_run_metrics_run_id", "run_metrics", ["run_id"])


def downgrade() -> None:
    for table in (
        "run_metrics",
        "llm_calls",
        "tool_registry",
        "approvals",
        "traces",
        "memories",
        "checkpoint_events",
        "ui_blocks",
        "artifacts",
        "evidences",
        "tool_calls",
        "steps",
        "tasks",
        "runs",
        "threads",
    ):
        op.drop_table(table)
