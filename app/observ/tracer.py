"""可观测层：Tracer / Span（架构 C15，Span 树构建与落库）。

**归属说明（重要）**：埋点部分在 **M1** 落地，不是 M6。
依据 03_BUILD_PLAN §2「时序硬约束 1」与风险 R-8：
> Tracer 埋点必须在 T3–T9 写 AgentLoop 时同步做；否则 M6 无数据可查。
M6 只做**查询与展示**（`/api/observability/runs/{id}/spans`、成本看板），不回头改 Loop
——为此在本文件追加**查询面** `build_span_tree()` 与 `Tracer.tree()`。

落库口径（架构 §12.1 / §12.2）：Span 扁平写 `traces` 表（`span_id` + `parent_span_id`），前端组树。
Span 树形如：run → plan → task#N → loop#M → llm / tool / compact，收口 synthesize。
安全（S2）：`attrs_json` 落库前统一过脱敏钩子。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.domain import new_id, now_ms
from app.store.repo import TraceRepository

__all__ = ["Span", "Tracer", "build_span_tree"]


def build_span_tree(rows: Iterable[Any]) -> list[dict[str, Any]]:
    """扁平 Span 行 → 嵌套树（M6 查询面）。

    输入是 `traces` 表的行（或任意具备 `span_id / parent_span_id / name / status /
    started_at / duration_ms / attrs_json` 的对象）；输出为根节点列表，每个节点带
    `children`。父节点缺失（如断线续传只取到子段）时该节点**提升为根**，
    保证不丢数据。
    """
    nodes: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        span_id = str(getattr(row, "span_id", "") or "")
        node: dict[str, Any] = {
            "span_id": span_id,
            "parent_span_id": getattr(row, "parent_span_id", None),
            "name": str(getattr(row, "name", "") or ""),
            "status": str(getattr(row, "status", "OK") or "OK"),
            "started_at": getattr(row, "started_at", None),
            "duration_ms": getattr(row, "duration_ms", None),
            "attrs": dict(getattr(row, "attrs_json", None) or {}),
            "children": [],
        }
        by_id[span_id] = node
        nodes.append(node)

    roots: list[dict[str, Any]] = []
    for node in nodes:
        parent = by_id.get(str(node["parent_span_id"] or ""))
        if parent is not None and parent is not node:
            parent["children"].append(node)
        else:
            roots.append(node)
    return roots


@dataclass(slots=True)
class Span:
    """一个已开启的 Span；退出 `with` 时自动结算耗时并落库。"""

    run_id: str
    name: str
    span_id: str
    parent_span_id: str | None
    started_at: int
    attrs: dict[str, Any] = field(default_factory=dict)
    status: str = "OK"
    duration_ms: int | None = None
    ended: bool = False

    def set(self, **attrs: Any) -> None:
        """补充/覆盖 Span 属性（如 tokens、cost、attempts、degrade）。"""
        self.attrs.update(attrs)

    @property
    def ok(self) -> bool:
        return self.status == "OK"


class Tracer:
    """Span 上下文管理器（契约：`Tracer.span(name, **attrs) -> ContextManager`）。

    - 单实例可复用于同一次 Run：`bind_run()` 切换归属，`_stack` 维护父子关系。
    - `enabled=False` 或未绑定 run_id 时只产出内存 Span、不落库（测试与降级场景）。
    - 写入是**同步**的（退出即落库），因此不存在"忘了 flush"的静默丢失。
    """

    def __init__(
        self,
        session: Session | None = None,
        *,
        run_id: str | None = None,
        enabled: bool = True,
        sanitize: Callable[[Any], Any] | None = None,
    ) -> None:
        self._repo = TraceRepository(session) if session is not None else None
        self.run_id = run_id
        self.enabled = enabled
        self._sanitize = sanitize
        self._stack: list[Span] = []
        #: 本次进程内已结算的 Span（M6 查询端与测试断言用）。
        self.spans: list[Span] = []

    # --- 绑定 ---
    def bind_run(self, run_id: str) -> None:
        self.run_id = run_id

    @property
    def current(self) -> Span | None:
        return self._stack[-1] if self._stack else None

    # --- 主入口 ---
    @contextmanager
    def span(self, name: str, **attrs: Any) -> Iterator[Span]:
        """开启一个 Span；异常时标记 `ERROR` 并继续抛出（不吞异常）。"""
        parent = self.current
        span = Span(
            run_id=self.run_id or "",
            name=name,
            span_id=new_id(),
            parent_span_id=parent.span_id if parent else None,
            started_at=now_ms(),
            attrs=dict(attrs),
        )
        self._stack.append(span)
        try:
            yield span
        except BaseException:
            span.status = "ERROR"
            raise
        finally:
            self._stack.pop()
            span.ended = True
            span.duration_ms = max(0, now_ms() - span.started_at)
            self.spans.append(span)
            self._persist(span)

    # --- 内部 ---
    def _persist(self, span: Span) -> None:
        if not self.enabled or self._repo is None or not span.run_id:
            return
        attrs = {
            key: (self._sanitize(value) if self._sanitize else value)
            for key, value in span.attrs.items()
        }
        self._repo.create(
            run_id=span.run_id,
            span_id=span.span_id,
            name=span.name,
            parent_span_id=span.parent_span_id,
            attrs_json=attrs,
            started_at=span.started_at,
            duration_ms=span.duration_ms,
            status=span.status,
        )

    # --- 查询辅助（M6 的查询面在此之上扩展）---
    def names(self) -> list[str]:
        return [s.name for s in self.spans]

    def children_of(self, span_id: str) -> list[Span]:
        return [s for s in self.spans if s.parent_span_id == span_id]

    def count(self) -> int:
        return len(self.spans)

    def count_in_db(self) -> int:
        """已落库的 Span 条数（M1 验收：Trace 中每个 Task 有完整 Loop 记录）。"""
        if self._repo is None or not self.run_id:
            return 0
        return self._repo.count_by_run(self.run_id)

    def rows_in_db(self) -> list[Any]:
        """已落库的 Span 行（按 `started_at` 升序，M6 查询面用）。"""
        if self._repo is None or not self.run_id:
            return []
        return self._repo.list_by_run(self.run_id)

    def tree(self) -> list[dict[str, Any]]:
        """已落库 Span 的**嵌套树**（Run → Plan → Task → Loop → llm/tool/compact）。"""
        return build_span_tree(self.rows_in_db())
