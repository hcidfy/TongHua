"""数据层：熔断器（架构 §9.3 第 ⑤ 步；03_BUILD_PLAN §3 M2）。

M1 在 `harness/tool_executor.py` 内置了最小实现；M2 由本模块**接管**，接口保持逐字一致：

    Breaker(*, threshold=3, cooldown_s=30.0).is_open(key) -> bool
    Breaker(*, threshold=3, cooldown_s=30.0).record_success(key) -> None
    Breaker(*, threshold=3, cooldown_s=30.0).record_failure(key) -> None

**只约束主源**：执行器第 ⑥ 步（备源 iFinD）在判定 `is_open()` 之后，不因主源熔断而跳过备源
（M1 已修——否则备用模型/备源会被主源熔断器连带拦死，见 HANDOFF 教训）。

加固点：加锁（跨 Lane 并行安全）、`reset()` 便于测试与运维恢复、`state()` 便于 Trace 观测。
"""

from __future__ import annotations

import threading
import time

__all__ = ["Breaker", "STATE_CLOSED", "STATE_OPEN"]

STATE_CLOSED = "CLOSED"
STATE_OPEN = "OPEN"


class Breaker:
    """按 `key`（工具名）维度的连续失败熔断器。"""

    def __init__(self, *, threshold: int = 3, cooldown_s: float = 30.0) -> None:
        if threshold < 1:
            raise ValueError("threshold 必须 >= 1")
        if cooldown_s <= 0:
            raise ValueError("cooldown_s 必须为正数")
        self.threshold = int(threshold)
        self.cooldown_s = float(cooldown_s)
        self._lock = threading.Lock()
        self._failures: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}

    def is_open(self, key: str) -> bool:
        """开路中返回 True；冷却期已过则**自动复位**（半开→放行一次）。"""
        with self._lock:
            return self._is_open_locked(key)

    def record_success(self, key: str) -> None:
        with self._lock:
            self._failures[key] = 0
            self._opened_at.pop(key, None)

    def record_failure(self, key: str) -> None:
        with self._lock:
            count = self._failures.get(key, 0) + 1
            self._failures[key] = count
            if count >= self.threshold:
                self._opened_at[key] = time.monotonic()

    # --- 观测 / 运维 ---
    def state(self, key: str) -> str:
        """`CLOSED` / `OPEN`（供 Trace attrs 与运维面板）。"""
        return STATE_OPEN if self.is_open(key) else STATE_CLOSED

    def failures(self, key: str) -> int:
        with self._lock:
            return self._failures.get(key, 0)

    def reset(self, key: str | None = None) -> None:
        """复位单个 key（或全部，`key=None`）。"""
        with self._lock:
            if key is None:
                self._failures.clear()
                self._opened_at.clear()
                return
            self._failures.pop(key, None)
            self._opened_at.pop(key, None)

    # --- 内部 ---
    def _is_open_locked(self, key: str) -> bool:
        opened = self._opened_at.get(key)
        if opened is None:
            return False
        if time.monotonic() - opened >= self.cooldown_s:
            # 冷却结束：复位计数，放行一次（失败会重新计数并再开路）
            self._opened_at.pop(key, None)
            self._failures[key] = 0
            return False
        return True
