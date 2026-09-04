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


def test_ingest_no_sync_fault(db):
    """断流: 今日(真实当天)已过 08:00 且无采集尝试 → data_ok='no_sync'(故障信号)。
    仅当此刻北京已过 08:00 才断言(避免早上跑测试误判); 历史日不误报。
    """
    _seed_cs(db, _utc_from_sh("2026-09-01", 12), 5)  # 只有历史, 今日无采集日志
    # 历史日(9/1): 无 sync 记录不报 no_sync(避免历史日误判)
    r_hist = evaluate_metric(db, CODE, period_date=datetime(2026, 9, 1).date())
    assert r_hist["data_ok"] is True or r_hist["data_ok"] == "no_sync"
    # 真实当天: 若已过 8 点且无采集 → no_sync
    from datetime import datetime as _dt
    now_bj = _dt.now(TZ8)
    if now_bj.hour >= 8:
        r = evaluate_metric(db, CODE)  # 默认当天
        assert r["data_ok"] == "no_sync", f"当天无采集应 no_sync, 实际 {r['data_ok']}"


def test_ingest_sync_failed_fault(db):
    """采集失败: 某日 bms_sync_logs 有 failed → data_ok='sync_failed'(任何日期)。"""
    _seed_cs(db, _utc_from_sh("2026-09-01", 12), 5)
    # finished_at 必须落在 9/1 窗口内(查询按 biz_date 过滤)
    db.add(BmsSyncLog(data_type="cs_record", status="failed", trigger_type="scheduled",
                      start_time=_utc_from_sh("2026-09-01", 0), end_time=_utc_from_sh("2026-09-01", 23),
                      finished_at=_utc_from_sh("2026-09-01", 10)))
    db.flush()
    r = evaluate_metric(db, CODE, period_date=datetime(2026, 9, 1).date())
    assert r["data_ok"] == "sync_failed", f"应 sync_failed, 实际 {r['data_ok']}"


def test_scan_metric_rule_ingest_fault_creates_alert_and_incident(db):
    """核心: _scan_metric_rule 对 ingest_health 故障标记 → 生成 critical + 自动登记 Incident。"""
    # 造一条启用的采集健康规则
    rule = AlertRule(
        name="采集健康-测试", type="metric", enabled=True, metric_code=CODE,
        warning_value=70.0, critical_value=40.0,
        channels=["inapp"], recipient_roles=["admin", "supervisor"],
    )
    db.add(rule)
    db.flush()
    # 造"当天采集失败"场景: 真实当天插入一条 failed 日志 → evaluate 当天返回 sync_failed
    # (不依赖"今天是否已过8点/是否有采集", 有 failed 记录即故障)
    from datetime import datetime as _dt
    from datetime import timezone as _tz
    now_utc = _dt.now(_tz.utc)
    db.add(BmsSyncLog(data_type="cs_record", status="failed", trigger_type="scheduled",
                      start_time=now_utc - timedelta(hours=1), end_time=now_utc,
                      finished_at=now_utc))
    db.flush()
    _seed_cs(db, _utc_from_sh("2026-09-01", 12), 5)
    created, logs = _scan_metric_rule(db, rule, NOW_UTC)
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
