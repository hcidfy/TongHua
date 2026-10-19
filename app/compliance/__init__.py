"""合规层（03_BUILD_PLAN §3 M6）：产物发布前的统一规则守卫。

`guard.py` 提供三类检查与 `GuardVerdict`（**模块内置枚举**，不计入
`app/domain/enums.py` 的 5 个领域枚举——契约冻结）。
`harness/hooks/pre|post/guard_*.py` 是最小版护栏（M1），本层是**产物层**的完整版，
且与 C19 渲染层的「无引用 FACT 拦截」构成**双保险**（架构 §11.3-2）。
"""

from .guard import (
    CATEGORY_ADVICE,
    CATEGORY_FACT_NO_REF,
    CATEGORY_INFERENCE_NO_CONDITION,
    CATEGORY_PREDICTION,
    CATEGORY_PROMISE,
    GUARD_FORBIDDEN_FIELDS,
    GUARD_REDLINE_COUNT,
    GUARD_REDLINES,
    Guard,
    GuardFinding,
    GuardResult,
    GuardVerdict,
)

__all__ = [
    "Guard",
    "GuardVerdict",
    "GuardFinding",
    "GuardResult",
    "GUARD_REDLINES",
    "GUARD_REDLINE_COUNT",
    "GUARD_FORBIDDEN_FIELDS",
    "CATEGORY_PROMISE",
    "CATEGORY_PREDICTION",
    "CATEGORY_ADVICE",
    "CATEGORY_FACT_NO_REF",
    "CATEGORY_INFERENCE_NO_CONDITION",
]
