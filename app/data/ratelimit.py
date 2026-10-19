"""数据层：令牌桶限流（架构 §9.3 第 ③ 步；03_BUILD_PLAN §3 M2）。

M1 在 `harness/tool_executor.py` 内置了最小实现；M2 由本模块**接管**，接口保持逐字一致：

    RateLimiter(*, rate_per_s=5.0, burst=5, max_wait_s=0.1).acquire(key) -> bool

与 M1 版的差异（均为实现加固，非契约变更）：

- **加锁**：会话管理层是「同 Lane 串行、跨 Lane 并行」（架构 §6.2），因此令牌桶必须线程安全；
- 参数非法时 `ValueError` 提前暴露（避免 `rate=0` 造成的除零/永久限流）。

`acquire()` 返回 False 时，执行器按第 ③ 步**直接降级**（不阻塞主链路）；扶摇 `4001` 的退避由
执行器的重试循环（指数退避 ×2）承担，本类只做**本地节流**。
"""

from __future__ import annotations

import threading
import time

__all__ = ["RateLimiter"]


class RateLimiter:
    """按 `key`（工具名）维度的令牌桶。"""

    def __init__(self, *, rate_per_s: float = 5.0, burst: int = 5, max_wait_s: float = 0.1) -> None:
        if rate_per_s <= 0:
            raise ValueError("rate_per_s 必须为正数")
        self.rate = float(rate_per_s)
        self.burst = max(1, int(burst))
        self.max_wait_s = max(0.0, float(max_wait_s))
        self._lock = threading.Lock()
        self._tokens: dict[str, float] = {}
        self._stamp: dict[str, float] = {}

    def acquire(self, key: str) -> bool:
        """尝试取一枚令牌；可取返回 True，桶空则**最多等 `max_wait_s`** 后返回 False。"""
        token = self._take(key)
        if token:
            return True
        wait = self._wait_s(key)
        if wait > 0:
            time.sleep(wait)
            return self._take(key)
        return False

    # --- 内部 ---
    def _take(self, key: str) -> bool:
        with self._lock:
            now = time.monotonic()
            tokens = self._refill(key, now)
            if tokens >= 1.0:
                self._tokens[key] = tokens - 1.0
                self._stamp[key] = now
                return True
            return False

    def _wait_s(self, key: str) -> float:
        with self._lock:
            tokens = self._refill(key, time.monotonic())
            if tokens >= 1.0:
                return 0.0
            return min(self.max_wait_s, (1.0 - tokens) / self.rate)

    def _refill(self, key: str, now: float) -> float:
        """按流逝时间补充令牌（调用方持锁）。"""
        tokens = self._tokens.get(key, float(self.burst))
        last = self._stamp.get(key, now)
        self._stamp[key] = now
        return min(float(self.burst), tokens + (now - last) * self.rate)
