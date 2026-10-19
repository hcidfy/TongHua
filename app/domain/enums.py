"""M0 契约：领域枚举（**5 个**）。

取值唯一来源：docs/02_ARCHITECTURE.md §5（状态机）与 docs/03_BUILD_PLAN.md §3 M0。
产出即冻结；后续模块只读，不得增删取值（契约变更 = 全员返工，见 HANDOFF §5.2）。
"""

from __future__ import annotations

from enum import StrEnum


class RunStatus(StrEnum):
    """Run 状态机取值（架构 §5.1）。"""

    CREATED = "CREATED"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    SYNTHESIZING = "SYNTHESIZING"
    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


class TaskStatus(StrEnum):
    """Task 状态机取值（架构 §5.2）。"""

    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"
    DEGRADED = "DEGRADED"
    SKIPPED = "SKIPPED"


class StopReason(StrEnum):
    """停止规则命中原因（FR-08 的 8 条规则 → 8 个取值，名称与 03_BUILD_PLAN §3 M3 一致）。"""

    MAX_STEPS = "MAX_STEPS"
    MAX_TOKENS = "MAX_TOKENS"
    MAX_COST = "MAX_COST"
    MAX_WALL_TIME = "MAX_WALL_TIME"
    LOOP_DETECT = "LOOP_DETECT"
    NO_PROGRESS = "NO_PROGRESS"
    USER_ABORT = "USER_ABORT"
    GUARD_REJECT = "GUARD_REJECT"


class Phase(StrEnum):
    """工具阶段：循环内核只认 phase，不认业务类型（架构 §1.2）。"""

    EXPLORE = "EXPLORE"
    COMMIT = "COMMIT"


class PermissionLevel(StrEnum):
    """工具权限级（架构 §9.1）。"""

    READ = "READ"
    EXPENSIVE = "EXPENSIVE"
    SENSITIVE = "SENSITIVE"


__all__ = [
    "RunStatus",
    "TaskStatus",
    "StopReason",
    "Phase",
    "PermissionLevel",
]
