"""Repository 基类：极简 CRUD（M0 只用得到这些）。"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.store.db import Base

ModelT = TypeVar("ModelT", bound=Base)

__all__ = ["BaseRepository"]


class BaseRepository(Generic[ModelT]):
    """泛型 CRUD：`add` / `get` / `list` / `count` / `delete`。

    所有写操作只 `flush()`（拿到主键与默认值），提交交给外层 `session_scope()` 统一控制。
    """

    model: type[ModelT]

    def __init__(self, session: Session) -> None:
        self.session = session

    # --- 内部 ---
    def _filtered(self, **filters: Any) -> Select[tuple[ModelT]]:
        stmt = select(self.model)
        for column, value in filters.items():
            stmt = stmt.where(getattr(self.model, column) == value)
        return stmt

    # --- 读 ---
    def get(self, pk: Any) -> ModelT | None:
        return self.session.get(self.model, pk)

    def list(self, **filters: Any) -> list[ModelT]:
        return list(self.session.scalars(self._filtered(**filters)).all())

    def first(self, **filters: Any) -> ModelT | None:
        return self.session.scalars(self._filtered(**filters)).first()

    def count(self, **filters: Any) -> int:
        stmt = select(func.count()).select_from(self.model)
        for column, value in filters.items():
            stmt = stmt.where(getattr(self.model, column) == value)
        return int(self.session.scalar(stmt) or 0)

    # --- 写 ---
    def add(self, **fields: Any) -> ModelT:
        obj = self.model(**fields)
        self.session.add(obj)
        self.session.flush()
        return obj

    def delete(self, pk: Any) -> bool:
        obj = self.get(pk)
        if obj is None:
            return False
        self.session.delete(obj)
        self.session.flush()
        return True
