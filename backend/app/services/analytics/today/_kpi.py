"""today 主题域子模块 kpi (拆分自 today.py #226)

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

from app.models.ods_clean import CardComplaintClean
from app.models.ods_clean import CsRecordClean
from app.models.ods_clean import DeviceComboClean
from app.models.ods_clean import ExternalComplaintClean
from app.models.group import Group, PersonGroupMembership
from app.models.incident import Incident
from app.models.person import Person, PersonAlias
from app.models.ods_clean import RefundClean
from app.models.ods_clean import RefundRecordReportClean
from app.models.specialty import PersonSpecialtyAssignment, Specialty
from app.models.task import SpecialTask
from app.models.ticket import ServiceTicket
from app.models.todo import TodoItem
from app.models.ods_clean import WalletWithdrawalClean
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
    _cs_operator_cond,
    _cs_time_cond,
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


def today_kpi(db: Session, date: str | None = None, start: str | None = None, end: str | None = None) -> dict[str, Any]:
    """模块1: KPI卡 + 前一周期环比 【全域统一退款核算 v4】

    支持三种时间调用方式(按优先级):
      1. start+end 明确范围 (周/月/季度粒度, 多日汇总) → 上一个同长度周期做环比
      2. date='YYYY-MM-DD' 指定整天 → 昨天整天做环比
      3. date=None 实时今天 → 昨天同时段做环比
    口径严格对齐 v4 规范 (2026-08-10 用户调整):
      - 退款笔数/金额 = 四路经营合计 A+B+C+D (经营侧登记的所有退款出款)
      - 真实现金流 = E (refund_record_reports 退款成功合计, 对账基准, 2026-09-05 起替代 merchant_refunds)
      - 对账偏差 = 经营估算 − 实际现金流
    """
    ts, te, ys, ye, is_realtime, disp_range = _resolve_windows(date=date, start=start, end=end)
    d_end_iso = None
    if disp_range:
        d_iso = disp_range[0][:10]
        d_end_iso = disp_range[1][:10]
    else:
        d_iso = (_dt.datetime.now(TZ).date() if date is None else _parse_date(date)).isoformat()

    def _between(col, s, e):
        return and_(col.isnot(None), col >= s, col <= e)

    # 1. 今日接待量 (cs_records.operation_time)
    #    #326: 时间窗谓词归一到 _cs_time_cond(与原 _between 展开逐字等价)
    cs_today = db.execute(select(func.count()).where(_cs_time_cond(ts, te))).scalar() or 0
    cs_yoy = db.execute(select(func.count()).where(_cs_time_cond(ys, ye))).scalar() or 0
    # 1b. 今日服务设备数 (cs_records.device_sn 去重, 排除 NULL/空串) — 补充口径：1台设备多次进线算1次，体现覆盖面
    cs_device_today = db.execute(select(
        func.count(func.distinct(func.nullif(func.trim(CsRecordClean.device_sn), "")))
    ).where(_cs_time_cond(ts, te))).scalar() or 0
    cs_device_yoy = db.execute(select(
        func.count(func.distinct(func.nullif(func.trim(CsRecordClean.device_sn), "")))
    ).where(_cs_time_cond(ys, ye))).scalar() or 0

    # 1c. ★ #78 接待来源分层（真人 / AI全自动 / 系统 / 客户自助）
    #     设计说明（Design Rationale）:
    #       目的: 让运营主管看到「AI 接管了多少、真人还剩多少工作量」。
    #       逻辑: AI 客服仍在迭代期, 只需量级 + 涉及 SN, 不做「AI 转人工」细分。
    #       口径: 判据 = operator + description 文本模式(平台无 AI 标志列),
    #             详见 app/services/system_accounts.classify_cs_record。
    #       取舍: ① 不删 AI 数据(接管率本身是可控指标), 只分层;
    #             ② 真人名下的「系统标记」行**保留在真人**(实测 >50% 与真人操作同行同时刻,
    #                属连带记录; 拆出反而虚减人工工作量 18%) — 该取舍在页面 ⓘ 显式声明;
    #             ③ 不引入新表新列, 纯查询层派生。
    #       工单: #78 / 架构: docs/arch/A-20260913-001-AI接待口径分层.md
    #     ⚠️ 保持不变式: 各 kind 的 sessions 之和 == cs_today（可对账, 见验收标准 2）
    _cs_kind_case = case(
        # 有操作人且非系统账户 → 真人（含人工点 AI 助手, 场景 1/2/4）
        (
            and_(
                CsRecordClean.operator.isnot(None),
                func.trim(CsRecordClean.operator) != "",
                ~func.lower(func.trim(CsRecordClean.operator)).in_(
                    ["wechat", "sys", "system", "用户自助", "客户自助", "客户", "系统"]
                ),
            ),
            SourceKind.HUMAN.value,
        ),
        (func.lower(func.trim(func.coalesce(CsRecordClean.operator, ""))) == "wechat", SourceKind.AI_AUTO.value),
        # 客户自助: 空操作人 + description 以 openid 开头(≥20 位字母数字)
        #   注: PostgreSQL 正则匹配符 `~` 在 SQLAlchemy 需用 .op("~")(pattern) 表达
        #   (Python 的 `~` 是一元按位取反, 直接写 a ~ b 会语法错误)
        (
            and_(
                func.coalesce(func.trim(CsRecordClean.operator), "") == "",
                func.trim(func.coalesce(CsRecordClean.description, "")).op("~")("^[A-Za-z0-9_-]{20,}"),
            ),
            SourceKind.CUSTOMER_SELF.value,
        ),
        else_=SourceKind.AI_SYSTEM.value,
    ).label("kind")

    cs_kind_rows = db.execute(
        select(
            _cs_kind_case,
            func.count().label("sessions"),
            func.count(func.distinct(func.nullif(func.trim(CsRecordClean.device_sn), ""))).label("devices"),
            func.count(func.distinct(
                func.concat(func.coalesce(CsRecordClean.device_sn, ""), "|",
                            func.to_char(CsRecordClean.operation_time, "YYYY-MM-DD HH24:MI:SS"))
            )).label("events"),
        )
        .where(_cs_time_cond(ts, te))
        .group_by(_cs_kind_case)
    ).all()
    cs_breakdown: dict[str, dict[str, int]] = {
        k.value: {"sessions": 0, "devices": 0, "events": 0} for k in SourceKind
    }
    for r in cs_kind_rows:
        key = r.kind if r.kind in cs_breakdown else SourceKind.AI_SYSTEM.value
        cs_breakdown[key] = {
            "sessions": int(r.sessions or 0),
            "devices": int(r.devices or 0),
            "events": int(r.events or 0),
        }
    # AI 接管率: 会话口径(全量占比) + 设备口径(AI 独有 SN 占比)
    _ai_sessions = cs_breakdown[SourceKind.AI_AUTO.value]["sessions"]
    _human_sn_rows = db.execute(
        select(func.count(func.distinct(func.nullif(func.trim(CsRecordClean.device_sn), ""))))
        .where(_cs_time_cond(ts, te),
               func.lower(func.trim(func.coalesce(CsRecordClean.operator, ""))) != "wechat",
               func.coalesce(func.trim(CsRecordClean.operator), "") != "")
    ).scalar() or 0
    _ai_sn_rows = db.execute(
        select(func.count(func.distinct(func.nullif(func.trim(CsRecordClean.device_sn), ""))))
        .where(_cs_time_cond(ts, te),
               func.lower(func.trim(func.coalesce(CsRecordClean.operator, ""))) == "wechat")
    ).scalar() or 0
    _overlap = db.execute(
        select(func.count()).select_from(
            select(func.nullif(func.trim(CsRecordClean.device_sn), "").label("sn"))
            .where(_cs_time_cond(ts, te))
            .group_by(func.nullif(func.trim(CsRecordClean.device_sn), ""))
            .having(and_(
                func.count().filter(func.lower(func.trim(func.coalesce(CsRecordClean.operator, ""))) == "wechat") > 0,
                func.count().filter(and_(
                    func.lower(func.trim(func.coalesce(CsRecordClean.operator, ""))) != "wechat",
                    func.coalesce(func.trim(CsRecordClean.operator), "") != "",
                )) > 0,
            )).subquery()
        )
    ).scalar() or 0
    cs_ai_takeover = {
        "session_pct": round(_ai_sessions * 100.0 / cs_today, 1) if cs_today else 0.0,
        "device_pct": round(max(int(_ai_sn_rows or 0) - int(_overlap or 0), 0) * 100.0 / int(cs_device_today or 1), 1)
        if cs_device_today else 0.0,
        "device_shared": int(_overlap or 0),
        "device_ai_total": int(_ai_sn_rows or 0),
        "device_human_total": int(_human_sn_rows or 0),
    }


    # 2/3/4. 四路经营退款+实际现金流 (通过统一聚合器一次性计算)
    routes_t = _cash_routes_total(db, start=ts, end=te)
    routes_y = _cash_routes_total(db, start=ys, end=ye)
    a_t, b_t = routes_t["route_a"], routes_t["route_b"]
    _a_y, _b_y = routes_y["route_a"], routes_y["route_b"]
    c_t, d_t = routes_t["route_c"], routes_t["route_d"]
    _c_y, _d_y = routes_y["route_c"], routes_y["route_d"]
    e_t = routes_t["actual_cashflow"]          # 实际现金流 E (今日)
    e_y = routes_y["actual_cashflow"]          # 实际现金流 E (昨日)
    op_t = routes_t["operational"]             # 经营估算 A+B+C+D (今日)
    op_y = routes_y["operational"]             # 经营估算 A+B+C+D (昨日)
    # 退款笔数/金额 = 四路经营合计
    rf_cnt_t, rf_amt_t = op_t["count"], op_t["amount"]
    rf_cnt_y, rf_amt_y = op_y["count"], op_y["amount"]
    # 真实现金流 = 实际现金流 E (refund_record_reports 退款成功合计)
    cash_t = e_t["amount"]
    cash_y = e_y["amount"]
    merchant_today = e_t["count"]              # 实际现金流笔数
    merchant_tip = None
    today_d_iso = _dt.datetime.now(TZ).date().isoformat()
    range_end_iso = d_end_iso or d_iso
    if range_end_iso == today_d_iso and merchant_today == 0:
        merchant_tip = "商户号流水通常T+1导入，本日/本周期数据可能为空"

    # ⚠️ 2026-09-01 补充口径: "当日完成退款(已退款)" —— 按退款完成时刻(updated_at_raw)
    #   统计路线A(线上直退)里"当天实际完成退款"的单(可能几天前创建)。
    #   背景: BMS 页面"实际退款时间"= 更新时间; 原统计按 created_at_raw(创建时间),
    #   会把"几天前创建、今天才完成退款"的单归到创建那天, 导致今日看板"已退款"看不到。
    #   这里仅作**新增指标**返回, 不改变原 routes/refund_count 口径(保持历史一致)。
    def _route_a_done_in_window(s: _dt.datetime, e: _dt.datetime) -> dict:
        row = db.execute(select(
            func.count(),
            func.coalesce(func.sum(RefundClean.refund_amount), 0),
        ).where(
            *_route_a_online_refund_filter(),
            RefundClean.status == "已退款",
            _between(RefundClean.updated_at_raw, s, e),
        )).first() or (0, 0)
        return {"count": int(row[0] or 0), "amount": round(float(row[1] or 0), 2)}
    a_done_t = _route_a_done_in_window(ts, te)
    a_done_y = _route_a_done_in_window(ys, ye)

    # 5. 今日已完工单 (新口径:有效完工时间推算)
    #    - 背景: finish_time 89% 被批量覆盖/同步填充(详见 docs/reports/20260801-数据时间审核-审计报告.md L67-L70)
    #    - 推算链: 真实完成样本(白名单) → handle_duration 秒 → handler 近 30 天真实平均 → 全局真实平均
    #    - 仅统计 status='已完成' 的工单 (在途不视为完工)
    done_t, real_done_t = _ticket_effective_finish_time_where(db, ts, te)
    done_y, real_done_y = _ticket_effective_finish_time_where(db, ys, ye)

    # 6. 今日在岗人数 (cs_records distinct operator)
    staff_t = db.execute(select(func.count(func.distinct(CsRecordClean.operator)))
                         .where(_cs_time_cond(ts, te),
                                _cs_operator_cond())).scalar() or 0
    staff_y = db.execute(select(func.count(func.distinct(CsRecordClean.operator)))
                         .where(_cs_time_cond(ys, ye),
                                _cs_operator_cond())).scalar() or 0

    # 6b. ★ #309 窗口充值(真实现金流充入): device_combos 按 created_at_raw 落窗,
    #     剔除工厂质检专用套餐流水(package_name ILIKE '%质检%') —— 与分品类运营看板
    #     #293 同判据(出厂质检单笔 999/9999 元非真实营收, 2026-10 曾占当期流水 57.5%)。
    #     环比 = 上一等长窗口(ys~ye), 与其他 KPI 卡同一对比语义。
    _not_qc = DeviceComboClean.package_name.notilike("%质检%")
    _rc_t = db.execute(select(
        func.count(), func.coalesce(func.sum(DeviceComboClean.actual_amount), 0),
    ).where(_between(DeviceComboClean.created_at_raw, ts, te), _not_qc)).first()
    _rc_y = db.execute(select(
        func.count(), func.coalesce(func.sum(DeviceComboClean.actual_amount), 0),
    ).where(_between(DeviceComboClean.created_at_raw, ys, ye), _not_qc)).first()
    rc_cnt_t, rc_amt_t = int(_rc_t[0] or 0), round(float(_rc_t[1] or 0), 2)
    rc_cnt_y, rc_amt_y = int(_rc_y[0] or 0), round(float(_rc_y[1] or 0), 2)

    # 7. 今日投诉单 —— ★★ #81 (2026-09-13) 口径统一到「唯一出口」★★
    #
    #   ⚠️ 变更背景（#81 实测）:
    #     原实现 = `工单投诉类 + external_complaints 全表 + card_complaints 全表`
    #     → 两个缺陷:
    #       ① **违反 #53**: 卡商被并入投诉总量（用户明令"卡商不纳入总量, 全部单算"）
    #       ② **重复计数**: 315/卡商补充表里已 `matched_ticket_no` 关联到工单的记录,
    #          工单侧已算过, 表侧又加一次（本月实测 315 重复 22 条）
    #     → 导致同页「核心指标·投诉数」与「投诉专项四卡」数字必然对不上
    #       （本月 1127 vs 1111），使用者无法判断该信哪个。
    #
    #   现实现 = **直接调用 `complaints._sources_reg_handled`**，与四卡/`/summary`
    #     共用同一个判据出口（`complaint_scope` 分桶 + 表侧扣重 + 卡商剥离）。
    #     口径: 内部 + 商户号 + 外部（**不含卡商**；卡商见独立 `card` 区块）。
    #
    #   ⚠️ 为什么不在本文件另写一套: #65 的教训就是口径漂移 —— 软纠正只落在对账侧,
    #      统计侧漏了 160 条真实监所投诉。**同源是唯一可靠做法**。
    #   ⚠️ 惰性导入原因: `complaints.py` 已 `from app.services.analytics import ...`
    #      （模块级），此处若顶层 import 会形成循环依赖。
    from app.api.v1.complaints import _sources_reg_handled as _srh

    _comp_t = _srh(db, ts, te, include_suppl_external=_INCLUDE_COMPLAINT_FILES)["total"]
    comp_t = int(_comp_t["registered"])
    _comp_y = _srh(db, ys, ye, include_suppl_external=_INCLUDE_COMPLAINT_FILES)["total"]
    comp_y = int(_comp_y["registered"])

    # 7b. ★ #82: 目标达成率水位（把「够不够」显示到看板上）
    #   【为什么加】看板原只有「环比」——只能说明"变了多少"，无法说明"是否可接受"。
    #     「涨了 5%」与「还差得远」是两件完全不同的事，缺目标线就分不清。
    #   【数据来源】`metric_thresholds` 表 —— **复用既有阈值引擎，不新建机制**。
    #   【口径锚点】阈值按**日**配置（metric.period='day'），而本接口支持周/月/季/年。
    #     ⚠️ 故**只在统计窗口正好是单日时**才给出级别，否则 level=None
    #       —— 拿日目标去判周数据是错的（会持续虚警）。有意的保守取舍。
    #
    # ★★★ 只接「口径与阈值都验证过」的指标（2026-09-13 生产实测，极重要）★★★
    #   实测发现既有阈值**多个严重错配**（详见工单 #82 评论）：
    #     refund_count            实际 314  阈值 30/50    → 差 10 倍
    #     ticket_backlog          实际 599  阈值 20/50    → 差 30 倍
    #     internal_complaint_daily实际 123  阈值 8/15     → 差 15 倍
    #   且这三条**口径也与看板卡不同**（refund_count=refunds 单表仅A路，
    #   而看板「退款笔数」= A+B+C+D 四路合计；internal_complaint_daily=内部单源，
    #   而看板「投诉数」= 三源合计）。
    #   ⇒ 若强行接上，看板会显示「退款笔数严重告警」（其实 314 是正常量），
    #     **比不显示更糟** —— 会训练使用者忽略告警。故本函数**只接口径同源且
    #     阈值经实测合理的指标**，其余明确不接（宁缺勿错，#81 同源原则）。
    #
    # ★★★ 接入指标（2026-09-14 复核，#83 阈值错配治理后扩展）★★★
    #   【准入条件 — 两条都必须满足】
    #     ① 口径同源：指标求值必须与看板对应卡走同一实现
    #     ② 阈值经分布验证：常态日不应恒告警
    #
    #   `refund_count` ✅ 两项都满足（#83 已修）:
    #     · 口径：已改为四路经营合计（`_cash_routes_total`），与看板「退款笔数」同源
    #     · 阈值：400/460（依据近90天四路分布 p90=403/p95=451）
    #   `recovery_rate` ✅:
    #     · 口径：底层同为 `refund_recovery_analysis`，与看板挽回卡逐位一致
    #     · 阈值：40/30/20（与 Today.vue 既有业务规则吻合）
    #
    #   ⚠️ 仍**刻意不接**的指标（原因各不相同）:
    #     · `ticket_backlog`（存量，585）—— 与看板「积压待处理」的**窗口口径不同**
    #       （看板是近30天窗口，指标是全量）；且存量指标在水位卡上意义有限
    #       （看趋势应用 `ticket_backlog_net`）→ 不在核心指标条展示，避免口径混淆
    #     · `internal_complaint_daily` —— 口径是**内部单源**，而看板「投诉数」是
    #       **三源合计**；放在同一排会让人误以为应相等（语义不同，不并列）
    #     · `external_complaint_7d` —— 是**滚动 7 天**窗口，与看板的窗口语义不同
    _metric_specs = {
        "recovery_rate": (
            lambda: _rr_val, "%", "higher_better",
            "退款挽回成功率（与退款挽回专项同口径）",
        ),
        "refund_count": (
            lambda: float(rf_cnt_t), "笔", "lower_better",
            "当日退款笔数 = 四路经营合计 A+B+C+D（与看板「退款笔数」同源）",
        ),
    }
    _metric_levels: dict[str, Any] = {}
    try:
        from app.services.metric_registry_service import _eval_recovery_rate as _ev_rr
        from app.services.metric_registry_service import classify as _mcls
        from app.services.metric_registry_service import get_threshold as _mthr

        # ⚠️ 单日判定用本函数已有的 `d_iso`/`d_end_iso`（来自 _resolve_windows），
        #   不要用 `ts_sh`/`te_sh` —— 那是 `today_hourly_trend` 的局部变量，此函数没有。
        _is_single_day = (d_end_iso is None) or (d_iso == d_end_iso)
        _rr_res = _ev_rr(db, ts, te)
        _rr_val = _rr_res.get("value") if _rr_res.get("data_ok") else None
        for _code, (_getter, _unit, _dir, _desc) in _metric_specs.items():
            _thr = _mthr(db, _code)
            if _thr is None:
                continue  # 未配阈值 → 不给水位（不编造目标线）
            _val = _getter()
            _metric_levels[_code] = {
                "value": _val,
                "unit": _unit,
                "direction": _dir,
                "level": _mcls(_val, _thr, _dir) if (_is_single_day and _val is not None) else None,
                "target": _thr.excellent_value,
                "warning": _thr.warning_value,
                "critical": _thr.critical_value,
                "desc": _desc,
                # ⚠️ 无法判定时必须让前端能解释"为什么不显示"，而非默默当作达标
                "reason": (
                    None if _is_single_day
                    else "目标线按【日】配置，当前窗口为多日，不做达标判定（避免用日目标判周数据）"
                ),
                # 阈值来源标注：不写死在前端，让使用者知道可调整
                "source": "指标注册表 metric_thresholds（可由管理员调整）",
            }
    except Exception as _e:  # noqa: BLE001
        # 阈值异常**不得**影响看板主数据 —— 水位是附加信息，缺失时前端不渲染
        logger.warning("[#82] 指标水位求值失败(不影响主数据): %s", _e)
        _metric_levels = {}
    metric_levels = _metric_levels

    def _k(v, yv):
        return {"value": v, "yoy_value": yv, "yoy_pct": _pct_delta(v, yv)}

    # 返回的window字段用上海时区展示(前端显示友好); ts/te仍用于DB比较
    _utc = _dt.timezone.utc
    def _sh_iso(naive_utc: _dt.datetime) -> str:
        return naive_utc.replace(tzinfo=_utc).astimezone(TZ).isoformat()

    return {
        "date": d_iso,
        "date_end": d_end_iso,
        "is_realtime": is_realtime,
        "window": {
            "today_start": _sh_iso(ts), "today_end": _sh_iso(te),
            "yoy_start": _sh_iso(ys), "yoy_end": _sh_iso(ye),
        },
        "reception":      {
            **_k(int(cs_today), int(cs_yoy)),
            "device_count": int(cs_device_today), "device_count_yoy": int(cs_device_yoy),
            # ★ #78 接待来源分层: 真人 / AI全自动 / 系统 / 客户自助
            #   不变式: 各 kind 的 sessions 之和 == value(会话数); devices 之和 >= device_count(SN 跨类重叠)
            "breakdown": cs_breakdown,
            # ★ #78 AI 接管率(可控指标前置): 会话占比 + AI 独有 SN 占比
            "ai_takeover": cs_ai_takeover,
        },
        "refund_count":   _k(rf_cnt_t, rf_cnt_y),
        "refund_amount":  _k(round(rf_amt_t, 2), round(rf_amt_y, 2)),
        # ★ #309 窗口充值(剔除质检流水): value=金额, count=笔数, 环比=上一等长窗口
        "recharge":       {**_k(rc_amt_t, rc_amt_y),
                           "count": rc_cnt_t, "count_yoy": rc_cnt_y,
                           "count_yoy_pct": _pct_delta(rc_cnt_t, rc_cnt_y)},
        "cash_out":       _k(round(cash_t, 2), round(cash_y, 2)),      # 经营估算 A+B+C+D
        "actual_cashflow":_k(round(e_t["amount"], 2), round(e_y["amount"], 2)), # (新) 实际现金流E
        # ⚠️ 2026-09-01 新增: 当日完成退款(已退款,按更新时间) —— 补 BMS"实际退款时间"口径
        "refund_done_today": {**_k(a_done_t["count"], a_done_y["count"]), "amount": a_done_t["amount"], "amount_yoy": a_done_y["amount"]},
        "ticket_done":    {**_k(int(done_t), int(done_y)), "real_done_today": int(real_done_t), "real_done_yoy": int(real_done_y)},
        "staff_on_duty":  _k(int(staff_t), int(staff_y)),
        "complaint":      _k(int(comp_t), int(comp_y)),
        "merchant_tip":   merchant_tip,
        # ★ #82: 目标达成率水位（把「够不够」给出来；空 dict = 无阈值或非单日窗口）
        #   结构: {metric_code: {value, unit, direction, level, target, warning, critical, desc, reason}}
        #   ⚠️ level=null 且 reason 非空 ⇒ 前端应显示"不判定"并给出原因，而非当作达标
        "metric_levels":  metric_levels,
        # ── 新增: 今日四路线拆分 + 实际现金流E + 对账偏差 (前端tooltip/明细卡) ──
        "cash_routes_today": {
            "route_a": a_t,  # A.退费单线上直退(按创建时间)
            "route_a_done": a_done_t,  # A.线上直退"当日完成(已退款,按更新时间)" 补充口径
            "route_b": b_t,  # B.钱包用户取现
            "route_c": c_t,  # C.闪猫退款 (独立表 shanmao_refunds, status=已退款 计入)
            "route_d": d_t,  # D.手动退款 (独立业务表 manual_refunds, is_refunded=是 计入)
            "operational":     routes_t["operational"],     # 经营估算 A+B+C+D
            "actual_cashflow": routes_t["actual_cashflow"], # 实际现金流 E (退款记录报表退款成功合计)
            "financial":       routes_t["financial"],       # 向后兼容: 财务实际 = E
            "diff":            routes_t["diff"],            # 对账偏差 = 经营 − 实际
        },
        # 前一等长周期四路(滑动周/月对比与日同刻对比用): routes_y 本就算好, 原先未透出,
        # 导致 LLM 写「四路环比」无数据。结构与 cash_routes_today 对齐(无 diff/done 派生)。
        "cash_routes_prev": {
            "route_a": routes_y["route_a"],
            "route_a_done": a_done_y,
            "route_b": routes_y["route_b"],
            "route_c": routes_y["route_c"],
            "route_d": routes_y["route_d"],
            "operational":     routes_y["operational"],
            "actual_cashflow": routes_y["actual_cashflow"],
            "financial":       routes_y["financial"],
            "window": {"start": _sh_iso(ys), "end": _sh_iso(ye)},
        },
        # ── 口径注释 (前端标题tooltip) ──
        "_spec": {
            "refund_count":     "退款笔数 = 四路经营合计 A线上直退 + B用户取现 + C闪猫退款 + D手动退款 (按创建时间)",
            "refund_amount":    "退款金额 = 四路经营合计 A+B+C+D (经营侧登记的所有退款出款)",
            "recharge":         "充值金额 = device_combos 窗口内 actual_amount 合计(按 created_at_raw 落窗), 已剔除工厂质检专用套餐流水(package_name 含「质检」) —— 与分品类运营看板 #293 同判据, 只计真实付费充入; count=充值笔数; 环比=上一等长窗口(#309)",
            "refund_done_today":"当日完成退款 = 路线A(线上直退) 中 status=已退款 且 更新时间(退款完成时刻)∈ 当日, 含几天前创建今天才完成的单 (BMS 实际退款时间口径)",
            "cash_out":         "真实现金流(对账基准) = refund_record_reports 退款成功合计, 不分ORIGINAL/BALANCE桶 (2026-09-05 v1.1 起替代 merchant_refunds)",
            "actual_cashflow":  "实际现金流(对账基准) = refund_record_reports 退款成功合计, 不分ORIGINAL/BALANCE桶",
            "ticket_done":      "有效完工时间推算: 10.7% 真实完成(白名单:status=已完成且finish在register+12h内且handler非空非SYS) → handle_duration秒 → 该handler近30天真实平均耗时 → 全局真实平均。仅status=已完成视为完工, 避免finish_time被BMS 03:15批量覆盖导致的虚高。",
            # ★ #81 (2026-09-13): 投诉口径统一后的说明（前端 ⓘ 直接引用，勿另写）
            "complaint":        "投诉数 = 三源合计(内部投诉 + 商户号投诉 + 外部投诉)，**不含 (外)卡商**（#53: 卡商独立统计, 全部单算）。判据与「投诉专项」四卡同源(complaint_scope 分桶 + #58 软纠正)，并已扣除补充表(315)中已关联到工单的重叠记录, 不重复计数。修正前为『工单 + 315表 + 卡商表』不扣重且含卡商 → 与四卡数字对不上(见 #81)。",
        },
    }

