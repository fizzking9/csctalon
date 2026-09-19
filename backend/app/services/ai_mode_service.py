"""AI 问句意图分级器 (工单 #152)

设计(A-20260919-001 §四): 回答按问句意图自动分级 brief/standard/deep。

判定优先级: **deep > standard > brief**(深度意图优先, 允许深度回答覆盖明细)。

- deep(深度): 命中深度意图词(为什么/为何/原因/对比/根因/建议/趋势/环比/同比/
  怎么回事/怎么变/深挖/深度/剖析/归因)→ 实时 27b 全量 feed
- standard(明细): 命中维度词(明细/分布/涉诉/挽回/四路/专项/笔数/金额/多少/几个/
  分析/详情/详细情况)→ 快照优先, 完整性校验缺维度补算
- 其余 → brief(简报): 泛问 → 快照摘取秒回

v1 实现取舍(架构文档 §4.2 的 LLM 兜底暂不接线):
- 规则对常见问句确定性强(毫秒级, 零 LLM); LLM 兜底会给最热的 brief 路径加 7b
  延迟, 且可能把泛问误判为 standard, 触发不必要的实时 27b(30-50s 劣化)。
- 规则的"漏"全部落到 brief(快照秒回保底), 维度由完整性校验器 + 补算器兜底,
  不会出"无答案"。

返回: {"mode": "brief|standard|deep", "source": "rule"}。
"""

import logging

logger = logging.getLogger(__name__)

# 深度意图词(命中 → deep; 覆盖"为什么/对比/根因/趋势/建议"五类深度问)
_DEEP_WORDS = (
    "为什么", "为何", "原因", "对比", "根因", "建议", "趋势",
    "环比", "同比", "怎么回事", "怎么变", "深挖", "深度", "剖析", "归因",
)

# 维度词(命中 → standard; 覆盖退款四路/涉诉/挽回、投诉、接待等维度细问)
_STANDARD_WORDS = (
    "明细", "分布", "涉诉", "挽回", "四路", "专项",
    "笔数", "金额", "多少", "几个", "分析", "详情", "详细情况",
)


def classify_question(question: str) -> dict:
    """问句意图分级: brief / standard / deep(纯规则, 毫秒级, 零 LLM)。

    deep > standard > brief: 问句同时含深度词与维度词时取 deep
    (如「为什么昨天退款四路这么少, 明细呢」→ 含"为什么"+"明细" → deep)。

    返回 {"mode": "brief|standard|deep", "source": "rule"}。
    """
    q = (question or "").strip()
    if not q:
        return {"mode": "brief", "source": "rule"}
    ql = q.lower()
    for w in _DEEP_WORDS:
        if w in ql:
            return {"mode": "deep", "source": "rule"}
    for w in _STANDARD_WORDS:
        if w in ql:
            return {"mode": "standard", "source": "rule"}
    return {"mode": "brief", "source": "rule"}
