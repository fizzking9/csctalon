"""AI 报告语义匹配层 (工单 #135 批次 B2)

设计(架构文档 A-20260917-001 LOCKED §四):
- semantic_match(db, question, candidates) -> dict: 27b 判定问句与候选报告整体相关性
- confidence≥0.6 视为命中 → 走摘取层; 否则走 L3 clarify 兜底
- 候选取前 5 条(避免 LLM 输入过长), 每条 question + answer_md 前 200 字摘要

LLM 调用约束(#145 改指轻量模型, 消除大模型冷加载超时):
- provider_code=_SEMANTIC_PROVIDER_CODE(talon-gmk-qwen2.5:7b, 经 talon 统一管理接入)
- timeout_s=30(前台实时问答用户在等)
- max_tokens=200(只输出 JSON 数组, 短)
- LLM 输出 JSON: [{index, confidence, reason}], confidence∈[0,1]

失败兜底: LLM 异常/JSON 解析失败 → 返回 {hit_report:None, confidence:0.0},
让 ai.py 走 L3 clarify 兜底, 不抛异常。
"""

import json
import logging

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.ai_report import AiReport
from app.services import llm_client

logger = logging.getLogger(__name__)

# #145 语义匹配走轻量模型(经 talon 统一管理接入): 判定问句↔报告相关性是封闭分类任务,
#   qwen2.5:7b 足够, 已在内存秒回, 不经 27b 冷加载超时。改动前走 default 链优先命中 27b。
_SEMANTIC_PROVIDER_CODE = "talon-gmk-qwen2.5-7b"

# confidence 阈值(架构文档 §4.3: 默认 0.6; 可通过 settings.AI_SEMANTIC_MATCH_THRESHOLD 配置)
_DEFAULT_THRESHOLD = 0.6

_SYSTEM = (
    "你是问句与报告相关性判定助手。基于给定的问句和候选报告摘要, "
    "判定问句与每个候选报告的语义相关性, 输出 JSON 数组。\n"
    "铁律:\n"
    "1. 若问句与经营数据、候选报告内容完全无关(如闲聊、寒暄、问候、"
    "个人话题等非经营问题), 所有候选的 confidence 必须输出 0, "
    "严禁给出任何非 0 值。\n"
    "2. confidence 只表示问句与报告内容的相关程度, 无关即 0; "
    "不要因为报告本身写得详细就提高置信度。"
)


async def semantic_match(db: Session, question: str,
                         candidates: list[AiReport]) -> dict:
    """27b 判定问句与候选报告的整体语义相关性。

    流程(架构文档 §4.2):
      1. 候选报告按 created_at DESC(由调用方排序)取前 5 条
      2. 每条候选取 question + answer_md 前 200 字摘要
      3. 调 27b(provider_code=None, timeout_s=30, max_tokens=200)输出 JSON 数组
      4. 取 confidence 最大者, 若 ≥ 阈值(默认 0.6)视为命中

    参数:
      db: Session
      question: 用户问句
      candidates: 候选报告列表(L1 未命中时的当日 is_public 报告集, 已按 created_at DESC 排序)

    返回:
      {
        "hit_report": AiReport | None,  # 命中报告(无则 None)
        "confidence": float,            # 命中置信度(无命中=最高那条的 confidence 或 0.0)
        "all_confidences": list[dict]   # 全部候选的 confidence(调试用)
      }

    失败处理: LLM 调用失败/JSON 解析失败 → 返回 {hit_report:None, confidence:0.0,
        all_confidences:[]}, 让 ai.py 走 L3 clarify 兜底, 不抛异常。
    """
    if not candidates:
        return {"hit_report": None, "confidence": 0.0, "all_confidences": []}
    cands = candidates[:5]
    cand_descs = []
    for i, r in enumerate(cands):
        ans = (r.answer_md or "").strip()
        snippet = ans[:200] + ("..." if len(ans) > 200 else "")
        q = (r.question or "").strip()[:80]
        cand_descs.append(f"[{i + 1}] report_id={r.id} question={q} snippet={snippet}")
    cand_block = "\n".join(cand_descs)
    user_prompt = (
        f"问句: {question}\n\n"
        f"候选报告(共 {len(cands)} 条):\n{cand_block}\n\n"
        "请判定问句与每个候选报告的语义相关性, 输出 JSON 数组, "
        "元素为 {\"index\": int, \"confidence\": float, \"reason\": str}。"
        "confidence∈[0,1], 1=完全相关, 0=完全无关。"
        "若问句与所有候选报告均无关(如闲聊、寒暄、问候、个人话题等), "
        "所有 confidence 必须输出 0。"
        "只输出 JSON, 不要其他文字。"
    )
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": user_prompt},
    ]
    try:
        result = await llm_client.chat_completion(
            messages, max_tokens=200, timeout_s=60.0,
            db=db, source="ai_semantic", provider_code=_SEMANTIC_PROVIDER_CODE,
        )
        text = str(result).strip()
        # 兼容 ```json 包裹的输出
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:].strip()
        arr = json.loads(text)
        if not isinstance(arr, list):
            raise ValueError("LLM 输出不是 JSON 数组")
        threshold = float(getattr(settings, "AI_SEMANTIC_MATCH_THRESHOLD",
                                  _DEFAULT_THRESHOLD) or _DEFAULT_THRESHOLD)
        all_confidences = []
        best_idx = -1
        best_conf = 0.0
        for item in arr:
            if not isinstance(item, dict):
                continue
            idx = int(item.get("index", 0)) - 1
            conf = float(item.get("confidence", 0.0))
            if 0 <= idx < len(cands):
                all_confidences.append({
                    "report_id": cands[idx].id,
                    "confidence": conf,
                    "reason": item.get("reason", ""),
                })
                if conf > best_conf:
                    best_conf = conf
                    best_idx = idx
        if best_idx >= 0 and best_conf >= threshold:
            return {"hit_report": cands[best_idx], "confidence": best_conf,
                    "all_confidences": all_confidences}
        return {"hit_report": None, "confidence": best_conf,
                "all_confidences": all_confidences}
    except Exception as e:  # noqa: BLE001  LLM 失败/JSON 解析失败 → 走 L3 兜底
        logger.warning("semantic_match LLM 调用/解析失败: %s", e)
        return {"hit_report": None, "confidence": 0.0, "all_confidences": []}
