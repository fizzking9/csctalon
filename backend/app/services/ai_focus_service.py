"""AI 助手关注点/分析视角 - 服务层 (A-20260904-003 v1.2 二期切片 / 工单 #26)

职责:
  1. 读取: 按问句关键词路由 scope_key → 拼注入 Prompt 的 Focus 文本(建议性指导)
  2. 进程内短缓存: 避免每次问句都查库; 管理端写操作显式失效
  3. CRUD: 管理路由(AdminUser)调用

设计(见 docs/design/20260907-ai-assistant-focus-configurable-0.md 已通过终审):
- Focus 注入位置 = AI Prompt user 段的【业务口径】与【给定数据】之间, 为建议性参考,
  不得覆盖 System 铁律/口径卡(代码常量)。
- scope_key: global(兜底全带)/refund/complaint/reception; 问句关键词命中则带对应
  scope + global, 未命中只带 global。
- 空库/全停用 → 返回空(注入空文本, 不报错, 退化=改造前行为)。
"""

import logging
import time

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_focus_config import AiFocusConfig

logger = logging.getLogger(__name__)

# scope_key 合法集合
FOCUS_SCOPES = ("global", "refund", "complaint", "reception")

# 问句关键词 → scope_key(与 ai_glossary.GLOSSARY_ROUTE 关键词对齐)
_KEYWORD_TO_SCOPE = {
    "退款": "refund", "提现": "refund", "涉诉": "refund", "退费": "refund",
    "投诉": "complaint", "积压": "complaint", "登记": "complaint", "处理": "complaint",
    "商户号": "complaint", "外部投诉": "complaint", "内部投诉": "complaint",
    "接待": "reception", "会话": "reception", "咨询": "reception", "专项": "reception",
}

# 进程内缓存: key='all' → (ts, rows)。写操作后失效。
_CACHE: dict[str, tuple[float, list[dict]]] = {}
_CACHE_TTL_SECONDS = 60


def _load_all(db: Session) -> list[dict]:
    """查 enabled 配置, 按 sort_order 排序(带 60s 进程缓存)。"""
    now = time.time()
    hit = _CACHE.get("all")
    if hit and now - hit[0] < _CACHE_TTL_SECONDS:
        return hit[1]
    rows = db.scalars(
        select(AiFocusConfig)
        .where(AiFocusConfig.enabled.is_(True))
        .order_by(AiFocusConfig.sort_order, AiFocusConfig.id)
    ).all()
    data = [
        {"code": r.code, "scope_key": r.scope_key, "title": r.title,
         "content": r.content, "sort_order": r.sort_order}
        for r in rows
    ]
    _CACHE["all"] = (now, data)
    return data


def invalidate_cache() -> None:
    """管理端写操作后调用, 让下次问句立即用新配置。"""
    _CACHE.pop("all", None)


def keyword_to_scope(question: str) -> str | None:
    """问句关键词 → 首个命中 scope_key(refund/complaint/reception); 未命中 None。

    #98 (2026-09-15) 层2: ai_feed 按此 scope 切 feed 段, 降输入 token。
    scope 值与 feed 顶层段名逐字符对齐(见 external._build_brief 的
    reception/complaint/refund 段), 故可直接做段选取。
    多 scope 命中时按 _KEYWORD_TO_SCOPE 迭代顺序取首个(确定性, 非随机)。
    """
    q = question or ""
    for kw, scope in _KEYWORD_TO_SCOPE.items():
        if kw in q:
            return scope
    return None


def focus_for_question(db: Session, question: str) -> str:
    """按问句关键词路由 scope_key, 拼注入文本(global 兜底全带)。

    返回 '' 表示无可用 Focus(空库/全停用/无命中且无 global)。
    """
    all_rows = _load_all(db)
    if not all_rows:
        return ""
    q = question or ""
    scopes: set[str] = set()
    for kw, scope in _KEYWORD_TO_SCOPE.items():
        if kw in q:
            scopes.add(scope)
    if "global" in {r["scope_key"] for r in all_rows}:
        scopes.add("global")  # global 恒带(有则带)
    if not scopes:
        # 未命中任何域 → 只带 global(若存在); 全无则空
        if not any(r["scope_key"] == "global" for r in all_rows):
            return ""
    lines = []
    for r in all_rows:
        if r["scope_key"] in scopes:
            lines.append(f"- 【{r['title']}】{r['content']}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────
# CRUD(管理端 AdminUser)
# ─────────────────────────────────────────────────────────────────────
def list_configs(db: Session) -> list[dict]:
    rows = db.scalars(select(AiFocusConfig).order_by(AiFocusConfig.sort_order, AiFocusConfig.id)).all()
    return [
        {"id": r.id, "code": r.code, "scope_key": r.scope_key, "title": r.title,
         "content": r.content, "sort_order": r.sort_order, "enabled": r.enabled,
         "created_by": r.created_by, "updated_by": r.updated_by}
        for r in rows
    ]


def create_config(db: Session, payload, username: str) -> dict:
    if payload.scope_key not in FOCUS_SCOPES:
        raise HTTPException(400, f"非法 scope_key: {payload.scope_key}; 允许 {', '.join(FOCUS_SCOPES)}")
    exists = db.scalar(select(AiFocusConfig).where(AiFocusConfig.code == payload.code))
    if exists:
        raise HTTPException(400, f"code 已存在: {payload.code}")
    row = AiFocusConfig(
        code=payload.code[:32], scope_key=payload.scope_key, title=payload.title[:64],
        content=payload.content, sort_order=payload.sort_order, enabled=payload.enabled,
        created_by=(username or "")[:64], updated_by=(username or "")[:64],
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    invalidate_cache()
    return list_configs(db)


def update_config(db: Session, config_id: int, payload, username: str) -> dict:
    row = db.get(AiFocusConfig, config_id)
    if not row:
        raise HTTPException(404, "关注点不存在")
    upd = payload.model_dump(exclude_unset=True)
    if "code" in upd:
        raise HTTPException(400, "code 不可修改(唯一标识)")
    for field in ("title", "content", "sort_order", "enabled", "scope_key"):
        if field in upd and upd[field] is not None:
            if field == "scope_key" and upd[field] not in FOCUS_SCOPES:
                raise HTTPException(400, f"非法 scope_key: {upd[field]}")
            setattr(row, field, upd[field])
    row.updated_by = (username or "")[:64]
    db.commit()
    invalidate_cache()
    return list_configs(db)


def delete_config(db: Session, config_id: int) -> bool:
    row = db.get(AiFocusConfig, config_id)
    if not row:
        return False
    db.delete(row)
    db.commit()
    invalidate_cache()
    return True
