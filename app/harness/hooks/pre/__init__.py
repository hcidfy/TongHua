"""PreHook 链：① ContextInject ② BudgetCheck ③ GuardPre ④ Plan（架构 §3.1）。

**ContextInjectHook 的文件实现在 M4**（`harness/hooks/pre/context_inject.py`），
本模块（M1）只保留**注册位**——即按顺序把四个 Hook 装配进 PreHook 链，顺序与架构一致。
"""

from __future__ import annotations

from ..base import Hook, PipelineContext

from .budget_check import BudgetCheckHook
from .context_inject import ContextInjectHook
from .guard_pre import GuardPreHook
from .plan import PlanHook

__all__ = [
    "ContextInjectHook",
    "BudgetCheckHook",
    "GuardPreHook",
    "PlanHook",
    "default_pre_hooks",
]


def default_pre_hooks() -> list[Hook]:
    """默认 PreHook 链（按架构 §3.1 的顺序：ContextInject → BudgetCheck → GuardPre → Plan）。"""
    return [ContextInjectHook(), BudgetCheckHook(), GuardPreHook(), PlanHook()]
