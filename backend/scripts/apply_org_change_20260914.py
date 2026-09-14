# -*- coding: utf-8 -*-
"""一次性组织架构变更（2026-09-14 用户决策）

## 背景
用户于 2026-09-14 确认一批组织调整，涉及行政分组改名、组长轮岗、岗位职级、
专项合并、成员迁移、离职登记。本脚本把**口头决策**精确落库。

## ★★ 决策原文（用户 2026-09-14，逐条可追溯）★★
1. 「黄亚康 已经任组长，吴晓龙已经任组长」
2. 「其他的 按照你的建议 更改角色设置」
3. 「洪佳 是投诉专项组长 从9月份开始 任命；组员包括：陈玉红、苏紫依、邵晨宇、田新月」
4. 「朱海洋 任主管」
5. 「张蕾 9.1 开始离职」
6. 「晓龙组 组长为黄亚康，海洋组 组长 吴晓龙」
7. 「投诉组 行政和专项 重合」（→ 投诉专项组 = 行政组 + 专项，洪佳兼任两头）
8. 「组名都改一下，叶珍凤组、董梦玲组、晓龙组，分别是接待1组 2组 3组；
    朱海洋组 专项1；原张蕾组 改为 投诉专项」
9. 「8月 9月 董梦玲 任职副主管」→ 董梦玲 组长 + 副主管，吴晓龙 → 组长
10. 「内外部投诉组合并」+ 系数「取外部投诉 5.0」
11. 张蕾「标为离职，日期 2026-09-01」
12. 生效日期 2026-09-01；目标=**生产库**；执行方式=本脚本（含 dry-run）

## ★ 合并策略：「只增不改」
新建「投诉专项」(5.0)，**保留**「内部投诉」「外部投诉」旧专项记录 ——
历史任职仍指向旧专项，**历史绩效可追溯、不重算**。

## 用法
```
# 预览（不写库）
PYTHONPATH=. .venv/bin/python scripts/apply_org_change_20260914.py --dry-run
# 执行
PYTHONPATH=. .venv/bin/python scripts/apply_org_change_20260914.py --apply
```

## 安全设计
- 默认 dry-run；必须显式 `--apply` 才写库
- 全程单事务，任一步失败即整体回滚
- 幂等：已存在的记录跳过（可重复执行）
- 每步打印「前 → 后」，便于审计
- **不物理删除**任何历史记录（只置 is_current=False + effective_to）
"""
from __future__ import annotations

import argparse
import sys
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.group import Group, GroupLeadershipHistory, PersonGroupMembership
from app.models.person import Person
from app.models.specialty import (
    PersonSpecialtyAssignment,
    Specialty,
    SpecialtyLeadershipHistory,
)
from app.services.staff_assignment_service import create_assignment

# ── 生效日期（用户拍板）──────────────────────────────
EFF = date(2026, 9, 1)
REASON = "2026-09-14 组织架构调整"

# ── 人员姓名 → 预期（脚本按姓名查，不硬编码 id）──────
NAMES = (
    "黄亚康", "吴晓龙", "洪佳", "陈玉红", "苏紫依", "邵晨雨", "田兴月",
    "朱海洋", "张蕾", "董梦玲", "叶珍凤", "刘景山", "唐彩慧", "杨馄瑶",
)

# ① 分组改名（用户决策 8）
GROUP_RENAME = {
    "叶珍凤组": "接待1组",
    "董梦玲组": "接待2组",
    "吴晓龙组": "接待3组",
    "朱海洋组": "专项1组",
    "张蕾组": "投诉专项组",
}

# ② 组长轮岗（用户决策 1/4/5/6/7）
GROUP_LEADER = {
    "接待1组": "叶珍凤",      # 保留（用户确认）
    "接待2组": "董梦玲",      # 保留（用户确认）
    "接待3组": "黄亚康",      # 决策 6
    "专项1组": "吴晓龙",      # 决策 6
    "投诉专项组": "洪佳",     # 决策 3/7
}

# ③ 岗位职级（用户决策 1/4/9）
POSITION = {
    "董梦玲": "副主管",   # 决策 9：组长 + 副主管
    "吴晓龙": "组长",     # 决策 1：原副主管 → 组长
    "黄亚康": "组长",     # 决策 1
    "洪佳": "组长",       # 决策 3
    "朱海洋": "主管",     # 决策 4
}

# ④ 专项合并（用户决策 10）：4 人迁入新「投诉专项」
COMPLAINT_TEAM = ("洪佳", "陈玉红", "苏紫依", "邵晨雨", "田兴月")

# ⑤ 成员迁组（用户决策 3/7 + 追问确认「投诉组行政与专项重合」）
MOVE_TO_GROUP = {
    "邵晨雨": "投诉专项组",   # 原董梦玲组
    "田兴月": "投诉专项组",   # 原董梦玲组
}

# ⑥ 专项标签校正（基于实测工作画像，用户「按建议更改」）
SPECIALTY_FIX = {
    "杨馄瑶": "咨询接待",     # 实测：退款类接待 0% → 实际做咨询
    "刘景山": None,           # ⚠️ 保留「内部投诉」但暂停用（见下方 SPECIALTY_HOLD）
}

# ⑦ 暂不自动改、需业务复核的项（脚本**只报告不执行**）
SPECIALTY_HOLD = {
    "刘景山": "实测零投诉完成、98% 接待是退款类 → 疑实际做退款挽回；"
              "但其「内部投诉」是历史专项，改动影响绩效，待人工确认",
    "唐彩慧": "专项「退款处理」+ 兼任「退款挽回」负责人；离职/转岗情况未知，待确认",
    "廖俊辉": "退款类接待占 23%，介于咨询与退款挽回之间，待确认",
}


def _p(db: Session, name: str) -> Person | None:
    return db.scalar(select(Person).where(Person.display_name == name))


# 新组名 → 旧组名（供 dry-run 解析；apply 模式下组已改名，直接命中新名）
_NEW_TO_OLD = {v: k for k, v in GROUP_RENAME.items()}


def _g(db: Session, new_name: str) -> Group | None:
    """按**新组名**查分组。

    ⚠️ dry-run 模式下步骤①不会真的改名，故此处回退用旧名查 ——
       否则预览会把"即将改成的组"误报为「缺失」，使得预览**不可信**
       （预览必须真实反映将要发生的事，否则等于没预览）。
    """
    g = db.scalar(select(Group).where(Group.name == new_name))
    if g is None:
        old = _NEW_TO_OLD.get(new_name)
        if old:
            g = db.scalar(select(Group).where(Group.name == old))
    return g


def _log(dry: bool, msg: str) -> None:
    print(f"  [{'预览' if dry else '执行'}] {msg}")


# ══════════════════════════════════════════════════════════════════
# 步骤 1: 分组改名
# ══════════════════════════════════════════════════════════════════
def step_rename_groups(db: Session, dry: bool) -> int:
    print("\n① 分组改名")
    n = 0
    for old, new in GROUP_RENAME.items():
        g = db.scalar(select(Group).where(Group.name == old))
        if g is None:
            # 幂等：可能已改名
            if db.scalar(select(Group).where(Group.name == new)):
                print(f"  [跳过] {old} → {new}（已改）")
                continue
            print(f"  ⚠️ [缺失] 找不到分组「{old}」")
            continue
        _log(dry, f"{old} → {new}（id={g.id}）")
        if not dry:
            g.name = new
        n += 1
    return n


# ══════════════════════════════════════════════════════════════════
# 步骤 2: 组长轮岗
# ══════════════════════════════════════════════════════════════════
def step_transfer_leaders(db: Session, dry: bool) -> int:
    print("\n② 组长轮岗")
    n = 0
    for gname, pname in GROUP_LEADER.items():
        g = _g(db, gname)
        p = _p(db, pname)
        if g is None or p is None:
            print(f"  ⚠️ [缺失] 组「{gname}」或人「{pname}」不存在")
            continue
        cur = db.scalar(
            select(GroupLeadershipHistory).where(
                GroupLeadershipHistory.group_id == g.id,
                GroupLeadershipHistory.role == "leader",
                GroupLeadershipHistory.is_current.is_(True),
            )
        )
        if cur and cur.person_id == p.id:
            print(f"  [跳过] {gname} 组长已是 {pname}")
            continue
        old_name = "(无)"
        if cur:
            old_p = db.get(Person, cur.person_id)
            old_name = old_p.display_name if old_p else str(cur.person_id)
        _log(dry, f"{gname}: 组长 {old_name} → {pname}")
        if not dry:
            if cur:
                cur.is_current = False
                cur.effective_to = EFF
            db.add(GroupLeadershipHistory(
                group_id=g.id, person_id=p.id, role="leader",
                effective_from=EFF, is_current=True,
                change_reason=REASON, source="manual",
            ))
            g.current_leader_id = p.id
            # 新组长自动入组（与 API transfer_leadership 同语义）
            m = db.scalar(select(PersonGroupMembership).where(
                PersonGroupMembership.person_id == p.id,
                PersonGroupMembership.is_current.is_(True),
            ))
            if m and m.group_id != g.id:
                m.is_current = False
                m.effective_to = EFF
                m.change_reason = REASON
                m = None
            if not m or m.group_id != g.id:
                db.add(PersonGroupMembership(
                    person_id=p.id, group_id=g.id, effective_from=EFF,
                    is_current=True, change_reason=REASON, source="manual",
                ))
                p.current_group_id = g.id
                p.group_name = g.name
        n += 1
    return n


# ══════════════════════════════════════════════════════════════════
# 步骤 3: 成员迁组
# ══════════════════════════════════════════════════════════════════
def step_move_members(db: Session, dry: bool) -> int:
    print("\n③ 成员迁组")
    n = 0
    for pname, gname in MOVE_TO_GROUP.items():
        p = _p(db, pname)
        g = _g(db, gname)
        if p is None or g is None:
            print(f"  ⚠️ [缺失] 人「{pname}」或组「{gname}」")
            continue
        m = db.scalar(select(PersonGroupMembership).where(
            PersonGroupMembership.person_id == p.id,
            PersonGroupMembership.is_current.is_(True),
        ))
        if m and m.group_id == g.id:
            print(f"  [跳过] {pname} 已在 {gname}")
            continue
        old = "(无)"
        if m:
            og = db.get(Group, m.group_id)
            old = og.name if og else str(m.group_id)
        _log(dry, f"{pname}: {old} → {gname}")
        if not dry:
            if m:
                m.is_current = False
                m.effective_to = EFF
                m.change_reason = REASON
            db.add(PersonGroupMembership(
                person_id=p.id, group_id=g.id, effective_from=EFF,
                is_current=True, change_reason=REASON, source="manual",
            ))
            p.current_group_id = g.id
            p.group_name = g.name
        n += 1
    return n


# ══════════════════════════════════════════════════════════════════
# 步骤 4: 岗位职级 + 专项＋统一走 create_assignment（收口服务）
# ══════════════════════════════════════════════════════════════════
def step_positions(db: Session, dry: bool) -> int:
    print("\n④ 岗位职级（经 staff_assignment_service 收口）")
    n = 0
    for pname, pos in POSITION.items():
        p = _p(db, pname)
        if p is None:
            print(f"  ⚠️ [缺失] 人「{pname}」")
            continue
        if (p.position or "") == pos:
            print(f"  [跳过] {pname} 岗位已是 {pos}")
            continue
        _log(dry, f"{pname}: 岗位 {(p.position or '无')} → {pos}")
        if not dry:
            create_assignment(db, p.id, _mk_payload(
                position=pos, specialty=_keep_specialty(db, p), group_name=p.group_name,
                reason=f"{REASON}·岗位变更",
            ))
        n += 1
    return n


# ══════════════════════════════════════════════════════════════════
# 步骤 5: 专项合并 → 新建「投诉专项」
# ══════════════════════════════════════════════════════════════════
def step_merge_complaint_specialty(db: Session, dry: bool) -> int:
    print("\n⑥ 投诉专项合并")
    from app.services.analytics import COMPLAINT_SPECIALTY, COMPLAINT_SPECIALTY_WEIGHT

    # 5.1 新建专项
    sp = db.scalar(select(Specialty).where(Specialty.name == COMPLAINT_SPECIALTY))
    if sp is None:
        _log(dry, f"新建专项「{COMPLAINT_SPECIALTY}」系数={COMPLAINT_SPECIALTY_WEIGHT}")
        if not dry:
            sp = Specialty(
                code="complaint_merged", name=COMPLAINT_SPECIALTY,
                description="内/外部投诉合并后的统一投诉专项（2026-09-14 组织调整）",
                difficulty_weight=COMPLAINT_SPECIALTY_WEIGHT,
                is_temporary=False, is_active=True, display_order=6,
            )
            db.add(sp)
            db.flush()
    else:
        print(f"  [跳过] 专项「{COMPLAINT_SPECIALTY}」已存在")

    if sp is None and dry:
        # 预览模式：新专项未建 → 仍要把**迁移明细**列出来，
        # 否则预览漏掉最关键的一步（5 人并入新专项），等于没预览。
        print("  [预览] 专项负责人 → 洪佳")
        for pname in COMPLAINT_TEAM:
            p = _p(db, pname)
            if p is None:
                print(f"  ⚠️ [缺失] 人「{pname}」")
                continue
            olds = db.scalars(select(PersonSpecialtyAssignment).where(
                PersonSpecialtyAssignment.person_id == p.id,
                PersonSpecialtyAssignment.is_current.is_(True),
            )).all()
            old_names = []
            for a in olds:
                o = db.get(Specialty, a.specialty_id)
                old_names.append(o.name if o else str(a.specialty_id))
            print(f"  [预览] {pname}: 专项 {','.join(old_names) or '无'} → {COMPLAINT_SPECIALTY}")
        return len(COMPLAINT_TEAM)

    # 5.2 负责人 = 洪佳
    lead = _p(db, "洪佳")
    if lead:
        cur = db.scalar(select(SpecialtyLeadershipHistory).where(
            SpecialtyLeadershipHistory.specialty_id == sp.id,
            SpecialtyLeadershipHistory.role == "lead",
            SpecialtyLeadershipHistory.is_current.is_(True),
        ))
        if cur and cur.person_id == lead.id:
            print("  [跳过] 负责人已是 洪佳")
        else:
            _log(dry, "专项负责人 → 洪佳")
            if not dry:
                if cur:
                    cur.is_current = False
                    cur.effective_to = EFF
                db.add(SpecialtyLeadershipHistory(
                    specialty_id=sp.id, person_id=lead.id, role="lead",
                    effective_from=EFF, is_current=True,
                    change_reason=REASON, source="manual",
                ))
                sp.current_lead_id = lead.id

    # 5.3 5 人迁入新专项（端旧内部/外部投诉专项）
    n = 0
    for pname in COMPLAINT_TEAM:
        p = _p(db, pname)
        if p is None:
            print(f"  ⚠️ [缺失] 人「{pname}」")
            continue
        # 端掉旧专项（内/外部投诉）
        olds = db.scalars(select(PersonSpecialtyAssignment).where(
            PersonSpecialtyAssignment.person_id == p.id,
            PersonSpecialtyAssignment.is_current.is_(True),
            PersonSpecialtyAssignment.specialty_id != sp.id,
        )).all()
        old_names = []
        for a in olds:
            o = db.get(Specialty, a.specialty_id)
            old_names.append(o.name if o else str(a.specialty_id))
            if not dry:
                a.is_current = False
                a.effective_to = EFF
                a.change_reason = f"{REASON}·并入投诉专项"
        _log(dry, f"{pname}: 专项 {','.join(old_names) or '无'} → {COMPLAINT_SPECIALTY}")
        if not dry:
            create_assignment(db, p.id, _mk_payload(
                position=p.position, specialty=COMPLAINT_SPECIALTY,
                group_name=p.group_name, reason=f"{REASON}·并入投诉专项",
            ))
        n += 1
    return n


# ══════════════════════════════════════════════════════════════════
# 步骤 6: 张蕾离职
# ══════════════════════════════════════════════════════════════════
def step_resign(db: Session, dry: bool) -> int:
    print("\n⑦ 张蕾离职（2026-09-01）")
    p = _p(db, "张蕾")
    if p is None:
        print("  ⚠️ [缺失] 张蕾")
        return 0
    n = 0
    _log(dry, f"张蕾: 在职状态 {p.employment_status} → resigned（离职日 {EFF}）")
    if not dry:
        p.resignation_date = EFF
        p.employment_status = "resigned"
    n += 1
    # 卸任组长
    g = _g(db, "投诉专项组")
    if g:
        cur = db.scalar(select(GroupLeadershipHistory).where(
            GroupLeadershipHistory.group_id == g.id,
            GroupLeadershipHistory.role == "leader",
            GroupLeadershipHistory.is_current.is_(True),
        ))
        if cur and cur.person_id == p.id:
            print("  （其组长职务已由步骤②转给洪佳）")
    return n


# ⑧ 需**卸任**的旧职务（组长/副主管 分属两个 role，必须分别清理）
# 依据：实测「吴晓龙组」原本只有 `role='deputy'`（吴晓龙），**没有 leader**
#   → 他转任专项1组组长后，若不清掉这条 deputy，会残留
#     「既是组3副主管、又是组5组长，且已不是组3成员」的矛盾状态。
END_LEADERSHIP = [
    # (人, 组名【用改后的新名，便于改名后重跑】, role)
    ("吴晓龙", "接待3组", "deputy"),
]


def step_end_leadership(db: Session, dry: bool) -> int:
    print("\n⑤ 卸任残留职务（组长/副主管 分属不同 role，需分别清理）")
    n = 0
    for pname, gname, role in END_LEADERSHIP:
        p = _p(db, pname)
        if p is None:
            print(f"  ⚠️ [缺失] 人「{pname}」")
            continue
        # 用**新组名**查（_g 已自带"新名→旧名"回退，改名前后都能命中）。
        # ⚠️ 曾用旧名查 → 步骤①改名后查不到 → **静默跳过**，
        #    实测残留「吴晓龙 同时为 接待3组副主管 + 专项1组组长」的矛盾状态。
        #    现改为显式报错，不充许静默跳过。
        g = _g(db, gname)
        if g is None:
            raise RuntimeError(f"卸任步骤找不到分组「{gname}」—— 必须修好，不可静默跳过")
        cur = db.scalar(select(GroupLeadershipHistory).where(
            GroupLeadershipHistory.group_id == g.id,
            GroupLeadershipHistory.person_id == p.id,
            GroupLeadershipHistory.role == role,
            GroupLeadershipHistory.is_current.is_(True),
        ))
        if cur is None:
            print(f"  [跳过] {pname} 无 {g.name}/{role} 现职")
            continue
        _log(dry, f"卸任 {pname} 的 {g.name}/{role}")
        if not dry:
            cur.is_current = False
            cur.effective_to = EFF
            cur.change_reason = f"{REASON}·卸任"
            if role == "leader":
                g.current_leader_id = None
            else:
                g.current_deputy_leader_id = None
        n += 1
    return n


def step_cleanup_departed(db: Session, dry: bool) -> int:
    """⑧ 离职人员清理：结束其组关系 + 卸任**旧投诉专项**负责人。

    ★ 为什么必须做：
      · 实测张蕾离职后仍是 **「内部投诉」专项 lead（is_current=true）**
        → 报表会把已离职的人算作在岗负责人；
      · 她仍在「投诉专项组」成员表里（is_current=true）
        → 组的"当前人数"虚高。

    ⚠️ 只**结束**（is_current=False + effective_to），**不物理删除**
       —— 历史区间需要这些记录来回溯"当时谁是负责人"。
    """
    print("\n⑧ 离职人员清理")
    n = 0
    p = _p(db, "张蕾")
    if p is None:
        raise RuntimeError("找不到 张蕾")

    # 8.1 卸任「内部投诉」专项负责人（该专项已并入投诉专项，不再需要 lead）
    stale = db.scalars(select(SpecialtyLeadershipHistory).where(
        SpecialtyLeadershipHistory.person_id == p.id,
        SpecialtyLeadershipHistory.is_current.is_(True),
    )).all()
    for h in stale:
        s = db.get(Specialty, h.specialty_id)
        sname = s.name if s else str(h.specialty_id)
        _log(dry, f"张蕾 卸任「{sname}」专项 {h.role}")
        if not dry:
            h.is_current = False
            h.effective_to = EFF
            h.change_reason = f"{REASON}·离职卸任"
            if s:
                if h.role == "lead":
                    s.current_lead_id = None
                else:
                    s.current_deputy_lead_id = None
        n += 1

    # 8.2 结束组成员关系
    m = db.scalar(select(PersonGroupMembership).where(
        PersonGroupMembership.person_id == p.id,
        PersonGroupMembership.is_current.is_(True),
    ))
    if m:
        g = db.get(Group, m.group_id)
        _log(dry, f"张蕾 退出分组「{g.name if g else m.group_id}」")
        if not dry:
            m.is_current = False
            m.effective_to = EFF
            m.change_reason = f"{REASON}·离职"
            p.current_group_id = None
            p.group_name = None
        n += 1
    else:
        print("  [跳过] 张蕾 无当前组关系")

    return n


def _mk_payload(**kw):
    from app.schemas.staff_assignment import StaffAssignmentCreate
    return StaffAssignmentCreate(
        effective_from=EFF,
        position=kw.get("position"),
        specialty=kw.get("specialty"),
        group_name=kw.get("group_name"),
        change_reason=kw.get("reason", REASON),
        source="manual",
    )


def _keep_specialty(db: Session, p: Person) -> str | None:
    """保持不变动时的现专项（避免 create_assignment 把 specialty 置空）"""
    return p.specialty or p.primary_specialty


def main() -> None:
    ap = argparse.ArgumentParser(description="2026-09-14 组织架构变更")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="只预览，不写库")
    g.add_argument("--apply", action="store_true", help="真正写库")
    a = ap.parse_args()
    dry = a.dry_run

    print("=" * 74)
    print(f" 2026-09-14 组织架构变更  |  {'★ 预览模式（不写库）' if dry else '★★ 执行模式（写库）'}")
    print(f" 生效日期: {EFF}   事务: 单事务，失败整体回滚")
    print("=" * 74)

    db = SessionLocal()
    try:
        step_rename_groups(db, dry)
        step_transfer_leaders(db, dry)
        step_move_members(db, dry)
        step_positions(db, dry)
        step_end_leadership(db, dry)
        step_merge_complaint_specialty(db, dry)
        step_resign(db, dry)
        step_cleanup_departed(db, dry)

        print("\n" + "=" * 74)
        print(" ⚠️ 需业务复核、本脚本**不自动执行**的项：")
        for k, v in SPECIALTY_HOLD.items():
            print(f"   · {k}: {v}")
        print("=" * 74)

        if dry:
            db.rollback()
            print("\n✅ 预览完成，**未写库**。确认无误后加 --apply 执行。")
        else:
            db.commit()
            print("\n✅ 已提交。建议随后执行：重建缓存 + 校验看板数字。")
    except Exception as e:
        db.rollback()
        print(f"\n❌ 失败已回滚: {type(e).__name__}: {e}", file=sys.stderr)
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
