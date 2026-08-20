"""存量投诉数据按新身份指纹(日期|手机|SN)重算 dedup_hash 并去重

背景: 2026-08-20 指纹方案从 (编号|日期|手机|SN|问题) 重规划为 (日期|手机|SN),
解决 workbuddy 丢编号/改内容导致指纹分裂重复的问题。存量行指纹需按新口径重算:
1. 计算新指纹, 按指纹分组
2. 每组保留"信息最全"的一条(编号非空+2 / 问题非空+1 / SN非空+1 / 已关联+1, 同分取先入库)
3. 删除同组其余行(否则唯一索引冲突), 保留行更新为新指纹
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.card_complaint import CardComplaint
from app.models.external_complaint import ExternalComplaint
from app.services.complaint_sync import _identity_dedup_hash


def rebuild(model, label: str) -> None:
    db = SessionLocal()
    try:
        rows = list(db.scalars(select(model)))
        print(f"[{label}] 处理前行数: {len(rows)}")

        groups: dict[str, list] = {}
        for r in rows:
            rec = {
                "complaint_date": r.complaint_date,
                "complainant_phone": r.complainant_phone,
                "device_sn": r.device_sn,
                "problem": r.problem,  # ⚠️ 必须包含问题(指纹含内容环节)
            }
            h = _identity_dedup_hash(rec)
            groups.setdefault(h, []).append(r)

        keep_ids: list[int] = []
        drop_ids: list[int] = []
        for h, members in groups.items():
            def score(m):
                s = 0
                if getattr(m, "complaint_no", None):
                    s += 2
                if m.problem:
                    s += 1
                if m.device_sn:
                    s += 1
                if m.matched_ticket_no:
                    s += 1
                return s
            best = max(members, key=lambda m: (score(m), -m.id))
            keep_ids.append(best.id)
            for m in members:
                if m.id != best.id:
                    drop_ids.append(m.id)

        # 先置空指纹(唯一索引允许 NULL 重复), 再删除多余行, 最后更新保留行指纹
        for r in rows:
            r.dedup_hash = None
        db.flush()
        if drop_ids:
            db.execute(model.__table__.delete().where(model.id.in_(drop_ids)))
        db.flush()
        for r in rows:
            if r.id in keep_ids:
                rec = {
                    "complaint_date": r.complaint_date,
                    "complainant_phone": r.complainant_phone,
                    "device_sn": r.device_sn,
                    "problem": r.problem,
                }
                r.dedup_hash = _identity_dedup_hash(rec)
        db.commit()
        after = db.scalar(select(__import__("sqlalchemy").func.count()).select_from(model)) or 0
        print(f"[{label}] 保留 {len(keep_ids)} 条, 删除重复 {len(drop_ids)} 条, 处理后行数: {after}")
    finally:
        db.close()


rebuild(ExternalComplaint, "external_complaints")
rebuild(CardComplaint, "card_complaints")
