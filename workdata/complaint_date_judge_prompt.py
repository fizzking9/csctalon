#!/usr/bin/env python3
"""小模型提示词：投诉日期语义判定（#195）。

给 qwen2.5:3b/7b 用，判断文本中是否包含可可靠定位的投诉日期。
输出严格 JSON，禁止编造。
"""

DATE_JUDGE_PROMPT = """你是数据清洗助手。判断给定的投诉文本中是否包含可可靠定位的投诉/诉求发起日期。

## 规则
1. 只输出 JSON：{"has_date": true/false, "date": "YYYY-MM-DD" 或 null, "confidence": "high/medium/low"}
2. 如果文本只有购买/充值/到期日，没有投诉发起日 → has_date=false
3. 如果文本明确提到「投诉」「举报」「要求退款」等动作且邻近有日期 → 输出该日期
4. 如果文本有「今天X月X日」「截止X月X日」等叙述当天词 → 输出该日期
5. 如果多个候选日期，选最可能是投诉发起日的（通常是最早的、与投诉动作邻近的）
6. 如果完全无法确定 → has_date=false，禁止编造
7. 年份：文本有明确年份就用；否则根据上下文推断（如「今年」指 2024 或 2026）

## 候选日期（规则引擎已提取）
{candidates}

## 上下文信息
- 上一行投诉日期：{prev_date}
- 下一行投诉日期：{next_date}
- 当前手机号：{phone}
- 是否重复投诉：{is_repeat}

## 投诉文本
{text}

请输出 JSON："""


def build_judge_prompt(text: str, candidates: list[str],
                       prev_date: str | None = None,
                       next_date: str | None = None,
                       phone: str = "",
                       is_repeat: bool = False) -> str:
    """构建小模型提示词。"""
    return DATE_JUDGE_PROMPT.format(
        text=text[:2000],  # 截断保护
        candidates="\n".join(f"- {c}" for c in candidates) if candidates else "无",
        prev_date=prev_date or "无",
        next_date=next_date or "无",
        phone=phone or "未知",
        is_repeat="是" if is_repeat else "否",
    )


# 版本化信息（用于 governance_prompts 表）
PROMPT_VERSION = {
    "code": "complaint_date_judge_v1",
    "version": "1.0.0",
    "change_reason": "初始版本：从 4090 决策逻辑沉淀，支持上下文感知日期判定",
    "created_by": "dev:coder-30b",
}
