"""投诉域 upsert: external_complaints / card_complaints + 指纹去重键(与 complaint_sync 同口径)。"""

import re

import pandas as pd
from sqlalchemy.orm import Session

from app.models.card_complaint import CardComplaint
from app.models.external_complaint import ExternalComplaint

# 跨子模块引用(拆分自 upsert.py, 原同文件内直接调用)
from app.services.fingerprint import complaint_dedup_hash
from app.services.upsert._core import (
    ImportResult,
    _execute_upsert,
    _sanitize_date,
    _sanitize_name_field,
    _sync_devices,
    _to_datetime,
    apply_mapping,
)


def _complaint_dedup_hash(parts: list[str]) -> str:
    """投诉身份指纹(md5前32位): 拼接规范化身份字段, 供 dedup_hash 列去重.

    与 workbuddy 同步服务(complaint_sync.py)保持同一口径, 保证手动导入与推送共用去重.
    O-6: 实现已单源化到 app.services.fingerprint.complaint_dedup_hash, 本函数仅作兼容委托。
    """
    return complaint_dedup_hash(parts)

def _complaint_problem_signature(problem: str | None, max_len: int = 2000) -> str:
    """投诉问题内容签名: 归一化(去空白/标点/小写)后取前 max_len 字.

    用于身份指纹的内容环节: 轻微文本变化(空格/标点)不影响指纹,
    实质性不同内容(手机号→多SN、SN→多投诉 的区分)产生不同指纹.
    """
    s = re.sub(r"[\s，。；、,.!?！？:：;；\"'“”‘’()（）\[\]【】\-—]+", "", str(problem or ""))
    return s[:max_len]

def _complaint_identity_hash(rec: dict) -> str:
    """身份指纹: md5(投诉日期 | 投诉手机号 | 设备SN | 归一化投诉问题)

    2026-08-20 指纹方案重规划(两版迭代):
    - v1 (编号|日期|手机|SN|问题): 编号上游易丢(53%)导致同一投诉拆两条 → 弃用编号
    - v2 (日期|手机|SN): 区分度不足, 同手机+同SN+同日期 不同投诉会误合并 → 加入内容
    - 现版 (日期|手机|SN|问题归一化): 内容环节解决误合并(实测误合并仅15行),
      跨文件匹配仍99.8%(问题文本微改不影响), 编号仍不入指纹(丢失免疫)
    """
    d = rec.get("complaint_date")
    date_s = d.strftime("%Y-%m-%d") if d else ""
    return _complaint_dedup_hash([
        date_s,
        rec.get("complainant_phone"),
        rec.get("device_sn"),
        _complaint_problem_signature(rec.get("problem")),
    ])

def upsert_external_complaints(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    """外部投诉(监督局/315) - 按 dedup_hash 身份指纹去重更新

    2026-08-20 变更: 原按 complaint_no 去重, 但样例文件中存在同编号多条不同投诉
    (含 "/" 等垃圾编号), 单一编号不可靠。现改用身份指纹
    md5(编号|日期|手机号|SN|问题) 去重: 指纹相同视为同一条投诉 → 更新,
    指纹不同(含编号相同但内容不同的投诉) → 各自保留。重复推送不会累积脏数据。
    """
    rows: list[dict] = []
    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            # 投诉编号剥离 \n/空白, 非法值(垃圾编号)置空
            raw_no = data.get("complaint_no")
            complaint_no = raw_no.replace("\n", "").strip() if isinstance(raw_no, str) else None
            if complaint_no and not re.fullmatch(r"[0-9A-Za-z\-]{6,64}", complaint_no):
                complaint_no = None
            rec = {
                "complaint_no": complaint_no or None,
                "complaint_date": _sanitize_date(_to_datetime(data.get("complaint_date"))),
                "platform": data.get("platform"),
                "complaint_account": data.get("complaint_account"),
                "company_entity": data.get("company_entity"),
                "complaint_shop": data.get("complaint_shop"),
                "purchase_order_no": data.get("purchase_order_no"),
                "complainant": _sanitize_name_field(data.get("complainant"), idx, result, "外部投诉-投诉人"),
                "complainant_phone": data.get("complainant_phone"),
                "device_sn": data.get("device_sn"),
                "wechat_id": data.get("wechat_id"),
                "problem": data.get("problem"),
                "keyword": data.get("keyword"),
                "complaint_category": data.get("complaint_category"),
                "solution": data.get("solution"),
                "result": data.get("result"),
                "follower": _sanitize_name_field(data.get("follower"), idx, result, "外部投诉-跟进人"),
                "handle_time": _sanitize_date(_to_datetime(data.get("handle_time"))),
                "handle_detail": data.get("handle_detail"),
            }
            rec["dedup_hash"] = _complaint_identity_hash(rec)
            rows.append(rec)
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}")
    all_rows = rows
    if not all_rows:
        return
    _sync_devices(all_rows, "device_sn", db)
    _execute_upsert(db, ExternalComplaint, "dedup_hash", all_rows, result)

def upsert_card_complaints(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    """卡商群投诉 - 按 dedup_hash 身份指纹去重更新

    2026-08-20 变更: 原为无唯一键简单 insert(重复导入会累积),现改用身份指纹
    md5(日期|群|手机号|SN|问题) 去重, 重复推送不累积。device_sn 单元格可能含
    多个 SN(换行分隔)或备注,取首个有效 SN 以保证 varchar(64) 与外键约束。
    """
    rows: list[dict] = []
    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            # device_sn 取首个(单元格可能含多个 SN,换行分隔)
            raw_sn = data.get("device_sn")
            device_sn = raw_sn.split("\n")[0].strip() if isinstance(raw_sn, str) and raw_sn.strip() else None
            rec = {
                "complaint_date": _sanitize_date(_to_datetime(data.get("complaint_date"))),
                "complaint_group": data.get("complaint_group"),
                "complainant": _sanitize_name_field(data.get("complainant"), idx, result, "卡商群投诉-投诉人"),
                "complainant_phone": data.get("complainant_phone"),
                "device_sn": device_sn,
                "problem": data.get("problem"),
                "problem_category": data.get("problem_category"),
                "result": data.get("result"),
                "solution": data.get("solution"),
                "handler": _sanitize_name_field(data.get("handler"), idx, result, "卡商群投诉-处理人"),
                "handle_result": data.get("handle_result"),
            }
            rec["dedup_hash"] = _complaint_identity_hash(rec)
            rows.append(rec)
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}")
    if not rows:
        return
    _sync_devices(rows, "device_sn", db)
    _execute_upsert(db, CardComplaint, "dedup_hash", rows, result)
