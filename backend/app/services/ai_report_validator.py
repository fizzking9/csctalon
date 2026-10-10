"""预生成 AI 报告写库前校验闸门 (R-P1-1 / #238)

评审 §3.2 W2: 预生成 LLM 输出原样写 `ai_reports`, 无 schema/数值校验, 空回答/截断残句/
程序占位符/模板要求维度缺失会被静默落库并 is_public=True 对外公开。

本模块对齐 `governance_judge_service.validate_resp` 与
`onboarding_draft_service.validate_llm_output` 的成熟模式:
    validate(...) -> (是否合法: bool, 错误列表: list[str])
调用方(ai_report_worker)在**写库前**校验; 非法重试 1 次, 两次均非法则不落库。

报告产物是 **markdown 文本(非 JSON)**, 故"schema 校验"落地为结构/完整性检查,
"数值校验"只做高置信、零误伤的占位符/格式护栏 —— 不做逐数字与 feed 对账(脆弱且
环比负百分比等合法负号会被误杀)。维度覆盖判定复用 #152 的 check_dimensions(单一真身,
锚点规则不在此重复实现)。
"""

from __future__ import annotations

import logging
import re
from types import SimpleNamespace

from app.models.ai_report_template import AiReportTemplate
from app.services.ai_completeness_service import check_dimensions

logger = logging.getLogger(__name__)

# 去空白后最小字符数: 只拦截近乎空/残句, 不卡简报(brief 本身结论导向较短)。
_MIN_ANSWER_CHARS = 30

# 程序占位 token: 中文经营报告绝不应出现这些字面量(出现即 LLM 把程序值泄漏进成稿)。
# 单词边界匹配, 大小写敏感(None 为 Python 字面量; null 为 JSON/JS 字面量)。
_PLACEHOLDER_RE = re.compile(r"\b(?:NaN|Infinity|undefined|null|None)\b")

# markdown 代码围栏(```)必须成对闭合; 奇数个 = 成稿被截断在代码块内。
_FENCE = "```"


def validate_pregen_report(answer_md: str | None,
                           template: AiReportTemplate | None) -> tuple[bool, list[str]]:
    """校验一份预生成报告 markdown 是否可安全落库。

    返回 (ok, errors); ok=True 时 errors 为空。纯文本判定, 不查库、不抛异常
    (check_dimensions 自身异常时按"完整"处理, 不阻塞写库)。
    """
    errors: list[str] = []
    md = (answer_md or "").strip()

    # ① 非空 + 最小长度
    if not md:
        # 空回答无需继续跑其余检查, 直接返回(后续检查对空串无意义)。
        return False, ["报告内容为空(LLM 未返回正文)"]
    if len(re.sub(r"\s+", "", md)) < _MIN_ANSWER_CHARS:
        errors.append(f"报告正文过短(去空白<{_MIN_ANSWER_CHARS}字), 疑似空回答或被 max_tokens 截断")

    # ② 代码围栏成对
    if md.count(_FENCE) % 2 != 0:
        errors.append("markdown 代码围栏(```)未闭合, 成稿疑似截断")

    # ③ 程序占位符(数值/格式护栏)
    placeholders = sorted(set(_PLACEHOLDER_RE.findall(md)))
    if placeholders:
        errors.append(f"报告含不应出现的程序占位符: {', '.join(placeholders)}")

    # ④ 模板要求维度全覆盖(复用 #152 锚点结构判定; required 为空则不校验)
    required = (template.required_dimensions or []) if template is not None else []
    if required:
        # check_dimensions 入参需带 .id/.answer_md 的 report 形状; 写库前尚无 id, 用轻量对象
        # (与 ai_feedback_service 对历史快照重算时的 SimpleNamespace 用法一致)。
        fake_report = SimpleNamespace(id=None, answer_md=md)
        dim = check_dimensions(None, fake_report, template)
        if not dim.get("complete"):
            missing = [str(m) for m in (dim.get("missing") or [])]
            errors.append("模板 required_dimensions 未全覆盖(锚点缺失或写成暂无/未提供): "
                          + "、".join(missing))

    return (not errors), errors
