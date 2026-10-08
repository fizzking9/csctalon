"""夜间投诉风险预警 v1 —— 确定性评分引擎 + 投诉专员优先清单 (#250 B3-4)

设计说明（Design Rationale）:
- 目的(替谁/答何决策): 替投诉专员回答「今晚/无人值守时段, 哪些设备最可能升级为
  外部投诉(工信部/12315)」, 按风险分降序出优先清单, 上班时间提前介入。
- 逻辑(为何这样组织): v1 用**确定性规则引擎**(不依赖 LLM)——四维事实打分:
  ① 充值多(高价值用户, 升级损失大) ② 流量少(疑似限速/信号差, 投诉前兆)
  ③ 愿沟通(近期有接待记录, 有沟通意愿不处理易升级) ④ 同题重复(历史投诉未根治,
  升级最强信号, 权重最高)。评分全程可解释: 每项得分都能下钻到原始事实行。
- 口径: 投诉判据 = 工单 service_category 空白或='投诉'(complaint_scope 唯一出口);
  外部投诉 = external_complaints 近 180 天; 同源组 = complaint_same_source_groups
  status in (auto/confirmed); 流量取最近完整月(month_complete=True)。
- 取舍(不做什么及原因): v1 不接 LLM(规则先行, 后续评估增强); 不做自动推送
  (只出清单, 专员主动看); 不做配置表(权重/阈值走函数参数常量, 配置化属 O-5 择机项);
  不做前端独立页面(本单只交后端 API + 测试, 前端接入另开工单)。
- 工单: #250 · 排期: docs/reports/20261009-下一步开发计划-排期规划.md §六 B3-4
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.complaint_same_source import ComplaintSameSourceGroup
from app.models.cs_record import CsRecord
from app.models.device import Device
from app.models.device_combo import DeviceCombo
from app.models.device_flow_usage import DeviceFlowUsage
from app.models.external_complaint import ExternalComplaint
from app.models.ticket import ServiceTicket
from app.services import complaint_scope

# ── 默认权重与阈值(v1 拍板值, 可调参数覆盖; 配置表化属 O-5 择机项) ──
DEFAULT_WEIGHTS: dict[str, int] = {
    "recharge": 25,   # 充值多: 高价值用户升级损失大
    "low_flow": 25,   # 流量少: 疑似限速/信号差
    "contact": 20,    # 愿沟通: 有沟通意愿, 不处理易升级
    "repeat": 30,     # 同题重复: 历史未根治是升级最强信号, 权重最高
}

DEFAULT_RECHARGE_DAYS = 90        # 充值回看窗口(天)
DEFAULT_CONTACT_DAYS = 30         # 接待回看窗口(天)
DEFAULT_COMPLAINT_DAYS = 90       # 投诉工单回看窗口(天)
DEFAULT_EXTERNAL_DAYS = 180       # 外部投诉回看窗口(天)
DEFAULT_LOW_FLOW_GB = 5.0         # 流量少阈值(GB, 最近完整月)
DEFAULT_RECHARGE_HIGH_AMOUNT = Decimal("500")  # 高价值充值金额阈值(元)

LEVEL_BY_SCORE = ((70, "critical"), (45, "high"), (25, "medium"))


def _recharge_score(cnt: int, amount: Decimal, high_amount: Decimal) -> int:
    """充值多维评分: 0 笔=0; 1 笔=40; 2 笔=70; ≥3 笔或金额≥阈值=100。"""
    if cnt <= 0:
        return 0
    if cnt >= 3 or amount >= high_amount:
        return 100
    if cnt == 2:
        return 70
    return 40


def _flow_score(flow_gb: float | None, low_gb: float) -> int:
    """流量少维评分: 无记录=30(未知中性); <阈值=100; <2×阈值=60; 否则=10。"""
    if flow_gb is None:
        return 30
    if flow_gb < low_gb:
        return 100
    if flow_gb < low_gb * 2:
        return 60
    return 10


def _contact_score(cnt: int) -> int:
    """愿沟通维评分: 0 次=0; 1 次=50; 2 次=75; ≥3 次=100。"""
    if cnt <= 0:
        return 0
    if cnt == 1:
        return 50
    if cnt == 2:
        return 75
    return 100


def _repeat_score(ticket_cnt: int, in_same_source: bool, external_cnt: int) -> int:
    """同题重复维评分: 投诉工单 each 25(上限75) + 同源组 15 + 外部投诉 each 10, 封顶 100。"""
    score = min(ticket_cnt * 25, 75)
    if in_same_source:
        score += 15
    score += external_cnt * 10
    return min(score, 100)


def _level(total: float) -> str:
    for threshold, name in LEVEL_BY_SCORE:
        if total >= threshold:
            return name
    return "low"


def _fetch_recharge_facts(db: Session, since: datetime) -> dict[str, dict[str, Any]]:
    """近 N 天每台设备充值笔数/金额(device_combos, 金额元 Decimal)。"""
    stmt = (
        select(
            DeviceCombo.device_sn,
            func.count().label("cnt"),
            func.coalesce(func.sum(DeviceCombo.actual_amount), 0).label("amount"),
        )
        .where(DeviceCombo.device_sn.isnot(None))
        .where(DeviceCombo.created_at_raw >= since)
        .group_by(DeviceCombo.device_sn)
    )
    return {
        r.device_sn: {"cnt": int(r.cnt), "amount": Decimal(str(r.amount))}
        for r in db.execute(stmt)
    }


def _fetch_flow_facts(db: Session) -> dict[str, float | None]:
    """每台设备最近完整月流量 GB(device_flow_usage, month_complete=True)。"""
    # 子查询取每设备最近完整月, 再回表取该月 flow_gb
    latest = (
        select(
            DeviceFlowUsage.device_sn,
            func.max(DeviceFlowUsage.stat_month).label("m"),
        )
        .where(DeviceFlowUsage.device_sn.isnot(None))
        .where(DeviceFlowUsage.month_complete.is_(True))
        .group_by(DeviceFlowUsage.device_sn)
        .subquery()
    )
    stmt = (
        select(DeviceFlowUsage.device_sn, DeviceFlowUsage.flow_gb)
        .join(
            latest,
            (DeviceFlowUsage.device_sn == latest.c.device_sn)
            & (DeviceFlowUsage.stat_month == latest.c.m),
        )
        .where(DeviceFlowUsage.month_complete.is_(True))
    )
    return {
        r.device_sn: (float(r.flow_gb) if r.flow_gb is not None else None)
        for r in db.execute(stmt)
    }


def _fetch_contact_facts(db: Session, since: datetime) -> dict[str, int]:
    """近 N 天每台设备接待次数(cs_records)。"""
    stmt = (
        select(CsRecord.device_sn, func.count().label("cnt"))
        .where(CsRecord.device_sn.isnot(None))
        .where(CsRecord.operation_time >= since)
        .group_by(CsRecord.device_sn)
    )
    return {r.device_sn: int(r.cnt) for r in db.execute(stmt)}


def _fetch_repeat_facts(
    db: Session, ticket_since: datetime, external_since: datetime
) -> dict[str, dict[str, Any]]:
    """同题重复三维事实: 近 N 天投诉工单数 / 是否同源组 / 近 N 天外部投诉数。"""
    ticket_stmt = (
        select(ServiceTicket.device_sn, func.count().label("cnt"))
        .where(ServiceTicket.device_sn.isnot(None))
        .where(complaint_scope.is_complaint_ticket_cond(ServiceTicket.service_category))
        .where(ServiceTicket.register_time >= ticket_since)
        .group_by(ServiceTicket.device_sn)
    )
    tickets = {r.device_sn: int(r.cnt) for r in db.execute(ticket_stmt)}

    group_stmt = (
        select(ComplaintSameSourceGroup.device_sn)
        .where(ComplaintSameSourceGroup.status.in_(("auto", "confirmed")))
        .group_by(ComplaintSameSourceGroup.device_sn)
    )
    same_source = {r.device_sn for r in db.execute(group_stmt)}

    ext_stmt = (
        select(ExternalComplaint.device_sn, func.count().label("cnt"))
        .where(ExternalComplaint.device_sn.isnot(None))
        .where(ExternalComplaint.complaint_date >= external_since)
        .group_by(ExternalComplaint.device_sn)
    )
    externals = {r.device_sn: int(r.cnt) for r in db.execute(ext_stmt)}

    sns = set(tickets) | set(same_source) | set(externals)
    return {
        sn: {
            "ticket_cnt": tickets.get(sn, 0),
            "in_same_source": sn in same_source,
            "external_cnt": externals.get(sn, 0),
        }
        for sn in sns
    }


def compute_complaint_risk_priority_list(
    db: Session,
    *,
    limit: int = 100,
    min_score: float = 0.0,
    weights: dict[str, int] | None = None,
    recharge_days: int = DEFAULT_RECHARGE_DAYS,
    contact_days: int = DEFAULT_CONTACT_DAYS,
    complaint_days: int = DEFAULT_COMPLAINT_DAYS,
    external_days: int = DEFAULT_EXTERNAL_DAYS,
    low_flow_gb: float = DEFAULT_LOW_FLOW_GB,
    recharge_high_amount: Decimal = DEFAULT_RECHARGE_HIGH_AMOUNT,
) -> dict[str, Any]:
    """投诉专员优先清单: 全量设备四维评分, 按总分降序取 top N。

    返回 {total_scored, items: [{device_sn, total, level, scores{...}, facts{...}}]}。
    只输出至少有任一风险信号(总分>0)的设备——无信号设备不构成专员动作对象。
    """
    w = dict(DEFAULT_WEIGHTS if weights is None else weights)
    now = datetime.now(UTC)

    recharges = _fetch_recharge_facts(db, now - timedelta(days=recharge_days))
    flows = _fetch_flow_facts(db)
    contacts = _fetch_contact_facts(db, now - timedelta(days=contact_days))
    repeats = _fetch_repeat_facts(
        db, now - timedelta(days=complaint_days), now - timedelta(days=external_days)
    )

    # 设备全集: 只评有任一维度事实的 SN(全部 2000+ 设备逐台评, 但无信号的不输出)
    all_sns = set(recharges) | set(flows) | set(contacts) | set(repeats)
    # 过滤掉已不在 devices 主表的孤儿 SN(采集残留)
    valid_sns = {
        r.sn for r in db.execute(
            select(Device.sn).where(Device.sn.in_(all_sns))
        )
    } if all_sns else set()

    items: list[dict[str, Any]] = []
    for sn in valid_sns:
        rc = recharges.get(sn, {"cnt": 0, "amount": Decimal("0")})
        flow_gb = flows.get(sn)
        contact_cnt = contacts.get(sn, 0)
        rp = repeats.get(sn, {"ticket_cnt": 0, "in_same_source": False, "external_cnt": 0})

        s_recharge = _recharge_score(rc["cnt"], rc["amount"], recharge_high_amount)
        s_flow = _flow_score(flow_gb, low_flow_gb)
        s_contact = _contact_score(contact_cnt)
        s_repeat = _repeat_score(rp["ticket_cnt"], rp["in_same_source"], rp["external_cnt"])

        total = (
            s_recharge * w["recharge"]
            + s_flow * w["low_flow"]
            + s_contact * w["contact"]
            + s_repeat * w["repeat"]
        ) / 100.0

        if total <= 0 or total < min_score:
            continue
        items.append({
            "device_sn": sn,
            "total": round(total, 1),
            "level": _level(total),
            "scores": {
                "recharge": s_recharge,
                "low_flow": s_flow,
                "contact": s_contact,
                "repeat": s_repeat,
            },
            "facts": {
                "recharge_cnt": rc["cnt"],
                # 出参边界: Decimal → float 一次性表示层转换(AGENTS 铁律4 C′ 方案)
                "recharge_amount_yuan": float(rc["amount"]),
                "latest_month_flow_gb": flow_gb,
                "contact_cnt_30d": contact_cnt,
                "complaint_ticket_cnt_90d": rp["ticket_cnt"],
                "in_same_source_group": rp["in_same_source"],
                "external_complaint_cnt_180d": rp["external_cnt"],
            },
        })

    items.sort(key=lambda x: x["total"], reverse=True)
    return {"total_scored": len(items), "items": items[:limit]}
