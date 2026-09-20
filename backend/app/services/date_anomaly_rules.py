"""不可能日期判据（工单 #169，设计: docs/design/采集语义增强设计-20260920.md §3.2）
—— 导入期与扫描期的**单一来源**。

为什么要有这个模块（判据同源，与 text_anomaly_rules 同一纪律）：
同一件事要在两个时点判断 —— 导入期（治新增，`upsert.run_upsert` 拿到**原始值**，
走 Python 侧 `impossible_kinds`）与扫描期（治存量，D5 在库上跑 SQL，只能看到已入库的值）。
若两边各写一套阈值，迟早漂移成「导入期拦的口径 ≠ 扫描期报的口径」
（同 A-20260910-004 菜单漂移的教训）。故阈值常量、类型枚举、白名单只在此定义一份。

四类判据（与设计 §3.2 表格一一对应）：
  A 年份越界  `MIN_YEAR ~ max_year()`（当前年+1）
  B 未来日期  `> now() + FUTURE_TOLERANCE_DAYS`
  C 时间倒挂  完成列 < 创建列（列对按阶段序推断，推不出则跳过）
  D 兜底值聚集 单一日期占非空行数 ≥ `CLUSTER_RATIO` **且**该日期形如兜底值

⚠️ D 的「形如兜底值」这一半是**必须的**（工单 #169 验收 1：真实集中日期（大促）不误报）：
纯占比判据会把大促 / 月初批量结算这类**真实集中日期**一并报出 —— 那是业务事实，
报出来只能靠人工反复驳回，等于把误报成本转嫁给使用者。加上形态判据后：
大促（如 2025-06-18）不报，Excel 兜底（2020-01-01）报。
**已知漏报**：非 01-01/12-31 形态的默认值（如 2021-03-15 被批量填充）只能靠 A/B/C
或人工发现 —— 宁可漏报，不可天天误报。

时区铁律（AGENTS.md / docs/standards/数据时间字段使用规范.md）：
所有「日期」一律按 **Asia/Shanghai** 取，禁止按容器本地时区（UTC）推断 ——
否则北京时间 00:00~08:00 的数据会整体算到前一天。
"""

import datetime as _dt

# 时区（AGENTS.md 铁律：时区换算走 Asia/Shanghai）
_TZ_CN = _dt.timezone(_dt.timedelta(hours=8))

# ── 判据 A：年份越界（常量可调）────────────────────────────────
MIN_YEAR = 2015          # 项目数据起点（2015 年前的业务时间必为兜底/笔误）
MAX_YEAR_OFFSET = 1      # 上界 = 当前年 + 1（跨年预填单 / 时区跨年留容差）

# ── 判据 B：未来日期 ──────────────────────────────────────────
FUTURE_TOLERANCE_DAYS = 1   # 留 1 天容差覆盖时区换算与同步延迟，避免边界误报

# ── 判据 D：兜底值聚集 ────────────────────────────────────────
CLUSTER_RATIO = 0.30     # 单一日期占该列非空行数的比例下限
CLUSTER_MIN_ROWS = 3     # 单日期至少出现这么多次才算「聚集」—— 1~2 行只是个别坏值
                         # （判据 A/B 或人工就能看见），报成「聚集」会稀释真聚集的信号
CLUSTER_TOP_N = 5        # metrics.top_dates 保留的日期数
# 「形如兜底值」的月日集合（判据 D 的形态判据，见模块头「已知漏报」说明）
DEFAULT_LIKE_MMDD = ("01-01", "12-31")

# ── 类型枚举（finding.metrics.kinds / 留痕 reject_reason 共用）──
KIND_YEAR_RANGE = "year_range"    # A 年份越界
KIND_FUTURE = "future"            # B 未来日期
KIND_REVERSED = "reversed"        # C 时间倒挂（完成列 < 创建列）
KIND_CLUSTER = "cluster"          # D 兜底值聚集

KIND_TEXT: dict[str, str] = {
    KIND_YEAR_RANGE: "年份越界",
    KIND_FUTURE: "未来日期",
    KIND_REVERSED: "时间倒挂",
    KIND_CLUSTER: "兜底值聚集",
}

# ── 留痕分类（写入 ingest_rejected_rows.reject_category，工单 #169）──
CATEGORY_DATE_IMPOSSIBLE = "date_impossible"

# ── 白名单：语义上「本就可能在未来 / 很久以后」的计划类列 ──────────────
# 判据 A/B 对它们一律跳过 —— 报它们等于天天误报（服务有效期本就该在未来）。
# ⚠️ 新增白名单项必须写明理由与证据（与 text_anomaly_rules.NEWLINE_ALLOW_COLUMNS 同一纪律），
#    否则白名单会退化成「误报开关」。列名取自 2026-09-20 生产 data_sources 表的日期列清单。
PLANNED_DATE_COLUMNS: dict[str, str] = {
    "end_date": "服务/保修结束日期（device_combos），本就应在未来",
    "end_time": "会话结束时间（scrm_sessions），排期会话可在未来",
    "expired_at": "标签过期时间（device_tag_rel），本就应在未来",
    "service_valid_until": "服务有效期截止，本就应在未来",
    "before_serve_valid_date": "服务前有效期，本就应在未来",
    "after_serve_valid_date": "服务后有效期，本就应在未来",
}

# ── 判据 C 的阶段序推断 ────────────────────────────────────────
# 值越小越早发生；同阶段内两列**不配对**（谁先谁后无判据，配了就是误报源）。
# 按**列名精确匹配**而非后缀模糊匹配：模糊匹配会把 refund_time / audit_time 这类
# 同阶段列错配成倒挂对，产生大量误报（宁可少报，不可误报）。
# 列名取自 2026-09-20 生产 data_sources 表的日期列清单（系统审计列已排除，见探测器）。
_STAGE_RULES: tuple[tuple[int, tuple[str, ...]], ...] = (
    (0, ("created_at_raw", "register_time", "register_time_raw", "apply_time", "start_time",
         "order_time", "trade_time", "complaint_date", "problem_create_time")),
    (1, ("op_time", "operation_time", "handle_time", "audit_time", "dispatch_time",
         "active_time", "last_dispatch_time", "transfer_time", "sign_time",
         "consult_time", "last_urge_time")),
    (2, ("finish_time", "end_time", "end_date", "complete_time", "refund_time",
         "actual_refund_time", "updated_at_raw", "return_time", "last_consult_time")),
)


def now_cn() -> _dt.datetime:
    """当前北京时间。"""
    return _dt.datetime.now(_TZ_CN)


def _as_cn_date(value):
    """把值归一为**北京日期**（date 原样；aware datetime 转北京；naive 视为北京本地；
    `YYYY-MM-DD` 字符串按原样取日期）。

    与扫描期 `_date_expr` 的语义逐条对应：timestamptz → `AT TIME ZONE 'Asia/Shanghai'`；
    naive timestamp 本身就是本地时间（不再换算）。弄错会让判据在日界处整体偏一天。
    接受 ISO 串是因为探测器的 `metrics.top_dates[].date` 就是 `date` 列的字符串形态。
    """
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        return (value.astimezone(_TZ_CN) if value.tzinfo else value).date()
    if isinstance(value, _dt.date):
        return value
    if isinstance(value, str):
        try:
            return _dt.date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def max_year(now=None) -> int:
    """年份上界 = 当前年 + `MAX_YEAR_OFFSET`。"""
    return (now or now_cn()).year + MAX_YEAR_OFFSET


def is_planned_column(column) -> bool:
    """该列是否属「计划类」（本就可能在未来/很久以后）→ 判据 A/B 跳过。"""
    return bool(column) and column in PLANNED_DATE_COLUMNS


def is_impossible_year(value, *, now=None) -> bool:
    """判据 A：年份落在 [MIN_YEAR, max_year] 之外。"""
    d = _as_cn_date(value)
    if d is None:
        return False
    return not (MIN_YEAR <= d.year <= max_year(now))


def is_future(value, *, now=None) -> bool:
    """判据 B：北京日期 > 今天 + `FUTURE_TOLERANCE_DAYS`。"""
    d = _as_cn_date(value)
    if d is None:
        return False
    return d > (now or now_cn()).date() + _dt.timedelta(days=FUTURE_TOLERANCE_DAYS)


def is_default_like_date(value) -> bool:
    """判据 D 的形态判据：该日期形如「兜底/默认值」（月日为 01-01 或 12-31）。"""
    d = _as_cn_date(value)
    if d is None:
        return False
    return f"{d.month:02d}-{d.day:02d}" in DEFAULT_LIKE_MMDD


def impossible_kinds(value, *, column=None, now=None) -> list[str]:
    """Python 侧判据（导入期用）：时间值 → 命中的类别列表（A/B）。

    `column` 为计划类列（`PLANNED_DATE_COLUMNS`）→ 不判 A/B。
    与扫描期 `sql_predicates` **逐条对应**（同一组常量），
    一致性由 tests/test_governance_date_anomaly.py 断言。
    """
    if value is None or is_planned_column(column):
        return []
    out: list[str] = []
    if is_impossible_year(value, now=now):
        out.append(KIND_YEAR_RANGE)
    if is_future(value, now=now):
        out.append(KIND_FUTURE)
    return out


def sql_predicates(date_expr: str, *, now=None, column=None) -> list[tuple[str, str]]:
    """SQL 侧判据（扫描期用）：北京日期表达式 → [(类型, WHERE 条件)]。

    `date_expr` 由调用方按**列类型**生成（复用 detector `_date_expr`，与 D2 同一口径）
    —— 本模块不猜列类型，避免把时区规则混进判据。
    """
    if is_planned_column(column):
        return []
    ref_date = "(now() AT TIME ZONE 'Asia/Shanghai')::date"
    return [
        (KIND_YEAR_RANGE,
         f"extract(year from {date_expr}) NOT BETWEEN {MIN_YEAR} AND {max_year(now)}"),
        (KIND_FUTURE, f"{date_expr} > {ref_date} + {FUTURE_TOLERANCE_DAYS}"),
    ]


def stage_of(column: str):
    """列名 → 阶段序（0 创建 / 1 处理 / 2 完成）；不在阶段表里 → None（不猜）。"""
    for stage, names in _STAGE_RULES:
        if column in names:
            return stage
    return None


def reversed_pairs(columns) -> list[tuple[str, str]]:
    """从表的列清单推断 (完成列, 创建列) 对（判据 C）。

    规则：阶段序小的列 × 阶段序大的列（同阶段不配对），返回 `(晚阶段列, 早阶段列)`。
    列名不在阶段表里的一律跳过 —— 推不出就不猜（设计 §3.2「推断不出则跳过」）。
    返回顺序由 `columns` 顺序决定（可复现）。
    """
    staged = [(c, stage_of(c)) for c in columns]
    staged = [(c, s) for c, s in staged if s is not None]
    out: list[tuple[str, str]] = []
    for i, (c1, s1) in enumerate(staged):
        for c2, s2 in staged[i + 1:]:
            if s1 < s2:
                out.append((c2, c1))
            elif s2 < s1:
                out.append((c1, c2))
    return out
