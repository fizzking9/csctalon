"""批次级异常检测 —— 防"整批数据被写坏"的流程层防线 (#42, 2026-09-10)

## 为什么需要它(实证动机)
2026-09-05 两批 workbuddy 推送落库时「投诉日期」被**整批转置**:

    库内年 = 2000 + 真值"日"  ;  库内"日" = 真值年份 − 2001  ;  月份不变

该批共 **2532 行**受影响。而**字段级校验只能检出 1684 行**(库内 <2020 的 1556 行
+ 未来日期的 128 行), 剩余 848 行落在"看似正常"的年份区间(如库内 2020~2022),
**单看某一行完全正常**。

结论: **字段级阈值天然抓不全"整批系统性错误"**。但换个视角 —— 这类错误在
**批次维度**上极其醒目:

    正常批次: external_inserted = 0 ~ 3   且  异常日期占比 ≈ 0%
    污染批次: external_inserted = 2434    且  异常日期占比 = 66.5%

所以需要一个**批次级**检查: 与历史基线对比"体量"与"异常比例"。

## 检测规则
  A-batch-size : 单批 inserted 显著超历史基线(中位数 × 系数), 且超绝对下限
  B-date-ratio : 该批"日期早于业务存在期 / 未来"的行占比超阈值
  C-combo      : A 与 B 同时命中 → 升级为 critical(最可能是整批解析错误)

## 设计原则
  · **只告警不改数据**: 异常检测不下结论、不阻断入库(与 A-20260908-003 的
    "拒收而非静默"分层: 拒收管单行, 本模块管批次)
  · **口径可测**: 纯函数 `detect_*` 不依赖 DB, 便于单测
  · **复用既有通道**: 告警走 `record_system_event`(事务通知页), 与 ingest_health 打通
  · **冷启动宽容**: 无历史基线时不触发 A(避免新环境首个大导入被误报)
"""
from __future__ import annotations

import logging
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_UTC = timezone.utc
_TZ_CN = timedelta(hours=8)

# ── 阈值(可在 CONFIG 覆盖) ──
# 日期异常占比: 正常批次应为 ~0%; 取 20% 留足余量(真实小规模脏数据不至于报警)
DATE_ANOMALY_RATIO_THRESHOLD = 0.20
# 单批体量: 超基线中位数 20 倍, 且至少 100 行才判定"突增"(防小基数放大)
BATCH_SIZE_MULTIPLIER = 20
BATCH_SIZE_MIN_ROWS = 100
# 历史基线取样: 最近 N 个"非本批"记录
BASELINE_SAMPLE = 20

# 业务存在期下限(与 ingest_validator._MIN_VALID_YEAR_BASE 同口径)
MIN_VALID_YEAR = 2020


def _to_cn(dt: Any) -> datetime | None:
    """timestamptz → 北京时间(比较日期必须按北京口径, 否则会差一天)"""
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_UTC)
    return dt.astimezone(timezone.utc) + _TZ_CN


def is_date_anomalous(dt: Any, *, now: datetime | None = None) -> bool:
    """单个日期是否异常: 早于业务存在期, 或为未来日期

    判据与 `ingest_validator._check_time` 同口径(见该模块顶部"结论修正"注释):
    两类都是**解析产物**的特征, 而非真实业务日期。
    """
    cn = _to_cn(dt)
    if cn is None:
        return False   # 缺失不算"异常日期"(另有 R-必填 负责)
    ref = (now or datetime.now(_UTC)) + _TZ_CN
    return cn.year < MIN_VALID_YEAR or cn > ref + timedelta(days=1)


def date_anomaly_stats(rows: list[dict], *, field: str = "complaint_date") -> dict[str, Any]:
    """统计一批行内某日期字段的异常情况(纯函数, 可测)

    返回: {total, with_value, anomalous, ratio}
      - total       : 行数
      - with_value  : 该字段非空的行数(分母用这个, 否则"整列缺失"会被稀释成 0%)
      - anomalous   : 异常行数
      - ratio       : anomalous / with_value (with_value=0 时为 0.0)
    """
    total = len(rows)
    with_value = 0
    anomalous = 0
    now = datetime.now(_UTC)
    for r in rows:
        v = r.get(field) if isinstance(r, dict) else None
        cn = _to_cn(v)
        if cn is None:
            continue
        with_value += 1
        if is_date_anomalous(v, now=now):
            anomalous += 1
    ratio = (anomalous / with_value) if with_value else 0.0
    return {"total": total, "with_value": with_value, "anomalous": anomalous, "ratio": ratio}


def batch_size_stats(historical: list[int]) -> dict[str, Any]:
    """历史批次的体量基线(纯函数, 可测)

    用**中位数**(而非均值): 体量分布高度右偏(多数 0~3, 偶发数千),
    均值会被离群值拉高而失去判别力。
    """
    vals = [int(x) for x in historical if x is not None]
    if not vals:
        return {"n": 0, "median": None}
    return {"n": len(vals), "median": float(statistics.median(vals))}


def detect_anomalies(
    *,
    batch: dict[str, Any],
    baseline_sizes: list[int],
) -> list[dict[str, Any]]:
    """综合判定(纯函数, 可测) → 异常项列表

    参数:
      batch: {
        "inserted": int,          # 本批新增行数
        "date_stats": {...},      # date_anomaly_stats() 结果
        "label": str,             # 批次标签(如 data_type / 来源文件)
      }
      baseline_sizes: 历史各批 inserted(不含本批)

    返回: [{"rule", "severity", "message", "detail"}]
      rule: A-batch-size / B-date-ratio / C-combo
    """
    out: list[dict[str, Any]] = []
    inserted = int(batch.get("inserted") or 0)
    ds = batch.get("date_stats") or {}
    ratio = float(ds.get("ratio") or 0.0)
    anomalous = int(ds.get("anomalous") or 0)
    with_value = int(ds.get("with_value") or 0)
    label = str(batch.get("label") or "批次")

    # ── B: 日期异常占比 ──
    ratio_hit = with_value > 0 and ratio >= DATE_ANOMALY_RATIO_THRESHOLD
    if ratio_hit:
        out.append({
            "rule": "B-date-ratio",
            "severity": "warning",
            "message": (
                f"{label} 本批日期异常占比 {ratio:.1%} "
                f"({anomalous}/{with_value} 行早于 {MIN_VALID_YEAR} 年或为未来), "
                f"疑似整批解析错误"
            ),
            "detail": {"ratio": ratio, "anomalous": anomalous, "with_value": with_value},
        })

    # ── A: 体量突增 ──
    bs = batch_size_stats(baseline_sizes)
    median = bs.get("median")
    size_hit = False
    if median is not None:
        # 基线为 0 时退化为绝对下限判定(否则 "×20" 恒为 0, 永不触发)
        if median <= 0:
            size_hit = inserted >= BATCH_SIZE_MIN_ROWS
        else:
            size_hit = (inserted >= BATCH_SIZE_MIN_ROWS
                        and inserted > median * BATCH_SIZE_MULTIPLIER)
    if size_hit:
        out.append({
            "rule": "A-batch-size",
            "severity": "warning",
            "message": (
                f"{label} 本批新增 {inserted} 行, 远超历史基线"
                f"(中位数 {median:.1f}, 阈值 ×{BATCH_SIZE_MULTIPLIER} / 至少 {BATCH_SIZE_MIN_ROWS} 行), "
                f"疑似全量重导或批量错误"
            ),
            "detail": {"inserted": inserted, "baseline_median": median,
                       "baseline_n": bs.get("n")},
        })

    # ── C: 组合(最危险: 体量大 + 日期普遍异常 = 整批被写坏) ──
    if ratio_hit and size_hit:
        out.append({
            "rule": "C-combo",
            "severity": "critical",
            "message": (
                f"{label} 危险组合: 本批新增 {inserted} 行且日期异常占比 {ratio:.1%} —— "
                f"疑似整批数据被写坏(参考 #42 的 2026-09-05 日期转置事件)"
            ),
            "detail": {"inserted": inserted, "ratio": ratio,
                       "anomalous": anomalous, "baseline_median": median},
        })
    return out


# ═══════════════════════════════════════════════════════════════════════
# DB 集成
# ═══════════════════════════════════════════════════════════════════════
def _historical_inserted(db: Session, table: str, col: str, limit: int) -> list[int]:
    """取最近 N 批的历史新增量(用于体量基线)"""
    try:
        rows = db.execute(
            select(getattr(table, col)).order_by(table.id.desc()).limit(limit)
        ).all()
        return [int(r[0] or 0) for r in rows]
    except Exception as e:  # noqa: BLE001 基线取不到不该影响主流程
        logger.warning(f"[ingest_anomaly] 取历史基线失败(跳过体量检查): {e}")
        return []


def check_complaint_batch(
    db: Session,
    *,
    rows: list[dict],
    inserted: int,
    label: str = "315投诉",
) -> list[dict[str, Any]]:
    """投诉批次的异常检测(接入 complaint_sync 调用)

    rows: 本批**清洗后**的待入库行(含 complaint_date)
    返回异常项列表; 同时写日志 + 事务通知(危险项)。
    """
    from app.models.complaint_sync_log import ComplaintSyncLog

    stats = date_anomaly_stats(rows, field="complaint_date")
    hist = _historical_inserted(db, ComplaintSyncLog, "external_inserted", BASELINE_SAMPLE)
    anomalies = detect_anomalies(
        batch={"inserted": inserted, "date_stats": stats, "label": label},
        baseline_sizes=hist,
    )
    _emit(db, anomalies, label=label, stats=stats, inserted=inserted)
    return anomalies


def _emit(
    db: Session,
    anomalies: list[dict[str, Any]],
    *,
    label: str,
    stats: dict[str, Any],
    inserted: int,
) -> None:
    """输出告警: 日志 + 事务通知(不阻断主流程)"""
    for a in anomalies:
        msg = a["message"]
        if a["severity"] == "critical":
            logger.error(f"[ingest_anomaly] {msg}")
        else:
            logger.warning(f"[ingest_anomaly] {msg}")
    # 只在有 critical 时落"事务通知"(warning 级别避免刷屏)
    crit = [a for a in anomalies if a["severity"] == "critical"]
    if not crit:
        return
    try:
        from app.services.system_event_service import record_system_event

        record_system_event(
            db,
            category="alert",
            action="ingest_batch_anomaly",
            title=f"批次异常告警: {label}",
            summary=crit[0]["message"][:512],
            status="failed",
            detail=(
                f"inserted={inserted} 日期异常={stats.get('anomalous')}/"
                f"{stats.get('with_value')} ({stats.get('ratio', 0):.1%}); "
                f"规则={[a['rule'] for a in anomalies]}"
            ),
            commit=False,   # 由调用方事务统一提交, 避免与同步主事务交错
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ingest_anomaly] 写事务通知失败(忽略): {e}")
