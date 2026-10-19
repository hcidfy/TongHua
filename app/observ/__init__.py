"""可观测层（M6：查询与展示面在此之上扩展）。

- `tracer.py`：Span 树构建与落库（M1 落地；M6 追加**查询面** `build_span_tree` / `Tracer.tree`）；
- `cost.py`：事后计价与汇总查询（`CostReporter`，口径唯一来源 `llm_calls` 聚合）；
- `sanitize.py`：日志与 Trace 脱敏（Key / 隐私）。
"""

from .cost import CostReporter, CostSummary
from .sanitize import (
    DEFAULT_SANITIZE_PATTERNS,
    REDACTED,
    Sanitizer,
    sanitize_text,
    sanitizer_from_settings,
)
from .tracer import Span, Tracer, build_span_tree

__all__ = [
    "Span",
    "Tracer",
    "build_span_tree",
    "CostReporter",
    "CostSummary",
    "Sanitizer",
    "sanitize_text",
    "sanitizer_from_settings",
    "DEFAULT_SANITIZE_PATTERNS",
    "REDACTED",
]
