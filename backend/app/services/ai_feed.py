"""AI 分析助手 - 数据底座 feed 组装 + Prompt 构造 (A-20260904-003 LOCKED)

- 数据源: **内部 service 直连聚合**, 与 external /brief 同一底层函数/同一 Redis 缓存
  (external._build_brief 仅纯组装 + cached_call, 无鉴权/限频副作用), 登录用户走 ManagerUser。
- 口径进上下文: Business Glossary(ai_glossary) 分层注入, 不整篇灌规划文本。
- Prompt 三层: System 铁律 / Business Context 口径卡 / Data feed + User Query。
"""

import json
import logging

from sqlalchemy.orm import Session

from app.api.v1.external import _build_brief
from app.core.config import settings
from app.services.ai_glossary import glossary_all, glossary_for_question

logger = logging.getLogger(__name__)

# System 铁律(固定, 防幻觉/防越权)
_SYSTEM_PROMPT = (
    "你是本客服/经营系统的数据分析助手。你的职责是基于【给定数据】回答经营/退款/投诉/接待问题。\n"
    "铁律:\n"
    "1. 所有数字必须来自【给定数据】, 严禁编造、外推或臆测; 数据中没有的写\"暂无/未提供\"。\n"
    "2. 严格按【业务口径】理解指标, 不要自创定义。\n"
    "3. 回答中文, 结构清晰(可用标题/要点/小表格), 结论注明数据窗口(如 今日/本周/本月 截至时间)。\n"
    "4. 只回答与经营数据相关的问题; 无关问题礼貌说明无法回答。\n"
    "5. 金额一律以元为单位。"
)

# 预设快捷触发句(前端展示用; 与 GLOSSARY_ROUTE 关键词对应)
QUICK_QUESTIONS = [
    "今日退款情况如何？四路分布和涉诉怎么样？",
    "昨日投诉分析：三源登记/处理/积压如何？",
    "本周接待量和各专项会话设备情况怎么样？",
    "今日经营总览：接待、退款、投诉一起说说。",
]


def _build_feed(db: Session, period: str, date: str | None) -> dict:
    """组装 feed(与 external /brief 同底层同缓存)。

    - period: daily/weekly/monthly
    - date:   period=daily 时可指定某整天(缺省=今天实时)
    返回 brief 结构(纯只读, 命中 today:kpi/refund:do/complaint:* 既有缓存)。
    """
    return _build_brief(db, period, date)


def _trim_feed(feed: dict) -> str:
    """feed → JSON 文本, 并按 AI_ANALYZE_MAX_CHARS 裁剪(防 token 超限)。"""
    raw = json.dumps(feed, ensure_ascii=False, default=str)
    limit = max(1000, int(getattr(settings, "AI_ANALYZE_MAX_CHARS", 6000) or 6000))
    if len(raw) <= limit:
        return raw
    # 截断并尽量保留 _spec(口径) — 截断到限长, 尾部标注省略
    return raw[:limit] + "\n...[数据已按长度截断]"


def build_messages(db: Session, period: str, date: str | None, question: str) -> list[dict]:
    """构造发给模型的 messages(三层 Prompt)。"""
    feed = _build_feed(db, period, date)
    feed_json = _trim_feed(feed)
    # 口径卡: 按问句路由选子集; 未命中全量
    glossary = glossary_for_question(question)
    business_ctx = (
        "【业务口径(必须遵守)】\n" + (glossary or glossary_all()) +
        "\n\n【数据窗口说明】" +
        f" period={period}; date_start={feed.get('date_start')}; date_end={feed.get('date_end')}; " +
        f" generated_at={feed.get('generated_at')}"
    )
    data_ctx = "【给定数据(JSON, 金额单位=元)】\n" + feed_json
    system = _SYSTEM_PROMPT
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"{business_ctx}\n\n{data_ctx}\n\n请回答以下问题：{question}"},
    ]


def available_scope_options() -> list[dict]:
    """GET /ai/config 返回可用 period 与快捷问(内部; ManagerUser)。"""
    return [
        {"period": "daily", "label": "当日"},
        {"period": "weekly", "label": "本周(周一起)"},
        {"period": "monthly", "label": "本月"},
        {"quick_questions": QUICK_QUESTIONS},
    ]
