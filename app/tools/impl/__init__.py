"""工具层：执行体包入口（`ToolBlueprint` → 可绑定的 `impl` / `fallback`）。"""

from .data_tools import make_data_impl, make_ifind_fallback, records_of, source_of, trim_rows
from .internal_tools import make_artifact_impl

__all__ = [
    "make_data_impl",
    "make_ifind_fallback",
    "make_artifact_impl",
    "source_of",
    "records_of",
    "trim_rows",
]
