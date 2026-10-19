"""M0 取数断言：Mock 与真实客户端同签名、信封同构、错误码映射、thscode 校验。

真实通道的用例在**无密钥时 skip（不 fail）**——仓库不得对 `api-key.txt` 产生硬依赖（HANDOFF §4.3）。
"""

from __future__ import annotations

import inspect
import json

import httpx
import pytest

from app.config.settings import Settings
from app.data import (
    FuyaoClient,
    InvalidThscode,
    MockClient,
    MockDataMissing,
    NoPermission,
    NotReady,
    RateLimited,
    SourceDown,
    SourceTimeout,
    build_client,
    ensure_single_thscode,
    error_from_code,
    validate_thscode,
)
from app.data.errors import FuyaoError

SNAPSHOT = "/api/a-share/prices/snapshot"
META_SEARCH = "/api/meta/tickers/search"


# --------------------------------------------------------------------------- #
# 同签名 / 同构
# --------------------------------------------------------------------------- #
def test_m0_mock_client_has_same_signatures_as_real_client() -> None:
    for method in ("get", "raw"):
        assert inspect.signature(getattr(MockClient, method)) == inspect.signature(
            getattr(FuyaoClient, method)
        ), f"{method} 签名与真实客户端不一致"


def test_m0_mock_envelope_is_isomorphic_to_real_shape() -> None:
    client = MockClient(Settings(mock_data_mode=True))
    raw = client.raw(SNAPSHOT, {"thscodes": "600519.SH"})
    payload = raw.payload

    # 真实信封四件套
    assert set(payload) >= {"code", "message", "request_id", "data"}
    assert payload["code"] == 0
    assert raw.status_code == 200  # 业务成功也是 200

    api = client.get(SNAPSHOT, {"thscodes": "600519.SH"})
    assert api.code == 0
    assert isinstance(api.timestamp, int) and api.timestamp > 0  # D2：毫秒级
    assert api.total == 1
    item = api.item[0]
    assert item["thscode"] == "600519.SH"
    assert isinstance(item["last_price"], float)
    # D3：涨跌幅是百分数原值，不得二次乘 100
    assert abs(item["price_change_ratio_pct"]) < 100
    assert api.raw_ref.startswith("mock://" + SNAPSHOT)


def test_m0_mock_currency_and_null_passthrough() -> None:
    client = MockClient(Settings(mock_data_mode=True))
    meta = client.get(META_SEARCH, {"q": "600519.SH"}).item
    target = next(it for it in meta if it["thscode"] == "600519.SH")
    assert target["currency"] == "CNY"  # 显式 currency（A 股恒 CNY）
    # D4：上游缺失字段透传 null，不补零 / 不补空串
    assert target["end_date"] is None


def test_m0_mock_filters_by_thscode_param() -> None:
    client = MockClient(Settings(mock_data_mode=True))
    api = client.get(SNAPSHOT, {"thscodes": "600519.SH"})
    assert [it["thscode"] for it in api.item] == ["600519.SH"]


def test_m0_mock_unknown_path_raises_domain_error() -> None:
    client = MockClient(Settings(mock_data_mode=True))
    with pytest.raises(MockDataMissing):
        client.get("/api/not-covered/whatever")


def test_m0_build_client_switches_on_mock_mode() -> None:
    assert isinstance(build_client(Settings(mock_data_mode=True)), MockClient)
    assert isinstance(
        build_client(Settings(mock_data_mode=False, fuyao_api_key="fake-key-1234567890")),
        FuyaoClient,
    )


# --------------------------------------------------------------------------- #
# thscode 校验（D1）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("good", ["600519.SH", "002371.SZ", "000001.SH", "881121.TI"])
def test_m0_validate_thscode_accepts_complete_codes(good: str) -> None:
    assert validate_thscode(good) == good


@pytest.mark.parametrize("bad", ["600519", "", "600519.", ".SH", "6005 19.SH"])
def test_m0_validate_thscode_rejects_bare_ticker(bad: str) -> None:
    with pytest.raises(InvalidThscode):
        validate_thscode(bad)


def test_m0_ensure_single_thscode_rejects_comma() -> None:
    with pytest.raises(InvalidThscode):
        ensure_single_thscode("600519.SH,000001.SH")


# --------------------------------------------------------------------------- #
# 错误码映射（D5）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (4001, RateLimited),
        (3002, NotReady),
        (5002, SourceTimeout),
        (5003, SourceDown),
        (2003, NoPermission),
    ],
)
def test_m0_error_from_code_maps_contract_codes(code: int, expected: type[FuyaoError]) -> None:
    error = error_from_code(code, "boom", request_id="rid-1")
    assert isinstance(error, expected)
    assert error.code == code and error.request_id == "rid-1"


def test_m0_business_error_over_http_200_is_raised_by_code() -> None:
    """D5：扶摇业务错误 HTTP 恒为 200，必须靠 code 判错。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"code": 2003, "message": "Invalid or revoked API key", "data": None,
                       "request_id": "rid-2003"}
        )

    http = httpx.Client(
        base_url="https://fuyao.aicubes.cn", transport=httpx.MockTransport(handler)
    )
    client = FuyaoClient(Settings(fuyao_api_key="fake-key-1234567890"), http_client=http)
    with pytest.raises(NoPermission) as excinfo:
        client.get(SNAPSHOT, {"thscodes": "600519.SH"})
    assert excinfo.value.code == 2003
    assert excinfo.value.request_id == "rid-2003"


def test_m0_success_envelope_is_peeled_with_timestamp() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": 0,
                "message": "success",
                "request_id": "rid-0",
                "data": {"timestamp": 1792393286000, "total": 1, "item": [{"thscode": "600519.SH"}]},
            },
        )

    http = httpx.Client(
        base_url="https://fuyao.aicubes.cn", transport=httpx.MockTransport(handler)
    )
    client = FuyaoClient(Settings(fuyao_api_key="fake-key-1234567890"), http_client=http)
    api = client.get(SNAPSHOT, {"thscodes": "600519.SH"})
    assert api.timestamp == 1792393286000
    assert api.request_id == "rid-0"
    assert api.raw_ref.startswith("https://fuyao.aicubes.cn/api/a-share/prices/snapshot")


def test_m0_real_client_without_key_raises_domain_error_not_crash() -> None:
    client = FuyaoClient(Settings(fuyao_api_key=None))
    with pytest.raises(FuyaoError):
        client.raw(SNAPSHOT, {"thscodes": "600519.SH"})


# --------------------------------------------------------------------------- #
# 真实通道（有密钥才跑；无密钥 / 网络不可达一律 skip）
# --------------------------------------------------------------------------- #
def _real_settings() -> Settings:
    from app.config.settings import get_settings, load_settings, reset_settings_cache

    reset_settings_cache()
    settings = load_settings()
    if not settings.fuyao_api_key:
        pytest.skip("无 FUYAO_API_KEY（api-key.txt 缺失）→ 跳过真实通道断言（HANDOFF §4.3）")
    return settings


def test_m0_real_snapshot_600519_has_timestamp_and_thscode() -> None:
    settings = _real_settings()
    try:
        with FuyaoClient(settings) as client:
            api = client.get(SNAPSHOT, {"thscodes": "600519.SH"})
            meta = client.get(META_SEARCH, {"q": "600519.SH", "limit": 3})
    except (FuyaoError, httpx.HTTPError) as exc:  # 网络/限流不可控 → 跳过而非失败
        pytest.skip(f"真实通道当前不可用：{exc}")

    assert api.code == 0
    assert isinstance(api.timestamp, int) and api.timestamp > 0
    item = api.item[0]
    assert item["thscode"] == "600519.SH"
    assert abs(item["price_change_ratio_pct"]) < 100  # D3
    # 快照本身不返回 currency（DATASOURCES §2.2）；currency 来自 meta 检索
    assert any(it.get("currency") == "CNY" for it in meta.item)


def test_m0_real_artifact_ref_keeps_raw_evidence_pointer() -> None:
    settings = _real_settings()
    try:
        with FuyaoClient(settings) as client:
            api = client.get(SNAPSHOT, {"thscodes": "600519.SH"})
    except (FuyaoError, httpx.HTTPError) as exc:
        pytest.skip(f"真实通道当前不可用：{exc}")

    # raw_ref 必须能回到原始响应（C3 证据可回溯），且**不含任何密钥**
    assert api.raw_ref.startswith("https://fuyao.aicubes.cn/")
    assert settings.fuyao_api_key not in api.raw_ref
    assert json.dumps(api.raw.payload, ensure_ascii=False)  # 原始 payload 可序列化留档
