"""对账回填服务 - 退款记录报表(E 路线上水单) 与经营四路 A/B/C/D 勾稽 + D 路时间回填

依据: A-20260905-002 LOCKED (#17 P2)。核心不变式: **叠加不推翻 v4 LOCKED 口径**,
不修改现有核算函数(_cash_routes_total 等)。

功能:
1. backfill_d_op_time(db): D 路(manual_refunds.is_refunded='是')缺 op_time/倒挂行,
   用 refund_record_reports 的精确退款时间按可信匹配回填(带 auto_filled 质量标记)。
2. water_statement(db, start, end, status): E 路水单(refund_record_reports)逐笔勾稽
   匹配 A(线上直退)/B(钱包)/C(闪猫)/D(手动) → matched/未登记, 输出对账差异下钻。

D 路回填匹配优先级(LOCKED A-20260905-002 §3.3):
  L1: manual.order_no == report.platform_order_no
  L2: manual.device_sn == report.device_sn AND |refund_amount| ≤ 0.01
  L3: manual.device_sn == report.device_sn AND report.refund_time ∈ [register_time-7d, register_time+7d]
  唯一候选才回填; 多候选 → quality_status=pending 人工复核。
"""

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import and_, func, or_, select, text

from app.models.manual_refund import ManualRefund
from app.models.refund_record_report import RefundRecordReport
from app.services.analytics._core import D

logger = logging.getLogger(__name__)

_TZ_CN = timezone(timedelta(hours=8))

# 退款记录报表里"退款成功"才算真扣款水单(勾稽/回填只认成功)
_SUCCESS_STATUSES = ("退款成功", "成功", "SUCCESS")


def _is_success_status(s: str | None) -> bool:
    return bool(s and s.strip() in _SUCCESS_STATUSES)


def _amount_eq(a, b, tol=0.01) -> bool:
    """金额近似相等(元, 容忍分位误差)"""
    if a is None or b is None:
        return False
    try:
        return abs(D(a) - D(b)) <= tol
    except (TypeError, ValueError):
        return False


def _dt_or_none(v):
    if v is None:
        return None
    return v


def _to_utc_aware(dt: datetime) -> datetime:
    """把时间窗参数统一成 UTC aware, 消除 naive 歧义(2026-09-05 修复)。

    约定: API 参数按"北京时间"传入(A-20260905-002 §五 description)。
    - naive datetime  → 视为北京时间 → 转 UTC aware
    - aware (+08:00)  → 视为北京时间 → 转 UTC aware
    - aware (UTC)     → 原样保留
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_TZ_CN).astimezone(timezone.utc)
    return dt.astimezone(timezone.utc)


def _to_cn(dt: datetime) -> datetime:
    """UTC aware → 北京时间(用于序列化返回给前端, 与参数口径对称)"""
    if dt.tzinfo is None:
        # 若仍是 naive(异常), 按 UTC 解释转北京, 不抛错
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_TZ_CN)


def _is_low_precision_op_time(op: datetime | None) -> bool:
    """op_time 是否为"日级低精度默认"(北京时间 15:00:00 整点)。

    _ensure_afternoon_time(upsert.py): 台账原始只有日期(00:00:00)时统一置为
    15:00:00 —— 该值是"当天近似"而非真实退款时刻。E 路唯一匹配上时可用
    refund_time(秒级精确) 提升精度。真实人工填的 15:00 也覆盖(有溯源可回滚)。
    """
    if op is None:
        return False
    try:
        cn = op.astimezone(_TZ_CN)
        return cn.hour == 15 and cn.minute == 0 and cn.second == 0
    except (ValueError, OSError):
        return False


# ─────────────────────────────────────────────────────────────
# 1. D 路 op_time 回填
# ─────────────────────────────────────────────────────────────
def _is_residual_no_sn_row(m: ManualRefund) -> bool:
    """疑似历史残留脏行: 无设备号 且 登记年份明显异常(非近两年真实经营期)。

    生产实证(#21, 2026-09-06): manual_refunds 存在大量"无SN + register_time
    乱年份(2001~2005/2031) + remark=余额已清除/押金"的占位残留行, 与同 order_no
    的有SN真实行成对。若给这类行自动回填 op_time, 会被 COALESCE(op_time,
    register_time) 拉进近期统计窗口 → D 路重复计入经营。
    护栏: 有SN → 绝不判残留(真实历史台账不受影响); 无SN且登记年份不在
    2025~2026 → 不进自动回填(转 pending 人工复核)。
    """
    if m.device_sn:
        return False
    if m.register_time is None:
        # 无SN且无登记时间 → 无佐证, 保守不进自动回填
        return True
    try:
        yr = m.register_time.astimezone(_TZ_CN).year
    except (ValueError, OSError):
        return True
    return yr < 2025 or yr > 2026


def backfill_d_op_time(db, limit: int = 5000) -> dict:
    """D 路(manual_refunds) op_time 缺失/倒挂/低精度 → 用退款记录报表退款时间回填。

    覆盖三类(均 is_refunded='是'):
      1. op_time IS NULL                     → 缺失, 直接回填
      2. op_time 早于 register_time >1天     → 倒挂, 视为异常重填
      3. op_time 为低精度默认(北京15:00:00)  → 台账"只记日期"的近似(_ensure_afternoon_time),
                                              若 E 路唯一匹配(L1订单号/L2设备+金额), 用
                                              refund_time 秒级精确值提升精度(2026-09-05, #20)
    匹配优先级: L1 订单号 == report.platform_order_no; L2 设备号+金额(±0.01);
               L3 设备号+登记前后7天唯一候选(仅缺/倒挂场景用, 低精度提升不用 L3 防误改)
    护栏(A-20260906-001 §3.1, #21):
      - 疑似残留脏行(无SN+乱年份) 不自动回填 → 标 pending 人工
      - 同一 report(backfill_manual_refund_id 已置位) 不再回填其它 manual 行
    幂等: 已 auto_filled 且 op_time 非空 → 跳过(重跑不覆盖已精确回填)。
    溯源: quality_status=auto_filled + note(留原值) + backfill_refund_record_report_id。
    """
    stats = {
        "scanned": 0, "matched": 0, "backfilled": 0, "precision_upgraded": 0,
        "pending": 0, "skipped_no_candidate": 0, "residual_skipped": 0,
        "report_occupied_skipped": 0,
    }
    # 低精度默认判断: op_time(UTC) 换算北京后为 15:00:00 → 台账"只记日期"的近似
    #   UTC 07:00 == 北京 15:00; 用 extract(timezone('Asia/Shanghai', op_time)) 精确表达
    op_cn = func.timezone("Asia/Shanghai", ManualRefund.op_time)
    low_prec_cond = and_(
        ManualRefund.op_time.isnot(None),
        func.extract("hour", op_cn) == 15,
        func.extract("minute", op_cn) == 0,
        func.extract("second", op_cn) == 0,
    )
    # 候选 manual 行: 已退款 且 op_time 缺失 / 倒挂 / 低精度默认
    candidates = list(
        db.scalars(
            select(ManualRefund)
            .where(
                ManualRefund.is_refunded == "是",
                or_(
                    ManualRefund.op_time.is_(None),
                    # 倒挂: op_time 比 register_time 早超过 1 天(视为异常,可重填)
                    ManualRefund.op_time < ManualRefund.register_time - timedelta(days=1),
                    low_prec_cond,
                ),
            )
            .order_by(ManualRefund.id)
            .limit(limit)
        )
    )
    stats["scanned"] = len(candidates)

    def _match_report(m: ManualRefund, allow_l3: bool) -> list[RefundRecordReport]:
        """L1/L2(/L3) 匹配, 返回唯一候选(多候选返回空由调用方判 pending)"""
        # L1: 订单号精确匹配(manual.order_no ↔ report.platform_order_no)
        matched: list[RefundRecordReport] = []
        if m.order_no:
            matched = list(
                db.scalars(
                    select(RefundRecordReport).where(
                        RefundRecordReport.platform_order_no == m.order_no.strip(),
                        RefundRecordReport.refund_status.in_(_SUCCESS_STATUSES),
                    )
                )
            )
        # L2: 设备号 + 金额
        if not matched and m.device_sn:
            cands = list(
                db.scalars(
                    select(RefundRecordReport).where(
                        RefundRecordReport.device_sn == m.device_sn,
                        RefundRecordReport.refund_status.in_(_SUCCESS_STATUSES),
                    )
                )
            )
            matched = [r for r in cands if _amount_eq(r.refund_amount, m.refund_amount)]
        # L3: 设备号 + 时间窗(登记前后7天, 唯一候选) —— 仅缺/倒挂场景
        if not matched and allow_l3 and m.device_sn and m.register_time:
            reg = m.register_time
            window_start = reg - timedelta(days=7)
            window_end = reg + timedelta(days=7)
            cands = list(
                db.scalars(
                    select(RefundRecordReport).where(
                        RefundRecordReport.device_sn == m.device_sn,
                        RefundRecordReport.refund_time >= window_start,
                        RefundRecordReport.refund_time <= window_end,
                        RefundRecordReport.refund_status.in_(_SUCCESS_STATUSES),
                    )
                )
            )
            # 时间窗候选里只取唯一(避免多月重取产生重复)
            matched = cands if len(cands) == 1 else []
        return matched

    for m in candidates:
        # 已回填过(auto_filled)且 op_time 非空 → 跳过(幂等)
        if m.quality_status == "auto_filled" and m.op_time is not None:
            stats["matched"] += 1
            continue

        # 护栏1(A-20260906-001 §3.1, #21): 疑似残留脏行(无SN+乱年份) 不自动回填
        if _is_residual_no_sn_row(m):
            if m.quality_status != "pending":
                m.quality_status = "pending"
                m.quality_note = (
                    "疑似历史残留脏行(无设备号且登记年份异常), 不自动回填 op_time,"
                    "需人工复核确认是否真实退款"
                )
            stats["residual_skipped"] += 1
            continue

        # 归入场景: 缺/倒挂(可走 L3) vs 低精度提升(只走 L1/L2 防误改)
        is_missing = m.op_time is None
        is_reversed = (m.op_time is not None and m.register_time is not None
                       and m.op_time < m.register_time - timedelta(days=1))
        is_low_prec = (not is_missing and not is_reversed
                       and _is_low_precision_op_time(m.op_time))
        if not (is_missing or is_reversed or is_low_prec):
            # 非三类候选(例如 limit 放宽捞出的正常精确时间行) → 跳过不计 skipped
            continue

        matched_reports = _match_report(m, allow_l3=(is_missing or is_reversed))

        if not matched_reports:
            stats["skipped_no_candidate"] += 1
            continue
        if len(matched_reports) > 1:
            # 多候选 → pending 人工复核
            m.quality_status = "pending"
            m.quality_note = (
                f"对账回填多候选(device_sn={m.device_sn}, 金额={m.refund_amount}),"
                "需人工确认退款时间"
            )
            stats["pending"] += 1
            continue

        report = matched_reports[0]
        # 护栏2(A-20260906-001 §3.1, #21): 同一 report 已回填过其它 manual 行 →
        # 说明该 report 已被占用, 当前行不得重复回填(避免一对多造成 D 路重复)。
        if report.backfill_manual_refund_id:
            m.quality_status = "pending"
            m.quality_note = (
                f"对账回填冲突: 退款记录报表 id={report.id} 已回填到"
                f" manual id={report.backfill_manual_refund_id}, 本行(order_no="
                f"{m.order_no})不再自动回填, 需人工确认是否真实退款"
            )
            stats["report_occupied_skipped"] += 1
            continue
        # 唯一命中 → 回填
        old_op = m.op_time
        m.op_time = report.refund_time
        m.quality_status = "auto_filled"
        # ⚠️ 2026-09-05 (#20): 结构化溯源列(替代/补充解析 quality_note 文本)
        m.backfill_refund_record_report_id = str(report.id)
        if is_low_prec:
            m.quality_note = (
                f"op_time 为低精度默认(15:00)被 E 路精确回填, 来源 refund_record_reports.id={report.id}"
                + (f", 原 op_time={_to_cn(old_op).isoformat()}" if old_op else "")
            )
            stats["precision_upgraded"] += 1
        else:
            m.quality_note = (
                f"由退款记录报表 refund_time 回填, 来源 refund_record_reports.id={report.id}"
                + (f", 原 op_time={old_op}" if old_op else "")
            )
        # 反向溯源(幂等: 同 report 只标一次)
        if not report.backfill_manual_refund_id:
            report.backfill_manual_refund_id = str(m.id)
        stats["backfilled"] += 1
        stats["matched"] += 1

    db.commit()
    return stats


# ─────────────────────────────────────────────────────────────
# 2. E 路水单逐笔勾稽
# ─────────────────────────────────────────────────────────────
def _build_route_maps(db, start, end):
    """预取各经营路匹配键, 加速勾稽。

    A 路(线上直退 refunds): platform_order_no / refund_no → id
    B 路(钱包 wallet 用户提现): device_sn+金额 → 备注含退费单号(弱) — 用 device_sn+op_amount 集合
    C 路(闪猫 shanmao_refunds): refund_no/device_sn
    D 路(手动 manual_refunds): order_no/device_sn+金额
    """
    # #334: 读 std_*_all 全量镜像(ods 同构透传), 业务代码零读 ods
    # A: 平台订单号 → refund_no(退费单)
    a_by_order: dict[str, set[str]] = {}
    rows = db.execute(text(
        "SELECT platform_order_no, refund_no FROM std_refund_online_all WHERE platform_order_no IS NOT NULL"
    )).all()
    for order_no, refund_no in rows:
        if order_no:
            a_by_order.setdefault(str(order_no).strip(), set()).add(str(refund_no))
    # A2: refund_no 本身(可能 report.platform_order_no 直接是 RF 退费单号)
    a_by_no: set[str] = set()
    for (rno,) in db.execute(text("SELECT refund_no FROM std_refund_online_all")).all():
        if rno:
            a_by_no.add(str(rno).strip())

    # C: shanmao 订单号集合(WXALR 前缀, 闪猫独占) + 设备号集合(兜底)
    # ⚠️ 2026-09-09 (M-4, A-20260909-001 §2.8.3): C 路 device_sn 覆盖 0%,
    #    原 c_sns 匹配实际全漏配 → 改用 order_no(WXALR)为主锚点。
    c_order_nos: set[str] = set()
    for (ono,) in db.execute(text(
        "SELECT order_no FROM std_refund_shanmao_all WHERE order_no IS NOT NULL AND status = '已退款'"
    )).all():
        if ono:
            c_order_nos.add(str(ono).strip())
    c_sns: set[str] = set()
    for (sn,) in db.execute(text(
        "SELECT device_sn FROM std_refund_shanmao_all WHERE device_sn IS NOT NULL"
    )).all():
        if sn:
            c_sns.add(str(sn).strip())

    # B: wallet 用户提现 设备+金额集合
    b_keys: set[tuple[str, Decimal]] = set()
    for sn, amt in db.execute(text(
        "SELECT device_sn, op_amount FROM std_wallet_flow_all "
        "WHERE op_type = '用户提现' AND device_sn IS NOT NULL AND op_amount IS NOT NULL"
    )).all():
        if sn and amt is not None:
            b_keys.add((str(sn).strip(), D(amt)))

    # D: manual 已退款 设备+金额 + order_no 集合
    d_keys: set[tuple[str, Decimal]] = set()
    d_order_nos: set[str] = set()
    for sn, amt, ono in db.execute(text(
        "SELECT device_sn, refund_amount, order_no FROM std_refund_manual_all "
        "WHERE is_refunded = '是' AND refund_amount IS NOT NULL"
    )).all():
        if sn and amt is not None:
            d_keys.add((str(sn).strip(), D(amt)))
        if ono:
            d_order_nos.add(str(ono).strip())

    return {
        "a_by_order": a_by_order,
        "a_by_no": a_by_no,
        "c_order_nos": c_order_nos,
        "c_sns": c_sns,
        "b_keys": b_keys,
        "d_keys": d_keys,
        "d_order_nos": d_order_nos,
    }


def _classify_report(report: RefundRecordReport, maps: dict) -> str:
    """单笔 report → 命中路由(A/B/C/D)/未登记。"""
    order_no = (report.platform_order_no or "").strip()
    sn = (report.device_sn or "").strip()
    amt = D(report.refund_amount) if report.refund_amount is not None else None

    # A 路: 平台订单号命中退费单 或 直接是退费单号
    if order_no:
        if order_no in maps["a_by_no"] or order_no in maps["a_by_order"]:
            return "A"
        # report 平台订单号是否出现在退费单的 platform_order_no
        if maps["a_by_order"].get(order_no):
            return "A"
    # C 路(闪猫): WXALR 订单号闪猫独占, 与 A/D(WS/WX/WN/WXGV)无前缀重叠 → 无归属歧义
    # ⚠️ 2026-09-09 (M-4, A-20260909-001 §2.8.3): 改用 order_no 匹配(原 SN 匹配因 C 路
    #    device_sn=0% 全漏配); SN 命中作为兜底保留(兼容极少数有 SN 场景)。
    if order_no and order_no in maps["c_order_nos"]:
        return "C"
    # D 路(order_no 命中手动台账)
    if order_no and order_no in maps["d_order_nos"]:
        return "D"
    # A2: report 订单号前缀 WS/WX/WN + 退费单关联(已在 a_by_order 覆盖)
    # B/D 按 设备+金额
    if sn and amt is not None:
        if (sn, amt) in maps["d_keys"]:
            return "D"
        if (sn, amt) in maps["b_keys"]:
            return "B"
        if sn in maps["c_sns"]:
            return "C"
    return "unmatched"


def water_statement(
    db,
    start: datetime,
    end: datetime,
    status: str = "all",
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """E 路水单逐笔勾稽视图(仅 refund_status='退款成功')。

    输出: 总笔数/金额, matched(按 A/B/C/D 分布), 未登记(unmatched), 明细分页。
    金额单位: 元(与退款报表一致)。不改变任何现有核算。
    """
    # 时间窗归一化: 参数按北京时间传入 → 统一 UTC aware 过滤 (2026-09-05 修复偏 8h)
    start = _to_utc_aware(start)
    end = _to_utc_aware(end)
    # #334: 读 std_*_all 全量镜像(ods 同构透传), 业务代码零读 ods
    _win_sql = (
        "FROM std_refund_record_all "
        "WHERE refund_status IN ('退款成功','成功','SUCCESS') "
        "AND refund_time >= :s AND refund_time <= :e"
    )
    _win_params = {"s": start, "e": end}
    total = db.scalar(text(f"SELECT count(*) {_win_sql}"), _win_params) or 0
    total_amt = (
        db.scalar(text(f"SELECT COALESCE(SUM(refund_amount), 0) {_win_sql}"), _win_params)
        or 0.0
    )

    maps = _build_route_maps(db, start, end)

    # 逐行分类(水单量通常可控; 如需更大可改 SQL 关联, 一期 Python 逐行)
    rows = list(db.execute(
        text(f"SELECT * {_win_sql} ORDER BY refund_time DESC"), _win_params
    ).all())
    by_route = {"A": 0, "B": 0, "C": 0, "D": 0, "unmatched": 0}
    classified = []
    for r in rows:
        route = _classify_report(r, maps)
        by_route[route] = by_route.get(route, 0) + 1
        classified.append({"report_id": r.id, "route": route, "report": r})

    matched_count = total - by_route["unmatched"]
    matched_amt = sum(
        (D(r.refund_amount) for rc in classified if rc["route"] != "unmatched"
         for r in [rc["report"]]),
        Decimal("0"),
    )
    unmatched_amt = D(total_amt) - matched_amt

    # 分页输出(按 route 过滤)
    if status != "all":
        classified = [c for c in classified if c["route"] == status]
    paged = classified[(page - 1) * page_size: page * page_size]

    def _ser(c):
        r = c["report"]
        return {
            "id": r.id,
            "platform_order_no": r.platform_order_no,
            "merchant_id": r.merchant_id,
            "merchant_name": r.merchant_name,
            "device_sn": r.device_sn,
            "product_name": r.product_name,
            "refund_time": _to_cn(r.refund_time).isoformat() if r.refund_time else None,
            "refund_amount": float(r.refund_amount) if r.refund_amount is not None else None,
            "route": c["route"],
        }

    return {
        "date_start": _to_cn(start).isoformat(),
        "date_end": _to_cn(end).isoformat(),
        "total_report_count": total,
        "total_report_amount": float(total_amt),
        "matched_count": matched_count,
        "matched_amount": round(matched_amt, 2),
        "unmatched_count": by_route["unmatched"],
        "unmatched_amount": round(unmatched_amt, 2),
        "by_route": by_route,
        "page": page,
        "page_size": page_size,
        "filtered_total": len(classified),
        "items": [_ser(c) for c in paged],
    }
