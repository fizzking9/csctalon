"""提示词版本化服务 (工单 #178, 设计: docs/design/AI提示词评估中心-20260920.md 演进)

两级提示词 DB 化 + 版本化的服务层：
- **加载**（生成/摘取调用点）：DB 有生效版本(is_active=TRUE) → 用 DB 内容；
  无/异常/禁用 → 回退代码常量（提示词铁律，零回归）。
- **版本管理**（admin）：list / create_new / activate / rollback，全部走
  prompt_configs 状态机，激活/回滚留痕（谁/何时/旧→新版本）。

设计说明（Design Rationale）
- 目的：给 #176 自动迭代闭环提供「版本化提示词」的安全落点 —— 新增版本不覆盖旧行、
  激活切生效、回滚恢复旧版本，全程可审计。
- 逻辑：`is_active` 部分唯一索引（每 code 至多一个生效版本）由模型承担；
  加载侧只读生效行；版本递增 = 复制当前生效行 content + version+1。
- 取舍：激活/回滚不调 LLM、不自动触发金丝雀（金丝雀在 #179 单独做）；
  本服务只负责「版本账本 + 生效切换」，保持单一职责。
"""

import logging
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.prompt_config import PromptConfig

logger = logging.getLogger(__name__)

PROMPT_CODES = ("gen_system", "extract_system")


def _now_cn() -> datetime:
    """北京时间（与项目时间规范一致，用于激活/回滚留痕）。"""
    import zoneinfo

    return datetime.now(zoneinfo.ZoneInfo("Asia/Shanghai"))


def load_effective_prompt(db: Session, code: str) -> str | None:
    """取生效版本提示词；无生效行/异常 → None（调用方回退代码常量）。

    这是加载侧唯一入口：DB 优先、代码兜底。查询失败不抛（提示词取不到不阻塞生成）。
    """
    if code not in PROMPT_CODES:
        return None
    try:
        row = db.query(PromptConfig).filter(
            PromptConfig.code == code,
            PromptConfig.is_active.is_(True),
        ).order_by(PromptConfig.version.desc()).first()
    except Exception as e:  # noqa: BLE001  提示词表查询失败不阻塞（回退默认）
        logger.warning("load_effective_prompt(%s) 查询失败, 回退代码常量: %s", code, e)
        return None
    if row is None or not (row.content or "").strip():
        return None
    return row.content


def list_versions(db: Session, code: str) -> list[dict]:
    """版本列表（含生效标记与激活人/时间），供版本管理界面。"""
    rows = db.query(PromptConfig).filter(PromptConfig.code == code) \
        .order_by(PromptConfig.version.desc()).all()
    return [
        {
            "id": r.id, "code": r.code, "name_cn": r.name_cn,
            "version": r.version, "content": r.content,
            "is_active": bool(r.is_active), "baseline": r.baseline,
            "created_by": r.created_by, "activated_by": r.activated_by,
            "activated_at": r.activated_at, "created_at": r.created_at,
        }
        for r in rows
    ]


def create_new_version(db: Session, code: str, content: str, user_id: int,
                       name_cn: str | None = None) -> PromptConfig:
    """新增版本：复制当前生效行 content 语义，version+1，不激活。

    `content` 为空时以当前生效版本为底（先改后升的典型流程）；
    新版本 is_active=FALSE（草稿），人工激活后才生效。
    """
    if code not in PROMPT_CODES:
        raise ValueError(f"code 仅支持: {'/'.join(PROMPT_CODES)}")
    content = (content or "").strip()
    active = db.query(PromptConfig).filter(
        PromptConfig.code == code, PromptConfig.is_active.is_(True),
    ).first()
    if not content:
        if active is not None and (active.content or "").strip():
            content = active.content.strip()
        else:
            raise ValueError("新版本内容不能为空（且无生效版本可复制）")
    max_ver = db.query(PromptConfig.version).filter(PromptConfig.code == code) \
        .order_by(PromptConfig.version.desc()).first()
    next_ver = (max_ver[0] if max_ver else 0) + 1
    row = PromptConfig(
        code=code,
        name_cn=name_cn or (f"{active.name_cn} v{next_ver}" if active else f"提示词 {code} v{next_ver}"),
        content=content,
        version=next_ver,
        is_active=False,
        created_by=user_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def activate_version(db: Session, user_id: int, config_id: int,
                     baseline: str | None = None) -> dict:
    """激活指定版本：先取消同 code 旧生效标记，再置本行为生效。回滚=激活旧版本。"""
    row = db.get(PromptConfig, config_id)
    if row is None:
        raise ValueError(f"提示词版本不存在: {config_id}")
    now = _now_cn()
    # 取消同 code 旧生效（部分唯一索引约束：同一 code 至多一个生效）
    db.execute(text(
        "UPDATE prompt_configs SET is_active = FALSE "
        "WHERE code = :code AND is_active = TRUE AND id != :cid"
    ), {"code": row.code, "cid": row.id})
    row.is_active = True
    row.activated_at = now
    row.activated_by = user_id
    row.baseline = baseline
    db.commit()
    return _dict(row)


def rollback_to_version(db: Session, user_id: int, config_id: int) -> dict:
    """回滚 = 激活历史版本（不删当前版本行，账本完整）。"""
    return activate_version(db, user_id, config_id)


def _dict(r: PromptConfig) -> dict:
    return {
        "id": r.id, "code": r.code, "name_cn": r.name_cn,
        "version": r.version, "content": r.content,
        "is_active": bool(r.is_active), "baseline": r.baseline,
        "created_by": r.created_by, "activated_by": r.activated_by,
        "activated_at": r.activated_at, "created_at": r.created_at,
    }
