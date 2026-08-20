"""一次性脚本: 回填 external_complaints / card_complaints 存量行的 dedup_hash

背景: 2026-08-20 新增 dedup_hash 业务去重指纹列(唯一索引)。
存量行(此前导入的 1312 条315 / 719 条卡商)该列为 NULL, 若不回填,
workbuddy 首推整表时因 NULL 不触发唯一冲突会全部重复插入 → 数据翻倍。

回填口径必须与 app.services.complaint_sync / app.services.upsert 的
_complaint_dedup_hash 完全一致(否则指纹对不上, 去重失效):
- external: md5(编号|日期iso|手机号|SN|问题)
- card:     md5(日期iso|群|手机号|SN|问题)
日期取 datetime.isoformat()(timezone-aware 输出带 +00:00 后缀)。

对回填后指纹重复的存量行(历史脏数据)保留最小 id, 其余删除。

用法(在 backend/ 目录, DB 环境变量已配好):
    python scripts/_backfill_complaint_dedup.py
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.models.card_complaint import CardComplaint
from app.models.external_complaint import ExternalComplaint
from app.services.upsert import _complaint_dedup_hash

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def _backfill(model, parts_fn, label: str) -> None:
    db = SessionLocal()
    try:
        rows = list(db.scalars(select(model).where(model.dedup_hash.is_(None))))
        logger.info(f"[backfill] {label}: 待回填 {len(rows)} 行")
        if not rows:
            return
        seen: dict[str, int] = {}
        dup_ids: list[int] = []
        for r in rows:
            h = _complaint_dedup_hash(parts_fn(r))
            r.dedup_hash = h
            if h in seen:
                # 保留最小 id, 删除后出现的
                if r.id < seen[h]:
                    dup_ids.append(seen[h])
                    seen[h] = r.id
                else:
                    dup_ids.append(r.id)
            else:
                seen[h] = r.id
        if dup_ids:
            db.execute(delete(model).where(model.id.in_(dup_ids)))
            logger.warning(f"[backfill] {label}: 指纹重复删除 {len(dup_ids)} 行(保留最小id)")
        db.commit()
        logger.info(f"[backfill] {label}: 完成, 回填后行数={len(rows) - len(dup_ids)}")
    finally:
        db.close()


def main() -> None:
    _backfill(
        ExternalComplaint,
        lambda r: [
            r.complaint_no,
            r.complaint_date.isoformat() if r.complaint_date else "",
            r.complainant_phone,
            r.device_sn,
            r.problem,
        ],
        "external_complaints",
    )
    _backfill(
        CardComplaint,
        lambda r: [
            r.complaint_date.isoformat() if r.complaint_date else "",
            r.complaint_group,
            r.complainant_phone,
            r.device_sn,
            r.problem,
        ],
        "card_complaints",
    )


if __name__ == "__main__":
    main()
