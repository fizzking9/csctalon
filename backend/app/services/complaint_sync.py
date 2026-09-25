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

from sqlalchemy import case, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.card_complaint import CardComplaint
from app.models.complaint_sync_log import ComplaintSyncLog
from app.models.external_complaint import ExternalComplaint
from app.services.complaint_scope import QSTATUS_DUP_SN_BACKFILL
from app.services.data_parser import COLUMN_TEMPLATES
from app.services.ingest_validator import (
    summarize_severity,
    validate_card_complaint_row,
    validate_external_complaint_row,
)
from app.services.upsert import (
    _sanitize_date,
    _sanitize_name_field,
    _sync_devices,
    _to_datetime,
    _to_str,
    _truncate_str,
)

logger = logging.getLogger(__name__)

# 有效投诉编号: 数字/字母/连字符, 长度 6~64(过滤 "/"、"1.4403020020251128E+24" 等垃圾值)
_VALID_COMPLAINT_NO_RE = re.compile(r"^[0-9A-Za-z\-]{6,64}$")


def _write_rejected(
    db: Session,
    *,
    data_type: str,
    source: str,
    category: str,
    reason: str | None = None,
    raw_row: dict | None = None,
    row_no: int | None = None,
) -> bool:
    """A-20260908-003: 未采纳行落拒收表(不阻塞主流程, 用 savepoint 隔离失败)。

    写入失败仅记日志, 不影响投诉/退款同步主流程。
    返回: 是否成功写入拒收表(供调用方累计 rejected 计数; #42 审查修复)
    """
    try:
        with db.begin_nested():  # savepoint: 失败只回滚本条, 不污染主事务
            from app.models.ingest_rejected_row import IngestRejectedRow

            db.add(IngestRejectedRow(
                data_type=data_type,
                source=source,
                reject_category=category,
                reject_reason=reason,
                raw_row=raw_row,
                source_row_no=row_no,
                status="open",
            ))
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ingest_rejected] 写拒收行失败(忽略): data_type={data_type} err={e}")
        return False

# SN 单元格可能包含多个 SN(换行/逗号分隔)或备注(如 "18870605701（移动流量卡）"),统一取首个有效 SN
# ★ #64: 分隔符补 `/` 与 `|` —— 实测源文档有「一格填两个SN」形态
#   (如 `58812351022558/63272421032891`), 原正则漏了 `/` → 整串不匹配任何工单。
#   ⚠️ 与电话的 split_phones 同类问题(当时只修了电话, 漏了 SN)。
_SN_SPLIT_RE = re.compile(r"[\n,;，;、\s/|]+")
_SN_ANNOTATION_RE = re.compile(r"[（(].*?[)）]")
# ★ #64: 剥离 Excel 文本格式残留的前导单引号 / 双引号
#   实测 5 条形如 `'70032611013040` —— 因 `'` 前缀导致与工单 SN 永不匹配。
_SN_LEADING_QUOTE_RE = re.compile(r"^[\'\"\u2018\u2019\u201c\u201d]+")


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
        self.filtered = 0
        self.warnings = 0
        self.matched = 0
        self.errors: list[dict[str, Any]] = []
        # #42: 真正落拒收表(ingest_rejected_rows)的条数 —— 与 dropped 不同义:
        #   dropped=硬失败行数; rejected=其中成功写入拒收表、可按行人工判定的条数
        self.rejected = 0
        # #42: 入库前校验问题清单(只读 advisory)。
        # ⚠️ 必须与 errors 严格分离: errors=硬失败明细(对外契约 failed/errors 语义),
        #    validation=校验告警(不丢弃行); 混入 errors 会让 failed=0 时 errors 非空,
        #    破坏 workbuddy 对外契约与 complaint_sync_logs.error 语义。
        self.validation: list[dict[str, Any]] = []        # ★ #59: SN 后补治理计数
        #   claimed = 有SN的行命中库内「缺SN残影」→ UPDATE 而非新增(去重成功)
        #   skipped = 无SN残影遇已有完整行 → 跳过(不覆盖)
        self.sn_backfill_claimed = 0
        self.sn_backfill_skipped = 0
    def add_validation(self, row_idx: int, issue: dict) -> None:
        """记录一条入库前校验问题(不影响 errors/failed 计数)"""
        self.validation.append({"row": row_idx, **issue})

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
    - 多SN(换行/逗号/分号/**斜杠**/竖线分隔)取第一个   ★ #64 补 `/` `|`
    - **剥离 Excel 文本格式残留的引号前缀**(如 `'70032611013040`)  ★ #64
    - 统一去首尾空白

    ⚠️ #64 修复的两个实际缺陷(生产实测 7 条):
      · 前导单引号 5 条 —— Excel 把长数字当文本时自动加 `'`, 导出后残留
      · 一格两SN   2 条 —— 形如 `58812351022558/63272421032891`, 原分隔符漏 `/`

      两者都导致该 SN **永不匹配工单** → 虚假「文档有、工单无」空悬。
    """
    s = _to_str(raw)
    if not s:
        return None
    s = _SN_ANNOTATION_RE.sub("", s)
    parts = _SN_SPLIT_RE.split(s)
    for p in parts:
        # ★ 剥引号前缀(在 strip 之前, 因引号可能在空白后)
        p = _SN_LEADING_QUOTE_RE.sub("", p.strip()).strip()
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
        # #195 日期恢复：日期列为空时从文本字段补救
        if not rec["complaint_date"]:
            from app.services.complaint_date_pipeline import recover_complaint_date_for_row
            rec["complaint_date"] = recover_complaint_date_for_row(data, "external")
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
        # #195 日期恢复：日期列为空时从文本字段补救
        if not rec["complaint_date"]:
            from app.services.complaint_date_pipeline import recover_complaint_date_for_row
            rec["complaint_date"] = recover_complaint_date_for_row(data, "card")
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


def _is_empty_external_rec(rec: dict[str, Any]) -> bool:
    """判断 315 外部投诉行是否全空(无任何有效信息) → 应过滤, 不落库
    Excel 末尾常有多余空行, 同步时若不过滤会产生"全空记录"。"""
    keys = (
        "complaint_no", "complaint_date", "platform", "company_entity",
        "complaint_shop", "complainant", "complainant_phone", "device_sn",
        "problem", "complaint_category", "result", "follower",
    )
    return all((rec.get(k) is None or str(rec.get(k)).strip() == "") for k in keys)


def _is_empty_card_rec(rec: dict[str, Any]) -> bool:
    """判断 卡商群投诉行是否全空 → 应过滤, 不落库"""
    keys = (
        "complaint_date", "complaint_group", "complainant", "complainant_phone",
        "device_sn", "problem", "problem_category", "result", "handler",
    )
    return all((rec.get(k) is None or str(rec.get(k)).strip() == "") for k in keys)


def _sn_to_ticket_map(db: Session, sns: set[str]) -> dict[str, list[tuple[str, datetime]]]:
    """按 SN 建立 → **外部投诉类工单** (工单号, 登记时间) 列表（按登记时间升序）

    ★★ 2026-09-11 (#56) 口径收敛 —— 委托 `external_reconcile._sn_candidates` ★★

      原实现用 `_complaint_source_type_case() == '外部投诉'` **单独**筛候选,
      与对账侧各写一套 → 判据漂移风险。现统一委托 `_sn_candidates`, 保证
      「同步时挂的工单」与「对账时认的工单」**完全同一集合**。

      判据 = `category` 空白/='投诉'  **AND** `item` 命中平台关键词
        ⚠️ 实测 918 条已匹配中 586 条(63.8%) 曾挂到 `未领取体验套餐/开发票/
           网慢网卡` 这类**非投诉类工单** —— 它们是"同 SN 的处理过程关联",
           不是投诉经营登记, 会污染对账基数。

    返回结构保持不变: `{sn: [(ticket_no, register_time), ...]}` 升序,
    供调用方用 `_pick_nearest_ticket()` 按日期就近选代表(#47 语义)。
    """
    from app.services.external_reconcile import _sn_candidates

    return _sn_candidates(db, sns)


def _pick_nearest_ticket(
    candidates: list[tuple[str, datetime]] | None,
    ref_date: datetime | None,
) -> tuple[str | None, int | None]:
    """从候选工单中挑**登记时间最接近投诉日期**的一张作为代表 (#47)

    参数:
      candidates: [(ticket_no, register_time), ...]  (来自 `_sn_to_ticket_map`)
      ref_date:   补充表侧的 complaint_date

    返回:
      (ticket_no, 日期差天数)。无候选或无法比较 → (最早一张, None) / (None, None)

    设计说明:
      · ref_date 为空 → 退化为取最早登记(与原行为一致, 不引入随机性)
      · 有 ref_date → 取 |register_time - ref_date| 最小者
        (同距时取更早登记的, 保证结果稳定可复现)
    """
    if not candidates:
        return None, None
    if ref_date is None:
        return candidates[0][0], None

    ref = ref_date.replace(tzinfo=None) if ref_date.tzinfo else ref_date
    best: tuple[str, int] | None = None
    for tno, rt in candidates:
        if rt is None:
            continue
        cur = rt.replace(tzinfo=None) if rt.tzinfo else rt
        try:
            diff = abs((cur - ref).days)
        except (TypeError, AttributeError):
            continue
        if best is None or diff < best[1]:
            best = (tno, diff)
    if best is None:
        return candidates[0][0], None
    return best


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


# 校验问题 → quality_status 打标用的类别标签(前缀与 manual_refund 同口径 '[类别] ')
_QUALITY_RULE_LABEL = {
    "R-必填": "必填缺失",
    "R-时间": "时间异常",
    "R-枚举": "枚举脏值",
    "R-金额": "金额异常",
    "R-校验器": "校验异常",
}
# quality_note 列宽 String(512) —— 截断留余量, 避免 DB 报错
_QUALITY_NOTE_MAX = 500


def _tag_quality(rec: dict[str, Any], issues: list[dict[str, Any]], result: SyncRowResult) -> None:
    """把入库前校验结果**落库打标**到该行的 quality_status / quality_note (abc-a)

    为什么不是"一律拒收"(设计 D2 的拒收分支):
      实证本地库 —— 一律拒收会丢 external 8.6% / card 8.7% / manual_refund 0.4% 的台账行,
      其中大量是**真实历史数据**(315 平台 2001~2019 台账 1556 行)。把真实台账挡在业务表外
      会造成报表口径缺口, 且被拒行仍需人工逐条判定 —— 治理成本远高于"入库打标 + 治理队列"。
      故采纳打标分支: 数据进表(报表口径完整), 问题行进待办(人工可见可处理)。

    打标规则:
      · 有 block 级问题 → quality_status='pending' (优先处理)
      · 仅 warn 级问题 → quality_status='pending' (常规处理)
      · 无问题        → 不动(保留库中原值, 见下"覆盖保护")

    覆盖保护(与 #36 'ok 保护' 同精神):
      不覆盖人工已判定结果 —— 若该行库内已是 'ok'(人工确认) 或 'ignored'(人工忽略),
      则本次不重打 pending, 避免已处理的行被每轮同步反复打回待办。

    注意: dedup_hash 不含 quality_* 字段, 因此打标不会改变指纹。但 _batch_upsert 的
    "内容未变则不更新"会在打标值未变时自然跳过更新(幂等)。
    """
    if rec is None:
        return
    # 覆盖保护: 已是人工终态的行不再打回
    #   (库内终态由 _batch_upsert 的 update 覆盖风险由 note 前缀保持稳定来规避:
    #    同型问题 note 相同 → 内容未变 → 不触发更新)
    if rec.get("quality_status") in ("ok", "ignored", "dup_residual"):
        return
    if not issues:
        # 无问题: 显式置 None(仅在新行/历史标记行上生效), 让已修复的行自动退出待办
        rec.setdefault("quality_status", None)
        rec.setdefault("quality_note", None)
        return
    # 类别标签去重(同规则多条问题只记一次), 稳定排序保证 note 可比较(幂等更新)
    labels: list[str] = []
    for rule in sorted({str(i.get("rule") or "其他") for i in issues}):
        labels.append(_QUALITY_RULE_LABEL.get(rule, rule))
    # ⚠️ 消息也按规则名稳定排序 —— 否则同一行两次校验结果顺序不同会生成不同 note,
    #    导致 _batch_upsert 的 "内容未变则不更新" 失效(每轮同步都写一次无意义更新)。
    ordered = sorted(issues, key=lambda i: str(i.get("rule") or ""))
    note = "[" + "/".join(labels) + "] " + "; ".join(
        str(i.get("message") or "") for i in ordered[:3]
    )
    rec["quality_status"] = "pending"
    rec["quality_note"] = note[:_QUALITY_NOTE_MAX]


def _upsert_external(db: Session, rows: list[dict], result: SyncRowResult, sn_ticket_map: dict[str, list[str]]) -> None:
    """315外部投诉 upsert: 按 dedup_hash 冲突更新(含关联工单字段)"""
    if not rows:
        return
    now = datetime.now(timezone.utc)
    now_dt = now
    for r in rows:
        r["dedup_hash"] = _identity_dedup_hash(r)
        # SN 关联工单: 取**登记时间最接近投诉日期**的一张作为代表(#47)
        # ⚠️ 原实现取 [0](最早登记), 与投诉日期无关 → 同 SN 多工单时会选错
        #    (实测 >60 天差异的 112 对里 93 对该 SN 有多张工单)。
        cands = sn_ticket_map.get(r.get("device_sn")) if r.get("device_sn") else None
        tno, _diff = _pick_nearest_ticket(cands, r.get("complaint_date"))
        if tno:
            r["matched_ticket_no"] = tno
            r["matched_at"] = now_dt
            result.matched += 1
        else:
            r["matched_ticket_no"] = None
            r["matched_at"] = None
    # ★ #59/#62: SN 后补治理 —— 有SN的行若命中库内「缺SN残影」, 把残影**标记为
    #   被取代**(不删不回填); 无SN的行若库内已有完整行, 跳过(不生残影)。
    marked, skipped = _reconcile_sn_backfill(db, ExternalComplaint, rows)
    result.sn_backfill_claimed += marked
    result.sn_backfill_skipped += skipped
    _batch_upsert(db, ExternalComplaint, rows, result)


def _upsert_card(db: Session, rows: list[dict], result: SyncRowResult, sn_ticket_map: dict[str, list[tuple[str, datetime]]]) -> None:
    """卡商群投诉 upsert: 按 dedup_hash 冲突更新(含关联工单字段)"""
    if not rows:
        return
    now = datetime.now(timezone.utc)
    for r in rows:
        r["dedup_hash"] = _identity_dedup_hash(r)
        # 同 315: 取登记时间最接近投诉日期的工单(#47)
        cands = sn_ticket_map.get(r.get("device_sn")) if r.get("device_sn") else None
        tno, _diff = _pick_nearest_ticket(cands, r.get("complaint_date"))
        if tno:
            r["matched_ticket_no"] = tno
            r["matched_at"] = now
            result.matched += 1
        else:
            r["matched_ticket_no"] = None
            r["matched_at"] = None
    # ★ #59/#62: 同 315 —— SN 后补治理(标记残影而非回填), 避免"先建行后补SN"重复
    marked, skipped = _reconcile_sn_backfill(db, CardComplaint, rows)
    result.sn_backfill_claimed += marked
    result.sn_backfill_skipped += skipped
    _batch_upsert(db, CardComplaint, rows, result)


def _weak_identity_hash(rec: dict[str, Any]) -> str:
    """**弱指纹**（不含 SN）: md5(投诉日期 | 投诉手机号 | 归一化问题) —— #59

    ## 为什么需要第二个指纹

      源文档存在「**先建行、后补 SN**」的采集特性:

      | 时刻 | 源文档 | 主指纹(含SN) | 结果 |
      |---|---|---|---|
      | T1 | SN 空 | 指纹 A | INSERT「残影」(字段不全) |
      | T2 | SN 补上 | 指纹 B | **又 INSERT** → 同一投诉 2 条并列 |

      实测(生产库): 85 条 = 37 组，冗余 **48 条**; 形态高度一致 ——
      每组都是「缺 SN 残影 + 有 SN 完整行」，且**有 SN 的总在后入库**。

    ## 用途（只在 SN 状态不一致时兜底，不替代主指纹）

      · 待入库行 **有 SN** 且弱指纹命中一条「缺 SN 的行」→ **UPDATE 该行**
        (回填 SN 与全部字段)，不新增
      · 待入库行 **无 SN** 且弱指纹命中一条「有 SN 的行」→ **跳过**
        (避免残影覆盖完整行)

    ⚠️ 为什么主指纹**不能**直接去掉 SN: 同一人可能有多台设备、
       同一天就同一问题各投一次 → 去掉 SN 会**误合并真实的多条投诉**。
       故主指纹保持原语义，弱指纹仅作「SN 从无到有」的识别桥。
    """
    from app.services.upsert import _complaint_problem_signature

    date = rec.get("complaint_date")
    date_s = date.strftime("%Y-%m-%d") if date else ""
    return _dedup_hash([
        date_s,
        rec.get("complainant_phone"),
        _complaint_problem_signature(rec.get("problem")),
    ])


def _reconcile_sn_backfill(db: Session, model, rows: list[dict]) -> tuple[int, int]:
    """处理「SN 后补」—— **标记被取代的残影**, 不新增也不回填 —— #59 / #62

    返回 (标记残影数, 跳过残影数)。

    ## ⚠️ 历史缺陷（已修正）

      初版(#59)想让"有SN的行"**认领**残影: 把该行的 `dedup_hash` 改成残影的
      (旧, 按无SN算) → 交给 upsert。但 `_batch_upsert` 用该指纹冲突匹配的同时
      **回填了 SN** ⇒ 落库结果是「**device_sn 有值 + dedup_hash 按无SN算**」
      的**自相矛盾行**, 与真完整行(另一指纹)**永不归并** ⇒ 反而制造重复。
      (生产实测: 26 条被写坏)

    ## 正确语义（本函数）

      · 有SN的行 遇到「同弱指纹的残影」→ **把残影标记为被取代**
        (`quality_status='dup_sn_backfill'`), 该行**按自己的真指纹**正常入库
        ⇒ 完整行权威; 残影被 #60 的统计排除(不重复计) ★
      · 无SN的行 遇到「同弱指纹的完整行」→ **跳过**(不写库, 免生残影) ★

    ## 为什么"标记"而非"删除"

      用户明令「不能乱删除 所以还是慎重」—— 残影行**保留**, 仅打标;
      置空 `quality_status` 即可完整回滚。

    ## 为什么用弱指纹

      残影缺 SN, 无法用主指纹(含SN)匹配; 而残影与完整行的
      **日期 + 电话 + 问题签名**一致(这正是"同一条投诉"的证据)。
    """
    if not rows:
        return 0, 0

    # 预载: 待入库行的 指纹 → 弱指纹
    weak_of = {r["dedup_hash"]: _weak_identity_hash(r) for r in rows}
    if not weak_of:
        return 0, 0

    phones = {r.get("complainant_phone") for r in rows if r.get("complainant_phone")}
    dates = {r.get("complaint_date") for r in rows if r.get("complaint_date")}
    if not phones or not dates:
        return 0, 0

    db_rows = db.execute(
        select(model.id, model.device_sn, model.complaint_date,
               model.complainant_phone, model.problem, model.quality_status)
        .where(
            model.complainant_phone.in_(phones),
            model.complaint_date >= min(dates),
            model.complaint_date <= max(dates),
        )
    ).all()

    ghosts: dict[str, int] = {}      # 弱指纹 → 残影 id（库内缺SN且未标记的行）
    weak_with_sn: set[str] = set()   # 弱指纹（库内已有SN的行）
    for rid, sn, cd, ph, pb, qs in db_rows:
        if qs == QSTATUS_DUP_SN_BACKFILL:
            continue                 # 已标记的残影不再处理
        w = _weak_identity_hash(
            {"complaint_date": cd, "complainant_phone": ph, "problem": pb}
        )
        if sn:
            weak_with_sn.add(w)
        else:
            ghosts.setdefault(w, rid)

    marked = skipped = 0
    out: list[dict] = []
    for r in rows:
        w = weak_of.get(r["dedup_hash"])
        if r.get("device_sn"):
            # ★ 有SN: 若命中库内残影 → 标记该残影为"被取代"(不删, 不回填)
            gid = ghosts.get(w)
            if gid is not None:
                note = (f"[{QSTATUS_DUP_SN_BACKFILL}] 已由含SN的记录取代"
                        f"(SN={r.get('device_sn')}, 同日期/电话/问题)")[:500]
                db.execute(
                    model.__table__.update()
                    .where(model.__table__.c.id == gid)
                    .values(quality_status=QSTATUS_DUP_SN_BACKFILL,
                            quality_note=note)
                )
                marked += 1
                ghosts.pop(w, None)   # 同一残影只标一次
            out.append(r)             # 本行按自己的真指纹正常入库
        else:
            # ★ 无SN: 若库内已有完整行 → 跳过(不生残影)
            if w in weak_with_sn:
                skipped += 1
                continue
            out.append(r)

    rows[:] = out
    if marked or skipped:
        logger.info(
            f"[complaint_sync] {model.__tablename__} SN后补治理: "
            f"标记残影 {marked} 条(被含SN记录取代), 跳过无SN行 {skipped} 条"
        )
    return marked, skipped


def _batch_upsert(db: Session, model, rows: list[dict], result: SyncRowResult) -> None:
    """按 dedup_hash 做 on_conflict_do_update

    同一批次内 dedup_hash 重复(文件里本身完全相同的行)保留最后一条,避免
    "ON CONFLICT DO UPDATE command cannot affect row a second time" 报错。
    分批写入(单批 <=500 行)避免单条 SQL 绑定参数超过 PG 65535 上限。

    2026-09-08 (#36 完善): 只在实际内容变化时 UPDATE——
      历史行每轮全量重写(updated 虚高为整表行数)无意义; 加
      WHERE 业务字段 IS DISTINCT FROM 入库值, 未变行不更新;
      用 RETURNING (xmax=0) 精确区分 insert/update, 计数准确。
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

    BATCH = 500
    total_ins = 0
    total_upd = 0
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
        # 内容比较字段: 排除去重键/系统派生时间戳/编号保护列——这些不应单独触发整行更新
        _compare_cols = [
            c for c in batch[0]
            if c not in ("dedup_hash", "matched_at", "complaint_no")
        ]
        _where = None
        if _compare_cols:
            _where = or_(*[
                getattr(model, c).is_distinct_from(stmt.excluded[c]) for c in _compare_cols
            ])
        stmt = stmt.on_conflict_do_update(
            index_elements=["dedup_hash"],
            set_=update_cols,
            where=_where,  # 未变行不更新(避免每轮整表重写)
        )
        # RETURNING (xmax=0)=insert; xmax!=0=update(排除被 where 挡下的未变冲突行)
        ret = db.execute(
            stmt.returning(text("(xmax = 0) AS _is_ins"))
        ).all()
        total_ins += sum(1 for r in ret if r[0])
        total_upd += len(ret) - sum(1 for r in ret if r[0])
        db.flush()
    result.inserted += total_ins
    result.updated += total_upd

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
                # A-20260908-003: 未采纳行落拒收表(parse_fail), 供人工判定
                if _write_rejected(
                    db, data_type="external_complaint", source=sync_source,
                    category="parse_fail", reason="315行不是对象",
                    raw_row={"row": row} if not isinstance(row, dict) else row,
                    row_no=idx,
                ):
                    ext_result.rejected += 1
                continue
            rec = _clean_external_row(row, idx, ext_result)
            if rec is None:
                # 已由 add_dropped 计数; 落拒收表保留原始行供人工判定
                if _write_rejected(
                    db, data_type="external_complaint", source=sync_source,
                    category="validate_fail", reason="315行解析失败",
                    raw_row=row, row_no=idx,
                ):
                    ext_result.rejected += 1
                continue
            if _is_empty_external_rec(rec):
                ext_result.filtered += 1
                continue
            # #42 入库前校验(只读): 时间/枚举/必填问题可见化, 不丢弃不改数据
            _issues = validate_external_complaint_row(rec, idx)
            for _iss in _issues:
                ext_result.add_validation(idx, _iss)
            # abc-a: 校验结果**落库打标**(进治理队列), 而非仅停在返回值; 见 _tag_quality
            _tag_quality(rec, _issues, ext_result)
            ext_rows.append(rec)
        ext_result.total = len(ext_rows_raw)

        card_rows: list[dict] = []
        for idx, row in enumerate(card_rows_raw):
            if not isinstance(row, dict):
                card_result.add_dropped(idx, "卡商行不是对象")
                if _write_rejected(
                    db, data_type="card_complaint", source=sync_source,
                    category="parse_fail", reason="卡商行不是对象",
                    raw_row={"row": row} if not isinstance(row, dict) else row,
                    row_no=idx,
                ):
                    card_result.rejected += 1
                continue
            rec = _clean_card_row(row, idx, card_result)
            if rec is None:
                if _write_rejected(
                    db, data_type="card_complaint", source=sync_source,
                    category="validate_fail", reason="卡商行解析失败",
                    raw_row=row, row_no=idx,
                ):
                    card_result.rejected += 1
                continue
            if _is_empty_card_rec(rec):
                card_result.filtered += 1
                continue
            # #195 批次年份传播：收集行号供后续恢复
            rec["_row_index"] = idx
            # #42 入库前校验(只读)
            _issues = validate_card_complaint_row(rec, idx)
            for _iss in _issues:
                card_result.add_validation(idx, _iss)
            _tag_quality(rec, _issues, card_result)
            card_rows.append(rec)
        card_result.total = len(card_rows_raw)

        # #195 批次年份传播恢复
        from app.services.complaint_date_pipeline import batch_recover_with_year_propagation
        recovery_results = batch_recover_with_year_propagation(
            [{**r, "_row_index": r["_row_index"]} for r in card_rows],
            "card",
        )
        for rec in card_rows:
            if not rec.get("complaint_date"):
                rec["complaint_date"] = recovery_results.get(rec["_row_index"])
            rec.pop("_row_index", None)

        # 收集需要关联工单的 SN(两张表共用,避免重复查)
        all_sns: set[str] = set()
        for r in ext_rows + card_rows:
            if r.get("device_sn"):
                all_sns.add(r["device_sn"])
        # ⚠️ 2026-09-10 (#47): 映射改为携带 register_time, 由 _pick_nearest_ticket
        #    按"登记时间最接近投诉日期"选代表工单。
        #    原实现取 [0](最早登记) → 同 SN 多工单时会错配;
        #    但**不**加硬时间窗(实测存在集中补录的合法投诉工单, 硬窗会误杀)。
        sn_ticket_map = _sn_to_ticket_map(db, all_sns)

        # 维护设备主表(缺失 SN 建空壳,避免外键失败)
        _sync_devices(ext_rows + card_rows, "device_sn", db)

        _upsert_external(db, ext_rows, ext_result, sn_ticket_map)
        _upsert_card(db, card_rows, card_result, sn_ticket_map)

        # ── #42 批次级异常检测(流程层防线) ──
        # 动机: 2026-09-05 日期转置事件中, 字段级校验只检出该批 2532 行里的 1684 行;
        # 余下 848 行落在"看似正常"的年份区间, 单行完全正常。这类**整批系统性错误**
        # 在批次维度上才醒目(该批 inserted=2434 vs 正常 0~3, 异常日期占比 66.5%)。
        # 只告警不改数据; critical 走事务通知。
        try:
            from app.services.ingest_anomaly import check_complaint_batch

            check_complaint_batch(
                db, rows=ext_rows, inserted=ext_result.inserted,
                label=f"315投诉({source_file or sync_source})",
            )
        except Exception as an_err:  # noqa: BLE001 检测不得影响入库主流程
            logger.warning(f"[complaint_sync] 批次异常检测失败(忽略): {an_err}")

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

        # ── #42 abc-c: 持久化拒收与校验信息(供采集日志/推送页展示历史) ──
        # 不写 error 列: 校验告警不是同步失败(推送可 success 且有告警),
        # 混入会让"错误"列在成功记录里显示文本(复审 N-2 曾因此回退)。
        _all_val = ext_result.validation + card_result.validation
        log.rejected = ext_result.rejected + card_result.rejected
        log.validation_count = len(_all_val)
        log.validation_severity = summarize_severity(_all_val)

        # ⚠️ log.error 只承载**真实错误**(level=error) —— 软警告(清洗提示)不进 error 列,
        #    否则成功推送会在采集日志"错误"列显示 warning 文本(复审 W-3)。
        all_errors = [
            e for e in (ext_result.errors + card_result.errors) if e.get("level") == "error"
        ][:50]
        if all_errors:
            log.error = str(all_errors)
        # 空行过滤信息: 仅日志记录(不写 log.error, 同 N-2 —— 空行过滤是正常噪音,
        # 写 error 列会让成功推送在采集日志"错误"列显示文本)
        if ext_result.filtered or card_result.filtered:
            logger.info(
                f"[complaint_sync] 过滤全空行: 315={ext_result.filtered} "
                f"卡商={card_result.filtered}"
            )
        # #42 校验问题计数(只读, 可见化): 独立通道汇总。
        # ⚠️ 不写 log.error(审查 N-2): 校验告警不是同步失败, 写入 error 列会让
        #    成功推送在采集日志"错误"列显示文本。已改为写入专用列
        #    validation_count / validation_severity (abc-c) + logger.warning。
        if log.validation_count:
            logger.warning(
                f"[complaint_sync] 入库前校验告警 {log.validation_count} 条"
                f"(315={len(ext_result.validation)} 卡商={len(card_result.validation)})"
                f"; 明细见返回 validation 字段"
            )
        if ext_result.dropped or card_result.dropped:
            log.status = "partial"
        else:
            log.status = "success"
        log.finished_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(log)

        # 缓存失效+预热: 投诉推送入库后, 投诉看板(complaint:*)与今日看板投诉区块(today:*)
        # 必须刷新, 否则看板显示旧数据直到 60s 缓存自然过期(见 数据源接入与缓存加速规范.md §九 G1)
        has_writes = (
            ext_result.inserted + ext_result.updated
            + card_result.inserted + card_result.updated
        ) > 0
        if has_writes:
            try:
                from app.core.redis_client import cache_invalidate_recent_days
                for pat in ("complaint:*", "today:*"):
                    cache_invalidate_recent_days(pat)
                # 预热今日视图(含投诉区块), 让首访命中热缓存; 投诉推送频率低(约20min/次),
                # 复用同步后预热入口(含单飞锁+can_warm检查), 月报顺带刷新成本可忽略
                from app.services.cache_warmer import warm_after_sync
                warm_after_sync()
                # #123 同步完成后触发 AI 报告预生成(sync-triggered 模板)
                # 失败仅 log, 不影响同步流程(在 warm_after_sync 之后, 共用单飞锁)
                try:
                    from app.services.ai_report_worker import warm_on_sync_complete
                    warm_on_sync_complete()
                except Exception as ai_err:  # noqa: BLE001
                    logger.warning(f"[complaint_sync] AI 报告预生成失败(不影响主流程): {ai_err}")
            except Exception as cache_err:
                logger.warning(f"[complaint_sync] 缓存失效/预热失败(不影响主流程): {cache_err}")

        # 系统事务通知: 投诉推送接收结果(独立于详细日志, 供「事务通知」页查看)
        try:
            from app.services.system_event_service import record_system_event
            record_system_event(
                db,
                category="push",
                action="complaint_push",
                title=f"投诉数据推送接收: {source_file or 'workbuddy-push'}",
                summary=(
                    f"315={ext_result.total}(+{ext_result.inserted}/~{ext_result.updated}/关联{ext_result.matched})"
                    f" 卡商={card_result.total}(+{card_result.inserted}/~{card_result.updated}/关联{card_result.matched})"
                ),
                status="success" if log.status == "success" else "partial",
                detail=f"sync_source={sync_source} log_id={log.id} 来源文件={source_file or '-'}",
            )
        except Exception as ev_err:
            logger.warning(f"[system_event] 投诉推送事务事件写入失败(不影响主流程): {ev_err}")

        logger.info(
            f"[complaint_sync] {source_file or 'workbuddy-push'} 完成: "
            f"315={ext_result.total}(+{ext_result.inserted}/~{ext_result.updated}/!{ext_result.dropped}/关联{ext_result.matched}) "
            f"卡商={card_result.total}(+{card_result.inserted}/~{card_result.updated}/!{card_result.dropped}/关联{card_result.matched})"
        )
        # ★★ #56: 同步后**自动重匹配** —— 解决「工单晚到不补挂」★★
        #   实测外部投诉类工单 created_at - register_time 平均滞后 **71.1 天**
        #   (>30 天占 53%)。而 `matched_ticket_no` 只在同步瞬间写一次,
        #   之后从不重算 → 工单晚到时该 315 记录**永久空悬**。
        #   用户 P0 要求「最新数据持续对应上」, 故每次有写入后重跑一次。
        #   ⚠️ 主事务此刻已 commit(见上方 db.commit()), 故此处独立提交安全;
        #      重匹配失败**不得**影响入库主流程(仅告警)。
        reconcile_stat = None
        if has_writes:
            try:
                from app.services.external_reconcile import remark_matched
                reconcile_stat = remark_matched(db, window_days=90)
            except Exception as rc_err:  # noqa: BLE001 重匹配失败不影响入库
                logger.warning(f"[complaint_sync] 同步后重匹配失败(不影响入库): {rc_err}")

        return {
            "log_id": log.id,
            "status": log.status,
            "reconcile": reconcile_stat,   # #56: 重匹配统计(可观测)
            # #42 校验问题清单(只读 advisory; 与 errors 严格分离)
            "validation": (ext_result.validation + card_result.validation)[:200],
            # abc-a: 按处置级别聚合(block=不可修复优先处理 / warn=常规)
            "validation_severity": summarize_severity(
                ext_result.validation + card_result.validation
            ),
            # #42 真正落拒收表的条数(与 failed/dropped 不同义)
            "rejected": ext_result.rejected + card_result.rejected,
            "external": {
                "total": ext_result.total,
                "inserted": ext_result.inserted,
                "updated": ext_result.updated,
                "failed": ext_result.dropped,
                "filtered": ext_result.filtered,
                "warnings": ext_result.warnings,
                "matched": ext_result.matched,
                "errors": ext_result.errors[:50],
            },
            "card": {
                "total": card_result.total,
                "inserted": card_result.inserted,
                "updated": card_result.updated,
                "failed": card_result.dropped,
                "filtered": card_result.filtered,
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
