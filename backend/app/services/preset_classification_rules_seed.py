"""预设分类规则 seed - 退款分类(互斥优先级)

2 条 ClassificationRule,按 classification_rule_ids 数组顺序决定优先级(引擎转 CASE WHEN,第一个命中生效):
1. 客诉-外部投诉(最高优先级):refund_wide.has_external_complaint = true
2. 客诉-原因:refund_wide.refund_reason 含"客诉"关键词

(#185 清理: 原规则3「客诉-审核人」依赖 _AUDITOR_COMPLAINT_LIST 名单, 该名单长期为空
  → 条件 IN [] 永不命中, 属占位规则。已删除定义; DB 中已注册的该规则由
  ensure_preset_classification_rules_registered 置 is_active=False)

不匹配任何规则 → default_label="普通退款"

注意:2 条规则都基于 refund_wide 物化宽表字段,因此仅适用于 source_code=refund_wide 的视图 segment。
规则1 必须走 refund_wide,因为 has_external_complaint 是物化字段(refund 表无此列)。
规则2 的 refund_reason 已冗余到 refund_wide,所以也能在 refund_wide 视图上一起用。

幂等注册:按 name upsert,返回 {name: rule_id} 供预设视图引用。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.analysis_view import ClassificationRule

# #185: 已废弃的预设规则名(定义已从 PRESET_RULES 移除)。ensure 时发现 DB 中仍存在 → 置 is_active=False。
_DEPRECATED_PRESET_NAMES: list[str] = ["客诉-审核人"]

PRESET_RULES: list[dict] = [
    {
        "name": "客诉-外部投诉",
        "description": "最高优先级:该 SN 有外部投诉记录(315/监督局)→ 归为客诉-外部投诉。基于 refund_wide.has_external_complaint 物化字段。",
        "rules": [
            {
                "label": "客诉-外部投诉",
                "match": "all",
                "conditions": [
                    {"field": "has_external_complaint", "op": "eq", "value": True},
                ],
            }
        ],
        "default_label": "普通退款",
    },
    {
        "name": "客诉-原因",
        "description": "次优先级:退费原因含'客诉'关键词 → 归为客诉-原因。",
        "rules": [
            {
                "label": "客诉-原因",
                "match": "any",
                "conditions": [
                    {"field": "refund_reason", "op": "contains", "value": "客诉"},
                ],
            }
        ],
        "default_label": "普通退款",
    },
]


def ensure_preset_classification_rules_registered(db: Session) -> dict[str, int]:
    """幂等注册预设分类规则:按 name upsert

    Returns:
        {name: rule_id} 映射,供预设视图引用 classification_rule_names → ids
    """
    name_to_id: dict[str, int] = {}
    for cfg in PRESET_RULES:
        existing = db.scalar(select(ClassificationRule).where(ClassificationRule.name == cfg["name"]))
        if existing:
            # 更新规则定义(允许调优)
            existing.description = cfg.get("description")
            existing.rules = cfg["rules"]
            existing.default_label = cfg["default_label"]
            existing.is_active = True
            name_to_id[cfg["name"]] = existing.id
        else:
            rule = ClassificationRule(
                name=cfg["name"],
                description=cfg.get("description"),
                rules=cfg["rules"],
                default_label=cfg["default_label"],
                is_active=True,
            )
            db.add(rule)
            db.flush()  # 获取 id
            name_to_id[cfg["name"]] = rule.id
    # #185 清理: 废弃预设规则(定义已移除)在 DB 中仍有行 → 置 inactive, 避免残留规则影响分类
    if _DEPRECATED_PRESET_NAMES:
        rows = list(
            db.scalars(
                select(ClassificationRule).where(ClassificationRule.name.in_(_DEPRECATED_PRESET_NAMES))
            ).all()
        )
        for r in rows:
            if r.is_active:
                r.is_active = False
    db.commit()
    return name_to_id
