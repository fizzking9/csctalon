"""device_combo 历史切片自动回补 (#299)

替谁解决什么问题:
  BMS「设备套餐」页导出存在 10 万行硬上限(实测 100005 数据行, 按创建时间倒序),
  而每日定时同步因日期面板拦截搜索按钮(#299 探针实证), 长期只导出默认近 7 天,
  2026-07-28 之前约 723 个北京业务日缺失。本服务在夜间空闲档(01:00~04:45)
  按「缺失北京业务日」自适应切片, 逐片走 BmsFetcher 导出 + 既有 _auto_import
  upsert 通道(与手工导入 #298 同通道, 幂等可重跑), 断点台账见
  combo_backfill_slices 表。

设计取舍(详见 #299 方案):
  - 不做前端界面: 操作入口仅调度器自动档 + admin API(手动触发/查进度)。
  - 不重写导入通道: 复用 _auto_import → ingest_table → upsert_device_combos。
  - 缺口实时从 device_combos 计算, 不维护独立缺口表; 台账只存切片执行状态。
  - 无缺口夜自动 no-op, 同时兼作未来漏采的自愈通道。
  - 触顶切片 status=split, 生成两个半片子切片; 父片已入的尾部数据被子片
    upsert 幂等覆盖, 不做删除。
"""

import asyncio
import logging
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import text

from app.core.database import SessionLocal
from app.models.combo_backfill_slice import (
    SLICE_EMPTY,
    SLICE_FAILED,
    SLICE_PENDING,
    SLICE_RUNNING,
    SLICE_SPLIT,
    SLICE_SUCCESS,
    ComboBackfillSlice,
)

logger = logging.getLogger(__name__)

TZ_CN = timezone(timedelta(hours=8))

DATA_TYPE = "device_combo"
# BMS 导出硬上限实测 100005 数据行; >=100000 即视为触顶(留 5 行余量防口径变动)
CAP_ROWS = 100_000
# BMS「1 分钟内请勿重复操作」: 相邻切片的 导出→下一片搜索 间隔
PACE_SECONDS = 65
# 夜间档: 01:00 启动(让开 05:00 补跑 / 06:00 低频档 / 08:00 起高频档)
NIGHT_START_HOUR = 1
NIGHT_DEADLINE_HOUR = 4
NIGHT_DEADLINE_MINUTE = 45
# 连续失败 3 片视为 BMS 凌晨维护/故障信号, 当夜休眠(已落台账, 次夜续跑)
MAX_CONSECUTIVE_FAIL = 3
# 单片当夜最多尝试次数(次夜开盘会把 failed 重新置 pending 并清零计数)
MAX_ATTEMPTS_PER_NIGHT = 3
# 回补地平线: 套餐业务 2024-01-01 起有数据(探针实证首单 2024-01-01 00:18)
HORIZON_START = date(2024, 1, 1)
MAX_ERROR_LEN = 2000

# 进程级运行锁(单 worker 部署, 内存锁即足够; 与 sync_task_manager 互斥配合)
_run_lock = asyncio.Lock()
_running: bool = False


def now_cn() -> datetime:
    """当前北京时间(tz-aware)"""
    return datetime.now(TZ_CN)


def in_night_window(now: datetime | None = None) -> bool:
    """是否处于夜间回补窗口 [01:00, 04:45)"""
    n = now or now_cn()
    if n.hour < NIGHT_START_HOUR:
        return False
    if n.hour < NIGHT_DEADLINE_HOUR:
        return True
    return n.hour == NIGHT_DEADLINE_HOUR and n.minute < NIGHT_DEADLINE_MINUTE


def night_deadline(now: datetime | None = None) -> datetime:
    """今天 04:45 北京时间(异步循环硬停止点)"""
    n = now or now_cn()
    return n.replace(hour=NIGHT_DEADLINE_HOUR, minute=NIGHT_DEADLINE_MINUTE,
                     second=0, microsecond=0)


def slice_width_for(d: date) -> int:
    """按年代数据密度给自适应基础片宽(天): 越早越稀疏, 片越宽。

    依据(#299 缺口实测): 2024 年初周量仅数百行; 2026 年近期周量已达 8 万行,
    接近 10 万导出上限, 近期必须窄片; 触顶仍有 split 折半兜底。
    """
    if d < date(2024, 9, 1):
        return 30
    if d < date(2025, 6, 1):
        return 21
    if d < date(2026, 1, 1):
        return 14
    return 7


def group_consecutive_dates(days: list[date]) -> list[list[date]]:
    """把升序日期列表按连续性分段(相邻差 1 天)"""
    if not days:
        return []
    runs: list[list[date]] = [[days[0]]]
    for d in days[1:]:
        if (d - runs[-1][-1]).days == 1:
            runs[-1].append(d)
        else:
            runs.append([d])
    return runs


def chunk_run(run: list[date]) -> list[tuple[date, date]]:
    """把一段连续缺失日按「起始日所属年代」的片宽切成 [start, end](均含)"""
    if not run:
        return []
    width = slice_width_for(run[0])
    return [
        (run[i], run[min(i + width, len(run)) - 1])
        for i in range(0, len(run), width)
    ]


def plan_slices(missing_days: list[date]) -> list[tuple[date, date]]:
    """缺失日 → 切片区间列表(升序)。纯函数, 供单测。"""
    out: list[tuple[date, date]] = []
    for run in group_consecutive_dates(missing_days):
        out.extend(chunk_run(run))
    return out


def split_range(start: date, end: date) -> list[tuple[date, date]]:
    """触顶折半: [start, end] 切成两个尽量等长的半片。

    单日片无法再切 → 返回空列表(调用方记 failed, 待人工/次日处置)。
    """
    span = (end - start).days
    if span <= 0:
        return []
    mid = start + timedelta(days=span // 2)
    return [(start, mid), (mid + timedelta(days=1), end)]


def window_datetimes(start: date, end: date) -> tuple[datetime, datetime]:
    """北京业务日 → fetcher 用的 tz-aware [当天00:00:00, 当天23:59:59]"""
    return (
        datetime.combine(start, time(0, 0, 0), tzinfo=TZ_CN),
        datetime.combine(end, time(23, 59, 59), tzinfo=TZ_CN),
    )


# ──────────────────────────── DB 操作(同步, to_thread 调用) ────────────────────────────

def query_missing_dates(db, start: date, end: date) -> list[date]:
    """查 device_combos 在 [start, end] 内缺失的北京业务日(升序)。

    口径与看板一致: created_at_raw AT TIME ZONE 'Asia/Shanghai' 取北京日
    (铁律 5, 禁止绕过此时区换算)。参数化绑定, 不拼 SQL。
    """
    sql = text(
        """
        SELECT d.d::date AS d
        FROM generate_series(:start_date, :end_date, '1 day') AS d(d)
        WHERE NOT EXISTS (
            SELECT 1
            FROM device_combos dc
            WHERE (dc.created_at_raw AT TIME ZONE 'Asia/Shanghai')::date = d.d::date
        )
        ORDER BY d.d
        """
    )
    return [row.d for row in db.execute(sql, {"start_date": start, "end_date": end}).all()]


def reset_failed_for_new_night(db) -> int:
    """新一夜开盘: 历史 failed 片重新置 pending 并清零当夜计数(给瞬时故障二次机会)。"""
    rows = db.query(ComboBackfillSlice).filter(
        ComboBackfillSlice.status == SLICE_FAILED
    ).all()
    for row in rows:
        row.status = SLICE_PENDING
        row.attempts = 0
        row.last_error = (row.last_error or "")[: MAX_ERROR_LEN - 40] + "\n[次夜重试, 已重置]"
    db.commit()
    return len(rows)


def ensure_slice_rows(db, ranges: list[tuple[date, date]]) -> int:
    """把计划切片登记进台账(已存在的任意状态行不动)。返回新增条数。"""
    existing = {
        (r.start_date, r.end_date)
        for r in db.query(ComboBackfillSlice.start_date, ComboBackfillSlice.end_date).all()
    }
    n = 0
    for s, e in ranges:
        if (s, e) in existing:
            continue
        db.add(ComboBackfillSlice(
            start_date=s, end_date=e, status=SLICE_PENDING, attempts=0,
        ))
        n += 1
    if n:
        db.commit()
    return n


def enqueue_children(db, parent: ComboBackfillSlice) -> list[tuple[date, date]]:
    """触顶父片折半, 子片以 pending 入台账(唯一约束防重复)。"""
    children = split_range(parent.start_date, parent.end_date)
    n = ensure_slice_rows(db, children)
    logger.info(f"[combo-backfill] 切片 {parent.start_date}~{parent.end_date} "
                f"触顶折半 → 子片 {len(children)} 个(新增 {n})")
    return children


def claim_next_slice(db):
    """领取下一个待执行片: pending 优先, failed 且当夜未超次数次之, 按起始日升序。

    running 残留(进程崩溃)也允许重新领取。返回 None 表示队列已清空。
    """
    return db.query(ComboBackfillSlice).filter(
        ComboBackfillSlice.status.in_([SLICE_PENDING, SLICE_RUNNING, SLICE_FAILED]),
        ComboBackfillSlice.attempts < MAX_ATTEMPTS_PER_NIGHT,
    ).order_by(ComboBackfillSlice.start_date.asc()).first()


def mark_running(db, sl: ComboBackfillSlice, triggered_by: str) -> None:
    sl.status = SLICE_RUNNING
    sl.attempts += 1
    sl.triggered_by = triggered_by
    sl.started_at = now_cn()
    db.commit()


def mark_done(db, sl: ComboBackfillSlice, status: str, *,
              stats: dict | None = None, error: str | None = None) -> None:
    sl.status = status
    sl.finished_at = now_cn()
    if stats:
        sl.total_rows = stats.get("total_rows")
        sl.inserted_rows = stats.get("inserted_rows")
        sl.updated_rows = stats.get("updated_rows")
        sl.failed_rows = stats.get("failed_rows")
    if error:
        sl.last_error = error[:MAX_ERROR_LEN]
    db.commit()


def backfill_status(db) -> dict:
    """台账汇总(给 GET status / 关单凭证用)。"""
    from sqlalchemy import func

    grouped = dict(db.query(
        ComboBackfillSlice.status, func.count(ComboBackfillSlice.id)
    ).group_by(ComboBackfillSlice.status).all())
    recent = db.query(ComboBackfillSlice).order_by(
        ComboBackfillSlice.updated_at.desc()
    ).limit(10).all()
    return {
        "running": _running,
        "counts": grouped,
        "remaining": int(sum(
            grouped.get(s, 0) for s in (SLICE_PENDING, SLICE_RUNNING, SLICE_FAILED)
        )),
        "recent": [
            {
                "start_date": str(r.start_date),
                "end_date": str(r.end_date),
                "status": r.status,
                "total_rows": r.total_rows,
                "inserted_rows": r.inserted_rows,
                "updated_rows": r.updated_rows,
                "attempts": r.attempts,
                "last_error": r.last_error,
            }
            for r in recent
        ],
    }


def _invalidate_caches() -> int:
    """回补结束失效近 N 天看板缓存(复用同步后标准通道, 历史日聚合一般不落缓存)。"""
    try:
        from app.core.redis_client import cache_invalidate_recent_days

        n = cache_invalidate_recent_days("today:*")
        if n:
            logger.info(f"[combo-backfill] 已失效 {n} 条 today:* 缓存")
        return n
    except Exception as ce:  # noqa: BLE001 缓存失效不影响回补结果
        logger.warning(f"[combo-backfill] 缓存失效失败(忽略): {ce}")
        return 0


# ──────────────────────────── 主流程 ────────────────────────────

def is_running() -> bool:
    return _running


async def run_backfill(
    *,
    triggered_by: str = "scheduled",
    deadline: datetime | None = None,
    allow_daytime: bool = False,
    horizon_start: date = HORIZON_START,
    horizon_end: date | None = None,
    max_slices: int | None = None,
) -> dict:
    """执行一夜的切片回补循环。可被调度器(01:00)或 admin API(手动)启动。

    安全护栏:
      - 进程级 asyncio 锁: 同一时刻只允许一个回补循环;
      - 与 sync_task_manager 互斥: 任一同步任务在跑则不启动/不领片;
      - deadline(默认今夜 04:45) 后硬停止, 未完成片留台账次夜续跑;
      - 连续 MAX_CONSECUTIVE_FAIL 片失败 → 当夜休眠(BMS 维护信号)。
    """
    global _running

    if _running:
        raise RuntimeError("device_combo 回补已在运行中")

    n = now_cn()
    if not allow_daytime and not in_night_window(n):
        raise RuntimeError(
            f"当前 {n:%H:%M} 不在夜间回补窗口(01:00~04:45), 如需白天手动跑请显式 allow_daytime"
        )

    async with _run_lock:
        _running = True
        # 截止点: 夜间跑取今夜 04:45; 白天手动跑给 3 小时预算
        stop_at = deadline or (
            night_deadline(n) if in_night_window(n) else n + timedelta(hours=3)
        )
        # 只补到「前天」: 昨天整天归 06:00 低频档(夜间 01:00 时它还没跑)
        end_date = horizon_end or (n.date() - timedelta(days=2))

        summary = {
            "triggered_by": triggered_by,
            "deadline": stop_at.isoformat(),
            "horizon": [str(horizon_start), str(end_date)],
            "planned": 0, "succeeded": 0, "empty": 0, "split": 0,
            "failed": 0, "deadline_stopped": 0, "inserted_rows": 0,
            "stopped_reason": None,
        }
        db = SessionLocal()
        try:
            # 1) 计划缺口切片(纯查询 + 登记)
            reset_failed_for_new_night(db)
            missing = await asyncio.to_thread(query_missing_dates, db, horizon_start, end_date)
            ranges = plan_slices(missing)
            summary["planned"] = await asyncio.to_thread(ensure_slice_rows, db, ranges)
            logger.info(
                f"[combo-backfill] 启动({triggered_by}): 缺失 {len(missing)} 天, "
                f"本轮新登记 {summary['planned']} 片, 截止 {stop_at:%Y-%m-%d %H:%M}"
            )
            if not missing:
                logger.info("[combo-backfill] 无缺失日期, 今夜 no-op")
                summary["stopped_reason"] = "no_gap"
                return summary

            # 2) 读 BMS 源配置(账号密码来自 sync_source_configs)
            from app.services.sync_source_config_service import get_sync_source_config

            config = get_sync_source_config(db, DATA_TYPE)
            if not config:
                raise RuntimeError(f"同步源配置不存在: {DATA_TYPE}")

            # 3) 逐片导出+导入(浏览器实例全程复用, 登录一次)
            from app.services.bms_fetcher import BmsFetcher
            from app.services.bms_sync import _auto_import
            from app.services.sync_task_manager import sync_task_manager

            consecutive_fail = 0
            done_this_night = 0
            async with BmsFetcher() as fetcher:
                while True:
                    if now_cn() >= stop_at:
                        summary["stopped_reason"] = "deadline"
                        logger.info("[combo-backfill] 到达 04:45 硬停止点, 未完成片留档次夜续跑")
                        break
                    if sync_task_manager.is_busy():
                        # 与其它同步任务互斥(BMS 同账号并发会互相踢登录)
                        summary["stopped_reason"] = "sync_busy"
                        logger.info("[combo-backfill] 检测到其它同步任务在跑, 当夜休眠")
                        break
                    if max_slices is not None and done_this_night >= max_slices:
                        summary["stopped_reason"] = "max_slices"
                        break

                    sl = await asyncio.to_thread(claim_next_slice, db)
                    if sl is None:
                        summary["stopped_reason"] = "queue_empty"
                        logger.info("[combo-backfill] 队列清空, 历史回补完成")
                        break

                    await asyncio.to_thread(mark_running, db, sl, triggered_by)
                    ws, we = window_datetimes(sl.start_date, sl.end_date)
                    logger.info(
                        f"[combo-backfill] 切片开始 {sl.start_date}~{sl.end_date} "
                        f"(第 {sl.attempts} 次)"
                    )
                    try:
                        fr = await fetcher.fetch_one(DATA_TYPE, ws, we, config)
                        if getattr(fr, "empty_result", False):
                            await asyncio.to_thread(
                                mark_done, db, sl, SLICE_EMPTY,
                                stats={"total_rows": 0, "inserted_rows": 0,
                                       "updated_rows": 0, "failed_rows": 0},
                            )
                            summary["empty"] += 1
                            consecutive_fail = 0
                        elif not fr.success:
                            raise RuntimeError(fr.error or "抓取失败")
                        else:
                            # 阻塞型 pandas/SQLAlchemy 导入放线程, 独立 session
                            def _import_work():
                                inner = SessionLocal()
                                try:
                                    return _auto_import(
                                        inner, DATA_TYPE, fr.file_path, fr.filename,
                                        f"combo-backfill-299:{triggered_by}",
                                    )
                                finally:
                                    inner.close()

                            stats = await asyncio.to_thread(_import_work)
                            summary["inserted_rows"] += int(stats.get("inserted_rows") or 0)
                            if int(stats.get("total_rows") or 0) >= CAP_ROWS:
                                # 触顶: 先确认可折半; 单日触顶(理论上 BMS 单日不会 10 万行)
                                # 不能静默 split, 记 failed 等人工核查
                                children = split_range(sl.start_date, sl.end_date)
                                if not children:
                                    raise RuntimeError(
                                        f"单日切片触顶 {CAP_ROWS} 行上限且无法折半, 需人工核查"
                                    )
                                # 父片记 split 并折半入队(已入尾部数据由子片幂等覆盖)
                                await asyncio.to_thread(
                                    mark_done, db, sl, SLICE_SPLIT, stats=stats,
                                )
                                await asyncio.to_thread(enqueue_children, db, sl)
                                summary["split"] += 1
                            else:
                                await asyncio.to_thread(
                                    mark_done, db, sl, SLICE_SUCCESS, stats=stats,
                                )
                                summary["succeeded"] += 1
                            consecutive_fail = 0
                    except Exception as e:  # noqa: BLE001 单片失败不拖垮整夜
                        logger.warning(f"[combo-backfill] 切片失败: {e}")
                        await asyncio.to_thread(
                            mark_done, db, sl, SLICE_FAILED, error=str(e),
                        )
                        summary["failed"] += 1
                        consecutive_fail += 1
                        if consecutive_fail >= MAX_CONSECUTIVE_FAIL:
                            summary["stopped_reason"] = "consecutive_fail"
                            logger.warning(
                                "[combo-backfill] 连续 3 片失败, 判定 BMS 维护/故障, 当夜休眠"
                            )
                            break

                    done_this_night += 1
                    # 相邻切片间遵守 BMS 1 分钟限流(最后一片无需空等, 但 sleep 成本无害,
                    # 保留统一节奏也让「队列清空」判定更稳)
                    if now_cn() < stop_at:
                        await asyncio.sleep(PACE_SECONDS)

            await asyncio.to_thread(_invalidate_caches)
            remaining = backfill_status(db)["remaining"]
            summary["remaining"] = remaining
            logger.info(f"[combo-backfill] 当夜结束: {summary}")
            return summary
        finally:
            db.close()
            _running = False
