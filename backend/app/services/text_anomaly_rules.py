"""文本异常判据（工单 #168，设计 §3.1）—— 导入期与扫描期的**单一来源**。

═══════════════════════════════════════════════════════════════════════
为什么要有这个模块（判据同源）
═══════════════════════════════════════════════════════════════════════
同一件事要在两个时点判断：
  · **导入期**（治新增）：`upsert.run_upsert` 拿到的是**原始值** → 能精确判断
  · **扫描期**（治存量）：D4 探测器在库上跑 SQL → 只能看到**已入库的值**

若两边各写一套判据，迟早漂移成「导入期拦的口径 ≠ 扫描期报的口径」
（同 A-20260910-004 菜单漂移的教训）。故此处定义**唯一**的：
  · 阈值常量（`NEAR_LIMIT_RATIO` / `near_limit_threshold`）
  · 类型枚举（`KIND_*`）
  · 白名单（`NEWLINE_ALLOW_COLUMNS`，逐条写理由）
  · 两种渲染：Python 侧 `kinds_of()`（导入期）/ SQL 侧 `sql_predicates()`（扫描期）
并有单测断言「同一份入库后的数据，两边结论一致」（见 tests/test_governance_text_anomaly.py）。

═══════════════════════════════════════════════════════════════════════
白名单是**证据驱动**的，不是拍脑袋
═══════════════════════════════════════════════════════════════════════
2026-09-20 生产探测（data_sources 登记表的 33 个文本列，Text 或 varchar(n≥200)）：
  含换行的列仅 5 个 —— external_complaints.problem(213/2699)、card_complaints.problem(59/770)、
  external_complaints.handle_detail(9/2699)、manual_refunds.remark(3/9239)、refunds.remark(2/185169)。
其中 `problem`（投诉问题描述，用户原文）与 `handle_detail`（处理过程记录）的换行是
**业务真实形态**（投诉内容本就多行）→ 进白名单；两个 `remark` 合计仅 5 行/近 20 万行，
形态可疑 → **不进白名单**，交 AI 研判（这正是框架「业务允许 vs 数据错误」的用武之地）。
"""

from typing import Any

# ── 异常类型枚举（finding.metrics.kinds / 留痕 reject_reason 共用）──
KIND_NEWLINE = "newline"          # 单元格内含换行
KIND_NEAR_LIMIT = "near_limit"    # 长度贴近列宽上限（截断嫌疑）

# 贴近列宽上限的判定比例：截断后长度恰等于列宽，留 5% 容差覆盖「截断后又被 strip」
NEAR_LIMIT_RATIO = 0.95

# ── 白名单：业务确需多行的列（按**列名**，因为同一语义列出现在多张表）──
# ⚠️ 新增白名单项必须写明理由与证据，否则白名单会退化成「误报开关」
NEWLINE_ALLOW_COLUMNS: dict[str, str] = {
    "problem": "投诉问题描述（用户原文，多行是业务真实形态；生产实测 external_complaints 213 行 / card_complaints 59 行）",
    "handle_detail": "投诉处理详情（多行记录处理过程；生产实测 9 行）",
}

# ── 留痕分类（写入 ingest_rejected_rows.reject_category，工单 #168）──
CATEGORY_NEWLINE = "newline_suspect"
CATEGORY_TRUNCATE = "truncate_suspect"
CATEGORY_BY_KIND = {KIND_NEWLINE: CATEGORY_NEWLINE, KIND_NEAR_LIMIT: CATEGORY_TRUNCATE}


def is_newline_allowed(column: str | None) -> bool:
    """该列是否允许含换行（白名单内 → 不报换行异常）。"""
    return bool(column) and column in NEWLINE_ALLOW_COLUMNS


def near_limit_threshold(col_max_len: int) -> int:
    """贴上限判定的**整数**阈值（Python 与 SQL 两侧必须用同一个数，否则边界不一致）。"""
    return int(col_max_len * NEAR_LIMIT_RATIO)


def kinds_of(value: Any, col_max_len: int | None, *, column: str | None = None) -> list[str]:
    """Python 侧判据（导入期用）：值 → 命中的异常类型列表。

    `col_max_len` 为 None（Text 列）→ 不判贴上限（与 SQL 侧一致：Text 无列宽）。
    """
    if value is None:
        return []
    s = str(value)
    out: list[str] = []
    if "\n" in s and not is_newline_allowed(column):
        out.append(KIND_NEWLINE)
    if col_max_len and len(s) >= near_limit_threshold(col_max_len):
        out.append(KIND_NEAR_LIMIT)
    return out


def sql_predicates(col_ident: str, col_max_len: int | None) -> list[tuple[str, str]]:
    """SQL 侧判据（扫描期用）：列标识符 → [(类型, WHERE 条件)]。

    与 `kinds_of` **逐条对应**（同一阈值常量、同一白名单）；`col_ident` 由调用方
    用双引号包好（本模块不做标识符拼接，避免把引用规则混进判据）。
    """
    out: list[tuple[str, str]] = []
    if not is_newline_allowed(col_ident.strip('"')):
        out.append((KIND_NEWLINE, f"{col_ident} LIKE '%' || chr(10) || '%'"))
    if col_max_len:
        out.append((KIND_NEAR_LIMIT,
                    f"length({col_ident}) >= {near_limit_threshold(col_max_len)}"))
    return out


def display_value(value: Any, *, max_chars: int = 200) -> str:
    """把值渲染成**人能看懂**的证据串：换行显示为 ⏎（否则看不出问题在哪）。"""
    if value is None:
        return ""
    s = str(value).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "⏎")
    return s[:max_chars] + ("…" if len(s) > max_chars else "")
