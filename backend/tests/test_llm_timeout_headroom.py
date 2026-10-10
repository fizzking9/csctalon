"""超时自洽性保障测试（2026-09-20 事故修复）

事故: qwen3.8:27b 的 352 次调用中 104 次 ReadTimeout（29.5%）。
根因: max_tokens=1500/3000 配置 + timeout_s=60/120 —— 60s 内产不出那么多 token。

本测试锁定: `_ensure_timeout_fits_tokens` 的按需放宽行为 + 云端不被误放宽。
"""
import pytest

from app.services.llm_client import (
    _SLOWEST_DECODE_TPS,
    _TIMEOUT_HEADROOM_S,
    _ensure_timeout_fits_tokens,
    _is_local_endpoint,
)


class TestIsLocalEndpoint:
    """本地/云端端点判定。"""

    @pytest.mark.parametrize("url", [
        "http://127.0.0.1:11434/v1",
        "http://localhost:4080/v1",
        "http://host.docker.internal:11434/v1",
        "http://192.168.10.21:4080/v1",      # talon 集群（生产实际值）
        "http://10.147.17.21:11434/v1",      # ZeroTier 网段
        "http://172.17.0.5:11434/v1",        # Docker 网桥
    ])
    def test_local_endpoints(self, url):
        assert _is_local_endpoint(url) is True

    @pytest.mark.parametrize("url", [
        "https://api.deepseek.com",
        "https://api.openai.com/v1",
        "",                       # 空值 → 不判定为本地
        None,
    ])
    def test_cloud_or_empty(self, url):
        assert _is_local_endpoint(url) is False


class TestEnsureTimeoutFitsTokens:
    """超时按 max_tokens 自动放宽。"""

    def test_local_relaxes_when_too_short(self):
        """本地端点 + max_tokens 过大 → 放宽（本次事故的核心场景）。

        1500 tok / 15 tps + 30s = 130s > 60s → 应返回 130s
        """
        got = _ensure_timeout_fits_tokens(60.0, 1500, base_url="http://192.168.10.21:4080/v1")
        assert got == pytest.approx(1500 / _SLOWEST_DECODE_TPS + _TIMEOUT_HEADROOM_S)
        assert got > 60.0

    def test_local_keeps_larger_caller_timeout(self):
        """调用方给的超时已足够大 → 不改小（只放大, 不缩小）。"""
        got = _ensure_timeout_fits_tokens(600.0, 2000, base_url="http://192.168.10.21:4080/v1")
        assert got == 600.0

    def test_cloud_not_relaxed(self):
        """云端端点不适用此保护（延迟低且有 prefix cache）。"""
        got = _ensure_timeout_fits_tokens(60.0, 5000, base_url="https://api.deepseek.com")
        assert got == 60.0

    def test_none_max_tokens_passthrough(self):
        """无 max_tokens（不限长）→ 保持调用方原值, 不做推算。"""
        assert _ensure_timeout_fits_tokens(60.0, None, base_url="http://127.0.0.1:11434/v1") == 60.0
        assert _ensure_timeout_fits_tokens(None, None, base_url="http://127.0.0.1:11434/v1") is None

    def test_none_timeout_returns_computed(self):
        """调用方未指定超时 → 返回按 token 推算值（而非 None 让下游裸奔）。"""
        got = _ensure_timeout_fits_tokens(None, 1500, base_url="http://127.0.0.1:11434/v1")
        assert got is not None and got > 60.0

    def test_min_timeout_floor(self):
        """极小 max_tokens 也要保住下限, 避免过短。"""
        got = _ensure_timeout_fits_tokens(None, 10, base_url="http://127.0.0.1:11434/v1")
        assert got >= 30.0

    @pytest.mark.parametrize("mt,expect_min", [
        (1200, 110.0),   # ai_extract
        (1500, 130.0),   # ai_analyze / onboarding_draft
        (2000, 163.0),   # 报告预生成
        (3000, 230.0),   # governance_judge
    ])
    def test_real_call_sites(self, mt, expect_min):
        """覆盖 csctalon 现有调用点的真实参数组合。"""
        got = _ensure_timeout_fits_tokens(60.0, mt, base_url="http://192.168.10.21:4080/v1")
        assert got >= expect_min, f"max_tokens={mt} 应至少放宽到 {expect_min}s, 实得 {got}"
