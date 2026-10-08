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

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.bms_order import BmsOrder
from app.models.card_complaint import CardComplaint
from app.models.cs_record import CsRecord
from app.models.device import Device
from app.models.device_combo import DeviceCombo
from app.models.device_flow_usage import DeviceFlowUsage
from app.models.external_complaint import ExternalComplaint
from app.models.manual_refund import ManualRefund
from app.models.refund import Refund
from app.models.refund_record_report import RefundRecordReport
from app.models.return_tracking import ReturnTracking
from app.models.shanmao_refund import ShanmaoRefund
from app.models.ticket import ServiceTicket
from app.models.wallet import WalletWithdrawal


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
    remaining = _sum(DeviceCombo, DeviceCombo.remaining_traffic, DeviceCombo.device_sn,
                     DeviceCombo.remaining_traffic != -1)
    total_flow = _sum(DeviceFlowUsage, DeviceFlowUsage.flow_gb, DeviceFlowUsage.device_sn)
    profile["kpi"] = {
        "refund_count": _cnt(Refund, Refund.device_sn),
        "ticket_count": _cnt(ServiceTicket, ServiceTicket.device_sn),
        "wallet_count": _cnt(WalletWithdrawal, WalletWithdrawal.device_sn),
        "cs_count": _cnt(CsRecord, CsRecord.device_sn),
        "external_complaint_count": _cnt(ExternalComplaint, ExternalComplaint.device_sn),
        "card_complaint_count": _cnt(CardComplaint, CardComplaint.device_sn),
        "combo_count": _cnt(DeviceCombo, DeviceCombo.device_sn),
        "combo_total_amount": round(
            _sum(DeviceCombo, DeviceCombo.actual_amount, DeviceCombo.device_sn), 2),
        "combo_remaining_bytes": int(remaining),
        "manual_refund_count": _cnt(ManualRefund, ManualRefund.device_sn),
        "shanmao_refund_count": _cnt(ShanmaoRefund, ShanmaoRefund.device_sn),
        "order_count": _cnt(BmsOrder, BmsOrder.device_sn),
        "return_tracking_count": _cnt(ReturnTracking, ReturnTracking.device_sn),
        "refund_record_report_count": _cnt(RefundRecordReport, RefundRecordReport.device_sn),
        "flow_month_count": _cnt(DeviceFlowUsage, DeviceFlowUsage.device_sn),
        "flow_total_gb": round(total_flow, 4),
    }
    return profile
