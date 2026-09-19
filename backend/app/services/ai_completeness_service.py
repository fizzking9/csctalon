"""AI 报告完整性校验器 (工单 #152)

设计(A-20260919-001 §五): 按模板 required_dimensions(关键词数组)校验快照 answer_md
的维度覆盖, 判定"这份快照能不能直接回答这个维度细问"。

**结构检查原则(2026-09-19 用户拍板)**: 报告结构由**提示词**确定(生成侧
【维度输出格式】约定每个维度必须以二级锚点行 `**<维度>**：<数据>` 输出, 缺失写
`**<维度>**：暂无`), 校验器只做**结构匹配**, 不做语义猜测(不猜行内措辞/旁述句/
比较句)。两边提示词对齐(ai_feed 生成侧 + ai_extract 摘取侧) → 简报/摘要/对齐同源。

判定(大小写不敏感):
- 对每个 required_dimension 条目:
  * 条目可为单个关键词字符串, 或同义词数组(任一同义词命中即覆盖)
  * **锚点行**形如 `**<标签>**：<内容>`(可带 `-`/`*` 列表前缀), 且标签含该维度关键词:
    - 无锚点行 → 缺失(旁述/建议/比较句不算, 如"与四路合计差异 100%")
    - 锚点内容为空/只剩分隔符/「无」 → 缺失
    - 锚点内容首段(到逗号/顿号)含缺失标记(未提供/暂无/未统计/无数据/缺失) → 缺失
      (如 `**四路金额（A/B/C/D）**：**暂无**…`)
    - 否则 → 覆盖(如 `**四路分布**：A 线上直退 23 笔、B 线下 12 笔`)
- 全部覆盖 → complete=True; 任一缺失 → complete=False + missing=list

失败处理: required_dimensions 为空/异常 → complete=True(不阻塞摘取秒回)。
"""

import logging
import re

from sqlalchemy.orm import Session

from app.models.ai_report import AiReport
from app.models.ai_report_template import AiReportTemplate

logger = logging.getLogger(__name__)

# 维度锚点行: 可选列表前缀 + **标签** + 冒号(ASCII/全角) + 内容
#   生成侧【维度输出格式】约定的结构; 标签限 24 字符(防过度匹配长句)
_ANCHOR_RE = re.compile(r"^\s*(?:[-*+]\s*)?\*\*(?P<label>[^*]{1,24}?)\*\*[:：](?P<body>.*)$")

# 维度"声明了但内容为空"的标记(LLM 按 _SYSTEM_PROMPT 铁律 1 如实写"暂无/未提供")
# "缺失" 覆盖"数据缺失"语境(实测 09-18 快照四路行写"数据缺失——未提供 A/B/C/D",
# 若不在标记表, 同句"四路退款金额及涉诉率数据缺失"会被误判为已覆盖 → 触发补算更诚实)
_MISSING_MARKERS = ("未提供", "暂无", "未统计", "无数据", "缺失")
# 宽松形态: "未直接提供"/"未在给定 JSON 中提供"等(实测 LLM 措辞), 行内 未…提供
_MISSING_RE = re.compile(r"未[^。；\n]{0,10}提供")

# 关键词后跟这些结尾也算空(如 "四路:", "四路：" 后无内容)
_EMPTY_TAILS = {"", ":", "：", "—", "-", "无", "无。", "无。"}


def _has_missing_mark(text: str) -> bool:
    """text 是否含缺失标记(精确词表 + 宽松"未…提供"正则)。"""
    if any(m in text for m in _MISSING_MARKERS):
        return True
    return _MISSING_RE.search(text) is not None


def _line_covered(line: str, kw: str) -> bool:
    """结构检查: 该行是否是含维度 kw 的锚点行且内容非空非缺失。

    仅认 `**<标签>**：<内容>` 结构(标签含 kw)。旁述/比较/建议句一律不算覆盖
    —— 结构由提示词保证, 代码不做语义猜测。
    """
    m = _ANCHOR_RE.match(line)
    if not m:
        return False
    if kw.lower() not in m.group("label").lower():
        return False
    body = m.group("body").strip()
    body_clean = body.rstrip("。. ")
    if not body_clean or body_clean in _EMPTY_TAILS:
        return False
    first_part = re.split(r"[,，、;；]", body_clean, 1)[0].strip()
    if _has_missing_mark(first_part):
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
