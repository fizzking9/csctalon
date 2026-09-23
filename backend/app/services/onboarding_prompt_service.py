"""接入助手起草提示词版本化服务 (2026-09-23 提示词全量版本化)

onboarding_prompts 从单行 UPDATE 覆盖升级为多版本行模式(与 prompt_configs #178 同):
- 加载(起草调用点 onboarding_draft_service._load_prompt): 取 is_active=true 那行, 逻辑不变;
  无/异常/空 → 模块级默认兜底(铁律, 零回归)。
- 版本管理(admin): list / create_new / activate / rollback, 全程留痕(谁/何时/旧→新版本)。
  不加金丝雀冒烟(起草提示词不是开放问答侧, 冒烟价值低; admin-only 直接激活)。

设计说明见 onboarding_prompt 模型 docstring。本期不追溯补历史 v1 旧版本行(已丢失)。
"""
import logging
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.onboarding_prompt import OnboardingPrompt

logger = logging.getLogger(__name__)

PROMPT_CODES = ("draft_system", "draft_user")


def _now_cn() -> datetime:
    import zoneinfo

    return datetime.now(zoneinfo.ZoneInfo("Asia/Shanghai"))


def load_effective_prompt(db: Session, code: str) -> str | None:
    """取生效版本提示词; 无生效行/异常 → None(调用方回退模块级默认)。"""
    if code not in PROMPT_CODES:
        return None
    try:
        row = db.query(OnboardingPrompt).filter(
            OnboardingPrompt.code == code,
            OnboardingPrompt.is_active.is_(True),
        ).order_by(OnboardingPrompt.version.desc()).first()
    except Exception as e:  # noqa: BLE001  提示词表查询失败不阻塞(回退默认)
        logger.warning("onboarding load_effective_prompt(%s) 查询失败, 回退默认: %s", code, e)
        return None
    if row is None or not (row.content or "").strip():
        return None
    return row.content


def list_versions(db: Session, code: str) -> list[dict]:
    """版本列表(含生效标记/激活人/时间/change_reason), 供版本管理界面。"""
    rows = db.query(OnboardingPrompt).filter(OnboardingPrompt.code == code) \
        .order_by(OnboardingPrompt.version.desc()).all()
    return [_dict(r) for r in rows]


def create_new_version(db: Session, code: str, content: str, user_id: int,
                       name_cn: str | None = None,
                       change_reason: str | None = None,
                       references: str | None = None) -> OnboardingPrompt:
    """新增版本: 复制当前生效行 content 语义, version+1, 不激活(is_active=False 草稿)。

    content 为空时以当前生效版本为底(先改后升); change_reason/references 供版本账本可溯。
    """
    if code not in PROMPT_CODES:
        raise ValueError(f"code 仅支持: {'/'.join(PROMPT_CODES)}")
    content = (content or "").strip()
    active = db.query(OnboardingPrompt).filter(
        OnboardingPrompt.code == code, OnboardingPrompt.is_active.is_(True),
    ).first()
    if not content:
        if active is not None and (active.content or "").strip():
            content = active.content.strip()
        else:
            raise ValueError("新版本内容不能为空（且无生效版本可复制）")
    max_ver = db.query(OnboardingPrompt.version).filter(OnboardingPrompt.code == code) \
        .order_by(OnboardingPrompt.version.desc()).first()
    next_ver = (max_ver[0] if max_ver else 0) + 1
    row = OnboardingPrompt(
        code=code,
        name_cn=name_cn or (f"{active.name_cn} v{next_ver}" if active else f"起草提示 {code} v{next_ver}"),
        content=content,
        version=next_ver,
        is_active=False,
        change_reason=(change_reason or "").strip() or None,
        references=(references or "").strip() or None,
        created_by=user_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def activate_version(db: Session, user_id: int, config_id: int) -> dict:
    """激活指定版本: 先取消同 code 旧生效标记, 再置本行为生效。回滚=激活旧版本。"""
    row = db.get(OnboardingPrompt, config_id)
    if row is None:
        raise ValueError(f"提示词版本不存在: {config_id}")
    now = _now_cn()
    db.execute(text(
        "UPDATE onboarding_prompts SET is_active = FALSE "
        "WHERE code = :code AND is_active = TRUE AND id != :cid"
    ), {"code": row.code, "cid": row.id})
    row.is_active = True
    row.activated_at = now
    row.activated_by = user_id
    db.commit()
    return _dict(row)


def rollback_to_version(db: Session, user_id: int, config_id: int) -> dict:
    """回滚 = 激活历史版本(不删当前版本行, 账本完整)。"""
    return activate_version(db, user_id, config_id)


def get_version(db: Session, config_id: int) -> dict | None:
    """按 id 取单版本; 不存在返回 None。"""
    row = db.get(OnboardingPrompt, config_id)
    return None if row is None else _dict(row)


def _dict(r: OnboardingPrompt) -> dict:
    return {
        "id": r.id, "code": r.code, "name_cn": r.name_cn,
        "version": r.version, "content": r.content,
        "is_active": bool(r.is_active),
        "change_reason": r.change_reason, "references": r.references,
        "created_by": r.created_by, "activated_by": r.activated_by,
        "activated_at": r.activated_at, "created_at": r.created_at,
    }
