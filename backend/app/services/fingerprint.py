"""业务去重指纹(md5)单源组装入口 —— O-6 / S-P2-1

⚠️ 铁律#9（数据采集合并去重机制说明.md）：去重键语义**不可变**。
本模块是全部业务 md5 指纹的**唯一组装点**；任何新采集通道需要指纹时，
必须在此新增语义明确的函数，禁止在调用方就地 hashlib.md5 拼装
（历史教训：upsert/_complaints.py 与 complaint_sync.py 曾各持一份
byte 级重复实现，漂移即产生重复入库）。

安全用途（token/sso/密码）的 sha256/hmac 不属于本模块范围。
"""

import hashlib


def md5_hex(raw: str) -> str:
    """md5 hexdigest 原语（32 字符）。业务指纹唯一直接调用 hashlib 的位置。"""
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def complaint_dedup_hash(parts: list[str | None]) -> str:
    """投诉域指纹：strip 每个片段后 "|" 拼接，取 md5（32 位）。

    覆盖 external_complaints / card_complaints 的主指纹与弱指纹（#59）。
    历史实现里 `[:32]` 截断是 no-op（md5 hexdigest 恒为 32 字符），此处不再保留。
    调用方（同口径，禁止各自重写）：
      - upsert/_complaints.py::_complaint_identity_hash（手动导入）
      - complaint_sync.py::_identity_dedup_hash / _weak_identity_hash（推送同步）
    """
    key = "|".join((p or "").strip() for p in parts)
    return md5_hex(key)


def staff_unique_key(
    source_config_id: int | None,
    wecom_nick: str,
    enterprise_suffix: str | None,
) -> str:
    """SCRM 人设业务唯一键。

    语义与历史 `scrm_staff_service.build_staff_unique_key` **byte 级一致**：
    - None 的 source_config_id 记为 "0"（非空串）
    - wecom_nick / enterprise_suffix **不 strip**（避免既有键值漂移）
    """
    raw = f"{source_config_id or 0}|{wecom_nick}|{enterprise_suffix or ''}"
    return md5_hex(raw)
