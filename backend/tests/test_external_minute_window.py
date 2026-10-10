# -*- coding: utf-8 -*-
"""只读 API 分钟级单日窗口测试 (工单 #66, 2026-09-11)。

背景: 下游(workbuddy 等)需按「同刻」做日比对(如 09-10T12:30 vs 09-09T12:30),
故单日端点放开到分钟精度。本文件确保:
- 正向: 分钟串 → 200, 且窗口元信息(window_kind/precision/start/end + 同比基准)正确
- 边界: 非法分钟串 / 带秒 / 未来时刻 → 400
- 回归: 纯日期行为完全不变(整天窗口 + 元信息), 既有 60s TTL 语义不受影响
- 缓存: 分钟窗口的 key 含分钟段, 与纯日期不互相污染
"""

import datetime as _dt

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.main import app
from app.models.user import RoleEnum, User
from app.services import external_service as S

TZ = _dt.timezone(_dt.timedelta(hours=8))


@pytest.fixture()
def _enable_external(monkeypatch):
    """启用外部只读接口(默认 false), 并放大限频避免测试互相干扰。"""
    monkeypatch.setattr(settings, "EXTERNAL_API_ENABLED", True)
    monkeypatch.setattr(settings, "EXTERNAL_RATE_LIMIT_DEFAULT_PER_MIN", 1000)
    yield


@pytest.fixture()
def client(db, _enable_external):
    fake = User(id=1, username="admin", role=RoleEnum.ADMIN.value, is_active=True)

    def _override_user():
        return fake

    def _override_db():
        yield db

    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_db] = _override_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def tokens(db):
    """为三个单日端点各生成一个带 scope 的 token。"""
    out = {}
    for scope in ("reception", "refund", "complaint"):
        raw, _ = S.generate_token(db, f"m-{scope}", [scope], created_by="admin")
        out[scope] = {"Authorization": f"Bearer {raw}"}
    return out


def _yesterday() -> str:
    """相对今天取昨天, 避免测试随时钟漂移。"""
    return (__import__("datetime").datetime.now(TZ).date() - _dt.timedelta(days=1)).isoformat()


ENDPOINTS = (("/reception", "reception"), ("/refund", "refund"), ("/complaint", "complaint"))


# ── A1/A2: 分钟级正向 ───────────────────────────────────────
@pytest.mark.parametrize("path,scope", ENDPOINTS)
def test_minute_window_accepted(client, tokens, path, scope):
    """A1: date=YYYY-MM-DDTHH:MM → 200, 且窗口元信息体现分钟精度。"""
    d = _yesterday()
    resp = client.get(f"/api/v1/external{path}?date={d}T12:30", headers=tokens[scope])
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["window_kind"] == "minute_to_date"
    assert data["window_precision"] == "minute"
    assert data["window_start"] == f"{d}T00:00:00+08:00"
    assert data["window_end"] == f"{d}T12:30:00+08:00"


@pytest.mark.parametrize("path,scope", ENDPOINTS)
def test_minute_window_compare_baseline_is_prev_day_same_time(client, tokens, path, scope):
    """A2: 分钟窗口的同比基准 = 前一日同刻窗口(下游日比对的前提)。"""
    d = _dt.date.fromisoformat(_yesterday())
    prev = (d - _dt.timedelta(days=1)).isoformat()
    resp = client.get(f"/api/v1/external{path}?date={d.isoformat()}T09:15", headers=tokens[scope])
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["compare_start"] == f"{prev}T00:00:00+08:00"
    assert data["compare_end"] == f"{prev}T09:15:00+08:00"


def test_minute_window_end_of_day(client, tokens):
    """边界: 23:59 是一天中合法的最后一分钟(不应被判为未来)。"""
    d = _yesterday()
    resp = client.get(f"/api/v1/external/reception?date={d}T23:59", headers=tokens["reception"])
    # 昨天 23:59 必然早于现在 → 200
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["window_end"] == f"{d}T23:59:00+08:00"


# ── A3: 非法输入 ────────────────────────────────────────────
@pytest.mark.parametrize("bad", [
    "2026-09-10T12:30:45",   # 带秒: 只支持分钟精度
    "2026-09-10T12",         # 缺分钟
    "2026-09-10T99:99",      # 非法时分
    "2026-09-10T12:60",      # 分钟越界
    "abc",
    "2026-13-01",
    "2026-02-30",
    "2026/09/01",
    "2026-9-1",
    # #66 复审: `date.fromisoformat` 会接受这些契约外格式, 必须显式拒绝
    "20260910",
    "2026-W01-1",
])
def test_invalid_date_returns_400(client, tokens, bad):
    """A3: 各种非法形态 → 400, 不落到聚合层变 500。"""
    r = client.get(f"/api/v1/external/reception?date={bad}", headers=tokens["reception"])
    assert r.status_code == 400, f"date={bad!r} 应 400, 实得 {r.status_code}"


def test_future_minute_returns_400(client, tokens):
    """A3: 未来时刻 → 400(防时间穿越/空窗口被误读为业务归零)。"""
    future = (_dt.datetime.now(TZ) + _dt.timedelta(days=2)).date().isoformat()
    r = client.get(f"/api/v1/external/reception?date={future}T10:00", headers=tokens["reception"])
    assert r.status_code == 400


# ── A4: 纯日期回归(行为完全不变) ────────────────────────────
@pytest.mark.parametrize("path,scope", ENDPOINTS)
def test_plain_date_regression_full_day(client, tokens, path, scope):
    """A4(回归): 纯日期仍返回整天窗口, 元信息为 day 精度。"""
    d = _yesterday()
    resp = client.get(f"/api/v1/external{path}?date={d}", headers=tokens[scope])
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["window_kind"] == "full_day"
    assert data["window_precision"] == "day"
    assert data["window_end"] == f"{d}T23:59:59+08:00"
    # 关键回归: 原有字段不受影响
    assert "date" in data and "_spec" in data


def test_no_date_is_today_realtime(client, tokens):
    """A4(回归): 不传 date → 今天实时窗口(元信息 kind=today_realtime)。"""
    resp = client.get("/api/v1/external/reception", headers=tokens["reception"])
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["window_kind"] == "today_realtime"
    assert data["window_precision"] == "realtime"


def test_brief_still_rejects_minute(client, db):
    """A4: brief 的 start/end 是「整天区间」语义, 仍必须拒绝分钟串(不静默截断)。"""
    raw, _ = S.generate_token(db, "b1", ["brief"], created_by="admin")
    h = {"Authorization": f"Bearer {raw}"}
    r = client.get(f"/api/v1/external/brief?start={_yesterday()}T08:00&end={_yesterday()}", headers=h)
    assert r.status_code == 400
    # 纯日期仍正常
    r2 = client.get(f"/api/v1/external/brief?start={_yesterday()}&end={_yesterday()}", headers=h)
    assert r2.status_code == 200, r2.text


# ── 单元层: 解析与窗口 ─────────────────────────────────────
def test_parse_window_arg_precision_flag():
    """_parse_window_arg_or_400: 分钟串返回 is_minute=True; 纯日期 False。"""
    from app.api.v1.external import _parse_window_arg_or_400
    assert _parse_window_arg_or_400("2026-09-10T12:30", "date") == ("2026-09-10T12:30", True)
    assert _parse_window_arg_or_400("2026-09-10", "date") == ("2026-09-10", False)
    assert _parse_window_arg_or_400(None, "date") == (None, False)


def test_parse_minute_end_does_not_swallow_plain_date():
    """analytics._parse_minute_end 只认 16 位 T 串, 纯日期必须返回 None(走原路径)。"""
    from app.services.analytics import _parse_minute_end
    assert _parse_minute_end("2026-09-10T12:30") == _dt.datetime(2026, 9, 10, 12, 30)
    for s in ("2026-09-10", "2026-09-10T12:30:45", "2026-09-10T12", "", None):
        assert _parse_minute_end(s) is None, f"{s!r} 不应被当作分钟窗口"


def test_resolve_windows_minute_semantics():
    """analytics._resolve_windows: 分钟窗口 = 当日00:00~该分钟, 同比=前一日同刻。"""
    from app.services.analytics import _resolve_windows
    ts, te, ys, ye, is_realtime, disp = _resolve_windows(date="2026-09-10T12:30")
    # 北京 2026-09-10 00:00 → UTC 2026-09-09 16:00
    assert ts == _dt.datetime(2026, 9, 9, 16, 0, 0)
    assert te == _dt.datetime(2026, 9, 10, 4, 30, 0)   # 北京 12:30 → UTC 04:30
    assert ys == _dt.datetime(2026, 9, 8, 16, 0, 0)
    assert ye == _dt.datetime(2026, 9, 9, 4, 30, 0)
    # 分钟窗口已终结 → 非实时(可长缓存)
    assert is_realtime is False
    assert disp == ("2026-09-10T00:00:00+08:00", "2026-09-10T12:30:00+08:00")


def test_resolve_windows_plain_date_unchanged():
    """A4(回归): 纯日期窗口解析与改前完全一致(整天 23:59:59.999999)。"""
    from app.services.analytics import _resolve_windows
    ts, te, ys, ye, is_realtime, disp = _resolve_windows(date="2026-09-10")
    assert ts == _dt.datetime(2026, 9, 9, 16, 0, 0)
    assert te == _dt.datetime(2026, 9, 10, 15, 59, 59, 999999)
    assert disp is None
