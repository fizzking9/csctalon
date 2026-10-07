"""退款挽回/人员/设备/决策 + 手动退款质检 + 导出 + 退款挽回主分析。"""

import datetime as _dt
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, select, text
from sqlalchemy.orm import Session

from app.models.manual_refund import ManualRefund
from app.models.person import Person, PersonAlias

logger = logging.getLogger(__name__)

# 跨子模块引用(拆分自 app.services.analytics, 原同文件内直接调用)
from app.services.analytics._core import (
    COMPLAINT_SPECIALTY,
    REFUND_RECOVERY_HR_CATS,
    SYS_OP_BLACKLIST_RR,
    TZ,
    _is_refund_staff,
    _normalize_refund_reason,
    _resolve_windows,
    _strip_qw_prefix,
    _withdraw_fail_remark_sql_cond,
)
from app.services.analytics.cash import _EXCL_QSTATUS_SQL, _quality_exclude_sql

# #201 S-P0-1：别名限定的质量排除片段（决策概览裸 SQL 用 r./w./s. 限定）
from app.services.quality_check import stats_quality_excluded_sql

# ==============================================================
# 退款看板深度分析 - 公共核心计算 (2026-08-12 新增)
# 提取 refund_recovery_analysis 的第1-5步核心逻辑, 供 9 个新接口复用
# 口径与 refund_recovery_analysis 完全一致
# ==============================================================

# cs_record描述中的"已进入退款流程"关键词 (匹配即判失败)
_RR_CS_FAIL_KW = ["退款说明", "退款后重新充值", "要求退回微信", "非挽回用户"]

# 系统自动过滤关键词
_RR_SYS_KW = ["系统自动", "自动发送", "自动回复", "自动推送", "自动补发", "自动触发", "自动生成"]

def _refund_recovery_core(db: Session, ts, te) -> dict:
    """退款挽回核心计算: 人员meta + 分母SN + 失败SN + 退款操作 + staff_detail.

    与 refund_recovery_analysis 第1-5步逻辑完全一致, 提取为公共函数供深度分析接口复用.

    Returns:
        dict with keys:
        - all_ops_meta: dict[op, {name, group, specialty, position, dept, hr_cat, is_recovery_staff}]
        - op_host_sns: dict[op, set[sn]]  人员→接待SN集合
        - global_host_sns: set[sn]  全局分母SN
        - sn_first_op_time: dict[sn, datetime]  SN→最早接待时间
        - sn_host_ops: dict[sn, list[op]]  SN→接待人员列表
        - sn_host_reason: dict[sn, str]  SN→归一化原因(从cs_record.problem_type)
        - global_fail_sns: set[sn]  失败SN集合
        - sn_failed_meta: dict[sn, {src, reason, amount, status, method}]  SN→失败明细
        - refund_op_sns: dict[op, set[sn]]  人员→退款操作SN集合
        - refund_op_amt: dict[op, float]  人员→退款操作金额
        - staff_detail: list[dict]  人员明细(staff_type/total_sn/success_sn/failed_sn/...)
        - ts, te: 时间窗口
    """
    SYS_KW = _RR_SYS_KW

    def _desc_auto_filter(col):
        return ~and_(*[func.coalesce(col, "").ilike(f"%{k}%") for k in SYS_KW])

    def _hr_map(hr_sp: str | None, pos: str | None) -> str | None:
        """人员主专项归一化映射 (2026-08-19 修订)
        返回: 专项名称 | "管理层" | None

        新增: 专项岗(专员)显式识别, 与管理岗同等待遇 → 跨类全豁免
        """
        # ① 管理岗（主管/组长/副主管）→ 跨类全豁免
        if (pos or "") in ("主管", "组长", "副主管"):
            return "管理层"
        # ② 专项岗(专员) 显式识别: 投诉专员/应急专员/退款挽回专员 等
        p = (pos or "").strip()
        if "专员" in p:
            if "投诉" in p:
                return "外部投诉" if "外部" in p else "内部投诉"
            if "挽回" in p:
                return "退款挽回"
            if "应急" in p:
                return "内部投诉"
            return "内部投诉"
        if not hr_sp:
            return None
        s = (hr_sp or "").strip()
        if s in ("咨询接待", "套餐专项", "退款处理", "退款挽回", "内部投诉", "外部投诉", COMPLAINT_SPECIALTY):
            return s
        if "外部投诉" in s: return "外部投诉"
        if "内部投诉" in s or "投诉" in s: return "内部投诉"
        if "退款挽回" in s: return "退款挽回"
        if "退款处理" in s or "退款" in s: return "退款处理"
        if "套餐" in s: return "套餐专项"
        if any(k in s for k in ("咨询", "接待", "综合问题", "其他问题")):
            return "咨询接待"
        return None

    # ---- 2. 人员维表 ----
    from app.models.person import Person as P
    from app.models.person import PersonAlias as PA
    person_rows = db.execute(
        select(
                PA.alias.label("operator"),
                PA.person_id.label("pid"),
                func.coalesce(P.display_name, PA.alias).label("display_name"),
                P.group_name,
                func.coalesce(P.specialty, P.primary_specialty).label("specialty"),
                P.position, P.department,
            )
        .select_from(PA)
        .outerjoin(P, PA.person_id == P.id)
        .where(PA.is_system.is_(False))
    ).all()

    recovery_ops: dict[str, dict] = {}
    all_ops_meta: dict[str, dict] = {}
    for pr in person_rows:
        op = pr.operator or ""
        if not op:
            continue
        hr_cat = _hr_map(pr.specialty, pr.position)
        info = {
            "name": pr.display_name or op,
            "person_id": pr.pid,
            "group": pr.group_name or "未分组",
            "specialty": pr.specialty,
            "position": pr.position,
            "dept": pr.department or "未设置",
            "hr_cat": hr_cat,
            "is_recovery_staff": _is_refund_staff(pr.group_name, hr_cat),
        }
        all_ops_meta[op] = info
        if info["is_recovery_staff"]:
            recovery_ops[op] = info

    # ---- 3. 分母SN (cs_records problem_category='退款') ----
    cs_refund_sn_sql = text(
        """
        SELECT c.operator AS op, NULLIF(TRIM(c.device_sn),'') AS sn,
               c.operation_time AS op_time,
               c.problem_type AS problem_type
        FROM cs_records c
        WHERE c.operation_time IS NOT NULL
          AND c.operation_time >= :ts AND c.operation_time <= :te
          AND NOT (c.operator = ANY(:sys_bl))
          AND c.problem_category = '退款'
          AND NOT (
              COALESCE(c.description,'') ILIKE ANY(ARRAY[:k1,:k2,:k3,:k4,:k5,:k6,:k7])
           OR COALESCE(c.execution_result,'') ILIKE ANY(ARRAY[:k1,:k2,:k3,:k4,:k5,:k6,:k7])
           OR COALESCE(c.solution,'') ILIKE ANY(ARRAY[:k1,:k2,:k3,:k4,:k5,:k6,:k7])
          )
        """
    )
    kw_params = {f"k{i+1}": f"%{k}%" for i, k in enumerate(SYS_KW)}
    cs_sn_rows = db.execute(
        cs_refund_sn_sql,
        {"ts": ts, "te": te, "sys_bl": list(SYS_OP_BLACKLIST_RR), **kw_params},
    ).fetchall()

    all_cs_ops: set[str] = set()
    for r in cs_sn_rows:
        if r.op:
            all_cs_ops.add(r.op)

    person_op_map = {p.operator: p for p in person_rows}
    for op in all_cs_ops:
        if op not in all_ops_meta:
            p = person_op_map.get(op)
            if p:
                all_ops_meta[op] = {
                    "name": p.display_name or op,
                    "group": p.group_name or "未分组",
                    "specialty": p.specialty or None,
                    "position": p.position or "",
                    "dept": p.department or "未设置",
                    "is_recovery_staff": (p.specialty in REFUND_RECOVERY_HR_CATS),
                }
            else:
                all_ops_meta[op] = {
                    "name": op, "group": "未知分组", "specialty": None,
                    "position": "", "dept": "未设置", "is_recovery_staff": False,
                }

    op_host_sns: dict[str, set[str]] = {}
    global_host_sns: set[str] = set()
    sn_first_op_time: dict[str, datetime] = {}
    sn_host_ops: dict[str, list[str]] = {}
    sn_host_reason: dict[str, str] = {}  # SN→归一化原因(从cs_record.problem_type)
    for r in cs_sn_rows:
        if not r.sn or not r.op:
            continue
        sn = r.sn
        op_host_sns.setdefault(r.op, set()).add(sn)
        global_host_sns.add(sn)
        if r.op_time and (sn not in sn_first_op_time or r.op_time < sn_first_op_time[sn]):
            sn_first_op_time[sn] = r.op_time
        sn_host_ops.setdefault(sn, [])
        if r.op not in sn_host_ops[sn]:
            sn_host_ops[sn].append(r.op)
        # 归一化原因(从cs_record.problem_type)
        if sn not in sn_host_reason:
            sn_host_reason[sn] = _normalize_refund_reason(r.problem_type)

    # ---- 4. 失败SN (refunds + tickets + cs_kw, T+7窗口) ----
    import datetime as _dt2
    te7 = te + _dt2.timedelta(days=7)
    CS_FAIL_KW = _RR_CS_FAIL_KW
    cs_kw_params = {f"ck{i+1}": f"%{k}%" for i, k in enumerate(CS_FAIL_KW)}
    failed_sn_sql = text(
        """
        WITH host_sns AS (SELECT unnest(CAST(:sn_arr AS text[])) AS sn),
        refund_sn AS (
            SELECT DISTINCT NULLIF(TRIM(r.device_sn),'') AS sn,
                   'refund' AS src, r.refund_reason AS reason,
                   COALESCE(r.actual_amount, r.refund_amount, 0) AS amount,
                   r.status AS status, r.refund_method AS method,
                   r.created_at_raw AS event_time
            FROM refunds r
            JOIN host_sns hs ON NULLIF(TRIM(r.device_sn),'') = hs.sn
            WHERE r.created_at_raw IS NOT NULL
              AND NULLIF(TRIM(r.device_sn),'') IS NOT NULL
              AND COALESCE(r.status, '') NOT IN ('无需退款', '驳回')
              AND r.created_at_raw >= :ts AND r.created_at_raw <= :te7
        ),
        ticket_sn AS (
            SELECT DISTINCT NULLIF(TRIM(st.device_sn),'') AS sn,
                   'ticket' AS src, st.service_item AS reason,
                   COALESCE(st.refund_amount, st.estimated_amount, 0) AS amount,
                   st.status AS status, st.service_category AS method,
                   st.register_time AS event_time
            FROM service_tickets st
            JOIN host_sns hs ON NULLIF(TRIM(st.device_sn),'') = hs.sn
            WHERE st.register_time IS NOT NULL
              AND NULLIF(TRIM(st.device_sn),'') IS NOT NULL
              AND COALESCE(st.service_category,'') = '退款'
              AND COALESCE(st.status, '') != '终止'
              AND st.register_time >= :ts AND st.register_time <= :te7
        ),
        cs_kw_sn AS (
            SELECT DISTINCT NULLIF(TRIM(c.device_sn),'') AS sn,
                   'cs_kw' AS src, LEFT(COALESCE(c.description,''),100) AS reason,
                   0 AS amount, '退款流程' AS status, 'cs_record关键词' AS method,
                   c.operation_time AS event_time
            FROM cs_records c
            JOIN host_sns hs ON NULLIF(TRIM(c.device_sn),'') = hs.sn
            WHERE c.operation_time IS NOT NULL
              AND NULLIF(TRIM(c.device_sn),'') IS NOT NULL
              AND c.problem_category = '退款'
              AND NOT (c.operator = ANY(:sys_bl))
              AND (
                  COALESCE(c.description,'') ILIKE ANY(ARRAY[:ck1,:ck2,:ck3,:ck4])
               OR COALESCE(c.execution_result,'') ILIKE ANY(ARRAY[:ck1,:ck2,:ck3,:ck4])
               OR COALESCE(c.solution,'') ILIKE ANY(ARRAY[:ck1,:ck2,:ck3,:ck4])
              )
              AND c.operation_time >= :ts AND c.operation_time <= :te7
        ),
        all_fail AS (
            SELECT sn, src, reason, amount, status, method, event_time FROM refund_sn
            UNION ALL SELECT sn, src, reason, amount, status, method, event_time FROM ticket_sn
            UNION ALL SELECT sn, src, reason, amount, status, method, event_time FROM cs_kw_sn
        )
        SELECT af.sn, af.src, af.reason, af.amount, af.status, af.method, af.event_time
        FROM all_fail af JOIN host_sns hs ON af.sn = hs.sn
        """
    )
    fail_rows = db.execute(
        failed_sn_sql,
        {"sn_arr": list(global_host_sns), "ts": ts, "te7": te7,
         "sys_bl": list(SYS_OP_BLACKLIST_RR), **cs_kw_params},
    ).fetchall()

    global_fail_sns: set[str] = set()
    sn_failed_meta: dict[str, dict] = {}
    for r in fail_rows:
        sn = r.sn or ""
        if not sn:
            continue
        first_op = sn_first_op_time.get(sn)
        if not first_op or not r.event_time:
            continue
        window_end = first_op + _dt2.timedelta(days=7)
        if not (first_op <= r.event_time <= window_end):
            continue
        global_fail_sns.add(sn)
        amt = float(r.amount or 0)
        prev = sn_failed_meta.get(sn)
        if prev is None or amt > float(prev.get("amount") or 0):
            sn_failed_meta[sn] = {"src": r.src, "reason": r.reason, "amount": amt,
                                   "status": r.status, "method": r.method}

    # ---- 4.6 退款操作执行人 ----
    refund_op_sns: dict[str, set[str]] = {}
    refund_op_amt: dict[str, float] = {}
    for r in db.execute(text(
        """
        SELECT NULLIF(TRIM(r.device_sn),'') AS sn, r.operator AS op,
               COALESCE(r.actual_amount, r.refund_amount, 0) AS amt
        FROM refunds r
        WHERE r.created_at_raw IS NOT NULL
          AND r.created_at_raw >= :ts AND r.created_at_raw <= :te7
          AND NULLIF(TRIM(r.device_sn),'') IS NOT NULL
          AND r.operator IS NOT NULL AND NOT (r.operator = ANY(:sys_bl))
          AND COALESCE(r.status, '') NOT IN ('无需退款', '驳回')
        """
    ), {"ts": ts, "te7": te7, "sys_bl": list(SYS_OP_BLACKLIST_RR)}).fetchall():
        if not r.sn or not r.op:
            continue
        refund_op_sns.setdefault(r.op, set()).add(r.sn)
        refund_op_amt[r.op] = refund_op_amt.get(r.op, 0.0) + float(r.amt or 0)
    for r in db.execute(text(
        """
        SELECT NULLIF(TRIM(st.device_sn),'') AS sn, st.handler AS op,
               COALESCE(st.refund_amount, st.estimated_amount, 0) AS amt
        FROM service_tickets st
        WHERE st.finish_time IS NOT NULL
          AND st.finish_time >= :ts AND st.finish_time <= :te7
          AND NULLIF(TRIM(st.device_sn),'') IS NOT NULL
          AND st.handler IS NOT NULL AND NOT (st.handler = ANY(:sys_bl))
          AND COALESCE(st.service_category,'') = '退款'
          AND COALESCE(st.status, '') = '已完成'
        """
    ), {"ts": ts, "te7": te7, "sys_bl": list(SYS_OP_BLACKLIST_RR)}).fetchall():
        if not r.sn or not r.op:
            continue
        refund_op_sns.setdefault(r.op, set()).add(r.sn)
        refund_op_amt[r.op] = refund_op_amt.get(r.op, 0.0) + float(r.amt or 0)

    # ---- 5. staff_detail ----
    staff_operators: dict[str, dict] = dict(all_ops_meta)
    for op in refund_op_sns.keys():
        if op not in staff_operators and op not in SYS_OP_BLACKLIST_RR:
            staff_operators[op] = {"name": op, "group": "退款执行", "specialty": None,
                                    "position": "退款执行人", "dept": "售后"}
    for pr in person_rows:
        op = pr.operator or ""
        if op and op in staff_operators:
            info = staff_operators[op]
            if not info.get("group") or info.get("group") == "退款执行":
                info["group"] = pr.group_name or info.get("group") or "未分组"
            if not info.get("position") or info.get("position") == "退款执行人":
                info["position"] = pr.position or info.get("position")
            info["dept"] = pr.department or info.get("dept") or "未设置"
            info["name"] = pr.display_name or op

    staff_detail = []
    for op, info in staff_operators.items():
        cs_host = op_host_sns.get(op, set())
        cs_failed = cs_host & global_fail_sns
        cs_success = cs_host - global_fail_sns
        total_cs = len(cs_host)
        fail_cs = len(cs_failed)
        succ_cs = len(cs_success)
        ro_sns = refund_op_sns.get(op, set())
        ro_cnt = len(ro_sns)
        ro_amt = refund_op_amt.get(op, 0.0)
        if total_cs == 0 and ro_cnt == 0:
            continue
        total_for_rate = total_cs
        rate = (succ_cs / total_for_rate * 100) if total_for_rate else None
        staff_detail.append({
            "operator": op,
            "name": info.get("name") or op,
            "person_id": info.get("person_id"),
            "group": info.get("group") or "未分组",
            "department": info.get("dept") or "未设置",
            "position": info.get("position") or "",
            "total_sn": total_cs,
            "success_sn": succ_cs,
            "failed_sn": fail_cs,
            "success_rate": round(rate, 2) if rate is not None else None,
            "refund_op_cnt": ro_cnt,
            "refund_op_amount": round(ro_amt, 2),
            "_is_recovery_staff": info.get("is_recovery_staff", False),
            "staff_type": "专职挽回" if info.get("is_recovery_staff", False) else ("退款操作" if total_cs == 0 else "其他人员挽回"),
        })
    staff_detail.sort(key=lambda x: (-(x.get("total_sn") or 0), -(x.get("refund_op_cnt") or 0), -(x.get("success_rate") or -1)))

    return {
        "all_ops_meta": all_ops_meta,
        "recovery_ops": recovery_ops,
        "op_host_sns": op_host_sns,
        "global_host_sns": global_host_sns,
        "sn_first_op_time": sn_first_op_time,
        "sn_host_ops": sn_host_ops,
        "sn_host_reason": sn_host_reason,
        "global_fail_sns": global_fail_sns,
        "sn_failed_meta": sn_failed_meta,
        "refund_op_sns": refund_op_sns,
        "refund_op_amt": refund_op_amt,
        "staff_detail": staff_detail,
        "ts": ts, "te": te,
    }



# ==============================================================
# 退款看板深度分析 - 9 个新接口函数 (2026-08-12)
# ==============================================================
def refund_personnel_analysis(
    db: Session, start: str | None = None, end: str | None = None,
    department: str | None = None, group: str | None = None,
    specialty: str | None = None, staff_type: str | None = None,
) -> dict[str, Any]:
    """接口1: 人员分析汇总 - 强化版 staff_detail + 估算金额 + 首末接待时间."""
    cur_s, cur_e, _, _, _, _ = _resolve_windows(None, start, end)
    core = _refund_recovery_core(db, cur_s, cur_e)
    staff_detail = core["staff_detail"]
    global_fail_sns = core["global_fail_sns"]
    sn_failed_meta = core["sn_failed_meta"]
    op_host_sns = core["op_host_sns"]
    all_ops_meta = core["all_ops_meta"]

    # 计算各原因类别的失败平均金额 (用于估算挽回金额)
    reason_fail_avg: dict[str, float] = {}
    reason_fail_cnt: dict[str, int] = {}
    reason_fail_sum: dict[str, float] = {}
    for sn in global_fail_sns:
        meta = sn_failed_meta.get(sn, {})
        reason = _normalize_refund_reason(meta.get("reason"))
        amt = float(meta.get("amount") or 0)
        reason_fail_sum[reason] = reason_fail_sum.get(reason, 0.0) + amt
        reason_fail_cnt[reason] = reason_fail_cnt.get(reason, 0) + 1
    for r, s in reason_fail_sum.items():
        c = reason_fail_cnt.get(r, 0)
        reason_fail_avg[r] = (s / c) if c else 0.0
    overall_fail_avg = sum(reason_fail_sum.values()) / max(sum(reason_fail_cnt.values()), 1)

    # 查询每个人员的首末接待时间
    op_first_reception: dict[str, datetime] = {}
    op_last_reception: dict[str, datetime] = {}
    for r in db.execute(text(
        """
        SELECT c.operator AS op, MIN(c.operation_time) AS first_t, MAX(c.operation_time) AS last_t
        FROM cs_records c
        WHERE c.operation_time IS NOT NULL
          AND c.operation_time >= :ts AND c.operation_time <= :te
          AND NOT (c.operator = ANY(:sys_bl))
          AND c.problem_category = '退款'
        GROUP BY c.operator
        """
    ), {"ts": cur_s, "te": cur_e, "sys_bl": list(SYS_OP_BLACKLIST_RR)}).fetchall():
        if r.op:
            op_first_reception[r.op] = r.first_t
            op_last_reception[r.op] = r.last_t

    # 筛选
    def _match(sd):
        if department and sd.get("department") != department:
            return False
        if group and sd.get("group") != group:
            return False
        if specialty:
            meta = all_ops_meta.get(sd["operator"], {})
            if meta.get("specialty") != specialty:
                return False
        if staff_type and sd.get("staff_type") != staff_type:
            return False
        return True

    filtered = [sd for sd in staff_detail if _match(sd)]

    # 补充估算金额 + 首末接待时间
    for sd in filtered:
        op = sd["operator"]
        host_sns = op_host_sns.get(op, set())
        success_sns = host_sns - global_fail_sns
        # 估算挽回金额 = 成功SN数 × 同类失败平均金额
        est_amt = 0.0
        for sn in success_sns:
            sn_reason = core["sn_host_reason"].get(sn, "其他(填写备注)")
            avg = reason_fail_avg.get(sn_reason, overall_fail_avg)
            est_amt += avg
        sd["estimated_save_amount"] = round(est_amt, 2)
        sd["first_reception"] = op_first_reception.get(op).isoformat() if op_first_reception.get(op) else None
        sd["last_reception"] = op_last_reception.get(op).isoformat() if op_last_reception.get(op) else None

    total_sn = sum(sd["total_sn"] for sd in filtered)
    total_refund_op_amount = sum(sd["refund_op_amount"] for sd in filtered)
    rates = [sd["success_rate"] for sd in filtered if sd["success_rate"] is not None]
    avg_rate = round(sum(rates) / len(rates), 2) if rates else 0.0

    return {
        "staff_detail": filtered,
        "summary": {
            "total_persons": len(filtered),
            "total_sn": total_sn,
            "avg_success_rate": avg_rate,
            "total_refund_op_amount": round(total_refund_op_amount, 2),
        },
    }

def refund_person_devices(
    db: Session, operator: str, start: str | None = None, end: str | None = None,
    result: str = "all",
) -> dict[str, Any]:
    """接口2: 人员接待设备清单 - 某人员周期内接待的所有SN + 成功/失败明细."""
    cur_s, cur_e, _, _, _, _ = _resolve_windows(None, start, end)
    core = _refund_recovery_core(db, cur_s, cur_e)
    host_sns = core["op_host_sns"].get(operator, set())
    global_fail_sns = core["global_fail_sns"]
    sn_failed_meta = core["sn_failed_meta"]
    core["sn_host_reason"]
    all_ops_meta = core["all_ops_meta"]

    meta = all_ops_meta.get(operator, {})
    name = meta.get("name", operator)

    # 查该人员接待每个SN的次数和首末时间
    sn_reception_stats: dict[str, dict] = {}
    for r in db.execute(text(
        """
        SELECT NULLIF(TRIM(c.device_sn),'') AS sn,
               MIN(c.operation_time) AS first_t, MAX(c.operation_time) AS last_t,
               COUNT(*) AS cnt
        FROM cs_records c
        WHERE c.operation_time IS NOT NULL
          AND c.operation_time >= :ts AND c.operation_time <= :te
          AND c.operator = :op
          AND c.problem_category = '退款'
          AND NULLIF(TRIM(c.device_sn),'') IS NOT NULL
        GROUP BY NULLIF(TRIM(c.device_sn),'')
        """
    ), {"ts": cur_s, "te": cur_e, "op": operator}).fetchall():
        if r.sn:
            sn_reception_stats[r.sn] = {"first_t": r.first_t, "last_t": r.last_t, "cnt": r.cnt}

    devices = []
    for sn in host_sns:
        is_failed = sn in global_fail_sns
        res = "failed" if is_failed else "success"
        if result != "all" and result != res:
            continue
        stats = sn_reception_stats.get(sn, {})
        fail_meta = sn_failed_meta.get(sn, {}) if is_failed else {}
        devices.append({
            "device_sn": sn,
            "first_reception_time": stats.get("first_t").isoformat() if stats.get("first_t") else None,
            "last_reception_time": stats.get("last_t").isoformat() if stats.get("last_t") else None,
            "reception_count": stats.get("cnt", 0),
            "result": res,
            "fail_reason": _normalize_refund_reason(fail_meta.get("reason")) if is_failed else None,
            "refund_amount": round(float(fail_meta.get("amount") or 0), 2) if is_failed else 0.0,
            "refund_time": None,  # 下面补
            "final_status": fail_meta.get("status") if is_failed else None,
            "refund_no": None,
        })

    # 补退款时间和单号
    if host_sns:
        for r in db.execute(text(
            """
            SELECT NULLIF(TRIM(r.device_sn),'') AS sn, r.refund_no, r.created_at_raw, r.status,
                   COALESCE(r.actual_amount, r.refund_amount, 0) AS amt
            FROM refunds r
            WHERE r.created_at_raw IS NOT NULL
              AND r.created_at_raw >= :ts AND r.created_at_raw <= :te7
              AND NULLIF(TRIM(r.device_sn),'') = ANY(:sn_arr)
              AND COALESCE(r.status, '') NOT IN ('无需退款', '驳回')
            """
        ), {"ts": cur_s, "te7": cur_e, "sn_arr": list(host_sns)}).fetchall():
            if not r.sn:
                continue
            for d in devices:
                if d["device_sn"] == r.sn and d["result"] == "failed":
                    d["refund_time"] = r.created_at_raw.isoformat() if r.created_at_raw else None
                    d["refund_no"] = r.refund_no
                    break

    devices.sort(key=lambda x: x.get("first_reception_time") or "", reverse=True)
    succ_cnt = sum(1 for d in devices if d["result"] == "success")
    fail_cnt = sum(1 for d in devices if d["result"] == "failed")
    total_amt = sum(d["refund_amount"] for d in devices if d["result"] == "failed")

    return {
        "operator": operator, "name": name,
        "devices": devices,
        "summary": {"total": len(devices), "success": succ_cnt, "failed": fail_cnt,
                     "total_refund_amount": round(total_amt, 2)},
    }

def refund_personnel_trend(
    db: Session, start: str | None = None, end: str | None = None,
    operators: str | None = None, grain: str = "day",
) -> dict[str, Any]:
    """接口3: 人员趋势 - 按人员拆分的日/周成功率趋势."""
    cur_s, cur_e, _, _, _, _ = _resolve_windows(None, start, end)
    core = _refund_recovery_core(db, cur_s, cur_e)
    staff_detail = core["staff_detail"]
    global_fail_sns = core["global_fail_sns"]
    core["op_host_sns"]

    # 选人: 有 operators 参数用指定的, 否则取 Top10 (按接待量)
    if operators:
        op_list = [o.strip() for o in operators.split(",") if o.strip()]
    else:
        op_list = [sd["operator"] for sd in staff_detail[:10] if sd["total_sn"] > 0]

    # 生成日期序列
    start_day = cur_s.astimezone(TZ).date()
    end_day = cur_e.astimezone(TZ).date()
    dates_list = []
    cur_d = start_day
    while cur_d <= end_day:
        dates_list.append(cur_d)
        cur_d += _dt.timedelta(days=1)

    # 按日查每个人员的接待SN
    day_op_host: dict[str, dict[str, set[str]]] = {}  # date_str -> op -> set(sn)
    if grain == "week":
        # 周粒度: 按ISO周聚合
        week_op_host: dict[str, dict[str, set[str]]] = {}
        for r in db.execute(text(
            """
            SELECT c.operator AS op, NULLIF(TRIM(c.device_sn),'') AS sn,
                   DATE_TRUNC('week', c.operation_time AT TIME ZONE 'Asia/Shanghai')::date AS wk
            FROM cs_records c
            WHERE c.operation_time IS NOT NULL
              AND c.operation_time >= :ts AND c.operation_time <= :te
              AND NOT (c.operator = ANY(:sys_bl))
              AND c.problem_category = '退款'
              AND NULLIF(TRIM(c.device_sn),'') IS NOT NULL
            """
        ), {"ts": cur_s, "te": cur_e, "sys_bl": list(SYS_OP_BLACKLIST_RR)}).fetchall():
            if r.op and r.sn and r.wk:
                key = str(r.wk)
                week_op_host.setdefault(key, {}).setdefault(r.op, set()).add(r.sn)
        day_op_host = week_op_host
        dates_list = sorted(set(day_op_host.keys()))
    else:
        for r in db.execute(text(
            """
            SELECT c.operator AS op, NULLIF(TRIM(c.device_sn),'') AS sn,
                   DATE(c.operation_time AT TIME ZONE 'Asia/Shanghai') AS d
            FROM cs_records c
            WHERE c.operation_time IS NOT NULL
              AND c.operation_time >= :ts AND c.operation_time <= :te
              AND NOT (c.operator = ANY(:sys_bl))
              AND c.problem_category = '退款'
              AND NULLIF(TRIM(c.device_sn),'') IS NOT NULL
            """
        ), {"ts": cur_s, "te": cur_e, "sys_bl": list(SYS_OP_BLACKLIST_RR)}).fetchall():
            if r.op and r.sn and r.d:
                key = str(r.d)
                day_op_host.setdefault(key, {}).setdefault(r.op, set()).add(r.sn)

    persons = []
    for op in op_list:
        meta = core["all_ops_meta"].get(op, {})
        trend = []
        for d_key in dates_list:
            # day_op_host 的 key 是 str(r.d)('YYYY-MM-DD'), 而 dates_list 存的是 date 对象 → 必须 str() 统一
            host = day_op_host.get(str(d_key), {}).get(op, set())
            fail = host & global_fail_sns
            total = len(host)
            succ = total - len(fail)
            rate = round(succ / total * 100, 2) if total else 0.0
            trend.append({"date": str(d_key), "total_sn": total, "success_sn": succ,
                           "failed_sn": len(fail), "success_rate": rate})
        persons.append({"operator": op, "name": meta.get("name", op), "trend": trend})

    return {"dates": [str(d) for d in dates_list], "persons": persons}

def refund_person_reason_matrix(
    db: Session, start: str | None = None, end: str | None = None, top: int = 15,
) -> dict[str, Any]:
    """接口4: 人员×原因 热力图矩阵 - 每个人员在7大原因下的接待量/成功率."""
    cur_s, cur_e, _, _, _, _ = _resolve_windows(None, start, end)
    core = _refund_recovery_core(db, cur_s, cur_e)
    staff_detail = core["staff_detail"]
    global_fail_sns = core["global_fail_sns"]
    op_host_sns = core["op_host_sns"]
    sn_host_reason = core["sn_host_reason"]

    reasons = ["不想用了", "充值错误", "设备故障", "网卡/卡无网络",
               "套餐问题(流量虚、涨价、升级)", "卡商问题", "其他(填写备注)"]

    # 取 Top N 人员 (按接待量)
    top_persons = [sd for sd in staff_detail if sd["total_sn"] > 0][:top]
    persons = []
    for sd in top_persons:
        op = sd["operator"]
        host_sns = op_host_sns.get(op, set())
        cells = []
        for reason in reasons:
            reason_sns = {sn for sn in host_sns if sn_host_reason.get(sn, "其他(填写备注)") == reason}
            fail_sns = reason_sns & global_fail_sns
            total = len(reason_sns)
            succ = total - len(fail_sns)
            rate = round(succ / total * 100, 2) if total else None
            cells.append({"reason": reason, "total_sn": total, "success_sn": succ,
                           "failed_sn": len(fail_sns), "success_rate": rate})
        persons.append({"operator": op, "name": sd["name"], "cells": cells})

    return {"reasons": reasons, "persons": persons}

def refund_device_analysis(
    db: Session, start: str | None = None, end: str | None = None,
    reason: str | None = None, amount_min: float | None = None,
    amount_max: float | None = None, reception_count_min: int | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """接口5: 设备分析 - Top风险设备 + 时差分布 + 接待次数分布."""
    cur_s, cur_e, _, _, _, _ = _resolve_windows(None, start, end)
    core = _refund_recovery_core(db, cur_s, cur_e)
    global_fail_sns = core["global_fail_sns"]
    sn_failed_meta = core["sn_failed_meta"]
    sn_host_ops = core["sn_host_ops"]
    sn_first_op_time = core["sn_first_op_time"]
    all_ops_meta = core["all_ops_meta"]

    # 失败设备明细
    devices = []
    for sn in global_fail_sns:
        meta = sn_failed_meta.get(sn, {})
        fail_reason = _normalize_refund_reason(meta.get("reason"))
        if reason and fail_reason != reason:
            continue
        amt = float(meta.get("amount") or 0)
        if amount_min is not None and amt < amount_min:
            continue
        if amount_max is not None and amt > amount_max:
            continue
        ops = sn_host_ops.get(sn, [])
        op_names = [all_ops_meta.get(op, {}).get("name", op) for op in ops]
        first_op = sn_first_op_time.get(sn)
        # 查退款时间和单号
        refund_info = {}
        r_row = db.execute(text(
            """
            SELECT r.refund_no, r.created_at_raw, r.status
            FROM refunds r
            WHERE r.device_sn = :sn AND r.created_at_raw IS NOT NULL
              AND COALESCE(r.status, '') NOT IN ('无需退款', '驳回')
            ORDER BY r.created_at_raw DESC LIMIT 1
            """
        ), {"sn": sn}).fetchone()
        if r_row:
            refund_info = {"refund_no": r_row.refund_no, "refund_time": r_row.created_at_raw,
                           "final_status": r_row.status}
        devices.append({
            "device_sn": sn, "reception_count": len(ops), "receptionist_count": len(set(ops)),
            "receptionists": op_names, "fail_reason": fail_reason,
            "refund_amount": round(amt, 2),
            "refund_time": refund_info.get("refund_time").isoformat() if refund_info.get("refund_time") else None,
            "final_status": refund_info.get("final_status") or meta.get("status"),
            "refund_no": refund_info.get("refund_no"),
            "first_reception": first_op.isoformat() if first_op else None,
        })

    # 接待次数筛选
    if reception_count_min is not None:
        devices = [d for d in devices if d["reception_count"] >= reception_count_min]

    # 排序: 按退款金额倒序
    devices.sort(key=lambda x: -x["refund_amount"])
    top_devices = devices[:limit]

    # 风险摘要
    repeat_refund = set()
    # 查复退款设备(SN在周期内出现>=2次退款诉求,即cs_record接待>=2次)
    for r in db.execute(text(
        """
        SELECT NULLIF(TRIM(c.device_sn),'') AS sn, COUNT(*) AS cnt
        FROM cs_records c
        WHERE c.operation_time IS NOT NULL
          AND c.operation_time >= :ts AND c.operation_time <= :te
          AND c.problem_category = '退款'
          AND NULLIF(TRIM(c.device_sn),'') IS NOT NULL
        GROUP BY NULLIF(TRIM(c.device_sn),'')
        HAVING COUNT(*) >= 2
        """
    ), {"ts": cur_s, "te": cur_e}).fetchall():
        if r.sn:
            repeat_refund.add(r.sn)

    cross_person = {sn for sn in global_fail_sns if len(set(sn_host_ops.get(sn, []))) >= 2}
    high_amount = {sn for sn in global_fail_sns if float(sn_failed_meta.get(sn, {}).get("amount") or 0) > 1000}

    # 接待→流失时差分布
    loss_buckets = {"当天": [], "1-3天": [], "3-7天": [], "7-15天": [], "15天+": []}
    for d in devices:
        first_op = sn_first_op_time.get(d["device_sn"])
        refund_time = d.get("refund_time")
        if first_op and refund_time:
            from datetime import datetime as _dt_tmp
            try:
                rt = _dt_tmp.fromisoformat(d["refund_time"].replace("Z", "+00:00")) if isinstance(d["refund_time"], str) else d["refund_time"]
                diff_days = (rt - first_op).days if hasattr(rt, 'days') else (rt - first_op).days
                if diff_days <= 0:
                    loss_buckets["当天"].append(d)
                elif diff_days <= 3:
                    loss_buckets["1-3天"].append(d)
                elif diff_days <= 7:
                    loss_buckets["3-7天"].append(d)
                elif diff_days <= 15:
                    loss_buckets["7-15天"].append(d)
                else:
                    loss_buckets["15天+"].append(d)
            except Exception:
                pass
    loss_time_dist = []
    for bucket, items in loss_buckets.items():
        loss_time_dist.append({"bucket": bucket, "sn_cnt": len(items),
                                "amount": round(sum(x["refund_amount"] for x in items), 2)})

    # 设备接待次数分布
    rc_buckets = {"1次": [], "2次": [], "3次": [], "4次": [], "5次+": []}
    all_host_sns = core["global_host_sns"]
    sn_rc_cnt: dict[str, int] = {}
    for r in db.execute(text(
        """
        SELECT NULLIF(TRIM(c.device_sn),'') AS sn, COUNT(DISTINCT c.operator) AS op_cnt
        FROM cs_records c
        WHERE c.operation_time IS NOT NULL
          AND c.operation_time >= :ts AND c.operation_time <= :te
          AND c.problem_category = '退款'
          AND NULLIF(TRIM(c.device_sn),'') IS NOT NULL
        GROUP BY NULLIF(TRIM(c.device_sn),'')
        """
    ), {"ts": cur_s, "te": cur_e}).fetchall():
        if r.sn:
            sn_rc_cnt[r.sn] = r.op_cnt
    for sn in all_host_sns:
        cnt = sn_rc_cnt.get(sn, 1)
        is_fail = sn in global_fail_sns
        if cnt == 1: rc_buckets["1次"].append((sn, is_fail))
        elif cnt == 2: rc_buckets["2次"].append((sn, is_fail))
        elif cnt == 3: rc_buckets["3次"].append((sn, is_fail))
        elif cnt == 4: rc_buckets["4次"].append((sn, is_fail))
        else: rc_buckets["5次+"].append((sn, is_fail))
    reception_count_dist = []
    for bucket, items in rc_buckets.items():
        total = len(items)
        fail = sum(1 for _, f in items if f)
        rate = round(fail / total * 100, 2) if total else 0.0
        reception_count_dist.append({"bucket": bucket, "device_cnt": total, "fail_rate": rate})

    return {
        "risk_summary": {
            "repeat_refund_devices": len(repeat_refund & global_fail_sns),
            "cross_person_devices": len(cross_person),
            "high_amount_devices": len(high_amount),
            "total_failed_devices": len(global_fail_sns),
        },
        "top_devices": top_devices,
        "loss_time_dist": loss_time_dist,
        "reception_count_dist": reception_count_dist,
    }

def refund_device_detail(
    db: Session, device_sn: str, start: str | None = None, end: str | None = None,
) -> dict[str, Any]:
    """接口6: 设备详情 - 接待时间线 + 退款记录 + 工单 + 钱包."""
    cur_s, cur_e, _, _, _, _ = _resolve_windows(None, start, end)
    core = _refund_recovery_core(db, cur_s, cur_e)
    global_fail_sns = core["global_fail_sns"]
    sn_failed_meta = core["sn_failed_meta"]
    sn_host_ops = core["sn_host_ops"]
    sn_first_op_time = core["sn_first_op_time"]
    all_ops_meta = core["all_ops_meta"]

    is_failed = device_sn in global_fail_sns
    fail_meta = sn_failed_meta.get(device_sn, {})
    ops = sn_host_ops.get(device_sn, [])
    op_names = [all_ops_meta.get(op, {}).get("name", op) for op in ops]
    first_op = sn_first_op_time.get(device_sn)

    # 接待时间线
    timeline = []
    for r in db.execute(text(
        """
        SELECT c.operation_time, c.operator, c.problem_type, c.description,
               c.execution_result, c.solution, c.consult_time
        FROM cs_records c
        WHERE c.operation_time IS NOT NULL
          AND c.operation_time >= :ts AND c.operation_time <= :te
          AND c.device_sn = :sn
          AND c.problem_category = '退款'
        ORDER BY c.operation_time ASC
        """
    ), {"ts": cur_s, "te": cur_e, "sn": device_sn}).fetchall():
        meta = all_ops_meta.get(r.operator, {})
        timeline.append({
            "time": r.operation_time.isoformat() if r.operation_time else None,
            "operator": r.operator, "operator_name": meta.get("name", r.operator),
            "problem_type": r.problem_type, "description": (r.description or "")[:200],
            "execution_result": (r.execution_result or "")[:200],
            "consult_time": r.consult_time,
        })

    last_op_time = timeline[-1]["time"] if timeline else (first_op.isoformat() if first_op else None)

    # 退款记录
    refund_records = []
    for r in db.execute(text(
        """
        SELECT r.refund_no, r.refund_amount, r.actual_amount, r.refund_method,
               r.status, r.operator, r.auditor, r.created_at_raw, r.refund_reason
        FROM refunds r
        WHERE r.device_sn = :sn AND r.created_at_raw IS NOT NULL
        ORDER BY r.created_at_raw DESC
        LIMIT 10
        """
    ), {"sn": device_sn}).fetchall():
        refund_records.append({
            "refund_no": r.refund_no, "refund_amount": float(r.refund_amount or 0),
            "actual_amount": float(r.actual_amount or 0) if r.actual_amount else None,
            "refund_method": r.refund_method, "status": r.status,
            "operator": r.operator, "auditor": r.auditor,
            "created_at_raw": r.created_at_raw.isoformat() if r.created_at_raw else None,
            "refund_reason": r.refund_reason,
        })

    # 关联工单
    tickets = []
    for r in db.execute(text(
        """
        SELECT st.ticket_no, st.service_item, st.service_category,
               st.register_time, st.finish_time, st.handler, st.status
        FROM service_tickets st
        WHERE st.device_sn = :sn AND st.register_time IS NOT NULL
        ORDER BY st.register_time DESC LIMIT 10
        """
    ), {"sn": device_sn}).fetchall():
        tickets.append({
            "ticket_no": r.ticket_no, "service_item": r.service_item,
            "service_category": r.service_category,
            "register_time": r.register_time.isoformat() if r.register_time else None,
            "finish_time": r.finish_time.isoformat() if r.finish_time else None,
            "handler": r.handler, "status": r.status,
        })

    # 钱包提现记录
    wallet_records = []
    for r in db.execute(text(
        """
        SELECT w.id, w.op_time, w.op_amount, w.op_type
        FROM wallet_withdrawals w
        WHERE w.device_sn = :sn AND w.op_time IS NOT NULL
        ORDER BY w.op_time DESC LIMIT 10
        """
    ), {"sn": device_sn}).fetchall():
        wallet_records.append({
            "wallet_id": str(r.id), "op_time": r.op_time.isoformat() if r.op_time else None,
            "op_amount": float(r.op_amount or 0), "op_type": r.op_type,
        })

    # 设备型号
    device_type = None
    try:
        for r in db.execute(text("SELECT device_type FROM devices WHERE sn = :sn LIMIT 1"), {"sn": device_sn}).fetchall():
            device_type = r.device_type
    except Exception:
        pass

    return {
        "device_sn": device_sn, "device_type": device_type,
        "first_reception": first_op.isoformat() if first_op else None,
        "last_reception": last_op_time,
        "reception_count": len(timeline),
        "receptionists": op_names,
        "result": "failed" if is_failed else "success",
        "fail_reason": _normalize_refund_reason(fail_meta.get("reason")) if is_failed else None,
        "refund_amount": round(float(fail_meta.get("amount") or 0), 2) if is_failed else 0.0,
        "refund_time": refund_records[0]["created_at_raw"] if refund_records else None,
        "refund_no": refund_records[0]["refund_no"] if refund_records else None,
        "final_status": fail_meta.get("status") if is_failed else None,
        "reception_timeline": timeline,
        "refund_records": refund_records,
        "tickets": tickets,
        "wallet_records": wallet_records,
    }

def refund_type_trend(
    db: Session, start: str | None = None, end: str | None = None,
    grain: str = "day", dimension: str = "reason",
) -> dict[str, Any]:
    """接口7: 类型趋势 - 按原因/人员类型拆分的日/周趋势."""
    cur_s, cur_e, _, _, _, _ = _resolve_windows(None, start, end)
    core = _refund_recovery_core(db, cur_s, cur_e)
    global_fail_sns = core["global_fail_sns"]
    core["sn_host_reason"]
    core["sn_host_ops"]
    core["all_ops_meta"]
    staff_detail = core["staff_detail"]

    # 生成日期序列
    start_day = cur_s.astimezone(TZ).date()
    end_day = cur_e.astimezone(TZ).date()
    dates_list = []
    cur_d = start_day
    while cur_d <= end_day:
        dates_list.append(cur_d)
        cur_d += _dt.timedelta(days=1)

    # 按日查每个SN的接待和原因
    day_sn_reason: dict[str, dict[str, str]] = {}  # date -> sn -> reason
    day_sn_staff_type: dict[str, dict[str, str]] = {}  # date -> sn -> staff_type

    # 构建人员→staff_type映射
    op_staff_type = {sd["operator"]: sd["staff_type"] for sd in staff_detail}

    if grain == "week":
        date_col = "DATE_TRUNC('week', c.operation_time AT TIME ZONE 'Asia/Shanghai')::date"
    else:
        date_col = "DATE(c.operation_time AT TIME ZONE 'Asia/Shanghai')"

    for r in db.execute(text(
        f"""
        SELECT {date_col} AS d, NULLIF(TRIM(c.device_sn),'') AS sn,
               c.problem_type, c.operator
        FROM cs_records c
        WHERE c.operation_time IS NOT NULL
          AND c.operation_time >= :ts AND c.operation_time <= :te
          AND NOT (c.operator = ANY(:sys_bl))
          AND c.problem_category = '退款'
          AND NULLIF(TRIM(c.device_sn),'') IS NOT NULL
        """
    ), {"ts": cur_s, "te": cur_e, "sys_bl": list(SYS_OP_BLACKLIST_RR)}).fetchall():
        if r.sn and r.d:
            key = str(r.d)
            if dimension == "reason":
                day_sn_reason.setdefault(key, {})[r.sn] = _normalize_refund_reason(r.problem_type)
            else:
                st = op_staff_type.get(r.operator, "其他人员挽回")
                day_sn_staff_type.setdefault(key, {})[r.sn] = st

    if dimension == "reason":
        reasons = ["不想用了", "充值错误", "设备故障", "网卡/卡无网络",
                   "套餐问题(流量虚、涨价、升级)", "卡商问题", "其他(填写备注)"]
        series = []
        for reason in reasons:
            trend = []
            for d_key in [str(d) for d in dates_list]:
                sn_reason_map = day_sn_reason.get(d_key, {})
                reason_sns = {sn for sn, r in sn_reason_map.items() if r == reason}
                fail = reason_sns & global_fail_sns
                total = len(reason_sns)
                succ = total - len(fail)
                rate = round(succ / total * 100, 2) if total else 0.0
                trend.append({"date": d_key, "total_sn": total, "success_sn": succ,
                               "failed_sn": len(fail), "success_rate": rate})
            series.append({"reason": reason, "trend": trend})
        return {"dates": [str(d) for d in dates_list], "reasons": reasons, "series": series}
    else:
        staff_types = ["专职挽回", "其他人员挽回", "退款操作"]
        series = []
        for st in staff_types:
            trend = []
            for d_key in [str(d) for d in dates_list]:
                sn_st_map = day_sn_staff_type.get(d_key, {})
                st_sns = {sn for sn, s in sn_st_map.items() if s == st}
                fail = st_sns & global_fail_sns
                total = len(st_sns)
                succ = total - len(fail)
                rate = round(succ / total * 100, 2) if total else 0.0
                trend.append({"date": d_key, "total_sn": total, "success_sn": succ,
                               "failed_sn": len(fail), "success_rate": rate})
            series.append({"staff_type": st, "trend": trend})
        return {"dates": [str(d) for d in dates_list], "staff_types": staff_types, "series": series}

def refund_decision_overview(
    db: Session, start: str | None = None, end: str | None = None, grain: str = "day",
) -> dict[str, Any]:
    """退款决策概览 (2026-08-23 v4: 四路经营 + E 独立对账 + 登记退款水位)

    五大块, 每块对应一个管理决策问题:
    1. summary         - KPI + 自动结论(钱出去多少/环比/涉诉占比/钱包水位/对账偏差)
    2. routes_daily    - 资金流出全景: A线上直退/B钱包提现/C闪猫退款/D手动退款 每日
                          + E实际现金流(商户号SUCCESS,对账基准,独立展示不累加)
    3. wallet_water    - 钱包水位: 转入/转出/净水位/系统扣费 每日
    4. manual_water    - 手动退款登记水位: 登记 vs 已退 = 蓄水 每日
    5. complaint_split - 涉诉 vs 普通 (线上直退, SN关联工单分类: 外部>商户号>内部 优先级)
    6. reason_operator - 退款原因Top + 操作人员Top (线上直退)
    """
    cur_s, cur_e, prev_s, prev_e, _rt, _disp = _resolve_windows(None, start, end)

    def _day(col: str) -> str:
        # 注意: 会话时区为 Etc/UTC, 必须用 col AT TIME ZONE 'Asia/Shanghai' 得到上海本地
        # naive 时间后再 DATE(), 不能先转 UTC 再转上海(那会导致 DATE(timestamptz) 按 UTC 截断-8h)
        expr = f"{col} AT TIME ZONE 'Asia/Shanghai'"
        return f"DATE({expr})" if grain == "day" else f"DATE_TRUNC('week', {expr})::date"

    # ── 2. 钱包水位 (转入/转出/系统扣费 按日) ──
    #    2026-08-28: 排除"提现申请-冻结"等失败/冻结备注(冻结非真实出账, 不属"用户提现"转出)
    wallet_rows = db.execute(text(f"""
        SELECT {_day("w.op_time")} AS d, w.op_type,
               count(*) AS cnt, COALESCE(sum(w.op_amount), 0) AS amt
        FROM wallet_withdrawals w
        WHERE w.op_time >= :ts AND w.op_time <= :te
          AND w.op_type IN ('订单退费转入', '用户提现', '系统扣费')
          AND {_withdraw_fail_remark_sql_cond()}
        GROUP BY 1, 2 ORDER BY 1, 2
    """), {"ts": cur_s, "te": cur_e}).fetchall()
    wallet_water = {"dates": [], "transfer_in_cnt": [], "transfer_in_amt": [],
                    "withdraw_cnt": [], "withdraw_amt": [], "fee_cnt": [], "fee_amt": [],
                    "net": [], "cum_net": []}
    wmap: dict[str, dict[str, list]] = {}
    for r in wallet_rows:
        wmap.setdefault(str(r.d), {})[r.op_type] = (int(r.cnt or 0), float(r.amt or 0))
    cum = 0.0
    for d in sorted(wmap):
        tin = wmap[d].get("订单退费转入", (0, 0.0))
        wd = wmap[d].get("用户提现", (0, 0.0))
        fee = wmap[d].get("系统扣费", (0, 0.0))
        net = tin[1] - wd[1] - fee[1]
        cum += net
        wallet_water["dates"].append(d)
        wallet_water["transfer_in_cnt"].append(tin[0])
        wallet_water["transfer_in_amt"].append(round(tin[1], 2))
        wallet_water["withdraw_cnt"].append(wd[0])
        wallet_water["withdraw_amt"].append(round(wd[1], 2))
        wallet_water["fee_cnt"].append(fee[0])
        wallet_water["fee_amt"].append(round(fee[1], 2))
        wallet_water["net"].append(round(net, 2))
        wallet_water["cum_net"].append(round(cum, 2))

    # ── 3. 涉诉 vs 普通: 每单按 SN 关联工单 bool_or 判定, 优先级 外部>商户号>内部 ──
    #     (2026-08-24 口径升级: 涉诉 = SN 关联投诉工单 OR 退款记录审核备注/备注含投诉类标识。
    #      后者是退款人员对"尚未进入投诉但预判可能投诉"的单做的类型标识, 同样计入涉诉。)
    #     三源关键词与 analytics._complaint_source_type_case 约定一致(2026-08-25 固化, 原生SQL场景)。
    _REMARK_COMPLAINT_MARKERS = ("涉投诉", "客诉", "投诉", "315", "争议", "催退")
    comp_rows = db.execute(text(f"""
        SELECT {_day("r.created_at_raw")} AS d, r.refund_amount,
               MAX(r.audit_remark) AS ar, MAX(r.remark) AS rm,
               bool_or(st.service_item ILIKE '%工信部%' OR st.service_item ILIKE '%12315%'
                   OR st.service_item ILIKE '%公共服务平台%' OR st.service_item ILIKE '%消费者协会%'
                   OR st.service_item ILIKE '%消协%' OR st.service_item ILIKE '%监督局%'
                   OR st.service_item ILIKE '%市场监管%' OR st.service_item ILIKE '%市监%'
                   OR st.service_item ILIKE '%315平台%' OR st.service_item ILIKE '%12345%'
                   OR st.service_item ILIKE '%政务平台%') AS has_ext,
               bool_or(st.service_item ILIKE '%微信%') AS has_merchant,
               bool_or(st.service_item ILIKE '%商品投诉%' OR st.service_item ILIKE '%服务投诉%') AS has_int
        FROM refunds r
        LEFT JOIN service_tickets st ON st.device_sn = r.device_sn
        WHERE r.refund_method = '线上' AND r.status = '已退款'
          AND r.created_at_raw >= :ts AND r.created_at_raw <= :te
        GROUP BY 1, r.refund_no
    """), {"ts": cur_s, "te": cur_e}).fetchall()
    cs_map: dict[str, dict[str, list]] = {}
    for r in comp_rows:
        day = str(r.d)
        if r.has_ext:
            ctype = "外部投诉"
        elif r.has_merchant:
            ctype = "商户号投诉"
        elif r.has_int:
            ctype = "内部投诉"
        elif any(m in ((r.ar or "") + (r.rm or "")) for m in _REMARK_COMPLAINT_MARKERS):
            # 无 SN 投诉工单, 但退款人员在审核备注/备注中标识了投诉/客诉/争议/催退等
            ctype = "备注涉诉"
        else:
            ctype = "普通退款"
        b = cs_map.setdefault(day, {})
        for k in ("内部投诉", "商户号投诉", "外部投诉", "备注涉诉", "普通退款"):
            b.setdefault(k, [0, 0.0])
        b[ctype][0] += 1
        b[ctype][1] += float(r.refund_amount or 0)
    complaint_split = {"dates": [], "internal_cnt": [], "internal_amt": [],
                       "merchant_cnt": [], "merchant_amt": [], "external_cnt": [], "external_amt": [],
                       "remark_cnt": [], "remark_amt": [],
                       "complaint_cnt": [], "complaint_amt": [], "normal_cnt": [], "normal_amt": [],
                       "ratio": []}
    for d in sorted(cs_map):
        b = cs_map[d]
        int_ = b["内部投诉"]; mer = b["商户号投诉"]; ext = b["外部投诉"]
        rmk = b["备注涉诉"]; nor = b["普通退款"]
        comp_cnt = int_[0] + mer[0] + ext[0] + rmk[0]
        comp_amt = int_[1] + mer[1] + ext[1] + rmk[1]
        total_amt = comp_amt + nor[1]
        complaint_split["dates"].append(d)
        complaint_split["internal_cnt"].append(int_[0]); complaint_split["internal_amt"].append(round(int_[1], 2))
        complaint_split["merchant_cnt"].append(mer[0]); complaint_split["merchant_amt"].append(round(mer[1], 2))
        complaint_split["external_cnt"].append(ext[0]); complaint_split["external_amt"].append(round(ext[1], 2))
        complaint_split["remark_cnt"].append(rmk[0]); complaint_split["remark_amt"].append(round(rmk[1], 2))
        complaint_split["complaint_cnt"].append(comp_cnt); complaint_split["complaint_amt"].append(round(comp_amt, 2))
        complaint_split["normal_cnt"].append(nor[0]); complaint_split["normal_amt"].append(round(nor[1], 2))
        complaint_split["ratio"].append(round(comp_amt / total_amt * 100, 1) if total_amt else 0.0)

    def _comp_window_totals(s, e) -> dict:
        """前一周期涉诉汇总(滑动/同刻环比用)。

        分类标记(SN 关联三源工单 bool_or + 审核备注/备注标识)与上面本期 comp_rows
        逐字同口径, 仅去掉按日分组、只汇总线上已退款单的涉诉笔数/金额, 避免双源漂移。
        s/e 为空(无前周期)时回零。
        """
        if not s or not e:
            return {"comp_cnt": 0, "comp_amt": 0.0, "online_cnt": 0, "online_amt": 0.0}
        rows = db.execute(text("""
            SELECT r.refund_amount,
                   bool_or(st.service_item ILIKE '%工信部%' OR st.service_item ILIKE '%12315%'
                       OR st.service_item ILIKE '%公共服务平台%' OR st.service_item ILIKE '%消费者协会%'
                       OR st.service_item ILIKE '%消协%' OR st.service_item ILIKE '%监督局%'
                       OR st.service_item ILIKE '%市场监管%' OR st.service_item ILIKE '%市监%'
                       OR st.service_item ILIKE '%315平台%' OR st.service_item ILIKE '%12345%'
                       OR st.service_item ILIKE '%政务平台%') AS has_ext,
                   bool_or(st.service_item ILIKE '%微信%') AS has_merchant,
                   bool_or(st.service_item ILIKE '%商品投诉%' OR st.service_item ILIKE '%服务投诉%') AS has_int,
                   MAX(r.audit_remark) AS ar, MAX(r.remark) AS rm
            FROM refunds r
            LEFT JOIN service_tickets st ON st.device_sn = r.device_sn
            WHERE r.refund_method = '线上' AND r.status = '已退款'
              AND r.created_at_raw >= :ts AND r.created_at_raw <= :te
            GROUP BY r.refund_no, r.refund_amount
        """), {"ts": s, "te": e}).fetchall()
        comp_cnt = 0
        comp_amt = 0.0
        online_cnt = 0
        online_amt = 0.0
        for r in rows:
            online_cnt += 1
            amt = float(r.refund_amount or 0)
            online_amt += amt
            is_comp = bool(r.has_ext or r.has_merchant or r.has_int) or any(
                m in ((r.ar or "") + (r.rm or "")) for m in _REMARK_COMPLAINT_MARKERS
            )
            if is_comp:
                comp_cnt += 1
                comp_amt += amt
        return {"comp_cnt": comp_cnt, "comp_amt": round(comp_amt, 2),
                "online_cnt": online_cnt, "online_amt": round(online_amt, 2)}

    # ── 5. 退款原因Top + 操作人员Top: 每单取最新完工工单的服务项作为原因 ──
    reason_rows = db.execute(text("""
        WITH rel AS (
            SELECT DISTINCT ON (r.refund_no)
                r.refund_no, r.refund_amount, r.operator,
                COALESCE(NULLIF(st.service_item, ''), '<未关联工单>') AS reason
            FROM refunds r
            LEFT JOIN service_tickets st ON st.device_sn = r.device_sn
            WHERE r.refund_method = '线上' AND r.status = '已退款'
              AND r.created_at_raw >= :ts AND r.created_at_raw <= :te
            ORDER BY r.refund_no, st.finish_time DESC NULLS LAST
        )
        SELECT reason, operator, count(*) AS cnt, COALESCE(sum(refund_amount), 0) AS amt
        FROM rel GROUP BY 1, 2
    """), {"ts": cur_s, "te": cur_e}).fetchall()
    reason_map: dict[str, list] = {}
    op_map: dict[str, list] = {}
    # 姓名归一: PersonAlias.alias → Person.display_name, 未命中再尝试去部门前缀后匹配
    alias_map: dict[str, str] = {}
    for a, dn in db.execute(
        select(PersonAlias.alias, func.coalesce(Person.display_name, PersonAlias.alias))
        .select_from(PersonAlias).outerjoin(Person, PersonAlias.person_id == Person.id)
    ):
        if a:
            alias_map[str(a)] = str(dn)

    def _norm_op(op: str | None) -> str:
        if not op:
            return "<系统>"
        raw = str(op)
        if raw.strip().lower() in ("sys", "system"):
            return "<系统>"
        if raw in alias_map:
            return _strip_qw_prefix(alias_map[raw])
        stripped = _strip_qw_prefix(raw)
        if stripped != raw and stripped in alias_map:
            return _strip_qw_prefix(alias_map[stripped])
        return stripped or raw

    for r in reason_rows:
        reason_map.setdefault(r.reason, [0, 0.0])
        reason_map[r.reason][0] += int(r.cnt or 0)
        reason_map[r.reason][1] += float(r.amt or 0)
        op = _norm_op(r.operator)
        op_map.setdefault(op, [0, 0.0])
        op_map[op][0] += int(r.cnt or 0)
        op_map[op][1] += float(r.amt or 0)
    reasons = sorted(reason_map.items(), key=lambda x: (-x[1][0], -x[1][1]))[:12]
    operators = sorted(op_map.items(), key=lambda x: -x[1][1])[:10]
    total_reason_amt = sum(v[1] for v in reason_map.values())
    reason_operator = {
        "reasons": [{"name": k, "count": v[0], "amount": round(v[1], 2),
                     "ratio": round(v[1] / total_reason_amt * 100, 1) if total_reason_amt else 0.0}
                    for k, v in reasons],
        "operators": [{"name": k, "count": v[0], "amount": round(v[1], 2),
                       "avg_amount": round(v[1] / v[0], 2) if v[0] else 0.0} for k, v in operators],
    }

    # ── 1. 资金流出全景: 严格四路经营 A线上直退 / B钱包提现 / C闪猫退款 / D手动退款 ──
    #     线下退款、转入提现账户(钱包内部流转)不属于四路, 不计入现金流全景
    #     对账归日: A 用 actual_refund_time(E路回填实际扣款), E滞后缺失退化 updated_at_raw
    #     (最新更新=真实退款完成时刻, 2026-09-09; 避免登记日 created 早于实际扣款造成错位)
    # #201 S-P0-1：A/B/C 三路日趋势补 dup_residual/ignored 排除（原仅 D 路排除）
    route_rows = db.execute(text(f"""
        SELECT {_day("COALESCE(r.actual_refund_time, r.updated_at_raw, r.created_at_raw)")} AS d,
               count(*) AS cnt, COALESCE(sum(r.refund_amount), 0) AS amt
        FROM refunds r
        WHERE r.refund_method = '线上' AND r.status = '已退款'
          AND {stats_quality_excluded_sql('r.quality_status')}
          AND COALESCE(r.actual_refund_time, r.updated_at_raw, r.created_at_raw) >= :ts
          AND COALESCE(r.actual_refund_time, r.updated_at_raw, r.created_at_raw) <= :te
        GROUP BY 1
    """), {"ts": cur_s, "te": cur_e}).fetchall()
    # B 用户提现(对账归日用实际扣款时间 actual_refund_time(2026-09-09 E回填), 缺失回退 op_time)
    # ⚠️ 口径: 决策概览/对账 B 用 actual_refund_time(实际扣款)对齐 E; 提现操作时效看 op_time 由其它视图表达
    wd_rows = db.execute(text(f"""
        SELECT {_day("COALESCE(w.actual_refund_time, w.op_time)")} AS d,
               count(*) AS cnt, COALESCE(sum(w.op_amount), 0) AS amt
        FROM wallet_withdrawals w
        WHERE COALESCE(w.actual_refund_time, w.op_time) >= :ts AND COALESCE(w.actual_refund_time, w.op_time) <= :te AND w.op_type = '用户提现'
          AND {_withdraw_fail_remark_sql_cond()}
          AND {stats_quality_excluded_sql('w.quality_status')}
        GROUP BY 1
    """), {"ts": cur_s, "te": cur_e}).fetchall()
    # C 闪猫退款 (独立业务表; 对账归日用实际扣款时间 refund_time(2026-09-09 E回填), 缺失回退 audit_time/created_at)
    # ⚠️ 口径: 决策概览/对账 C 用 refund_time(实际扣款)以对齐 E; 运营登记时效看 audit_time 由其它视图表达
    sm_rows = db.execute(text(f"""
        SELECT {_day("COALESCE(s.refund_time, s.audit_time, s.created_at)")} AS d,
               count(*) AS cnt, COALESCE(sum(COALESCE(s.actual_amount, s.refund_amount, 0)), 0) AS amt
        FROM shanmao_refunds s
        WHERE COALESCE(s.refund_time, s.audit_time, s.created_at) >= :ts
          AND COALESCE(s.refund_time, s.audit_time, s.created_at) <= :te
          AND s.status = '已退款'
          AND {stats_quality_excluded_sql('s.quality_status')}
        GROUP BY 1
    """), {"ts": cur_s, "te": cur_e}).fetchall()
    # D 手动退款 (manual_refunds, is_refunded='是'; 对账归日用实际扣款时间 actual_refund_time(2026-09-09 E回填),
    #    缺失回退 op_time/register_time; 排除 dup_residual/ignored → 经营口径不与重复/忽略行虚高; A-20260909-001)
    _d_excl_q = _quality_exclude_sql(db)
    d_rows = db.execute(text(f"""
        SELECT {_day("COALESCE(m.actual_refund_time, m.op_time, m.register_time)")} AS d,
               count(*) AS cnt, COALESCE(sum(m.refund_amount), 0) AS amt
        FROM manual_refunds m
        WHERE COALESCE(m.actual_refund_time, m.op_time, m.register_time) >= :ts
          AND COALESCE(m.actual_refund_time, m.op_time, m.register_time) <= :te
          AND m.is_refunded = '是'
          {"AND " + _EXCL_QSTATUS_SQL if _d_excl_q else ''}
        GROUP BY 1
    """), {"ts": cur_s, "te": cur_e}).fetchall()
    # E 实际现金流 (refund_record_reports 退款成功, 对账基准, 独立展示不与四路累加)
    e_rows = db.execute(text(f"""
        SELECT {_day("e.refund_time")} AS d,
               count(*) AS cnt, COALESCE(sum(e.refund_amount), 0) AS amt
        FROM refund_record_reports e
        WHERE e.refund_time >= :ts AND e.refund_time <= :te
          AND e.refund_status IN ('退款成功', '成功', 'SUCCESS')
        GROUP BY 1
    """), {"ts": cur_s, "te": cur_e}).fetchall()

    route_keys = ["A 线上直退", "B 钱包提现", "C 闪猫退款", "D 手动退款"]
    route_map: dict[str, dict[str, list]] = {}
    for rows, k in ((route_rows, "A 线上直退"), (wd_rows, "B 钱包提现"), (sm_rows, "C 闪猫退款"), (d_rows, "D 手动退款")):
        for r in rows:
            d = str(r.d)
            b = route_map.setdefault(d, {})
            b.setdefault(k, [0, 0.0])
            b[k][0] += int(r.cnt or 0)
            b[k][1] += float(r.amt or 0)
    e_map: dict[str, list] = {}
    for r in e_rows:
        e_map.setdefault(str(r.d), [0, 0.0])
        e_map[str(r.d)][0] += int(r.cnt or 0)
        e_map[str(r.d)][1] += float(r.amt or 0)
    routes_daily = {"dates": [], "series": {k: {"counts": [], "amounts": []} for k in route_keys}, "table": []}
    for d in sorted(route_map):
        b = route_map[d]
        row = {"date": d}
        total_cnt = total_amt = 0
        for k in route_keys:
            v = b.get(k, [0, 0.0])
            row[f"{k}_cnt"] = v[0]
            row[f"{k}_amt"] = round(v[1], 2)
            routes_daily["series"][k]["counts"].append(v[0])
            routes_daily["series"][k]["amounts"].append(round(v[1], 2))
            total_cnt += v[0]
            total_amt += v[1]
        row["total_cnt"] = total_cnt
        row["total_amt"] = round(total_amt, 2)
        row["actual_cnt"] = e_map.get(d, [0, 0.0])[0]   # E 实际现金流(对账基准)
        row["actual_amt"] = round(e_map.get(d, [0, 0.0])[1], 2)
        row["diff_amt"] = round(total_amt - row["actual_amt"], 2)  # 对账偏差 = 经营四路 − E
        routes_daily["table"].append(row)
        routes_daily["dates"].append(d)

    # ── summary: KPI + 环比 + 结论 (四路口径 A+B+C+D) ──
    def _window_totals(s: _dt.datetime, e: _dt.datetime) -> dict:
        tot = {"online_cnt": 0, "online_amt": 0.0, "withdraw_cnt": 0, "withdraw_amt": 0.0,
               "sm_cnt": 0, "sm_amt": 0.0, "d_cnt": 0, "d_amt": 0.0, "e_cnt": 0, "e_amt": 0.0}
        # #201 S-P0-1：A 路窗口汇总补 dup_residual/ignored 排除（与 routes_daily/cash_flow_wide 对齐）
        for r in db.execute(text(f"""
            SELECT count(*) AS cnt, COALESCE(sum(refund_amount), 0) AS amt
            FROM refunds r WHERE r.refund_method = '线上' AND r.status = '已退款'
              AND {stats_quality_excluded_sql('r.quality_status')}
              AND COALESCE(r.actual_refund_time, r.updated_at_raw, r.created_at_raw) >= :ts
              AND COALESCE(r.actual_refund_time, r.updated_at_raw, r.created_at_raw) <= :te
        """), {"ts": s, "te": e}).fetchall():
            tot["online_cnt"] += int(r.cnt or 0); tot["online_amt"] += float(r.amt or 0)
        # #201 S-P0-1：B 路窗口汇总补 dup_residual/ignored 排除
        for r in db.execute(text(f"""
            SELECT count(*) AS cnt, COALESCE(sum(op_amount), 0) AS amt
            FROM wallet_withdrawals w WHERE COALESCE(w.actual_refund_time, w.op_time) >= :ts
              AND COALESCE(w.actual_refund_time, w.op_time) <= :te AND w.op_type = '用户提现'
              AND {_withdraw_fail_remark_sql_cond()}
              AND {stats_quality_excluded_sql('w.quality_status')}
        """), {"ts": s, "te": e}).fetchall():
            tot["withdraw_cnt"] += int(r.cnt or 0); tot["withdraw_amt"] += float(r.amt or 0)
        # #201 S-P0-1：C 路窗口汇总补 dup_residual/ignored 排除
        for r in db.execute(text(f"""
            SELECT count(*) AS cnt, COALESCE(sum(COALESCE(actual_amount, refund_amount, 0)), 0) AS amt
            FROM shanmao_refunds s
            WHERE COALESCE(s.refund_time, s.audit_time, s.created_at) >= :ts
              AND COALESCE(s.refund_time, s.audit_time, s.created_at) <= :te
              AND s.status = '已退款'
              AND {stats_quality_excluded_sql('s.quality_status')}
        """), {"ts": s, "te": e}).fetchall():
            tot["sm_cnt"] += int(r.cnt or 0); tot["sm_amt"] += float(r.amt or 0)
        # D 手动退款 (is_refunded='是'; 归日=actual_refund_time 优先(实际扣款); 排除 dup_residual/ignored 已治理行)
        for r in db.execute(text(f"""
            SELECT count(*) AS cnt, COALESCE(sum(refund_amount), 0) AS amt
            FROM manual_refunds m
            WHERE COALESCE(m.actual_refund_time, m.op_time, m.register_time) >= :ts
              AND COALESCE(m.actual_refund_time, m.op_time, m.register_time) <= :te
              AND m.is_refunded = '是'
              {"AND " + _EXCL_QSTATUS_SQL if _d_excl_q else ''}
        """), {"ts": s, "te": e}).fetchall():
            tot["d_cnt"] += int(r.cnt or 0); tot["d_amt"] += float(r.amt or 0)
        # E 实际现金流 (refund_record_reports 退款成功, 对账基准)
        for r in db.execute(text("""
            SELECT count(*) AS cnt, COALESCE(sum(refund_amount), 0) AS amt
            FROM refund_record_reports e
            WHERE e.refund_time >= :ts AND e.refund_time <= :te
              AND e.refund_status IN ('退款成功', '成功', 'SUCCESS')
        """), {"ts": s, "te": e}).fetchall():
            tot["e_cnt"] += int(r.cnt or 0); tot["e_amt"] += float(r.amt or 0)
        return tot

    cur_tot = _window_totals(cur_s, cur_e)
    prev_tot = _window_totals(prev_s, prev_e) if prev_s and prev_e else dict.fromkeys(cur_tot, 0)
    cur_cash = cur_tot["online_amt"] + cur_tot["withdraw_amt"] + cur_tot["sm_amt"] + cur_tot["d_amt"]
    prev_cash = prev_tot["online_amt"] + prev_tot["withdraw_amt"] + prev_tot["sm_amt"] + prev_tot["d_amt"]
    chain_pct = round((cur_cash - prev_cash) / prev_cash * 100, 1) if prev_cash else None
    w_total_in = sum(wallet_water["transfer_in_amt"])
    w_total_out = sum(wallet_water["withdraw_amt"]) + sum(wallet_water["fee_amt"])
    wallet_net = round(w_total_in - w_total_out, 2)
    online_amt = cur_tot["online_amt"]
    comp_amt = sum(complaint_split["complaint_amt"])
    comp_ratio = round(comp_amt / online_amt * 100, 1) if online_amt else 0.0
    # 前一周期涉诉(环比): 分子=前周期涉诉金额(_comp_window_totals 同口径),
    #   分母=前周期线上直退金额(prev_tot, 与本期 comp_ratio 分母 cur_tot['online_amt'] 同源)
    prev_comp = _comp_window_totals(prev_s, prev_e)
    prev_comp_amt = prev_comp["comp_amt"]
    prev_online_amt = prev_tot.get("online_amt") or 0
    prev_comp_ratio = round(prev_comp_amt / prev_online_amt * 100, 1) if prev_online_amt else None
    actual_cash = cur_tot["e_amt"]                        # E 实际现金流(对账基准)
    reconcile_diff = round(cur_cash - actual_cash, 2)     # 对账偏差 = 经营四路 − E

    # ── 4. 手动退款登记水位 (与钱包水位同构: 登记 vs 已退款完成 = 蓄水) ──
    mreg_rows = db.execute(text(f"""
        SELECT {_day("m.register_time")} AS d,
               count(*) AS cnt, COALESCE(sum(m.refund_amount), 0) AS amt
        FROM manual_refunds m
        WHERE m.register_time >= :ts AND m.register_time <= :te
        GROUP BY 1 ORDER BY 1
    """), {"ts": cur_s, "te": cur_e}).fetchall()
    mdone_rows = db.execute(text(f"""
        SELECT {_day("COALESCE(m.op_time, m.register_time)")} AS d,
               count(*) AS cnt, COALESCE(sum(m.refund_amount), 0) AS amt
        FROM manual_refunds m
        WHERE COALESCE(m.op_time, m.register_time) >= :ts AND COALESCE(m.op_time, m.register_time) <= :te
          AND m.is_refunded = '是'
        GROUP BY 1 ORDER BY 1
    """), {"ts": cur_s, "te": cur_e}).fetchall()
    manual_water = {"dates": [], "reg_cnt": [], "reg_amt": [], "done_cnt": [], "done_amt": [],
                    "net": [], "cum_net": []}
    mreg_map = {str(r.d): (int(r.cnt or 0), float(r.amt or 0)) for r in mreg_rows}
    mdone_map = {str(r.d): (int(r.cnt or 0), float(r.amt or 0)) for r in mdone_rows}
    m_cum = 0.0
    for d in sorted(set(mreg_map) | set(mdone_map)):
        reg = mreg_map.get(d, (0, 0.0))
        done = mdone_map.get(d, (0, 0.0))
        net = reg[1] - done[1]
        m_cum += net
        manual_water["dates"].append(d)
        manual_water["reg_cnt"].append(reg[0]); manual_water["reg_amt"].append(round(reg[1], 2))
        manual_water["done_cnt"].append(done[0]); manual_water["done_amt"].append(round(done[1], 2))
        manual_water["net"].append(round(net, 2))
        manual_water["cum_net"].append(round(m_cum, 2))

    # E 数据最新日(refund_record_reports 持续采集, 显示其最新退款日作参考)
    e_data_until = None
    e_max = db.execute(text(
        "SELECT max(refund_time) FROM refund_record_reports "
        "WHERE refund_status IN ('退款成功', '成功', 'SUCCESS')"
    )).scalar()
    if e_max is not None:
        e_data_until = e_max.astimezone(TZ).strftime("%Y-%m-%d")

    conclusions = []
    extra = ""
    if cur_tot["sm_cnt"] or cur_tot["d_cnt"]:
        extra = f"，闪猫/手动 {cur_tot['sm_cnt'] + cur_tot['d_cnt']}笔/¥{cur_tot['sm_amt'] + cur_tot['d_amt']:,.0f}"
    conclusions.append(
        f"区间退款现金流出 ¥{cur_cash:,.0f}（线上直退 {cur_tot['online_cnt']}笔/¥{cur_tot['online_amt']:,.0f}，"
        f"钱包提现 {cur_tot['withdraw_cnt']}笔/¥{cur_tot['withdraw_amt']:,.0f}{extra}）。"
        + (f"较上一周期 {chain_pct:+.1f}%" if chain_pct is not None else "")
    )
    conclusions.append(
        f"商户号实际现金流 ¥{actual_cash:,.0f}，对账偏差(经营−实际) ¥{reconcile_diff:+,.0f}"
        + (f"（E 数据截止 {e_data_until}，截止日后对账仅供参考）" if e_data_until else "")
        + "。"
    )
    if reasons:
        top1, top2 = reasons[0], reasons[1] if len(reasons) > 1 else None
        reason_line = f"退款主因: {top1[0]} {top1[1][0]}笔/¥{top1[1][1]:,.0f}"
        if top2:
            reason_line += f"，其次 {top2[0]} {top2[1][0]}笔"
        # 原因覆盖率警示: <未关联工单> 占比高 → 原因分析失真, 需先补工单关联再做原因治理
        reason_total_cnt = sum(v[0] for v in reason_map.values())
        no_ticket_cnt = reason_map.get("<未关联工单>", [0, 0.0])[0]
        no_ticket_ratio = round(no_ticket_cnt / reason_total_cnt * 100, 1) if reason_total_cnt else 0.0
        if no_ticket_ratio >= 20:
            reason_line += f"。注意: 未关联工单 {no_ticket_cnt}笔({no_ticket_ratio:.0f}%)，原因覆盖不足，建议先核查工单关联再做原因治理。"
        else:
            reason_line += "。"
        conclusions.append(reason_line)
    # 涉诉比率解读(2026-08-24): 高=该退才退/控制到位(但需同时盯投诉绝对量); 低=大量退款无客诉驱动,警惕宽松
    comp_detail = (f"（内部 {sum(complaint_split['internal_cnt'])}笔/商户号 "
                   f"{sum(complaint_split['merchant_cnt'])}笔/外部 {sum(complaint_split['external_cnt'])}笔）")
    if comp_ratio >= 40:
        comp_reading = (f"涉诉占比 {comp_ratio:.1f}% 偏高: 退款大多由客诉/工单驱动、该退才退，控制到位；"
                        f"但同时需关注投诉绝对量（涉诉 {sum(complaint_split['complaint_cnt'])}笔）是否上升。")
    elif comp_ratio <= 15:
        comp_reading = (f"涉诉占比 {comp_ratio:.1f}% 偏低: 大量退款无客诉/工单关联，警惕退款政策过于宽松。")
    else:
        comp_reading = f"涉诉占比 {comp_ratio:.1f}% 处于常规区间，退款把关基本正常。"
    conclusions.append(f"涉诉退款 ¥{comp_amt:,.0f}{comp_detail}。{comp_reading}")
    # 钱包水位解读: 净水位是窗口内净流量, 须结合转入/转出量区分"过账快"与"转入不足"两种低水位成因
    w_max = max(w_total_in, w_total_out)
    if w_max == 0:
        wallet_note = "区间无钱包转入/转出流水，水位无变化。"
    elif abs(wallet_net) / w_max < 0.25:
        wallet_note = (f"水位基本平衡(+{wallet_net:,.0f} 元)但过账快: 退款到账后即被提走，流程顺畅；"
                       "若同时涉诉比率偏低，需警惕退款把关是否偏松。")
    elif wallet_net > 0:
        wallet_note = (f"水位偏高(+{wallet_net:,.0f} 元): 退款到账后滞留钱包未提走，既是沉淀风险（资金滞留），"
                       "也是待提现负债——若提现受阻或政策变动易集中挤兑并引发投诉，建议排查提现顺畅度、必要时引导提现。")
    elif w_total_in < w_total_out * 0.6:
        wallet_note = (f"水位偏低({wallet_net:,.0f} 元): 转入明显低于转出，退款业务量下降或转入端异常，建议排查转入。")
    else:
        wallet_note = (f"水位为负({wallet_net:,.0f} 元): 转出大于转入、用户提现活跃，"
                       "需关注是否出现集中提现（挤兑）或历史沉淀集中释放。")
    conclusions.append(
        f"钱包净水位 {wallet_net:+,.0f} 元（转入 ¥{w_total_in:,.0f} − 转出 ¥{w_total_out:,.0f}），累计 {sum(wallet_water['cum_net']):+,.0f} 元。"
        + wallet_note
    )
    m_cum_final = manual_water["cum_net"][-1] if manual_water["cum_net"] else 0.0
    if m_cum_final > 0:
        m_note = "登记蓄水偏高，存在未退积压，用户等待可能引发投诉，建议排查未退清单并加快处理。"
    elif m_cum_final < 0:
        m_note = "蓄水为负: 退款完成量超过登记量，处理及时、历史积压被清理，属健康状态。"
    else:
        m_note = "蓄水平衡，登记与完成基本同步。"
    conclusions.append(
        f"手动退款登记蓄水 ¥{m_cum_final:+,.0f} 元（登记 {sum(manual_water['reg_amt']):,.0f} − 已退 {sum(manual_water['done_amt']):,.0f}），"
        "即已登记但未完成退款的部分。"
        + m_note
    )

    summary = {
        "cash_out_total": round(cur_cash, 2),
        "cash_out_count": cur_tot["online_cnt"] + cur_tot["withdraw_cnt"] + cur_tot["sm_cnt"] + cur_tot["d_cnt"],
        "chain_pct": chain_pct,
        "online_count": cur_tot["online_cnt"],
        "online_amount": round(cur_tot["online_amt"], 2),
        "withdraw_amount": round(cur_tot["withdraw_amt"], 2),
        "wallet_net": wallet_net,
        "complaint_ratio": comp_ratio,
        "complaint_ratio_yoy": prev_comp_ratio,
        "complaint_ratio_delta": (round(comp_ratio - prev_comp_ratio, 1) if prev_comp_ratio is not None else None),
        "complaint_amount": round(comp_amt, 2),
        "complaint_amount_yoy": round(prev_comp_amt, 2),
        "complaint_count_yoy": prev_comp["comp_cnt"],
        "actual_cashflow": round(actual_cash, 2),        # E 实际现金流(对账基准)
        "actual_cashflow_count": cur_tot["e_cnt"],
        "reconcile_diff": reconcile_diff,                # 对账偏差 = 经营四路 − E
        "e_data_until": e_data_until,                    # E 数据截止日(快照)
        "manual_water_total": round(m_cum_final, 2),     # 手动退款登记蓄水
        "conclusions": conclusions,
    }

    return {
        "summary": summary,
        "routes_daily": routes_daily,
        "wallet_water": wallet_water,
        "manual_water": manual_water,
        "complaint_split": complaint_split,
        "reason_operator": reason_operator,
    }



# ---------- 手动退款登记 数据质量审核 (L3: 自动纠错记录 + 待人工确认) ----------
def _mr_quality_out(r: ManualRefund) -> dict[str, Any]:
    """手动退款登记行 → 质量审核输出"""
    def _sh(dt):
        return dt.astimezone(TZ).strftime("%Y-%m-%d %H:%M:%S") if dt else None
    return {
        "id": r.id,
        "register_time": _sh(r.register_time),
        "op_time": _sh(r.op_time),
        "is_refunded": r.is_refunded,
        "refund_amount": float(r.refund_amount or 0),
        "order_amount": float(r.order_amount or 0),
        "device_sn": r.device_sn,
        "order_no": r.order_no,
        "merchant_id": r.merchant_id,
        "registrant": r.registrant,
        "refund_operator": r.refund_operator,
        "remark": r.remark,
        "quality_status": r.quality_status,
        "quality_note": r.quality_note,
    }

def manual_refund_quality_list(db: Session, status: str = "all", page: int = 1, page_size: int = 50) -> dict[str, Any]:
    """手动退款登记质量审核列表: status = all/pending/auto_fixed/ok

    统计摘要: 各状态计数(供前端徽标)。列表按登记日期倒序。
    """
    summary = {}
    for st in ("pending", "auto_fixed", "ok"):
        summary[st] = db.scalar(
            select(func.count()).select_from(ManualRefund).where(ManualRefund.quality_status == st)
        ) or 0
    q = select(ManualRefund)
    if status in ("pending", "auto_fixed", "ok"):
        q = q.where(ManualRefund.quality_status == status)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.execute(
        q.order_by(ManualRefund.register_time.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()
    return {"total": total, "summary": summary,
            "list": [_mr_quality_out(r) for r in rows]}

def manual_refund_quality_confirm(db: Session, rid: int) -> dict[str, Any]:
    """人工确认通过: quality_status → ok (保留纠错说明, 追加确认标记)"""
    r = db.get(ManualRefund, rid)
    if not r:
        raise ValueError(f"手动退款记录不存在: id={rid}")
    r.quality_status = "ok"
    r.quality_note = ((r.quality_note or "") + " | 人工确认通过") if r.quality_note else "人工确认通过"
    db.commit()
    return {"id": rid, "quality_status": "ok"}

def manual_refund_quality_fix(db: Session, rid: int, payload: dict[str, Any]) -> dict[str, Any]:
    """人工修正: 更新指定字段(register_time/op_time/is_refunded/refund_amount/order_no/device_sn等),
    修正后置 ok。仅接受白名单字段, 时间字段支持 'YYYY-MM-DD' / 'YYYY-MM-DD HH:MM:SS' / ISO 格式。
    """
    r = db.get(ManualRefund, rid)
    if not r:
        raise ValueError(f"手动退款记录不存在: id={rid}")
    allowed = {"register_time", "op_time", "is_refunded", "refund_amount", "order_amount",
               "order_no", "device_sn", "merchant_id", "registrant", "refund_operator", "remark"}
    changes = []
    for k, v in (payload or {}).items():
        if k not in allowed or v is None:
            continue
        if k in ("register_time", "op_time"):
            setattr(r, k, _dt.datetime.fromisoformat(str(v)) if isinstance(v, str) and "T" in str(v)
                    else _dt.datetime.strptime(str(v), "%Y-%m-%d %H:%M:%S")
                    if isinstance(v, str) and len(str(v)) > 10
                    else _dt.datetime.strptime(str(v), "%Y-%m-%d"))
            changes.append(k)
        elif k in ("refund_amount", "order_amount"):
            setattr(r, k, float(v))
            changes.append(k)
        else:
            setattr(r, k, str(v))
            changes.append(k)
    r.quality_status = "ok"
    r.quality_note = ((r.quality_note or "") + f" | 人工修正字段: {','.join(changes)}") if changes else (r.quality_note or "")
    db.commit()
    return {"id": rid, "quality_status": "ok", "changed": changes}

def refund_export_original(
    db: Session, start: str | None = None, end: str | None = None,
    online_refunded_only: bool = False,
) -> list[dict[str, Any]]:
    """导出退款原始明细 (Excel 自定义分析用)

    每笔退费单一行, 含退款路线标记(线上直退/线下/转入提现账户/未填写),
    涉诉分类(按 SN 关联工单 bool_or 优先级 外部>商户号>内部) 与归一操作人。
    钱包提现/闪猫退款/手动退款(D路)不属于 refunds 表, 由前端调用方分别导出或仅导出 refunds 明细。

    online_refunded_only=True: 仅保留经营口径 A 路(线上直退且已退款),
    剔除驳回/无需退款/空白状态等非成功退款(统计/对账场景用; 导出明细默认全量供人工分析)。
    """
    cur_s, cur_e, _, _, _, _ = _resolve_windows(None, start, end)
    # A 路经营口径过滤子句 (refund_method='线上' AND status='已退款')
    _a_scope = "AND r.refund_method = '线上' AND r.status = '已退款'" if online_refunded_only else ""

    # 涉诉分类 (与决策概览口径一致; 三源关键词与 _complaint_source_type_case 约定一致)
    comp_map: dict[str, str] = {}
    for r in db.execute(text(f"""
        SELECT r.refund_no,
               bool_or(st.service_item ILIKE '%工信部%' OR st.service_item ILIKE '%12315%'
                   OR st.service_item ILIKE '%公共服务平台%' OR st.service_item ILIKE '%消费者协会%'
                   OR st.service_item ILIKE '%消协%' OR st.service_item ILIKE '%监督局%'
                   OR st.service_item ILIKE '%市场监管%' OR st.service_item ILIKE '%市监%'
                   OR st.service_item ILIKE '%315平台%' OR st.service_item ILIKE '%12345%'
                   OR st.service_item ILIKE '%政务平台%') AS has_ext,
               bool_or(st.service_item ILIKE '%微信%') AS has_merchant,
               bool_or(st.service_item ILIKE '%商品投诉%' OR st.service_item ILIKE '%服务投诉%') AS has_int
        FROM refunds r
        LEFT JOIN service_tickets st ON st.device_sn = r.device_sn
        WHERE r.created_at_raw >= :ts AND r.created_at_raw <= :te {_a_scope}
        GROUP BY r.refund_no
    """), {"ts": cur_s, "te": cur_e}).fetchall():
        comp_map[str(r.refund_no)] = "外部投诉" if r.has_ext else ("商户号投诉" if r.has_merchant else ("内部投诉" if r.has_int else ""))

    # 姓名归一映射
    alias_map: dict[str, str] = {}
    for a, dn in db.execute(
        select(PersonAlias.alias, func.coalesce(Person.display_name, PersonAlias.alias))
        .select_from(PersonAlias).outerjoin(Person, PersonAlias.person_id == Person.id)
    ):
        if a:
            alias_map[str(a)] = str(dn)

    def _norm_op(op: str | None) -> str:
        if not op:
            return ""
        raw = str(op)
        if raw.strip().lower() in ("sys", "system"):
            return "系统自动"
        if raw in alias_map:
            return _strip_qw_prefix(alias_map[raw])
        stripped = _strip_qw_prefix(raw)
        if stripped != raw and stripped in alias_map:
            return _strip_qw_prefix(alias_map[stripped])
        return stripped or raw

    def _route_of(m: str | None) -> str:
        if not m:
            return "未填写"
        s = m.strip()
        if s == "线上":
            return "A 线上直退"
        if s == "线下":
            return "线下退款"
        if "转入提现" in s or "提现" in s:
            return "转入提现账户(钱包内部)"
        return s

    rows = db.execute(text(f"""
        WITH rel AS (
            SELECT DISTINCT ON (r.refund_no)
                r.refund_no, r.platform_order_no, r.third_order_no, r.device_sn,
                r.refund_method, r.refund_reason, r.operator, r.auditor,
                r.refund_amount, r.actual_amount, r.status, r.merchant_name, r.package_name, r.remark,
                r.created_at_raw, r.updated_at_raw,
                COALESCE(NULLIF(st.service_item, ''), '') AS service_item
            FROM refunds r
            LEFT JOIN service_tickets st ON st.device_sn = r.device_sn
            WHERE r.created_at_raw >= :ts AND r.created_at_raw <= :te {_a_scope}
            ORDER BY r.refund_no, st.finish_time DESC NULLS LAST
        )
        SELECT * FROM rel ORDER BY created_at_raw
    """), {"ts": cur_s, "te": cur_e}).fetchall()

    def _sh(dt) -> str:
        if not dt:
            return ""
        return dt.astimezone(TZ).strftime("%Y-%m-%d %H:%M:%S")

    out = []
    for r in rows:
        out.append({
            "退款单号": r.refund_no,
            "平台订单号": r.platform_order_no or "",
            "三方订单号": r.third_order_no or "",
            "设备SN": r.device_sn or "",
            "退款路线": _route_of(r.refund_method),
            "退款原因": r.refund_reason or "",
            "关联工单服务项": r.service_item or "",
            "涉诉分类": comp_map.get(r.refund_no, "") or "",
            "操作人": _norm_op(r.operator),
            "审核人": _norm_op(r.auditor),
            "退款金额": float(r.refund_amount or 0),
            "实退金额": float(r.actual_amount or 0) if r.actual_amount is not None else "",
            "状态": r.status or "",
            "商户名称": r.merchant_name or "",
            "套餐名称": r.package_name or "",
            "创建时间": _sh(r.created_at_raw),
            "更新时间": _sh(r.updated_at_raw),
            "备注": (r.remark or "").replace("\n", " "),
        })
    return out

def refund_export_all(
    db: Session, start: str | None = None, end: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """导出退款分析 Excel(多 sheet): 明细(业务台账原始全量, 供人工分析) + 汇总(成功口径对账).

    明细 sheet(2026-09-09 放宽为台账原始, 人工分析用, 不砍状态/类型行, 附状态列自筛):
    - A退费单明细(全量): refunds 全部(线上直退/线下/转入提现账户/未填写 + 驳回/无需退款/空白), 时间=created_at_raw
    - B钱包流水明细(全量): wallet_withdrawals 全部 op_type(用户提现/订单退费转入/系统扣费), 时间=op_time
    - C闪猫退款明细(全量): shanmao_refunds 全部状态, 时间=COALESCE(audit_time, created_at)
    - D手动退款明细(全量): manual_refunds 全部(is_refunded 是/否), 时间=COALESCE(op_time, register_time)
    - E退款记录报表(全量): refund_record_reports 全部 refund_status, 时间=refund_time

    汇总 sheet(五路汇总(A-E))保持成功口径, 与 refund_decision_overview.routes_daily 一致:
    - A 线上直退: refund_method='线上' AND status='已退款'
    - B 钱包提现: op_type='用户提现' 且非失败/冻结备注
    - C 闪猫退款: status='已退款'
    - D 手动退款: is_refunded='是' (剔除 dup_residual/ignored)
    - E 退款记录报表: refund_status IN ('退款成功','成功','SUCCESS')
    """
    cur_s, cur_e, _, _, _, _ = _resolve_windows(None, start, end)
    sheets: dict[str, list[dict[str, Any]]] = {}
    # D 路 quality_status 排除标记(dup_residual=已治理重复): 汇总 sheet 排除, 明细 sheet 保留但加列标注
    _d_excl_q = _quality_exclude_sql(db)

    # A 退费单明细 (原始全量: 所有退费单行含线下/转入提现/未填写 + 驳回/无需退款/空白状态,
    # 供人工分析自筛; 2026-09-09 由'仅线上已退款'放宽回全量)
    sheets["A退费单明细(全量)"] = refund_export_original(db, start, end)

    def _sh(dt) -> str:
        if not dt:
            return ""
        return dt.astimezone(TZ).strftime("%Y-%m-%d %H:%M:%S")

    # B 钱包流水明细 (台账原始全量: 用户提现/订单退费转入/系统扣费等全部 op_type,
    # 含失败/冻结备注; 附"操作类型/备注"列供人工筛出真实出款; 2026-09-09 放宽)
    b_rows = db.execute(text("""
        SELECT account_id, device_sn, op_type, op_amount, wallet_balance, operator, op_time, remark
        FROM wallet_withdrawals w
        WHERE w.op_time >= :ts AND w.op_time <= :te
        ORDER BY w.op_time
    """), {"ts": cur_s, "te": cur_e}).fetchall()
    sheets["B钱包流水明细(全量)"] = [{
        "账户ID": r.account_id or "",
        "设备SN": r.device_sn or "",
        "操作类型": r.op_type or "",
        "提现金额": float(r.op_amount or 0),
        "钱包余额": float(r.wallet_balance) if r.wallet_balance is not None else "",
        "操作人": r.operator or "",
        "操作时间": _sh(r.op_time),
        "备注": (r.remark or "").replace("\n", " "),
    } for r in b_rows]

    # C 闪猫退款明细 (台账原始全量: 全部状态含申请中/已驳回/已退款; 附"状态"列自筛; 2026-09-09 放宽)
    c_rows = db.execute(text("""
        SELECT refund_no, order_no, device_sn, phone, paid_amount, refund_amount, actual_amount,
               package_name, merchant_name, refund_reason, refund_method, status,
               applicant, auditor, operator, apply_time, audit_time, refund_time, remark, audit_remark
        FROM shanmao_refunds
        WHERE COALESCE(audit_time, created_at) >= :ts AND COALESCE(audit_time, created_at) <= :te
        ORDER BY COALESCE(audit_time, created_at)
    """), {"ts": cur_s, "te": cur_e}).fetchall()
    sheets["C闪猫退款明细(全量)"] = [{
        "退款单号": r.refund_no or "",
        "订单号": r.order_no or "",
        "设备SN": r.device_sn or "",
        "手机号": r.phone or "",
        "实付金额": float(r.paid_amount) if r.paid_amount is not None else "",
        "申请退款金额": float(r.refund_amount) if r.refund_amount is not None else "",
        "实际退款金额": float(r.actual_amount) if r.actual_amount is not None else "",
        "套餐名称": r.package_name or "",
        "商户名称": r.merchant_name or "",
        "退款原因": r.refund_reason or "",
        "退款方式": r.refund_method or "",
        "状态": r.status or "",
        "申请人": r.applicant or "",
        "审核人": r.auditor or "",
        "操作人": r.operator or "",
        "申请时间": _sh(r.apply_time),
        "审核时间": _sh(r.audit_time),
        "退款时间": _sh(r.refund_time),
        "备注": (r.remark or "").replace("\n", " "),
        "审核备注": (r.audit_remark or "").replace("\n", " "),
    } for r in c_rows]

    # D 手动退款明细 (台账原始全量: 含 is_refunded=否 的登记(蓄水/未退)与已治理重复行;
    # 附"是否已退款/质量状态"列自筛; 2026-09-09 放宽)
    d_rows = db.execute(text("""
        SELECT register_time, device_sn, order_no, merchant_id, order_amount, refund_amount,
               registrant, is_refunded, refund_operator, op_time, remark, quality_status
        FROM manual_refunds
        WHERE COALESCE(op_time, register_time) >= :ts AND COALESCE(op_time, register_time) <= :te
        ORDER BY COALESCE(op_time, register_time)
    """), {"ts": cur_s, "te": cur_e}).fetchall()
    sheets["D手动退款明细(全量)"] = [{
        "登记时间": _sh(r.register_time),
        "设备号": r.device_sn or "",
        "订单号": r.order_no or "",
        "商户ID": r.merchant_id or "",
        "订单金额": float(r.order_amount) if r.order_amount is not None else "",
        "退款金额": float(r.refund_amount) if r.refund_amount is not None else "",
        "登记人": r.registrant or "",
        "是否已退款": r.is_refunded or "",
        "退款操作人": r.refund_operator or "",
        "操作时间": _sh(r.op_time),
        "质量状态": r.quality_status or "",
        "备注": (r.remark or "").replace("\n", " "),
    } for r in d_rows]

    # E 退款记录报表明细 (台账原始全量: 全部 refund_status; 附"退款状态"列自筛; 2026-09-09 放宽)
    e_rows = db.execute(text("""
        SELECT platform_order_no, wx_order_no, merchant_id, merchant_name, device_sn,
               product_name, refund_time, apply_refund_amount, refund_amount, refund_status
        FROM refund_record_reports
        WHERE refund_time >= :ts AND refund_time <= :te
        ORDER BY refund_time
    """), {"ts": cur_s, "te": cur_e}).fetchall()
    sheets["E退款记录报表明细(全量)"] = [{
        "平台订单号": r.platform_order_no or "",
        "微信订单号": r.wx_order_no or "",
        "商户号": r.merchant_id or "",
        "商户名": r.merchant_name or "",
        "设备号": r.device_sn or "",
        "商品名称": r.product_name or "",
        "退款时间": _sh(r.refund_time),
        "申请退款金额": float(r.apply_refund_amount) if r.apply_refund_amount is not None else "",
        "退款金额": float(r.refund_amount) if r.refund_amount is not None else "",
        "退款状态": r.refund_status or "",
    } for r in e_rows]

    # 五路按日汇总 (口径与决策概览 routes_daily 一致, 上海时区截断; E 为基准单独列)
    def _day(col: str) -> str:
        return f"DATE({col} AT TIME ZONE 'Asia/Shanghai')"

    agg_queries = [
        # #201 S-P0-1：导出汇总 A/B/C 与决策概览同口径补 dup_residual/ignored 排除
        ("A 线上直退", f"""
            SELECT {_day("COALESCE(r.actual_refund_time, r.updated_at_raw, r.created_at_raw)")} AS d, count(*) AS cnt, COALESCE(sum(r.refund_amount), 0) AS amt
            FROM refunds r WHERE r.refund_method = '线上' AND r.status = '已退款'
              AND {stats_quality_excluded_sql('r.quality_status')}
              AND COALESCE(r.actual_refund_time, r.updated_at_raw, r.created_at_raw) >= :ts
              AND COALESCE(r.actual_refund_time, r.updated_at_raw, r.created_at_raw) <= :te
            GROUP BY 1"""),
        ("B 钱包提现", f"""
            SELECT {_day("COALESCE(w.actual_refund_time, w.op_time)")} AS d, count(*) AS cnt, COALESCE(sum(w.op_amount), 0) AS amt
            FROM wallet_withdrawals w WHERE COALESCE(w.actual_refund_time, w.op_time) >= :ts
              AND COALESCE(w.actual_refund_time, w.op_time) <= :te AND w.op_type = '用户提现'
              AND {_withdraw_fail_remark_sql_cond()}
              AND {stats_quality_excluded_sql('w.quality_status')}
            GROUP BY 1"""),
        ("C 闪猫退款", f"""
            SELECT {_day("COALESCE(s.refund_time, s.audit_time, s.created_at)")} AS d, count(*) AS cnt,
                   COALESCE(sum(COALESCE(s.actual_amount, s.refund_amount, 0)), 0) AS amt
            FROM shanmao_refunds s
            WHERE COALESCE(s.refund_time, s.audit_time, s.created_at) >= :ts
              AND COALESCE(s.refund_time, s.audit_time, s.created_at) <= :te
              AND s.status = '已退款'
              AND {stats_quality_excluded_sql('s.quality_status')}
            GROUP BY 1"""),
        ("D 手动退款", f"""
            SELECT {_day("COALESCE(m.actual_refund_time, m.op_time, m.register_time)")} AS d, count(*) AS cnt,
                   COALESCE(sum(m.refund_amount), 0) AS amt
            FROM manual_refunds m
            WHERE COALESCE(m.actual_refund_time, m.op_time, m.register_time) >= :ts
              AND COALESCE(m.actual_refund_time, m.op_time, m.register_time) <= :te
              AND m.is_refunded = '是'
              {"AND " + _EXCL_QSTATUS_SQL if _d_excl_q else ''}
            GROUP BY 1"""),
        ("E退款记录报表", f"""
            SELECT {_day("r.refund_time")} AS d, count(*) AS cnt,
                   COALESCE(sum(r.refund_amount), 0) AS amt
            FROM refund_record_reports r
            WHERE r.refund_status IN ('退款成功','成功','SUCCESS')
              AND r.refund_time >= :ts AND r.refund_time <= :te
            GROUP BY 1"""),
    ]
    route_map: dict[str, dict[str, list]] = {}
    for name, sql in agg_queries:
        for r in db.execute(text(sql), {"ts": cur_s, "te": cur_e}).fetchall():
            b = route_map.setdefault(str(r.d), {})
            b.setdefault(name, [0, 0.0])
            b[name][0] += int(r.cnt or 0)
            b[name][1] += float(r.amt or 0)
    # E 是基准不参与合计, 单独列供对比 (见下方 summary)
    summary = []
    for d in sorted(route_map):
        b = route_map[d]
        row = {"日期": d}
        total_cnt = total_amt = 0
        for name, _ in agg_queries:
            v = b.get(name, [0, 0.0])
            row[f"{name}笔数"] = v[0]
            row[f"{name}金额"] = round(v[1], 2)
            if name != "E退款记录报表":
                total_cnt += v[0]
                total_amt += v[1]
        row["经营合计(A+B+C+D)笔数"] = total_cnt
        row["经营合计(A+B+C+D)金额"] = round(total_amt, 2)
        # 对账偏差: 经营合计 - E (正=经营多/负=E多)
        e = b.get("E退款记录报表", [0, 0.0])
        row["偏差(经营-E)笔数"] = total_cnt - e[0]
        row["偏差(经营-E)金额"] = round(total_amt - e[1], 2)
        summary.append(row)
    sheets["五路汇总(A-E)"] = summary

    return sheets

def refund_problem_type_detail(
    db: Session, start: str | None = None, end: str | None = None, top: int = 10,
) -> dict[str, Any]:
    """接口8: problem_type 细分 Top10 - 原始类型的接待量/成功率."""
    cur_s, cur_e, _, _, _, _ = _resolve_windows(None, start, end)
    core = _refund_recovery_core(db, cur_s, cur_e)
    global_fail_sns = core["global_fail_sns"]

    # 查每个 problem_type 的 SN 集合
    pt_sn: dict[str, set[str]] = {}
    for r in db.execute(text(
        """
        SELECT NULLIF(TRIM(c.device_sn),'') AS sn, c.problem_type
        FROM cs_records c
        WHERE c.operation_time IS NOT NULL
          AND c.operation_time >= :ts AND c.operation_time <= :te
          AND NOT (c.operator = ANY(:sys_bl))
          AND c.problem_category = '退款'
          AND NULLIF(TRIM(c.device_sn),'') IS NOT NULL
        """
    ), {"ts": cur_s, "te": cur_e, "sys_bl": list(SYS_OP_BLACKLIST_RR)}).fetchall():
        if r.sn and r.problem_type:
            pt_sn.setdefault(r.problem_type, set()).add(r.sn)

    items = []
    for pt, sns in pt_sn.items():
        fail = sns & global_fail_sns
        total = len(sns)
        succ = total - len(fail)
        rate = round(succ / total * 100, 2) if total else 0.0
        # 失败金额
        amt = sum(float(core["sn_failed_meta"].get(sn, {}).get("amount") or 0) for sn in fail)
        items.append({
            "problem_type": pt, "normalized_reason": _normalize_refund_reason(pt),
            "total_sn": total, "success_sn": succ, "failed_sn": len(fail),
            "success_rate": rate, "total_amount": round(amt, 2),
        })
    items.sort(key=lambda x: -x["total_sn"])
    return {"items": items[:top]}

def refund_group_comparison(
    db: Session, start: str | None = None, end: str | None = None,
) -> dict[str, Any]:
    """接口9: 组间对比 - 5个组的横向对比."""
    cur_s, cur_e, _, _, _, _ = _resolve_windows(None, start, end)
    core = _refund_recovery_core(db, cur_s, cur_e)
    staff_detail = core["staff_detail"]
    global_fail_sns = core["global_fail_sns"]
    sn_failed_meta = core["sn_failed_meta"]
    op_host_sns = core["op_host_sns"]

    # 按组聚合
    group_data: dict[str, dict] = {}
    for sd in staff_detail:
        g = sd.get("group") or "未分组"
        d = group_data.setdefault(g, {"person_cnt": 0, "total_sn": 0, "success_sn": 0,
                                       "failed_sn": 0, "refund_op_amount": 0.0,
                                       "rates": [], "success_sns": set()})
        d["person_cnt"] += 1
        d["total_sn"] += sd["total_sn"]
        d["success_sn"] += sd["success_sn"]
        d["failed_sn"] += sd["failed_sn"]
        d["refund_op_amount"] += sd["refund_op_amount"]
        if sd["success_rate"] is not None:
            d["rates"].append(sd["success_rate"])
        # 收集成功SN用于估算
        host = op_host_sns.get(sd["operator"], set())
        d["success_sns"] |= (host - global_fail_sns)

    # 计算各组估算金额
    reason_fail_avg: dict[str, float] = {}
    reason_fail_cnt: dict[str, int] = {}
    reason_fail_sum: dict[str, float] = {}
    for sn in global_fail_sns:
        meta = sn_failed_meta.get(sn, {})
        reason = _normalize_refund_reason(meta.get("reason"))
        amt = float(meta.get("amount") or 0)
        reason_fail_sum[reason] = reason_fail_sum.get(reason, 0.0) + amt
        reason_fail_cnt[reason] = reason_fail_cnt.get(reason, 0) + 1
    for r, s in reason_fail_sum.items():
        c = reason_fail_cnt.get(r, 0)
        reason_fail_avg[r] = (s / c) if c else 0.0
    overall_fail_avg = sum(reason_fail_sum.values()) / max(sum(reason_fail_cnt.values()), 1)

    groups = []
    for g, d in group_data.items():
        total = d["total_sn"]
        succ = d["success_sn"]
        rate = round(succ / total * 100, 2) if total else 0.0
        # 估算挽回金额
        est_amt = 0.0
        for sn in d["success_sns"]:
            sn_reason = core["sn_host_reason"].get(sn, "其他(填写备注)")
            avg = reason_fail_avg.get(sn_reason, overall_fail_avg)
            est_amt += avg
        avg_rate = round(sum(d["rates"]) / len(d["rates"]), 2) if d["rates"] else 0.0
        groups.append({
            "group": g, "person_cnt": d["person_cnt"], "total_sn": total,
            "success_sn": succ, "failed_sn": d["failed_sn"], "success_rate": rate,
            "total_refund_op_amount": round(d["refund_op_amount"], 2),
            "estimated_save_amount": round(est_amt, 2),
            "avg_success_rate": avg_rate,
        })
    groups.sort(key=lambda x: -x["total_sn"])
    return {"groups": groups}

def refund_recovery_analysis(
    db: Session,
    date: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """退款挽回专项分析: 总体KPI + 原因分布 + 按人员明细 + 周趋势.

    返回结构:
      {
        date, date_end,
        kpi: { total_sn, success_sn, failed_sn, success_rate,
               abandon_cnt, fail_cnt, success_cnt, recharge_cnt,
               abandon_amount, fail_amount, success_amount, recharge_amount,
               actual_refund_amount, fee_ratio },
        reason_dist: [{ reason, order_cnt, ratio, success_rate, amount }],
        staff_detail: [{ operator, name, group, total_sn, success_sn, failed_sn, success_rate }],
        weekly_trend: [{ week_label, abandon_cnt, fail_cnt, success_cnt, total_cnt, success_rate }],
        daily_reason: [{ date, reason1_cnt, reason1_ratio, ... }],
      }
    """
    # ---- 1. 统一时间窗口 ----
    cur_s, cur_e, _, _, is_realtime, disp = _resolve_windows(date, start, end)
    ts = cur_s; te = cur_e
    d_iso = ts.astimezone(TZ).date().isoformat()
    d_end_iso = te.astimezone(TZ).date().isoformat()

    # 系统自动过滤 (和 today_staff_efficiency 保持一致)
    SYS_KW = ["系统自动", "自动发送", "自动回复", "自动推送", "自动补发", "自动触发", "自动生成"]
    def _desc_auto_filter(col):
        return ~and_(*[func.coalesce(col, "").ilike(f"%{k}%") for k in SYS_KW])

    # ---- HR 专项 映射 (2026-08-10 细分后版本, 与 today_staff_efficiency 保持一致) ----
    def _hr_map(hr_sp: str | None, pos: str | None) -> str | None:
        """人员主专项归一化映射 (2026-08-19 修订)
        返回: 专项名称 | "管理层" | None

        新增: 专项岗(专员)显式识别, 与管理岗同等待遇 → 跨类全豁免
        """
        # ① 管理岗（主管/组长/副主管）→ 跨类全豁免
        if (pos or "") in ("主管", "组长", "副主管"):
            return "管理层"
        # ② 专项岗(专员) 显式识别: 投诉专员/应急专员/退款挽回专员 等
        p = (pos or "").strip()
        if "专员" in p:
            if "投诉" in p:
                return "外部投诉" if "外部" in p else "内部投诉"
            if "挽回" in p:
                return "退款挽回"
            if "应急" in p:
                return "内部投诉"
            return "内部投诉"
        if not hr_sp:
            return None
        s = (hr_sp or "").strip()
        # 6个标准专项
        if s in ("咨询接待", "套餐专项", "退款处理", "退款挽回", "内部投诉", "外部投诉", COMPLAINT_SPECIALTY):
            return s
        # 兼容旧数据模糊匹配
        if "外部投诉" in s: return "外部投诉"
        if "内部投诉" in s or "投诉" in s: return "内部投诉"
        if "退款挽回" in s: return "退款挽回"
        if "退款处理" in s or "退款" in s: return "退款处理"
        if "套餐" in s: return "套餐专项"
        if any(k in s for k in ("咨询", "接待", "综合问题", "其他问题")):
            return "咨询接待"
        return None

    # ---- 2. 先找 Person 维表: 建立所有人员的meta信息 ----
    #   注意: PersonAlias.alias = 业务表的 operator (如 "企微-叶珍凤"), 通过 person_id 关联到 Person (统一实体)
    from app.models.person import Person as P
    from app.models.person import PersonAlias as PA
    person_rows = db.execute(
        select(
                PA.alias.label("operator"),
                PA.person_id.label("person_id"),
                func.coalesce(P.display_name, PA.alias).label("display_name"),
                P.group_name,
                func.coalesce(P.specialty, P.primary_specialty).label("specialty"),
                P.position, P.department, P.qw_user_id,
            )
            .select_from(PA)
            .outerjoin(P, PA.person_id == P.id)
            .where(PA.is_system.is_(False))  # 排除 sys/system 等系统自动别名
    ).all()

    # 专职退款挽回人员 (hr_cat=退款挽回)
    recovery_ops: dict[str, dict] = {}
    # 所有人员的meta信息 (用于非专职人员接待退款时的挽回统计)
    all_ops_meta: dict[str, dict] = {}

    for pr in person_rows:
        op = pr.operator or ""
        if not op:
            continue
        hr_cat = _hr_map(pr.specialty, pr.position)
        info = {
            "person_id": pr.person_id,   # 2026-08-12 新增: 用于多alias归一合并
            "name": pr.display_name or op,
            "group": pr.group_name or "未分组",
            "specialty": pr.specialty,
            "position": pr.position,
            "dept": pr.department or "未设置",
            "qw_user_id": pr.qw_user_id,
            "hr_cat": hr_cat,
            "is_recovery_staff": _is_refund_staff(pr.group_name, hr_cat),
        }
        all_ops_meta[op] = info
        if info["is_recovery_staff"]:
            recovery_ops[op] = info

    if not all_ops_meta:
        return {
            "date": d_iso, "date_end": d_end_iso,
            "kpi": _empty_kpi(),
            "reason_dist": [],
            "staff_detail": [],
            "weekly_trend": [],
            "daily_reason": [],
            "daily_trend": [],
            "hourly_today": [],
            "_note": "当前时间窗口未识别到人员数据, 请检查 Person 维表配置.",
        }

    # ---- 3. 分母: 所有人员 在周期内 cs_records 接待的【真退款类】 SN 集合 (按 op 分组) ----
    #    口径修正(2026-08-11):
    #      - problem_category = '退款' (大类是退款诉求)
    #      - 不再限制operator必须是退款挽回专员: 任何人员(含组长/主管/退款处理员)接待的退款类SN都算入分母
    #      - 如果非专职挽回人员接待的退款SN最终没有产生退款行为 → 也算挽回成功
    #      - 移除 problem_type 过度过滤: 网络问题/其他问题等所有退款理由均计入
    cs_refund_sn_sql = text(
        """
        SELECT c.operator AS op, NULLIF(TRIM(c.device_sn),'') AS sn,
               c.operation_time AS op_time,
               c.problem_type AS problem_type
        FROM cs_records c
        WHERE c.operation_time IS NOT NULL
          AND c.operation_time >= :ts AND c.operation_time <= :te
          AND NOT (c.operator = ANY(:sys_bl))
          AND c.problem_category = '退款'
          AND NOT (
              COALESCE(c.description,'') ILIKE ANY(ARRAY[:k1,:k2,:k3,:k4,:k5,:k6,:k7])
           OR COALESCE(c.execution_result,'') ILIKE ANY(ARRAY[:k1,:k2,:k3,:k4,:k5,:k6,:k7])
           OR COALESCE(c.solution,'') ILIKE ANY(ARRAY[:k1,:k2,:k3,:k4,:k5,:k6,:k7])
          )
        """
    )
    kw_params = {f"k{i+1}": f"%{k}%" for i, k in enumerate(SYS_KW)}
    cs_sn_rows = db.execute(
        cs_refund_sn_sql,
        {"ts": ts, "te": te, "sys_bl": list(SYS_OP_BLACKLIST_RR), **kw_params},
    ).fetchall()

    # 收集所有在cs_records中出现的operator，然后补全他们的meta信息
    all_cs_ops: set[str] = set()
    for r in cs_sn_rows:
        if r.op:
            all_cs_ops.add(r.op)

    # 将cs_records中出现但不在person_rows中的operator也加入all_ops_meta
    person_op_map = {p.operator: p for p in person_rows}
    for op in all_cs_ops:
        if op not in all_ops_meta:
            p = person_op_map.get(op)
            if p:
                all_ops_meta[op] = {
                    "person_id": getattr(p, "person_id", None),
                    "name": p.display_name or op,
                    "group": p.group_name or "未分组",
                    "specialty": p.specialty or None,
                    "position": p.position or "",
                    "dept": p.department or "未设置",
                    "qw_user_id": getattr(p, "qw_user_id", None),
                    "is_recovery_staff": (p.specialty in REFUND_RECOVERY_HR_CATS),
                }
            else:
                all_ops_meta[op] = {
                    "person_id": None,
                    "name": op,
                    "group": "未知分组",
                    "specialty": None,
                    "position": "",
                    "dept": "未设置",
                    "qw_user_id": None,
                    "is_recovery_staff": False,
                }

    # op -> set(sn)   (每个接待人员的分母SN)
    op_host_sns: dict[str, set[str]] = {}
    for r in cs_sn_rows:
        if r.sn and r.op:
            op_host_sns.setdefault(r.op, set()).add(r.sn)

    # 全局分母SN (全部合并,去重) + 每个SN最早接待时间 + 接待人员列表
    global_host_sns: set[str] = set()
    sn_first_op_time: dict[str, datetime] = {}  # sn -> 最早接待时间
    sn_host_ops: dict[str, list[str]] = {}      # sn -> [接待过的operators] (区分专职/非专职)
    sn_host_reason: dict[str, str] = {}          # sn -> 归一化原因(从cs_record.problem_type, 首次记录为准)
    for r in cs_sn_rows:
        if r.sn:
            sn = r.sn
            global_host_sns.add(sn)
            # 记录最早接待时间
            if r.op_time and (sn not in sn_first_op_time or r.op_time < sn_first_op_time[sn]):
                sn_first_op_time[sn] = r.op_time
            # 记录接待人员
            if r.op:
                sn_host_ops.setdefault(sn, [])
                if r.op not in sn_host_ops[sn]:
                    sn_host_ops[sn].append(r.op)
            # 记录归一化原因 (首次非空记录为准)
            if sn not in sn_host_reason and r.problem_type:
                sn_host_reason[sn] = _normalize_refund_reason(r.problem_type)

    # ---- 4. 分子: 挽留失败 SN 集合 = 分母SN ∩ (refunds ∪ 退款工单) ----
    #   时间: 退款/工单 发生时间 在 [该SN最早接待时间, 最早接待时间+7天] 窗口 (T+7 宽限, 2026-08-10修正)
    #   修正说明: 之前只判断了全局时间窗口,没有校验"退款时间必须在接待时间之后",导致接待前已退款的SN被误判为失败
    #   说明: 允许专员接待后最长7天走到退款审批(到账/工单结案)才判定为失败, 避免"假成功"
    #   2026-08-11 修正: 增加第3个失败来源——cs_record描述关键词匹配
    #     大量退款操作通过企微等渠道执行,记录在cs_records.description中(如"退款说明"/"退款后重新充值说明"等)
    #     但没有同步到refunds表或service_tickets表,导致这些已进入退款流程的SN被误判为"挽回成功"
    #     新增cs_kw_sn分支: cs_record描述含退款流程关键词的SN直接判定为失败
    import datetime as _dt2
    te7 = te + _dt2.timedelta(days=7)
    # cs_record描述中的"已进入退款流程"关键词 (匹配即判失败)
    #   退款说明 = 已发送退款说明,进入退款流程
    #   退款后重新充值 = 已退款但重新充值
    #   要求退回微信 = 已进入退款流程
    #   非挽回用户 = 放弃挽回
    CS_FAIL_KW = ["退款说明", "退款后重新充值", "要求退回微信", "非挽回用户"]
    cs_kw_params = {f"ck{i+1}": f"%{k}%" for i, k in enumerate(CS_FAIL_KW)}
    failed_sn_sql = text(
        """
        WITH host_sns AS (SELECT unnest(CAST(:sn_arr AS text[])) AS sn),
        refund_sn AS (
            SELECT DISTINCT NULLIF(TRIM(r.device_sn),'') AS sn,
                   'refund' AS src,
                   r.refund_reason AS reason,
                   COALESCE(r.actual_amount, r.refund_amount, 0) AS amount,
                   r.status AS status,
                   r.refund_method AS method,
                   r.created_at_raw AS event_time
            FROM refunds r
            JOIN host_sns hs ON NULLIF(TRIM(r.device_sn),'') = hs.sn
            WHERE r.created_at_raw IS NOT NULL
              AND NULLIF(TRIM(r.device_sn),'') IS NOT NULL
              AND COALESCE(r.status, '') NOT IN ('无需退款', '驳回')  -- 无需退款/驳回不算失败
              -- 不限制operator和refund_method：任何方式的退款、任何人操作都算失败
              AND r.created_at_raw >= :ts AND r.created_at_raw <= :te7
        ),
        ticket_sn AS (
            SELECT DISTINCT NULLIF(TRIM(st.device_sn),'') AS sn,
                   'ticket' AS src,
                   st.service_item AS reason,
                   COALESCE(st.refund_amount, st.estimated_amount, 0) AS amount,
                   st.status AS status,
                   st.service_category AS method,
                   st.register_time AS event_time  -- 用工单提交时间而非结案时间，客户提交即表示坚持退款
            FROM service_tickets st
            JOIN host_sns hs ON NULLIF(TRIM(st.device_sn),'') = hs.sn
            WHERE st.register_time IS NOT NULL
              AND NULLIF(TRIM(st.device_sn),'') IS NOT NULL
              AND COALESCE(st.service_category,'') = '退款'
              AND COALESCE(st.status, '') != '终止'  -- 终止=客户撤单，不算失败
              -- 不限制handler：任何人处理的退款工单都算失败
              AND st.register_time >= :ts AND st.register_time <= :te7
        ),
        cs_kw_sn AS (
            -- 2026-08-11新增: cs_record描述含"退款说明"等关键词 = 已进入退款流程 = 挽回失败
            SELECT DISTINCT NULLIF(TRIM(c.device_sn),'') AS sn,
                   'cs_kw' AS src,
                   LEFT(COALESCE(c.description,''),100) AS reason,
                   0 AS amount,
                   '退款流程' AS status,
                   'cs_record关键词' AS method,
                   c.operation_time AS event_time
            FROM cs_records c
            JOIN host_sns hs ON NULLIF(TRIM(c.device_sn),'') = hs.sn
            WHERE c.operation_time IS NOT NULL
              AND NULLIF(TRIM(c.device_sn),'') IS NOT NULL
              AND c.problem_category = '退款'
              AND NOT (c.operator = ANY(:sys_bl))
              AND (
                  COALESCE(c.description,'') ILIKE ANY(ARRAY[:ck1,:ck2,:ck3,:ck4])
               OR COALESCE(c.execution_result,'') ILIKE ANY(ARRAY[:ck1,:ck2,:ck3,:ck4])
               OR COALESCE(c.solution,'') ILIKE ANY(ARRAY[:ck1,:ck2,:ck3,:ck4])
              )
              AND c.operation_time >= :ts AND c.operation_time <= :te7
        ),
        all_fail AS (
            SELECT sn, src, reason, amount, status, method, event_time FROM refund_sn
            UNION ALL
            SELECT sn, src, reason, amount, status, method, event_time FROM ticket_sn
            UNION ALL
            SELECT sn, src, reason, amount, status, method, event_time FROM cs_kw_sn
        )
        SELECT af.sn, af.src, af.reason, af.amount, af.status, af.method, af.event_time
        FROM all_fail af
        JOIN host_sns hs ON af.sn = hs.sn
        """
    )
    fail_rows = db.execute(
        failed_sn_sql,
        {"sn_arr": list(global_host_sns), "ts": ts, "te7": te7,
         "sys_bl": list(SYS_OP_BLACKLIST_RR), **cs_kw_params},
    ).fetchall()

    # 全局失败SN集合 + 明细 (按 SN 取金额最大那一条 作为该SN的归因)
    # 校验事件时间必须在 [该SN最早接待时间, 最早接待时间+7天] 窗口内
    global_fail_sns: set[str] = set()
    sn_failed_meta: dict[str, dict] = {}
    for r in fail_rows:
        sn = r.sn or ""
        if not sn:
            continue
        # 获取该SN最早接待时间
        first_op = sn_first_op_time.get(sn)
        if not first_op:
            continue
        event_time = r.event_time
        if not event_time:
            continue
        # 判断事件时间是否在 [接待时间, 接待时间+7天] 窗口内
        window_end = first_op + _dt2.timedelta(days=7)
        if not (first_op <= event_time <= window_end):
            continue  # 接待前已发生 或 超过T+7才发生, 不算失败
        global_fail_sns.add(sn)
        amt = float(r.amount or 0)
        prev = sn_failed_meta.get(sn)
        if prev is None or amt > float(prev.get("amount") or 0):
            sn_failed_meta[sn] = {
                "src": r.src, "reason": r.reason, "amount": amt,
                "status": r.status, "method": r.method,
            }

    # ---- 4.6 退款操作执行人统计 (同SN多人都算工作量, 不算入成功/失败分子) ----
    #   统计每个 operator 作为 refunds.operator / service_tickets.handler 处理的失败SN数
    #   注意: 这只是 "退款操作工作量" 独立字段, 不影响成功率计算的分子分母
    refund_op_sns: dict[str, set[str]] = {}   # operator alias -> set of 退款操作 SN
    refund_op_amt: dict[str, float] = {}     # operator alias -> 操作金额合计
    # 从 refunds 拉 operator (统计真人操作的所有退款方式:线上/线下/转入提现账户)
    for r in db.execute(text(
        """
        SELECT NULLIF(TRIM(r.device_sn),'') AS sn,
               r.operator AS op,
               COALESCE(r.actual_amount, r.refund_amount, 0) AS amt
        FROM refunds r
        WHERE r.created_at_raw IS NOT NULL
          AND r.created_at_raw >= :ts AND r.created_at_raw <= :te7
          AND NULLIF(TRIM(r.device_sn),'') IS NOT NULL
          AND r.operator IS NOT NULL AND NOT (r.operator = ANY(:sys_bl))
          AND COALESCE(r.status, '') NOT IN ('无需退款', '驳回')
        """
    ), {"ts": ts, "te7": te7, "sys_bl": list(SYS_OP_BLACKLIST_RR)}).fetchall():
        if not r.sn or not r.op:
            continue
        refund_op_sns.setdefault(r.op, set()).add(r.sn)
        refund_op_amt[r.op] = refund_op_amt.get(r.op, 0.0) + float(r.amt or 0)
    # 从 service_tickets 拉 handler (统计真人处理的已完成退款工单)
    for r in db.execute(text(
        """
        SELECT NULLIF(TRIM(st.device_sn),'') AS sn,
               st.handler AS op,
               COALESCE(st.refund_amount, st.estimated_amount, 0) AS amt
        FROM service_tickets st
        WHERE st.finish_time IS NOT NULL
          AND st.finish_time >= :ts AND st.finish_time <= :te7
          AND NULLIF(TRIM(st.device_sn),'') IS NOT NULL
          AND st.handler IS NOT NULL AND NOT (st.handler = ANY(:sys_bl))
          AND COALESCE(st.service_category,'') = '退款'
          AND COALESCE(st.status, '') = '已完成'
        """
    ), {"ts": ts, "te7": te7, "sys_bl": list(SYS_OP_BLACKLIST_RR)}).fetchall():
        if not r.sn or not r.op:
            continue
        refund_op_sns.setdefault(r.op, set()).add(r.sn)
        refund_op_amt[r.op] = refund_op_amt.get(r.op, 0.0) + float(r.amt or 0)

    # ---- 5. 按 op 拆分 成功/失败 + 退款操作工作量 (多人同SN各算各的) ----
    #   范围: refund_ops(退款专项组成员) + 所有 refund_op_sns 中出现的执行人
    staff_operators: dict[str, dict] = dict(all_ops_meta)  # 先加入 CS 接待侧的人员 meta
    for op in refund_op_sns.keys():
        if op not in staff_operators and op not in SYS_OP_BLACKLIST_RR:
            # 纯退款执行人 (没出现在 CS 接待侧) → 从维表补 meta
            staff_operators[op] = {"person_id": None, "name": op, "group": "退款执行", "specialty": None, "position": "退款执行人", "dept": "售后", "qw_user_id": None}
    # 再精确补一次维表信息(有可能维表里有但之前没圈进来)
    for pr in person_rows:
        op = pr.operator or ""
        if op and op in staff_operators:
            info = staff_operators[op]
            if not info.get("person_id"):
                info["person_id"] = getattr(pr, "person_id", None)
            if not info.get("group") or info.get("group") == "退款执行":
                info["group"] = pr.group_name or info.get("group") or "未分组"
            if not info.get("position") or info.get("position") == "退款执行人":
                info["position"] = pr.position or info.get("position")
            info["dept"] = pr.department or info.get("dept") or "未设置"
            info["name"] = pr.display_name or op
            if not info.get("qw_user_id"):
                info["qw_user_id"] = getattr(pr, "qw_user_id", None)

    # ---- 5b. 按 person_id 归一合并多 alias, 避免同一人显示多次 (2026-08-12) ----
    #   person_id 相同的 alias: 合倂 op_host_sns / refund_op_sns / refund_op_amt, 选 canonical op
    _pid_to_ops: dict[str, list[str]] = {}
    for op, info in staff_operators.items():
        pid = info.get("person_id")
        if pid is None:
            continue
        _pid_to_ops.setdefault(str(pid), []).append(op)
    # 仅处理多 alias 的分组
    for _pid, op_list in _pid_to_ops.items():
        if len(op_list) < 2:
            continue
        # 选 canonical op: 优先 display_name 与 alias 匹配、或 name 长度最短 (真名而非工号前缀)
        def _canonical_score(op):
            info = staff_operators.get(op, {})
            nm = (info.get("name") or op).strip()
            s = 0
            if nm == op.strip(): s += 10
            if "-" not in op: s += 5
            if len(op) <= len(nm): s += 2
            # 有接待量/退款量 优先
            s += len(op_host_sns.get(op, set())) * 0.01
            s += len(refund_op_sns.get(op, set())) * 0.01
            return -s
        op_list_sorted = sorted(op_list, key=_canonical_score)
        canonical = op_list_sorted[0]
        for alias in op_list_sorted[1:]:
            # 合并 op_host_sns
            for sn in op_host_sns.get(alias, set()):
                op_host_sns.setdefault(canonical, set()).add(sn)
            op_host_sns.pop(alias, None)
            # 合并 refund_op_sns
            for sn in refund_op_sns.get(alias, set()):
                refund_op_sns.setdefault(canonical, set()).add(sn)
            refund_op_sns.pop(alias, None)
            # 合并 refund_op_amt
            refund_op_amt[canonical] = float(refund_op_amt.get(canonical) or 0) + float(refund_op_amt.get(alias) or 0)
            refund_op_amt.pop(alias, None)
            # 移除 alias 条目
            staff_operators.pop(alias, None)

    def _is_qw_person(op: str, info: dict) -> bool:
        """企微人员判定: 别名以'企微'开头 / 部门为'企微' / 已绑定企微 qw_user_id。
        用于过滤退款挽回详情里 非企微(店铺/研发/生产/财务/测试等)人员 与 纯占位人员,
        避免大量 0 数据或无关人员干扰明细统计与展示。
        """
        if (op or "").startswith("企微"):
            return True
        if (info.get("dept") or "") == "企微":
            return True
        if (info.get("qw_user_id") or "").strip():
            return True
        return False

    staff_detail = []
    for op, info in staff_operators.items():
        # CS 接待工作量: 接待的真退款类SN
        cs_host = op_host_sns.get(op, set())
        cs_failed = cs_host & global_fail_sns
        cs_success = cs_host - global_fail_sns
        total_cs = len(cs_host)
        fail_cs = len(cs_failed)
        succ_cs = len(cs_success)

        # 退款操作工作量: 独立字段, 单独统计 (不重复计入 success/failed)
        ro_sns = refund_op_sns.get(op, set())
        ro_cnt = len(ro_sns)
        ro_amt = refund_op_amt.get(op, 0.0)

        # 跳过既没有接待也没有退款操作的人员 (避免无关人员出现在列表中)
        if total_cs == 0 and ro_cnt == 0:
            continue
        # 跳过非企微人员 (店铺/研发/生产/财务/测试等), 明细仅保留企微侧人员
        if not _is_qw_person(op, info):
            continue

        # 成功率口径: 基于 CS 接待侧
        # - 有接待的人员: 计算成功率
        # - 纯退款执行人(无CS接待): 成功率记为 None(空)
        total_for_rate = total_cs
        rate = (succ_cs / total_for_rate * 100) if total_for_rate else None

        staff_detail.append({
            "operator": op,
            "name": info.get("name") or op,
            "group": info.get("group") or "未分组",
            "department": info.get("dept") or "未设置",
            "position": info.get("position") or "",
            "total_sn": total_cs,           # CS 接待SN数 (退款类诉求接待)
            "success_sn": succ_cs,          # 接待成功SN数
            "failed_sn": fail_cs,           # 接待失败SN数
            "success_rate": round(rate, 2) if rate is not None else None,
            "refund_op_cnt": ro_cnt,        # 退款操作SN数: 作为 refunds.operator 或 service_tickets.handler 处理过的 SN
            "refund_op_amount": round(ro_amt, 2),  # 退款操作金额合计
            "_is_recovery_staff": info.get("is_recovery_staff", False),  # 是否是专职退款挽回人员
            "staff_type": "专职挽回" if info.get("is_recovery_staff", False) else ("退款操作" if total_cs == 0 else "其他人员挽回"),
        })
    staff_detail.sort(key=lambda x: (
        -(x.get("total_sn") or 0),            # 主排序: CS 接待SN数降序
        -(x.get("refund_op_cnt") or 0),       # 次排序: 退款操作SN数降序
        -(x.get("success_rate") or -1),       # 再次: 成功率降序
    ))

    # ---- 5b. SN 明细 (供前端折叠/表格展示) ----
    #   每个 SN 一行: 设备SN / 结果(成功/失败) / 处理人 / 退款原因 / 首次接待时间 / 金额
    sn_details: list[dict] = []
    sn_result_map: dict[str, str] = {}
    for sn in global_host_sns:
        sn_result_map[sn] = "failed" if sn in global_fail_sns else "success"
    # 按 (staff, sn) 聚合: 每人只保留自己接待过的 SN, 避免重复展示
    for op, info in staff_operators.items():
        # SN 明细同样仅保留企微侧人员, 与 staff_detail 过滤口径一致
        if not _is_qw_person(op, info):
            continue
        cs_host = op_host_sns.get(op, set())
        op_display = info.get("name") or op
        op_group = info.get("group") or "未分组"
        for sn in cs_host:
            result = sn_result_map.get(sn, "success")
            reason = sn_host_reason.get(sn) or ""
            ftime = sn_first_op_time.get(sn)
            fmeta = sn_failed_meta.get(sn, {}) if result == "failed" else {}
            amount = float(fmeta.get("amount") or 0) if result == "failed" else 0.0
            status = (fmeta.get("status") or "") if result == "failed" else ""
            src = (fmeta.get("src") or "") if result == "failed" else ""
            sn_details.append({
                "sn": sn,
                "result": result,
                "staff": op_display,
                "staff_group": op_group,
                "reason": reason,
                "time": ftime.isoformat() if ftime else None,
                "amount": round(amount, 2),
                "status": status,
                "src": src,
            })
    # 按结果 + 时间排序 (最新在前)
    sn_details.sort(key=lambda x: (
        0 if x["result"] == "failed" else 1,
        -(x["time"] and _dt.datetime.fromisoformat(x["time"]).timestamp() or 0),
    ))

    # ---- 5c. 将 sn_list 回填到 staff_detail, 供"每人展开查看SN明细" ----
    op_sn_list_map: dict[str, list] = {}
    for sd in sn_details:
        op_sn_list_map.setdefault(sd["staff"], []).append(sd)
    for row in staff_detail:
        row["sn_list"] = op_sn_list_map.get(row["name"], [])

    # ---- 6. 总体 KPI ----
    total_host = len(global_host_sns)
    total_fail = len(global_fail_sns)
    total_success = total_host - total_fail
    overall_rate = (total_success / total_host * 100) if total_host else 0.0

    # 金额拆分: 参考用户 Excel 分类
    #   失败(拦截失败): status 已退款 / 线上 退款成功 → fail_amount
    #   放弃(系统清退): status 申请中 / 无需退款 / sys操作 → abandon_amount
    #   退充(正常退充): remark/原因含"退充" → recharge_amount
    #   成功(拦截成功): 成功SN对应的估算金额 = 对应失败SN的平均金额 * 成功数 (粗略估算)
    abandon_cnt = 0; abandon_amount = 0.0
    fail_cnt = 0; fail_amount = 0.0
    recharge_cnt = 0; recharge_amount = 0.0
    for sn, meta in sn_failed_meta.items():
        status = (meta.get("status") or "").strip()
        reason = (meta.get("reason") or "").lower()
        amt = float(meta.get("amount") or 0)
        is_recharge = ("退充" in (meta.get("reason") or "")) or ("recharge" in reason)
        is_abandon = (meta.get("src") == "ticket" and status in ("待处理",)) or (status in ("无需退款", "申请中")) or (meta.get("method") == "线下")
        if is_recharge:
            recharge_cnt += 1; recharge_amount += amt
        elif is_abandon:
            abandon_cnt += 1; abandon_amount += amt
        else:
            fail_cnt += 1; fail_amount += amt

    success_cnt = max(0, total_success)  # 拦截成功单数 = 成功SN数
    # 成功金额 = 0 (业务口径: 挽回成功的设备没有进入退款流程, 无实际退款工单可对应金额, 暂不做估算)
    success_amount = 0.0

    actual_refund = fail_amount + recharge_amount  # 实际退款 = 失败金额 + 退充金额
    # 费占比改为单量口径: 成功单量 / (成功单量 + 实际退款单量(fail+recharge))
    # 金额口径的成功金额无法准确核算, 故用单量近似表达拦截比例
    actual_refund_cnt = fail_cnt + recharge_cnt
    denom_fee_cnt = success_cnt + actual_refund_cnt
    fee_ratio = (success_cnt / denom_fee_cnt * 100) if denom_fee_cnt else 0.0

    # 先占位: reason_order 将在第7部分原因分布生成后回填
    reason_order_placeholder: list[str] = []

    kpi = {
        # SN维度 (核心分母/分子)
        "total_sn": total_host,
        "success_sn": total_success,
        "failed_sn": total_fail,
        "success_rate": round(overall_rate, 2),
        # 单量维度 (对齐截图 放弃/失败/成功/退充 4类)
        "abandon_cnt": abandon_cnt,
        "fail_cnt": fail_cnt,
        "success_cnt": success_cnt,
        "recharge_cnt": recharge_cnt,
        "target_total_cnt": abandon_cnt + fail_cnt + success_cnt + recharge_cnt,
        # 金额维度 (单位: 元)
        # 注意: success_amount 恒为 0 (挽回成功无退款工单, 金额无法准确核算)
        "abandon_amount": round(abandon_amount, 2),
        "fail_amount": round(fail_amount, 2),
        "success_amount": round(success_amount, 2),
        "recharge_amount": round(recharge_amount, 2),
        "target_total_amount": round(abandon_amount + fail_amount + recharge_amount, 2),  # 不含 success_amount (为0)
        "actual_refund_amount": round(actual_refund, 2),
        "fee_ratio": round(fee_ratio, 2),  # 单量口径拦截比例, 非金额口径
        "_calc_note": {
            "denominator": "所有人员(含专职退款挽回专员、组长/主管、其他人员)接待的 problem_category='退款' 的SN去重总数 (不限制problem_type: 网络问题/不想用了/设备故障/其他等所有退款理由均计入)",
            "failed_rule": "分母SN 中, 在 [该SN最早接待时间, 最早接待时间+7天] T+7窗口内满足任一即判失败: 1)refunds表且status≠无需退款/驳回; 2)service_tickets退款类工单且status≠终止; 3)cs_record描述含退款流程关键词(退款说明/退款后重新充值/要求退回微信/非挽回用户)——大量企微退款操作未同步到refunds表,通过cs_record描述关键词补充判定",
            "success_rule": "「分母SN」 − 「失败SN」 → 挽留成功 (即接待过但最终没走到退款流程, 被沟通拦截下来; 专职挽回和组长/主管/其他人员的挽回均计入)",
            "t_window_note": "T+7窗口已关闭的历史数据(≥7天前接待)成功率才准确; 最近7天接待的数据因部分退款还在流程中,成功率会偏高属于正常",
            "staff_type_def": "专职挽回: Person.specialty=退款挽回的人员; 其他人员挽回: 非专职人员(组长/主管/咨询/套餐/投诉等)接待退款类SN且未产生退款; 退款操作: 仅执行退款操作(refunds.operator)无接待的人员",
            "multi_user_rule": "工作负荷/绩效层面: 同个SN 允许多人各算各的. 接待侧每人算各自的 total_sn/success_sn/failed_sn; 退款操作侧(refunds.operator/service_tickets.handler)单独计入 staff_detail.refund_op_cnt / refund_op_amount, 不重复计入成功率",
            "success_amount_rule": "成功金额暂不核算 (挽留成功的设备未进入退款流程, 无实际退款工单金额可对应)",
            "fee_ratio_rule": "费占比(单量口径) = 成功单数 / (成功单数 + 实际退款单数(失败+退充)) · 参考目标 37%+",
            "staff_scope": "所有人员(含专职退款挽回专员、组长/主管等管理人员、其他岗位人员)的退款类接待 + 退款操作执行人; 已排除系统别名(system/机器人/用户自助等)",
            "reason_order": reason_order_placeholder,
        },
    }

    # ---- 7. 退款原因分布 (挽留失败SN) → 统一 cs_record.problem_type 口径 (2026-08-12)
    #   旧口径: 使用 refunds.refund_reason + service_tickets.service_item (真实退款原因)
    #   新口径: 使用 cs_record.problem_type (客户首次表达退款意愿时的原因)
    #   好处: ① 与 退款意愿/挽回成功 分布图 完全同口径 (总数 = 分母 = 成功+失败)
    #        ② 归一化到 同样 7大类 便于三图对比
    reason_bucket: dict[str, int] = {}
    for sn in global_fail_sns:
        r = sn_host_reason.get(sn) or "其他(填写备注)"
        reason_bucket[r] = reason_bucket.get(r, 0) + 1
    reason_dist = []
    for reason, cnt in reason_bucket.items():
        ratio = (cnt / total_fail * 100) if total_fail else 0.0
        reason_dist.append({
            "reason": reason,
            "order_cnt": cnt,
            "ratio": round(ratio, 2),
            "amount": 0.0,  # 口径统一为 cs_record 原因分类, 暂不拆金额
            "success_rate": 0.0,
        })
    reason_dist.sort(key=lambda x: -x["order_cnt"])

    # 回填 kpi._calc_note.reason_order (按 单量降序 的原因列表, 供 日期×原因 矩阵前端使用)
    kpi["_calc_note"]["reason_order"] = [r["reason"] for r in reason_dist]

    # ---- 7b. 退款意愿原因分布 (全分母SN) + 挽回成功原因分布 (成功SN) ----
    #   退款意愿: 所有接待过退款类问题的SN, 按cs_record.problem_type归一化原因分布
    #   挽回成功: 分母SN - 失败SN, 即被沟通拦截未进入退款流程的SN, 按同样原因分布
    def _build_reason_dist(sn_set: set, total: int) -> list[dict]:
        bucket: dict[str, int] = {}
        for sn in sn_set:
            r = sn_host_reason.get(sn) or "其他(填写备注)"
            bucket[r] = bucket.get(r, 0) + 1
        out = []
        for reason, cnt in bucket.items():
            ratio = (cnt / total * 100) if total else 0.0
            out.append({"reason": reason, "order_cnt": cnt, "ratio": round(ratio, 2)})
        out.sort(key=lambda x: -x["order_cnt"])
        return out

    total_host = len(global_host_sns)
    success_sns = global_host_sns - global_fail_sns
    total_success = len(success_sns)
    wish_reason_dist = _build_reason_dist(global_host_sns, total_host)
    success_reason_dist = _build_reason_dist(success_sns, total_success)

    # ---- 8. 周趋势: 最近 8 周 拦截订单汇总 (新口径+T+7) ----
    now_sh = _dt.datetime.now(TZ)
    weekly_trend = []
    for wi in range(7, -1, -1):
        wk_end = (now_sh - _dt.timedelta(weeks=wi, days=now_sh.weekday())).replace(hour=23, minute=59, second=59, microsecond=0)
        wk_start = wk_end - _dt.timedelta(days=6, hours=23, minutes=59, seconds=59)
        wk_start_utc = wk_start.astimezone(_dt.timezone.utc).replace(tzinfo=None)
        wk_end_utc = wk_end.astimezone(_dt.timezone.utc).replace(tzinfo=None)
        wk_end7_utc = wk_end_utc + _dt.timedelta(days=7)  # T+7
        week_label = f"{(now_sh.isocalendar()[0])}-W{(now_sh - _dt.timedelta(weeks=wi)).isocalendar()[1]}"

        # 当周 所有人员 cs_record 接待【真退款类】 SN (2026-08-11: 包含所有人员,不只专职挽回)
        wk_cs = db.execute(text(
            """
            SELECT DISTINCT NULLIF(TRIM(c.device_sn),'') AS sn,
                   MIN(c.operation_time) AS first_op_time
            FROM cs_records c
            WHERE c.operation_time IS NOT NULL
              AND c.operation_time >= :ts AND c.operation_time <= :te
              AND NOT (c.operator = ANY(:sys_bl))
              AND c.problem_category = '退款'
              AND NOT (
                  COALESCE(c.description,'') ILIKE ANY(ARRAY[:k1,:k2,:k3,:k4,:k5,:k6,:k7])
               OR COALESCE(c.execution_result,'') ILIKE ANY(ARRAY[:k1,:k2,:k3,:k4,:k5,:k6,:k7])
               OR COALESCE(c.solution,'') ILIKE ANY(ARRAY[:k1,:k2,:k3,:k4,:k5,:k6,:k7])
              )
            GROUP BY sn
            """
        ), {"ts": wk_start_utc, "te": wk_end_utc, "sys_bl": list(SYS_OP_BLACKLIST_RR), **kw_params}).fetchall()
        wk_host: set[str] = set(r.sn for r in wk_cs if r.sn)
        wk_first_op = {r.sn: r.first_op_time for r in wk_cs if r.sn}

        # 当周 失败SN (T+7 窗口: 不限制operator/refund_method, 工单用register_time)
        wk_fail_rows = db.execute(text(
            """
            SELECT NULLIF(TRIM(r.device_sn),'') AS sn,
                   'refund' AS src, r.refund_reason AS reason,
                   COALESCE(r.actual_amount, r.refund_amount, 0) AS amount,
                   r.status AS status, r.refund_method AS method,
                   r.created_at_raw AS event_time
            FROM refunds r
            WHERE r.created_at_raw IS NOT NULL
              AND r.created_at_raw >= :ts AND r.created_at_raw <= :te7
              AND NULLIF(TRIM(r.device_sn),'') IS NOT NULL
              AND COALESCE(r.status, '') NOT IN ('无需退款', '驳回')
            UNION ALL
            SELECT NULLIF(TRIM(st.device_sn),'') AS sn,
                   'ticket' AS src, st.service_item AS reason,
                   COALESCE(st.refund_amount, st.estimated_amount, 0) AS amount,
                   st.status AS status, st.service_category AS method,
                   st.register_time AS event_time
            FROM service_tickets st
            WHERE st.register_time IS NOT NULL
              AND st.register_time >= :ts AND st.register_time <= :te7
              AND NULLIF(TRIM(st.device_sn),'') IS NOT NULL
              AND COALESCE(st.service_category,'') = '退款'
              AND COALESCE(st.status, '') != '终止'
            """
        ), {"ts": wk_start_utc, "te7": wk_end7_utc}).fetchall()
        wk_fail_map: dict[str, dict] = {}
        for r in wk_fail_rows:
            sn = r.sn or ""
            if not sn or sn not in wk_host:
                continue
            # 校验事件时间必须在 [接待时间, 接待时间+7天] 内
            first_op = wk_first_op.get(sn)
            if not first_op or not r.event_time:
                continue
            if not (first_op <= r.event_time <= first_op + _dt.timedelta(days=7)):
                continue
            amt = float(r.amount or 0)
            prev = wk_fail_map.get(sn)
            if prev is None or amt > float(prev.get("amount") or 0):
                wk_fail_map[sn] = {"reason": r.reason, "amount": amt, "status": r.status, "method": r.method, "src": r.src}
        wk_fail_sns = set(wk_fail_map.keys())

        wk_total = len(wk_host)
        wk_fail_n = len(wk_fail_sns)
        wk_succ_n = wk_total - wk_fail_n
        wk_rate = (wk_succ_n / wk_total * 100) if wk_total else 0.0
        wk_ab = 0; wk_fl = 0; wk_rc = 0
        for sn in wk_fail_sns:
            m = wk_fail_map[sn]
            st = (m.get("status") or "").strip()
            is_rc = "退充" in (m.get("reason") or "")
            is_ab = (m.get("src") == "ticket" and st == "待处理") or (st in ("无需退款", "申请中")) or (m.get("method") == "线下")
            if is_rc: wk_rc += 1
            elif is_ab: wk_ab += 1
            else: wk_fl += 1

        weekly_trend.append({
            "week_label": week_label,
            "date_range": [wk_start.strftime("%m/%d"), wk_end.strftime("%m/%d")],
            "abandon_cnt": wk_ab,
            "fail_cnt": wk_fl,
            "success_cnt": wk_succ_n,
            "recharge_cnt": wk_rc,
            "total_cnt": wk_ab + wk_fl + wk_succ_n + wk_rc,
            "success_rate": round(wk_rate, 2),
        })

    # ---- 9. 日×原因 明细 (当前时间窗口内, 按天 × 原因 交叉) (新口径) ----
    daily_reason = []
    if global_fail_sns:
        sn_first_day_sql = text(
            """
            SELECT sn, MIN(day) AS first_day
            FROM (
                SELECT NULLIF(TRIM(r.device_sn),'') AS sn,
                       DATE(r.created_at_raw AT TIME ZONE 'Asia/Shanghai') AS day
                FROM refunds r
                WHERE r.created_at_raw IS NOT NULL
                  AND r.created_at_raw >= :ts AND r.created_at_raw <= :te7
                  AND NULLIF(TRIM(r.device_sn),'') = ANY(:sn_arr)
                  AND COALESCE(r.status, '') NOT IN ('无需退款', '驳回')
                UNION ALL
                SELECT NULLIF(TRIM(st.device_sn),'') AS sn,
                       DATE(st.register_time AT TIME ZONE 'Asia/Shanghai') AS day
                FROM service_tickets st
                WHERE st.register_time IS NOT NULL
                  AND st.register_time >= :ts AND st.register_time <= :te7
                  AND NULLIF(TRIM(st.device_sn),'') = ANY(:sn_arr)
                  AND COALESCE(st.service_category,'') = '退款'
                  AND COALESCE(st.status, '') != '终止'
            ) x
            WHERE sn IS NOT NULL
            GROUP BY sn
            """
        )
        day_rows = db.execute(
            sn_first_day_sql,
            {"ts": ts, "te7": te7, "sn_arr": list(global_fail_sns)},
        ).fetchall()
        sn_day: dict[str, str] = {r.sn: str(r.first_day) for r in day_rows if r.sn and r.first_day}

        # 日 -> 原因 -> cnt
        day_reason_bucket: dict[str, dict[str, int]] = {}
        all_reasons_seen: set[str] = set()
        for sn in global_fail_sns:
            d = sn_day.get(sn)
            if not d:
                continue
            raw = sn_failed_meta.get(sn, {}).get("reason")
            b = _normalize_refund_reason(raw)
            all_reasons_seen.add(b)
            inner = day_reason_bucket.setdefault(d, {})
            inner[b] = inner.get(b, 0) + 1

        ordered_reasons = [r["reason"] for r in reason_dist]
        for extra in sorted(all_reasons_seen - set(ordered_reasons)):
            ordered_reasons.append(extra)
        ordered_days = sorted(day_reason_bucket.keys())

        daily_reason = []
        for d in ordered_days:
            total_d = sum(day_reason_bucket.get(d, {}).values())
            row = {"date": d, "_total": total_d}
            for reason in ordered_reasons:
                c = day_reason_bucket.get(d, {}).get(reason, 0)
                row[f"{reason}_cnt"] = c
                row[f"{reason}_ratio"] = round(c / total_d * 100, 2) if total_d else 0.0
            daily_reason.append(row)
        kpi["_calc_note"]["reason_order"] = ordered_reasons

    # ---- 10. 按日 挽回趋势 (近30天 或 当前时间窗口 范围内 日粒度) (新口径 新增) ----
    daily_trend: list[dict] = []
    try:
        # 生成每日日期序列
        start_day = ts.astimezone(TZ).date() if hasattr(ts, 'astimezone') else ts.date()
        end_day = te.astimezone(TZ).date() if hasattr(te, 'astimezone') else te.date()
        # 强制至少近30天用于 今日视图观察
        min_start = _dt.date.today() - _dt.timedelta(days=29)
        plot_start = min(start_day, min_start)
        dates_list: list[_dt.date] = []
        cur_d = plot_start
        while cur_d <= end_day:
            dates_list.append(cur_d)
            cur_d += _dt.timedelta(days=1)
        if dates_list:
            ts0 = _dt.datetime.combine(plot_start, _dt.time.min).replace(tzinfo=TZ).astimezone(_dt.timezone.utc).replace(tzinfo=None)
            te0 = _dt.datetime.combine(end_day, _dt.time.max).replace(tzinfo=TZ).astimezone(_dt.timezone.utc).replace(tzinfo=None)
            te0_7 = te0 + _dt.timedelta(days=7)
            # 日粒度 分母SN (按天, 2026-08-11: 包含所有人员,不只专职挽回)
            day_host_rows = db.execute(text(
                """
                SELECT DATE(c.operation_time AT TIME ZONE 'Asia/Shanghai') AS d,
                       NULLIF(TRIM(c.device_sn),'') AS sn
                FROM cs_records c
                WHERE c.operation_time IS NOT NULL
                  AND c.operation_time >= :ts AND c.operation_time <= :te
                  AND NOT (c.operator = ANY(:sys_bl))
                  AND c.problem_category = '退款'
                  AND NULLIF(TRIM(c.device_sn),'') IS NOT NULL
                  AND NOT (
                      COALESCE(c.description,'') ILIKE ANY(ARRAY[:k1,:k2,:k3,:k4,:k5,:k6,:k7])
                   OR COALESCE(c.execution_result,'') ILIKE ANY(ARRAY[:k1,:k2,:k3,:k4,:k5,:k6,:k7])
                   OR COALESCE(c.solution,'') ILIKE ANY(ARRAY[:k1,:k2,:k3,:k4,:k5,:k6,:k7])
                  )
                """
            ), {"ts": ts0, "te": te0, "sys_bl": list(SYS_OP_BLACKLIST_RR), **kw_params}).fetchall()
            day_to_host: dict[str, set[str]] = {}
            for r in day_host_rows:
                if r.d is None or not r.sn: continue
                key = str(r.d)
                day_to_host.setdefault(key, set()).add(r.sn)
            # 日粒度 失败SN (T+7, 修正口径: 不限制operator/refund_method, 工单用register_time)
            # 与 KPI global_fail_sns 一致: refunds + 退款工单 + cs_record关键词 三来源
            ck = _RR_CS_FAIL_KW
            day_fail_rows = db.execute(text(
                """
                SELECT NULLIF(TRIM(r.device_sn),'') AS sn
                FROM refunds r
                WHERE r.created_at_raw IS NOT NULL
                  AND r.created_at_raw >= :ts AND r.created_at_raw <= :te7
                  AND COALESCE(r.status, '') NOT IN ('无需退款', '驳回')
                  AND NULLIF(TRIM(r.device_sn),'') IS NOT NULL
                UNION
                SELECT NULLIF(TRIM(st.device_sn),'') AS sn
                FROM service_tickets st
                WHERE st.register_time IS NOT NULL
                  AND st.register_time >= :ts AND st.register_time <= :te7
                  AND COALESCE(st.service_category,'') = '退款'
                  AND COALESCE(st.status, '') != '终止'
                  AND NULLIF(TRIM(st.device_sn),'') IS NOT NULL
                UNION
                SELECT NULLIF(TRIM(c.device_sn),'') AS sn
                FROM cs_records c
                WHERE c.operation_time IS NOT NULL
                  AND c.operation_time >= :ts AND c.operation_time <= :te7
                  AND c.problem_category = '退款'
                  AND NOT (c.operator = ANY(:sys_bl))
                  AND NULLIF(TRIM(c.device_sn),'') IS NOT NULL
                  AND (COALESCE(c.description,'') ILIKE ANY(ARRAY[:ck1,:ck2,:ck3,:ck4])
                    OR COALESCE(c.execution_result,'') ILIKE ANY(ARRAY[:ck1,:ck2,:ck3,:ck4])
                    OR COALESCE(c.solution,'') ILIKE ANY(ARRAY[:ck1,:ck2,:ck3,:ck4]))
                """
            ), {"ts": ts0, "te7": te0_7, "sys_bl": list(SYS_OP_BLACKLIST_RR),
                "ck1": f"%{ck[0]}%", "ck2": f"%{ck[1]}%", "ck3": f"%{ck[2]}%", "ck4": f"%{ck[3]}%"}).fetchall()
            fail_sn_set = set(r.sn for r in day_fail_rows if r.sn)
            daily_trend = []
            for d_obj in dates_list:
                key = d_obj.isoformat()
                h = day_to_host.get(key, set())
                f = h & fail_sn_set
                total = len(h)
                fail_n = len(f)
                succ_n = total - fail_n
                rate = round((succ_n / total * 100), 2) if total else 0.0
                daily_trend.append({
                    "date": key,
                    "total_sn": total,
                    "success_sn": succ_n,
                    "failed_sn": fail_n,
                    "success_rate": rate,
                })
    except Exception as _daily_e:
        logger.warning(f"退款挽回 按日趋势 计算异常: {_daily_e}")
        daily_trend = []

    # ---- 11. 今日 按小时 挽回趋势 (00:00~当前) (新口径 新增) ----
    hourly_today: list[dict] = []
    try:
        today_sh = _dt.datetime.now(TZ).date()
        ts_today = _dt.datetime.combine(today_sh, _dt.time.min).replace(tzinfo=TZ).astimezone(_dt.timezone.utc).replace(tzinfo=None)
        te_today = _dt.datetime.now(TZ).astimezone(_dt.timezone.utc).replace(tzinfo=None)
        # 今日 分母SN (按小时, 2026-08-11: 包含所有人员,不只专职挽回)
        hr_rows = db.execute(text(
            """
            SELECT EXTRACT(HOUR FROM c.operation_time AT TIME ZONE 'Asia/Shanghai') AS hh,
                   NULLIF(TRIM(c.device_sn),'') AS sn
            FROM cs_records c
            WHERE c.operation_time IS NOT NULL
              AND c.operation_time >= :ts AND c.operation_time <= :te
              AND NOT (c.operator = ANY(:sys_bl))
              AND c.problem_category = '退款'
              AND NULLIF(TRIM(c.device_sn),'') IS NOT NULL
              AND NOT (
                  COALESCE(c.description,'') ILIKE ANY(ARRAY[:k1,:k2,:k3,:k4,:k5,:k6,:k7])
               OR COALESCE(c.execution_result,'') ILIKE ANY(ARRAY[:k1,:k2,:k3,:k4,:k5,:k6,:k7])
               OR COALESCE(c.solution,'') ILIKE ANY(ARRAY[:k1,:k2,:k3,:k4,:k5,:k6,:k7])
              )
            """
        ), {"ts": ts_today, "te": te_today, "sys_bl": list(SYS_OP_BLACKLIST_RR), **kw_params}).fetchall()
        hr_to_host: dict[int, set[str]] = {}
        for r in hr_rows:
            if r.hh is None or not r.sn: continue
            h = int(float(r.hh))
            hr_to_host.setdefault(h, set()).add(r.sn)
        # 今日 失败SN (所有今日接待的分母SN中, 有任何退款记录 = 今日判断失败, T+7宽限会导致今日数据部分还没出来, 但对图表走势无影响)
        today_all_sns = set()
        for s in hr_to_host.values():
            today_all_sns.update(s)
        today_fail_sns: set[str] = set()
        if today_all_sns:
            # 与 KPI global_fail_sns 口径一致: refunds + 退款工单 + cs_record关键词 三来源
            ck = _RR_CS_FAIL_KW
            tfr = db.execute(text(
                """
                WITH arr AS (SELECT unnest(CAST(:sn_arr AS text[])) AS sn)
                SELECT DISTINCT NULLIF(TRIM(r.device_sn),'') AS sn FROM refunds r JOIN arr a ON NULLIF(TRIM(r.device_sn),'') = a.sn
                 WHERE COALESCE(r.status, '') NOT IN ('无需退款', '驳回')
                UNION
                SELECT DISTINCT NULLIF(TRIM(st.device_sn),'') AS sn FROM service_tickets st JOIN arr a ON NULLIF(TRIM(st.device_sn),'') = a.sn
                 WHERE COALESCE(st.service_category,'') = '退款' AND COALESCE(st.status, '') != '终止'
                UNION
                SELECT DISTINCT NULLIF(TRIM(c.device_sn),'') AS sn
                FROM cs_records c JOIN arr a ON NULLIF(TRIM(c.device_sn),'') = a.sn
                 WHERE c.problem_category = '退款'
                   AND NOT (c.operator = ANY(:sys_bl))
                   AND (COALESCE(c.description,'') ILIKE ANY(ARRAY[:ck1,:ck2,:ck3,:ck4])
                     OR COALESCE(c.execution_result,'') ILIKE ANY(ARRAY[:ck1,:ck2,:ck3,:ck4])
                     OR COALESCE(c.solution,'') ILIKE ANY(ARRAY[:ck1,:ck2,:ck3,:ck4]))
                """
            ), {"sn_arr": list(today_all_sns), "sys_bl": list(SYS_OP_BLACKLIST_RR),
                "ck1": f"%{ck[0]}%", "ck2": f"%{ck[1]}%", "ck3": f"%{ck[2]}%", "ck4": f"%{ck[3]}%"}).fetchall()
            today_fail_sns = set(r.sn for r in tfr if r.sn)
        max_hh = _dt.datetime.now(TZ).hour
        hourly_today = []
        for h in range(0, max_hh + 1):
            hh_set = hr_to_host.get(h, set())
            h_fail = hh_set & today_fail_sns
            total = len(hh_set)
            fail_n = len(h_fail)
            hourly_today.append({
                "hour": f"{h:02d}",
                "total_sn": total,
                "success_sn": total - fail_n,
                "failed_sn": fail_n,
            })
    except Exception as _hourly_e:
        logger.warning(f"退款挽回 今日按小时趋势 计算异常: {_hourly_e}")
        hourly_today = []

    # ---- 2026-08-12 追加: Top5 高风险设备 + 金额分桶 (Tab1 强化) ----
    top_risk_devices = []
    amount_buckets = [
        {"bucket": "小额(<100)", "sn_cnt": 0, "amount": 0.0, "ratio": 0.0},
        {"bucket": "中额(100-500)", "sn_cnt": 0, "amount": 0.0, "ratio": 0.0},
        {"bucket": "大额(500-2000)", "sn_cnt": 0, "amount": 0.0, "ratio": 0.0},
        {"bucket": "超大额(>2000)", "sn_cnt": 0, "amount": 0.0, "ratio": 0.0},
    ]
    _risk_devices = []
    for sn in global_fail_sns:
        meta = sn_failed_meta.get(sn, {})
        amt = float(meta.get("amount") or 0)
        ops = sn_host_ops.get(sn, [])
        op_name = all_ops_meta.get(ops[0], {}).get("name", ops[0] if ops else "") if ops else ""
        _risk_devices.append({"device_sn": sn, "reason": _normalize_refund_reason(meta.get("reason")),
                              "amount": round(amt, 2), "operator": ops[0] if ops else "",
                              "operator_name": op_name, "status": meta.get("status", "")})
        if amt < 100:
            amount_buckets[0]["sn_cnt"] += 1; amount_buckets[0]["amount"] += amt
        elif amt < 500:
            amount_buckets[1]["sn_cnt"] += 1; amount_buckets[1]["amount"] += amt
        elif amt < 2000:
            amount_buckets[2]["sn_cnt"] += 1; amount_buckets[2]["amount"] += amt
        else:
            amount_buckets[3]["sn_cnt"] += 1; amount_buckets[3]["amount"] += amt
    _risk_devices.sort(key=lambda x: -x["amount"])
    top_risk_devices = _risk_devices[:5]
    _total_fail = len(global_fail_sns) or 1
    for b in amount_buckets:
        b["amount"] = round(b["amount"], 2)
        b["ratio"] = round(b["sn_cnt"] / _total_fail * 100, 1)

    return {
        "date": d_iso,
        "date_end": d_end_iso,
        "kpi": kpi,
        "reason_dist": reason_dist,
        "wish_reason_dist": wish_reason_dist,
        "success_reason_dist": success_reason_dist,
        "staff_detail": staff_detail,
        "staff_details": staff_detail,  # 兼容前端两种命名
        "sn_details": sn_details,
        "weekly_trend": weekly_trend,
        "daily_reason": daily_reason,
        "daily_trend": daily_trend,
        "hourly_today": hourly_today,
        # 2026-08-12 追加: Top5 高风险设备 + 金额分桶 (Tab1 强化)
        "top_risk_devices": top_risk_devices,
        "amount_buckets": amount_buckets,
        # 2026-08-09 追加: 数据核查调试信息 (前端不展示, 便于有疑问时在接口响应中直接核对)
        "_debug": {
            "refund_op_count": len(refund_op_sns),
            "refund_op_list_sample": list(refund_op_sns.keys())[:20],
            "recovery_staff_count": len(recovery_ops),
            "recovery_staff_list": list(recovery_ops.keys())[:20],
            "cs_sn_row_count": len(cs_sn_rows),
            "global_host_sn_count": len(global_host_sns),
            "global_fail_sn_count": len(global_fail_sns),
            "fail_refund_sn_cnt": sum(1 for v in sn_failed_meta.values() if v.get("src") == "refund"),
            "fail_ticket_sn_cnt": sum(1 for v in sn_failed_meta.values() if v.get("src") == "ticket"),
            "fail_cs_kw_sn_cnt": sum(1 for v in sn_failed_meta.values() if v.get("src") == "cs_kw"),
            "cs_fail_keywords": CS_FAIL_KW,
            "sys_keywords_used": SYS_KW,
            "sys_blacklist_ops": list(SYS_OP_BLACKLIST_RR),
            "problem_type_filter": ['不想用了', '充值错误', '设备故障', '服务原因'],
        },
    }

def _empty_kpi() -> dict:
    return {
        "total_sn": 0, "success_sn": 0, "failed_sn": 0, "success_rate": 0.0,
        "abandon_cnt": 0, "fail_cnt": 0, "success_cnt": 0, "recharge_cnt": 0, "target_total_cnt": 0,
        "abandon_amount": 0.0, "fail_amount": 0.0, "success_amount": 0.0, "recharge_amount": 0.0,
        "target_total_amount": 0.0, "actual_refund_amount": 0.0, "fee_ratio": 0.0,
        "_calc_note": {},
    }
