"""领域层（契约单点）：枚举 / 事件 / 模型。

产出即冻结（HANDOFF §1 第 3 条）：后续模块只读依赖，不得增删字段或取值。
"""

from .enums import PermissionLevel, Phase, RunStatus, StopReason, TaskStatus
from .events import EVENT_TYPE_COUNT, EVENT_TYPES, CheckpointEventType, all_event_types
from .models import (
    DOMAIN_MODEL_COUNT,
    DOMAIN_MODELS,
    Approval,
    ApprovalStatus,
    Artifact,
    CheckpointEvent,
    Evidence,
    Memory,
    MemoryScope,
    Run,
    Step,
    StepType,
    Task,
    Thread,
    ToolCall,
    ToolSpec,
    Trace,
    UIBlock,
    UIBlockSourceType,
    new_id,
    now_ms,
)

__all__ = [
    # enums
    "RunStatus",
    "TaskStatus",
    "StopReason",
    "Phase",
    "PermissionLevel",
    # events
    "CheckpointEventType",
    "EVENT_TYPE_COUNT",
    "EVENT_TYPES",
    "all_event_types",
    # models
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
    "StepType",
    "UIBlockSourceType",
    "MemoryScope",
    "ApprovalStatus",
    "DOMAIN_MODEL_COUNT",
    "DOMAIN_MODELS",
    "now_ms",
    "new_id",
]
