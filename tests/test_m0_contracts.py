"""M0 契约冻结断言：5 枚举 / 26 事件 / 13 模型 / 15 表 / 2 UNIQUE。

任何一条失败都意味着"契约被改"，应视为阻断性错误（HANDOFF §1 第 3 条）。
"""

from __future__ import annotations

from sqlalchemy import UniqueConstraint

from app.domain import enums, events, models
from app.store import schema
from app.store.db import Base

# --- 契约字段清单（逐行抄自 docs/HANDOFF.md §6.1，不改字） ---
EXPECTED_FIELDS: dict[str, set[str]] = {
    "Thread": {"id", "title", "goal", "status", "created_at", "updated_at"},
    "Run": {
        "id", "thread_id", "status", "parent_run_id", "resume_from_event_id",
        "stop_reason", "cost_total", "tokens_total", "started_at", "ended_at", "error",
    },
    "Task": {
        "id", "run_id", "seq", "title", "intent", "status", "deps_json",
        "attempts", "evidence_count", "degradation_reason",
    },
    "Step": {
        "id", "task_id", "seq", "type", "payload_json",
        "llm_span_id", "tool_call_id", "created_at",
    },
    "ToolCall": {
        "id", "run_id", "task_id", "tool_name", "args_json", "status", "attempts",
        "latency_ms", "error_code", "error_message", "idempotent", "dedupe_key", "created_at",
    },
    "Evidence": {
        "id", "run_id", "task_id", "tool_call_id", "source", "interface", "as_of",
        "fetched_at", "staleness_ms", "unit", "caliber", "value_json", "raw_ref",
        "confidence", "degrade_reason", "created_at",
    },
    "Artifact": {
        "id", "run_id", "version", "facts_json", "inferences_json",
        "uncertainties_json", "citations_json", "created_at",
    },
    "UIBlock": {
        "id", "run_id", "seq", "source_type", "component",
        "props_json", "refs_json", "render_status", "created_at",
    },
    "CheckpointEvent": {"id", "run_id", "seq", "event_type", "payload_json", "created_at"},
    "Memory": {
        "id", "scope", "thread_id", "run_id", "kind", "content", "embedding_ref", "created_at",
    },
    "Trace": {
        "id", "run_id", "span_id", "parent_span_id", "name",
        "attrs_json", "started_at", "duration_ms", "status",
    },
    "Approval": {
        "id", "run_id", "type", "payload_json", "status",
        "requested_at", "decided_at", "decided_by",
    },
    "ToolSpec": {
        "name", "description", "schema_json", "domain", "phase", "idempotent",
        "timeout_ms", "est_cost", "permission_level", "enabled", "version",
    },
}

EXPECTED_TABLES = {
    "threads", "runs", "tasks", "steps", "tool_calls", "evidences", "artifacts",
    "ui_blocks", "checkpoint_events", "memories", "traces", "approvals",
    "tool_registry", "llm_calls", "run_metrics",
}


def test_m0_enums_are_five_with_frozen_values() -> None:
    assert len(enums.__all__) == 5
    assert [s.value for s in enums.RunStatus] == [
        "CREATED", "PLANNING", "EXECUTING", "AWAITING_APPROVAL",
        "SYNTHESIZING", "COMPLETED", "STOPPED", "FAILED",
    ]
    assert [s.value for s in enums.TaskStatus] == [
        "PENDING", "READY", "RUNNING", "DONE", "FAILED", "DEGRADED", "SKIPPED",
    ]
    assert [s.value for s in enums.StopReason] == [
        "MAX_STEPS", "MAX_TOKENS", "MAX_COST", "MAX_WALL_TIME",
        "LOOP_DETECT", "NO_PROGRESS", "USER_ABORT", "GUARD_REJECT",
    ]
    assert [s.value for s in enums.Phase] == ["EXPLORE", "COMMIT"]
    assert [s.value for s in enums.PermissionLevel] == ["READ", "EXPENSIVE", "SENSITIVE"]


def test_m0_events_are_26_including_pause_resume() -> None:
    assert len(events.CheckpointEventType) == 26
    assert events.EVENT_TYPE_COUNT == 26
    assert len(events.all_event_types()) == 26
    assert "RunPaused" in events.CheckpointEventType.__members__
    assert "RunResumed" in events.CheckpointEventType.__members__


def test_m0_models_are_13_with_exact_contract_fields() -> None:
    assert models.DOMAIN_MODEL_COUNT == 13
    assert len(models.DOMAIN_MODELS) == 13
    assert len(EXPECTED_FIELDS) == 13
    for name, expected in EXPECTED_FIELDS.items():
        model = getattr(models, name)
        assert set(model.model_fields) == expected, f"{name} 字段与契约不一致"


def test_m0_artifact_has_no_advice_field_and_three_layers() -> None:
    fields = set(models.Artifact.model_fields)
    # C1：不设任何"操作建议 / 涨跌预测 / 收益承诺"字段
    assert not {"advice", "recommendation", "target_price", "prediction"} & fields
    # C2：事实 / 推断 / 不确定 三个独立数组
    assert {"facts_json", "inferences_json", "uncertainties_json"} <= fields


def test_m0_evidence_mandatory_fields_are_required() -> None:
    # C3：强制字段不得有默认值（未显式提供即报错）
    required = {"source", "interface", "as_of", "unit", "caliber", "raw_ref", "confidence"}
    for field_name in required:
        assert models.Evidence.model_fields[field_name].is_required(), field_name


def test_m0_schema_has_15_tables() -> None:
    assert schema.TABLE_COUNT == 15
    assert set(Base.metadata.tables) == EXPECTED_TABLES
    assert len(Base.metadata.tables) == 15


def test_m0_two_unique_constraints_exist_and_no_more() -> None:
    def uniques(table) -> set[str]:
        return {
            c.name
            for c in table.constraints
            if isinstance(c, UniqueConstraint) and c.name
        }

    assert uniques(schema.ToolCallRow.__table__) == {"uq_tool_calls_dedupe_key"}
    assert uniques(schema.CheckpointEventRow.__table__) == {"uq_checkpoint_events_run_seq"}
    # 契约只允许这 2 条 UNIQUE（其余表不得新增）
    allowed = {
        "tool_calls": {"uq_tool_calls_dedupe_key"},
        "checkpoint_events": {"uq_checkpoint_events_run_seq"},
    }
    for name, table in Base.metadata.tables.items():
        assert uniques(table) == allowed.get(name, set()), f"{name} 出现了契约外 UNIQUE"


def test_m0_m0_repo_tables_are_six() -> None:
    assert schema.M0_REPO_TABLES == (
        "threads", "runs", "tasks", "steps", "evidences", "checkpoint_events",
    )
