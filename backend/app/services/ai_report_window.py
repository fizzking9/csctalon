"""AI 报告预生成窗口计算模块 (工单 #132 批次 A)

设计:
- 纯计算函数, 无 IO(不查 DB / 不读 Redis / 不读 settings), 便于单测
- 周报/月报预生成窗口截止**昨天**整天(不含当天不完整数据, 用户原话:
  "时间颗粒度不需要到今天的小时数据, 今天的数据可以不纳入对比;
   截止到前一天整天就可以了")
- 日报保持现行 date=None(随同步刷新当天实时数据, refresh_on_sync 生效)

退化逻辑:
- weekly: 若今日是周一, "本周一~昨天"区间不存在(昨天属上周)→ 退化为上周一~上周日整周
- monthly: 若今日是1号, "本月1日~昨天"区间不存在(昨天属上月)→ 退化为上月1日~上月末整月

工单 #132 仅实现 compute_pregen_window 并接入 generate_for_template;
compute_compare_window 先实现但**不接入**(留给批次 B 随草稿结构化一起用)。
"""

import datetime as _dt

# 时区(AGENTS.md 铁律: 时区换算走 Asia/Shanghai)
_TZ_CN = _dt.timezone(_dt.timedelta(hours=8))

# 滑动窗口频率 → 近 N 天(截止昨天整天)。每天凌晨生成一份「近N天 vs 前一等长N天」对比快照;
# 与自然周/月(weekly/monthly, 周一/1号才出整周期)并存为不同模板, 互不替代。
# 前一对比窗口不在这里返: worker 拿 start/end 调 compute_compare_window 即得等长前N天。
ROLLING_DAYS = {"rolling_7d": 7, "rolling_30d": 30}


def _now_cn_date() -> _dt.date:
    """当前北京时间日期(AGENTS.md 铁律: 时区换算)。"""
    return _dt.datetime.now(_TZ_CN).date()


def compute_pregen_window(period: str, ref_date: _dt.date | None = None) -> dict | None:
    """计算周报/月报预生成窗口(截止昨天整天, 不含当天)。

    - period="daily" → 返回 None(日报保持现行 date=None 实时数据, refresh_on_sync 生效)
    - period="weekly":
        * 正常: 本周一 ~ 昨天(若今日不是周一)
        * 退化: 上周一 ~ 上周日整周(若今日是周一, "本周一~昨天"跨到上周不存在)
    - period="monthly":
        * 正常: 本月1日 ~ 昨天(若今日不是1号)
        * 退化: 上月1日 ~ 上月末整月(若今日是1号, "本月1日~昨天"跨到上月不存在)
    - period="rolling_7d" / "rolling_30d":
        * 截止昨天的近 N 天恒定滑动窗口(每天一份, 长度 N 不跳动), 无退化;
          前一等长 N 天对比窗口由调用方对返回的 start/end 调 compute_compare_window 得到。

    Args:
        period: "daily" / "weekly" / "monthly"
        ref_date: 基准日期(可选, 缺省=今日北京时间); 主要供单元测试注入固定日期

    Returns:
        dict | None:
            {start: "YYYY-MM-DD", end: "YYYY-MM-DD",
             snapshot_date: "YYYY-MM-DD"(今日), label: str}
            或 None(daily)
    """
    today = ref_date if ref_date is not None else _now_cn_date()
    snapshot_date = today.isoformat()

    if period == "daily":
        # 日报保持现行: date=None 让 build_messages 走 period=daily 当日实时数据
        return None

    yesterday = today - _dt.timedelta(days=1)

    if period in ROLLING_DAYS:
        # 滑动窗口: 截止昨天的近 N 天(含昨天), 每天一份, 长度恒定 N, 不随星期/月初跳动。
        #   例 rolling_7d 在 2026-09-23 生成 → 09-16~09-22; 前一等长周期(由
        #   compute_compare_window 推导) = 09-09~09-15。无退化情形(N 固定, 永远成立)。
        n = ROLLING_DAYS[period]
        start = yesterday - _dt.timedelta(days=n - 1)
        return {
            "start": start.isoformat(),
            "end": yesterday.isoformat(),
            "snapshot_date": snapshot_date,
            "window_days": n,
            "label": f"{period}(近{n}天·截止昨天 {start.isoformat()}~{yesterday.isoformat()})",
        }

    if period == "weekly":
        # 本周一(ISO 周一为一周起点, weekday()=0 表示周一)
        monday_this = today - _dt.timedelta(days=today.weekday())
        if yesterday < monday_this:
            # 退化: 今日是周一 → 昨天属上周 → 本周一~昨天区间不存在
            # 取上周一~上周日整周(全天数据, 与本周全天对等)
            monday_last = monday_this - _dt.timedelta(days=7)
            sunday_last = monday_last + _dt.timedelta(days=6)
            return {
                "start": monday_last.isoformat(),
                "end": sunday_last.isoformat(),
                "snapshot_date": snapshot_date,
                "label": (
                    f"weekly(退化取上周整周 {monday_last.isoformat()}~"
                    f"{sunday_last.isoformat()})"
                ),
            }
        return {
            "start": monday_this.isoformat(),
            "end": yesterday.isoformat(),
            "snapshot_date": snapshot_date,
            "label": (
                f"weekly(本周一~昨天 {monday_this.isoformat()}~"
                f"{yesterday.isoformat()})"
            ),
        }

    if period == "monthly":
        first_this = today.replace(day=1)
        if yesterday < first_this:
            # 退化: 今日是1号 → 昨天属上月 → 本月1日~昨天区间不存在
            # 取上月1日~上月末整月(全天数据, 与本月全天对等)
            last_month_end = first_this - _dt.timedelta(days=1)
            last_month_start = last_month_end.replace(day=1)
            return {
                "start": last_month_start.isoformat(),
                "end": last_month_end.isoformat(),
                "snapshot_date": snapshot_date,
                "label": (
                    f"monthly(退化取上月整月 {last_month_start.isoformat()}~"
                    f"{last_month_end.isoformat()})"
                ),
            }
        return {
            "start": first_this.isoformat(),
            "end": yesterday.isoformat(),
            "snapshot_date": snapshot_date,
            "label": (
                f"monthly(本月1日~昨天 {first_this.isoformat()}~"
                f"{yesterday.isoformat()})"
            ),
        }

    # 未识别的 period(如 "range"): 不应被预生成调用, 返回 None 让 caller 走原逻辑
    return None


def compute_compare_window(start: str, end: str) -> tuple[str, str]:
    """等长前对比窗口(紧贴 start 之前取同长度天数)。

    工单 #132 实现, 但**不接入 worker**; 留给批次 B 随草稿结构化一起用(对比段注入)。

    例:
      start=2026-09-15(周一), end=2026-09-16(周二), 长度2天
      → 前对比窗口 2026-09-13(周日)~2026-09-14(周一) 紧贴 start 之前2天

    Args:
        start: YYYY-MM-DD
        end:   YYYY-MM-DD

    Returns:
        (prev_start, prev_end) — 均为 YYYY-MM-DD
    """
    s = _dt.date.fromisoformat(start)
    e = _dt.date.fromisoformat(end)
    span_days = (e - s).days + 1  # 含首尾, 等长天数
    prev_end = s - _dt.timedelta(days=1)
    prev_start = prev_end - _dt.timedelta(days=span_days - 1)
    return prev_start.isoformat(), prev_end.isoformat()
