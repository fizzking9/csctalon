"""B4-1 (#257): 指标字典表 - metric_dictionary 口径单点登记 + 种子 19 条

Revision ID: b4_1_metric_dictionary
Revises: b3_7_person_skill_tags
Create Date: 2026-10-09 00:00:00.000000

依据: 排期 §七 B4-1 (S-P1-1); AGENTS 界面纪律"卡片必须有数据说明"。

范围:
1. metric_dictionary 表: code PK / name_cn 全站唯一 / definition 必填 /
   formula(JSONB: source/aggregation/filter/window) / unit / category /
   source_tables(JSONB) / owner / is_active / remark
2. 种子 19 条高频核心指标, 口径**逐条对照代码现状**核对
   (today/_kpi.py #81 v4 四路退款 / #78 接待分层 / complaint.py #81 三源合计),
   code 命名 = 域.名, 与看板接口实现同源(见各行 definition 中的实现指针)。

downgrade: 新表 DROP 即安全回退, 无业务数据损失。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = 'b4_1_metric_dictionary'
down_revision: Union[str, None] = 'b3_7_person_skill_tags'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 种子指标(口径以代码现状为准; definition 里写实现指针, 谁改口径谁更新字典)
_SEEDS = [
    # ── 接待域 (today/_kpi.py today_kpi + reception.py) ──
    {
        "code": "reception.session_count", "name_cn": "会话数(次)",
        "definition": "窗口内 cs_records 记录条数(operation_time ∈ 窗口), 含真人/AI全自动/系统/客户自助四类(各 kind 之和 == 本值, #78 分层不变式)。前端显示名 '会话数(次)'。",
        "formula": {"source": "cs_records", "aggregation": "count(*)", "filter": "operation_time ∈ [start, end]", "window": "看板当前窗口(日/周/月)"},
        "unit": "次", "category": "reception",
        "source_tables": ["cs_records"],
        "owner": "运营组", "remark": "#78 接待来源分层; 实现: today/_kpi.py today_kpi #1",
    },
    {
        "code": "reception.device_count", "name_cn": "接待设备数(个)",
        "definition": "窗口内 cs_records.device_sn 去重数(NULL/空串排除)。1台设备多次进线算1次, 体现覆盖面。AI/真人跨类重叠时按全量去重(不按 kind 拆分)。",
        "formula": {"source": "cs_records", "aggregation": "count(distinct nullif(trim(device_sn),''))", "filter": "operation_time ∈ [start, end]", "window": "同会话数"},
        "unit": "个", "category": "reception",
        "source_tables": ["cs_records"],
        "owner": "运营组", "remark": "实现: today/_kpi.py today_kpi #1b",
    },
    {
        "code": "reception.staff_on_duty", "name_cn": "在岗人数(人)",
        "definition": "窗口内 cs_records.operator 去重数(NULL 排除)。⚠️ 未排除系统/AI 账户(历史口径, 与人效表真人口径不同, 勿混用)。",
        "formula": {"source": "cs_records", "aggregation": "count(distinct operator)", "filter": "operation_time ∈ [start, end] AND operator IS NOT NULL", "window": "同会话数"},
        "unit": "人", "category": "reception",
        "source_tables": ["cs_records"],
        "owner": "运营组", "remark": "实现: today/_kpi.py today_kpi #6",
    },
    {
        "code": "reception.ticket_done", "name_cn": "完工工单(单)",
        "definition": "status='已完成' 的工单, 按**有效完工时间推算**计数: 真实完成样本(白名单) → handle_duration 秒 → handler 近30天真实平均 → 全局真实平均。finish_time 89% 被批量覆盖, 不能直接用。",
        "formula": {"source": "service_tickets", "aggregation": "count(*)", "filter": "status='已完成' AND 有效完工时间 ∈ [start, end]", "window": "同会话数"},
        "unit": "单", "category": "reception",
        "source_tables": ["service_tickets"],
        "owner": "运营组", "remark": "实现: complaint.py _ticket_effective_finish_time_where; 背景 docs/reports/20260801-数据时间审核-审计报告.md",
    },
    {
        "code": "reception.ai_takeover_session_pct", "name_cn": "AI接管率(会话)",
        "definition": "AI 全自动会话占比 = operator='wechat' 的会话数 / 会话总数 ×100%(#78 接待来源分层, 判据=operator+description 文本模式, 见 system_accounts.classify_cs_record)。",
        "formula": {"source": "cs_records", "aggregation": "sum(kind=AI_AUTO)/count(*)", "filter": "operation_time ∈ [start, end]", "window": "同会话数"},
        "unit": "%", "category": "reception",
        "source_tables": ["cs_records"],
        "owner": "运营组", "remark": "#78; 架构 docs/arch/A-20260913-001-AI接待口径分层.md",
    },
    {
        "code": "reception.ai_takeover_device_pct", "name_cn": "AI接管率(设备)",
        "definition": "AI 独有 SN 占比 = (AI 覆盖 SN − 人机共管 SN) / 接待设备数 ×100%。分母为 device_count, 共管设备归真人侧不计入 AI 接管。",
        "formula": {"source": "cs_records", "aggregation": "(ai_sn - overlap_sn)/device_count", "filter": "operation_time ∈ [start, end]", "window": "同会话数"},
        "unit": "%", "category": "reception",
        "source_tables": ["cs_records"],
        "owner": "运营组", "remark": "#78; device_shared/device_ai_total 随 KPI 透出",
    },
    # ── 绩效域 (today/_staff.py today_staff_efficiency) ──
    {
        "code": "performance.efficiency_score", "name_cn": "工作效率值",
        "definition": "双合成指标: 工作量分 = Σ(专项会话数 × 专项难度系数 × 本职系数); 效率分 = cs_record 接待SN ∪ ServiceTicket 处理SN 跨表去重数。本职系数 1.0 / 转接固定 0.2(不叠加难度); 管理岗/专员岗跨类全豁免(三级豁免判定)。前端显示 'n次(m个)'。系统/自助/AI 虚拟账户不进真人排名。",
        "formula": {"source": "cs_records + service_tickets", "aggregation": "工作量分=Σ(会话×难度×本职系数); 效率分=SN跨表union去重", "filter": "排除系统自动记录(system_accounts 黑名单)", "window": "同会话数"},
        "unit": "值", "category": "performance",
        "source_tables": ["cs_records", "service_tickets"],
        "owner": "运营组", "remark": "v3 转接系数(2026-08-09); 权重表 TRANSFER_PENALTY=0.2; 实现: today/_staff.py today_staff_efficiency",
    },
    # ── 退款域 (today/_kpi.py v4 四路核算; 实现 analytics/cash.py _cash_routes_total) ──
    {
        "code": "refund.count", "name_cn": "退款笔数",
        "definition": "四路经营合计 A+B+C+D(经营侧登记的所有退款出款), 各路按各自时间列 ∈ 窗口。⚠️ 与 metric_registry 的 refund_count 同名同源(#83 已修)。",
        "formula": {"source": "A+B+C+D 四路", "aggregation": "Σcount", "filter": "各路时间列 ∈ [start, end]", "window": "同会话数"},
        "unit": "笔", "category": "refund",
        "source_tables": ["refunds", "wallet_withdrawals", "shanmao_refunds", "manual_refunds"],
        "owner": "运营组", "remark": "v4 统一口径(2026-08-10); 实现: today/_kpi.py today_kpi #2/3/4",
    },
    {
        "code": "refund.amount", "name_cn": "退款金额",
        "definition": "四路经营合计金额 A+B+C+D(元), = 退款笔数同窗口的金额维度。",
        "formula": {"source": "A+B+C+D 四路", "aggregation": "Σamount", "filter": "各路时间列 ∈ [start, end]", "window": "同会话数"},
        "unit": "元", "category": "refund",
        "source_tables": ["refunds", "wallet_withdrawals", "shanmao_refunds", "manual_refunds"],
        "owner": "运营组", "remark": "金额定点数(AGENTS 铁律4); 出参 float 表示层转换",
    },
    {
        "code": "refund.route_a", "name_cn": "A路·线上直退",
        "definition": "refunds(BMS退费单)按 created_at_raw ∈ 窗口计入经营退款。补充口径 refund_done_today: status='已退款' 且 updated_at_raw(实际退款时刻) ∈ 当日, 含几天前创建今天完成的单(BMS 实际退款时间口径)。",
        "formula": {"source": "refunds", "aggregation": "count+sum(refund_amount)", "filter": "created_at_raw ∈ 窗口(_route_a_online_refund_filter)", "window": "同会话数"},
        "unit": "元", "category": "refund",
        "source_tables": ["refunds"],
        "owner": "运营组", "remark": "E路对账基准勿与A路混用(易误用口径已在数据源树标注)",
    },
    {
        "code": "refund.route_b", "name_cn": "B路·用户取现",
        "definition": "wallet_withdrawals 用户取现成功计入经营退款。",
        "formula": {"source": "wallet_withdrawals", "aggregation": "count+sum(amount)", "filter": "_route_b_withdraw_filter", "window": "同会话数"},
        "unit": "元", "category": "refund",
        "source_tables": ["wallet_withdrawals"],
        "owner": "运营组", "remark": "实现: analytics/cash.py",
    },
    {
        "code": "refund.route_c", "name_cn": "C路·闪猫退款",
        "definition": "shanmao_refunds 独立表, status='已退款' 计入经营退款。",
        "formula": {"source": "shanmao_refunds", "aggregation": "count+sum(paid_amount)", "filter": "status='已退款' AND 时间列 ∈ 窗口", "window": "同会话数"},
        "unit": "元", "category": "refund",
        "source_tables": ["shanmao_refunds"],
        "owner": "运营组", "remark": "实现: analytics/cash.py _route_c_shanmao_trend",
    },
    {
        "code": "refund.route_d", "name_cn": "D路·手动退款",
        "definition": "manual_refunds 独立业务表, is_refunded='是' 计入经营退款(去重键见数据采集合并去重机制说明)。",
        "formula": {"source": "manual_refunds", "aggregation": "count+sum(refund_amount)", "filter": "is_refunded='是' AND 时间列 ∈ 窗口", "window": "同会话数"},
        "unit": "元", "category": "refund",
        "source_tables": ["manual_refunds"],
        "owner": "运营组", "remark": "实现: analytics/cash.py _route_d_other_trend",
    },
    # ── 现金流域 ──
    {
        "code": "cash.actual_cashflow", "name_cn": "实际现金流",
        "definition": "refund_record_reports 退款成功合计(不分 ORIGINAL/BALANCE 桶), 是**对账基准**(2026-09-05 v1.1 起替代 merchant_refunds)。商户号流水 T+1 导入, 本日可能为空。",
        "formula": {"source": "refund_record_reports", "aggregation": "sum(退款成功金额)", "filter": "_route_e_cashflow_filter", "window": "同会话数"},
        "unit": "元", "category": "cash",
        "source_tables": ["refund_record_reports"],
        "owner": "运营组", "remark": "对账基准, 勿与经营估算混淆(数据源树已标注)",
    },
    {
        "code": "cash.recon_diff", "name_cn": "对账偏差",
        "definition": "对账偏差 = 经营估算(A+B+C+D) − 实际现金流(E)。为 0 表示两套账齐平; 持续为正说明经营侧多记或 E 少导。",
        "formula": {"source": "四路合计 vs refund_record_reports", "aggregation": "operational.amount - actual_cashflow.amount", "filter": "同窗口", "window": "同会话数"},
        "unit": "元", "category": "cash",
        "source_tables": ["refunds", "wallet_withdrawals", "shanmao_refunds", "manual_refunds", "refund_record_reports"],
        "owner": "运营组", "remark": "实现: cash.py _cash_routes_total 的 diff",
    },
    # ── 投诉域 (#81 口径统一到唯一出口) ──
    {
        "code": "complaint.total", "name_cn": "投诉数",
        "definition": "三源合计 = 内部投诉(工单投诉类) + 商户号投诉 + 外部投诉(含补充文件), **不含卡商**(#53 卡商全部单算)。判据与投诉专项四卡同源(complaint_scope 分桶), 已扣除补充表中已关联工单的重叠记录。",
        "formula": {"source": "service_tickets + external_complaints(+补充)", "aggregation": "count(registered)", "filter": "_sources_reg_handled 判据", "window": "同会话数"},
        "unit": "单", "category": "complaint",
        "source_tables": ["service_tickets", "external_complaints"],
        "owner": "运营组", "remark": "#81 口径统一(2026-09-13); 实现: complaints.py _sources_reg_handled",
    },
    {
        "code": "complaint.repeat_rate", "name_cn": "重复投诉率",
        "definition": "代理指标: 窗口内同 SN 出现 ≥2 次投诉的 SN 占比(重复投诉分布 reps>1)。⚠️ 禁止改叫「升级率」—— 这是升级的**代理信号**, 非真实升级率(真实值待升级投诉表到位)。",
        "formula": {"source": "三源投诉(同 complaint.total)", "aggregation": "reps≥1 的 SN / 去重 SN", "filter": "同 complaint.total", "window": "同会话数"},
        "unit": "%", "category": "complaint",
        "source_tables": ["service_tickets", "external_complaints", "card_complaints"],
        "owner": "运营组", "remark": "complaint.py complaint_health_metrics; 口径权威定义见投诉渠道口径与对账说明",
    },
    {
        "code": "complaint.card_total", "name_cn": "(外)卡商投诉数",
        "definition": "卡商投诉 = 工单侧真卡商 + card_complaints − 重叠(与 /summary 同公式)。⚠️ 独立统计, **不计入** complaint.total(#53 用户口径)。",
        "formula": {"source": "service_tickets(卡商) + card_complaints", "aggregation": "count − 重叠", "filter": "#65 软纠正判据", "window": "同会话数"},
        "unit": "单", "category": "complaint",
        "source_tables": ["service_tickets", "card_complaints"],
        "owner": "运营组", "remark": "#53/#65; 渠道口径权威定义见 docs/standards/投诉渠道口径与对账说明.md",
    },
    # ── 退款挽回 ──
    {
        "code": "recovery.rate", "name_cn": "退款挽回成功率",
        "definition": "退款挽回专项同口径(refund_recovery_analysis): 挽回成功单 / 挽回跟进单。higher_better, 阈值 40/30/20(#82 准入: 口径同源 + 阈值经分布验证)。",
        "formula": {"source": "refund_recovery_analysis", "aggregation": "recovered/followed", "filter": "专项判据", "window": "同会话数"},
        "unit": "%", "category": "performance",
        "source_tables": ["refunds", "manual_refunds"],
        "owner": "运营组", "remark": "#82; 实现: metric_registry_service._eval_recovery_rate",
    },
]


def upgrade() -> None:
    op.create_table(
        'metric_dictionary',
        sa.Column('code', sa.String(length=64), primary_key=True, comment='指标code(域.名, 如 reception.session_count)'),
        sa.Column('name_cn', sa.String(length=128), nullable=False, comment='显示名(全站唯一)'),
        sa.Column('definition', sa.Text(), nullable=False, comment='口径语义: 含什么/排什么/时点'),
        sa.Column('formula', JSONB(), nullable=True,
                  comment='结构化公式: {source, aggregation, filter, window}'),
        sa.Column('unit', sa.String(length=16), nullable=True, comment='单位: 个/次/人/笔/元/%/值'),
        sa.Column('category', sa.String(length=16), nullable=False, comment='分类: reception/refund/complaint/cash/performance'),
        sa.Column('source_tables', JSONB(), nullable=True, comment='来源表清单(供下钻/健康检查)'),
        sa.Column('owner', sa.String(length=32), nullable=True, comment='口径负责人'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true'),
                  comment='是否现行口径(停用不删, 留历史引用可查)'),
        sa.Column('remark', sa.Text(), nullable=True, comment='备注(变更背景/工单号)'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        comment='指标字典: 口径单点登记(#257 B4-1)',
    )
    op.create_index('ix_metric_dictionary_category', 'metric_dictionary', ['category'])
    op.create_index('ix_metric_dictionary_name_cn', 'metric_dictionary', ['name_cn'], unique=True)

    # 种子 19 条(幂等: ON CONFLICT DO NOTHING, 重复执行不报错)
    for s in _SEEDS:
        op.execute(
            sa.text("""
                INSERT INTO metric_dictionary
                    (code, name_cn, definition, formula, unit, category,
                     source_tables, owner, is_active, remark)
                VALUES (:code, :name_cn, :definition,
                        CAST(:formula AS jsonb), :unit, :category,
                        CAST(:source_tables AS jsonb), :owner, true, :remark)
                ON CONFLICT (code) DO NOTHING
            """).bindparams(
                code=s["code"], name_cn=s["name_cn"], definition=s["definition"],
                formula=json_dumps(s.get("formula")), unit=s.get("unit"),
                category=s["category"], source_tables=json_dumps(s.get("source_tables")),
                owner=s.get("owner"), remark=s.get("remark"),
            )
        )


def json_dumps(v):
    """JSONB 绑定参数序列化( None → NULL )。"""
    import json
    return json.dumps(v, ensure_ascii=False) if v is not None else None


def downgrade() -> None:
    op.drop_index('ix_metric_dictionary_name_cn', table_name='metric_dictionary')
    op.drop_index('ix_metric_dictionary_category', table_name='metric_dictionary')
    op.drop_table('metric_dictionary')
