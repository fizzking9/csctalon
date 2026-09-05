# -*- coding: utf-8 -*-
"""上传下载端点测试(2026-09-05, #20)

新增能力(补"文件可实际下载"缺口):
1. GET /uploads/template?data_type=xxx  → 下载 CSV 导入模板(表头=建议列)
2. GET /uploads/file?record_id=n        → 下载已上传/采集的原始文件

覆盖:
- template: 合法类型返回 CSV + BOM + 正确列头; 非法类型 400
- file: 存在记录返回原文件(200); 不存在记录 404
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def admin_client(_engine):
    from fastapi.testclient import TestClient

    from app.api.deps import get_current_user
    from app.core.database import get_db
    from app.main import app
    from app.models.user import RoleEnum, User

    fake_user = User(id=1, username="admin", role=RoleEnum.ADMIN.value, is_active=True)

    def _override_user():
        return fake_user

    def _override_db():
        from sqlalchemy.orm import Session

        session = Session(_engine, autocommit=False, autoflush=False)
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_db] = _override_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def test_template_download_refund_record_report(admin_client):
    """refund_record_report 模板下载: 200 + text/csv + 关键列头(含退款状态/退款时间)"""
    resp = admin_client.get("/api/v1/uploads/template", params={"data_type": "refund_record_report"})
    assert resp.status_code == 200
    assert "text/csv" in resp.headers.get("content-type", "")
    # UTF-8 BOM
    body = resp.content
    assert body.startswith(b"\xef\xbb\xbf")
    text = body.decode("utf-8-sig")
    # 模板表头应含该类型建议列(退款时间 / 退款状态 / 退款金额)
    assert "退款时间" in text or "退款完成时间" in text
    assert "退款金额" in text or "实退金额" in text


def test_template_download_bad_type(admin_client):
    """非法 data_type → 400"""
    resp = admin_client.get("/api/v1/uploads/template", params={"data_type": "not_exist_xxx"})
    assert resp.status_code == 400


def test_file_download_missing_record(admin_client):
    """记录不存在 → 404"""
    resp = admin_client.get("/api/v1/uploads/file", params={"record_id": 99999999})
    assert resp.status_code == 404


def test_file_download_ok(admin_client, _engine, tmp_path, monkeypatch):
    """已存在的 UploadRecord → 200 返回原始文件内容"""
    from sqlalchemy.orm import Session

    from app.core.config import settings
    from app.models.upload import UploadRecord
    from app.services.data_parser import COLUMN_TEMPLATES

    # 造一个真实落盘文件(monkeypatch UPLOAD_DIR 到 tmp)
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    fdir = tmp_path / "2026-09"
    fdir.mkdir(parents=True, exist_ok=True)
    fpath = fdir / "样本.csv"
    content = "退款时间,退款状态,退款金额\n2026-09-05 10:00:00,退款成功,19.00\n"
    fpath.write_text(content, encoding="utf-8")

    session = Session(_engine, autocommit=False, autoflush=False)
    try:
        rec = UploadRecord(
            data_type="refund_record_report",
            filename="样本.csv",
            stored_path="2026-09/样本.csv",
            file_size=fpath.stat().st_size,
            status="imported",
            uploaded_by="admin",
            source="bms",
        )
        session.add(rec)
        session.commit()
        session.refresh(rec)
        rec_id = rec.id
    finally:
        session.close()

    resp = admin_client.get("/api/v1/uploads/file", params={"record_id": rec_id})
    assert resp.status_code == 200
    assert resp.content.decode("utf-8") == content
    # 非 ASCII 文件名经 RFC5987 编码: filename*=utf-8''%E6%A0%B7%E6%9C%AC.csv
    cd = resp.headers.get("content-disposition", "")
    assert "attachment" in cd and "filename" in cd
    assert "%E6%A0%B7%E6%9C%AC.csv" in cd  # "样本.csv" 的 percent-encoded 形式
