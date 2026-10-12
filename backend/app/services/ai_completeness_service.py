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
  * **锚点行**两类形态(D9 扩展, 2026-10-12):
    1. 粗体锚点 `**<标签>**：<内容>`(可带列表前缀) —— 关键词须命中**标签**(严格,
       防旁述句"与四路合计差异 100%"误判);
    2. 标题锚点 `#{2,6} <标签>[:：<内容>]` —— 关键词命中**标题整行**(标签或冒号后
       内容)。依据: system 铁律 3 "H2 锚点结构"把"锚点"指向标题, detail 体裁下
       双模型(gpt-oss:120b/qwen3.8:27b)三轮实验(v0 boost/v1 中段枚举/v2 末尾契约)
       均稳定输出标题式结构(数据在标题行内或紧随其后的列表/表格), 纯粗体锚点
       纪律不可达成(0/4~0/8), 生产 rolling_7d/30d 断供 5 天同根因。
    判定:
    - 无锚点行 → 缺失(旁述/建议/比较句不算)
    - 内容为空/只剩分隔符/「无」/首段含缺失标记 → **段落块检查**: 向后看同一段落块
      (跳过空行, 最多 8 非空行, 遇 `#` 标题或 `**` 锚点行即停): 块内任一行含数字
      (数据存在) → 判覆盖; 无数字 → 缺失。
      (解决"空锚点行 + 完整数据列表/表格在后续行"被拒的主失败形态)
    - 内容有数据(含数字或非缺失陈述) → 覆盖
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

# 标题锚点行(D9): `#{2,6} <标签>[:：<内容>]` —— 不含 H1(文档标题)
_HEADING_RE = re.compile(r"^\s*#{2,6}\s+(?P<rest>.+)$")

# 维度"声明了但内容为空"的标记(LLM 按 _SYSTEM_PROMPT 铁律 1 如实写"暂无/未提供")
# "缺失" 覆盖"数据缺失"语境(实测 09-18 快照四路行写"数据缺失——未提供 A/B/C/D",
# 若不在标记表, 同句"四路退款金额及涉诉率数据缺失"会被误判为已覆盖 → 触发补算更诚实)
_MISSING_MARKERS = ("未提供", "暂无", "未统计", "无数据", "缺失")
# 宽松形态: "未直接提供"/"未在给定 JSON 中提供"等(实测 LLM 措辞), 行内 未…提供
_MISSING_RE = re.compile(r"未[^。；\n]{0,10}提供")

# 关键词后跟这些结尾也算空(如 "四路:", "四路：" 后无内容)
_EMPTY_TAILS = {"", ":", "：", "—", "-", "无", "无。", "无。"}

# D9(2026-10-12) 段落块检查: 数字即数据存在的最小信号(报告是经营数字文档)
_DIGIT_RE = re.compile(r"\d")
# 段落块边界: `#` 标题行 或 下一个锚点行(`**标签**：`) → 块结束, 不再向后归属
_SECTION_STOP_RE = re.compile(r"^\s*(?:#{1,6}\s|\*\*[^*]{1,24}?\*\*[:：])")
# 段落块最多向后看的非空行数(防跨段误归属)
_SECTION_MAX_LINES = 8


def _has_missing_mark(text: str) -> bool:
    """text 是否含缺失标记(精确词表 + 宽松"未…提供"正则)。"""
    if any(m in text for m in _MISSING_MARKERS):
        return True
    return _MISSING_RE.search(text) is not None


def _anchor_verdict(line: str, kw: str) -> bool | None:
    """结构检查: 该行作为维度 kw 的锚点行的判定。

    返回:
      True  → 锚点行命中 kw 且行内自带内容(直接覆盖)
      None  → 是 kw 的锚点行, 但行体为空或首段含缺失标记 → 需段落块检查
      False → 该行不是 kw 的锚点行(不覆盖, 继续看其他行)

    两类锚点形态(见模块 docstring D9 扩展):
      - 粗体 `**<标签>**：` —— 关键词须命中标签(严格, 防旁述);
      - 标题 `#{2,6} <标签>[:：<内容>]` —— 关键词命中标题整行(标签或内容)。
    """
    m = _ANCHOR_RE.match(line)
    if m:
        label, body = m.group("label"), m.group("body")
        if kw.lower() not in label.lower():
            return False
    else:
        h = _HEADING_RE.match(line)
        if not h:
            return False
        rest = h.group("rest").strip()
        parts = re.split(r"[:：]", rest, 1)
        if len(parts) == 2:
            label, body = parts[0], parts[1]
        else:
            label, body = rest, ""
        # 标题是节标题: 关键词命中标题整行即构成该维度的章节(标签或冒号后内容)。
        if kw.lower() not in (label + body).lower():
            return False
    body_clean = body.strip().rstrip("。. ")
    if not body_clean or body_clean in _EMPTY_TAILS:
        return None
    first_part = re.split(r"[,，、;；]", body_clean, 1)[0].strip()
    if _has_missing_mark(first_part):
        return None
    return True


def _section_body_has_data(lines: list[str], anchor_idx: int) -> bool:
    """D9(2026-10-12): 锚点行之后的段落块是否含数据。

    解决"空锚点行(或首段缺失标记) + 完整数据列表/表格在后续行"被拒的模式
    (生产 rolling_7d/30d 断供 5 天归因的主失败形态)。向后看最多 8 个非空行,
    跳过空行(markdown 表格/列表与锚点行之间常见空行), 遇 `#` 标题或下一个
    锚点行即停 —— 数据必须紧邻锚点行, 防跨段误归属。块内任一行含数字即判
    有数据(报告是经营数字文档, 纯缺失陈述行不含数字)。
    """
    scanned = 0
    for ln in lines[anchor_idx + 1:]:
        s = ln.strip()
        if not s:
            continue
        if _SECTION_STOP_RE.match(ln):
            break
        scanned += 1
        if scanned > _SECTION_MAX_LINES:
            break
        if _DIGIT_RE.search(s):
            return True
    return False


def _entry_covered(md: str, entry: str | list) -> bool:
    """判定一个维度条目(字符串或同义词数组)在 answer_md 中是否覆盖。"""
    keywords = entry if isinstance(entry, list) else [entry]
    lines = md.splitlines()
    for kw in keywords:
        kw_s = str(kw).strip()
        if not kw_s:
            continue
        for i, ln in enumerate(lines):
            v = _anchor_verdict(ln, kw_s)
            if v is True:
                return True
            if v is None and _section_body_has_data(lines, i):
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
