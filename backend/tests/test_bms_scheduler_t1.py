# -*- coding: utf-8 -*-
"""bms_scheduler T+1(退款记录报表)归低频档测试(2026-09-07 改写)

背景演进:
- 2026-09-05: 退款记录报表(E 路)当天无数据, 只能次日抓前一天 → 新增 T_PLUS_ONE_BMS_TYPES
  使其只在 5:00/8:00 兜底模式纳入(不参与白天每小时, 防当天查空+卡导出)。
- 2026-09-07 A-20260907-001 LOCKED / #31: T_PLUS_ONE_BMS_TYPES 特殊逻辑**退役**。
  改为"读 DB default_sync=true 分档": refund_record_report 若 DB true → 自动归入
  **低频档(low)** —— 低频档每天 6:00 跑"前一天整天" = 天然满足 T+1"次日抓前一天";
  若 DB false → 不进任何定时(退化为手动)。

本测试锁定:
1. refund_record_report 不是种子核心高频源(DEFAULT_SYNC_TYPES), 不会白天每小时跑
2. 读库 helper list_scheduled_bms_sources 能正确把 refund_record_report 分到 low(而非 high)
3. 高频 4 源(refund/ticket/wallet/cs_record)在 helper 中归 high
"""

from __future__ import annotations

from sqlalchemy import select

from app.services.bms_sync import DEFAULT_SYNC_TYPES


def test_refund_record_report_not_in_high_freq_defaults():
    """退款记录报表(E 路)不在种子高频源里 → 不会白天每小时跑(保持 T+1 语义根基)。"""
    assert "refund_record_report" not in DEFAULT_SYNC_TYPES
    # 高频 4 源仍在
    assert "refund" in DEFAULT_SYNC_TYPES
    assert "ticket" in DEFAULT_SYNC_TYPES
    assert "wallet" in DEFAULT_SYNC_TYPES
    assert "cs_record" in DEFAULT_SYNC_TYPES


def test_scheduler_helper_puts_refund_record_report_in_low(db):
    """读库 helper: refund_record_report(DB true) 应归 low 而非 high。"""
    from app.models.sync_source_config import SyncSourceConfig
    from app.services.sync_source_config_service import list_scheduled_bms_sources

    existing = db.scalar(
        select(SyncSourceConfig).where(SyncSourceConfig.data_type == "refund_record_report")
    )
    if existing is None:
        existing = SyncSourceConfig(
            data_type="refund_record_report", name="退款记录报表",
            base_url="https://wifi.cloud-cat.cn/bms",
            page_path="/index.html#/settle/refund-record-report",
            default_sync=True, enabled=True,
        )
        db.add(existing)
    else:
        # 已存在(测试库 seed 可能 default_sync=false) → 强制 true 以测"纳入定时"语义
        existing.default_sync = True
        existing.enabled = True
    db.flush()

    sched = list_scheduled_bms_sources(db)
    # refund_record_report 是 DB true 但非种子高频 → 应在 low, 不在 high
    assert "refund_record_report" in sched["low"], f"low={sched['low']}"
    assert "refund_record_report" not in sched["high"], f"high={sched['high']}"
    # 高频核心源应在 high
    for dt in ("refund", "ticket", "wallet", "cs_record"):
        exists = db.scalar(
            select(SyncSourceConfig).where(SyncSourceConfig.data_type == dt)
        )
        if exists is not None:
            assert dt in sched["high"], f"{dt} 应在 high: high={sched['high']}"


def test_high_freq_from_seed_defaults_alignment():
    """种子高频 DEFAULT_SYNC_TYPES 即 helper 的 high 档语义来源(交集判定)。"""
    for dt in ("refund", "ticket", "wallet", "cs_record"):
        assert dt in DEFAULT_SYNC_TYPES


def test_helper_filters_disabled_and_scrm(db):
    """helper 只返回 BMS default_sync=true AND enabled=true 的源(排除 SCRM / disabled)。"""
    from app.models.sync_source_config import SyncSourceConfig
    from app.services.sync_source_config_service import list_scheduled_bms_sources

    # 构造: scrm 源 default_sync=true(应被排除) + 一个 disabled 的 BMS 源(应被排除)
    for dt, name, scrm, enabled, dflt in [
        ("scrm_operator_t", "SCRM-测试", True, True, True),
        ("__disabled_t__", "停用测试", False, False, True),
    ]:
        row = db.scalar(select(SyncSourceConfig).where(SyncSourceConfig.data_type == dt))
        if row is None:
            db.add(SyncSourceConfig(
                data_type=dt, name=name,
                base_url="https://x.cloud-cat.cn" if scrm else "https://wifi.cloud-cat.cn/bms",
                page_path="/x", default_sync=dflt, enabled=enabled,
            ))
        else:
            row.default_sync = dflt
            row.enabled = enabled
    db.flush()

    sched = list_scheduled_bms_sources(db)
    assert "scrm_operator_t" not in sched["all"], "SCRM 源不应进 BMS 调度集合"
    assert "__disabled_t__" not in sched["all"], "disabled 源不应进调度集合"


# ── 低频档触发决策(should_run_low_freq) — 锁定忙顺延语义(Review CRITICAL 修复) ──
def test_low_freq_run_when_idle_in_window():
    """6:00~6:59 窗口内空闲 → 应执行(记 done)。"""
    from app.services.bms_scheduler import should_run_low_freq

    assert should_run_low_freq(6, done=False, busy=False) is True    # 6:00 空闲
    assert should_run_low_freq(6, done=False, busy=False) is True    # 6:59 空闲(同一小时)


def test_low_freq_not_run_when_busy_in_window():
    """窗口内 busy → 不执行且不记 done(下一分钟重试, 不静默丢档)。"""
    from app.services.bms_scheduler import should_run_low_freq

    assert should_run_low_freq(6, done=False, busy=True) is False    # 6:00 忙 → 顺延


def test_low_freq_give_up_at_deadline():
    """7:00(deadline)及之后 → 不再执行(上层记 done 放弃 + 告警)。"""
    from app.services.bms_scheduler import should_run_low_freq

    assert should_run_low_freq(7, done=False, busy=False) is False   # 到 7:00 放弃
    assert should_run_low_freq(8, done=False, busy=False) is False


def test_low_freq_skipped_when_done_or_disabled():
    """已 done 或 LOW_FREQ_HOUR<=0(禁用) → 不执行。"""
    from app.services.bms_scheduler import should_run_low_freq

    assert should_run_low_freq(6, done=True, busy=False) is False
    assert should_run_low_freq(5, done=False, busy=False) is False   # 未到低频小时
    assert should_run_low_freq(6, low_freq_hour=0, done=False, busy=False) is False  # 禁用


# ── 共享文档在线采集窗口判定(#35 统一调度; #51 改为**槽位**语义) ──
def test_shared_docs_run_in_slot():
    """命中高频槽位 → 应触发(#51 槽位语义)。

    注: 原断言传 (hour, minute) 的旧接口已改为传槽位标识 —— 槽位由
    `current_slot()` 与 BMS 共用同一判定, 保证两者永不脱钩。
    """
    from app.services.bms_scheduler import should_sync_shared_docs

    assert should_sync_shared_docs("10:00", done=False) is True   # 整点槽
    assert should_sync_shared_docs("10:30", done=False) is True   # 半小时槽(#51 新增)
    assert should_sync_shared_docs("08:00", done=False) is True   # 首槽


def test_shared_docs_not_run_outside_slot():
    """未命中槽位 → 不触发(并入高频节奏, 不独立乱采)。"""
    from app.services.bms_scheduler import should_sync_shared_docs

    assert should_sync_shared_docs(None, done=False) is False     # 未命中任何槽位


def test_shared_docs_slot_once():
    """同槽位只采一次(done) → 不重复触发。"""
    from app.services.bms_scheduler import should_sync_shared_docs

    assert should_sync_shared_docs("10:00", done=True) is False   # 该槽已采过
    assert should_sync_shared_docs("10:30", done=False) is True   # 下一个槽位重新可采


def test_shared_docs_slot_derived_from_current_slot():
    """🔒 一致性: 槽位由 current_slot() 统一产出 —— 与 BMS 高频同源。

    锁住「共享文档与 BMS 同一节奏」这条不变式(#51 架构 §六.6):
    同一时刻算出同一个槽位, 因此不可能出现"BMS 跑了共享文档没跑"。
    """
    from app.services.bms_scheduler import current_slot, should_sync_shared_docs
    from app.services.scheduler_settings_service import compute_slots

    slots = compute_slots(8, 24, 30)
    # 白天任意命中时刻 → 两者同槽
    for hh, mm in ((10, 0), (10, 29), (10, 30), (14, 15), (23, 30)):
        slot = current_slot(hh, mm, 30, slots)
        assert slot is not None, f"{hh}:{mm} 应命中某槽位"
        assert should_sync_shared_docs(slot, done=False) is True
    # 时段外 → 都不触发
    assert current_slot(3, 5, 30, slots) is None
    assert current_slot(6, 5, 30, slots) is None   # 6:00 低频档专属, 不属高频槽位


# ── #38 回归: 派生源 device_tag 不应被纳入周期采集 ──
def test_derived_device_tag_excluded_from_schedule_even_if_db_true(db):
    """device_tag 即使 DB default_sync=true 也不进调度集合(它是 device_tag_binding 的派生态)。

    背景(#38): device_tag 与 device_tag_binding 抓同一份打标明细 CSV, 标签定义由
    binding 导入时按 tag_name 自动补插; 若 device_tag 被纳入低频档, 会按 tag_name
    upsert 同批重复键 → CardinalityViolation 每次必失败并熔断。
    """
    from app.models.sync_source_config import SyncSourceConfig
    from app.services.sync_source_config_service import list_scheduled_bms_sources

    # 强制 device_tag DB true + enabled(模拟 UI 被打开/历史遗留)
    row = db.scalar(select(SyncSourceConfig).where(SyncSourceConfig.data_type == "device_tag"))
    if row is None:
        db.add(SyncSourceConfig(
            data_type="device_tag", name="设备标签",
            base_url="https://wifi.cloud-cat.cn/bms",
            page_path="/index.html#/device/device-tags",
            default_sync=True, enabled=True,
        ))
    else:
        row.default_sync = True
        row.enabled = True
    db.flush()

    sched = list_scheduled_bms_sources(db)
    assert "device_tag" not in sched["all"], f"device_tag 不应进调度集合: all={sched['all']}"
    assert "device_tag" not in sched["low"], f"device_tag 不应进低频: low={sched['low']}"
    # device_tag_binding 可被调度(手动/按需), 不受此剔除影响
    # (它 default_sync 可能 false, 仅验证不误伤: 若 true 应在集合中)
    binding = db.scalar(select(SyncSourceConfig).where(SyncSourceConfig.data_type == "device_tag_binding"))
    if binding is not None and binding.default_sync and binding.enabled:
        assert "device_tag_binding" in sched["all"], "不应误伤 device_tag_binding"
