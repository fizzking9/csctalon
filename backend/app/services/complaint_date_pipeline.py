#!/usr/bin/env python3
"""投诉日期恢复服务 - 入库管线集成（#195）。

在 complaint_sync.py 清洗后/validate 前调用，
对 complaint_date 为空的行做确定性恢复。
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime

from app.services.complaint_date_recovery import (
    extract_leading_handle_date,
    recover_from_fields,
)


def recover_complaint_date_for_row(
    raw: dict,
    source: str,
    fallback_year: int | None = None,
) -> str | None:
    """为单行数据恢复投诉日期。

    Args:
        raw: 原始行数据（dict）
        source: 数据来源（"card" 或 "external"）
        fallback_year: 批次内推断的年份

    Returns:
        恢复出的日期字符串（YYYY-MM-DD），或 None
    """
    # 先检查日期列是否已有值
    existing = _clean_date(raw.get("投诉日期"))
    if existing:
        return existing

    # 提取文本字段
    fields = _extract_fields(raw, source)

    # 确定性恢复
    result = recover_from_fields(fields, fallback_year=fallback_year)
    if result:
        return result

    # 特殊处理：处理结果字段的句首日期
    if source == "card":
        handle_result = raw.get("处理结果")
        if handle_result:
            result = extract_leading_handle_date(handle_result, fallback_year=fallback_year)
            if result:
                return result

    return None


def _clean_date(val: str | None) -> str | None:
    """清洗日期列。"""
    if not val:
        return None
    val = str(val).strip()
    if val in ("/", "0", "None", "nan"):
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})", val)
    if m:
        return m.group(1)
    m = re.match(r"(\d{4}/\d{2}/\d{2})", val)
    if m:
        return datetime.strptime(m.group(1), "%Y/%m/%d").strftime("%Y-%m-%d")
    return None


def _extract_fields(raw: dict, source: str) -> list[str | None]:
    """按来源提取文本字段。"""
    if source == "card":
        return [
            raw.get("投诉问题"),
            raw.get("结果"),
            raw.get("解决方案"),
            raw.get("处理结果"),
            raw.get("投诉群"),
        ]
    return [
        raw.get("投诉问题"),
        raw.get("处理明细"),
    ]


def batch_recover_with_year_propagation(
    rows: list[dict],
    source: str,
    window: int = 40,
    rounds: int = 2,
) -> dict[int, str | None]:
    """批次恢复，支持年份传播。

    Args:
        rows: 行数据列表，每行必须包含 "_row_index" 键（原始行号）
        source: 数据来源
        window: 年份传播窗口（±N 行）
        rounds: 传播轮数

    Returns:
        {原始行号: 恢复日期或 None}
    """
    results: dict[int, str | None] = {}

    # 第一轮：无 fallback
    for r in rows:
        idx = r["_row_index"]
        results[idx] = recover_complaint_date_for_row(r, source)

    # 年份传播
    for _ in range(rounds):
        changed = False
        for r in rows:
            idx = r["_row_index"]
            if results[idx]:
                continue

            # 收集窗口内已确定年份
            years = []
            for q in rows:
                q_idx = q["_row_index"]
                if abs(q_idx - idx) <= window and results[q_idx]:
                    years.append(results[q_idx][:4])

            if not years:
                continue

            fb = Counter(years).most_common(1)[0][0]
            v = recover_complaint_date_for_row(r, source, fallback_year=int(fb))
            if v:
                results[idx] = v
                changed = True

        if not changed:
            break

    return results


# 版本化信息
SERVICE_VERSION = {
    "code": "complaint_date_recovery",
    "version": "1.0.0",
    "change_reason": "初始版本：从 meta_loop 沉淀，支持批次年份传播",
    "created_by": "dev:coder-30b",
}
