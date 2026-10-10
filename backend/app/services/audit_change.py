"""统一变更审计服务 (S-P1-11 / #261 B4-5)

用法(与业务同事务):
    from app.services.audit_change import diff_model_changes, record_change

    data = payload.model_dump(exclude_unset=True)
    diffs = diff_model_changes(p, data)          # setattr 前先算 diff
    for k, v in data.items():
        setattr(p, k, v)
    db.commit()                                   # 同一事务提交
    record_change(db, entity=p, action="update", changes=diffs,
                  actor=_.user, source="api")     # commit 后独立落审计(失败不影响业务)

设计纪律:
- record_change 自带 try/except + 独立 commit: 审计失败绝不回滚业务(与
  operation_log 中间件「任何异常都吞掉」同一纪律), 但必须 logger.error 留痕。
- diff 只对传入的变更字段(白名单由调用方的 payload 决定), 不做全对象快照。
"""

import logging

from sqlalchemy.orm import Session

from app.models.audit_change import AuditChange

logger = logging.getLogger(__name__)


def diff_model_changes(obj, data: dict) -> dict:
    """对即将 setattr 的变更字段逐个比对新旧值。

    Returns:
        {field: [before, after]} —— 无实际变化的字段不输出
    """
    diffs: dict = {}
    for k, v in data.items():
        old = getattr(obj, k, None)
        if old != v:
            diffs[k] = [_jsonable(old), _jsonable(v)]
    return diffs


def _jsonable(v):
    """diff 值转 JSONB 可存形态(date/datetime/Decimal → str, 其余原样)"""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    return str(v)


def record_change(
    db: Session,
    *,
    entity_type: str,
    entity_id,
    action: str,
    changes: dict | None = None,
    actor=None,
    source: str = "api",
    summary: str | None = None,
) -> None:
    """落一条统一变更审计(独立事务, 失败不影响业务)。

    Args:
        entity_type: 实体表名(如 "persons"); 也支持直接传 ORM 对象 entity=obj 自动取
        action:      create/update/delete
        changes:     {field: [before, after]} (diff_model_changes 的产出)
        actor:       当前 User ORM 对象(无登录态传 None)
        source:      api / night_batch / manual_import / system
        summary:     人类可读摘要; 不传则自动生成(如 "update 3 fields: a, b, c")
    """
    if entity_type and not isinstance(entity_type, str):
        # 容错: 调用方传了 ORM 对象 → 取表名
        entity_type = entity_type.__tablename__
    row = AuditChange(
        entity_type=entity_type,
        entity_id=str(entity_id),
        action=action,
        actor_id=getattr(actor, "id", None),
        actor_name=getattr(actor, "username", None),
        source=source,
        changes=changes or None,
        summary=summary or (
            f"{action} {len(changes)} fields: {', '.join(sorted(changes))}" if changes else action
        ),
    )
    try:
        db.add(row)
        db.commit()
    except Exception:
        db.rollback()
        # 审计失败必须留痕(进程日志), 不静默
        logger.error(
            "audit_change 落库失败: entity=%s#%s action=%s changes=%s",
            entity_type, entity_id, action, changes, exc_info=True,
        )
