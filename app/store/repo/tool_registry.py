"""tool_registry 表 CRUD（工具注册表的持久化端）。

归属 M1（03_BUILD_PLAN §3 M1：`harness/tool_registry.py` 的「从 DB 加载」）。
表结构与 `ToolSpec` 由 M0 冻结（`ToolRegistryRow`），本文件只做 CRUD 与双向转换。
"""

from __future__ import annotations

from app.domain import Phase, PermissionLevel, ToolSpec
from app.store.schema import ToolRegistryRow

from .base import BaseRepository

__all__ = ["ToolRegistryRepository"]


class ToolRegistryRepository(BaseRepository[ToolRegistryRow]):
    model = ToolRegistryRow

    def get_by_name(self, name: str) -> ToolRegistryRow | None:
        """`name` 即主键，等价于 `get(name)`；显式命名便于调用方表达意图。"""
        return self.get(name)

    def list_enabled(self) -> list[ToolRegistryRow]:
        return self.list(enabled=True)

    def upsert(self, spec: ToolSpec) -> ToolRegistryRow:
        """按 `name` 主键写入或更新一条注册项（幂等）。"""
        row = self.get(spec.name)
        if row is None:
            return self.add(
                name=spec.name,
                description=spec.description,
                schema_json=dict(spec.schema_json),
                domain=spec.domain,
                phase=str(spec.phase),
                idempotent=spec.idempotent,
                timeout_ms=spec.timeout_ms,
                est_cost=spec.est_cost,
                permission_level=str(spec.permission_level),
                enabled=spec.enabled,
                version=spec.version,
            )
        row.description = spec.description
        row.schema_json = dict(spec.schema_json)
        row.domain = spec.domain
        row.phase = str(spec.phase)
        row.idempotent = spec.idempotent
        row.timeout_ms = spec.timeout_ms
        row.est_cost = spec.est_cost
        row.permission_level = str(spec.permission_level)
        row.enabled = spec.enabled
        row.version = spec.version
        self.session.flush()
        return row

    @staticmethod
    def to_spec(row: ToolRegistryRow) -> ToolSpec:
        """DB 行 → 领域契约 `ToolSpec`。"""
        return ToolSpec(
            name=row.name,
            description=row.description,
            schema_json=dict(row.schema_json or {}),
            domain=row.domain,
            phase=Phase(row.phase),
            idempotent=row.idempotent,
            timeout_ms=row.timeout_ms,
            est_cost=row.est_cost,
            permission_level=PermissionLevel(row.permission_level),
            enabled=row.enabled,
            version=row.version,
        )
