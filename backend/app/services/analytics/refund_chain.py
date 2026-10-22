"""退款链路: refund_withdrawal_* 概览/时间分布/维度/分面/明细/日拆分/沉睡链路。"""

import re as _re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import column, func, select, table, text
from sqlalchemy.orm import Session

# #334: 链路分析是按单业务台账语义（非蓄水池核算）—— A 路径=「转入提现账户」退款
#   工单(被 std A 路采纳规则排除), 按单时效(D1发起→D2入钱包→D4提现)需要工单锚点
#   (B 路进水 remark 无 RF 单号, 生产实测无法纯 B 还按单还原) → 退款单读
#   std_refund_online_all 全量镜像(ods.refunds 同构透传, 含被排除行);
#   B 成功提现与 D 手动退款取 std 表(采纳口径表层固化)。
#   蓄水池/水位核算与链路分析无关 —— 那是纯 B 一本账(口径文档 §四)。
from app.models.refund_standard import StdRefundManual, StdWalletFlow

# 跨子模块引用(拆分自 app.services.analytics, 原同文件内直接调用)
from app.services.analytics._core import (
    _AGE_PARAM_TO_BUCKET,
    _TIME_BINS,
    TZ,
    _active_age_bucket,
    _age_days_between,
    _annotate_chain_litigation,
    _bin_label,
    _chain_alias_map,
    _chain_complaint_filter,
    _classify_method,
    _device_dim_attrs,
    _device_dim_matched_sns,
    _device_dim_options,
    _is_meta_junk_operator,
    _naive,
    _norm_chain_operator,
    _pct,
)

_RFN_RE = _re.compile(r'退费单\s*([A-Za-z0-9]+)')

# #334: 全状态语义读 std_refund_online_all 全量镜像(ods.refunds 同构透传, 含被采纳规则排除行)。
#   用轻量 table()/column() 而非 ORM 实体: 视图只读, 且下方会对行内时间字段原地 naive 归一,
#   ORM 实体挂载 Session 后赋值会在 autoflush 时对只读视图发 UPDATE 报错。
_REFUND_ALL = table(
    "std_refund_online_all",
    column("refund_no"), column("platform_order_no"), column("device_sn"),
    column("refund_method"), column("refund_reason"), column("refund_amount"), column("actual_amount"),
    column("status"), column("package_name"), column("merchant_name"),
    column("auditor"), column("operator"),
    column("created_at_raw"), column("created_at"), column("updated_at_raw"), column("updated_at"),
)

# ── #344 Process-level memoization: _build_chain_rows 全量结果 ─────────────
#   生产实测: _build_chain_rows(db, None, None) = 2794ms, rw_overview 连调两次
#   (窗口 + 全量)合计 5.05ms → 全量那次在热路径里白白吃掉一半耗时。
#   本变量缓存 **仅全量**(start=None, end=None) 的 71944 行匹配结果,
#   带窗口的调用(start/end 非 None)不在此缓存(窗口组合多, 命中率低, 窗口筛选
#   在匹配完成之后做 —— 全量已经算过 D1/D2/D4, 过滤 O(N) 极快)。
#   失效: sync 完成事件 → _invalidate_chain_rows_cache() 主动清
#         时间窗口 30min → 兜底过期(避免 sync 事件漏触导致脏结果停留太久)
#         进程重启 → 自动丢(热重载/部署后自然刷新)
#   进程级单 worker(见 AGENTS.md) —— 无并发问题, 不需要锁。
import time as _time

_CHAIN_ROWS_CACHE_FULL: list[dict[str, Any]] | None = None
_CHAIN_ROWS_CACHE_TS: float = 0.0
_CHAIN_ROWS_CACHE_TTL: float = 1800.0   # 30min 兜底


def _invalidate_chain_rows_cache() -> None:
    """sync 完成后主动失效。由 sync_task_manager 的 on_success 回调触发。"""
    global _CHAIN_ROWS_CACHE_FULL, _CHAIN_ROWS_CACHE_TS
    _CHAIN_ROWS_CACHE_FULL = None
    _CHAIN_ROWS_CACHE_TS = 0.0


def _build_chain_rows(db: Session, start: datetime | None = None, end: datetime | None = None):
    """构建"一笔实际出款"的全链路记录 (只有 A=转入钱包+B=线上直退两条有效路径, 其他无效剔除)。

    筛选时间窗口作用在 **D4 (实际现金出款时刻)**, 而非 D1 发起退款时刻:
      - A 路径 D4 = 用户提现 op_time (钱包真实出款给用户的时刻)
      - B 路径 D4 = 退费单 updated_at_raw (接口回调成功 ≈ 实际到账)

    #344: start=None & end=None(全量) 走进程级 memoization, 避免 rw_overview 等
         多次调用方把同一份 71944 行结果反复算(单次 2.8s)。带窗口的调用(start/end 非 None)
         不走缓存, 每次全量 scan + 内存匹配(已在 O(N) 可接受范围), 然后 D4 窗口过滤。
    """
    global _CHAIN_ROWS_CACHE_FULL, _CHAIN_ROWS_CACHE_TS
    # 命中 memo: 仅全量调用(start=None, end=None)
    if start is None and end is None and _CHAIN_ROWS_CACHE_FULL is not None:
        age = _time.time() - _CHAIN_ROWS_CACHE_TS
        if age < _CHAIN_ROWS_CACHE_TTL:
            return _CHAIN_ROWS_CACHE_FULL
        # 兜底过期: 清掉重算(避免 sync 事件漏触)
        _CHAIN_ROWS_CACHE_FULL = None
    # 1. 取全量数据 (66k rows, 内存可容纳)
    # #334: 读 std_refund_online_all 全量镜像(ods 同构透传, 含被排除行) —— 链路 A 路径是
    #   「转入提现账户」退款, std_refund_online 不含该方式(采纳规则排除), 也不能只含已退款行(链路要全状态)
    from types import SimpleNamespace as _SN
    all_rf = [_SN(**dict(r._mapping)) for r in db.execute(select(_REFUND_ALL)).all()]
    # 2026-08-28 → #332: 只取"成功提现"—— std_wallet_flow 已在表层固化
    #   (失败/冻结/空 op_type 不进表, 原 _WITHDRAW_FAIL_REMARK 关键词机制删除)
    all_wd = list(db.execute(
        select(StdWalletFlow).where(StdWalletFlow.op_type == "用户提现")
    ).scalars().all())
    # 进水: flow_dir='inflow' 即 op_type='订单退费转入' (#332 std 派生列)
    all_wi = list(db.execute(select(StdWalletFlow).where(StdWalletFlow.flow_dir == "inflow")).scalars().all())

    # 所有 datetime 先统一 naive, 避免 tz 比较报错
    for r in all_rf:
        r.created_at_raw = _naive(r.created_at_raw)
        r.created_at = _naive(r.created_at)
        r.updated_at_raw = _naive(r.updated_at_raw)
        r.updated_at = _naive(r.updated_at)
    for w in all_wd + all_wi:
        w.op_time = _naive(w.op_time)

    # 2. 按 refund_no 建 用户提现 索引
    wd_by_rfn: dict[str, list] = {}
    wd_by_sn: dict[str, list] = {}
    for w in all_wd:
        m = _RFN_RE.search(w.remark or "")
        if m:
            wd_by_rfn.setdefault(m.group(1), []).append(w)
        sn = (w.device_sn or "").strip() or ""
        if sn:
            wd_by_sn.setdefault(sn, []).append(w)

    # 3. 订单退费转入 按 (account_id, device_sn, amount) 建桶 (贪心匹配A路径的入钱包时刻)
    from collections import defaultdict as _dd
    wi_bucket: dict[tuple, list] = _dd(list)
    for w in all_wi:
        sn = (w.device_sn or "").strip() or ""
        key = ((w.account_id or ""), sn, round(float(w.op_amount or 0), 2))
        wi_bucket[key].append(w)
    for k in wi_bucket:
        wi_bucket[k].sort(key=lambda x: x.op_time or datetime.min)
    # 已匹配的入钱包记录 id 集合: 不能用对象属性打标记(w._used),
    # 否则同一 Session 内重复调用本函数时, SQLAlchemy identity map 会返回
    # 同一批对象实例, 残留的 _used=True 导致第二次调用 D2 匹配率掉到 0%。
    used_wi_ids: set[int] = set()

    rows: list[dict[str, Any]] = []
    for r in all_rf:
        m_raw = (r.refund_method or "").strip() or "未填写"
        cat = _classify_method(m_raw)
        if cat is None:
            continue  # 线下/未填写/其他 → 非实际现金退款, 从看板统计剔除

        amt = round(float(r.refund_amount or 0), 2)
        d1 = r.created_at_raw or r.created_at
        if not d1:
            continue

        # 分类 A / B
        if cat == "A":
            best_wd = None
            for w in wd_by_rfn.get(r.refund_no, []):
                if (w.op_time or datetime.min) < d1:
                    continue
                if abs(float(w.op_amount or 0) - amt) >= 0.01:
                    continue
                if best_wd is None or w.op_time < best_wd.op_time:
                    best_wd = w
            # 兜底: device_sn + 金额 (用 device_sn 索引代替全量重扫, 避免大 O 兜底拖慢看板)
            if best_wd is None and r.device_sn:
                sn = r.device_sn.strip()
                for w in wd_by_sn.get(sn, []):
                    if abs(float(w.op_amount or 0) - amt) >= 0.01:
                        continue
                    if (w.op_time or datetime.min) < d1:
                        continue
                    if best_wd is None or w.op_time < best_wd.op_time:
                        best_wd = w
            if best_wd is None:
                continue  # A路径没找到用户提现行 → 无真实出款 → 不计入出款看板
            d_out = best_wd.op_time
            # 匹配对应的 订单退费入钱包时刻 (D2)
            d2 = None
            sn_key = (best_wd.device_sn or "").strip() or (r.device_sn or "").strip() or ""
            key = ((best_wd.account_id or ""), sn_key, amt)
            key2 = ((best_wd.account_id or ""), "", amt)
            for cand in (key, key2):
                for w in wi_bucket.get(cand, []):
                    if w.id in used_wi_ids:
                        continue
                    if (w.op_time or datetime.min) < d1:
                        continue
                    if w.op_time > (d_out or datetime.max):
                        continue
                    d2 = w.op_time
                    used_wi_ids.add(w.id)
                    break
                if d2:
                    break
            withdraw_id = best_wd.id
            withdraw_remark = best_wd.remark or ""
            account_id = best_wd.account_id or ""
            device_sn = (best_wd.device_sn or r.device_sn or "").strip() or None
        else:  # cat == "B"
            # 直接线上退款 (线上/微信/支付宝...): 真实到账是"接口回调成功"
            # 由于没有单独的 refunded_at 字段, 用 updated_at_raw 作近似, 但 updated_at_raw 会被后续改单/同步污染
            # → 经验口径: 直接退款的真实回调到账不可能晚于 D1 超过 3 天.
            #   若 updated_at_raw 在 (D1, D1+3天] 以内 → 视为有效回调时间, 采纳;
            #   若 updated_at_raw 超过 D1+3 天 → 判定为后续改单/同步时间戳, 回落到 D1 近似秒级到账;
            #   若 updated_at_raw 早于 D1 → 用 D1.
            MAX_B_DAYS = 3
            d2_upd = r.updated_at_raw or r.updated_at or d1
            if d2_upd <= d1 + timedelta(days=MAX_B_DAYS):
                d_out = d2_upd if d2_upd >= d1 else d1
            else:
                d_out = d1
            d2 = None
            withdraw_id = None
            withdraw_remark = ""
            account_id = ""
            device_sn = (r.device_sn or "").strip() or None

        # 时间筛选: **D4 实际出款时间** 落在筛选窗口 (不再是 D1 发起时间)
        if start and d_out < start:
            continue
        if end and d_out > end:
            continue

        # 三个时长(天)
        def _days(a, b):
            if not a or not b:
                return None
            return round((a - b).total_seconds() / 86400.0, 4)

        rows.append({
            "path": cat,
            "refund_no": r.refund_no,
            "platform_order_no": r.platform_order_no or "",
            "device_sn": device_sn,
            "account_id": account_id,
            "refund_method": m_raw,
            "refund_reason": (r.refund_reason or "").strip() or "未填写",
            "auditor": (r.auditor or "").strip() or "",
            # 退款人: 以审核人为准(用户口径), 无审核人时回退操作人
            "operator": (r.auditor or "").strip() or (r.operator or "").strip() or "SYS/系统",
            "status": (r.status or "").strip() or "",
            "package_name": (r.package_name or "")[:40],
            "merchant_name": (r.merchant_name or "")[:40],
            "refund_amount": amt,
            "actual_amount": round(float(r.actual_amount or 0), 2),
            "d1_refund_created": d1.isoformat() if d1 else None,
            "d2_wallet_in": d2.isoformat() if d2 else None,
            "d4_actual_cashout": d_out.isoformat() if d_out else None,
            "days_1_4": _days(d_out, d1),
            "days_1_2": _days(d2, d1),
            "days_2_4": _days(d_out, d2),
            "withdraw_id": withdraw_id,
            "withdraw_remark": withdraw_remark,
        })

    # 4. D 路: 手动退款 (std_refund_manual 采纳行)
    #    客服在商户号后台手动操作的退款登记台账, 时效 = 登记时间(register_time) → 实际退款操作时间(op_time)
    #    2026-09-06/09: 排除 dup_residual/ignored → #332 采纳作业已在表层固化
    #    (仅 is_refunded='是' 且非重复/不可回查行进表), 查询不再散落 WHERE
    all_mr = list(db.execute(
        select(StdRefundManual)
    ).scalars().all())
    for mr in all_mr:
        d1 = _naive(mr.register_time)
        d_out = _naive(mr.op_time) or d1
        if not d1 or not d_out:
            continue
        # 时间筛选: 与 A/B 一致, 按实际出款时刻 (op_time) 落在窗口
        if start and d_out < start:
            continue
        if end and d_out > end:
            continue
        m_amt = round(float(mr.refund_amount or 0), 2)
        op = (mr.refund_operator or "").strip() or (mr.registrant or "").strip() or "未填写"
        rows.append({
            "path": "D",
            "refund_no": f"MR{mr.id}",
            "platform_order_no": (mr.order_no or "").strip() or "",
            "device_sn": (mr.device_sn or "").strip() or None,
            "account_id": "",
            "refund_method": "手动退款",
            "refund_reason": "手动商户号操作",
            "operator": op,
            "auditor": "",
            "status": (mr.is_refunded or "").strip() or "",
            "package_name": "",
            "merchant_name": (mr.merchant_id or "").strip() or "",
            "refund_amount": m_amt,
            "actual_amount": m_amt,
            "d1_refund_created": d1.isoformat() if mr.register_time else None,
            "d2_wallet_in": None,
            "d4_actual_cashout": d_out.isoformat() if d_out else None,
            "days_1_4": _days(d_out, d1),
            "days_1_2": None,
            "days_2_4": None,
            "withdraw_id": None,
            "withdraw_remark": "",
        })
    # 回写 memo: 仅全量结果(start=None, end=None)
    if start is None and end is None:
        _CHAIN_ROWS_CACHE_FULL = rows
        _CHAIN_ROWS_CACHE_TS = _time.time()
    return rows

def refund_withdrawal_overview(db: Session, start: str | None = None, end: str | None = None) -> dict[str, Any]:
    """看板顶部 KPI 汇总卡片 + 路径对比 + 到账率分档 (窗口按实际出款D4统计)。

    覆盖三路有时效的退款路径:
      A 钱包提现(转入提现账户→用户提现) / B 直接线上退款 / D 手动退款(商户号后台手动操作)
    """
    s = datetime.fromisoformat(start) if start else None
    e = datetime.fromisoformat(end) if end else None
    rows = _build_chain_rows(db, s, e)

    a = [r for r in rows if r["path"] == "A"]
    b = [r for r in rows if r["path"] == "B"]
    d = [r for r in rows if r["path"] == "D"]
    abd = a + b + d
    ab = a + b

    def _arr(field: str, rs):
        return [r[field] for r in rs if r[field] is not None]

    def _quantile(arr, p):
        if not arr:
            return None
        a_s = sorted(arr)
        return round(a_s[min(len(a_s) - 1, int(len(a_s) * p // 100))], 3)

    def _metrics(rs):
        d = _arr("days_1_4", rs)
        cnt = len(d)
        if not cnt:
            return {}
        total_amt = sum(r["refund_amount"] for r in rs)
        return {
            "count": cnt,
            "amount": round(total_amt, 2),
            "avg_days": round(sum(d) / cnt, 3),
            "median_days": _quantile(d, 50),
            "p75_days": _quantile(d, 75),
            "p95_days": _quantile(d, 95),
            "max_days": _quantile(d, 100),
            "pct_within_1d": _pct(sum(1 for v in d if v <= 1), cnt),
            "pct_within_3d": _pct(sum(1 for v in d if v <= 3), cnt),
            "pct_within_7d": _pct(sum(1 for v in d if v <= 7), cnt),
            "pct_over_15d": _pct(sum(1 for v in d if v > 15), cnt),
        }

    # A路径未提现率 (全局口径, 不随D4窗口变化: 统计全量转入提现账户工单 vs 已匹配提现)
    # #334: 读 std_refund_online_all 全量镜像(ods 同构透传, 含被排除行) —— 按单台账口径
    #   (转入提现账户行被 std A 路采纳规则排除, std 采纳集里查不到);
    #   蓄水池/水位的纯 B 核算见决策概览 wallet_water, 与本指标无关
    rf_all = list(db.execute(
        select(_REFUND_ALL.c.refund_method, func.count(), func.sum(_REFUND_ALL.c.refund_amount))
        .group_by(_REFUND_ALL.c.refund_method)
    ).all())
    a_total_cnt = 0; a_total_amt = 0.0
    for m, c, am in rf_all:
        if m and "转入" in m:
            a_total_cnt += (c or 0); a_total_amt += float(am or 0)
    # a_matched_all = 全部历史已匹配提现的 A 路径 (不限筛选窗口)
    all_time_a = [r for r in _build_chain_rows(db, None, None) if r["path"] == "A"]
    a_matched_cnt = len(all_time_a)
    a_sleep_cnt = max(a_total_cnt - a_matched_cnt, 0)
    a_sleep_amt = max(a_total_amt - sum(r["refund_amount"] for r in all_time_a), 0)

    a_metrics = _metrics(a)
    b_metrics = _metrics(b)
    d_metrics = _metrics(d)
    ab_metrics = _metrics(ab)
    abd_metrics = _metrics(abd)
    # A 钱包路径两个子阶段耗时: D1→D2 = 工单到入钱包速度; D2→D4 = 钱躺在钱包里的时长
    a_d12 = [r["days_1_2"] for r in a if r["days_1_2"] is not None]
    a_d24 = [r["days_2_4"] for r in a if r["days_2_4"] is not None]
    if a_metrics:
        a_metrics["avg_days_1_2"] = round(sum(a_d12) / len(a_d12), 3) if a_d12 else 0.0
        a_metrics["median_days_1_2"] = round(sorted(a_d12)[len(a_d12) // 2], 3) if a_d12 else 0.0
        a_metrics["avg_days_2_4"] = round(sum(a_d24) / len(a_d24), 3) if a_d24 else 0.0
        a_metrics["median_days_2_4"] = round(sorted(a_d24)[len(a_d24) // 2], 3) if a_d24 else 0.0
    # 本窗口内实际出款 三路流结构汇总 (用中文命名, 直接供前端顶部醒目KPI卡展示)
    split = {
        "total_count": len(abd),
        "total_amount": round((abd_metrics or {}).get("amount", 0.0) or 0.0, 2),
        # B 流: 本窗口内直接线上退款(当天发起当天退, 或历史发起今天才线上完成)
        "direct_refund": {
            "count": (b_metrics or {}).get("count", 0) or 0,
            "amount": round((b_metrics or {}).get("amount", 0.0) or 0.0, 2),
            "count_ratio": _pct((b_metrics or {}).get("count", 0) or 0, len(abd)),
        },
        # A 流: 之前已经进了钱包, 本窗口内才被用户提现拿走 (相当于"历史沉淀今天才真正出款")
        "wallet_withdrawal": {
            "count": (a_metrics or {}).get("count", 0) or 0,
            "amount": round((a_metrics or {}).get("amount", 0.0) or 0.0, 2),
            "count_ratio": _pct((a_metrics or {}).get("count", 0) or 0, len(abd)),
            # A 流内部两个阶段, 用于解释这个"拖延效应"
            "avg_days_1_2": a_metrics.get("avg_days_1_2", 0.0),
            "avg_days_2_4": a_metrics.get("avg_days_2_4", 0.0),
        },
        # D 流: 客服在商户号后台手动操作的退款登记台账, 时效 = 登记→实际退款操作
        "manual_refund": {
            "count": (d_metrics or {}).get("count", 0) or 0,
            "amount": round((d_metrics or {}).get("amount", 0.0) or 0.0, 2),
            "count_ratio": _pct((d_metrics or {}).get("count", 0) or 0, len(abd)),
            "avg_days": (d_metrics or {}).get("avg_days", 0.0) or 0.0,
            "median_days": (d_metrics or {}).get("median_days") or 0.0,
        },
    }

    return {
        "scope": {"start": start, "end": end, "rows_total": len(rows), "filter_on": "D4_actual_cashout"},
        "cashout_split": split,  # 顶部三路流结构看板 (本窗口实际出款 = 直接线上退款 + 钱包提现 + 手动退款)
        "paths": {
            "A_wallet": a_metrics,
            "B_direct": b_metrics,
            "D_manual": d_metrics,
            "AB_merged": ab_metrics,
            "ABD_merged": abd_metrics,
        },
        "wallet_sleep": {  # 全局沉淀口径, 不随窗口变化 (总览指标)
            "a_total_refund_count": a_total_cnt,
            "a_matched_withdraw_count": a_matched_cnt,
            "still_in_wallet_count": a_sleep_cnt,
            "still_in_wallet_pct": _pct(a_sleep_cnt, a_total_cnt),
            "still_in_wallet_amount": round(a_sleep_amt, 2),
        },
    }

def refund_withdrawal_time_distribution(db: Session, start: str | None = None, end: str | None = None) -> list[dict[str, Any]]:
    """三路路径各自的耗时分桶 (用于 ECharts 堆叠柱状图)。"""
    s = datetime.fromisoformat(start) if start else None
    e = datetime.fromisoformat(end) if end else None
    rows = _build_chain_rows(db, s, e)
    result: list[dict[str, Any]] = []
    for lbl, *_ in _TIME_BINS:
        if lbl == "<0异常":
            continue
        row: dict[str, Any] = {"bin": lbl}
        for path, pname in (("A", "wallet_count",), ("B", "direct_count",), ("D", "manual_count",)):
            rs = [r for r in rows if r["path"] == path and r["days_1_4"] is not None]
            c = sum(1 for r in rs if _bin_label(r["days_1_4"]) == lbl)
            a = round(sum(r["refund_amount"] for r in rs if _bin_label(r["days_1_4"]) == lbl), 2)
            row[pname] = c
            row[pname.replace("count", "amount")] = a
            row[pname.replace("count", "ratio")] = _pct(c, len(rs))
        result.append(row)
    return result

def refund_withdrawal_dimensions(db: Session, start: str | None = None, end: str | None = None, topn: int = 20) -> dict[str, Any]:
    """聚合维度: 按处理人 / 退款原因 / 退款方式, 返回 Top N (三路 A/B/D)。"""
    s = datetime.fromisoformat(start) if start else None
    e = datetime.fromisoformat(end) if end else None
    rows = [r for r in _build_chain_rows(db, s, e) if r["path"] in ("A", "B", "D")]

    def _group(key):
        g: dict[str, list] = {}
        for r in rows:
            k = str(r.get(key) or "未填写") or "未填写"
            if key == "operator" and ("SYS" in k or "系统" in k or "未填" in k):
                k = "SYS/系统自动"
            g.setdefault(k, []).append(r)
        out = []
        for k, rs in g.items():
            d = [r["days_1_4"] for r in rs if r["days_1_4"] is not None]
            if not d:
                continue
            n = len(d); tot = len(rows)
            out.append({
                "name": k, "count": n, "count_ratio": _pct(n, tot),
                "amount": round(sum(r["refund_amount"] for r in rs), 2),
                "avg_amount": round(sum(r["refund_amount"] for r in rs) / n, 2),
                "median_days": round(sorted(d)[n // 2], 3),
                "avg_days": round(sum(d) / n, 3),
                "p95_days": round(sorted(d)[min(n - 1, n * 95 // 100)], 3),
                "max_days": round(max(d), 3),
            })
        out.sort(key=lambda x: -x["count"])
        return out[:topn]

    return {
        "by_operator": _group("operator"),
        "by_reason": _group("refund_reason"),
        "by_method": _group("refund_method"),
    }

def refund_withdrawal_facets(db: Session, start: str | None = None, end: str | None = None) -> dict[str, list[str]]:
    """退款明细清单的筛选候选项(操作人/退款原因), 用于前端下拉筛选"""
    s = datetime.fromisoformat(start) if start else None
    e = datetime.fromisoformat(end) if end else None
    rows = _build_chain_rows(db, s, e)
    rows = [r for r in rows if r["path"] in ("A", "B", "D") and r["days_1_4"] is not None]
    alias_map = _chain_alias_map(db)
    ops: set[str] = set()
    reasons: set[str] = set()
    for r in rows:
        op = _norm_chain_operator(r.get("operator"), alias_map)
        # 清洗被同步时间戳/占位符污染的 operator, 下拉只保留真实人员名
        if op and not _is_meta_junk_operator(op):
            ops.add(op)
        rsn = (r.get("refund_reason") or "").strip() or ""
        if rsn:
            reasons.add(rsn)
    # #273: 追加设备维度候选项(设备类型/套餐/商户号), 与投诉 backlog 同一口径
    return {
        "operators": sorted(ops, key=str.lower),
        "reasons": sorted(reasons, key=str.lower),
        **_device_dim_options(db),
    }

def refund_withdrawal_detail(db: Session, start: str | None = None, end: str | None = None,
                              sort: str = "days_desc", page: int = 1, page_size: int = 50,
                              slow_only: bool = False, path: str = "ALL",
                              comp: str = "all", operator: str = "", reason: str = "",
                              device_type: str = "", combo: str = "", merchant: str = "",
                              active_start: str = "", active_end: str = "",
                              active_age: str = "") -> dict[str, Any]:
    """明细列表: 支持分页、排序、路径筛选(ALL/ABD/AB/A/B/D)、慢单筛选、涉诉/操作人/退款原因筛选。

    #273: 追加设备维度筛选(设备类型/套餐/商户号/激活时间段), 口径走 devices 表
    (与分品类运营看板 #271 同源), SN 未建档的行在任一设备维度条件生效时被排除。
    #295: active_age 激活时长桶筛选(le15/16-30/31-180/181-365/gt365/none) —— 汇总图
    「激活时长」桶的下钻落点(相对天数无法用 active_start/end 绝对日期表达);
    锚点 = D1 退款创建日 − bms_orders 最早激活日(#251 同源); 无激活记录的行仅在
    active_age='none' 时保留。
    """
    s = datetime.fromisoformat(start) if start else None
    e = datetime.fromisoformat(end) if end else None
    rows = _build_chain_rows(db, s, e)
    if path in ("A", "B", "D"):
        rows = [r for r in rows if r["path"] == path]
    elif path == "AB":
        rows = [r for r in rows if r["path"] in ("A", "B")]
    elif path == "ABD":
        rows = [r for r in rows if r["path"] in ("A", "B", "D")]
    rows = [r for r in rows if r["days_1_4"] is not None]
    if slow_only:
        rows = [r for r in rows if (r["days_1_4"] or 0) > 3]
    # 操作人归一(全量, 供筛选与展示口径一致)
    alias_map = _chain_alias_map(db)
    for r in rows:
        r["operator"] = _norm_chain_operator(r.get("operator"), alias_map)
    # 涉诉分类(须在筛选与分页前全量计算, 以保证 total 正确)
    rows = _annotate_chain_litigation(db, rows)
    # 筛选
    rows = [r for r in rows
            if _chain_complaint_filter(r["complaint_type"], comp)
            and (not operator or (r.get("operator") or "").lower() == operator.lower())
            and (not reason or (reason in (r.get("refund_reason") or "")))]
    # #273: 设备维度筛选(命中 SN 集合为 None 表示未启用, 跳过)
    dim_sns = _device_dim_matched_sns(db, device_type or None, combo or None, merchant or None,
                                      active_start or None, active_end or None)
    if dim_sns is not None:
        rows = [r for r in rows if r.get("device_sn") in dim_sns]
    # #291: 激活后天数桶筛选(汇总图「激活时间段」桶下钻)
    if active_age:
        want = _AGE_PARAM_TO_BUCKET.get(active_age)
        if want:
            attrs = _device_dim_attrs(db, {r.get("device_sn") for r in rows if r.get("device_sn")})
            def _age_ok(r: dict) -> bool:
                a = attrs.get(r.get("device_sn"))
                d1 = r.get("d1_refund_created")
                if not d1:
                    return want == "(无激活记录)" and a is None
                if a is None or a.get("active_date") is None:
                    return want == "(无激活记录)"
                try:
                    ev_dt = datetime.fromisoformat(d1)
                    if ev_dt.tzinfo is None:
                        # 库里 naive 时间 = 北京时间(项目统一口径, 同 _core._to_utc 注释),
                        # 不能裸 astimezone —— 那会按系统本地时区解释, UTC 容器下桶判定偏移
                        ev_dt = ev_dt.replace(tzinfo=TZ)
                    ev = ev_dt.astimezone(TZ).date()
                except (ValueError, TypeError):
                    return False
                return _active_age_bucket(_age_days_between(ev, a["active_date"])) == want
            rows = [r for r in rows if _age_ok(r)]
    # 排序
    sort = sort or "days_desc"
    key_fn_map = {
        "days_desc": lambda r: -(r["days_1_4"] or 0),
        "days_asc": lambda r: (r["days_1_4"] or 0),
        "amount_desc": lambda r: -(r["refund_amount"] or 0),
        "d1_desc": lambda r: (r["d1_refund_created"] or ""),
    }
    rows.sort(key=key_fn_map.get(sort, key_fn_map["days_desc"]), reverse=("d1" in sort))
    total = len(rows)
    # 风险概览(基于当前筛选后的整批行, 供明细头部展示)
    risk_summary = {
        "high": sum(1 for r in rows if r.get("risk_level") == "高风险"),
        "medium": sum(1 for r in rows if r.get("risk_level") == "中风险"),
        "low": sum(1 for r in rows if r.get("risk_level") != "高风险" and r.get("risk_level") != "中风险"),
    }
    page = max(1, page or 1); ps = max(1, min(500, page_size or 50))
    sl = slice((page - 1) * ps, page * ps)
    return {"total": total, "page": page, "page_size": ps, "risk_summary": risk_summary, "list": rows[sl]}

def refund_withdrawal_daily_split(db: Session, start: str | None = None, end: str | None = None) -> list[dict[str, Any]]:
    """按**实际出款日 D4** 逐天拆分三条流: 直接线上退款出款 / 钱包沉淀后提现出款 / 手动退款出款.

    用于 ECharts 按日堆叠柱状图: 让用户一眼看到"每天这 N 笔实际退款里, 多少是今天直退的, 多少是之前进了钱包今天才被提现的, 多少是手动商户号操作的".
    返回数组按 date 升序, 每天一行:
      date, direct_count/amount (B直退), wallet_count/amount (A钱包提现), manual_count/amount (D手动退款), total_count/amount
      同时 A 流附带 avg_days_1_2 (工单→入钱包) / avg_days_2_4 (入钱包→用户提现拖延) 两个子阶段日均值,
      D 流附带 manual_avg_days (登记→实际退款时效日均值).
    """
    s = datetime.fromisoformat(start) if start else None
    e = datetime.fromisoformat(end) if end else None
    rows = _build_chain_rows(db, s, e)
    # 按 D4 出款日 (YYYY-MM-DD) 分组
    from collections import defaultdict as _dd
    g: dict[str, dict[str, Any]] = _dd(dict)
    for r in rows:
        d4 = r["d4_actual_cashout"]
        if not d4:
            continue
        day = d4[:10]
        if day not in g:
            g[day] = {
                "date": day,
                "direct_count": 0, "direct_amount": 0.0,
                "wallet_count": 0, "wallet_amount": 0.0,
                "manual_count": 0, "manual_amount": 0.0,
                "a_d12_sum": 0.0, "a_d12_n": 0,
                "a_d24_sum": 0.0, "a_d24_n": 0,
                "d14_sum": 0.0, "d14_n": 0,
            }
        bucket = g[day]
        amt = r["refund_amount"] or 0.0
        if r["path"] == "B":
            bucket["direct_count"] += 1
            bucket["direct_amount"] += amt
        elif r["path"] == "A":
            bucket["wallet_count"] += 1
            bucket["wallet_amount"] += amt
            if r.get("days_1_2") is not None:
                bucket["a_d12_sum"] += r["days_1_2"]
                bucket["a_d12_n"] += 1
            if r.get("days_2_4") is not None:
                bucket["a_d24_sum"] += r["days_2_4"]
                bucket["a_d24_n"] += 1
        elif r["path"] == "D":
            bucket["manual_count"] += 1
            bucket["manual_amount"] += amt
            if r.get("days_1_4") is not None:
                bucket["d14_sum"] += r["days_1_4"]
                bucket["d14_n"] += 1
    out = []
    for day in sorted(g.keys()):
        b = g[day]
        tc = b["direct_count"] + b["wallet_count"] + b["manual_count"]
        ta = round(b["direct_amount"] + b["wallet_amount"] + b["manual_amount"], 2)
        out.append({
            "date": day,
            "direct_count": b["direct_count"],
            "direct_amount": round(b["direct_amount"], 2),
            "wallet_count": b["wallet_count"],
            "wallet_amount": round(b["wallet_amount"], 2),
            "manual_count": b["manual_count"],
            "manual_amount": round(b["manual_amount"], 2),
            "total_count": tc,
            "total_amount": ta,
            # A 流两个子阶段日均值 (帮助定位到底是入钱包慢还是用户提现慢)
            "wallet_avg_days_1_2": round(b["a_d12_sum"] / b["a_d12_n"], 3) if b["a_d12_n"] else None,
            "wallet_avg_days_2_4": round(b["a_d24_sum"] / b["a_d24_n"], 3) if b["a_d24_n"] else None,
            # D 流时效日均值 (登记→实际退款)
            "manual_avg_days": round(b["d14_sum"] / b["d14_n"], 3) if b["d14_n"] else None,
        })
    return out

def refund_withdrawal_sleeping(
    db: Session, start: str | None = None, end: str | None = None,
    page: int = 1, page_size: int = 50,
) -> dict[str, Any]:
    """钱包沉淀清单: 转入提现账户但尚未被用户提现的退款单 (分页).

    匹配口径与 _build_chain_rows 的 A 路径一致:
      按退款单号(RF)命中用户提现 remark → 已提现; 兜底 device_sn+金额 且提现时间>=发起时间 → 已提现.
      未命中任何用户提现 → 沉淀(钱仍躺在提现钱包).
    """
    s = datetime.fromisoformat(start) if start else None
    e = datetime.fromisoformat(end) if end else None
    # #334: 全状态全方式语义(转入提现账户行被 std A 路采纳规则排除) →
    #   读 std_refund_online_all 全量镜像(ods 同构透传, 含被排除行)
    rf_rows = db.execute(text("""
        SELECT refund_no, device_sn, refund_amount, created_at_raw, operator
        FROM std_refund_online_all
        WHERE refund_method LIKE '%转入提现%'
          AND created_at_raw >= :ts AND created_at_raw <= :te
        ORDER BY created_at_raw
    """), {"ts": s, "te": e}).fetchall()
    # #332: std_wallet_flow 已固化成功提现(失败/冻结/空 op_type 不进表), 备注排除删除
    wd_rows = db.execute(text("""
        SELECT remark, device_sn, op_amount, op_time FROM std_wallet_flow w
        WHERE w.op_type = '用户提现'
    """)).fetchall()

    wd_by_rfn: dict[str, list] = {}
    wd_by_sn: dict[str, list] = {}
    for w in wd_rows:
        m = _RFN_RE.search(w.remark or "")
        if m:
            wd_by_rfn.setdefault(m.group(1), []).append(w)
        sn = (w.device_sn or "").strip()
        if sn:
            wd_by_sn.setdefault(sn, []).append(w)

    sleep_rows: list[dict[str, Any]] = []
    for r in rf_rows:
        amt = round(float(r.refund_amount or 0), 2)
        d1 = _naive(r.created_at_raw)
        if not d1:
            d1 = datetime.min
        matched = False
        for w in wd_by_rfn.get(r.refund_no, []):
            wo = _naive(w.op_time)
            if wo and wo >= d1 and abs(float(w.op_amount or 0) - amt) < 0.01:
                matched = True
                break
        if not matched and r.device_sn:
            sn = str(r.device_sn).strip()
            for w in wd_by_sn.get(sn, []):
                wo = _naive(w.op_time)
                if wo and wo >= d1 and abs(float(w.op_amount or 0) - amt) < 0.01:
                    matched = True
                    break
        if matched:
            continue
        sleep_rows.append({
            "refund_no": r.refund_no,
            "device_sn": (r.device_sn or "").strip() or None,
            "refund_amount": amt,
            "operator": (r.operator or "").strip() or "SYS/系统",
            "d1_refund_created": d1.astimezone(TZ).isoformat() if r.created_at_raw else None,
            "waiting_days": round((datetime.now() - d1).total_seconds() / 86400.0, 1) if r.created_at_raw else None,
        })

    total = len(sleep_rows)
    page = max(1, page or 1); ps = max(1, min(500, page_size or 50))
    return {"total": total, "page": page, "page_size": ps, "list": sleep_rows[(page - 1) * ps: page * ps]}
