"""Harness 内核：模型路由（架构 C14、§10；03_BUILD_PLAN §3 M1）。

契约（03_BUILD_PLAN §3 M1）::

    ModelRouter.complete(role, messages, schema) -> LLMResult

职责：
1. **角色 → 模型档位**映射（§10.1）：轻任务（Clarifier / Compactor）走小模型，
   重任务（Planner / Reasoner / Synthesizer / Guard）走旗舰模型；并在最前面
   自动注入该角色的系统提示词（`harness/prompts/<role>.md`）。
2. **结构化输出保障**（§10.2）：`response_format=json_schema` + Pydantic 二次校验
   + 自动重试（最多 2 次，重试时把校验错误回灌给模型）。
3. **超时 / 熔断 / failover**（§10.1）：单次 wall-timeout（默认 45s）→ 切换 → 熔断 → 降级。
4. **降级不崩**：`LLM_API_KEY_PRIMARY` 缺失或全部失败时返回 `LLMResult(status="DEGRADED")`，
   由上层走「降级研判」路径（只列证据、不给结论），**不得抛异常导致启动即崩**。
5. **审计落库**：每次调用写一行 `llm_calls`——它是 `runs.cost_total` / `runs.tokens_total`
   的**唯一数据来源**（HANDOFF §6.2），不靠内存累计。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.domain import new_id, now_ms
from app.store.repo import LlmCallRepository

from .prompts import load_prompt

__all__ = [
    "LLMResult",
    "ModelRouter",
    "LLMError",
    "ROLE_MODEL_TIER",
    "AI_ROLES",
    "RESPONSE_FORMAT_MODES",
    "cost_from_usage",
    "reset_response_format_preference",
]

#: 契约：6 个 AI 角色（PRD §5 / 03_BUILD_PLAN §10「AI 角色 = 6 个」）。
AI_ROLES: tuple[str, ...] = (
    "Clarifier",
    "Planner",
    "Reasoner",
    "Compactor",
    "Synthesizer",
    "Guard",
)

#: 角色 → 模型档位（§10.1）。未登记的角色按「重任务」处理（保守取强模型）。
ROLE_MODEL_TIER: dict[str, str] = {
    "Clarifier": "small",
    "Compactor": "small",
    "Planner": "primary",
    "Reasoner": "primary",
    "Synthesizer": "primary",
    "Guard": "primary",
}

#: 结构化输出校验失败后的最大重试次数（§10.2：最多 2 次）。
MAX_SCHEMA_RETRIES = 2

#: 熔断阈值：连续失败 N 次 → 开路 cooldown 秒（§10.1）。
BREAKER_THRESHOLD = 3
BREAKER_COOLDOWN_S = 30.0

#: 结构化输出的 harness 侧「能力档位」，由强到弱。多数 OpenAI 兼容端点只支持
#: `json_object`（甚至完全不支持该字段），`json_schema` 会被直接 400 拒绝（§10.2）。
RESPONSE_FORMAT_MODES: tuple[str, ...] = ("json_schema", "json_object", "none")

#: `provider:model` → 当前可用档位。**进程内记忆**：一次降档后不再重复试错，
#: 因此「上游不支持 json_schema」只会付出一次额外往返的代价。
_RESPONSE_FORMAT_PREFERENCE: dict[str, str] = {}


def preferred_response_format(provider: str, model: str) -> str:
    """返回该 `provider:model` 当前可用的结构化输出档位（默认最强档）。"""
    return _RESPONSE_FORMAT_PREFERENCE.get(f"{provider}:{model}", RESPONSE_FORMAT_MODES[0])


def reset_response_format_preference() -> None:
    """清空档位记忆（测试用）。"""
    _RESPONSE_FORMAT_PREFERENCE.clear()


def _response_format_body(mode: str, schema_json: dict[str, Any] | None) -> dict[str, Any] | None:
    """按档位构造 `response_format`；`none` 或「无 schema」时不带该字段。"""
    if schema_json is None or mode == "none":
        return None
    if mode == "json_object":
        return {"type": "json_object"}
    return {
        "type": "json_schema",
        "json_schema": {"name": "output", "schema": schema_json, "strict": False},
    }


def _rejects_response_format(detail: str) -> bool:
    """4xx 详情是否明确指向「结构化输出不被支持」（据此判定可否降档重试）。"""
    lowered = detail.lower()
    return "response_format" in lowered or "json_schema" in lowered


def cost_from_usage(
    usage: dict[str, Any] | None,
    *,
    input_per_mtok: float,
    output_per_mtok: float,
) -> float:
    """把上游 `usage` 按单价折算成人民币成本（元）。

    - 单价**未配置（0）时恒返回 `0.0`**——不编造单价（口径唯一来源仍是 `llm_calls` 聚合）；
    - prompt tokens 一律按「输入单价」计（不单独区分缓存命中折扣），属**保守取值**（成本上界）。
    """
    if not usage:
        return 0.0
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    return round((input_per_mtok * prompt + output_per_mtok * completion) / 1_000_000, 8)


class LLMError(RuntimeError):
    """模型调用失败（网络 / 超时 / 上游 5xx）；`retryable` 决定是否计入熔断与 failover。"""

    def __init__(self, message: str, *, code: int | None = None, retryable: bool = True) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(slots=True)
class LLMResult:
    """一次 LLM 调用的结果（含用量、时延与结构化解析结果）。"""

    role: str
    provider: str
    model: str
    text: str = ""
    parsed: dict[str, Any] | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0
    latency_ms: int = 0
    status: str = "OK"  # OK | DEGRADED | ERROR
    attempts: int = 0
    failover: bool = False
    error: str | None = None
    call_id: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "OK"

    @property
    def degraded(self) -> bool:
        """True 表示本角色未拿到可用输出，上层应走「降级研判」。"""
        return self.status != "OK"


#: 可注入的底层补全函数：`(provider, model, messages, schema_json, timeout_ms) -> dict`。
#: 返回 `{"text": str, "usage": {...}, "cost": float}`；测试注入确定性假实现即可离线跑通。
Completer = Callable[[str, str, list[dict[str, Any]], dict[str, Any] | None, int], dict[str, Any]]


class _CircuitBreaker:
    """最小熔断器（连续失败 `threshold` 次 → 开路 `cooldown_s` 秒）。

    注：M2 的 `data/breaker.py` 会提供共享实现，本类只作为 M1 的内置默认。
    """

    def __init__(self, *, threshold: int = BREAKER_THRESHOLD, cooldown_s: float = BREAKER_COOLDOWN_S):
        self.threshold = threshold
        self.cooldown_s = cooldown_s
        self._failures: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}

    def is_open(self, key: str) -> bool:
        opened = self._opened_at.get(key)
        if opened is None:
            return False
        if time.monotonic() - opened >= self.cooldown_s:
            self._opened_at.pop(key, None)
            self._failures[key] = 0
            return False
        return True

    def record_success(self, key: str) -> None:
        self._failures[key] = 0
        self._opened_at.pop(key, None)

    def record_failure(self, key: str) -> None:
        count = self._failures.get(key, 0) + 1
        self._failures[key] = count
        if count >= self.threshold:
            self._opened_at[key] = time.monotonic()


class ModelRouter:
    """角色 → provider / 模型档位的路由器。

    `completer` 可注入（测试用），默认走 OpenAI 兼容协议的 HTTP 调用。
    """

    def __init__(
        self,
        session: Session | None = None,
        *,
        settings: Settings | None = None,
        completer: Completer | None = None,
        breaker: _CircuitBreaker | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._repo = LlmCallRepository(session) if session is not None else None
        self._completer = completer or self._http_complete
        self._breaker = breaker or _CircuitBreaker()

    # --- 契约入口 ---
    def complete(
        self,
        role: str,
        messages: Sequence[dict[str, Any]],
        schema: Any = None,
        *,
        run_id: str | None = None,
        task_id: str | None = None,
        step_id: str | None = None,
    ) -> LLMResult:
        """按角色路由一次补全；任何失败都以 `LLMResult(status="DEGRADED")` 返回，不抛异常。"""
        provider = self.settings.llm_provider_primary or "openai-compatible"
        model = self._model_for(role)
        schema_json = _as_json_schema(schema)
        started = now_ms()
        base_messages = [{"role": "system", "content": load_prompt(role)}, *messages]

        # ① 未配置 → 直接降级（不得崩；HANDOFF §4.3 派生条款）
        if not self.settings.is_llm_configured:
            return self._record(
                LLMResult(
                    role=role,
                    provider=provider,
                    model=model,
                    status="DEGRADED",
                    error="LLM 未配置（LLM_API_KEY_PRIMARY / LLM_BASE_URL_PRIMARY / LLM_MODEL_PRIMARY 缺失）",
                    latency_ms=now_ms() - started,
                ),
                run_id=run_id,
                task_id=task_id,
                step_id=step_id,
            )

        breaker_key = f"{provider}:{model}"
        if self._breaker.is_open(breaker_key):
            return self._record(
                LLMResult(
                    role=role,
                    provider=provider,
                    model=model,
                    status="DEGRADED",
                    error=f"熔断器开路（{breaker_key}）",
                    latency_ms=now_ms() - started,
                ),
                run_id=run_id,
                task_id=task_id,
                step_id=step_id,
            )

        max_calls = 1 + max(0, self.settings.llm_max_failover)
        last_error: str | None = None
        #: 真实发起的外层调用次数（不可重试的错误会在首轮 break）。
        #: 收口时用**实际次数**而非 `max_calls`，避免 `attempts` 成为误导值。
        attempts_made = 0

        for call_index in range(max_calls):
            attempts_made = call_index + 1
            try:
                result = self._attempt(
                    role=role,
                    provider=provider,
                    model=model,
                    messages=base_messages,
                    schema=schema,
                    schema_json=schema_json,
                    started=started,
                )
                result.attempts = attempts_made
                result.failover = attempts_made > 1
                self._breaker.record_success(breaker_key)
                return self._record(result, run_id=run_id, task_id=task_id, step_id=step_id)
            except LLMError as exc:
                last_error = str(exc)
                if exc.retryable:
                    self._breaker.record_failure(breaker_key)
                if not exc.retryable or self._breaker.is_open(breaker_key):
                    break

        return self._record(
            LLMResult(
                role=role,
                provider=provider,
                model=model,
                status="DEGRADED",
                attempts=attempts_made,
                failover=attempts_made > 1,
                error=last_error or "LLM 调用失败",
                latency_ms=now_ms() - started,
            ),
            run_id=run_id,
            task_id=task_id,
            step_id=step_id,
        )

    # --- 内部 ---
    def _model_for(self, role: str) -> str:
        tier = ROLE_MODEL_TIER.get(role, "primary")
        if tier == "small":
            return self.settings.llm_model_small or self.settings.llm_model_primary
        return self.settings.llm_model_primary

    def _attempt(
        self,
        *,
        role: str,
        provider: str,
        model: str,
        messages: list[dict[str, Any]],
        schema: Any,
        schema_json: dict[str, Any] | None,
        started: int,
    ) -> LLMResult:
        """一轮调用 + 结构化输出校验（校验失败最多再试 `MAX_SCHEMA_RETRIES` 次）。"""
        attempts = 0
        working = list(messages)
        text = ""
        usage: dict[str, Any] = {}
        cost = 0.0

        while True:
            attempts += 1
            payload = self._completer(
                provider, model, working, schema_json, self.settings.llm_timeout_ms
            )
            text = str(payload.get("text") or "")
            usage = payload.get("usage") or {}
            cost = float(payload.get("cost") or 0.0)

            if schema is None:
                break
            parsed: dict[str, Any] | None = _parse_json_object(text)
            error = _validate(schema, parsed) if parsed is not None else "响应不是合法 JSON 对象"
            if error is None:
                return LLMResult(
                    role=role,
                    provider=provider,
                    model=model,
                    text=text,
                    parsed=parsed,
                    prompt_tokens=int(usage.get("prompt_tokens") or 0),
                    completion_tokens=int(usage.get("completion_tokens") or 0),
                    total_tokens=int(usage.get("total_tokens") or 0),
                    cost=cost,
                    latency_ms=now_ms() - started,
                    status="OK",
                    attempts=attempts,
                )
            if attempts > MAX_SCHEMA_RETRIES:
                raise LLMError(f"结构化输出校验失败（已重试 {MAX_SCHEMA_RETRIES} 次）：{error}")
            # 把校验错误回灌，让模型自我修正（§10.2）
            working = [
                *messages,
                {"role": "assistant", "content": text},
                {"role": "user", "content": f"上次输出不合法：{error}。请只输出符合 schema 的 JSON。"},
            ]

        return LLMResult(
            role=role,
            provider=provider,
            model=model,
            text=text,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            total_tokens=int(usage.get("total_tokens") or 0),
            cost=cost,
            latency_ms=now_ms() - started,
            status="OK",
            attempts=attempts,
        )

    def _http_complete(
        self,
        provider: str,
        model: str,
        messages: list[dict[str, Any]],
        schema_json: dict[str, Any] | None,
        timeout_ms: int,
    ) -> dict[str, Any]:
        """OpenAI 兼容协议的默认实现（`POST {base_url}/chat/completions`）。

        **结构化输出能力降档**（§10.2）：首轮按 `preferred_response_format()` 发
        `json_schema`；若上游以 4xx 明确拒绝 `response_format`（如 DeepSeek 返回
        `This response_format type is unavailable now`），则自动降为 `json_object`、
        必要时再降为「不带该字段」，并把可用档位记进进程内偏好表——后续调用直接用
        可用档位，不再重复试错。校验仍由 `_attempt()` 的 Pydantic 二次校验兜底，
        因此降档只影响「约束强度」，不影响功能正确性。
        """
        base = (self.settings.llm_base_url_primary or "").rstrip("/")
        preference = preferred_response_format(provider, model)
        modes = RESPONSE_FORMAT_MODES[RESPONSE_FORMAT_MODES.index(preference) :]
        key = f"{provider}:{model}"
        last_detail = ""

        for mode in modes:
            body: dict[str, Any] = {"model": model, "messages": messages, "temperature": 0.0}
            response_format = _response_format_body(mode, schema_json)
            if response_format is not None:
                body["response_format"] = response_format
            try:
                response = httpx.post(
                    f"{base}/chat/completions",
                    json=body,
                    headers={
                        "Authorization": f"Bearer {self.settings.llm_api_key_primary}",
                        "Content-Type": "application/json",
                    },
                    timeout=httpx.Timeout(timeout_ms / 1000),
                )
            except httpx.HTTPError as exc:  # 超时 / 连接失败
                raise LLMError(f"{provider} 调用失败：{exc}", retryable=True) from exc

            if response.status_code < 400:
                _RESPONSE_FORMAT_PREFERENCE[key] = mode
                data = response.json()
                choices = data.get("choices") or []
                content = (
                    str((choices[0].get("message") or {}).get("content") or "") if choices else ""
                )
                usage = data.get("usage") or {}
                return {
                    "text": content,
                    "usage": usage,
                    "cost": cost_from_usage(
                        usage,
                        input_per_mtok=self.settings.llm_price_input_per_mtok_cny,
                        output_per_mtok=self.settings.llm_price_output_per_mtok_cny,
                    ),
                }

            if response.status_code >= 500:
                raise LLMError(f"{provider} 上游错误 HTTP {response.status_code}", retryable=True)

            last_detail = response.text.replace("\n", " ")[:200]
            if mode != "none" and schema_json is not None and _rejects_response_format(last_detail):
                # 上游不支持该档结构化输出 → 降档重试，并记住本次可用档位
                _RESPONSE_FORMAT_PREFERENCE[key] = RESPONSE_FORMAT_MODES[
                    RESPONSE_FORMAT_MODES.index(mode) + 1
                ]
                continue
            raise LLMError(
                f"{provider} 请求被拒 HTTP {response.status_code}",
                code=response.status_code,
                retryable=False,
            )

        raise LLMError(f"{provider} 请求被拒（结构化输出档位不兼容）：{last_detail}", retryable=False)

    def _record(
        self,
        result: LLMResult,
        *,
        run_id: str | None,
        task_id: str | None,
        step_id: str | None,
    ) -> LLMResult:
        """落 `llm_calls`（成本结算的唯一数据来源）。"""
        if self._repo is not None and run_id:
            row = self._repo.create(
                run_id=run_id,
                provider=result.provider,
                model=result.model,
                status=result.status,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                total_tokens=result.total_tokens,
                cost=result.cost,
                latency_ms=result.latency_ms,
                task_id=task_id,
                step_id=step_id,
                error_code=None,
                request_ref=new_id(),
            )
            result.call_id = row.id
        return result


# --------------------------------------------------------------------------- #
# 结构化输出辅助（§10.2）
# --------------------------------------------------------------------------- #
def _as_json_schema(schema: Any) -> dict[str, Any] | None:
    """`schema` 允许是 Pydantic 模型类或 JSON Schema dict；其它类型视为「不约束」。"""
    if schema is None:
        return None
    if isinstance(schema, dict):
        return schema
    model_json_schema = getattr(schema, "model_json_schema", None)
    if callable(model_json_schema):
        return dict(model_json_schema())
    return None


def _parse_json_object(text: str) -> dict[str, Any] | None:
    """从模型输出中解析 JSON 对象（容忍 ```json 围栏与前后噪声）。"""
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        if candidate.lower().startswith("json"):
            candidate = candidate[4:]
        candidate = candidate.strip()
    try:
        value = json.loads(candidate)
    except ValueError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(candidate[start : end + 1])
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


def _validate(schema: Any, parsed: dict[str, Any] | None) -> str | None:
    """Pydantic 二次校验；通过返回 `None`，失败返回错误摘要。"""
    if parsed is None:
        return "输出为空"
    model_validate = getattr(schema, "model_validate", None)
    if callable(model_validate):
        try:
            model_validate(parsed)
        except Exception as exc:  # pydantic.ValidationError
            return str(exc).splitlines()[0] if str(exc) else "校验失败"
        return None
    # 纯 JSON Schema：只做「必填字段存在」的轻校验
    required = schema.get("required") or []
    missing = [key for key in required if key not in parsed]
    if missing:
        return f"缺少必填字段：{missing}"
    return None
