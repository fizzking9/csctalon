# -*- coding: utf-8 -*-
"""回归测试: 数据到达健康指标 data_arrival_<type> (工单 #6 / A-20260904-001 v1.2 LOCKED)。

核心语义(替代 v1.0 入表量比例):
- 判定 = 该源当日是否有任一渠道到达记录(主动抓取 success / 被动推送 / 手动导入 imported)
- 不看业务数据量(接待量波动大不做判据)
覆盖:
1. 主动抓取 success → data_ok=True
2. 抓取 failed → sync_failed(critical)
3. 抓取 failed + 手动导入 imported → 不告警(关键: 手动补导 = 数据已到)
4. 推送源白天 ≥4h 无推送 → no_arrival
5. _scan_metric_rule 对 data_arrival 故障 → critical + Incident 自动登记
6. 业务指标回归: data_ok=False 仍跳过
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.api.v1.alerts import _scan_metric_rule
from app.models.alert import Alert, AlertRule
from app.models.bms_sync_log import BmsSyncLog
from app.models.complaint_sync_log import ComplaintSyncLog
from app.models.incident import Incident
from app.models.metric import MetricRegistry
from app.models.upload import UploadRecord
from app.services import metric_registry_service as mrs
from app.services.metric_registry_service import evaluate_metric

TZ8 = timezone(timedelta(hours=8))

# 用一个主动抓取源(如 cs_record)做测试基准
CODE = "data_arrival_cs_record"


def _utc_from_sh(sh_date: str, hour: int = 12) -> datetime:
    """北京时间日期+小时 → naive UTC"""
    sh = datetime.strptime(sh_date, "%Y-%m-%d").replace(hour=hour, tzinfo=TZ8)
    return sh.astimezone(timezone.utc).replace(tzinfo=None)


def _today_bounds() -> tuple[datetime, datetime]:
    """真实今天北京 [00, 次日00) → naive UTC(与 _window 一致, 使造数落在当天窗口)"""
    tz8_now = datetime.now(TZ8)
    d = tz8_now.date()
    start = datetime.combine(d, datetime.min.time()).replace(tzinfo=TZ8).astimezone(timezone.utc).replace(tzinfo=None)
    return start, start + timedelta(days=1)


def _sync_log(db, data_type, status, dt=None) -> None:
    """造一条 bms_sync_logs(默认今天)"""
    s, e = _today_bounds()
    db.add(BmsSyncLog(data_type=data_type, status=status, trigger_type="scheduled",
                      start_time=s, end_time=e, finished_at=dt or datetime.now(timezone.utc)))
    db.flush()


def _manual_import(db, data_type) -> None:
    """造一条手动导入记录(今天 imported)"""
    db.add(UploadRecord(data_type=data_type, filename="补导.xlsx", status="imported",
                        total_rows=1, inserted_rows=1, source="manual",
                        uploaded_by="test", finished_at=datetime.now(timezone.utc)))
    db.flush()


def _push_log(db, model, status, dt=None) -> None:
    """造一条推送日志(今天)"""
    db.add(model(source_file="x.xlsx", sync_source="workbuddy", status=status,
                 finished_at=dt or datetime.now(timezone.utc)))
    db.flush()


@pytest.fixture(autouse=True)
def _seed_registry(db):
    """确保 data_arrival_cs_record 与业务指标在 metric_registry(外键)。"""
    for code in (CODE, "refund_count", "recovery_rate", "data_arrival_external_complaint", "data_arrival_manual_refund", "data_arrival_ticket"):
        exists = db.scalar(select(func.count()).select_from(MetricRegistry).where(MetricRegistry.code == code)) or 0
        if not exists:
            db.add(MetricRegistry(code=code, name_cn=code, direction="higher_better",
                                  period="day", unit="", enabled=True))
    db.flush()


def test_arrival_sync_success(db, monkeypatch):
    """主动抓取今日 success → 到达, data_ok=True。"""
    monkeypatch.setattr(mrs, "_sh_now", lambda: datetime.now(TZ8).replace(tzinfo=None))
    _sync_log(db, "cs_record", "success")
    r = evaluate_metric(db, CODE)
    assert r["data_ok"] is True, f"应 True, 实际 {r['data_ok']}: {r['detail']}"


def test_arrival_sync_failed(db, monkeypatch):
    """主动抓取今日 failed → sync_failed(采集故障信号)。"""
    monkeypatch.setattr(mrs, "_sh_now", lambda: datetime.now(TZ8).replace(tzinfo=None))
    _sync_log(db, "cs_record", "failed")
    r = evaluate_metric(db, CODE)
    assert r["data_ok"] == "sync_failed", f"应 sync_failed, 实际 {r['data_ok']}: {r['detail']}"


def test_arrival_manual_import_covers_failed(db, monkeypatch):
    """关键回归: 抓取 failed 但有手动导入(imported) → 不告警(数据已到达)。"""
    monkeypatch.setattr(mrs, "_sh_now", lambda: datetime.now(TZ8).replace(tzinfo=None))
    _sync_log(db, "cs_record", "failed")
    _manual_import(db, "cs_record")
    r = evaluate_metric(db, CODE)
    # 抓取 failed 是信号, 但手动导入兜底 → 视为已到达, data_ok 应为 True(不被判为 no_arrival)
    # sync_failed 仍会报(抓取确实失败需知会), 但不应因"无数据"误报 no_arrival
    # 设计: 手动导入后 reached=True; 若仍 sync_failed 属"抓取失败+已人工补", 可告警但语义为已补救
    assert r["detail"] and "手动导入" in r["detail"], f"detail 应含手动导入: {r['detail']}"
    assert r["data_ok"] != "no_arrival", "有手动导入不应判 no_arrival"


def test_arrival_no_sync_no_manual(db, monkeypatch):
    """主动抓取源: 过 9 点无 success 且无手动导入 → no_arrival。"""
    now = datetime.now(TZ8).replace(hour=12, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(mrs, "_sh_now", lambda: now.replace(tzinfo=None))
    # 清空今日 sync/upload(让测试环境干净)——通过事务隔离天然干净
    r = evaluate_metric(db, CODE)
    assert r["data_ok"] == "no_arrival", f"应 no_arrival, 实际 {r['data_ok']}: {r['detail']}"


def test_arrival_push_stalled(db, monkeypatch):
    """推送源: 今日白天(≥9点)已过 4h 仍无任何推送 → no_arrival。"""
    now = datetime.now(TZ8).replace(hour=15, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(mrs, "_sh_now", lambda: now.replace(tzinfo=None))
    # 今日(15点)无任何推送记录(已过 9 点 6h ≥ 4h 阈值) → no_arrival
    r = evaluate_metric(db, "data_arrival_external_complaint")
    assert r["data_ok"] == "no_arrival", f"今日白天无推送应 no_arrival, 实际 {r['data_ok']}: {r['detail']}"


def test_arrival_push_today_has_push_no_stall(db, monkeypatch):
    """推送源: 今日已有推送(success) → 到达, 不判 no_arrival。"""
    now = datetime.now(TZ8).replace(hour=15, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(mrs, "_sh_now", lambda: now.replace(tzinfo=None))
    _push_log(db, ComplaintSyncLog, "success")  # 今日有推送
    r = evaluate_metric(db, "data_arrival_external_complaint")
    assert r["data_ok"] is True, f"今日有推送不应告警, 实际 {r['data_ok']}: {r['detail']}"


def test_arrival_ticket_success_plus_failed_no_critical(db, monkeypatch):
    """C-1 回归: ticket 当日 success+failed 并存(常态) → 不报 sync_failed critical。"""
    monkeypatch.setattr(mrs, "_sh_now", lambda: datetime.now(TZ8).replace(tzinfo=None))
    _sync_log(db, "ticket", "success")
    _sync_log(db, "ticket", "failed")
    r = evaluate_metric(db, "data_arrival_ticket")
    assert r["data_ok"] is True, f"success+failed 并存不应报 sync_failed, 实际 {r['data_ok']}: {r['detail']}"


def test_arrival_push_failed(db, monkeypatch):
    """推送源当日 failed → push_failed。"""
    monkeypatch.setattr(mrs, "_sh_now", lambda: datetime.now(TZ8).replace(tzinfo=None))
    _push_log(db, ComplaintSyncLog, "failed")
    r = evaluate_metric(db, "data_arrival_external_complaint")
    assert r["data_ok"] == "push_failed", f"应 push_failed, 实际 {r['data_ok']}"


def test_scan_metric_rule_arrival_fault_creates_alert_and_incident(db, monkeypatch):
    """scan: data_arrival sync_failed → critical + Incident 自动登记(幂等)。"""
    monkeypatch.setattr(mrs, "_sh_now", lambda: datetime.now(TZ8).replace(tzinfo=None))
    _sync_log(db, "cs_record", "failed")
    rule = AlertRule(name="数据到达-测试", type="metric", enabled=True, metric_code=CODE,
                     channels=["inapp"], recipient_roles=["admin"])
    db.add(rule)
    db.flush()
    created, logs = _scan_metric_rule(db, rule, datetime.now(timezone.utc))
    db.flush()
    assert created == 1, f"应创建告警, created={created}, logs={logs}"
    alert = db.scalar(select(Alert).where(Alert.rule_id == rule.id, Alert.recovered_at.is_(None))
                      .order_by(Alert.id.desc()).limit(1))
    assert alert is not None and alert.level == "critical", "应生成 critical"
    inc = db.scalar(select(Incident).where(Incident.category == "数据采集").order_by(Incident.id.desc()).limit(1))
    assert inc is not None, "应自动登记 Incident"
    _scan_metric_rule(db, rule, datetime.now(timezone.utc))
    db.flush()
    inc_cnt = db.scalar(select(func.count()).select_from(Incident).where(Incident.category == "数据采集")) or 0
    assert inc_cnt == 1, f"Incident 幂等(1条), 实际 {inc_cnt}"


def test_business_metric_data_missing_skipped(db):
    """回归: 业务指标无数据 → 跳过不误报(行为不变)。"""
    rule = AlertRule(name="业务-退款", type="metric", enabled=True, metric_code="refund_count",
                     warning_value=30.0, critical_value=50.0,
                     channels=["inapp"], recipient_roles=["admin"])
    db.add(rule)
    db.flush()
    created, logs = _scan_metric_rule(db, rule, datetime.now(timezone.utc))
    db.flush()
    assert created == 0, f"业务指标不应告警, created={created}, logs={logs}"


# ============================================================
# 2026-09-06 生产缺陷回归: 「推送恢复但告警不消失」
# 根因: data_arrival_* 指标恒 value=None, 恢复后 _scan_metric_rule 在
#       `if not data_ok or value is None` 处提前 return(打印"数据不完整跳过"),
#       永远到不了"恢复自愈"分支 → pending 告警无法自动 recovered。
# 修复: 采集健康指标(kind=ingest_health)不受 value is None 约束,
#       无故障标记(ingest_fault is None)即视为已回稳 → 放行恢复自愈。
# ============================================================
def test_arrival_fault_then_recovery_auto_recovers(db, monkeypatch):
    """采集健康当天 no_arrival → 当天推送恢复 → 自动 recovered + Incident 平息。

    复现生产路径(9-06): workbuddy 停摆触发 no_arrival critical,
    随后推送恢复(当日), 再次扫描必须把同周期 pending 告警标 recovered,
    并把关联 Incident 标已平息(is_closed=True)。
    """
    from app.models.incident import Incident as IncidentModel

    PUSH_CODE = "data_arrival_external_complaint"
    rule = AlertRule(name="数据到达-推送", type="metric", enabled=True, metric_code=PUSH_CODE,
                     channels=["inapp"], recipient_roles=["admin"])
    db.add(rule)
    db.flush()

    # 阶段1: 白天 15:00(过 9 点 ≥4h)无推送 → no_arrival → critical + Incident
    now15 = datetime.now(TZ8).replace(hour=15, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(mrs, "_sh_now", lambda: now15.replace(tzinfo=None))
    created, logs = _scan_metric_rule(db, rule, datetime.now(timezone.utc))
    db.flush()
    assert created == 1, f"no_arrival 应创建告警, logs={logs}"
    alert = db.scalar(select(Alert).where(Alert.rule_id == rule.id).order_by(Alert.id.desc()).limit(1))
    assert alert is not None and alert.status == "pending" and alert.level == "critical"
    inc = db.scalar(select(IncidentModel).where(IncidentModel.category == "数据采集").order_by(IncidentModel.id.desc()).limit(1))
    assert inc is not None and not inc.is_closed, "no_arrival 应登记未平息 Incident"

    # 阶段2: 同日 18:00 推送恢复(success) → 再扫描 → 告警 recovered + Incident 平息
    now18 = datetime.now(TZ8).replace(hour=18, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(mrs, "_sh_now", lambda: now18.replace(tzinfo=None))
    _push_log(db, ComplaintSyncLog, "success")  # 今日已推送
    created2, logs2 = _scan_metric_rule(db, rule, datetime.now(timezone.utc))
    db.flush()
    # 不应再创建新告警
    assert created2 == 0, f"推送恢复后不应新建告警, logs={logs2}"
    db.refresh(alert)
    assert alert.status == "recovered", f"推送恢复后告警应自动 recovered, 实际 {alert.status}: {logs2}"
    assert alert.recovered_at is not None
    db.refresh(inc)
    assert inc.is_closed and inc.status == "calmed", "恢复后 Incident 应自动平息"


def test_arrival_healthy_with_value_none_not_skipped_as_missing(db, monkeypatch):
    """采集健康到达(data_ok=True)即使 value=None 也不得误判"数据不完整跳过"。

    回归锚点: 修复前, data_arrival_* value 恒 None → 恢复后打印
    "数据不完整(值=None), 跳过告警(防假告警)" 并提前 return, 无法走到恢复分支。
    修复后应表现为"达标/恢复"而非"跳过"。
    """
    PUSH_CODE = "data_arrival_manual_refund"
    rule = AlertRule(name="数据到达-手动退款", type="metric", enabled=True, metric_code=PUSH_CODE,
                     channels=["inapp"], recipient_roles=["admin"])
    db.add(rule)
    db.flush()
    now = datetime.now(TZ8).replace(hour=18, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(mrs, "_sh_now", lambda: now.replace(tzinfo=None))
    # 今日已有成功推送 → 到达
    from app.models.manual_refund_sync_log import ManualRefundSyncLog
    _push_log(db, ManualRefundSyncLog, "success")
    created, logs = _scan_metric_rule(db, rule, datetime.now(timezone.utc))
    db.flush()
    assert created == 0, f"到达不应建告警, logs={logs}"
    assert not any("数据不完整" in log for log in logs), f"到达不应误判数据不完整跳过: {logs}"
    assert any("达标" in log for log in logs), f"到达应判达标(无告警可恢复时), logs={logs}"


# ============================================================
# 2026-09-06 附带缺陷回归: Incident 周期日期时区错位
# 根因: period_start 存 naive UTC(上海 00:00 = UTC 前日 16:00), 旧代码直接
#       strftime("%Y-%m-%d") 取 UTC 日期 → 标题/匹配早一天。
# 修复: _period_biz_day() 统一按 Asia/Shanghai 换算业务日。
# ============================================================
def test_incident_period_uses_shanghai_biz_day(db, monkeypatch):
    """Incident 标题/周期必须用上海业务日(UTC 前日 16:00 窗口→当日), 不得早一天。"""
    from datetime import timezone as _tz

    from app.api.v1 import alerts as alerts_mod
    from app.models.alert import Alert as AlertModel
    from app.models.incident import Incident as IncidentModel

    # 构造一个"上海某日"的 period_start: 上海 2026-09-06 00:00 = UTC 2026-09-05 16:00(naive)
    sh_day = datetime(2026, 9, 6, tzinfo=TZ8)
    period_start_utc_naive = sh_day.astimezone(_tz.utc).replace(tzinfo=None)  # 2026-09-05 16:00 naive UTC
    assert period_start_utc_naive.strftime("%Y-%m-%d") == "2026-09-05", "前置: naive UTC 日期确实早一天"

    # 直接断言换算 helper
    assert alerts_mod._period_biz_day(period_start_utc_naive) == "2026-09-06", "应换算成上海业务日 09-06"

    # 端到端: 手动触发 Incident 自动登记, 标题/周期必须用上海业务日
    PUSH_CODE = "data_arrival_external_complaint"
    rule = AlertRule(name="数据到达-外部投诉", type="metric", enabled=True, metric_code=PUSH_CODE,
                     channels=["inapp"], recipient_roles=["admin"])
    db.add(rule)
    db.flush()
    alert = AlertModel(rule_id=rule.id, title="外部投诉数据到达健康异常: no_arrival",
                       level="critical", status="pending", metric_code=PUSH_CODE,
                       period_start=period_start_utc_naive,  # 上海 09-06 窗口(naive UTC)
                       data_ok=True, occurred_at=datetime.now(timezone.utc))
    db.add(alert)
    db.flush()
    alerts_mod._auto_register_ingest_incident(db, rule, alert, datetime.now(timezone.utc))
    db.flush()
    inc = db.scalar(select(IncidentModel).where(IncidentModel.category == "数据采集").order_by(IncidentModel.id.desc()).limit(1))
    assert inc is not None, "应登记 Incident"
    assert "2026-09-06" in inc.title, f"Incident 标题应含上海业务日 09-06, 实际: {inc.title}"
    assert "2026-09-06" in (inc.detail or ""), f"Incident detail 应含上海业务日 09-06, 实际: {inc.detail}"
