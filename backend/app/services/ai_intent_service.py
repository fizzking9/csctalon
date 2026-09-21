"""问句意图识别 (统一入口, 工单 #182)

## 为什么要有这个模块 (2026-09-21)

此前「理解用户问什么」这件事**散在 5 处**, 各自用字面包含匹配, 且互不通信:

    1. ai_time_parser               → 时间窗口
    2. ai_focus_service._KEYWORD_TO_SCOPE  → 内容域 (3 域)
    3. ai_report_matcher._question_matches_keywords → 模板命中
    4. ai_mode_service.classify_question   → 粒度 (brief/standard/deep)
    5. ai.py._has_deep_req_intent          → 事后悔改 ("太简单"/"再详细")

后果 (实测):

  · **粒度概念双重定义**: 模板侧有 `detail_level`/`format` 字段,
    问句侧有 `_DEEP_WORDS`/`_STANDARD_WORDS` —— 两套词表语义重叠但不一致,
    改一处不影响另一处。

  · **实时性判定倒置**: 是否走 30~50 秒实时路径, 取决于「模板命中与否」,
    而不取决于「问句是否需要实时」。措辞稍有差异导致未命中 → 直接掉进实时,
    中间没有「可否用快照回答」的判断。

  · **内容域覆盖窄**: 只有 refund/complaint/reception; 无「全量经营」,
    无「个人绩效」(而看板里两者都有数据)。

  · **关键词脆弱**: "客诉" 匹配不上 "投诉"。

本模块把上述维度**一次算出来**, 供调用方决策; 各旧模块保留 (避免破坏既有路径),
但新增判断一律走这里, 使「同一概念只有一处定义」。

## 设计取舍

**纯规则, 不调 LLM**: 识别发生在**每次**问答的入口 (最热路径),
调 LLM 会让最热的 brief 路径多 1~7 秒。规则漏判的兜底在**下游**:
域漏判 → feed 带全量段; 粒度漏判 → brief (快照秒回保底);
freshness 漏判 → 按保守值 (不额外实时化)。
即「漏判的代价是可接受的慢/全, 而非答不出」。

**不做语义推断**: 只认显式词表。宁可漏, 不可误 —— 误判会走错路径
且用户无从察觉 (如把"今天"误判为可复用昨天的快照)。
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# 维度 2: 内容域
# ─────────────────────────────────────────────────────────────────────
# 值: overview(全量经营) / refund(退款) / complaint(投诉) /
#     reception(接待) / personal(个人绩效)
#
# ⚠️ 同义词表必须覆盖**真实业务用词**。实测踩过: 只写"投诉"时
#    "客诉"匹配不上 → 域集合为空 → feed 不带投诉段 → AI 答"未提供"。
#    词表来源: 与 ai_focus_service._KEYWORD_TO_SCOPE、模板 question_keywords 对齐。
_DOMAIN_WORDS: dict = {
    "refund": (
        "退款", "退费", "退单", "提现", "涉诉", "退款率", "挽回",
        "四路", "退费原因", "退款原因", "退款意愿",
    ),
    "complaint": (
        "投诉", "客诉", "工单", "催单", "积压", "登记", "结案",
        "外部投诉", "内部投诉", "卡商群", "商户号", "投诉率",
    ),
    "reception": (
        "接待", "会话", "咨询", "来电", "话务", "接待量", "接待人",
        "服务量", "响应量",
    ),
    "personal": (
        "绩效", "排名", "排行", "考核", "工作量", "效率", "成绩",
        "个人", "本人", "我的", "谁最", "top", "TOPK", "达人",
        "人均", "客服排名", "专员排名",
        # ⚠️ 2026-09-21 补: 实测漏判「谁接待**最多**」——
        #    原表只有"谁最"(要求紧邻), 而"最多/最高/第一"是并列的常见说法。
        #    教训同 overview: 词表要覆盖**同类说法的各种变体**, 不能只写一种。
        "最多", "最少", "最高", "最低", "第一", "最好", "最差", "领先",
    ),
    "overview": (
        "整体", "总览", "全量", "大盘", "经营", "汇总", "综合",
        "晨会", "日报", "周报", "月报", "总况",
        # ⚠️ 2026-09-21 补: 实测漏判「本周专项接待**总计**」——
        #    原词表只有"整体/总览/全量", 缺"总计/合计/一共"这类**口语化的全量表达**。
        #    教训: 词表要按**用户实际说法**收集, 不能按设计者的书面语收集。
        "总计", "合计", "总共", "一共", "全部", "全部门", "整体情况",
    ),
}

# ─────────────────────────────────────────────────────────────────────
# 维度 3: 粒度 (brief 简报 / standard 明细 / deep 深度分析)
# ─────────────────────────────────────────────────────────────────────
# ⚠️ 与 `ai_report_templates.detail_level`(standard/deep) 的映射关系:
#      detail_level=standard → 本层 standard
#      detail_level=deep + format=detail → 本层 deep
#   即两个字段共同决定粒度, 而非各自独立 —— 消除"双重定义"。
#
# 判定优先级: deep > standard > brief (深度意图优先, 允许覆盖明细)
_GRANULARITY_WORDS: dict = {
    "deep": (
        "为什么", "为何", "原因", "根因", "归因", "对比", "环比", "同比",
        "趋势", "走势", "建议", "怎么回事", "怎么变", "深挖", "深度",
        "剖析", "分析下", "解读", "洞察", "影响", "关联",
    ),
    "standard": (
        "明细", "详情", "详细", "分布", "构成", "占比", "列表", "逐条",
        "笔数", "金额", "具体", "哪些", "展开",
        # ⚠️ 2026-09-21 移除 "多少"/"几个": 实测「接待量**多少**」被判 standard,
        #    但它只是疑问语气, **不表示要明细** —— 用户问总量也是这么问的。
        #    教训: 区分「疑问词」与「粒度词」。疑问词（多少/怎么/什么）到处都是,
        #    拿来判粒度会把绝大多数问句误判为 standard, 进而触发不必要的
        #    完整性校验与补算（慢而无益）。
        #    只有**明确的展开诉求**（明细/逐条/列表/展开）才算 standard。
    ),
}

# ─────────────────────────────────────────────────────────────────────
# 维度 4: 数据新鲜度 (能否复用快照 / 必须实时)
# ─────────────────────────────────────────────────────────────────────
# 语义: 这是**事前**判定 —— 在尝试匹配模板**之前**就该知道
#       「这个问句能不能用快照回答」, 而不是等匹配失败才掉进实时。
#
# 判定依据 (按优先级):
#   1. 显式要求实时 ("最新"/"现在"/"实时"/"马上") → needs_live
#   2. 窗口含今天 且 非概览域 → 可能需实时 (数据仍在变)
#   3. 窗口已结束 (昨天及更早) → snapshot_ok (数据已定, 快照即权威)
#   4. 概览域 + 任何窗口 → snapshot_ok (预生成日报/周报/月报覆盖)
_NEEDS_LIVE_WORDS = (
    "最新", "现在", "实时", "马上", "当前", "此刻", "刚刚",
    "截止现在", "截至目前", "此刻的",
)
_SNAPSHOT_OK_HINT_WORDS = (
    "昨天", "昨日", "前天", "上周", "上个月", "上月",
    "本周", "这周", "本月", "这个月", "这月",
)


@dataclass
class Intent:
    """一次问句的完整意图 (四个维度)"""

    question: str

    # ── 维度 2: 内容域 ──
    # 可能多域 (如"接待和退款一起说"); 空集表示未识别出具体域
    domains: set = field(default_factory=set)

    # ── 维度 3: 粒度 ──
    granularity: str = "brief"          # brief | standard | deep

    # ── 维度 4: 新鲜度 ──
    freshness: str = "snapshot_ok"      # snapshot_ok | needs_live
    freshness_reason: str = ""          # 判定理由 (可观测, 便于排查误判)

    # ── 维度 1: 时间窗口 ──
    # 不在本模块计算 —— 复用既有 `ai_time_parser` (已完善: 正则+LLM双解析、
    # 显式参数优先、防窗口错位)。此处只标记「问句是否显式提到时间」,
    # 供调用方决定是否需要触发时间解析。
    mentions_time: bool = False

    # ── 元信息 ──
    source: str = "rule"                # 识别方式 (当前恒 rule)
    unmatched_hint: bool = False        # True = 无任何维度命中 (可能需 clarify)

    @property
    def is_personal(self) -> bool:
        """是否个人绩效类问句"""
        return "personal" in self.domains

    @property
    def is_specialty(self) -> bool:
        """是否专项类问句 (接待专项/退款专项等)"""
        return "reception" in self.domains or "专项" in self.question

    def describe(self) -> dict:
        """对外描述 (审计/日志用)"""
        return {
            "domains": sorted(self.domains),
            "granularity": self.granularity,
            "freshness": self.freshness,
            "mentions_time": self.mentions_time,
            "source": self.source,
        }


def _hit(text: str, words) -> Optional[str]:
    """返回首个命中的词 (便于排查「为什么判成这个域」), 未命中 None"""
    for w in words:
        if w in text:
            return w
    return None


def classify(question: str) -> Intent:
    """识别问句意图 (纯规则, 毫秒级, 零 LLM)

    调用方典型用法:

        it = intent.classify(q)
        if it.freshness == "needs_live":
            ...  # 直接走实时, 不必先试模板匹配
        else:
            ...  # 先试快照, 未命中再降级
    """
    q = (question or "").strip()
    it = Intent(question=q)
    if not q:
        it.unmatched_hint = True
        return it

    ql = q.lower()

    # ── 维度 2: 内容域 (多域) ──
    for dom, words in _DOMAIN_WORDS.items():
        if _hit(ql, words):
            it.domains.add(dom)
    # 个人绩效可能同时命中 reception(如"接待排名") → 保留多域,
    # 由调用方决定优先级: personal 比 reception 更具体
    if not it.domains:
        it.unmatched_hint = True

    # ── 维度 3: 粒度 (deep > standard > brief) ──
    for gran in ("deep", "standard"):
        if _hit(ql, _GRANULARITY_WORDS[gran]):
            it.granularity = gran
            break

    # ── 维度 4: 新鲜度 ──
    it.freshness, it.freshness_reason = _decide_freshness(ql, it.domains)

    # ── 维度 1: 是否提到时间 (实际窗口交给 ai_time_parser) ──
    it.mentions_time = _mentions_time(ql)

    return it


def _decide_freshness(ql: str, domains: set) -> tuple:
    """判定数据新鲜度, 返回 (freshness, reason)

    ⚠️ 核心修正: 让「是否需要实时」由**问句特征**决定,
       而非由「模板命中与否」决定 (后者是把结果当依据, 因果倒置)。
    """
    # 1. 显式要求实时 —— 用户明确说了, 必须实时
    w = _hit(ql, _NEEDS_LIVE_WORDS)
    if w:
        return "needs_live", f"显式要求实时(命中「{w}」)"

    # 2. 个人绩效: 看板数据实时性强 (人员排名随时在变), 且无预生成模板
    if "personal" in domains:
        return "needs_live", "个人绩效无预生成模板, 需实时聚合"

    # 3. 已结束的窗口 → 数据已定, 快照即权威
    w = _hit(ql, _SNAPSHOT_OK_HINT_WORDS)
    if w:
        return "snapshot_ok", f"窗口已结束(命中「{w}」), 快照即权威"

    # 4. 概览域: 预生成日报/周报/月报覆盖, 快照可答
    if "overview" in domains:
        return "snapshot_ok", "概览域有预生成报告覆盖"

    # 5. 兜底: 保守取 snapshot_ok
    #    理由: 快照路径未命中时会**自动降级**到实时 (既有链路已如此),
    #    而反过来 (默认实时) 会让所有问句都慢 30~50 秒。
    #    ⚠️ 但「今天的专项明细」这类确实需要实时 —— 故第 2 条单独处理个人绩效。
    return "snapshot_ok", "默认取快照(未命中时会自动降级到实时)"


# 时间提及词 (只判断"有没有提", 不解析具体值)
_TIME_WORDS = (
    "今天", "今日", "昨天", "昨日", "前天", "明天",
    "本周", "这周", "上周", "下周", "本月", "这个月", "上月", "下月",
    "今年", "去年", "近7天", "近30天", "近一周", "近一月",
    "上午", "下午", "晚上", "凌晨", "早上",
)
_TIME_RANGE_RE = re.compile(r"\d{1,2}\s*月\s*\d{1,2}\s*[日号]|\d{4}-\d{2}-\d{2}")


def _mentions_time(ql: str) -> bool:
    """问句是否显式提到时间 (只判断有无, 不解析值)"""
    if _hit(ql, _TIME_WORDS):
        return True
    return bool(_TIME_RANGE_RE.search(ql))


def domain_scopes(intent: Intent) -> set:
    """把内容域映射为 feed 段名 (供 ai_focus_service 复用)

    `overview` 不产生具体段 —— 它表示"全都要", 由调用方保留全部段。
    """
    m = {"refund": "refund", "complaint": "complaint", "reception": "reception"}
    return {m[d] for d in intent.domains if d in m}
