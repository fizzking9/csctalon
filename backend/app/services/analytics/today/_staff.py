"""today 主题域子模块 staff (拆分自 today.py #226)

纯物理切片, 不改实现。原 today.py 头部 import 块原样保留,
函数体内惰性 import 随函数体原样保留。
"""
# ruff: noqa: F401, I001  # 拆分自原 today.py(#226), 保留原 import 顺序避免漂移
"""今日看板: today_kpi / 小时趋势 / 接待拆分 / 退款拆分 / 队列 / 员工效率。"""

import datetime as _dt
import logging
from decimal import Decimal
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, case, func, literal, or_, select, text, union
from sqlalchemy.orm import Session

from app.models.card_complaint import CardComplaint
from app.models.cs_record import CsRecord
from app.models.external_complaint import ExternalComplaint
from app.models.group import Group, PersonGroupMembership
from app.models.incident import Incident
from app.models.person import Person, PersonAlias
from app.models.refund import Refund
from app.models.refund_record_report import RefundRecordReport
from app.models.specialty import PersonSpecialtyAssignment, Specialty
from app.models.task import SpecialTask
from app.models.ticket import ServiceTicket
from app.models.todo import TodoItem
from app.models.wallet import WalletWithdrawal
from app.services.complaint_scope import (
    ALL_BUCKETS,
    BUCKET_CARD,
    BUCKET_EXTERNAL,
    BUCKET_INTERNAL,
    # ⚠️ #53: BUCKET_OTHER 已从来源桶移除(四类完备)。保留导入仅供历史数据识别,
    #    若确认无处使用可删除。
    BUCKET_OTHER,  # noqa: F401
    CARD_SCOPE,
)
from app.services.system_accounts import SourceKind  # noqa: I001
from app.services.system_accounts import is_virtual_name as _sa_is_virtual_name

logger = logging.getLogger(__name__)

# 跨子模块引用(拆分自 app.services.analytics, 原同文件内直接调用)
from app.services.analytics._core import (
    _EXTERNAL_DONE_RESULTS_LOCAL,
    _EXTERNAL_ITEM_COMPLAINT_KW,
    _INCLUDE_COMPLAINT_FILES,
    COMPLAINT_SPECIALTY,
    COMPLAINT_SPECIALTY_WEIGHT,
    D,
    TZ,
    _complaint_source_type_case,
    _complaint_subitem_case,
    _derive_service_item_for_refund,
    _parse_date,
    _pct_delta,
    _resolve_windows,
    _route_a_online_any_status_filter,
    _route_a_online_refund_filter,
    _route_b_withdraw_filter,
    _route_e_cashflow_filter,
    _strip_qw_prefix,
    _ticket_category_case,
)
from app.services.analytics.cash import (
    _cash_routes_total,
    _route_c_shanmao_trend,
    _route_d_other_trend,
)
from app.services.analytics.complaint import _ticket_effective_finish_time_where
from app.services.analytics.reception import (
    _category_case,
    _merge_person_rows,
    _operator_to_person_cte,
)


def today_staff_efficiency(
    db: Session,
    date: str | None = None,
    start: str | None = None,
    end: str | None = None,
    topn: int = 30,
    qw_only: bool = False,
    aggregate_by: str = "person",   # "person" 按人明细 / "specialty" 按专项 / "group" 按分组 / "all" 全员汇总
    department: str | None = None,  # 按部门筛选
    group_id: int | None = None,    # 按分组ID筛选
    specialty_id: int | None = None, # 按专项ID筛选
) -> dict[str, Any]:
    """模块7: 今日人均效率表 (按operator聚合, 关联Person拿display_name/group_name).

    两个独立合成指标 (用户 2026-08-02 新算法, 2026-08-02 v2 二次修订, 2026-08-09 v3 转接系数调整):
      a. 工作量分 (会话次数维度, 反映忙闲强度):
         = Σ (专项会话数) × 专项难度系数 × 本职系数(本职=1.0 / 非本职转接=固定0.2, 不再叠加专项难度)
      b. 效率分   (接待个数维度, SN去重, 反映服务覆盖面)
         —— 2026-08-02 修订: 跨表SN合并去重
         智能助手(cs_record)接待SN ∪ 服务工单(ServiceTicket)处理SN, 按当天汇总后去重
         同一SN在两个表都出现只算1个, 真实反映当天服务覆盖的独立设备/客户数.
         系统自动产生的记录排除: cs_record 中 sys/system/系统自动 等 operator,
         以及 description/execution_result/solution 中明确是自动触发的关键词.

    本职判定 (2026-08-19 v4 三级豁免):
      - ①岗位精确: 主管/组长/副主管 → 全豁免
      - ②岗位模式: 岗位含"专员"(投诉专员/应急专员/退款挽回专员/...) → 全豁免
      - ③专项兜底: 专项属投诉/应急/退款挽回类 → 全豁免 (即使岗位不含"专员")
      - 常规人员: HR预设专项与计算专项同一大类 → 本职全系数; 跨类 → 转接固定0.2
      - HR未登记新人 → 默认全部本职 (避免新人刚进来全部被打折)

    非本职 = 跨专项转接, 固定折算 TRANSFER_PENALTY (0.2), 不再 × 专项难度系数.
    系统/用户自助 虚拟账户 → 不进真人员工排名, 补在末尾 rank=NULL.

    新增 2026-08-09 筛选 & 聚合:
      qw_only = True  → 仅显示 Person 维表中 qw_user_id 非空 (已绑定企微账号) 的人员
      aggregate_by:
        "person"      → 原口径: 每人一行 (默认)
        "specialty"   → 按 hr_category (咨询接待/套餐专项/退款挽回/投诉专员) 聚合
        "group"       → 按 group_name (行政小组) 聚合
        "all"         → 所有真人一行总汇总 (显示"全员合计")
    """
    ts, te, _, _, is_realtime, disp_range = _resolve_windows(date=date, start=start, end=end)
    if disp_range:
        d_iso = disp_range[0][:10]
        d_end_iso = disp_range[1][:10]
    else:
        d_iso = (_dt.datetime.now(TZ).date() if date is None else _parse_date(date)).isoformat()
        d_end_iso = None
    op_person = _operator_to_person_cte(department=department, group_id=group_id, specialty_id=specialty_id)

    # ─────────────────────────────────────────────────────────────────────
    # 系统/自动记录排除规则 (同时用于 cs_record 与 退款/工单)
    #   1. operator 精确黑名单: sys/system/SYS/System 大小写变体 + 用户自助/客户自助
    #   2. 业务字段关键词: description/execution_result/solution 中包含"系统自动""自动发送"
    # ⚠️ #78: 黑名单已收敛到 app/services/system_accounts.py（唯一来源）。
    #    历史教训: 本文件内曾有 SYS_OP_BLACKLIST 与 SYSTEM_VIRTUAL_EXACT 两份且不一致,
    #    两份都漏 `WeChat`(ai-acc 的 AI 全自动接待账户) → 被判为真人并登上人效榜 rank=1。
    #    禁止再在此处定义系统账户集合。
    # ─────────────────────────────────────────────────────────────────────
    SYS_OP_BLACKLIST = ["sys", "system", "SYS", "System", "用户自助", "客户自助", "企微用户自助",
                        # #78 新增: AI 账户 + 客户(自助) —— 此前漏判导致 AI 计入真人工作量
                        "WeChat", "wechat", "WECHAT", "AI客服", "智能客服", "机器人客服", "客户"]
    def _sys_auto_filter():
        """返回 SQL 条件 排除 cs_record 中系统自动产生的记录"""
        kw = ("系统自动", "自动发送", "自动回复", "自动推送", "自动补发", "自动触发", "自动生成")
        kw_like = [CsRecord.description.ilike(f"%{k}%") for k in kw] \
                + [CsRecord.execution_result.ilike(f"%{k}%") for k in kw] \
                + [CsRecord.solution.ilike(f"%{k}%") for k in kw]
        return and_(
            CsRecord.operator.isnot(None),
            CsRecord.operator.notin_(SYS_OP_BLACKLIST),
            ~or_(*kw_like),  # 反向: 不包含任何自动关键词
        )

    # 1. 会话接待量 + 接待人数(去重SN) 总量 per operator
    #    v2: 排除系统自动记录
    cs_q = (select(CsRecord.operator.label("op"),
                   func.count().label("c"),
                   func.count(func.distinct(func.nullif(func.trim(CsRecord.device_sn), ""))).label("dc"))
            .where(CsRecord.operation_time.isnot(None),
                   CsRecord.operation_time >= ts, CsRecord.operation_time <= te,
                   _sys_auto_filter())
            .group_by(CsRecord.operator)).cte("cs_q")
    # 2. 会话接待 按 6 个 category_bucket 分桶 per operator: 会话数 + 接待数(SN去重) 两套
    #    v2: 同样排除系统自动记录
    cs_cat_q = (select(
        CsRecord.operator.label("op"),
        _category_case().label("bucket"),
        func.count().label("scnt"),
        func.count(func.distinct(func.nullif(func.trim(CsRecord.device_sn), ""))).label("pcnt"),
    ).where(
        CsRecord.operation_time.isnot(None),
        CsRecord.operation_time >= ts, CsRecord.operation_time <= te,
        _sys_auto_filter(),
    ).group_by(CsRecord.operator, "bucket")).cte("cs_cat_q")
    # 3. 退款挽回实际执行: 笔数 + 去重设备个数
    #    已有 sys/system 排除, 保留
    #    ⚠️ 2026-09-08: 工作量口径(无金额)保持全状态(any_status), 不随经营口径收紧到已退款,
    #    否则会低估"申请中/驳回"等已投入处理动作的工作量。
    rf_q = (select(Refund.operator.label("op"),
                   func.count().label("sc"),
                   func.count(func.distinct(func.nullif(func.trim(Refund.device_sn), ""))).label("pc"))
            .where(Refund.created_at_raw.isnot(None),
                   Refund.created_at_raw >= ts, Refund.created_at_raw <= te,
                   Refund.operator.isnot(None),
                   Refund.operator.notin_(["sys", "system", "SYS", "System"]),
                   *_route_a_online_any_status_filter())
            .group_by(Refund.operator)).cte("rf_q")
    # 4. 投诉类工单 处理人完工: 笔数 + 去重设备个数
    cm_q = (select(ServiceTicket.handler.label("op"),
                   func.count().label("sc"),
                   func.count(func.distinct(func.nullif(func.trim(ServiceTicket.device_sn), ""))).label("pc"))
            .where(ServiceTicket.finish_time.isnot(None),
                   ServiceTicket.finish_time >= ts, ServiceTicket.finish_time <= te,
                   ServiceTicket.handler.isnot(None),
                   ServiceTicket.handler.notin_(SYS_OP_BLACKLIST),
                   _ticket_category_case() == "投诉")
            .group_by(ServiceTicket.handler)).cte("cm_q")

    # ─────────────────────────────────────────────────────────────────────
    # v2 新增: 5. 服务工单 全类处理人完工(不只是投诉) — 用于 SN 合并去重
    #   当天任何完工的服务工单, 其 device_sn 都纳入该处理人"当天工作覆盖SN"
    #   因为: 咨询/套餐/退款/设备故障类工单也是实际工作, 不能只靠 cs_record
    # ─────────────────────────────────────────────────────────────────────
    tk_all_q = (select(ServiceTicket.handler.label("op"),
                       func.count().label("sc"),
                       func.count(func.distinct(func.nullif(func.trim(ServiceTicket.device_sn), ""))).label("pc"))
                .where(ServiceTicket.finish_time.isnot(None),
                       ServiceTicket.finish_time >= ts, ServiceTicket.finish_time <= te,
                       ServiceTicket.handler.isnot(None),
                       ServiceTicket.handler.notin_(SYS_OP_BLACKLIST))
                .group_by(ServiceTicket.handler)).cte("tk_all_q")

    # 并集: 做过接待 或 退款 或 投诉处理 或 任何工单完工 的人员
    ops_union = union(
        select(cs_q.c.op), select(rf_q.c.op), select(cm_q.c.op), select(tk_all_q.c.op)
    ).cte("ops_u")

    rows = db.execute(
        select(
            ops_union.c.op,
            func.coalesce(op_person.c.display_name, ops_union.c.op).label("name"),
            op_person.c.person_id.label("person_id"),
            op_person.c.department.label("dept"),
            op_person.c.group_name.label("grp"),
            op_person.c.position.label("pos"),
            op_person.c.specialty.label("sp"),
            op_person.c.qw_user_id.label("qw_user_id"),
            func.coalesce(cs_q.c.c, 0).label("rc"),
            func.coalesce(cs_q.c.dc, 0).label("rdc"),
        )
        .select_from(ops_union)
        .outerjoin(cs_q, ops_union.c.op == cs_q.c.op)
        .outerjoin(op_person, ops_union.c.op == op_person.c.operator)
    ).all()
    # 转成可变对象 (SQLAlchemy Row 不可变, 后续兜底匹配需要改字段)
    from types import SimpleNamespace as _SN
    rows = [_SN(**dict(r._mapping)) for r in rows]

    # 2026-08-11 兜底匹配: PersonAlias 精确匹配失败的 operator, 尝试去前缀变体匹配
    # 场景: refunds.operator='刘思敏', 但 PersonAlias 登记 '生产-刘思敏'/'全球WiFi-刘思敏' (带部门前缀)
    # 方案: 把所有 PersonAlias.alias 取出, 对未匹配 operator 做 "alias 去前缀后 == operator" 的 Python 匹配
    # 注意: 触发条件用 person_id is None (而非 dept is None), 否则会把"精确匹配成功但 Person.department 为空"
    #       的 operator (如 '徐依婷'→pid 365) 误当作未匹配, 被兜底覆盖成另一个带前缀的 Person ('财务-徐依婷')
    unmatched_ops = [r.op for r in rows if r.person_id is None and r.op]
    if unmatched_ops:
        # 一次性查全部 PersonAlias + Person 信息, 避免 N+1
        _fb_rows = db.execute(
            select(
                PersonAlias.alias, PersonAlias.person_id,
                Person.id.label("pid"), Person.display_name, Person.department,
                func.coalesce(Person.primary_specialty, Person.specialty).label("specialty"),
                Person.position, Person.qw_user_id,
                func.coalesce(Group.name, Person.group_name).label("group_name"),
            )
            .select_from(PersonAlias)
            .outerjoin(Person, PersonAlias.person_id == Person.id)
            .outerjoin(PersonGroupMembership, and_(PersonGroupMembership.person_id == Person.id, PersonGroupMembership.is_current.is_(True)))
            .outerjoin(Group, PersonGroupMembership.group_id == Group.id)
        ).all()
        # 建 "去前缀后缀 → person 信息" 索引 (取第一个匹配, 多个匹配时优先企微部门)
        suffix_map: dict[str, dict] = {}
        for a in _fb_rows:
            for sep in ('-', '_'):
                if sep in (a.alias or ''):
                    suffix = (a.alias or '').rsplit(sep, 1)[-1]
                    # 优先保留有部门信息的记录
                    if suffix not in suffix_map or (a.department and not suffix_map[suffix].get('dept')):
                        suffix_map[suffix] = {
                            'pid': a.pid, 'name': a.display_name or a.alias,
                            'dept': a.department, 'grp': a.group_name,
                            'pos': a.position, 'sp': a.specialty,
                            'qw_user_id': a.qw_user_id,
                        }
        # 对未匹配 operator 应用兜底
        _fb_applied = 0
        for r in rows:
            if r.person_id is None and r.op in suffix_map:
                info = suffix_map[r.op]
                _fb_applied += 1
                r.person_id = info['pid']
                r.name = info['name']
                r.dept = info['dept']
                r.grp = info['grp']
                r.pos = info['pos']
                r.sp = info['sp']
                r.qw_user_id = info['qw_user_id']
        if _fb_applied:
            import logging as _logging
            _logging.getLogger(__name__).info(
                f"today_staff_efficiency: 前缀兜底匹配成功 {_fb_applied}/{len(unmatched_ops)} 个未登记 operator"
            )

    # 2026-08-11 部门前缀推断: Person.department 为空时, 从 PersonAlias.alias / operator 前缀推断部门
    # 场景: '生产-刘思敏'/'财务-徐依婷'/'测试-谢莹' 等 Person 维表 department 暂空, 但 alias 前缀已携带部门信息
    # 生产/财务/测试 是独立部门 (非企微/店铺/研发), 通过 alias 前缀识别并补全
    _DEPT_PREFIX_MAP = {"企微": "企微", "店铺": "店铺", "研发": "研发",
                        "生产": "生产", "财务": "财务", "测试": "测试"}
    def _infer_dept(name: str | None) -> str | None:
        if not name: return None
        for pfx, dept in _DEPT_PREFIX_MAP.items():
            if name.startswith(pfx):
                rest = name[len(pfx):]
                if rest and (rest[0] in "-·—_ \t" or '\u4e00' <= rest[0] <= '\u9fa5'):
                    return dept
        return None
    # 建 person_id → 部门 映射 (从带部门前缀的 PersonAlias 推断, 一次性查全表避免 N+1)
    _pid_dept: dict[int, str] = {}
    for dr in db.execute(
        select(PersonAlias.person_id, PersonAlias.alias).where(PersonAlias.person_id.isnot(None))
    ).all():
        d = _infer_dept(dr.alias)
        if d:
            _pid_dept[dr.person_id] = d
    # 对 department 为空的人员, 优先用 person_id 映射, 其次用 operator 自身前缀推断
    _dept_filled = 0
    for r in rows:
        if (not r.dept or r.dept == "未设置") and r.person_id:
            inferred = _pid_dept.get(r.person_id) or _infer_dept(r.op)
            if inferred:
                r.dept = inferred
                _dept_filled += 1
    if _dept_filled:
        import logging as _logging
        _logging.getLogger(__name__).info(
            f"today_staff_efficiency: 部门前缀推断填充 {_dept_filled} 人 (生产/财务/测试 等独立部门)"
        )

    # 会话/接待两套 6 桶 按 op 聚合
    cs_cat_by_op: dict[str, dict[str, tuple[int, int]]] = {}
    for r in db.execute(select(cs_cat_q)).all():
        cs_cat_by_op.setdefault(r.op, {})[r.bucket] = (int(r.scnt or 0), int(r.pcnt or 0))
    rf_by_op: dict[str, tuple[int, int]] = {r.op: (int(r.sc or 0), int(r.pc or 0)) for r in db.execute(select(rf_q)).all()}
    {r.op: (int(r.sc or 0), int(r.pc or 0)) for r in db.execute(select(cm_q)).all()}
    {r.op: (int(r.sc or 0), int(r.pc or 0)) for r in db.execute(select(tk_all_q)).all()}

    # ─────────────────────────────────────────────────────────────────────
    # v3 2026-08-11: 6个专项明确区分 - 退款挽回/退款处理/内部投诉/外部投诉
    #   退款挽回(recovery): cs_record 中 problem_category='退款' 的接待工作
    #   退款处理(processing): refunds 表的实际退款操作 + service_tickets 退款类工单完工
    #   内部投诉(internal): service_tickets 中内部投诉类
    #   外部投诉(external): service_tickets + external_complaints + card_complaints 外部投诉类
    # ─────────────────────────────────────────────────────────────────────
    merged_sn_sql = text(
        """
        -- 子查询1: cs_record 按 6 类专项 × SN 输出 (过滤系统自动)
        WITH cs_sns AS (
            SELECT
                c.operator AS op,
                CASE
                    WHEN COALESCE(c.problem_category,'') ILIKE '%投诉%' THEN '内部投诉'
                    WHEN COALESCE(c.problem_category,'') ILIKE '%退款%' THEN '退款挽回'
                    WHEN COALESCE(c.problem_category,'') ILIKE '%套餐%'
                      OR COALESCE(c.problem_category,'') ILIKE '%降成%' THEN '套餐专项'
                    WHEN COALESCE(c.problem_category,'') ILIKE '%咨询%'
                      OR COALESCE(c.problem_category,'') ILIKE '%网络问题%'
                      OR COALESCE(c.problem_category,'') ILIKE '%综合%'
                      OR COALESCE(c.problem_category,'') ILIKE '%其他%'
                      OR COALESCE(c.problem_category,'') = '' THEN '咨询接待'
                    ELSE '咨询接待'
                END AS spec,
                c.device_sn AS sn
            FROM cs_records c
            WHERE c.operation_time IS NOT NULL
              AND c.operation_time >= :ts AND c.operation_time <= :te
              AND c.operator IS NOT NULL
              AND c.operator NOT IN ('sys','system','SYS','System','用户自助','客户自助','企微用户自助','WeChat','wechat','WECHAT','AI客服','智能客服','机器人客服','客户','班牛','系统','微信投诉')
              AND NOT (
                  COALESCE(c.description,'') ILIKE ANY(ARRAY['%系统自动%','%自动发送%','%自动回复%','%自动推送%','%自动补发%','%自动触发%','%自动生成%'])
                  OR COALESCE(c.execution_result,'') ILIKE ANY(ARRAY['%系统自动%','%自动发送%','%自动回复%','%自动推送%','%自动补发%','%自动触发%','%自动生成%'])
                  OR COALESCE(c.solution,'') ILIKE ANY(ARRAY['%系统自动%','%自动发送%','%自动回复%','%自动推送%','%自动补发%','%自动触发%','%自动生成%'])
              )
        ),
        -- 子查询2: refund SN → "退款处理" 专项 (实际退款操作)
        rf_sns AS (
            SELECT r.operator AS op, '退款处理' AS spec,
                   r.device_sn AS sn
            FROM refunds r
            WHERE r.created_at_raw IS NOT NULL
              AND r.created_at_raw >= :ts AND r.created_at_raw <= :te
              AND r.operator IS NOT NULL
              AND r.operator NOT IN ('sys','system','SYS','System')
              AND r.refund_method = '线上'
        ),
        -- 子查询3: 外部投诉工单 SN → "外部投诉" 专项
        cm_ext_sns AS (
            SELECT st.handler AS op, '外部投诉' AS spec,
                   st.device_sn AS sn
            FROM service_tickets st
            WHERE st.finish_time IS NOT NULL
              AND st.finish_time >= :ts AND st.finish_time <= :te
              AND st.handler IS NOT NULL
              AND st.handler NOT IN ('sys','system','SYS','System','用户自助','客户自助','企微用户自助','WeChat','wechat','WECHAT','AI客服','智能客服','机器人客服','客户','班牛','系统','微信投诉')
              AND (
                  -- 外部投诉判定: service_item 含外部投诉关键词
                  COALESCE(st.service_item,'') ILIKE ANY(ARRAY['%12315%','%工信部%','%消协%','%公共服务平台%','%消费者协会%','%监督局%','%市场监管%','%市监%','%315平台%','%12345%','%政务平台%'])
              )
        ),
        -- 子查询4: 内部投诉工单 SN → "内部投诉" 专项 (非外部的投诉)
        cm_int_sns AS (
            SELECT st.handler AS op, '内部投诉' AS spec,
                   st.device_sn AS sn
            FROM service_tickets st
            WHERE st.finish_time IS NOT NULL
              AND st.finish_time >= :ts AND st.finish_time <= :te
              AND st.handler IS NOT NULL
              AND st.handler NOT IN ('sys','system','SYS','System','用户自助','客户自助','企微用户自助','WeChat','wechat','WECHAT','AI客服','智能客服','机器人客服','客户','班牛','系统','微信投诉')
              AND (st.service_category IS NULL OR st.service_category = '投诉')
              AND NOT (
                  COALESCE(st.service_item,'') ILIKE ANY(ARRAY['%12315%','%工信部%','%消协%','%公共服务平台%','%消费者协会%','%监督局%','%市场监管%','%市监%','%315平台%','%12345%','%政务平台%'])
              )
        ),
        -- 子查询5: 非投诉工单 SN → 按 service_category 分桶 (退款→退款处理, 套餐疑问/套餐→套餐, 其他→咨询)
        tk_sns AS (
            SELECT st.handler AS op,
                   CASE
                       WHEN COALESCE(st.service_category,'') = '退款' THEN '退款处理'
                       WHEN COALESCE(st.service_category,'') IN ('套餐疑问','套餐') THEN '套餐专项'
                       WHEN COALESCE(st.service_category,'') IN ('换货','设备故障','网络故障') THEN '咨询接待'
                       ELSE '咨询接待'
                   END AS spec,
                   st.device_sn AS sn
            FROM service_tickets st
            WHERE st.finish_time IS NOT NULL
              AND st.finish_time >= :ts AND st.finish_time <= :te
              AND st.handler IS NOT NULL
              AND st.handler NOT IN ('sys','system','SYS','System','用户自助','客户自助','企微用户自助','WeChat','wechat','WECHAT','AI客服','智能客服','机器人客服','客户','班牛','系统','微信投诉')
              AND NOT (st.service_category IS NULL OR st.service_category = '投诉')
        ),
        -- 子查询6: handler为空/SYS的工单 → SN关联cs_record找处理人(取最近一次操作人)
        -- 解决BMS系统工单处理人字段大量为空的问题(覆盖率从12%提升到75%)
        -- 性能优化(2026-08-11): 原 CROSS JOIN LATERAL ... LIMIT 1 会被执行 N 次(N=空handler工单数, 实测8120次),
        --   每次扫 cs_records 时间窗口内全部行, 累计 1 亿次行扫描, 单段耗时 39.4s
        --   改用 DISTINCT ON + 普通 JOIN, PostgreSQL 选用 Merge Join 一次性完成, 实测降至 0.34s (115倍提升)
        tk_sns_linked AS (
            SELECT DISTINCT ON (st.ticket_no)
                   c2.operator AS op,
                   CASE
                       WHEN COALESCE(st.service_item,'') ILIKE ANY(ARRAY['%12315%','%工信部%','%消协%','%公共服务平台%','%消费者协会%','%监督局%','%市场监管%','%市监%','%315平台%','%12345%','%政务平台%']) THEN '外部投诉'
                       WHEN (st.service_category IS NULL OR st.service_category = '投诉') THEN '内部投诉'
                       WHEN COALESCE(st.service_category,'') = '退款' THEN '退款处理'
                       WHEN COALESCE(st.service_category,'') IN ('套餐疑问','套餐') THEN '套餐专项'
                       WHEN COALESCE(st.service_category,'') IN ('换货','设备故障','网络故障') THEN '咨询接待'
                       ELSE '咨询接待'
                   END AS spec,
                   st.device_sn AS sn
            FROM service_tickets st
            JOIN cs_records c2
              ON c2.device_sn = st.device_sn
             AND c2.operator IS NOT NULL
             AND c2.operator NOT IN ('sys','system','SYS','System','用户自助','客户自助','企微用户自助','WeChat','wechat','WECHAT','AI客服','智能客服','机器人客服','客户','班牛','系统','微信投诉')
             AND c2.operation_time IS NOT NULL
             AND c2.operation_time >= st.register_time - INTERVAL '2 days'
             AND c2.operation_time <= st.finish_time + INTERVAL '1 day'
            WHERE st.finish_time IS NOT NULL
              AND st.finish_time >= :ts AND st.finish_time <= :te
              AND (st.handler IS NULL OR st.handler = '' OR st.handler = 'SYS')
              AND st.device_sn IS NOT NULL
            -- DISTINCT ON (ticket_no) + ORDER BY ticket_no, operation_time DESC
            -- = 每个工单取最近一次 cs_record 操作人, 等价于 LATERAL ... LIMIT 1
            ORDER BY st.ticket_no, c2.operation_time DESC
        ),
        -- 合并 6 个子查询, 按 op × spec × DISTINCT SN 去重后 聚合: 该人员该专项的真实独立设备数
        merged AS (
            SELECT op, spec, COUNT(DISTINCT sn) AS sn_cnt
            FROM (
                SELECT op, spec, sn FROM cs_sns WHERE sn IS NOT NULL
                UNION ALL SELECT op, spec, sn FROM rf_sns WHERE sn IS NOT NULL
                UNION ALL SELECT op, spec, sn FROM cm_ext_sns WHERE sn IS NOT NULL
                UNION ALL SELECT op, spec, sn FROM cm_int_sns WHERE sn IS NOT NULL
                UNION ALL SELECT op, spec, sn FROM tk_sns WHERE sn IS NOT NULL
                UNION ALL SELECT op, spec, sn FROM tk_sns_linked WHERE sn IS NOT NULL
            ) all_sn
            GROUP BY op, spec
        )
        SELECT op, spec, sn_cnt FROM merged;
        """
    )
    merged_sn_rows = db.execute(merged_sn_sql, {"ts": ts, "te": te}).fetchall()
    # 构造成: merged_pp[op][spec] = 跨表去重后的 SN 接待个数
    merged_pp: dict[str, dict[str, int]] = {}
    for r in merged_sn_rows:
        merged_pp.setdefault(r.op, {})[r.spec or "咨询"] = int(r.sn_cnt or 0)

    # ─────────────────────────────────────────────────────────────────────
    # 继续计算各专项的会话次数 (按原逻辑, 非SN维度, 不需要去重合并)
    # 但需要把 工单的会话次数(=工单数) 也并入对应专项的会话总数:
    #   - 投诉专项会话 = cs_record[投诉]条数 + 投诉工单数
    #   - 退款专项会话 = cs_record[退款]条数 + 退款条数
    #   - 套餐专项会话 = cs_record[套餐]条数 + 套餐疑问工单数
    #   - 咨询专项会话 = cs_record[咨询/其他/综合]条数 + 其他类型工单数
    # 先查"套餐疑问工单数"和"其他类型工单数"按 handler 分桶
    # ─────────────────────────────────────────────────────────────────────
    tk_spec_count_sql = text(
        """
        -- 1. handler有值的工单 → 直接用handler归人
        SELECT st.handler AS op,
               CASE
                   -- 外部投诉
                   WHEN COALESCE(st.service_item,'') ILIKE ANY(ARRAY['%12315%','%工信部%','%消协%','%公共服务平台%','%消费者协会%','%监督局%','%市场监管%','%市监%','%315平台%','%12345%','%政务平台%']) THEN '外部投诉'
                   -- 内部投诉 (service_category为空或投诉，且不是外部投诉)
                   WHEN (st.service_category IS NULL OR st.service_category = '投诉') THEN '内部投诉'
                   -- 退款类工单 → 退款处理
                   WHEN COALESCE(st.service_category,'') = '退款' THEN '退款处理'
                   -- 套餐类
                   WHEN COALESCE(st.service_category,'') IN ('套餐疑问','套餐') THEN '套餐专项'
                   -- 其他 → 咨询接待
                   ELSE '咨询接待'
               END AS spec,
               COUNT(*) AS cnt
        FROM service_tickets st
        WHERE st.finish_time IS NOT NULL
          AND st.finish_time >= :ts AND st.finish_time <= :te
          AND st.handler IS NOT NULL
          AND st.handler NOT IN ('sys','system','SYS','System','用户自助','客户自助','企微用户自助','WeChat','wechat','WECHAT','AI客服','智能客服','机器人客服','客户','班牛','系统','微信投诉')
        GROUP BY 1, 2

        UNION ALL

        -- 2. handler为空/SYS的工单 → SN关联cs_record找处理人(取最近一次操作人)
        -- 性能优化(2026-08-11): 同 merged_sn_sql.tk_sns_linked, 改用 DISTINCT ON 替代 LATERAL JOIN
        --   原 LATERAL 单段耗时 39.4s, 改后降至 0.34s
        SELECT linked.op AS op,
               CASE
                   WHEN COALESCE(linked.service_item,'') ILIKE ANY(ARRAY['%12315%','%工信部%','%消协%','%公共服务平台%','%消费者协会%','%监督局%','%市场监管%','%市监%','%315平台%','%12345%','%政务平台%']) THEN '外部投诉'
                   WHEN (linked.service_category IS NULL OR linked.service_category = '投诉') THEN '内部投诉'
                   WHEN COALESCE(linked.service_category,'') = '退款' THEN '退款处理'
                   WHEN COALESCE(linked.service_category,'') IN ('套餐疑问','套餐') THEN '套餐专项'
                   ELSE '咨询接待'
               END AS spec,
               COUNT(*) AS cnt
        FROM (
            -- 子查询: 每个空-handler 工单取最近一次 cs_record operator (等价 LATERAL LIMIT 1)
            SELECT DISTINCT ON (st.ticket_no)
                   c2.operator AS op,
                   st.service_item AS service_item,
                   st.service_category AS service_category
            FROM service_tickets st
            JOIN cs_records c2
              ON c2.device_sn = st.device_sn
             AND c2.operator IS NOT NULL
             AND c2.operator NOT IN ('sys','system','SYS','System','用户自助','客户自助','企微用户自助','WeChat','wechat','WECHAT','AI客服','智能客服','机器人客服','客户','班牛','系统','微信投诉')
             AND c2.operation_time IS NOT NULL
             AND c2.operation_time >= st.register_time - INTERVAL '2 days'
             AND c2.operation_time <= st.finish_time + INTERVAL '1 day'
            WHERE st.finish_time IS NOT NULL
              AND st.finish_time >= :ts AND st.finish_time <= :te
              AND (st.handler IS NULL OR st.handler = '' OR st.handler = 'SYS')
              AND st.device_sn IS NOT NULL
            ORDER BY st.ticket_no, c2.operation_time DESC
        ) linked
        GROUP BY 1, 2
        """
    )
    tk_spec_rows = db.execute(tk_spec_count_sql, {"ts": ts, "te": te}).fetchall()
    tk_spec_by_op: dict[str, dict[str, int]] = {}
    for r in tk_spec_rows:
        tk_spec_by_op.setdefault(r.op or "", {})[r.spec or "咨询"] = int(r.cnt or 0)

    # 2026-08-11 明确区分: 6个独立专项, 每个专项独立展示列
    #   咨询接待×1.0 | 套餐专项×1.8 | 退款处理×2.0 | 退款挽回×2.2 | 内部投诉×3.0 | 外部投诉×5.0
    WEIGHT = {
        "咨询接待": 1.0,
        "套餐专项": 1.8,
        "退款处理": 2.0,
        "退款挽回": 2.2,
        "内部投诉": 3.0,
        "外部投诉": 5.0,
        # ★ 2026-09-14 合并后的投诉专项（系数取原外部投诉 5.0，用户拍板）
        # ⚠️ 旧两条**必须保留** —— 它们仍用于**历史区间**归因，
        #    删掉会使旧任职落到 None → 历史绩效归零/错算。
        COMPLAINT_SPECIALTY: COMPLAINT_SPECIALTY_WEIGHT,
    }
    # 管理岗/无主项人员使用的默认系数 (每个专项独立默认系数)
    CAT_DEFAULT_WEIGHT = {
        "咨询接待": 1.0,
        "套餐专项": 1.8,
        "退款处理": 2.0,
        "退款挽回": 2.2,
        "内部投诉": 3.0,
        "外部投诉": 5.0,
        COMPLAINT_SPECIALTY: COMPLAINT_SPECIALTY_WEIGHT,
    }
    # 6个独立专项桶 (不再合并大类, 每个专项独立一列展示)
    CAT_BUCKETS = [
        ("zixun", "咨询接待", {"咨询接待"}),
        ("taocan", "套餐专项", {"套餐专项"}),
        ("recovery", "退款挽回", {"退款挽回"}),
        ("processing", "退款处理", {"退款处理"}),
        ("internal", "内部投诉", {"内部投诉"}),
        ("external", "外部投诉", {"外部投诉"}),
        # ★ 合并后的投诉专项（独立一列，不肢解内/外两列）
        ("complaint", COMPLAINT_SPECIALTY, {COMPLAINT_SPECIALTY}),
    ]
    CATS = [c[1] for c in CAT_BUCKETS]  # ["咨询接待", "套餐专项", "退款挽回", "退款处理", "内部投诉", "外部投诉"]
    TRANSFER_PENALTY = 0.2
    # 2026-08-09 用户新要求: 转接接待工作量/效率折算 = 固定 TRANSFER_PENALTY (0.2)
    #   不再叠加 专项难度系数 WEIGHT[c], 即:
    #   原 咨询 1.0×0.2=0.2 / 套餐 1.8×0.2=0.36 / 退款 2.2×0.2=0.44 / 投诉 3.0×0.2=0.6
    #   新 全部 → 0.2 (理由: 转接接待本身负荷相似且不高, 不按原专项难度打折上折)
    # 计算口径:
    #   本职 → coef = WEIGHT[hr_cat] (本人主专项系数)
    #   转接 → coef = TRANSFER_PENALTY (固定, 不再乘以 WEIGHT[c])
    #   管理岗 → coef = CAT_DEFAULT_WEIGHT[cat] (大类默认系数, 跨类豁免全系数)
    #   专项岗(投诉/应急等) → coef = CAT_DEFAULT_WEIGHT[cat] (同管理岗, 跨类全豁免)
    # 2026-08-19 扩展: 投诉专员/应急专员 等专项岗 获得同管理岗跨类全豁免待遇
    # 豁免判定三级制:
    #   ① 岗位精确匹配: 主管/组长/副主管 → 全豁免
    #   ② 岗位模式匹配: 位置含"专员" → 全豁免 (投诉专员/应急专员/退款挽回专员/...)
    #   ③ 专项精确+模式匹配: 专项为投诉/应急/退款挽回类 → 全豁免 (即使岗位不含"专员")
    EXEMPT_POSITIONS = {"主管", "组长", "副主管"}
    EXEMPT_POSITION_PATTERNS = ("专员",)  # 岗位名称含"专员" → 跨类全豁免
    EXEMPT_CATEGORIES = {"内部投诉", "外部投诉", "退款挽回", COMPLAINT_SPECIALTY}  # 专项精确名称
    EXEMPT_CATEGORY_PATTERNS = ("投诉", "应急", "退款挽回")  # 专项名称含这些关键词 → 跨类全豁免
    # ⚠️ #78 (2026-09-13): 原 SYSTEM_VIRTUAL_EXACT / VIRTUAL_HINT_WORDS /
    #    _EMPLOYEE_ID_PATTERN / _PERSON_NAME_PATTERN 已**全部迁移**到
    #    `app/services/system_accounts.py`（唯一来源）。
    #    原因：本文件内曾并存两份不一致的系统账户清单，两份都漏 `WeChat`
    #    → AI 账户被判为真人并登上人效榜 rank=1（工单 #78 根因）。
    #    请勿在此处重新定义；新增账户请改 system_accounts.py。
    import re as _re_mod  # noqa: F401  (保留供下方既有正则使用)
    _EMPLOYEE_ID_PATTERN = _re_mod.compile(r'^[A-Za-z][A-Za-z0-9_-]{1,11}$')
    _PERSON_NAME_PATTERN = _re_mod.compile(r'^[\u4e00-\u9fa5]{2,4}$')  # 中文人名 2-4 字
    def _is_virtual_name(name: str, dept: str | None, grp: str | None, pos: str | None,
                        person_id: int | None = None) -> bool:
        """非真人虚拟账户判定。

        ★ #78 (2026-09-13): 实现已收敛到 `app/services/system_accounts.is_virtual_name`
          （唯一来源）。本函数保留为**薄的转发**，仅为兼容既有调用点。

        ⚠️ 修复的缺陷：旧实现只判 `VIRTUAL_HINT_WORDS` 后就去匹配工号正则
          `^[A-Za-z][A-Za-z0-9_-]{1,11}$`，导致 `WeChat`（AI 全自动接待账户）
          被判成「员工工号」→ 返回 False（真人）→ 登上生产人效榜 **rank=1**。
          新实现在工号判定**之前**加了词干否决（wechat/sys/ai/bot...）。

        判定顺序（与 system_accounts 保持一致）：
          ① 精确名单命中 → True
          ② 已匹配 Person（person_id 非空）→ False（真人）
          ③ 词干否决（wechat/sys/ai/bot）→ True   ← ★ 本次修复点
          ④ 关键字命中（wifi/管家/科技/公司/管理员 等）→ True
          ⑤ 工号格式 → False（真人）；中文人名 2-4 字 → False（真人）
          ⑥ 其余：长名（≥5 字）→ True，短名保守判 False
        """
        return _sa_is_virtual_name(name, dept=dept, grp=grp, pos=pos, person_id=person_id)

    def _hr_map(hr_sp: str | None, pos: str | None) -> str | None:
        """人员主专项归一化映射 (2026-08-19 修订)
        返回: 专项名称 | "管理层" | None

        新增: 专项岗(专员)显式识别, 与管理岗同等待遇 → 跨类全豁免
        """
        # ① 管理岗（主管/组长/副主管）→ 跨类全豁免
        if (pos or "") in ("主管", "组长", "副主管"):
            return "管理层"
        # ② 专项岗(专员) 显式识别: 投诉专员/应急专员/退款挽回专员 等
        #    即使 hr_sp 为空, 也按岗位名称识别专项类别
        p = (pos or "").strip()
        if "专员" in p:
            if "投诉" in p:
                return "外部投诉" if "外部" in p else "内部投诉"
            if "挽回" in p:
                return "退款挽回"
            if "应急" in p:
                return "内部投诉"  # 应急专员默认归为内部投诉类(跨类全豁免)
            # 其他专员 → 默认按内部投诉(高难度系数)处理
            return "内部投诉"
        if not hr_sp:
            return None
        # 精确匹配6个标准专项名称
        s = (hr_sp or "").strip()
        if s in WEIGHT:
            return s
        # 兼容旧数据的模糊匹配
        if "外部投诉" in s: return "外部投诉"
        if "内部投诉" in s or "投诉" in s: return "内部投诉"
        if "退款挽回" in s: return "退款挽回"
        if "退款处理" in s or "退款" in s: return "退款处理"
        if "套餐" in s: return "套餐专项"
        if any(k in s for k in ("咨询", "接待", "综合问题", "其他问题")):
            return "咨询接待"
        return None

    BUCK_FZ = ("咨询", "其他问题", "综合问题")

    # ═══════════════════════════════════════════════════════════════════
    # 2026-08-14: 按天匹配专项 - 构建 person_id → {specialty_name: days_count}
    #   解决人员周期内变更专项时，旧工作被错误按新专项系数计算的问题。
    #   假设工作在各天均匀分布，用天数加权平均系数（单天查询完全精确）。
    # ═══════════════════════════════════════════════════════════════════
    from datetime import date as _date_cls
    from datetime import timedelta as _td
    query_start = ts.date() if hasattr(ts, 'date') else _date_cls.fromisoformat(str(ts)[:10])
    query_end = te.date() if hasattr(te, 'date') else _date_cls.fromisoformat(str(te)[:10])
    date_list: list[_date_cls] = []
    d = query_start
    while d <= query_end:
        date_list.append(d)
        d += _td(days=1)

    work_person_ids = {r.person_id for r in rows if r.person_id}
    person_specialty_days: dict[int, dict[str, int]] = {}
    if work_person_ids:
        _sa_rows = db.execute(
            select(
                PersonSpecialtyAssignment.person_id,
                PersonSpecialtyAssignment.effective_from,
                PersonSpecialtyAssignment.effective_to,
                Specialty.name.label("specialty_name"),
            )
            .select_from(PersonSpecialtyAssignment)
            .join(Specialty, PersonSpecialtyAssignment.specialty_id == Specialty.id)
            .where(
                PersonSpecialtyAssignment.person_id.in_(work_person_ids),
                PersonSpecialtyAssignment.is_primary.is_(True),
                or_(
                    PersonSpecialtyAssignment.effective_to.is_(None),
                    PersonSpecialtyAssignment.effective_to >= query_start,
                ),
                PersonSpecialtyAssignment.effective_from <= query_end,
            )
        ).all()
        # 构建 person_id → {date: specialty_name}
        person_daily: dict[int, dict[_date_cls, str]] = {}
        for sa in _sa_rows:
            pid = sa.person_id
            sp_name = sa.specialty_name
            eff_from = sa.effective_from
            eff_to = sa.effective_to or query_end
            if pid not in person_daily:
                person_daily[pid] = {}
            for _d in date_list:
                if eff_from <= _d <= eff_to:
                    person_daily[pid][_d] = sp_name
        # 汇总为 person_id → {specialty_name: days_count}
        for pid, daily_map in person_daily.items():
            counts: dict[str, int] = {}
            for sp in daily_map.values():
                counts[sp] = counts.get(sp, 0) + 1
            person_specialty_days[pid] = counts

    out: list[dict[str, Any]] = []; virtual_rows: list[dict[str, Any]] = []
    for r in rows:
        op = r.op
        rc = int(r.rc or 0)
        rdc = int(getattr(r, "rdc", 0) or 0)
        buckets = cs_cat_by_op.get(op, {})
        # 会话次数 (sessions):  cs_record条数 + 对应专项工单完工条数
        def _sess_sum(names: tuple[str, ...]) -> int:
            return sum((buckets.get(n, (0,0))[0] for n in names), 0)
        # cs_record 侧的会话数
        z_sess_cs = _sess_sum(BUCK_FZ)
        t_sess_cs = buckets.get("套餐问题", (0,0))[0]
        # cs_record 中 problem_category='退款' → 退款挽回接待会话
        recovery_sess_cs = buckets.get("退款", (0,0))[0]
        # cs_record 中投诉 → 内部投诉会话 (外部投诉主要在工单侧区分)
        internal_sess_cs = buckets.get("投诉", (0,0))[0]
        external_sess_cs = 0  # cs_record中暂不区分外部投诉

        # 加上工单按专项分类的条数
        tk_sess = tk_spec_by_op.get(op or "", {})
        # 咨询接待: cs_record 咨询/其他/综合 + 工单 咨询接待
        z_sess = z_sess_cs + tk_sess.get("咨询接待", 0)
        # 套餐专项: cs_record 套餐问题 + 工单 套餐专项
        t_sess = t_sess_cs + tk_sess.get("套餐专项", 0)
        # 退款挽回: cs_record 退款接待 (工单侧的退款都是退款处理)
        recovery_sess = recovery_sess_cs
        # 退款处理: refunds表操作数 + 工单 退款处理
        processing_sess = rf_by_op.get(op, (0,0))[0] + tk_sess.get("退款处理", 0)
        # 内部投诉: cs_record 投诉 + 工单 内部投诉
        internal_sess = internal_sess_cs + tk_sess.get("内部投诉", 0)
        # ★ 2026-09-14 合并后的投诉专项桶
        #    工单侧仍按旧桶（内部/外部投诉）统计；新专项的人其**归集**
        #    仍落在 internal/external（因工单来源未变）→
        #    本桶用于**兼容新专项直接产出的工作**（当前为 0，预留）。
        complaint_sess = 0
        # 外部投诉: 工单 外部投诉
        external_sess = external_sess_cs + tk_sess.get("外部投诉", 0)

        rf_op_count = rf_by_op.get(op, (0,0))[0]

        # 接待个数 (people, SN去重跨表合并): 使用 SQL 侧预先算出的 merged_pp
        sn_pp = merged_pp.get(op or "", {})
        z_peop = sn_pp.get("咨询接待", 0)
        t_peop = sn_pp.get("套餐专项", 0)
        recovery_peop = sn_pp.get("退款挽回", 0)
        processing_peop = sn_pp.get("退款处理", 0)
        internal_peop = sn_pp.get("内部投诉", 0)
        complaint_peop = 0  # ★ 见上 complaint_sess 说明
        external_peop = sn_pp.get("外部投诉", 0)

        # 专项桶 (key → sessions, people)
        # ⚠️ 桶的 key 必须与上方 `CAT_BUCKETS` **逐项对应** ——
        #    `cat_data[bucket_key]` 是无守卫的直接索引，缺一个就 KeyError。
        #    曾因只给 CAT_BUCKETS 加了 `complaint` 而漏加此处 →
        #    生产人效接口报 `KeyError: 'complaint'`（缓存预热日志中的
        #    “预热 today:staff 失败: 'complaint'”）。
        cat_data = {
            "zixun": (z_sess, z_peop),
            "taocan": (t_sess, t_peop),
            "recovery": (recovery_sess, recovery_peop),
            "processing": (processing_sess, processing_peop),
            "internal": (internal_sess, internal_peop),
            "external": (external_sess, external_peop),
            # ★ 2026-09-14 投诉专项合并：新专项独立成桶
            #   旧 internal/external 桶**保留**（历史区间的工单仍按旧桶归集）
            "complaint": (complaint_sess, complaint_peop),
        }
        # 总接待设备数 = 全部桶 SN 之和 (近似用桶求和)
        # ⚠️ 新桶（complaint）当前恒为 0，故不改变原口径数值。
        total_devices = (
            z_peop + t_peop + recovery_peop + processing_peop
            + internal_peop + external_peop + complaint_peop
        )
        # 若 total_devices 比 cs_record 侧 rdc 大, 用 total_devices 更真实; 否则保留 rdc 保持旧口径
        reception_device_count = max(rdc, total_devices)
        # 总会话数 = 工单并入后的全部桶之和 (反映当天真实工作量条次)
        #   ⚠️ 新桶当前为 0，不改变原数值
        reception_count = (
            z_sess + t_sess + recovery_sess + processing_sess
            + internal_sess + external_sess + complaint_sess
        )
        # rc 是原 cs_record 侧算出来的会话总条数; 并入工单数后可能更大, 取 max 避免回退
        reception_count = max(rc, reception_count)

        position = r.pos or ""
        # 跨类全豁免判定 (三级制):
        #   ① 岗位精确匹配: 主管/组长/副主管
        #   ② 岗位模式匹配: 位置含"专员" (投诉专员/应急专员/退款挽回专员/...)
        #   ③ 专项精确+模式匹配: 专项为投诉/应急/退款挽回类
        mgmt = (
            position in EXEMPT_POSITIONS
            or any(p in position for p in EXEMPT_POSITION_PATTERNS)
        )
        # 当前主专项（用于展示和兜底判定）
        hr_cat = _hr_map(r.sp, position)
        # 专项岗豁免: 即使岗位不含"专员", 只要专项属于投诉/应急/退款挽回类 → 跨类全系数
        if not mgmt and hr_cat:
            if hr_cat in EXEMPT_CATEGORIES or any(p in hr_cat for p in EXEMPT_CATEGORY_PATTERNS):
                mgmt = True

        # 2026-08-14: 按天加权平均系数 - 替代单一 hr_cat
        pid = r.person_id
        sp_days = person_specialty_days.get(pid, {}) if pid else {}
        total_days = sum(sp_days.values()) if sp_days else 0
        # 2026-08-14 增强: SCD 无数据时, 自动用 Person.specialty 作为默认全天专项
        # （用户刚编辑过任职记录但 SCD 还未同步的常见场景）
        if total_days == 0 and hr_cat is not None and pid:
            sp_days = {hr_cat: len(date_list)}
            total_days = len(date_list)
        # 2026-08-19 增强: SCD 数据与 Person.specialty 不符时的兜底策略
        # 场景: SCD 有旧数据但 Person.specialty 已更新(如覃克泳:Person.specialty=退款挽回但 SCD 只有咨询接待)
        # 策略:
        #   a) SCD 与 Person.specialty 一致 → 完全信任 SCD (保持原行为)
        #   b) SCD 完全不含 Person.specialty → 覆盖:sp_days 全部按 hr_cat 计
        #   c) SCD 部分匹配 → SCD 保持, 额外追加 hr_cat 一天作为加权
        if total_days > 0 and hr_cat is not None:
            sp_cat_from_scd = {_hr_map(sp_name, position) for sp_name in sp_days.keys() if _hr_map(sp_name, position)}
            if hr_cat not in sp_cat_from_scd and total_days <= len(date_list):
                # 场景(b): SCD 完全不含 Person.specialty → 整个 sp_days 覆盖为 hr_cat
                # 用 hr_cat 覆盖所有 SCD 天 (SCD 是旧的, Person.specialty 是当前真实的)
                sp_days = {hr_cat: total_days}
                total_days = total_days

        work_score = 0.0; eff_score = 0.0
        bd: dict[str, dict[str, Any]] = {}
        for (bucket_key, cat_name, sp_set) in CAT_BUCKETS:
            sv, pv = cat_data[bucket_key]
            if sv == 0 and pv == 0:
                bd[cat_name] = {"sessions": 0, "people": 0, "weight": 0, "coef": 0, "is_own": False, "work_contrib": 0, "eff_contrib": 0}
                continue
            # 判断本职/转接
            if mgmt:
                # 管理岗: 跨类豁免, 使用大类默认系数
                own = True
                coef = CAT_DEFAULT_WEIGHT[cat_name]
            elif total_days > 0:
                # 按天加权平均系数
                weighted_coef = 0.0
                own_days = 0
                for sp_name, days in sp_days.items():
                    sp_cat = _hr_map(sp_name, position)
                    if sp_cat and sp_cat in sp_set:
                        weighted_coef += WEIGHT.get(sp_cat, 1.0) * days
                        own_days += days
                    else:
                        weighted_coef += TRANSFER_PENALTY * days
                coef = weighted_coef / total_days if total_days > 0 else TRANSFER_PENALTY
                own = own_days > total_days / 2
            elif hr_cat is not None and hr_cat in sp_set:
                # 回退: 无按天数据时使用单一 hr_cat
                own = True
                coef = WEIGHT[hr_cat]
            else:
                # 转接 → 固定 0.2
                own = False
                coef = TRANSFER_PENALTY
            wp = sv * coef; ep = pv * coef
            work_score += wp; eff_score += ep
            bd[cat_name] = {
                "sessions": sv, "people": pv,
                "weight": coef, "coef": round(coef, 3), "is_own": own,
                "work_contrib": round(wp, 1), "eff_contrib": round(ep, 1),
            }
        raw_name = r.name or op or "未知"
        norm_name = _strip_qw_prefix(raw_name) or raw_name
        _qw = getattr(r, "qw_user_id", None)
        # 2026-09-04 F-P1-1: 输出 person_id(可为 None)。此前 record 不含 person_id,
        # 导致同一人多行无法在后端按人归并、前端 person audit 也拿不到 id。
        record: dict[str, Any] = {
            "operator": op, "name": norm_name,
            "person_id": pid,
            "department": r.dept or "未设置", "group": r.grp or "未分组",
            "position": r.pos, "current_specialty": r.sp,
            "hr_category": hr_cat, "is_management": mgmt,
            "qw_user_id": _qw,
            # ★ #78 来源分类: 供前端把 AI/系统账户单列(不与真人混排)并排除出人效排名。
            #   判据见 app/services/system_accounts.classify_cs_record。
            #   ⚠️ source_kind 在下方虚拟判定后统一回填(此处先占位 human)
            "source_kind": SourceKind.HUMAN.value,
            "reception_count": reception_count,
            "reception_device_count": reception_device_count,
            "refund_op_count": rf_op_count,
            # 6个专项: 会话次数(次)
            "sp_zixun": z_sess, "sp_taocan": t_sess,
            "sp_recovery": recovery_sess, "sp_processing": processing_sess,
            "sp_internal": internal_sess, "sp_external": external_sess,
            # 兼容字段别名 (前端使用)
            "sp_wanhui": recovery_sess, "sp_tuikuan": processing_sess,
            "sp_neibu": internal_sess, "sp_waibu": external_sess,
            # 6个专项: 接待个数(个, SN去重)
            "pp_zixun": z_peop, "pp_taocan": t_peop,
            "pp_recovery": recovery_peop, "pp_processing": processing_peop,
            "pp_internal": internal_peop, "pp_external": external_peop,
            # 兼容字段别名 (前端使用)
            "pp_wanhui": recovery_peop, "pp_tuikuan": processing_peop,
            "pp_neibu": internal_peop, "pp_waibu": external_peop,
            "weights": dict(WEIGHT), "transfer_penalty": TRANSFER_PENALTY,
            "breakdown": bd,
            "work_score": round(work_score, 2), "eff_score": round(eff_score, 2),
            "rank": None,              # rank 先写默认 None, 排序后真人员工再写 1..N
            "is_virtual": False,       # 显式 False, 避免 undefined 误判成 null 非真人 Tag 全亮
            # v3 口径说明供前端 tooltip 渲染
            "_calc_note": {
                "sessions_merge": "会话次数(次)=cs_record条数 + 对应专项工单完工条数; 退款挽回=退款类接待; 退款处理=实际退款操作+退款工单",
                "sn_merge_rule": "接待个数(个)=智能助手SN ∪ 工单SN 跨表去重, 按人员×专项独立核算; 排除sys/system/系统自动产生的记录",
                "weight_rule": "本职: 主专项系数(外诉×5.0/内诉×3.0/退款挽回×2.2/退款处理×2.0/套餐×1.8/咨询×1.0); 转接: 固定×0.2; 管理岗/专员岗: 各专项默认系数(跨类全豁免,三级判定:①岗位=主管/组长/副主管 ②岗位含\"专员\" ③专项属投诉/应急/退款挽回类)",
                "recovery_def": "退款挽回: cs_record接待problem_category='退款'的客户(沟通挽留工作)",
                "processing_def": "退款处理: refunds表实际退款操作 + service_tickets退款类工单完工(执行退款)",
            },
        }
        # 非真人判定 = 精确虚拟匹配 OR 未登记+名字特征匹配
        if _is_virtual_name(norm_name, r.dept, r.grp, r.pos, r.person_id):
            record["is_virtual"] = True
            # ★ #78 回填来源分类: AI 全自动账户 vs 其他系统账户。
            #   前端据此把 AI 行**单列**(用户要求「区分出来，单列也可以」)，
            #   而不是混在真人排名里。
            _op_low = (op or "").strip().lower()
            record["source_kind"] = (
                SourceKind.AI_AUTO.value
                if _op_low in ("wechat", "ai客服", "智能客服", "机器人客服")
                else SourceKind.AI_SYSTEM.value
            )
            # AI/系统账户不进真人排名(rank 保持 None)，但保留其量级供「大致评估工作量」
            virtual_rows.append(record)
        else:
            out.append(record)

    # ═══════════════════════════════════════════════════════════════════
    # 2026-08-09 新增: 企微筛选 + 聚合视图
    # ═══════════════════════════════════════════════════════════════════
    def _filter_qw_only(recs: list[dict]) -> list[dict]:
        """qw_only: 仅保留 Person 维表已绑定企微 qw_user_id 的人员"""
        return [r for r in recs if (r.get("qw_user_id") or "").strip() != ""]

    def _aggregate_records(recs: list[dict], mode: str) -> list[dict]:
        """按 mode 聚合真人员工记录.
        mode: person (按 person_id/归一名 归并同一人多行) / specialty (按hr_category) / group (按组) / all (全员一行)
        """
        if mode == "person":
            # F-P1-1: 同一人跨表写法不一致 → 按 person_id(兜底 name)归并, 不再原样返回。
            # 逻辑在模块级 `_merge_person_rows`(可单测), 6 桶名单与 CATS 一致。
            return _merge_person_rows(recs)
        if not recs:
            return recs
        SUM_KEYS = [
            "reception_count", "reception_device_count", "refund_op_count",
            "sp_zixun", "sp_taocan", "sp_recovery", "sp_processing", "sp_internal", "sp_external",
            "sp_wanhui", "sp_tuikuan", "sp_neibu", "sp_waibu",  # 兼容别名
            "pp_zixun", "pp_taocan", "pp_recovery", "pp_processing", "pp_internal", "pp_external",
            "pp_wanhui", "pp_tuikuan", "pp_neibu", "pp_waibu",  # 兼容别名
            "work_score", "eff_score",
        ]
        buckets: dict[str, dict] = {}
        for r in recs:
            if mode == "specialty":
                key = r.get("hr_category") or "未登记专项"
                disp_name = key
            elif mode == "group":
                key = r.get("group") or "未分组"
                disp_name = key
            elif mode == "all":
                key = "__ALL__"
                disp_name = "全员合计"
            else:  # fallback
                key = r.get("name") or ""
                disp_name = key
            if key not in buckets:
                buckets[key] = {
                    "aggregate_key": key,
                    "operator": None,
                    "name": disp_name,
                    "department": _merge_mode_label(r.get("department"), mode),
                    "group": disp_name if mode == "group" else "—",
                    "position": _merge_mode_label(r.get("position"), mode),
                    "current_specialty": disp_name if mode == "specialty" else "—",
                    "hr_category": disp_name if mode == "specialty" else "—",
                    "person_count": 0,
                    "is_management": False,
                    "qw_user_id": None,
                    "weights": dict(WEIGHT),
                    "transfer_penalty": TRANSFER_PENALTY,
                    "breakdown": {c: {
                        "sessions": 0, "people": 0,
                        "weight": WEIGHT.get(c, 1.0),
                        "coef": WEIGHT.get(c, 1.0),
                        "is_own": True,
                        "work_contrib": 0.0, "eff_contrib": 0.0,
                    } for c in CATS},
                    "is_virtual": False,
                    "_calc_note": {
                        "aggregate_mode": mode,
                        "sessions_merge": "聚合视图: 各人员 Σ 工作量分 / Σ 效率分",
                    },
                    **{k: 0 for k in SUM_KEYS},
                }
            b = buckets[key]
            b["person_count"] += 1
            for k in SUM_KEYS:
                b[k] = b[k] + (r.get(k) or 0)
            for c in CATS:
                rb = (r.get("breakdown") or {}).get(c) or {}
                bb = b["breakdown"][c]
                for k in ("sessions", "people", "work_contrib", "eff_contrib"):
                    bb[k] = bb[k] + (rb.get(k) or 0)
        # 后处理: round, rank 后续再打
        out_list = list(buckets.values())
        for b in out_list:
            b["work_score"] = round(float(b["work_score"] or 0), 2)
            b["eff_score"] = round(float(b["eff_score"] or 0), 2)
            for c in CATS:
                bb = b["breakdown"][c]
                bb["work_contrib"] = round(float(bb["work_contrib"]), 1)
                bb["eff_contrib"] = round(float(bb["eff_contrib"]), 1)
        return out_list

    def _merge_mode_label(val: str | None, mode: str) -> str:
        if mode == "all":
            return "—"
        return val or "—"

    # 先做企微筛选 (真人员工 + 虚拟 都筛一遍; 虚拟账号一般 qw_user_id 为空就被筛掉)
    if qw_only:
        out = _filter_qw_only(out)
        virtual_rows = _filter_qw_only(virtual_rows)

    # ─────────────────────────────────────────────────────────────────────
    # ★ #78 (2026-09-13) AI / 系统账户**单列**
    #   背景: AI 账户此前被 `_sys_auto_filter()`（operator notin 黑名单）在 SQL 层
    #         直接排除，导致 AI 的工作量在生产上**完全不可见**。
    #   用户要求: 「区分出来，单列也可以」+「确认有他们在参与工作 + 大致评估工作量」。
    #   做法: 补一段独立聚合，把非真人账户的量级单独算出来，附在结果末尾
    #         （`is_virtual=true`, `rank=null`），前端渲染为单独的小表。
    #   取舍: ① 不进真人排名（AI 不是人，排名无意义，且会挤掉真人名次）；
    #         ② 不做「AI 转人工」细分（平台无稳定标记列，做了会返工 —— 见工单 #78）；
    #         ③ 空则不返回该行（无 AI 数据时不占版面）。
    #   工单: #78 / 架构: docs/arch/A-20260913-001-AI接待口径分层.md
    # ─────────────────────────────────────────────────────────────────────
    _AI_SYS_OPS = ["WeChat", "wechat", "WECHAT", "AI客服", "智能客服", "机器人客服",
                   "sys", "system", "SYS", "System", "系统", "班牛", "用户自助", "客户自助"]
    _virtual_agg = db.execute(
        select(
            CsRecord.operator.label("op"),
            func.count().label("rc"),
            func.count(func.distinct(func.nullif(func.trim(CsRecord.device_sn), ""))).label("rdc"),
        )
        .where(CsRecord.operation_time.isnot(None),
               CsRecord.operation_time >= ts, CsRecord.operation_time <= te,
               CsRecord.operator.in_(_AI_SYS_OPS))
        .group_by(CsRecord.operator)
    ).all()
    # ─────────────────────────────────────────────────────────────────────
    # ★ 2026-09-14 用户要求: 「AI 接待也纳入效率值核算，算法与专项人员一致」
    #
    # 之前只给了 AI 的**量级**（会话数/接待数），`work_score=eff_score=0`
    # → 无法与真人横向比较，也就答不了「AI 顶几个客服」。
    #
    # 本段用**与真人完全相同的权重表**（CAT_BUCKETS / WEIGHT /
    # CAT_DEFAULT_WEIGHT / TRANSFER_PENALTY）为 AI 行算效率值，
    # 但 AI 行**不参与真人排名**（`rank=None`）—— AI 不是人。
    #
    # ⚠️ AI 的专项构成怎么定？
    #   AI 账户没有 Person / 无专项任职 → 无法按「本职系数」算。
    #   故采用**接待实际构成**逐桶计算（= 每个桶按该桶默认系数），
    #   这与「管理岗跨类全豁免」的算法**同构**（`coef = CAT_DEFAULT_WEIGHT[cat]`）。
    #   于是 AI 的效率值 = Σ(该桶会话×该桶系数)，语义是
    #   「AI 承担了相当于多少标准人力工作量」。
    #
    # ⚠️ 为什么必须复用同一张权重表: 若给 AI 单独写一套系数，
    #    AI 与真人的效率值就**不可比** —— 那本次需求（比对 AI 效率）就失去意义。
    #    单测 `test_ai_uses_same_weight_table` 锁死这一点。
    # ─────────────────────────────────────────────────────────────────────
    def _ai_cat_counts(op_name: str) -> dict[str, tuple[int, int]]:
        """AI 账户按专项桶返回 (sessions, people) —— 与真人同口径。

        ⚠️ 为什么不复用 `cs_cat_by_op`:
           该字典来自 `cs_cat_q` CTE，其 WHERE 带 `_sys_auto_filter()`
           （**排除** WeChat/AI客服 等系统账户 —— 正当做法：真人统计不该混入 AI）
           → 用 AI 的 operator 去查恒为空（AI 效率值恒 0，需求失效）。

        ⚠️ 为什么不改 `cs_cat_q` 把 AI 放回来:
           它是**真人**人效的公共查询，放开会让 AI 混进真人榜（违反 #78）。
           故此处**另查一次**，但**判据与真人逐项一致**：
             · 分类判据 = 同一个 `_category_case()`（problem_category → 桶）
             · 去重判据 = 同 `count(distinct device_sn)`
             · 时间/非空条件同
           唯一差别 = **不过滤系统账户**（这正是要保留 AI 的原因）。
        """
        _ai_rows = db.execute(
            select(
                _category_case().label("bucket"),
                func.count().label("scnt"),
                func.count(
                    func.distinct(func.nullif(func.trim(CsRecord.device_sn), ""))
                ).label("pcnt"),
            )
            .where(
                CsRecord.operation_time.isnot(None),
                CsRecord.operation_time >= ts,
                CsRecord.operation_time <= te,
                CsRecord.operator == op_name,
            )
            .group_by("bucket")
        ).all()
        b: dict[str, tuple[int, int]] = {
            r.bucket: (int(r.scnt or 0), int(r.pcnt or 0)) for r in _ai_rows
        }
        # ⚠️ 桶名必须用 `_category_case()` 的**输出值**（咨询/退款/套餐问题/…），
        #    不是最终展示的专项名（咨询接待/退款挽回）。
        #    实测踩坑：AI 的 problem_category 全是「网络问题」→ 被
        #    `_CATEGORY_BUCKETS` 归入 **"咨询"** 桶；若这里写 `b.get("咨询接待")`
        #    就取不到值 → people=0 → `eff_score` 恒 0（不报错，静默归零）。
        #    真人侧同理由 `BUCK_FZ` 承担（见上方定义）。
        return {
            "zixun": (
                sum((b.get(n, (0, 0))[0] for n in BUCK_FZ), 0),
                sum((b.get(n, (0, 0))[1] for n in BUCK_FZ), 0),
            ),
            "taocan": (b.get("套餐问题", (0, 0))[0], b.get("套餐问题", (0, 0))[1]),
            "recovery": (b.get("退款", (0, 0))[0], b.get("退款", (0, 0))[1]),
            "processing": (0, 0),   # AI 不执行退款操作/工单完工
            "internal": (b.get("投诉", (0, 0))[0], b.get("投诉", (0, 0))[1]),
            # AI 无外部投诉渠道（那些走人工），恒 0
            "external": (0, 0),
            "complaint": (0, 0),
        }

    def _ai_scores(op_name: str) -> tuple[float, float, dict]:
        """AI 行效率值（与真人同一权重表；跨类豁免语义）。"""
        counts = _ai_cat_counts(op_name)
        w = e = 0.0
        bd: dict[str, dict[str, Any]] = {}
        for (bucket_key, cat_name, _sp_set) in CAT_BUCKETS:
            sv, pv = counts.get(bucket_key, (0, 0))
            if sv == 0 and pv == 0:
                bd[cat_name] = {
                    "sessions": 0, "people": 0, "weight": 0, "coef": 0,
                    "is_own": True, "work_contrib": 0, "eff_contrib": 0,
                }
                continue
            # ⚠️ 与真人「管理岗」分支同一条路径 —— 有意如此，保证可比
            coef = CAT_DEFAULT_WEIGHT[cat_name]
            wp = sv * coef
            ep = pv * coef
            w += wp
            e += ep
            bd[cat_name] = {
                "sessions": sv, "people": pv,
                "weight": coef, "coef": round(coef, 3), "is_own": True,
                "work_contrib": round(wp, 1), "eff_contrib": round(ep, 1),
            }
        return w, e, bd

    for vr in _virtual_agg:
        _vname = (vr.op or "").strip()
        _vlow = _vname.lower()
        _v_ws, _v_es, _v_bd = _ai_scores(_vname)
        virtual_rows.append({
            "operator": _vname, "name": _vname,
            "person_id": None,
            "department": "AI/系统", "group": "非真人",
            "position": None, "current_specialty": None, "hr_category": None,
            "is_management": False, "qw_user_id": None,
            # AI 全自动（真 AI 客服）vs 其他系统账户（自动流水）
            "source_kind": (SourceKind.AI_AUTO.value
                            if _vlow in ("wechat", "ai客服", "智能客服", "机器人客服")
                            else SourceKind.AI_SYSTEM.value),
            "is_virtual": True,
            "reception_count": int(vr.rc or 0),
            "reception_device_count": int(vr.rdc or 0),
            "refund_op_count": 0,
            # ★ 从 breakdown 取各桶明细（与真人同字段名，前端可同表渲染）
            "sp_zixun": _v_bd["咨询接待"]["sessions"],
            "sp_taocan": _v_bd["套餐专项"]["sessions"],
            "sp_recovery": _v_bd["退款挽回"]["sessions"],
            "sp_processing": _v_bd["退款处理"]["sessions"],
            "sp_internal": _v_bd["内部投诉"]["sessions"],
            "sp_external": _v_bd["外部投诉"]["sessions"],
            "pp_zixun": _v_bd["咨询接待"]["people"],
            "pp_taocan": _v_bd["套餐专项"]["people"],
            "pp_recovery": _v_bd["退款挽回"]["people"],
            "pp_processing": _v_bd["退款处理"]["people"],
            "pp_internal": _v_bd["内部投诉"]["people"],
            "pp_external": _v_bd["外部投诉"]["people"],
            "weights": dict(WEIGHT), "transfer_penalty": TRANSFER_PENALTY,
            "breakdown": _v_bd,
            # ★ 效率值（2026-09-14 用户要求：与专项人员算法一致，便于比对）
            "work_score": round(_v_ws, 2),
            "eff_score": round(_v_es, 2),
            "rank": None,
            "_calc_note": {
                "note": "非真人账户（AI/系统）。效率值采用与专项人员**完全相同的权重表**"
                        "（按各专项默认系数逐桶累加），故可与真人横向比对；"
                        "但不参与真人排名（工单 #78 / 2026-09-14 用户要求）",
                "weight_rule": "AI 无专项任职 → 按接待实际构成逐桶取该桶默认系数，"
                               "与「管理岗跨类全豁免」同构",
            },
        })

    # 真人员工 按 mode 聚合 (virtual_rows 不聚合, 原样保留在末尾)
    aggregate_mode = (aggregate_by or "person").lower()
    if aggregate_mode not in ("person", "specialty", "group", "all"):
        aggregate_mode = "person"
    out = _aggregate_records(out, aggregate_mode)

    out.sort(key=lambda x: -x["work_score"])
    out = out[: max(1, min(200, topn or 30))]
    for i, rec in enumerate(out, 1):
        rec["rank"] = i
    # 聚合模式（按专项/分组/全员）不追加虚拟账号行，只显示聚合后的真实数据汇总
    if aggregate_mode == "person":
        for rec in virtual_rows:
            rec["rank"] = None
            out.append(rec)
    return {
        "date": d_iso,
        "date_end": d_end_iso,
        "qw_only": qw_only,
        "aggregate_by": aggregate_mode,
        "data": out,
    }

