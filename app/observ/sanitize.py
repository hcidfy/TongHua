"""可观测层的脱敏构件（03_BUILD_PLAN §3 M6：`observ/sanitize.py`）。

职责：**日志与 Trace 脱敏（Key / 隐私）**。

两类遮蔽，先密钥后隐私：

1. **密钥**——复用 `app/config/settings.py` 的 `sanitize()` / `mask_secret()`（S1/S2：
   密钥绝不入库、不进日志、不进 Trace）；密钥清单来自 `Settings.secret_values`；
2. **隐私**——`DEFAULT_SANITIZE_PATTERNS` 内置正则（邮箱 / 手机号 / 身份证 / 银行卡 /
   `sk-` 形态的令牌），可用配置 `SANITIZE_PATTERNS`（逗号分隔）覆盖。

**确定性 / 离线**：纯字符串变换，不触网、不读文件。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any

from app.config.settings import mask_secret, sanitize

__all__ = [
    "REDACTED",
    "DEFAULT_SANITIZE_PATTERNS",
    "Sanitizer",
    "sanitize_text",
    "sanitizer_from_settings",
]

#: 统一遮蔽标记（与 `app/config/settings.sanitize()` 一致）。
REDACTED = "***REDACTED***"

#: 内置隐私正则（Key 之外的 PII）。均可被 `SANITIZE_PATTERNS` 覆盖。
#:
#: 数字类模式统一加 **十六进制边界** `(?<![0-9A-Fa-f]) ... (?![0-9A-Fa-f])`：
#: 实体主键是 32 位无连字符 UUID（`new_id()`），纯数字边界会把 UUID 里的
#: 长数字串误判成银行卡并改写实体 id——这是必须避免的「脱敏副作用」。
DEFAULT_SANITIZE_PATTERNS: tuple[str, ...] = (
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}",  # 邮箱
    r"(?<![0-9A-Fa-f])1[3-9]\d{9}(?![0-9A-Fa-f])",  # 中国大陆手机号
    r"(?<![0-9A-Fa-f])[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?![0-9A-Fa-f])",  # 身份证
    r"(?<![0-9A-Fa-f])\d{16,19}(?![0-9A-Fa-f])",  # 银行卡
    r"\bsk-[A-Za-z0-9_\-]{8,}\b",  # OpenAI 风格令牌
)


def sanitize_text(
    text: object,
    *,
    secrets: Sequence[str] = (),
    patterns: Sequence[str] | None = None,
) -> str:
    """先遮密钥、再遮隐私；`patterns=None` 用内置集，`()` 表示不做隐私遮蔽。"""
    result = sanitize(text, secrets=secrets)
    for pattern in DEFAULT_SANITIZE_PATTERNS if patterns is None else patterns:
        try:
            result = re.sub(pattern, REDACTED, result)
        except re.error:  # 非法正则由配置引入时静默跳过，绝不因脱敏而崩
            continue
    return result


class Sanitizer:
    """可调用脱敏器（`__call__` 使其可直接作为 `Tracer(sanitize=...)` 的钩子）。"""

    def __init__(
        self,
        *,
        secrets: Iterable[str] = (),
        patterns: Sequence[str] | None = None,
    ) -> None:
        self.secrets: list[str] = [s for s in secrets if s]
        self.patterns: tuple[str, ...] = tuple(
            DEFAULT_SANITIZE_PATTERNS if patterns is None else patterns
        )
        self._compiled: list[re.Pattern[str]] = []
        for pattern in self.patterns:
            try:
                self._compiled.append(re.compile(pattern))
            except re.error:  # pragma: no cover - 防御性：非法正则不参与遮蔽
                continue

    def __call__(self, text: object) -> str:
        return self.mask(text)

    def mask(self, text: object) -> str:
        """按「先密钥、后隐私」的顺序遮蔽（与 `sanitize_text` 同口径）。"""
        result = sanitize(text, secrets=self.secrets)
        for compiled in self._compiled:
            result = compiled.sub(REDACTED, result)
        return result

    def mask_any(self, value: Any) -> Any:
        """按值类型脱敏：**字符串**走 `mask()`，其余（int/float/bool/None/容器）原样返回。

        用作 `Tracer(sanitize=...)` 的钩子——Trace 属性里既有文本也有数值
        （`attempts` / `evidences_used` / `limit` 等），无条件 `str()` 会把数值型
        属性退化成字符串，破坏看板数值语义。
        """
        if isinstance(value, str):
            return self.mask(value)
        if isinstance(value, bytes):
            return self.mask(value.decode("utf-8", "ignore"))
        return value

    def mask_secret(self, secret: str | None) -> str:
        """把密钥渲染为可安全打印的形式（`abcd…wxyz(len=41)`）。"""
        return mask_secret(secret)


def sanitizer_from_settings(settings: Any = None) -> Sanitizer:
    """按注入的 `Settings` 装配脱敏器（测试注入 `offline_settings()` 亦适用）。

    - `secrets` ← `Settings.secret_values`（Keys）；
    - `patterns` ← `Settings.sanitize_pattern_list`；为空则用内置隐私集。
    """
    if settings is None:
        return Sanitizer()
    secrets = list(getattr(settings, "secret_values", []) or [])
    configured = list(getattr(settings, "sanitize_pattern_list", []) or [])
    return Sanitizer(secrets=secrets, patterns=configured if configured else None)
