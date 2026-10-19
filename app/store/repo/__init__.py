"""Repository 汇总。

- M0：6 张（threads / runs / tasks / steps / evidences / checkpoint_events）
- M1 补齐 5 张：tool_registry / tool_calls / llm_calls / traces / memories
- M3 补齐 1 张：approvals
- M5 补齐 2 张：artifacts / ui_blocks
- M6 补齐 1 张：run_metrics（表在 M0 已随 schema 冻结存在，此处只补 Repository）
"""

from .approvals import ApprovalRepository
from .artifacts import ArtifactRepository
from .base import BaseRepository
from .checkpoint_events import CheckpointEventRepository
from .evidences import EvidenceRepository
from .llm_calls import LlmCallRepository
from .memories import MemoryRepository
from .run_metrics import RunMetricRepository
from .runs import RunRepository
from .steps import StepRepository
from .tasks import TaskRepository
from .threads import ThreadRepository
from .tool_calls import ToolCallRepository
from .tool_registry import ToolRegistryRepository
from .traces import TraceRepository
from .ui_blocks import UIBlockRepository

__all__ = [
    "BaseRepository",
    "ThreadRepository",
    "RunRepository",
    "TaskRepository",
    "StepRepository",
    "EvidenceRepository",
    "CheckpointEventRepository",
    # --- M1 补齐 ---
    "ToolRegistryRepository",
    "ToolCallRepository",
    "LlmCallRepository",
    "TraceRepository",
    "MemoryRepository",
    # --- M3 补齐 ---
    "ApprovalRepository",
    # --- M5 补齐 ---
    "ArtifactRepository",
    "UIBlockRepository",
    # --- M6 补齐 ---
    "RunMetricRepository",
]
