"""接待/操作人员维度: 操作人统计、接待概览/分类/人均/分组明细、人员归并。"""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, func, select, text
from sqlalchemy.orm import Session

from app.models.cs_record import CsRecord
from app.models.group import Group, PersonGroupMembership
from app.models.person import Person, PersonAlias
from app.models.refund import Refund
from app.models.ticket import ServiceTicket

# 跨子模块引用(拆分自 app.services.analytics, 原同文件内直接调用)
from app.services.analytics._core import (
    COMPLAINT_SPECIALTY,
    _date_range,
    _route_a_online_refund_filter,
    _strip_qw_prefix,
)

# 2026-09-04 F-P1-1: 首页"今日工作效率"同一人多次出现的根因修复。
# today_staff_efficiency 的 SQL 按源表 operator/handler 字符串 GROUP BY, 同一人在
# cs_records.operator / refunds.operator / service_tickets.handler 三套文本里写法不一致
# (空格/企微-前缀/别名未登记/跨表变体)时, 经 PersonAlias 等值 join 会出多行(部分 person_id=None),
# 而 person 聚合模式此前"原样返回"不归并 → 界面同一人出现多行。
# 本函数: aggregate_by=person 时按人归并 — 归并键 = person_id(非空) 兜底 归一 name。
# 6 个专项桶名须与 today_staff_efficiency 内 CATS 保持一致。
_PERSON_CAT_NAMES = (
    "咨询接待", "套餐专项", "退款挽回", "退款处理",
    "内部投诉", "外部投诉",
    COMPLAINT_SPECIALTY,  # ★ 合并后的投诉专项
)

_PERSON_MERGE_NUM_KEYS = (
    "reception_count", "reception_device_count", "refund_op_count",
    "sp_zixun", "sp_taocan", "sp_recovery", "sp_processing", "sp_internal", "sp_external",
    "pp_zixun", "pp_taocan", "pp_recovery", "pp_processing", "pp_internal", "pp_external",
    "work_score", "eff_score",
)

_PERSON_MERGE_ATTR_KEYS = (
    "operator", "name", "person_id", "department", "group", "position",
    "current_specialty", "hr_category", "qw_user_id", "weights", "transfer_penalty",
)

def _merge_person_rows(recs: list[dict]) -> list[dict]:
    """真人员工记录按人归并(aggregate_by=person 专用)。

    - 归并键: person_id 非空 → "pid:{person_id}"; 否则 → "name:{归一 name}"
      (同名但均未登记到 PersonAlias 才可能误合并, 本项目人名唯一性强 + alias 覆盖率高, 风险极低)
    - 数值字段直接加总; 人员属性取"第一个有值", qw_user_id 非空优先
    - breakdown 6 桶 sessions/people/work_contrib/eff_contrib 加总,
      coef 展示用 work_contrib/sessions(加权平均系数, 与 specialty 聚合后语义一致)
    - work_score/eff_score = 各行直接加总后 round(与 tooltip"合计=score"一致)
    """
    if not recs:
        return recs

    def _mkey(r: dict) -> str:
        pid = r.get("person_id")
        return f"pid:{pid}" if pid is not None else f"name:{r.get('name') or ''}"

    merged: dict[str, dict] = {}
    order: list[str] = []
    for r in recs:
        k = _mkey(r)
        if k not in merged:
            order.append(k)
            base = {kk: r.get(kk) for kk in _PERSON_MERGE_ATTR_KEYS}
            base["is_management"] = bool(r.get("is_management"))
            base["breakdown"] = {
                c: {
                    "sessions": 0, "people": 0,
                    "weight": ((r.get("breakdown") or {}).get(c) or {}).get("weight", 1.0),
                    "coef": 0.0, "is_own": True, "work_contrib": 0.0, "eff_contrib": 0.0,
                }
                for c in _PERSON_CAT_NAMES
            }
            for kk in _PERSON_MERGE_NUM_KEYS:
                base[kk] = 0
            base["rank"] = None
            base["is_virtual"] = False
            base["_calc_note"] = {
                **((r.get("_calc_note") or {})),
                "merge_note": "同人多行已按人归并(2026-09-04): person_id/归一名相同行合并",
            }
            merged[k] = base
        else:
            b = merged[k]
            for kk in _PERSON_MERGE_ATTR_KEYS:  # 属性取"更优值": 空/占位值(未设置/未分组)被更有值行覆盖, qw_user_id 优先企微行
                cur = b.get(kk)
                nxt = r.get(kk)
                if not cur or str(cur).strip() in ("未设置", "未分组", "—"):
                    if nxt:
                        b[kk] = nxt
            if r.get("is_management"):
                b["is_management"] = True
        b = merged[k]
        for kk in _PERSON_MERGE_NUM_KEYS:
            b[kk] = b[kk] + (r.get(kk) or 0)
        rb = r.get("breakdown") or {}
        for c in _PERSON_CAT_NAMES:
            rbv = rb.get(c) or {}
            bbv = b["breakdown"][c]
            bbv["sessions"] += int(rbv.get("sessions") or 0)
            bbv["people"] += int(rbv.get("people") or 0)
            bbv["work_contrib"] += float(rbv.get("work_contrib") or 0.0)
            bbv["eff_contrib"] += float(rbv.get("eff_contrib") or 0.0)
    out_list = [merged[k] for k in order]
    for b in out_list:
        b["work_score"] = round(float(b["work_score"] or 0), 2)
        b["eff_score"] = round(float(b["eff_score"] or 0), 2)
        for c in _PERSON_CAT_NAMES:
            bbv = b["breakdown"][c]
            bbv["work_contrib"] = round(float(bbv["work_contrib"]), 1)
            bbv["eff_contrib"] = round(float(bbv["eff_contrib"]), 1)
            if bbv.get("sessions"):
                bbv["coef"] = round(float(bbv["work_contrib"]) / float(bbv["sessions"]), 3)
    return out_list

def operator_ticket_stats(db: Session, start: datetime | None = None, end: datetime | None = None) -> list[dict]:
    """人员工单处理统计(处理人) — 姓名归一: PersonAlias.alias = handler → Person.display_name"""
    start, end = _date_range(start, end)
    op_prs = _operator_to_person_cte()
    rows = db.execute(
        select(
            func.coalesce(op_prs.c.display_name, ServiceTicket.handler).label("operator"),
            func.count().label("ticket_count"),
            func.coalesce(func.sum(ServiceTicket.refund_amount), 0).label("refund_amount"),
            func.coalesce(func.avg(ServiceTicket.handle_duration), 0).label("avg_duration"),
        )
        .select_from(ServiceTicket)
        .outerjoin(op_prs, op_prs.c.operator == ServiceTicket.handler)
        .where(ServiceTicket.handler.isnot(None), ServiceTicket.register_time.between(start, end))
        .group_by(text("1"))
        .order_by(func.count().desc())
    ).all()
    return [
        {
            "operator": _strip_qw_prefix(r[0]),
            "ticket_count": r[1],
            "refund_amount": float(r[2] or 0),
            "avg_handle_duration": round(float(r[3] or 0), 2),
        }
        for r in rows
    ]

def operator_audit_stats(db: Session, start: datetime | None = None, end: datetime | None = None) -> list[dict]:
    """人员审核统计(审核人) 【统一口径: 路线A 退费单-线上直退】
    姓名归一: PersonAlias.alias = auditor → Person.display_name
    """
    start, end = _date_range(start, end)
    op_prs = _operator_to_person_cte()
    rows = db.execute(
        select(
            func.coalesce(op_prs.c.display_name, Refund.auditor).label("operator"),
            func.count().label("audit_count"),
            func.coalesce(func.sum(Refund.refund_amount), 0).label("audit_amount"),
        )
        .select_from(Refund)
        .outerjoin(op_prs, op_prs.c.operator == Refund.auditor)
        .where(Refund.auditor.isnot(None),
               Refund.created_at_raw.between(start, end),
               *_route_a_online_refund_filter())
        .group_by(text("1"))
        .order_by(func.sum(Refund.refund_amount).desc())
    ).all()
    return [
        {"operator": _strip_qw_prefix(r[0]), "audit_count": r[1], "audit_amount": float(r[2] or 0)}
        for r in rows
    ]

def operator_refund_stats(db: Session, start: datetime | None = None, end: datetime | None = None) -> list[dict]:
    """人员退费操作统计(操作人,排除 sys/system) 【统一口径: 路线A 退费单-线上直退】
    姓名归一: PersonAlias.alias = operator → Person.display_name;
    sys/system/SYS 等系统变体由于已在 alias 里链接到"系统自动", 所以额外再过滤掉 display_name='系统自动' 确保一致
    """
    start, end = _date_range(start, end)
    op_prs = _operator_to_person_cte()
    rows = db.execute(
        select(
            func.coalesce(op_prs.c.display_name, Refund.operator).label("operator"),
            func.count().label("op_count"),
            func.coalesce(func.sum(Refund.refund_amount), 0).label("refund_amount"),
        )
        .select_from(Refund)
        .outerjoin(op_prs, op_prs.c.operator == Refund.operator)
        .where(
            Refund.operator.isnot(None),
            Refund.operator.notin_(["sys", "system"]),
            Refund.created_at_raw.between(start, end),
            *_route_a_online_refund_filter(),
        )
        .group_by(text("1"))
        .having(func.coalesce(op_prs.c.display_name, Refund.operator) != "系统自动")
        .order_by(func.sum(Refund.refund_amount).desc())
    ).all()
    return [
        {"operator": _strip_qw_prefix(r[0]), "op_count": r[1], "refund_amount": float(r[2] or 0)}
        for r in rows
    ]



# ---------- 看板5: 客服接待看板 (对齐周报4模块) ----------

# 接待分项分类(对齐周报:投诉/退款/其他问题/套餐问题/咨询/综合问题)
# cs_record.problem_category 实际值可能不完全一致,此处做归并映射
_CATEGORY_BUCKETS = [
    ("投诉", ["投诉"]),
    ("退款", ["退款"]),
    ("套餐问题", ["套餐", "套餐问题", "降成"]),  # 降成=套餐降级/限速/VIP/赠送, 属套餐专项工作
    ("咨询", ["咨询", "网络问题"]),
    ("综合问题", ["综合", "综合问题", "其他"]),
    ("其他问题", []),  # 兜底:非上述任一的非空值 OR 空值
]

def _category_case():
    """返回 CASE WHEN 表达式把 problem_category 映射到 6 个周报复分类
    不匹配任何关键词的未知值 / NULL 统一归入最后一个兜底桶("其他问题")
    """
    whens = []
    for bucket, keywords in _CATEGORY_BUCKETS[:-1]:  # 除最后一项"其他问题"(兜底)
        if keywords:
            for kw in keywords:
                whens.append((CsRecord.problem_category.ilike(f"%{kw}%"), bucket))
    fallback = _CATEGORY_BUCKETS[-1][0]  # "其他问题"
    return case(*whens, else_=fallback).label("category_bucket")

def _operator_to_person_cte(department: str | None = None,
                            group_id: int | None = None,
                            specialty_id: int | None = None):
    """返回 operator → person_id, display_name, department, group_name, specialty, position, qw_user_id 的子查询 CTE

    通过 PersonAlias.alias (归一化匹配) → Person。找不到则 operator 作为 display_name。
    qw_user_id: 非空表示该别名所对应的 Person 已绑定企微 (有企微账号)
    department/group_id/specialty_id: 可选筛选条件
    group_name: 通过 PersonGroupMembership → Group 关联获取当前有效分组名称 (不依赖镜像字段)
    """
    person_conds = []
    if department:
        person_conds.append(Person.department == department)

    return (
        select(
            PersonAlias.alias.label("operator"),
            Person.id.label("person_id"),
            func.coalesce(Person.display_name, PersonAlias.alias).label("display_name"),
            Person.department.label("department"),
            func.coalesce(Group.name, Person.group_name).label("group_name"),
            # 2026-08-14: 优先读取 Person.specialty (任职镜像 = 用户直接编辑的最新值),
            # 兜底 Person.primary_specialty (SCD Type 2 镜像, 可能滞后)
            func.coalesce(Person.specialty, Person.primary_specialty).label("specialty"),
            Person.position.label("position"),
            Person.qw_user_id.label("qw_user_id"),
        )
        .select_from(PersonAlias)
        .outerjoin(Person, PersonAlias.person_id == Person.id)
        .outerjoin(
            PersonGroupMembership,
            and_(
                PersonGroupMembership.person_id == Person.id,
                PersonGroupMembership.is_current.is_(True),
            ),
        )
        .outerjoin(Group, PersonGroupMembership.group_id == Group.id)
        .where(and_(*person_conds) if person_conds else True)
        .cte(name="op_person")
    )

def reception_overview_trend(
    db: Session, start: datetime | None = None, end: datetime | None = None
) -> list[dict[str, Any]]:
    """模块1: 总体接待量 - 按周聚合

    输出: [{week_start, week_no, reception_total, day_count, daily_avg,
            staff_count, per_capita_weekly, per_capita_daily}]

    - week_start: 周一起始日(ISO)
    - week_no: 当年周号 (IW)
    - day_count: 本周有数据的天数 (用于日均计算)
    - staff_count: 本周实际有接待的 distinct 操作人数
    - per_capita_weekly: 人均接待量/周 = reception_total / staff_count
    - per_capita_daily: 人均接待量/天 = reception_total / staff_count / day_count
    """
    start, end = _date_range(start, end)
    week_trunc = func.date_trunc("week", CsRecord.operation_time).label("week_start")
    rows = db.execute(
        select(
            week_trunc,
            func.to_char(func.date_trunc("week", CsRecord.operation_time), "IW").label("week_no"),
            func.count().label("total"),
            func.count(func.distinct(func.to_char(CsRecord.operation_time, "YYYY-MM-DD"))).label("day_count"),
            func.count(func.distinct(CsRecord.operator)).filter(CsRecord.operator.isnot(None)).label("staff_count"),
        )
        .where(CsRecord.operation_time.between(start, end))
        .group_by("week_start")
        .order_by("week_start")
    ).all()
    result = []
    for r in rows:
        total = int(r.total or 0)
        day_count = int(r.day_count or 1) or 1
        staff_count = int(r.staff_count or 0) or 0
        result.append({
            "week_start": r.week_start.strftime("%Y-%m-%d") if r.week_start else None,
            "week_no": int(r.week_no or 0),
            "reception_total": total,
            "day_count": day_count,
            "daily_avg": round(total / day_count, 2) if day_count else 0,
            "staff_count": staff_count,
            "per_capita_weekly": round(total / staff_count, 2) if staff_count else 0,
            "per_capita_daily": round(total / staff_count / day_count, 2) if staff_count and day_count else 0,
        })
    return result

def reception_category_summary(
    db: Session, start: datetime | None = None, end: datetime | None = None
) -> list[dict[str, Any]]:
    """模块2: 接待分项汇总 - 按周 × 6 个分类 (投诉/退款/其他问题/套餐问题/咨询/综合问题)

    输出: [{week_start, week_no, 投诉, 退款, 其他问题, 套餐问题, 咨询, 综合问题}]
    """
    start, end = _date_range(start, end)
    week_trunc = func.date_trunc("week", CsRecord.operation_time).label("week_start")
    bucket = _category_case()
    rows = db.execute(
        select(
            week_trunc,
            func.to_char(func.date_trunc("week", CsRecord.operation_time), "IW").label("week_no"),
            bucket,
            func.count().label("cnt"),
        )
        .where(CsRecord.operation_time.between(start, end))
        .group_by("week_start", "category_bucket")
        .order_by("week_start", "category_bucket")
    ).all()
    # 按周归并
    per_week: dict[str, dict] = {}
    order = ["投诉", "退款", "其他问题", "套餐问题", "咨询", "综合问题"]
    for r in rows:
        ws = r.week_start.strftime("%Y-%m-%d") if r.week_start else ""
        if ws not in per_week:
            per_week[ws] = {
                "week_start": ws,
                "week_no": int(r.week_no or 0),
                **{k: 0 for k in order},
            }
        cat = r.category_bucket if r.category_bucket in order else "其他问题"
        per_week[ws][cat] += int(r.cnt or 0)
    return sorted(per_week.values(), key=lambda x: x["week_start"])

def reception_per_capita(
    db: Session, start: datetime | None = None, end: datetime | None = None
) -> list[dict[str, Any]]:
    """模块3: 接待分项人均工作 - 按周 × 按专项(投诉/退款/套餐/咨询)分组计算组别人数+人均

    每人按"本周接待量最大的分类"归入对应专项组。
    输出: [{week_start, week_no,
            complaint_total, complaint_staff, complaint_pc,
            refund_total, refund_staff, refund_pc,
            package_total, package_staff, package_pc,
            consult_total, consult_staff, consult_pc}]
    - *_total: 该专项本周总接待量
    - *_staff: 该专项本周人数(归入该专项组的操作人数)
    - *_pc: 该专项本周人均接待量 = total / staff (对齐周报表格数值)
    """
    start, end = _date_range(start, end)
    bucket = _category_case()

    # 步骤1: 按周 × operator × bucket 聚合每人每类接待数
    op_cat_rows = db.execute(
        select(
            func.date_trunc("week", CsRecord.operation_time).label("week_start"),
            func.to_char(func.date_trunc("week", CsRecord.operation_time), "IW").label("week_no"),
            CsRecord.operator.label("operator"),
            bucket,
            func.count().label("cnt"),
        )
        .where(CsRecord.operation_time.between(start, end), CsRecord.operator.isnot(None))
        .group_by("week_start", "week_no", "operator", "category_bucket")
    ).all()

    # 步骤2: 每人每周围攻分类(接待量最大的 bucket) → 归入对应专项组
    # 同时按周按 bucket 汇总 total_cnt 和 distinct staff
    SPECIALTY_ORDER = [
        ("投诉", "complaint"),
        ("退款", "refund"),
        ("套餐问题", "package"),
        ("咨询", "consult"),
        ("综合问题", "consult"),  # 综合问题并入咨询组
        ("其他问题", "consult"),  # 其他问题并入咨询组
    ]

    def bucket_to_specialty(b):
        for src, dst in SPECIALTY_ORDER:
            if b == src:
                return dst
        return "consult"

    # 临时存储: per_week[ws][specialty] → {"total": int, "staff": set()}
    per_week: dict[str, dict[str, dict]] = {}
    per_op_week: dict[tuple, dict[str, int]] = {}  # (week_start, operator) -> {bucket: cnt}
    for r in op_cat_rows:
        key = (str(r.week_start), r.operator)
        per_op_week.setdefault(key, {})[r.category_bucket] = int(r.cnt or 0)

    for (ws_str, operator), buckets in per_op_week.items():
        # 找该操作员本周接待量最大的分类
        dom_bucket, dom_cnt = max(buckets.items(), key=lambda x: x[1])
        specialty = bucket_to_specialty(dom_bucket)
        ws = ws_str
        per_week.setdefault(ws, {"complaint": {"total": 0, "staff": set()},
                                 "refund": {"total": 0, "staff": set()},
                                 "package": {"total": 0, "staff": set()},
                                 "consult": {"total": 0, "staff": set()}})
        # 该操作员接待的所有数据计入其主攻专项组
        for b, c in buckets.items():
            s = bucket_to_specialty(b)
            per_week[ws][s]["total"] += c
        # 操作员本身加入其主攻组 staff
        per_week[ws][specialty]["staff"].add(operator)

    # 构建结果
    result = []
    # 额外查询每周 week_no (用于输出)
    for ws, buckets in per_week.items():
        # 从 ws 反推 week_start 对象 -> week_no
        try:
            import datetime as _dt
            week_dt = _dt.datetime.strptime(ws.split(".")[0][:10], "%Y-%m-%d")
            week_no = int(week_dt.strftime("%V"))
        except Exception:
            week_no = 0
        row = {
            "week_start": ws.split(".")[0][:10],
            "week_no": week_no,
        }
        for sn, cn_name in [
            ("complaint", "投诉"),
            ("refund", "退款"),
            ("package", "套餐问题"),
            ("consult", "咨询"),
        ]:
            total = buckets[sn]["total"]
            staff = len(buckets[sn]["staff"])
            row[f"{sn}_total"] = total
            row[f"{sn}_staff"] = staff
            row[f"{sn}_pc"] = round(total / staff, 2) if staff else 0
        # 综合问题并入咨询,兼容列名(前端不使用此列,留作扩展)
        row["other_total"] = 0
        row["other_staff"] = 0
        row["other_pc"] = 0
        result.append(row)
    return sorted(result, key=lambda x: x["week_start"])

def reception_group_detail(
    db: Session, start: datetime | None = None, end: datetime | None = None
) -> dict[str, Any]:
    """模块4: 各组接待 - 按组名 × 人员的日接待明细 + 平均

    输出:
    {
      groups: [
        {
          group_name: "董梦玲组",
          people: [
            {
              display_name: "某某",
              category_type: "咨询",       # 该人员主攻接待类型(本周接待量最大的分类)
              daily: {"2026-07-06": 123, ...},
              daily_list: [123, ...],     # 按 start..end 日期顺序排序
              date_labels: ["07/06", ...],
              avg: 123.4,
              total: 863
            }, ...
          ],
          group_avg: 100.2,            # 组内人员平均接待量(每人的平均的平均)
          group_total: 12345           # 组内总计接待量
        }, ...
      ],
      ungrouped: [ ...same as people... ],   # 未分组人员
      date_labels: ["07/05", ...],           # 本周期日期标签(MM/DD)
      date_keys: ["2026-07-05", ...]         # 本周期完整日期
    }

    时间范围:默认取最近一周(7天),若 start/end 传入则按其范围。
    group_name 来源: Person.group_name(归并后)。operator 无法归并的人员放入 ungrouped。
    """
    start, end = _date_range(start, end)

    # 构建本周期完整日期列表(用于对齐 daily_list)
    def daterange(s, e):
        cur = s.date() if hasattr(s, "date") else s
        end_d = e.date() if hasattr(e, "date") else e
        while cur <= end_d:
            yield cur
            cur += timedelta(days=1)
    date_keys = [d.isoformat() for d in daterange(start, end)]
    date_labels = [d[5:] for d in date_keys]  # MM-DD

    op_person = _operator_to_person_cte()

    # 按 (operator, display_name, group_name, specialty, position, date, category_bucket) 聚合
    bucket = _category_case()
    rows = db.execute(
        select(
            CsRecord.operator.label("operator"),
            op_person.c.display_name.label("display_name"),
            op_person.c.group_name.label("group_name"),
            op_person.c.specialty.label("specialty"),
            func.to_char(CsRecord.operation_time, "YYYY-MM-DD").label("day"),
            bucket,
            func.count().label("cnt"),
        )
        .outerjoin(op_person, CsRecord.operator == op_person.c.operator)
        .where(CsRecord.operation_time.between(start, end), CsRecord.operator.isnot(None))
        .group_by(
            CsRecord.operator,
            op_person.c.display_name,
            op_person.c.group_name,
            op_person.c.specialty,
            "day",
            "category_bucket",
        )
    ).all()

    # 归并: per_person[display_name or operator]
    per_person: dict[str, dict] = {}
    person_info: dict[str, dict] = {}  # 保存 group/specialty 等
    for r in rows:
        key = r.display_name or r.operator
        per_person.setdefault(key, {})
        per_person[key][r.day] = per_person[key].get(r.day, 0) + int(r.cnt or 0)
        # 记录人员基本信息 + 每日分类信息(用于推断 category_type)
        info = person_info.setdefault(key, {
            "group_name": r.group_name,
            "specialty": r.specialty,
            "category_counts": {},
        })
        info["category_counts"][r.category_bucket] = (
            info["category_counts"].get(r.category_bucket, 0) + int(r.cnt or 0)
        )

    # 构建 people 对象列表
    groups_map: dict[str, list] = {}
    ungrouped = []
    for name, daily in per_person.items():
        total = sum(daily.values())
        day_count_nonzero = sum(1 for d in date_keys if daily.get(d, 0) > 0) or 1
        avg = round(total / day_count_nonzero, 2)
        daily_list = [daily.get(d, 0) for d in date_keys]
        info = person_info.get(name, {})
        # 推断 category_type: 从 category_counts 取最大的分类
        cc = info.get("category_counts", {})
        if cc:
            category_type = max(cc.items(), key=lambda x: x[1])[0]
        else:
            category_type = info.get("specialty") or "咨询"
        person_obj = {
            "name": name,
            "category_type": category_type,
            "total": total,
            "avg": avg,
            "daily": daily,
            "daily_list": daily_list,
        }
        gn = info.get("group_name")
        if gn:
            groups_map.setdefault(gn, []).append(person_obj)
        else:
            ungrouped.append(person_obj)

    # 组排序 + 组内按 total desc; 计算组均值/总计
    group_list = []
    for gn in sorted(groups_map.keys()):
        plist = sorted(groups_map[gn], key=lambda p: p["total"], reverse=True)
        g_total = sum(p["total"] for p in plist)
        g_avg = round(sum(p["avg"] for p in plist) / len(plist), 2) if plist else 0
        group_list.append({
            "group_name": gn,
            "people": plist,
            "group_total": g_total,
            "group_avg": g_avg,
        })

    ungrouped_sorted = sorted(ungrouped, key=lambda p: p["total"], reverse=True)

    return {
        "date_keys": date_keys,
        "date_labels": date_labels,
        "groups": group_list,
        "ungrouped": ungrouped_sorted,
    }
