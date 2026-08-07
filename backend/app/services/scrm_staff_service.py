"""SCRM 人设信息(ScrmStaffProfile) CRUD + 归一化匹配建议
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from difflib import SequenceMatcher
from typing import Iterable

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.person import Person
from app.models.scrm_staff_profile import ScrmStaffProfile
from app.models.user import User
from app.schemas.scrm_staff import (
    ScrmStaffMatchCandidate,
    ScrmStaffProfileUpdate,
)

log = logging.getLogger(__name__)

# ============================================================
#  业务唯一键
# ============================================================
def build_staff_unique_key(source_config_id: int | None, wecom_nick: str, enterprise_suffix: str | None) -> str:
    """构造人设信息的业务唯一键 md5"""
    raw = f"{source_config_id or 0}|{wecom_nick}|{enterprise_suffix or ''}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


# ============================================================
#  归一化匹配建议(用 User 表和 Person 表做候选)
# ============================================================
def _norm(s: str | None) -> str:
    if not s:
        return ""
    s = s.strip().lower()
    # 去全角括号 / 空格
    for ch in "（）()【】[]『』「」 \t-_·.、,，":
        s = s.replace(ch, "")
    return s


def _similarity(a: str, b: str) -> float:
    a = _norm(a)
    b = _norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.92
    return SequenceMatcher(None, a, b).ratio()


def suggest_matches_for_staff(
    db: Session,
    staff: ScrmStaffProfile,
    top_k: int = 5,
) -> list[ScrmStaffMatchCandidate]:
    """对单条 staff 生成 Person / User 匹配建议,按分数降序返回 top_k"""
    candidates: list[ScrmStaffMatchCandidate] = []
    # --- 比较源: 实名 / alias_name / wecom_nick / enterprise_suffix / company_group ---
    src_fields = [
        ("real_name", staff.real_name),
        ("alias_name", staff.alias_name),
        ("wecom_nick", staff.wecom_nick),
        ("external_display_name", staff.external_display_name),
    ]
    src_fields = [(k, v) for k, v in src_fields if v]

    # --- Person 候选 ---
    persons = db.scalars(select(Person).where(Person.is_active.is_(True))).all()
    for p in persons:
        best_score = 0
        reasons: list[str] = []
        # 比较 display_name / qw_name / scrm_operator_name
        for tgt_field, tgt_val in [
            ("display_name", p.display_name),
            ("qw_name", p.qw_name),
            ("scrm_operator_name", p.scrm_operator_name),
        ]:
            if not tgt_val:
                continue
            for src_key, src_val in src_fields:
                sim = _similarity(src_val, tgt_val)
                if sim > best_score:
                    best_score = sim
                    reasons = [f"{src_key}≈{tgt_field}(相似度 {sim * 100:.0f}%)"]
                elif abs(sim - best_score) < 1e-6 and sim > 0:
                    reasons.append(f"{src_key}≈{tgt_field}(相似度 {sim * 100:.0f}%)")
        if best_score >= 0.5:
            candidates.append(ScrmStaffMatchCandidate(
                target_kind="person",
                target_id=p.id,
                target_display_name=p.display_name,
                target_qw_name=p.qw_name,
                score=int(round(best_score * 1000)),
                reasons=reasons,
            ))

    # --- User 候选 ---
    users = db.scalars(select(User).where(User.is_active.is_(True))).all()
    for u in users:
        best_score = 0
        reasons: list[str] = []
        for tgt_field, tgt_val in [
            ("display_name", u.display_name),
            ("qw_name", u.qw_name),
            ("username", u.username),
        ]:
            if not tgt_val:
                continue
            for src_key, src_val in src_fields:
                sim = _similarity(src_val, tgt_val)
                if sim > best_score:
                    best_score = sim
                    reasons = [f"{src_key}≈{tgt_field}(相似度 {sim * 100:.0f}%)"]
                elif abs(sim - best_score) < 1e-6 and sim > 0:
                    reasons.append(f"{src_key}≈{tgt_field}(相似度 {sim * 100:.0f}%)")
        if best_score >= 0.5:
            candidates.append(ScrmStaffMatchCandidate(
                target_kind="user",
                target_id=u.id,
                target_display_name=u.display_name,
                target_qw_name=u.qw_name,
                target_username=u.username,
                score=int(round(best_score * 1000)),
                reasons=reasons,
            ))

    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[:top_k]


# ============================================================
#  CRUD
# ============================================================
def list_staff(
    db: Session,
    *,
    source_config_id: int | None = None,
    match_status: str | None = None,
    enterprise_suffix: str | None = None,
    company_group: str | None = None,
    keyword: str | None = None,
    only_unmatched: bool = False,
    limit: int = 500,
    offset: int = 0,
) -> tuple[list[ScrmStaffProfile], int]:
    stmt = select(ScrmStaffProfile)
    if source_config_id:
        stmt = stmt.where(ScrmStaffProfile.source_config_id == source_config_id)
    if match_status:
        stmt = stmt.where(ScrmStaffProfile.match_status == match_status)
    if enterprise_suffix:
        stmt = stmt.where(ScrmStaffProfile.enterprise_suffix == enterprise_suffix)
    if company_group:
        stmt = stmt.where(ScrmStaffProfile.company_group == company_group)
    if only_unmatched:
        stmt = stmt.where(ScrmStaffProfile.person_id.is_(None) & ScrmStaffProfile.user_id.is_(None))
    if keyword:
        kw = f"%{keyword}%"
        stmt = stmt.where(or_(
            ScrmStaffProfile.wecom_nick.ilike(kw),
            ScrmStaffProfile.real_name.ilike(kw),
            ScrmStaffProfile.alias_name.ilike(kw),
            ScrmStaffProfile.company_group.ilike(kw),
        ))
    # count
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    stmt = stmt.order_by(ScrmStaffProfile.company_group.asc(), ScrmStaffProfile.wecom_nick.asc())
    stmt = stmt.offset(offset).limit(limit)
    rows = db.scalars(stmt).all()
    return list(rows), total


def get_staff(db: Session, staff_id: int) -> ScrmStaffProfile | None:
    return db.get(ScrmStaffProfile, staff_id)


def update_staff(db: Session, staff_id: int, payload: ScrmStaffProfileUpdate) -> ScrmStaffProfile | None:
    row = db.get(ScrmStaffProfile, staff_id)
    if row is None:
        return None
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(row, k, v)
    db.add(row)
    db.flush()
    return row


def upsert_staff_from_web_rows(
    db: Session,
    *,
    source_config_id: int | None,
    source_name: str | None,
    rows: Iterable[dict[str, str | None]],
    sync_ts: datetime | None = None,
) -> int:
    """从网页抓取的表格行(中文列名)批量 upsert 到 scrm_staff_profiles

    典型 keys(来自 baiyin personalityManagement 解析):
      {'头像/昵称': '小苏（苏紫依）\\n@自在联',
       '分组': '深圳市云客猫科技有限公司',
       '在线状态': '在线',
       '实名': '', '别名': '随身WiFi-小苏',
       '对外展示': '未设置',
       '性别': '男',
       '自动通过好友请求': '未设置',
       '通过群聊验证': '已开启',
       '类型': 'ipad扫码号',
       '朋友圈封面': '未设置',
       '人设模板': '未设置',
       '新客运营模板': '未设置'}
    """
    sync_ts = sync_ts or datetime.utcnow()
    inserted = 0
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        # --- 解析头像/昵称+企业 ---
        avatar_nick = str(raw.get("头像/昵称") or raw.get("昵称") or "").strip()
        wecom_nick = avatar_nick
        ent = None
        if "\n@" in avatar_nick:
            parts = avatar_nick.split("\n@", 1)
            wecom_nick = parts[0].strip()
            ent = parts[1].strip()
        elif "@" in avatar_nick and not avatar_nick.startswith("@"):
            parts = avatar_nick.rsplit("@", 1)
            wecom_nick = parts[0].strip()
            ent = parts[1].strip()
        if not wecom_nick:
            continue  # 空行跳过
        uk = build_staff_unique_key(source_config_id, wecom_nick, ent)
        # 查 or 建
        row = db.scalar(select(ScrmStaffProfile).where(ScrmStaffProfile.staff_unique_key == uk))
        is_new = False
        if row is None:
            row = ScrmStaffProfile(
                staff_unique_key=uk,
                wecom_nick=wecom_nick,
                source_config_id=source_config_id,
            )
            is_new = True
        # --- 填充/覆盖业务字段 ---
        row.enterprise_suffix = ent
        row.company_group = raw.get("分组") or raw.get("企业") or None
        row.online_status = raw.get("在线状态") or None
        row.real_name = raw.get("实名") or None
        row.alias_name = raw.get("别名") or None
        row.external_display_name = raw.get("对外展示") or raw.get("对外展示名") or None
        row.gender = raw.get("性别") or None
        row.auto_accept_friend = raw.get("自动通过好友请求") or None
        row.auto_accept_group_verify = raw.get("通过群聊验证") or None
        row.device_type = raw.get("类型") or raw.get("设备类型") or None
        row.moments_cover = raw.get("朋友圈封面") or None
        row.persona_template = raw.get("人设模板") or None
        row.new_customer_template = raw.get("新客运营模板") or None
        row.sync_source_name = source_name or None
        row.last_synced_at = sync_ts
        row.raw_extra = dict(raw)
        db.add(row)
        db.flush()
        # --- 自动匹配: 未匹配过 & 非人工确认的 尝试自动归并 ---
        if row.match_status in ("unmatched", "auto_matched") or row.match_status is None:
            cands = suggest_matches_for_staff(db, row, top_k=3)
            if cands and cands[0].score >= 900:
                # 分数 ≥ 900 认为可靠, 自动填上 person 或 user
                top = cands[0]
                auto_before = (row.person_id, row.user_id, row.match_status, row.match_score, row.match_reason)
                if top.target_kind == "person":
                    row.person_id = top.target_id
                elif top.target_kind == "user":
                    row.user_id = top.target_id
                row.match_status = "auto_matched"
                row.match_score = top.score
                row.match_reason = "自动匹配: " + "; ".join(top.reasons[:3])
                _ = auto_before  # 可加审计日志
        if is_new:
            inserted += 1
    db.flush()
    return inserted
