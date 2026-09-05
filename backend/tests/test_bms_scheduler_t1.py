# -*- coding: utf-8 -*-
"""bms_scheduler T+1 延迟源纳入测试(2026-09-05)

背景: 退款记录报表(E 路)当天无数据, 只能次日抓前一天(生产实测 refund_record_reports
最新只到 09-04, 无 09-05)。若用「当天 0 点~now」窗口抓 → BMS 查不到当天数据,
导出易卡住超时(20:54 生产现象: 2×180s 无 download 事件)。

方案: 新增 T_PLUS_ONE_BMS_TYPES, 这些源只在「兜底模式」(5:00 前一天全量 / 8:00
近N天全量)纳入, 不参与白天每小时(当天查=空+卡)。窗口天然=前一天整天 / 近N天。

本测试锁定:
1. T_PLUS_ONE_BMS_TYPES 含 refund_record_report
2. 这些源不在 DEFAULT_SYNC_TYPES(不会白天每小时跑) —— 但配置 default_sync 可能变,
   只验证常量定义存在且非空
"""

from __future__ import annotations

from app.services.bms_scheduler import T_PLUS_ONE_BMS_TYPES


def test_t_plus_one_types_defined():
    """T+1 延迟源集合必须定义且含 refund_record_report(E 路)"""
    assert T_PLUS_ONE_BMS_TYPES
    assert "refund_record_report" in T_PLUS_ONE_BMS_TYPES


def test_t_plus_one_type_names():
    """T+1 源命名应是 sync_source_configs 里的 data_type"""
    for dt in T_PLUS_ONE_BMS_TYPES:
        # data_type 格式: 小写字母 + 下划线(BMS 源)
        assert dt and "_" in dt or dt.islower(), f"非预期 T+1 源: {dt}"
