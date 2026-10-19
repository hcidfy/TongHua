"""M0 契约：Checkpoint 事件类型（**26 类**）。

唯一来源：docs/02_ARCHITECTURE.md §4.2（24 类）＋ `RunPaused` / `RunResumed`（HANDOFF §6.3 已定案）。
产出即冻结：`RunPaused` / `RunResumed` 是「可暂停 / 可恢复」硬需求的事件载体，M3 恢复机制直接依赖。
"""

from __future__ import annotations

from enum import StrEnum


class CheckpointEventType(StrEnum):
    """事件溯源的事件类型。成员名与事件名同形，便于与契约表逐行对照。"""

    # --- Run 生命周期 ---
    RunStarted = "RunStarted"
    PlanGenerated = "PlanGenerated"
    PlanApproved = "PlanApproved"

    # --- Task 生命周期 ---
    TaskStarted = "TaskStarted"
    TaskCompleted = "TaskCompleted"
    TaskFailed = "TaskFailed"
    TaskDegraded = "TaskDegraded"
    TaskSkipped = "TaskSkipped"

    # --- 主循环 ---
    LoopIterationStarted = "LoopIterationStarted"
    ToolInvocationStarted = "ToolInvocationStarted"
    ToolInvocationSucceeded = "ToolInvocationSucceeded"
    ToolInvocationFailed = "ToolInvocationFailed"
    EvidenceRecorded = "EvidenceRecorded"
    ContextCompacted = "ContextCompacted"

    # --- 审批（HITL）---
    ApprovalRequested = "ApprovalRequested"
    ApprovalGranted = "ApprovalGranted"
    ApprovalRejected = "ApprovalRejected"

    # --- 预算与停止规则 ---
    BudgetExhausted = "BudgetExhausted"
    StopRuleHit = "StopRuleHit"

    # --- 产物与合规 ---
    ArtifactBuilt = "ArtifactBuilt"
    GuardRejected = "GuardRejected"

    # --- 暂停 / 恢复（可暂停、可恢复的载体）---
    RunPaused = "RunPaused"
    RunResumed = "RunResumed"

    # --- Run 终态 ---
    RunCompleted = "RunCompleted"
    RunFailed = "RunFailed"
    RunStopped = "RunStopped"


#: 契约数量（HANDOFF §6.3）：测试以此断言，防止后续误增删。
EVENT_TYPE_COUNT = 26

#: 全部事件类型（有序）。
EVENT_TYPES: tuple[CheckpointEventType, ...] = tuple(CheckpointEventType)


def all_event_types() -> tuple[CheckpointEventType, ...]:
    """返回全部 26 类事件类型。"""
    return EVENT_TYPES


__all__ = ["CheckpointEventType", "EVENT_TYPE_COUNT", "EVENT_TYPES", "all_event_types"]
