"""Harness 内核：Hook 层（03_BUILD_PLAN §3 M1）。

两段链的**唯一装配入口**在 `pre.default_pre_hooks()` / `post.default_post_hooks()`；
`Pipeline` 通过本包拿到默认链，也可以在构造时注入自定义链（测试与替换用）。

| 阶段 | 顺序 |
|---|---|
| PRE | ① ContextInject（M4 实现体） ② BudgetCheck ③ GuardPre ④ Plan |
| POST | ① EvidencePersist ② TraceFlush ③ MemoryWrite ④ CostSettle ⑤ GuardPost |

**POST 链无条件执行**（`Pipeline` 用 `try/finally` 保障），因此每个 PostHook 必须幂等。
"""

from __future__ import annotations

from . import post, pre
from .base import Hook, HookKind, HookResult, PipelineContext

__all__ = [
    "Hook",
    "HookKind",
    "HookResult",
    "PipelineContext",
    "pre",
    "post",
]
