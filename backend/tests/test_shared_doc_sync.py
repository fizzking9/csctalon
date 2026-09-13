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
        # #42 统一入库接口: 返回 IngestResult(extra 携带 external/card/log_id)
        from app.services.ingest_service import IngestResult
        return IngestResult(
            data_type="external_complaint+card_complaint", source="shared_doc", run_id="testrun",
            status="success", total=3, inserted=3, updated=0,
            extra={
                "log_id": 1,
                "external": {"total": 2, "inserted": 2, "updated": 0, "failed": 0,
                             "matched": 0, "filtered": 0, "warnings": 0, "errors": []},
                "card": {"total": 1, "inserted": 1, "updated": 0, "failed": 0,
                         "matched": 0, "filtered": 0, "warnings": 0, "errors": []},
            },
        )

    monkeypatch.setattr(sdss, "export_doc_xlsx", _fake_export)
    monkeypatch.setattr(sdss, "ingest_complaints", _fake_sync)

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
    # 来源区分: 统一入库接口以 source=shared_doc 标识(#42)
    assert captured["kwargs"]["source"] == "shared_doc"
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
        # #42 统一入库接口: 返回 IngestResult
        from app.services.ingest_service import IngestResult
        return IngestResult(
            data_type="manual_refund", source="shared_doc", run_id="testrun",
            status="success", total=1, inserted=1, updated=0, deleted=0, failed=0,
            extra={"log_id": 1, "warnings_count": 0},
        )

    monkeypatch.setattr(sdss, "export_doc_xlsx", _fake_export)
    monkeypatch.setattr(sdss, "ingest_manual_refunds", _fake_sync)

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


# ---- 读取层业务空行过滤(退款"只有日期"预留行, #33 完善) ----
def test_read_sheet_records_require_any_filters_only_date_rows(tmp_path):
    """require_any 指定业务字段后, 只有登记日期无业务字段的行被过滤。

    模拟金山登记明细行尾"只填登记日期、无订单号/设备号/金额/登记人"的预留空行:
    - 真实行(有订单号/设备号/登记人) → 保留
    - 只有登记日期的行 → 过滤(否则 manual_refund_sync 判缺订单号 failed)
    - 全空行 → 过滤(既有逻辑)
    """
    import pandas as pd

    xlsx = str(tmp_path / "doc_manual_filter.xlsx")
    with pd.ExcelWriter(xlsx, engine="openpyxl") as xw:
        pd.DataFrame(
            [
                {"登记日期": "2026-09-01", "设备号": "88000001", "订单号": "ORD1",
                 "商户ID": "M1", "订单金额": "100", "登记人": "客服A", "是否已退款": "是",
                 "退款金额": "100", "退款操作人": "客服A", "操作时间": "2026-09-01 10:00", "备注": ""},
                {"登记日期": "2026-09-08 00:00:00"},   # 只有日期(预留空行) → 应过滤
                {"登记日期": "2026-09-08 00:00:00"},   # 同上
                { },                                    # 全空 → 应过滤
            ]
        ).to_excel(xw, sheet_name="登记明细", index=False)
    # 用 _pick_manual_refund_sheet(带 require_any)
    recs = sdss._pick_manual_refund_sheet(xlsx, None)
    assert len(recs) == 1, f"期望只保留 1 行真实数据, 实际 {len(recs)}"
    assert recs[0]["订单号"] == "ORD1"
    # 直接测 _read_sheet_records 的 require_any
    recs2 = sdss._read_sheet_records(xlsx, "登记明细", require_any=("订单号", "设备号", "登记人", "订单金额"))
    assert len(recs2) == 1
    # 不指定 require_any → 只有日期的 2 行会保留(交给管线判 failed), 验证参数确实改变行为
    recs3 = sdss._read_sheet_records(xlsx, "登记明细")
    assert len(recs3) == 3  # 1 真实 + 2 只有日期(全空行仍被既有逻辑过滤)


def test_read_sheet_records_counters_reports_filtered_empty(tmp_path):
    """空行过滤计数上报(counters): 预留模板空行(只填日期无业务字段)被计。

    A-20260908-003 §2.4: 空模板行默认静默(仅计数不落库), 计数供 summary 展示。
    注: pandas read_excel 会把"全空白行"在读取层丢弃(不到 iterrows), 真正到过滤
    逻辑的是"部分填充"的预留模板行(如只填登记日期) → 计入 business_empty_rows。
    """
    import openpyxl

    xlsx = str(tmp_path / "doc_counters.xlsx")
    headers = ["登记日期", "设备号", "订单号", "商户ID", "订单金额", "登记人",
               "是否已退款", "退款金额", "退款操作人", "操作时间", "备注"]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "登记明细"
    ws.append(headers)
    ws.append(["2026-09-01", "88000001", "ORD1", "M1", "100", "客服A",
               "是", "100", "客服A", "2026-09-01 10:00", ""])
    ws.append(["2026-09-08 00:00:00"])  # 预留模板行(只填日期) → business_empty
    ws.append(["2026-09-09 00:00:00"])  # 预留模板行(只填日期) → business_empty
    wb.save(xlsx)

    counters: dict[str, int] = {}
    recs = sdss._pick_manual_refund_sheet(xlsx, None, counters=counters)
    assert len(recs) == 1
    assert counters.get("business_empty_rows") == 2  # 2 预留模板行被过滤并计数


# ─────────────────────────────────────────────────────────────────────
# 多 Sheet 合并 (#77, 2026-09-13 线上「D 路最近几天没数据」回归)
#   线上文档有 `登记明细`(历史全量, 到 09-11) + `9月份登记明细`(本月增量, 含 09-12/09-13),
#   两张表名字都含「登记明细」; 旧实现「取第一个命中就 return」→ 只读历史表,
#   月度表完全采不到 → D 路最近几天无数据, 且同步日志恒为 inserted=0/updated=0。
# ─────────────────────────────────────────────────────────────────────
def _make_two_sheet_doc(xlsx: str) -> None:
    """造一份与线上同构的文档: 历史表在前、月度表在后, 名字都含「登记明细」。"""
    import pandas as pd

    hist = [
        {"登记日期": "2026-09-10", "设备号": "SN001", "订单号": "ORD-A",
         "商户ID": "M1", "订单金额": "100", "登记人": "客服A",
         "是否已退款": "是", "退款金额": "100", "退款操作人": "客服B",
         "操作时间": "2026-09-10 10:00"},
        {"登记日期": "2026-09-11", "设备号": "SN002", "订单号": "ORD-B",
         "商户ID": "M1", "订单金额": "200", "登记人": "客服A",
         "是否已退款": "是", "退款金额": "200", "退款操作人": "客服B",
         "操作时间": "2026-09-11 10:00"},
    ]
    monthly = [
        # 与历史表同键同值(实测线上交集行逐字段一致)
        {"登记日期": "2026-09-11", "设备号": "SN002", "订单号": "ORD-B",
         "商户ID": "M1", "订单金额": "200", "登记人": "客服A",
         "是否已退款": "是", "退款金额": "200", "退款操作人": "客服B",
         "操作时间": "2026-09-11 10:00"},
        # ★ 仅月度表有: 09-12/09-13 —— 修复前完全采不到的部分
        {"登记日期": "2026-09-12", "设备号": "SN003", "订单号": "ORD-C",
         "商户ID": "M1", "订单金额": "300", "登记人": "客服C",
         "是否已退款": "是", "退款金额": "300", "退款操作人": "客服B",
         "操作时间": "2026-09-12 10:00"},
        {"登记日期": "2026-09-13", "设备号": "SN004", "订单号": "ORD-D",
         "商户ID": "M1", "订单金额": "400", "登记人": "客服C",
         "是否已退款": "否", "退款金额": "", "退款操作人": "",
         "操作时间": "2026-09-13 10:00"},
    ]
    with pd.ExcelWriter(xlsx, engine="openpyxl") as xw:
        pd.DataFrame(hist).to_excel(xw, sheet_name="登记明细", index=False)
        pd.DataFrame(monthly).to_excel(xw, sheet_name="9月份登记明细", index=False)


def test_pick_manual_refund_merges_all_detail_sheets(tmp_path):
    """🔒 回归: 必须合并**所有**「登记明细」类 sheet, 不能只取第一个。"""
    xlsx = str(tmp_path / "doc_multi_sheet.xlsx")
    _make_two_sheet_doc(xlsx)

    recs = sdss._pick_manual_refund_sheet(xlsx, None)
    dates = sorted({r["登记日期"][:10] for r in recs})
    assert dates == ["2026-09-10", "2026-09-11", "2026-09-12", "2026-09-13"], \
        f"未合并两张 sheet, 实际日期={dates}"


def test_pick_manual_refund_single_sheet_unchanged(tmp_path):
    """回归保护: 单 sheet 文档行为不变(合并逻辑不影响既有场景)。"""
    import pandas as pd

    xlsx = str(tmp_path / "doc_single.xlsx")
    with pd.ExcelWriter(xlsx, engine="openpyxl") as xw:
        pd.DataFrame([{
            "登记日期": "2026-09-01", "设备号": "SN1", "订单号": "O1",
            "商户ID": "M1", "订单金额": "10", "登记人": "甲",
            "是否已退款": "是", "退款金额": "10", "退款操作人": "乙",
            "操作时间": "2026-09-01",
        }]).to_excel(xw, sheet_name="登记明细", index=False)
    recs = sdss._pick_manual_refund_sheet(xlsx, None)
    assert len(recs) == 1 and recs[0]["订单号"] == "O1"


def test_pick_manual_refund_respects_sheet_rules(tmp_path):
    """显式 sheet_rules 时按其声明的 sheet 读(合并逻辑只作用于未配置的默认路径)。"""
    xlsx = str(tmp_path / "doc_multi_sheet3.xlsx")
    _make_two_sheet_doc(xlsx)

    recs = sdss._pick_manual_refund_sheet(xlsx, [{"sheet": "9月份登记明细"}])
    dates = sorted({r["登记日期"][:10] for r in recs})
    assert dates == ["2026-09-11", "2026-09-12", "2026-09-13"], f"实际={dates}"


def test_pick_manual_refund_merged_keys_dedupable(tmp_path):
    """合并后每笔业务只剩 1 个去重键(入库层复合键幂等, 同键行不会重复入库)。

    线上实测两表交集 735 行**逐字段一致** → 合并后按规范 §2.2 复合键
    (登记日期|订单号|设备号|登记人)去重不会产生重复行。
    """
    from app.services.upsert import _manual_dedup_key, _to_datetime

    xlsx = str(tmp_path / "doc_multi_sheet4.xlsx")
    _make_two_sheet_doc(xlsx)
    recs = sdss._pick_manual_refund_sheet(xlsx, None)

    # 读取层给的是字符串(dtype=str); 用入库层同一个 _to_datetime 转 aware datetime,
    # 保证测的是真实链路(naive 值直接进 _manual_dedup_key 会因缺 tzinfo 抛错)
    def _key(r):
        return _manual_dedup_key({
            "register_time": _to_datetime(r["登记日期"]), "order_no": r["订单号"],
            "device_sn": r["设备号"], "registrant": r["登记人"],
        })

    keys = [_key(r) for r in recs]
    # ORD-A/B/C/D = 4 笔不同业务 → 4 个唯一键(合并把两表数据都保住了)
    assert len(set(keys)) == 4, f"应 4 个业务键, 实际 {sorted(set(keys))}"
    # ORD-B 在两表各出现一次(2 行)但键相同 → 入库层合并为 1 行, 不产生重复
    b_keys = [_key(r) for r in recs if r["订单号"] == "ORD-B"]
    assert len(b_keys) == 2 and len(set(b_keys)) == 1, "同键重复行必须归一到同一键"


def test_manual_refund_sync_rejected_on_missing_order_no(db):
    """M 项: sync_manual_refunds_from_payload 缺订单号行(有业务值)落拒收表。

    A-20260908-003 / 用户决策 2026-09-09: 空行计数 + 失败行落拒收供质量页可查。
    有业务字段值但缺订单号/登记日期 → 真问题行落拒收(全空模板行不落)。
    """
    from sqlalchemy import func, select

    from app.models.ingest_rejected_row import IngestRejectedRow
    from app.services.manual_refund_sync import sync_manual_refunds_from_payload

    # 构造: 1 条真问题(有设备号/金额但缺订单号) + 1 条全空(模板, 不应落)
    payload = {
        "source_file": "test-reject.xlsx",
        "manual_refunds": [
            {"登记日期": "2026-09-01 10:00:00", "设备号": "88000001", "退款金额": "100",
             "登记人": "客服A"},   # 缺 order_no → validate_fail 落拒收
            {"登记日期": "2026-09-01 10:00:00"},   # 全空 → 不落
        ],
        "full_sync": False,
    }
    stats = sync_manual_refunds_from_payload(db, payload, source_file="test-reject.xlsx",
                                             sync_source="shared_doc")
    # 两行都缺订单号 → 2 dropped; 但只有"有业务值"的真问题行落拒收(全空/仅日期行=模板噪音不落)
    assert stats["failed"] == 2
    # 拒收表应恰好 1 条(source=shared_doc, category=validate_fail)
    n = db.scalar(select(func.count()).select_from(IngestRejectedRow).where(
        IngestRejectedRow.source == "shared_doc",
        IngestRejectedRow.data_type == "manual_refund",
    )) or 0
    assert n == 1, f"期望 1 条拒收, 实际 {n}"

