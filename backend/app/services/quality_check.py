"""通用数据质量校验器 (L2 导入自动检测 / L3 人工审核)

数据源: 手动退款(manual_refund)的质量校验模式泛化, 推广到核心业务表。
每条记录检测后写入 quality_status / quality_note:
  None         正常(无问题)
  pending      检测出问题, 需人工确认(不自动改数据)
  auto_fixed   高置信问题已自动修复(当前仅手动退款时间年份错位场景, 由 upsert_manual_refunds 内部处理)
  auto_filled  op_time 由 E 表(refund_record_reports)回填(D 路, reconcile.backfill_d_op_time)
  ok           人工确认通过(quality_confirm/quality_fix 置位)
  dup_residual 已治理重复(从经营统计排除, 不物理删除, 可恢复)
  ignored      人工裁定彻底忽略(从经营统计排除, 通用"该笔不纳入", A-20260909-001 §2.2)
  sn_unresolvable D路手动退款 device_sn 缺失且 order_no 三表(refunds/device_combos/
                bms_orders)反查均无匹配(#311 方案B, 由 backfill_manual_refund_sn.py 打标);
                不进入 STATS_EXCLUDED_QUALITY_STATUSES, 不影响经营统计口径, 仅作数据质量页可见标记

统计排除约定(analytics/_quality_exclude): COALESCE(quality_status,'') NOT IN
('dup_residual','ignored')  → 这两个状态的行不纳入经营四路 A/B/C/D 统计。
(sn_unresolvable 不在排除集合, 仍计入统计——D 路退款金额仍发生, 仅 SN 缺失不影响金额口径)

校验规则(按表配置, 全部 4 类):
  R-时间  时间字段异常: 缺失/未来日期/年份错位(远古)/时间倒挂/跨度异常
  R-枚举  枚举/状态脏值: 字段值不在预期集合
  R-金额  金额异常: <=0 或缺失
  R-必填  关键字段缺失: 影响跨表关联与统计
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.manual_refund import ManualRefund
from app.models.refund import Refund
from app.models.shanmao_refund import ShanmaoRefund
from app.models.ticket import ServiceTicket
from app.models.wallet import WalletWithdrawal

logger = logging.getLogger(__name__)

_UTC = timezone.utc
_TZ_CN = timezone(timedelta(hours=8))

# ── 经营统计质量排除口径（#201 S-P0-1：全仓唯一字面量定义） ────────
# 凡「经营四路 A/B/C/D / 经营宽表 / 物化 refund_wide」统计，统一排除以下 quality_status：
#   dup_residual = 已治理重复(不物理删除, 可恢复); ignored = 人工裁定彻底忽略
# 唯一允许的新增/修改位置就在本元组；其它任何模块必须 import 本常量或下面两个
# 同口径助手，禁止再写第二份 ('dup_residual','ignored') 字面量（双实现必然漂移）。
STATS_EXCLUDED_QUALITY_STATUSES: tuple[str, ...] = ("dup_residual", "ignored")


def stats_quality_excluded_sql(col: str = "quality_status") -> str:
    """raw SQL(text()) 排除片段：`COALESCE(<col>,'') NOT IN ('dup_residual','ignored')`。

    - 状态值来自程序常量（非用户输入），内联进 SQL 无注入风险；
    - `col` 仅限内部调用方传入的**列标识符**（可用 'r.quality_status' 这类别名限定），
      绝不接受外部输入；
    - 与 `stats_quality_include`（ORM 侧）必须保持同口径，改动任一处须同步另一处。
    """
    in_list = ",".join(f"'{s}'" for s in STATS_EXCLUDED_QUALITY_STATUSES)
    return f"COALESCE({col},'') NOT IN ({in_list})"


def stats_quality_include(column):
    """ORM 侧同口径条件：column IS NULL OR column NOT IN (排除集合)。

    用于 SQLAlchemy 查询；NULL(未打标=正常行)计入统计，与 SQL 片段的 COALESCE 语义一致。
    """
    return or_(column.is_(None), column.notin_(STATS_EXCLUDED_QUALITY_STATUSES))


# ── 枚举预期集合(脏值检测) ──────────────────────────────────────
REFUND_METHOD_SET = {"线上", "线下", "转入提现账户"}
WALLET_OP_TYPE_SET = {"用户提现", "订单退费转入", "系统扣费"}
SHANMAO_STATUS_SET = {"已退款", "已驳回", "审核中", "申请中", "退款中", "待审核", "已完成"}
TICKET_STATUS_SET = {"待处理", "处理中", "已完成", "已关闭", "终止", "已结案", "已解决", "待分派", "跟进中", "pending"}


# ── 时间工具 ────────────────────────────────────────────────────
def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=_UTC)


def _in_reasonable_range(dt: datetime | None) -> bool:
    """时间是否在合理窗口: [当前年-2 的1/1, now+1天]"""
    if dt is None:
        return False
    now = datetime.now(_UTC)
    low = datetime(now.year - 2, 1, 1, tzinfo=_UTC)
    high = now + timedelta(days=1)
    d = _as_utc(dt)
    return d is not None and low <= d <= high


def _time_note(dt: datetime | None, label: str) -> str | None:
    """R-时间: 缺失/未来/远古 → 返回问题描述"""
    if dt is None:
        return f"{label}缺失"
    d = _as_utc(dt)
    now = datetime.now(_UTC)
    if d > now + timedelta(days=1):
        return f"{label}为未来时间({d:%Y-%m-%d})"
    if d.year < now.year - 2:
        return f"{label}年份错位({d:%Y-%m-%d}, 疑似远古日期)"
    return None


def _reversed_note(a: datetime | None, b: datetime | None, la: str, lb: str) -> str | None:
    """R-时间: a 晚于 b(倒挂)"""
    if a is None or b is None:
        return None
    x, y = _as_utc(a), _as_utc(b)
    if x and y and x < y:
        return f"{la}早于{lb}(时间倒挂 {x:%Y-%m-%d} < {y:%Y-%m-%d})"
    return None


def _span_note(a: datetime | None, b: datetime | None, la: str, lb: str, max_hours: float) -> str | None:
    """R-时间: a 与 b 跨度超过 max_hours(疑似上游批量覆盖/异常时长)"""
    if a is None or b is None:
        return None
    x, y = _as_utc(a), _as_utc(b)
    if x and y:
        diff = (y - x).total_seconds() / 3600.0
        if diff > max_hours:
            return f"{la}→{lb}跨度 {diff:.0f}h(超{max_hours:.0f}h, 疑似批量覆盖)"
    return None


def _enum_note(value: str | None, allowed: set[str], label: str) -> str | None:
    """R-枚举: 非空且不在预期集合 → 脏值"""
    if not value:
        return None
    v = str(value).strip()
    if v not in allowed:
        return f"{label}异常'{v}'"
    return None


def _D(val) -> Decimal:
    """金额定点化（#222；与 analytics._core.D 同语义，本文件因循环依赖内联）。"""
    if val is None:
        return Decimal("0")
    if isinstance(val, Decimal):
        return val
    return Decimal(str(val))


def _amount_note(value: Any, label: str, allow_zero: bool = False) -> str | None:
    """R-金额: 缺失或 <=0(allow_zero=True 时允许 0)"""
    if value is None:
        return f"{label}缺失"
    try:
        v = _D(value)
    except (TypeError, ValueError, InvalidOperation):
        return f"{label}非数值({value})"
    if v < 0 or (v == 0 and not allow_zero):
        return f"{label}异常({v})"
    return None


def _required_note(value: Any, label: str) -> str | None:
    """R-必填: 关键字段缺失"""
    if value is None:
        return f"缺{label}"
    s = str(value).strip() if not isinstance(value, (int, float)) else str(value)
    if not s:
        return f"缺{label}"
    return None


def _finalize(notes: list[str | None]) -> tuple[str | None, str | None]:
    """汇总问题列表 → (quality_status, quality_note)"""
    issues = [n for n in notes if n]
    if not issues:
        return None, None
    return "pending", "; ".join(issues[:4]) + ("…" if len(issues) > 4 else "")


# ── 各表单行级校验 ──────────────────────────────────────────────
def check_refund_row(r: Refund) -> tuple[str | None, str | None]:
    """退费单: 只标记个别异常(系统性 0 元单=无需退款/申请中 为正常业务, 不标)。

    实测分布(2026-08): 0 元单 57k(无需退款/申请中, 正常); 缺SN 0; 负数金额 0。
    真正可人工处理的问题: 退款方式脏值、创建时间异常、更新时间早于创建时间(倒挂)。
    """
    notes: list[str | None] = [
        _enum_note(r.refund_method, REFUND_METHOD_SET, "退款方式"),
        _time_note(r.created_at_raw, "创建时间"),
        _reversed_note(r.updated_at_raw, r.created_at_raw, "更新时间", "创建时间"),
    ]
    return _finalize(notes)


def check_ticket_row(r: ServiceTicket) -> tuple[str | None, str | None]:
    """服务工单: 只标记时间矛盾(工单金额/服务类别缺失为数据源系统性特征, 不标)。

    实测分布(2026-08): 缺退款金额 99.8%、缺服务类别 29k、缺SN 0, 均非个别错误;
    finish_time 被上游批量覆盖是已知事实(已在数据源注册层标记), 只对跨天倒挂标 pending。
    """
    notes: list[str | None] = [
        _enum_note(r.status, TICKET_STATUS_SET, "状态"),
        _time_note(r.register_time, "登记时间"),
    ]
    # 完成时间早于登记时间超 1 天 → 确定的数据矛盾(同日内分钟级时差多为批量覆盖噪音, 不标)
    if r.finish_time is not None and r.register_time is not None:
        x, y = _as_utc(r.finish_time), _as_utc(r.register_time)
        if x and y and (y - x).total_seconds() > 86400:
            notes.append(f"完成时间早于登记时间超1天(疑似批量覆盖 {x:%Y-%m-%d} < {y:%Y-%m-%d})")
    return _finalize(notes)


# 2026-08-28: 提现失败/冻结备注关键词 (BMS 提现操作记录中, 备注含"提现申请-冻结"等
# 表示提现申请被冻结/拒绝, 非成功退款, 不计入成功退款统计)。
_WITHDRAW_FAIL_REMARK_KEYWORDS = ("冻结", "退款失败", "提现失败", "超期订单不允许退款", "驳回", "拒绝", "未到账")


def is_withdraw_fail_remark(remark: str | None) -> bool:
    """备注是否为"失败/冻结"类(冻结/退款失败/超期订单不允许退款等)。"""
    if not remark:
        return False
    return any(k in remark for k in _WITHDRAW_FAIL_REMARK_KEYWORDS)


def check_wallet_row(r: WalletWithdrawal) -> tuple[str | None, str | None]:
    """钱包流水: 数据质量整体好, 只标明确异常(操作类型脏值/金额负值/操作时间异常)。

    2026-08-28: 备注含"提现申请-冻结"等失败/冻结关键词的记录标记为 pending,
    便于数据质量页识别(此类记录不计入成功退款统计, 属业务标记而非数据异常)。
    """
    notes: list[str | None] = [
        _required_note(r.op_amount, "操作金额"),
        _enum_note(r.op_type, WALLET_OP_TYPE_SET, "操作类型"),
        _time_note(r.op_time, "操作时间"),
    ]
    if r.op_amount is not None:
        try:
            if _D(r.op_amount) < 0:
                notes.append(f"操作金额为负({r.op_amount})")
        except (TypeError, ValueError):
            notes.append(f"操作金额非数值({r.op_amount})")
    if is_withdraw_fail_remark(r.remark):
        notes.append("提现失败/冻结备注(不计入成功退款统计)")
    return _finalize(notes)


def check_shanmao_row(r: ShanmaoRefund) -> tuple[str | None, str | None]:
    """闪猫退款: 缺SN/申请时间为数据源系统性缺失(100%), 不标; 只标状态脏值/金额缺失/时间倒挂。"""
    notes: list[str | None] = [
        _required_note(r.actual_amount, "实际退款金额"),
        _enum_note(r.status, SHANMAO_STATUS_SET, "状态"),
        _reversed_note(r.audit_time, r.apply_time, "审核时间", "申请时间"),
        _reversed_note(r.refund_time, r.audit_time, "退款时间", "审核时间"),
    ]
    if r.actual_amount is not None:
        try:
            if _D(r.actual_amount) < 0:
                notes.append(f"实际退款金额为负({r.actual_amount})")
        except (TypeError, ValueError):
            notes.append(f"实际退款金额非数值({r.actual_amount})")
    return _finalize(notes)


# ── 表配置注册 ──────────────────────────────────────────────────
QUALITY_TABLES: dict[str, dict[str, Any]] = {
    "refund": {
        "model": Refund,
        "label": "退费单",
        "pk_field": "refund_no",
        "pk_label": "退费单号",
        "time_field": "created_at_raw",
        "check_fn": check_refund_row,
        "fixable_fields": {
            "refund_method": "退款方式", "status": "状态", "refund_amount": "退款金额",
            "created_at_raw": "创建时间", "updated_at_raw": "更新时间", "operator": "操作人",
            "auditor": "审核人", "device_sn": "设备SN", "refund_reason": "退款原因",
        },
    },
    "ticket": {
        "model": ServiceTicket,
        "label": "服务工单",
        "pk_field": "ticket_no",
        "pk_label": "工单号",
        "time_field": "register_time",
        "check_fn": check_ticket_row,
        "fixable_fields": {
            "status": "状态", "service_category": "服务类别", "handler": "处理人",
            "registrant": "登记人", "register_time": "登记时间", "finish_time": "完成时间",
            "device_sn": "设备SN", "refund_amount": "退款金额",
        },
    },
    "wallet": {
        "model": WalletWithdrawal,
        "label": "钱包流水",
        "pk_field": "id",
        "pk_label": "ID",
        "time_field": "op_time",
        "check_fn": check_wallet_row,
        "fixable_fields": {
            "op_type": "操作类型", "op_amount": "操作金额", "op_time": "操作时间",
            "operator": "操作人", "account_id": "账户ID", "device_sn": "设备SN", "remark": "备注",
        },
    },
    "shanmao": {
        "model": ShanmaoRefund,
        "label": "闪猫退款",
        "pk_field": "refund_no",
        "pk_label": "退款单号",
        "time_field": "apply_time",
        "check_fn": check_shanmao_row,
        "fixable_fields": {
            "status": "状态", "refund_method": "退款方式", "refund_amount": "退款金额",
            "actual_amount": "实际退款金额", "apply_time": "申请时间", "audit_time": "审核时间",
            "refund_time": "退款时间", "device_sn": "设备SN", "operator": "操作人",
        },
    },
    "manual_refund": {
        "model": ManualRefund,
        "label": "手动退款",
        "pk_field": "id",
        "pk_label": "ID",
        "time_field": "register_time",
        "check_fn": None,  # 手动退款校验在 upsert 内部(_manual_refund_quality_check), 不走通用规则
        "fixable_fields": {
            "register_time": "登记日期", "op_time": "操作时间", "is_refunded": "是否已退款",
            "refund_amount": "退款金额", "order_no": "订单号", "device_sn": "设备SN",
            "registrant": "登记人", "refund_operator": "退款操作人", "remark": "备注",
        },
    },
}


def get_table_config(code: str) -> dict[str, Any]:
    cfg = QUALITY_TABLES.get(code)
    if not cfg:
        raise ValueError(f"不支持的质量审核表单: {code}")
    return cfg


def quality_summary(db: Session, table: str) -> dict[str, int]:
    """各状态计数: {pending, auto_fixed, ok, ignored, dup_residual, total}

    A-20260909-001: 新增 ignored(人工彻底忽略)/dup_residual(已治理重复) 计数,
    供质量页"已忽略"筛选与看板展示。
    """
    cfg = get_table_config(table)
    model = cfg["model"]
    summary = {"pending": 0, "auto_fixed": 0, "ok": 0, "ignored": 0, "dup_residual": 0, "total": 0}
    for st in ("pending", "auto_fixed", "ok", "ignored", "dup_residual"):
        summary[st] = db.scalar(select(func.count()).select_from(model).where(model.quality_status == st)) or 0
    summary["total"] = db.scalar(select(func.count()).select_from(model)) or 0
    return summary


def quality_list(db: Session, table: str, status: str = "all",
                 page: int = 1, page_size: int = 50) -> dict[str, Any]:
    """质量记录列表: status = all/pending/auto_fixed/ok/ignored/dup_residual, 按时间字段倒序。"""
    cfg = get_table_config(table)
    model = cfg["model"]
    q = select(model)
    if status in ("pending", "auto_fixed", "ok", "ignored", "dup_residual"):
        q = q.where(model.quality_status == status)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    time_col = getattr(model, cfg["time_field"], None)
    q = q.order_by(time_col.desc().nullslast() if time_col is not None else model.quality_status)
    rows = db.execute(q.offset((page - 1) * page_size).limit(page_size)).scalars().all()
    return {"total": total, "summary": quality_summary(db, table),
            "list": [_row_to_dict(r) for r in rows]}


def quality_confirm(db: Session, table: str, pk: str | int) -> dict[str, Any]:
    """人工确认通过: quality_status → ok"""
    cfg = get_table_config(table)
    cfg["model"]
    r = _get_by_pk(db, cfg, pk)
    r.quality_status = "ok"
    r.quality_note = ((r.quality_note or "") + " | 人工确认通过") if r.quality_note else "人工确认通过"
    db.commit()
    return {"id": pk, "quality_status": "ok"}


def quality_fix(db: Session, table: str, pk: str | int, payload: dict[str, Any]) -> dict[str, Any]:
    """人工修正: 更新白名单字段, 修正后置 ok。时间字段支持多种格式。"""
    cfg = get_table_config(table)
    model = cfg["model"]
    r = _get_by_pk(db, cfg, pk)
    allowed = cfg["fixable_fields"]
    changes: list[str] = []
    from sqlalchemy import DateTime
    for k, v in (payload or {}).items():
        if k not in allowed or v is None:
            continue
        col = getattr(model, k, None)
        is_dt = col is not None and isinstance(col.property.columns[0].type, DateTime)
        if is_dt:
            setattr(r, k, _parse_dt(v))
            changes.append(k)
        elif k in ("refund_amount", "actual_amount", "op_amount", "order_amount"):
            try:
                setattr(r, k, _D(v))
            except (TypeError, ValueError, InvalidOperation):
                continue
            changes.append(k)
        else:
            setattr(r, k, str(v))
            changes.append(k)
    r.quality_status = "ok"
    r.quality_note = ((r.quality_note or "") + f" | 人工修正字段: {','.join(changes)}") if changes else (r.quality_note or "")
    db.commit()
    return {"id": pk, "quality_status": "ok", "changed": changes}


def quality_scan(db: Session, table: str, limit: int = 0) -> dict[str, Any]:
    """存量数据重检: 全表(或前 limit 条)重新跑校验规则, 覆盖 quality_status/note。

    用于首次部署后给存量数据打标, 或数据源重新导入后刷新问题清单。
    返回 {scanned, pending, ok}。
    """
    cfg = get_table_config(table)
    model = cfg["model"]
    check_fn = cfg.get("check_fn")
    if check_fn is None:
        return {"table": table, "scanned": 0, "pending": 0, "ok": 0, "message": "该表单无通用校验规则(导入时内部处理)"}
    q = select(model)
    if limit and limit > 0:
        q = q.limit(limit)
    rows = db.execute(q).scalars().all()
    scanned = 0
    pending = 0
    for r in rows:
        status, note = check_fn(r)
        r.quality_status = status
        r.quality_note = note
        scanned += 1
        if status == "pending":
            pending += 1
    db.commit()
    ok = scanned - pending
    logger.info(f"[quality_scan] {table}: scanned={scanned}, pending={pending}, ok={ok}")
    return {"table": table, "scanned": scanned, "pending": pending, "ok": ok}


# ── 内部工具 ────────────────────────────────────────────────────
def _get_by_pk(db: Session, cfg: dict[str, Any], pk: str | int):
    model = cfg["model"]
    pk_field = cfg["pk_field"]
    r = db.scalar(select(model).where(getattr(model, pk_field) == pk))
    if r is None:
        raise ValueError(f"{cfg['label']}记录不存在: {pk_field}={pk}")
    return r


def _parse_dt(v: Any) -> datetime:
    s = str(v).strip()
    dt: datetime | None = None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            break
        except ValueError:
            continue
    if dt is None:
        # ISO with tz
        from datetime import datetime as _dt_parse
        try:
            dt = _dt_parse.fromisoformat(s)
        except ValueError:
            raise ValueError(f"无法解析时间字段: {s}")
    # naive 视为北京时间(与 upsert 导入口径一致) → 转 UTC 后写入 timestamptz
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_TZ_CN)
    return dt.astimezone(timezone.utc)


def _row_to_dict(r: Any) -> dict[str, Any]:
    d = {}
    for col in r.__table__.columns:
        v = getattr(r, col.name)
        if isinstance(v, datetime):
            v = v.replace(tzinfo=_UTC).astimezone(_UTC).isoformat() if v.tzinfo else v.isoformat()
        d[col.name] = v
    return d
