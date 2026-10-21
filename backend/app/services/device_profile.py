"""设备档案与生命周期 KPI 聚合(service 单一真身)

2026-10-09 #237(B2-2): 从 app/api/v1/devices.py 的 _device_subject 原样下沉而来,
供 ① 内部设备详情/时间线路由 ② AI 数据供给层(ai_data_supply) ③ 外部 /external/device
复用同一份口径, 避免"前端一份、AI/外部一份"两处 count/sum 漂移。

口径说明:
- 各业务表均按设备 SN 精确匹配计数; 金额 actual_amount 单位=元(Numeric(12,2));
- combo_remaining_bytes 剩余流量为字节, 且排除 -1(无限流量包, 见 device_combos 语义);
- flow_total_gb 来自 device_flow_usage.flow_gb(月用量, GB, Numeric; 属 #217 登记的非金额 float 例外域)。
"""

from __future__ import annotations

from sqlalchemy import column, func, select, table, text
from sqlalchemy.orm import Session

from app.models.device import Device
from app.models.ticket import ServiceTicket

# #338: 设备档案读 std_all 镜像
_CS = table("std_cs_record", column("device_sn"))
_EC = table("std_external_complaint_all", column("device_sn"))
_CC = table("std_card_complaint_all", column("device_sn"))
_DC = table("std_device_combo_all",
            column("device_sn"), column("actual_amount"), column("package_name"),
            column("remaining_traffic"))
_BO = table("std_bms_order", column("device_sn"))
_DFU = table("std_device_flow_usage_all", column("device_sn"), column("flow_gb"))


def build_device_profile(db: Session, device: Device) -> dict:
    """设备 subject: 基础档案 + P4 扩展档案(BMS 设备管理采集) + 生命周期 KPI 概览。

    集中一处序列化, 供 /devices/{sn} 详情、/devices/{sn}/timeline、AI 数据供给层
    (app.services.ai_data_supply) 与外部 /external/device 复用, 避免多处口径漂移。
    """
    sn = device.sn

    def _cnt(model, col, extra=None):
        stmt = select(func.count()).select_from(model).where(col == sn)
        if extra is not None:
            stmt = stmt.where(extra)
        return db.scalar(stmt) or 0

    def _sum(model, value_col, match_col, extra=None):
        # #237 修正: 原内联实现把"求和列"误当"匹配 SN 的列"(WHERE value_col == sn),
        # 对金额/流量等数值列会生成 `actual_amount = 'SN...'` 的非法条件。拆成两个列:
        # match_col 恒为 *device_sn(设备匹配), value_col 才是被 SUM 的数值列。
        stmt = select(func.coalesce(func.sum(value_col), 0)).select_from(model).where(match_col == sn)
        if extra is not None:
            stmt = stmt.where(extra)
        return float(db.scalar(stmt) or 0)

    # ---- 设备档案(P4 扩展, 来自 BMS 设备管理采集, 已入库零展示) ----
    profile = {
        "kind": "device",
        "sn": device.sn,
        "device_type": device.device_type,
        "device_type_remark": device.device_type_remark,
        "merchant_name": device.merchant_name,
        "main_merchant_name": device.main_merchant_name,
        "active_flag": device.active_flag,
        "status": device.status,
        "active_time": device.active_time.isoformat() if device.active_time else None,
        "service_valid_until": device.service_valid_until.isoformat() if device.service_valid_until else None,
        "current_combo_name": device.current_combo_name,
        "start_iccid": device.start_iccid,
        "virtual_iccid": device.virtual_iccid,
        "app_version": device.app_version,
        "region": device.region,
        "batch_no": device.batch_no,
        "tags": [{"id": t.id, "name": t.tag_name, "color": t.color} for t in (device.tags or [])],
    }

    # ---- 生命周期 KPI 概览(各业务表 count + 关键 sum, 口径与 timeline/本地查询一致) ----
    remaining = _sum(_DC, _DC.c.remaining_traffic, _DC.c.device_sn,
                     _DC.c.remaining_traffic != -1)
    total_flow = _sum(_DFU, _DFU.c.flow_gb, _DFU.c.device_sn)

    # #334: 读 std_*_all 全量镜像(ods 同构透传), 业务代码零读 ods
    def _cnt_std(view_name: str) -> int:
        return int(db.scalar(
            text(f"SELECT count(*) FROM {view_name} WHERE device_sn = :sn"), {"sn": sn}
        ) or 0)

    profile["kpi"] = {
        "refund_count": _cnt_std("std_refund_online_all"),
        "ticket_count": _cnt(ServiceTicket, ServiceTicket.device_sn),
        "wallet_count": _cnt_std("std_wallet_flow_all"),
        "cs_count": _cnt(_CS, _CS.c.device_sn),
        "external_complaint_count": _cnt(_EC, _EC.c.device_sn),
        "card_complaint_count": _cnt(_CC, _CC.c.device_sn),
        "combo_count": _cnt(_DC, _DC.c.device_sn),
        # 累计充值金额排除质检套餐(#293): 出厂质检流水(999/9999元)非真实付费
        "combo_total_amount": round(
            _sum(_DC, _DC.c.actual_amount, _DC.c.device_sn,
                 _DC.c.package_name.notilike("%质检%")), 2),
        "combo_remaining_bytes": int(remaining),
        "manual_refund_count": _cnt_std("std_refund_manual_all"),
        "shanmao_refund_count": _cnt_std("std_refund_shanmao_all"),
        "order_count": _cnt(_BO, _BO.c.device_sn),
        "return_tracking_count": _cnt_std("std_return_tracking_all"),
        "refund_record_report_count": _cnt_std("std_refund_record_all"),
        "flow_month_count": _cnt(_DFU, _DFU.c.device_sn),
        "flow_total_gb": round(total_flow, 4),
    }
    return profile
