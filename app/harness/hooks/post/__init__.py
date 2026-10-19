"""PostHook 链：① EvidencePersist ② TraceFlush ③ MemoryWrite ④ CostSettle ⑤ GuardPost。

顺序即架构 §3.1 的顺序。**全部由 Pipeline 用 `try/finally` 无条件执行**，因此每个 Hook
都必须是**幂等**的（重复执行不得产生重复副作用）——各文件内单独说明。
"""

from __future__ import annotations

from ..base import Hook

from .cost_settle import CostSettleHook
from .evidence_persist import EvidencePersistHook
from .guard_post import GuardPostHook
from .memory_write import MemoryWriteHook
from .trace_flush import TraceFlushHook

__all__ = [
    "EvidencePersistHook",
    "TraceFlushHook",
    "MemoryWriteHook",
    "CostSettleHook",
    "GuardPostHook",
    "default_post_hooks",
]


def default_post_hooks() -> list[Hook]:
    """默认 PostHook 链（① EvidencePersist ② TraceFlush ③ MemoryWrite ④ CostSettle ⑤ GuardPost）。"""
    return [
        EvidencePersistHook(),
        TraceFlushHook(),
        MemoryWriteHook(),
        CostSettleHook(),
        GuardPostHook(),
    ]
