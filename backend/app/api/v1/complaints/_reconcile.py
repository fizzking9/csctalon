"""外部投诉对账双边闭环: complaint_reconcile / complaint_reconcile_remark(#56)。"""

from fastapi import Query

from app.api.deps import CurrentUser, DBSession, ManagerUser

# 跨子模块引用(拆分自 complaints_src.py, 原同文件内直接调用)
from app.api.v1.complaints._helpers import _parse_dt, _parse_dt_end
from app.api.v1.complaints._router import router
from app.schemas.common import R


@router.get("/reconcile", response_model=R,
            summary="外部投诉对账·双边闭环(经营登记 ↔ 接待过程) (#56)")
def complaint_reconcile(
    db: DBSession,
    _: CurrentUser,
    start: str | None = None,
    end: str | None = None,
    detail_limit: int = Query(200, ge=1, le=2000, description="空悬明细返回上限"),
):
    """外部投诉对账 —— **经营登记(BMS) ↔ 接待过程(文档)** 双向闭环。

    ★ 用户语义（2026-09-11 权威）:
      > 「内部投诉/商户号投诉 跟外部投诉 **不存在对账关系**。
      >   外部投诉对账 = 服务类别为空、类型是几个平台的**外部投诉**与文档数据拉齐。
      >   两边数据都有意义: BMS 是**经营登记**; 文档是**接待过程/分析/处理**。」

    ★ 对账目标 = **双边空悬**（不是数量相等, 两侧不互相抵消）:
      · `doc_orphan`    —— 文档有、工单无 → 投诉了但**没建单**(漏登记)
      · `ticket_orphan` —— 工单有、文档无 → 建单了但**没过程记录**

    ★ 口径: 候选工单**只含对应渠道**的投诉类工单:
      · `external` —— 外部投诉类(`item` 命中 12315/公共服务平台/消协会/监督局…)
      · `card`     —— (外)卡商类(`item = 工信部投诉`) ← #57 摘出

    ★★ #57 (2026-09-11): 分渠道对账 ★★
      用户: 「工信部投诉 应该是卡商投诉匹配, 可以从外部投诉 摘出来 做单独统计对账去」
            →「全部摘出吧 然后再分开对账」
      证据: 卡商表 ⨝ 工信部工单 的配对内容全是「物联网卡/转售商/ICCID/卡商群名」,
            日期差恒 +1 天; 315 文档里**没有工信部**渠道。

    返回: `{"external": {...}, "card": {...}}` 两套完整双边对账。
    """
    from app.services.external_reconcile import reconcile_all

    ts = _parse_dt(start)
    te = _parse_dt_end(end)
    data = reconcile_all(db, ts, te, detail_limit=detail_limit)
    return R(data=data)

@router.post("/reconcile/remark", response_model=R,
             summary="重算并回写 matched_ticket_no(修复「工单晚到不补挂」) (#56)")
def complaint_reconcile_remark(
    db: DBSession,
    _: ManagerUser,
    window_days: int = Query(90, ge=1, le=3650, description="重匹配回溯天数"),
    dry_run: bool = Query(False, description="true=只统计不改库"),
):
    """重算 `matched_ticket_no` —— 修复「工单晚到不补挂」。

    实测外部投诉类工单 `created_at - register_time` **平均滞后 71.1 天**
    (>30 天占 53%), 而匹配只在同步瞬间写一次 → 工单晚到时该 315 记录**永久空悬**。

    · 只写 `matched_ticket_no` / `matched_at`, **不动其他字段**
    · 幂等(候选集与选择规则确定), 可反复执行
    · 会把**已不合规的旧匹配**(指向非外部投诉类工单)清空, 避免统计污染
    """
    from app.services.external_reconcile import remark_matched

    stat = remark_matched(db, window_days=window_days, dry_run=dry_run)
    return R(data=stat)
