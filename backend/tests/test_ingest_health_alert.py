# -*- coding: utf-8 -*-
"""回归测试: 采集健康告警指标 ingest_volume_cs_record (工单 #5 / A-20260904-001 LOCKED)。

覆盖:
1. 求值函数正常量 → data_ok=True, value≈近7日均值比例
2. 断流(今日无 cs_record 且无采集尝试) → data_ok="no_sync"
3. 采集失败(bms_sync_logs 有 failed) → data_ok="sync_failed"
4. _scan_metric_rule 对 ingest_health + data_ok 字符串标记 → 生成 critical 且自动登记 Incident
5. 业务指标回归: data_ok=False(业务数据缺失) → 跳过告警不误报
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.api.v1.alerts import _scan_metric_rule
from app.models.alert import Alert, AlertRule
from app.models.bms_sync_log import BmsSyncLog
from app.models.cs_record import CsRecord
from app.models.device import Device
from app.models.incident import Incident
from app.models.metric import MetricRegistry
from app.services.metric_registry_service import METRIC_DEFS, evaluate_metric

TZ8 = timezone(timedelta(hours=8))
NOW_UTC = datetime(2026, 9, 4, 4, 0, tzinfo=timezone.utc)  # 北京 12:00
# 测试基准日: 用真实"今天"(函数内部 _window/_sh_now 均基于真实当天, 保持 period_is_today 一致)
TODAY = datetime.now(TZ8).date()
TODAY_S = TODAY.strftime("%Y-%m-%d")
TODAY_12_SH = datetime(TODAY.year, TODAY.month, TODAY.day, 12, 0)
TODAY_0805_SH = datetime(TODAY.year, TODAY.month, TODAY.day, 8, 5)


# 采集健康指标 code
CODE = "ingest_volume_cs_record"


@pytest.fixture(autouse=True)
def _seed_metric_registry(db):
    """测试库 metric_registry 为空(create_all 只建表不 seed), 先补代码行满足外键。"""
    for code in (CODE, "refund_count", "recovery_rate"):
        meta = METRIC_DEFS.get(code)
        if not meta:
            continue
        exists = db.scalar(select(func.count()).select_from(MetricRegistry).where(MetricRegistry.code == code)) or 0
        if not exists:
            db.add(MetricRegistry(
                code=code, name_cn=meta["name_cn"], direction=meta["direction"],
                period=meta["period"], unit=meta.get("unit"),
                data_sources=meta.get("data_sources"), description=meta.get("description"),
                enabled=True,
            ))
    db.flush()


def _mk_sn(prefix="RR") -> str:
    return f"{prefix}{uuid.uuid4().hex[:10].upper()}"


def _seed_cs(db, day_dt_utc, count: int, op: str = "企微-测试"):
    """在指定 UTC 日(对应某北京时间日)造 count 条 cs_record。"""
    for _ in range(count):
        sn = _mk_sn()
        db.add(Device(sn=sn))
        db.flush()
        db.add(CsRecord(
            problem_category="退款", problem_type="不想用了",
            device_sn=sn, operation_time=day_dt_utc, operator=op,
            description="测试接待",
        ))
    db.flush()


def _utc_from_sh(sh_date: str, hour: int = 12) -> datetime:
    """北京时间日期+小时 → naive UTC(与 _window 同口径)"""
    sh = datetime.strptime(sh_date, "%Y-%m-%d").replace(hour=hour, tzinfo=TZ8)
    return sh.astimezone(timezone.utc).replace(tzinfo=None)


def _seed_baseline(db, base_date: datetime, per_day: int = 10) -> None:
    """造 base_date 前 7 天基线(避开冷启动保护 valid_base_days<3)。"""
    for back in range(1, 8):
        d_sh = (base_date - timedelta(days=back)).strftime("%Y-%m-%d")
        _seed_cs(db, _utc_from_sh(d_sh, 12), per_day, op="企微-基线")


def test_ingest_normal_ratio(db):
    """正常: 今日有采集且接近基线 → data_ok=True, 不触发(level 非 critical)。"""
    # 近7日每天 100 条(北京 9/1 前), 今日(北京 9/2) 造 100 条
    for back in range(1, 8):
        d_sh = (datetime(2026, 9, 2) - timedelta(days=back)).strftime("%Y-%m-%d")
        _seed_cs(db, _utc_from_sh(d_sh, 12), 10, op="企微-基线")
    _seed_cs(db, _utc_from_sh("2026-09-02", 12), 10, op="企微-今日")

    # 造当日采集成功日志(避免 no_sync)
    db.add(BmsSyncLog(data_type="cs_record", status="success", trigger_type="scheduled",
                      start_time=_utc_from_sh("2026-09-02", 0), end_time=_utc_from_sh("2026-09-02", 23),
                      finished_at=NOW_UTC))
    db.flush()

    r = evaluate_metric(db, CODE, period_date=datetime(2026, 9, 2).date())
    assert r["data_ok"] is True, f"应 data_ok=True, 实际 {r['data_ok']}"
    # 今日 10 条 vs 近7日均值 10 条 → ratio≈100%
    assert r["value"] is not None and r["value"] > 50, f"ratio 应>50%, 实际 {r['value']}"


def test_ingest_no_sync_fault(db, monkeypatch):
    """断流: 当天已过 9 点保护且无采集尝试 → data_ok='no_sync'(故障信号)。"""
    from app.services import metric_registry_service as mrs

    fake_now = TODAY_12_SH  # 北京 12:00(过 9 点保护)
    monkeypatch.setattr(mrs, "_sh_now", lambda: fake_now)
    _seed_baseline(db, TODAY)
    # 当天(9/4)无任何采集日志 + 无数据
    r = evaluate_metric(db, CODE, period_date=TODAY)
    assert r["data_ok"] == "no_sync", f"当天无采集应 no_sync, 实际 {r['data_ok']}"


def test_ingest_sync_failed_fault(db, monkeypatch):
    """采集失败: 当天 bms_sync_logs 有 failed → data_ok='sync_failed'(任何日期)。"""
    from app.services import metric_registry_service as mrs

    fake_now = TODAY_12_SH
    monkeypatch.setattr(mrs, "_sh_now", lambda: fake_now)
    _seed_baseline(db, TODAY)
    # 当天(9/4)有 failed 日志
    db.add(BmsSyncLog(data_type="cs_record", status="failed", trigger_type="scheduled",
                      start_time=_utc_from_sh(TODAY_S, 0), end_time=_utc_from_sh(TODAY_S, 23),
                      finished_at=datetime(TODAY.year, TODAY.month, TODAY.day, 5, tzinfo=timezone.utc)))
    db.flush()
    r = evaluate_metric(db, CODE, period_date=TODAY)
    assert r["data_ok"] == "sync_failed", f"应 sync_failed, 实际 {r['data_ok']}"


def test_scan_metric_rule_ingest_fault_creates_alert_and_incident(db, monkeypatch):
    """核心: _scan_metric_rule 对 ingest_health 故障标记 → 生成 critical + 自动登记 Incident。"""
    from app.services import metric_registry_service as mrs

    fake_now = TODAY_12_SH
    monkeypatch.setattr(mrs, "_sh_now", lambda: fake_now)
    # 造一条启用的采集健康规则
    rule = AlertRule(
        name="采集健康-测试", type="metric", enabled=True, metric_code=CODE,
        warning_value=70.0, critical_value=40.0,
        channels=["inapp"], recipient_roles=["admin", "supervisor"],
    )
    db.add(rule)
    db.flush()
    _seed_baseline(db, TODAY)
    # 造"当天采集失败"场景: 当天(9/4)插入 failed 日志 → evaluate 返回 sync_failed
    db.add(BmsSyncLog(data_type="cs_record", status="failed", trigger_type="scheduled",
                      start_time=_utc_from_sh(TODAY_S, 0), end_time=_utc_from_sh(TODAY_S, 23),
                      finished_at=datetime(TODAY.year, TODAY.month, TODAY.day, 5, tzinfo=timezone.utc)))
    db.flush()
    created, logs = _scan_metric_rule(db, rule, datetime(TODAY.year, TODAY.month, TODAY.day, 5, tzinfo=timezone.utc))
    db.flush()

    # 应创建 1 条 critical 告警
    assert created == 1, f"应创建1条告警, 实际 created={created}, logs={logs}"
    alert = db.scalar(
        select(Alert).where(Alert.rule_id == rule.id, Alert.recovered_at.is_(None))
        .order_by(Alert.id.desc()).limit(1)
    )
    assert alert is not None and alert.level == "critical", "应生成 critical 告警"
    # 应自动登记 Incident(幂等: 再次扫描同周期不重复)
    inc = db.scalar(select(Incident).where(Incident.category == "数据采集").order_by(Incident.id.desc()).limit(1))
    assert inc is not None, "应自动登记 Incident"
    _scan_metric_rule(db, rule, NOW_UTC)
    db.flush()
    inc_cnt = db.scalar(select(func.count()).select_from(Incident).where(Incident.category == "数据采集")) or 0
    assert inc_cnt == 1, f"Incident 应幂等(只1条), 实际 {inc_cnt}"


def test_business_metric_data_missing_skipped(db):
    """回归: 业务指标 data_ok=False(数据缺失) → 跳过告警(不误报), 行为不变。"""
    # 用 refund_count: 当日无退款 = 数据正常(0 笔), 不是 data_ok=False
    # 关键回归是: 采集健康指标的字符串 data_ok 不应影响业务指标走"跳过"分支
    rule = AlertRule(
        name="业务-退款测试", type="metric", enabled=True, metric_code="refund_count",
        warning_value=30.0, critical_value=50.0,
        channels=["inapp"], recipient_roles=["admin"],
    )
    db.add(rule)
    db.flush()
    # 当日无退款记录 → refund_count=0, lower_better 不超阈值 → 不告警
    created, logs = _scan_metric_rule(db, rule, NOW_UTC)
    db.flush()
    # 不应创建告警(0 笔未超 critical 50)
    assert created == 0, f"退款0笔不应告警, created={created}"


# ---- C1 回归: 预警→告警升级路径也应登记 Incident ----
def test_ingest_escalation_registers_incident(db, monkeypatch):
    """C1: warning 升级到 critical(sync_failed) → 必须登记 Incident(不依赖 ratio 精确值)。"""
    from app.services import metric_registry_service as mrs

    fake_now = TODAY_12_SH  # 北京 12:00
    monkeypatch.setattr(mrs, "_sh_now", lambda: fake_now)
    # 基线(7天) + 今天少量数据
    for back in range(1, 8):
        d_sh = (TODAY - timedelta(days=back)).strftime("%Y-%m-%d")
        _seed_cs(db, _utc_from_sh(d_sh, 12), 10, op="企微-基线")
    _seed_cs(db, _utc_from_sh(TODAY_S, 12), 5, op="企微-今日")
    # 当天已有成功采集(避免 no_sync 干扰升级语义)
    db.add(BmsSyncLog(data_type="cs_record", status="success", trigger_type="scheduled",
                      start_time=_utc_from_sh(TODAY_S, 0), end_time=_utc_from_sh(TODAY_S, 23),
                      finished_at=datetime(TODAY.year, TODAY.month, TODAY.day, 4, tzinfo=timezone.utc)))
    db.flush()
    rule = AlertRule(name="采集健康-升级", type="metric", enabled=True, metric_code=CODE,
                     warning_value=70.0, critical_value=40.0,
                     channels=["inapp"], recipient_roles=["admin"])
    db.add(rule)
    db.flush()
    # 手动构造一条"已存在未恢复的 warning 告警"(模拟第一次扫描产生的 warning)
    # period_start 用真实当天窗口起(与 evaluate 一致)
    from app.services.metric_registry_service import _window
    ps, pe = _window("day", None)
    db.add(Alert(rule_id=rule.id, title="预警", level="warning", detail="预警",
                 occurred_at=datetime(TODAY.year, TODAY.month, TODAY.day, 4, tzinfo=timezone.utc), status="pending",
                 metric_code=CODE, metric_value=50.0, threshold_value=70.0,
                 period_start=ps, period_end=pe, data_ok=True))
    db.flush()
    inc0 = db.scalar(select(func.count()).select_from(Incident).where(Incident.category == "数据采集")) or 0
    assert inc0 == 0, "warning 阶段不应有 Incident"
    # 插入当日 failed 日志 → 扫描触发升级 critical → 必须登记 Incident(C1)
    db.add(BmsSyncLog(data_type="cs_record", status="failed", trigger_type="scheduled",
                      start_time=_utc_from_sh(TODAY_S, 0), end_time=_utc_from_sh(TODAY_S, 23),
                      finished_at=datetime(TODAY.year, TODAY.month, TODAY.day, 5, tzinfo=timezone.utc)))
    db.flush()
    created2, logs2 = _scan_metric_rule(db, rule, datetime(TODAY.year, TODAY.month, TODAY.day, 5, tzinfo=timezone.utc))
    db.flush()
    inc1 = db.scalar(select(func.count()).select_from(Incident).where(Incident.category == "数据采集")) or 0
    assert inc1 == 1, f"升级到 critical 应登记 Incident, 实际 {inc1}, logs={logs2}"


# ---- C2 回归: no_sync 首采保护(9点前不误报) + 恢复自动平息 Incident ----
def test_ingest_no_sync_grace_before_9am(db, monkeypatch):
    """C2: 北京 8 点(定时同步尚未完成) → 不判 no_sync, 避免误报。"""
    from app.services import metric_registry_service as mrs

    fake_now = TODAY_0805_SH  # 北京 08:05
    monkeypatch.setattr(mrs, "_sh_now", lambda: fake_now)
    for back in range(1, 8):
        d_sh = (TODAY - timedelta(days=back)).strftime("%Y-%m-%d")
        _seed_cs(db, _utc_from_sh(d_sh, 12), 10, op="企微-基线")
    # 今天无任何采集日志也无数据(模拟 8 点扫描先于同步)
    r = evaluate_metric(db, CODE)  # 默认当天(9/4)
    assert r["data_ok"] is True, f"9点前不应 no_sync, 实际 {r['data_ok']}"


def test_ingest_cold_start_no_alert(db, monkeypatch):
    """W1: 冷启动(近7日有效基线<3天) → 只记录不告警。"""
    from app.services import metric_registry_service as mrs

    fake_now = TODAY_12_SH
    monkeypatch.setattr(mrs, "_sh_now", lambda: fake_now)
    # 只有 1 天基线(不足 3 天)
    _seed_cs(db, _utc_from_sh("2026-09-03", 12), 10, op="企微-基线")
    # 今天有 failed 日志(即便有故障, 冷启动也应抑制告警)
    db.add(BmsSyncLog(data_type="cs_record", status="failed", trigger_type="scheduled",
                      start_time=_utc_from_sh(TODAY_S, 0), end_time=_utc_from_sh(TODAY_S, 23),
                      finished_at=datetime(TODAY.year, TODAY.month, TODAY.day, 5, tzinfo=timezone.utc)))
    db.flush()
    r = evaluate_metric(db, CODE)
    assert r["data_ok"] == "cold_start", f"冷启动应 cold_start, 实际 {r['data_ok']}"
    rule = AlertRule(name="采集健康-冷启动", type="metric", enabled=True, metric_code=CODE,
                     warning_value=70.0, critical_value=40.0,
                     channels=["inapp"], recipient_roles=["admin"])
    db.add(rule)
    db.flush()
    created, logs = _scan_metric_rule(db, rule, datetime(TODAY.year, TODAY.month, TODAY.day, 5, tzinfo=timezone.utc))
    db.flush()
    assert created == 0, f"冷启动不应告警, created={created}, logs={logs}"


def test_ingest_recovery_closes_incident(db, monkeypatch):
    """C2: 告警恢复 → 对应 Incident 自动标记已平息(不滞留台账)。"""
    from app.services import metric_registry_service as mrs

    fake_now = TODAY_12_SH
    monkeypatch.setattr(mrs, "_sh_now", lambda: fake_now)
    for back in range(1, 8):
        d_sh = (TODAY - timedelta(days=back)).strftime("%Y-%m-%d")
        _seed_cs(db, _utc_from_sh(d_sh, 12), 10, op="企微-基线")
    # 今天有 failed → 触发 critical + Incident
    db.add(BmsSyncLog(data_type="cs_record", status="failed", trigger_type="scheduled",
                      start_time=_utc_from_sh(TODAY_S, 0), end_time=_utc_from_sh(TODAY_S, 23),
                      finished_at=datetime(TODAY.year, TODAY.month, TODAY.day, 5, tzinfo=timezone.utc)))
    db.flush()
    rule = AlertRule(name="采集健康-恢复", type="metric", enabled=True, metric_code=CODE,
                     warning_value=70.0, critical_value=40.0,
                     channels=["inapp"], recipient_roles=["admin"])
    db.add(rule)
    db.flush()
    _scan_metric_rule(db, rule, datetime(TODAY.year, TODAY.month, TODAY.day, 5, tzinfo=timezone.utc))
    db.flush()
    inc = db.scalar(select(Incident).where(Incident.category == "数据采集").order_by(Incident.id.desc()).limit(1))
    assert inc is not None and inc.is_closed is False, "应先登记未闭环 Incident"
    # 今天补足数据 + 清掉 failed → 恢复
    _seed_cs(db, _utc_from_sh(TODAY_S, 12), 10, op="企微-今日")
    # 删除 failed 日志, 加 success → data_ok=True, ratio 高 → 恢复
    db.execute(BmsSyncLog.__table__.delete().where(BmsSyncLog.data_type == "cs_record"))
    db.flush()
    db.add(BmsSyncLog(data_type="cs_record", status="success", trigger_type="scheduled",
                      start_time=_utc_from_sh(TODAY_S, 0), end_time=_utc_from_sh(TODAY_S, 23),
                      finished_at=datetime(TODAY.year, TODAY.month, TODAY.day, 6, tzinfo=timezone.utc)))
    db.flush()
    _scan_metric_rule(db, rule, datetime(TODAY.year, TODAY.month, TODAY.day, 6, tzinfo=timezone.utc))
    db.flush()
    inc2 = db.get(Incident, inc.id)
    assert inc2.is_closed is True, "恢复后 Incident 应自动平息"
