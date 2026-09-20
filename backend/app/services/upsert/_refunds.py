"""退款域 upsert: refunds / wallet / merchant_refunds / shanmao_refunds / manual_refunds + 质检。"""

import logging
import types
from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.manual_refund import ManualRefund
from app.models.merchant_refund import MerchantRefund
from app.models.refund import Refund
from app.models.shanmao_refund import ShanmaoRefund
from app.models.wallet import WalletWithdrawal
from app.services.quality_check import (
    check_refund_row,
    check_shanmao_row,
    check_wallet_row,
)

logger = logging.getLogger(__name__)

# 跨子模块引用(拆分自 upsert.py, 原同文件内直接调用)
from app.services.upsert._core import (
    ImportResult,
    _ensure_afternoon_time,
    _execute_upsert,
    _freeze_ok_manual_refund_rows,
    _is_manual_refund_name_polluted,
    _looks_like_bad_refund_no,
    _manual_dedup_key,
    _op_str,
    _sanitize_date,
    _sanitize_name_field,
    _sync_devices,
    _to_datetime,
    _to_float,
    apply_mapping,
)


# ---------- 各数据类型 upsert ----------
def upsert_refunds(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    rows: list[dict] = []
    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            refund_no = (data.get("refund_no") or "").strip() if data.get("refund_no") else None
            if not refund_no:
                result.add_error(idx, "退费单号为空")
                continue
            if _looks_like_bad_refund_no(refund_no):
                result.add_error(
                    idx,
                    f"退费单号 '{refund_no}' 格式异常(含中文/标点/空白或过长,疑似误填为备注文本),请核对",
                    {"refund_no": refund_no},
                )
                continue
            # operator / auditor 必须像人名(不能是数字,否则是金额列映射错位)
            operator = _sanitize_name_field(data.get("operator"), idx, result, "退费单-操作人")
            auditor = _sanitize_name_field(data.get("auditor"), idx, result, "退费单-审核人")
            row_dict = {
                "refund_no": refund_no,
                "platform_order_no": data.get("platform_order_no"),
                "third_order_no": data.get("third_order_no"),
                "device_sn": data.get("device_sn"),
                "refund_reason": data.get("refund_reason"),
                "package_name": data.get("package_name"),
                "merchant_name": data.get("merchant_name"),
                "refund_amount": _to_float(data.get("refund_amount")),
                "actual_amount": _to_float(data.get("actual_amount")),
                "remark": data.get("remark"),
                "status": data.get("status"),
                "refund_method": data.get("refund_method"),
                "audit_remark": data.get("audit_remark"),
                "auditor": auditor,
                "operator": operator,
                "created_at_raw": _to_datetime(data.get("created_at_raw")),
                "updated_at_raw": _to_datetime(data.get("updated_at_raw")),
                "package_price": _to_float(data.get("package_price")),
            }
            # 通用数据质量校验(L2): 时间异常/枚举脏值/金额异常/必填缺失
            row_dict["quality_status"], row_dict["quality_note"] = check_refund_row(
                types.SimpleNamespace(**row_dict)
            )
            rows.append(row_dict)
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}")
    _sync_devices(rows, "device_sn", db)
    _execute_upsert(db, Refund, "refund_no", rows, result)

def upsert_wallet(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    """钱包提现 - 按业务复合唯一键去重(account_id, op_type, op_time, operator, op_amount, device_sn)

    防止重复同步导致数据膨胀;冲突时 DO NOTHING。

    ⚠️  2026-08-11 修复: 增加 op_time 必填校验
        历史问题: 2026-08-06 一次错误导入(工单文件被误当 wallet 导入),
        导致 8068 行只有 device_sn 和 remark(投诉文本) 但 op_time/account_id/op_amount 全 NULL 的
        垃圾数据进入 wallet_withdrawals 表。已清理。
        修复: op_time 为空时直接跳过,记录到错误,防止再次产生垃圾数据。
    """
    rows: list[dict] = []
    skipped_no_op_time = 0
    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            op_time = _to_datetime(data.get("op_time"))
            # ★ op_time 是钱包流水的核心字段,为空说明数据不完整或映射错误(如工单文件被误导入)
            #   直接跳过,避免产生只有 device_sn 没有 op_time 的垃圾数据
            if op_time is None:
                skipped_no_op_time += 1
                result.add_error(
                    idx,
                    "op_time 为空,跳过(钱包流水必须有操作时间,可能是错误映射或数据不完整)",
                    {"device_sn": data.get("device_sn"), "remark": (data.get("remark") or "")[:80]},
                )
                continue
            row_dict = {
                "account_id": data.get("account_id"),
                "wallet_balance": _to_float(data.get("wallet_balance")),
                "device_sn": data.get("device_sn"),
                "op_type": data.get("op_type"),
                "op_amount": _to_float(data.get("op_amount")),
                "remark": data.get("remark"),
                "op_time": op_time,
                "operator": _sanitize_name_field(data.get("operator"), idx, result, "钱包提现-操作人"),
            }
            # 通用数据质量校验(L2)
            row_dict["quality_status"], row_dict["quality_note"] = check_wallet_row(
                types.SimpleNamespace(**row_dict)
            )
            rows.append(row_dict)
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}")
    if skipped_no_op_time > 0:
        logger.warning(
            f"[wallet] 跳过 {skipped_no_op_time} 行 op_time 为空的数据(防止垃圾数据入库)"
        )
    if not rows:
        return

    # 1. 同批次内按业务键去重
    seen: dict[tuple, dict] = {}
    for r in rows:
        key = (
            r.get("account_id"),
            r.get("op_type"),
            r.get("op_time"),
            r.get("operator"),
            r.get("op_amount"),
            r.get("device_sn"),
        )
        seen[key] = r
    unique_rows = list(seen.values())
    dup_in_batch = len(rows) - len(unique_rows)
    if dup_in_batch > 0:
        logger.info(f"[wallet] 同批次内去重: {len(rows)} → {len(unique_rows)} 行 (移除 {dup_in_batch} 条重复)")

    # ⚠️ 2026-09-01 审计建议3: 时间窗口二次校验(仅监控,不阻断)。
    #   BMS 页面时间窗口设置失败时, 拉回的 op_time 会落在"默认近一周/上次范围",
    #   而非期望的 [start,end] 窗口 —— 生产 09-01 14:05 轮: 同步窗口应为 00:00~14:05,
    #   实际拉回却大多落在前一天深夜~凌晨。这里输出实际 op_time 分布,
    #   便于运维对照同步日志里的"设置时间范围"判断窗口是否真的生效。
    #   (op_time 在 upsert_wallet 中已做北京时间→UTC 转换, 这里统一按 UTC 打印)
    _op_times = [r["op_time"] for r in unique_rows if r.get("op_time") is not None]
    if _op_times:
        _op_min, _op_max = min(_op_times), max(_op_times)
        logger.info(
            "[wallet] 实际入库 %d 行 op_time 范围: %s ~ %s",
            len(unique_rows),
            _op_str(_op_min),
            _op_str(_op_max),
        )

    _sync_devices(unique_rows, "device_sn", db)

    # 2. 查询已存在记录数(精确统计)
    existing_count = 0
    if unique_rows:
        conditions = []
        for r in unique_rows:
            cond = and_(
                WalletWithdrawal.account_id == r.get("account_id"),
                WalletWithdrawal.op_type == r.get("op_type"),
                WalletWithdrawal.op_time == r.get("op_time"),
                WalletWithdrawal.operator == r.get("operator"),
                WalletWithdrawal.op_amount == r.get("op_amount"),
            )
            if r.get("device_sn") is not None:
                cond = and_(cond, WalletWithdrawal.device_sn == r.get("device_sn"))
            else:
                cond = and_(cond, WalletWithdrawal.device_sn.is_(None))
            conditions.append(cond)
        existing_count = db.scalar(select(func.count()).select_from(WalletWithdrawal).where(or_(*conditions))) or 0

    # 3. 插入,冲突时跳过
    # ★ #161 治理: device_sn 为 NULL 的行原 6 列约束**对 NULL 不去重**(PG NULL≠NULL),
    #   故 on_conflict_do_nothing(constraint=...) 匹配不上 → 每次同步都重复插入
    #   (C-P1-7 / #160 事故: 累积 40603 行重复)。
    #   现按 device_sn 是否为空**分流到两个冲突目标**:
    #     - 非空: 原 6 列约束 uq_wallet_withdrawals_business
    #     - 为空: 部分唯一索引 uq_wallet_withdrawals_null_sn (5 列, WHERE device_sn IS NULL)
    _null_sn_rows = [r for r in unique_rows if r.get("device_sn") is None]
    _sn_rows = [r for r in unique_rows if r.get("device_sn") is not None]
    if _sn_rows:
        db.execute(
            pg_insert(WalletWithdrawal)
            .values(_sn_rows)
            .on_conflict_do_nothing(constraint="uq_wallet_withdrawals_business")
        )
    if _null_sn_rows:
        db.execute(
            pg_insert(WalletWithdrawal)
            .values(_null_sn_rows)
            .on_conflict_do_nothing(
                index_elements=["account_id", "op_type", "op_time", "operator", "op_amount"],
                index_where=text("device_sn IS NULL"),
            )
        )
    db.flush()
    # ⚠️ 2026-09-01 审计修复: 原 result.inserted += len(unique_rows) - existing_count
    #   在「BMS 页面时间窗口未设置成功、拉到大量已存在旧流水」时(existing_count > unique_rows),
    #   会产生负数"新增"(生产日志 -4885/-587/-515/-153/-15)。
    #   这里:
    #     a) inserted 不为负(统计口径真实 = 本次实际新增条数)
    #     b) 负数即告警: 提示"本轮拉到大量已存在旧数据, 疑似 BMS 时间窗口未设置成功"
    #        (窗口失败 → 拉到默认近一周 → 大多是已入库旧流水 → 便于早发现早修复)
    net_inserted = len(unique_rows) - existing_count
    if net_inserted < 0:
        logger.warning(
            "[wallet] 疑似时间窗口未设置成功: 本轮拉回 %d 行中 %d 行已存在于库内"
            " (净新增 %d<0)。BMS 页面可能保留了默认/上次时间范围, 建议检查该轮"
            " '设置时间范围' 日志与导出行 op_time 分布。",
            len(unique_rows), existing_count, net_inserted,
        )
    result.inserted += max(net_inserted, 0)

def upsert_merchant_refunds(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    """商户号现金流 - 按 (trade_time, merchant_order_no) 复合键去重 upsert

    微信退款单号在 Excel 导出时被 float 截断丢失精度,支付行则为 0,不可作主键。
    改用 (交易时间, 商户订单号) 复合去重键(已验证 86352 行零重复),重复导入按此键
    更新而非累计,后续拿到准确数据也能按此键覆盖补全。
    缺少交易时间或商户订单号的行无法定位,跳过。单批超 PG 65535 绑定参数上限,按 2000 行/批。
    """
    rows: list[dict] = []
    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            trade_time = _to_datetime(data.get("trade_time"))
            mono = data.get("merchant_order_no")
            mono = None if (isinstance(mono, str) and mono.strip() in ("", "0", "0.0")) else mono
            # 缺少去重键(交易时间或商户订单号)的行无法定位,跳过
            if trade_time is None or mono is None:
                result.add_error(idx, "缺少交易时间或商户订单号,无法去重定位(跳过)")
                continue
            # merchant_refund_no 为 "0"/"0.0" 时归一为 None(支付行无商户退款单号,非真实单号)
            mrn = data.get("merchant_refund_no")
            if isinstance(mrn, str) and mrn.strip() in ("0", "0.0"):
                mrn = None
            rows.append({
                "wx_refund_no": data.get("wx_refund_no"),
                "merchant_refund_no": mrn,
                "trade_time": trade_time,
                "app_id": data.get("app_id"),
                "merchant_id": data.get("merchant_id"),
                "sub_merchant_id": data.get("sub_merchant_id"),
                "device_no": data.get("device_no"),
                "wx_order_no": data.get("wx_order_no"),
                "merchant_order_no": mono,
                "user_id": data.get("user_id"),
                "trade_type": data.get("trade_type"),
                "trade_status": data.get("trade_status"),
                "pay_bank": data.get("pay_bank"),
                "currency": data.get("currency"),
                "settle_amount": _to_float(data.get("settle_amount")),
                "voucher_amount": _to_float(data.get("voucher_amount")),
                "refund_amount": _to_float(data.get("refund_amount")),
                "recharge_voucher_refund": _to_float(data.get("recharge_voucher_refund")),
                "refund_type": data.get("refund_type"),
                "refund_status": data.get("refund_status"),
                "product_name": data.get("product_name"),
                "fee": _to_float(data.get("fee")),
                "rate": _to_float(data.get("rate")),
                "order_amount": _to_float(data.get("order_amount")),
                "apply_refund_amount": _to_float(data.get("apply_refund_amount")),
            })
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}")
    if not rows:
        return
    _sync_devices(rows, "device_no", db)
    # 分批 on_conflict_do_update:按 (trade_time, merchant_order_no) 部分唯一索引去重更新。
    # index_where 须与索引定义一致以供 PG 推断该部分索引。
    before = db.scalar(select(func.count()).select_from(MerchantRefund)) or 0
    BATCH = 2000
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        stmt = pg_insert(MerchantRefund).values(batch)
        update_cols = {c: stmt.excluded[c] for c in batch[0].keys()
                       if c not in ("trade_time", "merchant_order_no")}
        stmt = stmt.on_conflict_do_update(
            index_elements=["trade_time", "merchant_order_no"],
            index_where=text("trade_time IS NOT NULL AND merchant_order_no IS NOT NULL"),
            set_=update_cols,
        )
        db.execute(stmt)
        db.flush()
    after = db.scalar(select(func.count()).select_from(MerchantRefund)) or 0
    inserted = max(after - before, 0)
    result.inserted += inserted
    result.updated += max(len(rows) - inserted, 0)

def upsert_shanmao_refunds(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    """闪猫退款(已退款申请) - 主键: refund_no

    实际 Excel 列(2026-08-03 样例,14列):
      退费单号,订单号,手机号码,套餐名称,实际支付金额,申请退款金额,退款金额,
      退款原因,退款状态,退款方式,审核人名称,审核时间,审核备注,申请备注
    """
    rows: list[dict] = []
    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            refund_no = (data.get("refund_no") or "").strip() if data.get("refund_no") else None
            if not refund_no:
                result.add_error(idx, "闪猫退款单号为空")
                continue

            # 姓名类字段清洗:数字/过长纯数字视为脏数据置空
            applicant = _sanitize_name_field(data.get("applicant"), idx, result, "闪猫退款-申请人")
            auditor = _sanitize_name_field(data.get("auditor"), idx, result, "闪猫退款-审核人")
            operator = _sanitize_name_field(data.get("operator"), idx, result, "闪猫退款-操作人")

            row_dict = {
                "refund_no": refund_no,
                "order_no": data.get("order_no"),
                "phone": data.get("phone"),
                "device_sn": data.get("device_sn"),
                "paid_amount": _to_float(data.get("paid_amount")),
                "refund_amount": _to_float(data.get("refund_amount")),
                "actual_amount": _to_float(data.get("actual_amount")),
                "package_name": data.get("package_name"),
                "merchant_name": data.get("merchant_name"),
                "refund_reason": data.get("refund_reason"),
                "refund_method": data.get("refund_method"),
                "status": data.get("status"),
                "applicant": applicant,
                "auditor": auditor,
                "operator": operator,
                "apply_time": _to_datetime(data.get("apply_time")),
                "audit_time": _sanitize_date(_to_datetime(data.get("audit_time"))),
                "refund_time": _to_datetime(data.get("refund_time")),
                "remark": data.get("remark"),
                "audit_remark": data.get("audit_remark"),
            }
            # 通用数据质量校验(L2)
            row_dict["quality_status"], row_dict["quality_note"] = check_shanmao_row(
                types.SimpleNamespace(**row_dict)
            )
            rows.append(row_dict)
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}")
    if not rows:
        return
    _sync_devices(rows, "device_sn", db)
    _execute_upsert(db, ShanmaoRefund, "refund_no", rows, result)



# ---------- 手动退款登记 数据质量校验(2026-08-23) ----------
# 业务规律: 登记日期应连续(基本同日/隔日), 不可能晚于今年或过早年份。
# 规则:
#   R1 示例模板行: 备注='示例' → 跳过导入
#   R2 登记日期超出合理范围[当前年-2 的1/1, 今天+3天]:
#      - 有操作时间佐证(在合理范围内) → 年份按 op_time 修正(保留月日), quality=auto_fixed
#      - 无佐证或修正后仍异常 → quality=pending(待人工确认)
#   R3 时间倒挂(op_time 早于 register_time 超过1天) → quality=pending
#   R4 op_time 自身未来/异常年份(> now+3d): 有正常 register_time 佐证 → 年份按 register_time 修正(保留月日时分), auto_fixed
#       (2026-09-05 补, #20: 曾发现 register_time=2026 正常但 op_time=2029 的录入笔误)
def _safe_replace_year(dt: datetime, year: int) -> datetime:
    """把 dt 的年份改为 year, 处理闰年 2/29 边界(2026-09-06 审查发现, #20)。

    datetime.replace(year) 在 2/29 且目标年非闰年时抛 ValueError → 原实现会让
    脏 2/29 数据整行丢弃。此 helper 退到 2/28(最小相邻日修正), 保留时分秒, 不丢行。
    """
    try:
        return dt.replace(year=year)
    except ValueError:
        # 2/29 → 目标非闰年: 退到 2/28
        return dt.replace(year=year, month=2, day=28)

def _manual_refund_quality_check(
    register_time: datetime | None,
    op_time: datetime | None,
    row_idx: int,
    result: ImportResult,
) -> tuple[datetime | None, datetime | None, str | None, str | None]:
    """手动退款登记时间质量校验: 返回 (修正后register_time, 修正后op_time, quality_status, quality_note)

    - R2 年份/范围异常(有操作时间佐证 → auto_fixed 修正年份; 无佐证 → pending)
    - R3 时间倒挂(操作早于登记超过1天) → pending(覆盖 R2 结果)
    - R4 op_time 未来年份(> now+3d): 有正常 register_time 佐证 → 修正 op_time 年份(auto_fixed)
    """
    now = datetime.now(timezone.utc)
    low = datetime(now.year - 2, 1, 1, tzinfo=timezone.utc)
    high = now + timedelta(days=3)
    status: str | None = None
    note: str | None = None
    # 质量问题类别标签(2026-09-08, #36): 写入 quality_note 前缀 [xxx],
    # 供"ok 保护"判定"同型问题已人工确认过, 不再重复报待办"。
    # 取值: 年份异常 / 操作时间异常 / 时间倒挂(见 _manual_refund_quality_kind)
    kind: str | None = None

    # R2 登记日期年份/范围异常
    if register_time is not None and not (low <= register_time <= high):
        orig = register_time
        if op_time is not None and low <= op_time <= high:
            fixed = _safe_replace_year(register_time, op_time.year)
            if low <= fixed <= high:
                status, note = "auto_fixed", (
                    f"登记日期年份异常(原始 {orig:%Y-%m-%d}), "
                    f"按操作时间 {op_time:%Y-%m-%d} 佐证修正为 {fixed:%Y-%m-%d}")
                register_time = fixed
        if status is None:
            status, note = "pending", f"登记日期超出合理范围(原始 {orig:%Y-%m-%d}), 无可靠佐证, 待人工确认"
        if status:
            kind = "年份异常"

    # R4 操作时间自身未来/异常年份(> now+3d): 有正常登记日期佐证 → 修正 op_time 年份
    if op_time is not None and op_time > high and register_time is not None and (low <= register_time <= high):
        fixed_op = _safe_replace_year(op_time, register_time.year)
        # 仅当修正后 op_time 不再未来且与登记日期不倒挂(允许同日)才算合理
        if low <= fixed_op <= high:
            if status is None:
                status, note = "auto_fixed", ""
            note = (note + " " if note else "") + (
                f"操作时间年份异常(原始 {op_time:%Y-%m-%d}), 按登记日期 {register_time:%Y-%m-%d} 佐证修正为 {fixed_op:%Y-%m-%d}")
            op_time = fixed_op
        else:
            if status is None:
                status, note = "pending", (
                    f"操作时间超出合理范围(原始 {op_time:%Y-%m-%d}), 无可靠佐证, 待人工确认")
        if status and not kind:
            kind = "操作时间异常"

    # R3 时间倒挂(操作早于登记超过1天): 优先级最高, 覆盖 R2/R4 的 pending 类别
    if op_time is not None and register_time is not None and op_time < register_time - timedelta(days=1):
        status, note = "pending", (
            f"操作时间 {op_time:%Y-%m-%d} 早于登记日期 {register_time:%Y-%m-%d} 超过1天, 待人工确认")
        kind = "时间倒挂"
    if status:
        # #36: 统一加类别标签前缀(ok 保护按类别比对, 同型不再重复报)
        note = f"[{kind}] {note}" if kind else note
        result.add_warning(row_idx, note)
    return register_time, op_time, status, note

def upsert_manual_refunds(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    """手动退款登记(商户号手机退款处理登记) - 按 (登记日期, 订单号, 设备号, 登记人) 去重 upsert

    设备号+订单号+登记日期+登记人 即可确定一笔退款单(见 _manual_dedup_key),
    重复导入按该键更新而非累计; 退款金额/状态被修改时更新同一行, 不再因金额变化产生新行。
    is_refunded 归一: 非"是"一律"否"(含空 → 未退款,计入登记蓄水)。
    缺登记日期或订单号的行无法定位,跳过。导入时做时间质量校验(自动纠错/待人工确认, 见
    _manual_refund_quality_check), 结果写入 quality_status/quality_note 供数据质量审核。
    """
    rows: list[dict] = []
    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            register_time = _ensure_afternoon_time(_to_datetime(data.get("register_time")))
            order_no = (data.get("order_no") or "").strip() if data.get("order_no") else None
            if register_time is None or not order_no:
                result.add_error(idx, "缺少登记日期或订单号,无法去重定位(跳过)")
                continue
            remark = (data.get("remark") or "").strip() if data.get("remark") is not None else ""
            # R1 示例模板行跳过
            if remark == "示例":
                result.add_warning(idx, "备注为'示例'的模板数据行,已跳过")
                continue
            op_time = _ensure_afternoon_time(_to_datetime(data.get("op_time")))
            # R2/R3/R4 时间质量校验(含自动纠错/待确认标记; R4 2026-09-05 起也修正 op_time 未来年份)
            register_time_before = register_time  # 纠错前原始值(存 register_time_raw)
            register_time, op_time, quality_status, quality_note = _manual_refund_quality_check(
                register_time, op_time, idx, result)
            # 人员污染检测(#36): 登记人/退款操作人被数字/时间戳/备注污染 → sanitize 会置空,
            # 此时补打 [人员污染] pending, 让 L3 质量页可见可处理(而非仅日志 warning 不可见)
            name_polluted = (_is_manual_refund_name_polluted(data.get("registrant"))
                             or _is_manual_refund_name_polluted(data.get("refund_operator")))
            if name_polluted and quality_status is None:
                quality_status, quality_note = "pending", "[人员污染] 登记人/退款操作人疑似列错位被置空, 待人工核对"
            registrant = _sanitize_name_field(data.get("registrant"), idx, result, "手动退款-登记人")
            refund_operator = _sanitize_name_field(data.get("refund_operator"), idx, result, "手动退款-退款操作人")
            raw_refunded = (data.get("is_refunded") or "").strip() if data.get("is_refunded") is not None else ""
            rows.append({
                "register_time": register_time,
                # ⚠️ 2026-09-05 (#20): 纠错(auto_fixed)发生时保留原始值到 register_time_raw,
                #   供审计/回滚; 未纠错(值与 register_time 相同)则置 NULL
                "register_time_raw": register_time_before if quality_status == "auto_fixed" else None,
                "device_sn": (data.get("device_sn") or "").strip() if data.get("device_sn") else None,
                "order_no": order_no,
                "merchant_id": (data.get("merchant_id") or "").strip() if data.get("merchant_id") else None,
                "order_amount": _to_float(data.get("order_amount")),
                "registrant": registrant,
                "is_refunded": "是" if raw_refunded == "是" else "否",
                "refund_amount": _to_float(data.get("refund_amount")),
                "refund_operator": refund_operator,
                "op_time": op_time,
                "remark": data.get("remark"),
                "quality_status": quality_status,
                "quality_note": quality_note,
            })
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}")
    if not rows:
        return
    _sync_devices(rows, "device_sn", db)
    # 同批次内去重: 按 (登记日期, 订单号, 设备号, 登记人) 键(Excel 中可能存在重复行,
    # PG ON CONFLICT DO UPDATE 不允许同一语句内二次命中,保留最后一条)
    seen: dict[tuple, dict] = {}
    for r in rows:
        seen[_manual_dedup_key(r)] = r
    rows = list(seen.values())
    # ok 保护(#36): 剔除"库内已人工确认(ok)且本次问题类别相同"的行, 冻结不更新不重报
    rows = _freeze_ok_manual_refund_rows(db, rows)
    if not rows:
        logger.info("[manual_refund] 全部行被 ok 保护冻结(无更新)")
        return
    # 分批 on_conflict_do_update: 按 (北京登记日期, order_no, COALESCE(device_sn,''), COALESCE(registrant,''))
    # 部分唯一索引去重更新(退款金额不参与键, 金额修改走 UPDATE)
    # #36: 加 WHERE 内容比较——未变行不更新(避免每轮整表重写历史行), RETURNING 精确计数
    total_ins = 0
    total_upd = 0
    BATCH = 2000
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        stmt = pg_insert(ManualRefund).values(batch)
        _key_cols = ("register_time", "order_no", "device_sn", "registrant")
        update_cols = {c: stmt.excluded[c] for c in batch[0].keys() if c not in _key_cols}
        # 内容比较字段: 去重键与系统派生/溯源字段不参与"是否变化"判定
        _compare_cols = [
            c for c in batch[0].keys()
            if c not in _key_cols and c not in ("quality_note", "register_time_raw")
        ]
        _where = None
        if _compare_cols:
            _where = or_(*[
                getattr(ManualRefund, c).is_distinct_from(stmt.excluded[c]) for c in _compare_cols
            ])
        stmt = stmt.on_conflict_do_update(
            # 表达式部分唯一索引 (北京登记日期, order_no, COALESCE(device_sn,''), COALESCE(registrant,''))
            index_elements=[
                text("((register_time AT TIME ZONE 'Asia/Shanghai')::date)"),
                text("order_no"),
                text("COALESCE(device_sn, '')"),
                text("COALESCE(registrant, '')"),
            ],
            index_where=text("order_no IS NOT NULL"),
            set_=update_cols,
            where=_where,
        )
        ret = db.execute(
            stmt.returning(text("(xmax = 0) AS _is_ins"))
        ).all()
        total_ins += sum(1 for r in ret if r[0])
        total_upd += len(ret) - sum(1 for r in ret if r[0])
        db.flush()
    result.inserted += total_ins
    result.updated += total_upd
