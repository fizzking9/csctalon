"""AI 报告语义匹配层 (工单 #135 批次 B2)

设计(架构文档 A-20260917-001 LOCKED §四):
- semantic_match(db, question, candidates) -> dict: 判定问句与候选报告整体相关性
- confidence≥0.6 视为命中 → 走摘取层; 否则走 L3 clarify 兜底
- 候选取前 5 条(避免 LLM 输入过长), 每条 question + answer_md 前 200 字摘要

LLM 调用约束:
- provider_code=llm_tiers.resolve_tier("assistant") —— 能力档位链（首选→备选→云端兜底）
  ⚠️ 2026-09-20 档位内首选由 qwen2.5:7b 改为 qwen3-coder:30b
     （实测 7b 在本任务 5/5 全错，见文件末尾常量注释）
- timeout_s=60(前台实时问答用户在等)
- max_tokens=400(输出 JSON 数组; 原 200 实测被截, 见调用点注释)
- LLM 输出 JSON: [{index, confidence, reason}], confidence∈[0,1]
  ⚠️ index 基准模型间不一致(0-based / 1-based)，解析层已自适应兼容

失败兜底: LLM 异常/JSON 解析失败 → 返回 {hit_report:None, confidence:0.0},
让 ai.py 走 L3 clarify 兜底, 不抛异常。
"""

import json
import logging

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.ai_report import AiReport
from app.services import llm_client, llm_tiers

logger = logging.getLogger(__name__)

# ⚠️ 2026-09-20 修正（实测推翻 #145 的选型）——见 llm_tiers 档位声明
#   #145 原选 7b 的理由是"封闭分类任务, 7b 足够"，但**实测证伪**:
#   用生产真实 prompt 格式复现 5 轮，qwen2.5:7b **5/5 全部返回 confidence=0.0**
#   （理由自相矛盾: "候选报告均未涉及退款原因" —— 而摘要里明写「退款原因以网络故障为主」）
#   → 生产逻辑取 max(confidence) >= 0.6 判定命中，故 **语义匹配从未命中过**，
#     用户每次都走 clarify 兜底，看不到报告。
#
#   实测对比（生产真实 prompt, 多轮）:
#     qwen2.5:7b       ❌ 5/5 全错（恒 0.00）
#     qwen3-coder:30b  ✅ 6/6 全对（命中 0.8~0.9 / 闲聊 0.0），且快 3x（1.4s vs 6s）
#     qwen3.8:27b      ✅ 5/5 全对（0.95，最稳）
#
#   2026-09-21: 不再硬编码 provider_code，改走**能力档位**（llm_tiers）。
#   语义匹配属「速度优先、非数值」任务 → 档位 = assistant（助理）。
#   档位内部已声明首选/备选/云端兜底，调整选型只需改 llm_tiers.py。
_TIER = "assistant"

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
      3. 调 27b(provider_code=None, timeout_s=60, max_tokens=400)输出 JSON 数组
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
            # ⚠️ 2026-09-21 由 200 调到 400。
            #    原注释假设「只输出 JSON 数组, 短」，但实测被截在**恰好 200**
            #    （done_reason='length'），说明候选条目多时数组会超。
            #    被截的后果是**静默的**: 返回的 JSON 可能是数组被砍掉尾巴，
            #    解析后少几个语义匹配结果, 调用方看不出异常。
            #    400 是实测输出的 2 倍余量（被截样本 out_chars 400~466）。
            messages, max_tokens=400, timeout_s=60.0,
            db=db, source="ai_semantic",
            # 传 db → 优先用界面配置的角色成员；DB 无配置时回退源码常量
            provider_code=llm_tiers.resolve_tier(_TIER, db=db),
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
        # ── index 基准兼容（2026-09-20 修复 off-by-one）──
        # prompt 用 "[1]" 起编号，但实测模型返回基准不一致：
        #   qwen2.5:7b    → 0-based（index 从 0 开始）
        #   qwen3-coder:30b / qwen3.8:27b → 1-based（index 从 1 开始）
        # 原实现固定 "-1"，导致 0-based 输出的 index=0 变成 -1 被静默丢弃
        # （all_confidences 为空、永不命中）。此处按数组内实际基准自适应。
        _has_zero_idx = any(
            isinstance(it, dict) and int(it.get("index", -1)) == 0 for it in arr
        )

        def _to_zero_based(raw) -> int:
            """把模型输出的 index 归一到 0-based。"""
            i = int(raw)
            return i if _has_zero_idx else i - 1

        all_confidences = []
        best_idx = -1
        best_conf = 0.0
        for item in arr:
            if not isinstance(item, dict):
                continue
            idx = _to_zero_based(item.get("index", 0))
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
