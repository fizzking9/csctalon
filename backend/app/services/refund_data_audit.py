"""退款数据审计服务（#196）

定位
----
对「商户号手机退款处理登记」共享文档与 manual_refunds 入库数据做确定性审计：
1. 采集偏差：文档行 vs 库行对账（丢行/多行/键漂移）
2. 日期跳跃：文档按行序相邻「登记日期」跳变检测
3. 负数金额：订单金额/退款金额负值检测

铁律
----
1. 全确定性，不调模型；凡工具能算的，不让模型逐行判断
2. 误报必须为 0：已打标治理行（quality_status 非空）不计入 fail
3. 证据不足的异常标 pending_review，不标 fail

口径
----
- 对账键 = (登记日期[北京日], 订单号, SN, 登记人)
- SN 规范化：去首尾空白/引号；仅当整体匹配 \\d+\\.0 时截尾（禁全局 replace）
- 库 register_time 为 UTC，转北京日期后入键
- 异常年份：登记年 <2024 或 >2026（文档为退款登记，业务不可能超出）
- 日期跳跃：sheet 内按 Excel 行号排序，相邻已解析日期 |gap|>7 天记一处；
  成对 ± 同幅度为行序颠倒，标 info 不计异常
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

# ── 解析与规范化 ──────────────────────────────────────────────────────────

_DT_FORMATS = (
    "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d",
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
    "%Y.%m.%d %H:%M", "%Y.%m.%d",
    "%Y年%m月%d日 %H:%M", "%Y年%m月%d日",
)
_YMD_LOOSE_RE = re.compile(r"(20\d{2})\s*[年./\-]\s*(\d{1,2})\s*[月./\-]\s*(\d{1,2})")
_NUM_RE = re.compile(r"-?\d+(\.\d+)?")
_SN_FLOAT_TAIL_RE = re.compile(r"\d+\.0")
_QUOTES = "'\"‘’“”"

VALID_YEAR_MIN = 2024
VALID_YEAR_MAX = 2026
DATE_JUMP_THRESHOLD_DAYS = 7


def parse_doc_datetime(s: Any) -> datetime | None:
    """解析文档日期字符串（北京时间 naive）。解析失败返回 None。"""
    s = str(s or "").strip()
    if not s or s in ("/", "0", "None", "nan"):
        return None
    for fmt in _DT_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    m = _YMD_LOOSE_RE.match(s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    # ★ #196: YY/MMDD 斜杠紧凑形态(如 '26/0920' → 2026-09-20)
    m2 = re.match(r'^(\d{2})/(\d{2})(\d{2})$', s)
    if m2:
        try:
            dt = datetime(2000 + int(m2.group(1)), int(m2.group(2)), int(m2.group(3)))
        except ValueError:
            return None
        return dt
    return None


def parse_amount(s: Any) -> float | None:
    """解析金额：去 ,￥¥ 后 float；失败则从文本提取首个数字；再失败 None。"""
    s = str(s or "").strip().replace(",", "").replace("￥", "").replace("¥", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        m = _NUM_RE.search(s)
        return float(m.group(0)) if m else None


def norm_sn(s: Any) -> str:
    """SN 规范化：去首尾空白/引号；整体为 \\d+\\.0 时截尾。"""
    s = str(s or "").strip().strip(_QUOTES)
    if _SN_FLOAT_TAIL_RE.fullmatch(s):
        s = s[:-2]
    return s


def norm_text(s: Any) -> str:
    return str(s or "").strip()


def beijing_date(dt: datetime) -> str:
    """UTC aware datetime → 北京日期串。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
    return (dt.astimezone(timezone(timedelta(hours=8)))).strftime("%Y-%m-%d")


# ── 主审计 ────────────────────────────────────────────────────────────────

def audit_refund_data(
    doc_rows: list[dict],
    db_rows: list[dict],
) -> dict[str, Any]:
    """退款数据审计主入口。

    :param doc_rows: [{sheet, row(2-based), raw{中文列名:str}}]
    :param db_rows:  manual_refunds 行 dict 列表（英文列名，register_time 为
                     datetime 或可解析字符串）
    :return: 结构化审计报告（含计数、样例、verdict）
    """
    # ── 1. 文档侧逐行解析 ────────────────────────────────────────────────
    parsed: list[dict] = []
    empty_rows: list[dict] = []
    unparseable: list[dict] = []
    negatives: list[dict] = []
    for r in doc_rows:
        raw = r["raw"]
        order_no = norm_text(raw.get("订单号", ""))
        sn = norm_sn(raw.get("设备号", ""))
        registrant = norm_text(raw.get("登记人", ""))
        reg_dt = parse_doc_datetime(raw.get("登记日期", ""))
        oa = parse_amount(raw.get("订单金额", ""))
        ra = parse_amount(raw.get("退款金额", ""))
        loc = {"sheet": r["sheet"], "row": r["row"]}
        if not order_no and not sn and not registrant:
            empty_rows.append(loc)
            continue
        if reg_dt is None:
            unparseable.append({**loc, "raw": str(raw.get("登记日期", ""))[:30]})
        for field, val in (("订单金额", oa), ("退款金额", ra)):
            if val is not None and val < 0:
                negatives.append({**loc, "field": field, "value": val})
        parsed.append({**loc, "reg_dt": reg_dt, "order_no": order_no,
                       "sn": sn, "registrant": registrant})

    # ── 2. 日期跳跃（sheet 内按行号排序）─────────────────────────────────
    jumps: list[dict] = []
    sheets: dict[str, list[dict]] = {}
    for p in parsed:
        if p["reg_dt"]:
            sheets.setdefault(p["sheet"], []).append(p)
    for sheet_rows in sheets.values():
        sheet_rows.sort(key=lambda p: p["row"])
        for a, b in zip(sheet_rows, sheet_rows[1:]):
            gap = (b["reg_dt"] - a["reg_dt"]).days
            if abs(gap) > DATE_JUMP_THRESHOLD_DAYS:
                jumps.append({"sheet": a["sheet"],
                              "row_from": a["row"], "row_to": b["row"],
                              "from": a["reg_dt"].strftime("%Y-%m-%d"),
                              "to": b["reg_dt"].strftime("%Y-%m-%d"),
                              "gap_days": gap})
    # 成对 ± 同幅度 = 行序颠倒（info）；其余为真跳变（warn）
    paired_keys = set()
    real_jumps = []
    for i, j in enumerate(jumps):
        if i in paired_keys:
            continue
        mate = None
        for k in range(i + 1, min(i + 3, len(jumps))):
            m = jumps[k]
            if (m["sheet"] == j["sheet"] and m["row_from"] == j["row_to"]
                    and m["gap_days"] == -j["gap_days"]):
                mate = k
                break
        if mate is not None:
            paired_keys.add(mate)
            j["kind"] = "row_swap"
        else:
            j["kind"] = "jump"
            real_jumps.append(j)

    # ── 3. 库侧异常 ─────────────────────────────────────────────────────
    def db_dt(v: Any) -> datetime | None:
        if isinstance(v, datetime):
            return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
        s = str(v or "").strip()
        if not s:
            return None
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    governed = 0
    weird_year: list[dict] = []
    db_negatives: list[dict] = []
    db_key_rows: list[tuple] = []
    for row in db_rows:
        if norm_text(row.get("quality_status", "")):
            governed += 1
        reg = db_dt(row.get("register_time"))
        oa = parse_amount(row.get("order_amount"))
        ra = parse_amount(row.get("refund_amount"))
        for field, val in (("order_amount", oa), ("refund_amount", ra)):
            if val is not None and val < 0:
                db_negatives.append({"id": row.get("id"), "field": field, "value": val})
        if reg and not (VALID_YEAR_MIN <= reg.year <= VALID_YEAR_MAX):
            weird_year.append({"id": row.get("id"),
                               "register_time": reg.isoformat()})
        db_key_rows.append((
            beijing_date(reg) if reg else "",
            norm_text(row.get("order_no", "")),
            norm_sn(row.get("device_sn", "")),
            norm_text(row.get("registrant", "")),
        ))

    # ── 4. 对账（多重集合）──────────────────────────────────────────────
    doc_key_list = [
        (p["reg_dt"].strftime("%Y-%m-%d") if p["reg_dt"] else "",
         p["order_no"], p["sn"], p["registrant"], p["sheet"])
        for p in parsed
    ]
    doc_keys = Counter(k[:4] for k in doc_key_list)
    db_keys = Counter(db_key_rows)
    doc_only = doc_keys - db_keys
    db_only = db_keys - doc_keys
    matched = sum((doc_keys & db_keys).values())

    # 文档内重复（同键多份）：跨 sheet 重叠 vs 同 sheet 重复
    doc_dup_extra = 0
    doc_dup_cross_sheet = 0
    for key, cnt in doc_keys.items():
        if cnt > 1:
            extra = cnt - 1
            doc_dup_extra += extra
            sheets = {k[4] for k in doc_key_list if k[:4] == key}
            if len(sheets) > 1:
                doc_dup_cross_sheet += extra

    # 真实漂移：doc_only 中 (订单号,SN) 不在库 = 文档有而库里没有（真丢行）
    db_order_sn = {(k[1], k[2]) for k in db_key_rows}
    doc_order_sn = {(k[1], k[2]) for k in doc_key_list}
    truly_missing = sum(1 for k, c in doc_only.items()
                        if (k[1], k[2]) not in db_order_sn for _ in range(c))
    # 库侧无法解释的多余行：未打标 且 (订单号,SN) 不在文档
    db_only_keyset = set(db_only)
    untagged_extra = 0
    for row, key in zip(db_rows, db_key_rows):
        if key in db_only_keyset and not norm_text(row.get("quality_status", "")):
            if (key[1], key[2]) not in doc_order_sn:
                untagged_extra += 1

    # ── 5. verdict ──────────────────────────────────────────────────────
    verdict = {
        "collection_drift": (
            "pass" if truly_missing == 0 and untagged_extra == 0
            else "warn"
        ),
        "date_jump": "pass" if not real_jumps else "warn",
        "negative_amount": (
            "pass" if not negatives and not db_negatives else "fail"
        ),
    }

    return {
        "doc": {
            "total_rows": len(doc_rows),
            "empty_rows": len(empty_rows),
            "date_unparseable": len(unparseable),
            "date_unparseable_sample": unparseable[:5],
            "negative_amount_rows": len(negatives),
            "negative_sample": negatives[:5],
            "date_jumps_gt7d": len(jumps),
            "date_jump_row_swaps": len(jumps) - len(real_jumps),
            "date_jump_sample": jumps[:10],
            "internal_duplicate_extra": doc_dup_extra,
            "cross_sheet_duplicate_extra": doc_dup_cross_sheet,
        },
        "db": {
            "total_rows": len(db_rows),
            "known_governed_rows": governed,
            "weird_year_rows": len(weird_year),
            "weird_year_sample": weird_year[:5],
            "negative_amount_rows": len(db_negatives),
        },
        "reconcile": {
            "doc_keys_total": sum(doc_keys.values()),
            "db_keys_total": sum(db_keys.values()),
            "matched": matched,
            "doc_only_rows": sum(doc_only.values()),
            "db_only_rows": sum(db_only.values()),
            "truly_missing_in_db": truly_missing,
            "untagged_extra_in_db": untagged_extra,
            "doc_only_sample": [list(k) for k in list(doc_only)[:5]],
            "db_only_sample": [list(k) for k in list(db_only)[:5]],
        },
        "verdict": verdict,
    }
