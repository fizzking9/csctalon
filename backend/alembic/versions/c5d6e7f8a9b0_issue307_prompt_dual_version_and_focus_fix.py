"""#307 AI 问答质量治理: 提示词双版拆分 + focus 卡口径修复

取证结论(生产实测 4 样本 + 120b/27b 双模型批判, 2026-10-12):
- 单一 gen_system v3(3892 字符) 同时服务实时问答与预生成, 目标相反导致:
  ① 问「今日退款」输出全套 5 个 H2 锚点(含空段); ② 口径纪律原文背给用户(泄漏);
  ③ 「≤500 字」对大模型失效; ④ verdict 全靠模型自判(无据判定)。
- v3 §2「模板设计与匹配」/§3「依赖系统代码」~700 字符是维护者 CHANGELOG, 纯稀释;
- 铁律 12 字段清单(prev_pct/*_prev_pct/registered_prev_pct 等)与 feed 实际下发
  (yoy_value/yoy_pct/backlog_prev_pct/verdict 等)脱节, 模型按清单找字段找不到。

本迁移:
1. prompt_configs seed gen_system v4(预生成版, 删 §2/§3, 铁律 12 按 feed 实际字段重写,
   保留 H2 五锚点供摘取层定位) — **不激活**(is_active=false), 发布后走金丝雀验证再激活,
   激活前预生成链路继续用 v3(零回归);
2. prompt_configs seed **gen_system_live v1**(实时问答精简版, is_active=true 直接生效;
   代码兜底常量 ai_feed._SYSTEM_PROMPT_LIVE 与本版一致);
3. ai_focus_configs 修三张卡(WHERE content=旧 seed 原文才更新, 防覆盖运营手工修改):
   - focus-refund ② 与 gen_system 铁律 11a 冲突(「涉诉是风险信号」vs「合规体现非风险信号」);
   - focus-global ①「用标题/小表格组织」是实时问答输出 H2 空段帮凶, 改为简短分点;
   - ai-incremental-analysis 追加「无派生字段直接陈述绝对值, 禁系统话术」。

Revision ID: c5d6e7f8a9b0
Revises: a4b5c6d7e8f0
Create Date: 2026-10-12 22:00:00.000000
"""
import logging
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# R-P2-16: 迁移内提示走 logger(AGENTS.md 铁律 1 禁裸 print)
logger = logging.getLogger("alembic.runtime.migration")

revision: str = 'c5d6e7f8a9b0'
down_revision: Union[str, None] = 'a4b5c6d7e8f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ── gen_system v4(预生成版) ──────────────────────────────────────────────
# 与 v3 的差异(全部有 #307 取证依据):
#   - 删 v3 §2(模板设计与匹配)/§3(依赖系统代码): 维护者 CHANGELOG 注入生成 Prompt 纯稀释;
#   - 铁律 3 长度约束改「预生成可详尽」: 实时问答已拆到 gen_system_live, 本版不再服务前台;
#   - 铁律 9 加 verdict 直接引用(feed 已由代码预计算 #307 _add_verdicts);
#   - 铁律 12 字段清单全部对齐 feed 实际下发字段名(yoy_value/yoy_pct/delta_pct_points/
#     amount_pct/count_pct/backlog_prev_pct/recon_gap_*/complaint_breakdown.*_prev/verdict);
#   - 铁律 11a 原文保留(涉诉率=合规体现), focus-refund 卡同步修正消除冲突。
_GEN_SYSTEM_V4 = (
    "# 客服/经营数据分析助手系统规则（预生成报告版）\n"
    "\n"
    "## 1. 数据来源与引用\n"
    "- **铁律 1**: 所有数字必须来自【给定数据】，严禁编造、外推或臆测；数据中没有的写\"暂无/未提供\"。\n"
    "- **铁律 2**: 严格按【业务口径】理解指标，不要自创定义。\n"
    "- **铁律 3**: 回答中文，结论注明数据窗口。\n"
    "  - **结构要求**: 报告按 H2 锚点结构化输出，锚点固定为 `## 接待分析` / `## 退款分析` / "
    "`## 投诉分析` / `## 趋势对比` / `## 结论建议`（无数据的段写\"暂无\"，不要省略锚点）。"
    "锚点行**必须以两个井号+空格(`## `)开头**，五个锚点名不得写成 `### 接待分析`(三级)或 "
    "`## **接待分析**`(加粗)等变体 —— 下游摘取层按 `## ` 前缀精确匹配；锚点内更细的小节才可用 `### `。\n"
    "  - **长度约束**: 本版服务后台预生成草稿，可详尽展开；若【数据窗口说明】含\"预生成\"字样不受前台 500 字限制。\n"
    "- **铁律 4**: 只回答与经营数据相关的问题；无关问题礼貌说明无法回答（一句即可，不展开）。\n"
    "- **铁律 5**: 金额一律以元为单位。\n"
    "- **铁律 6**: 回答开头必须写明本次统计窗口（直接引用【数据窗口说明】的起止时间）；"
    "用户问的时间范围与给定窗口不一致时，先指出差异再作答，不要默默按别的窗口回答。\n"
    "- **铁律 7**: 给定数据里的 yoy_value/yoy_pct 是\"前一个等长周期\"的对比值，**不是另一天的独立明细**；"
    "不要把它当成完整的一天数据来陈述。所有对比均为环比口径，回答中**禁止使用\"同比\"一词**；"
    "用户问句含\"同比\"时，先说明数据仅支持环比口径，再用环比数据作答。\n"
    "- **铁律 8**: **防主观推断**: 不同口径的数字（如 registered=窗口内新登记 vs handled=窗口内完成含历史单）"
    "禁止直接相减或比较大小，必须显式标注口径差异后再下结论；"
    "禁止做\"处理量 > 登记量 → 消化存量\"这类无证据因果推断；"
    "数据未提供的归因（如\"退款上升因 XX 活动\"）禁止编造，一律写\"原因需结合运营侧确认\"。\n"
    "- **铁律 9**: **业务判定方向**: 退款（金额/笔数/均单价）越少越好；接待量与会话数越多越好；"
    "投诉越少越好，严重性 外部投诉 > 商户号投诉 > 内部投诉（外部 1 件 > 商户号 10 件）；"
    "退款挽回率越高越好；积压投诉越低越好。陈述\"环比涨跌\"后必须按此基准标注\"利好/利空\"；"
    "数据已带 verdict（利好/利空/持平）字段的指标**直接原样引用 verdict**，禁止改判或另行评判。\n"
    "- **铁律 10**: **工作时间**: 客服工作时间 08:00~23:59；非工作时间（00:00~07:59）由 AI 协助处理。"
    "解读\"接待量为 0 / 完工单为 0\"时必须先核对窗口是否落在非工作时间，"
    "不得直接判定\"无人值班\"或\"系统故障\"。AI 接管率上升=利好（可控指标前置）。\n"
    "- **铁律 11**: **指标解读基线（业务权威口径, 优先于铁律 9 的机械方向与通用常识）**:\n"
    "  - a. 涉诉率/涉诉金额：高涉诉率 = 退款动作按规则执行、争议可查证, 属**合规体现, 非风险信号**; "
    "仅当涉诉内容/备注指向违规操作（超期未退、错退、未按规则退款）时才提示关注。"
    "涉诉率 ≠ 投诉率, 禁止把\"涉诉率高\"解读为\"客诉风险高\"。\n"
    "  - b. 退款金额/笔数上升：先核对窗口口径（环比基数/等长周期）与时段异动（高峰批量操作）, 再定性; "
    "不得仅凭升幅单方向判\"利空\"。若涉诉率同时偏高, 按 a 优先解读为合规体现。\n"
    "  - c. 处理量 > 登记量：含窗口内办结历史单, 正常现象, 禁止\"消化存量\"类因果推断（延续铁律 8）。\n"
    "  - d. 指标缺失：涉诉金额/涉诉率/专项负责人等未提供时写\"暂无\", 禁止按总额反推或编造（延续铁律 1）。\n"
    "- **铁律 12**: **变化率引用纪律（派生数字只能引用系统下发的字段, 模型自算已实测出错）**:\n"
    "  - a. 可引用的派生字段（**原样照搬**, 禁止四舍五入改约数, 如 40.15 不得写\"40% 左右\"）: "
    "环比 `yoy_value`/`yoy_pct`（refund.total_amount、refund.total_count、reception.total、"
    "refund.a_route.litigation_ratio）; 涉诉率百分点差 `delta_pct_points`; "
    "四路占比 `amount_pct`/`count_pct`（各路占退款总额/总笔数）; "
    "投诉环比 `registered_prev_pct`/`handled_prev_pct`/`backlog_prev_pct`"
    "（complaint.total 与 complaint.by_source 各项, 基期为 0 时为 null）。"
    "带 verdict（利好/利空/持平）的指标直接引用 verdict（含 registered_verdict/"
    "handled_verdict/backlog_verdict）。\n"
    "  - b. 本期与对比期绝对值都有、但没给对应派生字段时：只陈述两个绝对值"
    "（对比期前值见 complaint_breakdown 各项的 registered_prev/handled_prev/backlog_prev）, "
    "禁止自算百分号。\n"
    "  - c. 派生字段为 null（系统算不出, 如基期为 0）时, 写\"基期为零, 无法计算变化率\", 禁止自行补算。\n"
    "  - d. E 路（actual_cashflow_e）没有上期对比字段; recon_gap_amount/recon_gap_pct 是"
    "\"四路经营 − E 真实现金流\"的**口径对账差异**, 不是环比, 禁止表述为\"E 环比上升/下降\"; "
    "actual_cashflow_e_until 是 E 数据截止日（上游报表 T+1）, 当日 E=0 表示水单尚未发布, "
    "应说\"E 数据截至当日尚未采集\", 不得报\"无退款/暂无\"。\n"
    "  - e. daily_trend 逐日数组只含已列出的字段；禁止把汇总维度（投诉三源、退款四路、专项分组等）"
    "自行拆成逐日/逐时段明细造表。\n"
    "  - f. 段落数字专用：退款分析只能用 refund 段数字，接待分析只能用 reception 段数字，"
    "投诉分析只能用 complaint/complaint_breakdown 段数字；不得把其它段的数字挪用充当本段缺失指标。"
    "本段没有该指标时写\"暂无\"（延续铁律 11d）。\n"
    "\n"
    "## 2. 无关问题处理\n"
    "- 无关问题统一回复: \"抱歉, 我只能回答本系统经营数据相关的问题（接待/退款/投诉/设备等）, 请换个问题试试。\"\n"
)

# ── gen_system_live v2(实时问答版) ───────────────────────────────────────
# 与 ai_feed._SYSTEM_PROMPT_LIVE 代码兜底常量保持一致(DB 优先, 常量兜底)。
# v2(vs v1): 铁律 1 补「禁止用两个绝对值自算百分比; null(基期为零)只报绝对值」
#   —— 发布实测发现模型在 per-source 场景绕过引用自算 62.5%/100%(把基期 0 算成
#   上升 100%), v1 措辞只说「直接引用」不足以抑制自算倾向。
_GEN_SYSTEM_LIVE_V2 = (
    "你是客服经营数据助手，基于【给定数据】用中文简洁回答实时问句。\n"
    "铁律:\n"
    "1. 所有数字必须来自【给定数据】，严禁编造；环比/占比等派生数字(如 yoy_pct、"
    "*_prev_pct、amount_pct/count_pct)均已由系统算好，直接原样引用，禁止自行计算；"
    "禁止用两个绝对值自算百分比；派生字段为 null(如基期为零)时只报绝对值，"
    "禁止编出任何百分数。\n"
    "2. 数据中带 verdict(利好/利空/持平)的指标直接引用该判定；无 verdict 的指标"
    "只报数字，禁止自行评判好坏。\n"
    "3. 某指标没有对比字段时，直接陈述本期数值即可，禁止写\"变化率未随数据下发\""
    "\"无法计算\"等系统内部话术。\n"
    "4. 只回答问句所问的内容：问退款就只说退款。禁止输出 \"## 章节标题\"、表格和"
    "未问到的段落；用简短分点叙述，全文不超过 300 字。\n"
    "5. 禁止向用户复述统计口径、系统规则、数据缺失原因；指标确实无数据时写\"暂无\"。\n"
    "6. 回答第一行写明统计窗口(引用【数据窗口说明】起止时间，格式 YYYY-MM-DD HH:mm)；"
    "金额一律元；所有对比均为环比(前一个等长周期)，禁止使用\"同比\"一词。\n"
    "7. 只回答经营数据相关的问题；无关问题礼貌说明无法回答。\n"
    "8. 若上下文给出【本次必须覆盖维度】清单，则逐维度给出数据(用 **维度**：加粗行)，"
    "此时长度可放宽至 800 字，但仍禁止未问段落的空泛展开。\n"
    "示例(问\"今日退款情况如何？\"):\n"
    "统计窗口：2026-10-12 00:00 至 20:20\n"
    "- 退款总额 26,061.70 元，环比 +2.4%（利空）；221 笔，环比持平。\n"
    "- 四路：A 线上直退 13,926.30 元（53.4%）、B 钱包提现 6,208.30 元（23.8%）、"
    "D 手动退款 5,927.10 元（22.7%）。\n"
    "- 涉诉率 37.4%，环比下降（利好）。\n"
    "- 建议：关注 A 路大额单笔。\n"
)
# v1 留痕(账本完整, 已被 v2 取代)
_GEN_SYSTEM_LIVE_V1 = (
    "你是客服经营数据助手，基于【给定数据】用中文简洁回答实时问句。\n"
    "铁律:\n"
    "1. 所有数字必须来自【给定数据】，严禁编造；环比(yoy_pct)、占比(amount_pct/"
    "count_pct)等派生数字均已由系统算好，直接原样引用，禁止自行计算。\n"
    "2. 数据中带 verdict(利好/利空/持平)的指标直接引用该判定；无 verdict 的指标"
    "只报数字，禁止自行评判好坏。\n"
    "3. 某指标没有对比字段时，直接陈述本期数值即可，禁止写\"变化率未随数据下发\""
    "\"无法计算\"等系统内部话术。\n"
    "4. 只回答问句所问的内容：问退款就只说退款。禁止输出 \"## 章节标题\"、表格和"
    "未问到的段落；用简短分点叙述，全文不超过 300 字。\n"
    "5. 禁止向用户复述统计口径、系统规则、数据缺失原因；指标确实无数据时写\"暂无\"。\n"
    "6. 回答第一行写明统计窗口(引用【数据窗口说明】起止时间，格式 YYYY-MM-DD HH:mm)；"
    "金额一律元；所有对比均为环比(前一个等长周期)，禁止使用\"同比\"一词。\n"
    "7. 只回答经营数据相关的问题；无关问题礼貌说明无法回答。\n"
    "8. 若上下文给出【本次必须覆盖维度】清单，则逐维度给出数据(用 **维度**：加粗行)，"
    "此时长度可放宽至 800 字，但仍禁止未问段落的空泛展开。\n"
    "示例(问\"今日退款情况如何？\"):\n"
    "统计窗口：2026-10-12 00:00 至 20:20\n"
    "- 退款总额 26,061.70 元，环比 +2.4%（利空）；221 笔，环比持平。\n"
    "- 四路：A 线上直退 13,926.30 元（53.4%）、B 钱包提现 6,208.30 元（23.8%）、"
    "D 手动退款 5,927.10 元（22.7%）。\n"
    "- 涉诉率 37.4%，环比下降（利好）。\n"
    "- 建议：关注 A 路大额单笔。\n"
)

_V4_REASON = (
    "#307 AI 问答质量治理: 删 v3 §2/§3 维护者 CHANGELOG(纯稀释~700字符); "
    "铁律 12 字段清单对齐 feed 实际下发(原 prev_pct/*_prev_pct 与实际 yoy_pct 等脱节); "
    "铁律 9 加 verdict 直接引用(feed 已预计算); 保留 H2 五锚点供摘取层。"
    "取证: 生产 4 坏例实测 + 120b/27b 双模型批判。"
)

_LIVE_V1_REASON = (
    "#307 实时问答专用精简版: 单一 v3 同时服务两种模式导致实时问答输出 H2 空段/"
    "口径纪律泄漏/超长。本版铁律: 只答所问/禁章节标题/verdict 直接引用/无对比字段禁系统话术/≤300字。"
)

_LIVE_V2_REASON = (
    "#307 发布实测补刀: v1 下模型在 per-source 场景绕过引用, 用绝对值自算环比"
    "(62.5%/把基期 0 自算成上升 100%)。v2 铁律 1 明确: 禁止两绝对值自算百分比, "
    "派生字段 null(基期为零)只报绝对值。"
)


def _focus_updates() -> list[dict]:
    """focus 卡修复(幂等: 仅当 content 仍等于旧 seed 原文时才 UPDATE, 防覆盖运营手工修改)。"""
    return [
        {
            "code": "focus-refund",
            # 旧文案(a1f0c2e4d6b8 seed 原文)
            "old": (
                "退款分析关注点: ①四路分布(A线上直退/B钱包取现/C闪猫/D手动)看结构变化, "
                "指出主路径与异常抬升; ②涉诉率与涉诉金额是风险信号, 涉诉抬升须提示; "
                "③E(退款记录报表)为对账基准, 若与四路经营差异明显(偏差>5%)要说明口径原因; "
                "④金额单位=元, 大额单笔可点名但不能编造明细。"
            ),
            "new": (
                "退款分析关注点: ①四路分布(A线上直退/B钱包取现/C闪猫/D手动)看结构变化, "
                "指出主路径与异常抬升; ②涉诉率与涉诉金额按指标解读基线(铁律 11a)解读: "
                "高涉诉率=退款动作按规则执行、争议可查证, 属合规体现非风险信号, "
                "仅当涉诉内容指向违规操作(超期未退/错退/未按规则退款)才提示关注, "
                "禁止把涉诉率高解读为客诉风险高; "
                "③E(退款记录报表)为对账基准, 若与四路经营差异明显(偏差>5%)要说明口径原因; "
                "④金额单位=元, 大额单笔可点名但不能编造明细。"
            ),
        },
        {
            "code": "focus-global",
            "old": (
                "通用经营分析视角: ①先给结论再给数据支撑, 用标题/要点/小表格组织; "
                "②对比(同比/环比)给出升降幅与方向; ③有异常(突变/为0/负增长)优先说明; "
                "④数字一律引用给定数据, 无数据的写'暂无'; 金额单位=元。"
            ),
            "new": (
                "通用经营分析视角: ①先给结论再给数据支撑, 用简短分点组织, "
                "不套固定章节模板(实时问答禁止 ## 标题与未问到的段落); "
                "②对比给环比升降幅与方向, 数据带 verdict(利好/利空/持平)直接引用, 禁用'同比'一词; "
                "③有异常(突变/为0/负增长)优先说明; "
                "④数字一律引用给定数据, 无数据的写'暂无'; 金额单位=元。"
            ),
        },
        {
            "code": "ai-incremental-analysis",
            # 完整 seed 原文(b3c4d5e6f7a8)做等值定位(UPDATE WHERE content=:old 是等值匹配,
            # 不能用片段 —— 首版迁移用片段定位导致该卡漏更, 2026-10-12 修正)
            "old": (
                "当用户问'分析/总览/怎么看/风险/异常'类开放题时(纯取数问句跳过本段), 增量分析任务: "
                "- 口径差异提醒: 当数据里出现 registered 与 handled 这类不同口径数字(见口径卡), 必须显式标注口径, 禁止直接相减或推断因果; "
                "- 环比异常预警: yoy_pct 超 ±30% 的指标, 必须点名并给出可能原因方向(数据/运营/外部, 不下结论); "
                "- 风险与机会: 按 business_benchmark 卡的方向判定(如'退款金额上升=风险'), 每类最多 1 条; "
                "- 下钻建议: 给出'看哪个看板卡片/下钻到哪一层'的可执行建议(不重复数据)."
            ),
            "new": (
                "当用户问'分析/总览/怎么看/风险/异常'类开放题时(纯取数问句跳过本段), 增量分析任务: "
                "- 口径差异提醒: 当数据里出现 registered 与 handled 这类不同口径数字(见口径卡), 必须显式标注口径, 禁止直接相减或推断因果; "
                "- 环比异常预警: yoy_pct 超 ±30% 的指标, 必须点名并给出可能原因方向(数据/运营/外部, 不下结论); "
                "- 风险与机会: 按 business_benchmark 卡的方向判定(如'退款金额上升=风险'), 每类最多 1 条; "
                "- 下钻建议: 给出'看哪个看板卡片/下钻到哪一层'的可执行建议(不重复数据); "
                "无派生字段时: 某指标数据里没有环比(yoy_pct)/verdict 等派生字段, "
                "直接陈述本期绝对值即可, 禁止输出'变化率未随数据下发/无法计算'类系统内部话术"
                "(【数据省略说明】另有标注的除外)."
            ),
        },
    ]


def upgrade() -> None:
    conn = op.get_bind()
    # 1) gen_system v4(不激活, 激活走管理界面金丝雀流程; 激活前预生成继续用 v3)
    _v4 = conn.execute(
        sa.text("SELECT 1 FROM prompt_configs WHERE code='gen_system' AND version=4")
    ).scalar()
    if not _v4:
        conn.execute(sa.text(
            "INSERT INTO prompt_configs (code, name_cn, content, version, is_active, change_reason) "
            "VALUES ('gen_system', '生成侧·System 铁律', :content, 4, false, :reason)"
        ), {"content": _GEN_SYSTEM_V4, "reason": _V4_REASON})
    # 2) gen_system_live: v1 留痕 + v2 激活(v1 为空库 seed; 已装库 v1 已存在→去激活,
    #    插入 v2 生效。同 code 至多一个生效(部分唯一索引), 幂等)
    _live1 = conn.execute(
        sa.text("SELECT 1 FROM prompt_configs WHERE code='gen_system_live' AND version=1")
    ).scalar()
    if not _live1:
        conn.execute(sa.text(
            "INSERT INTO prompt_configs (code, name_cn, content, version, is_active, change_reason) "
            "VALUES ('gen_system_live', '生成侧·实时问答 System', :content, 1, false, :reason)"
        ), {"content": _GEN_SYSTEM_LIVE_V1, "reason": _LIVE_V1_REASON})
    conn.execute(sa.text(
        "UPDATE prompt_configs SET is_active=false WHERE code='gen_system_live' AND version<2"
    ))
    _live2 = conn.execute(
        sa.text("SELECT 1 FROM prompt_configs WHERE code='gen_system_live' AND version=2")
    ).scalar()
    if not _live2:
        conn.execute(sa.text(
            "INSERT INTO prompt_configs (code, name_cn, content, version, is_active, "
            "activated_at, change_reason) "
            "VALUES ('gen_system_live', '生成侧·实时问答 System', :content, 2, true, now(), :reason)"
        ), {"content": _GEN_SYSTEM_LIVE_V2, "reason": _LIVE_V2_REASON})
    # 3) focus 卡修复(仅当仍是 seed 原文时更新, 运营手工改过的不动)
    for row in _focus_updates():
        res = conn.execute(
            sa.text("UPDATE ai_focus_configs SET content=:new, updated_by='issue307', "
                    "updated_at=now() WHERE code=:code AND content=:old"),
            {"code": row["code"], "old": row["old"], "new": row["new"]},
        )
        if res.rowcount:
            logger.info("  focus card updated: %s", row["code"])
        else:
            logger.warning(
                "  focus card skipped (content 不匹配旧 seed, 疑运营已手工修改): %s", row["code"]
            )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DELETE FROM prompt_configs WHERE code='gen_system' AND version=4"))
    # live: 删 v2, v1 重激活(回到首发布态)
    conn.execute(sa.text("DELETE FROM prompt_configs WHERE code='gen_system_live' AND version=2"))
    conn.execute(sa.text(
        "UPDATE prompt_configs SET is_active=true, activated_at=now() "
        "WHERE code='gen_system_live' AND version=1"
    ))
    # focus 卡不回滚(文案修正无副作用, 且 WHERE 原文已不在)
