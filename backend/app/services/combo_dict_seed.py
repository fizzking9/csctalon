"""套餐归一字典种子加载 (#288 套餐簇启用)

数据来源: 生产三源套餐原始名(devices.current_combo_name / device_combos.package_name
/ refunds.package_name)归一解析结果, 覆盖率 98.3% 行, 635 归一码 / 1161 别名。
解析签名: (家族, 流量GB, 时长, 通道), 详见 reports 与本工单说明; 249 归并组已
人工审查(全部为符号/格式差异: */&/×、GB→G、全半角括号、促销后缀等)。

幂等规则:
- combo_dict: 缺失才插入, 不覆盖人工维护的标准名/状态。
- combo_name_alias: 同一 (raw_package_name, merchant_pk=NULL) 当前映射已存在则跳过,
  不覆盖人工/商户级映射; 不做关闭与删除。
- effective_from: 取该原始名在生产事实表中最早出现日期(上海时区), 无记录时种子内置日期。

未命中别名的约 1.7% 长尾原始名由消费侧回落原始名, 不影响历史数字。
"""

import json
import logging
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.combo_dict import ComboDict, ComboNameAlias

logger = logging.getLogger(__name__)

_SEED_FILE = Path(__file__).with_name("combo_dict_seed_data.json")


def load_payload() -> dict:
    """读取随代码发布的种子 JSON (635 combos / 1161 aliases)。"""
    with _SEED_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def _parse_date(s: str) -> date:
    # 种子日期为 ISO 格式, 统一转 date 对象写入 Date 列
    return date.fromisoformat(s)


def apply_combo_seed(db: Session) -> dict:
    """幂等灌入套餐归一主字典与全局别名, 返回新增/跳过计数。

    由 admin 端点显式触发(一次性初始化), 不挂启动 lifespan, 避免每次启动竞争。
    """
    payload = load_payload()

    existing_codes = set(db.scalars(select(ComboDict.combo_code)).all())
    combos_added = 0
    for row in payload["combos"]:
        if row["combo_code"] in existing_codes:
            continue
        db.add(ComboDict(
            combo_code=row["combo_code"],
            canonical_name=row["canonical_name"],
            package_type=row.get("package_type"),
            traffic_type=row.get("traffic_type"),
            status=row.get("status", "active"),
        ))
        combos_added += 1

    # 已存在的"当前"全局别名原始名集合(本种子只灌全局别名 merchant_pk=NULL)
    existing_raw = set(db.scalars(
        select(ComboNameAlias.raw_package_name).where(
            ComboNameAlias.merchant_pk.is_(None),
            ComboNameAlias.is_current.is_(True),
        )
    ).all())
    aliases_added = 0
    for row in payload["aliases"]:
        raw = row["raw_package_name"]
        if raw in existing_raw:
            continue
        db.add(ComboNameAlias(
            combo_code=row["combo_code"],
            raw_package_name=raw,
            merchant_pk=None,
            effective_from=_parse_date(row["effective_from"]),
            effective_to=None,
            is_current=True,
            source="seed",
        ))
        # 加入集合保证同批内去重(部分唯一索引兜底)
        existing_raw.add(raw)
        aliases_added += 1

    db.commit()
    logger.info(
        "套餐归一种子应用完成: combos +%d (种子 %d), aliases +%d (种子 %d)",
        combos_added, len(payload["combos"]), aliases_added, len(payload["aliases"]),
    )
    return {
        "version": payload.get("version"),
        "combos_added": combos_added,
        "combos_in_seed": len(payload["combos"]),
        "aliases_added": aliases_added,
        "aliases_in_seed": len(payload["aliases"]),
    }
