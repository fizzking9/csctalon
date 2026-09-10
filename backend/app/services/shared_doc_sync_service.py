"""共享文档在线采集同步服务 (#33, A-20260907-002)

目标: 把原先经 workbuddy 被动推送的两类文件(315投诉/卡商群投诉、商户号手机退款)
转为「共享文档在线采集」—— 每个采集源(doc_sync_sources 一行)对应一份共享文档:

  导出 xlsx(官方 UI + 登录态) → pandas(calamine) 读 Sheet → 中文表头 dict 行
  → 复用既有入库管线:
      complaint       → complaint_sync.sync_complaints_from_payload
                         (external_complaints / card_complaints, 按 sheet_rules 分 Sheet)
      manual_refund   → manual_refund_sync.sync_manual_refunds_from_payload
                         (manual_refunds, ⚠️ full_sync 必须 false——在线采集是增量 upsert,
                          全量对账会删除库中不在文档内的行, 见记忆 shared-doc-fullsync-lesson)

浏览器导出逻辑封装自 scripts/tencent_doc_export.py / scripts/kdocs_export.py:
- 登录态文件: settings.BROWSER_STATE_DIR / (tencent_doc.json | kdocs.json), 硬编码禁止
- 两者均为 headless 下已实测可行的 playwright sync 导出流程
"""
from __future__ import annotations

import logging
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.doc_sync_source import DocSyncSource
from app.services.complaint_sync import sync_complaints_from_payload
from app.services.manual_refund_sync import sync_manual_refunds_from_payload

logger = logging.getLogger(__name__)

# ── 平台 / 目标类型常量 ──────────────────────────────────────────────
PLATFORM_TENCENT = "tencent"
PLATFORM_KDOCS = "kdocs"
TARGET_COMPLAINT = "complaint"
TARGET_MANUAL_REFUND = "manual_refund"

# 各平台默认登录态文件名(可用配置行 state_file 覆盖)
_DEFAULT_STATE_FILE = {PLATFORM_TENCENT: "tencent_doc.json", PLATFORM_KDOCS: "kdocs.json"}

# 腾讯 UI 导出坐标与菜单(与 scripts/tencent_doc_export.py 保持一致)
_TENCENT_MENU_XY = (1361, 15)
# 金山左上角菜单坐标(与 scripts/kdocs_export.py 保持一致)
_KDOCS_MENU_XY = (95, 15)

# xlsx 读取引擎(与 POC _poc_shared_doc_reader.py 一致: 快 + 免疫 WPS/腾讯导出样式崩溃)
_EXCEL_ENGINE = "calamine"

# 疑似"长编号被 Excel 数字精度截断"特征(#42 2026-09-10):
#   源单元格为「数字型」且长度 >=15 时, Excel/浮点只保留约 15 位有效数字,
#   超出部分被补 0(实测 1440306002024122303480530 → 14403060020241100.../尾串 00000)。
#   读取层无法修复(源已损坏), 但必须**可见**: 计数 + 告警, 供上游改为文本列。
_SUSPECT_PRECISION_RE = re.compile(r"^\d{15,}0{4,}$")


def _count_suspect_precision(df: "pd.DataFrame") -> dict[str, int]:
    """统计疑似受浮点精度影响的长数字单元格: {列名: 数量}(仅计数, 不改数据)

    判据: 纯数字、长度>=15、末尾连续>=4 个 0 —— 正常业务编号极少以多个 0 结尾,
    该形态高度指向「Excel 数字单元格 >15 位被截断」。
    """
    hits: dict[str, int] = {}
    for col in df.columns:
        s = df[col].astype(str)
        n = int(s.str.match(_SUSPECT_PRECISION_RE).sum())
        if n:
            hits[str(col)] = n
    return hits


def _export_tencent_xlsx(doc_url: str, out_path: str, state_filename: str | None = None) -> str:
    """用腾讯登录态打开共享表 → 更多菜单 → 导出为 → 本地Excel(.xlsx)

    state_filename: 登录态文件名(默认 tencent_doc.json; 支持配置行 state_file 覆盖)。
    返回下载建议文件名。无登录态或导出失败时抛出异常(由调用方记录状态)。
    """
    from playwright.sync_api import sync_playwright

    state_path = Path(settings.BROWSER_STATE_DIR) / (state_filename or _DEFAULT_STATE_FILE[PLATFORM_TENCENT])
    if not state_path.exists():
        raise FileNotFoundError(
            f"未找到腾讯登录态 {state_path}。请先运行 scripts/tencent_doc_login.py 扫码登录"
        )
    out = str(Path(out_path).resolve())
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            storage_state=str(state_path),
            viewport={"width": 1600, "height": 1000},
            accept_downloads=True,
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            ),
        )
        page = ctx.new_page()
        try:
            page.goto(doc_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(8000)  # 等 SPA + 表格渲染
            # 点击右上角 ⋯(更多)菜单
            page.mouse.click(*_TENCENT_MENU_XY)
            page.wait_for_timeout(1500)
            # hover「导出为」展开子菜单, 再点「本地Excel表格 (.xlsx)」
            page.hover("text=导出为")
            page.wait_for_timeout(800)
            with page.expect_download(timeout=90000) as dl_info:
                page.click("text=本地Excel表格 (.xlsx)")
            dl = dl_info.value
            dl.save_as(out)
            return dl.suggested_filename
        finally:
            browser.close()


def _export_kdocs_xlsx(doc_url: str, out_path: str, state_filename: str | None = None) -> str:
    """用金山登录态打开共享表 → 左上角菜单 → 导出为 → 导出为 Excel(.xlsx)

    state_filename: 登录态文件名(默认 kdocs.json; 支持配置行 state_file 覆盖)。
    返回下载建议文件名。无登录态或导出失败时抛出异常。
    """
    from playwright.sync_api import sync_playwright

    state_path = Path(settings.BROWSER_STATE_DIR) / (state_filename or _DEFAULT_STATE_FILE[PLATFORM_KDOCS])
    if not state_path.exists():
        raise FileNotFoundError(
            f"未找到金山登录态 {state_path}。请先运行 scripts/kdocs_login.py 扫码登录"
        )
    out = str(Path(out_path).resolve())
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            storage_state=str(state_path),
            viewport={"width": 1600, "height": 1000},
            accept_downloads=True,
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            ),
        )
        page = ctx.new_page()
        try:
            page.goto(doc_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(10000)
            # 1. 点左上角菜单图标
            page.mouse.click(*_KDOCS_MENU_XY)
            page.wait_for_timeout(1500)
            # 2. 精确定位"导出为"(排除 导出为PDF/图片/多维表格)
            clicked = page.evaluate(
                """() => {
                    const all=[...document.querySelectorAll('*')];
                    const targets = all.filter(e => (e.innerText||'').trim() === '导出为' && e.children.length===0);
                    if (!targets.length) return false;
                    targets[0].click();
                    return true;
                }"""
            )
            if not clicked:
                raise RuntimeError("菜单中未找到'导出为'入口(金山前端可能变动)")
            page.wait_for_timeout(1500)
            # 3. 点「导出为 Excel(.xlsx)」并捕获下载
            with page.expect_download(timeout=90000) as dl_info:
                page.click("text=导出为 Excel(.xlsx)", timeout=8000)
            dl = dl_info.value
            dl.save_as(out)
            return dl.suggested_filename
        finally:
            browser.close()


def export_doc_xlsx(platform: str, doc_url: str, out_path: str, state_filename: str | None = None) -> str:
    """按平台分发导出 xlsx, 返回下载建议文件名(供 source_file 展示)"""
    if platform == PLATFORM_TENCENT:
        return _export_tencent_xlsx(doc_url, out_path, state_filename)
    if platform == PLATFORM_KDOCS:
        return _export_kdocs_xlsx(doc_url, out_path, state_filename)
    raise ValueError(f"不支持的共享文档平台: {platform}")


# ── xlsx 读取层: Sheet → 中文表头 dict 行(参考 _poc_shared_doc_reader) ──
def _norm_header(h: Any) -> str:
    """表头归一化: 去首尾空白/全角空格/换行; 合并单元格产生的 Unnamed 视为无效"""
    if h is None:
        return ""
    s = str(h).strip().replace("\u3000", " ").replace("\n", "")
    if s.startswith("Unnamed:"):
        return ""
    return s


def _sheet_names(path: str) -> list[str]:
    """列出 xlsx 内所有 Sheet 名"""
    xl = pd.ExcelFile(path, engine=_EXCEL_ENGINE)
    return list(xl.sheet_names)


def _read_sheet_records(
    path: str, sheet_name: str, require_any: tuple[str, ...] | None = None,
    counters: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """读单个 Sheet → [ {中文表头: 值}, ... ]

    只负责取数(NaN→None, 丢全空列/空表头, **过滤全空行**), 不做字段映射/清洗
    —— 那是 complaint_sync / manual_refund_sync 内部的事。

    过滤全空行原因: 共享文档(尤其金山)常预留空白模板行(仅部分列/全空),
    若不滤掉会作为"缺登记日期/订单号"的脏行计入 failed, 污染同步统计与日志
    (实测金山登记明细 4244 行中含 1146 空行, 有效仅 ~3117)。

    require_any: 可选"业务字段集合"。指定后, 若某行这些字段**全空**(只有日期等
    非业务字段有值), 视为"业务空行"一并过滤 —— 用于退款型: 登记明细行尾常有
    "只填登记日期、无订单号/设备号/金额/登记人"的预留空行, 非真实数据, 应在读取
    层过滤(否则 manual_refund_sync 判"缺订单号" failed, 污染日志)。

    counters: 可选 dict, 用于统计被过滤的空行数(全空/业务空行), 汇入同步 summary
    展示 —— 空行过滤可见可查(A-20260908-003 / 用户决策 2026-09-09), 默认静默仅计数。
    """
    # ⚠️ #42(2026-09-10): 统一 dtype=str, 与 data_parser 读取层一致。
    #   理由: 长编号(订单号/投诉编号)以数字型导出时会被按 float 解析, >15 位精度丢失;
    #   显式 dtype=str 至少保证「文本型单元格」100% 原样读取(实测 647/647 一致),
    #   数字型无法挽救但由下方 _count_suspect_precision 计数暴露。
    df = pd.read_excel(path, sheet_name=sheet_name, header=0, engine=_EXCEL_ENGINE, dtype=str)
    # 精度可疑计数(可见化, 不改数据; #42)
    for _col, _n in _count_suspect_precision(df).items():
        logger.warning(
            f"[shared_doc] {path} sheet={sheet_name} 列「{_col}」有 {_n} 个疑似浮点精度"
            f"截断的长数字(>=15位且尾部多0); 上游应将该列设为文本格式"
        )
        if counters is not None:
            counters.setdefault("suspect_precision", {})[_col] = (
                counters.get("suspect_precision", {}).get(_col, 0) + _n
            )
    df = df.dropna(axis=1, how="all")  # 丢弃全空列(合并表头/末尾多余列)
    df.columns = [_norm_header(c) for c in df.columns]
    df = df.loc[:, [c for c in df.columns if c != ""]]
    records: list[dict[str, Any]] = []
    n_empty = 0
    n_biz_empty = 0
    for _, row in df.iterrows():
        rec = {str(col): (None if pd.isna(v) else v) for col, v in row.items()}
        # 过滤全空行(所有字段均为 None/空): 与 complaint_sync._is_empty_*_rec 同语义,
        # 避免空白模板行进入入库管线被判 dropped/failed
        if all(v is None or (isinstance(v, str) and v.strip() == "") for v in rec.values()):
            n_empty += 1
            continue
        # 业务空行过滤(仅当指定 require_any): 这些业务字段全空 → 非真实数据行
        if require_any:
            def _has_val(v: Any) -> bool:
                return v is not None and not (isinstance(v, str) and v.strip() == "")
            if not any(_has_val(rec.get(k)) for k in require_any):
                n_biz_empty += 1
                continue
        records.append(rec)
    if counters is not None:
        counters["empty_rows"] = counters.get("empty_rows", 0) + n_empty
        counters["business_empty_rows"] = counters.get("business_empty_rows", 0) + n_biz_empty
    return records


def _sheet_matches(sheet_name: str, keywords: tuple[str, ...]) -> bool:
    """Sheet 名是否含任一关键词(用于无 sheet_rules 时的自动匹配)"""
    s = sheet_name.strip().lower()
    return any(k.lower() in s for k in keywords)


def _pick_complaint_sheets(
    file_path: str, rules: list[dict] | None,
    counters: dict[str, int] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """从导出的 xlsx 里取 315/卡商 两个 Sheet 的数据

    - 有 sheet_rules: 按 [{'sheet': 名, 'target': 'external_complaint'|'card_complaint'}]
      逐条匹配(命中即读该 sheet)
    - 无 sheet_rules: 遍历所有非空 sheet, 按表头关键词自动匹配
      (315 表头含「投诉编号」, 卡商表头含「投诉群」)
    返回 {"external_complaint": [...], "card_complaint": [...]}, 未命中的为空列表。
    counters: 可选, 汇聚被过滤空行计数(供 summary 展示)。
    """
    ext_rules: list[str] = []
    card_rules: list[str] = []
    if rules:
        for r in rules:
            if not isinstance(r, dict):
                continue
            sheet = str(r.get("sheet") or "").strip()
            target = str(r.get("target") or "")
            if target == "external_complaint" and sheet:
                ext_rules.append(sheet)
            elif target == "card_complaint" and sheet:
                card_rules.append(sheet)

    sheets = _sheet_names(file_path)
    # 校验配置的 sheet 名确实存在(避免静默漏采)
    known = {s.strip() for s in sheets}
    for r in rules or []:
        if isinstance(r, dict) and str(r.get("sheet") or "").strip() and r["sheet"].strip() not in known:
            logger.warning(f"[shared_doc] sheet_rules 配置的 Sheet 不存在于导出文件: {r['sheet']}")

    def _collect(wanted: list[str], keywords: tuple[str, ...]) -> list[dict[str, Any]]:
        if wanted:
            recs: list[dict[str, Any]] = []
            for name in wanted:
                recs.extend(_read_sheet_records(file_path, name, counters=counters))
            return recs
        # 自动匹配: 遍历所有有数据的 sheet, Sheet 名或任一表头列名含关键词即命中
        # (315 表头列名含「投诉编号」, 卡商表头含「投诉群」)
        kw = tuple(k.lower() for k in keywords)
        for name in sheets:
            recs = _read_sheet_records(file_path, name, counters=counters)
            if not recs:
                continue
            if _sheet_matches(name, keywords):
                return recs
            headers = {str(k).strip().lower() for k in recs[0].keys()}
            if any(any(k in h for k in kw) for h in headers):
                return recs
        return []

    external = _collect(ext_rules, ("投诉编号",))
    card = _collect(card_rules, ("投诉群",))
    return {"external_complaint": external, "card_complaint": card}


def _pick_manual_refund_sheet(file_path: str, rules: list[dict] | None,
                              counters: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """取 manual_refund 目标 sheet 的数据

    优先 sheet_rules 里声明的 sheet(首个), 否则读「登记明细」(或第一张有数据的)。
    业务空行过滤(require_any): 只填登记日期、无 订单号/设备号/登记人/金额 的预留
    空行视为非真实数据, 在读取层过滤(failed 归零, 不污染日志)。
    counters: 可选, 汇聚被过滤空行计数(供 summary 展示)。
    """
    # 退款业务字段: 至少有其一才算真实登记行(登记日期 alone 不算)
    _BUSINESS_COLS = ("订单号", "设备号", "登记人", "订单金额", "退款金额", "商户ID", "退款操作人", "处理结果")
    if rules:
        for r in rules:
            if isinstance(r, dict) and str(r.get("sheet") or "").strip():
                return _read_sheet_records(file_path, r["sheet"].strip(),
                                           require_any=_BUSINESS_COLS, counters=counters)

    sheets = _sheet_names(file_path)
    for name in sheets:
        if "登记明细" in name:
            return _read_sheet_records(file_path, name, require_any=_BUSINESS_COLS, counters=counters)
    # 回退: 第一张有数据的 sheet
    for name in sheets:
        recs = _read_sheet_records(file_path, name, require_any=_BUSINESS_COLS, counters=counters)
        if recs:
            return recs
    return []


# ── 配置行读写 ───────────────────────────────────────────────────────
def _state_file_for(row: DocSyncSource) -> str:
    """解析登录态文件名: 配置行 state_file 优先, 否则按平台默认"""
    if row.state_file and row.state_file.strip():
        return row.state_file.strip()
    return _DEFAULT_STATE_FILE.get(row.platform, "")


def row_to_dict(row: DocSyncSource) -> dict[str, Any]:
    """ORM → 展示 dict(不暴露任何敏感字段; 本表本就不含凭证)"""
    return {
        "id": row.id,
        "platform": row.platform,
        "doc_url": row.doc_url,
        "doc_name": row.doc_name,
        "target_type": row.target_type,
        "sheet_rules": row.sheet_rules,
        "state_file": row.state_file,
        "enabled": row.enabled,
        "sync_interval_minutes": row.sync_interval_minutes,
        "last_sync_at": row.last_sync_at,
        "last_sync_status": row.last_sync_status,
        "last_sync_summary": row.last_sync_summary,
        "last_error": row.last_error,
    }


def list_doc_sources(db: Session) -> list[dict[str, Any]]:
    """列出所有共享文档采集源配置(enabled 在前的稳定顺序)"""
    rows = db.scalars(
        select(DocSyncSource).order_by(DocSyncSource.enabled.desc(), DocSyncSource.id)
    ).all()
    return [row_to_dict(r) for r in rows]


def get_doc_source(db: Session, source_id: int) -> DocSyncSource | None:
    """按 id 取采集源配置行(ORM 对象, 供同步使用)"""
    return db.get(DocSyncSource, source_id)


def due_doc_sources(db: Session) -> list[DocSyncSource]:
    """返回「到点需同步」的采集源(调度器轮询用)

    条件: enabled + 有 doc_url + 距上次同步 >= sync_interval_minutes(首次恒到点)。
    采集方式总开关(monitor)关闭时不轮询 —— 与 doc_sync_service.should_sync_tencent_doc
    共用 monitor 开关, 避免"投诉接入"关闭后共享文档仍在自动采集。
    """
    from datetime import timedelta

    from app.services.ingest_mode_service import is_ingest_mode_enabled

    if not is_ingest_mode_enabled(db, "monitor"):
        return []
    rows = db.scalars(
        select(DocSyncSource).where(DocSyncSource.enabled.is_(True)).order_by(DocSyncSource.id)
    ).all()
    now = datetime.now(timezone.utc)
    due: list[DocSyncSource] = []
    for r in rows:
        if not r.doc_url:
            continue
        if r.last_sync_at is None:
            due.append(r)  # 首次
            continue
        interval = max(r.sync_interval_minutes or 60, 5)
        if now >= r.last_sync_at + timedelta(minutes=interval):
            due.append(r)
    return due


# ── 配置 CRUD(供管理 API / 前端) ────────────────────────────────────
def save_doc_source(
    db: Session,
    data: dict,
    source_id: int | None = None,
) -> DocSyncSource:
    """新建/更新一个共享文档采集源配置。

    - source_id 为空 → 新建(platform/doc_url/target_type 必填; platform+doc_url 唯一冲突则报错)
    - source_id 有值 → 更新既有行(未传字段保持不变; 传了 platform/doc_url 需合法且不与其它行冲突)
    返回更新后的 ORM 行。
    """
    # 新建: 必填三项; 更新: 仅校验传入的字段
    if source_id is None:
        platform = str(data.get("platform") or "").strip()
        doc_url = str(data.get("doc_url") or "").strip()
        target_type = str(data.get("target_type") or "").strip()
        if platform not in (PLATFORM_TENCENT, PLATFORM_KDOCS):
            raise ValueError(f"platform 必须为 {PLATFORM_TENCENT}/{PLATFORM_KDOCS}: {platform!r}")
        if not doc_url:
            raise ValueError("doc_url(共享文档链接)不能为空")
        if target_type not in (TARGET_COMPLAINT, TARGET_MANUAL_REFUND):
            raise ValueError(f"target_type 必须为 {TARGET_COMPLAINT}/{TARGET_MANUAL_REFUND}: {target_type!r}")
        # 唯一冲突检查(先查后建, 避免新建行自匹配)
        dup = db.scalar(
            select(DocSyncSource).where(
                DocSyncSource.platform == platform,
                DocSyncSource.doc_url == doc_url,
            )
        )
        if dup:
            raise ValueError(f"已存在同平台同链接的采集源(id={dup.id}): {doc_url}")
        row = DocSyncSource(platform=platform, doc_url=doc_url, target_type=target_type)
        db.add(row)
    else:
        row = db.get(DocSyncSource, source_id)
        if row is None:
            raise ValueError(f"采集源不存在: id={source_id}")
        # 部分更新: 仅校验并应用传入的 platform/doc_url/target_type
        new_platform = str(data.get("platform") or "").strip() or row.platform
        if str(data.get("platform") or "").strip():
            platform = new_platform
            if platform not in (PLATFORM_TENCENT, PLATFORM_KDOCS):
                raise ValueError(f"platform 必须为 {PLATFORM_TENCENT}/{PLATFORM_KDOCS}: {platform!r}")
        new_doc_url = str(data.get("doc_url") or "").strip() or row.doc_url
        # W3: platform 或 doc_url 任一变更后, 一律按(最终 platform, 最终 doc_url)做唯一冲突预检(排除自身),
        #     避免只改 platform 时落到 DB 唯一约束抛 IntegrityError → 500(应在 API 层 400)
        if (str(data.get("platform") or "").strip() or str(data.get("doc_url") or "").strip()):
            dup = db.scalar(
                select(DocSyncSource).where(
                    DocSyncSource.platform == new_platform,
                    DocSyncSource.doc_url == new_doc_url,
                    DocSyncSource.id != row.id,
                )
            )
            if dup:
                raise ValueError(f"已存在同平台同链接的采集源(id={dup.id}): {new_doc_url}")
        row.platform = new_platform
        row.doc_url = new_doc_url
        target_type = str(data.get("target_type") or "").strip()
        if target_type:
            if target_type not in (TARGET_COMPLAINT, TARGET_MANUAL_REFUND):
                raise ValueError(f"target_type 必须为 {TARGET_COMPLAINT}/{TARGET_MANUAL_REFUND}: {target_type!r}")
            row.target_type = target_type

    # 通用字段(新建/更新都可传; 未传保持不变)
    if data.get("doc_name") is not None:
        row.doc_name = str(data.get("doc_name") or "").strip() or None
    if data.get("sheet_rules") is not None:
        sr = data.get("sheet_rules")
        row.sheet_rules = sr if isinstance(sr, list) else None
    if data.get("state_file") is not None:
        sf = str(data.get("state_file") or "").strip()
        row.state_file = sf or None
    if data.get("enabled") is not None:
        # W2: 仅接受真布尔, 拒绝 JSON 字符串 "false"(bool("false") is True 陷阱)
        v = data["enabled"]
        if not isinstance(v, bool):
            raise ValueError("enabled 必须是布尔值 true/false")
        row.enabled = v
    if data.get("sync_interval_minutes") is not None:
        row.sync_interval_minutes = max(int(data["sync_interval_minutes"]), 5)
    db.commit()
    db.refresh(row)
    logger.info(f"[shared_doc] 配置 {'新建' if source_id is None else '更新'} 源 #{row.id}: "
                f"{row.platform} {row.doc_name or row.doc_url} target={row.target_type}")
    return row


def delete_doc_source(db: Session, source_id: int) -> None:
    """删除采集源配置(物理删除; 同步日志/历史数据保留在业务表)"""
    row = db.get(DocSyncSource, source_id)
    if row is None:
        raise ValueError(f"采集源不存在: id={source_id}")
    logger.info(f"[shared_doc] 删除采集源 #{row.id}: {row.platform} {row.doc_name or row.doc_url}")
    db.delete(row)
    db.commit()


# ── 单源同步主流程 ───────────────────────────────────────────────────
def sync_one_source(db: Session, row: DocSyncSource, trigger: str = "manual") -> dict[str, Any]:
    """同步单个共享文档采集源 → 返回统计 dict

    流程:
      1. 导出 xlsx 到临时目录(临时文件用完即清; 用 TemporaryDirectory 无论成败自动清理)
      2. pandas(calamine) 读 target_type 对应 sheet(s) → 中文表头 dict 行
      3. complaint: 按 sheet_rules 分 sheet 组装 payload →
                    sync_complaints_from_payload(指纹去重 upsert)
         manual_refund: 读「登记明细」/首个有数据 sheet → payload full_sync:false →
                    sync_manual_refunds_from_payload(增量 upsert, 严禁全量对账)
      4. 更新 row.last_sync_at/status/summary/error(成功记 success/partial + 行数统计;
         失败记 failed + error)并返回统计

    失败时: 状态记录后仍向上抛出(供路由层展示具体错误)。
    """
    # 配置错误(停用/缺链接)不启动同步, 直接抛, 不覆盖既有同步状态
    if not row.enabled:
        raise ValueError(f"采集源已停用(platform={row.platform}, id={row.id})")
    if not row.doc_url:
        raise ValueError(f"采集源缺少 doc_url(id={row.id})")

    try:
        with tempfile.TemporaryDirectory(prefix="shared_doc_") as tmpdir:
            # 临时导出文件: playwright 下载需要以 .xlsx 结尾的绝对路径
            out_path = str(Path(tmpdir) / f"doc_{row.platform}.xlsx")
            stats = _do_sync(db, row, out_path, trigger)
    except Exception as e:  # noqa: BLE001
        # 失败状态写回配置行(时间 UTC), 然后向上抛出
        try:
            _record_status(db, row, "failed", None, str(e)[:2000])
        except Exception as rec_err:  # noqa: BLE001
            logger.error(f"[shared_doc] 失败状态写回失败(不影响错误抛出): {rec_err}")
        raise
    # 成功: 入库状态(complaint 可能 partial) + 行数统计写回配置行
    _record_status(db, row, stats["status"], _summary_from_stats(row, stats), None)
    return stats


def _do_sync(db: Session, row: DocSyncSource, out_path: str, trigger: str) -> dict[str, Any]:
    """导出 → 解析 → 入库(不负责配置行状态写回, 由 sync_one_source 统一处理)"""
    source_file = row.doc_name or row.doc_url
    try:
        # 登录态文件: 配置行 state_file 优先, 否则按平台默认(见 _state_file_for)
        export_doc_xlsx(row.platform, row.doc_url, out_path, _state_file_for(row))
    except FileNotFoundError as e:
        # 登录态缺失: 属于配置/环境问题, 明确提示去扫码
        raise RuntimeError(
            f"共享文档导出失败: {e}。请先完成 {row.platform} 登录态(scripts/{row.platform}_doc_login.py)"
        ) from e
    logger.info(f"[shared_doc] {source_file} 导出成功, 开始解析入库")

    if row.target_type == TARGET_COMPLAINT:
        stats = _sync_complaint_from_file(db, row, out_path, source_file)
    elif row.target_type == TARGET_MANUAL_REFUND:
        stats = _sync_manual_refund_from_file(db, row, out_path, source_file)
    else:
        raise ValueError(f"不支持的 target_type: {row.target_type}")
    return stats


def _sync_complaint_from_file(
    db: Session, row: DocSyncSource, file_path: str, source_file: str
) -> dict[str, Any]:
    """读取投诉型文档的两个 sheet 并复用 complaint_sync 管线入库"""
    counters: dict[str, int] = {}
    sheets = _pick_complaint_sheets(file_path, row.sheet_rules, counters=counters)
    ext_rows = sheets["external_complaint"]
    card_rows = sheets["card_complaint"]
    if not ext_rows and not card_rows:
        raise RuntimeError("导出的表格中未匹配到 315/卡商 任一投诉 Sheet(无数据可入库)")

    payload: dict[str, Any] = {
        "source_file": source_file,
        "external_complaints": ext_rows,
        "card_complaints": card_rows,
    }
    # sync_source 用 shared_doc 与 workbuddy push 区分(complain_sync_logs.sync_source 展示)
    stats = sync_complaints_from_payload(
        db, payload, source_file=source_file, sync_source="shared_doc"
    )
    # 空行计数上报(默认静默仅计数, A-20260908-003 §2.4)
    stats["filtered_empty_rows"] = counters.get("empty_rows", 0)
    # #42: 长数字精度可疑计数上报(可见化; 上游应把编号列改文本格式)
    if counters.get("suspect_precision"):
        stats["suspect_precision"] = counters["suspect_precision"]
    logger.info(
        f"[shared_doc] {source_file} 投诉入库完成: "
        f"315={stats['external']['total']}(+{stats['external']['inserted']}/~{stats['external']['updated']}) "
        f"卡商={stats['card']['total']}(+{stats['card']['inserted']}/~{stats['card']['updated']}) "
        f"滤空行={counters.get('empty_rows', 0)} 精度可疑={counters.get('suspect_precision') or '无'} status={stats['status']}"
    )
    return stats


def _sync_manual_refund_from_file(
    db: Session, row: DocSyncSource, file_path: str, source_file: str
) -> dict[str, Any]:
    """读取退款型文档的登记明细 sheet 并复用 manual_refund_sync 管线入库

    ⚠️ full_sync 固定 false: 在线采集=增量 upsert, 全量对账会删除库中不在
    文档内的行(文档可能只是其中一部分来源, 误删血泪教训, 见 shared-doc-fullsync-lesson)。
    """
    counters: dict[str, int] = {}
    rows = _pick_manual_refund_sheet(file_path, row.sheet_rules, counters=counters)
    if not rows:
        raise RuntimeError("导出的表格中未匹配到退款登记明细 Sheet(无数据可入库)")

    payload: dict[str, Any] = {
        "source_file": source_file,
        "manual_refunds": rows,
        "full_sync": False,  # 增量 upsert, 严禁全量对账删除
    }
    stats = sync_manual_refunds_from_payload(
        db, payload, source_file=source_file, sync_source="shared_doc"
    )
    # 空行/业务空行计数上报(默认静默仅计数, A-20260908-003 §2.4)
    stats["filtered_empty_rows"] = counters.get("empty_rows", 0)
    stats["filtered_business_empty_rows"] = counters.get("business_empty_rows", 0)
    # #42: 长数字精度可疑计数上报(可见化)
    if counters.get("suspect_precision"):
        stats["suspect_precision"] = counters["suspect_precision"]
    logger.info(
        f"[shared_doc] {source_file} 退款入库完成: "
        f"{stats['total']}(+{stats['inserted']}/~{stats['updated']}/!{stats['failed']}) "
        f"滤空行={counters.get('empty_rows', 0)}/{counters.get('business_empty_rows', 0)} "
        f"精度可疑={counters.get('suspect_precision') or '无'} "
        f"status={stats['status']}"
    )
    return stats


def _record_status(db: Session, row: DocSyncSource, status: str, summary: dict | None, error: str | None) -> None:
    """把同步结果写回配置行(时间统一 UTC 存储)"""
    row.last_sync_at = datetime.now(timezone.utc)
    row.last_sync_status = status
    row.last_sync_summary = summary
    row.last_error = error
    db.commit()


def sync_doc_source_safe(source_id: int, trigger: str = "manual") -> dict[str, Any] | None:
    """独立 session 的安全入口: 失败不抛出(调度器/手动触发用)

    仿 doc_sync_service.sync_tencent_doc_safe:
      - sync_one_source 内部已负责把成功/失败状态写回配置行
      - 本入口只在极端异常(如源不存在/状态写回也失败)时兜底返回 None,
        绝不向调度器抛异常
    """
    db = SessionLocal()
    try:
        row = db.get(DocSyncSource, source_id)
        if row is None:
            logger.error(f"[shared_doc] 采集源不存在: id={source_id}")
            return None
        stats = sync_one_source(db, row, trigger=trigger)
        logger.info(
            f"[shared_doc] 源 #{source_id}({row.doc_name or row.doc_url}) {trigger} 同步成功: "
            f"status={stats['status']}"
        )
        return stats
    except Exception as e:  # noqa: BLE001  安全入口兜底, 不抛出
        logger.exception(f"[shared_doc] 源 #{source_id} {trigger} 同步失败(已记录): {e}")
        return None
    finally:
        db.close()


def _summary_from_stats(row: DocSyncSource, stats: dict[str, Any]) -> dict[str, Any]:
    """把入库统计压缩为配置行 last_sync_summary(JSONB 友好)"""
    if row.target_type == TARGET_COMPLAINT:
        s = {
            "external": {k: stats["external"][k] for k in ("total", "inserted", "updated", "failed", "matched")},
            "card": {k: stats["card"][k] for k in ("total", "inserted", "updated", "failed", "matched")},
        }
        if stats.get("filtered_empty_rows"):
            s["filtered_empty_rows"] = stats["filtered_empty_rows"]  # 滤空行计数(默认静默仅计数)
        if stats.get("suspect_precision"):
            s["suspect_precision"] = stats["suspect_precision"]  # #42 长数字精度可疑(上游需改文本列)
        return s
    if row.target_type == TARGET_MANUAL_REFUND:
        s = {k: stats[k] for k in ("total", "inserted", "updated", "deleted", "failed", "warnings")}
        for k in ("filtered_empty_rows", "filtered_business_empty_rows"):
            if stats.get(k):
                s[k] = stats[k]
        if stats.get("suspect_precision"):
            s["suspect_precision"] = stats["suspect_precision"]
        return s
    return {"total": stats.get("total")}
