"""人员别名提取 - 从业务表的人名字段提取候选别名

各业务表的 operator/auditor/handler/registrant 视为别名，映射到统一 Person。
sys/system 标记为系统自动操作（is_system=true），不建 person。
首次出现的别名自动建一个待确认 Person（is_confirmed=false）。
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.person import Person, PersonAlias
from app.models.refund import Refund
from app.models.ticket import ServiceTicket
from app.models.wallet import WalletWithdrawal

# 系统自动操作标识（不归并到真人）
SYSTEM_ALIASES = {"sys", "system", "SYS", "System"}
# 无效占位符
INVALID_ALIASES = {"", "nan", "null", "none", "NaN", "NULL", "None"}

# (model, field_attr, source_table, source_field)
PERSON_FIELD_SOURCES = [
    (Refund, Refund.operator, "refunds", "operator"),
    (Refund, Refund.auditor, "refunds", "auditor"),
    (ServiceTicket, ServiceTicket.handler, "service_tickets", "handler"),
    (ServiceTicket, ServiceTicket.registrant, "service_tickets", "registrant"),
    (WalletWithdrawal, WalletWithdrawal.operator, "wallet_withdrawals", "operator"),
]


def _normalize_alias(raw: str) -> str:
    """归一化：去前后空白；剥离「企微-」「企微·」「企微—」等前缀；lower

    用于 alias_normalized 字段（不修改 alias 原值），给归并建议用。
    样本中存在「企微-张三」「企微·李四」两种分隔符，统一剥离。
    """
    s = raw.strip()
    # 剥离企微前缀（兼容 - · — 等分隔符）
    if s.startswith("企微"):
        # 去掉「企微」后，跳过紧随的分隔符（- · — - 空格 等）
        rest = s[2:]
        if rest and rest[0] in "-·—－- ":
            rest = rest[1:]
        s = rest.strip()
    return s.lower()


def extract_person_aliases(db: Session) -> dict:
    """从 5 个 person 字段提取候选别名（幂等）

    - sys/system/空/nan/null 跳过（sys/system 入库标 is_system 供审计）
    - alias 已存在则只更新 last_seen_at
    - 新 alias 自动建一个待确认 Person（display_name=alias 原值, is_confirmed=False）
    """
    stats = {"new_aliases": 0, "new_persons": 0, "skipped": 0, "system_aliases": 0}
    for model, field_attr, src_table, src_field in PERSON_FIELD_SOURCES:
        # GROUP BY 一次拉所有 distinct 值 + 最后出现时间（系统导入时间）
        rows = db.execute(
            select(field_attr, func.max(model.created_at)).where(field_attr.isnot(None)).group_by(field_attr)
        ).all()
        for raw_value, last_seen in rows:
            if not raw_value or str(raw_value).strip() in INVALID_ALIASES:
                stats["skipped"] += 1
                continue
            raw_value = str(raw_value)
            existing = db.scalar(select(PersonAlias).where(PersonAlias.alias == raw_value))
            if existing:
                existing.last_seen_at = last_seen
                continue
            if raw_value in SYSTEM_ALIASES:
                # sys/system：入库标 is_system，不建 person（审计可追溯）
                db.add(PersonAlias(
                    alias=raw_value,
                    alias_normalized=raw_value.lower(),
                    source_table=src_table,
                    source_field=src_field,
                    is_system=True,
                    last_seen_at=last_seen,
                ))
                stats["system_aliases"] += 1
                stats["new_aliases"] += 1
                continue
            # 自动建待确认 Person
            person = Person(display_name=raw_value, is_confirmed=False, role="agent")
            db.add(person)
            db.flush()
            db.add(PersonAlias(
                person_id=person.id,
                alias=raw_value,
                alias_normalized=_normalize_alias(raw_value),
                source_table=src_table,
                source_field=src_field,
                last_seen_at=last_seen,
            ))
            stats["new_aliases"] += 1
            stats["new_persons"] += 1
    db.commit()
    return stats
