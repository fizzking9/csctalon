"""入库校验规则 —— 让坏值可见, 不静默(#42, 2026-09-10)

设计原则(用户 2026-09-10):
  "确保数据拿回来就是干净的, 或者少量问题在数据治理的时候再来处理"

本模块提供**四类校验规则**(对齐 quality_check 既有口径):
  R-时间  时间字段异常: 缺失/未来/远古/时间倒挂/跨度异常
  R-枚举  枚举脏值: 值不在预期集合
  R-金额  金额异常: <=0 或缺失
  R-必填  关键字段缺失: 影响跨表关联与统计

⚠️ 本期为**只读检测**(advisory): 输出问题清单供 IngestResult/summary/日志展示,
   **不丢弃、不改数据** —— 避免一次性改变入库语义引入回归。
   P2 给投诉表补 QualityMixin 后, 这些规则将用于自动打标 pending(进治理队列)。

用法:
    issues = validate_rows(rows, data_type="external_complaint")
    # [{"row": 3, "rule": "R-时间", "field": "handle_time", "message": "..."}]
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

_UTC = timezone.utc

# ── 枚举预期集合(与 quality_check.py 同口径, 避免漂移) ──
_EXTERNAL_RESULT_SET = {
    "和解结案", "撤销结案", "暂行办结", "调解结案", "调节结案", "投诉无效",
    "重复投诉", "终止调节", "退回消委会", "未达成一致方案", "暂行办理", "需跟进",
    "不予受理", "已提交报告",
}# ⚠️ 保留别名兼容: 上游曾有"未达成一致方案"(方案) 与"未达成一致意见"(意见) 两种写法,
#    两者都应视为合法办结类结果, 避免被 R-枚举 误报。
_EXTERNAL_RESULT_SET |= {"未达成一致方案"}# 注: 工单(ticket)类校验器尚未接入(本期仅投诉/手动退款); 若后续接入,
#     其状态集合为 {待处理, 处理中, 已完成, 已关闭, 终止, 已结案, 已解决, 待分派, 跟进中, pending}。

# 合理年份窗口: 未来超 1 天即异常(上游录入笔误);
# 下限与回填脚本 fix_complaint_handle_time._BAD_BEFORE 保持一致(2020),
# 用于捕获 1969/1970 这类伪日期(Excel 序列号解析 bug 产物)。
_MAX_FUTURE_DAYS = 1
_MIN_VALID_YEAR = 2020


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=_UTC)


def _check_time(dt: Any, field: str, row_no: int, issues: list[dict], *, label: str) -> None:
    """R-时间: 未来/远古 检测(缺失由具体 data_type 的必填规则另行判定)"""
    d = _as_utc(dt if isinstance(dt, datetime) else None)
    if d is None:
        return
    now = datetime.now(_UTC)
    if d > now + timedelta(days=_MAX_FUTURE_DAYS):
        issues.append({
            "row": row_no, "rule": "R-时间", "field": field,
            "message": f"{label}为未来时间({d:%Y-%m-%d})",
        })
    elif d.year < _MIN_VALID_YEAR:
        issues.append({
            "row": row_no, "rule": "R-时间", "field": field,
            "message": f"{label}年份异常({d:%Y-%m-%d}, 疑似伪日期/解析错误)",
        })


def _is_present(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return v.strip() != "" and v.strip().lower() not in ("nan", "null", "none")
    return True


def validate_external_complaint_row(rec: dict[str, Any], row_no: int = 0) -> list[dict[str, Any]]:
    """校验单条 315 外部投诉行(清洗后) → 问题清单

    规则:
      R-必填  投诉日期缺失(时效/趋势统计的主时间锚点)
      R-时间  投诉日期/处理时间为未来或远古; 处理时间早于投诉日期(倒挂)
      R-枚举  result 不在预期集合(未知状态会让"已办结"判定失效)
    """
    issues: list[dict[str, Any]] = []
    cd, ht = rec.get("complaint_date"), rec.get("handle_time")

    if not _is_present(cd):
        issues.append({
            "row": row_no, "rule": "R-必填", "field": "complaint_date",
            "message": "投诉日期缺失(时效/趋势统计主锚点)",
        })
    _check_time(cd, "complaint_date", row_no, issues, label="投诉日期")
    _check_time(ht, "handle_time", row_no, issues, label="处理时间")

    # 时间倒挂: 处理时间早于投诉日期 1 天以上(物理不可能; 实测 228 行存量由此暴露)
    d_cd, d_ht = _as_utc(cd if isinstance(cd, datetime) else None), _as_utc(ht if isinstance(ht, datetime) else None)
    if d_cd and d_ht and d_ht < d_cd - timedelta(days=1):
        issues.append({
            "row": row_no, "rule": "R-时间", "field": "handle_time",
            "message": f"处理时间({d_ht:%Y-%m-%d})早于投诉日期({d_cd:%Y-%m-%d})",
        })

    result = rec.get("result")
    if _is_present(result) and str(result).strip() not in _EXTERNAL_RESULT_SET:
        issues.append({
            "row": row_no, "rule": "R-枚举", "field": "result",
            "message": f"结果值不在预期集合: {str(result)[:24]!r}",
        })
    return issues


def validate_card_complaint_row(rec: dict[str, Any], row_no: int = 0) -> list[dict[str, Any]]:
    """校验单条 卡商群投诉行(清洗后) → 问题清单

    卡商 result 是另一套语义(已解决/已退款完成/投诉错误/流量虚…), 不做枚举校验。
    """
    issues: list[dict[str, Any]] = []
    cd = rec.get("complaint_date")
    if not _is_present(cd):
        issues.append({
            "row": row_no, "rule": "R-必填", "field": "complaint_date",
            "message": "投诉日期缺失(统计主锚点)",
        })
    _check_time(cd, "complaint_date", row_no, issues, label="投诉日期")
    return issues


def validate_manual_refund_row(rec: dict[str, Any], row_no: int = 0) -> list[dict[str, Any]]:
    """校验单条 手动退款行(清洗后) → 问题清单

    规则:
      R-必填  订单号缺失(去重键/对账关联关键字段)
      R-时间  操作时间/登记时间 未来或远古; 操作时间早于登记时间(倒挂)
      R-金额  退款金额 <= 0(已退款行)
    """
    issues: list[dict[str, Any]] = []
    if not _is_present(rec.get("order_no")):
        issues.append({
            "row": row_no, "rule": "R-必填", "field": "order_no",
            "message": "订单号缺失(去重键/对账关联关键字段)",
        })
    reg, op = rec.get("register_time"), rec.get("op_time")
    _check_time(reg, "register_time", row_no, issues, label="登记时间")
    _check_time(op, "op_time", row_no, issues, label="操作时间")
    d_reg, d_op = _as_utc(reg if isinstance(reg, datetime) else None), _as_utc(op if isinstance(op, datetime) else None)
    if d_reg and d_op and d_op < d_reg - timedelta(days=1):
        issues.append({
            "row": row_no, "rule": "R-时间", "field": "op_time",
            "message": f"操作时间({d_op:%Y-%m-%d})早于登记时间({d_reg:%Y-%m-%d})",
        })
    if str(rec.get("is_refunded") or "").strip() == "是":
        amt = rec.get("refund_amount")
        try:
            if amt is not None and float(amt) <= 0:
                issues.append({
                    "row": row_no, "rule": "R-金额", "field": "refund_amount",
                    "message": f"已退款但退款金额 <= 0({amt})",
                })
        except (TypeError, ValueError):
            issues.append({
                "row": row_no, "rule": "R-金额", "field": "refund_amount",
                "message": f"退款金额非数值({amt!r})",
            })
    return issues


# data_type → 校验函数
_VALIDATORS: dict[str, Any] = {
    "external_complaint": validate_external_complaint_row,
    "card_complaint": validate_card_complaint_row,
    "manual_refund": validate_manual_refund_row,
}


def validate_rows(rows: list[dict[str, Any]], data_type: str) -> list[dict[str, Any]]:
    """批量校验 → 问题清单(只读, 不改数据)

    未注册校验器的 data_type 返回空清单(不影响入库)。
    ⚠️ 单行异常不抛出(校验不得阻塞入库), 但会**计入 validator_errors 计数**并告警,
       避免"系统性报错被逐行静默吞掉"导致校验形同虚设(2026-09-10 审查 WARNING)。
    """
    fn = _VALIDATORS.get(data_type)
    if fn is None or not rows:
        return []
    out: list[dict[str, Any]] = []
    n_err = 0
    for i, rec in enumerate(rows):
        try:
            out.extend(fn(rec, i))
        except Exception as e:  # noqa: BLE001 校验本身不得阻塞入库
            n_err += 1
            logger.warning(f"[ingest_validator] {data_type} 第 {i} 行校验异常(跳过): {e}")
    if n_err:
        # 以可识别的问题条目上报, 使"校验器自身失效"也能被看到
        out.append({
            "row": -1, "rule": "R-校验器", "field": None,
            "message": f"{n_err} 行校验时发生异常(校验器可能存在缺陷, 需排查)",
        })
    return out


def summarize_issues(issues: list[dict[str, Any]]) -> dict[str, int]:
    """问题清单 → 按规则聚合计数(供 summary/日志展示)"""
    agg: dict[str, int] = {}
    for it in issues:
        rule = str(it.get("rule") or "其他")
        agg[rule] = agg.get(rule, 0) + 1
    return agg
