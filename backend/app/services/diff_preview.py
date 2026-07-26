"""导入 diff 引擎 - 按主键策略计算 existing/new/abnormal 分类

复用 upsert 的字段转换逻辑（apply_mapping + 类型推断），保证 diff 与实际 upsert 口径一致。

策略：
  - pk_strategy=none → 全标 new（无主键无法判断已存在）
  - pk_strategy=primary_key → 批量 SELECT 现有行，逐字段比较
    - 主键不存在 → new
    - 主键已存在且字段全一致 → existing
    - 主键已存在但有字段不同 → abnormal（显示 old vs new）
    - 主键为空 → abnormal（无法识别）
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pandas as pd
from sqlalchemy import DateTime, Float, Integer, Numeric, inspect, select
from sqlalchemy.orm import Session

from app.models.data_source import DataSource
from app.services.data_source_registry import DATA_SOURCE_SEEDS
from app.services.upsert import (
    _clean_value,
    _to_datetime,
    _to_float,
    _to_int,
    _to_str,
    apply_mapping,
)

# code → 模型类
_MODEL_BY_CODE: dict[str, type] = {s["code"]: s["model"] for s in DATA_SOURCE_SEEDS}

# 比较时排除的系统字段（TimestampMixin 系统时间 + raw_extra 冗余 + 自增 id）
_EXCLUDED_FIELDS = {"id", "created_at", "updated_at", "raw_extra"}

# 硬限制（plan 决策 #7）
MAX_DIFF_ROWS = 50_000
MAX_NEW_DETAIL = 200
MAX_ABNORMAL_DETAIL = 500
MAX_EXISTING_SAMPLE = 50
BATCH_SIZE = 1000  # 批量 SELECT 的批次大小


# ---------- 字段类型转换 ----------


def _column_converters(model: type) -> dict[str, Any]:
    """根据模型列类型返回 字段名 → 转换函数 的映射

    与 upsert 中各 upsert_* 函数的字段转换口径一致：
    DateTime→_to_datetime, Numeric/Float→_to_float, Integer→_to_int, 其余→_to_str
    """
    converters: dict[str, Any] = {}
    mapper = inspect(model)
    for col in mapper.columns:
        if col.name in _EXCLUDED_FIELDS:
            continue
        if isinstance(col.type, DateTime):
            converters[col.name] = _to_datetime
        elif isinstance(col.type, (Numeric, Float)):
            converters[col.name] = _to_float
        elif isinstance(col.type, Integer):
            converters[col.name] = _to_int
        else:
            converters[col.name] = _to_str
    return converters


def _build_intended_row(data: dict[str, Any], converters: dict[str, Any]) -> dict[str, Any]:
    """将 apply_mapping 后的 data 按列类型转换，生成将要写入 DB 的行

    与 upsert 的行构建逻辑一致：apply_mapping 已做 _clean_value（去空白/NaN/null），
    这里再按列类型做 _to_float/_to_datetime 等转换。
    """
    row: dict[str, Any] = {}
    for field, value in data.items():
        if field in _EXCLUDED_FIELDS:
            continue
        conv = converters.get(field)
        row[field] = conv(value) if conv else _clean_value(value)
    return row


# ---------- 值比较 ----------


def _normalize_for_compare(val: Any) -> Any:
    """归一化值用于比较：Decimal→float, datetime 统一 naive UTC, NaN/NaT→None, 空串→None, str→strip"""
    if val is None:
        return None
    if isinstance(val, Decimal):
        return float(val)
    # pd.Timestamp 是 datetime 子类，统一按 datetime 处理
    if isinstance(val, datetime):
        # DB 的 timestamptz 是 aware(UTC)，CSV 解析的 datetime 是 naive
        # 统一转 naive UTC 使两侧可比
        if val.tzinfo is not None:
            val = val.astimezone(timezone.utc).replace(tzinfo=None)
        return val
    try:
        if pd.isna(val):  # 捕获 numpy nan / pandas NaT
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(val, str):
        s = val.strip()
        return None if s == "" else s
    return val


def _values_equal(a: Any, b: Any) -> bool:
    """比较两个值是否一致（None vs "" 视为一致）"""
    return _normalize_for_compare(a) == _normalize_for_compare(b)


# ---------- JSON 安全转换 ----------


def _json_safe_value(val: Any) -> Any:
    """转 JSON 安全类型（Decimal→float, datetime→isoformat, NaN→None）"""
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(val, Decimal):
        return float(val)
    if hasattr(val, "isoformat"):
        return val.isoformat()
    return val


def _json_safe_row(row: dict[str, Any], idx: int, pk: str | None = None) -> dict[str, Any]:
    """转整行为 JSON 安全，附 row_idx 和 pk"""
    return {
        "row_idx": idx,
        "pk": pk,
        "data": {k: _json_safe_value(v) for k, v in row.items()},
    }


# ---------- 主入口 ----------


def _clean_df(df: pd.DataFrame) -> pd.DataFrame:
    """与 run_upsert 一致的清洗：NaN→None, 文本占位符→None"""
    df = df.where(pd.notna(df), None)
    df = df.map(
        lambda v: None
        if (isinstance(v, str) and v.strip().lower() in ("nan", "null", "none", ""))
        else v
    )
    return df


def compute_diff(
    db: Session,
    df: pd.DataFrame,
    data_type: str,
    mapping: dict[str, str],
    ds: DataSource | None,
) -> dict[str, Any]:
    """计算导入 diff

    Args:
        db: 数据库会话
        df: 已加载的 DataFrame（load_dataframe 已清洗，这里再补 run_upsert 同款清洗）
        data_type: 数据源 code（refund/wallet/ticket/merchant_refund）
        mapping: 目标字段 → 源列名
        ds: DataSource 元数据（含 pk_field/pk_strategy）；None 表示未注册

    Returns:
        {
            strategy: "primary_key" | "none" | "unknown",
            summary: {total, existing, new, abnormal},
            truncated: bool,
            existing_sample: [{pk, row_idx}],     # ≤50
            new_rows: [{row_idx, pk, data}],       # ≤200
            abnormal_rows: [{row_idx, pk, diffs}], # ≤500
            note: str | None,
        }
    """
    total = len(df)
    truncated = total > MAX_DIFF_ROWS
    if truncated:
        df = df.head(MAX_DIFF_ROWS)
    overflow = total - len(df) if truncated else 0

    # 未注册数据源 → 无法 diff
    model = _MODEL_BY_CODE.get(data_type)
    if model is None or ds is None:
        return {
            "strategy": "unknown",
            "summary": {"total": total, "existing": 0, "new": 0, "abnormal": 0},
            "truncated": truncated,
            "existing_sample": [],
            "new_rows": [],
            "abnormal_rows": [],
            "note": f"数据类型 '{data_type}' 未注册数据源，无法计算 diff，可直接导入",
        }

    df = _clean_df(df)
    converters = _column_converters(model)
    pk_field = ds.pk_field
    strategy = ds.pk_strategy

    # ---------- 无主键 → 全部标 new ----------
    if strategy == "none" or not pk_field:
        new_rows_detail: list[dict] = []
        new_count = 0
        for idx, row in enumerate(df.to_dict("records")):
            new_count += 1
            if len(new_rows_detail) < MAX_NEW_DETAIL:
                data = apply_mapping(row, mapping)
                intended = _build_intended_row(data, converters)
                new_rows_detail.append(_json_safe_row(intended, idx))
        new_count += overflow  # 截断部分统一标 new
        return {
            "strategy": "none",
            "summary": {"total": total, "existing": 0, "new": new_count, "abnormal": 0},
            "truncated": truncated,
            "existing_sample": [],
            "new_rows": new_rows_detail,
            "abnormal_rows": [],
            "note": "无主键，全部视为新增（无法判断已存在）",
        }

    # ---------- primary_key 策略 ----------
    pk_converter = converters.get(pk_field, _to_str)

    # 1. 构建所有 intended rows
    all_intended: list[tuple[int, str | None, dict[str, Any]]] = []  # (idx, pk_str, intended_row)
    for idx, row in enumerate(df.to_dict("records")):
        data = apply_mapping(row, mapping)
        intended = _build_intended_row(data, converters)
        pk_raw = data.get(pk_field)
        pk_val = pk_converter(pk_raw) if pk_raw is not None else None
        pk_str = str(pk_val).strip() if pk_val is not None else None
        all_intended.append((idx, pk_str, intended))

    # 2. 批量查现有行（分批避免 IN 列表过长）
    valid_pks = [pk for _, pk, _ in all_intended if pk]
    existing_db_rows: dict[str, Any] = {}
    for i in range(0, len(valid_pks), BATCH_SIZE):
        chunk = valid_pks[i : i + BATCH_SIZE]
        objs = db.execute(
            select(model).where(getattr(model, pk_field).in_(chunk))
        ).scalars().all()
        for obj in objs:
            existing_db_rows[str(getattr(obj, pk_field))] = obj

    # 3. 逐行分类
    abnormal_rows: list[dict] = []
    abnormal_count = 0
    new_rows_detail = []
    new_count = 0
    existing_sample: list[dict] = []
    existing_count = 0

    for idx, pk_str, intended in all_intended:
        # 主键为空 → abnormal
        if not pk_str:
            abnormal_count += 1
            if len(abnormal_rows) < MAX_ABNORMAL_DETAIL:
                abnormal_rows.append({
                    "row_idx": idx,
                    "pk": None,
                    "diffs": [],
                    "message": f"主键 {pk_field} 为空",
                })
            continue

        existing_obj = existing_db_rows.get(pk_str)
        if existing_obj is None:
            # 主键不存在 → new
            new_count += 1
            if len(new_rows_detail) < MAX_NEW_DETAIL:
                new_rows_detail.append(_json_safe_row(intended, idx, pk_str))
        else:
            # 主键已存在 → 逐字段比较
            diffs: list[dict] = []
            for field, new_val in intended.items():
                if field == pk_field or field in _EXCLUDED_FIELDS:
                    continue
                old_val = getattr(existing_obj, field, None)
                if not _values_equal(old_val, new_val):
                    diffs.append({
                        "field": field,
                        "old": _json_safe_value(old_val),
                        "new": _json_safe_value(new_val),
                    })
            if diffs:
                abnormal_count += 1
                if len(abnormal_rows) < MAX_ABNORMAL_DETAIL:
                    abnormal_rows.append({
                        "row_idx": idx,
                        "pk": pk_str,
                        "diffs": diffs,
                    })
            else:
                existing_count += 1
                if len(existing_sample) < MAX_EXISTING_SAMPLE:
                    existing_sample.append({"pk": pk_str, "row_idx": idx})

    # 截断部分统一标 new
    new_count += overflow

    return {
        "strategy": "primary_key",
        "summary": {
            "total": total,
            "existing": existing_count,
            "new": new_count,
            "abnormal": abnormal_count,
        },
        "truncated": truncated,
        "existing_sample": existing_sample,
        "new_rows": new_rows_detail,
        "abnormal_rows": abnormal_rows,
        "note": None,
    }
