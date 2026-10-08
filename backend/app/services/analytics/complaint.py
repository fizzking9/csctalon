"""投诉分类统计 + 投诉健康指标(complaint_health_metrics)与 category_by_* 主体聚合。"""

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, func, or_, select, text
from sqlalchemy.orm import Session

from app.models.combo_dict import ComboDict, ComboNameAlias
from app.models.cs_record import CsRecord
from app.models.device import Device, DimDeviceType
from app.models.merchant import DeviceMerchantLink, Merchant
from app.models.refund import Refund
from app.models.shop import DeviceShopLink, Shop
from app.models.ticket import ServiceTicket
from app.services.complaint_scope import (
    ALL_BUCKETS,
    BUCKET_EXTERNAL,
    BUCKET_INTERNAL,
    # ⚠️ #53: BUCKET_OTHER 已从来源桶移除(四类完备)。保留导入仅供历史数据识别,
    #    若确认无处使用可删除。
    BUCKET_OTHER,  # noqa: F401
    is_complaint_ticket_cond,
)

logger = logging.getLogger(__name__)

# 跨子模块引用(拆分自 app.services.analytics, 原同文件内直接调用)
from app.services.analytics._core import (
    REAL_FINISH_EPOCH_SECS,
    _complaint_source_type_case,
    _person_position_map,
    _resolve_windows,
    _route_a_online_refund_filter,
    _ticket_category_case,
)


# ══════════════════════════════════════════════════════════
# 工单「处理人」多级 fallback —— **单一来源** (#87)
# ══════════════════════════════════════════════════════════
# 原实现在 `api/v1/complaints._handler_fallback_cols`（仅统计汇总用）。
# #87 的「岗位负荷」也需要同一口径 → 上提到本模块，`complaints.py` 改为委派，
# **禁止**再写第二份（口径漂移是 #81/#83/#85 的共同病根）。
#
# 回归场景: 历史工单 `handler` 未规范化填写，但 `assignee`/`updated_by_raw`/
#   `registrant` 有值。优先取「已指派并填写」的 handler。
# ⚠️ 本函数**不改 DB 原始值**，仅用于统计归属。
def handler_fallback_expr(t):
    """工单处理人多级 fallback: handler → assignee → updated_by_raw → registrant

    参数 `t` = `ServiceTicket` 模型（传模型而非列，便于跨模块复用）。
    """
    return case(
        (t.handler.isnot(None) & (t.handler != ""), t.handler),
        (t.assignee.isnot(None) & (t.assignee != ""), t.assignee),
        (t.updated_by_raw.isnot(None) & (t.updated_by_raw != ""), t.updated_by_raw),
        else_=t.registrant,
    )



# ============================================================
# 模块: 工单有效完工时间推算 (effective_finish_time)
# ============================================================
# 背景: ticket.finish_time 89% 被批量覆盖/同步填充(详见 docs/reports/20260801-数据时间审核-审计报告.md L67-L70)
#   - 50% finish > register+12h (BMS 03:15 批量覆盖)
#   - 23% finish == register (导入时同步填充)
#   - 17% 更早于 register (同瞬填充)
#   - 仅 10.7% 是真实完成时间 (status=已完成, handler 非空, finish 落在 register..register+12h)
#
# 因此在统计"今日完工单量 / 人员绩效结单量"时,必须走以下推断链:
#   1) 先过白名单,真实完成 → 直接用 finish_time
#   2) 否则若 handle_duration 存在 → effective_finish = register_time + handle_duration 秒
#   3) 否则若该 handler 在近 30 天有真实平均耗时 → effective_finish = register_time + avg(handler)
#   4) 否则 → effective_finish = register_time + 全局平均(近 30 天真实完成样本)
#
# 最终只算 status == '已完成' 的工单 (status != 已完成意味着仍在途,不能视为"完工")。
# ============================================================
def _ticket_effective_finish_time_where(db: Session, start_time: datetime, end_time: datetime,
                                        lookback_days: int = 30) -> tuple[int, int]:
    """返回 (status=已完成 且 推算后 effective_finish 落在 [start_time,end_time] 里的工单数,
               其中「真实完工样本」数量(用于前端口径说明))

    用两层查询:
      第一层 SQL: 取近 lookback_days 每个 handler(+全局) 的平均 handle_duration(仅 真实完成样本参与, 避免平均被批量覆盖污染)
      第二层: 取 status='已完成' 且 register_time < end_time + 12h 的工单, Python 侧算 effective_finish 再计数。
      这比单条巨型 SQL 可读性高得多,性能也足够(量级 <= 30 天 ≈ 9k 行)。
    """
    SYS_USERS = {"sys", "system", "SYS", "System", "客户", None}

    # 统一为带 UTC tzinfo 的 aware datetime, 避免 naive <-> aware 比较 TypeError
    import datetime as __dt
    def _as_utc_aware(dt: datetime) -> datetime:
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=__dt.timezone.utc)
        return dt.astimezone(__dt.timezone.utc)
    _s = _as_utc_aware(start_time)
    _e = _as_utc_aware(end_time)

    # ── Step 1: 近 30 天 真实完成样本, 按 handler 聚合平均处理时长 ──
    hist_since = _s - timedelta(days=lookback_days)
    # 真实完成样本 = status=已完成 AND handler 非空非系统 AND handler 字面像人名 AND finish 在 register..register+12h
    real_done_cond = and_(
        ServiceTicket.status == "已完成",
        ServiceTicket.handler.isnot(None),
        ~ServiceTicket.handler.in_(list(SYS_USERS) + ["客户"]),
        ServiceTicket.finish_time.isnot(None),
        ServiceTicket.register_time.isnot(None),
        ServiceTicket.finish_time >= ServiceTicket.register_time,
        ServiceTicket.finish_time <= func.timeadd('hour', 12, ServiceTicket.register_time) if False
        else ServiceTicket.finish_time <= ServiceTicket.register_time + text("INTERVAL '12 hours'"),
    )
    # a) 按 handler 平均 (只对有真实完成样本的 handler 建索引)
    handler_avg_rows = db.execute(
        select(
            ServiceTicket.handler,
            func.avg(
                case(
                    (ServiceTicket.handle_duration > 0, ServiceTicket.handle_duration),
                    else_=func.extract("epoch", ServiceTicket.finish_time - ServiceTicket.register_time),
                )
            ).label("avg_dur_s"),
            func.count().label("n"),
        )
        .where(real_done_cond, ServiceTicket.register_time >= hist_since)
        .group_by(ServiceTicket.handler)
    ).all()
    handler_avg: dict[str, float] = {}
    weighted_total, weighted_count = 0.0, 0
    for r in handler_avg_rows:
        dur = float(r._mapping["avg_dur_s"] or 0.0)
        n = int(r._mapping["n"] or 0)
        if n <= 0 or dur <= 0:
            continue
        handler_avg[str(r.handler).strip()] = dur
        weighted_total += dur * n
        weighted_count += n
    global_avg = (weighted_total / weighted_count) if weighted_count > 0 else (60.0 * 28)  # fallback = 28 分钟

    # ── Step 2: 拉 status='已完成' 且 register_time 落在 [hist_since, end_time+12h] 范围内的工单
    #            ( register > end_time 绝对不可能今日完工, 提前过滤) ──
    rows = db.execute(
        select(
            ServiceTicket.register_time,
            ServiceTicket.finish_time,
            ServiceTicket.handle_duration,
            ServiceTicket.handler,
            ServiceTicket.status,
        )
        .where(
            ServiceTicket.status == "已完成",
            ServiceTicket.register_time.isnot(None),
            ServiceTicket.register_time >= hist_since,
            ServiceTicket.register_time <= _e + timedelta(hours=13),
        )
    ).all()

    done_cnt = 0
    real_done_cnt = 0
    for r in rows:
        reg, fin, hd, hdlr, _st = (r.register_time, r.finish_time, r.handle_duration, r.handler, r.status)
        if not reg:
            continue
        # 把 reg/fin 都转成 UTC aware, 保证加 timedelta 后仍能和 _s/_e 比较
        reg_a = _as_utc_aware(reg)
        fin_a = _as_utc_aware(fin) if fin else None
        # 判定是否真实完成样本 (和上面 real_done_cond 保持一致)
        is_real = bool(
            fin_a is not None
            and hdlr is not None
            and hdlr not in SYS_USERS
            and fin_a >= reg_a
            and fin_a <= reg_a + timedelta(hours=12)
        )
        if is_real:
            eff = fin_a
        else:
            # 2) handle_duration > 0 直接用
            if isinstance(hd, (int, float)) and hd > 0:
                eff = reg_a + timedelta(seconds=float(hd))
            else:
                # 3) handler 近 30 天真实平均
                avg = handler_avg.get(str(hdlr).strip()) if hdlr else None
                if avg and avg > 0:
                    eff = reg_a + timedelta(seconds=avg)
                else:
                    # 4) 全局 fallback
                    eff = reg_a + timedelta(seconds=global_avg)
        if _s <= eff <= _e:
            done_cnt += 1
            if is_real:
                real_done_cnt += 1

    return done_cnt, real_done_cnt

def category_by_service_item(db: Session) -> list[dict]:
    """按服务项汇总(含类别兜底:空类别→投诉,返回外层category便于前端分组)"""
    rows = db.execute(
        select(
            _ticket_category_case(),
            ServiceTicket.service_item,
            func.coalesce(func.sum(ServiceTicket.refund_amount), 0),
            func.count(),
        )
        .where(ServiceTicket.service_item.isnot(None))
        .group_by("ticket_category", ServiceTicket.service_item)
        .order_by(func.sum(ServiceTicket.refund_amount).desc())
    ).all()
    total = sum(float(r[2] or 0) for r in rows) or 1
    return [
        {
            "category": r[0],  # 投诉/退款/套餐疑问/...
            "name": r[1],
            "value": float(r[2] or 0),
            "count": r[3],
            "ratio": round(float(r[2] or 0) / total, 4),
        }
        for r in rows
    ]

def category_by_merchant(db: Session, limit: int = 20) -> list[dict]:
    """按商户汇总退款 【统一口径: 路线A 退费单-线上直退】"""
    rows = db.execute(
        select(Refund.merchant_name, func.coalesce(func.sum(Refund.refund_amount), 0), func.count())
        .where(Refund.merchant_name.isnot(None), *_route_a_online_refund_filter())
        .group_by(Refund.merchant_name)
        .order_by(func.sum(Refund.refund_amount).desc())
        .limit(limit)
    ).all()
    return [{"name": r[0], "value": float(r[1] or 0), "count": r[2]} for r in rows]

def _package_canonical_sq():
    """套餐归一标量子查询 (#247 B3-2, A-20261007-002 §3.5)

    SCD 时点关联: 取"退款事件发生时生效"的别名快照, 不回改历史数字。
    事件时点 = COALESCE(created_at_raw, created_at) 的日期部分。
    匹配优先级: 商户级别名(merchant_pk 命中) > 全局别名(merchant_pk IS NULL)。
    未命中返回 NULL, 由调用方回落原始名。
    """
    event_date = func.date(func.coalesce(Refund.created_at_raw, Refund.created_at))
    merchant_sq = (
        select(Merchant.id)
        .where(Merchant.sub_name == Refund.merchant_name)
        .limit(1)
        .scalar_subquery()
    )
    return (
        select(ComboDict.canonical_name)
        .select_from(ComboNameAlias)
        .join(ComboDict, ComboDict.combo_code == ComboNameAlias.combo_code)
        .where(
            ComboNameAlias.raw_package_name == Refund.package_name,
            ComboNameAlias.effective_from <= event_date,
            or_(ComboNameAlias.effective_to.is_(None), ComboNameAlias.effective_to > event_date),
            or_(
                ComboNameAlias.merchant_pk.is_(None),
                ComboNameAlias.merchant_pk == merchant_sq,
            ),
        )
        # 商户级别名优先于全局别名
        .order_by(case((ComboNameAlias.merchant_pk.isnot(None), 0), else_=1))
        .limit(1)
        .scalar_subquery()
    )


def category_by_package(db: Session, limit: int = 20) -> list[dict]:
    """按套餐汇总退款 【统一口径: 路线A 退费单-线上直退】

    #247 B3-2: group by 由原始名切换为归一码(SCD 时点关联), 未命中回落原始名;
    历史报表用事件发生时生效的别名快照, 不回改历史数字。
    """
    canonical = _package_canonical_sq()
    name_expr = func.coalesce(canonical, Refund.package_name)
    rows = db.execute(
        select(name_expr, func.coalesce(func.sum(Refund.refund_amount), 0), func.count())
        .where(Refund.package_name.isnot(None), *_route_a_online_refund_filter())
        .group_by(name_expr)
        .order_by(func.sum(Refund.refund_amount).desc())
        .limit(limit)
    ).all()
    return [{"name": r[0], "value": float(r[1] or 0), "count": r[2]} for r in rows]


def package_alias_coverage(db: Session) -> dict:
    """套餐归一覆盖率 (#247): 供看板卡片 ⓘ 说明"未命中原始名占比" """
    canonical = _package_canonical_sq()
    total, matched = db.execute(
        select(
            func.count(),
            func.count().filter(canonical.isnot(None)),
        ).where(Refund.package_name.isnot(None), *_route_a_online_refund_filter())
    ).one()
    unmatched = (total or 0) - (matched or 0)
    return {
        "total": total or 0,
        "matched": matched or 0,
        "unmatched": unmatched,
        "unmatched_ratio": float(unmatched / total) if total else 0.0,
    }



# ═══════════════════════════════════════════════════════════════════════
# #87 第一批投诉健康度指标（设计文档 A-20260914-001 §4.1）
# ═══════════════════════════════════════════════════════════════════════
# 交付 9 项指标（全部**新增**，不改既有 work_score/投诉口径）：
#   1 活跃SN基数 · 2 投诉率(全站) · 3 投诉率(对外) · 4 投诉率(对内)
#   5 组长负荷 · 6 投诉专员负荷 · 7 客服接待负荷
#   8 重复投诉率 · 9 重复投诉SN数
#
# ★★ 口径铁律（用户 2026-09-14 拍板）★★
#   · 投诉判据 = `complaint_scope.is_complaint_ticket_cond`（**空白 OR '投诉'**）
#     ⚠️ 用 `service_category LIKE '%投诉%'` 只能匹配 5 条 → 会得出假结论（本人已踩）
#   · 活跃 SN = 窗口内 `cs_records.device_sn` 去重（用户确认口径）
#   · 岗位归属 = `StaffAssignment` + **`is_current=true`**
#     ⚠️ 不可直读 `Person.position`（仅镜像）；不可用 `total`（131 vs current 38 会重复计历史）
#   · 处理人归属 = `PersonAlias.alias` 精确匹配（实测覆盖 100%）
#     ⚠️ `person_aliases` 含 `企微-董梦玲`/`组长-董梦玲`/`董梦玲` 多种写法，
#        别名表已覆盖 → **不需要** regexp 去前缀（那是另一套做法，勿混用）
#   · 负荷分子 = 窗口内**真实完成**（finish 落窗 且 finish−register ≥ 180s），与
#     `complaints._REAL_FINISH_EPOCH_SECS` 同源
#   · 客服负荷分子 = **接待数**（用户确认：客服分子用接待数，非投诉数）
#   · 「重复投诉率」**禁止**改叫「升级率」（代理指标 ≠ 真实升级率）
# ═══════════════════════════════════════════════════════════════════════
# 位置口径依据（用户 2026-09-14 拍板）:
#   · 投诉专员 = `specialty ∈ (内部投诉, 外部投诉)`（`specialties` 字典的 2 条，
#     实测 4 人：刘景山/苏紫依=内部投诉, 洪佳/陈玉红=外部投诉）
#     ⚠️ 这是**职能**维度，与「投诉来源桶」同名但语义不同，勿混淆
#   · 组长 = `position ∈ (组长, 副主管)`（实测该层承担 67% 投诉）
#   · 客服 = `position = 客服`
_COMPLAINT_SPECIALIST_SPECIALTIES = ("内部投诉", "外部投诉")

def complaint_health_metrics(
    db: Session,
    date: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """#87 第一批投诉健康度指标（活跃SN / 投诉率三维 / 三岗位负荷 / 重复投诉）

    返回结构:
      {
        window: {start, end},
        active_sn: int,                       # 活跃 SN 基数（所有比率的分母基底）
        complaint_sn: int,                    # 投诉 SN（去重）
        rates: {overall, external, internal},  # 投诉率 三维（%）
        by_position: [{bucket, count, headcount, per_capita, unit}],
        repeat: {repeat_tickets, repeat_sn, repeat_rate, distribution[]},
        _spec: {...}                          # 口径说明（前端 ⓘ 直接引用）
      }

    ⚠️ 空数据的含义（前端 ⓘ 必须写明）:
      · active_sn = 0 → 该窗口**无接待记录**，所有比率无法计算（返回 None 而非 0）
      · rates.x = None → 分母为 0，**不是 0%**（前端应显示「—」而非 0%）
    """
    ts, te, _, _, _, _ = _resolve_windows(date=date, start=start, end=end)

    # ── 1. 活跃 SN 基数（分母基底）──────────────────────────────
    # 口径: 窗口内 `cs_records.operation_time` 命中的去重 `device_sn`
    active_sn = int(
        db.scalar(
            select(func.count(func.distinct(CsRecord.device_sn))).where(
                CsRecord.operation_time >= ts,
                CsRecord.operation_time <= te,
                CsRecord.device_sn.isnot(None),
            )
        )
        or 0
    )

    # ── 2-4. 投诉 SN（按来源桶）→ 投诉率三维 ──────────────────────
    # 判据统一走 complaint_scope（空白 OR '投诉'）；来源分桶复用 analytics 薄封装。
    _is_c = is_complaint_ticket_cond(ServiceTicket.service_category)
    _src = _complaint_source_type_case()
    _win = (
        ServiceTicket.register_time >= ts,
        ServiceTicket.register_time <= te,
        ServiceTicket.device_sn.isnot(None),
    )
    sn_by_src: dict[str, int] = {}
    for bucket in ALL_BUCKETS:
        n = db.scalar(
            select(func.count(func.distinct(ServiceTicket.device_sn))).where(
                _is_c, _src == bucket, *_win
            )
        )
        sn_by_src[bucket] = int(n or 0)

    complaint_sn = int(
        db.scalar(
            select(func.count(func.distinct(ServiceTicket.device_sn))).where(_is_c, *_win)
        )
        or 0
    )
    complaint_tickets = int(
        db.scalar(select(func.count()).select_from(ServiceTicket).where(_is_c, *_win))
        or 0
    )

    def _rate(num: int) -> float | None:
        """比率 = 分子/活跃SN。分母 0 → None（不是 0%）。"""
        if not active_sn:
            return None
        return round(num * 100.0 / active_sn, 2)

    rates = {
        "overall": _rate(complaint_sn),
        "external": _rate(sn_by_src.get(BUCKET_EXTERNAL, 0)),
        "internal": _rate(sn_by_src.get(BUCKET_INTERNAL, 0)),
    }

    # ── 5-7. 三岗位负荷 ────────────────────────────────────────
    # 分子: 投诉专员/组长 = 窗口内**真实完成**的投诉数（唯一出口 _sources_reg_handled 的
    #       handled 全量口径 = 三源 handled 之和）；客服 = **接待数**（用户拍板）。
    # 分母: 在岗人数（按别名的 bucket 归类）。
    try:
        _tot = _complaint_reg_handled_total(db, ts, te)
        complaint_handled = int(_tot.get("handled", 0))
    except Exception as _e:  # noqa: BLE001
        logger.warning("[#87] 投诉 handled 汇总失败(降级为0): %s", _e)
        complaint_handled = 0

    alias_map = _person_position_map(db)

    # 窗口内实际处理人 → 投诉完成数（用于「有效率」辅助列，非主分子）
    h_expr = handler_fallback_expr(ServiceTicket)
    handler_rows = db.execute(
        select(h_expr.label("h"), func.count())
        .where(
            _is_c,
            ServiceTicket.finish_time.isnot(None),
            ServiceTicket.register_time.isnot(None),
            func.extract("epoch", ServiceTicket.finish_time - ServiceTicket.register_time)
            >= REAL_FINISH_EPOCH_SECS,
            ServiceTicket.finish_time >= ts,
            ServiceTicket.finish_time <= te,
        )
        .group_by("h")
    ).all()

    # 客服接待数（按操作人，取接待会话数）
    reception_rows = db.execute(
        select(CsRecord.operator, func.count())
        .where(
            CsRecord.operation_time >= ts,
            CsRecord.operation_time <= te,
            CsRecord.operator.isnot(None),
        )
        .group_by(CsRecord.operator)
    ).all()

    # 在岗人数（按 bucket）—— 只统计**有别名**的人，避免重复
    headcount: dict[str, int] = {}
    seen_pids: dict[str, set] = {}
    for _alias, info in alias_map.items():
        b = info["bucket"]
        pid = info.get("person_id")
        key = pid if pid else _alias
        seen_pids.setdefault(b, set())
        if key in seen_pids[b]:
            continue
        seen_pids[b].add(key)
        headcount[b] = headcount.get(b, 0) + 1

    def _sum_by_bucket(rows, name_idx=0) -> dict[str, int]:
        acc: dict[str, int] = {}
        for r in rows:
            raw = r[name_idx]
            if raw is None:
                continue
            info = alias_map.get(str(raw).strip())
            b = info["bucket"] if info else "其他"
            acc[b] = acc.get(b, 0) + int(r[1] or 0)
        return acc

    done_by_bucket = _sum_by_bucket(handler_rows)
    recv_by_bucket = _sum_by_bucket(reception_rows)

    # ★ 负荷分子必须**按岗位归因**（用户 2026-09-14 拍板：分子按岗位取数）
    #   ⚠️ 曾错误地把「全站投诉结案总数」同时当作组长与专员的分子 →
    #      两者人均都等于 1307，完全无意义（同一分母比同一分子）。
    #   正解：组长/专员分子 = 窗口内**由该岗位处理人完成**的投诉数；
    #        客服分子 = 该岗位的接待数。全站结案总数另行给出作参照。
    # ★ 负荷分子必须**按岗位归因**（用户 2026-09-14 拍板：分子按岗位取数）
    #   ⚠️ 曾错误地把「全站投诉结案总数」同时当作组长与专员的分子 →
    #      两者人均都等于 1307，完全无意义（同一分母比同一分子）。
    #   正解：组长/专员分子 = 窗口内**由该岗位处理人完成**的投诉数；
    #        客服分子 = 该岗位的接待数。全站结案总数另行给出作参照。
    _NUMERATOR_BUCKETS = (
        # (bucket, 归因分子, 单位, 分子口径说明)
        ("组长", int(done_by_bucket.get("组长", 0)), "单", "由组长/副主管完成的投诉结案数"),
        ("投诉专员", int(done_by_bucket.get("投诉专员", 0)), "单", "由投诉专员完成的投诉结案数"),
        ("客服", int(recv_by_bucket.get("客服", 0)), "次", "客服接待数（接待会话）"),
    )
    by_position: list[dict[str, Any]] = []
    for b, numerator, unit, basis in _NUMERATOR_BUCKETS:
        hc = int(headcount.get(b, 0))
        by_position.append(
            {
                "bucket": b,
                "headcount": hc,
                "numerator": numerator,
                "numerator_basis": basis,
                "per_capita": round(numerator / hc, 1) if hc else None,
                "unit": unit,
            }
        )

    # ★ 兼办归属（仅投诉维度）：完成投诉但岗位**不属于**组长/投诉专员的人。
    #   为什么必须显式给出: 实测发现岗位**临时调配**（一线客服/未登记岗位也在办投诉），
    #   且专项标签与实际工作存在偏差（见设计文档 §3.6）。
    #   若不单列，这部分工作量会"消失"在归因差额里 → 使用者误判为数据缺失。
    #   ⚠️ 只统计投诉维度（接待是客服本职，把客服的接待再列一遍属重复展示）。
    _attributed_complaint = int(done_by_bucket.get("组长", 0)) + int(
        done_by_bucket.get("投诉专员", 0)
    )
    _other_complaint = int(done_by_bucket.get("其他", 0))
    if _other_complaint:
        # ⚠️ **不给人均** —— 「其他」桶是所有未登记岗位/主管的集合（实测 239 人，
        #    接近全员），拿它当"团队规模"算人均会得到 2.4 单/人这种**无意义数字**
        #    （分母不是"兼办团队"，而是"没登记岗位的所有人"）。
        #    本行的价值是**暴露工作量迁移**（有多少投诉由非专职人员承担），不是评价个人。
        by_position.append(
            {
                "bucket": "兼办投诉",
                "headcount": None,
                "numerator": _other_complaint,
                "numerator_basis": "由兼职/临时调配人员完成的投诉结案数（非组长/专员本职）"
                "—— 本行只反映工作量迁移，**不计算人均**（分母非真实团队规模）",
                "per_capita": None,
                "unit": "单",
            }
        )

    # ── 8-9. 重复投诉（30 天内同 SN ≥2 次）────────────────────────
    # ★ 这是「升级」的**代理信号**，非真实升级率（真实值待升级投诉表到位）
    # 口径: 对窗口内投诉按 device_sn 排序，LAG 取上一次投诉时间，
    #       相邻两次间隔 ≤30 天 → 本次计为「重复投诉」。
    repeat_tickets = int(
        db.scalar(
            text(
                """
                WITH t AS (
                  SELECT device_sn, register_time,
                         LAG(register_time) OVER (
                           PARTITION BY device_sn ORDER BY register_time
                         ) AS prev
                  FROM service_tickets
                  WHERE register_time BETWEEN :ts AND :te
                    AND device_sn IS NOT NULL
                    AND (service_category = '投诉'
                         OR service_category IS NULL
                         OR btrim(service_category) = '')
                )
                SELECT count(*) FROM t
                WHERE prev IS NOT NULL
                  AND register_time - prev <= interval '30 days'
                """
            ),
            {"ts": ts, "te": te},
        )
        or 0
    )
    repeat_sn = int(
        db.scalar(
            text(
                """
                WITH t AS (
                  SELECT device_sn, register_time,
                         LAG(register_time) OVER (
                           PARTITION BY device_sn ORDER BY register_time
                         ) AS prev
                  FROM service_tickets
                  WHERE register_time BETWEEN :ts AND :te
                    AND device_sn IS NOT NULL
                    AND (service_category = '投诉'
                         OR service_category IS NULL
                         OR btrim(service_category) = '')
                ), m AS (
                  SELECT device_sn,
                         count(*) FILTER (
                           WHERE prev IS NOT NULL
                             AND register_time - prev <= interval '30 days'
                         ) AS reps
                  FROM t GROUP BY device_sn
                )
                SELECT count(*) FROM m WHERE reps > 0
                """
            ),
            {"ts": ts, "te": te},
        )
        or 0
    )
    dist_rows = db.execute(
        text(
            """
            WITH t AS (
              SELECT device_sn, register_time,
                     LAG(register_time) OVER (
                       PARTITION BY device_sn ORDER BY register_time
                     ) AS prev
              FROM service_tickets
              WHERE register_time BETWEEN :ts AND :te
                AND device_sn IS NOT NULL
                AND (service_category = '投诉'
                     OR service_category IS NULL
                     OR btrim(service_category) = '')
            ), m AS (
              SELECT device_sn,
                     count(*) FILTER (
                       WHERE prev IS NOT NULL
                         AND register_time - prev <= interval '30 days'
                     ) AS reps
              FROM t GROUP BY device_sn
            )
            SELECT reps, count(*) FROM m GROUP BY reps ORDER BY reps LIMIT 8
            """
        ),
        {"ts": ts, "te": te},
    ).all()

    repeat_rate = (
        round(repeat_tickets * 100.0 / complaint_tickets, 2) if complaint_tickets else None
    )

    return {
        "active_sn": active_sn,
        "complaint_sn": complaint_sn,
        "complaint_tickets": complaint_tickets,
        "complaint_handled": complaint_handled,
        "attribution": {
            "total_handled": int(sum(done_by_bucket.values())),
            "by_bucket": {k: int(v) for k, v in done_by_bucket.items()},
        },
        "rates": rates,
        "sn_by_source": sn_by_src,
        "by_position": by_position,
        "repeat": {
            "repeat_tickets": repeat_tickets,
            "repeat_sn": repeat_sn,
            "repeat_rate": repeat_rate,
            "distribution": [{"reps": int(r[0] or 0), "sn_count": int(r[1] or 0)} for r in dist_rows],
        },
        "_spec": {
            "active_sn": "活跃SN = 窗口内 cs_records 有接待记录的去重设备SN（所有投诉率的分母基底）。"
            "『0』表示该窗口无接待记录，此时所有比率显示『—』而非 0%。",
            "rate_overall": "投诉率(全站) = 投诉SN / 活跃SN。投诉判据 = 工单服务类别为空白或『投诉』"
            "（客服登记投诉时不选类别，故空白即投诉）。**不含 (外)卡商**（#53 卡商独立统计）。",
            "rate_external": "投诉率(对外) = 外部投诉SN / 活跃SN（来源桶=外部投诉，含 315 补充表净新增，#58 软纠正后口径）",
            "rate_internal": "投诉率(对内) = 内部投诉SN / 活跃SN（来源桶=内部投诉，工单直接登记未走外部渠道）",
            "by_position": "岗位负荷 = 岗位归因分子 / 在岗人数。"
            "组长与投诉专员分子 = 窗口内由该岗位人员完成的投诉结案数（真实完成：finish 落窗且 finish−登记 ≥3 分钟）；"
            "客服分子 = 接待数。岗位归属取 person.position / specialty（已与 SCD 任职源同步），"
            "投诉专员 = 专项∈(内部投诉, 外部投诉)；组长含副主管。"
            "『其他』岗位（未登记岗位/主管）不列入本表，但已计入 attribution.total_handled，"
            "故 组长+专员+其他 = attribution.total_handled。",
            "repeat_rate": "★ 重复投诉率 = 『30 天内同一设备(SN)被投诉 ≥2 次』的条数 / 投诉工单数。"
            "**这是升级的代理信号，不是真实升级率** —— 真实升级率需升级投诉表（申报/化解字段）"
            "到位后才能严格计算，本指标用于在数据缺口期提供可观测的替代视角。",
            "repeat_distribution": "重复次数分布：reps=0 表示窗口内该 SN 只投诉 1 次（正常）。",
        },
    }

def _complaint_reg_handled_total(db: Session, ts, te) -> dict[str, int]:
    """投诉三源 registered/handled 合计（#87 负荷分子用）。

    ⚠️ 复用 `api/v1/complaints._sources_reg_handled`（**投诉总量的唯一出口**，#81），
       惰性导入避免循环依赖 —— 与 `today_kpi` 对投诉数的处理方式一致。
    """
    from app.api.v1.complaints import _sources_reg_handled

    data = _sources_reg_handled(db, ts, te)
    total = data.get("total") or {}
    return {
        "registered": int(total.get("registered", 0) or 0),
        "handled": int(total.get("handled", 0) or 0),
    }


def complaint_by_device_tag(
    db: Session,
    date: str | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """#249 B3-3: 投诉按设备标签组合维度聚合 (A-20261007-002 §3.6 标签事实化)

    口径:
      · 投诉判据 = complaint_scope.is_complaint_ticket_cond（空白 OR '投诉'），与 #87 完全一致
      · 关联: service_tickets.device_sn → device_tag_rel(status='active') → device_tags
      · 一台设备可有多个 active 标签 → 该 SN 的投诉单在**每个**所属标签各计一次
        （「组合维度」语义: 回答"哪些人群/批次的投诉最集中", 不是互斥分桶, 总和≠投诉总量）
      · 未打标 SN 单独归入 "(未打标)" 桶 —— 暴露标签覆盖率缺口

    返回: [{tag_id, tag_name, ticket_count, sn_count, refund_amount}] 按单量倒序。
    """
    from app.models.device import DeviceTag, device_tag_rel

    ts, te, _, _, _, _ = _resolve_windows(date=date, start=start, end=end)
    _is_c = is_complaint_ticket_cond(ServiceTicket.service_category)
    _win = (
        ServiceTicket.register_time >= ts,
        ServiceTicket.register_time <= te,
        ServiceTicket.device_sn.isnot(None),
    )

    # ── 已打标部分: 按标签分组 ──
    rows = db.execute(
        select(
            device_tag_rel.c.tag_id,
            func.count().label("ticket_count"),
            func.count(func.distinct(ServiceTicket.device_sn)).label("sn_count"),
            func.coalesce(func.sum(ServiceTicket.refund_amount), 0).label("amt"),
        )
        .join(
            device_tag_rel,
            and_(
                device_tag_rel.c.device_sn == ServiceTicket.device_sn,
                device_tag_rel.c.status == "active",
            ),
        )
        .where(_is_c, *_win)
        .group_by(device_tag_rel.c.tag_id)
        .order_by(func.count().desc())
        .limit(limit)
    ).all()
    tag_names = {t.id: t.tag_name for t in db.scalars(select(DeviceTag))}
    data = [
        {
            "tag_id": r.tag_id,
            "tag_name": tag_names.get(r.tag_id),
            "ticket_count": int(r.ticket_count),
            "sn_count": int(r.sn_count),
            "refund_amount": float(r.amt or 0),
        }
        for r in rows
    ]

    # ── 未打标部分: 投诉 SN 中无任何 active 标签的 ──
    untagged = db.execute(
        select(
            func.count().label("ticket_count"),
            func.count(func.distinct(ServiceTicket.device_sn)).label("sn_count"),
            func.coalesce(func.sum(ServiceTicket.refund_amount), 0).label("amt"),
        )
        .where(
            _is_c,
            *_win,
            ~ServiceTicket.device_sn.in_(
                select(device_tag_rel.c.device_sn).where(device_tag_rel.c.status == "active")
            ),
        )
    ).one()
    if int(untagged.ticket_count or 0):
        data.append(
            {
                "tag_id": None,
                "tag_name": "(未打标)",
                "ticket_count": int(untagged.ticket_count),
                "sn_count": int(untagged.sn_count),
                "refund_amount": float(untagged.amt or 0),
            }
        )
    return data


# ═══════════════════════════════════════════════════════════════════════
# #252 B3-11 投诉分析扩展维度（店铺/商户号/设备型号/套餐/激活后时长）
# ═══════════════════════════════════════════════════════════════════════

_ACTIVE_DAYS_BUCKETS = ("0-30天", "31-90天", "91-180天", "181-365天", ">365天", "(无激活记录)")


def _topn_with_other(rows: list, name_idx: int, cnt_idx: int, limit: int) -> list[dict]:
    """TopN + 其余归 (其他)；rows 已按计数降序。"""
    top = rows[:limit]
    rest = rows[limit:]
    out = [{"name": r[name_idx], "count": int(r[cnt_idx])} for r in top]
    if rest:
        out.append({"name": "(其他)", "count": sum(int(r[cnt_idx]) for r in rest)})
    return out


def complaint_extended_dims(
    db: Session,
    date: str | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int = 10,
) -> dict[str, list[dict]]:
    """#252 B3-11 (用户#5): 投诉工单按五维扩展分布。

    口径:
      · 投诉判据 = complaint_scope.is_complaint_ticket_cond（空白 OR '投诉'），与 #87 一致
      · 店铺/商户号: SN → device_shop_links / device_merchant_links 的 **is_current** 建链
        （回答「现在哪些店铺/商户在投诉」，运营动作面向当前归属；非投诉发生时点 SCD 快照）
      · 设备型号: devices.device_type_code → dim_device_type.type_name，未命中回落原始
        device_type 文本，再空归 (未知类型)
      · 套餐: devices.current_combo_name → combo_name_alias(is_current) → combo_dict
        归一名，未命中回落原始名，空归 (无套餐)
      · 激活后时长: register_time − devices.active_time 天数分桶（上海时区日期差）
      · 每维 TopN + 其余归 (其他)；未建链显式分桶暴露覆盖缺口
    """
    ts, te, _, _, _, _ = _resolve_windows(date=date, start=start, end=end)
    _is_c = is_complaint_ticket_cond(ServiceTicket.service_category)
    _win = (
        ServiceTicket.register_time >= ts,
        ServiceTicket.register_time <= te,
        ServiceTicket.device_sn.isnot(None),
    )
    cnt = func.count()

    # ── 1. 店铺（通路② 归属建链, 未建链暴露缺口）──
    shop_rows = db.execute(
        select(func.coalesce(Shop.shop_name, "(未建链店铺)"), cnt)
        .select_from(ServiceTicket)
        .outerjoin(
            DeviceShopLink,
            and_(
                DeviceShopLink.device_sn == ServiceTicket.device_sn,
                DeviceShopLink.is_current.is_(True),
            ),
        )
        .outerjoin(Shop, Shop.id == DeviceShopLink.shop_pk)
        .where(_is_c, *_win)
        .group_by(func.coalesce(Shop.shop_name, "(未建链店铺)"))
        .order_by(cnt.desc())
    ).all()

    # ── 2. 商户号（通路① 交易建链, 未建链暴露缺口）──
    merchant_rows = db.execute(
        select(func.coalesce(Merchant.sub_name, "(未建链商户号)"), cnt)
        .select_from(ServiceTicket)
        .outerjoin(
            DeviceMerchantLink,
            and_(
                DeviceMerchantLink.device_sn == ServiceTicket.device_sn,
                DeviceMerchantLink.is_current.is_(True),
            ),
        )
        .outerjoin(Merchant, Merchant.id == DeviceMerchantLink.merchant_pk)
        .where(_is_c, *_win)
        .group_by(func.coalesce(Merchant.sub_name, "(未建链商户号)"))
        .order_by(cnt.desc())
    ).all()

    # ── 3. 设备型号（归一码 → 中文名, 回落原始文本）──
    type_name = func.coalesce(DimDeviceType.type_name, Device.device_type, "(未知类型)")
    dtype_rows = db.execute(
        select(type_name, cnt)
        .select_from(ServiceTicket)
        .outerjoin(Device, Device.sn == ServiceTicket.device_sn)
        .outerjoin(DimDeviceType, DimDeviceType.type_code == Device.device_type_code)
        .where(_is_c, *_win)
        .group_by(type_name)
        .order_by(cnt.desc())
    ).all()

    # ── 4. 套餐（当前别名归一, 未命中回落原始名）──
    canonical = (
        select(ComboDict.canonical_name)
        .select_from(ComboNameAlias)
        .join(ComboDict, ComboDict.combo_code == ComboNameAlias.combo_code)
        .where(
            ComboNameAlias.raw_package_name == Device.current_combo_name,
            ComboNameAlias.is_current.is_(True),
        )
        .order_by(case((ComboNameAlias.merchant_pk.isnot(None), 0), else_=1))
        .limit(1)
        .scalar_subquery()
    )
    pkg_name = func.coalesce(canonical, Device.current_combo_name, "(无套餐)")
    pkg_rows = db.execute(
        select(pkg_name, cnt)
        .select_from(ServiceTicket)
        .outerjoin(Device, Device.sn == ServiceTicket.device_sn)
        .where(_is_c, *_win)
        .group_by(pkg_name)
        .order_by(cnt.desc())
    ).all()

    # ── 5. 激活后时长（上海时区日期差分桶）──
    days_expr = (
        func.date(ServiceTicket.register_time.op("AT TIME ZONE")("Asia/Shanghai"))
        - func.date(Device.active_time.op("AT TIME ZONE")("Asia/Shanghai"))
    )
    bucket_expr = case(
        (Device.active_time.is_(None), "(无激活记录)"),
        (days_expr <= 30, "0-30天"),
        (days_expr <= 90, "31-90天"),
        (days_expr <= 180, "91-180天"),
        (days_expr <= 365, "181-365天"),
        else_=">365天",
    )
    active_rows = db.execute(
        select(bucket_expr, cnt)
        .select_from(ServiceTicket)
        .outerjoin(Device, Device.sn == ServiceTicket.device_sn)
        .where(_is_c, *_win)
        .group_by(bucket_expr)
    ).all()
    active_map = {r[0]: int(r[1]) for r in active_rows}
    by_active_days = [
        {"name": b, "count": active_map.get(b, 0)} for b in _ACTIVE_DAYS_BUCKETS
    ]

    return {
        "by_shop": _topn_with_other(shop_rows, 0, 1, limit),
        "by_merchant": _topn_with_other(merchant_rows, 0, 1, limit),
        "by_device_type": _topn_with_other(dtype_rows, 0, 1, limit),
        "by_package": _topn_with_other(pkg_rows, 0, 1, limit),
        "by_active_days": by_active_days,
    }
