"""预设分类规则 seed - 退款分类(互斥优先级)

3 条 ClassificationRule,按 classification_rule_ids 数组顺序决定优先级(引擎转 CASE WHEN,第一个命中生效):
1. 客诉-外部投诉(最高优先级):refund_wide.has_external_complaint = true
2. 客诉-原因:refund_wide.refund_reason 含"客诉"关键词(占位,待用户细化)
3. 客诉-审核人:refund_wide.auditor 在客诉组名单内(名单占位,待用户提供)

不匹配任何规则 → default_label="普通退款"

注意:3 条规则都基于 refund_wide 物化宽表字段,因此仅适用于 source_code=refund_wide 的视图 segment。
规则1 必须走 refund_wide,因为 has_external_complaint 是物化字段(refund 表无此列)。
规则2/3 的 refund_reason/auditor 已冗余到 refund_wide,所以也能在 refund_wide 视图上一起用。

幂等注册:按 name upsert,返回 {name: rule_id} 供预设视图引用。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.analysis_view import ClassificationRule

# 客诉组审核人名单(占位,待用户提供完整名单后更新)
# 当前为空列表:规则3 条件 IN [] 永远不命中,等价于"暂不启用规则3"
_AUDITOR_COMPLAINT_LIST: list[str] = []

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
        "description": "次优先级:退费原因含'客诉'关键词 → 归为客诉-原因。关键词占位,待用户细化。",
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
    {
        "name": "客诉-审核人",
        "description": "最低优先级:审核人在客诉组名单内 → 归为客诉-审核人。名单占位,待用户提供。",
        "rules": [
            {
                "label": "客诉-审核人",
                "match": "any",
                "conditions": [
                    {"field": "auditor", "op": "in", "value": _AUDITOR_COMPLAINT_LIST},
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
    db.commit()
    return name_to_id
