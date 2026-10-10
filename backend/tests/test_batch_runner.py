# -*- coding: utf-8 -*-
"""B4-2 (#258) 夜间批处理基建: 调度表 + 幂等锁 + 限额熔断

核心用例直接调 run_due_jobs(同步入口), job 函数用注册表临时替身。
注意: run_due_jobs 内 db.commit() 会真实落测试库 —— batch_jobs/batch_job_runs
fixture 前后各清一次; _ensure_job_rows 的 ON CONFLICT 补插也被清表覆盖。
"""

import uuid as _uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete

from app.models.batch_job import BREAKER_FAILURE_THRESHOLD, BatchJob, BatchJobRun
from app.services import batch_runner as br


@pytest.fixture()
def clean_batch_tables(db):
    db.execute(delete(BatchJobRun))
    db.execute(delete(BatchJob))
    db.commit()
    yield
    db.execute(delete(BatchJobRun))
    db.execute(delete(BatchJob))
    db.commit()


@pytest.fixture()
def fake_job(db, clean_batch_tables, monkeypatch):
    """注册一个临时测试任务并把 DB 行设为到期(直接过期 next_run_at)。"""
    name = f"test_job_{_uuid.uuid4().hex[:8]}"
    calls = {"n": 0}

    def _fn(session):
        calls["n"] += 1
        return {"calls": calls["n"]}

    monkeypatch.setitem(br.BATCH_JOBS, name, {
        "fn": _fn, "schedule_daily_at": "02:30", "timeout_minutes": 60,
    })
    row = BatchJob(job_name=name, schedule_daily_at="02:30", enabled=True,
                   timeout_minutes=60,
                   next_run_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    db.add(row)
    db.commit()
    return {"name": name, "calls": calls, "row": row}


def test_job_runs_once_when_due(db, fake_job):
    """到期任务执行一次: success + detail 落库 + next_run_at 顺延 + 失败计数清零。"""
    handled = br.run_due_jobs(db)
    assert fake_job["name"] in handled
    assert fake_job["calls"]["n"] == 1
    row = db.get(BatchJob, fake_job["name"])
    assert row.last_status == "success" and row.consecutive_failures == 0
    assert row.next_run_at > datetime.now(timezone.utc)
    run = db.query(BatchJobRun).filter_by(job_name=fake_job["name"]).one()
    assert run.status == "success" and run.detail == {"calls": 1}


def test_idempotent_daily_lock(db, fake_job):
    """同 business_date 二次触发被日锁拦截: 不重跑, run 记 skipped。"""
    assert fake_job["name"] in br.run_due_jobs(db)
    # 第二轮仍到期(手动把 next_run_at 拨回过去)
    row = db.get(BatchJob, fake_job["name"])
    row.next_run_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    handled2 = br.run_due_jobs(db)
    assert fake_job["name"] in handled2
    assert fake_job["calls"]["n"] == 1  # 不重跑
    statuses = [r.status for r in db.query(BatchJobRun).filter_by(job_name=fake_job["name"]).all()]
    assert statuses.count("skipped") == 1 and statuses.count("success") == 1


def test_failure_breaker_trips(db, fake_job, monkeypatch):
    """连续失败 ≥3 → enabled=false 熔断; 手动重开后可再执行。"""
    def _boom(session):
        raise RuntimeError("boom")
    br.BATCH_JOBS[fake_job["name"]]["fn"] = _boom
    for i in range(BREAKER_FAILURE_THRESHOLD):
        row = db.get(BatchJob, fake_job["name"])
        row.enabled = True  # 前两轮失败不清 enabled; 第三轮由熔断关闭
        row.next_run_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
        br.run_due_jobs(db)
        row = db.get(BatchJob, fake_job["name"])
        if i < BREAKER_FAILURE_THRESHOLD - 1:
            assert row.enabled is True
    assert row.enabled is False  # 熔断
    assert row.consecutive_failures == BREAKER_FAILURE_THRESHOLD
    # 熔断后不再执行
    row.next_run_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    row.enabled = False
    db.commit()
    br.run_due_jobs(db)
    assert fake_job["calls"]["n"] == 0  # 替身未跑过(全程都是 _boom)


def test_timeout_reap_releases_lock(db, fake_job):
    """running 超 timeout_minutes → 回收标记 timeout; 同业务日可重跑。"""
    name = fake_job["name"]
    # 伪造一条 2 小时前开始的 running(锁 60 分钟)
    stale = BatchJobRun(job_name=name, business_date=br._business_date_now(),
                        status="running",
                        started_at=datetime.now(timezone.utc) - timedelta(hours=2))
    db.add(stale)
    db.commit()
    reaped = br._reap_timeouts(db)
    assert reaped == 1
    db.refresh(stale)
    assert stale.status == "timeout" and stale.finished_at is not None
    # 锁已释放: 正常执行可跑
    assert fake_job["name"] in br.run_due_jobs(db)
    assert fake_job["calls"]["n"] == 1


def test_new_job_registered_and_not_due(db, clean_batch_tables):
    """_ensure_job_rows 幂等补插注册表任务; 未到 next_run_at 不执行。"""
    br.run_due_jobs(db)  # 首轮: 补插 rebuild_* 两行(时刻未来, 不执行)
    names = {j.job_name for j in db.query(BatchJob).all()}
    assert {"rebuild_cash_flow_wide", "rebuild_refund_wide"} <= names
    for j in db.query(BatchJob).all():
        assert j.next_run_at is not None
    runs = db.query(BatchJobRun).all()
    assert runs == []  # 都未到期
    # 二轮补插幂等(无重复行/无异常)
    br.run_due_jobs(db)
    assert db.query(BatchJob).count() == len(br.BATCH_JOBS)


def test_next_run_at_timezone_math(db, clean_batch_tables):
    """next_run_at_for: 北京 02:30 → 未来时刻; 今日已过顺延一天; 非法输入拒绝。"""
    now = datetime(2026, 10, 8, 20, 0, tzinfo=timezone.utc)  # 北京 10-09 04:00
    nxt = br.next_run_at_for("02:30", now=now)
    assert nxt > now
    # 北京 10-09 04:00 的下一次 02:30 = 北京 10-10 02:30 = UTC 10-09 18:30
    assert nxt.hour == 18 and nxt.minute == 30 and nxt.day == 9
    with pytest.raises(ValueError):
        br._parse_daily_at("25:00")
    with pytest.raises(ValueError):
        br._parse_daily_at("bad")
