"""M0 配置断言：Key 解析 5 条规则 / 无密钥自动降级 Mock / 脱敏钩子。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import mask_secret, parse_fuyao_key, sanitize
from app.config.settings import Settings

#: 伪密钥（非真实，仅用于解析规则验证）
FAKE_KEY = "abcdefghij0123456789ABCDEFGHIJ0123456789"
FAKE_JWE = (
    "eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2R0NNIn0."
    + ".".join(["A" * 210, "B" * 210, "C" * 210, "D" * 210, "E" * 210])
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("FUYAO_API_KEY", "FUYAO_API_KEY_FILE", "MOCK_DATA_MODE", "FUYAO_BASE_URL"):
        monkeypatch.delenv(var, raising=False)


def test_m0_parse_key_skips_blank_and_comment_lines() -> None:
    text = f"\n# 注释行\n\n{FAKE_KEY}\n"
    assert parse_fuyao_key(text) == FAKE_KEY


def test_m0_parse_key_excludes_ifind_jwe_line() -> None:
    # 真实 api-key.txt 的形态：第 1 行纯密钥、第 2 行 iFinD JWE
    text = f"{FAKE_KEY}\n{FAKE_JWE}\n"
    assert parse_fuyao_key(text) == FAKE_KEY


def test_m0_parse_key_falls_back_to_part_after_colon() -> None:
    # 兼容旧「标签:密钥」格式（HANDOFF §4.2 规则⑤）
    assert parse_fuyao_key(f"my-label:{FAKE_KEY}\n") == FAKE_KEY


def test_m0_parse_key_picks_longest_candidate() -> None:
    shorter = FAKE_KEY[:20]
    assert parse_fuyao_key(f"{shorter}\n{FAKE_KEY}\n") == FAKE_KEY


def test_m0_parse_key_returns_none_for_empty_or_jwe_only() -> None:
    assert parse_fuyao_key("") is None
    assert parse_fuyao_key("\n# 只有注释\n") is None
    assert parse_fuyao_key(FAKE_JWE) is None


def test_m0_settings_without_key_degrades_to_mock_not_crash() -> None:
    settings = Settings(mock_data_mode=False, fuyao_api_key=None)
    assert settings.is_mock is True  # 无密钥 → 强制 Mock，不得崩（HANDOFF §4.3）


def test_m0_settings_with_key_is_not_mock() -> None:
    settings = Settings(mock_data_mode=False, fuyao_api_key=FAKE_KEY)
    assert settings.is_mock is False


def test_m0_settings_reads_key_file_candidates(tmp_path: Path) -> None:
    key_file = tmp_path / "api-key.txt"
    key_file.write_text(f"{FAKE_KEY}\n{FAKE_JWE}\n", encoding="utf-8")
    settings = Settings(mock_data_mode=False, fuyao_api_key_file=str(key_file))
    from app.config.settings import _read_key_from_files

    resolved = _read_key_from_files(settings.key_file_candidates)
    assert resolved == FAKE_KEY


def test_m0_settings_missing_key_file_is_silent(tmp_path: Path) -> None:
    from app.config.settings import _read_key_from_files

    assert _read_key_from_files((tmp_path / "not-exist.txt",)) is None


def test_m0_mask_secret_does_not_leak_full_key() -> None:
    masked = mask_secret(FAKE_KEY)
    assert FAKE_KEY not in masked
    assert masked.startswith(FAKE_KEY[:4]) and "len=" in masked
    assert mask_secret(None) == "<empty>"


def test_m0_sanitize_redacts_secret() -> None:
    text = f"X-api-key: {FAKE_KEY} 与 request_id=abc"
    cleaned = sanitize(text, secrets=[FAKE_KEY])
    assert FAKE_KEY not in cleaned
    assert "***REDACTED***" in cleaned
    assert "request_id=abc" in cleaned
