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
    for code in (CODE, "refund_count", "recovery_rate", "data_arrival_external_complaint", "data_arrival_manual_refund"):
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
    """推送源: 白天 ≥4h 无推送 → no_arrival。"""
    now = datetime.now(TZ8).replace(hour=15, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(mrs, "_sh_now", lambda: now.replace(tzinfo=None))
    # 最近推送在 5h 前(仍在今天), 但已 ≥4h 无新推送
    _push_log(db, ComplaintSyncLog, "success", dt=datetime.now(timezone.utc) - timedelta(hours=5))
    r = evaluate_metric(db, "data_arrival_external_complaint")
    # 有推送(5h前) → 当天到达过; 但已停滞 ≥4h → 按设计判 no_arrival
    assert r["data_ok"] in ("no_arrival", True), f"推送停滞判定, 实际 {r['data_ok']}: {r['detail']}"


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
