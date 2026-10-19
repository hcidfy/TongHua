"""Harness 内核：工具注册表（架构 C4；03_BUILD_PLAN §3 M1）。

职责：`ToolSpec` 注册 / 查询 / 从 DB 加载 / **phase 声明**（EXPLORE / COMMIT）。

契约（03_BUILD_PLAN §3 M1）::

    ToolRegistry.register(spec) / list() / get(name)

**实现绑定说明（重要）**：`ToolSpec` 契约（HANDOFF §6.1）**没有 `backing` 字段**
（架构 §9.1 的示例含 `backing`，但契约表为准），因此「工具怎么执行」不写在 spec 里，
而由运行期 `bind(name, fn)` 另行绑定：

- spec = 对外契约（给 LLM 看的 schema、phase、幂等、成本、权限）；
- impl = 真正的取数函数（M2 的 `tools/impl/*.py` 是它的来源；M1 测试用桩函数绑定）。

两者分离还带来一个好处：`phase` 由循环内核判定（EXPLORE 只探索、COMMIT 才产出），
而循环内核**不认识任何业务类型**（架构 §1.2）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from sqlalchemy.orm import Session

from app.domain import Phase, ToolSpec
from app.store.repo import ToolRegistryRepository

__all__ = ["ToolRegistry", "ToolImpl"]

#: 工具实现签名：`impl(args: dict) -> ToolResult`。
ToolImpl = Callable[[dict[str, Any]], Any]


class ToolRegistry:
    """`ToolSpec` 的内存注册表 + `tool_registry` 表持久化端。

    - 有 `session` 时，`register()` 会同步 upsert 到 DB（`name` 为主键）；
    - `load_from_db()` 供进程重启后从表回填（M3 恢复前的铺垫）。
    """

    def __init__(self, session: Session | None = None) -> None:
        self._session = session
        self._repo = ToolRegistryRepository(session) if session is not None else None
        self._specs: dict[str, ToolSpec] = {}
        self._impls: dict[str, ToolImpl] = {}
        self._fallbacks: dict[str, ToolImpl] = {}

    # --- 注册（契约方法）---
    def register(self, spec: ToolSpec) -> ToolSpec:
        """注册 / 更新一条工具声明（按 `name` 幂等）。"""
        self._specs[spec.name] = spec
        if self._repo is not None:
            self._repo.upsert(spec)
        return spec

    def register_many(self, specs: Iterable[ToolSpec]) -> int:
        """批量注册，返回注册条数。"""
        count = 0
        for spec in specs:
            self.register(spec)
            count += 1
        return count

    def bind(self, name: str, impl: ToolImpl, *, fallback: ToolImpl | None = None) -> None:
        """绑定工具实现（可另绑一个备源实现，对应降级阶梯第 6 步）。"""
        self._impls[name] = impl
        if fallback is not None:
            self._fallbacks[name] = fallback

    # --- 查询（契约方法）---
    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def has(self, name: str) -> bool:
        return name in self._specs

    def list(
        self,
        *,
        enabled_only: bool = True,
        phase: Phase | None = None,
    ) -> list[ToolSpec]:
        """返回工具声明（`phase` 过滤供循环内核区分 EXPLORE / COMMIT）。"""
        specs = list(self._specs.values())
        if enabled_only:
            specs = [s for s in specs if s.enabled]
        if phase is not None:
            specs = [s for s in specs if s.phase == phase]
        return sorted(specs, key=lambda s: s.name)

    def names(self, *, enabled_only: bool = True, phase: Phase | None = None) -> list[str]:
        """工具名清单——Planner / AgentLoop 的**字段白名单**来源（架构 §10.2）。"""
        return [s.name for s in self.list(enabled_only=enabled_only, phase=phase)]

    def get_impl(self, name: str) -> ToolImpl | None:
        return self._impls.get(name)

    def get_fallback(self, name: str) -> ToolImpl | None:
        return self._fallbacks.get(name)

    # --- DB 回填 ---
    def load_from_db(self) -> int:
        """从 `tool_registry` 表回填内存（覆盖同名 spec），返回条数。"""
        if self._repo is None:
            return 0
        rows = self._repo.list(enabled=True)
        for row in rows:
            spec = ToolRegistryRepository.to_spec(row)
            self._specs[spec.name] = spec
        return len(rows)
