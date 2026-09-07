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


def test_window_weekly_given_date_full_week():
    """v1.1: weekly + date → 该 date 所在 ISO 周(周一~周日), 历史周整天。"""
    # 2026-08-05 是周三 → 所在周 2026-08-03(周一) ~ 2026-08-09(周日)
    w = _window("weekly", "2026-08-05")
    assert w["start"] == "2026-08-03"
    assert w["end"] == "2026-08-09"
    assert w["date"] == "2026-08-05"
    assert w["is_realtime"] is False
    assert w["date_start"].startswith("2026-08-03T00:00:00")
    assert w["date_end"].startswith("2026-08-09T23:59:59")


def test_window_monthly_first_to_current():
    """monthly → 1 日到当前(未满月时实时)。"""
    w = _window("monthly", None)
    today = _dt.datetime.now(TZ).date()
    assert w["start"] == today.replace(day=1).isoformat()
    import calendar
    last_day = calendar.monthrange(today.year, today.month)[1]
    if today.day != last_day:
        assert w["is_realtime"] is True


# ---- v1.2: _window start/end 任意区间模式(历史标准周/单日/非法) ----
def test_window_range_historical_full_week():
    """range: start/end 历史整周(周一~周日) → 整天非实时, grain=week。"""
    # 2026-08-03 是周一? 校验: 取 2026-08-03 所在周的周一~周日(ISO 自然周)。
    s, e = "2026-08-03", "2026-08-09"
    w = _window("range", None, start=s, end=e)
    assert w["period"] == "range"
    assert w["start"] == s and w["end"] == e
    assert w["is_realtime"] is False
    assert w["grain"] == "week"
    assert w["range"] == {"start": s, "end": e, "span_days": 7}
    assert w["date_start"].startswith(f"{s}T00:00:00")
    assert w["date_end"].startswith(f"{e}T23:59:59")


def test_window_range_single_day():
    """range: start==end(单日) → 整天, grain=day(等价 period=daily&date)。"""
    w = _window("range", None, start="2026-08-01", end="2026-08-01")
    assert w["grain"] == "day"
    assert w["is_realtime"] is False
    assert w["range"]["span_days"] == 1
    assert w["date_start"].startswith("2026-08-01T00:00:00")


def test_window_range_end_today_realtime():
    """range: end==今天 → 实时截止 now。"""
    today = _dt.datetime.now(TZ).date().isoformat()
    # start 取今天前 6 天(=滚动 7 天区间, 非 ISO 周)
    import datetime as _dt2
    s = (_dt2.date.fromisoformat(today) - _dt.timedelta(days=6)).isoformat()
    w = _window("range", None, start=s, end=today)
    assert w["is_realtime"] is True
    assert w["grain"] == "week"


def test_window_range_invalid():
    """range: 非法参数/起>止/只传一个 → 400。"""
    import pytest
    from fastapi import HTTPException
    # 只传 start
    with pytest.raises(HTTPException) as e1:
        _window("range", None, start="2026-08-01")
    assert e1.value.status_code == 400
    # 只传 end
    with pytest.raises(HTTPException) as e2:
        _window("range", None, end="2026-08-01")
    assert e2.value.status_code == 400
    # 起>止
    with pytest.raises(HTTPException) as e3:
        _window("range", None, start="2026-08-09", end="2026-08-03")
    assert e3.value.status_code == 400
    # 非法格式
    with pytest.raises(HTTPException) as e4:
        _window("range", None, start="abc", end="2026-08-03")
    assert e4.value.status_code == 400


def test_window_range_future_end_rejected():
    """护栏: end 晚于今天(北京) → 400(防时间穿越/未来空窗)。"""
    import pytest
    from fastapi import HTTPException
    today = _dt.datetime.now(TZ).date()
    future = (today + timedelta(days=1)).isoformat()
    past = (today - timedelta(days=7)).isoformat()
    with pytest.raises(HTTPException) as exc:
        _window("range", None, start=past, end=future)
    assert exc.value.status_code == 400
    assert "不能晚于今天" in exc.value.detail


def test_window_range_exceeds_max_days_rejected(monkeypatch):
    """护栏: 跨度超过 EXTERNAL_BRIEF_MAX_RANGE_DAYS → 400。"""
    import pytest
    from fastapi import HTTPException

    from app.core.config import settings
    # 临时把上限调小到 7 天, 构造 8 天区间验证拦截(避免依赖真实 93 天造 94 天跨年数据)
    monkeypatch.setattr(settings, "EXTERNAL_BRIEF_MAX_RANGE_DAYS", 7)
    today = _dt.datetime.now(TZ).date()
    # 8 天区间(今天往前 7 天 ~ 今天) → 超过 7 天上限
    s = (today - timedelta(days=7)).isoformat()
    e = today.isoformat()
    with pytest.raises(HTTPException) as exc:
        _window("range", None, start=s, end=e)
    assert exc.value.status_code == 400
    assert "跨度" in exc.value.detail and "上限" in exc.value.detail
    # 还原上限后, 同 8 天区间允许(默认 93 天)
    monkeypatch.undo()
    w = _window("range", None, start=s, end=e)
    assert w["range"]["span_days"] == 8


def test_brief_range_exceeds_max_days_http(brief_client, db, monkeypatch):
    """HTTP: brief start/end 超跨度上限 → 400。"""
    from app.core.config import settings
    monkeypatch.setattr(settings, "EXTERNAL_BRIEF_MAX_RANGE_DAYS", 7)
    raw, _ = S.generate_token(db, "brief-wide", ["brief"], created_by="admin")
    today = _dt.datetime.now(TZ).date()
    s = (today - timedelta(days=7)).isoformat()
    resp = brief_client.get(
        f"/api/v1/external/brief?start={s}&end={today.isoformat()}",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 400


def test_brief_range_future_end_http(brief_client, db):
    """HTTP: brief start/end end 为未来 → 400。"""
    raw, _ = S.generate_token(db, "brief-future", ["brief"], created_by="admin")
    today = _dt.datetime.now(TZ).date()
    s = (today - timedelta(days=1)).isoformat()
    future = (today + timedelta(days=1)).isoformat()
    resp = brief_client.get(
        f"/api/v1/external/brief?start={s}&end={future}",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 400


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


# ---- v1.2: brief start/end 任意区间 HTTP 回看 ----
def test_brief_range_endpoint_historical_week(brief_client, db):
    """HTTP: brief?start=&end= 历史标准周 → 200, 结构与 period 模式一致 + range 透出。"""
    raw, _ = S.generate_token(db, "brief-range", ["brief"], created_by="admin")
    # 历史 ISO 自然周(2026-08-03 周一 ~ 08-09 周日)
    resp = brief_client.get(
        "/api/v1/external/brief?start=2026-08-03&end=2026-08-09",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["period"] == "range"
    assert data["range"] == {"start": "2026-08-03", "end": "2026-08-09", "span_days": 7}
    assert data["date_start"].startswith("2026-08-03T00:00:00")
    # 结构与 period 模式一致(核心段齐全)
    assert "total" in data["reception"] and "by_specialty" in data["reception"]
    assert set(data["refund"]["routes"].keys()) == {"route_a", "route_b", "route_c", "route_d"}
    assert set(data["complaint"].keys()) == {"total", "by_source"}
    assert "_spec" in data


def test_brief_range_requires_pair(brief_client, db):
    """边界: 只传 start/end 之一 → 400。"""
    raw, _ = S.generate_token(db, "brief-range-bad", ["brief"], created_by="admin")
    h = {"Authorization": f"Bearer {raw}"}
    r1 = brief_client.get("/api/v1/external/brief?start=2026-08-03", headers=h)
    assert r1.status_code == 400
    r2 = brief_client.get("/api/v1/external/brief?end=2026-08-03", headers=h)
    assert r2.status_code == 400
    # 起>止
    r3 = brief_client.get("/api/v1/external/brief?start=2026-08-09&end=2026-08-03", headers=h)
    assert r3.status_code == 400


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


def test_supplementary_registered_handled(db):
    """/supplementary 返回 登记/处理 计数(F-P1-2)。

    - 3 条 315 记录(窗口内 complaint_date): registered=3
    - 其中 1 条 result∈办结类(和解结案) → handled=1; 1 条 result=空但 handle_time 窗口内 → handled+1
    """
    from app.api.v1.complaints import complaint_supplementary
    from app.models.external_complaint import ExternalComplaint
    base = _dt.datetime.strptime(_REVIEW_DAY, "%Y-%m-%d").replace(tzinfo=TZ_CN)
    db.add_all([
        ExternalComplaint(complaint_no="SP-1", complaint_date=base + timedelta(minutes=1),
                          result="和解结案", handle_time=None, platform="315"),
        ExternalComplaint(complaint_no="SP-2", complaint_date=base + timedelta(minutes=2),
                          result="待跟进", handle_time=base + timedelta(minutes=30), platform="315"),
        ExternalComplaint(complaint_no="SP-3", complaint_date=base + timedelta(minutes=3),
                          result=None, handle_time=None, platform="315"),
    ])
    db.flush()
    resp = complaint_supplementary(db, None, start=_REVIEW_DAY, end=_REVIEW_DAY,
                                   source_type="external", matched=None, limit=100)
    # FastAPI 端点用 R(data=...) 返回; 兼容 R 模型(.data)或裸 dict
    data = getattr(resp, "data", resp)
    if data is None and isinstance(resp, dict):
        data = resp.get("data", resp)
    body = data if isinstance(data, dict) else (data.model_dump() if hasattr(data, "model_dump") else {})
    assert body["registered"] == 3   # SP-1/2/3 均在窗口
    assert body["handled"] == 2      # SP-1 办结类 + SP-2 handle_time 窗口内
    assert body["total"] == 3


def test_detail_subject_filter_normalized(db):
    """/detail subject 按归一化主体过滤(F-P2-4)。

    商户号投诉工单 registrant 存 RAW 主体名(深圳市云客猫科技有限公司), subject_ranking
    用归一名(云客猫)。subject=云客猫 应能筛出该行(entity 归一后匹配)。
    - T1: 微信投诉(→商户号), registrant=深圳市云客猫科技有限公司 → subject 过滤命中
    - T2: 微信投诉(→商户号), registrant=大秦WiFi管家 → 不命中
    """
    from app.api.v1.complaints import complaint_detail
    base = _dt.datetime.strptime(_REVIEW_DAY, "%Y-%m-%d").replace(tzinfo=TZ_CN)
    db.add_all([
        ServiceTicket(ticket_no="SUB-1", service_category="投诉", service_item="微信投诉",
                      status="已完成", register_time=base + timedelta(minutes=1),
                      finish_time=base + timedelta(minutes=10), registrant="深圳市云客猫科技有限公司"),
        ServiceTicket(ticket_no="SUB-2", service_category="投诉", service_item="微信投诉",
                      status="已完成", register_time=base + timedelta(minutes=2),
                      finish_time=base + timedelta(minutes=11), registrant="大秦WiFi管家"),
    ])
    db.flush()
    # ⚠️ 绕过 @cached_json 直接测原始业务函数(2026-09-05 修复):
    #   包装版在 Redis 命中时可能返回其它测试写入的"空 items 旧缓存" → 偶发 items=0;
    #   CI 无 Redis 时 cache miss 返回 R 对象, 原测试只认 dict 会崩。
    #   用 __wrapped__ 取原始函数, 测真实 subject 过滤逻辑(不依赖缓存/Redis)。
    resp = complaint_detail.__wrapped__(db, None, start=_REVIEW_DAY, end=_REVIEW_DAY,
                                        source_type="商户号投诉", subject="云客猫", limit=100,
                                        include_supplementary=False)
    data = getattr(resp, "data", resp)
    body = data if isinstance(data, dict) else (data.model_dump() if hasattr(data, "model_dump") else {})
    items = body.get("items") or []
    assert len(items) == 1, f"期望只命中云客猫主体, 实际 {len(items)}"
    assert items[0]["no"] == "SUB-1"  # detail 输出 no 字段
    assert items[0]["entity"] == "深圳市云客猫科技有限公司"  # 保留 RAW, 归一仅用于过滤
    assert body["total"] == 1


def test_detail_subject_filter_matches_attached_315(db):
    """/detail subject 命中挂靠到工单行的 315 补充(W2 回归, 2026-09-04)。

    315 记录 SN 匹配到"商户号工单"(registrant=大秦WiFi) → include_supplementary 时
    315 挂在主行 supplementary 下, 主行 entity=大秦WiFi 非该 315 主体。
    主体 subject=云客猫 应能通过 supplementary[].company_entity=云客猫科技 命中该行。
    """
    from app.api.v1.complaints import complaint_detail
    from app.models.device import Device
    from app.models.external_complaint import ExternalComplaint
    base = _dt.datetime.strptime(_REVIEW_DAY, "%Y-%m-%d").replace(tzinfo=TZ_CN)
    db.add(Device(sn="DEV-W2"))  # 满足 external_complaints.device_sn 外键
    # 商户号工单 SN=DEV-W2(registrant=大秦WiFi)
    db.add(ServiceTicket(ticket_no="SUB-W2", service_category="投诉", service_item="微信投诉",
                         status="已完成", device_sn="DEV-W2",
                         register_time=base + timedelta(minutes=1),
                         finish_time=base + timedelta(minutes=10), registrant="大秦WiFi管家"))
    # 315 同 SN + company_entity=深圳市云客猫科技有限公司 + matched 到该工单 → 挂靠
    db.add(ExternalComplaint(complaint_no="EC-W2", device_sn="DEV-W2",
                             complaint_date=base + timedelta(minutes=2),
                             company_entity="深圳市云客猫科技有限公司", platform="315",
                             matched_ticket_no="SUB-W2"))
    db.flush()
    # 绕过 cached_json 直接测原始业务函数(2026-09-05: 避免 Redis 缓存/无缓存下行为差异)
    resp = complaint_detail.__wrapped__(db, None, start=_REVIEW_DAY, end=_REVIEW_DAY,
                                        source_type="商户号投诉", subject="云客猫", limit=100,
                                        include_supplementary=True)
    data = getattr(resp, "data", resp)
    body = data if isinstance(data, dict) else (data.model_dump() if hasattr(data, "model_dump") else {})
    items = body.get("items") or []
    assert len(items) == 1, f"期望经 supplementary 命中云客猫主体行, 实际 {len(items)}"
    assert items[0]["no"] == "SUB-W2"
    # 主行 entity 回填补充 company_entity(或保留 registrant), supplementary 含云客猫
    suppl_ents = [s.get("company_entity") for s in (items[0].get("supplementary") or []) if s.get("company_entity")]
    assert any("云客猫" in (e or "") for e in suppl_ents), f"supplementary 应含云客猫主体: {suppl_ents}"


# ============================================================
# 2026-09-06 缺陷回归: 外部投诉「处理」未并入 315 补充表已办结
# 根因: _source_reg_handled 的 handled 只算工单真实完成(finish−register≥3min),
#       workbuddy 推送进 external_complaints 且已办结(result∈办结类)的记录从不计
#       入 → 外部投诉 daily 处理率虚低、积压恒高(476 登记 vs 47 处理 的生产现象)。
# 修复: external 桶 handled 并入 315 补充表已办结, 扣除 matched 工单已在工单侧计
#       handled 的部分(防同一笔双算)。
# ============================================================
def test_source_reg_handled_external_handled_includes_suppl_done(db):
    """外部投诉桶: 315 补充表已办结(result=和解结案)计入 handled。

    场景:
      - 外部工单 T-EH-1(12315) 窗口内登记+真实完成 → registered=1, handled=1(工单侧)
      - 315 记录 EH-1: 窗口内、result=和解结案(办结)、matched=None → handled 应 +1
      - 315 记录 EH-2: 窗口内、result=None(未办结) → 不计 handled
    """
    from app.api.v1.complaints import _parse_dt, _parse_dt_end, _source_reg_handled
    from app.models.external_complaint import ExternalComplaint
    base = _dt.datetime.strptime(_REVIEW_DAY, "%Y-%m-%d").replace(tzinfo=TZ_CN)
    _mk_ticket(db, "T-EH-1", item="12315", status="已完成", fin_delta_min=10)
    db.add_all([
        ExternalComplaint(complaint_no="EH-1", complaint_date=base + timedelta(minutes=1),
                          result="和解结案", platform="315"),
        ExternalComplaint(complaint_no="EH-2", complaint_date=base + timedelta(minutes=2),
                          result=None, platform="315"),
    ])
    db.flush()
    ts = _parse_dt(_REVIEW_DAY)
    te = _parse_dt_end(_REVIEW_DAY)
    reg, hdl = _source_reg_handled(db, "外部投诉", ts, te)
    # registered: 工单 T-EH-1(1) + 补充表净新增 EH-1/EH-2(2, 未 matched 工单不去重) = 3
    assert reg == 3, f"registered={reg}, 期望 3"
    # handled: 工单 T-EH-1(真实完成 1) + 315 EH-1(办结 1) = 2; EH-2 未办结不计
    assert hdl == 2, f"handled={hdl}, 期望 2(工单1 + 315办结1)"


def test_source_reg_handled_external_done_matched_no_double_count(db):
    """防双算回归: 315 办结记录 matched 到"窗口内真实完成"工单 → 工单侧已计, 不重复加。

    场景:
      - 外部工单 T-EH-2(12315) 窗口内登记+真实完成(handled=1 工单侧)
      - 315 记录 EH-3: 办结 + matched=T-EH-2(同一投诉两种视角) → 工单侧已计 handled,
        补充侧办结必须去重, 否则 hdl=2 双算
    """
    from app.api.v1.complaints import _parse_dt, _parse_dt_end, _source_reg_handled
    from app.models.external_complaint import ExternalComplaint
    base = _dt.datetime.strptime(_REVIEW_DAY, "%Y-%m-%d").replace(tzinfo=TZ_CN)
    _mk_ticket(db, "T-EH-2", item="12315", status="已完成", fin_delta_min=10)
    db.add(ExternalComplaint(complaint_no="EH-3", complaint_date=base + timedelta(minutes=1),
                             result="和解结案", platform="315",
                             matched_ticket_no="T-EH-2"))
    db.flush()
    ts = _parse_dt(_REVIEW_DAY)
    te = _parse_dt_end(_REVIEW_DAY)
    reg, hdl = _source_reg_handled(db, "外部投诉", ts, te)
    # registered: T-EH-2(1) + 补充 EH-3 已 matched 工单 → 去重(0) = 1
    assert reg == 1, f"registered={reg}, 期望 1(补充 matched 去重)"
    # handled: 工单 1; EH-3 办结 matched 到该工单 → 工单侧已计, 不重复 → 仍 1
    assert hdl == 1, f"handled={hdl}, 期望 1(防双算, 不应为 2)"

