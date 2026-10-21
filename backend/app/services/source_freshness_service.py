"""数据源时效与"空结果"告警服务 (工单 #191)。

背景(生产实测): E 路(退款记录报表)已配置 `default_sync=true`, 调度器每天 06:00 真的执行了它,
但 `scheduled` 档**每次都取回 0 行**(上游 T+1 且次日白天才发布), 而采集链路把"成功但 0 行"
按成功记录 —— 既无告警也无通知, 数据断了 2 天没有任何人知道(事故见 #190)。

本模块补四件事(都是"事实查询", 不做业务推断):
1. `notify_empty_result`: scheduled 档取 0 行 → 写 `system_events` 告警(按源 + 当日节流);
2. `check_freshness`: 关键业务时间列"最新值距今天数"巡检, 超阈值 → 告警;
3. `run_freshness_check`: 巡检入口(调度器每小时调用一次)。
4. `check_device_combo_coverage`(#299 顺带项 5): "成功 ≠ 完整" —— 低频档/回补跑完仍可能
   覆盖不全, 用 devices 无有效套餐占比做守卫, 超阈值 → 告警。

新增巡检目标时必须用 **ORM 列**引用表/字段(AGENTS.md 铁律: 禁止拼接 SQL 标识符)。
"""

import datetime as _dt
import json
import logging

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.device import Device
from app.models.refund_standard import StdRefundRecord
from app.services.system_event_service import record_system_event

logger = logging.getLogger(__name__)

_TZ_CN = _dt.timezone(_dt.timedelta(hours=8))

# ── 巡检目标: (source, 显示名, 业务时间列(ORM 属性), 允许落后天数, 采集应完成小时, 说明) ──
#   max_lag_days 判据 = 基准日 - 最新业务日 的天数; 超过即告警。
#   expected_by_hour(#191 修正): 该源**当天应完成采集的小时**(与 options.low_freq_hour 一致)。
#     判定基准日随它变化: 当天尚未到该小时 → 基准日=昨天(今天的采集还没到, 不该判超期);
#     已过该小时 → 基准日=今天(该跑的没跑 → 超期)。0 = 随时可判。
#     ⚠️ 不加这条会**每天早晨误报**: E 路上游 T+1, 正常在 D 日 15:00 采到 D-1; 若在 D+1 06:05
#     用"今天"为基准, 最新 D-1 会被算成落后 2 天 → 每个上午都告警一次(stale 噪音)。
FRESHNESS_TARGETS: tuple[dict, ...] = (
    {
        "source": "refund_record_report",
        "name": "退款记录报表(E 路对账基准)",
        "time_attr": StdRefundRecord.refund_time,
        "max_lag_days": 1,
        "expected_by_hour": 15,   # 与 bms_fetcher.BMS_DATA_SOURCES[...]["options"]["low_freq_hour"] 一致
        "note": "上游 T+1(次日白天发布); 当天 15:00 档后仍落后>1天=采集链路失效",
    },
)

# 0 行告警白名单: 这些源"当日应有数据", scheduled 档取 0 行属异常(其余源允许天然为空)
EXPECTED_DATA_SOURCES: frozenset[str] = frozenset(
    {"refund", "ticket", "wallet", "cs_record"} | {t["source"] for t in FRESHNESS_TARGETS}
)

# 进程内当日节流: 同一告警 key 每天只报一次(调度器每分钟 tick, 否则刷屏)
_alerted: dict[str, str] = {}


def _today_cn() -> _dt.date:
    return _dt.datetime.now(_TZ_CN).date()


def _emit_once(key: str, biz_date: str, *, action: str, title: str, summary: str,
               detail: dict, status: str = "partial", level: str = "warning") -> bool:
    """按 (key, biz_date) 节流写一条 system_events 告警; 当日已报过 → 返回 False。"""
    if _alerted.get(key) == biz_date:
        return False
    db = SessionLocal()
    try:
        record_system_event(
            db, category="sync", action=action, title=title, summary=summary,
            status=status, level=level,
            detail=json.dumps(detail, ensure_ascii=False),
        )
        _alerted[key] = biz_date
        return True
    except Exception as e:  # noqa: BLE001  告警失败不能影响同步主流程
        logger.warning("写采集告警事件失败(action=%s): %s", action, e)
        return False
    finally:
        db.close()


def notify_empty_result(*, data_type: str, name: str, trigger_type: str) -> bool:
    """scheduled 档取 0 行告警(#191 处理动作 1)。

    仅对"当日应有数据"的源告警(EXPECTED_DATA_SOURCES): 低频大表/报表类源天然可能为空,
    一律告警会淹没通知中心。manual 档不告警(人工触发本身就是人在盯)。
    """
    if trigger_type != "scheduled" or data_type not in EXPECTED_DATA_SOURCES:
        return False
    today = _today_cn().isoformat()
    return _emit_once(
        f"empty:{data_type}", today,
        action="scheduled_empty_result",
        title=f"采集空结果: {name}",
        summary=(f"{name}({data_type}) 定时档执行成功但取回 0 行; "
                 f"若该源当日应有数据, 说明窗口/上游发布时序有问题"),
        detail={"data_type": data_type, "trigger_type": trigger_type, "total_rows": 0},
    )


def check_freshness(db: Session | None = None, *, now: _dt.datetime | None = None) -> list[dict]:
    """数据时效巡检: 返回每个目标的 {source, latest, baseline, lag_days, max_lag_days, stale}。

    判定用"基准日"(#191 修正): 当天尚未到该源的 `expected_by_hour` → 基准日=昨天
    (今天的采集窗口还没到, 不能判超期); 已到/未配置小时 → 基准日=今天。
    只做"基准日 vs 最新业务日"的距离计算; 取数失败的目标记 stale=None(不误报)。
    now 参数供单测注入固定时刻。
    """
    own = db is None
    db = db or SessionLocal()
    out: list[dict] = []
    try:
        now_cn = now.astimezone(_TZ_CN) if now is not None else _dt.datetime.now(_TZ_CN)
        today = now_cn.date()
        for t in FRESHNESS_TARGETS:
            # 基准日: 未到"当天应采集小时"→ 用昨天(否则上游 T+1 会每天上午误报)
            by_hour = int(t.get("expected_by_hour") or 0)
            pending_today = by_hour > 0 and now_cn.hour < by_hour
            baseline = today - _dt.timedelta(days=1) if pending_today else today
            try:
                latest = db.execute(select(func.max(t["time_attr"]))).scalar()
            except Exception as e:  # noqa: BLE001  查询失败不误报
                logger.warning("时效巡检取数失败(source=%s): %s", t["source"], e)
                out.append({"source": t["source"], "latest": None, "lag_days": None,
                            "max_lag_days": t["max_lag_days"], "stale": None})
                continue
            lag = None
            latest_day = None
            if latest is not None:
                if latest.tzinfo is None:
                    latest = latest.replace(tzinfo=_dt.timezone.utc)
                latest_day = latest.astimezone(_TZ_CN).date()
                lag = (baseline - latest_day).days
            out.append({
                "source": t["source"], "name": t["name"],
                "latest": latest_day.isoformat() if latest_day else None,
                "baseline": baseline.isoformat(),
                "pending_today": pending_today,
                "expected_by_hour": by_hour,
                "lag_days": lag, "max_lag_days": t["max_lag_days"],
                "note": t.get("note"),
                "stale": (lag is None) or (lag > t["max_lag_days"]),
            })
    finally:
        if own:
            db.close()
    return out


# 设备套餐覆盖率阈值(#299 顺带项 5): 无套餐设备占比上限(%)
# 依据: #298 基线 40.2%(历史回补前) → #299 回补后 2.62%(52,659/2,011,583)。
#   超 5% 说明 device_combo 链路"成功但覆盖不完整"(0 行当成功 / 断采), 需人工介入。
COMBO_MISSING_MAX_PCT: float = 5.0


def check_device_combo_coverage(db: Session | None = None) -> dict:
    """只读巡检: devices 无有效套餐(current_combo_name 为空)占比(#299 顺带项 5)。

    口径(与 #298/#320/#321 一致):
      - 分母 = devices 全量行数;
      - 分子 = `current_combo_name IS NULL OR ''`(无套餐设备);
      - 套餐单一真实源为 `dim_device_combo`(#320), `current_combo_name` 是其投影(#321),
        故本巡检等价于"套餐覆盖是否完整"的守卫 —— 成功 ≠ 完整。
    事实查询, 不做业务推断; 取数失败 → alert=False(不误报)。
    """
    own = db is None
    db = db or SessionLocal()
    try:
        total = db.execute(select(func.count()).select_from(Device)).scalar() or 0
        missing = db.execute(
            select(func.count()).select_from(Device).where(
                or_(Device.current_combo_name.is_(None), Device.current_combo_name == "")
            )
        ).scalar() or 0
    except Exception as e:  # noqa: BLE001  取数失败不误报
        logger.warning("套餐覆盖率巡检取数失败: %s", e)
        return {"total": None, "missing": None, "pct": None,
                "threshold_pct": COMBO_MISSING_MAX_PCT, "alert": False}
    finally:
        if own:
            db.close()
    pct = round(missing / total * 100, 2) if total else 0.0
    return {"total": total, "missing": missing, "pct": pct,
            "threshold_pct": COMBO_MISSING_MAX_PCT, "alert": pct > COMBO_MISSING_MAX_PCT}


def run_freshness_check(db: Session | None = None) -> dict:
    """巡检 + 对超期目标写告警(按源 + 当日节流)。返回巡检明细与告警数。"""
    rows = check_freshness(db)
    today = _today_cn().isoformat()
    alerts = 0
    for r in rows:
        if not r["stale"]:
            continue
        if _emit_once(
            f"stale:{r['source']}", today,
            action="source_stale",
            title=f"数据源时效超期: {r.get('name') or r['source']}",
            summary=(f"{r.get('name') or r['source']}({r['source']}) 最新业务日="
                     f"{r['latest']}, 已落后 {r['lag_days']} 天(阈值 {r['max_lag_days']} 天)"
                     f"{'; ' + r['note'] if r.get('note') else ''}"),
            detail={"source": r["source"], "latest": r["latest"],
                    "lag_days": r["lag_days"], "max_lag_days": r["max_lag_days"]},
        ):
            alerts += 1

    # #299 顺带项 5: 覆盖率守卫 —— "成功 ≠ 完整"(0 行当成功 / 回补跑完仍覆盖不全)
    coverage = check_device_combo_coverage(db)
    if coverage["alert"]:
        if _emit_once(
            "coverage:device_combo", today,
            action="device_combo_coverage",
            title="设备套餐覆盖率异常波动",
            summary=(f"无套餐设备 {coverage['missing']}/{coverage['total']} "
                     f"= {coverage['pct']}%(阈值 {coverage['threshold_pct']}%); "
                     f"采集/回补虽报成功但覆盖不完整, 需核查 device_combo 链路"),
            detail={"total": coverage["total"], "missing": coverage["missing"],
                    "pct": coverage["pct"], "threshold_pct": coverage["threshold_pct"]},
        ):
            alerts += 1

    return {"checked": len(rows), "alerts": alerts, "rows": rows, "coverage": coverage}

