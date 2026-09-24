"""数据源时效与"空结果"告警服务 (工单 #191)。

背景(生产实测): E 路(退款记录报表)已配置 `default_sync=true`, 调度器每天 06:00 真的执行了它,
但 `scheduled` 档**每次都取回 0 行**(上游 T+1 且次日白天才发布), 而采集链路把"成功但 0 行"
按成功记录 —— 既无告警也无通知, 数据断了 2 天没有任何人知道(事故见 #190)。

本模块补三件事(都是"事实查询", 不做业务推断):
1. `notify_empty_result`: scheduled 档取 0 行 → 写 `system_events` 告警(按源 + 当日节流);
2. `check_freshness`: 关键业务时间列"最新值距今天数"巡检, 超阈值 → 告警;
3. `run_freshness_check`: 巡检入口(调度器每小时调用一次)。

新增巡检目标时必须用 **ORM 列**引用表/字段(AGENTS.md 铁律: 禁止拼接 SQL 标识符)。
"""

import datetime as _dt
import json
import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.refund_record_report import RefundRecordReport
from app.services.system_event_service import record_system_event

logger = logging.getLogger(__name__)

_TZ_CN = _dt.timezone(_dt.timedelta(hours=8))

# ── 巡检目标: (source, 显示名, 业务时间列(ORM 属性), 允许落后天数, 说明) ──
#   max_lag_days 判据 = 今天(北京) - 最新业务日 的天数; 超过即告警。
#   E 路上游为 T+1(次日白天才发布), 故阈值取 1: 落后 >1 天即说明采集断了。
FRESHNESS_TARGETS: tuple[dict, ...] = (
    {
        "source": "refund_record_report",
        "name": "退款记录报表(E 路对账基准)",
        "time_attr": RefundRecordReport.refund_time,
        "max_lag_days": 1,
        "note": "上游 T+1(次日白天发布); 落后>1天=采集链路失效",
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


def check_freshness(db: Session | None = None) -> list[dict]:
    """数据时效巡检: 返回每个目标的 {source, latest, lag_days, max_lag_days, stale}。

    只做"最新业务时间 vs 今天(北京)"的距离计算; 取数失败的目标记 stale=None(不误报)。
    """
    own = db is None
    db = db or SessionLocal()
    out: list[dict] = []
    try:
        today = _today_cn()
        for t in FRESHNESS_TARGETS:
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
                lag = (today - latest_day).days
            out.append({
                "source": t["source"], "name": t["name"],
                "latest": latest_day.isoformat() if latest_day else None,
                "lag_days": lag, "max_lag_days": t["max_lag_days"],
                "note": t.get("note"),
                "stale": (lag is None) or (lag > t["max_lag_days"]),
            })
    finally:
        if own:
            db.close()
    return out


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
    return {"checked": len(rows), "alerts": alerts, "rows": rows}
