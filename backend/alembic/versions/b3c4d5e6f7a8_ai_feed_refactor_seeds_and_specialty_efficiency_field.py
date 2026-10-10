"""ai_feed refactor: specialty efficiency_field + ai_focus_configs new seeds

AI 助手数据底座重构 (工单 #98 后续 / 2026-09-15):
1. specialties 表加 efficiency_field 字段(替代 external._BUCKET 硬编码)
2. 把已合并到"投诉专项"的历史专项"内部投诉"/"外部投诉" is_active 置 False
   (代码层 _reception_specialty 只过滤 is_active=True 即可, 不再靠 LEGACY 常量名硬编码过滤)
3. ai_focus_configs 表插入 4 条新 seed(business-benchmark / work-time-ai-assist /
   inference-discipline / ai-incremental-analysis) — 业务判定基准/工作时间/AI 协助/
   防主观推断指导/AI 增量分析任务全部 DB 化, 运营可通过 /ai/focus-configs REST 路由增改
4. UPDATE 现有 focus-complaint / focus-reception seed 修正过时口径(原 focus-complaint
   含"处理滞后于登记说明积压恶化"是错误推断; 原 focus-reception 列出六个专项但
   内部/外部已合并到投诉专项)

Revision ID: b3c4d5e6f7a8
Revises: e5f6a7b8c9d0
Create Date: 2026-09-15 11:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'b3c4d5e6f7a8'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ── ai_focus_configs 新增 4 条 seed (业务判定基准/工作时间/防主观推断/AI 增量分析) ──
_NEW_FOCUS_SEEDS = [
    {
        "code": "business-benchmark",
        "scope_key": "global",
        "title": "业务判定基准",
        "sort_order": 2,
        "content": (
            "业务判定基准(方向性判据, 用于判定'好/坏'与'风险/机会'): "
            "退款越少越好(金额/笔数); 均单价越低越好; 退款单数越少越好; "
            "接待量越多越好; 接待会话数越多越好; "
            "投诉越少越好; 投诉严重性 外部投诉 > 商户号投诉 > 内部投诉(即外部投诉 1 件 > 商户号 10 件); "
            "投诉处理越及时越好(handle - register 间隔越短越好); "
            "退款挽回率越高越好(挽回成功/分母); "
            "积压投诉越低越好(窗口内登记未闭环). "
            "陈述'环比涨跌'后必须按此基准标注'利好/利空', 不得只报数字不评判."
        ),
    },
    {
        "code": "work-time-ai-assist",
        "scope_key": "global",
        "title": "工作时间与 AI 协助",
        "sort_order": 3,
        "content": (
            "客服工作时间 08:00~23:59; 非工作时间(00:00~07:59)由 AI 协助处理. "
            "解读'接待量为 0 / 完工单为 0'时, 必须先核对窗口是否落在非工作时间, "
            "不得直接判定'无人值班'或'系统故障'. "
            "AI 接管率(ai_takeover)是可控指标前置: 接待量可升可控, AI 接管率上升=利好."
        ),
    },
    {
        "code": "inference-discipline",
        "scope_key": "global",
        "title": "防主观推断指导",
        "sort_order": 4,
        "content": (
            "防主观推断指导: "
            "① 不同口径的数字(如 registered=窗口内新登记 vs handled=窗口内完成含历史单)禁止直接相减或比较大小; "
            "必须显式标注口径差异后再下结论. "
            "② 禁止做'处理量 > 登记量 → 消化存量'这类无证据因果推断; "
            "如需描述关系, 只能说'本期 handled 高于 registered, 差额源于窗口外登记、窗口内办结的历史单'等可验证陈述. "
            "③ 数据未提供的归因(如'退款上升因 XX 活动')禁止编造, 一律写'原因需结合运营侧确认'. "
            "④ yoy_value/yoy_pct 是上一等长周期汇总, 不是另一天的独立明细, 不得当独立日数据展开陈述."
        ),
    },
    {
        "code": "ai-incremental-analysis",
        "scope_key": "global",
        "title": "AI 增量分析任务",
        "sort_order": 5,
        "content": (
            "当用户问'分析/总览/怎么看/风险/异常'类开放题时(纯取数问句跳过本段), 增量分析任务: "
            "- 口径差异提醒: 当数据里出现 registered 与 handled 这类不同口径数字(见口径卡), 必须显式标注口径, 禁止直接相减或推断因果; "
            "- 环比异常预警: yoy_pct 超 ±30% 的指标, 必须点名并给出可能原因方向(数据/运营/外部, 不下结论); "
            "- 风险与机会: 按 business_benchmark 卡的方向判定(如'退款金额上升=风险'), 每类最多 1 条; "
            "- 下钻建议: 给出'看哪个看板卡片/下钻到哪一层'的可执行建议(不重复数据)."
        ),
    },
]

# ── 修正现有 seed (focus-complaint / focus-reception 过时口径) ──
_FOCUS_UPDATES = {
    "focus-complaint": {
        # 原: ②登记(窗口新增)与处理(真实办结)要分开看, 处理滞后于登记说明积压恶化
        # 改: ②登记(窗口新增)与处理(真实办结)是不同口径, 禁止直接相减或推断因果;
        #     处理>登记属正常(含历史单办结), 不必然意味'消化存量'
        "content": (
            "投诉分析关注点: ①三源(内部/商户号/外部)结构, 外部平台投诉最需重视; "
            "②登记(窗口新增)与处理(真实办结)是不同口径, 禁止直接相减或推断因果; "
            "处理>登记属正常(含历史单办结), 不必然意味'消化存量'; "
            "③积压(未闭环存量)是核心风险, 抬升须提示; ④措辞区分'登记量/处理量/积压', 不要混用."
        ),
    },
    "focus-reception": {
        # 原: ②六个专项(咨询/套餐/退款处理/退款挽回/内部投诉/外部投诉)的会话与设备规模
        # 改: ②五个正式专项(咨询接待/套餐专项/退款处理/退款挽回/投诉专项), 历史已合并
        "content": (
            "接待分析关注点: ①总体接待量与服务设备数(SN去重)看覆盖面; "
            "②五个正式专项(咨询接待/套餐专项/退款处理/退款挽回/投诉专项)的会话与设备规模, "
            "历史专项(内部投诉/外部投诉)已合并到投诉专项, "
            "指出量大的专项与负责人(lead); ③环比(同比前移等长窗口)变化要解释可能原因; "
            "④接待量含智能助手处理(cs_record), 口径与'人工工单'不同勿混。"
        ),
    },
}

# 6 个正式专项的 efficiency_field 填值(对应 today_staff_efficiency 的 sp_*/pp_* 字段)
# 注: "内部投诉"/"外部投诉" 不填 efficiency_field(is_active=False 不参与)
_SPECIALTY_EFFICIENCY_FIELDS = {
    "咨询接待": "sp_zixun,pp_zixun",
    "套餐专项": "sp_taocan,pp_taocan",
    "退款挽回": "sp_recovery,pp_recovery",
    "退款处理": "sp_processing,pp_processing",
    "投诉专项": "sp_internal,pp_internal",  # 投诉类合并到此桶
}


def upgrade() -> None:
    # ── 1. specialties 表加 efficiency_field 字段 ──
    op.add_column(
        'specialties',
        sa.Column(
            'efficiency_field',
            sa.String(length=64),
            nullable=True,
            comment="对应 today_staff_efficiency 的字段名, 格式 'sp_xxx,pp_xxx'; NULL=不参与会话/设备统计",
        ),
    )

    # ── 2. 给 5 个正式专项填 efficiency_field 值 ──
    conn = op.get_bind()
    for name, field in _SPECIALTY_EFFICIENCY_FIELDS.items():
        conn.execute(
            sa.text("UPDATE specialties SET efficiency_field = :field WHERE name = :name AND is_active = true"),
            {"field": field, "name": name},
        )

    # ── 3. 把"内部投诉"/"外部投诉"两条历史专项的 is_active 置 False ──
    # (已合并到"投诉专项", DB 治理后代码层 _reception_specialty 只过滤 is_active=True 即可)
    # 注: LEGACY_COMPLAINT_SPECIALTIES 常量保留(analytics.py 用于 SCD 历史归因, 不影响)
    conn.execute(
        sa.text("UPDATE specialties SET is_active = false WHERE name IN ('内部投诉', '外部投诉')")
    )

    # ── 4. ai_focus_configs 插入 4 条新 seed (幂等: code 唯一, 已存在则跳过) ──
    for seed in _NEW_FOCUS_SEEDS:
        existing = conn.execute(
            sa.text("SELECT id FROM ai_focus_configs WHERE code = :code"),
            {"code": seed["code"]},
        ).scalar()
        if existing:
            # 已存在(可能是历史迁移重复执行), 跳过插入
            continue
        conn.execute(
            sa.text(
                "INSERT INTO ai_focus_configs (code, scope_key, title, content, sort_order, enabled, created_by) "
                "VALUES (:code, :scope_key, :title, :content, :sort_order, true, 'seed-b3c4d5e6f7a8')"
            ),
            seed,
        )

    # ── 5. UPDATE 现有 focus-complaint / focus-reception seed 修正过时口径 ──
    for code, update_fields in _FOCUS_UPDATES.items():
        conn.execute(
            sa.text("UPDATE ai_focus_configs SET content = :content, updated_by = 'seed-b3c4d5e6f7a8' WHERE code = :code"),
            {"code": code, "content": update_fields["content"]},
        )


def downgrade() -> None:
    conn = op.get_bind()

    # 1. 还原 focus-complaint / focus-reception seed 内容(回退到 a1f0c2e4d6b8 的原 seed)
    conn.execute(
        sa.text(
            "UPDATE ai_focus_configs SET content = :content, updated_by = NULL WHERE code = 'focus-complaint'"
        ),
        {"content": (
            "投诉分析关注点: ①三源(内部/商户号/外部)结构, 外部平台投诉最需重视; "
            "②登记(窗口新增)与处理(真实办结)要分开看, 处理滞后于登记说明积压恶化; "
            "③积压(未闭环存量)是核心风险, 抬升须提示; ④措辞区分'登记量/处理量/积压', 不要混用。"
        )},
    )
    conn.execute(
        sa.text(
            "UPDATE ai_focus_configs SET content = :content, updated_by = NULL WHERE code = 'focus-reception'"
        ),
        {"content": (
            "接待分析关注点: ①总体接待量与服务设备数(SN去重)看覆盖面; "
            "②六个专项(咨询/套餐/退款处理/退款挽回/内部投诉/外部投诉)的会话与设备规模, "
            "指出量大的专项与负责人(lead); ③环比(同比前移等长窗口)变化要解释可能原因; "
            "④接待量含智能助手处理(cs_record), 口径与'人工工单'不同勿混。"
        )},
    )

    # 2. 删除 4 条新 seed
    for seed in _NEW_FOCUS_SEEDS:
        conn.execute(
            sa.text("DELETE FROM ai_focus_configs WHERE code = :code AND created_by = 'seed-b3c4d5e6f7a8'"),
            {"code": seed["code"]},
        )

    # 3. 还原"内部投诉"/"外部投诉" is_active = True
    conn.execute(
        sa.text("UPDATE specialties SET is_active = true WHERE name IN ('内部投诉', '外部投诉')")
    )

    # 4. 清空 efficiency_field 值
    conn.execute(sa.text("UPDATE specialties SET efficiency_field = NULL"))

    # 5. 删除 efficiency_field 字段
    op.drop_column('specialties', 'efficiency_field')
