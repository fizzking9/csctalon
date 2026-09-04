# -*- coding: utf-8 -*-
"""外部只读开放平台 - 一键经营简报 brief 测试。

对应架构: docs/arch/A-20260902-021-外部工具只读开放平台.md §2.2
覆盖:
- _window: daily/weekly/monthly 窗口口径(日=当日0点起, 周=周一起, 月=整月/1日到当前)
- brief 组装结构: reception.by_specialty / refund.routes + a_route / complaint.by_source 齐全
- brief 只读端点: 正确 scope → 200 结构完整
"""

import datetime as _dt
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.api.v1.external import TZ_CN, _complaint_enhanced, _window
from app.core.config import settings
from app.core.database import get_db
from app.main import app
from app.models.ticket import ServiceTicket
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


# ---- 投诉增强口径(修复审查 CRITICAL): handled 需 finish>=register+3min, backlog 需归一化 ----

_REVIEW_DAY = "2026-07-15"  # 固定历史日(测试库空, 与真实数据无冲突)


def _mk_ticket(db, no: str, *, item: str = "商品投诉", status: str | None = "待处理",
               reg_delta_min: int = 0, fin_delta_min: int | None = None):
    """构造投诉工单: register_time 固定于 _REVIEW_DAY 00:00(+reg_delta_min), finish 可选(+fin_delta_min)。"""
    reg = _dt.datetime.strptime(_REVIEW_DAY, "%Y-%m-%d").replace(tzinfo=TZ_CN) + timedelta(minutes=reg_delta_min)
    fin = reg + timedelta(minutes=fin_delta_min) if fin_delta_min is not None else None
    db.add(ServiceTicket(
        ticket_no=no, service_category="投诉", service_item=item, status=status,
        register_time=reg, finish_time=fin,
    ))
    db.flush()


def test_complaint_enhanced_handled_requires_real_finish(db):
    """CRITICAL-1 回归: handled 仅统计 finish>=register+3min 的真实完成(同 _is_real_finish)。

    - T1: finish 晚 2min(<3min, 同瞬填充) → 不计 handled
    - T2: finish 晚 10min(真实完成)        → 计 handled
    两单都在窗口内登记(registered=2)。
    """
    _mk_ticket(db, "T-R-001", fin_delta_min=2)     # 非真实完成(2min)
    _mk_ticket(db, "T-R-002", fin_delta_min=10)    # 真实完成(10min)
    out = _complaint_enhanced(db, _REVIEW_DAY, _REVIEW_DAY)
    internal = next(x for x in out["by_source"] if x["source"] == "内部投诉")
    assert internal["registered"] == 2
    assert internal["handled"] == 1    # 仅 T-R-002


def test_complaint_enhanced_backlog_excludes_norm_done(db):
    """CRITICAL-2 回归: backlog 排除"状态待处理但实际已真实完成(≥3min)"(同 _norm_status)。

    - T1: 状态=待处理, finish 晚 10min(真实完成) → _norm_status=已完成 → 不进 backlog
    - T2: 状态=待处理, finish=None(真未处理)      → 进 backlog
    """
    _mk_ticket(db, "T-R-011", status="待处理", fin_delta_min=10)
    _mk_ticket(db, "T-R-012", status="待处理", fin_delta_min=None)
    out = _complaint_enhanced(db, _REVIEW_DAY, _REVIEW_DAY)
    internal = next(x for x in out["by_source"] if x["source"] == "内部投诉")
    assert internal["registered"] == 2
    assert internal["backlog"] == 1    # 仅 T-R-012


def test_complaint_enhanced_backlog_done_status_excluded(db):
    """backlog 基线: 显式已完成状态不进 backlog; 真待处理进。"""
    _mk_ticket(db, "T-R-021", status="已完成", fin_delta_min=None)
    _mk_ticket(db, "T-R-022", status="待处理", fin_delta_min=None)
    out = _complaint_enhanced(db, _REVIEW_DAY, _REVIEW_DAY)
    internal = next(x for x in out["by_source"] if x["source"] == "内部投诉")
    assert internal["registered"] == 2
    assert internal["backlog"] == 1    # 仅 T-R-022


def test_complaint_enhanced_external_registered_dedup_matched(db):
    """CRITICAL-3 回归: 外部投诉 registered 补充表减 matched 重叠(同 complaint_summary)。

    场景(2026-08 实测: 97 工单 + 243 补充, 其中 37 matched 重叠需去重):
      - 外部投诉工单 T-EXT-1(12315, 窗口内登记)
      - 补充表记录 matched 到 T-EXT-1(同一投诉两种视角) → 应从 registered 中减掉, 防双计
      - 补充表记录 matched 到"窗口外"工单(或不 matched) → 保留计外部
    构造:
      ec1.complaint_date 窗口内 + matched=T-EXT-1        → 重叠(工单侧已计) → 去重
      ec2.complaint_date 窗口内 + matched=None           → 净新增 → 计入
      ec3.complaint_date 窗口内 + matched=窗口外工单号     → 净新增 → 计入(窗口外工单未计)
    """
    from app.models.external_complaint import ExternalComplaint
    # 窗口内登记的外部投诉工单(12315 → 外部桶)
    _mk_ticket(db, "T-EXT-1", item="12315", status="已完成", fin_delta_min=None)
    # 补充表: complaint_date 用窗口日(+1min 避开午界)
    base = _dt.datetime.strptime(_REVIEW_DAY, "%Y-%m-%d").replace(tzinfo=TZ_CN)
    db.add_all([
        ExternalComplaint(complaint_no="EC-1", complaint_date=base + timedelta(minutes=1),
                          matched_ticket_no="T-EXT-1", platform="315"),
        ExternalComplaint(complaint_no="EC-2", complaint_date=base + timedelta(minutes=2),
                          matched_ticket_no=None, platform="315"),
        ExternalComplaint(complaint_no="EC-3", complaint_date=base + timedelta(minutes=3),
                          matched_ticket_no="T-OUTSIDE-WINDOW", platform="315"),
    ])
    db.flush()
    out = _complaint_enhanced(db, _REVIEW_DAY, _REVIEW_DAY)
    external = next(x for x in out["by_source"] if x["source"] == "外部投诉")
    # 工单 1(T-EXT-1) + 补充净新增 2(EC-2/EC-3, EC-1 重叠去重) = 3
    assert external["registered"] == 3, f"registered={external['registered']}, 期望 3"


# ---- complaints._source_reg_handled / _sources_reg_handled (F-P1-2, 2026-09-04) ----
# 共享 helper 供 /summary、/analysis 输出"三源 × 当日登记/当日处理(真实完成)"。
# 与 external._complaint_enhanced 同口径: registered=register∈窗口; handled=finish∈窗口
# 且 finish−register≥3min(含窗口外登记、窗口内办结的历史单 —— 用户拍板"处理=finish 落窗口即算")。


def test_source_reg_handled_real_finish_and_historical_finish(db):
    """handled 口径: 真实完成(finish−register≥3min) 且 finish 落窗口。

    - T1: register 窗口内、finish 窗口内+10min(真实完成) → registered=1, handled=1
    - T2: register 窗口内、finish 窗口内+2min(同瞬填充) → registered=1, handled=0
    - T3: register 窗口外(前一日)、finish 窗口内+30min(历史单今日办结) → registered=0, handled=1
    """
    from app.api.v1.complaints import _parse_dt, _parse_dt_end, _source_reg_handled
    base = _dt.datetime.strptime(_REVIEW_DAY, "%Y-%m-%d").replace(tzinfo=TZ_CN)
    # T1/T2: 窗口内登记
    _mk_ticket(db, "RH-1", fin_delta_min=10)    # 真实完成
    _mk_ticket(db, "RH-2", fin_delta_min=2)     # 非真实(同瞬填充)
    # T3: 窗口外登记(前一日) + 窗口内完成(历史单今日办结)
    db.add(ServiceTicket(
        ticket_no="RH-3", service_category="投诉", service_item="商品投诉",
        status="已完成", register_time=base - timedelta(days=1),
        finish_time=base + timedelta(minutes=30),
    ))
    db.flush()
    ts = _parse_dt(_REVIEW_DAY)
    te = _parse_dt_end(_REVIEW_DAY)
    reg, hdl = _source_reg_handled(db, "内部投诉", ts, te)
    assert reg == 2      # RH-1 + RH-2(窗口内登记)
    assert hdl == 2      # RH-1(真实) + RH-3(历史单今日完成); RH-2 同瞬填充不计


def test_sources_reg_handled_three_buckets(db):
    """三源分桶: 商品投诉→内部 / 微信→商户号 / 12315→外部; total 汇总。"""
    from app.api.v1.complaints import _parse_dt, _parse_dt_end, _sources_reg_handled
    _mk_ticket(db, "S-1", item="商品投诉", fin_delta_min=10)
    _mk_ticket(db, "S-2", item="微信投诉", fin_delta_min=10)
    _mk_ticket(db, "S-3", item="12315", fin_delta_min=10)
    db.flush()
    ts = _parse_dt(_REVIEW_DAY)
    te = _parse_dt_end(_REVIEW_DAY)
    out = _sources_reg_handled(db, ts, te)
    assert out["内部投诉"] == {"registered": 1, "handled": 1}
    assert out["商户号投诉"] == {"registered": 1, "handled": 1}
    assert out["外部投诉"]["registered"] >= 1   # 含 315/卡商补充表净新增(库中历史补充表可能计入)
    assert out["total"]["registered"] >= 3
    assert out["total"]["handled"] >= 3

