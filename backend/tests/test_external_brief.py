# -*- coding: utf-8 -*-
"""外部只读开放平台 - 一键经营简报 brief 测试。

对应架构: docs/arch/A-20260902-021-外部工具只读开放平台.md §2.2
覆盖:
- _window: daily/weekly/monthly 窗口口径(日=当日0点起, 周=周一起, 月=整月/1日到当前)
- brief 组装结构: reception.by_specialty / refund.routes + a_route / complaint.by_source 齐全
- brief 只读端点: 正确 scope → 200 结构完整
"""

import datetime as _dt

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.api.v1.external import _window
from app.core.config import settings
from app.core.database import get_db
from app.main import app
from app.models.user import RoleEnum, User
from app.services import external_service as S

TZ = _dt.timezone(_dt.timedelta(hours=8))


@pytest.fixture()
def brief_client(db, monkeypatch):
    monkeypatch.setattr(settings, "EXTERNAL_API_ENABLED", True)
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


# ---- _window 纯逻辑(不依赖 DB) ----
def test_window_daily_default_today():
    """daily 缺省 → 今天 0 点起。"""
    w = _window("daily", None)
    today = _dt.datetime.now(TZ).date().isoformat()
    assert w["period"] == "daily"
    assert w["start"] == today
    assert w["date"] == today
    assert w["date_start"].startswith(f"{today}T00:00:00")


def test_window_daily_given_date_full_day():
    """daily 指定历史日期 → 整天(非实时)。"""
    w = _window("daily", "2026-08-01")
    assert w["date"] == "2026-08-01"
    assert w["is_realtime"] is False
    assert w["date_end"].startswith("2026-08-01T23:59:59")


def test_window_weekly_monday():
    """weekly → 周一起。"""
    w = _window("weekly", None)
    today = _dt.datetime.now(TZ).date()
    monday = today - _dt.timedelta(days=today.weekday())
    assert w["start"] == monday.isoformat()


def test_window_monthly_first_to_current():
    """monthly → 1 日到当前(未满月时实时)。"""
    w = _window("monthly", None)
    today = _dt.datetime.now(TZ).date()
    assert w["start"] == today.replace(day=1).isoformat()
    import calendar
    last_day = calendar.monthrange(today.year, today.month)[1]
    if today.day != last_day:
        assert w["is_realtime"] is True


# ---- brief 组装/接口 ----
def test_brief_endpoint_structure(brief_client, db):
    """正向: brief 只读端点返回完整结构(接待专项/退款/投诉增强)。"""
    raw, _ = S.generate_token(db, "brief-token", ["brief"], created_by="admin")
    resp = brief_client.get("/api/v1/external/brief?period=daily", headers={"Authorization": f"Bearer {raw}"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["period"] == "daily"
    # reception: total + by_specialty
    assert "total" in data["reception"] and "by_specialty" in data["reception"]
    # refund: routes 四路 + a_route 涉诉率
    assert "total_amount" in data["refund"]
    assert set(data["refund"]["routes"].keys()) == {"route_a", "route_b", "route_c", "route_d"}
    assert "a_route" in data["refund"] and "litigation_ratio" in data["refund"]["a_route"]
    # complaint: total + by_source 三源
    assert set(data["complaint"].keys()) == {"total", "by_source"}
    sources = {x["source"] for x in data["complaint"]["by_source"]}
    assert sources == {"内部投诉", "商户号投诉", "外部投诉"}
    for item in data["complaint"]["by_source"]:
        assert set(item.keys()) >= {"source", "registered", "handled", "backlog"}
    # _spec
    assert "_spec" in data


def test_brief_requires_brief_scope(brief_client, db):
    """边界: 无 brief scope 的 token 调 brief → 403。"""
    raw, _ = S.generate_token(db, "no-brief", ["reception"], created_by="admin")
    resp = brief_client.get("/api/v1/external/brief", headers={"Authorization": f"Bearer {raw}"})
    assert resp.status_code == 403


def test_brief_invalid_period(brief_client, db):
    """边界: 非法 period → 422(Query 校验)。"""
    raw, _ = S.generate_token(db, "b1", ["brief"], created_by="admin")
    resp = brief_client.get("/api/v1/external/brief?period=yearly", headers={"Authorization": f"Bearer {raw}"})
    assert resp.status_code == 422
