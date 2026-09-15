"""fix_specialty_lead_duplicates_and_stale_leads

#90: 专项负责人硬编码遗留 + 历史表 is_current 重复 → 「唐彩慧/董梦玲 是负责人」

## 问题（2026-09-14 用户反馈 + 生产实测）

用户：「明明有清晰的负责人、组长划分，为什么会出现唐彩绘是负责人、
董梦玲是负责人的说法？是不是有硬编码？」

**是硬编码，且是三层叠加的缺陷**：

### ① 硬编码人名（原罪）
`scripts/init_groups_specialties.py` 把负责人**写死人名**：
```python
specialty_leaders = {
    "complaint": "张蕾",
    "refund_recovery": "唐彩慧",   # 用户已明确「他不是 lead」
    "package": "朱海洋",
    "consulting": "董梦玲",
}
```
该脚本**无幂等守卫**，重复执行会直接覆盖 `specialties.current_lead_id`。

### ② 生产指针未按用户决策收敛
`scripts/apply_org_change_20260914.py` 的 `SPECIALTY_LEAD`（用户第三批决策
「退款挽回 + 套餐 两专项组长都是吴晓龙」）**从未生效** —— 生产实测：

| id | code | 专项 | current_lead_id | 人员 | 应有 |
|---|---|---|---|---|---|
| 3 | package | 套餐专项 | 151 | 朱海洋 | 吴晓龙 |
| 4 | refund_recovery | 退款挽回 | 207 | 唐彩慧 | 吴晓龙 |
| 1 | consulting | 咨询接待 | 146 | 董梦玲 | 保持/待确认 |

### ③ 历史表 is_current 重复且无唯一约束（真正无法自愈的原因）
```
id | specialty | person   | is_current | source    | created
3  | 套餐专项   | 朱海洋   | t          | migration | 08-09
9  | 套餐专项   | 吴晓龙   | t          | manual    | 06:07
7  | 套餐专项   | 吴晓龙   | t          | manual    | 06:40   ← 重复 2 条
2  | 退款挽回   | 唐彩慧   | t          | migration | 08-09   ← 旧职未卸
6  | 退款挽回   | 吴晓龙   | t          | manual    | 06:07
8  | 退款挽回   | 吴晓龙   | t          | manual    | 06:40   ← 重复
```
两个独立 bug：
- **卸任查询少维度**：`apply_org_change` 卸任时按
  `(specialty_id, person_id, is_current)` 查 → 查「吴晓龙」查不到（他无旧岗）
  → **旧 lead（唐彩慧/朱海洋）从未被卸任**；
- **`__main__` 重复执行**：新任幂等守卫查 `(specialty_id, role, is_current).scalar()`
  → 命中吴晓龙就 `continue`（正确），但因**首次执行时旧记录未被卸任**，
  再次执行时该 query 返回**两条**(吴晓龙×2) → `scalar()` 报错或静默取一条，
  且每次都 `db.add` 新行 → 同一 `(specialty, person, role)` 产生 multiple current。

数据库**无** `UNIQUE (specialty_id, role) WHERE is_current` 部分唯一索引
→ 重复行可静默写入（原索引 `ix_slh_spec_role_current` 是普通 btree 索引）。

## 本迁移做三件事

### 1. 收敛重复的 is_current（每个 (specialty_id, role) 只留一条）
保留规则：`effective_from` 最大者；并列时取 `id` 最大者（最新创建）。
其余置 `is_current=false` 并补 `effective_to`，同时写入 `change_reason` 以便追溯。

### 2. 卸任陈旧 lead（用户已明确「不做该专项」）
- `refund_recovery`：唐彩慧（207）卸任 —— 用户原文「唐彩绘是专职退款处理，
  不做退款挽回，他不是 lead」
- `package`：朱海洋（151）卸任 —— 用户第三批「套餐专项组长是吴晓龙」
- 幂等：仅当指针**仍指向旧人**时才改，避免覆盖后续人工调整。

### 3. 加部分唯一索引（防复发，本次核心）
```sql
CREATE UNIQUE INDEX uq_slh_spec_role_current
  ON specialty_leadership_history (specialty_id, role) WHERE is_current;
CREATE UNIQUE INDEX uq_glh_group_role_current
  ON group_leadership_history (group_id, role) WHERE is_current;
```
此后**数据库层**拒绝第二条 current，重复行不可能再写入。

⚠️ 注意：`internal_complaint`(id=2) 与 `external_complaint`(id=6) 的
   `current_lead_id` 均为空 —— 该两专项已被 `complaint_merged` 合并，
   空值是**正确终态**，本迁移不动。

Revision ID: e5f6a7b8c9d0
Revises: c4d5e6f7a8b9
Create Date: 2026-09-14 18:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 需卸任的陈旧 lead: (专项 code, 人员姓名, 卸任原因)
STALE_LEADS = [
    ("refund_recovery", "唐彩慧",
     "2026-09-14 组织调整·卸任(用户: 唐彩慧是专职退款处理, 不做退款挽回, 不是 lead)"),
    ("package", "朱海洋",
     "2026-09-14 组织调整·卸任(用户: 套餐专项组长为吴晓龙)"),
]

# 用户决策应生效的 lead: (专项 code, 人员姓名)
#   ⚠️ 来自 apply_org_change_20260914.py 的 SPECIALTY_LEAD（该脚本未生效）
TARGET_LEADS = [
    ("refund_recovery", "吴晓龙"),
    ("package", "吴晓龙"),
]


def upgrade() -> None:
    bind = op.get_bind()

    # ── 1. 收敛重复 is_current ───────────────────────────────────────
    # 每个 (specialty_id, role) 保留 effective_from 最大者（并列取 id 最大者）。
    # 用窗口函数一次定位「应保留」的行, 其余置 false。
    dup_rows = bind.execute(sa.text("""
        SELECT id, specialty_id, role, person_id
        FROM (
            SELECT id, specialty_id, role, person_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY specialty_id, role
                       ORDER BY effective_from DESC, id DESC
                   ) AS rn
            FROM specialty_leadership_history
            WHERE is_current
        ) t
        WHERE rn > 1
    """)).fetchall()

    for row in dup_rows:
        bind.execute(sa.text("""
            UPDATE specialty_leadership_history
            SET is_current = false,
                effective_to = COALESCE(effective_to, :to_date),
                change_reason = COALESCE(change_reason, '') ||
                    ' | #90 收敛重复 current(effective_from 非最大)'
            WHERE id = :id
        """), {"id": row[0], "to_date": _today(bind)})

    # 行政组同表治理（同一缺陷形态）
    dup_groups = bind.execute(sa.text("""
        SELECT id
        FROM (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY group_id, role
                       ORDER BY effective_from DESC, id DESC
                   ) AS rn
            FROM group_leadership_history
            WHERE is_current
        ) t
        WHERE rn > 1
    """)).fetchall()

    for row in dup_groups:
        bind.execute(sa.text("""
            UPDATE group_leadership_history
            SET is_current = false,
                effective_to = COALESCE(effective_to, :to_date),
                change_reason = COALESCE(change_reason, '') ||
                    ' | #90 收敛重复 current(effective_from 非最大)'
            WHERE id = :id
        """), {"id": row[0], "to_date": _today(bind)})

    # ── 2. 卸任陈旧 lead + 补写应生效 lead ───────────────────────────
    for code, pname, reason in STALE_LEADS:
        spec = _find_specialty(bind, code)
        person = _find_person(bind, pname)
        if spec is None or person is None:
            continue
        # 幂等: 仅当指针**仍指向该旧人**时才卸任
        cur_lead = bind.execute(sa.text(
            "SELECT current_lead_id FROM specialties WHERE id = :sid"
        ), {"sid": spec}).scalar()
        if cur_lead != person:
            continue
        bind.execute(sa.text("""
            UPDATE specialty_leadership_history
            SET is_current = false,
                effective_to = COALESCE(effective_to, :to_date),
                change_reason = :reason
            WHERE specialty_id = :sid AND person_id = :pid
              AND role = 'lead' AND is_current
        """), {"sid": spec, "pid": person, "to_date": _today(bind), "reason": reason})
        # 指针暂置空（随后由 TARGET_LEADS 写入正确人）
        bind.execute(sa.text(
            "UPDATE specialties SET current_lead_id = NULL WHERE id = :sid"
        ), {"sid": spec})

    for code, pname in TARGET_LEADS:
        spec = _find_specialty(bind, code)
        person = _find_person(bind, pname)
        if spec is None or person is None:
            continue
        cur_lead = bind.execute(sa.text(
            "SELECT current_lead_id FROM specialties WHERE id = :sid"
        ), {"sid": spec}).scalar()
        if cur_lead == person:
            continue   # 已是目标人 → 跳过（幂等）
        # 若尚有其它 current lead, 一并卸任（保证唯一）
        bind.execute(sa.text("""
            UPDATE specialty_leadership_history
            SET is_current = false,
                effective_to = COALESCE(effective_to, :to_date),
                change_reason = COALESCE(change_reason, '') || ' | #90 轮岗卸任'
            WHERE specialty_id = :sid AND role = 'lead' AND is_current
        """), {"sid": spec, "to_date": _today(bind)})
        # 该人可能已有一条 current(=被上面的收敛保留的那条) → 复用, 避免重复
        existing = bind.execute(sa.text("""
            SELECT id FROM specialty_leadership_history
            WHERE specialty_id = :sid AND person_id = :pid
              AND role = 'lead' AND is_current
        """), {"sid": spec, "pid": person}).scalar()
        if existing is None:
            bind.execute(sa.text("""
                INSERT INTO specialty_leadership_history
                    (specialty_id, person_id, role, effective_from, is_current,
                     change_reason, source, created_at, updated_at)
                VALUES (:sid, :pid, 'lead', :eff, true, :reason, 'manual',
                        NOW(), NOW())
            """), {"sid": spec, "pid": person, "eff": _eff_date(bind),
                   "reason": "2026-09-14 组织架构调整（#90 补生效）"})
        bind.execute(sa.text(
            "UPDATE specialties SET current_lead_id = :pid WHERE id = :sid"
        ), {"sid": spec, "pid": person})

    # ── 3. 加部分唯一索引（防复发）────────────────────────────────────
    # PostgreSQL 部分唯一索引: WHERE is_current → 每个 (spec, role) 最多一条 current
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_slh_spec_role_current
        ON specialty_leadership_history (specialty_id, role)
        WHERE is_current
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_glh_group_role_current
        ON group_leadership_history (group_id, role)
        WHERE is_current
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_glh_group_role_current")
    op.execute("DROP INDEX IF EXISTS uq_slh_spec_role_current")
    # 说明: 数据收敛/卸任**不自动回滚** —— 那是业务决策结果, 不是迁移副作用。
    # 如需回退, 请走专项负责人轮岗接口人工操作, 保留完整审计轨迹。


# ── helpers ──────────────────────────────────────────────────────────
def _today(bind) -> str:
    """当日日期（字符串）—— 用于 effective_to 兜底"""
    return bind.execute(sa.text("SELECT CURRENT_DATE::text")).scalar()


def _eff_date(bind) -> str:
    """生效日期: 用户决策的组织调整生效日为 2026-09-01"""
    return "2026-09-01"


def _find_specialty(bind, code: str):
    return bind.execute(sa.text(
        "SELECT id FROM specialties WHERE code = :c ORDER BY id LIMIT 1"
    ), {"c": code}).scalar()


def _find_person(bind, name: str):
    return bind.execute(sa.text(
        "SELECT id FROM persons WHERE display_name = :n ORDER BY id LIMIT 1"
    ), {"n": name}).scalar()
