"""外部只读开放平台路由 (docs/arch/A-20260902-021 LOCKED v1.0)

对外只读接口(供 workbuddy 等外部工具用 token+scope 拉取经营数据), 与内部 JWT 体系隔离:
- 鉴权: Authorization: Bearer <token>, token 由管理员在"系统设置/外部接入"生成, 库中存 SHA-256 hash。
- 只读端点: reception / refund / complaint(单日, date 缺省今天) + brief(period 日/周/月)。
- 管理端点(内部 JWT AdminUser): token CRUD(生成仅显一次明文/吊销/改 scope/限频)。
- 限频: Redis 固定窗口(每 token 可配 rate_limit_per_min, 否则全局默认)。
- 审计: 每次外部拉取写 system_events(category=external)。

金额单位: 只读透传既有聚合, 单位=元(Numeric(12,2), 2位小数), 不做分/元换算。
所有统计口径复用 analytics / complaints 模块既有实现, 不重复造轮子。
"""

import calendar
import datetime as _dt
import logging
import re
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Request
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import AdminUser, DBSession
from app.api.v1.complaints import (
    _BACKLOG_DONE_SET,
    _EXTERNAL_DONE_RESULTS,
    _external_done_sns,
)
from app.core.config import settings
from app.core.redis_client import cache_ttl_for, cached_call
from app.models.card_complaint import CardComplaint
from app.models.external_complaint import ExternalComplaint
from app.models.external_token import ExternalAccessToken
from app.models.person import Person
from app.models.specialty import PersonSpecialtyAssignment, Specialty
from app.models.ticket import ServiceTicket
from app.models.user import User
from app.schemas.common import R
from app.schemas.external_token import (
    ExternalTokenCreate,
    ExternalTokenOut,
    ExternalTokenUpdate,
)
from app.services import analytics as A
from app.services import external_service as S
from app.services import setup_package

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/external", tags=["外部只读开放平台"])

TZ_CN = _dt.timezone(_dt.timedelta(hours=8))

# 投诉"已完成"状态集合 — 单一来源: 复用 complaints._BACKLOG_DONE_SET(避免两处集合漂移)
_DONE_STATUS = _BACKLOG_DONE_SET

# 归一化后可能判为"已完成"的非完成态集合(与 complaints._norm_status 语义一致, 2026-08-25):
#   处理中类 / 待处理类状态 + 空状态, 若存在真实完成(finish>=register+3min) → _norm_status 判为已完成 → 不进积压。
_NORM_DONE_CANDIDATE_STATUS = {
    # 处理中类
    "处理中", "跟进中", "跟进", "进行中",
    # 待处理类
    "待处理", "未处理", "待分派", "pending", "待跟进",
}

# 真实完成判定 = finish_time 晚于 register_time ≥ 3 分钟(SQL 层表达, 与 complaints._is_real_finish 一致)
_REAL_FINISH_INTERVAL_SECS = 180


def _now_cn() -> datetime:
    return datetime.now(TZ_CN)


# 分钟级单日窗口(2026-09-11 #66): 下游需按"同刻"做日比对(如 09-10T12:30 vs 09-09T12:30),
# 故单日端点放开到分钟精度。仅接受 `YYYY-MM-DD` 或 `YYYY-MM-DDTHH:MM` 两种形态, 其余一律 400
# (不引入秒/时区偏移, 全部按北京时间解释, 避免下游各自猜测口径)。
_MINUTE_DATE_LEN = 16  # len("YYYY-MM-DDTHH:MM")


def _parse_window_arg_or_400(value: str | None, name: str) -> tuple[str | None, bool]:
    """解析单日窗口参数 → (规范化字符串, 是否分钟精度)。非法 → 400。

    - `None` → (None, False)
    - `YYYY-MM-DD` → (同日字符串, False)
    - `YYYY-MM-DDTHH:MM` → (同字符串, True); 秒/微秒一律拒绝(固定分钟精度)
    - 未来时刻(晚于当前北京时刻) → 400(防时间穿越/空窗口, 与 range 模式 end 护栏一致)
    """
    if value is None:
        return None, False
    raw = (value or "").strip()
    if not raw:
        raise HTTPException(400, f"{name} 参数非法: {value!r}, 应为 YYYY-MM-DD 或 YYYY-MM-DDTHH:MM")
    # 分钟级: 长度固定 16 且第 11 位为 'T'
    if len(raw) == _MINUTE_DATE_LEN and raw[10] == "T":
        try:
            dt = _dt.datetime.fromisoformat(raw)
        except (ValueError, TypeError):
            raise HTTPException(400, f"{name} 参数非法: {value!r}, 应为 YYYY-MM-DDTHH:MM(分钟精度)")
        if _dt.datetime.now(TZ_CN).replace(tzinfo=None) < dt:
            raise HTTPException(400, f"{name}({raw}) 不能晚于当前时刻({_now_cn().isoformat(timespec='minutes')})")
        return raw, True
    # 纯日期
    _require_plain_date_or_400(raw, name)
    return raw, False


# 严格纯日期: 字面 `YYYY-MM-DD`(4-2-2 数字)。
# ⚠️ 不能只用 `date.fromisoformat`: Python ≥3.11 会**接受** `20260910` / `2026-W01-1`
#   这类紧凑/ISO 周格式 —— 那是契约外输入, 却会被静默当成合法日期(2026-09-11 #66 复审发现)。
#    与 `ai._require_plain_date` 保持**同强度**校验, 避免"AI 入口 400 而外部入口 200"的裂缝。
_PLAIN_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _require_plain_date_or_400(value: str | None, name: str) -> str:
    """校验 `value` 为字面 `YYYY-MM-DD` 且是真实日历日; 否则 400。返回规范化串。"""
    raw = (value or "").strip()
    if not _PLAIN_DATE_RE.match(raw):
        raise HTTPException(400, f"{name} 参数非法: {value!r}, 应为 YYYY-MM-DD")
    try:
        _dt.date.fromisoformat(raw)
    except (ValueError, TypeError):
        raise HTTPException(400, f"{name} 参数非法: {value!r}, 不是有效日期")
    return raw


def _valid_date_or_400(date: str | None) -> str | None:
    """校验**纯日期** date 参数(YYYY-MM-DD)。非法 → 400(架构 §2.1/§八)。

    仅用于 range 模式的 start/end: 区间语义是"整天", 混入分钟会让跨度(span_days)计算失真,
    故分钟串在此**显式拒绝**(不静默截断, 避免下游拿到与请求不符的窗口)。
    单日端点请改用 `_parse_window_arg_or_400`(支持分钟精度)。
    """
    if date is None:
        return None
    return _require_plain_date_or_400(date, "date")


# ─────────────────────────────────────────────────────────────────────
# 鉴权
# ─────────────────────────────────────────────────────────────────────
def _auth(db: Session, authorization: str | None) -> ExternalAccessToken:
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    row = S.authenticate(db, token)
    S.check_rate_limit(row)
    S.touch_last_used(db, row)
    return row


def _src_case():
    """三源投诉分类 CASE(与 analytics._complaint_source_type_case 一致)"""
    return A._complaint_source_type_case()


def _cat_case():
    """投诉类工单判定(与 analytics._ticket_category_case 一致)"""
    return A._ticket_category_case()


# ─────────────────────────────────────────────────────────────────────
# 对外只读: 三核心单日端点
# ─────────────────────────────────────────────────────────────────────
# 分钟级窗口(2026-09-11 #66): `date=YYYY-MM-DDTHH:MM` → 该日 00:00 ~ 该分钟, 供下游同刻日比对。
# 缓存 key 需带上分钟段, 否则 12:30 与 12:31 会互相污染; 分钟窗口已终结(is_realtime=False)
# 故可走长 TTL(与历史纯日期一致), 无需随数据写入失效。
_WINDOW_CACHE_TTL = 60


def _kpi(db: Session, date: str | None, is_minute: bool = False):
    """取 KPI 聚合(带缓存)。

    - 纯日期 / 今天实时: 保持既有 60s 短 TTL(**行为完全不变**, 避免影响既有外部调用方)
    - 分钟窗口: 窗口已终结(护栏保证不晚于当前时刻), 数据定型 → 走日期感知 TTL
      (历史日 24h / 今天 60s), 避免下游逐分钟比对时反复全量重算
    """
    ttl = cache_ttl_for(date, date, _WINDOW_CACHE_TTL) if is_minute else _WINDOW_CACHE_TTL
    return cached_call(f"today:kpi:{date}:None:None", ttl, A.today_kpi, db, date, None, None)


def _window_meta(date: str | None, is_minute: bool) -> dict:
    """窗口元信息: 让下游明确知道拿到的是"整天"还是"截至某分钟", 并对齐同比窗口。"""
    if date is None:
        return {"window_kind": "today_realtime", "window_precision": "realtime"}
    if is_minute:
        d = date[:10]
        return {
            "window_kind": "minute_to_date",
            "window_precision": "minute",
            "window_start": f"{d}T00:00:00+08:00",
            "window_end": f"{date}:00+08:00",
            # 同比/环比基准: 前一日的同刻窗口(与 analytics._resolve_windows 完全一致)
            "compare_start": f"{_prev_day(d)}T00:00:00+08:00",
            "compare_end": f"{_prev_day(d)}{date[10:]}:00+08:00",
        }
    return {
        "window_kind": "full_day",
        "window_precision": "day",
        "window_start": f"{date}T00:00:00+08:00",
        "window_end": f"{date}T23:59:59+08:00",
        "compare_start": f"{_prev_day(date)}T00:00:00+08:00",
        "compare_end": f"{_prev_day(date)}T23:59:59+08:00",
    }


def _prev_day(d: str) -> str:
    return (_dt.date.fromisoformat(d) - _dt.timedelta(days=1)).isoformat()


@router.get("/reception", response_model=R, summary="外部-当日/指定日接待量(支持分钟)")
def external_reception(
    db: DBSession,
    date: str | None = Query(
        None,
        description="YYYY-MM-DD(整天) 或 YYYY-MM-DDTHH:MM(该日00:00至该分钟, 供同刻日比对); 缺省=今天实时",
    ),
    authorization: Annotated[str | None, Header()] = None,
):
    row = _auth(db, authorization)
    S.require_scope(row, "reception")
    S.record_external_access(db, row, "reception")
    date, is_minute = _parse_window_arg_or_400(date, "date")
    kpi = _kpi(db, date, is_minute)
    return R(data={
        "date": kpi.get("date"),
        "generated_at": _now_cn().isoformat(),
        "reception": kpi.get("reception"),
        **_window_meta(date, is_minute),
        "_spec": {
            "reception": "接待量=客服处理记录数(cs_record.operation_time 落当日); device_count=去重设备SN数",
            "window": "date 传 YYYY-MM-DDTHH:MM 时, 窗口=该日00:00~该分钟(分钟精度), 同比基准=前一日同刻窗口",
        },
    })


@router.get("/refund", response_model=R, summary="外部-当日/指定日退款(支持分钟)")
def external_refund(
    db: DBSession,
    date: str | None = Query(
        None,
        description="YYYY-MM-DD(整天) 或 YYYY-MM-DDTHH:MM(该日00:00至该分钟, 供同刻日比对); 缺省=今天实时",
    ),
    authorization: Annotated[str | None, Header()] = None,
):
    row = _auth(db, authorization)
    S.require_scope(row, "refund")
    S.record_external_access(db, row, "refund")
    date, is_minute = _parse_window_arg_or_400(date, "date")
    kpi = _kpi(db, date, is_minute)
    return R(data={
        "date": kpi.get("date"),
        "generated_at": _now_cn().isoformat(),
        "refund_count": kpi.get("refund_count"),
        "refund_amount": kpi.get("refund_amount"),
        "refund_done_today": kpi.get("refund_done_today"),
        "actual_cashflow": kpi.get("actual_cashflow"),
        **_window_meta(date, is_minute),
        "_spec": {
            "refund_count": "退款笔数=四路经营合计 A线上直退+B钱包取现+C闪猫+D手动 (按创建时间)",
            "refund_amount": "退款金额=四路经营合计, 单位=元, 2位小数",
            "refund_done_today": "当日完成退款=路线A status=已退款 且更新时间∈当日",
            "actual_cashflow": "真实现金流E=refund_record_reports 退款成功合计(对账基准, 2026-09-05 v1.1 起替代 merchant_refunds), 元",
            "window": "date 传 YYYY-MM-DDTHH:MM 时, 窗口=该日00:00~该分钟(分钟精度), 同比基准=前一日同刻窗口",
        },
    })


@router.get("/complaint", response_model=R, summary="外部-当日/指定日投诉(登记/处理双口径, 支持分钟)")
def external_complaint(
    db: DBSession,
    date: str | None = Query(
        None,
        description="YYYY-MM-DD(整天) 或 YYYY-MM-DDTHH:MM(该日00:00至该分钟, 供同刻日比对); 缺省=今天实时",
    ),
    authorization: Annotated[str | None, Header()] = None,
):
    row = _auth(db, authorization)
    S.require_scope(row, "complaint")
    S.record_external_access(db, row, "complaint")
    date, is_minute = _parse_window_arg_or_400(date, "date")
    kpi = _kpi(db, date, is_minute)
    # 登记/处理双口径(2026-09-08 #33 用户诉求): 外部投诉"登记"与"处理"是两个口径,
    # 单日端点除兼容总量 complaint 外, 补充 registered/handled/backlog(与 brief 同源
    # _complaint_enhanced 单日窗口, 避免外部只看到"总量"而误判未处理)。
    # ⚠️ 分钟窗口下 start 必须是**当日 00:00**、end 才是该分钟, 否则两端都取该分钟会退化成
    #   零宽窗口(start=end=12:30 → 什么都查不到)。此处显式把起点压回整天开头。
    win_start = date[:10] if is_minute else date
    enhanced = _complaint_enhanced(db, win_start, date)
    return R(data={
        "date": kpi.get("date"),
        "generated_at": _now_cn().isoformat(),
        "complaint": kpi.get("complaint"),
        # 三源 registered(登记)/handled(处理)/backlog(积压) — 与内部看板一致
        "by_source": enhanced.get("by_source"),
        "registered": enhanced.get("total", {}).get("registered"),
        "handled": enhanced.get("total", {}).get("handled"),
        "backlog": enhanced.get("total", {}).get("backlog"),
        **_window_meta(date, is_minute),
        "_spec": {"complaint": "投诉量=service_tickets投诉 + external_complaints + card_complaints 三表并集(落当日); "
                               "registered=窗口内新登记(工单按 register_time, 315/卡商补充表按 complaint_date 并入外部); "
                               "handled=窗口内真实完成(工单 finish_time∈窗口≥3min + 315已办结并入, 与内部口径一致); "
                               "backlog=窗口内登记未闭环",
                  "window": "date 传 YYYY-MM-DDTHH:MM 时, 窗口=该日00:00~该分钟(分钟精度), 同比基准=前一日同刻窗口"},
    })


# ─────────────────────────────────────────────────────────────────────
# 对外只读: 一键经营简报 brief (period 日/周/月)
# ─────────────────────────────────────────────────────────────────────
@router.get("/brief", response_model=R, summary="外部-一键经营简报(日/周/月/任意区间)")
def external_brief(
    db: DBSession,
    period: str = Query("daily", pattern="^(daily|weekly|monthly)$", description="daily/weekly/monthly"),
    date: str | None = Query(None, description="period=daily 时指定某整天 YYYY-MM-DD(缺省=今天实时)"),
    start: str | None = Query(None, description="任意区间起点, 仅纯日期 YYYY-MM-DD(与 end 成对; 给定则优先于 period/date, 支持历史标准周回看; 起点日 00:00:00 起)"),
    end: str | None = Query(None, description="任意区间终点, 仅纯日期 YYYY-MM-DD(与 start 成对; end=今天则实时截止 now, 否则整天 23:59:59)"),
    authorization: Annotated[str | None, Header()] = None,
):
    row = _auth(db, authorization)
    S.require_scope(row, "brief")
    tag = f"brief:{period}" if (start is None and end is None) else f"brief:range:{start}:{end}"
    S.record_external_access(db, row, tag)
    return R(data=_build_brief(db, period, date, start=start, end=end))


def _window(period: str, date: str | None, start: str | None = None, end: str | None = None) -> dict:
    """period/date 或 start/end → 时间窗口(架构 §2.2 口径 + v1.2 range 扩展)

    - 默认: daily=当日0点起(实时), date 给定则该整天; weekly=整周(周一起); monthly=整月(未满月则1日到当前)。
    - v1.2 range 扩展: start/end 同时给出 → 任意连续区间(历史标准周回看 = 传该周周一~周日);
      仅收纯日期 YYYY-MM-DD; 跨天区间默认整天(起点 00:00:00 ~ 终点 23:59:59), end==今天 → 实时截止 now。
      只给一个 → 400。
    返回 start/end(YYYY-MM-DD, 供 analytics 按北京时区解释); grain 供 refund_decision_overview(单日→day, 区间→week)。
    """
    now = _now_cn()
    today = now.date()

    # ── v1.2: start/end 任意区间模式(成对校验 + 宽度/未来护栏) ──
    if start is not None or end is not None:
        if start is None or end is None:
            raise HTTPException(400, "start/end 必须成对提供(YYYY-MM-DD)")
        s = _valid_date_or_400(start)
        e = _valid_date_or_400(end)
        s_d = _dt.date.fromisoformat(s)
        e_d = _dt.date.fromisoformat(e)
        if s_d > e_d:
            raise HTTPException(400, f"start({s}) 不能晚于 end({e})")
        # ⚠️ 护栏(2026-09-07): end 晚于今天(北京) → 400(防时间穿越/未来空窗, 不静默截断)。
        if e_d > today:
            raise HTTPException(400, f"end({e}) 不能晚于今天({today.isoformat()})")
        span_days = (e_d - s_d).days + 1
        # ⚠️ 护栏: 单次区间跨度上限(settings.EXTERNAL_BRIEF_MAX_RANGE_DAYS, 默认 93 天≈1季度)。
        #   防一次拉太宽历史窗口压库/被当全量导出工具滥用; 与限频(频次)正交, 这是"单次宽度"层护栏。
        max_days = int(getattr(settings, "EXTERNAL_BRIEF_MAX_RANGE_DAYS", 93) or 93)
        if span_days > max_days:
            raise HTTPException(
                400, f"start/end 跨度 {span_days} 天超过上限 {max_days} 天; 请缩短日期范围后重试"
            )
        is_realtime = (e_d == today)
        return {
            "period": "range", "date": None, "grain": "day" if span_days <= 1 else "week",
            "date_start": f"{s}T00:00:00+08:00",
            "date_end": now.isoformat() if is_realtime else f"{e}T23:59:59+08:00",
            "is_realtime": is_realtime, "start": s, "end": e,
            "range": {"start": s, "end": e, "span_days": span_days},
        }

    if period == "daily":
        d = _valid_date_or_400(date)
        d = _dt.date.fromisoformat(d) if d else today
        is_realtime = (d == today)
        return {
            "period": "daily", "date": d.isoformat(), "grain": "day",
            "date_start": f"{d.isoformat()}T00:00:00+08:00",
            "date_end": now.isoformat() if is_realtime else f"{d.isoformat()}T23:59:59+08:00",
            "is_realtime": is_realtime, "start": d.isoformat(), "end": d.isoformat(),
        }
    if period == "weekly":
        # date 给定 → 取该日所在 ISO 自然周的周一~周日(历史周整天; 若该周含今天则实时截止 now);
        # date 缺省 → 本周周一~今天(实时, 向后兼容 v1.0 行为不变)。
        base = _valid_date_or_400(date)
        base_d = _dt.date.fromisoformat(base) if base else today
        monday = base_d - _dt.timedelta(days=base_d.weekday())
        sunday = monday + _dt.timedelta(days=6)
        is_realtime = (base is None) or (sunday >= today and monday <= today)
        end_d = today if is_realtime else sunday
        return {
            "period": "weekly", "date": base_d.isoformat() if base else None, "grain": "week",
            "date_start": f"{monday.isoformat()}T00:00:00+08:00",
            "date_end": now.isoformat() if is_realtime else f"{sunday.isoformat()}T23:59:59+08:00",
            "is_realtime": is_realtime,
            "start": monday.isoformat(), "end": end_d.isoformat(),
        }
    month_start = today.replace(day=1)
    last_day = calendar.monthrange(today.year, today.month)[1]
    is_realtime = today.day != last_day
    return {
        "period": "monthly", "grain": "week",
        "date_start": f"{month_start.isoformat()}T00:00:00+08:00",
        "date_end": now.isoformat() if is_realtime else f"{today.isoformat()}T23:59:59+08:00",
        "is_realtime": is_realtime,
        "start": month_start.isoformat(), "end": today.isoformat(),
    }


def _build_brief(db: Session, period: str, date: str | None,
                 start: str | None = None, end: str | None = None) -> dict:
    """组装 brief(period 模式或 v1.2 start/end 任意区间模式)。

    start/end 同时给出 → 覆盖 period/date(历史标准周/任意区间回看, 架构 §一.1.1);
    否则走既有 period(+date) 窗口逻辑(向后兼容, 行为不变)。
    """
    win = _window(period, date, start=start, end=end)
    start_s, end_s = win["start"], win["end"]
    # KPI/退款四路/现金流: 复用 today_kpi(内部 _resolve_windows 已按北京时区解释 start/end;
    #   end==今天→实时截止 now; 否则整天; cash_routes_today 即同窗口四路+E)
    # #98 (2026-09-15): TTL 由 cache_ttl_for 自动感知(历史日 24h, 今天 60s), 原固定 60s 是 bug
    kpi = cached_call(f"today:kpi:None:{start_s}:{end_s}", cache_ttl_for(start_s, end_s, 60), A.today_kpi, db, None, start_s, end_s)
    cash = kpi.get("cash_routes_today") or {}
    # 涉诉率/四路涉诉金额: refund_decision_overview 同窗口(start/end 字符串), grain 按架构 §2.2 映射
    #   (daily→day, weekly/monthly→week); 走 cached_call 复用内部 refund:do:* 缓存(架构 §5.4)
    _do_key = f"refund:do:{start_s}:{end_s}:{win['grain']}"
    dec = cached_call(_do_key, cache_ttl_for(start_s, end_s, 60), A.refund_decision_overview, db, start_s, end_s, grain=win["grain"])
    summary = dec.get("summary", {}) or {}
    # 投诉增强(三源×registered/handled/backlog)
    complaint = _complaint_enhanced(db, start_s, end_s)
    # 接待专项增强(名单+会话+设备)
    reception_spec = _reception_specialty(db, start_s, end_s)
    out = {
        "period": win["period"],
        "date": win.get("date"),
        "date_start": win["date_start"],
        "date_end": win["date_end"],
        "generated_at": _now_cn().isoformat(),
        "reception": {"total": kpi.get("reception"), "by_specialty": reception_spec},
        "complaint": complaint,
        "refund": {
            "total_amount": kpi.get("refund_amount"),
            "total_count": kpi.get("refund_count"),
            "routes": {
                "route_a": cash.get("route_a"),
                "route_b": cash.get("route_b"),
                "route_c": cash.get("route_c"),
                "route_d": cash.get("route_d"),
            },
            "actual_cashflow_e": cash.get("actual_cashflow") or kpi.get("actual_cashflow"),
            "a_route": {
                "litigation_ratio": {"value": summary.get("complaint_ratio"),
                                     "yoy_value": None, "yoy_pct": None},
                "litigation_amount": summary.get("complaint_amount"),
            },
        },
        "_spec": _spec_brief(),
    }
    # v1.2: start/end 区间模式透出 range(便于调用方核对窗口; period 模式无此字段, 向后兼容)
    if win.get("range"):
        out["range"] = win["range"]
    return out


# ─────────────────────────────────────────────────────────────────────
# 投诉增强: 三源(内部/商户号/外部) × registered/handled/backlog
# ─────────────────────────────────────────────────────────────────────
def _complaint_enhanced(db: Session, start_s: str, end_s: str) -> dict:
    """三源 × 登记/完成/积压(口径与 complaints.complaint_summary/backlog 严格一致)。

      registered = 窗口内登记的投诉工单(register_time∈窗口)
                   + 315/卡商补充表净新增(complaint_date∈窗口, 减 matched 到窗口内工单的重叠, 并入外部)
      handled    = 窗口内真实完成的投诉工单(finish_time∈窗口 且 finish >= register+3min,
                   即 complaints._is_real_finish 语义; 排除同瞬填充/批量覆盖)
      backlog    = 窗口内登记且"归一化后未闭环"的存量:
                   状态非已完成 且 非(状态∈处理中/待处理/空 但有真实完成≥3min → _norm_status=已完成)
                   且 非(外部投诉工单 SN 命中 315 已办结登记 → 视为已处理, _external_done_sns)
    """
    # 北京时区窗口 → naive UTC 边界(timestamptz 列比较正确性, 与 analytics 一致)
    ts, te = S.parse_bj_window(start_s, end_s)
    src = _src_case()
    cat = _cat_case()
    sources = ["内部投诉", "商户号投诉", "外部投诉"]
    # 真实完成(SQL 表达 = complaints._is_real_finish 的 ≥3min): epoch(finish - register) >= 180s
    #   注: 不能用 func.make_interval(secs=...) — SQLAlchemy 会把关键字当函数参数名导致编译 TypeError
    real_finish = ServiceTicket.finish_time.isnot(None) & ServiceTicket.register_time.isnot(None) & (
        func.extract("epoch", ServiceTicket.finish_time - ServiceTicket.register_time) >= _REAL_FINISH_INTERVAL_SECS
    )
    by_source = []

    def _reg_conds(src_cond):
        conds = [cat == "投诉", src_cond, ServiceTicket.register_time.isnot(None)]
        if ts:
            conds.append(ServiceTicket.register_time >= ts)
        if te:
            conds.append(ServiceTicket.register_time <= te)
        return conds

    def _fin_conds(src_cond):
        conds = [cat == "投诉", src_cond, real_finish]
        if ts:
            conds.append(ServiceTicket.finish_time >= ts)
        if te:
            conds.append(ServiceTicket.finish_time <= te)
        return conds

    for source in sources:
        src_cond = src == source
        # registered: 窗口内登记(register_time)
        reg = db.scalar(
            select(func.count()).select_from(ServiceTicket).where(*_reg_conds(src_cond))
        ) or 0
        # handled: 窗口内真实完成(finish 落窗口 且 finish >= register+3min, 对齐 _is_real_finish)
        fin = db.scalar(
            select(func.count()).select_from(ServiceTicket).where(*_fin_conds(src_cond))
        ) or 0
        # backlog: 窗口内登记 且 归一化后未闭环
        #   ① 状态非已完成(含空)
        #   ② 状态∈{处理中/待处理/空} 但已有真实完成(≥3min) → _norm_status=已完成 → 剔除
        #   ③ 外部投诉工单 SN 命中 315 已办结登记(ext_done_sns) → 视为已处理 → 剔除
        bl_conds = _reg_conds(src_cond) + [
            or_(ServiceTicket.status.is_(None), ServiceTicket.status == "",
                ~ServiceTicket.status.in_(_DONE_STATUS)),
            ~and_(
                real_finish,
                or_(
                    ServiceTicket.status.is_(None),
                    ServiceTicket.status == "",
                    ServiceTicket.status.in_(_NORM_DONE_CANDIDATE_STATUS),
                ),
            ),
        ]
        if source == "外部投诉":
            # 315 登记已办结的 SN 对应的外部投诉工单(状态未同步)不进积压 — 与 complaints._query_backlog_rows 一致
            ext_done = _external_done_sns(db)
            if ext_done:
                bl_conds.append(or_(
                    ServiceTicket.device_sn.is_(None),
                    ~ServiceTicket.device_sn.in_(ext_done),
                ))
        bl = db.scalar(select(func.count()).select_from(ServiceTicket).where(*bl_conds)) or 0
        by_source.append({"source": source, "registered": int(reg), "handled": int(fin), "backlog": int(bl)})

    # 补充表(315/卡商) → 外部投诉 registered: 净新增 = 窗口内补充记录 - 已 matched 到"窗口内登记工单"的重叠
    #   (与 complaints.complaint_summary 的 total - overlap 同语义; 避免同一投诉在工单+补充表双计)
    def _suppl_overlap(model, done_only: bool = False, handled_side: bool = False) -> int:
        """补充记录 matched_ticket_no 指向"窗口内(登记|真实完成)投诉工单"的条数。

        - done_only=True:   只数 result∈办结类 的补充记录(handled 并入去重用)。
        - handled_side=True: 只数 matched 工单"窗口内真实完成"的那部分
          (即工单侧 handled 已计; 办结补充若指向这类工单则不应在补充侧重复计入)。
        与 complaints._source_reg_handled._overlap 完全同语义, 保证内外口径一致。
        """
        sub_conds = [cat == "投诉"]
        if handled_side:
            sub_conds += [
                ServiceTicket.finish_time.isnot(None),
                ServiceTicket.register_time.isnot(None),
                func.extract("epoch", ServiceTicket.finish_time - ServiceTicket.register_time) >= _REAL_FINISH_INTERVAL_SECS,
            ]
        else:
            sub_conds.append(ServiceTicket.register_time.isnot(None))
        if ts:
            sub_conds.append(ServiceTicket.finish_time >= ts if handled_side else ServiceTicket.register_time >= ts)
        if te:
            sub_conds.append(ServiceTicket.finish_time <= te if handled_side else ServiceTicket.register_time <= te)
        sub = select(ServiceTicket.ticket_no).where(*sub_conds)
        conds = [model.complaint_date.isnot(None), model.matched_ticket_no.isnot(None),
                 model.matched_ticket_no.in_(sub)]
        if done_only:
            conds.append(model.result.in_(_EXTERNAL_DONE_RESULTS))
        if ts:
            conds.append(model.complaint_date >= ts)
        if te:
            conds.append(model.complaint_date <= te)
        return db.scalar(select(func.count()).select_from(model).where(*conds)) or 0

    ext_reg = 0
    for model in (ExternalComplaint, CardComplaint):
        conds = [model.complaint_date.isnot(None)]
        if ts:
            conds.append(model.complaint_date >= ts)
        if te:
            conds.append(model.complaint_date <= te)
        suppl_total = db.scalar(select(func.count()).select_from(model).where(*conds)) or 0
        ext_reg += int(suppl_total) - _suppl_overlap(model)
    # 外部投诉桶并入补充表净新增(不为负)
    for item in by_source:
        if item["source"] == "外部投诉":
            item["registered"] = int(item["registered"]) + max(int(ext_reg), 0)

    # ⚠️ 外部投诉桶 handled 并入(2026-09-08, #33 修复内外口径不一致):
    #   complaints._source_reg_handled(内部看板) 2026-09-06 起把「315 补充表已办结
    #   (result∈办结类)」并入 external handled——否则大量"登记已办结但无工单/工单状态
    #   未同步"的 315 记录在内部看板算已处理、经外部只读 API 却显示未处理(用户反馈:
    #   "外部调用总是显示外部投诉没处理")。
    #   此处对齐: external handled += (315 办结数 − 已计入工单侧 handled 的重叠)。
    #   ⚠️ 卡商(card_complaints)不并入: 其 result 是另一套语义(已解决/已退款完成/
    #      投诉错误/流量虚...), 无统一办结集合, 套用 315 集合会恒为 0 → 保持不进 handled。
    #  (与 complaints._source_reg_handled include_suppl_external 分支逐行对齐)
    _suppl_done_conds = [ExternalComplaint.complaint_date.isnot(None),
                         ExternalComplaint.result.in_(_EXTERNAL_DONE_RESULTS)]
    if ts:
        _suppl_done_conds.append(ExternalComplaint.complaint_date >= ts)
    if te:
        _suppl_done_conds.append(ExternalComplaint.complaint_date <= te)
    _ext_done = db.scalar(select(func.count()).select_from(ExternalComplaint).where(*_suppl_done_conds)) or 0
    # matched 到"窗口内真实完成工单"的已办结 → 工单侧 handled 已计, 不重复加
    _ext_done_overlap_handled = _suppl_overlap(ExternalComplaint, done_only=True, handled_side=True)
    _ext_handled_extra = max(int(_ext_done) - int(_ext_done_overlap_handled), 0)
    if _ext_handled_extra:
        for item in by_source:
            if item["source"] == "外部投诉":
                item["handled"] = int(item["handled"]) + _ext_handled_extra

    total = {
        "registered": sum(int(x["registered"]) for x in by_source),
        "handled": sum(int(x["handled"]) for x in by_source),
        "backlog": sum(int(x["backlog"]) for x in by_source),
    }
    return {"total": total, "by_source": by_source}


# ─────────────────────────────────────────────────────────────────────
# 接待专项增强: 各正式专项 名单 + 会话数 + 设备数
# ─────────────────────────────────────────────────────────────────────
def _reception_specialty(db: Session, start_s: str, end_s: str) -> list[dict]:
    """名单=组织分配(person_specialty_assignments.is_current); 会话/设备=内容分桶(today_staff_efficiency)。

    #98 重构(2026-09-15): 桶映射改为 DB 字段驱动(`specialty.efficiency_field`),
      替代原 _BUCKET dict 硬编码; 历史专项(内部投诉/外部投诉)靠 is_active=False 自动排除,
      不再依赖 LEGACY_COMPLAINT_SPECIALTIES 常量名硬编码过滤。
      未配置 efficiency_field 的专项(临时/未配置)sessions/devices 显示 0, 不再硬兜底到"咨询接待"桶。
    """
    specs = list(db.scalars(
        select(Specialty).where(Specialty.is_active.is_(True)).order_by(Specialty.display_order, Specialty.name)
    ))
    if not specs:
        return []
    # #98 (2026-09-15): 改用 cached_call + topn=200, 与 cache_warmer._warm_analytics 的
    # `today:staff:None:{start}:{end}:200:0:person:None:None:None` key 逐字符对齐 → 命中预热缓存,
    # 避免冷加载(today_staff_efficiency 含 4 大 CTE ILIKE 全表扫, 冷加载 ~1.8s)。
    # TTL 由 cache_ttl_for 自动感知(历史日 24h, 今天 60s), 与 today:kpi 一致。
    eff = cached_call(
        f"today:staff:None:{start_s}:{end_s}:200:0:person:None:None:None",
        cache_ttl_for(start_s, end_s, 60),
        A.today_staff_efficiency,
        db, None, start_s, end_s, 200, False, "person",
        department=None, group_id=None, specialty_id=None,
    )
    person_rows = {r.get("name"): r for r in (eff.get("data") or []) if not r.get("is_virtual")}
    result = []
    for s in specs:
        # 组织分配名单(person_specialty_assignments.is_current, 含主/辅专项)
        # 桶映射: 从 specialty.efficiency_field 读 sp_*/pp_* 字段名(格式 'sp_xxx,pp_xxx');
        #   未配置则该专项不计入会话/设备统计(不再硬兜底到"咨询接待",避免错误归并)
        sp_field = pp_field = None
        if s.efficiency_field:
            parts = [p.strip() for p in s.efficiency_field.split(",") if p.strip()]
            if len(parts) >= 2:
                sp_field, pp_field = parts[0], parts[1]
            elif parts:
                sp_field = parts[0]
        assignments = list(db.scalars(
            select(PersonSpecialtyAssignment).where(
                PersonSpecialtyAssignment.specialty_id == s.id,
                PersonSpecialtyAssignment.is_current.is_(True),
            )
        ))
        pids = [a.person_id for a in assignments]
        persons = {}
        if pids:
            persons = {p.id: p for p in db.scalars(select(Person).where(Person.id.in_(pids))).all()}
        members = []
        sessions = 0
        devices = 0
        for a in assignments:
            p = persons.get(a.person_id)
            if p is None:
                continue
            members.append({
                "name": p.display_name,
                "position": p.position,
                "group_name": p.group_name,
                "is_primary": a.is_primary,
                "workload_ratio": a.workload_ratio,
            })
            if sp_field and pp_field:
                rec = person_rows.get(p.display_name) or {}
                sessions += int(rec.get(sp_field) or 0)
                devices += int(rec.get(pp_field) or 0)
        lead = None
        if s.current_lead:
            # ── #91 (2026-09-14) 口径标签 ─────────────────────────────────
            # 为什么必须带 role_label：
            #   本仓存在**两套独立**的负责人体系：
            #     · 专项负责人（业务）→ `specialties.current_lead_id`
            #     · 行政组长（管理）  → `groups.current_leader_id`
            #   二者**互不隶属**：同一人可在 A 专项是 lead、在 B 行政组只是成员，
            #   反之亦然。而原实现只透出 `{"role": "lead"}`（英文枚举），
            #   读者无法区分这到底是"专项负责人"还是"行政组长" ——
            #   用户实测反馈「明明有清晰的负责人、组长划分，为什么说法不一致」
            #   部分正源于此（同名不同体系，却都叫"负责人"）。
            #   故显式带**语义标签**，让消费方（外部工具/AI/前端）无需猜测。
            lead = {
                "name": s.current_lead.display_name,
                "role": "lead",
                "role_label": "专项负责人",
                "scope_note": "专项业务负责人（非行政组长；行政组长见 groups 体系）",
            }
        elif s.current_deputy_lead:
            lead = {
                "name": s.current_deputy_lead.display_name,
                "role": "deputy",
                "role_label": "专项副负责人",
                "scope_note": "专项业务副负责人（非行政副主管）",
            }
        result.append({
            "specialty": s.name,
            "is_temporary": s.is_temporary,
            "lead": lead,
            "members": members,
            "sessions": {"value": sessions},
            "devices": {"value": devices},
        })
    return result


def _spec_brief() -> dict:
    return {
        "money_unit": "金额单位=元(Numeric(12,2), 2位小数)",
        "period": "daily=当日0点起; weekly=整周(周一起); monthly=整月(未满月则1日到当前); 传 start/end 则优先(任意连续区间/历史标准周回看, end=今天实时截止)",
        "range": "start/end 同时给出 → 该闭区间(YYYY-MM-DD, 北京时间整天; end=今天则实时截止 now); 单日区间等价 period=daily&date",
        "litigation_ratio": "A路直退涉诉率=涉诉金额/A路(线上直退)金额×100; 涉诉=SN关联投诉工单(外部>商户号>内部) OR 备注含投诉标识; 清退不算",
        "complaint": ("registered=窗口内新登记(工单按 register_time; 315/卡商补充表按 complaint_date 并"
                        "入外部桶, 已 matched 到窗口内工单的去重); handled=窗口内真实完成(finish_time∈窗口 且"
                        "finish 晚于登记 ≥3min, 排除同瞬填充); backlog=窗口内登记且归一化后未闭环(状态非已完成,"
                        "排除有真实完成≥3min 的待处理; 315 已办结 SN 外部投诉视为已处理)"),
        # ── #91 (2026-09-14): 负责人口径必须显式声明 ────────────────────
        # 「负责人」在本系统中是**两个独立体系**，不加说明会被读成同一个概念 ——
        # 用户实测反馈的「负责人说法不一致」部分由此而来。
        "lead": ("reception.by_specialty[].lead = **专项负责人**（业务口径），来源 "
                 "`specialties.current_lead_id` + `specialty_leadership_history`；"
                 "role_label=专项负责人 / 专项副负责人。"
                 "⚠️ 与**行政组长**（管理口径，来源 `groups.current_leader_id`）是两套独立体系："
                 "同一人可在某专项任 lead 而在行政组仅为成员，反之亦然；"
                 "两套体系都变更时须分别调用 "
                 "`POST /specialties/{id}/transfer-lead` 与 `POST /groups/{id}/transfer-leadership`。"
                 "缺失语义标签易被误读为同一角色（见工单 #91）。"),
    }


# ─────────────────────────────────────────────────────────────────────
# 管理端点(内部 JWT AdminUser): token CRUD
# ─────────────────────────────────────────────────────────────────────
@router.get("/tokens", response_model=R, summary="外部只读 token 列表(内部)")
def list_tokens(db: DBSession, _: AdminUser):
    rows = db.scalars(select(ExternalAccessToken).order_by(ExternalAccessToken.id.desc())).all()
    # F-P2-5: created_by(username) → display_name 映射(创建人可读姓名), 找不到则保留 username
    unames = {t.created_by for t in rows if t.created_by}
    disp_map: dict[str, str] = {}
    if unames:
        for u in db.scalars(select(User).where(User.username.in_(unames))).all():
            disp_map[u.username] = u.display_name or u.qw_name or u.username
    items = []
    for t in rows:
        d = ExternalTokenOut.model_validate(t)
        # F-P2-5: created_by(username) → display_name; pydantic 用 model_copy 规范更新
        d = d.model_copy(update={"created_by_display": disp_map.get(t.created_by) if t.created_by else None})
        items.append(d)
    return R(data=items)


@router.post("/tokens", response_model=R, summary="生成外部只读 token(明文仅此一次, 内部)")
def create_token(payload: ExternalTokenCreate, db: DBSession, me: AdminUser):
    raw, row = S.generate_token(
        db, payload.name, payload.scopes,
        rate_limit_per_min=payload.rate_limit_per_min,
        expires_at=payload.expires_at,
        owner_note=payload.owner_note,
        created_by=me.username,
    )
    return R(data={
        "id": row.id,
        "name": row.name,
        "token": raw,  # 明文仅此一次
        "token_prefix": row.token_prefix,
        "scopes": row.scopes,
        "rate_limit_per_min": row.rate_limit_per_min,
        "expires_at": row.expires_at,
        "owner_note": row.owner_note,
    })


@router.patch("/tokens/{token_id}", response_model=R, summary="更新外部只读 token(内部)")
def update_token(token_id: int, payload: ExternalTokenUpdate, db: DBSession, _: AdminUser):
    row = db.get(ExternalAccessToken, token_id)
    if not row:
        raise HTTPException(404, "token 不存在")
    if payload.name is not None:
        row.name = payload.name[:64]
    if payload.scopes is not None:
        invalid = [s for s in payload.scopes if s not in S.SCOPES]
        if invalid:
            raise HTTPException(400, f"非法 scope: {', '.join(invalid)}")
        row.scopes = list(dict.fromkeys(payload.scopes))
    # v1.2: rate_limit_per_min 三值语义(0=不限 / null=全局默认 / >0=个性化)。
    #   用 model_fields_set 判断"是否显式传入"(含显式 null), 才能把限频"清回全局默认";
    #   未传该字段(=部分更新)则保持原值, 与 name/scopes 部分更新语义一致。
    if "rate_limit_per_min" in payload.model_fields_set:
        row.rate_limit_per_min = payload.rate_limit_per_min
    if payload.owner_note is not None:
        row.owner_note = payload.owner_note[:255]
    if payload.enabled is not None:
        row.enabled = payload.enabled
    db.commit()
    return R(data=ExternalTokenOut.model_validate(row))


@router.post("/tokens/{token_id}/revoke", response_model=R, summary="吊销外部只读 token(内部)")
def revoke_token(token_id: int, db: DBSession, me: AdminUser):
    ok = S.revoke_token(db, token_id, revoked_by=me.username)
    if not ok:
        raise HTTPException(404, "token 不存在")
    return R(data={"ok": True})


@router.post("/tokens/{token_id}/enable", response_model=R, summary="恢复已吊销 token(内部)")
def enable_token(token_id: int, db: DBSession, me: AdminUser):
    """恢复已吊销的 token。

    A-20260910-003 (#43) v1.3: 把吊销从「终态」改为「可逆状态」, 误吊销无需重建 token。
    幂等: 已启用的 token 再调用返回 200 且状态不变。
    """
    row = S.enable_token(db, token_id, restored_by=me.username)
    if row is None:
        raise HTTPException(404, "token 不存在")
    # DB 时区为 timestamptz, 统一转北京时间返回
    restored_at = _now_cn()
    data = ExternalTokenOut.model_validate(row).model_dump(mode="json")
    data.update({"restored_by": me.username, "restored_at": restored_at.isoformat()})
    # 已过期的 token 恢复后仍不可用 —— 明确告警, 不静默失败
    if row.expires_at is not None and row.expires_at <= _now_cn():
        data["warning"] = "该 token 已过期, 恢复后仍不可用, 请同时更新过期时间"
    return R(data=data)


@router.post("/tokens/{token_id}/rotate", response_model=R, summary="重新生成 token 明文(旧 token 立即失效)")
def rotate_token(token_id: int, db: DBSession, _: AdminUser):
    """换钥: 覆盖 hash/prefix, 旧明文立即失效, 新明文仅本响应返回一次。

    这是「明文只显示一次」约束下的唯一合法补救路径 —— 不是回看历史明文, 而是换一把新锁。
    """
    res = S.rotate_token(db, token_id)
    if res is None:
        raise HTTPException(404, "token 不存在")
    raw, row = res
    return R(data={
        "id": row.id,
        "name": row.name,
        "token": raw,  # 明文仅此一次
        "token_prefix": row.token_prefix,
        "enabled": row.enabled,
        "notice": "旧 token 已立即失效, 请马上更新调用方配置",
    })


@router.get("/tokens/{token_id}/setup-package", response_model=R, summary="生成接入配置包(内部)")
def token_setup_package(
    token_id: int,
    db: DBSession,
    request: Request,
    _: AdminUser,
    base_url: str | None = Query(None, description="系统对外访问地址; 缺省按请求推导"),
):
    """返回该 token 的可复制接入配置包(Markdown)。

    ⚠️ 配置包中 token 位置固定为占位符 —— 本端点**不返回明文**, 不是明文回看后门。
    调用方应保存生成时拿到的明文, 或走 rotate 换钥 + 重新复制配置包。
    """
    row = db.get(ExternalAccessToken, token_id)
    if not row:
        raise HTTPException(404, "token 不存在")
    # 对外地址优先显式传入(反向代理下 request.base_url 可能是内网地址)
    effective_base = (base_url or str(request.base_url)).rstrip("/")
    pkg = setup_package.build_setup_package(
        row, effective_base,
        global_rate_limit=settings.EXTERNAL_RATE_LIMIT_DEFAULT_PER_MIN,
    )
    pkg.update({
        "token_id": row.id,
        "name": row.name,
        "token_prefix": row.token_prefix,
        "enabled": row.enabled,
    })
    return R(data=pkg)


@router.get("/config", response_model=R, summary="外部只读接口启用状态(内部)")
def external_config(db: DBSession, _: AdminUser):
    return R(data={
        "enabled": settings.EXTERNAL_API_ENABLED,
        "default_rate_limit_per_min": settings.EXTERNAL_RATE_LIMIT_DEFAULT_PER_MIN,
        "scopes": list(S.SCOPES),
    })
