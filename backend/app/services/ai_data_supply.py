"""AI 数据供给层(统一聚合, #237 / B2-2)

定位: 设备 / 充值流量 / 设备标签 / 排班 四类"实体维度"数据的**单一聚合真身**,
同时供给两侧, 保证口径一致:
  ① 外部开放平台 app/api/v1/external.py 的结构化 JSON 端点(供 talon 等工具 token+scope 拉取);
  ② 内部 AI 问答 app/services/ai_feed.py 的条件注入段(build_entity_context)。

设计取舍(工单 #237):
- 纯只读聚合, 不写库; 不重复造 analytics 既有口径, 设备 KPI 复用 device_profile,
  设备时间线复用 audit_timeline.build_device_timeline。
- 金额: DB 为 Numeric(元), 本层在**出参边界**转 float(元, #217 方案 C′);
  流量: device_combos 存字节, 同时给字节/GB/人类可读; -1=无限流量包不计入剩余字节合计。
- 客户标签/客户主数据强依赖尚未建设的客户域(B3-1), 本层**不含**客户标签, 推迟 B3-3。
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import re
from datetime import date, datetime, time

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.models.device import Device, DeviceTag, device_tag_rel
from app.models.device_combo import DeviceCombo
from app.models.person import Person
from app.models.schedule import Attendance, Schedule, ShiftTemplate
from app.services.audit_timeline import build_device_timeline
from app.services.device_profile import build_device_profile

logger = logging.getLogger(__name__)

TZ_CN = _dt.timezone(_dt.timedelta(hours=8))

# 区间护栏: 与 external brief 一致, 单次实体区间不超过 1 季度, 防被当全量导出工具。
MAX_RANGE_DAYS = 93

# 设备 SN 候选形态(字母/数字/连字符/下划线, 6~32 位)。中文问句里的普通英文单词也会被
# 正则扫到, 故**必须再经 Device 表精确匹配**才认定 —— 以 DB 为权威, 零误报(宁可漏报)。
_SN_CANDIDATE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{5,31}")
# 单次问句最多核对的候选数, 防止超长问句产生大量 IN 参数。
_MAX_SN_CANDIDATES = 8

# 排班域问句关键词(命中才注入排班段, 不污染普通经营问答)。
_SCHEDULE_KEYWORDS = ("排班", "班次", "出勤", "考勤", "打卡", "顶班", "请假", "调班", "早班", "晚班")

# 明细类异常名单在文本注入时的封顶(避免长名单占满 prompt 预算)。
_TEXT_LIST_CAP = 20
_EXCEPTION_LIST_CAP = 50


def _now_cn() -> datetime:
    return datetime.now(TZ_CN)


def _bytes_to_gb(b: int | None) -> float | None:
    if b is None or b == -1:  # -1=无限流量包, 不参与 GB 换算
        return None
    return round(b / (1024.0 ** 3), 4)


def _bytes_to_human(b: int | None) -> str | None:
    if b is None:
        return None
    if b == -1:
        return "无限"
    step = 1024.0
    v = float(b)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(v) < step:
            return f"{v:.2f} {unit}"
        v /= step
    return f"{v:.2f} PB"


def _validate_range(start_s: str | None, end_s: str | None) -> tuple[date, date] | None:
    """校验纯日期区间成对/合法/不穿越/跨度 ≤ 上限。

    返回 (start_date, end_date); 两者皆空 → None(表示全量, 仅充值/标签允许)。
    非法 → ValueError(由路由层转 400)。
    """
    if start_s is None and end_s is None:
        return None
    if not start_s or not end_s:
        raise ValueError("start/end 必须成对提供(YYYY-MM-DD)")
    try:
        s = date.fromisoformat(start_s)
        e = date.fromisoformat(end_s)
    except ValueError as exc:
        raise ValueError(f"start/end 日期非法: {start_s} ~ {end_s}") from exc
    if s > e:
        raise ValueError(f"start({start_s}) 不能晚于 end({end_s})")
    if e > _now_cn().date():
        raise ValueError(f"end({end_s}) 不能晚于今天")
    span = (e - s).days + 1
    if span > MAX_RANGE_DAYS:
        raise ValueError(f"区间跨度 {span} 天超过上限 {MAX_RANGE_DAYS} 天")
    return s, e


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


# ─────────────────────────────────────────────────────────────────────
# ① 充值 & 流量
# ─────────────────────────────────────────────────────────────────────
def supply_recharge(db: Session, *, sn: str | None = None,
                    start: str | None = None, end: str | None = None) -> dict:
    """充值套餐与流量聚合(本地 device_combos 表)。

    口径与内部 GET /recharges/device-combos/local/summary 一致: 按 created_at_raw(充值时间)
    落区间; sn 精确; 返回笔数/金额(元)/可用·已用·剩余流量(字节)。
    额外补 unlimited_count(剩余流量=-1 的无限套餐数), 因为 -1 不应混入字节合计。
    """
    rng = _validate_range(start, end)
    stmt = select(DeviceCombo)
    if sn:
        stmt = stmt.where(DeviceCombo.device_sn == sn)
    if rng is not None:
        s, e = rng
        stmt = stmt.where(DeviceCombo.created_at_raw >= datetime.combine(s, time.min))
        stmt = stmt.where(DeviceCombo.created_at_raw <= datetime.combine(e, time.max))

    # 单条聚合查询拿全部 6 指标; 无限包用 CASE 求和计数(-1 语义)。
    # ⚠️ 聚合表达式必须引用 sub.c.*(子查询列); 若引用 DeviceCombo 原表列, SQLAlchemy 会把
    #    原表也加进 FROM, 与子查询形成笛卡尔积(计数翻倍)。
    sub = stmt.subquery()
    total, amount, avail, used, remain, unlimited = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(sub.c.actual_amount), 0.0),
            func.coalesce(func.sum(sub.c.available_traffic), 0),
            func.coalesce(func.sum(sub.c.used_traffic), 0),
            func.coalesce(func.sum(sub.c.remaining_traffic), 0),
            func.coalesce(func.sum(case((sub.c.remaining_traffic == -1, 1), else_=0)), 0),
        )
    ).one()

    avail_i, used_i, remain_i = int(avail or 0), int(used or 0), int(remain or 0)
    return {
        "filter": {"sn": sn, "start": start, "end": end},
        "generated_at": _now_cn().isoformat(),
        "recharge_count": int(total or 0),
        "total_amount": round(float(amount or 0.0), 2),
        "available_traffic": avail_i,
        "used_traffic": used_i,
        "remaining_traffic": remain_i,
        "available_traffic_gb": _bytes_to_gb(avail_i),
        "used_traffic_gb": _bytes_to_gb(used_i),
        "remaining_traffic_gb": _bytes_to_gb(remain_i),
        "available_traffic_human": _bytes_to_human(avail_i),
        "used_traffic_human": _bytes_to_human(used_i),
        "remaining_traffic_human": _bytes_to_human(remain_i),
        "unlimited_count": int(unlimited or 0),
        "_spec": {
            "source": "本地 device_combos 表(BMS 充值套餐快照)",
            "recharge_count": "窗口内充值套餐笔数(按 created_at_raw 充值时间)",
            "total_amount": "套餐实付金额合计, 单位=元, 2 位小数",
            "traffic": "available/used/remaining=可用/已用/剩余流量(字节); *_gb=按 1024 换算的 GB; *_human=人类可读",
            "unlimited_count": "剩余流量=-1 的无限套餐数; 无限包不参与剩余字节/GB 合计",
            "window": "start/end 为北京日历日(整天 00:00:00~23:59:59), 成对提供, 跨度≤93 天; 缺省=全量",
        },
    }


# ─────────────────────────────────────────────────────────────────────
# ② 设备标签(仅设备标签; 客户标签依赖 B3-1, 推迟 B3-3)
# ─────────────────────────────────────────────────────────────────────
def supply_device_tags(db: Session, *, sn: str | None = None) -> dict:
    """设备标签。sn 给定时返回该设备生效标签; 否则返回全标签各自绑定的设备 SN 数。"""
    if sn:
        rows = db.execute(
            select(DeviceTag.id, DeviceTag.tag_name, DeviceTag.color)
            .join(device_tag_rel, device_tag_rel.c.tag_id == DeviceTag.id)
            .where(device_tag_rel.c.device_sn == sn, device_tag_rel.c.status == "active")
            .order_by(DeviceTag.id)
        ).all()
        tags = [{"tag_id": i, "tag_name": n, "color": c} for i, n, c in rows]
        return {
            "sn": sn,
            "generated_at": _now_cn().isoformat(),
            "count": len(tags),
            "tags": tags,
            "_spec": {
                "binding": "device_tag_rel 中 status=active 的绑定, 关联 device_tags 取标签名/颜色",
                "note": "仅设备标签; 客户标签依赖客户域(B3-1), 暂不提供",
            },
        }

    rows = db.execute(
        select(device_tag_rel.c.tag_id, func.count())
        .where(device_tag_rel.c.status == "active")
        .group_by(device_tag_rel.c.tag_id)
    ).all()
    name_map = {t.id: (t.tag_name, t.color) for t in db.scalars(select(DeviceTag))}
    tags = [
        {
            "tag_id": tag_id,
            "tag_name": name_map.get(tag_id, (None, None))[0],
            "color": name_map.get(tag_id, (None, None))[1],
            "sn_count": int(cnt),
        }
        for tag_id, cnt in rows
    ]
    tags.sort(key=lambda x: -x["sn_count"])
    return {
        "generated_at": _now_cn().isoformat(),
        "tag_count": len(tags),
        "total_bindings": int(sum(t["sn_count"] for t in tags)),
        "tags": tags,
        "_spec": {
            "sn_count": "该标签下 status=active 的去重设备绑定数(device_tag_rel 按 tag_id 分组)",
            "note": "仅设备标签; 客户标签依赖客户域(B3-1), 暂不提供",
        },
    }


# ─────────────────────────────────────────────────────────────────────
# ③ 单设备行为(档案 KPI + 生命周期时间线)
# ─────────────────────────────────────────────────────────────────────
def supply_device_behavior(db: Session, sn: str, *,
                           timeline_limit: int = 50, timeline_days: int = 90) -> dict | None:
    """单设备档案 + 生命周期 KPI + 近期事件时间线。设备不存在 → None(路由层转 404)。"""
    device = db.scalar(select(Device).where(Device.sn == sn))
    if device is None:
        return None
    profile = build_device_profile(db, device)
    timeline = build_device_timeline(db, sn, limit=timeline_limit, days=timeline_days)
    return {
        "generated_at": _now_cn().isoformat(),
        "profile": profile,
        "timeline": {
            "window_days": timeline_days,
            "total": timeline.get("total"),
            "counts": timeline.get("counts"),
            "items": timeline.get("items"),
        },
        "_spec": {
            "profile": "设备档案 + 各业务表按 SN 精确匹配的生命周期计数; 金额 combo_total_amount 单位=元",
            "flow_total_gb": "device_flow_usage.flow_gb 月用量合计(GB)",
            "timeline": f"近 {timeline_days} 天内退款/工单/提现/投诉/充值/订单/退货等业务事件合并时间线, 封顶 {timeline_limit} 条",
        },
    }


# ─────────────────────────────────────────────────────────────────────
# ④ 排班 × 考勤 区间聚合(新写, 供 AI 与外部)
# ─────────────────────────────────────────────────────────────────────
def supply_schedule(db: Session, start_s: str, end_s: str) -> dict:
    """区间排班/考勤人天汇总。

    与单日 GET /schedules/comparison 的**出勤判定口径一致**(有上班或下班打卡即算出勤),
    但区间版只做人天级汇总/班次·状态分布/每日序列, 不重复单日"接待/工单别名对照"重逻辑。
    排班/考勤日期为北京日历日(Date 列), 直接按日期比较, 无时区换算。
    """
    rng = _validate_range(start_s, end_s)
    if rng is None:
        raise ValueError("排班聚合必须提供 start/end(YYYY-MM-DD)")
    s, e = rng

    sch_rows = db.execute(
        select(Schedule.person_id, Schedule.schedule_date,
               Schedule.shift_template_id, Schedule.status)
        .where(Schedule.schedule_date >= s, Schedule.schedule_date <= e)
    ).all()
    # 有任一打卡即记一个"出勤人天"; set 去重(同人同日可能多条打卡记录)。
    att_rows = db.execute(
        select(Attendance.person_id, Attendance.attendance_date)
        .where(
            Attendance.attendance_date >= s,
            Attendance.attendance_date <= e,
            or_(Attendance.check_in_at.isnot(None), Attendance.check_out_at.isnot(None)),
        )
    ).all()
    att_set = {(p, d) for p, d in att_rows}
    shifts = {t.id: t.name for t in db.scalars(select(ShiftTemplate))}
    persons = {p.id: p.display_name for p in db.scalars(select(Person))}

    sch_keys = [(pid, d) for pid, d, _, _ in sch_rows]
    scheduled_set = set(sch_keys)
    present_person_days = sum(1 for k in sch_keys if k in att_set)

    by_shift: dict[str, int] = {}
    by_status: dict[str, int] = {}
    daily: dict[str, dict] = {}
    for pid, d, shift_id, status in sch_rows:
        shift_name = shifts.get(shift_id) or "未指定班次"
        by_shift[shift_name] = by_shift.get(shift_name, 0) + 1
        by_status[status or "正常"] = by_status.get(status or "正常", 0) + 1
        cell = daily.setdefault(d.isoformat(), {"date": d.isoformat(), "scheduled": 0, "present": 0})
        cell["scheduled"] += 1
        if (pid, d) in att_set:
            cell["present"] += 1

    scheduled_no_present = [
        {"person_id": pid, "person_name": persons.get(pid), "date": d.isoformat()}
        for pid, d in sorted(scheduled_set - att_set)
    ]
    present_no_schedule = [
        {"person_id": pid, "person_name": persons.get(pid), "date": d.isoformat()}
        for pid, d in sorted(att_set - scheduled_set)
    ]

    scheduled_total = len(sch_keys)
    return {
        "start": s.isoformat(),
        "end": e.isoformat(),
        "span_days": (e - s).days + 1,
        "generated_at": _now_cn().isoformat(),
        "summary": {
            "scheduled_person_days": scheduled_total,
            "present_person_days": present_person_days,
            "attendance_rate": round(present_person_days / scheduled_total, 4) if scheduled_total else None,
            "unique_persons_scheduled": len({pid for pid, _ in scheduled_set}),
            "unique_persons_present": len({pid for pid, _ in att_set}),
        },
        "by_shift": [
            {"shift_name": k, "person_days": v}
            for k, v in sorted(by_shift.items(), key=lambda kv: -kv[1])
        ],
        "by_status": [
            {"status": k, "person_days": v}
            for k, v in sorted(by_status.items(), key=lambda kv: -kv[1])
        ],
        "daily": [daily[k] for k in sorted(daily)],
        "scheduled_no_present": {
            "total": len(scheduled_no_present),
            "items": scheduled_no_present[:_EXCEPTION_LIST_CAP],
        },
        "present_no_schedule": {
            "total": len(present_no_schedule),
            "items": present_no_schedule[:_EXCEPTION_LIST_CAP],
        },
        "_spec": {
            "scheduled_person_days": "区间内 schedules 记录数(人×日)",
            "present_person_days": "排班且当日有上班/下班打卡的人天数(出勤判定与 /schedules/comparison 一致)",
            "attendance_rate": "present_person_days / scheduled_person_days; 无排班时为 null",
            "scheduled_no_present": "有排班无考勤(疑似请假/脱岗)人天, 明细封顶 50 条, total 为完整数",
            "present_no_schedule": "有考勤无排班(疑似漏排/顶班)人天, 明细封顶 50 条, total 为完整数",
            "window": "排班/考勤按北京日历日 Date 列比较, start/end 整天, 跨度≤93 天",
        },
    }


# ─────────────────────────────────────────────────────────────────────
# ⑤ AI prompt 条件注入(ai_feed 调用): 实体维度数据按需追加
# ─────────────────────────────────────────────────────────────────────
def _extract_device_sns(db: Session, question: str) -> list[str]:
    """从问句提取候选 SN 并经 Device 表精确核对, 返回真实存在的 SN(保序去重)。"""
    candidates: list[str] = []
    for tok in _SN_CANDIDATE_RE.findall(question or ""):
        if tok not in candidates:
            candidates.append(tok)
    if not candidates:
        return []
    rows = db.scalars(select(Device.sn).where(Device.sn.in_(candidates[:_MAX_SN_CANDIDATES]))).all()
    return [sn for sn in candidates if sn in set(rows)]


def build_entity_context(db: Session, question: str, *,
                         start: str | None = None, end: str | None) -> str:
    """按问句条件构造实体维度补充段(设备号/排班关键词触发); 无命中 → 空串。

    刻意独立于 ai_feed 的 feed 字符预算裁剪管线: 实体段是问句显式点名的信息,
    应保证下发; 内部已对时间线条数/异常名单封顶, 体量可控。任何异常都吞掉并记日志,
    绝不影响主问答链路(与 ai_focus 注入同策略)。
    """
    try:
        parts: list[str] = []

        # 设备实体: 档案 KPI + 近 30 日动态计数/事件 + 全生命周期充值流量
        for sn in _extract_device_sns(db, question):
            behavior = supply_device_behavior(db, sn, timeline_limit=_TEXT_LIST_CAP, timeline_days=30)
            if behavior is not None:
                parts.append(
                    f"【设备 {sn} 档案与近30日动态】\n"
                    + _dumps({
                        "profile": behavior["profile"],
                        "recent_event_counts": behavior["timeline"]["counts"],
                        "recent_events": (behavior["timeline"]["items"] or [])[:_TEXT_LIST_CAP],
                    })
                )
            recharge = supply_recharge(db, sn=sn)
            recharge.pop("_spec", None)
            parts.append(f"【设备 {sn} 充值流量(生命周期全量)】\n" + _dumps(recharge))

        # 排班实体: 仅当问句命中排班域关键词; 窗口缺省退化为"当天"。
        if any(k in (question or "") for k in _SCHEDULE_KEYWORDS):
            s = start or _now_cn().date().isoformat()
            e = end or s
            schedule = supply_schedule(db, s, e)
            schedule.pop("_spec", None)
            parts.append(f"【排班与考勤 {s}~{e}】\n" + _dumps(schedule))

        if not parts:
            return ""
        return (
            "【实体维度补充数据(由问句中的设备号/排班关键词触发, 金额单位=元, 流量字段含字节/GB)】\n"
            + "\n\n".join(parts)
        )
    except Exception as exc:  # noqa: BLE001  实体注入是增强项, 失败不得阻断问答
        logger.warning("build_entity_context 注入失败(忽略): %s", exc)
        return ""
