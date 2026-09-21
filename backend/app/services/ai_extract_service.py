"""AI 报告摘取层 (工单 #135 批次 B2)

设计(架构文档 A-20260917-001 LOCKED §三):
- split_sections(answer_md) -> dict[str, str]: 按 ## H2 锚点切分草稿(与 #134 _SYSTEM_PROMPT
  第 3 条锚点一致), 兜底按段落分(日报无 H2 时)
- extract_section(db, question, report, template) -> dict: 关键词路由 scope → 段落
  → 调轻量 qwen2.5:7b(经 talon, #145)摘取+排版

LLM 调用约束(2026-09-21 改用能力档位):
- provider_code=llm_tiers.resolve_tier("assistant") —— 首选 coder-30b / 备选 7b / 云端兜底
- timeout_s=60(前台实时问答用户在等, 超时返回 clarify 兜底)
- max_tokens=1200(摘取输出比生成全文短, 1200 足够)
- LLM 指令铁律: 基于给定段落内容摘取+排版, 禁止编造/外推/添加未在段落中出现的数据

失败兜底: LLM 异常/超时 → 返回 {need_clarify:True, clarify_options:草稿 H2 标题列表},
让 ai.py 走 L3 兜底(返回 clarify + 保留实时 LLM 路径), 不抛异常。
"""

import logging
import re

from sqlalchemy.orm import Session

from app.models.ai_report import AiReport
from app.models.ai_report_template import AiReportTemplate
from app.services import llm_client, llm_tiers
from app.services.ai_focus_service import keywords_to_scopes
from app.services.ai_report_matcher import _count_keyword_hits  # #144 复用, 全树统一口径

logger = logging.getLogger(__name__)

# 2026-09-21: 不再硬编码 provider_code，改走**能力档位**（llm_tiers）。
# 摘取排版 = 速度优先、非数值任务（实测 7b / coder-30b 均能正确保留数字）→ assistant。
# ⚠️ 与 ai_semantic_matcher 属同一档位但需求相反（那边 7b 实测全错）——
#    档位内「首选 coder-30b / 备选 7b」两者均能胜任本任务，故共用档位无问题。
_TIER = "assistant"

# H2 锚点(与 #134 _SYSTEM_PROMPT 第 3 条一致; 周报/月报必须按这 5 个锚点结构化)
H2_ANCHORS = ["## 接待分析", "## 退款分析", "## 投诉分析", "## 趋势对比", "## 结论建议"]

# scope → H2 段落映射(与 ai_feed._select_feed_sections 同款逻辑, 全树统一)
#   reception/refund/complaint 三个业务段一一对应;
#   趋势对比/结论建议 不在 keywords_to_scopes 路由, 由泛问兜底取全部段落覆盖
_SCOPE_TO_H2 = {
    "reception": "## 接待分析",
    "refund":    "## 退款分析",
    "complaint": "## 投诉分析",
}

# 27b 摘取指令(铁律: 基于给定段落内容摘取+排版, 禁止编造/外推)
# #152 对齐生成侧: 识别 **<维度>**： 二级锚点行(生成侧【维度输出格式】约定的结构),
#   问句指向某维度时优先摘该锚点行及其后内容; 锚点行写"暂无"时如实保留。
_EXTRACT_SYSTEM = (
    "你是经营数据分析摘取助手。基于给定的草稿段落, 按问句摘取相关部分并排版输出。\n"
    "铁律:\n"
    "1. 只能从【给定段落】中摘取, 严禁编造、外推或添加未在段落中出现的数据。\n"
    "2. 严格按问句意图排版: 简要问给简要答, 详细问给详细答(可保留段落内明细与表格)。\n"
    "3. 段落内 `**<维度>**：` 是维度锚点行(如 `**四路分布**：A 线上直退 23 笔`); "
    "问句指向某维度时, 优先摘取该锚点行及其后内容, 锚点行写\"暂无/未提供\"时如实保留, "
    "不要从旁述/建议句里拼凑该维度的数据。\n"
    "4. 回答中文, 开头注明数据窗口(直接引用段落里出现的窗口说明)。\n"
    "5. 段落里没有的部分如实写\"未提供\", 不要从段落外内容补充。\n"
    "6. 金额单位保持与段落一致(元/分), 不要转换。"
)


def _load_extract_system(db) -> str:
    """#178 提示词版本化: DB 生效版本(extract_system)优先，无/异常回退代码常量（铁律）。"""
    try:
        from app.services.prompt_config_service import load_effective_prompt
        return load_effective_prompt(db, "extract_system") or _EXTRACT_SYSTEM
    except Exception:  # noqa: BLE001  提示词加载失败不阻塞摘取
        return _EXTRACT_SYSTEM

# 行首 ## 标题正则(2 级标题; MULTILINE 让 ^ 匹配每行开头)
_H2_TITLE_RE = re.compile(r"^##\s+.+$", re.MULTILINE)


def split_sections(answer_md: str) -> dict[str, str]:
    """按 ## H2 锚点切分草稿。

    返回 {section_title: section_body} 如 {"## 退款分析": "退款总额..."}。
    兜底: 若 answer_md 不含 ## H2(日报), 按 \\n\\n 段落分, key 用段落首行前 20 字。
    空草稿返回 {}。
    """
    if not answer_md:
        return {}
    md = answer_md.strip()
    if not md:
        return {}
    matches = list(_H2_TITLE_RE.finditer(md))
    sections: dict[str, str] = {}
    if matches:
        for i, m in enumerate(matches):
            title = m.group().strip()
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(md)
            body = md[start:end].strip()
            sections[title] = body
        return sections
    # 兜底: 按 \n\n 段落分(日报无 H2)
    paras = [p.strip() for p in md.split("\n\n") if p.strip()]
    if not paras:
        return {}
    for i, p in enumerate(paras):
        first_line = p.split("\n", 1)[0][:20]
        key = f"段落{i + 1}: {first_line}" if first_line else f"段落{i + 1}"
        sections[key] = p
    return sections


async def extract_section(db: Session, question: str, report: AiReport,
                          template: AiReportTemplate | None = None) -> dict:
    """从命中预生成报告按问句摘取相关段落 + 按问句排版输出。

    流程(架构文档 §3.2):
      1. split_sections(report.answer_md) 切分草稿
      2. 关键词路由: keywords_to_scopes(question) → scope 集合
         scope 到段落的映射(与 ai_feed._select_feed_sections 同款):
           reception → ## 接待分析, refund → ## 退款分析, complaint → ## 投诉分析
         泛问(无 scope 命中)→ 取全部段落(让 27b 自己选相关部分)
      3. 拼接命中段落 body 成 input_md(多 scope 命中 → 多段拼接, 让 27b 组织结构)
      4. 调 27b(provider_code=None, timeout_s=30, max_tokens=1200)摘取+排版
      5. 输出 JSON {result_md, confidence, need_clarify, clarify_options}

    参数:
      db: Session(供 LLM 调用记日志/落库)
      question: 用户问句(用于路由 + LLM 摘取指令)
      report: AiReport ORM 实例(answer_md 是切分源)
      template: 模板(预留; #135 暂未使用, 后续可用 question_keywords 优化路由)

    返回:
      {
        "result_md": str,          # 摘取+排版后的输出(空当 need_clarify=True)
        "confidence": float,       # 摘取置信度(默认 0.7, 失败=0.0)
        "need_clarify": bool,      # 是否需要澄清(段落空/LLM 失败)
        "clarify_options": list[str]  # 草稿 H2 标题列表(need_clarify=True 时填)
      }

    失败处理: LLM 调用失败(超时/异常) → need_clarify=True, clarify_options=草稿 H2 标题,
    让 ai.py 走 L3 兜底, 不抛异常。

    #144 规则短路(摘取层超时根因修复):
      若 template 不为 None AND _count_keyword_hits(question,
      template.question_keywords) >= 2 → 直接返 report.answer_md 全文,
      不调 LLM。原因: keyword 命中 ≥ 2 说明用户问句就是模板原文意图(如
      月报模板 keywords=['本月','这个月','月报','上月','同比'], 用户问「本月
      经营情况...含同比」命中 2 个), 整篇预生成报告都与问句相关, 不必摘取。
      跳过 27b 冷加载(35s > 30s timeout 必超时), <1s 返回。
    """
    # #144 规则短路: keyword 命中 ≥ 2 → 直接返全文(不调 LLM, 避免冷加载超时)
    if template is not None and report.answer_md:
        hits = _count_keyword_hits(question, template.question_keywords)
        if hits >= 2:
            logger.info("extract_section #144 短路: report_id=%s template=%s "
                        "keyword_hits=%d ≥2 → 直接返全文(不调 LLM)",
                        report.id, getattr(template, "code", None), hits)
            return {"result_md": report.answer_md, "confidence": 0.95,
                    "need_clarify": False, "clarify_options": []}

    sections = split_sections(report.answer_md or "")
    if not sections:
        logger.warning("extract_section: report_id=%s answer_md 为空或不可切分", report.id)
        return {"result_md": "", "confidence": 0.0, "need_clarify": True, "clarify_options": []}

    # 关键词路由 scope(复用 ai_focus_service.keywords_to_scopes, 全树统一)
    scopes = keywords_to_scopes(question)
    # #181 专项桶: 命中报告的模板 scope 是权威专项(如退款桶=全篇退款内容),
    # 问句无专项意图(泛问)时也并入该专项段 —— 整体日报才需要泛问取全部段落,
    # 专项桶按模板 scope 取对应段, 避免整篇重复输出。
    tmpl_scope = getattr(template, "scope", "overview") if template is not None else "overview"
    if tmpl_scope in _SCOPE_TO_H2:
        scopes = scopes | {tmpl_scope}
    selected_bodies: list[str] = []
    if scopes:
        for scope in scopes:
            h2 = _SCOPE_TO_H2.get(scope)
            if h2 and h2 in sections:
                selected_bodies.append(f"{h2}\n{sections[h2]}")
    if not selected_bodies:
        # 泛问(无 scope 命中)→ 取全部段落, 让 27b 自己选相关部分
        for title, body in sections.items():
            selected_bodies.append(f"{title}\n{body}")

    input_md = "\n\n".join(selected_bodies)
    if not input_md:
        return {"result_md": "", "confidence": 0.0, "need_clarify": True,
                "clarify_options": list(sections.keys())}

    user_prompt = (
        f"问句: {question}\n\n"
        f"草稿段落(来自预生成报告, 窗口见段落内说明):\n{input_md}\n\n"
        "请按问句意图从上面段落摘取相关部分并排版输出, "
        "保留段落内的数据窗口说明, 禁止编造/外推。"
    )
    messages = [
        # #178 提示词版本化: DB 有生效版本(extract_system) → 用之；无/异常 → 代码常量兜底
        {"role": "system", "content": _load_extract_system(db)},
        {"role": "user", "content": user_prompt},
    ]
    try:
        result = await llm_client.chat_completion(
            messages, max_tokens=1200, timeout_s=60.0,
            db=db, source="ai_extract", provider_code=llm_tiers.resolve_tier(_TIER),
        )
        result_md = str(result).strip()
        if not result_md:
            logger.warning("extract_section: LLM 返回空, report_id=%s", report.id)
            return {"result_md": "", "confidence": 0.0, "need_clarify": True,
                    "clarify_options": list(sections.keys())}
        return {"result_md": result_md, "confidence": 0.7,
                "need_clarify": False, "clarify_options": []}
    except Exception as e:  # noqa: BLE001  LLM 失败不阻塞, 让 ai.py 走 L3 兜底
        logger.warning("extract_section LLM 调用失败(report_id=%s): %s", report.id, e)
        return {"result_md": "", "confidence": 0.0, "need_clarify": True,
                "clarify_options": list(sections.keys())}
