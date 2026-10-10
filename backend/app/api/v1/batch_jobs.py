"""夜批任务管理路由 (#258 B4-2)

设计说明（Design Rationale）:
  目的: 替管理员回答「夜批任务跑没跑/几点跑/为什么没跑」, 并提供失败后的人工干预入口。
  逻辑: 调度参数在 batch_jobs 行(代码层 BATCH_JOBS 注册实现), 本路由只读写配置与
        触发记录; 手动触发走 run_due_jobs(force=...) 复用同一套幂等锁与留痕。
  取舍: 不做管理页(数据量小, API 维护); 不做任意时刻 cron(v1 每日 HH:MM)。
  工单: #258
"""

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import DBSession, ManagerUser
from app.models.batch_job import BatchJob, BatchJobRun
from app.schemas.common import R
from app.services.batch_runner import (
    BATCH_JOBS,
    _parse_daily_at,
    next_run_at_for,
    run_due_jobs,
)

router = APIRouter(prefix="/batch/jobs", tags=["夜批任务"])


def _job_item(j: BatchJob) -> dict:
    return {
        "job_name": j.job_name,
        "schedule_daily_at": j.schedule_daily_at,
        "enabled": j.enabled,
        "timeout_minutes": j.timeout_minutes,
        "next_run_at": j.next_run_at.isoformat() if j.next_run_at else None,
        "last_run_at": j.last_run_at.isoformat() if j.last_run_at else None,
        "last_status": j.last_status,
        "last_error": j.last_error,
        "consecutive_failures": j.consecutive_failures,
        "registered": j.job_name in BATCH_JOBS,
    }


@router.get("", response_model=R, summary="夜批任务列表(调度状态 + 最近结果)")
def list_jobs(db: DBSession, _: ManagerUser):
    rows = db.scalars(select(BatchJob).order_by(BatchJob.job_name)).all()
    return R(data={"items": [_job_item(j) for j in rows], "total": len(rows)})


@router.get("/{job_name}/runs", response_model=R, summary="任务执行日志(最近 50 条)")
def list_runs(job_name: str, db: DBSession, _: ManagerUser):
    rows = db.scalars(
        select(BatchJobRun)
        .where(BatchJobRun.job_name == job_name)
        .order_by(BatchJobRun.id.desc())
        .limit(50)
    ).all()
    return R(data={"items": [
        {
            "id": r.id, "business_date": r.business_date, "status": r.status,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "detail": r.detail, "error": r.error, "triggered_by": r.triggered_by,
        }
        for r in rows
    ]})


@router.put("/{job_name}", response_model=R, summary="调整调度参数(管理员; 熔断后人工重开也走这里)")
def update_job(job_name: str, payload: dict, db: DBSession, user: ManagerUser):
    j = db.get(BatchJob, job_name)
    if not j:
        raise HTTPException(status_code=404, detail=f"任务 {job_name} 未登记")
    if "schedule_daily_at" in payload:
        v = str(payload["schedule_daily_at"]).strip()
        try:
            _parse_daily_at(v)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        j.schedule_daily_at = v
        j.next_run_at = next_run_at_for(v)  # 时刻变更即重算下次调度
    if "timeout_minutes" in payload:
        try:
            t = int(payload["timeout_minutes"])
        except (TypeError, ValueError) as e:
            raise HTTPException(status_code=400, detail="timeout_minutes 须为整数") from e
        if t < 1 or t > 24 * 60:
            raise HTTPException(status_code=400, detail="timeout_minutes 须在 1~1440")
        j.timeout_minutes = t
    if "enabled" in payload:
        j.enabled = bool(payload["enabled"])
        # 人工重新启用: 时刻变更无关, 但熔断恢复需要知道连续失败基数 —— 保留计数,
        # 由下一次成功清零(防"启用即忘"导致再次静默熔断无感知)
        j.updated_by = getattr(user, "username", None)
        if j.enabled:
            j.next_run_at = next_run_at_for(j.schedule_daily_at)  # 启用即排到下一时刻
    db.commit()
    return R(message="调度参数已更新", data=_job_item(j))


@router.post("/{job_name}/run", response_model=R, summary="手动触发(管理员; 幂等锁仍生效, 当日已跑会被拦截)")
def trigger_job(job_name: str, db: DBSession, user: ManagerUser):
    if job_name not in BATCH_JOBS:
        raise HTTPException(status_code=404, detail=f"任务 {job_name} 未在代码层注册")
    handled = run_due_jobs(db, triggered_by="manual", force=job_name)
    run = db.scalars(
        select(BatchJobRun)
        .where(BatchJobRun.job_name == job_name)
        .order_by(BatchJobRun.id.desc())
        .limit(1)
    ).first()
    return R(data={
        "handled": handled,
        "last_run": {
            "business_date": run.business_date, "status": run.status,
            "finished_at": run.finished_at.isoformat() if run and run.finished_at else None,
            "detail": run.detail, "error": run.error,
        } if run else None,
    })
