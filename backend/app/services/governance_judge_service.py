"""AI 数据治理框架 · 研判层 (工单 #164, 设计: docs/design/AI数据治理框架-20260919.md §五)

把探测证据（findings）读给「AI 治理分析师」，区分「真问题 vs 业务允许」，
产出结构化治理建议；结论经人工确认后**沉淀为规则**，下次同类 finding 直接命中。

═══════════════════════════════════════════════════════════════════════
五条要点（与设计 §五 一一对应，改动前务必读懂）
═══════════════════════════════════════════════════════════════════════
① **规则短路**（§5.1/§5.4）：研判前先查 `governance_rules`，命中则**不调 LLM**
   —— 直接按规则回填 verdict/建议并记 `hit_count`。省调用，且保证同一问题判定一致。
② **批量研判**（§5.1）：一次 LLM 调用打包多条 open finding（探测器已脱敏样本），
   不逐条调。
③ **结构化输出 + 校验 + 重试**（§5.2）：只许输出 JSON（verdict/reason/suggestion/
   confidence），schema 不过**整体重试 1 次**；两次仍非法 → 这批保持 `open`（**不落脏数据**），
   下轮重试。
④ **铁律双守**（§5.3）：不可逆动作必须 `requires_human_confirm=true`（L2 红线）、
   「可空列多个 NULL 通常是业务允许」、拿不准给 `uncertain` + 低 confidence。
   提示词里写 + `validate_resp` 里查，两道闸。
⑤ **规则沉淀**（§5.4）：人工 dismiss（判定业务允许）→ 写 `governance_rules`，
   下次同 (detector, 表, target) 命中规则、**不再问 AI**。

留痕（设计 §3.3，category 恒为 `ai_governance`）：
  · `governance_judge`      —— 每次研判（含规则短路命中数 / provider / 耗时）
  · `governance_rule_sink`  —— 人工 dismiss 沉淀规则

调度（§5.3）：复用 `llm_scheduler` L1（admin 触发；不占用户实时问答 L2），超时 60s。
"""

import asyncio
import datetime as _dt
import fnmatch
import json
import logging
import re
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.governance_finding import (
    DETECTOR_FILLABLE_GAP,
    DETECTOR_IMPOSSIBLE_DATE,
    DETECTOR_TEXT_ANOMALY,
    FINDING_STATUS_IGNORED,
    FINDING_STATUS_JUDGED,
    FINDING_STATUS_OPEN,
    VERDICT_ALLOWED_BY_BUSINESS,
    VERDICT_REAL_ISSUE,
    VERDICT_UNCERTAIN,
    GovernanceFinding,
)
from app.models.governance_prompt import GovernancePrompt
from app.models.governance_rule import (
    AUTO_LEVEL_IRREVERSIBLE,
    AUTO_LEVEL_READONLY,
    DISPOSITION_LEVELS,
    DISPOSITION_MARK_DUP,
    DISPOSITION_MARK_IGNORED,
    DISPOSITION_REPORT_ONLY,
    GovernanceRule,
)
from app.models.llm_provider import LlmCallLog
from app.services import llm_client
from app.services.system_event_service import record_system_event, update_system_event

logger = logging.getLogger(__name__)

# ── 研判参数（设计 §5.3）──────────────────────────────────────────────
# ⚠️ 超时与批大小按**生产实测**定（#169，2026-09-20）：
#   单条 finding 研判 ≈ 22~25s（provider=talon-gmk-qwen3.8-27b）；2 条 ≈ 55s（已贴着 60s 超时）；
#   10 条批实测 **267s 全部 ReadTimeout** → 该批保持 open，且下一轮仍是同样 10 条 →
#   **研判永不收敛（livelock）**。故：超时放宽到 120s、批降到 3（≈75s，留足余量）。
#   副作用：批小了 → LLM 调用次数变多（每条成本不变、单次调用固定开销增加）；
#   这是「能研判」与「更省调用」之间的取舍，宁可多几次调用，不可永远研判不了。
_TIMEOUT_S = 120.0           # 单次 LLM 调用超时
_MAX_TOKENS = 3000           # 结构化 JSON 输出 token 上限
_LLM_SOURCE = "governance_judge"  # chat_completion 审计 source + 留痕溯源
_LLM_RETRY = 1               # 非法输出整体重试次数（设计 §5.2）
_LLM_LEVEL = 1               # llm_scheduler L1（admin 触发，不占 L2 实时问答）
_BATCH_SIZE = 3              # 单次批量研判的 finding 上限（防超长上下文；见上方实测）
_REPRO_SQL_CHARS = 400       # 复现 SQL 注入长度上限（够看懂索引语义即可）

# system_events 留痕（category 恒为 ai_governance）
EVENT_CATEGORY = "ai_governance"
EVENT_JUDGE = "governance_judge"          # 每次研判
EVENT_RULE_SINK = "governance_rule_sink"  # 人工 dismiss 沉淀规则

# 合法枚举（schema 校验用；与 governance_rule.DISPOSITION_LEVELS **派生**同一来源）
_VALID_VERDICTS = frozenset({VERDICT_REAL_ISSUE, VERDICT_ALLOWED_BY_BUSINESS, VERDICT_UNCERTAIN})
# 动作白名单 = 分级表的键（不另写一份：新增动作时若忘了同步，会静默变成「AI 给的合法动作被拒」）
_VALID_ACTIONS = frozenset(DISPOSITION_LEVELS.keys())
_VALID_RISKS = frozenset({"low", "medium", "high"})
# 不可逆动作（L2 红线：必须人工确认，见设计 §六）——同样从分级表派生
# （#169 教训：原先手写 {delete_dups, create_index} 漏掉了 fix_time，
#   会让 AI 的 fix_time 建议绕过「必须 requires_human_confirm」的 schema 闸门）
_IRREVERSIBLE_ACTIONS = frozenset(
    a for a, level in DISPOSITION_LEVELS.items() if level == AUTO_LEVEL_IRREVERSIBLE
)

# 输出 JSON 结构说明（注入系统提示词，与 validate_resp 一一对应）
_OUTPUT_SCHEMA = """{
  "judgments": [
    {
      "finding_id": 1,
      "verdict": "real_issue | allowed_by_business | uncertain",
      "reason": "判据（须引用规范条款或表说明）",
      "suggestion": {
        "action": "report_only | mark_ignored | mark_dup_residual | mark_pending | delete_dups | create_index | fix_time",
        "params": {},
        "impact": "影响面（涉及多少行/哪些统计）",
        "risk": "low | medium | high",
        "requires_human_confirm": true
      },
      "confidence": 0.0
    }
  ]
}"""

# 模块级默认提示词（DB 行取不到/被禁用时的兜底；与迁移 seed 内容镜像）
_DEFAULT_SYSTEM_PROMPT = (
    "你是「数据治理分析师」。系统会给你一批由**确定性探测器**算出的「结构性可疑」"
    "（findings：含表名、目标列/索引、量化证据 metrics、脱敏样本行、复现 SQL），"
    "请逐条判断「这是真的数据问题，还是业务允许的正常现象」，并给出治理建议。\n"
    "\n【项目规范原文（判据必须引用）】\n{spec_rules}\n"
    "\n【输出 JSON 结构】\n{output_schema}\n"
    "\n【铁律】\n"
    "1. 先分「业务允许 vs 数据错误」：PostgreSQL 普通唯一索引按 = 比较，NULL≠NULL —— "
    "可空列上出现多个 NULL **通常是业务允许**（如实时问答没有 task_id、草稿未分配 code），"
    "不要把「多个 NULL」当重复；只有组内**非 NULL 列**已足以判定同一实体、索引却拦不住时，"
    "才是真缺口（保护缺口）。\n"
    "2. 不可逆动作（删行 / 改值 / 建索引 / 改表结构）必须 requires_human_confirm=true（红线，AI 永不自决）。\n"
    "3. 判据必须引用规范条款或表说明；禁止编造业务结论（如「这是对账基准」）。\n"
    "4. 拿不准给 uncertain 且 confidence<0.7，不要硬判。\n"
    "5. 每条 finding 必须给出且只给出一条 judgment，finding_id 原样回填，禁止新增、合并或遗漏。\n"
    "6. 判为 `allowed_by_business`（业务允许）时，**禁止**建议 `mark_ignored` / `mark_dup_residual`\n"
    "   —— 那会把行**排除出经营统计**、改变口径（红线）。业务允许应给 `report_only`，\n"
    "   由人工走 dismiss（判定业务允许 + 沉淀规则，数据一行不动）。\n"
    "   需要「标出来给人核对、但不改口径」时用 `mark_pending`（行**仍计入**经营统计）。\n"
    "7. `fix_time`（改日期值）属**不可逆**动作：必须 requires_human_confirm=true，且**只在上下文\n"
    "   足以推断正确值**时才给，并在 `params.updates` 写明 `[{{pk, col, old, new}}]`（pk 取样本的\n"
    "   `_id`，old 原样回填库中现值）。依据不足一律 `uncertain` + confidence<0.7，**禁止编造**\n"
    "   —— 改错值比不改更糟（人工确认时会核对 old 是否与库中一致，不一致直接拒绝执行）。\n"
    "   ⚠️ 本串会被 `str.format` 渲染：提示词里的**字面花括号必须成对写**（两个左花括号表示一个），\n"
    "      写成单只会让 format 抛错、`_safe_format` 静默退回**未替换**的模板，\n"
    "      导致 AI 根本拿不到判据段 —— 2026-09-20 E2 实测踩过。"
)
_DEFAULT_USER_PROMPT = (
    "【待研判的结构性可疑（共 {count} 条）】\n{findings}\n"
    "\n【表说明（data_sources.description，判定「业务允许与否」的依据）】\n{table_notes}\n"
    "\n请按系统提示词要求，只输出 JSON，禁止任何解释文字或 markdown 代码块标记。"
)


class JudgeError(Exception):
    """研判失败（输入非法 / finding 不存在 / LLM 两次仍非法）。"""


def _now_utc() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


# ═══════════════════════════════════════════════════════════════════════
# 一、规则匹配（要点①：命中即短路，不调 LLM）
# ═══════════════════════════════════════════════════════════════════════
def _target_match(pattern: str, target: str) -> bool:
    """target_pattern 匹配 target：`*`/`?` 走 glob 通配（如 uq_wallet_*），否则精确。

    （设计 §3.2 写「正则/精确」；实现取更保守的 glob —— 规则由人工/AI 沉淀，
    正则元字符（如 `.` `(`）在索引名里常见，按正则解释会静默匹配错目标。）
    """
    if not pattern or pattern == "*":
        return True
    if "*" in pattern or "?" in pattern:
        return fnmatch.fnmatchcase(target, pattern)
    return pattern == target


def _metrics_match(cond: dict | None, metrics: dict | None) -> bool:
    """match_metrics 判定（如 {"ratio": {"gt": 0.5}}）；NULL/空 = 不限制。

    字段缺失或非数值 → 不命中（缺判据不瞎配，宁可回落到 AI 研判）。
    """
    if not cond:
        return True
    metrics = metrics or {}
    for field, expect in cond.items():
        actual = metrics.get(field)
        if not isinstance(expect, dict):
            if actual != expect:
                return False
            continue
        for op, ref in expect.items():
            if not isinstance(actual, (int, float)) or isinstance(actual, bool):
                return False
            if not isinstance(ref, (int, float)):
                return False
            if op == "gt" and not actual > ref:
                return False
            if op == "gte" and not actual >= ref:
                return False
            if op == "lt" and not actual < ref:
                return False
            if op == "lte" and not actual <= ref:
                return False
            if op == "eq" and not actual == ref:
                return False
            if op == "ne" and actual == ref:
                return False
    return True


def match_rule(rule: GovernanceRule, finding: GovernanceFinding) -> bool:
    """单条规则是否命中某 finding（detector + 表 + target + 可选 metrics 四段全过才算命中）。"""
    if rule.detector != finding.detector:
        return False
    if rule.table_name not in ("*", finding.table_name):
        return False
    if not _target_match(rule.target_pattern, finding.target):
        return False
    return _metrics_match(rule.match_metrics, finding.metrics)


def find_matching_rule(db: Session, finding: GovernanceFinding) -> GovernanceRule | None:
    """取命中的启用规则（多条命中时取**最具体**的一条：表名精确 > 通配；条件多者优先）。"""
    candidates = db.query(GovernanceRule).filter(
        GovernanceRule.enabled.is_(True),
        GovernanceRule.detector == finding.detector,
    ).all()
    best, best_score = None, -1
    for rule in candidates:
        if not match_rule(rule, finding):
            continue
        score = (2 if rule.table_name == finding.table_name else 0) + len(rule.match_metrics or {})
        if score > best_score:
            best, best_score = rule, score
    return best


def _rule_suggestion(rule: GovernanceRule) -> dict:
    """把规则转成 finding.suggestion（形态与 AI 建议一致，便于执行层统一处理）。"""
    return {
        "action": rule.disposition or DISPOSITION_REPORT_ONLY,
        "params": rule.disposition_params or {},
        "impact": f"由规则 #{rule.id} 判定（{rule.reason}）",
        "risk": "high" if rule.disposition in _IRREVERSIBLE_ACTIONS else "low",
        "requires_human_confirm": rule.disposition in _IRREVERSIBLE_ACTIONS,
    }


# ═══════════════════════════════════════════════════════════════════════
# 二、提示词与上下文（要点③④）
# ═══════════════════════════════════════════════════════════════════════
def build_spec_rules(detectors: set[str] | None = None) -> str:
    """规范原文注入段（**由代码常量生成**，不在提示词里存第二份 META 定义）。

    与 A-20260910-004「菜单/口径两份定义必然漂移」同一条纪律：
    规范原文的单源是 docs/standards/ 下文档，此处只给判据要点 + 文档指针。

    `detectors`（工单 #169，设计 §五）：**按本批实际命中的探测器注入专用判据段** ——
    把 D1~D6 全塞进一条提示词会稀释注意力、撑大上下文；按批注入最省。
    None/空 = 只注入通用段（向后兼容，供不带 detector 上下文的调用方使用）。
    """
    base = (
        "1) 唯一索引与 NULL（判 D1 的根判据，PostgreSQL 规范行为）: 普通唯一索引按 = 比较，"
        "NULL≠NULL → 含 NULL 的行**不受约束保护**；GROUP BY 把 NULL 归为一组，故"
        "「整组全 NULL」= 设计如此（如 ai_reports.task_id 全空）→ 业务允许；"
        "「部分列有值、仅某列 NULL」= 索引拦不住的**真缺口**（如 wallet_withdrawals 仅 device_sn 空）。\n"
        "2) 去重键红线（docs/standards/数据采集合并去重机制说明.md）: 去重键错=重复入库；"
        "采集通道的唯一键有权威定义，改动必须人工确认。\n"
        "3) 时区（docs/standards/数据时间字段使用规范.md）: 时间一律 Asia/Shanghai 换算，"
        "禁止按容器本地时区推断日期；表的时间列以 data_sources.time_field 为权威。\n"
        "4) 缓存（docs/standards/数据源接入与缓存加速规范.md）: 任何改数据的处置执行后，"
        "必须重建受影响物化表 + 失效对应缓存 pattern。\n"
        "5) 执行分级（docs/design/AI数据治理框架-20260919.md §六）: L0 只读自动；"
        "L1 可逆自动（标 quality_status，可 restore）；L2 不可逆（删/改/建索引）必须人工确认。\n"
        "6) 经营口径（AGENTS.md 绝对禁止第 10 条）: `mark_ignored` / `mark_dup_residual` 会把行"
        "**排除出经营统计**（改口径）；`mark_pending` 只标「待人工核对」，**仍计入统计**。"
        "判为业务允许时禁止使用前两者。"
    )
    dets = detectors or set()
    extra: list[str] = []
    if DETECTOR_TEXT_ANOMALY in dets:
        extra.append(
            "文本异常（D4，判据见 backend/app/services/text_anomaly_rules.py）: "
            "备注/说明类列出现换行可能业务需要（多行备注）；编号/SN/单号类列出现换行几乎必是坏值"
            "（截断或串行）。「长度贴列宽上限」只是**截断嫌疑不是证据** —— 看样本是否切在词中"
            "（中文突然终止、英文字母半截、数字位数不整）；源文件已不在库里，恢复不了原值 → "
            "默认给 report_only，交人工按 repro_sql 处置。"
        )
    if DETECTOR_IMPOSSIBLE_DATE in dets:
        extra.append(
            "不可能日期（D5，判据/阈值见 backend/app/services/date_anomaly_rules.py）: 四类 —— "
            "A 年份越界（<2015 或 > 当前年+1）、B 未来日期、C 时间倒挂（完成列 < 创建列，用"
            "**北京日期**比较）、D 兜底值聚集（单一日期占非空行数 ≥ 30% 且形如 01-01/12-31）。\n"
            "   判「业务允许」的典型情形：大促/月初批量结算造成的日期集中（D 只报兜底值形态，"
            "但**近月内的集中**仍可能是真实业务）、上游已知的批量覆盖（如 service_tickets.finish_time "
            "被上游批量改写）→ 这类给 report_only 或由人工 dismiss 沉淀规则。\n"
            "   判「真问题」且**上下文足以推断正确值**时（如 2026/9/19 缺前导零、Excel 序列号被当"
            "年份、位数异常、与同表其它时间列矛盾），给 `fix_time`（L2，requires_human_confirm=true）"
            "并在 `params.updates` 给出 `[{pk, col, old, new}]`（pk 取样本里的 `_id`，old 必须原样"
            "回填库中现值，new 写你推断出的值）。**依据不足一律 uncertain + confidence<0.7，"
            "禁止编造正确值** —— 写错值比不写更糟。\n"
            "   `mark_pending` 仅当该表在行级质量处置覆盖范围内（refund/ticket/wallet/shanmao/"
            "manual_refund）时可用，用于「确实可疑但拿不准正确值」的行。"
        )
    if DETECTOR_FILLABLE_GAP in dets:
        extra.append(
            "可推断空缺（D6，判据/来源清单见 backend/app/services/governance_detector.py 的 D6 段）: "
            "三类 —— A SN→设备属性、B 订单→SN、C 别名→标准人。**这些空缺的共同点是「值在库里"
            "另有权威来源」，不是「猜」**：`samples[]._source` 给出该建议值取自哪张表哪一行，"
            "人工可照 repro_sql 核对；`metrics.excluded_null_rows` 是**已排除出经营统计**"
            "（dup_residual/ignored）的空缺，探测器已剔除、不参与判断。\n"
            "   判「真问题」且来源唯一（`samples[]._alts` 为空或缺席）时：给 `mark_pending`"
            "（L1，可逆，requires_human_confirm=false）把样本行标「待人工核对」——"
            "**它仍计入经营统计，不改口径**。但 `mark_pending` 仅当该表在行级质量处置覆盖范围内"
            "（refund/ticket/wallet/shanmao/manual_refund）时可用；其余表（如 devices / scrm_*）"
            "只能给 `report_only`。\n"
            "   ⚠️ **本期一律不写值**（禁止给 `fix_time` 或任何改值动作）：补全的收益（省手工）"
            "远小于改错的风险；`suggestion.params` 只放补全依据与 confidence，"
            "不要把建议值写成要执行的 `updates`。\n"
            "   出现 `_alts`（多来源给出**不同**值）→ 说明建议值不唯一，"
            "给 `uncertain` + confidence<0.7 并在 reason 里列出各来源的值，交人工定夺。\n"
            "   判「业务允许」的典型情形：该列本就允许长期为空（如未激活设备无 device_type），"
            "或空缺量与来源命中量悬殊（绝大多数空缺推不出来 = 无依据，不是错误）→ "
            "`allowed_by_business` + report_only，由人工 dismiss 沉淀规则。"
        )
    # 编号接在通用段 1~6 之后（多个探测器同时命中时依次 7、8…，不重号）
    numbered = "\n".join(f"{7 + i}) {t}" for i, t in enumerate(extra))
    return base + ("\n" + numbered if numbered else "")


def _load_prompt(db: Session, code: str, default: str) -> str:
    """取启用中的提示词模板；无/异常/空 → 模块级默认（兜底不阻塞研判）。"""
    try:
        row = db.query(GovernancePrompt).filter(
            GovernancePrompt.code == code,
            GovernancePrompt.is_active.is_(True),
        ).first()
    except Exception as e:  # noqa: BLE001  提示词表查询失败不阻塞（回退默认）
        logger.warning("governance_judge 读提示词 %s 失败, 回退默认: %s", code, e)
        return default
    if row is None or not (row.content or "").strip():
        return default
    return row.content


def _safe_format(template: str, **kwargs: Any) -> str:
    """format 占位符失败（如 DB 提示词被改掉占位符）→ 原样返回，不抛 KeyError。"""
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return template


def _finding_payload(f: GovernanceFinding) -> dict:
    """一条 finding 的 LLM 输入形态（只给**证据**，不给结论字段）。

    metrics 去掉 `curve`（按日曲线可能上百点，对判定无益却撑爆上下文）。
    """
    metrics = {k: v for k, v in (f.metrics or {}).items() if k != "curve"}
    return {
        "finding_id": f.id,
        "detector": f.detector,
        "table_name": f.table_name,
        "target": f.target,
        "severity_raw": f.severity_raw,
        "metrics": metrics,
        "samples": f.samples,
        "repro_sql": (f.repro_sql or "")[:_REPRO_SQL_CHARS],
    }


def _table_notes(db: Session, findings: list[GovernanceFinding]) -> str:
    """涉及表的业务说明（data_sources.description）—— AI 判「业务允许」的主要依据。"""
    tables = sorted({f.table_name for f in findings})
    rows = db.execute(text(
        "SELECT table_name, name_cn, description FROM data_sources "
        "WHERE table_name = ANY(:tables)"
    ), {"tables": tables}).mappings().all()
    known = {r["table_name"]: r for r in rows}
    lines = []
    for t in tables:
        r = known.get(t)
        if r is None:
            lines.append(f"- {t}: （data_sources 未登记，无业务说明）")
        else:
            lines.append(f"- {t}（{r['name_cn']}）: {r['description'] or '（无 description）'}")
    return "\n".join(lines) if lines else "（无）"


def build_messages(db: Session, findings: list[GovernanceFinding]) -> list[dict]:
    """构造 [system(角色+铁律+规范+输出结构), user(证据+表说明)]。

    判据段按**本批实际命中的探测器**注入（设计 §五，工单 #169）。
    """
    payload = json.dumps([_finding_payload(f) for f in findings],
                         ensure_ascii=False, default=str, indent=1)
    detectors = {f.detector for f in findings}
    system = _safe_format(
        _load_prompt(db, "judge_system", _DEFAULT_SYSTEM_PROMPT),
        spec_rules=build_spec_rules(detectors),
        output_schema=_OUTPUT_SCHEMA,
    )
    user = _safe_format(
        _load_prompt(db, "judge_user", _DEFAULT_USER_PROMPT),
        count=len(findings),
        findings=payload,
        table_notes=_table_notes(db, findings),
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ═══════════════════════════════════════════════════════════════════════
# 三、输出解析与 schema 校验（要点③④）
# ═══════════════════════════════════════════════════════════════════════
def _parse_llm_json(raw: str) -> dict | None:
    """解析 LLM 输出 JSON（容错 markdown 代码块标记）；非 dict 返回 None。"""
    t = (raw or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    try:
        obj = json.loads(t)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _suggestion_errors(idx: int, suggestion: Any) -> list[str]:
    """suggestion 结构校验（含 L2 红线：不可逆动作必须人工确认）。"""
    errors: list[str] = []
    if not isinstance(suggestion, dict):
        return [f"judgments[{idx}].suggestion 缺失或不是对象"]
    action = suggestion.get("action")
    if action not in _VALID_ACTIONS:
        errors.append(f"judgments[{idx}].suggestion.action 非法: {action}")
    if suggestion.get("risk") not in _VALID_RISKS:
        errors.append(f"judgments[{idx}].suggestion.risk 非法: {suggestion.get('risk')}")
    if not str(suggestion.get("impact") or "").strip():
        errors.append(f"judgments[{idx}].suggestion.impact 缺失（须说明影响面）")
    # 红线（设计 §六）：不可逆动作必须 requires_human_confirm=true
    if action in _IRREVERSIBLE_ACTIONS and suggestion.get("requires_human_confirm") is not True:
        errors.append(
            f"judgments[{idx}].suggestion.requires_human_confirm 必须为 true"
            f"（{action} 是不可逆动作，红线）"
        )
    return errors


def validate_resp(obj: dict, finding_ids: set[int]) -> tuple[bool, list[str]]:
    """schema 校验整批 LLM 输出；返回 (是否合法, 错误列表)。

    覆盖设计 §5.2：verdict 枚举 / reason 非空 / suggestion 结构 / confidence 0~1，
    并要求**每条 finding 恰好一条 judgment**（禁止遗漏、新增、重复）。
    任一不合法 → 调用方整体重试 1 次（非法即整批不落库）。
    """
    errors: list[str] = []
    arr = obj.get("judgments")
    if not isinstance(arr, list) or not arr:
        return False, ["judgments 缺失或为空数组"]
    if len(arr) != len(finding_ids):
        errors.append(f"judgments 条数({len(arr)})与待研判条数({len(finding_ids)})不一致")

    seen: set[int] = set()
    for i, j in enumerate(arr):
        if not isinstance(j, dict):
            errors.append(f"judgments[{i}] 不是对象")
            continue
        fid = j.get("finding_id")
        if fid not in finding_ids:
            errors.append(f"judgments[{i}].finding_id 不在本批范围内: {fid}")
            continue
        if fid in seen:
            errors.append(f"judgments[{i}].finding_id 重复: {fid}")
        seen.add(fid)
        if j.get("verdict") not in _VALID_VERDICTS:
            errors.append(f"judgments[{i}].verdict 非法: {j.get('verdict')}")
        if not str(j.get("reason") or "").strip():
            errors.append(f"judgments[{i}].reason 缺失（判据必须引用规范条款或表说明）")
        conf = j.get("confidence")
        if not isinstance(conf, (int, float)) or isinstance(conf, bool) or not 0 <= conf <= 1:
            errors.append(f"judgments[{i}].confidence 非法（须 0~1 数值）: {conf}")
        errors += _suggestion_errors(i, j.get("suggestion"))
    missing = finding_ids - seen
    if missing:
        errors.append(f"以下 finding 未被判定（禁止遗漏）: {sorted(missing)}")
    return (not errors), errors


def _normalize_suggestion(suggestion: dict, verdict: str | None = None) -> dict:
    """落库前规范 suggestion（**第二道闸**）：

    ① 不可逆动作强制 `requires_human_confirm=true`（L2 红线）；
    ② `verdict=allowed_by_business` 时**禁止**标 ignored/dup 动作（工单 #171）——
       那会把行排除出经营统计、改变口径（AGENTS.md 绝对禁止第 10 条）。
       AI 自己判「业务允许」却建议一个改口径的动作是自相矛盾的；提示词里已写铁律，
       但**提示词不是保证**，故在此强制降级为 `report_only`（数据一行不动），
       并留 `note` 说明降级原因（可审计「AI 原本建议了什么」）。
    """
    out = dict(suggestion or {})
    out["params"] = out.get("params") or {}
    out["impact"] = str(out.get("impact") or "").strip()
    out["risk"] = out.get("risk") or "medium"
    if out.get("action") in _IRREVERSIBLE_ACTIONS:
        if out.get("requires_human_confirm") is not True:
            logger.warning("governance_judge: 不可逆动作 %s 未标人工确认, 已强制置 true",
                           out.get("action"))
        out["requires_human_confirm"] = True
    else:
        out["requires_human_confirm"] = bool(out.get("requires_human_confirm"))

    # ② 业务允许 + 标排除动作 → 降级为只读报告（#171 红线兜底）
    if (verdict == VERDICT_ALLOWED_BY_BUSINESS
            and out.get("action") in (DISPOSITION_MARK_IGNORED, DISPOSITION_MARK_DUP)):
        logger.warning("governance_judge: 业务允许却建议 %s（会排除出经营统计），"
                       "已降级为 report_only", out.get("action"))
        out["note"] = (f"AI 原建议 {out['action']}，因判定为业务允许（不应改变经营口径）"
                       f"已降级为 report_only；如需停止重复报出，请用「判定业务允许」沉淀规则")
        out["action"] = DISPOSITION_REPORT_ONLY
        out["params"] = {}
    return out


# ═══════════════════════════════════════════════════════════════════════
# 四、LLM 调用与批量研判（要点②③）
# ═══════════════════════════════════════════════════════════════════════
def _last_call_log(db: Session) -> dict | None:
    """取最近一条本服务 LLM 调用审计（provider/model 溯源，留痕用）。"""
    try:
        row = db.query(LlmCallLog).filter(LlmCallLog.source == _LLM_SOURCE) \
            .order_by(LlmCallLog.id.desc()).first()
    except Exception as e:  # noqa: BLE001  审计查询失败不阻塞
        logger.warning("governance_judge 查 LLM 审计失败: %s", e)
        return None
    if row is None:
        return None
    return {"provider_code": row.provider_code, "model": row.model}


async def _llm_judge_call(db: Session, messages: list[dict], *,
                          correction: str | None = None) -> tuple[str | None, str | None]:
    """调一次 chat_completion（默认实时链 27b，超时 60s）。返回 (content, error)。"""
    msgs = list(messages)
    if correction:
        msgs.append({
            "role": "user",
            "content": f"【上次输出无效】{correction}。请只输出符合 schema 的 JSON，不要解释。",
        })
    try:
        result = await llm_client.chat_completion(
            msgs,
            temperature=0.2,
            max_tokens=_MAX_TOKENS,
            timeout_s=_TIMEOUT_S,
            db=db,
            source=_LLM_SOURCE,
            provider_code=None,  # 默认实时链（27b），不降级 122b（#131 语义）
        )
        return str(result), None
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001  LLM 调用失败由上层重试/报错
        return None, str(e)


async def _judge_batch(db: Session, findings: list[GovernanceFinding]) -> dict[int, dict]:
    """对一批 finding 跑一次 LLM 研判（含重试）；返回 {finding_id: judgment}。

    两次仍失败/非法 → 返回空 dict（这批整体放弃，保持 open，不落脏数据）。
    被抢占（CancelledError）直接上抛，交由上层提示可重试。
    """
    messages = build_messages(db, findings)
    ids = {f.id for f in findings}
    invalid_hint = ""
    for attempt in range(_LLM_RETRY + 1):
        content, err = await _llm_judge_call(db, messages, correction=invalid_hint)
        if content is None:
            invalid_hint = f"LLM 调用失败: {err or '未知错误'}"
            if attempt < _LLM_RETRY:
                logger.warning("governance_judge LLM 调用失败(第%d次, 将重试): %s", attempt + 1, err)
            continue
        obj = _parse_llm_json(content)
        if obj is None:
            invalid_hint = "输出无法解析为 JSON（检查是否输出了解释文字或代码块标记）"
            continue
        ok, errors = validate_resp(obj, ids)
        if ok:
            return {int(j["finding_id"]): j for j in obj["judgments"]}
        invalid_hint = "；".join(errors[:5])
        logger.warning("governance_judge schema 校验不过(第%d次): %s", attempt + 1, invalid_hint)
    logger.error("governance_judge 批量研判失败(重试后仍失败), 本批保持 open: %s", invalid_hint)
    return {}


# ═══════════════════════════════════════════════════════════════════════
# 五、研判编排（规则短路 + 批量 AI + 回填 + 留痕）
# ═══════════════════════════════════════════════════════════════════════
def _apply_rule(f: GovernanceFinding, rule: GovernanceRule, now: _dt.datetime) -> None:
    """规则短路回填（要点①）：不调 LLM，直接给结论 + 记规则命中计数。"""
    f.verdict = rule.verdict
    f.verdict_reason = f"命中已沉淀规则 #{rule.id}: {rule.reason}"
    f.suggestion = _rule_suggestion(rule)
    f.confidence = 1.0
    f.rule_id = rule.id
    f.judged_at = now
    f.status = FINDING_STATUS_JUDGED
    rule.hit_count = (rule.hit_count or 0) + 1
    rule.last_hit_at = now


def _apply_ai(f: GovernanceFinding, judgment: dict, now: _dt.datetime) -> None:
    """AI 结论回填（要点③）：verdict/reason/suggestion/confidence + status→judged。"""
    f.verdict = judgment["verdict"]
    f.verdict_reason = judgment.get("reason")
    f.suggestion = _normalize_suggestion(judgment.get("suggestion"), verdict=f.verdict)
    f.confidence = float(judgment.get("confidence") or 0.0)
    f.rule_id = None  # AI 判定不绑定规则（rule_id 只表示「由规则判定」）
    f.judged_at = now
    f.status = FINDING_STATUS_JUDGED


async def _judge_core(db: Session, finding_ids: list[int] | None, *, commit: bool,
                      event_id: int | None = None) -> dict:
    """研判核心：取 open → 规则短路 → 批量 AI → 回填 → 留痕（无调度层，供门闸/测试调用）。

    `event_id` 非空（手动触发路径，工单 #165）：**回填**该占位事件（running → 终态），
    不再新建一行（202 + 轮询模式一行事件贯穿全程）。
    """
    t0 = time.perf_counter()
    query = db.query(GovernanceFinding).filter(GovernanceFinding.status == FINDING_STATUS_OPEN)
    if finding_ids:
        query = query.filter(GovernanceFinding.id.in_(finding_ids))
    open_list = query.order_by(GovernanceFinding.detected_at.asc()).all()
    if not open_list:
        # 无待研判项 → 直接返回且**不写 system_events**（#166 教训：无节流的重复留痕
        # 会淹没真实变化；空跑不该在事务流里刷屏）；占位事件仍要收尾，否则前端一直转圈
        if event_id is not None:
            update_system_event(db, event_id, status="success", title="治理研判: 无待研判项",
                                summary="无 open finding", detail=json.dumps(
                                    {"total_open": 0}, ensure_ascii=False), commit=commit)
        return {"status": "success", "total_open": 0, "judged": 0,
                "by_rule": 0, "by_ai": 0, "llm_called": False,
                "unjudged_ids": [], "duration_ms": 0}

    now = _now_utc()
    by_rule = 0
    pending: list[GovernanceFinding] = []
    for f in open_list:
        rule = find_matching_rule(db, f)
        if rule is not None:
            _apply_rule(f, rule, now)
            by_rule += 1
        else:
            pending.append(f)

    # 规则命中即**短路**：只有 pending 非空才会调用 LLM（验收第 3 条的断言点）
    by_ai = 0
    for start in range(0, len(pending), _BATCH_SIZE):
        batch = pending[start:start + _BATCH_SIZE]
        judgments = await _judge_batch(db, batch)
        for f in batch:
            j = judgments.get(f.id)
            if j is None:
                continue  # 本批未获判定 → 保持 open，下轮重试（不落脏数据）
            _apply_ai(f, j, now)
            by_ai += 1

    duration_ms = int((time.perf_counter() - t0) * 1000)
    llm_called = bool(pending)
    call_log = _last_call_log(db) if llm_called else None
    provider = (call_log or {}).get("provider_code") or ("default(实时链)" if llm_called else "未调用(规则短路)")
    title = f"治理研判: {len(open_list)} 条结构性可疑"
    summary = (f"规则短路 {by_rule} 条 / AI 研判 {by_ai} 条; "
               f"provider={provider}; 耗时 {duration_ms}ms")
    detail = json.dumps({
        "total_open": len(open_list),
        "by_rule": by_rule,
        "by_ai": by_ai,
        "llm_called": llm_called,
        "provider": provider,
        "model": (call_log or {}).get("model"),
        "duration_ms": duration_ms,
        "finding_ids": [f.id for f in open_list],
    }, ensure_ascii=False)
    if event_id is not None:
        update_system_event(db, event_id, status="success", title=title,
                            summary=summary, detail=detail, commit=False)
    else:
        record_system_event(
            db, category=EVENT_CATEGORY, action=EVENT_JUDGE,
            title=title, summary=summary, status="success",
            detail=detail, commit=False,
        )
    # 未获判定的（LLM 两次失败）保持 open，下轮重试 —— 先取 id 再 commit（避免过期重载）
    unjudged_ids = [f.id for f in pending if f.status == FINDING_STATUS_OPEN]
    if commit:
        db.commit()
    return {
        "status": "success",
        "total_open": len(open_list),
        "judged": by_rule + by_ai,
        "by_rule": by_rule,
        "by_ai": by_ai,
        "llm_called": llm_called,
        "unjudged_ids": unjudged_ids,
        "duration_ms": duration_ms,
    }


async def judge_findings_async(db: Session, finding_ids: list[int] | None = None, *,
                               commit: bool = True,
                               event_id: int | None = None) -> dict:
    """异步研判入口：经 llm_scheduler **L1** 门闸执行（不占用户实时问答 L2，设计 §5.3）。

    供 #165 管理页接口（后台任务）调用；被更高优先级任务抢占 → CancelledError 上抛，
    调用方可提示重试（属正常抢占，非失败）。
    """
    from app.services.llm_scheduler import acquire, release

    handle = await acquire(level=_LLM_LEVEL, name="governance_judge")
    try:
        return await _judge_core(db, finding_ids, commit=commit, event_id=event_id)
    finally:
        await release(handle)


def judge_findings(db: Session, finding_ids: list[int] | None = None, *,
                   commit: bool = True, event_id: int | None = None) -> dict:
    """同步研判入口（worker / 测试 / 非 async 上下文用；同 onboarding_draft 模式）。"""
    try:
        return asyncio.run(judge_findings_async(db, finding_ids, commit=commit,
                                                event_id=event_id))
    except RuntimeError as e:
        logger.warning("governance_judge 已在事件循环中, 换新 loop: %s", e)
        return asyncio.new_event_loop().run_until_complete(
            judge_findings_async(db, finding_ids, commit=commit, event_id=event_id)
        )


def start_judge_task(event_id: int) -> None:
    """后台跑一次研判并回填占位事件（202 + 轮询，工单 #165）。

    与体检不同：研判是**异步**的（等 LLM），故直接在事件循环里 create_task，
    db session 在协程内自建自关（不跨线程）。
    """
    async def _run() -> None:
        db = SessionLocal()
        try:
            await judge_findings_async(db, event_id=event_id)
        except asyncio.CancelledError:
            # 被更高优先级任务抢占（L0 预生成 / L2 实时问答）—— 属正常，占位事件收尾为可重试
            logger.info("governance_judge 后台任务被抢占, 占位事件置 partial")
            try:
                update_system_event(db, event_id, status="partial",
                                    summary="研判被更高优先级任务抢占, 可重试",
                                    commit=True)
            except Exception:  # noqa: BLE001
                db.rollback()
        except Exception as e:  # noqa: BLE001  后台任务失败只留痕
            logger.error("governance 后台研判异常: %s", e)
            try:
                db.rollback()
                update_system_event(db, event_id, status="failed",
                                    summary=f"研判失败: {e}"[:512],
                                    detail=json.dumps({"error": str(e)}, ensure_ascii=False),
                                    commit=True)
            except Exception:  # noqa: BLE001
                db.rollback()
        finally:
            db.close()

    asyncio.get_running_loop().create_task(_run())


# ═══════════════════════════════════════════════════════════════════════
# 六、人工处置：dismiss → 规则沉淀（要点⑤）
# ═══════════════════════════════════════════════════════════════════════
def dismiss_finding(db: Session, finding_id: int, *, reason: str | None = None,
                    created_by: str | None = None, commit: bool = True) -> dict:
    """人工裁定「业务允许/非问题」→ finding 置 ignored + **沉淀治理规则**。

    沉淀的规则 verdict 恒为 `allowed_by_business`、动作恒为 L0 只读（report_only）——
    「判定没问题」不应附带任何自动改数据的授权（与设计 §六 红线一致：不可逆动作永远人工确认）。
    下次同 (detector, 表, target) 的 finding 直接命中规则，**不再调 LLM**。

    若已有同款启用规则 → 复用（只累加 hit_count），不重复建（防规则漂移）。
    返回 {"finding_id", "rule_id", "created"}。
    """
    f = db.get(GovernanceFinding, finding_id)
    if f is None:
        raise JudgeError(f"finding 不存在: id={finding_id}")
    if f.status == FINDING_STATUS_IGNORED:
        raise JudgeError(f"finding #{finding_id} 已是 ignored 状态")

    now = _now_utc()
    f.status = FINDING_STATUS_IGNORED
    f.verdict = VERDICT_ALLOWED_BY_BUSINESS
    f.verdict_reason = reason or (f.verdict_reason or "人工裁定：业务允许（非问题）")
    f.judged_at = now

    existing = find_matching_rule(db, f)
    if existing is not None:
        existing.hit_count = (existing.hit_count or 0) + 1
        existing.last_hit_at = now
        f.rule_id = existing.id
        rule, created = existing, False
    else:
        rule = GovernanceRule(
            detector=f.detector,
            table_name=f.table_name,
            target_pattern=f.target,
            match_metrics=None,   # 同表同目标即同一结构情形，不额外限制
            verdict=VERDICT_ALLOWED_BY_BUSINESS,
            reason=f.verdict_reason,
            disposition=DISPOSITION_REPORT_ONLY,
            auto_level=AUTO_LEVEL_READONLY,
            hit_count=0,
            source_finding_id=f.id,
            created_by=created_by,
            enabled=True,
        )
        db.add(rule)
        db.flush()
        f.rule_id = rule.id
        created = True

    record_system_event(
        db,
        category=EVENT_CATEGORY,
        action=EVENT_RULE_SINK,
        title=f"治理规则沉淀: {f.table_name}/{f.target}",
        summary=f"来源 finding #{f.id}（{f.detector}）→ 规则 #{rule.id}"
                f"{'（新建）' if created else '（复用已有）'}",
        status="success",
        detail=json.dumps({
            "finding_id": f.id,
            "rule_id": rule.id,
            "created": created,
            "created_by": created_by,
            "reason": f.verdict_reason,
        }, ensure_ascii=False),
        commit=False,
    )
    if commit:
        db.commit()
        db.refresh(rule)
    return {"finding_id": f.id, "rule_id": rule.id, "created": created}
