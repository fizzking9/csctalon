# -*- coding: utf-8 -*-
"""共享文档在线采集源测试 (#33, A-20260907-002)。

覆盖:
- doc_sync_sources 模型可建/可查(测试库由 conftest 的 create_all 建表)
- list_doc_sources / get_doc_source service 读写 / 默认 state_file 解析
- sync_one_source 异常路径: monkeypatch 导出抛错 → 抛异常且配置行记录
  last_sync_status=failed + last_error(不写真实导出/网络测试——需要登录态)。

说明: 测试库走 conftest create_all(不执行 migration seed), 故用例自建配置行;
      db fixture 全程在事务内, 测试结束自动回滚, 无需手工清理。
"""

import pytest

from app.models.doc_sync_source import DocSyncSource
from app.services import shared_doc_sync_service as sdss

SEED_URL_TENCENT = "https://docs.qq.com/sheet/TESTDOC000TENCENT001"
SEED_URL_KDOCS = "https://www.kdocs.cn/l/TESTKDOCS000001"


@pytest.fixture()
def seed_source(db):
    """造一条腾讯投诉采集源(测试库不走 migration seed, 手动插入)。"""
    row = DocSyncSource(
        platform=sdss.PLATFORM_TENCENT,
        doc_url=SEED_URL_TENCENT,
        doc_name="315投诉处理登记表新(测试)",
        target_type=sdss.TARGET_COMPLAINT,
        sheet_rules=[
            {"sheet": "监督局-315投诉", "target": "external_complaint"},
            {"sheet": "卡商群投诉", "target": "card_complaint"},
        ],
        enabled=True,
        sync_interval_minutes=60,
    )
    db.add(row)
    db.flush()
    return row


@pytest.fixture(autouse=True)
def _no_commit(db, monkeypatch):
    """service 内部 commit 变为 flush, 保持测试事务回滚隔离(见 test_gap_queue_service)。"""
    monkeypatch.setattr(db, "commit", db.flush)


# ── 模型存在性 ───────────────────────────────────────────────────────
def test_doc_sources_model_exists(db):
    """模型可建/可查: create_all 已建表, 插入一行后可回读。"""
    row = DocSyncSource(
        platform=sdss.PLATFORM_KDOCS,
        doc_url=SEED_URL_KDOCS,
        doc_name="商户号手机退款处理登记(测试)",
        target_type=sdss.TARGET_MANUAL_REFUND,
        enabled=True,
    )
    db.add(row)
    db.flush()
    got = db.get(DocSyncSource, row.id)
    assert got is not None
    assert got.platform == sdss.PLATFORM_KDOCS
    assert got.target_type == sdss.TARGET_MANUAL_REFUND
    assert got.enabled is True
    # JSONB 字段默认 None(未配置 sheet_rules)
    assert got.sheet_rules is None


# ── service 读取 ─────────────────────────────────────────────────────
def test_list_doc_sources_service(db, seed_source):
    """list_doc_sources 返回所有行; 展示 dict 字段齐全且不含敏感凭证字段。"""
    items = sdss.list_doc_sources(db)
    assert any(x["doc_url"] == SEED_URL_TENCENT for x in items)
    src = next(x for x in items if x["doc_url"] == SEED_URL_TENCENT)
    for k in (
        "id", "platform", "doc_url", "doc_name", "target_type", "sheet_rules",
        "state_file", "enabled", "sync_interval_minutes",
        "last_sync_at", "last_sync_status", "last_sync_summary", "last_error",
    ):
        assert k in src
    assert src["target_type"] == sdss.TARGET_COMPLAINT
    assert src["enabled"] is True
    # 本表不含凭证字段, 防御性断言
    assert "secret" not in " ".join(str(v) for v in src.values()).lower()


def test_get_doc_source_service(db, seed_source):
    """get_doc_source 按 id 取到 ORM 行; 不存在返回 None。"""
    row = sdss.get_doc_source(db, seed_source.id)
    assert row is not None
    assert row.doc_url == SEED_URL_TENCENT
    assert sdss.get_doc_source(db, 99999999) is None


def test_state_file_default(db, seed_source):
    """state_file 为空时按平台默认解析(tencent → tencent_doc.json)。"""
    assert seed_source.state_file is None
    assert sdss._state_file_for(seed_source) == "tencent_doc.json"


def test_platform_default_state_file_kdocs(db):
    """kdocs 平台默认 state_file = kdocs.json; 显式配置优先。"""
    row = DocSyncSource(
        platform=sdss.PLATFORM_KDOCS, doc_url=SEED_URL_KDOCS,
        target_type=sdss.TARGET_MANUAL_REFUND,
    )
    db.add(row)
    db.flush()
    assert sdss._state_file_for(row) == "kdocs.json"
    # 配置行显式 state_file 优先
    row.state_file = "my_state.json"
    assert sdss._state_file_for(row) == "my_state.json"


# ── 同步异常路径(不依赖真实导出/网络) ────────────────────────────────
def test_sync_one_source_failure_records_status(db, seed_source, monkeypatch):
    """导出抛错 → sync_one_source 向上抛出, 且配置行已记 failed + last_error。"""
    def _boom(*args, **kwargs):
        raise RuntimeError("模拟导出失败: 登录态过期")

    monkeypatch.setattr(sdss, "export_doc_xlsx", _boom)
    with pytest.raises(RuntimeError, match="模拟导出失败"):
        sdss.sync_one_source(db, seed_source, trigger="manual")

    # 配置行已记录失败状态(时间 UTC)
    db.refresh(seed_source)
    assert seed_source.last_sync_status == "failed"
    assert seed_source.last_sync_summary is None
    assert seed_source.last_sync_at is not None
    assert seed_source.last_error is not None
    assert "模拟导出失败" in seed_source.last_error


def test_sync_one_source_raises_on_disabled(db, seed_source):
    """停用源直接抛错且不触发导出(未开始同步, 不写 failed)。"""
    seed_source.enabled = False
    db.flush()
    with pytest.raises(ValueError, match="已停用"):
        sdss.sync_one_source(db, seed_source)
    # 配置错误前置校验 → 不应覆盖既有状态
    db.refresh(seed_source)
    assert seed_source.last_sync_status is None
    assert seed_source.last_error is None


# ── 成功路径(不依赖真实导出: mock export 生成本地 xlsx, 验证解析+payload) ──
def _write_fake_doc(tmp_path, filename="doc_tencent.xlsx"):
    """用 pandas 写一个模拟腾讯导出的 xlsx(315 + 卡商两个 sheet)。"""
    import pandas as pd

    p = tmp_path / filename
    with pd.ExcelWriter(p, engine="openpyxl") as xw:
        pd.DataFrame(
            [
                {"投诉编号": "SN202609010001", "投诉日期": "2026-09-01", "平台": "12315",
                 "投诉人": "张三", "投诉设备号": "88000001", "投诉问题": "充值未到账"},
                {"投诉编号": "SN202609010002", "投诉日期": "2026-09-01", "平台": "12315",
                 "投诉人": "李四", "投诉设备号": "88000002", "投诉问题": "流量异常"},
            ]
        ).to_excel(xw, sheet_name="监督局-315投诉", index=False)
        pd.DataFrame(
            [
                {"投诉日期": "2026-09-02", "投诉群": "卡商A群", "投诉人": "王五",
                 "设备号": "88000003", "投诉问题": "断网"},
            ]
        ).to_excel(xw, sheet_name="卡商群投诉", index=False)
    return str(p)


def test_sync_complaint_payload_assembled(db, seed_source, monkeypatch, tmp_path):
    """mock 导出(本地 xlsx)→ 按 sheet_rules 拆 315/卡商 → payload 正确 → 状态成功写回。"""
    xlsx = _write_fake_doc(tmp_path)
    captured = {}

    def _fake_export(platform, doc_url, out_path, state_filename=None):
        # 复制本地 xlsx 到 service 期望的导出位置
        import shutil

        shutil.copy(xlsx, out_path)
        return "315投诉处理登记表新(测试).xlsx"

    def _fake_sync(db, payload, **kwargs):
        captured["payload"] = payload
        captured["kwargs"] = kwargs
        return {
            "status": "success",
            "external": {"total": 2, "inserted": 2, "updated": 0, "failed": 0,
                         "matched": 0, "filtered": 0, "warnings": 0, "errors": []},
            "card": {"total": 1, "inserted": 1, "updated": 0, "failed": 0,
                     "matched": 0, "filtered": 0, "warnings": 0, "errors": []},
        }

    monkeypatch.setattr(sdss, "export_doc_xlsx", _fake_export)
    monkeypatch.setattr(sdss, "sync_complaints_from_payload", _fake_sync)

    stats = sdss.sync_one_source(db, seed_source, trigger="manual")
    # 成功 → 状态写回
    db.refresh(seed_source)
    assert seed_source.last_sync_status == "success"
    assert seed_source.last_sync_summary["external"]["total"] == 2
    assert seed_source.last_error is None

    # payload: 315 与卡商按 sheet 拆分正确
    pl = captured["payload"]
    assert len(pl["external_complaints"]) == 2
    assert len(pl["card_complaints"]) == 1
    assert pl["external_complaints"][0]["投诉编号"] == "SN202609010001"
    assert pl["card_complaints"][0]["投诉群"] == "卡商A群"
    # sync_source 区分来源
    assert captured["kwargs"]["sync_source"] == "shared_doc"
    assert stats["external"]["total"] == 2


def test_sync_manual_refund_full_sync_false(db, tmp_path, monkeypatch):
    """退款型: mock 导出 → 读「登记明细」→ payload full_sync=False(增量, 严禁全量对账)。"""
    import pandas as pd

    # 构造退款采集源
    row = DocSyncSource(
        platform=sdss.PLATFORM_KDOCS,
        doc_url="https://www.kdocs.cn/l/TESTKDOCS000001",
        doc_name="商户号手机退款(测试)",
        target_type=sdss.TARGET_MANUAL_REFUND,
        enabled=True,
    )
    db.add(row)
    db.flush()

    xlsx = str(tmp_path / "doc_kdocs.xlsx")
    with pd.ExcelWriter(xlsx, engine="openpyxl") as xw:
        pd.DataFrame(
            [
                {"登记日期": "2026-09-01", "设备号": "88000001", "订单号": "ORD1",
                 "商户ID": "M1", "订单金额": "100", "登记人": "客服A", "是否已退款": "是",
                 "退款金额": "100", "退款操作人": "客服A", "操作时间": "2026-09-01 10:00", "备注": ""},
            ]
        ).to_excel(xw, sheet_name="登记明细", index=False)
        # 干扰 sheet: 不应被误采
        pd.DataFrame({"无关": ["x"]}).to_excel(xw, sheet_name="统计", index=False)

    captured = {}

    def _fake_export(platform, doc_url, out_path, state_filename=None):
        import shutil

        shutil.copy(xlsx, out_path)
        return "商户号手机退款(测试).xlsx"

    def _fake_sync(db, payload, **kwargs):
        captured["payload"] = payload
        return {
            "status": "success", "total": 1, "inserted": 1, "updated": 0,
            "deleted": 0, "failed": 0, "filtered": 0, "warnings": 0, "errors": [],
        }

    monkeypatch.setattr(sdss, "export_doc_xlsx", _fake_export)
    monkeypatch.setattr(sdss, "sync_manual_refunds_from_payload", _fake_sync)

    stats = sdss.sync_one_source(db, row, trigger="manual")
    pl = captured["payload"]
    # ⚠️ 核心安全约束: 在线采集必须 full_sync=False
    assert pl["full_sync"] is False
    assert len(pl["manual_refunds"]) == 1
    assert int(pl["manual_refunds"][0]["设备号"]) == 88000001  # pandas 数字列读成 int
    assert stats["status"] == "success"


# ---- 配置 CRUD + 到点判断(调度器用) ----
def test_save_doc_source_create_and_update(db):
    """save_doc_source 新建→可读; 更新字段生效; 非法 platform/target 拒绝。"""
    row = sdss.save_doc_source(db, {
        "platform": sdss.PLATFORM_TENCENT,
        "doc_url": "https://docs.qq.com/sheet/CRUD0001",
        "doc_name": "CRUD测试",
        "target_type": sdss.TARGET_COMPLAINT,
        "sheet_rules": [{"sheet": "监督局-315投诉", "target": "external_complaint"}],
        "enabled": True,
        "sync_interval_minutes": 30,
    })
    assert row.id is not None
    got = sdss.get_doc_source(db, row.id)
    assert got.doc_name == "CRUD测试"
    assert got.target_type == sdss.TARGET_COMPLAINT
    assert got.sync_interval_minutes == 30
    # 更新
    sdss.save_doc_source(db, {"doc_name": "改名"}, source_id=row.id)
    db.refresh(got)
    assert got.doc_name == "改名"
    # 非法值
    import pytest
    with pytest.raises(ValueError):
        sdss.save_doc_source(db, {"platform": "bad", "doc_url": "x", "target_type": "t"})
    with pytest.raises(ValueError):
        sdss.save_doc_source(db, {"platform": "tencent", "doc_url": "", "target_type": "t"})


def test_save_doc_source_dup_rejected(db, seed_source):
    """同平台同链接重复新建 → 拒绝(唯一约束); 更新自身不冲突。"""
    import pytest
    with pytest.raises(ValueError, match="已存在同平台同链接"):
        sdss.save_doc_source(db, {
            "platform": sdss.PLATFORM_TENCENT,
            "doc_url": SEED_URL_TENCENT,
            "target_type": sdss.TARGET_COMPLAINT,
        })
    # 更新自身(seed_source 已是同 url)不应冲突
    sdss.save_doc_source(db, {"doc_name": "自身更新"}, source_id=seed_source.id)
    db.refresh(seed_source)
    assert seed_source.doc_name == "自身更新"


def test_delete_doc_source(db, seed_source):
    """删除采集源后不可再读取。"""
    sid = seed_source.id
    sdss.delete_doc_source(db, sid)
    assert sdss.get_doc_source(db, sid) is None
    import pytest
    with pytest.raises(ValueError, match="不存在"):
        sdss.delete_doc_source(db, sid)


def test_due_doc_sources_respects_interval(db, seed_source):
    """due_doc_sources: 首次恒到点; 同步后未到 interval 不到点; 到点后再到点。"""
    from datetime import datetime, timedelta, timezone
    # seed_source.last_sync_at 为空 → 首次到点
    due = sdss.due_doc_sources(db)
    assert any(x.id == seed_source.id for x in due)
    # 模拟刚同步过(now) → 未到 60min interval → 不到点
    seed_source.last_sync_at = datetime.now(timezone.utc)
    db.flush()
    due = sdss.due_doc_sources(db)
    assert not any(x.id == seed_source.id for x in due)
    # 改小 interval 且 last_sync 很早 → 到点
    seed_source.sync_interval_minutes = 5
    seed_source.last_sync_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    db.flush()
    due = sdss.due_doc_sources(db)
    assert any(x.id == seed_source.id for x in due)


def test_due_doc_sources_disabled_excluded(db, seed_source):
    """停用源不进到点集合; monitor 开关关闭时全部不到点。"""
    seed_source.enabled = False
    db.flush()
    due = sdss.due_doc_sources(db)
    assert not any(x.id == seed_source.id for x in due)
