"""AI 预生成「无新事件跳过」新鲜度判定 (#193/#204, 2026-10-07)

设计文档: docs/design/20260924-#193-AI报告刷新策略-无新事件跳过-实现方案.md

## 为什么存在

预生成每 30min 随同步触发, 但源表约四成 30 分钟桶是「零新增」—— 重算同一批数字,
只有窗口末尾时刻不同(生产实测 7 天 512 份报告仅 90 次命中, ≈88% 从未被读)。
本模块在 `warm_on_sync_complete` 的 20min 节流之后、generate 之前插一步「水位判定」:
自上次成功快照以来, 模板 scope 对应事件源没有任何新行 → 本可跳过。

## 三档模式(settings.AI_PREGEN_SKIP_MODE, 见设计文档 §4.3)

- off     : 不判定(回滚位, 行为与改造前完全一致)
- dry_run : 判定但**照常生成**, 「本可跳过」写 system_events(action=pregen_skipped)留痕
- enforce : 判定为无新事件则**真跳过**(不调 LLM), 同样留痕

## 五类保底(防「该刷不刷」, 见设计文档 §4.2)

| 场景 | reason | 行为 |
|---|---|---|
| 当日无快照(首次/跨日) | first_snapshot_today | 必生成 |
| 距上次快照 > MAX_STALE_MINUTES | stale_over_limit | 必生成 |
| 手动触发(level>=1) | manual_trigger | 必生成 |
| 判定查询异常 | judge_error(fail-open) | 必生成 |
| 上次快照后存在生成失败事件 | recent_failure | 必生成 |

## 判据与口径

- 事件源列全部为 timestamptz 且与统计口径同源(选择依据见设计文档 §五前置核实项,
  索引已于 2026-10-07 逐列核实): `refunds` 用 `created_at_raw`(运营口径, analytics
  同源)而非 `actual_refund_time`(对账口径)。`shanmao_refunds.refund_time` 暂无索引
  (闪猫量级小, 全扫可接受; 数据量上来后需补索引, 走 Alembic)。
- 表名/列名来自本模块白名单常量(**非外部输入**), 值一律绑定参数 —— 符合
  AGENTS.md「禁止无参数化拼接 SQL」铁律(标识符无法绑定参数, 用白名单自检替代)。
"""

import datetime as _dt
import json
import logging
import re

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from app.services.system_event_service import record_system_event

logger = logging.getLogger(__name__)

_TZ_CN = _dt.timezone(_dt.timedelta(hours=8))
_UTC = _dt.timezone.utc

# 标识符白名单自检用(表名/列名只允许小写字母+下划线)
_IDENT_RE = re.compile(r"^[a-z_]+$")

# scope → 事件源白名单: (表名, 业务时间列, 中文语义)
# ⚠️ 判据必须与统计口径同源(设计文档 §五核实项 1), 否则"有新事件"与
# "报告数字变了"不一致(见 §七风险 1)。
SCOPE_EVENT_SOURCES: dict[str, list[tuple[str, str, str]]] = {
    # 经营总览: 退费单 + 钱包提现 + 投诉工单登记 + 接待记录(任一 >0 即有新事件)
    "overview": [
        ("refunds", "created_at_raw", "退费单"),
        ("wallet_withdrawals", "op_time", "钱包提现"),
        ("service_tickets", "register_time", "投诉工单登记"),
        ("cs_records", "operation_time", "接待记录"),
    ],
    # 退款专项: 四路退款
    "refund": [
        ("refunds", "created_at_raw", "A路退费单"),
        ("wallet_withdrawals", "op_time", "B路钱包提现"),
        ("shanmao_refunds", "refund_time", "C路闪猫退款"),
        ("manual_refunds", "register_time", "D路手动退款登记"),
    ],
    # 投诉专项: 投诉工单登记 + 外部投诉(315/卡商)
    "complaint": [
        ("service_tickets", "register_time", "投诉工单登记"),
        ("external_complaints", "complaint_date", "外部投诉(315/卡商)"),
    ],
}

# 预生成失败的事件 action(保底⑤): 生成失败只写 system_events 不落 ai_reports,
# 「上次成功快照之后失败过」→ 必生成, 不能把失败当"刚生成过"。
_FAILED_ACTIONS = (
    "pregen_build_messages_failed",
    "pregen_llm_failed",
    "pregen_save_failed",
)


def _settings():
    """惰性导入 settings(避免导入环; 测试可 monkeypatch 返回替身)。"""
    from app.core.config import settings

    return settings


def last_snapshot_created_at(
    db: Session, template_id: int, *, snapshot_period: str, snapshot_date: str,
) -> _dt.datetime | None:
    """当日该模板最近一次**成功**快照的生成时刻(tz-aware UTC); 无则 None。

    只统计 is_public=TRUE: 失败的生成不落 ai_reports(generate_for_template 仅
    success 落库), 天然只认成功快照 —— 保底⑤据此补检失败之后的补生成。
    查询异常 fail-open 返回 None → 上层按「当日首份必生成」处理(宁多算不漏刷)。
    """
    try:
        latest = db.execute(
            text(
                """SELECT MAX(created_at) FROM ai_reports
                   WHERE template_id = :tid AND is_public = TRUE
                     AND snapshot_period = :sp AND snapshot_date = :sd"""
            ),
            {"tid": template_id, "sp": snapshot_period, "sd": snapshot_date},
        ).scalar()
    except Exception as e:  # noqa: BLE001
        logger.warning("pregen_freshness 快照查询失败(template_id=%s): %s", template_id, e)
        return None
    if latest is None:
        return None
    if latest.tzinfo is None:  # naive 值按 UTC 补齐(与 worker 节流同约定)
        latest = latest.replace(tzinfo=_UTC)
    return latest


def _count_source(db: Session, table: str, col: str, window_start, window_end) -> int:
    """单事件源计数: (window_start, window_end] 内业务时间列新增行数。

    标识符来自模块白名单常量, 双重自检(正则 + 常量来源), 拒绝拼接外部输入;
    时间值全部绑定参数。
    """
    if not (_IDENT_RE.match(table) and _IDENT_RE.match(col)):
        raise ValueError(f"非法标识符: {table}.{col}")
    n = db.execute(
        text(f"SELECT count(*) FROM {table} WHERE {col} > :ws AND {col} <= :we"),
        {"ws": window_start, "we": window_end},
    ).scalar()
    return int(n or 0)


def has_new_events(db: Session, scope: str, window_start: _dt.datetime, *,
                   now: _dt.datetime | None = None) -> dict:
    """判定 (window_start, now] 内 scope 对应事件源是否有新增行。

    返回 {"has_new": bool, "counts": {表名: 行数}, "error": str|None}
    fail-open(设计文档 §4.2 保底④): 任一查询异常/未登记 scope → has_new=True,
    宁可多算一次, 也不因监控故障停掉刷新。
    """
    if now is None:
        now = _dt.datetime.now(_UTC)
    sources = SCOPE_EVENT_SOURCES.get(scope)
    if not sources:
        # 未登记 scope 无法判定 → 按「有新事件」处理(不跳过)
        return {"has_new": True, "counts": {}, "error": f"unregistered_scope:{scope}"}
    counts: dict[str, int] = {}
    try:
        for table, col, _label in sources:
            counts[table] = _count_source(db, table, col, window_start, now)
    except Exception as e:  # noqa: BLE001  fail-open: 表/字段/DB 报错 → 必生成
        logger.warning("pregen_freshness 判定查询失败(scope=%s): %s", scope, e)
        return {"has_new": True, "counts": counts, "error": str(e)}
    return {
        "has_new": any(v > 0 for v in counts.values()),
        "counts": counts,
        "error": None,
    }


def has_recent_failure(db: Session, template_id: int, since: _dt.datetime) -> bool:
    """保底⑤: `since` 之后该模板是否存在预生成失败事件。

    失败事件 detail 为 JSON {"template_id": ..., "code": ...}; 事件量小
    (record_system_event 去重 30min), 取近 200 条在 Python 侧解析匹配,
    不把 detail 文本格式带进 SQL(防格式漂移误配)。查询失败 → True(保守必生成)。
    """
    try:
        rows = db.execute(
            text(
                "SELECT detail FROM system_events "
                "WHERE category = 'ai' AND action IN :actions AND created_at > :since "
                "ORDER BY id DESC LIMIT 200"
            ).bindparams(bindparam("actions", expanding=True)),
            {"actions": list(_FAILED_ACTIONS), "since": since},
        ).all()
    except Exception as e:  # noqa: BLE001
        logger.warning("pregen_freshness 失败事件查询异常, 按存在失败处理: %s", e)
        return True
    for (detail,) in rows:
        try:
            d = json.loads(detail) if detail else {}
        except (TypeError, ValueError):
            continue
        if str(d.get("template_id")) == str(template_id):
            return True
    return False


def evaluate_pregen_skip(
    db: Session,
    *,
    template_id: int,
    template_code: str,
    scope: str,
    snapshot_period: str,
    snapshot_date: str,
    trigger_level: int = 0,
    mode: str | None = None,
    max_stale_minutes: int | None = None,
    enabled_scopes: set[str] | None = None,
    now: _dt.datetime | None = None,
) -> dict:
    """三档判定主入口(warm_on_sync_complete 接入点; 不抛异常)。

    返回 {"skip": bool, "reason": str, "verdict": dict|None, "mode": str}
    - skip=True 表示「本可跳过」: dry_run 由调用方留痕后**照常生成**;
      enforce 由调用方留痕后**真跳过**。
    - skip=False 时调用方不写事件、行为与改造前一致(mode=off 时甚至不判定)。
    参数 mode/max_stale_minutes/enabled_scopes 显式传值可覆盖 settings(测试用)。
    """
    s = _settings()
    if mode is None:
        mode = (str(s.AI_PREGEN_SKIP_MODE) or "off").strip().lower()
    if max_stale_minutes is None:
        max_stale_minutes = int(s.AI_PREGEN_MAX_STALE_MINUTES)
    if enabled_scopes is None:
        enabled_scopes = {
            x.strip() for x in str(s.AI_PREGEN_FRESHNESS_ENABLED_SCOPES).split(",")
            if x.strip()
        }
    if now is None:
        now = _dt.datetime.now(_UTC)

    base = {"verdict": None, "mode": mode}
    if mode == "off":
        return {**base, "skip": False, "reason": "mode_off"}
    # 灰度白名单 + 未登记 scope 双重限制(不在名单/白名单 → 不判, 维持现状)
    if scope not in enabled_scopes or scope not in SCOPE_EVENT_SOURCES:
        return {**base, "skip": False, "reason": "scope_disabled"}
    if trigger_level >= 1:
        # 保底③: 人在盯(admin 手动触发), 跳过会显得"点了没反应"
        return {**base, "skip": False, "reason": "manual_trigger"}

    last = last_snapshot_created_at(
        db, template_id, snapshot_period=snapshot_period, snapshot_date=snapshot_date)
    if last is None:
        # 保底①: 当日无快照(首次/跨日)必生成 —— 命中链(#135/#151)需要当日底稿
        return {**base, "skip": False, "reason": "first_snapshot_today"}
    stale_min = (now - last).total_seconds() / 60
    if stale_min > max_stale_minutes:
        # 保底②: 报告首行"统计窗口…至 now"不能长期停在数小时前(可核对性)
        return {**base, "skip": False, "reason": "stale_over_limit"}
    if has_recent_failure(db, template_id, since=last):
        # 保底⑤: 上次成功快照之后失败过 → 必生成(不能把失败当"刚生成过")
        return {**base, "skip": False, "reason": "recent_failure"}

    verdict = has_new_events(db, scope, last, now=now)
    verdict = {
        **verdict,
        "window_start": last.isoformat(),
        "window_end": now.isoformat(),
    }
    if verdict["error"] is not None:
        # 保底④: 判定异常 fail-open → 必生成
        return {**base, "skip": False, "reason": "judge_error", "verdict": verdict}
    if verdict["has_new"]:
        return {**base, "skip": False, "reason": "has_new_events", "verdict": verdict}
    return {**base, "skip": True, "reason": "no_new_events", "verdict": verdict}


def record_pregen_skipped_event(
    db: Session,
    *,
    template_id: int,
    template_code: str,
    scope: str,
    decision: dict,
    enforced: bool,
) -> None:
    """跳过/本可跳过留痕(system_events, category=ai, action=pregen_skipped)。

    dry_run 与 enforce 都写本事件, 由 detail.enforced 区分「影子判定」与「真跳过」;
    reason 字段区分 no_new_events / throttled_20min(设计文档 §4.5)。
    证据 JSON 结构与 §4.1 约定一致(check_ai_issue_gates ③ 按此对账):
    {"template","scope","window","counts","has_new"} + {"mode","reason","enforced"}。
    info 级不进角标、不外发(record_system_event 默认策略)。
    """
    verdict = decision.get("verdict") or {}
    detail = {
        "template": template_code,
        "template_id": template_id,
        "scope": scope,
        "window": [verdict.get("window_start"), verdict.get("window_end")],
        "counts": verdict.get("counts") or {},
        "has_new": verdict.get("has_new"),
        "mode": decision.get("mode"),
        "reason": decision.get("reason"),
        "enforced": bool(enforced),
    }
    record_system_event(
        db,
        category="ai",
        action="pregen_skipped",
        title=f"AI 预生成{'跳过' if enforced else '影子判定可跳过'}: {template_code}",
        summary=f"scope={scope} 自上次快照零新增 counts={verdict.get('counts') or {}}",
        status="info",
        detail=json.dumps(detail, ensure_ascii=False),
    )
