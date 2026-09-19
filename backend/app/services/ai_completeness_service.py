"""AI 报告完整性校验器 (工单 #152)

设计(A-20260919-001 §五): 按模板 required_dimensions(关键词数组)校验快照 answer_md
的维度覆盖, 判定"这份快照能不能直接回答这个维度细问"。

判定(全树统一, 大小写不敏感):
- 对每个 required_dimension 条目:
  * 条目可为单个关键词字符串, 或同义词数组(任一同义词命中即覆盖)
  * 关键词未出现在 answer_md → 缺失
  * 关键词出现, 但所在行紧跟"未提供/暂无/未统计/无数据"标记 → 缺失
    (维度声明了但内容为空, 如「四路分布: 未提供」→ 算未覆盖, 触发补算)
  * 否则 → 覆盖
- 全部覆盖 → complete=True; 任一缺失 → complete=False + missing=list

失败处理: required_dimensions 为空/异常 → complete=True(不阻塞摘取秒回)。
"""

import logging
import re

from sqlalchemy.orm import Session

from app.models.ai_report import AiReport
from app.models.ai_report_template import AiReportTemplate

logger = logging.getLogger(__name__)

# 维度"声明了但内容为空"的标记(LLM 按 _SYSTEM_PROMPT 铁律 1 如实写"暂无/未提供")
# "缺失" 覆盖"数据缺失"语境(实测 09-18 快照四路行写"数据缺失——未提供 A/B/C/D",
# 若不在标记表, 同句"四路退款金额及涉诉率数据缺失"会被误判为已覆盖 → 触发补算更诚实)
_MISSING_MARKERS = ("未提供", "暂无", "未统计", "无数据", "缺失")
# 宽松形态: "未直接提供"/"未在给定 JSON 中提供"等(实测 LLM 措辞), 行内 未…提供
_MISSING_RE = re.compile(r"未[^。；\n]{0,10}提供")


def _has_missing_mark(text: str) -> bool:
    """text 是否含缺失标记(精确词表 + 宽松"未…提供"正则)。"""
    if any(m in text for m in _MISSING_MARKERS):
        return True
    return _MISSING_RE.search(text) is not None

# 关键词后跟这些结尾也算空(如 "四路:", "四路：" 后无内容)
_EMPTY_TAILS = {"", ":", "：", "—", "-", "无", "无。", "无。"}


def _line_covered(line: str, kw: str) -> bool:
    """判定单个 md 行中关键词 kw 是否"有效出现"(未落在缺失语境)。

    规则(实测 09-18 快照修正: LLM 缺数据时写法多样):
      a. 关键词**前 30 字符**含缺失标记 → 空(如「数据中未直接**提供**四路金额」)
      b. 关键词后数据段首片段(到逗号/顿号为止)**含数字** → 覆盖
         (如「四路分布: 线上直退 23 笔, 线下未提供」→ 首片段是数据, 不算空)
      c. 首片段或数据段整体含缺失标记 → 空
         (如「四路: 未提供」「退款四路金额…未在给定 JSON 中**提供**」)
      d. 否则 → 覆盖(如「四路分布: A 线上直退, B 线下」)
    """
    idx = line.lower().find(kw.lower())
    if idx == -1:
        return False
    # a. 关键词前的紧邻文本含缺失标记 → 空
    before = line[max(0, idx - 30):idx]
    if _has_missing_mark(before):
        return False
    tail = line[idx + len(kw):].strip()
    if not tail:
        return False
    # 数据段 = 关键词后第一个冒号(ASCII 或全角)之后的内容(无冒号则整段)
    seg = tail
    ci = tail.find(":")
    ci2 = tail.find("：")
    if ci2 != -1 and (ci == -1 or ci2 < ci):
        ci = ci2
    if ci != -1:
        seg = tail[ci + 1:].strip()
    seg_clean = seg.rstrip("。. ")
    if not seg_clean:
        return False
    if seg_clean in _EMPTY_TAILS:
        return False
    first_part = re.split(r"[,，、;；]", seg_clean, 1)[0].strip()
    # b. 首片段含数字(明确数据) → 覆盖(不受 c 中"部分未提供"误伤)
    if re.search(r"\d", first_part):
        return True
    # c. 首片段或数据段整体含缺失标记 → 空
    if _has_missing_mark(first_part):
        return False
    if _has_missing_mark(seg_clean):
        return False
    return True


def _entry_covered(md: str, entry: str | list) -> bool:
    """判定一个维度条目(字符串或同义词数组)在 answer_md 中是否覆盖。"""
    keywords = entry if isinstance(entry, list) else [entry]
    lines = md.splitlines()
    for kw in keywords:
        kw_s = str(kw).strip()
        if not kw_s:
            continue
        for ln in lines:
            if _line_covered(ln, kw_s):
                return True
    return False


def check_dimensions(db: Session, report: AiReport,
                     template: AiReportTemplate | None) -> dict:
    """按模板 required_dimensions 校验快照 answer_md 的维度覆盖。

    参数:
      db: Session(预留, 当前判定纯文本无需查库)
      report: AiReport(命中快照, answer_md 是校验对象)
      template: AiReportTemplate(required_dimensions 来源; None/空 → complete=True)

    返回:
      {
        "complete": bool,       # 是否维度齐全
        "missing": list[str],   # 缺失维度列表(complete=True 时为空)
        "required": list[str]   # 校验的维度清单(调试用)
      }
    """
    required = (template.required_dimensions or []) if template is not None else []
    md = report.answer_md or ""
    try:
        if not required:
            return {"complete": True, "missing": [], "required": []}
        missing: list[str] = []
        for entry in required:
            if not _entry_covered(md, entry):
                missing.append(entry)
        complete = not missing
        if not complete:
            logger.info(
                "check_dimensions: report_id=%s template=%s missing=%s",
                report.id, getattr(template, "code", None), missing,
            )
        return {"complete": complete, "missing": missing,
                "required": list(required)}
    except Exception as e:  # noqa: BLE001  校验异常不阻塞摘取秒回
        logger.warning("check_dimensions 异常(report_id=%s), 按完整处理: %s", report.id, e)
        return {"complete": True, "missing": [], "required": list(required)}
