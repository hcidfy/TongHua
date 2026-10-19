"""Harness 内核：工具执行器（架构 C5、§9.3；03_BUILD_PLAN §3 M1）。

契约（03_BUILD_PLAN §3 M1）::

    ToolExecutor.invoke(run_id, task_id, call) -> ToolResult | DegradedResult

**8 步降级阶梯**（架构 §9.3，顺序严格）：

1. 参数校验（JSON Schema，轻量）
2. 幂等检查（`tool_calls.dedupe_key` 命中 → 直接复用，**不重复副作用**）
3. 限流检查（令牌桶 + `429/4001` 退避）
4. 缓存检查（TTL 内命中 → 返回并标记 `cached`）
5. 主源调用（扶摇）→ 失败：重试（指数退避 ×2）→ 熔断器计数
6. 备源调用（iFinD，若配置且已启用）→ 失败：进入降级
7. 降级（返回 `DegradedResult`：可用缓存 + `degrade_reason`）
8. 质量元数据注入（`as_of` / `staleness_ms` / `confidence` / `caliber`）

限流 / 缓存 / 熔断三个协作件在 M2 已由 `data/ratelimit.py` / `data/cache.py` / `data/breaker.py`
接管（接口不变，仍可经构造参数注入替换，例如注入「永远无令牌」的限流器来测第 ③ 步）。
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field, replace
from typing import Any

from sqlalchemy.orm import Session

from app.data import Breaker, RateLimiter, TTLCache
from app.data.errors import FuyaoError, NotReady, RateLimited, SourceDown, SourceTimeout
from app.data.quality import IMPACT_HIGH, IMPACT_MEDIUM, degradation_notice
from app.domain import new_id, now_ms
from app.observ import Tracer
from app.store.repo import EvidenceRepository, ToolCallRepository

from .tool_registry import ToolRegistry

__all__ = [
    "ToolInvocation",
    "ToolResult",
    "DegradedResult",
    "ToolExecutor",
    "RETRYABLE_ERRORS",
    "dedupe_key_for",
]

#: 可重试的领域异常（架构 §9.3 第 5 步：重试 → 熔断）。
RETRYABLE_ERRORS: tuple[type[FuyaoError], ...] = (
    RateLimited,  # 4001（等价 HTTP 429）
    NotReady,  # 3002
    SourceTimeout,  # 5002
    SourceDown,  # 5003
)


def dedupe_key_for(run_id: str, task_id: str, tool_name: str, args: dict[str, Any]) -> str:
    """`hash(run_id + task_id + tool_name + args)`（架构 §6.2）——恢复时不重复调用的唯一依据。"""
    payload = json.dumps(args or {}, sort_keys=True, ensure_ascii=False, default=str)
    raw = f"{run_id}|{task_id}|{tool_name}|{payload}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:32]


def _cache_key_for(tool_name: str, args: dict[str, Any]) -> str:
    """TTL 缓存键：只含「工具 + 入参」，**不含** run_id / task_id（§9.3 第 4 步）。"""
    payload = json.dumps(args or {}, sort_keys=True, ensure_ascii=False, default=str)
    return f"{tool_name}:{hashlib.sha1(payload.encode('utf-8')).hexdigest()[:16]}"


@dataclass(slots=True)
class ToolInvocation:
    """一次工具调用的入参（轻量请求对象，非契约模型）。"""

    tool_name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolResult:
    """成功（或带降级说明但仍可用）的工具结果，自带质量元数据（C3）。"""

    tool_name: str
    data: Any = None
    source: str = ""
    interface: str = ""
    as_of: int | None = None
    unit: str | None = None
    caliber: str | None = None
    raw_ref: str = ""
    confidence: float | None = None
    staleness_ms: int | None = None
    degrade_reason: str | None = None
    attempts: int = 1
    latency_ms: int | None = None
    reused: bool = False
    cached: bool = False
    tool_call_id: str | None = None

    @property
    def ok(self) -> bool:
        return self.degrade_reason is None

    @property
    def notice(self) -> dict[str, Any] | None:
        """降级（但仍可用）时的 UI 白名单块（架构 §11.2）；正常结果返回 None。"""
        if self.degrade_reason is None:
            return None
        return degradation_notice(self.degrade_reason, scope=self.tool_name, impact=IMPACT_MEDIUM)


@dataclass(slots=True)
class DegradedResult:
    """降级结果（第 7 步）：拿不到主源数据，必须显式带原因（C4：不得静默出"正常"结论）。"""

    tool_name: str
    reason: str
    error_code: int | None = None
    attempts: int = 0
    cached: bool = False
    data: Any = None
    as_of: int | None = None
    tool_call_id: str | None = None

    @property
    def ok(self) -> bool:
        return False

    @property
    def notice(self) -> dict[str, Any]:
        """降级的 UI 白名单块（架构 §11.2 `degradation_notice`，对应 SSE `degrade`，§13.1）。"""
        return degradation_notice(self.reason, scope=self.tool_name, impact=IMPACT_HIGH)


class ToolExecutor:
    """按 8 步阶梯执行一次工具调用，并落 `tool_calls` 审计。"""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        session: Session | None = None,
        settings: Any = None,
        tracer: Tracer | None = None,
        max_attempts: int = 3,
        base_backoff_s: float = 0.05,
        limiter: Any = None,
        cache: Any = None,
        breaker: Any = None,
    ) -> None:
        self.registry = registry
        self.settings = settings
        self.tracer = tracer
        self.max_attempts = max(1, max_attempts)
        self.base_backoff_s = base_backoff_s
        self._calls = ToolCallRepository(session) if session is not None else None
        self._evidences = EvidenceRepository(session) if session is not None else None
        # 三件协作件（M2 起由 `app/data/*.py` 提供）：TTL 取自 `CACHE_TTL_S`（架构 §14.1）。
        self.limiter = limiter if limiter is not None else RateLimiter()
        self.cache = (
            cache
            if cache is not None
            else TTLCache(ttl_s=float(getattr(settings, "cache_ttl_s", None) or 60.0))
        )
        self.breaker = breaker if breaker is not None else Breaker()

    # --- 契约入口 ---
    def invoke(
        self,
        run_id: str,
        task_id: str,
        call: ToolInvocation,
        *,
        tracer: Tracer | None = None,
    ) -> ToolResult | DegradedResult:
        """执行一次工具调用；**任何失败都以 `DegradedResult` 返回，不抛异常**。"""
        active_tracer = tracer or self.tracer
        if active_tracer is not None:
            args_hash = dedupe_key_for(run_id, task_id, call.tool_name, call.args)[:12]
            with active_tracer.span("tool", args_hash=args_hash) as span:
                # 架构 §12.1：tool Span 的 attrs 为 `name/args_hash/status/attempts/latency/degrade`。
                # `name` 与 `span(name, **attrs)` 的形参同名，故进入 Span 后再 set()（避免重复传参）。
                span.set(name=call.tool_name)
                result = self._invoke(run_id, task_id, call)
                span.set(
                    status="OK" if result.ok else "DEGRADED",
                    attempts=result.attempts,
                    latency=getattr(result, "latency_ms", None),
                    reused=bool(getattr(result, "reused", False)),
                    degrade=None if result.ok else getattr(result, "reason", None),
                )
                return result
        return self._invoke(run_id, task_id, call)

    # --- 阶梯本体 ---
    def _invoke(self, run_id: str, task_id: str, call: ToolInvocation) -> ToolResult | DegradedResult:
        tool_name = call.tool_name

        # ① 参数校验（+ 注册与启用检查）
        spec = self.registry.get(tool_name)
        if spec is None:
            return DegradedResult(tool_name=tool_name, reason=f"工具未注册：{tool_name}")
        if not spec.enabled:
            return DegradedResult(tool_name=tool_name, reason=f"工具已停用：{tool_name}")
        schema_error = _validate_args(spec.schema_json, call.args)
        if schema_error is not None:
            return DegradedResult(tool_name=tool_name, reason=f"参数校验失败：{schema_error}")

        # ② 幂等检查
        key = dedupe_key_for(run_id, task_id, tool_name, call.args)
        if self._calls is not None:
            existing = self._calls.get_by_dedupe_key(key)
            if existing is not None and existing.status == "OK":
                return self._reuse(existing.id, tool_name)

        # 建审计行（`dedupe_key` UNIQUE 由 DB 兜底）
        row = None
        if self._calls is not None:
            row = self._calls.create(
                run_id=run_id,
                task_id=task_id,
                tool_name=tool_name,
                args_json=call.args,
                dedupe_key=key,
                idempotent=spec.idempotent,
                status="PENDING",
            )
        call_id = row.id if row is not None else None

        # ③ 限流检查
        if not self.limiter.acquire(tool_name):
            return self._degrade(
                call_id, tool_name, "触发本地限流（令牌桶耗尽）", attempts=0, cached=False
            )

        # ④ 缓存检查（键只含「工具 + 入参」，因此跨 Task / 跨 Run 的相同取数可命中；
        #    若把 task_id 并入键，则任何重复调用都会先被 ② 幂等命中，第 4 步永不生效）
        cache_key = _cache_key_for(tool_name, call.args)
        cached = self.cache.get(cache_key)
        if cached is not None:
            result: ToolResult = cached
            result.reused = True
            result.cached = True
            result.attempts = 0  # 本次调用未打主源（与 `_mark(..., attempts=0)` 对齐）
            result.tool_call_id = call_id
            self._mark(call_id, status="OK", attempts=0, latency_ms=0)
            return result

        # ⑤ 主源调用（重试 ×2 + 熔断计数）
        impl = self.registry.get_impl(tool_name)
        if impl is None:
            return self._degrade(call_id, tool_name, f"工具实现未绑定：{tool_name}", attempts=0)

        attempts = 0
        last_error: FuyaoError | None = None
        started = now_ms()
        while attempts < self.max_attempts:
            if self.breaker.is_open(tool_name):
                last_error = FuyaoError(f"熔断器开路：{tool_name}", code=5003)
                break
            # `timeout_ms`（§3.1 每工具配置）是主源整体的**墙钟预算**：耗尽即不再重试，
            # 直接进入备源 / 降级（单次 HTTP 超时由客户端的 `fuyao_timeout_ms` 兜住）。
            if attempts and now_ms() - started >= spec.timeout_ms:
                last_error = SourceTimeout(
                    f"工具 {tool_name} 超出 timeout_ms={spec.timeout_ms}（已尝试 {attempts} 次）"
                )
                break
            attempts += 1
            try:
                produced = impl(dict(call.args))
                result = _coerce(produced, tool_name)
                result.attempts = attempts
                result.latency_ms = now_ms() - started
                result.tool_call_id = call_id
                self._inject_quality(result, call_id)
                self.breaker.record_success(tool_name)
                self.cache.set(cache_key, result)
                self._mark(
                    call_id, status="OK", attempts=attempts, latency_ms=result.latency_ms
                )
                return result
            except RETRYABLE_ERRORS as exc:
                last_error = exc
                self.breaker.record_failure(tool_name)
                if attempts < self.max_attempts:
                    time.sleep(self.base_backoff_s * (2 ** (attempts - 1)))  # 指数退避
            except FuyaoError as exc:
                last_error = exc
                break
            except Exception as exc:  # 未预期异常：不重试，直接进入降级
                last_error = FuyaoError(f"工具实现异常：{exc}")
                break

        # ⑥ 备源调用（主源熔断/失败后切 iFinD；熔断器只约束主源，不拦备源）
        primary_error = last_error
        fallback = self.registry.get_fallback(tool_name)
        if fallback is not None:
            attempts += 1
            try:
                produced = fallback(dict(call.args))
                result = _coerce(produced, tool_name)
                result.attempts = attempts
                result.latency_ms = now_ms() - started
                result.tool_call_id = call_id
                result.degrade_reason = result.degrade_reason or "主源不可用，已切换备源"
                self._inject_quality(result, call_id)
                self._mark(call_id, status="DEGRADED", attempts=attempts, latency_ms=result.latency_ms)
                return result
            except Exception as exc:
                # 保留主源错误：否则「单标的接口不接受逗号」这类**真正原因**会被备源异常覆盖，
                # 降级原因就说不清了（C4：不得静默，也要说得出「为什么」）。
                last_error = FuyaoError(f"备源亦失败：{exc}")

        # ⑦ 降级
        if primary_error is None:
            reason = "工具调用失败"
        elif primary_error is last_error:
            reason = f"主源与备源均失败：{primary_error}"
        else:
            reason = f"主源与备源均失败：{primary_error}；{last_error}"

        # 架构 §10.3：数据过期 → 用缓存 + 标注 staleness（比空手而归更有用，且**显式**标注）
        stale = self.cache.get_stale(cache_key) if hasattr(self.cache, "get_stale") else None
        if stale is not None:
            value, age_ms = stale
            base = value if isinstance(value, ToolResult) else _coerce(value, tool_name)
            # 用副本返回：缓存条目本身要保持「未降级」，否则后续正常命中会一直被标注为降级
            result = replace(
                base,
                attempts=attempts,
                latency_ms=now_ms() - started,
                reused=True,
                cached=True,
                degrade_reason=base.degrade_reason
                or f"{reason}；改用本地缓存（缓存年龄 {age_ms}ms）",
                tool_call_id=call_id,
            )
            self._mark(call_id, status="DEGRADED", attempts=attempts, latency_ms=result.latency_ms)
            return result

        return self._degrade(
            call_id,
            tool_name,
            reason,
            attempts=attempts,
            error_code=getattr(last_error, "code", None),
        )

    # --- 内部 ---
    def _reuse(self, tool_call_id: str, tool_name: str) -> ToolResult | DegradedResult:
        """幂等命中：优先用留档证据复原结果（M3 恢复的核心路径）。"""
        if self._evidences is None:
            return DegradedResult(
                tool_name=tool_name, reason="幂等命中但无证据留档", cached=True, tool_call_id=tool_call_id
            )
        rows = self._evidences.list(tool_call_id=tool_call_id)
        if not rows:
            return DegradedResult(
                tool_name=tool_name, reason="幂等命中但无证据留档", cached=True, tool_call_id=tool_call_id
            )
        row = rows[0]
        return ToolResult(
            tool_name=tool_name,
            data=row.value_json,
            source=row.source,
            interface=row.interface,
            as_of=row.as_of,
            unit=row.unit,
            caliber=row.caliber,
            raw_ref=row.raw_ref,
            confidence=row.confidence,
            staleness_ms=row.staleness_ms,
            degrade_reason=row.degrade_reason,
            attempts=0,
            reused=True,
            cached=True,
            tool_call_id=tool_call_id,
        )

    def _inject_quality(self, result: ToolResult, call_id: str | None) -> None:
        """第 8 步：质量元数据注入（`as_of` 缺失时回落到抓取时刻）。"""
        if result.as_of is None:
            result.as_of = now_ms()
        if result.staleness_ms is None:
            result.staleness_ms = max(0, now_ms() - int(result.as_of))
        if result.confidence is None:
            result.confidence = 0.9
        if not result.raw_ref:
            result.raw_ref = f"tool://{result.tool_name}#call={call_id or '-'}&at={result.as_of}"

    def _mark(
        self,
        call_id: str | None,
        *,
        status: str,
        attempts: int,
        latency_ms: int | None = None,
        error_code: int | None = None,
        error_message: str | None = None,
    ) -> None:
        if self._calls is not None and call_id:
            self._calls.mark(
                call_id,
                status=status,
                attempts=attempts,
                latency_ms=latency_ms,
                error_code=error_code,
                error_message=error_message,
            )

    def _degrade(
        self,
        call_id: str | None,
        tool_name: str,
        reason: str,
        *,
        attempts: int,
        error_code: int | None = None,
        cached: bool = False,
    ) -> DegradedResult:
        self._mark(
            call_id,
            status="FAILED",
            attempts=attempts,
            error_code=error_code,
            error_message=reason,
        )
        return DegradedResult(
            tool_name=tool_name,
            reason=reason,
            error_code=error_code,
            attempts=attempts,
            cached=cached,
            tool_call_id=call_id,
        )


# --------------------------------------------------------------------------- #
# 辅助
# --------------------------------------------------------------------------- #
def _validate_args(schema_json: dict[str, Any] | None, args: dict[str, Any]) -> str | None:
    """轻量 JSON Schema 校验：只检查 `required` 与顶层 `type/properties` 的明显冲突。"""
    if not schema_json:
        return None
    if not isinstance(args, dict):
        return "args 必须是对象"
    required = schema_json.get("required") or []
    missing = [key for key in required if key not in args]
    if missing:
        return f"缺少必填参数 {missing}"
    properties = schema_json.get("properties") or {}
    for key, value in args.items():
        expected = (properties.get(key) or {}).get("type")
        if expected == "array" and not isinstance(value, list):
            return f"参数 {key} 应为数组"
        if expected == "string" and not isinstance(value, str):
            return f"参数 {key} 应为字符串"
        if expected == "number" and not isinstance(value, (int, float)):
            return f"参数 {key} 应为数值"
    return None


def _coerce(produced: Any, tool_name: str) -> ToolResult:
    """工具实现的返回值归一：`ToolResult` 直接透传，`dict` 按字段填充。"""
    if isinstance(produced, ToolResult):
        produced.tool_name = produced.tool_name or tool_name
        return produced
    if isinstance(produced, dict):
        allowed = {
            "data",
            "source",
            "interface",
            "as_of",
            "unit",
            "caliber",
            "raw_ref",
            "confidence",
            "staleness_ms",
            "degrade_reason",
        }
        fields = {k: v for k, v in produced.items() if k in allowed}
        return ToolResult(tool_name=tool_name, **fields)
    return ToolResult(tool_name=tool_name, data=produced)


def _unused_import_guard() -> str:  # pragma: no cover - 保持 new_id 供后续扩展的显式引用
    return new_id()
