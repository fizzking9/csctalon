"""workbuddy 投诉数据推送同步服务

workbuddy 每 20 分钟检查文件(315投诉处理登记表.xlsx 等),文件变化时将
两个 Sheet 的明细以 JSON 推送过来,本服务负责:

1. 数据清洗: 列名映射(兼容中文/英文字段)、SN 清理(多SN取首/去括号备注)、
   日期时区(北京时间→UTC)、姓名/金额字段防御性校验、长度截断
2. 业务去重: 计算 dedup_hash 身份指纹(编号|日期|手机号|SN|问题),
   按指纹 upsert,防止 20 分钟重复推送产生脏数据
3. SN 关联工单: 按 device_sn 匹配 service_tickets 中的外部投诉类工单,
   写回 matched_ticket_no / matched_at(核心匹配:SN,一个SN可对应多张工单)
4. 同步日志: 每次推送写入 complaint_sync_logs,记录清洗/去重/匹配/报错统计
"""

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.card_complaint import CardComplaint
from app.models.complaint_sync_log import ComplaintSyncLog
from app.models.external_complaint import ExternalComplaint
from app.models.ticket import ServiceTicket
from app.services.analytics import _complaint_source_type_case
from app.services.data_parser import COLUMN_TEMPLATES
from app.services.upsert import (
    _sanitize_date,
    _sanitize_name_field,
    _sync_devices,
    _to_datetime,
    _to_float,
    _to_str,
    _truncate_str,
)

logger = logging.getLogger(__name__)

# 有效投诉编号: 数字/字母/连字符, 长度 6~64(过滤 "/"、"1.4403020020251128E+24" 等垃圾值)
_VALID_COMPLAINT_NO_RE = re.compile(r"^[0-9A-Za-z\-]{6,64}$")

# SN 单元格可能包含多个 SN(换行/逗号分隔)或备注(如 "18870605701（移动流量卡）"),统一取首个有效 SN
_SN_SPLIT_RE = re.compile(r"[\n,;，;、\s]+")
_SN_ANNOTATION_RE = re.compile(r"[（(].*?[)）]")


class SyncRowResult:
    """单表同步结果统计

    - dropped  : 硬失败(整行被丢弃, 如行解析异常/非对象) → 影响同步状态(partial)
    - warnings : 软警告(行保留, 但某字段被清洗, 如姓名列填了手机号 → 置空) → 仅记录, 不影响状态
    """

    def __init__(self) -> None:
        self.total = 0
        self.inserted = 0
        self.updated = 0
        self.dropped = 0
        self.warnings = 0
        self.matched = 0
        self.errors: list[dict[str, Any]] = []

    def add_warning(self, row_idx: int, msg: str, data: dict | None = None) -> None:
        """软警告: 行保留但字段被清洗"""
        self.warnings += 1
        self.errors.append({"row": row_idx, "level": "warning", "message": msg, "data": data})

    def add_error(self, row_idx: int, msg: str, data: dict | None = None) -> None:
        """兼容 upsert._sanitize_name_field 的软警告回调(该回调不会丢弃行)"""
        self.add_warning(row_idx, msg, data)

    def add_dropped(self, row_idx: int, msg: str, data: dict | None = None) -> None:
        """硬失败: 整行被丢弃"""
        self.dropped += 1
        self.errors.append({"row": row_idx, "level": "error", "message": msg, "data": data})


def _clean_sn(raw: Any) -> str | None:
    """清理设备 SN:
    - 去括号备注(如 188****5701（移动流量卡） → 188****5701)
    - 多SN(换行/逗号/分号分隔)取第一个
    - 统一去首尾空白
    """
    s = _to_str(raw)
    if not s:
        return None
    s = _SN_ANNOTATION_RE.sub("", s)
    parts = _SN_SPLIT_RE.split(s)
    for p in parts:
        p = p.strip()
        # 过滤明显不是SN的片段(纯中文/过短/过长)
        if not p or len(p) < 6 or len(p) > 64:
            continue
        if re.search(r"[\u4e00-\u9fff]", p):
            continue
        return p
    return None


def _is_valid_complaint_no(s: str | None) -> bool:
    if not s:
        return False
    return bool(_VALID_COMPLAINT_NO_RE.match(s))


def _norm_text(val: Any) -> str | None:
    """通用文本:去首尾空白/\\n,空转 None"""
    s = _to_str(val)
    if not s:
        return None
    return s.replace("\r", "").strip() or None


def _dedup_hash(parts: list[str]) -> str:
    """身份指纹: 拼接规范化的身份字段, md5 前 32 位"""
    key = "|".join((p or "").strip() for p in parts)
    return hashlib.md5(key.encode("utf-8")).hexdigest()[:32]


def _map_row(row: dict[str, Any], data_type: str) -> dict[str, Any]:
    """将原始行(中文列名或英文字段名)映射为模型字段名

    优先用 COLUMN_TEMPLATES 的中文候选列名; 同时兼容调用方直接给英文字段名。
    """
    out: dict[str, Any] = {}
    template = COLUMN_TEMPLATES.get(data_type, {})
    # 收集可用中文列名(小写化用于匹配)
    row_keys = {str(k).strip(): v for k, v in row.items()}
    for target_field, candidates in template.items():
        # 1) 直接命中英文字段名
        if target_field in row_keys:
            out[target_field] = row_keys[target_field]
            continue
        # 2) 中文候选列名精确匹配
        found = False
        for cand in candidates:
            for rk, rv in row_keys.items():
                if rk == cand:
                    out[target_field] = rv
                    found = True
                    break
            if found:
                break
    return out


def _clean_external_row(row: dict[str, Any], idx: int, result: SyncRowResult) -> dict[str, Any] | None:
    """清洗单条 315 外部投诉行 → 模型字段值"""
    data = _map_row(row, "external_complaint")
    rec: dict[str, Any] = {}
    try:
        # 投诉编号:剥离 \\n 前缀, 非法值(含"/"等垃圾)置空, 走指纹去重
        raw_no = data.get("complaint_no")
        complaint_no = _norm_text(str(raw_no).replace("\n", "")) if raw_no is not None else None
        if not _is_valid_complaint_no(complaint_no):
            complaint_no = None
        rec["complaint_no"] = complaint_no
        rec["complaint_date"] = _sanitize_date(_to_datetime(data.get("complaint_date")))
        rec["platform"] = _truncate_str(data.get("platform"), 64)
        rec["complaint_account"] = _truncate_str(data.get("complaint_account"), 128)
        rec["company_entity"] = _truncate_str(data.get("company_entity"), 128)
        rec["complaint_shop"] = _truncate_str(data.get("complaint_shop"), 128)
        rec["purchase_order_no"] = _truncate_str(data.get("purchase_order_no"), 128)
        rec["complainant"] = _sanitize_name_field(data.get("complainant"), idx, result, "315-投诉人")
        rec["complainant_phone"] = _truncate_str(data.get("complainant_phone"), 32)
        rec["device_sn"] = _clean_sn(data.get("device_sn"))
        rec["wechat_id"] = _truncate_str(data.get("wechat_id"), 128)
        rec["problem"] = _norm_text(data.get("problem"))
        rec["keyword"] = _truncate_str(data.get("keyword"), 128)
        rec["complaint_category"] = _truncate_str(data.get("complaint_category"), 64)
        rec["solution"] = _truncate_str(data.get("solution"), 512)
        rec["result"] = _truncate_str(data.get("result"), 64)
        rec["follower"] = _sanitize_name_field(data.get("follower"), idx, result, "315-跟进人")
        rec["handle_time"] = _sanitize_date(_to_datetime(data.get("handle_time")))
        rec["handle_detail"] = _norm_text(data.get("handle_detail"))
    except Exception as e:
        result.add_dropped(idx, f"315行解析失败: {e}")
        return None
    return rec


def _clean_card_row(row: dict[str, Any], idx: int, result: SyncRowResult) -> dict[str, Any] | None:
    """清洗单条 卡商群投诉行 → 模型字段值"""
    data = _map_row(row, "card_complaint")
    rec: dict[str, Any] = {}
    try:
        rec["complaint_date"] = _sanitize_date(_to_datetime(data.get("complaint_date")))
        rec["complaint_group"] = _truncate_str(data.get("complaint_group"), 64)
        rec["complainant"] = _sanitize_name_field(data.get("complainant"), idx, result, "卡商-投诉人")
        rec["complainant_phone"] = _truncate_str(data.get("complainant_phone"), 32)
        rec["device_sn"] = _clean_sn(data.get("device_sn"))
        rec["problem"] = _norm_text(data.get("problem"))
        rec["problem_category"] = _truncate_str(data.get("problem_category"), 64)
        rec["result"] = _truncate_str(data.get("result"), 128)
        rec["solution"] = _truncate_str(data.get("solution"), 128)
        rec["handler"] = _sanitize_name_field(data.get("handler"), idx, result, "卡商-处理人")
        rec["handle_result"] = _norm_text(data.get("handle_result"))
    except Exception as e:
        result.add_dropped(idx, f"卡商行解析失败: {e}")
        return None
    return rec


def _sn_to_ticket_map(db: Session, sns: set[str]) -> dict[str, list[str]]:
    """按 SN 建立 → 外部投诉类工单号列表(核心匹配:SN,一个SN可对应多张工单)

    用 _complaint_source_type_case() == '外部投诉' 筛选(工信部/12315/消协/监督局等平台投诉)。
    每张工单取最早登记的作为代表关联(matched_ticket_no), 完整关联在明细查询时按SN合并。
    """
    if not sns:
        return {}
    stmt = (
        select(ServiceTicket.device_sn, ServiceTicket.ticket_no, ServiceTicket.register_time)
        .where(
            ServiceTicket.device_sn.in_(sns),
            _complaint_source_type_case() == "外部投诉",
        )
        .order_by(ServiceTicket.register_time.asc().nullslast())
    )
    result: dict[str, list[str]] = {}
    for sn, tno, _rt in db.execute(stmt).all():
        result.setdefault(sn, []).append(tno)
    return result


def _identity_dedup_hash(rec: dict[str, Any]) -> str:
    """身份指纹: md5(投诉日期 | 投诉手机号 | 设备SN | 归一化投诉问题)

    2026-08-20 指纹方案重规划(与 upsert._complaint_identity_hash 同一口径):
    - 编号不入指纹: 上游 workbuddy 推送会丢失/改写(实测 53%), 纳入会导致同一投诉拆两条
    - 问题以归一化签名入指纹: 区分"同手机同SN同日期但不同投诉"(手机号→多SN/SN→多投诉
      等小概率误合并场景, 实测误合并仅15行), 且跨文件匹配仍 99.8%(文本微改不影响)
    """
    from app.services.upsert import _complaint_problem_signature

    date = rec.get("complaint_date")
    date_s = date.strftime("%Y-%m-%d") if date else ""
    return _dedup_hash([
        date_s,
        rec.get("complainant_phone"),
        rec.get("device_sn"),
        _complaint_problem_signature(rec.get("problem")),
    ])


def _upsert_external(db: Session, rows: list[dict], result: SyncRowResult, sn_ticket_map: dict[str, list[str]]) -> None:
    """315外部投诉 upsert: 按 dedup_hash 冲突更新(含关联工单字段)"""
    if not rows:
        return
    now = datetime.now(timezone.utc)
    now_dt = now
    for r in rows:
        r["dedup_hash"] = _identity_dedup_hash(r)
        # SN 关联工单(取最早一张作为代表)
        ticket_nos = sn_ticket_map.get(r.get("device_sn")) if r.get("device_sn") else None
        if ticket_nos:
            r["matched_ticket_no"] = ticket_nos[0]
            r["matched_at"] = now_dt
            result.matched += 1
        else:
            r["matched_ticket_no"] = None
            r["matched_at"] = None
    _batch_upsert(db, ExternalComplaint, rows, result)


def _upsert_card(db: Session, rows: list[dict], result: SyncRowResult, sn_ticket_map: dict[str, list[str]]) -> None:
    """卡商群投诉 upsert: 按 dedup_hash 冲突更新(含关联工单字段)"""
    if not rows:
        return
    now = datetime.now(timezone.utc)
    for r in rows:
        r["dedup_hash"] = _identity_dedup_hash(r)
        ticket_nos = sn_ticket_map.get(r.get("device_sn")) if r.get("device_sn") else None
        if ticket_nos:
            r["matched_ticket_no"] = ticket_nos[0]
            r["matched_at"] = now
            result.matched += 1
        else:
            r["matched_ticket_no"] = None
            r["matched_at"] = None
    _batch_upsert(db, CardComplaint, rows, result)


def _batch_upsert(db: Session, model, rows: list[dict], result: SyncRowResult) -> None:
    """按 dedup_hash 做 on_conflict_do_update

    同一批次内 dedup_hash 重复(文件里本身完全相同的行)保留最后一条,避免
    "ON CONFLICT DO UPDATE command cannot affect row a second time" 报错。
    分批写入(单批 <=500 行)避免单条 SQL 绑定参数超过 PG 65535 上限。
    """
    if not rows:
        return
    seen: dict[str, dict] = {}
    for r in rows:
        seen[r["dedup_hash"]] = r
    unique_rows = list(seen.values())
    dup_in_batch = len(rows) - len(unique_rows)
    if dup_in_batch:
        logger.info(f"[complaint_sync] {model.__tablename__} 批次内重复指纹 {dup_in_batch} 行(保留最后一条)")

    # 精确区分 insert/update: 先查已存在的指纹数(分批 in_ 查询避免超长)
    hashes = [r["dedup_hash"] for r in unique_rows]
    existing_count = 0
    for i in range(0, len(hashes), 2000):
        chunk = hashes[i:i + 2000]
        existing_count += (
            db.scalar(select(func.count()).select_from(model).where(model.dedup_hash.in_(chunk))) or 0
        )

    BATCH = 500
    for i in range(0, len(unique_rows), BATCH):
        batch = unique_rows[i:i + BATCH]
        stmt = pg_insert(model).values(batch)
        update_cols = {c: stmt.excluded[c] for c in batch[0] if c != "dedup_hash"}
        # 编号保护(仅315表): 推送行有编号才覆盖, 无编号保留库中已有编号。
        # 避免 workbuddy 数据编号缺失时, 同指纹 update 把已回填的编号清空。
        if model.__tablename__ == "external_complaints" and "complaint_no" in update_cols:
            update_cols["complaint_no"] = case(
                (stmt.excluded.complaint_no.isnot(None), stmt.excluded.complaint_no),
                else_=model.complaint_no,
            )
        stmt = stmt.on_conflict_do_update(index_elements=["dedup_hash"], set_=update_cols)
        db.execute(stmt)
        db.flush()
    result.inserted += max(len(unique_rows) - existing_count, 0)
    result.updated += min(existing_count, len(unique_rows))


def sync_complaints_from_payload(
    db: Session,
    payload: dict[str, Any],
    source_file: str | None = None,
    sync_source: str = "workbuddy",
) -> dict[str, Any]:
    """workbuddy JSON 推送主入口

    payload 结构:
    {
      "source_file": "315投诉处理登记表.xlsx",          # 可选
      "external_complaints": [ {...中文列名行...} ],   # 可选,监督局-315投诉 Sheet
      "card_complaints": [ {...中文列名行...} ],       # 可选,卡商群投诉 Sheet
    }
    行内字段名兼容 xlsx 中文列名与英文字段名。
    返回统计(与 complaint_sync_logs 记录一致)。
    """
    log = ComplaintSyncLog(source_file=source_file or "workbuddy-push", sync_source=sync_source, status="success")
    db.add(log)
    ext_result = SyncRowResult()
    card_result = SyncRowResult()
    try:
        ext_rows_raw = payload.get("external_complaints") or []
        card_rows_raw = payload.get("card_complaints") or []

        ext_rows: list[dict] = []
        for idx, row in enumerate(ext_rows_raw):
            if not isinstance(row, dict):
                ext_result.add_dropped(idx, "315行不是对象")
                continue
            rec = _clean_external_row(row, idx, ext_result)
            if rec is not None:
                ext_rows.append(rec)
        ext_result.total = len(ext_rows_raw)

        card_rows: list[dict] = []
        for idx, row in enumerate(card_rows_raw):
            if not isinstance(row, dict):
                card_result.add_dropped(idx, "卡商行不是对象")
                continue
            rec = _clean_card_row(row, idx, card_result)
            if rec is not None:
                card_rows.append(rec)
        card_result.total = len(card_rows_raw)

        # 收集需要关联工单的 SN(两张表共用,避免重复查)
        all_sns: set[str] = set()
        for r in ext_rows + card_rows:
            if r.get("device_sn"):
                all_sns.add(r["device_sn"])
        sn_ticket_map = _sn_to_ticket_map(db, all_sns)

        # 维护设备主表(缺失 SN 建空壳,避免外键失败)
        _sync_devices(ext_rows + card_rows, "device_sn", db)

        _upsert_external(db, ext_rows, ext_result, sn_ticket_map)
        _upsert_card(db, card_rows, card_result, sn_ticket_map)

        # 统计写日志(dropped=硬失败行, 影响状态; warnings=软警告仅记录)
        log.external_total = ext_result.total
        log.external_inserted = ext_result.inserted
        log.external_updated = ext_result.updated
        log.external_failed = ext_result.dropped
        log.external_matched = ext_result.matched
        log.card_total = card_result.total
        log.card_inserted = card_result.inserted
        log.card_updated = card_result.updated
        log.card_failed = card_result.dropped
        log.card_matched = card_result.matched

        all_errors = (ext_result.errors + card_result.errors)[:50]
        if all_errors:
            log.error = str(all_errors)
        if ext_result.dropped or card_result.dropped:
            log.status = "partial"
        else:
            log.status = "success"
        log.finished_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(log)

        logger.info(
            f"[complaint_sync] {source_file or 'workbuddy-push'} 完成: "
            f"315={ext_result.total}(+{ext_result.inserted}/~{ext_result.updated}/!{ext_result.dropped}/关联{ext_result.matched}) "
            f"卡商={card_result.total}(+{card_result.inserted}/~{card_result.updated}/!{card_result.dropped}/关联{card_result.matched})"
        )
        return {
            "log_id": log.id,
            "status": log.status,
            "external": {
                "total": ext_result.total,
                "inserted": ext_result.inserted,
                "updated": ext_result.updated,
                "failed": ext_result.dropped,
                "warnings": ext_result.warnings,
                "matched": ext_result.matched,
                "errors": ext_result.errors[:50],
            },
            "card": {
                "total": card_result.total,
                "inserted": card_result.inserted,
                "updated": card_result.updated,
                "failed": card_result.dropped,
                "warnings": card_result.warnings,
                "matched": card_result.matched,
                "errors": card_result.errors[:50],
            },
        }
    except Exception as e:
        db.rollback()
        log.status = "failed"
        log.error = str(e)[:2000]
        log.finished_at = datetime.now(timezone.utc)
        try:
            db.commit()
        except Exception:
            db.rollback()
        logger.exception(f"[complaint_sync] 推送失败: {e}")
        raise
