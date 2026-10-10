# -*- coding: utf-8 -*-
"""E 路时效与采集告警测试 (工单 #191)。

覆盖:
- notify_empty_result: 仅 scheduled + "应有数据"白名单源才告警(manual/未知源不打扰)
- EXPECTED_DATA_SOURCES / FRESHNESS_TARGETS: E 路必须在册且阈值=1 天(T+1 上游)
- check_freshness: 最新业务日 → lag_days / stale 判定(E 路超 1 天即 stale)
- list_low_freq_schedules: 低频源 options.low_freq_hour/window_days 读取, 高频源不参与
- _spec_brief / glossary: E 路 T+1 时效口径已进 Prompt(否则模型会把 E=0 读成"未提供")
"""

import datetime as _dt

from app.services.ai_glossary import GLOSSARY
from app.services.source_freshness_service import (
    EXPECTED_DATA_SOURCES,
    FRESHNESS_TARGETS,
    _alerted,
    check_freshness,
    notify_empty_result,
)

# ── 白名单与目标配置(纯配置断言, 不连库) ────────────────────────────────


def test_e_route_is_expected_and_freshness_tracked():
    """E 路必须同时进"应有数据"白名单与时效巡检目标, 阈值=1 天(上游 T+1)。"""
    assert "refund_record_report" in EXPECTED_DATA_SOURCES
    targets = {t["source"]: t for t in FRESHNESS_TARGETS}
    assert targets["refund_record_report"]["max_lag_days"] == 1


def test_notify_empty_result_skips_manual_and_unknown_sources():
    """manual 档是人在盯 → 不告警; 非白名单源(天然可为空) → 不告警。"""
    _alerted.clear()
    assert notify_empty_result(data_type="refund_record_report", name="E",
                               trigger_type="manual") is False
    assert notify_empty_result(data_type="device_tag_binding", name="标签明细",
                               trigger_type="scheduled") is False
    assert _alerted == {}   # 未写任何告警记录


# ── check_freshness(用可控 db 桩, 规避测试库既有数据干扰) ───────────────


class _FakeDB:
    """只提供 check_freshness 需要的 db.execute(...).scalar()(规避测试库既有数据干扰)。"""

    def __init__(self, value):
        self._v = value

    def execute(self, *_a, **_k):
        return self

    def scalar(self):
        return self._v

    def close(self):
        pass


def test_check_freshness_marks_stale_when_lag_exceeds_threshold():
    """已过当天采集小时, 且最新业务日落后 2 天(>阈值 1) → stale=True, lag_days 准确。"""
    tz = _dt.timezone(_dt.timedelta(hours=8))
    now = _dt.datetime.now(tz).replace(hour=16, minute=5, second=0, microsecond=0)
    latest = (now - _dt.timedelta(days=2)).replace(hour=23, minute=0)
    rows = {r["source"]: r for r in check_freshness(_FakeDB(latest), now=now)}
    e = rows["refund_record_report"]
    assert e["lag_days"] == 2
    assert e["stale"] is True


def test_check_freshness_fresh_when_lag_within_threshold():
    """已过采集小时, 最新业务日=昨天 → 未超期(stale=False)。"""
    tz = _dt.timezone(_dt.timedelta(hours=8))
    now = _dt.datetime.now(tz).replace(hour=16, minute=5, second=0, microsecond=0)
    latest = (now - _dt.timedelta(days=1)).replace(hour=23, minute=0)
    rows = {r["source"]: r for r in check_freshness(_FakeDB(latest), now=now)}
    e = rows["refund_record_report"]
    assert e["lag_days"] == 1 and e["stale"] is False


def test_check_freshness_no_morning_false_positive():
    """#191 修正核心: 当天采集小时(E 路 15:00)未到 → 基准日=昨天, 不因 T+1 上游误报。

    真实时间线: D 日 15:05 采集到 D-1(上游 T+1) → D+1 06:05 时最新仍是 D-1,
    而今天的 15:00 档还没到。用"今天(D+1)"为基准会算成落后 2 天 → 每个上午误报一次;
    改用基准日(D)后为 1 天 → 不报。
    """
    tz = _dt.timezone(_dt.timedelta(hours=8))
    now = _dt.datetime.now(tz).replace(hour=6, minute=5, second=0, microsecond=0)
    latest = (now - _dt.timedelta(days=2)).replace(hour=23, minute=0)   # = D-1
    rows = {r["source"]: r for r in check_freshness(_FakeDB(latest), now=now)}
    e = rows["refund_record_report"]
    assert e["pending_today"] is True
    assert e["baseline"] == (now.date() - _dt.timedelta(days=1)).isoformat()
    assert e["lag_days"] == 1 and e["stale"] is False


def test_check_freshness_after_hour_catches_missed_run():
    """当天采集小时已过且没采到 → 报超期(修完误报后仍能抓住真漏采)。"""
    tz = _dt.timezone(_dt.timedelta(hours=8))
    now = _dt.datetime.now(tz).replace(hour=16, minute=5, second=0, microsecond=0)
    latest = (now - _dt.timedelta(days=2)).replace(hour=23, minute=0)   # 今日档失败 → 仍停在 D-2
    rows = {r["source"]: r for r in check_freshness(_FakeDB(latest), now=now)}
    e = rows["refund_record_report"]
    assert e["pending_today"] is False
    assert e["lag_days"] == 2 and e["stale"] is True


def test_check_freshness_no_data_is_stale_but_not_misreported():
    """完全无数据(max=None) → stale=True(确属断更)且 lag_days=None(不臆造天数)。"""
    tz = _dt.timezone(_dt.timedelta(hours=8))
    now = _dt.datetime.now(tz).replace(hour=16, minute=5, second=0, microsecond=0)
    rows = {r["source"]: r for r in check_freshness(_FakeDB(None), now=now)}
    assert rows["refund_record_report"]["lag_days"] is None
    assert rows["refund_record_report"]["stale"] is True


def test_freshness_expected_hour_matches_seed_config():
    """漂移守卫: 巡检目标的 expected_by_hour 必须与该源种子配置的 low_freq_hour 一致。

    两者分处两个模块(监控口径 vs 采集档位), 不一致会导致"采集窗口"与"判定基准"错位
    ——要么漏采不报, 要么照旧误报。
    """
    from app.services.bms_fetcher._fetch import BMS_DATA_SOURCES

    for t in FRESHNESS_TARGETS:
        seed_hour = (BMS_DATA_SOURCES.get(t["source"], {}).get("options") or {}).get("low_freq_hour")
        assert seed_hour == t["expected_by_hour"], (
            f"{t['source']}: 巡检 expected_by_hour={t['expected_by_hour']} "
            f"≠ 采集种子 low_freq_hour={seed_hour}"
        )


# ── list_low_freq_schedules(源级档位参数) ───────────────────────────────


def _upsert_cfg(db, data_type, **kw):
    """测试库已由种子注册过各源 → 存在则改, 不存在则插(避免唯一键冲突)。"""
    from app.models.sync_source_config import SyncSourceConfig

    row = db.query(SyncSourceConfig).filter_by(data_type=data_type).first()
    if row is None:
        row = SyncSourceConfig(data_type=data_type, name=data_type,
                               base_url="https://bms.invalid", page_path="/x")
        db.add(row)
    for k, v in kw.items():
        setattr(row, k, v)
    db.commit()
    return row


def test_list_low_freq_schedules_reads_source_options(db):
    """低频源 options.low_freq_hour/window_days 被读出; 高频源(refund)不参与。"""
    from app.services.sync_source_config_service import list_low_freq_schedules

    _upsert_cfg(db, "refund_record_report", default_sync=True, enabled=True,
                options={"low_freq_hour": 15, "window_days": 3})
    _upsert_cfg(db, "bms_order", default_sync=True, enabled=True, options=None)
    _upsert_cfg(db, "refund", default_sync=True, enabled=True, options={"low_freq_hour": 15})

    sched = list_low_freq_schedules(db)
    assert sched["refund_record_report"] == {"hour": 15, "window_days": 3}
    assert "bms_order" not in sched          # 未配置 → 不进表(按默认档位处理)
    assert "refund" not in sched             # 高频源不走低频档


def test_list_low_freq_schedules_rejects_invalid_values(db):
    """非法值(小时越界 / window_days<=0)被忽略, 不产生脏配置。"""
    from app.services.sync_source_config_service import list_low_freq_schedules

    _upsert_cfg(db, "return_tracking", default_sync=True, enabled=True,
                options={"low_freq_hour": 99, "window_days": 0})
    assert "return_tracking" not in list_low_freq_schedules(db)


def test_low_freq_done_backfill_uses_low_source_logs_not_high_slots():
    """#191 回填判据: 只看低频源自身的 scheduled 成功记录(高频槽位不算)。

    反例(本用例要防的回归): E 路配 15:00, 而 15:00 也是高频槽位 —— 若按槽位回填,
    重启后会把"当天 15:00 低频档"误判为已跑 → E 路整天漏采。
    """
    import datetime as _dt

    from app.services.bms_scheduler import low_freq_done_from_logs

    tz = _dt.timezone(_dt.timedelta(hours=8))
    today = _dt.datetime.now(tz).date()
    # 低频源在 15:00 成功过 → 记 done; 高频源(refund)不算; 昨天的记录不算
    rows = [
        ("refund_record_report", _dt.datetime.combine(today, _dt.time(15, 3, tzinfo=tz))),
        ("refund", _dt.datetime.combine(today, _dt.time(15, 1, tzinfo=tz))),        # 高频源, 忽略
        ("device_combo", _dt.datetime.combine(today, _dt.time(6, 5, tzinfo=tz))),   # 默认 6:00 档
        ("refund_record_report", _dt.datetime.combine(
            today - _dt.timedelta(days=1), _dt.time(15, 3, tzinfo=tz))),            # 昨天, 忽略
    ]
    sched = {"refund_record_report": {"hour": 15, "window_days": 3}}
    got = low_freq_done_from_logs(rows, sched, today_key=today.isoformat(), today_cn=today,
                                  default_hour=6, tz_cn=tz)
    assert got == {f"{today.isoformat()}:15": True, f"{today.isoformat()}:6": True}


def test_low_freq_done_backfill_ignores_failed_and_missing_time():
    """失败记录 / finished_at 为空 → 不记 done(避免把没跑的档当已跑)。"""
    import datetime as _dt

    from app.services.bms_scheduler import low_freq_done_from_logs

    tz = _dt.timezone(_dt.timedelta(hours=8))
    today = _dt.datetime.now(tz).date()
    got = low_freq_done_from_logs([("refund_record_report", None)], {"refund_record_report": {"hour": 15}},
                                  today_key=today.isoformat(), today_cn=today,
                                  default_hour=6, tz_cn=tz)
    assert got == {}


# ── AI 侧 E 时效口径(直击"E 对账基准：暂无"误报) ────────────────────────


def test_spec_brief_declares_e_route_t_plus_one():
    """口径卡必须声明 E 上游 T+1 与"按数据截止日表述", 并给出字段名。"""
    from app.api.v1.external import _spec_brief

    spec = _spec_brief()
    assert "actual_cashflow_e" in spec
    text = spec["actual_cashflow_e"]
    assert "T+1" in text
    assert "actual_cashflow_e_until" in text
    assert "未提供" in text   # 明确禁止写"E 未提供"


def test_glossary_refund_routes_declares_e_timeliness():
    """退款四路口径卡同样声明 E 时效(glossary 路由命中时也要带上)。"""
    text = GLOSSARY["refund_routes"]
    assert "T+1" in text and "actual_cashflow_e_until" in text


def test_refund_brief_exposes_e_until_key(db):
    """brief 的 refund 段必须带 actual_cashflow_e_until(即使值为 None 也保留键, 供 AI 判读时效)。"""
    from app.api.v1.external import _build_brief

    rf = (_build_brief(db, "daily", None).get("refund") or {})
    assert "actual_cashflow_e_until" in rf
