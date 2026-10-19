"""数据层：iFinD MCP 客户端骨架（**降级备源**；03_BUILD_PLAN §3 M2 / §0 表 L28）。

定位（架构 §9.3 第 ⑥ 步）：扶摇是主源，iFinD MCP 只在主源不可用时作为**备源**被调用。
它不是主链路的依赖，因此：

- `IFIND_MCP_ENABLED` 默认 **true**，但缺 `ENDPOINT` / `KEY` 时 `enabled` 为假 →
  调用 `call_tool()` 抛 `NotEnabled`，由执行器第 ⑥→⑦ 步转成降级（**不崩、不静默**）；
- iFinD 返回**自然语言 / 表格文本、无字段级 schema**（DATASOURCES §3），故 M2 只落「文本 +
  低置信度」的证据骨架；「文本 → 字段」抽取层留待 M3+（ADR 未禁止，但 24h 内不做）。

协议事实（实测，DATASOURCES §3）：MCP over HTTP（JSON-RPC 2.0，`protocolVersion=2025-03-26`），
鉴权头 `Authorization: <auth_token>`（**裸 token，无 `Bearer` 前缀**），会话头 `Mcp-Session-Id`，
握手顺序 `initialize` → `notifications/initialized`，方法 `tools/list` / `tools/call`。
"""

from __future__ import annotations

import json
import re
import threading
from datetime import date, timedelta
from typing import Any

import httpx

from app.config import Settings, get_settings

from .errors import FuyaoError, NoPermission, NotEnabled, SourceDown, SourceTimeout

__all__ = ["IfindMCPClient", "build_ifind_client", "MCP_PROTOCOL_VERSION"]

#: MCP 协议版本（DATASOURCES §3 实测值）。
MCP_PROTOCOL_VERSION = "2025-03-26"

#: 客户端自述（MCP `initialize` 必填项）。
_CLIENT_INFO: dict[str, Any] = {"name": "investment-x-buddy", "version": "1.0"}

#: 会话头（大小写两种写法兼容）。
_SESSION_HEADERS = ("Mcp-Session-Id", "mcp-session-id")


def _parse_body(response: httpx.Response) -> dict[str, Any] | None:
    """解析 MCP over HTTP 响应：兼容 `application/json` 与 `text/event-stream`（SSE）。"""
    text = response.text or ""
    content_type = (response.headers.get("content-type") or "").lower()
    head = text.lstrip()
    if "text/event-stream" in content_type or head.startswith("event:") or head.startswith("data:"):
        payload: dict[str, Any] | None = None
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if not chunk or chunk == "[DONE]":
                continue
            try:
                candidate = json.loads(chunk)
            except ValueError:
                continue
            if isinstance(candidate, dict):
                payload = candidate
        return payload
    if not text.strip():
        return None
    try:
        value = response.json()
    except ValueError as exc:
        raise SourceDown(f"iFinD MCP 返回非 JSON 响应：{text[:200]}") from exc
    return value if isinstance(value, dict) else {"result": value}


def _extract_text(result: Any) -> str:
    """从 `tools/call` 结果里抽取文本（MCP 标准结构 `result.content[].text`）。"""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        blocks = result.get("content")
        if isinstance(blocks, list):
            texts = [
                str(block.get("text"))
                for block in blocks
                if isinstance(block, dict) and block.get("text") is not None
            ]
            if texts:
                return "\n".join(texts)
        structured = result.get("structuredContent")
        if structured is not None:
            return json.dumps(structured, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False, default=str)


class IfindMCPClient:
    """iFinD MCP 客户端（JSON-RPC 2.0 over HTTP，延迟初始化 + 会话保持）。"""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        # 与扶摇共用一个外呼超时（架构 §14.2「所有外部调用设超时与上限」；不新增配置项）。
        timeout_s = max(1.0, float(self.settings.fuyao_timeout_ms) / 1000)
        self._client = http_client or httpx.Client(
            timeout=httpx.Timeout(timeout_s), follow_redirects=True
        )
        self._owns_client = http_client is None
        self._lock = threading.Lock()
        self._session_id: str | None = None
        self._initialized = False
        self._tool_names: list[str] = []

    # --- 生命周期 ---
    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> IfindMCPClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- 状态 ---
    @property
    def enabled(self) -> bool:
        """三项配置齐全才认为「已启用」（§0 表 L28：默认启用，缺配置即不可用）。"""
        return bool(
            self.settings.ifind_mcp_enabled
            and self.settings.ifind_mcp_endpoint
            and self.settings.ifind_mcp_key
        )

    @property
    def endpoint(self) -> str:
        return (self.settings.ifind_mcp_endpoint or "").rstrip("/")

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def tool_names(self) -> list[str]:
        """最近一次 `list_tools()` 的结果（未调用过则为空）。"""
        return list(self._tool_names)

    # --- 请求 ---
    def _headers(self) -> dict[str, str]:
        key = self.settings.ifind_mcp_key
        if not key:
            raise NotEnabled("缺少 IFIND_MCP_KEY：iFinD 备源不可用")
        headers = {
            # 裸 token，无 Bearer（DATASOURCES §3 实测）
            "Authorization": key,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _post(self, payload: dict[str, Any], *, expect_result: bool = True) -> Any:
        """发一次 JSON-RPC 请求；网络/协议错误统一转领域异常（供执行器降级）。"""
        if not self.enabled:
            raise NotEnabled(
                "iFinD MCP 未启用（需同时配置 IFIND_MCP_ENABLED / IFIND_MCP_ENDPOINT / IFIND_MCP_KEY）"
            )
        try:
            response = self._client.post(self.endpoint, json=payload, headers=self._headers())
        except httpx.TimeoutException as exc:
            raise SourceTimeout(f"iFinD MCP 超时：{exc}") from exc
        except httpx.HTTPError as exc:
            raise SourceDown(f"iFinD MCP 不可达：{exc}") from exc

        for header in _SESSION_HEADERS:
            sid = response.headers.get(header)
            if sid:
                self._session_id = sid
                break

        if response.status_code in (401, 403):
            raise NoPermission(f"iFinD MCP 拒绝访问（HTTP {response.status_code}）")
        if response.status_code >= 500:
            raise SourceDown(f"iFinD MCP 服务错误（HTTP {response.status_code}）")
        if not expect_result:
            return None
        body = _parse_body(response)
        if body is None:
            return None
        if "error" in body:
            raise SourceDown(f"iFinD MCP 协议错误：{body['error']}")
        return body.get("result")

    def _rpc(self, method: str, params: dict[str, Any] | None, rpc_id: int | None = 1) -> Any:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        if rpc_id is not None:
            payload["id"] = rpc_id
        return self._post(payload, expect_result=rpc_id is not None)

    # --- 契约方法 ---
    def initialize(self, *, force: bool = False) -> dict[str, Any]:
        """握手：`initialize` → `notifications/initialized`（幂等，已握手则直接返回）。"""
        with self._lock:
            if self._initialized and not force:
                return {"session_id": self._session_id, "reused": True}
            result = self._rpc(
                "initialize",
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": _CLIENT_INFO,
                },
            )
            self._rpc("notifications/initialized", None, rpc_id=None)
            self._initialized = True
            return {"session_id": self._session_id, "server": result}

    def list_tools(self) -> list[dict[str, Any]]:
        """`tools/list`：iFinD 侧可用工具清单（36 个，DATASOURCES §3）。"""
        self.initialize()
        result = self._rpc("tools/list", {})
        tools = (result or {}).get("tools") if isinstance(result, dict) else None
        self._tool_names = [
            str(item.get("name")) for item in (tools or []) if isinstance(item, dict)
        ]
        return list(tools or [])

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        """`tools/call`：返回**自然语言/表格文本**（无字段级 schema，DATASOURCES §3）。"""
        tool = (name or "").strip()
        if not tool:
            raise NotEnabled("iFinD 工具名为空：未配置备源工具映射（§3.1「无 schema 时直接降级」）")
        self.initialize()
        result = self._rpc("tools/call", {"name": tool, "arguments": arguments or {}})
        if isinstance(result, dict) and result.get("isError"):
            raise FuyaoError(f"iFinD 工具 {tool} 返回业务错误：{_extract_text(result)}")
        return _extract_text(result)

    def query(self, text: str, *, tool: str) -> str:
        """按自然语言提问调用指定 iFinD 工具（供工具备源实现使用）。"""
        question = (text or "").strip()
        if not question:
            raise FuyaoError("iFinD 查询语句为空")
        if tool in {"search_news", "search_notice"}:
            today = date.today()
            years = [int(y) for y in re.findall(r"20\d{2}", question)]
            start = today - timedelta(days=3 * 366)
            if years:
                start = min(start, date(min(years), 1, 1))
            return self.call_tool(tool, {
                "query": question,
                "size": 5,
                "time_start": start.isoformat(),
                "time_end": today.isoformat(),
            })
        return self.call_tool(tool, {"query": question})


def build_ifind_client(settings: Settings | None = None) -> IfindMCPClient:
    """构造 iFinD MCP 客户端（**永远返回对象**：未启用时其调用会抛 `NotEnabled`）。"""
    return IfindMCPClient(settings)
