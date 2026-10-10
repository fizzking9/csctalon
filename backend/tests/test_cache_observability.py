"""缓存预热可观测性守护测试 (#92)

## 为什么需要

生产事故（2026-09-14）：`complaint:subject` 缓存预热**连续 5 天全部失败**，
却**没有任何指标暴露** —— 因为 `_warm_one` 的 `except` 把异常降级成一条
WARNING，而「预期命中的缓存 0 命中」这一事实无人可见。

实测影响（nginx access.log）：
| 端点 | 前端调用 | 缓存命中 | 平均耗时 |
|---|---|---|---|
| `complaints/subject-analysis` | 115 | **0** | 0.19~0.25s |
| `complaints/analysis` | 90 | **0** | 0.43~0.57s |
| `analytics/today/kpi`（对照） | 2000+ | 全部 | **0.000s** |

同型告警 6792 条，占 app.log **28.9% 字节**。

## 本测试锁定两条不变式

1. **预热必须产出汇总**（不能只有孤立的单条 WARNING）——
   计数 + 失败清单，让失败一眼可见；
2. **缓存调用必须记录命中率** —— 使「0 命中」可被观测，
   而不是只能靠人工比对接口耗时反推。
"""

from __future__ import annotations

import inspect

import pytest


# ── 预热汇总 ──────────────────────────────────────────────────────────
def test_warm_one_records_outcome():
    """`_warm_one` 的三个出口（warmed/fresh/failed）都必须记录结果。

    回归: 原来失败只 `logger.warning(...)` 就 return，聚合层无从统计。
    """
    from app.services import cache_warmer as W

    src = inspect.getsource(W._warm_one)
    assert "_record_warm_outcome" in src, (
        "_warm_one 必须记录预热结果（warmed/fresh/failed），否则失败无法被聚合（#92）"
    )
    # 三个分支都要覆盖
    assert src.count("_record_warm_outcome") >= 4, (
        "warmed/fresh/failed(含 None 结果) 各分支都需记录（#92）"
    )


def test_warm_stats_report_exists_and_warns_on_failure():
    """汇总函数必须存在，且**有失败时用 WARNING**（不能降级为 INFO）。

    设计意图：预热失败必须是**可见的告警**，而不是淹没在 INFO 里的噪音。
    """
    from app.services import cache_warmer as W

    assert hasattr(W, "_warm_stats_report"), "缺少预热汇总报告函数（#92）"
    src = inspect.getsource(W._warm_stats_report)
    assert "logger.warning" in src, "有失败时必须 WARNING（#92）"
    assert "logger.info" in src, "全成功时用 INFO（#92）"
    # 失败清单要出现在文案里（否则只知道"失败了"不知"哪个失败"）
    assert "失败项" in src or "failed" in src.lower()


def test_run_resets_and_reports_stats():
    """`_run` 必须**复位累加器**再执行、执行后**汇总上报**。

    否则统计会跨轮累积，第二次预热报出的失败数里混着上一轮的（误导）。
    """
    from app.services import cache_warmer as W

    src = inspect.getsource(W._run)
    assert src.count("clear()") >= 3, "_run 必须复位 warmed/fresh/failed 三个累加器（#92）"
    assert "_warm_stats_report" in src, "_run 收尾必须调用汇总上报（#92）"


def test_stats_report_is_bounded():
    """失败清单必须**限长**，否则会重演「6792 条告警占 28.9% 日志」的问题。"""
    from app.services import cache_warmer as W

    src = inspect.getsource(W._warm_stats_report)
    assert "[:10]" in src, "失败清单必须截断（#92：原事故正是告警量撑爆日志）"


# ── 缓存命中率 ────────────────────────────────────────────────────────
def test_cached_call_records_hit_and_miss():
    """`cached_call` 的命中与未命中两条路径都必须记录统计。"""
    from app.core import redis_client as RC

    src = inspect.getsource(RC.cached_call)
    assert src.count("_record_cache_stat") >= 2, (
        "命中/未命中都要记录，否则算不出命中率（#92）"
    )
    assert "hit=True" in src and "hit=False" in src


def test_cache_stats_snapshot_shape():
    """快照结构必须含 hit/miss/total/hit_rate，供前端直接渲染。

    `hit_rate=0` 正是「静默退化」的信号（预期命中却 0 命中）。
    """
    from app.core import redis_client as RC

    RC.cache_stats_reset()
    RC._record_cache_stat("today", hit=True, key="today:kpi:None:2026-09-14:2026-09-14")
    RC._record_cache_stat("complaint", hit=False, key="complaint:subject:2026-09-14:x")
    RC._record_cache_stat("complaint", hit=False, key="complaint:subject:2026-09-14:y")

    snap = RC.cache_stats_snapshot()
    assert snap["today"]["hit"] == 1
    assert snap["today"]["hit_rate"] == 1.0
    assert snap["complaint"]["miss"] == 2
    assert snap["complaint"]["hit_rate"] == 0.0, "0 命中必须显式可读（#92 核心信号）"
    RC.cache_stats_reset()


def test_key_shape_is_normalized():
    """key 形态必须**归一化**（日期/数字→占位符），否则样本会被高基数 key 撑爆。"""
    from app.core import redis_client as RC

    assert RC._normalize_key_shape("today:kpi:None:2026-09-14:2026-09-14") == (
        "today:kpi:None:<date>:<date>"
    )
    assert RC._normalize_key_shape("complaint:detail:x:y:500:1") == (
        "complaint:detail:x:y:<n>:<n>"
    )
    # ⚠️ 分钟级缓存的 key 形如 `...:2026-09-10T12:30` —— 整体落在**同一段**
    #    （不含 `:` 之外的分隔），故被识别为一个 <date>。
    #    注意 `2026-09-10T12:30` 自身含冒号，若 key 以冒号分隔会让它裂成两段
    #    （`<date>` + `<n>`）—— 归一化按冒号切段，这里如实断言其行为，
    #    目的仅保证「值不泄漏进样本」而非追求最优形态。
    shape = RC._normalize_key_shape("k:2026-09-10T12:30")
    assert "2026-09-10" not in shape, "具体日期必须被占位，不得进入样本"
    assert "12:30" not in shape, "具体时刻必须被占位，不得进入样本"
    assert shape.startswith("k:<date>")


def test_key_group_extraction():
    """分组取第一段 —— 保证 `today:*` 与 `today:kpi:*` 归入同一组。"""
    from app.core import redis_client as RC

    assert RC._key_group("today:kpi:None:a:b") == "today"
    assert RC._key_group("complaint:subject:x") == "complaint"
    assert RC._key_group("noseparator") == "noseparator"
    assert RC._key_group("") == "unknown"


def test_cache_info_exposes_hit_stats():
    """缓存管理接口必须暴露 `hit_stats`（否则指标算了却看不到）。"""
    from app.api.v1 import cache_admin

    src = inspect.getsource(cache_admin.cache_info)
    assert "hit_stats" in src, "cache/info 必须返回 hit_stats（#92）"


@pytest.mark.parametrize("fn_name", ["cache_stats_snapshot", "cache_stats_reset"])
def test_public_helpers_exist(fn_name: str):
    """命中率快照/复位必须是公开函数（供 API 与运维脚本使用）。"""
    from app.core import redis_client as RC

    assert hasattr(RC, fn_name), f"缺少 {fn_name}（#92）"
