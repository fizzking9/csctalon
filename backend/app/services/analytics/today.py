"""今日看板: today_kpi / 小时趋势 / 接待拆分 / 退款拆分 / 队列 / 员工效率。"""

import datetime as _dt
import logging
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
    cs_today = db.execute(select(func.count()).where(_between(CsRecord.operation_time, ts, te))).scalar() or 0
    cs_yoy = db.execute(select(func.count()).where(_between(CsRecord.operation_time, ys, ye))).scalar() or 0
    # 1b. 今日服务设备数 (cs_records.device_sn 去重, 排除 NULL/空串) — 补充口径：1台设备多次进线算1次，体现覆盖面
    cs_device_today = db.execute(select(
        func.count(func.distinct(func.nullif(func.trim(CsRecord.device_sn), "")))
    ).where(_between(CsRecord.operation_time, ts, te))).scalar() or 0
    cs_device_yoy = db.execute(select(
        func.count(func.distinct(func.nullif(func.trim(CsRecord.device_sn), "")))
    ).where(_between(CsRecord.operation_time, ys, ye))).scalar() or 0

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
                CsRecord.operator.isnot(None),
                func.trim(CsRecord.operator) != "",
                ~func.lower(func.trim(CsRecord.operator)).in_(
                    ["wechat", "sys", "system", "用户自助", "客户自助", "客户", "系统"]
                ),
            ),
            SourceKind.HUMAN.value,
        ),
        (func.lower(func.trim(func.coalesce(CsRecord.operator, ""))) == "wechat", SourceKind.AI_AUTO.value),
        # 客户自助: 空操作人 + description 以 openid 开头(≥20 位字母数字)
        #   注: PostgreSQL 正则匹配符 `~` 在 SQLAlchemy 需用 .op("~")(pattern) 表达
        #   (Python 的 `~` 是一元按位取反, 直接写 a ~ b 会语法错误)
        (
            and_(
                func.coalesce(func.trim(CsRecord.operator), "") == "",
                func.trim(func.coalesce(CsRecord.description, "")).op("~")("^[A-Za-z0-9_-]{20,}"),
            ),
            SourceKind.CUSTOMER_SELF.value,
        ),
        else_=SourceKind.AI_SYSTEM.value,
    ).label("kind")

    cs_kind_rows = db.execute(
        select(
            _cs_kind_case,
            func.count().label("sessions"),
            func.count(func.distinct(func.nullif(func.trim(CsRecord.device_sn), ""))).label("devices"),
            func.count(func.distinct(
                func.concat(func.coalesce(CsRecord.device_sn, ""), "|",
                            func.to_char(CsRecord.operation_time, "YYYY-MM-DD HH24:MI:SS"))
            )).label("events"),
        )
        .where(_between(CsRecord.operation_time, ts, te))
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
        select(func.count(func.distinct(func.nullif(func.trim(CsRecord.device_sn), ""))))
        .where(_between(CsRecord.operation_time, ts, te),
               func.lower(func.trim(func.coalesce(CsRecord.operator, ""))) != "wechat",
               func.coalesce(func.trim(CsRecord.operator), "") != "")
    ).scalar() or 0
    _ai_sn_rows = db.execute(
        select(func.count(func.distinct(func.nullif(func.trim(CsRecord.device_sn), ""))))
        .where(_between(CsRecord.operation_time, ts, te),
               func.lower(func.trim(func.coalesce(CsRecord.operator, ""))) == "wechat")
    ).scalar() or 0
    _overlap = db.execute(
        select(func.count()).select_from(
            select(func.nullif(func.trim(CsRecord.device_sn), "").label("sn"))
            .where(_between(CsRecord.operation_time, ts, te))
            .group_by(func.nullif(func.trim(CsRecord.device_sn), ""))
            .having(and_(
                func.count().filter(func.lower(func.trim(func.coalesce(CsRecord.operator, ""))) == "wechat") > 0,
                func.count().filter(and_(
                    func.lower(func.trim(func.coalesce(CsRecord.operator, ""))) != "wechat",
                    func.coalesce(func.trim(CsRecord.operator), "") != "",
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
            func.coalesce(func.sum(Refund.refund_amount), 0),
        ).where(
            *_route_a_online_refund_filter(),
            Refund.status == "已退款",
            _between(Refund.updated_at_raw, s, e),
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
    staff_t = db.execute(select(func.count(func.distinct(CsRecord.operator)))
                         .where(_between(CsRecord.operation_time, ts, te),
                                CsRecord.operator.isnot(None))).scalar() or 0
    staff_y = db.execute(select(func.count(func.distinct(CsRecord.operator)))
                         .where(_between(CsRecord.operation_time, ys, ye),
                                CsRecord.operator.isnot(None))).scalar() or 0

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
        # ── 口径注释 (前端标题tooltip) ──
        "_spec": {
            "refund_count":     "退款笔数 = 四路经营合计 A线上直退 + B用户取现 + C闪猫退款 + D手动退款 (按创建时间)",
            "refund_amount":    "退款金额 = 四路经营合计 A+B+C+D (经营侧登记的所有退款出款)",
            "refund_done_today":"当日完成退款 = 路线A(线上直退) 中 status=已退款 且 更新时间(退款完成时刻)∈ 当日, 含几天前创建今天才完成的单 (BMS 实际退款时间口径)",
            "cash_out":         "真实现金流(对账基准) = refund_record_reports 退款成功合计, 不分ORIGINAL/BALANCE桶 (2026-09-05 v1.1 起替代 merchant_refunds)",
            "actual_cashflow":  "实际现金流(对账基准) = refund_record_reports 退款成功合计, 不分ORIGINAL/BALANCE桶",
            "ticket_done":      "有效完工时间推算: 10.7% 真实完成(白名单:status=已完成且finish在register+12h内且handler非空非SYS) → handle_duration秒 → 该handler近30天真实平均耗时 → 全局真实平均。仅status=已完成视为完工, 避免finish_time被BMS 03:15批量覆盖导致的虚高。",
            # ★ #81 (2026-09-13): 投诉口径统一后的说明（前端 ⓘ 直接引用，勿另写）
            "complaint":        "投诉数 = 三源合计(内部投诉 + 商户号投诉 + 外部投诉)，**不含 (外)卡商**（#53: 卡商独立统计, 全部单算）。判据与「投诉专项」四卡同源(complaint_scope 分桶 + #58 软纠正)，并已扣除补充表(315)中已关联到工单的重叠记录, 不重复计数。修正前为『工单 + 315表 + 卡商表』不扣重且含卡商 → 与四卡数字对不上(见 #81)。",
        },
    }

def today_hourly_trend(db: Session, date: str | None = None, start: str | None = None, end: str | None = None, trend_grain: str | None = None) -> dict[str, Any]:
    """模块2: 时间节奏 (小时/日/周/月 支持手动指定聚合粒度) v5

    粒度规则:
      - trend_grain 手动指定优先级最高: hour/day/week/month
      - 单日自动 → 按小时聚合 (00:00~23:00, 对应今日实时到当前小时)
      - 多日自动 → 按天聚合 (date_trunc day, 标签为 MM-DD)
      - 周聚合: 周一为起始, 标签如 "W32(08-04~08-10)"
      - 月聚合: 标签如 "08月"
    按三组分类展示:
      【工作量组】左Y柱:
        - 会话数(次) / 接待数(个): 独立柱
      【款项组】左Y柱(笔数堆叠) + 右Y面积(金额堆叠):
        - 退款笔数: 四路堆叠柱 → A(线上直退) + B(钱包提现) + C(闪猫退款) + D(手动退款) = 经营合计
        - 退款金额: 四路堆叠面积 → A+B+C+D = 经营合计; 独立虚线 = E(商户号实际现金流)
      【投诉组】左Y柱:
        - 投诉量: 独立柱
    """
    ts, te, _, _, is_realtime, disp_range = _resolve_windows(date=date, start=start, end=end)

    # 先计算跨度天数(北京时间日历日)
    ts_sh = _dt.datetime.fromtimestamp(ts.replace(tzinfo=_dt.timezone.utc).timestamp(), tz=TZ).date()
    te_sh = _dt.datetime.fromtimestamp(te.replace(tzinfo=_dt.timezone.utc).timestamp(), tz=TZ).date()
    span_days = (te_sh - ts_sh).days + 1

    # 确定聚合粒度
    valid_grains = {"hour", "day", "week", "month"}
    if trend_grain in valid_grains:
        grain = trend_grain
    elif span_days <= 1:
        grain = "hour"
    else:
        grain = "day"
    # ── 时间截断函数 ──
    def _time_trunc(col):
        if grain == "hour":
            return func.date_trunc("hour", func.timezone("Asia/Shanghai", col)).label("trunc_t")
        elif grain == "week":
            # date_trunc('week', ...) 返回周一为起始的日期
            return func.date_trunc("week", func.timezone("Asia/Shanghai", col)).label("trunc_t")
        elif grain == "month":
            return func.date_trunc("month", func.timezone("Asia/Shanghai", col)).label("trunc_t")
        return func.date_trunc("day", func.timezone("Asia/Shanghai", col)).label("trunc_t")
    # 2. 小时/日/周/月 标签与横轴: 填充连续槽位
    now_sh = _dt.datetime.now(TZ)
    # 预定义辅助函数: datetime转date
    def _to_date(d):
        if isinstance(d, _dt.datetime):
            return d.date()
        return d
    if grain == "hour":
        # 小时轴: 0~23 小时
        list(range(24))
        is_today_like = is_realtime
        max_slot = now_sh.hour if is_today_like else 23
        def _slot_idx(trunc_t_local: _dt.datetime) -> int | None:
            return int(trunc_t_local.hour)
        def _slot_label(slot: int) -> str:
            return f"{slot:02d}:00"
        x_label = "小时节奏 (北京时间)"
        n_slots = 24
    elif grain == "week":
        # 周轴: 从start所在周到end所在周 (周一为起始)
        # 用datetime对象计算周起始
        ts_sh_dt = _dt.datetime.fromtimestamp(ts.replace(tzinfo=_dt.timezone.utc).timestamp(), tz=TZ)
        te_sh_dt = _dt.datetime.fromtimestamp(te.replace(tzinfo=_dt.timezone.utc).timestamp(), tz=TZ)
        def _start_of_week(d):
            d = _to_date(d)
            return d - timedelta(days=d.weekday())
        cur_week_start = _start_of_week(ts_sh_dt)
        end_week_start = _start_of_week(te_sh_dt)
        slots_weeks = []
        while cur_week_start <= end_week_start:
            slots_weeks.append(cur_week_start)
            cur_week_start += timedelta(days=7)
        # 判断最后一周是否是当前周(实时)
        current_week_start = _start_of_week(now_sh)
        def _slot_idx(trunc_t_local) -> int | None:
            # date_trunc 返回 datetime，先转 date
            ws = _start_of_week(trunc_t_local)
            if ws in slots_weeks:
                return slots_weeks.index(ws)
            return None
        def _slot_label(slot: int) -> str:
            ws = slots_weeks[slot]
            we = ws + timedelta(days=6)
            return f"W{ws.isocalendar()[1]}({ws.strftime('%m-%d')}~{we.strftime('%m-%d')})"
        max_slot = slots_weeks.index(current_week_start) if current_week_start in slots_weeks and is_realtime else len(slots_weeks) - 1
        x_label = "周节奏 (周一~周日)"
        n_slots = len(slots_weeks)
    elif grain == "month":
        # 月轴: 从start所在月到end所在月
        ts_sh_dt = _dt.datetime.fromtimestamp(ts.replace(tzinfo=_dt.timezone.utc).timestamp(), tz=TZ)
        te_sh_dt = _dt.datetime.fromtimestamp(te.replace(tzinfo=_dt.timezone.utc).timestamp(), tz=TZ)
        def _start_of_month(d):
            d = _to_date(d)
            return d.replace(day=1)
        cur_month_start = _start_of_month(ts_sh_dt)
        end_month_start = _start_of_month(te_sh_dt)
        slots_months = []
        while cur_month_start <= end_month_start:
            slots_months.append(cur_month_start)
            # 下月1号
            if cur_month_start.month == 12:
                cur_month_start = cur_month_start.replace(year=cur_month_start.year + 1, month=1)
            else:
                cur_month_start = cur_month_start.replace(month=cur_month_start.month + 1)
        # 判断最后一月是否是当前月(实时)
        current_month_start = _start_of_month(now_sh)
        def _slot_idx(trunc_t_local) -> int | None:
            ms = _start_of_month(trunc_t_local)
            if ms in slots_months:
                return slots_months.index(ms)
            return None
        def _slot_label(slot: int) -> str:
            ms = slots_months[slot]
            return f"{ms.strftime('%Y-%m')}"
        max_slot = slots_months.index(current_month_start) if current_month_start in slots_months and is_realtime else len(slots_months) - 1
        x_label = "月节奏"
        n_slots = len(slots_months)
    else:
        # 日轴: 从 start 到 end 连续日期 (按 Asia/Shanghai 日历日)
        # ts_sh/te_sh已经在开头计算为date类型
        slots_days: list[_dt.date] = []
        cur = ts_sh
        while cur <= te_sh:
            slots_days.append(cur)
            cur += timedelta(days=1)
        max_slot = len(slots_days) - 1
        now_sh.date()
        def _slot_idx(trunc_t_local: _dt.datetime) -> int | None:
            d_local = _to_date(trunc_t_local)
            if d_local in slots_days:
                return slots_days.index(d_local)
            return None
        def _slot_label(slot: int) -> str:
            return slots_days[slot].strftime("%m-%d")
        x_label = "日期 (跨度 {} 天)".format(len(slots_days))
        n_slots = len(slots_days)
    cs_rows = db.execute(
        select(_time_trunc(CsRecord.operation_time),
               func.count().label("cnt"),
               func.count(func.distinct(func.nullif(func.trim(CsRecord.device_sn), ""))).label("dev_cnt"))
        .where(CsRecord.operation_time.isnot(None),
               CsRecord.operation_time >= ts, CsRecord.operation_time <= te)
        .group_by("trunc_t")
    ).all()
    cs_map: dict[int, int] = {}
    cs_dev_map: dict[int, int] = {}
    for r in cs_rows:
        tt = r._mapping["trunc_t"]
        if tt is None:
            continue
        idx = _slot_idx(tt)
        if idx is None:
            continue
        cs_map[idx] = int(r.cnt or 0)
        cs_dev_map[idx] = int(r.dev_cnt or 0)

    # --- 路线A: 退费单线上直退(按粒度 笔数+金额) ---
    rf_rows = db.execute(
        select(_time_trunc(Refund.created_at_raw),
               func.count().label("cnt"),
               func.coalesce(func.sum(Refund.refund_amount), 0).label("amt"))
        .where(Refund.created_at_raw.isnot(None),
               Refund.created_at_raw >= ts, Refund.created_at_raw <= te,
               *_route_a_online_refund_filter())
        .group_by("trunc_t")
    ).all()
    a_cnt_map: dict[int, int] = {}
    a_amt_map: dict[int, float] = {}
    for r in rf_rows:
        tt = r._mapping["trunc_t"]
        if tt is None:
            continue
        idx = _slot_idx(tt)
        if idx is None:
            continue
        a_cnt_map[idx] = int(r.cnt or 0)
        a_amt_map[idx] = float(r.amt or 0)

    # --- 路线B: 钱包提现(按粒度 笔数+金额) ---
    wb_rows = db.execute(
        select(_time_trunc(WalletWithdrawal.op_time),
               func.count().label("cnt"),
               func.coalesce(func.sum(WalletWithdrawal.op_amount), 0).label("amt"))
        .where(WalletWithdrawal.op_time.isnot(None),
               WalletWithdrawal.op_time >= ts, WalletWithdrawal.op_time <= te,
               *_route_b_withdraw_filter())
        .group_by("trunc_t")
    ).all()
    b_cnt_map: dict[int, int] = {}
    b_amt_map: dict[int, float] = {}
    for r in wb_rows:
        tt = r._mapping["trunc_t"]
        if tt is None:
            continue
        idx = _slot_idx(tt)
        if idx is None:
            continue
        b_cnt_map[idx] = int(r.cnt or 0)
        b_amt_map[idx] = float(r.amt or 0)

    # --- 路线C: 闪猫退款(按粒度 笔数+金额) ---
    c_cnt_map, c_amt_map = _route_c_shanmao_trend(db, ts, te, grain, _slot_idx)

    # --- 路线D: 手动退款(按粒度 笔数+金额) ---
    d_cnt_map, d_amt_map = _route_d_other_trend(db, ts, te, grain, _slot_idx)

    # --- 路线E: 商户号实际现金流(按粒度 笔数+金额, 数据源=refund_record_reports) ---
    e_rows = db.execute(
        select(_time_trunc(RefundRecordReport.refund_time),
               func.count().label("cnt"),
               func.coalesce(func.sum(RefundRecordReport.refund_amount), 0).label("amt"))
        .where(RefundRecordReport.refund_time.isnot(None),
               RefundRecordReport.refund_time >= ts, RefundRecordReport.refund_time <= te,
               *_route_e_cashflow_filter())
        .group_by("trunc_t")
    ).all()
    e_cnt_map: dict[int, int] = {}
    e_amt_map: dict[int, float] = {}
    for r in e_rows:
        tt = r._mapping["trunc_t"]
        if tt is None:
            continue
        idx = _slot_idx(tt)
        if idx is None:
            continue
        e_cnt_map[idx] = int(r.cnt or 0)
        e_amt_map[idx] = float(r.amt or 0)

    # 投诉: ★★ #85 口径修正 —— 由「工单 + 315全表 + 卡商全表」改为
    #   **与 #81 核心指标同源**（分桶 + 扣 315 已 matched 重叠 + 剥卡商）
    #
    #   【修什么】原实现（2026-09-14 前）：
    #     `工单投诉类 + ExternalComplaint 全表 + CardComplaint 全表`
    #     → ① **含卡商**（与 #53「卡商独立、不纳入总量」直接冲突）
    #       ② **不扣重叠** → 315 里已 matched 到工单的记录被重复计入
    #       ③ 与同页「核心指标·投诉数」**对不上**（实测 1127 vs 1111，差 16）
    #     #81 统一了核心指标，但**漏改了趋势层** → 本处补上。
    #
    #   【新实现】按三源分桶逐桶统计（与 `_sources_reg_handled` 同源）：
    #     · 内部/商户号 = 工单按 `register_time` 落桶（无补充表）
    #     · 外部 = 工单按登记日 + 315 补充表**净新增**（总数 − 已 matched 重叠）
    #     · **卡商不并入**（#53）→ 保证「柱加总 == 核心指标投诉数」
    #
    #   【为什么按分桶而非直接复用 _sources_reg_handled】后者返回**窗口汇总**，
    #     而趋势需要**按桶（小时/日/周/月）拆分**。故此处按同一判据 `src_expr`
    #     分组，而非调用汇总函数 —— **判据同源，粒度不同**。
    src_expr_cp = _complaint_source_type_case()
    cp_map: dict[int, int] = {}
    cp_rows = db.execute(
        select(_time_trunc(ServiceTicket.register_time), src_expr_cp.label("src"),
               func.count().label("cnt"))
        .where(ServiceTicket.register_time.isnot(None),
               ServiceTicket.register_time >= ts, ServiceTicket.register_time <= te,
               _ticket_category_case() == "投诉",
               # ★ #53: 卡商独立，不并入投诉柱
               src_expr_cp != CARD_SCOPE)
        .group_by("trunc_t", "src")
    ).all()
    for r in cp_rows:
        tt = r._mapping["trunc_t"]
        if tt is None:
            continue
        idx = _slot_idx(tt)
        if idx is None:
            continue
        cp_map[idx] = cp_map.get(idx, 0) + int(r.cnt or 0)
    # ★ 处理序列（finish 落桶 + 真实完成）—— 用户 2026-09-14 选定「柱(登记)+叠处理线」
    #   口径与 `complaints._source_reg_handled` 的 handled **同源**：
    #     finish_time 落桶 且 finish − register ≥ 3min（排除同瞬填充的伪完成）
    #     finish_time 落桶 且 finish − register ≥ 3min（排除同瞬填充的伪完成）
    #
    #   ⚠️⚠️ 关键：**不限制 register_time 也在窗口内**（2026-09-14 实测踩到）
    #     若加上该限制，会**丢掉 83 条「窗口外登记、本窗口办结」的历史单**
    #     （实测 1044 → 961）—— 而"今天实际清掉多少历史积压"**正是主管要看的数**
    #     （#82 账龄分档显示有 284 条 >90 天陈年单，办结它们才是有价值的信号）。
    #     → 故**按 finish 落桶**，语义 = 「该时段内完成了多少（含往期单）」。
    #   ★ 与「登记柱」配合读法：柱高 > 线高 ⇒ 在累积；柱高 < 线高 ⇒ 在消化。
    cp_done_map: dict[int, int] = {}
    cp_done_rows = db.execute(
        select(_time_trunc(ServiceTicket.finish_time), func.count().label("cnt"))
        .where(ServiceTicket.finish_time.isnot(None),
               ServiceTicket.register_time.isnot(None),
               ServiceTicket.finish_time >= ts, ServiceTicket.finish_time <= te,
               _ticket_category_case() == "投诉",
               src_expr_cp != CARD_SCOPE,
               func.extract("epoch", ServiceTicket.finish_time - ServiceTicket.register_time) >= 180)
        .group_by("trunc_t")
    ).all()
    for r in cp_done_rows:
        tt = r._mapping["trunc_t"]
        if tt is None:
            continue
        idx = _slot_idx(tt)
        if idx is None:
            continue
        cp_done_map[idx] = int(r.cnt or 0)
    # 315 补充表「已办结」并入处理线（与 `_source_reg_handled` 的 external 分支同源）
    #   去重: 已办结但 matched 工单已在工单侧计入 handled 的排除（避免同一笔双算）
    if _INCLUDE_COMPLAINT_FILES:
        _done_sub = select(ServiceTicket.ticket_no).where(
            _ticket_category_case() == "投诉",
            ServiceTicket.finish_time.isnot(None),
            ServiceTicket.register_time.isnot(None),
            func.extract("epoch", ServiceTicket.finish_time - ServiceTicket.register_time) >= 180,
            ServiceTicket.finish_time >= ts, ServiceTicket.finish_time <= te)
        ec_done_rows = db.execute(
            select(_time_trunc(ExternalComplaint.complaint_date), func.count().label("cnt"),
                   func.count(ExternalComplaint.matched_ticket_no)
                   .filter(ExternalComplaint.matched_ticket_no.in_(_done_sub)).label("ov"))
            .where(ExternalComplaint.complaint_date.isnot(None),
                   ExternalComplaint.complaint_date >= ts, ExternalComplaint.complaint_date <= te,
                   ExternalComplaint.result.in_(_EXTERNAL_DONE_RESULTS_LOCAL))
            .group_by("trunc_t")
        ).all()
        for r in ec_done_rows:
            tt = r._mapping["trunc_t"]
            if tt is None:
                continue
            idx = _slot_idx(tt)
            if idx is None:
                continue
            net = int(r.cnt or 0) - int(r.ov or 0)
            if net:
                cp_done_map[idx] = cp_done_map.get(idx, 0) + net
    # 315 补充表**净新增**并入「外部投诉」（与 /summary 合并口径一致）
    #   净新增 = 当日总数 − 已 matched 到本范围内工单的条数（避免与工单双算）
    if _INCLUDE_COMPLAINT_FILES:
        _ec_cond = [ExternalComplaint.complaint_date.isnot(None),
                    ExternalComplaint.complaint_date >= ts, ExternalComplaint.complaint_date <= te]
        _sub_tk = select(ServiceTicket.ticket_no).where(
            _ticket_category_case() == "投诉",
            ServiceTicket.register_time.isnot(None),
            ServiceTicket.register_time >= ts, ServiceTicket.register_time <= te)
        ec_rows = db.execute(
            select(_time_trunc(ExternalComplaint.complaint_date), func.count().label("cnt"),
                   func.count(ExternalComplaint.matched_ticket_no)
                   .filter(ExternalComplaint.matched_ticket_no.in_(_sub_tk)).label("ov"))
            .where(*_ec_cond)
            .group_by("trunc_t")
        ).all()
        for r in ec_rows:
            tt = r._mapping["trunc_t"]
            if tt is None:
                continue
            idx = _slot_idx(tt)
            if idx is None:
                continue
            net = int(r.cnt or 0) - int(r.ov or 0)
            if net:
                cp_map[idx] = cp_map.get(idx, 0) + net
        # ⚠️ CardComplaint **不再并入**（#53 卡商独立，见上方说明）

    # ── 填充到槽位: 单日填当前小时之前, 多日填全部(最后一天可能实时) ──
    slots = list(range(n_slots))
    def _fill_list(*maps, kind='count'):
        """将多个map按slot合并成list, 返回各路线list + 合计list"""
        n_maps = len(maps)
        lists = [[] for _ in range(n_maps)]
        total_list = []
        for i in slots:
            if i > max_slot:
                for L in lists: L.append(None)
                total_list.append(None)
            else:
                total = 0
                for j, m in enumerate(maps):
                    v = m.get(i, 0)
                    lists[j].append(v if kind == 'count' else v)
                    total += v
                total_list.append(total if kind == 'count' else round(total, 2))
        return lists + [total_list]

    # 退款笔数: A/B/C/D 四路 + 合计
    cnt_lists = _fill_list(a_cnt_map, b_cnt_map, c_cnt_map, d_cnt_map, kind='count')
    r_cnt_a, r_cnt_b, r_cnt_c, r_cnt_d, r_cnt_total = cnt_lists
    # 退款金额: A/B/C/D 四路 + 合计(经营估算)
    amt_lists = _fill_list(a_amt_map, b_amt_map, c_amt_map, d_amt_map, kind='amount')
    r_amt_a, r_amt_b, r_amt_c, r_amt_d, r_amt_total = amt_lists
    # 实际现金流E (笔数+金额)
    e_cnt_list = [e_cnt_map.get(i, 0) if i <= max_slot else None for i in slots]
    e_amt_list = [round(e_amt_map.get(i, 0), 2) if i <= max_slot else None for i in slots]

    # 旧兼容字段 = 四路合计
    refund_count = r_cnt_total
    refund_amount = r_amt_total

    # 展示用日期 (单日 → date; 范围 → start date, 另附 date_end)
    if disp_range:
        d_iso = disp_range[0][:10]
        d_end_iso: str | None = disp_range[1][:10]
    else:
        d_iso = (now_sh.date() if date is None else _parse_date(date)).isoformat()
        d_end_iso = None

    slot_labels = [_slot_label(i) for i in slots]
    return {
        "date": d_iso,
        "date_end": d_end_iso,
        "grain": grain,
        "x_label": x_label,
        "hours": slots if grain == "hour" else None,   # 旧兼容
        "hour_labels": slot_labels,                     # 前端统一用 hour_labels
        "slot_labels": slot_labels,
        # === 工作量组 ===
        "reception":          [cs_map.get(i, 0)         if i <= max_slot else None for i in slots],
        "reception_devices":  [cs_dev_map.get(i, 0)     if i <= max_slot else None for i in slots],
        # === 款项组 - 退款笔数: 四路拆分 + 合计 ===
        "refund_count":       refund_count,      # 旧兼容 = 四路合计
        "refund_count_a":     r_cnt_a,           # 路线A 退费单线上直退
        "refund_count_b":     r_cnt_b,           # 路线B 钱包用户取现
        "refund_count_c":     r_cnt_c,           # 路线C 闪猫退款
        "refund_count_d":     r_cnt_d,           # 路线D 手动退款
        "refund_count_direct":  r_cnt_a,         # 向后兼容: direct = A
        "refund_count_withdraw": r_cnt_b,        # 向后兼容: withdraw = B
        "refund_count_total":  r_cnt_total,      # 四路合计 A+B+C+D
        # === 款项组 - 退款金额: 四路拆分 + 合计 + 实际现金流E ===
        "refund_amount":      refund_amount,     # 旧兼容 = 四路合计
        "refund_amount_a":    r_amt_a,           # 路线A 金额
        "refund_amount_b":    r_amt_b,           # 路线B 金额
        "refund_amount_c":    r_amt_c,           # 路线C 金额
        "refund_amount_d":    r_amt_d,           # 路线D 金额
        "refund_amount_e":    e_amt_list,        # 路线E 实际现金流金额
        "refund_amount_direct":  r_amt_a,        # 向后兼容
        "refund_amount_withdraw": r_amt_b,       # 向后兼容
        "refund_amount_total":  r_amt_total,     # 四路经营合计 A+B+C+D
        "cashflow_e_count":   e_cnt_list,        # 实际现金流E 笔数
        "cashflow_e_amount":  e_amt_list,        # 实际现金流E 金额
        # === 投诉组 ===
        "complaint":          [cp_map.get(i, 0)         if i <= max_slot else None for i in slots],
        # ★ #85: 投诉**处理**序列（柱=登记 + 线=处理，用户 2026-09-14 选定）
        #   口径: finish_time 落桶 且 真实完成(finish−register≥3min)，**不含卡商**，
        #   与 `complaints._source_reg_handled` 的 handled 同源。
        #   ⚠️ 「处理」含**历史单今日办结**（登记柱在别的桶、处理线在本桶）——
        #      这正是主管要知道的"今天实际清掉多少"，故按 finish 落桶而非登记日。
        "complaint_done":     [cp_done_map.get(i, 0)    if i <= max_slot else None for i in slots],
        "_spec": {
            "refund_count_a":  "路线A: 退费单 refund_method='线上' (线上直退)",
            "refund_count_b":  "路线B: 钱包提现 op_type='用户提现'",
            "refund_count_c":  "路线C: 闪猫退款 (shanmao_refunds status=已退款)",
            "refund_count_d":  "路线D: 手动退款 (manual_refunds)",
            "refund_count_total": "四路经营合计 A+B+C+D",
            "refund_amount_a": "路线A: 退费单线上直退金额",
            "refund_amount_b": "路线B: 钱包用户取现金额",
            "refund_amount_c": "路线C: 闪猫退款金额",
            "refund_amount_d": "路线D: 手动退款金额",
            "refund_amount_e": "路线E: 商户号实际现金流 (refund_record_reports 退款成功)",
            "refund_amount_total": "四路经营合计金额 A+B+C+D",
            "reception":     "会话数(次) = cs_record 计数；接待数(个) = 去重设备SN计数(去NULL/空串)",
            "groups":        "工作量: 会话/接待 · 款项: 退款笔数(四路堆叠)/退款金额(四路堆叠面积+实际现金流E线) · 投诉: 登记(柱) + 处理(线)",
            "grain":         f"时间粒度: {grain} (跨度{span_days}天 → 自动选择粒度)",
        },
    }

def today_reception_breakdown(db: Session, date: str | None = None, start: str | None = None, end: str | None = None) -> dict[str, Any]:
    """模块3: 今日接待分项 = 分类饼图 + Top10客服柱图 + 投诉子类饼图 + 投诉根因柱图.
    分类: 6 类 (投诉/退款/套餐问题/咨询/综合问题/其他问题)
    投诉相关基于 service_tickets (service_category 兜底=投诉), 根因通过 device_sn ±24h 关 cs_record 判断
    """
    ts, te, _, _, is_realtime, disp_range = _resolve_windows(date=date, start=start, end=end)
    if disp_range:
        d_iso = disp_range[0][:10]
        d_end_iso = disp_range[1][:10]
    else:
        d_iso = (_dt.datetime.now(TZ).date() if date is None else _parse_date(date)).isoformat()
        d_end_iso = None
    between = and_(CsRecord.operation_time.isnot(None),
                   CsRecord.operation_time >= ts, CsRecord.operation_time <= te)
    tk_between = and_(ServiceTicket.register_time.isnot(None),
                      ServiceTicket.register_time >= ts, ServiceTicket.register_time <= te)

    # 左1: 智能接待分类饼 (复用 _category_case)
    # 两个视角: 接待量(会话次数) vs 设备量(去重SN数)
    bucket = _category_case()
    cat_rows = db.execute(
        select(bucket, func.count().label("cnt"))
        .where(between)
        .group_by("category_bucket")
    ).all()
    cat_device_rows = db.execute(
        select(bucket, func.count(func.distinct(func.nullif(func.trim(CsRecord.device_sn), ""))).label("dcnt"))
        .where(between)
        .group_by("category_bucket")
    ).all()
    categories = []
    categories_device = []
    order = ["投诉", "退款", "套餐问题", "咨询", "综合问题", "其他问题"]
    # 接待量
    for r in cat_rows:
        cat = r.category_bucket if r.category_bucket in order else "其他问题"
        categories.append({"name": cat, "value": int(r.cnt or 0)})
    present = {c["name"] for c in categories}
    for nm in order:
        if nm not in present:
            categories.append({"name": nm, "value": 0})
    # 设备量 (独立设备SN去重)
    for r in cat_device_rows:
        cat = r.category_bucket if r.category_bucket in order else "其他问题"
        categories_device.append({"name": cat, "value": int(r.dcnt or 0)})
    present_d = {c["name"] for c in categories_device}
    for nm in order:
        if nm not in present_d:
            categories_device.append({"name": nm, "value": 0})

    # 右1: Top10客服 (按operator分组count, 关联PersonAlias→Person拿group_name/display_name)
    #     2026-08-02 加设备SN去重计数: 设备接待台数, 体现覆盖面, 1台设备多次进线算1台
    op_person = _operator_to_person_cte()
    top_rows = db.execute(
        select(
            CsRecord.operator.label("raw_op"),
            op_person.c.display_name.label("display_name"),
            op_person.c.department.label("department"),
            op_person.c.group_name.label("group_name"),
            func.count().label("cnt"),
            func.count(func.distinct(func.nullif(func.trim(CsRecord.device_sn), ""))).label("dcnt"),
        )
        .outerjoin(op_person, CsRecord.operator == op_person.c.operator)
        .where(between, CsRecord.operator.isnot(None))
        .group_by("raw_op", op_person.c.display_name, op_person.c.department, op_person.c.group_name)
        .order_by(func.count().desc())
        .limit(10)
    ).all()
    top_staff = []
    for r in top_rows:
        raw_name = r.display_name or r.raw_op or "未知"
        top_staff.append({
            "name": _strip_qw_prefix(raw_name) or raw_name or "未知",
            "department": r.department or "未设置",
            "group": r.group_name or "未分组",
            "count": int(r.cnt or 0),
            "device_count": int(r.dcnt or 0),
        })

    # ---------- 新增: 今日投诉统计 (口径=三表 UNION: service_tickets + external_complaints + card_complaints) ----------
    # 左2: 投诉子类饼图 (从 service_item 归一化, 仅统计今日投诉工单)
    # 同时拿到每个子类的来源类型(内部/外部) 方便前端分组着色与拆分展示
    comp_sub_rows = db.execute(
        select(_complaint_subitem_case(),
               _complaint_source_type_case(),
               func.count().label("cnt"))
        .where(tk_between, _ticket_category_case() == "投诉")
        # ⚠️ 标签名随 complaint_scope 变更为 'complaint_bucket'; SQLAlchemy 2.0 要求
        #    text() 显式包裹(不能用裸字符串引用 label)。
        .group_by("complaint_sub", text("complaint_bucket"))
        .order_by(text("cnt DESC"))
    ).all()
    complaint_subs = [
        {"name": r[0] or "未标注",
         "source_type": r[1] or BUCKET_INTERNAL,
         "value": int(r[2] or 0)}
        for r in comp_sub_rows
    ]
    # 叠加 external_complaints 今日 (按 complaint_category 聚合,未分类则归入 "外部投诉(监督局/315等)")
    # 2026-08-14: _INCLUDE_COMPLAINT_FILES=False 时跳过, ec_between=literal(False) 使下游查询返回空
    if _INCLUDE_COMPLAINT_FILES:
        ec_between = and_(ExternalComplaint.complaint_date.isnot(None),
                          ExternalComplaint.complaint_date >= ts, ExternalComplaint.complaint_date <= te)
        ec_rows = db.execute(
            select(func.coalesce(ExternalComplaint.complaint_category, "外部投诉(监督局/315等)"),
                   func.count().label("cnt"))
            .where(ec_between)
            .group_by(func.coalesce(ExternalComplaint.complaint_category, "外部投诉(监督局/315等)"))
            .order_by(text("cnt DESC"))
        ).all()
        for r in ec_rows:
            complaint_subs.append({
                "name": str(r[0] or "外部投诉(监督局/315等)"),
                "source_type": "外部投诉",
                "value": int(r[1] or 0),
            })
        # 叠加 card_complaints 今日 (按 problem_category 聚合, 未分类归入 "卡商群投诉")
        cc_between = and_(CardComplaint.complaint_date.isnot(None),
                          CardComplaint.complaint_date >= ts, CardComplaint.complaint_date <= te)
        cc_rows = db.execute(
            select(func.coalesce(CardComplaint.problem_category, BUCKET_CARD),
                   func.count().label("cnt"))
            .where(cc_between)
            .group_by(func.coalesce(CardComplaint.problem_category, BUCKET_CARD))
            .order_by(text("cnt DESC"))
        ).all()
        for r in cc_rows:
            complaint_subs.append({
                "name": str(r[0] or BUCKET_CARD),
                "source_type": BUCKET_CARD,
                "value": int(r[1] or 0),
            })
    else:
        ec_between = literal(False)
        cc_between = literal(False)
    # 为避免重复计算,直接重算 total = 工单投诉 + 315补充表
    # ★★ 2026-09-11 (#53) 卡商剥离 ★★
    #   `complaint_total` **不再含卡商** —— 用户: "卡商的投诉 不纳入总量的管理,
    #   整体独立统计分析; 无论是界面上 还是各项指标的分析 统计 接口 业务逻辑 等等
    #   全部单算"。
    #   ⚠️ 卡商不进 `complaint_total` —— 这个是**用户口径**, 不受 #65 影响。
    _cnt_st = db.execute(select(func.count()).where(tk_between, _ticket_category_case() == "投诉")).scalar() or 0
    _cnt_ec = db.execute(select(func.count()).where(ec_between)).scalar() or 0
    _cnt_cc = db.execute(select(func.count()).where(cc_between)).scalar() or 0
    complaint_total = int(_cnt_st) + int(_cnt_ec)          # ← 不含卡商
    # ★★ #65: 卡商 = 工单侧真卡商 + 补充表 − 重叠 ★★
    #   ⚠️ #53 原公式为 `card_total = _cnt_cc`(纯补充表行数)。
    #      #57 证明「工单侧无卡商分类」的检测方式有误 —— 卡商在工单侧以
    #      `service_item='工信部投诉'` 形式存在。现有 28 条/近3月, 与补充表
    #      SN 重叠 16 条 → 不去重会双计。公式与外部侧平行。
    #   ⚠️ 与 `/summary` 的 `card.total` 保持**同一公式**(避免看板/决策页不一致)。
    _cnt_card_tk = db.execute(
        select(func.count()).where(tk_between, _ticket_category_case() == "投诉",
                                   _complaint_source_type_case() == BUCKET_CARD)
    ).scalar() or 0
    _ov_card_cc = db.execute(
        select(func.count()).select_from(CardComplaint)
        .where(cc_between, CardComplaint.matched_ticket_no.isnot(None),
               CardComplaint.matched_ticket_no.in_(
                   select(ServiceTicket.ticket_no).where(
                       tk_between, _ticket_category_case() == "投诉",
                       _complaint_source_type_case() == BUCKET_CARD,
                   )
               ))
    ).scalar() or 0
    card_total = int(_cnt_card_tk) + int(_cnt_cc) - int(_ov_card_cc)   # ← 卡商独立总量(并集)
    # 投诉来源类型聚合: 内部/商户号/外部 **三类**(卡商不在此列)
    # 2026-09-04 用户确认: 商户号投诉(微信)不得并入外部桶, 单列展示。
    # 原两桶口径 _complaint_source_type_simple_case(微信归外部) 已弃用。
    # ⚠️ #65: `_complaint_source_type_case` 现已含 **#58 软纠正** → 本聚合
    #    自动把「误录为工信部、实为监所」的工单归入外部桶。
    comp_src_rows = db.execute(
        select(_complaint_source_type_case(), func.count().label("cnt"))
        .where(tk_between, _ticket_category_case() == "投诉")
        # ⚠️ 用 text() 显式包裹标签: 标签名随 complaint_scope 变更为
        #    'complaint_bucket' (原 'complaint_source_type'),
        #    SQLAlchemy 2.0 不允许裸字符串引用 label。
        .group_by(text("complaint_bucket"))
        .order_by(text("cnt DESC"))
    ).all()
    complaint_by_source = [
        {"name": r[0] or BUCKET_INTERNAL, "value": int(r[1] or 0)}
        for r in comp_src_rows
    ]
    # 315 补充表(external_complaints) → 归入「外部投诉」桶
    if _cnt_ec > 0:
        found_outer = False
        for x in complaint_by_source:
            if x["name"] == BUCKET_EXTERNAL:
                x["value"] += int(_cnt_ec); found_outer = True; break
        if not found_outer:
            complaint_by_source.append({"name": BUCKET_EXTERNAL, "value": int(_cnt_ec)})
    # ★★ (#53) 卡商**已从来源桶移除** ★★
    #   用户: "(外)卡商 的投诉 不纳入总量的管理, 整体独立统计分析…全部单算"。
    #   卡商改为独立字段 `card_*` 返回(见下方 card_source / card_total),
    #   **不再出现在 complaint_by_source**。旧 #47 的"单列成第4桶"口径已废弃。
    # 补齐三类桶,缺失补0 (前端颜色固定)
    _src_present = {x["name"] for x in complaint_by_source}
    for nm in ALL_BUCKETS:
        if nm not in _src_present:
            complaint_by_source.append({"name": nm, "value": 0})
    complaint_by_source.sort(key=lambda x: -x["value"])

    # ── (#53) 卡商独立统计体系(不参与投诉总量) ──
    #   口径 = 表自身行数(按 complaint_date 窗口), **不扣工单重叠**
    #   (卡商在工单侧无独立分类, 无重叠可言 —— 实证 item/category 含"卡商"均 0 行)
    card_by_category: list[dict] = []
    if _cnt_cc > 0:
        _cc_rows = db.execute(
            select(func.coalesce(CardComplaint.problem_category, CARD_SCOPE),
                   func.count().label("cnt"))
            .where(cc_between)
            .group_by(func.coalesce(CardComplaint.problem_category, CARD_SCOPE))
            .order_by(text("cnt DESC"))
        ).all()
        card_by_category = [
            {"name": str(r[0] or CARD_SCOPE), "value": int(r[1] or 0)}
            for r in _cc_rows
        ]

    # 右2: 投诉根因柱图 (口径严格 = complaint_total: service_tickets投诉 + external_complaints + card_complaints 全部覆盖)
    #
    # 分层统计策略:
    #   A. service_tickets投诉工单:
    #        A1. 能匹配到 cs_record ±24h → 用 cs_record 的 problem_category/type 精确归类 (原有6类)
    #        A2. 未匹配 cs_record → 基于 service_item/service_category 文本关键词粗略归类, 否则"待标注"
    #   B. external_complaints (外部投诉: 315/监督局等): 基于 complaint_category/platform/keyword 归类,
    #        否则归入"外部投诉平台受理类"(不虚构根因, 仅标识来自外部平台)
    #   C. card_complaints (卡商群投诉): 基于 problem_category/problem 文本关键词归类,
    #        否则归入"卡商群待标注"
    #
    # 最终保证: SUM(complaint_roots[*].count) == complaint_total, complaint_unmatched 仅作为覆盖率提示

    # ---------- A层: service_tickets 投诉工单根因 ----------
    st_root_sql = text(
        """
        WITH comp_tickets AS (
            SELECT
                st.ticket_no,
                st.service_item,
                st.service_category,
                st.device_sn,
                st.register_time
            FROM service_tickets st
            WHERE st.register_time IS NOT NULL
              AND st.register_time >= :ts
              AND st.register_time <= :te
              AND (
                st.service_category IS NULL
                OR st.service_category ILIKE '%投诉%'
                OR st.service_item ILIKE ANY (ARRAY[
                    '%12315%', '%工信部%', '%消协%', '%公共服务平台%', '%消费者协会%',
                    '%监督局%', '%市场监管%', '%市监%', '%315平台%', '%12345%', '%政务平台%',
                    '%商品投诉%', '%服务投诉%', '%微信投诉%'
                ])
              )
        ),
        nearest_cs AS (
            SELECT DISTINCT ON (ct.ticket_no)
                ct.ticket_no,
                ct.service_item,
                ct.service_category,
                c.problem_category,
                c.problem_type,
                -- 是否匹配到 cs_record: 1=匹配, 0=未匹配
                CASE WHEN c.problem_category IS NOT NULL THEN 1 ELSE 0 END AS has_match
            FROM comp_tickets ct
            LEFT JOIN cs_records c
              ON ct.device_sn = c.device_sn
              AND c.operation_time IS NOT NULL
              AND c.operation_time BETWEEN (ct.register_time - INTERVAL '24 hours')
                                       AND (ct.register_time + INTERVAL '24 hours')
            ORDER BY ct.ticket_no,
                     CASE WHEN c.problem_category IS NULL THEN 1 ELSE 0 END,
                     ABS(EXTRACT(EPOCH FROM (c.operation_time - ct.register_time))) ASC
        )
        SELECT
            CASE
                -- ====== A1: 已匹配 cs_record → 用 cs_record 的精确归类 ======
                WHEN has_match = 1 AND (
                    COALESCE(problem_category, '') = '网络问题'
                    OR COALESCE(problem_type, '') ~* '(网|卡|信号|离线|WiFi|激活网卡)'
                ) THEN '网络质量类'
                WHEN has_match = 1 AND (
                    COALESCE(problem_category, '') = '套餐问题'
                    OR COALESCE(problem_type, '') ~* '(套餐|涨价|流量统计|360天|包月|加油包|返现|预存|切网)'
                ) THEN '套餐争议类'
                WHEN has_match = 1 AND COALESCE(problem_category, '') = '退款'
                THEN '退款协商失败类'
                WHEN has_match = 1 AND (
                    COALESCE(problem_category, '') = '降成问题'
                    OR COALESCE(problem_type, '') ~* '(限速|降成|钻石VIP|VIP|赠送)'
                ) THEN '降成争议类'
                WHEN has_match = 1 AND (
                    COALESCE(problem_category, '') IN ('其他问题', '激活实名')
                    OR COALESCE(problem_type, '') ~* '(注销|发票|退换货)'
                ) THEN '其它(注销/发票/退换)'

                -- ====== A2: 未匹配 cs_record 或 用cs_record仍落入待标注 → 用 service_item 文本关键词粗略归类 ======
                WHEN COALESCE(service_item, '') ~* '(网|卡|信号|离线|WiFi|无网络|网速|断网|网卡)'
                THEN '网络质量类'
                WHEN COALESCE(service_item, '') ~* '(套餐|涨价|流量统计|360天|包月|加油包|返现|预存|切网)'
                THEN '套餐争议类'
                WHEN COALESCE(service_item, '') ~* '(退款|退费|退钱|不予退款|拒绝退款)'
                THEN '退款协商失败类'
                WHEN COALESCE(service_item, '') ~* '(限速|降成|钻石VIP|VIP|赠送)'
                THEN '降成争议类'
                WHEN COALESCE(service_item, '') ~* '(注销|发票|退换货|退货|换货)'
                THEN '其它(注销/发票/退换)'

                -- ═══════════════════════════════════════════════════════════
                -- 2026-08-13 新增: A3 基于 service_item "投诉大类名" 直接归位
                --   现实数据 80%+ service_item 就是投诉子类(不带具体问题),
                --   之前全部落入 待标注 导致饼图"今日投诉 6条 待标注100%"
                -- ═══════════════════════════════════════════════════════════
                -- A3-1: 商品投诉 → 商品质量/售后类 (实物投诉)
                WHEN COALESCE(service_item, '') ~* '商品投诉'
                THEN '商品质量/售后类'
                -- A3-2: 服务投诉 → 服务态度/时效类 (非商品性投诉, 客服/流程/时效问题)
                WHEN COALESCE(service_item, '') ~* '服务投诉'
                THEN '服务态度/时效类'
                -- A3-3: 微信投诉(商户号投诉,独立展示单独桶)
                WHEN COALESCE(service_item, '') ~* '微信投诉'
                THEN '微信商户号投诉类'
                -- A3-4: 外部平台名(12315/工信部/消协/公共服务/市监/政务) → 外部平台投诉受理类
                --       注意: 这里不重复 外部投诉(ExternalComplaint)表, 这些工单是 service_tickets
                --       中通过 "投诉子类" 字段直接录入"全国12315平台"等名称的记录
                WHEN COALESCE(service_item, '') ~* '(12315|工信部|消协|消费者协会|公共服务|监督局|市场监管|市监|12345|政务平台)'
                THEN '外部平台投诉受理类'

                -- 真的啥都匹配不到 → 待标注(剩下 <5% 预留人工标注接口)
                ELSE '待标注'
            END AS root_cause,
            COUNT(*) AS cnt,
            SUM(has_match) AS matched_cnt  -- 有多少条是通过 cs_record 精确匹配的 (用于计算覆盖率)
        FROM nearest_cs
        GROUP BY 1
        ORDER BY cnt DESC;
        """
    )
    st_root_rows = db.execute(st_root_sql, {"ts": ts, "te": te}).fetchall()
    complaint_roots_map: dict[str, int] = {}
    st_matched_count = 0
    for r in st_root_rows:
        nm = str(r[0]); cnt = int(r[1] or 0); mcnt = int(r[2] or 0)
        complaint_roots_map[nm] = complaint_roots_map.get(nm, 0) + cnt
        st_matched_count += mcnt

    # ---------- B层: external_complaints 外部投诉(平台受理) ----------
    def _ec_root_cause(complaint_category: str | None, platform: str | None, keyword: str | None) -> str:
        """external_complaint 根因归类 (基于文本关键词, 不虚构根因)"""
        blob = " ".join(x or "" for x in [complaint_category or "", platform or "", keyword or ""])
        b = blob.lower()
        net_kw = ("网", "卡", "信号", "离线", "wifi", "无网络", "网速", "断网", "网卡")
        pkg_kw = ("套餐", "涨价", "流量统计", "360天", "包月", "加油包", "返现", "预存", "切网")
        ref_kw = ("退款", "退费", "退钱", "不予退款", "拒绝退款")
        down_kw = ("限速", "降成", "钻石vip", "vip", "赠送")
        oth_kw = ("注销", "发票", "退换货", "退货", "换货")
        if any(k in b for k in net_kw): return "网络质量类"
        if any(k in b for k in pkg_kw): return "套餐争议类"
        if any(k in b for k in ref_kw): return "退款协商失败类"
        if any(k in b for k in down_kw): return "降成争议类"
        if any(k in b for k in oth_kw): return "其它(注销/发票/退换)"
        # 无可用关键词 → 归入"外部平台投诉受理类"(独立桶, 保持6+1类)
        return "外部平台投诉受理类"

    ec_list_rows = db.execute(
        select(ExternalComplaint.complaint_category,
               ExternalComplaint.platform,
               ExternalComplaint.keyword)
        .where(ec_between)
    ).all()
    for r in ec_list_rows:
        rc = _ec_root_cause(r[0], r[1], r[2])
        complaint_roots_map[rc] = complaint_roots_map.get(rc, 0) + 1

    # ---------- C层: card_complaints 卡商群投诉 ----------
    def _cc_root_cause(problem_category: str | None, problem: str | None) -> str:
        """card_complaint 根因归类 (基于文本关键词)"""
        blob = " ".join(x or "" for x in [problem_category or "", problem or ""])
        b = blob.lower()
        net_kw = ("网", "卡", "信号", "离线", "wifi", "无网络", "网速", "断网", "网卡")
        pkg_kw = ("套餐", "涨价", "流量统计", "360天", "包月", "加油包", "返现", "预存", "切网")
        ref_kw = ("退款", "退费", "退钱", "不予退款", "拒绝退款")
        down_kw = ("限速", "降成", "钻石vip", "vip", "赠送")
        oth_kw = ("注销", "发票", "退换货", "退货", "换货")
        if any(k in b for k in net_kw): return "网络质量类"
        if any(k in b for k in pkg_kw): return "套餐争议类"
        if any(k in b for k in ref_kw): return "退款协商失败类"
        if any(k in b for k in down_kw): return "降成争议类"
        if any(k in b for k in oth_kw): return "其它(注销/发票/退换)"
        return "卡商群待标注类"

    cc_list_rows = db.execute(
        select(CardComplaint.problem_category,
               CardComplaint.problem)
        .where(cc_between)
    ).all()
    for r in cc_list_rows:
        rc = _cc_root_cause(r[0], r[1])
        complaint_roots_map[rc] = complaint_roots_map.get(rc, 0) + 1

    # ---------- 汇总根因 ----------
    complaint_roots = [{"name": k, "count": v} for k, v in complaint_roots_map.items()]
    # 所有根因的总数 = complaint_total (保证柱图总数与投诉总数匹配)
    sum(r["count"] for r in complaint_roots)
    # 补齐标准根因桶 (6 标准 + 2 扩展 + A3新增3桶 + 待标注) 缺失补 0
    #   新增: 商品质量/售后类 / 服务态度/时效类 / 微信商户号投诉类 — 来自 service_tickets.service_item 大类名
    root_order = [
        "网络质量类", "套餐争议类", "退款协商失败类",
        "降成争议类", "其它(注销/发票/退换)",
        "商品质量/售后类", "服务态度/时效类", "微信商户号投诉类",
        "外部平台投诉受理类", "卡商群待标注类", "待标注",
    ]
    present_root = {r["name"] for r in complaint_roots}
    for nm in root_order:
        if nm not in present_root:
            complaint_roots.append({"name": nm, "count": 0})
    complaint_roots.sort(key=lambda x: -x["count"])

    # 覆盖率统计: service_tickets 中有多少条精确匹配到了 cs_record
    # complaint_unmatched 含义调整为: 未精确匹配 cs_record 的数量 (其余用文本归类或扩展桶)
    # 这样 SUM(roots.count) == complaint_total 严格成立, unmatched 仅作质量提示
    complaint_unmatched = max(0, _cnt_st - st_matched_count)

    # ═══════════════════════════════════════════════════════════
    # 2026-08-13 新增: 投诉处理人 × 处理状态 统计 (三张投诉表合并)
    #   - 处理人来源: service_tickets.handler / ExternalComplaint.handler / CardComplaint.handler
    #   - 统一归一化: "企微-洪佳" → "洪佳"，空值 → "未分配"
    #   - 状态口径: 3表统一映射为 [待处理/处理中/已完成/已终止/未标注] 5 桶
    # ═══════════════════════════════════════════════════════════
    _handler_sql = text("""
        WITH st_h AS (
            SELECT
                COALESCE(NULLIF(TRIM(st.handler), ''), '未分配') AS raw_handler,
                st.status AS raw_status,
                '内部投诉'::text AS source_type,
                COUNT(*)::int AS cnt
            FROM service_tickets st
            WHERE st.register_time IS NOT NULL
              AND st.register_time >= :ts AND st.register_time <= :te
              AND (
                st.service_category IS NULL
                OR st.service_category ILIKE '%投诉%'
                OR st.service_item ILIKE ANY (ARRAY[
                    '%12315%', '%工信部%', '%消协%', '%公共服务平台%', '%消费者协会%',
                    '%监督局%', '%市场监管%', '%市监%', '%315平台%', '%12345%', '%政务平台%',
                    '%商品投诉%', '%服务投诉%', '%微信投诉%'
                ])
              )
            GROUP BY 1, 2, 3
        ),
        ec_h AS (
            SELECT
                -- external_complaints 处理人: follower(跟进人)
                COALESCE(NULLIF(TRIM(ec.follower), ''), '未分配') AS raw_handler,
                -- 没有 status 列, 直接基于 result + handle_time 判断状态
                CASE
                    WHEN COALESCE(ec.result, '') ILIKE '%有效%' THEN '已完成'
                    WHEN COALESCE(ec.result, '') ILIKE '%无效%' THEN '已终止'
                    WHEN COALESCE(ec.result, '') ILIKE '%处理%' THEN '处理中'
                    WHEN ec.handle_time IS NOT NULL THEN '已完成'
                    WHEN COALESCE(ec.handle_detail, '') <> '' THEN '处理中'
                    ELSE '未标注'
                END AS raw_status,
                '外部投诉'::text AS source_type,
                COUNT(*)::int AS cnt
            FROM external_complaints ec
            WHERE ec.complaint_date IS NOT NULL
              AND ec.complaint_date >= :ts AND ec.complaint_date <= :te
            GROUP BY 1, 2, 3
        ),
        cc_h AS (
            SELECT
                COALESCE(NULLIF(TRIM(cc.handler), ''), '未分配') AS raw_handler,
                -- card_complaints 没有 status 列, 直接基于 result + handle_result 判断
                CASE
                    WHEN COALESCE(cc.result, '') ILIKE '%已处理%' THEN '已完成'
                    WHEN COALESCE(cc.result, '') ILIKE '%未处理%' THEN '待处理'
                    WHEN COALESCE(cc.result, '') ILIKE '%处理%' THEN '处理中'
                    WHEN COALESCE(cc.handle_result, '') <> '' THEN '已完成'
                    WHEN COALESCE(cc.solution, '') <> '' THEN '处理中'
                    ELSE '未标注'
                END AS raw_status,
                '卡商群投诉'::text AS source_type,
                COUNT(*)::int AS cnt
            FROM card_complaints cc
            WHERE cc.complaint_date IS NOT NULL
              AND cc.complaint_date >= :ts AND cc.complaint_date <= :te
            GROUP BY 1, 2, 3
        ),
        all_h AS (
            SELECT raw_handler, raw_status, source_type, cnt FROM st_h
            UNION ALL
            SELECT raw_handler, raw_status, source_type, cnt FROM ec_h
            UNION ALL
            SELECT raw_handler, raw_status, source_type, cnt FROM cc_h
        )
        SELECT raw_handler, raw_status, source_type, SUM(cnt)::int AS cnt
        FROM all_h
        GROUP BY 1, 2, 3
        ORDER BY SUM(cnt) DESC;
    """)
    handler_rows = db.execute(_handler_sql, {"ts": ts, "te": te}).mappings().all()

    # 聚合: {handler: {total: N, by_status: {st: N}, by_source: {src: N}}}
    handlers_acc: dict[str, dict] = {}
    for hr in handler_rows:
        raw = str(hr["raw_handler"] or "")
        # 去除 "企微-" 前缀 (企微-洪佳 → 洪佳), 再首尾 strip
        cleaned = raw.removeprefix("企微-").removeprefix("企微").strip() if raw else ""
        handler = cleaned or raw or "未分配"
        if not handler:
            handler = "未分配"
        raw_st = str(hr["raw_status"] or "未标注")
        # 状态口径统一映射: 待处理/处理中/已完成/已终止/未标注
        rs = raw_st
        if any(k in rs for k in ["待处理", "待接单", "待派", "未处理", "new", "open"]):
            status_bucket = "待处理"
        elif any(k in rs for k in ["处理中", "进行中", "处理", "running", "dispatch", "已派"]):
            status_bucket = "处理中"
        elif any(k in rs for k in ["已完成", "完成", "办结", "关闭", "closed", "done", "finish", "有效", "已处理"]):
            status_bucket = "已完成"
        elif any(k in rs for k in ["终止", "取消", "取消处理", "无效", "拒绝", "reject"]):
            status_bucket = "已终止"
        else:
            status_bucket = "未标注"
        src_bucket = str(hr["source_type"] or "未标注")
        cnt = int(hr["cnt"] or 0)
        acc = handlers_acc.setdefault(handler, {
            "total": 0,
            "by_status": {"待处理": 0, "处理中": 0, "已完成": 0, "已终止": 0, "未标注": 0},
            # ⚠️ 2026-09-10 (#47): by_source 的键为**统计分类桶**常量, 不再用字面"卡商群投诉"
            #    ("卡商群投诉"是腾讯文档 Sheet 名/数据源名, 与统计分类是两回事)
            "by_source": {BUCKET_INTERNAL: 0, BUCKET_EXTERNAL: 0, BUCKET_CARD: 0},
        })
        acc["total"] += cnt
        acc["by_status"][status_bucket] = acc["by_status"].get(status_bucket, 0) + cnt
        if src_bucket in (BUCKET_INTERNAL, BUCKET_EXTERNAL, BUCKET_CARD):
            acc["by_source"][src_bucket] = acc["by_source"].get(src_bucket, 0) + cnt
        elif src_bucket == "卡商群投诉":  # 兼容旧值(未归一的上游数据)
            acc["by_source"][BUCKET_CARD] = acc["by_source"].get(BUCKET_CARD, 0) + cnt
        else:
            acc["by_source"]["未标注"] = acc["by_source"].get("未标注", 0) + cnt

    # 取 Top 15 处理人 (按 total 倒序), "未分配" 固定排首位 if 存在
    complaint_handlers = []
    if handlers_acc.get("未分配"):
        unallocated = handlers_acc.pop("未分配")
        complaint_handlers.append({"handler": "未分配", **unallocated})
    sorted_names = sorted(handlers_acc.keys(), key=lambda n: -handlers_acc[n]["total"])[:15]
    for nm in sorted_names:
        complaint_handlers.append({"handler": nm, **handlers_acc[nm]})

    # 同时汇总: 3 表合并后的投诉处理状态分布(与饼图对齐)
    status_agg: dict[str, int] = {"待处理": 0, "处理中": 0, "已完成": 0, "已终止": 0, "未标注": 0}
    for ch in complaint_handlers:
        for st, c in ch["by_status"].items():
            status_agg[st] = status_agg.get(st, 0) + int(c or 0)
    complaint_status_all = [{"name": k, "value": v} for k, v in status_agg.items() if v]

    # ═══════════════════════════════════════════════════════════
    # 2026-08-14: 拆分内部/外部投诉处理人排行
    #   - 内部投诉处理人: source_type == '内部投诉' (仅 service_tickets)
    #   - 外部投诉处理人: source_type == '外部投诉' + '卡商群投诉'
    #   - 因数量级差异较大，分开展示更清晰
    #
    # ⚠️⚠️ 2026-09-10 (#47) 变更: 卡商**从外部拆出成独立表** ⚠️⚠️
    #   用户要求"(外)卡商 这个分类; 在各个统计口 都单列出来"。
    #   旧口径(2026-08-14): external = '外部投诉' + '卡商群投诉'(合并)。
    #   新口径: 三张表并列 —— 内部投诉 / 外部投诉 / (外)卡商。
    #   ⚠️ 本变更与原 LOCKED A-20260902-016 §问题3 冲突
    #      (其明文规定 `complaint_external_handlers = 外部投诉 + 卡商群投诉`),
    #      需走人工审核修订该 LOCKED 文档(见 docs/arch/_pending/A-20260910-005)。
    #      `complaint_external_handlers` 字段**保留**(向后兼容), 但语义已**不含卡商**;
    #      新增 `complaint_card_handlers` 承载卡商。
    # ═══════════════════════════════════════════════════════════
    internal_handlers_acc: dict[str, dict] = {}
    external_handlers_acc: dict[str, dict] = {}
    card_handlers_acc: dict[str, dict] = {}
    for hr in handler_rows:
        raw = str(hr["raw_handler"] or "")
        cleaned = raw.removeprefix("企微-").removeprefix("企微").strip() if raw else ""
        handler = cleaned or raw or "未分配"
        if not handler:
            handler = "未分配"
        raw_st = str(hr["raw_status"] or "未标注")
        rs = raw_st
        if any(k in rs for k in ["待处理", "待接单", "待派", "未处理", "new", "open"]):
            status_bucket = "待处理"
        elif any(k in rs for k in ["处理中", "进行中", "处理", "running", "dispatch", "已派"]):
            status_bucket = "处理中"
        elif any(k in rs for k in ["已完成", "完成", "办结", "关闭", "closed", "done", "finish", "有效", "已处理"]):
            status_bucket = "已完成"
        elif any(k in rs for k in ["终止", "取消", "取消处理", "无效", "拒绝", "reject"]):
            status_bucket = "已终止"
        else:
            status_bucket = "未标注"
        src_bucket = str(hr["source_type"] or "未标注")
        cnt = int(hr["cnt"] or 0)
        if src_bucket == BUCKET_INTERNAL:
            acc = internal_handlers_acc.setdefault(handler, {
                "total": 0,
                "by_status": {"待处理": 0, "处理中": 0, "已完成": 0, "已终止": 0, "未标注": 0},
            })
        elif src_bucket == BUCKET_CARD or src_bucket == "卡商群投诉":
            # 卡商独立表(#47): 兼容旧值 '卡商群投诉' 与新增 BUCKET_CARD('(外)卡商')
            acc = card_handlers_acc.setdefault(handler, {
                "total": 0,
                "by_status": {"待处理": 0, "处理中": 0, "已完成": 0, "已终止": 0, "未标注": 0},
            })
        else:
            acc = external_handlers_acc.setdefault(handler, {
                "total": 0,
                "by_status": {"待处理": 0, "处理中": 0, "已完成": 0, "已终止": 0, "未标注": 0},
            })
        acc["total"] += cnt
        acc["by_status"][status_bucket] = acc["by_status"].get(status_bucket, 0) + cnt

    def _build_handler_list(acc_dict, top_n=15):
        result = []
        if acc_dict.get("未分配"):
            unallocated = acc_dict.pop("未分配")
            result.append({"handler": "未分配", **unallocated})
        sorted_names = sorted(acc_dict.keys(), key=lambda n: -acc_dict[n]["total"])[:top_n]
        for nm in sorted_names:
            result.append({"handler": nm, **acc_dict[nm]})
        return result

    complaint_internal_handlers = _build_handler_list(internal_handlers_acc)
    # ⚠️ 语义变更(#47): 不再含卡商。卡商见下方 complaint_card_handlers。
    complaint_external_handlers = _build_handler_list(external_handlers_acc)
    # 卡商处理人排行（新增, #47 卡商单列）
    complaint_card_handlers = _build_handler_list(card_handlers_acc)

    # ═══════════════════════════════════════════════════════════
    # 2026-08-13 新增: 近期未处理投诉列表
    #   - 覆盖口径: 三张投诉表 (service_tickets 投诉类 + external_complaints + card_complaints)
    #   - 待处理定义: status_bucket ∉ {已完成, 已终止}  (即 待处理/处理中/未标注 任一都算"未闭环")
    #   - 有效信息列: 来源/编号/投诉时间/投诉人/SN/投诉标题或内容/状态/处理人
    #   - 跨度限制: 仅当 窗口≤31 天才返回列表(用户反馈"跨度太长意义不大"),
    #       超过 31 天 → 返回 [] + skipped=true + 提示文本,避免前端被成千上万条淹没
    # ═══════════════════════════════════════════════════════════
    span_days = (te - ts).days + 1
    PENDING_LIMIT_DAYS = 31
    PENDING_LIMIT_ROWS = 200

    def _to_status_bucket(raw_st: str | None) -> str:
        rs = raw_st or ""
        if any(k in rs for k in ["待处理", "待接单", "待派", "未处理", "new", "open"]):
            return "待处理"
        if any(k in rs for k in ["处理中", "进行中", "处理", "running", "dispatch", "已派"]):
            return "处理中"
        if any(k in rs for k in ["已完成", "完成", "办结", "关闭", "closed", "done", "finish", "有效", "已处理"]):
            return "已完成"
        if any(k in rs for k in ["终止", "取消", "取消处理", "无效", "拒绝", "reject"]):
            return "已终止"
        return "未标注"

    def _clean_handler(raw: str | None) -> str:
        if not raw:
            return "未分配"
        return (raw.removeprefix("企微-").removeprefix("企微").strip()) or raw or "未分配"

    complaint_pending: list[dict] = []
    pending_skipped = False
    pending_skip_reason = ""
    # ═══════════════════════════════════════════════════════════
    # 倒查窗口(创建时间范围): 不管视图选的是哪一天/哪一周,
    # 投诉待处理列表统一取 "视图终点 向前 7 天内 创建的 + 到现在仍未闭环"
    #   例如: 视图=08-13单日  →  创建窗口=[08-06, 08-13] (倒查7天,含今天)
    #        视图=08-01~08-07周 → 创建窗口=[min(08-01, 07-31), 08-07] = [07-31, 08-07] (也附带7天回查)
    #   这样即使选 "今日" 视图, 也能看到 前 6 天内积压的未闭环投诉 (否则只看当天创建的会漏掉老积压)
    # ═══════════════════════════════════════════════════════════
    PENDING_LOOKBACK_DAYS = 7
    ts_pending = min(ts, te - timedelta(days=PENDING_LOOKBACK_DAYS - 1))  # 创建窗口起点(含今天,所以-1)
    te_pending = te  # 创建窗口终点 = 视图终点(实时窗口 = now)

    if span_days > PENDING_LIMIT_DAYS:
        pending_skipped = True
        pending_skip_reason = (
            f"当前查询跨度 {span_days} 天, 超过 {PENDING_LIMIT_DAYS} 天阈值;"
            " 未处理列表仅在日/周视图下提供, 请缩短时间范围后查看。"
        )
    else:
        # ── A. service_tickets 投诉类工单 (未闭环) ──
        # 注意: 创建时间用 ts_pending / te_pending(倒查7天窗口), 而不是视图原始窗口
        st_kws = _EXTERNAL_ITEM_COMPLAINT_KW + ("商品投诉", "服务投诉", "微信投诉")
        st_where = and_(
            ServiceTicket.register_time.isnot(None),
            ServiceTicket.register_time >= ts_pending,
            ServiceTicket.register_time <= te_pending,
            or_(
                ServiceTicket.service_category.is_(None),
                ServiceTicket.service_category.ilike("%投诉%"),
                or_(*[ServiceTicket.service_item.ilike(f"%{k}%") for k in st_kws]),
            ),
        )
        # ═══════════════════════════════════════════════════════════
        # ⚠ 2026-08-13 根因修复:
        # 旧逻辑: SQL 层 ORDER BY ASC LIMIT 201 → Python 层 bucket 过滤 → 只取到了最早 201 行,
        #         而最早 201 条投诉 (8/6-8/7 创建的历史老单) 95%+ 都是已完成, 导致:
        #         ① 真实未闭环 208 条 只展示了 9-13 条 (1/15 量级放大漏单)
        #         ② 109 条 微信商户号投诉 中有大量排在 ASC 序号 #202+ 的被 LIMIT 吞掉
        # 新策略:
        #   1. SQL 层只做【倒查7天创建 + 投诉判定】两道前置过滤, 不做 LIMIT
        #      (近 7 天投诉类工单仅 ~1276 条, 内存压力完全可接受, 远小于全量工单 7595 条)
        #   2. Python 层先按 status bucket 过滤掉 已完成/已终止 → 得到真正未闭环的全集
        #   3. 合并三表后按 occur_time ASC(最早积压排第一), 再在 Python 层统一 LIMIT 200
        #      (超过 200 条时才截断, 并附 tooltip 提示运营优先处理最早那一批)
        # ═══════════════════════════════════════════════════════════
        st_pending_rows = db.execute(
            select(
                ServiceTicket.ticket_no,
                ServiceTicket.register_time,
                ServiceTicket.device_sn,
                ServiceTicket.phone,
                ServiceTicket.service_item,
                ServiceTicket.service_category,
                ServiceTicket.status,
                ServiceTicket.handler,
                ServiceTicket.remark,
            )
            .where(st_where)
            .order_by(ServiceTicket.register_time.asc())
            #  ⚠️ 移除 LIMIT: 让所有近 7 天内创建、命中投诉判定的行都进入 Python 层 status 过滤
        ).mappings().all()
        for r in st_pending_rows:
            bucket = _to_status_bucket(r["status"])
            if bucket in ("已完成", "已终止"):
                continue
            # 投诉标题/摘要: 优先 service_item, 否则 service_category, 兜底 remark 前 32 字
            title_parts = []
            if r["service_item"]:
                title_parts.append(r["service_item"])
            elif r["service_category"]:
                title_parts.append(r["service_category"])
            if r["remark"]:
                _rmk = str(r["remark"])[:32]
                if _rmk and (not title_parts or _rmk not in title_parts[-1]):
                    title_parts.append(_rmk)
            title = " · ".join(title_parts) or "内部投诉工单(未登记详情)"
            complaint_pending.append({
                "source_type": "内部投诉",
                "no": r["ticket_no"] or "",
                "occur_time": r["register_time"].isoformat() if r["register_time"] else "",
                "complainant": "",
                "complainant_phone": r["phone"] or "",
                "device_sn": r["device_sn"] or "",
                "title": title,
                "status_bucket": bucket,
                "status_raw": r["status"] or "",
                "handler": _clean_handler(r["handler"]),
            })

        # ── B. external_complaints 外部投诉 (未闭环) ──
        # 注意: complaint_pending 的三表查询直接使用表模型，不受 _INCLUDE_COMPLAINT_FILES 标志影响
        # 该标志仅控制投诉子类饼图/根因等聚合查询，并不阻止未闭环列表查询
        ec_where = and_(
            ExternalComplaint.complaint_date.isnot(None),
            ExternalComplaint.complaint_date >= ts_pending,
            ExternalComplaint.complaint_date <= te_pending,
        )
        ec_pending_rows = db.execute(
            select(
                ExternalComplaint.complaint_no,
                ExternalComplaint.complaint_date,
                ExternalComplaint.complainant,
                ExternalComplaint.complainant_phone,
                ExternalComplaint.device_sn,
                ExternalComplaint.platform,
                ExternalComplaint.problem,
                ExternalComplaint.complaint_category,
                ExternalComplaint.result,
                ExternalComplaint.handle_time,
                ExternalComplaint.handle_detail,
                ExternalComplaint.follower,
            )
            .where(ec_where)
            .order_by(ExternalComplaint.complaint_date.asc())
            # ⚠️ 移除 LIMIT (和 service_tickets 同理, 统一放到 Python 层 过滤完 bucket 后再 LIMIT)
        ).mappings().all()
        for r in ec_pending_rows:
            # 状态推断: 复用 handler_stats 口径 (result/handle_time/handle_detail)
            rs_raw = ""
            if (r["result"] or "").find("有效") >= 0:
                rs_raw = "已完成"
            elif (r["result"] or "").find("无效") >= 0:
                rs_raw = "已终止"
            elif (r["result"] or "").find("处理") >= 0:
                rs_raw = "处理中"
            elif r["handle_time"] is not None:
                rs_raw = "已完成"
            elif r["handle_detail"]:
                rs_raw = "处理中"
            else:
                rs_raw = "未标注"
            bucket = _to_status_bucket(rs_raw)
            if bucket in ("已完成", "已终止"):
                continue
            # 投诉摘要: 平台 + 分类/问题
            title_parts = []
            if r["platform"]:
                title_parts.append(f"[{r['platform']}]")
            if r["complaint_category"]:
                title_parts.append(r["complaint_category"])
            if r["problem"]:
                _pb = str(r["problem"])[:64]
                if _pb:
                    title_parts.append(_pb)
            title = " ".join(title_parts) or "外部投诉(未登记详情)"
            handler_raw = r["follower"]
            complaint_pending.append({
                "source_type": "外部投诉",
                "no": r["complaint_no"] or "",
                "occur_time": r["complaint_date"].isoformat() if r["complaint_date"] else "",
                "complainant": r["complainant"] or "",
                "complainant_phone": r["complainant_phone"] or "",
                "device_sn": r["device_sn"] or "",
                "title": title,
                "status_bucket": bucket,
                "status_raw": r["result"] or rs_raw,
                "handler": _clean_handler(handler_raw),
            })

        # ── C. card_complaints 卡商群投诉 (未闭环) ──
        cc_where = and_(
            CardComplaint.complaint_date.isnot(None),
            CardComplaint.complaint_date >= ts_pending,
            CardComplaint.complaint_date <= te_pending,
        )
        cc_pending_rows = db.execute(
            select(
                CardComplaint.id,
                CardComplaint.complaint_date,
                CardComplaint.complainant,
                CardComplaint.complainant_phone,
                CardComplaint.device_sn,
                CardComplaint.complaint_group,
                CardComplaint.problem,
                CardComplaint.problem_category,
                CardComplaint.result,
                CardComplaint.handle_result,
                CardComplaint.solution,
                CardComplaint.handler,
            )
            .where(cc_where)
            .order_by(CardComplaint.complaint_date.asc())
            # ⚠️ 移除 LIMIT (和 service_tickets 同理, 统一放到 Python 层 过滤完 bucket 后再 LIMIT)
        ).mappings().all()
        for r in cc_pending_rows:
            rs_raw = ""
            if (r["result"] or "").find("已处理") >= 0:
                rs_raw = "已完成"
            elif (r["result"] or "").find("未处理") >= 0:
                rs_raw = "待处理"
            elif (r["result"] or "").find("处理") >= 0:
                rs_raw = "处理中"
            elif r["handle_result"]:
                rs_raw = "已完成"
            elif r["solution"]:
                rs_raw = "处理中"
            else:
                rs_raw = "未标注"
            bucket = _to_status_bucket(rs_raw)
            if bucket in ("已完成", "已终止"):
                continue
            title_parts = []
            if r["complaint_group"]:
                title_parts.append(f"[{r['complaint_group']}]")
            if r["problem_category"]:
                title_parts.append(r["problem_category"])
            if r["problem"]:
                _pb = str(r["problem"])[:64]
                if _pb:
                    title_parts.append(_pb)
            title = " ".join(title_parts) or "(外)卡商投诉(未登记详情)"
            complaint_pending.append({
                "source_type": BUCKET_CARD,   # #47: 独立桶, 不再并入外部投诉
                "no": f"CC-{r['id']}" if r["id"] is not None else "",
                "occur_time": r["complaint_date"].isoformat() if r["complaint_date"] else "",
                "complainant": r["complainant"] or "",
                "complainant_phone": r["complainant_phone"] or "",
                "device_sn": r["device_sn"] or "",
                "title": title,
                "status_bucket": bucket,
                "status_raw": r["result"] or rs_raw,
                "handler": _clean_handler(r["handler"]),
            })

        # 合并三张表结果后按 投诉时间 ASC(最早积压排第一, 倒逼优先处理最久未动的工单)
        complaint_pending.sort(
            key=lambda x: (x["occur_time"] or "9999-12-31"),
        )
        # 截断超过限制,并给出提示
        if len(complaint_pending) > PENDING_LIMIT_ROWS:
            complaint_pending = complaint_pending[:PENDING_LIMIT_ROWS]
            pending_skipped = True
            pending_skip_reason = (
                f"未闭环投诉超过 {PENDING_LIMIT_ROWS} 条, 仅展示最早需处理的前 {PENDING_LIMIT_ROWS} 条;"
                " 请优先处理最早的投诉或缩小查询范围。"
            )

    # 注意: pending_total 口径 = "倒查 7 天窗口内仍未闭环" 的实际条数(和列表一致)
    # 而 complaint_status_all 里的待处理/处理中统计仍然是「原视图窗口」(和饼图/处理人排行口径一致, 不改动)
    complaint_pending_total = len(complaint_pending)

    # 数据一致性校验: 确保 total 与 list 长度一致，防止前端展示不一致
    import logging
    _log = logging.getLogger(__name__)
    if complaint_pending_total > 0 and len(complaint_pending) == 0:
        _log.warning(f"complaint_pending 数据不一致: total={complaint_pending_total}, list 为空")
        complaint_pending_total = 0
    elif complaint_pending_total != len(complaint_pending):
        _log.warning(f"complaint_pending 数据不一致: total={complaint_pending_total}, list_len={len(complaint_pending)}")

    return {
        "date": d_iso,
        "date_end": d_end_iso,
        # 原接待分类
        "categories": sorted(categories, key=lambda x: -x["value"]),
        "categories_device": sorted(categories_device, key=lambda x: -x["value"]),
        "top_staff": top_staff,
        # 新增: 今日投诉
        # ★★ 2026-09-11 (#53): complaint_total **不含卡商** ★★
        #    卡商已剥离为独立体系, 见下方 card_* 字段。
        "complaint_total": complaint_total,
        "complaint_subs": complaint_subs,     # 投诉子类 (饼图: 商品投诉/服务投诉/微信投诉/12315/工信部/公共服务平台, 每项带 source_type)
        # 投诉来源 内部/商户号/外部 **三类**(2026-09-11 #53: 卡商已移出, 见 card_*)
        #   2026-09-04 问题3: 商户号不并入外部
        "complaint_by_source": complaint_by_source,
        # ── (#53) 卡商独立统计体系（不参与投诉总量）──
        #   用户: "卡商的投诉 不纳入总量的管理, 整体独立统计分析…全部单算"。
        #   口径(#65): 工单侧真卡商 + `card_complaints` − 重叠(与 /summary 同公式)。
        "card_total": card_total,                    # 卡商独立总量(非 complaint_total 的一部分)
        "card_scope": CARD_SCOPE,                    # "(外)卡商" —— 前端标注用
        "card_by_category": card_by_category,        # 卡商问题分类分布
        "complaint_roots": complaint_roots,   # 投诉根因 (柱图: 6+3+扩展桶)
        "complaint_unmatched": complaint_unmatched,  # 未匹配到接待记录的投诉数
        # ↓↓↓ 2026-08-13 新增: 投诉处理人 + 处理状态全景 ↓↓↓
        "complaint_handlers": complaint_handlers,  # Top 15 处理人(合并): {handler, total, by_status, by_source}
        "complaint_internal_handlers": complaint_internal_handlers,  # 2026-08-14 新增: 内部投诉处理人 Top 15
        # ⚠️ 2026-09-10 (#47) 语义变更: **不再含卡商**(卡商见 complaint_card_handlers)。
        #    原 LOCKED A-20260902-016 §问题3 规定 = 外部投诉 + 卡商群投诉(合并),
        #    现按用户要求拆开单列, 字段名保留以向后兼容。
        "complaint_external_handlers": complaint_external_handlers,  # 外部投诉处理人 Top 15
        "complaint_card_handlers": complaint_card_handlers,  # (#47 新增) (外)卡商处理人 Top 15
        "complaint_status_all": complaint_status_all,  # 三表合并处理状态 5 桶(对齐处理人status)
        # ↓↓↓ 2026-08-13 新增: 近期未处理投诉列表 ↓↓↓
        "complaint_pending_total": complaint_pending_total,   # 未闭环投诉总数(待+处理中+未标注), 与 complaint_status_all 口径一致
        "complaint_pending_skipped": pending_skipped,         # 是否因跨度超限而被跳过(日/周视图=false, 长跨度=true)
        "complaint_pending_skip_reason": pending_skip_reason, # 跳过/截断说明(给前端 tooltip)
        "complaint_pending_list": complaint_pending,          # 待处理明细列表(最多200条)
    }

def _ensure_service_tickets_for_unmatched_refunds(
    db: Session,
    range_start: _dt.datetime,
    range_end: _dt.datetime,
    match_win_sec: int = 86400 * 45,
) -> dict[str, int]:
    """F2: 审计兜底 — 针对 [range_start, range_end) 内所有路线A退款(线上直退),
    若找不到 ±match_win_sec 内同 SN 的 service_ticket, 自动补建一条 service_ticket,
    用 refund.operator → registrant, refund.auditor 或 operator → handler,
    用 remark/audit_remark/package_name 关键字推 service_category/service_item.

    为保证幂等: 新增的工单号 prefix = 'GDREF_'+refund_no (和正常 GDC/GD prefix 完全区分);
    若已存在同 ticket_no 的工单 → 跳过 (INSERT ON CONFLICT DO NOTHING 语义, 用 merge instead)

    返回: {"inserted": N, "skipped": M} 供调用方参考
    """
    # 1. 扫所有日期内路线A退款
    in_range = and_(
        Refund.created_at_raw.isnot(None),
        Refund.created_at_raw >= range_start,
        Refund.created_at_raw <= range_end,
        *_route_a_online_refund_filter(),
    )
    refunds_rows = db.execute(select(
        Refund.refund_no, Refund.device_sn, Refund.operator, Refund.auditor,
        Refund.remark, Refund.audit_remark, Refund.package_name, Refund.refund_reason,
        Refund.refund_amount, Refund.actual_amount, Refund.created_at_raw, Refund.updated_at_raw,
        Refund.platform_order_no, Refund.status,
    ).where(in_range)).all()
    if not refunds_rows:
        return {"inserted": 0, "skipped": 0}

    # 1.5 姓名归一查维表: 把 operator/auditor → Person.display_name 一次性先查好 (避免 for 里 N+1)
    all_names = sorted({
        x for r in refunds_rows for x in [r.operator or "", r.auditor or ""] if x
    })
    name_map: dict[str, str] = {}
    if all_names:
        op_prs = _operator_to_person_cte()
        alias_rows = db.execute(select(op_prs.c.operator, op_prs.c.display_name
        ).where(op_prs.c.operator.in_(all_names))).all()
        for a, dn in alias_rows:
            if dn and a:
                name_map[a] = dn
    def _dn(x: str | None) -> str | None:
        if not x: return None
        return name_map.get(x, x)  # 没alias登记则保留原值

    # 2. 批量判断每条退款是否有"有效"工单 (1 次 SQL 直接跑存在性判断)
    # v3.2 P0修复: 只有匹配到 handler 非空(已指派/已处理) 的工单才算"有工单";
    #   如果匹配到的是 handler=NULL 的用户自助工单(H5/微信投诉), 视为"仍然无有效接待人",
    #   需要继续补建 GDREF_ 兜底工单, 避免 handler 空值落入 [未分配] 桶.
    #   同时增加 service_category IS NOT NULL 条件以改善原因榜的覆盖率(与P0-1排序规则对齐)
    have_ticket: set[str] = set()
    # 简单策略: 直接按 SN 列表过滤 + 时间窗
    sns = sorted({r.device_sn for r in refunds_rows if r.device_sn})
    if sns:
        sec = match_win_sec
        pairs = db.execute(select(Refund.refund_no, ServiceTicket.ticket_no
        ).select_from(Refund).join(ServiceTicket, and_(
            ServiceTicket.device_sn == Refund.device_sn,
            ServiceTicket.device_sn.in_(sns),
            Refund.device_sn.in_(sns),
            ServiceTicket.register_time.isnot(None),
            Refund.created_at_raw.isnot(None),
            func.abs(func.extract("epoch", ServiceTicket.register_time - Refund.created_at_raw)) < sec,
            # v3.2 P0新增: 必须handler非空, 才算"已被服务工单覆盖"
            ServiceTicket.handler.isnot(None),
            ServiceTicket.handler != "",
            # 同时工单必须有明确分类信息(避免"空分类工单"阻止补建, 导致原因榜也失真)
            ServiceTicket.service_category.isnot(None),
            ServiceTicket.service_category != "",
        )).where(in_range)).all()
        for rno, _tno in pairs:
            have_ticket.add(rno)

    # 3. 对没工单的退款 建 service_ticket
    inserted = 0
    skipped = 0
    # 批量查重(避免循环内逐条 SELECT 的 N+1): 一次性取出已存在的 GDREF_ 工单号集合,
    # 循环里直接 in 判断, 把 N 次 SELECT 降为 1 次(窗口内退款多时尤其明显)。
    unmatched_rows = [r for r in refunds_rows if r.refund_no not in have_ticket]
    candidate_tickets = ["GDREF_" + r.refund_no for r in unmatched_rows]
    existing_tickets: set[str] = set()
    if candidate_tickets:
        existing_tickets = set(db.scalars(
            select(ServiceTicket.ticket_no).where(ServiceTicket.ticket_no.in_(candidate_tickets))
        ).all())
    for r in unmatched_rows:
        # 幂等: 工单号 = GDREF_{refund_no}  (64 字符限制 OK, refund_no ~ 22 字符)
        ticket_no = "GDREF_" + r.refund_no
        if ticket_no in existing_tickets:
            skipped += 1
            have_ticket.add(r.refund_no)
            continue
        scat, sitm = _derive_service_item_for_refund(r.remark, r.audit_remark, r.package_name, r.refund_reason)
        registrant_raw = r.operator or "系统自动补建"
        handler_raw = (r.auditor or r.operator or None)
        # 写入前姓名归一 (维表 PersonAlias→Person.display_name), 保持与展示侧 by_staff 口径一致, 避免同一人在不同表里用不同字符串
        registrant = _dn(registrant_raw) or registrant_raw
        handler = _dn(handler_raw) if handler_raw else "未指派"
        # 备注信息: 合并退款 remark/audit_remark/refund_reason + 来源 refund_no
        rmk_parts = []
        if r.remark:
            rmk_parts.append("退款备注:" + r.remark)
        if r.audit_remark:
            rmk_parts.append("审核:" + r.audit_remark)
        if r.refund_reason and r.refund_reason != "用户退款":
            rmk_parts.append("原退款原因:" + r.refund_reason)
        rmk_parts.append("来源:退款单" + r.refund_no)
        if r.package_name:
            rmk_parts.append("商品:" + r.package_name)
        # 登记时间用退款的 created_at_raw
        reg_time = r.created_at_raw or (range_start + (range_end - range_start) / 2)
        # finish_time 用 updated_at_raw (如 status 非空且已完成)
        finish_time = None
        st_status = "处理中"
        if r.status and ("退款" in r.status or "完成" in r.status or "成功" in r.status):
            st_status = "已完成"
            finish_time = r.updated_at_raw or reg_time
        # 处理人如果是中文非空, 把 st_status 直接置已完成(默认已经操作完了), 避免人工重算
        if handler and handler != "未指派":
            st_status = "已完成"
            finish_time = r.updated_at_raw or reg_time
        new_t = ServiceTicket(
            ticket_no=ticket_no,
            device_sn=r.device_sn,
            phone=None,
            service_category=scat,
            service_item=sitm,
            priority=2,
            status=st_status,
            remark="; ".join(rmk_parts),
            register_time=reg_time,
            active_time=reg_time,
            dispatch_time=reg_time if (handler and handler != "未指派") else None,
            finish_time=finish_time,
            handle_duration=None,
            registrant=registrant,
            channel="系统自动补建(退款兜底)",
            handle_node="系统自动补建",
            handler=handler,
            handle_opinion=(
                "经营-实际对账兜底: 该退款发生前/后 " + str(match_win_sec // 86400) +
                " 天内无对应服务工单; 系统依据退款单字段自动补建以便统计分析。operator=" +
                (r.operator or "") + ", auditor=" + (r.auditor or "")
            ),
            is_appeal="否",
            order_no=r.platform_order_no,
            submit_params=None,
            refund_amount=float(r.actual_amount or r.refund_amount or 0.0),
            estimated_amount=float(r.refund_amount or 0.0),
        )
        db.add(new_t)
        inserted += 1
        have_ticket.add(r.refund_no)
    # Flush 以便 immediately following 的 best_match CTE 能看到; 外部事务统一 commit
    if inserted:
        db.flush()
    return {"inserted": inserted, "skipped": skipped}

def today_refund_breakdown(db: Session, date: str | None = None, start: str | None = None, end: str | None = None) -> dict[str, Any]:
    """模块4: 今日退款分项 = 退款细分原因Top10 + 退款接待人Top10 【统一口径: 路线A refund_method='线上'】

    v3.1 升级(2026-08-02):
      1. 时间窗过窄修复: |register_time − created_at_raw| < ±30天 → ±45天 (覆盖老套餐月末冷静期退款)
      2. 未关联工单兜底: 进入分析前调用_ensure_service_tickets_for_unmatched_refunds
         自动按退款单补建 service_ticket(ticket_no=GDREF_*),
         handler = auditor或operator, service_item 由 remark/package 关键词推导
         → 彻底消除 "[未分配]" 桶绩效失真
      3. 原因榜/接待人榜仍用同一张 best_match CTE
    """
    ts, te, _, _, is_realtime, disp_range = _resolve_windows(date=date, start=start, end=end)
    if disp_range:
        d_iso = disp_range[0][:10]
        d_end_iso = disp_range[1][:10]
    else:
        d_iso = (_dt.datetime.now(TZ).date() if date is None else _parse_date(date)).isoformat()
        d_end_iso = None
    between = and_(Refund.created_at_raw.isnot(None),
                   Refund.created_at_raw >= ts, Refund.created_at_raw <= te,
                   *_route_a_online_refund_filter())
    _WIN_45D = 86400 * 45  # v3.1: ±30d → ±45d,修复 8/1 样本里 31.28d 这种工单登记稍早被错漏的情况

    # ── F2. 自动补建工单: 日期内 ±45d 未匹配到工单的退款, 自动建一条 service_ticket, 保证 接待人/原因 绩效不漏 ──
    _ensure_service_tickets_for_unmatched_refunds(db, ts, te, match_win_sec=_WIN_45D)

    # ── CTE 共享: 每个 refund 最佳匹配工单 ──
    # v3.2 排序升级 (P0修复):
    #   1) handler 非空优先 (避免"时差最小但handler=NULL的用户自助工单"覆盖掉"时差稍大但有真实处理人"的工单)
    #   2) scat/sitm 非空优先 (同理, 原因榜不要选上空分类的工单)
    #   3) 时间差最小 (原有逻辑, 作为次级排序)
    # 同时带出 Refund.auditor/operator 作为最终兜底: 工单hdl仍空时直接回退到退款单审核人/操作人
    _hdl_ok = case(
        (and_(ServiceTicket.handler.isnot(None), ServiceTicket.handler != ""), 1),
        else_=0,
    )
    _cat_ok = case(
        (and_(
            ServiceTicket.service_category.isnot(None), ServiceTicket.service_category != "",
            ServiceTicket.service_item.isnot(None), ServiceTicket.service_item != "",
        ), 2),
        (and_(
            (ServiceTicket.service_category.isnot(None)) & (ServiceTicket.service_category != ""),
        ), 1),
        else_=0,
    )
    best_stmt = select(
        Refund.refund_no.label("rid"),
        Refund.refund_amount.label("amt"),
        Refund.package_name.label("pkg"),
        Refund.refund_reason.label("rrsn"),
        Refund.auditor.label("raud"),   # 新增: 退款单审核人 (P2回退)
        Refund.operator.label("rop"),   # 新增: 退款单操作人 (P2回退)
        # 工单字段 (左连接, 可能 NULL)
        ServiceTicket.service_category.label("scat"),
        ServiceTicket.service_item.label("sitm"),
        ServiceTicket.handler.label("hdl"),
        func.abs(func.extract("epoch",
            ServiceTicket.register_time - Refund.created_at_raw
        )).label("diff_sec"),
    ).select_from(Refund).outerjoin(ServiceTicket, and_(
        ServiceTicket.device_sn == Refund.device_sn,
        Refund.device_sn.isnot(None), Refund.device_sn != "",
        ServiceTicket.register_time.isnot(None),
        func.abs(func.extract("epoch",
            ServiceTicket.register_time - Refund.created_at_raw
        )) < _WIN_45D,
    )).where(between)
    best_cte = best_stmt.distinct(text("rid")).order_by(
        text("rid"),
        _hdl_ok.desc(),          # P0: handler 非空优先 (1 > 0)
        _cat_ok.desc(),          # 同步: 分类非空优先
        text("diff_sec NULLS LAST"),
    ).cte("best_match")

    # ── ① 左: 细分原因 Top10 (按金额倒序) ──
    def _name_case():
        sc = best_cte.c.scat
        si = best_cte.c.sitm
        # 投诉工单的 service_category 空 → 按既有规范统一回退到 '投诉'
        sc_disp = case(
            (sc.is_(None), "投诉"),
            (sc == "", "投诉"),
            else_=sc,
        )
        has_both = sc.isnot(None) & (sc != "") & si.isnot(None) & (si != "")
        has_sc = sc.isnot(None) & (sc != "")
        matched = case(
            (has_both, sc_disp + " / " + si),
            (has_sc, sc_disp),
            else_=None,
        )
        return case(
            (matched.isnot(None), matched),                    # 1. 工单分类
            else_="[原因未登记] 未关联到工单",                # 2. 未匹配统一桶（B修复: 不混商品）
        )

    reason_agg = db.execute(select(
        _name_case().label("name"),
        func.count().label("cnt"),
        func.coalesce(func.sum(best_cte.c.amt), 0).label("amt"),
    ).group_by(text("name")).order_by(func.sum(best_cte.c.amt).desc())).all()
    reasons = [
        {"name": r[0] or "未填写", "count": int(r.cnt or 0), "amount": round(float(r.amt or 0), 2)}
        for r in reason_agg
    ][:10]  # Top10 按金额

    # ── ② 右: 退款接待人 Top10 ──
    # v3.2 两级兜底策略 (P2修复):
    #   Tier1: 工单handler → 经 PersonAlias 归一化后取 display_name
    #   Tier2: 工单handler仍空时 → 用退款单自身的 auditor (优先) / operator (次选) 回退计名
    #          与 _ensure_service_tickets_for_unmatched_refunds 补建工单时取handler逻辑完全一致
    #   Tier3: auditor/operator 都空 → 才进 "[未分配]" 桶
    #
    # 姓名归一化: 一次性从 best_cte 取出 hdl/raud/rop 三列的去重 raw 值集合,
    # 用 1 次 _operator_to_person_cte 反查构建 name_map (避免 SQL 里多CTE同名冲突 & N+1),
    # 然后在 SQL 里按优先级算出 raw_handler, 最后 Python 端统一做 display_name 替换 + 聚合.
    # Step B1: 查所有候选name的归一结果 (1次SQL取三列, Python端合并去重构建name_map, 避免Union/CTE复杂)
    name_rows = db.execute(select(
        best_cte.c.hdl, best_cte.c.raud, best_cte.c.rop,
    ).select_from(best_cte)).all()
    all_raw: set[str] = set()
    for r in name_rows:
        if r.hdl: all_raw.add(r.hdl)
        if r.raud: all_raw.add(r.raud)
        if r.rop: all_raw.add(r.rop)
    name_map: dict[str, str] = {}
    if all_raw:
        op_prs = _operator_to_person_cte()
        nm_rows = db.execute(select(
            op_prs.c.operator, op_prs.c.display_name,
        ).where(op_prs.c.operator.in_(sorted(all_raw)))).all()
        for _op, dn in nm_rows:
            if dn and _op:
                name_map[_op] = dn

    def _dn(x: str | None) -> str | None:
        if not x: return None
        return name_map.get(x, x)

    # Step B2: SQL 聚合只按 raw_handler (三级回退优先级) 聚合, 不做维表JOIN
    _raw_hdl = case(
        (best_cte.c.hdl.isnot(None) & (best_cte.c.hdl != ""), best_cte.c.hdl),
        (best_cte.c.raud.isnot(None) & (best_cte.c.raud != ""), best_cte.c.raud),
        (best_cte.c.rop.isnot(None) & (best_cte.c.rop != ""), best_cte.c.rop),
        else_=None,
    )
    staff_raw = db.execute(select(
        _raw_hdl.label("raw"),
        func.count().label("cnt"),
        func.coalesce(func.sum(best_cte.c.amt), 0).label("amt"),
    ).select_from(best_cte
    ).group_by(text("raw"))).all()

    # Step B3: Python 端 display_name 归一 → 合并 → 排序取 Top
    merged: dict[str, dict] = {}
    for r in staff_raw:
        raw = r.raw
        if not raw:
            nm = "[未分配] 未关联工单或未指派处理人"
        else:
            nm = _dn(raw) or raw or "[未分配] 未关联工单或未指派处理人"
        if nm not in merged:
            merged[nm] = {"name": nm, "count": 0, "amount": 0.0}
        merged[nm]["count"] += int(r.cnt or 0)
        merged[nm]["amount"] += float(r.amt or 0)
    by_staff = sorted(merged.values(), key=lambda x: -x["amount"])[:15]
    for s in by_staff:
        s["amount"] = round(s["amount"], 2)

    # ── ③ 补充字段: 退款商品分布 Top10 (独立于原因维度, 用于业务分析但不混原因榜) ──
    pkg_agg = db.execute(select(
        Refund.package_name.label("pkg"),
        func.count().label("cnt"),
        func.coalesce(func.sum(Refund.refund_amount), 0).label("amt"),
    ).where(between).group_by(Refund.package_name
    ).order_by(func.sum(Refund.refund_amount).desc()).limit(10)).all()
    packages = [
        {"name": r[0] or "商品名空", "count": int(r.cnt or 0), "amount": round(float(r.amt or 0), 2)}
        for r in pkg_agg
    ]

    return {
        "date": d_iso,
        "date_end": d_end_iso,
        "reasons": reasons,                 # 左: 退款原因榜 (工单分类 + 未登记统一桶)
        "packages": packages,               # 独立榜: 退款商品分布 Top10 (与原因榜解耦)
        "by_staff": by_staff,               # 右: 退款接待人 Top10 (工单处理人 handler)
        "methods": [],                      # 向后兼容: 原"退款方式分布" 只路线A一桶无意义, 移除以防误导
        "_spec": {
            "scope": "路线A: 退费单 refund_method='线上'",
            "reason_strategy": (
                "v3.2: 1)best_match DISTINCT ON排序: 先分类完整性(scat&sitm > scat > 空) → 再时差最小; "
                "2)自动补建GDREF_工单的判定条件升级为: 只有[handler非空 & 分类非空]的工单才算'已覆盖' → "
                "否则继续补建GDREF_以纠正原因榜; 3)补建后仍无分类 → '[原因未登记] 未关联到工单' 统一桶"
            ),
            "staff_strategy": (
                "v3.2 P0+P2三级兜底: 1)best_match DISTINCT ON 先选handler非空的工单(>handler空时差最小); "
                "2)工单handler仍空 → 回退用退款单auditor → operator (与补建工单取handler口径一致, 自动PersonAlias归一); "
                "3)auditor/operator全空 → 才进'[未分配] 未关联工单或未指派处理人'桶; "
                "4)补建工单判定升级为: 仅handler非空工单算已覆盖, 否则仍补建GDREF_填handler"
            ),
            "time_window_seconds": _WIN_45D,
            "packages_strategy": "独立字段 packages: 按 package_name 商品 Top10 (原因榜与商品榜彻底解耦)",
        },
    }

def _recurring_matches_today(pattern: str | None, valid_from, target_date) -> bool:
    """判断周期性事件在 target_date 当日是否生效.
    pattern: daily(每日) / weekly(同周几) / weekday(工作日) / monthly(同日)
    valid_from: 周期起始(用于确定 周几/几号 基准). 无 pattern 默认按 daily.
    """
    if pattern is None or pattern == "" or pattern == "daily":
        return True
    vf = valid_from
    if vf is None:
        return True
    # 统一取 weekday(0=Mon..6=Sun) 与 day(月内日)
    try:
        vf_wd = vf.weekday() if hasattr(vf, "weekday") else None
        vf_day = vf.day if hasattr(vf, "day") else None
        tgt_wd = target_date.weekday() if hasattr(target_date, "weekday") else None
        tgt_day = target_date.day if hasattr(target_date, "day") else None
    except Exception:
        return True
    if pattern == "weekly":
        return vf_wd is not None and tgt_wd is not None and vf_wd == tgt_wd
    if pattern == "weekday":
        return tgt_wd is not None and tgt_wd < 5  # 周一~周五
    if pattern == "monthly":
        return vf_day is not None and tgt_day is not None and vf_day == tgt_day
    return True

def today_queue(db: Session, date: str | None = None, start: str | None = None, end: str | None = None) -> dict[str, Any]:
    """模块5: 今日处理队列 = 今日截止待办 + 进行中专项 + 今日异常事件.
    每项最多10条, 点击可跳转详情页.
    """
    ts, te, _, _, is_realtime, disp_range = _resolve_windows(date=date, start=start, end=end)
    if disp_range:
        d_iso = disp_range[0][:10]
        d_end_iso = disp_range[1][:10]
    else:
        d_iso = (_dt.datetime.now(TZ).date() if date is None else _parse_date(date)).isoformat()
        d_end_iso = None
    today_start = ts
    today_end = te
    # 待办 status: pending/processing/confirming 是未完成, done 已完成
    not_done = TodoItem.status.notin_(["done"])
    todo_rows = db.execute(
        select(TodoItem.id, TodoItem.title, TodoItem.priority, TodoItem.status,
               TodoItem.deadline, TodoItem.related_refund_no)
        .where(TodoItem.deadline.isnot(None),
               TodoItem.deadline >= today_start, TodoItem.deadline <= today_end,
               not_done)
        .order_by(case(
            (TodoItem.priority == "urgent", 0),
            (TodoItem.priority == "high", 1),
            (TodoItem.priority == "medium", 2),
            (TodoItem.priority == "low", 3),
            else_=4
        ), TodoItem.deadline.asc())
        .limit(10)
    ).all()
    todos = [{
        "id": r.id, "title": r.title, "priority": r.priority, "status": r.status,
        "deadline": r.deadline.isoformat() if r.deadline else None,
        "related_refund_no": r.related_refund_no,
    } for r in todo_rows]

    # 2. 进行中专项: status in (pending_start/executing/following) + deadline在7天内或无deadline
    active_status = ["pending_start", "executing", "following"]
    week_later = today_end + timedelta(days=7)
    task_rows = db.execute(
        select(SpecialTask.id, SpecialTask.title, SpecialTask.status,
               SpecialTask.task_type, SpecialTask.deadline)
        .where(SpecialTask.status.in_(active_status))
        .order_by(case(
            (SpecialTask.deadline.is_(None), 1),
            (SpecialTask.deadline <= week_later, 0),
            else_=2
        ), SpecialTask.deadline.asc().nullslast())
        .limit(10)
    ).all()
    tasks = [{
        "id": r.id, "title": r.title, "status": r.status, "type": r.task_type,
        "deadline": r.deadline.isoformat() if r.deadline else None,
    } for r in task_rows]

    # 3. 今日影响事件:
    #    (a) 未闭环(is_closed=False)的非周期事件 持续每天显示, 直到闭环 —— "状态不注明就持续影响每天"
    #    (b) 当日新发生/登记的非周期事件
    #    (c) 周期性事件(is_recurring=True): 在 [valid_from, valid_to] 生效区间内 + 周期模式命中当日
    #        用于广告推送/定期维护等阶段性影响, 每日生效时明确显示
    #    排序: 周期性/未闭环在前(按发生时间倒序), 已闭环的当日事件在后
    inc_between = or_(
        and_(Incident.occurred_at.isnot(None), Incident.occurred_at >= today_start, Incident.occurred_at <= today_end),
        and_(Incident.created_at.isnot(None), Incident.created_at >= today_start, Incident.created_at <= today_end),
    )
    # 非周期事件: 未闭环 或 当日发生
    non_recur_rows = db.execute(
        select(Incident.id, Incident.title, Incident.status, Incident.subject,
               Incident.occurred_at, Incident.duration_minutes, Incident.is_closed,
               Incident.severity, Incident.is_recurring, Incident.recurrence_pattern)
        .where(Incident.is_recurring == False, or_(Incident.is_closed == False, inc_between))  # noqa: E712
        .order_by(Incident.is_closed.asc(), Incident.occurred_at.desc().nullslast(), Incident.created_at.desc())
        .limit(10)
    ).all()
    seen_ids = {r.id for r in non_recur_rows}
    incidents = [{
        "id": r.id, "title": r.title, "status": r.status, "subject": r.subject,
        "occurred_at": r.occurred_at.isoformat() if r.occurred_at else None,
        "duration_minutes": r.duration_minutes,
        "is_closed": bool(r.is_closed),
        "severity": r.severity,
        "is_recurring": False, "recurrence_pattern": None,
    } for r in non_recur_rows]

    # 周期性事件: 生效区间覆盖当日 + 周期模式命中
    recur_rows = db.execute(
        select(Incident.id, Incident.title, Incident.status, Incident.subject,
               Incident.occurred_at, Incident.duration_minutes, Incident.is_closed,
               Incident.severity, Incident.is_recurring, Incident.recurrence_pattern,
               Incident.valid_from, Incident.valid_to)
        .where(
            Incident.is_recurring == True,  # noqa: E712
            Incident.valid_from.isnot(None),
            Incident.valid_from <= today_end,
            or_(Incident.valid_to.is_(None), Incident.valid_to >= today_start),
        )
        .order_by(Incident.occurred_at.desc().nullslast())
        .limit(30)
    ).all()
    # 当日目标日期(本地时区)用于周期模式命中判定
    target_date = today_start.astimezone(TZ).date() if today_start.tzinfo else today_start.date()
    for r in recur_rows:
        if r.id in seen_ids:
            continue
        if not _recurring_matches_today(r.recurrence_pattern, r.valid_from, target_date):
            continue
        seen_ids.add(r.id)
        incidents.append({
            "id": r.id, "title": r.title, "status": r.status, "subject": r.subject,
            "occurred_at": r.occurred_at.isoformat() if r.occurred_at else None,
            "duration_minutes": r.duration_minutes,
            "is_closed": bool(r.is_closed),
            "severity": r.severity,
            "is_recurring": True, "recurrence_pattern": r.recurrence_pattern,
        })
    # 周期性在前, 其次未闭环, 最后已闭环当日
    def _inc_sort_key(inc):
        return (
            0 if inc["is_recurring"] else (1 if not inc["is_closed"] else 2),
            inc["occurred_at"] or "",
        )
    incidents.sort(key=_inc_sort_key, reverse=True)
    incidents = incidents[:10]

    return {
        "date": d_iso,
        "date_end": d_end_iso,
        "todos": todos,
        "tasks": tasks,
        "incidents": incidents,
    }

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
                NULLIF(TRIM(c.device_sn),'') AS sn
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
                   NULLIF(TRIM(r.device_sn),'') AS sn
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
                   NULLIF(TRIM(st.device_sn),'') AS sn
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
                   NULLIF(TRIM(st.device_sn),'') AS sn
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
                   NULLIF(TRIM(st.device_sn),'') AS sn
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
                   NULLIF(TRIM(st.device_sn),'') AS sn
            FROM service_tickets st
            JOIN cs_records c2
              ON NULLIF(TRIM(c2.device_sn),'') = NULLIF(TRIM(st.device_sn),'')
             AND c2.operator IS NOT NULL
             AND c2.operator NOT IN ('sys','system','SYS','System','用户自助','客户自助','企微用户自助','WeChat','wechat','WECHAT','AI客服','智能客服','机器人客服','客户','班牛','系统','微信投诉')
             AND c2.operation_time IS NOT NULL
             AND c2.operation_time >= st.register_time - INTERVAL '2 days'
             AND c2.operation_time <= st.finish_time + INTERVAL '1 day'
            WHERE st.finish_time IS NOT NULL
              AND st.finish_time >= :ts AND st.finish_time <= :te
              AND (st.handler IS NULL OR st.handler = '' OR st.handler = 'SYS')
              AND NULLIF(TRIM(st.device_sn),'') IS NOT NULL
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
              ON NULLIF(TRIM(c2.device_sn),'') = NULLIF(TRIM(st.device_sn),'')
             AND c2.operator IS NOT NULL
             AND c2.operator NOT IN ('sys','system','SYS','System','用户自助','客户自助','企微用户自助','WeChat','wechat','WECHAT','AI客服','智能客服','机器人客服','客户','班牛','系统','微信投诉')
             AND c2.operation_time IS NOT NULL
             AND c2.operation_time >= st.register_time - INTERVAL '2 days'
             AND c2.operation_time <= st.finish_time + INTERVAL '1 day'
            WHERE st.finish_time IS NOT NULL
              AND st.finish_time >= :ts AND st.finish_time <= :te
              AND (st.handler IS NULL OR st.handler = '' OR st.handler = 'SYS')
              AND NULLIF(TRIM(st.device_sn),'') IS NOT NULL
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
