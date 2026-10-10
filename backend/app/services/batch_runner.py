"""夜间批处理 runner (#258 B4-2, S-P1-7) —— 调度表驱动 + 幂等锁 + 限额熔断

机制来源(只抄模式, 自建不引入 dify/APScheduler/croniter):
- dify: 调度表 DB 驱动(next_run_at 轮询) + 节点级执行日志(batch_job_runs)
- zammad daily_event_lock: 触发幂等锁(此处用部分唯一索引落地)

分工(同 metric_registry 模式):
- 代码层: BATCH_JOBS 注册 job_name → 可调用 + 默认调度参数(函数实现不进 DB)
- DB 层:  batch_jobs 行存调度参数(时刻/开关/超时), 管理员可调

幂等两道锁(SQLAlchemy 模型 + 迁移双写, 部分唯一索引):
- ux_batch_job_runs_running: 同 job 仅一个 running(防重叠)
- ux_batch_job_runs_daily:   同 job 同 business_date 仅一个 running/success(防当日重跑)

business_date 口径: 调度时刻的**北京日期**(夜批 02:30 跑昨天的数据, 归属仍记调度日;
时区换算遵循数据时间字段使用规范, 统一经 Asia/Shanghai)。

熔断: 连续失败 ≥ BREAKER_FAILURE_THRESHOLD → enabled=false(logger 醒目告警),
人工在 API/脚本重新启用后 consecutive_failures 保留(不自动复活)。
"""

import asyncio
import logging
import traceback
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.batch_job import BREAKER_FAILURE_THRESHOLD, BatchJob, BatchJobRun
from app.services.analytics import TZ

logger = logging.getLogger(__name__)

# 轮询间隔(秒): 夜批精度到分钟级足够, 不抢 CPU
POLL_INTERVAL_SECONDS = 60


# ─────────────────────────────────────────────────────────────────────
# 代码层任务注册表: job_name → (可调用, 默认调度时刻, 默认超时分钟)
#
# job 函数签名: fn(db: Session) -> dict  (返回值写入 batch_job_runs.detail)
# 同步函数; runner 在 asyncio.to_thread 中执行(重活不堵事件循环)。
# 注册即声明: 首次 run_due_jobs 时对缺失的 DB 行做幂等补插(INSERT ON CONFLICT DO NOTHING),
# 新任务挂接 = 这里加一行, 无需迁移。
# ─────────────────────────────────────────────────────────────────────
def _rebuild_cash_flow_wide(db: Session) -> dict:
    # 夜间全量重建经营四路宽表(纯派生表, 重建安全; #258 种子任务)
    from app.services.cash_flow_wide_builder import build_cash_flow_wide
    return build_cash_flow_wide(db)


def _rebuild_refund_wide(db: Session) -> dict:
    from app.services.wide_table_builder import build_refund_wide
    return build_refund_wide(db)


def _purge_expired_logs(db: Session) -> dict:
    """按保留期批删超期日志/审计(#261 B4-5)。

    保留期: settings.OPERATION_LOG_RETENTION_DAYS / AUDIT_CHANGE_RETENTION_DAYS。
    破坏性操作纪律: 走 db_guard.assert_safe_to_destroy 并显式 allow_real_db=True ——
    该删除是设计内的常态运维(非脚本误触), reason 写明保留期依据以便审计。
    批删用 id 分批(LIMIT 5000)避免长事务锁表。
    """
    from app.core.config import settings
    from app.core.db_guard import assert_safe_to_destroy
    from app.models.audit_change import AuditChange
    from app.models.operation_log import OperationLog
    from app.services.analytics import TZ

    now_bj = datetime.now(TZ)
    out: dict = {}
    assert_safe_to_destroy(
        db,
        reason="夜批 purge_expired_logs: 按保留期清理超期日志(设计内运维, #261)",
        allow_real_db=True,
    )
    # operation_logs: HTTP 层操作日志, 保留 OPERATION_LOG_RETENTION_DAYS 天
    op_cutoff = (now_bj - timedelta(days=settings.OPERATION_LOG_RETENTION_DAYS))
    deleted_ops = 0
    while True:
        ids = db.scalars(
            select(OperationLog.id).where(OperationLog.created_at < op_cutoff).limit(5000)
        ).all()
        if not ids:
            break
        db.execute(OperationLog.__table__.delete().where(OperationLog.id.in_(ids)))
        db.commit()
        deleted_ops += len(ids)
    out["operation_logs"] = {"cutoff": op_cutoff.isoformat(), "deleted": deleted_ops}
    # audit_changes: 统一变更审计, 保留更久
    ac_cutoff = (now_bj - timedelta(days=settings.AUDIT_CHANGE_RETENTION_DAYS))
    deleted_ac = 0
    while True:
        ids = db.scalars(
            select(AuditChange.id).where(AuditChange.created_at < ac_cutoff).limit(5000)
        ).all()
        if not ids:
            break
        db.execute(AuditChange.__table__.delete().where(AuditChange.id.in_(ids)))
        db.commit()
        deleted_ac += len(ids)
    out["audit_changes"] = {"cutoff": ac_cutoff.isoformat(), "deleted": deleted_ac}
    return out


BATCH_JOBS: dict[str, dict] = {
    # #261: 日志保留期清理先跑(02:00), 在重建类任务前释放磁盘/降低表膨胀
    "purge_expired_logs": {
        "fn": _purge_expired_logs,
        "schedule_daily_at": "02:00",
        "timeout_minutes": 60,
    },
    "rebuild_cash_flow_wide": {
        "fn": _rebuild_cash_flow_wide,
        "schedule_daily_at": "02:30",
        "timeout_minutes": 120,
    },
    "rebuild_refund_wide": {
        "fn": _rebuild_refund_wide,
        "schedule_daily_at": "03:10",
        "timeout_minutes": 120,
    },
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    """DB 读出的 timestamptz 统一转 UTC 比较(psycopg2 返回 aware; naive 视为 UTC)。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _business_date_now() -> str:
    """当前北京日期(ISO 字符串) —— business_date 判定唯一来源。"""
    return datetime.now(TZ).date().isoformat()


def _parse_daily_at(hhmm: str) -> tuple[int, int]:
    """'HH:MM' → (h, m); 非法值抛 ValueError(API 层校验用同一函数)。"""
    parts = str(hhmm).strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"schedule_daily_at 须为 HH:MM, 实际 {hhmm!r}")
    h, m = int(parts[0]), int(parts[1])
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"schedule_daily_at 越界: {hhmm!r}")
    return h, m


def next_run_at_for(hhmm: str, *, now: datetime | None = None) -> datetime:
    """下一调度时刻: 北京时间 HH:MM → UTC tz-aware; 今日时刻已过则顺延一天。"""
    h, m = _parse_daily_at(hhmm)
    now_bj = (now or datetime.now(timezone.utc)).astimezone(TZ)
    candidate = now_bj.replace(hour=h, minute=m, second=0, microsecond=0)
    if candidate <= now_bj:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def _ensure_job_rows(db: Session) -> None:
    """注册表 → DB 行幂等补插(新任务上线/新库首启自动登记, 已有行不覆盖)。"""
    for name, spec in BATCH_JOBS.items():
        db.execute(
            text("""
                INSERT INTO batch_jobs (job_name, schedule_daily_at, enabled,
                                        timeout_minutes, next_run_at, consecutive_failures)
                VALUES (:n, :s, true, :t, :nr, 0)
                ON CONFLICT (job_name) DO NOTHING
            """).bindparams(n=name, s=spec["schedule_daily_at"], t=spec["timeout_minutes"],
                            nr=next_run_at_for(spec["schedule_daily_at"]))
        )
    db.commit()


def _reap_timeouts(db: Session) -> int:
    """超时回收: running 且 started_at 超过 timeout_minutes → 标记 timeout 释放锁。

    timeout 不占 (running,success) 日锁 → 下一轮该业务日可重试。
    Returns: 回收行数(留痕日志)。
    """
    reaped = 0
    rows = db.scalars(
        select(BatchJobRun).where(BatchJobRun.status == "running")
    ).all()
    for run in rows:
        job = db.get(BatchJob, run.job_name)
        limit = timedelta(minutes=job.timeout_minutes if job else 120)
        if _now() - _as_utc(run.started_at) > limit:
            run.status = "timeout"
            run.finished_at = _now()
            run.error = f"运行超过 {limit} 被回收(进程可能在重启前被中断或任务卡死)"
            if job:
                job.last_status = "timeout"
                job.last_error = run.error
                job.last_run_at = run.started_at
            reaped += 1
    if reaped:
        db.commit()
        logger.warning("[batch-runner] 回收超时 run %s 条", reaped)
    return reaped


def run_due_jobs(db: Session, *, triggered_by: str = "scheduler", force: str | None = None) -> list[str]:
    """执行所有到期任务(核心同步入口; 测试直接调用; loop 经 to_thread 调)。

    force: 传入 job_name 时无视 next_run_at 立即执行该任务(manual 触发),
           幂等锁仍然生效(当日已 success / 已有 running 则 skipped)。
    Returns: 实际执行(或被锁跳过)的 job_name 列表。
    """
    _ensure_job_rows(db)
    _reap_timeouts(db)
    now = _now()
    handled: list[str] = []

    jobs = db.scalars(select(BatchJob).where(BatchJob.enabled.is_(True))).all()
    for job in jobs:
        spec = BATCH_JOBS.get(job.job_name)
        if spec is None:
            continue  # DB 有行但代码未注册(已下线任务) → 跳过, 不报错
        if force is None:
            due = job.next_run_at is not None and job.next_run_at <= now
            if not due:
                continue
        handled.append(job.job_name)

        biz_date = _business_date_now()
        run = BatchJobRun(
            job_name=job.job_name, business_date=biz_date,
            status="running", started_at=now, triggered_by=triggered_by,
        )
        db.add(run)
        try:
            db.commit()  # 命中部分唯一索引 → 抢锁失败
        except IntegrityError:
            db.rollback()
            logger.info("[batch-runner] %s 抢锁失败(已有 running 或当日已跑), skipped", job.job_name)
            # 拦截也留痕(skipped 不占两把锁, 可安全插入; 节点级日志: 为什么没跑要可查)
            db.add(BatchJobRun(
                job_name=job.job_name, business_date=biz_date, status="skipped",
                started_at=now, finished_at=now, triggered_by=triggered_by,
                error="幂等锁拦截: 已有 running 或当日已 success",
            ))
            db.commit()
            _mark_last(db, job, "skipped", "幂等锁拦截: 已有 running 或当日已 success")
            continue

        job.last_run_at = now
        db.commit()
        try:
            detail = spec["fn"](db) or {}
            run.status = "success"
            run.finished_at = _now()
            run.detail = detail if isinstance(detail, dict) else {"result": str(detail)}
            job.last_status = "success"
            job.last_error = None
            job.consecutive_failures = 0
        except Exception as e:  # noqa: BLE001 —— 任务失败不允许带崩 runner 循环
            db.rollback()
            run.status = "failed"
            run.finished_at = _now()
            run.error = f"{type(e).__name__}: {e}"[:2000]
            job.last_status = "failed"
            job.last_error = run.error
            job.consecutive_failures = (job.consecutive_failures or 0) + 1
            if job.consecutive_failures >= BREAKER_FAILURE_THRESHOLD:
                job.enabled = False
                logger.error(
                    "[batch-runner] ★ 熔断: %s 连续失败 %d 次, 已自动停用(需人工启用)",
                    job.job_name, job.consecutive_failures,
                )
            logger.error(
                "[batch-runner] %s 执行失败(%d/%d): %s",
                job.job_name, job.consecutive_failures, BREAKER_FAILURE_THRESHOLD,
                traceback.format_exception_only(type(e), e)[-1].rstrip(),
            )
        finally:
            # 先把 run 落库再算 next_run(failed 也顺延, 不同分钟重试 —— 防失败风暴)
            db.add(run)
            db.commit()
        job.next_run_at = next_run_at_for(job.schedule_daily_at, now=now)
        db.commit()
    return handled


def _mark_last(db: Session, job: BatchJob, status: str, err: str) -> None:
    job.last_status = status
    job.last_error = err
    db.commit()


# ─────────────────────────────────────────────────────────────────────
# asyncio 循环(挂 main lifespan, 复用 bms_scheduler 的启动锁路径)
# ─────────────────────────────────────────────────────────────────────
class BatchRunner:
    """夜批轮询循环; start/stop 幂等(与 bms_scheduler 同风格)。"""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return  # 已在跑, 幂等
        self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None

    async def _loop(self) -> None:
        logger.info("[batch-runner] 夜批轮询启动(间隔 %ds)", POLL_INTERVAL_SECONDS)
        while True:
            try:
                # 独立 Session: 与请求/采集事务隔离; to_thread 避免堵事件循环
                def _tick() -> None:
                    db = SessionLocal()
                    try:
                        run_due_jobs(db)
                    finally:
                        db.close()

                await asyncio.to_thread(_tick)
            except asyncio.CancelledError:
                logger.info("[batch-runner] 夜批轮询停止")
                raise
            except Exception as e:  # noqa: BLE001 —— 轮询自身异常只记日志, 不退出
                logger.error("[batch-runner] 轮询异常(继续): %s", e)
            await asyncio.sleep(POLL_INTERVAL_SECONDS)


batch_runner = BatchRunner()
