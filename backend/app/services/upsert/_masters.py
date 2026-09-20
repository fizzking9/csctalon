"""主数据/设备域 upsert: merchants / device_* / refund_record_reports / bms_orders / return_trackings / device_profiles。"""

from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.bms_order import BmsOrder
from app.models.device import Device, DeviceTag, DeviceTagBinding
from app.models.device_combo import DeviceCombo
from app.models.device_flow_usage import DeviceFlowUsage
from app.models.merchant import Merchant
from app.models.refund_record_report import RefundRecordReport
from app.models.return_tracking import ReturnTracking

# 跨子模块引用(拆分自 upsert.py, 原同文件内直接调用)
from app.services.upsert._core import (
    _TZ_CN,
    ImportResult,
    _execute_upsert,
    _sanitize_name_field,
    _sync_devices,
    _to_datetime,
    _to_float,
    _to_int,
    _to_str,
    _truncate_str,
    apply_mapping,
)


def upsert_merchants(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    rows: list[dict] = []
    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            sub_name = (data.get("sub_name") or "").strip() if data.get("sub_name") else None
            if not sub_name:
                continue
            rows.append({
                "sub_name": sub_name,
                "main_name": data.get("main_name"),
                "merchant_id": data.get("merchant_id"),
                "legal_person": data.get("legal_person"),
                "super_admin": data.get("super_admin"),
            })
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}")
    _execute_upsert(db, Merchant, "sub_name", rows, result)

def upsert_device_combos(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    """设备套餐 - 主键 recharge_order_no,有则更新无则插入

    初始套餐(免费赠送/体验套餐)在 BMS 导出时无充值订单号,此前被主键非空校验跳过,
    导致这部分记录缺失。现为其生成稳定 fallback 主键 `INIT::{device_sn}::{package_name}::{start_date}`:
      - `INIT::` 前缀与真实订单号(BR/WX/SP 开头)隔离,绝不冲突
      - (device_sn, package_name, start_date) 组合唯一,保证多次增量同步幂等(命中 on_conflict_do_update)
      - device_sn 缺失时无法构造稳定唯一键,才报错跳过(安全网,正常初始套餐数据不触发)
    """
    rows: list[dict] = []
    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            order_no = (data.get("recharge_order_no") or "").strip() if data.get("recharge_order_no") else None
            device_sn = data.get("device_sn")
            package_name = data.get("package_name")
            start_date = _to_datetime(data.get("start_date"))

            if not order_no:
                # 无充值订单号(典型:初始套餐),生成 fallback 主键入库
                if not device_sn:
                    result.add_error(idx, "充值订单号为空且设备SN为空,无法生成主键")
                    continue
                start_str = start_date.strftime("%Y-%m-%d") if start_date else "unknown"
                order_no = f"INIT::{device_sn}::{package_name or 'unknown'}::{start_str}"

            rows.append({
                "recharge_order_no": order_no,
                "device_sn": device_sn,
                "package_name": package_name,
                "package_type": data.get("package_type"),
                "traffic_type": data.get("traffic_type"),
                "merchant_name": data.get("merchant_name"),
                "actual_amount": _to_float(data.get("actual_amount")),
                "start_date": start_date,
                "end_date": _to_datetime(data.get("end_date")),
                "updated_at_raw": _to_datetime(data.get("updated_at_raw")),
                "created_at_raw": _to_datetime(data.get("created_at_raw")),
                "valid_days": _to_int(data.get("valid_days")),
                "creator": data.get("creator"),
                "available_traffic": _to_int(data.get("available_traffic")),
                "used_traffic": _to_int(data.get("used_traffic")),
                "remaining_traffic": _to_int(data.get("remaining_traffic")),
                "status": data.get("status"),
            })
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}")
    if not rows:
        return
    _sync_devices(rows, "device_sn", db)
    _execute_upsert(db, DeviceCombo, "recharge_order_no", rows, result)

def upsert_device_tags(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    """设备标签定义 - 主键 id,无 id 时按 tag_name upsert(避免重复名)

    BMS 导出的标签字典通常含:标签ID、标签名称、标签类型、颜色、描述等。
    若 BMS 未导出 id:优先用 tag_name 唯一键做 upsert。

    ⚠️ P0-2 修复: 早期迁移脚本可能导致 device_tags.id 列的 nextval(序列) 默认值
    未被正确设置, bulk insert 时若不带 id 字段会抛 NotNullViolation: "null value in
    column \"id\" of relation \"device_tags\" violates not-null constraint"。
    修复策略: 对缺 id 的批次,统一手动从 device_tags_id_seq 取 nextval 补齐,避免依赖 DB 默认值。
    """
    from sqlalchemy import text as sa_text
    rows: list[dict] = []
    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            tag_id = _to_int(data.get("id"))
            tag_name = _to_str(data.get("tag_name"))
            if not tag_name:
                result.add_error(idx, "标签名称为空")
                continue
            tag_name = tag_name.strip()
            tag_type = _to_str(data.get("tag_type")) or "preset"
            # tag_type 归一:中文(预设/自定义)→英文 preset/custom
            if tag_type in ("预设", "默认", "系统", "sys", "default"):
                tag_type = "preset"
            elif tag_type in ("自定义", "用户", "自定义标签", "custom", "user"):
                tag_type = "custom"
            r: dict = {
                "tag_name": tag_name,
                "tag_type": tag_type,
                "color": _to_str(data.get("color")),
                "description": _to_str(data.get("description")),
            }
            if tag_id is not None:
                r["id"] = tag_id
            rows.append(r)
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}")
    if not rows:
        return

    # ── P0-2: 对缺 id 的行手动补 nextval(device_tags_id_seq),避免 DB 默认值缺失
    has_seq = True
    try:
        nextval_sql = sa_text("SELECT nextval(pg_get_serial_sequence('device_tags','id'))")
    except Exception:
        has_seq = False
    for r in rows:
        if "id" in r and r["id"] is not None:
            continue
        if has_seq:
            try:
                seq_val = db.scalar(nextval_sql)
                if seq_val is not None:
                    r["id"] = int(seq_val)
                    continue
            except Exception:
                # nextval 失败(例如序列名不对) → fallback 按 tag_name 查询避免重复 id
                pass
        # Fallback: query max(id) + 1(纯内存自增,有并发风险但 device_tags 导入很少,且 tag_name 有唯一索引兜底)
        try:
            from sqlalchemy import func as sa_fn
            from sqlalchemy import select as sa_sel
            max_id = db.scalar(sa_sel(sa_fn.max(DeviceTag.id))) or 0
            # 临时用 max_id+offset:tag_name 唯一索引会冲突时在 on_conflict 里 merge, 不影响正确性
            r["id"] = int(max_id) + len(rows) + rows.index(r) + 1  # 确保本批次内不重复
        except Exception:
            r["id"] = 10_000 + rows.index(r)

    # ── 同批 tag_name 去重(#38) ──
    # 源文件(尤其从打标明细反推/手填字典)同一标签名可能出现多行;
    # 若不先去重, 单条 INSERT ... ON CONFLICT(tag_name) DO UPDATE 遇同批重复冲突键
    # 会抛 CardinalityViolation: cannot affect row a second time(PG 硬限制)。
    # 保留最后一条(源文件靠后行为更新值, 与 on_conflict do_update 语义一致);
    # 被去重行数是 rows 去重前后差, 不逐行告警(明细来源可能大量重复, 静默去重即可)。
    _dedup_seen: set[str] = set()
    _dedup_rows: list[dict] = []
    for r in rows:
        _key = str(r.get("tag_name") or "").strip()
        if not _key:
            continue
        if _key in _dedup_seen:
            continue
        _dedup_seen.add(_key)
        _dedup_rows.append(r)
    rows = _dedup_rows

    # 统一使用 pg_insert on_conflict_do_update (tag_name 有唯一索引已覆盖所有冲突)
    stmt = pg_insert(DeviceTag).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["tag_name"],
        set_={
            "tag_type": stmt.excluded.tag_type,
            "color": stmt.excluded.color,
            "description": stmt.excluded.description,
        },
    )
    res = db.execute(stmt)
    # Postgres on_conflict_do_update 不返回插入/更新计数,这里用近似值
    inserted_or_updated = res.rowcount or 0
    result.inserted = inserted_or_updated
    db.flush()

def upsert_device_tag_bindings(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    """设备-标签绑定明细(打标记录) - 复合主键 (device_sn, tag_id)

    BMS 导出通常包含:设备SN、标签名称/标签ID、打标人、打标时间、设备所属商户、设备当前套餐等。
    步骤:
      1. 若导出的是 tag_name,先查 device_tags 表按 tag_name 解析出 tag_id
      2. 按 (device_sn, tag_id) 复合键 do_nothing upsert(重复绑定不报错)
      3. 顺带维护 devices 主表(出现新 SN 自动插入空壳 Device;并从导出列补 merchant_name/package_name 到 Device)
    """
    from sqlalchemy import select as sa_select

    rows: list[dict] = []
    tag_name_cache: dict[str, int] = {}
    # suggest_mapping 会返回 {target_field: source_col_str}, 所以这里取到的是字符串(源列名),需要统一包成 list
    _merchant_col: str | None = mapping.get("_merchant_name")  # type: ignore[assignment]
    _package_col: str | None = mapping.get("_package_name")  # type: ignore[assignment]
    alias_merchant: list[str] = [_merchant_col] if _merchant_col else []
    alias_package: list[str] = [_package_col] if _package_col else []

    def _resolve_tag_id(tag_name: str | None, tag_id_raw: object) -> int | None:
        if tag_id_raw is not None:
            tid = _to_int(tag_id_raw)
            if tid is not None:
                return tid
        if not tag_name:
            return None
        if tag_name in tag_name_cache:
            return tag_name_cache[tag_name]
        found = db.scalar(sa_select(DeviceTag).where(DeviceTag.tag_name == tag_name))
        if found:
            tag_name_cache[tag_name] = found.id
            return found.id
        # 标签不存在 → 临时插入一条 preset 标签(后续刷新 device_tags 数据源会覆盖)
        tmp = DeviceTag(tag_name=tag_name, tag_type="preset")
        db.add(tmp)
        db.flush()
        tag_name_cache[tag_name] = tmp.id
        return tmp.id

    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            device_sn = _to_str(data.get("device_sn"))
            if device_sn:
                device_sn = device_sn.strip()  # BMS 导出常带前后空白
            if not device_sn:
                result.add_error(idx, "设备SN为空")
                continue
            tag_name = _to_str(data.get("tag_name"))
            if tag_name:
                tag_name = tag_name.strip()
            tag_id = _resolve_tag_id(tag_name, data.get("tag_id"))
            if tag_id is None:
                result.add_error(idx, "标签ID和标签名称均为空,无法定位标签")
                continue
            created_by = _sanitize_name_field(data.get("created_by"), idx, result, "打标人")
            # 从原始 BMS 列名中取 merchant / package 补充给 Device
            merchant_name = package_name = None
            for ali in alias_merchant:
                v = _to_str(row.get(ali)) if isinstance(row, dict) else None
                if v:
                    merchant_name = v.strip()
                    break
            for ali in alias_package:
                v = _to_str(row.get(ali)) if isinstance(row, dict) else None
                if v:
                    package_name = v.strip()
                    break
            rows.append({
                "device_sn": device_sn,
                "tag_id": tag_id,
                "created_by": created_by,
                "merchant_name": merchant_name,
                "package_name": package_name,
            })
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}")
    if not rows:
        return
    # 维护 devices 主表(新 SN 先插入,避免外键报错;还补 merchant_name)
    # 注意: Device 表没有 package_name 字段,套餐名存在 device_combos,不往 Device 填
    _sync_devices(rows, "device_sn", db, fill_fields=["merchant_name"])
    # ⚠️ 复合主键 do_nothing 需要的 rows 里字段名必须与 DeviceTagBinding ORM 列完全一致
    # (注意: _sync_devices 会把 device_sn strip 了,但这里 rows 里的 device_sn 已经在上面 strip 过了 — 一致)
    bind_rows = [
        {
            "device_sn": r["device_sn"],
            "tag_id": r["tag_id"],
            "created_by": r.get("created_by"),
        }
        for r in rows
    ]
    stmt = pg_insert(DeviceTagBinding).values(bind_rows)
    stmt = stmt.on_conflict_do_nothing(index_elements=["device_sn", "tag_id"])
    res = db.execute(stmt)
    result.inserted = res.rowcount or 0
    db.flush()

def upsert_device_flow_usage(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    """设备月流量使用 - (device_sn, stat_month) 复合唯一键幂等 upsert

    来源: BMS 设备流量 HTTP API(flowDate 留空=全量有流量月份 / 指定月单查)。
    逐行解析后 on_conflict_do_update(唯一约束 uq_device_flow_usage_sn_month) 覆盖更新。
    不完整月(stat_month == 当前北京月)反复重取 = 覆盖更新, 不新增(用户需求)。
    """
    from datetime import datetime as _dt
    _now_cn = _dt.now(_TZ_CN)
    _cur_month = _now_cn.strftime("%Y-%m")
    rows: list[dict] = []
    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            device_sn = (_to_str(data.get("device_sn")) or "").strip() or None
            stat_month = (_to_str(data.get("stat_month")) or "").strip() or None
            if not device_sn or not stat_month:
                result.add_error(idx, "缺少 device_sn 或 stat_month,无法去重定位(跳过)")
                continue
            flow_gb = _to_float(data.get("flow_gb"))
            rows.append({
                "device_sn": device_sn,
                "merchant_id": _truncate_str(_to_str(data.get("merchant_id")), 64),
                "merchant_name": _truncate_str(_to_str(data.get("merchant_name")), 128),
                "device_type_name": _truncate_str(_to_str(data.get("device_type_name")), 64),
                "device_group_name": _truncate_str(_to_str(data.get("device_group_name")), 64),
                "device_combo_name": _truncate_str(_to_str(data.get("device_combo_name")), 128),
                "stat_month": stat_month,
                "flow_gb": flow_gb,
                "month_complete": stat_month < _cur_month,  # 非当前月视为完整(可历史补全); 当前月=false 可重取
                "fetched_at": _dt.now(_TZ_CN),
            })
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}")
    if not rows:
        return
    _sync_devices(rows, "device_sn", db, fill_fields=["merchant_name"])
    seen: dict[tuple, dict] = {}
    for r in rows:
        seen[(r["device_sn"], r["stat_month"])] = r
    rows = list(seen.values())
    before = db.scalar(select(func.count()).select_from(DeviceFlowUsage)) or 0
    BATCH = 2000
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        stmt = pg_insert(DeviceFlowUsage).values(batch)
        update_cols = {c: stmt.excluded[c] for c in batch[0]
                       if c not in ("device_sn", "stat_month")}
        stmt = stmt.on_conflict_do_update(
            index_elements=["device_sn", "stat_month"],
            set_=update_cols,
        )
        db.execute(stmt)
        db.flush()
    after = db.scalar(select(func.count()).select_from(DeviceFlowUsage)) or 0
    inserted = max(after - before, 0)
    result.inserted += inserted
    result.updated += max(len(rows) - inserted, 0)

def upsert_refund_record_reports(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    """退款记录报表 - (platform_order_no, refund_time, refund_amount) 幂等 upsert

    来源: BMS 退款记录报表导出 CSV(10列实测)。E 路线上退款水单。
    时间 refund_time naive-北京 → UTC(经 _to_datetime)。
    """
    from datetime import datetime as _dt
    _now = _dt.now(timezone.utc)
    rows: list[dict] = []
    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            platform_order_no = (_to_str(data.get("platform_order_no")) or "").strip() or None
            if not platform_order_no:
                result.add_error(idx, "缺少平台订单号(跳过)")
                continue
            refund_time = _to_datetime(data.get("refund_time"))
            if refund_time is None:
                result.add_error(idx, "退款时间为空(跳过)")
                continue
            refund_amount = _to_float(data.get("refund_amount"))
            if refund_amount is None:
                result.add_error(idx, "退款金额为空(跳过)")
                continue
            device_sn = (_to_str(data.get("device_sn")) or "").strip() or None
            rows.append({
                "platform_order_no": platform_order_no,
                "wx_order_no": _truncate_str((_to_str(data.get("wx_order_no")) or "").strip(), 128) or None,
                "merchant_id": _truncate_str((_to_str(data.get("merchant_id")) or "").strip(), 64) or None,
                "merchant_name": _truncate_str((_to_str(data.get("merchant_name")) or "").strip(), 128) or None,
                "device_sn": device_sn,
                "product_name": _truncate_str((_to_str(data.get("product_name")) or "").strip(), 128) or None,
                "refund_time": refund_time,
                "apply_refund_amount": _to_float(data.get("apply_refund_amount")),
                "refund_amount": refund_amount,
                "refund_status": _truncate_str((_to_str(data.get("refund_status")) or "").strip(), 32) or None,
                "synced_at": _now,
            })
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}")
    if not rows:
        return
    _sync_devices(rows, "device_sn", db, fill_fields=["merchant_name"])
    seen: dict[tuple, dict] = {}
    for r in rows:
        # 金额转字符串归一,避免 Decimal('19.00') vs '19' 视为不同键
        seen[(r["platform_order_no"], _str_ts(r["refund_time"]), str(r["refund_amount"]))] = r
    rows = list(seen.values())
    before = db.scalar(select(func.count()).select_from(RefundRecordReport)) or 0
    BATCH = 2000
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        stmt = pg_insert(RefundRecordReport).values(batch)
        update_cols = {c: stmt.excluded[c] for c in batch[0]
                       if c not in ("platform_order_no", "refund_time", "refund_amount")}
        # ⚠️ 唯一索引是表达式(经 _to_datetime 后 refund_time 已是 timestamptz, PG 存毫秒级),
        #    直接用列 on_conflict_do_update 可能不命中表达式唯一索引; 但 uq 是普通列唯一约束
        #    (platform_order_no, refund_time, refund_amount), 故 index_elements 用三列即可。
        stmt = stmt.on_conflict_do_update(
            index_elements=["platform_order_no", "refund_time", "refund_amount"],
            set_=update_cols,
        )
        db.execute(stmt)
        db.flush()
    after = db.scalar(select(func.count()).select_from(RefundRecordReport)) or 0
    inserted = max(after - before, 0)
    result.inserted += inserted
    result.updated += max(len(rows) - inserted, 0)

def upsert_bms_orders(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    """订单管理 - order_no 主键 upsert(幂等)

    来源: BMS 订单管理(导出/HTTP findPageList)。同步锚点=创建时间 create_date。
    订单量可能大(8月全月43万), 分批 2000 走 _execute_upsert 语义(单主键)。
    """
    rows: list[dict] = []
    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            order_no = (_to_str(data.get("order_no")) or "").strip()
            if not order_no:
                result.add_error(idx, "缺少订单号(跳过)")
                continue
            rows.append({
                "order_no": order_no,
                "order_type": _truncate_str((_to_str(data.get("order_type")) or "").strip(), 32) or None,
                "device_sn": (_to_str(data.get("device_sn")) or (_to_str(data.get("sn")) or "")).strip() or None,
                "combo_name": _truncate_str((_to_str(data.get("combo_name")) or "").strip(), 128) or None,
                "combo_price": _to_float(data.get("combo_price")),
                "payable_price": _to_float(data.get("payable_price")),
                "actual_pay_price": _to_float(data.get("actual_pay_price")),
                "balance_pay_price": _to_float(data.get("balance_pay_price")),
                "wx_order_no": _truncate_str((_to_str(data.get("wx_order_no")) or "").strip(), 128) or None,
                "pay_date": _to_datetime(data.get("pay_date")),
                "pay_type": _truncate_str((_to_str(data.get("pay_type")) or "").strip(), 32) or None,
                "order_status": _truncate_str((_to_str(data.get("order_status")) or "").strip(), 32) or None,
                "order_result": (data.get("order_result") or None) if data.get("order_result") is not None else None,
                "order_status_raw": _to_int(data.get("order_status_raw")),
                "create_user": _truncate_str((_to_str(data.get("create_user")) or "").strip(), 64) or None,
                "create_date": _to_datetime(data.get("create_date")),
                "business_name": _truncate_str((_to_str(data.get("business_name")) or "").strip(), 128) or None,
                "business_id": _truncate_str((_to_str(data.get("business_id")) or "").strip(), 64) or None,
                "device_type_name": _truncate_str((_to_str(data.get("device_type_name")) or "").strip(), 64) or None,
                "refund_status": _truncate_str((_to_str(data.get("refund_status")) or "").strip(), 32) or None,
                "active_date": _to_datetime(data.get("active_date")),
                "last_stored_time": _to_datetime(data.get("last_stored_time")),
                "mch_id": _truncate_str((_to_str(data.get("mch_id")) or "").strip(), 64) or None,
                "merchant_name": _truncate_str((_to_str(data.get("merchant_name")) or "").strip(), 128) or None,
                "before_serve_valid_date": _to_datetime(data.get("before_serve_valid_date")),
                "after_serve_valid_date": _to_datetime(data.get("after_serve_valid_date")),
                "days": _to_int(data.get("days")),
                "synced_at": datetime.now(timezone.utc),
            })
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}")
    if not rows:
        return
    _sync_devices(rows, "device_sn", db, fill_fields=["merchant_name"])
    _execute_upsert(db, BmsOrder, "order_no", rows, result)

def upsert_return_trackings(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    """退货跟踪 - 幂等 upsert

    来源: BMS 退货跟踪(导出/API)。因导出样本未定, 采用批次内自去重 + 全量 INSERT 策略
    (BMS 页面本身可复现同一退货单, 但无稳定唯一键前不覆盖删行)。
    时间 naive-北京 → UTC。
    """
    from datetime import datetime as _dt
    _now = _dt.now(timezone.utc)
    rows: list[dict] = []
    seen_key: set[tuple] = set()
    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            device_sn = (_to_str(data.get("device_sn")) or (_to_str(data.get("sn")) or "")).strip() or None
            order_no = (_to_str(data.get("order_no")) or (_to_str(data.get("sales_order_no")) or "")).strip() or None
            # 批次内去重锚: device_sn + order_no + 退货类型 + 退货时间(若有)
            return_time = _to_datetime(data.get("return_time"))
            dedup_key = (device_sn or "", order_no or "", (_to_str(data.get("return_type")) or "").strip(), _str_ts(return_time) if return_time else "")
            if dedup_key in seen_key:
                continue
            seen_key.add(dedup_key)
            rows.append({
                "device_sn": device_sn,
                "device_type": _truncate_str((_to_str(data.get("device_type")) or "").strip(), 64) or None,
                "shop": _truncate_str((_to_str(data.get("shop")) or "").strip(), 128) or None,
                "merchant": _truncate_str((_to_str(data.get("merchant")) or "").strip(), 128) or None,
                "paid_amount": _to_float(data.get("paid_amount")),
                "return_type": _truncate_str((_to_str(data.get("return_type")) or "").strip(), 32) or None,
                "refund_amount": _to_float(data.get("refund_amount")),
                "return_sign_status": _truncate_str((_to_str(data.get("return_sign_status")) or "").strip(), 32) or None,
                "return_express_no": _truncate_str((_to_str(data.get("return_express_no")) or "").strip(), 64) or None,
                "return_reason": _truncate_str((_to_str(data.get("return_reason")) or "").strip(), 128) or None,
                "order_no": order_no,
                "order_time": _to_datetime(data.get("order_time")),
                "return_time": return_time,
                "sign_time": _to_datetime(data.get("sign_time")),
                "creator": _truncate_str((_to_str(data.get("creator")) or "").strip(), 64) or None,
                "updater": _truncate_str((_to_str(data.get("updater")) or "").strip(), 64) or None,
                "created_at_raw": _to_datetime(data.get("created_at_raw")),
                "updated_at_raw": _to_datetime(data.get("updated_at_raw")),
                "remark": data.get("remark") or None,
                "synced_at": _now,
            })
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}")
    if not rows:
        return
    _sync_devices(rows, "device_sn", db, fill_fields=["merchant_name"])
    before = db.scalar(select(func.count()).select_from(ReturnTracking)) or 0
    BATCH = 2000
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        stmt = pg_insert(ReturnTracking).values(batch)
        stmt = stmt.on_conflict_do_nothing()  # 无稳定唯一键: 冲突跳过(靠批次内去重防同文件重复)
        db.execute(stmt)
        db.flush()
    after = db.scalar(select(func.count()).select_from(ReturnTracking)) or 0
    result.inserted += max(after - before, 0)
    result.updated += 0

def _str_ts(dt) -> str:
    """datetime → 稳定字符串(去重键用, 处理 aware/naive 混用)"""
    if dt is None:
        return ""
    if getattr(dt, "tzinfo", None):
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def upsert_device_profiles(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    """设备管理档案(P4, #18) - 回填 devices 主表(不建独立表)

    来源: BMS 设备管理导出 CSV(14列实测, 激活时间窗)。sn 主键 on_conflict_do_update,
    补齐 active_time/device_type/merchant_name/主商户/ICCID/版本/区域/批次 等档案字段。
    """
    rows: list[dict] = []
    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            sn = (_to_str(data.get("device_sn")) or "").strip()
            if not sn:
                result.add_error(idx, "缺少设备号(跳过)")
                continue
            rows.append({
                "sn": sn,
                "device_type": _truncate_str((_to_str(data.get("device_type")) or "").strip(), 64) or None,
                "device_type_remark": None,
                "active_time": _to_datetime(data.get("active_time")),
                "merchant_name": _truncate_str((_to_str(data.get("merchant_name")) or "").strip(), 128) or None,
                "status": "正常",
                "main_merchant_name": _truncate_str((_to_str(data.get("main_merchant_name")) or "").strip(), 128) or None,
                "active_flag": _truncate_str((_to_str(data.get("active_flag")) or "").strip(), 16) or None,
                "service_valid_until": _to_datetime(data.get("service_valid_until")),
                "current_combo_name": _truncate_str((_to_str(data.get("current_combo_name")) or "").strip(), 128) or None,
                "start_iccid": _truncate_str((_to_str(data.get("start_iccid")) or "").strip(), 64) or None,
                "virtual_iccid": _truncate_str((_to_str(data.get("virtual_iccid")) or "").strip(), 64) or None,
                "app_version": _truncate_str((_to_str(data.get("app_version")) or "").strip(), 64) or None,
                "region": _truncate_str((_to_str(data.get("region")) or "").strip(), 128) or None,
                "batch_no": _truncate_str((_to_str(data.get("batch_no")) or "").strip(), 64) or None,
            })
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}")
    if not rows:
        return
    # sn 批内去重(保留最后一条)
    seen: dict[str, dict] = {}
    for r in rows:
        seen[r["sn"]] = r
    rows = list(seen.values())
    # 精确区分 insert/update: 先查这批 sn 已有数(原本不存在 = insert, 存在 = update)
    existing_sns = set(
        db.scalars(select(Device.sn).where(Device.sn.in_([r["sn"] for r in rows])))
    )
    inserted = sum(1 for r in rows if r["sn"] not in existing_sns)
    BATCH = 2000
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        stmt = pg_insert(Device).values(batch)
        update_cols = {c: stmt.excluded[c] for c in batch[0] if c != "sn"}
        stmt = stmt.on_conflict_do_update(index_elements=["sn"], set_=update_cols)
        db.execute(stmt)
        db.flush()
    result.inserted += inserted
    result.updated += max(len(rows) - inserted, 0)
