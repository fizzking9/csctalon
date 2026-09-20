"""提示词版本化配置表 prompt_configs (工单 #178)

设计: #178 B（两级提示词 DB 化 + 版本化）

两级提示词（生成侧 _SYSTEM_PROMPT / 摘取侧 _EXTRACT_SYSTEM）从代码常量升级为
DB 可迭代 + 版本化：代码常量保留为兜底默认（提示词铁律），DB 行只有
`is_active=TRUE` 时才生效。

⚠️ seed 策略（零回归关键）：
- seed 两行（gen_system / extract_system）**is_active=FALSE** —— 初始不激活，
  加载侧「DB 无生效行 → 回退代码常量」，升级后行为与现状完全一致；
- 管理员经版本管理激活新版本后才 DB 优先，且旧版本行保留可回滚。

⚠️ revision id 说明：初版误用 a1b2c3d4e5f6（与 add_shanmao_phone_paid_amount 撞车），
  换为 e8d7c6b5a4f3（已 grep 全 versions 确认唯一）。

Revision ID: e8d7c6b5a4f3
Revises: s9t0u1v2w3x4
Create Date: 2026-09-21
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'e8d7c6b5a4f3'
down_revision: Union[str, None] = 's9t0u1v2w3x4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'prompt_configs',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('code', sa.String(length=64), nullable=False,
                  comment='提示词标识（gen_system=生成侧 / extract_system=摘取侧）'),
        sa.Column('name_cn', sa.String(length=128), nullable=False, comment='显示名'),
        sa.Column('content', sa.Text(), nullable=False, comment='提示词正文'),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1',
                  comment='版本号（每次迭代递增；旧版本行保留可回滚）'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('false'),
                  comment='生效标记；TRUE=当前生效版本（每 code 至多一个），FALSE=历史/草稿版本'),
        sa.Column('baseline', sa.String(length=255), nullable=True,
                  comment='激活时基线指标（命中率/live 占比，金丝雀验证回填）'),
        sa.Column('created_by', sa.Integer(), nullable=True, comment='创建人 user_id'),
        sa.Column('activated_at', sa.DateTime(timezone=True), nullable=True, comment='激活时刻'),
        sa.Column('activated_by', sa.Integer(), nullable=True, comment='激活人 user_id'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('now()'), comment='创建时刻'),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('now()'), comment='更新时间'),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('ix_prompt_configs_code', 'code'),
        # 同一 code 至多一个生效版本（部分唯一索引，与模型 Index 镜像）
        sa.Index('uq_prompt_configs_active_code', 'code', unique=True,
                 postgresql_where=sa.text('is_active')),
    )

    # seed 初始版本（is_active=FALSE → 不生效，加载回退代码常量，零回归）
    # 内容 = 当前代码常量快照，仅作初始版本留痕；管理员激活后才 DB 优先
    _gen_seed = (
        "你是本客服/经营系统的数据分析助手。你的职责是基于【给定数据】回答经营/退款/投诉/接待问题。\n"
        "铁律:\n"
        "1. 所有数字必须来自【给定数据】, 严禁编造、外推或臆测; 数据中没有的写\"暂无/未提供\"。\n"
        "2. 严格按【业务口径】理解指标, 不要自创定义。\n"
        "3. 回答中文, 结论注明数据窗口(如 今日/本周/本月 截至时间)。\n"
        "   **结构要求**: 周报/月报**必须**按 H2 锚点结构化输出, 锚点固定为 "
        "`## 接待分析` / `## 退款分析` / `## 投诉分析` / `## 趋势对比` / `## 结论建议`"
        "(无数据的段写\"暂无\", 不要省略锚点); 日报保持简洁, 不强制 H2。\n"
        "   **长度约束**: 前台实时问答 ≤500 字(突出核心结论, 不展开逐项明细; 超长会被截断); "
        "后台预生成草稿(数据窗口说明含\"预生成\"字样时)可详尽展开, 不受此限。\n"
        "4. 只回答与经营数据相关的问题; 无关问题礼貌说明无法回答。\n"
        "5. 金额一律以元为单位。\n"
        "6. **回答开头必须写明本次统计窗口**(直接引用【数据窗口说明】里的起止时间), 让用户能核对窗口"
        "是否符合预期; 若用户问的时间范围与给定窗口不一致, 先指出差异再作答, 不要默默按别的窗口回答。\n"
        "7. 给定数据里的 yoy_value/yoy_pct 是\"前一个等长周期\"的对比值, **不是另一天的独立明细**; "
        "不要把它当成完整的一天数据来陈述。若用户要某天的明细而该窗口未提供, 如实说明。\n"
        "8. **防主观推断**: 不同口径的数字(如 registered=窗口内新登记 vs handled=窗口内完成含历史单)"
        "禁止直接相减或比较大小; 必须显式标注口径差异后再下结论。"
        "禁止做\"处理量 > 登记量 → 消化存量\"这类无证据因果推断; "
        "数据未提供的归因(如\"退款上升因 XX 活动\")禁止编造, 一律写\"原因需结合运营侧确认\"。\n"
        "9. **业务判定方向**: 退款(金额/笔数/均单价)越少越好; 接待量与会话数越多越好; "
        "投诉越少越好, 严重性 外部投诉 > 商户号投诉 > 内部投诉(外部 1 件 > 商户号 10 件); "
        "退款挽回率越高越好; 积压投诉越低越好。陈述\"环比涨跌\"后必须按此基准标注\"利好/利空\", "
        "不得只报数字不评判。\n"
        "10. **工作时间**: 客服工作时间 08:00~23:59; 非工作时间(00:00~07:59)由 AI 协助处理。"
        "解读\"接待量为 0 / 完工单为 0\"时, 必须先核对窗口是否落在非工作时间, "
        "不得直接判定\"无人值班\"或\"系统故障\"。AI 接管率上升=利好(可控指标前置)。"
    )
    _extract_seed = (
        "你是经营数据分析摘取助手。基于给定的草稿段落, 按问句摘取相关部分并排版输出。\n"
        "铁律:\n"
        "1. 只能从【给定段落】中摘取, 严禁编造、外推或添加未在段落中出现的数据。\n"
        "2. 严格按问句意图排版: 简要问给简要答, 详细问给详细答(可保留段落内明细与表格)。\n"
        "3. 段落内 `**<维度>**：` 是维度锚点行(如 `**四路分布**：A 线上直退 23 笔`); "
        "问句指向某维度时, 优先摘取该锚点行及其后内容, 锚点行写\"暂无/未提供\"时如实保留, "
        "不要从旁述/建议句里拼凑该维度的数据。\n"
        "4. 回答中文, 开头注明数据窗口(直接引用段落里出现的窗口说明)。\n"
        "5. 段落里没有的部分如实写\"未提供\", 不要从段落外内容补充。\n"
        "6. 金额单位保持与段落一致(元/分), 不要转换。"
    )
    op.bulk_insert(
        sa.table(
            'prompt_configs',
            sa.column('code', sa.String(64)),
            sa.column('name_cn', sa.String(128)),
            sa.column('content', sa.Text()),
            sa.column('version', sa.Integer()),
            sa.column('is_active', sa.Boolean()),
            sa.column('created_by', sa.Integer()),
        ),
        [
            {'code': 'gen_system', 'name_cn': '生成侧·System 铁律',
             'content': _gen_seed, 'version': 1, 'is_active': False, 'created_by': None},
            {'code': 'extract_system', 'name_cn': '摘取侧·System 提示词',
             'content': _extract_seed, 'version': 1, 'is_active': False, 'created_by': None},
        ],
    )


def downgrade() -> None:
    op.drop_table('prompt_configs')
