"""6 个 AI 角色的系统提示词（PRD §5 / 03_BUILD_PLAN §3 M1 的 `harness/prompts/*.md`）。

`load_prompt(role)` 是唯一读取入口：`ModelRouter` 在每次 `complete(role, ...)` 时
自动把对应角色的系统提示词前置到消息列表，避免调用方各写一份。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

__all__ = ["PROMPT_DIR", "ROLES", "load_prompt"]

#: 提示词目录（`app/harness/prompts/`）。
PROMPT_DIR = Path(__file__).resolve().parent

#: 契约：6 个角色（与 `model_router.AI_ROLES` 同名同序）。
ROLES: tuple[str, ...] = (
    "Clarifier",
    "Planner",
    "Reasoner",
    "Compactor",
    "Synthesizer",
    "Guard",
)

_FALLBACK = "你是投资研究助手。只依据给定证据作答，不得给出买卖建议或涨跌预测。"


def _prompt_path(role: str) -> Path:
    return PROMPT_DIR / f"{role.strip().lower()}.md"


@lru_cache(maxsize=16)
def load_prompt(role: str) -> str:
    """读取角色提示词；文件缺失时回落为通用合规提示（不抛异常）。"""
    path = _prompt_path(role)
    if not path.is_file():
        return _FALLBACK
    text = path.read_text(encoding="utf-8").strip()
    return text or _FALLBACK
