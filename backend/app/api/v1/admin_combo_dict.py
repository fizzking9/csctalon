"""套餐归一字典管理（admin）— 工单 #288（套餐簇启用）

- GET  /admin/combo-dict/stats   当前字典/别名计数与种子规模
- POST /admin/combo-dict/apply   幂等灌入官方种子(635 归一码 / 1161 全局别名)

种子只新增不覆盖, 消费侧见 analytics/category.py 与 analytics/complaint.py。
"""

import logging

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import AdminUser, DBSession
from app.models.combo_dict import ComboDict, ComboNameAlias
from app.schemas.common import R
from app.services import combo_dict_seed

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/combo-dict", tags=["admin-combo-dict"])


@router.get("/stats", response_model=R, summary="套餐归一字典统计(admin)")
def stats(db: DBSession, _: AdminUser):
    # 当前全局别名计数 = 种子实际生效规模; 商户级别名另计
    total_aliases = db.scalar(select(func.count()).select_from(ComboNameAlias)) or 0
    global_current = db.scalar(
        select(func.count()).select_from(ComboNameAlias).where(
            ComboNameAlias.merchant_pk.is_(None),
            ComboNameAlias.is_current.is_(True),
        )
    ) or 0
    payload = combo_dict_seed.load_payload()
    return R(data={
        "combos": db.scalar(select(func.count()).select_from(ComboDict)) or 0,
        "aliases_total": total_aliases,
        "global_current_aliases": global_current,
        "seed_version": payload.get("version"),
        "seed_combos": len(payload["combos"]),
        "seed_aliases": len(payload["aliases"]),
    })


@router.post("/apply", response_model=R, summary="幂等灌入套餐归一种子(admin)")
def apply(db: DBSession, _: AdminUser):
    # 只新增不覆盖; 重复调用返回 0 新增, 安全
    result = combo_dict_seed.apply_combo_seed(db)
    return R(data=result, message="套餐归一种子应用完成")
