"""采纳治理列混入 (#332 退款五路数据标准层)

五路 ods 原始表(refunds / wallet_withdrawals / shanmao_refunds / manual_refunds /
refund_record_reports)共用的四列, 由「采纳作业」(app.services.governance.refund_adoption)
填写, 把"这行原数据是否纳入统计"的结论**表层化、版本化、可重算**:

- adopt_state    三态: adopted(采纳, 进入对应 std_* 标准表) / excluded(规则排除) /
                 pending(出现规则白名单外的未知枚举, 待业务确认, 不进 std 表);
                 NULL = 尚未跑过采纳作业。
- adopt_reason   规则码(机器结论, 如 A_EX_METHOD / B_EX_FAIL_FROZEN;
                 人工覆盖时由作业加 manual: 前缀, 规则码全集见采纳作业模块)。
- adopt_rule_ver 规则版本(首版 r20261016_v1); 规则变更须升版本并经业务确认
                 (口径文档: docs/standards/退款五路数据标准与采纳规则.md)。
- adopted_at     本次判定时间。

铁律(口径文档 FR-5): **同步 upsert 任何路径都不得写这四列** —— 新同步行四列保持
NULL, 等采纳作业判定; 冲突更新不得覆盖既有判定。通用 upsert 执行器有显式护栏,
回归测试见 tests/test_sync_preserves_adoption.py。

注: adopt_state 的 adopted/excluded 部分索引为作业批量过滤的**性能索引**, 只在
Alembic 迁移中 DDL 创建(不进 ORM __table_args__), 不影响任何正确性约束。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

# 治理四列列名集合: 同步 upsert 各路径的显式护栏统一引用,
# 保证「新增不带入、冲突更新不覆盖」(FR-5; 回归测试 tests/test_sync_preserves_adoption.py)。
ADOPT_COLUMNS = frozenset(
    {"adopt_state", "adopt_reason", "adopt_rule_ver", "adopted_at"}
)


class AdoptionColumnsMixin:
    """五路 ods 原始表共用的采纳治理四列。"""

    adopt_state: Mapped[str | None] = mapped_column(
        String(16), nullable=True,
        comment="采纳三态: adopted/excluded/pending; NULL=未跑过采纳作业",
    )
    adopt_reason: Mapped[str | None] = mapped_column(
        String(32), nullable=True,
        comment="规则码(机器结论); 人工覆盖行由作业加 manual: 前缀",
    )
    adopt_rule_ver: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="采纳规则版本(首版 r20261016_v1)"
    )
    adopted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="本次采纳判定时间"
    )
