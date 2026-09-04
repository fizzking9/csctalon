# -*- coding: utf-8 -*-
"""采集缺口队列服务(A-20260904-002 LOCKED / 工单#2 3.3)

一源一日一行的缺口记账(gap_records 表), 提供:
  - record_from_sync_results: 同步结果聚合后记账(失败→open/partial, 全天覆盖→filled)
  - list_gaps: 查看缺口(可按业务日/状态过滤)
  - settle_and_backfill_yesterday: 次日清点昨天缺口, 逐源强制补跑(5:00 调用)

状态语义:
  open    = 该源该业务日存在缺口(尚未补)
  partial = 部分成功(有成功也有失败片), 剩余失败范围待补
  filled  = 缺口已补齐
  ignored = 人工忽略

记账只关心"业务数据日期"(同步时间范围的起始日), 与触发时刻无关。
5:00 补跑(昨天整天)/8:00 全量(近7天)覆盖成功 → 视为 filled;
白天增量(0点~now)失败 → 记 open(当天未结束, 后续小时/次日可补)。
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.gap_record import GapRecord

logger = logging.getLogger(__name__)

TZ_CN = timezone(timedelta(hours=8))


# ───────────────────────── 记账 ─────────────────────────


def _biz_date_of(dt: datetime | str | None) -> str | None:
    """取业务日期(YYYY-MM-DD)。naive 视为 UTC 转东八, aware 直接转; str 先解析。"""
    if dt is None:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc).astimezone(TZ_CN)
    else:
        dt = dt.astimezone(TZ_CN)
    return dt.strftime("%Y-%m-%d")


def _upsert_gap(db, biz_date: str, data_type: str, **updates) -> GapRecord:
    """按 (biz_date, data_type) upsert 缺口记录(保留 first_gap_at, 更新其它)。

    并发安全: 唯一键冲突(理论仅并发插入触发, 单 worker 少见)用 savepoint
    (begin_nested) 局部回滚重查, 避免 rollback 整个外层事务丢其它已 flush 记账。
    """
    row = db.scalar(
        select(GapRecord).where(
            GapRecord.biz_date == biz_date,
            GapRecord.data_type == data_type,
        )
    )
    if row is None:
        row = GapRecord(
            biz_date=biz_date,
            data_type=data_type,
            status="open",
            first_gap_at=datetime.now(TZ_CN),
            try_count=0,
        )
        db.add(row)
    for k, v in updates.items():
        if v is not None:
            setattr(row, k, v)
    try:
        db.flush()
    except IntegrityError:
        # 唯一键并发冲突: 只回滚到 savepoint, 重查已存在行再更新
        try:
            with db.begin_nested():
                row = db.scalar(
                    select(GapRecord).where(
                        GapRecord.biz_date == biz_date,
                        GapRecord.data_type == data_type,
                    )
                )
                if row is not None:
                    for k, v in updates.items():
                        if v is not None:
                            setattr(row, k, v)
                db.flush()
        except Exception:  # noqa: BLE001 savepoint 内再失败则回滚 savepoint 即可
            db.rollback()
            row = db.scalar(
                select(GapRecord).where(
                    GapRecord.biz_date == biz_date,
                    GapRecord.data_type == data_type,
                )
            )
    return row


def record_from_sync_results(
    db,
    results: list[dict],
    trigger_type: str = "",
    full_range: tuple[str, str] | None = None,
) -> int:
    """同步结果聚合后统一记账(在 sync_all 收尾调用, 覆盖所有触发方式)。

    规则:
      1) 失败源(success=False)      → open(记录错误)
      2) 部分成功(partial_failures) → partial(记录失败片范围/摘要)
      3) 全天覆盖成功(由调用方标记 full_day=True / full_range 窗口) → filled
         - full_day: 单日覆盖(5:00 前一天全天) → 该 biz_date 标 filled
         - full_range: 近N天窗口(8:00 全量兜底) → 该源窗口内所有 open/partial 标 filled
           (避免 8:00 全量已成功覆盖后, 缺口队列仍把窗口内的旧缺口当"待补"重复补跑)
    白天增量(trigger_type=scheduled 且非常规补跑)不做 filled(当天未结束)。

    Returns: 本次记账影响的缺口行数。
    """
    changed = 0
    for r in results:
        if not isinstance(r, dict) or not r.get("data_type"):
            continue
        data_type = str(r["data_type"])
        # ticket_finish 按 ticket 记账(双维度同属 ticket 源)
        if data_type == "ticket_finish":
            data_type = "ticket"
        start = r.get("start_time")
        biz_date = _biz_date_of(start)
        success = bool(r.get("success"))
        partial = r.get("partial_failures") or []
        full_day = bool(r.get("full_day"))  # 调用方标记: 本次覆盖整天

        # 近 N 天全量窗口成功 → 把该源窗口内历史 open/partial 一并标 filled
        if full_range and success and not partial:
            _mark_range_filled(db, data_type, full_range[0], full_range[1])
            continue
        if not biz_date:
            continue

        if success and not partial:
            # 成功且无失败片: 仅当"全天覆盖"才可标 filled(白天增量不标)
            if full_day:
                _upsert_gap(db, biz_date, data_type, status="filled", error=None, remark="同步成功覆盖该日")
                changed += 1
            continue
        if success and partial:
            # 部分成功: 失败片范围记录为缺口(结构化 source_range + remark 摘要)
            ranges = []
            min_start = None
            max_end = None
            for p in partial:
                s = p.get("start")
                e = p.get("end")
                ranges.append(f"{s}~{e}")
                try:
                    sd = datetime.fromisoformat(s) if s else None
                    ed = datetime.fromisoformat(e) if e else None
                    if sd and (min_start is None or sd < min_start):
                        min_start = sd
                    if ed and (max_end is None or ed > max_end):
                        max_end = ed
                except ValueError:
                    pass
            _upsert_gap(
                db,
                biz_date,
                data_type,
                status="partial",
                error=(r.get("error") or "部分分片失败")[:2000],
                remark="; ".join(ranges[:20])[:500],
                last_try_at=datetime.now(TZ_CN),
                source_range_start=min_start,
                source_range_end=max_end,
            )
            changed += 1
            continue
        # 失败(整窗未取到数据): 范围 = 本次同步窗口(若可解析)
        src_start = None
        src_end = None
        try:
            if isinstance(start, str):
                src_start = datetime.fromisoformat(start)
        except ValueError:
            pass
        try:
            end_s = r.get("end_time")
            if isinstance(end_s, str):
                src_end = datetime.fromisoformat(end_s)
        except ValueError:
            pass
        _upsert_gap(
            db,
            biz_date,
            data_type,
            status="open",
            error=(r.get("error") or "同步失败")[:2000],
            remark=r.get("error_class", ""),
            last_try_at=datetime.now(TZ_CN),
            source_range_start=src_start,
            source_range_end=src_end,
        )
        changed += 1
    db.commit()
    return changed


def _mark_range_filled(db, data_type: str, start_date: str, end_date: str) -> int:
    """把某源在 [start_date, end_date] 日期范围内的 open/partial 缺口标为 filled。

    用于近 N 天全量兜底(8:00)成功覆盖后, 清理该窗口内已被实际导入覆盖的旧缺口。
    Returns: 标记数量。
    """
    from sqlalchemy import update

    n = 0
    try:
        result = db.execute(
            update(GapRecord)
            .where(
                GapRecord.data_type == data_type,
                GapRecord.biz_date >= start_date,
                GapRecord.biz_date <= end_date,
                GapRecord.status.in_(("open", "partial")),
            )
            .values(status="filled", remark="近N天全量兜底成功覆盖")
        )
        db.flush()
        n = result.rowcount or 0
    except Exception as e:  # noqa: BLE001
        db.rollback()
        logger.warning(f"[gap] _mark_range_filled 失败({data_type}): {e}")
    return n


# ───────────────────────── 查询 ─────────────────────────


def list_gaps(
    db,
    biz_date: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """列出缺口记录(默认近期未补齐的 open/partial)。"""
    stmt = select(GapRecord).order_by(GapRecord.biz_date.desc(), GapRecord.id.desc())
    if biz_date:
        stmt = stmt.where(GapRecord.biz_date == biz_date)
    if status:
        stmt = stmt.where(GapRecord.status == status)
    else:
        stmt = stmt.where(GapRecord.status.in_(("open", "partial")))
    rows = db.scalars(stmt.limit(limit)).all()
    return [
        {
            "id": g.id,
            "biz_date": g.biz_date,
            "data_type": g.data_type,
            "status": g.status,
            "first_gap_at": g.first_gap_at.isoformat() if g.first_gap_at else None,
            "last_try_at": g.last_try_at.isoformat() if g.last_try_at else None,
            "try_count": g.try_count,
            "error": g.error,
            "remark": g.remark,
        }
        for g in rows
    ]


def mark_ignored(db, gap_id: int) -> bool:
    """人工忽略缺口(不再自动补)。"""
    g = db.get(GapRecord, gap_id)
    if not g:
        return False
    g.status = "ignored"
    g.remark = (g.remark or "") + " | 人工忽略"
    db.commit()
    return True


def mark_filled(db, biz_date: str, data_type: str, source: str = "manual") -> bool:
    """外部覆盖确认: 某源某业务日数据已齐 → 标 filled。

    手动补导(upload_records imported)覆盖时调用。
    """
    row = db.scalar(
        select(GapRecord).where(
            GapRecord.biz_date == biz_date,
            GapRecord.data_type == data_type,
        )
    )
    if row is None:
        return False  # 无缺口记录, 无需处理
    if row.status == "filled":
        return True
    row.status = "filled"
    row.remark = (row.remark or "") + f" | 已补齐({source})"
    db.commit()
    return True


# ───────────────────────── 次日清点 + 强制补跑 ─────────────────────────


async def settle_and_backfill_yesterday(db, yesterday: str | None = None) -> dict:
    """次日清点昨天的缺口并强制补跑(5:00 调用)。

    - 查询 gap_records WHERE biz_date=yesterday AND status IN (open, partial)
    - 对每个缺口逐源调 sync_all(时间范围 = 昨天整天), 成功 → filled, 失败 → 保留
      + try_count+1
    - 结果记录到 bms_sync_logs(trigger_type=catch_up)
    - 补跑会忽略熔断(补跑语义本就是强制)

    注意: 本函数内部逐源 commit(自管提交), 调用方须传入**独立 session**
    并在调用后负责 close; 不应在更大外层事务中调用本函数。

    Returns: {"total": n, "backfilled": [...], "still_failed": [...]}
    """
    from app.services.bms_sync import DEFAULT_SYNC_TYPES, sync_all

    if yesterday is None:
        yesterday_dt = datetime.now(TZ_CN).date() - timedelta(days=1)
        yesterday = yesterday_dt.strftime("%Y-%m-%d")

    gaps = db.scalars(
        select(GapRecord).where(
            GapRecord.biz_date == yesterday,
            GapRecord.status.in_(("open", "partial")),
        )
    ).all()
    if not gaps:
        logger.info(f"[gap] {yesterday} 无缺口待补, 跳过清点补跑")
        return {"total": 0, "backfilled": [], "still_failed": []}

    # 只补可定向的源(BMS HTTP/Playwright 默认源; 其它如 SCRM 不在此队列范围)
    bms_known = set(DEFAULT_SYNC_TYPES)
    backfilled: list[str] = []
    still_failed: list[str] = []

    # 范围: 昨天整天(北京)
    y_dt = datetime.strptime(yesterday, "%Y-%m-%d")
    range_start = datetime(y_dt.year, y_dt.month, y_dt.day, 0, 0, 0, tzinfo=TZ_CN)
    range_end = range_start + timedelta(days=1)

    for gap in gaps:
        dt = gap.data_type
        if dt not in bms_known:
            logger.warning(f"[gap] {dt} 非 BMS 主动抓取源(不可按天补跑), 跳过")
            continue
        logger.info(f"[gap] 补跑缺口: {yesterday}/{dt}(try_count={gap.try_count}, status={gap.status})")
        gap.try_count += 1
        gap.last_try_at = datetime.now(TZ_CN)
        db.flush()
        try:
            res = await sync_all(
                trigger_type="catch_up",
                triggered_by="scheduler-gap-backfill",
                data_types=[dt],
                time_ranges={dt: (range_start, range_end)},
            )
            ok = any(
                isinstance(r, dict) and r.get("success") and r.get("data_type") in (dt, f"{dt}_finish")
                for r in (res if isinstance(res, list) else [])
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[gap] 补跑异常 {yesterday}/{dt}: {e}", exc_info=True)
            ok = False
            gap.error = str(e)[:2000]

        if ok:
            gap.status = "filled"
            gap.error = None
            gap.remark = (gap.remark or "") + " | 补跑成功"
            db.flush()
            backfilled.append(dt)
            logger.info(f"[gap] 补跑成功: {yesterday}/{dt} → filled")
        else:
            still_failed.append(dt)
            logger.warning(f"[gap] 补跑仍失败: {yesterday}/{dt}(保留 open/partial, 等 8:00 兜底/次日)")
        db.commit()

    logger.info(
        f"[gap] {yesterday} 缺口清点完成: 共 {len(gaps)} 个,"
        f" 补成功 {len(backfilled)}, 仍失败 {len(still_failed)}"
    )
    return {"total": len(gaps), "backfilled": backfilled, "still_failed": still_failed}
