# -*- coding: utf-8 -*-
"""B3-8 (#256) 排班镜像落地: 企微排班表导入 + 冲突处理 + 来源标注。

注意: API 内 db.commit() 会真实落测试库(外层 rollback 救不回),
与 test_person_skills 同模式 —— fixtures 前后各清理一次相关表。
"""

import uuid as _uuid

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.main import app
from app.models.person import Person
from app.models.schedule import Schedule, ShiftTemplate
from app.models.upload import UploadRecord
from app.models.user import RoleEnum, User


@pytest.fixture()
def authed_client(db, tmp_path, monkeypatch):
    """带鉴权 + 重定向 UPLOAD_DIR 到 tmp 的 TestClient。"""
    fake = User(id=1, username="tester", role=RoleEnum.ADMIN.value, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: fake

    def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    db.query(Schedule).delete()
    db.query(UploadRecord).filter_by(data_type="schedule").delete()
    db.commit()
    try:
        with TestClient(app) as c:
            yield c
    finally:
        db.query(Schedule).delete()
        db.query(UploadRecord).filter_by(data_type="schedule").delete()
        db.commit()
        app.dependency_overrides.clear()


def _mk_xlsx(tmp_path, rows: list[dict], rel: str | None = None) -> str:
    """写一个排班 xlsx 到 UPLOAD_DIR, 返回 stored_path 相对路径。"""
    rel = rel or f"2026-10/{_uuid.uuid4().hex[:8]}.xlsx"
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(p, engine="openpyxl") as xw:
        pd.DataFrame(rows).to_excel(xw, sheet_name="Sheet1", index=False)
    return rel


def _mk_person(db, name: str) -> Person:
    p = Person(display_name=name)
    db.add(p)
    db.flush()
    return p


def _mk_shift(db, name: str = "早班", start: str = "08:00", end: str = "16:00") -> ShiftTemplate:
    s = db.query(ShiftTemplate).filter_by(name=name).first()
    if s:
        return s
    s = ShiftTemplate(name=name, start_time=start, end_time=end, sort_order=1)
    db.add(s)
    db.flush()
    return s


def test_import_basic_and_trace(db, authed_client, tmp_path):
    """正常导入: 别名匹配 + source=wecom_export + upload_records 留痕 + 列表返回来源。"""
    from app.models.person import PersonAlias

    p1 = _mk_person(db, f"张三-{_uuid.uuid4().hex[:6]}")
    p2 = _mk_person(db, f"李四-{_uuid.uuid4().hex[:6]}")
    alias_name = f"三丰-{_uuid.uuid4().hex[:6]}"  # 别名 uuid 化防脏数据(unique 约束)
    db.add(PersonAlias(person_id=p1.id, alias=alias_name, alias_normalized=alias_name))
    _mk_shift(db, "早班")
    db.flush()

    stored = _mk_xlsx(tmp_path, [
        {"姓名": p1.display_name, "日期": "2026-10-10", "班次": "早班"},
        {"姓名": alias_name, "日期": "2026-10-11", "班次": "早班"},       # 别名命中 p1
        {"姓名": p2.display_name, "日期": "2026-10-10", "时间": "12:00-20:00"},  # 无班次→时间列
    ])
    r = authed_client.post("/api/v1/schedules/import", json={
        "stored_path": stored, "filename": "排班.xlsx", "conflict_mode": "skip",
    })
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["inserted"] == 3 and d["failed"] == 0 and d["skipped"] == 0

    rows = db.query(Schedule).filter_by(person_id=p1.id).all()
    assert len(rows) == 2
    assert all(s.source == "wecom_export" for s in rows)
    by_date = {s.schedule_date.isoformat(): s for s in rows}
    assert by_date["2026-10-10"].shift_template_id is not None
    assert by_date["2026-10-11"].shift_template_id is not None
    p2_row = db.query(Schedule).filter_by(person_id=p2.id).one()
    assert (p2_row.start_time, p2_row.end_time) == ("12:00", "20:00")

    # 留痕: upload_records(data_type='schedule')
    rec = db.query(UploadRecord).filter_by(data_type="schedule").one()
    assert rec.status == "imported" and rec.total_rows == 3 and rec.inserted_rows == 3

    # 列表接口带 source 字段(前端来源标注数据面)
    ls = authed_client.get("/api/v1/schedules", params={"start": "2026-10-10", "end": "2026-10-11"})
    assert all(it["source"] == "wecom_export" for it in ls.json()["data"])


def test_import_conflict_skip_vs_overwrite(db, authed_client, tmp_path):
    """同人同日冲突: skip 保持原班次+source; overwrite 覆盖且 source 变 wecom_export。"""
    p = _mk_person(db, f"王五-{_uuid.uuid4().hex[:6]}")
    shift = _mk_shift(db, "早班")
    other = _mk_shift(db, "晚班", "16:00", "24:00")
    # 系统编排已有排班
    db.add(Schedule(person_id=p.id, schedule_date=__import__("datetime").date(2026, 10, 12),
                    shift_template_id=shift.id, source="manual", created_by="boss"))
    db.flush()

    stored = _mk_xlsx(tmp_path, [{"姓名": p.display_name, "日期": "2026-10-12", "班次": "晚班"}])
    # skip: 不覆盖
    r1 = authed_client.post("/api/v1/schedules/import", json={"stored_path": stored, "conflict_mode": "skip"})
    d1 = r1.json()["data"]
    assert d1["skipped"] == 1 and d1["updated"] == 0
    row = db.query(Schedule).filter_by(person_id=p.id).one()
    assert row.shift_template_id == shift.id and row.source == "manual"

    # overwrite: 覆盖班次且 source 变为 wecom_export
    r2 = authed_client.post("/api/v1/schedules/import", json={"stored_path": stored, "conflict_mode": "overwrite"})
    d2 = r2.json()["data"]
    assert d2["updated"] == 1
    db.expire_all()
    row2 = db.query(Schedule).filter_by(person_id=p.id).one()
    assert row2.shift_template_id == other.id and row2.source == "wecom_export"


def test_import_row_errors(db, authed_client, tmp_path):
    """行级错误计数: 未知人员/未知班次/空姓名 → failed, 且错误带行号。"""
    p = _mk_person(db, f"赵六-{_uuid.uuid4().hex[:6]}")
    _mk_shift(db, "早班")
    db.flush()
    stored = _mk_xlsx(tmp_path, [
        {"姓名": "不存在的人", "日期": "2026-10-13", "班次": "早班"},  # 人员未匹配
        {"姓名": p.display_name, "日期": "2026-10-13", "班次": "神秘班"},  # 班次未匹配
        {"姓名": "", "日期": "2026-10-13", "班次": "早班"},  # 姓名空
        {"姓名": p.display_name, "日期": "2026-10-13", "班次": "早班"},  # 正常
    ])
    r = authed_client.post("/api/v1/schedules/import", json={"stored_path": stored, "conflict_mode": "skip"})
    d = r.json()["data"]
    assert d["failed"] == 3 and d["inserted"] == 1
    assert sum("人员未匹配" in e for e in d["errors"]) == 1
    assert sum("班次未匹配" in e for e in d["errors"]) == 1
    # 仅 1 条正常行入库
    assert db.query(Schedule).filter_by(person_id=p.id).count() == 1


def test_import_missing_columns(db, authed_client, tmp_path):
    """缺必填列(姓名/日期) → 400 + 留痕记录 status=failed。"""
    stored = _mk_xlsx(tmp_path, [{"人员": "张三", "班次": "早班"}])  # 无日期列
    r = authed_client.post("/api/v1/schedules/import", json={"stored_path": stored})
    assert r.status_code == 400
    assert "缺少必填列" in r.json()["detail"]
    rec = db.query(UploadRecord).filter_by(data_type="schedule").one()
    assert rec.status == "failed"
