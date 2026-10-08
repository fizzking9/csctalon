# -*- coding: utf-8 -*-
"""工单#2 / A-20260904-002 3.3 缺口待补队列 —— 单元测试。

覆盖 gap_queue_service:
1. record_from_sync_results: 失败→open / 部分成功→partial / 全天覆盖成功→filled
2. _biz_date_of: naive/aware 时间 → 北京业务日
3. list_gaps / mark_ignored / mark_filled
4. settle_and_backfill_yesterday: 清点缺口 + mock sync_all 补跑
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.gap_record import GapRecord
from app.services import gap_queue_service as gq

TZ_CN = timezone(timedelta(hours=8))


@pytest.fixture(autouse=True)
def _no_commit(db, monkeypatch):
    """让 service 内 commit 变为 flush(测试事务回滚隔离)。"""
    monkeypatch.setattr(db, "commit", db.flush)


# ──────────── 1. 记账: record_from_sync_results ────────────


def _res(data_type, success, error=None, partial=None, start=None, full_day=False):
    return {
        "data_type": data_type,
        "success": success,
        "error": error,
        "partial_failures": partial,
        "start_time": (start or datetime(2026, 9, 3, 10, 0, tzinfo=TZ_CN)).isoformat(),
        "end_time": datetime(2026, 9, 3, 11, 0, tzinfo=TZ_CN).isoformat(),
        "full_day": full_day,
    }


def test_record_failure_open(db):
    """失败 → open + 错误记录。"""
    gq.record_from_sync_results(db, [_res("cs_record", False, error="HTTP 500")])
    gap = db.query(GapRecord).filter_by(biz_date="2026-09-03", data_type="cs_record").one()
    assert gap.status == "open"
    assert "HTTP 500" in (gap.error or "")


def test_record_partial_partial_status(db):
    """部分成功(有失败片) → partial + 失败片范围记录。"""
    gq.record_from_sync_results(
        db,
        [_res("cs_record", True, partial=[{"start": "2026-09-03T00:00:00", "end": "2026-09-03T10:00:00"}])],
    )
    gap = db.query(GapRecord).filter_by(biz_date="2026-09-03", data_type="cs_record").one()
    assert gap.status == "partial"
    assert "2026-09-03T00:00:00" in (gap.remark or "")


def test_record_full_day_success_filled(db):
    """全天覆盖成功 → filled。"""
    gq.record_from_sync_results(db, [_res("cs_record", True, full_day=True)])
    gap = db.query(GapRecord).filter_by(biz_date="2026-09-03", data_type="cs_record").one()
    assert gap.status == "filled"


def test_record_daytime_success_no_filled(db):
    """白天增量成功(无 full_day 标记) → 不产生 filled 记录。"""
    n = gq.record_from_sync_results(db, [_res("cs_record", True)])
    # 成功且非全天覆盖 → 无缺口记录被创建
    cnt = db.query(GapRecord).filter_by(biz_date="2026-09-03", data_type="cs_record").count()
    assert cnt == 0
    assert n == 0


def test_record_full_range_marks_old_gaps_filled(db):
    """近 N 天全量窗口成功 → 窗口内该源历史 open/partial 一并标 filled。"""
    # 先造 3 天前的缺口 + 2 天前的缺口
    d3 = (datetime(2026, 9, 1, 10, 0, tzinfo=TZ_CN)).isoformat()
    d2 = (datetime(2026, 9, 2, 10, 0, tzinfo=TZ_CN)).isoformat()
    gq.record_from_sync_results(db, [{"data_type": "cs_record", "success": False, "error": "x",
                                      "start_time": d3, "end_time": d3}])
    gq.record_from_sync_results(db, [{"data_type": "cs_record", "success": False, "error": "x",
                                      "start_time": d2, "end_time": d2}])
    assert db.query(GapRecord).filter_by(data_type="cs_record", status="open").count() == 2

    # 8:00 近 7 天全量成功覆盖该源 → 传 full_range=[09-01, 09-08] → 旧缺口全 filled
    res = [{"data_type": "cs_record", "success": True, "start_time": d3, "end_time": d3}]
    gq.record_from_sync_results(db, res, full_range=("2026-09-01", "2026-09-08"))
    filled = db.query(GapRecord).filter_by(data_type="cs_record", status="filled").all()
    assert len(filled) == 2
    assert db.query(GapRecord).filter_by(data_type="cs_record", status="open").count() == 0


def test_record_full_range_only_matching_type(db):
    """full_range 只清匹配 data_type 的缺口, 其它源不受影响。"""
    d3 = (datetime(2026, 9, 1, 10, 0, tzinfo=TZ_CN)).isoformat()
    gq.record_from_sync_results(db, [{"data_type": "cs_record", "success": False, "error": "x",
                                      "start_time": d3, "end_time": d3}])
    gq.record_from_sync_results(db, [{"data_type": "refund", "success": False, "error": "x",
                                      "start_time": d3, "end_time": d3}])
    res = [{"data_type": "cs_record", "success": True, "start_time": d3, "end_time": d3}]
    gq.record_from_sync_results(db, res, full_range=("2026-09-01", "2026-09-08"))
    # cs_record 已 filled, refund 仍 open
    assert db.query(GapRecord).filter_by(data_type="cs_record", status="filled").count() == 1
    assert db.query(GapRecord).filter_by(data_type="refund", status="open").count() == 1


def test_record_upsert_idempotent(db):
    """同一源同日多次失败 → 只保留一行(upsert 幂等)。"""
    gq.record_from_sync_results(db, [_res("cs_record", False, error="e1")])
    gq.record_from_sync_results(db, [_res("cs_record", False, error="e2")])
    rows = db.query(GapRecord).filter_by(biz_date="2026-09-03", data_type="cs_record").all()
    assert len(rows) == 1
    assert "e2" in (rows[0].error or "")  # 保留最新


def test_ticket_finish_maps_to_ticket(db):
    """ticket_finish 双维度 → 记到 ticket。"""
    gq.record_from_sync_results(db, [_res("ticket_finish", False, error="boom")])
    gap = db.query(GapRecord).filter_by(biz_date="2026-09-03", data_type="ticket").one()
    assert gap.status == "open"


# ──────────── 2. 业务日期换算 ────────────


def test_biz_date_of_naive_utc():
    """naive datetime 按 UTC 转北京。"""
    # 2026-09-03 20:00 UTC = 09-04 04:00 北京
    dt = datetime(2026, 9, 3, 20, 0, 0)  # naive 视为 UTC
    assert gq._biz_date_of(dt) == "2026-09-04"


def test_biz_date_of_aware_cn():
    dt = datetime(2026, 9, 3, 10, 0, 0, tzinfo=TZ_CN)
    assert gq._biz_date_of(dt) == "2026-09-03"


# ──────────── 3. 查询/标记 ────────────


def test_list_and_ignore(db):
    gq.record_from_sync_results(db, [_res("cs_record", False, error="x")])
    gaps = gq.list_gaps(db, biz_date="2026-09-03")
    assert len(gaps) == 1
    assert gaps[0]["status"] == "open"
    # 忽略
    ok = gq.mark_ignored(db, gaps[0]["id"])
    assert ok is True
    # ignored 不在默认列表(open/partial)
    assert gq.list_gaps(db, biz_date="2026-09-03") == []


def test_mark_filled(db):
    gq.record_from_sync_results(db, [_res("cs_record", False, error="x")])
    ok = gq.mark_filled(db, "2026-09-03", "cs_record", source="manual")
    assert ok is True
    gap = db.query(GapRecord).filter_by(biz_date="2026-09-03", data_type="cs_record").one()
    assert gap.status == "filled"
    # 无记录时返回 False
    assert gq.mark_filled(db, "2026-09-04", "cs_record") is False


# ──────────── 3.5 R-P1-17: record_abandoned_slots ────────────


def test_record_abandoned_slots_creates_open(db):
    """放弃档(未尝试) → 逐行新建 open, 保证次日清点会补。"""
    rows = [("2026-09-03", "wallet"), ("2026-09-03", "refund_record_report")]
    n = gq.record_abandoned_slots(db, rows, reason="低频档15:00持续忙碌当日放弃")
    assert n == 2
    g1 = db.query(GapRecord).filter_by(biz_date="2026-09-03", data_type="wallet").one()
    assert g1.status == "open"
    assert "放弃" in (g1.error or "")


def test_record_abandoned_slots_no_downgrade(db):
    """已 filled/ignored 的记录不被放弃档重新打开。"""
    # 先有一条 filled + 一条 ignored
    gq.record_from_sync_results(db, [_res("cs_record", True, full_day=True)])
    gq.record_from_sync_results(db, [_res("wallet", False, error="x")])
    wallet_gap = db.query(GapRecord).filter_by(biz_date="2026-09-03", data_type="wallet").one()
    gq.mark_ignored(db, wallet_gap.id)

    rows = [("2026-09-03", "cs_record"), ("2026-09-03", "wallet"), ("2026-09-03", "ticket")]
    n = gq.record_abandoned_slots(db, rows, reason="busy")
    # 只新建 ticket 一行; filled/ignored 不动
    assert n == 1
    assert db.query(GapRecord).filter_by(biz_date="2026-09-03", data_type="cs_record").one().status == "filled"
    assert db.query(GapRecord).filter_by(biz_date="2026-09-03", data_type="wallet").one().status == "ignored"
    assert db.query(GapRecord).filter_by(biz_date="2026-09-03", data_type="ticket").one().status == "open"


# ──────────── 3.6 R-P1-17: mark_filled_for_dates ────────────


def test_mark_filled_for_dates(db):
    """多日缺口批量回填; 无记录日期不新增; 只统计实际转换数。"""
    gq.record_from_sync_results(db, [_res("cs_record", False, error="x")])  # 09-03 open
    n = gq.mark_filled_for_dates(
        db, "cs_record", ["2026-09-03", "2026-09-04"], source="upload:99",
    )
    # 09-03 转换计 1; 09-04 无缺口记录, 不新增
    assert n == 1
    gap = db.query(GapRecord).filter_by(biz_date="2026-09-03", data_type="cs_record").one()
    assert gap.status == "filled"
    assert "upload:99" in (gap.remark or "")
    assert db.query(GapRecord).filter_by(biz_date="2026-09-04").count() == 0
    # 已 filled 再调 → 转换数为 0
    assert gq.mark_filled_for_dates(db, "cs_record", ["2026-09-03"], source="upload:100") == 0


# ──────────── 3.7 R-P1-17: 手动导入回写(_close_gaps_from_import) ────────────


def test_close_gaps_from_import(db):
    """导入数据按 DataSource.time_field + 列映射提取业务日并回填。

    关键: 文件内 naive 时间按北京墙钟取日 —— 凌晨 03:00 不得因 UTC 换算落到前一天。
    """
    import pandas as pd

    from app.api.v1.uploads import _close_gaps_from_import
    from app.models.data_source import DataSource

    db.add(DataSource(
        code="cs_record", table_name="cs_records", name_cn="测试·接待",
        time_field="created_at_raw",
    ))
    # 09-03 的 open 缺口(凌晨行) + 09-02 的 open 缺口
    gq.record_from_sync_results(db, [_res("cs_record", False, error="x")])
    d2 = datetime(2026, 9, 2, 10, 0, tzinfo=TZ_CN).isoformat()
    gq.record_from_sync_results(db, [{"data_type": "cs_record", "success": False, "error": "x",
                                      "start_time": d2, "end_time": d2}])

    df = pd.DataFrame({"创建时间": ["2026-09-03 03:00:00", "2026-09-02 18:00:00", "bad"]})
    _close_gaps_from_import(db, "cs_record", df, {"created_at_raw": "创建时间"}, upload_id=7)

    g3 = db.query(GapRecord).filter_by(biz_date="2026-09-03", data_type="cs_record").one()
    g2 = db.query(GapRecord).filter_by(biz_date="2026-09-02", data_type="cs_record").one()
    assert g3.status == "filled"  # 凌晨 3 点仍归属 09-03
    assert g2.status == "filled"
    assert "upload:7" in (g3.remark or "")


# ──────────── 4. settle_and_backfill_yesterday ────────────


@pytest.mark.asyncio
async def test_settle_no_gap(db):
    """无缺口 → total=0。"""
    r = await gq.settle_and_backfill_yesterday(db, yesterday="2026-09-03")
    assert r["total"] == 0


@pytest.mark.asyncio
async def test_settle_backfill_success(db, monkeypatch):
    """有缺口 → mock sync_all 成功 → filled + backfilled。"""
    gq.record_from_sync_results(db, [_res("cs_record", False, error="x")])

    async def fake_sync_all(**kwargs):
        return [{"data_type": "cs_record", "success": True, "inserted_rows": 5}]

    monkeypatch.setattr("app.services.bms_sync.sync_all", fake_sync_all)
    # 测试库无 sync_source_configs 行(空表 = 无源纳入定时), mock 已知 BMS 源集合
    monkeypatch.setattr(
        "app.services.sync_source_config_service.list_scheduled_bms_sources",
        lambda _db: {"all": ["cs_record"], "high": ["cs_record"], "low": []},
    )
    r = await gq.settle_and_backfill_yesterday(db, yesterday="2026-09-03")
    assert r["total"] == 1
    assert r["backfilled"] == ["cs_record"]
    gap = db.query(GapRecord).filter_by(biz_date="2026-09-03", data_type="cs_record").one()
    assert gap.status == "filled"
    assert gap.try_count == 1


@pytest.mark.asyncio
async def test_settle_backfill_fail(db, monkeypatch):
    """补跑失败 → 保留 open + try_count+1 + still_failed。"""
    gq.record_from_sync_results(db, [_res("cs_record", False, error="x")])

    async def fake_sync_all(**kwargs):
        return [{"data_type": "cs_record", "success": False, "error": "still down"}]

    monkeypatch.setattr("app.services.bms_sync.sync_all", fake_sync_all)
    # 同 success 用例: mock 已知 BMS 源集合(测试库空配置)
    monkeypatch.setattr(
        "app.services.sync_source_config_service.list_scheduled_bms_sources",
        lambda _db: {"all": ["cs_record"], "high": ["cs_record"], "low": []},
    )
    r = await gq.settle_and_backfill_yesterday(db, yesterday="2026-09-03")
    assert r["total"] == 1
    assert r["backfilled"] == []
    assert r["still_failed"] == ["cs_record"]
    gap = db.query(GapRecord).filter_by(biz_date="2026-09-03", data_type="cs_record").one()
    assert gap.status == "open"
    assert gap.try_count == 1
