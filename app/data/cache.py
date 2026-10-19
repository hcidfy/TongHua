"""数据层：TTL 缓存（架构 §9.3 第 ④ 步；03_BUILD_PLAN §3 M2）。

M1 在 `harness/tool_executor.py` 内置了最小实现；M2 由本模块**接管**，接口保持逐字一致：

    TTLCache(*, ttl_s=60.0).get(key) -> ToolResult | None
    TTLCache(*, ttl_s=60.0).set(key, value) -> None

TTL 默认值由调用方从 `Settings.cache_ttl_s`（`CACHE_TTL_S`）传入（架构 §14.1）。

新增能力（服务架构 §10.3「数据过期 → 用缓存 + 标注 staleness」）：

- `get_stale(key) -> (value, age_ms) | None`：**无论是否过期**都返回，供执行器第 ⑦ 步降级时
  取「过期缓存」并在 `degrade_reason` / `staleness_ms` 上显式标注（C4：不得静默失败）。

缓存键只含「工具 + 入参」（`harness/tool_executor._cache_key_for`），**不含** run_id / task_id
——否则同一次 Run 内的重复调用会先被第 ② 步幂等命中，第 ④ 步永不生效（M1 已修）。
"""

from __future__ import annotations

import threading
import time
from typing import Any

__all__ = ["TTLCache"]


class TTLCache:
    """线程安全的 TTL 缓存（值为 `ToolResult`）。"""

    def __init__(self, *, ttl_s: float = 60.0) -> None:
        if ttl_s <= 0:
            raise ValueError("ttl_s 必须为正数")
        self.ttl_s = float(ttl_s)
        self._lock = threading.Lock()
        self._store: dict[str, tuple[float, Any]] = {}

    # --- 契约接口（与 M1 内置实现一致）---
    def get(self, key: str) -> Any | None:
        """命中且未过期返回缓存值；**过期返回 None 但保留条目**。

        不过期即淘汰会让执行器第 ⑦ 步的「过期缓存兜底」永远拿不到东西（第 ④ 步先调 `get()`，
        条目已被就地删掉）——因此过期条目留到被 `set()` 覆盖或 `purge()` 清空
        （架构 §10.3：「数据过期 → 用缓存 + 标注 staleness」）。
        """
        with self._lock:
            hit = self._store.get(key)
            if hit is None:
                return None
            stamp, value = hit
            if time.monotonic() - stamp > self.ttl_s:
                return None
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._store[key] = (time.monotonic(), value)

    # --- 降级用扩展 ---
    def get_stale(self, key: str) -> tuple[Any, int] | None:
        """**无论是否过期**都返回 `(value, age_ms)`；无该键返回 None。"""
        with self._lock:
            hit = self._store.get(key)
            if hit is None:
                return None
            stamp, value = hit
            return value, int((time.monotonic() - stamp) * 1000)

    def age_ms(self, key: str) -> int | None:
        """该键的存入时长（毫秒）；无该键返回 None。"""
        stale = self.get_stale(key)
        return None if stale is None else stale[1]

    def purge(self) -> int:
        """清空并返回被清除的条目数。"""
        with self._lock:
            count = len(self._store)
            self._store.clear()
            return count

    def __len__(self) -> int:  # pragma: no cover - 便于调试
        with self._lock:
            return len(self._store)
