"""入库校验规则 —— 让坏值可见, 不静默(#42, 2026-09-10)

设计原则(用户 2026-09-10):
  "确保数据拿回来就是干净的, 或者少量问题在数据治理的时候再来处理"

本模块提供**四类校验规则**(对齐 quality_check 既有口径):
  R-时间  时间字段异常: 缺失/未来/纪元伪日期/时间倒挂
  R-枚举  枚举脏值: 值不在预期集合
  R-金额  金额异常: <=0 或缺失
  R-必填  关键字段缺失: 影响跨表关联与统计

## 处置分级 (abc-a, 2026-09-10)

校验结果**不丢弃数据**(入库语义与 #42 一期一致, 零回归风险), 但区分优先级:

  severity="block"  → 不可修复的身份/主锚点缺失: 该行无法参与统计与关联,
                      进入治理队列**优先**处理 (`SEV_BLOCK`)。
  severity="warn"   → 可修复/待复核: 时间越界、倒挂、枚举脏值、金额异常等,
                      进治理队列 (默认)。

设计依据(实证): 一律拒收会丢 8.6%~26.6% 的台账行 —— 其中大量是**真实历史数据**
(315 平台 2001~2019 台账 1556 行)。把这些行挡在业务表外, 报表口径会直接缺口,
且被拒行仍需人工逐条判定, 治理成本远高于"入库打标 + 治理队列"。
故采纳 D2 的**打标分支**(而非拒收分支): 不可修复 → 优先处理; 可修复 → 常规处理。

用法:
    issues = validate_rows(rows, data_type="external_complaint")
    # [{"row": 3, "rule": "R-时间", "field": "handle_time",
    #   "message": "...", "severity": "warn"}]
    by_sev = summarize_severity(issues)   # {"block": 1, "warn": 4}
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

# 合理年份窗口: 未来超 1 天即异常(上游录入笔误)。
#
# ⚠️⚠️ 2026-09-10 结论修正 (务必阅读, 我曾在此判断错误并已回退) ⚠️⚠️
#   第一版修正(错误): 看到本地库 complaint_date 有 2001~2019 共 1556 行且"逐年均匀分布",
#      误判为"315 平台真实历史台账", 于是把下限从 2020 放宽到 1990。
#   **该判断是错的**, 已用源表交叉验证推翻:
#      · 源文件 `315投诉处理登记表新.xlsx` 的「投诉日期」真实年份分布为
#        2025(1362) / 2026(1136) / 2024(138) / 2023(7) —— 几乎不存在 <2023 的投诉;
#      · 而库内 `<2024` 竟有 1908 行。逐行反查(手机号+问题签名):
#          库内 2009-03-23 ← 源表 2024-03-09
#          库内 2001-03-24 ← 源表 2025-03-01
#          库内 2023-10-17 ← 源表 2023-10-18
#      · 规律: **库内年份 = 2000 + 真值"日"; 库内"日" = 真值年份 − 2001**。
#        即源表日期的「年/月/日」三段在入库时被**转置**了。
#        "逐年均匀分布"正是该公式的产物(库内年份按真值日 1~31 循环), 不是真实台账分布 ——
#        我最初正是被这个"看似合理"的分布骗过。
#   修正后的口径(下方常量):
#      · 伪日期判定 = 落在 **投诉业务存在之前**(< _MIN_VALID_YEAR_BASE) → 必为解析错误;
#        取 2018 作基线(业务可查投诉最早为 2023-09, 留 5 年余量, 既可捕获上述转置产物,
#        又不会误伤任何真实投诉)。
#      · 未来日期(> now+1d) → 同为解析产物(上方 128 行即此类)。
#      · 1970 纪元形态由 (d.year < _MIN_VALID_YEAR_BASE) 一并覆盖, 无需单独分支。
#
#   ⚠️ 遗留真缺陷(不在本模块职责内, 需单独排期): 上述"年/月/日转置"的**写入侧成因**
#       尚未定位(现有 _to_datetime 对该列不会产出该形态, 疑发生在上游 workbuddy 侧
#       JSON 生成或早期版本)。**在校验层打标只是可见化, 不等于已修复**;
#       在该缺陷修掉前, 依赖 complaint_date 的时效/趋势统计仍不可信。
#       另注: 转置产物覆盖 库内年 2001~2031(库内年 = 2000 + 真值日, 真值日 1~31),
#       因此**任何**年份阈值都只能捕获其中一部分; 阈值取 2020 捕获其中较早的一批
#       (库内年 <2020 共 1556 行), 余下落在合理年份区间的需靠别的信号另行识别。
_MIN_VALID_YEAR_BASE = 2020   # < 此年份 → 早于业务存在期, 判为解析错误(保守阈值)
_MIN_VALID_YEAR = _MIN_VALID_YEAR_BASE   # 兼容旧常量名
_EPOCH_FAKE_LAST_YEAR = _MIN_VALID_YEAR_BASE   # 兼容旧常量名
_MAX_FUTURE_DAYS = 1

# 处置分级(abc-a): block=不可修复/优先处理; warn=可修复/常规处理
SEV_BLOCK = "block"
SEV_WARN = "warn"


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=_UTC)


def _check_time(dt: Any, field: str, row_no: int, issues: list[dict], *, label: str) -> None:
    """R-时间: 未来 / 早于业务存在期 检测(缺失由具体 data_type 的必填规则另行判定)

    判定依据见模块顶部常量注释(⚠️ 结论修正段): 早于业务存在期(< _MIN_VALID_YEAR_BASE)
    与未来日期 **都**是解析错误的产物 ——
      · 未来: 如库内 2027-04-25 ← 真值(编号内嵌) 2026-04-27
      · 过早: 如库内 2001-03-24 ← 源表真值 2025-03-01(年/月/日转置, 库内年 = 2000 + 真值日)
    两者都会使时效/趋势统计失真, 故均按 R-时间 上报。
    """
    d = _as_utc(dt if isinstance(dt, datetime) else None)
    if d is None:
        return
    now = datetime.now(_UTC)
    if d > now + timedelta(days=_MAX_FUTURE_DAYS):
        issues.append({
            "row": row_no, "rule": "R-时间", "field": field,
            "message": f"{label}为未来时间({d:%Y-%m-%d}), 疑似解析错误",
            "severity": SEV_WARN,
        })
    elif d.year < _MIN_VALID_YEAR_BASE:
        issues.append({
            "row": row_no, "rule": "R-时间", "field": field,
            "message": (
                f"{label}早于业务存在期({d:%Y-%m-%d}, 早于 {_MIN_VALID_YEAR_BASE} 年), "
                f"疑似解析错误(年份错位/序列号伪日期)"
            ),
            "severity": SEV_WARN,
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
            "severity": SEV_BLOCK,
        })
    _check_time(cd, "complaint_date", row_no, issues, label="投诉日期")
    _check_time(ht, "handle_time", row_no, issues, label="处理时间")

    # 时间倒挂: 处理时间早于投诉日期 1 天以上(物理不可能; 实测 228 行存量由此暴露)
    d_cd, d_ht = _as_utc(cd if isinstance(cd, datetime) else None), _as_utc(ht if isinstance(ht, datetime) else None)
    if d_cd and d_ht and d_ht < d_cd - timedelta(days=1):
        issues.append({
            "row": row_no, "rule": "R-时间", "field": "handle_time",
            "message": f"处理时间({d_ht:%Y-%m-%d})早于投诉日期({d_cd:%Y-%m-%d})",
            "severity": SEV_WARN,
        })

    result = rec.get("result")
    if _is_present(result) and str(result).strip() not in _EXTERNAL_RESULT_SET:
        issues.append({
            "row": row_no, "rule": "R-枚举", "field": "result",
            "message": f"结果值不在预期集合: {str(result)[:24]!r}",
            "severity": SEV_WARN,
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
            "severity": SEV_BLOCK,
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
            "severity": SEV_BLOCK,
        })
    reg, op = rec.get("register_time"), rec.get("op_time")
    _check_time(reg, "register_time", row_no, issues, label="登记时间")
    _check_time(op, "op_time", row_no, issues, label="操作时间")
    d_reg, d_op = _as_utc(reg if isinstance(reg, datetime) else None), _as_utc(op if isinstance(op, datetime) else None)
    if d_reg and d_op and d_op < d_reg - timedelta(days=1):
        issues.append({
            "row": row_no, "rule": "R-时间", "field": "op_time",
            "message": f"操作时间({d_op:%Y-%m-%d})早于登记时间({d_reg:%Y-%m-%d})",
            "severity": SEV_WARN,
        })
    if str(rec.get("is_refunded") or "").strip() == "是":
        amt = rec.get("refund_amount")
        try:
            if amt is not None and float(amt) <= 0:
                issues.append({
                    "row": row_no, "rule": "R-金额", "field": "refund_amount",
                    "message": f"已退款但退款金额 <= 0({amt})",
                    "severity": SEV_WARN,
                })
        except (TypeError, ValueError):
            issues.append({
                "row": row_no, "rule": "R-金额", "field": "refund_amount",
                "message": f"退款金额非数值({amt!r})",
                "severity": SEV_WARN,
            })
    return issues


# 处置分级常量已移至模块顶部(见 SEV_BLOCK / SEV_WARN)


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
            "severity": SEV_WARN,
        })
    return out


def summarize_issues(issues: list[dict[str, Any]]) -> dict[str, int]:
    """问题清单 → 按规则聚合计数(供 summary/日志展示)"""
    agg: dict[str, int] = {}
    for it in issues:
        rule = str(it.get("rule") or "其他")
        agg[rule] = agg.get(rule, 0) + 1
    return agg


def summarize_severity(issues: list[dict[str, Any]]) -> dict[str, int]:
    """问题清单 → 按处置级别聚合计数 ({"block": n, "warn": m})

    供 IngestResult.extra["validation_severity"] 与采集日志展示, 使"优先处理项"
    无需人工在完整清单里翻找。
    """
    agg = {SEV_BLOCK: 0, SEV_WARN: 0}
    for it in issues:
        sev = str(it.get("severity") or SEV_WARN)
        agg[sev] = agg.get(sev, 0) + 1
    return agg


def blocking_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """仅保留需优先处理者(severity=block, 或校验器自身告警)"""
    return [
        it for it in issues
        if str(it.get("severity") or "") == SEV_BLOCK or it.get("rule") == "R-校验器"
    ]
