"""配置层：Pydantic Settings + 扶摇 Key 可选加载 + 脱敏钩子。

依据：HANDOFF §4.2 / §4.3 / §5.3、架构 §14.1、03_BUILD_PLAN §3 M0。

硬约束（HANDOFF §4.3 派生条款，M0 必须落地）：
- `FUYAO_API_KEY` **可选**：读不到密钥时自动切 `MOCK_DATA_MODE`，**不得启动即崩**。
- 仓库内**不得对 `api-key.txt` 产生硬依赖**：文件不存在 = 正常情况，静默降级。
- 密钥**绝不硬编码 / 不入库 / 不进日志**（S1/S2）：提供 `sanitize()` 脱敏钩子。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = [
    "Settings",
    "parse_fuyao_key",
    "sanitize",
    "mask_secret",
    "get_settings",
    "reset_settings_cache",
]

#: 项目根目录（`app/config/settings.py` → 上溯 2 级）。
PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: `api-key.txt` 的默认候选位置（不存在即跳过，非硬依赖）。可用 `FUYAO_API_KEY_FILE` 覆盖。
DEFAULT_KEY_FILE_CANDIDATES: tuple[Path, ...] = (
    PROJECT_ROOT / "docs" / "api-key.txt",
    PROJECT_ROOT / "api-key.txt",
)

#: 脱敏时用于遮蔽的最小密钥长度（过短的串不参与替换，避免误伤）。
_MIN_SECRET_LEN = 8
IFIND_NEWS_ENDPOINT = (
    "https://api-mcp.51ifind.com:8643/ds-mcp-servers/hexin-ifind-ds-news-mcp"
)


def parse_fuyao_key(text: str) -> str | None:
    """按 HANDOFF §4.2 的 5 条规则从 `api-key.txt` 内容解析扶摇纯密钥。

    规则：①跳过空行与 `#` 行 ②排除 iFinD 的 JWE 行（含 `.` 且长度 > 200）
    ③剩余行即纯密钥 ④多候选取最长 ⑤兜底：行内若仍有 `:`，取冒号后并 strip。
    """
    candidates: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # ② iFinD JWE 行
        if "." in line and len(line) > 200:
            continue
        # ⑤ 兼容旧「标签:密钥」格式
        if ":" in line:
            line = line.split(":", 1)[1].strip()
            if not line:
                continue
        candidates.append(line)
    if not candidates:
        return None
    # ④ 多候选取最长
    return max(candidates, key=len)


def _read_key_from_files(paths: Iterable[Path]) -> str | None:
    """依次尝试候选文件；任何异常都视为"无密钥"，绝不抛出。"""
    for path in paths:
        try:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8-sig", errors="ignore")
        except OSError:
            continue
        key = parse_fuyao_key(text)
        if key:
            return key
    return None


def _read_ifind_key_from_files(paths: Iterable[Path]) -> str | None:
    """可选地读取本地受保护文件中的 iFinD JWE；环境变量始终优先。"""
    for path in paths:
        try:
            if not path.is_file():
                continue
            lines = path.read_text(encoding="utf-8-sig", errors="ignore").splitlines()
        except OSError:
            continue
        for raw in lines:
            token = raw.strip()
            if len(token) > 200 and len(token.split(".")) == 5:
                return token
    return None


def mask_secret(secret: str | None) -> str:
    """把密钥渲染为可安全打印的形式：`abcd…wxyz(len=41)`。"""
    if not secret:
        return "<empty>"
    if len(secret) <= 8:
        return f"***{len(secret)}"
    return f"{secret[:4]}…{secret[-4:]}(len={len(secret)})"


def sanitize(text: object, secrets: Sequence[str] = ()) -> str:
    """脱敏钩子（S2）：遮蔽文本中的任何密钥。日志 / Trace 落库前统一走这里。"""
    result = str(text)
    for secret in secrets:
        if secret and len(secret) >= _MIN_SECRET_LEN:
            result = result.replace(secret, "***REDACTED***")
    return result


class Settings(BaseSettings):
    """M0 配置项（HANDOFF §5.3）：环境变量大小写不敏感，`.env` 缺失也不报错。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- 数据源 ---
    fuyao_base_url: str = "https://fuyao.aicubes.cn"
    #: 可选；缺失时由 `load_settings()` 尝试从 api-key.txt 补，仍无则降级 Mock（§4.3）。
    fuyao_api_key: str | None = None
    fuyao_timeout_ms: int = Field(default=8000, gt=0)

    # --- 存储 ---
    database_url: str = "sqlite:///./data/buddy.db"

    # --- 降级备源（iFinD MCP，M2；架构 §14.1）---
    #: **默认启用，但仅作降级备源**（03_BUILD_PLAN §0 表 L28）；缺 endpoint/key 时 `enabled` 为假，
    #: 备源调用抛 `NotEnabled` → 降级阶梯第 ⑦ 步产出 `degradation_notice`。
    ifind_mcp_enabled: bool = True
    ifind_mcp_endpoint: str = IFIND_NEWS_ENDPOINT
    ifind_mcp_key: str | None = None

    # --- 数据工具（M2；架构 §9.3 第 ④ 步 / §14.1）---
    #: 工具结果 TTL 缓存秒数。降级时可返回「缓存 + 过期标注」（架构 §10.3）。
    cache_ttl_s: float = Field(default=60.0, gt=0)

    # --- 模型（M1，架构 §14.1 / 03_BUILD_PLAN §3 M1；`CACHE_TTL_S` 归属 M2，不在此）---
    llm_provider_primary: str = "openai-compatible"
    llm_base_url_primary: str = ""
    #: 可选；缺失时 ModelRouter 直接返回降级结果（触发「降级研判」），**不得启动即崩**。
    llm_api_key_primary: str | None = None
    llm_model_primary: str = ""
    #: 小模型档位（Compactor / Clarifier 等轻任务）；空则回落到 `llm_model_primary`。
    llm_model_small: str = ""
    llm_timeout_ms: int = Field(default=45000, gt=0)
    #: 额外 failover 次数（架构 §10.1：超时 → 切换 → 熔断 → 降级）。
    llm_max_failover: int = Field(default=2, ge=0)
    #: LLM 单价（人民币 / 百万 token），用于把上游 `usage` 折算成 `llm_calls.cost`。
    #: **未配置（0）即不做计价、成本记 0——不编造单价**：口径唯一来源仍是 `llm_calls`
    #: 聚合（HANDOFF §6.2），本项只决定「单次调用写进去的 cost 是多少」。填了真实单价后
    #: `runs.cost_total` 与 `budget_max_cost_cny` 成本闸门才真正生效。
    llm_price_input_per_mtok_cny: float = Field(default=0.0, ge=0)
    llm_price_output_per_mtok_cny: float = Field(default=0.0, ge=0)

    # --- 预算与停止规则（M3；架构 §14.1）---
    #: 单次 Run 的最大 Step 数（8 条停止规则之 MAX_STEPS 与预算前置检查共用）。
    budget_max_steps: int = Field(default=20, gt=0)
    #: 单次 Run 的最大 token 消耗（唯一数据来源仍是 `llm_calls` 聚合，见 §6.2）。
    budget_max_tokens: int = Field(default=100000, gt=0)
    #: 单次 Run 的最大 LLM 成本（人民币）。
    budget_max_cost_cny: float = Field(default=2.0, gt=0)
    #: 单次 Run 的最大墙钟时长（秒）。
    budget_max_wall_time_s: float = Field(default=180.0, gt=0)
    #: 循环检测：同一动作签名连续重复 N 次 → `StopReason.LOOP_DETECT`。
    loop_detect_repeat: int = Field(default=3, gt=0)
    #: 无进展检测：连续 N 个动作未产出新证据 → `StopReason.NO_PROGRESS`。
    no_progress_steps: int = Field(default=4, gt=0)

    # --- 审批（HITL，M3；架构 §6.1）---
    #: 审批挂起的最长等待时长（秒），超时置 `EXPIRED`（默认 30min）。
    approval_timeout_s: float = Field(default=1800.0, gt=0)

    # --- 上下文与记忆（M4；架构 §7 / §8 / §14.1）---
    #: 上下文 token 估算超过「预算上限 × 本比例」即触发压缩（架构 §7.1，默认 60%）。
    compact_threshold_ratio: float = Field(default=0.6, gt=0, le=1.0)
    #: 记忆召回条数上限（架构 §8：长期记忆召回「按相关性截断」，默认 5）。
    memory_recall_top_k: int = Field(default=5, gt=0)
    #: 记忆召回的时间衰减半衰期（小时；架构 §8 默认 72h）。
    memory_decay_halflife_h: float = Field(default=72.0, gt=0)

    # --- 产物与动态 UI（M5；架构 §11 / 03_BUILD_PLAN §3 M5）---
    #: 单 Run 单次渲染的 UIBlock 上限（超出截断，防「动态 UI 无限增长」）。
    ui_block_max: int = Field(default=200, gt=0)
    #: 严格渲染：非法组件 / 未实现组件 / 非法 props / FACT 无引用一律降级为 FallbackJsonCard。
    render_strict: bool = True

    # --- 可观测与合规（M6；架构 §12 / 03_BUILD_PLAN §3 M6）---
    #: Trace 落库开关；关掉后 `Tracer` 只产出内存 Span、不写 `traces` 表。
    trace_enabled: bool = True
    #: 合规严格模式：True 时 Guard 的任何发现即拦截；False 时仅**结构性**缺陷
    #: （无引用 FACT / 推断无切换条件）拦截，文本类红线降为提示。
    guard_strict: bool = True
    #: 隐私脱敏正则（逗号分隔）；为空表示使用 `observ.sanitize.DEFAULT_SANITIZE_PATTERNS`。
    sanitize_patterns: str = ""

    # --- 应用 ---
    app_env: str = "dev"
    # 有真实 Key 时缺省走真实通道；明确开启时仍可离线演示。
    mock_data_mode: bool = False

    #: 非契约配置：允许覆盖 `api-key.txt` 位置（不设即用默认候选，缺失即跳过）。
    fuyao_api_key_file: str | None = None

    @property
    def is_mock(self) -> bool:
        """实际数据通道：显式 Mock，或**无密钥时强制 Mock**（不得崩）。"""
        return self.mock_data_mode or not self.fuyao_api_key

    @property
    def is_llm_configured(self) -> bool:
        """LLM 主 provider 是否可用；不可用时 `ModelRouter` 走降级路径而非抛错。"""
        return bool(self.llm_api_key_primary and self.llm_base_url_primary and self.llm_model_primary)

    @property
    def secret_values(self) -> list[str]:
        """已知密钥清单（供 `sanitize()` 一次性遮蔽；S1/S2）。"""
        return [s for s in (self.fuyao_api_key, self.llm_api_key_primary, self.ifind_mcp_key) if s]

    @property
    def sanitize_pattern_list(self) -> list[str]:
        """`SANITIZE_PATTERNS`（逗号分隔）解析为列表。

        返回空列表表示「未配置」——由 `observ.sanitize` 回落到内置隐私集，
        因此本层不导入 observ，避免循环依赖。
        """
        raw = str(self.sanitize_patterns or "").strip()
        return [part.strip() for part in raw.split(",") if part.strip()]

    @property
    def key_file_candidates(self) -> tuple[Path, ...]:
        if self.fuyao_api_key_file:
            return (Path(self.fuyao_api_key_file),)
        return DEFAULT_KEY_FILE_CANDIDATES

    def sanitize(self, text: object) -> str:
        """用本配置已知的全部密钥做脱敏（S2：日志 / Trace 落库前统一走这里）。"""
        return sanitize(text, secrets=self.secret_values)


def load_settings() -> Settings:
    """构造配置并完成「无密钥 → 自动降级 Mock」的仲裁。"""
    settings = Settings()
    if not settings.fuyao_api_key:
        settings.fuyao_api_key = _read_key_from_files(settings.key_file_candidates)
    if not settings.fuyao_api_key:
        # 无密钥：强制 Mock，绝不抛异常（HANDOFF §4.3）。
        settings.mock_data_mode = True
    if not settings.ifind_mcp_key and settings.ifind_mcp_enabled:
        settings.ifind_mcp_key = _read_ifind_key_from_files(settings.key_file_candidates)
    if settings.ifind_mcp_enabled and settings.ifind_mcp_key and not settings.ifind_mcp_endpoint:
        settings.ifind_mcp_endpoint = IFIND_NEWS_ENDPOINT
    return settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程内单例配置；测试可用 `reset_settings_cache()` 重置。"""
    return load_settings()


def reset_settings_cache() -> None:
    """清空 `get_settings()` 缓存（测试用）。"""
    get_settings.cache_clear()
